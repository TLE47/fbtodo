"""The desktop app's store: one SQLite project, read per turn — and all its live threads."""

from __future__ import annotations

import glob
import json
import os
import sqlite3
import time
from .base import *  # noqa: F401,F403 — the package is one namespace

# ===================================================== several threads at once
# The app runs more than one thread per project (each tab is one, and a thread can be left
# running while you open another), but the store only ever holds the transcript: a list is
# written per turn, so "is this thread live?" is a question about how recently one was
# written. Two windows, both about lists rather than about wall clocks: a thread whose list
# moved inside `DESKTOP_LIVE_MS` is one a pane should still show, and one whose list moved
# inside `DESKTOP_RUNNING_MS` is the one that is working right now.
DESKTOP_LIVE_MS = 90 * 60 * 1000

DESKTOP_RUNNING_MS = 3 * 60 * 1000

# How many threads a pane will show including the one it follows. Four fits a pane that is
# worth reading (a heading and a step or two each); `--threads 0` is the old answer, the
# followed thread alone.
DESKTOP_MAX_THREADS = 4

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
        f" {status}, {archived}"
        " FROM messages m JOIN threads t ON t.id = m.thread_id"
        " JOIN json_each(m.parts_json) AS part"
        " WHERE m.parts_json LIKE '%write_todos%'"
        " AND json_extract(part.value,'$.toolName') = 'write_todos'"
        " ORDER BY m.ts DESC"
    )
    out: list = []
    seen = {followed}
    for tid, ts, todos_json, title, status, archived in rows:
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
        out.append({
            "id": tid,
            "title": title or "",
            "todos": todos,
            "current": False,
            "running": bool(ts and now_ms - ts <= DESKTOP_RUNNING_MS),
            "source_updated_ms": ts,
        })
        if len(out) >= others:
            break
    return out


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
            return _with_threads(cur, state, target, others, now_ms, window_ms)
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
    }] + extra
    return state


__all__ = [
    "project_path_of", "same_path", "pick_db", "load_workspace", "tab_for_app",
    "db_for_thread", "thread_columns", "live_threads", "_with_threads", "_thread_query",
    "read_desktop",
    "DESKTOP_LIVE_MS", "DESKTOP_RUNNING_MS", "DESKTOP_MAX_THREADS",
]
