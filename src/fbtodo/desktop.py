"""The desktop app's store: one SQLite conversation, read per turn."""

from __future__ import annotations

import glob
import json
import os
import sqlite3
from .base import *  # noqa: F401,F403 (the package is one namespace)

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


__all__ = [
    "project_path_of", "same_path", "pick_db", "load_workspace", "tab_for_app",
    "db_for_thread", "_thread_query", "read_desktop",
]
