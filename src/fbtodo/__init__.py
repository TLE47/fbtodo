"""fbtodo — watch the Freebuff agent's todo list.

    fbtodo              live pane (default); auto-starts the watcher, exits with freebuff.
                        m quiet until this list finishes · M quiet until you say · u loud
                        again (the phone/ntfy switch; --no-keys to ignore the keyboard);
                        n the push alone, or click the button on the status row
                        (--no-mouse, FBTODO_PANE_MOUSE=off, keeps the mouse for selecting)
    fbtodo snap         one snapshot, plain text
    fbtodo json         one snapshot, clean JSON
    fbtodo bar          "todos 3/5" — for a tmux status bar
    fbtodo init          write the `fb` launcher into your shell startup file
    fbtodo daemon       start the background watcher (-f for foreground)
    fbtodo stop         stop the watcher
    fbtodo keep         turn the pane-repair knob off/on: `keep off` for this pane — kept
                        as it is, still diagnosed and told — `keep on` back, `keep
                        default` to inherit again (`--pane %ID`, `--window TARGET`,
                        `--server`; no verb prints what is in force)
    fbtodo locks        audit every claim file — who holds it, whether the lock and the
                        name are one file, whether a record is stale, and what clearing
                        it would take (--json; the audit itself never writes; --fix clears
                        dead records and ends untied role processes, asking before any
                        kill; --restart re-claims the watcher and keeper through the
                        normal ask afterwards; --dry-run plans without changing anything)
    fbtodo pane-watch   keep the todo panes open, for as long as freebuff runs
                        (started for you; --once for one pass)
    fbtodo mute         the pane's phone/ntfy switch, without a keystroke: list (or no
                        verb) reports it, on mutes until told otherwise, off gives the
                        notifications back, until-done mutes until this list finishes
                        (the same switch the pane's m/M/u keys write; --json)
    fbtodo pin          pin the list pane's side or size for one window
                        (--side v|h|auto, --size N, --window TARGET, --list, --clear)
    fbtodo why          why each list pane is where it is: the side and size in force,
                        the source that supplied them, the pane it sits beside
                        (--window TARGET, --json)
    fbtodo panes        every todo pane on this machine: its pane and window (its tty
                        outside tmux), the subject it holds and the interpreter it runs
                        (--json)
    fbtodo board        every live session on this machine in one frame: its list, its
                        heading and its clock, most recently active first (--live keeps
                        redrawing it; --json for a script; --board-max N, --board-live
                        MIN, --board-rows N)
    fbtodo dead         every stretch of code this build's own source proves can never
                        run — all of them, not the first the reload probe refuses on
                        (--json); exits nonzero when it finds any
    fbtodo status       instance, watcher, state-file and scratch footprint
                        (--watch reports only keeper changes; --json for a script)
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
in — the freebuff pane itself — since that is where a glance looks for it.
`--pane-seconds N` sets how often it looks (3s), `--ask-seconds
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
writes (`~/.config/freebuff-patch-watch/watch.log` and `~/.config/freebuff-notify/phone.log`),
so neither has to be fetched with a probe of its own; a state with neither fact renders
exactly as before. `fbtodo status` prints the same pair in the same words.

Two stores, both on this machine:
    CLI        ~/.config/manicode/projects/<project>/chats/<ISO>/log.jsonl   live, mid-turn
    Desktop    ~/.config/freebuff-desktop/projects/<slug>/desktop-v2.db      per turn, app only
`--source auto` prefers a CLI chat for the cwd. There is no remote source: a list is read
where it was written, and the pane follows the freebuff PROCESS here that is writing it.

Every new list replaces the old one: state is never merged, and when the session
changes the previous list is dropped immediately instead of lingering.

Each list is headed by its big goal: the agent's own one-line `Goal:` statement, which
AGENTS.md asks for and the journal keeps in `fullResponse`. The request the list was
written for is never shown as the goal — a quote is the phrasing, not the objective — so
a session that skips the line is told so loudly: `big goal · no heading`, naming the
`Goal:` line the agent owes, in the warn yellow and in both renderers — the plain one says
it where the heading would be, and the framed one draws the same note on that row rather
than nothing at all. When a newer
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
from .tasks import *  # noqa: F401,F403 — the package is one namespace
from .board import *  # noqa: F401,F403 — the package is one namespace
from .fb_init import *  # noqa: F401,F403 — the package is one namespace
from .mute import *  # noqa: F401,F403 — the package is one namespace


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
        for gone in ("goal", "goal_source", "goal_stale"):
            state[gone] = None
        # ...and its CLOCKS go too. `task_times` is per-step and belongs to the list it was
        # measured on; left standing, a cleared state keeps the dropped steps' timings, and the
        # pane — which rebuilds them only when they are MISSING (`if not state.get("task_times")`)
        # — never rebuilt them for the next list written into the same session. Emptying it also
        # forces that rebuild, so the new list's clocks are measured rather than inherited. The
        # cross-session memories (`task_history`, `task_shapes`, `task_calls`) deliberately stay:
        # those are what a NEW list projects from, and the task log keeps the finished records
        # regardless of what the state carries.
        state["task_times"] = {}
    fp = list_fingerprint(state)
    version = int((prev or {}).get("list_version") or 0)
    session_changed = bool(prev) and prev.get("session") != state.get("session")
    list_changed = bool(prev) and prev.get("list_id") != fp
    cleared = False

    if session_changed:
        # A new session owns the pane now: its list is not the previous one, and
        # the old list must not linger while the new one is being written. With no list of
        # its own yet, the previous session's clocks have nothing left to describe, so they
        # go with it — one session's timings must never be read against another's steps.
        version = 0
        if not todos:
            cleared = True
            state["task_times"] = {}
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


def finish_probe(state: dict) -> dict:
    """`finish_state` for a one-shot probe, which may be reading a FILE.

    A probe that read a file has that file's own list identity in hand, so the file is
    compared with itself: renumbering it would let a recorded state say `#7` and the pane
    print `#0`. A probe that read a journal or a database has no history and passes None,
    borrowing the counter from the watcher (`adopt_version`).
    """
    if state.get("backend") == "file":
        # The file states its own list number, so the comparison is the file with itself and
        # nothing moves: the number it recorded is the number the pane prints, and a file that
        # recorded none starts at the first (`1`), not at a borrowed watcher's counter.
        recorded = state.get("list_version")
        state["list_version"] = int(recorded) if recorded is not None else (
            1 if state.get("todos") else 0)
        state["list_id"] = list_fingerprint(state)
        return finish_state(state, dict(state))
    return adopt_version(finish_state(state, None))


# ==================================================================== push
# In plain words: the generic source. A journal and a database are the two ways THIS machine
# finds a list; anything else — another agent, a script, a CI job, a hand written file — can
# say what the list is in one JSON object. `fbtodo push` takes one on
# stdin and makes it the live state, and `-s file:PATH` reads the same shape back. Neither
# knows what a Freebuff is.
PUSH_FIELDS = (
    "session", "title", "summary", "goal", "goal_source", "now", "nudge", "todos",
    "model", "turn_ended", "iteration", "list_id", "list_version", "ts",
)

# The ingress caps, shared by every path that turns untrusted JSON into state — `fbtodo
# push` on stdin and `-s file:PATH` off disk. They are limits, not truncations: a payload
# over one is refused with the data error (65) and the state it would have replaced stands.
# 1 MiB of stdin is far past any real list; 200 steps is past anything a session writes;
# 500 characters is past any goal or step name, and a task is a STRING — coercing a number
# or an object with `str()` was how a dict of junk reached a step row.
PUSH_MAX_BYTES = 1 << 20
PUSH_MAX_STEPS = 200
PUSH_MAX_STRING = 500


def check_ingress(data) -> None:
    """Refuse a payload that breaks an ingress cap. Raises ValueError; writes nothing.

    Shared by `state_from_push` and the `file:PATH` source, so the two doorways answer to
    one contract rather than each having its own idea of "too big".
    """
    if not isinstance(data, dict):
        raise ValueError("stdin must be a JSON object, not an array or a scalar")
    todos = data.get("todos")
    if todos is None:
        todos = []
    if not isinstance(todos, list):
        raise ValueError("todos must be a list")
    if len(todos) > PUSH_MAX_STEPS:
        raise ValueError(f"too many steps: {len(todos)} (limit {PUSH_MAX_STEPS})")
    for t in todos:
        if not isinstance(t, dict):
            raise ValueError("every todo must be an object")
        task = t.get("task")
        if not isinstance(task, str):
            raise ValueError("a todo's task must be a string")
        if len(task) > PUSH_MAX_STRING:
            raise ValueError(f"a todo's task is longer than {PUSH_MAX_STRING} characters")
        if not task.strip():
            raise ValueError("a todo with no task text")
        done = t.get("completed")
        # Absent (or an explicit null) means "not done"; anything present must be a real
        # boolean. The coercion `bool(value)` counted the STRING "false" as a finished step,
        # so a pusher that sent a string got its list inverted, not refused.
        if done is not None and not isinstance(done, bool):
            raise ValueError("a todo's completed must be true or false")
    for key in PUSH_FIELDS:
        value = data.get(key)
        if isinstance(value, str) and len(value) > PUSH_MAX_STRING:
            raise ValueError(f"{key} is longer than {PUSH_MAX_STRING} characters")


def check_file_ingress(path: str) -> int | None:
    """The `file:PATH` doorway to `check_ingress`, refused the way `push` refuses a payload.

    The file is an ingress like stdin, so it gets the same byte cap before it is parsed (a
    1 MiB file cannot make the reader allocate the denial-of-service it was capped against)
    and a recursive payload gets the same data error the parser's `RecursionError` would
    otherwise turn into "no state file". A simply absent file is not an error here — that is
    the source's own "no state file" answer — so it returns None and lets the source speak.
    Returns an exit code to stop on, or None when the file is fine or missing.
    """
    try:
        with open(path, "rb") as fh:
            chunk = fh.read(PUSH_MAX_BYTES + 1)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        print(f"fbtodo: {path}: {exc}", file=sys.stderr)
        return EX_CODES["ioerr"]
    if len(chunk) > PUSH_MAX_BYTES:
        print(f"fbtodo: {path}: larger than {PUSH_MAX_BYTES} bytes — refused", file=sys.stderr)
        return EX_CODES["dataerr"]
    try:
        data = json.loads(chunk.decode("utf-8", "replace"))
    except RecursionError:
        print(f"fbtodo: {path}: too deeply nested to parse", file=sys.stderr)
        return EX_CODES["dataerr"]
    except ValueError as exc:
        print(f"fbtodo: {path}: not JSON: {exc}", file=sys.stderr)
        return EX_CODES["dataerr"]
    try:
        check_ingress(data)
    except ValueError as exc:
        print(f"fbtodo: {path}: {exc}", file=sys.stderr)
        return EX_CODES["dataerr"]
    return None


def state_from_push(data: dict) -> dict:
    """A pushed JSON object as a state the rest of the tool can read.

    Only the fields a list is MADE of are taken — the steps, the goal, the session, and the
    identity a pusher may carry over (`list_id`/`list_version`) — and everything else is
    stamped here: the pusher does not need to know the tool's clock fields, and a pusher that
    sent the whole object `fbtodo json` printed is not punished for the extra keys. What it
    WILL refuse is a payload over the ingress caps (`check_ingress`), before anything is
    built from it.
    """
    check_ingress(data)
    todos = data.get("todos") or []
    steps = [{"task": t["task"].strip(), "completed": bool(t.get("completed"))}
             for t in todos]
    now = int(time.time() * 1000)
    st = {k: data[k] for k in PUSH_FIELDS if k in data}
    st["todos"] = steps
    st.update({
        "ts": int(st.get("ts") or now),
        "backend": "push",
        "source": "push",
        "status": "watching",
        "tool_version": VERSION,
        "heartbeat_ms": now,
        "probed_ms": now,
        "source_updated_ms": int(st.get("source_updated_ms") or st.get("ts") or now),
    })
    # The pusher's prose is filtered here like any other source's: the state file is read by
    # `json` and `status` too, so cleaning only at render would leave them exposed.
    return clean_observation(st)


def cmd_push(args) -> int:
    """Make a state JSON on stdin the live state, so anything can drive the pane.

    The pushed object is normalized to the fields a list is made of and put through the same
    `finish_state` a watched list goes through, so its counts, its session and its list number
    are the tool's rather than the pusher's — a pusher that says `list_version: 9` gets 9 only
    if it is the same list the state already held, and one that says nothing gets 1. `--to
    PATH` writes that file instead of the live state (which is how the demo does not touch
    yours), `--dry-run` writes nothing at all, `--quiet` prints nothing, and anything that is
    not an object with a list of steps is refused with the state left alone.
    """
    # Bounded read: one byte past the cap is enough to know it was broken, and reading the
    # whole thing first is exactly the denial-of-service the cap exists to refuse.
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    chunk = stream.read(PUSH_MAX_BYTES + 1)
    if isinstance(chunk, bytes):
        oversize, raw = len(chunk) > PUSH_MAX_BYTES, chunk.decode("utf-8", "replace")
    else:
        oversize, raw = len(chunk) > PUSH_MAX_BYTES, chunk
    if oversize:
        print(f"fbtodo push: stdin is larger than {PUSH_MAX_BYTES} bytes — refused",
              file=sys.stderr)
        return EX_CODES["dataerr"]
    if not raw.strip():
        print("fbtodo push: nothing on stdin — expected a state JSON (see docs/SOURCES.md)",
              file=sys.stderr)
        return EX_CODES["noinput"]
    try:
        data = json.loads(raw)
    except RecursionError:
        # A pathological run of `[`/`{` recurses the parser past its limit; that is a
        # malformed payload, so it is the same data error as any other bad JSON.
        print("fbtodo push: stdin is too deeply nested to parse", file=sys.stderr)
        return EX_CODES["dataerr"]
    except ValueError as exc:
        print(f"fbtodo push: stdin is not JSON: {exc}", file=sys.stderr)
        return EX_CODES["dataerr"]
    if not isinstance(data, dict):
        print("fbtodo push: stdin must be a JSON object, not an array or a scalar",
              file=sys.stderr)
        return EX_CODES["dataerr"]
    try:
        state = state_from_push(data)
    except ValueError as exc:
        print(f"fbtodo push: {exc}", file=sys.stderr)
        return EX_CODES["dataerr"]
    target = getattr(args, "push_to", None) or STATE_PATH
    state = finish_state(state, read_json(target, None), claim_first=True)
    if not args.dry_run:
        atomic_write_json(target, state)
    if not args.quiet:
        json.dump(state, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    return 0


def state_matches_request(state: dict | None, args) -> bool:
    """Is the cached watcher state an answer to THIS question?

    `auto` takes whatever is live, but an explicit `-s cli` must not be answered by a
    state file describing the desktop app's session. The
    producer's version is checked for the same reason: a watcher left running across an
    upgrade keeps writing the old layout, and then a pane that trusts it silently loses
    every field the new one added (that is how a goal line would go missing).
    """
    if not state:
        return False
    if state.get("tool_version") != VERSION:
        return False
    source = source_kind(getattr(args, "source", "auto"))
    if source != "auto" and (state.get("backend") or "") != source:
        return False
    # ...and it has to be about the SUBJECT this caller named. A watcher is ONE process
    # serving whoever asks first, so its file can hold another chat's or another thread's
    # list; a pane that latched to one subject and then drew the watcher's answer to a
    # different question is "one pane, two threads" — the lock held in the arguments while
    # the frame came from somewhere else. `--chat` is the CLI's chat directory and
    # `--thread` the desktop thread's id, which is exactly how each state names its subject
    # (`target`, `session`), so the comparison is the state's own words.
    wanted_thread = getattr(args, "thread", None)
    wanted_chat = getattr(args, "chat", None)
    if wanted_thread:
        if state.get("session") != wanted_thread:
            return False
    elif wanted_chat:
        target = str(state.get("target") or "")
        if not target or os.path.realpath(target) != os.path.realpath(
                os.path.expanduser(str(wanted_chat))):
            return False
    # ...and the stack has to be the size that was asked for. A pane asks for ONE list
    # (`cmd_pane` sets `--threads 1`) while the watcher it shares with `fbtodo json --threads
    # 4` writes four of them: without this the pane drew every live thread of the store under
    # one frame, which is the same report in its other shape.
    if int(getattr(args, "threads", 0) or 0) <= 1 and state.get("threads"):
        return False
    return True


def pane_cached_state(st: dict | None, args, instance_pid, now_ms: int) -> bool:
    """Is the cached watcher state usable for this pane right now?

    Three things have to hold: the file is fresh, it answers THIS request, and the session it
    names is still live. The last one is what `state_matches_request` could not ask — `auto`
    accepts whatever backend the watcher wrote — and a watcher heartbeats the file it owns while
    the chat behind it has already finished, so a pane kept rendering the ended session until
    the file aged out or somebody reloaded it by hand. A dead session now sends the pane back to
    `snapshot`, which is where `auto` re-chooses its source.

    There is deliberately no "already handled" memo here. The file a pane reads is not the file
    the fresh answer lives in, so remembering that this session was dealt with would only mean
    trusting the same stale file again on the next poll — the pane dropped a list and would pick
    it straight back up, which is the freeze this rule exists to end. The pane therefore keeps
    answering from `snapshot` until the file itself names a live session again (a running
    watcher rewrites it within a poll; with no watcher at all, one probe per poll is what this
    pane already does).
    """
    if not state_matches_request(st, args):
        return False
    if not state_is_fresh(st, max(HEARTBEAT_GRACE, getattr(args, "interval", 1.0) * 3)):
        return False
    return not followed_session_over(st, instance_pid, now_ms)


def pane_subject_from_argv(argv=None) -> bool:
    """Did this pane's OWN command line name its subject, so nothing may override it?

    A lock that rode in from the environment cannot say whether a person asked for that
    subject or `auto` resolved it, and the two must be treated differently: `auto`'s answer
    inside the desktop app is the bug `pane_place_subject` exists to correct, while a
    `-s cli` / `--chat` / `--thread` somebody typed is theirs to keep. The pane's own argv
    still knows which it was, and `-s auto` is not a subject: it is the request to choose.
    """
    toks = list(sys.argv[1:] if argv is None else argv)
    for at, tok in enumerate(toks):
        if tok in ("-s", "--source"):
            value = toks[at + 1] if at + 1 < len(toks) else ""
            if value and source_kind(value) != "auto":
                return True
        elif tok.startswith("--source="):
            if source_kind(tok.partition("=")[2]) != "auto":
                return True
        elif tok in ("--chat", "-t", "--thread") or tok.startswith(("--chat=", "--thread=")):
            return True
    return False


def apply_pane_subject_lock(args, subject: str) -> str:
    """Pin this pane to a subject a caller already resolved, and describe it in one line.

    The same mechanics `latch_pane_subject` uses for the subject a poll RETURNED — the
    arguments become the lock (`--chat`/`--thread`, `--threads 1`), the environment carries
    it across a reload, and the record under `pane-subjects/` makes it readable from outside.
    Factored out because one caller decides the subject BEFORE the poll
    (`pane_place_subject`) and both must end in exactly the same state; "" is a subject that
    names nothing, which locks nothing.
    """
    kind, _, what = (subject or "").partition(":")
    if not what or kind not in ("cli", "desktop"):
        return ""
    if kind == "cli":
        args.chat, args.source = what, "cli"
    else:
        # A desktop thread is named by its id, and `others` goes to zero so the frame stops
        # stacking the threads this pane was not asked about.
        args.thread, args.source, args.threads = what, "desktop", 1
    os.environ[PANE_LOCK_ENV] = subject
    pane_subject_write(subject)
    args.pane_locked = True
    if kind == "cli":
        return f"locked to the cli chat {os.path.basename(what.rstrip('/'))}"
    return f"locked to the desktop thread {what[:8]}"


def pane_place_subject(args, cwd: str) -> str:
    """The subject the pane's own PLACE names — a desktop thread inside the app, else "".

    In plain words: *why does it use the cli pane?* A pane the desktop app opens runs a bare
    `fbtodo` in a thread's terminal, and `auto` answered it with whatever was live in the
    directory — a CLI chat — so every desktop pane latched the SAME chat while the thread it
    was drawn beside went unshown. The app hands the pane no thread id (measured 2026-10-05:
    its terminal environment carries `FREEBUFF_DESKTOP_STATE_PATH` and nothing per-thread,
    and the orchestrator keeps the per-terminal `threadId` in memory behind a launch token
    that is deliberately stripped), so the binding is inferred from the app's own store: the
    live threads of THIS project, the one the app is working in first (the store's own focus
    rule, `focused_thread`), then any other live thread. A thread another live pane already
    holds (`held_pane_subjects`) is passed over — that is what makes two panes of one project
    hold two threads rather than both holding the focused one.

    Deliberately narrow, because a wrong guess moves a reader's window: only inside the app
    (`desktop_app_pane`), only with no subject named (`-s auto`, no `--chat`/`--thread`), and
    only when the store really has a thread to name. Anything else returns "" and the pane
    keeps the answer it had — `auto`'s chain, cli included — so a machine with the app closed
    or a project with no desktop thread behaves exactly as before.
    """
    if not desktop_app_pane():
        return ""
    # A person who named the subject keeps it; only `auto` is ours to resolve.
    if pane_subject_from_argv():
        return ""
    # A store named outright (`--db PATH`) is that store, the way `DesktopSource.find` reads it;
    # the glob is only the search when nobody named one.
    pattern = getattr(args, "db", DEFAULT_DB_GLOB) or DEFAULT_DB_GLOB
    explicit = pattern if "*" not in pattern else None
    try:
        db = os.path.expanduser(explicit) if explicit else pick_db(
            pattern, getattr(args, "project", None), cwd=cwd, own_only=True)
    except Exception:
        return ""
    if not db or not os.path.exists(db):
        return ""
    try:
        st = read_desktop(db, source="active", others=8,
                          state_path=getattr(args, "state", DEFAULT_WORKSPACE_STATE))
    except Exception:
        return ""
    order: list[str] = []
    session = str(st.get("session") or "")
    if session:
        order.append(session)
    for row in st.get("threads") or []:
        tid = str((row or {}).get("id") or "")
        if tid and tid not in order:
            order.append(tid)
    if not order:
        return ""
    held = held_pane_subjects()
    for tid in order:
        if f"desktop:{tid}" not in held:
            return f"desktop:{tid}"
    return ""


def latch_pane_subject(args, state: dict) -> str:
    """Pin this pane to the subject it first resolved, and never move it again.

    A pane's job is to be ONE window on ONE list. Left to re-choose, it changed its mind
    under the reader every time the work moved: you type in another tab, the app's own
    picker answers with that thread instead, and the pane you were reading becomes a pane
    about something else — with no frame drawn to say so, because both frames were valid.
    That is "it keeps switching threads", and no amount of drawing fixes it.

    So the first successful poll decides, and the decision is made in the ARGUMENTS every
    later poll already goes through: one source, one thread or one chat, one list. The
    existing flags are the mechanism — `--thread`, `--chat`, `--threads 1` — so nothing new
    has to be remembered between polls, and a pane that reloads itself into a new build comes
    back locked the same way, because the lock is in its own command line from then on.

    What it does NOT do is decide the FIRST subject: that is still `auto`'s chain, so the
    rule that a finished chat must not answer a live desktop thread (the 2026-10-04 freeze)
    still picks the starting list. What stops is the moving afterwards — including when the
    locked thread's turn ends, because a finished list is still a list somebody is reading,
    and re-acquiring here is the flapping this is meant to end. An answer with no subject to
    name (an error, or nothing here yet) latches nothing and the pane keeps asking.

    Returns a one-line description of the lock, or "" once there is one.
    """
    backend = str(state.get("backend") or "")
    session = str(state.get("session") or "")
    target = str(state.get("target") or "")
    if getattr(args, "pane_locked", None) or not session or backend not in ("cli", "desktop"):
        return ""
    if backend == "cli":
        if not target:
            return ""
        args.chat, args.source = target, "cli"
        os.environ[PANE_LOCK_ENV] = f"cli:{target}"
        # ...and written down, because the environment it rides in cannot be read from
        # outside on every platform (the kernel snapshots an environment at exec, and this
        # write is later): `fbtodo panes` reads the record instead. See `pane_subject_write`.
        pane_subject_write(f"cli:{target}")
        # The flag is set HERE and not only read from the environment: it is what makes the
        # lock hold for the life of this process rather than only across a reload. Without
        # it every poll re-latched, so a pane whose accepted state named a different chat
        # (the watcher's answer to whoever asked first) silently moved the reader's window —
        # measured on this machine 2026-10-05: four different chats between 10:52:37 and
        # 10:52:57. Set only where a subject was actually named, so a state that cannot say
        # which chat or thread it is leaves the pane asking.
        args.pane_locked = True
        return f"locked to the cli chat {os.path.basename(target.rstrip('/'))}"
    # A desktop thread is named by its id, and `others` goes to zero so the frame stops
    # stacking the threads this pane was not asked about.
    args.thread, args.source, args.threads = session, "desktop", 1
    os.environ[PANE_LOCK_ENV] = f"desktop:{session}"
    pane_subject_write(f"desktop:{session}")
    args.pane_locked = True
    return f"locked to the desktop thread {session[:8]}"


def pane_poll_state(prev: dict | None, note: str, mode: str | None = None) -> dict:
    """The state a pane draws when its own next poll RAISED (see `cmd_pane`).

    Not the source's answer but the pane's: the list it was showing is gone, so the error row
    — which `render` already draws in place of a list, in red — carries the message, and the
    backend/session are kept so the title still says which source it had been reading when it
    broke (the *mode*, `-s`, when there was nothing to keep yet: a first poll that fails has no
    session to name and still has to say what it was trying to read). `todos` is emptied
    because an error state is not a list: nothing downstream may re-time or re-count rows from
    an answer that was never received.
    """
    base = {key: (prev or {}).get(key) for key in ("backend", "session", "source")}
    where = f" reading {mode}" if mode else ""
    base.update({"todos": [], "error": f"poll failed{where} — {note}"})
    return base


def cached_state_ok(st: dict | None, args) -> bool:
    """May a LOOK-ONLY command answer with the cached watcher state?

    `json` and `bar` are reads: they serve the state file while it is fresh and describes what
    was asked for, and only scan the stores when it is not. That rule was missing the same half
    the pane was: a watcher heartbeats the file it owns after the chat behind it has finished,
    so `fbtodo json -s auto` kept answering with an ended session's list from a directory whose
    live work had moved into the app — the pane's own freeze, in the command people run to see
    what the pane is about to draw (found by the self-check that switches a cached CLI chat for
    a live desktop thread through the real CLI, 2026-10-03). The instance is resolved only once
    the file is otherwise an answer, so the common case — a session still running — costs no
    `pgrep`.
    """
    if not st or not state_is_fresh(st, max(HEARTBEAT_GRACE, getattr(args, "interval", 1.0) * 3)):
        return False
    inst, _ = find_instance(os.path.realpath(os.getcwd()), args.watch_pid, args.instance_of)
    return pane_cached_state(st, args, inst, int(time.time() * 1000))


# =================================================================== commands
def ensure_daemon(args, quiet: bool = True) -> int | None:
    # The panes are kept by their own process, asked for here because this is the moment
    # a session and its pane appear — and asked for BEFORE the watcher lock is consulted,
    # so a second daemon holding that lock cannot leave a local pane unguarded.
    ensure_pane_keeper(args, quiet=quiet)
    running = live_watcher_pid(LOCK_PATH)  # an upgrade's leftover is replaced, not adopted
    if running:
        return running
    cwd = os.path.realpath(os.getcwd())
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
        # The pane draws what the WATCHER wrote, so how many threads to stack has to reach
        # it: a daemon that fell back to the module default would quietly unstack a pane
        # that asked for two.
        "--threads",
        str(getattr(args, "threads", DESKTOP_MAX_THREADS)),
        "--thread-live",
        str(getattr(args, "thread_live", DESKTOP_LIVE_MS // 60_000)),
        # Passed on because a daemon that inherited
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
    # wait for the lock so callers can rely on the daemon existing. The probe is the
    # right question for a START: a free record naming a dead pid must not read as "it
    # started" (`lock_peek` would leave the leftover that made it read that way).
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
    # A watcher that re-execs itself (see the build check in the loop below) hands its claim
    # over the exec — same pid, same open file description — so the claim is taken BACK
    # before anything asks whether one is free: `daemon_pid()` with the handed-over claim
    # still in hand names this very process, and the guard would stand down the watcher it
    # just restarted. Nothing to take is no problem: this is then an ordinary start and the
    # guard below decides.
    carried = lock_adopt()
    # The start guard probes on purpose: a leftover must be cleared before this process
    # claims the name, and only a HELD claim is a watcher to stand down for (or refuse
    # without `--force`). A look would use `lock_peek`.
    if not carried and daemon_pid() and not args.force:
        if not args.quiet:
            print(f"watcher already running (pid {daemon_pid()})", file=sys.stderr)
        return 0

    cwd, instance_pid = resolve_instance(args)
    if not instance_pid:
        if not args.quiet:
            print("no running Freebuff instance", file=sys.stderr)
        return EX_CODES["nofile"]

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
    # The root this claim lives in, by IDENTITY and not by existence: a claim file that moved
    # (removed, replaced) leaves the directory's inode alone, while a directory that was
    # REMOVED and re-created — even by this watcher's own state write, whose `atomic_write`
    # makes the directory again — is a different inode. That is what keeps "the name moved"
    # (re-claim) apart from "the whole root is gone" (stop, never resurrect a home somebody
    # removed), and it survives the race where a state write lands alongside the removal.
    def root_ident():
        try:
            st = os.stat(os.path.dirname(LOCK_PATH))
        except OSError:
            return None
        return (st.st_dev, st.st_ino)

    claimed_root = root_ident()
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
    # When the watcher next asks whether it is still the build on disk. It asks the same
    # question the pane asks (`source_newer_than`) for the same reason: nothing re-imports a
    # running process, so a watcher left over from an earlier version would keep writing its
    # older answers until the next `fbtodo` start replaced it — and the watcher is where every
    # reader's numbers come from. The difference from the pane is the claim: the pane holds
    # none, while the claim is the watcher's whole standing and must not so much as blink, or
    # a second watcher could take it in the gap. `lock_handoff` carries it through the exec
    # and `lock_adopt` above takes it back. `held_note` remembers the broken save already
    # logged, so a half-written file is noted once rather than once per tick.
    next_build_check = 0.0
    held_note = None
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
            if not pid_alive(instance_pid):
                stop_reason = "instance-exited"
                stop_code = 0
                break
            if not lock_ours(LOCK_PATH):
                # The name half of the claim moved — the file was removed, or replaced by a
                # fresh one — while the kernel lock on the unlinked inode kept this process
                # looking like the owner. Left alone the watcher writes state no reader ties
                # to a watcher: `lock_peek` says the role is not running, the process half of
                # `locks` names it an orphan, and `locks --fix` ends it. So it takes the NAME
                # back the way the keeper does — a free or absent name is claimed again
                # (`write_lock` drops the registry entry that no longer points at the name),
                # and a name a live process HOLDS belongs to that process: another watcher
                # serves this root now, so this one stands down rather than fight it, which
                # is its start's own rule. The one case to stop for is a root that is GONE,
                # judged by the directory's IDENTITY rather than its existence (see
                # `root_ident` below): re-claiming into a removed root would resurrect a home
                # somebody removed.
                if root_ident() != claimed_root:
                    stop_reason = "root-removed"
                    stop_code = 0
                    break
                gone = not os.path.exists(LOCK_PATH)
                if not write_lock(cwd, instance_pid):
                    stop_reason = "claim taken by another watcher"
                    stop_code = 0
                    break
                append_log(
                    LOG_PATH,
                    f"watcher re-claimed its name (pid {os.getpid()}, the file was "
                    + ("removed)" if gone else "replaced)"),
                )
            now = time.time()
            if now >= next_build_check:
                next_build_check = now + BUILD_CHECK_S
                changed = source_newer_than(STARTED_AT, now)
                if changed:
                    # A parse is not enough to hand the process over to: a tree that compiles
                    # file by file can still fail to BECOME a process (a name imported from a
                    # module that no longer defines it, a raise at module scope), and the exec
                    # below would replace this running watcher with one that dies at import.
                    # So the new build is asked to load first (`reload_probe_error`), and only
                    # a build that answers yes is executed into; otherwise this watcher holds,
                    # keeping the last build that provably loaded.
                    broken = source_syntax_error()
                    unfit = broken or reload_probe_error()
                    if unfit:
                        if unfit != held_note:  # once per distinct problem, not once a tick
                            held_note = unfit
                            append_log(
                                LOG_PATH,
                                f"watcher holding, source does not parse: {unfit}" if broken
                                else f"watcher holding, the new build does not load: {unfit}",
                            )
                    else:
                        append_log(LOG_PATH, f"watcher reloading: {changed} changed "
                                             "after this process started")
                        fd = lock_handoff()
                        argv = self_argv()
                        try:
                            os.execv(argv[0], [*argv, *sys.argv[1:]])
                        except OSError as exc:
                            # Still the watcher: put the claim's fd back the way this process
                            # keeps it, and record that the reload did not happen — once per
                            # distinct failure, because the source is still newer on the next
                            # tick and a line a second is a flood, not a note.
                            if fd is not None:
                                os.environ.pop(LOCK_FD_ENV, None)
                                try:
                                    os.set_inheritable(fd, False)
                                except OSError:
                                    pass
                            note = f"reload failed: {exc.__class__.__name__}"
                            if note != held_note:
                                held_note = note
                                append_log(LOG_PATH, f"watcher {note}")
            st = snapshot(args, cwd=cwd, instance_pid=instance_pid)
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
            if args.pause_seconds > 0 and time.monotonic() >= pause_due:
                pause_due = time.monotonic() + args.pause_seconds
                pause_notify_once(args, instance_pid, quiet=args.quiet)
            # What it reports is this machine's tmux, so the watcher asks about it
            # whatever list it is serving.
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


def _short_python(path: str) -> str:
    """`/opt/homebrew/…/Python`: enough of an interpreter path to tell two of them apart.

    A framework python's path ends in forty characters of the same suffix on every install
    (`…/Resources/Python.app/Contents/MacOS/Python`), so printed whole the two halves of
    `pane python` differ somewhere off the right edge of the terminal. The head is where the
    difference is — `/opt/homebrew`, `/Library/Developer`, `/usr/bin` — so the head and the
    name are what is kept.
    """
    parts = path.split(os.sep)
    if len(parts) <= 5:
        return path
    return os.sep.join([*parts[:3], "…", parts[-1]])


def one_python_check(roles: list) -> tuple:
    """The doctor's `one python` line: the pane, its watcher and the keeper on one interpreter.

    In plain words: the pin exists so these three agree, and a disagreement is invisible from
    the outside — which is how one went unnoticed until it was found by hand (2026-10-01: a
    pane on `/usr/bin/python3` 3.9.6 beside a watcher on Homebrew's 3.14). `doctor` is where
    the question gets asked on purpose, and the roles are read from the processes themselves
    (`pane_python`, `python_of`), never assumed, and compared by REAL PATH — a symlinked
    spelling is the same interpreter, the same rule `pane_drifted` uses.

    A disagreement is a WARN and not a FAIL: the keeper reopens a drifted pane on its pin and
    the next session comes up pinned, so nothing here stops a pane — but a machine that
    answers with three paths is a machine to look at, and those paths ARE the answer, which
    is why they are printed whole rather than shortened. Fewer than two roles running is
    nothing to compare, which is `ok` said out loud rather than silently.
    """
    known = [(role, path) for role, path in roles if path]
    if not known:
        return ("ok", "no pane, watcher or keeper running — nothing to compare")
    if len(known) == 1:
        return ("ok", f"{known[0][0]} on {_short_python(known[0][1])} — nothing to compare "
                       "yet (a pane and its keeper start with a session)")
    if len({os.path.realpath(path) for _role, path in known}) == 1:
        names = [role for role, _path in known]
        who = names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"
        return ("ok", f"{who} all on {_short_python(known[0][1])}")
    # A mismatch is where the reader needs the whole path: the two heads are the difference
    # being diagnosed, and `_short_python`'s ellipsis would hide exactly that.
    return ("warn", " · ".join(f"{role} {path}" for role, path in known)
            + " — the keeper reopens a pane on its watcher's pin")


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
    # The watcher's claim is READ, not probed: `lock_holder` is deliberately destructive (a
    # free claim means the record is a leftover it removes), and `doctor` must not empty a
    # state root it was only asked to look at — that record is what `_claim_live` decides the
    # move from, and what `one python` reads the watcher's pid from. `lock_peek` asks the same
    # two questions — can the lock be taken, and what pid does the record name — with no
    # unlink and no write anywhere: a doctor run leaves every claim record byte-identical.
    held, holder = lock_peek(LOCK_PATH)
    if held:
        checks.append(("ok", "watcher",
                       f"pid {holder} holds {os.path.basename(LOCK_PATH)}" if holder
                       else f"a claim is held on {os.path.basename(LOCK_PATH)} "
                            "(no pid in the record)"))
    else:
        checks.append(("warn", "watcher", "not running (a pane starts one)"))

    kit = os.path.dirname(TODO_NOTIFY)
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

    # One interpreter for the pane, its watcher and the keeper: the pin exists to make them
    # agree, and a repair that would leave the keeper itself behind on another one is exactly
    # what a keeper left from an older build used to do. See `one_python_check` for why a
    # disagreement is a warn rather than a fail, and why the paths are printed whole.
    #
    # The keeper's pid comes from the record it writes, not from `lock_holder`: that probe is
    # deliberately destructive — a claim it finds free means the record is a leftover it
    # REMOVES — and `doctor` must not empty a state root it was only asked to look at. The
    # record is exactly what the move decision is read from (`_claim_live` asks the same
    # file), so a diagnostic that deleted it would erase the fact it reported a line earlier.
    # The read is the pane bell's (`keeper_alive`): the record, and whether its pid is alive.
    table = process_table()
    rows = pane_rows()
    panes = local_pane_ids(inst, rows, table) if inst else []
    keeper_rec = read_json(PANE_KEEPER_PATH, {}) or {}
    keeper_pid = keeper_rec.get("pid")
    keeper_pid = int(keeper_pid) if keeper_pid and pid_alive(keeper_pid) else None
    python_level, python_detail = one_python_check([
        *[(f"pane {p}" if len(panes) > 1 else "pane", pane_python(p, rows, table))
          for p in panes],
        ("watcher", python_of((table.get(holder) or (0, ""))[1]) if holder else None),
        ("keeper", python_of((table.get(keeper_pid) or (0, ""))[1]) if keeper_pid else None),
    ])
    checks.append((python_level, "one python", python_detail))

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
    # Stop is an ACTOR: `daemon_pid` is the probe, so a free record naming a dead pid is a
    # leftover — removed here rather than believed. Reading it as a watcher would signal
    # nothing and leave the record for the next start to trip over; the look-only family
    # uses `lock_peek` instead (see `daemon_pid`).
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


def keeper_server_view() -> dict:
    """The keeper's server and whether the asks that could replace it agree.

    One answer for the row and for `--json`: a script watching for keeper churn reads the
    same facts the row prints, from the same reader. `status` only READS — the record is
    believed by the pid it names, never by taking the claim, whose free answer removes the
    record (the doctor learned that the hard way). `server` is the record reduced to the
    socket it denotes (a record written by an older build spells the socket differently,
    which is a spelling, not another server), `recorded` is the spelling itself, `asks`
    are the names the two asks would write — reduced the same way, `None` when that context
    cannot name a server — and `would_replace` names the asks that name a different server:
    each one, when it next runs, kills this keeper and starts its own. `note` is the row's
    sentence.
    """
    rec = read_json(PANE_KEEPER_PATH, {}) or {}
    pid = rec.get("pid")
    pid = int(pid) if pid and pid_alive(pid) else None
    if not pid:
        return {
            "running": False, "pid": None, "server": None, "recorded": None,
            "asks": {"pane": None, "watcher": None}, "agree": False,
            "would_replace": [], "note": "— none running",
        }
    recorded = rec.get("tmux")
    server = tmux_socket_of(recorded) or recorded or "unnameable"
    here = tmux_identity() if os.environ.get("TMUX") else None
    outside = tmux_identity_outside()
    asks = {
        "pane": tmux_socket_of(here) or here if here else None,
        "watcher": tmux_socket_of(outside) or outside if outside else None,
    }
    stranger = [
        (label, ask) for label, ask in (("pane", here), ("watcher", outside))
        if ask and not same_tmux_server(recorded, ask)
    ]
    if stranger:
        note = "; ".join(
            f"the {label}'s ask names {tmux_socket_of(ask) or ask}" for label, ask in stranger
        ) + " — the next ask replaces this keeper"
    elif here and outside:
        note = "the pane's and the watcher's asks name it"
    elif here:
        note = "the pane's ask names it"
    elif outside:
        note = "the watcher's ask names it"
    else:
        note = "no ask from this context names a server"
    if recorded and server != recorded:
        note += f"; recorded as {recorded}"
    return {
        "running": True, "pid": pid, "server": server, "recorded": recorded,
        "asks": asks, "agree": not stranger and bool(here or outside),
        "would_replace": [label for label, _ask in stranger], "note": note,
    }


def keeper_watch_event(prev: dict | None, cur: dict) -> dict | None:
    """The keeper change worth a line between two reads, or None when nothing moved.

    In plain words: a keeper's failure mode is history — a pid that was replaced, asks
    that would replace it — which a snapshot cannot show, and a poll that reported every
    read would bury that history under identical lines. So two reads are compared and only
    the moves become events: the state a watch STARTS in (`now`, once), a pid change
    (`pid`, which reads `gone` when the keeper stops answering), the asks that would
    replace it going from agreement to a stranger (`churn`), and the same set going back to
    agreement (`agree`). Everything else — same pid, same verdict — is None and says
    nothing, because the silence is what makes the stream readable over an hour.
    """
    if prev is None:
        return {**cur, "event": "now", "previous_pid": None}
    if prev["pid"] != cur["pid"]:
        return {**cur, "event": "gone" if cur["pid"] is None else "pid",
                "previous_pid": prev["pid"]}
    if cur["would_replace"] != prev["would_replace"]:
        if cur["would_replace"]:
            return {**cur, "event": "churn", "previous_pid": prev["pid"]}
        if prev["would_replace"]:
            return {**cur, "event": "agree", "previous_pid": prev["pid"]}
    return None


def keeper_watch_line(event: dict) -> str:
    """One watch event as the line a person reads — the same facts `--json` carries."""
    what, pid, was = event["event"], event["pid"], event["previous_pid"]
    head = f"{what:<7} keeper : "
    if pid is None:
        return head + "— none running" + (f"  (was {was})" if was else "")
    if what == "pid":
        detail = f"was {was or '—'}, replaced" if was else "appeared"
    else:
        detail = event["note"]
    return f"{head}{pid}  ({detail})"


def watch_keeper(args) -> int:
    """Watch the keeper: one line for the state it starts in, then one per CHANGE only.

    In plain words: `status` is a snapshot, and a keeper's failure mode is history — a pid
    that was replaced, asks that would replace it — which a snapshot cannot show. This
    polls the same reader the row uses (`keeper_server_view`), prints the state once, and
    after that only moves: a pid change, churn due, churn cleared. Between them it says
    nothing, so the output stays readable however long it runs and a script can watch for
    keeper churn without filtering identical lines (`--json`: one event object per line,
    the same facts the snapshot's `keeper` object carries). `-i` sets the poll; Ctrl-C
    ends it, quietly.
    """
    every = max(0.2, float(getattr(args, "interval", None) or 1.0))
    seen = None
    try:
        while True:
            now = keeper_server_view()
            event = keeper_watch_event(seen, now)
            if event is not None:
                seen = now
                if args.json:
                    json.dump({"at_ms": int(time.time() * 1000), **event}, sys.stdout,
                              ensure_ascii=False)
                    sys.stdout.write("\n")
                else:
                    print(keeper_watch_line(event))
                sys.stdout.flush()
            time.sleep(every)
    except KeyboardInterrupt:
        return EX_CODES["ok"]
    except BrokenPipeError:
        # `status --watch | head -1` is a normal way to read it: a closed reader is an end,
        # not an error, and the interpreter must not complain while flushing at exit.
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass
        return EX_CODES["ok"]


def cmd_keep(args) -> int:
    """Turn the pane-repair knob off and on — `@fbtodo_repair`, without the incantation.

    In plain words: the knob exists so a deliberate interpreter mix survives the keeper's
    automatic repair (see `pane_repair_off`), and until now it was only reachable as a tmux
    command. Three verbs are the whole interface: `off` keeps a drifted pane as it is —
    still diagnosed, still told on its own chip (`KEPT …`) — `on` resumes the repair, said
    explicitly so that one pane can win over a server-wide `off`, and `default` removes the
    choice at the scope instead of writing a value over it, so what the scope inherits from
    applies again. With no verb, it prints what is in force. One scope per call: the pane
    you are in, or one named with `--pane %3`; one window with `--window TARGET`, resolved
    through tmux so `main:2` and a window name both work and the report names the id that
    landed; `--server` is the whole server, which is how a machine that mixes interpreters
    on purpose marks every pane at once.
    """
    server = bool(getattr(args, "keep_server", False))
    pane = getattr(args, "keep_pane", None)
    asked = (getattr(args, "window", None) or "").strip()
    given = [name for name, on in
             (("--server", server), ("--pane", bool(pane)), ("--window", bool(asked))) if on]
    if len(given) > 1:
        print(f"fbtodo keep: {given[0]} is one scope and {given[1]} another; pick one",
              file=sys.stderr)
        return EX_CODES["ex_usage"]
    window = None
    if asked:
        # Resolved once, before the read and used by the write: `window_of` accepts the
        # same targets `pin`/`why` do, answers with the id, and a window that is gone is
        # tmux's silence (69) rather than a guessed `-w` target.
        window = window_of(asked)
        if window is None:
            print(f"fbtodo keep: no such window: {asked}", file=sys.stderr)
            return EX_CODES["unavailable"]
        scope = f"window {window}"
    elif server:
        scope = "server-wide"
    else:
        pane = pane or os.environ.get("TMUX_PANE")
        if not pane:
            print("fbtodo keep: not in a tmux pane — name one with --pane %ID, a window "
                  "with --window TARGET, or use --server for every pane", file=sys.stderr)
            return EX_CODES["ex_usage"]
        scope = f"pane {pane}"

    def report(state: str) -> int:
        effect = {
            "off": "a drifted pane is kept as it is, and told",
            "on": "a drifted pane is reopened on its pin",
            "default": ("the server's choice applies again" if window
                        else "the window's or the server's choice applies again"),
        }[state]
        print(f"  repair : {state}  ({scope} — {effect})")
        return EX_CODES["ok"]

    verb = args.keep_verb
    if verb is None:
        if window:
            value = window_repair_value(window)
            if value is None:
                print(f"fbtodo keep: tmux cannot read window {window}", file=sys.stderr)
                return EX_CODES["unavailable"]
        elif server:
            value = tmux_run("show-options", "-g", "-v", "@fbtodo_repair")
            if value is None and tmux_run("list-sessions") is None:
                print("fbtodo keep: no tmux server to ask", file=sys.stderr)
                return EX_CODES["unavailable"]
        else:
            value = pane_repair_value(pane)
            if value is None:
                print(f"fbtodo keep: tmux cannot read pane {pane}", file=sys.stderr)
                return EX_CODES["unavailable"]
        return report("off" if repair_is_off(value) else "on")
    setting = None if verb == "default" else verb
    if window:
        wrote = set_window_repair(window, setting)
    else:
        wrote = set_pane_repair(None if server else pane, setting)
    if not wrote:
        print(f"fbtodo keep: tmux refused to set @fbtodo_repair on {scope}", file=sys.stderr)
        return EX_CODES["unavailable"]
    return report(verb)


def claim_files() -> list:
    """Every claim file this state root knows: the role, and the path it is claimed at.

    The three roles' own files, plus the legacy root's own two when they are still there —
    a claim that was never moved can still hold the old store (`_claim_live`), so the audit
    names it instead of pretending this root is all there is.
    """
    files = [("watcher", LOCK_PATH), ("keeper", PANE_KEEPER_PATH)]
    seen = {os.path.realpath(p) for _, p in files}
    for label, name in (("watcher", LEGACY_CLAIMS[0]), ("keeper", LEGACY_CLAIMS[1])):
        path = os.path.join(LEGACY_SCRATCH, name)
        if os.path.exists(path) and os.path.realpath(path) not in seen:
            files.append((f"legacy {label}", path))
            seen.add(os.path.realpath(path))
    return files


def locks_findings(rows: list, running: list) -> dict:
    """Every finding the audit can name, keyed by the THING that is wrong — a watch's unit.

    In plain words: `locks` prints two kinds of wrong, and a watch needs them as a SET so it
    can tell a new finding from a standing one. An ORPHAN is a live role process no claim
    names (`orphan:<role>:<pid>`), read from the process half of the audit. A BROKEN TIE is
    a claim file that is free while its record names a LIVE process (`tie:<role>:<pid>`):
    the file and the record are no longer the same claim. A held claim, a dead leftover and
    an absent file are not findings — the audit's own row explains each, and a watch that
    cried at a claim being born would be noise. The value carries what a message needs
    (role, pid, path, whether the process could be placed at all).
    """
    out: dict = {}
    for label, entry in rows:
        for proc in claim_orphans(entry, [p for p in running if p["role"] == label]):
            if int(proc["pid"]) == os.getpid():
                continue  # a watch is not a finding about itself
            out[f"orphan:{label}:{proc['pid']}"] = {
                "kind": "orphan", "role": label, "pid": int(proc["pid"]),
                "path": entry["path"], "root_named": bool(proc.get("root_named", True)),
                "env_clipped": bool(proc.get("env_clipped", False)),
            }
        if (entry.get("leftover") and entry.get("name_inode") == "mismatch"
                and entry.get("pid_alive") and entry.get("pid")):
            out[f"tie:{label}:{entry['pid']}"] = {
                "kind": "tie", "role": label, "pid": int(entry["pid"]),
                "path": entry["path"], "version": entry.get("version"),
            }
    return out


def locks_watch_line(event: str, finding: dict) -> str:
    """One watch event as the line a person reads — the same facts `--json` carries."""
    role = f"{finding['role']:<9}"
    head = f"{event:<6} {finding['kind']:<7} {role} pid {finding['pid']:<7}"
    where = os.path.basename(finding.get("path") or "")
    if finding["kind"] == "orphan":
        detail = "no claim names it" + (
            "" if finding.get("root_named", True) else " (this root assumed)")
    else:
        detail = "free while its record names this live pid — the name and the inode parted"
    if event == "clear":
        detail = "a claim names it again" if finding["kind"] == "orphan" else \
            "the file and the record agree again"
    return f"{head}{detail}  ({where})" if where else f"{head}{detail}"


def watch_locks(args) -> int:
    """Watch the audit: a line per finding as it appears, and one when it goes.

    In plain words: `locks` is a snapshot, and the two things it can find are both things
    that HAPPEN — a role process whose claim name was replaced under it (it holds a lock on
    an inode nothing points at, so every reader calls the role dead and a second one may be
    started over the fresh name), and a claim file whose name and inode have parted. Both
    are invisible in every store, so nothing else will ever mention them: a watch is the
    only way an operator hears about it without typing `locks` at the right moment. The
    stream prints each finding once, as it appears, plus one line when it is resolved, and
    says nothing in between however long it runs. When a NEW finding appears it asks the
    locks watch (`LOCKS_NOTIFY`, absent on a machine without the kit) to push it to the
    phone; the bell owns whether it has already pushed, so a restart cannot double-send.
    `-i` sets the poll; Ctrl-C ends it, quietly.
    """
    every = max(0.5, float(getattr(args, "interval", None) or 5.0))
    seen: dict = {}
    try:
        while True:
            rows = [(label, claim_audit(path)) for label, path in claim_files()]
            findings = locks_findings(rows, claim_processes())
            appeared = [key for key in findings if key not in seen]
            for key in appeared:
                seen[key] = findings[key]
                event = {"at_ms": int(time.time() * 1000), "event": "open",
                         "key": key, **findings[key]}
                if args.json:
                    json.dump(event, sys.stdout, ensure_ascii=False)
                    sys.stdout.write("\n")
                else:
                    print(locks_watch_line("open", findings[key]))
            cleared = [k for k in seen if k not in findings]
            for key in cleared:
                gone = seen.pop(key)
                event = {"at_ms": int(time.time() * 1000), "event": "clear",
                         "key": key, **gone}
                if args.json:
                    json.dump(event, sys.stdout, ensure_ascii=False)
                    sys.stdout.write("\n")
                else:
                    print(locks_watch_line("clear", gone))
            # Both kinds flush: a watch is read through a pipe (`| head`), and a line the
            # reader cannot see until the buffer fills is not a watch. Flushing only the
            # open lines left a resolution invisible for as long as the pipe stayed open.
            if appeared or cleared:
                sys.stdout.flush()
            if appeared:
                locks_notify_once(args, quiet=args.quiet)
            time.sleep(every)
    except KeyboardInterrupt:
        return EX_CODES["ok"]
    except BrokenPipeError:
        # `locks --watch | head -3` is a normal way to read it: a closed reader is an end,
        # not an error, and the interpreter must not complain while flushing at exit.
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass
        return EX_CODES["ok"]


def cmd_locks(args) -> int:
    """Audit every claim file: holder, name↔inode tie, stale records, and clearing.

    In plain words: each role that owns a scratch dir — the watcher and the pane keeper —
    owns it through one claim file, and this asks those files the
    questions their own readers and writers use, WITHOUT writing anything anywhere (the
    probe `lock_holder` would remove a leftover; an audit exists to say so instead). Who
    holds it (the record's pid, and whether it is alive); whether the lock and the NAME are
    one file (a held claim blocks this process's own open of the name, so the tie holds; a
    record naming a LIVE pid while the file is free is the tie broken — a leftover, a claim
    being born, or a claim orphaned by a replaced file); whether the record is stale; and
    what clearing it would take (a held claim: end the holder, and unlinking does not; a
    free leftover: the next ask; an empty free file: nothing — it may be about to be
    locked).

    ...and the rows are CROSS-CHECKED against the processes that are actually running
    (`claim_processes`): a claim file can only name a holder that still ties to its name,
    so a process whose name was replaced — the leftover cleanup above took it, and it went
    on running invisibly — leaves a file that reads `absent` or `free`. The process table
    is asked the other end: every watcher or keeper running with THIS state root,
    and which of them no claim names. Those rows print under the claim that cannot see
    them, and `orphans` carries the same facts in `--json`. `--json` is the same rows for
    a script, and no run changes a claim file — `--fix` is the separate path that acts on
    these findings (see `locks_fix`), so the audit stays a look.
    """
    if getattr(args, "fix", False):
        return locks_fix(args)
    if getattr(args, "watch", False):
        return watch_locks(args)
    rows = [(label, claim_audit(path)) for label, path in claim_files()]
    running = claim_processes()
    orphans = {label: claim_orphans(entry, [p for p in running if p["role"] == label])
               for label, entry in rows}

    def level_of(entry: dict) -> str:
        if entry["name_inode"] == "mismatch":
            return "warn"
        if entry["state"] == "held":
            return "note" if entry["pid"] and not entry["pid_alive"] else "ok"
        if entry["state"] == "absent":
            return "ok"
        return "note"

    if args.json:
        json.dump(
            {"version": VERSION, "scratch": SCRATCH,
             "claims": [{"role": label, **entry,
                         "orphans": orphans.get(label, [])} for label, entry in rows]},
            sys.stdout, ensure_ascii=False,
        )
        sys.stdout.write("\n")
        return EX_CODES["ok"]
    hint = {
        "watcher": " — `fbtodo stop` asks this one to end",
        "keeper": " — it also exits with the last freebuff",
    }
    print(f"fbtodo locks  {SCRATCH}")
    for label, entry in rows:
        role = label[len("legacy "):] if label.startswith("legacy ") else label
        tie = "" if entry["name_inode"] is None else f"  [name↔inode: {entry['name_inode']}]"
        print(f"  {level_of(entry):<5} {label:<14} {os.path.basename(entry['path']):<23}"
              f" {entry['note']}{tie}")
        if entry["state"] != "absent":
            print(f"        clear: {entry['clearing']}{hint.get(role, '')}")
        for proc in orphans.get(label, []):
            # The sentence the name-based read cannot produce: a process is RUNNING under
            # this root, and the file above does not name it. It holds a claim nobody can
            # find — a lock on an unlinked inode — and clearing means ending it, not
            # unlinking (there is nothing on disk left to unlink). A process whose
            # environment could not be READ — or came back clipped at the kernel's ~1 KB
            # copy — is reported with the assumption said out loud, because that root is
            # only assumed and `locks --fix` does not end what it cannot place.
            py = f"  [{_short_python(proc['python'])}]" if proc.get("python") else ""
            if proc.get("root_named", True):
                print(f"        running: pid {proc['pid']} ({label}) with this state root, "
                      f"holding a claim no name points at{py}")
                print(f"        clear: end pid {proc['pid']} (SIGTERM) — its lock lives on an "
                      "unlinked file; nothing on disk to unlink")
            else:
                why = ("its environment came back clipped at the kernel's ~1 KB copy, so a "
                       "root variable may have been cut off" if proc.get("env_clipped") else
                       "no environment could be read from it")
                print(f"        running: pid {proc['pid']} ({label}), holding a claim no name "
                      f"points at{py}")
                print(f"        assume: {why} — this state root is assumed only, so "
                      "`locks --fix` will not end it")
    return EX_CODES["ok"]


def locks_fix_plan(rows: list, running: list) -> tuple:
    """What a fix would do — the audit read one more time, with no side effects.

    In plain words: two findings are fixable, and only these two. A claim whose record is
    FREE and names a pid is a `leftover` (the next ask would remove it anyway) and goes in
    `clears`. A running role process no claim can name is UNTIED — it holds a lock on an
    unlinked inode, so nothing on disk can free it — and goes in `kills`; but only when it
    was PLACED, i.e. its own environment was read and named this state root (`root_named`).
    `claim_processes` deliberately counts an environment it could not read — or one that came
    back clipped at the kernel's ~1 KB copy, which may have cut the root variable off — as
    ours: "a false positive is a sentence, not a kill", and a sentence is all the audit may
    do with that. Those go in `skipped` instead, because ending a process this run cannot
    place is the one thing a repair must not do on a guess. A held claim with its name, an empty free file (a claim being born),
    an absent file, and this very process are not findings.
    """
    orphans = {label: claim_orphans(entry, [p for p in running if p["role"] == label])
               for label, entry in rows}
    clears = [(label, entry) for label, entry in rows
              if entry["state"] == "free" and entry.get("leftover")]
    kills: list = []
    skipped: list = []
    for label, _entry in rows:
        for proc in orphans.get(label, []):
            if int(proc["pid"]) == os.getpid():
                continue
            (kills if proc.get("root_named", True) else skipped).append((label, proc))
    return clears, kills, skipped


def _confirm(prompt: str, args) -> bool | None:
    """Ask before acting: True yes, False no, None when it cannot be asked at all.

    The prompt goes to STDERR so `--json` stdout stays one parseable object, and a `y`/`yes`
    (any case) approves — anything else, including EOF on a disconnected stdin, does not.
    `--yes` answers for the operator, and `--no-input` — or a stdin that is not a terminal —
    never prompts: a question nobody can answer must fail fast (the caller turns None into
    66) rather than hang.
    """
    if getattr(args, "yes", False):
        return True
    if getattr(args, "no_input", False) or not sys.stdin.isatty():
        return None
    sys.stderr.write(f"{prompt} [y/N] ")
    sys.stderr.flush()
    try:
        answer = sys.stdin.readline()
    except (OSError, KeyboardInterrupt):
        return None
    return bool(answer) and answer.strip().lower() in ("y", "yes")


def restart_claims(args) -> dict:
    """Ask the machine to watch again after a fix: the keeper, then the watcher.

    In plain words: a repair that ended an untied role process leaves the claim FREE, and
    nothing re-claims it until the next session start or the next shell hook happens to run
    — which can be hours on a quiet machine, with the panes unguarded while it waits. So
    `--restart` runs the same ask a session start uses (`ensure_daemon`, which asks for the
    keeper before it consults the watcher's lock), and reports what each role looks like
    afterwards. It is a no-op where a watcher or keeper is already running — the ask returns
    the holder rather than starting a second — and it never ends anything to make room: any
    ending was `locks --fix`'s own job, already done above. A start that cannot even be
    attempted (no running Freebuff instance to follow) is not an error here: the keeper may
    still have been asked for, and the watcher is simply left as it is.
    """
    def held(path: str):
        ok, pid = lock_peek(path)
        return pid or None if ok else None

    before = {"watcher": held(LOCK_PATH), "keeper": held(PANE_KEEPER_PATH)}
    try:
        ensure_daemon(args, quiet=True)
    except Exception as exc:  # a start that could not be attempted at all
        return {role: {"pid": before[role], "before": before[role],
                       "error": exc.__class__.__name__} for role in before}
    return {role: {"pid": held(LOCK_PATH if role == "watcher" else PANE_KEEPER_PATH),
                   "before": before[role]} for role in before}


def locks_fix(args) -> int:
    """Resolve what the audit found: clear dead records, end untied role processes.

    In plain words: `locks` is a LOOK and says what clearing each claim would take; this is
    the same read with the hand that acts. The free leftovers it reports are removed
    through the same probe the next ask uses (`lock_holder`), which re-opens the file, takes
    it and re-checks the name under the lock — so a claim that appeared between the audit
    and here is never deleted. Untied role processes are ended with SIGTERM (a bounded wait,
    and a survivor is reported rather than escalated): their lock lives on an inode no name
    points at, so ending them is the only repair, and it is the receiver that then re-claims
    when it next runs. A repair that would end ANYTHING asks first — on the terminal, or with
    `--yes` — and refuses with 66 when it cannot ask (`--no-input`, no terminal), changing
    nothing at all: a kill is never taken on a guess, and never half-applied.    `--dry-run` prints the same plan and touches nothing. On its own it starts nothing —
    the claims it frees are what the next ask (the shell's `fbtodo daemon`, or a pane)
    re-claims; `--restart` is the opt-in that runs that ask now (see `restart_claims`),
    so one command can leave the machine watched again instead of waiting for the next
    session to start.
    """
    rows = [(label, claim_audit(path)) for label, path in claim_files()]
    clears, kills, skipped = locks_fix_plan(rows, claim_processes())
    applied, reason = False, None
    cleared: list = []
    ended: list = []
    failed: list = []
    restarted: dict = {}
    if args.dry_run:
        reason = "dry run"
    elif kills:
        asked = _confirm(
            "end " + str(len(kills)) + " untied process(es) — "
            + ", ".join(f"{label} pid {proc['pid']}" for label, proc in kills)
            + "?",
            args,
        )
        if asked is None:
            reason = "confirmation required"
        elif not asked:
            reason = "declined"
        else:
            applied = True
    else:
        applied = True
    if applied:
        for label, entry in clears:
            lock_holder(entry["path"])  # the ask's own cleanup: re-checks the name, then unlinks
            cleared.append({"role": label, "path": entry["path"],
                            "removed": not os.path.exists(entry["path"])})
        for label, proc in kills:
            pid = int(proc["pid"])
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                ended.append({"role": label, "pid": pid, "outcome": "already gone"})
                continue
            # `pid_running`, not `pid_alive`: the keeper this ends is usually somebody
            # else's child, and an unreaped one answers `kill(pid, 0)` from beyond the
            # grave — reporting that corpse as a survivor would be a failure that is not
            # there (measured: the self-check's own keeper, a child of the suite).
            for _ in range(40):
                if not pid_running(pid):
                    break
                time.sleep(0.05)
            outcome = "still alive after SIGTERM" if pid_running(pid) else "ended"
            ended.append({"role": label, "pid": pid, "outcome": outcome})
            if outcome != "ended":
                failed.append(ended[-1])
        if getattr(args, "restart", False):
            # After the hand that acts: ask for the watcher and the keeper back. Only when
            # something was actually applied — a declined or unconfirmed fix changed nothing,
            # and a start on top of that would be a change nobody approved.
            restarted = restart_claims(args)
    code = EX_CODES["ok"]
    if reason == "confirmation required":
        code = EX_CODES["noinput"]
    elif reason == "declined" or failed:
        code = 1
    if args.json:
        doc = {
            "version": VERSION, "scratch": SCRATCH,
            "dry_run": bool(args.dry_run), "applied": applied, "reason": reason,
            "planned": {
                "clears": [{"role": label, "path": entry["path"]}
                           for label, entry in clears],
                "kills": [{"role": label, "pid": proc["pid"]} for label, proc in kills],
                "skip": [{"role": label, "pid": proc["pid"]} for label, proc in skipped],
            },
            "cleared": cleared, "ended": ended,
            "restarted": restarted,
            "claims": [{"role": label, **entry} for label, entry in rows],
        }
        json.dump(doc, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        print("fbtodo locks --dry-run  " + SCRATCH if args.dry_run
              else "fbtodo locks --fix  " + SCRATCH)
        for label, entry in clears:
            name = os.path.basename(entry["path"])
            if not applied:
                print(f"  would clear  {label:<14} {name:<23} a free record — dead weight")
                continue
            got = next((c for c in cleared if c["role"] == label), {})
            tail = "dead record gone; the next ask re-claims" if got.get("removed") \
                else "a claim appeared — left alone"
            print(f"  {'cleared' if got.get('removed') else 'left   '}  {label:<14} "
                  f"{name:<23} {tail}")
        for label, proc in kills:
            if not applied:
                print(f"  would end    {label:<14} pid {proc['pid']} (SIGTERM) — its claim "
                      "lives on an unlinked file")
                continue
            got = next((e for e in ended if e["pid"] == proc["pid"] and e["role"] == label),
                       {})
            print(f"  {'ended' if got.get('outcome') == 'ended' else 'failed':<14} "
                  f"{label:<14} pid {proc['pid']}  ({got.get('outcome')})")
        for label, proc in skipped:
            why = ("its environment came back clipped at the kernel's ~1 KB copy"
                   if proc.get("env_clipped") else "its environment could not be read")
            print(f"  skip         {label:<14} pid {proc['pid']} — {why}, so this root is "
                  "only assumed (a sentence, not a kill)")
        if getattr(args, "restart", False):
            if restarted:
                for role in ("watcher", "keeper"):
                    info = restarted.get(role, {})
                    pid = info.get("pid")
                    if info.get("error"):
                        print(f"  restart      {role:<14} not attempted ({info['error']})")
                    elif pid and info.get("before") == pid:
                        print(f"  watching     {role:<14} pid {pid}  (already running)")
                    elif pid:
                        print(f"  restarted    {role:<14} pid {pid}  — re-claimed")
                    else:
                        print(f"  restart      {role:<14} not running after the ask")
            elif args.dry_run:
                print("  would restart watcher and keeper through the normal ask")
            else:
                print("  restart      skipped — nothing was changed")
        if not (clears or kills or skipped or getattr(args, "restart", False)):
            print("  nothing to fix")
    if reason == "confirmation required":
        print(f"fbtodo locks --fix: refusing to end {len(kills)} untied process(es) without "
              "confirmation — rerun with --yes, or --dry-run to see the plan",
              file=sys.stderr)
        return code
    if reason == "declined":
        print("fbtodo locks --fix: nothing changed", file=sys.stderr)
    return code


def cmd_status(args) -> int:
    if getattr(args, "watch", False):
        return watch_keeper(args)
    cwd = os.path.realpath(os.getcwd())
    inst, inst_cwd = find_instance(cwd, args.watch_pid, args.instance_of)
    # The watcher's claim is READ, not probed: `daemon_pid()` is deliberately destructive
    # (its free-claim answer removes a leftover record), and `status` only looks. The row
    # and `--json` number are unchanged — a held claim names the record's pid, a free one
    # says "not running" — but a leftover `fbtodo-daemon.pid` now survives the run: it is
    # what `_claim_live` decides the state root from, and a look that erased it would
    # erase the fact it reports on (found in `doctor` first, 2026-10-02). Same read as
    # that row (`lock_peek`): open, try the lock, no unlink and no write anywhere.
    held, pid = lock_peek(LOCK_PATH)
    pid = pid if held else None
    st = read_json(STATE_PATH, None)
    view = st
    read_theme()  # the palette resolves its refusals here, so both branches can report them
    if args.json:
        json.dump(
            {
                "instance_pid": inst,
                "instance_cwd": inst_cwd,
                "watcher_pid": pid,
                "keeper": keeper_server_view(),
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
    # and its watcher are the same build, and a stale stamp is the state a watcher wrote
    # before it reloaded itself (`daemon_loop`) — or, for one whose build is gone, before
    # the next start replaces it (`live_watcher_pid`).
    watching = (st or {}).get("tool_version")
    stale_note = (
        f"  (watcher {watching} — stale; it reloads itself, or the next start replaces it)"
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
    seen = {label: text for label, text, _sev in patch_row(local_patch_alert())}
    print(f"  cli patches       : {seen.get('PATCH') or '— nothing logged'}")
    print(f"  last alert        : {seen.get('ALERT') or '— nothing logged'}")
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
            # The boundary guard: a turn that changed files without re-publishing the list
            # has not finished, whatever the ticks say.
            gap = unlisted_note(view)
            if gap:
                print(f"  agent             : steps still open — {gap}")
            else:
                print("  agent             : turn ended — waiting for you")
        else:
            print("  agent             : working (the list is not the finish line)")
    # The FINISH push, in the same spirit — and the one notify row that is not a cadence:
    # the finish bell is asked by the pane that watched a list finish (and, for a CLI
    # session, by the shell wrapper's timer every 5s), so what belongs in a status line is
    # the last thing it decided. That row is where "the list finished and my phone stayed
    # quiet" is answered: a decision that sent nothing never reaches phone.log, and the bell
    # writes its own record only when the asker asks for it (the pane does).
    if not os.path.exists(TODO_NOTIFY):
        print("  finish push       : none installed (~/.config/freebuff-notify/todo-bell.py)")
    else:
        last = last_finish()
        if last:
            age = short_duration((last.get("age_s") or 0) * 1000)
            print(f"  finish push       : {last['text']}  — {f'{age} ago' if age else 'just now'}")
        else:
            print(f"  finish push       : nothing recorded yet ({FINISH_LOG})")
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
        # In plain words: which Python is that pane actually on? The pane and the watcher are
        # the two that have to agree, and a pane rendering under one Python while its watcher
        # writes under another is a difference nobody can see from the outside — which is how
        # it went unnoticed until one was found by hand (2026-10-01: a pane respawned by tmux
        # on `/usr/bin/python3` 3.9.6 beside a watcher on Homebrew's 3.14, because tmux
        # rebuilds a pane's PATH and the pane command used to be resolved through it). Both
        # are read from the processes themselves (`pane_python`, `python_of`), never assumed,
        # and the pin's own job is to make this line boring: `same as the watcher`. When they
        # do differ the keeper reopens the pane on its pin (see `repair_drifted_panes`), so
        # the line reports the disagreement rather than asking the reader to fix it.
        table = process_table()
        pane_py = pane_python(mine[0], table=table) if mine else None
        watch_py = python_of((table.get(pid) or (0, ""))[1]) if pid else None
        if pane_py and watch_py:
            note = (
                "same as the watcher"
                if os.path.realpath(pane_py) == os.path.realpath(watch_py)
                else f"watcher {_short_python(watch_py)} — they differ; the keeper puts the pane back on its pin"
            )
            print(f"  pane python       : {_short_python(pane_py)}  ({note})")
        elif pane_py:
            print(f"  pane python       : {_short_python(pane_py)}")
        # ...and which of those panes was captured BEFORE this build's pin. The keeper's
        # upgrade would rewrite it on the next pass, so this is the line that names the
        # pane while it is still what it was — and, once the repair is off for it, the line
        # that keeps naming it, because nothing else will change it.
        pin_note = stale_pin_note(stale_pane_ids(inst))
        if pin_note:
            print(f"  pane pin          : {pin_note}")
    # The keeper's server, in the same shape as the pane python line above: the name its
    # record actually holds, canonicalised (a record written by an older build spells the
    # socket as the raw `TMUX` value — a spelling, not a different server), and whether
    # the asks that can replace it agree with it. The pane's ask is the name an ask from
    # this context writes, and it is only there when this IS a pane (`TMUX` is set); the
    # watcher's is the ask that spawns a watcher, made outside tmux (the shell autostart,
    # the desktop integration; `tmux_identity_outside`). A disagreement is the churn
    # itself: the next ask from that side kills this keeper and starts its own. Read,
    # never taken: `status` must not remove a leftover record (the doctor learned that
    # the hard way).
    keeper = keeper_server_view()
    if not keeper["running"]:
        print("  keeper server     : — none running")
    else:
        print(f"  keeper server     : {keeper['server']}  ({keeper['note']})")
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


# The note a pane leaves for its own replacement across a reload (`cmd_pane`). The old image
# cannot know the new build's `VERSION` — that is in code it never imported — so it leaves its
# own behind in the environment, which along with the open fds is what an exec carries, and the
# image on the other side says what it is running. `RELOAD_NOTE_S` is how long the title chip
# carries it: long enough to catch from the corner of an eye, short enough that the chip is the
# pane's own name again before anyone could read it as state. The file that changed rides the
# same hand-over for the log rather than the chip.
PANE_RELOAD_ENV = "FBTODO_PANE_RELOADED"
# What a pane has latched itself to (`cli:<chat dir>` or `desktop:<thread id>`), carried in
# the environment so it survives the exec a reload does. See `latch_pane_subject`.
PANE_LOCK_ENV = "FBTODO_PANE_LOCK"
RELOAD_NOTE_S = 5.0


def reload_note(raw: str | None) -> str | None:
    """What a pane that just re-exec'd says on its title chip: `RELOADED 4.30.2 → 4.30.3`.

    In plain words: which build is this now? The hand-over is JSON (`{"from": …, "file": …}`)
    and one that does not parse is no note rather than a traceback — a pane's job is the list,
    and a garbled variable is not worth taking it down. The build the pane came FROM is named
    only when it differs from the one it is on: an edit between releases still reloads, and
    there the chip just says which build it is now.
    """
    if not raw:
        return None
    try:
        was = json.loads(raw)
        old = str(was.get("from") or "")
    except (ValueError, AttributeError):
        return None
    if old and old != VERSION:
        return f"RELOADED {old} → {VERSION}"
    return f"RELOADED {VERSION}"


def reopen_note(note: dict | None) -> str | None:
    """What a pane says on its title chip about what the keeper saw: `REOPENED (was on …)`,
    or `KEPT (was on …)` when the repair is turned off for it (`@fbtodo_repair`).

    In plain words: the keeper's finding was loud in its log and silent in the pane — the
    wrong way round, because the person watching is looking at the PANE when its process is
    replaced under them. The note cannot ride the environment the way a reload's does
    (`respawn-pane` starts a fresh command with none of ours), so the keeper leaves it in a
    file and the pane claims it by its own id, `TMUX_PANE`: at startup for a respawn, and on
    its own poll for a `kept` note — nothing restarted that pane, so nothing else would ever
    deliver it. It names the interpreter the pane — or a child of it — was on, shortened the
    way `status` shortens one, since that is the whole reason the repair exists; a note
    somehow missing its name still says which act it was.
    """
    if not note:
        return None
    kept = (note.get("kind") or "reopen") == "kept"
    was = str(note.get("was") or "")
    if not was:
        return "KEPT (repair off)" if kept else "REOPENED ON THE PIN"
    who = "a child was on" if note.get("child") else "was on"
    return f"{'KEPT' if kept else 'REOPENED'} ({who} {_short_python(was)})"


def pane_lock_subject(args) -> str:
    """The subject a set of pane ARGUMENTS already names, as a record string, or "".

    A reloaded pane applies its lock from the environment before its first poll (`cmd_pane`),
    so it knows what it holds without latching again — and the record it left under its OLD
    pid is about a process that has just exec'd away. Recording it under the new pid is how
    the subject survives a reload in a form a reader outside the process can find; "" is a
    pane that has not locked yet, which records nothing.
    """
    source = source_kind(getattr(args, "source", "auto"))
    if source == "cli" and getattr(args, "chat", None):
        return f"cli:{args.chat}"
    if source == "desktop" and getattr(args, "thread", None):
        return f"desktop:{args.thread}"
    return ""


def cmd_pane(args) -> int:
    cwd = os.path.realpath(os.getcwd())
    if not args.no_daemon:
        ensure_daemon(args)
    if not sys.stdout.isatty() and not args.once:
        print("refusing to poll without a TTY (use `fbtodo snap`)", file=sys.stderr)
        return EX_CODES["usage"]
    # A pane that re-execs itself (the reload probe below) comes back LOCKED. The latch
    # lives in this process's parsed arguments, and an exec throws those away with
    # everything else in memory, so a lock that did not ride out would be the one thing a
    # reloaded pane forgot — which is how a pane would start following tabs again, silently,
    # the first time you edited the code. It travels in the environment the way the
    # hand-over note already does, and is applied before the first poll so the new image
    # never gets a chance to choose.
    carried = os.environ.get(PANE_LOCK_ENV, "")
    kind, _, what = carried.partition(":")
    if what and kind in ("cli", "desktop"):
        args.source = kind
        if kind == "cli":
            args.chat = what
        else:
            args.thread = what
        # ...and marked as already decided, or the first poll would latch onto whatever the
        # new image happens to resolve and quietly undo the lock it was given.
        args.pane_locked = True
        # The record follows the reload: the pid is new, so the old process's record names a
        # process that no longer exists (`pane_subject_write`). A one-shot is not a pane on
        # screen and leaves nothing behind.
        if not args.once:
            pane_subject_write(pane_lock_subject(args))

    # ...and the pane a DESKTOP APP opened belongs to a desktop thread from the start. The
    # app hands its terminals no thread id (see `pane_place_subject`), so the thread this pane
    # is drawn beside is resolved here — once, before the first poll — which is also what
    # replaces a carried `cli:` lock that `auto` chose for a pane that is not a CLI pane.
    # A person who named the subject in this pane's own command line keeps it.
    if not args.once and not pane_subject_from_argv():
        placed = apply_pane_subject_lock(args, pane_place_subject(args, cwd))
        if placed:
            pane_log(f"pane {placed} (its own terminal)")

    color = use_color()
    # A PANE is one window on one list, so it asks for one thread from the first poll —
    # before there is anything to latch to. The stacked frame (`--threads N`, one heading
    # per live thread) was the other half of the same problem: a reader asking "how far is
    # this list" got an answer about three, and which three changed while they read. The
    # reads keep the flag (`fbtodo json`, `snap`, `board` still stack on request); a pane
    # does not, because it is the window a person is looking at.
    args.threads = 1
    hide = False
    last_sig = None
    last_draw = 0.0
    last_frame = None
    tick = max(1.0, args.tick)
    stale_after_s = max(0.0, args.stale_after) * 60.0
    last_act = None
    last_activity = time.time()
    # The finish push's memory (`finish_notify`): which list this pane last saw with work
    # still in it, and which ones it has already asked the finish bell about. The bell owns
    # the decision and the one-push-per-list record; this pair is only what keeps a pane from
    # asking about a finish nobody watched — a pane opened on work that was already over has
    # nothing recorded here, and says nothing.
    watched_open = None
    finish_asked = set()
    # The pane's own switch over the phone. Nothing else reads this terminal — it IS the
    # pane's tmux pane — so taking it for single keystrokes costs nobody else anything, and
    # `PaneKeys` hands the attributes back on every way out of this function (see `restore`
    # and the `finally`). Off for a pane that is not a pane (`--once`) and for a pane whose
    # owner said so (`--no-keys`, or FBTODO_PANE_KEYS=off, the flag winning).
    keys_on = not (args.once or getattr(args, "no_keys", False)
                   or str(os.environ.get("FBTODO_PANE_KEYS", "")).strip().casefold()
                   in ("off", "0", "no", "false", "disable", "disabled"))
    # ...and the MOUSE, which is the other half of the same control: the frame's button is
    # clicked, and a pane that cannot be clicked still names its key. Its own switch, because
    # taking the mouse changes what the terminal does with it — a click is a selection no more
    # — so a reader who wants their mouse back can have it and keep the keys (`--no-mouse`,
    # FBTODO_PANE_MOUSE=off). Taken only when there is a terminal to give it back to: a pane
    # whose stdin is not a TTY would otherwise capture the mouse and never read a click, which
    # is the one combination nothing on this machine could recover from.
    mouse_wanted = keys_on and not (
        getattr(args, "no_mouse", False)
        or str(os.environ.get("FBTODO_PANE_MOUSE", "")).strip().casefold()
        in ("off", "0", "no", "false", "disable", "disabled"))
    keys = PaneKeys(enabled=keys_on)
    keys.open()
    mouse = mouse_wanted and keys.fd is not None
    # ...and the note a keypress leaves on the chip: the switch itself is said for as long
    # as it is true (`mute_chip`), so this is the transient half — the words of what the key
    # did, and the only word there is when a key was pressed and NOTHING happened (a switch
    # that could not be written), which is exactly the case that would otherwise look broken.
    key_note = None
    key_note_until = None
    # Poll and paint run on separate clocks. A poll is a store read and costs something,
    # while a repaint is free, so the pane wakes often enough to move its own numbers at
    # 1Hz and only reaches for the store every `args.interval`. Sleeping the whole gap is
    # what made a pane look stuck: its clock and its "N ago" could not move until the next
    # poll had come back.
    WAKE = 0.25
    next_poll = 0.0
    # When this pane next asks whether it is still the build on disk — `BUILD_CHECK_S`, the
    # clock the watcher's own reload runs on too (`daemon_loop`).
    next_build_check = 0.0
    held_note = None
    # A pane that re-exec'd itself left the note for its replacement (see the build check
    # below). It is shown on the title chip until `reloaded_until`, then the chip is the
    # pane's own name again — and the variable is popped whatever it says, so nothing this
    # pane starts inherits a note about a reload that is long over. The clock is armed by the
    # first frame the note can be painted into, not here: a reload can begin with a slow poll
    # (a slow store read), and a note that expired while waiting for the state
    # is a note nobody sees.
    reloaded = reload_note(os.environ.pop(PANE_RELOAD_ENV, None))
    # ...and the other note a pane can start with: the keeper reopened this one because its
    # Python was not its watcher's, and it left the reason in a file (a respawn carries no
    # environment of ours — see `pane_note_take`). The pane's own id is how the note finds it.
    keeper_note = reopen_note(pane_note_take(os.environ.get("TMUX_PANE")))
    reloaded_until = None
    keeper_note_until = None
    # ...and the note with no restart behind it: a pane the keeper KEPT as it is — the repair
    # is off for it (`@fbtodo_repair`) — can only be told on the chip of the process already
    # running, so this pane asks the note file itself (the poll in the loop below), and only
    # for `kept`: a `reopen` note belongs to the process that will replace this one.
    next_note_check = 0.0
    state = watching = inst = None
    # The last failure this pane's own tick hit, so a build that breaks every tick logs once
    # rather than once a second (see the poll's `except`): the frame still shows it.
    poll_note = None
    pane_lock_note = ""

    def restore_terminal() -> None:
        """The cursor and the mouse back: on every way out of this loop, signals included.

        The cursor is shown on STDERR rather than stdout, the way this pane has always done it:
        a pane can be ending because its stdout is the thing that went away, and what has to be
        left as it was found is the TERMINAL. The mouse is given back in the same breath and for
        the sharper version of the same reason: a terminal left in mouse-reporting mode cannot
        select text in any window until something restarts it, so this write is the pane's way
        of not being the program that did that.
        """
        if hide:
            sys.stderr.write("\x1b[?25h")
        if mouse:
            sys.stderr.write(MOUSE_OFF)
        sys.stderr.flush()

    def restore(*_a):
        restore_terminal()
        keys.close()
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
        # ...and the two things this pane claims about the TERMINAL rather than about the list
        # ride out with every frame: the cursor stays hidden and the mouse stays reported.
        # Asked for once at startup, both are lost to anything that resets the terminal under a
        # RUNNING pane — a desktop window reloaded (a fresh emulator, with no mouse mode and a
        # cursor back), a soft reset, a pane re-attached by hand — and a pane whose mouse mode
        # was reset underneath it is a pane whose button is dead until someone restarts it,
        # which is the one failure a reader cannot see the cause of. Sixteen bytes a frame asks
        # again; asking twice is what this sequence already is on a terminal that heard it.
        marks = (("\x1b[?25l" if hide else "") + (MOUSE_ON if mouse else ""))
        sys.stdout.write(marks + out)
        sys.stdout.flush()

    try:
        if not args.once:
            sys.stdout.write("\x1b[?25l")
            if mouse:
                sys.stdout.write(MOUSE_ON)
            sys.stdout.flush()
            hide = True
        while True:
            now = time.time()
            # In plain words: am I still the code that is there? A pane keeps the build it
            # imported, and an upgrade — a release, a `git pull`, an edit under the pane's own
            # feet — used to leave the list drawn by the OLD build until someone respawned the
            # pane by hand; a pane found running a day-old build is why `status` prints a
            # `tool version` beside the watcher's at all. Nothing re-imports a running Python
            # process, so the pane starts itself over instead. The exec keeps the pane, its
            # pid and its tty — only the code changes — and it is deferred while the sources
            # do not parse, so a half-saved file cannot take the pane away from the person
            # who is editing it. The same guard covers a tree that PARSES but will not become
            # a process (`reload_probe_error`): a pane that execs into a build which dies at
            # import is a pane the keeper then reopens, into the same broken tree, over and
            # over — so an unfit build is one the pane holds on, not one it becomes.
            if not args.once and now >= next_build_check:
                next_build_check = now + BUILD_CHECK_S
                changed = source_newer_than(STARTED_AT, now)
                if changed:
                    broken = source_syntax_error()
                    unfit = broken or reload_probe_error()
                    if unfit:
                        if unfit != held_note:  # once per distinct problem, not once a second
                            held_note = unfit
                            pane_log(
                                f"pane holding, source does not parse: {unfit}" if broken
                                else f"pane holding, the new build does not load: {unfit}",
                            )
                    else:
                        # The cursor back on first — and the mouse handed back with it: the exec
                        # replaces this process, so a build that fails to import should leave a
                        # readable pane behind it rather than an invisible one holding a mouse
                        # that nothing is listening to.
                        sys.stdout.write("\x1b[?25h")
                        if mouse:
                            sys.stdout.write(MOUSE_OFF)
                        sys.stdout.flush()
                        sys.stderr.flush()
                        pane_log(f"pane reloading: {changed} changed after this process started")
                        # ...and what the next image cannot know: this one's VERSION. The
                        # environment is what an exec carries besides the open fds, so the
                        # hand-over rides there and the new chip can name the build it came
                        # from — and the file, which the log line above already has.
                        os.environ[PANE_RELOAD_ENV] = json.dumps({"from": VERSION,
                                                                  "file": changed})
                        argv = self_argv()
                        try:
                            os.execv(argv[0], [*argv, *sys.argv[1:]])
                        except OSError as exc:
                            os.environ.pop(PANE_RELOAD_ENV, None)
                            pane_log(f"pane reload failed: {exc.__class__.__name__}")
            if state is None or now >= next_poll:
                # A poll that RAISES must not take the pane with it. The pane is the one
                # process whose whole job is to be on the screen, and it re-execs itself into
                # whatever is on disk — including a tree that PARSES and IMPORTS and is still
                # internally inconsistent, which is what a half-finished edit looks like to
                # `reload_probe_error` (measured 2026-10-03: an edit that changed a signature
                # and its call site in two steps exec'd the pane into the between-state, and
                # the TypeError took the pane down where the list was, mid-work). A failed
                # poll costs one tick; dying costs the pane, and for a kept pane the keeper
                # reopens it into the same tree. So the exception becomes the FRAME: logged
                # once per distinct message, drawn on the error row the renderer already has,
                # and retried on the next tick — which is also how a pane left running a
                # broken build comes back by itself the moment that build is fixed. Only
                # `Exception` is caught: Ctrl-C and the exit path must still restore the
                # cursor and the terminal.
                try:
                    inst, _ = find_instance(cwd, args.watch_pid, args.instance_of)
                    st = read_json(STATE_PATH, None)
                    if pane_cached_state(st, args, inst, int(now * 1000)):
                        # Only this source's watcher state, so a pane asking for one store
                        # is not handed another's list because that one is fresher.
                        state = st
                        # The pane ACTS (it tracks tasks and may start a watcher), so the probe
                        # is allowed here: a leftover record must not be printed as the watcher
                        # serving this state. Look-only commands use `lock_peek`.
                        watching = st.get("daemon_pid") or daemon_pid()
                    else:
                        # ...and this half IS the re-resolve: the cached file was good except that
                        # its session has ended, so the answer comes from the stores themselves.
                        state = finish_probe(snapshot(args, cwd=cwd, instance_pid=inst))
                        # No watcher is serving THIS request (that is why we snapshotted), so name
                        # one only if the cached state actually describes it: a pane asking for
                        # another store must not be credited with the watcher's that is not its.
                        watching = (daemon_pid()
                                    if (st and state_matches_request(st, args)) else None)
                    if not state.get("error") and not state.get("task_times"):
                        # either no watcher, or one from before timings existed — track
                        # here so task clocks are never silently missing
                        state = track_tasks(state)
                    # ...and the pane decides ONCE which list it is about. Every poll after
                    # this one asks for that subject by name, so a tab change, a finished
                    # turn or a second thread going live can no longer move the reader's
                    # window out from under them (see `latch_pane_subject`).
                    locked_note = latch_pane_subject(args, state)
                    if locked_note and locked_note != pane_lock_note:
                        pane_lock_note = locked_note
                        pane_log(f"pane {locked_note}")
                except Exception as exc:
                    note = f"{exc.__class__.__name__}: {exc}"
                    if note != poll_note:  # once per distinct failure, not once a tick
                        poll_note = note
                        pane_log(f"pane poll failed: {note}")
                    state = pane_poll_state(state, note, args.source)
                    watching = None
                next_poll = time.time() + max(0.2, args.interval)
            now = time.time()
            if now >= next_note_check:
                next_note_check = now + 1.0
                polled = reopen_note(pane_note_take(os.environ.get("TMUX_PANE"),
                                                    kind="kept", sweep=False))
                if polled:
                    # a sighting with no respawn behind it: the keeper kept this pane as it
                    # is, and only the process already on screen can say so
                    keeper_note, keeper_note_until = polled, None
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
            # the pane's own switch over the phone (`mute.py`): one keystroke writes the
            # notify kit's two switch files and the record of what they were, and nothing
            # else in this loop changes because of it — so it is asked once per wake, which
            # costs one select() on a terminal nobody else is reading. One key per wake on
            # purpose: a paste of three switches is three half-seconds, which is no one's
            # hurry, and a tight loop over a keyboard is how a pane eats its own terminal.
            while keys.fd is not None:
                pressed = keys.poll()
                if not pressed:
                    break
                if isinstance(pressed, MouseClick):
                    # The pane's mouse answers ONE thing, and only inside the button's own cells:
                    # anywhere else a click is nothing at all, because this is a window on a list
                    # and not an editor — everything else the mouse could do here is something
                    # the terminal itself already does better.
                    span = ntfy_button_span(last_frame)
                    if not (span and span[0] == pressed.y
                            and span[1] <= pressed.x <= span[2]):
                        continue
                    key_chip, key_said = toggle_push(state or {})
                    where = f"button click at {pressed.x},{pressed.y}"
                else:
                    key_chip, key_said = apply_pane_key(pressed, state or {})
                    where = f"key '{pressed}'"
                if key_said:
                    pane_log(f"pane {where}: {key_said}")
                    # The standing chip is the better answer whenever it HAS one: a mute
                    # names itself and its undo key on every frame from here, and a chip
                    # that is clipped to make room for a note about itself says less. The
                    # note is for the cases the chip cannot — a key that did nothing at all.
                    if not key_chip:
                        key_note, key_note_until = key_said, now + RELOAD_NOTE_S
                    last_sig = None       # repaint this tick: the chip may have changed
            # ...and the default's other half. A mute taken "until this list finishes" ends
            # itself when the list does — in THIS pane or in the next one, since the record is
            # on disk (`MUTE_PATH`) and this pane re-execs into every new build it sees.
            lifted = auto_unmute(state)
            if lifted:
                pane_log(f"pane: {lifted}")
                key_note, key_note_until = lifted, now + RELOAD_NOTE_S
                last_sig = None
            # ...and the phone's other half, which no other process here can see: a finish
            # that happens in THIS pane's list. The session timer's bell follows a shell's
            # CLI session (`todo-bell.py <pid>`), and the desktop app's turns have no such
            # session at all — its own process runs them, and the only thing watching the
            # thread is the pane drawing it. So when this list finishes for good (every step
            # ticked AND the turn ended — `list_is_finished` and `list_has_ended`, the same
            # two halves the bell decides with) the bell is asked, once, and told which state
            # to decide about (`--state -`): the list on screen, not one re-resolved from a
            # pid nobody is looking at. Only a finish this pane WATCHED can ring, which is
            # why the list is remembered while it still has work in it: a pane opened on a
            # thread that was already over has nothing remembered and stays silent, and a
            # pane reloaded mid-turn remembers it again on its first poll.
            finish_key = (state.get("session"), state.get("list_id"))
            if finish_key[1] and not state.get("error"):
                if not list_is_finished(state) or not list_has_ended(state):
                    watched_open = finish_key
                elif finish_key == watched_open and finish_key not in finish_asked:
                    # Asked once per list whatever the answer: a bell that decides "no"
                    # (already pushed, nothing configured) is not going to decide "yes" on
                    # the next tick, and a pane asking every three seconds is a process a
                    # second for nothing. Nothing to ask at all (no kit installed) is spent
                    # the same way, once: a missing kit is a fact about the machine, not
                    # about the tick.
                    # One line per watched finish, saying what happened to it — the bell's own
                    # account of the decision when there was one ("no push: already pushed for
                    # this list"), or why there was nobody to ask. That line is the answer to
                    # "the list finished and the phone stayed quiet", and the bell keeps the
                    # same account in the kit's record (`FINISH_LOG`), which `fbtodo status`
                    # reads back for an operator who comes to the question later. Written once
                    # per list either way: a pane that could not ask must not say so every
                    # 0.2s, and a missing kit is a fact about the machine, not about the tick.
                    sent, said = finish_notify(state)
                    finish_asked.add(finish_key)
                    pane_log(f"pane: finish bell asked ({state.get('list_id')})"
                             + (f", exited {sent}" if sent
                                else (f": {said}" if said else "")))
            # What the chip says about the notifications right now, whatever put them there.
            quiet_chip = mute_chip()
            # ...and the BUTTON's own fact, which is a different switch from the one above: the
            # push alone (`toggle_push`). Read here and HANDED to the renderer rather than read
            # by it, because a frame is a function of its arguments — a golden file has no notify
            # kit behind it, and a frame that changed with somebody's phone would be no contract.
            push_on = not push_is_off()
            # the notes, while they last: the frame's own title chip says which build this
            # pane is now, or what the keeper saw of it and did about it — reopened it, or
            # kept it because repair is off — then goes back to the pane's name (see
            # `reload_note`, `reopen_note`). A keypress outranks the keeper's note, being
            # the newer thing the reader did; the mute itself is not a note at all, it is the
            # chip's standing text (`quiet_chip`) until the switch is lifted.
            if reloaded and reloaded_until is None:
                reloaded_until = now + RELOAD_NOTE_S
            if keeper_note and keeper_note_until is None:
                keeper_note_until = now + RELOAD_NOTE_S
            if key_note and key_note_until is None:
                key_note_until = now + RELOAD_NOTE_S
            note = (reloaded if reloaded_until and now < reloaded_until
                    else key_note if key_note_until and now < key_note_until
                    else keeper_note if keeper_note_until and now < keeper_note_until
                    else None)
            try:
                text = render(
                    state,
                    color,
                    watching=watching,
                    width=width_of_default(),
                    idle_s=idle_s,
                    stale_after_s=stale_after_s,
                    goal_lines=args.goal_lines,
                    height=_shutil.get_terminal_size(fallback=(80, 24)).lines,
                    reloaded=note,
                    mute_note=quiet_chip,
                    ntfy=push_on,
                )
            except Exception as exc:
                # ...and the drawing half of the same tick, for the same reason: a frame the
                # renderer cannot produce (the state is fine, the code is not) must leave a
                # readable line behind, not an empty pane and a traceback on its way out.
                note = f"{exc.__class__.__name__}: {exc}"
                if note != poll_note:
                    poll_note = note
                    pane_log(f"pane cannot draw its frame: {note}")
                text = f"fbtodo: cannot draw this frame — {note}"
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
                # ...and the note, so the chip goes back to the pane's own name on the tick
                # it expires rather than whenever the frame next moves
                note,
                # ...and the mute chip, for the same reason: a keypress changes no row of the
                # list, so without this the frame would sit there unchanged and the reader
                # would not learn that the phone just went quiet.
                quiet_chip,
                # ...and the button's own state, which is neither note nor chip: a click on it
                # changes no row of the list either, and the word on it has to follow the switch
                # on the same tick or the button would answer with the state it just left.
                push_on,
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
            # Nothing else ends a pane. Not an ended session, not an exited process.
            #
            # The pane's lifetime is its WINDOW's: Ctrl-C, the `--stale-after` line above, a
            # window that goes away, `--once`. Every earlier version asked the SESSION too and
            # closed on it — first the pid the pane had resolved, then, when that was found to
            # be wrong for `auto` (a pane drawing the app's thread while the CLI that opened it
            # had exited), the journal's own turn boundary. That second rule was closer, and
            # still wrong for the same reason the first was: what the pane draws is a LIST, and
            # a list whose session is over is a list somebody is still reading, finishing, or
            # waiting on. A pane that leaves on its own takes the reader's eyes with it at the
            # moment they are most likely to be looking — which is why "it keeps closing
            # mid-run" survived both fixes (2026-10-03, then 2026-10-04).
            #
            # `--wait` used to be the way to opt OUT of this (a pane whose real owner was the
            # shell around it). It no longer has to be, and it never said what the pane did
            # instead — so it is still accepted, as a no-op, because a command line that
            # already carries it must not start failing over a switch that now says what
            # every pane already does.
            time.sleep(min(WAKE, max(0.05, next_poll - time.time())))
    except KeyboardInterrupt:
        restore()
        return 0
    finally:
        restore_terminal()
        keys.close()
        # A pane that exits holding a mute leaves a phone that never rings again, and the
        # only key that fixes it is on the pane that just closed — so the exit gives the
        # notifications back (a finished list, a dead session, Ctrl-C: all three are the
        # read being over). `--once` never took a key, so it never hands one back.
        if not args.once:
            released = mute_release("the pane closed")
            if released:
                pane_log(f"pane: {released}")





def c_stale_notice(color: bool, idle_s: float, limit_s: float) -> str:
    return paint(color)(
        "2",
        f"todo list idle {short_duration((idle_s or 0) * 1000)} "
        f"(limit {short_duration(limit_s * 1000)}) — fbtodo pane closing",
    )


def cmd_mute(args) -> int:
    """`fbtodo mute` — the pane's notification switch, without a keystroke.

    The pane can mute its phone with `m`/`M`/`u` (`mute.py`), which is the fast way when
    you are looking at the pane and the wrong way for everything else: a script, a status
    row, another program's button, or a shell that is not a pane at all. So the same switch
    is a command here, with the same words and the same files underneath — there is no
    second implementation to drift.

    Four verbs, and the fourth is the shape of the first three: `list` (or no verb) reports
    what is in force, `on` mutes until told otherwise, `off` gives the notifications back,
    and `until-done` mutes until the list this directory is working on is finished. That last
    one is a PROMISE about a list, so it reads the list rather than assuming one: asked when
    the list is already finished it says so and leaves the phone alone, because a mute whose
    condition is already met is a mute nobody would have wanted. `until-done` is lifted by the
    pane that watches the list (`auto_unmute`), which means by the next pane to run if none
    is open now — and, unlike a mute a key took, it is NOT lifted by a pane merely closing:
    `mute_release(only_mine=True)` is the pane's promise to itself, not the person's.

    `--json` prints the same facts as a document (the switches with the word in each file
    and whether the environment is what holds it, the record, and the chip the pane would be
    showing). A switch that cannot be written is 73 — the file is the switch, and a switch
    that could not be written is not a mute.
    """
    verb = getattr(args, "mute_verb", None)

    def emit(state: dict, line: str) -> int:
        if args.json:
            json.dump(state, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
            return EX_CODES["ok"]
        print(line)
        return EX_CODES["ok"]

    if verb in (None, "list"):
        state = describe_mute()
        if not state["muted"]:
            return emit(state, "  notifications : on")
        scope = state["scope"] or "off (set outside this pane)"
        who = {"key": "a pane key", "command": "`fbtodo mute`",
               "button": "the pane's ntfy button"}.get(state["by"], "someone")
        detail = []
        for sw in state["switches"]:
            if sw["off"]:
                detail.append(f"{sw['what']} off" + (" by env" if sw["held_by_env"] else ""))
        return emit(state, f"  notifications : off — {scope}, by {who}"
                           f" ({'; '.join(detail) or 'nothing of ours'})")

    if verb == "off":
        rec = read_mute_record()
        if not rec.get("until"):
            # Nothing of ours to undo. Refusing here (rather than writing `on` over both
            # files) is the same rule the `u` key follows: a switch this pane never wrote is
            # not this command's to overwrite.
            state = describe_mute()
            if not state["muted"]:
                return emit(state, "  notifications : on  (nothing of ours to undo)")
        _chip, said = set_unmuted()
        if said.startswith("no mute"):
            return emit(describe_mute(), "  notifications : on  (nothing of ours to undo)")
        return emit(describe_mute(), f"  notifications : on  ({said})")

    # ...`on` and `until-done`: the switch itself. `until-done` is the one that has to know
    # which list it is promising about, so it reads the state the pane would be showing —
    # and only it does, because a full list read is not the price of turning a bell off.
    state = {}
    if verb == "until-done":
        cwd = os.path.realpath(os.getcwd())
        try:
            state = finish_probe(snapshot(args, cwd=cwd))
        except Exception as exc:                      # noqa: BLE001 — a report is not a crash
            print(f"fbtodo mute until-done: cannot read the list ({exc.__class__.__name__}) — "
                  "muted until you say otherwise with `fbtodo mute off`", file=sys.stderr)
            state = {}
        if state and not state.get("error") and list_is_finished(state):
            return emit(describe_mute(),
                        f"  notifications : on  (the list is already finished — nothing to "
                        f"wait for; `fbtodo mute on` if you meant it anyway)")
    _chip, said = set_muted("list" if verb == "until-done" else "sticky", state, by="command")
    if said.startswith("cannot write"):
        print(f"fbtodo mute: {said}", file=sys.stderr)
        return EX_CODES["cantcreat"]
    scope = "until this list finishes" if verb == "until-done" else "until `fbtodo mute off`"
    return emit(describe_mute(), f"  notifications : off — {scope} ({said})")


def cmd_snap(args) -> int:
    state = finish_probe(snapshot(args))
    if not state.get("error"):
        state = track_tasks(state)
    print(render(state, use_color(), width=width_of_default(), goal_lines=args.goal_lines))
    return 0


def cmd_json(args) -> int:
    st = read_json(STATE_PATH, None)
    state = st if cached_state_ok(st, args) else finish_probe(snapshot(args))
    if not state.get("error") and not state.get("task_times"):
        state = track_tasks(state)
    json.dump(state, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def cmd_bar(args) -> int:
    st = read_json(STATE_PATH, None)
    state = st if cached_state_ok(st, args) else finish_probe(snapshot(args))
    print(bar_text(state))
    return 0
def apply_pin(window: str) -> list[str]:
    """Put this window's list panes where the pin now says; their ids back.

    Run when a pin is set, so the effect is visible at once instead of on the next keeper
    pass, and with the same two halves the keeper enforces: the side the pane sits on, and
    the size it holds.
    """
    rows, table = pane_rows(), process_table()
    rects = pane_rects()
    layout = pane_layout(window)
    touched = []
    for inst, win in session_windows(rows, table).items():
        if win != window:
            continue
        inst_pane = freebuff_pane_id(inst, rows=rows, table=table)
        if not inst_pane:
            continue
        for pane in local_pane_ids(inst, rows, table):
            if place_pane_beside(pane, inst_pane, rects, layout["side"]):
                touched.append(pane)
                rects = pane_rects()
            if resize_pane_to(pane, rects, layout["side"], layout["size"]):
                touched.append(pane)
                rects = pane_rects()
    return touched

def pane_reason(pane, window: str | None, anchor: str | None, rects: dict,
                layout: dict, anchor_note: str) -> dict:
    """What decided one list pane's place: the rule, the anchor, and the numbers.

    Four answers, always the same four, because that is what a person asks about a pane:
    which pane; where it should be (the anchor it is placed against, and the side
    that decides which edge of it); what side and size are in force and which source
    supplied them; and whether it is actually there.
    """
    here = rects.get(pane) if pane else None
    there = rects.get(anchor) if anchor else None
    # A pane in ANOTHER window than its session's is an arrangement the owner made, and both
    # the keeper and the watcher leave it alone — so a report that called it misplaced would
    # be sending somebody to fix what is not broken (`place_pane_beside`).
    elsewhere = bool(here and there and here["window"] != there["window"])
    # The side as it stands for this pane, not only as the file remembers it: a pane kept on
    # the other axis of its session reads `seen` until the keeper has filed it (kept_layout).
    kept = kept_layout(pane, anchor, rects, layout) if pane else layout
    placed = bool(pane and anchor) and placed_beside(anchor, pane, rects, kept["side"])
    return {
        "pane": pane,
        "elsewhere": elsewhere,
        "window_id": window,
        "window": layout["window"] or (window or ""),
        "anchor": anchor,
        "anchor_note": anchor_note,
        "side": kept["side"],
        "side_source": kept["side_source"],
        "size": layout["size"],
        "size_source": layout["size_source"],
        "placed": placed,
        "now": (f"L{here['left']} T{here['top']} {here['width']}x{here['height']}" if here else ""),
        "expected": (f"below {anchor}" if kept["side"] == "v" else f"beside {anchor}")
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
        layout = pane_layout(window, pins, last)
        anchor = freebuff_pane_id(inst, rows=rows, table=table)
        starts = {row["pane"]: row["start"] for row in rows}
        # The pin to judge by is the SESSION's, read from its own process — not this shell's.
        # `why` can be typed in any shell, and a pane the keeper would leave alone must not
        # read as stale just because that shell spells a value differently (`session_pinned_env`).
        wanted = session_pinned_env(inst, rows, table)
        panes = local_pane_ids(inst, rows, table) or [None]
        for pane in panes:
            rec = pane_reason(pane, window, anchor, rects, layout, f"freebuff pid {inst}")
            # A pane captured before this build's pin: the keeper's upgrade will rewrite its
            # recorded command on the next pass, so `why` says so rather than leaving the
            # reader to wonder why a pane it described a moment ago is suddenly different.
            rec["stale_pin"] = bool(pane and stale_pin(starts.get(pane, ""), wanted))
            records.append(rec)
    return records


def cmd_panes(args) -> int:
    """Every todo pane on this machine, with the subject it holds and the interpreter it runs.

    In plain words: `why` answers where ONE session's list pane is, for a window the reader
    chose; this answers which todo panes exist at all, across every window and every session —
    including the ones OUTSIDE tmux, which no `list-panes` can name. Each row is a pane
    process (`todo_panes`): its tmux pane and window when it has them, else its controlling
    terminal, which is how the desktop app's panes are named; the subject it latched itself to
    (`cli:<chat>` / `desktop:<thread>`, read from the record the pane writes — see
    `pane_subject_write`); and the interpreter its own command line names. `--json` is the same
    document for a script; `--quiet` prints nothing.
    """
    rows, table = pane_rows(), process_table()
    found = todo_panes(rows=rows, table=table)
    names = {}
    for row in rows:
        if row["window"] and row["window"] not in names:
            names[row["window"]] = window_key(row["window"]) or ""
    panes = [
        {**rec, "window_name": names.get(rec["window"] or "") or None} for rec in found
    ]
    if args.json:
        json.dump({"panes": panes, "count": len(panes)}, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    if args.quiet:
        return 0
    if not panes:
        print("no todo pane on this machine")
        return 0
    c = paint(use_color())
    print(f"{c('1', 'fbtodo panes')}  {len(panes)} todo pane(s)")
    for rec in panes:
        where = rec["window_name"] or rec["pane"] or rec["tty"] or "—"
        hold = subject_note(rec["subject"]) if rec["subject"] else "— (not locked yet)"
        python = _short_python(rec["python"]) if rec["python"] else "— not known"
        print(f"  {rec['pane'] or '—':<6} {where:<12} pid {rec['pid']:<7} {hold}"
              f"  python {python}")
    return 0


def cmd_why(args) -> int:
    """Say why every list pane is where it is — the rule, the anchor, and the numbers.

    The pane keeper moves panes and opens them, and both answer to the same
    four things: the window's pin, what the pane was left at last time, FBTODO_SPLIT / FBTODO_PANE_SIZE, and the built-in default.
    Which one won is invisible in a layout that merely looks right — and the first question
    when one is wrong — so this prints, per pane, the side and size in force WITH the source
    that supplied them, the pane it is placed against, and whether it is actually there.
    A pane the owner has moved to the other axis of its session is not one of the four
    sources winning but a decision being kept: it reads `seen (kept, not filed yet)` until
    the keeper has seen it twice and written it down as the remembered side (`kept_layout`).
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
        print(f"{rec['window']:<12} {rec['pane'] or '(none)':<6} {rule}")
        print(f"{'':<12} {'':<6} {tail} — {rec['anchor_note']}")
        if rec.get("stale_pin"):
            print(f"{'':<12} {'':<6} on an OLDER PIN than this build writes — the keeper "
                  "reopens it on the current one (`@fbtodo_repair off` keeps it)")
    bad = [
        rec["pane"] for rec in records
        if rec["pane"] and not rec["placed"] and not rec["elsewhere"]
    ]
    stale = [rec["pane"] for rec in records if rec.get("stale_pin")]
    print(
        f"{len(records)} list pane(s), {len(bad)} misplaced"
        + (f": {' '.join(bad)} — tell the keeper to move them: fbtodo pane-watch --once" if bad else "")
        + (f"; {len(stale)} on an older pin: {' '.join(stale)}" if stale else "")
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

    Bare `fbtodo pin` reports what this window is set to and where each half came from
    (a pin, the remembered size, FBTODO_SPLIT/FBTODO_PANE_SIZE, or the default);
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
            # A pin written while there were two list panes per window filed its answer
            # under the role that owned it; `local` is this window's only pane now, so the
            # half is reported with the flat one rather than dropped.
            scoped = entry.get("local")
            scoped = scoped if isinstance(scoped, dict) else {}
            flat = {f: entry[f] for f in ("side", "size") if entry.get(f)}
            flat.update({f: scoped[f] for f in ("side", "size") if scoped.get(f)})
            got = [f"{f}={flat[f]}" for f in ("side", "size") if flat.get(f)]
            print(f"{key:<16} {' '.join(got) or '-':<16} {state}")
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
    if args.clear:
        had = key in pins
        pins.pop(key, None)
        save_pins(pins)
        apply_pin(window)
        if not args.quiet:
            print(f"{key}: {'pin cleared' if had else 'nothing was pinned'}")
        return 0
    asked = args.side is not None or args.size is not None
    target = entry
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
    if asked:
        if entry:
            pins[key] = entry
        else:
            pins.pop(key, None)
        save_pins(pins)
    layout = pane_layout(window, pins)
    touched = list(dict.fromkeys(apply_pin(window))) if asked else []
    if args.json:
        json.dump(
            {
                "window": key, "pin": entry, "layout": layout,
                "side": layout["side"], "size": layout["size"], "panes": touched,
            },
            sys.stdout, ensure_ascii=False,
        )
        sys.stdout.write("\n")
        return 0
    if args.quiet:
        return 0
    lines = [f"{key}  ({'pinned' if entry else 'nothing pinned'})"]
    lines.append(
        f"  side={layout['side']} ({source_note(layout['side_source'], key)})"
        f"  size={layout['size']} ({source_note(layout['size_source'], key)})"
    )
    if touched:
        lines.append(f"  list pane {', '.join(touched)} re-placed")
    print("\n".join(lines))
    return 0


# ======================================================================== main
def cmd_dead(args) -> int:
    """List every stretch of code this build's own source proves can never run.

    In plain words: the reload probe holds a rebuild the moment `unreachable_code` names one
    dead branch, but it only names the FIRST — enough to refuse the build, not enough to fix
    it. This is the same two passes over the whole package with every finding kept, so the
    shape of the problem is visible in one look: a statement stranded after a `return`, a
    constant-false branch, a comparison of literals that is false, or a branch an earlier
    guard (or `assert`) already settled. `--json` is the same rows for a script, and the exit
    is nonzero when anything is found — the same fact the probe acts on.
    """
    findings = dead_code()
    if args.json:
        json.dump({"version": VERSION, "count": len(findings), "findings": findings},
                  sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return EX_CODES["ok"] if not findings else EX_CODES["ex_software"]
    if not findings:
        if not args.quiet:
            print(f"fbtodo dead  {VERSION}: nothing unreachable")
        return EX_CODES["ok"]
    plural = "" if len(findings) == 1 else "s"
    print(f"fbtodo dead  {VERSION}  ({len(findings)} finding{plural})")
    for found in findings:
        print(f"  {found['path']}:{found['line']}")
        print(f"      {found['why']}")
    return EX_CODES["ex_software"]


def build_parser():
    ap = argparse.ArgumentParser(
        prog="fbtodo",
        description="Watch the Freebuff agent's todo list.",
        add_help=False,
    )
    ap.add_argument("command", nargs="?", default="pane",
                    choices=["pane", "snap", "json", "bar", "daemon", "stop", "status",
                             "prune", "pane-watch", "pin", "why", "panes", "ledger", "doctor",
                             "push", "init", "keep", "locks", "dead", "board", "mute"])
    ap.add_argument("verb", nargs="?", default=None, metavar="VERB",
                    help="the command's own verb (omitted: print what is in force): "
                         "keep: on resumes the pane-repair, off keeps a drifted pane as it "
                         "is, default removes the choice again; "
                         "mute: on/off switch the notifications (see `fbtodo mute --help`)")
    ap.add_argument("--pane", dest="keep_pane", metavar="ID",
                    help="keep: the pane whose knob to read or set (default: the pane you are in)")
    ap.add_argument("--server", dest="keep_server", action="store_true",
                    help="keep: apply to the whole tmux server instead of one pane")
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
    # --- the board: every live session in one pane. The three knobs are the same questions
    # the single-session pane answers with `--threads` / `--thread-live`, asked about the
    # machine instead of one store: how many sessions to draw, how quiet one may be and still
    # count as live, and how many of its steps to show.
    ap.add_argument("--board-max", type=int, default=BOARD_MAX, metavar="N",
                    help=f"board: how many live sessions to draw (default {BOARD_MAX})")
    ap.add_argument("--board-live", type=int, default=BOARD_LIVE_MS // 60_000, metavar="MIN",
                    help="board: how quiet a session's store may be and still count as live "
                         f"(default {BOARD_LIVE_MS // 60_000} minutes; 0 = no window)")
    ap.add_argument("--board-rows", type=int, default=BOARD_ROWS, metavar="N",
                    help=f"board: steps drawn per session (default {BOARD_ROWS})")
    ap.add_argument("--live", action="store_true",
                    help="board: keep redrawing in place instead of printing once")
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
    ap.add_argument(
        "--no-keys",
        action="store_true",
        help="pane: ignore the keyboard, so m/M/u/n do nothing (FBTODO_PANE_KEYS=off)",
    )
    ap.add_argument(
        "--no-mouse",
        action="store_true",
        help="pane: leave the mouse alone, so the ntfy button is worked by its key (n) alone "
             "(FBTODO_PANE_MOUSE=off)",
    )
    ap.add_argument("--tick", type=float, default=5.0,
                    help="pane: repaint at least this often while nothing moves "
                         "(1s while a step's clock is counting up, 1s for a store "
                         "that is still being written)")
    ap.add_argument("--stale-after", type=float, default=60.0, metavar="MIN",
                    help="pane: close after MIN minutes of no store activity (0 = never)")
    ap.add_argument("-i", "--interval", type=float, default=1.0,
                    help="pane, status --watch, locks --watch: seconds between polls (the "
                         "clock repaints far more often than this; locks --watch polls "
                         "every 5s by default)")
    ap.add_argument(
        "-s", "--source", default="auto", metavar="SOURCE",
        help="where the list comes from: auto, cli, desktop, or file:PATH (a state "
             "JSON on disk, the same shape `fbtodo push` writes)",
    )
    ap.add_argument(
        "--to", dest="push_to", metavar="PATH",
        help="push: write this state file instead of the live state (the list a `-s file:PATH` "
             "then reads back)",
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
    ap.add_argument("--db", default=DEFAULT_DB_GLOB,
                    help="desktop store glob; a path with no `*` names ONE store (and skips "
                         "the walk below), else auto takes this directory's own project, "
                         "deepest first, and -s desktop walks up")
    ap.add_argument("--state", default=DEFAULT_WORKSPACE_STATE)
    ap.add_argument("-t", "--thread")
    ap.add_argument("--last", action="store_true")
    ap.add_argument("-A", "--follow-app", action="store_true")
    # The desktop store holds every thread of a project, and the app can have several of
    # them running at once: `--threads` is how many the pane stacks (the one it follows
    # included, 0 for that one alone) and `--thread-live` how quiet a thread's own list may
    # be and still be one of them (0 = no window at all). Neither means anything to a
    # journal or a file, which have exactly one list by construction.
    ap.add_argument("--threads", type=int, default=DESKTOP_MAX_THREADS)
    ap.add_argument("--thread-live", type=int, default=DESKTOP_LIVE_MS // 60_000)
    ap.add_argument("-f", "--foreground", action="store_true", help="daemon: don't detach")
    ap.add_argument("--force", action="store_true", help="daemon: start even if one runs")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--json", action="store_true", help="status: JSON output; with --watch, one event object per line")
    ap.add_argument("--watch", action="store_true",
                    help="status: report only keeper changes — a pid that moved, asks that "
                         "would replace it, churn clearing — instead of a snapshot "
                         "(Ctrl-C ends; -i sets the poll)")
    ap.add_argument("--fix", action="store_true",
                    help="locks: resolve the audit's findings — clear free leftover records "
                         "and end untied role processes (SIGTERM), asking before any kill; "
                         "--dry-run plans without changing anything")
    ap.add_argument("--yes", action="store_true",
                    help="locks --fix: approve ending the untied processes without asking")
    ap.add_argument("--restart", action="store_true",
                    help="locks --fix: after the repair, re-claim the watcher and the pane "
                         "keeper through the same ask a session start uses, so one command "
                         "leaves the machine watched again")
    ap.add_argument("--no-input", action="store_true",
                    help="never prompt — a confirmation that cannot be asked is refused (66)")
    ap.add_argument("--no-daemon", action="store_true", help="pane: read stores directly")
    # `--wait` was the way to opt out of a pane closing itself; nothing closes itself now, so
    # it says nothing. It is still accepted rather than dropped, because a command line that
    # already carries it must not start failing over a switch whose meaning became the default.
    ap.add_argument(
        "--wait", action="store_true",
        help="pane: no-op, and the default — a pane does not close itself",
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
             "0 = never; it is skipped when "
             "~/.config/freebuff-notify/ask-bell.py is missing)",
    )
    # --- per-window pins (`pin`): where the list pane goes in THIS window, and how big
    ap.add_argument("--side", choices=["v", "h", "auto"],
                    help="pin: v puts the list below the session, h beside it, auto unpins the side")
    ap.add_argument("--size", type=int, metavar="N",
                    help="pin: keep the list pane N lines (0 unpins the size)")
    ap.add_argument("--window", metavar="TARGET",
                    help="pin, why, keep: the tmux window to act on (default: the pane "
                         "you are in)")
    ap.add_argument("--list", action="store_true", help="pin: every pin, and whether its window exists")
    ap.add_argument("--clear", action="store_true", help="pin: drop this window's pin")
    ap.add_argument("--dry-run", action="store_true",
                    help="init: show without "
                         "writing; locks --fix: plan without changing anything")
    ap.add_argument("--shell", metavar="SHELL", default=None,
                    help="init: shell to configure (bash, zsh, ksh, mksh, dash, sh, fish; "
                         "default: detected from $SHELL, then the parent process)")
    ap.add_argument("--startup-file", metavar="PATH", default=None,
                    help="init: startup file to edit, for a shell not in the table")
    ap.add_argument("--instance-pid", type=int, help="daemon: watch this pid (internal)")
    ap.add_argument("--watch-pid", type=int, help="treat this pid as the instance (internal)")
    ap.add_argument("--instance-of", type=int, metavar="PID",
                    help="follow the freebuff process launched by PID (the shell that started it)")
    ap.add_argument("--cwd", help="daemon: directory to serve")
    return ap


def probe_answer() -> int:
    """The self-reload probe's answer: does this build actually WORK, or merely import?

    In plain words: importing only proves the module-level code ran. A function body is not
    name-resolved until it runs, so a reference to a global that was renamed or deleted sits
    harmless until the one command that reaches that line is called — and a self-reload
    would have replaced a running image with that time bomb in the meantime. The probe
    therefore exercises what it safely can and interrogates the rest: it BUILDS THE COMMAND
    PARSER (the entry point every invocation goes through, so its own body and every default
    it computes are executed), then asks the two questions the parse can answer about what
    an import never touches. `undefined_global_names`: does every name the package's
    functions load as a global still exist? And `unreachable_code`: does its own source
    prove that some of it can never run — a statement after a `return`, a constant-false
    branch? Either answer is printed for the caller's log and the exit is nonzero, so the
    caller HOLDS.

    Nothing is claimed, moved or written here: this runs before `init_state_root`, the first
    thing that touches disk, and the parser is built without ever reading the real argv. A
    probe is therefore inert even though it now runs code.
    """
    build_parser()
    missing = undefined_global_names()
    if missing:
        print(f"fbtodo: this build uses a name it never defines — {missing}", file=sys.stderr)
        return EX_CODES["ex_software"]
    dead = unreachable_code()
    if dead:
        print(f"fbtodo: this build has code it can never run — {dead}", file=sys.stderr)
        return EX_CODES["ex_software"]
    return 0


def main(argv=None) -> int:
    if os.environ.get(RELOAD_PROBE_ENV):
        # The self-reload's own pre-flight (`reload_probe_error`): a child run of this very
        # command line asks whether the build on disk can BE a process at all. It returns
        # before `init_state_root`, the first thing that touches disk, so a probe creates
        # nothing, moves nothing and claims nothing. `probe_answer` is the whole check: the
        # parser is exercised and every global the functions load is resolved, not just the
        # module-level code the import ran.
        return probe_answer()
    # The state root is chosen at import but ACTED ON only here: importing the package must
    # not create a directory or move the legacy store (see `init_state_root`). Before the
    # parser is built, so a flag whose default is a path defaults to the real one.
    init_state_root()
    ap = build_parser()
    try:
        args, extra = ap.parse_known_args(argv)
    except SystemExit:
        return EX_CODES["usage"]
    if extra:
        print(f"unexpected arguments: {' '.join(extra)}", file=sys.stderr)
        return EX_CODES["usage"]
    args.keep_verb = getattr(args, "keep_verb", None)
    args.mute_verb = None
    if args.verb is not None:
        # One positional, several commands' verbs: argparse would hand `mute on` to the
        # FIRST positional (whose choices are `keep`'s), so the words are routed here
        # instead, where each command's own list is checked. A verb on a command that has
        # none, and a word that is not one of its verbs, are both the caller's usage error
        # (64) — the same answer the choices= used to give, from one place.
        allowed = {"keep": ("on", "off", "default"),
                   "mute": ("on", "off", "list", "until-done")}.get(args.command)
        if not allowed:
            print(f"unexpected argument: {args.verb!r} — {args.command} takes no verb",
                  file=sys.stderr)
            return EX_CODES["usage"]
        if args.verb not in allowed:
            print(f"fbtodo {args.command}: {args.verb!r} is not a verb here — "
                  f"{' / '.join(allowed)}", file=sys.stderr)
            return EX_CODES["usage"]
        if args.command == "keep":
            args.keep_verb = args.verb
        else:
            args.mute_verb = args.verb
    if args.watch and args.command not in ("status", "locks"):
        print("unexpected argument: --watch — it belongs to `status` and `locks`",
              file=sys.stderr)
        return EX_CODES["usage"]
    if args.watch and args.fix:
        print("unexpected argument: --watch with --fix — one watches, the other repairs",
              file=sys.stderr)
        return EX_CODES["usage"]
    if args.fix and args.command != "locks":
        print("unexpected argument: --fix — it belongs to `locks`", file=sys.stderr)
        return EX_CODES["usage"]
    if args.restart and not args.fix:
        print("unexpected argument: --restart — it belongs to `locks --fix`", file=sys.stderr)
        return EX_CODES["usage"]
    if args.live and args.command != "board":
        print("unexpected argument: --live — it belongs to `board`", file=sys.stderr)
        return EX_CODES["usage"]
    if args.live and args.json:
        print("unexpected arguments: --json with --live — one is a document, the other a pane",
              file=sys.stderr)
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
    # `-s` is a word or a `file:PATH`, so the word set is checked here rather than by
    # argparse's choices: a typo is the caller's usage error (64), and a `file:` prefix
    # must reach `sources_for` instead of being rejected before it.
    if args.source not in ("auto", "cli", "desktop") and not source_path(args.source):
        print(f"fbtodo: unknown source {args.source!r} — expected auto, cli, desktop, "
              "or file:PATH", file=sys.stderr)
        return EX_CODES["ex_usage"]
    if args.command == "locks" and args.watch and args.interval <= 1.0:
        # One poll is a full audit — every claim file plus the process table — and a finding
        # is worth hearing within seconds, not within one.
        args.interval = 5.0
    os.makedirs(SCRATCH, mode=0o700, exist_ok=True)
    # A `file:PATH` state is an ingress like push, so the same caps apply and a payload over
    # one is the same data error (65), not a state the pane is asked to draw. A file that is
    # simply absent is left to the source's own "no state file" answer.
    spath = source_path(args.source)
    if spath:
        code = check_file_ingress(state_file_in(spath))
        if code is not None:
            return code

    if args.command == "push":
        return cmd_push(args)
    if args.command == "init":
        return cmd_init(args)
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
    if args.command == "keep":
        return cmd_keep(args)
    if args.command == "mute":
        return cmd_mute(args)
    if args.command == "locks":
        return cmd_locks(args)
    if args.command == "status":
        return cmd_status(args)
    if args.command == "doctor":
        return cmd_doctor(args)
    if args.command == "prune":
        return cmd_prune(args)
    if args.command == "ledger":
        return cmd_ledger(args)
    if args.command == "pane-watch":
        return cmd_pane_watch(args)
    if args.command == "pin":
        return cmd_pin(args)
    if args.command == "why":
        return cmd_why(args)
    if args.command == "panes":
        return cmd_panes(args)
    if args.command == "dead":
        return cmd_dead(args)
    if args.command == "board":
        return cmd_board(args)
    return EX_CODES["usage"]


if __name__ == "__main__":
    sys.exit(main())
