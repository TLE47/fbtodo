"""The floor under everything else: where state lives, and the small tools.

This module is the only one with no `fbtodo` module under it. It holds what the rest of the
program is written on top of:

* the paths — the state directory and its migration, the lock, log and pins files, and the
  environment variables that move them;
* the settings the program reads at import: the transcript constants, the estimate and
  history bounds, the scratch limits, the exit codes;
* the generic helpers — atomic writes, the process table, the clocks, and the text tools
  (ANSI-stripping, screen-cell widths, wrapping) that every renderer is built from.

Nothing here knows what a todo list is, which is what lets `scan`, `tasks` and `render` be
read on their own.
"""

from __future__ import annotations

import copy
try:
    import fcntl
except ImportError:  # pragma: no cover - no flock, so a claim is the record alone
    fcntl = None
import glob
import json
import os
import re
import shlex
import shutil as _shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unicodedata

VERSION = "4.29.0"


HOME = os.path.expanduser("~")


# FBTODO_HOME points state/lock/log elsewhere — used by the self-check so it never
# touches the real watcher's files. Failing that, state belongs where the XDG spec says it
# does ($XDG_STATE_HOME/fbtodo) rather than in a dotdir of our own devising; the legacy
# `~/.freebuff` is moved there once, and only when nothing is still writing it.
LEGACY_SCRATCH = os.path.join(HOME, ".freebuff")


STATE_DIR = os.path.join(
    os.path.expanduser(os.environ.get("XDG_STATE_HOME") or os.path.join(HOME, ".local", "state")),
    "fbtodo",
)


# What the move carries. Listed as names rather than taken from the paths below, because the
# root has to be chosen before those are built.
LEGACY_NAMES = (
    "fbtodo-state.json", "fbtodo-tasks.json", "fbtodo-tasks.jsonl",
    "fbtodo-daemon.pid", "fbtodo-daemon.log",
    "fbtodo-nas-pane.pid", "fbtodo-nas-pane.json", "fbtodo-nas-pane.log",
    "fbtodo-pane-keeper.pid", "fbtodo-pane.log", "fbtodo-pins.json", "fbtodo-last.json",
    "fbtodo-nas.json",
)


# The records that say "a process is using this directory": the watcher's and the keeper's.
# Both are checked, because moving the keeper's claim out from under a keeper leaves it
# writing a record at a path nothing reads, and a second keeper would then start.
LEGACY_CLAIMS = ("fbtodo-daemon.pid", "fbtodo-pane-keeper.pid")


def _claim_live(record: str) -> bool:
    """Is the process at `record` still there? Two gates, both of which err towards yes.

    The record must not be flocked, and its pid must not be alive. The second one matters
    because a process from before there was a claim left a record nobody holds, and the pid
    is then the only thing that says it is still running.
    """
    try:
        with open(record, "r", encoding="utf-8") as fh:
            pid = int((json.load(fh) or {}).get("pid") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        pid = 0
    if pid:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            pass
        except OSError:
            return True
    if fcntl is None:
        return False
    try:
        with open(record, "r+", encoding="utf-8") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return True
    except OSError:
        return False
    return False


def _legacy_claim_live() -> bool:
    """Is any of the legacy locks held, or its record's pid alive?"""
    return any(
        _claim_live(os.path.join(LEGACY_SCRATCH, name)) for name in LEGACY_CLAIMS
    )


def _state_root():
    """(root, note) — where this run keeps its state, and how it got there.

    The note is one of `None` (nothing to move), `"moved"`, or a reason the run stayed on
    the legacy root: `"live"` (a watcher there still owns the store, so moving out from
    under it would leave the pane reading a file nobody updates) or `"failed"` (the move
    was refused; the old root still works, so it is what we use).
    """
    env_home = os.environ.get("FBTODO_HOME")
    if env_home:
        return os.path.expanduser(env_home), None
    if os.path.abspath(STATE_DIR) == os.path.abspath(LEGACY_SCRATCH):
        return STATE_DIR, None
    if not os.path.exists(os.path.join(LEGACY_SCRATCH, "fbtodo-state.json")):
        return STATE_DIR, None
    if os.path.exists(os.path.join(STATE_DIR, "fbtodo-state.json")):
        return STATE_DIR, None
    if _legacy_claim_live():
        return LEGACY_SCRATCH, "live"
    try:
        os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
        for name in LEGACY_NAMES:
            src = os.path.join(LEGACY_SCRATCH, name)
            if os.path.exists(src):
                os.replace(src, os.path.join(STATE_DIR, name))
    except OSError:
        return LEGACY_SCRATCH, "failed"
    return STATE_DIR, "moved"


SCRATCH, STATE_NOTE = _state_root()


STATE_FILE_NAME = "fbtodo-state.json"
STATE_PATH = os.path.join(SCRATCH, STATE_FILE_NAME)


# The task log is two files. The stream is the evidence store — one JSON object per line,
# appended to and never edited — and the document beside it is a memo of the fold, carrying
# the byte offset it was folded to. Anything that can be derived from the stream is derived
# again rather than trusted.
TASKS_PATH = os.path.join(SCRATCH, "fbtodo-tasks.json")


def events_path(path: str = "") -> str:
    """The stream that belongs to a task-log view — `fbtodo-tasks.json` -> `.jsonl`.

    Derived from the view's name rather than held as a second constant, so a caller that
    points the view somewhere else (the self-check's unit blocks, a restore from a backup)
    gets its own stream by construction and cannot fold one log's events into another's
    records.
    """
    return (path or TASKS_PATH) + "l"


def self_argv(here: str = "") -> list:
    """This program as a command line — how the panes, the keeper and the daemon name it.

    The LAUNCHER beside the package, not the file asking. A package's `__init__.py` is not the
    package when it is run as a script (a second copy of every module, and no relative
    imports), so pointing the re-invocations at whichever file happens to hold this function
    would break as soon as the program lived in more than one of them. A copy that carries the
    package without the launcher falls back to the package's own entry file — named from the
    asker's directory, so `src/fbtodo/anything.py` still answers `src/fbtodo/__init__.py` —
    which the `__main__` guard at the top turns back into the package.

    `here` is the file asking: this file in practice, and a stand-in in the self-check, which
    is how the fallback is exercised without a launcher-less copy on disk.
    """
    here = os.path.abspath(here or __file__)
    root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    launcher = os.path.join(root, "fbtodo")
    if os.path.exists(launcher):
        return [sys.executable, launcher]
    return [sys.executable, os.path.join(os.path.dirname(here), "__init__.py")]


LOCK_PATH = os.path.join(SCRATCH, "fbtodo-daemon.pid")


LOG_PATH = os.path.join(SCRATCH, "fbtodo-daemon.log")


# The NAS pane watcher: its own lock, log and state, because it is a different question
# ("has a session started over there?") from the local watcher's ("what is the instance
# in this directory doing?").
NAS_LOCK_PATH = os.path.join(SCRATCH, "fbtodo-nas-pane.pid")


# The pane keeper's claim. A keeper is neither watcher: it has no store, no state file and
# no instance of its own — only the panes. Its own lock because being kept out by a
# watcher's lock was the whole problem: a `-s nas` daemon holds that one, and a killed pane
# then went unopened (measured: the pane never came back while a slow-socket NAS daemon
# owned the watcher lock). One keeper per tmux server, which is why the record carries the
# server it belongs to.
PANE_KEEPER_PATH = os.path.join(SCRATCH, "fbtodo-pane-keeper.pid")


PANE_LOG_PATH = os.path.join(SCRATCH, "fbtodo-pane.log")


# Where the list pane goes, per window, per role (`fbtodo pin`):
#   {`session:index`: {side, size, local: {side, size}, nas: {side, size}}}
# The flat halves are the window's shared answer (what `--role both` writes, and what
# every pin written before there was a role is); a `local`/`nas` entry is that pane's own
# and outranks the shared one for it, so a window holding a session and a NAS ssh can size
# its two lists differently.
PINS_PATH = os.path.join(SCRATCH, "fbtodo-pins.json")


# Where it was left last time, per window and per role: {`session:index`: {local|nas: …}}.
LAST_PATH = os.path.join(SCRATCH, "fbtodo-last.json")


NAS_STATE_PATH = os.path.join(SCRATCH, "fbtodo-nas-pane.json")


NAS_LOG_PATH = os.path.join(SCRATCH, "fbtodo-nas-pane.log")


# The phone notifier (the finish notification's sender), asked once per interval while a
# NAS session runs: it owns the decision AND the "already pushed" record, so fbtodo only
# has to time it. Optional — a machine without the notify dir simply never asks, and the
# NAS-side work stays exactly as it was.
NAS_NOTIFY = os.path.expanduser(
    os.environ.get("FBTODO_NOTIFY")
    or os.path.join(HOME, ".config", "freebuff-notify", "todo-bell.py")
)


# The drop watch: asked when a NAS session STOPS instead of ending. A session can die with
# the ssh under it — the marker's pid is gone while the marker is still there — and that is
# a different question from "did the task finish", so it has its own script (and its own
# run-once-per-death record). Optional, like the notifier above.
DROP_NOTIFY = os.path.expanduser(
    os.environ.get("FBTODO_DROP")
    or os.path.join(HOME, ".config", "freebuff-notify", "drop-bell.py")
)


# The ask watch: asked while a session runs, and the only notifier here that is about the
# PRESENT rather than the past — a question is on screen and the agent is stopped until it
# is answered. It reads panes, not this session's store, so the local watcher and a NAS one
# both cover the whole tmux server; its own record makes the overlap harmless. Optional,
# like the other two.
ASK_NOTIFY = os.path.expanduser(
    os.environ.get("FBTODO_ASK")
    or os.path.join(HOME, ".config", "freebuff-notify", "ask-bell.py")
)


# The stall watch: the other half of the ask watch's question. A question is the CLI
# waiting for YOU; a stall is the CLI having stopped without ending its turn — the step
# cap cutting a turn short, a loop wedged — while the list still has work in it. Local
# only: the NAS build writes no `shouldEndTurn`, so nothing there can tell a stop from a
# slow step. Optional, like the other notifiers.
PAUSE_NOTIFY = os.path.expanduser(
    os.environ.get("FBTODO_PAUSE")
    or os.path.join(HOME, ".config", "freebuff-notify", "pause-bell.py")
)


# The pane watch: the other half of the keeper's promise, and the only one of the four
# that is about a thing being BROKEN rather than a session waiting. A session with no todo
# pane, or a tmux server with no keeper to put one back, is invisible from every store and
# from the pane log too — that log records a pane that came back, never one that did not.
# It reads the layout from `why --json` rather than re-deriving the session/pane matching
# the keeper acts on, and takes the keeper's record path from here so the two cannot drift.
PANE_NOTIFY = os.path.expanduser(
    os.environ.get("FBTODO_PANE_BELL")
    or os.path.join(HOME, ".config", "freebuff-notify", "pane-bell.py")
)


# tmux overridable so tests drive a private server instead of the owner's.
TMUX_BIN = shlex.split(os.environ.get("FBTODO_TMUX") or "tmux")


# Every subcommand this program issues, in one list: a name missing here would run against
# the DEFAULT server while FBTODO_TMUX points at another one — and the tests run on a
# private socket, where `move-pane` on the owner's server would move a real pane.
TMUX_SUBCOMMANDS = (
    "split-window",
    "move-pane",
    "resize-pane",
    "kill-pane",
    "list-panes",
    "display-message",
    "list-clients",
    "list-sessions",
)


DEFAULT_DB_GLOB = os.path.join(HOME, ".config/freebuff-desktop/projects/*/desktop-v2.db")


DEFAULT_WORKSPACE_STATE = os.path.join(HOME, ".config/freebuff-desktop/state.json")


DEFAULT_CLI_ROOT = os.path.join(HOME, ".config/manicode/projects")


# The remote ("nas") store, as seen from this machine: the freebuff state directory is
# usually a bind mount on the remote host, so the host reads the journal directly — no
# docker exec, and nothing to install inside the container. There is no built-in target:
# set FBTODO_NAS / _ROOT / _PROJECT (or --nas-host / --nas-root / --nas-project), which is
# also how a project name that is not the login name is spelled.
NAS_HOST = os.environ.get("FBTODO_NAS") or ""


NAS_ROOT = os.environ.get("FBTODO_NAS_ROOT") or ""


NAS_PROJECT = os.environ.get("FBTODO_NAS_PROJECT") or ""


# The process on the NAS that IS the session: /bin/sh /usr/local/bin/freebuff → node …/freebuff/index.js
# → /root/.config/manicode/freebuff, and the host sees those pids (docker does not hide them), so this is
# one cheap pgrep. Liveness is asked about THIS rather than about the local ssh client, because the pane
# belongs to the freebuff session, not to the terminal that happens to be carrying it.
NAS_PROC = os.environ.get("FBTODO_NAS_PROC") or "manicode/freebuff"


# What `-s nas` says when nobody has told it where the remote store is.
NAS_UNCONFIGURED = (
    "the remote source is not configured: set --nas-host, --nas-root and --nas-project "
    "(or FBTODO_NAS, FBTODO_NAS_ROOT, FBTODO_NAS_PROJECT)"
)


# The two facts about the CLI patches themselves, shown in the pane so neither has to be
# fetched with an ssh probe: the last outcome of the patch step, and the last alert that
# was pushed. Each is read from the log the producing step already writes, so the pane
# keeps no state of its own and there is nothing to fall out of sync.
#
#   local   ~/.config/freebuff-patch-watch/watch.log   the launchd watcher's own log
#           ~/.config/freebuff-notify/phone.log        every alert the kit has sent
#   NAS     $FBTODO_NAS_PATCH_LOG                      the launch hook's log
#           $FBTODO_NAS_ALERT_LOG                      its notifier's log
#
# The two remote paths have no default (every deployment names them differently). Unset,
# the probe's `tail` reads nothing and that half of the reply comes back empty, which the
# parser treats as "no fact to show" — the row is simply not drawn.
# The NAS pair is read at the far end of the ssh the pane already makes (see nas_probe), so
# a NAS pane pays no extra round trip for it. FBTODO_PATCH_LOG / FBTODO_ALERT_LOG /
# FBTODO_NAS_PATCH_LOG / FBTODO_NAS_ALERT_LOG point any of the four at a fixture.
PATCH_LOG = os.path.expanduser(
    os.environ.get("FBTODO_PATCH_LOG")
    or os.path.join(HOME, ".config", "freebuff-patch-watch", "watch.log")
)


PATCH_META = os.path.expanduser(
    os.environ.get("FBTODO_PATCH_META")
    or os.path.join(HOME, ".config", "manicode", "freebuff-metadata.json")
)


ALERT_LOG = os.path.expanduser(
    os.environ.get("FBTODO_ALERT_LOG")
    or os.path.join(HOME, ".config", "freebuff-notify", "phone.log")
)


NAS_PATCH_LOG = os.environ.get("FBTODO_NAS_PATCH_LOG") or ""


NAS_ALERT_LOG = os.environ.get("FBTODO_NAS_ALERT_LOG") or ""


PATCH_TAIL_BYTES = 8192   # both logs are append-only and grow: only the last entries are read


PATCH_REASON_MAX = 60     # the row is one line, so a reason is clipped rather than wrapped


PATCH_TAIL_LINES = 4      # the NAS tail, arrived in the probe: the outcome, its complaints


def nas_pgrep(name: str) -> str:
    """A `pgrep -f` pattern the probe cannot satisfy by itself.

    The probe's whole script is on the argv of the `sh -c` that runs it, so a plain
    `manicode/freebuff` matches THAT process: LIVE was always 1, and a NAS with no
    session at all looked alive. Bracketing the first character keeps the same regex
    honest — "[m]anicode/freebuff" does not contain "manicode/freebuff".
    """
    return f"[{name[0]}]{name[1:]}" if name else name


NAS_TIMEOUT = 12.0       # per ssh; the pane is useless if a poll can outlive its interval


# Multiplexed ssh: the first poll pays the handshake, the rest ride the same connection, which is the
# difference between a 1.5s poll and a 0.2s one. ControlPersist closes it 60s after the last poll.
# Rooted in $HOME, not the scratch dir: a unix socket path is limited to ~104 chars, and ssh appends
# a 16-char random suffix to %C's 40-char hash (a longer scratch dir overflowed it).
NAS_CONTROL = os.path.join(HOME, ".fbtodo-ssh-%C")


# The NAS build does NOT write tool inputs to its journal (measured: `"todos"` appears 0 times in a
# 236 KB log.jsonl that names write_todos 29 times — those are tool-NAME lists, not calls), so the only
# place a todo list exists over there is the conversation store, `chat-messages.json`, which is written
# per completed turn. Hence an extractor run on the far side: pulling 2.8 MB per poll to parse it here
# would be absurd, and the parse is skipped outright while the file has not changed.
NAS_EXTRACT = r'''
import json, os, sys
path, prev = sys.argv[1], sys.argv[2]
try:
    mt = str(int(os.stat(path).st_mtime))
    if prev and prev == mt:
        print("UNCHANGED"); raise SystemExit
except Exception as exc:
    print("ERR " + type(exc).__name__ + ": " + str(exc)[:120]); raise SystemExit
try:
    doc = json.load(open(path, encoding="utf-8", errors="replace"))
except Exception as exc:
    print("ERR " + type(exc).__name__ + ": " + str(exc)[:120]); raise SystemExit
import re
# The heading rule the local journal uses, copied rather than imported because this program
# runs standalone on the NAS; the self-check compares the two so they cannot drift apart.
GOAL_RE = re.compile(r"^\s*(?:[-*#>]+\s*)?(?:\*\*)?goal(?:\*\*)?\s*[:：]\s*(\S.*)$", re.I)
# The same continuation test the local journal uses, injected into this source instead of
# imported because this program runs standalone on the NAS.
NUDGE_WORDS = set(__NUDGE_WORDS__)
def is_nudge(text):
    text = " ".join((text or "").split())
    if not text or len(text) > __NUDGE_MAX__:
        return False
    words = re.findall(r"[a-z']+", text.lower())
    return bool(words) and all(w in NUDGE_WORDS for w in words)
found, seen = [], {"goal": None, "goal_n": 0, "user": None, "user_n": 0, "n": 0}
# What this session has actually called, so a list-less answer can say so: "no
# write_todos call yet" is a shrug, "called run_terminal_command 20 times and still no
# list" is a diagnosis. Counted here because this is the only place that sees the store.
tools = {}
def text_of(node):
    c = node.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        bits = [p.get("text") for p in c if isinstance(p, dict) and isinstance(p.get("text"), str)]
        return "\n".join(bits)
    return ""
def goal_in(node):
    for line in (text_of(node) or "").splitlines():
        m = GOAL_RE.match(line)
        if m:
            return " ".join(m.group(1).split()).strip(" *_`") or None
    return None
def walk(node):
    # Two things are tracked, in transcript order: the AGENT's own `Goal:` line (the
    # heading) and the user's requests (what "now" compares against). Measured on the real
    # NAS store, `variant='ai'` messages carry no prose at all — content length 0, tool
    # blocks only — so a NAS list heads with "none stated" today, and this lights up by
    # itself if that build ever starts recording prose.
    if isinstance(node, dict):
        seen["n"] += 1
        tn = node.get("toolName")
        if isinstance(tn, str) and tn:
            tools[tn] = tools.get(tn, 0) + 1
        g = goal_in(node)
        if g:
            seen["goal"], seen["goal_n"] = g, seen["n"]
        if node.get("variant") == "user":
            u = " ".join((text_of(node) or "").split())
            if u:
                seen["user"], seen["user_n"] = u, seen["n"]
        if node.get("toolName") == "write_todos":
            inp = node.get("input") or node.get("args") or {}
            if isinstance(inp, dict) and inp.get("todos") is not None:
                found.append((node, seen["goal"], seen["goal_n"], seen["user_n"]))
        for v in node.values():
            walk(v)
    elif isinstance(node, list):
        for v in node:
            walk(v)
walk(doc)
if not found:
    print("NONE " + json.dumps({"tools": tools})); raise SystemExit
last, goal, goal_n, user_n = found[-1]
# "now" is a NEWER REQUEST: the agent's line that came after the list if there is one,
# else the newest user message — unless the list already answers the newest request.
now, nudge = None, None
if seen["goal"] and seen["goal_n"] > goal_n:
    now = seen["goal"]
elif seen["user"] and seen["user_n"] > user_n:
    # A continuation ("continue", "go on", "keep going", ...) is not a new task: it asks
    # for the list to be RE-WRITTEN before the work resumes (AGENTS.md), so it is reported
    # as a nudge rather than as drift. The transcript is written per completed turn, which
    # makes this the pane saying "that turn ended on a continue and the list was not
    # touched" — and a later list clears it, exactly as on the local journal path.
    if is_nudge(seen["user"]):
        nudge = seen["user"]
    else:
        now = seen["user"]
# A tool block need not carry a time; the mtime of the transcript itself is the honest
# fallback, since it is rewritten as each turn completes.
ts = last.get("timestamp") or last.get("ts") or int(os.stat(path).st_mtime * 1000)
print(json.dumps({"todos": (last.get("input") or last.get("args") or {}).get("todos"),
                  "ts": ts, "calls": len(found), "goal": goal, "now": now,
                  "nudge": nudge, "tools": tools}))
'''


FB_PROC = "bin/freebuff"


FB_NAME = "freebuff"


# Where a Linux machine keeps every process's working directory. macOS has no /proc, so
# cwd discovery asks this first on both and falls back to one batched lsof (see `cwds_for`).
PROC_ROOT = "/proc"


# The `freebuff` a bare command name means: one PATH lookup for the process, not one per
# candidate per poll. A list, not a scalar, so a caller (and the self-check) can refill it.
_FREEBUFF_WHICH: list = []


CHUNK = 1 << 20


MAX_SCAN = 64 << 20


# A list is only half the story: the prompt it was written for is what makes it
# legible hours later, when the session has worked on without rewriting it. Prompts
# shorter than this are continuations ("continue", "yes", "ok"), not goals.
PROMPT_MIN_CHARS = 12


# A continuation is a request to keep going, not a new task, and it asks for one thing
# of the agent: re-write the list BEFORE the work resumes (AGENTS.md). Detected on WORDS
# rather than length, so "continue", "continue please", "go on then" and "keep going
# with it" all read as nudges while "continue the NAS work" stays a real request.
NUDGE_WORDS = {
    "continue", "cont", "carry", "keep", "going", "go", "proceed", "resume", "next",
    "ahead", "on", "then", "now", "please", "pls", "and", "with", "the", "it", "that",
    "yes", "yep", "ok", "okay", "sure", "fine",
}


NUDGE_MAX_CHARS = 40  # a nudge is a phrase; longer means it is saying something else


# The NAS extractor has no `fbtodo` to import, so the continuation rule is injected into
# its source text here: one word list, two readers, and the self-check compares them.
NAS_EXTRACT = (
    NAS_EXTRACT.replace("__NUDGE_WORDS__", repr(tuple(sorted(NUDGE_WORDS))))
    .replace("__NUDGE_MAX__", str(NUDGE_MAX_CHARS))
)


GOAL_CHASE_CHUNKS = 4  # extra 1 MiB chunks to look back for a usable goal prompt


TASK_MAX_LINES = 3     # pane only: one verbose step must not fill the whole strip


# The agent's own objective, asked for by AGENTS.md. Anchored per line and case-insensitive
# so prose that merely mentions the word ("...the goal: unclear") is not mistaken for it.
GOAL_LINE_RE = re.compile(r"^\s*(?:[-*#>]+\s*)?(?:\*\*)?goal(?:\*\*)?\s*[:：]\s*(\S.*)$", re.I)


# The wrapper's pane is a full-width strip (50 columns here) and `big goal · ` spends 11
# of them, so this is the longest heading that still renders WHOLE on one line. AGENTS.md
# carries the same number for the agent; the self-check compares the two.
GOAL_MAX_CHARS = 38


# The one-line "what happened" the phone message carries under the heading. Long enough
# for a sentence about the fix, short enough to read on a lock screen.
SUMMARY_MAX_CHARS = 200


# Which records could carry prose, found WITHOUT json-parsing them: most iterations write
# an empty `"fullResponse":""`, and the needle must accept both the CLI's compact writer
# and a spaced one (`": "`), which is what a fixture writes. Matching a value that starts
# with a non-quote byte rules the empty form out in one pass.
PROSE_NEEDLE_RE = re.compile(rb'"fullResponse"\s*:\s*"(?!")')


# The model the session is running, read without parsing: every iteration record carries
# it, and a step's remembered span means something only against the model that took it —
# a `deepseek` step and a `space-bunny` step are not the same quantity.
MODEL_RE = re.compile(rb'"model"\s*:\s*"([^"]{1,80})"')


HEARTBEAT_GRACE = 5.0  # seconds a state file stays "fresh"


# The watcher rewrites its state every poll because the heartbeat has to stay young. The
# heartbeat is the only thing in it that moves when nothing has happened, so the rewrite is
# gated on one of two things: something changed, or the ON-DISK heartbeat is about to fall
# out of that window. A third of the grace leaves room for a slow poll.
HB_REFRESH = max(1.0, HEARTBEAT_GRACE / 2.5)


# What moves every poll without saying anything: the heartbeat, the "seen at" stamps, and
# any field anywhere in the state that is itself a moment (`*_ms`) or an age (`age_s` — the
# patch row's and the alert's carry their own). Everything else is evidence, and two states
# with the same evidence are the same state as far as any reader is concerned. Measured on
# the live session, those are the ONLY things that move when nothing has happened: the four
# stamps, the two ages, and a running step's `elapsed_ms` — which the pane re-derives from
# `started_ms` and its own tick anyway (see `live_elapsed`), so a state two seconds old
# draws exactly the same frame as one written this second.
STATE_CLOCK_KEYS = ("heartbeat_ms", "probed_ms", "observed", "source_updated_ms",
                    "store_mtime_ms", "age_s")


# Retention. The scratch directory is housekeeping, not history: task records are
# dropped once they age out or exceed the count cap, the daemon log is truncated at
# the cap, and temp files orphaned by a hard kill are swept. Enforced at most once
# per PRUNE_INTERVAL so the hot path (a status bar polling every second) stays cheap.
# ...but the ESTIMATES are the one thing here that is supposed to get better with use, and
# the old 7 days / 500 records could not give them enough to learn from: measured 2026-09-29
# on this machine, the log held 176 records over 3 days of which only 39 carried a finished
# span, and its oldest record was 6.68 days old — the prune was cutting the memory off
# exactly as it matured, leaving the shape memory with one sample and the wording memory
# with nothing. 60 days / 2000 records is the trade: about a month of real use at 182 bytes
# a record (≈365 KB, parsed in a few ms on each poll) instead of a week.
MAX_TASK_RECORDS = 2000


MAX_TASK_AGE_DAYS = 60


LOG_CAP_BYTES = 1 << 20


PRUNE_INTERVAL_S = 3600.0


TEMP_MAX_AGE_S = 3600.0


# Per-task estimate memory: a step's remembered span lives as long as its record, and the
# map is capped to the most recently seen names, so a long-dead wording cannot drag the
# pace it is never asked about.
HISTORY_MAX_AGE_DAYS = MAX_TASK_AGE_DAYS


# Cap on the WORDING map, which grows without bound (a new label per step, essentially).
# The shape map, which is what can actually accumulate, is keyed by a space of a few dozen
# buckets, so this cap never binds it — no bucket is ever evicted for being old.
HISTORY_MAX_ENTRIES = 400


SHAPE_MIN_SAMPLES = 2    # remembered steps a size needs before it may set anyone's number


# The size memory is keyed by FINISHED steps' full tallies but looked up with a RUNNING step's
# partial one, and a partial tally is a smaller step: at its first call a step sits a median of
# 3 buckets below the one it will end in, and it reaches its final bucket only 71% of the way
# through its own clock (measured 2026-09-30 over the 171 ticked steps whose journals record a
# per-call trail). So the lookup is not asking "how long does a step this small take" — it is
# asking "how long did the steps that stopped this small take", which is a different and much
# smaller population. Asked at the first call the rung misses by a median 14.1x and beats the
# list's pace on 17% of steps; at 25% of the clock, 3.3x and 42%; only past half the clock does
# it draw level. A floor at `calls4` (16 calls so far) is where the question starts to have an
# answer: the number it sets is then a median 1.96x / 3.38x mean off against the pace's 2.45x /
# 4.39x, beating that same pace on 79% of the steps it fires on, and it scores better in 100%
# of session bootstraps (2000 resamples over 14 sessions) where every floor below it loses
# outright (2, 4) or merely ties (8, 12). Below the floor the rung stands aside and the blend or
# the pace answers — the pace is the floor of the ladder, so standing aside costs nothing.
SHAPE_MIN_BUCKET = 4     # `calls4` = 16 calls: the youngest tally a running step may be sized at


SPREAD_MIN_SAMPLES = 2   # measured steps needed before a spread may be claimed at all


SPREAD_MIN_RATIO = 3.0   # hi/lo at which one number stops being the honest form


# A span shorter than this is a list flip, not work: the agent ticked a step and the next
# write showed the following one in flight within seconds. Measured 2026-09-29 over the 258
# steps recovered from the CLI journals, 9 of 170 ticked spans were under 10s and they
# poisoned every score they touched (a 2s step projected a whole list at `~2s`, a 45x miss).
# Such a span is still SHOWN — it is what happened — but it is not evidence: it sets no pace
# and moves no memory. Tunable per machine with `--label-floor SEC` / `FBTODO_LABEL_FLOOR`;
# 0 keeps every span, which is what the earlier builds did.
LABEL_FLOOR_S = float(os.environ.get("FBTODO_LABEL_FLOOR") or 10.0)


MIN_LABEL_MS = max(0, int(LABEL_FLOOR_S * 1000))


# How much of a WAITING step's number comes from its wording rather than the list's pace, in
# log space: 0 is the pace alone, 1 is the wording alone, 0.5 is the default.
# Be honest about what 0.5 buys, because it is less than it first looked. Measured 2026-09-29
# by leave-one-session-out replay of the 160 real spans, scoring the shipped `pending_blend_ms`
# against the same step's pace: it is a COIN FLIP on the typical step (better on 77 of 160,
# worse on 83, median ratio 1.01x) and a real gain only in the tail (mean 3.31x against 3.75x,
# worst 14.9x against 33.9x). An earlier reading of this - "beats the pace on every column,
# 74% of steps" - was a harness bug: it ordered each session's steps by span instead of by
# time, so the "pace so far" it compared against was the session's smallest steps. So keep the
# blend as a tail-smoother, not as a median win, and let the `forecast error` scoreboard settle
# it on real traffic.
# Refit 2026-09-29 on the same 161 spans, paired and bootstrapped over sessions (every step
# decides here, because the blend changes nearly every number):
#
#     w=0 (pace alone)  even     — beats this one on 50% of steps, 51% of resamples
#     w=0.25            53%      80% of resamples — not significant, and worse on the mean/worst
#     w=0.75            44%       6% of resamples — worse
#     w=1.0             42%       2% of resamples — the only comparison that RESOLVES
#
# So the one thing the data settles is that handing the number to the label model outright
# (1.0) is worse, in 98% of resamples: the pace has to stay in the blend. Everything between
# 0 and 0.75 is a plateau — 0.25 leans slightly better on the typical step, 0.5 on the mean and
# the worst case, and neither lean is separable from noise. The midpoint stands for that
# reason: it sits inside the flat top of the surface, and picking a different point on the
# curve would be picking the one this particular sample happened to favour.
# Tunable with `--blend-weight W` / `FBTODO_BLEND_WEIGHT`.
BLEND_WEIGHT = min(1.0, max(0.0, float(os.environ.get("FBTODO_BLEND_WEIGHT") or 0.5)))


# The two knobs above are what the environment said at import; this is what the run uses.
# They are asked for rather than read as constants because `--label-floor`/`--blend-weight` are
# applied once `main` starts, and the readers are spread over the program: a name imported into
# another module is a COPY, and a flag that moved only this module's would leave the copy — the
# one the estimator actually consults — at the old value. A dict is one object that every
# reader holds, so a write to it is visible from everywhere.
ESTIMATE_KNOBS = {"label_floor_ms": MIN_LABEL_MS, "blend_weight": BLEND_WEIGHT}


def label_floor_ms() -> int:
    """The shortest span that counts as evidence, asked for at the point of use."""
    return ESTIMATE_KNOBS["label_floor_ms"]


def blend_weight() -> float:
    """How much of a waiting step's number is its wording rather than the list's pace."""
    return ESTIMATE_KNOBS["blend_weight"]


def set_estimate_knobs(label_floor=None, blend_weight=None) -> None:
    """`--label-floor` / `--blend-weight`: one write here, every reader moves (see the table).

    None means "this flag was not given", so a caller cannot accidentally reset the other one.
    """
    if label_floor is not None:
        ESTIMATE_KNOBS["label_floor_ms"] = max(0, int(label_floor * 1000))
    if blend_weight is not None:
        ESTIMATE_KNOBS["blend_weight"] = min(1.0, max(0.0, blend_weight))


# The forecast ledger is only a forecast if it was written before the step had run. A watcher
# that starts (or restarts) with a step already in flight sees it running for the first time
# minutes in, so its vector would carry the answer's clock, not a prediction of it. Such a
# vector is still recorded — the record is the record — but flagged, and `forecast_error`
# leaves flagged rows out of the scoreboard. One poll of grace, generously.
LEDGER_FRESH_MS = 5000


# What it would take to re-choose the two estimate constants from the LOG instead of from a
# replay of the journals, so `fbtodo status` can say how far off that is. Both numbers come
# from the 2026-09-29 refit (the table is in the `step_pace_ms` docstring): a median needs
# about this many closed steps before one step stops being the whole distribution, and the
# clip needs about this many steps on which it actually MOVED the number — it is consulted
# only on the first few steps of a list, and on most of those it changes nothing, which is
# why the second count and not the first is the one that decides.
REFIT_MIN_SCORED = 30


REFIT_MIN_DECIDED = 78


EX_CODES = {
    "ok": 0,
    "usage": 2,
    "ex_usage": 64,
    "dataerr": 65,
    "nofile": 66,
    "noinput": 66,
    "cantcreat": 73,
    "ioerr": 74,
    "tempfail": 75,
    "config": 78,
}


# ------------------------------------------------------------- untrusted text
# Every string the pane prints that is not the tool's own comes out of a file an agent
# wrote: a step's name, the `Goal:` line, the last thing the agent said, the command in
# an action-feed row, the tail of a far-side log. A journal is not hostile input in the
# usual sense, but a terminal is a PROGRAM, and it obeys what it is fed: H1 measured an
# OSC 52 sequence in a step's name setting the owner's clipboard, `\x1b[2J` wiping the
# frame the pane sits under, a CSI move putting the cursor outside the box, and U+202E
# reversing a row so it reads as something it never said — in the rich pane, in the
# plain `snap` frame and in `json`, all verbatim (2026-09-29).
#
# So the filter lives here, and it is applied twice: where a source's prose enters the
# state (which is also what `json` and `status` read, so cleaning only at render would
# leave them exposed) and again as the frame is built, because a state file an older
# build wrote is still on disk and a cache is not a promise.
_ESC_SEQ_RE = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"     # OSC … BEL | ST (window title, clipboard)
    r"|\x1b[P^_X][^\x1b]*(?:\x1b\\)?"         # DCS / PM / APC / SOS … ST
    r"|\x1b\[[0-?]*[ -/]*[@-~]"               # CSI (cursor moves, erases, SGR)
    r"|\x1b[@-_]"                             # two-character escape
)


_TEXT_DROP_RE = re.compile(
    "["
    "\x00-\x08\x0b-\x1f\x7f-\x9f"   # C0 (tab and newline aside), DEL, and all of C1
    "\u200b-\u200f"                 # zero-width space and joiners, LRM, RLM
    "\u202a-\u202e\u2066-\u2069"    # bidi embeddings, overrides and isolates
    "\ufeff"                        # BOM / zero-width no-break space
    "]"
)


# The keys a source contributes as prose, and the row keys inside the two lists.
TEXT_KEYS = ("goal", "now", "nudge", "summary", "title", "first_prompt", "model",
             "thread", "session", "patch", "alert", "error", "stop_reason")


TEXT_LIST_KEYS = ("todos", "observed")


TEXT_ROW_KEYS = ("task", "verb", "what")


def clean_text(value) -> str:
    """One string, stripped of everything a terminal would ACT on rather than show.

    Escape sequences go whole — an OSC 52 leaves no `52;c;…` behind to read — and then
    every remaining control, delimiter and bidi/zero-width character. Newlines stay: a
    step's name may legitimately be three lines, and the row splitter needs them. A CR
    becomes one, because a CR moves the cursor rather than starting a line.
    """
    text = _ESC_SEQ_RE.sub("", str("" if value is None else value))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _TEXT_DROP_RE.sub("", text)


def clean_observation(state: dict) -> dict:
    """The prose a source contributes, filtered — see `clean_text`.

    Called where a source's state is assembled and again by `render()`; both, so the
    state file itself is clean for its other readers and a frame cannot be painted from
    a state that was written before this filter existed.
    """
    if not isinstance(state, dict):
        return state
    out = dict(state)
    for key in TEXT_KEYS:
        if isinstance(out.get(key), str):
            out[key] = clean_text(out[key])
    for key in TEXT_LIST_KEYS:
        rows = out.get(key)
        if not isinstance(rows, list):
            continue
        fixed = []
        for row in rows:
            if isinstance(row, str):
                fixed.append(clean_text(row))
            elif isinstance(row, dict):
                row = dict(row)
                for field in TEXT_ROW_KEYS:
                    if isinstance(row.get(field), str):
                        row[field] = clean_text(row[field])
                fixed.append(row)
            else:
                fixed.append(row)
        out[key] = fixed
    return out


# --------------------------------------------------------------------- helpers
# In plain words: save, then swap. The new copy is written to a scratch name in the same
# folder and only then renamed over the real one, so a reader can never catch a
# half-written file — they see the old copy or the new one, never a mixture. The fsync
# calls mean "get this onto the disk", not just into the computer's memory.
def atomic_write_json(path: str, payload: dict, mode: int = 0o600, fsync: bool = True) -> None:
    """Temp file in the target's own directory, fsync, rename, fsync the dir.

    `fsync=False` keeps the atomicity — still a rename over the target, so a reader never
    sees half a file — and drops the durability. That is the right trade for a rewrite whose
    only change is the heartbeat: if the machine goes down in the same second, the cost is a
    state file whose clock is a little old, which a reader answers by probing. What it saves
    is the pair of flushes a second, every second, on a file nobody asked to keep.
    """
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".fbtodo.", suffix=".tmp", dir=d)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
            fh.flush()
            if fsync:
                os.fsync(fh.fileno())
        os.replace(tmp, path)
        if fsync:
            dfd = os.open(d, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def pid_alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def proc_cwds(pids: list[int]) -> dict[int, str]:
    """pid -> cwd from /proc, for the pids that answer there ({} when there is no /proc).

    Linux keeps the working directory of every process as a symlink, so this is a readlink
    and no subprocess at all — the cheapest question in the poll on the platform most
    likely to be running a watcher under load. A pid owned by another user answers with
    EACCES and is simply left out for lsof to try (which will also fail, quietly).
    """
    if not os.path.isdir(PROC_ROOT):
        return {}
    found: dict[int, str] = {}
    for pid in pids:
        try:
            found[pid] = os.readlink(os.path.join(PROC_ROOT, str(pid), "cwd"))
        except OSError:
            continue
    return found


def lsof_cwds(pids: list[int]) -> dict[int, str]:
    """pid -> cwd for many pids in ONE lsof call — the macOS fallback.

    Per-pid lsof calls dominated the poll (and a transient failure silently dropped a
    candidate); the comma-separated `-p` form returns them all. A machine with no lsof on
    it is not an error here: `except Exception` covers a missing binary, and the caller
    keeps whatever `/proc` could answer.
    """
    if not pids:
        return {}
    try:
        out = subprocess.run(
            ["lsof", "-a", "-d", "cwd", "-Fn", "-p", ",".join(str(p) for p in pids)],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except Exception:
        return {}
    cwds: dict[int, str] = {}
    current: int | None = None
    for line in out.splitlines():
        if line.startswith("p"):
            current = int(line[1:]) if line[1:].isdigit() else None
        elif line.startswith("n") and current is not None:
            cwds[current] = line[1:]
            current = None
    return cwds


def cwds_for(pids: list[int]) -> dict[int, str]:
    """pid -> cwd, by /proc where there is one and one batched lsof for the rest.

    Both, rather than either: on Linux `/proc/<pid>/cwd` answers without a subprocess, so
    it is asked first and lsof only covers what it could not answer (another user's
    process, a pid that went away) — and on a machine with no lsof at all, a normal Linux
    server, the watcher can still follow its own session instead of giving up on a missing
    binary.
    """
    want = [int(p) for p in pids]
    if not want:
        return {}
    cwds = proc_cwds(want)
    missing = [p for p in want if p not in cwds]
    if missing:
        cwds.update(lsof_cwds(missing))
    return cwds


def pid_cwd(pid) -> str | None:
    return cwds_for([int(pid)]).get(int(pid))


def parse_etime(text: str) -> int | None:
    """`ps -o etime` gives [[DD-]HH:]MM:SS. macOS has no `etimes` keyword."""
    days = 0
    if "-" in text:
        head, text = text.split("-", 1)
        if not head.isdigit():
            return None
        days = int(head)
    parts = text.split(":")
    if not 2 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        return None
    nums = [int(p) for p in parts]
    hours, minutes, secs = (0, *nums) if len(nums) == 2 else tuple(nums)
    return days * 86400 + hours * 3600 + minutes * 60 + secs


def ages_for(pids: list[int]) -> dict[int, int]:
    """pid -> seconds of runtime. Pid order is not age (pids wrap), so ask ps."""
    if not pids:
        return {}
    try:
        out = subprocess.run(
            ["ps", "-o", "pid=,etime=", "-p", ",".join(str(p) for p in pids)],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except Exception:
        return {}
    ages: dict[int, int] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit():
            secs = parse_etime(parts[1])
            if secs is not None:
                ages[int(parts[0])] = secs
    return ages


def installed_freebuff() -> str | None:
    """The executable a bare `freebuff` would run, or None when PATH has none."""
    if not _FREEBUFF_WHICH:
        found = _shutil.which(FB_NAME)
        _FREEBUFF_WHICH.append(os.path.realpath(found) if found else None)
    return _FREEBUFF_WHICH[0]


def is_freebuff_cmd(cmd: str) -> bool:
    """True only for the CLI itself.

    Deliberately not a substring test on the whole command line: any process whose
    *arguments* mention the path (a python -c probe, a grep, a test) would match, and the
    pane would then follow the wrong pid. A TOKEN has to be the CLI, in one of the shapes a
    launcher actually produces:

      * argv[0] is a bare `freebuff` — how a PATH lookup is spelled in argv, which the
        original rule (an absolute path) could not see at all. Accepted only when such a
        program exists, never because the word appears;
      * a path-like token ending in `bin/freebuff` — the original rule, still a subset of
        this one, plus `./bin/freebuff` and `~/bin/freebuff`;
      * a path-like token that mentions `freebuff` and RESOLVES to `.../bin/freebuff`, so a
        symlinked or relocated install is still the CLI (an absolute path only, and only
        where the name gives a reason to resolve it).
    """
    toks = cmd.split()
    if toks and toks[0] == FB_NAME:
        return bool(installed_freebuff())
    for tok in toks:
        if not tok.startswith(("~", "/", ".")) or FB_NAME not in tok:
            continue  # `grep freebuff`, `python3 -c '…bin/freebuff…'`, any other argument
        if tok.endswith(FB_PROC):
            return True
        real = os.path.realpath(tok)
        if real.endswith(FB_PROC) or os.path.basename(real) == FB_NAME:
            return True
    return False


def freebuff_pids() -> list[int]:
    table = process_table()
    return sorted(p for p, (_ppid, cmd) in table.items() if is_freebuff_cmd(cmd))


def process_table() -> dict[int, tuple[int, str]]:
    """pid -> (ppid, command line) from one ps call."""
    try:
        out = subprocess.run(
            ["ps", "-Ao", "pid=,ppid=,command="], capture_output=True, text=True, timeout=5
        ).stdout
    except Exception:
        return {}
    table: dict[int, tuple[int, str]] = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        table[int(parts[0])] = (int(parts[1]), parts[2] if len(parts) > 2 else "")
    return table


def instance_of_parent(parent_pid: int) -> int | None:
    """The Freebuff process launched by this shell."""
    return descendant_instance(process_table(), parent_pid)


# In plain words: every running program has a number, and knows the number of the program
# that started it. Following those links downwards lists everything a program has spawned,
# and going widest-first means the shallowest match comes out first — which is what "the
# freebuff process this shell started" means.
def descendant_pids(table: dict, parent_pid: int) -> list[tuple[int, int]]:
    """(pid, depth) for everything under this pid, breadth-first, from a fetched table.

    Both lookups that need a process tree share this: the freebuff *instance* under a
    shell (the shallowest match wins) and the ssh a NAS session runs inside (the pane
    that hosts it). One walk, so neither pays for a second pass over the table.
    """
    kids: dict[int, list[int]] = {}
    for pid, (ppid, _cmd) in table.items():
        kids.setdefault(ppid, []).append(pid)
    out: list[tuple[int, int]] = []
    frontier, depth, seen = list(kids.get(parent_pid, [])), 1, set()
    while frontier:
        nxt = []
        for pid in frontier:
            if pid in seen:
                continue
            seen.add(pid)
            out.append((pid, depth))
            nxt.extend(kids.get(pid, []))
        frontier, depth = nxt, depth + 1
    return out


def descendant_instance(table: dict, parent_pid: int) -> int | None:
    """The Freebuff process under this pid, from an ALREADY-FETCHED process table.

    A shell may host several descendants; the instance is the *shallowest* one
    whose command line looks like the CLI, because deeper ones are per-turn
    children that come and go while the session lives. The table is passed in
    because the pane bookkeeping asks this of several pids in one pass, and a
    `ps` per pid would cost more than the whole poll.
    """
    if parent_pid not in table:
        # the parent may already be gone; fall back to any freebuff process
        cands = [p for p, (_, cmd) in table.items() if is_freebuff_cmd(cmd)]
        return min(cands) if cands else None
    best: tuple[int, int] | None = None  # (depth, pid)
    for pid, depth in descendant_pids(table, parent_pid):
        if is_freebuff_cmd(table.get(pid, (0, ""))[1]):
            cand = (depth, pid)
            best = cand if best is None or cand < best else best
    return best[1] if best else None


# In plain words: "which freebuff is mine?" Several can run at once, so this narrows it the
# way a person would — the one started from this folder, and where there is more than one,
# the one started most recently. `--instance-of` skips the guessing: the shell that opened
# the pane hands over its own number and this walks down from there.
def find_instance(
    cwd: str, watch_pid: int | None = None, instance_of: int | None = None
) -> tuple[int | None, str | None]:
    """(pid, cwd) of the Freebuff process serving this directory."""
    if instance_of:
        pid = instance_of_parent(instance_of)
        if pid:
            return pid, pid_cwd(pid)
        # the child may not be visible yet (the pane opens as freebuff starts) or
        # the launch tree may be unusual — degrade to the cwd match below rather
        # than sitting on a pane that never attaches
    if watch_pid:
        return (watch_pid, pid_cwd(watch_pid)) if pid_alive(watch_pid) else (None, None)
    want = os.path.realpath(cwd)
    pids = freebuff_pids()
    if not pids:
        return None, None
    cwds = cwds_for(pids)
    matching = [p for p in pids if cwds.get(p) and os.path.realpath(cwds[p]) == want]
    pool = matching or pids
    # youngest wins: a directory can host several sessions, and "the one started
    # here" is what the user means. Age comes from ps, because pid order is not
    # chronological (a 9h session can hold a lower pid than a 2-day one).
    ages = ages_for(pool)
    pick = min(pool, key=lambda p: ages.get(p, 1 << 30))
    return pick, cwds.get(pick)


# ================================================================ CLI backend
def cli_chat_dir(cwd: str, project: str | None, chat: str | None, root: str) -> str | None:
    if chat:
        d = os.path.expanduser(chat)
        return d if os.path.isdir(d) else None
    if project:
        name = os.path.basename(os.path.realpath(project)) if os.path.sep in project else project
    else:
        name = os.path.basename(os.path.realpath(cwd))
    base = os.path.join(os.path.expanduser(root), name, "chats")
    dirs = [d for d in glob.glob(os.path.join(base, "*")) if os.path.isdir(d)]
    return max(dirs, key=os.path.getmtime) if dirs else None


# ===================================================================== display
def use_color(stream=sys.stdout) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM", "") in ("", "dumb"):
        return False
    if os.environ.get("FORCE_COLOR") or os.environ.get("CLICOLOR_FORCE"):
        return True
    if os.environ.get("CLICOLOR") == "0":
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def paint(enable: bool):
    return lambda code, text: f"\x1b[{code}m{text}\x1b[0m" if enable else text


def fmt_age(ms, now_ms: int | None = None) -> str:
    """How long ago the list was last written — '4m ago', so a still pane reads
    as 'nothing has changed', not 'this is hung'.

    `now_ms` is the renderer's own clock when there is one. The pane derives every other
    number from it, and a fixture's `now` is a constant rather than the wall clock, so
    reading `time.time()` here would make the one age in the frame disagree with all the
    others.
    """
    if not ms:
        return "?"
    now = (now_ms / 1000.0) if now_ms else time.time()
    delta = max(0.0, now - ms / 1000.0)
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def _plain(text: str) -> str:
    """The words of a styled line, with the colour taken out (see `prose_text`)."""
    return _ANSI_RE.sub("", str(text))


# In plain words: a terminal is not a grid of equal letters. An emoji or a Japanese
# character takes two columns on screen where an ordinary letter takes one, so counting
# characters miscounts them and the box's right-hand wall ends up ragged. This counts
# screen columns, which is what keeps the frame square.
def _cell_width(text: str) -> int:
    """Visible columns, so box borders and emoji line up in a terminal."""
    width = 0
    for ch in _plain(text):
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


# In plain words: shorten a coloured line to a width without cutting a colour code in half.
# Those invisible instructions have to survive, or everything after the cut inherits the
# wrong ink; the ellipsis is the polite way of saying the line was trimmed.
def _clip_cells(text: str, width: int) -> str:
    """Clip a styled string to a visible-column budget, keeping ANSI intact."""
    visible = _plain(text)
    if width <= 0:
        return ""
    if _cell_width(visible) <= width:
        return text
    out, used = [], 0
    for ch in visible:
        w = 0 if unicodedata.combining(ch) else (
            2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        )
        if used + w > max(0, width - 1):
            break
        out.append(ch)
        used += w
    return "".join(out) + "…"


def _pad_cells(text: str, width: int) -> str:
    return text + " " * max(0, width - _cell_width(text))


def _wrap(text: str, width: int, indent: int) -> list[str]:
    """Wrap prose to the pane without making normal words look broken.

    `indent` is the room the caller's prefix takes.  Most text wraps at word
    boundaries, but an unusually long token (a URL, for example) is split only as
    a last resort so the pane can never paint past its right edge.
    """
    available = max(1, width - max(0, indent))
    wrapper = textwrap.TextWrapper(
        width=available,
        break_long_words=False,
        break_on_hyphens=False,
        replace_whitespace=True,
        drop_whitespace=True,
    )
    lines = wrapper.wrap(str(text)) or [""]
    wrapped: list[str] = []
    for line in lines:
        while len(line) > available:
            wrapped.append(line[:available])
            line = line[available:]
        if line:
            wrapped.append(line)
    return wrapped or [""]


def _clip(text: str, width: int) -> str:
    """Fit one unindented line, using an ellipsis only when necessary."""
    text = str(text)
    if len(text) <= width:
        return text
    if width <= 1:
        return "…"[:width]
    return text[: width - 1] + "…"


def _fit_blocks(blocks: list, avail: int | None, prefer: int) -> tuple[int, int]:
    """The slice of task blocks that fits in `avail` lines, keeping `prefer` on screen.

    Two lines are held back for the elision markers, and the preferred block (the step
    being worked on now) is never among the ones dropped: a window that hides the
    current step to show finished ones would defeat the point.
    """
    total = len(blocks)
    if not total:
        return 0, 0
    if avail is None or sum(len(b) for b in blocks) <= avail:
        return 0, total
    budget = max(1, avail - 2)
    prefer = max(0, min(prefer, total - 1))
    end = prefer + 1
    used = len(blocks[prefer])
    while end < total and used + len(blocks[end]) <= budget:
        used += len(blocks[end])
        end += 1
    start = prefer
    while start > 0 and used + len(blocks[start - 1]) <= budget:
        used += len(blocks[start - 1])
        start -= 1
    return start, end


# In plain words: has the watcher written anything recently? Every write carries the time
# it was made, so a file that stops being stamped belongs to a watcher that has stopped —
# which is how "the list has not changed" is told apart from "nobody is updating it".
def _drop_clock(value):
    """The same structure with every moment taken out of it, at any depth."""
    if isinstance(value, dict):
        return {
            k: _drop_clock(v) for k, v in value.items()
            if k not in STATE_CLOCK_KEYS and not k.endswith("_ms")
        }
    if isinstance(value, list):
        return [_drop_clock(v) for v in value]
    return value


def state_evidence(state: dict | None) -> dict:
    """The state with the clock taken out: what a poll actually learned.

    Compared rather than the state itself, because the un-taken clock moves on every poll
    and would make every poll look like news. Everything else is compared as a whole, so a
    changed todo list, a new goal, a started turn or a finished session all count — and so
    does anything added to the state later, without this needing to know about it.
    """
    return _drop_clock(state) if state else {}


def state_is_fresh(state: dict | None, max_age: float = HEARTBEAT_GRACE) -> bool:
    if not state or state.get("status") != "watching":
        return False
    hb = state.get("heartbeat_ms") or 0
    return (time.time() * 1000 - hb) / 1000.0 <= max_age


__all__ = [
    "VERSION", "HOME", "LEGACY_SCRATCH", "STATE_DIR", "LEGACY_NAMES", "LEGACY_CLAIMS",
    "_claim_live", "_legacy_claim_live", "_state_root", "SCRATCH", "STATE_NOTE", "STATE_PATH",
    "STATE_FILE_NAME",
    "TASKS_PATH", "events_path", "self_argv", "LOCK_PATH", "LOG_PATH", "NAS_LOCK_PATH",
    "PANE_KEEPER_PATH", "PANE_LOG_PATH", "PINS_PATH", "LAST_PATH", "NAS_STATE_PATH",
    "NAS_LOG_PATH", "NAS_NOTIFY", "DROP_NOTIFY", "ASK_NOTIFY", "PAUSE_NOTIFY", "PANE_NOTIFY",
    "TMUX_BIN", "TMUX_SUBCOMMANDS", "DEFAULT_DB_GLOB", "DEFAULT_WORKSPACE_STATE",
    "DEFAULT_CLI_ROOT", "NAS_HOST", "NAS_ROOT", "NAS_PROJECT", "NAS_PROC", "NAS_UNCONFIGURED",
    "PATCH_LOG", "PATCH_META", "ALERT_LOG", "NAS_PATCH_LOG", "NAS_ALERT_LOG",
    "PATCH_TAIL_BYTES", "PATCH_REASON_MAX", "PATCH_TAIL_LINES", "nas_pgrep", "NAS_TIMEOUT",
    "NAS_CONTROL", "NAS_EXTRACT", "FB_PROC", "FB_NAME", "PROC_ROOT", "_FREEBUFF_WHICH",
    "CHUNK", "MAX_SCAN", "PROMPT_MIN_CHARS", "NUDGE_WORDS", "NUDGE_MAX_CHARS", "NAS_EXTRACT",
    "GOAL_CHASE_CHUNKS", "TASK_MAX_LINES", "GOAL_LINE_RE", "GOAL_MAX_CHARS",
    "SUMMARY_MAX_CHARS", "PROSE_NEEDLE_RE", "MODEL_RE", "HEARTBEAT_GRACE", "HB_REFRESH",
    "STATE_CLOCK_KEYS", "MAX_TASK_RECORDS", "MAX_TASK_AGE_DAYS", "LOG_CAP_BYTES",
    "PRUNE_INTERVAL_S", "TEMP_MAX_AGE_S", "HISTORY_MAX_AGE_DAYS", "HISTORY_MAX_ENTRIES",
    "SHAPE_MIN_SAMPLES", "SHAPE_MIN_BUCKET", "SPREAD_MIN_SAMPLES", "SPREAD_MIN_RATIO",
    "LABEL_FLOOR_S", "MIN_LABEL_MS", "BLEND_WEIGHT", "ESTIMATE_KNOBS", "label_floor_ms",
    "blend_weight", "set_estimate_knobs", "LEDGER_FRESH_MS", "REFIT_MIN_SCORED",
    "REFIT_MIN_DECIDED", "EX_CODES", "_ESC_SEQ_RE", "_TEXT_DROP_RE", "TEXT_KEYS",
    "TEXT_LIST_KEYS", "TEXT_ROW_KEYS", "clean_text", "clean_observation", "atomic_write_json",
    "read_json", "pid_alive", "proc_cwds", "lsof_cwds", "cwds_for", "pid_cwd", "parse_etime",
    "ages_for", "installed_freebuff", "is_freebuff_cmd", "freebuff_pids", "process_table",
    "instance_of_parent", "descendant_pids", "descendant_instance", "find_instance",
    "cli_chat_dir", "use_color", "paint", "fmt_age", "_ANSI_RE", "_plain", "_cell_width",
    "_clip_cells", "_pad_cells", "_wrap", "_clip", "_fit_blocks", "_drop_clock",
    "state_evidence", "state_is_fresh",
]
