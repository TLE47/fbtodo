#!/usr/bin/env python3
"""Say when the todo pane is NOT being looked after, where every other bell says something.

Four siblings now, one per thing that can hold a session up or go wrong around it:
`todo-bell.py` says the work ended, `ask-bell.py` says it is asking a question,
`pause-bell.py` says it stopped mid-list, and this says the pane watcher is not doing its
job — a session has no todo pane and the keeper has not put one back, or there is no keeper
at all while a session is waiting for one.

WHY THIS IS WORTH A NOTIFICATION. The pane keeper is the only thing that repairs a pane,
and every failure it has is silent by construction: it has no stderr anybody reads, and
what it writes (a log beside the keeper's claim record) records a pane that CAME BACK, never one
that did not. Its two failure modes are both quiet — a pass that cannot open a pane (a
window too small, a pin pointing at a window that is gone, tmux refusing the split), and
the keeper itself being killed, replaced, or watching a server that has gone. Measured
while building this: those are the two states in which a session runs for the rest of its
life with no list at all, and the only way to notice was to go and look at the screen.

ONE PUSH PER OCCURRENCE, NOT PER PASS. The keeper polls every 3s and this is asked every
60s, so a naive watch would push the same news forever. The claim is the OCCURRENCE, not
the session: a key is claimed when the condition is first announced and FORGOTTEN the
moment it stops being true, so the same pane going missing again later is new news. That
is the opposite of a time-based cooldown, and deliberately: a condition that has been
quiet for an hour has not stopped being true.

A grace period, because `fbtodo why` reports a session with no pane as a matter of course
for the few seconds between the CLI starting and the keeper's first pass — and because the
wrapper splits the pane itself a moment before the CLI exists. 30s is ten keeper passes:
long enough that a healthy repair never reaches it, short enough that a real failure is
announced while there is still a session to look at.

Deliberately local. Every fact here is about panes on this machine's tmux, and every
session that could want a pane runs on this machine; the bell stays silent on its own when
there is no session to want one.

usage: pane-bell.py --keeper PATH [--print] [--quiet]

Environment: FREEBUFF_PANE_BELL_STATE overrides the one-push-per-occurrence record (tests);
FREEBUFF_FBTODO points at another fbtodo; FREEBUFF_PANE_GRACE (30s) is how long a session
may go without a pane before it counts; FREEBUFF_PHONE=off mutes the push and
FREEBUFF_PHONE_SH points at a different sender (both tests).

Exit: 0 decided · 78 the pane watcher is failing and there is nothing configured to send to.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

try:
    import fcntl
except ImportError:  # pragma: no cover - not a POSIX host
    fcntl = None

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.environ.get("FREEBUFF_PANE_BELL_STATE") or os.path.join(HERE, "pane-bell.state")
PHONE = os.environ.get("FREEBUFF_PHONE_SH") or os.path.join(HERE, "phone.sh")
TIMEOUT = 30.0
# How long a session may have no list pane before it is a failure rather than a startup.
# Ten keeper passes at the default 3s cadence.
GRACE = float(os.environ.get("FREEBUFF_PANE_GRACE") or 30.0)
CLAIM_MAX = 50
SELF = "pane-bell"


def default_state_dir() -> str:
    """fbtodo's state root, without asking fbtodo.

    `FBTODO_HOME`, then the XDG state directory the tool now uses, then — for a machine that
    has not been moved yet — the legacy `~/.freebuff`. The legacy root wins only while the
    new one has no keeper record and the old one does: that is a keeper still running from
    the old directory, and the bell should read the claim it actually holds.
    """
    home = os.path.expanduser("~")
    env = os.environ.get("FBTODO_HOME")
    if env:
        return os.path.expanduser(env)
    xdg = os.path.expanduser(
        os.environ.get("XDG_STATE_HOME") or os.path.join(home, ".local", "state")
    )
    new = os.path.join(xdg, "fbtodo")
    legacy = os.path.join(home, ".freebuff")
    if not os.path.exists(os.path.join(new, "fbtodo-pane-keeper.pid")) and os.path.exists(
        os.path.join(legacy, "fbtodo-pane-keeper.pid")
    ):
        return legacy
    return new


def keeper_log_for(keeper_path: str = "") -> str:
    """The keeper's log sits in the same directory as its claim: one state root, not two."""
    if keeper_path:
        return os.path.join(os.path.dirname(os.path.abspath(keeper_path)), "fbtodo-pane.log")
    return os.path.join(default_state_dir(), "fbtodo-pane.log")


KEEPER_LOG = keeper_log_for()


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


def alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (ProcessLookupError, ValueError, TypeError):
        return False
    except PermissionError:
        return True


def why() -> list[dict]:
    """fbtodo's own answer about every list pane on this server, and every session without one.

    Asked of the tool that owns the fact rather than re-derived here: the session/pane
    matching it does is the same code the keeper acts on, and a second copy of it would
    drift — which is how the keeper came to spend a build watching a server that was gone.
    """
    binary = fbtodo_binary()
    if not binary:
        return None
    out = _run([binary, "why", "--json", "--quiet"])
    if not out.strip():
        return None
    try:
        records = json.loads(out).get("panes")
    except ValueError:
        return None
    return records if isinstance(records, list) else None


def keeper_alive(keeper_path: str) -> tuple[bool, str]:
    """(alive, why) for this tmux's keeper, read from the record it writes for that purpose."""
    try:
        with open(keeper_path) as handle:
            rec = json.load(handle)
    except FileNotFoundError:
        return False, "no keeper record"
    except (OSError, ValueError):
        return False, "the keeper record is unreadable"
    if not isinstance(rec, dict):
        return False, "the keeper record is not a record"
    pid = rec.get("pid")
    if not alive(pid):
        return False, f"keeper pid {pid or '?'} is gone"
    return True, f"keeper pid {pid}"


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


def _capped(got: object) -> dict:
    if not isinstance(got, dict):
        return {}
    if len(got) > CLAIM_MAX:
        return dict(sorted(got.items(), key=lambda kv: kv[1], reverse=True)[:CLAIM_MAX])
    return dict(got)


def session_key(rec: dict) -> str:
    """One key per session that wants a pane and has none."""
    seed = f"{rec.get('anchor_note') or ''}|{rec.get('window_id') or rec.get('window') or ''}"
    return "session:" + hashlib.sha1(seed.encode("utf-8", "replace")).hexdigest()[:12]


def keeper_key() -> str:
    return "keeper:" + hashlib.sha1(b"pane-keeper").hexdigest()[:12]


def observe(records: list[dict], keeper_path: str, doc: dict, now_ms: int) -> tuple[dict, dict, str]:
    """(failing, present, why) — the keys past the grace period, and every key true now.

    Both are keyed dicts, because a push is about a SET: two sessions going without a pane
    on one pass is one message, not two. `present` is what the claims are released against
    — a key that stops being true stops being claimed, so the same pane going missing again
    later is announced again.
    """
    seen = _capped(doc.get("seen"))
    missing = [
        rec for rec in records
        if rec.get("role") == "local" and not rec.get("pane") and rec.get("anchor")
    ]
    present = {session_key(rec): rec for rec in missing}
    # The keeper only matters while something wants what it repairs.
    keeper_ok, keeper_note = keeper_alive(keeper_path)
    if missing and not keeper_ok:
        present[keeper_key()] = {"keeper": keeper_note, "missing": len(missing)}
    for key in present:
        seen.setdefault(key, now_ms)
    # A key that is not true right now is forgotten entirely: its clock and its claim.
    for key in [k for k in seen if k not in present]:
        seen.pop(key, None)
    if not present:
        # Say which of the two is true rather than assuming the flattering half: a
        # keeper that is not running is only a problem while something needs it.
        if keeper_ok:
            return {}, {}, "every session that wants a pane has one, and a keeper is watching"
        return {}, {}, "every session that wants a pane has one (no keeper, and none needed)"
    failing = {k: v for k, v in present.items() if now_ms - int(seen[k]) >= GRACE * 1000}
    if not failing:
        youngest = max(now_ms - int(v) for v in seen.values())
        return {}, present, f"a pane is missing for {youngest // 1000}s (needs {GRACE:.0f}s)"
    return failing, present, ""


def report(failing: dict, present: dict, seen: dict) -> tuple[str, str, str]:
    """(kind, title, body) — one message for everything failing on this pass."""
    keeper_down = keeper_key() in failing
    sessions = [rec for key, rec in failing.items() if key != keeper_key()]
    windows = sorted({str(rec.get("window") or "?") for rec in sessions})
    if keeper_down and sessions:
        kind, title = "both", f"fbtodo pane watcher down · {len(sessions)} session(s)"
    elif keeper_down:
        kind, title = "keeper", "fbtodo pane keeper down"
    elif len(windows) == 1:
        kind, title = "panes", f"fbtodo pane missing · {windows[0]}"
    else:
        kind, title = "panes", f"fbtodo pane missing · {len(sessions)} session(s)"
    now_ms = int(time.time() * 1000)
    lines = []
    for rec in sessions:
        for_age = (now_ms - int(seen.get(session_key(rec), now_ms))) // 1000
        lines.append(
            f"{rec.get('window') or '?'}: no list pane for {rec.get('anchor_note') or 'a session'}"
            f" — {for_age}s, and the keeper has not put one back"
        )
    if keeper_down:
        note = (present.get(keeper_key()) or {}).get("keeper") or "no keeper"
        lines.append(f"and {note} — nothing will put the pane back")
    lines.append("the keeper repairs this on its own; if it does not: fbtodo pane-watch --once")
    lines.append(f"its log: {KEEPER_LOG}")
    return kind, title, "\n".join(lines)


def decide(failing: dict, present: dict, seen: dict, claims: dict) -> tuple[bool, str, str, str]:
    """(push?, note, kind, title) — the note is printed either way; 'no push' needs one."""
    if not failing:
        return False, "", "", ""
    if not [key for key in failing if key not in claims]:
        return False, f"already announced ({len(failing)} still failing)", "", ""
    kind, title, body = report(failing, present, seen)
    return True, body, kind, title


def phone_ready() -> bool:
    if os.environ.get("FREEBUFF_PHONE") == "off" or not os.path.exists(PHONE):
        return False
    try:
        proc = subprocess.run([PHONE, "--print"], capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and "topic=set" in proc.stdout


def push(title: str, body: str) -> bool:
    """Hand it to phone.sh, detached: a poll must not wait on a network."""
    try:
        subprocess.Popen(
            [PHONE, "--title", title, "--message", body, "--priority", "high", "--tags", "warning"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
    except OSError:
        return False
    return True


class one_pusher:
    """Serialize observe+claim, so two watchers cannot both announce the same failure."""

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
    global KEEPER_LOG
    keeper_path = (
        argv[argv.index("--keeper") + 1] if "--keeper" in argv
        else os.path.join(default_state_dir(), "fbtodo-pane-keeper.pid")
    )
    KEEPER_LOG = keeper_log_for(keeper_path)
    records = why()
    if records is None:
        if print_only:
            print("silent: fbtodo could not answer — no fbtodo, or it failed")
        return 0
    doc = read_state()
    now_ms = int(time.time() * 1000)
    failing, present, why_not = observe(records, keeper_path, doc, now_ms)
    # A pass that finds nothing true releases every claim, which is what makes a
    # recurrence new news rather than a suppressed one.
    seen = _capped(doc.get("seen"))
    claimed = {k: v for k, v in _capped(doc.get("claimed")).items() if k in present}
    if print_only:
        push_now, note, kind, _title = decide(failing, present, seen, claimed)
        print(f"PANE: {kind} — {note}" if push_now else f"silent: {why_not or note}")
        return 0
    if not failing:
        # Write only when something actually changed, and keep a claim whose key is still
        # true — dropping one that is still true would let a slow clock re-announce.
        if claimed != _capped(doc.get("claimed")) or seen != _capped(doc.get("seen")):
            write_state({
                **doc, "schema": 1, "seen": seen, "claimed": claimed,
                "cleared_ms": now_ms,
            })
        return 0
    if not phone_ready():
        if not quiet:
            print(f"{SELF}: nothing configured to send to (see phone.sh --print)", file=sys.stderr)
        return 78
    with one_pusher():
        doc = read_state()  # re-read under the lock: a racing pass may have claimed
        failing, present, why_not = observe(records, keeper_path, doc, int(time.time() * 1000))
        seen = _capped(doc.get("seen"))
        claimed = {k: v for k, v in _capped(doc.get("claimed")).items() if k in present}
        push_now, note, kind, title = decide(failing, present, seen, claimed)
        if push_now:
            for key in failing:
                claimed[key] = int(time.time() * 1000)
            write_state({
                **doc, "schema": 1, "seen": seen, "claimed": _capped(claimed),
                "pushed_ms": int(time.time() * 1000), "kind": kind, "why": note[:400],
            })
    if not push_now:
        return 0
    if not push(title, note):
        if not quiet:
            print(f"{SELF}: could not hand the push to phone.sh", file=sys.stderr)
        return 78
    return 0


if __name__ == "__main__":
    sys.exit(main())
