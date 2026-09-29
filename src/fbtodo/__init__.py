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

from .base import *  # noqa: F401,F403 — the package is one namespace



def _iso_ms(ts):
    if not ts:
        return None
    try:
        import datetime as dt

        return int(dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:
        return None


def _record_todos(rec: dict, thread: str):
    """The `write_todos` call in this journal record, or None.

    The match is STRUCTURAL: a line may mention write_todos in prose (reasoning about
    the tool, or a todo whose text names it), and only a real toolCall carries a list.
    """
    data = rec.get("data") or {}
    for call in data.get("toolCalls") or []:
        if call.get("toolName") != "write_todos":
            continue
        todos = (call.get("input") or {}).get("todos")
        if todos is None:
            continue
        return {
            "ts": _iso_ms(rec.get("timestamp")),
            "todos": todos,
            "title": "",
            "thread": thread,
            "iteration": data.get("iteration"),
        }
    return None


# The second basket, for the model that never calls `write_todos`: what the session has
# actually DONE. Only calls that change or investigate something are named — `read_files`
# is left out on purpose, because reading is not progress and a reading session is exactly
# the one whose pane would fill with noise. The verb is the pane's word for the tool, so
# the feed reads as English rather than as a tool catalogue.
ACTION_TOOLS = {
    "str_replace": "edited",
    "write_file": "wrote",
    "apply_patch": "patched",
    "run_terminal_command": "ran",
    "code_search": "searched",
    "web_search": "searched the web",
    "read_url": "read",
    "skill": "loaded skill",
    "render_ui": "rendered a card",
    "ask_user": "asked you",
}
ACTION_KEEP = 12  # the newest calls carried in the state; the pane draws the first few
ACTION_ROWS = 3   # rows the framed pane gives the feed
ACTION_SCAN_KEEP = 240  # calls counted into the turn's tally; a runaway scan stops here
FILE_KEEP = 12    # distinct files named by a turn's tally
EDIT_VERBS = ("edited", "wrote", "patched")  # verbs that mean a file was touched
TURN_CHASE_CHUNKS = 6   # extra 1 MiB chunks read back while a turn's start is still unfound
QUIET_MS = 3 * 60_000   # no new transcript record for this long is worth naming, not judging


def _action_what(name: str, inp) -> str:
    """The most useful few words about one call: the file, the command, the query.

    One line, never a tree: a row in the pane is a glance, so the file's own name is what
    is wanted and the directories that lead to it are not.
    """
    if not isinstance(inp, dict):
        return ""
    paths = inp.get("paths")
    if isinstance(paths, list) and paths:
        return os.path.basename(str(paths[0]))
    for key in ("path", "file_path", "filePath", "target"):
        if isinstance(inp.get(key), str) and inp[key]:
            return os.path.basename(inp[key])
    if name == "run_terminal_command" and isinstance(inp.get("command"), str):
        return " ".join(inp["command"].split())[:80]
    for key in ("pattern", "query", "url", "name", "prompt", "text"):
        if isinstance(inp.get(key), str) and inp[key]:
            return " ".join(inp[key].split())[:60]
    return ""


def _record_actions(rec: dict, key) -> list:
    """(key, ts_ms, verb, what) for the calls in this record worth naming, newest first.

    The transcript records every call the session made, whether or not it wrote a list, and
    a call that happened is a fact where a guessed plan could only ever be a guess. That is
    the point of this second source: the pane stops depending on one tool being used.
    """
    ts = _iso_ms(rec.get("timestamp"))
    out = []
    for call in (rec.get("data") or {}).get("toolCalls") or []:
        if not isinstance(call, dict):
            continue
        name = call.get("toolName")
        verb = ACTION_TOOLS.get(name)
        if not verb:
            continue
        inp = call.get("input") or call.get("args") or {}
        out.append((key, ts, verb, _action_what(name, inp)))
    # Reversed: one record can hold several calls, written in the order they were made, and
    # the newest of them is the last.
    return list(reversed(out))


def _record_goal(rec: dict):
    """The agent's own one-line objective, if this record states one.

    AGENTS.md asks every task to open with `Goal: …`, and the journal keeps the agent's
    prose in `fullResponse` — the only agent-authored text in the store. That makes it
    the one place a goal the *agent* chose can come from; the prompts next to it are the
    user's wording, typos and all.
    """
    data = rec.get("data")
    if not isinstance(data, dict):
        return None
    text = data.get("fullResponse")
    if not isinstance(text, str) or not text:
        return None
    for line in text.splitlines():
        match = GOAL_LINE_RE.match(line)
        if match:
            return " ".join(match.group(1).split()).strip(" *_`") or None
    return None


def prose_text(line: str) -> str:
    """One line of the agent's prose as the phone can show it: markdown out, wrapped in.

    Emphasis and code markers are dropped, but underscores are left alone — they are how
    this store spells its own tool names (`write_todos`), and eating them would turn a
    summary into nonsense.

    Not to be confused with `_plain`, which is the renderer's: that one takes the colour off a
    styled line and leaves the words exactly as they are. The two were once the same name, and
    the renderer's — defined later in the file — quietly won every call.
    """
    line = re.sub(r"`([^`]*)`", r"\1", line)
    line = re.sub(r"`", "", line)
    line = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
    line = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", line)
    line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
    line = re.sub(r"^\s*(?:[-*+>|]+|\#{1,6})\s*", "", line)
    return " ".join(line.split())


def _record_summary(rec: dict):
    """The agent's headline: the first usable line of its last answer.

    The notification wants a sentence about what happened and what changed, and the
    agent's own words are the only place that exists — `fullResponse` holds the prose of
    the iteration that produced an answer. The FIRST line is taken rather than the last:
    answers open with the outcome ("Confirmed — the pipe was never the problem"), while
    the tail is usually the detail nobody reads on a phone. A `Goal:` line is skipped
    because the heading above it already carries one.
    """
    data = rec.get("data")
    if not isinstance(data, dict):
        return None
    text = data.get("fullResponse")
    if not isinstance(text, str) or not text.strip():
        return None
    for line in text.splitlines():
        plain = prose_text(line)
        if not plain or GOAL_LINE_RE.match(line):
            continue
        return plain if len(plain) <= SUMMARY_MAX_CHARS else plain[: SUMMARY_MAX_CHARS - 1].rstrip() + "…"
    return None


def _record_prompt(rec: dict):
    """The turn's user request, if this record carries one.

    The journal logs the prompt on its own record as each turn starts, which makes it
    the only place in the store where what was *asked* survives — the todos say what
    the answer is, never what it is for.
    """
    data = rec.get("data")
    if not isinstance(data, dict):
        return None
    text = data.get("prompt")
    if not isinstance(text, str):
        return None
    return " ".join(text.split()) or None


def _record_turn(rec: dict):
    """Did the agent END ITS TURN on this record — and is it therefore waiting for you?

    The per-iteration debug record carries `shouldEndTurn`: False while the agent is still
    working (a tool call in flight keeps writing "False" records and then goes silent for
    as long as the command takes), True on the iteration that produced its final answer.

    This is why the bell does not use a quiet window: a five-minute `run_terminal_command`
    leaves the journal untouched for minutes while the agent is emphatically NOT done, and
    a silence heuristic would ring in the middle of it.
    """
    data = rec.get("data")
    if not isinstance(data, dict) or "shouldEndTurn" not in data:
        return None
    ended = data.get("shouldEndTurn")
    return ended if isinstance(ended, bool) else None


# In plain words: is this message just "keep going"? When every word of it is one of the
# handful of continuation words, the ask is not new work — it is a nudge to carry on, and
# the agent's own instructions say a nudge means re-publish the list first, so the pane says
# so until a newer list arrives. A message carrying any real content fails this test on
# purpose: better to call a nudge a request than to lose a request as a nudge.
def is_nudge(text: str) -> bool:
    """Is this request just "keep going"? Then it is a continuation, not a task.

    Word-based on purpose: a bare `continue` and `keep going with it` say the same thing
    to the agent — re-write the list, then carry on — and both must be recognised for the
    pane to be able to say so. A phrase that adds any real content (a file, a subject)
    fails the test and is treated as a new request, which is the safer direction: no
    heading is ever lost to the nudge slot.
    """
    text = (text or "").strip()
    if not text or len(text) > NUDGE_MAX_CHARS:
        return False
    words = re.findall(r"[a-z']+", text.lower())
    return bool(words) and all(w in NUDGE_WORDS for w in words)


def pick_prompt(entries) -> str | None:
    """The newest entry that reads like a goal, else simply the newest one."""
    if not entries:
        return None
    ranked = sorted(entries, key=lambda kv: kv[0], reverse=True)
    for _key, text in ranked:
        if len(text) >= PROMPT_MIN_CHARS and not is_nudge(text):
            return text
    return ranked[0][1]


def _newest(entries):
    """The newest (key, text) pair, or None. Takes any iterable, including a filter."""
    best = None
    for key, text in entries:
        if best is None or key > best[0]:
            best = (key, text)
    return best


# In plain words: one block of the agent's transcript, read newest line first. Most lines
# are brushed off by a plain text search — "this line cannot contain a to-do list" — and
# only the few that might are decoded as data. That is the difference between a pane you
# can leave open all day and one that heats the laptop.
def _turn_extend(newer: dict, older: dict) -> dict:
    """Prepend an older chunk's turn onto the newer one's, because both walk newest-first.

    A turn is often longer than the 1 MiB window the scan reads at a time, and a chunk
    boundary is not a turn boundary: while the request that opened the turn has not been
    seen, every older chunk is still part of it.
    """
    counts = dict(newer.get("verbs") or {})
    for verb, n in (older.get("verbs") or {}).items():
        counts[verb] = counts.get(verb, 0) + n
    files = list(newer.get("files") or [])
    for name in older.get("files") or []:
        if name not in files:
            files.append(name)
    return {
        "start_ms": older.get("start_ms") or newer.get("start_ms"),
        "iterations": int(newer.get("iterations") or 0) + int(older.get("iterations") or 0),
        "verbs": counts,
        "files": files[:FILE_KEEP],
        "open": bool(older.get("open")),
    }


def journal_scan(blob: bytes, thread: str, chunk: int = 0):
    """(hit, hit_key, prompts, goals, turns, summaries, models, actions, turn), newest first.

    Keys are (chunk, -line index) — chunks are numbered from the start of the file and read
    backwards from its end, so both terms grow towards the newest line and a plain tuple
    comparison is a time comparison. Numbering them from the start (rather than by counting
    back from an end that moves with every append) is what lets a walk keep the chunks it
    has parsed — see `scan_live_log`.
    Cheap by construction: the substring test runs on every line, but json parses only
    the rare line mentioning write_todos with a payload, or carrying a prompt.
    """
    hit = None
    hit_key = None
    prompts: list = []
    goals: list = []
    turns: list = []
    summaries: list = []
    models: list = []
    actions: list = []
    # This TURN, bounded by the request that started it. A plan would supply a denominator;
    # nothing in these stores does, so nothing here counts progress. What a turn can supply
    # honestly is a boundary and a numerator: how long this work has been going, how many
    # model iterations it has taken, and what it has done so far. `open` means the walk has
    # not reached the request yet — the numbers are then lower bounds and are labelled so.
    turn = {"start_ms": None, "iterations": 0, "verbs": {}, "files": [], "open": True}
    for order, raw in enumerate(reversed(blob.split(b"\n"))):
        want_call = hit is None and b"write_todos" in raw and b'"todos"' in raw
        want_prompt = b'"prompt"' in raw
        # Parsing every prose record would cost real time on each poll (the agent writes
        # a lot of prose); the capital-G needle is what AGENTS.md asks for, so only the
        # few records that could hold a goal line get json.
        want_goal = b"fullResponse" in raw and (b"Goal" in raw or b"goal:" in raw)
        want_turn = b"shouldEndTurn" in raw
        # A record with prose in it, found without parsing (see PROSE_NEEDLE_RE).
        want_summary = PROSE_NEEDLE_RE.search(raw) is not None
        # The model, likewise found by regex and not by json: it rides on records that are
        # none of the above, so the early `continue` below would otherwise skip it.
        model = MODEL_RE.search(raw)
        if model:
            models.append(((chunk, -order), model.group(1).decode("utf-8", "replace")))
        # The second basket (see `_record_actions`): what this record called. Every call
        # record also carries `shouldEndTurn` (measured: 536 of 536), so these are records
        # the turn counter below already parses — tallying them costs no extra json work.
        # The cap is the tally's, and the pane's 12-row feed is a slice of it.
        want_action = (
            len(actions) < ACTION_SCAN_KEEP and b'"toolCalls"' in raw and b'"toolName"' in raw
        )
        if not (want_call or want_prompt or want_goal or want_turn or want_summary or want_action):
            continue
        try:
            rec = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            continue
        key = (chunk, -order)
        if want_call:
            found = _record_todos(rec, thread)
            if found:
                hit, hit_key = found, key
        if want_prompt:
            text = _record_prompt(rec)
            if text:
                prompts.append((key, text))
                if turn["open"]:
                    # The request that opened this turn — the journal logs it on its own
                    # record as the turn starts, which is the one exact boundary the store
                    # has. Everything newer is this turn's work; everything older is not, and
                    # closing here is what stops an earlier turn's calls being counted into
                    # this one's tally.
                    asked_ms = _iso_ms(rec.get("timestamp"))
                    if asked_ms:
                        turn["start_ms"] = asked_ms
                    turn["open"] = False
        if want_goal:
            goal_text = _record_goal(rec)
            if goal_text:
                goals.append((key, goal_text))
        if want_turn:
            ended = _record_turn(rec)
            if ended is not None:
                turns.append((key, ended))
                if turn["open"]:
                    # One model iteration, inside this turn. Walking backwards, the last one
                    # written is the oldest, so the start keeps being overwritten and ends up
                    # as the first iteration of the turn — and the request that opened it
                    # moves it back once more when this walk reaches that record.
                    iteration_ms = _iso_ms(rec.get("timestamp"))
                    if iteration_ms:
                        turn["start_ms"] = iteration_ms
                    turn["iterations"] += 1
        if want_summary:
            said = _record_summary(rec)
            if said:
                summaries.append((key, said))
        if want_action:
            acts = _record_actions(rec, key)
            actions.extend(acts)
            if turn["open"]:
                for _k, _ts, verb, what in acts:
                    turn["verbs"][verb] = turn["verbs"].get(verb, 0) + 1
                    if what and verb in EDIT_VERBS and what not in turn["files"]:
                        if len(turn["files"]) < FILE_KEEP:
                            turn["files"].append(what)
    return hit, hit_key, prompts, goals, turns, summaries, models, actions, turn


# In plain words: the transcript behind the pane. It only ever grows, one line per event,
# and what matters is at the end, so this reads it backwards in blocks and stops as soon as
# it has both things the pane needs: the most recent to-do list the agent wrote, and the
# most recent thing you asked for.
# The last scan of a journal, keyed by the file's identity, size and mtime.
#
# A scan is a pure function of the journal's bytes, and the watcher asks for the same answer
# once a second while the agent iterates once every few seconds — measured on a live 20 MB
# journal, re-walking it costs 165-355 ms per poll, and ~9 polls in 10 find it byte-identical
# to the one before. The key is the whole of what the answer depends on, so a rewritten or
# rotated log is a MISS rather than a stale answer; the value is a copy on the way out,
# because the caller decorates what it gets back.
#
# A journal that GREW is the third case, and the one a working session produces poll after
# poll: the same file with a few more lines on the end. The chunks of an append-only file
# never change once they are full, so each is remembered against the bytes it was (checked,
# not trusted), and a walk that reaches back into them re-parses only the chunk at the end
# that moved. Remembering them by absolute index from the START of the file — rather than by
# counting back from its end, which shifts with every append — is what lets them survive the
# next line, and why the walk's window no longer decides its price.
_SCAN_CACHE: dict = {}
_SCAN_HITS = [0]
_SCAN_PARSED = [0]  # bytes handed to journal_scan: the walk's price, read by the self-check
_SCAN_REUSED = [0]  # chunks answered from that memory rather than parsed a second time
CACHED_CHUNKS = 48  # per journal: deeper than any chase, bounded for a long-lived watcher


def scan_live_log(chat_dir: str) -> dict:
    """The last `write_todos` call and the prompts that give it meaning.

    Two different questions, answered in one backward pass: what this list is *for* (the
    agent's own `Goal:` line at or before the call, else the request it was written for),
    and the newest request in the session, which may be much newer — a list can sit
    unchanged for hours while the agent works on. The pane shows the first as the big goal
    and the second only when they differ, which is exactly when the list looks frozen but
    is not stale.
    """
    path = os.path.join(chat_dir, "log.jsonl")
    thread = os.path.basename(chat_dir.rstrip("/"))
    scan = {"hit": None, "goal": None, "goal_source": None, "now": None,
            "turn_ended": False, "summary": None, "observed": [], "turn": {}}
    try:
        st = os.stat(path)
        key = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
        cached = _SCAN_CACHE.get(path)
        if cached is not None and cached["key"] == key:
            _SCAN_HITS[0] += 1
            return copy.deepcopy(cached["scan"])
        size = st.st_size
        fh = open(path, "rb")
    except OSError:
        return scan
    # What the cursor remembered, and only when it applies: the same file (same device and
    # inode) holding the same session, and LONGER than the walk that remembered it. A
    # rewrite or a rotation is a different file's worth of chunks, and a shorter one is not
    # a prefix of what was read.
    chunks: dict = {}
    if (cached is not None and cached["id"] == (st.st_dev, st.st_ino)
            and cached["thread"] == thread and 0 < cached["size"] < size):
        chunks = cached["chunks"]
    try:
        hit = hit_key = hit_step = None
        prompts: list = []
        goals: list = []
        turns: list = []
        summaries: list = []
        models: list = []
        actions: list = []
        turn = None
        tail = b""
        covered = 0
        step_no = 0
        for chunk in range((size - 1) // CHUNK, -1, -1):
            start = chunk * CHUNK
            step = min(CHUNK, size - start)
            covered += step
            if covered > MAX_SCAN:
                break
            fh.seek(start)
            raw = fh.read(step)
            crc = zlib.crc32(raw)
            remembered = chunks.get(chunk)
            if remembered is not None and remembered[0] == thread and remembered[1] == crc:
                _SCAN_REUSED[0] += 1
                (found, found_key, found_prompts, found_goals, found_turns, found_said,
                 found_models, found_actions, found_turn) = remembered[2]
                leading = remembered[3]
            else:
                lines = (raw + tail).split(b"\n")
                if start > 0:
                    leading, lines = lines[0], lines[1:]
                else:
                    leading = b""
                _SCAN_PARSED[0] += step
                result = journal_scan(b"\n".join(lines), thread, chunk)
                (found, found_key, found_prompts, found_goals, found_turns, found_said,
                 found_models, found_actions, found_turn) = result
                # Only a FULL chunk is remembered, and only when it holds a newline of its
                # own: an append cannot reach a full chunk's bytes, and the partial line at
                # its start (the tail end of a line that began in the chunk below it) is the
                # bytes before that first newline — final, and the same value the next walk
                # computes when it reaches this chunk from the other side.
                if step == CHUNK and b"\n" in raw:
                    chunks[chunk] = (thread, crc, result, leading)
            tail = leading
            if found and hit is None:
                hit, hit_key, hit_step = found, found_key, step_no
            prompts.extend(found_prompts)
            goals.extend(found_goals)
            turns.extend(found_turns)
            summaries.extend(found_said)
            models.extend(found_models)
            actions.extend(found_actions)
            turn = found_turn if turn is None else (
                _turn_extend(turn, found_turn) if turn.get("open") else turn
            )
            turn_found = turn is not None and not turn.get("open")
            if hit is not None:
                # Stop as soon as a usable goal prompt is in hand; a tail of nothing
                # but "continue" is worth a few more chunks, not an unbounded scan.
                if any(
                    k <= hit_key and len(t) >= PROMPT_MIN_CHARS and not is_nudge(t)
                    for k, t in prompts
                ):
                    break
                # The start of this turn is one more thing worth a bounded look back: while
                # it is unfound the budget is TURN_CHASE_CHUNKS, and past it the pane prints
                # minimums instead of reading on. Once found, this is the rule it always was.
                budget = GOAL_CHASE_CHUNKS if turn_found else TURN_CHASE_CHUNKS
                if step_no - hit_step >= budget:
                    break
            step_no += 1
        scan["hit"] = hit
        # The turn, with `open` resolved into `truncated`: left standing it means the walk
        # never reached the request that started this turn, so the numbers the pane prints
        # are lower bounds ("12+") rather than a measured span.
        final_turn = dict(turn or {})
        final_turn["truncated"] = bool(final_turn.pop("open", True))
        scan["turn"] = final_turn
        # The second basket: the newest calls, newest first, for the pane to draw when the
        # agent has written no list. Capped here rather than left whole, so the state file
        # stays small — a glance needs a handful of rows, not a history.
        scan["observed"] = [
            {"verb": verb, "what": what, "ts_ms": ts}
            for _key, ts, verb, what in actions[:ACTION_KEEP]
        ]
        # The newest model this session named. Scanned from the same backward pass, and
        # kept even when the list itself is old: it is what the ESTIMATES are about, and
        # an estimate borrowed from another model's pace is a number with no referent.
        scan["model"] = (_newest(models) or (None, None))[1]
        # What the agent last SAID, which is the only sentence available about the work
        # itself: the todos say what is left, the heading says what it is for.
        said = _newest(summaries)
        scan["summary"] = said[1] if said else None
        newer = [(k, t) for k, t in prompts if hit_key is None or k > hit_key]
        # A NUDGE (a request too short to be a goal: "continue", "go on", "yes") that came
        # after the list means "keep going from where you were" — and AGENTS.md asks the
        # agent to re-write the list before carrying on, so the pane says so until a newer
        # `write_todos` arrives. Self-clearing: no newer list, no line.
        scan["nudge"] = None
        newest_prompt = _newest(prompts)
        if (
            newest_prompt
            and is_nudge(newest_prompt[1])
            and (hit_key is None or newest_prompt[0] > hit_key)
        ):
            scan["nudge"] = newest_prompt[1]
        # The heading is the AGENT's line, never the request's wording: AGENTS.md asks the
        # agent for it, and a quote of the user reads as a goal while being only the
        # phrasing. No line means no goal, said out loud rather than papered over.
        #
        # Which line belongs to which list is a question about TURNS, and a turn is
        # bracketed by its prompt: prose and tool calls are separate iterations, so the
        # heading of a turn can be written after the list it belongs to. Hence the three
        # positions below — before the list, before the newest request (same turn as the
        # list), or nowhere yet.
        asked = _newest(prompts)
        if hit_key is None:
            # No list yet: the newest statement of what is going on is the heading.
            stated = _newest(goals)
            scan["goal"] = stated[1] if stated else None
        else:
            stated = (
                _newest((k, t) for k, t in goals if k <= hit_key)
                or _newest((k, t) for k, t in goals if asked and k < asked[0])
                or (_newest((k, t) for k, t in goals if k > hit_key) if not newer else None)
            )
            scan["goal"] = stated[1] if stated else None
        if scan["goal"]:
            scan["goal_source"] = "agent"
        # "Since" compares against the newest REQUEST: a heading written after it is the
        # agent's line for that request, which reads the same way the heading does and so
        # beats the raw prompt. A line written before it belongs to the previous turn.
        scan["now"] = None
        if hit_key is not None and newer:
            # `>=`: a turn's prompt and its heading can share one record (both are
            # written as the turn starts), and that heading is the one for THIS request.
            later = _newest((k, t) for k, t in goals if asked and k >= asked[0])
            if later:
                scan["now"] = later[1]
            else:
                pick = pick_prompt(newer)
                # Same words again is not drift — the list already answers it. (The goal
                # itself can no longer be the thing this compares against: the heading is
                # the agent's line, and it is often absent.)
                answered = pick_prompt([(k, t) for k, t in prompts if k <= hit_key])
                scan["now"] = pick if pick and pick != answered else None
        # "The agent has finished and is waiting for you": the newest end-of-turn record,
        # and it must come after the newest request — otherwise it is the previous turn's
        # ending, which says nothing about the request just made.
        newest_turn = _newest(turns)
        scan["turn_ended"] = bool(
            newest_turn and newest_turn[1] and (asked is None or newest_turn[0] >= asked[0])
        )
        if len(_SCAN_CACHE) > 8:  # a long-lived watcher that keeps switching sessions
            _SCAN_CACHE.clear()
        if len(chunks) > CACHED_CHUNKS:  # hygiene: the walk reaches the newest ones first
            for stale in sorted(chunks)[: len(chunks) - CACHED_CHUNKS]:
                chunks.pop(stale, None)
        _SCAN_CACHE[path] = {"key": key, "id": (st.st_dev, st.st_ino), "size": size,
                             "thread": thread, "chunks": chunks, "scan": scan}
        return scan
    finally:
        fh.close()


def read_cli(chat_dir: str) -> dict:
    scan = scan_live_log(chat_dir)
    state = scan.get("hit")
    meta = read_json(os.path.join(chat_dir, "chat-meta.json"), {}) or {}
    if state is None:
        state = {
            "ts": None,
            "todos": [],
            "title": "",
            "thread": os.path.basename(chat_dir.rstrip("/")),
            "iteration": None,
            "no_log": True,
        }
    state["chat_dir"] = chat_dir
    state["model"] = scan.get("model") or None
    # What the session has actually done — the pane's fallback when there is no list — and
    # the turn those calls belong to.
    state["observed"] = scan.get("observed") or []
    state["turn"] = scan.get("turn") or {}
    state["first_prompt"] = meta.get("firstPrompt")
    # The heading is only ever the agent's own line: `first_prompt` is kept for `status`
    # and `json`, never rendered as a goal (a quote is the phrasing, not the objective).
    state["goal"] = scan.get("goal")
    state["goal_source"] = scan.get("goal_source") if state["goal"] else None
    # Has the agent finished this turn (and is it therefore waiting for you)? Read from the
    # same backward pass; the bell rings on "all todos done AND turn ended", so a long
    # command that merely leaves the journal quiet never rings it.
    state["turn_ended"] = bool(scan.get("turn_ended"))
    now = scan.get("now")
    # A nudge says the same thing as `now` but names what to do about it, so it wins the
    # slot rather than both lines saying `continue` in different words.
    nudge = scan.get("nudge")
    state["nudge"] = nudge or None
    state["now"] = now if now and now != state["goal"] and not nudge else None
    # The agent's own last sentence — the phone message shows it under the heading, and
    # `status` prints it so a silent notification can be traced to a missing summary.
    state["summary"] = scan.get("summary")
    if state.get("ts") is None:
        state["ts"] = meta.get("messagesMtimeMs")
    return clean_observation(state)


# ==================================================================== NAS backend
def nas_ssh(script: str, host: str, timeout: float = NAS_TIMEOUT) -> bytes:
    """Run one script on the NAS, returning its stdout.

    FBTODO_NAS may hold a bare ssh target or a whole command (the self-check points
    it at a fake that speaks this same protocol), so it is split rather than assumed.
    """
    target = shlex.split(host)
    if target and (os.path.sep in target[0] or target[0] in ("sh", "bash", "zsh")):
        attempts = [target + [script]]
    else:
        base = [
            "ssh",
            *target,
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=6",
            "-o",
            "LogLevel=ERROR",
        ]
        attempts = [
            # One handshake, then reuse: a fresh handshake per poll costs about an
            # order of magnitude more than the read it carries.
            base
            + [
                "-o",
                "ControlMaster=auto",
                "-o",
                f"ControlPath={NAS_CONTROL}",
                "-o",
                "ControlPersist=60",
                script,
            ],
            # ...but a stale or unusable control socket must not cost us the poll.
            base + [script],
        ]
    failure: RuntimeError | None = None
    for argv in attempts:
        try:
            proc = subprocess.run(argv, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"ssh {host} timed out after {timeout:.0f}s")
        except OSError as exc:
            raise RuntimeError(f"cannot run ssh: {exc}")
        if proc.returncode == 0:
            return proc.stdout
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        failure = RuntimeError(f"ssh exited {proc.returncode}: {(detail[-1] if detail else '')[:140]}")
    raise failure or RuntimeError("ssh failed")


def nas_probe(
    host: str,
    root: str,
    project: str,
    chat: str | None = None,
    prev_mtime: int | None = None,
    marker: str = "$HOME/.fb-session",
    timeout: float = NAS_TIMEOUT,
) -> dict:
    """ONE ssh round trip: which chat, how big, is the session alive, and its last todo list.

    Five header lines — DIR, SIZE, MTIME, LIVE, FB — then the extractor's single line,
    which is either `NONE`, `UNCHANGED` (the conversation file has not moved since
    `prev_mtime` — the parse is skipped outright, so an idle poll costs a stat), `ERR …`,
    or one compact JSON object. Two more lines ride after it, `PATCH …` and `ALERT …`: the
    tails of the hook's patch log and its notifier's log, so the pane's patch row costs
    nothing extra over the ssh it is already making.

    `chat` pins one session (the name of a chat directory under …/chats) instead of the
    newest, which is also what lets the self-check point this at a fixture.

    LIVE is "a freebuff process exists somewhere on the NAS". FB is the sharper answer
    from the host's `fb` wrapper marker (`pid started dir`): 1 = that session is live,
    0 = the marker is stale, `-` = no marker, i.e. the hook is not installed. The marker
    also names the directory `fb` started in, which is the project whose store is then
    read — `fb` is run from all over the NAS, so guessing `--nas-project` would show
    another project's list.
    """
    q = shlex.quote
    marker_expr = q(marker) if marker != "$HOME/.fb-session" else '"$HOME/.fb-session"'
    if chat:
        name = os.path.basename(str(chat).rstrip("/"))
        head = (
            f"root={q(root)}; p={q(project)}; "
            f"d={q(root)}/{q(project)}/chats/{q(name)}/"
        )
    else:
        head = (
            f"root={q(root)}; p={q(project)}; "
            # The fb marker, if there is one: pid, start time, container cwd.
            f"fbpid=; fbt=\"-\"; fbd=\"\"; m={marker_expr}; "
            "if [ -s \"$m\" ]; then read -r fbpid fbt fbd < \"$m\" 2>/dev/null; "
            "if [ -n \"$fbpid\" ] && kill -0 \"$fbpid\" 2>/dev/null; then FB=1; "
            "else FB=0; fi; else FB='-'; fi; "
            # A marker that names a project with a store says where the session STARTED,
            # which is not always where its store lands: `fb` maps a cwd the container
            # cannot see, and a container cwd of `/` makes the project nameless — that
            # session writes `projects/chats/<thread>` while the marker names `host`, so
            # trusting the marker read a two-day-old session and could never show the live
            # one. A live session settles it: follow the store that was written last,
            # wherever it is (the marker is what is used when nothing is live).
            "mp=\"\"; [ -n \"$fbd\" ] && { case \"$fbd\" in */|*//) fbd=${fbd%/};; esac; "
            "[ -d \"$root/${fbd##*/}\" ] && mp=${fbd##*/}; }; "
            "d=\"\"; if [ \"$FB\" = 1 ]; then "
            "d=$(ls -1dt $root/*/chats/*/ $root/chats/*/ 2>/dev/null | head -1); "
            "case \"$d\" in $root/chats/*) p=\"\";; $root/*) rest=${d#$root/}; p=${rest%%/*};; esac; "
            "fi; "
            "if [ -z \"$d\" ]; then [ -n \"$mp\" ] && p=$mp; "
            "cd $root/$p/chats 2>/dev/null || { echo NODIR; exit 0; }; "
            "d=$(ls -1dt */ 2>/dev/null | head -1); fi; "
            "[ -n \"$d\" ] || { echo NODIR; exit 0; }"
        )
    msgs = '"${d}chat-messages.json"'
    # Pipe-separated, because an empty field would otherwise collapse and shift the
    # project into the directory slot (a marker-less probe has an empty directory).
    fb_line = (
        f"printf 'FB %s|%s|%s\\n' \"${{FB:--}}\" \"${{fbd:-}}\" \"${{p:-}}\"; "
        if not chat
        else "printf 'FB -||\\n'; "
    )
    script = (
        f"{head}; "
        f'[ -n "${{d}}" ] || {{ echo NODIR; exit 0; }}; '
        "printf 'DIR %s\\n' \"$d\"; "
        # A session is reportable the moment its directory exists, transcript or not:
        # the transcript only appears once a turn has completed, and calling a live
        # session "no session" during its first turn would be a lie.
        f"if [ -f {msgs} ]; then "
        f"printf 'SIZE %s\\n' \"$(wc -c < {msgs} 2>/dev/null || echo 0)\"; "
        # -c is the Linux/BusyBox spelling; -f is BSD. The NAS needs the first, the
        # self-check's local stand-in needs the second.
        f"printf 'MTIME %s\\n' \"$(stat -c %Y {msgs} 2>/dev/null || stat -f %m {msgs} 2>/dev/null || echo 0)\"; "
        "else printf 'SIZE 0\\nMTIME 0\\n'; fi; "
        f"printf 'LIVE %s\\n' \"$(pgrep -f {q(nas_pgrep(NAS_PROC))} >/dev/null 2>&1 && echo 1 || echo 0)\"; "
        f"{fb_line}"
        f"if [ -f {msgs} ]; then "
        # PATH is spelled out because a non-login ssh shell has no login PATH, and the
        # extractor is python (the NAS ships python3; the freebuff image does not matter).
        "export PATH=/usr/local/bin:/usr/bin:/bin:$PATH; "
        f"command -v python3 >/dev/null 2>&1 || {{ echo 'ERR no python3 on the NAS'; exit 0; }}; "
        # Whole seconds, matching `stat %Y` in the header and the extractor's own check:
        # comparing an ms value against a second-resolution mtime never matched, so the
        # skip never fired.
        f"python3 -c {q(NAS_EXTRACT)} {msgs} {q(str(int(prev_mtime // 1000) if prev_mtime else 0))}; "
        "else echo NONE; fi; "
        # ...and the two facts about the patches themselves, gathered in the same round
        # trip: the tails are a few hundred bytes each, and the far side does no
        # classifying — BusyBox sh is a poor place for that. They ride AFTER the state line
        # (the parser reads them back by prefix, so a reply without them still parses).
        f"PT=$(tail -n {PATCH_TAIL_LINES} {q(NAS_PATCH_LOG)} 2>/dev/null | tr -d '\\r'"
        f" | cut -c1-220 | tr '\\n' '\\034'); "
        f"AT=$(tail -n 2 {q(NAS_ALERT_LOG)} 2>/dev/null | tr -d '\\r'"
        f" | cut -c1-220 | tr '\\n' '\\034'); "
        "printf 'PATCH %s\\nALERT %s\\n' \"$PT\" \"$AT\"; exit 0"
    )
    try:
        out = nas_ssh(script, host, timeout)
    except RuntimeError as exc:
        return {"dir": None, "error": str(exc)}
    if not out.startswith(b"DIR "):
        return {"dir": None, "error": "no session on the NAS"}
    parts = out.split(b"\n", 6)
    if len(parts) < 6:
        return {"dir": None, "error": "malformed reply from the NAS"}

    def num(line: bytes, prefix: str) -> int:
        try:
            return int(line[len(prefix):].strip() or 0)
        except ValueError:
            return 0

    name = parts[0][4:].decode("utf-8", "replace").strip()
    fb_bits = parts[4][3:].decode("utf-8", "replace").strip().split("|")
    fb_flag = (fb_bits[0] if fb_bits else "-") or "-"
    fb_dir = fb_bits[1] if len(fb_bits) > 1 else ""
    fb_project = fb_bits[2] if len(fb_bits) > 2 else ""
    state_line = parts[5].strip().decode("utf-8", "replace")
    state = {"kind": "none"}
    if state_line.startswith("{"):
        try:
            state = {"kind": "list", **json.loads(state_line)}
        except Exception:
            state = {"kind": "error", "error": "unparsable reply from the NAS"}
    elif state_line == "NONE" or state_line.startswith("NONE "):
        # `NONE {json}` carries the tool names this session did call, which is the only
        # thing there is to say when it has written no list.
        state = {"kind": "none"}
        if len(state_line) > 5:
            try:
                state["tools"] = json.loads(state_line[5:]).get("tools") or {}
            except Exception:
                pass
    elif state_line == "UNCHANGED":
        state = {"kind": "unchanged"}
    elif state_line.startswith("ERR"):
        state = {"kind": "error", "error": state_line[4:].strip() or "the NAS could not read its store"}
    # The patch tail, read back by PREFIX rather than by position: a reply from a build
    # that does not send it (or one whose logs are both empty) parses exactly as it did.
    tail = parts[6].decode("utf-8", "replace") if len(parts) > 6 else ""
    patch_blob = alert_blob = ""
    # split on "\n" and NOT splitlines(): the far side joins its log entries with \x1c, and
    # `str.splitlines` treats that as a line boundary too — it cut every complaint off the
    # entry it belonged to, leaving a failure with no reason to show.
    for line in tail.split("\n"):
        if line.startswith("PATCH "):
            patch_blob = line[6:]
        elif line.startswith("ALERT "):
            alert_blob = line[6:]
    return {
        "dir": name,
        "thread": name.rstrip("/") or None,
        "size": num(parts[1], b"SIZE "),
        "mtime_ms": num(parts[2], b"MTIME ") * 1000 or None,
        "live": parts[3][5:].strip() == b"1",
        "fb": fb_flag,                # 1 live, 0 stale marker, - no marker at all
        "fb_dir": fb_dir,
        "fb_project": fb_project,
        "state": state,
        "patch": nas_patch_from_tail(patch_blob),
        "alert": alert_from_lines(alert_blob.split("\x1c"), NAS_ALERT_LOG, utc=True),
    }


def nas_liveness(args) -> dict:
    """The cheapest NAS question there is: is a freebuff SESSION running over there?

    The pane-open watcher asks this and nothing else, so the transcript is never touched —
    no parse, no 2.8 MB read, just the marker the host's `fb` keeps and one pgrep. `fb`
    runs inside the container and cannot reach this Mac's tmux, so the only way a pane can
    appear when a NAS session STARTS is for something local to watch for exactly this.
    """
    if not args.nas_host:
        return {
            "error": NAS_UNCONFIGURED, "alive": False, "fb": "-", "dir": "",
            "live": False, "started": "",
        }
    marker = getattr(args, "fb_marker", None) or "$HOME/.fb-session"
    expr = shlex.quote(marker) if marker != "$HOME/.fb-session" else '"$HOME/.fb-session"'
    script = (
        f"m={expr}; fbpid=; fbd=; "
        # A marker whose pid is gone was orphaned by a killed ssh: not a live session.
        "if [ -s \"$m\" ]; then read -r fbpid fbt fbd < \"$m\" 2>/dev/null; fi; "
        "FB='-'; if [ -n \"$fbpid\" ]; then "
        "if kill -0 \"$fbpid\" 2>/dev/null; then FB=1; else FB=0; fi; fi; "
        f"LIVE=$(pgrep -f {shlex.quote(nas_pgrep(NAS_PROC))} >/dev/null 2>&1 && echo 1 || echo 0); "
        # The marker's start time comes back too: it is how a pane is placed when several
        # ssh sessions to this NAS are open (see `nas_ssh_pane`).
        "printf 'FB %s|%s|%s\\n' \"$FB\" \"${fbd:-}\" \"${fbt:-}\"; printf 'LIVE %s\\n' \"$LIVE\""
    )
    try:
        out = nas_ssh(script, args.nas_host)
    except RuntimeError as exc:
        return {
            "error": str(exc), "alive": False, "fb": "-", "dir": "",
            "live": False, "started": "",
        }
    fb, directory, started, live = "-", "", "", False
    for line in out.decode("utf-8", "replace").splitlines():
        if line.startswith("FB "):
            bits = line[3:].split("|", 2)
            fb = (bits[0].strip() or "-") if bits else "-"
            directory = bits[1].strip() if len(bits) > 1 else ""
            started = bits[2].strip() if len(bits) > 2 else ""
        elif line.startswith("LIVE "):
            live = line[5:].strip() == "1"
    return {
        "fb": fb,
        "dir": directory,
        "started": started,          # the NAS's clock, ISO UTC; "" when there is no marker
        "live": live,
        "alive": fb == "1" or (fb == "-" and live),
        "error": None,
    }


def read_nas(args) -> dict:
    """The last todo list the NAS session wrote, from its conversation store.

    Turn-granular, and honest about it: the NAS build writes no tool inputs to its
    journal (see NAS_EXTRACT), so `chat-messages.json` — rewritten per completed turn —
    is the only place a list can be found. The same extractor that would run remotely
    here runs inside the self-check against a fixture, so both paths are the same code.
    """
    host, root, project = args.nas_host, args.nas_root, args.nas_project
    if not host or not root or not project:
        # No built-in target (see NAS_HOST): say which knob is missing rather than letting
        # ssh fail with "could not resolve hostname" three frames down.
        return clean_observation({
            "todos": [], "no_log": True, "title": "", "iteration": None, "ts": None,
            "goal": None, "now": None, "nudge": None, "tool_calls": {},
            "nas": {"dir": None, "error": NAS_UNCONFIGURED},
        })
    prev = read_json(os.path.join(SCRATCH, "fbtodo-nas.json"), {}) or {}
    prev_mtime = prev.get("mtime_ms") if prev.get("dir") else None
    marker = getattr(args, "fb_marker", None) or "$HOME/.fb-session"
    probe = nas_probe(host, root, project, args.chat or None, prev_mtime, marker)
    # What the remote session has called, as counted by the extractor over there.
    tools = (probe.get("state") or {}).get("tools") or {}
    # The `fb` marker is the sharper signal: it is written by the wrapper the operator
    # runs, so it says WHICH session is live and where it started. Without one (no wrapper
    # hook installed) the process probe still answers "is anything running".
    fb_flag = probe.get("fb") or "-"
    nas = {
        "dir": probe.get("dir"),
        "live": bool(probe.get("live")),
        "fb": fb_flag,
        "fb_live": fb_flag == "1",
        "fb_dir": probe.get("fb_dir") or "",
        "fb_project": probe.get("fb_project") or "",
        "alive": fb_flag == "1" or (fb_flag == "-" and bool(probe.get("live"))),
        "size": probe.get("size"),
        "mtime_ms": probe.get("mtime_ms"),
        # A session directory exists before its transcript does (the transcript is written
        # per completed turn), so "no transcript" and "no list" are different answers.
        "has_transcript": bool(probe.get("size")),
        "unchanged": False,
    }
    empty = {
        "todos": [], "no_log": True, "title": "", "iteration": None, "ts": None,
        "goal": None, "now": None, "nudge": None, "nas": nas, "tool_calls": tools,
        # The patch row's two facts come back on every probe, list or no list: a NAS pane
        # waiting for a session is exactly when "did the hook's patch step come out
        # clean?" is worth showing.
        "patch": probe.get("patch"), "alert": probe.get("alert"),
    }
    if not probe.get("dir"):
        nas["error"] = probe.get("error") or "no session on the NAS"
        return clean_observation(empty)
    state = probe.get("state") or {}
    # The far side skips its parse while the transcript has not moved, so an "unchanged"
    # answer carries no list: serve the one we already extracted, or the pane would blank
    # out every idle poll. A missing cache (wiped scratch dir) forces a real read.
    if state.get("kind") == "unchanged":
        if (prev.get("dir") or "") == nas["dir"] and prev.get("todos") is not None:
            nas["unchanged"] = True
            return {
                "todos": prev.get("todos") or [],
                "ts": prev.get("ts"),
                "calls": prev.get("calls"),
                "goal": prev.get("goal"),
                "now": prev.get("now"),
                "nudge": prev.get("nudge"),
                "title": "",
                "iteration": None,
                "thread": probe.get("thread"),
                "tool_calls": prev.get("tool_calls") or {},
                "nas": nas,
                "patch": probe.get("patch"), "alert": probe.get("alert"),
            }
        probe = nas_probe(host, root, project, args.chat or None, None, marker)
        state = probe.get("state") or {}
        tools = (state.get("tools") or {}) or tools
        nas["dir"] = probe.get("dir") or nas["dir"]
        nas["size"], nas["mtime_ms"] = probe.get("size"), probe.get("mtime_ms")
        nas["has_transcript"] = bool(probe.get("size"))
        nas["fb"] = probe.get("fb") or nas["fb"]
        nas["fb_live"] = nas["fb"] == "1"
        nas["alive"] = nas["fb_live"] or (nas["fb"] == "-" and bool(probe.get("live")))
    if state.get("kind") == "error":
        nas["error"] = state["error"]
        return empty
    if state.get("kind") == "list":
        ts = state.get("ts")
        ms = _iso_ms(ts) or (ts if isinstance(ts, int) else None)
        todos = state.get("todos") or []
        goal = state.get("goal") or None
        now = state.get("now") or None
        # The transcript's newest user message being a continuation is a nudge: the NAS
        # path has no prompt records, but it does have the requests themselves.
        nudge = state.get("nudge") or None
        atomic_write_json(
            os.path.join(SCRATCH, "fbtodo-nas.json"),
            {"dir": nas["dir"], "mtime_ms": nas["mtime_ms"], "todos": todos, "ts": ms,
             "calls": state.get("calls"), "goal": goal, "now": now, "nudge": nudge,
             "tool_calls": tools},
        )
        return clean_observation({
            "todos": todos,
            "ts": ms,
            "calls": state.get("calls"),
            "goal": goal,
            "now": None if nudge else (now if now and now != goal else None),
            "nudge": nudge,
            "title": "",
            "iteration": None,
            "thread": probe.get("thread"),
            "tool_calls": tools,
            "nas": nas,
            "patch": probe.get("patch"), "alert": probe.get("alert"),
        })
    # `NONE` means this session has written no list yet. That is not an error and not a
    # zero: the pane says "no write_todos call yet", rather than claiming an empty list.
    empty["thread"] = probe.get("thread")
    return clean_observation(empty)


# ==================================================== the NAS pane watcher
def tmux_run(*argv, timeout: float = 10.0):
    """Run tmux (overridable for tests: FBTODO_TMUX="tmux -L private").

    Returns stdout, or None when there is no usable server/client — every caller here is
    "do this if you can", so a failure must not be an exception.
    """
    argv = list(argv)
    if argv and argv[0] in TMUX_SUBCOMMANDS:
        argv = [*TMUX_BIN, *argv]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def tmux_split_target() -> str | None:
    """A window to split: the most recently active client's, else the busiest session's.

    A watcher is not attached to anything, so "your window" has to be inferred. Clients
    first (that is the one being looked at), then attached-or-not sessions, so a pane still
    lands somewhere sensible when only a detached server is left running.
    """
    out = tmux_run("list-clients", "-F", "#{client_activity} #{client_name}")
    best = None
    for line in (out or "").splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            best = max(best or (0, ""), (int(parts[0]), parts[1]))
    if best:
        win = tmux_run("display-message", "-c", best[1], "-p", "#{window_id}")
        if win and win.strip():
            return win.strip()
    out = tmux_run("list-sessions", "-F", "#{session_activity} #{session_name}")
    best = None
    for line in (out or "").splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            best = max(best or (0, ""), (int(parts[0]), parts[1]))
    if best:
        win = tmux_run("display-message", "-t", best[1], "-p", "#{window_id}")
        if win and win.strip():
            return win.strip()
    return None


# ------------------------------------------- the pane the NAS session runs inside
# `fb` is typed INSIDE an ssh over there, so the pane hosting the session is the pane
# whose descendant ssh process is that login — the same "by ancestry" rule the local
# instance's pane uses. A *window* is not good enough as a target: tmux answers one with
# its ACTIVE pane, which is whatever you happened to be looking at (measured — with a NAS
# pane and a session pane sharing a window, a fresh pane landed in the wrong column).
SSH_SESSION_CMDS = ("ssh", "ssh.exe")
# Flags that make an ssh something other than a login shell. A tunnel or a control master
# names the same host and sits in a pane too, and must not be taken for the session.
SSH_NON_SESSION_FLAGS = {"-N", "-M", "-L", "-R", "-D", "-W", "-O", "-S", "-f"}
# The marker's clock is the NAS's and the ssh's is this Mac's, so "before" allows a little.
NAS_CLOCK_SKEW_MS = 5000.0


def is_ssh_cmd(cmd: str) -> bool:
    """Does this command line start an ssh client — directly, or as a script's argv[1]?

    Only the first two tokens: a later argument that happens to be called `ssh` (a path
    handed to something) is not a client. The test stand-in is a shell script, so its
    command line reads `/bin/sh /path/to/ssh -t …`.
    """
    return any(os.path.basename(tok) in SSH_SESSION_CMDS for tok in cmd.split()[:2])


def nas_host_tokens(nas_host: str) -> set[str]:
    """What names this NAS on an ssh command line — empty when it cannot be derived.

    In real use `--nas-host` is `user@host`, and that string is what the ssh process
    shows. A stand-in (a path, a fixture's fake transport) names a *program* instead:
    then there is no host to match, and the caller accepts any session-like ssh rather
    than refusing to place the pane at all.
    """
    host = nas_host.strip()
    return {host, host.split("@")[-1]} if "@" in host else set()


def ssh_session_candidates(nas_host: str, rows=None, table=None) -> list[dict]:
    """Panes hosting an ssh that could be the NAS session: pane, window, ssh pid.

    A pane is listed once, by its shallowest such ssh. A `docker exec` over ssh is
    skipped: that is a command run on the remote host, not a login shell with a freebuff
    prompt in it.
    """
    rows = pane_rows() if rows is None else rows
    table = process_table() if table is None else table
    needles = nas_host_tokens(nas_host)
    found: dict[str, dict] = {}
    for row in rows:
        if not row["pid"] or row["pane"] in found:
            continue
        # The pane's own process counts: a pane can BE the ssh (a window opened as
        # `ssh host`), and then there is nothing below it to find.
        for pid, _depth in [(row["pid"], 0), *descendant_pids(table, row["pid"])]:
            cmd = table.get(pid, (0, ""))[1]
            if not is_ssh_cmd(cmd):
                continue
            tokens = cmd.split()
            if "docker" in cmd or any(tok in SSH_NON_SESSION_FLAGS for tok in tokens):
                continue
            if needles and not any(needle in tok for tok in tokens for needle in needles):
                continue
            found[row["pane"]] = {
                "pane": row["pane"], "window": row["window"], "ssh_pid": pid, "cmd": cmd,
            }
            break
    return list(found.values())


def pick_ssh_pane(began: dict[str, float], started_ms: float | None) -> str | None:
    """Which of several logins hosts the session, given when each of them began.

    An ssh cannot host a session that was already running when it logged in, so the newest
    login that began before the session did wins. With no marker timestamp at all (the
    session was found by `pgrep`, so there is no start time to compare) the newest login is
    the best answer; a session that began before every candidate takes the earliest of
    them. `NAS_CLOCK_SKEW_MS` covers the two clocks being different machines'.
    """
    if not began:
        return None
    if not started_ms:
        return max(began, key=began.get)
    before = [pane for pane, t in began.items() if t <= started_ms + NAS_CLOCK_SKEW_MS]
    return max(before, key=began.get) if before else min(began, key=began.get)


def nas_ssh_pane(
    nas_host: str, started_ms: float | None, rows=None, table=None
) -> str | None:
    """The pane the live NAS session is drawn in — its ssh — when it can be told apart.

    One candidate and there is nothing to decide. Several, and the session's own start
    time (the marker's) is what tells them apart — see `pick_ssh_pane`. None at all means
    the pane cannot be placed against anything, and the caller keeps its fallback.
    """
    cands = ssh_session_candidates(nas_host, rows, table)
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]["pane"]
    ages = ages_for([c["ssh_pid"] for c in cands])
    now_ms = time.time() * 1000
    began = {c["pane"]: now_ms - ages.get(c["ssh_pid"], 0) * 1000 for c in cands}
    return pick_ssh_pane(began, started_ms)


def nas_pane_ids() -> list[str]:
    """Panes already showing a NAS todo list — the watcher's own, or a hand-opened one.

    Matched on `pane_start_command`, because that survives the pane's own repaints and
    names the source: a LOCAL pane is `fbtodo --instance-of …` and must not be mistaken
    for one of these.
    """
    out = tmux_run("list-panes", "-a", "-F", "#{pane_id} #{pane_start_command}")
    found = []
    for line in (out or "").splitlines():
        parts = line.split(None, 1)
        start = parts[1] if len(parts) > 1 else ""
        if len(parts) == 2 and "fbtodo" in start and "-s nas" in start:
            found.append(parts[0])
    return found


def nas_place_panes(panes: list[str], anchor: str | None, quiet: bool = True) -> list[str]:
    """Put NAS list panes back beside the ssh their session runs inside, at the size it says.

    Only with an anchor: with no ssh identified there is nothing to place them against,
    and moving a pane somewhere unresearched is worse than leaving it where it is. The
    panes are moved, never re-opened — the running one keeps its scrollback and its clock.

    Side and size come from the same place a local pane's do — `pane_layout` for the role
    `nas`, in the window the ssh sits in — so a pin can move this pane's side or hold its
    size exactly as it does the local one's, and both are remembered for the next pane.
    """
    if not anchor:
        return []
    window = window_of(anchor)
    layout = pane_layout(window, "nas")
    rects = pane_rects()
    moved, sized = [], []
    for pane in panes:
        if place_pane_beside(pane, anchor, rects, layout["side"]):
            moved.append(pane)
            rects = pane_rects()  # the layout just moved under us
        if hold_pane_size(pane, layout["window"], "nas", rects, layout):
            sized.append((pane, layout["size_source"]))
            rects = pane_rects()
        remember_layout(window, "nas", pane, rects, layout["side"])
    forget_settled({row["pane"] for row in pane_rows()})
    for pane in moved:
        # Written down quiet or not, like the keeper's: the watcher is spawned `--quiet`
        # (its log IS its stdout), so a print alone would leave nothing to look at.
        append_log(NAS_LOG_PATH, f"moved {pane} beside the session's ssh pane")
    for pane, source in sized:
        append_log(
            NAS_LOG_PATH,
            f"resized {pane} to its {'pinned' if source.startswith('pin') else source} size",
        )
    if not quiet:
        if moved:
            print(
                f"put NAS todo pane(s) back beside the session's ssh: {', '.join(moved)}",
                file=sys.stderr,
            )
        if sized:
            print(
                "resized NAS todo pane(s): "
                + ", ".join(f"{pane} ({source})" for pane, source in sized),
                file=sys.stderr,
            )
    return moved


def nas_pane_open(args, anchor: str | None = None) -> str | None:
    """Split a pane running the NAS pane command, and return its pane id.

    `anchor` is the pane the session is drawn in — its ssh — when that could be worked
    out, so the list lands directly under it. The most recently active client's window is
    the fallback: a pane in the wrong place is still better than no list at all, and the
    watcher's placement pass moves it as soon as the ssh can be seen.

    Side and size come from `pane_layout` for the role `nas`, so a pin opens the pane where
    it says rather than only dragging it there on the next pass.
    """
    target = anchor or tmux_split_target()
    if not target:
        return None
    layout = pane_layout(window_of(target), "nas")
    command = nas_pane_command(args)
    out = tmux_run(
        "split-window", f"-{layout['side']}", "-l", str(layout["size"]), "-d", "-P", "-F",
        "#{pane_id}", "-t", target, command,
    )
    return out.strip() if out and out.strip() else None


def nas_pane_command(args) -> str:
    """The command a watcher-opened pane runs.

    `--no-daemon` on purpose: the pane reads the NAS store itself each poll, and asking it
    for a watcher would collide with the local one's single lock (two daemons, one state
    file). The pane exits by itself when the far session goes, so a killed watcher still
    cannot leave a pane behind.
    """
    argv = [
        *self_argv(), "-s", "nas", "--stale-after", "0",
        "--no-daemon", "--quiet",
        "--nas-host", args.nas_host, "--nas-root", args.nas_root,
        "--nas-project", args.nas_project, "--fb-marker", args.fb_marker,
    ]
    return " ".join(shlex.quote(part) for part in argv)


def nas_pane_kill(pane: str) -> None:
    tmux_run("kill-pane", "-t", pane)


# ------------------------------------------------ the local pane, kept open
# The pane next to a local session is split by the zsh wrapper, which then sits inside
# the CLI and cannot notice anything — including that pane being killed. The watcher can:
# it is the one process here that outlives the pane and follows the instance. So it puts
# the pane back, which is what makes killing it a mistake rather than a way to lose the
# list. `FBTODO_NO_PANE=1` (the wrapper's own switch) and `--pane-seconds 0` both stop it.
PANE_INSTANCE_RE = re.compile(r"--instance-of\s+(\d+)")
PANE_WATCH_RE = re.compile(r"--watch-pid\s+(\d+)")


def pane_off() -> bool:
    return bool(os.environ.get("FBTODO_NO_PANE"))


def append_log(path: str, line: str) -> None:
    """Append one timestamped line to a log, keeping it bounded.

    Its own handle, opened and closed per line: the NAS watcher's log is also that
    process's stdout, held open in append mode by whoever spawned it, and a second
    long-lived handle could interleave with it.
    """
    try:
        with open(path, "a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
        if os.path.getsize(path) > 65536:
            with open(path) as fh:
                tail = fh.read()[-16384:]
            with open(path, "w") as fh:
                fh.write(tail)
    except OSError:
        pass


def pane_log(line: str) -> None:
    """A line in the keeper's own log — bounded, the way the watcher's is."""
    append_log(PANE_LOG_PATH, line)


def pane_rows() -> list[dict]:
    """Every pane, with what deciding about it needs: its id, its window, its shell."""
    fmt = "#{pane_id}\t#{window_id}\t#{pane_pid}\t#{pane_start_command}"
    rows = []
    for line in (tmux_run("list-panes", "-a", "-F", fmt) or "").splitlines():
        parts = line.split("\t", 3)
        if len(parts) < 4 or not parts[0].startswith("%"):
            continue
        rows.append(
            {
                "pane": parts[0],
                "window": parts[1],
                "pid": int(parts[2]) if parts[2].isdigit() else 0,
                "start": parts[3],
            }
        )
    return rows


def local_pane_ids(instance_pid, rows=None, table=None) -> list[str]:
    """The panes showing THIS instance's list, by name or by ancestry.

    The wrapper's pane names its shell (`--instance-of $$`, and the instance is that
    shell's child) while a replacement names the instance pid itself (`--watch-pid`),
    so both forms are recognised — otherwise this would open a second pane beside the
    one already there, and keep opening one.
    """
    rows = pane_rows() if rows is None else rows
    table = process_table() if table is None else table
    found = []
    for row in rows:
        start = row["start"]
        # a NAS pane is the other watcher's business, and `--no-daemon` is a pane that
        # refuses to poll — neither is this instance's todo list
        if "fbtodo" not in start or "-s nas" in start or "--no-daemon" in start:
            continue
        match = PANE_WATCH_RE.search(start)
        if match and int(match.group(1)) == instance_pid:
            found.append(row["pane"])
            continue
        match = PANE_INSTANCE_RE.search(start)
        if match and descendant_instance(table, int(match.group(1))) == instance_pid:
            found.append(row["pane"])
    return found


def freebuff_pane_id(instance_pid, rows=None, table=None) -> str | None:
    """The pane the session itself is drawn in — what the ask/stall watches read.

    By ancestry, not by the most recent client: the pane to read is the one the instance
    is running in, whichever window you happen to be looking at. None means the session is
    not in tmux, and then no pane can answer for it (the notify scripts say so rather than
    guess).
    """
    rows = pane_rows() if rows is None else rows
    table = process_table() if table is None else table
    for row in rows:
        if row["pid"] and descendant_instance(table, row["pid"]) == instance_pid:
            return row["pane"]
    return None


def session_windows(rows, table) -> dict:
    """instance pid -> the window its own pane is drawn in, for every local session.

    Found by ancestry, not by the most recent client: a replacement pane belongs beside
    the session it describes, even when you are looking at another window. An instance
    that is not in tmux at all is simply absent — and then there is no pane to reopen.
    """
    found = {}
    for row in rows:
        if not row["pid"]:
            continue
        inst = descendant_instance(table, row["pid"])
        if inst:
            found.setdefault(inst, row["window"])
    return found


def instances_with_pane(rows, table) -> set:
    """Which instances already have a live todo pane, in one pass over the panes."""
    covered = set()
    for row in rows:
        start = row["start"]
        if "fbtodo" not in start or "-s nas" in start or "--no-daemon" in start:
            continue
        match = PANE_WATCH_RE.search(start)
        if match:
            covered.add(int(match.group(1)))
            continue
        match = PANE_INSTANCE_RE.search(start)
        if match:
            inst = descendant_instance(table, int(match.group(1)))
            if inst:
                covered.add(inst)
    return covered


# ------------------------------------------- placement: beside the session's own pane
# A todo pane belongs in the strip its session is drawn in, starting on the row under it:
# that is where splitting the session's pane puts it, and where a glance finds it. No
# opener can promise it, though. The wrapper splits BEFORE the CLI exists, so all it can
# name is the pane it believes it is in, and a layout change moves panes afterwards. So
# the position is re-derived from the geometry after the fact and repaired in place.
def pane_rects() -> dict:
    """Geometry per pane: what deciding "is this beside that" needs (one tmux call)."""
    fmt = (
        "#{pane_id}\t#{window_id}\t#{pane_left}\t#{pane_top}"
        "\t#{pane_width}\t#{pane_height}"
    )
    rects = {}
    for line in (tmux_run("list-panes", "-a", "-F", fmt) or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 6 or not parts[0].startswith("%"):
            continue
        try:
            left, top, width, height = (int(part) for part in parts[2:6])
        except ValueError:
            continue
        rects[parts[0]] = {
            "window": parts[1],
            "left": left,
            "top": top,
            "width": width,
            "height": height,
        }
    return rects


def placed_beside(inst_pane: str, todo_pane: str, rects: dict, split: str) -> bool:
    """Is the todo pane on the side of its session's pane that `split` opens it on?

    Below by default, right of it for `FBTODO_SPLIT=h`, so the two agree on one rule.
    Deliberately lenient about the pane being *wider* than the session's — a strip under
    two panes is a layout somebody chose, and dragging it narrow again would be the tool
    arguing with its owner — and strict about where it starts, so a pane that sits
    somewhere else altogether is brought back.
    """
    a, b = rects.get(inst_pane), rects.get(todo_pane)
    if not a or not b or a["window"] != b["window"]:
        return False
    if split == "h":
        return (
            b["left"] == a["left"] + a["width"] + 1
            and b["top"] <= a["top"]
            and b["top"] + b["height"] >= a["top"] + a["height"]
        )
    return (
        b["top"] == a["top"] + a["height"] + 1
        and b["left"] <= a["left"]
        and b["left"] + b["width"] >= a["left"] + a["width"]
    )


def place_pane_beside(pane: str, anchor: str, rects: dict, split: str) -> bool:
    """Move a drifted todo pane back to its session's side of its anchor; True if it moved.

    The anchor is the pane the session is drawn in: the freebuff pane for a local session,
    the ssh for a NAS one. `move-pane` moves the pane itself, so its process, its
    scrollback and its step clocks come along: a repair, never a restart (a re-opened pane
    would lose the timer of the step it is running). A pane found in ANOTHER window is left
    alone — that is an arrangement the owner made, and the window a session happens to sit
    in has no say over one built somewhere else.
    """
    if pane == anchor or placed_beside(anchor, pane, rects, split):
        return False
    here, there = rects.get(pane), rects.get(anchor)
    if not here or not there or here["window"] != there["window"]:
        return False
    return tmux_run("move-pane", "-d", f"-{split}", "-s", pane, "-t", anchor) is not None


# ------------------------------------------------------- per-window pins (the layout)
# A window can be given its own answer for side and size — `fbtodo pin --side h --size 16`
# run in it, or with `--window` naming another. The pin is filed under `session:index`
# (what survives a tmux restart, and what you would type), it is more specific than the
# FBTODO_SPLIT/FBTODO_PANE_SIZE knobs so it wins for its window, and it is enforced the
# way placement is: moved on the wrong side, resized away from the pinned one.
def load_pins() -> dict:
    """The pins file: one read, and nothing trusted beyond "it is an object"."""
    pins = read_json(PINS_PATH, {}) or {}
    return pins if isinstance(pins, dict) else {}


def window_key(window: str | None) -> str | None:
    """`<session>:<index>` for a window — what a pin is filed under.

    A window id (`@71`) belongs to one tmux run; the session name and the window index are
    what survive a restart and what you would type, so a pin follows "window 2 of main".
    """
    if not window:
        return None
    out = tmux_run(
        "display-message", "-t", window, "-p", "#{session_name}:#{window_index}"
    )
    return out.strip() if out and out.strip() else None


def pin_for_window(window: str | None, pins: dict | None = None) -> dict:
    """This window's pin, or {} — looked up by session:index, never by window id."""
    pins = load_pins() if pins is None else pins
    if not pins or not window:
        return {}
    entry = pins.get(window_key(window) or "")
    return entry if isinstance(entry, dict) else {}


# ------------------------------------------------- remembered layout (the last place)
# What a pane looked like last time, so the next one opens there instead of at the
# default: after a tmux server restart (which takes every pane with it) the list comes
# back the size and side it was left on, in the window it belonged to. Keyed by
# `session:index` and by ROLE (`local` / `nas`), because one window can hold both a
# session's list and a NAS one, and they are not the same size. A pin is the deliberate
# version of the same idea and outranks it; this file is written by the keeper, the pins
# file only by `fbtodo pin`, so the two never race.
def load_last() -> dict:
    """The remembered-layout file: one read, nothing trusted beyond "it is an object"."""
    last = read_json(LAST_PATH, {}) or {}
    return last if isinstance(last, dict) else {}


def save_last(last: dict) -> None:
    atomic_write_json(LAST_PATH, last)


def pin_value(window: str | None, role: str, field: str, pins: dict | None = None):
    """One half of a window's pin for a role: `(value, source)`, or `(None, "")` if unset.

    A window can hold two list panes — a local session's and a NAS one — and one
    `{side, size}` need not suit both: `fbtodo pin --role nas` files that role's own half
    under the role, which outranks the shared one for it, while a pin written without a
    role (`--role both`, and every pin this tool wrote before there was a choice) is the
    window's shared answer and stands for both. The source names which of the two
    answered, so `fbtodo why` can say it out loud.
    """
    entry = pin_for_window(window, pins)
    entry = entry if isinstance(entry, dict) else {}
    scoped = entry.get(role)
    if isinstance(scoped, dict) and field in scoped:
        value = scoped[field]
        if (field == "side" and value in ("v", "h")) or (
            field == "size" and isinstance(value, int) and value > 0
        ):
            return value, f"pin:{role}"
    value = entry.get(field)
    if field == "side":
        return (value, "pin") if value in ("v", "h") else (None, "")
    return (value, "pin") if isinstance(value, int) and value > 0 else (None, "")


def pane_layout(window: str | None, role: str, pins: dict | None = None,
                last: dict | None = None) -> dict:
    """Side and size in force for a window's list pane of `role`, and where each came from.

    Most specific first: the window's explicit pin (`fbtodo pin`, that role's own half
    before the window's shared one), then how this pane was left last time (remembered per
    role, so a local list and a NAS one in the same window do not overwrite each other),
    then FBTODO_SPLIT/FBTODO_PANE_SIZE, then the built-in default. The source travels with
    the number because `fbtodo why` prints it: a pane that opens somewhere you did not
    choose can say which of the four did it. `pins`/`last` are passed in by callers that
    ask this for several panes in one pass, so the files are read once.
    """
    key = window_key(window) or ""
    seen = (load_last() if last is None else last).get(key)
    seen = (seen.get(role) if isinstance(seen, dict) else None) or {}
    side, side_source = pin_value(window, role, "side", pins)
    if not side and seen.get("side") in ("v", "h"):
        side, side_source = seen["side"], "last"
    if not side:
        env_side = os.environ.get("FBTODO_SPLIT")
        side = env_side if env_side in ("v", "h") else "v"
        side_source = "env" if env_side in ("v", "h") else "default"
    size, size_source = pin_value(window, role, "size", pins)
    if not size and isinstance(seen.get("size"), int) and seen["size"] > 0:
        size, size_source = seen["size"], "last"
    if not size:
        try:
            env_size = int(os.environ.get("FBTODO_PANE_SIZE") or 0)
        except ValueError:
            env_size = 0
        size, size_source = env_size or 12, "env" if env_size else "default"
    return {
        "side": side, "side_source": side_source,
        "size": size, "size_source": size_source,
        "window": key, "role": role,
    }


def source_note(source: str, window: str) -> str:
    """A layout source as a person reads it — `pin (main:1 nas)` rather than `pin:nas`."""
    if source.startswith("pin:"):
        return f"pin ({window} {source.split(':', 1)[1]})"
    return {
        "pin": f"pin ({window})",
        "last": "remembered",
        "env": "FBTODO_SPLIT/FBTODO_PANE_SIZE",
    }.get(source, "default")


# The keeper's own memory of what it has already seen, so a pane's remembered size is
# applied ONCE when it appears (a standing application would make resizing by hand
# impossible) and so a value nobody chose — a border drag, a terminal resize, a pane
# squashed between others — is not written as if it were a choice.
_SETTLED: set[str] = set()
_LAST_SEEN: dict[str, tuple] = {}


def resize_pane_to(pane: str, rects: dict, side: str, size: int) -> bool:
    """Resize a list pane to a given size; True if it had to move.

    "A given size" is either a pin — enforced on every pass, that is what a pin is for —
    or the remembered size, applied once to a pane that has just appeared.
    """
    rect = rects.get(pane)
    if not rect or (rect["width"] if side == "h" else rect["height"]) == size:
        return False
    flag = "-x" if side == "h" else "-y"
    return tmux_run("resize-pane", "-t", pane, flag, str(size)) is not None


def hold_pane_size(pane: str, window: str, role: str, rects: dict, layout: dict) -> bool:
    """Hold a list pane at the size in force for it; True if it had to be resized.

    Three different promises in one place. A PIN is enforced on every pass — that is what a
    pin is for, and a hand-drag has to lose to it. The REMEMBERED size is applied once, to
    a pane that has just appeared: it is where the pane was left, not a standing
    instruction, and re-applying it every poll would make resizing by hand impossible —
    which is also why the pane is then forgotten, so the next one is welcomed the same way.
    The environment and the built-in default decide only how a NEW pane is split — they are
    what the opener reads (`pane_layout`) — never the size of one already on screen.
    """
    marker = f"{window}\u001f{role}\u001f{pane}"
    source = layout["size_source"]
    if not source.startswith("pin"):
        if source != "last" or marker in _SETTLED:
            return False
    _SETTLED.add(marker)
    return resize_pane_to(pane, rects, layout["side"], layout["size"])


def forget_settled(alive: set) -> None:
    """Drop settled panes that are no longer on the server — a new pane gets its size."""
    for marker in [m for m in _SETTLED if m.rsplit("\u001f", 1)[-1] not in alive]:
        _SETTLED.discard(marker)


def remember_layout(window: str | None, role: str, pane: str, rects: dict, side: str) -> bool:
    """Remember how a list pane is sitting, so the next one opens in the same place.

    The side is the one placement just verified against the geometry. The size comes from
    the pane itself, and is ignored under three cells — that is a pane squashed by a small
    terminal, not a preference. One pass is not enough either: a drag or a resize moves a
    pane through values nobody chose, so the same numbers have to be seen twice before
    they are written. The caller keeps running (the keeper polls every few seconds), and
    the gate is its own memory, per window and role.
    """
    key = window_key(window)
    rect = rects.get(pane)
    if not key or not rect:
        return False
    size = rect["width"] if side == "h" else rect["height"]
    if size < 3:
        return False
    marker = f"{key}\u001f{role}"
    if _LAST_SEEN.get(marker) != (side, size):
        _LAST_SEEN[marker] = (side, size)
        return False
    last = load_last()
    entry = dict(last.get(key) or {})
    if entry.get(role) == {"side": side, "size": size}:
        return False
    entry[role] = {"side": side, "size": size}
    last[key] = entry
    save_last(last)
    return True


def save_pins(pins: dict) -> None:
    """Write the pins file: temp file, rename, like every other write here."""
    atomic_write_json(PINS_PATH, pins)


def window_of(target: str | None) -> str | None:
    """The window a pane (or window) target lives in — pins and layout are per window."""
    if not target:
        return None
    out = tmux_run("display-message", "-t", target, "-p", "#{window_id}")
    return out.strip() if out and out.strip() else None


# How long a keeper waits before deciding there is nothing left to keep a pane open
# for. Not zero: the wrapper splits the pane a moment BEFORE it starts the CLI, so the
# first passes of a fresh keeper see no instance at all.
KEEPER_GRACE = 90.0


def tmux_identity() -> str:
    """Which tmux this program talks to: the `-L`/`-f` form when one is forced, else the
    socket this shell is inside. A keeper belongs to exactly one server, so this is part
    of its record — a keeper left polling a server that has gone cannot serve the one you
    are in, and it must not stand in the way of the one that can."""
    return os.environ.get("FBTODO_TMUX") or os.environ.get("TMUX") or "-default"


def ensure_pane_keeper(args, quiet: bool = True) -> int | None:
    """Have somebody watching the panes — one keeper per tmux server, not one per session.

    Called from the places that already know a session just appeared (the pane's own
    `ensure_daemon`, and the shell autostart that spawns a watcher). Its own process and
    its own lock: a watcher cannot promise this, because the lock that makes it a watcher
    is exactly what a `-s nas` daemon can be holding while your own window's pane is
    gone.
    """
    if pane_off() or args.pane_seconds <= 0:
        return None
    target = tmux_identity()
    rec = read_json(PANE_KEEPER_PATH, {}) or {}
    pane_log(
        f"asked for a keeper (tmux {target}; record "
        + (f"{rec.get('pid')} for {rec.get('tmux')}" if rec.get("pid") else "none")
        + ")"
    )
    pid = lock_holder(PANE_KEEPER_PATH)
    if pid and rec.get("version") == VERSION and rec.get("tmux") == target:
        return int(pid)
    if pid:
        # An older build, or a keeper for another server: it cannot serve this one, and
        # leaving it in place would block the keeper that can (measured in the self-check:
        # a keeper watching a since-killed private server kept every pane it was asked to
        # keep, and never saw them).
        try:
            os.kill(int(pid), signal.SIGTERM)
        except OSError:
            pass
        for _ in range(40):
            if not pid_alive(int(pid)):
                break
            time.sleep(0.05)
        clear_lock(int(pid), path=PANE_KEEPER_PATH)
        pane_log(f"replacing keeper {pid} (tmux {rec.get('tmux')} -> {target})")
    argv = [
        *self_argv(), "pane-watch",
        "--foreground", "--quiet", "--pane-seconds", str(args.pane_seconds),
    ]
    log = open(PANE_LOG_PATH, "ab", buffering=0)
    try:
        child = subprocess.Popen(
            argv, stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True
        )
    except OSError as exc:
        pane_log(f"could not start the keeper: {exc.__class__.__name__}")
        return None
    finally:
        log.close()
    for _ in range(40):
        time.sleep(0.05)
        pid = lock_holder(PANE_KEEPER_PATH)
        if pid:
            return int(pid)
    pane_log(f"keeper {child.pid} never claimed the lock (every {args.pane_seconds:.0f}s)")
    if not quiet:
        print("fbtodo: the pane keeper did not start; see " + PANE_LOG_PATH, file=sys.stderr)
    return None


def cmd_pane_watch(args) -> int:
    """Keep every local session's todo pane open, for as long as there are sessions.

    A process of its own because nothing else can promise it: the pane is the thing being
    watched, so it cannot watch itself, and a watcher is bound to the lock it holds while
    this has none of its own to lose. It reads the process table and tmux the way the rest
    of this program does, keeps every session's pane (not just one), and leaves when the
    last freebuff does — so it never outlives what it is for.
    """
    target = tmux_identity()
    running = lock_holder(PANE_KEEPER_PATH)
    rec = (read_json(PANE_KEEPER_PATH, {}) or {}) if running else {}
    if running and rec.get("version") == VERSION and rec.get("tmux") == target and not args.force:
        if not args.quiet:
            print(f"pane keeper already running (pid {running})", file=sys.stderr)
        return 0
    if not write_lock(os.getcwd(), None, path=PANE_KEEPER_PATH, extra={"tmux": target}):
        if not args.quiet:
            print("pane keeper already running (another process holds the lock)",
                  file=sys.stderr)
        return 0
    pane_log(f"keeper started (pid {os.getpid()}, tmux {target}, every {args.pane_seconds:.0f}s)")

    stop_reason, code = "stopped", 0

    def shutdown(signum=None, _frame=None):
        nonlocal stop_reason, code
        stop_reason = f"signal-{signum}" if signum else "stopped"
        code = 128 + signum if signum else 0
        raise SystemExit(code)

    if not args.once:
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, shutdown)
    empty_since = None
    gone_passes = 0
    try:
        while True:
            # A tmux server always has at least one pane, so an empty list means the
            # server this keeper belongs to is gone — and a keeper watching a dead server
            # can never put a pane back. Three passes, because a server can be busy for a
            # moment; then it leaves, which is also what keeps a test from leaving one
            # behind (measured: a suite of them accumulated while the private servers they
            # watched were killed).
            if pane_rows():
                gone_passes = 0
            else:
                gone_passes += 1
                if gone_passes >= 3:
                    stop_reason = "tmux server gone"
                    break
            for pane in ensure_local_panes(quiet=args.quiet):
                # Written down quiet or not: a pane that came back is the one thing worth
                # being able to see afterwards, and a background process has no stderr
                # anybody reads (this is the file `--help` points at).
                pane_log(f"reopened {pane} for a session in {target}")
            if args.once:
                return 0
            if freebuff_pids():
                empty_since = None
            elif empty_since is None:
                empty_since = time.monotonic()
            elif time.monotonic() - empty_since > KEEPER_GRACE:
                stop_reason = "no instance"
                break
            time.sleep(max(0.5, min(args.pane_seconds, 30.0)))
    except SystemExit:
        pass
    except Exception as exc:
        stop_reason, code = f"error: {exc.__class__.__name__}", EX_CODES["tempfail"]
    finally:
        clear_lock(os.getpid(), path=PANE_KEEPER_PATH)
        pane_log(f"keeper stopped ({stop_reason})")
    if not args.quiet and stop_reason:
        print(f"fbtodo pane keeper stopped ({stop_reason})", file=sys.stderr)
    return code


def local_pane_command(instance_pid) -> str:
    argv = [
        *self_argv(),
        "--watch-pid", str(instance_pid), "--stale-after", "0",
    ]
    return " ".join(shlex.quote(part) for part in argv)


def local_pane_open(cwd: str, instance_pid, window: str, rows=None, table=None) -> str | None:
    # What this window is set to: its pin for this role, then the window's shared pin, then
    # what the pane was left at last time, then the two global knobs (`pane_layout`).
    layout = pane_layout(window, "local")
    split, size = layout["side"], str(layout["size"])
    # Split the pane the session is DRAWN in, not merely its window: given a window, tmux
    # picks that window's *active* pane, which is not necessarily the one running freebuff,
    # and the list then opens beside somebody else's pane — measured live, where a NAS
    # pane and a session pane sharing a window was enough to land a fresh pane in the
    # wrong column. The window stands in only when the instance's own pane is not found.
    target = freebuff_pane_id(instance_pid, rows=rows, table=table) or window
    out = tmux_run(
        "split-window", f"-{split}", "-l", str(size), "-d", "-P", "-F", "#{pane_id}",
        "-c", cwd, "-t", target, local_pane_command(instance_pid),
    )
    return out.strip() if out and out.strip() else None


def ensure_local_panes(quiet: bool = True) -> list[str]:
    """Put back every local session's todo pane that is missing, in the right place.

    Deliberately not scoped to the watcher's own instance: sessions outnumber watchers
    here (one daemon, several `fb` runs in as many windows), and the watcher that holds
    the lock may not be following any local instance at all — a `-s nas` daemon follows
    none. So the pass asks the whole tmux server instead: which panes are running a
    freebuff, which of those already show a list, and what is left to open — and then
    whether the panes that ARE there still sit under the pane their session is drawn in.

    The panes it opens are named after the instance (`--watch-pid <pid>`), so the next
    pass recognises them and this stays a no-op: a pane that is there is never re-split,
    and one that is already placed is never moved.
    """
    if pane_off():
        return []
    rows, table = pane_rows(), process_table()
    wanted = session_windows(rows, table)
    missing = {inst: win for inst, win in wanted.items() if inst not in instances_with_pane(rows, table)}
    # No lock around this: the keeper is one process per tmux server (see
    # `ensure_pane_keeper`), so nothing else splits panes — and a lock nobody can release
    # after a kill would be a way for a pane to stay missing forever.
    opened = []
    cwds = cwds_for(list(missing))
    for inst, window in missing.items():
        pane = local_pane_open(cwds.get(inst) or os.getcwd(), inst, window, rows, table)
        if pane:
            opened.append(pane)
    # A pane that is already there is not necessarily in the right place, which is the
    # other half of the same promise: the wrapper's split can miss (see
    # `local_pane_open`), a hand-move is always possible, and a pane this build opened
    # before an upgrade is wherever the old one put it. Geometry is read AFTER the splits
    # above, which have just moved every pane in the windows they touched.
    rects = pane_rects()
    # Read once for the whole pass: one file read per window would be a syscall per session
    # for an answer that is a dictionary lookup (`pane_layout`).
    pins, last = load_pins(), load_last()
    moved, sized = [], []
    for inst, window in wanted.items():
        inst_pane = freebuff_pane_id(inst, rows=rows, table=table)
        if not inst_pane:
            continue
        # Per window AND per role, because that is where a pin lives: a wide window may
        # want the list beside its session and a tall one below it, and a window holding a
        # NAS ssh as well has two list panes that can want different sizes.
        layout = pane_layout(window, "local", pins, last)
        for pane in local_pane_ids(inst, rows, table):
            if place_pane_beside(pane, inst_pane, rects, layout["side"]):
                moved.append(pane)
                rects = pane_rects()  # the layout just moved under us
            if hold_pane_size(pane, layout["window"], "local", rects, layout):
                sized.append((pane, layout["size_source"]))
                rects = pane_rects()
            # Written down for next time: a pane killed and reopened — or a whole tmux
            # server that went down — comes back at the size it was left, the side it was
            # put on, instead of at the default.
            remember_layout(window, "local", pane, rects, layout["side"])
    forget_settled({row["pane"] for row in pane_rows()})
    for pane in moved:
        # Quiet or not, written down: a pane that came back is the one thing worth being
        # able to see afterwards, and a keeper has no stderr anybody reads.
        pane_log(f"moved {pane} beside its session's pane")
    for pane, source in sized:
        pane_log(f"resized {pane} to its {'pinned' if source.startswith('pin') else source} size")
    if not quiet:
        if opened:
            print(f"fbtodo pane(s) reopened: {', '.join(opened)}", file=sys.stderr)
        if moved:
            print(f"fbtodo pane(s) re-placed beside their session: {', '.join(moved)}", file=sys.stderr)
        if sized:
            print(
                "fbtodo pane(s) resized: "
                + ", ".join(f"{pane} ({source})" for pane, source in sized),
                file=sys.stderr,
            )
    return opened


def nas_notify_once(args, quiet: bool = True) -> int | None:
    """Ask the phone notifier about the live NAS session: one pass, None when absent.

    Run through its shebang rather than `python3 <script>` so a replacement written in
    anything else still works. Exit 78 means "no topic to send to" and the caller stops
    asking for a while; a session must not respawn a script that has nothing to send.
    """
    if not os.path.exists(NAS_NOTIFY):
        return None
    try:
        proc = subprocess.run(
            [NAS_NOTIFY, "--nas-watch", "--once", "--quiet"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if not quiet:
            print(f"nas notifier failed: {exc.__class__.__name__}", file=sys.stderr)
        return None
    if proc.returncode and not quiet:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        print(
            f"nas notifier exited {proc.returncode}: {(detail[-1] if detail else '')[:120]}",
            file=sys.stderr,
        )
    return proc.returncode


def ask_notify_once(args, quiet: bool = True) -> int | None:
    """Ask the question watch whether any freebuff pane is waiting on an answer.

    One pass, its own clock, and its own record of what it has already announced: this
    only has to know WHEN to ask. Run through its shebang rather than `python3 <script>`
    so a replacement written in anything else still works. Exit 78 means there is nothing
    configured to send to — the same signal the other notifiers use.
    """
    if not os.path.exists(ASK_NOTIFY):
        return None
    try:
        proc = subprocess.run(
            [ASK_NOTIFY, "--quiet"], capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if not quiet:
            print(f"ask watch failed: {exc.__class__.__name__}", file=sys.stderr)
        return None
    if proc.returncode and not quiet:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        print(
            f"ask watch exited {proc.returncode}: {(detail[-1] if detail else '')[:120]}",
            file=sys.stderr,
        )
    return proc.returncode


def pane_notify_once(args, quiet: bool = True) -> int | None:
    """Ask the pane watch whether the pane keeper is doing its job.

    No session and no pane are passed: the watch reads the whole tmux server through
    `fbtodo why --json`, because the failure it reports is often one this daemon cannot
    see from inside — a session in another window with no pane, or a keeper that is not
    running at all. It decides, it records, it pushes; this only owns WHEN to ask. Exit
    78 means there is nothing configured to send to, the same signal the other three use.
    """
    if not os.path.exists(PANE_NOTIFY):
        return None
    argv = [PANE_NOTIFY, "--quiet", "--keeper", PANE_KEEPER_PATH]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.SubprocessError) as exc:
        if not quiet:
            print(f"pane watch failed: {exc.__class__.__name__}", file=sys.stderr)
        return None
    if proc.returncode and not quiet:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        print(
            f"pane watch exited {proc.returncode}: {(detail[-1] if detail else '')[:120]}",
            file=sys.stderr,
        )
    return proc.returncode


def pause_notify_once(args, instance_pid, quiet: bool = True) -> int | None:
    """Ask the stall watch whether this session has stopped mid-task.

    It is given the session and the pane it is drawn in, and decides the rest itself
    (its own record, its own sender). Exit 78 means there is nothing configured to send
    to, the same signal the other notifiers use.
    """
    if not os.path.exists(PAUSE_NOTIFY) or not instance_pid:
        return None
    pane = freebuff_pane_id(instance_pid)
    argv = [PAUSE_NOTIFY, "--watch-pid", str(instance_pid), "--quiet"]
    if pane:
        argv += ["--pane", pane]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        if not quiet:
            print(f"stall watch failed: {exc.__class__.__name__}", file=sys.stderr)
        return None
    if proc.returncode and not quiet:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        print(
            f"stall watch exited {proc.returncode}: {(detail[-1] if detail else '')[:120]}",
            file=sys.stderr,
        )
    return proc.returncode


def nas_drop_once(args, death: dict, quiet: bool = True) -> int | None:
    """Tell the drop watch that the NAS session stopped, and what the marker looked like.

    The decision (a stale marker is a death, an absent one is a clean exit), the
    "already reported" record and the push all belong to that script; fbtodo only knows
    WHEN to ask — the pass after a live session was seen to go. Exit 78 means there is
    nothing configured to send to, which the caller honours as it does for the finish
    notification; 10 means a drop was reported.
    """
    if not os.path.exists(DROP_NOTIFY):
        return None
    argv = [
        DROP_NOTIFY, "--nas", "--quiet",
        "--fb", str(death.get("fb") or "-"),
        "--live", "1" if death.get("live") else "0",
    ]
    if death.get("where"):
        argv += ["--cwd", str(death["where"])]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        if not quiet:
            print(f"nas drop watch failed: {exc.__class__.__name__}", file=sys.stderr)
        return None
    if proc.returncode not in (0, 10) and not quiet:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        print(
            f"nas drop watch exited {proc.returncode}: {(detail[-1] if detail else '')[:120]}",
            file=sys.stderr,
        )
    return proc.returncode


def live_watcher_pid(path: str) -> int | None:
    """The watcher claiming `path`, with a VERSION-STALE one replaced rather than adopted.

    A watcher keeps the code it started with, and it outlives the shell that spawned it,
    so after an upgrade the OLD one is still polling — with the old bugs. It is killed and
    its lock cleared instead of being honoured: it writes the old layout, and the pane
    (which checks the producer's version) then silently loses every field the new one
    added. That is how a pre-fix NAS watcher stayed "live" forever, and how an upgrade
    could leave the pane rendering yesterday's shape.
    """
    pid = lock_holder(path)  # the claim, not the number: a leftover record is not a watcher
    if not pid:
        return None
    if (read_json(path, {}) or {}).get("version") != VERSION:
        try:
            os.kill(int(pid), signal.SIGTERM)
        except OSError:
            pass
        for _ in range(40):
            if not pid_alive(int(pid)):
                break
            time.sleep(0.05)
        clear_lock(path=path)  # free once it is gone; refused while it still holds it
        return None
    return int(pid)


def nas_pane_daemon_pid() -> int | None:
    return live_watcher_pid(NAS_LOCK_PATH)


def cmd_nas(args) -> int:
    """Watch the NAS for a session STARTING and open a pane for it (and close it after).

    Not tied to the ssh: the ssh only carries you over there, and a pane opened with it sat
    waiting for something the owner had not started yet. This watches the marker the remote
    host's own `freebuff` wrapper keeps, so the pane appears when the session does — however
    you got there — and goes when the session goes.
    """
    if args.stop:
        pid = nas_pane_daemon_pid()
        if not pid:
            print("fbtodo nas: not running", file=sys.stderr)
            return 0
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        for _ in range(40):
            if not pid_alive(pid):
                break
            time.sleep(0.05)
        try:
            os.unlink(NAS_LOCK_PATH)
        except OSError:
            pass
        if not args.quiet:
            print(f"fbtodo nas watcher stopped (pid {pid})", file=sys.stderr)
        return 0
    if args.status:
        pid = nas_pane_daemon_pid()
        state = read_json(NAS_STATE_PATH, {}) or {}
        panes = nas_pane_ids()
        # Which pane the session's ssh is in, i.e. what a list pane is placed against.
        anchor = (
            nas_ssh_pane(args.nas_host, _iso_ms(state.get("started")))
            if state.get("alive") else None
        )
        if args.json:
            json.dump(
                {"watcher_pid": pid, "panes": panes, "ssh_pane": anchor, "state": state},
                sys.stdout, ensure_ascii=False,
            )
            sys.stdout.write("\n")
            return 0
        print(f"fbtodo nas  {args.nas_host}")
        print(f"  watcher           : {pid or '— not running'}")
        print(
            "  nas session       : "
            + ("live" if state.get("alive") else "not running")
            + (f"  {state.get('dir')}" if state.get("dir") else "")
        )
        print(f"  pane              : {', '.join(panes) if panes else '— none'}")
        if state.get("alive"):
            print(
                "  ssh pane          : "
                + (
                    f"{anchor}  (the pane this session runs in)"
                    if anchor
                    else "— not identified; the pane goes to the active window"
                )
            )
        if not os.path.exists(NAS_NOTIFY):
            print("  phone notifier    : none installed (~/.config/freebuff-notify/todo-bell.py)")
        elif args.notify_seconds > 0:
            print(f"  phone notifier    : every {args.notify_seconds:.0f}s while a session runs")
        else:
            print("  phone notifier    : off (--notify-seconds 0)")
        if not os.path.exists(ASK_NOTIFY):
            print("  ask watch         : none installed (~/.config/freebuff-notify/ask-bell.py)")
        elif args.ask_seconds > 0:
            print(f"  ask watch         : on, every {args.ask_seconds:.0f}s while a session runs")
        else:
            print("  ask watch         : off (--ask-seconds 0)")
        if not os.path.exists(DROP_NOTIFY):
            print("  drop watch        : none installed (~/.config/freebuff-notify/drop-bell.py)")
        else:
            print(
                "  drop watch        : on, when a session stops"
                + ("  (this one is being confirmed)" if state.get("drop_pending") else "")
            )
        hb = state.get("heartbeat_ms")
        if hb:
            age = int(time.time() - hb / 1000)
            print(f"  last poll         : {age}s ago · {state.get('polls', 0)} polls")
            if pid and age > 90:
                print("                      (a stale heartbeat: the watcher is wedged, `fbtodo nas --stop` first)")
        elif pid:
            print("  last poll         : — none yet")
        if state.get("error"):
            print(f"  last error        : {state['error']}")
        return 0
    if args.once or args.foreground or args.dry_run:
        return nas_watch_loop(args)
    running = nas_pane_daemon_pid()
    if running:
        if not args.quiet:
            print(f"fbtodo nas watcher already running (pid {running})", file=sys.stderr)
        return 0
    return 0 if spawn_nas_daemon(args, quiet=args.quiet) else EX_CODES["tempfail"]


def spawn_nas_daemon(args, quiet: bool = True) -> int | None:
    argv = [
        *self_argv(), "nas", "-f", "--quiet",
        "--nas-host", args.nas_host, "--nas-root", args.nas_root,
        "--nas-project", args.nas_project, "--fb-marker", args.fb_marker,
        "--poll", str(args.poll), "--poll-live", str(args.poll_live),
        "--poll-idle", str(args.poll_idle),
        "--notify-seconds", str(args.notify_seconds),
        "--ask-seconds", str(args.ask_seconds),
    ]
    log = open(NAS_LOG_PATH, "ab", buffering=0)
    try:
        subprocess.Popen(
            argv, stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True
        )
    finally:
        log.close()
    for _ in range(60):
        time.sleep(0.05)
        if nas_pane_daemon_pid():
            break
    pid = nas_pane_daemon_pid()
    if not quiet and not pid:
        print("fbtodo nas watcher failed to start; see " + NAS_LOG_PATH, file=sys.stderr)
    return pid


def nas_watch_loop(args) -> int:
    """One pass per poll: a live session and no pane means open one; a dead session and a
    pane we are responsible for means close it. `--once` runs exactly one of these."""
    dry = args.dry_run
    if not dry:
        if nas_pane_daemon_pid() and not (args.once or args.force):
            if not args.quiet:
                print(f"fbtodo nas watcher already running (pid {nas_pane_daemon_pid()})", file=sys.stderr)
            return 0
        if not claim_or_force(NAS_LOCK_PATH, os.path.realpath(os.getcwd()), None,
                              bool(getattr(args, "force", False))):
            if not args.quiet:
                print(f"fbtodo nas watcher already running (pid {nas_pane_daemon_pid() or '—'})",
                      file=sys.stderr)
            return 0

    stop_reason, code = "stopped", 0
    owned: list[str] = []
    last_busy = time.monotonic()
    # The drop watch's state: whether the session was live on the previous pass, and the
    # death witness kept for the confirming pass (see the loop).
    prev_alive = False
    pending_death: dict | None = None
    # The phone notifier's schedule: asked while a session is live, and switched off for
    # half an hour once it says there is nothing configured to send to.
    notify_due = 0.0
    notify_off_until = 0.0
    # The ask watch's own clock: it reads the panes, not this watcher's store, so it is
    # asked on its own schedule rather than as a side effect of the render.
    ask_due = 0.0

    def shutdown(signum=None, _frame=None):
        nonlocal stop_reason, code
        stop_reason = f"signal-{signum}" if signum else "stopped"
        code = 128 + signum if signum else 0
        raise SystemExit(code)

    if not args.once and not dry:
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, shutdown)

    try:
        while True:
            live = nas_liveness(args)
            panes = nas_pane_ids()
            if dry:
                # The ssh pane is named here too: it is what a pane about to be opened
                # would be split off, and the one thing to look at when a pane lands
                # somewhere unexpected.
                print(
                    f"nas={'live' if live.get('alive') else 'idle'} "
                    f"fb={live.get('fb')} dir={live.get('dir') or '-'} "
                    f"ssh={nas_ssh_pane(args.nas_host, _iso_ms(live.get('started'))) or '-'} "
                    f"panes={panes or '-'} "
                    f"action={'open' if live.get('alive') and not panes else 'close' if not live.get('alive') and owned else 'none'}"
                )
                return 0
            alive = bool(live.get("alive"))
            # A session that STOPS is reported once, and only after one more pass agrees:
            # a clean exit removes the marker for a heartbeat too, so the first pass that
            # sees nothing could be a session on its way out in an orderly way. The
            # witness (what the marker looked like as it went) is kept, not re-read.
            if alive:
                pending_death = None
            elif prev_alive:
                pending_death = {
                    "fb": live.get("fb"),
                    "live": bool(live.get("live")),
                    "where": live.get("dir") or "",
                }
            elif pending_death is not None:
                nas_drop_once(args, pending_death, quiet=args.quiet)
                pending_death = None
            prev_alive = alive
            if live.get("alive"):
                # Where the session is drawn — its ssh — so the list can sit beside it.
                # Resolved before the pane exists (that is what it is for) and applied
                # again below, while it does.
                anchor = nas_ssh_pane(args.nas_host, _iso_ms(live.get("started")))
                if panes:
                    owned = panes  # a pane exists (ours from a previous run, or a hand-opened
                    #                 one): adopt it rather than stacking a second
                    nas_place_panes(panes, anchor, quiet=args.quiet)
                else:
                    pane = nas_pane_open(args, anchor)
                    if pane:
                        owned = [pane]
                        if not args.quiet:
                            print(f"opened NAS todo pane {pane}", file=sys.stderr)
                # The phone push matters most when nobody is looking at that pane, so it
                # is its own pass, on its own clock, not a side effect of the render.
                if args.notify_seconds > 0 and time.monotonic() >= max(notify_due, notify_off_until):
                    notify_due = time.monotonic() + args.notify_seconds
                    rc = nas_notify_once(args, quiet=args.quiet)
                    if rc == EX_CODES["config"]:
                        notify_off_until = time.monotonic() + 1800
                if args.ask_seconds > 0 and time.monotonic() >= ask_due:
                    ask_due = time.monotonic() + args.ask_seconds
                    ask_notify_once(args, quiet=args.quiet)
            elif owned:
                # close only what this watcher is responsible for; a pane the owner opened
                # by hand while idle is theirs to keep
                for pane in owned:
                    if pane in panes:
                        nas_pane_kill(pane)
                owned = []
            atomic_write_json(
                NAS_STATE_PATH,
                {
                    "alive": bool(live.get("alive")), "fb": live.get("fb"),
                    "dir": live.get("dir"), "started": live.get("started"),
                    "panes": owned,
                    "drop_pending": bool(pending_death),
                    "error": live.get("error"), "heartbeat_ms": int(time.time() * 1000),
                    "watcher_pid": os.getpid(), "polls": (read_json(NAS_STATE_PATH, {}) or {}).get("polls", 0) + 1,
                },
            )
            if args.once:
                return 0
            clients = tmux_run("list-clients", "-F", "#{client_name}") or ""
            idle = not clients.strip()
            # A watcher spawns itself from a remote shell and then outlives the ssh, so it
            # has to be able to leave: with nobody looking and no session to watch it is
            # only a once-a-minute ssh, and the next remote shell starts it again.
            if live.get("alive") or not idle or owned:
                last_busy = time.monotonic()
            elif args.idle_exit > 0 and time.monotonic() - last_busy > args.idle_exit * 60:
                stop_reason = f"idle {args.idle_exit:.0f}m"
                break
            time.sleep(
                max(0.2, args.poll_live if live.get("alive") else args.poll_idle if idle else args.poll)
            )
    except SystemExit:
        pass
    except Exception as exc:
        stop_reason, code = f"error: {exc.__class__.__name__}", EX_CODES["tempfail"]
    finally:
        if not dry and not args.once:
            final = read_json(NAS_STATE_PATH, {}) or {}
            final.update(status="stopped", stop_reason=stop_reason, panes=[], watcher_pid=None)
            try:
                atomic_write_json(NAS_STATE_PATH, final)
            except OSError:
                pass
            clear_lock(os.getpid(), path=NAS_LOCK_PATH)
    if not args.quiet and stop_reason:
        print(f"fbtodo nas watcher stopped ({stop_reason})", file=sys.stderr)
    return code


# ============================================================ desktop backend
def project_path_of(db: str):
    return (read_json(os.path.join(os.path.dirname(db), "project.json"), {}) or {}).get(
        "projectPath"
    )


def same_path(a, b) -> bool:
    return bool(a) and bool(b) and os.path.realpath(a) == os.path.realpath(b)


def pick_db(pattern: str, project: str | None) -> str | None:
    dbs = [p for p in glob.glob(pattern) if os.path.exists(p)]
    if not dbs:
        return None
    dbs.sort(key=os.path.getmtime, reverse=True)
    if project:
        for db in dbs:
            if same_path(project_path_of(db), project):
                return db
        for db in dbs:
            if project.lower() in db.lower():
                return db
        for db in dbs:
            if project.lower() in (project_path_of(db) or "").lower():
                return db
        return None
    cwd = os.path.realpath(os.getcwd())
    for db in dbs:
        pp = project_path_of(db)
        if not pp:
            continue
        rp = os.path.realpath(pp)
        if cwd == rp or cwd.startswith(rp + os.sep):
            return db
    return dbs[0]


def load_workspace(path: str) -> dict:
    return (read_json(os.path.expanduser(path), {}) or {}).get("workspace") or {}


def tab_for_app(workspace: dict):
    tab_id = workspace.get("activeId")
    if not tab_id:
        return None, None
    for tab in workspace.get("tabs") or []:
        if tab.get("id") == tab_id:
            return tab_id, tab.get("projectPath")
    return tab_id, None


def db_for_thread(pattern: str, thread_id) -> str | None:
    if not thread_id:
        return None
    for db in sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True):
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
            try:
                if con.execute("SELECT 1 FROM threads WHERE id = ?", (thread_id,)).fetchone():
                    return db
            finally:
                con.close()
        except Exception:
            continue
    return None


def _thread_query(cur, sql, params=()):
    row = cur.execute(sql, params).fetchone()
    if not row:
        return None
    tid, ts, todos_json, title = row
    try:
        todos = json.loads(todos_json or "[]")
    except Exception:
        return None
    return {"ts": ts, "todos": todos, "thread": tid, "title": title or ""}


def read_desktop(db: str, thread_id=None, source="active", state_path=DEFAULT_WORKSPACE_STATE):
    project = project_path_of(db)
    workspace = {} if source == "last" else load_workspace(state_path)
    active_tab = None
    if source == "active":
        for space in workspace.get("spaces") or []:
            if same_path(space.get("projectPath"), project):
                active_tab = space.get("activeId")
                break
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    try:
        cur = con.cursor()

        def exists(tid):
            return bool(
                tid and cur.execute("SELECT 1 FROM threads WHERE id = ?", (tid,)).fetchone()
            )

        target = thread_id
        if not target and source == "active":
            target = active_tab if exists(active_tab) else None
        if target and not exists(target):
            target = None
        if target:
            state = _thread_query(
                cur,
                """SELECT m.thread_id, m.ts, json_extract(part.value,'$.input.todos'), t.title
                     FROM messages m JOIN threads t ON t.id = m.thread_id
                     JOIN json_each(m.parts_json) AS part
                    WHERE m.thread_id = ? AND m.parts_json LIKE '%write_todos%'
                      AND json_extract(part.value,'$.toolName') = 'write_todos'
                    ORDER BY m.seq DESC LIMIT 1""",
                (target,),
            ) or {"ts": None, "todos": [], "thread": target, "title": ""}
            state["active"] = target == active_tab
            state["source"] = "active-tab" if target == active_tab else "pinned"
            state["session"] = target
            return state
        state = _thread_query(
            cur,
            """SELECT m.thread_id, m.ts, json_extract(part.value,'$.input.todos'), t.title
                 FROM messages m JOIN threads t ON t.id = m.thread_id
                 JOIN json_each(m.parts_json) AS part
                WHERE m.parts_json LIKE '%write_todos%'
                  AND json_extract(part.value,'$.toolName') = 'write_todos'
                ORDER BY m.ts DESC LIMIT 1""",
        ) or {"ts": None, "todos": [], "thread": None, "title": ""}
        state["active"] = False
        state["source"] = "last" if source == "last" else "fallback"
        state["session"] = state.get("thread")
        return state
    finally:
        con.close()


# =================================================================== snapshot
# ------------------------------------------------------------------ the source seam
# In plain words: a list can come from three different machines' worth of transcript — a
# CLI journal in a directory here, the desktop app's conversation DB, and a session on the
# NAS over ssh — and every reader downstream (the pane, `json`, `snap`, the estimates)
# reads ONE state. A source is that seam: each one answers "is there anything of this kind
# here?" in its own vocabulary and then translates what it found into the state's fields,
# so the dispatch is a loop rather than three branches whose differences drift apart in
# silence. Each source's reading happens in `find` (that is the ssh, the DB open, the file
# stat) and its translation in `describe` — nothing that fetches is in the mapping.
class Source:
    """One place a todo list can come from.

    The contract is three things:

    * `find(args, cwd)` — the raw observation this machine has for this directory, or None
      when there is nothing of this kind to watch. Fetching belongs here.
    * `describe(args, cwd, ob)` — that observation as the fields the state carries:
      `backend`, `target`, `session`, `todos`, `source`, and whatever else only this source
      can supply.
    * `miss()` — what to say when the caller asked for THIS source and it found nothing.
      A source that can never miss still answers, because the caller cannot know which one
      it is asking: that is what keeps `-s cli` an error rather than a quiet fall-through
      into the desktop store.

    `name` is the `-s` flag's word for it; `backend` is the state's.
    """

    name = ""
    backend = ""

    def find(self, args, cwd: str):
        raise NotImplementedError

    def describe(self, args, cwd: str, ob: dict) -> dict:
        raise NotImplementedError

    def miss(self) -> dict:
        return {"backend": None, "todos": [], "error": f"no {self.name} source here"}


class CliSource(Source):
    """The Freebuff CLI's journal: `log.jsonl` in a chat directory, appended mid-turn."""

    name = "cli"
    backend = "cli"

    def find(self, args, cwd: str):
        chat = cli_chat_dir(cwd, args.project, args.chat, args.cli_root)
        return {"chat": chat} if chat else None

    def describe(self, args, cwd: str, ob: dict) -> dict:
        chat = ob["chat"]
        st = read_cli(chat)
        return {
            "backend": "cli",
            "target": chat,
            "store_mtime_ms": store_mtime_ms(chat, "cli"),
            "session": os.path.basename(chat.rstrip("/")),
            "title": st.get("title") or "",
            "first_prompt": st.get("first_prompt"),
            "goal": st.get("goal"),
            "goal_source": st.get("goal_source"),
            "summary": st.get("summary"),
            "now": st.get("now"),
            "nudge": st.get("nudge"),
            "turn_ended": bool(st.get("turn_ended")),
            "iteration": st.get("iteration"),
            "source_updated_ms": st.get("ts"),
            "no_log": bool(st.get("no_log")),
            "todos": st.get("todos") or [],
            # The pane's fallback when there is no list: what this session has done, and the
            # turn it did it in.
            "observed": st.get("observed") or [],
            "turn": st.get("turn") or {},
            # Which model this session runs: the estimates are remembered per model, because
            # a step's span is not comparable across them.
            "model": st.get("model"),
            "source": "cli-journal",
        }

    def miss(self) -> dict:
        return {"backend": None, "todos": [], "error": "no CLI chat for this directory"}


class DesktopSource(Source):
    """The desktop app's conversation DB: one sqlite store, one thread."""

    name = "desktop"
    backend = "desktop"

    def find(self, args, cwd: str):
        source = "last" if args.last else "active"
        thread_id = args.thread
        hint = args.project
        explicit = args.db if "*" not in args.db else None
        db = os.path.expanduser(explicit) if explicit else None
        if args.follow_app and not db:
            tab_id, tab_project = tab_for_app(load_workspace(args.state))
            if tab_id:
                thread_id = tab_id
                source = "pinned"
                hint = tab_project or db_for_thread(DEFAULT_DB_GLOB, tab_id)
        db = db or pick_db(args.db, hint)
        if not db or not os.path.exists(db):
            return None
        return {"db": db, "thread": thread_id, "mode": source}

    def describe(self, args, cwd: str, ob: dict) -> dict:
        st = read_desktop(
            ob["db"], thread_id=ob["thread"], source=ob["mode"], state_path=args.state
        )
        return {
            "backend": "desktop",
            "target": ob["db"],
            "store_mtime_ms": store_mtime_ms(ob["db"], "desktop"),
            "session": st.get("session"),
            "title": st.get("title") or "",
            "iteration": None,
            "source_updated_ms": st.get("ts"),
            "todos": st.get("todos") or [],
            "source": st.get("source"),
        }

    def miss(self) -> dict:
        return {"backend": None, "todos": [], "error": "no conversation DB found"}


class NasSource(Source):
    """A session on the NAS, read over ssh by the extractor the probe ships."""

    name = "nas"
    backend = "nas"

    def find(self, args, cwd: str):
        # A probe always answers — it says what it could not reach rather than nothing — so
        # `find` cannot miss and the error rides on the observation (see the mapping below).
        return {"obs": read_nas(args)}

    def describe(self, args, cwd: str, ob: dict) -> dict:
        st = ob["obs"]
        nas = st.get("nas") or {}
        out = {
            "backend": "nas",
            "target": nas.get("dir") or "",
            "store_mtime_ms": nas.get("mtime_ms"),
            "session": st.get("thread"),
            "title": st.get("title") or "",
            "first_prompt": st.get("first_prompt"),
            "goal": st.get("goal"),
            # A continuation that the NAS transcript's newest user message turns out to be is
            # a nudge there too — and it takes the `now` slot, as locally: both would be
            # saying `continue`, one with the instruction attached.
            "nudge": st.get("nudge"),
            "now": None if st.get("nudge") else st.get("now"),
            # The NAS build records no per-iteration `shouldEndTurn`, so a NAS list can never
            # claim its turn ended; the bell is a local-session feature.
            "turn_ended": False,
            "iteration": st.get("iteration"),
            "source_updated_ms": st.get("ts"),
            "no_log": bool(st.get("no_log")),
            "todos": st.get("todos") or [],
            # What the remote session has called, so an empty list can say what it is waiting
            # on instead of only that it is waiting.
            "tool_calls": st.get("tool_calls") or {},
            "source": "nas-journal",
            # What closes the pane: the session on the NAS, not a pid on this Mac.
            "instance_alive": bool(nas.get("alive")),
            "nas": nas,
            # Read at the far end of the probe above, so the row costs nothing extra.
            "patch": st.get("patch"),
            "alert": st.get("alert"),
        }
        if nas.get("error") and not nas.get("dir"):
            out["error"] = nas["error"]
        return out

    def miss(self) -> dict:
        return {"backend": "nas", "todos": [], "error": "no NAS session"}


SOURCE_CLASSES = (CliSource, DesktopSource, NasSource)
SOURCES = {cls.name: cls() for cls in SOURCE_CLASSES}
# What `-s auto` means, in order: the journal if this directory has one, else the desktop
# store. Every other mode asks ONE source, which is the whole difference between "nothing
# here, look elsewhere" and "nothing here, say so".
SOURCE_ORDER = {
    "auto": ("cli", "desktop"),
    "cli": ("cli",),
    "desktop": ("desktop",),
    "nas": ("nas",),
}


def sources_for(args) -> tuple:
    """The sources `args.source` asks for, in the order they are asked."""
    return tuple(SOURCES[name] for name in SOURCE_ORDER.get(args.source, ("cli", "desktop")))


def snapshot(args, cwd: str | None = None, instance_pid=None) -> dict:
    """The todo state, whatever backend is live, plus the two patch facts for that machine.

    A wrapper rather than four call sites: every local backend — and the paths that find
    no backend at all — should carry the patch row without each one having to remember it.
    The NAS answer is already in its own probe, and reading THIS Mac's logs under a NAS
    pane would be an answer about the wrong machine, so it is left alone there.
    """
    state = _snapshot(args, cwd=cwd, instance_pid=instance_pid)
    if state.get("backend") != "nas":
        state.update(local_patch_alert())
    return state


def _snapshot(args, cwd: str | None = None, instance_pid=None) -> dict:
    """The current todo state, whatever backend is live."""
    cwd = cwd or os.getcwd()
    if instance_pid is None:
        instance_pid, _ = find_instance(
            cwd, getattr(args, "watch_pid", None), getattr(args, "instance_of", None)
        )
    base = {
        "schema": 1,
        "instance_pid": instance_pid,
        "cwd": os.path.realpath(cwd),
        "probed_ms": int(time.time() * 1000),
    }
    # Ask the sources `-s` names, in order, and take the first that has something here.
    chosen = sources_for(args)
    for src in chosen:
        ob = src.find(args, cwd)
        if ob is None:
            continue
        base.update(src.describe(args, cwd, ob))
        return base
    # Nothing to watch: the LAST source asked speaks for the whole chain, which is how
    # `-s cli` says "no CLI chat for this directory" instead of quietly reading the desktop
    # store, and how `auto` ends on the desktop source's own answer.
    base.update(chosen[-1].miss())
    return base


def store_mtime_ms(target: str | None, backend: str | None) -> int | None:
    """When the underlying store last moved. For the CLI that is the journal, which
    is appended every iteration while the agent works, so it is a real activity
    signal — not the todo list, which legitimately sits still during a long step."""
    if not target or not backend:
        return None
    path = os.path.join(target, "log.jsonl") if backend == "cli" else target
    try:
        return int(os.stat(path).st_mtime * 1000)
    except OSError:
        return None


def scratch_size() -> int:
    total = 0
    try:
        for name in os.listdir(SCRATCH):
            try:
                total += os.path.getsize(os.path.join(SCRATCH, name))
            except OSError:
                pass
    except OSError:
        pass
    return total


def prune_scratch(
    now_ms: int | None = None,
    max_records: int | None = None,
    max_age_days: float | None = None,
    log_cap: int | None = None,
    force: bool = False,
) -> dict:
    """Bound the scratch footprint and report what was dropped.

    Throttled with a marker inside the task log, so callers can invoke it on every
    poll or write without turning into a filesystem chore.
    """
    now_ms = now_ms or int(time.time() * 1000)
    max_records = MAX_TASK_RECORDS if max_records is None else max_records
    max_age_days = MAX_TASK_AGE_DAYS if max_age_days is None else max_age_days
    log_cap = LOG_CAP_BYTES if log_cap is None else log_cap
    report = {
        "records_removed": 0,
        "records_kept": 0,
        "temp_removed": 0,
        "log_truncated_bytes": 0,
        "skipped": False,
    }

    log = load_tasklog()
    last = log.get("pruned_ms") or 0
    if not force and (now_ms - last) < PRUNE_INTERVAL_S * 1000:
        report["skipped"] = True
        report["records_kept"] = len(log.get("tasks") or {})
        return report

    tasks = log.setdefault("tasks", {})
    cutoff = now_ms - max_age_days * 86400 * 1000 if max_age_days else 0
    for key, rec in list(tasks.items()):
        stamp = (rec or {}).get("done_ms") or (rec or {}).get("started_ms") or 0
        if cutoff and stamp and stamp < cutoff:
            tasks.pop(key, None)
            report["records_removed"] += 1
    if max_records and len(tasks) > max_records:  # cap: keep the most recent
        order = sorted(
            tasks.items(),
            key=lambda kv: (kv[1].get("done_ms") or kv[1].get("started_ms") or 0),
        )
        for key, _rec in order[: len(tasks) - max_records]:
            tasks.pop(key, None)
            report["records_removed"] += 1
    report["records_kept"] = len(tasks)
    log["pruned_ms"] = now_ms
    # ...and the stream the view came from: this is the one moment the events about records
    # that are gone can be dropped, and it is why the stream cannot grow without bound
    # behind the caps above. Compaction carries the cursor with it, so the memo written
    # below is the fold of what is left rather than of everything ever appended.
    try:
        compact_task_events(log)
    except OSError:
        pass
    try:
        atomic_write_json(TASKS_PATH, log)
    except OSError:
        pass

    # temp files orphaned by a kill mid-write (atomic writes clean up on exception,
    # not on SIGKILL)
    try:
        for name in os.listdir(SCRATCH):
            if not (name.startswith(".fbtodo.") and name.endswith(".tmp")):
                continue
            path = os.path.join(SCRATCH, name)
            try:
                if time.time() - os.path.getmtime(path) > TEMP_MAX_AGE_S:
                    os.unlink(path)
                    report["temp_removed"] += 1
            except OSError:
                pass
    except OSError:
        pass

    # the daemon's stdio is append-only; truncating an append-mode fd is safe
    try:
        size = os.path.getsize(LOG_PATH)
        if log_cap and size > log_cap:
            os.truncate(LOG_PATH, 0)
            report["log_truncated_bytes"] = size
    except OSError:
        pass
    return report


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


# ------------------------------------------- the patches themselves, and the alerts
# The pane says two things about the CLI patches that no store knows: what the last
# patch pass DID, and when the phone was last told something. Both are already in a log,
# so the reader is a tail plus one classification.
# A log line's own stamp: the Mac watcher writes local time (`2026-09-27 12:44:09`), the
# NAS hook writes UTC (`2026-09-27T19:53:48Z`), and the separator is what tells them apart.
_STAMP_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})")
# The local watcher's own words -> an outcome. `patched` and `FAILED` close a pass;
# `binary changed` opens one, which is why it is not read as a failure. A line matching
# none of these is not an outcome at all (a lock takeover, a `reanchor:` note) and is
# skipped rather than guessed at — those can be the newest line while a pass is still
# running, and reading one as the outcome would report a failure as a success.
LOCAL_PATCH_WORDS = (
    ("patched", "ok"),
    ("FAILED", "failed"),
    ("binary changed", "pending"),
    ("converge reported", "pending"),
)
# The NAS hook writes one line per pass — `…Z converge=ok patch=ok binary=0.1.1 [size …]` —
# with anything wrong on the indented lines under it.
NAS_PATCH_RE = re.compile(r"patch=(\w+)\s+binary=(\S+)")
# An alert's own words -> the colour it gets. Everything the kit sends is a fact; `muted`
# means the phone was deliberately NOT told, which is worth an amber row.
ALERT_KIND_SEVERITY = {"sent": "ok", "resolved": "ok", "duplicate": "ok", "muted": "warn"}
PATCH_SEVERITY = {"ok": "ok", "pending": "warn", "incomplete": "bad", "failed": "bad"}


def entry_ms(text: str, utc: bool = False) -> int | None:
    """The epoch-ms a log entry starts with, or None when it starts with no stamp.

    A continuation line (the NAS hook indents its complaints) has no stamp, which is how
    the reader tells one entry from the next.

    Two clocks write the space-separated form: this Mac's kit logs LOCAL time and the NAS
    container logs UTC (`TZ` unset over there), so the SOURCE has to say which — `utc` is
    passed by the NAS readers. The `T…Z` form is unambiguous and decides itself. Guessing
    from the format alone made a 28-minute-old NAS alert read as `0s ago`: its UTC stamp
    was a future time on a PDT clock, and the age was clamped to zero.
    """
    m = _STAMP_RE.match(text or "")
    if not m:
        return None
    y, mo, d, h, mi, s = (int(g) for g in m.groups())
    secs = (
        calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0))
        if utc or (text or "")[10:11] == "T"
        else time.mktime((y, mo, d, h, mi, s, 0, 0, -1))
    )
    return int(secs * 1000)


def read_tail(path: str, limit: int = PATCH_TAIL_BYTES) -> list[str]:
    """The last few non-empty lines of a file, read from its TAIL.

    Both logs are append-only and grow without bound, and this is asked on every poll of
    the pane it feeds, so the file is seeked rather than read.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - limit))
            blob = fh.read()
    except OSError:
        return []
    lines = blob.decode("utf-8", "replace").splitlines()
    if size > limit and lines:
        lines = lines[1:]  # the seek landed mid-line: that first fragment is not an entry
    return [ln.rstrip() for ln in lines if ln.strip()]


def _age_s(at_ms: int | None) -> int | None:
    return None if not at_ms else max(0, int(time.time() - at_ms / 1000.0))


def _tidy(text: str, limit: int = PATCH_REASON_MAX) -> str:
    """One entry's payload, collapsed to one line and one row's worth of columns."""
    return _clip(" ".join(str(text or "").split()), limit)


# In plain words: the PATCH and ALERT rows are borrowed from two log files that other
# parts of this setup write to. Rather than watch them, this reads their last few lines
# whenever the pane is drawn and reports the newest line it understands.
def local_patch_alert(patch_log: str | None = None, alert_log: str | None = None) -> dict:
    """The last patch outcome and the last alert, from this Mac's own logs.

    Both paths are read from the module constants at CALL time, so a fixture (the
    self-check) can point them somewhere else without a subprocess.
    """
    patch_log = patch_log or PATCH_LOG
    alert_log = alert_log or ALERT_LOG
    lines = read_tail(patch_log)
    patch = None
    for line in reversed(lines):
        m = _STAMP_RE.match(line)
        if not m:
            continue
        body = line[m.end():].strip()
        head, _dash, tail = body.partition(" — ")
        word = head.strip()
        outcome = next((o for needle, o in LOCAL_PATCH_WORDS if word.startswith(needle)), None)
        if not outcome:
            continue
        at = entry_ms(line)
        # `(86192720:1790538247:388406469)` is the binary's identity, which the row does not
        # need (the version is shown instead) and which would spend its whole width.
        reason = re.sub(r"\s*\([^()]*\)\s*$", "", (tail or word).strip())
        patch = {
            "outcome": outcome,
            "severity": PATCH_SEVERITY.get(outcome, "warn"),
            "reason": _tidy(reason),
            "version": read_json(PATCH_META, {}).get("version"),
            "at_ms": at,
            "age_s": _age_s(at),
            "source": patch_log,
        }
        break
    return {
        "patch": patch,
        "alert": alert_from_lines(read_tail(alert_log), alert_log),
    }


def alert_from_lines(lines: list[str], source: str, utc: bool = False) -> dict | None:
    """The newest alert line, in either host's format, as {kind, text, at_ms, severity}.

    `source` and `utc` are passed in rather than read from the constants because the NAS
    answer arrives inside the probe: its path is the honest one to report, and its clock
    is the container's (UTC), not this Mac's.
    """
    for line in reversed(lines):
        m = _STAMP_RE.match(line)
        if not m:
            continue
        body = re.sub(r"^phone:\s*", "", line[m.end():].strip())
        kind = (body.split(" ", 1)[0] or "").lower()
        if kind not in ("sent", "muted", "duplicate", "resolved"):
            continue
        at = entry_ms(line, utc)
        # ` — http 200 {"id":…}` is the transport's own reply: not what this row is about,
        # and long. The digest in `(2f14bfcf14a4d794)` is the dedupe key, not news either.
        text = re.split(r"\s+—\s+http\s", body)[0]
        text = re.sub(r"\s*\([0-9a-f]{8,}\)", "", text).rstrip(" —-\t").strip()
        return {
            "kind": kind,
            "severity": ALERT_KIND_SEVERITY.get(kind, "warn"),
            "text": _tidy(text),
            "at_ms": at,
            "age_s": _age_s(at),
            "source": source,
        }
    return None


def nas_patch_from_tail(blob: str) -> dict | None:
    """The NAS patch tail, as it came back in the probe: entries split on the \\x1c the
    far side joins them with.

    The outcome entry is the last one that starts with a stamp; the indented lines under
    it are its own account of what went wrong, which is the text worth showing when the
    news is bad. Parsed here rather than over there because BusyBox is a poor place to
    classify anything.
    """
    lines = (blob or "").split("\x1c")
    idx = None
    for i, line in enumerate(lines):
        if entry_ms(line, True) is not None and NAS_PATCH_RE.search(line):
            idx = i
    if idx is None:
        return None
    m = NAS_PATCH_RE.search(lines[idx])
    outcome = (m.group(1) or "").lower()
    # `freebuff: local CLI patches incomplete (no window for …); the rest were applied` is
    # the hook's own wording: the program's tag and the outcome word are what the row
    # already says, and the parenthetical IS the reason, so all three are taken off.
    reasons = [
        _tidy(re.sub(
            r"^\((.*?)\)",
            r"\1",
            re.sub(r"^(incomplete|failed|ok)\s+", "",
                   re.sub(r"^\s*local CLI patches\s+", "", re.sub(r"^\s*freebuff:\s*", "", ln))),
        ))
        for ln in lines[idx + 1:] if ln.strip() and entry_ms(ln, True) is None
    ]
    at = entry_ms(lines[idx], True)
    return {
        "outcome": outcome,
        "severity": PATCH_SEVERITY.get(outcome, "warn"),
        "reason": reasons[0] if reasons else "",
        "version": m.group(2),
        "at_ms": at,
        "age_s": _age_s(at),
        "source": NAS_PATCH_LOG,
    }


def patch_row_text(pairs: list) -> str:
    """One row's plain text, `PATCH  …   ALERT  …`, without the leading indent."""
    return "   ".join(f"{label}  {text}" for label, text, _sev in pairs)


def patch_detail_forms(state: dict) -> tuple:
    """The two facts in every width they can be written: (patch forms, alert forms).

    Patch, widest first: `outcome · version · age · reason` (the reason only when the news
    is bad — `patched — the collapse and the session-end reason are in place` says nothing
    the `ok` beside it does not), then the same without the reason, then without the
    version. Alert: its own words with the age, then just the kind with the age, then the
    kind alone.
    """
    patch = state.get("patch") or {}
    alert = state.get("alert") or {}
    patch_forms, alert_forms = [], []
    if patch.get("outcome"):
        bits = [str(patch["outcome"])]
        if patch.get("version"):
            bits.append(str(patch["version"]))
        if patch.get("at_ms"):
            bits.append(fmt_age(patch["at_ms"]))
        seen = list(bits)
        if patch.get("outcome") != "ok" and patch.get("reason"):
            bits.append(_tidy(str(patch["reason"])))
        patch_forms = [" · ".join(bits), " · ".join(seen)]
        tight = [str(patch["outcome"])]
        if patch.get("at_ms"):
            tight.append(fmt_age(patch["at_ms"]))
        # ...and the tightest form the row can fall back to: the outcome and how long ago.
        patch_forms.append(" · ".join(tight))
    if alert.get("kind"):
        text = str(alert.get("text") or alert["kind"])
        kind = str(alert["kind"])
        age = fmt_age(alert["at_ms"]) if alert.get("at_ms") else ""
        alert_forms = [" · ".join(b for b in (text, age) if b),
                       " · ".join(b for b in (kind, age) if b),
                       kind]
    # `patch`/`alert` are the severities for colouring; kept beside the forms so a caller
    # does not have to re-read the state.
    return (patch_forms, alert_forms, patch.get("severity") or "warn",
            alert.get("severity") or "warn")


def _shrink(pair: tuple, width: int) -> list:
    """One fact, made to fit `width`: first its tail goes, then it is clipped."""
    label, text, sev = pair
    bits = text.split(" · ")
    while len(bits) > 3 and _cell_width(
        "  " + patch_row_text([(label, " · ".join(bits), sev)])
    ) > width:
        bits.pop()
    room = max(4, width - _cell_width(f"  {label}  "))
    return [(label, _clip(" · ".join(bits), room), sev)]


def refit_row(state: dict, width: int | None = None) -> list:
    """The refit progress as one row of [(label, text, severity)] — or nothing at all.

    Nothing until the log carries `REFIT_MIN_SCORED` closed steps: below that a "progress"
    row would be counting towards a number nobody can read yet, and every row of chrome is a
    step the list loses. Once it shows, it counts the steps the young-list clip actually
    DECIDED — the only ones that can judge the clip, since on most steps the two configs write
    the same number — against what the 2026-09-29 refit says it takes (see `REFIT_MIN_DECIDED`).
    `ok` throughout: this is the pane's quiet chrome, and the words say whether it is ready.
    """
    rr = state.get("refit") or {}
    scored = int(rr.get("scored") or 0)
    if scored < REFIT_MIN_SCORED:
        return []
    decided = int(rr.get("decided") or 0)
    if decided >= REFIT_MIN_DECIDED:
        text = f"ready · {decided} decided over {scored} scored — the clip can be judged"
    else:
        pct = int(round(100.0 * decided / REFIT_MIN_DECIDED))
        text = f"{decided}/{REFIT_MIN_DECIDED} decided · {scored} scored ({pct}% to judging the clip)"
    room = None if width is None else max(8, width - _cell_width("  REFIT  "))
    return [("REFIT", text if room is None else _clip(text, room), "ok")]


def patch_row(state: dict, width: int | None = None) -> list:
    """The two facts as ONE row of [(label, text, severity)], laddered to `width`.

    One row and never two: the pane's height is fixed, so every row of chrome is a step the
    list loses. The ladder therefore trades DETAIL — the alert's own words first, then the
    patch's version — rather than spending another row on the second fact. Empty when the
    state carries neither fact, which is what lets a state written by a build that did not
    read them (or a fixture) render exactly as it always did.
    """
    patch_forms, alert_forms, psev, asev = patch_detail_forms(state)
    if not patch_forms:
        return []
    # Every pair form first, and the patch alone only after all of them: detail is traded
    # for detail (the alert's words, then the version), so the alert is given up only when
    # no form carrying both facts fits at all.
    cands: list = [
        [("PATCH", p, psev), ("ALERT", a, asev)] for p in patch_forms for a in alert_forms
    ]
    cands += [[("PATCH", p, psev)] for p in patch_forms]
    if width is None:
        return cands[0]
    for cand in cands:
        if _cell_width("  " + patch_row_text(cand)) <= width:
            return cand
    return _shrink(("PATCH", patch_forms[-1], psev), width)


TASKLOG_SCHEMA = 2  # 2 = records keyed by (session, task)


def short_duration(ms, seconds: bool = False) -> str:
    """'45s', '3m', '1h02m' — coarse by default, for ages and idle counters.

    `seconds=True` is for a clock the reader is watching: the seconds stay visible
    past the minute (`2m35s`, zero-padded so the field does not jitter) instead of
    a step that took 2m35s reading as `2m`. Above an hour they go: `1h02m` is the
    length a duration can spend on a step line, and a step that long is rare.
    """
    if not ms:
        return ""
    secs = max(0, int(ms // 1000))
    if secs < 60:
        return f"{secs}s"
    mins = secs // 60
    if mins < 60 and seconds:
        return f"{mins}m{secs % 60:02d}s"
    if mins < 60:
        return f"{mins}m"
    return f"{mins // 60}h{mins % 60:02d}m"


# ---- time estimation --------------------------------------------------------------
# The pane does not know how long a step will take, so it learns a pace from the steps
# already finished and projects the rest with it. The number is deliberately coarse — a
# projection, not a promise — and rides inline on a row (`~3m`) rather than claiming a
# line of its own: the frame's height is fixed, and every extra row costs a step.
DEFAULT_PACE_MS = 120_000       # nothing finished yet: fall back to a couple of minutes
PACE_BOUND_FACTOR = 4.0         # how far the list's own pace may sit from the remembered one
PACE_BOUND_SAMPLES = 3          # ...and only while the list has fewer finished steps than this
STUCK_FACTOR = 2.0              # running time / pace at which a step reads as stuck
EST_CAP_MS = 6 * 3_600_000      # clamp one span, so a runaway clock cannot skew the ETA
LIST_BEHIND_MS = 10 * 60_000   # a FINISHED list this far behind a still-writing session
MIN_TASK_TEXT = 18              # columns a tagged row keeps for the task text itself


def step_spans_ms(times: dict, todos: list, now_ms: int) -> list[int]:
    """The measured duration of every finished step that was actually seen running."""
    spans: list[int] = []
    for t in todos or []:
        if not t.get("completed"):
            continue
        rec = (times or {}).get(str(t.get("task", ""))) or {}
        span = live_elapsed(rec, now_ms)
        # A sub-10s span is a list flip, not a measurement: it is shown on its row but it
        # must not set the pace for everything after it (see `label_floor_ms`).
        if span and span >= label_floor_ms():
            spans.append(min(span, EST_CAP_MS))
    return spans


# In plain words: the middle value, not the average. Five steps of 1, 2, 3, 4 and 40
# minutes average about 10 and have a middle of 3 — and 3 is the better guess for the next
# one. A single freak-slow step must not drag every estimate with it.
def _median_ms(values: list[int]) -> int | None:
    """The middle of a list of spans, robust to one value running away."""
    if not values:
        return None
    spans = sorted(values)
    mid = len(spans) // 2
    return spans[mid] if len(spans) % 2 else (spans[mid - 1] + spans[mid]) // 2


# In plain words: a memory of how long each named step took, kept between sessions and kept
# per model — the same step costs very different time under different models. It is filed
# under the step's own wording, so "Run the tests" is estimated from the last "Run the
# tests" and not from the average of whatever this list happens to hold.
def task_history_from_log(tasks: dict, now_ms: int | None = None, model: str | None = None) -> dict:
    """Per-task remembered span (median), read from the log's finished records.

    Kept ACROSS sessions, so a later list projects a step from what it took last time
    rather than from the average of an unrelated list. Two pruning rules keep a stale
    wording from skewing a pace it is never asked about:

    * a record outside the retention window (`HISTORY_MAX_AGE_DAYS`, the same as the log's
      own prune) contributes nothing, so an ancient fluke drops out immediately instead of
      waiting for the hourly prune to remove the record;
    * the map is capped to `HISTORY_MAX_ENTRIES` of the most-recently-seen names, so the
      fallback median is not diluted by a long tail of one-off titles.

    Median again, for the same reason as the pace: one slow run must not poison the
    estimate forever.

    MODEL-AWARE: a step's span is only comparable to a step taken by the SAME model —
    measured 2026-09-26, `deepseek/deepseek-v4-flash` and `stealth/space-bunny-alpha` on
    the same machine differed by more than the whole estimate. So when the session names a
    model, records carrying that model win, and records from any other model are used only
    if it has none of its own (a model seen for the first time is better served by a rough
    number than by none, and its own records take over as soon as it has them).

    Each entry is `{"med": int, "n": int}` rather than a bare number: the median is still
    what a caller reads, and `n` is how many samples stand behind it — which is the whole
    difference between a guess from one run and a settled number. Older state files hold a
    bare int and read the same way (see `hist_med`).
    """
    by_task, last_seen = _history_gather(
        tasks, now_ms, model, lambda rec, label: label
    )
    history: dict[str, dict] = {}
    for label, spans in by_task.items():
        entry = _hist_entry(spans)
        if entry:
            history[label] = entry
    if len(history) > HISTORY_MAX_ENTRIES:
        recent = sorted(history, key=lambda k: last_seen.get(k, 0), reverse=True)
        history = {k: history[k] for k in recent[:HISTORY_MAX_ENTRIES]}
    return history


# In plain words: the same memory, keyed by what a step DID instead of what it was called.
# This is the one that can actually get better with use: the wording space grows without
# bound (172 of 173 labels seen once) while the shape space is small — a handful of verbs
# binned three ways — so samples accumulate in the same bucket and the number sharpens.
def shape_history_from_log(
    tasks: dict, now_ms: int | None = None, model: str | None = None
) -> dict:
    """Per-SHAPE remembered span: {`edited3+ ran2`: {med, n}} from the log's finished steps.

    Same retention and model rules as `task_history_from_log` (one gatherer serves both),
    so a shape learned on a fast model never projects a slow one. A step whose calls were
    never recorded contributes nothing rather than a bucket of its own.
    """
    by_shape, last_seen = _history_gather(
        tasks, now_ms, model, lambda rec, label: shape_of(rec.get("shape"))
    )
    shapes: dict[str, dict] = {}
    for bucket, spans in by_shape.items():
        entry = _hist_entry(spans)
        if entry:
            shapes[bucket] = entry
    if len(shapes) > HISTORY_MAX_ENTRIES:
        recent = sorted(shapes, key=lambda k: last_seen.get(k, 0), reverse=True)
        shapes = {k: shapes[k] for k in recent[:HISTORY_MAX_ENTRIES]}
    return shapes


# In plain words: one remembered number together with the evidence behind it — how many
# samples, and how far apart they were. A number without its `n` cannot be told from a
# guess, and one without its spread cannot be told from a promise.
def _hist_entry(spans: list[int]) -> dict | None:
    """One history entry: the median, its sample count, and its quartiles when there are two."""
    mid = _median_ms(spans)
    if not mid:
        return None
    entry: dict = {"med": mid, "n": len(spans)}
    spread = _spread_ms(spans)
    if spread:
        entry["lo"], entry["hi"] = spread
    return entry


# In plain words: one pass over the task log, read either way — by step wording for the
# per-step memory, or by call shape for the one that accumulates. One gatherer, so both
# memories age out, cap and split by model identically.
def _history_gather(tasks: dict, now_ms: int | None, model: str | None, key_of):
    """({key: [spans]}, {key: last stamp}) for this model, or for every model if it has none."""
    now_ms = now_ms or int(time.time() * 1000)
    cutoff = now_ms - HISTORY_MAX_AGE_DAYS * 86400 * 1000
    mine: dict[str, list[int]] = {}      # spans this model took
    every: dict[str, list[int]] = {}     # spans of any model
    last_seen: dict[str, int] = {}
    for key, rec in (tasks or {}).items():
        if not isinstance(rec, dict):
            continue
        label = key.split("\x1f", 1)[1] if "\x1f" in key else key
        bucket = key_of(rec, label)
        if not bucket:
            continue
        started, stopped = rec.get("started_ms"), rec.get("done_ms")
        stamp = stopped or started or 0
        if stamp and stamp < cutoff:
            continue
        last_seen[bucket] = max(last_seen.get(bucket, 0), stamp)
        if not started or not stopped or stopped <= started:
            continue
        span = min(stopped - started, EST_CAP_MS)
        if span < label_floor_ms():
            continue  # a list flip is shown, not remembered (see `label_floor_ms`)
        every.setdefault(bucket, []).append(span)
        if model and rec.get("model") == model:
            mine.setdefault(bucket, []).append(span)
    # this model's own spans when it has any, every model's when it has none yet
    return (mine or every), last_seen


# In plain words: the SHAPE of a step is what it DID — how many calls it made — rather than
# what it was called. The first build signed the MIX (`edited3+ ran2`); measured 2026-09-29
# over the 258 steps recovered from the CLI journals, the COUNT alone carries more of the
# duration than the mix does — out-of-sample R^2 0.645 against 0.390, and present for 100% of
# steps against 59% — so the signature is the total call count, binned by powers of two.
# That is also why "three edits and nine edits" finally land in different buckets.
def shape_of(shape: dict | None) -> str:
    """A step's size as its signature: `calls0`, `calls1`, ... = log2 of the call count.

    Log2 bins, not exact counts: 1, 2-3, 4-7, 8-15 calls. Few enough buckets that samples
    accumulate (the verb mix needed 68 keys for the same 170 steps; this needs 7), and fine
    enough that a step of one call is never confused with one of twelve.
    """
    total = 0
    for n in (shape or {}).values():
        try:
            total += int(n)
        except (TypeError, ValueError):
            continue
    if total <= 0:
        return ""
    return f"calls{total.bit_length() - 1}"


def step_shape_bucket(times: dict, task) -> str:
    """The size the step being worked on has revealed so far, or "" for one that has none."""
    return shape_of(((times or {}).get(str(task)) or {}).get("shape"))


def bucket_ordinal(bucket: str) -> int | None:
    """The log2 call bucket's ordinal (`calls4` = 16-31 calls -> 4), or None if not a bucket."""
    name = str(bucket or "")
    if not name.startswith("calls"):
        return None
    try:
        return int(name[len("calls"):])
    except ValueError:
        return None


def sized_entry(shapes: dict | None, bucket: str) -> dict | None:
    """The size memory's entry for `bucket`, or None while that step is too young to have one.

    The one place the sample gate and the `SHAPE_MIN_BUCKET` floor are applied, so no caller
    can look a size up on its own terms and drift from the ladder `pick_estimate` walks.
    """
    ordinal = bucket_ordinal(bucket)
    if ordinal is None or ordinal < SHAPE_MIN_BUCKET:
        return None
    entry = (shapes or {}).get(str(bucket))
    if isinstance(entry, dict) and int(entry.get("n") or 0) >= SHAPE_MIN_SAMPLES:
        return entry
    return None


# In plain words: what a WAITING step is probably going to be. It has made no calls, so it
# has no size and the size memory cannot price it — but it does have its wording. Measured
# 2026-09-29 over the 170 ticked steps recovered from the CLI journals, a keyword class of
# that wording predicts the call count well enough that `class x seconds-per-call`, blended
# 50/50 in log space with the list's pace, beats the pace ALONE on every column: median
# 2.10x against 2.89x, mean 3.63x against 4.71x, p90 6.37x against 8.48x, and it wins on 74%
# of steps. Used by itself the label model fixes the median and wrecks the tail, which is
# exactly why it is only ever a half of the answer.
CLASS_RULES = (
    ("deploy", ("deploy", "nas", "ssh", "container", "docker", "publish", "push")),
    ("run", ("run", "test", "tests", "verify", "execute", "build", "sweep", "selfcheck",
             "check", "profile", "measure", "bench")),
    ("docs", ("document", "docs", "readme", "doc", "comment", "comments", "explain")),
    ("edit", ("add", "write", "fix", "implement", "update", "edit", "change", "refactor",
              "create", "remove", "delete", "rename", "move", "wire", "retire", "switch")),
    ("inspect", ("read", "look", "check", "confirm", "find", "search", "examine", "review",
                 "inspect", "locate", "understand", "explore", "map", "recon", "see")),
)
CLASS_WORDS = 64   # words of a label the class test looks at, so a whole paragraph is cheap
STOP_WORDS = frozenset(
    "the a an to of and or for in on with into from it its this that is are be as at by "
    "not no new so if then else my your our their".split()
)


def label_class(task: str) -> str:
    """The coarse kind of work a step's wording names: deploy / run / docs / edit / inspect."""
    text = re.sub(r"\([^)]*\)", " ", str(task or "")).lower()
    words = {w for w in re.findall(r"[a-z0-9]+", text)
             if len(w) > 1 and w not in STOP_WORDS}
    for name, needles in CLASS_RULES:
        if words.intersection(needles):
            return name
    return "other"


def call_memory_from_log(tasks: dict, now_ms: int | None = None, model: str | None = None) -> dict:
    """{classes: {cls: calls}, calls: int, rate_ms: int} learned from finished steps.

    The one memory a waiting step can use. Model-aware and age-gated like the span memories,
    and it counts only real steps (a sub-10 s flip has no shape worth learning from).
    """
    now_ms = now_ms or int(time.time() * 1000)
    cutoff = now_ms - HISTORY_MAX_AGE_DAYS * 86400 * 1000
    mine: dict[str, list[int]] = {}
    every: dict[str, list[int]] = {}
    mine_rate: list[int] = []
    every_rate: list[int] = []
    for key, rec in (tasks or {}).items():
        if not isinstance(rec, dict):
            continue
        started, stopped = rec.get("started_ms"), rec.get("done_ms")
        if not started or not stopped or stopped <= started or stopped < cutoff:
            continue
        span = min(stopped - started, EST_CAP_MS)
        if span < label_floor_ms():
            continue
        total = 0
        for n in (rec.get("shape") or {}).values():
            try:
                total += int(n)
            except (TypeError, ValueError):
                continue
        if total <= 0:
            continue
        label = key.split("\x1f", 1)[1] if "\x1f" in key else key
        cls = label_class(label)
        every.setdefault(cls, []).append(total)
        every_rate.append(max(1, round(span / total)))
        if model and rec.get("model") == model:
            mine.setdefault(cls, []).append(total)
            mine_rate.append(max(1, round(span / total)))
    classes, rates = (mine, mine_rate) if mine else (every, every_rate)
    if not classes:
        return {}
    return {
        "classes": {c: _median_ms(v) for c, v in classes.items()},
        "calls": _median_ms([n for v in classes.values() for n in v]),
        "rate_ms": _median_ms(rates) or 0,
    }


def pending_blend_ms(
    task: str, pace_ms: int, calls_mem: dict | None, weight: float | None = None
) -> int | None:
    """A waiting step's projection: label-class calls x seconds-per-call, blended with the pace.

    Blended in log space, `w` on the label model and `1 - w` on the pace (0.5 by default,
    which is the geometric mean). `w` was not fitted — it is the midpoint, and the honest
    reading of the replay is that the blend smooths the tail rather than beating the pace on
    the typical step (see `blend_weight` for the numbers). None when there is nothing to blend
    with, and then the pace stands alone exactly as it did before, so a fresh log cannot
    regress.
    """
    w = blend_weight() if weight is None else weight
    if not calls_mem or not pace_ms or w <= 0:
        return None
    calls = (calls_mem.get("classes") or {}).get(label_class(task)) or calls_mem.get("calls")
    rate = calls_mem.get("rate_ms")
    if not calls or not rate:
        return None
    predicted = int(calls) * int(rate)
    if predicted <= 0:
        return None
    blended = (predicted ** w) * (pace_ms ** (1.0 - w))
    return min(int(blended), EST_CAP_MS)


# In plain words: one remembered span, read the same way whether it was stored as a bare
# number (an older build's task log) or as a small record of the samples behind it.
def hist_med(entry) -> int | None:
    """The median inside one history entry: a bare int, or a dict with `med`."""
    if isinstance(entry, dict):
        val = entry.get("med")
    else:
        val = entry
    try:
        val = int(val)
    except (TypeError, ValueError):
        return None
    return val if val > 0 else None


# In plain words: the rate the estimates borrow from, for steps with no history of their
# own. The middle duration of the steps finished in THIS list; failing that, the middle of
# the remembered ones; and only with no past at all, a flat couple of minutes.
def step_pace_ms(times: dict, todos: list, now_ms: int, history: dict | None = None) -> int:
    """The pace the estimates ride on: the median duration of the steps already finished.

    Median, not mean: one step that ran away (a long build, a stuck probe) must not drag
    every other projection with it. With nothing finished in THIS list there is no
    distribution to speak of, so the remembered history takes over — the median of what
    every step in the log has taken before — and only a session with no past at all falls
    back to the default that keeps the numbers in the order of minutes.

    BOUNDED while the list is still young, and that bound was measured, not guessed: a median
    of ONE OR TWO finished steps is not a distribution, and it is where the estimates go
    badly wrong — one 2s step projected the whole list at `~2s` while the next step took
    1m53s (45x out), and a `~18m` pace came from a single fast step (22x out). Blending the
    pace toward history instead was tried and REJECTED: it made the typical case worse,
    because a list is homogeneous within itself and the log's other sessions are not.

    So the list's own pace still wins whenever it has `PACE_BOUND_SAMPLES` or more finished
    steps behind it; below that it is clipped to within `PACE_BOUND_FACTOR` of what this
    model's steps are remembered to cost, and never replaced by it.

    REFIT 2026-09-29 over the 161 real spans recovered from the CLI journals, leave-one-
    session-out, through this function. It replaces a 17-prediction table whose harness walked
    each session's steps by SPAN instead of by time, which is what made the bound look better
    than it is; the honest numbers are:

        config                     median    mean     p90     worst
        no bound at all             2.18x    3.80x   7.56x    33.9x
        4x, while n < 3 (ships)     2.18x    3.70x   7.56x    29.9x
        2x, while n < 3             2.41x    3.66x   7.46x    29.9x
        8x, while n < 3             2.18x    3.80x   7.56x    33.9x

    The median does not move at ANY factor — a tighter bound is worse on it (2.41x at 2x) and no
    bound at all is worse only in the tail. That much of the original reasoning survives. What
    does not is how much the bound is buying: it is consulted on FOUR steps of the whole replay
    (every other step has the same number either way), and on those four the no-bound reading
    wins three — the bound turns one 45x miss into an 18x one and makes three 1.2-2.8x steps
    about a tenth of a factor worse. So it trades one large save for three small losses: better
    on the mean and the worst case, worse on a bare step count, identical on the median and p90,
    and a bootstrap over sessions cannot separate the two readings (73% of resamples, on four
    steps). Four steps do not settle that, so the bound stays as a JUDGMENT CALL rather than a
    finding — and neither constant is worth refitting again until there are several hundred
    spans, because at 161 a median difference under about 0.15x is noise and screening a grid
    for something smaller is how noise gets shipped as a result.
    """
    spans = step_spans_ms(times, todos, now_ms)
    pace = _median_ms(spans)
    remembered = [
        min(m, EST_CAP_MS)
        for m in (hist_med(v) for v in (history or {}).values())
        if m
    ]
    reminded = _median_ms(remembered)
    if pace is None:
        return reminded or DEFAULT_PACE_MS
    if reminded and len(spans) < PACE_BOUND_SAMPLES:
        low = max(1, int(reminded / PACE_BOUND_FACTOR))
        high = int(reminded * PACE_BOUND_FACTOR)
        return min(max(pace, low), high)
    return pace


# In plain words: one step's estimate, in the order the evidence deserves — what this exact
# step took before, then what steps of the same SHAPE took, then the list's pace.
def estimate_for(
    times: dict, task: str, pace_ms: int, shapes: dict | None = None,
    calls_mem: dict | None = None,
) -> int:
    """One step's projection, from the best evidence available about it."""
    return task_estimate_ms(
        task, pace_ms, shapes, step_shape_bucket(times, task), calls_mem
    )


# In plain words: the ladder the estimate walks, written once so that no caller can drift
# from it. `None` for the value means "nothing better than the list's pace was available",
# and the source it returns is what the error report files the result under.
def pick_estimate(
    task: str, shapes: dict | None = None, bucket: str = "",
    pace_ms: int | None = None, calls_mem: dict | None = None,
) -> tuple[int | None, str]:
    """(value or None, source) — the size memory, then the label blend, then the pace.

    The size rung answers only for a step that has shown `SHAPE_MIN_BUCKET` worth of size, so
    `bucket` being non-empty is not enough to fire it; below that floor the blend or the pace
    carries the number (see the constant for why the floor exists at all).

    There is deliberately no `own wording` rung any more. It was the first rung for a long
    time and, measured 2026-09-29, it never once fired: replayed over the 258 steps recovered
    from the CLI journals, 0 of 170 ticked steps met their own wording in another session
    (172 of the 173 labels ever written have been seen exactly once). A rung that cannot
    answer is not a safety net; it is a branch that only hides the pace under it.

    What replaced it is the other direction: not "has this step run before", but "what kind
    of step is this" — which is the only question a row that has not STARTED can answer.
    """
    entry = sized_entry(shapes, bucket)
    if entry:
        seen = hist_med(entry)
        if seen:
            return min(seen, EST_CAP_MS), "shape"
    blended = pending_blend_ms(task, pace_ms or 0, calls_mem)
    if blended:
        return blended, "blend"
    return None, "pace"


def task_estimate_ms(
    task: str,
    pace_ms: int,
    shapes: dict | None = None,
    bucket: str = "",
    calls_mem: dict | None = None,
) -> int:
    """The estimate for one step: the size memory, else the label blend, else the pace.

    The log remembers every finished step by how big it was — the calls it made (see
    `shape_of`) — so a step already running, whose calls are being counted as it works, is
    projected from steps of the same size rather than from the average of whatever this list
    happens to contain. A size is only allowed to answer once `SHAPE_MIN_SAMPLES` remembered
    steps of that size stand behind it, so one coincidence never overrides the list's pace —
    and only once the step's own tally has passed `SHAPE_MIN_BUCKET`, because until then its
    bucket names a smaller step than the one it is going to be (see the constant).

    A waiting row has no calls yet and so no size: it is priced at the pace. That is the
    honest limit of this memory, and the reason `EST REM` — mostly waiting rows — moves
    little as the memory learns.
    """
    value, _source = pick_estimate(task, shapes, bucket, pace_ms, calls_mem)
    return value if value else pace_ms


# In plain words: how far apart the samples behind a number were. Two finished steps of
# 10 seconds and 10 minutes is not a pace of five minutes with a bit of noise on it — it is
# a list where the next step is genuinely unknowable to within a factor of 60, and saying
# `~5m` alone would be claiming a precision nobody has.
def _spread_ms(spans: list[int]) -> tuple[int, int] | None:
    """(low, high) of the samples behind a number — quartiles, or None with too few."""
    vals = sorted(s for s in (spans or []) if s and s > 0)
    if len(vals) < SPREAD_MIN_SAMPLES:
        return None
    mid = len(vals) // 2
    low = _median_ms(vals[:mid]) or vals[0]
    high = _median_ms(vals[mid + (len(vals) % 2):]) or vals[-1]
    return low, high


def fmt_range(lo: int, hi: int) -> str:
    """`1m–8m` — the low and high of the samples behind an estimate."""
    return f"{short_duration(lo) or '0s'}–{short_duration(hi) or '0s'}"


def is_wide(lo: int, hi: int) -> bool:
    """Is one number the wrong shape for this spread? 3x or more, measured either side."""
    return bool(lo and hi and hi >= lo * SPREAD_MIN_RATIO)


def fmt_estimate_spread(point: int, spread: tuple[int, int] | None) -> str:
    """`~3m`, or `~3m (1m–8m)` when the samples behind the 3m disagree by 3x or more.

    The point estimate is never thrown away — it is what the eye wants — and the range is
    only added where the spread is genuinely wide, so an ordinary list keeps reading `~3m`.
    """
    token = fmt_estimate(point)
    if not spread:
        return token
    lo, hi = spread
    return f"{token} ({fmt_range(lo, hi)})" if is_wide(lo, hi) else token


# In plain words: the spread behind the pace — this list's own finished steps when there are
# two or more, else the remembered ones. Also the honest bound on `EST REM`, which is a sum
# of projections: if a step is 10s-or-20m, so is the total.
def pace_spread_ms(
    times: dict, todos: list, now_ms: int, history: dict | None = None
) -> tuple[int, int] | None:
    """The spread the pace rides on, from the same samples that set it."""
    mine = step_spans_ms(times, todos, now_ms)
    if len(mine) >= SPREAD_MIN_SAMPLES:
        return _spread_ms(mine)
    remembered = [m for m in (hist_med(v) for v in (history or {}).values()) if m]
    return _spread_ms(remembered)


def entry_spread(entry) -> tuple[int, int] | None:
    """The `lo`/`hi` inside a history entry, when both are there."""
    if not isinstance(entry, dict):
        return None
    lo, hi = entry.get("lo"), entry.get("hi")
    try:
        lo, hi = int(lo), int(hi)
    except (TypeError, ValueError):
        return None
    return (lo, hi) if lo > 0 and hi > 0 else None


# In plain words: did the estimates get better? The task log already holds the pair needed
# to answer that — what a step was projected to take at the moment it closed, and what it
# actually took — so the error is computed from the records rather than kept in a tally that
# could drift away from them. Reported per SOURCE, because that is the only way to see
# whether the shape memory is earning its place on this machine's real work.
def forecast_error(
    tasks: dict,
    now_ms: int | None = None,
    model: str | None = None,
    days: float | None = None,
) -> dict:
    """{rung: {n, med, mean, worst}} — each rung scored on its FIRST-poll forecast.

    Rungs whose one vector was stamped too late to be a forecast (see `LEDGER_FRESH_MS`) are
    left out of every rung and counted under `late`, so a watcher restart mid-step cannot
    quietly improve the numbers it appears in.

    The honest scoreboard, and the reason the ledger exists. `estimate_error` scores the
    estimate the pane was showing when a step CLOSED, which for the size rung is a key built
    from calls the step had already made — so it is partly scored with the answer in hand and
    can flatter a rung that recognises rather than predicts. This scores the vector written
    on the first poll that saw the step running (see the `fc` stamp in `track_tasks`), when
    nothing about the step's size was known, and scores every rung on every step — which is
    what makes the numbers comparable to each other rather than to their own sample.
    """
    now_ms = now_ms or int(time.time() * 1000)
    cutoff = now_ms - int((days if days is not None else HISTORY_MAX_AGE_DAYS) * 86400 * 1000)
    out: dict[str, list[float]] = {}
    late = 0
    for key, rec in (tasks or {}).items():
        if not isinstance(rec, dict):
            continue
        started, stopped = rec.get("started_ms"), rec.get("done_ms")
        if not started or not stopped or stopped <= started or stopped < cutoff:
            continue
        if model and rec.get("model") and rec.get("model") != model:
            continue
        actual = stopped - started
        if actual < label_floor_ms():
            continue
        fc = rec.get("fc") or {}
        if not fc:
            continue
        # A vector whose own stamp says the step was already running when the watcher first
        # saw it (a restart mid-step) is not a forecast: it carries the step's clock inside
        # it. Scored as one it would flatter whichever rung reads elapsed, which is the exact
        # flattery the ledger exists to prevent, so it is left out and counted instead.
        if fc.get("late"):
            late += 1
            continue
        for rung in ("shape", "blend", "pace"):
            try:
                pred = int(fc.get(rung))
            except (TypeError, ValueError):
                continue
            if pred <= 0:
                continue
            out.setdefault(rung, []).append(max(pred / actual, actual / pred))
    report = {}
    for rung, fs in out.items():
        fs = sorted(fs)
        mid = len(fs) // 2
        med = fs[mid] if len(fs) % 2 else (fs[mid - 1] + fs[mid]) / 2
        report[rung] = {
            "n": len(fs),
            "med": round(med, 2),
            "mean": round(sum(fs) / len(fs), 2),
            "worst": round(max(fs), 1),
        }
    if late:
        report["late"] = {"n": late, "med": 0.0, "mean": 0.0, "worst": 0.0}
    return report


def refit_readiness(
    tasks: dict,
    now_ms: int | None = None,
    model: str | None = None,
    days: float | None = None,
) -> dict:
    """{scored, eligible, decided, spans_needed} — how close the log is to refitting itself.

    The score lines say what the rungs are doing; this says whether there is enough of it to
    re-choose the constants, and it is computed from the log alone, no replay needed:

    * `scored` — closed steps carrying a forecast: the sample a median would stand on. One
      step is not a distribution, so this is compared against `REFIT_MIN_SCORED`.
    * `eligible` — steps that started while their own LIST (the `lv` stamped on the record,
      or the session on older records) had fewer than `PACE_BOUND_SAMPLES` FINISHED steps,
      which is the only time the clip is consulted.
    * `decided` — steps where it actually MOVED the number. The pace stored in the step's own
      vector is the clipped one, and the median of its list's earlier finished spans — read
      from the log, the same way `step_spans_ms` reads it, floor and cap included — is what
      it would have used WITHOUT the clip. When those two differ, the clip decided this step.
      Measured over the 161-span replay, 157 spans were identical either way, so this count
      is the one that governs how long it takes to judge the clip at all.

    `spans_needed` extrapolates the observed decide rate to `REFIT_MIN_DECIDED`, and is None
    while fewer than five steps have been decided: a rate built on one or two is not a rate.
    """
    now_ms = now_ms or int(time.time() * 1000)
    cutoff = now_ms - int((days if days is not None else HISTORY_MAX_AGE_DAYS) * 86400 * 1000)
    rows: list[tuple[str, int, int, dict]] = []
    for key, rec in (tasks or {}).items():
        if not isinstance(rec, dict) or not isinstance(rec.get("fc"), dict):
            continue
        started, stopped = rec.get("started_ms"), rec.get("done_ms")
        if not started or not stopped or stopped <= started or started < cutoff:
            continue
        if model and (rec.get("model") or "") != model:
            continue
        # The LIST it was in when it was last seen, falling back to the session for records
        # written before `lv` existed — a coarser grouping, and the only one available then.
        group = f"{str(key).partition(chr(31))[0]}\x1f{rec.get('lv')}"
        rows.append((group, started, stopped - started, rec["fc"]))
    rows.sort(key=lambda r: r[1])
    by_list: dict[str, list[tuple[int, int]]] = {}
    scored = eligible = decided = 0
    for group, started, span, fc in rows:
        prior = [
            sp
            for st, sp in by_list.get(group, [])
            if st < started and sp >= label_floor_ms()
        ]
        by_list.setdefault(group, []).append((started, span))
        if span < label_floor_ms():
            continue
        scored += 1
        unclipped = _median_ms([min(sp, EST_CAP_MS) for sp in prior])
        if unclipped is None or len(prior) >= PACE_BOUND_SAMPLES:
            continue
        eligible += 1
        try:
            clipped = int(fc.get("pace") or 0)
        except (TypeError, ValueError):
            continue
        if clipped and abs(clipped - unclipped) > 500:
            decided += 1
    rate = (decided / scored) if scored else 0.0
    return {
        "scored": scored,
        "eligible": eligible,
        "decided": decided,
        "spans_needed": (
            int(REFIT_MIN_DECIDED / rate) if decided >= 5 and rate > 0 else None
        ),
    }


def estimate_error(
    tasks: dict,
    now_ms: int | None = None,
    model: str | None = None,
    days: float | None = None,
) -> dict:
    """{source: {n, med, mean, worst}} — how far closed steps missed, by where the number came from.

    Factor error (`max(pred/actual, actual/pred)`), so a step that took twice its estimate
    and one that took half of it count the same. Records written before this build stamped
    an estimate carry none, so the report fills in as steps close.
    """
    now_ms = now_ms or int(time.time() * 1000)
    cutoff = now_ms - int((days if days is not None else HISTORY_MAX_AGE_DAYS) * 86400 * 1000)
    factors: dict[str, list[float]] = {}
    for key, rec in (tasks or {}).items():
        if not isinstance(rec, dict):
            continue
        started, stopped = rec.get("started_ms"), rec.get("done_ms")
        if not started or not stopped or stopped <= started or stopped < cutoff:
            continue
        if model and rec.get("model") and rec.get("model") != model:
            continue
        try:
            pred = int(rec.get("est_ms"))
        except (TypeError, ValueError):
            continue
        if pred <= 0:
            continue
        actual = stopped - started
        if actual < label_floor_ms():
            continue  # a list flip is not a step, and scoring it only adds noise
        factors.setdefault(str(rec.get("est_src") or "pace"), []).append(
            max(pred / actual, actual / pred)
        )
    report = {}
    for src, fs in factors.items():
        fs = sorted(fs)
        mid = len(fs) // 2
        med = fs[mid] if len(fs) % 2 else (fs[mid - 1] + fs[mid]) / 2
        report[src] = {
            "n": len(fs),
            "med": round(med, 2),
            "mean": round(sum(fs) / len(fs), 2),
            "worst": round(max(fs), 1),
        }
    return report


def estimate_spread_ms(
    times: dict,
    task: str,
    shapes: dict | None = None,
    bucket: str = "",
) -> tuple[int, int] | None:
    """The spread belonging to whichever source supplied this step's number."""
    entry = sized_entry(shapes, bucket)
    if entry:
        return entry_spread(entry)
    return None


# In plain words: how much is left — each remaining step at its usual duration, less the
# time the step in progress has already spent. So the number falls as work lands rather
# than sitting still until the next list.
def remaining_estimate_ms(
    times: dict,
    todos: list,
    now_ms: int,
    pace_ms: int,
    shapes: dict | None = None,
    calls_mem: dict | None = None,
) -> int:
    """Projected time still to run, at the pace (and history) learned from finished steps.

    The step being worked on is credited for the time it has already spent, so a step
    four minutes into a three-minute pace has nothing left to project: `EST REM` shrinks
    as work lands instead of sitting flat until the next write. Each waiting step is
    projected from its own remembered duration where there is one.
    """
    idx = current_index(todos)
    total = 0
    for i, t in enumerate(todos or []):
        if t.get("completed"):
            continue
        est = estimate_for(times, t.get("task", ""), pace_ms, shapes, calls_mem)
        if i == idx:
            elapsed = live_elapsed(
                (times or {}).get(str(t.get("task", ""))) or {}, now_ms
            )
            if elapsed is not None:
                est = max(0, est - elapsed)
        total += est
    return total


def elapsed_total_ms(times: dict, todos: list, now_ms: int) -> int | None:
    """What the list has already spent, from the first clock it ever started.

    A clock is only ever started for a step seen *unfinished*, so the earliest
    `started_ms` on the list is when the work began — not `turn_started`, which
    moves every time the owner says something, and not the store's mtime, which
    moves on every poll. None means no step was ever seen running, and a zero
    there would read as "this list took no time" (see NO_TIMES_TICKED).
    """
    starts = [
        rec.get("started_ms")
        for rec in ((times or {}).get(str(t.get("task", ""))) or {} for t in todos or [])
        if rec.get("started_ms")
    ]
    if not starts:
        return None
    return max(0, now_ms - min(starts))


def total_estimate_ms(
    times: dict,
    todos: list,
    now_ms: int,
    pace_ms: int,
    shapes: dict | None = None,
    calls_mem: dict | None = None,
) -> int | None:
    """The overall time to reach the goal: what the list has spent plus what is left.

    `EST REM` answers "how much longer", which is the operational question; this
    answers "how long is this whole thing", which is the one asked when deciding
    whether to let a long job run. It is a projection, not a promise: the spent
    half is measured and the remaining half is projected at the same pace, so it
    moves as work lands instead of sitting still. None when the list was never
    seen running, because there is no start to measure a total from.
    """
    spent = elapsed_total_ms(times, todos, now_ms)
    if spent is None:
        return None
    return spent + remaining_estimate_ms(times, todos, now_ms, pace_ms, shapes, calls_mem)


def run_variance_ms(times: dict, todos: list, now_ms: int, pace_ms: int) -> int | None:
    """How far a run of finished steps ran from the pace, or None with too little data.

    Three or more measured spans: with one or two the pace IS those steps, so the number
    would be zero by construction or pure noise.
    """
    spans = step_spans_ms(times, todos, now_ms)
    if len(spans) < 3:
        return None
    return sum(spans) - pace_ms * len(spans)


def fmt_estimate(ms: int) -> str:
    """`~3m` — the coarse inline projection."""
    return "~" + (short_duration(max(0, int(ms))) or "0s")


def fmt_variance(ms: int) -> str:
    """`-1m20s` / `+40s` — finished work set against the pace."""
    return ("-" if ms < 0 else "+") + (
        short_duration(abs(int(ms)), seconds=True) or "0s"
    )


def fmt_eta(rem_ms: int, now_ms: int) -> str:
    """The wall clock the list projects to finish at, `18:06`."""
    return time.strftime("%H:%M", time.localtime((now_ms + max(0, int(rem_ms))) / 1000))


def task_key(session, task: str) -> str:
    """A task record belongs to a (session, task) pair.

    Keyed by task text alone, a session flip — a thread switch, a watcher restart
    that resolved another thread — reset the whole log, and every step of the list
    you were watching then read as completed in one instant, with no duration at
    all. Keyed this way a flip is invisible: each session keeps its own clocks, and
    going back to one restores them instead of restarting them.
    """
    return f"{session or ''}\x1f{task}"


# In plain words: a record changes once in a while, and the log has thousands of them. An
# observation used to rewrite every record to change one; now it appends a line, and the
# JSON the readers use is a memo of those lines with the offset it was folded to. The
# events are absolute (last write wins per key), so folding them again is harmless — which
# is what makes the window between the append and the rewrite cost a re-read, not a step.
def _task_event(row: dict) -> bytes:
    """One event, one line — the stream's only writer."""
    return (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _events_size() -> int:
    try:
        return os.path.getsize(events_path())
    except OSError:
        return 0


def _stamp_events_cursor(log: dict) -> None:
    """Record how much of the stream this memo has folded.

    Sound because the append that just happened was this process's own and was the last
    thing written: the file's size now is the end of what the memo holds.
    """
    try:
        st = os.stat(events_path())
    except OSError:
        return
    log["events"] = {"dev": st.st_dev, "ino": st.st_ino, "off": st.st_size}


def append_task_events(rows: list) -> int:
    """Append events to the stream, one JSON object per line; the new size back.

    One `O_APPEND` write per call, so a reader that saw the bytes before this call still
    sees them after it, and a crash in the middle of a line leaves a prefix rather than a
    file with a hole in it.
    """
    if not rows:
        return _events_size()
    blob = b"".join(_task_event(r) for r in rows)
    try:
        fd = os.open(events_path(), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    except OSError:
        return 0
    try:
        view = memoryview(blob)
        while view:
            wrote = os.write(fd, view)
            if wrote <= 0:
                break
            view = view[wrote:]
        try:
            os.fsync(fd)
        except OSError:
            pass
    except OSError:
        pass
    finally:
        os.close(fd)
    return _events_size()


def fold_task_events(view: dict) -> dict:
    """Bring a memo of the task log up to date with the stream.

    A record is set by `k` and removed by `drop`, so the fold is last-write-wins per key
    and running it twice says the same thing as running it once. Starting from the
    offset the memo carries and starting from the beginning therefore agree, which is what
    makes the window between an append and the rewrite harmless: the next reader folds the
    events the memo missed. A torn last line (a crash mid-append) is left for the rest of
    itself to arrive rather than guessed at.
    """
    stream = events_path()
    try:
        st = os.stat(stream)
    except OSError:
        return view
    cur = view.get("events") if isinstance(view.get("events"), dict) else {}
    start = 0
    if (cur.get("dev") == st.st_dev and cur.get("ino") == st.st_ino
            and 0 <= int(cur.get("off") or 0) <= st.st_size):
        start = int(cur["off"])
    try:
        with open(stream, "rb") as fh:
            fh.seek(start)
            blob = fh.read()
    except OSError:
        return view
    cut = blob.rfind(b"\n")
    if cut < 0:
        return view
    tasks = view.setdefault("tasks", {})
    for line in blob[: cut + 1].splitlines():
        try:
            row = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        if "k" in row and isinstance(row.get("r"), dict):
            tasks[row["k"]] = row["r"]
        elif isinstance(row.get("drop"), list):
            for key in row["drop"]:
                tasks.pop(key, None)
        if "session" in row:
            view["session"] = row["session"]
        if "pruned_ms" in row:
            view["pruned_ms"] = row["pruned_ms"]
        if row.get("v"):
            view["schema"] = max(int(view.get("schema") or 0), int(row["v"]))
    view["events"] = {"dev": st.st_dev, "ino": st.st_ino, "off": start + cut + 1}
    return view


def compact_task_events(log: dict) -> int:
    """Rewrite the stream as one event per record the log still holds.

    A stream appended to for a month is mostly events about records the prune has since
    dropped: this is the fold point, run by the prune and only by it. Written to a temp
    name and renamed, because a reader mid-fold must see the old stream or the new one.
    """
    rows = [{"v": TASKLOG_SCHEMA, "session": log.get("session")}]
    for key, rec in (log.get("tasks") or {}).items():
        rows.append({"v": TASKLOG_SCHEMA, "k": key, "r": rec})
    if log.get("pruned_ms"):
        rows.append({"v": TASKLOG_SCHEMA, "pruned_ms": log["pruned_ms"]})
    blob = b"".join(_task_event(r) for r in rows)
    stream = events_path()
    d = os.path.dirname(stream)
    os.makedirs(d, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".fbtodo.", suffix=".tmp", dir=d)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(blob)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, stream)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    st = os.stat(stream)
    log["events"] = {"dev": st.st_dev, "ino": st.st_ino, "off": len(blob)}
    return len(blob)


def load_tasklog() -> dict:
    log = read_json(TASKS_PATH, None)
    if not isinstance(log, dict) or "tasks" not in log:
        log = {"schema": TASKLOG_SCHEMA, "session": None, "tasks": {}}
    if log.get("schema", 1) < TASKLOG_SCHEMA and log.get("tasks"):
        # flat task-text keys from before the (session, task) change: re-key them
        # under the session they were recorded for, so an upgrade does not throw
        # away the clocks of the list being watched at the time
        session = log.get("session")
        log["tasks"] = {task_key(session, k): v for k, v in log["tasks"].items()}
        log["schema"] = TASKLOG_SCHEMA
    return fold_task_events(log)


def current_index(todos: list) -> int | None:
    """The step being worked on: the first one not ticked off.

    The list is sequential, which is what makes one clock per task meaningful — a
    task is running only while it is this one.
    """
    for i, t in enumerate(todos or []):
        if not t.get("completed"):
            return i
    return None


# In plain words: a number written down is not the same as work happening. A step with a
# recorded start and no recorded finish is, as far as this record knows, still running —
# and that is all this answers. Whether it is running *now* is a different question; the
# next function asks it.
def live_clock(rec: dict | None) -> bool:
    """Is this step's clock counting, as the record stands?

    A number in the record is not the same as work in progress. A span that has been
    closed — by the step being ticked, or by the turn ending with it still current — is a
    *measurement*, and reading it as "working" is what left a finished turn counting up in
    the pane for as long as the list sat there: the strip said WORKING with a number that
    grew past the estimate, and nothing was being worked on at all.

    This is the record's own answer, so it is deliberately blind to the turn: a file the
    watcher has not rewritten yet (a stale state, or a stopped watcher) still says
    "counting". That is why the callers also ask the state — see `step_is_running`.
    """
    if not rec:
        return False
    return rec.get("started_ms") is not None and rec.get("done_ms") is None


# In plain words: the pane's real question — is anything being worked on at this moment?
# Two sources must agree. The record says a clock is counting; the transcript says whether
# the agent has finished its turn. Once the turn has ended nothing is in progress, whatever
# a number left behind by the last write may suggest.
def step_is_running(state: dict, rec: dict | None) -> bool:
    """The question the pane actually asks: is a step in progress *right now*?

    Two things have to agree, and the state is the one that settles it. The record says a
    clock is counting; the journal says whether the agent has finished the turn. When the
    turn has ended, nothing in the list is in progress — and since the pane is a separate
    process reading a file, a record the watcher has not yet closed must not be able to
    keep saying WORKING on its own.
    """
    if state.get("turn_ended"):
        return False
    return live_clock(rec)


# In plain words: the clock is worked out from the recorded start and the current time,
# rather than copied from the last update, so the number keeps moving between the watcher's
# writes instead of standing still and then jumping.
def live_elapsed(rec: dict, now_ms: int) -> int | None:
    """The clock as of *now*, not as of the watcher's last write.

    The state file carries `elapsed_ms` from the poll that produced it, so the pane
    re-derives the running step's number from its recorded start and lets its own
    tick move it. None means "no clock": the task was never seen running, and a
    zero there would read as "this took no time".
    """
    started = rec.get("started_ms")
    if not started:
        return rec.get("elapsed_ms")
    return max(0, (rec.get("done_ms") or now_ms) - started)


def has_running_clock(state: dict, now_ms: int | None = None) -> bool:
    """True while a step's clock is counting up.

    A counting number is the difference between "alive and working on step 4" and
    "stuck"; the pane redraws on a 1s tick while this is true (instead of the 5s
    idle tick) so it visibly moves rather than jumping in 5-second steps. The pane
    sleeps in short wakes, not until its next poll, so this holds for the NAS pane
    too — where a poll is an ssh round trip.
    """
    todos = state.get("todos") or []
    idx = current_index(todos)
    if idx is None:
        return False
    rec = (state.get("task_times") or {}).get(str(todos[idx].get("task", ""))) or {}
    return step_is_running(state, rec)


def track_tasks(state: dict, now_ms: int | None = None, persist: bool = True) -> dict:
    """Per-task elapsed time, measured by observation, counted one step at a time.

    Freebuff stores no per-task timestamps — a snapshot is text plus a completed
    flag — so a clock is built from what the watcher sees:

    * it starts when the task becomes the step being worked on, not when the list
      is written: otherwise every task bills the time spent on the ones before it,
      and the last step of a six-step list shows the whole turn's duration;
    * it stops the first time that task is seen completed, and keeps that number;
    * it stops when the TURN ends with the step still current — the work is over, so a
      clock left running bills the gap and the pane counts a step nobody is on. The span
      so far is kept, and the clock restarts if the agent comes back to that step;
    * a task never seen running (already ticked off at first sight, or fbtodo
      attached late) gets no number at all rather than a zero;
    * a re-opened task restarts, since it is being worked on again.

    Elapsed time is a floor: a transition is timestamped by the poll that noticed
    it, so a step is over-counted by at most one polling interval. Reopening a
    completed task restarts its clock. Records are kept per session, so a thread
    flip or a list that momentarily reads as empty cannot wipe them.
    """
    now_ms = now_ms or int(time.time() * 1000)
    log = load_tasklog()
    session = state.get("session")
    prior_session = log.get("session")
    log["session"] = session
    tasks = log.setdefault("tasks", {})
    todos = state.get("todos") or []
    # What this poll may touch, as text, before anything is mutated: the records of the
    # steps on this list. The write below then appends the events that differ instead of
    # rewriting the records that do not, which is most of them — the log is a history and a
    # list is a handful of steps.
    touched = [task_key(session, str(t.get("task", ""))) for t in todos]
    before_recs = {key: json.dumps(tasks.get(key), sort_keys=True) for key in touched}
    running = current_index(todos)
    # The agent's own answer to "is anything being worked on right now", from the
    # journal's end-of-turn record. Everything below hangs off it: a turn that has ended
    # is waiting for you, so no step of it is in progress — and the strip's WORKING/IDLE
    # question has to be answered the same way, or it says the opposite of the truth.
    ended = bool(state.get("turn_ended"))
    times: dict[str, dict] = {}
    seen: set[str] = set()
    prefix = task_key(session, "")
    # The calls this TURN has made, and which of its steps they belong to (see below).
    turn = state.get("turn") or {}
    turn_ms = int(turn.get("start_ms") or 0)
    turn_verbs = dict(turn.get("verbs") or {})

    for i, t in enumerate(todos):
        label = str(t.get("task", ""))
        key = task_key(session, label)
        seen.add(key)
        rec = tasks.get(key)
        if not isinstance(rec, dict):
            rec = {"started_ms": None, "done_ms": None}
            tasks[key] = rec
        # The model the step was worked on by, so its span can be compared with like
        # spans later (see task_history_from_log). Stamped on every sighting, so a
        # record written before this field existed picks it up on the next poll.
        if state.get("model"):
            rec["model"] = state["model"]
        # Which LIST this step was last seen in. The pace a step was given came from the
        # steps finished before it IN ITS OWN LIST, so that is what the clip's own readiness
        # count has to group by — a session can hold many lists, and grouping by session
        # would count steps from earlier turns as if this step had inherited their pace.
        if state.get("list_version") is not None:
            rec["lv"] = state["list_version"]
        # What this step has DONE, as opposed to what it was called: the calls this turn
        # has made, less what the steps BEFORE it in the same turn were credited with.
        # Order decides who did what — no timestamps needed, and no call is counted twice.
        # Only the step in flight is credited: a waiting step has made no calls, and a
        # finished one keeps the shape it earned. Nothing is credited once the turn ends,
        # so the last step's signature is whatever it had when the work stopped.
        if i == running and not ended and turn_verbs and turn_ms:
            credited: dict[str, int] = {}
            for other, other_rec in tasks.items():
                if other == key or not isinstance(other_rec, dict):
                    continue
                if not (other.startswith(prefix) and prefix):
                    continue
                if int(other_rec.get("turn_ms") or 0) != turn_ms:
                    continue
                for verb, n in (other_rec.get("shape") or {}).items():
                    credited[verb] = credited.get(verb, 0) + int(n or 0)
            left = {v: n - credited.get(v, 0) for v, n in turn_verbs.items()}
            left = {v: n for v, n in left.items() if n > 0}
            if left:
                rec["shape"] = left
            rec["turn_ms"] = turn_ms
        if t.get("completed"):
            if rec.get("started_ms"):
                rec["done_ms"] = rec.get("done_ms") or now_ms
            else:
                rec["done_ms"] = None  # never seen running: no duration to show
        elif rec.get("done_ms") and not ended:
            rec["started_ms"] = now_ms  # re-opened: the clock restarts
            rec["done_ms"] = None
        if not t.get("completed"):
            if i != running:
                # only the step being worked on has a running clock; one that is waiting
                # its turn (or was left behind because an earlier step was unticked) has
                # none, so its number can never include someone else's time
                rec["started_ms"] = None
            elif ended:
                # The turn ended with this step still current: close the clock ONCE and
                # keep the span. Gated on `ended` rather than stamped every pass, or the
                # next poll would re-open it (the re-opened branch above) and the pair
                # would flap, and the number would grow again by exactly the idle gap it
                # had just been stopped for.
                if rec.get("started_ms") and not rec.get("done_ms"):
                    rec["done_ms"] = now_ms
            else:
                rec["started_ms"] = rec.get("started_ms") or now_ms
        started = rec.get("started_ms")
        times[label] = {
            "started_ms": started,
            "done_ms": rec.get("done_ms") if started else None,
            "elapsed_ms": None
            if not started
            else (rec.get("done_ms") or now_ms) - started,
            # carried out to the renderers so a running step can be projected from the kind
            # of work it is visibly doing, without a second pass over the task log
            "shape": rec.get("shape") or {},
        }

    # Only this session's tasks, and never on a snapshot that reads as empty: a momentary
    # no-list poll means "nothing to look at", not "those tasks are gone".
    # FINISHED records are then deliberately spared. A record with a span is evidence — it is
    # what `estimate_error` and `forecast_error` score, and what the pace, size and call
    # memories learn from — and this session's key is (session, label), so a step that was
    # reworded becomes another sample rather than a duplicate. Dropping them made the two
    # score lines self-erasing: measured 2026-09-29, a rewritten list deleted the very step
    # whose forecast was the only scored row in the log, so `fbtodo status` could never show
    # more than the current list and "is this getting better?" was unanswerable live. What is
    # still dropped is a record that never finished (no `done_ms`): its `started_ms` would
    # keep a clock running for a step that is no longer on any list.
    dropped: list = []
    if seen:
        prefix = task_key(session, "")
        dropped = [
            k
            for k, rec in tasks.items()
            if k.startswith(prefix)
            and k not in seen
            and not (isinstance(rec, dict) and rec.get("done_ms"))
        ]
        for gone in dropped:
            tasks.pop(gone, None)

    # Per-task memory, kept ACROSS sessions: every finished step in the log contributes its
    # measured span under its own text, so a later list projects a step from what it took
    # last time instead of from the average of an unrelated list. Age-gated and capped
    # inside, so a stale wording falls out rather than skewing today's projections.
    state["task_history"] = task_history_from_log(tasks, now_ms, state.get("model"))
    # ...and the same memory keyed by what each step DID. This is the half that can improve
    # with use: wordings never repeat (172 of 173 seen once) while shapes do, so samples
    # accumulate in a bucket instead of scattering one per step name.
    state["task_shapes"] = shape_history_from_log(tasks, now_ms, state.get("model"))
    # ...and the one memory a WAITING step can use, since it has no calls to size it by: the
    # calls a step of its KIND makes, and the seconds each of those calls costs (see
    # call_memory_from_log). This is what prices a pending row, blended with the pace.
    state["task_calls"] = call_memory_from_log(tasks, now_ms, state.get("model"))
    # ...and how close the log is to being able to re-choose these constants, so the pane's
    # REFIT row and `fbtodo status` report the same count from the same function.
    state["refit"] = refit_readiness(tasks, now_ms, state.get("model"))
    # What the pane is projecting for the step in flight at this moment, stamped onto its
    # record. The moment that step closes, its estimate and its actual span are a matched
    # pair, and `estimate_error` reads the pair straight back out of the log — which is the
    # only way "are these numbers getting better?" can be answered with evidence rather than
    # with faith. Written before the persist below, so the stamp is what lands on disk.
    idx = current_index(todos)
    if idx is not None and not ended:
        label = str(todos[idx].get("task", ""))
        rec = tasks.get(task_key(session, label))
        if isinstance(rec, dict) and rec.get("started_ms"):
            pace = step_pace_ms(times, todos, now_ms, state["task_history"])
            bucket = step_shape_bucket(times, label)
            value, src = pick_estimate(
                label,
                state["task_shapes"],
                bucket,
                pace,
                state.get("task_calls"),
            )
            rec["est_ms"] = value or pace
            rec["est_src"] = src
            # The forecast LEDGER, written once, on the first poll that sees this step
            # running — the moment at which nothing about its size is known yet. `est_ms`
            # above is restamped every poll and is therefore scored with the answer partly
            # in hand (the shape rung's key is the calls the step has ALREADY made by the
            # time it closes), so it cannot say whether the shape rung predicts or merely
            # recognises. This vector can: every rung is recorded as it stood before the
            # work started, and `forecast_error` scores them all on every step.
            if "fc" not in rec:
                fc = {"at": now_ms, "v": VERSION, "model": state.get("model") or ""}
                late = now_ms - int(rec.get("started_ms") or now_ms)
                if late > LEDGER_FRESH_MS:
                    fc["late"] = late  # seen first at this many ms in — not a forecast
                entry = sized_entry(state["task_shapes"], bucket)
                if entry:
                    seen = hist_med(entry)
                    if seen:
                        fc["shape"] = seen
                blended = pending_blend_ms(label, pace, state.get("task_calls"))
                if blended:
                    fc["blend"] = blended
                fc["pace"] = pace
                fc["pick"] = src
                rec["fc"] = fc
    if persist:
        rows = []
        if log.get("session") != prior_session:
            rows.append({"v": TASKLOG_SCHEMA, "session": log.get("session")})
        for key in touched:
            if json.dumps(tasks.get(key), sort_keys=True) != before_recs.get(key):
                rows.append({"v": TASKLOG_SCHEMA, "k": key, "r": tasks.get(key)})
        if dropped:
            rows.append({"v": TASKLOG_SCHEMA, "drop": sorted(dropped)})
        if rows:
            try:
                append_task_events(rows)
            except OSError:
                pass
            # The memo carries the offset it was folded to. Stamped after the append and
            # never before it: an event that landed without a memo is folded again next
            # time, which costs a read, where the other order would lose the record.
            _stamp_events_cursor(log)
            try:
                atomic_write_json(TASKS_PATH, log)
            except OSError:
                pass
            prune_scratch(now_ms=now_ms)  # throttled internally; keeps the record count capped
    state["task_times"] = times
    return state


def _window_anchor(cur_index: int | None, count: int) -> int:
    """Which step the elided window keeps on screen when the list is longer than the pane.

    The step being worked on — and, with none in progress because the list is finished,
    the LAST one. That puts the collapsed run at the TOP: the steps that fall out are the
    earlier, completed ones, and their `┊ N earlier steps completed` marker sits above the
    visible steps in list order. Anchoring a finished list at its first step collapsed the
    tail instead, which put a completed marker below the steps it stood for.
    """
    if cur_index is not None:
        return cur_index
    return max(0, count - 1)


# In plain words: "there is no list, and here is why" — one sentence, in the words the pane
# shows. Four situations produce no list at all: a session that has not written one yet, a
# session that changed (so the old list was dropped rather than left up as if it were
# current), a turn that began after the previous list was finished (the same misreading one
# turn earlier — 100% of work that is already over), and a remote session whose pane is open
# before the session is. Both renderers and `fbtodo status` ask this one function, so the four
# can never disagree about it.
def no_list_reason(state: dict) -> str:
    """Why there is no list to draw, in the pane's own words."""
    if state.get("backend") == "nas" and not state.get("instance_alive"):
        return "waiting for a NAS freebuff session (run `fb` there)"
    if state.get("cleared_turn"):
        return "last turn's list is done — waiting for this turn's list"
    if state.get("cleared"):
        return "new session — old list dropped, waiting for a new one"
    return "no write_todos call yet in this session"


# In plain words: "is the list behind the work?" — usually this cannot be answered, and it is
# worth being precise about why. A step takes as long as it takes and the list is not supposed
# to change while one runs, so the transcript moving on is NORMAL, not evidence. What is
# evidence is a list with nothing left on it — every step ticked — inside a session that is
# still writing: then the agent is doing work the list does not describe at all. Only that case
# is flagged. The ones that cannot be proved are left to the step's own clock (`[STUCK?]`, past
# twice its estimate) and to `now`/`NUDGE` when a request is waiting on the list.
def list_behind(state: dict, now_ms: int) -> bool:
    """A finished list, in a session that has kept writing past it."""
    todos = state.get("todos") or []
    written = int(state.get("ts") or state.get("source_updated_ms") or 0)
    moved = int(state.get("store_mtime_ms") or 0)
    if not todos or written <= 0 or moved <= 0:
        return False
    if any(not t.get("completed") for t in todos):
        return False          # work left on the list: the step's own clock is the record
    if state.get("turn_ended"):
        return False          # waiting for you, not working past it
    return moved - written >= LIST_BEHIND_MS


# In plain words: a pane with no list should not just shrug at you. This lists the tools the
# session has actually called — "called so far: run_terminal_command 20, skill 3" — which turns
# "no list yet" from a mystery into a diagnosis: it is working, and it is not planning out loud.
def tools_note(tools: dict, limit: int = 3) -> str:
    """`called so far: run_terminal_command 20, skill 3 +2 more` — what the session has
    actually called. A list-less pane that names the tools is diagnosing; one that only
    says "no write_todos call yet" is shrugging."""
    items = sorted(
        ((n, c) for n, c in (tools or {}).items() if n != "write_todos" and c),
        key=lambda nc: (-nc[1], nc[0]),
    )
    if not items:
        return ""
    head = ", ".join(f"{n} {c}" for n, c in items[:limit])
    more = len(items) - limit
    return f"called so far: {head}" + (f" +{more} more" if more > 0 else "")


# In plain words: the second basket. `write_todos` is the only place in the transcript that
# holds a PLAN (measured: no other tool, and no other field, carries one), so a model that
# skips the call leaves this pane with nothing to draw. What the transcript does hold is
# every call the session did make — the file it edited, the command it ran — and that is a
# fact, not a plan. So when there is no list, the pane says what the session has actually
# DONE, newest first, and never dresses it up as a list of steps: no progress bar over it,
# no estimate, no ticks.
# In plain words: what a TURN can honestly say, as opposed to what a list would say. A plan
# supplies a denominator — seven steps — and nothing in these stores has one, so a bar, a
# percentage or an estimate would be a guess by construction, and this pane does not guess.
# What the store does have is a boundary and a numerator: the request that opened the turn
# (the journal logs it on its own record), how long ago that was, how many model iterations
# have run since, and what they have done. That is status, not progress, and the two are
# deliberately separate things.
def turn_note(state: dict, now_ms: int) -> str:
    """`turn 11m · 9 iterations · 3 files edited · 24 calls`, or "" with no turn to name."""
    turn = state.get("turn")
    if not isinstance(turn, dict) or not turn.get("start_ms"):
        return ""
    # Past the scan's cap the counts are lower bounds, and a `+` says so rather than leaving
    # a number that reads as exact.
    plus = "+" if turn.get("truncated") else ""
    bits = [f"turn {short_duration(max(0, now_ms - int(turn['start_ms']))) or '0s'}"]
    iters = int(turn.get("iterations") or 0)
    if iters:
        bits.append(f"{iters}{plus} iteration" + ("" if iters == 1 else "s"))
    files = turn.get("files") or []
    if files:
        bits.append(f"{len(files)}{plus} file" + ("" if len(files) == 1 else "s") + " edited")
    calls = sum(int(n or 0) for n in (turn.get("verbs") or {}).values())
    if calls:
        bits.append(f"{calls}{plus} call" + ("" if calls == 1 else "s"))
    note = " · ".join(bits)
    # The slot `[STUCK?]` takes when a step's clock has a list to sit on. A turn with no list
    # has no clock, so a transcript that has gone quiet is what is left to report — and it
    # says exactly that, because a long command writes no records either.
    quiet_ms = now_ms - int(state.get("store_mtime_ms") or 0)
    if not state.get("turn_ended") and quiet_ms >= QUIET_MS:
        note += "  ·  quiet " + (short_duration(quiet_ms) or "0m")
    return note


def observed_rows(state: dict, limit: int = ACTION_ROWS, now_ms: int | None = None) -> list:
    """`edited fbtodo · 2m ago` — the newest calls, for a session that wrote no list.

    Empty for a state with no calls recorded in it (a fixture, a brand-new session, or a
    build that did not carry them), so the renderers that draw this feed fall back to the
    line they drew before rather than drawing nothing.
    """
    acts = state.get("observed")
    if not isinstance(acts, list):
        return []
    out = []
    for act in acts[:limit]:
        if not isinstance(act, dict):
            continue
        what = " ".join(str(act.get("what") or "").split())
        verb = " ".join(str(act.get("verb") or "did").split()) or "did"
        line = f"{verb} {what}".strip()
        age = fmt_age(act.get("ts_ms"), now_ms)
        out.append(f"{line}  ·  {age}" if age != "?" else line)
    return out


# In plain words: every duration in the pane is measured by watching, never read from the
# agent. A step gets a clock only if fbtodo saw it unfinished at least once. So a list that
# turns up already finished — or one fbtodo attached to after the work had started — has no
# numbers at all, and the pane says which of those happened rather than showing a clock that
# looks broken.
#
# Every per-step number the pane shows — the row's clock, the list's pace, `EST REM`,
# `ETA` — is measured by WATCHING the list change from one poll to the next, and a step
# only gets a clock if it was seen *unfinished* at some point. So a list that arrives with
# every step already ticked — an agent that wrote it once, at the end — has no numbers to
# show, and says so rather than looking like a broken clock. (Measured 2026-09-26 on a NAS
# session: one `write_todos` call, five todos, all completed, so nothing was ever running
# to time. The build was identical to the Mac's, version 0.0.199; this was the agent, not
# the store.)
NO_TIMES_TICKED = "no per-step times · the list arrived with every step already ticked"
NO_TIMES_UNSEEN = "no per-step times · no step was ever seen running"


# In plain words: why are the numbers missing? There are two honest answers, and the pane
# names the one that applies; an empty answer means there is nothing to explain.
def no_times_note(state: dict) -> str:
    """Why the per-step numbers are missing, when they are — or None-ish when they are not."""
    todos = state.get("todos") or []
    if not todos:
        return ""
    times = state.get("task_times") or {}
    if any((rec or {}).get("started_ms") for rec in times.values()):
        return ""   # a step does carry a clock: there is nothing to explain
    if all(t.get("completed") for t in todos):
        return NO_TIMES_TICKED
    return NO_TIMES_UNSEEN


# In plain words: the renderer a script gets — plain text, one line per thing, no frame and
# no colour unless asked for. It is deliberately the boring one: other programs read it, so it
# stays stable while the framed pane is free to be pretty.
def _render_plain(
    state: dict,
    color: bool,
    watching: int | None = None,
    width: int = 80,
    now_ms: int | None = None,
    idle_s: float | None = None,
    stale_after_s: float = 0.0,
    goal_lines: int = 3,
    height: int | None = None,
) -> str:
    c = paint(color)
    dim, bold, green, yellow, red = "2", "1", "32", "33", "31"
    now_ms = now_ms or int(time.time() * 1000)
    compact = width < 60
    backend = state.get("backend") or "?"
    session = str(state.get("session") or "")
    lines: list[str] = []

    if compact:
        # Keep the identity, backend, and session on one identity line in a narrow
        # strip.  A separate session row used to consume scarce height before any
        # step had appeared.
        identity = f"fbtodo  {backend}"
        if session:
            identity += "  " + _clip(session, max(1, width - len(identity) - 2))
        lines.append(c(bold, "fbtodo") + "  " + c(dim, identity[8:]))
    else:
        context = f"{backend} · {session}" if session else backend
        lines.append(
            c(bold, "Freebuff todos")
            + "  "
            + c(dim, _clip(context, max(1, width - len("Freebuff todos  "))))
        )

    if state.get("error"):
        lines += [c(red, seg) for seg in _wrap(str(state["error"]), width, 2)]
        return "\n".join(lines)

    if watching:
        # What is being followed differs by backend: a local pid here, or the remote
        # session for the NAS store (where there is no local pid at all).
        followed = (
            "the NAS session"
            if backend == "nas"
            else f"freebuff pid {state.get('instance_pid')}"
        )
        if compact:
            # Short form on a narrow pane: the long one wraps mid-word at 22 columns,
            # which is the whole reason the layout is width-aware.
            short = "the NAS session" if backend == "nas" else f"pid {state.get('instance_pid')}"
            lines += [c(dim, seg) for seg in _wrap(f"watch {watching} → {short}", width, 2)]
        else:
            lines.append(c(dim, f"watcher: pid {watching} following {followed}"))
    if state.get("title"):
        lines += [c(dim, seg) for seg in _wrap(str(state["title"])[:90], width, 2)]

    # The goal heads the list: it is the request the list was written for, so the tasks
    # below it read as steps towards something. `now` appears only when a newer request
    # has arrived since — the case where the list is old but the session is not idle,
    # instead of leaving the pane to look frozen.
    goal = str(state.get("goal") or "")
    now_txt = str(state.get("now") or "")
    if compact:
        # A strip this narrow has ~8 rows for the steps themselves, so the heading gets at
        # most two of them. It is not clipped for length: AGENTS.md asks for a rewrite
        # short enough to fit whole (GOAL_MAX_CHARS), and a `Goal:` line that overruns is
        # reported by `status` rather than quietly losing its end here.
        goal_lines = min(goal_lines, 2)
    if goal_lines > 0:
        if not goal and state.get("todos"):
            # Said, not silently replaced by a quote of the request: the heading is the
            # agent's to write, and a missing one is a rule that was skipped. Wrapped like
            # the heading itself — it is 24 columns, so on a 22-column pane a raw append
            # ran past the edge (caught by the width sweep, on a list with no heading).
            segs = textwrap.wrap(
                "— none stated",
                width=max(16, width),
                initial_indent="big goal · ",
                subsequent_indent="           ",
                max_lines=goal_lines,
                placeholder=" …",
            )
            lines += [c(dim, seg) for seg in segs]
        if goal:
            # `initial_indent` is counted INSIDE `width`, so the full pane width goes
            # here: subtracting the label as well wrapped headings 11 columns early.
            segs = textwrap.wrap(
                goal,
                width=max(16, width),
                initial_indent="big goal · ",
                subsequent_indent="           ",
                max_lines=goal_lines,
                placeholder=" …",
            )
            lines.append(c(bold, segs[0]))
            lines += [c(dim, seg) for seg in segs[1:]]
        if now_txt:
            segs = textwrap.wrap(
                now_txt,
                width=max(16, width),
                initial_indent="now · ",
                subsequent_indent="      ",
                max_lines=2,
                placeholder=" …",
            )
            lines += [c(yellow, seg) for seg in segs]
        nudge = str(state.get("nudge") or "")
        if nudge and nudge != now_txt:
            # "continue" is not a new task, but it does ask for a fresh list: AGENTS.md
            # says re-write it before carrying on. Shown only while the list has NOT been
            # rewritten since (a newer write_todos clears `nudge`), so it is the pane
            # reporting a skipped rule rather than a permanent nag.
            segs = textwrap.wrap(
                "rewrite the list, then continue",
                width=max(16, width),
                initial_indent=f"nudge · {nudge} — ",
                subsequent_indent="        ",
                max_lines=2,
                placeholder=" …",
            )
            lines += [c(yellow, seg) for seg in segs]

    # In plain words: a session with no list is the one case where the pane has nothing to
    # draw, so it says why in words instead — and the words differ by cause rather than being
    # one catch-all shrug.
    todos = state.get("todos") or []
    if not todos:
        if state.get("first_prompt") and not goal:
            lines += [c(dim, seg) for seg in _wrap(str(state["first_prompt"]), width, 2)]
        why = no_list_reason(state)
        lines += [c(dim, seg) for seg in _wrap(why, width, 2)]
        # The turn first — a boundary and a numerator, never a denominator — and the second
        # basket after it: what the session has done. Both are absent from a state that
        # carries neither, so this snapshot is unchanged there.
        turn_line = turn_note(state, now_ms)
        if turn_line:
            lines += [c(dim, s) for s in _wrap("  " + turn_line, width, 1)]
        for seg in observed_rows(state, 1 if turn_line else 2, now_ms):
            lines += [c(dim, s) for s in _wrap("  " + seg, width, 1)]
        lines.append(c(dim, f"  live {time.strftime('%H:%M:%S', time.localtime(now_ms / 1000))}"))
        return "\n".join(lines)
    # No note here, deliberately: a plain snapshot is parsed by scripts, and the suite
    # holds its line count still whatever the timings are (fbtodo-selfcheck: "a snapshot's
    # line count must not move because an estimate exists"). Both notes below are the
    # pane's to say — it is the thing a person reads, and its frame is height-managed.

    times = state.get("task_times") or {}
    history = state.get("task_history") or {}
    shapes = state.get("task_shapes") or {}
    calls_mem = state.get("task_calls") or {}
    blocks: list[list[str]] = []
    cur_index = current_index(todos)
    pace_ms = step_pace_ms(times, todos, now_ms, history)
    for i, t in enumerate(todos):
        task = str(t.get("task", ""))
        done_flag = bool(t.get("completed"))
        active = i == cur_index and not done_flag
        rec = times.get(task) or {}
        # '· 3m…' while the step being worked on counts up, '· 2m' once it is done and
        # frozen, nothing at all for a task that was never seen running (a zero there
        # would claim it took no time). Re-derived from the recorded start/stop against
        # `now_ms`, so the number moves on the pane's own tick instead of only when the
        # watcher writes — the watcher's poll is 2.5s at best, and 60s when idle.
        elapsed = live_elapsed(rec, now_ms)
        suffix = ""
        if elapsed is not None:
            suffix = " · " + (short_duration(elapsed, seconds=True) or "0s")
            if not rec.get("done_ms"):
                suffix += "…"
        # Keep the duration attached when it fits; otherwise give it its own quiet
        # continuation line.  Reserving suffix width on every wrapped line made an
        # ordinary task unnecessarily short and was the source of the old mid-word
        # looking breaks.
        segs = _wrap(task, width, 6)
        if suffix:
            if len(segs[-1]) + len(suffix) <= width - 6:
                segs[-1] += suffix
            else:
                segs.append(suffix)
        # The projection rides the SAME line, never one of its own: a piped `snap` is
        # parsed by scripts, so its line count must not move because an estimate exists.
        # When the token does not fit, it is simply dropped — the duration above, when
        # there is one, has already had its say.
        if not done_flag:
            est_ms = estimate_for(times, task, pace_ms, shapes, calls_mem)
            spread = estimate_spread_ms(
                times, task, shapes, step_shape_bucket(times, task)
            ) or pace_spread_ms(times, todos, now_ms, history)
            # The range is offered FIRST and the plain number second: a row with room says
            # `~2m (10s–20m)`, and a row without it keeps the `~2m` it would have had, so a
            # narrow pane never trades its estimate away for detail it cannot show.
            for cand in (fmt_estimate_spread(est_ms, spread), fmt_estimate(est_ms)):
                token = (" · " + cand) if elapsed is None else (" / " + cand)
                if len(segs[-1]) + len(token) <= width - 6:
                    segs[-1] += token
                    break
        block = []
        for j, seg in enumerate(segs):
            if j == 0:
                mark = c(green, "[x]") if done_flag else c(yellow if active else dim, "[ ]")
                block.append(f"  {mark} " + (c(dim, seg) if done_flag else seg))
            else:
                block.append(" " * 6 + (c(dim, seg) if done_flag else seg))
        if height and len(block) > TASK_MAX_LINES:
            # In the pane only: one verbose step must not become the whole pane, and the
            # full text is always in `fbtodo json` (and in any piped `snap`).
            block = block[:TASK_MAX_LINES]
            block[-1] += c(dim, " …")
        blocks.append(block)

    # The footer is built first so the list can be given exactly the room that is left
    # over. A pane has a fixed height: without this, a six-step list pushed the goal and
    # the progress bar clean off the top of a 16-row strip.
    # counted from the list being drawn, as in the framed pane: the state's own tally is
    # the same number while this build wrote it, and a stale one must never let the bar
    # contradict the steps printed right above it
    total = len(todos)
    done = sum(1 for t in todos if t.get("completed"))
    # the bar shares its line with "  [" + "]" + " n/m done"; size it to fit
    label = f"{done}/{total} done" if width >= 40 else f"{done}/{total}"
    bar_w = max(6, min(20, width - (len(label) + 6)))
    filled = int(bar_w * done / total) if total else 0
    bar = c(green, "#" * filled) + c(dim, "-" * (bar_w - filled))
    meta = []
    current = todos[cur_index] if cur_index is not None else None
    cur_rec = times.get(str(current.get("task", ""))) if current else None
    # the same liveness rule as the framed pane: a closed clock is a measurement, and a
    # finished turn is idle even if the file still says the clock is counting
    cur_elapsed = live_elapsed(cur_rec or {}, now_ms) if step_is_running(state, cur_rec) else None
    cur_est_ms = estimate_for(
        times, str(current.get("task", "")) if current else "", pace_ms, shapes, calls_mem
    )
    if cur_elapsed is not None:
        working = "working " + (short_duration(cur_elapsed, seconds=True) or "0s")
        if cur_elapsed > cur_est_ms:
            # the step has outrun its estimate: name the overrun where the clock is
            working += " (+" + (
                short_duration(cur_elapsed - cur_est_ms, seconds=True) or "0s"
            ) + ")"
        meta.append(working)
    meta.append(fmt_age(state.get("source_updated_ms")))
    if state.get("list_version"):
        meta.append(f"list #{state['list_version']}")
    if total and done == total:
        meta.append("all steps done")
    clock = time.strftime("%H:%M:%S", time.localtime(now_ms / 1000))
    # The footer is intentionally tight: in a fixed-height strip, a blank separator
    # costs a visible step.  The bar and status remain the visual anchor, while the
    # metadata and clock are kept on separate rows only when the pane is narrow.
    footer = []
    progress = f"  [{bar}] {c(bold, label)}"
    # Same two projections as the framed pane, measured on the plain text before any styling
    # is added, and each one taken only if the row still has room: `EST REM`/`ETA` keep the
    # all-or-nothing rule they had, and the overall time to the goal is appended after them
    # so a narrow strip gains the total rather than losing the remaining count.
    extra = ""
    rem_ms = remaining_estimate_ms(times, todos, now_ms, pace_ms, shapes, calls_mem)
    if rem_ms > 0:
        suffix = " | EST REM: " + (short_duration(rem_ms, seconds=True) or "0s")
        suffix += " | ETA " + fmt_eta(rem_ms, now_ms)
        if _cell_width(progress) + _cell_width(extra) + len(suffix) <= width:
            extra += suffix
    tot_ms = total_estimate_ms(times, todos, now_ms, pace_ms, shapes, calls_mem)
    if tot_ms is not None:
        tot_suffix = " | TOT " + (short_duration(tot_ms) or "0s")
        if _cell_width(progress) + _cell_width(extra) + len(tot_suffix) <= width:
            extra += tot_suffix
    if extra:
        progress = progress + c(dim, extra)
    footer.append(progress)
    meta_text = " · ".join(x for x in meta if x)
    if compact:
        footer.append(c(dim, "  " + _clip(meta_text, max(1, width - 2))))
        footer.append(c(dim, f"  live {clock}"))
    else:
        live = f"live {clock}"
        room = max(1, width - len(live) - 5)
        left = _clip(meta_text, room)
        footer.append(f"  {c(dim, left)}   {c(dim, live)}")
    # The patch rows — what the last patch pass did and when the phone was last told
    # something — drawn only when the state actually carries those two facts. A state from
    # a build that did not read them (or a fixture) renders exactly as it always did, which
    # is what keeps this machine-readable path's line count stable.
    row = patch_row(state, width)
    if row:
        footer.append("  " + patch_row_text(row))
    # ...and the refit progress, when the log has enough closed steps for it to mean anything.
    # The plain path is the machine-readable one, so this is the same text the pane draws.
    refit = refit_row(state, width)
    if refit:
        footer.append("  " + patch_row_text(refit))
    if idle_s and idle_s >= 60:
        txt = f"  idle {short_duration(idle_s * 1000)}"
        if stale_after_s:
            txt += f" · closes in {short_duration(max(0.0, stale_after_s - idle_s) * 1000)}"
        footer.append(c(dim, txt[: max(16, width)]))

    avail = None if not height else max(1, height - len(lines) - len(footer))
    # With no step in progress the list is finished, and the window anchors at its END: the
    # run that gets collapsed is then the EARLIER, completed work, so its marker sits at the
    # top of the list body, above the visible steps, in list order. Anchored at the top it
    # collapsed the tail instead and put a completed marker under the steps it belonged to.
    start, end = _fit_blocks(blocks, avail, _window_anchor(cur_index, len(blocks)))
    if start:
        lines.append(c(dim, f"  … {start} earlier step{'s' if start > 1 else ''}"))
    for block in blocks[start:end]:
        lines += block
    if end < len(blocks):
        rest = len(blocks) - end
        lines.append(c(dim, f"  … {rest} more step{'s' if rest > 1 else ''}"))
    lines += footer
    return "\n".join(lines)


# ============================================================ rich pane layout
def _frame_row(text: str, width: int, frame) -> str:
    """One bordered content row, padded to the pane's inner width.

    `frame` is the border's role-bound painter (see `_styles`), not the raw colour
    function: the edges, the rules and the connectors are one ink, and `text` carries its
    own styling. One cell of padding inside each edge.
    """
    inner = max(0, width - 4)
    return frame("│") + " " + _pad_cells(_clip_cells(text, inner), inner) + " " + frame("│")


def _divider_row(width: int, frame) -> str:
    return frame("├" + "─" * max(0, width - 2) + "┤")


def _bottom_row(width: int, frame) -> str:
    return frame("╰" + "─" * max(0, width - 2) + "╯")


_SESSION_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})")


def _session_label(state: dict) -> str:
    """A short name for the session a pane is showing.

    Session ids are ISO stamps (`2026-09-24T07-05-26.585Z`) and the top border has
    room for a tag, not for that: month-day and clock, with the backend for context.
    """
    backend = str(state.get("backend") or "")
    session = str(state.get("session") or "")
    m = _SESSION_RE.match(session)
    stamp = f"{m.group(2)}-{m.group(3)} {m.group(4)}:{m.group(5)}" if m else session
    if backend and stamp:
        return f"{backend} · {stamp}"
    return stamp or backend or "no session"


def _top_border(width: int, frame, title: str, right: str, badge) -> str:
    """A rounded top border: the title on an accent badge, faint metadata right.

    `frame` is the border's painter and `badge` the title's chip — both role-bound, so
    this stays layout. The chip is reverse video (`7;<accent>`), which needs no
    background selector and so survives a terminal that ignores `dim`. The right slot
    gets whatever columns the title leaves, so a long session label shrinks rather than
    pushing the corner off the pane — and drops out entirely when there is not room for
    a readable tag.
    """
    label = f" {title} "
    left = f"╭── {label} "
    room = width - _cell_width(left) - 2  # corner cell, plus the space before it
    if right and room >= 12:
        tail = f" {_clip_cells(right, room - 4)} ──╮"
    else:
        tail = "╮"
    fill = max(0, width - _cell_width(left) - _cell_width(tail))
    return frame("╭── ") + badge(label) + frame(" ") + frame("─" * fill) + frame(tail)


# The frame's palette. `accent` may be a `#rrggbb` colour or a raw SGR code (`1;36`);
# the gradient stops are colours. Precedence is the tool's usual one: FBTODO_ACCENT /
# FBTODO_GRADIENT_START / FBTODO_GRADIENT_END beat a project `.fbtodo-theme.json`,
# which beats `~/.config/fbtodo/theme.json`, which beats these defaults.
# The four inks the pane paints in, and nothing else but the two STATE colours kept in the
# code (`warn` 33 for a step past its estimate or a patch that did not come out clean,
# `error` 31 for a failure): a palette is not a licence to hide a failure in grey.
#
#   accent   the interactive ink — the title chip, the active row's marker and estimate
#            badge, the bar's first stop
#   active   the one thing being worked on: the active row and the goal it belongs to
#   success  done: a finished step's tick, a patch that came out clean, the bar's last stop
#   muted    everything secondary — a step's description, durations, the age, the strip;
#            with `faint` for metadata that is not even claiming to be read: the frame's
#            edges, the section labels, the watcher pid, the model
THEME_DEFAULTS = {
    "accent": "1;36",
    "active": "#e6edf3",
    "success": "#2ea043",
    "gradient_start": "#22d3ee",
    # the bar ramps accent -> success, so the two ends of the one piece of data on the
    # frame are the same two hues as everything else on it
    "gradient_end": "#2ea043",
    "muted": "#8b949e",   # secondary text: durations, the goal line, the age
    # The progress bar's empty cells. Mid grey on purpose: dark enough to read as empty
    # next to the gradient fill, light enough that the track's full length is visible on
    # a dark pane — a near-background grey is the same as no track at all.
    "track": "#6b6b73",
    # The frame's own ink: the drawn edges, the collapsed-run rule and the right-hand
    # metadata. 242 in xterm-256, and a real colour rather than the `dim` attribute, which
    # a themed terminal is free to render onto the background.
    "faint": "#6b6b73",
}
THEME_KEYS = tuple(THEME_DEFAULTS)
THEME_FILE_LOCAL = ".fbtodo-theme.json"
THEME_FILE_GLOBAL = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config"),
    "fbtodo", "theme.json",
)


def _hex_rgb(value) -> tuple | None:
    """`#22d3ee` / `22d3ee` / `#2ee` as an (r, g, b) triple, or None if it is not one."""
    m = re.fullmatch(r"#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", str(value or "").strip())
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_256(rgb) -> str:
    """The nearest xterm-256 colour: the 6x6x6 cube, or the grey ramp for neutrals."""
    r, g, b = rgb
    if max(rgb) - min(rgb) < 12:
        grey = (r + g + b) // 3
        if grey < 8:
            return "16"
        if grey > 238:
            return "231"
        return str(232 + min(23, (grey - 8) // 10))
    cube = lambda v: 0 if v < 48 else 1 if v < 115 else min(5, (v - 35) // 40)  # noqa: E731
    return str(16 + 36 * cube(r) + 6 * cube(g) + cube(b))


def _color_sgr(value, truecolor: bool) -> str:
    """A theme colour as SGR parameters: hex is drawn in 24-bit where it can be, and in
    the nearest 256-colour entry where it cannot. A plain SGR code is used verbatim."""
    rgb = _hex_rgb(value)
    if rgb is None:
        return str(value or "").strip() or "0"
    if truecolor:
        return f"38;2;{rgb[0]};{rgb[1]};{rgb[2]}"
    return f"38;5;{_rgb_256(rgb)}"


def _theme_stamp() -> tuple:
    """What a resolved theme depends on: the env overrides, and both files' mtimes."""
    return (
        *(os.environ.get("FBTODO_" + k.upper(), "") for k in THEME_KEYS),
        *(
            (p, os.path.getmtime(p) if os.path.exists(p) else 0.0)
            for p in (THEME_FILE_GLOBAL, THEME_FILE_LOCAL)
        ),
    )


_THEME_CACHE: tuple | None = None


# A theme value ends up INSIDE an escape sequence, which makes it code the terminal
# runs: H2 measured `FBTODO_ACCENT='1;36<BEL><ESC>]52;c;…<BEL>'` painting a real escape
# into the frame, because a value without a `#` is passed through as an SGR list. Two
# forms are accepted — a raw SGR parameter list and a `#rrggbb` colour — and nothing
# else, so the value can only ever be parameters.
THEME_VALUE_RE = re.compile(r"^(?:#[0-9a-fA-F]{6}|\d{1,3}(?:;\d{1,3})*)$")
# [(source, value)] the last `read_theme()` refused. `status` prints it: a theme that
# silently does nothing is exactly how a bad value got this far unnoticed.
THEME_PROBLEMS: list = []


def _theme_value(value) -> str | None:
    """The value when it is a colour or an SGR list, and None when it is neither."""
    raw = str(value or "").strip()
    return raw if THEME_VALUE_RE.match(raw) else None


def read_theme() -> dict:
    """The frame's palette, resolved once per env/file state — a pane repaints often.

    Each value is validated as it is read (`_theme_value`): an unusable one leaves the
    role at its default rather than at a value that would carry whatever it contained
    into the frame, and is remembered for `status`.
    """
    global _THEME_CACHE, THEME_PROBLEMS
    stamp = _theme_stamp()
    if _THEME_CACHE and _THEME_CACHE[0] == stamp:
        THEME_PROBLEMS = _THEME_CACHE[2]
        return _THEME_CACHE[1]
    theme = dict(THEME_DEFAULTS)
    problems: list = []
    for source, path in (("theme.json", THEME_FILE_GLOBAL),
                         ("project theme", THEME_FILE_LOCAL)):
        data = read_json(path, None)
        if isinstance(data, dict):
            for k, v in data.items():
                if k not in THEME_KEYS or not str(v).strip():
                    continue
                good = _theme_value(v)
                if good is None:
                    problems.append((f"{source}: {k}", str(v)[:40]))
                else:
                    theme[k] = good
    for key in THEME_KEYS:
        value = (os.environ.get("FBTODO_" + key.upper()) or "").strip()
        if not value:
            continue
        good = _theme_value(value)
        if good is None:
            problems.append((f"FBTODO_{key.upper()}", value[:40]))
        else:
            theme[key] = good
    THEME_PROBLEMS = problems
    _THEME_CACHE = (stamp, theme, problems)
    return theme


def _theme_gradient(theme) -> tuple:
    """The two gradient stops as RGB, falling back to the default stop by stop."""
    fallback = [_hex_rgb(THEME_DEFAULTS[k]) for k in ("gradient_start", "gradient_end")]
    return tuple(
        _hex_rgb(theme.get(k)) or fallback[i]
        for i, k in enumerate(("gradient_start", "gradient_end"))
    )


def _supports_truecolor() -> bool:
    """24-bit colour, or a terminal that advertises it. `FBTODO_TRUECOLOR` wins."""
    override = os.environ.get("FBTODO_TRUECOLOR", "").strip().lower()
    if override:
        return override not in ("0", "off", "false", "no")
    if (os.environ.get("COLORTERM") or "").lower() in ("truecolor", "24bit", "24-bit"):
        return True
    term = (os.environ.get("TERM") or "").lower()
    return "truecolor" in term or "direct" in term or "24bit" in term


def _styles(theme: dict, truecolor: bool) -> dict:
    """The pane's style declarations: role -> SGR parameters, resolved once per paint.

    This is the only place a colour is decided. The drawing code asks for a ROLE, so
    changing the palette — or how a role is expressed — never reaches into layout.

    * `accent`  the title chip, the active row's marker and estimate badge
    * `active`  the ink of the one thing being worked on: the active row, the goal
    * `strong`  that ink with weight: the first line of the active row
    * `success` a finished step's tick, a clean patch, the bar's last stop
    * `faint`   the frame's own ink: edges, rules, collapsed-run connectors, metadata
    * `muted`   secondary text: durations, the goal's label, the status strip
    * `done`    the description of a finished step (muted, plus the dim attribute)
    * `tick`    a finished step's marker: the success hue, dimmed so the row recedes
    * `track`   the progress bar's empty cells
    * `badge`   the accent as a chip: reverse video, so no background selector is used
    """
    sgr = lambda key: _color_sgr(theme.get(key), truecolor)  # noqa: E731
    accent, muted = sgr("accent"), sgr("muted")
    return {
        "accent": accent,
        "active": sgr("active"),
        "strong": f"1;{sgr('active')}",
        "success": sgr("success"),
        "faint": sgr("faint"),
        "muted": muted,
        "done": f"2;{muted}",
        "tick": f"2;{sgr('success')}",
        "track": sgr("track"),
        "badge": f"7;{accent}",
    }


# The progress bar's boundary cell: the nearest of these to the fraction left over, so a
# third of a list draws as a third of a bar instead of rounding away to a whole cell.
BAR_EIGHTHS = " ▏▎▍▌▋▊▉█"

# The status strip's spinner while a step is running: one frame per second, advanced by the
# pane's own repaint clock rather than a timer of its own.
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _progress_bar(
    done: int, total: int, bar_w: int, c, truecolor: bool, theme: dict | None = None
) -> str:
    """`████▊░░░` — `█` filled, the nearest eighth at the boundary, `░` on the track.

    The count beside the bar carries the exact number, and the eighth block puts the
    fractional cell where it belongs; the empty cells are drawn in an explicit `track`
    colour rather than the `dim` attribute, because dim is the terminal's own idea of dim
    and where it lands on the background the bar reads as ending at the fill, with no
    length to judge against.

    Every cell is the GLYPH painted with a foreground colour (`38;2;…` where the terminal
    takes 24-bit colour, `38;5;…` where it does not) — never a background-filled block.
    A background block would depend on the terminal's own background scheme to read at all,
    which is the same trap the track fell into, and it would stop being a character.
    """
    theme = read_theme() if theme is None else theme
    start, end = _theme_gradient(theme)
    track = _color_sgr(theme.get("track"), truecolor)
    ratio = (done / total) if total else 0.0
    ratio = min(1.0, max(0.0, ratio))
    cells = ratio * bar_w
    full = min(bar_w, int(cells))
    frac = cells - full  # the one cell the eighths are for
    out = []
    for i in range(bar_w):
        if i > full or (i == full and frac <= 0):
            out.append(f"\x1b[{track}m░\x1b[0m")
            continue
        # The ramp spans the FILLED run, not the whole bar: sampled across the bar the
        # gradient only reached its emerald end at 100%, so a half-done list showed a
        # flat cyan-teal run. Here the fill always reads cyan at its left edge and emerald
        # at its leading edge, and the count beside it says how far along it is.
        t = min(1.0, (i + 0.5) / max(1, full))
        rgb = [int(round(start[k] + (end[k] - start[k]) * t)) for k in range(3)]
        # the same ramp in both: a flat accent made the 256-colour terminal's bar one solid
        # cyan while the 24-bit one ran cyan to green, which is not the same bar. The 256
        # form needs its `38;5;` prefix — a bare index is a BACKGROUND colour in SGR, so
        # `\x1b[45m█` paints a magenta block instead of a cyan character.
        colour = (
            f"38;2;{rgb[0]};{rgb[1]};{rgb[2]}" if truecolor
            else "38;5;" + _rgb_256(rgb)
        )
        glyph = "█" if i < full else BAR_EIGHTHS[min(8, max(1, int(round(frac * 8))))]
        out.append(f"\x1b[{colour}m{glyph}\x1b[0m")
    return "".join(out)


def _elision_note(count: int, flags: list, where: str) -> str:
    """The marker for steps the window left out, worded from THOSE steps.

    `│ 3 earlier steps completed` / `│ 4 more steps pending`, the rule drawn in the same
    column as the step markers so a collapsed run reads as a rule down the list. The word
    used to come from
    the side of the window the marker sits on, which contradicted the bar above it: a
    finished list whose last steps were elided read `2 more steps pending` at 100% (4/4),
    and `fbtodo status` said `All done` in the same breath. A run of hidden steps that is
    all done is past work whatever side it sits on; `more … pending` is kept for a run
    that still has something left in it, which is also when `done < total`.
    """
    plural = "s" if count > 1 else ""
    if count and all(flags):
        return f"  │ {count} earlier step{plural} completed"
    return f"  │ {count} {where} step{plural} pending"


# In plain words: the framed pane. This is the drawing code, and the longest part of the file,
# because a terminal is not a canvas: every row is measured in screen columns, cut to a frame
# whose height depends on the pane it is in, and the colours are roles resolved from a theme
# rather than colours sprinkled through the drawing. Read it as "decide what fits, then draw".
def _render_rich(
    state: dict,
    color: bool,
    watching: int | None,
    width: int,
    now_ms: int,
    idle_s: float | None,
    stale_after_s: float,
    goal_lines: int,
    height: int | None,
) -> str:
    """The framed, high-density pane shown on a colour terminal.

    The plain renderer above stays the machine-readable path (pipes, `snap`,
    self-checks): it has no borders, so line counts and first-column markers stay
    stable for scripts.  A live pane is a TTY with colour, and gets this layout.
    """
    c = paint(color)
    # Only the two STATE colours are literals any more; everything else the drawing asks
    # for is a role from `_styles`. `warn` is a step past its estimate, a nudge or a patch
    # that did not come out clean; `red` is a failure or an overrun.
    yellow, red = "33", "31"
    theme = read_theme()
    truecolor = _supports_truecolor()
    # Every colour decision lives in `_styles`; this function only asks for roles, so the
    # drawing below reads the same whatever the palette resolves to.
    st = _styles(theme, truecolor)
    accent, muted = st["accent"], st["muted"]
    active_ink, success = st["active"], st["success"]
    done_style, tick_style = st["done"], st["tick"]
    # A finished step's description is the muted grey plus the dim attribute: dim is what
    # `muted` means on a terminal with no theme of its own — and its tick is the success
    # hue dimmed the same way, so a row that is over recedes instead of shouting.
    # The one row that is not over is the only one painted in `active` ink; the accent is
    # spent on what the eye should find first (the title chip, the active marker, the
    # estimate badge, the bar), never on prose.

    def frame(text: str) -> str:
        """The frame's own ink: edges, rules and the collapsed-run connectors."""
        return c(st["faint"], text)

    def badge(text: str) -> str:
        """A chip: the accent as reverse video, so no background selector is used."""
        return c(st["badge"], text)

    inner = max(10, width - 4)
    # The patches' own two facts, each on a row of its own when the pane is too narrow to
    # share one: the outcome the owner would otherwise ssh in to read, and when the phone
    # was last told something. They cost rows of the fixed height, which the budgets below
    # are told about through `patch_rows`.
    patch_row_here = patch_row(state, inner)
    refit_row_here = refit_row(state, inner)

    def patch_row_styled(pairs: list) -> str:
        """A row as drawn: labels in the frame's grey, values in their own severity."""
        # the label is metadata (faint), the value carries its own severity — and a clean
        # patch reads as `success`, which is what the green is FOR
        ink = {"ok": success, "warn": yellow, "bad": red}
        return "  " + "   ".join(
            c(st["faint"], f"{label}  ") + c(ink.get(sev, muted), text)
            for label, text, sev in pairs
        )

    # With a watcher, name it; without one the pane still has to say which session
    # it is showing, so the top border carries the (short) session title instead.
    # Which model this session runs, on the border: the estimate memory is per model (see
    # task_history_from_log), so a pane that quotes a pace should name the model that
    # taught it. AFTER the watcher pid on purpose — the border clips the right end, and
    # the pid is what the pane has always said it is showing (asserted in two checks);
    # the model is the newcomer, so on a narrow pane the model is what goes.
    model = _clip_cells((state.get("model") or "").split("/")[-1], 14) if state.get("model") else ""
    who = f"watcher: pid {watching}" if watching else _session_label(state)
    right = f"{who} · {model}" if model else who
    rows = [_top_border(width, frame, "FREEBUFF TODOS", right, badge)]

    if state.get("error"):
        rows.append(_divider_row(width, frame))
        for seg in _wrap(str(state["error"]), inner, 0):
            rows.append(_frame_row(c(red, seg), width, frame))
        rows.append(_bottom_row(width, frame))
        return "\n".join(rows)

    # The heading is the agent's own `Goal:` line, in muted grey so the steps below it
    # are what the eye lands on. A session that wrote none gets no heading ROW at all —
    # the strip is fixed-height, and `— none stated` spent one of its rows saying
    # nothing; `fbtodo status` reports the missing line instead. The emoji is two cells
    # but one character, which is why the wrap budget is a column short.
    goal = str(state.get("goal") or "")
    now_txt = str(state.get("now") or "")
    nudge = str(state.get("nudge") or "")
    head: list[str] = []
    max_goal_lines = min(goal_lines, 2) if width < 60 else goal_lines
    goal_prefix = "🎯 Goal: "
    if max_goal_lines > 0 and goal:
        segs = textwrap.wrap(
            goal,
            width=max(12, inner - 1),
            initial_indent=goal_prefix,
            subsequent_indent=" " * _cell_width(goal_prefix),
            max_lines=max_goal_lines,
            placeholder=" …",
        )
        # The icon and its label carry the identity in the muted grey; the goal itself is
        # the frame's heading, so it is drawn in the readable ink rather than being dimmed
        # along with the label — and without weight, which belongs to the active row.
        pad = " " * _cell_width(goal_prefix)
        # The label stays the muted grey; the goal itself is `active` ink WITHOUT the bold
        # attribute — it is the list's heading, not its subject, and weight spent here was
        # weight the active row could not have.
        head.append(c(muted, goal_prefix) + c(active_ink, segs[0][len(goal_prefix):]))
        head += [c(active_ink, pad + seg[len(pad):]) for seg in segs[1:]]
    if now_txt:
        segs = textwrap.wrap(
            now_txt, width=inner, initial_indent="NOW · ", subsequent_indent="      ",
            max_lines=1, placeholder=" …",
        )
        # The macro goal and the micro step are separated by INK, not by two bright colours
        # competing above the list: the goal's label is the muted grey, `NOW` is the accent
        # (the one thing on this row that is a pointer), and both carry `active` prose. The
        # yellow that used to be here is now only ever a warning.
        body = segs[0][len("NOW · "):]
        indent = segs[0][:len("NOW · ")] if segs[0].startswith("NOW · ") else "NOW · "
        head += [c(accent, indent.rstrip(" ·")) + c(st["faint"], " · ")
                 + c(active_ink, body)]
    if nudge and nudge != now_txt:
        segs = textwrap.wrap(
            "rewrite the list, then continue", width=inner,
            initial_indent=f"NUDGE · {nudge} — ", subsequent_indent="        ",
            max_lines=1, placeholder=" …",
        )
        head += [c(yellow, seg) for seg in segs]
    no_times = no_times_note(state)
    if height:
        # Seven rows of chrome sit below the list — eight when the patch row is drawn — and
        # the no-times note borrows one more when it is there, so the frame still fits the
        # pane rather than pushing its top border off the screen on every repaint.
        head = head[: max(1, min(
            len(head), height - 7 - (1 if patch_row_here else 0)
            - (1 if refit_row_here else 0) - (1 if no_times else 0)
        ))]

    # In plain words: the same situations the plain renderer names, plus the one line the
    # framed pane can afford and a script's snapshot cannot — what the session has been doing
    # instead of writing a list.
    todos = state.get("todos") or []
    if not todos:
        rows += [_frame_row(h, width, frame) for h in head]
        rows.append(_divider_row(width, frame))
        why = no_list_reason(state)
        for seg in _wrap(why, inner, 0):
            rows.append(_frame_row(c(muted, seg), width, frame))
        # The second basket first, then the tool counts only when there is no feed to show:
        # both say "and here is what it has been doing instead", and the feed says it with
        # less room — which matters, because this branch has no height management of its own.
        note = tools_note(state.get("tool_calls") or {})
        # The turn is the frame the calls sit in, so it is drawn first; both are clipped to a
        # single row each, which is what keeps this branch's height countable. Nine rows of
        # chrome and the no-list explanation are already spent above this point — plus the
        # refit row when it has something to say, which is charged here too.
        room = max(0, (height - 9 - (1 if refit_row_here else 0)) if height
                   else ACTION_ROWS + 1)
        turn_line = turn_note(state, now_ms)
        body = [turn_line] if turn_line else []
        body += observed_rows(state, ACTION_ROWS, now_ms)[: max(0, room - len(body))]
        if body:
            for seg in body:
                rows.append(
                    _frame_row(c(muted, "  " + _clip_cells(seg, inner - 4)), width, frame)
                )
        else:
            for seg in _wrap(note, inner, 1) if note else []:
                rows.append(_frame_row(c(muted, seg), width, frame))
        if patch_row_here:
            # A frame with no list at all is exactly the NAS pane waiting for `fb` — when
            # "did the hook's patch step come out clean?" is the question being asked.
            rows.append(_frame_row(patch_row_styled(patch_row_here), width, frame))
        if refit_row_here:
            rows.append(_frame_row(patch_row_styled(refit_row_here), width, frame))
        rows.append(_bottom_row(width, frame))
        return "\n".join(rows)

    times = state.get("task_times") or {}
    history = state.get("task_history") or {}
    shapes = state.get("task_shapes") or {}
    calls_mem = state.get("task_calls") or {}
    cur_index = current_index(todos)
    # The pace the whole list is projected at, learned from the steps already finished —
    # and from what those steps took in earlier sessions.
    pace_ms = step_pace_ms(times, todos, now_ms, history)
    blocks: list[list[str]] = []
    flags: list[bool] = []  # per block: was that step already done, for the elision notes
    for i, t in enumerate(todos):
        task = str(t.get("task", ""))
        done_flag = bool(t.get("completed"))
        rec = times.get(task) or {}
        elapsed = live_elapsed(rec, now_ms)
        active = i == cur_index and not done_flag
        # One symbol per row, never a box glued to an arrow: `➔ [ ]` and `○ [ ]` said the
        # same thing twice, and the box made an active row six cells wider than a finished
        # one. The three markers are one cell each, so every row's text starts in the same
        # column, and a wrapped line hangs under it.
        prefix = "  ✔  " if done_flag else "  ➔  " if active else "  ○  "
        # Every row's right-hand token carries the row's own state: muted for a step that is
        # over or not started, the accent for the one in flight (its estimate is bracketed
        # there, a badge rather than a second bare number), and red when it has run past it.
        tag_ink = muted
        indent = _cell_width(prefix)
        # The row's right-margin token, in one voice per state: the frozen span of a
        # finished step, the running clock against the pace for the step in flight, or
        # just the pace for one still ahead. A step never seen running gets no number
        # rather than a zero, which would read as "this took no time".
        running = elapsed is not None and not rec.get("done_ms")
        clock = (
            (short_duration(elapsed, seconds=True) or "0s") + ("…" if running else "")
            if elapsed is not None else ""
        )
        # This step's own estimate: its remembered history when there is one, else the
        # pace the list has settled on. The PLAIN path keeps the bare `~3m` on purpose: a
        # piped `snap` is a published, script-parsed contract, and a range would change the
        # shape of a token those scripts already match. The framed pane and `fbtodo status`
        # are where the spread is shown (see `fmt_estimate_spread`).
        est_ms = estimate_for(times, task, pace_ms, shapes, calls_mem)
        est = fmt_estimate(est_ms)
        budget = max(0, inner - indent - 8)
        if done_flag:
            tag = clock
        elif active and clock:
            stuck = est_ms > 0 and elapsed > STUCK_FACTOR * est_ms
            # When the estimate rides along, the running number drops its own `…`: the
            # pair already says the clock has not stopped, and `4m10s… / ~3m` reads as
            # two things happening to one number.
            base = clock[:-1] if running else clock
            # Widest form first, but a row never trades away so much of the task that it
            # stops being readable: `[STUCK?]` appears where the flag still leaves
            # MIN_TASK_TEXT columns of text, and on a tight pane the running/estimate pair
            # carries the alert on its own.
            cands = ([f"{base} [{est}] [STUCK?]"] if stuck else []) + [
                f"{base} [{est}]", clock,
            ]
            tag_ink = "7;31" if stuck else accent
            tag = clock
            for cand in cands:
                if _cell_width(cand) > budget:
                    continue
                if inner - (_cell_width(cand) + 2) - indent >= MIN_TASK_TEXT:
                    tag = cand
                    break
        else:
            tag = clock or est
        # The duration belongs against the frame's right wall, not glued to the last word:
        # line 1 reserves room for it, and the gap is padded out to the inner edge.
        tag_w = _cell_width(tag)
        reserve = min(tag_w + 2, max(0, inner - indent - 8)) if tag_w else 0
        segs = _wrap(task, inner - reserve, indent)
        block = []
        for j, seg in enumerate(segs):
            if j == 0:
                if done_flag:
                    # both halves of a finished row are dim: the tick no longer out-shines
                    # the description it belongs to, so the row recedes as one unit
                    line = c(tick_style, "  ✔") + "  " + c(done_style, seg)
                elif active:
                    # the one row in the frame with the accent on its marker and its
                    # number: bright ink, and the estimate as a badge
                    line = c(accent, "  ➔") + "  " + c(st["strong"], seg)
                else:
                    line = c(muted, "  ○") + "  " + c(muted, seg)
                if tag:
                    line += " " * max(1, inner - _cell_width(line) - tag_w) + c(tag_ink, tag)
            else:
                # a wrapped line carries the same voice as its first line
                if done_flag:
                    line = " " * indent + c(done_style, seg)
                elif active:
                    line = " " * indent + c(st["strong"], seg)
                else:
                    line = " " * indent + c(muted, seg)
            block.append(line)
        if height and len(block) > TASK_MAX_LINES:
            block = block[:TASK_MAX_LINES]
            block[-1] += c(muted, " …")
        blocks.append(block)
        flags.append(done_flag)

    # Six rows of chrome, plus the optional ones this frame is actually carrying: the patch
    # and refit facts, and the no-times explanation. That last one was missing here — it was
    # subtracted where the HEADING was clipped but not from the step area's budget, so a
    # session with no clocks to report painted one row past its pane and lost the title.
    avail = None if not height else max(
        1, height - len(head) - 6 - (1 if patch_row_here else 0)
        - (1 if refit_row_here else 0) - (1 if no_times else 0)
    )
    anchor = _window_anchor(cur_index, len(blocks))
    start, end = _fit_blocks(blocks, avail, anchor)

    def _rows_used(s: int, e: int) -> int:
        """Rows the task area paints: both elision notes plus the LINES of the steps kept.

        Counting blocks rather than lines is what let the goal row push this pane past its
        height: a step that wraps to two lines is two rows, and the budget is in rows.
        """
        return ((1 if s else 0)
                + sum(len(blocks[i]) for i in range(s, e))
                + (1 if e < len(blocks) else 0))

    # Rows the steps did not need, held back one at a time: the first buys the total its own
    # row (where the pane is tall enough to hand one over), the second buys the vertical
    # breath between the list and the footer. The fit is re-run with each row held back and
    # given straight back if the steps turn out to want it, so the frame can never grow past
    # `height` and no step is ever elided to make room for a number ABOUT the list.
    spare = 0
    if avail is None:
        spare = 1
    else:
        for hold in (1, 2):
            if avail - hold < 1:
                break
            if _rows_used(*_fit_blocks(blocks, avail - hold, anchor)) < avail - hold:
                spare = hold
                start, end = _fit_blocks(blocks, avail - hold, anchor)

    # Counted from the list being drawn, not from the state's own tally: the two agree
    # when this build wrote the state, and a state left by another one must never paint
    # 100% over a step list that still has unticked steps in it.
    total = len(todos)
    done = sum(1 for t in todos if t.get("completed"))
    pct = int(round(100.0 * done / total)) if total else 0
    label = f"{pct}% ({done}/{total})"
    # The bar keeps its size; the projection only borrows space the row already has left
    # over, so a narrow pane shows a shorter suffix (or none) rather than a squeezed bar.
    # `EST REM` and `ETA` are one step apart, and on a tight row the clock goes first.
    # The bar row is labelled by the bar: `PROGRESS` cost eight columns of a 68-column row
    # and said what `[████░░]` already says, and it started one column left of `PATCH`,
    # `GOAL`, `ALERT` and `REFIT` — the one footer label out of the fixed slot the others
    # keep. It is not drawn any more, and the two spaces of that slot are.
    base_w = _cell_width("  []  ") + _cell_width(label)
    bar_w = max(4, min(20, inner - base_w))
    rem_ms = remaining_estimate_ms(times, todos, now_ms, pace_ms, shapes, calls_mem)
    # Vertical room for a row of its own is decided above, where the step rows were fitted.
    spent_ms = elapsed_total_ms(times, todos, now_ms)
    tot_ms = (
        spent_ms + rem_ms if spent_ms is not None
        else None
    )
    suffix = ""
    room = inner - base_w - bar_w
    # The pace and the model that learned it ride HERE, on the row the estimates are on,
    # because the status strip is full while a step runs (the chip carries the clock and
    # the overrun) and the owner asked to see it. Same discipline as the suffix above: the
    # bar keeps its size, the text borrows what the row has left, and the first candidate
    # that fits wins — so a tight strip shows EST REM and says the pace on the strip
    # instead, where there is room when no step is running.
    cands: list[str] = []
    if rem_ms > 0:
        rem_txt = short_duration(rem_ms, seconds=True) or "0s"
        rem_spread = pace_spread_ms(times, todos, now_ms, history)
        # The remaining time is a sum of projections, so it inherits the spread of the
        # steps it sums: `EST REM: 12m (2m–1h)` says what `EST REM: 12m` alone cannot.
        # Widest first — the whole range with the clock and the pace, then the same range
        # on its own, and only then the bare number.
        ranged = (
            [f" | EST REM {rem_txt} ({fmt_range(*rem_spread)})"]
            if rem_spread and is_wide(*rem_spread) else []
        )
        # Widest form first, and the pace is not among them: the list's own pace used to
        # ride here as `~8m · model`, which is the same number every step row already
        # carries, on the rows where it is a prediction about THAT step. The model stayed
        # on the top border, where there is room for it at any width.
        cands += [
            (ranged[0] + f" | ETA {fmt_eta(rem_ms, now_ms)}") if ranged else None,
            (ranged[0]) if ranged else None,
            f" | EST REM {rem_txt} | ETA {fmt_eta(rem_ms, now_ms)}",
            f" | EST REM {rem_txt}",
        ]
        cands = [c for c in cands if c]
    for cand in cands:
        if _cell_width(cand) <= room:
            suffix = cand
            break
    # The overall time to the goal rides AFTER that ladder rather than inside it: `EST REM`
    # and `ETA` keep the priority they already had, and the total only takes columns the row
    # has left over. It borrows the progress row only when there is no spare line to carry
    # it on its own — a row of its own is the better answer, and it is what the owner asked
    # for after watching a 64-column strip drop the field entirely.
    if tot_ms is not None and not spare:
        tot_suffix = f" | TOT {short_duration(tot_ms) or '0s'}"
        if not suffix and _cell_width(tot_suffix) <= room:
            suffix = tot_suffix
        elif suffix and _cell_width(suffix + tot_suffix) <= room:
            suffix += tot_suffix
    bar = _progress_bar(done, total, bar_w, c, truecolor, theme)
    # The ratio is the row's primary datum: `active` ink (bright, no bold attribute — the
    # bold white that used to be here was one of the weights the frame could not spend),
    # and the projection is metadata, so it is muted.
    progress = (
        "  [" + bar + "]  " + c(active_ink, label)
        + (c(muted, suffix) if suffix else "")
    )

    current = todos[cur_index] if cur_index is not None else None
    cur_rec = (times.get(str(current.get("task", ""))) or {}) if current else {}
    # A closed clock is a measurement, not work in progress — and a turn that ended with a
    # step still current is idle however long ago the list was written, even if the record
    # on disk has not been rewritten yet.
    cur_elapsed = live_elapsed(cur_rec, now_ms) if step_is_running(state, cur_rec) else None
    # One state, not three tags: a finished list IS idle, so `ALL DONE` beside `IDLE 4m`
    # spent half the footer saying the same thing twice. The list's own age rides on the
    # LIST field and the LIVE clock stays rightmost; the running step's clock is not here
    # at all, because it is on the row this strip is about.
    # Uppercase, like the title chip: the pane's chrome is one voice, so a badge never
    # mixes `FREEBUFF TODOS` with `All done` in the same frame.
    cur_est_ms = estimate_for(
        times, current.get("task", "") if current else "", pace_ms, shapes, calls_mem
    )
    over_ms = (cur_elapsed - cur_est_ms) if cur_elapsed is not None else 0
    if cur_elapsed is not None:
        spin = SPINNER[int(now_ms // 1000) % len(SPINNER)] + " "
        # The footer says the STATE; the clock that goes with it is on the step's own row,
        # and it is the same number — `WORKING 11m07s` here and `11m07s [~10m]` there was the
        # footer repeating the row it is the footer for. The chip still turns red on an
        # overrun, because the footer is where a glance lands.
        word = f"{spin}WORKING"
    elif total and done == total:
        word = "ALL DONE"
    else:
        word = "IDLE"
    # The age belongs to a state that is waiting: beside a running step its own counter
    # is the honest number, and the two would read as a contradiction ("Working 42s
    # (2m ago)").
    # The list's OWN age, carried by the LIST field rather than by the chip: `LIST: #3 ·
    # 12m ago`. On the chip it had to be hidden whenever a step was running — `Working 42s
    # (2m ago)` read as the STATE's age, and the two looked like a contradiction — and that
    # hid it on exactly the panes that need it, because a list an agent has stopped
    # re-writing looks current for as long as a clock is on it. On the LIST field there is
    # nothing to misread, so it is drawn whatever the state is doing. The minute's gate
    # stays: below it the number would jitter on every repaint and say nothing new.
    updated = int(state.get("source_updated_ms") or state.get("ts") or 0)
    age_text = (
        f"{short_duration(now_ms - updated) or '0m'} ago"
        if (updated and now_ms - updated >= 60_000)
        else ""
    )
    clock = time.strftime("%H:%M:%S", time.localtime(now_ms / 1000))
    live_field = "LIVE: " + clock
    listed = f"LIST: #{state['list_version']}" if state.get("list_version") else ""
    listed_age = f"{listed} · {age_text}" if (listed and age_text) else listed
    if listed_age and list_behind(state, now_ms):
        # A finished list in a session that has written well past it: the one case worth a
        # verdict, and a question mark because it is still a guess (see `list_behind`).
        listed_age += " [STALE?]"

    # An over-budget step keeps the same chip shape but turns it red, so the overrun reads
    # at a glance without adding a word to the strip.
    chip = badge if over_ms <= 0 else (lambda text: c("7;31", text))

    def strip(text: str, keep_list: bool, keep_age: bool) -> str:
        """The status strip: the state on a badge, then the fields ` │ ` apart."""
        body = chip(f" {text} ")
        fields: list[str] = []
        if listed:
            # the age is what a narrow strip spends first, and it goes whole
            if keep_list:
                fields.append(listed_age if keep_age else listed)
        elif keep_age and age_text:
            # No LIST field exists to carry the age — a one-shot probe has no list NUMBER to
            # show (measured on this machine: a bare `json` reports `list #0`) — so the age
            # stays where it used to live rather than going missing.
            body += c(muted, age_text)
        return body + "".join(
            c(st["faint"], " │ ") + c(muted, field) for field in fields + [live_field]
        )

    # On a strip too narrow for all of it the list's own age goes first, then LIST: itself —
    # the state chip and the live clock are what the row is for. The tiers are (keep_list,
    # keep_age). The age is the last field to go because it is the only thing on the row
    # that says the list has fallen behind the work.
    for tier in ((word, True, True), (word, True, False), (word, False, False)):
        status = strip(*tier)
        if _cell_width(status) <= inner:
            break
    else:
        status = _clip_cells(strip(word, False, False), inner)

    rows += [_frame_row(h, width, frame) for h in head]
    rows.append(_divider_row(width, frame))
    # The two summaries are rows of the step AREA, and at a budget of one or two there is no
    # window that fits both of them and a step: the frame then grew past its pane and its top
    # scrolled away, which is how a 9-line pane came to show a list with no title over it at
    # all — the height the pane gave the steps had already been spent on the notes about the
    # steps. A note is a courtesy to the steps it hides; the title and the goal are not, so
    # the notes are drawn only while the area has rows left for them (the count of what is
    # still AHEAD wins the one row, if only one is left).
    def rows_free(s: int, e: int) -> int:
        """Rows of the step area left over once the steps it keeps have painted."""
        if avail is None:
            return 2
        return max(0, avail - sum(len(blocks[i]) for i in range(s, e)))

    # ...and when the summaries were the thing that did not fit, the heading's LAST row is
    # what buys them back: at this height `NOW` is a nicety, while `4 more steps pending` is
    # the list itself. One row, one retry — never the goal, never the title.
    note_room = rows_free(start, end)
    if (height and note_room == 0 and len(head) > 1
            and (start or end < len(blocks)) and avail is not None):
        head = head[:-1]
        avail = max(1, height - len(head) - 6 - (1 if patch_row_here else 0)
                    - (1 if refit_row_here else 0) - (1 if no_times else 0))
        start, end = _fit_blocks(blocks, avail, anchor)
        note_room = rows_free(start, end)
    # With one row to spend, it goes to the LARGER hidden run: that is where the work the
    # window is not showing is, and on a finished list it is the completed steps above.
    show_above = start > 0
    show_below = end < len(blocks)
    if note_room < 2:
        if (len(blocks) - end) > start:
            show_above = False
        else:
            show_below = False
    if show_above:
        note = _elision_note(start, flags[:start], "earlier")
        # A run of finished work that collapses away can carry its net variance — how far
        # those steps ran from the pace — when there is one to speak of. It rides in the
        # same parentheses the idle age uses, so the row keeps its shape.
        if all(flags[:start]):
            variance = run_variance_ms(times, todos[:start], now_ms, pace_ms)
            if variance:
                note += f" ({fmt_variance(variance)})"
        rows.append(_frame_row(c(muted, note), width, frame))
    for block in blocks[start:end]:
        rows += [_frame_row(line, width, frame) for line in block]
    if show_below:
        rows.append(_frame_row(
            c(muted, _elision_note(len(blocks) - end, flags[end:], "more")), width, frame,
        ))
    rows.append(_divider_row(width, frame))
    rows.append(_frame_row(progress, width, frame))
    # The overall time to reach the goal, on a row of its own where the pane is tall enough
    # to hand one over. Two numbers, not three: spent and total are what this row has and
    # nowhere else has, while the THIRD it used to print — `3m30s left` — is the `EST REM`
    # on the bar row above, which is where the remaining work is projected from. The label
    # is metadata like `PATCH`'s, so it is drawn in the frame's faint ink and stops
    # competing with the number it introduces.
    goal_at = len(rows)
    if spare:
        if tot_ms is not None:
            goal_bits = [f"{short_duration(tot_ms, seconds=True) or '0s'} total"]
            if spent_ms:
                goal_bits.insert(0, f"{short_duration(spent_ms, seconds=True) or '0s'} spent")
            rows.append(_frame_row(
                "  " + c(st["faint"], "GOAL  ") + c(muted, "  ·  ".join(goal_bits)),
                width, frame,
            ))
        # ...and a spare row past that is spent on a breath rather than on nothing: the one
        # blank line between the list and the footer is the vertical padding a fixed-height
        # pane can afford, because it is the row the steps did not want — never one taken
        # off a step to buy it.
        if spare >= 2 or tot_ms is None:
            rows.append(_frame_row("", width, frame))
    goal_n = len(rows) - goal_at
    if no_times:
        rows.append(_frame_row(c(muted, no_times), width, frame))
    facts_at = len(rows)
    if patch_row_here:
        rows.append(_frame_row(patch_row_styled(patch_row_here), width, frame))
    if refit_row_here:
        rows.append(_frame_row(patch_row_styled(refit_row_here), width, frame))
    facts_n = len(rows) - facts_at
    rows.append(_frame_row(status, width, frame))
    rows.append(_bottom_row(width, frame))
    # ...and the frame still FITS: a row that scrolls off the top takes the title with it,
    # which is what a short pane used to show — a list with nothing over it. What is given
    # up is given up in the order of how little it changes what the pane is FOR: the patch
    # and alert facts first (they are news, not state, and `fbtodo status` has them), then
    # the total's own row, then the heading — never the list, the bar or the state, which
    # are what the frame exists to say at all.
    if height and len(rows) > height:
        over = len(rows) - height
        for at, count in ((facts_at, facts_n), (goal_at, goal_n), (1, len(head))):
            if over <= 0:
                break
            take = min(over, count)
            del rows[at + count - take:at + count]  # the LAST of them first: NOW, then goal
            over -= take
    return "\n".join(rows)


def render(
    state: dict,
    color: bool,
    watching: int | None = None,
    width: int = 80,
    now_ms: int | None = None,
    idle_s: float | None = None,
    stale_after_s: float = 0.0,
    goal_lines: int = 3,
    height: int | None = None,
) -> str:
    """Pick the framed pane (colour terminal) or the plain machine-readable text."""
    # The second half of the text filter (see `clean_text`): a state that came off disk —
    # this process's own cache, or a file an older build wrote — is filtered here, so no
    # frame can be painted from prose that never went through a source.
    state = clean_observation(state)
    now_ms = now_ms or int(time.time() * 1000)
    if color and width >= 30:
        return _render_rich(
            state, color, watching, width, now_ms, idle_s, stale_after_s, goal_lines, height,
        )
    return _render_plain(
        state, color, watching, width, now_ms, idle_s, stale_after_s, goal_lines, height,
    )


def width_of_default() -> int:
    return _shutil.get_terminal_size(fallback=(80, 24)).columns


def adopt_version(state: dict) -> dict:
    """Borrow the list counter from a fresh watcher state file describing the same
    list — a standalone probe has no history of its own."""
    if state.get("list_version"):
        return state
    prev = read_json(STATE_PATH, None)
    if (
        state_is_fresh(prev)
        and prev.get("session") == state.get("session")
        and prev.get("list_id") == state.get("list_id")
    ):
        state["list_version"] = prev.get("list_version")
    return state


def bar_text(state: dict) -> str:
    todos = state.get("todos") or []
    if not todos:
        return "todos -"
    done = sum(1 for t in todos if t.get("completed"))
    return f"todos {done}/{len(todos)}"# ======================================================================== lock
# In plain words: a claim is a file HOLDING A LOCK THE OPERATING SYSTEM OWNS. The number
# is written inside the file for a person reading the scratch dir, but the exclusion comes
# from the kernel, because a number is not evidence: a pid can come round again, and a
# process that answers `kill -0` may never have been the watcher at all — which is how a
# record left behind by a crash could hold the next watcher off, and how a pid reused by
# something else could make a dead watcher look alive. The kernel drops the lock when the
# process that took it ends, however it ends, so nothing has to be cleaned up to be
# correct: a claim that cannot be taken is live, and one that can be taken is not.
_LOCK_FDS: dict[str, int] = {}


def lock_open(path: str) -> int:
    """A 0600 fd for the claim file, created if it is not there yet."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(fd, 0o600)
    except OSError:
        pass
    return fd


def lock_take(fd: int) -> bool:
    """True when this fd now holds the exclusive claim. Never blocks."""
    if fcntl is None:  # pragma: no cover - a platform without flock keeps the old guard
        return True
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def lock_ours(path: str) -> bool:
    """Is `path` still the SAME claim this process took — not a file that replaced it?"""
    fd = _LOCK_FDS.get(path)
    if fd is None:
        return False
    try:
        return os.fstat(fd).st_ino == os.stat(path).st_ino
    except OSError:
        return False


def lock_holder(path: str) -> int | None:
    """The pid holding the claim on `path`, or None when nobody does.

    `flock` cannot say WHO holds a lock, so this asks the one question that does have an
    answer: can the claim be taken? If it can, nobody holds it — and the record sitting
    there is a leftover, so it is removed instead of believed (a reader that believed it
    would report a watcher that is not running). If it cannot, the pid inside the record
    is the holder's: it was written by whoever took the claim, and the claim is still held.
    """
    fd = lock_open(path)
    try:
        if lock_take(fd):
            if fcntl is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                os.unlink(path)  # free, so the record is a leftover
            except OSError:
                pass
            return None
        rec = read_json(path, {}) or {}
        pid = rec.get("pid")
        # 0 means "held, and the record says nothing": the caller refuses to start rather
        # than run a second watcher over one scratch dir, but has no number to print.
        return int(pid) if pid else 0
    finally:
        os.close(fd)


def daemon_pid() -> int | None:
    return lock_holder(LOCK_PATH)


def write_lock(cwd: str, instance_pid, path: str | None = None,
               extra: dict | None = None) -> bool:
    """Claim `path` for this process, and write the record a reader sees.

    `path` exists for the NAS watcher and the pane keeper, whose claims on the scratch dir
    are their own files: three roles, three claims, and they must not block each other.
    `version` lets a reader tell a live watcher from one left behind by an upgrade.

    False when someone else holds it — the caller must stop rather than run a second
    watcher over one scratch dir. The record is written THROUGH the locked fd and never by
    rename: replacing the file would leave the new copy unlocked and the claim lost.
    """
    target = path or LOCK_PATH
    fd = _LOCK_FDS.get(target)
    if fd is None:
        fd = lock_open(target)
        if not lock_take(fd):
            os.close(fd)
            return False
    record = {
        "pid": os.getpid(),
        "instance_pid": instance_pid,
        "cwd": cwd,
        "started_ms": int(time.time() * 1000),
        "version": VERSION,
    }
    record.update(extra or {})
    payload = json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\n"
    try:
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, payload)
        os.fsync(fd)
    except OSError:
        pass
    _LOCK_FDS[target] = fd  # held until this process ends; the kernel frees it then
    return True


def claim_or_force(path: str, cwd: str, instance_pid=None, force: bool = False,
                   extra: dict | None = None) -> bool:
    """Claim `path` — and with `--force`, stop whoever holds it first.

    `--force` cannot mean "run a second watcher anyway": two processes writing one state
    file is the interference this claim exists to prevent. It means "the one that is
    running is in the way", so it is asked to stop (SIGTERM, then a bounded wait for the
    kernel to drop its lock) and the claim is taken once it is free.
    """
    if write_lock(cwd, instance_pid, path=path, extra=extra):
        return True
    if not force:
        return False
    pid = lock_holder(path)
    if pid:
        try:
            os.kill(int(pid), signal.SIGTERM)
        except OSError:
            pass
        for _ in range(40):
            if not pid_alive(int(pid)):
                break
            time.sleep(0.05)
    return write_lock(cwd, instance_pid, path=path, extra=extra)


def clear_lock(pid: int | None = None, path: str | None = None) -> None:
    """Give up the claim and remove the record — never stealing one that is live.

    Unlinking a file another process holds the claim on does NOT lift that claim: the
    holder keeps its inode, and a third process could then create a fresh file and claim
    that, so two watchers would own one scratch dir. So a claim is only removed when it is
    OURS to remove: held by this process, or free (a leftover).
    """
    target = path or LOCK_PATH
    fd = _LOCK_FDS.pop(target, None)
    if fd is None:
        if pid is not None:
            rec = read_json(target, {}) or {}
            if rec.get("pid") not in (None, pid):
                return  # someone else owns it now
        lock_holder(target)  # removes the record itself when the claim is free
        return
    try:
        os.unlink(target)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass


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
    fparts = [
        f"{src} {ferr[src]['med']:.2f}x over {ferr[src]['n']}"
        for src in ("shape", "blend", "pace")
        if ferr.get(src)
    ]
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
        for rung in ("pace", "blend", "shape"):
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
    shown = rows if not limit else rows[:limit]
    for r in shown:
        span = short_duration(r["span_ms"], seconds=True) if r["span_ms"] else "—"
        label = r["label"] if len(r["label"]) <= 62 else r["label"][:61] + "…"
        out.append(f"  {span:<8} {label:<62} {c('2', fmt_age(r['started_ms'], now_ms))}")
        bits = []
        for rung in ("pace", "blend", "shape"):
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
        # The frame is built to fill the pane exactly; a trailing newline on a full
        # frame scrolls the top border off the screen on every single repaint.
        rows = _shutil.get_terminal_size(fallback=(80, 24)).lines
        tail = "" if len(text.splitlines()) >= rows else "\n"
        sys.stdout.write("\x1b[H\x1b[2J" + text + tail)
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
