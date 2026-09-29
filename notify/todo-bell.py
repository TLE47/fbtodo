#!/usr/bin/env python3
"""Ring when the AGENT HAS FINISHED THE TASK, not when the session exits.

The trigger is read from the todo list, via `fbtodo --instance-of <shell pid> json`:

    every todo completed        done == total > 0
    and its turn has ended      fbtodo's `turn_ended`, read from the journal's own
                                per-iteration `shouldEndTurn` record

Both halves matter, and the second is what makes this honest. Silence is NOT a signal:
a five-minute `run_terminal_command` leaves the journal untouched for minutes while the
agent is emphatically still working, so a quiet-window heuristic rings in the middle of
it. `shouldEndTurn` is written on the iteration that produced the agent's final answer,
so a tool call in flight can never ring the bell — however long it runs.

One ring per list: the list's fingerprint is remembered, so a finished list that stays on
screen does not ring again, while a new list (or the same one reopened) can.

The same decision, sent to the phone (phone.sh → ntfy), covers the case the bell cannot:
you are not at the Mac. A NAS session has no end-of-turn record to ask (that build
writes tool NAMES, never inputs, and no shouldEndTurn), so there the honest test is
"the list is complete and the store has stopped moving" — the store is written every
few seconds while the agent works.

The push is remembered against the list, in a map keyed by its fingerprint. That was a
single slot before, which silently assumed ONE session per Mac: the NAS watcher and a
local session overwrote each other's claim, so on every poll each decided the other had
never pushed — the phone got the same "done" every ~15s until the thread was muted. The
fingerprint includes the session, so two instances cannot collide in the map.

usage: todo-bell.py <shell-pid> [--print] [--tty PATH]
       todo-bell.py --nas-watch [--once] [--print]   follow a NAS session (started by
                                                     `fbtodo nas -f`), exiting with it
       --print  decide and report, play/send nothing (also says WHY it stayed silent)

Environment: FREEBUFF_TODO_BELL_STATE overrides the one-ring-per-list record (tests);
FREEBUFF_BELL=off mutes the chime (bell.sh); FREEBUFF_PHONE=off mutes the push
(phone.sh); FREEBUFF_PHONE_NAS_QUIET (45s) and FREEBUFF_PHONE_NAS_POLL (10s) tune the
NAS side; FREEBUFF_PHONE_SH points at a different sender (tests).
"""

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
# The test drives this from a sandbox home, and the "already rang for this list" record is
# the one piece of state that must not be shared with a real session.
STATE = os.environ.get("FREEBUFF_TODO_BELL_STATE") or os.path.join(HERE, "todo-bell.state")
BELL = os.path.join(HERE, "bell.sh")
# Is the AGENT's own text allowed in the push? Off by default: the goal, the sentence it
# finished on and the question on screen are written by a model and land on a phone, where
# a link, a number or an instruction reads as real. `FREEBUFF_PHONE_TEXT=agent` puts them
# back for an owner who wants them. The metadata (session, state, counts) always goes.
TEXT_AGENT = (os.environ.get("FREEBUFF_PHONE_TEXT") or "").strip().lower() in (
    "agent", "on", "1", "all", "full",
)
PHONE = os.environ.get("FREEBUFF_PHONE_SH") or os.path.join(HERE, "phone.sh")
TIMEOUT = 6.0


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


NAS_QUIET = _env_float("FREEBUFF_PHONE_NAS_QUIET", 45.0)
NAS_POLL = _env_float("FREEBUFF_PHONE_NAS_POLL", 10.0)


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


CLAIM_MAX = 50  # list ids remembered per action; one session writes one per list

# The state file's old shape was one slot per action, which two instances overwrote.
LEGACY_CLAIM = {"rung": ("list_id", "rung_ms"), "pushed": ("pushed_list_id", "pushed_ms")}


def claims(doc: dict, kind: str) -> dict:
    """The list ids already claimed for `kind` ("rung" | "pushed") as id -> ms.

    Folds the old single slot in on the way, so the first run of this shape does not
    re-fire for the list that was already claimed before it.
    """
    got = doc.get(kind)
    if not isinstance(got, dict):
        got = {}
    legacy_id, legacy_ms = LEGACY_CLAIM[kind]
    old = doc.get(legacy_id)
    if old and old not in got:
        got[old] = int(doc.get(legacy_ms) or 0)
    return got


def remember(doc: dict, kind: str, list_id: str | None) -> None:
    """Record the claim, oldest entries first when the map has to be trimmed."""
    if not list_id:
        return
    got = claims(doc, kind)
    got[list_id] = int(time.time() * 1000)
    if len(got) > CLAIM_MAX:
        for stale in sorted(got, key=lambda key: got[key])[: len(got) - CLAIM_MAX]:
            got.pop(stale, None)
    doc[kind] = got


def snapshot(root: int, source: str | None = None) -> dict | None:
    """The list for the session that belongs to this shell, or the NAS one.

    `source="nas"` asks `fbtodo -s nas json`, which follows the live session on the
    NAS (and is mtime-skipped on the far side: an unchanged transcript costs one ssh,
    no parse).
    """
    binary = fbtodo_binary()
    if not binary:
        return None
    if source == "nas":
        argv = [binary, "-s", "nas", "json"]
    elif root:
        argv = [binary, "--instance-of", str(root), "json"]
    else:
        return None
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return None


def decide(state: dict, doc: dict) -> tuple[bool, str]:
    """(ring?, why) — the reason is printed either way, because 'no ring' needs a story."""
    if state.get("backend") != "cli":
        return False, f"no local session to follow ({state.get('backend') or 'nothing'})"
    total = int(state.get("total") or 0)
    done = int(state.get("done") or 0)
    if not total:
        return False, "no todo list yet"
    if done < total:
        return False, f"still working ({done}/{total} done)"
    if not state.get("turn_ended"):
        return False, f"all {total} done, but the agent has not finished its turn"
    list_id = state.get("list_id")
    if not list_id:
        return False, "list has no identity to ring once for"
    if list_id in claims(doc, "rung"):
        return False, f"already rang for this list ({done}/{total})"
    return True, f"all {total} todos done and the turn ended"


def phone_decide(state: dict, doc: dict, now_ms: int | None = None) -> tuple[bool, str]:
    """(push?, why) — the same question as the bell's, answered for both backends.

    Local: exactly the bell's rule (every todo done AND the turn ended). NAS: the list
    is complete AND the store has been quiet for `NAS_QUIET` seconds, since that build
    records no end of turn. Either way the push is remembered against the LIST: one per
    list, the bell's own rule. It used to also require the store's mtime to have moved,
    which on a live NAS session rewrote the transcript every turn and pushed the SAME
    finished list every couple of minutes — the kind of repeat that gets a topic muted,
    or a thread collapsed and never looked at. A new turn writes a new list, and that
    pushes.
    """
    backend = state.get("backend") or ""
    total = int(state.get("total") or 0)
    done = int(state.get("done") or 0)
    if not total:
        return False, "no todo list yet"
    if done < total:
        return False, f"still working ({done}/{total} done)"
    list_id = state.get("list_id")
    if not list_id:
        return False, "list has no identity to push once for"
    mtime = int(state.get("store_mtime_ms") or 0)
    if backend == "nas":
        if not state.get("instance_alive"):
            return False, "no NAS session is running"
        if not mtime:
            return False, "no store activity to time the quiet window from"
        quiet = (int(now_ms or time.time() * 1000) - mtime) / 1000.0
        if quiet < NAS_QUIET:
            return False, f"the store is still moving ({quiet:.0f}s quiet, needs {NAS_QUIET:.0f}s)"
        why = f"all {total} todos done and the store quiet {quiet:.0f}s"
    else:
        if not state.get("turn_ended"):
            return False, f"all {total} done, but the agent has not finished its turn"
        why = f"all {total} todos done and the turn ended"
    if list_id in claims(doc, "pushed"):
        return False, f"already pushed for this list ({done}/{total})"
    return True, why


def nas_where(path: str) -> str:
    """Which NAS run this was: `projects/<name>/chats/<thread>`, or the nameless
    `projects/chats/<thread>` — where the thread's own clock is the honest label."""
    parts = [p for p in str(path).strip("/").split("/") if p]
    if "chats" not in parts:
        return ""
    i = parts.index("chats")
    if i >= 1 and parts[i - 1] != "projects":
        return parts[i - 1]
    thread = parts[i + 1] if len(parts) > i + 1 else ""
    if "T" in thread:
        return thread.split("T", 1)[1][:8].replace("-", ":")
    return thread


def ended_at(state: dict) -> int:
    """When the run finished, in epoch ms — the store's own last write.

    Not the moment we noticed: for a local session that is the transcript's final append
    and for the NAS it is the write the quiet window is measured from, so both are the
    agent stopping rather than the notifier waking up.
    """
    return int(
        state.get("store_mtime_ms")
        or state.get("source_updated_ms")
        or state.get("probed_ms")
        or 0
    )


def phone_message(state: dict) -> tuple[str, str]:
    """(title, body) — title says which run, body carries the big goal, the clock and
    the count.

    The goal is the part worth waking up for; the date and time are what place it (a
    push read hours later is otherwise impossible to date), and the count is the part
    that says it is over. A NAS store path names the run by its thread clock, which is
    what a Mac-side title cannot guess — so the body can spend its line on the finish
    time instead of repeating it.
    """
    backend = state.get("backend") or ""
    where = ""
    if backend == "nas":
        where = nas_where(state.get("session") or state.get("target") or "")
        title = f"NAS freebuff done · run {where}" if where else "NAS freebuff done"
    else:
        project = os.path.basename((state.get("cwd") or "").rstrip("/"))
        title = f"freebuff done · {project}" if project else "freebuff done"
    ended = ended_at(state)
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(ended / 1000.0)) if ended else ""
    count = f"{int(state.get('done') or 0)}/{int(state.get('total') or 0)} steps done"
    tail = " · ".join(part for part in (count, when) if part)
    # METADATA ONLY by default: which session, which state, how much of the list it got
    # through. The prose below is the agent's own words — an LLM's sentence arriving on a
    # phone, where a link, a number or an instruction reads as real — so it is opt-in
    # (`FREEBUFF_PHONE_TEXT=agent`), not a default.
    if not TEXT_AGENT:
        session = str(state.get("session") or "-")
        return title, "\n".join(part for part in (tail, f"session {session}") if part)
    # The big goal is the agent's own `Goal:` line; only when the list has none is the
    # session's opening request worth showing — and then it must not be labelled a goal.
    goal = " ".join(str(state.get("goal") or "").split())
    head = f"Goal: {goal}" if goal else " ".join(str(state.get("first_prompt") or "").split())
    if len(head) > 200:
        head = head[:197] + "…"
    # What came of it, in the agent's own words: the heading says what the task was FOR,
    # this says what happened and what changed. fbtodo takes the first line of the agent's
    # last answer and keeps it to SUMMARY_MAX_CHARS; the cap here is the phone's, not the
    # store's, and a store with no prose (a NAS transcript carries none today) simply
    # contributes no line.
    said = " ".join(str(state.get("summary") or "").split())
    if len(said) > 220:
        said = said[:219] + "…"
    return title, "\n".join(part for part in (head, said, tail) if part)


def phone_ready() -> tuple[bool, str]:
    """(configured?, detail) — a notifier with no topic has nothing to do, ever."""
    if not os.path.exists(PHONE):
        return False, f"no sender at {PHONE}"
    try:
        proc = subprocess.run([PHONE, "--print"], capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return False, f"cannot run {PHONE}"
    if proc.returncode != 0:
        return False, (proc.stderr.strip() or f"{PHONE} --print exited {proc.returncode}")
    if "topic=set" not in proc.stdout:
        return False, "phone.sh has no NTFY_TOPIC (run `phone.sh --init`)"
    return True, proc.stdout.strip()


def push(state: dict) -> bool:
    """Hand the notification to phone.sh, detached: the timer must not wait on a network.

    phone.sh bounds itself (connect 10s, max-time 30s, curl's own retry of 429/5xx) and
    logs a failure instead of queueing it — a "done" that arrives an hour late is worse
    than one that never arrives.
    """
    if not os.path.exists(PHONE):
        return False
    title, body = phone_message(state)
    try:
        subprocess.Popen(
            [PHONE, "--title", title, "--message", body],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
    except OSError:
        return False
    return True


class one_pusher:
    """Serialize decide+claim, so two passes cannot both send for the same finish.

    More than one NAS watcher can be running (a stale lock, an upgrade race), and both
    would ask this script at their own phase. Each asks the store, decides, and only then
    records the push — so two passes that overlap in those few milliseconds would both
    send. Holding an exclusive lock across that window makes the second pass re-read the
    claim the first one just wrote, and stay silent. Best effort: no lock available is
    not a reason to skip the notification.
    """

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


def claim(doc: dict, state: dict) -> dict:
    """Remember the push BEFORE sending: a repeat tick must not push twice."""
    remember(doc, "pushed", state.get("list_id"))
    doc.update(
        pushed_ms=int(time.time() * 1000),
        session=state.get("session"),
        done=state.get("done"),
        total=state.get("total"),
    )
    return doc


def nas_watch(args: list[str]) -> int:
    """Follow the NAS session this Mac is driving; push when its list is done and quiet.

    Started by the NAS watcher (`fbtodo nas -f`) for the life of a session and left to
    exit with it — the same lifetime rule as the pane, but it neither needs the pane nor
    needs you to be sitting in front of it, which is the whole point of a phone push.
    """
    once, print_only = "--once" in args, "--print" in args
    ready, detail = phone_ready()
    if not ready:
        if print_only:
            print(f"no push: {detail}")
        return 0 if print_only else 78
    if print_only:
        print(f"sender: {detail}")
    while True:
        state = snapshot(0, source="nas")
        if state is None:
            if print_only:
                print("no push: fbtodo could not answer for the NAS")
            if once:
                return 0
            time.sleep(NAS_POLL)
            continue
        doc = read_state()
        send, why = phone_decide(state, doc)
        if print_only:
            print(f"{'PUSH' if send else 'no push'}: {why}  [session {state.get('session')}]")
        if send and not print_only:
            with one_pusher():
                doc = read_state()  # re-read under the lock: a racing pass may have claimed
                send, why = phone_decide(state, doc)
                if send:
                    write_state(claim(doc, state))
            if send:
                push(state)
        if once:
            return 0
        if not state.get("instance_alive"):
            if print_only:
                print("the NAS session is gone — notifier exiting")
            return 0
        time.sleep(NAS_POLL)


def main() -> int:
    argv = sys.argv[1:]
    if "--nas-watch" in argv:
        return nas_watch(argv)

    root = 0
    for arg in argv:
        if arg.isdigit():
            root = int(arg)
    print_only = "--print" in argv
    tty = "/dev/tty"
    if "--tty" in argv:
        tty = argv[argv.index("--tty") + 1]

    state = snapshot(root)
    if state is None:
        if print_only:
            print("no state: fbtodo could not answer for this shell")
        return 0

    doc = read_state()
    ring, why = decide(state, doc)
    send, why_push = phone_decide(state, doc)
    if print_only:
        print(f"{'RING' if ring else 'silent'}: {why}"
              f"  [session {state.get('session')} list {state.get('list_id')}]")
        print(f"{'PUSH' if send else 'no push'}: {why_push}")
        return 0

    if ring or send:
        # Serialize decide+claim, the way the NAS path below does. The timer asks every
        # 5s, and one shell can have MORE THAN ONE timer on it: the wrapper kills its
        # timer when `command freebuff` returns, but an interrupted launch never gets
        # there, and the timer's own exit condition is the launching shell — which is
        # still alive. Two timers, one finished list, no lock: both ask the store, both
        # see the list unclaimed, both send. Measured in the real log: two iMessage and
        # two ntfy in the SAME second for one finish ("freebuff done · myproj",
        # 2026-09-24 14:33:38). Holding the lock re-reads the claim the first pass wrote,
        # and the second pass goes quiet.
        with one_pusher():
            doc = read_state()  # re-read under the lock: a racing pass may have claimed
            ring, why = decide(state, doc)
            send, why_push = phone_decide(state, doc)
            if ring:
                remember(doc, "rung", state.get("list_id"))
                doc.update(rung_ms=int(time.time() * 1000), session=state.get("session"),
                           done=state.get("done"), total=state.get("total"))
            if send:
                claim(doc, state)
            if ring or send:
                write_state(doc)
    # Both actions land after the claim, and outside the lock: the claim is what keeps a
    # racing pass silent, and the chime is not worth holding a lock across.
    if ring:
        try:
            subprocess.run([BELL, tty, why], timeout=TIMEOUT, check=False)
        except (OSError, subprocess.SubprocessError):
            pass
    if send:
        push(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
