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
you are not at the Mac.

The push is remembered against the list, in a map keyed by its fingerprint. That was a
single slot before, which silently assumed ONE session per Mac: two sessions overwrote each
other's claim, so on every poll each decided the other had never pushed — the phone got the
same "done" every ~15s until the thread was muted. The fingerprint includes the session, so
two instances cannot collide in the map.

usage: todo-bell.py <shell-pid> [--print] [--tty PATH]
       todo-bell.py --state -|PATH [--print] [--push-only] [--note PATH]
                                                the state the CALLER has in hand, rather
                                                than one resolved from a shell pid
       --print  decide and report, play/send nothing (also says WHY it stayed silent)
       --push-only
                the phone alone, never the chime: for an asker that is not the session's
                own timer (a pane, which reports a finish it watched).
       --note PATH
                keep what was decided at PATH (one line per ask, the newest kept) and say
                the same lines on stdout — the file is for the operator afterwards, the
                stdout for the asker that wants to log it too. Never written without this
                flag: the session timer asks every 5s and would fill it.

Environment: FREEBUFF_TODO_BELL_STATE overrides the one-ring-per-list record (tests);
FREEBUFF_BELL=off mutes the chime (bell.sh); FREEBUFF_PHONE=off mutes the push
(phone.sh); FREEBUFF_PHONE_SH points at a different sender (tests);
FREEBUFF_PHONE_TEXT=agent puts the agent's own prose in the PHONE body (the channel
gets it by default); FREEBUFF_DISCORD_SUMMARY=off takes it out of the CHANNEL's.
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
# The CHANNEL gets the agent's own account of the turn by default — the `Goal:` heading, one
# line about the outcome and the count — and this is the switch that takes it away
# (`FREEBUFF_DISCORD_SUMMARY=off`). It is not the phone's rule: a channel is read deliberately,
# by a person or by the agent living in it, and "the list ended" is the half of the news that
# never needed saying — while the phone is a nudge where a model's sentence must be asked for.
# With the switch off the channel falls back to the phone's body.
DISCORD_TEXT = (os.environ.get("FREEBUFF_DISCORD_SUMMARY") or "").strip().lower() not in (
    "off", "0", "no", "false", "disabled",
)
PHONE = os.environ.get("FREEBUFF_PHONE_SH") or os.path.join(HERE, "phone.sh")
TIMEOUT = 6.0


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default




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


def snapshot(root: int) -> dict | None:
    """The list for the session that belongs to this shell."""
    binary = fbtodo_binary()
    if not binary:
        return None
    if root:
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


def unlisted_files(state: dict) -> str:
    """`3 files` — the count behind the boundary guard, for a reason line."""
    n = len((state.get("turn") or {}).get("files") or [])
    return f"{n} file{'' if n == 1 else 's'}"


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
    # The boundary guard (fbtodo's `files_unlisted`): the turn ended having edited files
    # without publishing a newer list, so the list does not account for the work and this
    # is not a finish to announce.
    if state.get("files_unlisted"):
        return False, f"all {total} done, but {unlisted_files(state)} changed after the list"
    list_id = state.get("list_id")
    if not list_id:
        return False, "list has no identity to ring once for"
    if list_id in claims(doc, "rung"):
        return False, f"already rang for this list ({done}/{total})"
    return True, f"all {total} todos done and the turn ended"


def phone_decide(state: dict, doc: dict) -> tuple[bool, str]:
    """(push?, why) — the same question as the bell's, and the same answer.

    Every todo done AND the turn ended. The push is remembered against the LIST: one per
    list, the bell's own rule. It used to also require the store's mtime to have moved,
    which on a live session rewrote the transcript every turn and pushed the SAME finished
    list every couple of minutes — the kind of repeat that gets a topic muted, or a thread
    collapsed and never looked at. A new turn writes a new list, and that pushes.
    """
    total = int(state.get("total") or 0)
    done = int(state.get("done") or 0)
    if not total:
        return False, "no todo list yet"
    if done < total:
        return False, f"still working ({done}/{total} done)"
    list_id = state.get("list_id")
    if not list_id:
        return False, "list has no identity to push once for"
    # The turn's own boundary, in the source's own words. A CLI journal writes one
    # (`shouldEndTurn`, which `turn_ended` carries); the desktop store writes none — its
    # rows are committed when a turn CLOSES, so the transcript says nothing about the
    # boundary — but its `threads` row does say whether a turn is alive, and a thread with
    # no turn running and every step ticked has exactly the news a journal's boundary
    # gives. (A turn that died leaves that row idle too; the list would have to be complete
    # for this to announce anything, and a half-done list is the stall watch's question.)
    if state.get("backend") == "desktop":
        if state.get("turn_running"):
            return False, f"all {total} done, but the turn is still running"
    elif not state.get("turn_ended"):
        return False, f"all {total} done, but the agent has not finished its turn"
    if state.get("files_unlisted"):
        return False, f"all {total} done, but {unlisted_files(state)} changed after the list"
    if list_id in claims(doc, "pushed"):
        return False, f"already pushed for this list ({done}/{total})"
    return True, f"all {total} todos done and the turn ended"
def ended_at(state: dict) -> int:
    """When the run finished, in epoch ms — the store's own last write.

    Not the moment we noticed: that is the transcript's final append, so it is the agent
    stopping rather than the notifier waking up.
    """
    return int(
        state.get("store_mtime_ms")
        or state.get("source_updated_ms")
        or state.get("probed_ms")
        or 0
    )


def push_tail(state: dict) -> str:
    """`count · when` — the bookkeeping both bodies end with.

    The date and time are what place it (a push read hours later is otherwise impossible
    to date), and the count is the part that says it is over.
    """
    ended = ended_at(state)
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(ended / 1000.0)) if ended else ""
    count = f"{int(state.get('done') or 0)}/{int(state.get('total') or 0)} steps done"
    return " · ".join(part for part in (count, when) if part)


# How much of each labelled line the channel gets. The store caps the summary before it ever
# arrives (fbtodo's SUMMARY_MAX_CHARS, 200); this is the push's own ceiling, applied after the
# line has been flattened.
CHANNEL_LINE_MAX = 220


def one_line(text, limit: int = CHANNEL_LINE_MAX) -> str:
    """`text` as ONE line — "" for nothing to say.

    A model's answer is multi-line as often as not, and every line this script sends is a
    single field of a sentence-shaped body, so newlines and runs of spaces collapse to one
    space and an over-long line is cut with an ellipsis. Flattening is not cosmetic: a
    newline left in place turns one sentence into two, and the second reads as a second
    thought nobody wrote.
    """
    said = " ".join(str(text or "").split())
    if len(said) > limit:
        said = said[: limit - 1] + "…"
    return said


def spoken_summary(state: dict) -> str:
    """The agent's own account of what the turn did, as ONE line — "" when it said nothing.

    `state["summary"]` is fbtodo's first line of the agent's last answer (the journal's
    `fullResponse`, trimmed to SUMMARY_MAX_CHARS), so this is not a second extraction, just
    the flattening: every newline and run of spaces becomes one space, and the cap here is
    the push's rather than the store's. A store with no prose contributes no line at all.
    """
    return one_line(state.get("summary"))


def phone_message(state: dict) -> tuple[str, str]:
    """(title, body) — title says which run, body carries the big goal, the clock and
    the count.

    The goal is the part worth waking up for; the date and time are what place it (a
    push read hours later is otherwise impossible to date), and the count is the part
    that says it is over.
    """
    project = os.path.basename((state.get("cwd") or "").rstrip("/"))
    title = f"freebuff done · {project}" if project else "freebuff done"
    tail = push_tail(state)
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
    # store's, and a store with no prose simply
    # contributes no line.
    return title, "\n".join(part for part in (head, spoken_summary(state), tail) if part)


def discord_message(state: dict) -> str:
    """What the CHANNEL is told the turn did, or "" to let it post the phone's body.

    Three lines, and every one of them labelled, because this message is read out of
    context — the agent in the channel answers questions from what reached it, and the
    owner scrolls past it later — where a bare sentence above a count says nothing about
    which half is which:

        Goal: shrink the pane heading to one line
        Summary: the heading now fits and the two failing checks were fixed
        3/3 steps done · 2026-10-06 18:14

    The goal is the agent's own `Goal:` heading (`state["goal"]`) and nothing else: the
    session's opening request is NOT promoted into it, because a quote labelled as a goal
    is a claim the agent never made (the phone's agent-mode body makes the same
    distinction by leaving the fallback unlabelled). The summary is the same one line the
    phone can carry — the first line of the agent's last answer — and the tail is the
    count and clock that place the finish. Each is capped and flattened on its own so a
    long one cannot swallow the next.

    Nothing is invented: with neither a goal nor a summary this returns "", and the sink
    posts the phone's body — the message it posted before it had one of its own.
    """
    if not DISCORD_TEXT:
        return ""
    goal = one_line(state.get("goal"))
    said = spoken_summary(state)
    if not goal and not said:
        return ""
    lines = []
    if goal:
        lines.append(f"Goal: {goal}")
    if said:
        lines.append(f"Summary: {said}")
    lines.append(push_tail(state))
    return "\n".join(part for part in lines if part)


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
    # `--discord` alongside the phone push: a finished turn is one of the two events that
    # go to Discord as well (see `discord-send.sh`), so the activity is visible to a reader
    # there — the Hermes agent in the channel, or the owner's own phone via Discord. The
    # other bells ask for the sink by name and do not, which is what keeps the channel to
    # finishes and drops.
    argv = [PHONE, "--title", title, "--message", body, "--discord"]
    channel = discord_message(state)
    if channel:
        argv += ["--discord-message", channel]
    try:
        subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
    except OSError:
        return False
    return True


class one_pusher:
    """Serialize decide+claim, so two passes cannot both send for the same finish.

    More than one pass can be in flight (a stale lock, an upgrade race), and both would
    ask this script at their own phase. Each asks the store, decides, and only then
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


# How many decisions the record keeps. The asker that passes `--note` is the pane, and the
# pane asks once per finish it watched, so this is a ceiling nobody reaches by accident; it
# is here so that an asker which DOES ask on a clock cannot grow the file without end.
NOTE_MAX_LINES = 500


def note_line(action: str, state: dict, why: str) -> str:
    """One line of the record: when, what was decided, which list, and why.

    In plain words: the file an operator reads when the phone stayed quiet. `silent` and
    `no push` are the ones worth reading — they answer "the list finished and nothing
    arrived" — and a `push` line beside them is what shows the two are not the same event.
    """
    when = time.strftime("%Y-%m-%d %H:%M:%S")
    # No column padding: the action words differ in length, so aligning them costs the
    # separators their regularity — and a record is read by both a person and a grep
    # (`fbtodo status`) whose two-space fields are the only structure it has.
    return f"{when}  {action}  list {state.get('list_id') or '—'}  {why}"


def write_note(path: str, lines: list) -> None:
    """Append the decision to the record, keeping the newest `NOTE_MAX_LINES` lines.

    A rewrite rather than an append, because the file is BOUNDED: the newest lines are what
    an operator wants, and a file that can only grow is a file somebody has to clean by
    hand. Written to a sibling temp file and renamed over the target, so a reader never sees
    half a line.
    """
    if not path or not lines:
        return
    try:
        with open(path, encoding="utf-8") as handle:
            kept = handle.read().splitlines()
    except OSError:
        kept = []
    kept = (kept + list(lines))[-NOTE_MAX_LINES:]
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write("\n".join(kept) + "\n")
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def handed_state(argv: list) -> dict | None:
    """The list the CALLER already has (`--state -|PATH`), rather than one we resolve.

    In plain words: a pane is the process watching the list on screen, so it is the one that
    knows a turn ended *here and now* — and for the desktop app there is no shell pid to
    resolve at all (its turns are the app's own, and no `freebuff` process is behind them).
    Asking the caller to name a session would make the bell's answer a guess about a
    session nobody is looking at (measured 2026-10-05: a pane on a desktop thread while the
    ask resolved a finished CLI chat in the shell's cwd — `quiet` for a list that had
    already been pushed, and silence for every finish after it). So the asker may hand the
    state over instead: the same JSON `fbtodo json` prints, from stdin (`-`) or a file.
    """
    if "--state" not in argv:
        return None
    at = argv.index("--state") + 1
    path = argv[at] if len(argv) > at else "-"
    try:
        text = sys.stdin.read() if path == "-" else open(path).read()
    except OSError:
        return None
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


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
def main() -> int:
    argv = sys.argv[1:]
    root = 0
    for arg in argv:
        if arg.isdigit():
            root = int(arg)
    print_only = "--print" in argv
    # The phone alone, never the chime: an asker that is not the session's own timer
    # reports what IT saw (a pane, on the list it is drawing), and the chime belongs to the
    # session's timer — two ringers for one finish is a Mac that rings twice.
    push_only = "--push-only" in argv
    # Where the ASKER wants the decision kept, if it asked for that. Nothing is written
    # without the flag: the session timer asks every 5s, and a record of that would be a
    # record of the clock rather than of the finishes.
    note_path = argv[argv.index("--note") + 1] if "--note" in argv else ""
    tty = "/dev/tty"
    if "--tty" in argv:
        tty = argv[argv.index("--tty") + 1]

    state = snapshot(root)
    if "--state" in argv:
        state = handed_state(argv)
    if state is None:
        if print_only:
            print("no state: nothing usable was handed over" if "--state" in argv
                  else "no state: fbtodo could not answer for this shell")
        return 0

    doc = read_state()
    ring, why = decide(state, doc)
    if push_only:
        ring, why = False, "the phone alone (--push-only)"
    send, why_push = phone_decide(state, doc)
    if print_only:
        print(f"{'RING' if ring else 'silent'}: {why}"
              f"  [session {state.get('session')} list {state.get('list_id')}]")
        print(f"{'PUSH' if send else 'no push'}: {why_push}")
        return 0

    if ring or send:
        # Serialize decide+claim. The timer asks every 5s, and one shell can have
        # MORE THAN ONE timer on it: the wrapper kills its
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
            if push_only:
                ring, why = False, "the phone alone (--push-only)"
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
    # ...and the record of it. `--note PATH` is how an asker keeps the decision: the pane
    # passes it, so the file says why the phone did or did not ring for the finish it just
    # watched, and the same lines go to stdout because the asker that wants them kept is
    # also the one that logs them. `--print` writes nothing: report only means report only.
    if note_path:
        lines = []
        if not push_only:
            lines.append(note_line("ring" if ring else "silent", state, why))
        lines.append(note_line("push" if send else "no push", state, why_push))
        write_note(note_path, lines)
        for line in lines:
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
