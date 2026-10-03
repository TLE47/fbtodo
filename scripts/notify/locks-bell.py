#!/usr/bin/env python3
"""Say when a CLAIM has gone wrong — the fifth bell, and the only one that is not a session.

Four siblings one per thing that can hold a session up: `todo-bell.py` says the work ended,
`ask-bell.py` says it is asking a question, `pause-bell.py` says it stopped mid-list,
`pane-bell.py` says the panes are not being looked after. This says the claims are not, and
it is the only bell whose subject is not a session at all but the records that say who is
watching:

  - an ORPHAN: a watcher, keeper or NAS watcher RUNNING with a claim no reader can find. The
    process holds a lock on an inode the name has left behind, so every file-based read calls
    the role not running, a new one may be started over the fresh name, and whatever the
    process is protecting is unguarded from the outside.
  - a BROKEN TIE: a claim file that is FREE while its record names a process that is alive.
    The record and the file are no longer the same claim, which is what the name-versus-inode
    comparison exists to catch.

WHY THIS IS WORTH A NOTIFICATION. Neither state is visible from anywhere else: the stores say
nothing about processes, the pane log records panes that came back, and the audit that sees
both — `fbtodo locks` — only runs when somebody types it. Measured while this was built:
after a killed watcher (SIGKILL leaves the record and drops the lock) the machine goes on
reporting a watcher that is not running until the next ask clears it, and an orphan keeps
running for as long as nobody looks. The phone is for exactly the looking nobody does.

ONE PUSH PER OCCURRENCE, NOT PER PASS. A watch polls every few seconds, so a naive bell would
push the same news forever. The claim is the OCCURRENCE: a key is claimed when it is first
announced and FORGOTTEN the moment it stops being true, so the same claim going wrong again
later is new news. That is the opposite of a cooldown, and deliberately — a claim that has
been orphaned for an hour has not stopped being orphaned.

A grace period, because a claim being born and a keeper handing its claim over look wrong for
a moment: `write_lock` creates the file a breath before it locks it, and `locks --fix` may
remove a leftover just as its owner re-claims. 10s is many polls — long enough that no
healthy hand-over reaches it, short enough that a real orphan is announced while there is
still something to repair.

It reads the audit rather than re-deriving it (`fbtodo locks --json`, the same rows the
operator would see), which is the rule every bell here follows: the tool owns the fact, this
owns only WHEN you are told. Runs against the state root of whichever fbtodo is found —
FREEBUFF_FBTODO, then PATH.

usage: locks-bell.py [--print] [--quiet]

Environment: FREEBUFF_LOCKS_BELL_STATE overrides the one-push-per-occurrence record (tests);
FREEBUFF_LOCKS_GRACE (10s) is how long a finding must last to count; FREEBUFF_FBTODO points
at another fbtodo; FREEBUFF_PHONE=off mutes the push and FREEBUFF_PHONE_SH points at a
different sender (both tests).

Exit: 0 decided · 78 a claim is failing and there is nothing configured to send to.
"""

from __future__ import annotations

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
STATE = os.environ.get("FREEBUFF_LOCKS_BELL_STATE") or os.path.join(HERE, "locks-bell.state")
PHONE = os.environ.get("FREEBUFF_PHONE_SH") or os.path.join(HERE, "phone.sh")
TIMEOUT = 30.0
# How long a finding must last before it is a finding rather than a claim being born or a
# claim being handed over. Many polls at any sane cadence.
GRACE = float(os.environ.get("FREEBUFF_LOCKS_GRACE") or 10.0)
CLAIM_MAX = 50
SELF = "locks-bell"


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


def audit() -> dict | None:
    """fbtodo's own answer about every claim of this state root, or None.

    Asked of the tool that owns the fact rather than re-derived here: which files are claims,
    what state each is in, and which running processes no claim names is the audit's whole
    job, and a second copy of it would drift. `--json` is the same rows the text output
    prints, so the bell and the operator cannot disagree about what is wrong.
    """
    binary = fbtodo_binary()
    if not binary:
        return None
    out = _run([binary, "locks", "--json", "--quiet"])
    if not out.strip():
        return None
    try:
        doc = json.loads(out)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def findings(doc: dict) -> dict[str, dict]:
    """Every claim finding, keyed by the THING that is wrong — the bell's unit of news.

    Two shapes, and only these two: an orphan (a live role process no claim names — from
    the process half of the audit, which is why it is on the claim row) and a broken tie (a
    free file whose record names a live process, which is the name-versus-inode mismatch).
    A held claim, a dead leftover and an absent file are not findings: the audit explains
    each in its own row, and a bell that rang for a claim being born would be ignored inside
    a day.
    """
    out: dict[str, dict] = {}
    for claim in doc.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        role = str(claim.get("role") or "?")
        path = str(claim.get("path") or "")
        for proc in claim.get("orphans") or []:
            pid = proc.get("pid")
            if not pid:
                continue
            out[f"orphan:{role}:{pid}"] = {
                "kind": "orphan", "role": role, "pid": int(pid), "path": path,
                "exact": bool(proc.get("root_named", True)),
                "clipped": bool(proc.get("env_clipped", False)),
            }
        if (claim.get("state") == "free" and claim.get("name_inode") == "mismatch"
                and claim.get("pid_alive") and claim.get("pid")):
            pid = int(claim["pid"])
            out[f"tie:{role}:{pid}"] = {
                "kind": "tie", "role": role, "pid": pid, "path": path,
                "exact": False, "clipped": False,
            }
    return out


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


def observe(present: dict, doc: dict, now_ms: int) -> tuple[dict, dict, str]:
    """(failing, present, why) — the findings past the grace, and every one true now.

    Both keyed, because a push is about a SET: two claims broken on one pass is one message.
    `present` is what the claims are released against — a key that stops being true stops
    being claimed, so the same claim going wrong again later is announced again.
    """
    seen = _capped(doc.get("seen"))
    for key in present:
        seen.setdefault(key, now_ms)
    for key in [k for k in seen if k not in present]:
        seen.pop(key, None)  # not true right now: its clock and its claim both go
    if not present:
        return {}, {}, "every claim is held by a live process, and nothing is running untied"
    failing = {k: v for k, v in present.items() if now_ms - int(seen[k]) >= GRACE * 1000}
    if not failing:
        youngest = max(now_ms - int(v) for v in seen.values())
        return {}, present, f"a claim has been wrong for {youngest // 1000}s (needs {GRACE:.0f}s)"
    return failing, present, ""


def report(failing: dict) -> tuple[str, str, str]:
    """(kind, title, body) — one message for everything failing on this pass."""
    orphans = [f for f in failing.values() if f["kind"] == "orphan"]
    ties = [f for f in failing.values() if f["kind"] == "tie"]
    if orphans and ties:
        kind = "both"
        title = f"fbtodo locks · {len(orphans)} untied process(es), {len(ties)} broken name(s)"
    elif orphans:
        kind = "orphans"
        title = f"fbtodo locks · {len(orphans)} untied process(es)"
    else:
        kind = "ties"
        title = f"fbtodo locks · {len(ties)} broken claim name(s)"
    lines = []
    for finding in orphans:
        where = os.path.basename(finding["path"]) or finding["path"]
        tail = ""
        if not finding.get("exact"):
            tail = (" (its environment could not be read"
                    + (", it came back clipped" if finding.get("clipped") else "") + ")")
        lines.append(f"{finding['role']}: pid {finding['pid']} is running with no claim "
                     f"naming it — {where}{tail}")
    for finding in ties:
        where = os.path.basename(finding["path"]) or finding["path"]
        lines.append(f"{finding['role']}: {where} is free while pid {finding['pid']} is alive "
                     "— the name and the inode have parted")
    lines.append("`fbtodo locks` prints the row; `fbtodo locks --fix` ends an untied "
                 "process (never one it could not place)")
    return kind, title, "\n".join(lines)


def decide(failing: dict, claims: dict) -> tuple[bool, str, str, str]:
    """(push?, note, kind, title) — the note is printed either way; 'no push' needs one."""
    if not failing:
        return False, "", "", ""
    if not [key for key in failing if key not in claims]:
        return False, f"already announced ({len(failing)} still failing)", "", ""
    kind, title, body = report(failing)
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
            [PHONE, "--title", title, "--message", body, "--priority", "high",
             "--tags", "warning"],
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
    doc = audit()
    if doc is None:
        if print_only:
            print("silent: fbtodo could not answer — no fbtodo, or it failed")
        return 0
    present = findings(doc)
    doc = read_state()
    now_ms = int(time.time() * 1000)
    failing, present, why_not = observe(present, doc, now_ms)
    # A pass that finds nothing true releases every claim, which is what makes a
    # recurrence new news rather than a suppressed one.
    seen = _capped(doc.get("seen"))
    claimed = {k: v for k, v in _capped(doc.get("claimed")).items() if k in present}
    if print_only:
        push_now, note, kind, _title = decide(failing, claimed)
        print(f"LOCKS: {kind} — {note}" if push_now else f"silent: {why_not or note}")
        return 0
    if not failing:
        # Write only when something changed, and keep a claim whose key is still true —
        # dropping one that is still true would let a slow clock re-announce.
        if claimed != _capped(doc.get("claimed")) or seen != _capped(doc.get("seen")):
            write_state({**doc, "schema": 1, "seen": seen, "claimed": claimed,
                         "cleared_ms": now_ms})
        return 0
    if not phone_ready():
        if not quiet:
            print(f"{SELF}: nothing configured to send to (see phone.sh --print)",
                  file=sys.stderr)
        return 78
    with one_pusher():
        doc = read_state()  # re-read under the lock: a racing pass may have claimed
        now_ms = int(time.time() * 1000)
        failing, present, why_not = observe(present, doc, now_ms)
        seen = _capped(doc.get("seen"))
        claimed = {k: v for k, v in _capped(doc.get("claimed")).items() if k in present}
        push_now, note, kind, title = decide(failing, claimed)
        if push_now:
            for key in failing:
                claimed[key] = now_ms
            write_state({**doc, "schema": 1, "seen": seen, "claimed": _capped(claimed),
                         "pushed_ms": now_ms, "kind": kind, "why": note[:400]})
    if not push_now:
        return 0
    if not push(title, note):
        if not quiet:
            print(f"{SELF}: could not hand the push to phone.sh", file=sys.stderr)
        return 78
    return 0


if __name__ == "__main__":
    sys.exit(main())
