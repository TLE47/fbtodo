#!/usr/bin/env python3
"""Ring when freebuff is WAITING ON AN ANSWER, not when a task finishes.

`todo-bell.py` answers "did the work end"; this answers "is it stuck on you". The two
are the same shape — one decision, one record, one sender — because they are asked on
the same clock by the same watcher.

The signal is the PANE, and that is a measurement, not a preference. The journal
records an `ask_user` call only when the iteration ENDS, which is after the answer
arrived: `log.jsonl` at 2026-09-22T19:05:40.504Z carries one record holding both the
tool call and its `toolResults.answers`, with `duration: 385767` — the 6m26s the
question sat unanswered. So during the wait the store says nothing at all, while the
screen says everything: the modal draws its own title, and that title is the test.

    ╭────────────── Some questions for you ──────────────╮
    │                                            Close ✕ │
    │ ▼ Put the four taskboard items back? I have their  │
    │   ○ Restore all four, marked done                  │
    │   ○ Custom                                         │
    ╰────────────────────────────────────────────────────╯

The pane is where every session on this Mac is visible at once: they are panes on the
same tmux server, so one scan covers all of them. (A freebuff started outside tmux has no
pane to read — nothing here can see it.)

Two things keep a painted string from becoming a notification: the header must sit on a
line that also carries the box's own `╭`, and the pane's foreground command must be one
a TUI runs under. A `grep "Some questions for you"` leaves the words on screen but no
box, and a `cat` of a captured frame leaves the box but runs under `cat`.

One push per question: the question and its options are hashed, so a modal that stays up
for twenty minutes — exactly the case that made this worth writing — is announced once,
while the next question is a different hash and announces itself. The last 50 are kept,
so the same question asked again much later can still ring.

usage: ask-bell.py [--print] [--quiet] [--pane %id]... [--tmux PATH]
       ask-bell.py --print   decide and report, send nothing (also says WHY it was
                             silent, which is the only way to read a quiet pass)

Environment: FREEBUFF_ASK_BELL_STATE overrides the one-push-per-question record (tests);
FREEBUFF_TMUX (or FBTODO_TMUX) points at another tmux; FREEBUFF_ASK_CMDS overrides the
command allowlist; FREEBUFF_ASK_MAX caps the questions remembered; FREEBUFF_ASK_PHONE_MAX
is the body's character budget; FREEBUFF_PHONE=off mutes the push (phone.sh) and
FREEBUFF_PHONE_SH points at a different sender (tests).

Exit: 0 decided · 78 the question is up and there is nothing configured to send it to.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import time

try:
    import fcntl
except ImportError:  # pragma: no cover - not a POSIX host
    fcntl = None

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.environ.get("FREEBUFF_ASK_BELL_STATE") or os.path.join(HERE, "ask-bell.state")
# Agent prose is opt-in — see todo-bell.py's `TEXT_AGENT`. The question and its options are
# written by the model, so off by default the push says only that a session is stopped and
# waiting for an answer, and how many options are on screen; `FREEBUFF_PHONE_TEXT=agent`
# sends the question itself.
TEXT_AGENT = (os.environ.get("FREEBUFF_PHONE_TEXT") or "").strip().lower() in (
    "agent", "on", "1", "all", "full",
)
PHONE = os.environ.get("FREEBUFF_PHONE_SH") or os.path.join(HERE, "phone.sh")
TMUX = os.environ.get("FREEBUFF_TMUX") or os.environ.get("FBTODO_TMUX") or "tmux"
TIMEOUT = 8.0

# The modal's own title. Anything else on screen that happens to say "question" is not
# this: the CLI draws exactly one thing for `ask_user`, and this is its word for it.
HEADER = "Some questions for you"
# The box's corner, which the header's own line carries. Both must be on the SAME line,
# which is what a captured frame has and a command line mentioning the phrase does not.
HEADER_BOX = "╭"
# The drawer's frame. A modal is only a modal if it is drawn.
BOX = "│"
# Bullets and the marker above the question: the modal's own shapes, so an option label
# is read from the drawing rather than guessed from the text.
BULLETS = ("○", "●")
MARKER = "▼"
# Foreground commands a TUI runs under. `ssh` is here because a remote shell opened in
# tmux is a pane this scan must skip rather than mistake for a session's window.
PANE_CMDS = {
    c.strip()
    for c in os.environ.get("FREEBUFF_ASK_CMDS", "node,freebuff,ssh,docker,deno,bun").split(",")
    if c.strip()
}
CLAIM_MAX = int(os.environ.get("FREEBUFF_ASK_MAX") or 50)
BODY_MAX = int(os.environ.get("FREEBUFF_ASK_PHONE_MAX") or 320)


def _run(argv: list[str]) -> str:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout if proc.returncode == 0 else ""


def tmux(*args: str) -> str:
    return _run([*shlex.split(TMUX), *args])


def panes(wanted: list[str]) -> list[dict]:
    """Every pane, or just the named ones — with the three fields the gate needs."""
    # `|`, not a tab: a tab in a tmux format is not portable (see panes.py's pane_rows).
    fmt = "#{pane_id}|#{pane_current_command}|#{pane_current_path}|#{window_name}"
    rows = []
    if wanted:
        for pane in wanted:
            out = tmux("display-message", "-p", "-t", pane, fmt)
            for line in (out or "").splitlines():
                rows.append(line)
    else:
        rows = (tmux("list-panes", "-a", "-F", fmt) or "").splitlines()
    found = []
    for line in rows:
        parts = line.split("|", 3)
        if len(parts) < 4 or not parts[0].startswith("%"):
            continue
        found.append(
            {"pane": parts[0], "cmd": parts[1], "path": parts[2], "window": parts[3]}
        )
    return found


def unbox(line: str) -> str:
    """The text inside one row of the modal: between the first and last frame bar.

    A row whose right bar is off the edge still yields its text rather than nothing —
    a line that overran the pane must not take the question's first words with it.
    """
    first, last = line.find(BOX), line.rfind(BOX)
    if first < 0:
        return ""
    return (line[first + 1 :] if last == first else line[first + 1 : last]).rstrip()


def parse(text: str) -> dict | None:
    """(question, options) off a captured frame, or None when no modal is up.

    The drawer is read from the drawing: the question from the marker row down to the
    first option (an expanded option's description lines sit under its own label, so
    they are not the question), the options from the bulleted rows. Nothing here is
    inferred from wording, so a question in any language reads the same.
    """
    lines = text.splitlines()
    top = None
    for i, line in enumerate(lines):
        if HEADER in line and HEADER_BOX in line:
            top = i
            break
    if top is None:
        return None
    body: list[str] = []
    for line in lines[top + 1 :]:
        stripped = line.lstrip()
        if not stripped.startswith(BOX):
            break  # the modal's last drawn row; its bottom border is often off-pane
        content = unbox(line).strip()
        if content.startswith("╭") or content.startswith("╰"):
            break  # the Submit button's own little box: the modal's flow ends here
        if content:
            body.append(content)
    question: list[str] = []
    options: list[str] = []
    for content in body:
        if content.startswith("Close") or content.startswith("✕"):
            continue
        if content.startswith(BULLETS):
            label = content[1:].strip()
            if label and label not in options:
                options.append(label)
            continue
        if content.startswith(MARKER):
            question.append(content[1:].strip())
            continue
        if question and not options:
            # a wrapped question line
            question.append(content)
        elif not question and not options:
            question.append(content)  # a question the marker row did not start
    text_q = " ".join(" ".join(question).split())
    if not text_q:
        return None
    return {"question": text_q, "options": options[:6]}


def scan(wanted: list[str]) -> list[dict]:
    """Every freebuff pane that is asking right now."""
    found = []
    for pane in panes(wanted):
        if pane["cmd"] not in PANE_CMDS:
            continue
        text = tmux("capture-pane", "-p", "-t", pane["pane"])
        parsed = parse(text or "")
        if not parsed:
            continue
        fingerprint = hashlib.sha1(
            (
                pane["pane"]
                + "\n"
                + parsed["question"]
                + "\n"
                + "\n".join(parsed["options"])
            ).encode("utf-8", "replace")
        ).hexdigest()[:12]
        found.append({**pane, **parsed, "fingerprint": fingerprint})
    return found


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
    """Question fingerprints already pushed, oldest dropped past the cap."""
    got = doc.get("asked")
    if not isinstance(got, dict):
        got = {}
    if len(got) > CLAIM_MAX:
        newest = sorted(got.items(), key=lambda kv: kv[1], reverse=True)[:CLAIM_MAX]
        got = dict(newest)
    return got


def claim(doc: dict, finding: dict) -> dict:
    """Remember the push BEFORE sending: a second pass must not push twice."""
    got = claims(doc)
    got[finding["fingerprint"]] = int(time.time() * 1000)
    doc.update(schema=1, asked=got, pushed_ms=int(time.time() * 1000), question=finding["question"][:200])
    return doc


def decide(findings: list[dict], doc: dict) -> tuple[dict | None, str]:
    """(what to announce?, why not) — the reason is printed either way."""
    if not findings:
        return None, "no freebuff pane is asking"
    got = claims(doc)
    for finding in findings:
        if finding["fingerprint"] not in got:
            return finding, f"{finding['pane']} is asking"
    return None, f"already pushed for this question ({len(findings)} pane(s) asking)"


def where_of(finding: dict) -> str:
    """The project, as the pane's own working directory names it."""
    path = (finding.get("path") or "").rstrip("/")
    name = os.path.basename(path)
    return "" if not name or path == os.path.expanduser("~") else name


def phone_message(finding: dict) -> tuple[str, str]:
    """(title, body): the question you have to answer, then what you may answer."""
    project = where_of(finding)
    title = f"freebuff asks · {project}" if project else "freebuff asks"
    if not TEXT_AGENT:
        # metadata only: the model is stopped until a human answers, so the push has one
        # job — say so, and say where the screen is
        options = len(finding.get("options") or [])
        body = "\n".join([
            f"a question is on screen, waiting ({options} option"
            f"{'s' if options != 1 else ''})",
            "",
            f"at {finding['pane']} ({finding['window']}) on the Mac",
        ])
        return title, body
    bits = [finding["question"]]
    if finding["options"]:
        bits.append("")
        for i, option in enumerate(finding["options"], 1):
            bits.append(f"{i}) {option}")
    bits.append("")
    bits.append(f"at {finding['pane']} ({finding['window']}) on the Mac")
    body = "\n".join(bits)
    if len(body) > BODY_MAX:
        body = body[: BODY_MAX - 1].rstrip() + "…"
    return title, body


def push(finding: dict) -> bool:
    """Hand the notification to phone.sh, detached: a poll must not wait on a network.

    `high` because a waiting agent is a stopped session, not news, and the ntfy tag is
    the question mark the app shows for exactly this. phone.sh owns both transports
    (`both` sends down iMessage and ntfy), so this script never picks one.
    """
    if not os.path.exists(PHONE):
        return False
    title, body = phone_message(finding)
    try:
        subprocess.Popen(
            [PHONE, "--title", title, "--message", body, "--priority", "high", "--tags", "question"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
    except OSError:
        return False
    return True


def phone_ready() -> bool:
    """A sender with no topic has nothing to do, ever."""
    if not os.path.exists(PHONE):
        return False
    try:
        proc = subprocess.run([PHONE, "--print"], capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and "topic=set" in proc.stdout


class one_pusher:
    """Serialize decide+claim, so two watchers cannot both announce the same question.

    More than one watcher can be running (a stale lock, an upgrade race) and each asks
    this script on its own clock. Each pass scans,
    decides and only then records — two passes overlapping in those milliseconds would
    both send. Holding an exclusive lock across that window makes the second pass
    re-read the claim the first one just wrote, and stay silent. Best effort: no lock
    available is not a reason to lose the notification.
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


def main() -> int:
    argv = sys.argv[1:]
    print_only = "--print" in argv
    quiet = "--quiet" in argv
    wanted: list[str] = []
    rest = argv
    if "--pane" in rest:
        i = rest.index("--pane")
        wanted = [p for p in rest[i + 1 :] if p.startswith("%")]

    findings = scan(wanted)
    doc = read_state()
    finding, why = decide(findings, doc)
    if print_only:
        said = (
            f"ASK: {finding['pane']} {finding['fingerprint']} — {finding['question']}"
            if finding
            else f"silent: {why}"
        )
        print(said)
        return 0

    if finding is None:
        return 0
    if not phone_ready():
        if not quiet:
            print("ask-bell: nothing configured to send to (see phone.sh --print)", file=sys.stderr)
        return 78

    with one_pusher():
        doc = read_state()  # re-read under the lock: a racing pass may have claimed
        finding, why = decide(findings, doc)
        if finding is not None:
            write_state(claim(doc, finding))
    if finding is None:
        return 0
    if not push(finding):
        if not quiet:
            print("ask-bell: could not hand the push to phone.sh", file=sys.stderr)
        return 78
    return 0


if __name__ == "__main__":
    sys.exit(main())
