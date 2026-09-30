"""fbtodo — watch the Freebuff agent's todo list.

    fbtodo              live pane (default); auto-starts the watcher, exits with freebuff
    fbtodo snap         one snapshot, plain text
    fbtodo json         one snapshot, clean JSON
    fbtodo bar          "todos 3/5" — for a tmux status bar
    fbtodo daemon       start the background watcher (-f for foreground)
    fbtodo stop         stop the watcher
    fbtodo pane-watch   keep the todo panes open, for as long as freebuff runs
                        (started for you; --once for one pass)
    fbtodo pin          pin the list pane's side or size for one window
                        (--side v|h|auto, --size N, --role local|nas|both,
                        --window TARGET, --list, --clear)
    fbtodo why          why each list pane is where it is: the side and size in force,
                        the source that supplied them, the pane it sits beside
                        (--window TARGET, --json)
    fbtodo status       instance, watcher, state-file and scratch footprint
    fbtodo ledger       every step's forecast vector beside the outcome it was scored
                        against — the rows behind the scoreboard
                        (--days N, --limit N, --model M, --json)
    fbtodo prune        enforce retention now (defaults: 2000 records / 60 days /
                        1 MiB log); runs by itself about hourly either way

The watcher (`daemon`) follows one running Freebuff process: while it lives, the
todo state is refreshed into `fbtodo-state.json` in the state directory
(`$XDG_STATE_HOME/fbtodo`, i.e. `~/.local/state/fbtodo`, or `FBTODO_HOME`); when the process
exits, the watcher shuts itself down and removes its lock. A second process, the pane
keeper (`pane-watch`), exists because a pane cannot be watched by itself: it puts a
todo pane back when one is killed mid-session, for every local session, and exits when
the last freebuff does. Either pane is also put back under the pane its session is drawn
in — the freebuff pane for a local session, the ssh for a NAS one — since that is where
a glance looks for it. `--pane-seconds N` sets how often it looks (3s), `--ask-seconds
N` how often a waiting question is looked for (3s; see `ask-bell.py`), and 0 switches
either off — as does FBTODO_NO_PANE for the panes, the same switch the shell wrapper
honours.

Every one of those failures is quiet by construction: the keeper has no stderr anybody
reads, and its log records a pane that came BACK, never one that did not. So a fourth
watch reports the watcher itself — `--pane-bell-seconds N` (60s; 0 = never) asks
`pane-bell.py` whether a session has no todo pane the keeper has failed to put back, or
whether there is no keeper at all while one is needed, and pushes to your phone once per
occurrence. `fbtodo status` says what it currently thinks.

The colour pane is framed, and its palette is themeable: FBTODO_ACCENT (the title's
badge), FBTODO_FAINT (the frame's own ink and the right-hand metadata), FBTODO_MUTED (the
grey of secondary text — step durations, the goal's label, the status strip), FBTODO_TRACK
(the progress bar's empty cells) and FBTODO_GRADIENT_START / FBTODO_GRADIENT_END (a
`#rrggbb` colour, or — for the accent — a raw SGR code like `1;36`) override
`./.fbtodo-theme.json`, which overrides `~/.config/fbtodo/theme.json`. Layout never names
a colour: `_styles` resolves the palette into roles, and the drawing code asks for a role.
Progress is a gradient bar of `█` over a visible `░` track — an eighth block for the one
cell at the boundary — drawn in 24-bit colour where the terminal takes it and in the nearest
256-colour entry where it does not, always as a glyph in a FOREGROUND colour rather than a
filled background cell. The title sits on an accent badge (reverse video, so no background
selector is needed), a step's duration is right-aligned against the frame's right wall, the
status strip badges the live state and spins while a step runs, and a heading the agent
never wrote is not drawn at all. The plain renderer is deliberately untouched: it is the
machine-readable path, and the patch row below appears there too, but only when the state
carries it.

Above that strip is one more row when there is something to say: `PATCH` — the last
outcome of the CLI-patch step, with the binary's version and how long ago — and `ALERT` —
the last thing the phone was told. Both are read from the log the producing step already
writes (`~/.config/freebuff-patch-watch/watch.log` and `~/.config/freebuff-notify/phone.log`,
or the NAS hook's own two, read at the far end of the ssh the pane already makes), so
neither has to be fetched with a probe of its own; a state with neither fact renders
exactly as before. `fbtodo status` prints the same pair in the same words.

Three stores: the two this machine keeps, and the one a remote host keeps when you run
freebuff there over ssh (`docker exec -it <container> freebuff`):
    CLI        ~/.config/manicode/projects/<project>/chats/<ISO>/log.jsonl   live, mid-turn
    Desktop    ~/.config/freebuff-desktop/projects/<slug>/desktop-v2.db      per turn, app only
    NAS        <host>:<root>/<project>/chats/<ISO>/log.jsonl                 live, over ssh
`--source auto` prefers a CLI chat for the cwd. `-s nas` reads the remote store
over ssh (see --nas-*, and set them: there is no built-in host), and follows the remote
freebuff PROCESS rather than a local pid, so the pane lives as long as the remote
session does.

Every new list replaces the old one: state is never merged, and when the session
changes the previous list is dropped immediately instead of lingering.

Each list is headed by its big goal: the agent's own one-line `Goal:` statement, which
AGENTS.md asks for and the journal keeps in `fullResponse`. The request the list was
written for is never shown as the goal — a quote is the phrasing, not the objective — so
a session that skips the line says `big goal · — none stated` in the plain renderer, and
in the framed pane simply draws no heading row. When a newer
request has arrived since, a `now` line says so: a list can sit unchanged for hours while
the session works on.

A comment that begins `# In plain words:` explains the code under it for a reader who does
not program. They are asides, not part of the program: read them straight down for a tour
of the file, or skip every one with a search for `In plain words`.
"""
from __future__ import annotations

import argparse
import calendar
import copy
import glob
import hashlib

try:
    # POSIX only, and the watcher's claim is a real one because of it: see `write_lock`.
    import fcntl
except ImportError:  # pragma: no cover - no flock, so a claim is the record alone
    fcntl = None

import re
import shlex
import shutil as _shutil
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
import unicodedata
import zlib

# Running this file AS A SCRIPT — `python3 src/fbtodo/__init__.py …`, which is what a copy
# carrying the package without the launcher ends up doing (`self_argv` names the launcher
# when there is one). Left alone it would be a module called `__main__` sitting beside a
# package called `fbtodo`: every file loaded twice and no relative import resolvable. So it
# re-enters as the package, where there is exactly one of everything, and exits there.
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from fbtodo import main as _main

    sys.exit(_main())

from .render import *  # noqa: F401,F403 — the package is one namespace


from .panes import *  # noqa: F401,F403 — the package is one namespace


from .sources import *  # noqa: F401,F403 — the package is one namespace


# The program, module by module: `base` is the floor (paths, settings, the generic tools), and
# each layer above it is imported with `*` so that everything stays reachable as `fbtodo.X` —
# one namespace, however many files it is written in. What each module holds is its `__all__`;
# the self-check reads the package statically to keep this promise honest.
from .base import *  # noqa: F401,F403 — the package is one namespace
from .locks import *  # noqa: F401,F403 — the package is one namespace
from .alerts import *  # noqa: F401,F403 — the package is one namespace
from .scan import *  # noqa: F401,F403 — the package is one namespace
from .desktop import *  # noqa: F401,F403 — the package is one namespace
from .nas import *  # noqa: F401,F403 — the package is one namespace
from .tasks import *  # noqa: F401,F403 — the package is one namespace


def list_fingerprint(state: dict) -> str:
    """Identity of one todo list: the session plus the *set of tasks*.

    Completion flags are deliberately excluded — ticking a task off is progress
    within a list, not a new list.
    """
    tasks = [str(t.get("task", "")) for t in (state.get("todos") or [])]
    payload = json.dumps(
        {"s": state.get("session"), "t": tasks}, ensure_ascii=False, sort_keys=True
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def finish_state(state: dict, prev: dict | None, claim_first: bool = False) -> dict:
    """Apply the replace-on-new-list, drop-on-new-session and drop-on-new-turn rules.

    `claim_first` belongs to the watcher: only it may declare an unseen list to be
    "list #1". A one-shot probe cannot see the list history, so it must borrow the
    counter from the watcher (see `adopt_version`) rather than invent one.
    """
    todos = state.get("todos") or []
    # In plain words: a list that is FINISHED does not belong to the turn that comes after it.
    # Left standing, it reads as this turn's progress — the previous project's steps, at 100%,
    # while the agent is on something else entirely — and it stays that way until a new list
    # happens to arrive, which can be many minutes. So a finished list is dropped the moment a
    # newer request or a newer turn shows up. A list with work LEFT on it is never dropped:
    # that is the agent's standing plan, and `now`/`NUDGE` is how its drift gets reported.
    written = int(state.get("ts") or state.get("source_updated_ms") or 0)
    turn_start = int((state.get("turn") or {}).get("start_ms") or 0)
    after_turn = bool(turn_start and written and turn_start > written)
    after_request = bool(state.get("now") or state.get("nudge"))
    drop_turn = bool(
        todos and all(t.get("completed") for t in todos) and (after_turn or after_request)
    )
    if drop_turn:
        todos = []
        state["todos"] = []
        # What goes with the list is the objective it was written FOR — a pane still headed
        # with the previous turn's goal is the same misreading in prose, and this turn's own
        # heading arrives with its own list. `now` and `nudge` deliberately stay: they describe
        # the request that replaced the list, not the list, and with nothing on screen they are
        # the only thing left naming what this turn is about.
        for gone in ("goal", "goal_source"):
            state[gone] = None
    fp = list_fingerprint(state)
    version = int((prev or {}).get("list_version") or 0)
    session_changed = bool(prev) and prev.get("session") != state.get("session")
    list_changed = bool(prev) and prev.get("list_id") != fp
    cleared = False

    if session_changed:
        # A new session owns the pane now: its list is not the previous one, and
        # the old list must not linger while the new one is being written.
        version = 0
        if not todos:
            cleared = True
    if drop_turn:
        # Note that this does NOT touch the counter: the list number identifies a list, and a
        # dropped one is still the list it was. The new turn's list is what increments it.
        cleared = True
    if list_changed and todos:
        version += 1
    elif not prev and claim_first:
        version = 1 if todos else 0

    state["list_id"] = fp
    state["list_version"] = version
    if session_changed or prev is None:
        state["session_changed_ms"] = state.get("probed_ms") or int(time.time() * 1000)
    else:
        state["session_changed_ms"] = (prev or {}).get("session_changed_ms")
    state["cleared"] = cleared
    # Which of the two drops it was decides the sentence the pane prints, so it is carried
    # rather than inferred: "a new session" and "a finished turn" are different news.
    state["cleared_turn"] = drop_turn
    state["replaced"] = bool(list_changed and todos and not session_changed)
    state["done"] = sum(1 for t in todos if t.get("completed"))
    state["total"] = len(todos)
    return state


def state_matches_request(state: dict | None, args) -> bool:
    """Is the cached watcher state an answer to THIS question?

    `auto` takes whatever is live, but an explicit `-s nas` must not be answered by a
    state file describing the local CLI session: a status bar asking for the NAS list
    printed the Mac's own list instead, which looked like the NAS had one. The
    producer's version is checked for the same reason: a watcher left running across an
    upgrade keeps writing the old layout, and then a pane that trusts it silently loses
    every field the new one added (that is how a goal line would go missing).
    """
    if not state:
        return False
    if state.get("tool_version") != VERSION:
        return False
    source = getattr(args, "source", "auto")
    return source == "auto" or (state.get("backend") or "") == source


# =================================================================== commands
def ensure_daemon(args, quiet: bool = True) -> int | None:
    # The panes are kept by their own process, asked for here because this is the moment
    # a session and its pane appear — and asked for BEFORE the watcher lock is consulted,
    # so a `-s nas` daemon holding that lock cannot leave a local pane unguarded.
    ensure_pane_keeper(args, quiet=quiet)
    running = live_watcher_pid(LOCK_PATH)  # an upgrade's leftover is replaced, not adopted
    if running:
        return running
    cwd = os.path.realpath(os.getcwd())
    if args.source == "nas":
        # Nothing local to adopt: the session is a process on the NAS, and the watcher
        # finds out about it from the probe rather than from this machine's process table.
        return spawn_daemon(args, cwd, None, quiet=quiet)
    inst, _ = find_instance(
        cwd, getattr(args, "watch_pid", None), getattr(args, "instance_of", None)
    )
    if not inst:
        if not quiet:
            print(f"no running Freebuff instance for {cwd}", file=sys.stderr)
        return None
    return spawn_daemon(args, cwd, inst, quiet=quiet)


def spawn_daemon(args, cwd: str, instance_pid: int, quiet: bool = True) -> int | None:
    argv = [
        *self_argv(),
        "daemon",
        "--foreground",
        "--quiet",
        "--cwd",
        cwd,
        "-i",
        str(args.interval),
        "-s",
        args.source,
        "--cli-root",
        args.cli_root,
        "--db",
        args.db,
        "--state",
        args.state,
        # Passed on for the same reason as the NAS knobs below: a daemon that inherited
        # the module default would keep a killed pane reopened for a caller who asked it
        # not to (or never notice one, for a caller who asked it to be quicker).
        "--pane-seconds",
        str(args.pane_seconds),
        "--ask-seconds",
        str(args.ask_seconds),
        "--pause-seconds",
        str(args.pause_seconds),
    ]
    if instance_pid:
        argv += ["--instance-pid", str(instance_pid)]
    if args.project:
        argv += ["--project", args.project]
    if args.chat:
        argv += ["--chat", args.chat]
    # Only meaningful for -s nas, but passed unconditionally so the daemon never falls back
    # to the module defaults when the caller overrode them.
    argv += [
        "--nas-host",
        args.nas_host,
        "--nas-root",
        args.nas_root,
        "--nas-project",
        args.nas_project,
    ]
    # The estimate knobs, forwarded only when the caller actually set them: the watcher is
    # the process that builds the memories, so a flag it never saw would be silently halved.
    if args.label_floor is not None:
        argv += ["--label-floor", str(args.label_floor)]
    if args.blend_weight is not None:
        argv += ["--blend-weight", str(args.blend_weight)]
    log = open(LOG_PATH, "ab", buffering=0)
    try:
        proc = subprocess.Popen(
            argv, stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True
        )
    finally:
        log.close()
    # wait for the lock so callers can rely on the daemon existing
    for _ in range(60):
        time.sleep(0.05)
        if daemon_pid():
            break
    pid = daemon_pid()
    if not quiet and not pid:
        print("watcher failed to start; see " + LOG_PATH, file=sys.stderr)
    return pid


def resolve_instance(args):
    """(cwd to serve, instance pid) — or exit 66 when Freebuff is not running.

    Started from a shell hook (no --cwd, no --watch-pid) the watcher deliberately
    adopts whichever instance is running and serves *its* directory, so one
    watcher covers the machine instead of only the shell's own cwd.
    """
    served = os.path.realpath(os.path.expanduser(args.cwd)) if args.cwd else None
    cwd = served or os.path.realpath(os.getcwd())
    if args.source == "nas":
        # The session is on the NAS, so there is no local pid to report — the lock carries
        # None and liveness comes from the probe.
        return cwd, None
    if args.instance_pid is not None:
        return cwd, args.instance_pid
    inst, inst_cwd = find_instance(cwd, args.watch_pid, args.instance_of)
    if not inst:
        return None, None
    if served is None and args.watch_pid is None and args.instance_of is None and inst_cwd:
        cwd = os.path.realpath(inst_cwd)
    return cwd, inst


def cmd_daemon(args) -> int:
    if not args.foreground:
        # The shell autostart comes through here, so a keeper appears with the first
        # freebuff of the day even if nothing opens a pane in this terminal.
        ensure_pane_keeper(args, quiet=args.quiet)
        if live_watcher_pid(LOCK_PATH) and not args.force:
            if not args.quiet:
                print(f"watcher already running (pid {daemon_pid()})", file=sys.stderr)
            return 0
        cwd, inst = resolve_instance(args)
        if not inst:
            if not args.quiet:
                print("no running Freebuff instance", file=sys.stderr)
            return EX_CODES["nofile"]
        pid = spawn_daemon(args, cwd, inst, quiet=args.quiet)
        if not pid:
            return EX_CODES["tempfail"]
        if not args.quiet:
            print(f"fbtodo watcher started (pid {pid}, following freebuff pid {inst})", file=sys.stderr)
        return 0
    return daemon_loop(args)


def daemon_loop(args) -> int:
    if daemon_pid() and not args.force:
        if not args.quiet:
            print(f"watcher already running (pid {daemon_pid()})", file=sys.stderr)
        return 0

    cwd, instance_pid = resolve_instance(args)
    remote = args.source == "nas"
    if not instance_pid and not remote:
        if not args.quiet:
            print("no running Freebuff instance", file=sys.stderr)
        return EX_CODES["nofile"]
    remote_seen = False

    prev = read_json(STATE_PATH, None)
    # What the last poll had to say (the clock taken out), and when the heartbeat on disk
    # next needs refreshing. `0.0` is due immediately, so the first poll always writes.
    prev_evidence = state_evidence(prev)
    heartbeat_due = 0.0
    if not claim_or_force(LOCK_PATH, cwd, instance_pid, bool(getattr(args, "force", False))):
        # Between the check above and here another watcher could have taken the claim; the
        # claim is what decides, so losing that race means standing down rather than
        # writing a second watcher's state over the same files.
        if not args.quiet:
            print(f"watcher already running (pid {daemon_pid() or '—'})", file=sys.stderr)
        return 0
    # The ask watch's own clock: one scan of the panes every `--ask-seconds`, whatever the
    # render interval is, because a question can arrive between two of them.
    ask_due = 0.0
    # ...and the stall watch's, asked on its own pace: a stop is measured in minutes, so
    # looking more often than `--pause-seconds` buys nothing.
    pause_due = 0.0
    # ...and the pane watch's, on a third clock. It starts one whole interval out, and that
    # is not a detail: it only reports a session that has had no pane for longer than its
    # 30s grace, so an ask before that can never have anything to say — while costing the
    # same half second as the other two, in the very iteration whose heartbeat a reader is
    # most likely to be watching. The ask watch is the opposite case (a question can be on
    # screen at any moment) and starts at once.
    bell_due = time.monotonic() + max(1.0, args.pane_bell_seconds)
    stop_reason = "stopped"
    stop_code = 0

    def shutdown(signum=None, _frame=None):
        nonlocal stop_reason, stop_code
        stop_reason = f"signal-{signum}" if signum else "stopped"
        stop_code = 128 + signum if signum else 0
        raise SystemExit(stop_code)

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, shutdown)

    try:
        while True:
            if not remote and not pid_alive(instance_pid):
                stop_reason = "instance-exited"
                stop_code = 0
                break
            if not lock_ours(LOCK_PATH):
                # the claim is this watcher's hold on the state directory; if it is gone
                # (removed, or the directory itself), or if the file is no longer the one
                # this process locked (something replaced it), the watcher is no longer
                # the owner — stop instead of re-creating the directory.
                stop_reason = "lock-removed"
                stop_code = 0
                break
            st = snapshot(args, cwd=cwd, instance_pid=instance_pid)
            if remote:
                # There is no local pid to lose, so the session ending is read from the probe:
                # close only after one has actually been SEEN, because the pane is opened by the
                # ssh alias and `fb` may not be typed for minutes afterwards.
                if st.get("instance_alive"):
                    remote_seen = True
                elif remote_seen:
                    stop_reason = "instance-exited"
                    stop_code = 0
                    break
            st = finish_state(st, prev, claim_first=True)
            st = track_tasks(st)
            prune_scratch()  # no-op unless an hour has passed
            st["daemon_pid"] = os.getpid()
            st["heartbeat_ms"] = int(time.time() * 1000)
            st["tool_version"] = VERSION
            st["status"] = "watching"
            st["stop_reason"] = None
            # Write when there is something to say, and otherwise only to keep the
            # heartbeat inside the window a reader calls fresh — measured on a live list:
            # 0.96 rewrites a second (26.8 MB/hour) of a file whose only change was the
            # clock. A rewrite that carries no evidence also carries no fsync: there is
            # nothing in it worth flushing.
            evidence = state_evidence(st)
            if evidence != prev_evidence or time.monotonic() >= heartbeat_due:
                atomic_write_json(STATE_PATH, st, fsync=evidence != prev_evidence)
                heartbeat_due = time.monotonic() + HB_REFRESH
            prev = st
            prev_evidence = evidence
            if args.ask_seconds > 0 and time.monotonic() >= ask_due:
                ask_due = time.monotonic() + args.ask_seconds
                ask_notify_once(args, quiet=args.quiet)
            if not remote and args.pause_seconds > 0 and time.monotonic() >= pause_due:
                pause_due = time.monotonic() + args.pause_seconds
                pause_notify_once(args, instance_pid, quiet=args.quiet)
            # Not gated on `remote`: what it reports is this machine's tmux, and a `-s
            # nas` daemon is often the only thing running here to ask.
            if args.pane_bell_seconds > 0 and time.monotonic() >= bell_due:
                bell_due = time.monotonic() + args.pane_bell_seconds
                pane_notify_once(args, quiet=args.quiet)
            time.sleep(max(0.2, args.interval))
    except SystemExit:
        pass
    except Exception as exc:  # never die silently leaving a stale lock
        stop_reason = f"error: {exc.__class__.__name__}"
        stop_code = EX_CODES["tempfail"]
    finally:
        try:
            if os.path.isdir(os.path.dirname(LOCK_PATH)):
                # leave a tombstone so consumers can tell "stopped" from "never
                # ran" — but never resurrect a directory that was removed
                final = dict(prev or {})
                final.update(
                    status="stopped",
                    stop_reason=stop_reason,
                    heartbeat_ms=int(time.time() * 1000),
                    daemon_pid=None,
                )
                final.pop("todos", None)  # a stopped watcher owns no list
                atomic_write_json(STATE_PATH, final)
        except Exception:
            pass
        clear_lock(os.getpid())
    if not args.quiet and stop_reason:
        print(f"fbtodo watcher stopped ({stop_reason})", file=sys.stderr)
    return stop_code


def cmd_doctor(args) -> int:
    """Whether this machine can show a pane, and what to fix when it cannot.

    Every question here is one the tool asks for real somewhere else in this file — tmux,
    a writable scratch dir, the terminal's colour story, the tools discovery needs — asked
    all at once and ANSWERED rather than assumed. `ok` needs no action, `warn` is a
    preference that is not met (a pane still works), `FAIL` is something that stops one,
    and any FAIL exits non-zero: that is what makes this a gate a wrapper or a CI job can
    call, rather than a paragraph of advice. `--json` is the same list for a machine.
    """
    checks: list = []  # (level, name, detail)

    py = sys.version_info
    checks.append(("ok" if py >= (3, 9) else "FAIL", "python",
                   f"{py.major}.{py.minor}.{py.micro} at {sys.executable}"))

    tmux_bin = TMUX_BIN[0] if TMUX_BIN else "tmux"
    if not _shutil.which(tmux_bin):
        checks.append(("FAIL", "tmux", f"no {tmux_bin} on PATH — a pane cannot be opened"))
    else:
        ver = ""
        try:
            ver = subprocess.run(TMUX_BIN + ["-V"], capture_output=True, text=True,
                                 timeout=5).stdout.strip()
        except Exception:
            ver = ""
        checks.append(("ok" if ver else "warn", "tmux", ver or "present, but would not answer -V"))
        feats = ""
        try:
            feats = subprocess.run(TMUX_BIN + ["show", "-gv", "terminal-features"],
                                   capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:
            feats = ""
        if not feats:
            checks.append(("warn", "tmux colour", "no server to ask (a pane will start one)"))
        elif "RGB" in feats:
            checks.append(("ok", "tmux colour", "RGB in terminal-features"))
        else:
            # tmux's own forwarding story, which is NOT what the pane's colours are decided
            # by (that is the `colour` line below, from COLORTERM/TERM): a server without
            # RGB takes the 256-colour palette, and the pane knows it.
            checks.append(("warn", "tmux colour",
                           "no RGB in terminal-features — tmux forwards 256 colours"))

    truecolor = _supports_truecolor()
    checks.append(("ok" if truecolor else "warn", "colour",
                   f"{'24-bit' if truecolor else '256-colour'} "
                   f"(TERM={os.environ.get('TERM') or '—'}, "
                   f"COLORTERM={os.environ.get('COLORTERM') or '—'})"
                   + ("" if truecolor else " — COLORTERM=truecolor gets the designed palette")))
    loc = os.environ.get("LC_ALL") or os.environ.get("LC_CTYPE") or os.environ.get("LANG") or ""
    enc = sys.stdout.encoding or ""
    utf8 = "utf" in loc.lower() or "utf" in enc.lower()
    checks.append(("ok" if utf8 else "warn", "locale",
                   f"{loc or 'unset'} / stdout {enc or '—'}"
                   + ("" if utf8 else " — the frame's glyphs need a UTF-8 locale")))

    for tool, level, why in (
        ("ps", "FAIL", "the process table is how the instance is found"),
        ("lsof", "warn" if os.path.isdir(PROC_ROOT) else "FAIL", "cwd discovery"),
    ):
        found = _shutil.which(tool)
        checks.append(("ok", tool, found) if found else (level, tool, f"missing — {why}"))
    checks.append(("ok", "cwd source",
                   f"/proc at {PROC_ROOT}" if os.path.isdir(PROC_ROOT) else "lsof (no /proc)"))

    try:
        os.makedirs(SCRATCH, mode=0o700, exist_ok=True)
        probe = os.path.join(SCRATCH, ".fbtodo-doctor-probe")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.unlink(probe)
        checks.append(("ok", "scratch", SCRATCH))
    except OSError as exc:
        checks.append(("FAIL", "scratch", f"{SCRATCH}: {exc.__class__.__name__}"))
    if STATE_NOTE == "moved":
        checks.append(("ok", "state", f"{SCRATCH} (moved from {LEGACY_SCRATCH})"))
    elif STATE_NOTE == "live":
        checks.append(("warn", "state",
                       f"still {SCRATCH} — a live watcher or keeper owns it; "
                       f"stop both and the next run moves it to {STATE_DIR}"))
    elif STATE_NOTE == "failed":
        checks.append(("warn", "state", f"still {SCRATCH} — could not move to {STATE_DIR}"))
    holder = lock_holder(LOCK_PATH)
    checks.append(("ok" if holder else "warn", "watcher",
                   f"pid {holder} holds {os.path.basename(LOCK_PATH)}" if holder
                   else "not running (a pane starts one)"))

    kit = os.path.dirname(NAS_NOTIFY)
    if not os.path.isdir(kit):
        checks.append(("warn", "notify kit", f"not installed ({kit}) — panes work, bells do not"))
    else:
        missing = [n for n in ("phone.sh", "todo-bell.py", "bell.sh")
                   if not os.path.exists(os.path.join(kit, n))]
        checks.append(("warn", "notify kit", f"missing {', '.join(missing)}") if missing
                      else ("ok", "notify kit", kit))

    inst, inst_cwd = find_instance(os.path.realpath(os.getcwd()))
    checks.append(("ok", "instance", f"freebuff pid {inst} in {inst_cwd}") if inst
                  else ("warn", "instance",
                        "no freebuff for this directory — a watcher here would exit 66"))

    fails = [c for c in checks if c[0] == "FAIL"]
    warns = [c for c in checks if c[0] == "warn"]
    if args.json:
        json.dump({"version": VERSION, "ok": not fails, "fail": len(fails),
                   "warn": len(warns),
                   "checks": [{"level": lv, "name": name, "detail": detail}
                              for lv, name, detail in checks]},
                  sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 1 if fails else 0
    ink = {"ok": "2", "warn": "33", "FAIL": "1;31"}
    c = paint(use_color())
    print(f"{c('1', 'fbtodo doctor')}  {c('2', VERSION)}")
    for level, name, detail in checks:
        print(f"  {c(ink[level], level.ljust(4))} {name.ljust(11)} {detail}")
    print(f"  {len(checks) - len(warns) - len(fails)} ok · {len(warns)} warn · {len(fails)} fail"
          + (f"   first problem: {fails[0][1]}" if fails else ""))
    return 1 if fails else 0


def cmd_stop(args) -> int:
    pid = daemon_pid()
    if not pid:
        clear_lock()
        print("fbtodo watcher: not running", file=sys.stderr)
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    for _ in range(40):
        if not pid_alive(pid):
            break
        time.sleep(0.05)
    clear_lock()
    if not args.quiet:
        print(f"fbtodo watcher stopped (pid {pid})", file=sys.stderr)
    return 0


def cmd_status(args) -> int:
    cwd = os.path.realpath(os.getcwd())
    inst, inst_cwd = find_instance(cwd, args.watch_pid, args.instance_of)
    pid = daemon_pid()
    st = read_json(STATE_PATH, None)
    # The state file is whatever the LOCAL watcher follows. `-s nas` asks about the NAS, so
    # ask it (one probe): reporting the local session's list against "nas session: — no
    # session found" was a lie about a box that was running a freebuff at the time.
    live = adopt_version(finish_state(snapshot(args), None)) if args.source == "nas" else None
    view = live or st
    read_theme()  # the palette resolves its refusals here, so both branches can report them
    if args.json:
        json.dump(
            {
                "instance_pid": inst,
                "instance_cwd": inst_cwd,
                "watcher_pid": pid,
                "state_file": STATE_PATH,
                "state_fresh": state_is_fresh(st),
                "theme_problems": [list(p) for p in THEME_PROBLEMS],
                "refit_readiness": refit_readiness(
                    load_tasklog().get("tasks") or {}, model=(st or {}).get("model")
                ),
                "state": st,
            },
            sys.stdout,
            ensure_ascii=False,
        )
        sys.stdout.write("\n")
        return 0
    c = paint(use_color())
    print(f"{c('1', 'fbtodo')}  {c('2', cwd)}")
    print(f"  freebuff instance : {inst or '—'}{f'  ({inst_cwd})' if inst_cwd else ''}")
    others = [p for p in freebuff_pids() if p != inst]
    if others:
        ages = ages_for(others)
        shown = ", ".join(
            f"{p} ({short_duration(ages.get(p, 0) * 1000)} old)" for p in others
        )
        print(f"  other instances   : {shown}  — pin one with --instance-of/--watch-pid")
    print(f"  watcher           : {pid or '— not running'}")
    # A theme value the palette REFUSED (see `read_theme`) is reported here rather than at
    # render time: the pane falls back to the default and looks right, which is exactly
    # how a bad value survived unnoticed before.
    read_theme()
    if THEME_PROBLEMS:
        shown = ", ".join(f"{name}={value!r}" for name, value in THEME_PROBLEMS)
        print(f"  theme values      : {len(THEME_PROBLEMS)} ignored  ({shown})")
    # In plain words: which build of fbtodo is actually running. The version is
    # hand-maintained, so a watcher left over from an earlier copy keeps working while the
    # pane beside it is a different build — the one way this pane has ever misled its
    # owner. Both numbers on one line make that self-diagnosing: they agree when the pane
    # and its watcher are the same build, and the state's own stamp is exactly what a
    # version-stale watcher is killed for on the next start (see `live_watcher_pid`).
    watching = (st or {}).get("tool_version")
    stale_note = (
        f"  (watcher {watching} — stale, replaced on the next start)"
        if watching and watching != VERSION else ""
    )
    print(f"  tool version      : {VERSION}{stale_note}")
    sess = (st or {}).get("session")
    reason = (st or {}).get("stop_reason")
    bits = ["fresh" if state_is_fresh(st) else "stale"]
    if sess:
        bits.append(f"session {sess}")
    if reason:
        bits.append(str(reason))
    print(f"  state file        : {'  '.join(bits)}")
    # The patches' own two facts, in the same words the pane's row uses and from the same
    # reader: what the last patch pass did, and when the phone was last told something.
    # The NAS answer rides in the probe above; locally it is two tail reads.
    rep = (
        {"patch": (view or {}).get("patch"), "alert": (view or {}).get("alert")}
        if args.source == "nas"
        else local_patch_alert()
    )
    seen = {label: text for label, text, _sev in patch_row(rep)}
    print(f"  cli patches       : {seen.get('PATCH') or '— nothing logged'}")
    print(f"  last alert        : {seen.get('ALERT') or '— nothing logged'}")
    if args.source == "nas" or (st or {}).get("backend") == "nas":
        nas = (view or {}).get("nas") or {}
        print(
            f"  nas session       : {nas.get('dir') or '— no session found'}"
            f"  ({'live' if nas.get('live') else 'not running'})"
        )
        if nas.get("fb") and nas.get("fb") != "-":
            marker = "live" if nas.get("fb") == "1" else "stale"
            print(f"  fb marker         : {marker}  {nas.get('fb_project') or '-'}")
    if view and not view.get("todos"):
        # Said out loud, in the pane's own words. Until this line existed a session with no
        # list had no `todos` row at all, and a missing row reads as a broken command rather
        # than as "the agent has not written one yet" — which is the whole question this
        # answers. Same sentence as the pane, from the same function.
        print(f"  todos             : — none yet  ({no_list_reason(view)})")
        # The second basket, in the same words the pane uses: what the session has DONE
        # instead of writing a list. Absent when the state carries no calls, so a fixture
        # or a brand-new session prints exactly as it did before this line existed.
        feed = observed_rows(view, 4)
        for i, seg in enumerate(feed):
            print(f"  {'last actions      ' if not i else '                   '}: {seg}")
    if view and view.get("todos"):
        counts = f"{view.get('done', 0)}/{view.get('total', len(view['todos']))} done"
        if view.get("list_version"):
            counts += f", list #{view['list_version']}"
        print(f"  todos             : {counts}")
        # The list's own age. The pane has this number too, but only in the strip's
        # parentheses and only while the state is idle — a clock on a step takes that slot,
        # by design. Said here it is always answered, which is what makes "an agent that
        # stopped calling `write_todos` leaves the old progress up" a question the command
        # line can settle.
        print(
            "  list written      : "
            + (fmt_age(view.get("source_updated_ms") or view.get("ts")) or "?")
        )
        if list_behind(view, int(time.time() * 1000)):
            moved = int(view.get("store_mtime_ms") or 0) - int(
                view.get("ts") or view.get("source_updated_ms") or 0
            )
            print(
                "  list behind       : yes  — every step is ticked, and no newer"
                f" `write_todos` in {short_duration(moved) or '10m'}"
            )
    if view and (view.get("goal") or view.get("todos")):
        goal = str(view.get("goal") or "")
        if not goal:
            print("  big goal          : — none stated (the agent owes a `Goal:` line)")
        else:
            over = len(goal) - GOAL_MAX_CHARS
            note = "" if over <= 0 else f"  — {over} chars over the {GOAL_MAX_CHARS} that fit"
            print(f"  big goal          : {goal}{note}")
            print(f"  goal source       : agent's `Goal:` line")
    if view and view.get("now"):
        # "has not caught up" is a claim about a list: with none on screen (a finished one was
        # just dropped), the request is simply the newer thing, and nothing is behind it.
        behind = "  — the list has not caught up" if view.get("todos") else ""
        print(f"  newer request     : {view.get('now')}{behind}")
    if view:
        # The turn, for both cases: with a list it says which turn the list belongs to, and
        # without one it is the only measured thing there is. Same sentence as the pane's.
        turn_line = turn_note(view, int(time.time() * 1000))
        if turn_line:
            print(f"  turn              : {turn_line}")
    if view and view.get("nudge"):
        print(
            f"  pending nudge     : {view.get('nudge')}  — "
            "re-write the list, then continue"
        )
    if view and view.get("backend") == "cli" and view.get("todos"):
        # What the finished-task bell rings on, said out loud: a ticked-off list with the
        # agent still working is exactly the case that must stay silent.
        if view.get("turn_ended"):
            print("  agent             : turn ended — waiting for you")
        else:
            print("  agent             : working (the list is not the finish line)")
    # The ask watch, said out loud like the other notifiers: a question on screen stops
    # the agent until it is answered, so "is one up right now" belongs in a status line.
    if not os.path.exists(PAUSE_NOTIFY):
        print("  stall watch       : none installed (~/.config/freebuff-notify/pause-bell.py)")
    elif args.pause_seconds <= 0:
        print("  stall watch       : off (--pause-seconds 0)")
    else:
        print(f"  stall watch       : on, every {args.pause_seconds:.0f}s")
    if not os.path.exists(ASK_NOTIFY):
        print("  ask watch         : none installed (~/.config/freebuff-notify/ask-bell.py)")
    elif args.ask_seconds <= 0:
        print("  ask watch         : off (--ask-seconds 0)")
    else:
        asking = ""
        try:
            proc = subprocess.run(
                [ASK_NOTIFY, "--print"], capture_output=True, text=True, timeout=10
            )
            line = (proc.stdout or "").strip()
            if line.startswith("ASK: "):
                asking = "  — a question is on screen now: " + line.split(" — ", 1)[-1][:60]
        except (OSError, subprocess.SubprocessError):
            pass
        print(f"  ask watch         : on, every {args.ask_seconds:.0f}s{asking}")
    # The pane watch, in the same three states — and it says what it currently thinks,
    # because "is the pane watcher working" is otherwise only answerable by killing a pane
    # and waiting to see whether it comes back.
    if not os.path.exists(PANE_NOTIFY):
        print("  pane watch        : none installed (~/.config/freebuff-notify/pane-bell.py)")
    elif getattr(args, "pane_bell_seconds", 0) <= 0:
        print("  pane watch        : off (--pane-bell-seconds 0)")
    else:
        verdict = ""
        try:
            proc = subprocess.run(
                [PANE_NOTIFY, "--print", "--keeper", PANE_KEEPER_PATH],
                capture_output=True, text=True, timeout=30,
            )
            line = (proc.stdout or "").strip()
            if line.startswith("PANE: "):
                verdict = "  — FAILING NOW: " + line.split(" — ", 1)[-1].splitlines()[0][:70]
            elif line.startswith("silent: "):
                verdict = "  — " + line.split(": ", 1)[-1][:70]
        except (OSError, subprocess.SubprocessError):
            pass
        print(
            f"  pane watch        : on, every {args.pane_bell_seconds:.0f}s{verdict}"
        )
    # Where this session's list is drawn. It is put back within seconds of being killed,
    # so "none" is a fact about right now rather than a broken setup — and outside tmux
    # there is nothing to show, which is not an error either.
    if inst:
        mine = local_pane_ids(inst)
        print(f"  todo pane         : {', '.join(mine) if mine else '— none right now'}")
    log = load_tasklog()
    last = log.get("pruned_ms")
    when = f"{short_duration((time.time() * 1000 - last))} ago" if last else "never"
    print(
        f"  task records      : {len(log.get('tasks') or {})} kept "
        f"(caps {MAX_TASK_RECORDS} records / {MAX_TASK_AGE_DAYS}d), pruned {when}"
    )
    # What the estimates ride on, and where each waiting step's number comes from: the
    # feature is measured from the log, so this is the block that answers "why is that
    # step ~5m?" without reading the JSON by hand.
    model = (view or {}).get("model") or None
    history = task_history_from_log(log.get("tasks") or {}, model=model)
    shapes = shape_history_from_log(log.get("tasks") or {}, model=model)
    calls_mem = call_memory_from_log(log.get("tasks") or {}, model=model)
    todos = (view or {}).get("todos") or []
    times = (view or {}).get("task_times") or {}
    now_est = int(time.time() * 1000)
    pace = step_pace_ms(times, todos, now_est, history)
    pace_spread = pace_spread_ms(times, todos, now_est, history)
    n_samples = sum(int((v or {}).get("n") or 1) for v in history.values())
    origin = f"{n_samples} remembered step(s)" if history else "no remembered steps yet"
    # The spread is printed with the pace it belongs to: `2m` from 10s and 20m of finished
    # steps is a different claim from `2m` from 2m and 2m, and only the spread says which.
    if pace_spread and is_wide(*pace_spread):
        origin += f", spread {fmt_range(*pace_spread)}"
    print(f"  remembered pace   : {short_duration(pace, seconds=True)}  ({origin})")
    # The memory that can improve with use: keyed by what a step DID rather than what it was
    # called, so its samples accumulate where the wording memory has almost none. Printed
    # with its own sample counts, because "is this getting better?" should be answerable
    # from the terminal and not from faith.
    if shapes:
        # What the rung can actually READ first: a bucket the ladder would never look up is
        # not evidence that the memory is pulling its weight, so the held-back ones are
        # counted beside it rather than shown as if they were in use.
        readable = {b: e for b, e in shapes.items() if sized_entry(shapes, b)}
        young = [b for b in shapes if (bucket_ordinal(b) or 0) < SHAPE_MIN_BUCKET]
        thin = [b for b in shapes if b not in young and sized_entry(shapes, b) is None]
        held = ""
        if young or thin:
            why = []
            if young:
                why.append(f"{len(young)} under the calls{SHAPE_MIN_BUCKET} floor")
            if thin:
                why.append(f"{len(thin)} short of {SHAPE_MIN_SAMPLES} sample(s)")
            held = f" · {len(young) + len(thin)} not read yet ({', '.join(why)})"
        top = sorted(readable.items(), key=lambda kv: -int(kv[1].get("n") or 0))[:3]
        shown = ", ".join(
            f"{bucket} {fmt_estimate(entry.get('med') or 0)} (n={entry.get('n')})"
            for bucket, entry in top
        )
        if top:
            print(f"  remembered sizes  : {len(shapes)} kept — {shown}{held}")
        else:
            print(f"  remembered sizes  : {len(shapes)} kept, none the rung may read yet{held}")
    else:
        print("  remembered sizes  : none yet (a size is learned when a step finishes)")
    # What a WAITING step is priced from: its wording's kind, the calls that kind usually
    # takes, and what each of those calls costs. Printed because it is the memory behind
    # every row that has not started, and those are most of what `EST REM` sums.
    if calls_mem:
        kinds = ", ".join(
            f"{k} {v}" for k, v in sorted((calls_mem.get("classes") or {}).items())
        )
        print(f"  remembered calls  : {calls_mem.get('calls')} calls x "
              f"{short_duration(calls_mem.get('rate_ms') or 0, seconds=True)} "
              f"({kinds})")
    else:
        print("  remembered calls  : none yet (a kind is learned when a step finishes)")
    # The one line that answers "is this getting better?" — and the only honest way to
    # answer it, since the estimate and the outcome are both in the log. Split by source so
    # a shape that is pulling its weight shows up as a lower median than the list's pace.
    err = estimate_error(log.get("tasks") or {}, model=model)
    parts = [
        f"{src} {err[src]['med']:.2f}x median over {err[src]['n']}"
        for src in ("shape", "blend", "pace")
        if err.get(src)
    ]
    if parts:
        print(f"  estimate error    : {' · '.join(parts)}  (factor error of closed steps)")
    else:
        print(
            "  estimate error    : — nothing closed yet since estimates were stamped "
            "(a factor of 1.0x would be exact)"
        )
    # ...and the honest half: the same rungs scored on the forecast each step got on the
    # FIRST poll that saw it running, before its size was known. The line above scores the
    # number the pane was showing as the step closed, whose size key is built from calls the
    # step had already made; this one cannot flatter a rung that recognises.
    ferr = forecast_error(log.get("tasks") or {}, model=model)
    # The interval rides beside the median, because the median alone cannot tell a rung that
    # misses by a tenth every time from one that misses by a tenth on average.
    fparts = [f"{src} {fmt_rung(ferr[src])}" for src in SHIPPED_RUNGS if ferr.get(src)]
    skipped = (ferr.get("late") or {}).get("n") or 0
    tail = f" · {skipped} stamped late, not scored" if skipped else ""
    if fparts:
        print(
            f"  forecast error    : {' · '.join(fparts)}  "
            f"(first poll, no answer in hand{tail})"
        )
    elif skipped:
        print(f"  forecast error    : — {skipped} step(s) stamped late, not scored")
    else:
        print("  forecast error    : — no step has started since the ledger was added")
    # ...and the rungs that are scored but never picked. Their own line, because it has to be
    # visible that the pane is carrying an experiment and that none of it moves a row on
    # screen: `recent` is judged here and can be picked by nothing.
    sparts = [f"{src} {fmt_rung(ferr[src])}" for src in SHADOW_RUNGS if ferr.get(src)]
    if sparts:
        print(f"  shadow rungs      : {' · '.join(sparts)}  (scored, never picked)")
    else:
        print("  shadow rungs      : — nothing scored yet (stamped into the vector, never picked)")
    # ...and whether a gap between two of those rungs is the rung or the sample. Only pairs
    # the log can actually judge are printed: a duel nobody has the steps for is not a draw,
    # and showing it as one would read as "these two rungs are equivalent".
    verdicts = duel_summaries(ledger_rows(log.get("tasks") or {}, now_est, model=model))
    if verdicts:
        for a_rung, b_rung, verdict in verdicts:
            print(f"  rung duel         : {duel_note(a_rung, b_rung, verdict)}")
    else:
        print("  rung duel         : — no pair of rungs has been scored on the same steps yet")
    # ...and whether there is yet enough of that to re-choose the constants from the log
    # rather than from a replay of the journals. Both counts are what the constants' own
    # comments say they need; the second is the one that governs the pace bound, because the
    # clip is consulted on the first few steps of every list and changes nothing on most.
    rr = refit_readiness(log.get("tasks") or {}, model=model)
    if not rr["scored"]:
        print("  refit readiness   : — no closed step carries a forecast yet")
    else:
        short = max(0, REFIT_MIN_SCORED - rr["scored"])
        head = f"{rr['scored']} closed step(s) scored"
        if short:
            head += f", {short} short of the {REFIT_MIN_SCORED} a median needs"
        else:
            head += f" — a median can be read from these ({REFIT_MIN_SCORED}+)"
        print(f"  refit readiness   : {head}")
        detail = (f"the bound was consulted on {rr['eligible']} of them and moved the number "
                  f"on {rr['decided']}")
        if rr["spans_needed"]:
            print(f"                      {detail}; judging the clip needs ~"
                  f"{REFIT_MIN_DECIDED} decided, which at this rate is ~{rr['spans_needed']} "
                  f"spans")
        elif rr["decided"]:
            print(f"                      {detail}; too few to say how many spans judging the "
                  f"clip would take")
        else:
            print(f"                      {detail} — the clip has not changed a single number "
                  f"here yet")
    # whose steps those are: the memory is per model, so a number without its model is
    # not auditable — a fast model remembered here must not project a slow one
    if model:
        print(f"  remembered from   : {model}")
    for t in [t for t in todos if not t.get("completed")][:5]:
        task = str(t.get("task", ""))
        bucket = step_shape_bucket(times, task)
        entry = sized_entry(shapes, bucket)
        samples = len(step_spans_ms(times, todos, now_est))
        blended = pending_blend_ms(task, pace, calls_mem)
        if entry:
            source = f"size {bucket}, {entry.get('n')} sample(s)"
        elif blended:
            source = f"label blend, {label_class(task)}"
        else:
            source = f"list pace, {samples} sample(s)" if samples else "list pace"
        spread = estimate_spread_ms(times, task, shapes, bucket) or pace_spread
        print(
            f"  estimate          : {_clip(task, 44)}  "
            f"{fmt_estimate_spread(estimate_for(times, task, pace, shapes, calls_mem), spread)}"
            f"  ({source})"
        )
    print(f"  scratch           : {scratch_size()} bytes in {SCRATCH}")
    if STATE_NOTE == "moved":
        print(f"  state             : {SCRATCH} (moved from {LEGACY_SCRATCH})")
    elif STATE_NOTE in ("live", "failed"):
        print(f"  state             : {SCRATCH} — "
              + ("a watcher or the keeper still holds it; stop them to finish the move to "
                 f"{STATE_DIR}" if STATE_NOTE == "live" else f"could not move to {STATE_DIR}"))
    return 0


def cmd_prune(args) -> int:
    report = prune_scratch(
        max_records=args.max_records,
        max_age_days=args.max_age_days,
        log_cap=(args.log_cap_kb * 1024) if args.log_cap_kb is not None else None,
        force=True,
    )
    if args.json:
        json.dump(
            {"scratch": SCRATCH, "scratch_bytes": scratch_size(), **report},
            sys.stdout,
            ensure_ascii=False,
        )
        sys.stdout.write("\n")
        return 0
    c = paint(use_color())
    trunc = report["log_truncated_bytes"]
    print(f"{c('1', 'fbtodo prune')}  {c('2', SCRATCH)}")
    print(f"  task records      : kept {report['records_kept']}, removed {report['records_removed']}")
    print(f"  temp files swept  : {report['temp_removed']}")
    print(f"  daemon log        : {'truncated ' + str(trunc) + ' bytes' if trunc else 'within cap'}")
    print(f"  scratch after     : {scratch_size()} bytes")
    return 0


# Which rungs are worth judging against each other, in the order a reader reads them. The
# three the pane can pick between, paired: the question "is this one better" only has a
# meaning between two rungs that were both asked the same steps.
DUEL_PAIRS = (("pace", "blend"), ("pace", "shape"), ("blend", "shape"),
              ("pace", "recent"))  # ...and the shadow rung against the one it would replace


def ledger_rows(
    tasks: dict,
    now_ms: int | None = None,
    days: float | None = None,
    model: str | None = None,
) -> list[dict]:
    """One row per step that carries a forecast vector, newest first.

    `fbtodo status` gives the SCOREBOARD; this gives the numbers it was computed from, which
    is the only way to see why a rung scores what it scores: the vector as it stood before the
    work started, the span it was scored against, and the factor error of each rung on it.
    A row carries no errors when there is nothing to score it against — the step is still
    running, its span is under the evidence floor, or the vector was stamped too late to be a
    forecast — and `why` says which of those it is rather than letting the row read as a miss.
    """
    now_ms = now_ms or int(time.time() * 1000)
    days = HISTORY_MAX_AGE_DAYS if days is None else days
    cutoff = now_ms - int(days * 86400 * 1000)
    rows: list[dict] = []
    for key, rec in (tasks or {}).items():
        if not isinstance(rec, dict):
            continue
        fc = rec.get("fc")
        if not isinstance(fc, dict):
            continue
        started, stopped = rec.get("started_ms"), rec.get("done_ms")
        if not started or started < cutoff:
            continue
        session, _, label = str(key).partition("\x1f")
        if model and (rec.get("model") or "") != model:
            continue
        span = (stopped - started) if (stopped and stopped > started) else None
        late = int(fc.get("late") or 0)
        # How far into the step the vector was written. A step that is ticked, unticked and
        # worked again restarts its clock while KEEPING the vector it earned on the first
        # sighting, so the stamp can read as negative — it was written before this run began,
        # which is the safest kind of forecast there is, but it is not what a reader assumes.
        stamp_in = (int(fc.get("at") or 0) - started) if fc.get("at") else None
        preds = {}
        for rung in SCORED_RUNGS:
            try:
                value = int(fc.get(rung) or 0)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                preds[rung] = value
        errs: dict[str, float] = {}
        why = ""
        if late:
            why = f"stamped {short_duration(late, seconds=True)} in — late, not scored"
        elif span is None:
            why = "no outcome yet"
        elif span < label_floor_ms():
            why = f"span under the {short_duration(label_floor_ms())} floor — not scored"
        else:
            errs = {r: round(max(p / span, span / p), 2) for r, p in preds.items()}
        ver = f"v{fc.get('v')}" if fc.get("v") else ""
        if late or stamp_in is None:
            # a late row's `why` already says when the stamp landed, so do not say it twice
            note = ver
        elif stamp_in < 0:
            note = f"{ver} · stamped {short_duration(-stamp_in, seconds=True)} before this run"
        elif stamp_in < 1000:
            # the normal case: the poll that started the clock wrote the vector on the same
            # pass, and `short_duration(0)` is an empty string — which read as "stamped  in"
            note = f"{ver} · stamped on the first poll"
        else:
            note = f"{ver} · stamped {short_duration(stamp_in, seconds=True)} in"
        rows.append({
            "session": session, "label": label, "model": rec.get("model") or "",
            "started_ms": started, "span_ms": span, "fc_at": int(fc.get("at") or 0),
            "v": fc.get("v") or "", "pick": fc.get("pick") or "",
            "preds": preds, "errors": errs, "late_ms": late, "stamp_in_ms": stamp_in,
            "scored": bool(errs), "why": why, "note": note,
        })
    rows.sort(key=lambda r: -r["started_ms"])
    return rows


def duel_summaries(rows: list[dict]) -> list[tuple]:
    """The rung pairs this set of ledger rows can actually judge, with their verdicts.

    Returned rather than printed so `doctor` and `fbtodo ledger` say the same sentence over
    the same evidence — and so the pair nobody has the steps for is left out of both instead
    of being reported as a draw. The rows are the whole window, not the page a `--limit`
    shows: the verdict is about the log, and truncating the display must not change it.
    """
    out = []
    for a_rung, b_rung in DUEL_PAIRS:
        verdict = rung_duel(rows, a_rung, b_rung)
        if verdict["steps"]:
            out.append((a_rung, b_rung, verdict))
    return out


def fmt_ledger(
    rows: list[dict],
    now_ms: int,
    days: float | None = None,
    limit: int = 20,
    color: bool = False,
    empty: str = "",
) -> str:
    """The `fbtodo ledger` report as text — split out from the command so a fixture can
    assert on what a reader would actually see, honesty notes included."""
    c = paint(color)
    days = HISTORY_MAX_AGE_DAYS if days is None else days
    out = [f"{c('1', 'fbtodo ledger')}  {c('2', TASKS_PATH)}"]
    if not rows:
        out.append(empty or (
            "  no step carries a forecast yet — one is written on the first poll "
            "that finds a step running, and scored when that step closes"
        ))
        return "\n".join(out)
    scored = sum(1 for r in rows if r["scored"])
    late = sum(1 for r in rows if r["late_ms"])
    out.append(f"  {len(rows)} step(s) with a forecast · {scored} scored · {late} stamped "
               f"late (not scored) · evidence floor {short_duration(label_floor_ms())} · "
               f"last {days:g}d")
    # ...and the verdict on the rungs those rows are evidence for, above them: the rows say
    # what each step predicted, and this says what the difference between two of them is worth
    for a_rung, b_rung, verdict in duel_summaries(rows):
        out.append(f"  rung duel: {duel_note(a_rung, b_rung, verdict)}")
    shown = rows if not limit else rows[:limit]
    for r in shown:
        span = short_duration(r["span_ms"], seconds=True) if r["span_ms"] else "—"
        label = r["label"] if len(r["label"]) <= 62 else r["label"][:61] + "…"
        out.append(f"  {span:<8} {label:<62} {c('2', fmt_age(r['started_ms'], now_ms))}")
        bits = []
        # the shipped rungs in their reading order, then any shadow rung the row actually
        # carries: a row recorded before the shadow rung existed is not shown a `recent —` it
        # never had, and a row that has one shows it beside the rungs it is competing with
        shown_rungs = list(SHIPPED_RUNGS) + [x for x in SHADOW_RUNGS if x in r["preds"]]
        for rung in shown_rungs:
            pred = r["preds"].get(rung)
            if pred is None:
                if rung != "pace":
                    bits.append(f"{rung} —")
                continue
            text = f"{rung} {short_duration(pred, seconds=True)}"
            miss = r["errors"].get(rung)
            if miss is not None:
                text += " ×%.2f" % miss
            bits.append(text)
        picked = f" · pick {r['pick']}" if r["pick"] else ""
        detail = " · ".join(x for x in (r["model"], r["note"], r["why"]) if x)
        suffix = f"  ({detail})" if detail else ""
        out.append(f"         {c('2', ' · '.join(bits) + picked)}{c('2', suffix)}")
    if len(shown) < len(rows):
        out.append(f"  … {len(rows) - len(shown)} more (--limit 0 for all, --days N to widen)")
    return "\n".join(out)


def cmd_ledger(args) -> int:
    """Every step's forecast vector, next to the outcome it was scored against.

    The ledger is the honest half of the scoreboard: each step's prediction is written once,
    on the first poll that saw it running, so the rungs are compared before anything about
    the step's size was known. `fbtodo status` prints the summary; this prints the rows,
    newest first, with each rung's factor error — and says when a row could not be scored
    rather than showing it as a miss. Read-only: it changes nothing. `--days N` (default 60),
    `--limit N` (default 20, 0 for all), `--model M`, `--json` for the same fields.
    """
    now_ms = int(time.time() * 1000)
    rows = ledger_rows(load_tasklog().get("tasks") or {}, now_ms, args.days, args.model)
    if args.json:
        json.dump({"at": now_ms, "count": len(rows), "ledger": rows}, sys.stdout,
                  ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    if args.quiet:
        return 0
    filters = []
    if args.model:
        filters.append(f"--model {args.model}")
    if args.days is not None:
        filters.append(f"--days {args.days:g}")
    empty = (f"  no step matches {' '.join(filters)} — drop the filter to see what there is"
             if filters else "")
    sys.stdout.write(
        fmt_ledger(rows, now_ms, args.days, args.limit, use_color(), empty) + "\n"
    )
    return 0


def cmd_pane(args) -> int:
    cwd = os.path.realpath(os.getcwd())
    if not args.no_daemon:
        ensure_daemon(args)
    if not sys.stdout.isatty() and not args.once:
        print("refusing to poll without a TTY (use `fbtodo snap`)", file=sys.stderr)
        return EX_CODES["usage"]

    color = use_color()
    hide = False
    saw_instance = False
    last_sig = None
    last_draw = 0.0
    last_frame = None
    tick = max(1.0, args.tick)
    stale_after_s = max(0.0, args.stale_after) * 60.0
    last_act = None
    last_activity = time.time()
    # Poll and paint run on separate clocks. A poll is expensive — on the NAS it is an
    # ssh round trip plus a remote probe — while a repaint is free, so the pane wakes
    # often enough to move its own numbers at 1Hz and only reaches for the store every
    # `args.interval`. Sleeping the whole gap is what made a NAS pane look stuck: its
    # clock and its "N ago" could not move until the next ssh had come back.
    WAKE = 0.25
    next_poll = 0.0
    state = watching = inst = None

    def restore(*_a):
        if hide:
            sys.stderr.write("\x1b[?25h")
            sys.stderr.flush()
        raise SystemExit(130)

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, restore)

    def draw(text: str) -> None:
        # Row by row against the last paint (see `pane_repaint`): the clock moves every tick and
        # only its own row changes, so a full clear-and-paint would flash the whole pane to move
        # one line — and would write kilobytes a second to move it.
        nonlocal last_frame
        frame = text.splitlines()
        rows = _shutil.get_terminal_size(fallback=(80, 24)).lines
        out = pane_repaint(last_frame, frame, rows)
        last_frame = frame
        if not out:
            return
        sys.stdout.write(out)
        sys.stdout.flush()

    try:
        if not args.once:
            sys.stdout.write("\x1b[?25l")
            sys.stdout.flush()
            hide = True
        while True:
            now = time.time()
            if state is None or now >= next_poll:
                remote = args.source == "nas"
                if remote:
                    inst = None  # decided from the state below: a live remote session, or nothing
                else:
                    inst, _ = find_instance(cwd, args.watch_pid, args.instance_of)
                st = read_json(STATE_PATH, None)
                if state_is_fresh(st, max(HEARTBEAT_GRACE, args.interval * 3)) and state_matches_request(st, args):
                    # Only this source's watcher state: a `-s nas` pane must not render the
                    # local CLI watcher's list just because that one happens to be fresh.
                    state = st
                    watching = st.get("daemon_pid") or daemon_pid()
                else:
                    state = adopt_version(
                        finish_state(snapshot(args, cwd=cwd, instance_pid=inst), None)
                    )
                    # No watcher is serving THIS request (that is why we snapshotted), so name
                    # one only if the cached state actually describes it: a `-s nas` pane was
                    # crediting the local CLI watcher for a remote session it never watched.
                    watching = daemon_pid() if (st and state_matches_request(st, args)) else None
                if remote:
                    # A live session on the NAS is the "instance" this pane follows; once it is gone
                    # the pane closes itself (and until one appears, it says it is waiting).
                    inst = True if state.get("instance_alive") else None
                if not state.get("error") and not state.get("task_times"):
                    # either no watcher, or one from before timings existed — track
                    # here so task clocks are never silently missing
                    state = track_tasks(state)
                next_poll = time.time() + max(0.2, args.interval)
            now = time.time()
            # “stale” means the *store* stopped moving, not that the list stopped:
            # the journal is appended every iteration while the agent works, so a
            # long single step keeps the pane alive while a finished or abandoned
            # session lets it expire.
            act = (
                state.get("store_mtime_ms"),
                state.get("iteration"),
                state.get("list_id"),
                state.get("done"),
                state.get("total"),
                state.get("session"),
                state.get("goal"),
                state.get("now"),
            )
            if act != last_act:
                last_act, last_activity = act, now
            idle_s = now - last_activity
            text = render(
                state,
                color,
                watching=watching,
                width=width_of_default(),
                idle_s=idle_s,
                stale_after_s=stale_after_s,
                goal_lines=args.goal_lines,
                height=_shutil.get_terminal_size(fallback=(80, 24)).lines,
            )
            if args.once:
                print(text)
                return 0
            # repaint when the *content* changes, and otherwise on a tick so the
            # clock in the footer keeps moving — a still pane must still look alive
            sig = (
                state.get("list_id"),
                state.get("list_version"),
                state.get("done"),
                state.get("total"),
                state.get("backend"),
                state.get("session"),
                state.get("goal"),
                state.get("now"),
                state.get("cleared"),
                state.get("error"),
                watching,
            )
            if sig != last_sig or now - last_draw >= (
                1.0 if has_running_clock(state, int(now * 1000)) else tick
            ):
                draw(text)
                last_sig, last_draw = sig, now
            if stale_after_s and idle_s >= stale_after_s:
                draw(text)
                print(c_stale_notice(color, idle_s, stale_after_s))
                return 0
            if inst is not None:
                saw_instance = True
            elif saw_instance and not args.wait:
                # the instance this pane was watching is gone: don't linger.
                # --wait is for a pane whose real lifetime is the shell that owns
                # it (an ssh into the remote host: freebuff can be run again in it).
                draw(text)
                print(c_notice(color))
                return 0
            time.sleep(min(WAKE, max(0.05, next_poll - time.time())))
    except KeyboardInterrupt:
        restore()
        return 0
    finally:
        if hide:
            sys.stderr.write("\x1b[?25h")
            sys.stderr.flush()


def c_notice(color: bool) -> str:
    return paint(color)("2", "freebuff instance exited — fbtodo pane closing")


def c_stale_notice(color: bool, idle_s: float, limit_s: float) -> str:
    return paint(color)(
        "2",
        f"todo list idle {short_duration((idle_s or 0) * 1000)} "
        f"(limit {short_duration(limit_s * 1000)}) — fbtodo pane closing",
    )


def cmd_snap(args) -> int:
    state = adopt_version(finish_state(snapshot(args), None))
    if not state.get("error"):
        state = track_tasks(state)
    print(render(state, use_color(), width=width_of_default(), goal_lines=args.goal_lines))
    return 0


def cmd_json(args) -> int:
    st = read_json(STATE_PATH, None)
    state = (
        st
        if state_is_fresh(st) and state_matches_request(st, args)
        else adopt_version(finish_state(snapshot(args), None))
    )
    if not state.get("error") and not state.get("task_times"):
        state = track_tasks(state)
    json.dump(state, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def cmd_bar(args) -> int:
    st = read_json(STATE_PATH, None)
    state = (
        st
        if state_is_fresh(st) and state_matches_request(st, args)
        else adopt_version(finish_state(snapshot(args), None))
    )
    print(bar_text(state))
    return 0


def nas_anchor_in_window(window: str | None, nas_host: str | None) -> str | None:
    """The ssh pane a NAS list in THIS window belongs under, when exactly one login is in it.

    Only panes already on this server are looked at — no ssh, no marker: a pin set at the
    keyboard has to be answered at keyboard speed, and the watcher does the authoritative
    placement while a session runs anyway. Several logins in one window is ambiguous (which
    one runs `fb` takes the far side's marker), so the pin is left to the watcher then.
    """
    if not window or not nas_host:
        return None
    rows, table = pane_rows(), process_table()
    here = [
        cand["pane"] for cand in ssh_session_candidates(nas_host, rows, table)
        if window_of(cand["pane"]) == window
    ]
    return here[0] if len(here) == 1 else None


def apply_pin(window: str, role: str = "both", nas_host: str | None = None) -> list[str]:
    """Put this window's list panes where the pin now says; their ids back.

    Run when a pin is set, so the effect is visible at once instead of on the next keeper
    pass, and with the same two halves the keeper and the NAS watcher enforce: the side the
    pane sits on, and the size it holds. `role` limits it to one of them — that is what
    `--role nas` is for, and a window holding both a session and an ssh has two panes.
    """
    rows, table = pane_rows(), process_table()
    rects = pane_rects()
    touched = []
    for one in (("local", "nas") if role == "both" else (role,)):
        layout = pane_layout(window, one)
        anchors = []
        if one == "local":
            for inst, win in session_windows(rows, table).items():
                if win != window:
                    continue
                inst_pane = freebuff_pane_id(inst, rows=rows, table=table)
                if inst_pane:
                    anchors.append((inst_pane, local_pane_ids(inst, rows, table)))
        else:
            anchor = nas_anchor_in_window(window, nas_host or NAS_HOST)
            if anchor:
                anchors.append(
                    (anchor, [pane for pane in nas_pane_ids() if window_of(pane) == window])
                )
        for anchor, panes in anchors:
            for pane in panes:
                if place_pane_beside(pane, anchor, rects, layout["side"]):
                    touched.append(pane)
                    rects = pane_rects()
                if resize_pane_to(pane, rects, layout["side"], layout["size"]):
                    touched.append(pane)
                    rects = pane_rects()
    return touched

def pane_reason(pane, role: str, window: str | None, anchor: str | None, rects: dict,
                layout: dict, anchor_note: str) -> dict:
    """What decided one list pane's place: the rule, the anchor, and the numbers.

    Four answers, always the same four, because that is what a person asks about a pane:
    which pane and role; where it should be (the anchor it is placed against, and the side
    that decides which edge of it); what side and size are in force and which source
    supplied them; and whether it is actually there.
    """
    here = rects.get(pane) if pane else None
    there = rects.get(anchor) if anchor else None
    # A pane in ANOTHER window than its session's is an arrangement the owner made, and both
    # the keeper and the watcher leave it alone — so a report that called it misplaced would
    # be sending somebody to fix what is not broken (`place_pane_beside`).
    elsewhere = bool(here and there and here["window"] != there["window"])
    placed = bool(pane and anchor) and placed_beside(anchor, pane, rects, layout["side"])
    return {
        "pane": pane,
        "role": role,
        "elsewhere": elsewhere,
        "window_id": window,
        "window": layout["window"] or (window or ""),
        "anchor": anchor,
        "anchor_note": anchor_note,
        "side": layout["side"],
        "side_source": layout["side_source"],
        "size": layout["size"],
        "size_source": layout["size_source"],
        "placed": placed,
        "now": (f"L{here['left']} T{here['top']} {here['width']}x{here['height']}" if here else ""),
        "expected": (f"below {anchor}" if layout["side"] == "v" else f"beside {anchor}")
        if anchor else "no pane to place it against",
    }


def why_records(pins: dict | None = None, last: dict | None = None) -> list[dict]:
    """One record per list pane on this server — and per session with no pane at all.

    The missing ones are records too (pane None): "why is there no list here" is the same
    question as "why is it there", and the answer is the same layout — the pane keeper has
    not opened it yet, at the side and size the sources below it say.
    """
    rows, table = pane_rows(), process_table()
    rects = pane_rects()
    pins = load_pins() if pins is None else pins
    last = load_last() if last is None else last
    records = []
    for inst, window in session_windows(rows, table).items():
        layout = pane_layout(window, "local", pins, last)
        anchor = freebuff_pane_id(inst, rows=rows, table=table)
        panes = local_pane_ids(inst, rows, table) or [None]
        for pane in panes:
            records.append(
                pane_reason(pane, "local", window, anchor, rects, layout, f"freebuff pid {inst}")
            )
    # The NAS half needs no ssh: the panes are on this server, and the ssh anchor is the
    # login already sitting here. WHICH login runs `fb` over there takes the marker on the
    # far side, so with several candidates the record says which rule the watcher applies.
    cands = ssh_session_candidates(NAS_HOST)
    nas_window = None
    nas_panes = nas_pane_ids()
    for pane in nas_panes:
        if nas_window is None:
            nas_window = window_of(pane)
            layout = pane_layout(nas_window, "nas", pins, last)
            if len(cands) == 1:
                anchor, note = cands[0]["pane"], f"the only ssh login to {NAS_HOST}"
            elif cands:
                anchor, note = None, (
                    f"{len(cands)} ssh logins ({', '.join(c['pane'] for c in cands)}) — "
                    "the watcher picks the newest that began before the session"
                )
            else:
                anchor, note = None, f"no ssh login to {NAS_HOST} on this server"
        records.append(pane_reason(pane, "nas", nas_window, anchor, rects, layout, note))
    return records


def cmd_why(args) -> int:
    """Say why every list pane is where it is — the rule, the anchor, and the numbers.

    The pane keeper moves panes and the NAS watcher opens them, and both answer to the same
    four things: the window's pin (that role's own, then the window's shared one), what the
    pane was left at last time, FBTODO_SPLIT / FBTODO_PANE_SIZE, and the built-in default.
    Which one won is invisible in a layout that merely looks right — and the first question
    when one is wrong — so this prints, per pane, the side and size in force WITH the source
    that supplied them, the pane it is placed against, and whether it is actually there.
    `--window TARGET` limits it to one window; `--json` is the same thing for a script.
    """
    pins, last = load_pins(), load_last()
    records = why_records(pins, last)
    asked = (getattr(args, "window", None) or "").strip()
    if asked:
        want = window_of(asked)
        if not want:
            print(f"fbtodo why: no such window: {asked}", file=sys.stderr)
            return EX_CODES["nofile"]
        records = [rec for rec in records if rec["window_id"] == want]
    if args.json:
        json.dump(
            {
                "panes": records,
                "pins": pins,
                "last": last,
                "knobs": {
                    "FBTODO_SPLIT": os.environ.get("FBTODO_SPLIT") or None,
                    "FBTODO_PANE_SIZE": os.environ.get("FBTODO_PANE_SIZE") or None,
                },
            },
            sys.stdout, ensure_ascii=False,
        )
        sys.stdout.write("\n")
        return 0
    if args.quiet:
        return 0
    if not records:
        print("no list pane, and no session to put one beside")
        return 0
    for rec in records:
        rule = (
            f"side={rec['side']} ({source_note(rec['side_source'], rec['window'])})"
            f"  size={rec['size']} ({source_note(rec['size_source'], rec['window'])})"
        )
        if not rec["pane"]:
            tail = "no list pane yet — a keeper pass opens it there"
        elif rec["elsewhere"]:
            tail = f"in another window than its session — left alone, by design  {rec['now']}"
        else:
            tail = rec["expected"] + (
                ", placed" if rec["placed"] else ", MISPLACED (a pass moves it back)"
            )
            tail += f"  {rec['now']}"
        print(f"{rec['window']:<12} {rec['role']:<5} {rec['pane'] or '(none)':<6} {rule}")
        print(f"{'':<12} {'':<5} {'':<6} {tail} — {rec['anchor_note']}")
    bad = [
        rec["pane"] for rec in records
        if rec["pane"] and not rec["placed"] and not rec["elsewhere"]
    ]
    print(
        f"{len(records)} list pane(s), {len(bad)} misplaced"
        + (f": {' '.join(bad)} — tell the keeper to move them: fbtodo pane-watch --once" if bad else "")
    )
    return 0


def cmd_pin(args) -> int:
    """Pin the list pane's side or size, per window.

    The pane follows its session, but a window can have a mind of its own about where that
    is: a wide window reads better with the list beside the session, a tall one with it
    below. `fbtodo pin --side h --size 16` run in the window you mean (or with `--window`
    naming another, `main:2` or `@71`) files that under `<session>:<index>` in
    `fbtodo-pins.json` in the state directory, and the pane keeper enforces both halves — the side it
    splits on and puts a drifted pane back on, and the size it resizes back to.

    `--role` says which list pane it is for: `local` (a session's), `nas` (the one a NAS
    ssh pulls in), or `both` (the default) for the window's shared answer. A window holding
    both needs the distinction — the two panes need not be the same size — so the role's own
    pin outranks the shared one for that pane, and only that one.

    Bare `fbtodo pin` reports what this window is set to, per role, and where each half came
    from (a pin, the remembered size, FBTODO_SPLIT/FBTODO_PANE_SIZE, or the default);
    `--list` reports every pin; `--clear` drops the window's pin whole; `--side auto` and
    `--size 0` drop one half of it.
    """
    pins = load_pins()
    if args.list:
        if args.json:
            json.dump(pins, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
            return 0
        if not pins:
            print("no pins (set one with: fbtodo pin --side h --size 16)")
            return 0
        for key in sorted(pins):
            entry = pins[key] if isinstance(pins[key], dict) else {}
            state = "live" if window_key(key) else "no such window"
            shared = [f"{f}={entry[f]}" for f in ("side", "size") if entry.get(f)]
            print(f"{key:<16} {'both':<6} {' '.join(shared) or '-':<16} {state}")
            for one in ("local", "nas"):
                sub = entry.get(one)
                sub = sub if isinstance(sub, dict) else {}
                got = [f"{f}={sub[f]}" for f in ("side", "size") if sub.get(f)]
                if got:
                    print(f"{key:<16} {one:<6} {' '.join(got):<16} {state}")
        return 0
    window = (getattr(args, "window", None) or "").strip()
    if not window:
        out = tmux_run("display-message", "-p", "#{window_id}")
        window = (out or "").strip()
    key = window_key(window)
    if not key:
        print(
            "fbtodo pin: no tmux window to pin (run it inside a pane, or name one with "
            "--window main:2)",
            file=sys.stderr,
        )
        return EX_CODES["nofile"]
    entry = dict(pins.get(key)) if isinstance(pins.get(key), dict) else {}
    role = getattr(args, "role", None) or "both"
    if args.clear:
        had = key in pins
        pins.pop(key, None)
        save_pins(pins)
        apply_pin(window, nas_host=args.nas_host)
        if not args.quiet:
            print(f"{key}: {'pin cleared' if had else 'nothing was pinned'}")
        return 0
    asked = args.side is not None or args.size is not None
    # `both` writes the window's shared halves — what every pin written before there was a
    # role is; `local`/`nas` write that role's own, which outranks the shared one for it.
    target = entry if role == "both" else dict(entry.get(role) or {})
    if getattr(args, "side", None):
        if args.side == "auto":
            target.pop("side", None)
        elif args.side in ("v", "h"):
            target["side"] = args.side
        else:
            print("fbtodo pin: --side takes v, h or auto", file=sys.stderr)
            return EX_CODES["usage"]
    if args.size is not None:
        if args.size <= 0:
            target.pop("size", None)
        else:
            target["size"] = args.size
    if role != "both":
        if target:
            entry[role] = target
        else:
            entry.pop(role, None)
    if asked:
        if entry:
            pins[key] = entry
        else:
            pins.pop(key, None)
        save_pins(pins)
    roles = ("local", "nas") if role == "both" else (role,)
    layouts = {one: pane_layout(window, one, pins) for one in roles}
    touched = list(dict.fromkeys(apply_pin(window, role, args.nas_host))) if asked else []
    first = layouts["local" if "local" in layouts else roles[0]]
    if args.json:
        json.dump(
            {
                "window": key, "role": role, "pin": entry, "layouts": layouts,
                "side": first["side"], "size": first["size"], "panes": touched,
            },
            sys.stdout, ensure_ascii=False,
        )
        sys.stdout.write("\n")
        return 0
    if args.quiet:
        return 0
    lines = [f"{key}  ({'pinned' if entry else 'nothing pinned'})"]
    for one, layout in layouts.items():
        lines.append(
            f"  {one:<5} side={layout['side']}"
            f" ({source_note(layout['side_source'], key)})"
            f"  size={layout['size']}"
            f" ({source_note(layout['size_source'], key)})"
        )
    if touched:
        lines.append(f"  list pane {', '.join(touched)} re-placed")
    print("\n".join(lines))
    return 0


# ======================================================================== main
def build_parser():
    ap = argparse.ArgumentParser(
        prog="fbtodo",
        description="Watch the Freebuff agent's todo list.",
        add_help=False,
    )
    ap.add_argument("command", nargs="?", default="pane",
                    choices=["pane", "snap", "json", "bar", "daemon", "stop", "status",
                             "prune", "nas", "pane-watch", "pin", "why", "ledger", "doctor"])
    ap.add_argument(
        "--label-floor", type=float, default=None, metavar="SEC",
        help="estimates: a finished span under SEC is not evidence — it sets no pace and "
             "moves no memory (0 = keep every span; FBTODO_LABEL_FLOOR)",
    )
    ap.add_argument(
        "--blend-weight", type=float, default=None, metavar="W",
        help="estimates: 0..1, how much a WAITING step's wording counts against the list's "
             "pace (0 = pace only, 1 = wording only; FBTODO_BLEND_WEIGHT)",
    )
    ap.add_argument("--days", type=float, metavar="N",
                    help="ledger: how far back to list (default 60, the retention window)")
    ap.add_argument("--limit", type=int, default=20, metavar="N",
                    help="ledger: rows to print, newest first (default 20, 0 = all)")
    ap.add_argument("--model", metavar="NAME", help="ledger: only this model's steps")
    ap.add_argument("--max-records", type=int, help="prune: cap on kept task records")
    ap.add_argument("--max-age-days", type=float, help="prune: drop records older than this")
    ap.add_argument("--log-cap-kb", type=int, help="prune: truncate the daemon log above this")
    ap.add_argument("-h", "--help", action="store_true")
    ap.add_argument("-V", "--version", action="store_true")
    ap.add_argument("--once", action="store_true", help="pane: render once and exit")
    ap.add_argument("--tick", type=float, default=5.0,
                    help="pane: repaint at least this often while nothing moves "
                         "(1s while a step's clock is counting up, 1s for a store "
                         "that is still being written)")
    ap.add_argument("--stale-after", type=float, default=60.0, metavar="MIN",
                    help="pane: close after MIN minutes of no store activity (0 = never)")
    ap.add_argument("-i", "--interval", type=float, default=1.0,
                    help="pane: seconds between polls (the clock repaints far more "
                         "often than this; 5s for -s nas)")
    ap.add_argument("-s", "--source", default="auto", choices=["auto", "cli", "nas", "desktop"])
    ap.add_argument(
        "--nas-host", default=NAS_HOST, metavar="USER@HOST",
        help="nas: ssh target holding the freebuff store (FBTODO_NAS; required for -s nas)",
    )
    ap.add_argument(
        "--nas-root", default=NAS_ROOT, metavar="DIR",
        help="nas: remote projects directory (FBTODO_NAS_ROOT; required for -s nas)",
    )
    ap.add_argument(
        "--nas-project", default=NAS_PROJECT, metavar="NAME",
        help="nas: project name inside that directory (FBTODO_NAS_PROJECT; required for -s nas)",
    )
    ap.add_argument(
        "--fb-marker",
        default=os.environ.get("FBTODO_FB_MARKER") or "$HOME/.fb-session",
        help="nas: the host `fb` wrapper's session marker (pid, started, dir), read on the NAS",
    )
    ap.add_argument(
        "--wait",
        action="store_true",
        help="pane: stay open when the session goes away (a remote pane is closed with its ssh)",
    )
    ap.add_argument(
        "--goal-lines",
        type=int,
        default=3,
        metavar="N",
        help="pane: lines for the goal heading the list (0 hides it)",
    )
    ap.add_argument("--chat")
    ap.add_argument("--cli-root", default=DEFAULT_CLI_ROOT)
    ap.add_argument("-p", "--project")
    ap.add_argument("--db", default=DEFAULT_DB_GLOB)
    ap.add_argument("--state", default=DEFAULT_WORKSPACE_STATE)
    ap.add_argument("-t", "--thread")
    ap.add_argument("--last", action="store_true")
    ap.add_argument("-A", "--follow-app", action="store_true")
    ap.add_argument("-f", "--foreground", action="store_true", help="daemon: don't detach")
    ap.add_argument("--force", action="store_true", help="daemon: start even if one runs")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--json", action="store_true", help="status: JSON output")
    ap.add_argument("--no-daemon", action="store_true", help="pane: read stores directly")
    # --- the NAS pane watcher (`nas`): a local daemon because `fb` over there cannot
    # reach this Mac's tmux, so something here has to notice the session starting.
    ap.add_argument("--stop", action="store_true", help="nas: stop the pane watcher")
    ap.add_argument("--status", action="store_true", help="nas: the watcher, the session, and the pane")
    ap.add_argument("--poll", type=float,
                    default=float(os.environ.get("FBTODO_NAS_POLL") or 5.0),
                    help="nas: seconds between polls while you are at the keyboard")
    ap.add_argument("--poll-idle", type=float,
                    default=float(os.environ.get("FBTODO_NAS_POLL_IDLE") or 60.0),
                    help="nas: seconds between polls when no tmux client is attached")

    ap.add_argument("--poll-live", type=float, default=2.5,
                    help="nas: seconds between polls while a NAS session runs")
    ap.add_argument(
        "--notify-seconds", type=float,
        default=float(os.environ.get("FBTODO_NOTIFY_SECONDS") or 15.0),
        help="nas: ask the phone notifier this often while a session runs (0 = never; "
             "it is skipped when ~/.config/freebuff-notify/todo-bell.py is missing)",
    )
    ap.add_argument(
        "--pause-seconds", type=float,
        default=float(os.environ.get("FBTODO_PAUSE_SECONDS") or 30.0),
        help="watcher: ask the stall watch this often whether the session has stopped "
             "mid-task, local sessions only (0 = never; it is skipped when "
             "~/.config/freebuff-notify/pause-bell.py is missing)",
    )
    ap.add_argument(
        "--pane-seconds", type=float,
        default=float(os.environ.get("FBTODO_PANE_SECONDS") or 3.0),
        help="watcher: this often, put the local todo pane back if it was killed while "
             "the session runs (0 = never; FBTODO_NO_PANE=1 disables it too)",
    )
    ap.add_argument(
        "--pane-bell-seconds", type=float,
        default=float(os.environ.get("FBTODO_PANE_BELL_SECONDS") or 60.0),
        help="watcher: ask the pane watch this often whether a session has no todo pane "
             "or the keeper is gone (0 = never; it is skipped when "
             "~/.config/freebuff-notify/pane-bell.py is missing)",
    )
    ap.add_argument(
        "--ask-seconds", type=float,
        default=float(os.environ.get("FBTODO_ASK_SECONDS") or 3.0),
        help="watcher: look for a freebuff pane waiting on a question this often, "
             "local and NAS alike (0 = never; it is skipped when "
             "~/.config/freebuff-notify/ask-bell.py is missing)",
    )
    # --- per-window pins (`pin`): where the list pane goes in THIS window, and how big
    ap.add_argument("--side", choices=["v", "h", "auto"],
                    help="pin: v puts the list below the session, h beside it, auto unpins the side")
    ap.add_argument("--size", type=int, metavar="N",
                    help="pin: keep the list pane N lines (0 unpins the size)")
    ap.add_argument("--role", choices=["local", "nas", "both"], default="both",
                    help="pin: which list pane the pin is for — a session's (`local`), the "
                         "one a NAS ssh pulls in (`nas`), or the window's shared answer "
                         "for any of them (`both`, the default)")
    ap.add_argument("--window", metavar="TARGET",
                    help="pin, why: the tmux window to act on (default: the pane you are in)")
    ap.add_argument("--list", action="store_true", help="pin: every pin, and whether its window exists")
    ap.add_argument("--clear", action="store_true", help="pin: drop this window's pin")
    ap.add_argument("--dry-run", action="store_true", help="nas: say what it would do, change nothing")
    ap.add_argument("--idle-exit", type=float, default=30.0, metavar="MIN",
                    help="nas: quit after MIN of no session and no client (0 = run forever)")
    ap.add_argument("--instance-pid", type=int, help="daemon: watch this pid (internal)")
    ap.add_argument("--watch-pid", type=int, help="treat this pid as the instance (internal)")
    ap.add_argument("--instance-of", type=int, metavar="PID",
                    help="follow the freebuff process launched by PID (the shell that started it)")
    ap.add_argument("--cwd", help="daemon: directory to serve")
    return ap


def main(argv=None) -> int:
    ap = build_parser()
    try:
        args, extra = ap.parse_known_args(argv)
    except SystemExit:
        return EX_CODES["usage"]
    if extra:
        print(f"unexpected arguments: {' '.join(extra)}", file=sys.stderr)
        return EX_CODES["usage"]
    if args.help:
        print(__doc__)
        return 0
    if args.version:
        print(VERSION)
        return 0
    # The two estimate knobs, applied through the one table every reader asks (see
    # `set_estimate_knobs`): the env vars are read at import, and a flag beats them, as the
    # precedence promises.
    set_estimate_knobs(args.label_floor, args.blend_weight)
    if args.source == "nas" and args.interval <= 1.0:
        # One ssh per poll, and the pane no longer needs a poll to move its own numbers:
        # the clock and the "N ago" repaint locally (see the pane loop), so the store is
        # reached every 5s — half the ssh traffic of the old 2.5s — while the pane still
        # looks alive every second.
        args.interval = 5.0
    os.makedirs(SCRATCH, mode=0o700, exist_ok=True)

    if args.command == "pane":
        return cmd_pane(args)
    if args.command == "snap":
        return cmd_snap(args)
    if args.command == "json":
        return cmd_json(args)
    if args.command == "bar":
        return cmd_bar(args)
    if args.command == "daemon":
        return cmd_daemon(args)
    if args.command == "stop":
        return cmd_stop(args)
    if args.command == "status":
        return cmd_status(args)
    if args.command == "doctor":
        return cmd_doctor(args)
    if args.command == "prune":
        return cmd_prune(args)
    if args.command == "ledger":
        return cmd_ledger(args)
    if args.command == "nas":
        return cmd_nas(args)
    if args.command == "pane-watch":
        return cmd_pane_watch(args)
    if args.command == "pin":
        return cmd_pin(args)
    if args.command == "why":
        return cmd_why(args)
    return EX_CODES["usage"]


if __name__ == "__main__":
    sys.exit(main())
