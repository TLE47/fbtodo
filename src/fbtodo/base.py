"""The floor under everything else: where state lives, and the small tools.

This module is the only one with no `fbtodo` module under it. It holds what the rest of the
program is written on top of:

* the paths — the state directory and its migration, the lock, log and pins files, and the
  environment variables that move them;
* the settings the program reads at import: the transcript constants, the estimate and
  history bounds, the scratch limits, the exit codes;
* the generic helpers — atomic writes, the process table, the clocks, and the text tools
  (ANSI-stripping, screen-cell widths, wrapping) that every renderer is built from.

Nothing here knows what a todo list is, which is what lets `scan`, `tasks` and `render` be
read on their own.
"""

from __future__ import annotations

import ast
import builtins
import copy
try:
    import fcntl
except ImportError:  # pragma: no cover - no flock, so a claim is the record alone
    fcntl = None
import glob
import json
import os
import re
import shlex
import shutil as _shutil
import subprocess
import symtable
import sys
import tempfile
import textwrap
import time
import unicodedata

VERSION = "4.30.2"


# In plain words: when this process read the package off disk. Nothing re-imports a running
# Python process, so this is the divider between "the build I am running" and "the build that
# is there now": a source written after this moment is code this process does not have (see
# `source_newer_than`). Stamped here, as early as it can be, because base is imported before
# anything that could care about it.
STARTED_AT = time.time()


# How long to wait after a source write before believing it. An editor caught halfway through
# a save would otherwise exec a pane into a half-written file, and the pane is the one place
# that must not die: the person editing is the person looking at it.
SOURCE_SETTLE_S = 2.0


# How often a long-running process asks whether it is still the build on disk. Both askers —
# the pane and the watcher — re-exec themselves when the answer is no, and one listing a
# second is nothing beside the poll either of them already does. The answer only changes when
# somebody writes a file.
BUILD_CHECK_S = 1.0


HOME = os.path.expanduser("~")


# FBTODO_HOME points state/lock/log elsewhere — used by the self-check so it never
# touches the real watcher's files. Failing that, state belongs where the XDG spec says it
# does ($XDG_STATE_HOME/fbtodo) rather than in a dotdir of our own devising; the legacy
# `~/.freebuff` is moved there once, and only when nothing is still writing it.
LEGACY_SCRATCH = os.path.join(HOME, ".freebuff")


STATE_DIR = os.path.join(
    os.path.expanduser(os.environ.get("XDG_STATE_HOME") or os.path.join(HOME, ".local", "state")),
    "fbtodo",
)


# What the move carries. Listed as names rather than taken from the paths below, because the
# root has to be chosen before those are built.
# What the move carries. Listed as names rather than taken from the paths below, because the
# root has to be chosen before those are built. The `nas` names are listed although nothing
# writes them any more: they are what an install that had the remote source leaves in the old
# dotdir, and a move that skipped them would strand them there forever.
LEGACY_NAMES = (
    "fbtodo-state.json", "fbtodo-tasks.json", "fbtodo-tasks.jsonl",
    "fbtodo-daemon.pid", "fbtodo-daemon.log",
    "fbtodo-nas-pane.pid", "fbtodo-nas-pane.json", "fbtodo-nas-pane.log",
    "fbtodo-pane-keeper.pid", "fbtodo-pane.log", "fbtodo-pins.json", "fbtodo-last.json",
    "fbtodo-nas.json",
)


# The records that say "a process is using this directory": the watcher's and the keeper's.
# Both are checked, because moving the keeper's claim out from under a keeper leaves it
# writing a record at a path nothing reads, and a second keeper would then start.
LEGACY_CLAIMS = ("fbtodo-daemon.pid", "fbtodo-pane-keeper.pid")


def _claim_live(record: str) -> bool:
    """Is the process at `record` still there? Two gates, both of which err towards yes.

    The record must not be flocked, and its pid must not be alive. The second one matters
    because a process from before there was a claim left a record nobody holds, and the pid
    is then the only thing that says it is still running.
    """
    try:
        with open(record, "r", encoding="utf-8") as fh:
            pid = int((json.load(fh) or {}).get("pid") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        pid = 0
    if pid:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            pass
        except OSError:
            return True
    if fcntl is None:
        return False
    try:
        with open(record, "r+", encoding="utf-8") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return True
    except OSError:
        return False
    return False


def _legacy_claim_live() -> bool:
    """Is any of the legacy locks held, or its record's pid alive?"""
    return any(
        _claim_live(os.path.join(LEGACY_SCRATCH, name)) for name in LEGACY_CLAIMS
    )


def _state_root(migrate: bool = True):
    """(root, note) — where this run keeps its state, and how it got there.

    The note is one of `None` (nothing to move), `"moved"`, or a reason the run stayed on
    the legacy root: `"live"` (a watcher there still owns the store, so moving out from
    under it would leave the pane reading a file nobody updates) or `"failed"` (the move
    was refused; the old root still works, so it is what we use).

    `migrate=False` is the IMPORT-time answer: the same root chosen from the same facts,
    but with the move (and the `makedirs`, and the pid-file reads that decide it) left for
    `init_state_root()`. Importing a module is not an action, so the module picks a root
    and touches nothing; `main()` runs the move before any command reads a path.
    """
    env_home = os.environ.get("FBTODO_HOME")
    if env_home:
        return os.path.expanduser(env_home), None
    if os.path.abspath(STATE_DIR) == os.path.abspath(LEGACY_SCRATCH):
        return STATE_DIR, None
    if not os.path.exists(os.path.join(LEGACY_SCRATCH, "fbtodo-state.json")):
        return STATE_DIR, None
    if os.path.exists(os.path.join(STATE_DIR, "fbtodo-state.json")):
        return STATE_DIR, None
    if not migrate:
        # There IS something to move, but deciding whether to (a live watcher?) would mean
        # reading (and locking) pid files. Take the destination now; `init` decides.
        return STATE_DIR, None
    if _legacy_claim_live():
        return LEGACY_SCRATCH, "live"
    try:
        os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
        for name in LEGACY_NAMES:
            src = os.path.join(LEGACY_SCRATCH, name)
            if os.path.exists(src):
                os.replace(src, os.path.join(STATE_DIR, name))
    except OSError:
        return LEGACY_SCRATCH, "failed"
    return STATE_DIR, "moved"


# The root is CHOSEN here, without side effects; the move waits for `init_state_root()`.
SCRATCH, STATE_NOTE = _state_root(migrate=False)


STATE_FILE_NAME = "fbtodo-state.json"
STATE_PATH = os.path.join(SCRATCH, STATE_FILE_NAME)


# The task log is two files. The stream is the evidence store — one JSON object per line,
# appended to and never edited — and the document beside it is a memo of the fold, carrying
# the byte offset it was folded to. Anything that can be derived from the stream is derived
# again rather than trusted.
TASKS_PATH = os.path.join(SCRATCH, "fbtodo-tasks.json")


def events_path(path: str = "") -> str:
    """The stream that belongs to a task-log view — `fbtodo-tasks.json` -> `.jsonl`.

    Derived from the view's name rather than held as a second constant, so a caller that
    points the view somewhere else (the self-check's unit blocks, a restore from a backup)
    gets its own stream by construction and cannot fold one log's events into another's
    records.
    """
    return (path or TASKS_PATH) + "l"


def self_argv(here: str = "") -> list:
    """This program as a command line — how the panes, the keeper and the daemon name it.

    The LAUNCHER beside the package, not the file asking. A package's `__init__.py` is not the
    package when it is run as a script (a second copy of every module, and no relative
    imports), so pointing the re-invocations at whichever file happens to hold this function
    would break as soon as the program lived in more than one of them. A copy that carries the
    package without the launcher falls back to the package's own entry file — named from the
    asker's directory, so `src/fbtodo/anything.py` still answers `src/fbtodo/__init__.py` —
    which the `__main__` guard at the top turns back into the package.

    `here` is the file asking: this file in practice, and a stand-in in the self-check, which
    is how the fallback is exercised without a launcher-less copy on disk.
    """
    here = os.path.abspath(here or __file__)
    root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    launcher = os.path.join(root, "fbtodo")
    if os.path.exists(launcher):
        return [sys.executable, launcher]
    return [sys.executable, os.path.join(os.path.dirname(here), "__init__.py")]


def _package_sources(here: str = "") -> list:
    """Every `.py` beside the asker, plus the launcher `self_argv` names — the program.

    The list is the whole program by construction: everything fbtodo can do is in the
    package, and the launcher beside it is what every re-invocation runs. A copy carrying
    the package without a launcher answers with the package's own entry file, which is
    already in the list — asking twice about one file costs a second `stat` and nothing else.
    Nothing outside the package counts: `docs/`, `scripts/` and the tests are not the build a
    pane runs, and a commit that touches only those has not made the pane stale.
    """
    here = os.path.abspath(here or __file__)
    folder = os.path.dirname(here)
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".py"))
    except OSError:
        return []  # nothing readable to ask about, which is not a reason to rebuild
    return [os.path.join(folder, n) for n in names] + [self_argv(here)[1]]


def source_newer_than(started: float, now: float | None = None,
                      settle: float = SOURCE_SETTLE_S, here: str = "") -> str | None:
    """The source file written after `started`, or None when this process is the build on disk.

    In plain words: "am I still the code that is there?" A long-running pane keeps the build
    it imported while the checkout underneath it moves, and an upgrade then leaves the list
    drawn by the OLD one until somebody respawns the pane by hand — measured 2026-10-01, when
    a pane started the day before a release still rendered the day-before's fields. Nothing
    re-imports a running Python process, so the honest answer is to ask the filesystem and
    start over.

    `started` is the moment this process imported the package (`STARTED_AT`): any source
    newer than that is code this process is NOT running, whatever the version says — which is
    the point of asking the clock rather than `VERSION`, since files change between releases
    too. `settle` holds the answer back for a moment after the write, so a save still landing
    is not read as a finished one; a newer file inside the settle window is not "no change",
    it is "ask again on the next tick". The newest such file comes back as a path, for the
    log rather than for a decision.
    """
    if now is None:
        now = time.time()
    newest = None
    for path in _package_sources(here):
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            continue  # the launcher a package-only copy does not have
        if mtime <= started or now - mtime < settle:
            continue
        if newest is None or mtime > newest[1]:
            newest = (path, mtime)
    return newest[0] if newest else None


def source_syntax_error(here: str = "") -> str | None:
    """The first source file that will not parse, or None when the package still compiles.

    In plain words: a reason NOT to restart. The pane is the one process whose whole job is to
    be on the screen, and it is looked at precisely while its owner is editing — so a restart
    into half-saved code would take the pane away at the worst moment, with a traceback where
    the list was. Asking is a parse per file and only ever happens when a source has actually
    changed (`source_newer_than`), so the cost is a compile of the package per save, not per
    tick. The answer names the file and the line, because that is what the pane log records
    while it waits for the save to finish.
    """
    for path in _package_sources(here):
        try:
            with open(path, "rb") as fh:
                compile(fh.read(), path, "exec")
        except SyntaxError as exc:
            return f"{path}: line {exc.lineno}: {exc.msg}"
        except OSError:
            continue
    return None


# The variable that makes a child answer one question and stop: does this build WORK? A
# self-reload runs the SAME command line in a child with this set, and `main` answers through
# `probe_answer` — it builds the parser and resolves every global the functions load — before
# it can create, move or claim anything (see `reload_probe_error`).
RELOAD_PROBE_ENV = "FBTODO_RELOAD_PROBE"

# How long the probe may take. Importing this package is a few hundred milliseconds, and the
# deep passes add a parse of each module on top of it (`symtable` and `ast`); the bound is
# here only so that a build which HANGS on import (a lock, a network call added at module
# scope) is a reason to hold rather than a way to wedge the process that is waiting on it.
RELOAD_PROBE_TIMEOUT_S = 20.0


# How much of a failed probe's last line is kept. A note is a DIAGNOSIS, and the part that
# makes it one is at the END — the file, the line, and above all WHY that line can never run.
# The 240 this replaces was enough on a checkout that lived two directories deep and stopped
# being enough the moment one did not: the reason fell off the end and left a note saying only
# that some line is unreachable, which is just as true of a healthy build. A length cap on a
# diagnosis has to clear the longest path it can be handed, so this one is generous on purpose.
PROBE_NOTE_CHARS = 420


def reload_probe_error(timeout: float | None = None, here: str = "") -> str | None:
    """Would the build on disk work? None when it would, else the reason to hold.

    In plain words: a parse is not enough, because the self-reload does not re-import — it
    EXECS. A tree can compile file by file and still be internally inconsistent (a name
    imported from a module that no longer defines it, a call added at module scope with the
    wrong arity, a module that raises on import), and then the reload replaces a running
    image with one that dies on its own first line: the watcher or the keeper simply
    disappears, and for the pane the keeper then reopens it into the same broken tree and the
    two of them loop. `source_syntax_error` catches none of that, so this asks the new build
    itself: the same command line the reload is about to exec (`self_argv`), run once in a
    child with `RELOAD_PROBE_ENV` set, in which `main` answers through `probe_answer`. That
    answer is deeper than the import: the command parser is built (so `build_parser`'s body
    and every default it computes actually run), and the parse is then asked the two things
    an import never touches. `undefined_global_names` resolves every name the package's
    functions load as a global, because a function body is name-resolved only when it runs —
    a reference to a global that was renamed or deleted therefore fails here instead of
    waiting for the one command that reaches it. `unreachable_code` reads the source itself
    and fails a build whose own parse proves some of it can never run — a statement after a
    `return`, a constant-false branch — so a path that would silently never execute is a
    reason to hold too. Nothing is claimed and nothing is written — the child stops before
    `init_state_root`, the first thing that touches disk — so a build that answers here is
    safe to BECOME. Anything else (a nonzero exit, a signal, a hang past `timeout`) is the
    answer and the caller holds, keeping the image it is already running: the last build that
    provably loaded is the one it keeps.
    """
    argv = self_argv(here)
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True,
            timeout=RELOAD_PROBE_TIMEOUT_S if timeout is None else timeout,
            env={**os.environ, RELOAD_PROBE_ENV: "1"},
        )
    except subprocess.TimeoutExpired:
        return f"the new build did not finish loading in {timeout or RELOAD_PROBE_TIMEOUT_S:.0f}s"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"the new build could not be probed ({exc.__class__.__name__})"
    if proc.returncode == 0:
        return None
    lines = (proc.stderr or proc.stdout or "").strip().splitlines()
    return (lines[-1][:PROBE_NOTE_CHARS] if lines else f"the new build exited {proc.returncode}")


def _undefined_in(table, known: set, path: str, scope: str = "") -> str | None:
    """The first global name a scope LOADS that `known` does not hold, walking children.

    `symtable` already separates the three kinds a name can be: a local (assigned here), a
    free/cell variable (a closure's), and a GLOBAL — the only kind this asks about. A global
    load is exactly what the interpreter resolves at run time and what an import does not
    touch, so it is the whole surface of the fault being hunted.
    """
    for child in table.get_children():
        here = child.get_name() or scope
        for sym in child.get_symbols():
            if sym.is_referenced() and sym.is_global() and sym.get_name() not in known:
                return f"{path}: {here}: {sym.get_name()!r} is used but never defined"
        deeper = _undefined_in(child, known, path, here)
        if deeper:
            return deeper
    return None


def _package_modules(here: str = ""):
    """(path, source, module) for every file of this package, in a fixed order.

    The two static questions the probe asks (an undefined global, code that can never run)
    both read the SAME module set in the SAME order through this, so neither can quietly
    disagree with the other about which files this build even has.

    `here` names one file of a COPY of this package, and every file is then read from that
    copy's directory instead of from where the imported modules live. The names are still
    resolved against the live namespaces — that is what the question is about — but the TEXT
    is the copy's, so a reader can ask the question of a tree nobody is editing. A checkout
    being written while this parses it is otherwise a false failure: a half-written file
    either stops parsing (and is skipped) or parses into a name that is not defined yet.
    """
    root = os.path.dirname(os.path.abspath(here)) if here else ""
    for name in sorted(sys.modules):
        if not (name == "fbtodo" or name.startswith("fbtodo.")):
            continue
        mod = sys.modules[name]
        path = getattr(mod, "__file__", None)
        if not path or not path.endswith(".py"):
            continue
        if root:
            path = os.path.join(root, os.path.basename(path))
        try:
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
        except OSError:
            continue
        yield path, src, mod


def undefined_global_names(here: str = "") -> str | None:
    """The first global a FUNCTION in this build loads that is not defined — the deep half.

    In plain words: importing a package runs the module-level code and NOTHING else. A
    function body is name-resolved only when it RUNS, so a reference to a global that was
    renamed or deleted — a constant, a helper, a whole module — looks healthy right up until
    the one command that reaches that line is next called, and by then a self-reload has
    already replaced a working image with a time bomb. The reload probe cannot CALL every
    function (they stop watchers, open panes, write files), so it asks the question the
    interpreter would ask before each of those calls: does every name the code loads as a
    GLOBAL still exist? Every scope of every module of this package is parsed with
    `symtable`, each global load is looked up in the module's own namespace (which already
    holds what `from .x import *` and its imports bound) and in the builtins, and the first
    name that is nowhere is the answer — with the module and the enclosing function, because
    that is what the log needs to point at.

    It is deliberately not a linter. Attribute names are not symbols, so `os.path` and
    `pane["pid"]` are not asked about; a name a module binds at run time passes, because
    the lookup is the live namespace, not the parse; and a local or a closure variable is
    not a global at all. What it can still get wrong is a name a FUTURE build injects into a
    module some other way, which is why the self-check pins both sides: this checkout (which
    must pass) and a copy whose function uses a name that was deleted (which must fail).
    """
    for path, src, mod in _package_modules(here):
        try:
            top = symtable.symtable(src, path, "exec")
        except (SyntaxError, ValueError):
            continue  # a file that will not parse is `source_syntax_error`'s answer
        known = set(vars(mod)) | set(dir(builtins))
        missing = _undefined_in(top, known, path)
        if missing:
            return missing
    return None


# The statements that END a block: nothing after one of these, in the SAME suite, can run.
# `break` and `continue` are only legal inside a loop, and that is where they always appear.
_TERMINATOR_WORD = {
    ast.Return: "return", ast.Raise: "raise", ast.Break: "break", ast.Continue: "continue",
}

# The fields of a node that hold a SUITE (an ordered list of statements). Walking the tree
# and reading these is what lets one `while`/`try`/`match` case be checked without a branch
# here for every compound statement Python has.
_SUITE_FIELDS = ("body", "orelse", "finalbody")


# A value that is not a value: the answer when an expression is not made of literals. A
# sentinel rather than `None`, because `None` IS a literal whose truth is False.
_NO_VALUE = object()


def _literal_value(node):
    """The value of an expression made only of literals, or `_NO_VALUE` when it is not one.

    In plain words: what does this actually equal, with nothing left to run? `literal_eval`
    answers for a literal, a tuple/list/set/dict of them, and a sign in front of a number —
    and raises for everything else, which is exactly the line this check refuses to cross. A
    name, a call, an attribute, an f-string with a placeholder, a `not`: none of them have a
    value until the program runs, so none of them get one here.
    """
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return _NO_VALUE


def _compare_value(node):
    """True or False when EVERY operand of a comparison is a literal, else `_NO_VALUE`.

    The operators are folded by hand rather than handed to `eval`, and `is` / `is not` are
    deliberately left unfolded: identity is not a value question (two equal literals are one
    object in CPython and two in another build), so it is exactly the kind of answer this
    check must not guess. A chained comparison (`0 < n < 5`, and the literal form too) is
    folded the way Python runs one — left to right, stopping at the first false link — so a
    later link that would raise (`1 in 2`, an empty dict as a container) never gets asked.
    """
    left = _literal_value(node.left)
    if left is _NO_VALUE:
        return _NO_VALUE
    for op, comparator in zip(node.ops, node.comparators):
        right = _literal_value(comparator)
        if right is _NO_VALUE:
            return _NO_VALUE
        try:
            if isinstance(op, ast.Eq):
                ok = left == right
            elif isinstance(op, ast.NotEq):
                ok = left != right
            elif isinstance(op, ast.Lt):
                ok = left < right
            elif isinstance(op, ast.LtE):
                ok = left <= right
            elif isinstance(op, ast.Gt):
                ok = left > right
            elif isinstance(op, ast.GtE):
                ok = left >= right
            elif isinstance(op, ast.In):
                ok = left in right
            elif isinstance(op, ast.NotIn):
                ok = left not in right
            else:
                return _NO_VALUE  # `is` / `is not`, or an operator a future Python adds
        except (TypeError, ValueError):
            return _NO_VALUE  # a comparison that cannot be made (`1 in 2`) is not a guess
        if not ok:
            return False
        left = right
    return True


def _static_truth(node):
    """True or False when a test's value is provable from the parse alone, else None.

    Deliberately narrow, and narrow in only these ways: a literal, a `not` around one, or a
    comparison whose every operand is a literal (`if 1 > 2:`, `while 0 == 1:`). Anything
    whose value could turn on a name, a call or an attribute is NOT answered, because the
    answer would be a guess and this check only gets to speak when it is certain.
    """
    if isinstance(node, ast.Constant):
        return bool(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        inner = _static_truth(node.operand)
        return None if inner is None else (not inner)
    if isinstance(node, ast.Compare):
        value = _compare_value(node)
        return None if value is _NO_VALUE else bool(value)
    return None


def _dead_sentence(path: str, found: tuple[int, str]) -> str:
    """One finding as the sentence every reader of this pass already prints.

    The ONE place the `path: line N: unreachable — why` shape is written, so the first-only
    readers (`_unreachable_in`, `_guarded_in`) and the full list (`dead_code`) cannot drift
    apart: a finding is `(line, why)`, and the sentence is its rendering.
    """
    line, why = found
    return f"{path}: line {line}: unreachable — {why}"


def _dead_at(node) -> tuple[int, str] | None:
    """The arm of `node` a constant test proves can never run, as `(line, why)`, or None.

    Only the shapes whose dead arm is the arm itself: an `if` or a `while` testing a
    constant (so one whole suite is unreachable) and a conditional expression with one.
    `while True:` is deliberately NOT reported — a spinning loop is how a loop is usually
    meant to be written, and a body that is live is not dead code.
    """
    truth = _static_truth(node.test)
    if isinstance(node, ast.While):
        if truth is False and node.body:
            return (node.body[0].lineno,
                    f"the `while` on line {node.lineno} tests a constant false, "
                    "so its body never runs")
        return None
    if isinstance(node, ast.If):
        if truth is False and node.body:
            return (node.body[0].lineno,
                    f"the branch under the constant-false test on line {node.lineno} never runs")
        if truth is True and node.orelse:
            return (node.orelse[0].lineno,
                    f"the branch under the constant-true test on line {node.lineno} never runs")
        return None
    if isinstance(node, ast.IfExp) and truth is not None:
        arm = node.body if truth is False else node.orelse
        return (arm.lineno,
                f"this arm of the conditional on line {node.lineno} tests a constant")
    return None


def _unreachable_all(tree, path: str) -> list[tuple[int, str]]:
    """EVERY stretch of code in `tree` the parse proves can never run, in walk order.

    Two kinds, both certain from the source alone: a statement that follows the one which
    ends its block (`return`, `raise`, `break`, `continue` — nothing after it in that same
    suite can run), and the dead arm of a constant conditional (`if False:`, `if True: …
    else:`, `while False:`, `x if False else y` — and `if 1 > 2:`, a test whose every
    operand is a literal, folded by `_compare_value`). The order is the walk's, so the
    first entry is exactly what `_unreachable_in` used to return.
    """
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While, ast.IfExp)):
            found = _dead_at(node)
            if found:
                out.append(found)
        for field in _SUITE_FIELDS:
            suite = getattr(node, field, None)
            if not isinstance(suite, list):
                continue
            for i, stmt in enumerate(suite):
                word = _TERMINATOR_WORD.get(type(stmt))
                if word and i + 1 < len(suite):
                    after = suite[i + 1]
                    out.append((after.lineno,
                                f"nothing runs after the {word} on line {stmt.lineno}"))
    return out


def _unreachable_in(tree, path: str) -> str | None:
    """The first stretch of code in `tree` the parse proves can never run, or None."""
    found = _unreachable_all(tree, path)
    return _dead_sentence(path, found[0]) if found else None


# The operators and nodes a test may be built from and still count as SIDE-EFFECT-FREE.
# `Attribute` and `Subscript` are deliberately absent even though `self.x is None` is a
# common guard: reading an attribute runs a `__getattribute__` (or a property), and a call
# between the guard and the later test can change what it returns — which is exactly the
# kind of value this pass refuses to have an opinion about.
_PURE_UNARY = (ast.Not, ast.USub, ast.UAdd, ast.Invert)
_PURE_COMPARE = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
                 ast.Is, ast.IsNot, ast.In, ast.NotIn)


def _pure_names(node):
    """The names a side-effect-free test loads, or None when it could do anything at all.

    `None` is the only safe answer for a Call, an Await, a lambda, a comprehension, an
    f-string or a walrus — none of them can be promised to mean the same thing twice, and a
    guard's whole force is that it does.
    """
    if isinstance(node, ast.Name):
        return {node.id} if isinstance(node.ctx, ast.Load) else None
    if isinstance(node, ast.Constant):
        return set()
    if isinstance(node, ast.UnaryOp):
        return _pure_names(node.operand) if isinstance(node.op, _PURE_UNARY) else None
    if isinstance(node, ast.Compare):
        if not all(isinstance(op, _PURE_COMPARE) for op in node.ops):
            return None
        names = _pure_names(node.left)
        for comparator in node.comparators:
            got = _pure_names(comparator)
            if names is None or got is None:
                return None
            names |= got
        return names
    if isinstance(node, ast.BoolOp):
        if not isinstance(node.op, (ast.And, ast.Or)):
            return None
        names = set()
        for value in node.values:
            got = _pure_names(value)
            if got is None:
                return None
            names |= got
        return names
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        names = set()
        for elt in node.elts:
            got = _pure_names(elt)
            if got is None:
                return None
            names |= got
        return names
    if isinstance(node, ast.Dict):
        names = set()
        for key in node.keys:
            got = set() if key is None else _pure_names(key)
            if got is None:
                return None
            names |= got
        for value in node.values:
            got = _pure_names(value)
            if got is None:
                return None
            names |= got
        return names
    return None


def _same_expr(a, b) -> bool:
    """Is `b` the same expression as `a`? Compared structurally, line numbers aside."""
    return ast.dump(a) == ast.dump(b)


# The compare operators CPython defines as EXACT complements of each other. `==` / `!=` and
# the orderings are deliberately absent: a class may define `__ne__` (or `__ge__`) to answer
# something that is not `not __eq__` (or `not __lt__`), so treating those as negations would
# be assuming a value — the one thing this pass never does.
_NEGATED_COMPARE = {
    ast.Is: ast.IsNot, ast.IsNot: ast.Is,
    ast.In: ast.NotIn, ast.NotIn: ast.In,
}


def _is_negation_of(a, b) -> bool:
    """Is `a` the exact logical negation of `b`, by the language and not by a guess?

    Two shapes qualify. A leading `not` is a negation by construction, either way round. And
    a single comparison is one when it tests the SAME operands with a complementary operator
    — but only the pairs in `_NEGATED_COMPARE`, which the language promises are complements.
    An `and`/`or` test is not answered: `not (p and q)` is not a shape this pass rewrites.
    """
    if isinstance(a, ast.UnaryOp) and isinstance(a.op, ast.Not) and _same_expr(a.operand, b):
        return True
    if isinstance(b, ast.UnaryOp) and isinstance(b.op, ast.Not) and _same_expr(b.operand, a):
        return True
    if isinstance(a, ast.Compare) and isinstance(b, ast.Compare) \
            and len(a.ops) == 1 and len(b.ops) == 1 \
            and len(a.comparators) == 1 and len(b.comparators) == 1 \
            and _same_expr(a.left, b.left) \
            and _same_expr(a.comparators[0], b.comparators[0]):
        return _NEGATED_COMPARE.get(type(a.ops[0])) is type(b.ops[0])
    return False


def _assigned_names(node) -> set:
    """Every name a subtree BINDS: assignment, loop and `with` targets, imports, defs, …

    Deliberately over-inclusive (a name bound inside a nested function is collected too):
    the only use is to ask "could this sentence have changed since the guard?", and one
    name too many costs a finding while one too few costs correctness.
    """
    out = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
            out.add(child.id)
        elif isinstance(child, (ast.Global, ast.Nonlocal)):
            out.update(child.names)
        elif isinstance(child, (ast.Import, ast.ImportFrom)):
            out.update((alias.asname or alias.name).split(".")[0] for alias in child.names)
        elif isinstance(child, ast.ExceptHandler) and child.name:
            out.add(child.name)
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(child.name)
        elif isinstance(child, (ast.MatchAs, ast.MatchStar)) and child.name:
            out.add(child.name)
        elif isinstance(child, ast.MatchMapping) and child.rest:
            out.add(child.rest)
    return out


def _function_locals(func) -> set:
    """The names this function binds for itself — parameters plus everything it assigns.

    Only a LOCAL can carry a guard's promise: a name this function does not bind is a
    global or a closure cell, and a call made between the guard and the test can move it
    without a line of this function changing. Names declared `global` / `nonlocal` are
    subtracted for the same reason.
    """
    args = func.args
    names = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    for extra in (args.vararg, args.kwarg):
        if extra is not None:
            names.add(extra.arg)
    declared = set()
    for child in ast.walk(func):
        if isinstance(child, (ast.Global, ast.Nonlocal)):
            declared.update(child.names)
    for stmt in getattr(func, "body", []):
        names |= _assigned_names(stmt)
    return names - declared


def _always_terminates(stmts) -> bool:
    """Does EVERY path through this suite leave it — return, raise, break or continue?

    The half a guard needs. `if P: return` only promises anything if the return is not
    itself conditional, so an `if`/`else` counts only when both arms terminate, and a
    `with` counts when its own body does (leaving a `with` leaves it even if `__exit__`
    swallows the exception). `for` and `while` are not answered: a loop may run zero times,
    and proving otherwise is a different question from this one.
    """
    for stmt in stmts:
        if type(stmt) in _TERMINATOR_WORD:
            return True
        if isinstance(stmt, ast.If) and stmt.orelse \
                and _always_terminates(stmt.body) and _always_terminates(stmt.orelse):
            return True
        if isinstance(stmt, (ast.With, ast.AsyncWith)) and _always_terminates(stmt.body):
            return True
        if isinstance(stmt, ast.Try):
            if _always_terminates(stmt.finalbody):
                return True
            if _always_terminates(stmt.body) \
                    and all(_always_terminates(h.body) for h in stmt.handlers):
                return True
    return False


def _nested_guard_suites(stmt, path: str, locals_: set, facts):
    """Guard-check the suites nested in ONE statement, with the facts in force at it.

    The blocks a statement ENTERS are all dominated by whatever held before it — a `with`
    body, both arms of an `if`, a `try` body and its handlers, and a loop's body and else —
    so a test there is asked against the same facts. The caller hands in only the facts the
    statement cannot disturb (so a loop body is entered with the facts its own assignments
    left standing), and a suite holding another `def` is left to that scope's own turn.
    """
    for field in _SUITE_FIELDS:
        suite = getattr(stmt, field, None)
        if isinstance(suite, list) and suite:
            yield from _guard_suites(suite, path, locals_, facts)
    for handler in getattr(stmt, "handlers", None) or []:
        yield from _guard_suites(handler.body, path, locals_, facts)
    for case in getattr(stmt, "cases", None) or []:
        yield from _guard_suites(case.body, path, locals_, facts)


def _guard_suites(stmts, path: str, locals_: set, facts=()):
    """EVERY branch an earlier guard — here or in a block that dominates this one — rules out.

    In plain words: `if P: return` at the top of a function means P is FALSE for every line
    below it — control only gets past the guard by not entering it. So a later `if P:` can
    never run, and a later `if not P:` is always true. `assert P` makes the same promise by
    the other route: control continues past it only when P held, so the same two branches are
    dead below it. That is the whole rule, and the four things that keep it honest are all
    here: the guard must LEAVE on every path (`_always_terminates`) or raise (`assert`), the
    test must be side-effect-free (so it means the same thing twice), no statement between the
    two may bind one of the names it reads, and a name this function does not bind at all is
    somebody else's to change — refused outright.

    The fact also holds inside any block the guard DOMINATES, so it is carried into the bodies
    of later `if` / `with` / `try` statements and their handlers (`_nested_guard_suites`), where a
    third look at the same test is caught too. A LOOP body is entered as well, and that is the
    one place a repeat matters: a later pass through the body sees whatever the previous one
    left behind, so only the facts the loop cannot disturb go in — a fact whose names the loop
    ASSIGNS anywhere is dropped first (the same `_assigned_names` filter every statement gets,
    and for a loop it is also the cross-iteration guarantee). A test settled before the loop
    and repeated inside a `for`/`while` body is therefore caught, while one the body could
    have moved is not. One thing is never crossed: a SCOPE boundary (a nested `def`/`class`,
    whose own turn it gets with its own locals).

    `assert` is believed on purpose, the way every static reader believes it: the pass runs on
    a build about to be exec'd, never under `-O` (which strips asserts), so a fact taken from
    one is a fact where this code actually runs.
    """
    facts = list(facts)  # [(test node, guard line, {names it loads}, truth, kind)]
    for stmt in stmts:
        # Another scope: its body is asked on its own turn, with its own locals — nothing is
        # carried across the boundary, and its own bindings are not this suite's business.
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        dead = None
        if isinstance(stmt, ast.If):
            sent = _pure_names(stmt.test)
            # `sent <= locals_` is the line that keeps this honest: a name this function
            # does not bind is a global or a closure cell, and a call in between could
            # move it without a line of this function changing.
            if sent is not None and sent and sent <= locals_:
                for fact in facts:
                    dead = _dead_arm_at(stmt, fact)
                    if dead:
                        break
        # The first finding for this `if` is emitted before we go on: an `if` can have only
        # one dead arm, and the walk continues so a later statement can be found too.
        if dead:
            yield dead
        # The bindings this statement performs end any fact about the names it binds: the
        # sentence would no longer mean what the guard proved. (Its own test was asked
        # first, above, because a test is evaluated before its body runs.)
        if facts:
            rebound = _assigned_names(stmt)
            facts = [f for f in facts if not (f[2] & rebound)]
        # Carry what survives into the blocks this statement enters. A loop is entered too:
        # the filter above already dropped every fact whose names the loop assigns ANYWHERE
        # (`_assigned_names` walks the whole loop — target, body and else), so what is left is
        # a fact a previous iteration could not have changed, and a repeat of its test inside
        # the body is a real dead end. Facts a loop DOES disturb are gone before we descend.
        yield from _nested_guard_suites(stmt, path, locals_, facts)
        # A guard records its promise for everything after it, here and in the blocks below:
        # `if P: <leaves>` and `assert P` are the two shapes. They are asked the same two
        # questions of the test — that it is pure, and that it reads only this function's own
        # names — and they differ in ONE way: a guard is passed only when its test is FALSE
        # (so P holds after it), while an `assert` is passed only when its test is TRUE (the
        # raise is what an `assert` takes instead of a body to leave with). `truth` carries
        # that, and `_dead_arm_at` reads it back.
        if isinstance(stmt, ast.If) and stmt.body and _always_terminates(stmt.body):
            test, truth, kind = stmt.test, False, "guard"
        elif isinstance(stmt, ast.Assert):
            test, truth, kind = stmt.test, True, "assert"
        else:
            continue
        names = _pure_names(test)
        if names and names <= locals_:
            facts.append((test, stmt.lineno, names, truth, kind))


def _dead_arm_at(stmt, fact) -> tuple[int, str] | None:
    """The arm of this `if` a prior fact rules out, as `(line, why)` — or None.

    In plain words: a fact says one test was settled before this line, one way or the other.
    If this `if` asks the SAME test, the answer is known and one of its arms can never run;
    if it asks the exact NEGATION (again by the language, not by a guess — see
    `_is_negation_of`), the answer is the other way and the other arm dies. Which arm that is
    depends on the fact's own polarity: a guard is passed when its test is false, an `assert`
    when it is true. Reading an unknown test is not an answer, and neither is a test the
    fact's names do not cover — both return None and the walk goes on.
    """
    guard, guard_line, _guard_names, truth, kind = fact
    sent = _pure_names(stmt.test)
    if sent is None or not sent:
        return None
    same = _same_expr(stmt.test, guard)
    negated = _is_negation_of(stmt.test, guard)
    if not (same or negated):
        return None
    label = (f"the guard on line {guard_line}" if kind == "guard"
             else f"the `assert` on line {guard_line}")
    if truth is False and same:
        arm, why = stmt.body, f"{label} again, and control only reaches here when it is false"
    elif truth is False and negated:
        arm, why = stmt.orelse, f"{label} negated, so it is already true here"
    elif truth is True and same:
        arm, why = stmt.orelse, f"{label} again, so it is already true here"
    else:
        arm, why = stmt.body, f"{label} negated, so it is already false here"
    if not arm:
        return None
    return (arm[0].lineno, f"the test on line {stmt.test.lineno} is {why}")


def _guarded_suites(tree, path: str):
    """EVERY branch an earlier guard in the SAME function proves can never run, in scope order.

    The scope is walked function by function, never across a boundary: each `def` is asked
    about its own locals and its own suites, so a guard in one function can never speak for
    another, and a nested `def` is scanned on its own turn.
    """
    scopes = [n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for func in scopes:
        yield from _guard_suites(getattr(func, "body", []), path, _function_locals(func))


def _guarded_in(tree, path: str) -> str | None:
    """The first branch an earlier guard in the SAME function proves can never run, or None."""
    found = next(_guarded_suites(tree, path), None)
    return _dead_sentence(path, found) if found else None


def unreachable_code() -> str | None:
    """The first code in this build that can never run — the other deep half.

    In plain words: a name that no longer exists is one way a build is quietly wrong; code
    that its own source proves will never execute is the other. A statement after a
    `return` in the same suite, a whole `if False:` branch, a body under `while False:` —
    each is invisible to an import (it is valid Python, it compiles), so it survives every
    check until someone notices the path it should have run is not running. The probe has
    just asked whether the build CAN be a process; this asks whether the process would do
    what the source says. Only certainties are reported: a test that is a literal, a `not`
    around one, or a comparison whose EVERY operand is a literal (`if 1 > 2:`, `while
    0 == 1:`, a chained `0 < 1 < 0`) — and code the parse can place behind a terminator.
    Nothing that could depend on a value at run time is guessed at, so a comparison against
    a name, an `in` that cannot be made (`1 in 2`), or an identity test (`is` is not a value
    question) is left alone and no live branch is ever called dead.

    The second half reasons about a guard rather than a literal (`_guarded_in`): inside one
    function, `if P: return` at the top means P is false for every line below it, so a later
    `if P:` can never run and a later `if not P:` can never take its `else`. The promise is
    only made where the parse can keep it — a body that leaves on every path, a test with
    no way to do anything but read locals this function binds, and no statement in between
    that rebinds one of those names. It is carried into the blocks the guard dominates (a
    later `if`/`with`/`try` body, a handler, and a loop body — but a loop only with the facts
    its own assignments leave standing, since a repeat will see the previous pass's work).
    It never crosses a scope, so no guard is ever asked to speak for a variable another
    function or another pass controls.

    It is pinned both ways like the name pass: this checkout must pass, and a copy carrying
    a statement after a `return`, a constant-false branch, a literal comparison that is
    false, or a branch a guard rules out must fail and name it.

    This is the FIRST finding — what the reload probe needs to hold a build. `dead_code` is
    the same walk with every finding kept, for `fbtodo dead`.
    """
    findings = dead_code()
    if not findings:
        return None
    first = findings[0]
    return _dead_sentence(first["path"], (first["line"], first["why"]))


def dead_code() -> list[dict]:
    """EVERY piece of code the package's own source proves can never run, across the build.

    The same two passes `unreachable_code` reads — `_unreachable_all` for literals and for
    what follows a terminator, and `_guarded_suites` for a guard's promise — asked of every
    file with ALL findings kept instead of the first: `{path, line, why}`, in the order the
    per-file first-only reader would have met them, so `dead_code()[0]` is exactly what
    `unreachable_code` reports. A file that will not parse contributes nothing — that is
    `source_syntax_error`'s answer, not this one's.
    """
    out: list[dict] = []
    for path, src, _mod in _package_modules():
        try:
            tree = ast.parse(src, path)
        except (SyntaxError, ValueError):
            continue  # a file that will not parse is `source_syntax_error`'s answer
        for line, why in (*_unreachable_all(tree, path), *_guarded_suites(tree, path)):
            out.append({"path": path, "line": line, "why": why})
    return out


def pinned_path() -> str:
    """The PATH every pane — and every child of this program — is handed explicitly.

    In plain words: stop asking tmux. A pane's environment is REBUILT by the server, so a pane
    command that names `fbtodo` on PATH and leans on `#!/usr/bin/env python3` can come up on a
    different interpreter than the process that asked for it. Measured 2026-10-01 on this
    machine: a pane respawned into the running server came up on `/usr/bin/python3` (3.9.6)
    while its watcher was Homebrew's 3.14 — and the pane and the watcher then disagree about
    the Python under them, which is not a version question but a reproducibility one: the
    same PATH decides the interpreter for `scripts/notify/*.py`, so a bell and the watcher that
    rang it could be two different Pythons.

    The answer is the PATH the opener itself had — the owner's own, at the moment they asked —
    carried into the pane's command line on purpose (`pane_command` in panes.py), so nothing
    about it is left to the server's rebuild. `FBTODO_PATH` replaces the base for a machine
    that needs a specific one; a pane that was opened pinned needs nothing special to pass its
    own pin on, since the pin IS a PATH and that is what the opener reads.

    This interpreter's own directory goes FIRST, ahead of whatever the opener had: the pane is
    on `sys.executable` by construction, but everything the pane then starts by name is not —
    `#!/usr/bin/env python3` in `scripts/notify/` is the interpreter question all over again,
    one level down, and "the pane and its watcher agree about the Python" is only true if the
    same rule holds there. A duplicate of that directory is dropped from the rest, so the
    result is still a PATH and not a search path with one entry twice.
    """
    here = os.path.dirname(sys.executable)
    base = os.environ.get("FBTODO_PATH") or os.environ.get("PATH") or ""
    rest = [p for p in base.split(os.pathsep) if p and os.path.realpath(p) != os.path.realpath(here)]
    return os.pathsep.join([here, *rest])


# The own-environment settings a pane must be HANDED rather than left to inherit, in the
# order they are written into a pane command. tmux starts a pane from its SERVER's
# environment (and `respawn-pane` re-runs the recorded command under that same one), not
# from the environment of the process that asked for the pane — so each of these is
# whatever the server was started with, which may be a shell from before this one, or a
# login shell's own profile. Every key here decides WHERE a pane works or WHICH project it
# follows: the state root is the pair `_state_root` reads (FBTODO_HOME first, XDG_STATE_HOME
# second), FBTODO_TMUX names the tmux server a pane drives, and FBTODO_FB_MARKER names the
# session marker it counts live sessions by. Losing one moves the pane — and the watcher it
# starts — to another store, another server or another set of sessions, with nothing on
# screen to say so. The six notify-watch paths are the same argument one step out: they
# decide WHERE the pane's bells GO, so a machine that points its watches at its own scripts
# (the shipped notify kit, a stub in a test) must have the pane's watcher use the same ones
# — otherwise every bell falls back to `~/.config/freebuff-notify/`, which on that machine
# is either absent or somebody else's. (The watches' cadences are not here either: a number that moves how OFTEN a bell
# rings is not a path that moves where it goes, and the script it rings is the half that
# could be lost without a word.)
PINNED_ENV_KEYS = (
    "FBTODO_HOME", "XDG_STATE_HOME", "FBTODO_TMUX", "FBTODO_FB_MARKER",
    "FBTODO_NOTIFY", "FBTODO_DROP", "FBTODO_ASK", "FBTODO_PAUSE",
    "FBTODO_PANE_BELL", "FBTODO_LOCKS_BELL",
)


def pinned_env() -> list[str]:
    """`KEY=value` for every own-environment setting that decides where a pane works.

    In plain words: the pane command already names the interpreter and carries the PATH
    (`pinned_path`); this is the other half — the values the program reads from its OWN
    environment to choose a state root, a tmux server, the sessions it follows, and the six
    scripts its bells are sent to. They ride in the same command line, so a `respawn-pane`,
    the keeper's repair, or a login shell that sources its own profile brings the opener's
    answer back instead of the server's. Values
    that are NOT set are left out rather than carried empty: an empty `FBTODO_HOME=` in the
    command line would read as "this process names the root" to the locks audit, which must
    keep telling a command line apart from an environment (`_proc_environ` in locks.py).
    """
    return [f"{key}={os.environ[key]}" for key in PINNED_ENV_KEYS if os.environ.get(key)]


def python_of(command: str) -> str | None:
    """The interpreter a command line names, or None when it names none.

    In plain words: which python is that process on? Read from the line rather than assumed,
    and written to survive the two ways a pin is spelled: `/usr/bin/env PATH=… python3 …`
    (the `env` and its assignment are skipped) and a bare absolute interpreter. Anything whose
    basename is not a python is no answer rather than a wrong one — a pane whose command is
    still a shell, or a process that has gone, must not be reported as running `/bin/zsh`.

    The name is matched without regard to case on purpose: the interpreter Homebrew names
    `python3.14` is named `Python` where macOS keeps its own, inside the framework bundle
    (`…/Python.app/Contents/MacOS/Python`) — the exact line a machine that has both produces,
    and the one a case-sensitive `python` test silently refused to answer about.
    """
    for token in shlex.split(command)[:3]:
        if token.endswith("env") or "=" in token:
            continue
        return token if os.path.basename(token).lower().startswith("python") else None
    return None


LOCK_PATH = os.path.join(SCRATCH, "fbtodo-daemon.pid")


LOG_PATH = os.path.join(SCRATCH, "fbtodo-daemon.log")





# The pane keeper's claim. A keeper is neither watcher: it has no store, no state file and
# no instance of its own — only the panes. Its own lock because being kept out by a
# watcher's lock was the whole problem: a second daemon holds that one, and a killed pane
# then went unopened. One keeper per tmux server, which is why the record carries the
# server it belongs to.
PANE_KEEPER_PATH = os.path.join(SCRATCH, "fbtodo-pane-keeper.pid")


PANE_LOG_PATH = os.path.join(SCRATCH, "fbtodo-pane.log")


# What a keeper leaves for a pane it is about to reopen: `{"%499": {"at_ms": …, "was": …,
# "child": …}}`. The chip note cannot ride the environment the way a pane's reload does —
# `respawn-pane` starts a fresh command in the SERVER's environment, with no exec of this
# process's to carry it — so it waits at a path instead: written before the respawn, claimed
# by the pane on its first breath (its own id is `TMUX_PANE`), and dropped unshown when it
# has waited too long.
PANE_NOTE_PATH = os.path.join(SCRATCH, "fbtodo-pane-note.json")


# What each todo pane has latched itself to, one small file per pane process:
#   pane-subjects/<pid>.json -> {"subject": "cli:<chat dir>" | "desktop:<thread id>", "at_ms": …}
# The lock itself lives in the pane's OWN environment (`PANE_LOCK_ENV`), and that is not
# readable from outside on every platform: the kernel snapshots a process's environment at
# exec, so a value written at runtime — which is exactly what `latch_pane_subject` does — is
# invisible to `ps` and to every other reader. The pane therefore WRITES ITS SUBJECT DOWN as
# well, and `fbtodo panes` reads the records back, keyed by the pid the process table already
# gives it. A directory of one file per pid rather than one shared file, so two panes
# starting together cannot lose each other's entry to a read-modify-write race.
PANE_SUBJECT_DIR = os.path.join(SCRATCH, "pane-subjects")


# Where the list pane goes, per window (`fbtodo pin`):
#   {`session:index`: {side, size}}
# The halves are the window's answer, flat. A pin file written while a window held two list
# panes may carry its answer under a `local` key instead; that half is read as the window's
# own and dropped on the next write, so an existing file keeps working and cannot disagree
# with itself afterwards.
PINS_PATH = os.path.join(SCRATCH, "fbtodo-pins.json")


# Where it was left last time, per window and per role: {`session:index`: {local: …}}.
LAST_PATH = os.path.join(SCRATCH, "fbtodo-last.json")


# What the pane's own mute key last did: {`until`: "list"|"sticky", `was`: {…}, `at_ms`,
# `session`, `list_id`}. It is the switch's MEMORY, and it has to be on disk rather than in
# the pane: a pane re-execs itself into every new build it sees (see `BUILD_CHECK_S`), so a
# mute held in a variable would be dropped by the very upgrade it was pressed a minute
# earlier — leaving the switches `off` with nothing left to turn them back on. It also names
# the list the mute was taken for, so a pane that comes back to a finished list can honour
# the promise the first one made ("until this list finishes") and unmute on its own.
MUTE_PATH = os.path.join(SCRATCH, "fbtodo-pane-mute.json")


def _state_paths(root: str) -> dict:
    """The `{name: path}` bundle above, for one root."""
    return {
        "SCRATCH": root,
        "STATE_PATH": os.path.join(root, STATE_FILE_NAME),
        "TASKS_PATH": os.path.join(root, "fbtodo-tasks.json"),
        "LOCK_PATH": os.path.join(root, "fbtodo-daemon.pid"),
        "LOG_PATH": os.path.join(root, "fbtodo-daemon.log"),
        "PANE_KEEPER_PATH": os.path.join(root, "fbtodo-pane-keeper.pid"),
        "PANE_LOG_PATH": os.path.join(root, "fbtodo-pane.log"),
        "PANE_NOTE_PATH": os.path.join(root, "fbtodo-pane-note.json"),
        "PANE_SUBJECT_DIR": os.path.join(root, "pane-subjects"),
        "PINS_PATH": os.path.join(root, "fbtodo-pins.json"),
        "LAST_PATH": os.path.join(root, "fbtodo-last.json"),
        "MUTE_PATH": os.path.join(root, "fbtodo-pane-mute.json"),
    }


def _install_paths(bundle: dict, note) -> None:
    """Rebind the state paths (and the note) here and in every module that star-imported them.

    `from .base import *` gave each module its OWN copy of these names, so patching the
    package alone would leave an owner's copy — the one its code reads — at the old root.
    The write goes to every loaded module of this package that holds the name, which is
    what "the module's global" meant when all of it was one file (the self-check's
    `set_knob` does the same thing for its knobs).
    """
    bundle = dict(bundle, STATE_NOTE=note)
    me = sys.modules.get(__name__)
    owners = [m for n, m in list(sys.modules.items())
              if (n == "fbtodo" or n.startswith("fbtodo.")) and m is not None]
    if me is not None and me not in owners:
        owners.append(me)
    for owner in owners:
        for name, value in bundle.items():
            if hasattr(owner, name):
                setattr(owner, name, value)


def init_state_root():
    """Choose the state root for real, moving the legacy store if that is what it takes.

    Called from `main()` before anything reads a path. Import time picks the SAME root from
    the same facts but performs nothing, so a module import never creates a directory or
    moves a file; this is the one place the migration happens.
    """
    root, note = _state_root(migrate=True)
    _install_paths(_state_paths(root), note)
    return root


# The phone notifier (the finish notification's sender): it owns the decision AND the
# "already pushed" record, so fbtodo only has to point at it. Optional — a machine
# without the notify dir simply never asks.
TODO_NOTIFY = os.path.expanduser(
    os.environ.get("FBTODO_NOTIFY")
    or os.path.join(HOME, ".config", "freebuff-notify", "todo-bell.py")
)


# The drop watch: asked when a session STOPS instead of ending. A session can die with the
# terminal under it — the marker's pid is gone while the marker is still there — and that
# is a different question from "did the task finish", so it has its own script (and its own
# run-once-per-death record). Optional, like the notifier above.
DROP_NOTIFY = os.path.expanduser(
    os.environ.get("FBTODO_DROP")
    or os.path.join(HOME, ".config", "freebuff-notify", "drop-bell.py")
)


# The ask watch: asked while a session runs, and the only notifier here that is about the
# PRESENT rather than the past — a question is on screen and the agent is stopped until it
# is answered. It reads panes, not this session's store, so every watcher covers the whole
# tmux server; its own record makes the overlap harmless. Optional, like the other two.
ASK_NOTIFY = os.path.expanduser(
    os.environ.get("FBTODO_ASK")
    or os.path.join(HOME, ".config", "freebuff-notify", "ask-bell.py")
)


# The stall watch: the other half of the ask watch's question. A question is the CLI
# waiting for YOU; a stall is the CLI having stopped without ending its turn — the step
# cap cutting a turn short, a loop wedged — while the list still has work in it. Optional,
# like the other notifiers.
PAUSE_NOTIFY = os.path.expanduser(
    os.environ.get("FBTODO_PAUSE")
    or os.path.join(HOME, ".config", "freebuff-notify", "pause-bell.py")
)


# The pane watch: the other half of the keeper's promise, and the only one of the four
# that is about a thing being BROKEN rather than a session waiting. A session with no todo
# pane, or a tmux server with no keeper to put one back, is invisible from every store and
# from the pane log too — that log records a pane that came back, never one that did not.
# It reads the layout from `why --json` rather than re-deriving the session/pane matching
# the keeper acts on, and takes the keeper's record path from here so the two cannot drift.
PANE_NOTIFY = os.path.expanduser(
    os.environ.get("FBTODO_PANE_BELL")
    or os.path.join(HOME, ".config", "freebuff-notify", "pane-bell.py")
)


# The locks watch: the fifth bell, and the only one whose subject is a CLAIM rather than a
# session. A watcher or keeper running with no claim a reader can find (an orphan),
# or a claim file whose name and inode have parted (the tie broken), is invisible from every
# store — the audit that sees both (`fbtodo locks`) is the only place either exists, and
# nothing runs it unless somebody types it. Asked by `locks --watch` when a NEW finding
# appears; reads `locks --json` itself, and keeps its own record of what it has pushed, so a
# watch that runs for hours pushes once per occurrence. Optional, like the other four.
LOCKS_NOTIFY = os.path.expanduser(
    os.environ.get("FBTODO_LOCKS_BELL")
    or os.path.join(HOME, ".config", "freebuff-notify", "locks-bell.py")
)

# The notify kit's OWN directory, which is where its two mute switches live: `phone-state`
# is what `phone.sh` reads (with `FREEBUFF_PHONE` ahead of it) and `state` is what
# `bell.sh` reads (with `FREEBUFF_BELL` ahead of it). The pane's mute key writes those two
# words rather than reaching for a script of its own, so muting is the same act whoever
# performs it — a key, a `FREEBUFF_PHONE=off` in a wrapper, or a hand-edited file — and the
# scripts need no new switch of their own. `FBTODO_NOTIFY_DIR` points the pane at another
# kit's (a test's, a second user's); it is deliberately NOT in `PINNED_ENV_KEYS` with the
# six watches: those decide where a bell is SENT, and this decides where a switch word is
# written, which the default already gets right through `HOME` like every other notify path.
NOTIFY_DIR = os.path.expanduser(
    os.environ.get("FBTODO_NOTIFY_DIR") or os.path.join(HOME, ".config", "freebuff-notify")
)


# tmux overridable so tests drive a private server instead of the owner's.
TMUX_BIN = shlex.split(os.environ.get("FBTODO_TMUX") or "tmux")


# Every subcommand this program issues, in one list: a name missing here would run against
# the DEFAULT server while FBTODO_TMUX points at another one — and the tests run on a
# private socket, where `move-pane` on the owner's server would move a real pane.
TMUX_SUBCOMMANDS = (
    "split-window",
    "move-pane",
    "resize-pane",
    "respawn-pane",
    "kill-pane",
    "list-panes",
    "display-message",
    "list-clients",
    "list-sessions",
    "set-option",
    "show-options",
)


DEFAULT_DB_GLOB = os.path.join(HOME, ".config/freebuff-desktop/projects/*/desktop-v2.db")


DEFAULT_WORKSPACE_STATE = os.path.join(HOME, ".config/freebuff-desktop/state.json")


DEFAULT_CLI_ROOT = os.path.join(HOME, ".config/manicode/projects")


# The two facts about the CLI patches themselves, shown in the pane so neither has to be
# fetched with an ssh probe: the last outcome of the patch step, and the last alert that
# was pushed. Each is read from the log the producing step already writes, so the pane
# keeps no state of its own and there is nothing to fall out of sync.
#
#   local   ~/.config/freebuff-patch-watch/watch.log   the launchd watcher's own log
#           ~/.config/freebuff-notify/phone.log        every alert the kit has sent
#
# FBTODO_PATCH_LOG / FBTODO_ALERT_LOG point either at a fixture.
PATCH_LOG = os.path.expanduser(
    os.environ.get("FBTODO_PATCH_LOG")
    or os.path.join(HOME, ".config", "freebuff-patch-watch", "watch.log")
)


PATCH_META = os.path.expanduser(
    os.environ.get("FBTODO_PATCH_META")
    or os.path.join(HOME, ".config", "manicode", "freebuff-metadata.json")
)


ALERT_LOG = os.path.expanduser(
    os.environ.get("FBTODO_ALERT_LOG")
    or os.path.join(HOME, ".config", "freebuff-notify", "phone.log")
)


PATCH_TAIL_BYTES = 8192   # both logs are append-only and grow: only the last entries are read


PATCH_REASON_MAX = 60     # the row is one line, so a reason is clipped rather than wrapped


PATCH_TAIL_LINES = 4      # how much of each log the row may quote

FB_PROC = "bin/freebuff"


FB_NAME = "freebuff"


# Where a Linux machine keeps every process's working directory. macOS has no /proc, so
# cwd discovery asks this first on both and falls back to one batched lsof (see `cwds_for`).
PROC_ROOT = "/proc"


# The `freebuff` a bare command name means: one PATH lookup for the process, not one per
# candidate per poll. A list, not a scalar, so a caller (and the self-check) can refill it.
_FREEBUFF_WHICH: list = []


CHUNK = 1 << 20


MAX_SCAN = 64 << 20


# A list is only half the story: the prompt it was written for is what makes it
# legible hours later, when the session has worked on without rewriting it. Prompts
# shorter than this are continuations ("continue", "yes", "ok"), not goals.
PROMPT_MIN_CHARS = 12


# A continuation is a request to keep going, not a new task, and it asks for one thing
# of the agent: re-write the list BEFORE the work resumes (AGENTS.md). Detected on WORDS
# rather than length, so "continue", "continue please", "go on then" and "keep going
# with it" all read as nudges while "continue the other work" stays a real request.
NUDGE_WORDS = {
    "continue", "cont", "carry", "keep", "going", "go", "proceed", "resume", "next",
    "ahead", "on", "then", "now", "please", "pls", "and", "with", "the", "it", "that",
    "yes", "yep", "ok", "okay", "sure", "fine",
}


NUDGE_MAX_CHARS = 40  # a nudge is a phrase; longer means it is saying something else


GOAL_CHASE_CHUNKS = 4  # extra 1 MiB chunks to look back for a usable goal prompt


TASK_MAX_LINES = 3     # pane only: one verbose step must not fill the whole strip


# The agent's own objective, asked for by AGENTS.md. Anchored per line and case-insensitive
# so prose that merely mentions the word ("...the goal: unclear") is not mistaken for it.
GOAL_LINE_RE = re.compile(r"^\s*(?:[-*#>]+\s*)?(?:\*\*)?goal(?:\*\*)?\s*[:：]\s*(\S.*)$", re.I)


# The wrapper's pane is a full-width strip (50 columns here) and `big goal · ` spends 11
# of them, so this is the longest heading that still renders WHOLE on one line. AGENTS.md
# carries the same number for the agent; the self-check compares the two.
GOAL_MAX_CHARS = 38


# The one-line "what happened" the phone message carries under the heading. Long enough
# for a sentence about the fix, short enough to read on a lock screen.
SUMMARY_MAX_CHARS = 200


# Which records could carry prose, found WITHOUT json-parsing them: most iterations write
# an empty `"fullResponse":""`, and the needle must accept both the CLI's compact writer
# and a spaced one (`": "`), which is what a fixture writes. Matching a value that starts
# with a non-quote byte rules the empty form out in one pass.
PROSE_NEEDLE_RE = re.compile(rb'"fullResponse"\s*:\s*"(?!")')


# The model the session is running, read without parsing: every iteration record carries
# it, and a step's remembered span means something only against the model that took it —
# a `deepseek` step and a `space-bunny` step are not the same quantity.
MODEL_RE = re.compile(rb'"model"\s*:\s*"([^"]{1,80})"')


HEARTBEAT_GRACE = 5.0  # seconds a state file stays "fresh"


# The watcher rewrites its state every poll because the heartbeat has to stay young. The
# heartbeat is the only thing in it that moves when nothing has happened, so the rewrite is
# gated on one of two things: something changed, or the ON-DISK heartbeat is about to fall
# out of that window. A third of the grace leaves room for a slow poll.
HB_REFRESH = max(1.0, HEARTBEAT_GRACE / 2.5)


# What moves every poll without saying anything: the heartbeat, the "seen at" stamps, and
# any field anywhere in the state that is itself a moment (`*_ms`) or an age (`age_s` — the
# patch row's and the alert's carry their own). Everything else is evidence, and two states
# with the same evidence are the same state as far as any reader is concerned. Measured on
# the live session, those are the ONLY things that move when nothing has happened: the four
# stamps, the two ages, and a running step's `elapsed_ms` — which the pane re-derives from
# `started_ms` and its own tick anyway (see `live_elapsed`), so a state two seconds old
# draws exactly the same frame as one written this second.
STATE_CLOCK_KEYS = ("heartbeat_ms", "probed_ms", "observed", "source_updated_ms",
                    "store_mtime_ms", "age_s")


# Retention. The scratch directory is housekeeping, not history: task records are
# dropped once they age out or exceed the count cap, the daemon log is truncated at
# the cap, and temp files orphaned by a hard kill are swept. Enforced at most once
# per PRUNE_INTERVAL so the hot path (a status bar polling every second) stays cheap.
# ...but the ESTIMATES are the one thing here that is supposed to get better with use, and
# the old 7 days / 500 records could not give them enough to learn from: measured 2026-09-29
# on this machine, the log held 176 records over 3 days of which only 39 carried a finished
# span, and its oldest record was 6.68 days old — the prune was cutting the memory off
# exactly as it matured, leaving the shape memory with one sample and the wording memory
# with nothing. 60 days / 2000 records is the trade: about a month of real use at 182 bytes
# a record (≈365 KB, parsed in a few ms on each poll) instead of a week.
MAX_TASK_RECORDS = 2000


MAX_TASK_AGE_DAYS = 60


LOG_CAP_BYTES = 1 << 20


PRUNE_INTERVAL_S = 3600.0


TEMP_MAX_AGE_S = 3600.0


# Per-task estimate memory: a step's remembered span lives as long as its record, and the
# map is capped to the most recently seen names, so a long-dead wording cannot drag the
# pace it is never asked about.
HISTORY_MAX_AGE_DAYS = MAX_TASK_AGE_DAYS


# Cap on the WORDING map, which grows without bound (a new label per step, essentially).
# The shape map, which is what can actually accumulate, is keyed by a space of a few dozen
# buckets, so this cap never binds it — no bucket is ever evicted for being old.
HISTORY_MAX_ENTRIES = 400


SHAPE_MIN_SAMPLES = 2    # remembered steps a size needs before it may set anyone's number


# The size memory is keyed by FINISHED steps' full tallies but looked up with a RUNNING step's
# partial one, and a partial tally is a smaller step: at its first call a step sits a median of
# 3 buckets below the one it will end in, and it reaches its final bucket only 71% of the way
# through its own clock (measured 2026-09-30 over the 171 ticked steps whose journals record a
# per-call trail). So the lookup is not asking "how long does a step this small take" — it is
# asking "how long did the steps that stopped this small take", which is a different and much
# smaller population. Asked at the first call the rung misses by a median 14.1x and beats the
# list's pace on 17% of steps; at 25% of the clock, 3.3x and 42%; only past half the clock does
# it draw level. A floor at `calls4` (16 calls so far) is where the question starts to have an
# answer: the number it sets is then a median 1.96x / 3.38x mean off against the pace's 2.45x /
# 4.39x, beating that same pace on 79% of the steps it fires on, and it scores better in 100%
# of session bootstraps (2000 resamples over 14 sessions) where every floor below it loses
# outright (2, 4) or merely ties (8, 12). Re-scored 2026-09-30 on the post-refit forecast
# LEDGER instead of the journal replay, the advantage does not appear (floor 4 fires on 5 rows,
# median 2.30x against that pace's 1.51x) — because the ledger samples the FIRST poll, where
# the rung is known to be at its worst; see docs/ESTIMATES.md, "Re-scoring the size floor".
# Below the floor the rung stands aside and the blend or the pace answers — the pace is the
# floor of the ladder, so standing aside costs nothing.
SHAPE_MIN_BUCKET = 4     # `calls4` = 16 calls: the youngest tally a running step may be sized at


SPREAD_MIN_SAMPLES = 2   # measured steps needed before a spread may be claimed at all


SPREAD_MIN_RATIO = 3.0   # hi/lo at which one number stops being the honest form


# A span shorter than this is a list flip, not work: the agent ticked a step and the next
# write showed the following one in flight within seconds. Measured 2026-09-29 over the 258
# steps recovered from the CLI journals, 9 of 170 ticked spans were under 10s and they
# poisoned every score they touched (a 2s step projected a whole list at `~2s`, a 45x miss).
# Such a span is still SHOWN — it is what happened — but it is not evidence: it sets no pace
# and moves no memory. Tunable per machine with `--label-floor SEC` / `FBTODO_LABEL_FLOOR`;
# 0 keeps every span, which is what the earlier builds did.
LABEL_FLOOR_S = float(os.environ.get("FBTODO_LABEL_FLOOR") or 10.0)


MIN_LABEL_MS = max(0, int(LABEL_FLOOR_S * 1000))


# How much of a WAITING step's number comes from its wording rather than the list's pace, in
# log space: 0 is the pace alone, 1 is the wording alone, 0.5 is the default.
# Be honest about what 0.5 buys, because it is less than it first looked. Measured 2026-09-29
# by leave-one-session-out replay of the 160 real spans, scoring the shipped `pending_blend_ms`
# against the same step's pace: it is a COIN FLIP on the typical step (better on 77 of 160,
# worse on 83, median ratio 1.01x) and a real gain only in the tail (mean 3.31x against 3.75x,
# worst 14.9x against 33.9x). An earlier reading of this - "beats the pace on every column,
# 74% of steps" - was a harness bug: it ordered each session's steps by span instead of by
# time, so the "pace so far" it compared against was the session's smallest steps. So keep the
# blend as a tail-smoother, not as a median win, and let the `forecast error` scoreboard settle
# it on real traffic.
# Refit 2026-09-29 on the same 161 spans, paired and bootstrapped over sessions (every step
# decides here, because the blend changes nearly every number):
#
#     w=0 (pace alone)  even     — beats this one on 50% of steps, 51% of resamples
#     w=0.25            53%      80% of resamples — not significant, and worse on the mean/worst
#     w=0.75            44%       6% of resamples — worse
#     w=1.0             42%       2% of resamples — the only comparison that RESOLVES
#
# So the one thing the data settles is that handing the number to the label model outright
# (1.0) is worse, in 98% of resamples: the pace has to stay in the blend. Everything between
# 0 and 0.75 is a plateau — 0.25 leans slightly better on the typical step, 0.5 on the mean and
# the worst case, and neither lean is separable from noise. The midpoint stands for that
# reason: it sits inside the flat top of the surface, and picking a different point on the
# curve would be picking the one this particular sample happened to favour.
# Tunable with `--blend-weight W` / `FBTODO_BLEND_WEIGHT`.
BLEND_WEIGHT = min(1.0, max(0.0, float(os.environ.get("FBTODO_BLEND_WEIGHT") or 0.5)))


# The two knobs above are what the environment said at import; this is what the run uses.
# They are asked for rather than read as constants because `--label-floor`/`--blend-weight` are
# applied once `main` starts, and the readers are spread over the program: a name imported into
# another module is a COPY, and a flag that moved only this module's would leave the copy — the
# one the estimator actually consults — at the old value. A dict is one object that every
# reader holds, so a write to it is visible from everywhere.
ESTIMATE_KNOBS = {"label_floor_ms": MIN_LABEL_MS, "blend_weight": BLEND_WEIGHT}


def label_floor_ms() -> int:
    """The shortest span that counts as evidence, asked for at the point of use."""
    return ESTIMATE_KNOBS["label_floor_ms"]


def blend_weight() -> float:
    """How much of a waiting step's number is its wording rather than the list's pace."""
    return ESTIMATE_KNOBS["blend_weight"]


def set_estimate_knobs(label_floor=None, blend_weight=None) -> None:
    """`--label-floor` / `--blend-weight`: one write here, every reader moves (see the table).

    None means "this flag was not given", so a caller cannot accidentally reset the other one.
    """
    if label_floor is not None:
        ESTIMATE_KNOBS["label_floor_ms"] = max(0, int(label_floor * 1000))
    if blend_weight is not None:
        ESTIMATE_KNOBS["blend_weight"] = min(1.0, max(0.0, blend_weight))


# The forecast ledger is only a forecast if it was written before the step had run. A watcher
# that starts (or restarts) with a step already in flight sees it running for the first time
# minutes in, so its vector would carry the answer's clock, not a prediction of it. Such a
# vector is still recorded — the record is the record — but flagged, and `forecast_error`
# leaves flagged rows out of the scoreboard. One poll of grace, generously.
LEDGER_FRESH_MS = 5000


# What it would take to re-choose the two estimate constants from the LOG instead of from a
# replay of the journals, so `fbtodo status` can say how far off that is. Both numbers come
# from the 2026-09-29 refit (the table is in the `step_pace_ms` docstring): a median needs
# about this many closed steps before one step stops being the whole distribution, and the
# clip needs about this many steps on which it actually MOVED the number — it is consulted
# only on the first few steps of a list, and on most of those it changes nothing, which is
# why the second count and not the first is the one that decides.
REFIT_MIN_SCORED = 30


REFIT_MIN_DECIDED = 78


EX_CODES = {
    "ok": 0,
    "usage": 2,
    "ex_usage": 64,
    "dataerr": 65,
    "ex_software": 70,
    "nofile": 66,
    "noinput": 66,
    "cantcreat": 73,
    "ioerr": 74,
    "tempfail": 75,
    "unavailable": 69,
    "config": 78,
}


# ------------------------------------------------------------- untrusted text
# Every string the pane prints that is not the tool's own comes out of a file an agent
# wrote: a step's name, the `Goal:` line, the last thing the agent said, the command in
# an action-feed row, the tail of a far-side log. A journal is not hostile input in the
# usual sense, but a terminal is a PROGRAM, and it obeys what it is fed: H1 measured an
# OSC 52 sequence in a step's name setting the owner's clipboard, `\x1b[2J` wiping the
# frame the pane sits under, a CSI move putting the cursor outside the box, and U+202E
# reversing a row so it reads as something it never said — in the rich pane, in the
# plain `snap` frame and in `json`, all verbatim (2026-09-29).
#
# So the filter lives here, and it is applied twice: where a source's prose enters the
# state (which is also what `json` and `status` read, so cleaning only at render would
# leave them exposed) and again as the frame is built, because a state file an older
# build wrote is still on disk and a cache is not a promise.
_ESC_SEQ_RE = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"     # OSC … BEL | ST (window title, clipboard)
    r"|\x1b[P^_X][^\x1b]*(?:\x1b\\)?"         # DCS / PM / APC / SOS … ST
    r"|\x1b\[[0-?]*[ -/]*[@-~]"               # CSI (cursor moves, erases, SGR)
    r"|\x1b[@-_]"                             # two-character escape
)


_TEXT_DROP_RE = re.compile(
    "["
    "\x00-\x08\x0b-\x1f\x7f-\x9f"   # C0 (tab and newline aside), DEL, and all of C1
    "\u061c"                       # Arabic letter mark: invisible, reorders a run
    "\u200b-\u200f"                 # zero-width space and joiners, LRM, RLM
    "\u2028-\u202e\u2066-\u2069"    # line/paragraph separators; bidi embeddings, overrides, isolates
    "\u2060-\u2064"                 # word joiner and the invisible operators
    "\ufeff"                        # BOM / zero-width no-break space
    "\U000e0000-\U000e007f"         # tag characters: invisible, and an invisible language tag
    "]"
)


def clean_text(value) -> str:
    """One string, stripped of everything a terminal would ACT on rather than show.

    Escape sequences go whole — an OSC 52 leaves no `52;c;…` behind to read — and then
    every remaining control, delimiter and bidi/zero-width character. Newlines stay: a
    step's name may legitimately be three lines, and the row splitter needs them. A CR
    becomes one, because a CR moves the cursor rather than starting a line; a tab becomes a
    single space, because one tab is eight columns of a pane it was never measured for.
    """
    text = _ESC_SEQ_RE.sub("", str("" if value is None else value))
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    return _TEXT_DROP_RE.sub("", text)


def _clean_key(key):
    """A dict key is a string the pane may print (a tool name in `tool_calls`) — clean it."""
    return clean_text(key) if isinstance(key, str) else key


def _clean_value(value):
    """Every string under `value`, cleaned in place of the original — a recursive walk.

    Dicts and lists are rebuilt (never mutated), so a caller's state is untouched. There is
    no exemption list and no depth limit: every string is cleaned, including a dict key,
    because a key is a string the pane may print (a tool name in `tool_calls`) and the walk
    cannot know which keys those will be. The tool's own enum values (`backend`, `source`,
    `status`, `goal_source`, `schema`) are cleaned like anything else; they are ASCII the tool
    writes itself, so cleaning them is a no-op — and if one ever did carry an escape, printing
    it raw is exactly the bug this exists to stop.
    """
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, dict):
        return {_clean_key(key): _clean_value(sub) for key, sub in value.items()}
    if isinstance(value, list):
        return [_clean_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_clean_value(item) for item in value)
    return value


def clean_observation(state: dict) -> dict:
    """Every string a source contributes, filtered — see `clean_text`.

    Called where a source's state is assembled and again by `render()`; both, so the state
    file itself is clean for its other readers and a frame cannot be painted from a state
    that was written before this filter existed. A recursive walk rather than a list of
    known keys, and with no key exempted: anything a source put in the state is cleaned,
    values and dict keys alike, at any depth.
    """
    if not isinstance(state, dict):
        return state
    return _clean_value(state)


# --------------------------------------------------------------------- helpers
# In plain words: save, then swap. The new copy is written to a scratch name in the same
# folder and only then renamed over the real one, so a reader can never catch a
# half-written file — they see the old copy or the new one, never a mixture. The fsync
# calls mean "get this onto the disk", not just into the computer's memory.
def atomic_write_json(path: str, payload: dict, mode: int = 0o600, fsync: bool = True) -> None:
    """Temp file in the target's own directory, fsync, rename, fsync the dir.

    `fsync=False` keeps the atomicity — still a rename over the target, so a reader never
    sees half a file — and drops the durability. That is the right trade for a rewrite whose
    only change is the heartbeat: if the machine goes down in the same second, the cost is a
    state file whose clock is a little old, which a reader answers by probing. What it saves
    is the pair of flushes a second, every second, on a file nobody asked to keep.
    """
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".fbtodo.", suffix=".tmp", dir=d)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
            fh.flush()
            if fsync:
                os.fsync(fh.fileno())
        os.replace(tmp, path)
        if fsync:
            dfd = os.open(d, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path: str, text: str, mode: int = 0o600, fsync: bool = True) -> None:
    """`atomic_write_json` for one word — a switch another program reads.

    The notify kit's `phone-state` and `state` are single words read by shell scripts, and
    a reader that catches one mid-write reads an empty string, which both scripts read as
    ON. So the swap is by rename here for the same reason it is for the state JSON, and the
    directory is created `0700` with the file `0600`: these two files decide whether the
    owner's phone rings.
    """
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".fbtodo.", suffix=".tmp", dir=d)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            if fsync:
                os.fsync(fh.fileno())
        os.replace(tmp, path)
        if fsync:
            dfd = os.open(d, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def pid_alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def pid_running(pid) -> bool:
    """Whether `pid` is still a process that can run — `pid_alive` minus the zombies.

    In plain words: the kernel keeps answering for a process that has DIED but whose parent
    has not reaped it — `kill(pid, 0)` succeeds and `ps` says `Z` — and that answer is right
    for "the pid is taken" and wrong for "something is still working here". A wait that
    polls a killed process needs the second question, because the process this program ends
    is usually somebody ELSE's child: its parent may be busy for a while, and a repair that
    reported the corpse as a survivor would claim a failure it does not have. So the state
    is asked in the same two ways the rest of this file asks about processes — the `/proc`
    stat where there is one (Linux), `ps -o state=` otherwise (macOS) — and an unreadable
    answer falls back to `pid_alive`, the pre-existing behaviour rather than a new guess.
    """
    if not pid_alive(pid):
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return True
    if os.path.isdir(PROC_ROOT):
        try:
            with open(os.path.join(PROC_ROOT, str(pid), "stat"), encoding="utf-8") as fh:
                blob = fh.read()
        except OSError:
            return True
        after = blob.rpartition(")")[2].strip()
        return not after.startswith("Z") if after else True
    try:
        proc = subprocess.run(["ps", "-o", "state=", "-p", str(pid)],
                              capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return True
    state = proc.stdout.strip()
    return not state.startswith("Z") if state else True


def proc_cwds(pids: list[int]) -> dict[int, str]:
    """pid -> cwd from /proc, for the pids that answer there ({} when there is no /proc).

    Linux keeps the working directory of every process as a symlink, so this is a readlink
    and no subprocess at all — the cheapest question in the poll on the platform most
    likely to be running a watcher under load. A pid owned by another user answers with
    EACCES and is simply left out for lsof to try (which will also fail, quietly).
    """
    if not os.path.isdir(PROC_ROOT):
        return {}
    found: dict[int, str] = {}
    for pid in pids:
        try:
            found[pid] = os.readlink(os.path.join(PROC_ROOT, str(pid), "cwd"))
        except OSError:
            continue
    return found


def lsof_cwds(pids: list[int]) -> dict[int, str]:
    """pid -> cwd for many pids in ONE lsof call — the macOS fallback.

    Per-pid lsof calls dominated the poll (and a transient failure silently dropped a
    candidate); the comma-separated `-p` form returns them all. A machine with no lsof on
    it is not an error here: `except Exception` covers a missing binary, and the caller
    keeps whatever `/proc` could answer.
    """
    if not pids:
        return {}
    try:
        out = subprocess.run(
            ["lsof", "-a", "-d", "cwd", "-Fn", "-p", ",".join(str(p) for p in pids)],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except Exception:
        return {}
    cwds: dict[int, str] = {}
    current: int | None = None
    for line in out.splitlines():
        if line.startswith("p"):
            current = int(line[1:]) if line[1:].isdigit() else None
        elif line.startswith("n") and current is not None:
            cwds[current] = line[1:]
            current = None
    return cwds


def cwds_for(pids: list[int]) -> dict[int, str]:
    """pid -> cwd, by /proc where there is one and one batched lsof for the rest.

    Both, rather than either: on Linux `/proc/<pid>/cwd` answers without a subprocess, so
    it is asked first and lsof only covers what it could not answer (another user's
    process, a pid that went away) — and on a machine with no lsof at all, a normal Linux
    server, the watcher can still follow its own session instead of giving up on a missing
    binary.
    """
    want = [int(p) for p in pids]
    if not want:
        return {}
    cwds = proc_cwds(want)
    missing = [p for p in want if p not in cwds]
    if missing:
        cwds.update(lsof_cwds(missing))
    return cwds


def pid_cwd(pid) -> str | None:
    return cwds_for([int(pid)]).get(int(pid))


def parse_etime(text: str) -> int | None:
    """`ps -o etime` gives [[DD-]HH:]MM:SS. macOS has no `etimes` keyword."""
    days = 0
    if "-" in text:
        head, text = text.split("-", 1)
        if not head.isdigit():
            return None
        days = int(head)
    parts = text.split(":")
    if not 2 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        return None
    nums = [int(p) for p in parts]
    hours, minutes, secs = (0, *nums) if len(nums) == 2 else tuple(nums)
    return days * 86400 + hours * 3600 + minutes * 60 + secs


def ages_for(pids: list[int]) -> dict[int, int]:
    """pid -> seconds of runtime. Pid order is not age (pids wrap), so ask ps."""
    if not pids:
        return {}
    try:
        out = subprocess.run(
            ["ps", "-o", "pid=,etime=", "-p", ",".join(str(p) for p in pids)],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except Exception:
        return {}
    ages: dict[int, int] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit():
            secs = parse_etime(parts[1])
            if secs is not None:
                ages[int(parts[0])] = secs
    return ages


def installed_freebuff() -> str | None:
    """The executable a bare `freebuff` would run, or None when PATH has none."""
    if not _FREEBUFF_WHICH:
        found = _shutil.which(FB_NAME)
        _FREEBUFF_WHICH.append(os.path.realpath(found) if found else None)
    return _FREEBUFF_WHICH[0]


def is_freebuff_cmd(cmd: str) -> bool:
    """True only for the CLI itself.

    Deliberately not a substring test on the whole command line: any process whose
    *arguments* mention the path (a python -c probe, a grep, a test) would match, and the
    pane would then follow the wrong pid. A TOKEN has to be the CLI, in one of the shapes a
    launcher actually produces:

      * argv[0] is a bare `freebuff` — how a PATH lookup is spelled in argv, which the
        original rule (an absolute path) could not see at all. Accepted only when such a
        program exists, never because the word appears;
      * a path-like token ending in `bin/freebuff` — the original rule, still a subset of
        this one, plus `./bin/freebuff` and `~/bin/freebuff`;
      * a path-like token that mentions `freebuff` and RESOLVES to `.../bin/freebuff`, so a
        symlinked or relocated install is still the CLI (an absolute path only, and only
        where the name gives a reason to resolve it).
    """
    toks = cmd.split()
    if toks and toks[0] == FB_NAME:
        return bool(installed_freebuff())
    for tok in toks:
        if not tok.startswith(("~", "/", ".")) or FB_NAME not in tok:
            continue  # `grep freebuff`, `python3 -c '…bin/freebuff…'`, any other argument
        if tok.endswith(FB_PROC):
            return True
        real = os.path.realpath(tok)
        if real.endswith(FB_PROC) or os.path.basename(real) == FB_NAME:
            return True
    return False


def freebuff_pids() -> list[int]:
    table = process_table()
    return sorted(p for p, (_ppid, cmd) in table.items() if is_freebuff_cmd(cmd))


def process_table() -> dict[int, tuple[int, str]]:
    """pid -> (ppid, command line) from one ps call."""
    try:
        out = subprocess.run(
            ["ps", "-Ao", "pid=,ppid=,command="], capture_output=True, text=True, timeout=5
        ).stdout
    except Exception:
        return {}
    table: dict[int, tuple[int, str]] = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        table[int(parts[0])] = (int(parts[1]), parts[2] if len(parts) > 2 else "")
    return table


def process_ttys() -> dict[int, str]:
    """pid -> its controlling terminal's short name, from one `ps` call.

    In plain words: a pane outside tmux has no pane id and no window to be named by — the two
    the desktop app attaches to a pty have only their terminal — so `fbtodo panes` reads the
    tty here to have something to print beside such a pane. `?` is ps's "no terminal", which
    is no answer rather than a name, so it is left out.
    """
    try:
        out = subprocess.run(
            ["ps", "-Ao", "pid=,tty="], capture_output=True, text=True, timeout=5
        ).stdout
    except Exception:
        return {}
    ttys: dict[int, str] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2 or not parts[0].isdigit() or parts[1] in ("?", "??"):
            continue
        ttys[int(parts[0])] = parts[1]
    return ttys


def instance_of_parent(parent_pid: int) -> int | None:
    """The Freebuff process launched by this shell."""
    return descendant_instance(process_table(), parent_pid)


# In plain words: every running program has a number, and knows the number of the program
# that started it. Following those links downwards lists everything a program has spawned,
# and going widest-first means the shallowest match comes out first — which is what "the
# freebuff process this shell started" means.
def descendant_pids(table: dict, parent_pid: int) -> list[tuple[int, int]]:
    """(pid, depth) for everything under this pid, breadth-first, from a fetched table.

    Both lookups that need a process tree share this: the freebuff *instance* under a
    shell (the shallowest match wins) and the one inside a pane the keeper runs (the pane
    that hosts it). One walk, so neither pays for a second pass over the table.
    """
    kids: dict[int, list[int]] = {}
    for pid, (ppid, _cmd) in table.items():
        kids.setdefault(ppid, []).append(pid)
    out: list[tuple[int, int]] = []
    frontier, depth, seen = list(kids.get(parent_pid, [])), 1, set()
    while frontier:
        nxt = []
        for pid in frontier:
            if pid in seen:
                continue
            seen.add(pid)
            out.append((pid, depth))
            nxt.extend(kids.get(pid, []))
        frontier, depth = nxt, depth + 1
    return out


def descendant_instance(table: dict, parent_pid: int) -> int | None:
    """The Freebuff process under this pid, from an ALREADY-FETCHED process table.

    A shell may host several descendants; the instance is the *shallowest* one
    whose command line looks like the CLI, because deeper ones are per-turn
    children that come and go while the session lives. The table is passed in
    because the pane bookkeeping asks this of several pids in one pass, and a
    `ps` per pid would cost more than the whole poll.
    """
    if parent_pid not in table:
        # the parent may already be gone; fall back to any freebuff process
        cands = [p for p, (_, cmd) in table.items() if is_freebuff_cmd(cmd)]
        return min(cands) if cands else None
    best: tuple[int, int] | None = None  # (depth, pid)
    for pid, depth in descendant_pids(table, parent_pid):
        if is_freebuff_cmd(table.get(pid, (0, ""))[1]):
            cand = (depth, pid)
            best = cand if best is None or cand < best else best
    return best[1] if best else None


# In plain words: "which freebuff is mine?" Several can run at once, so this narrows it the
# way a person would — the one started from this folder, and where there is more than one,
# the one started most recently. `--instance-of` skips the guessing: the shell that opened
# the pane hands over its own number and this walks down from there.
def find_instance(
    cwd: str, watch_pid: int | None = None, instance_of: int | None = None
) -> tuple[int | None, str | None]:
    """(pid, cwd) of the Freebuff process serving this directory."""
    if instance_of:
        pid = instance_of_parent(instance_of)
        if pid:
            return pid, pid_cwd(pid)
        # the child may not be visible yet (the pane opens as freebuff starts) or
        # the launch tree may be unusual — degrade to the cwd match below rather
        # than sitting on a pane that never attaches
    if watch_pid:
        return (watch_pid, pid_cwd(watch_pid)) if pid_alive(watch_pid) else (None, None)
    want = os.path.realpath(cwd)
    pids = freebuff_pids()
    if not pids:
        return None, None
    cwds = cwds_for(pids)
    matching = [p for p in pids if cwds.get(p) and os.path.realpath(cwds[p]) == want]
    pool = matching or pids
    # youngest wins: a directory can host several sessions, and "the one started
    # here" is what the user means. Age comes from ps, because pid order is not
    # chronological (a 9h session can hold a lower pid than a 2-day one).
    ages = ages_for(pool)
    pick = min(pool, key=lambda p: ages.get(p, 1 << 30))
    return pick, cwds.get(pick)


# ================================================================ CLI backend
def cli_chat_dir(cwd: str, project: str | None, chat: str | None, root: str) -> str | None:
    if chat:
        d = os.path.expanduser(chat)
        return d if os.path.isdir(d) else None
    if project:
        name = os.path.basename(os.path.realpath(project)) if os.path.sep in project else project
    else:
        name = os.path.basename(os.path.realpath(cwd))
    base = os.path.join(os.path.expanduser(root), name, "chats")
    dirs = [d for d in glob.glob(os.path.join(base, "*")) if os.path.isdir(d)]
    return max(dirs, key=os.path.getmtime) if dirs else None


# ===================================================================== display
def use_color(stream=sys.stdout) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM", "") in ("", "dumb"):
        return False
    if os.environ.get("FORCE_COLOR") or os.environ.get("CLICOLOR_FORCE"):
        return True
    if os.environ.get("CLICOLOR") == "0":
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def paint(enable: bool):
    return lambda code, text: f"\x1b[{code}m{text}\x1b[0m" if enable else text


def fmt_age(ms, now_ms: int | None = None) -> str:
    """How long ago the list was last written — '4m ago', so a still pane reads
    as 'nothing has changed', not 'this is hung'.

    `now_ms` is the renderer's own clock when there is one. The pane derives every other
    number from it, and a fixture's `now` is a constant rather than the wall clock, so
    reading `time.time()` here would make the one age in the frame disagree with all the
    others.
    """
    if not ms:
        return "?"
    now = (now_ms / 1000.0) if now_ms else time.time()
    delta = max(0.0, now - ms / 1000.0)
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def _plain(text: str) -> str:
    """The words of a styled line, with the colour taken out (see `prose_text`)."""
    return _ANSI_RE.sub("", str(text))


# In plain words: a terminal is not a grid of equal letters. An emoji or a Japanese
# character takes two columns on screen where an ordinary letter takes one, so counting
# characters miscounts them and the box's right-hand wall ends up ragged. This counts
# screen columns, which is what keeps the frame square.
def _cell_width(text: str) -> int:
    """Visible columns, so box borders and emoji line up in a terminal."""
    width = 0
    for ch in _plain(text):
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


# In plain words: shorten a coloured line to a width without cutting a colour code in half.
# Those invisible instructions have to survive, or everything after the cut inherits the
# wrong ink; the ellipsis is the polite way of saying the line was trimmed.
def _clip_cells(text: str, width: int) -> str:
    """Clip a styled string to a visible-column budget, keeping ANSI intact."""
    visible = _plain(text)
    if width <= 0:
        return ""
    if _cell_width(visible) <= width:
        return text
    out, used = [], 0
    for ch in visible:
        w = 0 if unicodedata.combining(ch) else (
            2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        )
        if used + w > max(0, width - 1):
            break
        out.append(ch)
        used += w
    return "".join(out) + "…"


def _pad_cells(text: str, width: int) -> str:
    return text + " " * max(0, width - _cell_width(text))


def _wrap(text: str, width: int, indent: int) -> list[str]:
    """Wrap prose to the pane without making normal words look broken.

    `indent` is the room the caller's prefix takes.  Most text wraps at word
    boundaries, but an unusually long token (a URL, for example) is split only as
    a last resort so the pane can never paint past its right edge.
    """
    available = max(1, width - max(0, indent))
    wrapper = textwrap.TextWrapper(
        width=available,
        break_long_words=False,
        break_on_hyphens=False,
        replace_whitespace=True,
        drop_whitespace=True,
    )
    lines = wrapper.wrap(str(text)) or [""]
    wrapped: list[str] = []
    for line in lines:
        while len(line) > available:
            wrapped.append(line[:available])
            line = line[available:]
        if line:
            wrapped.append(line)
    return wrapped or [""]


def _clip(text: str, width: int) -> str:
    """Fit one unindented line, using an ellipsis only when necessary."""
    text = str(text)
    if len(text) <= width:
        return text
    if width <= 1:
        return "…"[:width]
    return text[: width - 1] + "…"


def _fit_blocks(blocks: list, avail: int | None, prefer: int) -> tuple[int, int]:
    """The slice of task blocks that fits in `avail` lines, keeping `prefer` on screen.

    Two lines are held back for the elision markers, and the preferred block (the step
    being worked on now) is never among the ones dropped: a window that hides the
    current step to show finished ones would defeat the point.
    """
    total = len(blocks)
    if not total:
        return 0, 0
    if avail is None or sum(len(b) for b in blocks) <= avail:
        return 0, total
    budget = max(1, avail - 2)
    prefer = max(0, min(prefer, total - 1))
    end = prefer + 1
    used = len(blocks[prefer])
    while end < total and used + len(blocks[end]) <= budget:
        used += len(blocks[end])
        end += 1
    start = prefer
    while start > 0 and used + len(blocks[start - 1]) <= budget:
        used += len(blocks[start - 1])
        start -= 1
    return start, end


# In plain words: has the watcher written anything recently? Every write carries the time
# it was made, so a file that stops being stamped belongs to a watcher that has stopped —
# which is how "the list has not changed" is told apart from "nobody is updating it".
def _drop_clock(value):
    """The same structure with every moment taken out of it, at any depth."""
    if isinstance(value, dict):
        return {
            k: _drop_clock(v) for k, v in value.items()
            if k not in STATE_CLOCK_KEYS and not k.endswith("_ms")
        }
    if isinstance(value, list):
        return [_drop_clock(v) for v in value]
    return value


def state_evidence(state: dict | None) -> dict:
    """The state with the clock taken out: what a poll actually learned.

    Compared rather than the state itself, because the un-taken clock moves on every poll
    and would make every poll look like news. Everything else is compared as a whole, so a
    changed todo list, a new goal, a started turn or a finished session all count — and so
    does anything added to the state later, without this needing to know about it.
    """
    return _drop_clock(state) if state else {}


def state_is_fresh(state: dict | None, max_age: float = HEARTBEAT_GRACE) -> bool:
    if not state or state.get("status") != "watching":
        return False
    hb = state.get("heartbeat_ms") or 0
    return (time.time() * 1000 - hb) / 1000.0 <= max_age


__all__ = [
    "VERSION", "HOME", "LEGACY_SCRATCH", "STATE_DIR", "LEGACY_NAMES", "LEGACY_CLAIMS",
    "_claim_live", "_legacy_claim_live", "_state_root", "init_state_root", "SCRATCH",
    "STATE_NOTE", "STATE_PATH",
    "STATE_FILE_NAME",
    "TASKS_PATH", "events_path", "self_argv", "STARTED_AT", "SOURCE_SETTLE_S", "BUILD_CHECK_S",
    "RELOAD_PROBE_ENV", "PROBE_NOTE_CHARS", "reload_probe_error", "undefined_global_names", "unreachable_code",
    "dead_code",
    "source_newer_than", "source_syntax_error", "pinned_path", "pinned_env", "PINNED_ENV_KEYS",
    "python_of",
    "LOCK_PATH", "LOG_PATH",
    "pid_alive", "pid_running",
    "PANE_KEEPER_PATH", "PANE_LOG_PATH", "PANE_NOTE_PATH", "PANE_SUBJECT_DIR",
    "PINS_PATH", "LAST_PATH", "MUTE_PATH", "NOTIFY_DIR",
    "TODO_NOTIFY", "DROP_NOTIFY", "ASK_NOTIFY", "PAUSE_NOTIFY", "PANE_NOTIFY",
    "LOCKS_NOTIFY",
    "TMUX_BIN", "TMUX_SUBCOMMANDS", "DEFAULT_DB_GLOB", "DEFAULT_WORKSPACE_STATE",
    "DEFAULT_CLI_ROOT",
    "PATCH_LOG", "PATCH_META", "ALERT_LOG",
    "PATCH_TAIL_BYTES", "PATCH_REASON_MAX", "PATCH_TAIL_LINES",
    "FB_PROC", "FB_NAME", "PROC_ROOT", "_FREEBUFF_WHICH",
    "CHUNK", "MAX_SCAN", "PROMPT_MIN_CHARS", "NUDGE_WORDS", "NUDGE_MAX_CHARS",
    "GOAL_CHASE_CHUNKS", "TASK_MAX_LINES", "GOAL_LINE_RE", "GOAL_MAX_CHARS",
    "SUMMARY_MAX_CHARS", "PROSE_NEEDLE_RE", "MODEL_RE", "HEARTBEAT_GRACE", "HB_REFRESH",
    "STATE_CLOCK_KEYS", "MAX_TASK_RECORDS", "MAX_TASK_AGE_DAYS", "LOG_CAP_BYTES",
    "PRUNE_INTERVAL_S", "TEMP_MAX_AGE_S", "HISTORY_MAX_AGE_DAYS", "HISTORY_MAX_ENTRIES",
    "SHAPE_MIN_SAMPLES", "SHAPE_MIN_BUCKET", "SPREAD_MIN_SAMPLES", "SPREAD_MIN_RATIO",
    "LABEL_FLOOR_S", "MIN_LABEL_MS", "BLEND_WEIGHT", "ESTIMATE_KNOBS", "label_floor_ms",
    "blend_weight", "set_estimate_knobs", "LEDGER_FRESH_MS", "REFIT_MIN_SCORED",
    "REFIT_MIN_DECIDED", "EX_CODES", "_ESC_SEQ_RE", "_TEXT_DROP_RE",
    "clean_text", "clean_observation", "atomic_write_json", "atomic_write_text",
    "read_json", "pid_alive", "proc_cwds", "lsof_cwds", "cwds_for", "pid_cwd", "parse_etime",
    "ages_for", "installed_freebuff", "is_freebuff_cmd", "freebuff_pids", "process_table",
    "process_ttys", "instance_of_parent", "descendant_pids", "descendant_instance", "find_instance",
    "cli_chat_dir", "use_color", "paint", "fmt_age", "_ANSI_RE", "_plain", "_cell_width",
    "_clip_cells", "_pad_cells", "_wrap", "_clip", "_fit_blocks", "_drop_clock",
    "state_evidence", "state_is_fresh",
]
