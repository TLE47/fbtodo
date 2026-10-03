"""tmux: the list pane, the keeper that reopens it, and the watchers behind both.

A pane is opened beside the freebuff instance it belongs to, remembered per window, and kept
alive by a keeper that is neither watcher — it has no store, no state file and no instance of
its own, only the panes.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from .base import *  # noqa: F401,F403 (the package is one namespace)
from .locks import *  # noqa: F401,F403 (the package is one namespace)
from .scan import *  # noqa: F401,F403 (the package is one namespace)
from .nas import *  # noqa: F401,F403 (the package is one namespace)

# ==================================================== the NAS pane watcher
def tmux_run(*argv, timeout: float = 10.0):
    """Run tmux (overridable for tests: FBTODO_TMUX="tmux -L private").

    Returns stdout, or None when there is no usable server/client — every caller here is
    "do this if you can", so a failure must not be an exception.
    """
    argv = list(argv)
    if argv and argv[0] in TMUX_SUBCOMMANDS:
        argv = [*TMUX_BIN, *argv]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def tmux_split_target() -> str | None:
    """A window to split: the most recently active client's, else the busiest session's.

    A watcher is not attached to anything, so "your window" has to be inferred. Clients
    first (that is the one being looked at), then attached-or-not sessions, so a pane still
    lands somewhere sensible when only a detached server is left running.
    """
    out = tmux_run("list-clients", "-F", "#{client_activity} #{client_name}")
    best = None
    for line in (out or "").splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            best = max(best or (0, ""), (int(parts[0]), parts[1]))
    if best:
        win = tmux_run("display-message", "-c", best[1], "-p", "#{window_id}")
        if win and win.strip():
            return win.strip()
    out = tmux_run("list-sessions", "-F", "#{session_activity} #{session_name}")
    best = None
    for line in (out or "").splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            best = max(best or (0, ""), (int(parts[0]), parts[1]))
    if best:
        win = tmux_run("display-message", "-t", best[1], "-p", "#{window_id}")
        if win and win.strip():
            return win.strip()
    return None


# ------------------------------------------- the pane the NAS session runs inside
# `fb` is typed INSIDE an ssh over there, so the pane hosting the session is the pane
# whose descendant ssh process is that login — the same "by ancestry" rule the local
# instance's pane uses. A *window* is not good enough as a target: tmux answers one with
# its ACTIVE pane, which is whatever you happened to be looking at (measured — with a NAS
# pane and a session pane sharing a window, a fresh pane landed in the wrong column).
SSH_SESSION_CMDS = ("ssh", "ssh.exe")


# Flags that make an ssh something other than a login shell. A tunnel or a control master
# names the same host and sits in a pane too, and must not be taken for the session.
SSH_NON_SESSION_FLAGS = {"-N", "-M", "-L", "-R", "-D", "-W", "-O", "-S", "-f"}


# The marker's clock is the NAS's and the ssh's is this Mac's, so "before" allows a little.
NAS_CLOCK_SKEW_MS = 5000.0


def is_ssh_cmd(cmd: str) -> bool:
    """Does this command line start an ssh client — directly, or as a script's argv[1]?

    Only the first two tokens: a later argument that happens to be called `ssh` (a path
    handed to something) is not a client. The test stand-in is a shell script, so its
    command line reads `/bin/sh /path/to/ssh -t …`.
    """
    return any(os.path.basename(tok) in SSH_SESSION_CMDS for tok in cmd.split()[:2])


def nas_host_tokens(nas_host: str) -> set[str]:
    """What names this NAS on an ssh command line — empty when it cannot be derived.

    In real use `--nas-host` is `user@host`, and that string is what the ssh process
    shows. A stand-in (a path, a fixture's fake transport) names a *program* instead:
    then there is no host to match, and the caller accepts any session-like ssh rather
    than refusing to place the pane at all.
    """
    host = nas_host.strip()
    return {host, host.split("@")[-1]} if "@" in host else set()


def ssh_session_candidates(nas_host: str, rows=None, table=None) -> list[dict]:
    """Panes hosting an ssh that could be the NAS session: pane, window, ssh pid.

    A pane is listed once, by its shallowest such ssh. A `docker exec` over ssh is
    skipped: that is a command run on the remote host, not a login shell with a freebuff
    prompt in it.
    """
    rows = pane_rows() if rows is None else rows
    table = process_table() if table is None else table
    needles = nas_host_tokens(nas_host)
    found: dict[str, dict] = {}
    for row in rows:
        if not row["pid"] or row["pane"] in found:
            continue
        # The pane's own process counts: a pane can BE the ssh (a window opened as
        # `ssh host`), and then there is nothing below it to find.
        for pid, _depth in [(row["pid"], 0), *descendant_pids(table, row["pid"])]:
            cmd = table.get(pid, (0, ""))[1]
            if not is_ssh_cmd(cmd):
                continue
            tokens = cmd.split()
            if "docker" in cmd or any(tok in SSH_NON_SESSION_FLAGS for tok in tokens):
                continue
            if needles and not any(needle in tok for tok in tokens for needle in needles):
                continue
            found[row["pane"]] = {
                "pane": row["pane"], "window": row["window"], "ssh_pid": pid, "cmd": cmd,
            }
            break
    return list(found.values())


def pick_ssh_pane(began: dict[str, float], started_ms: float | None) -> str | None:
    """Which of several logins hosts the session, given when each of them began.

    An ssh cannot host a session that was already running when it logged in, so the newest
    login that began before the session did wins. With no marker timestamp at all (the
    session was found by `pgrep`, so there is no start time to compare) the newest login is
    the best answer; a session that began before every candidate takes the earliest of
    them. `NAS_CLOCK_SKEW_MS` covers the two clocks being different machines'.
    """
    if not began:
        return None
    if not started_ms:
        return max(began, key=began.get)
    before = [pane for pane, t in began.items() if t <= started_ms + NAS_CLOCK_SKEW_MS]
    return max(before, key=began.get) if before else min(began, key=began.get)


def nas_ssh_pane(
    nas_host: str, started_ms: float | None, rows=None, table=None
) -> str | None:
    """The pane the live NAS session is drawn in — its ssh — when it can be told apart.

    One candidate and there is nothing to decide. Several, and the session's own start
    time (the marker's) is what tells them apart — see `pick_ssh_pane`. None at all means
    the pane cannot be placed against anything, and the caller keeps its fallback.
    """
    cands = ssh_session_candidates(nas_host, rows, table)
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]["pane"]
    ages = ages_for([c["ssh_pid"] for c in cands])
    now_ms = time.time() * 1000
    began = {c["pane"]: now_ms - ages.get(c["ssh_pid"], 0) * 1000 for c in cands}
    return pick_ssh_pane(began, started_ms)


def nas_pane_ids() -> list[str]:
    """Panes already showing a NAS todo list — the watcher's own, or a hand-opened one.

    Matched on `pane_start_command`, because that survives the pane's own repaints and
    names the source: a LOCAL pane is `fbtodo --instance-of …` and must not be mistaken
    for one of these.
    """
    out = tmux_run("list-panes", "-a", "-F", "#{pane_id} #{pane_start_command}")
    found = []
    for line in (out or "").splitlines():
        parts = line.split(None, 1)
        start = parts[1] if len(parts) > 1 else ""
        if len(parts) == 2 and "fbtodo" in start and "-s nas" in start:
            found.append(parts[0])
    return found


def nas_place_panes(panes: list[str], anchor: str | None, quiet: bool = True) -> list[str]:
    """Put NAS list panes back beside the ssh their session runs inside, at the size it says.

    Only with an anchor: with no ssh identified there is nothing to place them against,
    and moving a pane somewhere unresearched is worse than leaving it where it is. The
    panes are moved, never re-opened — the running one keeps its scrollback and its clock.

    Side and size come from the same place a local pane's do — `pane_layout` for the role
    `nas`, in the window the ssh sits in — so a pin can move this pane's side or hold its
    size exactly as it does the local one's, and both are remembered for the next pane.
    """
    if not anchor:
        return []
    window = window_of(anchor)
    layout = pane_layout(window, "nas")
    rects = pane_rects()
    moved, sized = [], []
    for pane in panes:
        here = kept_layout(pane, anchor, rects, layout)
        if place_pane_beside(pane, anchor, rects, here["side"]):
            moved.append(pane)
            rects = pane_rects()  # the layout just moved under us
        if hold_pane_size(pane, here["window"], "nas", rects, here):
            sized.append((pane, here["size_source"]))
            rects = pane_rects()
        remember_layout(window, "nas", pane, rects, here["side"], anchor)
    forget_settled({row["pane"] for row in pane_rows()})
    for pane in moved:
        # Written down quiet or not, like the keeper's: the watcher is spawned `--quiet`
        # (its log IS its stdout), so a print alone would leave nothing to look at.
        append_log(NAS_LOG_PATH, f"moved {pane} beside the session's ssh pane")
    for pane, source in sized:
        append_log(
            NAS_LOG_PATH,
            f"resized {pane} to its {'pinned' if source.startswith('pin') else source} size",
        )
    if not quiet:
        if moved:
            print(
                f"put NAS todo pane(s) back beside the session's ssh: {', '.join(moved)}",
                file=sys.stderr,
            )
        if sized:
            print(
                "resized NAS todo pane(s): "
                + ", ".join(f"{pane} ({source})" for pane, source in sized),
                file=sys.stderr,
            )
    return moved


def nas_pane_open(args, anchor: str | None = None) -> str | None:
    """Split a pane running the NAS pane command, and return its pane id.

    `anchor` is the pane the session is drawn in — its ssh — when that could be worked
    out, so the list lands directly under it. The most recently active client's window is
    the fallback: a pane in the wrong place is still better than no list at all, and the
    watcher's placement pass moves it as soon as the ssh can be seen.

    Side and size come from `pane_layout` for the role `nas`, so a pin opens the pane where
    it says rather than only dragging it there on the next pass.
    """
    target = anchor or tmux_split_target()
    if not target:
        return None
    layout = pane_layout(window_of(target), "nas")
    command = nas_pane_command(args)
    # The edge the owner last kept it on (`before`): `split-window -h` alone would open a
    # pane the owner had put on the left back on the right, and one from under the session
    # back over it.
    before = ["-b"] if layout.get("before") else []
    out = tmux_run(
        "split-window", *before, f"-{layout['side']}", "-l", str(layout["size"]), "-d", "-P",
        "-F", "#{pane_id}", "-t", target, command,
    )
    return out.strip() if out and out.strip() else None


def nas_pane_command(args) -> str:
    """The command a watcher-opened pane runs.

    `--no-daemon` on purpose: the pane reads the NAS store itself each poll, and asking it
    for a watcher would collide with the local one's single lock (two daemons, one state
    file). The pane exits by itself when the far session goes, so a killed watcher still
    cannot leave a pane behind.
    """
    return pane_command([
        *self_argv(), "-s", "nas", "--stale-after", "0",
        "--no-daemon", "--quiet",
        "--nas-host", args.nas_host, "--nas-root", args.nas_root,
        "--nas-project", args.nas_project, "--fb-marker", args.fb_marker,
    ])


def nas_pane_kill(pane: str) -> None:
    tmux_run("kill-pane", "-t", pane)


# ------------------------------------------------ the local pane, kept open
# The pane next to a local session is split by the zsh wrapper, which then sits inside
# the CLI and cannot notice anything — including that pane being killed. The watcher can:
# it is the one process here that outlives the pane and follows the instance. So it puts
# the pane back, which is what makes killing it a mistake rather than a way to lose the
# list. `FBTODO_NO_PANE=1` (the wrapper's own switch) and `--pane-seconds 0` both stop it.
PANE_INSTANCE_RE = re.compile(r"--instance-of\s+(\d+)")


PANE_WATCH_RE = re.compile(r"--watch-pid\s+(\d+)")


def pane_off() -> bool:
    return bool(os.environ.get("FBTODO_NO_PANE"))


def append_log(path: str, line: str) -> None:
    """Append one timestamped line to a log, keeping it bounded.

    Its own handle, opened and closed per line: the NAS watcher's log is also that
    process's stdout, held open in append mode by whoever spawned it, and a second
    long-lived handle could interleave with it.
    """
    try:
        with open(path, "a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
        if os.path.getsize(path) > 65536:
            with open(path) as fh:
                tail = fh.read()[-16384:]
            with open(path, "w") as fh:
                fh.write(tail)
    except OSError:
        pass


def pane_log(line: str) -> None:
    """A line in the keeper's own log — bounded, the way the watcher's is."""
    append_log(PANE_LOG_PATH, line)


def pane_repaint(previous: list | None, frame: list, rows: int) -> str:
    """The bytes that turn the last painted frame into this one, row by row.

    A pane repaints on a tick even when nothing moved — its clock does — and clearing the screen
    and writing the whole frame every time is a flash on every tick, in a pane that sits there
    all day at 1Hz. So the repaint is a diff: each row that changed is addressed and rewritten in
    place (`\\x1b[<row>;1H`, then erase the line, then the row), and an unchanged frame writes
    NOTHING at all — no cursor move, no erase, not one byte.

    A frame whose SHAPE changed is painted whole instead: the row count moves on a resize and on
    the first paint, and rewriting rows one by one would leave the rows that are no longer spoken
    for on the screen. `rows` is the pane's height: a frame that does not fill the pane keeps a
    trailing newline, because a newline after a full one scrolls the top border — and the title
    with it — off the screen. For the same reason the cursor is left on the row BELOW the frame
    (the whole-frame paint gets there with its newline, a diff with one cursor move), which is
    where the pane prints the line it says on the way out.
    """
    if previous is None or len(previous) != len(frame):
        tail = "" if len(frame) >= rows else "\n"
        return "\x1b[H\x1b[2J" + "\n".join(frame) + tail
    out = "".join(
        f"\x1b[{at};1H\x1b[2K{line}"
        for at, (old, line) in enumerate(zip(previous, frame), start=1) if old != line
    )
    if not out:
        return ""
    if len(frame) < rows:
        out += f"\x1b[{len(frame) + 1};1H"
    return out


def pane_start_command(reported: str) -> str:
    """`#{pane_start_command}` as the command itself, not as tmux's quoting of it.

    In plain words: tmux re-quotes a format value the way a SHELL would quote it when it
    needs it — single quotes where the value holds a double quote (`X"Y` is reported as
    `'X"Y'`), double quotes with `"` and `\\` escaped when it holds a space or a single
    quote (`sleep 300` as `"sleep 300"`, `X "Y"` as `"X \\"Y\\""`), and nothing when
    it needs nothing (measured 2026-10-02 against the tmux on this machine). A pinned
    command always has spaces, so it always comes back wrapped — and reading it back and
    comparing it with
    the pin it was started from was false forever, which a keeper must not believe: it
    would respawn a pane that is already running its pin, once per pass.

    `shlex.split` is the reader that undoes exactly this encoding: a value tmux quoted
    comes back as the one word it was. A value tmux left alone is returned untouched,
    backslashes and all — this only decodes what is wrapped.
    """
    if len(reported) >= 2 and reported[0] in "'\"" and reported[-1] == reported[0]:
        try:
            parts = shlex.split(reported)
        except ValueError:  # an unbalanced quote is not ours to repair
            return reported
        if len(parts) == 1:
            return parts[0]
    return reported


def pane_rows() -> list[dict]:
    """Every pane, with what deciding about it needs: its id, its window, its shell."""
    # The field separator is a printable `|`, not a tab: a tab inside a `-F` format does not
    # survive every tmux, and where it did not the whole row failed to parse — which read as
    # "no panes at all" on that machine (seen on a Linux runner under a C locale, where the
    # tab came back as `_`). Nothing else about the format depends on the separator.
    fmt = "#{pane_id}|#{window_id}|#{pane_pid}|#{pane_start_command}"
    rows = []
    for line in (tmux_run("list-panes", "-a", "-F", fmt) or "").splitlines():
        parts = line.split("|", 3)
        if len(parts) < 4 or not parts[0].startswith("%"):
            continue
        rows.append(
            {
                "pane": parts[0],
                "window": parts[1],
                "pid": int(parts[2]) if parts[2].isdigit() else 0,
                "start": pane_start_command(parts[3]),
            }
        )
    return rows


def local_pane_ids(instance_pid, rows=None, table=None) -> list[str]:
    """The panes showing THIS instance's list, by name or by ancestry.

    The wrapper's pane names its shell (`--instance-of $$`, and the instance is that
    shell's child) while a replacement names the instance pid itself (`--watch-pid`),
    so both forms are recognised — otherwise this would open a second pane beside the
    one already there, and keep opening one.
    """
    rows = pane_rows() if rows is None else rows
    table = process_table() if table is None else table
    found = []
    for row in rows:
        start = row["start"]
        # a NAS pane is the other watcher's business, and `--no-daemon` is a pane that
        # refuses to poll — neither is this instance's todo list
        if "fbtodo" not in start or "-s nas" in start or "--no-daemon" in start:
            continue
        match = PANE_WATCH_RE.search(start)
        if match and int(match.group(1)) == instance_pid:
            found.append(row["pane"])
            continue
        match = PANE_INSTANCE_RE.search(start)
        if match and descendant_instance(table, int(match.group(1))) == instance_pid:
            found.append(row["pane"])
    return found


def freebuff_pane_id(instance_pid, rows=None, table=None) -> str | None:
    """The pane the session itself is drawn in — what the ask/stall watches read.

    By ancestry, not by the most recent client: the pane to read is the one the instance
    is running in, whichever window you happen to be looking at. None means the session is
    not in tmux, and then no pane can answer for it (the notify scripts say so rather than
    guess).
    """
    rows = pane_rows() if rows is None else rows
    table = process_table() if table is None else table
    for row in rows:
        if row["pid"] and descendant_instance(table, row["pid"]) == instance_pid:
            return row["pane"]
    return None


def session_windows(rows, table) -> dict:
    """instance pid -> the window its own pane is drawn in, for every local session.

    Found by ancestry, not by the most recent client: a replacement pane belongs beside
    the session it describes, even when you are looking at another window. An instance
    that is not in tmux at all is simply absent — and then there is no pane to reopen.
    """
    found = {}
    for row in rows:
        if not row["pid"]:
            continue
        inst = descendant_instance(table, row["pid"])
        if inst:
            found.setdefault(inst, row["window"])
    return found


def instances_with_pane(rows, table) -> set:
    """Which instances already have a live todo pane, in one pass over the panes."""
    covered = set()
    for row in rows:
        start = row["start"]
        if "fbtodo" not in start or "-s nas" in start or "--no-daemon" in start:
            continue
        match = PANE_WATCH_RE.search(start)
        if match:
            covered.add(int(match.group(1)))
            continue
        match = PANE_INSTANCE_RE.search(start)
        if match:
            inst = descendant_instance(table, int(match.group(1)))
            if inst:
                covered.add(inst)
    return covered


# ------------------------------------------- placement: beside the session's own pane
# A todo pane belongs in the strip its session is drawn in, starting on the row under it:
# that is where splitting the session's pane puts it, and where a glance finds it. No
# opener can promise it, though. The wrapper splits BEFORE the CLI exists, so all it can
# name is the pane it believes it is in, and a layout change moves panes afterwards. So
# the position is re-derived from the geometry after the fact and repaired in place.
def pane_rects() -> dict:
    """Geometry per pane: what deciding "is this beside that" needs (one tmux call)."""
    fmt = (
        "#{pane_id}|#{window_id}|#{pane_left}|#{pane_top}"
        "|#{pane_width}|#{pane_height}"
    )
    rects = {}
    for line in (tmux_run("list-panes", "-a", "-F", fmt) or "").splitlines():
        parts = line.split("|")
        if len(parts) < 6 or not parts[0].startswith("%"):
            continue
        try:
            left, top, width, height = (int(part) for part in parts[2:6])
        except ValueError:
            continue
        rects[parts[0]] = {
            "window": parts[1],
            "left": left,
            "top": top,
            "width": width,
            "height": height,
        }
    return rects


def pane_axis(anchor: dict | None, pane: dict | None) -> str | None:
    """Which axis a pane sits on relative to its anchor: `v` stacked, `h` side by side.

    Read from the geometry rather than from the pane's own layout, because the question it
    answers is where the pane IS: two panes whose rows do not overlap share a column band
    and are stacked, two whose columns do not overlap share a row band and sit side by
    side, and anything else — the far corner of a grid — is on no axis of its anchor at
    all. `None` for a pane in another window too, which is nobody's business.
    """
    if not anchor or not pane or anchor["window"] != pane["window"]:
        return None
    rows_apart = pane["top"] > anchor["top"] + anchor["height"] or (
        anchor["top"] > pane["top"] + pane["height"]
    )
    cols_apart = pane["left"] > anchor["left"] + anchor["width"] or (
        anchor["left"] > pane["left"] + pane["width"]
    )
    if cols_apart and not rows_apart:
        return "h"
    if rows_apart and not cols_apart:
        return "v"
    return None


def placed_beside(inst_pane: str, todo_pane: str, rects: dict, split: str) -> bool:
    """Is the todo pane on the side of its session's pane that `split` says it sits on?

    Below by default, beside it for `FBTODO_SPLIT=h`, so the two agree on one rule. What a
    side fixes is the AXIS, not the order on it: a strip above its session and one under it
    are both `v`, and a column to the left of one is as much `h` as a column to the right —
    the other edge of the same axis is a place the owner drags a pane to, not a drift to
    bring back. Deliberately lenient about the pane being *wider* than the session's — a
    strip under two panes is a layout somebody chose, and dragging it narrow again would be
    the tool arguing with its owner — and strict about the band it sits in, so a pane that
    has come off the axis altogether is brought back.
    """
    a, b = rects.get(inst_pane), rects.get(todo_pane)
    if not a or not b or a["window"] != b["window"]:
        return False
    if split == "h":
        return (
            b["top"] <= a["top"]
            and b["top"] + b["height"] >= a["top"] + a["height"]
            and (
                b["left"] == a["left"] + a["width"] + 1
                or b["left"] + b["width"] + 1 == a["left"]
            )
        )
    return (
        b["left"] <= a["left"]
        and b["left"] + b["width"] >= a["left"] + a["width"]
        and (
            b["top"] == a["top"] + a["height"] + 1
            or b["top"] + b["height"] + 1 == a["top"]
        )
    )


def pane_before(anchor: dict, pane: dict, split: str) -> bool:
    """Is the pane on its anchor's leading edge — above it (`v`) or left of it (`h`)?

    The half of the position a repair has to keep: the axis comes from the side in force,
    but WHICH edge of it the list hangs off belongs to the owner (`placed_beside`), so a
    pane that has to be moved back onto a side it was already on goes back to the edge it
    came from rather than to the one `split-window` would pick for a brand new pane.
    """
    if split == "h":
        return pane["left"] < anchor["left"]
    return pane["top"] < anchor["top"]


def place_pane_beside(pane: str, anchor: str, rects: dict, split: str) -> bool:
    """Move a drifted todo pane back to its session's side of its anchor; True if it moved.

    The anchor is the pane the session is drawn in: the freebuff pane for a local session,
    the ssh for a NAS one. `move-pane` moves the pane itself, so its process, its
    scrollback and its step clocks come along: a repair, never a restart (a re-opened pane
    would lose the timer of the step it is running). A pane found in ANOTHER window is left
    alone — that is an arrangement the owner made, and the window a session happens to sit
    in has no say over one built somewhere else.
    """
    if pane == anchor or placed_beside(anchor, pane, rects, split):
        return False
    here, there = rects.get(pane), rects.get(anchor)
    if not here or not there or here["window"] != there["window"]:
        return False
    # Back to the edge it was on, on the side it belongs to: `-b` is `move-pane`'s "before
    # the target", which is what puts a pane above a session rather than under it, or to
    # its left rather than its right (tmux's own default, and so `split-window`'s). Only
    # when it was already on that side, though: a pane arriving from the other axis has no
    # edge to keep, and `pane_before` asked about it answers from a position with nothing
    # to do with the axis being applied — a strip under the session is "left of" it, which
    # put a freshly pinned `h` pane on the left instead of the right.
    same_axis = pane_axis(there, here) == split
    before = ["-b"] if same_axis and pane_before(there, here, split) else []
    return tmux_run(
        "move-pane", "-d", *before, f"-{split}", "-s", pane, "-t", anchor
    ) is not None


# ------------------------------------------------------- per-window pins (the layout)
# A window can be given its own answer for side and size — `fbtodo pin --side h --size 16`
# run in it, or with `--window` naming another. The pin is filed under `session:index`
# (what survives a tmux restart, and what you would type), it is more specific than the
# FBTODO_SPLIT/FBTODO_PANE_SIZE knobs so it wins for its window, and it is enforced the
# way placement is: moved on the wrong side, resized away from the pinned one.
def load_pins() -> dict:
    """The pins file: one read, and nothing trusted beyond "it is an object"."""
    pins = read_json(PINS_PATH, {}) or {}
    return pins if isinstance(pins, dict) else {}


def window_key(window: str | None) -> str | None:
    """`<session>:<index>` for a window — what a pin is filed under.

    A window id (`@71`) belongs to one tmux run; the session name and the window index are
    what survive a restart and what you would type, so a pin follows "window 2 of main".
    """
    if not window:
        return None
    out = tmux_run(
        "display-message", "-t", window, "-p", "#{session_name}:#{window_index}"
    )
    return out.strip() if out and out.strip() else None


def pin_for_window(window: str | None, pins: dict | None = None) -> dict:
    """This window's pin, or {} — looked up by session:index, never by window id."""
    pins = load_pins() if pins is None else pins
    if not pins or not window:
        return {}
    entry = pins.get(window_key(window) or "")
    return entry if isinstance(entry, dict) else {}


# ------------------------------------------------- remembered layout (the last place)
# What a pane looked like last time, so the next one opens there instead of at the
# default: after a tmux server restart (which takes every pane with it) the list comes
# back the size and side it was left on, in the window it belonged to. Keyed by
# `session:index` and by ROLE (`local` / `nas`), because one window can hold both a
# session's list and a NAS one, and they are not the same size. A pin is the deliberate
# version of the same idea and outranks it; this file is written by the keeper, the pins
# file only by `fbtodo pin`, so the two never race.
def load_last() -> dict:
    """The remembered-layout file: one read, nothing trusted beyond "it is an object"."""
    last = read_json(LAST_PATH, {}) or {}
    return last if isinstance(last, dict) else {}


def save_last(last: dict) -> None:
    atomic_write_json(LAST_PATH, last)


def pin_value(window: str | None, role: str, field: str, pins: dict | None = None):
    """One half of a window's pin for a role: `(value, source)`, or `(None, "")` if unset.

    A window can hold two list panes — a local session's and a NAS one — and one
    `{side, size}` need not suit both: `fbtodo pin --role nas` files that role's own half
    under the role, which outranks the shared one for it, while a pin written without a
    role (`--role both`, and every pin this tool wrote before there was a choice) is the
    window's shared answer and stands for both. The source names which of the two
    answered, so `fbtodo why` can say it out loud.
    """
    entry = pin_for_window(window, pins)
    entry = entry if isinstance(entry, dict) else {}
    scoped = entry.get(role)
    if isinstance(scoped, dict) and field in scoped:
        value = scoped[field]
        if (field == "side" and value in ("v", "h")) or (
            field == "size" and isinstance(value, int) and value > 0
        ):
            return value, f"pin:{role}"
    value = entry.get(field)
    if field == "side":
        return (value, "pin") if value in ("v", "h") else (None, "")
    return (value, "pin") if isinstance(value, int) and value > 0 else (None, "")


def pane_layout(window: str | None, role: str, pins: dict | None = None,
                last: dict | None = None) -> dict:
    """Side and size in force for a window's list pane of `role`, and where each came from.

    Most specific first: the window's explicit pin (`fbtodo pin`, that role's own half
    before the window's shared one), then how this pane was left last time (remembered per
    role, so a local list and a NAS one in the same window do not overwrite each other),
    then FBTODO_SPLIT/FBTODO_PANE_SIZE, then the built-in default. The source travels with
    the number because `fbtodo why` prints it: a pane that opens somewhere you did not
    choose can say which of the four did it. `pins`/`last` are passed in by callers that
    ask this for several panes in one pass, so the files are read once.
    """
    key = window_key(window) or ""
    seen = (load_last() if last is None else last).get(key)
    seen = (seen.get(role) if isinstance(seen, dict) else None) or {}
    side, side_source = pin_value(window, role, "side", pins)
    if not side and seen.get("side") in ("v", "h"):
        side, side_source = seen["side"], "last"
    env_before: bool | None = None
    if not side:
        # FBTODO_SPLIT names the SIDE (`v`/`h` — the splitter's own trailing edge, as it
        # always has) or the PLACE, which fixes the edge too: `left`/`top` put the list on
        # the leading edge a bare `split-window` would not pick, `right`/`bottom` on the
        # trailing one it would. The place is how a default of "the list on the left" is
        # typed, and it has to reach the keeper as well as the opener — the wrapper passes
        # it into the pane's command line, and the pane's own watcher inherits it.
        place = {
            "v": ("v", False), "h": ("h", False),
            "left": ("h", True), "right": ("h", False),
            "top": ("v", True), "bottom": ("v", False),
        }.get(os.environ.get("FBTODO_SPLIT") or "")
        if place:
            (side, env_before), side_source = place, "env"
        else:
            side, side_source = "v", "default"
    size, size_source = pin_value(window, role, "size", pins)
    if not size and isinstance(seen.get("size"), int) and seen["size"] > 0:
        size, size_source = seen["size"], "last"
    if not size:
        try:
            env_size = int(os.environ.get("FBTODO_PANE_SIZE") or 0)
        except ValueError:
            env_size = 0
        size, size_source = env_size or 12, "env" if env_size else "default"
    # The EDGE on that side: where the pane actually is, when that has been written down
    # (`remember_layout` files it after two passes, so it wins over any default), else the
    # edge FBTODO_SPLIT's place named, else `split-window`'s own — right for `h`, below for
    # `v`. A place in the environment therefore decides a BRAND-NEW pane's edge, and once
    # the owner drags it the file takes over; there is no pin half to type it with because
    # the file is the record of what the pane really is.
    seen_before = seen.get("before")
    before = seen_before if isinstance(seen_before, bool) else bool(env_before)
    return {
        "side": side, "side_source": side_source,
        "size": size, "size_source": size_source,
        "before": before,
        "window": key, "role": role,
    }


def kept_layout(pane: str, anchor: str | None, rects: dict, layout: dict) -> dict:
    """`layout` as it applies to THIS pane: the side in force, or the one it is kept on.

    A pin is a standing instruction somebody typed, so it decides the side and the keeper
    puts the pane back on that axis every pass. Without one, the side in force is only how
    a pane was OPENED (`pane_layout`: remembered, FBTODO_SPLIT, or the built-in default),
    and a pane found on the other axis of its session is the owner's own arrangement rather
    than drift: it is left where it is, and the side travels on as `seen` — which is the
    side `remember_layout` then files, after the same two passes a hand-resize gets, so a
    layout that shifts panes about on its own is never mistaken for a decision. A pane on
    no axis of its anchor keeps the side in force and is repaired as it always was, and so
    does one in another window (`pane_axis`).
    """
    if layout["side_source"].startswith("pin"):
        return layout
    seen = pane_axis(rects.get(anchor) if anchor else None, rects.get(pane))
    if not seen or seen == layout["side"]:
        return layout
    return {**layout, "side": seen, "side_source": "seen"}


def source_note(source: str, window: str) -> str:
    """A layout source as a person reads it — `pin (main:1 nas)` rather than `pin:nas`."""
    if source.startswith("pin:"):
        return f"pin ({window} {source.split(':', 1)[1]})"
    return {
        "pin": f"pin ({window})",
        "last": "remembered",
        "seen": "seen (kept, not filed yet)",
        "env": "FBTODO_SPLIT/FBTODO_PANE_SIZE",
    }.get(source, "default")


# The keeper's own memory of what it has already seen, so a pane's remembered size is
# applied ONCE when it appears (a standing application would make resizing by hand
# impossible) and so a value nobody chose — a border drag, a terminal resize, a pane
# squashed between others — is not written as if it were a choice.
_SETTLED: set[str] = set()


_LAST_SEEN: dict[str, tuple] = {}


def resize_pane_to(pane: str, rects: dict, side: str, size: int) -> bool:
    """Resize a list pane to a given size; True if it had to move.

    "A given size" is either a pin — enforced on every pass, that is what a pin is for —
    or the remembered size, applied once to a pane that has just appeared.
    """
    rect = rects.get(pane)
    if not rect or (rect["width"] if side == "h" else rect["height"]) == size:
        return False
    flag = "-x" if side == "h" else "-y"
    return tmux_run("resize-pane", "-t", pane, flag, str(size)) is not None


def hold_pane_size(pane: str, window: str, role: str, rects: dict, layout: dict) -> bool:
    """Hold a list pane at the size in force for it; True if it had to be resized.

    Three different promises in one place. A PIN is enforced on every pass — that is what a
    pin is for, and a hand-drag has to lose to it. The REMEMBERED size is applied once, to
    a pane that has just appeared: it is where the pane was left, not a standing
    instruction, and re-applying it every poll would make resizing by hand impossible —
    which is also why the pane is then forgotten, so the next one is welcomed the same way.
    The environment and the built-in default decide only how a NEW pane is split — they are
    what the opener reads (`pane_layout`) — never the size of one already on screen.
    """
    marker = f"{window}\u001f{role}\u001f{pane}"
    source = layout["size_source"]
    if not source.startswith("pin"):
        if source != "last" or marker in _SETTLED:
            return False
    _SETTLED.add(marker)
    return resize_pane_to(pane, rects, layout["side"], layout["size"])


def forget_settled(alive: set) -> None:
    """Drop settled panes that are no longer on the server — a new pane gets its size."""
    for marker in [m for m in _SETTLED if m.rsplit("\u001f", 1)[-1] not in alive]:
        _SETTLED.discard(marker)


def remember_layout(window: str | None, role: str, pane: str, rects: dict, side: str,
                    anchor: str | None = None) -> bool:
    """Remember how a list pane is sitting, so the next one opens in the same place.

    The side is the one placement just verified against the geometry. The size comes from
    the pane itself, and is ignored under three cells — that is a pane squashed by a small
    terminal, not a preference. So is the EDGE (`before`): which side of its session the
    list hangs off is the owner's, and the opener can only tell the difference by being
    told — `split-window -h` puts a new pane on the right, so a list the owner keeps on the
    left comes back on the left only because this was written down. One pass is not enough
    either: a drag or a resize moves a pane through values nobody chose, so the same
    numbers have to be seen twice before they are written. The caller keeps running (the
    keeper polls every few seconds), and the gate is its own memory, per window and role.
    """
    key = window_key(window)
    rect = rects.get(pane)
    if not key or not rect:
        return False
    size = rect["width"] if side == "h" else rect["height"]
    if size < 3:
        return False
    there = rects.get(anchor) if anchor else None
    before = bool(there and pane_before(there, rect, side))
    marker = f"{key}\u001f{role}"
    if _LAST_SEEN.get(marker) != (side, size, before):
        _LAST_SEEN[marker] = (side, size, before)
        return False
    last = load_last()
    entry = dict(last.get(key) or {})
    if entry.get(role) == {"side": side, "size": size, "before": before}:
        return False
    entry[role] = {"side": side, "size": size, "before": before}
    last[key] = entry
    save_last(last)
    return True


def save_pins(pins: dict) -> None:
    """Write the pins file: temp file, rename, like every other write here."""
    atomic_write_json(PINS_PATH, pins)


def window_of(target: str | None) -> str | None:
    """The window a pane (or window) target lives in — pins and layout are per window."""
    if not target:
        return None
    out = tmux_run("display-message", "-t", target, "-p", "#{window_id}")
    return out.strip() if out and out.strip() else None


# How long a keeper waits before deciding there is nothing left to keep a pane open
# for. Not zero: the wrapper splits the pane a moment BEFORE it starts the CLI, so the
# first passes of a fresh keeper see no instance at all.
KEEPER_GRACE = 90.0

# How long an ask waits for SOME keeper to claim before it reports that none did. A claim
# is normally taken in well under a second; the margin is spent only when a second ask's
# keeper is racing for the same claim — each ask starts a keeper before either has claimed
# — or when the machine is loaded enough that python and tmux are slow to start.
KEEPER_CLAIM_WAIT_S = 5.0

# How often the keeper asks whether its claim's NAME still points at it.
#
# A lock is only a claim while a reader can find it: the file can be removed or replaced
# under a running keeper (a probe's leftover cleanup, an admin's `rm`, a test), and the
# keeper then holds a lock on a file the name has left behind — `lock_peek` says the role is
# not running, the process half of the audit names it an orphan, and `locks --fix` ends it.
# A keeper reported dead and killed for it is the one failure this tick exists to prevent,
# so it is short: one `stat` per second next to a pane pass that walks tmux many times a
# second is nothing. The pane passes keep their own cadence (`--pane-seconds`); this only
# decides how often the claim is re-asked.
KEEPER_CLAIM_CHECK_S = 1.0


def _tmux_socket_dir() -> str:
    """Where tmux keeps its sockets: `$TMUX_TMPDIR` or `/tmp`, under `tmux-<uid>/`."""
    return os.path.join(os.environ.get("TMUX_TMPDIR") or "/tmp", f"tmux-{os.getuid()}")


def _tmux_default_socket() -> str:
    """The socket of the default server — what bare `tmux` talks to."""
    return os.path.join(_tmux_socket_dir(), "default")


def tmux_socket_of(name: str | None) -> str | None:
    """The socket a tmux name denotes, or None when the name names no server we can read.

    In plain words: a server is named by whichever spelling its context had, and the
    spellings have to be comparable — an ask must tell "the same server, written
    differently" from "another server", because the two askers write their own and used
    to read each other's as a stranger. Four spellings exist. The canonical one is the
    socket path itself (what `display-message -p '#{socket_path}'` answers). A raw `TMUX`
    value is `socket,server pid,session id` — the last two fields say which pane asked,
    not which server it is, so the socket is what is left of its last two commas. A
    forced `FBTODO_TMUX` is a command line: `-S path` names the socket outright, `-L
    name` the one in tmux's own directory, and no such flag means the default server,
    like bare `tmux`. `-default` is this program's old placeholder for exactly that
    default server, kept here so a record written before the name was canonicalised
    still compares as the server it meant. Anything else is no answer, and only string
    equality is left to it.
    """
    if not name:
        return None
    if name == "-default":
        return os.path.realpath(_tmux_default_socket())
    if " " in name:
        # the forced form — `tmux -L work`, or `tmux -S /path/sock -f conf` — and only
        # when its own command word is tmux; a socket path may hold a space of its own
        try:
            parts = shlex.split(name)
        except ValueError:
            return None
        if parts and os.path.basename(parts[0]).lower().startswith("tmux"):
            sock = None
            for at, tok in enumerate(parts[1:], start=1):
                if tok == "-S" and at + 1 < len(parts):
                    sock = parts[at + 1]
                elif tok == "-L" and at + 1 < len(parts):
                    sock = os.path.join(_tmux_socket_dir(), parts[at + 1])
                elif tok.startswith("-S") and len(tok) > 2:
                    sock = tok[2:]
                elif tok.startswith("-L") and len(tok) > 2:
                    sock = os.path.join(_tmux_socket_dir(), tok[2:])
            return os.path.realpath(sock or _tmux_default_socket())
    if name.startswith("/"):
        # a socket path, or a raw `TMUX` value with the server pid and session id on it
        if name.count(",") >= 2:
            name = name.rsplit(",", 2)[0]
        return os.path.realpath(name)
    return None


def same_tmux_server(a: str | None, b: str | None) -> bool:
    """Whether two tmux names denote one SERVER — the comparison every keeper ask makes.

    In plain words: an ask and a record are written by different processes, often of
    different builds, and each writes the spelling its own context had — the raw `TMUX`
    value (socket, server pid, session id), the old `-default`, a forced `-L work`, the
    canonical socket. Compared as strings those read as two servers, and the ask replaces
    a keeper that is already on the right one: measured 2026-10-02, the pane and the path
    that spawns the watcher did exactly that several times an hour. Both names are reduced
    to the socket each denotes (`tmux_socket_of`) and compared by real path. A name that
    reduces to nothing — an empty record, a stanza nobody recognises — is equal only to
    itself, so a difference stays a difference rather than becoming a guess.
    """
    if a == b:
        return True
    if a is None or b is None:
        return False
    sock_a, sock_b = tmux_socket_of(a), tmux_socket_of(b)
    return sock_a is not None and sock_a == sock_b


def tmux_identity() -> str | None:
    """Which tmux server this program talks to, named the one way every context agrees on.

    In plain words: a pane spells its tmux as the `TMUX` value — socket, server pid and
    session id — while the desktop integration, launched outside tmux, has no `TMUX` at all
    and used to say only `-default`. Both name the same server, and compared raw they read
    as two: measured 2026-10-02, each ask killed the other's keeper (`replacing keeper …`)
    several times an hour. So the name is the one thing both contexts can spell identically
    — the SERVER's own socket path, asked of the server itself (`display-message -p
    '#{socket_path}'`), which answers the same string inside a session and from a shell with
    no `TMUX` at all. `FBTODO_TMUX` is that override verbatim: a forced server is named
    deliberately, and it is the same string in every context that sets it. None is the
    honest answer when nothing can be asked and there is no `TMUX` to read: this context
    cannot NAME a server, and callers must not read that as a different one. Naming is not
    comparing, though — a name written by another build or another spelling is read back
    through `same_tmux_server`.
    """
    forced = os.environ.get("FBTODO_TMUX")
    if forced:
        return forced
    out = tmux_run("display-message", "-p", "#{socket_path}")
    if out and out.strip():
        return os.path.realpath(out.strip())
    tmux = os.environ.get("TMUX")
    if tmux:
        # A dead server still has its name in `TMUX`; the socket is the name, and the
        # pid and session id after it say which pane asked, not which server it is.
        return tmux_socket_of(tmux) or tmux
    return None


def tmux_identity_outside() -> str | None:
    """The name an ask from OUTSIDE tmux writes: the shell autostart's, the desktop's.

    `tmux_identity` answers for the context it runs in — with `TMUX` set that is the
    pane's server, without it the default one — while the ask that spawns a watcher comes
    from outside any pane: the autostart runs before a pane exists, and the desktop
    integration is not in tmux at all. `status` compares that ask with the keeper's record
    even when `status` itself is typed inside a pane, so `TMUX` is taken away for the one
    question and put back. `FBTODO_TMUX` still answers verbatim: a forced server is named
    deliberately, and that name is the same from every context.
    """
    saved = os.environ.pop("TMUX", None)
    try:
        return tmux_identity()
    finally:
        if saved is not None:
            os.environ["TMUX"] = saved


def keeper_serves(rec: dict, target: str | None) -> bool:
    """Whether the keeper a record names is the one this ask needs: current, same server.

    In plain words: a keeper is one per server, so an ask that names a server needs the
    keeper to be recording that same one — compared as a SERVER, not as a string
    (`same_tmux_server`), because an ask and a record are written by different builds with
    different spellings: the raw `TMUX` value, the old `-default`, a forced server and the
    canonical socket all reduce to the socket they denote. An ask that names NONE (no
    server to ask, no `TMUX` to read) has nothing to compare, so it must not evict what it
    cannot judge — even with one spelling, an ask that cannot tell one server from another
    would kill the keeper of the server it is not on. A keeper from an older build is
    served by neither rule — nothing re-imports a running process, so an upgrade has to
    replace it.
    """
    if rec.get("version") != VERSION:
        return False
    if target is None:
        return True
    return same_tmux_server(rec.get("tmux"), target)


def ensure_pane_keeper(args, quiet: bool = True) -> int | None:
    """Have somebody watching the panes — one keeper per tmux server, not one per session.

    Called from the places that already know a session just appeared (the pane's own
    `ensure_daemon`, and the shell autostart that spawns a watcher). Its own process and
    its own lock: a watcher cannot promise this, because the lock that makes it a watcher
    is exactly what a `-s nas` daemon can be holding while your own window's pane is
    gone.
    """
    if pane_off() or args.pane_seconds <= 0:
        return None
    target = tmux_identity()
    rec = read_json(PANE_KEEPER_PATH, {}) or {}
    pane_log(
        f"asked for a keeper (tmux {target or 'unnameable'}; record "
        + (f"{rec.get('pid')} for {rec.get('tmux') or 'unnameable'}"
           if rec.get("pid") else "none")
        + ")"
    )
    # An ask is an ACTOR too: `lock_holder` both answers "is somebody keeping panes" and
    # removes a leftover record that names a dead pid, so the keeper it decides to start
    # is not blocked by a name nothing holds.
    pid = lock_holder(PANE_KEEPER_PATH)
    if pid and keeper_serves(rec, target):
        return int(pid)
    if pid:
        # An older build, or a keeper for another server: it cannot serve this one, and
        # leaving it in place would block the keeper that can (measured in the self-check:
        # a keeper watching a since-killed private server kept every pane it was asked to
        # keep, and never saw them).
        try:
            os.kill(int(pid), signal.SIGTERM)
        except OSError:
            pass
        for _ in range(40):
            if not pid_alive(int(pid)):
                break
            time.sleep(0.05)
        clear_lock(int(pid), path=PANE_KEEPER_PATH)
        pane_log(
            f"replacing keeper {pid} (tmux {rec.get('tmux') or 'unnameable'} -> "
            f"{target or 'unnameable'})"
        )
    argv = [
        *self_argv(), "pane-watch",
        "--foreground", "--quiet", "--pane-seconds", str(args.pane_seconds),
    ]
    log = open(PANE_LOG_PATH, "ab", buffering=0)
    try:
        child = subprocess.Popen(
            argv, stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True
        )
    except OSError as exc:
        pane_log(f"could not start the keeper: {exc.__class__.__name__}")
        return None
    finally:
        log.close()
    # Wait for the claim — whoever won it. Two asks arrive together (the pane's own
    # `ensure_daemon` and the shell autostart, or two panes opening in one breath), each
    # starts a keeper before either has claimed, and exactly one wins: the other sees the
    # winner and stands down (`keeper_serves`, or `write_lock` failing). The ask that
    # spawned the stand-down has still got a keeper — the winner's — so the pid returned
    # is the HOLDER's, not necessarily this child's: a loss is an answer, not a failure.
    # Asking `lock_holder` is what makes it so, and it is also what used to break it: the
    # loser's own poll could take the winner's just-created claim (free, still empty) and
    # remove it as a leftover, so the winner kept every pane with nothing on disk to name
    # it and the ask logged "never claimed the lock" (the empty-file rule is in locks.py).
    deadline = time.monotonic() + KEEPER_CLAIM_WAIT_S
    while time.monotonic() < deadline:
        time.sleep(0.05)
        pid = lock_holder(PANE_KEEPER_PATH)
        if pid:
            return int(pid)
    pane_log(f"keeper {child.pid} never claimed the lock (every {args.pane_seconds:.0f}s)")
    if not quiet:
        print("fbtodo: the pane keeper did not start; see " + PANE_LOG_PATH, file=sys.stderr)
    return None


def keeper_reclaim(target: str | None, started_ms=None) -> str:
    """Take the keeper's claim BY NAME again after the name moved, or stand down.

    In plain words: a claim is a lock AND a name, and the keeper can lose the name half
    without losing the lock — the file is removed or replaced, and the kernel lock on the
    unlinked inode keeps answering `lock_ours` yes about a file nobody can find. Left alone
    the keeper watches panes invisibly until an operator notices: the audit's process half
    names it an orphan (nothing on disk ties it), and `locks --fix` ends it as dead weight.
    So the keeper re-takes the name it writes every pane record through: a FREE or ABSENT
    name is claimed again (`write_lock` re-claims on its own, dropping the registry entry
    that no longer points at the name), and a name a live process already HOLDS belongs to
    that process — another keeper serves this root now, so this one stands down rather than
    fight it, exactly as its start does.

    `started_ms` travels into the rewritten record, so a keeper that has been watching for
    hours is not reported as one that started a second ago. Returns `"reclaimed"`, or the
    stop reason when the claim is somebody else's.
    """
    extra = {"tmux": target}
    if started_ms:
        extra["started_ms"] = started_ms
    if not write_lock(os.getcwd(), None, path=PANE_KEEPER_PATH, extra=extra):
        return "claim taken by another keeper"
    return "reclaimed"


def cmd_pane_watch(args) -> int:
    """Keep every local session's todo pane open, for as long as there are sessions.

    A process of its own because nothing else can promise it: the pane is the thing being
    watched, so it cannot watch itself, and a watcher is bound to the lock it holds while
    this has none of its own to lose. It reads the process table and tmux the way the rest
    of this program does, keeps every session's pane (not just one), and leaves when the
    last freebuff does — so it never outlives what it is for. Like the pane and the
    watcher, it starts itself over when the build under it changes, carrying its claim
    through the exec.
    """
    # A keeper that re-execs itself (see the build check in the loop below) hands its claim
    # over the exec — same pid, same open file description — so the claim is taken BACK
    # before anything asks whether one is free. Without this the record still names this
    # very process, and the guard would stand down the keeper it just restarted; with it,
    # the claim is already in hand and `write_lock` below merely refreshes the record
    # through the same fd. Nothing to take is an ordinary start.
    carried = lock_adopt(PANE_KEEPER_PATH)
    target = tmux_identity()
    # The keeper is an ACTOR: the probe is the right question here, because a free record
    # is a leftover this keeper may claim (and must not be told is a holder), while only a
    # HELD claim is a keeper already serving this server.
    running = lock_holder(PANE_KEEPER_PATH)
    rec = (read_json(PANE_KEEPER_PATH, {}) or {}) if running else {}
    if not carried and running and keeper_serves(rec, target) and not args.force:
        if not args.quiet:
            print(f"pane keeper already running (pid {running})", file=sys.stderr)
        return 0
    if not write_lock(os.getcwd(), None, path=PANE_KEEPER_PATH, extra={"tmux": target}):
        if not args.quiet:
            print("pane keeper already running (another process holds the lock)",
                  file=sys.stderr)
        return 0
    # When this keeper began, carried across a re-claim (see `keeper_reclaim`): a record
    # rewritten an hour in must still say when the watch started.
    claim_started = (read_json(PANE_KEEPER_PATH, {}) or {}).get("started_ms")
    pane_log(
        f"keeper started (pid {os.getpid()}, tmux {target or 'unnameable'}, "
        f"every {args.pane_seconds:.0f}s)"
    )

    # When the keeper next asks whether it is still the build on disk. The same question the
    # pane and the watcher ask, for the same reason: nothing re-imports a running process, so
    # a keeper left over from an earlier version would keep reopening panes with its older
    # pin — and every pane it repaired would be running code the keeper itself is not. The
    # claim crosses that exec with it (`lock_handoff`/`lock_adopt`, taken back at the top):
    # a keeper standing down in between would leave the panes unwatched with a record still
    # naming it. `held_note` remembers the broken save already logged, so a half-written
    # file is noted once rather than once per tick.
    next_build_check = 0.0
    held_note = None

    stop_reason, code = "stopped", 0

    def shutdown(signum=None, _frame=None):
        nonlocal stop_reason, code
        stop_reason = f"signal-{signum}" if signum else "stopped"
        code = 128 + signum if signum else 0
        raise SystemExit(code)

    if not args.once:
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, shutdown)
    empty_since = None
    gone_passes = 0
    next_claim_check = 0.0  # the claim is asked about before the first pass
    next_pass = 0.0
    try:
        while True:
            now = time.time()
            # The claim, before the panes: a lock whose name has moved is not a claim, and a
            # keeper that keeps panes while nobody can find it is the orphan the audit names
            # and `locks --fix` ends. Ask every tick, take the name again, or stand down for
            # the keeper that holds it now.
            if now >= next_claim_check:
                next_claim_check = now + KEEPER_CLAIM_CHECK_S
                if not lock_ours(PANE_KEEPER_PATH):
                    gone = not os.path.exists(PANE_KEEPER_PATH)
                    outcome = keeper_reclaim(target, claim_started)
                    if outcome != "reclaimed":
                        stop_reason = outcome
                        break
                    pane_log(
                        f"keeper re-claimed its name (pid {os.getpid()}, the file was "
                        + ("removed)" if gone else "replaced)")
                    )
            if now < next_pass:
                # Waiting for the next pass — or for the next claim check, whichever comes
                # first. A short claim tick must not turn every wake into a tmux survey, so
                # the pane work below is what `--pane-seconds` paces.
                time.sleep(min(KEEPER_CLAIM_CHECK_S, next_pass - now))
                continue
            next_pass = now + max(0.5, min(args.pane_seconds, 30.0))
            if now >= next_build_check:
                next_build_check = now + BUILD_CHECK_S
                changed = source_newer_than(STARTED_AT, now)
                if changed:
                    # A parse is not enough to hand the process over to: a tree that compiles
                    # file by file can still die at import (an imported name that no longer
                    # exists, a raise at module scope), and the exec below would replace a
                    # running keeper with one that never starts — leaving every pane unwatched.
                    # So the new build is asked to load first (`reload_probe_error`); only a
                    # build that answers yes is executed into, and an unfit one is held on,
                    # keeping the last build that provably loaded.
                    broken = source_syntax_error()
                    unfit = broken or reload_probe_error()
                    if unfit:
                        if unfit != held_note:  # once per distinct problem, not once a tick
                            held_note = unfit
                            pane_log(
                                f"keeper holding, source does not parse: {unfit}" if broken
                                else f"keeper holding, the new build does not load: {unfit}",
                            )
                    else:
                        pane_log(f"keeper reloading: {changed} changed after this "
                                 "process started")
                        fd = lock_handoff(PANE_KEEPER_PATH)
                        argv = self_argv()
                        try:
                            os.execv(argv[0], [*argv, *sys.argv[1:]])
                        except OSError as exc:
                            # Still this keeper: put the claim's fd back the way this process
                            # keeps it, and record that the reload did not happen — once per
                            # distinct failure, because the source is still newer on the next
                            # tick and a line every tick is a flood, not a note.
                            if fd is not None:
                                os.environ.pop(LOCK_FD_ENV, None)
                                try:
                                    os.set_inheritable(fd, False)
                                except OSError:
                                    pass
                            note = f"reload failed: {exc.__class__.__name__}"
                            if note != held_note:
                                held_note = note
                                pane_log(f"keeper {note}")
            # A tmux server always has at least one pane, so an empty list means the
            # server this keeper belongs to is gone — and a keeper watching a dead server
            # can never put a pane back. Three passes, because a server can be busy for a
            # moment; then it leaves, which is also what keeps a test from leaving one
            # behind (measured: a suite of them accumulated while the private servers they
            # watched were killed).
            if pane_rows():
                gone_passes = 0
            else:
                gone_passes += 1
                if gone_passes >= 3:
                    stop_reason = "tmux server gone"
                    break
            for pane in ensure_local_panes(quiet=args.quiet):
                # Written down quiet or not: a pane that came back is the one thing worth
                # being able to see afterwards, and a background process has no stderr
                # anybody reads (this is the file `--help` points at).
                pane_log(f"reopened {pane} for a session in {target}")
            if args.once:
                return 0
            if freebuff_pids():
                empty_since = None
            elif empty_since is None:
                empty_since = time.monotonic()
            elif time.monotonic() - empty_since > KEEPER_GRACE:
                stop_reason = "no instance"
                break
            # Waiting for the next pass happens at the top of the loop, where it shares one
            # clock with the claim check; a second sleep here would pace the tick twice.
    except SystemExit:
        pass
    except Exception as exc:
        stop_reason, code = f"error: {exc.__class__.__name__}", EX_CODES["tempfail"]
    finally:
        clear_lock(os.getpid(), path=PANE_KEEPER_PATH)
        pane_log(f"keeper stopped ({stop_reason})")
    if not args.quiet and stop_reason:
        print(f"fbtodo pane keeper stopped ({stop_reason})", file=sys.stderr)
    return code


def pane_command(argv) -> str:
    """A pane's command line: an absolute interpreter, under an explicit PATH.

    In plain words: the pane gets the environment its opener meant, not the one the tmux server
    hands it. `self_argv()` already names the interpreter and the launcher absolutely, so the
    pin's other half is the PATH in front of them — the one that decides what the pane's own
    children resolve: `#!/usr/bin/env python3` in `scripts/notify/`, `tmux`, `ssh`, and the
    watcher the pane starts.

    `env` carries the assignment rather than a bare `VAR=value command` prefix, because the
    pane command is run by the owner's login shell and fish has no such prefix form. The
    assignment is written into the command string on purpose: it rides along in
    `pane_start_command`, which is what `fbtodo status`, `fbtodo why` and the keeper read back
    — so a pane respawned by hand (`tmux respawn-pane`) gets the same pin back with no help.
    """
    # `/usr/bin/env` where it exists, the plain name elsewhere: this runs before the pane's own
    # PATH can have been fixed, so resolving `env` by name is the one lookup that must not
    # matter. On every system this runs on it is there.
    env = "/usr/bin/env" if os.path.exists("/usr/bin/env") else "env"
    return " ".join(shlex.quote(part) for part in [env, f"PATH={pinned_path()}", *argv])


def pane_python(pane: str, rows=None, table=None) -> str | None:
    """The interpreter a pane is actually running, or None when it cannot be told.

    In plain words: the pin says what that pane SHOULD be on, and this says what it is. Read
    from the process table rather than asked of the pane: a pinned pane's line names its
    interpreter outright, so `fbtodo status` can print it beside the watcher's and settle the
    question from outside — which is how this whole pin was discovered, by noticing two
    processes that disagreed.

    The pane's own process is asked first, then its nearest descendants: the login shell that
    runs a pane command does not always hand its number over (zsh forks rather than execs when
    the command starts with a variable assignment, which is exactly what a pinned command
    string begins with), so looking one level down is what makes an answer arrive in both
    shapes — measured, with a stub server, before this walk was added.
    """
    rows = pane_rows() if rows is None else rows
    table = process_table() if table is None else table
    pid = next((row["pid"] for row in rows if row["pane"] == pane), 0)
    if not pid:
        return None
    for cand in (pid, *(one for one, _depth in descendant_pids(table, pid))):
        found = python_of((table.get(cand) or (0, ""))[1])
        if found:
            return found
    return None


def tree_pythons(pane: str, rows=None, table=None) -> list[tuple[int, str]]:
    """Every interpreter under a pane, in tree order — the pane's own answer first.

    In plain words: `pane_python` stops at the first answer because its question is "what
    is this pane on". A pane does not run alone, though — a keeper under it, a watcher it
    started, a notify bell through either, a tmux child — and one of those resolving a
    different Python is a disagreement a reader that stops at the pane can never see. So
    this walks the whole subtree and answers with every process whose line names an
    interpreter, the pane's pid first: the first entry is exactly what `pane_python` would
    have said, and the rest is what the tree is made of.

    Bounded by the process table, not by guessing: a descendant that has gone, or one whose
    line is a shell or a tmux client, is no entry at all (`python_of`).
    """
    rows = pane_rows() if rows is None else rows
    table = process_table() if table is None else table
    pid = next((row["pid"] for row in rows if row["pane"] == pane), 0)
    if not pid:
        return []
    found = []
    for cand in (pid, *(one for one, _depth in descendant_pids(table, pid))):
        python = python_of((table.get(cand) or (0, ""))[1])
        if python:
            found.append((cand, python))
    return found


def tree_drift(pane: str, rows=None, table=None) -> tuple[int, str] | None:
    """(pid, interpreter) of the first process under a pane on another Python, else None.

    In plain words: the pane's own interpreter is the reference — the tree is supposed to
    agree with the process it serves — and every other member is compared with it by REAL
    PATH, the same rule `pane_drifted` uses, because two spellings of one interpreter are
    not a drift. What comes back is the EVIDENCE, not just a yes: the pid and the
    interpreter that disagreed, which is what the keeper's log names when the pane is
    reopened, and the only way to tell which child was the odd one when a tree has several.

    None is "no answer" as much as "agrees": a pane with no interpreter anywhere in its
    tree (still starting, or a plain shell) has nothing to compare, and nothing is repaired
    on a guess.
    """
    found = tree_pythons(pane, rows, table)
    if not found:
        return None
    _first, reference = found[0]
    want = os.path.realpath(reference)
    for at, python in found[1:]:
        if os.path.realpath(python) != want:
            return at, python
    return None


# How long a keeper leaves a drifted pane alone after one attempt at it. The repair is a
# single tmux call, but a pane that cannot be put right — a tmux that refuses, a pane that
# left between the read and the call — would otherwise be asked again on every pass, and a
# pass is seconds apart.
DRIFT_RETRY_S = 60.0

# What this keeper has already tried, and what it has already said, about a pane: the same
# kind of memory `_SETTLED` and `_LAST_SEEN` keep for placement, and per process on purpose
# — a keeper that restarts has no idea what the last one did, which is what a fresh start
# should mean. `_DRIFT_HELD` is the pin case (the pane is already on this build's pin) and
# `_DRIFT_KEPT` the knob case (`@fbtodo_repair off`); both are said once per pane, in the
# log and on the pane's chip, not once per pass.
_DRIFT_TRIED: dict[str, float] = {}
_DRIFT_HELD: set[str] = set()
_DRIFT_KEPT: set[str] = set()

# How long a note left for a pane waits to be claimed. A reopened pane claims it within its
# own startup (`cmd_pane`), seconds at the outside; past this the note is dropped unshown,
# because a process that takes that pane id later — a split reusing it, a hand respawn — is
# not the repair it was meant for and must not be told it was repaired.
PANE_NOTE_S = 15.0


def pane_note_write(pane: str, was: str, child: int | None = None,
                    kind: str = "reopen") -> None:
    """Leave the note a pane the keeper acted on will read (see `pane_note_take`).

    In plain words: the log says which pane was reopened and from what, and the pane itself
    says it on its title chip so the person who was watching it is not left with a process
    they did not restart. Written BEFORE the respawn — the pane cannot read a file that is
    not there yet — and the entry is keyed by pane id, so a pass that reopens two panes
    leaves two notes and each claims its own. `kind` says which act: `reopen` for a
    respawn, claimed by the process it starts; `kept` for a drift the keeper left alone
    because repair is off (@fbtodo_repair) — no restart is coming to deliver that one, so
    the RUNNING pane picks it up on its own (see `cmd_pane`).
    """
    note = read_json(PANE_NOTE_PATH, {}) or {}
    if not isinstance(note, dict):
        note = {}
    note[pane] = {"at_ms": int(time.time() * 1000), "was": was, "child": child,
                  "kind": kind}
    try:
        atomic_write_json(PANE_NOTE_PATH, note)
    except OSError:
        pass


def pane_note_forget(pane: str) -> None:
    """Take back a note whose repair never happened, so nothing else can claim it."""
    note = read_json(PANE_NOTE_PATH, {}) or {}
    if isinstance(note, dict) and note.pop(pane, None) is not None:
        try:
            atomic_write_json(PANE_NOTE_PATH, note)
        except OSError:
            pass


def pane_note_take(pane: str | None, kind: str | None = None,
                   sweep: bool = True) -> dict | None:
    """Claim the note left for this pane, once — or None when there is none, or it is stale.

    In plain words: a pane asks this on its first breath, naming itself by `TMUX_PANE`; the
    entry is removed whether or not it is fresh, so a note is shown once and an old one is
    gone rather than waiting for the next process to take that id. `kind` filters the
    claim: a running pane's own poll asks for `kept` only, so it can never swallow the
    `reopen` note waiting for the process that will replace it; the startup claim asks for
    any kind. The startup claim also sweeps other panes' stale entries, which is what keeps
    the file to the panes being repaired right now rather than to every pane ever repaired
    (`atomic_write_json` each pass, so a reader never sees half of it) — a running pane
    asks every second and passes `sweep=False`, leaving the file alone when there is
    nothing of its own to take.
    """
    if not pane:
        return None
    note = read_json(PANE_NOTE_PATH, {}) or {}
    if not isinstance(note, dict):
        return None
    now_ms = time.time() * 1000
    mine = note.get(pane) if isinstance(note.get(pane), dict) else None
    if mine is not None and kind is not None and (mine.get("kind") or "reopen") != kind:
        return None  # not this reader's note: leave it for the one that will show it
    mine = note.pop(pane, None)
    stale = [other for other, entry in note.items()
             if not isinstance(entry, dict)
             or now_ms - float(entry.get("at_ms") or 0) > PANE_NOTE_S * 1000]
    if sweep:
        for other in stale:
            del note[other]
    if mine is not None or (sweep and stale):
        try:
            atomic_write_json(PANE_NOTE_PATH, note)
        except OSError:
            pass
    if isinstance(mine, dict) and now_ms - float(mine.get("at_ms") or 0) <= PANE_NOTE_S * 1000:
        return mine
    return None


# The spellings that MEAN off. A person may write any of them; anything else — including
# unset, which reads as an empty string — leaves the repair running.
REPAIR_OFF_WORDS = ("off", "no", "0", "false")


def repair_is_off(value: str | None) -> bool:
    """Whether a `@fbtodo_repair` value marks the knob off — unset is not off."""
    return (value or "").strip().lower() in REPAIR_OFF_WORDS


def pane_repair_value(pane: str) -> str | None:
    """The knob's effective value for `pane` ('' when unset), or None when tmux cannot answer.

    Asked through `display-message`, so tmux resolves it the way it resolves everything
    else — pane, then window, then server — and an unset pane inherits whatever was chosen
    above it, which is exactly what the keeper reads (`pane_repair_off`). The pane's own id
    is read in the same breath, because a target that does not exist is NOT a pane with the
    option unset: tmux answers an empty string for both (measured 2026-10-02), so an empty
    id is the "no such pane" answer, not a knob that happens to be off.
    """
    out = tmux_run("display-message", "-p", "-t", pane, "#{pane_id} #{@fbtodo_repair}")
    if out is None:
        return None
    ident, _, value = out.partition(" ")
    return value.strip() if ident.strip() else None


def set_pane_repair(target: str | None, value: str | None) -> bool:
    """Set the knob for `target` (None = the whole server); None as the VALUE unsets it.

    The scope is the one the knob documents: `-p -t <pane>` is one pane, `-g` the server
    every pane inherits from, and `-u` removes the choice at that scope instead of writing
    a value over it, so "default" really inherits again. False means tmux refused — a
    target that is gone, or no server at all — never a guess. The middle rung has its own
    writer (`set_window_repair`), because `-p` needs a pane and a window target is not one.
    """
    argv = ["set-option", "-g"] if target is None else ["set-option", "-p", "-t", target]
    if value is None:
        return tmux_run(*argv, "-u", "@fbtodo_repair") is not None
    return tmux_run(*argv, "@fbtodo_repair", value) is not None


def window_repair_value(window: str) -> str | None:
    """The knob's effective value for `window` ('' when unset), or None when tmux cannot answer.

    The middle rung of the chain `pane_repair_value` reads: asked through `display-message`
    against a window target, tmux resolves window, then server — the pane rung is simply
    not part of a window's inheritance — and an empty window id is the "no such window"
    answer the same way an empty pane id is for a pane.
    """
    out = tmux_run("display-message", "-t", window, "-p", "#{window_id} #{@fbtodo_repair}")
    if out is None:
        return None
    ident, _, value = out.partition(" ")
    return value.strip() if ident.strip() else None


def set_window_repair(window: str, value: str | None) -> bool:
    """Set the knob for one WINDOW (None as the VALUE unsets it there); False = tmux refused.

    `-w -t <window>` is the rung `set_pane_repair`'s `-p` and `-g` straddle: every pane in
    the window inherits it, panes in its siblings do not, and `-u` removes this window's own
    choice so the server's applies again instead of a value being written over it.
    """
    argv = ["set-option", "-w", "-t", window]
    if value is None:
        return tmux_run(*argv, "-u", "@fbtodo_repair") is not None
    return tmux_run(*argv, "@fbtodo_repair", value) is not None


def pane_repair_off(pane: str) -> bool:
    """Whether the keeper's automatic repair is OFF for this pane — the `@fbtodo_repair` knob.

    In plain words: a machine can mix interpreters on purpose — a pane held on an older
    Python while everything else runs the pin — and the automatic repair would fight that
    choice on every pass, respawning the pane the person deliberately started. The knob is
    a tmux user option, so it is per PANE by construction and needs no list of ids:
    `tmux set -p -t %3 @fbtodo_repair off` — or `-w` / `-g` for a window or the whole
    server, since tmux resolves the option up the chain and this reads it back the same
    way (`pane_repair_value`). Anything but off/no/0/false leaves the repair running: unset
    is the old behaviour, which is also what a machine that never heard of the knob gets.
    `fbtodo keep` is this sentence as a command, so nobody has to remember the incantation.
    The pane is still DIAGNOSED either way — the keeper logs what it saw and leaves a note
    the running pane shows on its chip (`KEPT (was on …)`); only the respawn is withheld.
    """
    return repair_is_off(pane_repair_value(pane))


def pane_drifted(pane: str, watcher_python: str | None, rows=None, table=None) -> str | None:
    """The interpreter a pane is on when it disagrees with the watcher's, else None.

    In plain words: `pane_python` says what a pane is running and the watcher's own line
    says what it is running, and the pin (`pane_command`) exists to make the two the same.
    This is the question that finds the one that is not — `/usr/bin/python3` staring back
    at a pane whose watcher is on Homebrew's. Both ends are read from the processes, never
    assumed, and they are compared by REAL PATH: `/opt/homebrew/bin/python3` and the
    Cellar's `python3.14` are one interpreter, and calling that a drift would have the
    keeper respawn a pane that is already right.

    None is "no answer", not "fine": a pane that has not finished starting, a line that
    names no interpreter, or no watcher to compare against are all cases where a respawn
    would be a guess. The pane's interpreter is returned only when both ends are known and
    really differ.
    """
    if not watcher_python:
        return None
    pane_py = pane_python(pane, rows, table)
    if pane_py and os.path.realpath(pane_py) != os.path.realpath(watcher_python):
        return pane_py
    return None


def repair_drifted_panes(wanted, watcher_python, rows=None, table=None) -> list[str]:
    """Reopen every pane that is on a different interpreter than its watcher's, in place.

    In plain words: `ensure_local_panes` already reopens a pane that is GONE; this is the
    sibling repair for one that is there but on the wrong Python. `respawn-pane -k` runs the
    pinned command in the SAME pane — same id, same window, same row of the layout the
    geometry pass just verified — which is exactly what a hand would have done after
    `fbtodo status` said the two disagreed. The keeper can deliver it because it is the
    process that would have opened the pane in the first place: the pin it reopens with is
    its own `self_argv()` (`local_pane_command`), the same one a fresh split gets, and the
    assignment rides in the command string so the PATH comes with it.

    The disagreement is read twice over. First the pane itself, against the watcher's own
    line (`pane_drifted`), as before. Then — when those two agree — the pane's whole TREE:
    a notify bell or a tmux child that resolved another Python than the pane's is a
    disagreement of the same shape one level down (`tree_drift`), and the pane is reopened
    on the pin so the tree it starts next starts from the pin.

    Three things keep that from becoming a respawn loop. A pane whose command is already this
    build's pin is left alone: respawning it would re-run the command it is running, so the
    pane is not the one out of step — the keeper's own interpreter is, or a child started
    from a PATH the pane's pin cannot change — and either is said in the pane log once, not
    acted on every pass. A pane the operator marked `@fbtodo_repair off` is left alone the
    same way and for the opposite reason: the mix is deliberate, so the keeper keeps the
    pane, says what it saw in the log and on the pane's own chip (`KEPT (was on …)`) — but
    does not respawn it (`pane_repair_off`). And an attempt is remembered (`DRIFT_RETRY_S`),
    so a repair that could not be delivered is retried, not hammered. A pane still starting
    answers nothing (`pane_drifted`), which is the last brake: the repair only ever acts on
    ends that can both be read.
    """
    rows = pane_rows() if rows is None else rows
    table = process_table() if table is None else table
    repaired = []
    for inst in wanted:
        pin = local_pane_command(inst)
        for pane in local_pane_ids(inst, rows, table):
            # First the pane's own line against the watcher's — and when those two agree,
            # the tree under it: a notify bell or a tmux child that resolved another
            # Python is the same disagreement one level down (`tree_drift`), and the
            # repair is the same: the pane goes back on the pin, and the tree it starts
            # next starts from that pin.
            was = pane_drifted(pane, watcher_python, rows, table)
            child = None
            if not was:
                child = tree_drift(pane, rows, table)
                if not child:
                    continue
                was = child[1]
            started_with = next((row["start"] for row in rows if row["pane"] == pane), "")
            if started_with == pin:
                if pane not in _DRIFT_HELD:
                    _DRIFT_HELD.add(pane)
                    if child is None:
                        pane_log(
                            f"held {pane}: it is on {was} while the watcher is on "
                            f"{watcher_python}, but its command already carries this "
                            "build's pin"
                        )
                    else:
                        pane_log(
                            f"held {pane}: a child (pid {child[0]}) is on {was} while the "
                            "pane carries this build's pin — re-running the pin cannot "
                            "change what that child resolved, so the launch is what has to"
                        )
                continue
            # The knob, checked before any attempt is made: a pane marked `@fbtodo_repair off`
            # is kept as it is — the diagnosis still happens, and the pane is told what the
            # keeper saw, but the respawn is withheld. The entry is dropped the moment the
            # knob is back on, so a later hold is said again rather than swallowed. Read
            # every pass on purpose: the option is the operator's, and turning it back on has
            # to resume the repair without restarting the keeper.
            if pane_repair_off(pane):
                if pane not in _DRIFT_KEPT:
                    _DRIFT_KEPT.add(pane)
                    pane_note_write(pane, was, child=child[0] if child else None, kind="kept")
                    from_what = (f"it is on {was} while the watcher is on {watcher_python}"
                                 if child is None
                                 else f"a child (pid {child[0]}) is on {was} while the "
                                      f"watcher is on {watcher_python}")
                    pane_log(f"kept {pane}: {from_what} — repair is off (@fbtodo_repair)")
                continue
            _DRIFT_KEPT.discard(pane)
            # `None`, not `0.0`, for "never tried": `time.monotonic()` is not promised to be
            # far from zero (measured 2026-10-02: 0.57 s on this machine, so the first attempt
            # at a pane was read as one made 0.57 s ago and skipped for a minute).
            tried = _DRIFT_TRIED.get(pane)
            now = time.monotonic()
            if tried is not None and now - tried < DRIFT_RETRY_S:
                continue
            _DRIFT_TRIED[pane] = now
            # The note goes down before the respawn (the pane cannot read a file that is not
            # there yet) and comes back up if the respawn did not happen — a note whose
            # repair never ran must not be claimed by whatever process comes next.
            pane_note_write(pane, was, child=child[0] if child else None)
            if tmux_run("respawn-pane", "-k", "-t", pane, pin) is None:
                pane_note_forget(pane)
                continue
            repaired.append(pane)
            from_what = (f"was {was}; watcher {watcher_python}" if child is None
                         else f"a child, pid {child[0]}, was on {was}")
            pane_log(f"reopened {pane} on the pinned interpreter ({from_what})")
    # What was remembered about a pane that is gone is not about the next one that takes
    # its id: the same sweep `forget_settled` makes for placement, and it is what keeps
    # these two memories bounded by the panes on screen rather than by the keeper's uptime.
    alive = {row["pane"] for row in rows}
    for pane in [p for p in _DRIFT_TRIED if p not in alive]:
        del _DRIFT_TRIED[pane]
    for pane in [p for p in _DRIFT_HELD if p not in alive]:
        _DRIFT_HELD.discard(pane)
    for pane in [p for p in _DRIFT_KEPT if p not in alive]:
        _DRIFT_KEPT.discard(pane)
    return repaired


def local_pane_command(instance_pid) -> str:
    return pane_command([
        *self_argv(),
        "--watch-pid", str(instance_pid), "--stale-after", "0",
    ])


def local_pane_open(cwd: str, instance_pid, window: str, rows=None, table=None) -> str | None:
    # What this window is set to: its pin for this role, then the window's shared pin, then
    # what the pane was left at last time, then the two global knobs (`pane_layout`).
    layout = pane_layout(window, "local")
    split, size = layout["side"], str(layout["size"])
    # The edge the owner last kept it on (`before`), because the splitter's own answer is
    # always the trailing one: without this a list kept in the left column comes back on
    # the right, and one kept above the session comes back under it.
    before = ["-b"] if layout.get("before") else []
    # Split the pane the session is DRAWN in, not merely its window: given a window, tmux
    # picks that window's *active* pane, which is not necessarily the one running freebuff,
    # and the list then opens beside somebody else's pane — measured live, where a NAS
    # pane and a session pane sharing a window was enough to land a fresh pane in the
    # wrong column. The window stands in only when the instance's own pane is not found.
    target = freebuff_pane_id(instance_pid, rows=rows, table=table) or window
    out = tmux_run(
        "split-window", *before, f"-{split}", "-l", str(size), "-d", "-P", "-F", "#{pane_id}",
        "-c", cwd, "-t", target, local_pane_command(instance_pid),
    )
    return out.strip() if out and out.strip() else None


def ensure_local_panes(quiet: bool = True) -> list[str]:
    """Put back every local session's todo pane that is missing, in the right place.

    Deliberately not scoped to the watcher's own instance: sessions outnumber watchers
    here (one daemon, several `fb` runs in as many windows), and the watcher that holds
    the lock may not be following any local instance at all — a `-s nas` daemon follows
    none. So the pass asks the whole tmux server instead: which panes are running a
    freebuff, which of those already show a list, and what is left to open — and then
    whether the panes that ARE there still sit under the pane their session is drawn in.

    The panes it opens are named after the instance (`--watch-pid <pid>`), so the next
    pass recognises them and this stays a no-op: a pane that is there is never re-split,
    and one that is already placed is never moved. One drift of this shape is not about
    position at all — a pane on a different interpreter than the watcher's — and it is
    repaired by the same pass (`repair_drifted_panes`).
    """
    if pane_off():
        return []
    rows, table = pane_rows(), process_table()
    # The interpreter these panes have to agree with: the local watcher's, read from its
    # own process line. None when no local watcher is running (a `-s nas` daemon follows
    # nothing here), and then there is nothing to compare a pane against and no repair to
    # make — a pane is never respawned on a guess.
    # Acting path, so the PROBE is right: a pass opens and moves panes, and a free record
    # naming a dead pid is a leftover — `daemon_pid` removes it instead of handing back a
    # watcher that is not there (whose "interpreter" would then be compared against every
    # pane). A command that only looks uses `lock_peek` (see `daemon_pid`'s own note).
    watcher = daemon_pid()
    watcher_python = python_of((table.get(watcher) or (0, ""))[1]) if watcher else None
    wanted = session_windows(rows, table)
    missing = {inst: win for inst, win in wanted.items() if inst not in instances_with_pane(rows, table)}
    # No lock around this: the keeper is one process per tmux server (see
    # `ensure_pane_keeper`), so nothing else splits panes — and a lock nobody can release
    # after a kill would be a way for a pane to stay missing forever.
    opened = []
    cwds = cwds_for(list(missing))
    for inst, window in missing.items():
        pane = local_pane_open(cwds.get(inst) or os.getcwd(), inst, window, rows, table)
        if pane:
            opened.append(pane)
    # A pane that is already there is not necessarily in the right place, which is the
    # other half of the same promise: the wrapper's split can miss (see
    # `local_pane_open`), a hand-move is always possible, and a pane this build opened
    # before an upgrade is wherever the old one put it. Geometry is read AFTER the splits
    # above, which have just moved every pane in the windows they touched.
    rects = pane_rects()
    # Read once for the whole pass: one file read per window would be a syscall per session
    # for an answer that is a dictionary lookup (`pane_layout`).
    pins, last = load_pins(), load_last()
    moved, sized = [], []
    for inst, window in wanted.items():
        inst_pane = freebuff_pane_id(inst, rows=rows, table=table)
        if not inst_pane:
            continue
        # Per window AND per role, because that is where a pin lives: a wide window may
        # want the list beside its session and a tall one below it, and a window holding a
        # NAS ssh as well has two list panes that can want different sizes.
        layout = pane_layout(window, "local", pins, last)
        for pane in local_pane_ids(inst, rows, table):
            # The side this pane is held at: a pin's, or the one it is keeping (`kept_layout`),
            # which is also the one written down below — so a pane the owner moved to the
            # other axis of its session stays there and the next one opens there.
            here = kept_layout(pane, inst_pane, rects, layout)
            if place_pane_beside(pane, inst_pane, rects, here["side"]):
                moved.append(pane)
                rects = pane_rects()  # the layout just moved under us
            if hold_pane_size(pane, here["window"], "local", rects, here):
                sized.append((pane, here["size_source"]))
                rects = pane_rects()
            # Written down for next time: a pane killed and reopened — or a whole tmux
            # server that went down — comes back at the size it was left, the side it was
            # put on, instead of at the default.
            remember_layout(window, "local", pane, rects, here["side"], inst_pane)
    # The other drift: a pane that is there but on a different Python than the watcher's
    # (see `repair_drifted_panes`). Read from the same table the geometry pass used — a
    # respawn does not move a pane, so nothing above needs to run again.
    drifted = repair_drifted_panes(wanted, watcher_python, rows, table)
    forget_settled({row["pane"] for row in pane_rows()})
    for pane in moved:
        # Quiet or not, written down: a pane that came back is the one thing worth being
        # able to see afterwards, and a keeper has no stderr anybody reads.
        pane_log(f"moved {pane} beside its session's pane")
    for pane, source in sized:
        pane_log(f"resized {pane} to its {'pinned' if source.startswith('pin') else source} size")
    if not quiet:
        if opened:
            print(f"fbtodo pane(s) reopened: {', '.join(opened)}", file=sys.stderr)
        if moved:
            print(f"fbtodo pane(s) re-placed beside their session: {', '.join(moved)}", file=sys.stderr)
        if sized:
            print(
                "fbtodo pane(s) resized: "
                + ", ".join(f"{pane} ({source})" for pane, source in sized),
                file=sys.stderr,
            )
        if drifted:
            print(
                "fbtodo pane(s) reopened on the pinned interpreter: "
                + ", ".join(drifted),
                file=sys.stderr,
            )
    return opened


def notify_argv(argv: list) -> list:
    """A notify script as a command line: its own shebang, under THIS process's pin.

    In plain words: every bell in `scripts/notify/` is an `env python3` script, and which
    Python that means is decided by the PATH of whoever runs it. Inside a pane's tree that
    is the pin — but a watcher started by the shell autostart, or a keeper started from
    another context, carries its own PATH, and a bell launched from there can end up on a
    different interpreter than the pane it is reporting about. That is exactly the drift
    the keeper reads (`tree_drift`) — and a bell is gone by the time it is seen, so the
    only place it can be repaired is at the launch. Running it behind an explicit
    `PATH=<pin>` makes the shebang resolve in this process's pin wherever the launcher
    came from. The script is still exec'd AS ITSELF, never `python3 <script>`, so a
    replacement written in any other language keeps working: only PATH is fixed, and only
    for the script's own shebang to resolve in.
    """
    env = "/usr/bin/env" if os.path.exists("/usr/bin/env") else "env"
    return [env, f"PATH={pinned_path()}", *argv]


def nas_notify_once(args, quiet: bool = True) -> int | None:
    """Ask the phone notifier about the live NAS session: one pass, None when absent.

    Run through its shebang rather than `python3 <script>` (`notify_argv`) so a replacement
    written in anything else still works, under this process's pin so the shebang resolves
    to the same Python as the tree that rang it. Exit 78 means "no topic to send to" and
    the caller stops asking for a while; a session must not respawn a script that has
    nothing to send.
    """
    if not os.path.exists(NAS_NOTIFY):
        return None
    try:
        proc = subprocess.run(
            notify_argv([NAS_NOTIFY, "--nas-watch", "--once", "--quiet"]),
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if not quiet:
            print(f"nas notifier failed: {exc.__class__.__name__}", file=sys.stderr)
        return None
    if proc.returncode and not quiet:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        print(
            f"nas notifier exited {proc.returncode}: {(detail[-1] if detail else '')[:120]}",
            file=sys.stderr,
        )
    return proc.returncode


def ask_notify_once(args, quiet: bool = True) -> int | None:
    """Ask the question watch whether any freebuff pane is waiting on an answer.

    One pass, its own clock, and its own record of what it has already announced: this
    only has to know WHEN to ask. Run through its shebang rather than `python3 <script>`
    so a replacement written in anything else still works. Exit 78 means there is nothing
    configured to send to — the same signal the other notifiers use.
    """
    if not os.path.exists(ASK_NOTIFY):
        return None
    try:
        proc = subprocess.run(
            notify_argv([ASK_NOTIFY, "--quiet"]), capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if not quiet:
            print(f"ask watch failed: {exc.__class__.__name__}", file=sys.stderr)
        return None
    if proc.returncode and not quiet:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        print(
            f"ask watch exited {proc.returncode}: {(detail[-1] if detail else '')[:120]}",
            file=sys.stderr,
        )
    return proc.returncode


def pane_notify_once(args, quiet: bool = True) -> int | None:
    """Ask the pane watch whether the pane keeper is doing its job.

    No session and no pane are passed: the watch reads the whole tmux server through
    `fbtodo why --json`, because the failure it reports is often one this daemon cannot
    see from inside — a session in another window with no pane, or a keeper that is not
    running at all. It decides, it records, it pushes; this only owns WHEN to ask. Exit
    78 means there is nothing configured to send to, the same signal the other three use.
    """
    if not os.path.exists(PANE_NOTIFY):
        return None
    argv = notify_argv([PANE_NOTIFY, "--quiet", "--keeper", PANE_KEEPER_PATH])
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.SubprocessError) as exc:
        if not quiet:
            print(f"pane watch failed: {exc.__class__.__name__}", file=sys.stderr)
        return None
    if proc.returncode and not quiet:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        print(
            f"pane watch exited {proc.returncode}: {(detail[-1] if detail else '')[:120]}",
            file=sys.stderr,
        )
    return proc.returncode


def locks_notify_once(args, quiet: bool = True) -> int | None:
    """Ask the locks watch whether a claim has gone wrong: one pass, None when absent.

    The fifth notifier, and the only one whose subject is a CLAIM: a role process running
    with no name pointing at it, or a claim file whose name and inode have parted. It reads
    `fbtodo locks --json` for itself — the same rows the watch just printed — and keeps its
    own record of what it pushed, so `locks --watch` running for hours pushes once per
    occurrence. Exit 78 means there is nothing configured to send to, the same signal the
    other four use.
    """
    if not os.path.exists(LOCKS_NOTIFY):
        return None
    try:
        proc = subprocess.run(
            notify_argv([LOCKS_NOTIFY, "--quiet"]), capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if not quiet:
            print(f"locks watch failed: {exc.__class__.__name__}", file=sys.stderr)
        return None
    if proc.returncode and not quiet:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        print(
            f"locks watch exited {proc.returncode}: {(detail[-1] if detail else '')[:120]}",
            file=sys.stderr,
        )
    return proc.returncode


def pause_notify_once(args, instance_pid, quiet: bool = True) -> int | None:
    """Ask the stall watch whether this session has stopped mid-task.

    It is given the session and the pane it is drawn in, and decides the rest itself
    (its own record, its own sender). Exit 78 means there is nothing configured to send
    to, the same signal the other notifiers use.
    """
    if not os.path.exists(PAUSE_NOTIFY) or not instance_pid:
        return None
    pane = freebuff_pane_id(instance_pid)
    argv = [PAUSE_NOTIFY, "--watch-pid", str(instance_pid), "--quiet"]
    if pane:
        argv += ["--pane", pane]
    try:
        proc = subprocess.run(notify_argv(argv), capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        if not quiet:
            print(f"stall watch failed: {exc.__class__.__name__}", file=sys.stderr)
        return None
    if proc.returncode and not quiet:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        print(
            f"stall watch exited {proc.returncode}: {(detail[-1] if detail else '')[:120]}",
            file=sys.stderr,
        )
    return proc.returncode


def nas_drop_once(args, death: dict, quiet: bool = True) -> int | None:
    """Tell the drop watch that the NAS session stopped, and what the marker looked like.

    The decision (a stale marker is a death, an absent one is a clean exit), the
    "already reported" record and the push all belong to that script; fbtodo only knows
    WHEN to ask — the pass after a live session was seen to go. Exit 78 means there is
    nothing configured to send to, which the caller honours as it does for the finish
    notification; 10 means a drop was reported.
    """
    if not os.path.exists(DROP_NOTIFY):
        return None
    argv = [
        DROP_NOTIFY, "--nas", "--quiet",
        "--fb", str(death.get("fb") or "-"),
        "--live", "1" if death.get("live") else "0",
    ]
    if death.get("where"):
        argv += ["--cwd", str(death["where"])]
    try:
        proc = subprocess.run(notify_argv(argv), capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        if not quiet:
            print(f"nas drop watch failed: {exc.__class__.__name__}", file=sys.stderr)
        return None
    if proc.returncode not in (0, 10) and not quiet:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        print(
            f"nas drop watch exited {proc.returncode}: {(detail[-1] if detail else '')[:120]}",
            file=sys.stderr,
        )
    return proc.returncode


def live_watcher_pid(path: str) -> int | None:
    """The watcher claiming `path`, with a VERSION-STALE one replaced rather than adopted.

    A watcher keeps the code it started with, and it outlives the shell that spawned it,
    so after an upgrade the OLD one is still polling — with the old bugs. It is killed and
    its lock cleared instead of being honoured: it writes the old layout, and the pane
    (which checks the producer's version) then silently loses every field the new one
    added. That is how a pre-fix NAS watcher stayed "live" forever, and how an upgrade
    could leave the pane rendering yesterday's shape.
    """
    # The probe, not a peek, and it MUST be: this path replaces — it is what a start or a
    # stop does, so a free leftover is removed, and a watcher left on another build is
    # killed and its claim cleared below. A command that only LOOKS (`nas --status` is the
    # one that used to come through here) reads with `lock_peek` instead.
    pid = lock_holder(path)  # the claim, not the number: a leftover record is not a watcher
    if not pid:
        return None
    if (read_json(path, {}) or {}).get("version") != VERSION:
        try:
            os.kill(int(pid), signal.SIGTERM)
        except OSError:
            pass
        for _ in range(40):
            if not pid_alive(int(pid)):
                break
            time.sleep(0.05)
        clear_lock(path=path)  # free once it is gone; refused while it still holds it
        return None
    return int(pid)


def nas_pane_daemon_pid() -> int | None:
    return live_watcher_pid(NAS_LOCK_PATH)


def cmd_nas(args) -> int:
    """Watch the NAS for a session STARTING and open a pane for it (and close it after).

    Not tied to the ssh: the ssh only carries you over there, and a pane opened with it sat
    waiting for something the owner had not started yet. This watches the marker the remote
    host's own `freebuff` wrapper keeps, so the pane appears when the session does — however
    you got there — and goes when the session goes.
    """
    if args.stop:
        pid = nas_pane_daemon_pid()
        if not pid:
            print("fbtodo nas: not running", file=sys.stderr)
            return 0
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        for _ in range(40):
            if not pid_alive(pid):
                break
            time.sleep(0.05)
        try:
            os.unlink(NAS_LOCK_PATH)
        except OSError:
            pass
        if not args.quiet:
            print(f"fbtodo nas watcher stopped (pid {pid})", file=sys.stderr)
        return 0
    if args.status:
        # A status is a LOOK: `nas_pane_daemon_pid` is `lock_holder`, whose free-claim
        # answer removes a leftover record — and, for a watcher from another build, KILLS
        # it and clears the claim. Neither belongs to a command that was only asked what
        # is out there. `lock_peek` asks the same question with no unlink and no kill: a
        # held claim is a running watcher, and the record's pid is the answer. Stop and
        # start keep `live_watcher_pid`'s replacement — they are the paths that act.
        held, pid = lock_peek(NAS_LOCK_PATH)
        pid = pid if held else None
        state = read_json(NAS_STATE_PATH, {}) or {}
        panes = nas_pane_ids()
        # Which pane the session's ssh is in, i.e. what a list pane is placed against.
        anchor = (
            nas_ssh_pane(args.nas_host, _iso_ms(state.get("started")))
            if state.get("alive") else None
        )
        if args.json:
            json.dump(
                {"watcher_pid": pid, "panes": panes, "ssh_pane": anchor, "state": state},
                sys.stdout, ensure_ascii=False,
            )
            sys.stdout.write("\n")
            return 0
        print(f"fbtodo nas  {args.nas_host}")
        print(f"  watcher           : {pid or '— not running'}")
        print(
            "  nas session       : "
            + ("live" if state.get("alive") else "not running")
            + (f"  {state.get('dir')}" if state.get("dir") else "")
        )
        print(f"  pane              : {', '.join(panes) if panes else '— none'}")
        if state.get("alive"):
            print(
                "  ssh pane          : "
                + (
                    f"{anchor}  (the pane this session runs in)"
                    if anchor
                    else "— not identified; the pane goes to the active window"
                )
            )
        if not os.path.exists(NAS_NOTIFY):
            print("  phone notifier    : none installed (~/.config/freebuff-notify/todo-bell.py)")
        elif args.notify_seconds > 0:
            print(f"  phone notifier    : every {args.notify_seconds:.0f}s while a session runs")
        else:
            print("  phone notifier    : off (--notify-seconds 0)")
        if not os.path.exists(ASK_NOTIFY):
            print("  ask watch         : none installed (~/.config/freebuff-notify/ask-bell.py)")
        elif args.ask_seconds > 0:
            print(f"  ask watch         : on, every {args.ask_seconds:.0f}s while a session runs")
        else:
            print("  ask watch         : off (--ask-seconds 0)")
        if not os.path.exists(DROP_NOTIFY):
            print("  drop watch        : none installed (~/.config/freebuff-notify/drop-bell.py)")
        else:
            print(
                "  drop watch        : on, when a session stops"
                + ("  (this one is being confirmed)" if state.get("drop_pending") else "")
            )
        hb = state.get("heartbeat_ms")
        if hb:
            age = int(time.time() - hb / 1000)
            print(f"  last poll         : {age}s ago · {state.get('polls', 0)} polls")
            if pid and age > 90:
                print("                      (a stale heartbeat: the watcher is wedged, `fbtodo nas --stop` first)")
        elif pid:
            print("  last poll         : — none yet")
        if state.get("error"):
            print(f"  last error        : {state['error']}")
        return 0
    if args.once or args.foreground or args.dry_run:
        return nas_watch_loop(args)
    running = nas_pane_daemon_pid()
    if running:
        if not args.quiet:
            print(f"fbtodo nas watcher already running (pid {running})", file=sys.stderr)
        return 0
    return 0 if spawn_nas_daemon(args, quiet=args.quiet) else EX_CODES["tempfail"]


def spawn_nas_daemon(args, quiet: bool = True) -> int | None:
    argv = [
        *self_argv(), "nas", "-f", "--quiet",
        "--nas-host", args.nas_host, "--nas-root", args.nas_root,
        "--nas-project", args.nas_project, "--fb-marker", args.fb_marker,
        "--poll", str(args.poll), "--poll-live", str(args.poll_live),
        "--poll-idle", str(args.poll_idle),
        "--notify-seconds", str(args.notify_seconds),
        "--ask-seconds", str(args.ask_seconds),
    ]
    log = open(NAS_LOG_PATH, "ab", buffering=0)
    try:
        subprocess.Popen(
            argv, stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True
        )
    finally:
        log.close()
    for _ in range(60):
        time.sleep(0.05)
        if nas_pane_daemon_pid():
            break
    pid = nas_pane_daemon_pid()
    if not quiet and not pid:
        print("fbtodo nas watcher failed to start; see " + NAS_LOG_PATH, file=sys.stderr)
    return pid


def nas_watch_loop(args) -> int:
    """One pass per poll: a live session and no pane means open one; a dead session and a
    pane we are responsible for means close it. `--once` runs exactly one of these."""
    dry = args.dry_run
    if not dry:
        if nas_pane_daemon_pid() and not (args.once or args.force):
            if not args.quiet:
                print(f"fbtodo nas watcher already running (pid {nas_pane_daemon_pid()})", file=sys.stderr)
            return 0
        if not claim_or_force(NAS_LOCK_PATH, os.path.realpath(os.getcwd()), None,
                              bool(getattr(args, "force", False))):
            if not args.quiet:
                print(f"fbtodo nas watcher already running (pid {nas_pane_daemon_pid() or '—'})",
                      file=sys.stderr)
            return 0

    stop_reason, code = "stopped", 0
    owned: list[str] = []
    last_busy = time.monotonic()
    # The drop watch's state: whether the session was live on the previous pass, and the
    # death witness kept for the confirming pass (see the loop).
    prev_alive = False
    pending_death: dict | None = None
    # The phone notifier's schedule: asked while a session is live, and switched off for
    # half an hour once it says there is nothing configured to send to.
    notify_due = 0.0
    notify_off_until = 0.0
    # The ask watch's own clock: it reads the panes, not this watcher's store, so it is
    # asked on its own schedule rather than as a side effect of the render.
    ask_due = 0.0

    def shutdown(signum=None, _frame=None):
        nonlocal stop_reason, code
        stop_reason = f"signal-{signum}" if signum else "stopped"
        code = 128 + signum if signum else 0
        raise SystemExit(code)

    if not args.once and not dry:
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, shutdown)

    try:
        while True:
            live = nas_liveness(args)
            panes = nas_pane_ids()
            if dry:
                # The ssh pane is named here too: it is what a pane about to be opened
                # would be split off, and the one thing to look at when a pane lands
                # somewhere unexpected.
                print(
                    f"nas={'live' if live.get('alive') else 'idle'} "
                    f"fb={live.get('fb')} dir={live.get('dir') or '-'} "
                    f"ssh={nas_ssh_pane(args.nas_host, _iso_ms(live.get('started'))) or '-'} "
                    f"panes={panes or '-'} "
                    f"action={'open' if live.get('alive') and not panes else 'close' if not live.get('alive') and owned else 'none'}"
                )
                return 0
            alive = bool(live.get("alive"))
            # A session that STOPS is reported once, and only after one more pass agrees:
            # a clean exit removes the marker for a heartbeat too, so the first pass that
            # sees nothing could be a session on its way out in an orderly way. The
            # witness (what the marker looked like as it went) is kept, not re-read.
            if alive:
                pending_death = None
            elif prev_alive:
                pending_death = {
                    "fb": live.get("fb"),
                    "live": bool(live.get("live")),
                    "where": live.get("dir") or "",
                }
            elif pending_death is not None:
                nas_drop_once(args, pending_death, quiet=args.quiet)
                pending_death = None
            prev_alive = alive
            if live.get("alive"):
                # Where the session is drawn — its ssh — so the list can sit beside it.
                # Resolved before the pane exists (that is what it is for) and applied
                # again below, while it does.
                anchor = nas_ssh_pane(args.nas_host, _iso_ms(live.get("started")))
                if panes:
                    owned = panes  # a pane exists (ours from a previous run, or a hand-opened
                    #                 one): adopt it rather than stacking a second
                    nas_place_panes(panes, anchor, quiet=args.quiet)
                else:
                    pane = nas_pane_open(args, anchor)
                    if pane:
                        owned = [pane]
                        if not args.quiet:
                            print(f"opened NAS todo pane {pane}", file=sys.stderr)
                # The phone push matters most when nobody is looking at that pane, so it
                # is its own pass, on its own clock, not a side effect of the render.
                if args.notify_seconds > 0 and time.monotonic() >= max(notify_due, notify_off_until):
                    notify_due = time.monotonic() + args.notify_seconds
                    rc = nas_notify_once(args, quiet=args.quiet)
                    if rc == EX_CODES["config"]:
                        notify_off_until = time.monotonic() + 1800
                if args.ask_seconds > 0 and time.monotonic() >= ask_due:
                    ask_due = time.monotonic() + args.ask_seconds
                    ask_notify_once(args, quiet=args.quiet)
            elif owned:
                # close only what this watcher is responsible for; a pane the owner opened
                # by hand while idle is theirs to keep
                for pane in owned:
                    if pane in panes:
                        nas_pane_kill(pane)
                owned = []
            atomic_write_json(
                NAS_STATE_PATH,
                {
                    "alive": bool(live.get("alive")), "fb": live.get("fb"),
                    "dir": live.get("dir"), "started": live.get("started"),
                    "panes": owned,
                    "drop_pending": bool(pending_death),
                    "error": live.get("error"), "heartbeat_ms": int(time.time() * 1000),
                    "watcher_pid": os.getpid(), "polls": (read_json(NAS_STATE_PATH, {}) or {}).get("polls", 0) + 1,
                },
            )
            if args.once:
                return 0
            clients = tmux_run("list-clients", "-F", "#{client_name}") or ""
            idle = not clients.strip()
            # A watcher spawns itself from a remote shell and then outlives the ssh, so it
            # has to be able to leave: with nobody looking and no session to watch it is
            # only a once-a-minute ssh, and the next remote shell starts it again.
            if live.get("alive") or not idle or owned:
                last_busy = time.monotonic()
            elif args.idle_exit > 0 and time.monotonic() - last_busy > args.idle_exit * 60:
                stop_reason = f"idle {args.idle_exit:.0f}m"
                break
            time.sleep(
                max(0.2, args.poll_live if live.get("alive") else args.poll_idle if idle else args.poll)
            )
    except SystemExit:
        pass
    except Exception as exc:
        stop_reason, code = f"error: {exc.__class__.__name__}", EX_CODES["tempfail"]
    finally:
        if not dry and not args.once:
            final = read_json(NAS_STATE_PATH, {}) or {}
            final.update(status="stopped", stop_reason=stop_reason, panes=[], watcher_pid=None)
            try:
                atomic_write_json(NAS_STATE_PATH, final)
            except OSError:
                pass
            clear_lock(os.getpid(), path=NAS_LOCK_PATH)
    if not args.quiet and stop_reason:
        print(f"fbtodo nas watcher stopped ({stop_reason})", file=sys.stderr)
    return code


__all__ = [
    "tmux_run", "tmux_split_target", "SSH_SESSION_CMDS", "SSH_NON_SESSION_FLAGS",
    "NAS_CLOCK_SKEW_MS", "is_ssh_cmd", "nas_host_tokens", "ssh_session_candidates",
    "pick_ssh_pane", "nas_ssh_pane", "nas_pane_ids", "nas_place_panes", "nas_pane_open",
    "nas_pane_command", "nas_pane_kill", "PANE_INSTANCE_RE", "PANE_WATCH_RE",    "pane_off", "append_log", "pane_log", "pane_repaint", "pane_rows", "local_pane_ids",
    "freebuff_pane_id",
    "session_windows", "instances_with_pane", "pane_rects", "pane_axis", "placed_beside",
    "pane_before", "place_pane_beside", "kept_layout",
    "load_pins", "window_key", "pin_for_window", "load_last", "save_last",
    "pin_value", "pane_layout", "source_note", "_SETTLED", "_LAST_SEEN", "resize_pane_to",
    "hold_pane_size", "forget_settled", "remember_layout", "save_pins", "window_of",
    "KEEPER_GRACE", "KEEPER_CLAIM_CHECK_S", "keeper_reclaim",
    "PANE_NOTE_S", "pane_note_write", "pane_note_forget", "pane_note_take",
    "tmux_identity", "tmux_identity_outside", "tmux_socket_of", "same_tmux_server",
    "keeper_serves",
    "ensure_pane_keeper", "cmd_pane_watch",
    "pane_command", "pane_python", "pane_start_command", "DRIFT_RETRY_S",
    "pane_drifted", "repair_is_off", "pane_repair_value", "set_pane_repair", "pane_repair_off",
    "window_repair_value", "set_window_repair",
    "tree_pythons", "tree_drift", "repair_drifted_panes",
    "_DRIFT_TRIED", "_DRIFT_HELD", "_DRIFT_KEPT",
    "local_pane_command", "local_pane_open",
    "ensure_local_panes", "notify_argv", "nas_notify_once",
    "ask_notify_once", "pane_notify_once", "locks_notify_once", "pause_notify_once",
    "nas_drop_once",
    "live_watcher_pid", "nas_pane_daemon_pid", "cmd_nas", "spawn_nas_daemon", "nas_watch_loop",
]
