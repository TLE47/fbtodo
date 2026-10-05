"""The desktop app's store: one SQLite project, read per turn — and all its live threads."""

from __future__ import annotations

import glob
import json
import os
import sqlite3
import time
from .base import *  # noqa: F401,F403 — the package is one namespace
# `is_nudge` / `pick_prompt` / `_newest` / `goal_line` are the CLI journal's own reading of
# the request stream, reused here so the desktop's `now`/`nudge` cannot drift from the CLI's.
from .scan import *  # noqa: F401,F403 — the package is one namespace

# ===================================================== several threads at once
# The app runs more than one thread per project (each tab is one, and a thread can be left
# running while you open another), but the store only ever holds the transcript: a list is
# written per turn, so "is this thread live?" is a question about how recently one was
# written. Two windows, both about lists rather than about wall clocks: a thread whose list
# moved inside `DESKTOP_LIVE_MS` is one a pane should still show, and one whose list moved
# inside `DESKTOP_RUNNING_MS` is the one that is working right now.
DESKTOP_LIVE_MS = 90 * 60 * 1000

DESKTOP_RUNNING_MS = 3 * 60 * 1000

# How stale a thread's own ask may be and still count as "where the app is being worked right
# now" — the window inside which a correction of the app's persisted tab is allowed at all.
# It is deliberately the SAME window `DESKTOP_LIVE_MS` already uses ("a thread whose list moved
# inside this is one a pane should still show"), because the correction is sized by the GAP it
# exists to cover and not by a guess about how long you work: the app's `activeId` write is
# debounced and was measured ten minutes and more behind a live turn (2026-10-04: the file
# unchanged for 10m while a thread worked), and a turn that runs longer than a narrow window
# would leave the pane falling back to the tab you had already left half-way through it. Past
# the window the app's answer is taken again — by then the write it was waiting for has landed.
DESKTOP_FOCUS_MS = DESKTOP_LIVE_MS

# How many threads a pane will show including the one it follows. Four fits a pane that is
# worth reading (a heading and a step or two each); `--threads 0` is the old answer, the
# followed thread alone.
DESKTOP_MAX_THREADS = 4

# How a committed row says WHO asked: the app writes every user message with the `input_id` of
# a `queue_items` receipt, and that row's `source` is the app's own word for the asker — `user`
# for the person (typed, queued or steered), `assistant` for a prompt the app wrote for itself
# (an auto-run decision, a suggestion, a sponsored task), `mission-*` for a campaign and `skill`
# for a skill activation — while `kind` says whether the row is a request at all (`prompt`
# versus `skill-context` or `close-tab`). The harness tags the same distinction `USER_PROMPT`
# on its live history; this is the committed half of that tag, read from the app bundle
# (`MessagesRepo.append` / `Ledger.accept`) and measured against the live stores 2026-10-04.
# ...except one prompt, which is the app's OWN continuation: the Continue button shown while a
# turn is interrupted sends a fixed line through the ordinary send path, so its row is marked
# exactly like a typed prompt and only its words give it away (the button calls the composer's
# own send in the UI bundle the app serves on 127.0.0.1). A resume is not an ask: the app
# pressing its own button must not become the pane's `now` or `nudge`.
DESKTOP_RESUME_PROMPTS = ("continue the interrupted request from where you left off.",)


def _table_exists(cur, name: str) -> bool:
    """Does this store carry a table called `name`? A miss is False, never an error."""
    try:
        return bool(cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone())
    except sqlite3.Error:
        return False


def _receipt_is_request(source, kind) -> bool:
    """Does the app's receipt say the USER asked for this row?

    `None` on both means the row has no receipt this store offers at all — an older store
    without `queue_items`, or a row something other than the app wrote — and the request
    STANDS: refusing it would silently empty a pane rather than hide an injection, which is
    the safer way to be wrong.
    """
    if source is None and kind is None:
        return True
    return (source or "user") == "user" and (kind or "prompt") == "prompt"


def _is_resume_prompt(text) -> bool:
    """Is this the app's own resume line rather than words the user wrote?"""
    return " ".join((text or "").split()).casefold() in DESKTOP_RESUME_PROMPTS


# ============================================================ desktop backend
def project_path_of(db: str):
    return (read_json(os.path.join(os.path.dirname(db), "project.json"), {}) or {}).get(
        "projectPath"
    )


def same_path(a, b) -> bool:
    return bool(a) and bool(b) and os.path.realpath(a) == os.path.realpath(b)


def pick_db(pattern: str, project: str | None, cwd: str | None = None,
            own_only: bool = False) -> str | None:
    """The store that answers here: the named `project`, else the one this `cwd` belongs to.

    With a `project` the answer is the store whose `project.json` names it, through three
    widening steps (its path, its name inside the store's path, its path as a substring), so
    a caller who names a project gets that project or nothing.

    With no `project` the cwd decides, and the DEEPEST project that contains it wins. Ranking
    by depth rather than by which store moved last is what keeps a broad project from
    shadowing a narrow one: an app opened on `~/myapp` is the store for `~/myapp/src` even
    when a second project was touched more recently, and a project opened on the home
    directory — the one path that contains every path there is — must never speak for a
    directory that has a project of its own.

    `own_only` narrows that to the project whose path IS this directory. `auto` asks each
    store for "the session working in THIS directory", and it already asks the CLI journal
    exactly that way: `cli_chat_dir` reads `basename(cwd)` and never a parent, so a directory
    under the home directory sorts a chat by its own name or finds none. Measured 2026-10-03:
    with `auto` walking up, a pane in `~/Projects/fbtodo` answered the home project's thread —
    this very session's list, drawn over a repository that the app had never opened — while
    the repository's own CLI chat was right there. An explicit `-s desktop` has not been told
    which directory, so it keeps the wider walk; only `auto` narrows.
    """
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
    here = os.path.realpath(cwd or os.getcwd())
    # `(exact, -depth, index)`, smallest first: the cwd's own project beats every ancestor of
    # it, a deeper ancestor beats a shallower one, and two projects naming the same path fall
    # back to the store that moved last (`dbs` is newest first, so a smaller index is newer).
    ranked = []
    for i, db in enumerate(dbs):
        pp = project_path_of(db)
        if not pp:
            continue
        rp = os.path.realpath(pp)
        if here == rp:
            ranked.append((0, 0, i, db))
        elif here.startswith(rp + os.sep) and not own_only:
            ranked.append((1, -rp.count(os.sep), i, db))
    if ranked:
        return min(ranked)[3]
    # Nothing here claims this directory. A caller who asked for the store by name still gets
    # it (the newest one is the app's current project, which is the best guess there is); one
    # that asked for THIS directory's session gets nothing, and says so.
    return None if own_only else dbs[0]


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


def thread_columns(cur) -> set:
    """The columns this store's `threads` table actually has.

    The app's schema moves with its version: measured on this machine, one project's
    `desktop-v2.db` carries `sidebar_archived_at` and another's does not, so a query that
    NAMES a column an older store was written without fails the whole read — the pane then
    has no list at all, from a store that is perfectly readable. A column that is not there
    cannot be a reason to put a thread away, which is what the empty answer turns into.
    """
    try:
        return {row[1] for row in cur.execute("PRAGMA table_info(threads)")}
    except sqlite3.Error:
        return set()


def live_threads(cur, followed, others: int, now_ms: int, window_ms: int) -> list:
    """The OTHER live threads of this store, newest list first — one entry each.

    `others` is how many to look for (0 asks for none, which is what every build did before
    a pane could show more than one). A thread is live when its newest `write_todos` is
    inside `window_ms` (0 = no window at all) and the app has not been told to put it away:
    a thread whose status is closed, or that sits in the sidebar's archive, is one the
    owner has already filed, and a pane that kept showing it would be arguing with the
    sidebar. The list itself is the newest one in that thread, exactly as it is for the
    thread being followed.
    """
    if others <= 0:
        return []
    cols = thread_columns(cur)
    status = "t.status" if "status" in cols else "NULL"
    archived = "t.sidebar_archived_at" if "sidebar_archived_at" in cols else "NULL"
    rows = cur.execute(
        "SELECT m.thread_id, m.ts, json_extract(part.value,'$.input.todos'), t.title,"
        f" {status}, {archived}, m.seq, part.key"
        " FROM messages m JOIN threads t ON t.id = m.thread_id"
        " JOIN json_each(m.parts_json) AS part"
        " WHERE m.parts_json LIKE '%write_todos%'"
        " AND json_extract(part.value,'$.toolName') = 'write_todos'"
        # One message row holds a whole turn — every `write_todos` the agent made in it,
        # as separate parts in call order — so the LIST is the last of them, not the first
        # one `json_each` happens to hand over (measured 2026-10-03: a finished turn drew
        # as `0/6` because the turn's opening list was read instead of its final one).
        " ORDER BY m.ts DESC, m.seq DESC, part.key DESC"
    ).fetchall()
    # ...fetched to the end as well: the prose read below runs on the SAME cursor, and a
    # query inside an unfinished iteration would cancel what is left of it.
    out: list = []
    seen = {followed}
    for tid, ts, todos_json, title, status, archived, seq, part in rows:
        if tid in seen or status == "closed" or archived:
            continue
        seen.add(tid)
        if window_ms and ts and now_ms - ts > window_ms:
            continue
        try:
            todos = json.loads(todos_json or "[]")
        except Exception:
            continue
        if not todos:
            continue
        row = {
            "id": tid,
            "title": title or "",
            "todos": todos,
            "current": False,
            "running": bool(ts and now_ms - ts <= DESKTOP_RUNNING_MS),
            "source_updated_ms": ts,
        }
        # ...and the row is read the way the followed thread's own read reads it: the live
        # turn FIRST (a running thread shows the in-flight list from `harness_state`, its own
        # heading included — otherwise a board row and a pane row about the same running
        # thread would disagree, which is exactly what the cross-reader invariant forbids),
        # then the committed-prose rules for everything the store has closed. A thread whose
        # store keeps no prose simply carries neither `goal` nor `goal_stale`.
        mini = {"thread": tid, "seq": seq, "list_part": part, "todos": todos, "ts": ts}
        _live_turn(cur, mini, tid, now_ms)
        if mini.get("live_list"):
            row["todos"] = mini.get("todos") or []
            if mini.get("ts"):
                row["source_updated_ms"] = mini["ts"]
        else:
            _committed_prose(cur, mini)
        if mini.get("goal"):
            row["goal"] = mini["goal"]
        if mini.get("goal_stale"):
            row["goal_stale"] = True
        out.append(row)
        if len(out) >= others:
            break
    return out


# The app commits a message row only when a turn CLOSES, so the transcript says nothing
# about the work in flight and a running thread looks exactly like one that never called
# `write_todos` — the pane's old "no write_todos call yet in this session", which reads as
# "the agent forgot" when the truth is "the store has not committed yet". The `threads` row
# DOES move mid-turn, though: `turn_state` goes `running`, `turn_alive_at` is a heartbeat,
# and `harness_state` is the agent's OWN state, rewritten as it works — and the only place a
# running turn's list exists before it is committed. Both are read here, so a running
# desktop thread shows the live list and its turn the way the CLI's journal does.
# Guarded by `thread_columns`: an older store may not carry a column at all.
def turn_running(cur, tid, now_ms: int) -> bool:
    """Is this thread's turn alive right now, per the store's own live signal?"""
    if not tid:
        return False
    cols = thread_columns(cur)
    if "turn_state" not in cols:
        return False
    row = cur.execute("SELECT turn_state FROM threads WHERE id = ?", (tid,)).fetchone()
    if not row or str(row[0] or "").lower() != "running":
        return False
    if "turn_alive_at" not in cols:
        return True
    beat = cur.execute("SELECT turn_alive_at FROM threads WHERE id = ?", (tid,)).fetchone()
    beat = int((beat or [0])[0] or 0)
    # A `running` state with a stale heartbeat is a turn that died, not one in flight: the
    # app rewrites the beat every few seconds while it works.
    return not (beat and now_ms - beat > DESKTOP_RUNNING_MS)


def focused_thread(cur, now_ms: int, window_ms: int = DESKTOP_FOCUS_MS, prefer=None) -> tuple:
    """`(thread, asked_ms, note)` — the thread this store is being WORKED in right now.

    The app's own answer to "which tab are you in" is `workspace.activeId`, and it is only
    ever as fresh as the app's next write of that file: measured 2026-10-04, a tab switch
    reached the disk minutes later, so a pane reading only that file draws the tab you have
    already left, for minutes at a time. Nothing in the store is a focus flag either — no
    column, no table, no receipt names the tab in front of you — and the app's live channels
    (its orchestrator API and its CDP bridge, both on 127.0.0.1) answer `401` to anything that
    is not the app's own process, which is where their tokens live. So this is the next honest
    thing: a thread whose last ask is inside `window_ms` is one you are working in, because
    sending is what a person does in the tab they are looking at.

    Deliberately narrow, because a pane that guesses wrong is worse than one that is late:

    * only threads whose `turn_state` is `running` count, and only the per-thread ask clock
      is read — never `turn_alive_at`, which this store writes ONE VALUE FOR ALL THREADS
      (measured: three threads, three identical heartbeats), so it cannot say which thread is
      yours and a liveness test built on it would say every tab is live at once;
    * `prefer` is the app's persisted tab, and it WINS whenever it is itself among the live
      ones: when the file and the work agree, nothing moves under the reader;
    * with several threads live and none of them the persisted tab, the newest ask wins —
      the tab you typed in last — and `note` says which tab was passed over and how long ago
      this one was asked for;
    * a store written by an older app carries none of these columns and answers `(None, None,
      None)`, leaving `activeId` exactly as it was.
    """
    cols = thread_columns(cur)
    if "turn_state" not in cols:
        return (None, None, None)
    clock = next((c for c in ("last_prompt_at", "updated_at") if c in cols), None)
    if not clock:
        return (None, None, None)
    try:
        rows = cur.execute(
            f"SELECT id, {clock} FROM threads WHERE turn_state = 'running' AND {clock} IS NOT NULL"
        ).fetchall()
    except sqlite3.Error:
        return (None, None, None)
    live = []
    for tid, asked in rows:
        asked = int(asked or 0)
        if asked and now_ms - asked <= window_ms:
            live.append((tid, asked))  # else: a turn that ended, or one you walked away from
    if not live:
        return (None, None, None)
    for tid, asked in live:
        if tid == prefer:
            return (prefer, asked, None)
    best = max(live, key=lambda pair: pair[1])
    note = None
    if prefer:
        # Named in the pane's title, so a list that changes tab under the reader says which tab
        # the app's file still names and how long ago this one was asked for.
        ago = max(0, (now_ms - best[1]) // 60_000)
        note = f"{str(prefer)[:8]} behind → {best[0][:8]} asked {ago}m ago"
    return (best[0], best[1], note)


# One walk over a thread's live history, read in SQLite rather than in Python. The blob
# grows with the whole session (`messageHistory` is every turn's) and a pane ticks once a
# second, so pulling it into Python and discarding it is the cost this avoids. The CASE
# columns keep the transfer to what is needed: a text part's text (never a reasoning part's,
# which can be long) and a `write_todos` call's list. Rows come back in history order, so the
# last usable value of each kind is the newest.
_HARNESS_SQL = (
    "SELECT msg.key, part.key, json_extract(msg.value, '$.role'),"
    "       json_extract(msg.value, '$.tags'),"
    "       json_extract(msg.value, '$.sentAt'),"
    "       json_extract(part.value, '$.type'),"
    "       json_extract(part.value, '$.toolName'),"
    "       CASE WHEN json_extract(part.value, '$.type') = 'text'"
    "            THEN json_extract(part.value, '$.text') END,"
    "       CASE WHEN json_extract(part.value, '$.toolName') = 'write_todos'"
    "            THEN json_extract(part.value, '$.input.todos') END"
    "  FROM threads t,"
    "       json_each(t.harness_state,"
    "                 '$.sessionState.mainAgentState.messageHistory') AS msg,"
    "       json_each(msg.value, '$.content') AS part"
    " WHERE t.id = ?"
    " ORDER BY msg.key, part.key"
)


def harness_live(cur, tid) -> dict:
    """What the app's OWN live state holds for a thread — the in-flight list and requests.

    In plain words: this is the running turn's list AND the requests around it.
    `harness_state` is a JSON blob the app rewrites as the agent works, and
    `mainAgentState.messageHistory` carries the messages as they are made — the user's
    requests, the agent's prose, the `write_todos` calls — before any of them is committed as
    a `messages` row, which is the one live transcript the store has.

    Returns `{todos, at_ms, hit_key, prompts, goals}`: the newest usable `write_todos` (or
    None), when it was written (`sentAt`, ms), its position in the history (the anchor the
    `now`/`nudge` rule measures requests against), and the requests and `Goal:` headings as
    `(position, text)` pairs in history order. A request is a user message the app TAGGED
    `USER_PROMPT` — its own word for a real prompt — which is what keeps a compaction summary
    or a tool-error injection out of the pane. Everything is best-effort: an older app may
    carry no such column, a shape this build does not know, or a blob that is not JSON, and
    any miss leaves the pane with the committed list it already had rather than an error.
    """
    out: dict = {"todos": None, "at_ms": 0, "hit_key": None, "prompts": [], "goals": []}
    if "harness_state" not in thread_columns(cur):
        return out
    try:
        rows = cur.execute(_HARNESS_SQL, (tid,)).fetchall()
    except sqlite3.Error:
        return out
    # A position is `(message, part)` and not the message alone: one message carries a whole
    # turn's parts, so two headings (or two lists) in the same message would share a position
    # and a tie would hand `_newest` the FIRST of them — the same first-vs-last trap the
    # committed read had. The positions below are compared, never arithmetic'd, and the CLI's
    # own positions are journal byte offsets, so both readers can order their own and neither
    # can be mixed up with the other's.
    for key, pkey, role, tags, at, ptype, tool, text, todos in rows:
        pos = (key, pkey)
        if role == "user" and ptype == "text" and isinstance(tags, str) and "USER_PROMPT" in tags:
            said = " ".join((text or "").split())
            # The app's own resume line rides the same USER_PROMPT tag the user's words do,
            # so the tag alone is not the test (`_is_resume_prompt`).
            if said and not _is_resume_prompt(said):
                out["prompts"].append((pos, said))
        elif role == "assistant" and ptype == "text":
            stated = goal_line(text)
            if stated:
                out["goals"].append((pos, stated))
        if tool == "write_todos" and todos:
            try:
                parsed = json.loads(todos)
            except (ValueError, TypeError):
                continue
            if (isinstance(parsed, list) and parsed
                    and all(isinstance(t, dict) for t in parsed)):
                out["todos"] = parsed
                out["hit_key"] = pos
                try:
                    out["at_ms"] = int(at or 0)
                except (TypeError, ValueError):
                    out["at_ms"] = 0
    return out


def harness_turn(cur, tid) -> dict:
    """Just the in-flight list from `harness_live`: `{todos, at_ms}` — the older shape."""
    live = harness_live(cur, tid)
    return {"todos": live["todos"], "at_ms": live["at_ms"]}


def _now_and_nudge(prompts, goals, hit_key, goal):
    """`now` and `nudge`, computed the way the CLI journal computes them.

    Same rules, different store. `hit_key` is the position of the list the pane is showing:
    a request AFTER it is newer work the list does not describe. A `nudge` — a request that is
    only "keep going" — wins the line and names what to do; otherwise `now` shows the newest
    request, unless the agent has already written a `Goal:` line for it (that heading is the
    better sentence) or the request merely repeats the one the current list answers (same
    words is not drift). With no list to anchor on there is no `now`, but a `nudge` can still
    be said. `is_nudge` / `pick_prompt` / `_newest` are the CLI's own, so the two sources
    cannot drift apart.
    """
    newest = _newest(prompts)
    nudge = None
    if newest and is_nudge(newest[1]) and (hit_key is None or newest[0] > hit_key):
        nudge = newest[1]
    now = None
    newer = [(k, t) for k, t in prompts if hit_key is None or k > hit_key]
    if hit_key is not None and newer:
        later = _newest((k, t) for k, t in goals if newest and k >= newest[0])
        if later:
            now = later[1]
        else:
            pick = pick_prompt(newer)
            answered = pick_prompt([(k, t) for k, t in prompts if k <= hit_key])
            now = pick if pick and pick != answered else None
    if nudge:
        now = None
    if now and now == goal:
        now = None
    return now, nudge


# The PROSE around a committed list: the heading the agent wrote for it and the requests
# around it, in the shape the harness read already produces. Positions are `(seq, part.key)`
# PAIRS — one row is a whole turn, so a heading and a list in the same row would otherwise
# share a position — and `pick_goal`, `goal_stale_at` and `_now_and_nudge` only ever COMPARE
# positions, so a pair orders exactly like the journal's byte offsets they get from the CLI.
#
# The heading is not always in the list's own row: an agent that states its goal once and
# then re-publishes the same list in later turns leaves the heading one row back (measured on
# the thread this was built for, whose heading sits in the previous turn's row while the
# newest copy of the list is the next one). So the rows are read BACK from the list's own row,
# newest first, `COMMITTED_PROSE_ROWS` of them: far enough to hold the heading and the request
# that opened the turn, bounded because this runs on every poll — and a heading older than
# that bound is one this read does not find, which leaves the pane with its honest `no
# heading` rather than an answer about a turn nobody can see.
COMMITTED_PROSE_ROWS = 24
# ...with the receipt joined when the store has one (`_receipt_is_request`); the plain shape
# answers a store older than the receipts, where every user row stands as it always did.
_COMMITTED_PROSE_SQL = (
    "SELECT m.seq, m.role, part.key, json_extract(part.value,'$.text'), q.source, q.kind"
    "  FROM (SELECT seq, role, parts_json, input_id FROM messages"
    "         WHERE thread_id = ? AND seq <= ? ORDER BY seq DESC LIMIT ?) AS m"
    "  LEFT JOIN queue_items q ON q.id = m.input_id"
    "  JOIN json_each(m.parts_json) AS part"
    " WHERE json_extract(part.value,'$.kind') = 'text'"
    " ORDER BY m.seq, part.key"
)
_COMMITTED_PROSE_PLAIN_SQL = (
    "SELECT m.seq, m.role, part.key, json_extract(part.value,'$.text'), NULL, NULL"
    "  FROM (SELECT seq, role, parts_json FROM messages"
    "         WHERE thread_id = ? AND seq <= ? ORDER BY seq DESC LIMIT ?) AS m"
    "  JOIN json_each(m.parts_json) AS part"
    " WHERE json_extract(part.value,'$.kind') = 'text'"
    " ORDER BY m.seq, part.key"
)
# ...and the requests AFTER the list: what the shown list does not describe (`now`, or a
# `nudge` when it is only "keep going"). Bounded at eight, which is more than a list can be
# followed by — eight newer requests is eight newer turns, and then this is not the newest
# list any more.
_COMMITTED_PROMPTS_SQL = (
    "SELECT m.seq, part.key, json_extract(part.value,'$.text'), q.source, q.kind"
    "  FROM messages m LEFT JOIN queue_items q ON q.id = m.input_id"
    "  JOIN json_each(m.parts_json) AS part"
    " WHERE m.thread_id = ? AND m.seq > ? AND m.role = 'user'"
    "   AND json_extract(part.value,'$.kind') = 'text'"
    " ORDER BY m.seq, part.key LIMIT 8"
)
_COMMITTED_PROMPTS_PLAIN_SQL = (
    "SELECT m.seq, part.key, json_extract(part.value,'$.text'), NULL, NULL"
    "  FROM messages m JOIN json_each(m.parts_json) AS part"
    " WHERE m.thread_id = ? AND m.seq > ? AND m.role = 'user'"
    "   AND json_extract(part.value,'$.kind') = 'text'"
    " ORDER BY m.seq, part.key LIMIT 8"
)


def _committed_prose(cur, state: dict) -> None:
    """Give a COMMITTED turn's list its own heading, `now` and `nudge`.

    The live read (`harness_live`) has the prose of a turn in flight; the committed rows have
    the prose of every turn that closed, and nothing used to read it: a pane following the app
    whose agent state carries no history drew the row's list under the warning `no heading —
    the agent owes a Goal: line` while the very row it was reading held the line (measured
    2026-10-03 on the pane following this session; the row's own `Goal:` text part sat beside
    its four `write_todos` parts). Same store, same rules: `pick_goal` chooses the heading that
    belongs to this list and `_now_and_nudge` the request after it, so the app's committed turn
    reads the way the CLI journal of a finished chat already does — including the heading one
    row back, which is where an agent that states its goal once leaves it while re-publishing
    the same list in later turns (`_COMMITTED_PROSE_SQL`, and `goal_stale_at` is what tells the
    two situations apart). What counts as a request is the APP's answer rather than ours: a
    user row must carry the receipt of a request the user made (`_receipt_is_request`) and
    must not be the app's own resume line (`_is_resume_prompt`) to feed `now`, `nudge` or the
    heading's staleness bracket.

    Called only when the list on the screen IS this row's — the harness had no list of its own
    to show — because a heading belongs to the list it was written for: over a newer list from
    the harness it would be last turn's objective, which is what `goal_stale` exists to refuse.
    Best-effort throughout: a store without the `kind`/`role` columns this reads, or a row that
    vanished between the two queries, leaves the state as it was rather than raising into a
    pane's poll.
    """
    tid, seq, part = state.get("thread"), state.get("seq"), state.get("list_part")
    if tid is None or seq is None or part is None:
        return
    anchor = (seq, part)
    goals: list = []
    prompts: list = []
    # Which of the two shapes this store answers: the receipts joined to their rows, or the
    # rows alone.
    receipts = _table_exists(cur, "queue_items")
    # The two halves fail apart on purpose: an older store may carry one and not the other,
    # and a heading with no requests is still a heading — while a store that can answer
    # neither leaves the state exactly as it was.
    try:
        for rseq, role, key, text, source, kind in cur.execute(
                _COMMITTED_PROSE_SQL if receipts else _COMMITTED_PROSE_PLAIN_SQL,
                (tid, seq, COMMITTED_PROSE_ROWS)):
            if role == "assistant":
                stated = goal_line(text)
                if stated:
                    goals.append(((rseq, key), stated))
            elif role == "user":
                # A request is what the app's receipt says the USER asked for, and not the
                # app's own resume line — see the two rules at the top of this module.
                if not _receipt_is_request(source, kind):
                    continue
                said = " ".join((text or "").split())
                if said and not _is_resume_prompt(said):
                    prompts.append(((rseq, key), said))
    except sqlite3.Error:
        pass
    try:
        for rseq, key, text, source, kind in cur.execute(
                _COMMITTED_PROMPTS_SQL if receipts else _COMMITTED_PROMPTS_PLAIN_SQL,
                (tid, seq)):
            said = " ".join((text or "").split())
            if said and _receipt_is_request(source, kind) and not _is_resume_prompt(said):
                prompts.append(((rseq, key), said))
    except sqlite3.Error:
        pass
    if not goals and not prompts:
        return
    stated = pick_goal(goals, prompts, anchor)
    if stated:
        state["goal"] = stated
        state["goal_source"] = "agent"
        # ...and whether it is left over from an earlier turn than the list it heads — the one
        # field both sources carry only when it is true (`_live_turn` says it the same way).
        if goal_stale_at(goals, prompts, anchor):
            state["goal_stale"] = True
    now, nudge = _now_and_nudge(prompts, goals, anchor, state.get("goal"))
    state["nudge"] = nudge or None
    state["now"] = now if now and now != state.get("goal") and not nudge else None


def _live_turn(cur, state: dict, tid, now_ms: int) -> None:
    """Fold the app's live turn into a state — the running flag, the turn, its list and now.

    In plain words: while a turn is running the committed row is the LAST turn's, so the
    pane shows the app's own live state instead. Five things come of it: `turn_running`
    (so a turn with nothing written yet says so rather than blaming the agent), the turn's
    own start (`last_prompt_at`, which lets `finish_state` drop a finished previous list
    instead of reading it as this turn's progress), the live list itself when the agent has
    written one THIS turn, the agent's own `Goal:` heading for it, and `now`/`nudge` when a
    request came after the list. Silent —
    the committed answer stands — when the turn is not running, when the store carries none
    of this, or when the only list in flight predates the turn (the previous turn's) or is
    older than what is already committed.
    """
    running = turn_running(cur, tid, now_ms)
    state["turn_running"] = running
    if not running:
        return
    cols = thread_columns(cur)
    start = 0
    if "last_prompt_at" in cols:
        try:
            row = cur.execute("SELECT last_prompt_at FROM threads WHERE id = ?", (tid,)).fetchone()
        except sqlite3.Error:
            row = None
        start = int((row or [0])[0] or 0)
    if start:
        state["turn"] = {"start_ms": start}
    live = harness_live(cur, tid)
    # The anchor the `now`/`nudge` rule measures against is the position of the list being
    # shown — which is the harness's own list only when that is the one TAKEN below.
    anchor = live["hit_key"]
    at = live["at_ms"]
    committed = int(state.get("ts") or 0)
    if live["todos"]:
        if start and at and at < start:
            pass        # the newest list predates this turn: the committed row stands
        elif committed and at and at < committed:
            pass        # the harness is behind the store: the committed row stands
        else:
            state["todos"] = live["todos"]
            if at:
                state["ts"] = at
            # ...and the heading for THIS list is the harness's own, chosen below: the
            # committed row's prose belongs to the committed list, which is not this one
            # (`read_desktop` skips it on this flag).
            state["live_list"] = True
    if live["todos"] and not state.get("live_list"):
        # The newest list in the history is NOT the one on the screen — it predates this turn,
        # or it is behind what the store already committed — so prose anchored to IT cannot
        # head that list. The committed row's own answer belongs there, and it is asked
        # separately (`_committed_prose`); carrying the harness's heading here put the last
        # turn's objective over a list it was never written for (measured 2026-10-03, on the
        # pane following this session: `Goal: stop the pane closing mid-run` heading a row
        # whose own prose stated no goal at all).
        return
    # The agent's own `Goal:` heading for the list being shown. It is read from the SAME
    # prose the CLI journal reads (`goal_line` over the assistant's text parts) and chosen by
    # the same rule (`pick_goal`), so a mid-turn desktop pane shows the heading a CLI pane
    # would have shown for the same turn — the store only holds this live, before the turn's
    # messages are committed, which is exactly when the pane needs it.
    goal = pick_goal(live["goals"], live["prompts"], anchor)
    if goal:
        state["goal"] = goal
        state["goal_source"] = "agent"
        # ...and whether that heading is left over from an EARLIER list than the one being
        # shown, which is the CLI's shared rule (`goal_stale_at`) — the harness's list and the
        # heading both come from its history, so the two positions are comparable. Carried
        # only when true, so a fresh heading leaves the state byte-identical (the same shape
        # the CLI source keeps).
        if goal_stale_at(live["goals"], live["prompts"], anchor):
            state["goal_stale"] = True
    now, nudge = _now_and_nudge(live["prompts"], live["goals"], anchor, state.get("goal"))
    state["nudge"] = nudge or None
    state["now"] = now if now and now != state.get("goal") and not nudge else None


def _thread_query(cur, sql, params=()):
    """The row a list was read from, plus WHERE in it the list was: `seq` and `part.key`.

    Those two are the list's position, and the committed prose read (`_committed_prose`) needs
    it to ask which heading and which request belong to this list rather than to a neighbour.
    They ride on the state beside `ts`/`todos`; no renderer or downstream reader looks at them
    (and `DesktopSource.describe` builds its answer field by field, so they cannot leak out).
    """
    row = cur.execute(sql, params).fetchone()
    if not row:
        return None
    tid, ts, todos_json, title, seq, part = row
    try:
        todos = json.loads(todos_json or "[]")
    except Exception:
        return None
    return {"ts": ts, "todos": todos, "thread": tid, "title": title or "",
            "seq": seq, "list_part": part}


def read_desktop(db: str, thread_id=None, source="active", state_path=DEFAULT_WORKSPACE_STATE,
                 others: int = 0, window_ms: int = DESKTOP_LIVE_MS, now_ms: int | None = None):
    """This project's list — and, when asked, the other live threads of the same store.

    `others` is how many more threads to carry (`--threads`, 0 for none): they ride on the
    state as `threads`, the one being followed marked `current`, which is all a renderer
    needs to stack them in one pane. The followed thread is always first and always drawn,
    live window or not: it is the thread the pane was pointed at, and dropping it for being
    quiet would leave the pane describing somebody else's work.
    """
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    project = project_path_of(db)
    workspace = {} if source == "last" else load_workspace(state_path)
    active_tab = None
    source_note = None
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
            # The app's persisted tab, corrected by the app's LIVE work when the file is
            # behind: a tab switch reaches that file minutes late (see `focused_thread`), so
            # a pane reading only the file draws the tab you have already left.
            live_id, _asked, note = focused_thread(cur, now_ms, prefer=target)
            if live_id and live_id != target and exists(live_id):
                target, source_note = live_id, note
        if target and not exists(target):
            target = None
        if target:
            state = _thread_query(
                cur,
                """SELECT m.thread_id, m.ts, json_extract(part.value,'$.input.todos'), t.title,
                        m.seq, part.key
                     FROM messages m JOIN threads t ON t.id = m.thread_id
                     JOIN json_each(m.parts_json) AS part
                    WHERE m.thread_id = ? AND m.parts_json LIKE '%write_todos%'
                      AND json_extract(part.value,'$.toolName') = 'write_todos'
                    ORDER BY m.ts DESC, m.seq DESC, part.key DESC LIMIT 1""",
                (target,),
            ) or {"ts": None, "todos": [], "thread": target, "title": ""}
            state["active"] = target == active_tab
            state["source"] = "active-tab" if target == active_tab else "pinned"
            if source_note:
                # Which tab the app's file still names, and which one is actually being
                # worked in — the pane's title carries it, because a list that changes tab
                # under the reader should say why it moved.
                state["source_why"] = source_note
                state["source"] = "live-tab"
            state["session"] = target
            _live_turn(cur, state, target, now_ms)
            if not state.get("live_list"):
                _committed_prose(cur, state)
            return _with_threads(cur, state, target, others, now_ms, window_ms)
        state = _thread_query(
            cur,
            """SELECT m.thread_id, m.ts, json_extract(part.value,'$.input.todos'), t.title,
                    m.seq, part.key
                 FROM messages m JOIN threads t ON t.id = m.thread_id
                 JOIN json_each(m.parts_json) AS part
                WHERE m.parts_json LIKE '%write_todos%'
                  AND json_extract(part.value,'$.toolName') = 'write_todos'
                ORDER BY m.ts DESC, m.seq DESC, part.key DESC LIMIT 1""",
        ) or {"ts": None, "todos": [], "thread": None, "title": ""}
        state["active"] = False
        state["source"] = "last" if source == "last" else "fallback"
        state["session"] = state.get("thread")
        _live_turn(cur, state, state.get("thread"), now_ms)
        if not state.get("live_list"):
            _committed_prose(cur, state)
        return _with_threads(cur, state, state.get("thread"), others, now_ms, window_ms)
    finally:
        con.close()


def _with_threads(cur, state: dict, followed, others: int, now_ms: int, window_ms: int) -> dict:
    """Attach the other live threads to a state, when there are any and any were asked for.

    The key is left OFF a single-thread answer rather than set empty: a renderer tells "show
    one list" from "stack these" by its presence, and a state a CLI journal or a file
    produced must stay exactly the shape it always had.

    The other threads are a BONUS on top of a list that was already read, so a store that
    cannot answer this second question the way it answered the first (a schema this build
    does not know) leaves the pane with the list it has rather than with none: the failure
    is the add-on's, and it is confined to it.
    """
    try:
        extra = live_threads(cur, followed, others, now_ms, window_ms)
    except sqlite3.Error:
        return state
    if not extra:
        return state
    ts = state.get("ts")
    state["threads"] = [{
        "id": followed,
        "title": state.get("title") or "",
        "todos": state.get("todos") or [],
        "current": True,
        "running": bool(ts and now_ms - ts <= DESKTOP_RUNNING_MS),
        "source_updated_ms": ts,
        # The rows all carry the SAME fields, so a reader can compare any thread's row with
        # another's without knowing which one the pane follows (`list_groups` draws them alike).
        "goal": state.get("goal"),
        "goal_stale": bool(state.get("goal_stale")),
    }] + extra
    return state


__all__ = [
    "project_path_of", "same_path", "pick_db", "load_workspace", "tab_for_app",
    "db_for_thread", "thread_columns", "turn_running", "focused_thread", "live_threads",
    "_with_threads",
    "harness_live", "harness_turn", "_now_and_nudge", "_live_turn", "_thread_query",
    "_committed_prose", "COMMITTED_PROSE_ROWS", "read_desktop",
    "DESKTOP_LIVE_MS", "DESKTOP_RUNNING_MS", "DESKTOP_FOCUS_MS", "DESKTOP_MAX_THREADS",
    "DESKTOP_RESUME_PROMPTS",
]
