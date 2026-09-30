#!/usr/bin/env python3
"""Ring and push when a freebuff session dies BY ITSELF.

The finish bell answers "did the agent finish the task". This answers the other
question: did the session end because the user ended it, or did it DROP? Silence is not
an option for the second one — a session that dies while you are away is exactly the case
nothing else on this Mac reports, and the tab title went with the terminal.

Three ways a drop is witnessed, all funnelled through here so the decision, the log line
and the push cannot drift apart:

    --local --exit CODE   the `freebuff()` wrapper's post-mortem: it watched the CLI's
                          exit status and the store's own per-iteration `turn_ended`
    --watch PID           the same question asked after the wrapper is gone — a terminal
                          that dies takes the shell with it, so a watchdog that survives
                          it has "no report arrived" as its witness
    --nas --fb F          a NAS session whose marker outlived it (`F`=0, a killed ssh or
                          window) or whose CLI vanished while its `fb` was still up (`F`=1)

A DROP is:
  * an unclean exit — anything but 0 and 130, i.e. killed by a signal or an error exit;
  * an end while the agent's turn had NOT ended (the journal's own `shouldEndTurn`),
    whatever the status: the work stopped mid-step;
  * a session that vanished with no report at all.

An unclean exit and a vanished session always report. The one ambiguity is the user's own
interrupt, whose only witness is "the turn had not ended": `FREEBUFF_DROP_INTERRUPT=off`
(or a `drop-interrupt` file beside this script) silences that signature and nothing else.

Exit: 0 nothing on its own happened (the caller's usual exit chime still applies) ·
10 a drop, reported · 2 usage · 70 it could not decide.
`--print` decides and reports without ringing, pushing or logging, and says why either way.

Environment: FREEBUFF_DROP_STATE / FREEBUFF_DROP_LOG relocate the claim and the log (the
sandbox test drives both); FREEBUFF_PHONE_SH points at a different sender; FREEBUFF_DROP_POLL
sets how often a `--watch` asks the pid; FREEBUFF_BELL / FREEBUFF_PHONE mute the two halves
of a report (read by bell.sh and phone.sh).
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# The test drives this from a sandbox home; the "already reported" claim is the one piece
# of state that must not be shared with a real session.
STATE = os.environ.get("FREEBUFF_DROP_STATE") or os.path.join(HERE, "drop.state")
LOG = os.environ.get("FREEBUFF_DROP_LOG") or os.path.join(HERE, "drop.log")
BELL = os.path.join(HERE, "bell.sh")
PHONE = os.environ.get("FREEBUFF_PHONE_SH") or os.path.join(HERE, "phone.sh")
def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


TIMEOUT = 6.0             # a decision never waits longer than this on a local store
NAS_TIMEOUT = 25.0        # ...but the NAS store is one ssh round trip away
POLL = _env_float("FREEBUFF_DROP_POLL", 2.0)   # --watch: how often the pid is asked
GRACE = 6.0               # --watch: how long a report may still arrive after the shell
MAX_WAIT = 12 * 3600.0    # --watch: never outlive a marathon session
EX_DROP = 10
CLEAN_EXITS = {0, 130}    # quit, and SIGINT — the user's own interrupt
MAX_ERR = 240             # the stderr tail that fits in a notification
LOG_CAP = 65536           # the drop log is a record, not a history (like phone.log)

ANSI = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"      # CSI
    r"|\x1b[@-Z\\-_]"                 # two-character escapes
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC (window title and friends)
)


# ------------------------------------------------------------------ small helpers
def off_switch(name: str, default: str = "on") -> bool:
    """An on/off switch the way bell.sh has them: the environment, then a state file."""
    value = os.environ.get(f"FREEBUFF_DROP_{name.upper()}", "")
    path = os.path.join(HERE, f"drop-{name}")
    if not value and os.path.exists(path):
        try:
            with open(path) as handle:
                value = handle.read().strip()
        except OSError:
            value = ""
    return (value or default).strip().lower() in {"off", "0", "false", "no", "disable", "disabled"}


def signal_name(code: int) -> str:
    """`143` -> `SIGTERM`. A shell reports a signalled child as 128+n."""
    if code < 128:
        return ""
    try:
        return signal.Signals(code - 128).name
    except ValueError:
        return f"signal {code - 128}"


def fbtodo_binary():
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


def store_snapshot(cwd: str | None, nas: bool = False) -> dict | None:
    """What the store says about the session that just ended.

    `fbtodo` answers from the journal on disk, so this still works with the CLI gone —
    which is the whole point: "the agent had not finished" is only knowable from there.
    For `--nas` it is the far side's store (`fbtodo -s nas json`), one ssh away.
    """
    binary = fbtodo_binary()
    if not binary:
        return None
    if nas:
        argv, timeout, where = [binary, "-s", "nas", "json"], NAS_TIMEOUT, None
    else:
        # --cwd is a daemon flag: the CLI store is chosen from the directory the process
        # runs in, so the caller's directory has to be the subprocess's.
        argv, timeout, where = [binary, "-s", "cli", "json"], TIMEOUT, cwd or None
    try:
        proc = subprocess.run(argv, cwd=where, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        doc = json.loads(proc.stdout)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def mid_turn(state: dict | None) -> bool:
    """Did the session stop while the agent was still working?

    `turn_ended` is the journal's own end-of-turn record, so a five-minute command in
    flight cannot look like an ending — and a session that quit at its prompt cannot look
    like a death. Only the local CLI store records it; a NAS store says `False` because
    that build writes none, which is why NAS drops are decided from the marker instead.
    """
    return bool(state) and state.get("backend") == "cli" and not state.get("turn_ended")


def still_served(state: dict | None) -> bool:
    """Is the session this state describes still running?

    The store is chosen by DIRECTORY, and a directory can host more than one session, so
    the state a post-mortem reads is not necessarily the session that just ended. A live
    `instance_pid` is proof that it is not: that session is still at work, so its
    unfinished list cannot witness this ending. This is not hypothetical — 2026-09-21
    18:17 pushed "freebuff dropped · myproj, quit mid-turn" for a session that was
    mid-turn because it was WORKING, and which finished its turn a minute later and is
    still running (the ending belonged to a short-lived session in the same directory).

    No pid means no proof either way, so the earlier reading stands.
    """
    served = int((state or {}).get("instance_pid") or 0)
    return bool(served) and pid_alive(served)


def read_tail(path: str | None, max_bytes: int = LOG_CAP) -> str:
    if not path:
        return ""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
            return handle.read().decode("utf-8", "replace")
    except OSError:
        return ""


def error_tail(path: str | None) -> str:
    """The last thing the session printed, as one line fit for a notification.

    A full-screen app writes escape sequences and carriage-return frames into that log, so
    it is stripped, split, and filtered down to lines that actually say something (a
    spinner frame has no alphanumerics at all).
    """
    # ...and the spinner's own frames, which are braille cells rather than escapes.
    text = re.sub(r"[\u2800-\u28ff]", "", ANSI.sub("", read_tail(path))).replace("\x08", "")
    lines = [re.sub(r"\s+", " ", part).strip() for part in re.split(r"[\r\n]+", text)]
    spoken = [line for line in lines if any(ch.isalnum() for ch in line)]
    tail = " · ".join(spoken[-2:])
    return tail[: MAX_ERR - 1] + "…" if len(tail) > MAX_ERR else tail


# -------------------------------------------------------------- the drop questions
def decide_local(exit_code: int, state: dict | None) -> tuple[bool, str, str]:
    """(drop?, why, priority) for a session whose exit status was watched."""
    if exit_code not in CLEAN_EXITS:
        name = signal_name(exit_code)
        return True, f"died on its own ({name or f'exit {exit_code}'})", "high"
    if mid_turn(state):
        if still_served(state):
            # The list is not this session's: whatever ended, the store was read from a
            # neighbour that is still working, and a clean exit judged against somebody
            # else's half-finished turn is exactly how a healthy session got reported.
            return False, "the user quit — the unfinished list belongs to a session still running", "default"
        if off_switch("interrupt"):
            return False, "the turn was interrupted, and interruptions are muted", "default"
        how = "interrupted" if exit_code == 130 else "quit"
        return True, f"{how} mid-turn — the agent had not finished", "default"
    return False, "the user quit", "default"


def decide_vanished(state: dict | None) -> tuple[bool, str, str]:
    """(drop?, why, priority) for a session that ended with no report at all.

    The wrapper never got to say anything, so the store is the only witness: a session
    that went away with its turn finished was almost certainly a closed window, and one
    that left mid-turn (or left no readable store behind) is a death.
    """
    if mid_turn(state):
        return True, "the session vanished mid-turn (the terminal or the process died)", "high"
    if state:
        return False, "the session vanished after its turn had ended", "default"
    return True, "the session vanished with nothing left to read", "high"


def decide_nas(fb: str, live: bool, _state: dict | None) -> tuple[bool, str, str]:
    """(drop?, why, priority) for a NAS session the Mac-side watcher saw go.

    `fb` is the host wrapper's marker: `1` alive, `0` stale (its pid is gone, i.e. the ssh
    or the window was killed under it), `-` no marker at all — which is also what a clean
    exit looks like, and what a NAS without the hook looks like, so it cannot be called a
    drop without inventing one.
    """
    if fb == "0":
        return True, "the session was killed — its marker outlived it", "high"
    if fb == "1" and not live:
        return True, "the freebuff process disappeared while the session was still up", "high"
    return False, "the session closed normally", "default"


# --------------------------------------------------------------- report and claim
def sweep_records(max_age: float = 24 * 3600.0) -> None:
    """Drop report files older than a day: one is written per session, and sessions end.

    The watchdog consumes its report (and removes it) and the wrapper removes it after
    killing the watchdog, so this only catches a session that was hard-killed between the
    two — but nothing here should pile up unchecked.
    """
    try:
        names = os.listdir(HERE)
    except OSError:
        return
    now = time.time()
    for name in names:
        if not (name.startswith("drop-session-") and name.endswith(".report")):
            continue
        path = os.path.join(HERE, name)
        try:
            if now - os.path.getmtime(path) > max_age:
                os.unlink(path)
        except OSError:
            pass


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
        if len(doc.get("claimed") or {}) > 50:  # a record, not a history
            keep = sorted(doc["claimed"].items(), key=lambda kv: kv[1])[-50:]
            doc["claimed"] = dict(keep)
        with open(tmp, "w") as handle:
            json.dump(doc, handle)
        os.replace(tmp, STATE)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


class claim_lock:
    """Serialize decide+claim, so two passes cannot both push for one death.

    The local path is a single post-mortem, but a NAS watcher can be doubled by a stale
    lock or an upgrade race and every pass would ask at its own phase — the same window
    `todo-bell.py` guards. Best effort: no lock is not a reason to skip the report.
    """

    def __enter__(self):
        self.handle = None
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


def title_of(state: dict | None, where: str, nas: bool) -> str:
    """`where` is a directory (the session's cwd, or the NAS marker's): name it, not it."""
    project = os.path.basename(str(where or (state or {}).get("cwd") or "").rstrip("/"))
    kind = "NAS freebuff dropped" if nas else "freebuff dropped"
    return f"{kind} · {project}" if project else kind


def body_of(why: str, state: dict | None, err: str) -> str:
    """Why it dropped, what it was doing, and the last thing it said."""
    lines = [why]
    goal = " ".join(str((state or {}).get("goal") or (state or {}).get("first_prompt") or "").split())
    if goal:
        lines.append(goal[:200])
    total = int((state or {}).get("total") or 0)
    if total:
        lines.append(f"{int((state or {}).get('done') or 0)}/{total} steps done")
    if err:
        lines.append(f"stderr: {err}")
    return "\n".join(lines)


def ring(why: str, tty: str) -> None:
    try:
        subprocess.run([BELL, "--drop", tty, why], timeout=TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def push(title: str, body: str, priority: str) -> bool:
    """Hand the notification to phone.sh, detached: nothing waits on a network."""
    if not os.path.exists(PHONE):
        return False
    try:
        subprocess.Popen(
            [PHONE, "--title", title, "--message", body,
             "--priority", priority, "--tags", "warning"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
    except OSError:
        return False
    return True


def log_drop(kind: str, why: str, state: dict | None, err: str) -> None:
    """The error message the watchdog saw, kept on disk as well as pushed.

    A phone can be missed, and the log survives the terminal that a drop usually takes
    with it — this is the file to read afterwards.
    """
    session = (state or {}).get("session") or "-"
    line = (
        f"{time.strftime('%Y-%m-%d %H:%M:%S')} drop {kind} session={session} "
        f"why={why}" + (f' stderr="{err}"' if err else "")
    )
    try:
        with open(LOG, "a") as handle:
            handle.write(line + "\n")
    except OSError:
        return
    try:
        if os.path.getsize(LOG) > LOG_CAP:
            with open(LOG, "rb") as handle:
                handle.seek(max(0, os.path.getsize(LOG) - LOG_CAP // 4))
                tail = handle.read()
            with open(LOG, "wb") as handle:
                handle.write(tail)
    except OSError:
        pass


def report(kind: str, why: str, state: dict | None, err: str, where: str,
           nas: bool, priority: str, tty: str, print_only: bool) -> None:
    title = title_of(state, where, nas)
    body = body_of(why, state, err)
    if print_only:
        print(f"DROP: {why}  [{kind} session {(state or {}).get('session') or '-'}]")
        print(f"would push: {title} | {' ~ '.join(body.splitlines())}")
        return
    log_drop(kind, why, state, err)
    ring(why, tty)
    push(title, body, priority)


# ------------------------------------------------------------------------ modes
def local_mode(argv: dict, print_only: bool) -> int:
    state = argv.get("state")
    tty = argv.get("tty") or "/dev/tty"
    drop, why, priority = decide_local(argv["exit"], state)
    if not drop:
        if print_only:
            print(f"no drop: {why}")
        return 0
    # Keyed by the session, not by this process: the claim has to outlive the short-lived
    # process that writes it. With no session to key on (an unreadable store) the report is
    # made rather than risk swallowing a death — the wrapper asks once per session anyway.
    session = (state or {}).get("session") or ""
    key = f"local:{argv.get('shell_pid') or '-'}:{session}" if session else None
    with claim_lock():
        if key and key in (read_state().get("claimed") or {}):
            if print_only:
                print(f"no drop: already reported this session ({session})")
            return 0
        if print_only:
            report("local", why, state, argv.get("err"), argv.get("where", ""), False,
                   priority, tty, True)
            return EX_DROP
        if key:
            doc = read_state()
            doc.setdefault("claimed", {})[key] = int(time.time() * 1000)
            write_state(doc)
    report("local", why, state, argv.get("err"), argv.get("where", ""), False,
           priority, tty, False)
    return EX_DROP


def vanished_report(argv: dict, print_only: bool, tty: str) -> int:
    state = argv.get("state")
    drop, why, priority = decide_vanished(state)
    if not drop:
        if print_only:
            print(f"no drop: {why}")
        return 0
    key = f"vanished:{argv.get('pid')}"
    with claim_lock():
        if key in (read_state().get("claimed") or {}):
            return 0
        if print_only:
            report("vanished", why, state, argv.get("err"), argv.get("where", ""), False,
                   priority, tty, True)
            return EX_DROP
        doc = read_state()
        doc.setdefault("claimed", {})[key] = int(time.time() * 1000)
        write_state(doc)
    report("vanished", why, state, argv.get("err"), argv.get("where", ""), False,
           priority, tty, False)
    return EX_DROP


def watch_mode(argv: dict, print_only: bool) -> int:
    """Wait for the shell to go and for no report to arrive, then report the vanishing.

    A terminal that dies takes the wrapper with it mid-`command freebuff`, so the exit
    status it would have reported is never written. This is spawned disowned by the
    wrapper: while the shell lives it only asks `kill -0`; a report appearing (the normal
    exit path) ends it silently. The store is read at the moment of death, never at
    spawn — a snapshot taken here would describe the START of the session.
    """
    pid = int(argv["pid"])
    record = argv.get("record")
    tty = argv.get("tty") or "/dev/tty"
    deadline = time.monotonic() + MAX_WAIT
    while True:
        if record and os.path.exists(record):
            consume(record)
            return 0
        if not pid_alive(pid):
            wait_until = time.monotonic() + float(argv.get("grace") or GRACE)
            while time.monotonic() < wait_until:
                time.sleep(0.2)
                if record and os.path.exists(record):
                    consume(record)
                    return 0
            argv["state"] = store_snapshot(argv.get("cwd") or None)
            argv["err"] = error_tail(argv.get("errlog"))
            code = vanished_report(argv, print_only, tty)
            consume(record)
            return code
        if time.monotonic() > deadline:
            return 0
        time.sleep(POLL)


def consume(record: str | None) -> None:
    """A report that has been read is finished with: it is one session's, and it is gone."""
    if not record:
        return
    try:
        os.unlink(record)
    except OSError:
        pass


def truthy(value) -> bool:
    """`--live 0` is a real answer, and `bool("0")` is True — parse it as text."""
    return str(value).strip().lower() in {"1", "yes", "true", "on"}


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def nas_mode(argv: dict, print_only: bool) -> int:
    state = argv.get("state") or store_snapshot(None, nas=True)
    tty = argv.get("tty") or "/dev/tty"
    drop, why, priority = decide_nas(argv.get("fb") or "-", truthy(argv.get("live")), state)
    if not drop:
        if print_only:
            print(f"no drop: {why}")
        return 0
    session = (state or {}).get("session") or ""
    key = f"nas:{session}" if session else None
    with claim_lock():
        if key and key in (read_state().get("claimed") or {}):
            if print_only:
                print(f"no drop: already reported this session ({session})")
            return 0
        if print_only:
            report("nas", why, state, argv.get("err"), argv.get("where", ""), True,
                   priority, tty, True)
            return EX_DROP
        if key:
            doc = read_state()
            doc.setdefault("claimed", {})[key] = int(time.time() * 1000)
            write_state(doc)
    report("nas", why, state, argv.get("err"), argv.get("where", ""), True,
           priority, tty, False)
    return EX_DROP


# -------------------------------------------------------------------- interface
def parse(argv: list[str]) -> dict:
    out: dict = {"exit": None, "print": False, "tty": "/dev/tty", "grace": GRACE}
    flags = {"--local": "local", "--nas": "nas"}
    takes_value = {
        "--exit": "exit", "--cwd": "cwd", "--stderr-log": "errlog", "--shell-pid": "shell_pid",
        "--tty": "tty", "--record": "record", "--grace": "grace", "--fb": "fb",
        "--live": "live",
    }
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--watch":       # --watch PID: the mode and its one argument
            out["mode"] = "watch"
            i += 1
            if i >= len(argv):
                raise SystemExit(2)
            out["pid"] = argv[i]
        elif arg in flags:
            out["mode"] = flags[arg]
        elif arg in ("--print", "--dry-run"):
            out["print"] = True
        elif arg in ("-q", "--quiet"):
            pass  # the same shape as phone.sh: a caller may ask for quiet, nothing to say
        elif arg in takes_value:
            i += 1
            if i >= len(argv):
                raise SystemExit(2)
            out[takes_value[arg]] = argv[i]
        elif arg in ("-h", "--help"):
            out["help"] = True
        else:
            raise SystemExit(2)
        i += 1
    return out


def main() -> int:
    try:
        argv = parse(sys.argv[1:])
    except SystemExit:
        print(__doc__.split("Exit:")[0].strip(), file=sys.stderr)
        return 2
    if argv.get("help"):
        print(__doc__)
        return 0
    mode = argv.get("mode")
    if not mode:
        print("usage: drop-bell.py --local|--watch|--nas [...]", file=sys.stderr)
        return 2

    sweep_records()
    where = argv.get("cwd") or ""
    argv["where"] = where
    if mode == "watch":
        if not argv.get("pid"):
            print("--watch needs the pid of the shell that started freebuff", file=sys.stderr)
            return 2
        return watch_mode(argv, bool(argv["print"]))

    argv["err"] = error_tail(argv.get("errlog"))
    if mode == "local":
        if argv.get("exit") is None:
            print("--local needs --exit CODE (the status the CLI exited with)", file=sys.stderr)
            return 2
        argv["exit"] = int(argv["exit"])
        argv["state"] = store_snapshot(where or None)
        return local_mode(argv, bool(argv["print"]))

    return nas_mode(argv, bool(argv["print"]))


if __name__ == "__main__":
    sys.exit(main())
