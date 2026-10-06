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

# ==================================================== panes and their keeper
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

    Its own handle, opened and closed per line: a daemon's log is also that process's
    stdout, held open in append mode by whoever spawned it, and a second long-lived
    handle could interleave with it.
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
        # `--no-daemon` is a pane that refuses to poll — not this instance's todo list
        if "fbtodo" not in start or "--no-daemon" in start:
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
        if "fbtodo" not in start or "--no-daemon" in start:
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

    The anchor is the pane the session is drawn in. `move-pane` moves the pane itself, so its process, its
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
# `session:index`. A pin is the deliberate
# version of the same idea and outranks it; this file is written by the keeper, the pins
# file only by `fbtodo pin`, so the two never race.
def load_last() -> dict:
    """The remembered-layout file: one read, nothing trusted beyond "it is an object"."""
    last = read_json(LAST_PATH, {}) or {}
    return last if isinstance(last, dict) else {}


def save_last(last: dict) -> None:
    atomic_write_json(LAST_PATH, last)


def pin_value(window: str | None, field: str, pins: dict | None = None):
    """One half of a window's pin: `(value, source)`, or `(None, "")` if unset.

    A pin is the window's answer, flat: `{"side": …, "size": …}`. A pin file written
    while there were two list panes per window may carry its answer under a `local` key
    instead, and that half is read as the window's own — the role it was filed for is the
    only role there is now, so nothing is lost and the file keeps working. The source names
    which of the two answered, so `fbtodo why` can say it out loud.
    """
    entry = pin_for_window(window, pins)
    entry = entry if isinstance(entry, dict) else {}
    scoped = entry.get("local")
    if isinstance(scoped, dict) and field in scoped:
        value = scoped[field]
        if (field == "side" and value in ("v", "h")) or (
            field == "size" and isinstance(value, int) and value > 0
        ):
            return value, "pin:local"
    value = entry.get(field)
    if field == "side":
        return (value, "pin") if value in ("v", "h") else (None, "")
    return (value, "pin") if isinstance(value, int) and value > 0 else (None, "")


def pane_layout(window: str | None, pins: dict | None = None,
                last: dict | None = None) -> dict:
    """Side and size in force for a window's list pane, and where each came from.

    Most specific first: the window's explicit pin (`fbtodo pin`), then how this pane was
    left last time,
    then FBTODO_SPLIT/FBTODO_PANE_SIZE, then the built-in default. The source travels with
    the number because `fbtodo why` prints it: a pane that opens somewhere you did not
    choose can say which of the four did it. `pins`/`last` are passed in by callers that
    ask this for several panes in one pass, so the files are read once.
    """
    key = window_key(window) or ""
    seen = (load_last() if last is None else last).get(key)
    seen = seen if isinstance(seen, dict) else {}
    # A layout file written while there were two list panes per window holds this pane's
    # own numbers under `local`; that half is read as the window's, so an existing file
    # keeps working. `remember_layout` writes the flat shape and drops the old key, so the
    # two can never disagree after one pass.
    scoped = seen.get("local")
    seen = scoped if isinstance(scoped, dict) else seen
    side, side_source = pin_value(window, "side", pins)
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
    size, size_source = pin_value(window, "size", pins)
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
        "window": key,
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
    """A layout source as a person reads it — `pin (main:1 local)` rather than `pin:local`."""
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


def hold_pane_size(pane: str, window: str, rects: dict, layout: dict) -> bool:
    """Hold a list pane at the size in force for it; True if it had to be resized.

    Three different promises in one place. A PIN is enforced on every pass — that is what a
    pin is for, and a hand-drag has to lose to it. The REMEMBERED size is applied once, to
    a pane that has just appeared: it is where the pane was left, not a standing
    instruction, and re-applying it every poll would make resizing by hand impossible —
    which is also why the pane is then forgotten, so the next one is welcomed the same way.
    The environment and the built-in default decide only how a NEW pane is split — they are
    what the opener reads (`pane_layout`) — never the size of one already on screen.
    """
    marker = f"{window}\u001f{pane}"
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


def remember_layout(window: str | None, pane: str, rects: dict, side: str,
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
    keeper polls every few seconds), and the gate is its own memory, per window.
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
    marker = key
    if _LAST_SEEN.get(marker) != (side, size, before):
        _LAST_SEEN[marker] = (side, size, before)
        return False
    last = load_last()
    entry = dict(last.get(key) or {})
    entry.pop("local", None)      # the flat shape is this pane's own now (see `pane_layout`)
    if entry == {"side": side, "size": size, "before": before}:
        return False
    entry.update({"side": side, "size": size, "before": before})
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
    is exactly what a second daemon can be holding while your own window's pane is
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
    """A pane's command line: an absolute interpreter, under the opener's own environment.

    In plain words: the pane gets the environment its opener meant, not the one the tmux server
    hands it. `self_argv()` already names the interpreter and the launcher absolutely; the rest
    of the pin is `pinned_env()`, and the two halves of it are the same argument twice over.
    The PATH decides what the pane's own children resolve — `#!/usr/bin/env python3` in
    `scripts/notify/`, `tmux`, and the watcher the pane starts. The other carried
    values decide WHERE it works: the state root (`FBTODO_HOME` / `XDG_STATE_HOME`), the tmux
    server it drives (`FBTODO_TMUX`) and the session marker it counts live sessions by
    (`FBTODO_FB_MARKER`). A pane inherits the SERVER's environment, so any of those missing
    there quietly moves the pane and its watcher to another store or another set of sessions.

    `env` carries the assignments rather than a bare `VAR=value command` prefix, because the
    pane command is run by the owner's login shell and fish has no such prefix form. They are
    written into the command string on purpose: it rides along in `pane_start_command`, which
    is what `fbtodo status`, `fbtodo why` and the keeper read back — so a pane respawned by
    hand (`tmux respawn-pane`) gets the same pin back with no help.
    """
    # `/usr/bin/env` where it exists, the plain name elsewhere: this runs before the pane's own
    # PATH can have been fixed, so resolving `env` by name is the one lookup that must not
    # matter. On every system this runs on it is there.
    env = "/usr/bin/env" if os.path.exists("/usr/bin/env") else "env"
    return " ".join(
        shlex.quote(part) for part in [env, f"PATH={pinned_path()}", *pinned_env(), *argv]
    )


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

# The same two memories for the OTHER repair (`upgrade_stale_panes`, below): panes whose
# recorded command predates the pin this build writes. Kept apart from the drift repair's on
# purpose — the two act for different reasons on different panes, and one must not spend the
# other's retry budget.
_STALE_TRIED: dict[str, float] = {}
_STALE_HELD: set[str] = set()

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


# ------------------------------------- what a pane holds, said where a reader can find it
# A pane's subject is decided by `latch_pane_subject` and lives in its arguments and its own
# environment. Neither answers a reader outside the process: the arguments are memory, and the
# kernel snapshots an environment at exec, so a value written at runtime — which is exactly
# what the latch writes — is invisible to `ps` and to everything else that only looks. So the
# pane also WRITES IT DOWN, one small file per process, and `fbtodo panes` pairs those records
# with the pane processes the process table already names.

def pane_subject_write(subject: str, root: str | None = None) -> None:
    """Write what this pane holds, and sweep the records of panes that have ended.

    In plain words: `pane-subjects/<pid>.json` is the pane's note to anybody reading from
    outside, written when it latches and again by a reloaded image (whose pid is new). Its OWN
    file, not a shared registry: panes start in bursts — a machine-wide restart, a tmux server
    coming up — and a read-modify-write merge would drop an entry to a race that a per-pid file
    cannot have. The sweep is what keeps the directory to the panes actually running: a record
    whose pid is gone belongs to a pane that has ended, and it goes the next time any pane
    writes. `fbtodo panes` itself never cleans up — a look must change nothing.
    """
    if not subject:
        return
    where = pane_subject_dir(root)
    try:
        os.makedirs(where, mode=0o700, exist_ok=True)
        atomic_write_json(
            os.path.join(where, f"{os.getpid()}.json"),
            {"subject": subject, "at_ms": int(time.time() * 1000)},
        )
    except OSError:
        return
    try:
        names = os.listdir(where)
    except OSError:
        return
    for name in names:
        stem, _, suffix = name.partition(".")
        if suffix != "json" or not stem.isdigit():
            continue
        if int(stem) != os.getpid() and not pid_alive(int(stem)):
            try:
                os.unlink(os.path.join(where, name))
            except OSError:
                pass


def pane_subject_dir(root: str | None = None) -> str:
    """Where a state root keeps its panes' subject records — `<root>/pane-subjects`."""
    return PANE_SUBJECT_DIR if not root else os.path.join(root, "pane-subjects")


def pane_process_root(pid: int, blob: str | None = None) -> str:
    """The state root a pane process works in, from its OWN environment.

    In plain words: the record a pane leaves is written under the root the pane works in, and
    different roots mean different panes — one pointed at another `FBTODO_HOME` keeps its
    subject somewhere else. The root is the one thing the environment answers from outside,
    because the pin writes it into the command line and so it is there at exec; `_proc_root`
    reads it the way the program does. An environment that cannot be read at all falls back to
    this run's root, the safe direction: a pane sharing it is still found.
    """
    if blob is None:
        try:
            blob, _clipped = _proc_environ(int(pid))
        except (OSError, TypeError, ValueError):
            blob = ""
    if not blob:
        return SCRATCH
    return _proc_root(blob)


def pane_subject_read(pid: int, root: str | None = None) -> str:
    """What the pane process `pid` recorded as its subject, or "" — its own root first.

    `root` is that pane's own answer when the caller could read it (`pane_process_root`); this
    process's root is tried as well, because an environment that could not be read must not
    turn a pane sharing this root into one that holds nothing.
    """
    where = [pane_subject_dir(root)] if root else []
    if PANE_SUBJECT_DIR not in where:
        where.append(PANE_SUBJECT_DIR)
    for folder in where:
        rec = read_json(os.path.join(folder, f"{int(pid)}.json"), {})
        if isinstance(rec, dict) and rec.get("subject"):
            return str(rec["subject"])
    return ""


def subject_note(subject: str) -> str:
    """A recorded subject as a person reads it: `cli 2026-10-05T17-52-39.193Z`, or `—`.

    The raw record is the honest thing to keep (`cli:<full chat dir>`), and the name a reader
    says out loud is the short one: the chat directory's own name, or the first eight
    characters of a desktop thread's id — the same shortening the latch's log line uses.
    """
    kind, _, what = (subject or "").partition(":")
    if not what:
        return "—"
    if kind == "cli":
        return f"cli {os.path.basename(what.rstrip('/')) or what}"
    return f"{kind or 'subject'} {what[:8]}"


def desktop_app_pane(environ=None) -> bool:
    """Is this process a pane the Freebuff DESKTOP app opened?

    In plain words: the app spawns one terminal per thread and the pane runs bare `fbtodo`
    inside it, so the pane's own environment is the only thing that says where it lives —
    and the app marks every process it starts with `FREEBUFF_DESKTOP_STATE_PATH` (its own
    workspace-state file) and the bundle id. A pane typed in a terminal has neither, which
    is exactly the distinction this is for: inside the app a pane's subject belongs to a
    desktop thread, and a pane in a plain shell keeps asking `auto` the way it always did.
    """
    env = os.environ if environ is None else environ
    # ...and a pane inside tmux is never one of the app's: the app's terminals are plain ptys
    # (its own `~/.zshrc` excludes it from tmux), while a tmux SERVER started from one would
    # hand the marker to every pane it spawns — a Terminal.app pane measured with
    # `__CFBundleIdentifier=com.apple.Terminal`, and the guard is what keeps that answer true
    # even if the server's environment ever carries the app's.
    if env.get("TMUX"):
        return False
    return bool(
        env.get("FREEBUFF_DESKTOP_STATE_PATH")
        or env.get("__CFBundleIdentifier") == "com.freebuff.desktop"
    )


def held_pane_subjects(root: str | None = None) -> set[str]:
    """The subjects LIVE panes already hold, read from their own records.

    The records are per-pid files (`pane_subject_write`), and a record whose pid is gone is a
    pane that has ended: this reads only the living ones. It is what lets two panes of the
    same project share out the app's live threads instead of both landing on the one thread
    the app is working in — see `pane_place_subject`.
    """
    folder = pane_subject_dir(root)
    try:
        names = os.listdir(folder)
    except OSError:
        return set()
    held: set[str] = set()
    for name in names:
        stem, _, suffix = name.partition(".")
        if suffix != "json" or not stem.isdigit() or not pid_alive(int(stem)):
            continue
        rec = read_json(os.path.join(folder, name), {})
        if isinstance(rec, dict) and rec.get("subject"):
            held.add(str(rec["subject"]))
    return held


def is_todo_pane_command(cmd: str) -> bool:
    """Whether a command line is a todo pane — a `fbtodo` that asked for no other command.

    In plain words: a pane is a `fbtodo` invocation that named no subcommand (`pane` is the
    default) or said `pane` outright — the wrapper's `--watch-pid …` form, a pinned
    `respawn-pane`, and the desktop app's bare `fbtodo` are all that shape. The launcher has to
    be the PROGRAM token, the one right after the interpreter and past the `/usr/bin/env` and
    `KEY=value` prefix a pin carries: `grep fbtodo`, `rg fbtodo` and a script that merely
    mentions the name have the word in them too, and none of them is a pane. A leading flag IS
    the default subcommand (`--watch-pid`, `--once`, `-s cli`); `-h` and `-V` are the two that
    leave without ever drawing a list.
    """
    toks = shlex.split(cmd)
    at = 0
    while at < len(toks) and (toks[at].endswith("env") or "=" in toks[at]):
        at += 1
    if at >= len(toks):
        return False
    if os.path.basename(toks[at]).lower().startswith("python"):
        at += 1
    if at >= len(toks):
        return False
    if os.path.basename(toks[at]) != "fbtodo" and not toks[at].endswith(
            os.path.join("fbtodo", "__init__.py")):
        return False
    sub = toks[at + 1] if at + 1 < len(toks) else None
    if sub is None or sub == "pane":
        return True
    return sub.startswith("-") and sub not in ("-h", "--help", "-V", "--version")


def todo_panes(rows=None, table=None, environs=None, ttys=None) -> list[dict]:
    """Every todo pane on this machine: where it is, what it holds, and what runs it.

    In plain words: a pane is a PROCESS — the one fbtodo a window is showing — so this walks
    the process table rather than tmux, and THAT is what makes it every pane on the machine:
    the two the desktop app attaches to a pty are panes as much as a tmux one, and a
    `list-panes` reader would never see them. A tmux pane's process is tied back to its pane id
    and window by ancestry, so the panes that ARE in tmux are named the way tmux names them.
    Each row is one pane: its pid, its tmux pane and window (`None` outside tmux), its
    controlling terminal, the subject it recorded (`pane_subject_read`), and the interpreter
    its command line names (`python_of`).

    The injection points are for the self-check, exactly like `claim_processes`: `rows` and
    `table` (tmux and ps), `environs` (pid -> environment blob, so the root a pane works under
    can be pinned), and `ttys`. Read-only by construction: no record is written and nothing is
    pruned, so a look never changes what it reports.
    """
    table = process_table() if table is None else table
    rows = pane_rows() if rows is None else rows
    ttys = process_ttys() if ttys is None else ttys
    # pid -> (pane, window) for every tmux pane, by ancestry: a split pane's process is the
    # login shell that runs the pinned command, a respawned pane's IS the command.
    where: dict[int, tuple] = {}
    for row in rows:
        if not row["pid"]:
            continue
        where.setdefault(row["pid"], (row["pane"], row["window"]))
        for pid, _depth in descendant_pids(table, row["pid"]):
            where.setdefault(pid, (row["pane"], row["window"]))
    found = []
    for pid, (_ppid, cmd) in sorted(table.items()):
        if not is_todo_pane_command(cmd):
            continue
        pane, window = where.get(pid, (None, None))
        blob = (environs or {}).get(pid)
        found.append({
            "pid": pid,
            "pane": pane,
            "window": window,
            "tty": ttys.get(pid),
            "subject": pane_subject_read(pid, pane_process_root(pid, blob)),
            "python": python_of(cmd),
        })
    return found


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
    # What this window is set to: its pin, then
    # what the pane was left at last time, then the two global knobs (`pane_layout`).
    layout = pane_layout(window)
    split, size = layout["side"], str(layout["size"])
    # The edge the owner last kept it on (`before`), because the splitter's own answer is
    # always the trailing one: without this a list kept in the left column comes back on
    # the right, and one kept above the session comes back under it.
    before = ["-b"] if layout.get("before") else []
    # Split the pane the session is DRAWN in, not merely its window: given a window, tmux
    # picks that window's *active* pane, which is not necessarily the one running freebuff,
    # and the list then opens beside somebody else's pane — measured live, where a plain
    # shell and a session pane sharing a window was enough to land a fresh pane in the
    # wrong column. The window stands in only when the instance's own pane is not found.
    target = freebuff_pane_id(instance_pid, rows=rows, table=table) or window
    out = tmux_run(
        "split-window", *before, f"-{split}", "-l", str(size), "-d", "-P", "-F", "#{pane_id}",
        "-c", cwd, "-t", target, local_pane_command(instance_pid),
    )
    return out.strip() if out and out.strip() else None


# The boundary between one assignment and the next in a process's environment as the kernel
# and `ps` print it: whitespace before a `NAME=`. The name part is what makes it a boundary —
# a value may itself hold spaces (`FBTODO_TMUX=tmux -L s`), so splitting on whitespace alone
# would truncate it, and a truncated value compares unequal to the one the pane carries.
_ENV_ASSIGN = re.compile(r"(?:^|[\s\x00])([A-Za-z_][A-Za-z0-9_]*)=")


def pinned_env_from(blob: str) -> list[str]:
    """The `pinned_env` values a process's environment blob carries, in `PINNED_ENV_KEYS` order.

    In plain words: an environment is `KEY=value` pairs joined by whitespace (and, from
    `/proc`, NULs turned to spaces) — but a value can itself contain spaces, so the only
    honest split is at the whitespace before the NEXT `NAME=`. That is what this reads:
    everything from a known key's `=` up to the next assignment, which keeps
    `FBTODO_TMUX=tmux -L s` whole rather than truncating it at the first space. Only the keys
    this build hands on are kept (`PINNED_ENV_KEYS`), and the first reading of a key wins.
    """
    text = (blob or "").replace("\x00", " ")
    marks = list(_ENV_ASSIGN.finditer(text))
    found: dict[str, str] = {}
    for idx, mark in enumerate(marks):
        name = mark.group(1)
        if name not in PINNED_ENV_KEYS or name in found:
            continue
        end = marks[idx + 1].start() if idx + 1 < len(marks) else len(text)
        value = text[mark.end():end].strip()
        if value:
            found[name] = value
    return [f"{key}={found[key]}" for key in PINNED_ENV_KEYS if found.get(key)]


def session_env_pids(inst, rows=None, table=None) -> list[int]:
    """The processes whose environment IS the session's — the keeper first, then the pane's shell.

    In plain words: the pin a pane carries was written by the process that opened it, and the
    one that would rewrite it (`upgrade_stale_panes`) is the KEEPER. So the keeper's own
    environment is the authority a diagnostic must agree with, and it is read first. A server
    with no live keeper (or none that can be read) falls back to the shell the session is
    drawn in — the pane `freebuff_pane_id` finds, whose environment is the session's own for
    the same reason the keeper's is. `inst` may be None when only the keeper's answer matters.
    """
    pids: list[int] = []
    rec = read_json(PANE_KEEPER_PATH, {}) or {}
    try:
        keeper = int(rec.get("pid") or 0)
    except (TypeError, ValueError):
        keeper = 0
    if keeper and pid_alive(keeper):
        pids.append(keeper)
    rows = pane_rows() if rows is None else rows
    table = process_table() if table is None else table
    pane = freebuff_pane_id(inst, rows=rows, table=table) if inst else None
    if pane:
        for row in rows:
            if row["pane"] == pane and row["pid"]:
                pids.append(row["pid"])
                break
    return pids


def session_pinned_env(inst=None, rows=None, table=None, blob=None) -> list[str]:
    """The pin as the SESSION answers it — what a NON-keeper reader (`status`, `why`) compares.

    In plain words: `status` and `why` run in whatever shell the owner typed them in, but the
    pin a pane carries belongs to the SESSION, and the keeper that repairs stale panes
    compares against its own (the session's) environment. A diagnostic that reads the CLI's
    OWN environment calls a current pane stale the moment the two shells differ — a different
    `FBTODO_HOME`, a `FBTODO_TMUX` this shell never set, a bell path only in the session's
    profile — and names a pane the keeper will never touch. So the keeper's process (or, with
    no live keeper, the session pane's shell) is asked instead (`session_env_pids` /
    `_proc_environ`), and `status`/`why` agree with the keeper however they were launched.
    `blob` injects an environment for a test; with nothing readable, this process's own
    `pinned_env` is the answer — what every caller used before, and the safe direction.
    """
    if blob is not None:
        carried = pinned_env_from(blob)
        return carried or pinned_env()
    for pid in session_env_pids(inst, rows, table):
        try:
            env_blob, _clipped = _proc_environ(int(pid))
        except (OSError, TypeError, ValueError):
            continue
        carried = pinned_env_from(env_blob)
        if carried:
            return carried
    return pinned_env()


def stale_pin(start: str, wanted=None) -> bool:
    """Is this recorded pane command a pin from BEFORE the values this build carries?

    In plain words: the pin is not only the interpreter and the PATH — it is also every value
    the program reads from its OWN environment to choose where it works and where its bells
    go (`pinned_env`). Those were added to the pin one at a time, so a pane opened by an
    earlier build runs with an earlier answer, and its RECORDED command is what would bring
    that old answer back if it were ever re-run. What is compared here is exactly those
    values: every one this build carries must be present, with THIS value, as a `KEY=value`
    token in the command.

    Three things are deliberately NOT compared. The interpreter, the PATH and the arguments
    after them: the `fb` launcher opens a pane that names the same values and a PATH built
    in the owner's own shell (the interpreter's own directory is fronted by `pinned_path`,
    which the wrapper's PATH does not do), so comparing those would call a perfectly current
    pane stale and respawn it for nothing. And a command that names none of these values is
    not stale when this build carries none either — with no values to hand on, the server's
    environment IS the answer.

    A pane that is not ours (the launcher `self_argv` names is not in its line) is never
    stale here: another checkout's pane, an ssh, a plain shell. The caller has already
    narrowed to the panes showing this instance's list (`local_pane_ids`).

    `wanted` is the set of values to compare against, `pinned_env` by default — the keeper's
    own answer, which is the session's. A reader that is NOT the keeper (`status`, `why`)
    passes the session's values read from the instance process (`session_pinned_env`), so it
    judges a pane by the same pin the keeper will, whatever shell it was typed in.
    """
    if not start:
        return False
    wanted = set(pinned_env()) if wanted is None else set(wanted)
    if not wanted:
        return False
    tokens = shlex.split(start)
    if not tokens or self_argv()[1] not in tokens:
        return False
    # A token counts as carried when its name is one this build hands on (`XDG_STATE_HOME`
    # rides with the `FBTODO_*` keys, so the prefix is read off `wanted` rather than assumed).
    names = {want.partition("=")[0] for want in wanted}
    carried = {tok for tok in tokens if tok.partition("=")[0] in names}
    return not wanted <= carried


def stale_pane_ids(inst, rows=None, table=None, wanted=None) -> list[str]:
    """This instance's panes whose RECORDED command predates the pin this build writes.

    The keeper's `upgrade_stale_panes` acts on exactly these; this only NAMES them, so
    `status` can say what the keeper is about to change (or is holding, when the pane is
    marked `@fbtodo_repair off`) instead of a pane quietly becoming something else. Read
    from the same rows the keeper reads through the same predicate (`stale_pin`), and — so
    the two cannot disagree — against the SESSION's values (`session_pinned_env(inst)`) that
    the keeper itself carries, not this reader's. `wanted` may inject them for a test.
    """
    rows = pane_rows() if rows is None else rows
    table = process_table() if table is None else table
    wanted = session_pinned_env(inst, rows, table) if wanted is None else list(wanted)
    starts = {row["pane"]: row["start"] for row in rows}
    return [pane for pane in local_pane_ids(inst, rows, table)
            if stale_pin(starts.get(pane, ""), wanted)]


def stale_pin_note(panes) -> str:
    """One sentence naming panes on an older pin, split by whether the repair is off — or "".

    The two halves matter to the reader for different reasons: the free ones are about to
    be reopened by the keeper (so this is the last look at what they were), and the held
    ones will NOT change, which is the state somebody who marked them was asking for.
    """
    panes = [p for p in (panes or []) if p]
    if not panes:
        return ""
    held = [p for p in panes if pane_repair_off(p)]
    free = [p for p in panes if p not in held]
    bits = []
    if free:
        bits.append(
            f"{', '.join(free)} on an older pin — the keeper reopens it on the current one"
        )
    if held:
        bits.append(f"{', '.join(held)} on an older pin, kept (@fbtodo_repair off)")
    return "; ".join(bits)


def upgrade_stale_panes(wanted, rows=None, table=None) -> list[str]:
    """Reopen every pane whose recorded command predates the pin, so it becomes this one.

    In plain words: the drift repair next door only looks at a pane whose INTERPRETER
    disagrees with its watcher's. A pane whose command is an older pin can agree about the
    interpreter and still be wrong — the state root it was started with, or the tmux server
    it drives, or the scripts its bells go to — and nothing else in the keeper would ever
    notice: the recorded command is only re-run when the pane is respawned, and a respawn is
    what nobody has a reason to do. So it is done here, once: `respawn-pane -k` runs the
    current pin in the same pane — same id, same window, same row — and that also REWRITES
    `pane_start_command`, which is the point. A pane found already carrying this build's
    values is left alone, so the pass settles: after one respawn the pane matches, and a
    keeper that restarts, or a hand `respawn-pane`, finds a current command.

    `start == pin` is not required, and must not be: a pane the `fb` launcher opened carries
    the right values under `--instance-of` while a fresh split says `--watch-pid`, and
    respawning it would replace a working pane to change an argument nobody disagreed about.
    What is required is what `stale_pin` asks — the carried values — so the respawn only
    ever happens where the environment is genuinely behind.

    The brakes are the drift repair's, for the same reasons: the pane's own `@fbtodo_repair`
    switch (a mix can be deliberate), one attempt per `DRIFT_RETRY_S` (`_STALE_TRIED`), and a
    note left for the pane so the person watching is told rather than surprised (`reopen`,
    with no interpreter to name — the pane says `REOPENED ON THE PIN`). A pane is never
    respawned on a guess: it has to be one of this instance's panes by name or ancestry
    (`local_pane_ids`) before it is looked at at all.
    """
    rows = pane_rows() if rows is None else rows
    table = process_table() if table is None else table
    upgraded = []
    for inst in wanted:
        pin = local_pane_command(inst)
        for pane in local_pane_ids(inst, rows, table):
            start = next((row["start"] for row in rows if row["pane"] == pane), "")
            if not stale_pin(start):
                continue
            if pane_repair_off(pane):
                if pane not in _STALE_HELD:
                    _STALE_HELD.add(pane)
                    pane_note_write(pane, "", kind="kept")
                    pane_log(
                        f"kept {pane}: its recorded command is an older pin, and the "
                        "repair is off (@fbtodo_repair)"
                    )
                continue
            _STALE_HELD.discard(pane)
            tried = _STALE_TRIED.get(pane)
            now = time.monotonic()
            if tried is not None and now - tried < DRIFT_RETRY_S:
                continue
            _STALE_TRIED[pane] = now
            # The note goes down before the respawn and comes back up if it did not happen,
            # exactly as the drift repair does it: a note whose respawn never ran must not be
            # claimed by whatever process takes that pane id next.
            pane_note_write(pane, "")
            if tmux_run("respawn-pane", "-k", "-t", pane, pin) is None:
                pane_note_forget(pane)
                continue
            upgraded.append(pane)
            pane_log(f"reopened {pane} on the current pin (its recorded command was an older one)")
    alive = {row["pane"] for row in rows}
    for pane in [p for p in _STALE_TRIED if p not in alive]:
        del _STALE_TRIED[pane]
    for pane in [p for p in _STALE_HELD if p not in alive]:
        _STALE_HELD.discard(pane)
    return upgraded


def ensure_local_panes(quiet: bool = True) -> list[str]:
    """Put back every local session's todo pane that is missing, in the right place.

    Deliberately not scoped to the watcher's own instance: sessions outnumber watchers
    here (one daemon, several `fb` runs in as many windows), and the watcher that holds
    the lock may not be following any instance at all. So the pass asks the whole tmux
    server instead: which panes are running a
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
    # own process line. None when no watcher is running, and then there is nothing to
    # compare a pane against and no repair to
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
        # ssh as well is not a second list pane to place, so one layout answers it.
        layout = pane_layout(window, pins, last)
        for pane in local_pane_ids(inst, rows, table):
            # The side this pane is held at: a pin's, or the one it is keeping (`kept_layout`),
            # which is also the one written down below — so a pane the owner moved to the
            # other axis of its session stays there and the next one opens there.
            here = kept_layout(pane, inst_pane, rects, layout)
            if place_pane_beside(pane, inst_pane, rects, here["side"]):
                moved.append(pane)
                rects = pane_rects()  # the layout just moved under us
            if hold_pane_size(pane, here["window"], rects, here):
                sized.append((pane, here["size_source"]))
                rects = pane_rects()
            # Written down for next time: a pane killed and reopened — or a whole tmux
            # server that went down — comes back at the size it was left, the side it was
            # put on, instead of at the default.
            remember_layout(window, pane, rects, here["side"], inst_pane)
    # The other drift: a pane that is there but on a different Python than the watcher's
    # (see `repair_drifted_panes`). Read from the same table the geometry pass used — a
    # respawn does not move a pane, so nothing above needs to run again.
    drifted = repair_drifted_panes(wanted, watcher_python, rows, table)
    # ...and the other repair: a pane whose recorded command is an OLDER pin. It can be in
    # step about the interpreter and still have been started with the wrong state root, or
    # with the old bells, and nothing else in the keeper has a reason to touch it. Read
    # fresh (`upgrade_stale_panes` re-reads) because the drift repair above may just have
    # respawned one of these panes: its new command IS the pin, and respawning it a second
    # time for the same finding is exactly what must not happen.
    upgraded = upgrade_stale_panes(wanted)
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
        if upgraded:
            print(
                "fbtodo pane(s) reopened on the current pin: "
                + ", ".join(upgraded),
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

    The fifth notifier, and the only one whose subject is a CLAIM: a process running
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


def live_watcher_pid(path: str) -> int | None:
    """The watcher claiming `path`, with a VERSION-STALE one replaced rather than adopted.

    A watcher keeps the code it started with, and it outlives the shell that spawned it,
    so after an upgrade the OLD one is still polling — with the old bugs. It is killed and
    its lock cleared instead of being honoured: it writes the old layout, and the pane
    (which checks the producer's version) then silently loses every field the new one
    added. That is how a pre-fix watcher stayed "live" forever, and how an upgrade
    could leave the pane rendering yesterday's shape.
    """
    # The probe, not a peek, and it MUST be: this path replaces — it is what a start or a
    # stop does, so a free leftover is removed, and a watcher left on another build is
    # killed and its claim cleared below. A command that only LOOKS reads with `lock_peek`
    # instead.
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


__all__ = [
    "tmux_run", "tmux_split_target", "PANE_INSTANCE_RE", "PANE_WATCH_RE",    "pane_off", "append_log", "pane_log", "pane_repaint", "pane_rows", "local_pane_ids",
    "freebuff_pane_id",
    "session_windows", "instances_with_pane", "pane_rects", "pane_axis", "placed_beside",
    "pane_before", "place_pane_beside", "kept_layout",
    "load_pins", "window_key", "pin_for_window", "load_last", "save_last",
    "pin_value", "pane_layout", "source_note", "_SETTLED", "_LAST_SEEN", "resize_pane_to",
    "hold_pane_size", "forget_settled", "remember_layout", "save_pins", "window_of",
    "KEEPER_GRACE", "KEEPER_CLAIM_CHECK_S", "keeper_reclaim",
    "PANE_NOTE_S", "pane_note_write", "pane_note_forget", "pane_note_take",
    "pane_subject_write", "pane_subject_read", "pane_subject_dir", "pane_process_root",
    "subject_note", "desktop_app_pane", "held_pane_subjects",
    "is_todo_pane_command", "todo_panes",
    "tmux_identity", "tmux_identity_outside", "tmux_socket_of", "same_tmux_server",
    "keeper_serves",
    "ensure_pane_keeper", "cmd_pane_watch",
    "pane_command", "pane_python", "pane_start_command", "DRIFT_RETRY_S",
    "pane_drifted", "repair_is_off", "pane_repair_value", "set_pane_repair", "pane_repair_off",
    "window_repair_value", "set_window_repair",
    "tree_pythons", "tree_drift", "repair_drifted_panes", "stale_pin", "stale_pane_ids",
    "stale_pin_note", "upgrade_stale_panes", "pinned_env_from", "session_pinned_env",
    "session_env_pids",
    "_DRIFT_TRIED", "_DRIFT_HELD", "_DRIFT_KEPT", "_STALE_TRIED", "_STALE_HELD",
    "local_pane_command", "local_pane_open",
    "ensure_local_panes", "notify_argv",
    "ask_notify_once", "pane_notify_once", "locks_notify_once", "pause_notify_once",
    "live_watcher_pid",
]
