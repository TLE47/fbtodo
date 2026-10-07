"""The CLI transcript: one journal read, folded into the fields of a state.

The journal is append-only and megabytes long, so this is where the reading is cheap: records
are parsed as they are met, chunk answers are remembered per file, and a journal that only
grew since the last poll is folded from its cursor rather than walked again.
"""

from __future__ import annotations

import copy
import json
import os
import re
import zlib
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


# ---- idle dead time: no bare `sleep`, no poll loop with nothing between -----------------
# AGENTS.md tells a session never to leave dead time — no `sleep` merely to wait, and no poll
# loop with no useful work between the polls. That is a claim about what a session DID, and
# the journal records every call it made in order with its timestamp, so the rule is checkable
# from the transcript instead of from the session's own word for itself.
#
# The walk below runs over EVERY call, not over the recounted actions above: `read_files` is
# left out of `ACTION_TOOLS` on purpose (reading is not progress), and a poll loop interleaved
# with reads is not a loop with nothing between the polls. Asking this question of the action
# list would flag exactly the sessions that were working.
BARE_SLEEP_RE = re.compile(r"^sleep\s+(?P<secs>\d+(?:\.\d+)?)s?$")


POLL_RUN = 3  # this many calls of one command in a row, with nothing else called between


DEAD_TIME_TAIL = 2 * 1024 * 1024  # bytes of journal read back when looking for dead time


DEAD_TIME_KEEP = 4000  # calls remembered; a session that made more has a bigger problem


def _record_calls(rec: dict, key) -> list:
    """(key, ts_ms, tool, what) for EVERY call in this record, in the order it made them.

    `_record_actions` above names only the calls worth showing in the pane. This is the same
    walk without that filter, because "nothing happened between the polls" is a question about
    the calls that were NOT shown as much as about the ones that were.
    """
    ts = _iso_ms(rec.get("timestamp"))
    out = []
    for call in (rec.get("data") or {}).get("toolCalls") or []:
        if not isinstance(call, dict):
            continue
        name = call.get("toolName")
        if not name:
            continue
        inp = call.get("input") or call.get("args") or {}
        out.append((key, ts, str(name), _action_what(name, inp)))
    return out


def journal_calls(chat_dir: str) -> list:
    """Every tool call in a session's journal, oldest first: (key, ts_ms, tool, what).

    Only the tail is read (`DEAD_TIME_TAIL`): dead time is a property of what a session is
    doing, and the start of a long journal is not. Parsing is needle-gated exactly the way the
    pane's own walk gates it, so a megabyte of prose costs one `in` test per line and no json.

    The relative order of the calls in one record is the order the session made them, and the
    keys sort records against each other — so an action list built from this needs no re-sort.
    """
    try:
        with open(os.path.join(chat_dir, "log.jsonl"), "rb") as fh:
            fh.seek(0, os.SEEK_END)
            start = max(0, fh.tell() - DEAD_TIME_TAIL)
            fh.seek(start)
            blob = fh.read()
    except OSError:
        return []
    if start:
        blob = blob.split(b"\n", 1)[-1]  # a partial first line is not a record
    rows: list = []
    for order, raw in enumerate(blob.split(b"\n")):
        if b'"toolCalls"' not in raw or b'"toolName"' not in raw:
            continue
        try:
            rec = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            continue
        rows.extend(_record_calls(rec, (0, order)))
        if len(rows) >= DEAD_TIME_KEEP:
            break
    return rows[:DEAD_TIME_KEEP]


def _bare_sleep_seconds(row) -> float | None:
    """The seconds this call is *nothing but* a sleep, else None.

    Only a command that is ONLY a sleep counts. `sleep 5 && curl …` is work that happens to
    wait, and `sleep 600 &` leaves a process behind rather than holding anyone; flagging either
    would teach the rule's reader to ignore it, which costs more than the miss.
    """
    if row[2] != "run_terminal_command":
        return None
    match = BARE_SLEEP_RE.match((row[3] or "").strip())
    return float(match.group("secs")) if match else None


def _span_s(first, last) -> float | None:
    """Seconds between two journal timestamps, or None when either is missing."""
    if first is None or last is None:
        return None
    return round(max(0.0, (last - first) / 1000.0), 1)


def idle_dead_time(calls, *, poll_run: int = POLL_RUN, min_sleep_s: float = 0.0) -> list:
    """The never-idle rule's violations in a session's calls, oldest first.

    `calls` is what `journal_calls` yields — `(key, ts_ms, tool, what)` — in any order; this
    sorts by key, and the sort is stable, so calls sharing a record keep the order they were
    made in. Two shapes are violations, and both are about what the session DID:

      * **bare sleep** — the call is nothing but a sleep (`sleep 30`), so the window it takes
        is dead time by construction. `min_sleep_s` ignores sleeps shorter than that (0 by
        default: a bare sleep is a bare sleep, whatever it was waiting for).
      * **poll loop** — `poll_run` or more calls of the SAME command in a row with no other
        call between them: the session is waiting rather than working. Any other call breaks
        the run, including a read.

    Each violation is a dict: `kind`, `what`, `seconds` (bare-sleep), `count` (poll-loop),
    `key` (where in the journal it starts) and `span_s` (the wall time it covered, when the
    records carry timestamps).
    """
    rows = sorted(calls, key=lambda r: r[0])
    out: list = []
    i = 0
    while i < len(rows):
        secs = _bare_sleep_seconds(rows[i])
        if secs is not None:
            if secs >= min_sleep_s:
                out.append({"kind": "bare-sleep", "what": rows[i][3], "seconds": secs,
                            "count": 1, "key": rows[i][0], "span_s": secs})
            i += 1
            continue
        # A run of the SAME call, with nothing else called in between. A call with no `what`
        # is not nameable, so it can neither form a run nor be mistaken for one.
        if not rows[i][3]:
            i += 1
            continue
        j = i + 1
        while (j < len(rows) and rows[j][2:] == rows[i][2:]
               and _bare_sleep_seconds(rows[j]) is None):
            j += 1
        if j - i >= poll_run:
            out.append({"kind": "poll-loop", "what": rows[i][3], "seconds": None,
                        "count": j - i, "key": rows[i][0],
                        "span_s": _span_s(rows[i][1], rows[j - 1][1])})
        i = j
    return out


def journal_dead_time(chat_dir: str, **kw) -> list:
    """`idle_dead_time` over a session's own journal — one call to ask the rule of a session."""
    return idle_dead_time(journal_calls(chat_dir), **kw)


def dead_time_report(violations) -> list:
    """One line per violation, oldest first: what a log or a terminal says about it.

    Kept beside the rule so the wording and the detection cannot drift apart — the phase that
    enforces this reads these lines, not a second copy of them.
    """
    out = []
    for v in violations:
        if v["kind"] == "bare-sleep":
            out.append(f"dead time: a bare `sleep {v['seconds']:g}` — nothing else was called")
        else:
            span = f", over {v['span_s']:g}s" if v.get("span_s") is not None else ""
            out.append(f"dead time: `{v['what']}` called {v['count']}x with nothing between"
                       f"{span}")
    return out


def goal_line(text: str):
    """The first `Goal:` line in a block of the agent's prose, cleaned — or None.

    AGENTS.md asks every task to open with `Goal: …`, and the agent's own words are the one
    place a goal the *agent* chose can come from; a request next to it is the user's
    wording, typos and all. Shared by the CLI journal (`_record_goal`, reading
    `fullResponse`) and the desktop app's live history (`harness_state`), which keep the
    same prose in different shapes.
    """
    if not isinstance(text, str) or not text:
        return None
    for line in text.splitlines():
        match = GOAL_LINE_RE.match(line)
        if match:
            return " ".join(match.group(1).split()).strip(" *_`") or None
    return None


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
    return goal_line(data.get("fullResponse"))


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


def _goal_choice(goals, prompts, hit_key):
    """The `(position, text)` of the heading that belongs to the list at `hit_key`, or None.

    In plain words: which heading goes with which list is a question about TURNS, and a turn
    is bracketed by its prompt — prose and tool calls are separate iterations, so a turn's
    heading can be written AFTER the list it belongs to. Hence three positions, tried in
    order: a heading at or before the list (the ordinary same-turn case), a heading before
    the newest request when the list has no heading of its own (the agent stated it as the
    turn opened), and — only when no request came after the list — a heading after it. No
    list at all means the newest heading is the statement of what is going on. Split out of
    `pick_goal` so the staleness rule below can read the heading's POSITION as well as its
    words.
    """
    if hit_key is None:
        return _newest(goals)
    newer = [(k, t) for k, t in prompts if k > hit_key]
    asked = _newest(prompts)
    return (
        _newest((k, t) for k, t in goals if k <= hit_key)
        or _newest((k, t) for k, t in goals if asked and k < asked[0])
        or (_newest((k, t) for k, t in goals if k > hit_key) if not newer else None)
    )


def pick_goal(goals, prompts, hit_key) -> str | None:
    """The agent's `Goal:` heading that belongs to the list at `hit_key` — or None.

    `goals` and `prompts` are `(position, text)` pairs in history order; the positions order
    the same way in the journal and in the app's live history, so this reads identically for
    both, which is the whole reason it is here rather than written twice.
    """
    stated = _goal_choice(goals, prompts, hit_key)
    return stated[1] if stated else None


def goal_stale_at(goals, prompts, hit_key) -> bool:
    """Is the heading in hand left over from an EARLIER turn than the list it heads?

    In plain words: a heading belongs to the turn that wrote it, and a turn is bracketed by
    its request — so the heading is the shown list's own only when it is not older than the
    request that opened that list's turn. A heading written before that request belongs to an
    earlier turn, and a pane drawing it over this list would be heading the work with the last
    turn's objective. The request that opened the shown list's turn is the newest one at or
    before `hit_key`; with no such request (or no list) there is nothing to prove, so this is
    False rather than a guess. Shared by the CLI journal and the app's live history so a stale
    heading is reported the same way by both.
    """
    stated = _goal_choice(goals, prompts, hit_key)
    if not stated or hit_key is None:
        return False
    opened = _newest((k, t) for k, t in prompts if k <= hit_key)
    return bool(opened) and stated[0] < opened[0]


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
    # The newest edit across both halves: both walk newest-first, so the larger key is the
    # later edit, whichever chunk it was parsed in.
    edit_keys = [k for k in (newer.get("last_edit_key"), older.get("last_edit_key"))
                 if k is not None]
    return {
        "start_ms": older.get("start_ms") or newer.get("start_ms"),
        "iterations": int(newer.get("iterations") or 0) + int(older.get("iterations") or 0),
        "verbs": counts,
        "files": files[:FILE_KEEP],
        "last_edit_key": max(edit_keys) if edit_keys else None,
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
    turn = {"start_ms": None, "iterations": 0, "verbs": {}, "files": [],
            "last_edit_key": None, "open": True}
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
                    if verb in EDIT_VERBS and (
                        turn["last_edit_key"] is None or key > turn["last_edit_key"]
                    ):
                        # Where in the journal this turn last touched a file. The
                        # boundary guard compares it with the newest `write_todos` to
                        # ask whether the list was told about the work (see
                        # `files_unlisted` in `scan_live_log`).
                        turn["last_edit_key"] = key
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
    scan = {"hit": None, "goal": None, "goal_source": None, "goal_stale": False, "now": None,
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
        # heading of a turn can be written after the list it belongs to. That choice is
        # `pick_goal`'s, shared with the desktop app's live history so the two readers of
        # agent prose cannot answer it differently.
        asked = _newest(prompts)
        scan["goal"] = pick_goal(goals, prompts, hit_key)
        if scan["goal"]:
            scan["goal_source"] = "agent"
            # ...and whether that heading is left over from an earlier turn than the list
            # it heads — the same rule the desktop source applies, so a stale heading is
            # reported identically by both (see `goal_stale_at`).
            scan["goal_stale"] = goal_stale_at(goals, prompts, hit_key)
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
        # The boundary guard (docs/OPEN-PROBLEMS.md, direction 5): this turn ENDED having
        # edited files but having published no `write_todos` since its own last edit — so
        # the list does not account for the work that just landed, and neither the pane's
        # "all done" nor the completion bell may vouch for it. Computed here because this
        # is the only place that holds both halves: the turn's edits and the newest list's
        # position in the journal. `last_edit_key` is a walk key (a tuple), so it is read
        # and dropped here rather than carried into the JSON state.
        last_edit = scan["turn"].pop("last_edit_key", None)
        edits = scan["turn"].get("files") or []
        scan["files_unlisted"] = bool(
            scan["turn_ended"] and edits
            and (hit_key is None or (last_edit is not None and last_edit > hit_key))
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


# How much of a journal's end the LIVENESS read looks at: the agent's newest records are
# within a few lines of the file's end, so a bounded tail is enough to name them, and a
# liveness question is asked on every poll of every `auto` pane.
LIVE_TAIL_BYTES = 256 * 1024


# What marks a record as the AGENT's rather than the app's: the turn boundary (`prompt` on
# the request that opened a turn, `shouldEndTurn` on every iteration), its prose
# (`fullResponse`) and its tool calls. The desktop app appends its own records to the same
# journal — `cli.feedback_button_hovered`, a note saved, a tab closed — and those carry
# none of these, which is the whole point of asking.
AGENT_RECORD_NEEDLES = (b'"shouldEndTurn"', b'"prompt"', b'"fullResponse"', b'"toolCalls"')

_LIVE_CACHE: dict = {}


def journal_liveness(chat_dir: str) -> tuple:
    """`(agent_ms, ended)` — when the AGENT last wrote to this journal, and whether it stopped.

    Not the file's mtime, which is what liveness used to be judged by: the desktop app appends
    its own records to the same journal (`cli.feedback_button_hovered`, a note saved, a tab
    closed), so a chat whose turn ended at 11:20 read as "just written" at 12:04 and `auto` kept
    answering a live desktop thread with that finished list, frozen (measured 2026-10-04: an
    `ALL DONE` 6/6 pane under a thread working in the app, unchanged across two tabs). Only the
    agent's own records are read, newest first, and the first turn boundary among them decides:

    * a `shouldEndTurn: true` record is the agent saying it has finished and is waiting for
      you, so `ended` is True — not the session WORKING here, whatever else has touched the
      file since;
    * a `prompt` record opens a turn, so a bare `continue` puts the session back to work with
      no clock involved at all.

    A tail with no boundary in it (a journal longer than the tail, or one holding nothing but
    prose) is not a guess: `ended` is False and the newest agent record still answers, so an
    unusual journal falls back to the quiet window as it always did.
    """
    path = os.path.join(chat_dir, "log.jsonl")
    try:
        st = os.stat(path)
        key = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
        cached = _LIVE_CACHE.get(path)
        if cached is not None and cached[0] == key:
            return cached[1]
        with open(path, "rb") as fh:
            start = max(0, st.st_size - LIVE_TAIL_BYTES)
            fh.seek(start)
            blob = fh.read()
    except OSError:
        return (None, False)
    agent_ms = None
    # Newest first, and the first boundary found is the answer — the walk stops there rather
    # than reading the tail to its end, so this costs a handful of lines on a live journal.
    lines = blob.split(b"\n")
    if start > 0:
        lines = lines[1:]  # the first line here began before the window and is half a record
    for raw in reversed(lines):
        if not raw.strip() or not any(n in raw for n in AGENT_RECORD_NEEDLES):
            continue
        try:
            rec = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            continue
        data = rec.get("data")
        if not isinstance(data, dict):
            continue
        ts = _iso_ms(rec.get("timestamp"))
        agent_ms = ts if ts else agent_ms
        ended = data.get("shouldEndTurn")
        if isinstance(ended, bool):
            answer = (agent_ms, ended)
            break
        if isinstance(data.get("prompt"), str) and data["prompt"].strip():
            answer = (agent_ms, False)   # a new request: working again
            break
    else:
        answer = (agent_ms, False)
    if len(_LIVE_CACHE) > 8:  # a long-lived pane that keeps switching sessions
        _LIVE_CACHE.clear()
    _LIVE_CACHE[path] = (key, answer)
    return answer


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
    # The heading can be left over from an earlier turn than the list it heads (see
    # `goal_stale_at`); carried on the state so the pane can badge it rather than head this
    # list with the previous turn's objective. The key rides WITH the goal: no heading, no
    # staleness, and no field — the same shape the desktop source keeps.
    if state["goal"] and scan.get("goal_stale"):
        state["goal_stale"] = True
    # Has the agent finished this turn (and is it therefore waiting for you)? Read from the
    # same backward pass; the bell rings on "all todos done AND turn ended", so a long
    # command that merely leaves the journal quiet never rings it.
    state["turn_ended"] = bool(scan.get("turn_ended"))
    # The boundary guard's own fact: the turn ended with file edits the list never got
    # told about. Carried on the state so the renderers and the bell read one answer
    # rather than each recomputing it (the bell only ever sees the state file).
    state["files_unlisted"] = bool(scan.get("files_unlisted"))
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


__all__ = [
    "_iso_ms", "_record_todos", "ACTION_TOOLS", "ACTION_KEEP", "ACTION_ROWS",
    "ACTION_SCAN_KEEP", "FILE_KEEP", "EDIT_VERBS", "TURN_CHASE_CHUNKS", "QUIET_MS",
    "_action_what", "_record_actions", "_record_goal", "prose_text", "_record_summary",
    "BARE_SLEEP_RE", "POLL_RUN", "DEAD_TIME_TAIL", "DEAD_TIME_KEEP", "_record_calls",
    "journal_calls", "idle_dead_time", "journal_dead_time", "dead_time_report",
    "_record_prompt", "_record_turn", "is_nudge", "pick_prompt", "_newest", "_turn_extend",
    "goal_line", "pick_goal", "goal_stale_at",
    "journal_scan", "_SCAN_CACHE", "_SCAN_HITS", "_SCAN_PARSED", "_SCAN_REUSED",
    "CACHED_CHUNKS", "scan_live_log", "read_cli", "journal_liveness",
    "LIVE_TAIL_BYTES", "AGENT_RECORD_NEEDLES",
]
