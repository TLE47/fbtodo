#!/usr/bin/env python3
"""Ring when freebuff has STOPPED with work left, not when it finished.

Three siblings now, one per way a session can be waiting on you: `todo-bell.py` says the
work ended, `ask-bell.py` says it is asking a question, and this says it simply stopped.

Measured, because the obvious signal is a trap. A turn cut short — the step cap, a wedged
loop — leaves the journal's last record saying `shouldEndTurn: false` and then nothing at
all, which looks exactly like a long step. The two are told apart on the PANE:

    working...                  ↓             40m 45s  ■ Esc     <- a step is running
    ❯ Enter a coding task                                        <- ...and this alone

A five-minute command keeps the first line up for the whole five minutes (measured: a
pane showed 40m of `working...`), so a quiet store plus no `working...` on screen is a
session that stopped rather than one that is busy. A quiet window on its own would be the
quiet-window heuristic `todo-bell.py` already rejected — this is that heuristic with the
CLI's own answer attached to it.

What it will not call a stop:

  * a turn that ENDED (`shouldEndTurn`) — that is waiting for you, not stuck;
  * a session with no list yet — nothing was planned, so nothing is unfinished;
  * a pane showing the ask modal — `ask-bell.py` owns that one, and two notifications for
    one question is exactly the repeat that gets a topic muted;
  * a session outside tmux: with no pane there is no honest way to tell a stop from a slow
    step, and guessing would fire during long builds.

usage: pause-bell.py --watch-pid PID [--pane %id] [--print] [--quiet]
       pause-bell.py <shell-pid> …          the pid that launched freebuff instead

Environment: FREEBUFF_PAUSE_BELL_STATE overrides the one-push-per-stop record (tests);
FREEBUFF_PAUSE_QUIET (150s) is how long the store must be still; FREEBUFF_FBTODO points at
another fbtodo; FREEBUFF_TMUX (or FBTODO_TMUX) at another tmux; FREEBUFF_PHONE=off mutes
the push (phone.sh) and FREEBUFF_PHONE_SH points at a different sender (tests).

Exit: 0 decided · 78 the session has stopped and there is nothing configured to send to.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time

try:
    import fcntl
except ImportError:  # pragma: no cover - not a POSIX host
    fcntl = None

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.environ.get("FREEBUFF_PAUSE_BELL_STATE") or os.path.join(HERE, "pause-bell.state")
# Agent prose is opt-in — see todo-bell.py's `TEXT_AGENT`. Off by default the body is the
# metadata fbtodo itself measured (how long the store has been quiet, how many steps are
# done); `FREEBUFF_PHONE_TEXT=agent` adds the heading and the goal on top.
TEXT_AGENT = (os.environ.get("FREEBUFF_PHONE_TEXT") or "").strip().lower() in (
    "agent", "on", "1", "all", "full",
)
PHONE = os.environ.get("FREEBUFF_PHONE_SH") or os.path.join(HERE, "phone.sh")
TMUX = os.environ.get("FREEBUFF_TMUX") or os.environ.get("FBTODO_TMUX") or "tmux"
TIMEOUT = 20.0
# How long the journal must have been still before a missing `working...` means a stop.
# Generous on purpose: it only has to outlast the gap between two iterations, because the
# pane is what rules out a long step.
QUIET = float(os.environ.get("FREEBUFF_PAUSE_QUIET") or 150.0)
# The CLI's own words for "a step is running", and for "a question is up" (the ask watch's
# business, not ours).
WORKING = "working"
ESCAPE_HINT = "Esc"
MODAL = "Some questions for you"
MODAL_BOX = "╭"
CLAIM_MAX = 50


def fbtodo_binary() -> str | None:
    """Where fbtodo is: FREEBUFF_FBTODO, then PATH, then the usual script dirs."""
    for cand in (
        os.environ.get("FREEBUFF_FBTODO"),
        shutil.which("fbtodo"),
        os.path.join(os.path.expanduser("~"), ".local", "bin", "fbtodo"),
        os.path.join(os.path.expanduser("~"), "Scripts", "fbtodo"),
    ):
        if cand and os.path.exists(cand):
            return cand
    return None


def _run(argv: list[str], timeout: float = TIMEOUT) -> str:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout if proc.returncode == 0 else ""


def snapshot(watch_pid, instance_of) -> dict | None:
    """The watcher's own answer about this session, through fbtodo's JSON."""
    binary = fbtodo_binary()
    if not binary:
        return None
    argv = [binary, "--watch-pid" if watch_pid else "--instance-of", str(watch_pid or instance_of), "json"]
    out = _run(argv)
    if not out.strip():
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def pane_text(pane: str | None) -> str | None:
    if not pane:
        return None
    out = _run([*shlex.split(TMUX), "capture-pane", "-p", "-t", pane])
    return out if out else None


def looks_working(text: str) -> bool:
    """Is a step running, according to the pane's own status line?"""
    for line in text.splitlines():
        if WORKING in line and ESCAPE_HINT in line:
            return True
    return False


def asking(text: str) -> bool:
    """Is the ask modal up? (Its watch reports that; two pushes for one question is noise.)"""
    for line in text.splitlines():
        if MODAL in line and MODAL_BOX in line:
            return True
    return False


def read_state() -> dict:
    try:
        with open(STATE) as handle:
            doc = json.load(handle)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(doc: dict) -> None:
    tmp = f"{STATE}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w") as handle:
            json.dump(doc, handle)
        os.replace(tmp, STATE)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def claims(doc: dict) -> dict:
    got = doc.get("stalled")
    if not isinstance(got, dict):
        got = {}
    if len(got) > CLAIM_MAX:
        got = dict(sorted(got.items(), key=lambda kv: kv[1], reverse=True)[:CLAIM_MAX])
    return got


def stop_key(state: dict) -> str:
    """This STOP, not this session: the journal's last write is what it stopped at."""
    seed = f"{state.get('session') or ''}:{int(state.get('store_mtime_ms') or 0)}"
    return hashlib.sha1(seed.encode("utf-8", "replace")).hexdigest()[:12]


def decide(state, text, watch_pid, doc, now_ms=None) -> tuple[bool, str, str]:
    """(push?, why, key) — the reason is printed either way, because 'no push' needs one."""
    if not state:
        return False, "fbtodo could not answer for this session", ""
    if watch_pid and not alive(watch_pid):
        return False, "the session is gone", ""
    total = int(state.get("total") or 0)
    done = int(state.get("done") or 0)
    if not total:
        return False, "no list yet — nothing was planned, so nothing is unfinished", ""
    if state.get("turn_ended"):
        return False, f"the turn ended ({done}/{total}) — it is waiting for you, not stuck", ""
    mtime = int(state.get("store_mtime_ms") or 0)
    quiet = ((now_ms or time.time() * 1000) - mtime) / 1000.0
    if not mtime:
        return False, "no journal activity to time the quiet window from", ""
    if quiet < QUIET:
        return False, f"the journal moved {quiet:.0f}s ago (needs {QUIET:.0f}s)", ""
    if text is None:
        return False, "no pane to read — a session outside tmux cannot be told from a slow step", ""
    if asking(text):
        return False, "the ask modal is up — the ask watch has that one", ""
    if looks_working(text):
        return False, f"the pane still says a step is running (quiet {quiet:.0f}s)", ""
    key = stop_key(state)
    if key in claims(doc):
        return False, f"already pushed for this stop ({done}/{total})", ""
    return True, f"stopped {short(quiet)} ago with {done}/{total} steps done", key


def short(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def phone_message(state, why, pane) -> tuple[str, str]:
    project = os.path.basename((state.get("cwd") or "").rstrip("/"))
    title = f"freebuff stalled · {project}" if project else "freebuff stalled"
    where = f"at {pane} on the Mac" if pane else "not in tmux"
    if not TEXT_AGENT:
        # metadata only: `why` is fbtodo's own measurement ("stopped 12m ago with 2/5 steps
        # done"), and the session is named so a silent push can still be traced
        session = str(state.get("session") or "-")
        return title, "\n".join(part for part in (why, f"session {session}", where) if part)
    goal = " ".join(str(state.get("goal") or "").split())
    head = f"Goal: {goal}" if goal else " ".join(str(state.get("first_prompt") or "").split())
    if len(head) > 200:
        head = head[:197] + "…"
    return title, "\n".join(part for part in (head, why, where) if part)


def push(state, why, pane) -> bool:
    """Hand it to phone.sh, detached: a poll must not wait on a network."""
    if not os.path.exists(PHONE):
        return False
    title, body = phone_message(state, why, pane)
    try:
        subprocess.Popen(
            [PHONE, "--title", title, "--message", body, "--priority", "high", "--tags", "warning"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
    except OSError:
        return False
    return True


def phone_ready() -> bool:
    if not os.path.exists(PHONE):
        return False
    try:
        proc = subprocess.run([PHONE, "--print"], capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and "topic=set" in proc.stdout


class one_pusher:
    """Serialize decide+claim, so two watchers cannot both announce the same stop."""

    def __enter__(self):
        self.handle = None
        if fcntl is None:
            return self
        try:
            self.handle = open(f"{STATE}.lock", "w")
            fcntl.flock(self.handle, fcntl.LOCK_EX)
        except OSError:
            self.handle = None
        return self

    def __exit__(self, *_exc):
        if self.handle is not None:
            try:
                self.handle.close()
            except OSError:
                pass
        return False


def main() -> int:
    argv = sys.argv[1:]
    print_only = "--print" in argv
    quiet = "--quiet" in argv
    watch_pid = None
    instance_of = None
    pane = None
    if "--watch-pid" in argv:
        watch_pid = argv[argv.index("--watch-pid") + 1]
    if "--pane" in argv:
        pane = argv[argv.index("--pane") + 1]
    for arg in argv:
        if arg.isdigit() and arg not in (watch_pid, instance_of, pane):
            instance_of = arg
    state = snapshot(watch_pid, instance_of)
    text = pane_text(pane)
    doc = read_state()
    push_now, why, key = decide(state, text, watch_pid, doc)
    if print_only:
        said = f"STALL: {key} — {why}" if push_now else f"silent: {why}"
        print(said)
        return 0
    if not push_now:
        return 0
    if not phone_ready():
        if not quiet:
            print("pause-bell: nothing configured to send to (see phone.sh --print)", file=sys.stderr)
        return 78
    with one_pusher():
        doc = read_state()  # re-read under the lock: a racing pass may have claimed
        push_now, why, key = decide(state, text, watch_pid, doc)
        if push_now:
            got = claims(doc)
            got[key] = int(time.time() * 1000)
            doc.update(schema=1, stalled=got, pushed_ms=int(time.time() * 1000), why=why[:200])
            write_state(doc)
    if not push_now:
        return 0
    if not push(state, why, pane):
        if not quiet:
            print("pause-bell: could not hand the push to phone.sh", file=sys.stderr)
        return 78
    return 0


if __name__ == "__main__":
    sys.exit(main())
