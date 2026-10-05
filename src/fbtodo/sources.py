"""Where a list can come from, and the one protocol all of them answer.

Each source answers "is there anything of this kind here?" in its own vocabulary (`find`) and
then translates what it found into the state's fields (`describe`). The dispatch is a loop, so
they cannot drift apart in silence.
"""

from __future__ import annotations

import os
import time
from .base import *  # noqa: F401,F403 (the package is one namespace)
from .scan import *  # noqa: F401,F403 (the package is one namespace)
from .desktop import *  # noqa: F401,F403 (the package is one namespace)
from .alerts import *  # noqa: F401,F403 (the package is one namespace)
from .tasks import *  # noqa: F401,F403 (`short_duration`, for the chain's own note)

# In plain words: a list can come from two different kinds of transcript here — a CLI journal
# in a directory, and the desktop app's conversation DB — and every reader downstream (the
# pane, `json`, `snap`, the estimates) reads ONE state. A source is that seam: each one
# answers "is there anything of this kind here?" in its own vocabulary and then translates
# what it found into the state's fields, so the dispatch is a loop rather than two branches
# whose differences drift apart in silence. Each source's reading happens in `find` (that is
# the DB open, the file stat) and its translation in `describe` — nothing that fetches is in
# the mapping.
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
        out = {
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
        # ...and whether the heading is left over from an earlier turn than the list it
        # heads. Carried only when true, so a fresh heading leaves the state exactly the
        # shape it always had (the same rule the desktop source and `threads` follow).
        if st.get("goal_stale"):
            out["goal_stale"] = True
        return out

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
        # A file is taken at its word about WHAT it says (the docstring above), not about
        # what a terminal would DO with it: the same walk every other source runs.
        return clean_observation(st)

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
        # `auto` asks for THIS directory's own project (`own_only`), the way `cli_chat_dir`
        # asks for THIS directory's chat: a repo under the home directory must not inherit
        # the home project's session just because home contains it.
        db = db or pick_db(args.db, hint, cwd=cwd,
                           own_only=source_kind(getattr(args, "source", None)) == "auto")
        if not db or not os.path.exists(db):
            return None
        return {"db": db, "thread": thread_id, "mode": source}

    def describe(self, args, cwd: str, ob: dict) -> dict:
        # `--threads` is how many of the store's live threads a pane shows, the one being
        # followed included; `--thread-live` is how quiet a thread's own list may be and
        # still count (0 = no window, so every open thread with a list is shown).
        st = read_desktop(
            ob["db"], thread_id=ob["thread"], source=ob["mode"], state_path=args.state,
            others=max(0, int(getattr(args, "threads", 0) or 0) - 1),
            window_ms=max(0, int(getattr(args, "thread_live", 90) or 0)) * 60_000,
        )
        out = {
            "backend": "desktop",
            "target": ob["db"],
            "store_mtime_ms": store_mtime_ms(ob["db"], "desktop"),
            "session": st.get("session"),
            "title": st.get("title") or "",
            "iteration": None,
            "source_updated_ms": st.get("ts"),
            "todos": st.get("todos") or [],
            "source": st.get("source"),
            # The store's live turn: a running thread carries it so the pane says
            # `turn running · no list yet` rather than blaming the agent, and the turn's own
            # start (and its live list, when there is one) so a pane mid-turn reads the way
            # a CLI pane mid-turn does.
            "turn_running": bool(st.get("turn_running")),
        }
        if st.get("turn"):
            out["turn"] = st["turn"]
        # The agent's own `Goal:` heading for the live list, read from the app's prose the way
        # the CLI journal reads it — so a mid-turn desktop pane shows the same heading a CLI
        # pane would have shown (a request is never the goal; only the agent's line is).
        if st.get("goal"):
            out["goal"] = st["goal"]
            out["goal_source"] = st.get("goal_source")
            # ...and whether it is left over from an earlier list than the one being shown.
            # Carried only when true: a fresh heading keeps every state byte-identical.
            if st.get("goal_stale"):
                out["goal_stale"] = True
        # ...and the request that came after the list, if the app's live history carries one
        # (a `nudge` is a bare continuation and wins the slot; see `desktop._now_and_nudge`).
        if st.get("now"):
            out["now"] = st["now"]
        if st.get("nudge"):
            out["nudge"] = st["nudge"]
        # The desktop store's own account of ITS choice, when it made one: it followed the
        # thread being worked in rather than the tab the app's debounced workspace file still
        # names (`focused_thread`). Carried only when it did, for the same reason `goal_stale`
        # is: a state that says nothing new must stay byte for byte what it always was.
        if st.get("source_why"):
            out["source_why"] = st["source_why"]
        # Only when there is more than one: a state that describes ONE list must stay the
        # shape every other source produces, which is what keeps a single-list frame and the
        # recorded goldens byte for byte what they were.
        if st.get("threads"):
            out["threads"] = st["threads"]
        return clean_observation(out)

    def miss(self) -> dict:
        return {"backend": None, "todos": [], "error": "no conversation DB found"}


SOURCE_CLASSES = (CliSource, DesktopSource)


SOURCES = {cls.name: cls() for cls in SOURCE_CLASSES}


# What `-s auto` means, in order: the journal if this directory has one that is LIVE, else the
# desktop store — a chat directory keeps its last chat forever, so asking only "is there one
# here?" let a finished session answer for a directory whose work had moved to the app (see
# `_snapshot`). Every other mode asks ONE source, which is the whole difference between
# "nothing here, look elsewhere" and "nothing here, say so".
SOURCE_ORDER = {
    "auto": ("cli", "desktop"),
    "cli": ("cli",),
    "desktop": ("desktop",),
    "file": ("file",),
}

# How long a CLI journal may sit still and still count as the session WORKING in this
# directory. The journal is appended every iteration while an agent runs, so a chat this quiet
# (with no Freebuff process behind it) is a finished session — the same window `fbtodo board`
# calls live. It is only ever consulted by `auto`, and only to decide which store answers.
SOURCE_LIVE_MS = 90 * 60 * 1000


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

    A wrapper rather than two call sites: every local backend — and the paths that find
    no backend at all — should carry the patch row without each one having to remember it.
    """
    state = _snapshot(args, cwd=cwd, instance_pid=instance_pid)
    state.update(local_patch_alert())
    return state


def _chat_is_live(ob: dict, instance_pid) -> bool:
    """True while the CLI chat here is the session WORKING in this directory.

    Two signals, and which of them can outrank the other is the whole rule. A session that
    ended its turn wrote that down ITSELF, and a session waiting for you is not the session
    working in this directory — so the journal's own boundary decides, with one exception: a
    **Freebuff process behind this chat right now** is working it, whatever the boundary says,
    and that is evidence about the chat rather than about the pane.

    The exception has to say Freebuff, because the pid can be anything. `find_instance` takes
    `--watch-pid` as given whenever that pid is alive, and the shell wrapper passes one on
    every session — including, in a directory the desktop app also works in, a pid that has
    nothing to do with the chat whose finished journal is sitting there. Measured 2026-10-04 on
    the fixture the pane-close check builds (one directory, an ended chat and a live desktop
    thread): with a live non-Freebuff `--watch-pid` the pane's first frame was the ENDED
    chat's list, `cli · 2026-02-02…`, and without one it was the live thread — the freeze this
    rule exists to prevent, reachable whenever a pid resolves. The pid's identity is asked
    only when the boundary has already said no, which is the only time it changes an answer.

    Failing both, the quiet window decides: a session that ended without ever writing the
    record that says so is judged by the clock, and a chat with neither is a finished
    session, which `auto` must not answer a live desktop thread with (see `_snapshot`). A
    chat that is merely QUIET — one long step outlasting the window, the journal appended
    every iteration but a tail that can still go stale between two of them — is rescued by the
    pid, which is what a pid is good for.
    """
    chat = ob.get("chat")
    if journal_liveness(chat)[1]:
        return bool(instance_pid) and int(instance_pid) in set(freebuff_pids())
    if instance_pid:
        return True
    mtime = store_mtime_ms(chat, "cli")
    return bool(mtime) and int(time.time() * 1000) - mtime <= SOURCE_LIVE_MS


def followed_session_over(state: dict, instance_pid, now_ms: int) -> bool:
    """Has the CLI session this state names stopped being live?

    The other side of `_chat_is_live`: that one decides whether to FOLLOW the chat, this one
    whether to STOP following it — `auto` chooses a source by it, and the pane drops a cached
    state by it (sending an ended session back to `snapshot`, where `auto` re-picks). It is
    asked about the JOURNAL's clock, never the list's: a long step writes no
    todos while the journal still moves every iteration, so work in progress can never be
    mistaken for an ended session. Two things end a chat, and the pane's own cache must believe
    both: the turn boundary the agent wrote (a session waiting for you is over as far as a
    CACHED list goes, whether the state file was written before that field existed or the app
    has since touched the journal), and a journal with no process behind it, silent past the
    live window.

    Only the CLI backend answers here. A `file:` state has no process to outlive it, and the
    desktop store windows its own live threads and re-picks the active one on every read, so
    neither can be called "over" from a store clock the way a single journal can.
    """
    if state.get("backend") != "cli":
        return False
    if instance_pid:
        return False
    # The journal's own turn boundary, asked of the CHAT this state names rather than of the
    # cached clock: a watcher heartbeats the state file long after the chat behind it ended,
    # and the file's mtime is moved by the app's appends besides, so a finished session looks
    # live from both. The state already carries what the scan read (`turn_ended`); asking the
    # journal directly covers a state written before that field existed.
    if state.get("turn_ended"):
        return True
    if state.get("target") and journal_liveness(state["target"])[1]:
        return True
    mtime = state.get("store_mtime_ms")
    if not mtime:
        return False
    return now_ms - int(mtime) > SOURCE_LIVE_MS





def _store_clock(name: str, ob: dict) -> int:
    """When the store behind one source's observation last moved (0 when it has none).

    Only the two local sources are compared by `auto`, and each keeps its store where its own
    `describe` knows: the CLI's is the journal inside the chat directory, the desktop's is the
    conversation DB itself. The CLI's clock is the AGENT's last record rather than the file's
    mtime, for the reason `_chat_is_live` gives: this comparison asks which store moved more
    recently, and an app append to a finished journal is not the session moving.
    """
    if name == "cli":
        return journal_liveness(ob.get("chat"))[0] or store_mtime_ms(ob.get("chat"), "cli") or 0
    if name == "desktop":
        return store_mtime_ms(ob.get("db"), "desktop") or 0
    return 0


def _resolution_note(why: list, answered: str | None) -> str:
    """What an `auto` chain did, in a few words — why each source it passed went by.

    The pane's title carries this (`source_why`), because a list that looks wrong should name
    the choice that produced it: `cli finished 31h → desktop` is the whole story of a pane
    showing the app's thread over a directory whose own CLI session ended yesterday. The
    answerer goes last, after the reasons, so a narrow title clips the part the right slot
    already repeats.

    Only `auto` ever has one: a chain of one source has nothing to explain away, and an
    explicit `-s cli|desktop|file` means exactly what it says.
    """
    if not why:
        return ""
    return " · ".join(why) + (f" → {answered}" if answered else "")


def _snapshot(args, cwd: str | None = None, instance_pid=None) -> dict:
    """The current todo state, whatever backend is live."""
    cwd = cwd or os.getcwd()
    if instance_pid is None:
        instance_pid, _ = find_instance(
            cwd, getattr(args, "watch_pid", None), getattr(args, "instance_of", None)
        )
    now_ms = int(time.time() * 1000)
    base = {
        "schema": 1,
        "instance_pid": instance_pid,
        "cwd": os.path.realpath(cwd),
        "probed_ms": now_ms,
    }
    # Ask the sources `-s` names, in order, and take the first that has something here.
    chosen = sources_for(args)
    auto = source_kind(getattr(args, "source", None)) == "auto"
    held = None  # an `auto` CLI chat whose session is over, kept in case nothing fresher is here
    # ...and the reasons an `auto` chain passed a source over, in the order it asked them.
    why: list = []

    def note(answered: str | None) -> None:
        text = _resolution_note(why, answered)
        # A source may bring its OWN note — the desktop store's, when it followed the thread
        # being worked in rather than the tab the app's debounced file still names (see
        # `focused_thread`). Two questions, one line: WHICH source answered, and WHICH tab it
        # followed. Joining them keeps both in the slot the pane already clips, instead of one
        # silently winning — which is what happened when they shared a key.
        own = str(base.get("source_why") or "")
        if own and own not in text:
            text = f"{text} · {own}" if text else own
        if text:
            base["source_why"] = text

    for src in chosen:
        ob = src.find(args, cwd)
        if ob is None:
            if auto:
                why.append(f"{src.name} none here")
            continue
        # `auto` means "the session WORKING in this directory", and a chat directory keeps
        # its last chat forever: a directory that has not run a CLI session since Tuesday
        # still answers with Tuesday's list, and the pane renders it, frozen, while the live
        # session sits in the app's store (measured 2026-10-03: a pane pinned to a two-day-old
        # `09-30 07:12` chat reading `ALL DONE`, while two desktop threads in the same project
        # were live). A finished chat is therefore HELD, not chosen, and only steps back in if
        # the source after it has nothing here or moved no more recently. `-s cli` never
        # reaches this branch: asking for one source still means exactly that.
        if auto and src.name == "cli" and not _chat_is_live(ob, instance_pid):
            held = (src, ob)
            quiet = _store_clock("cli", ob)
            why.append("cli finished" + (f" {short_duration(now_ms - quiet)}" if quiet else ""))
            continue
        if held is not None and _store_clock(src.name, ob) <= _store_clock(
            held[0].name, held[1]
        ):
            # the held chat's store is the fresher one after all: it still answers
            why.append(f"{src.name} older")
            break
        base.update(src.describe(args, cwd, ob))
        note(src.name)
        return base
    if held is not None:
        # Nothing here is fresher than the finished chat — an old list is a better answer
        # than an empty one, and `-s cli` would have said the same thing anyway.
        base.update(held[0].describe(args, cwd, held[1]))
        note(held[0].name)
        return base
    # Nothing to watch: the LAST source asked speaks for the whole chain, which is how
    # `-s cli` says "no CLI chat for this directory" instead of quietly reading the desktop
    # store, and how `auto` ends on the desktop source's own answer.
    base.update(chosen[-1].miss())
    note(None)
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
    "Source", "CliSource", "DesktopSource", "FileSource", "SOURCE_CLASSES",
    "SOURCES", "SOURCE_ORDER", "FILE_SOURCE_PREFIX", "source_path", "source_kind",
    "state_file_in", "sources_for", "snapshot", "_snapshot", "store_mtime_ms", "journal_liveness",
    "_resolution_note",
    "SOURCE_LIVE_MS", "_chat_is_live", "_store_clock", "followed_session_over",
]
