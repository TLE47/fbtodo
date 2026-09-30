"""Where a list can come from, and the one protocol all three answer.

Each source answers "is there anything of this kind here?" in its own vocabulary (`find`) and
then translates what it found into the state's fields (`describe`). The dispatch is a loop, so
the three cannot drift apart in silence.
"""

from __future__ import annotations

import os
import time
from .base import *  # noqa: F401,F403 (the package is one namespace)
from .scan import *  # noqa: F401,F403 (the package is one namespace)
from .nas import *  # noqa: F401,F403 (the package is one namespace)
from .desktop import *  # noqa: F401,F403 (the package is one namespace)
from .alerts import *  # noqa: F401,F403 (the package is one namespace)

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


class FileSource(Source):
    """A state JSON on disk: the generic source, for anything that can write one file.

    Nothing here watches a journal or a process. A file either holds a state or it does not,
    so `find` is a read and `describe` is the mapping — which is the whole point: any agent,
    any script, any hand-typed JSON can drive the pane through `fbtodo push`, and the same
    state comes back through `-s file:PATH`. The state inside is taken at its word, including
    the list number it recorded; this is a RE-player, not a re-numberer.
    """

    name = "file"
    backend = "file"

    def __init__(self, path: str) -> None:
        self.path = path

    def find(self, args, cwd: str):
        st = read_json(self.path, None)
        return st if isinstance(st, dict) else None

    def describe(self, args, cwd: str, ob: dict) -> dict:
        st = dict(ob)
        todos = st.get("todos") or []
        st.update({
            "backend": "file",
            "target": self.path,
            "todos": todos,
            "source": "state-file",
            # There is no process behind a file to outlive the pane, so nothing here may read
            # as "the session went away": the file's own mtime is the only clock there is.
            "instance_alive": True,
            "store_mtime_ms": store_mtime_ms(self.path, "file"),
            "source_updated_ms": st.get("ts") or st.get("source_updated_ms"),
        })
        if todos and not st.get("list_version"):
            # A recorded list with no number on it is that list's first. Saying so here keeps
            # `adopt_version` from borrowing a live watcher's counter for a file it never saw.
            st["list_version"] = 1
        return st

    def miss(self) -> dict:
        return {"backend": "file", "todos": [],
                "error": f"no state file at {self.path}"}


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
    "file": ("file",),
}

# `-s file:PATH` names a path rather than a mode, so it is a prefix rather than a word. The
# prefix is required: `-s some/dir` must keep failing as the usage error it has always been
# instead of quietly reading a file the caller did not name.
FILE_SOURCE_PREFIX = "file:"


def source_path(value: str | None) -> str | None:
    """The path in a `file:PATH` source, or None when the value is not one."""
    text = str(value or "")
    if not text.startswith(FILE_SOURCE_PREFIX):
        return None
    return text[len(FILE_SOURCE_PREFIX):] or None


def source_kind(value: str | None) -> str:
    """What `-s` MEANS, without its path: `file` for `file:PATH`, else the word itself.

    A state's `backend` is a word, so anything comparing the two has to compare like with
    like: `state_matches_request` asking whether a cached state answers `file:/tmp/s.json`
    would never match the `file` a watcher wrote.
    """
    return "file" if source_path(value) else str(value or "auto")


def state_file_in(path: str) -> str:
    """The file a `file:` source names, expanding a directory to the state inside it.

    So `-s file:$FBTODO_HOME` reads that home's own state — the one a run with that home
    writes — and `-s file:/path/state.json` reads exactly that file.
    """
    return os.path.join(path, STATE_FILE_NAME) if os.path.isdir(path) else path


def sources_for(args) -> tuple:
    """The sources `args.source` asks for, in the order they are asked."""
    path = source_path(getattr(args, "source", None))
    if path is not None:
        return (FileSource(state_file_in(path)),)
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


__all__ = [
    "Source", "CliSource", "DesktopSource", "NasSource", "FileSource", "SOURCE_CLASSES",
    "SOURCES", "SOURCE_ORDER", "FILE_SOURCE_PREFIX", "source_path", "source_kind",
    "state_file_in", "sources_for", "snapshot", "_snapshot", "store_mtime_ms",
]
