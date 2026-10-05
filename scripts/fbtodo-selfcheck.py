#!/usr/bin/env python3
"""Self-check for `fbtodo`, the checkout this file lives under.

Runs everything against a throwaway FBTODO_HOME (`.freebuff/fbtodo-test/`) and a
fake "freebuff instance" (a sleep process) whose death must stop the watcher:

    python3 scripts/fbtodo-selfcheck.py

The suite drives real tmux servers it starts itself and takes ~90-160 s. It cleans
up only that one directory — created by this script, verified by path — never a
parent.
"""
from __future__ import annotations

import ast
import builtins
import importlib
import importlib.util
import json
import math
import os
import re
import shlex
import signal
import sqlite3
import subprocess
import shutil
import sys
import threading
import time
import types
import unicodedata

HOME = os.path.expanduser("~")
# This suite lives in `scripts/`; the checkout it drives is the directory above it, so the
# launcher, `src/`, `tests/`, `docs/` and `examples/` are all found from ROOT rather than
# from the suite's own directory.
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FB = os.path.join(ROOT, "fbtodo")
# PER RUN, not one shared directory. The suite wipes its state root at the start and removes
# it at the end, so two runs — a second shell's, or the one this session starts while the
# first is still going — used to delete each other's claims, state files and logs mid-check,
# and the loser failed on missing files it had just created (measured: `FileNotFoundError`
# on `fbtodo-state.json`, and a watcher that "never took its lock" because the other run
# had just removed the directory under it). A pid in the name makes the runs independent;
# a crashed run leaves its own directory behind rather than anyone's, and the sweep below
# takes those only once they are demonstrably stale.
TEST_HOME = os.path.join(HOME, ".freebuff", f"fbtodo-test-{os.getpid()}")


def sock(name: str) -> str:
    """A tmux socket name this run alone can be holding.

    Every pane check drives its own private tmux server, and the names were fixed strings, so
    two runs shared them: each killed the other's server on the way in AND on the way out, so
    a concurrent suite destroyed the panes its sibling was about to inspect (measured: `keep`
    printing no repair line at all, because the pane it named no longer existed). The pid in
    the name makes the servers private the same way the state root now is — and the cleanup
    can then kill only what this run started.
    """
    return f"{name}-{os.getpid()}"


def sweep_stale_test_homes(older_than_s: float = 24 * 3600.0) -> int:
    """How many abandoned `fbtodo-test-<pid>` directories this sweep removed.

    A run killed mid-phase cannot clean up after itself, and a directory nobody owns is the
    one thing a shared root could not have. Only directories whose own mtime is older than the
    bound go: a run that is merely slow has just written to it, so it is never mistaken for
    a corpse.
    """
    base = os.path.join(HOME, ".freebuff")
    removed = 0
    try:
        names = os.listdir(base)
    except OSError:
        return 0
    for name in names:
        if not (name.startswith("fbtodo-test-") and name != os.path.basename(TEST_HOME)):
            continue
        path = os.path.join(base, name)
        try:
            if time.time() - os.stat(path).st_mtime < older_than_s:
                continue
            shutil.rmtree(path)
            removed += 1
        except OSError:
            continue
    return removed
REAL_HOME = os.path.join(HOME, ".freebuff")
PANES = os.path.join(ROOT, "fbtodo")
# The program is the package under `src/`; `FB` above is the launcher beside it, which is
# what every subprocess phase runs and what `self_argv` names back to us.
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)
CWD = HOME
STRIP = lambda s: re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", s)  # noqa: E731

# The pane's progress row, found by what is ON it rather than by a label: the bar labels
# itself (`[████▉░░░]  25% (2/8)`), because `PROGRESS` cost eight columns of a 68-column row
# and started one column left of `PATCH`, `GOAL`, `ALERT` and `REFIT`.
BAR_CELLS = re.compile(r"\[[█▏▎▍▌▋▊▉░]+\]")


def bar_row_of(framed: str) -> str:
    """The one row of a rendered frame that carries a bracketed progress bar.

    Matched on the STRIPPED line and returned as it was drawn: every cell of the bar is a
    glyph wrapped in its own escape, so between `[` and `]` there is more than the bar.
    """
    for line in framed.splitlines():
        if BAR_CELLS.search(STRIP(line)):
            return line
    raise AssertionError(f"no progress row in the frame:\n{framed}")

env = dict(os.environ, FBTODO_HOME=TEST_HOME)
# ...and the state root in THIS process too, which is not only about `env`. The module
# instances this script loads in-process (`module`, `mod`, `mp`, `panes_mod`) fix their
# state and task-log paths at import, and so does a `fbtodo` a phase spawns without an
# explicit `env` — a module loaded with the real root writes the real log. Found
# 2026-09-29: the live task stream held a fixture session's events, appended by a phase
# while the throwaway home sat there empty. The root is decided at import, so this is the
# one place that covers every one of them.
os.environ["FBTODO_HOME"] = TEST_HOME
# The phone notifiers, muted for the whole run — in THIS process's environment too, not
# just in `env`. Some phases start a REAL watcher (the zshrc autostart hook inherits the
# shell's environment), and a real watcher reading the fixture's dead-looking session
# pushes "freebuff dropped" to the owner's phone: measured 2026-09-22, three spurious
# pairs while this suite ran. `/usr/bin/true` answers every notifier contract well enough
# for phases that only assert a watcher came up; the block below points its own
# commands at logged stubs instead.
MUTE = shutil.which("true") or "/usr/bin/true"
for _var in ("FBTODO_NOTIFY", "FBTODO_DROP", "FBTODO_ASK", "FBTODO_PAUSE", "FBTODO_PANE_BELL"):
    os.environ[_var] = MUTE
    env[_var] = MUTE
# The patch row's own sources, pointed at fixtures for the whole run — in this process's
# environment too, because the module reads these paths when it is imported below. The
# real ones are this Mac's (~/.config/freebuff-patch-watch/watch.log and
# ~/.config/freebuff-notify/phone.log), and a suite whose rendered output changes with the
# owner's last patch pass would be reporting history rather than the code. The version is
# one no build can have, so a check can prove the pane read the fixture and not the host.
PATCH_HOME = os.path.join(TEST_HOME, "patchlogs")
PATCH_FIXTURE = os.path.join(PATCH_HOME, "watch.log")
ALERT_FIXTURE = os.path.join(PATCH_HOME, "phone.log")
META_FIXTURE = os.path.join(PATCH_HOME, "freebuff-metadata.json")
for _key, _val in (
    ("FBTODO_PATCH_LOG", PATCH_FIXTURE),
    ("FBTODO_ALERT_LOG", ALERT_FIXTURE),
    ("FBTODO_PATCH_META", META_FIXTURE),
):
    os.environ[_key] = _val
    env[_key] = _val
PATCH_FIXTURE_STAMP = ""  # filled in with the fixtures themselves, below


def keeper_aim() -> dict:
    """Live `pane-watch` processes → the tmux server each one is aimed at, from its own env.

    The environment is the only place that decision is written down (`tmux_identity()`
    takes FBTODO_TMUX, then asks the server for its socket, then falls back to `TMUX`), and
    it is what makes a keeper
    started by a TEST hook inside the owner's own pane dangerous: it goes off keeping the
    OWNER's panes with the test state root — invisible (its log is a file in a home the
    suite wipes) and immortal (the owner's server always has panes and a freebuff, so it
    never reaches the pass that would let go), so it went on moving the owner's list pane
    back to the default side for as long as it lived. Keyed by pid, so a caller can tell
    what THIS run added from what was already on the machine.

    The read is the program's own two-source one (`_proc_environ`: `/proc/<pid>/environ`,
    then `ps -Eww`, then the kernel's copy), not a bare `ps -Eww`. That distinction is the
    whole point here: a `ps -Eww` answer that is only a command line is not an environment,
    and a copy the kernel cut short may have dropped the very `FBTODO_HOME` this check
    exists to find — either way a keeper that IS a leak would read as one aimed somewhere
    else, which is the one failure this guard must not have. Each pid therefore also carries
    `_read` (was an environment obtained at all) and `_clipped` (did it come back capped),
    the two facts a caller needs to treat an environment it could not account for as
    suspect rather than as a keeper running elsewhere.
    """
    aims = {}
    listing = subprocess.run(
        ["ps", "-eo", "pid=,args="], capture_output=True, text=True
    ).stdout or ""
    for line in listing.splitlines():
        pid, _, args = line.strip().partition(" ")
        if not pid.isdigit() or "pane-watch" not in args or "grep" in args:
            continue
        blob, clipped = module._proc_environ(int(pid))
        fields = {}
        for token in blob.replace("\0", " ").split():
            key, _, value = token.partition("=")
            if value and key in ("FBTODO_HOME", "FBTODO_TMUX", "TMUX"):
                fields[key] = value
        fields["_read"] = module._env_read(blob)
        fields["_clipped"] = clipped
        aims[pid] = fields
    return aims


def keeper_leaks(before: dict, home: str, server: str | None,
                 aims: dict | None = None) -> list:
    """Keepers this run ADDED that keep `server`'s panes while running with `home`.

    Nothing should ever be one: a keeper reads the state root it was given and it keeps
    whatever panes the tmux server in its environment has, so a test home pointed at a
    live server is a keeper that edits the owner's layout from a scratch file.

    When the read could PLACE the keeper — a named root, or a readable environment that
    names none and so runs on the default one — the comparison is exact, as before. When it
    could not (no environment read at all, or a copy the kernel clipped before the root), a
    keeper whose state root cannot be accounted for is reported rather than waved through:
    a guard that quietly skips the one case it cannot read is the trap this whole read
    exists to close. `aims` is the injection point, so the rule is pinned without depending
    on what is running.
    """
    out = []
    for pid, fields in (keeper_aim() if aims is None else aims).items():
        if pid in before:
            continue
        named = fields.get("FBTODO_HOME")
        if named is not None:
            if named != home:
                continue  # a readable root that is not this test's
        elif fields.get("_read") and not fields.get("_clipped"):
            continue  # read, and names no root: a keeper on the default root, not this test's
        if (fields.get("FBTODO_TMUX") or fields.get("TMUX") or None) == server:
            out.append((pid, fields.get("TMUX"), fields.get("FBTODO_TMUX")))
        elif named is None:
            # the environment could not be read (or came back clipped with no root), so
            # even the server cannot be compared: a keeper this run cannot account for
            out.append((pid, None, None))
    return out


ok = []
START = time.monotonic()  # the suite's clock; `say()` reports each check's cost against it


def run(*args, timeout=30, check=False, cwd=CWD):
    # `cwd` is the directory the command is asked ABOUT (`auto` resolves a source from it);
    # nearly every check wants the throwaway home, and the few that do not say so.
    p = subprocess.run(
        [sys.executable, FB, *args], capture_output=True, text=True, env=env,
        cwd=cwd, timeout=timeout,
    )
    if check and p.returncode != 0:
        raise AssertionError(f"{args} -> {p.returncode}\n{p.stdout}\n{p.stderr}")
    return p


def put_tasklog(mod, log):
    """Install a task log by hand: the memo AND an empty stream beside it.

    The JSON beside the stream is a memo of it, and the stream is the one that is believed,
    so writing the memo alone would leave the previous test's events to be folded back into
    the records on the next read. A hand-installed log has no past, on purpose.
    """
    stream = mod.events_path()
    if os.path.exists(stream):
        os.unlink(stream)
    mod.atomic_write_json(mod.TASKS_PATH, log)


def say(msg):
    """Report a passing check, and — with FBTODO_SELFCHECK_TIME=1 — what it cost.

    The suite takes 90-160 s and the reason is not obvious from the outside (real tmux
    servers, keeper passes, notifier fixtures), so the elapsed seconds since the last
    check go
    on the line: `[+12.4s 3m21s]`. That is how the expensive phases are found instead of
    guessed at.
    """
    ok.append(msg)
    if os.environ.get("FBTODO_SELFCHECK_TIME") == "1":
        now = time.monotonic()
        since = now - say.last
        print(f"{msg}  [+{since:.1f}s {now - START:.0f}s]")
    else:
        print(msg)
        now = time.monotonic()
    say.last = now


say.last = time.monotonic()


def load_fbtodo(home: str | None = None) -> object:
    """The program as a module: the package, freshly imported for `home`.

    Its paths (state root, task log, lock) are decided at import, and this suite asks for
    several roots in one process — which is why it used to load the single file by path
    under a new name each time. Dropping the package's modules and importing it again does
    the same thing for a package, and gives a module object whose globals are patchable
    exactly as the single file's were.
    """
    if home is not None:
        os.environ["FBTODO_HOME"] = home
    for name in [n for n in sys.modules if n == "fbtodo" or n.startswith("fbtodo.")]:
        del sys.modules[name]
    return importlib.import_module("fbtodo")


def load_module(path: str, name: str) -> object:
    """A file as a module, by path — how the kit's scripts are read where they live.

    The notification kit is not importable (its scripts sit in `scripts/notify/`, run by
    their own shebang and found through `~/.config/freebuff-notify/`), so a test that wants
    to ask one a question loads it by path. Same idea as `load_fbtodo`, one file at a time.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def set_knob(mod, name, value):
    """Set one of the program's knobs by name, in every module that holds it.

    The program is one package in several modules, and a name taken in with `import *` is a
    COPY: patching it on the package would leave the owner's own copy — the one its code reads
    — at the old value, and the test would pass while testing nothing. So the write goes to
    every loaded module of this package that holds the name, which is what "the module's
    global" meant when all of it was one file.
    """
    owners = [m for n, m in list(sys.modules.items())
              if (n == mod.__name__ or n.startswith(mod.__name__ + ".")) and name in vars(m)]
    assert owners, f"no module under {mod.__name__} owns {name!r}"
    for owner in owners:
        setattr(owner, name, value)


def spawn_quiet(*args):
    """Detached from this process's stdio, in its own group.

    Anything that inherits the caller's stdout keeps `… | tail` open after this
    script exits — a leaked `sleep` holding the pipe looked exactly like a hang.
    """
    return subprocess.Popen(
        list(args),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def tmux_geometry(tmux_cmd: list) -> dict:
    """pane id -> [left, top, width, height], on whichever server `tmux_cmd` names.

    The placement checks are about numbers, so they read the same fields fbtodo places
    by — and restate the rule themselves rather than asking the code under test.
    """
    p = subprocess.run(
        list(tmux_cmd) + ["list-panes", "-a", "-F",
                          "#{pane_id} #{pane_left} #{pane_top} #{pane_width} #{pane_height}"],
        capture_output=True, text=True,
    )
    rects = {}
    for line in (p.stdout or "").splitlines():
        parts = line.split()
        if len(parts) == 5:
            rects[parts[0]] = [int(v) for v in parts[1:]]
    return rects


def kill_tree(proc) -> None:
    """Kill the whole group: `sleep 600 & wait` leaves a child behind."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


# ---- subset runs: `--only <sel>` and `--list` --------------------------------------
# The suite is one long script, and deliberately so: the phases share the fixture home,
# the muted notifiers and a live watcher, so a block cannot be called up on its own — it
# depends on everything above it. What CAN be dropped is the expensive half: each phase
# below starts its own tmux server and waits out real keeper passes, and they are the
# reason the suite costs ~70 s (measured 2026-09-23, FBTODO_SELFCHECK_TIME=1). Each one
# carries a `#@phase <name>` line; `--only` re-runs this file with the phases you did not
# name cut out (as `pass`, line numbers preserved), keeping the cheap body — fixture
# setup, marker checks, unit tests — that the phases depend on. `--list` shows them.
#
#   python3 scripts/fbtodo-selfcheck.py --list
#   python3 scripts/fbtodo-selfcheck.py --only local-session
#   python3 scripts/fbtodo-selfcheck.py --only 1 --only "local session"
SUBSET_GUARD = "FBTODO_SELFCHECK_SUBSET"  # set for the re-run, so it does not recurse
PHASE_MARK = "#@phase "


def selector_phases(src: str) -> list:
    """The `#@phase`-marked blocks: index, name, line range, checks, first check text."""
    import ast

    tree = ast.parse(src)
    lines = src.splitlines()
    hero = max((n for n in tree.body if isinstance(getattr(n, "body", None), list)),
               key=lambda n: n.end_lineno)
    out = []
    for node in hero.body:
        mark = lines[node.lineno - 2] if node.lineno >= 2 else ""
        if not mark.lstrip().startswith(PHASE_MARK):
            continue
        says = [n for n in ast.walk(node) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name) and n.func.id == "say"]
        first = next((s.args[0].value for s in says
                      if s.args and isinstance(s.args[0], ast.Constant)), "")
        out.append({"i": len(out), "name": mark.split(PHASE_MARK, 1)[1].strip(),
                    "start": node.lineno, "end": node.end_lineno,
                    "checks": len(says), "first": first})
    return out


def selector_run() -> None:
    """`--list` / `--only`: re-run this file with the unselected phases cut out."""
    argv = sys.argv[1:]
    if os.environ.get(SUBSET_GUARD) == "1":
        return
    want_list, only, rest, i = False, [], [], 0
    while i < len(argv):
        if argv[i] == "--list":
            want_list, i = True, i + 1
        elif argv[i] == "--only":
            only.append(argv[i + 1] if i + 1 < len(argv) else "")
            i += 2
        elif argv[i].startswith("--only="):
            only.append(argv[i].split("=", 1)[1])
            i += 1
        else:
            rest.append(argv[i])
            i += 1
    if not (want_list or only):
        return
    path = os.path.abspath(__file__)
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    phases = selector_phases(src)
    if want_list:
        for p in phases:
            print(f"{p['i']:2d}  L{p['start']:<5d} {p['checks']:2d} checks  {p['name']}\n"
                  f"         {p['first'][:110]}")
        print("\n--only <index|substring of the name or first check>  (no --only: all of them)")
        raise SystemExit(0)
    keep = set()
    for sel in only:
        hits = [p["i"] for p in phases
                if sel == str(p["i"]) or sel.lower() in p["name"].lower()
                or sel.lower() in p["first"].lower()]
        if not hits:
            raise SystemExit(
                f"--only {sel!r} matched none of the {len(phases)} phases; --list shows them"
            )
        keep.update(hits)
    lines = src.splitlines(keepends=True)
    for p in phases:
        if p["i"] in keep:
            continue
        for ln in range(p["start"] - 1, p["end"]):
            line = lines[ln]
            if ln == p["start"] - 1:
                lines[ln] = line[: len(line) - len(line.lstrip())] + "pass\n"
            else:
                lines[ln] = "#" + line
    print(f"--only {', '.join(only)}: running {len(keep)} of {len(phases)} phases, "
          "the rest of the body as usual\n")
    os.environ[SUBSET_GUARD] = "1"
    sys.argv = [sys.argv[0], *rest]
    exec(compile("".join(lines), path, "exec"),
         {"__name__": "__main__", "__file__": path})
    raise SystemExit(0)  # the re-run WAS the suite: this process must not run it again


selector_run()

sweep_stale_test_homes()
if os.path.exists(TEST_HOME):
    shutil.rmtree(TEST_HOME)
os.makedirs(TEST_HOME, mode=0o700)
# ...but a wiped test home is not a fresh machine: a watcher started by an earlier
# run is not tied to the directory, and a run that dies before its own cleanup leaves it
# polling forever against a path this run has just recreated. Only processes whose argv names
# this home are touched; the owner's own watchers never mention this path.
for _line in subprocess.run(
    ["ps", "-Ao", "pid=,args="], capture_output=True, text=True
).stdout.splitlines():
    _pid, _, _args = _line.strip().partition(" ")
    if TEST_HOME in _args and "fbtodo" in _args:
        try:
            os.kill(int(_pid), signal.SIGTERM)
        except (OSError, ValueError):
            pass
# The patch row's fixtures: written HERE rather than with the paths above, because the
# wipe just above takes the whole test home with it — and read by the module when it is
# imported below, which is why the paths are set before this point.
os.makedirs(PATCH_HOME, mode=0o700, exist_ok=True)
PATCH_FIXTURE_STAMP = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 1800))
with open(PATCH_FIXTURE, "w") as fh:
    # a pass in progress above the finished one: the OUTCOME is the newest line that IS
    # one, not simply the newest line
    fh.write(f"{PATCH_FIXTURE_STAMP} binary changed (1:2:3) — converge, then re-anchor\n")
    fh.write(f"{PATCH_FIXTURE_STAMP} patched — the collapse and the session-end reason are"
             " in place (1:2:4)\n")
with open(ALERT_FIXTURE, "w") as fh:
    fh.write(f"{PATCH_FIXTURE_STAMP} phone: sent ntfy freebuff done · fixture\n")
with open(META_FIXTURE, "w") as fh:
    json.dump({"version": "9.9.9-fixture", "target": "darwin-arm64"}, fh)

try:
    state_path = os.path.join(TEST_HOME, "fbtodo-state.json")
    lock_path = os.path.join(TEST_HOME, "fbtodo-daemon.pid")

    # A CLI root of our own. This block proves the watcher reads a real CLI journal, and it
    # used to read the OPERATOR's live one — which made it pass or fail with whatever the
    # owner's session happened to be doing at that second. A session that has just dropped a
    # finished list is legitimately list-less, and that is not the watcher's fault.
    cli_root = os.path.join(TEST_HOME, "cliwatch")
    # The journal root is `<root>/<project>/chats`, and this run follows a session whose
    # `--cwd` is HOME, so the project is the home directory's own name — `billthuan1` on
    # the owner's machine, `runner` on a CI runner. Hard-coding the owner's name made this
    # phase pass locally and fail on every runner, so it is derived, not typed.
    chat_dir = os.path.join(cli_root, os.path.basename(HOME), "chats",
                            "2026-01-01T00-00-00.000Z")
    os.makedirs(chat_dir, exist_ok=True)
    with open(os.path.join(chat_dir, "log.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "level": "DEBUG",
            "timestamp": "2026-01-01T00:00:00.000Z",
            "data": {
                "iteration": 1,
                "prompt": "watch this session",
                "toolCalls": [{"toolName": "write_todos", "input": {"todos": [
                    {"task": "one", "completed": True},
                    {"task": "two", "completed": False},
                ]}}],
            },
        }) + "\n")

    # ---- a fake freebuff instance we are allowed to kill
    victim = spawn_quiet("sleep", "600")
    time.sleep(0.2)

    # ---- daemon starts, watches the victim, refreshes the state file
    # The desktop glob is NAMED as one that matches nothing, and this is the same reason the
    # json/bar/snap check below names its own: this daemon follows a session whose `--cwd` is
    # the home directory, and on the owner's machine the app's store for THAT directory is
    # live and being written — so `auto` answers from it and this fixture's own journal, which
    # is what the check is about, is never read (measured 2026-10-04: the watcher answered
    # `backend: desktop` with the operator's session on screen). A fixture that cannot be
    # asserted about is not a fixture.
    no_desk = os.path.join(TEST_HOME, "no-desktop", "*.db")
    daemon = subprocess.Popen(
        [sys.executable, FB, "daemon", "--foreground", "--quiet",
         "--instance-pid", str(victim.pid), "--cwd", CWD, "-i", "0.2",
         "--cli-root", cli_root, "--db", no_desk],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, start_new_session=True, env=env, cwd=CWD,
    )
    deadline = time.time() + 10
    while time.time() < deadline and not os.path.exists(lock_path):
        time.sleep(0.1)
    assert os.path.exists(lock_path), "watcher never took its lock"

    # the lock is taken before the first snapshot, so poll the file, don't assume
    deadline = time.time() + 20
    st = {}
    while time.time() < deadline:
        try:
            st = json.load(open(state_path))
        except (OSError, ValueError):
            time.sleep(0.1)
            continue
        if st.get("status") == "watching" and st.get("heartbeat_ms"):
            break
        time.sleep(0.1)
    assert st.get("status") == "watching", st
    assert st.get("instance_pid") == victim.pid, st
    assert st.get("backend") == "cli" and st.get("todos"), st
    assert st.get("heartbeat_ms") and st.get("list_version"), st
    say(f"watcher follows the instance and refreshes state: ok "
        f"({st['done']}/{st['total']} todos, list #{st['list_version']})")

    # ---- the heartbeat stays young, but no longer by rewriting the file every poll: on a
    #      quiet list the clock is the only thing that moves, so a rewrite is gated on
    #      evidence OR on the heartbeat about to fall out of the grace window. Measured on
    #      the operator's live session this is ~1 rewrite a second -> ~1 every 2s, and none
    #      of the clock-only ones flush to disk.
    hb1 = json.load(open(state_path))["heartbeat_ms"]
    stamps = set()
    window_end = time.time() + 5.0
    while time.time() < window_end:
        time.sleep(0.05)
        stamps.add(os.stat(state_path).st_mtime_ns)
    hb2 = json.load(open(state_path))["heartbeat_ms"]
    assert hb2 > hb1, (hb1, hb2)
    age = (time.time() * 1000 - hb2) / 1000.0
    assert age <= 5.0, (age, hb2)  # HEARTBEAT_GRACE, the window `state_is_fresh` uses
    # -i 0.2 over those 5s is ~25 polls; only the heartbeat's own pace (2s) may write.
    assert len(stamps) <= 6, (len(stamps), "a quiet list rewrote its state every poll")
    say(f"heartbeat stays fresh without a rewrite per poll: ok "
        f"({len(stamps)} writes in 5s, heartbeat {age:.1f}s old)")

    # ---- ...and evidence is still written the moment it appears: a poll that HAS something
    #      to say does not wait for the heartbeat's pace.
    with open(os.path.join(chat_dir, "log.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "level": "DEBUG",
            "timestamp": "2026-01-01T00:00:05.000Z",
            "data": {
                "iteration": 2,
                "prompt": "watch this session",
                "toolCalls": [{"toolName": "write_todos", "input": {"todos": [
                    {"task": "one", "completed": True},
                    {"task": "two", "completed": True},
                ]}}],
            },
        }) + "\n")
    deadline = time.time() + 4
    while time.time() < deadline:
        try:
            if json.load(open(state_path)).get("done") == 2:
                break
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    assert json.load(open(state_path)).get("done") == 2, "a changed list waited for the clock"
    say("a poll with something to say writes it: ok")

    # ---- status reports a live watcher
    out = run("status").stdout
    assert "not running" not in out, out
    # The build is named, and no stale-watcher warning rides beside it: this watcher was
    # started by this same file, so the two versions must agree.
    ver = run("-V").stdout.strip()
    assert ver and f"  tool version      : {ver}" in out, (ver, out)
    assert "stale" not in out.split("tool version")[1].splitlines()[0], out
    say("status shows the live watcher and names the build: ok")

    # ---- kill the instance: the watcher must shut itself down and drop its lock
    kill_tree(victim)
    deadline = time.time() + 10
    while time.time() < deadline and os.path.exists(lock_path):
        time.sleep(0.1)
    assert not os.path.exists(lock_path), "watcher kept its lock after the instance died"
    assert daemon.wait(timeout=10) == 0, "watcher did not exit cleanly"
    final = json.load(open(state_path))
    assert final.get("status") == "stopped", final
    assert final.get("stop_reason") == "instance-exited", final
    assert "todos" not in final, "a stopped watcher kept a list"
    say(f"watcher shuts down when the instance dies: ok ({final['stop_reason']})")

    # ---- stop is idempotent on a dead watcher
    assert run("stop").returncode == 0
    say("stop is a no-op when nothing runs: ok")

    # ---- the watcher's own claim NAME can move without the kernel lock moving. Nothing
    #      outside can see that — `lock_peek` reports the role as not running, the process
    #      half of `locks` names it an orphan — so the watcher notices it itself and takes
    #      the name back, the way the keeper does. Two accidents heal, and a name a LIVE
    #      process holds is the one it must stand down for.
    lk = load_fbtodo()  # this row's own reader; `module` is bound later in the suite
    victim_l = spawn_quiet("sleep", "600")
    daemon_l = subprocess.Popen(
        [sys.executable, FB, "daemon", "--foreground", "--quiet",
         "--instance-pid", str(victim_l.pid), "--cwd", CWD, "-i", "0.2"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, start_new_session=True, env=env, cwd=CWD,
    )

    def watcher_reclaimed(seconds: float = 10.0) -> bool:
        # The record, not `lock_holder`: a probe TAKES the free name for the moment it asks,
        # so polling with it could stand the watcher down mid-accident. The watcher writes
        # its record through the locked fd, so a record naming it means it holds the name.
        deadline = time.time() + seconds
        while time.time() < deadline:
            if (lk.read_json(lock_path, {}) or {}).get("pid") == daemon_l.pid:
                return True
            time.sleep(0.1)
        return False

    holder = None
    try:
        assert watcher_reclaimed(), "the watcher never claimed"
        assert lk.lock_holder(lock_path) == daemon_l.pid, "the watcher never claimed"
        # (1) the file is REMOVED under it: the name is free, so the same watcher takes it
        #     again — same pid, same record — instead of writing on with nothing on disk.
        os.unlink(lock_path)
        assert not os.path.exists(lock_path), "the fixture lock was not removed"
        assert watcher_reclaimed(), "the watcher did not re-claim its removed name"
        assert daemon_l.poll() is None, "the watcher stopped instead of re-claiming"
        assert lk.lock_holder(lock_path) == daemon_l.pid, "the re-claimed name is not held"
        # (2) the file is REPLACED — a rename onto the name is what "replaced" means, since
        #     writing into the held file keeps the lock and only rewrites the record.
        foreign = os.path.join(TEST_HOME, "claim-foreign.pid")
        with open(foreign, "w") as fh:
            fh.write("{}\n")
        os.replace(foreign, lock_path)
        assert watcher_reclaimed(), "the watcher did not re-claim its replaced name"
        assert daemon_l.poll() is None, "the watcher stopped on a replaced name"
        assert lk.lock_holder(lock_path) == daemon_l.pid, "the re-claimed file is not held"
        # (3) a name a LIVE process HOLDS belongs to that process: the watcher stands down
        #     rather than fight it. A helper locks a fresh file first and is renamed onto the
        #     name, so there is no moment where the name is free for either side.
        holder_script = os.path.join(TEST_HOME, "claim-holder.py")
        with open(holder_script, "w") as fh:
            fh.write(
                "import fcntl, json, os, sys, time\n"
                "path = sys.argv[1]\n"
                "fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)\n"
                "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
                "os.ftruncate(fd, 0)\n"
                "os.write(fd, (json.dumps({'pid': os.getpid()}) + '\\n').encode())\n"
                "print('held', flush=True)\n"
                "time.sleep(600)\n"
            )
        held_file = os.path.join(TEST_HOME, "claim-held.pid")
        holder = subprocess.Popen(
            [sys.executable, holder_script, held_file],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True, cwd=CWD,
        )
        assert holder.stdout.readline().strip() == b"held", "the helper did not take the name"
        os.replace(held_file, lock_path)
        assert lk.lock_holder(lock_path) == holder.pid, "the helper does not hold the name"
        assert daemon_l.wait(timeout=15) == 0, "the watcher fought a live holder"
        assert lk.lock_holder(lock_path) == holder.pid, "the helper's claim went away"
        say("a watcher whose claim name moves re-claims a free or replaced name as the same "
            "pid, and stands down for a live holder: ok")
    finally:
        if holder is not None:
            kill_tree(holder)
        if daemon_l.poll() is None:
            daemon_l.terminate()
            try:
                daemon_l.wait(timeout=15)
            except subprocess.TimeoutExpired:
                kill_tree(daemon_l)
        kill_tree(victim_l)
        if os.path.exists(lock_path):
            os.unlink(lock_path)

    # ---- ...and a state root that is GONE is the one case the watcher stops for: it must
    #      not resurrect a home somebody removed. A root of its own, so nothing here is
    #      shared with the fixture above.
    gone_root = os.path.join(TEST_HOME, "gone-root")
    victim_g = spawn_quiet("sleep", "600")
    env_gone = {**env, "FBTODO_HOME": gone_root}
    daemon_g = subprocess.Popen(
        [sys.executable, FB, "daemon", "--foreground", "--quiet",
         "--instance-pid", str(victim_g.pid), "--cwd", CWD, "-i", "0.2"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, start_new_session=True, env=env_gone, cwd=CWD,
    )
    try:
        gone_lock = os.path.join(gone_root, "fbtodo-daemon.pid")
        deadline = time.time() + 10
        while time.time() < deadline and not os.path.exists(gone_lock):
            time.sleep(0.1)
        assert os.path.exists(gone_lock), "the gone-root watcher never claimed"
        shutil.rmtree(gone_root, ignore_errors=True)
        assert daemon_g.wait(timeout=15) == 0, "the watcher did not stop on a removed root"
        # ...and it did NOT re-claim. A state write already in flight may put the directory
        # back, so the directory's existence is not the question — the claim is: a removed
        # root must not be healed the way a moved name is.
        assert not os.path.exists(gone_lock), "the watcher re-claimed a removed state root"
        say("a removed state root stops the watcher instead of being re-claimed: ok")
    finally:
        kill_tree(daemon_g)
        kill_tree(victim_g)
        shutil.rmtree(gone_root, ignore_errors=True)# ---- json/bar/snap contracts, against the same fixture root the watcher read: the
    #      operator's live session is not a fixture and cannot be asserted about.
    #      ...which is also why the desktop glob is NAMED here, as one that matches nothing:
    #      `auto` at the home directory prefers a LIVE desktop thread over a CLI journal
    #      that finished days ago (a pane following this very session is such a thread), so
    #      with the machine's own default glob this check was answering from the operator's
    #      store and went red on a healthy machine — measured 2026-10-04, twice, on a live
    #      session, with the fixture root it was given sitting right there unused. The
    #      assertion is unchanged; only the question stops depending on what else is running.
    j = json.loads(run("json", "--cli-root", cli_root, "--db", no_desk).stdout)
    assert j.get("backend") == "cli" and j.get("todos"), j
    bar_out = run("bar", "--cli-root", cli_root, "--db", no_desk).stdout
    assert bar_out.strip().startswith("todos "), bar_out
    assert "no conversation DB found" not in run(
        "snap", "--cli-root", cli_root, "--db", no_desk
    ).stderr
    say("json / bar / snap subcommands: ok")

    # ---- the golden files: the same three contracts, byte for byte. Their own fixture
    #      state is fed through the same functions the commands call, with the clock frozen
    #      and the state directory empty, so a change to what the pane prints is a DIFF to
    #      read rather than a surprise in the terminal.
    gold = os.path.join(ROOT, "tests", "golden.py")
    run_gold = lambda where=None: subprocess.run(  # noqa: E731
        [sys.executable, gold] + (["--golden", where] if where else []),
        capture_output=True, text=True, env=env, cwd=CWD, timeout=180,
    )
    g = run_gold()
    assert g.returncode == 0, (g.returncode, g.stdout[-400:], g.stderr[-2000:])
    # ...and it CAN fail: a checker nobody has seen fail is a checker nobody can trust.
    mutated = os.path.join(TEST_HOME, "golden-mutated")
    shutil.rmtree(mutated, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, "tests", "golden"), mutated)
    with open(os.path.join(mutated, "bar.txt"), "a") as fh:
        fh.write("todos 9/9\n")
    bad = run_gold(mutated)
    assert bad.returncode == 1, (bad.returncode, bad.stdout[-300:], bad.stderr[-600:])
    assert "CHANGED" in bad.stderr and "todos 9/9" in bad.stderr, bad.stderr[-600:]
    say("golden files: json, bar, snap and the frame match what was recorded — and a "
        "changed contract fails the check: ok")

    # ---- new session drops the previous list instead of showing it
    module = load_fbtodo()

    # ---- what decides a state write: the clock comes out of the comparison, everything
    #      else stays, and "everything else" includes fields this does not know about — a
    #      whitelist of what to compare would silently stop noticing whatever a later change
    #      adds to the state.
    ev = module.state_evidence({
        "heartbeat_ms": 1, "probed_ms": 2, "age_s": 3, "nested": {"at_ms": 4, "text": "x"},
        "task_times": {"a": {"elapsed_ms": 5, "started_ms": 6, "done": True}},
        "todos": [{"task": "a", "completed": True}], "goal": "g",
    })
    assert ev["goal"] == "g" and ev["todos"][0]["task"] == "a", ev
    assert not [k for k in ev if k.endswith("_ms") or k == "age_s"], ev
    assert ev["nested"] == {"text": "x"}, ev
    assert ev["task_times"]["a"] == {"done": True}, ev
    # a real change is not hidden by a moved clock, which is the whole point of the gate
    a = {"todos": [{"task": "a", "completed": False}], "heartbeat_ms": 1}
    b = {"todos": [{"task": "a", "completed": True}], "heartbeat_ms": 9}
    assert module.state_evidence(a) != module.state_evidence(b)
    say("state: the clock is not evidence, and a real change still is: ok")

    # ---- a write that carries no evidence may skip the flushes, never the rename: the file
    #      it leaves is whole, and no temp file is left behind for a sweep to find.
    tmpj = os.path.join(TEST_HOME, "no-fsync.json")
    module.atomic_write_json(tmpj, {"a": 1}, fsync=False)
    with open(tmpj) as fh:
        assert json.load(fh) == {"a": 1}
    assert not [n for n in os.listdir(TEST_HOME) if n.startswith(".fbtodo.")], "temp left"
    say("atomic writes: a no-flush write is still atomic and leaves nothing behind: ok")

    # ---- the journal scan is remembered by the file's identity, size and mtime. The watcher
    #      asks once a second while the agent iterates every few seconds, so most polls are
    #      the same question; the cache must never answer a DIFFERENT one, and must never hand
    #      out the dict it is holding.
    scan_dir = os.path.join(TEST_HOME, "scan-cache")
    shutil.rmtree(scan_dir, ignore_errors=True)
    os.makedirs(scan_dir)
    scan_log = os.path.join(scan_dir, "log.jsonl")

    def scan_line(iteration: int, task: str) -> str:
        return json.dumps({
            "level": "DEBUG",
            "timestamp": f"2026-01-01T00:00:{iteration:02d}.000Z",
            "data": {"iteration": iteration, "prompt": "do the thing", "toolCalls": [
                {"toolName": "write_todos", "input": {"todos": [
                    {"task": task, "completed": False}]}}]},
        }) + "\n"

    with open(scan_log, "w") as fh:
        fh.write(scan_line(1, "one"))
    first = module.scan_live_log(scan_dir)
    assert "one" in json.dumps(first.get("hit")), first
    hits = module._SCAN_HITS[0]
    again = module.scan_live_log(scan_dir)
    assert module._SCAN_HITS[0] == hits + 1, "an unchanged journal was re-walked"
    assert again == first, (again, first)
    # the caller decorates what it gets: a mutation of the answer must not reach the cache
    again["observed"].append({"verb": "x", "what": "y", "ts_ms": 0})
    assert not module.scan_live_log(scan_dir)["observed"], "the cache handed out its own dict"
    # ...and a journal that HAS changed is not answered from it
    before = module._SCAN_HITS[0]
    with open(scan_log, "a") as fh:
        fh.write(scan_line(2, "two"))
    moved = module.scan_live_log(scan_dir)
    assert "two" in json.dumps(moved.get("hit")), moved
    assert module._SCAN_HITS[0] == before, "a changed journal was answered from the cache"
    say("journal scan: the same question is answered once, and a changed journal is not: ok")

    # ---- a journal that only GREW is folded from its cursor instead of being walked again.
    #      The chunks of an append-only file never change once they are full, so the cursor
    #      is the end of the last full chunk the walk read: what is re-parsed is the bytes
    #      after it — one chunk per poll — not the window. The bytes are CHECKED, not
    #      trusted, so a journal rewritten in place and grown past the old size is not
    #      answered from what the cursor remembered.
    saved_chunk = module.CHUNK
    set_knob(module, "CHUNK", 4096)  # so a fixture a few KiB spans the several chunks a fold needs
    try:
        grow_dir = os.path.join(TEST_HOME, "scan-grow")
        shutil.rmtree(grow_dir, ignore_errors=True)
        os.makedirs(grow_dir)
        grow_log = os.path.join(grow_dir, "log.jsonl")
        pad = "x" * 200

        def grow_line(n, task=None, prompt=None):
            data = {"iteration": n, "filler": pad}
            if prompt:
                data["prompt"] = prompt
            if task:
                data["toolCalls"] = [{"toolName": "write_todos", "input": {"todos": [
                    {"task": task, "completed": False}]}}]
            return json.dumps({
                "level": "DEBUG",
                "timestamp": f"2026-01-01T00:{n // 60:02d}:{n % 60:02d}.000Z",
                "data": data}) + "\n"

        with open(grow_log, "w") as fh:
            for n in range(120):
                fh.write(grow_line(n))
            # the list, then prose after it: the walk has to look BACK for the hit, which is
            # what makes a cold scan read several chunks rather than one
            fh.write(grow_line(200, task="one", prompt="do the thing"))
            for n in range(201, 261):
                fh.write(grow_line(n))
        size0 = os.path.getsize(grow_log)
        assert size0 > 8 * module.CHUNK, size0
        module._SCAN_CACHE.clear()
        module._SCAN_PARSED[0] = 0
        module._SCAN_REUSED[0] = 0
        cold = module.scan_live_log(grow_dir)
        cold_parsed = module._SCAN_PARSED[0]
        assert "one" in json.dumps(cold.get("hit")), cold
        assert cold_parsed >= 4 * module.CHUNK, cold_parsed
        assert module._SCAN_REUSED[0] == 0, "a cold walk answered itself from a cache"
        # the journal only grows: one poll's worth of prose arrives
        with open(grow_log, "a") as fh:
            fh.write(grow_line(300))
            fh.write(grow_line(301))
        module._SCAN_PARSED[0] = 0
        module._SCAN_REUSED[0] = 0
        grown = module.scan_live_log(grow_dir)
        grown_parsed = module._SCAN_PARSED[0]
        assert "one" in json.dumps(grown["hit"]), grown
        assert module._SCAN_REUSED[0] >= 2, (
            f"a grown journal re-walked instead of folding from its cursor: {grown_parsed} bytes")
        assert grown_parsed * 3 < cold_parsed, (grown_parsed, cold_parsed)
        # ...and the fold says exactly what walking the whole thing says
        module._SCAN_CACHE.clear()
        assert grown == module.scan_live_log(grow_dir), "the folded answer is not the walked one"
        # rewritten in place and grown past the old size: the bytes behind the cursor are
        # checked, so the list that changed inside them is not answered from the old parse
        body = open(grow_log, "rb").read()
        with open(grow_log, "wb") as fh:
            fh.write(body.replace(b'"task": "one"', b'"task": "ONE"'))
            fh.write(grow_line(400).encode("utf-8"))
        rewritten = module.scan_live_log(grow_dir)
        module._SCAN_CACHE.clear()
        assert rewritten == module.scan_live_log(grow_dir), "a rewritten journal was folded stale"
        assert "ONE" in json.dumps(rewritten["hit"]), rewritten
        # truncated in place: shorter than the cursor, so it is walked from scratch
        with open(grow_log, "wb") as fh:
            fh.write(body[: max(1, len(body) // 3)])
        cut = module.scan_live_log(grow_dir)
        module._SCAN_CACHE.clear()
        assert cut == module.scan_live_log(grow_dir), "a truncated journal was folded"
        # rotated onto the same path: a new inode, so nothing of the old walk applies
        with open(grow_log + ".new", "w") as fh:
            fh.write(grow_line(1, task="rotated", prompt="rotate me") * 40)
        os.replace(grow_log + ".new", grow_log)
        rot = module.scan_live_log(grow_dir)
        module._SCAN_CACHE.clear()
        assert rot == module.scan_live_log(grow_dir), "a rotated journal was folded"
        assert "rotated" in json.dumps(rot.get("hit")), rot
        say("journal cursor: a grown journal is folded, a rewritten one is not: ok")
    finally:
        set_knob(module, "CHUNK", saved_chunk)
        shutil.rmtree(os.path.join(TEST_HOME, "scan-grow"), ignore_errors=True)

    # ---- the claim is a REAL one: the kernel holds it. A record naming a live process
    #      that never took it (a leftover from a crash, or a pid that came round again) is
    #      not a watcher, and a bystander cannot give away a live watcher's claim by
    #      unlinking its file — the holder keeps its inode while a third process could
    #      claim a fresh file of the same name, which is two watchers on one scratch dir.
    victim_lk = spawn_quiet("sleep", "600")
    holder = subprocess.Popen(
        [sys.executable, FB, "daemon", "--foreground", "--quiet",
         "--instance-pid", str(victim_lk.pid), "--cwd", CWD, "-i", "0.2"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, start_new_session=True, env=env, cwd=CWD,
    )
    deadline = time.time() + 10
    while time.time() < deadline and not os.path.exists(lock_path):
        time.sleep(0.1)
    assert module.lock_holder(lock_path) == holder.pid, (module.lock_holder(lock_path),
                                                         holder.pid)
    # ...with the path spelled out everywhere: this process's own LOCK_PATH is the REAL
    # scratch dir (only the child daemon gets FBTODO_HOME), and a claim taken there would
    # be a claim over the operator's live watcher
    assert module.write_lock(CWD, victim_lk.pid, path=lock_path) is False, \
        "a second claim was taken over a live one"
    assert not module.clear_lock(path=lock_path), "a bystander removed a live watcher's claim"
    assert os.path.exists(lock_path), "a bystander removed the lock file"
    assert module.lock_holder(lock_path) == holder.pid, module.lock_holder(lock_path)
    # SIGKILL is what a crash looks like: no chance to tidy up, so the RECORD survives the
    # watcher while the CLAIM does not — the kernel drops it with the process. The leftover
    # is removed by whoever asks next, which is the other half of `lock_holder`.
    kill_tree(holder)
    kill_tree(victim_lk)
    deadline = time.time() + 10
    while time.time() < deadline and module.lock_holder(lock_path):
        time.sleep(0.1)
    assert module.lock_holder(lock_path) is None, "a killed watcher's claim survived it"
    assert not os.path.exists(lock_path), "the leftover record was not cleaned up"
    say("locks: the claim is the kernel's, and only its holder can give it up: ok")

    # ---- a record naming a LIVE process is not a watcher. The pid resolves (it is ours),
    #      and a pid-alive check would believe it; the claim decides, so the leftover is
    #      cleaned up and a fresh watcher is free to start over it.
    module.atomic_write_json(lock_path, {"pid": os.getpid(), "cwd": CWD, "started_ms": 0,
                                         "instance_pid": None, "version": module.VERSION})
    assert module.lock_holder(lock_path) is None, "a leftover record was read as a running watcher"
    assert not os.path.exists(lock_path), "the leftover record was left behind"
    assert module.live_watcher_pid(lock_path) is None
    say("locks: a leftover record whose pid is alive is not a watcher, and is cleaned up: ok")

    # ---- a claim being BORN is not a leftover. `write_lock` creates the file a breath
    #      before it can lock it, and a probe that takes a free file used to remove it as a
    #      stale record. Two asks arriving together then each removed the other's newborn
    #      claim: each keeper went on with its claim on an unlinked inode — nothing on disk
    #      to name it — and each ask logged "never claimed the lock" while a keeper was
    #      running. A free file that NAMES A PID is still cleaned up (the test above); an
    #      EMPTY one is a claim about to be locked, and a probe leaves it alone.
    missing = os.path.join(TEST_HOME, "claim-missing.pid")
    if os.path.exists(missing):
        os.unlink(missing)
    assert module.lock_holder(missing) is None, "a missing claim was read as held"
    assert not os.path.exists(missing), "a probe created a claim file where none existed"
    newborn = os.path.join(TEST_HOME, "claim-newborn.pid")
    if os.path.exists(newborn):
        os.unlink(newborn)
    fd = module.lock_open(newborn)             # what `write_lock` does first...
    assert module.lock_holder(newborn) is None, "an empty claim was read as a holder"
    assert os.path.exists(newborn), "a probe removed a claim before it was locked"
    assert module.lock_take(fd), "the newborn claim could not be locked after the probe"
    module._LOCK_FDS[newborn] = fd             # ...and what it keeps once the lock is held
    assert module.write_lock(CWD, None, path=newborn, extra={"tmux": "probe"})
    assert module.lock_holder(newborn) == os.getpid(), \
        "a claim stayed hidden after a probe raced it"
    module.clear_lock(path=newborn)
    say("locks: a claim being born survives a probe and stays visible once locked: ok")

    # ---- and the last window in that same moment: a claim file REPLACED between opening
    #      and locking. `write_lock` opens the file, then locks it; in between, a probe can
    #      take the free file and remove it (the leftover cleanup above) — the claimer would
    #      then lock an inode the name no longer points at, keeping every pane with nothing
    #      on disk to name it, while a second keeper could claim a fresh file of the same
    #      name. `write_lock` re-checks the name against the locked inode and takes the
    #      name again instead. The window is held open here with a wrapped `lock_open`: the
    #      claimer pauses after opening (a breath a probe can step into), the probe runs its
    #      leftover cleanup, and the claimer resumes over the removed name.
    race_path = os.path.join(TEST_HOME, "claim-race.pid")
    keep_path = os.path.join(TEST_HOME, "claim-kept.pid")
    gates = {race_path: {"first": True, "opened": threading.Event(), "go": threading.Event()},
             keep_path: {"first": True, "opened": threading.Event(), "go": threading.Event()}}
    real_open = module.lock_open

    def paused_open(p, *a, **k):
        fd = real_open(p, *a, **k)
        slot = gates.get(p)
        if slot and slot["first"]:
            slot["first"] = False
            slot["opened"].set()
            assert slot["go"].wait(timeout=10), "the race never resumed"
        return fd

    set_knob(module, "lock_open", paused_open)
    try:
        module.atomic_write_json(race_path, {"pid": os.getpid(), "cwd": CWD, "started_ms": 0,
                                             "instance_pid": None, "version": module.VERSION})
        held = {}

        def claim_race():
            held["ok"] = module.write_lock(CWD, None, path=race_path)

        racer = threading.Thread(target=claim_race)
        racer.start()
        assert gates[race_path]["opened"].wait(timeout=10), "the claim was never opened"
        assert module.lock_holder(race_path) is None, "a free record was read as a holder"
        assert not os.path.exists(race_path), "the probe did not clear the stale record"
        gates[race_path]["go"].set()
        racer.join(timeout=30)
        assert not racer.is_alive(), "the racing claim never finished"
        assert held.get("ok") is True, held
        # ...and the keeper that lost its name to the probe is VISIBLE again: the name
        # holds a claim, and it is this process's
        assert os.path.exists(race_path), "a claim removed under the claimer stayed removed"
        assert module.lock_holder(race_path) == os.getpid(), \
            "a claim replaced between opening and locking left a keeper invisible"
        say("locks: a claim replaced between opening and locking is taken again, never left "
            "nameless: ok")

        # ---- and the inverse must hold too: a probe that opened one file must never remove
        #      the NAME once it has been replaced by somebody else's claim — that would
        #      strand the second keeper exactly the same way. The probe is paused after its
        #      open, the name moves on to a fresh, HELD claim, and the probe resumes: it must
        #      answer about the file the name points at, and leave it alone.
        module.atomic_write_json(keep_path, {"pid": os.getpid(), "cwd": CWD, "started_ms": 0,
                                             "instance_pid": None, "version": module.VERSION})
        probed = {}

        def keep_probe():
            probed["pid"] = module.lock_holder(keep_path)

        probe = threading.Thread(target=keep_probe)
        probe.start()
        assert gates[keep_path]["opened"].wait(timeout=10), "the probe never opened the claim"
        os.unlink(keep_path)                 # the name moves...
        kfd = module.lock_open(keep_path)    # ...to a fresh claim, held here
        assert module.lock_take(kfd), "the replacement claim could not be locked"
        module._LOCK_FDS[keep_path] = kfd
        assert module.write_lock(CWD, None, path=keep_path, extra={"who": "replacement"})
        gates[keep_path]["go"].set()
        probe.join(timeout=30)
        assert not probe.is_alive(), "the probe never finished"
        assert os.path.exists(keep_path), "a probe removed a name that was not its own file"
        assert probed.get("pid") == os.getpid(), probed
        assert module.lock_holder(keep_path) == os.getpid(), \
            "the replacement claim went invisible"
        say("locks: a probe never removes a name that is not the file it locked: ok")
    finally:
        set_knob(module, "lock_open", real_open)
        module.clear_lock(path=race_path)
        module.clear_lock(path=keep_path)

    # ---- the audit `fbtodo locks` asks: who holds each claim, whether the lock and the
    #      NAME are one file, whether a record is stale, and what clearing it takes — asked
    #      WITHOUT writing anything. The probe `lock_holder` would remove a free leftover;
    #      an audit exists to say so instead, so every one of these checks re-reads the
    #      file afterwards: the states are absent, a dead-pid leftover, a live-pid free
    #      record (the tie broken — a birth, a leftover, or an orphan), a held claim, and
    #      this process's OWN claim with the name moved off it.
    audit_absent = os.path.join(TEST_HOME, "claim-audit-absent.pid")
    if os.path.exists(audit_absent):
        os.unlink(audit_absent)
    entry = module.claim_audit(audit_absent)
    assert entry["state"] == "absent" and not os.path.exists(audit_absent), entry

    audit_dead = os.path.join(TEST_HOME, "claim-audit-dead.pid")
    victim_audit = spawn_quiet("sleep", "600")
    dead_pid_audit = victim_audit.pid
    kill_tree(victim_audit)
    module.atomic_write_json(audit_dead, {"pid": dead_pid_audit, "cwd": CWD, "started_ms": 0,
                                          "instance_pid": None, "version": module.VERSION})
    entry = module.claim_audit(audit_dead)
    assert entry["state"] == "free" and entry["leftover"] is True, entry
    assert entry["pid"] == dead_pid_audit and entry["pid_alive"] is False, entry
    assert entry["name_inode"] is None, entry
    assert "leftover" in entry["note"] and "safe to remove" in entry["clearing"], entry
    assert os.path.exists(audit_dead), "the audit removed the file it was reading"

    # a free file whose record names a LIVE process: the name-to-inode tie is the thing
    # broken — the named process does not hold the file this name points at
    module.atomic_write_json(audit_dead, {"pid": os.getpid(), "cwd": CWD, "started_ms": 0,
                                          "instance_pid": None, "version": module.VERSION})
    entry = module.claim_audit(audit_dead)
    assert entry["state"] == "free" and entry["pid_alive"] is True, entry
    assert entry["name_inode"] == "mismatch" and "does not hold" in entry["note"], entry
    os.unlink(audit_dead)

    # held: the lock blocks this process's own open of the name, so the name IS what is
    # claimed — and clearing means ending the holder, never unlinking the file
    audit_held = os.path.join(TEST_HOME, "claim-audit-held.pid")
    module.atomic_write_json(audit_held, {"pid": os.getpid(), "cwd": CWD, "started_ms": 0,
                                          "instance_pid": None, "version": module.VERSION})
    held_fd = module.lock_open(audit_held)
    assert module.lock_take(held_fd), "the audited claim could not be locked"
    entry = module.claim_audit(audit_held)
    assert entry["state"] == "held" and entry["name_inode"] == "match", entry
    assert entry["pid"] == os.getpid() and entry["pid_alive"] is True, entry
    assert "end the holder" in entry["clearing"], entry
    assert os.path.exists(audit_held), "the audit removed a held claim"
    # ...and the same claim with the NAME moved off it: the audit sees the orphan from the
    # inside — the registry entry is ours, the name is another file
    module._LOCK_FDS[audit_held] = held_fd
    module.atomic_write_json(audit_held, {"pid": os.getpid(), "cwd": CWD, "started_ms": 1,
                                          "instance_pid": None, "version": module.VERSION})
    entry = module.claim_audit(audit_held)
    assert entry["ours_claimed"] is True and entry["ours_named"] is False, entry
    assert entry["name_inode"] == "mismatch", entry
    assert "no longer points at the name" in entry["note"], entry
    assert "re-claim" in entry["clearing"], entry
    module._LOCK_FDS.pop(audit_held, None)
    os.close(held_fd)
    os.unlink(audit_held)
    say("locks: the audit names each claim's holder, its name-to-inode tie, a stale record "
        "and what clearing takes — without writing anything, its own claim included: ok")

    # ---- and the command over the state root's own files: the watcher's leftover, the
    #      keeper's claim held by this process, and a file that is not there at all —
    #      text and JSON say the same facts, and the run does not change a byte (flush
    #      the rows, then read the files back). The absent one is asked FIRST, while the
    #      keeper has not claimed anything: a role with no file is a real answer, and the
    #      only way to see one is for there to be one.
    module.atomic_write_json(module.LOCK_PATH, {"pid": dead_pid_audit, "cwd": CWD,
                                                "started_ms": 0, "instance_pid": None,
                                                "version": module.VERSION})
    bare = STRIP(run("locks").stdout)
    assert "watcher" in bare and "leftover" in bare, bare
    assert "no file — no claim" in bare, bare
    assert json.loads(run("locks", "--json").stdout)["claims"][1]["state"] == "absent", \
        json.loads(run("locks", "--json").stdout)["claims"]
    module.atomic_write_json(module.PANE_KEEPER_PATH, {"pid": os.getpid(), "cwd": CWD,
                                                       "started_ms": 0, "instance_pid": None,
                                                       "version": module.VERSION})
    audit_fd = module.lock_open(module.PANE_KEEPER_PATH)
    assert module.lock_take(audit_fd), "the keeper claim could not be locked"
    module._LOCK_FDS[module.PANE_KEEPER_PATH] = audit_fd
    try:
        before_audit = {p: open(p, "rb").read()
                        for p in (module.LOCK_PATH, module.PANE_KEEPER_PATH)}
        ran = run("locks")
        assert ran.returncode == 0, (ran.returncode, ran.stdout, ran.stderr)
        out = STRIP(ran.stdout)
        assert "watcher" in out and "leftover" in out, out
        assert "keeper" in out and f"pid {os.getpid()}" in out and "held by" in out, out
        doc = json.loads(run("locks", "--json").stdout)
        by_role = {c["role"]: c for c in doc["claims"]}
        assert by_role["watcher"]["state"] == "free", by_role["watcher"]
        assert by_role["watcher"]["leftover"] is True, by_role["watcher"]
        assert by_role["watcher"]["pid"] == dead_pid_audit, by_role["watcher"]
        assert by_role["keeper"]["state"] == "held", by_role["keeper"]
        assert by_role["keeper"]["pid"] == os.getpid() and by_role["keeper"]["pid_alive"], \
            by_role["keeper"]
        assert by_role["keeper"]["name_inode"] == "match", by_role["keeper"]
        after_audit = {p: open(p, "rb").read()
                       for p in (module.LOCK_PATH, module.PANE_KEEPER_PATH)}
        assert after_audit == before_audit, "the audit wrote to a claim file"
        say("locks: the command audits the watcher's leftover, the held keeper and an absent "
            "file — text and JSON, and not a byte written: ok")
    finally:
        module.clear_lock(path=module.PANE_KEEPER_PATH)
        if os.path.exists(module.LOCK_PATH):
            os.unlink(module.LOCK_PATH)

    # ---- ...and the audit has a second half the name cannot see: a running watcher or
    #      keeper whose claim file was replaced under it holds a lock on an inode no name
    #      points at — the file reads `absent` or `free` while the process goes on keeping
    #      panes. `claim_processes` finds the processes by their argv (the subcommand right
    #      after the launcher, so a system daemon whose line merely contains the word is not
    #      one) and keeps only those whose environment names THIS state root; `claim_orphans`
    #      matches them against what the claim's own audit says. Injected tables and blobs,
    #      so the rule is pinned without depending on what is running on this machine —
    #      and the end-to-end case with a real keeper comes later, in the keeper block.
    #
    # The argv rule first, on strings: the subcommand right after the launcher (or the
    # package's own entry file), never a word that merely appears in the line.
    assert module._fbtodo_subcommand(
        f"{sys.executable} /p/fbtodo daemon --foreground") == "daemon"
    assert module._fbtodo_subcommand(
        f"{sys.executable} /p/fbtodo/__init__.py pane-watch --foreground") == "pane-watch"
    assert module._fbtodo_subcommand("/usr/sbin/distnoted daemon") is None
    assert module._fbtodo_subcommand("python /p/fbtodo") is None
    assert module._fbtodo_subcommand(
        "grep -n 'pane-watch' /p/fbtodo") is None, "a mention is not a run"
    say("locks: a role process is recognized by its launcher token and the subcommand "
        "right after it, never by a word in the line: ok")

    PROC_BLOBS = {
        900: f"FBTODO_HOME={TEST_HOME}",      # a watcher for this root: ours
        901: f"FBTODO_HOME={TEST_HOME}",      # a keeper for this root: ours
        902: f"FBTODO_HOME=/elsewhere/state",  # another checkout's: not ours
        903: "",                                # unreadable environment: assumed ours
        904: f"FBTODO_HOME={TEST_HOME}",
        905: f"FBTODO_HOME={TEST_HOME}",      # launcher-less copy: the entry file's shape
        906: f"FBTODO_HOME={TEST_HOME}",      # a bare spawn: it only STARTS a watcher
        907: f"FBTODO_HOME={TEST_HOME}",
        # the answer `ps -Eww` gives for a process whose environment the kernel will not
        # copy: its command line and nothing else. No assignment in it, so it is not an
        # environment, and no root may be invented from it.
        908: "    1 ??   219:10.15 /sbin/launchd",
    }
    proc_table = {
        900: (1, f"{sys.executable} /p/fbtodo daemon --foreground --quiet --cwd /x"),
        901: (1, f"{sys.executable} /p/fbtodo pane-watch --foreground --quiet"),
        902: (1, f"{sys.executable} /p/fbtodo daemon --foreground"),
        903: (1, f"{sys.executable} /p/fbtodo daemon --foreground --quiet --cwd /y"),
        904: (1, "/usr/sbin/distnoted daemon"),
        905: (1, f"{sys.executable} /p/fbtodo/__init__.py pane-watch --quiet"),
        906: (1, f"{sys.executable} /p/fbtodo daemon --quiet"),
        908: (1, f"{sys.executable} /p/fbtodo daemon --foreground --quiet"),
    }
    saved_ages = module.ages_for
    set_knob(module, "ages_for", lambda pids: {p: 400 for p in pids})
    try:
        procs = module.claim_processes(root=TEST_HOME, table=proc_table, environs=PROC_BLOBS)
        by_pid = {p["pid"]: p for p in procs}
        assert set(by_pid) == {900, 901, 903, 905, 908}, by_pid
        assert by_pid[900]["role"] == "watcher" and by_pid[901]["role"] == "keeper", by_pid
        assert by_pid[903]["role"] == "watcher" and by_pid[905]["role"] == "keeper", by_pid
        assert 906 not in by_pid, "a bare spawn that only STARTS a watcher is not one"
        assert by_pid[900]["root"] == TEST_HOME and by_pid[900]["age_s"] == 400, by_pid[900]
        # the launcher-less copy's python is its own line's, read not assumed
        assert by_pid[905]["python"] == sys.executable, by_pid[905]
        # ...and whether the environment was READ at all. An unreadable one is still assumed
        # to be ours for the AUDIT's sentence ("a false positive is a sentence, not a
        # kill"); the fix is the other side of that promise, so the row says which it is.
        assert by_pid[900]["root_named"] is True and by_pid[905]["root_named"] is True, by_pid
        assert by_pid[903]["root_named"] is False, by_pid[903]
        # a command line is not an environment: it is reported (this root assumed for the
        # sentence) and never placed, so `locks --fix` cannot end it
        assert by_pid[908]["root_named"] is False, by_pid[908]
        assert by_pid[908]["root"] == TEST_HOME and by_pid[908]["env_clipped"] is False, \
            by_pid[908]
        assert by_pid[900]["env_clipped"] is False, by_pid[900]
        say("locks: the cross-check finds the watcher and keeper running with "
            "this state root — launcher or entry file alike — leaves another root's "
            "processes alone, and ignores the bare spawns that only START a watcher: ok")
        # ---- the environment read itself. `ps -Eww` answers with a command line whether or
        #      not the kernel let it copy the environment, and reading that as an environment
        #      places a process on the DEFAULT root it never named. Measured on this machine
        #      2026-10-02: the kernel CAPS its copy for a process it will not expose — every
        #      launchd/GUI process came back with 1012–1132 bytes while `ps` printed 516–974
        #      of them, a launcher run with two variables got 1644, the roles 3044–3844, and a
        #      250 KB environment 257612 — so the size of the kernel's own copy is the signal,
        #      and both halves are pinned here.
        assert module._env_read("FBTODO_HOME=/x PATH=/bin") is True
        assert module._env_read("    1 ??   219:10.15 /sbin/launchd") is False
        assert module._env_read("") is False and module._env_read("PID TTY TIME CMD") is False
        assert module._env_names("PATH=/bin FBTODO_HOME=/x") == {"PATH", "FBTODO_HOME"}
        assert module._env_root("FBTODO_HOME=/x") is True
        assert module._env_root("XDG_STATE_HOME=/y PATH=/bin") is True
        assert module._env_root("PATH=/bin SHELL=/bin/zsh") is False
        assert module._environ_clipped("    1 ??  0:01 /sbin/launchd", 78) is False, \
            "a short command line was read as a clipped environment"
        assert module._environ_clipped("x" * 516, 1012) is True, "a capped copy was read whole"
        assert module._environ_clipped("x" * 766, 1284) is False, "a whole copy was called capped"
        assert module._environ_clipped("x" * 3383, 3844) is False, "a full copy was called capped"
        assert module._environ_clipped("", 1012) is False and \
            module._environ_clipped("x" * 40, 0) is False, "a missing source invented a clip"
        assert module.KERNEL_COPY_FLOOR <= 1012 <= module.KERNEL_COPY_CAP < 1284
        say("locks: a `ps -Eww` answer with no assignment in it is a command line, not an "
            "environment — it is never believed, and never read as the default root: ok")
        say("locks: the clip is the kernel's own byte count (1012 bytes is the capped kind "
            "`ps` printed 516 of, 3844 is a whole copy), a short command line is not a clip, "
            "and a missing second source is never an invented one: ok")
        # ...and the same read on real processes: a child run with a root in its environment
        # is placed by it, and a platform binary — whose environment this platform will not
        # hand over — reads as no environment at all rather than as the default root.
        kid = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            env={"PATH": os.environ.get("PATH", ""), "FBTODO_HOME": TEST_HOME},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            kid_blob, kid_clipped = module._proc_environ(kid.pid)
            assert module._env_read(kid_blob) and module._env_root(kid_blob), kid_blob[:200]
            assert module._proc_root(kid_blob) == TEST_HOME, module._proc_root(kid_blob)
            assert kid_clipped is False, "a child's own environment was called clipped"
        finally:
            kid.terminate()
            kid.wait(timeout=10)
        one_blob, _one_clip = module._proc_environ(1)
        assert module._env_read(module._environ_ps(1)) is False, module._environ_ps(1)[:200]
        assert module._env_read(one_blob) is False, one_blob[:200]
        assert module._proc_root(one_blob) == os.path.join(os.path.expanduser("~"),
                                                           ".local", "state", "fbtodo")
        say("locks: a real child's root comes from the environment it was started with, and "
            "a platform binary's env — which this platform will not hand over — reads as NO "
            "environment, not as the default root: ok")
        # a HELD claim names its holder: every other running process of that role is untied
        held_audit = {"state": "held", "pid": 900, "pid_alive": True, "name_inode": "match"}
        assert [p["pid"] for p in module.claim_orphans(held_audit, [by_pid[900], by_pid[901]])] \
            == [901], "a held claim's other process was not reported"
        # a free file whose record names a LIVE pid: the tie is broken — the leftover
        # cleanup took that process's name and it went on running — so it is an orphan
        free_audit = {"state": "free", "pid": 900, "pid_alive": True, "name_inode": "mismatch"}
        assert [p["pid"] for p in module.claim_orphans(free_audit, [by_pid[900], by_pid[901]])] \
            == [900, 901], "a free file's running processes were not reported"
        # ...and a DEAD pid's record is the same free file with a different note: it ties
        # nothing either, so the processes running with this root are still the answer
        dead_audit = {"state": "free", "pid": 999999, "pid_alive": False, "name_inode": None}
        assert [p["pid"] for p in module.claim_orphans(dead_audit, [by_pid[900]])] == [900], \
            dead_audit
        # a file with nothing on it at all: every running process of the role is untied
        absent_audit = {"state": "absent", "pid": None, "pid_alive": False, "name_inode": None}
        assert [p["pid"] for p in module.claim_orphans(absent_audit, [by_pid[900], by_pid[903]])] \
            == [900, 903], "a process nothing names was not reported"
        say("locks: the orphan rule — a held claim names its holder, a free or absent file "
            "ties nobody, so every running process of that role is reported: ok")
        # ...and a process YOUNGER than the grace is left out: a watcher between its start
        # and its claim, and a second watcher standing down, both look untied for a moment
        young = dict(by_pid[900], age_s=0.5)
        assert module.claim_orphans(absent_audit, [young]) == [], young
        old = dict(by_pid[900], age_s=None)
        assert [p["pid"] for p in module.claim_orphans(absent_audit, [old])] == [900], old
        say("locks: a just-started process is not an orphan, and an unreadable age hides "
            "nothing: ok")
        # ...and the fix's plan, which is those two reads put together: a FREE record that
        # NAMES a pid is a leftover to clear, a placed untied process is ended, an
        # unplaceable one is only named (an unreadable environment must never be a kill),
        # and a healthy held claim is left entirely alone.
        fix_rows = [
            ("watcher", {"state": "free", "leftover": True, "path": "/p/daemon.pid"}),
            ("keeper", {"state": "free", "leftover": True, "path": "/p/keeper.pid"}),
        ]
        clears, kills, skipped = module.locks_fix_plan(fix_rows, [by_pid[900], by_pid[903]])
        assert [label for label, _e in clears] == ["watcher", "keeper"], clears
        assert [p["pid"] for _l, p in kills] == [900], kills
        assert [p["pid"] for _l, p in skipped] == [903], skipped
        held_rows = [("watcher", {"state": "held", "leftover": False,
                                  "path": "/p/daemon.pid"})]
        assert module.locks_fix_plan(held_rows, [by_pid[900]]) == ([], [], []), \
            "a held claim with its name was planned for"
        say("locks --fix: an audit and a process table become a plan — a free leftover "
            "cleared, a placed untied process ended, an unplaceable one only named, a held "
            "claim untouched: ok")
    finally:
        set_knob(module, "ages_for", saved_ages)

    def soon(pred, seconds: float = 20.0) -> bool:
        """Wait for a REAL process to get somewhere, bounded: a watch polls on its own clock."""
        limit = time.time() + seconds
        while time.time() < limit and not pred():
            time.sleep(0.1)
        return pred()

    def poll_bound(interval_s: float, ticks: int = 3, startup_s: float = 10.0) -> float:
        """How long to wait for something a polling child announces, as ITS clock says.

        A fixed 20 s is a guess that only holds when the machine is idle: a child told to poll
        every six seconds needs two ticks for `open` then `clear`, and on a loaded machine
        (this suite beside another one, an agent turn, a watcher reloading) its own ticks come
        late — so the bound has to be the interval it was given, times the ticks the check
        waits for, plus enough for the process to start at all. Derived from the same number
        the child was launched with, a check cannot drift away from it.
        """
        return interval_s * ticks + startup_s

    def hold_window(checks: int = 4) -> float:
        """How long to watch a reloading child prove that it did NOT reload.

        The window is the child's own clocks: a file it must first stop seeing being written
        (`SOURCE_SETTLE_S`) and then several of its build checks to notice. A fixed `sleep(2.5)`
        was both too short to catch a late reload on a loaded machine — so the check passed
        vacuously — and pure dead time in every run. Waiting out the real interval and then
        requiring the count to be unchanged is both stronger and the same length.
        """
        return module.SOURCE_SETTLE_S + checks * module.BUILD_CHECK_S

    def quiet_for(pred, seconds: float) -> bool:
        """True when `pred` stayed false for the whole window: a NON-event, asserted."""
        limit = time.time() + seconds
        while time.time() < limit:
            if pred():
                return False
            time.sleep(0.1)
        return True

    # ---- ...and the safety read that uses the same two-source rule. `keeper_aim` asks every
    #      live `pane-watch` process for its state root and tmux server, and a bare `ps -Eww`
    #      here would inherit both traps — a command line read as an environment, and a copy
    #      the kernel clipped before FBTODO_HOME — and then report a real leak as aimed
    #      somewhere else. That is precisely the leak this guard exists to catch, so the read
    #      it uses is pinned: a stand-in pane-watch process with a real environment, then the
    #      command-line-only and clipped shapes forced through the sources.
    aim_pkg = os.path.join(TEST_HOME, "aim-build", "fbtodo")
    os.makedirs(aim_pkg, mode=0o700, exist_ok=True)
    with open(os.path.join(aim_pkg, "__init__.py"), "w", encoding="utf-8") as fh:
        fh.write("import time\n\ntime.sleep(600)\n")
    aim_proc = subprocess.Popen(
        [sys.executable, os.path.join(aim_pkg, "__init__.py"), "pane-watch"],
        env={"FBTODO_HOME": TEST_HOME, "FBTODO_TMUX": "aim-socket", "TMUX": "aim-server",
             "PATH": os.environ.get("PATH", "")},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, start_new_session=True,
    )
    saved_sources = {name: getattr(module, name)
                     for name in ("_environ_proc", "_environ_ps", "_kernel_copy")}
    try:
        assert soon(lambda: str(aim_proc.pid) in keeper_aim()), keeper_aim()
        aimed = keeper_aim()[str(aim_proc.pid)]
        assert aimed.get("FBTODO_HOME") == TEST_HOME and aimed["_read"] is True, aimed
        assert aimed.get("FBTODO_TMUX") == "aim-socket", aimed
        assert aimed.get("TMUX") == "aim-server" and aimed["_clipped"] is False, aimed
        # (a) the command-line-only shape: what `ps -Eww` answers for a process the kernel
        #     will not expose. It is not an environment, so the aim must say so rather than
        #     invent a keeper on the default root.
        set_knob(module, "_environ_proc", lambda pid: "")
        set_knob(module, "_environ_ps",
                 lambda pid: "  123 ??   0:01.00 /usr/sbin/distnoted daemon")
        set_knob(module, "_kernel_copy", lambda pid: ("", 0))
        blind = keeper_aim()[str(aim_proc.pid)]
        assert blind["_read"] is False and blind["_clipped"] is False, blind
        assert "FBTODO_HOME" not in blind and "FBTODO_TMUX" not in blind, blind
        # (b) the clipped shape: `ps` printed a command line and the kernel copy is the ~1 KB
        #     kind a root could have been cut off in. `_read` is still false, and `_clipped`
        #     records the capped copy the caller refuses to place on it.
        set_knob(module, "_kernel_copy", lambda pid: ("", 1012))
        capped = keeper_aim()[str(aim_proc.pid)]
        assert capped["_read"] is False and capped["_clipped"] is True, capped
        # (c) each source alone answers: /proc with the environment, or only `ps` with it.
        #     The read is their union, so either is enough.
        set_knob(module, "_environ_proc",
                 lambda pid: "FBTODO_HOME=/p/home FBTODO_TMUX=proc-socket TMUX=proc-server")
        set_knob(module, "_environ_ps", lambda pid: "")
        set_knob(module, "_kernel_copy", lambda pid: ("", 0))
        from_proc = keeper_aim()[str(aim_proc.pid)]
        assert from_proc.get("FBTODO_HOME") == "/p/home" and from_proc["_read"] is True, \
            from_proc
        set_knob(module, "_environ_proc", lambda pid: "")
        set_knob(module, "_environ_ps",
                 lambda pid: "FBTODO_HOME=/p/home2 FBTODO_TMUX=ps-socket TMUX=ps-server")
        from_ps = keeper_aim()[str(aim_proc.pid)]
        assert from_ps.get("FBTODO_HOME") == "/p/home2" and from_ps["_read"] is True, from_ps
        say("keeper aim: a live keeper's state root and tmux server come from the "
            "program's two-source environment read — a command-line-only answer and a "
            "kernel-clipped copy are reported as unaccounted for, never as the default "
            "root: ok")
    finally:
        for name, fn in saved_sources.items():
            set_knob(module, name, fn)
        kill_tree(aim_proc)
    # ...and the guard that consumes the aim, on injected aims: a test-home keeper keeping
    # this server is a leak, another root's is not, a readable default-root keeper is not,
    # and one whose environment could not be read is reported rather than skipped.
    assert keeper_leaks({}, TEST_HOME, "owner-server",
                        aims={1: {"FBTODO_HOME": TEST_HOME, "TMUX": "owner-server"}}) \
        == [(1, "owner-server", None)]
    assert keeper_leaks({}, TEST_HOME, "owner-server",
                        aims={1: {"FBTODO_HOME": "/other", "TMUX": "owner-server"}}) == []
    assert keeper_leaks({}, TEST_HOME, "owner-server",
                        aims={1: {"_read": True, "_clipped": False, "TMUX": "owner-server"}}) \
        == []
    assert keeper_leaks({}, TEST_HOME, "owner-server",
                        aims={1: {"_read": False, "_clipped": False}}) == [(1, None, None)]
    assert keeper_leaks({1: {}}, TEST_HOME, "owner-server",
                        aims={1: {"FBTODO_HOME": TEST_HOME, "TMUX": "owner-server"}}) == []
    say("keeper aim: the leak guard flags a test-home keeper on the live server, leaves "
        "another root's and a default-root keeper alone, and reports one it could not "
        "read: ok")

    # ---- the same audit as a WATCH, which is the only way either failure is ever heard
    #      outside a terminal: an orphan (a role process running with no claim naming it)
    #      and a broken tie (a free file whose record names a live pid) are both things that
    #      HAPPEN, and a snapshot taken at the wrong moment says nothing about either. The
    #      unit is a keyed SET, so a watch can tell a new finding from a standing one, and
    #      a resolution from silence.
    watch_rows = [
        ("keeper", {"path": "/p/fbtodo-pane-keeper.pid", "state": "absent", "pid": None,
                    "pid_alive": False, "leftover": False, "name_inode": None}),
        ("watcher", {"path": "/p/fbtodo-daemon.pid", "state": "free", "pid": os.getpid(),
                     "pid_alive": True, "leftover": True, "name_inode": "mismatch"}),
        ("keeper", {"path": "/p/fbtodo-pane-keeper.pid", "state": "free", "pid": 1,
                    "pid_alive": False, "leftover": True, "name_inode": "mismatch"}),
        ("legacy keeper", {"path": "/old/fbtodo-pane-keeper.pid", "state": "held", "pid": 900,
                           "pid_alive": True, "leftover": False, "name_inode": "match"}),
    ]
    watch_procs = [dict(by_pid[901], role="keeper", age_s=400)]
    found = module.locks_findings(watch_rows, watch_procs)
    assert set(found) == {f"tie:watcher:{os.getpid()}", "orphan:keeper:901"}, found
    assert found[f"tie:watcher:{os.getpid()}"]["kind"] == "tie", found
    assert found["orphan:keeper:901"]["kind"] == "orphan", found
    assert found["orphan:keeper:901"]["path"] == "/p/fbtodo-pane-keeper.pid", found
    assert "pid 901" in module.locks_watch_line("open", found["orphan:keeper:901"])
    assert "no claim names it" in module.locks_watch_line("open", found["orphan:keeper:901"])
    assert "a claim names it again" in module.locks_watch_line("clear", found["orphan:keeper:901"])
    assert "the name and the inode parted" in \
        module.locks_watch_line("open", found[f"tie:watcher:{os.getpid()}"]), found
    # ...and the two states that are NOT findings: a dead pid in a free record (the next ask
    # clears it, and it is not a live tie), and a held claim that names its own holder.
    # The keeper's own row above is that dead-pid case.
    assert not [k for k in found if k.startswith("tie:keeper")], "a dead leftover was a tie"
    assert not [k for k in found if k.startswith("tie:legacy")], found
    # ...and a watch does not report itself: this process is a live pid too.
    self_row = module.locks_findings(watch_rows, [dict(by_pid[901], pid=os.getpid(),
                                                       role="keeper", age_s=400)])
    assert not [k for k in self_row if k.startswith("orphan:")], self_row
    say("locks watch: an orphan and a broken tie are the findings, keyed by the thing "
        "itself — a dead leftover, a held claim that names its holder, and this process "
        "are not: ok")

    # ---- ...and the watch on a real root. A tie is seeded by hand — a FREE claim file whose
    #      record names a live pid, which is exactly what a replaced file looks like from
    #      outside — then `locks --watch` must announce it, ask the kit once, and announce it
    #      cleared once the tie is whole again.
    saved_lock = None
    if os.path.exists(lock_path):
        with open(lock_path, "rb") as fh:
            saved_lock = fh.read()
    asked = os.path.join(TEST_HOME, "locks-bell-asked.log")
    stub_bell = os.path.join(TEST_HOME, "locks-bell")
    with open(stub_bell, "w") as fh:
        fh.write(f"#!/bin/sh\necho \"$*\" >> {asked}\n")
    os.chmod(stub_bell, 0o755)
    module.atomic_write_json(lock_path, {"pid": os.getpid(), "cwd": CWD, "started_ms": 0,
                                         "instance_pid": None, "version": module.VERSION})
    watch_log = os.path.join(TEST_HOME, "locks-watch.jsonl")

    def watch_events() -> list:
        try:
            with open(watch_log) as fh:
                return [json.loads(line) for line in fh if line.strip()]
        except (OSError, ValueError):
            return []

    # The watch is told to poll every 6 s, and the check below waits for TWO of its events
    # (`open`, then `clear`), so the bound is its own interval rather than a fixed 20 s that
    # only holds on an idle machine (measured: this check failing under load, with the events
    # arriving a tick or two after the bound did).
    WATCH_I = 6
    watch_bound = poll_bound(WATCH_I, ticks=3)
    try:
        with open(watch_log, "w") as handle:
            watcher = subprocess.Popen(
                [sys.executable, FB, "locks", "--watch", "--json", "-i", str(WATCH_I)],
                env={**env, "FBTODO_LOCKS_BELL": stub_bell}, stdout=handle,
                stderr=subprocess.DEVNULL, cwd=CWD,
            )
            try:
                key = f"tie:watcher:{os.getpid()}"
                assert soon(lambda: any(e["event"] == "open" and e["key"] == key
                                        for e in watch_events()), watch_bound), watch_events()
                opened = next(e for e in watch_events() if e["key"] == key and
                              e["event"] == "open")
                assert opened["kind"] == "tie" and opened["pid"] == os.getpid(), opened
                assert isinstance(opened["at_ms"], int), opened
                assert soon(lambda: os.path.exists(asked), watch_bound), \
                    "the watch never asked the kit"
                os.unlink(lock_path)  # the tie is whole again: no file, no tie
                assert soon(lambda: any(e["event"] == "clear" and e["key"] == key
                                        for e in watch_events()), watch_bound), watch_events()
                # ...and the SEQUENCE, not the tally: the finding is announced once when it
                # appears and once when it is gone, in that order. Compared as a list of the
                # events this key ever had, so a re-announcement on a later tick is caught
                # while an event about some other finding (or an extra tick's silence) is not
                # mistaken for one.
                time.sleep(WATCH_I / 2)
                seq = [e["event"] for e in watch_events() if e["key"] == key]
                assert seq[0] == "open" and seq[-1] == "clear" and set(seq) <= {"open", "clear"}, (
                    seq)
                with open(asked) as fh:
                    asks = fh.read().splitlines()
                assert asks and len(asks) == 1, asks  # once per new finding, not per tick
                assert asks[0].strip() == "--quiet", asks
            finally:
                watcher.terminate()
                watcher.wait(timeout=15)
        say("locks --watch: a seeded tie is announced as it appears, the kit is asked once "
            "about it, and the resolution is announced too: ok")
        # ...and the kit's own reading of the audit is the same reading: the bell is loaded
        # from the repository by path and asked about the same document, and the findings it
        # sees are the findings the watch would print. Two implementations, one document.
        module.atomic_write_json(lock_path, {"pid": os.getpid(), "cwd": CWD, "started_ms": 0,
                                             "instance_pid": None, "version": module.VERSION})
        bell = load_module(os.path.join(ROOT, "scripts", "notify", "locks-bell.py"),
                           "locks_bell")
        doc = json.loads(run("locks", "--json").stdout)
        theirs = bell.findings(doc)
        mine = module.locks_findings(
            [(label, module.claim_audit(path)) for label, path in module.claim_files()],
            module.claim_processes())
        assert set(theirs) == set(mine), (sorted(theirs), sorted(mine))
        assert f"tie:watcher:{os.getpid()}" in theirs, theirs
        say("locks watch: the bell and the watcher read one audit document the same way — "
            "the seeded tie is a tie to both: ok")
    finally:
        if os.path.exists(lock_path):
            os.unlink(lock_path)
        if saved_lock is not None:
            with open(lock_path, "wb") as fh:
                fh.write(saved_lock)

    # ---- the claim crosses the watcher's own self-reload. The watcher re-execs itself when
    #      the build under it changes (the row after this one), and the claim has to cross
    #      that exec or a second watcher could take it in the gap: an exec keeps the pid and
    #      the descriptor table, so `lock_handoff` marks the locked fd inheritable and names
    #      it in the environment, and `lock_adopt` takes it back on the other side. Which of
    #      that is true is the kernel's to say, not this file's, so the proof is a REAL exec
    #      — and a second run WITHOUT the hand-over, where the same probe loses the claim,
    #      because Python's descriptors are close-on-exec: that is what the machinery is for,
    #      and a build that dropped it would fail the first run and pass the second.
    probe = os.path.join(TEST_HOME, "reload-claim-probe.py")
    with open(probe, "w") as fh:
        fh.write(
            "import json, os, sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from fbtodo.locks import lock_adopt, lock_handoff, lock_holder, lock_ours, write_lock\n"
            "lib, path, mode, cwd = sys.argv[1:5]\n"
            "if mode != 'adopt':\n"
            "    assert write_lock(cwd, None, path=path), 'the probe could not claim'\n"
            "    if mode == 'handoff':\n"
            "        fd = lock_handoff(path)\n"
            "        assert fd is not None and os.get_inheritable(fd), 'the claim cannot exec'\n"
            "    os.execv(sys.executable, [sys.executable, __file__, lib, path, 'adopt', cwd,\n"
            "                              str(os.getpid())])\n"
            "before = int(sys.argv[5])\n"
            "print(json.dumps({'adopted': lock_adopt(path), 'ours': lock_ours(path),\n"
            "                  'same_pid': os.getpid() == before,\n"
            "                  'holder': lock_holder(path), 'pid': os.getpid()}))\n"
        )
    carry_path = os.path.join(TEST_HOME, "reload-claim.pid")
    for mode in ("handoff", "nohandoff"):
        ran = subprocess.run([sys.executable, probe, SRC, carry_path, mode, CWD],
                             capture_output=True, text=True, timeout=60)
        assert ran.returncode == 0, (mode, ran.returncode, ran.stdout, ran.stderr)
        answer = json.loads(ran.stdout.strip().splitlines()[-1])
        assert answer["same_pid"] is True, f"the exec did not keep the pid: {answer}"
        if mode == "handoff":
            assert answer["adopted"] is True and answer["ours"] is True, answer
            assert answer["holder"] == answer["pid"], \
                f"the handed-over claim is not held after the exec: {answer}"
        else:
            assert answer["adopted"] is False and answer["ours"] is False, answer
            assert answer["holder"] is None, f"a claim crossed an exec with no hand-over: {answer}"
    # ...and a hand-over that is not this process's own is never believed: a stale variable,
    # a failed exec's leftover, or a number somebody typed must not hand out a claim.
    for junk in ("0:0", f"{os.getpid()}:0", "not-a-number", "3"):
        os.environ[module.LOCK_FD_ENV] = junk
        assert module.lock_adopt(carry_path) is False, f"a junk hand-over was believed: {junk}"
        assert module.LOCK_FD_ENV not in os.environ, f"{junk!r} was left in the environment"
    for junk_path in (probe, carry_path):
        if os.path.exists(junk_path):
            os.remove(junk_path)
    say("reload: the claim survives the watcher's own exec, and only through a hand-over: ok")

    # ---- the watcher starts itself over when the build under it changes, and the claim goes
    #      through the exec with it. The pane's own row asked the question; this drives the
    #      answer through a real watcher: a COPY of the build (never this checkout — touching
    #      its sources would reload the owner's own pane and watcher), a source written after
    #      it started, and the watcher must come back as the SAME pid, still holding its
    #      claim and still writing state, with the reload in its log. A source that does not
    #      parse must HOLD instead: a watcher that exec'd into a half-written file would die
    #      in the middle of the save that is replacing it.
    build = os.path.join(TEST_HOME, "reload-build")
    shutil.rmtree(build, ignore_errors=True)
    os.makedirs(build)
    shutil.copytree(os.path.join(ROOT, "src", "fbtodo"), os.path.join(build, "src", "fbtodo"))
    shutil.copy(os.path.join(ROOT, "fbtodo"), os.path.join(build, "fbtodo"))
    reload_src = os.path.join(build, "src", "fbtodo")
    victim_rl = spawn_quiet("sleep", "600")
    repl = subprocess.Popen(
        [sys.executable, os.path.join(build, "fbtodo"), "daemon", "--foreground", "--quiet",
         "--cwd", CWD, "--instance-pid", str(victim_rl.pid), "-i", "0.2",
         "--ask-seconds", "0", "--pause-seconds", "0", "--pane-seconds", "0",
         "--pane-bell-seconds", "0", "--db", no_desk],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        start_new_session=True, env=env, cwd=CWD,
    )

    def beats() -> int:
        return int((module.read_json(module.STATE_PATH, {}) or {}).get("heartbeat_ms") or 0)

    def wait_for(pred, seconds: float = 25.0) -> bool:
        deadline = time.time() + seconds
        while time.time() < deadline and not pred():
            time.sleep(0.1)
        return pred()

    def stale_window_bound(minutes: float = 0.05, startup_s: float = 20.0) -> float:
        """How long a pane asked to close on a STALE window may be given to do it.

        The window is the subject's own clock and the test sets it: `--stale-after 0.05` is
        three seconds, and the pane has to start, find its source, notice the store has
        stopped moving, and exit. A flat `+ 25` cannot say whether it is slack or luck — it
        was the same number on both sides of a check that waits for the reader's window and
        one that waits for a process to settle, and only one of them had a window at all.
        """
        return minutes * 60.0 + startup_s

    def log_text() -> str:
        try:
            with open(module.LOG_PATH) as fh:
                return fh.read()
        except OSError:
            return ""

    # Every reload below is counted from HERE, not from zero. The log is this run's, so it can
    # already hold reloads from an earlier phase (or from a watcher this check did not start),
    # and an absolute `== 2` is then waiting for a number that will never arrive — a check
    # that fails on the state of the world rather than on the behaviour. Relative, it asserts
    # what it means to: one more reload happened, and none happened while the tree was broken.
    reloaded = lambda: log_text().count("watcher reloading:")  # noqa: E731
    reloads_at_start = reloaded()

    try:
        deadline = time.time() + 10
        while time.time() < deadline and not os.path.exists(lock_path):
            time.sleep(0.1)
        assert module.lock_holder(lock_path) == repl.pid, "the copied watcher never claimed"
        beat = beats()
        assert wait_for(lambda: beats() > beat), "the copied watcher never wrote state"
        # a source newer than the watcher landed: it must start itself over, keeping both the
        # pid and the claim (a stand-down here would leave no watcher at all)
        with open(os.path.join(reload_src, "zz_reload.py"), "w") as fh:
            fh.write("x = 1\n")
        assert wait_for(lambda: "watcher reloading:" in log_text()), log_text()[-400:]
        assert reloaded() >= reloads_at_start + 1, log_text()[-400:]
        beat = beats()
        assert repl.poll() is None and os.path.exists(lock_path), "the reload took it down"
        assert module.lock_holder(lock_path) == repl.pid, "the claim changed hands across a reload"
        assert wait_for(lambda: beats() > beat), "the reloaded watcher is not serving"
        # a source that does not parse: hold, and do not exec into it
        with open(os.path.join(reload_src, "zz_broken.py"), "w") as fh:
            fh.write("def half(\n")
        assert wait_for(lambda: "watcher holding" in log_text()), log_text()[-400:]
        assert quiet_for(lambda: reloaded() != reloads_at_start + 1, hold_window()), (
            "it reloaded into a broken file")
        assert repl.poll() is None, "the watcher died on a source that does not parse"
        assert module.lock_holder(lock_path) == repl.pid, "the claim moved on a held reload"
        os.remove(os.path.join(reload_src, "zz_broken.py"))
        with open(os.path.join(reload_src, "zz_reload2.py"), "w") as fh:
            fh.write("y = 2\n")
        assert wait_for(lambda: reloaded() >= reloads_at_start + 2), log_text()[-400:]
        assert module.lock_holder(lock_path) == repl.pid and repl.poll() is None
        beat = beats()
        assert wait_for(lambda: beats() > beat), "the second reload left nobody serving"
        # ...and a copy whose launcher is gone still reloads, through `self_argv`'s fallback to
        # the package's own entry file — the very path a launcher-less copy runs, guard and all
        os.rename(os.path.join(build, "fbtodo"), os.path.join(build, "fbtodo.gone"))
        with open(os.path.join(reload_src, "zz_reload3.py"), "w") as fh:
            fh.write("z = 3\n")
        assert wait_for(lambda: reloaded() >= reloads_at_start + 3), log_text()[-400:]
        assert repl.poll() is None and module.lock_holder(lock_path) == repl.pid
        beat = beats()
        assert wait_for(lambda: beats() > beat), "the launcher-less reload left nobody serving"
        # ...and a tree that PARSES but will not BECOME a process must never be executed into.
        # `source_syntax_error` is silent about it — every file still compiles — while the
        # import dies, so the probe (`reload_probe_error`) is what holds it. A raise at module
        # scope in a module the package actually loads is the shape: without the probe the
        # exec would replace a running watcher with one that never starts.
        render_copy = os.path.join(reload_src, "render.py")
        with open(render_copy) as fh:
            render_orig = fh.read()
        with open(render_copy, "a") as fh:
            fh.write("\nraise RuntimeError('internally inconsistent')\n")
        with open(os.path.join(reload_src, "zz_reload4.py"), "w") as fh:
            fh.write("w = 4\n")
        assert wait_for(lambda: "does not load" in log_text()), log_text()[-500:]
        assert module.source_syntax_error(here=render_copy) is None, \
            "the unloadable tree does not parse cleanly, so the probe was not what held it"
        held_at = reloaded()
        assert quiet_for(lambda: reloaded() != held_at, hold_window()), (
            "it reloaded into an unfit build")
        assert repl.poll() is None, "the watcher exec'd into a build that will not load"
        assert module.lock_holder(lock_path) == repl.pid, "the claim moved on a held reload"
        assert reloaded() == reloads_at_start + 3, "it reloaded into an unfit build"
        # ...and once the tree is coherent again, the SAME watcher reloads into it — the hold
        # is a wait, not a surrender.
        with open(render_copy, "w") as fh:
            fh.write(render_orig)
        assert wait_for(lambda: reloaded() >= reloads_at_start + 4), log_text()[-500:]
        assert repl.poll() is None and module.lock_holder(lock_path) == repl.pid
        beat = beats()
        assert wait_for(lambda: beats() > beat), "the recovered reload left nobody serving"
        say("reload: the watcher replaces itself across an exec — claim, pid and heartbeat — "
            "holds while the sources do not parse OR the new build will not load, and "
            "reloads through `self_argv`'s fallback: ok")
    finally:
        repl.send_signal(signal.SIGTERM)
        deadline = time.time() + 10
        while time.time() < deadline and repl.poll() is None:
            time.sleep(0.1)
        kill_tree(repl)
        kill_tree(victim_rl)
        deadline = time.time() + 10
        while time.time() < deadline and module.lock_holder(lock_path):
            time.sleep(0.1)
        shutil.rmtree(build, ignore_errors=True)

    # ---- discovery, including the platform this machine is not: /proc answers first where
    #      it exists, lsof covers the rest, and the machine's own answer still works
    victim_cwd = os.path.join(TEST_HOME, "victimcwd")
    os.makedirs(victim_cwd, exist_ok=True)
    proc_fixture = os.path.join(TEST_HOME, "proc")
    os.makedirs(os.path.join(proc_fixture, "4242"), exist_ok=True)
    os.symlink(victim_cwd, os.path.join(proc_fixture, "4242", "cwd"))
    saved_proc = module.PROC_ROOT
    set_knob(module, "PROC_ROOT", proc_fixture)
    try:
        assert module.cwds_for([4242]) == {4242: victim_cwd}, module.cwds_for([4242])
        assert module.pid_cwd(4242) == victim_cwd
        # a pid /proc cannot answer for is left to the fallback, not guessed at
        assert module.cwds_for([4242, 999_999]).get(4242) == victim_cwd
    finally:
        set_knob(module, "PROC_ROOT", saved_proc)
    me = os.path.realpath(module.pid_cwd(os.getpid()) or "")
    assert me == os.path.realpath(os.getcwd()), (me, os.getcwd())
    say("discovery: /proc answers first, and the real root still resolves this process: ok")

    # ...and the shapes argv can take. A bare `freebuff` is how a PATH lookup is spelled in
    # argv — invisible to the old absolute-path rule — and a token that RESOLVES to a
    # `bin/freebuff` is the same CLI installed elsewhere. A probe whose ARGUMENT happens to
    # name the path is not the CLI, however it is worded.
    launcher = os.path.join(TEST_HOME, "freebuff-relocated")
    target = os.path.join(TEST_HOME, "bin", "freebuff")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    open(target, "w").close()
    os.symlink(target, launcher)
    saved_which = list(module._FREEBUFF_WHICH)
    module._FREEBUFF_WHICH[:] = ["/somewhere/else/bin/freebuff"]  # a PATH answer exists
    try:
        assert module.is_freebuff_cmd("/opt/homebrew/bin/freebuff")
        assert module.is_freebuff_cmd("./bin/freebuff")
        assert module.is_freebuff_cmd("~/bin/freebuff")
        assert module.is_freebuff_cmd("freebuff")
        assert module.is_freebuff_cmd(launcher + " --cli")
        # the OLD rule is a subset of this one: everything it matched, this matches
        for old in ("/x/bin/freebuff", "/opt/a/b/bin/freebuff --flag"):
            assert module.is_freebuff_cmd(old), old
        # ...and none of these is the CLI
        for other in ("grep bin/freebuff", "python3 -c print('/x/bin/freebuff')",
                      "/usr/bin/grep freebuff", "sleep 600", "node /apps/freebuff-ui"):
            assert not module.is_freebuff_cmd(other), other
    finally:
        module._FREEBUFF_WHICH[:] = saved_which
    module._FREEBUFF_WHICH[:] = [None]  # ...with nothing on PATH a bare name is not the CLI
    assert not module.is_freebuff_cmd("freebuff"), "a bare name matched with no freebuff"
    module._FREEBUFF_WHICH[:] = saved_which
    say("discovery: a launcher's argv is matched by shape, and a probe that names it is not: ok")

    # ---- the package is several modules now, and `import *` is what keeps it one namespace. A
    #      module that reads a global nothing under `fbtodo/` provides would only fail when that
    #      line runs — for a leaf helper, days later, in the pane. So read the package statically:
    #      every name a module's functions read must come from that module, from a module it
    #      star-imports (transitively), or from the builtins. This is what a dropped import or a
    #      module that forgot a layer looks like before anything runs it.
    pkg_dir = os.path.dirname(os.path.abspath(module.__file__))
    trees = {}
    for _name in sorted(os.listdir(pkg_dir)):
        if _name.endswith(".py"):
            with open(os.path.join(pkg_dir, _name), encoding="utf-8") as _fh:
                trees[_name[:-3]] = ast.parse(_fh.read())

    def _star_deps(mod_name) -> list:
        return [n.module.split(".")[-1] for n in trees[mod_name].body
                if isinstance(n, ast.ImportFrom) and n.module
                and any(a.name == "*" for a in n.names)]

    def _names(mod_name) -> set:
        """The module-level names one file binds itself."""
        names = set()
        for node in trees[mod_name].body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = getattr(node, "targets", None) or [node.target]
                names |= {x.id for t in targets for x in ast.walk(t) if isinstance(x, ast.Name)}
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                names |= {(a.asname or a.name.split(".")[0]) for a in node.names if a.name != "*"}
        return names

    def _exports(mod_name, seen=frozenset()) -> set:
        """What `from .mod import *` hands out: its `__all__`, or its names and its layers'."""
        if mod_name in seen or mod_name not in trees:
            return set()
        for node in trees[mod_name].body:
            if isinstance(node, ast.Assign) and any(
                    getattr(t, "id", "") == "__all__" for t in node.targets):
                return set(ast.literal_eval(node.value))
        names = _names(mod_name)
        for dep in _star_deps(mod_name):
            names |= _exports(dep, seen | {mod_name})
        return names

    def _available(mod_name, seen=frozenset()) -> set:
        """Everything one module's own code may read: its names, plus its star-imports'."""
        if mod_name in seen or mod_name not in trees:
            return set()
        names = _names(mod_name)
        for dep in _star_deps(mod_name):
            names |= _exports(dep, seen | {mod_name})
        return names

    for _mod, _tree in trees.items():
        bound = set(_available(_mod))
        for _node in ast.walk(_tree):
            if isinstance(_node, ast.Name) and isinstance(_node.ctx, ast.Store):
                bound.add(_node.id)  # a local counts as bound, wherever it is bound
            elif isinstance(_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                bound.add(_node.name)
            if isinstance(_node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                args = [*_node.args.args, *_node.args.kwonlyargs,
                        *getattr(_node.args, "posonlyargs", [])]
                bound |= {a.arg for a in args}
                # `*args`/`**kwargs` bind their names too — a variadic helper's body reads
                # them like any other parameter, and leaving them out reported a name
                # nothing provided (measured on `_joined(*blobs)` in locks.py).
                for _var in (_node.args.vararg, _node.args.kwarg):
                    if _var is not None:
                        bound.add(_var.arg)
            elif isinstance(_node, ast.ExceptHandler) and _node.name:
                bound.add(_node.name)
            elif isinstance(_node, (ast.Import, ast.ImportFrom)):
                bound |= {(a.asname or a.name.split(".")[0]) for a in _node.names if a.name != "*"}
        unread = sorted(
            {n.id for n in ast.walk(_tree)
             if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)} - bound
            - set(dir(builtins)) - {"__file__", "__name__", "__doc__", "__package__"},
        )
        assert not unread, f"{_mod}.py reads names nothing provides: {unread}"
    say("the package: every module's globals are provided for by its own layers: ok")

    # ---- ...and no module may CAPTURE one of the state paths at import time. `base` picks the
    #      root at import (`SCRATCH` and the paths derived from it), and `init_state_root` may
    #      move it by the time the first command runs, so a function default or a module-level
    #      expression that reads one of these names freezes the path this process started with —
    #      the pane and the daemon would then disagree about where the state lives. Only `base`,
    #      which owns them, may hold them.
    state_path_names = {
        "SCRATCH", "STATE_PATH", "TASKS_PATH", "LOCK_PATH", "LOG_PATH",
        "PANE_KEEPER_PATH", "PANE_LOG_PATH", "PINS_PATH", "LAST_PATH",
    }

    def _import_time_names(node) -> list:
        """The `Load` names a top-level statement reads when the module is imported.

        Descends through everything a `def`/`class` evaluates immediately — its defaults,
        decorators and bases (and, for a class, its body) — but not into a function body,
        which runs later, when the name is read afresh from `base`.
        """
        found = []

        def walk(n):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                for d in [*n.args.defaults, *[x for x in n.args.kw_defaults if x]]:
                    walk(d)
                for dec in n.decorator_list:
                    walk(dec)
                return
            if isinstance(n, ast.ClassDef):
                for dec in n.decorator_list:
                    walk(dec)
                for base in n.bases:
                    walk(base)
                for kw in n.keywords:
                    walk(kw.value)
                for stmt in n.body:      # a class body runs at import too
                    walk(stmt)
                return
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                found.append(n.id)
            for child in ast.iter_child_nodes(n):
                walk(child)

        walk(node)
        return found

    for _mod, _tree in trees.items():
        if _mod == "base":
            continue
        captured = set()
        # a default is evaluated when its `def` runs — at import for a module-level def — so ANY
        # default that reads a path name freezes it, however deeply the def is nested
        for _node in ast.walk(_tree):
            if isinstance(_node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                for _d in [*_node.args.defaults, *[x for x in _node.args.kw_defaults if x]]:
                    captured |= {n.id for n in ast.walk(_d) if isinstance(n, ast.Name)}
        for _stmt in _tree.body:
            captured |= set(_import_time_names(_stmt))
        captured &= state_path_names
        assert not captured, (
            f"{_mod}.py captures a state path at import: {sorted(captured)} — read it through "
            "base at call time, not into a default or a module global"
        )
    say("the package: no module freezes a state path into a default or a global: ok")

    # ---- how the program names itself back to itself. Every pane, the keeper and the daemon
    #      are re-invocations, so this must name something that RUNS. That is the launcher
    #      beside the package; a copy carrying the package without one has to fall back to the
    #      package's own entry file — the guard then re-enters the package, where today it would
    #      start a second copy of everything as `__main__` beside the package.
    argv = module.self_argv()
    assert argv[0] == sys.executable and os.path.basename(argv[1]) == "fbtodo", argv
    ver = subprocess.run([*argv, "--version"], capture_output=True, text=True, timeout=60)
    assert ver.returncode == 0 and ver.stdout.strip() == module.VERSION, (ver.returncode,
                                                                         ver.stdout, ver.stderr)
    copy_root = os.path.join(TEST_HOME, "copy-without-launcher")
    assert module.self_argv(here=os.path.join(copy_root, "src", "fbtodo", "base.py")) == [
        sys.executable, os.path.join(copy_root, "src", "fbtodo", "__init__.py")], \
        "the no-launcher fallback names the package's entry, not the file that asked"
    say("argv: the re-invocations name the launcher, or the package when there is none: ok")

    # ---- the pane's own staleness, asked by the pane about itself. A pane keeps the build it
    #      imported, so an upgrade used to leave the list drawn by the old one until somebody
    #      respawned the pane by hand; `source_newer_than` is the question that lets it start
    #      itself over. Three answers matter: a source written AFTER the start counts (that is
    #      code this process is not running, whatever `VERSION` says), a write still landing
    #      does NOT (an editor halfway through a save must not be exec'd into), and a file that
    #      will not parse is a reason to WAIT rather than restart — a pane dies on the import,
    #      and it would die in front of the person editing it.
    src_copy = os.path.join(TEST_HOME, "source-newer", "src", "fbtodo")
    shutil.rmtree(os.path.dirname(os.path.dirname(src_copy)), ignore_errors=True)
    os.makedirs(src_copy)
    older = os.path.join(src_copy, "older.py")
    newer = os.path.join(src_copy, "newer.py")
    for path, age in ((older, 3600), (newer, 10)):
        with open(path, "w") as fh:
            fh.write("x = 1\n")
        os.utime(path, (time.time() - age, time.time() - age))
    here = os.path.join(src_copy, "base.py")
    started = time.time() - 60
    assert module.source_newer_than(started, settle=0, here=here) == newer, \
        "the newest source written after the start is the answer, not the first one found"
    assert module.source_newer_than(time.time(), settle=0, here=here) is None, \
        "nothing written after the start means this process IS the build on disk"
    assert module.source_newer_than(started, settle=60, here=here) is None, \
        "a write inside the settle window is not believed yet: ask again, do not restart"
    assert module.source_newer_than(started, settle=0,
                                    here=os.path.join(TEST_HOME, "no-such", "base.py")) is None, \
        "a source tree that cannot be read is not a reason to rebuild"
    assert module.source_syntax_error(here=here) is None, "two one-line files parse"
    broken_src = os.path.join(src_copy, "broken.py")
    with open(broken_src, "w") as fh:
        fh.write("def half(\n")
    why = module.source_syntax_error(here=here)
    assert why and broken_src in why and "line" in why, why
    os.remove(broken_src)
    assert module.source_syntax_error(here=here) is None, "and it parses again once the file goes"
    say("source staleness: newer than the start counts, a save still landing waits, and a file "
        "that will not parse is a reason to wait rather than restart: ok")

    # ---- and a tree that PARSES can still fail to BECOME a process. The self-reload does not
    #      re-import, it EXECS, so a build that dies at import would replace a running image
    #      with a corpse — a watcher or keeper gone, a pane respawned into the same broken
    #      tree. The probe runs the same command line in a child and asks only whether it
    #      loads, so the caller can HOLD instead.
    assert module.reload_probe_error() is None, "the probe rejected the checkout it runs from"
    assert module.reload_probe_error(timeout=0.001) is not None, \
        "a probe that could not finish was read as a yes"
    probe_build = os.path.join(TEST_HOME, "probe-build")
    shutil.rmtree(probe_build, ignore_errors=True)
    os.makedirs(probe_build)
    shutil.copytree(os.path.join(ROOT, "src", "fbtodo"),
                    os.path.join(probe_build, "src", "fbtodo"))
    shutil.copy(os.path.join(ROOT, "fbtodo"), os.path.join(probe_build, "fbtodo"))
    probe_base = os.path.join(probe_build, "src", "fbtodo", "base.py")
    with open(os.path.join(probe_build, "src", "fbtodo", "render.py"), "a") as fh:
        fh.write("\nraise RuntimeError('internally inconsistent')\n")
    assert module.source_syntax_error(here=probe_base) is None, \
        "the probe build does not parse, so this would test the parser instead"
    bad = module.reload_probe_error(here=probe_base)
    assert bad and "internally inconsistent" in bad, bad
    shutil.rmtree(probe_build, ignore_errors=True)
    say("the reload probe: a tree that parses but cannot import is a reason to HOLD, not a "
        "build to exec into: ok")

    # ---- ...and a tree that IMPORTS cleanly can still hide a name a FUNCTION uses. A function
    #      body is name-resolved only when it runs, so a global that was renamed or deleted
    #      waits for the one command that reaches that line — and by then the reload has
    #      already replaced a working image with it. The probe now exercises the entry points
    #      it safely can (the command parser is built) and then resolves every global the
    #      package's functions LOAD, so a name used only inside a command that is never called
    #      still fails the pre-flight. The bait is a function nobody calls, naming a global
    #      that does not exist; the control is the same shape with the name actually defined.
    name_build = os.path.join(TEST_HOME, "probe-name-build")
    shutil.rmtree(name_build, ignore_errors=True)
    os.makedirs(name_build)
    shutil.copytree(os.path.join(ROOT, "src", "fbtodo"),
                    os.path.join(name_build, "src", "fbtodo"))
    shutil.copy(os.path.join(ROOT, "fbtodo"), os.path.join(name_build, "fbtodo"))
    name_base = os.path.join(name_build, "src", "fbtodo", "base.py")
    with open(name_base, "a", encoding="utf-8") as fh:
        fh.write("\n_PROBE_PRESENT = 1\n\ndef _probe_fine():\n    return _PROBE_PRESENT\n")
    assert module.reload_probe_error(here=name_base) is None, \
        "a global that IS defined, used only inside a function, was read as missing"
    with open(name_base, "a", encoding="utf-8") as fh:
        fh.write("\ndef _probe_bait():\n    return FBTODO_PROBE_MISSING_NAME\n")
    assert module.source_syntax_error(here=name_base) is None, \
        "the bait tree does not parse, so this would test the parser instead"
    named = module.reload_probe_error(here=name_base)
    assert named and "FBTODO_PROBE_MISSING_NAME" in named and "_probe_bait" in named, named
    shutil.rmtree(name_build, ignore_errors=True)
    # Read from a SNAPSHOT of the tree, not from the checkout the suite happens to be run
    # in: this parses every source file, and anything writing the checkout while the suite
    # runs — an agent turn in this very repo, an IDE save — can leave a half-written file
    # under the reader, which then fails a check about the build rather than about the code
    # (measured 2026-10-04: `the checkout names everything it uses` failing on a tree that
    # named everything a minute earlier). The names are still resolved against the live
    # namespaces, so the question is still "does the code we RUN name everything it uses".
    globals_snap = os.path.join(TEST_HOME, "globals-snap")
    shutil.rmtree(globals_snap, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, "src", "fbtodo"), os.path.join(globals_snap, "fbtodo"))
    snap_error = module.undefined_global_names(
        here=os.path.join(globals_snap, "fbtodo", "base.py"))
    assert snap_error is None, f"the checkout names everything it uses: {snap_error}"
    assert module.undefined_global_names() is None, (
        "the build that is running names everything it uses")
    say("the reload probe: a function body's globals are resolved too — a name used only "
        "inside a command that was never called still fails the pre-flight: ok")

    # ---- ...and a tree whose names all resolve can still contain code its own SOURCE proves
    #      will never run: a statement after a `return`, a whole `if False:` branch, a body
    #      under `while False:`. That is invisible to an import — it is valid Python and it
    #      compiles — so nothing else notices that a path the source describes never happens.
    #      The probe answers it from the parse too, and only from certainties; the controls
    #      are the LIVE shapes (`while True:` with a `break`, an `if x:`), so the check cannot
    #      pass by calling live code dead. The bait is chosen so the failure is the dead part,
    #      not a missing name: `_PROBE_NEVER` is assigned, just never reached.
    dead_build = os.path.join(TEST_HOME, "probe-dead-build")
    shutil.rmtree(dead_build, ignore_errors=True)
    os.makedirs(dead_build)
    shutil.copytree(os.path.join(ROOT, "src", "fbtodo"),
                    os.path.join(dead_build, "src", "fbtodo"))
    shutil.copy(os.path.join(ROOT, "fbtodo"), os.path.join(dead_build, "fbtodo"))
    dead_base = os.path.join(dead_build, "src", "fbtodo", "base.py")
    with open(dead_base, "a", encoding="utf-8") as fh:
        fh.write("\ndef _probe_live(x):\n    while True:\n        if x:\n            return 1\n"
                 "        break\n    return 2\n")
    assert module.reload_probe_error(here=dead_base) is None, \
        "a live loop and a live branch were read as code that can never run"
    with open(dead_base, "a", encoding="utf-8") as fh:
        fh.write("\ndef _probe_dead():\n    return 1\n    _PROBE_NEVER = 2\n")
    assert module.source_syntax_error(here=dead_base) is None, \
        "the bait tree does not parse, so this would test the parser instead"
    held = module.reload_probe_error(here=dead_base)
    assert held and "unreachable" in held and "after the return" in held, held
    shutil.copy(os.path.join(ROOT, "src", "fbtodo", "base.py"), dead_base)
    with open(dead_base, "a", encoding="utf-8") as fh:
        fh.write("\ndef _probe_branch(x):\n    if False:\n        return x\n    return 3\n")
    assert module.source_syntax_error(here=dead_base) is None, \
        "the branch bait does not parse, so this would test the parser instead"
    held = module.reload_probe_error(here=dead_base)
    assert held and "unreachable" in held and "constant-false" in held, held

    #      ...including a test that is a COMPARISON of literals, which the parse can fold
    #      (`if 1 > 2:`, `while 0 == 1:`, a chained `0 < 1 < 0`) — and only when every
    #      operand is a literal. A comparison against a NAME, an `in` that cannot be made
    #      (`1 in 2`), and `is`/`is not` (identity is not a value question) are left alone;
    #      the controls must pass, so folding can never call a live branch dead.
    shutil.copy(os.path.join(ROOT, "src", "fbtodo", "base.py"), dead_base)
    with open(dead_base, "a", encoding="utf-8") as fh:
        fh.write("\ndef _probe_cmp_live(x, y):\n    if x > 2:\n        return x\n"
                 "    if 1 < x:\n        return 1\n    if 1 in y:\n        return 3\n"
                 "    if 1 in 2:\n        return 4\n    return 0\n")
    assert module.reload_probe_error(here=dead_base) is None, \
        "a comparison against a name, or one that cannot be made, was read as a constant"
    with open(dead_base, "a", encoding="utf-8") as fh:
        fh.write("\ndef _probe_cmp_dead():\n    if 0 < 1 < 0:\n        return 1\n    return 0\n")
    held = module.reload_probe_error(here=dead_base)
    assert held and "unreachable" in held and "constant-false" in held, held

    #      ...and the parse reasons about a GUARD as well as a literal: inside one function
    #      `if P: <leaves>` means P is false for every line below it, so a later `if P:` can
    #      never run and a later `if not P:` can never take its `else`; an `assert P` makes
    #      the same promise by the other route, so P is already true below it. The fact is
    #      carried into any block the guard DOMINATES (a `with`/`if`/`try` body, a handler,
    #      and a loop body — but a loop gets only the facts its own assignments leave
    #      standing) and never across a scope. The promise is only made where it cannot be
    #      wrong — the guard must leave on every path, the test may read nothing but locals
    #      this function binds, and nothing between the two may rebind one of them — so the
    #      controls below are exactly those refusals: a rebinding, a module global, a closure
    #      cell, an attribute, a call, a guard that does not always leave, and a loop that
    #      assigns the name (so a later pass through the body could have moved it). A loop the
    #      body does NOT disturb is the opposite: a place the fact is carried into, and its
    #      own baits follow.
    shutil.copy(os.path.join(ROOT, "src", "fbtodo", "base.py"), dead_base)
    with open(dead_base, "a", encoding="utf-8") as fh:
        fh.write('''
_PROBE_MODE = None


def _probe_live_rebind(x):
    if x is None:
        return None
    x = 5
    if x is None:
        return 1
    return 0


def _probe_live_global():
    if _PROBE_MODE is None:
        return 2
    if _PROBE_MODE is None:
        return 3
    return 0


def _probe_live_attr(other):
    if other.h is None:
        return 4
    if other.h is None:
        return 5
    return 0


def _probe_live_call(y):
    if y.get() is None:
        return 6
    if y.get() is None:
        return 7
    return 0


def _probe_live_partial(x, y):
    if x is None:
        if y:
            return 8
    if x is None:
        return 9
    return 0


def _probe_live_closure(y):
    z = 1

    def _inner():
        if y is None:
            return 12
        if y is None:
            return 13

    return _inner, z


def _probe_live_assert_global():
    assert _PROBE_MODE is None
    if _PROBE_MODE is None:
        return 14
    return 0


def _probe_live_assert_attr(other):
    assert other.h is None
    if other.h is None:
        return 15
    return 0


def _probe_live_assert_call(y):
    assert y.get() is None
    if y.get() is None:
        return 16
    return 0


def _probe_live_assert_rebind(x):
    assert x is None
    x = 5
    if x is None:
        return 17
    return 0


def _probe_live_for_rebind(x, y):
    assert x is not None
    for _i in y:
        x = 5
        if x is None:
            return 18
    return 0


def _probe_live_for_target(x, y):
    assert x is not None
    for x in y:
        if x is None:
            return 19
    return 0
''')
    assert module.reload_probe_error(here=dead_base) is None, \
        "a guard's promise outlived a rebinding, a global, a closure, an attribute, a call, " \
        "or a loop that assigns the name"
    with open(dead_base, "a", encoding="utf-8") as fh:
        fh.write("\ndef _probe_guard_dead(x):\n    if x is None:\n        return None\n"
                 "    if x is None:\n        return 1\n    return 2\n")
    held = module.reload_probe_error(here=dead_base)
    assert held and "unreachable" in held and "guard on line" in held, held
    shutil.copy(os.path.join(ROOT, "src", "fbtodo", "base.py"), dead_base)
    with open(dead_base, "a", encoding="utf-8") as fh:
        fh.write("\ndef _probe_guard_neg(x):\n    if x is None:\n        return None\n"
                 "    if x is not None:\n        return 1\n    else:\n        return 2\n")
    held = module.reload_probe_error(here=dead_base)
    assert held and "unreachable" in held and "negated" in held, held
    #      The same fact is carried into a block the guard DOMINATES, so a third look at the
    #      test is caught there too — in a `with` body and in a `try` handler. What must NOT
    #      be caught is unchanged: the controls above already prove a rebinding, a global, an
    #      attribute, a call and a loop that assigns the name all stay silent.
    shutil.copy(os.path.join(ROOT, "src", "fbtodo", "base.py"), dead_base)
    with open(dead_base, "a", encoding="utf-8") as fh:
        fh.write("\ndef _probe_guard_nested(x):\n    if x is None:\n        return None\n"
                 "    with open('f') as h:\n        if x is None:\n            return 1\n"
                 "    return 2\n")
    held = module.reload_probe_error(here=dead_base)
    assert held and "unreachable" in held and "guard on line" in held, held
    shutil.copy(os.path.join(ROOT, "src", "fbtodo", "base.py"), dead_base)
    with open(dead_base, "a", encoding="utf-8") as fh:
        fh.write("\ndef _probe_guard_handler(x):\n    if x is None:\n        return None\n"
                 "    try:\n        pass\n    except ValueError:\n"
                 "        if x is None:\n            return 1\n    return 2\n")
    held = module.reload_probe_error(here=dead_base)
    assert held and "unreachable" in held and "guard on line" in held, held
    #      An `assert` makes the same promise by the other route: control continues past it
    #      only when its test held, so the same test is already true and its negation already
    #      false. Both arrows are proved, and each names the `assert` it came from.
    shutil.copy(os.path.join(ROOT, "src", "fbtodo", "base.py"), dead_base)
    with open(dead_base, "a", encoding="utf-8") as fh:
        fh.write("\ndef _probe_assert_dead(x):\n    assert x is not None\n"
                 "    if x is None:\n        return 1\n    return 0\n")
    held = module.reload_probe_error(here=dead_base)
    assert held and "unreachable" in held and "`assert`" in held \
        and "already false here" in held, held
    shutil.copy(os.path.join(ROOT, "src", "fbtodo", "base.py"), dead_base)
    with open(dead_base, "a", encoding="utf-8") as fh:
        fh.write("\ndef _probe_assert_same(x):\n    assert x is not None\n"
                 "    if x is not None:\n        return 1\n    else:\n        return 2\n")
    held = module.reload_probe_error(here=dead_base)
    assert held and "unreachable" in held and "`assert`" in held \
        and "already true here" in held, held
    #      A LOOP body is entered too, and only with the facts its own assignments leave
    #      standing: a test settled before the loop and repeated inside the body is caught,
    #      while one the body could have moved is not (the controls above). Both `for` and
    #      `while` are proved, and the `else` suite of a loop counts as well.
    shutil.copy(os.path.join(ROOT, "src", "fbtodo", "base.py"), dead_base)
    with open(dead_base, "a", encoding="utf-8") as fh:
        fh.write("\ndef _probe_for_dead(x, y):\n    assert x is not None\n"
                 "    for _i in y:\n        if x is None:\n            return 1\n    return 0\n")
    held = module.reload_probe_error(here=dead_base)
    assert held and "unreachable" in held and "`assert`" in held \
        and "already false here" in held, held
    shutil.copy(os.path.join(ROOT, "src", "fbtodo", "base.py"), dead_base)
    with open(dead_base, "a", encoding="utf-8") as fh:
        fh.write("\ndef _probe_while_dead(x, y):\n    if x is None:\n        return None\n"
                 "    while y:\n        if x is None:\n            return 1\n    return 2\n")
    held = module.reload_probe_error(here=dead_base)
    assert held and "unreachable" in held and "guard on line" in held, held
    #      ...and `fbtodo dead` is the SAME two passes with every finding kept, not only the
    #      first the probe refuses on. The build under test here already carries the `while`
    #      bait; one more stranded statement makes two, so the subcommand must name BOTH
    #      (not stop at one), end nonzero, and answer `--json` with the same rows — while
    #      this checkout itself has nothing to list, exactly as `unreachable_code` says.
    with open(dead_base, "a", encoding="utf-8") as fh:
        fh.write("\ndef _probe_dead_extra(x):\n    return 1\n    x = 2\n"
                 "    if x is None:\n        return 3\n")
    ran = subprocess.run(
        [sys.executable, os.path.join(dead_build, "fbtodo"), "dead", "--json"],
        capture_output=True, text=True, env=env, cwd=dead_build, timeout=30,
    )
    assert ran.returncode != 0, (ran.returncode, ran.stdout, ran.stderr)
    doc = json.loads(ran.stdout)
    assert doc["count"] >= 2 and len(doc["findings"]) == doc["count"], doc
    assert all({"path", "line", "why"} <= set(f) for f in doc["findings"]), doc
    assert sum("guard on line" in f["why"] for f in doc["findings"]) >= 1, doc
    assert sum("nothing runs after" in f["why"] for f in doc["findings"]) >= 1, doc
    assert module.dead_code() == [], module.dead_code()
    assert module.unreachable_code() is None, "the checkout runs everything it says it does"
    shutil.rmtree(dead_build, ignore_errors=True)
    say("the reload probe: code the parse proves can never run — a statement after a "
        "return, a constant-false branch, a comparison of literals, or a branch an earlier "
        "guard or `assert` rules out (inside a block it dominates, including a loop body it "
        "does not disturb) — is a reason to HOLD, while a comparison against a name, a "
        "rebinding, a loop that moves the name, or anything the guard's promise does not "
        "cover is not: ok")

    # ---- the pane's environment, pinned rather than inherited. tmux REBUILDS a pane's PATH,
    #      so a pane command resolved through it can come up on a different Python than the
    #      process that asked for the pane — measured 2026-10-01, a pane on `/usr/bin/python3`
    #      3.9.6 beside a watcher on Homebrew's 3.14. `pane_command` names the interpreter
    #      absolutely and hands the pane the opener's own PATH; `python_of`/`pane_python` are
    #      what let `fbtodo status` say which Python that turned out to be. The flags must
    #      survive the pin: the keeper finds a pane by reading them back out of the line.
    cmd = module.local_pane_command(4242)
    parts = shlex.split(cmd)
    assert parts[0] == "/usr/bin/env" and f"PATH={module.pinned_path()}" in parts, cmd
    assert module.self_argv()[0] in parts and module.self_argv()[1] in parts, cmd
    assert module.PANE_WATCH_RE.search(cmd).group(1) == "4242", cmd
    assert module.PANE_INSTANCE_RE.search(
        module.pane_command(["x", "--instance-of", "7"])).group(1) == "7"
    # The PIN's shape: the interpreter's own directory first (so anything the pane starts by
    # name — an `env python3` shebang under `scripts/notify/` — is the same Python the pane is
    # on), the opener's PATH kept behind it, and no directory listed twice. FBTODO_PATH
    # replaces that base, never the leading directory.
    here = os.path.dirname(sys.executable)
    pin = module.pinned_path().split(os.pathsep)
    assert pin[0] == here, pin[:3]
    assert pin.count(here) == 1, pin[:3]
    os.environ["FBTODO_PATH"] = os.pathsep.join([here, "/usr/bin", here])
    deduped = module.pinned_path().split(os.pathsep)
    assert deduped[0] == here and deduped.count(here) == 1, deduped[:3]
    os.environ["FBTODO_PATH"] = "/pinned/once"
    assert module.pinned_path() == os.pathsep.join([here, "/pinned/once"]), \
        "FBTODO_PATH replaces the base, and the interpreter still leads"
    del os.environ["FBTODO_PATH"]

    assert module.python_of("/opt/homebrew/bin/python3.14 /p/fbtodo --watch-pid 1") == \
        "/opt/homebrew/bin/python3.14"
    # the two spellings a pin takes, and the one answer that must NOT be given: a shell is not
    # a python, and saying it was one is what would make `status` lie about a pane
    assert module.python_of(
        "/usr/bin/env PATH=/usr/bin:/bin /a/Python.app/Contents/MacOS/Python /p/fbtodo") == \
        "/a/Python.app/Contents/MacOS/Python", "macOS names its interpreter `Python`"
    assert module.python_of("-zsh -c 'fbtodo --instance-of 1'") is None
    rows = [{"pane": "%1", "pid": 900, "window": "@1", "start": "pinned"}]
    table = {900: (1, "-zsh -c '/usr/bin/env PATH=/x /usr/bin/python3 /p/fbtodo'"),
             901: (900, "/opt/homebrew/bin/python3.14 /p/fbtodo --instance-of 1")}
    assert module.pane_python("%1", rows=rows, table=table) == \
        "/opt/homebrew/bin/python3.14", "a pane whose command is a shell answers from its child"
    assert module.pane_python("%2", rows=rows, table=table) is None, "no such pane, no answer"
    say("pane environment: the interpreter is named absolutely under the opener's PATH, the "
        "flags survive it, and the pane's own Python can be read back: ok")

    # ---- ...and the pin covers WHERE the pane works, not only what it runs. tmux starts a
    #      pane from its SERVER's environment, so the state root (`FBTODO_HOME` /
    #      `XDG_STATE_HOME`), the tmux server it drives, the session marker it counts live
    #      sessions by and the six notify-watch paths its bells are sent to are the SERVER's
    #      answer unless they ride in the command with the PATH — a pane whose server
    #      predates one of them silently reads another store, follows another set of
    #      sessions, or rings the DEFAULT bells. `pinned_env` carries only the values this
    #      process HAS: an empty `FBTODO_HOME=` in the line would read as "names a root" to
    #      the locks audit, which must keep telling a command line from an environment.
    #      Every PINNED_ENV_KEYS entry needs a sample here, so a key added without one fails
    #      loudly rather than going untested.
    sample = {"FBTODO_HOME": "/p/h", "XDG_STATE_HOME": "/p/x", "FBTODO_TMUX": "tmux -L s",
              "FBTODO_FB_MARKER": "/p/m", "FBTODO_NOTIFY": "/p/todo.py",
              "FBTODO_DROP": "/p/drop.py", "FBTODO_ASK": "/p/ask.py",
              "FBTODO_PAUSE": "/p/pause.py", "FBTODO_PANE_BELL": "/p/pane.py",
              "FBTODO_LOCKS_BELL": "/p/locks.py"}
    keep_env = {key: os.environ.get(key) for key in module.PINNED_ENV_KEYS}
    for key in module.PINNED_ENV_KEYS:
        os.environ.pop(key, None)
    assert module.pinned_env() == [], "an unset variable was carried as an empty assignment"
    for key in module.PINNED_ENV_KEYS:
        os.environ[key] = sample[key]
    carried = module.pinned_env()
    assert carried == [f"{key}={sample[key]}" for key in module.PINNED_ENV_KEYS], carried
    parts = shlex.split(module.local_pane_command(4242))
    assert not [c for c in carried if c not in parts], (carried, parts)
    for key, value in keep_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    #      The launcher builds the same string in shell, and there are two POSIX bodies to
    #      prove it with: the one the repository ships and the one `fbtodo init` GENERATES.
    #      Both are run under a stub tmux (the generated one written out first) with a value
    #      for every key, and the command each would have split is read back and checked —
    #      a body that forgot a key, or wrote one unquoted, is a failure and not a comment.
    #      The fish body is run too where fish exists.
    launched = dict(os.environ, FREEBUFF_NO_REFRESH="1", TMUX="stub",
                    PATH=os.path.join(ROOT) + os.pathsep + os.environ.get("PATH", ""),
                    **sample)
    fbi = sys.modules.get("fbtodo.fb_init") or module.fb_init
    generated = os.path.join(TEST_HOME, "fb-probe.sh")
    with open(generated, "w", encoding="utf-8") as fh:
        fh.write(fbi.FB_SCRIPT)
    stub = 'tmux() { printf "%s\\n" "$6"; exit 0; }; . "$1"; fb'
    for label, source in (("the shipped examples/fb.sh", os.path.join(ROOT, "examples", "fb.sh")),
                          ("the generated `fbtodo init` body", generated)):
        out = subprocess.run(["bash", "-c", stub, "bash", source],
                             env=launched, capture_output=True, text=True, timeout=30)
        for key in module.PINNED_ENV_KEYS:
            want = f"{key}='{sample[key]}'"
            assert want in out.stdout, (label, want, out.stdout, out.stderr)
    for dialect, body in (("posix", fbi.FB_SCRIPT), ("fish", fbi.FISH_SCRIPT)):
        for key in module.PINNED_ENV_KEYS:
            assert key in body, f"the {dialect} launcher never carries {key}"
    if shutil.which("fish"):
        fish_file = os.path.join(TEST_HOME, "fb-probe.fish")
        with open(fish_file, "w", encoding="utf-8") as fh:
            fh.write(fbi.FISH_SCRIPT)
        fstub = ("function tmux; printf '%s\\n' $argv[6]; exit 0; end\n"
                 f"source {fish_file}\nfb\n")
        fout = subprocess.run(["fish", "-c", fstub], env=launched,
                              capture_output=True, text=True, timeout=30)
        for key in module.PINNED_ENV_KEYS:
            want = f"{key}='{sample[key]}'"
            assert want in fout.stdout, (want, fout.stdout, fout.stderr)
    say("pane environment: the state root, the tmux server, the session marker and the "
        "notify-watch paths ride into the pane with the interpreter and the PATH: ok")

    # ---- the keeper's other repair: a pane found on a different interpreter than its
    #      watcher's is reopened with the pinned command IN PLACE, so it puts itself right
    #      instead of `status` telling somebody to respawn it. The decision is read from the
    #      processes at BOTH ends and compared by real path — one interpreter under two names
    #      is not a drift — and it refuses to guess: no watcher, or a pane whose own line
    #      names no interpreter (still starting), is left alone. The repair itself is driven
    #      through a real private tmux server, because `respawn-pane -k` is the whole point:
    #      the pane keeps its id and its slot and comes back on the pin, and a pane already
    #      carrying this build's pin is not respawned again (which would be a storm, once per
    #      pass, on a keeper that is itself the one out of step).
    drift_dir = os.path.join(TEST_HOME, "pane-python")
    os.makedirs(drift_dir, exist_ok=True)
    real_py = os.path.join(drift_dir, "python3")
    with open(real_py, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\nexit 0\n")
    os.chmod(real_py, 0o755)
    twin_py = os.path.join(drift_dir, "python3-link")
    if os.path.lexists(twin_py):
        os.remove(twin_py)
    os.symlink(real_py, twin_py)
    d_rows = [{"pane": "%1", "pid": 900, "window": "@1", "start": "fbtodo --watch-pid 4242"}]
    d_table = {900: (1, "-zsh"), 901: (900, f"{real_py} /p/fbtodo --watch-pid 4242")}
    assert module.pane_drifted("%1", real_py, rows=d_rows, table=d_table) is None, \
        "a pane on the watcher's interpreter is not a drift"
    assert module.pane_drifted("%1", "/usr/bin/python3", rows=d_rows, table=d_table) == real_py, \
        "a pane on another interpreter is the drift the keeper repairs"
    assert module.pane_drifted("%1", twin_py, rows=d_rows, table=d_table) is None, \
        "one interpreter under two names is not a drift: the comparison is by real path"
    assert module.pane_drifted("%1", None, rows=d_rows, table=d_table) is None, \
        "no watcher to compare against is not a reason to respawn a pane"
    shell_table = {900: (1, "-zsh -c '/p/fbtodo --watch-pid 4242'")}
    assert module.pane_drifted("%1", "/usr/bin/python3", rows=d_rows, table=shell_table) is None, \
        "a pane whose line names no interpreter (still starting) is left alone"
    # ...and the command a pane reports is read back as the COMMAND: tmux quotes it the way
    # a shell would (`'X Y'`, `"X 'Y'"`) when it needs quoting, and a pinned command always
    # does. Compared raw, the pin a pane was started from never equals what tmux says it is,
    # which is how a keeper that believed the comparison would respawn on every pass.
    assert module.pane_start_command('"sleep 300"') == "sleep 300"
    assert module.pane_start_command("'X\"Y'") == 'X"Y'
    assert module.pane_start_command('"X \\"Y\\""') == 'X "Y"'
    assert module.pane_start_command("nosuchcmd") == "nosuchcmd"
    assert module.pane_start_command("a\\b") == "a\\b", "an unwrapped value is left alone"
    assert module.pane_start_command("'unbalanced") == "'unbalanced"
    # A pane whose command is ALREADY this build's pin is not the one out of step — respawning
    # it would re-run what it is running — so it stays, and the pane log says once which side
    # to look at. `%9999` is on purpose: no such pane exists, and nothing may reach tmux for
    # one that is only here to make the guard fire.
    def pane_log_text() -> str:
        try:
            with open(module.PANE_LOG_PATH, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""

    log_before_held = pane_log_text()
    assert module.repair_drifted_panes(
        {4242: "@1"}, "/usr/bin/python3",
        rows=[{"pane": "%9999", "pid": 900, "window": "@1",
               "start": module.local_pane_command(4242)}],
        table=d_table,
    ) == [], "a pane already carrying the pin was respawned"
    assert "already carries this build's pin" in pane_log_text()[len(log_before_held):], \
        pane_log_text()[-300:]

    # ...and the repair through a real server. `FBTODO_PANE_SECONDS=0` and `FBTODO_HOME` in
    # the server's environment are load-bearing: the pane the respawn starts calls
    # `ensure_daemon`, and a keeper or watcher built for a stand-in pane would outlive it.
    if shutil.which("tmux"):
        pin_sock = sock("fbtpinsock")
        tmux_pin = ["tmux", "-L", pin_sock]
        subprocess.run(tmux_pin + ["kill-server"], capture_output=True)
        made = subprocess.run(
            tmux_pin + [
                "new-session", "-d", "-s", "pinsess", "-x", "80", "-y", "24",
                "-c", TEST_HOME,
                "-e", f"FBTODO_HOME={TEST_HOME}",
                "-e", "FBTODO_PANE_SECONDS=0",
                "-e", "FBTODO_NO_AUTOSTART=1",
                "sleep 300",
            ],
            capture_output=True, text=True,
        )
        assert made.returncode == 0, made.stderr
        saved_tmux = list(module.TMUX_BIN)
        set_knob(module, "TMUX_BIN", list(tmux_pin))
        try:
            listed = subprocess.run(
                tmux_pin + ["list-panes", "-t", "pinsess", "-F", "#{pane_id} #{pane_pid}"],
                capture_output=True, text=True,
            ).stdout.split()
            live_pane, live_pid = listed[0], int(listed[1])
            live_rows = [{"pane": live_pane, "pid": live_pid, "window": "@1",
                          "start": "fbtodo --watch-pid 4242"}]
            live_table = {live_pid: (1, "-zsh"),
                          live_pid + 1: (live_pid, f"{real_py} /p/fbtodo --watch-pid 4242")}
            # The knob, before any attempt is made: a pane marked `@fbtodo_repair off` — a
            # tmux user option, so it is per pane by construction — is kept exactly as it
            # is, and the keeper still says what it saw: the log names the drift, and the
            # note is the `kept` one the RUNNING pane picks up (nothing is restarting to
            # deliver a reopen note). `on`/`0`/unset are read back the way the manager
            # writes them, and turning it back on resumes the repair with no keeper restart.
            subprocess.run(tmux_pin + ["set", "-p", "-t", live_pane, "@fbtodo_repair", "off"],
                           capture_output=True)
            assert module.pane_repair_off(live_pane) is True, \
                "the `@fbtodo_repair off` mark was not read"
            module._DRIFT_TRIED.clear()
            module._DRIFT_KEPT.clear()
            kept_mark = len(pane_log_text())
            assert module.repair_drifted_panes({4242: "@1"}, "/usr/bin/python3",
                                               rows=live_rows, table=live_table) == [], \
                "a pane marked repair-off was respawned"
            assert "repair is off (@fbtodo_repair)" in pane_log_text()[kept_mark:], \
                pane_log_text()[-300:]
            kept_note = module.pane_note_take(live_pane, kind="kept")
            assert kept_note and kept_note.get("was") == real_py, kept_note
            assert module.reopen_note(kept_note) == \
                f"KEPT (was on {module._short_python(real_py)})", module.reopen_note(kept_note)
            assert subprocess.run(
                tmux_pin + ["list-panes", "-t", "pinsess", "-F", "#{pane_id}"],
                capture_output=True, text=True,
            ).stdout.split() == [live_pane], "the kept pane was replaced anyway"
            subprocess.run(tmux_pin + ["set", "-p", "-t", live_pane, "@fbtodo_repair", "on"],
                           capture_output=True)
            assert module.pane_repair_off(live_pane) is False, "`on` did not turn it back"
            subprocess.run(tmux_pin + ["set", "-p", "-t", live_pane, "@fbtodo_repair", "0"],
                           capture_output=True)
            assert module.pane_repair_off(live_pane) is True, "the `0` spelling was not read"
            subprocess.run(tmux_pin + ["set", "-p", "-u", "-t", live_pane, "@fbtodo_repair"],
                           capture_output=True)
            assert module.pane_repair_off(live_pane) is False, \
                "an unset option stayed off (the repair never comes back)"
            # ...and the same knob as a COMMAND, so nobody has to remember the
            # incantation: `fbtodo keep off/on/default` against this pane, the same verbs
            # server-wide, an explicit `on` winning over a server-wide `off`, and the
            # target rules (a pane can be named from outside it; no target outside tmux is
            # the caller's error, not a guess). `FBTODO_TMUX` is how the launcher names a
            # private server, which is exactly what the flag is for.
            keep_env = dict(env, FBTODO_TMUX=f"tmux -L {pin_sock}")

            def keep(*argv):
                return subprocess.run(
                    [sys.executable, FB, "keep", *argv], capture_output=True, text=True,
                    env=keep_env, cwd=CWD, timeout=60,
                )

            def keep_line(*argv) -> str:
                out = keep(*argv).stdout
                rows = [ln for ln in STRIP(out).splitlines() if "repair" in ln]
                assert rows, out[-300:]
                return rows[0]

            assert "repair : on" in keep_line("--pane", live_pane), keep_line("--pane", live_pane)
            ran = keep("off", "--pane", live_pane)
            assert ran.returncode == 0 and "repair : off" in ran.stdout, \
                (ran.returncode, ran.stdout, ran.stderr)
            assert "kept as it is" in ran.stdout, ran.stdout[-200:]
            assert module.pane_repair_off(live_pane) is True, "the CLI did not set the knob"
            ran = keep("default", "--pane", live_pane)
            assert ran.returncode == 0 and module.pane_repair_off(live_pane) is False, ran
            # an explicit `on` wins over a server-wide `off`; `default` gives both back
            assert keep("off", "--server").returncode == 0, "the server-wide knob was refused"
            assert module.pane_repair_off(live_pane) is True, "the server-wide knob was not read"
            ran = keep("on", "--pane", live_pane)
            assert ran.returncode == 0 and module.pane_repair_off(live_pane) is False, ran
            assert "reopened on its pin" in ran.stdout, ran.stdout[-200:]
            bare = {k: v for k, v in keep_env.items() if k != "TMUX_PANE"}
            ran = subprocess.run([sys.executable, FB, "keep", "off"],
                                 capture_output=True, text=True, env=bare, cwd=CWD, timeout=60)
            assert ran.returncode == 64 and "--pane" in ran.stderr, (ran.returncode, ran.stderr)
            assert keep("default", "--server").returncode == 0, "the server-wide unset was refused"
            assert module.pane_repair_off(live_pane) is False, \
                "the server-wide unset did not stick"
            # ...and the MIDDLE scope, a window: the knob written with `-w` belongs to that
            # window's panes and to no pane in a sibling, `default` hands the window back to
            # the server's answer again, and a target that names no window is tmux's silence
            # (69), never a guess. `--window` takes the same TARGET shapes `pin`/`why` do —
            # a session, `session:index`, a window id — resolved through tmux, with the
            # report naming the id the write actually landed on.
            made = subprocess.run(
                tmux_pin + ["new-window", "-d", "-t", "pinsess", "-n", "sibling",
                            "-c", TEST_HOME, "sleep 300"],
                capture_output=True, text=True,
            )
            assert made.returncode == 0, made.stderr
            win_rows = [line.split() for line in subprocess.run(
                tmux_pin + ["list-windows", "-t", "pinsess",
                            "-F", "#{window_id} #{window_name}"],
                capture_output=True, text=True,
            ).stdout.splitlines()]
            assert len(win_rows) == 2, win_rows
            first_win, other_win = win_rows[0][0], win_rows[1][0]
            assert module.window_repair_value("@9999") is None, \
                "a window target that exists nowhere must read as no answer, not as unset"
            other_pane = subprocess.run(
                tmux_pin + ["list-panes", "-t", other_win, "-F", "#{pane_id}"],
                capture_output=True, text=True,
            ).stdout.split()[0]
            # The row above left live_pane with its own pane-level `on` — that pin is about
            # a pane outranking the server — and a pane's own choice also outranks a
            # window's, so it is cleared first: these rows are about the rung BELOW the
            # pane, and the window's write has to be visible through it.
            assert keep("default", "--pane", live_pane).returncode == 0, \
                "the pane-level choice would not clear"
            assert "repair : on" in keep_line("--window", first_win), \
                keep_line("--window", first_win)
            ran = keep("off", "--window", "pinsess")
            assert ran.returncode == 0 and "repair : off" in ran.stdout, \
                (ran.returncode, ran.stdout, ran.stderr)
            assert f"window {first_win}" in ran.stdout, ran.stdout
            assert (module.tmux_run("show-options", "-w", "-v", "-t", first_win,
                                    "@fbtodo_repair") or "").strip() == "off", \
                "the write did not land on the window rung"
            assert module.pane_repair_off(live_pane) is True, \
                "a pane in the window did not inherit the write"
            assert module.pane_repair_off(other_pane) is False, \
                "a pane in a sibling window inherited the write"
            assert "repair : on" in keep_line("--window", other_win), \
                keep_line("--window", other_win)
            ran = keep("default", "--window", "pinsess")
            assert ran.returncode == 0 and "the server's choice applies again" in ran.stdout, \
                (ran.returncode, ran.stdout)
            assert module.tmux_run("show-options", "-w", "-v", "-t", first_win,
                                   "@fbtodo_repair") is None, \
                "default left the window's own choice in place"
            # the window rung outranks the server's: a server-wide off reads through both
            # windows, and a window's own `on` reads through its own panes only
            assert keep("off", "--server").returncode == 0
            assert module.pane_repair_off(live_pane) is True \
                and module.pane_repair_off(other_pane) is True, "the server-wide off did not read"
            ran = keep("on", "--window", other_win)
            assert ran.returncode == 0 and module.pane_repair_off(other_pane) is False, ran
            assert module.pane_repair_off(live_pane) is True, \
                "a window's `on` leaked into a sibling window"
            assert keep("default", "--window", other_win).returncode == 0
            assert module.pane_repair_off(other_pane) is True, \
                "default did not hand the window back to the server's off"
            # the scopes are one per call, and a window that does not exist is named as such
            ran = keep("off", "--window", first_win, "--pane", live_pane)
            assert ran.returncode == 64 and "--window" in ran.stderr, (ran.returncode, ran.stderr)
            ran = keep("off", "--server", "--window", first_win)
            assert ran.returncode == 64 and "--server" in ran.stderr, (ran.returncode, ran.stderr)
            ran = keep("--window", "no-such-window")
            assert ran.returncode == 69 and "no such window" in ran.stderr, \
                (ran.returncode, ran.stderr)
            assert keep("default", "--server").returncode == 0, "the server-wide unset was refused"
            assert module.pane_repair_off(live_pane) is False, \
                "the server-wide unset did not stick (after the window rows)"
            module._DRIFT_TRIED.clear()
            repaired = module.repair_drifted_panes({4242: "@1"}, "/usr/bin/python3",
                                                   rows=live_rows, table=live_table)
            assert repaired == [live_pane], (repaired, live_pane)
            after = subprocess.run(
                tmux_pin + ["list-panes", "-t", "pinsess",
                            "-F", "#{pane_id}|#{pane_start_command}"],
                capture_output=True, text=True,
            ).stdout.strip()
            got_pane, got_start = after.split("|", 1)
            got_start = module.pane_start_command(got_start)
            assert got_pane == live_pane, "the repair changed the pane's id"
            assert got_start == module.local_pane_command(4242), got_start
            # The next pass reads that command back from tmux and finds the pin on it, so the
            # pane is left alone — no second respawn, however often the keeper looks.
            module._DRIFT_TRIED.clear()
            assert module.repair_drifted_panes(
                {4242: "@1"}, "/usr/bin/python3",
                rows=[{"pane": got_pane, "pid": live_pid, "window": "@1", "start": got_start}],
                table=live_table,
            ) == [], "a pane already carrying the pin was respawned again"
        finally:
            set_knob(module, "TMUX_BIN", saved_tmux)
            subprocess.run(tmux_pin + ["kill-server"], capture_output=True)
        say("pane interpreter drift: a pane on another Python than its watcher is reopened on "
            "the pinned command in place, one already on the pin is left alone, and one "
            "marked `@fbtodo_repair off` is kept as it is — diagnosed, logged and told on its "
            "own chip — and `fbtodo keep` flips the knob, pane, window or server, without "
            "the incantation: ok")

    # ---- the keeper's third repair: a pane whose RECORDED command is an older PIN. The
    #      drift repair next door only asks whether the INTERPRETER agrees; a pane whose
    #      command names the same Python can still have been started with the wrong state
    #      root, the wrong tmux server or the old bells, and nothing else in the keeper ever
    #      looks — the recorded command is re-run only when somebody respawns the pane, and
    #      nobody has a reason to. `stale_pin` is therefore about the CARRIED VALUES and
    #      nothing else: every value this build hands on must be in the line, with THIS
    #      value. The interpreter, the PATH and the arguments after them are deliberately
    #      not compared, so an `fb` pane on `--instance-of` that carries every value is not
    #      called stale for differing from a split's `--watch-pid` — and with no values to
    #      hand on, no command is stale at all (the server's environment IS the answer).
    sample = {"FBTODO_HOME": "/p/h", "XDG_STATE_HOME": "/p/x", "FBTODO_TMUX": "tmux -L s",
              "FBTODO_FB_MARKER": "/p/m", "FBTODO_NOTIFY": "/p/todo.py",
              "FBTODO_DROP": "/p/drop.py", "FBTODO_ASK": "/p/ask.py",
              "FBTODO_PAUSE": "/p/pause.py", "FBTODO_PANE_BELL": "/p/pane.py",
              "FBTODO_LOCKS_BELL": "/p/locks.py"}
    stale_keep = {key: os.environ.get(key) for key in module.PINNED_ENV_KEYS}
    try:
        for key in module.PINNED_ENV_KEYS:
            os.environ.pop(key, None)
        assert module.stale_pin("fbtodo --watch-pid 1") is False, \
            "a command was called stale with no values to hand on"
        for key in module.PINNED_ENV_KEYS:
            os.environ[key] = sample[key]
        pin = module.local_pane_command(4242)
        ours = f"{module.self_argv()[0]} {module.self_argv()[1]} --watch-pid 1"
        assert module.stale_pin("") is False, "an empty command was called stale"
        assert module.stale_pin("tmux new-window") is False, \
            "a pane that is not ours was called stale"
        assert module.stale_pin(ours) is True, \
            "a pane of ours carrying no values at all was called fresh"
        assert module.stale_pin(pin) is False, "the current pin was called stale"
        fb_like = module.pane_command([*module.self_argv(), "--instance-of", "4242"])
        assert module.stale_pin(fb_like) is False, \
            "an fb pane carrying every value was called stale"
        for key in ("FBTODO_HOME", "XDG_STATE_HOME", "FBTODO_LOCKS_BELL"):
            gone = [t for t in shlex.split(pin) if not t.startswith(f"{key}=")]
            assert module.stale_pin(shlex.join(gone)) is True, \
                f"a pin missing {key} was called fresh"
        swapped = [f"{t.partition('=')[0]}=/elsewhere"
                   if t.partition("=")[0].startswith("FBTODO_") else t
                   for t in shlex.split(pin)]
        assert module.stale_pin(shlex.join(swapped)) is True, \
            "a pin carrying a DIFFERENT value was called fresh"
        #      ...and the reader that is NOT the keeper judges by the SESSION's values, not
        #      its own: `status`/`why` can be typed in any shell, and a pane the keeper would
        #      leave alone must not read stale because THAT shell spells a value differently.
        #      The two environments differ in one key so the choice is visible — judged by the
        #      client's, the very pane carrying the session's pin reads stale (the bug).
        sess_values = [f"{k}={sample[k]}" for k in module.PINNED_ENV_KEYS]
        client_values = [v for v in sess_values if not v.startswith("FBTODO_LOCKS_BELL=")] \
            + ["FBTODO_LOCKS_BELL=/client-only"]
        assert module.stale_pin(pin, sess_values) is False, \
            "a pane on the session's pin was called stale under the session's own values"
        assert module.stale_pin(pin, client_values) is True, \
            "the injected client values were not what stale_pin compared against"
        # the blob reader keeps a value's own spaces and orders by PINNED_ENV_KEYS, and a
        # process naming none of the keys falls back to this process's own answer
        blob = "PATH=/bin OTHER=1 " + " ".join(sess_values)
        assert module.pinned_env_from(blob) == sess_values, module.pinned_env_from(blob)
        assert module.session_pinned_env(None, blob=blob) == sess_values
        assert module.session_pinned_env(None, blob="PATH=/bin OTHER=1") == module.pinned_env(), \
            "a process naming none of the keys did not fall back to this process's own"
    finally:
        for key, value in stale_keep.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    #      ...and the repair itself, through a private server: a pane recorded on a command
    #      with none of the values is reopened on the pin IN PLACE — same pane id, same
    #      window — and the readback shows the CURRENT pin, which is the whole point, since
    #      the command is what a later respawn would re-run. One already carrying the pin is
    #      left alone, and one marked `@fbtodo_repair off` is kept exactly as it is and told
    #      so on its own chip (the `kept` note the running pane picks up, since nothing is
    #      restarting to deliver a reopen one). A respawn that never happened takes its note
    #      back, so nothing unknown claims it.
    if shutil.which("tmux"):
        stale_sock = sock("fbstalesock")
        tmux_stale = ["tmux", "-L", stale_sock]
        subprocess.run(tmux_stale + ["kill-server"], capture_output=True)
        made = subprocess.run(
            tmux_stale + [
                "new-session", "-d", "-s", "stalesess", "-x", "80", "-y", "24",
                "-c", TEST_HOME,
                "-e", f"FBTODO_HOME={TEST_HOME}",
                "-e", "FBTODO_PANE_SECONDS=0",
                "-e", "FBTODO_NO_AUTOSTART=1",
                "sleep 300",
            ],
            capture_output=True, text=True,
        )
        assert made.returncode == 0, made.stderr
        saved_tmux = list(module.TMUX_BIN)
        set_knob(module, "TMUX_BIN", list(tmux_stale))
        try:
            listed = subprocess.run(
                tmux_stale + ["list-panes", "-t", "stalesess", "-F", "#{pane_id} #{pane_pid}"],
                capture_output=True, text=True,
            ).stdout.split()
            stale_pane, stale_pid = listed[0], int(listed[1])
            stale_table = {stale_pid: (1, "-zsh")}
            # a command of OURS — the real launcher token, which is what `stale_pin`
            # recognises us by — but carrying none of the values
            old_line = " ".join(
                shlex.quote(t) for t in [*module.self_argv(), "--watch-pid", "4242"]
            )
            assert "fbtodo" in old_line, old_line
            old_rows = [{"pane": stale_pane, "pid": stale_pid, "window": "@1",
                         "start": old_line}]
            # the knob, first: a stale pane marked repair-off is left as it is, logged, and
            # told on its own chip — and said only once, however often the keeper looks
            subprocess.run(tmux_stale + ["set", "-p", "-t", stale_pane, "@fbtodo_repair", "off"],
                           capture_output=True)
            module._STALE_TRIED.clear()
            module._STALE_HELD.clear()
            kept_mark = len(pane_log_text())
            assert module.upgrade_stale_panes({4242: "@1"}, rows=old_rows,
                                              table=stale_table) == [], \
                "a stale pane marked repair-off was reopened"
            assert "older pin" in pane_log_text()[kept_mark:], pane_log_text()[-300:]
            kept_note = module.pane_note_take(stale_pane, kind="kept")
            assert kept_note is not None, "the kept stale pane was not told on its chip"
            held_mark = len(pane_log_text())
            assert module.upgrade_stale_panes({4242: "@1"}, rows=old_rows,
                                              table=stale_table) == []
            assert "older pin" not in pane_log_text()[held_mark:], \
                "the kept stale pane was logged again on the next pass"
            assert subprocess.run(
                tmux_stale + ["list-panes", "-t", "stalesess", "-F", "#{pane_id}"],
                capture_output=True, text=True,
            ).stdout.split() == [stale_pane], "the kept pane was replaced anyway"
            subprocess.run(tmux_stale + ["set", "-p", "-u", "-t", stale_pane, "@fbtodo_repair"],
                           capture_output=True)
            assert module.pane_repair_off(stale_pane) is False, "the repair never came back"
            # now the real respawn: the pane keeps its id and its recorded command becomes
            # THIS build's pin, so a later reader — and a later respawn — finds it current
            module._STALE_TRIED.clear()
            module._STALE_HELD.clear()
            log_mark = len(pane_log_text())
            assert module.upgrade_stale_panes({4242: "@1"}, rows=old_rows,
                                              table=stale_table) == [stale_pane]
            after = subprocess.run(
                tmux_stale + ["list-panes", "-t", "stalesess",
                              "-F", "#{pane_id}|#{pane_start_command}"],
                capture_output=True, text=True,
            ).stdout.strip()
            got_pane, got_start = after.split("|", 1)
            assert got_pane == stale_pane, "the upgrade changed the pane's id"
            assert module.pane_start_command(got_start) == module.local_pane_command(4242), got_start
            assert f"reopened {stale_pane}" in pane_log_text()[log_mark:], \
                pane_log_text()[-300:]
            # ...and the pass SETTLES: the command the pane now reports is the pin, so the
            # very next pass is a no-op — no respawn however often the keeper looks
            module._STALE_TRIED.clear()
            current_rows = [{"pane": stale_pane, "pid": stale_pid, "window": "@1",
                             "start": module.pane_start_command(got_start)}]
            assert module.upgrade_stale_panes({4242: "@1"}, rows=current_rows,
                                              table=stale_table) == [], \
                "a pane already on the pin was reopened again"
            # ---- ...and the DIAGNOSTIC that names a stale pane BEFORE the keeper rewrites
            #      it: `status`'s `pane pin` line and `why`'s flag both read
            #      `stale_pane_ids`, the same predicate the upgrade acts on — so a pane
            #      cannot be named stale one moment and treated as current the next, and one
            #      marked repair-off keeps being named. The two halves of the sentence mean
            #      different things (about to change vs will not), so they are split.
            assert module.stale_pane_ids(4242, rows=current_rows, table=stale_table) == [], \
                "a pane already on the pin was named stale"
            assert module.stale_pane_ids(4242, rows=old_rows, table=stale_table) == [stale_pane], \
                "a stale pane was not named"
            assert module.stale_pane_ids(9999, rows=old_rows, table=stale_table) == [], \
                "another instance's pane was named stale"
            assert module.stale_pane_ids(4242, rows=old_rows, table=stale_table,
                                         wanted=[]) == [], \
                "stale_pane_ids ignored an injected pin of no values"
            assert module.stale_pin_note([]) == "", "an empty pane list said something"
            subprocess.run(tmux_stale + ["set", "-p", "-u", "-t", stale_pane, "@fbtodo_repair"],
                           capture_output=True)
            assert "reopens it on the current one" in module.stale_pin_note([stale_pane]), \
                module.stale_pin_note([stale_pane])
            subprocess.run(tmux_stale + ["set", "-p", "-t", stale_pane, "@fbtodo_repair", "off"],
                           capture_output=True)
            assert "kept (@fbtodo_repair off)" in module.stale_pin_note([stale_pane]), \
                module.stale_pin_note([stale_pane])
            subprocess.run(tmux_stale + ["set", "-p", "-u", "-t", stale_pane, "@fbtodo_repair"],
                           capture_output=True)
            # ...and a failed respawn takes its note back, so nothing unknown claims it
            calls = []

            def fake_tmux(*argv, timeout=10.0):
                calls.append(argv)
                return None

            real_tmux = module.tmux_run
            set_knob(module, "tmux_run", fake_tmux)
            try:
                module._STALE_TRIED.clear()
                module._STALE_HELD.clear()
                assert module.upgrade_stale_panes({4242: "@1"}, rows=old_rows,
                                                  table=stale_table) == []
                assert ("respawn-pane", "-k", "-t") in [tuple(c[:3]) for c in calls], calls
                left = module.read_json(module.PANE_NOTE_PATH, {}) or {}
                assert stale_pane not in left, "a note stayed when the respawn was abandoned"
            finally:
                set_knob(module, "tmux_run", real_tmux)
        finally:
            set_knob(module, "TMUX_BIN", saved_tmux)
            subprocess.run(tmux_stale + ["kill-server"], capture_output=True)
        say("pane pin upgrade: a pane whose recorded command is an older pin — the same "
            "interpreter agreeing, the values behind — is reopened once in place so its "
            "command becomes this build's, one already on the pin is left alone, and a pane "
            "marked `@fbtodo_repair off` is kept as it is and told so; `status` names a stale "
            "pane before the keeper rewrites it, split by whether the repair is off: ok")

    # ---- ...and the drift repair reads the pane's whole TREE now, not only its own line:
    #      what a pane starts under itself — a keeper, a bell through its watcher, a tmux
    #      child — is part of the same promise (one interpreter for the session), and a
    #      reader that stops at the pane can never see a child that resolved differently.
    #      `tree_pythons` answers with every interpreter in the subtree, the pane's own
    #      first; `tree_drift` names the first member on another one by REAL PATH; the
    #      keeper treats that as the same drift and reopens the pane on the pin when its
    #      command is not the pin already — and when it IS, re-running it cannot change what
    #      a child resolved, so the child is named once and the launch is left to the pin
    #      (`notify_argv`, whose shape is asserted here too).
    tree_dir = os.path.join(TEST_HOME, "pane-tree")
    os.makedirs(tree_dir, exist_ok=True)
    other_py = os.path.join(tree_dir, "python3")
    with open(other_py, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\nexit 0\n")
    os.chmod(other_py, 0o755)
    t_rows = [{"pane": "%1", "pid": 900, "window": "@1", "start": "fbtodo --watch-pid 4242"}]
    # the pane's own line is a login shell — its nearest descendant is the answer
    # `pane_python` would give — and a tmux child under that resolved elsewhere
    t_shell = {900: (1, "-zsh -c '/p/fbtodo --watch-pid 4242'")}
    t_tree = {
        901: (900, f"{real_py} /p/fbtodo --watch-pid 4242"),
        902: (901, f"{other_py} /p/fbtodo --watch-pid 4242"),
    }
    got_tree = module.tree_pythons("%1", rows=t_rows, table={**t_shell, **t_tree})
    assert got_tree[0] == (901, real_py), "the pane's nearest answer does not lead the tree"
    assert sorted(got_tree) == sorted([(901, real_py), (902, other_py)]), got_tree
    assert module.tree_drift("%1", rows=t_rows, table={**t_shell, **t_tree}) == (902, other_py), \
        "a child on another interpreter is the drift the tree reader exists for"
    assert module.tree_drift(
        "%1", rows=t_rows,
        table={**t_shell, 901: (900, f"{real_py} /p/fbtodo --watch-pid 4242"),
               902: (901, f"{twin_py} /p/fbtodo --watch-pid 4242")},
    ) is None, "one interpreter under two names is not a drift: the comparison is by real path"
    assert module.tree_drift("%1", rows=t_rows, table=t_shell) is None, \
        "a tree with no interpreter in it has nothing to compare"
    assert module.tree_pythons("%404", rows=t_rows, table=t_shell) == []
    # every bell runs behind an explicit PATH=<pin>, so the `env python3` in its shebang
    # resolves in the asking process's pin rather than in whatever PATH it inherited
    bell = module.notify_argv([os.path.join(TEST_HOME, "bell.py"), "--quiet"])
    assert bell[0] == ("/usr/bin/env" if os.path.exists("/usr/bin/env") else "env"), bell
    assert bell[1] == f"PATH={module.pinned_path()}" and bell[2].endswith("bell.py"), bell
    assert bell[3:] == ["--quiet"], bell

    # ---- and the pane a keeper reopened SAYS SO on its title chip. The log was the only
    #      place the repair spoke, which is the wrong way round — the person watching is
    #      looking at the pane when its process is replaced under them. The note cannot ride
    #      the environment the way a reload's does (`respawn-pane` starts a fresh command in
    #      the server's environment, with no exec to carry it), so it waits at a path: written
    #      by the keeper before the respawn, claimed once by the pane it names (its own id is
    #      `TMUX_PANE`), and dropped unshown when it has waited too long.
    long_py = ("/opt/homebrew/Cellar/python@3.14/3.14.7/Frameworks/Python.framework/"
               "Versions/3.14/Resources/Python.app/Contents/MacOS/Python")
    assert module.reopen_note({"was": "/usr/bin/python3"}) == \
        "REOPENED (was on /usr/bin/python3)"
    assert module.reopen_note({"was": long_py, "child": 4242}) == \
        f"REOPENED (a child was on {module._short_python(long_py)})", \
        "a long interpreter path is not shortened on the chip"
    assert module.reopen_note(None) is None and module.reopen_note({}) is None
    assert module.reopen_note({"was": ""}) == "REOPENED ON THE PIN", \
        "a note without the name still does not say which act it was"
    # ...and the knob's note: the keeper SAW the drift and kept the pane, so the chip says
    # that instead of `REOPENED` — with the same evidence, and with the knob's own words
    # when there was nothing to name
    assert module.reopen_note({"was": long_py, "child": 4242, "kind": "kept"}) == \
        f"KEPT (a child was on {module._short_python(long_py)})"
    assert module.reopen_note({"was": "/usr/bin/python3", "kind": "kept"}) == \
        "KEPT (was on /usr/bin/python3)"
    assert module.reopen_note({"kind": "kept"}) == "KEPT (repair off)"
    # ...and the two kinds never steal each other's note: the running pane's poll takes
    # `kept` only, so a `reopen` note stays for the process that will replace it
    module.pane_note_write("%noteD", "/usr/bin/python3")
    assert module.pane_note_take("%noteD", kind="kept", sweep=False) is None, \
        "the running pane took the note meant for its replacement"
    got_d = module.pane_note_take("%noteD")
    assert got_d and (got_d.get("kind") or "reopen") == "reopen", got_d
    module.pane_note_write("%noteE", "/usr/bin/python3", kind="kept")
    got_e = module.pane_note_take("%noteE", kind="kept", sweep=False)
    assert got_e and got_e.get("kind") == "kept", got_e
    assert module.pane_note_take("%noteE", kind="kept", sweep=False) is None, \
        "a kept note was shown twice"
    module.pane_note_write("%noteA", "/usr/bin/python3")
    got_note = module.pane_note_take("%noteA")
    assert got_note and got_note.get("was") == "/usr/bin/python3" \
        and got_note.get("child") is None, got_note
    assert module.pane_note_take("%noteA") is None, "a note was shown twice"
    assert module.pane_note_take(None) is None and module.pane_note_take("%nobody") is None
    module.pane_note_write("%noteB", long_py, child=7)
    stale = module.read_json(module.PANE_NOTE_PATH, {}) or {}
    stale["%noteB"] = dict(stale["%noteB"],
                           at_ms=int((time.time() - module.PANE_NOTE_S - 5) * 1000))
    with open(module.PANE_NOTE_PATH, "w", encoding="utf-8") as fh:
        json.dump(stale, fh)
    assert module.pane_note_take("%noteB") is None, "a stale note was shown"
    module.pane_note_write("%noteC", "/usr/bin/python3")
    module.pane_note_forget("%noteC")
    assert module.pane_note_take("%noteC") is None, "a withdrawn note was shown"
    # ...and the whole path once through the real command: a pane started with a note for
    # the id it reports says it on the frame's title chip, and the note is gone afterwards
    module.pane_note_write("%fbnote", "/usr/bin/python3")
    note_env = dict(env, TERM="xterm-256color", COLORTERM="truecolor", FORCE_COLOR="1",
                    TMUX_PANE="%fbnote")
    note_env.pop("NO_COLOR", None)
    note_run = subprocess.run(
        [sys.executable, FB, "pane", "--once", "--no-daemon", "--stale-after", "0"],
        capture_output=True, text=True, env=note_env, cwd=CWD, timeout=60,
    )
    assert note_run.returncode == 0, (note_run.returncode, note_run.stderr[-400:])
    assert "REOPENED (was on /usr/bin/python3)" in note_run.stdout, \
        note_run.stdout.splitlines()[0] if note_run.stdout else note_run.stderr[-300:]
    assert "%fbnote" not in (module.read_json(module.PANE_NOTE_PATH, {}) or {}), \
        "the pane did not claim its note"
    # ...and the same way for a pane the keeper KEPT — the chip explains what was seen even
    # though nothing was restarted: written for a running pane to claim, and shown by it
    module.pane_note_write("%fbkept", "/usr/bin/python3", kind="kept")
    kept_run = subprocess.run(
        [sys.executable, FB, "pane", "--once", "--no-daemon", "--stale-after", "0"],
        capture_output=True, text=True, env=dict(note_env, TMUX_PANE="%fbkept"),
        cwd=CWD, timeout=60,
    )
    assert kept_run.returncode == 0, (kept_run.returncode, kept_run.stderr[-400:])
    assert "KEPT (was on /usr/bin/python3)" in kept_run.stdout, \
        kept_run.stdout.splitlines()[0] if kept_run.stdout else kept_run.stderr[-300:]
    assert "%fbkept" not in (module.read_json(module.PANE_NOTE_PATH, {}) or {}), \
        "the pane did not claim its kept note"
    say("pane repair note: a pane the keeper acted on says so on its title chip for a few "
        "seconds — `REOPENED (was on …)` for a respawn, `KEPT (was on …)` for one it left "
        "alone — and claims the note once, and a stale one is never shown: ok")

    # ...and the repair through a real server, on tree evidence: the pane keeps its id and
    # comes back on the pin. `FBTODO_PANE_SECONDS=0` and `FBTODO_HOME` in the server's
    # environment are load-bearing, the same as in the row above.
    if shutil.which("tmux"):
        tree_sock = sock("fbtreesock")
        tmux_tree = ["tmux", "-L", tree_sock]
        subprocess.run(tmux_tree + ["kill-server"], capture_output=True)
        tree_script = os.path.join(TEST_HOME, "fbtodo-tree-pane.py")
        with open(tree_script, "w", encoding="utf-8") as fh:
            fh.write("import time\ntime.sleep(300)\n")
        made = subprocess.run(
            tmux_tree + [
                "new-session", "-d", "-s", "treesess", "-x", "80", "-y", "24",
                "-c", TEST_HOME,
                "-e", f"FBTODO_HOME={TEST_HOME}",
                "-e", "FBTODO_PANE_SECONDS=0",
                "-e", "FBTODO_NO_AUTOSTART=1",
                f"{sys.executable} {tree_script} --watch-pid 4242",
            ],
            capture_output=True, text=True,
        )
        assert made.returncode == 0, made.stderr
        saved_tmux = list(module.TMUX_BIN)
        set_knob(module, "TMUX_BIN", list(tmux_tree))
        try:
            listed = subprocess.run(
                tmux_tree + ["list-panes", "-t", "treesess", "-F", "#{pane_id} #{pane_pid}"],
                capture_output=True, text=True,
            ).stdout.split()
            tree_pane, tree_pid = listed[0], int(listed[1])
            live_rows, live_table = module.pane_rows(), module.process_table()
            # the real tree, read from the real processes: one interpreter, read the same
            # way by both readers
            live_tree = module.tree_pythons(tree_pane, live_rows, live_table)
            assert live_tree, "the live pane's own interpreter was not read"
            assert module.tree_drift(tree_pane, live_rows, live_table) is None, live_tree
            assert module.pane_python(tree_pane, live_rows, live_table) == live_tree[0][1]
            # a tree that genuinely disagrees: the child's line names an interpreter this
            # machine does not run the tree on, which is what the decision below is about
            elsewhere = os.path.join(tree_dir, "elsewhere", "python3")
            drift_table = {
                tree_pid: (1, f"{sys.executable} {tree_script} --watch-pid 4242"),
                tree_pid + 1: (tree_pid, f"{elsewhere} /p/fbtodo --watch-pid 4242"),
            }
            # the keeper's own wiring, with tmux stubbed: the note is written BEFORE the
            # respawn (a pane cannot read a file that is not there yet) and taken back when
            # the respawn did not happen, so nothing else can claim it
            seen = {}

            def fake_tmux(*argv, timeout=10.0):
                seen["argv"] = argv
                seen["note"] = module.read_json(module.PANE_NOTE_PATH, {}) or {}
                return None

            real_tmux = module.tmux_run
            set_knob(module, "tmux_run", fake_tmux)
            try:
                module._DRIFT_TRIED.clear()
                module._DRIFT_HELD.clear()
                assert module.repair_drifted_panes({4242: "@1"}, None, rows=live_rows,
                                                   table=drift_table) == []
                assert tuple(seen["argv"][:3]) == ("respawn-pane", "-k", "-t"), seen["argv"]
                assert seen["note"].get(tree_pane, {}).get("was") == elsewhere, seen["note"]
                assert seen["note"].get(tree_pane, {}).get("child"), seen["note"]
                left = module.read_json(module.PANE_NOTE_PATH, {}) or {}
                assert tree_pane not in left, "a note stayed when the respawn was abandoned"
            finally:
                set_knob(module, "tmux_run", real_tmux)
            module._DRIFT_TRIED.clear()
            module._DRIFT_HELD.clear()
            log_before_tree = pane_log_text()
            repaired = module.repair_drifted_panes({4242: "@1"}, None, rows=live_rows,
                                                   table=drift_table)
            assert repaired == [tree_pane], (repaired, tree_pane)
            after = subprocess.run(
                tmux_tree + ["list-panes", "-t", "treesess",
                             "-F", "#{pane_id}|#{pane_start_command}"],
                capture_output=True, text=True,
            ).stdout.strip()
            got_pane, got_start = after.split("|", 1)
            assert got_pane == tree_pane, "the repair changed the pane's id"
            assert module.pane_start_command(got_start) == module.local_pane_command(4242), got_start
            # a pane that already carries the pin is not respawned for a child: re-running
            # the pin cannot change what that child resolved, so it is named once instead
            module._DRIFT_TRIED.clear()
            module._DRIFT_HELD.clear()
            held_rows = [{"pane": tree_pane, "pid": tree_pid, "window": "@1",
                          "start": module.local_pane_command(4242)}]
            assert module.repair_drifted_panes({4242: "@1"}, None, rows=held_rows,
                                               table=drift_table) == [], \
                "a pane already carrying the pin was respawned for a child"
            since = pane_log_text()[len(log_before_tree):]
            assert "a child (pid" in since and "carries this build's pin" in since, \
                since[-300:]
        finally:
            set_knob(module, "TMUX_BIN", saved_tmux)
            subprocess.run(tmux_tree + ["kill-server"], capture_output=True)
        say("pane tree drift: a child under a pane on another interpreter is the drift too, "
            "and the pane is reopened on the pin — or the child is named once when the pin "
            "cannot change it: ok")

    # ---- the keeper starts itself over when the build under it changes — the third of the
    #      three long-running images to learn it (the pane first, then the watcher, then this
    #      one): a keeper left on an old build would keep reopening panes with its OLDER pin,
    #      so every repair it made would be made by code it no longer is. Driven through a
    #      real keeper, on a private tmux server and a COPY of the build (never this checkout,
    #      whose sources belong to the owner's own pane, watcher and keeper), and the claim
    #      has to cross the exec and be taken back on the other side: the keeper is the one
    #      process that must not stand down in between, or the panes go unwatched with a
    #      record still naming it. A source that does not parse must HOLD — the same promise
    #      the watcher makes — never exec into a save that is still landing.
    if shutil.which("tmux"):
        keep_sock = sock("fbtkeepsock")
        tmux_keep = ["tmux", "-L", keep_sock]
        subprocess.run(tmux_keep + ["kill-server"], capture_output=True)
        made = subprocess.run(
            tmux_keep + [
                "new-session", "-d", "-s", "keepsess", "-x", "80", "-y", "24",
                "-c", TEST_HOME,
                "-e", f"FBTODO_HOME={TEST_HOME}",
                "-e", "FBTODO_PANE_SECONDS=0",
                "-e", "FBTODO_NO_AUTOSTART=1",
                "sleep 300",
            ],
            capture_output=True, text=True,
        )
        assert made.returncode == 0, made.stderr
        keeper_build = os.path.join(TEST_HOME, "keeper-reload-build")
        shutil.rmtree(keeper_build, ignore_errors=True)
        os.makedirs(keeper_build)
        shutil.copytree(os.path.join(ROOT, "src", "fbtodo"),
                        os.path.join(keeper_build, "src", "fbtodo"))
        shutil.copy(os.path.join(ROOT, "fbtodo"), os.path.join(keeper_build, "fbtodo"))
        keeper_src = os.path.join(keeper_build, "src", "fbtodo")
        keep_env = dict(env, FBTODO_TMUX=f"tmux -L {keep_sock}")
        keep = subprocess.Popen(
            [sys.executable, os.path.join(keeper_build, "fbtodo"), "pane-watch",
             "--foreground", "--quiet", "--pane-seconds", "0.5"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            start_new_session=True, env=keep_env, cwd=CWD,
        )

        def keeper_log_text() -> str:
            try:
                with open(module.PANE_LOG_PATH, encoding="utf-8") as fh:
                    return fh.read()
            except OSError:
                return ""

        def keeper_stamp() -> int:
            return int((module.read_json(module.PANE_KEEPER_PATH, {}) or {}).get("started_ms") or 0)

        try:
            assert wait_for(lambda: module.lock_holder(module.PANE_KEEPER_PATH) == keep.pid), \
                "the copied keeper never claimed"
            stamp = keeper_stamp()
            assert stamp, "the copied keeper wrote no record"
            # Only what THIS keeper wrote: the log is append-only across the whole suite.
            base_len = len(keeper_log_text())

            def keeper_since() -> str:
                return keeper_log_text()[base_len:]

            # a source newer than the keeper landed: it must start itself over, keeping both
            # the pid and the claim (a stand-down here would leave nobody keeping a pane)
            with open(os.path.join(keeper_src, "zz_reload.py"), "w") as fh:
                fh.write("x = 1\n")
            assert wait_for(lambda: "keeper reloading:" in keeper_since()), keeper_since()[-400:]
            assert keep.poll() is None and os.path.exists(module.PANE_KEEPER_PATH), \
                "the reload took the keeper down"
            assert module.lock_holder(module.PANE_KEEPER_PATH) == keep.pid, \
                "the claim changed hands across the keeper's reload"
            # ...and the record's clock is the proof the image AFTER the exec is the one
            # serving: only a keeper that adopted the claim and ran writes it afresh.
            assert wait_for(lambda: keeper_stamp() > stamp), \
                "the reloaded keeper never refreshed its record"
            stamp = keeper_stamp()
            # a source that does not parse: hold, and do not exec into it
            with open(os.path.join(keeper_src, "zz_broken.py"), "w") as fh:
                fh.write("def half(\n")
            assert wait_for(lambda: "keeper holding" in keeper_since()), keeper_since()[-400:]
            assert quiet_for(lambda: keeper_since().count("keeper reloading:") != 1,
                             hold_window()), "it reloaded into a broken file"
            assert keep.poll() is None, "the keeper died on a source that does not parse"
            assert keeper_since().count("keeper reloading:") == 1, \
                "it reloaded into a broken file"
            os.remove(os.path.join(keeper_src, "zz_broken.py"))
            with open(os.path.join(keeper_src, "zz_reload2.py"), "w") as fh:
                fh.write("y = 2\n")
            assert wait_for(lambda: keeper_since().count("keeper reloading:") == 2), \
                keeper_since()[-400:]
            assert module.lock_holder(module.PANE_KEEPER_PATH) == keep.pid and keep.poll() is None
            assert wait_for(lambda: keeper_stamp() > stamp), \
                "the second reload left nobody keeping"
            # ...and a tree that PARSES but will not load is held on, not exec'd into: the
            # keeper would otherwise replace itself with one that never starts, leaving every
            # pane unwatched. `source_syntax_error` is silent (every file compiles), so the
            # probe (`reload_probe_error`) is what must hold it.
            render_keep = os.path.join(keeper_src, "render.py")
            with open(render_keep) as fh:
                keep_orig = fh.read()
            with open(render_keep, "a") as fh:
                fh.write("\nraise RuntimeError('internally inconsistent')\n")
            with open(os.path.join(keeper_src, "zz_reload3.py"), "w") as fh:
                fh.write("z = 3\n")
            assert wait_for(lambda: "does not load" in keeper_since()), keeper_since()[-500:]
            assert module.source_syntax_error(here=render_keep) is None, \
                "the unloadable keeper tree parses cleanly, so the probe was what held it"
            assert quiet_for(lambda: keeper_since().count("keeper reloading:") != 2,
                             hold_window()), "it reloaded into an unfit keeper build"
            assert keep.poll() is None, "the keeper exec'd into a build that will not load"
            assert module.lock_holder(module.PANE_KEEPER_PATH) == keep.pid, \
                "the claim moved on a held keeper reload"
            assert keeper_since().count("keeper reloading:") == 2, \
                "it reloaded into an unfit keeper build"
            with open(render_keep, "w") as fh:
                fh.write(keep_orig)
            assert wait_for(lambda: keeper_since().count("keeper reloading:") == 3), \
                keeper_since()[-500:]
            assert keep.poll() is None and module.lock_holder(module.PANE_KEEPER_PATH) == keep.pid
            say("reload: the pane keeper replaces itself across an exec — claim, pid and "
                "record — and holds while the sources do not parse or the new build will "
                "not load: ok")
        finally:
            keep.send_signal(signal.SIGTERM)
            deadline = time.time() + 10
            while time.time() < deadline and keep.poll() is None:
                time.sleep(0.1)
            kill_tree(keep)
            deadline = time.time() + 10
            while time.time() < deadline and module.lock_holder(module.PANE_KEEPER_PATH):
                time.sleep(0.1)
            assert module.lock_holder(module.PANE_KEEPER_PATH) is None, \
                "the keeper's claim survived it"
            subprocess.run(tmux_keep + ["kill-server"], capture_output=True)
            shutil.rmtree(keeper_build, ignore_errors=True)

    # ---- one keeper per SERVER, and a server has one name. A pane spells its tmux as the
    #      `TMUX` value — socket, server pid, session id — while the desktop integration,
    #      launched outside tmux, has no `TMUX` at all and used to say only `-default`. Both
    #      name the same server, and compared raw they read as two: measured 2026-10-02,
    #      each ask killed the other's keeper several times an hour (`replacing keeper …`),
    #      leaving the panes unwatched until the next ask. The name is now asked of the
    #      server itself (`display-message -p '#{socket_path}'`), which answers the same
    #      string inside a session and outside one, and an ask that cannot name a server at
    #      all does not evict a keeper it cannot judge — only one for a genuinely different
    #      server, or from an older build, is replaced.
    if shutil.which("tmux"):
        ident_sock = sock("fbtidentsock")
        tmux_ident = ["tmux", "-L", ident_sock]
        subprocess.run(tmux_ident + ["kill-server"], capture_output=True)
        made = subprocess.run(
            tmux_ident + [
                "new-session", "-d", "-s", "identsess", "-x", "80", "-y", "24",
                "-c", TEST_HOME,
                "-e", f"FBTODO_HOME={TEST_HOME}",
                "-e", "FBTODO_NO_AUTOSTART=1",
                "sleep 300",
            ],
            capture_output=True, text=True,
        )
        assert made.returncode == 0, made.stderr
        saved_tmux_ident = list(module.TMUX_BIN)
        saved_env_ident = {k: os.environ.get(k) for k in ("TMUX", "FBTODO_TMUX")}
        set_knob(module, "TMUX_BIN", list(tmux_ident))
        try:
            #      `ident_path`, not `sock`: this is module level, so a bare `sock` here
            #      would replace the helper every later line — including the later `sock2 = sock(...)`
            #      and the teardown that kills these servers — with a string.
            ident_path = subprocess.run(
                tmux_ident + ["display-message", "-p", "#{socket_path}"],
                capture_output=True, text=True,
            ).stdout.strip()
            assert ident_path, "the private server did not answer its own socket path"
            os.environ.pop("FBTODO_TMUX", None)
            # a `TMUX` value carries a server pid and a session id besides the socket, and
            # two panes on one server differ in exactly those: neither changes the name
            os.environ["TMUX"] = f"{ident_path},4242,7"
            assert module.tmux_identity() == os.path.realpath(ident_path), module.tmux_identity()
            os.environ["TMUX"] = f"{ident_path},9999,8"
            assert module.tmux_identity() == os.path.realpath(ident_path), \
                "a second session on the same server got a name of its own"
            # ...and the desktop integration's context: outside tmux, no `TMUX` at all
            del os.environ["TMUX"]
            assert module.tmux_identity() == os.path.realpath(ident_path), \
                "an ask outside tmux named a different server than the one it talks to"
            # a forced server is named deliberately and verbatim, the same string anywhere
            os.environ["FBTODO_TMUX"] = f"tmux -L {ident_sock}"
            assert module.tmux_identity() == f"tmux -L {ident_sock}"
            del os.environ["FBTODO_TMUX"]
            # with no server to ask and no `TMUX` to read there is no name to be had — and
            # `-default` must not stand in for one, which is how the two askers fought
            set_knob(module, "TMUX_BIN", ["tmux", "-L", "fbtidentgone"])
            assert module.tmux_identity() is None, module.tmux_identity()
            os.environ["TMUX"] = "/private/tmp/tmux-501/gone,1,2"
            assert module.tmux_identity() == os.path.realpath("/private/tmp/tmux-501/gone"), \
                "with a dead server, `TMUX` is the only name there is"
            assert module.tmux_identity_outside() is None, \
                "the outside ask fell back to the pane's `TMUX`"
            del os.environ["TMUX"]
            set_knob(module, "TMUX_BIN", list(tmux_ident))
            # ...and the rule the churn broke, before any process is started
            same = {"version": module.VERSION, "tmux": ident_path}
            assert module.keeper_serves(same, ident_path) is True
            assert module.keeper_serves(same, None) is True, \
                "an ask that cannot name a server evicted one it cannot judge"
            assert module.keeper_serves(same, "/x/other") is False, \
                "an ask naming another server kept a keeper that cannot serve it"
            assert module.keeper_serves({"version": "0.0.0", "tmux": ident_path}, ident_path) is False, \
                "a keeper from an older build was kept"
            assert module.keeper_serves({}, None) is False, "a lock with no record is no keeper"
            # ...and the comparison is a SERVER comparison, not a string one: the spellings
            # a record and an ask can meet in — the raw `TMUX` value, the old `-default`, a
            # forced `-L`/`-S`, the canonical path — are reduced to the socket each denotes,
            # and two of them are one server exactly when their sockets are one file.
            link = os.path.join(TEST_HOME, "tmux-link")
            real = os.path.join(TEST_HOME, "tmux-real")
            os.makedirs(real, exist_ok=True)
            if not os.path.islink(link):
                os.symlink(real, link)
            assert module.same_tmux_server(os.path.join(link, "s"), os.path.join(real, "s")), \
                "two spellings of one path were read as two servers"
            assert module.same_tmux_server(f"{ident_path},4242,7", ident_path), "a raw `TMUX` value"
            default_sock = os.path.realpath(os.path.join(
                os.environ.get("TMUX_TMPDIR") or "/tmp", f"tmux-{os.getuid()}", "default"))
            assert module.same_tmux_server("-default", default_sock), \
                "the old placeholder stopped meaning the default server"
            assert module.same_tmux_server(f"tmux -L {ident_sock}", ident_path), \
                "a forced server written as a name and as its socket"
            assert module.same_tmux_server(f"tmux -S {ident_path}", ident_path)
            assert not module.same_tmux_server("-default", ident_path), \
                "the default server and a private one were read as one"
            assert not module.same_tmux_server("/x/one", "/x/two"), \
                "distinct names were read as one"
            assert not module.same_tmux_server(None, "/x/one") and \
                module.same_tmux_server(None, None), "nameless names"
            assert module.same_tmux_server("/x/one", "/x/one"), "equal names were read as two"
            # TMUX_TMPDIR moves tmux's whole socket directory, and a forced name follows it
            os.environ["TMUX_TMPDIR"] = os.path.join(TEST_HOME, "tmuxdir")
            moved = os.path.realpath(os.path.join(
                os.environ["TMUX_TMPDIR"], f"tmux-{os.getuid()}", ident_sock))
            assert module.tmux_socket_of(f"tmux -L {ident_sock}") == moved, \
                "a forced `-L` ignored TMUX_TMPDIR"
            os.environ.pop("TMUX_TMPDIR")
            say("keeper identity: names written by different builds are compared as servers, "
                "not as strings — the raw `TMUX` value, `-default` and a forced server all "
                "name the socket they denote: ok")

            # ...and then the two asks that used to fight, against a real keeper: the
            # pane's (with `TMUX`), the desktop integration's (none) and a second pane
            # session's — one keeper across all three, its record naming the server once,
            # canonically. The keeper is spawned from this checkout on purpose (nothing
            # here edits the sources), pointed at the private server by `TMUX` and at the
            # throwaway home by the environment it inherits.
            ident_args = types.SimpleNamespace(pane_seconds=2.0, quiet=True)

            def ident_log() -> str:
                try:
                    with open(module.PANE_LOG_PATH, encoding="utf-8") as fh:
                        return fh.read()
                except OSError:
                    return ""

            base_len = len(ident_log())
            # The status context for everything below: a pane on the private server
            # (`TMUX`) with the server forced by name (`FBTODO_TMUX`), so one run reads
            # both asks. `keeper server` names them, and `--json` carries the same facts
            # for a script: the server, both asks, and which ask would replace the keeper.
            env_status = dict(env, FBTODO_TMUX=f"tmux -L {ident_sock}", TMUX=f"{ident_path},4242,7")

            def keeper_status_line() -> str:
                out = subprocess.run(
                    [sys.executable, FB, "status"], capture_output=True, text=True,
                    env=env_status, cwd=CWD,
                ).stdout
                rows = [ln for ln in STRIP(out).splitlines() if "keeper server" in ln]
                assert rows, out[-500:]
                return rows[0]

            def keeper_status_json() -> dict:
                out = subprocess.run(
                    [sys.executable, FB, "status", "--json"], capture_output=True, text=True,
                    env=env_status, cwd=CWD,
                ).stdout
                doc = json.loads(out)
                assert "keeper" in doc, sorted(doc)
                return doc["keeper"]

            # nothing running yet: the machine-readable answer says so, and names no server
            none_yet = keeper_status_json()
            assert none_yet["running"] is False and none_yet["server"] is None, none_yet
            assert none_yet["would_replace"] == [] and none_yet["agree"] is False, none_yet
            os.environ["TMUX"] = f"{ident_path},4242,7"
            first = module.ensure_pane_keeper(ident_args)
            assert first, "the first ask started no keeper"
            assert wait_for(lambda: module.lock_holder(module.PANE_KEEPER_PATH) == first), \
                "the keeper never claimed"
            assert wait_for(
                lambda: (module.read_json(module.PANE_KEEPER_PATH, {}) or {}).get("tmux")
                == os.path.realpath(ident_path)
            ), (module.read_json(module.PANE_KEEPER_PATH, {}) or {})
            del os.environ["TMUX"]
            assert module.ensure_pane_keeper(ident_args) == first, \
                "the desktop integration replaced the pane's keeper"
            os.environ["TMUX"] = f"{ident_path},9999,8"
            assert module.ensure_pane_keeper(ident_args) == first, \
                "a second session's ask replaced the keeper"
            assert "replacing keeper" not in ident_log()[base_len:], ident_log()[base_len:][-300:]
            # ...and `status` names both at once: the server the keeper holds and whether
            # the asks that could replace it agree. The pane's ask is read from a pane
            # context (`TMUX`), the watcher's from outside tmux — here both point at the
            # forced private server, the way the desktop integration names one.
            line = keeper_status_line()
            assert os.path.realpath(ident_path) in line, line
            assert "asks name it" in line, line
            # ...and the same facts for a script, from the same reader: the server, both
            # asks reduced to the sockets they name, and no churn due
            view = keeper_status_json()
            assert view["running"] is True and view["pid"] == first, view
            assert view["server"] == os.path.realpath(ident_path), view
            assert view["recorded"] == os.path.realpath(ident_path), view
            assert view["asks"] == {"pane": os.path.realpath(ident_path),
                                    "watcher": os.path.realpath(ident_path)}, view
            assert view["agree"] is True and view["would_replace"] == [], view
            assert "asks name it" in view["note"], view
            # ...and a record written by an OLDER build — the raw `TMUX` value, which names
            # this same server with a server pid and a session id on it — is that server
            # too: a different spelling, not a reason to replace the keeper.
            rec = module.read_json(module.PANE_KEEPER_PATH, {}) or {}
            rec["tmux"] = f"{ident_path},4242,7"
            with open(module.PANE_KEEPER_PATH, "w", encoding="utf-8") as fh:
                json.dump(rec, fh)
            assert module.ensure_pane_keeper(ident_args) == first, \
                "an older build's spelling of the same server replaced the keeper"
            assert "replacing keeper" not in ident_log()[base_len:], ident_log()[base_len:][-300:]
            # the record's spelling is old, the server is not: `status` prints the name it
            # denotes and says where the spelling came from, without calling it a difference
            line = keeper_status_line()
            assert os.path.realpath(ident_path) in line and "recorded as" in line, line
            assert "asks name it" in line, line
            view = keeper_status_json()
            assert view["server"] == os.path.realpath(ident_path), view
            assert view["recorded"] == f"{ident_path},4242,7", view
            assert view["agree"] is True and view["would_replace"] == [], view
            say("status: the keeper's server is named canonically, and the asks that could "
                "replace it are compared: ok")
            # ...and the snapshot is not the only way to read it: `status --watch` prints
            # the keeper's state once and then only CHANGES — the pid moving (replaced,
            # gone, appeared), churn offered, churn cleared — and stays silent while the
            # keeper stands still. Text for a person, and `--json` one event object per
            # line for a script, carrying the same facts the snapshot's `keeper` object
            # does. The record below is driven by hand at `-i 0.2`, so the test runs at
            # the speed of its mutations rather than of real churn.
            watch_log = os.path.join(TEST_HOME, "status-watch.log")

            def watch_text() -> str:
                try:
                    with open(watch_log, encoding="utf-8") as fh:
                        return fh.read()
                except OSError:
                    return ""

            settled = module.read_json(module.PANE_KEEPER_PATH, {}) or {}
            churn_rec = {**settled, "tmux": "/private/tmp/tmux-501/elsewhere"}
            watch_env = dict(env_status, FBTODO_HOME=TEST_HOME)

            def write_record(payload: dict) -> None:
                # In place, like the identity rows above: the record IS the claim file, and
                # the keeper holds its lock — a rename over the name would orphan that
                # claim, and the next ask would start a second keeper instead of replacing
                # this one.
                with open(module.PANE_KEEPER_PATH, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh)

            def start_watch(*extra):
                handle = open(watch_log, "w", encoding="utf-8")
                proc = subprocess.Popen(
                    [sys.executable, FB, "status", "--watch", "-i", "0.2", *extra],
                    stdout=handle, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                    env=watch_env, cwd=CWD, start_new_session=True,
                )
                return proc, handle

            watch, watch_handle = start_watch()
            try:
                assert wait_for(lambda: "now     keeper : " in watch_text()), \
                    watch_text()[-300:]
                base = watch_text()
                assert f"now     keeper : {first}  (" in base, base
                # nothing moved, nothing said — the silence is half the contract
                time.sleep(0.6)
                assert watch_text() == base, "the watch spoke with the keeper standing still"
                # the pid moved, then the keeper stopped answering, then came back
                write_record({**settled, "pid": os.getpid()})
                assert wait_for(lambda: f"pid     keeper : {os.getpid()}  "
                                        f"(was {first}, replaced)" in watch_text()), \
                    watch_text()[-400:]
                write_record({**settled, "pid": 999999999})
                assert wait_for(lambda: f"gone    keeper : — none running  "
                                        f"(was {os.getpid()})" in watch_text()), \
                    watch_text()[-400:]
                write_record(settled)
                assert wait_for(lambda: f"pid     keeper : {first}  (appeared)" in watch_text()), \
                    watch_text()[-400:]
                # churn offered, then cleared — the row's own words, as events
                write_record(churn_rec)
                assert wait_for(lambda: "churn   keeper : " in watch_text()), \
                    watch_text()[-400:]
                assert "the next ask replaces this keeper" in watch_text(), watch_text()[-400:]
                write_record(settled)
                assert wait_for(lambda: "agree   keeper : " in watch_text()), watch_text()[-400:]
                quiet = watch_text()
                time.sleep(0.6)
                assert watch_text() == quiet, "the watch spoke after the churn cleared"
            finally:
                watch.terminate()
                watch.wait(timeout=10)
                watch_handle.close()
                write_record(settled)
            # ...and the same stream as JSON: one object per line, each naming the event
            # and carrying the state the snapshot's `keeper` object would.
            watch, watch_handle = start_watch("--json")
            try:
                assert wait_for(lambda: watch_text().strip()), watch_text()
                event = json.loads(watch_text().strip().splitlines()[0])
                assert event["event"] == "now" and event["pid"] == first, event
                assert event["would_replace"] == [] and event["agree"] is True, event
                assert isinstance(event["at_ms"], int) and event["note"], event
                write_record(churn_rec)
                assert wait_for(lambda: '"churn"' in watch_text()), watch_text()[-300:]
                churn = json.loads(watch_text().strip().splitlines()[-1])
                assert churn["event"] == "churn" and churn["would_replace"], churn
                assert churn["pid"] == first and churn["previous_pid"] == first, churn
            finally:
                watch.terminate()
                watch.wait(timeout=10)
                watch_handle.close()
                write_record(settled)
            say("status --watch reports the keeper's changes only — a pid that moved, one "
                "that stopped answering, churn offered and cleared — in text and as one "
                "JSON object per line, and says nothing while the keeper stands still: ok")
            # ...and a keeper recorded for a DIFFERENT server is still replaced: the rule
            # must evict what it can judge foreign, or another server's keeper would keep
            # answering for this one.
            rec = module.read_json(module.PANE_KEEPER_PATH, {}) or {}
            rec["tmux"] = "/private/tmp/tmux-501/elsewhere"
            with open(module.PANE_KEEPER_PATH, "w", encoding="utf-8") as fh:
                json.dump(rec, fh)
            # a script can see the churn coming: both asks name a server this keeper is not
            churn = keeper_status_json()
            assert churn["server"] == "/private/tmp/tmux-501/elsewhere", churn
            assert churn["would_replace"] == ["pane", "watcher"], churn
            assert churn["agree"] is False, churn
            assert "replaces this keeper" in churn["note"], churn
            os.environ["TMUX"] = f"{ident_path},4242,7"
            replaced = module.ensure_pane_keeper(ident_args)
            assert replaced and replaced != first, "a keeper for another server was kept"
            assert "replacing keeper" in ident_log()[base_len:], "the replacement was not logged"
            assert wait_for(lambda: module.lock_holder(module.PANE_KEEPER_PATH) == replaced), \
                "the replacement never claimed"
            for _keeper in (first, replaced):
                try:
                    os.kill(int(_keeper), signal.SIGTERM)
                except OSError:
                    pass
            assert wait_for(lambda: module.lock_holder(module.PANE_KEEPER_PATH) is None), \
                "a keeper's claim survived it"
            say("keeper identity: the pane's ask and the desktop integration's name the same "
                "server and share one keeper, a second session's ask does not disturb it, "
                "and a keeper for a different server is still replaced: ok")

            # ...and two asks arriving TOGETHER converge on one keeper. Each starts a keeper
            # before either has claimed; exactly one wins the claim, the other sees the
            # winner and stands down — and the ask that spawned the stand-down returns the
            # WINNER, silently. It used to log "never claimed the lock" instead: the loser's
            # own poll could remove the winner's newborn claim before it was locked, and
            # then no ask — and no reader — could see a keeper that was running. Threads, so
            # the asks really overlap; the keepers are processes either way.
            race_mark = len(ident_log())
            race_pids = []
            race_line = threading.Barrier(2)

            def race_ask():
                race_line.wait()
                race_pids.append(module.ensure_pane_keeper(ident_args))

            racers = [threading.Thread(target=race_ask) for _ in range(2)]
            for racer in racers:
                racer.start()
            for racer in racers:
                racer.join()
            assert len(race_pids) == 2 and all(race_pids), race_pids
            assert race_pids[0] == race_pids[1], \
                f"two simultaneous first asks did not converge: {race_pids}"
            assert module.lock_holder(module.PANE_KEEPER_PATH) == race_pids[0], \
                "the converged keeper does not hold the claim"
            race_log = ident_log()[race_mark:]
            assert "never claimed" not in race_log, race_log[-400:]
            assert "replacing keeper" not in race_log, race_log[-400:]
            assert race_log.count("keeper started") == 1, race_log[-400:]
            try:
                os.kill(int(race_pids[0]), signal.SIGTERM)
            except OSError:
                pass
            assert wait_for(lambda: module.lock_holder(module.PANE_KEEPER_PATH) is None), \
                "the converged keeper's claim survived it"
            say("keeper race: two asks arriving together converge on one keeper, and the "
                "ask that lost adopts the winner instead of logging a failure: ok")

            # ---- ...and the keeper HEALS its own claim. A claim is a lock AND a name, and
            #      the name half can go without the lock going: the file is removed or
            #      replaced under a running keeper (a probe's leftover cleanup, an admin's
            #      `rm`), and the kernel lock survives on the unlinked inode. Left alone the
            #      keeper watches panes invisibly — `claim_audit` reads the role `absent`,
            #      the process table calls it an orphan, and `locks --fix` ends it — so the
            #      keeper takes its name back itself, within a tick: `locks` then has no
            #      orphan to offer and no kill to plan. Both accidents, in order.
            healer = module.ensure_pane_keeper(ident_args)
            assert healer, "the healing test's keeper never started"
            assert wait_for(lambda: module.lock_holder(module.PANE_KEEPER_PATH) == healer), \
                "the healing test's keeper never claimed"
            first_rec = module.read_json(module.PANE_KEEPER_PATH, {}) or {}
            os.unlink(module.PANE_KEEPER_PATH)  # the name is gone; the keeper is not
            assert wait_for(
                lambda: module.lock_holder(module.PANE_KEEPER_PATH) == healer, seconds=8
            ), "the keeper did not take its removed name back"
            healed = module.read_json(module.PANE_KEEPER_PATH, {}) or {}
            assert healed.get("pid") == healer, (first_rec, healed)
            assert healed.get("tmux") == first_rec.get("tmux"), (first_rec, healed)
            assert healed.get("started_ms") == first_rec.get("started_ms"), \
                "the re-claimed record forgot when the keeper started"
            assert wait_for(lambda: "keeper re-claimed its name" in ident_log()), \
                "the re-claim was not written down"
            # ...and a name REPLACED by another file is the same story. A fresh INODE is
            # what replaced means: writing into the file the keeper holds keeps the same
            # lock and only rewrites the record.
            incoming = module.PANE_KEEPER_PATH + ".incoming"
            with open(incoming, "w", encoding="utf-8") as fh:
                json.dump({"pid": 1, "version": "0.0.1"}, fh)
            os.replace(incoming, module.PANE_KEEPER_PATH)
            assert wait_for(
                lambda: module.lock_holder(module.PANE_KEEPER_PATH) == healer, seconds=8
            ), "the keeper did not take its replaced name back"
            healed = module.read_json(module.PANE_KEEPER_PATH, {}) or {}
            assert healed.get("pid") == healer and healed.get("version") == module.VERSION, \
                healed
            assert healed.get("started_ms") == first_rec.get("started_ms"), healed
            doc = json.loads(run("locks", "--json").stdout)
            keeper_row = next(c for c in doc["claims"] if c["role"] == "keeper")
            assert keeper_row["state"] == "held" and keeper_row["pid"] == healer, keeper_row
            assert keeper_row["orphans"] == [], keeper_row
            assert json.loads(run("locks", "--fix", "--json", "--dry-run").stdout) \
                ["planned"]["kills"] == [], "the fix planned to end a keeper that healed"
            say("keeper claim: a name removed or replaced under the keeper is taken back "
                "by the keeper itself — same pid, same server and start time, no orphan "
                "left behind and nothing for `locks --fix` to end: ok")
            try:
                os.kill(int(healer), signal.SIGTERM)
            except OSError:
                pass
            assert wait_for(lambda: module.lock_holder(module.PANE_KEEPER_PATH) is None), \
                "the healer's claim survived it"
            try:
                os.unlink(module.PANE_KEEPER_PATH)  # no name under the stand-in either
            except OSError:
                pass

            # ---- ...and an untied keeper that CANNOT heal — one from an older build,
            #      whose loop knows nothing of the re-claim. The name-based read is blind to
            #      it (`claim_audit` says `absent`), so the process table is the only place
            #      it can be found: the audit must still name it, and `locks --fix` must
            #      still be the hand for it. A stand-in process with a keeper's argv shape
            #      and nothing else of it — the cross-check reads exactly three things off
            #      it, its command line, its environment and its age, and this is those
            #      three. Its environment is short on purpose: `ps -Eww` truncates long
            #      ones, and a root the cross-check cannot read is one it cannot place.
            fake_pkg = os.path.join(TEST_HOME, "old-build", "fbtodo")
            os.makedirs(fake_pkg, mode=0o700, exist_ok=True)
            with open(os.path.join(fake_pkg, "__init__.py"), "w", encoding="utf-8") as fh:
                fh.write("import time\n\ntime.sleep(600)\n")
            orphan = subprocess.Popen(
                [sys.executable, os.path.join(fake_pkg, "__init__.py"), "pane-watch"],
                env={"FBTODO_HOME": TEST_HOME, "PATH": os.environ.get("PATH", "")},
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, start_new_session=True,
            )
            orphan_keeper = orphan.pid
            # Long enough for the stand-in keeper to have finished its first pass and written
            # (or, here, failed to write) its claim, taken from the keeper's OWN claim-check
            # interval rather than typed in: a fixed 2.5 s is a number that silently stops
            # meaning "one pass" the moment that interval changes.
            time.sleep(module.KEEPER_CLAIM_CHECK_S * 2.5)
            blind = module.claim_audit(module.PANE_KEEPER_PATH)
            assert blind["state"] == "absent" and blind["pid"] is None, blind
            seen_orphan = run("locks")
            assert seen_orphan.returncode == 0, (seen_orphan.returncode, seen_orphan.stderr)
            out = STRIP(seen_orphan.stdout)
            assert f"running: pid {orphan_keeper} (keeper) with this state root" in out, out
            assert "no file — no claim" in out, out  # the name-based half is blind
            doc = json.loads(run("locks", "--json").stdout)
            keeper_row = next(c for c in doc["claims"] if c["role"] == "keeper")
            assert keeper_row["state"] == "absent", keeper_row
            assert [o["pid"] for o in keeper_row["orphans"]] == [orphan_keeper], keeper_row
            assert keeper_row["orphans"][0]["role"] == "keeper", keeper_row["orphans"][0]
            say("locks: an untied keeper that does not heal — a build older than the "
                "re-claim — is still named: the file reads absent, and the process table "
                "says who is running: ok")
            # ...and `locks --fix` is the hand for that finding. A dry run names it and
            # changes nothing; a run that cannot ask refuses with 66 and still changes
            # nothing (a kill is never taken on a guess, and never half-applied); `--yes`
            # ends it, which is the only repair there is — its lock lives on an unlinked
            # file, so nothing on disk can free it.
            dry_fix = run("locks", "--fix", "--dry-run")
            assert dry_fix.returncode == 0 and "would end" in STRIP(dry_fix.stdout), \
                dry_fix.stdout
            assert module.pid_alive(orphan_keeper), "--dry-run ended a process"
            refused = subprocess.run(
                [sys.executable, FB, "locks", "--fix"], capture_output=True, text=True,
                env=env, cwd=CWD, stdin=subprocess.DEVNULL, timeout=60,
            )
            assert refused.returncode == 66, (refused.returncode, refused.stdout[-200:])
            assert "without confirmation" in refused.stderr, refused.stderr
            assert module.pid_alive(orphan_keeper), "a refused fix ended a process"
            fixed_fix = run("locks", "--fix", "--yes", "--json")
            doc_fix = json.loads(fixed_fix.stdout)
            assert fixed_fix.returncode == 0, (fixed_fix.returncode, fixed_fix.stderr[-200:],
                                               doc_fix["planned"], doc_fix["ended"])
            assert orphan_keeper in [k["pid"] for k in doc_fix["planned"]["kills"]], doc_fix
            ended_row = next(e for e in doc_fix["ended"] if e["pid"] == orphan_keeper)
            assert ended_row == {"role": "keeper", "pid": orphan_keeper, "outcome": "ended"}, \
                doc_fix["ended"]
            # `pid_running`, not `pid_alive`: this keeper is the SUITE's own child, and an
            # unreaped one keeps answering `kill(pid, 0)` after it is gone — the exact
            # distinction the fix had to learn (see `pid_running`).
            assert wait_for(lambda: not module.pid_running(orphan_keeper)), \
                "the untied keeper survived the fix"
            assert not os.path.exists(module.PANE_KEEPER_PATH)
            assert json.loads(run("locks", "--fix", "--json", "--yes").stdout)["planned"] \
                ["kills"] == [], "the fix planned the same kill twice"
            say("locks --fix: an untied keeper is named by a dry run, a run without "
                "confirmation refuses with 66 and changes nothing, and `--yes` ends it: ok")
            try:
                os.kill(int(orphan_keeper), signal.SIGTERM)
            except OSError:
                pass
            # Reaped, not merely signalled: an unreaped child stays a zombie, which is what
            # `pid_running` exists to see through (see the fix's own wait). The suite leaves
            # no process of its own behind either way.
            try:
                orphan.wait(timeout=10)
            except subprocess.TimeoutExpired:
                orphan.kill()
            assert wait_for(lambda: module.lock_holder(module.PANE_KEEPER_PATH) is None), \
                "the orphan's name came back"
        finally:
            holder = module.lock_holder(module.PANE_KEEPER_PATH)
            if holder:
                try:
                    os.kill(int(holder), signal.SIGTERM)
                except OSError:
                    pass
                deadline = time.time() + 10
                while time.time() < deadline and module.lock_holder(module.PANE_KEEPER_PATH):
                    time.sleep(0.1)
                if module.lock_holder(module.PANE_KEEPER_PATH):
                    # it would not go: `start_new_session` made it the leader of its group
                    try:
                        os.killpg(int(holder), signal.SIGKILL)
                    except OSError:
                        pass
            set_knob(module, "TMUX_BIN", saved_tmux_ident)
            for _key, _val in saved_env_ident.items():
                if _val is None:
                    os.environ.pop(_key, None)
                else:
                    os.environ[_key] = _val
            subprocess.run(tmux_ident + ["kill-server"], capture_output=True)

    # ---- a pane that reloaded says WHICH BUILD it is now, on its own title chip, for a few
    #      seconds. The old image cannot know the new build's `VERSION` — that is in code it
    #      never imported — so it leaves its own behind in the environment (which, with the
    #      open fds, is what an exec carries) and the new image answers with the number it is
    #      running. The chip is the one slot of the frame that belongs to the PROCESS rather
    #      than the list, so the note must cost no row and move nothing: the frame has to be
    #      identical but for its top border.
    assert module.reload_note(None) is None and module.reload_note("not json") is None, \
        "a hand-over that is not a hand-over was read as one"
    assert module.reload_note(json.dumps({"from": module.VERSION, "file": "base.py"})) == \
        f"RELOADED {module.VERSION}", "an edit between releases still names the build"
    assert module.reload_note(json.dumps({"from": "1.2.3", "file": "base.py"})) == \
        f"RELOADED 1.2.3 → {module.VERSION}", "an upgrade names the build it came from"
    st_note = {
        "backend": "cli", "session": "S", "todos": [{"task": "t", "completed": False}],
        "done": 0, "total": 1, "goal": "a note on the chip", "model": "gpt-5",
    }
    now_note = 1_700_000_000_000
    plain_frame = module.render(st_note, True, watching=4242, width=68, now_ms=now_note)
    noted_frame = module.render(st_note, True, watching=4242, width=68, now_ms=now_note,
                                reloaded=f"RELOADED 1.2.3 → {module.VERSION}")
    a_rows, b_rows = plain_frame.splitlines(), noted_frame.splitlines()
    assert len(a_rows) == len(b_rows), "the note changed how many rows the frame has"
    changed = [i for i, (a, b) in enumerate(zip(a_rows, b_rows)) if a != b]
    assert changed == [0], f"the note disturbed the frame: rows {changed} changed"
    assert "FREEBUFF TODOS" in STRIP(a_rows[0]) and "FREEBUFF TODOS" not in STRIP(b_rows[0])
    assert f"RELOADED 1.2.3 → {module.VERSION}" in STRIP(b_rows[0]), STRIP(b_rows[0])
    assert "watcher: pid 4242" in STRIP(b_rows[0]), "the note cost the border its metadata"
    # a narrow pane gives up the note's tail, never the right-hand slot's existence
    for w in (34, 40, 68, 80):
        line = STRIP(module.render(st_note, True, watching=4242, width=w, now_ms=now_note,
                                   reloaded=f"RELOADED 1.2.3 → {module.VERSION}").splitlines()[0])
        assert module._cell_width(line) <= w, (w, line)
    assert module.render(st_note, False, width=50, reloaded="RELOADED 9.9.9") == \
        module.render(st_note, False, width=50), "a plain frame has no chip to note on"
    say("reload note: a reloaded pane names the build on its title chip, and the frame is "
        "untouched but for that one row: ok")

    # ---- doctor: the machine's own answer, as a gate. A machine with the tools a pane
    #      needs passes and exits 0; one that cannot even write its scratch dir says so and
    #      exits non-zero, which is what makes this callable from a wrapper or CI.
    doc = run("doctor")
    djson = json.loads(run("doctor", "--json").stdout)
    assert djson["version"] == module.VERSION and isinstance(djson["checks"], list), djson
    assert {c["level"] for c in djson["checks"]} <= {"ok", "warn", "FAIL"}, djson
    if shutil.which("tmux") and shutil.which("ps"):
        assert doc.returncode == 0, (doc.returncode, doc.stdout[-300:])
        assert djson["ok"] is True and djson["fail"] == 0, djson
        assert "0 fail" in doc.stdout, doc.stdout[-200:]
    else:  # a machine without tmux/ps is exactly the case doctor exists for
        assert djson["ok"] is False and djson["fail"] >= 1, djson
        assert doc.returncode != 0 and "FAIL" in doc.stdout, doc.stdout[-300:]
    broken = subprocess.run(
        [sys.executable, FB, "doctor"], capture_output=True, text=True, cwd=CWD,
        env=dict(env, PATH="/nonexistent"), timeout=30,
    )
    assert broken.returncode != 0, (broken.returncode, broken.stdout[-300:])
    assert "FAIL" in broken.stdout and "tmux" in broken.stdout, broken.stdout[-300:]
    # ...and the interpreter question as its own line: the pane, its watcher and the keeper
    # on ONE Python — `ok` when they are (or with nothing to compare), a `warn` naming each
    # role and its WHOLE path when they are not, because those paths are the diagnosis
    py_a, py_b = "/usr/bin/python3", "/opt/homebrew/opt/python@3.14/bin/python3.14"
    empty = module.one_python_check([])
    assert empty[0] == "ok" and "nothing to compare" in empty[1], empty
    alone = module.one_python_check([("pane", None), ("watcher", py_a), ("keeper", None)])
    assert alone[0] == "ok" and py_a in alone[1], alone
    agree = module.one_python_check([("pane", py_a), ("watcher", py_a), ("keeper", py_a)])
    assert agree[0] == "ok" and "pane, watcher and keeper" in agree[1] and py_a in agree[1], agree
    mixed = module.one_python_check([("pane", py_a), ("watcher", py_b), ("keeper", py_a)])
    assert mixed[0] == "warn" and py_a in mixed[1] and py_b in mixed[1], mixed
    assert all(role in mixed[1] for role in ("pane", "watcher", "keeper")), mixed
    # a symlinked spelling is one interpreter, the same rule every other reader here uses
    link_real = os.path.join(TEST_HOME, "doctor-py")
    link_alias = os.path.join(TEST_HOME, "doctor-py-link")
    os.makedirs(link_real, exist_ok=True)
    if not os.path.islink(link_alias):
        os.symlink(link_real, link_alias)
    twinned = module.one_python_check([("pane", os.path.join(link_alias, "py")),
                                       ("watcher", os.path.join(link_real, "py"))])
    assert twinned[0] == "ok", twinned
    named = {c["name"]: c for c in djson["checks"]}
    assert "one python" in named and named["one python"]["level"] in ("ok", "warn") \
        and named["one python"]["detail"], named.keys()
    say("doctor: the pane, its watcher and the keeper are compared as one interpreter — and "
        "a disagreement names each role with its path: ok")

    # ---- and a doctor run is a LOOK: it leaves every claim record exactly as it found it,
    #      byte for byte. The watcher row asked `lock_holder`, whose free-claim answer REMOVES
    #      the record it finds — a doctor run over a dead watcher's leftovers deleted
    #      `fbtodo-daemon.pid`, the record `_claim_live` decides the state root from and `one
    #      python` reads the watcher's pid from. The row now reads through `lock_peek` (open,
    #      try the lock; no unlink, no write), and this pins the whole contract: records for
    #      the daemon and the keeper seeded with a DEAD pid — free, the exact
    #      case a cleanup would have removed — must survive a run byte-identical, and so must
    #      the same record while a claim is HELD, with the row naming its holder.
    doctor_victim = spawn_quiet("sleep", "600")
    dead_pid = doctor_victim.pid
    kill_tree(doctor_victim)
    deadline = time.time() + 5
    while time.time() < deadline and module.pid_alive(dead_pid):
        time.sleep(0.05)
    claim_paths = [module.LOCK_PATH, module.PANE_KEEPER_PATH]

    def watcher_row(text: str) -> str:
        rows = [ln for ln in STRIP(text).splitlines() if re.match(r"\s*\S+\s+watcher\s", ln)]
        assert rows, text[-400:]
        return rows[0]

    try:
        for p in claim_paths:
            module.atomic_write_json(p, {"pid": dead_pid, "cwd": CWD, "started_ms": 0,
                                         "instance_pid": None, "version": module.VERSION})
        before = {p: open(p, "rb").read() for p in claim_paths}
        seen = run("doctor")
        assert seen.returncode == 0, (seen.returncode, seen.stdout[-300:])
        after = {p: open(p, "rb").read() for p in claim_paths}
        assert after == before, "doctor rewrote a claim record it was only asked to read"
        assert all(os.path.exists(p) for p in claim_paths), "doctor removed a claim record"
        row = watcher_row(seen.stdout)
        assert "not running" in row, row
        # ...and again while the daemon claim is HELD: the row names the holder, and not a
        # byte moves (a probe would have been free to tidy the keeper's leftover)
        assert module.write_lock(CWD, None, path=module.LOCK_PATH)
        held_before = {p: open(p, "rb").read() for p in claim_paths}
        held_seen = run("doctor")
        assert held_seen.returncode == 0, (held_seen.returncode, held_seen.stdout[-300:])
        row = watcher_row(held_seen.stdout)
        assert f"pid {os.getpid()} holds" in row, row
        assert {p: open(p, "rb").read() for p in claim_paths} == held_before, \
            "doctor rewrote a claim record while a claim was held"
        say("doctor: a run leaves every claim record byte-identical — seeded daemon and "
            "keeper records, dead pids and a held claim alike: ok")
    finally:
        module.clear_lock(path=module.LOCK_PATH)
        for p in (module.PANE_KEEPER_PATH,):
            try:
                os.unlink(p)
            except OSError:
                pass

    # ---- ...and not just `doctor`: EVERY command that only looks keeps the same promise.
    #      `status` was the last one left on `daemon_pid()` — the destructive probe whose
    #      free-claim answer removes the record it then reports on — a status read
    #      its claim through `lock_holder` again (which for a watcher
    #      from another build even KILLS it and clears the claim). Both now read through
    #      `lock_peek` like the doctor's row. This pins the family: with the daemon, keeper
    #      records seeded with a DEAD pid (free — the exact case a cleanup would
    #      have removed), each look-only command must leave every claim record byte-identical
    #      and still present, and the state root otherwise untouched. The task log is
    #      deliberately outside the pin: `snap` and `json` record tracking events in it by
    #      design (`track_tasks`), so they are held to the claims half of the contract only,
    #      and the whole-root comparison starts after their write.
    look_paths = [module.LOCK_PATH, module.PANE_KEEPER_PATH]

    def scratch_bytes() -> dict:
        """Every file under the test state root, by path — what a look may not move."""
        out = {}
        for base, _dirs, names in os.walk(TEST_HOME):
            for name in names:
                path = os.path.join(base, name)
                try:
                    with open(path, "rb") as fh:
                        out[path] = fh.read()
                except OSError:
                    out[path] = None
        return out

    def watcher_claim_row(text: str) -> str:
        rows = [ln for ln in STRIP(text).splitlines() if re.match(r"\s*watcher\s+:", ln)]
        assert rows, text[-400:]
        return rows[0]

    look_victim = spawn_quiet("sleep", "600")
    look_dead = look_victim.pid
    kill_tree(look_victim)
    deadline = time.time() + 5
    while time.time() < deadline and module.pid_alive(look_dead):
        time.sleep(0.05)
    try:
        for p in look_paths:
            module.atomic_write_json(p, {"pid": look_dead, "cwd": CWD, "started_ms": 0,
                                         "instance_pid": None, "version": module.VERSION})
        before = {p: open(p, "rb").read() for p in look_paths}
        # The two that record tracking events: the claims are the promise, the task log is
        # their business, so their write happens before the whole-root comparison begins.
        for argv in (["snap"], ["json"]):
            seen = run(*argv)
            assert seen.returncode == 0, (argv, seen.returncode, seen.stderr[-200:])
        assert {p: open(p, "rb").read() for p in look_paths} == before, \
            "snap/json rewrote a claim record they were only asked to read"
        assert all(os.path.exists(p) for p in look_paths), \
            "snap/json removed a claim record"
        # The dead-pid records are FREE, and every look says so — from the records it leaves
        # behind to say it next time.
        assert "— not running" in watcher_claim_row(run("status").stdout), "status row"
        assert json.loads(run("status", "--json").stdout)["watcher_pid"] is None, "status json"
        scratch_before = scratch_bytes()
        for argv in (
            ["status"], ["status", "--json"],
            ["why"], ["why", "--json"],
            ["ledger"], ["ledger", "--json"],
            ["locks"], ["locks", "--json"],
            ["doctor"], ["doctor", "--json"],
            ["bar"], ["pin", "--list"],
        ):
            seen = run(*argv)
            assert seen.returncode == 0, (argv, seen.returncode, seen.stderr[-200:])
        after = {p: open(p, "rb").read() for p in look_paths}
        assert after == before, "a look-only command rewrote a claim record it was only asked to read"
        assert all(os.path.exists(p) for p in look_paths), \
            "a look-only command removed a claim record"
        assert scratch_bytes() == scratch_before, "a look-only command wrote into the state root"
        # ...and the same with the daemon claim HELD: the row and the JSON name the holder,
        # and still not a byte moves (a held claim is the case neither a probe nor a peek
        # may tidy).
        assert module.write_lock(CWD, None, path=module.LOCK_PATH)
        held_before = {p: open(p, "rb").read() for p in look_paths}
        held_row = watcher_claim_row(run("status").stdout)
        m = re.match(r"\s*watcher\s+:\s*(\d+)\s*$", held_row)
        assert m and int(m.group(1)) == os.getpid(), held_row
        assert json.loads(run("status", "--json").stdout)["watcher_pid"] == os.getpid()
        assert {p: open(p, "rb").read() for p in look_paths} == held_before, \
            "status rewrote a claim record while a claim was held"
        # ...and the other half of the contract: the paths that ACT still PROBE. A stop or
        # a start has to be able to clear a dead record, which is what `daemon_pid` is for
        # (the looks above refused to touch the same file), so the probe must still remove
        # the leftover — dropping it would leave a watcher that is not running reported as
        # one the next look reads.
        module.clear_lock(path=module.LOCK_PATH)
        module.atomic_write_json(module.LOCK_PATH, {"pid": look_dead, "cwd": CWD,
                                                    "started_ms": 0, "instance_pid": None,
                                                    "version": module.VERSION})
        assert module.daemon_pid() is None and not os.path.exists(module.LOCK_PATH), \
            "the acting probe stopped clearing a dead record"
        say("looks: every command that only looks — status, why, ledger, locks, doctor, "
            "--status, bar and pin --list, plus the tracking snap/json — leaves every claim "
            "record byte-identical, reports a dead pid as not running, and a held claim by "
            "its holder, while the acting probe still clears a dead record: ok")
    finally:
        module.clear_lock(path=module.LOCK_PATH)
        for p in (module.PANE_KEEPER_PATH,):
            try:
                os.unlink(p)
            except OSError:
                pass

    # ---- ...and `locks --fix` is the same read with a hand: the free records the looks just
    #      refused to touch are CLEARED (through the ask's own probe, which re-checks the
    #      name under the lock), nothing else is, and the plan comes first so an operator can
    #      see it before anything moves. A fix with no untied process to end asks nothing —
    #      there is no kill in it — and a second run finds nothing to clear, which is what
    #      makes it safe on a schedule. The flag belongs to its command, like `--watch`.
    fix_paths = [module.LOCK_PATH, module.PANE_KEEPER_PATH]
    fix_victim = spawn_quiet("sleep", "600")
    fix_dead = fix_victim.pid
    kill_tree(fix_victim)
    deadline = time.time() + 5
    while time.time() < deadline and module.pid_alive(fix_dead):
        time.sleep(0.05)
    try:
        for p in fix_paths:
            module.atomic_write_json(p, {"pid": fix_dead, "cwd": CWD, "started_ms": 0,
                                         "instance_pid": None, "version": module.VERSION})
        dry_fix = json.loads(run("locks", "--fix", "--dry-run", "--json").stdout)
        assert dry_fix["dry_run"] is True and dry_fix["applied"] is False, dry_fix
        assert [c["role"] for c in dry_fix["planned"]["clears"]] == \
            ["watcher", "keeper"], dry_fix["planned"]
        assert all(os.path.exists(p) for p in fix_paths), "--dry-run removed a claim record"
        fixed = json.loads(run("locks", "--fix", "--json", "--yes").stdout)
        assert fixed["applied"] is True and fixed["reason"] is None, fixed
        assert [c["role"] for c in fixed["cleared"]] == ["watcher", "keeper"] \
            and all(c["removed"] for c in fixed["cleared"]), fixed["cleared"]
        assert all(not os.path.exists(p) for p in fix_paths), "the fix left a leftover record"
        again = json.loads(run("locks", "--fix", "--json", "--yes").stdout)
        assert again["planned"]["clears"] == [], "the fix planned the same clear twice"
        assert run("status", "--fix").returncode == 2, "the --fix guard did not hold"
        say("locks --fix: the free leftovers a look refuses to touch are cleared — planned "
            "first with --dry-run, applied through the ask's own probe, idempotent on a "
            "second run: ok")
    finally:
        for p in fix_paths:
            try:
                os.unlink(p)
            except OSError:
                pass

    # ---- `--restart` is the opt-in that runs the ask after the repair. On its own, `locks
    #      --fix` leaves the claims it ended FREE — nothing re-claims them until the next
    #      session start, which can be hours — so this is the one command that leaves the
    #      machine watched again. It runs the same ask a session start uses (`ensure_daemon`),
    #      it is firmly opt-in (`--dry-run` still only plans; the flag belongs to `locks
    #      --fix`, not to `locks`), and it is idempotent: a second run keeps the watcher the
    #      first one started instead of spawning another.
    restart_victim = spawn_quiet("sleep", "600")
    restart_lock = os.path.join(TEST_HOME, "fbtodo-daemon.pid")
    # A keeper ask here would name the operator's own tmux server; `FBTODO_NO_PANE` makes the
    # restart exercise the watcher half alone, against this fixture root.
    restart_env = {**env, "FBTODO_NO_PANE": "1"}

    def restart_run(*extra):
        return json.loads(subprocess.run(
            [sys.executable, FB, "locks", "--fix", "--restart", *extra, "--json",
             "--watch-pid", str(restart_victim.pid), "-i", "0.2"],
            capture_output=True, text=True, env=restart_env, cwd=CWD, timeout=60,
        ).stdout)

    try:
        if os.path.exists(restart_lock):
            os.unlink(restart_lock)
        assert run("locks", "--restart").returncode == 2, "the --restart guard did not hold"
        assert run("status", "--restart").returncode == 2, "the --restart guard did not hold"
        # a dry run plans the restart and starts nothing
        planned = restart_run("--dry-run")
        assert planned["dry_run"] is True and planned["applied"] is False, planned
        assert planned["restarted"] == {}, planned["restarted"]
        assert not os.path.exists(restart_lock), "a dry run started a watcher"
        # the real run brings a watcher back on this root, through the normal ask
        done = restart_run("--yes")
        row = done["restarted"]["watcher"]
        assert row["pid"] and row["before"] is None, row
        assert module.lock_holder(restart_lock) == row["pid"], "the restart left no watcher"
        assert module.pid_alive(row["pid"]), "the restarted watcher is not alive"
        # ...and a second run keeps the one it started — the ask is not a restart loop
        kept = restart_run("--yes")
        assert kept["restarted"]["watcher"] == {"pid": row["pid"], "before": row["pid"]}, \
            kept["restarted"]
        say("locks --fix --restart: a dry run plans it and starts nothing, the real run "
            "re-claims the watcher through the normal ask, and a second run keeps it: ok")
    finally:
        holder = module.lock_holder(restart_lock)
        if holder:
            try:
                os.kill(int(holder), signal.SIGTERM)
            except OSError:
                pass
            deadline = time.time() + 10
            while time.time() < deadline and module.pid_alive(int(holder)):
                time.sleep(0.1)
        if os.path.exists(restart_lock):
            os.unlink(restart_lock)
        kill_tree(restart_victim)

    # ---- state root: the XDG directory by default, the legacy `~/.freebuff` moved there
    #      ONCE, and only when nothing is still writing it. Driven through child processes
    #      with their own HOME, because the root is decided at import — the same reason this
    #      suite has to set FBTODO_HOME before it imports the module.
    root = os.path.join(TEST_HOME, "state-root")
    shutil.rmtree(root, ignore_errors=True)
    no_fbhome = {k: v for k, v in env.items() if k != "FBTODO_HOME"}

    def state_checks(home: str, legacy: dict = None, xdg: str = None, fbtodo_home: str = None):
        """Doctor's checks for a child with `HOME=home` and `~/.freebuff` seeded."""
        os.makedirs(os.path.join(home, ".freebuff"), exist_ok=True)
        for name, blob in (legacy or {}).items():
            with open(os.path.join(home, ".freebuff", name), "w") as fh:
                fh.write(blob)
        child = dict(no_fbhome, HOME=home)
        child.pop("XDG_STATE_HOME", None)
        if xdg is not None:
            child["XDG_STATE_HOME"] = xdg
        if fbtodo_home is not None:
            child["FBTODO_HOME"] = fbtodo_home
        proc = subprocess.run(
            [sys.executable, FB, "doctor", "--json"], capture_output=True, text=True,
            env=child, cwd=CWD, timeout=90,
        )
        doc = json.loads(proc.stdout)
        return {c["name"]: c for c in doc["checks"]}

    a_home = os.path.join(root, "a")
    checks = state_checks(a_home)
    assert checks["scratch"]["detail"] == os.path.join(a_home, ".local", "state", "fbtodo"), checks
    assert "state" not in checks, "a fresh machine has nothing to move"
    checks = state_checks(os.path.join(root, "a2"), fbtodo_home=os.path.join(root, "a2", "fbhome"))
    assert checks["scratch"]["detail"] == os.path.join(root, "a2", "fbhome"), checks
    assert "state" not in checks, checks
    say("state root: XDG by default, FBTODO_HOME when set, and nothing moved on a fresh machine: ok")

    b_xdg = os.path.join(root, "b-xdg")
    b_home = os.path.join(root, "b")
    seeded = {"fbtodo-state.json": '{"todos": [], "instance_pid": 1}', "fbtodo-last.json": "{}"}
    checks = state_checks(b_home, seeded, xdg=b_xdg)
    assert checks["scratch"]["detail"] == os.path.join(b_xdg, "fbtodo"), checks["scratch"]
    assert checks["state"]["level"] == "ok" and "moved from" in checks["state"]["detail"], checks
    assert not os.path.exists(os.path.join(b_home, ".freebuff", "fbtodo-state.json")), "old copy left"
    assert os.path.exists(os.path.join(b_xdg, "fbtodo", "fbtodo-state.json")), "never arrived"
    assert os.path.exists(os.path.join(b_xdg, "fbtodo", "fbtodo-last.json")), "moved part of the set"
    again = state_checks(b_home, xdg=b_xdg)
    assert again["scratch"]["detail"] == os.path.join(b_xdg, "fbtodo"), again["scratch"]
    assert "state" not in again, "the move is not once"
    say("state root: the legacy directory is moved once, whole, and reported: ok")

    c_home = os.path.join(root, "c")
    checks = state_checks(
        c_home,
        {"fbtodo-state.json": '{"todos": []}',
         "fbtodo-daemon.pid": json.dumps({"pid": os.getpid(), "version": module.VERSION})},
    )
    assert checks["scratch"]["detail"] == os.path.join(c_home, ".freebuff"), checks["scratch"]
    assert checks["state"]["level"] == "warn" and "live watcher" in checks["state"]["detail"], checks
    assert os.path.exists(os.path.join(c_home, ".freebuff", "fbtodo-state.json")), "moved anyway"
    assert not os.path.exists(
        os.path.join(c_home, ".local", "state", "fbtodo", "fbtodo-state.json")
    ), "copied out from under a live watcher"
    # ...and the keeper counts too: moving ITS claim would leave the running keeper writing a
    # record nothing reads, and a second keeper would start on top of it. The record has to
    # still be there after the doctor run for the same reason it is read at all: `_claim_live`
    # decides the move from it, and `one python` reads the keeper's interpreter from it, so a
    # diagnostic that cleaned up after itself would erase the fact it had just reported —
    # `doctor` used to ask `lock_holder`, whose free-claim answer REMOVES the record instead of
    # believing it (found here, 2026-10-02).
    d_home = os.path.join(root, "d")
    checks = state_checks(
        d_home,
        {"fbtodo-state.json": '{"todos": []}',
         "fbtodo-pane-keeper.pid": json.dumps({"pid": os.getpid(), "tmux": "default"})},
    )
    assert checks["scratch"]["detail"] == os.path.join(d_home, ".freebuff"), checks["scratch"]
    assert checks["state"]["level"] == "warn", checks
    assert os.path.exists(os.path.join(d_home, ".freebuff", "fbtodo-pane-keeper.pid")), "moved anyway"
    say("state root: a live watcher or keeper keeps the old root, and the move waits: ok")

    # ---- and importing the package is not an action: the root is CHOSEN at import (the move
    #      is left for `main`), so a plain `import fbtodo` creates no directory and moves
    #      nothing. A fresh home (nothing may appear) and a home with a legacy store (it must
    #      stay exactly where it is) — the second is the case a module-level migration would
    #      have quietly acted on.
    imp_base = {k: v for k, v in no_fbhome.items() if k != "XDG_STATE_HOME"}
    for label, seed in (("fresh", None),
                        ("legacy", {"fbtodo-state.json": '{"todos": [], "instance_pid": 1}'})):
        imp_home = os.path.join(root, "import-%s" % label)
        shutil.rmtree(imp_home, ignore_errors=True)
        os.makedirs(imp_home, mode=0o700, exist_ok=True)
        if seed:
            os.makedirs(os.path.join(imp_home, ".freebuff"), exist_ok=True)
            for name, blob in seed.items():
                with open(os.path.join(imp_home, ".freebuff", name), "w") as fh:
                    fh.write(blob)
        proc = subprocess.run(
            [sys.executable, "-c", "import fbtodo"], capture_output=True, text=True,
            env=dict(imp_base, HOME=imp_home, PYTHONPATH=SRC), cwd=CWD, timeout=90,
        )
        assert proc.returncode == 0, (label, proc.returncode, proc.stderr[-300:])
        assert not os.path.exists(os.path.join(imp_home, ".local")), (
            "importing the package created the state root (%s)" % label)
        if seed:
            assert os.path.exists(
                os.path.join(imp_home, ".freebuff", "fbtodo-state.json")
            ), "importing the package moved the legacy store"
    # ...and an FBTODO_HOME that does not exist yet is not created by the import either
    imp_env = os.path.join(root, "import-env-home")
    proc = subprocess.run(
        [sys.executable, "-c", "import fbtodo"], capture_output=True, text=True,
        env=dict(imp_base, HOME=os.path.join(root, "import-env-base"),
                 FBTODO_HOME=imp_env, PYTHONPATH=SRC), cwd=CWD, timeout=90,
    )
    assert proc.returncode == 0 and not os.path.exists(imp_env), (proc.returncode,
                                                                  proc.stderr[-300:])
    say("state root: importing the package chooses a root but touches no files: ok")

    # ---- the source seam: three readers of three machines' transcripts, one protocol, and
    #      a dispatch that is a loop. What matters is not that the loop exists but that the
    #      contract holds for every source — a name, the backend it reports, its own answer
    #      for "there is nothing here" — and that `-s` picks the sources it documents.
    class _Ask:
        def __init__(self, mode: str):
            self.source = mode

    def asked(mode: str) -> list:
        return [src.name for src in module.sources_for(_Ask(mode))]

    assert sorted(module.SOURCES) == ["cli", "desktop"], sorted(module.SOURCES)
    for name, cls in (("cli", "CliSource"), ("desktop", "DesktopSource"),
                      ):
        src = module.SOURCES[name]
        assert isinstance(src, getattr(module, cls)), (name, type(src))
        assert (src.name, src.backend) == (name, name), (src.name, src.backend)
        for method in ("find", "describe", "miss"):
            assert getattr(type(src), method) is not getattr(module.Source, method), (name, method)
        assert src.miss().get("error"), (name, src.miss())
    assert asked("auto") == ["cli", "desktop"], asked("auto")
    assert asked("cli") == ["cli"] and asked("desktop") == ["desktop"]
    # `-s cli` asks ONE source: with no journal in this directory the answer is an error,
    # not the desktop store — which is what the whole seam has to keep true
    assert module.SOURCES["cli"].miss()["error"] == "no CLI chat for this directory"
    assert module.SOURCES["desktop"].miss()["error"] == "no conversation DB found"
    say("sources: one protocol, three readers, and the order `-s` asks them in: ok")

    # ---- a cached state that is FRESH and answers the request can still name a session that
    #      has ENDED: a watcher heartbeats the file it owns after the chat behind it finished,
    #      and `auto` accepts whatever backend the watcher wrote. The pane used to hold that
    #      until the file aged out or somebody reloaded it by hand; now it re-resolves once,
    #      as soon as the session is over.
    now_ms = int(time.time() * 1000)

    class _PaneArgs:
        source = "auto"
        interval = 1.0

    live_cli = {"backend": "cli", "session": "LIVE", "tool_version": module.VERSION,
                "status": "watching", "heartbeat_ms": now_ms,
                "store_mtime_ms": now_ms - 1_000}
    dead_cli = dict(live_cli, session="DEAD",
                    store_mtime_ms=now_ms - module.SOURCE_LIVE_MS - 60_000)
    assert not module.followed_session_over(live_cli, None, now_ms), "a moving journal is live"
    assert module.followed_session_over(dead_cli, None, now_ms), "a silent one with no process is over"
    assert not module.followed_session_over(dead_cli, 1234, now_ms), (
        "a Freebuff process behind the chat keeps it live"
    )
    assert not module.followed_session_over({"backend": "desktop", "store_mtime_ms": 1}, None,
                                             now_ms), "only the CLI answers from a journal clock"
    assert not module.followed_session_over({"backend": "cli", "store_mtime_ms": None}, None,
                                             now_ms), "no clock to judge by is not 'over'"
    # the pane keeps a fresh, live state and drops a fresh one whose session has ended — that
    # drop IS the re-resolve — and it stays dropped: the file still names the ended session, so
    # there is no "already handled" that could hand the same list back on the next poll
    assert module.pane_cached_state(live_cli, _PaneArgs(), None, now_ms)
    assert not module.pane_cached_state(dead_cli, _PaneArgs(), None, now_ms)
    assert not module.pane_cached_state(dead_cli, _PaneArgs(), None, now_ms + 60_000), (
        "a dropped session must not come back just because a poll went by"
    )
    # a stale file is unusable whatever it says, and a state that is not an answer to THIS
    # request is never used (`-s cli` is not answered by the desktop store's state)
    stale = dict(live_cli, heartbeat_ms=now_ms - 60_000)
    assert not module.pane_cached_state(stale, _PaneArgs(), None, now_ms)

    class _CliArgs:
        source = "cli"
        interval = 1.0

    assert not module.pane_cached_state(
        dict(live_cli, backend="desktop", session="D"), _CliArgs(), None, now_ms
    ), "an explicit source is never answered by another backend's cached state"
    say("the pane: a cached list is dropped the moment its session ends: ok")

    # ---- ...and the PANE does it, not just the helper: the real loop, in a pty, started from
    #      a cached state that is fresh and answers the request but names a finished session.
    #      It must come up on the LIVE chat — and do it on its own poll, with no reload.
    import fcntl
    import pty
    import select
    import struct
    import termios

    proj = "liveproj"
    cli_root = os.path.join(TEST_HOME, "paneroot")
    chat = os.path.join(cli_root, proj, "chats", "2026-01-01T00-00-00.000Z")
    os.makedirs(chat, exist_ok=True)
    live_step = "the live session's only step"
    with open(os.path.join(chat, "log.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "level": "DEBUG", "timestamp": "2026-01-01T00:00:01.000Z",
            "data": {"toolCalls": [{"toolName": "write_todos",
                                    "input": {"todos": [{"task": live_step,
                                                        "completed": False}]}}]},
        }) + "\n")
    pane_cwd = os.path.join(TEST_HOME, "panecwd", proj)
    os.makedirs(pane_cwd, exist_ok=True)
    # the cached state the pane starts from: fresh, matching, and a FINISHED session
    dead_now = int(time.time() * 1000)
    with open(module.STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump({
            "schema": 1, "backend": "cli", "session": "DEAD", "status": "watching",
            "tool_version": module.VERSION, "heartbeat_ms": dead_now,
            "store_mtime_ms": dead_now - module.SOURCE_LIVE_MS - 60_000,
            "todos": [{"task": "a finished session's step", "completed": True}],
        }, fh)
    master, slave = pty.openpty()
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 100, 0, 0))
    # `--watch-pid` names a pid that cannot be alive, so `find_instance` answers None on ANY
    # machine: the check must not pass or fail on whether this one happens to run freebuff.
    pane_proc = subprocess.Popen(
        [sys.executable, FB, "pane", "--no-daemon", "--interval", "0.2",
         "--stale-after", "0", "--cli-root", cli_root, "--watch-pid", "999999"],
        cwd=pane_cwd, stdin=slave, stdout=slave, stderr=slave, env=env, close_fds=True,
    )
    os.close(slave)
    os.set_blocking(master, False)
    seen = bytearray()
    ended = time.time() + 6.0
    try:
        while time.time() < ended:
            ready, _, _ = select.select([master], [], [], 0.2)
            if not ready:
                continue
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            seen += chunk
            if live_step.encode() in seen:
                break
    finally:
        pane_proc.send_signal(signal.SIGINT)
        try:
            pane_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pane_proc.kill()
        os.close(master)
    painted = STRIP(seen.decode("utf-8", "replace"))
    assert live_step in painted, painted[-800:]
    assert "a finished session's step" not in painted, (
        "the pane painted the finished session's list instead of re-resolving\n" + painted[-800:]
    )
    say("the pane: it re-resolves away from an ended session without a reload: ok")


    old = module.finish_state(
        {"session": "S1", "todos": [{"task": "a", "completed": True}]}, None
    )
    new_session_empty = module.finish_state(
        {"session": "S2", "todos": [], "task_times": {"a": {"started_ms": 1, "done_ms": 2}}},
        old,
    )
    assert new_session_empty["cleared"] is True, new_session_empty
    # the previous session's clocks have nothing left to describe, so they go with its list
    assert new_session_empty["task_times"] == {}, new_session_empty
    assert new_session_empty["list_version"] == 0, new_session_empty
    assert new_session_empty["todos"] == [], new_session_empty
    new_list = module.finish_state(
        {"session": "S2", "todos": [{"task": "b", "completed": False}]}, new_session_empty
    )
    assert new_list["list_version"] == 1 and new_list["total"] == 1, new_list
    same_list_again = module.finish_state(
        {"session": "S2", "todos": [{"task": "b", "completed": True}]}, new_list
    )
    assert same_list_again["list_version"] == 1, same_list_again  # progress, not a new list
    probe = module.finish_state(
        {"session": "S3", "todos": [{"task": "c", "completed": False}]}, None
    )
    assert not probe.get("list_version"), probe  # a probe must not invent a counter
    say("new session drops the old list; progress does not renumber it: ok")
    say("a standalone probe reports no list counter instead of guessing #1: ok")

    # ---- a FINISHED list does not belong to the turn after it: it is dropped, not carried
    done = {"session": "S", "todos": [{"task": "a", "completed": True}], "ts": 1000,
            "source_updated_ms": 1000, "probed_ms": 3000, "goal": "the old project",
            "task_times": {"a": {"started_ms": 10, "done_ms": 70, "elapsed_ms": 60}},
            "now": None, "nudge": None}
    was = dict(done, list_version=4, list_id="x")
    after = module.finish_state(dict(done, turn={"start_ms": 2000}), was)
    assert after["todos"] == [] and after["total"] == 0 and after["done"] == 0, after
    assert after["cleared"] is True and after["cleared_turn"] is True, after
    assert after["goal"] is None, after          # the heading described the dropped list
    assert after["task_times"] == {}, after      # ...and so did the dropped list's clocks
    assert after["list_version"] == 4, after     # a drop is not a new list
    assert "last turn's list is done" in module.no_list_reason(after), after
    # work LEFT on it is the agent's standing plan, so it stays — heading and all
    left = dict(
        done, todos=[{"task": "a", "completed": True}, {"task": "b", "completed": False}],
        turn={"start_ms": 2000},
    )
    held = module.finish_state(left, was)
    assert len(held["todos"]) == 2 and not held["cleared_turn"], held
    assert held["goal"] == "the old project", held
    assert held["task_times"] == done["task_times"], held   # a standing plan keeps its clocks
    # a list written INSIDE this turn is this turn's own, however finished it looks
    inside = dict(done, ts=3000, source_updated_ms=3000, turn={"start_ms": 2000})
    kept = module.finish_state(inside, was)
    assert len(kept["todos"]) == 1 and not kept["cleared_turn"], kept
    assert kept["task_times"] == done["task_times"], kept  # this turn's own list keeps its clocks
    # the desktop path carries no turn clock; a newer request is enough there
    desktop_ish = module.finish_state(dict(done, now="something new"), was)
    assert desktop_ish["todos"] == [] and desktop_ish["cleared_turn"] is True, desktop_ish
    say("a finished list is dropped when the next turn starts, and only then: ok")

    # ---- the desktop app runs SEVERAL threads per project (each tab is one, and a thread
    #      can be left working while you open another), but its store only holds transcripts:
    #      "is this thread live?" is a question about how recently it wrote a list, and the
    #      app's own marks (closed, archived) are answers too. `--threads N` is how many a
    #      pane stacks, so the rule is checked against a store built by hand: the followed
    #      thread, the live ones, and the kinds that must NOT be drawn.
    desk = os.path.join(TEST_HOME, "deskstore")
    os.makedirs(desk, exist_ok=True)
    desk_db = os.path.join(desk, "desktop-v2.db")
    if os.path.exists(desk_db):
        os.remove(desk_db)
    with open(os.path.join(desk, "project.json"), "w", encoding="utf-8") as fh:
        json.dump({"projectPath": "/tmp/deskproj"}, fh)
    # The windows below are relative to the CLOCK the CLI reads, so the fixture's "now" has
    # to be the real one: a frozen 2026 date is 11 hours old by the time a `--thread-live 90`
    # window is applied through the command (found live: the threads were dropped as stale).
    DESK_NOW = int(time.time() * 1000)

    def desk_todos(*steps):
        return json.dumps([{"task": t, "completed": d} for t, d in steps])

    def desk_parts(todos):
        if not todos:
            return json.dumps([{"toolName": "read_file"}])
        return json.dumps([
            {"toolName": "write_todos", "input": {"todos": json.loads(todos)}}
        ])

    con = sqlite3.connect(desk_db)
    con.execute(
        "CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, status TEXT,"
        " sidebar_archived_at INTEGER)"
    )
    con.execute("CREATE TABLE messages (seq INTEGER, thread_id TEXT, parts_json TEXT, ts INTEGER)")
    desk_rows = [
        # the thread the pane was pointed at (30s ago: that is a thread that is working)
        ("T0", "Followed thread", "open", None,
         desk_todos(("its first step", True), ("its second step", False)), DESK_NOW - 30_000),
        # live, and quiet enough to be neither: 5 minutes is outside `DESKTOP_RUNNING_MS`
        ("T1", "Other live thread", "open", None,
         desk_todos(("other one", True), ("other two", True), ("other three", False)),
         DESK_NOW - 300_000),
        # outside the live window (which the flag can widen)
        ("T2", "Quiet thread", "open", None, desk_todos(("older step", False)),
         DESK_NOW - 200 * 60_000),
        # the app has been told to put these two away
        ("T3", "Closed thread", "closed", None, desk_todos(("closed step", False)),
         DESK_NOW - 1_000),
        ("T4", "Archived thread", "open", DESK_NOW - 1_000, desk_todos(("archived step", False)),
         DESK_NOW - 1_000),
        # never wrote a list at all: nothing to draw under a heading
        ("T5", "Listless thread", "open", None, None, DESK_NOW),
    ]
    for seq, (tid, title, status, archived, todos, ts) in enumerate(desk_rows):
        con.execute("INSERT INTO threads VALUES (?, ?, ?, ?)", (tid, title, status, archived))
        con.execute(
            "INSERT INTO messages VALUES (?, ?, ?, ?)", (seq, tid, desk_parts(todos), ts)
        )
    con.commit()
    con.close()

    one = module.read_desktop(desk_db, thread_id="T0", source="pinned", now_ms=DESK_NOW)
    assert one["session"] == "T0" and len(one["todos"]) == 2, one
    assert not one.get("threads"), "a single-thread answer must not carry the key at all"
    many = module.read_desktop(desk_db, thread_id="T0", source="pinned", others=3,
                               now_ms=DESK_NOW)
    assert [t["id"] for t in many["threads"]] == ["T0", "T1"], many["threads"]
    assert many["threads"][0]["current"] is True, many["threads"][0]
    assert many["threads"][0]["todos"] == many["todos"], many["threads"][0]
    assert (many["threads"][0]["running"], many["threads"][1]["running"]) == (True, False), (
        many["threads"]
    )
    assert [t["id"] for t in module.read_desktop(
        desk_db, thread_id="T0", source="pinned", others=1, now_ms=DESK_NOW
    )["threads"]] == ["T0", "T1"], "the cap is how many threads are SHOWN"
    no_window = module.read_desktop(desk_db, thread_id="T0", source="pinned", others=3,
                                    now_ms=DESK_NOW, window_ms=0)
    assert [t["id"] for t in no_window["threads"]] == ["T0", "T1", "T2"], no_window["threads"]
    say("desktop: the live threads ride on the state, filed-away and quiet ones do not: ok")

    # ---- through the CLI, because that is where the flags are read: `--threads` is how
    #      many, `--thread-live 0` widens the window to every thread with a list.
    desk_state = os.path.join(desk, "workspace.json")
    cli = run("json", "-s", "desktop", "--db", desk_db, "--state", desk_state, "-t", "T0")
    doc = json.loads(cli.stdout)
    assert doc["backend"] == "desktop" and doc["session"] == "T0", doc
    assert [t["id"] for t in doc["threads"]] == ["T0", "T1"], doc.get("threads")
    widened = json.loads(run(
        "json", "-s", "desktop", "--db", desk_db, "--state", desk_state, "-t", "T0",
        "--threads", "4", "--thread-live", "0",
    ).stdout)
    assert [t["id"] for t in widened["threads"]] == ["T0", "T1", "T2"], widened.get("threads")
    off = json.loads(run(
        "json", "-s", "desktop", "--db", desk_db, "--state", desk_state, "-t", "T0",
        "--threads", "0",
    ).stdout)
    assert not off.get("threads"), "--threads 0 is the one-list answer"
    say("desktop: --threads and --thread-live reach the state through the CLI: ok")

    # ---- a turn writes its list MORE THAN ONCE — that is what updating a todo list IS — and
    #      the app commits the whole turn as ONE message whose parts carry every call in order.
    #      So one `messages` row holds several `write_todos` lists and only the LAST of them is
    #      the turn's list. A reader that took the first (SQLite is free to hand `json_each`'s
    #      rows over in any order) drew a finished turn as `0/6` with a live clock beside it —
    #      measured 2026-10-03 on the pane following this very session, its own turn's OPENING
    #      list, after the turn had closed with every step ticked. The row is added here rather
    #      than to `desk_rows` above so the live-thread assertions already made cannot change.
    multi_parts = json.dumps([
        {"toolName": "write_todos",
         "input": {"todos": [{"task": "first step", "completed": False},
                             {"task": "second step", "completed": False}]}},
        {"toolName": "read_file"},
        {"toolName": "write_todos",
         "input": {"todos": [{"task": "first step", "completed": True},
                             {"task": "second step", "completed": True}]}},
    ])
    con = sqlite3.connect(desk_db)
    con.execute("INSERT INTO threads VALUES ('T6', 'Rewritten list', 'open', NULL)")
    con.execute("INSERT INTO messages VALUES (99, 'T6', ?, ?)",
                (multi_parts, DESK_NOW - 210 * 60_000))
    con.commit()
    con.close()
    multi = module.read_desktop(desk_db, thread_id="T6", source="pinned", now_ms=DESK_NOW)
    assert [t["completed"] for t in multi["todos"]] == [True, True], multi["todos"]
    wide = module.read_desktop(desk_db, thread_id="T0", source="pinned", others=9,
                               now_ms=DESK_NOW, window_ms=0)
    six = next(t for t in wide["threads"] if t["id"] == "T6")
    assert all(t["completed"] for t in six["todos"]), six["todos"]
    rewritten = json.loads(run(
        "json", "-s", "desktop", "--db", desk_db, "--state", desk_state, "-t", "T6"
    ).stdout)
    assert (rewritten["done"], rewritten["total"]) == (2, 2), (rewritten["done"], rewritten["total"])
    say("desktop: a turn's list is its LAST write_todos, not its first: ok")

    # ---- ...and the row's PROSE, which nothing read at all: the live history has a running
    #      turn's heading and requests, the committed rows have every finished turn's — so a
    #      pane following the app whose agent state carries no history drew a heading-less list
    #      beside `no heading — the agent owes a Goal: line` while the row it was reading held
    #      the line (measured 2026-10-03 on the pane following this session). The committed row
    #      is read by the same rules, anchored to ITS list — and its positions are `(seq, key)`
    #      pairs for the same reason the list read orders by `part.key`: one row is a whole
    #      turn, so two headings in it would otherwise share a position.
    prose_dir = os.path.join(TEST_HOME, "prosestore")
    os.makedirs(prose_dir, exist_ok=True)
    prose_db = os.path.join(prose_dir, "desktop-v2.db")
    if os.path.exists(prose_db):
        os.remove(prose_db)
    prose_first = [{"task": "the older step", "completed": True},
                   {"task": "the newer step", "completed": False}]
    prose_last = [{"task": "the older step", "completed": True},
                  {"task": "the newer step", "completed": True}]
    prose_opened, prose_list = DESK_NOW - 90_000, DESK_NOW - 60_000
    prose_parts = [
        {"kind": "text", "text": "Goal: the heading of the FIRST list"},
        {"kind": "tool", "toolName": "write_todos", "input": {"todos": prose_first}},
        {"kind": "text", "text": "Goal: the heading that belongs to the shown list"},
        {"kind": "tool", "toolName": "write_todos", "input": {"todos": prose_last}},
    ]

    def write_prose(requests=(), harness=None, turn_state="idle", beat=0, opened=0, parts=None,
                    rows=None):
        con = sqlite3.connect(prose_db)
        con.execute("CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, title TEXT,"
                    " status TEXT, turn_state TEXT, turn_alive_at INTEGER,"
                    " last_prompt_at INTEGER, sidebar_archived_at INTEGER, harness_state TEXT)")
        con.execute("CREATE TABLE IF NOT EXISTS messages (seq INTEGER, thread_id TEXT,"
                    " role TEXT, parts_json TEXT, ts INTEGER)")
        con.execute("DELETE FROM threads")
        con.execute("DELETE FROM messages")
        con.execute("INSERT INTO threads VALUES ('P0', 'Prose thread', 'open', ?, ?, ?, NULL, ?)",
                    (turn_state, beat, opened, json.dumps(harness) if harness else None))
        if rows is None:
            rows = [
                (200, "user", [{"kind": "text", "text": "tidy the footer"}], prose_opened),
                (201, "assistant", prose_parts if parts is None else parts, prose_list),
            ]
        for seq, role, body, ts in rows:
            con.execute("INSERT INTO messages VALUES (?, 'P0', ?, ?, ?)",
                        (seq, role, json.dumps(body), ts))
        for seq, text, ts in requests:
            con.execute("INSERT INTO messages VALUES (?, 'P0', 'user', ?, ?)",
                        (seq, json.dumps([{"kind": "text", "text": text}]), ts))
        con.commit()
        con.close()

    def read_prose():
        return module.read_desktop(prose_db, thread_id="P0", source="pinned", now_ms=DESK_NOW)

    write_prose(requests=[(202, "add a colour palette", DESK_NOW - 10_000)])
    st = read_prose()
    assert [t["completed"] for t in st["todos"]] == [True, True], st["todos"]
    assert st.get("goal") == "the heading that belongs to the shown list", st.get("goal")
    assert not st.get("goal_stale"), st
    assert st["now"] == "add a colour palette" and st["nudge"] is None, st
    # a bare continuation after the list is a nudge, exactly as in the live history
    write_prose(requests=[(203, "continue", DESK_NOW - 10_000)])
    st = read_prose()
    assert st["nudge"] == "continue" and st["now"] is None, st
    # nothing after the list: no request the shown list does not already describe
    write_prose()
    st = read_prose()
    assert st["now"] is None and st["nudge"] is None, st
    assert st.get("goal") == "the heading that belongs to the shown list", st.get("goal")
    # ...and it reaches the command people run to see what the pane is about to draw
    drawn = json.loads(run(
        "json", "-s", "desktop", "--db", prose_db, "-t", "P0",
        "--state", os.path.join(prose_dir, "workspace.json"),
    ).stdout)
    assert drawn["goal"] == "the heading that belongs to the shown list", drawn.get("goal")
    assert (drawn["done"], drawn["total"]) == (2, 2), (drawn["done"], drawn["total"])
    say("desktop: a committed row's own heading, now and nudge are read — the last part and "
        "the newest request: ok")

    # ---- ...and the heading is not always in the list's own row: an agent that states its goal
    #      once and then re-publishes the same list in later turns leaves it one row back — its
    #      own words are kept and the shared rule decides whether they still belong to this list
    #      (`_goal_choice`) and whether the reader should be told the list moved on
    #      (`goal_stale_at`). Both sides below are the shapes a real thread has.
    back_one = [{"kind": "text", "text": "Goal: stated once, one turn back"}]
    two_lists = [prose_parts[1], prose_parts[3]]
    write_prose(rows=[(300, "user", [{"kind": "text", "text": "tidy the footer"}],
                       prose_opened),
                      (301, "assistant", back_one, prose_list - 20_000),
                      (302, "assistant", two_lists, prose_list)])
    st = read_prose()
    assert st.get("goal") == "stated once, one turn back", st.get("goal")
    assert not st.get("goal_stale"), st
    write_prose(rows=[(300, "user", [{"kind": "text", "text": "the older ask"}],
                       prose_opened),
                      (301, "assistant", back_one, prose_list - 20_000),
                      (302, "user", [{"kind": "text", "text": "a newer ask"}],
                       prose_list - 10_000),
                      (303, "assistant", two_lists, prose_list)])
    st = read_prose()
    assert st.get("goal") == "stated once, one turn back", st.get("goal")
    assert st.get("goal_stale") is True, st
    say("desktop: a heading written one row back still heads the list — and is marked when "
        "the list's own turn opened after it: ok")

    # ---- ...and the LIVE history's prose must not be carried over a list it does not head: a
    #      heading from `harness_state` belongs to the harness's list, so when the committed row
    #      is what the pane is showing, its own prose answers — and when that row states no
    #      heading, the answer is NO heading (the honest `the agent owes a Goal: line`), never
    #      the live history's. The harness's list here predates the turn (`last_prompt_at` is
    #      newer), which is exactly when this happened; the committed row is the same lists
    #      with the headings taken out, so what the state carries IS the harness's leak.
    stale_history = {"sessionState": {"mainAgentState": {"messageHistory": [
        {"role": "assistant", "sentAt": prose_list - 40_000,
         "content": [{"type": "tool-call", "toolName": "write_todos",
                      "input": {"todos": prose_first}}]},
        {"role": "assistant", "sentAt": prose_list - 39_000,
         "content": [{"type": "text", "text": "Goal: the live history's own heading"}]},
    ]}}}
    write_prose(harness=stale_history, turn_state="running", beat=DESK_NOW - 1_000,
                opened=DESK_NOW - 5_000, parts=[prose_parts[1], prose_parts[3]])
    st = read_prose()
    assert st["turn_running"] is True and st["todos"] == prose_last, st
    assert st.get("goal") is None, st.get("goal")
    say("desktop: the live history's heading never heads a committed row's list: ok")

    # ---- ...and the committed rows say WHO asked, in a receipt rather than a tag: every user
    #      message the app writes carries the `input_id` of a `queue_items` row, whose `source`
    #      is the app's own word for the asker (`user` for the person, `assistant` for a prompt
    #      it wrote for itself, `mission-*`, `skill`) and whose `kind` says whether the row is a
    #      request at all. The harness calls the same distinction `USER_PROMPT`; skipping it
    #      let the app's own prompt stand as the newest ask (measured 2026-10-04 on the live
    #      store: the pane offered `Find how the desktop app marks a real user request` — an
    #      auto-run step — as `now`). One receipt the app cannot write for itself: its Continue
    #      button posts its fixed resume line through the ORDINARY send path, so only the words
    #      give it away.
    receipt_dir = os.path.join(TEST_HOME, "receiptstore")
    os.makedirs(receipt_dir, exist_ok=True)
    receipt_db = os.path.join(receipt_dir, "desktop-v2.db")
    if os.path.exists(receipt_db):
        os.remove(receipt_db)
    resume = "Continue the interrupted request from where you left off."

    def write_receipts(requests=(), harness=None, turn_state="idle", beat=0, opened=0):
        """The prose store again, with receipts: `requests` are (seq, text, source, kind, ts)."""
        con = sqlite3.connect(receipt_db)
        con.execute("CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, title TEXT,"
                    " status TEXT, turn_state TEXT, turn_alive_at INTEGER,"
                    " last_prompt_at INTEGER, sidebar_archived_at INTEGER, harness_state TEXT)")
        con.execute("CREATE TABLE IF NOT EXISTS messages (seq INTEGER, thread_id TEXT,"
                    " role TEXT, parts_json TEXT, ts INTEGER, input_id TEXT)")
        con.execute("CREATE TABLE IF NOT EXISTS queue_items (id TEXT PRIMARY KEY,"
                    " source TEXT, kind TEXT)")
        for table in ("threads", "messages", "queue_items"):
            con.execute(f"DELETE FROM {table}")
        con.execute("INSERT INTO threads VALUES ('R0', 'Receipt thread', 'open', ?, ?, ?, NULL, ?)",
                    (turn_state, beat, opened, json.dumps(harness) if harness else None))
        con.execute("INSERT INTO messages VALUES (200, 'R0', 'user', ?, ?, 'r-200')",
                    (json.dumps([{"kind": "text", "text": "tidy the footer"}]), prose_opened))
        con.execute("INSERT INTO queue_items VALUES ('r-200', 'user', 'prompt')")
        con.execute("INSERT INTO messages VALUES (201, 'R0', 'assistant', ?, ?, NULL)",
                    (json.dumps(prose_parts), prose_list))
        for seq, text, source, kind, ts in requests:
            con.execute("INSERT INTO messages VALUES (?, 'R0', 'user', ?, ?, ?)",
                        (seq, json.dumps([{"kind": "text", "text": text}]), ts, f"r-{seq}"))
            con.execute("INSERT INTO queue_items VALUES (?, ?, ?)", (f"r-{seq}", source, kind))
        con.commit()
        con.close()

    def read_receipts():
        return module.read_desktop(receipt_db, thread_id="R0", source="pinned", now_ms=DESK_NOW)

    # The newest row here is the app's OWN prompt; the pane must still show the user's ask.
    write_receipts(requests=[(202, "add a colour palette", "user", "prompt", DESK_NOW - 20_000),
                             (203, "audit every committed-row read", "assistant", "prompt",
                              DESK_NOW - 10_000)])
    st = read_receipts()
    assert st["now"] == "add a colour palette" and st["nudge"] is None, st
    # A row that is not a request at all — a skill activation, say — is not one either.
    write_receipts(requests=[(202, "/merge-local", "user", "skill-context", DESK_NOW - 10_000)])
    st = read_receipts()
    assert st["now"] is None and st["nudge"] is None, st
    # ...and the app's own resume line, which rides the ordinary send path (receipt `user`),
    # is not an ask: the request before it stands, and when it is the only thing there the
    # pane says nothing rather than reporting the app's button as the newest request.
    write_receipts(requests=[(202, "add a colour palette", "user", "prompt", DESK_NOW - 20_000),
                             (203, resume, "user", "prompt", DESK_NOW - 10_000)])
    st = read_receipts()
    assert st["now"] == "add a colour palette" and st["nudge"] is None, st
    write_receipts(requests=[(203, resume, "user", "prompt", DESK_NOW - 10_000)])
    st = read_receipts()
    assert st["now"] is None and st["nudge"] is None, st
    # The LIVE history gets the same treatment: the button's line is tagged `USER_PROMPT`
    # exactly like the user's words, so the tag alone cannot tell them apart — and here the
    # live list is the one the pane shows, which is the state the app is in right after the
    # button is pressed.
    resume_history = {"sessionState": {"mainAgentState": {"messageHistory": [
        {"role": "assistant", "sentAt": prose_list + 4_000,
         "content": [{"type": "tool-call", "toolName": "write_todos",
                      "input": {"todos": prose_last}}]},
        {"role": "user", "sentAt": prose_list + 5_000, "tags": ["USER_PROMPT"],
         "content": [{"type": "text", "text": resume}]},
    ]}}}
    write_receipts(harness=resume_history, turn_state="running", beat=DESK_NOW - 1_000,
                   opened=DESK_NOW - 120_000)
    st = read_receipts()
    assert st.get("live_list") is True and st["todos"] == prose_last, st
    assert st["now"] is None and st["nudge"] is None, st
    say("desktop: only a row the app's own receipt attributes to the user is a request — the "
        "app's prompts and its resume line are not: ok")

    # ---- `auto` asks the session WORKING in this directory, and a chat directory keeps its
    #      last chat forever: a finished CLI journal used to answer for a directory whose live
    #      session was in the desktop store, so the pane rendered a two-day-old list, frozen,
    #      while the app's own thread moved (the `09-30 07:12 · ALL DONE` pane of 2026-10-03).
    #      A finished journal is HELD now, and only answers when nothing fresher is here.
    dead = os.path.join(TEST_HOME, "deadchat")
    os.makedirs(dead, exist_ok=True)
    dead_log = os.path.join(dead, "log.jsonl")
    with open(dead_log, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"data": {"toolCalls": []}}) + "\n")
    stale_at = time.time() - (module.SOURCE_LIVE_MS / 1000.0) - 3600
    os.utime(dead_log, (stale_at, stale_at))

    def auto_pick(*extra, instance_pid=0):
        args = module.build_parser().parse_args(
            ["json", "-s", "auto", "--chat", dead, "--db", desk_db, "--state", desk_state,
             *extra]
        )
        return module._snapshot(args, cwd=TEST_HOME, instance_pid=instance_pid)

    assert not module._chat_is_live({"chat": dead}, None), (
        "a journal quiet past the live window is a finished session"
    )
    picked = auto_pick()
    assert picked["backend"] == "desktop", picked          # the live store answers instead
    # ...and the chain says so on the state, in its own words: this is what the pane's title
    # draws, so a list that looks wrong names the choice behind it
    assert picked["source_why"].startswith("cli finished "), picked
    assert picked["source_why"].endswith("→ desktop"), picked
    running = auto_pick(instance_pid=1234)
    assert running["backend"] == "cli", "a running Freebuff process keeps its chat"
    assert "source_why" not in running, (
        "nothing was passed over, so there is nothing to explain"
    )
    os.utime(dead_log, None)                               # the journal is alive again
    assert module._chat_is_live({"chat": dead}, None)
    assert auto_pick()["backend"] == "cli", "a working CLI session still answers"
    os.utime(dead_log, (stale_at, stale_at))
    cli_args = module.build_parser().parse_args(["json", "-s", "cli", "--chat", dead])
    single = module._snapshot(cli_args, cwd=TEST_HOME, instance_pid=0)
    assert single["backend"] == "cli", (
        "`-s cli` never falls through: asking for one source means exactly that"
    )
    assert "source_why" not in single, (
        "a chain of ONE source has no other source to explain away"
    )
    say("sources: `auto` follows the live store, not a finished chat's last list: ok")

    # ---- the three source paths nothing else in this suite reaches, each one a way a pane
    #      can end up answering from the wrong place. Found by reading the branches of
    #      `sources.py` against this suite (2026-10-04): `--project` never appears in it, the
    #      file source's own miss string appears nowhere, and the held-chat fallback — the one
    #      branch where `auto` deliberately answers a FINISHED chat — had no check at all.
    gap_root = os.path.join(TEST_HOME, "gaproot")
    proj_a = os.path.join(TEST_HOME, "gapprojA")
    proj_b = os.path.join(TEST_HOME, "gapprojB")
    for d in (proj_a, proj_b):
        os.makedirs(d, exist_ok=True)
    chat_b = os.path.join(gap_root, "gapprojB", "chats", "2026-01-01T00-00-00.000Z")
    os.makedirs(chat_b, exist_ok=True)
    b_step = "project B's own step"
    with open(os.path.join(chat_b, "log.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"timestamp": "2026-01-01T00:00:00.000Z",
                             "data": {"prompt": "work on B", "shouldEndTurn": False}}) + "\n")
        fh.write(json.dumps({"data": {"toolCalls": [
            {"toolName": "write_todos",
             "input": {"todos": [{"task": b_step, "completed": False}]}}]}}) + "\n")

    # (1) `--project`: the chat a DIRECTORY answers with is named by the directory's own
    # name, so a pane opened in a directory that has no chat of its own has nothing — and
    # `-p` is how a reader points a pane at a sibling project instead. It reaches
    # `cli_chat_dir` as the basename it is given, so this is a path of its own and not a
    # spelling of `--chat`: the cwd below is a project with NO chat, and the answer must come
    # from the one that was named.
    proj_args = module.build_parser().parse_args(
        ["json", "-s", "cli", "--cli-root", gap_root, "--project", "gapprojB"])
    by_project = module._snapshot(proj_args, cwd=proj_a, instance_pid=0)
    assert by_project["backend"] == "cli", by_project
    assert by_project["target"] == chat_b, (by_project["target"], chat_b)
    assert [t["task"] for t in by_project["todos"]] == [b_step], by_project
    # ...and the same directory with no project named is the miss it has always been, which
    # is what makes the flag worth having rather than a second spelling of the cwd.
    here_args = module.build_parser().parse_args(
        ["json", "-s", "cli", "--cli-root", gap_root])
    assert module._snapshot(here_args, cwd=proj_a, instance_pid=0)["backend"] is None, (
        "a directory with no chat of its own must not borrow a sibling project's"
    )
    say("sources: `--project` answers for the project it names, not this directory's: ok")

    # (2) the file source's own miss. `-s file:PATH` is the one backend a reader can point at
    # a path that does not exist, and its answer must say WHICH path rather than inherit the
    # last source's wording — a wrong-path typo is the whole failure a reader is chasing when
    # they read it.
    missing_path = os.path.join(TEST_HOME, "no-such-state.json")
    missing = module._snapshot(
        module.build_parser().parse_args(["json", "-s", f"file:{missing_path}"]),
        cwd=TEST_HOME, instance_pid=0)
    assert missing["backend"] == "file", missing
    assert missing["error"] == f"no state file at {missing_path}", missing
    assert missing["todos"] == [], missing
    say("sources: a file source with no file names the path it looked for: ok")

    # (3) the held-chat fallback: when the finished chat is all `auto` has, it answers with it
    # and SAYS SO. This is the branch the freeze fix pushed the work onto, and it is what keeps
    # `auto` from being a source that sometimes says nothing — an old list is a better answer
    # than an empty one, and `-s cli` would have said the same thing.
    chat_c = os.path.join(gap_root, "gapprojB", "chats", "2026-02-02T00-00-00.000Z")
    os.makedirs(chat_c, exist_ok=True)
    with open(os.path.join(chat_c, "log.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"data": {"prompt": "an earlier request",
                                      "shouldEndTurn": False}}) + "\n")
        fh.write(json.dumps({"data": {"toolCalls": [
            {"toolName": "write_todos",
             "input": {"todos": [{"task": "the finished chat's step",
                                 "completed": True}]}}]}}) + "\n")
        fh.write(json.dumps({"data": {"shouldEndTurn": True}}) + "\n")
    held_at = time.time() - 60_000
    os.utime(os.path.join(chat_c, "log.jsonl"), (held_at, held_at))
    held_args = module.build_parser().parse_args(
        ["json", "-s", "auto", "--cli-root", gap_root,
         "--db", os.path.join(TEST_HOME, "gapstores", "*", "desktop-v2.db"),
         "--state", os.path.join(TEST_HOME, "gapworkspace.json")])
    held = module._snapshot(held_args, cwd=proj_b, instance_pid=0)
    assert held["backend"] == "cli", held
    assert held["target"] == chat_c, (held["target"], chat_c)
    assert held["source_why"].startswith("cli finished "), held.get("source_why")
    assert held["source_why"].endswith("→ cli"), held.get("source_why")
    say("sources: a finished chat is still the answer when nothing fresher is: ok")

    # ---- ...and the clock it judges that by is the AGENT's, not the file's. The desktop app
    #      appends its own records to the same journal (`cli.feedback_button_hovered`, a note
    #      saved, a tab closed), so a chat whose turn ENDED an hour ago still had a fresh
    #      mtime, `auto` kept answering a live desktop thread with that finished list, and the
    #      pane sat frozen on one session's `ALL DONE` while the app worked (measured
    #      2026-10-04: unchanged across two tabs). The turn boundary decides instead.
    def agent_journal(name, *records):
        """A chat directory whose journal holds `records` newest-last, and is fresh."""
        chat = os.path.join(TEST_HOME, name)
        os.makedirs(chat, exist_ok=True)
        with open(os.path.join(chat, "log.jsonl"), "w", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec) + "\n")
        os.utime(os.path.join(chat, "log.jsonl"), None)
        return chat

    def at(seconds_ago, **data):
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(
            time.time() - seconds_ago)) + ".000Z"
        return {"timestamp": stamp, "data": data}

    ended_chat = agent_journal("endedchat",
                               at(3600, prompt="a request an hour ago", shouldEndTurn=False),
                               at(3500, fullResponse="the answer", iteration=9,
                                  shouldEndTurn=True),
                               # ...and then the APP touched the file, minutes later
                               at(120, eventId="cli.feedback_button_hovered",
                                  messageId="m1", source="cli"))
    assert module.journal_liveness(ended_chat)[1] is True, (
        "a journal whose newest turn boundary ended is a session waiting for you"
    )
    assert not module._chat_is_live({"chat": ended_chat}, None), (
        "an app append must not make a finished session the session WORKING here"
    )
    # ...and the pid has to BE one. `--watch-pid` is taken as given whenever that pid is
    # alive, and the shell wrapper passes one on every session, so in a directory the app
    # also works in a live pid with no Freebuff behind it is not evidence about the chat
    # whose finished journal is sitting there — measured 2026-10-04, the same freeze this
    # whole rule exists to prevent, reachable whenever a pid resolves. So the exception
    # says Freebuff and the pid's identity is asked. (The stand-in is a script, because
    # macOS SIGKILLs a copied /bin/sleep: AMFI takes the copy's signature, and `ps` then
    # shows no command line to recognise.)
    lbin = os.path.join(TEST_HOME, "livebin")
    os.makedirs(lbin, exist_ok=True)
    lfake = os.path.join(lbin, "freebuff")
    with open(lfake, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\nsleep 600 & wait\n")
    os.chmod(lfake, 0o755)
    live_fb = spawn_quiet(lfake)
    assert wait_for(lambda: live_fb.poll() is None
                    and live_fb.pid in module.freebuff_pids()), (
        "the stand-in freebuff never reached the process table"
    )
    try:
        assert module._chat_is_live({"chat": ended_chat}, live_fb.pid), (
            "a real Freebuff process behind an ended chat is still working it"
        )
    finally:
        kill_tree(live_fb)
    assert not module._chat_is_live({"chat": ended_chat}, os.getpid()), (
        "a live pid that is not Freebuff must not resurrect a finished session"
    )
    # ...and it is the journal's own turn, not a quiet window: this one ended a minute ago,
    # well inside `SOURCE_LIVE_MS`, and is still not a session working here.
    just_ended = agent_journal("justended", at(30, shouldEndTurn=True))
    assert not module._chat_is_live({"chat": just_ended}, None), (
        "a turn that ended inside the live window is finished all the same"
    )
    # ...while the two shapes that ARE working answer true, and the pane's other half — the
    # cached state it keeps drawing — drops the finished session the same way.
    working = agent_journal("workingchat",
                            at(30, prompt="keep going", shouldEndTurn=False),
                            at(20, toolCalls=[{"toolName": "write_todos"}],
                               shouldEndTurn=False))
    assert module._chat_is_live({"chat": working}, None), "a turn in flight is live"
    assert not module.journal_liveness(working)[1], "a turn in flight has not ended"
    waiting = agent_journal("waitingchat", at(30, shouldEndTurn=True),
                            at(20, prompt="and now the other half"))
    assert not module.journal_liveness(waiting)[1], (
        "a request after the turn ended puts the session back to work"
    )
    assert module._chat_is_live({"chat": waiting}, None), (
        "a fresh request is a working session whatever the previous turn ended"
    )
    fresh = int(time.time() * 1000)
    assert module.followed_session_over({"backend": "cli", "session": "E", "target": ended_chat,
                                         "store_mtime_ms": fresh}, None, fresh), (
        "a fresh heartbeat over an ended journal must not keep the pane on it"
    )
    assert not module.followed_session_over({"backend": "cli", "session": "W", "target": working,
                                             "store_mtime_ms": fresh}, None, fresh), (
        "a journal still being written keeps the pane"
    )
    assert module.followed_session_over(dict(live_cli, target=ended_chat), None, now_ms), (
        "a state written before `turn_ended` existed is still asked directly"
    )
    say("the clock `auto` follows: the agent's last record, not the app's mtime: ok")

    # ---- the two states a pane's close used to be DECIDED by, pinned against the code that
    #      owns them now. `session_still_live` — the helper that answered "is this session
    #      still live?" for the close rule — was deleted with the rule itself (2026-10-04,
    #      CHANGELOG: the pane's lifetime is its window's), so there is nothing left to pin
    #      BY NAME. What is pinned here is the behaviour those two states still have, because
    #      both were once the input to a close and neither may ever be one again.
    #
    #      An ERROR state is not a finished session. Nothing was read, nothing was watched,
    #      and there is no journal behind it that could have ended — so every reader of the
    #      question has to say so. The one that matters is the cached-state drop: answering
    #      True would send a pane back to `snapshot`, which would resolve the same error and
    #      cost a re-read to learn nothing.
    error_state = {"backend": None, "todos": [], "error": "no conversation DB found"}
    assert not module.followed_session_over(error_state, None, fresh), (
        "an error state is not a session that ended"
    )
    assert not module.followed_session_over(error_state, 4321, fresh), (
        "an error state is not a session that ended, instance or not"
    )
    # ...and the pane shows the error INSTEAD of a list, which is the half a reader sees: an
    # error says "no source here", where an empty list would say "a source with nothing
    # written yet" — two different situations that used to look alike.
    error_frame = STRIP(module.render(error_state, True, watching=None, width=68,
                                      now_ms=fresh, height=14, theme={}, truecolor=False))
    assert "no conversation DB found" in error_frame, error_frame
    assert "0%" not in error_frame and "todos" not in error_frame, error_frame

    #      An ENDED JOURNAL is the other one, and it is the opposite answer: the session it
    #      names IS over. Every reader of that fact must agree — `auto` will not choose the
    #      chat again, and the pane drops a cached list for it rather than freezing it at
    #      whatever the heartbeat last said.
    ended_state = {"backend": "cli", "session": "E", "target": ended_chat,
                   "turn_ended": True, "store_mtime_ms": fresh}
    assert module.followed_session_over(ended_state, None, fresh), (
        "an ended journal is a session that ended, however fresh the heartbeat is"
    )
    assert not module._chat_is_live({"chat": ended_chat}, None), (
        "an ended journal is not the session working in this directory"
    )
    # ...and the pane KEEPS drawing it, because a finished session is not a finished pane.
    # This is the pair the deleted helper used to collapse into one question, so it is worth
    # having both answers next to each other: ended at the SOURCE, alive at the window.
    ended_frame = STRIP(module.render(
        dict(ended_state, todos=[{"task": "the step the reader is reading", "completed": True}]),
        True, watching=None, width=68, now_ms=fresh, height=14, theme={}, truecolor=False))
    assert "the step the reader is reading" in ended_frame, ended_frame
    say("the two states a close used to be decided by: an error is not a finished session, "
        "and a finished session is not a finished pane: ok")

    # ---- ...and the tab the pane FOLLOWS is live where the app's own file is behind. The app
    #      persists `workspace.activeId` on a debounce, and it reached the disk minutes after
    #      a tab switch, so a pane reading only that file drew the tab you had already left.
    #      No live focus signal exists to read instead — the orchestrator API and the CDP
    #      bridge answer 401 to anything outside the app's own process (their tokens are
    #      minted in its bootstrap), the store has no focus column, and `turn_alive_at` is
    #      written ONE VALUE FOR ALL THREADS, so it cannot say which tab is yours. What IS
    #      per-thread is the ask, so `focused_thread` corrects the persisted tab with the
    #      thread being worked in — and only when the file is behind, so the app's own answer
    #      still wins whenever the two agree.
    focus_home = os.path.join(TEST_HOME, "focusproj")
    focus_dir = os.path.join(focus_home, "store")
    os.makedirs(focus_dir, exist_ok=True)
    focus_db = os.path.join(focus_dir, "desktop-v2.db")
    if os.path.exists(focus_db):
        os.remove(focus_db)
    with open(os.path.join(focus_dir, "project.json"), "w", encoding="utf-8") as fh:
        json.dump({"projectPath": focus_home}, fh)
    focus_con = sqlite3.connect(focus_db)
    focus_con.execute(
        "CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, status TEXT,"
        " sidebar_archived_at INTEGER, turn_state TEXT, last_prompt_at INTEGER)"
    )
    focus_con.execute(
        "CREATE TABLE messages (seq INTEGER, thread_id TEXT, parts_json TEXT, ts INTEGER)"
    )
    FOCUS_NOW = int(time.time() * 1000)

    def focus_thread(tid, title, asked_ms, state, task):
        focus_con.execute("INSERT INTO threads VALUES (?, ?, 'open', NULL, ?, ?)",
                          (tid, title, state, asked_ms))
        focus_con.execute(
            "INSERT INTO messages VALUES (?, ?, ?, ?)",
            (len(task), tid, desk_parts(desk_todos(task)), asked_ms or FOCUS_NOW),
        )

    focus_thread("A", "the tab the app still names", FOCUS_NOW - 3 * 3600_000, "idle",
                 ("the tab you left", False))
    focus_thread("B", "the tab you are working in", FOCUS_NOW - 30_000, "running",
                 ("the step you just sent", False))
    focus_con.commit()
    focus_state = os.path.join(focus_home, "state.json")
    with open(focus_state, "w", encoding="utf-8") as fh:
        json.dump({"workspace": {"activeId": "A", "tabs": [{"id": "A"}],
                                 "spaces": [{"projectPath": focus_home, "activeId": "A"}]}}, fh)

    behind = module.read_desktop(focus_db, source="active", state_path=focus_state, now_ms=FOCUS_NOW)
    assert behind["session"] == "B", behind
    assert behind["source"] == "live-tab" and behind["todos"][0]["task"] == "the step you just sent", behind
    assert behind["source_why"] == "A behind → B asked 0m ago", behind.get("source_why")
    # ...and when the app's file is RIGHT, the app's answer stands and nothing says otherwise:
    # a correction that fires on every poll would move the pane under the reader.
    with sqlite3.connect(focus_db) as fix:
        fix.execute("UPDATE threads SET turn_state = 'running', last_prompt_at = ? WHERE id = 'A'",
                    (FOCUS_NOW - 5_000,))
    agree = module.read_desktop(focus_db, source="active", state_path=focus_state, now_ms=FOCUS_NOW)
    assert agree["session"] == "A" and agree["source"] == "active-tab", agree
    assert not agree.get("source_why"), agree.get("source_why")
    # ...the persisted tab is answered whatever it is, and a thread asked outside the window
    # is not "where you are": the correction waits for the app's own write rather than guessing.
    # The window is the one a pane already uses to decide a thread is worth showing, because it
    # is sized by the lag it covers and not by how long a turn runs.
    with sqlite3.connect(focus_db) as fix:
        fix.execute("UPDATE threads SET last_prompt_at = ? WHERE id = 'A'",
                    (FOCUS_NOW - module.DESKTOP_FOCUS_MS - 60_000,))
        fix.execute("UPDATE threads SET last_prompt_at = ? WHERE id = 'B'",
                    (FOCUS_NOW - module.DESKTOP_FOCUS_MS - 60_000,))
        fix.execute("UPDATE threads SET turn_state = 'idle' WHERE id = 'A'")
    stale = module.read_desktop(focus_db, source="active", state_path=focus_state, now_ms=FOCUS_NOW)
    assert stale["session"] == "A", stale
    # ...and the newest ask wins when two threads are live and neither is the persisted tab.
    focus_thread("C", "the other one you are working in", FOCUS_NOW - 5_000, "running",
                 ("a newer step", False))
    focus_con.commit()
    focus_con.close()
    with sqlite3.connect(focus_db) as fix:
        fix.execute("UPDATE threads SET last_prompt_at = ? WHERE id = 'B'",
                    (FOCUS_NOW - 120_000,))
    two_live = module.read_desktop(focus_db, source="active", state_path=focus_state,
                                   now_ms=FOCUS_NOW)
    assert two_live["session"] == "C", two_live
    # A store from an app that writes neither column answers nothing, and `activeId` is then
    # the whole answer — the correction is additive, never a replacement.
    old_db = os.path.join(focus_dir, "old-v2.db")
    if os.path.exists(old_db):
        os.remove(old_db)
    old_con = sqlite3.connect(old_db)
    old_con.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, status TEXT,"
                    " sidebar_archived_at INTEGER)")
    old_con.execute("CREATE TABLE messages (seq INTEGER, thread_id TEXT, parts_json TEXT, ts INTEGER)")
    old_con.execute("INSERT INTO threads VALUES ('A', 'old tab', 'open', NULL)")
    old_con.execute("INSERT INTO threads VALUES ('B', 'old other', 'open', NULL)")
    old_con.execute("INSERT INTO messages VALUES (0, 'B', ?, ?)",
                    (desk_parts(desk_todos(("an old step", False))), FOCUS_NOW))
    old_con.commit()
    old_con.close()
    old = module.read_desktop(old_db, source="active", state_path=focus_state, now_ms=FOCUS_NOW)
    assert old["session"] == "A" and old["source"] == "active-tab", old
    say("the tab followed: the app's file first, its live work correcting it while it lags: ok")

    # ---- and `auto` asks that store for THIS directory's project, the way it already asks
    #      the CLI journal for this directory's chat (`cli_chat_dir` reads `basename(cwd)` and
    #      never walks up). The store's own answer used to be "the first project that contains
    #      this directory, newest store first" — and the home directory contains every path
    #      there is, so a pane in a repo under it was answered by the home project's session
    #      (measured 2026-10-03: a repo's pane drawing the home thread's list, over a
    #      repository the app had never opened). DEPTH now decides, and `auto` narrows it to
    #      the project whose path IS this directory; an explicit `-s desktop` was not told a
    #      directory, so it keeps the wider walk.
    pk = os.path.join(TEST_HOME, "pickdb")
    root = os.path.join(pk, "home")
    child = os.path.join(root, "repo")
    deep = os.path.join(child, "pkg")
    os.makedirs(deep, exist_ok=True)
    pick_pat = os.path.join(pk, "*", "desktop-v2.db")

    def make_store(name, project_path, age_s, task):
        """A one-thread store whose `project.json` names `project_path`."""
        d = os.path.join(pk, name)
        os.makedirs(d, exist_ok=True)
        db = os.path.join(d, "desktop-v2.db")
        if os.path.exists(db):
            os.remove(db)
        with open(os.path.join(d, "project.json"), "w", encoding="utf-8") as fh:
            json.dump({"projectPath": project_path}, fh)
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, status TEXT,"
                    " sidebar_archived_at INTEGER)")
        con.execute("CREATE TABLE messages (seq INTEGER, thread_id TEXT, parts_json TEXT,"
                    " ts INTEGER)")
        con.execute("INSERT INTO threads VALUES (?, ?, 'open', NULL)", (name, name + " thread"))
        con.execute("INSERT INTO messages VALUES (0, ?, ?, ?)",
                    (name, desk_parts(desk_todos((task, False))), DESK_NOW))
        con.commit()
        con.close()
        at = time.time() - age_s
        os.utime(db, (at, at))                       # the parent store is the NEWER one
        return db

    home_db = make_store("home", root, 0, "the home project's step")
    child_db = make_store("repo", child, 3600, "the repo project's step")
    assert module.pick_db(pick_pat, None, cwd=deep) == child_db, (
        "the deepest project that contains the cwd wins, whatever moved last"
    )
    assert module.pick_db(pick_pat, None, cwd=root) == home_db, (
        "a directory with no project of its own still belongs to the one that contains it"
    )
    assert module.pick_db(pick_pat, None, cwd=child, own_only=True) == child_db
    assert module.pick_db(pick_pat, None, cwd=deep, own_only=True) is None, (
        "a directory UNDER a project is not that project's own session"
    )
    assert module.pick_db(pick_pat, None, cwd=os.path.join(pk, "nowhere"),
                          own_only=True) is None
    assert module.pick_db(pick_pat, None, cwd=os.path.join(pk, "nowhere")) == home_db, (
        "nothing here at all: the fallback is still the newest store"
    )
    assert module.pick_db(pick_pat, child) == child_db, "a named project answers by its path"
    # two stores naming the SAME path: nothing to rank by depth, so the newest still wins
    newer_db = make_store("home2", root, -60, "the newer home project's step")
    assert module.pick_db(pick_pat, None, cwd=root, own_only=True) == newer_db, (
        "two stores for one path: the one that moved last answers"
    )
    say("desktop: the store for a directory is its OWN project, deepest first: ok")

    # ---- ...and `auto` is what narrows it, so it is checked at the seam `-s` is read at:
    #      a cwd inside a project gets no desktop answer, one that IS the project gets it.
    empty_root = os.path.join(TEST_HOME, "emptyroot")
    os.makedirs(empty_root, exist_ok=True)

    def auto_here(cwd, source="auto"):
        args = module.build_parser().parse_args(
            ["json", "-s", source, "--cli-root", empty_root, "--db", pick_pat,
             "--state", desk_state]
        )
        return module._snapshot(args, cwd=cwd, instance_pid=0)

    own = auto_here(child)
    assert own["backend"] == "desktop" and own["target"] == child_db, own
    assert own["session"] == "repo" and own["todos"][0]["task"] == "the repo project's step", own
    under = auto_here(deep)
    assert under.get("backend") != "desktop", (
        "`auto` in a directory under a project must not inherit that project's session"
    )
    assert auto_here(deep, "desktop")["target"] == child_db, (
        "`-s desktop` was told the store, not a directory, and still walks up"
    )
    say("sources: `auto` answers a directory's own project, never a parent's: ok")

    # ---- ...and the same switch when the live half is the APP, not another CLI chat: the
    #      pane's cached state names a finished CLI chat for this directory, and the session
    #      actually working here is a desktop thread. Everything below runs through the REAL
    #      CLI — `-s auto` resolving from the arguments a user types, `cli_chat_dir` naming the
    #      chat by the directory and `pick_db` naming the store by its `project.json` — and
    #      then watches the pane's own frames across several polls. The CLI→CLI check above
    #      proves the drop; this one proves WHERE it lands and that it does not slide back.
    #      (That last half is not decoration: an earlier build memoised the dropped session and
    #      trusted the still-unchanged cached file again on the next poll, so the pane painted
    #      the live thread for one frame and then went back to the finished one.)
    switch_root = os.path.join(TEST_HOME, "switchroot")
    proj_dir = os.path.join(TEST_HOME, "switchproj")
    os.makedirs(proj_dir, exist_ok=True)
    dead_chat = os.path.join(switch_root, os.path.basename(proj_dir), "chats",
                             "2026-02-02T00-00-00.000Z")
    os.makedirs(dead_chat, exist_ok=True)
    dead_step = "the finished CLI session's step"
    with open(os.path.join(dead_chat, "log.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "data": {"toolCalls": [{"toolName": "write_todos",
                                    "input": {"todos": [{"task": dead_step,
                                                        "completed": True}]}}]},
        }) + "\n")
    dead_at = time.time() - (module.SOURCE_LIVE_MS / 1000.0) - 1800
    os.utime(os.path.join(dead_chat, "log.jsonl"), (dead_at, dead_at))
    # the store for THIS directory and no other: its `projectPath` IS the directory
    switch_stores = os.path.join(TEST_HOME, "switchstores")
    store_dir = os.path.join(switch_stores, "proj")
    os.makedirs(store_dir, exist_ok=True)
    with open(os.path.join(store_dir, "project.json"), "w", encoding="utf-8") as fh:
        json.dump({"projectPath": proj_dir}, fh)
    switch_db = os.path.join(store_dir, "desktop-v2.db")
    if os.path.exists(switch_db):
        os.remove(switch_db)
    switch_step = "the desktop thread's live step"
    con = sqlite3.connect(switch_db)
    con.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, status TEXT,"
                " sidebar_archived_at INTEGER)")
    con.execute("CREATE TABLE messages (seq INTEGER, thread_id TEXT, parts_json TEXT, ts INTEGER)")
    con.execute("INSERT INTO threads VALUES ('SW', 'Switch thread', 'open', NULL)")
    con.execute("INSERT INTO messages VALUES (0, 'SW', ?, ?)",
                (desk_parts(desk_todos(("an earlier step", True), (switch_step, False))),
                 DESK_NOW))
    con.commit()
    con.close()
    switch_pat = os.path.join(switch_stores, "*", "desktop-v2.db")
    switch_state = os.path.join(TEST_HOME, "switchworkspace.json")

    # the cached state a watcher would have left behind for that finished chat: fresh,
    # matching, and an ended session
    switch_now = int(time.time() * 1000)
    with open(module.STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump({
            "schema": 1, "backend": "cli", "session": "2026-02-02T00-00-00.000Z",
            "status": "watching", "tool_version": module.VERSION,
            "heartbeat_ms": switch_now,
            "store_mtime_ms": switch_now - module.SOURCE_LIVE_MS - 1_800_000,
            "todos": [{"task": dead_step, "completed": True}],
        }, fh)
    # the resolution the pane runs, through the real CLI, from the directory's own cwd, with
    # that cached file sitting there: `json` and `bar` are reads and may serve it, but only
    # while it names a live session. `--watch-pid` names a pid that cannot be alive, so a
    # machine that happens to be running Freebuff in a parent directory resolves as this one
    # does; `-s cli` is asked the same question afterwards to prove the switch is `auto`'s
    # doing (`hold the finished journal, answer the live store`) and not the chat vanishing.
    seen_json = run("json", "-s", "auto", "--cli-root", switch_root, "--db", switch_pat,
                    "--state", switch_state, "--watch-pid", "999999", cwd=proj_dir).stdout
    assert dead_step not in seen_json, (
        "`fbtodo json` answered with the ended session's cached list\n" + seen_json
    )
    assert switch_step in seen_json, seen_json
    # `bar` carries no step text, only the count: the ended chat's list is 1/1 and the live
    # thread's is 1/2, so the count alone says which one was answered
    seen_bar = run("bar", "-s", "auto", "--cli-root", switch_root, "--db", switch_pat,
                   "--state", switch_state, "--watch-pid", "999999", cwd=proj_dir).stdout
    assert "todos 1/2" in seen_bar, (
        "`fbtodo bar` answered with the ended session's cached list\n" + seen_bar
    )
    doc = json.loads(run("json", "-s", "auto", "--cli-root", switch_root, "--db", switch_pat,
                         "--state", switch_state, "--watch-pid", "999999", cwd=proj_dir).stdout)
    assert doc["backend"] == "desktop" and doc["session"] == "SW", doc
    assert [t["task"] for t in doc["todos"]] == ["an earlier step", switch_step], doc
    cli_doc = json.loads(run("json", "-s", "cli", "--cli-root", switch_root,
                             cwd=proj_dir).stdout)
    assert cli_doc["backend"] == "cli" and [t["task"] for t in cli_doc["todos"]] == [dead_step], (
        cli_doc
    )
    master, slave = pty.openpty()
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 100, 0, 0))
    switch_proc = subprocess.Popen(
        [sys.executable, FB, "pane", "--no-daemon", "--interval", "0.2", "--stale-after", "0",
         "--cli-root", switch_root, "--db", switch_pat, "--state", switch_state,
         "--watch-pid", "999999"],
        cwd=proj_dir, stdin=slave, stdout=slave, stderr=slave, env=env, close_fds=True,
    )
    os.close(slave)
    os.set_blocking(master, False)
    seen = bytearray()
    settled_at = None
    ended = time.time() + 8.0
    try:
        while time.time() < ended:
            if settled_at is not None and time.time() - settled_at > 3.0:
                break  # the pane has drawn the same subject for several polls now
            ready, _, _ = select.select([master], [], [], 0.2)
            if not ready:
                continue
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            seen += chunk
            if settled_at is None and switch_step.encode() in seen:
                settled_at = time.time()
    finally:
        switch_proc.send_signal(signal.SIGINT)
        try:
            switch_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            switch_proc.kill()
        os.close(master)
    painted = STRIP(seen.decode("utf-8", "replace"))
    # The pane reaches the live thread, because the CACHED state is dropped before anything
    # else: `pane_cached_state` refuses a state whose session has ended, so the first poll the
    # pane accepts already answers the stores. That is the 2026-10-04 freeze fix and it is
    # untouched by the lock — which is why the pane never draws the finished chat here, and why
    # this half of the check is the same claim it was before.
    assert settled_at is not None, (
        "the pane never reached the live desktop thread\n" + painted[-800:]
    )
    assert dead_step not in painted, (
        "the pane painted the finished CLI session's list instead of the live thread\n"
        + painted[-800:]
    )
    # ...and then it STAYS: the lock is what makes this true by construction rather than by
    # luck, and a pane that flapped back would draw both subjects' rows into one window.
    tail = painted[-1200:]
    assert switch_step in tail and dead_step not in tail, tail
    # ...and the READ still says WHY this list is the app's, on its own title: the finished
    # chat is the whole reason a fresh resolve lands on the thread, and a reader running
    # `fbtodo why` should not have to guess. This is the read's note, not the pane's: a pane
    # that latched carries the session it latched to in its own title, which is the thing the
    # reader is being held on.
    assert doc["source_why"].startswith("cli finished "), doc["source_why"]
    assert doc["source_why"].endswith("→ desktop"), doc["source_why"]
    # ...and the `cli` half of the chain reaches the same note through `-s cli`'s own state:
    # a chain of one has nothing to explain, which is what keeps `-s cli` byte-identical
    assert "source_why" not in cli_doc, cli_doc.get("source_why")
    say("the pane: dropped a finished cached list, reached the live thread, and stayed there: ok")

    # ---- ...and the case the lock exists for: the reader types in ANOTHER tab, that thread
    #      starts running, and the app's own picker would answer with it. The pane must not.
    #      Two threads, both with a list, both live; the pane starts while only the FIRST is
    #      running, and the check then makes the SECOND the newest ask — the exact input the
    #      store's thread picker takes, and the one a following pane used to obey.
    lock_proj = os.path.join(TEST_HOME, "lockproj")
    os.makedirs(lock_proj, exist_ok=True)
    lock_store = os.path.join(TEST_HOME, "lockstores", "proj")
    os.makedirs(lock_store, exist_ok=True)
    with open(os.path.join(lock_store, "project.json"), "w", encoding="utf-8") as fh:
        json.dump({"projectPath": lock_proj}, fh)
    lock_db = os.path.join(lock_store, "desktop-v2.db")
    if os.path.exists(lock_db):
        os.remove(lock_db)
    first_step, second_step = "the first tab's step", "the second tab's step"
    con = sqlite3.connect(lock_db)
    con.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, status TEXT,"
                " turn_state TEXT, last_prompt_at INTEGER, sidebar_archived_at INTEGER)")
    con.execute("CREATE TABLE messages (seq INTEGER, thread_id TEXT, parts_json TEXT, ts INTEGER)")
    con.execute("INSERT INTO threads VALUES ('ONE', 'First tab', 'open', 'running', ?, NULL)",
                (DESK_NOW - 5_000,))
    con.execute("INSERT INTO threads VALUES ('TWO', 'Second tab', 'open', 'idle', ?, NULL)",
                (DESK_NOW - 9_000,))
    for tid, step in (("ONE", first_step), ("TWO", second_step)):
        con.execute("INSERT INTO messages VALUES (0, ?, ?, ?)",
                    (tid, desk_parts(desk_todos((step, False))), DESK_NOW))
    con.commit()
    con.close()
    lock_pat = os.path.join(TEST_HOME, "lockstores", "*", "desktop-v2.db")
    lock_state = os.path.join(TEST_HOME, "lockworkspace.json")
    lock_pane = spawn_quiet("sleep", "600")
    pid_l, fd_l = pty.fork()
    if pid_l == 0:
        os.environ["FBTODO_HOME"] = TEST_HOME
        os.chdir(lock_proj)
        os.execv(sys.executable, [sys.executable, FB, "pane", "--no-daemon", "-i", "0.3",
                                 "-s", "desktop", "--db", lock_pat, "--state", lock_state,
                                 "--watch-pid", str(lock_pane.pid)])

    def lock_drain(seconds):
        got = b""
        stop_at = time.time() + seconds
        while time.time() < stop_at:
            if not select.select([fd_l], [], [], 0.2)[0]:
                continue
            try:
                chunk = os.read(fd_l, 65536)
            except OSError:
                break
            if not chunk:
                break
            got += chunk
        return got.decode("utf-8", "replace")

    try:
        before = lock_drain(2.5)
        assert first_step in before, before[-600:]
        assert second_step not in before, (
            "a pane must draw one list, not every live thread's\n" + before[-600:]
        )
        # the reader types in the other tab: it starts running and becomes the newest ask,
        # which is what a fresh resolve would follow
        con2 = sqlite3.connect(lock_db)
        con2.execute("UPDATE threads SET turn_state = 'running', last_prompt_at = ?"
                     " WHERE id = 'TWO'", (DESK_NOW + 60_000,))
        con2.commit()
        con2.close()
        after = lock_drain(4.0)
        assert first_step in after, after[-600:]
        assert second_step not in after, (
            "the pane moved to another thread while the reader was on this one\n"
            + after[-600:]
        )
    finally:
        try:
            os.kill(pid_l, signal.SIGTERM)
            os.waitpid(pid_l, 0)
        except (ProcessLookupError, ChildProcessError):
            pass
        os.close(fd_l)
        kill_tree(lock_pane)
    say("a pane stays on its thread when another tab starts working: ok")

    # ---- ...and a poll that RAISES — the other half of the same hazard. The pane re-execs
    #      itself into whatever is on disk, and the reload probe can prove a tree PARSES and
    #      IMPORTS without proving its call sites still agree: measured 2026-10-03, an edit
    #      that changed a signature in one step and its call site in the next exec'd a live
    #      pane into the between-state, and the TypeError took the pane down mid-work with a
    #      traceback where the list was. A failed poll must cost one tick, not the pane: the
    #      frame shows the failure, the log records it ONCE rather than once a second, and the
    #      next tick can still answer — which is how a pane left running a broken build comes
    #      back on its own the moment that build is fixed.
    surv = os.path.join(TEST_HOME, "panesurvives")
    surv_proj = os.path.join(surv, "proj")
    surv_store_dir = os.path.join(surv, "stores", "p")
    os.makedirs(surv_proj, exist_ok=True)
    os.makedirs(surv_store_dir, exist_ok=True)
    with open(os.path.join(surv_store_dir, "project.json"), "w", encoding="utf-8") as fh:
        json.dump({"projectPath": surv_proj}, fh)
    surv_db = os.path.join(surv_store_dir, "desktop-v2.db")
    if os.path.exists(surv_db):
        os.remove(surv_db)
    os.makedirs(surv_db)          # a DIRECTORY where the store belongs: opening it raises
    surv_pat = os.path.join(surv, "stores", "*", "desktop-v2.db")
    surv_step = "the step after a failed poll"

    def surv_store():
        """Replace the directory with a real store: the next poll has an answer again.

        Built BESIDE it and moved into place. The check counts the logged failures, and a heal
        that spent its time between `os.rmdir` and `sqlite3.connect` left the store MISSING for
        a poll or two — a second, genuinely distinct error logged as the distinct failure it
        was, which failed the count on a race in this fixture (met 2026-10-04). Two syscalls
        back to back leave no window a 0.2 s poll can land in.
        """
        os.rmdir(surv_db)
        healed_db = surv_db + ".heal"
        if os.path.exists(healed_db):
            os.remove(healed_db)
        con = sqlite3.connect(healed_db)
        con.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, status TEXT,"
                    " sidebar_archived_at INTEGER)")
        con.execute("CREATE TABLE messages (seq INTEGER, thread_id TEXT, parts_json TEXT,"
                    " ts INTEGER)")
        con.execute("INSERT INTO threads VALUES ('SV', 'survivor', 'open', NULL)")
        con.execute("INSERT INTO messages VALUES (0, 'SV', ?, ?)",
                    (desk_parts(desk_todos((surv_step, False))), DESK_NOW))
        con.commit()
        con.close()
        os.rename(healed_db, surv_db)

    def pane_bytes(fd, seconds, stop_when=None):
        """What the pane painted in the next `seconds` — or until `stop_when` shows up."""
        out = bytearray()
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([fd], [], [], 0.2)
            if not ready:
                continue
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
            if stop_when is not None and stop_when.encode() in out:
                break
        return out

    master, slave = pty.openpty()
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 100, 0, 0))
    surv_proc = subprocess.Popen(
        [sys.executable, FB, "pane", "--no-daemon", "--interval", "0.2", "--stale-after", "0",
         "-s", "desktop", "--db", surv_pat, "--watch-pid", "999999"],
        cwd=surv_proj, stdin=slave, stdout=slave, stderr=slave, env=env, close_fds=True,
    )
    os.close(slave)
    os.set_blocking(master, False)
    try:
        broke = STRIP(pane_bytes(master, 3.0).decode("utf-8", "replace"))
        assert surv_proc.poll() is None, (
            "a poll that raised took the pane down\n" + broke[-600:]
        )
        assert "poll failed reading desktop" in broke, broke[-600:]
        surv_store()
        healed = STRIP(pane_bytes(master, 4.0, stop_when=surv_step).decode("utf-8", "replace"))
        assert surv_step in healed, (
            "the pane never polled again after its failure\n" + broke[-400:] + healed[-400:]
        )
        assert surv_proc.poll() is None, "the pane died after recovering"
    finally:
        surv_proc.send_signal(signal.SIGINT)
        try:
            survived = surv_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            surv_proc.kill()
            survived = "killed"
        os.close(master)
    assert survived == 130, (
        "a pane that has survived a bad poll must still leave cleanly on Ctrl-C: "
        f"{survived}"
    )
    # ...and the failure is in the log ONCE: ~15 polls failed in that window, and a pane that
    # logged each one would fill its own log with the same line
    try:
        with open(module.PANE_LOG_PATH, encoding="utf-8") as fh:
            pane_log_text = fh.read()
    except OSError:
        pane_log_text = ""
    assert pane_log_text.count("pane poll failed") == 1, (
        "the poll failure was logged more than once (or not at all):\n" + pane_log_text[-400:]
    )
    say("the pane: a poll that raises becomes the frame, and the next tick still answers: ok")

    # ---- the pane's own switch over the phone. A key inside the pane writes the notify
    #      kit's two state files — the very words `phone.sh` and `bell.sh` already read — so
    #      "quiet while I read this" is one keystroke instead of a file's name and a
    #      variable's name, and the pane has to be able to say it is quiet and undo it.
    #      Three halves, because each fails on its own: the decision (`apply_pane_key`), the
    #      chip that tells the reader the switch is in force, and the keyboard itself.
    mute_dir = os.path.join(TEST_HOME, "notify")
    shutil.rmtree(mute_dir, ignore_errors=True)
    os.makedirs(mute_dir, mode=0o700, exist_ok=True)
    with open(os.path.join(mute_dir, "state"), "w", encoding="utf-8") as fh:
        fh.write("on\n")            # the chime is loud; `phone-state` is not there at all
    set_knob(module, "NOTIFY_DIR", mute_dir)

    def mute_switches():
        out = {}
        for name, _env, _script, _what in module.MUTE_SWITCHES:
            try:
                with open(os.path.join(mute_dir, name), encoding="utf-8") as fh:
                    out[name] = fh.read().strip()
            except FileNotFoundError:
                out[name] = None
        return out

    quiet = {"done": 1, "total": 2, "session": "QUIET", "list_id": "q1",
             "todos": [{"task": "step one", "completed": True}, {"task": "step two"}]}
    finished = dict(quiet, done=2, todos=[{"task": "step one", "completed": True}])

    # A key that is not a switch key must change NOTHING: a stray byte in a pane's input
    # cannot silence a phone.
    assert module.apply_pane_key("q", quiet) == (None, ""), module.apply_pane_key("q", quiet)
    assert not module.muted_now() and mute_switches() == {"phone-state": None, "state": "on"}, (
        mute_switches()
    )
    chip, said = module.apply_pane_key("m", quiet)
    assert chip == "quiet until done · u", chip
    assert "phone.sh" in said and "bell.sh" in said, said
    assert mute_switches() == {"phone-state": "off", "state": "off"}, mute_switches()
    record = module.read_mute_record()
    assert record["until"] == "list" and record["was"] == {"phone-state": "", "state": "on"}, (
        record
    )
    # ...and the promise it made is kept while there is something left to read. A list that
    # is not there is not a finished list either: a pane muted before its agent planned
    # anything must stay quiet, or the mute lasts less than the keypress that set it.
    assert module.list_is_finished(quiet) is False, quiet
    assert module.list_is_finished({}) is False and module.list_is_finished(None) is False
    assert module.auto_unmute(quiet) is None, "a list with work left must stay quiet"
    assert mute_switches() == {"phone-state": "off", "state": "off"}, mute_switches()
    assert module.auto_unmute(finished), "a finished list must end the mute on its own"
    # ...and restoring RESTORES: the file that was never there is gone again, and the word
    # that was there is the word that is there now — `on`, not a guess at it.
    assert mute_switches() == {"phone-state": None, "state": "on"}, mute_switches()
    assert module.read_mute_record() == {}, module.read_mute_record()
    # `M` is the same switch with no automatic end, for a read that outlasts its list.
    chip, said = module.apply_pane_key("M", finished)
    assert chip == "muted · u" and module.read_mute_record()["until"] == "sticky", (chip, said)
    assert module.auto_unmute(finished) is None, "a sticky mute must not end itself"
    assert module.apply_pane_key("u", finished)[1] == "notifications back"
    assert mute_switches() == {"phone-state": None, "state": "on"}, mute_switches()
    # Someone else's mute is not this pane's to lift — and the chip has to say whose it is,
    # because a reader who pressed `u` and saw nothing happen needs to know that the pane is
    # not broken, the switch is simply not this pane's.
    with open(os.path.join(mute_dir, "phone-state"), "w", encoding="utf-8") as fh:
        fh.write("off\n")
    chip, said = module.apply_pane_key("u", finished)
    assert mute_switches()["phone-state"] == "off", (
        "an undo removed a switch this pane never wrote"
    )
    assert chip == "notifications off · m", chip
    os.unlink(os.path.join(mute_dir, "phone-state"))
    say("the pane's mute key: m writes the notify kit's own switch files, holds them off "
        "until this list finishes and puts back exactly what was there: ok")

    # ---- the chip: the one slot on a frame that belongs to the PROCESS rather than to the
    #      list, and the only place a reader can learn that the phone is off and which key
    #      undoes it. Drawn here by hand rather than read off a pane, so this half fails on
    #      its own if the title ever stops carrying the switch.
    chip_state = {"session": "QUIET", "backend": "cli", "goal": "a list to read",
                  "list_id": "q1", "list_version": 4,
                  "todos": [{"task": "step one", "completed": True}, {"task": "step two"}],
                  "done": 1, "total": 2}
    chip_title = STRIP(module.render(chip_state, True, width=100, height=24,
                                    mute_note="quiet until done · u")).splitlines()[0]
    assert "quiet until done · u" in chip_title, chip_title
    # ...it is the STANDING half of the title, so it survives a transient note taking the
    # chip for a few seconds: both are the process's own facts and both are said at once.
    both_title = STRIP(module.render(chip_state, True, width=100, height=24,
                                     reloaded="reloaded: build 4.30.2",
                                     mute_note="quiet until done · u")).splitlines()[0]
    assert "reloaded: build 4.30.2" in both_title and "quiet until done" in both_title, both_title
    assert "quiet until done" not in STRIP(
        module.render(chip_state, True, width=100, height=24)), "the chip is on with no switch"
    say("the pane's chip: the title says the notifications are off, and names the key that "
        "turns them back on: ok")

    # ---- and the keyboard, through the pane itself. A byte typed into a pane's own terminal
    #      must write the switch, say so, and come back on `u` — and the pane must hand its
    #      terminal attributes back on the way out, which is the half a pane that wedged in
    #      its own signal handler would never notice about itself.
    mute_state = os.path.join(TEST_HOME, "mute-state.json")
    module.atomic_write_json(mute_state, dict(chip_state, schema=1))
    # A colour TERM, because the chip is the FRAMED pane's chrome and the plain renderer has
    # none: a pane under a dumb terminal still writes the switch, it just has nowhere to say
    # so. (`penv` also names the notify kit: the pane is a separate process, and it has to be
    # pointed at this test's switches rather than at this machine's.)
    penv = dict(env, FBTODO_NOTIFY_DIR=mute_dir, TERM="xterm-256color", COLORTERM="truecolor")
    penv.pop("NO_COLOR", None)

    def pty_pane(extra=()):
        master, slave = pty.openpty()
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 100, 0, 0))
        before = termios.tcgetattr(master)
        proc = subprocess.Popen(
            [sys.executable, FB, "pane", "--no-daemon", "--interval", "0.2", "--tick", "1",
             "--stale-after", "0", "--watch-pid", "999999", "-s", f"file:{mute_state}",
             *extra],
            cwd=CWD, stdin=slave, stdout=slave, stderr=slave, env=penv, close_fds=True,
        )
        os.close(slave)
        os.set_blocking(master, False)
        return master, proc, before

    def pane_read(fd, seconds, stop_when=None):
        """What the pane painted in the next `seconds` — or until `stop_when` shows up."""
        out = bytearray()
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([fd], [], [], 0.2)
            if not ready:
                continue
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
            if stop_when is not None and stop_when.encode() in out:
                break
        return out

    def pane_stop(master, proc):
        """Ctrl-C a pane and report how it left: its exit code and its terminal."""
        proc.send_signal(signal.SIGINT)
        try:
            code = proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = "hung"
        after = termios.tcgetattr(master)
        os.close(master)
        return code, after

    master, key_proc, before_attrs = pty_pane()
    seen = bytearray()
    try:
        seen += pane_read(master, 4.0, stop_when="a list to read")
        os.write(master, b"m")
        # The keypress's own words are a NOTE and expire after `RELOAD_NOTE_S`; the standing
        # chip — the one naming the undo key, shown on every frame while the switch is in
        # force — is what the pane must still be painting a few seconds later.
        seen += pane_read(master, module.RELOAD_NOTE_S + 4.0, stop_when="quiet until done")
        assert mute_switches() == {"phone-state": "off", "state": "off"}, (
            "a keypress in the pane did not write the notify kit's switch files"
        )
        painted = STRIP(seen.decode("utf-8", "replace"))
        assert "quiet until done · u" in painted, (
            "the pane never said on its own title that the notifications are off\n"
            + painted[-600:]
        )
        os.write(master, b"u")
        pane_read(master, 4.0, stop_when="notifications back")
        assert mute_switches() == {"phone-state": None, "state": "on"}, (
            "u in the pane did not put the notifications back"
        )
    finally:
        key_code, key_after = pane_stop(master, key_proc)
    assert key_code == 130, f"a pane with the key reader must still leave on Ctrl-C, not {key_code}"
    assert key_after[:4] == before_attrs[:4], (
        "the pane left its terminal in key-reading mode for whatever runs after it"
    )

    # `--no-keys` is a claim about the KEYBOARD and nothing else: the pane still paints, and a
    # byte typed into it changes no switch — the opt-out for a pane whose terminal is not
    # its own to read.
    master, quiet_proc, _ = pty_pane(extra=("--no-keys",))
    try:
        pane_read(master, 3.0, stop_when="a list to read")
        os.write(master, b"m")
        pane_read(master, 2.0)
        assert mute_switches() == {"phone-state": None, "state": "on"}, (
            "a pane told to ignore the keyboard acted on a keypress anyway"
        )
    finally:
        quiet_code, _ = pane_stop(master, quiet_proc)
    assert quiet_code == 130, quiet_code
    say("the pane's keys: m writes the switch from inside the pane and says so on its own "
        "title, u gives the notifications back, --no-keys ignores both, and the terminal is "
        "handed back on exit: ok")

    # ---- the same switch without a keystroke: `fbtodo mute`, for a script, a status row,
    #      or a shell that is not a pane at all. It must be the SAME switch — the same two
    #      files, the same record, the same undo — and it carries one thing the keys cannot:
    #      a promise about a list that outlives the pane watching it, because nobody asked
    #      that pane for anything.
    mute_env = dict(env, FBTODO_NOTIFY_DIR=mute_dir)

    def mute_run(*args, timeout=30):
        return subprocess.run([sys.executable, FB, *args], capture_output=True, text=True,
                              env=mute_env, cwd=CWD, timeout=timeout)

    assert mute_switches() == {"phone-state": None, "state": "on"}, mute_switches()
    loud = mute_run("mute")
    # the row's own indent is part of what a `status`-shaped report promises, so compare the
    # line as written — `.strip()` would eat the leading indent and match a bare label.
    assert loud.returncode == 0 and loud.stdout.rstrip("\n") == "  notifications : on", loud
    # ...and no verb is `list`, because a report with no way to ask for one is half a command
    assert mute_run("mute", "list").stdout == loud.stdout, "no verb and `list` must agree"
    bad = mute_run("mute", "bogus")
    assert bad.returncode == 2 and "is not a verb here" in bad.stderr, bad
    assert mute_run("snap", "bogus").returncode == 2, "a verb on a command with none"

    quieted = mute_run("mute", "on")
    assert quieted.returncode == 0, quieted
    assert mute_switches() == {"phone-state": "off", "state": "off"}, mute_switches()
    record = module.read_mute_record()
    assert record["until"] == "sticky" and record["by"] == "command", record
    reported = mute_run("mute", "list")
    assert "off — sticky, by `fbtodo mute`" in reported.stdout, reported
    # ...and the document form, which has to be clean JSON (a script parses it) and to name
    # the switch that is actually off rather than only that something is.
    doc = json.loads(mute_run("mute", "--json").stdout)
    assert doc["muted"] is True and doc["scope"] == "sticky" and doc["by"] == "command", doc
    assert [s["script"] for s in doc["switches"]] == ["phone.sh", "bell.sh"], doc
    assert all(s["off"] and s["value"] == "off" for s in doc["switches"]), doc
    assert doc["chip"] == "muted · u", doc
    assert mute_run("mute", "off").returncode == 0, "off"
    assert mute_switches() == {"phone-state": None, "state": "on"}, mute_switches()
    assert json.loads(mute_run("mute", "--json").stdout)["muted"] is False
    # `off` twice is not an error and does not touch a switch of somebody else's making.
    assert mute_run("mute", "off").returncode == 0

    # `until-done` is a promise about a LIST, so it reads one rather than assuming it: asked
    # when the list is already finished it says so and leaves the phone alone, because a mute
    # whose condition is already met is a mute nobody would have wanted.
    finished_state = os.path.join(TEST_HOME, "mute-state-done.json")
    module.atomic_write_json(finished_state, dict(chip_state, schema=1, done=2,
                                                  todos=[{"task": "step one", "completed": True}]))
    already = mute_run("mute", "until-done", "-s", f"file:{finished_state}")
    assert already.returncode == 0 and "already finished" in already.stdout, already
    assert mute_switches() == {"phone-state": None, "state": "on"}, (
        "until-done muted a list that was already finished"
    )
    promised = mute_run("mute", "until-done", "-s", f"file:{mute_state}")
    assert promised.returncode == 0 and "until this list finishes" in promised.stdout, promised
    assert module.read_mute_record()["until"] == "list", module.read_mute_record()
    assert module.muted_now() is True, mute_switches()
    # ...and the two promises are told apart: the LIST lifts it (whoever asked), while a
    # pane merely CLOSING does not (nobody asked that pane).
    assert module.mute_release("the pane closed") == "", (
        "a closing pane lifted a mute `fbtodo mute` took"
    )
    assert module.muted_now(), "the closing pane lifted it anyway"
    assert module.auto_unmute({"done": 2, "total": 2}), (
        "a finished list must lift a mute made with `fbtodo mute until-done`"
    )
    assert not module.muted_now(), mute_switches()
    say("fbtodo mute: on, off, list and until-done drive the same switch as the pane's keys, "
        "and only the list lifts the promise a command made: ok")

    # ---- and the frame stacks them: a heading row per thread (its title, its own
    #      done/total, and whether it is the one being worked in), that thread's steps under
    #      it, and the frame still inside the height it was asked for.
    # Built by hand, not out of the reader above: this half is the RENDERER's, and it should
    # fail on its own if a stacked state ever stops drawing headings (a reader-only check
    # would pass a frame that quietly drew one list).
    pair = [
        {"id": "T0", "title": "Followed thread", "current": True, "running": True,
         "todos": [{"task": "its first step", "completed": True},
                   {"task": "its second step", "completed": False}],
         "source_updated_ms": DESK_NOW - 30_000},
        {"id": "T1", "title": "Other live thread", "current": False, "running": False,
         "todos": [{"task": "other one", "completed": True},
                   {"task": "other two", "completed": True},
                   {"task": "other three", "completed": False}],
         "source_updated_ms": DESK_NOW - 300_000},
    ]
    stacked = {"backend": "desktop", "session": "T0", "goal": "the followed thread's goal",
               "todos": pair[0]["todos"], "threads": pair, "list_version": 3,
               "source_updated_ms": DESK_NOW - 30_000}
    frame = module.render(stacked, True, watching=None, width=68, now_ms=DESK_NOW, height=18,
                          theme={}, truecolor=False)
    plain = module.render(stacked, False, width=68, now_ms=DESK_NOW)
    for text in ("Followed thread", "Other live thread", "its first step", "other three"):
        assert text in frame, (text, frame)
        assert text in plain, (text, plain)
    assert "1/2" in frame and "2/3" in frame, frame          # each thread's own count
    assert len(frame.splitlines()) <= 18, frame
    short = module.render(stacked, True, watching=None, width=40, now_ms=DESK_NOW, height=9,
                          theme={}, truecolor=False)
    assert len(short.splitlines()) <= 9, short
    # ...and a state with ONE thread renders exactly as the same state without the key, so
    # nothing about a single list moved: the goldens are the other half of that promise.
    alone = {k: v for k, v in stacked.items() if k != "threads"}
    assert module.render({**alone, "threads": [pair[0]]}, False, width=68,
                         now_ms=DESK_NOW) == module.render(alone, False, width=68, now_ms=DESK_NOW)
    # The followed thread's list is the state's own, not the thread entry's copy: a finished
    # list dropped for a newer request must not come back from under a heading.
    finished = dict(stacked, todos=[{"task": "the finished step", "completed": True}],
                    now="a newer request")
    module.finish_state(finished, {"session": "T0", "list_version": 1, "list_id": "x"})
    assert finished["todos"] == [], finished
    after_frame = module.render(finished, False, width=68, now_ms=DESK_NOW)
    assert "the finished step" not in after_frame, after_frame
    assert "Other live thread" in after_frame and "other three" in after_frame, after_frame
    # ...and the bar counts the lists the frame is DRAWING once the followed thread has none:
    # `0% (0/0)` sitting under a heading that says `2/3` is the frame contradicting itself,
    # which is what a reader sees as a bar stuck at zero on a list that is all ticked.
    assert module.drawn_counts(stacked) == (1, 2), module.drawn_counts(stacked)
    assert module.drawn_counts(finished) == (2, 3), module.drawn_counts(finished)
    after_rich = module.render(finished, True, watching=None, width=68, now_ms=DESK_NOW,
                               height=18, theme={}, truecolor=False)
    assert "67% (2/3)" in after_rich, after_rich
    assert "2/3 done" in after_frame, after_frame
    assert "0/0" not in after_rich and "0/0" not in after_frame, (after_rich, after_frame)
    say("desktop: a pane stacks the live threads, each under its own heading: ok")

    # ---- the app's own live signal: the store commits a row only when a turn CLOSES, so
    #      a thread that is WORKING has no list to read yet. `threads.turn_state` and
    #      `turn_alive_at` do move mid-turn, and the pane names the turn rather than
    #      blaming the agent for never calling `write_todos`.
    turned = os.path.join(TEST_HOME, "turnstore")
    os.makedirs(turned, exist_ok=True)
    turn_db = os.path.join(turned, "desktop-v2.db")
    if os.path.exists(turn_db):
        os.remove(turn_db)
    con = sqlite3.connect(turn_db)
    con.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, status TEXT,"
                " turn_state TEXT, turn_alive_at INTEGER, sidebar_archived_at INTEGER)")
    con.execute("CREATE TABLE messages (seq INTEGER, thread_id TEXT, parts_json TEXT, ts INTEGER)")
    con.execute("INSERT INTO threads VALUES ('R0', 'Working thread', 'open', 'running', ?, NULL)",
                (DESK_NOW - 2_000,))
    con.commit()
    con.close()

    def reason_with(state, beat_age_ms):
        c2 = sqlite3.connect(turn_db)
        c2.execute("UPDATE threads SET turn_state = ?, turn_alive_at = ?",
                   (state, DESK_NOW - beat_age_ms))
        c2.commit()
        c2.close()
        st = module.read_desktop(turn_db, thread_id="R0", source="pinned", now_ms=DESK_NOW)
        return st, module.no_list_reason(st)

    st, why = reason_with("running", 2_000)
    assert st["turn_running"] is True and why == "turn running · no list yet", (st, why)
    # A `running` state whose heartbeat has stopped is a turn that DIED, not one in flight
    st, why = reason_with("running", 10 * 60_000)
    assert st["turn_running"] is False, st
    assert why == "no write_todos call yet in this session", why
    st, why = reason_with("idle", 2_000)
    assert st["turn_running"] is False, st
    say("desktop: a running turn says so instead of blaming the agent: ok")

    # ---- ...and a running turn SHOWS ITS LIST, the way a CLI pane does mid-turn: the app
    #      keeps the agent's own state in `threads.harness_state`, whose
    #      `mainAgentState.messageHistory` carries the tool calls as they are made — so the
    #      newest `write_todos` there is the in-flight list, read with SQLite's own JSON
    #      walk (the blob grows with the whole session; a pane ticks once a second). Three
    #      rules make it the CURRENT list and not the last turn's: only while the turn is
    #      running, only when the list was written at or after the turn's own start
    #      (`last_prompt_at`), and only when it is not older than what is committed. The
    #      turn's start also lets `finish_state` drop a FINISHED previous list rather than
    #      read it as this turn's progress.
    live_dir = os.path.join(TEST_HOME, "livestore")
    os.makedirs(live_dir, exist_ok=True)
    live_db = os.path.join(live_dir, "desktop-v2.db")
    if os.path.exists(live_db):
        os.remove(live_db)
    live_todos = [{"task": "the in-flight step", "completed": False},
                  {"task": "a step still to come", "completed": False}]
    done_todos = [{"task": "the previous turn's step", "completed": True}]
    committed_ts = DESK_NOW - 60_000
    prompt_at = DESK_NOW - 30_000

    def harness_history(*messages) -> str:
        hist = []
        for kind, at, payload in messages:
            if kind == "todos":
                hist.append({"role": "assistant", "sentAt": at,
                             "content": [{"type": "tool-call", "toolName": "write_todos",
                                          "input": {"todos": payload}}]})
            elif kind == "prompt":
                hist.append({"role": "user", "sentAt": at, "tags": ["USER_PROMPT"],
                             "content": [{"type": "text", "text": payload}]})
            elif kind == "goal":
                hist.append({"role": "assistant", "sentAt": at,
                             "content": [{"type": "text", "text": payload}]})
            elif kind == "noise":        # a compaction summary or a tool-error injection
                hist.append({"role": "user", "sentAt": at, "tags": [payload[0]],
                             "content": [{"type": "text", "text": payload[1]}]})
        return json.dumps({"sessionState": {"mainAgentState": {"messageHistory": hist}}})

    def write_live(harness, prompt=prompt_at):
        con = sqlite3.connect(live_db)
        con.execute("CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, title TEXT,"
                    " status TEXT, turn_state TEXT, turn_alive_at INTEGER,"
                    " last_prompt_at INTEGER, sidebar_archived_at INTEGER, harness_state TEXT)")
        con.execute("CREATE TABLE IF NOT EXISTS messages (seq INTEGER, thread_id TEXT,"
                    " parts_json TEXT, ts INTEGER)")
        con.execute("DELETE FROM threads")
        con.execute("DELETE FROM messages")
        con.execute(
            "INSERT INTO threads VALUES ('R0', 'Working thread', 'open', 'running', ?, ?, NULL, ?)",
            (DESK_NOW - 2_000, prompt, harness),
        )
        con.execute(
            "INSERT INTO messages VALUES (1, 'R0', ?, ?)",
            (json.dumps([{"toolName": "write_todos", "input": {"todos": done_todos}}]),
             committed_ts),
        )
        con.commit()
        con.close()

    # a list written THIS turn: the pane reads it, and the turn's start rides with it
    write_live(harness_history(("todos", committed_ts, done_todos),
                               ("todos", DESK_NOW - 5_000, live_todos)))
    st = module.read_desktop(live_db, thread_id="R0", source="pinned", now_ms=DESK_NOW)
    assert st["turn_running"] is True and st["todos"] == live_todos, st
    assert st["ts"] == DESK_NOW - 5_000, st
    assert st["turn"] == {"start_ms": prompt_at}, st
    # ...and it is THIS turn's list, so the finished previous one is not what stands
    module.finish_state(st, None)
    assert st["todos"] == live_todos, st          # not dropped as last turn's progress
    assert st["cleared_turn"] is False, st
    # a list that PREDATES the turn is the previous turn's: the committed row must stand,
    # and (being finished and older than the new request) it is dropped as the CLI drops it
    write_live(harness_history(("todos", DESK_NOW - 40_000, done_todos)))
    st = module.read_desktop(live_db, thread_id="R0", source="pinned", now_ms=DESK_NOW)
    assert st["todos"] == done_todos and st["turn_running"] is True, st
    assert st["ts"] == committed_ts, st
    module.finish_state(st, None)
    assert module.no_list_reason(st) == "last turn's list is done — waiting for this turn's list", \
        module.no_list_reason(st)
    # the newest call may be a shape this build does not know: it is skipped, and the newest
    # list it CAN read stands rather than a half-read one
    write_live(harness_history(("todos", DESK_NOW - 5_000, live_todos),
                               ("todos", "bad", {"task": "not a list"})))
    assert module.read_desktop(live_db, thread_id="R0", source="pinned",
                               now_ms=DESK_NOW)["todos"] == live_todos
    # ---- ...and the same live history carries the REQUESTS, so a running desktop turn can
    #      say `now` (or `nudge` for a bare continuation) exactly as a CLI pane does. The
    #      rules are the CLI's, REUSED rather than re-written (`is_nudge` / `pick_prompt` /
    #      `_newest`, `desktop._now_and_nudge`): a request after the list is newer work the
    #      list does not describe; a `Goal:` heading written for it wins the line; a bare
    #      `continue` is a nudge that wins the slot; and a request the current list already
    #      answers is not drift. Only a USER_PROMPT message counts — the app tags compaction
    #      summaries and tool-error injections, and those are not something a person asked.
    args = types.SimpleNamespace(threads=1, thread_live=90,
                                 state=module.DEFAULT_WORKSPACE_STATE)

    def requests(history, prompt=prompt_at):
        write_live(history, prompt=prompt)
        return module.read_desktop(live_db, thread_id="R0", source="pinned", now_ms=DESK_NOW)

    # a request after the list: `now` names it
    st = requests(harness_history(("todos", DESK_NOW - 5_000, live_todos),
                                  ("prompt", DESK_NOW - 1_000, "add a colour palette")))
    assert st["todos"] == live_todos and st["now"] == "add a colour palette", st
    assert st["nudge"] is None, st
    # ...and when the agent has written a heading FOR that request, the heading is the line
    st = requests(harness_history(("todos", DESK_NOW - 5_000, live_todos),
                                  ("prompt", DESK_NOW - 1_000, "add a colour palette"),
                                  ("goal", DESK_NOW - 900, "Goal: a colour palette in the pane")))
    assert st["now"] == "a colour palette in the pane", st
    # a bare continuation is a NUDGE, and it wins the slot over `now`
    st = requests(harness_history(("todos", DESK_NOW - 5_000, live_todos),
                                  ("prompt", DESK_NOW - 1_000, "continue")))
    assert st["nudge"] == "continue" and st["now"] is None, st
    # the same words again is not drift: the list already answers that request
    st = requests(harness_history(("prompt", DESK_NOW - 9_000, "tidy the footer"),
                                  ("todos", DESK_NOW - 5_000, live_todos),
                                  ("prompt", DESK_NOW - 1_000, "tidy the footer")))
    assert st["now"] is None and st["nudge"] is None, st
    # a compaction summary or a tool-error injection is not a request, however it reads
    st = requests(harness_history(("todos", DESK_NOW - 5_000, live_todos),
                                  ("noise", DESK_NOW - 1_000,
                                   ("MODEL_COMPACTION", "Goal: continue from the summary"))))
    assert st["now"] is None and st["nudge"] is None, st
    # with no list to anchor on there is no `now`, but a nudge can still be said
    st = requests(harness_history(("prompt", DESK_NOW - 1_000, "continue")))
    assert st["nudge"] == "continue" and st["now"] is None, st
    # ...and the source forwards them, so a renderer can draw NOW / NUDGE
    write_live(harness_history(("todos", DESK_NOW - 5_000, live_todos),
                               ("prompt", DESK_NOW - 1_000, "add a colour palette")))
    now_obs = module.DesktopSource().describe(
        args, HOME, {"db": live_db, "thread": "R0", "mode": "pinned"})
    assert now_obs["now"] == "add a colour palette" and "nudge" not in now_obs, now_obs
    # ...and the same history carries the AGENT's `Goal:` heading for the list, read from the
    # same text parts and chosen by the CLI's own rule (`pick_goal`): a heading at or before
    # the list belongs to it, and the source forwards it so a renderer can head the list.
    st = requests(harness_history(("prompt", DESK_NOW - 9_000, "tidy the footer"),
                                  ("goal", DESK_NOW - 8_000, "Goal: a tidier footer"),
                                  ("todos", DESK_NOW - 5_000, live_todos)))
    assert st["goal"] == "a tidier footer" and st["goal_source"] == "agent", st
    assert st["now"] is None, st
    goal_obs = module.DesktopSource().describe(
        args, HOME, {"db": live_db, "thread": "R0", "mode": "pinned"})
    assert goal_obs["goal"] == "a tidier footer", goal_obs
    assert goal_obs["goal_source"] == "agent", goal_obs
    # with no list at all the newest heading is the statement of what is going on
    st = requests(harness_history(("goal", DESK_NOW - 9_000, "Goal: only a heading")))
    assert st["goal"] == "only a heading", st

    # ---- THE REPLAY: one list of cases through BOTH readers. `pick_goal` and
    #      `_now_and_nudge` are shared code, but the two stores are not, and a rule that is
    #      right in the CLI journal and wrong in the app's live history would only ever be
    #      caught by reading the SAME turn twice and comparing the answer. Each case below
    #      is written to a journal AND to a harness history, and the two states must carry
    #      the same `goal`, `now` and `nudge` — the fields a pane actually draws.
    replay_dir = os.path.join(TEST_HOME, "replaystore")
    os.makedirs(replay_dir, exist_ok=True)
    replay_db = os.path.join(replay_dir, "desktop-v2.db")
    if os.path.exists(replay_db):
        os.remove(replay_db)
    replay_chat = os.path.join(replay_dir, "chat")
    os.makedirs(replay_chat, exist_ok=True)
    REPLAY_TS = 1_700_000_000_000

    def _rep_rec(kind, at, payload):
        """One history entry, shaped for whichever reader this case is being replayed for.

        The harness history uses the app's own message shape (a role, `sentAt`, tags); the
        CLI journal uses one DEBUG record per event (the prompt in `data.prompt`, the prose
        in `data.fullResponse`, the list in a `write_todos` toolCall). Both are keyed by the
        same `at`, so the two positions line up and the two readers are comparing the same
        turn and not merely the same words.
        """
        return {"kind": kind, "at": at, "payload": payload}

    def _replay_harness(events) -> str:
        hist = []
        for ev in events:
            at, payload = ev["at"], ev["payload"]
            if ev["kind"] == "todos":
                hist.append({"role": "assistant", "sentAt": at,
                             "content": [{"type": "tool-call", "toolName": "write_todos",
                                          "input": {"todos": payload}}]})
            elif ev["kind"] == "prompt":
                hist.append({"role": "user", "sentAt": at, "tags": ["USER_PROMPT"],
                             "content": [{"type": "text", "text": payload}]})
            elif ev["kind"] == "goal":
                hist.append({"role": "assistant", "sentAt": at,
                             "content": [{"type": "text", "text": payload}]})
            elif ev["kind"] == "noise":
                hist.append({"role": "user", "sentAt": at, "tags": [payload[0]],
                             "content": [{"type": "text", "text": payload[1]}]})
        return json.dumps({"sessionState": {"mainAgentState": {"messageHistory": hist}}})

    def _replay_journal(events) -> None:
        # Removed first, so each case is a NEW inode: the scanner's cache is keyed on
        # (device, inode, size, mtime), and a rewrite of the same file that happened to land
        # on the same byte count could otherwise be answered from the previous case's walk.
        log_path = os.path.join(replay_chat, "log.jsonl")
        if os.path.exists(log_path):
            os.unlink(log_path)
        with open(log_path, "w", encoding="utf-8") as fh:
            for ev in events:
                at, payload = ev["at"], ev["payload"]
                data: dict = {"iteration": 1}
                if ev["kind"] == "todos":
                    data["toolCalls"] = [{"toolName": "write_todos",
                                          "input": {"todos": payload}}]
                elif ev["kind"] == "prompt":
                    data["prompt"] = payload
                elif ev["kind"] == "goal":
                    data["fullResponse"] = payload
                elif ev["kind"] == "noise":
                    data["toolResults"] = [payload[1]]
                fh.write(json.dumps({
                    "level": "DEBUG",
                    "timestamp": _ms_to_iso(at),
                    "data": data,
                }) + "\n")

    def _ms_to_iso(ms: int) -> str:
        import datetime as _dt
        return _dt.datetime.fromtimestamp(ms / 1000, _dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def _replay_desktop(events, prompt_at) -> dict:
        con = sqlite3.connect(replay_db)
        con.execute("CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, title TEXT,"
                    " status TEXT, turn_state TEXT, turn_alive_at INTEGER,"
                    " last_prompt_at INTEGER, sidebar_archived_at INTEGER, harness_state TEXT)")
        con.execute("CREATE TABLE IF NOT EXISTS messages (seq INTEGER, thread_id TEXT,"
                    " parts_json TEXT, ts INTEGER)")
        con.execute("DELETE FROM threads")
        con.execute("DELETE FROM messages")
        # A list this turn: `last_prompt_at` opens the turn and the beat is fresh, so the
        # newest list in the history is the live one — the anchor both readers measure
        # requests against (`_replay_both` checks the two readers landed on the same one).
        con.execute(
            "INSERT INTO threads VALUES ('Q0', 'Replay thread', 'open', 'running', ?, ?, NULL, ?)",
            (REPLAY_TS - 2_000, prompt_at, _replay_harness(events)),
        )
        con.commit()
        con.close()
        return module.read_desktop(replay_db, thread_id="Q0", source="pinned",
                                   now_ms=REPLAY_TS)

    def _replay_both(events, hit_at, prompt_at):
        _replay_journal(events)
        st_cli = module.read_cli(replay_chat)
        st_desk = _replay_desktop(events, prompt_at)
        if hit_at is not None:
            # Both readers must have anchored on the SAME list, or the fields below would be
            # compared across different turns and agree for the wrong reason. This is the
            # fixture checking itself: the list's own timestamp, read back from each store.
            assert st_cli.get("ts") == hit_at, ("cli anchored elsewhere", hit_at, st_cli.get("ts"))
            assert st_desk.get("ts") == hit_at, ("desktop anchored elsewhere", hit_at,
                                                st_desk.get("ts"))
        return st_cli, st_desk

    # (name, events, hit_at, prompt_at): the list's position and the turn's start are fixed
    # per case, exactly as they would be in a real store — only the goal/now/nudge fields
    # are compared, so every other field of the two states is free to differ.
    list_at, ask_at = REPLAY_TS - 30_000, REPLAY_TS - 60_000
    replay_cases = [
        ("heading before the list",
         [_rep_rec("prompt", ask_at, "tidy the footer"),
          _rep_rec("goal", ask_at + 1_000, "Goal: a tidier footer"),
          _rep_rec("todos", list_at, [{"task": "t", "completed": False}])],
         list_at, ask_at),
        ("request after the list is now",
         [_rep_rec("todos", list_at, [{"task": "t", "completed": False}]),
          _rep_rec("prompt", list_at + 1_000, "add a colour palette")],
         list_at, ask_at),
        ("heading written for the newer request",
         [_rep_rec("todos", list_at, [{"task": "t", "completed": False}]),
          _rep_rec("prompt", list_at + 1_000, "add a colour palette"),
          _rep_rec("goal", list_at + 2_000, "Goal: a colour palette in the pane")],
         list_at, ask_at),
        ("bare continuation is a nudge",
         [_rep_rec("todos", list_at, [{"task": "t", "completed": False}]),
          _rep_rec("prompt", list_at + 1_000, "continue")],
         list_at, ask_at),
        ("the same words again is not drift",
         [_rep_rec("prompt", ask_at, "tidy the footer"),
          _rep_rec("todos", list_at, [{"task": "t", "completed": False}]),
          _rep_rec("prompt", list_at + 1_000, "tidy the footer")],
         list_at, ask_at),
        ("a heading, no list",
         [_rep_rec("goal", ask_at, "Goal: only a heading")],
         None, ask_at),
        ("a nudge with no list to anchor on",
         [_rep_rec("prompt", ask_at, "continue")],
         None, ask_at),
        ("noise is not a request",
         [_rep_rec("todos", list_at, [{"task": "t", "completed": False}]),
          _rep_rec("noise", list_at + 1_000,
                   ("MODEL_COMPACTION", "Goal: continue from the summary"))],
         list_at, ask_at),
        # ...and a heading left over from an EARLIER turn than the list it heads: the new
        # request opened a new turn, a new list arrived, and no new heading did — so the old
        # one is STALE, and both readers must say so as well as agreeing on its words.
        ("stale heading from the previous turn",
         [_rep_rec("prompt", ask_at, "tidy the footer"),
          _rep_rec("goal", ask_at + 1_000, "Goal: a tidier footer"),
          _rep_rec("todos", list_at, [{"task": "t", "completed": False}]),
          _rep_rec("prompt", list_at + 1_000, "add a colour palette"),
          _rep_rec("todos", list_at + 2_000, [{"task": "u", "completed": False}])],
         list_at + 2_000, ask_at),
    ]
    for case_name, case_events, case_hit, case_prompt in replay_cases:
        st_cli, st_desk = _replay_both(case_events, case_hit, case_prompt)
        for field in ("goal", "now", "nudge", "goal_stale"):
            assert st_cli.get(field) == st_desk.get(field), (
                case_name, field, st_cli.get(field), st_desk.get(field))
        assert st_desk.get("goal_source") == ("agent" if st_cli.get("goal") else None), case_name
    # the stale case above is checked for its ANSWER, not only its two-way agreement
    st_cli, st_desk = _replay_both(replay_cases[-1][1], replay_cases[-1][2], replay_cases[-1][3])
    assert st_cli.get("goal") == "a tidier footer" and st_cli.get("goal_stale") is True, st_cli
    assert st_desk.get("goal_stale") is True, st_desk
    # ...and the SHARED rules are exercised too, so the two readers cannot agree by both
    # ignoring a rule: each case's expected field is checked as well as the two-way match.
    assert module.is_nudge("continue") and module.is_nudge("go on") and module.is_nudge("yes")
    assert not module.is_nudge("continue the other work") and not module.is_nudge("")
    goals_p = [(1, "g1"), (3, "g3")]
    prompts_p = [(2, "p2"), (4, "p4")]
    assert module.pick_goal(goals_p, prompts_p, 3) == "g3"
    assert module.pick_goal([], prompts_p, 3) is None
    assert module.pick_goal(goals_p, prompts_p, None) == "g3"
    # `goal_stale_at` itself: a heading before the request that opened the list's turn is a
    # leftover; one at or after it belongs to the list; with no request to bracket the turn
    # there is nothing to prove, so it is False rather than a guess.
    assert module.goal_stale_at([(1, "old")], [(2, "ask")], 3) is True
    assert module.goal_stale_at([(3, "new")], [(2, "ask")], 3) is False
    assert module.goal_stale_at([(1, "old")], [], 3) is False
    assert module.goal_stale_at([(1, "old")], [(2, "ask")], None) is False
    say("replay: the CLI journal and the desktop harness history answer `goal`, `now` and "
        "`nudge` identically for the same turn, across every case: ok")

    # an older store with no such column answers empty rather than failing
    bare = sqlite3.connect(turn_db)
    try:
        assert module.harness_turn(bare.cursor(), "R0") == {"todos": None, "at_ms": 0}
    finally:
        bare.close()
    # ...and the source FORWARDS all of it: the running flag and the turn reach the state
    # the renderers read, which is what `no_list_reason` and the pane's turn line want
    write_live(harness_history(("todos", DESK_NOW - 5_000, live_todos)))
    obs = module.DesktopSource().describe(args, HOME,
                                          {"db": live_db, "thread": "R0", "mode": "pinned"})
    assert obs["turn_running"] is True, obs
    assert obs["turn"] == {"start_ms": prompt_at}, obs
    assert obs["todos"] == live_todos, obs
    write_live(harness_history(("todos", DESK_NOW - 5_000, live_todos)), prompt=0)
    idle_obs = module.DesktopSource().describe(
        args, HOME, {"db": live_db, "thread": "R0", "mode": "pinned"})
    assert "turn" not in idle_obs, idle_obs          # no prompt, no turn to name
    say("desktop: a running turn shows the app's OWN in-flight list — the newest "
        "`write_todos` in `harness_state`, taken only for this turn's window — and the "
        "turn's start rides with it, so a finished previous list is dropped the way the "
        "CLI drops it; the same history says `now`/`nudge` for a request newer than the "
        "list, by the CLI's own rules, and carries the agent's own `Goal:` heading for it, "
        "read from the same prose: ok")

    # ---- one history MESSAGE holds a whole turn's parts, so two headings — or two lists —
    #      inside it share the message and are told apart by their PART: the walk is ordered by
    #      `msg.key, part.key` and a position names both, because a position that named only the
    #      message would tie and hand `_newest` the FIRST heading — the same first-vs-last trap
    #      the committed read was just fixed for, one store over.
    two_in_one = [{"role": "assistant", "sentAt": DESK_NOW - 5_000, "content": [
        {"type": "text", "text": "Goal: the first statement"},
        {"type": "tool-call", "toolName": "write_todos", "input": {"todos": done_todos}},
        {"type": "text", "text": "Goal: the statement that stands"},
        {"type": "tool-call", "toolName": "write_todos", "input": {"todos": live_todos}},
    ]}]
    write_live(json.dumps({"sessionState": {"mainAgentState": {"messageHistory": two_in_one}}}))
    st = module.read_desktop(live_db, thread_id="R0", source="pinned", now_ms=DESK_NOW)
    assert st["todos"] == live_todos, st["todos"]
    assert st["goal"] == "the statement that stands", st.get("goal")
    say("desktop: several calls in ONE history message — the last list and the last heading: ok")

    # ---- the big goal heading a list: the AGENT's own line, and drift since
    goals = os.path.join(TEST_HOME, "goalstore")
    os.makedirs(goals, exist_ok=True)

    def rec(prompt=None, todos=None, filler=None, prose=None, ended=None, calls=None):
        data = {"iteration": 1}
        if prompt is not None:
            data["prompt"] = prompt
        if prose is not None:
            data["fullResponse"] = prose
        if ended is not None:
            data["shouldEndTurn"] = ended
        if todos is not None:
            data["toolCalls"] = [
                {"toolName": "write_todos", "input": {"todos": todos}}
            ]
        if calls is not None:
            data["toolCalls"] = calls
        if filler is not None:
            data["toolResults"] = [filler]
        return {"level": "DEBUG", "timestamp": "2026-09-21T01:00:00.000Z", "data": data}

    def write_journal(rows):
        with open(os.path.join(goals, "log.jsonl"), "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    # no agent line, no goal: the request is NOT promoted into the heading (a quote is
    # the phrasing, not the objective), and the newer request still shows as `now`
    write_journal(
        [
            rec(prompt="add a goal line to the pane", todos=[{"task": "t", "completed": False}]),
            # "continue" is a nudge, not a goal: the newer REAL request must win
            rec(prompt="continue"),
            rec(prompt="and check why the list looks frozen"),
        ]
    )
    st_g = module.read_cli(goals)
    assert st_g["goal"] is None and st_g["goal_source"] is None, st_g
    assert st_g["now"] == "and check why the list looks frozen", st_g
    text_none = STRIP(module.render(dict(st_g, done=0, total=1), False, width=50))
    # the note wraps, so it is read flat: the words are what matter, not the line break
    assert "big goal · " + module.GOAL_MISSING_NOTE in " ".join(text_none.split()), text_none
    # ...and it is drawn in the warn yellow, not the muted grey: a note that only dims reads
    # as if there were nothing to report, which is how a skipped rule stayed invisible
    coloured = module.render(dict(st_g, done=0, total=1), True, width=50)
    note_row = next(ln for ln in coloured.splitlines() if "no heading" in ln)
    assert "\x1b[33m" in note_row, repr(note_row)
    # ...and the warning fires for exactly the shown list: a heading silences it, and a
    # cleared list (no todos) has nothing to head, so `finish_state` dropping a list does
    # not leave a warning pointing at nothing
    assert module.goal_missing_note({"goal": None, "todos": [{"task": "t"}]}) == \
        module.GOAL_MISSING_NOTE
    assert module.goal_missing_note({"goal": "a heading", "todos": [{"task": "t"}]}) == ""
    assert module.goal_missing_note({"goal": None, "todos": []}) == ""
    say("big goal: with no agent line the pane says so loudly instead of quoting you: ok")

    # a repeated request is not drift (the list already answers it)
    write_journal(
        [
            rec(prompt="same request here", todos=[{"task": "t", "completed": False}]),
            rec(prompt="same request here"),
        ]
    )
    st_g = module.read_cli(goals)
    assert st_g["now"] is None, st_g
    say("a repeated prompt is not reported as drift: ok")

    # ---- THE TURN: a boundary and a numerator, never a denominator. The request is the
    # boundary (the journal logs it on its own record as the turn starts), so the calls of
    # the previous turn must not be counted into this one's tally.
    write_journal(
        [
            rec(prompt="first turn",
                calls=[{"toolName": "str_replace", "input": {"path": "/a/one.md"}}]),
            rec(calls=[{"toolName": "run_terminal_command", "input": {"command": "ls"}}],
                ended=True),
            # the boundary: everything below this row belongs to the next turn
            rec(prompt="second turn"),
            rec(calls=[{"toolName": "str_replace", "input": {"path": "/a/two.md"}}],
                ended=False),
            rec(calls=[{"toolName": "run_terminal_command", "input": {"command": "make"}}],
                ended=False),
        ]
    )
    st_t = module.read_cli(goals)
    turn = st_t["turn"]
    assert turn["iterations"] == 2, turn
    assert turn["verbs"] == {"edited": 1, "ran": 1}, turn
    assert turn["files"] == ["two.md"], turn          # the other turn's file is not in it
    assert turn["truncated"] is False, turn
    say("a turn is bounded by the request that opened it, and counts only its own calls: ok")

    # ...and with no request in the store the counts are lower bounds, said out loud
    write_journal(
        [rec(calls=[{"toolName": "run_terminal_command", "input": {"command": "ls"}}],
             ended=False)]
    )
    st_t2 = module.read_cli(goals)
    assert st_t2["turn"]["truncated"] is True, st_t2["turn"]
    assert st_t2["turn"]["iterations"] == 1, st_t2["turn"]
    say("a turn whose start was never reached reports minimums, not a number: ok")

    # ---- the words: one line, no percentage, and the two honest suffixes
    note = module.turn_note(
        {"turn": {"start_ms": 40_000, "iterations": 1, "verbs": {"ran": 3}, "files": []}},
        100_000,
    )
    assert note == "turn 1m · 1 iteration · 3 calls", note
    capped = module.turn_note(
        {"turn": {"start_ms": 40_000, "iterations": 9, "verbs": {}, "files": ["a", "b"],
                  "truncated": True}},
        100_000,
    )
    assert capped == "turn 1m · 9+ iterations · 2+ files edited", capped
    quiet = module.turn_note(
        {"turn": {"start_ms": 40_000, "iterations": 2, "verbs": {}},
         "store_mtime_ms": 100_000 - 4 * 60_000},
        100_000,
    )
    assert quiet.endswith("quiet 4m"), quiet
    ended_note = module.turn_note(
        {"turn": {"start_ms": 40_000, "iterations": 2, "verbs": {}},
         "store_mtime_ms": 0, "turn_ended": True},
        100_000,
    )
    assert "quiet" not in ended_note, ended_note
    assert "%" not in quiet and "~" not in quiet, quiet
    say("the turn note is a sentence, not a percentage: ok")

    # ---- the summary the phone message carries under the heading: the agent's own
    # last sentence, which is the only place a "what happened" line can come from
    write_journal(
        [
            rec(
                prompt="why did it stop",
                todos=[{"task": "t", "completed": True}],
                prose="**Goal:** find why finish pings stopped\n\n"
                "Confirmed — the pipe was never the problem.\n\n| a | b |",
            )
        ]
    )
    st_s = module.read_cli(goals)
    assert st_s["summary"] == "Confirmed — the pipe was never the problem.", st_s
    say("summary: the first usable line of the agent's answer, markdown out: ok")

    # Markdown is stripped as well, because the phone shows this line bare: leaving the
    # emphasis markers in would make the message read as punctuation. It went missing for a
    # while unnoticed — the module had two helpers called `_plain`, the later one (ANSI off a
    # coloured line, for the renderer) shadowed the prose one, and this line was the only
    # caller of the older of the two.
    write_journal(
        [
            rec(
                prompt="a markdown answer",
                todos=[{"task": "t", "completed": True}],
                prose="**Goal:** x\n\n`fbtodo bar` shows *the box* now.\n",
            )
        ]
    )
    st_s = module.read_cli(goals)
    assert st_s["summary"] == "fbtodo bar shows the box now.", st_s
    say("summary: the phone's line has the markdown taken out: ok")

    write_journal(
        [rec(prompt="quiet turn", todos=[{"task": "t", "completed": True}], prose="")]
    )
    st_s = module.read_cli(goals)
    assert st_s["summary"] is None, st_s
    say("summary: an empty answer leaves the line out instead of blank: ok")

    runaway = "x" * (module.SUMMARY_MAX_CHARS + 40)
    write_journal(
        [rec(prompt="a long answer", todos=[{"task": "t", "completed": True}], prose=runaway)]
    )
    st_s = module.read_cli(goals)
    assert len(st_s["summary"]) == module.SUMMARY_MAX_CHARS, st_s
    assert st_s["summary"].endswith("…"), st_s
    say("summary: a runaway line is clipped to the phone's budget: ok")

    # A `Goal:` line is skipped, not quoted: the heading already carries it, and the phone
    # would otherwise show the same sentence twice.
    write_journal(
        [
            rec(
                prompt="goal only",
                todos=[{"task": "t", "completed": True}],
                prose="Goal: keep the notification short\n",
            )
        ]
    )
    st_s = module.read_cli(goals)
    assert st_s["goal"] == "keep the notification short", st_s
    assert st_s["summary"] is None, st_s
    say("summary: a bare Goal: line is not repeated as the summary: ok")

    # ---- a NUDGE after the list: "continue" is not a new task, but AGENTS.md asks for a
    #      fresh list before carrying on — and the pane says so until one arrives
    write_journal(
        [
            rec(prompt="add a goal line to the pane", todos=[{"task": "t", "completed": True}]),
            rec(prompt="continue"),
        ]
    )
    st_n = module.read_cli(goals)
    assert st_n["nudge"] == "continue", st_n
    nudge_txt = STRIP(
        module.render(dict(st_n, done=1, total=1), False, width=60, goal_lines=2)
    )
    assert "nudge · continue — rewrite the list, then continue" in nudge_txt, nudge_txt
    say("a nudge: `continue` asks for the list to be rewritten, and the pane says so: ok")

    # ...and it clears itself the moment the agent complies with a newer list
    write_journal(
        [
            rec(prompt="add a goal line to the pane", todos=[{"task": "t", "completed": True}]),
            rec(prompt="continue"),
            rec(todos=[{"task": "t", "completed": True}, {"task": "next", "completed": False}]),
        ]
    )
    st_n = module.read_cli(goals)
    assert st_n["nudge"] is None, st_n
    say("a nudge: the line clears as soon as a newer list exists: ok")

    # A continuation wears many hats, so it is recognised on WORDS, not length: every
    # phrasing below means "keep going" to the agent and must reach the pane, while a
    # prompt that adds real content stays a request — no heading is lost to the nudge slot.
    for phrase, is_cont in (
        ("continue", True),
        ("Continue!", True),
        ("continue please", True),
        ("ok, carry on then", True),
        ("keep going with it", True),
        ("go on", True),
        ("proceed", True),
        ("continue with the other work", False),
        ("keep going and fix the bell", False),
        ("pick up where the compaction left off", False),
    ):
        write_journal(
            [
                rec(prompt="add a goal line to the pane", todos=[{"task": "t", "completed": True}]),
                rec(prompt=phrase),
            ]
        )
        st_p = module.read_cli(goals)
        assert bool(st_p["nudge"]) == is_cont, (phrase, st_p)
    say("a nudge: any phrasing of `continue` nags, one that adds content does not: ok")

    # a real request after the nudge owns `now`, so there is nothing pending to nag about
    write_journal(
        [
            rec(prompt="add a goal line to the pane", todos=[{"task": "t", "completed": True}]),
            rec(prompt="continue"),
            rec(prompt="now also handle the empty-store case"),
        ]
    )
    st_n = module.read_cli(goals)
    assert st_n["nudge"] is None and st_n["now"], st_n
    say("a nudge: superseded by a real request, so only `now` shows: ok")

    # ---- `turn_ended`: what the finished-task bell rings on. It is the AGENT's own
    #      end-of-turn record, never a quiet journal — a long command writes nothing at
    #      all while it runs, and silence would ring in the middle of it.
    write_journal(
        [
            rec(prompt="ring when the task is done", todos=[{"task": "t", "completed": True}]),
            rec(ended=False, filler="run_terminal_command: 4 minutes of nothing written"),
            rec(ended=True, prose="done"),
        ]
    )
    st_t = module.read_cli(goals)
    assert st_t["turn_ended"] is True, st_t
    say("turn_ended: true once the agent's own end-of-turn record lands: ok")

    # the same journal one record earlier: still working, however quiet it has gone
    write_journal(
        [
            rec(prompt="ring when the task is done", todos=[{"task": "t", "completed": True}]),
            rec(ended=False, filler="run_terminal_command: 4 minutes of nothing written"),
        ]
    )
    st_t = module.read_cli(goals)
    assert st_t["turn_ended"] is False, st_t
    say("turn_ended: false while a tool call is in flight, however quiet: ok")

    # a request that came after the turn ended means the agent owes an answer again
    write_journal(
        [
            rec(prompt="first", todos=[{"task": "t", "completed": True}]),
            rec(ended=True, prose="done"),
            rec(prompt="and now do this instead"),
        ]
    )
    st_t = module.read_cli(goals)
    assert st_t["turn_ended"] is False, st_t
    say("turn_ended: false again when a newer request has no answer yet: ok")

    # ---- the boundary guard (`files_unlisted`): a turn that edited files but published no
    #      `write_todos` SINCE ITS LAST EDIT is not a finish, whatever the boxes say. Both
    #      halves are the journal's own — the newest edit's position, and the newest list's.
    write_journal(
        [
            rec(prompt="land the fix"),
            rec(todos=[{"task": "t", "completed": True}]),
            rec(calls=[{"toolName": "str_replace", "input": {"paths": ["/x/y.py"]}}]),
            rec(ended=True, prose="done"),
        ]
    )
    st_t = module.read_cli(goals)
    assert st_t["turn_ended"] is True and st_t["files_unlisted"] is True, st_t
    assert "changed, list not rewritten" in module.unlisted_note(st_t), st_t
    say("files_unlisted: true when files change after the list and the turn ends: ok")

    # the SAME turn with its list rewritten after the last edit: the list accounts for the
    # work, so the guard is silent and the bell may ring
    write_journal(
        [
            rec(prompt="land the fix"),
            rec(calls=[{"toolName": "str_replace", "input": {"paths": ["/x/y.py"]}}]),
            rec(todos=[{"task": "t", "completed": True}]),
            rec(ended=True, prose="done"),
        ]
    )
    st_t = module.read_cli(goals)
    assert st_t["turn_ended"] is True and st_t["files_unlisted"] is False, st_t
    say("files_unlisted: false once the list is rewritten past its last edit: ok")

    # ...and mid-turn there is no boundary to guard: the agent may still be about to write
    write_journal(
        [
            rec(prompt="land the fix"),
            rec(todos=[{"task": "t", "completed": True}]),
            rec(calls=[{"toolName": "str_replace", "input": {"paths": ["/x/y.py"]}}]),
        ]
    )
    st_t = module.read_cli(goals)
    assert st_t["files_unlisted"] is False, st_t
    say("files_unlisted: silent mid-turn — the boundary is where the bell decides: ok")

    # ---- the agent's own line is the heading: the journal keeps prose in fullResponse,
    # nowhere else, and AGENTS.md is what makes the agent write it
    write_journal(
        [
            rec(prose="Goal: head every list with its objective.",
                prompt="for each fbtodo list can you add thea big goal on the top"),
            rec(todos=[{"task": "t", "completed": False}]),
        ]
    )
    st_g = module.read_cli(goals)
    assert st_g["goal"] == "head every list with its objective.", st_g
    assert st_g["goal_source"] == "agent", st_g
    assert "for each fbtodo list" not in str(st_g["goal"]), st_g
    say("big goal: the agent's own Goal: line heads the list, not your wording: ok")

    # a line 1.6 MB deep is still found: the scan walks back past the filler, which is
    # what a real journal looks like hours into a session
    write_journal(
        [
            rec(prose="Goal: plant the heading before the list",
                prompt="plant the seed request"),
            rec(todos=[{"task": "t", "completed": False}]),
            *[rec(filler="x" * 8000) for _ in range(200)],
            rec(prompt="a much later request altogether"),
        ]
    )
    st_g = module.read_cli(goals)
    assert st_g["goal"] == "plant the heading before the list", st_g
    assert st_g["now"] == "a much later request altogether", st_g
    say("big goal: found past 1.6 MB of later journal, not just in the tail: ok")

    # markdown emphasis and bullets are what a reply actually looks like
    for prose in ("**Goal:** summarize the ask in one line.", "- Goal: summarize the ask in one line."):
        write_journal(
            [rec(prose=prose, prompt="do the thing"), rec(todos=[{"task": "t", "completed": False}])]
        )
        st_g = module.read_cli(goals)
        assert st_g["goal"] == "summarize the ask in one line.", (prose, st_g)
    say("big goal: `**Goal:**` and `- Goal:` forms are read too: ok")

    # prose that merely mentions the word is not a goal
    write_journal(
        [
            rec(prose="we discussed the goal: it was unclear", prompt="a proper request here"),
            rec(todos=[{"task": "t", "completed": False}]),
        ]
    )
    st_g = module.read_cli(goals)
    assert st_g["goal"] is None, st_g
    say("big goal: a mid-sentence 'goal:' is not mistaken for one: ok")

    # a newer Goal: line is what "now" shows — and a repeat is not drift
    write_journal(
        [
            rec(prose="Goal: fix the frozen pane.", prompt="the pane is frozen"),
            rec(todos=[{"task": "t", "completed": False}]),
            rec(prose="Goal: say big goal instead.", prompt="change the word"),
        ]
    )
    st_g = module.read_cli(goals)
    assert st_g["goal"] == "fix the frozen pane.", st_g
    assert st_g["now"] == "say big goal instead.", st_g
    write_journal(
        [
            rec(prose="Goal: same line both times.", prompt="p"),
            rec(todos=[{"task": "t", "completed": False}]),
            rec(prose="Goal: same line both times.", prompt="p2"),
        ]
    )
    st_g = module.read_cli(goals)
    assert st_g["goal"] == "same line both times.", st_g
    say("big goal: a newer Goal: line becomes `now`: ok")

    # prose and the tool call are separate iterations, so the line heading a turn often
    # lands just AFTER its list: with no newer request in between it is still that list's
    # goal — otherwise a turn that writes todos first would never get an agent heading
    write_journal(
        [
            rec(prompt="state the objective properly here"),
            rec(todos=[{"task": "t", "completed": False}]),
            rec(prose="Goal: state the objective."),
        ]
    )
    st_g = module.read_cli(goals)
    assert st_g["goal"] == "state the objective.", st_g
    assert st_g["goal_source"] == "agent" and st_g["now"] is None, st_g
    # ...but a line after a NEWER request belongs to that request, not to the list — and
    # a line from the PREVIOUS turn (before the newest request) still heads its own list
    write_journal(
        [
            rec(prompt="the first request"),
            rec(todos=[{"task": "t", "completed": False}]),
            rec(prose="Goal: the previous turn's heading."),
            rec(prompt="a newer request arrived"),
            rec(prose="Goal: handle the newer request."),
        ]
    )
    st_g = module.read_cli(goals)
    assert st_g["goal"] == "the previous turn's heading.", st_g
    assert st_g["now"] == "handle the newer request.", st_g
    say("big goal: turn boundaries decide which line heads the list: ok")

    # ---- the heading is SIZED for the strip: a compliant line renders whole, not cut
    base_state = {
        "backend": "cli", "session": "S", "todos": [{"task": "t", "completed": False}],
        "done": 0, "total": 1, "goal": "fit the pane heading whole",
    }
    assert len(base_state["goal"]) <= module.GOAL_MAX_CHARS

    def goal_lines_rendered(state, width, **kw):
        rendered = STRIP(module.render(state, False, width=width, **kw)).splitlines()
        # 11 spaces: the heading's own continuation indent (tasks use 6, so a 6-space
        # prefix would match them too)
        return [
            line for line in rendered if line.startswith(("big goal ·", "now ·", " " * 11))
        ]

    for width in (50, 46, 60):
        lines = goal_lines_rendered(base_state, width)
        assert lines and not lines[0].endswith("…"), (width, lines)
        assert len(lines) <= 2, (width, lines)
    # the wrapper's own strip: one line, whole, which is what GOAL_MAX_CHARS is sized for
    assert len(goal_lines_rendered(base_state, 50)) == 1, goal_lines_rendered(base_state, 50)
    # only a line that ignores the budget is cut, and then only past --goal-lines
    huge = dict(base_state, goal="word " * 200)
    assert goal_lines_rendered(huge, 46)[-1].endswith("…"), goal_lines_rendered(huge, 46)
    assert goal_lines_rendered(huge, 90)[-1].endswith("…"), goal_lines_rendered(huge, 90)
    assert len(goal_lines_rendered(huge, 90)) == 3, goal_lines_rendered(huge, 90)
    assert "big goal ·" not in STRIP(module.render(base_state, False, width=46, goal_lines=0))
    assert module.build_parser().parse_args(["--goal-lines", "0"]).goal_lines == 0
    # the request the list was written for is never rendered as the heading
    assert "now · and check" in STRIP(
        module.render(dict(base_state, now="and check the frozen pane"), False, width=46)
    ), STRIP(module.render(dict(base_state, now="and check the frozen pane"), False, width=46))
    say("big goal: a compliant line renders whole, and only overruns are elided: ok")

    # without the rule in AGENTS.md there is no line at all: the convention IS the feature,
    # so the file and the tool's budget are checked together. The operator's own
    # `~/AGENTS.md` is the authority when it is there; a runner has no such home, so the
    # copy this repository ships (docs/AGENTS.md) is checked instead — the phrases and
    # `GOAL_MAX_CHARS` still have to agree, on every machine.
    agents_md = os.path.join(HOME, "AGENTS.md")
    if not os.path.isfile(agents_md):
        agents_md = os.path.join(ROOT, "docs", "AGENTS.md")
    agents_text = open(agents_md, encoding="utf-8").read()
    assert "`Goal:`" in agents_text, f"{agents_md} lost the `Goal:` convention the pane reads"
    assert "concise rewrite" in agents_text, f"{agents_md} no longer asks for a concise goal"
    assert f"{module.GOAL_MAX_CHARS} characters" in agents_text, (
        f"{agents_md} and GOAL_MAX_CHARS ({module.GOAL_MAX_CHARS}) disagree on the budget"
    )
    say(f"big goal: {os.path.basename(agents_md)} asks for a line that fits, at the tool's "
        "own budget: ok")

    # ...and the other half of the convention: a list is only as true as its last call, so
    # the rule has to ask for the update as each step lands rather than in a batch
    assert "the moment a step lands" in agents_text, (
        f"{agents_md} no longer asks for the list to be updated as steps land"
    )
    assert "batch" in agents_text, f"{agents_md} dropped the reason it must not be batched"
    assert "On a bare continuation" in agents_text and "nudge" in agents_text, (
        f"{agents_md} no longer asks for a fresh list on `continue` — the pane's nudge line "
        "would then nag about a rule nobody has"
    )
    assert "steps in the list, not an epilogue" in agents_text, (
        f"{agents_md} no longer asks for the checks to BE steps in the list — a list that "
        "stops at the implementation hides the verification from the pane"
    )
    say("the list rule: it asks for the update the moment a step lands: ok")
    say("the list rule: and for a re-written list before continuing from a nudge: ok")
    say("the list rule: and for the checks themselves to be steps of it: ok")

    # ...and the skill that CARRIES the rule has to be OFFERED for the task in the first
    # place: Freebuff shows the model each skill's name and DESCRIPTION — nothing else —
    # until it loads one, so the trigger has to live in the description, and a skill marked
    # `disable-model-invocation` is never offered at all. The operator's skills sit outside
    # this repository; a runner has none, so this is checked where it is installed and named
    # as absent where it is not.
    skill_md = os.path.join(HOME, ".agents", "skills", "freebuff-todo-pane", "SKILL.md")
    if os.path.isfile(skill_md):
        skill_text = open(skill_md, encoding="utf-8").read()
        front = skill_text.split("---", 2)[1] if skill_text.startswith("---") else ""
        assert "name: freebuff-todo-pane" in front, f"{skill_md} lost its name"
        assert "disable-model-invocation" not in front, (
            f"{skill_md} is marked non-invocable, so the model is never offered it"
        )
        for phrase in ("whenever", "fbtodo", "Goal:"):
            assert phrase in front, (
                f"{skill_md}'s description no longer triggers on {phrase!r} — a task that "
                "touches fbtodo would not be offered the rule"
            )
        say("the list rule: the todo-pane skill asks to load on any fbtodo task: ok")
    else:
        say("the list rule: no todo-pane skill is installed here to check: ok")


    # a sentence where a heading belongs is reported, not silently clipped. Its own
    # FBTODO_HOME: the shared one holds the watcher's state, and doctoring that would
    # leak into every later check.
    long_goal = "rewrite the whole pane so that the heading can hold a long sentence"
    status_home = os.path.join(TEST_HOME, "status-home")
    os.makedirs(status_home, mode=0o700, exist_ok=True)
    module.atomic_write_json(
        os.path.join(status_home, "fbtodo-state.json"),
        {
            "backend": "cli", "session": "S", "tool_version": module.VERSION,
            "goal": long_goal, "goal_source": "agent",
            "heartbeat_ms": int(time.time() * 1000), "todos": [],
        },
    )
    status_out = STRIP(
        subprocess.run(
            [sys.executable, FB, "status"], capture_output=True, text=True, cwd=CWD,
            env=dict(env, FBTODO_HOME=status_home), timeout=30,
        ).stdout
    )
    assert long_goal[:40] in status_out, status_out
    assert "chars over" in status_out, status_out
    say("status: a goal too long for the strip is called out, not just clipped: ok")

    # ---- the estimate memory is auditable: which steps are remembered, the pace they
    #      set, and where each waiting step's number comes from
    now_ms = int(time.time() * 1000)
    module.atomic_write_json(
        os.path.join(status_home, "fbtodo-tasks.json"),
        {
            "schema": module.TASKLOG_SCHEMA, "session": "S",
            "tasks": {
                module.task_key("S", "Run the tests"): {
                    "started_ms": now_ms - 240_000, "done_ms": now_ms - 60_000,
                },
                module.task_key("S", "Deploy"): {
                    "started_ms": now_ms - 400_000, "done_ms": now_ms - 100_000,
                },
            },
        },
    )
    module.atomic_write_json(
        os.path.join(status_home, "fbtodo-state.json"),
        {
            "backend": "cli", "session": "S", "tool_version": module.VERSION,
            "heartbeat_ms": now_ms, "list_version": 3,
            "todos": [
                {"task": "Run the tests", "completed": False},
                {"task": "Deploy the app", "completed": False},
            ],
        },
    )
    hist_out = STRIP(
        subprocess.run(
            [sys.executable, FB, "status"], capture_output=True, text=True, cwd=CWD,
            env=dict(env, FBTODO_HOME=status_home), timeout=30,
        ).stdout
    )
    # the remembered spans are 3m and 5m, so the pace is their median, 4m — and since the
    # `own wording` rung was retired (it fired 0 times in 170 replayed steps), BOTH steps
    # ride that pace rather than one of them finding its own name in the log
    assert "remembered pace   : 4m00s" in hist_out, hist_out
    assert "2 remembered step(s)" in hist_out, hist_out
    assert "Run the tests" in hist_out and "~4m" in hist_out and "list pace" in hist_out, hist_out
    assert "Deploy the app" in hist_out and "list pace" in hist_out, hist_out
    say("status: the remembered pace and each step's estimate are shown: ok")

    # ---- the size memory is reported for what the LADDER can read of it, not for what is
    #      kept: a bucket under `SHAPE_MIN_BUCKET` is never looked up, so showing it beside the
    #      readable ones would read as evidence that is in use. Its own FBTODO_HOME.
    sizes_home = os.path.join(TEST_HOME, "sizes-home")
    os.makedirs(sizes_home, mode=0o700, exist_ok=True)
    module.atomic_write_json(
        os.path.join(sizes_home, "fbtodo-tasks.json"),
        {
            "schema": module.TASKLOG_SCHEMA, "session": "S",
            "tasks": {
                # one `calls2` size (4-7 calls), which the rung may never read...
                module.task_key("S", "small a"): {
                    "started_ms": now_ms - 300_000, "done_ms": now_ms - 60_000,
                    "shape": {"edited": 4},
                },
                # ...and two `calls4` ones (16-31), both 4m, which it may
                module.task_key("S", "big a"): {
                    "started_ms": now_ms - 300_000, "done_ms": now_ms - 60_000,
                    "shape": {"edited": 16},
                },
                module.task_key("S", "big b"): {
                    "started_ms": now_ms - 300_000, "done_ms": now_ms - 60_000,
                    "shape": {"edited": 17},
                },
            },
        },
    )
    module.atomic_write_json(
        os.path.join(sizes_home, "fbtodo-state.json"),
        {"backend": "cli", "session": "S", "tool_version": module.VERSION,
         "heartbeat_ms": now_ms, "list_version": 1,
         "todos": [{"task": "Run the tests", "completed": False}]},
    )
    sizes_out = STRIP(
        subprocess.run(
            [sys.executable, FB, "status"], capture_output=True, text=True, cwd=CWD,
            env=dict(env, FBTODO_HOME=sizes_home), timeout=30,
        ).stdout
    )
    assert "remembered sizes  : 2 kept — calls4 ~4m (n=2)" in sizes_out, sizes_out
    assert "1 not read yet (1 under the calls4 floor)" in sizes_out, sizes_out
    assert "calls2" not in sizes_out.split("remembered sizes")[1].split("\n")[0], sizes_out
    say("status: a size the rung may not read is counted, not shown as evidence: ok")

    # ---- the memory is PER MODEL. Measured 2026-09-26: the same step on
    #      `deepseek/deepseek-v4-flash` and on `stealth/space-bunny-alpha` differed by more
    #      than the whole estimate, so a remembered span from one must not project the
    #      other. Records carry the model; the history prefers this session's model and
    #      falls back to every model only while the current one has nothing of its own.
    fast, slow = "stealth/space-bunny-alpha", "deepseek/deepseek-v4-flash"
    keyed = {"schema": module.TASKLOG_SCHEMA, "session": "S", "tasks": {}}
    for name, model, span in (("Run the tests", fast, 30_000), ("Deploy", slow, 600_000)):
        keyed["tasks"][module.task_key("S", name)] = {
            "started_ms": now_ms - span - 60_000, "done_ms": now_ms - 60_000, "model": model,
        }
    # an entry is a small record now, not a bare number: the median is still what a caller
    # reads, and `n` says how many samples stand behind it
    meds = lambda h: {k: module.hist_med(v) for k, v in h.items()}  # noqa: E731
    counts = lambda h: {k: v["n"] for k, v in h.items()}  # noqa: E731
    assert meds(module.task_history_from_log(keyed["tasks"], now_ms, fast)) == {
        "Run the tests": 30_000}
    assert meds(module.task_history_from_log(keyed["tasks"], now_ms, slow)) == {
        "Deploy": 600_000}
    assert counts(module.task_history_from_log(keyed["tasks"], now_ms, fast)) == {
        "Run the tests": 1}
    # a model with no history of its own still gets a number, borrowed from any model
    borrowed = meds(module.task_history_from_log(keyed["tasks"], now_ms, "some/new-model"))
    assert borrowed == {"Run the tests": 30_000, "Deploy": 600_000}, borrowed
    # ...and with no model named at all, nothing is filtered
    assert meds(module.task_history_from_log(keyed["tasks"], now_ms)) == borrowed
    say("estimates: a step's remembered span is kept per model, not pooled: ok")

    # ---- the SIZE memory: the same span, keyed by how big a step was rather than what it
    #      was called. Measured 2026-09-29 over 258 replayed steps, the call COUNT carries
    #      more of a step's duration than the verb mix did (out-of-sample R^2 0.645 vs
    #      0.390), so the signature is the log2-binned count.
    shaped = {"schema": module.TASKLOG_SCHEMA, "session": "S", "tasks": {}}
    for name, verbs, span in (
        ("Fix the parser", {"edited": 4, "ran": 1}, 60_000),      # 5 calls -> calls2
        ("Repair the tokenizer", {"edited": 5, "ran": 1}, 90_000),  # 6 calls -> calls2
        ("Run the tests", {"ran": 1}, 20_000),                    # 1 call  -> calls0
    ):
        shaped["tasks"][module.task_key("S", name)] = {
            "started_ms": now_ms - span - 60_000, "done_ms": now_ms - 60_000,
            "model": fast, "shape": verbs,
        }
    sh = module.shape_history_from_log(shaped["tasks"], now_ms, fast)
    # 5 and 6 calls land in the SAME bucket, which is the whole point: two differently-worded
    # steps of one size, pooled into a median of 1m15s
    assert set(sh) == {"calls2", "calls0"}, sh
    # the entry carries the evidence with the number: how many samples, and how far apart
    # they were — one sample has no spread and so has no `lo`/`hi` at all
    assert sh["calls2"] == {
        "med": 75_000, "n": 2, "lo": 60_000, "hi": 90_000}, sh
    assert sh["calls0"] == {"med": 20_000, "n": 1}, sh
    # the signature is the total call count, binned by powers of two: 1, 2-3, 4-7, 8-15
    assert module.shape_of({"edited": 9, "ran": 2}) == "calls3", module.shape_of(
        {"edited": 9, "ran": 2})
    assert module.shape_of({"edited": 1}) == "calls0" and module.shape_of({"edited": 3}) == "calls1"
    assert module.shape_of({"edited": 4}) == "calls2" and module.shape_of({"edited": 9}) == "calls3"
    assert module.shape_of({}) == "" and module.shape_of(None) == ""
    assert module.shape_of({"searched the web": 1}) == "calls0"
    # a step whose calls were never recorded gets no bucket rather than an empty one
    assert module.shape_history_from_log(
        {module.task_key("S", "nothing"): {"started_ms": 1, "done_ms": 2}}, now_ms) == {}
    say("estimates: a step's size pools steps worded differently: ok")

    # ---- the ladder: the size memory, then the list's pace — and a size may only answer
    #      once SHAPE_MIN_SAMPLES steps of that size stand behind it. The `own wording` rung
    #      is gone: it fired 0 times in 170 replayed steps.
    assert module.task_estimate_ms("anything", 600_000, {"calls4": {"med": 30_000, "n": 2}},
                                   "calls4") == 30_000
    assert module.task_estimate_ms("anything", 600_000, {"calls4": {"med": 30_000, "n": 1}},
                                   "calls4") == 600_000   # one sample: not yet
    assert module.task_estimate_ms("anything", 600_000, {}, "calls4") == 600_000
    assert module.task_estimate_ms("anything", 600_000, {}, "") == 600_000
    # ---- ...and a RUNNING step below the floor is not sized at all, however many samples that
    #      bucket holds: a partial tally names a smaller step than the one it is going to be, so
    #      the memory for `calls3` describes the steps that STOPPED at 8-15 calls (see the
    #      constant). This is the whole reason the ladder reads through `sized_entry`.
    assert module.SHAPE_MIN_BUCKET == 4
    assert module.sized_entry({"calls3": {"med": 30_000, "n": 9}}, "calls3") is None
    assert module.sized_entry({"calls4": {"med": 30_000, "n": 2}}, "calls4") == {
        "med": 30_000, "n": 2}
    assert module.sized_entry({"calls8": {"med": 30_000, "n": 9}}, "calls8") == {
        "med": 30_000, "n": 9}
    assert module.sized_entry({"calls4": {"med": 30_000, "n": 1}}, "calls4") is None
    assert module.sized_entry(None, "calls4") is None and module.sized_entry({}, "") is None
    # a bucket name that is not one of ours is no size either, rather than a crash
    for junk in ("calls", "callsX", "edited4", "calls-1", "calls02"):
        assert module.sized_entry({"calls4": {"med": 30_000, "n": 2}}, junk) is None, junk
    assert module.task_estimate_ms("anything", 600_000, {"calls3": {"med": 30_000, "n": 9}},
                                   "calls3") == 600_000
    assert module.task_estimate_ms("anything", 600_000, {"calls3": {"med": 30_000, "n": 9}},
                                   "calls4") == 600_000   # a size nobody has seen yet
    # the memory still RECORDS every bucket: the floor is on the lookup, not on what is kept,
    # so the evidence for moving the floor stays in the log
    assert module.shape_history_from_log({
        module.task_key("S", "small"): {"started_ms": 1, "done_ms": 20_001,
                                         "model": "m", "shape": {"edited": 2}},
    }, 60_000, "m") == {"calls1": {"med": 20_000, "n": 1}}
    say("estimates: the size answers only for a step big enough, and only behind one: ok")

    # ---- a WAITING step has made no calls, so it has no size — but it does have its
    #      wording, and its kind of work is a real predictor. Measured 2026-09-29 over the
    #      170 ticked steps recovered from the CLI journals: `class x seconds-per-call`,
    #      blended 50/50 in log space with the pace, beats the pace alone on the median
    #      (2.10x against 2.89x), the mean (3.63x against 4.71x) and the p90 (6.37x vs
    #      8.48x), and wins on 74% of steps. Alone the label model wrecks the tail, so it is
    #      only ever a half of the answer.
    assert module.label_class("Run the tests") == "run"
    assert module.label_class("Update the README") == "docs"
    assert module.label_class("Deploy to the cluster") == "deploy"
    assert module.label_class("frobnicate the widget") == "other"
    cm = {"classes": {"run": 4, "edit": 8}, "calls": 5, "rate_ms": 30_000}
    # 4 calls x 30s = 2m, blended with an 8m pace = sqrt(120_000 * 480_000) = 4m
    assert module.pending_blend_ms("Run the tests", 480_000, cm) == 240_000
    assert module.task_estimate_ms("Run the tests", 480_000, {}, "", cm) == 240_000
    assert module.pick_estimate("Run the tests", {}, "", 480_000, cm) == (240_000, "blend")
    # a size that IS known still wins: the blend is for rows with no calls yet
    assert module.pick_estimate(
        "Run the tests", {"calls4": {"med": 90_000, "n": 2}}, "calls4", 480_000, cm
    ) == (90_000, "shape")
    # ...but a bucket below the floor hands the number to the blend even when it is full of
    # samples: the floor outranks the memory, not the other way round
    assert module.pick_estimate(
        "Run the tests", {"calls2": {"med": 90_000, "n": 9}}, "calls2", 480_000, cm
    ) == (240_000, "blend")
    # no call memory (a fresh log): nothing to blend, and the pace stands alone
    assert module.pick_estimate("Run the tests", {}, "", 480_000, {}) == (None, "pace")
    assert module.task_estimate_ms("Run the tests", 480_000, {}, "", {}) == 480_000
    assert module.pending_blend_ms("Run the tests", 0, cm) is None
    # ...and the blend is a knob, not a hardcoded half: 0 is the pace alone (nothing to
    # blend, so the caller falls through to it), 1 is the wording alone
    assert module.pending_blend_ms("Run the tests", 480_000, cm, 0.0) is None
    assert module.pending_blend_ms("Run the tests", 480_000, cm, 1.0) == 120_000
    assert module.pending_blend_ms("Run the tests", 480_000, cm, 0.25) == int(
        (120_000 ** 0.25) * (480_000 ** 0.75))
    # ...as is the label floor: 0 keeps every span, and `step_spans_ms` is where it bites
    assert module.MIN_LABEL_MS == int(module.LABEL_FLOOR_S * 1000)
    assert module.step_spans_ms(
        {"s": {"started_ms": 0, "done_ms": 2_000, "elapsed_ms": 2_000}},
        [{"task": "s", "completed": True}], 2_000) == []
    assert module.step_spans_ms(
        {"s": {"started_ms": 0, "done_ms": 12_000, "elapsed_ms": 12_000}},
        [{"task": "s", "completed": True}], 12_000) == [12_000]
    say("estimates: a waiting step is priced from its wording, blended with the pace: ok")

    # Both knobs are ASKED FOR rather than held: `--label-floor`/`--blend-weight` are applied
    # while `main` starts, and the readers are spread over the module (soon: over more than one
    # module, where an imported name is a copy the flag could not reach). So one write has to
    # move every reader, which is what this pins.
    assert module.label_floor_ms() == module.MIN_LABEL_MS == int(module.LABEL_FLOOR_S * 1000)
    module.set_estimate_knobs(label_floor=0, blend_weight=1.0)
    try:
        assert module.label_floor_ms() == 0 and module.blend_weight() == 1.0
        assert module.step_spans_ms(
            {"s": {"started_ms": 0, "done_ms": 2_000, "elapsed_ms": 2_000}},
            [{"task": "s", "completed": True}], 2_000) == [2_000], "the floor did not move"
        assert module.pending_blend_ms("Run the tests", 480_000, cm) == 120_000, \
            "the blend did not move"
    finally:
        module.set_estimate_knobs(label_floor=module.LABEL_FLOOR_S,
                                  blend_weight=module.BLEND_WEIGHT)
    assert module.step_spans_ms(
        {"s": {"started_ms": 0, "done_ms": 2_000, "elapsed_ms": 2_000}},
        [{"task": "s", "completed": True}], 2_000) == []
    say("estimates: both knobs are one write and every reader moves with it: ok")

    # ...which the pane's journal reader has to know in the first place: the model comes
    # out of the same backward pass that finds the list, with no json parse per line.
    model_home = os.path.join(TEST_HOME, "model-home")
    os.makedirs(model_home, exist_ok=True)
    model_chat = os.path.join(model_home, "2026-09-26T00-00-00.000Z")
    os.makedirs(model_chat, exist_ok=True)
    def mrec(prompt=None, todos=None, prose=None):
        # the same record shape the journal fixtures above use, with the model field
        # this check is about
        data = {"iteration": 1}
        if prompt is not None:
            data["prompt"] = prompt
        if prose is not None:
            data["fullResponse"] = prose
        if todos is not None:
            data["toolCalls"] = [
                {"toolName": "write_todos", "input": {"todos": todos}}]
        row = {"level": "DEBUG", "timestamp": "2026-09-26T00:00:00.000Z", "data": data}
        row["model"] = fast
        return row

    with open(os.path.join(model_chat, "log.jsonl"), "w") as fh:
        for row in (mrec(prompt="do the thing"),
                    mrec(todos=[{"task": "one", "completed": False}], prose="working")):
            fh.write(json.dumps(row) + "\n")
    got = module.read_cli(model_chat)
    assert [t["task"] for t in got["todos"]] == ["one"], got
    assert got.get("model") == fast, got.get("model")
    say("estimates: the model is read out of the journal with the list: ok")

    # ---- the pane is a fixed height: the goal, the current step and the bar all have
    # to survive a list that is longer than the strip
    tall = dict(
        base_state,
        todos=[{"task": f"step {i}", "completed": i < 4} for i in range(14)],
        done=4,
        total=14,
    )
    steps_shown = lambda text: sum(  # noqa: E731
        1 for line in text.splitlines() if re.match(r"^  \[[ x]\] ", line)
    )
    for rows in (12, 16, 24, 40):
        out = STRIP(module.render(tall, False, width=46, height=rows)).splitlines()
        assert len(out) <= rows, (rows, out)
        assert any(line.startswith("big goal ·") for line in out), (rows, out)
        assert any(line.startswith("  [") and "done" in line for line in out), (rows, out)
    short = STRIP(module.render(tall, False, width=46, height=16))
    assert "[ ] step 4" in short, short  # the step being worked on is never elided
    assert steps_shown(short) < 14, short
    assert "earlier step" in short and "more step" in short, short
    assert "step 0" not in short, short  # finished steps give way, not the current one
    deep = dict(tall, todos=[{"task": f"step {i}", "completed": i < 20} for i in range(40)], done=20, total=40)
    deep_out = STRIP(module.render(deep, False, width=46, height=16))
    assert "[ ] step 20" in deep_out, deep_out
    say("pane height: the goal, the current step and the bar fit, the rest is elided: ok")

    # one verbose step must not become the whole pane (and piped output keeps it whole)
    verbose = dict(base_state, todos=[{"task": "word " * 60, "completed": False}], done=0, total=1)
    pane_out = STRIP(module.render(verbose, False, width=46, height=16)).splitlines()
    full_out = STRIP(module.render(verbose, False, width=46)).splitlines()
    first_task = next(i for i, line in enumerate(pane_out) if line.startswith("  [ ]"))
    block = [
        line for line in pane_out[first_task:] if line.startswith(("  [ ]", "      "))
    ]
    assert len(block) <= 3, pane_out
    assert len(full_out) > len(pane_out), (len(full_out), len(pane_out))
    say("a very long step is capped in the pane, whole in piped output: ok")

    # ---- upgrade mid-flight: a watcher still writing the old layout must not answer a
    # pane that expects the new fields (it silently lost them before)
    args_auto = module.build_parser().parse_args([])
    assert not module.state_matches_request({"backend": "cli", "tool_version": "0.0.0"}, args_auto)
    assert module.state_matches_request(
        {"backend": "cli", "tool_version": module.VERSION}, args_auto
    )
    say("a state file from another build is ignored rather than trusted: ok")

    # ---- what a pane closes on, stated once. An instance dying used to be enough on its
    #      own, which is what "it keeps closing mid-run" was (2026-10-03), and the fix that
    #      came after it — the journal's turn boundary — was closer and still wrong for the
    #      same reason: what a pane draws is a LIST, and a list whose session is over is a
    #      list somebody is reading, finishing or waiting on. So the rule is the window's,
    #      and only these end a pane: `--stale-after` (the reader's OWN window, measured on
    #      the store the pane is following — `0`, the default, never fires), Ctrl-C,
    #      `--once`, and the window going away. Never: an instance exiting, a session or a
    #      turn ending, a journal going quiet on its own, or the store under the pane changing
    #      (`finish_state` dropping a finished list is a frame change, not an ending).
    #
    #      An `auto` pane has no session to close on at all: its subject is re-picked every
    #      poll, so "the session this was following ended" is not an event it can be closed
    #      by — it is a hand-over, to whatever `auto` answers with next. That is why the
    #      rule above is the only one, and why the last two cases below are `-s auto`: the
    #      chat ends (a real turn boundary in the fixture, not just an old mtime), the
    #      instance it was opened beside is killed, and the pane must still be drawing —
    #      now from the app's thread — until the reader's own window says otherwise.
    import pty
    import select

    pane_root = os.path.join(TEST_HOME, "paneclose")

    def pane_chat(name, age_s, ended=False):
        """A CLI chat whose journal is `age_s` old, and whose turn `ended` when asked.

        Two separate facts, and the fixture has to be able to state each on its own. A turn
        BOUNDARY in the tail (`shouldEndTurn`) is what a finished session looks like to
        `journal_liveness` — the agent saying it has stopped and is waiting for you. The
        mtime is a different clock: it is how long the STORE has stopped moving, which is
        the one `--stale-after` reads. A finished session written with a fresh mtime is the
        case the old mtime rule got wrong, so the fixture can now ask for it.
        """
        chat = os.path.join(pane_root, name)
        os.makedirs(chat, exist_ok=True)
        log = os.path.join(chat, "log.jsonl")
        with open(log, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"data": {"toolCalls": [
                {"toolName": "write_todos",
                 "input": {"todos": [{"task": f"{name} step", "completed": False}]}}]}}) + "\n")
            if ended:
                fh.write(json.dumps({"data": {"shouldEndTurn": True}}) + "\n")
        old = time.time() - age_s
        os.utime(log, (old, old))
        return chat

    finished_chat = pane_chat("finished", module.SOURCE_LIVE_MS / 1000.0 + 600, ended=True)
    working_chat = pane_chat("working", 5)
    # A pane answers from the cached watcher state while it is fresh and names a live session,
    # and another block of this suite leaves one behind: removed, so every case below is
    # answered by the fixture it names (a stale file that happened to match would hide the
    # rule). The file lives in the throwaway home.
    if os.path.exists(module.STATE_PATH):
        os.remove(module.STATE_PATH)

    

    def kill_instance_pane(*extra, cwd=None, settle=1.0, wait=2.0):
        """One real pane whose instance dies under it: (exit code, or None while it lives).

        `cwd` is the DIRECTORY `auto` resolves against — `auto` answers for the project the
        pane stands in, not for whatever the fixtures happen to be named, so an `auto` case
        has to stand where its own fixture's project does. `settle` is how long the pane gets
        to find its source and paint before the instance dies, and `wait` how long it is then
        given to close itself: an `auto` pane resolves a chain of two sources per poll, so it
        is given longer than a pane that was told which one to read.
        """
        victim = spawn_quiet("sleep", "600")
        time.sleep(0.2)
        pid, fd = pty.fork()
        if pid == 0:
            os.environ["FBTODO_HOME"] = TEST_HOME
            if cwd:
                os.chdir(cwd)
            os.execv(sys.executable, [sys.executable, PANES, "pane", "--watch-pid",
                                     str(victim.pid), "--no-daemon", "-i", "0.2", *extra])
        time.sleep(settle)   # long enough for the pane to have seen its instance once
        kill_tree(victim)
        # Drain the pane's pty while waiting. The pane paints into this pty and a terminal
        # always reads it; nobody here did, so once the 64 KB buffer filled the pane sat in
        # write() and could not reach the top-of-loop liveness check — measured 2026-09-23:
        # the same pane, pty drained, exits 0.20 s after its instance dies (that is the
        # behaviour under test); undrained, this check spent 11.8 s and looked like a slow
        # pane. The test, not the tool, was the slow part.
        drawn, code = [], None
        deadline = time.time() + wait     # several polls past the instance's death
        while time.time() < deadline:
            while select.select([fd], [], [], 0)[0]:
                try:
                    chunk = os.read(fd, 65536)
                except OSError:          # the child closed its side
                    chunk = b""
                if not chunk:
                    break
                drawn.append(chunk)
            wpid, status = os.waitpid(pid, os.WNOHANG)
            if wpid:
                code = os.waitstatus_to_exitcode(status)
                break
            time.sleep(0.05)
        text = b"".join(drawn).decode("utf-8", "replace")
        if code is None:                 # still on screen — that IS the answer
            os.kill(pid, signal.SIGTERM)
            os.waitpid(pid, 0)
        os.close(fd)
        return code, text

    # The one rule this whole block exists for: an ended session does not end a PANE. The pane
    # is the reader's window on a list; a list whose session is finished is a list somebody is
    # still working through, and closing under them is the "it keeps closing mid-run" report.
    # So the fixture here is a chat whose turn really ended — the boundary is in the tail —
    # watched by a pane whose instance is then killed: the exact shape that used to print
    # `freebuff instance exited` and leave. It must stay, drawing the finished list, until the
    # reader's own window does.
    code, text = kill_instance_pane("-s", "cli", "--chat", finished_chat)
    assert code is None, "a pane closed itself because the session it was showing had ended"
    assert "finished step" in text, text[-600:]
    assert "instance exited" not in text, text[-600:]
    say("pane holds a finished session when its instance dies — only the window ends it: ok")

    code, text = kill_instance_pane("-s", "cli", "--chat", working_chat)
    assert code is None, "a pane must not close while the journal it follows is still moving"
    assert "working step" in text, text[-600:]
    say("pane holds while its journal is still being written, instance or not: ok")

    code, text = kill_instance_pane("-s", "desktop", "--db", desk_db, "--state", desk_state,
                                    "--thread", "T0")
    assert code is None, "a pane following the app's store must not close with a CLI pid"
    assert "its second step" in text, text[-600:]
    assert "instance exited" not in text, text[-600:]
    say("pane holds while it follows the app's thread, instance or not: ok")

    # ---- ...and the two cases that decide the rule: what an `auto` pane closes on, which is
    #      nothing it did not ask for. `auto` re-picks its source every poll, so a session
    #      ending is not an ending at all — it is a hand-over, and the pane's subject after it
    #      is whatever the directory is working on NOW. The fixture is a directory with both
    #      halves: a CLI chat that has ended (a real boundary, an old store) and a desktop
    #      store with one live thread in it. The pane stands in that directory, its instance
    #      is killed, and it must go on drawing — from the thread, because that is the list
    #      this directory is working on now.
    auto_dir = os.path.join(TEST_HOME, "paneclose-auto-proj")
    os.makedirs(auto_dir, exist_ok=True)
    auto_root = os.path.join(TEST_HOME, "paneclose-auto-root")
    auto_chat = os.path.join(auto_root, os.path.basename(auto_dir), "chats",
                             "2026-02-02T00-00-00.000Z")
    os.makedirs(auto_chat, exist_ok=True)
    ended_step = "the ended CLI session's step"
    with open(os.path.join(auto_chat, "log.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"data": {"toolCalls": [
            {"toolName": "write_todos",
             "input": {"todos": [{"task": ended_step, "completed": False}]}}]}}) + "\n")
        fh.write(json.dumps({"data": {"shouldEndTurn": True}}) + "\n")
    ended_at = time.time() - (module.SOURCE_LIVE_MS / 1000.0) - 1800
    os.utime(os.path.join(auto_chat, "log.jsonl"), (ended_at, ended_at))
    auto_store = os.path.join(TEST_HOME, "paneclose-auto-stores", "proj")
    os.makedirs(auto_store, exist_ok=True)
    with open(os.path.join(auto_store, "project.json"), "w", encoding="utf-8") as fh:
        json.dump({"projectPath": auto_dir}, fh)
    auto_db = os.path.join(auto_store, "desktop-v2.db")
    if os.path.exists(auto_db):
        os.remove(auto_db)
    auto_step = "the live thread this directory is working on"
    con = sqlite3.connect(auto_db)
    con.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, status TEXT,"
                " sidebar_archived_at INTEGER)")
    con.execute("CREATE TABLE messages (seq INTEGER, thread_id TEXT, parts_json TEXT, ts INTEGER)")
    con.execute("INSERT INTO threads VALUES ('AU', 'Auto thread', 'open', NULL)")
    con.execute("INSERT INTO messages VALUES (0, 'AU', ?, ?)",
                (desk_parts(desk_todos((auto_step, False))), DESK_NOW))
    con.commit()
    con.close()
    auto_pat = os.path.join(TEST_HOME, "paneclose-auto-stores", "*", "desktop-v2.db")
    auto_state = os.path.join(TEST_HOME, "paneclose-auto-state.json")

    code, text = kill_instance_pane(
        "-s", "auto", "--cli-root", auto_root, "--db", auto_pat, "--state", auto_state,
        cwd=auto_dir, settle=2.0, wait=6.0,
    )
    assert code is None, f"an auto pane closed itself when a session ended: {code}"
    assert auto_step in text, text[-800:]
    assert ended_step not in text, text[-800:]
    assert "instance exited" not in text, text[-800:]
    say("an auto pane follows the session on: an ended chat is a hand-over, not an ending: ok")

    # ...and the other half, which is what makes the first half a RULE rather than a shrug:
    # asked for, the one window left does close it. `--stale-after` is measured on the store
    # the pane is following, so an auto pane whose sources have all stopped moving goes at
    # the end of that window — the same exit and the same words as any other source, which is
    # the whole of what an auto pane can be closed by.
    pid_w, fd_w = pty.fork()
    if pid_w == 0:
        os.environ["FBTODO_HOME"] = TEST_HOME
        os.chdir(auto_dir)
        os.execv(sys.executable, [sys.executable, PANES, "pane", "--no-daemon", "-i", "0.2",
                                 "-s", "auto", "--cli-root", auto_root, "--db", auto_pat,
                                 "--state", auto_state, "--stale-after", "0.05"])
    out_w, deadline_w = b"", time.time() + stale_window_bound()
    os.set_blocking(fd_w, False)
    status_w = None
    while time.time() < deadline_w:
        try:
            out_w += os.read(fd_w, 65536)
        except (BlockingIOError, OSError):
            pass
        wpid, st_w = os.waitpid(pid_w, os.WNOHANG)
        if wpid:
            status_w = st_w
            break
        time.sleep(0.1)
    assert status_w is not None, "an auto pane ignored the window its reader asked for"
    text_w = STRIP(out_w.decode("utf-8", "replace"))
    assert "idle" in text_w and "closing" in text_w, text_w[-800:]
    assert os.waitstatus_to_exitcode(status_w) == 0, os.waitstatus_to_exitcode(status_w)
    say("an auto pane closes on the window, and on nothing else: ok")

    # ---- rendering: narrow panes must not wrap mid-word, and the footer clock
    # must move so a still pane still proves it is alive
    ansi = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
    # The shape (the keys, and every field the pane receives) comes from the live `json`,
    # so a check cannot drift from what the renderer is actually handed. What VARIES is
    # pinned here: a check that read the model or the remembered history live passed or
    # failed with whatever session this machine happened to be running, and the footer's
    # `pace · model` field is decided by exactly those two. A machine with no session at
    # all is fine too — `json` always answers with a full state (`todos: []`).
    base_state = dict(
        json.loads(run("json").stdout),
        model="some-provider/space-bunny-alpha-preview",
        # `error` is machine state, not shape: a runner with no session of its own gets
        # `no conversation DB found` here, and `render` draws that INSTEAD of the list —
        # so every check below was asserting against a two-line error frame. Cleared, the
        # shape still comes from the live `json` and the list is really rendered.
        error=None,
        # ...and `turn_ended` is machine state too, with a worse symptom: the checks below
        # read the state chip's WORD from it and assert a WORKING chip, so a fixture that
        # inherited it passed or failed with the moment the suite happened to run — FALSE
        # mid-turn, TRUE in the seconds after one. The list drawn here is mid-turn by
        # construction (a step is unfinished), so the fixture says so, and a machine with
        # no live session to inherit from cannot change the answer.
        turn_ended=False,
        task_history={},        # no remembered pace: the default keeps `~2m` in the strip
        # `now`, `nudge` and the patch/alert pair ride along in the live `json`, and each one
        # can add a ROW to the frame. Measured 2026-09-29: a request arriving mid-run put a
        # `NOW` row in, took the 12-line frame to 13 and failed the height assertion below —
        # with nothing wrong with the renderer. The shape still comes from the live `json`;
        # the height budget is tested on a head with no live text in it.
        now=None,
        nudge=None,
        patch=None,
        alert=None,
        # ...and the SOURCE the live `json` answered from is machine state of the same kind.
        # With `auto` following the live store (see `_snapshot`), a runner in a directory whose
        # CLI chat has finished inherits the DESKTOP answer — a long thread id on the title and
        # `threads` stacking heading rows, which is a different frame from the single CLI list
        # these checks are written for. Measured 2026-10-03: the live desktop answer took the
        # 12-line `short_patch` frame to the point where its PATCH row was dropped. The shape
        # still comes from the live `json`; the source is pinned so the frame under test does
        # not move with whichever session this machine happens to be running.
        backend="cli",
        session="S",
        source="cli-journal",
        threads=None,
        goal_stale=None,
        # ...and `source_why` is source state of exactly that kind: the chain's own note about
        # which source it passed over, which the live `json` carries whenever THIS machine's
        # directory had a finished chat to walk past. It is drawn on the title, so inheriting
        # it would move every frame below with the moment the suite ran; cleared, the shape is
        # the single-source frame these checks are written for, and the note is checked on its
        # own (see "the pane's title names the source that answered").
        source_why=None,
    )
    steps = [
        {"task": "Diagnose the frozen-looking pane and prove it is alive", "completed": True},
        {"task": "Add a liveness tick so the pane repaints on a clock", "completed": False},
    ]
    # Durations too: the suffix rides on the last line of a step, so a 22-column strip
    # is where an untouched task line used to run past the edge. One frozen clock, one
    # counting up through the seconds, at "59m59s" — the widest a duration gets below
    # the hour, which is where the seconds are shown.
    SWEEP_NOW = 3_599_999
    clocks = {
        "Diagnose the frozen-looking pane and prove it is alive": {
            "started_ms": 1, "done_ms": 90_000, "elapsed_ms": 90_000,
        },
        "Add a liveness tick so the pane repaints on a clock": {
            "started_ms": 1, "done_ms": None, "elapsed_ms": 3_599_998,
        },
    }
    for heading, extra in (
        ("a heading", {"goal": "diagnose the frozen pane", "now": None, "nudge": None}),
        ("no heading", {"goal": None, "now": None, "nudge": None}),
        ("a pending nudge", {"goal": None, "now": None, "nudge": "continue"}),
    ):
        wide_state = dict(
            base_state, todos=steps, done=1, total=2, list_version=3,
            task_times=clocks, **extra
        )
        for w in (22, 34, 80, 120):
            rendered = module.render(wide_state, False, watching=999, width=w, now_ms=SWEEP_NOW)
            for line in ansi.sub("", rendered).splitlines():
                assert len(line) <= w, (heading, w, repr(line))
    wide_state = dict(
        base_state, todos=steps, done=1, total=2, list_version=3,
        goal="diagnose the frozen pane",
    )
    frame_a = module.render(wide_state, False, width=80, now_ms=1000)
    frame_b = module.render(wide_state, False, width=80, now_ms=2000)
    assert frame_a != frame_b, (
        "footer clock does not move — a still pane would look frozen\n"
        f"A={frame_a!r}\nB={frame_b!r}"
    )

    # The same pair with a clock only 42 s old: an ordinary running step, which is the case
    # the list's age has to survive.
    SHORT_CLOCKS = dict(
        clocks,
        **{
            "Add a liveness tick so the pane repaints on a clock": {
                "started_ms": SWEEP_NOW - 42_000, "done_ms": None, "elapsed_ms": 42_000,
            }
        },
    )
    # the colour pane is framed and high-density: boxes line up, the active step is
    # the only loud one, and every visible row still fits the pane it was drawn in
    rich_state = dict(wide_state, task_times=clocks)
    rich = ansi.sub("", module.render(
        rich_state, True, watching=999, width=46, height=20, now_ms=SWEEP_NOW,
    ))
    assert "╭" in rich and "╯" in rich, rich
    # rounded corners everywhere and no square ones anywhere: the frame is drawn with
    # `╭ ╮ ╰ ╯` only, which the corner assertions above and this one pin
    assert not any(ch in rich for ch in "┌┐└┘"), rich
    assert "FREEBUFF TODOS" in rich and "watcher: pid 999" in rich, rich
    # the marker is `▸` and not an emoji: a frame is a grid, and a glyph the code
    # and the terminal measure differently is enough to step its border sideways
    assert "▸ Goal:" in rich and "✔" in rich and "➔" in rich, rich
    assert BAR_CELLS.search(rich) and "LIVE:" in rich, rich
    assert "PROGRESS" not in rich, "the bar row still carries its old label"
    assert len(rich.splitlines()) <= 20, rich
    for line in rich.splitlines():
        assert module._cell_width(line) <= 46, (line, module._cell_width(line))
    # The patch row: what the last patch pass did, and when the phone was last told
    # something — the two facts the pane used to need an ssh to see. Built into the state
    # here (rather than taken from a `json` child, whose facts depend on WHOSE state file
    # is fresh) so the renderer is checked on its own.
    facts = {
        "patch": {"outcome": "incomplete", "severity": "bad", "version": "9.9.9-fixture",
                  "at_ms": int(time.time() * 1000) - 60_000, "reason": "no window for x"},
        "alert": {"kind": "sent", "severity": "ok", "text": "sent ntfy incomplete",
                  "at_ms": int(time.time() * 1000) - 60_000},
    }
    with_patch = dict(rich_state, **facts)
    rich_patch = ansi.sub("", module.render(
        with_patch, True, watching=999, width=46, height=20, now_ms=SWEEP_NOW,
    ))
    assert "PATCH" in rich_patch and "ALERT" in rich_patch, rich_patch
    for line in rich_patch.splitlines():
        assert module._cell_width(line) <= 46, (line, module._cell_width(line))
    plain_rows = ansi.sub("", module.render(with_patch, False, width=80, now_ms=SWEEP_NOW))
    assert "PATCH" in plain_rows and "ALERT" in plain_rows, plain_rows
    # A state from a build that read neither fact (or a fixture written for one) renders
    # exactly as it always did: the row is drawn from the state, not from the host.
    no_facts = {k: v for k, v in rich_state.items() if k not in ("patch", "alert")}
    assert "PATCH" not in ansi.sub("", module.render(
        no_facts, False, width=80, now_ms=SWEEP_NOW))
    short_rich = ansi.sub("", module.render(
        rich_state, True, watching=999, width=46, height=12, now_ms=SWEEP_NOW,
    ))
    assert len(short_rich.splitlines()) <= 12, short_rich
    assert "Add a liveness tick" in short_rich, short_rich
    # ...and the row costs ONE line of a fixed-height pane, whatever the width: the ladder
    # trades detail (the alert's words, then the patch's version) rather than another row
    short_patch = ansi.sub("", module.render(
        with_patch, True, watching=999, width=46, height=12, now_ms=SWEEP_NOW,
    ))
    assert len(short_patch.splitlines()) <= 12, short_patch
    assert sum(1 for ln in short_patch.splitlines() if "PATCH" in ln) == 1, short_patch
    assert "Add a liveness tick" in short_patch, short_patch

    # ---- the patch row's two readers, on fixtures
    rep = module.local_patch_alert()
    assert rep["patch"]["outcome"] == "ok", rep
    assert rep["patch"]["version"] == "9.9.9-fixture", rep  # the fixture, not this Mac
    assert rep["patch"]["reason"].endswith("in place"), rep
    assert rep["patch"]["severity"] == "ok", rep
    assert rep["alert"]["kind"] == "sent" and "fixture" in rep["alert"]["text"], rep
    say("patch row: the last patch outcome and the last alert are read from their logs: ok")
    # a failure is the newest OUTCOME, and the `reanchor:` note above it is not mistaken
    # for one — reading the newest line would report a failure as a success
    with open(PATCH_FIXTURE, "a") as fh:
        fh.write(f"{PATCH_FIXTURE_STAMP} reanchor: freebuff: local CLI patches incomplete"
                 " (no window for x)\n")
        fh.write(f"{PATCH_FIXTURE_STAMP} FAILED — the patches are still not in the"
                 " binary; will retry\n")
    rep = module.local_patch_alert()
    assert rep["patch"]["outcome"] == "failed" and rep["patch"]["severity"] == "bad", rep
    assert "will retry" in rep["patch"]["reason"], rep
    with open(PATCH_FIXTURE, "w") as fh:  # restored for everything below
        fh.write(f"{PATCH_FIXTURE_STAMP} patched — the collapse and the session-end reason"
                 " are in place (1:2:4)\n")
    say("patch row: a failure is reported, and a `reanchor:` note is not read as one: ok")
    # the notifier's own line, whose ` — http 200 {…}` transport reply is not the row's
    # business
    alert = module.alert_from_lines([
        '2026-01-01 00:00:01 sent ntfy incomplete (2f14bfcf14a4d794) — http 200 {"id":"x"}',
    ], "fixture")
    assert alert["kind"] == "sent" and alert["text"] == "sent ntfy incomplete", alert
    alert = module.alert_from_lines([
        "2026-01-01 00:00:02 resolved — the patches are in place again",
    ], "fixture")
    assert alert["kind"] == "resolved" and alert["severity"] == "ok", alert
    alert = module.alert_from_lines([
        "2026-01-01 00:00:03 muted — failed (7be3655000448c52) not sent",
    ], "fixture")
    assert alert["kind"] == "muted" and alert["severity"] == "warn", alert
    assert module.alert_from_lines([], "fixture") is None
    # Two clocks can write a stamp, so the SEPARATOR decides: a local one after a
    # space, a UTC one after a `T`. Guessing made a 28-minute-old alert read as `0s
    # ago`: a UTC stamp was a future time on a PDT clock, and the age clamped to zero.
    local_stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 1800))
    utc_stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 1800))
    for stamp, label in ((local_stamp, "local"), (utc_stamp, "utc")):
        age = (module.entry_ms(f"{stamp} sent ntfy x") or 0) / 1000.0
        assert 1740 < time.time() - age < 1890, (label, stamp)
    alert = module.alert_from_lines([f"{local_stamp} sent ntfy incomplete"], "fixture")
    assert alert and 1740 < alert["age_s"] < 1890, alert
    say("patch row: a UTC stamp is read as UTC and a local one as local: ok")
    # the row, laddered by width: both facts stay on it, the detail goes
    dirty = {
        "patch": {"outcome": "incomplete", "severity": "bad", "version": "9.9.9",
                  "at_ms": int(time.time() * 1000) - 60_000, "reason": "no window for x"},
        "alert": {"kind": "sent", "severity": "ok", "text": "sent ntfy incomplete",
                  "at_ms": int(time.time() * 1000) - 60_000},
    }
    for w in (22, 30, 46, 60, 120):
        row = module.patch_row(dirty, w)
        assert row and module._cell_width("  " + module.patch_row_text(row)) <= w, (w, row)
    assert [p[0] for p in module.patch_row(dirty, 46)] == ["PATCH", "ALERT"], module.patch_row(dirty, 46)
    assert len(module.patch_row(dirty, 22)) == 1, module.patch_row(dirty, 22)
    assert module.patch_row({}, 60) == [] and module.patch_row({}, None) == []
    say("patch row: both facts ride one line, traded down by width instead of by rows: ok")
    status_fix = STRIP(run("status").stdout)
    assert "cli patches       : ok · 9.9.9-fixture" in status_fix, status_fix
    assert "last alert        : sent ntfy freebuff done · fixture" in status_fix, status_fix
    say("status: the same two facts, in the pane's own words: ok")
    # a pane too narrow for a frame keeps the plain renderer rather than a broken box
    assert ansi.sub("", module.render(
        rich_state, True, width=22, now_ms=SWEEP_NOW
    )) == ansi.sub("", module.render(rich_state, False, width=22, now_ms=SWEEP_NOW))
    # without a watcher the top border still has to name the session it is showing —
    # the right slot carries the short session title, never the raw ISO stamp
    no_watch = ansi.sub("", module.render(
        rich_state, True, width=46, height=20, now_ms=SWEEP_NOW,
    ))
    top = no_watch.splitlines()[0]
    assert "watcher" not in top, top
    assert module._session_label(rich_state) in top, (top, module._session_label(rich_state))
    assert top.startswith("╭──  FREEBUFF TODOS  ") and top.endswith("╮"), top
    assert module._cell_width(top) <= 46, top
    for w in (30, 34, 46, 80):
        line = ansi.sub("", module.render(
            rich_state, True, width=w, height=20, now_ms=SWEEP_NOW,
        )).splitlines()[0]
        assert module._cell_width(line) <= w, (w, line)
    # the bar is a gradient where the terminal takes 24-bit colour and a flat green
    # one where it does not; the glyphs — and so the frame — stay identical
    os.environ["FBTODO_TRUECOLOR"] = "1"
    tc = module.render(rich_state, True, watching=999, width=46, height=20, now_ms=SWEEP_NOW)
    assert "\x1b[38;2;" in tc, "truecolor bar missing its gradient"
    os.environ["FBTODO_TRUECOLOR"] = "0"
    flat = module.render(rich_state, True, watching=999, width=46, height=20, now_ms=SWEEP_NOW)
    assert "\x1b[38;2;" not in flat, flat
    # ...and the 256-colour fallback is the same bar: the ramp still runs stop to stop
    # instead of collapsing into one flat accent cell colour
    flat_stops = [int(n) for n in re.findall(r"\x1b\[38;5;(\d+)m█", flat)]
    assert flat_stops and len(set(flat_stops)) > 1, repr(flat)
    del os.environ["FBTODO_TRUECOLOR"]
    assert ansi.sub("", tc) == ansi.sub("", flat), "the gradient changed the bar's shape"
    # a third done is not a whole number of cells: the bar must show a partial block.
    # The list itself has to be a third — the bar counts the steps it is drawing, so a
    # `done=1, total=3` beside a four-step list is no longer a thing it can be told.
    third = dict(
        rich_state,
        todos=[
            dict(steps[0], completed=True),
            dict(steps[1], completed=False),
            {"task": "A third step, so a third of the bar is not a whole cell",
             "completed": False},
        ],
        done=1, total=3,
    )
    bar_row = [bar_row_of(ansi.sub("", module.render(
        third, True, watching=999, width=46, height=20, now_ms=SWEEP_NOW,
    )))]
    # the bar is two glyphs at EVERY percentage — a cell that would be a fraction rounds to
    # the nearer of them, rather than drawing a third glyph in the middle of the ramp. A
    # third of a 20-cell bar is 6.67 cells, so seven of them are filled.
    bar_glyphs = bar_row[0].split("[", 1)[1].split("]", 1)[0]
    # a third of a 20-cell bar is 6.67 cells: six whole ones, the nearest eighth at the
    # boundary, and the track for the rest. Two glyphs everywhere else.
    assert bar_glyphs == "█" * 6 + "▋" + "░" * 13, bar_row
    # character-based rendering that survives any background scheme: every filled cell is
    # the `█` GLYPH painted with a foreground colour, and nothing on the row is ever a
    # background-filled block (40-47, 48, 100-107)
    raw_bar = bar_row_of(module.render(
        third, True, watching=999, width=46, height=20, now_ms=SWEEP_NOW,
    ))
    filled = re.findall(r"(\x1b\[[0-9;]*m)(?:█|▋)", raw_bar)
    assert len(filled) == 7, raw_bar  # the assertions below are about THESE cells
    assert all(esc.startswith("\x1b[38;") for esc in filled), raw_bar
    # ...and nothing on the row selects a BACKGROUND. A single-parameter escape is the only
    # place SGR can do that without its number being an operand of `38;2`/`38;5`: a bare
    # index is a background, so `\x1b[45m█` painted a magenta block instead of a character.
    assert not re.search(r"\x1b\[(4[0-7]|10[0-7])m", raw_bar), raw_bar
    assert "\x1b[48" not in raw_bar, raw_bar
    # ---- ...and the frame never falls off the TOP of the pane it is drawn in: the top
    #      border carries the title and the session's identity, and a row that scrolls away
    #      takes them with it — a 9-line pane used to show a list with nothing over it,
    #      because the two elision summaries had spent the height the steps were given.
    #      Swept from 9 — the height of the pane this was found on — up to a tall one, for a
    #      long list and a short one. Below 9 the step area cannot hold even one wrapped step
    #      plus its summary, and no pane that short is opened by anything: the wrapper wants a
    #      20-line window and the default pane is 12.
    for h in range(9, 25):
        for state_dict in (rich_state, dict(rich_state, todos=steps, task_times={})):
            framed = module.render(state_dict, True, watching=999, width=66, height=h,
                                   now_ms=SWEEP_NOW)
            got = STRIP(framed).splitlines()
            assert len(got) <= h, (h, framed)
            assert got[0].startswith("╭──  FREEBUFF TODOS") and got[-1].startswith("╰"), (
                h, framed)
    say("fits the pane it is given, at every height, without losing its own title: ok")

    say("renders the framed colour pane inside its width and height: ok")

    # ---- the pane's own layout rules: a tight checkmark whose wrapped lines hang under
    #      its own text column, the step's duration against the right wall, no heading row
    #      when the agent never wrote one, and a footer that says one state, not three
    lines = rich.splitlines()
    assert "  ✔  Diagnose the frozen" in rich, rich
    assert "✔     " not in rich, "the checkmark is still padded out to the box width"
    for head_word in ("Diagnose the frozen", "Add a liveness"):
        i = next(n for n, line in enumerate(lines) if head_word in line)
        first, cont = lines[i][1:], lines[i + 1][1:]  # drop the frame's left border
        assert first.index(head_word.split()[0]) == len(cont) - len(cont.lstrip()), (
            lines[i], lines[i + 1],
        )
    done_row = next(line for line in lines if "Diagnose the frozen" in line)
    # the duration is the last thing on the row: against the frame's right wall
    assert re.search(r"[0-9]+m[0-9]{2}s │$", done_row), repr(done_row)
    # the empty track is drawn, so the bar's length reads at any percentage
    nothing_done = dict(
        rich_state, done=0,
        todos=[dict(t, completed=False) for t in rich_state["todos"]],
    )
    zero = ansi.sub("", module.render(
        nothing_done, True, width=66, height=20, now_ms=SWEEP_NOW,
    ))
    zero_bar = bar_row_of(zero)
    assert set(zero_bar.split("[", 1)[1].split("]", 1)[0]) == {"░"}, zero_bar
    # a list with no heading is NAMED, not hidden: the framed pane draws the same warning the
    # plain renderer does, in the warn yellow, on the row the heading would have taken — the
    # skipped rule is surfaced loudly instead of drawing nothing
    bare_col = module.render(
        dict(rich_state, goal=None), True, watching=999, width=46, height=20,
        now_ms=SWEEP_NOW,
    )
    bare = ansi.sub("", bare_col)
    assert "▸" not in bare, bare                          # no invented heading
    assert module.GOAL_MISSING_NOTE in bare, bare        # ...but the gap is named
    assert "none stated" not in bare, bare
    note_row = next(ln for ln in bare_col.splitlines() if "no heading" in ln)
    assert "\x1b[33m" in note_row, repr(note_row)         # and in the warn yellow
    # one state word, with the age in the parentheses the idle counter used to spend —
    # and the three states a list can be in, at the width the real pane gets
    def footer_of(state_dict, width=66):
        frame = ansi.sub("", module.render(
            state_dict, True, watching=999, width=width, height=20, now_ms=SWEEP_NOW,
        ))
        return next(line for line in frame.splitlines() if "LIVE:" in line), frame

    # The strip is a badge and then fields, ` │ ` apart. The LIST's OWN AGE rides on the
    # LIST field (`LIST: #3 · 2m ago`) rather than after the chip, where `Working 42s
    # (2m ago)` read as the STATE's age and had to be hidden whenever a step was running —
    # which hid it on exactly the panes that need it. On the LIST field it cannot be
    # misread, so it is drawn whatever the state is doing; the pace is what a narrow strip
    # spends first.
    idle_row, _ = footer_of(
        dict(rich_state, task_times={}, source_updated_ms=SWEEP_NOW - 143_000)
    )
    assert " IDLE  │ LIST: #3 · 2m ago │ LIVE: " in idle_row, repr(idle_row)
    no_model_row, _ = footer_of(
        dict(rich_state, model=None, task_times={}, source_updated_ms=SWEEP_NOW - 143_000)
    )
    assert " IDLE  │ LIST: #3 · 2m ago │ LIVE: " in no_model_row, repr(no_model_row)
    assert "space-bunny" not in no_model_row, repr(no_model_row)
    # A wider strip keeps its age rather than trading it for a pace, and a longer model name
    # is clipped, never wrapped: the row is one line by contract.
    long_row, _ = footer_of(
        dict(rich_state, model="some-provider/space-bunny-alpha-preview",
             task_times={}, source_updated_ms=SWEEP_NOW - 143_000),
        width=70,
    )
    # The strip is the state, the list's age and the clock — three fields, and the pace is
    # not one of them any more: `~2m · model` was the same number every step row already
    # carries, on the row where it is a prediction about THAT step, and it was spending the
    # age's room on a 70-column pane. The model stays on the top border, where a long name
    # is clipped rather than wrapped: the border is one line by contract.
    assert "~2m" not in long_row and "IDLE  │ LIST: #3 · 2m ago │ LIVE: " in long_row, \
        repr(long_row)
    assert "preview" not in long_row, repr(long_row)
    # ...and the model is named on the TOP BORDER too, where there is room for it at any
    # width the strip cannot hold: the pane that quotes a pace says whose pace it is.
    _, bordered = footer_of(dict(rich_state, model="stealth/space-bunny-alpha"))
    assert "watcher: pid 999 · space-bunny-a…" in bordered.splitlines()[0], bordered
    # A step that is RUNNING does not hide the list's age: that is the case this row exists
    # for, because a list an agent has stopped re-writing looked current for exactly as long
    # as a clock was on it.
    tick_row, _ = footer_of(dict(rich_state, source_updated_ms=SWEEP_NOW - 143_000,
                                 task_times=SHORT_CLOCKS))
    assert " WORKING  │ LIST: #3 · 2m ago │ LIVE: " in tick_row, repr(tick_row)
    run_row, _ = footer_of(dict(rich_state, source_updated_ms=SWEEP_NOW - 143_000))
    # The chip is the STATE and nothing else: the running clock that used to be repeated
    # here — `WORKING 11m07s` beside `11m07s [~3m]` on the step's own row — is the row's,
    # where the estimate it is being compared against is. The overrun is not restated here
    # either; the chip turning red is all the footer has to say about it.
    assert " WORKING  │ LIST: #3 · 2m ago │ LIVE: " in run_row, repr(run_row)
    assert "59m59s" not in run_row and "(+58m29s)" not in run_row, repr(run_row)
    raw_run = module.render(rich_state, True, watching=999, width=66, height=20,
                            now_ms=SWEEP_NOW)
    assert "\x1b[7;31m" in raw_run, repr(raw_run)
    assert module.SPINNER[SWEEP_NOW // 1000 % len(module.SPINNER)] in run_row, run_row
    turned, _ = footer_of(dict(rich_state, now_ms=SWEEP_NOW))
    later = ansi.sub("", module.render(
        rich_state, True, watching=999, width=66, height=20, now_ms=SWEEP_NOW + 1000,
    ))
    spin_now = next(line for line in turned.splitlines() if "LIVE:" in line)
    spin_next = next(line for line in later.splitlines() if "LIVE:" in line)
    assert spin_now != spin_next, (spin_now, spin_next)  # the spinner moves on the tick
    ended_row, ended = footer_of(dict(
        rich_state, done=2, total=2, list_version=9, source_updated_ms=SWEEP_NOW - 143_000,
        store_mtime_ms=SWEEP_NOW - 143_000,
        todos=[dict(t, completed=True) for t in rich_state["todos"]],
    ))
    # `ALL DONE` is a wider chip than `IDLE`, and the age is held: at 66 columns the pace
    # is what gives way here, which the long_model row above pins at a width that holds it.
    assert " ALL DONE  │ LIST: #9 · 2m ago │ LIVE: " in ended_row, repr(ended_row)
    # one state, not two: the chip says it once, and never sighs both "done" and "idle"
    assert ended.count("ALL DONE") == 1 and "IDLE" not in ended, ended
    # A finished list inside a session that has written well past it: the ONE staleness
    # fbtodo can actually prove, and even that is a question mark — the agent may simply be
    # tidying up. The marker rides with the age, so a narrow strip spends them together.
    finished = [dict(t, completed=True) for t in rich_state["todos"]]
    stale_row, _ = footer_of(dict(
        rich_state, done=2, total=2, list_version=9, todos=finished,
        ts=SWEEP_NOW - 900_000, source_updated_ms=SWEEP_NOW - 900_000,
        store_mtime_ms=SWEEP_NOW - 60_000,
    ))
    assert " ALL DONE  │ LIST: #9 · 15m ago [STALE?] │ LIVE: " in stale_row, repr(stale_row)
    # ...waiting for you is not "behind": the turn ended, so nothing is being worked on past
    # the list, and that case belongs to the bell and the stall watch.
    waiting_row, _ = footer_of(dict(
        rich_state, done=2, total=2, list_version=9, todos=finished, turn_ended=True,
        ts=SWEEP_NOW - 900_000, source_updated_ms=SWEEP_NOW - 900_000,
        store_mtime_ms=SWEEP_NOW - 60_000,
    ))
    assert "[STALE?]" not in waiting_row, repr(waiting_row)
    # ...nor is a list with a step still to do, however long the session has written: a step
    # takes as long as it takes, and its own clock (with `[STUCK?]` past twice its estimate)
    # is the record there. This is the boundary the marker exists on the right side of.
    busy_row, _ = footer_of(dict(
        rich_state, ts=SWEEP_NOW - 900_000, source_updated_ms=SWEEP_NOW - 900_000,
        store_mtime_ms=SWEEP_NOW - 60_000,
    ))
    assert "[STALE?]" not in busy_row, repr(busy_row)
    # the chrome is one voice: the state chip and the title are both uppercase
    assert " FREEBUFF TODOS " in ended and " All done" not in ended, ended
    # an elided window must word its marker from the steps it stands for: a finished list
    # read `7 more steps pending` at 100% (8/8), right above a bar saying All done
    deep = dict(
        rich_state, list_version=9, task_times={},
        todos=[{"task": f"step {n}", "completed": n < 8} for n in range(8)],
        done=8, total=8, source_updated_ms=SWEEP_NOW - 143_000,
    )
    ended_frame = ansi.sub("", module.render(
        deep, True, watching=999, width=46, height=9, now_ms=SWEEP_NOW,
    ))
    assert "100% (8/8)" in ended_frame and " ALL DONE" in ended_frame, ended_frame
    assert "earlier steps completed" in ended_frame, ended_frame
    assert "pending" not in ended_frame, ended_frame
    # ...and the collapsed run sits ABOVE the steps it stands for, in list order, so the
    # window keeps the list's END: the marker used to land under the step it followed
    ended_lines = ended_frame.splitlines()
    marked = next(n for n, line in enumerate(ended_lines) if "earlier steps completed" in line)
    shown = next(n for n, line in enumerate(ended_lines) if "✔" in line)
    assert marked < shown, ended_frame
    assert "step 7" in ended_frame and "step 0" not in ended_frame, ended_frame
    # ...and the bar counts the list it is drawing, not a stale tally beside it: `done` and
    # `total` here are the OLD list's numbers, and must not paint 100% over four live steps
    middling = dict(
        deep, done=4, total=4,
        todos=[{"task": f"step {n}", "completed": n < 4} for n in range(8)],
    )
    mid_frame = ansi.sub("", module.render(
        middling, True, watching=999, width=70, height=12, now_ms=SWEEP_NOW,
    ))
    assert len(mid_frame.splitlines()) == 12, mid_frame
    assert "50% (4/8)" in mid_frame, mid_frame
    # The wording is the marker's own rule, checked where it is decided: a run of steps that
    # are all DONE says `completed` whatever side of the window it sits on, and a run with
    # anything still to do says `pending` (the bug this replaced: a finished list read `7
    # more steps pending` at 100%, right above a bar saying All done).
    assert module._elision_note(7, [True] * 7, "earlier") == "  │ 7 earlier steps completed"
    assert module._elision_note(3, [False] * 3, "more") == "  │ 3 more steps pending"
    assert module._elision_note(1, [True], "more") == "  │ 1 earlier step completed"
    assert module._elision_note(1, [False], "earlier") == "  │ 1 earlier step pending"
    # ...and on the frame while the pane has the rows for them. Two markers and two steps
    # here, and the frame is exactly its pane's height — it used to be three rows over and
    # lost its own title off the top.
    assert "4 earlier steps completed" in mid_frame, mid_frame
    assert "2 more steps pending" in mid_frame, mid_frame
    squat_elide = ansi.sub("", module.render(
        middling, True, watching=999, width=46, height=9, now_ms=SWEEP_NOW,
    ))
    assert len(squat_elide.splitlines()) == 9, squat_elide
    assert "50% (4/8)" in squat_elide, squat_elide
    assert sum("steps" in ln for ln in squat_elide.splitlines()) == 1, squat_elide
    assert "4 earlier steps completed" in squat_elide, squat_elide
    # the bar's total width is defined at a partial percentage: half filled, half track
    mid_bar = bar_row_of(mid_frame)
    glyphs = mid_bar.split("[", 1)[1].split("]", 1)[0]
    assert glyphs.count("█") == 10 and glyphs.count("░") == 10, mid_bar
    # the ramp spans the FILLED run, so a quarter-done bar still ends in the gradient's
    # emerald stop: sampled across the whole bar the fill only reached green at 100%, and
    # a partial bar read as one flat cyan-teal block
    os.environ["FBTODO_TRUECOLOR"] = "1"
    try:
        quarter = dict(
            rich_state, task_times={},
            todos=[{"task": f"step {n}", "completed": n < 1} for n in range(4)],
        )
        q_row = bar_row_of(module.render(
            quarter, True, watching=999, width=46, now_ms=SWEEP_NOW,
        ))
    finally:
        os.environ.pop("FBTODO_TRUECOLOR", None)
        set_knob(module, "_THEME_CACHE", None)
    q_stops = [tuple(int(v) for v in s) for s in re.findall(
        r"\x1b\[38;2;(\d+);(\d+);(\d+)m█", q_row
    )]
    assert len(q_stops) == 5, q_row  # a quarter of a 20-cell bar
    # The ramp is the PALETTE's, and the assertion is against the palette rather than
    # against two magic numbers: the fill starts at the accent's cyan and ends near the
    # success green — the two hues the pane is built from — and never goes back the other
    # way along the way.
    start_rgb = module._hex_rgb(module.THEME_DEFAULTS["gradient_start"])
    end_rgb = module._hex_rgb(module.THEME_DEFAULTS["gradient_end"])
    assert abs(q_stops[0][2] - start_rgb[2]) < 40 and q_stops[0][2] > end_rgb[2], q_stops
    assert abs(q_stops[-1][2] - end_rgb[2]) < 40, q_stops
    assert all(a[2] >= b[2] for a, b in zip(q_stops, q_stops[1:])), q_stops
    # one symbol per row, no box glued to it, and the three of them put the text in the
    # same column — a wrapped line then hangs under the words, not under the marker
    three_kinds = ansi.sub("", module.render(
        middling, True, watching=999, width=46, now_ms=SWEEP_NOW,
    ))
    assert "[ ]" not in three_kinds, three_kinds
    for mark in ("✔", "➔", "○"):
        row = next(line for line in three_kinds.splitlines() if mark in line)
        assert row[1:4] == "   " and row[4] == mark and row[5:7] == "  ", (mark, row)
        assert row[7] != " ", (mark, row)

    # ---- the pane's projections: a pending row carries the pace the finished steps
    #      taught, the step in flight its running clock against that pace, the bar the time
    #      left and the clock it lands on, and a step past twice its estimate is flagged
    est_state = dict(
        rich_state,
        todos=[
            {"task": "one", "completed": True},
            {"task": "two", "completed": True},
            {"task": "three", "completed": True},
            {"task": "four", "completed": False},
            {"task": "five", "completed": False},
        ],
        done=3, total=5, list_version=4,
        task_times={
            "one": {"started_ms": 0, "done_ms": 60_000, "elapsed_ms": 60_000},
            "two": {"started_ms": 0, "done_ms": 120_000, "elapsed_ms": 120_000},
            "three": {"started_ms": 0, "done_ms": 180_000, "elapsed_ms": 180_000},
            "four": {"started_ms": SWEEP_NOW - 30_000, "done_ms": None,
                     "elapsed_ms": 30_000},
        },
    )
    # the median of 1m/2m/3m finished steps is 2m; the active step is 30s in, so 1m30s of
    # it is left, plus a full 2m for the one still waiting: 3m30s to run
    assert module.step_pace_ms(est_state["task_times"], est_state["todos"],
                               SWEEP_NOW) == 120_000
    assert module.remaining_estimate_ms(est_state["task_times"], est_state["todos"],
                                        SWEEP_NOW, 120_000) == 210_000
    assert module.fmt_estimate(120_000) == "~2m"
    assert module.fmt_eta(210_000, SWEEP_NOW) == time.strftime(
        "%H:%M", time.localtime((SWEEP_NOW + 210_000) / 1000))
    assert module.fmt_variance(-80_000) == "-1m20s" and module.fmt_variance(40_000) == "+40s"
    est_wide = ansi.sub("", module.render(
        est_state, True, watching=999, width=80, height=20, now_ms=SWEEP_NOW,
    ))
    est_lines = est_wide.splitlines()
    active_est = next(line for line in est_lines if "➔" in line)
    # the active row's number is a BADGE: the clock, then the estimate in brackets, so the
    # row reads as one item and the estimate cannot be mistaken for another duration
    assert "30s [~2m]" in active_est and "STUCK" not in active_est, active_est
    pending_est = next(line for line in est_lines if "○" in line)
    assert pending_est.rstrip().endswith("~2m │"), pending_est
    est_bar = bar_row_of(est_wide)
    # The finished steps are 1m, 2m and 3m, so the spread behind the pace is 1m–3m — 3x, and
    # therefore wide enough to be said out loud. Where the row has room for only one of
    # them the RANGE takes it and the ETA does not: the range says something the bare number
    # cannot, while the ETA is that same number told as a clock. The plain path (checked
    # below) keeps the bare token, because scripts parse it.
    assert "EST REM 3m30s (1m–3m)" in est_bar, est_bar
    # ...and on a wider row it carries the ETA as well: dropping the row's label and the
    # duplicate pace bought the columns. The PRIORITY is what the width decides, not
    # whether the fact exists — `EST REM` and the range come first, the clock is added only
    # when the range has already fitted.
    assert " | ETA " in est_bar, est_bar
    mid_est_bar = bar_row_of(ansi.sub("", module.render(
        est_state, True, watching=999, width=68, height=20, now_ms=SWEEP_NOW,
    )))
    assert "EST REM 3m30s (1m–3m)" in mid_est_bar and "ETA" not in mid_est_bar, mid_est_bar
    # ...and a list whose finished steps AGREE shows no range at all: 2m from 2m is a real
    # 2m, so the eye keeps the plain number and the ETA keeps its place on the row
    even_times = {
        "step a": {"started_ms": 0, "done_ms": 120_000, "elapsed_ms": 120_000},
        "step b": {"started_ms": 0, "done_ms": 120_000, "elapsed_ms": 120_000},
    }
    even_todos = [
        {"task": "step a", "completed": True},
        {"task": "step b", "completed": True},
        {"task": "step c", "completed": False},
    ]
    assert module.pace_spread_ms(even_times, even_todos, SWEEP_NOW) == (120_000, 120_000)
    assert not module.is_wide(120_000, 120_000), "2m from 2m must not be called wide"
    assert module.fmt_estimate_spread(120_000, (120_000, 120_000)) == "~2m"
    even_bar = bar_row_of(ansi.sub("", module.render(
        dict(est_state, task_times=even_times, todos=even_todos, done=2, total=3),
        True, watching=999, width=80, height=20, now_ms=SWEEP_NOW,
    )))
    assert "~2m (" not in even_bar and " | ETA " in even_bar, even_bar
    # ---- the overall time to the goal: 30s already spent plus the 3m30s still to run.
    #      The active step is the only one with a real start (0 means "never seen
    #      running"), so 30s is what the list can be said to have spent.
    assert module.elapsed_total_ms(
        est_state["task_times"], est_state["todos"], SWEEP_NOW) == 30_000
    assert module.total_estimate_ms(
        est_state["task_times"], est_state["todos"], SWEEP_NOW, 120_000) == 240_000
    # no step was ever seen running: there is no start to measure a total from, and a
    # zero here would read as a list that took no time at all
    assert module.elapsed_total_ms({}, est_state["todos"], SWEEP_NOW) is None
    assert module.total_estimate_ms({}, est_state["todos"], SWEEP_NOW, 120_000) is None
    # a list whose clocks have all STOPPED freezes: the span is first start to last stop,
    # not to `now` — a finished list left up for two days used to read `65h59m spent`,
    # climbing with nobody working. The two clocks here run 1s->61s and 20s->200s.
    done_times = {
        "one": {"started_ms": 1_000, "done_ms": 61_000, "elapsed_ms": 60_000},
        "two": {"started_ms": 20_000, "done_ms": 200_000, "elapsed_ms": 180_000},
    }
    done_todos = [
        {"task": "one", "completed": True},
        {"task": "two", "completed": True},
    ]
    assert module.elapsed_total_ms(done_times, done_todos, 999_999_999) == 199_000
    # ...while a list with one clock still counting still measures to `now`, so it keeps
    # moving between the watcher's writes
    open_times = dict(done_times, two={"started_ms": 20_000, "done_ms": None})
    assert module.elapsed_total_ms(open_times, done_todos, 999_999_999) == 999_998_999
    goal_row = next(line for line in est_lines if "GOAL" in line)
    # Two numbers, not three: what this row has and nowhere else has is how much is behind
    # the owner and how much the whole thing is. The third it used to print — the time
    # left — is the `EST REM` on the bar row above, and saying it twice was the repetition
    # the footer was carrying.
    assert "30s spent" in goal_row and "4m00s total" in goal_row, goal_row
    # ...and a FINISHED list's row is the same two numbers whenever it is drawn: the pane
    # left open for ten more hours must not show a `spent` that grew by ten hours
    done_state = dict(est_state, todos=done_todos, done=2, total=2,
                      task_times=done_times, turn_ended=True)
    frozen_row = next(ln for ln in ansi.sub("", module.render(
        done_state, True, watching=999, width=80, height=20, now_ms=SWEEP_NOW,
    )).splitlines() if "GOAL" in ln)
    later_row = next(ln for ln in ansi.sub("", module.render(
        done_state, True, watching=999, width=80, height=20,
        now_ms=SWEEP_NOW + 10 * 3_600_000,
    )).splitlines() if "GOAL" in ln)
    assert frozen_row == later_row, (frozen_row, later_row)
    assert "3m19s spent" in frozen_row and "3m19s total" in frozen_row, frozen_row
    assert "left" not in goal_row, goal_row
    # the row is bought with height the owner gave the pane, never with a step off the
    # list: a pane too short for one drops the row, and the number moves onto the bar's
    # own row instead of being lost
    est_squat = ansi.sub("", module.render(
        est_state, True, watching=999, width=120, height=8, now_ms=SWEEP_NOW,
    ))
    assert not any("GOAL" in line for line in est_squat.splitlines()), est_squat
    squat_bar = bar_row_of(est_squat)
    assert "TOT 4m" in squat_bar, squat_bar
    # ...and the row is additive height inside the budget, not a step traded for it: the
    # frame still fits `height`, and all five steps are still on screen
    assert len(est_wide.splitlines()) <= 20, est_wide
    step_rows = [ln for ln in est_lines if any(g in ln for g in ("✔", "➔", "○"))]
    assert len(step_rows) == 5, step_rows
    # the bar keeps its 20 cells and the projection only uses leftover room: a narrow pane
    # drops the suffix rather than squeezing the bar
    est_narrow = ansi.sub("", module.render(
        est_state, True, watching=999, width=46, height=20, now_ms=SWEEP_NOW,
    ))
    narrow_bar = bar_row_of(est_narrow)
    assert "EST REM" not in narrow_bar, narrow_bar
    narrow_cells = narrow_bar.split("[", 1)[1].split("]", 1)[0]
    assert narrow_cells.count("█") + narrow_cells.count("░") == 20, narrow_bar
    # a step twice its pace is flagged, but only where the flag still leaves the task room
    tight_active = next(line for line in rich.splitlines() if "➔" in line)
    assert "[STUCK?]" not in tight_active, tight_active  # 46 columns is too tight
    wide_stuck = ansi.sub("", module.render(
        rich_state, True, watching=999, width=66, height=20, now_ms=SWEEP_NOW,
    ))
    stuck_line = next(line for line in wide_stuck.splitlines() if "➔" in line)
    assert "[STUCK?]" in stuck_line and "59m59s" in stuck_line, stuck_line
    # a collapsed run of finished work carries its net variance, once there is a
    # distribution to measure against
    var_todos = [{"task": f"step {n}", "completed": n < 6} for n in range(8)]
    var_times = {
        f"step {n}": {"started_ms": 0, "done_ms": span, "elapsed_ms": span}
        for n, span in enumerate((60_000, 60_000, 60_000, 60_000, 60_000, 300_000))
    }
    assert module.run_variance_ms(var_times, var_todos, SWEEP_NOW, 60_000) == 240_000
    var_frame = ansi.sub("", module.render(
        dict(rich_state, todos=var_todos, done=6, total=8, list_version=5,
             task_times=var_times),
        True, watching=999, width=46, height=9, now_ms=SWEEP_NOW,
    ))
    assert "6 earlier steps completed (+4m00s)" in var_frame, var_frame
    # a remembered NAME no longer prices a step by itself: the `own wording` rung was retired
    # (it fired 0 times in 170 replayed steps), so `five` is remembered at 5m and still reads
    # the list's pace, 2m — which is all the ladder can honestly say about a waiting row
    hist_state = dict(est_state, task_history={"five": 300_000})
    hist_wide = ansi.sub("", module.render(
        hist_state, True, watching=999, width=80, height=20, now_ms=SWEEP_NOW,
    ))
    hist_row = next(line for line in hist_wide.splitlines() if "○" in line)
    assert hist_row.rstrip().endswith("~2m │"), hist_row
    hist_bar = bar_row_of(hist_wide)
    assert "EST REM 3m30s" in hist_bar, hist_bar

    # ---- the bound on a YOUNG list's pace, and the measurement behind it. A median of one
    #      or two finished steps is not a distribution, and that is exactly where the
    #      estimates went badly wrong: replayed over the 258 steps recovered from this
    #      machine's CLI journals (2026-09-29), a 2s flip could project a whole list at
    #      `~2s` while the next step took 1m53s — 45.7x out. Two fixes came out of that
    #      replay: a sub-10s span is not evidence at all (MIN_LABEL_MS), and a young list's
    #      pace is held within 4x of the remembered one rather than replaced by it.
    #      Blending instead of bounding was tried and is WORSE (median 3.1x).
    def pace_of(spans, remembered):
        return module.step_pace_ms(
            {f"s{n}": {"started_ms": 0, "done_ms": span, "elapsed_ms": span}
             for n, span in enumerate(spans)},
            [{"task": f"s{n}", "completed": True} for n in range(len(spans))],
            SWEEP_NOW, remembered,
        )
    # the floor first: a sub-10s span is a list flip and is not a sample at all (see
    # MIN_LABEL_MS), so it cannot pace the list however few real samples there are
    assert pace_of([2_000], {"t": 50_000}) == 50_000, "a 2s flip is not a pace"
    assert pace_of([2_000, 4_000], {"t": 50_000}) == 50_000, "nor are two of them"
    assert pace_of([2_000, 20_000], {"t": 50_000}) == 20_000, "the real sample stands alone"
    # and then the bound: one or two REAL samples are not a distribution, so a young list's
    # pace is held within 4x of the remembered one
    assert pace_of([12_000], {"t": 50_000}) == 12_500, "a 12s single sample is held near 50s"
    assert pace_of([600_000], {"t": 50_000}) == 200_000, "nor must one 10m sample"
    assert pace_of([12_000, 14_000], {"t": 50_000}) == 13_000, "two samples: still bounded"
    assert pace_of([12_000, 14_000, 19_000], {"t": 50_000}) == 14_000, "three: the list's own median"
    assert pace_of([12_000], {}) == 12_000, "nothing remembered: nothing to bound against"
    # an older task log stored a bare number here; a small record must read the same way
    assert module.hist_med(50_000) == 50_000 and module.hist_med({"med": 50_000}) == 50_000
    assert module.hist_med(None) is None and module.hist_med({"n": 2}) is None
    say("bounds a young list's pace against the remembered one: ok")
    # the plain, machine-readable path carries the same projections INLINE: a piped
    # `snap` is parsed by scripts, so an estimate may never claim a line of its own
    plain_wide = ansi.sub("", module.render(est_state, False, width=80, now_ms=SWEEP_NOW))
    assert "· 30s… / ~2m" in plain_wide, plain_wide    # the running step
    assert "· ~2m" in plain_wide, plain_wide           # the step still waiting
    assert "EST REM: 3m30s" in plain_wide and "| ETA " in plain_wide, plain_wide
    assert "TOT 4m" in plain_wide, plain_wide  # the same total, inline as always
    assert not any(line.strip().startswith("~") for line in plain_wide.splitlines()), plain_wide
    plain46 = ansi.sub("", module.render(est_state, False, width=46, now_ms=SWEEP_NOW))
    bare_clocks = ansi.sub("", module.render(
        dict(est_state, task_times={}), False, width=46, now_ms=SWEEP_NOW,
    ))
    # estimates are inline-only, so they cannot change how many lines the strip takes
    assert len(plain46.splitlines()) == len(bare_clocks.splitlines()), (plain46, bare_clocks)
    say("projects the pace onto the steps, the bar and the strip: ok")

    # ...and the greys are the theme's, not the terminal's `dim` attribute
    saved_greys = {k: os.environ.get("FBTODO_" + k) for k in ("MUTED", "TRACK", "TRUECOLOR")}
    os.environ.update(FBTODO_MUTED="#ff8800", FBTODO_TRACK="#0000ff", FBTODO_TRUECOLOR="1")
    try:
        painted = module.render(rich_state, True, watching=999, width=46, height=20,
                                now_ms=SWEEP_NOW)
        grey_row = next(line for line in painted.splitlines() if "Diagnose the frozen" in line)
        assert re.search(
            r"\x1b\[38;2;255;136;0m[0-9]+m[0-9]{2}s\x1b\[0m "
            r"\x1b\[38;2;107;107;115m│",  # the duration ends against the frame's own ink
            grey_row,
        ), repr(grey_row)
        # the frame is drawn in the faint role (242 grey), not the `dim` attribute — the
        # border, the rules and the collapsed-run connectors are all that one ink
        assert "\x1b[38;2;107;107;115m╭── " in painted, repr(painted.splitlines()[0])
        assert "\x1b[2m" not in painted, "the pane still rides on the dim attribute"
        # A finished row recedes as ONE unit: the tick is the success hue dimmed exactly
        # like the description beside it, so the marker no longer out-shouts the words it
        # belongs to — and nothing in the frame is a bright raw green any more.
        assert "\x1b[2;38;2;46;160;67m  ✔\x1b[0m" in grey_row, repr(grey_row)
        assert "\x1b[32m" not in painted and "\x1b[1;32m" not in painted, repr(painted)
        # ...the goal is the `active` ink rather than a second bold white, and the one row
        # being worked on is the only text in the frame carrying weight
        goal_row = next(line for line in painted.splitlines() if "▸ Goal:" in line)
        assert "\x1b[38;2;230;237;243m" in goal_row, repr(goal_row)
        active_row = next(line for line in painted.splitlines() if "➔" in line)
        assert "\x1b[1;38;2;230;237;243m" in active_row, repr(active_row)
        # ...with its estimate as an accent badge, which is what tells the row apart from
        # `○ ~2m` on the steps that have not started
        assert "\x1b[1;36m" in active_row and "[" in active_row, repr(active_row)
        assert "\x1b[2;38;2;255;136;0mDiagnose" in grey_row, repr(grey_row)
        assert "\x1b[38;2;255;136;0m▸ Goal:" in painted, repr(painted)
        painted_bar = bar_row_of(painted)
        assert "\x1b[38;2;0;0;255m░" in painted_bar, repr(painted_bar)
    finally:
        for key, value in saved_greys.items():
            if value is None:
                os.environ.pop("FBTODO_" + key, None)
            else:
                os.environ["FBTODO_" + key] = value
        set_knob(module, "_THEME_CACHE", None)
    say("lays out the steps, the heading and the footer: ok")

    compact = ansi.sub("", module.render(wide_state, False, width=34, now_ms=1000))
    assert "live " in compact and "list #3" in compact, compact
    say("renders within the pane width and ticks the footer clock: ok")

    # ---- the frame's palette is themeable, and the sources resolve the tool's usual way:
    #      the env beats `./.fbtodo-theme.json`, which beats `~/.config/fbtodo/theme.json`
    saved_env = {k: os.environ.get("FBTODO_" + k.upper()) for k in module.THEME_KEYS}
    saved_truecolor = os.environ.get("FBTODO_TRUECOLOR")
    saved_paths = (module.THEME_FILE_GLOBAL, module.THEME_FILE_LOCAL)
    theme_dir = os.path.join(TEST_HOME, "theme")
    os.makedirs(theme_dir, exist_ok=True)
    global_file = os.path.join(theme_dir, "global.json")
    local_file = os.path.join(theme_dir, "local.json")
    set_knob(module, "THEME_FILE_GLOBAL", global_file)
    set_knob(module, "THEME_FILE_LOCAL", local_file)
    try:
        for key in module.THEME_KEYS:
            os.environ.pop("FBTODO_" + key.upper(), None)
        assert module.read_theme() == module.THEME_DEFAULTS, module.read_theme()
        with open(global_file, "w", encoding="utf-8") as fh:
            json.dump({"accent": "#ff8800", "gradient_start": "#ff0000"}, fh)
        theme = module.read_theme()
        assert theme["accent"] == "#ff8800" and theme["gradient_start"] == "#ff0000", theme
        assert theme["gradient_end"] == module.THEME_DEFAULTS["gradient_end"], theme
        with open(local_file, "w", encoding="utf-8") as fh:
            json.dump({"accent": "3;35", "gradient_end": "#0000ff", "nonsense": "#fff"}, fh)
        theme = module.read_theme()
        assert theme["accent"] == "3;35", theme  # the project file wins, per key...
        assert theme["gradient_start"] == "#ff0000", theme  # ...not all or nothing
        assert theme["gradient_end"] == "#0000ff", theme
        assert "nonsense" not in theme, theme
        os.environ["FBTODO_ACCENT"] = "1;33"
        assert module.read_theme()["accent"] == "1;33", module.read_theme()  # ...and env wins
        starts = module._theme_gradient(module.read_theme())
        assert starts == (module._hex_rgb("#ff0000"), module._hex_rgb("#0000ff")), starts
        # hex is 24-bit where the terminal takes it and the nearest 256 entry where it does
        # not; a raw SGR code passes through either way
        assert module._color_sgr("#ff8800", True) == "38;2;255;136;0"
        assert module._color_sgr("#ff8800", False) == "38;5;208"
        assert module._color_sgr("#808080", False) == "38;5;244"  # the grey ramp
        assert module._color_sgr("#fff", True) == "38;2;255;255;255"  # shorthand
        assert module._color_sgr("1;33", False) == "1;33"
        # the frame is what follows the palette: the title and the progress label are
        # painted in the accent, and the bar ramps from the first stop to the second
        os.environ["FBTODO_GRADIENT_START"] = "#ff0000"
        os.environ["FBTODO_GRADIENT_END"] = "#0000ff"
        os.environ["FBTODO_TRUECOLOR"] = "1"
        # `with_patch` so the frame carries every footer label at once: the bar's column is
        # the one the others keep, which is only checkable beside one of them
        themed = module.render(
            with_patch, True, watching=999, width=46, height=20, now_ms=SWEEP_NOW,
        )
        # the title is the accent as a reverse-video chip, not a plain bold word
        assert "\x1b[7;1;33m FREEBUFF TODOS " in themed, repr(themed.splitlines()[0])
        bar_row = bar_row_of(themed)
        # the bar row has no label to paint any more, and the ratio on it is the `active`
        # ink — the accent is spent on the ramp and the chip, not on a word
        assert "PROGRESS" not in themed, repr(bar_row)
        assert re.search(r"\x1b\[38;2;230;237;243m\d+% \(\d+/\d+\)", bar_row), repr(bar_row)
        # ...and the footer's rows now share one left edge: the bar's own row starts in the
        # column `PATCH` and `GOAL` start in, which the 8-column label it used to wear pushed
        # it one cell left of
        patch_led = next(line for line in themed.splitlines() if "PATCH" in line)
        assert (STRIP(bar_row).index("[") == STRIP(patch_led).index("PATCH") == 4), (
            repr(bar_row), repr(patch_led))
        # the ramp is sampled off the FILLED cells only: the empty track is painted in
        # its own colour, and counting that as a stop would hide a flat bar
        stops = [tuple(int(v) for v in s) for s in re.findall(
            r"\x1b\[38;2;(\d+);(\d+);(\d+)m█", bar_row
        )]
        assert len(stops) >= 2, repr(bar_row)
        first, last = stops[0], stops[-1]
        assert first[0] > last[0] and last[2] > first[2], (first, last)  # ramps red to blue
        # a broken theme must not break the pane: a value that is not a colour is IGNORED,
        # so the stop keeps whatever the earlier source gave it — and the built-in default
        # is what is left when no source gave it one
        os.environ.pop("FBTODO_GRADIENT_START", None)
        os.environ["FBTODO_GRADIENT_END"] = "mauve"
        assert module._theme_gradient(module.read_theme())[0] == module._hex_rgb("#ff0000")
        assert module._theme_gradient(module.read_theme())[1] == module._hex_rgb("#0000ff")
        assert [name for name, _v in module.THEME_PROBLEMS] == ["FBTODO_GRADIENT_END"], \
            module.THEME_PROBLEMS
        with open(local_file, "w", encoding="utf-8") as fh:
            json.dump({"gradient_end": "mauve"}, fh)
        os.environ.pop("FBTODO_GRADIENT_END", None)
        assert module._theme_gradient(module.read_theme())[1] == module._hex_rgb("#2ea043")
        assert [name for name, _v in module.THEME_PROBLEMS] == ["project theme: gradient_end"]
        # ...and a theme file that is not an object is ignored
        with open(local_file, "w", encoding="utf-8") as fh:
            fh.write("[1, 2, 3]")
        assert module.read_theme()["accent"] == "1;33", module.read_theme()

        # ---- an untrusted theme value is a piece of code the terminal runs. H2 measured
        #      `FBTODO_ACCENT='1;36<BEL><ESC>]52;c;…<BEL>'` painting real escapes into the
        #      frame, because a value without a `#` is passed through as an SGR list. A
        #      value is now an SGR parameter list or a `#rrggbb` colour and nothing else,
        #      and what was thrown away is NAMED in `status`: a theme that silently does
        #      nothing is how the value got this far.
        bad_values = {
            "FBTODO_ACCENT": "1;36\x07\x1b]52;c;aGFjaw==\x07",
            "FBTODO_MUTED": "not-a-colour",
            "FBTODO_TRACK": "1;36;2J",
        }
        for var, value in bad_values.items():
            os.environ[var] = value
        theme = module.read_theme()
        # the refused env value is thrown away, so `accent` keeps what the theme FILE gave
        # it and a role nothing else set stays on the built-in colour
        assert theme["accent"] == "#ff8800", theme["accent"]
        assert theme["muted"] == module.THEME_DEFAULTS["muted"], theme["muted"]
        assert theme["track"] == module.THEME_DEFAULTS["track"], theme["track"]
        reported = " ".join(name for name, _value in module.THEME_PROBLEMS)
        for var in bad_values:
            assert var in reported, (var, module.THEME_PROBLEMS)
        bad_frame = module.render(
            with_patch, True, watching=999, width=46, height=20, now_ms=SWEEP_NOW,
        )
        assert "\x1b]52" not in bad_frame and "\x07" not in bad_frame, repr(bad_frame[:200])
        # ...and a value that IS one of the two forms still gets through: a filter, not a mute
        os.environ["FBTODO_ACCENT"] = "1;33"
        os.environ["FBTODO_MUTED"] = "#89b4fa"
        os.environ["FBTODO_TRACK"] = "1;90"
        assert module.read_theme()["accent"] == "1;33"
        assert module.read_theme()["muted"] == "#89b4fa"
        assert module.read_theme()["track"] == "1;90"
        assert not module.THEME_PROBLEMS, module.THEME_PROBLEMS
        for var in bad_values:
            os.environ.pop(var, None)
        env["FBTODO_ACCENT"] = "not-a-colour"
        status_out = run("status").stdout
        env.pop("FBTODO_ACCENT", None)
        assert "theme values" in status_out and "FBTODO_ACCENT" in status_out, status_out[-400:]
        say("theme: a value that is neither a colour nor an SGR list is ignored, and `status` says so: ok")

        # ---- untrusted journal text (H1): a step's name, the `Goal:` line, `now`, the
        #      action feed's `what` and a command string all reached the terminal exactly
        #      as they were written. Measured 2026-09-29: OSC 52 (sets the clipboard),
        #      OSC 0 (retitles the window), a CSI cursor move, a RIGHT-TO-LEFT OVERRIDE, a
        #      zero-width space and a raw BEL all printed verbatim — in the rich pane, in
        #      the plain `snap` frame and in `json`. One filter now runs where a source's
        #      prose enters state AND where the frame is built, because a state file an
        #      older build wrote is still on disk.
        osc52 = "\x1b]52;c;aGFjaw==\x07"
        osc0 = "\x1b]0;pwnd\x07"
        csi = "\x1b[9;1H"
        rlo, zwsp, bom = "\u202e", "\u200b", "\ufeff"
        dirty_bits = (osc52, osc0, csi, rlo, zwsp, bom, "\u061c", "\u2028", "\u2029",
                      "\u2060", "\u2064", "\U000E0001", "\U000E007F")
        ESC_SEQ = re.compile(r"\x1b(?:\[[0-9;:?]*[A-Za-z]|.)")
        # The pane's OWN escapes are SGR and nothing else. A residual CSI cursor move, an
        # OSC, or a bare CSI with no `m` all fail this — which is the signature of a
        # payload that survived the filter.
        TOOL_SEQ = re.compile(r"^\x1b\[[0-9;:]*m$")

        def uncleaned(obj):
            """Every escape, control or bidi character still anywhere in a value."""
            found = []
            if isinstance(obj, dict):
                for key, value in obj.items():
                    found += uncleaned(key) + uncleaned(value)
            elif isinstance(obj, (list, tuple)):
                for value in obj:
                    found += uncleaned(value)
            elif isinstance(obj, str):
                found += [c for c in obj if c in dirty_bits or (ord(c) < 32 and c != "\n")]
            return found

        dirty_prose = "Goal: " + rlo + "ship it" + osc52 + "\n\nthe work " + osc0
        dirty_todos = [
            {"task": "tick " + osc52 + "one", "completed": True},
            {"task": "write " + rlo + "left" + zwsp, "completed": False},
        ]
        dirty_store = os.path.join(TEST_HOME, "dirtystore")
        os.makedirs(dirty_store, exist_ok=True)
        with open(os.path.join(dirty_store, "log.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "level": "DEBUG", "timestamp": "2026-01-01T00:00:00.000Z",
                "data": {
                    "iteration": 1,
                    "prompt": "run " + csi + "the thing",
                    "fullResponse": dirty_prose,
                    "toolCalls": [
                        {"toolName": "write_todos", "input": {"todos": dirty_todos}},
                        {"toolName": "run_terminal_command",
                         "input": {"command": "git status " + osc52, "pattern": "a" + bom}},
                    ],
                },
            }) + "\n")
        # ...the source's own state, before any pane sees it: `json` and `status` print
        # this file too, so cleaning only at render would leave the other readers exposed
        ingested = module.read_cli(dirty_store)
        assert ingested["todos"], ingested
        assert not uncleaned(ingested), uncleaned(ingested)[:8]
        assert "ship it" in json.dumps(ingested), "the readable part of the prose was dropped too"
        say("text: prose, a step's name and a command arrive from the journal stripped: ok")

        # ...and the render-side half, handed a state that never went through a source:
        # an older build's state file, or a cache, must not be able to paint a frame like
        # that either
        dirty_state = dict(
            with_patch, todos=dirty_todos, done=1, total=2, goal=dirty_prose,
            now="ran " + osc0 + "ls", list_version=3,
            observed=[{"verb": "ran " + csi, "what": "git " + osc52 + " status",
                       "ts_ms": SWEEP_NOW - 60_000}],
        )
        for colour in (False, True):
            framed = module.render(
                dirty_state, colour, watching=999, width=80, height=20, now_ms=SWEEP_NOW,
            )
            for bad in dirty_bits:
                assert bad not in framed, (colour, repr(bad), framed[:200])
            # the tool's own escapes are the only ones left: strip them and nothing that a
            # terminal would ACT on may remain
            assert not uncleaned(ESC_SEQ.sub("", framed)), (colour, uncleaned(framed)[:8])
            for seq in ESC_SEQ.findall(framed):
                assert TOOL_SEQ.match(seq), (colour, repr(seq))
            assert "ship it" in framed, framed[:400]
        # the action feed draws only when there is no list: give it one and check the same
        feed_frame = module.render(
            dict(dirty_state, todos=[]), True, watching=999, width=80, height=20,
            now_ms=SWEEP_NOW,
        )
        for bad in dirty_bits:
            assert bad not in feed_frame, (repr(bad), feed_frame[:200])
        assert not uncleaned(ESC_SEQ.sub("", feed_frame)), uncleaned(feed_frame)[:8]
        say("text: OSC 52, OSC 0, cursor moves and bidi overrides never reach the terminal: ok")

        # ---- and the STRUCTURAL half: not a list of remembered prose keys, but a walk of
        #      every string in the state. The allow-list this replaced cleaned the fields
        #      someone had thought of, so a thread's title, a session's `fb_dir`, a tool name in
        #      `tool_calls` and every `turn` field were printed as written. A fully populated
        #      state (stacked threads, a patch, an alert, the action feed, a store observation,
        #      turn) is copied once per string leaf, that one string replaced by a payload, and
        #      rendered plain and rich: nothing but SGR may come out. No key is exempt, not even
        #      the enum fields the tool owns (backend/source/status/goal_source/schema): they are
        #      cleaned like everything else, and their values are ASCII, so it is a no-op.
        ALL_BAD = "".join((osc52, osc0, csi, "\u202e", "\u061c", "\u2028", "\u2029", "\u2060",
                           "\u2064", "\U000E0001", "\U000E007F", "\x1b[?25l", "\t"))
        full_state = dict(
            with_patch,
            threads=[
                {"id": "t1", "title": "the first thread", "current": True, "running": True,
                 "source_updated_ms": SWEEP_NOW,
                 "todos": [{"task": "one", "completed": True}]},
                {"id": "t2", "title": "the second thread", "current": False, "running": False,
                 "source_updated_ms": SWEEP_NOW,
                 "todos": [{"task": "two", "completed": False}]},
            ],
            tool_calls={"read_files": 12, "code_search": 5, "write_todos": 2},
            turn={"start_ms": SWEEP_NOW - 60_000, "iterations": 9, "files": ["a.py", "b.py"],
                  "verbs": {"read_files": 3}, "truncated": False},
            observed=[{"verb": "edit", "what": "the renderer",
                       "ts_ms": SWEEP_NOW - 60_000}],
            status="live", heartbeat_ms=SWEEP_NOW, probed_ms=SWEEP_NOW,
            source_updated_ms=SWEEP_NOW, store_mtime_ms=SWEEP_NOW,
            thread="2026-09-29T11-26-00.000Z", model="deepseek-v4-flash",
            list_id=1, list_version=3, done=1, total=2,
        )

        def variants(obj, payload):
            """Every copy of `obj` with exactly one string in it replaced by `payload`.

            Dict KEYS count: a tool name in `tool_calls` is a string the pane prints and the
            tool does not own — and so is `status`. No key is skipped, because the filter skips
            none: every string at every depth is offered to the walk, which is the property
            this half of the sweep exists to check.
            """
            out = []
            if isinstance(obj, str):
                return [payload]
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if isinstance(key, str):
                        renamed = dict(obj)
                        del renamed[key]
                        renamed[payload] = value
                        out.append(renamed)
                    if isinstance(value, str):
                        out.append(dict(obj, **{key: payload}))
                    else:
                        for sub in variants(value, payload):
                            copy = dict(obj)
                            copy[key] = sub
                            out.append(copy)
            elif isinstance(obj, (list, tuple)):
                for i, value in enumerate(obj):
                    for sub in ([payload] if isinstance(value, str) else variants(value, payload)):
                        copy = list(obj)
                        copy[i] = sub
                        out.append(tuple(copy) if isinstance(obj, tuple) else copy)
            return out

        def clean_frame(frame, where):
            for bad in dirty_bits:
                assert bad not in frame, (where, repr(bad), frame[:200])
            assert not uncleaned(ESC_SEQ.sub("", frame)), (where, uncleaned(frame)[:8])
            for seq in ESC_SEQ.findall(frame):
                assert TOOL_SEQ.match(seq), (where, repr(seq))

        # every string leaf, one composite payload: the walk touches each in turn. Both the
        # list and the no-list (action-feed) layout are painted, plain and rich.
        swept = 0
        for dirty in variants(full_state, ALL_BAD):
            for label, st in (("list", dirty), ("feed", dict(dirty, todos=[]))):
                for color in (False, True):
                    framed = module.render(
                        st, color, watching=999, width=46 if color else 80,
                        height=20 if color else None, now_ms=SWEEP_NOW,
                    )
                    clean_frame(framed, (label, color))
                    swept += 1
        assert swept > 40, swept
        # ...and each payload on its own, in a rendered value and in a tool-call KEY
        for payload in (osc52, osc0, csi, "\u202e", "\u061c", "\u2028", "\u2060",
                        "\U000E0001", "\U000E007F", "\x1b[?25l", "\t"):
            for st in (dict(full_state, goal="head " + payload + " tail"),
                       dict(full_state, todos=[], tool_calls={payload + "read_files": 3})):
                for color in (False, True):
                    framed = module.render(
                        st, color, watching=999, width=46 if color else 80,
                        height=20 if color else None, now_ms=SWEEP_NOW,
                    )
                    clean_frame(framed, ("payload", repr(payload), color))
        # a state that only has something to shout about: `error` takes an early return, and
        # the payload must be gone there too
        for color in (False, True):
            framed = module.render(
                dict(full_state, error="broke: " + ALL_BAD), color, watching=999,
                width=46 if color else 80, height=20 if color else None, now_ms=SWEEP_NOW,
            )
            clean_frame(framed, ("error", color))
        say("text: every string in a state is filtered, not a list of known keys: ok")
    finally:
        for key, value in saved_env.items():
            var = "FBTODO_" + key.upper()
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value
        if saved_truecolor is None:
            os.environ.pop("FBTODO_TRUECOLOR", None)
        else:
            os.environ["FBTODO_TRUECOLOR"] = saved_truecolor
        set_knob(module, "THEME_FILE_GLOBAL", saved_paths[0])
        set_knob(module, "THEME_FILE_LOCAL", saved_paths[1])
        set_knob(module, "_THEME_CACHE", None)
    say("the frame palette resolves from the env and the theme files: ok")

    # ---- with the palette AND the colour depth handed to it, `render` is a function of its
    #      arguments: same state, same clock, same bytes, whatever the environment or the theme
    #      files say. That is what a recorded frame is a contract on (tests/golden) and what a
    #      repaint can be diffed against — a frame that quietly reads the environment behind the
    #      palette it was given can be neither.
    def frozen_frame(depth=True, theme=None, width=68, height=18):
        return module.render(
            rich_state, True, watching=999, width=width, height=height, now_ms=SWEEP_NOW,
            theme=dict(theme if theme is not None else module.THEME_DEFAULTS),
            truecolor=depth,
        )

    frozen = frozen_frame()
    assert frozen == frozen_frame(), "two frames of one state differ"
    probe = {var: os.environ.get(var)
             for var in ("FBTODO_ACCENT", "FBTODO_GRADIENT_END", "FBTODO_TRUECOLOR",
                         "COLORTERM", "TERM")}
    try:
        os.environ["FBTODO_ACCENT"] = "#ff0000"
        os.environ["FBTODO_GRADIENT_END"] = "#00ff00"
        os.environ["FBTODO_TRUECOLOR"] = "0"
        os.environ["COLORTERM"] = "truecolor"
        assert frozen_frame() == frozen, "the frame read the environment behind the palette"
    finally:
        for var, value in probe.items():
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value
    # ...and only the INK follows the depth: an 8-colour terminal gets different escapes, not a
    # different layout — same geometry, same words, or the two panes would disagree about what
    # is on the list
    shallow = frozen_frame(depth=False)
    assert shallow != frozen, "the colour depth made no difference at all"
    sgr = lambda s: re.sub(r"\x1b\[[0-9;]*m", "", s)  # noqa: E731
    assert sgr(shallow) == sgr(frozen), "the layout moved with the colour depth"
    say("render: handed a palette and a depth, the frame is a function of its arguments: ok")

    # ---- the pane is a fixed grid, so no frame may be wider than the width it was asked
    #      for or taller than the height: a row one cell over wraps, and one row too many
    #      scrolls the top border off the screen — which is exactly what the pane looks like
    #      when it has gone wrong. Checked across the sizes a pane is actually given, on a
    #      long, half-finished list (the busy case) and on an empty one (the bare case).
    long_list = [
        {"task": f"step {n} of a long list", "completed": n < 6,
         "started_ms": SWEEP_NOW - (20 - n) * 60_000, "done_ms": SWEEP_NOW - (19 - n) * 60_000}
        for n in range(14)
    ]
    wide_state = dict(rich_state, todos=long_list, done=6, total=14, task_times={
        f"step {n} of a long list": {"started_ms": SWEEP_NOW - (20 - n) * 60_000,
                                      "done_ms": SWEEP_NOW - (19 - n) * 60_000}
        for n in range(14)
    })
    for probe_state in (wide_state, dict(wide_state, todos=[], done=0, total=0)):
        for trial_w in (30, 34, 46, 68, 80, 120, 200):
            for trial_h in (5, 6, 8, 12, 18, 24, 40):
                frame = module.render(
                    probe_state, True, watching=999, width=trial_w, height=trial_h,
                    now_ms=SWEEP_NOW, theme=module.THEME_DEFAULTS, truecolor=True,
                )
                rows = frame.splitlines()
                over = [(len(r), module._cell_width(r)) for r in rows
                        if module._cell_width(r) > trial_w][:2]
                assert not over, (trial_w, trial_h, over, frame[:400])
                assert len(rows) <= trial_h, (trial_w, trial_h, len(rows), frame[-300:])
                plain = module.render(
                    probe_state, False, width=trial_w, height=trial_h, now_ms=SWEEP_NOW,
                )
                rows = plain.splitlines()
                assert all(module._cell_width(r) <= trial_w for r in rows), (trial_w, rows[:2])
                assert len(rows) <= trial_h, (trial_w, trial_h, len(rows))
    say("render: every frame fits the pane it was asked for, at every size: ok")

    # ---- the pane is repainted on a tick even when nothing moved (its clock does), so clearing
    #      the screen and painting the whole frame every tick is a flash on every tick — and the
    #      bytes are written to a pseudo-terminal at 1Hz forever. The repaint is a DIFF: the rows
    #      that changed, addressed in place, and nothing at all when the frame is identical. A
    #      frame whose SHAPE changed (a resize, or the first paint) is painted whole, because
    #      rows have moved and a row-wise rewrite would leave the old ones behind.
    full = module.render(
        rich_state, True, width=68, height=18, now_ms=SWEEP_NOW,
        theme=module.THEME_DEFAULTS, truecolor=True,
    ).splitlines()
    assert 6 <= len(full) <= 18, len(full)
    first = module.pane_repaint(None, full, 24)  # a pane taller than the frame keeps the newline
    assert first.startswith("\x1b[H\x1b[2J") and first.endswith("\n"), repr(first[-30:])
    exact = module.pane_repaint(None, full, len(full))  # a frame that FILLS the pane
    assert not exact.endswith("\n"), "a frame that fills the pane got a newline"
    assert module.pane_repaint(full, full, 24) == "", "an unchanged frame was written again"
    moved = list(full)
    moved[-2] = moved[-2].replace("WORKING", "ALL DONE")
    row = f"\x1b[{len(full) - 1};1H\x1b[2K{moved[-2]}"
    diff = module.pane_repaint(full, moved, 24)  # a pane taller than the frame
    assert diff == row + f"\x1b[{len(full) + 1};1H", repr(diff[-40:])  # ...cursor parked below
    assert module.pane_repaint(full, moved, len(full)) == row, "a full pane parked the cursor"
    assert "\x1b[2J" not in diff, "a one-row change cleared the screen"
    assert len(diff) < len(first) // 3, (len(diff), len(first))
    for shaped in (full + ["extra row"], full[:-2]):
        out = module.pane_repaint(full, shaped, 24)
        assert out.startswith("\x1b[H\x1b[2J"), (len(shaped), repr(out[:24]))
    say("the pane repaints only the rows that moved, and writes nothing when none did: ok")

    # ---- the frame has a floor: below 30 columns there is no room for a box, its rules and a
    #      step, so the plain strip is drawn even on a colour terminal — the same rule that
    #      decides `snap`'s output, in one place. What must NOT happen is a box drawn into a
    #      strip it does not fit, which is what the width check above would catch only after
    #      the fact.
    for narrow_w in (12, 20, 29):
        strip = module.render(
            rich_state, True, width=narrow_w, now_ms=SWEEP_NOW,
            theme=module.THEME_DEFAULTS, truecolor=True,
        )
        assert "╭" not in strip and "│" not in strip, (narrow_w, strip[:120])
        assert all(module._cell_width(line) <= narrow_w for line in strip.splitlines()), narrow_w
    boxed = module.render(
        rich_state, True, width=30, now_ms=SWEEP_NOW,
        theme=module.THEME_DEFAULTS, truecolor=True,
    )
    assert STRIP(boxed).splitlines()[0].startswith("╭"), boxed[:80]
    say("the frame has a floor of 30 columns; below it the strip is plain: ok")

    # ---- the framed top border: the right slot names the watcher, or — with no watcher
    #      serving the pane — the session it is showing, and it never breaks the frame
    def top_of(state_dict, width, watching=None):
        line = module._plain(module.render(
            state_dict, True, watching=watching, width=width, height=20, now_ms=SWEEP_NOW,
        ).splitlines()[0])
        assert module._cell_width(line) == width, (width, line)
        # the title sits on a chip, so its one space of padding shows on each side
        assert line.startswith("╭──  FREEBUFF TODOS  ") and line.endswith("╮"), line
        return line

    border_state = dict(
        rich_state, backend="cli", session="2026-09-24T07-05-26.585Z",
    )
    watched = top_of(border_state, 46, watching=999)
    assert "watcher: pid 999" in watched, watched
    assert module._session_label(border_state) not in watched, watched
    # no watcher is serving this pane, so the border says which session it is showing —
    # and never the raw ISO stamp, which used to be clipped into that slot
    bare = top_of(border_state, 46)
    assert "watcher" not in bare, bare
    assert module._session_label(border_state) in bare, (bare, module._session_label(border_state))
    assert "2026-09-24T07-05" not in bare, bare
    for width in (30, 33, 34, 46, 60, 80, 120):
        line = top_of(border_state, width)
        if width < 33:  # no room for a readable tag: the slot is dropped, not mangled
            assert "cli" not in line, (width, line)
    assert module._session_label({"backend": "cli"}) == "cli"
    assert module._session_label({"session": "manual-run"}) == "manual-run"
    assert module._session_label({}) == "no session"

    # ---- ...and when the resolution had to pass a source over, the title says which list
    #      this is AND why it is not the other one. `_snapshot` writes the note (`source_why`,
    #      and only for `auto`), the border and the plain heading both carry it, and a state
    #      without one is the frame it always was — every hand-built fixture here has no note,
    #      which is exactly why the assertions above still describe the same border.
    assert module._resolution_note([], "desktop") == "", "nothing skipped, nothing to say"
    assert module._resolution_note(["cli finished 31h"], "desktop") == (
        "cli finished 31h → desktop"
    ), module._resolution_note(["cli finished 31h"], "desktop")
    assert module._resolution_note(["cli none here", "desktop none here"], None) == (
        "cli none here · desktop none here"
    ), "with nothing answered the reasons stand alone"
    note = {"source_why": "cli finished 31h → desktop"}
    assert module._source_title(note, 100, "cli · 09-30 07:12") == (
        "FREEBUFF TODOS · cli finished 31h → desktop"
    ), module._source_title(note, 100, "cli · 09-30 07:12")
    assert module._source_title({}, 100, "cli · 09-30 07:12") == "FREEBUFF TODOS"

    def border_of(state_dict, width):
        return module._plain(module.render(
            state_dict, True, watching=None, width=width, height=20, now_ms=SWEEP_NOW,
        ).splitlines()[0])

    noted = border_of(dict(border_state, **note), 100)
    assert module._cell_width(noted) == 100, (100, noted)
    assert "cli finished 31h → desktop" in noted, noted
    assert module._session_label(border_state) in noted, (
        "the note costs the right slot no session: both fit at this width"
    )
    # ...and a title it cannot say usefully is not said: at 44 columns the session keeps its
    # columns and the pane keeps its name, rather than the note shoving the stamp off the rim
    capped = border_of(dict(border_state, **note), 44)
    assert capped.startswith("╭──  FREEBUFF TODOS  "), capped
    assert "cli finished" not in capped, capped
    assert "cli · 09-24" in capped, (
        "...and the session it is showing is still the thing in the slot\n" + capped
    )
    # the plain renderer says it in the same place: the line naming what is being shown
    listed = module._plain(module.render(dict(border_state, **note), False, width=100))
    assert "cli finished 31h → desktop" in listed.splitlines()[0], listed
    assert module._plain(module.render(border_state, False, width=100)).splitlines()[0] == (
        listed.splitlines()[0].replace(" · cli finished 31h → desktop", "")
    ), "the note is the only difference"
    say("the pane's title names the source that answered and what it passed over: ok")
    longer = top_of(dict(border_state, session="a-session-name-that-will-never-fit"), 46)
    assert "…" in longer, longer
    assert module._cell_width(longer) == 46, longer
    say("the top border carries the watcher, or the session: ok")

    # ---- the frame owns the pane: a frame that already fills every row must not be
    #      followed by a newline, or each repaint scrolls the top border off the screen
    draw_rows, draw_cols = 12, 46
    draw_home = os.path.join(TEST_HOME, "draw-home")
    os.makedirs(draw_home, exist_ok=True)
    draw_state_file = os.path.join(draw_home, "fbtodo-state.json")

    def first_paint(todos):
        """The bytes of one pane's first paint, from a pty exactly `draw_rows` tall."""
        import pty

        now_ms = int(time.time() * 1000)
        with open(draw_state_file, "w", encoding="utf-8") as fh:
            json.dump({
                "status": "watching", "tool_version": module.VERSION, "backend": "cli",
                "session": "2026-09-24T07-05-26.585Z", "list_id": "draw",
                "list_version": 1, "heartbeat_ms": now_ms, "source_updated_ms": now_ms,
                "goal": "fit the frame in the pane", "now": None, "nudge": None,
                "todos": todos,
                "done": sum(1 for t in todos if t["completed"]), "total": len(todos),
            }, fh)
        victim = spawn_quiet("sleep", "600")
        time.sleep(0.2)
        pid, fd = pty.fork()
        if pid == 0:
            # `shutil.get_terminal_size` reads these first, so the pane draws for the size
            # this check picked rather than for the pty's unset 0x0
            os.environ["FBTODO_HOME"] = draw_home
            os.environ["COLUMNS"], os.environ["LINES"] = str(draw_cols), str(draw_rows)
            # ...and a REAL terminal, because which frame the pane draws is decided from
            # one: `use_color` reads TERM, and under the `dumb` a runner's environment
            # carries the pane draws its plain, unboxed layout — so these frame
            # assertions failed on the machine the suite ran on, not on the code. Pinned
            # here rather than inherited so the row is one check everywhere; COLORTERM
            # rides along for the designed palette, and NO_COLOR is dropped because a
            # developer's own "no colour" must not decide a test's frame either. The
            # whole suite must still pass with TERM=dumb and no colour environment —
            # `ci.yml`'s `dumb-terminal` lane holds that — so this pin stays in the
            # child: the rest of the suite keeps seeing the terminal it was run in.
            os.environ["TERM"] = "xterm-256color"
            os.environ["COLORTERM"] = "truecolor"
            os.environ.pop("NO_COLOR", None)
            os.execv(sys.executable, [sys.executable, PANES, "pane", "--watch-pid",
                                      str(victim.pid), "--no-daemon", "-i", "0.2"])
        os.set_blocking(fd, False)
        # Wait for the frame and ONE diffed row rather than for a fixed second. The split
        # below cuts the first frame at its first row-addressed write; a fixed sleep assumed
        # that write had happened by then, and under load a pane whose first paint was late
        # had the instance killed before it — the closing notice then landed inside the
        # "frame" and the check failed on a pane that had drawn perfectly. Measured
        # 2026-09-29 on a loaded machine; the same pane passes instantly when idle.
        early, deadline = b"", time.time() + 10
        while time.time() < deadline:
            try:
                early += os.read(fd, 65536)
            except (BlockingIOError, OSError):
                pass
            if re.search(rb"\x1b\[\d+;1H", early):
                break
            time.sleep(0.05)
        kill_tree(victim)
        data, deadline = early, time.time() + 8
        while time.time() < deadline:
            try:
                chunk = os.read(fd, 65536)
            except (BlockingIOError, OSError):
                chunk = b""
            if chunk:
                data += chunk
                continue
            if os.waitpid(pid, os.WNOHANG)[0]:  # it has painted for the last time
                break
            time.sleep(0.05)
        try:
            os.close(fd)
        except OSError:
            pass
        chunks = data.split(b"\x1b[2J")
        assert len(chunks) >= 2, data
        # ...the FIRST frame only: the pane diffs its later paints (a row-addressed write is
        # where the next one starts), so what follows the clear is that frame and then a change.
        return re.split(rb"\x1b\[\d+;1H", chunks[1])[0]

    short_paint = first_paint([{"task": f"step {i}", "completed": i < 2} for i in range(3)])
    full_paint = first_paint([{"task": f"step {i}", "completed": i < 2} for i in range(29)])
    for label, paint in (("short frame", short_paint), ("full frame", full_paint)):
        # the pty it was drawn into turns newlines into CRLF; strip both the colour and
        # that, so this reads the frame the pane actually built
        text = re.sub(rb"\x1b\[[0-9;?]*[a-zA-Z]", b"", paint).replace(b"\r\n", b"\n")
        frame = text.rstrip(b"\n")
        tail = text[len(frame):]
        lines = frame.split(b"\n")
        assert frame.startswith("╭──  FREEBUFF TODOS".encode()), (label, frame[:80])
        assert frame.endswith("╯".encode()), (label, frame[-80:])
        assert sum(1 for line in lines if line.startswith("╭".encode())) == 1, (label, lines)
        assert len(lines) <= draw_rows, (label, len(lines))
        assert tail == (b"" if len(lines) >= draw_rows else b"\n"), (label, len(lines), tail)
    def rows_of(paint):
        text = re.sub(rb"\x1b\[[0-9;?]*[a-zA-Z]", b"", paint).replace(b"\r\n", b"\n")
        return text.rstrip(b"\n").split(b"\n")

    full_lines = rows_of(full_paint)
    assert len(full_lines) == draw_rows, (len(full_lines), full_lines)  # the tight branch ran
    assert len(rows_of(short_paint)) < draw_rows, short_paint  # ...and so did the roomy one
    say("a full-height frame is not followed by a newline: ok")

    # ---- per-task timing is measured, so it must behave under observation: the clock
    #      belongs to the step being worked on, counts up while it runs, freezes when it
    #      is ticked off, and moves on to the next one
    mod = load_fbtodo()
    set_knob(mod, "TASKS_PATH", os.path.join(TEST_HOME, "tasks-unit.json"))

    def seq(a, b, c):
        return [
            {"task": "a", "completed": a},
            {"task": "b", "completed": b},
            {"task": "c", "completed": c},
        ]

    t0 = 10_000_000
    S = {"session": "S"}
    q1 = mod.track_tasks(dict(S, todos=seq(False, False, False)), now_ms=t0)
    assert q1["task_times"]["a"]["started_ms"] == t0, q1["task_times"]
    assert q1["task_times"]["a"]["elapsed_ms"] == 0, q1["task_times"]
    assert q1["task_times"]["b"]["started_ms"] is None, "a waiting step was given a clock"
    q2 = mod.track_tasks(dict(S, todos=seq(False, False, False)), now_ms=t0 + 90_000)
    assert q2["task_times"]["a"]["elapsed_ms"] == 90_000, q2["task_times"]
    assert q2["task_times"]["b"]["elapsed_ms"] is None, "a waiting step billed someone else's time"
    assert mod.has_running_clock(q2, t0 + 90_000), "a counting clock must make the pane tick"
    q3 = mod.track_tasks(dict(S, todos=seq(True, False, False)), now_ms=t0 + 120_000)
    assert q3["task_times"]["a"]["elapsed_ms"] == 120_000, q3["task_times"]
    assert q3["task_times"]["b"]["started_ms"] == t0 + 120_000, "the clock did not follow the work"
    q4 = mod.track_tasks(dict(S, todos=seq(True, True, False)), now_ms=t0 + 300_000)
    assert q4["task_times"]["a"]["elapsed_ms"] == 120_000, "a done task's clock kept running"
    assert q4["task_times"]["b"]["elapsed_ms"] == 180_000, q4["task_times"]
    # the pane's own tick moves a running number, between watcher writes
    assert mod.live_elapsed(q4["task_times"]["c"], t0 + 340_000) == 40_000
    running = ansi.sub("", module.render(q4, False, width=56, now_ms=t0 + 340_000))
    # a clock keeps its seconds past the minute, so 2m does not read as "2m"
    assert "· 2m00s" in running and "· 3m00s" in running and "· 40s…" in running, running
    assert mod.short_duration(150_000, seconds=True) == "2m30s"
    assert mod.short_duration(150_000) == "2m", "the coarse form changed for ages and idle"
    assert mod.short_duration(60_000, seconds=True) == "1m00s"
    assert mod.short_duration(3_723_000, seconds=True) == "1h02m", "seconds above an hour"
    q5 = mod.track_tasks(dict(S, todos=seq(True, True, True)), now_ms=t0 + 400_000)
    assert q5["task_times"]["c"]["elapsed_ms"] == 100_000, q5["task_times"]
    assert not mod.has_running_clock(q5, t0 + 500_000), "a finished list still ticks"
    # each step carries its own span, and together they are the list's span
    assert sum(v["elapsed_ms"] for v in q5["task_times"].values()) == 400_000, q5["task_times"]
    assert mod.short_duration(0) == "", "a zero duration must not render as empty text"
    assert "· 1m40s" in ansi.sub("", module.render(q5, False, width=56, now_ms=t0 + 400_000))
    # a step never seen running has no number at all, rather than a zero
    never = mod.track_tasks(
        {"session": "N", "todos": [{"task": "x", "completed": True}]}, now_ms=t0
    )
    assert never["task_times"]["x"]["elapsed_ms"] is None, never["task_times"]
    assert "x · " not in ansi.sub("", module.render(never, False, width=56, now_ms=t0))
    # a list that momentarily reads as empty is not a reason to throw the clocks away
    mod.track_tasks({"session": "S", "todos": []}, now_ms=t0 + 401_000)
    back = mod.track_tasks(dict(S, todos=seq(True, True, True)), now_ms=t0 + 402_000)
    assert back["task_times"]["a"]["elapsed_ms"] == 120_000, "an empty poll wiped the clocks"
    # nor is a thread flip: records are keyed per session, so going back restores them
    mod.track_tasks(dict(S, todos=seq(False, False, False), session="OTHER"), now_ms=t0 + 500_000)
    restored = mod.track_tasks(dict(S, todos=seq(True, True, True)), now_ms=t0 + 600_000)
    assert restored["task_times"]["a"]["elapsed_ms"] == 120_000, "a session flip wiped the clocks"
    assert mod.track_tasks(
        dict(S, todos=seq(True, True, False), session="OTHER"), now_ms=t0 + 500_000
    )["task_times"]["a"]["started_ms"] == t0 + 500_000, "another session's clock was adopted"
    # re-opening a finished step restarts its clock
    reopened = mod.track_tasks(dict(S, todos=seq(True, True, False)), now_ms=t0 + 700_000)
    assert reopened["task_times"]["c"]["started_ms"] == t0 + 700_000, reopened["task_times"]
    # ---- but a record that FINISHED is evidence, and evidence is not dropped because the list
    # moved on. Measured live 2026-09-29: a rewritten list deleted the one step whose forecast
    # was the only scored row in the log, so `estimate error` and `forecast error` could never
    # show more than the current list. A record with no `done_ms` still goes: its `started_ms`
    # would keep a clock running for a step that is on no list any more.
    keep = mod.load_tasklog()
    keep["tasks"][mod.task_key("KEEP", "gone but finished")] = {
        "started_ms": t0, "done_ms": t0 + 60_000, "model": "m/e",
        "fc": {"at": t0, "v": "0", "pace": 60_000}}
    keep["tasks"][mod.task_key("KEEP", "gone and unfinished")] = {
        "started_ms": t0, "model": "m/e"}
    put_tasklog(mod, keep)
    mod.track_tasks(
        {"session": "KEEP", "todos": [{"task": "now", "completed": False}]},
        now_ms=t0 + 120_000)
    left = mod.load_tasklog()["tasks"]
    assert mod.task_key("KEEP", "gone but finished") in left, "a measured span was thrown away"
    assert "fc" in left[mod.task_key("KEEP", "gone but finished")], "its forecast went with it"
    assert mod.task_key("KEEP", "gone and unfinished") not in left, "a stale clock survived"
    # ---- a turn that ENDS with a step still current must stop counting. Reported 2026-09-25:
    # a finished list sat at 9/10 with the last step unticked, the turn ended, and the pane
    # kept saying WORKING with a number that grew past the estimate — because the clock was
    # only ever closed by a tick, and nothing was going to tick it.
    W = {"session": "W"}
    w1 = mod.track_tasks(dict(W, todos=seq(True, False, False)), now_ms=t0 + 800_000)
    assert mod.live_clock(w1["task_times"]["b"]), "the working step has no live clock"
    w2 = mod.track_tasks(
        dict(W, todos=seq(True, False, False), turn_ended=True), now_ms=t0 + 900_000
    )
    assert not mod.live_clock(w2["task_times"]["b"]), (
        f"the turn ended and the clock is still counting: {w2['task_times']['b']}"
    )
    assert w2["task_times"]["b"]["elapsed_ms"] == 100_000, (
        f"the span worked before the turn ended was thrown away: {w2['task_times']['b']}"
    )
    # ...and it must not resume counting by itself on the next poll
    w3 = mod.track_tasks(
        dict(W, todos=seq(True, False, False), turn_ended=True), now_ms=t0 + 1_500_000
    )
    assert w3["task_times"]["b"]["elapsed_ms"] == 100_000, (
        f"an ended turn kept billing the idle gap: {w3['task_times']['b']}"
    )
    assert not mod.has_running_clock(w3, t0 + 1_500_000), "an ended turn still ticks at 1Hz"
    # and the strip says so, in both renderers: the number is a measurement, not work
    ended_state = dict(w3, source_updated_ms=t0 + 900_000, list_version=7)
    framed = ansi.sub("", mod.render(ended_state, True, width=80, now_ms=t0 + 1_500_000))
    plain = ansi.sub("", mod.render(ended_state, False, width=80, now_ms=t0 + 1_500_000))
    assert "WORKING" not in framed, f"the framed pane still says WORKING: {framed}"
    assert "IDLE" in framed, framed
    assert "working" not in plain, f"the plain render still says working: {plain}"
    # the agent coming back to that step restarts its clock — from then, not from before
    w4 = mod.track_tasks(dict(W, todos=seq(True, False, False)), now_ms=t0 + 1_600_000)
    assert mod.live_clock(w4["task_times"]["b"]), "a resumed turn did not restart the clock"
    assert w4["task_times"]["b"]["started_ms"] == t0 + 1_600_000, (
        f"the resumed clock inherited the idle gap: {w4['task_times']['b']}"
    )
    say("a turn that ends mid-list stops the clock, keeps the span, and restarts on resume: ok")
    # A record the OLD rule left behind — started, never done, on a list whose turn has
    # ended — is what every state file on disk held when this was fixed. The pane's next
    # paint has to call that idle on its own, before any watcher pass has rewritten the log.
    stale = {
        "session": "S", "todos": seq(True, False, False), "turn_ended": True,
        "list_version": 8, "source_updated_ms": t0 + 100_000,
        "task_times": {"a": {"started_ms": t0, "done_ms": t0 + 40_000, "elapsed_ms": 40_000},
                       "b": {"started_ms": t0 + 100_000, "done_ms": None, "elapsed_ms": None}},
    }
    stale_framed = ansi.sub("", mod.render(stale, True, width=80, now_ms=t0 + 3_000_000))
    assert "WORKING" not in stale_framed, (
        f"a clock left open by the old rule still reads as working: {stale_framed}"
    )
    assert "IDLE" in stale_framed, stale_framed
    say("a clock left open on disk by the old rule renders as idle, not working: ok")
    # what a step took is remembered ACROSS sessions; since the `own wording` rung was
    # retired the map feeds the PACE rather than pricing a step directly, so a remembered
    # name no longer changes its own step's number
    assert mod.hist_med(reopened["task_history"]["a"]) == 120_000, reopened["task_history"]
    assert mod.hist_med(reopened["task_history"]["b"]) == 180_000, reopened["task_history"]
    assert mod.task_estimate_ms("a", 90_000) == 90_000
    assert mod.task_estimate_ms("never seen", 90_000) == 90_000
    # with nothing finished in this list the remembered pace stands in for the default
    assert mod.step_pace_ms(
        {}, [{"task": "z", "completed": False}], t0, reopened["task_history"]
    ) == 150_000  # median(120s, 180s)
    assert mod.step_pace_ms({}, [{"task": "z", "completed": False}], t0) == \
        mod.DEFAULT_PACE_MS, "a session with no past lost its default pace"
    # a stale wording falls out of the memory instead of skewing a pace it is never asked
    # about: a record outside the retention window contributes nothing
    # expressed against the retention window itself rather than a hardcoded week: the claim
    # is "a record outside the window contributes nothing", and it has to keep holding
    # whichever way the window is set (it was 7 days until 2026-09-29)
    day = 86400 * 1000
    later = t0 + int(mod.HISTORY_MAX_AGE_DAYS + 1) * day
    aged = mod.task_history_from_log(
        {
            mod.task_key("S", "ancient"): {"started_ms": t0, "done_ms": t0 + 600_000},
            mod.task_key("S", "fresh"): {"started_ms": later - 60_000, "done_ms": later},
        },
        later,
    )
    assert "ancient" not in aged and mod.hist_med(aged.get("fresh")) == 60_000, aged
    # ...and the map is capped to the most recently seen names, so the fallback median is
    # not diluted by a long tail of one-off titles
    many = mod.task_history_from_log(
        {
            mod.task_key("S", f"step {n}"): {
                "started_ms": later - mod.MIN_LABEL_MS - n, "done_ms": later,
            }
            for n in range(mod.HISTORY_MAX_ENTRIES + 5)
        },
        later,
    )
    assert len(many) == mod.HISTORY_MAX_ENTRIES, len(many)
    # a log written before the (session, task) change still resolves its records
    flat = os.path.join(TEST_HOME, "tasks-flat.json")
    set_knob(mod, "TASKS_PATH", flat)
    put_tasklog(mod, {
        "schema": 1, "session": "S", "tasks": {"a": {"started_ms": t0, "done_ms": t0 + 5000}},
    })
    legacy = mod.track_tasks(dict(S, todos=[{"task": "a", "completed": True}]), now_ms=t0 + 60_000)
    assert legacy["task_times"]["a"]["elapsed_ms"] == 5000, "an upgrade threw the clocks away"
    assert mod.task_key(None, "a") != mod.task_key("S", "a"), "records are not session-scoped"

    # ---- a step's SHAPE: what it DID, taken from the turn's own call tally and shared out
    #      between the steps of that turn BY ORDER. Step one is credited first, the step in
    #      flight takes what is left, and a finished step keeps what it earned — so no call
    #      is counted twice, none is lost, and no timestamp is needed to tell them apart.
    def hseq(a, b, c):
        return [
            {"task": "h-one", "completed": a},
            {"task": "h-two", "completed": b},
            {"task": "h-three", "completed": c},
        ]

    def hturn(verbs, start):
        return {"turn": {"start_ms": start, "verbs": verbs}}

    H = {"session": "H", "model": "m/h"}
    k1 = mod.track_tasks(
        dict(H, todos=hseq(False, False, False), **hturn({"edited": 2}, t0)), now_ms=t0)
    assert k1["task_times"]["h-one"]["shape"] == {"edited": 2}, k1["task_times"]
    # the first step is ticked: two edits are ITS shape, and the step now in flight starts
    # from nothing rather than inheriting them
    k2 = mod.track_tasks(
        dict(H, todos=hseq(True, False, False), **hturn({"edited": 2, "ran": 1}, t0)),
        now_ms=t0 + 10_000)
    assert k2["task_times"]["h-one"]["shape"] == {"edited": 2}, k2["task_times"]
    assert k2["task_times"]["h-two"]["shape"] == {"ran": 1}, k2["task_times"]
    assert mod.shape_of(k2["task_times"]["h-one"]["shape"]) == "calls1"
    assert mod.shape_of(k2["task_times"]["h-two"]["shape"]) == "calls0"
    # the finished step's size is remembered with its span; the one still running has no
    # span yet, so it contributes nothing to the memory it will later feed
    mem = mod.shape_history_from_log(mod.load_tasklog()["tasks"], t0 + 10_000, "m/h")
    assert mem == {"calls1": {"med": 10_000, "n": 1}}, mem
    # a NEW turn brings its own tally, and the previous turn's steps are not credited
    # against it — otherwise a call made before the request would be billed to the work
    # the request started
    k3 = mod.track_tasks(
        dict(H, todos=hseq(True, True, False), **hturn({"edited": 1}, t0 + 20_000)),
        now_ms=t0 + 30_000)
    assert k3["task_times"]["h-three"]["shape"] == {"edited": 1}, k3["task_times"]
    assert k3["task_times"]["h-two"]["shape"] == {"ran": 1}, "a finished shape moved"
    # ...and a turn with no tally at all (a session fbtodo attached to mid-flight) leaves
    # the step's shape as it stood rather than blanking it: it is the same step, and the
    # calls it already made were still made
    k4 = mod.track_tasks(dict(H, todos=hseq(True, True, False)), now_ms=t0 + 31_000)
    assert k4["task_times"]["h-three"]["shape"] == {"edited": 1}, k4["task_times"]
    # a step the pane can project from what it is DOING: sixteen and seventeen calls are the
    # same SIZE (`calls4`), so two such steps at 10s and 30s set that size's number at 20s —
    # where the list's own pace says 4m
    shapes = mod.shape_history_from_log({
        mod.task_key("H", "x"): {"started_ms": t0, "done_ms": t0 + 10_000,
                                 "model": "m/h", "shape": {"edited": 16}},
        mod.task_key("H", "y"): {"started_ms": t0, "done_ms": t0 + 30_000,
                                 "model": "m/h", "shape": {"edited": 17}},
    }, t0 + 60_000, "m/h")
    assert shapes == {"calls4": {"med": 20_000, "n": 2, "lo": 10_000, "hi": 30_000}}, shapes
    own = {"h-three": {"started_ms": t0, "done_ms": None, "shape": {"edited": 16}}}
    assert mod.estimate_for(own, "h-three", 240_000, shapes) == 20_000
    # one step of that size is not evidence yet, and neither is a size nobody has seen
    assert mod.estimate_for(own, "h-three", 240_000, {"calls4": {"med": 5_000, "n": 1}}) \
        == 240_000
    assert mod.estimate_for({}, "nothing", 240_000, shapes) == 240_000
    # ...and the same step while it is still small is priced at the pace, not at the memory
    # of the steps that stopped that small
    young = {"h-three": {"started_ms": t0, "done_ms": None, "shape": {"edited": 4}}}
    assert mod.estimate_for(young, "h-three", 240_000,
                            {"calls2": {"med": 20_000, "n": 2}}) == 240_000

    # ---- the estimate the pane is SHOWING is stamped on the step in flight, so that when
    #      that step closes its projection and its outcome are a matched pair in the log.
    #      Stamped from the same ladder the renderers use, and asserted against that same
    #      call: a stamp that drifts from what the pane showed would measure nothing.
    E = {"session": "E", "model": "m/e"}
    # two remembered `calls4` steps for this model, written into the log the watcher reads,
    # so the stamp below is taken through the whole live path and not from a local fixture
    e_log = mod.load_tasklog()
    for n, span in enumerate((10_000, 30_000)):
        e_log["tasks"][mod.task_key("SEED", f"seed {n}")] = {
            "started_ms": t0, "done_ms": t0 + span, "model": "m/e", "shape": {"edited": 16},
        }
    put_tasklog(mod, e_log)
    e_state = mod.track_tasks(
        dict(E, todos=[{"task": "e-one", "completed": False}], **hturn({"edited": 17}, t0)),
        now_ms=t0 + 300_000)
    e_rec = mod.load_tasklog()["tasks"][mod.task_key("E", "e-one")]
    e_pace = mod.step_pace_ms(e_state["task_times"], e_state["todos"], t0 + 300_000,
                              e_state["task_history"])
    assert e_rec["est_ms"] == mod.estimate_for(
        e_state["task_times"], "e-one", e_pace,
        e_state["task_shapes"]) == 20_000, e_rec
    # ...and the source is named, so the error report knows which rung of the ladder missed:
    # two remembered `calls4` steps at 10s and 30s stand behind this one
    assert e_rec["est_src"] == "shape", e_rec
    # ...and the first-poll ledger rode along on the same record, with the rungs it saw then
    assert isinstance(e_rec.get("fc"), dict), e_rec
    assert e_rec["fc"].get("pace") and e_rec["fc"].get("v") == mod.VERSION, e_rec["fc"]
    assert mod.pick_estimate("anything", {}, "") == (None, "pace")
    assert mod.pick_estimate("e-one", shapes, "calls4") == (20_000, "shape")
    # the retired `own` rung is never a source any more: only size, blend and pace remain
    assert mod.pick_estimate("e-one", shapes, "calls9") == (None, "pace")

    # ---- did the estimates get better? The pair (projection, outcome) is in the records, so
    #      the answer is computed from them per source rather than kept in a tally of its own
    errt = {
        mod.task_key("E", "over"): {"started_ms": t0, "done_ms": t0 + 100_000,
                                     "est_ms": 200_000, "est_src": "pace", "model": "m/e"},
        mod.task_key("E", "under"): {"started_ms": t0, "done_ms": t0 + 200_000,
                                      "est_ms": 100_000, "est_src": "pace", "model": "m/e"},
        mod.task_key("E", "exact"): {"started_ms": t0, "done_ms": t0 + 100_000,
                                      "est_ms": 100_000, "est_src": "shape", "model": "m/e"},
        # a step that closed before this build stamped anything contributes nothing
        mod.task_key("E", "unstamped"): {"started_ms": t0, "done_ms": t0 + 100_000,
                                          "model": "m/e"},
        # ...and neither does another model's work
        mod.task_key("E", "elsewhere"): {"started_ms": t0, "done_ms": t0 + 100_000,
                                          "est_ms": 9_000_000, "est_src": "pace", "model": "z"},
    }
    err = mod.estimate_error(errt, t0 + 1000, "m/e")
    # two 2x misses either side of the true value, counted the same, and one exact hit
    assert err["pace"] == {"n": 2, "med": 2.0, "mean": 2.0, "worst": 2.0}, err
    assert err["shape"] == {"n": 1, "med": 1.0, "mean": 1.0, "worst": 1.0}, err
    assert "elsewhere" not in str(err) and err.get("z") is None, err
    assert mod.estimate_error({}, t0) == {}
    say("estimates: the projection is stamped, and scored when the step closes: ok")

    # ---- the forecast LEDGER: the vector written on the FIRST poll that saw a step running,
    #      when nothing about its size was known yet — the only score that cannot flatter a
    #      rung which recognises rather than predicts. Every rung is scored on every step.
    ft = {
        mod.task_key("E", "a"): {"started_ms": t0, "done_ms": t0 + 100_000, "model": "m/e",
                                 "fc": {"shape": 50_000, "blend": 200_000, "pace": 100_000}},
        mod.task_key("E", "b"): {"started_ms": t0, "done_ms": t0 + 100_000, "model": "m/e",
                                 "fc": {"pace": 200_000}},
        # a step the watcher never saw start has no vector, and is not scored
        mod.task_key("E", "c"): {"started_ms": t0, "done_ms": t0 + 100_000, "model": "m/e"},
    }
    fe = mod.forecast_error(ft, t0 + 1000, "m/e")
    assert fe["pace"] == {"n": 2, "med": 1.5, "mean": 1.5, "worst": 2.0,
                         "lo": 1.0, "hi": 2.0}, fe
    assert fe["shape"] == {"n": 1, "med": 2.0, "mean": 2.0, "worst": 2.0,
                           "lo": 2.0, "hi": 2.0}, fe
    assert fe["blend"] == {"n": 1, "med": 2.0, "mean": 2.0, "worst": 2.0,
                           "lo": 2.0, "hi": 2.0}, fe
    assert mod.forecast_error({}, t0) == {}
    assert "elsewhere" not in str(mod.forecast_error(ft, t0 + 1000, "m/z")), "model-filtered"
    # ...and a vector stamped after the step had ALREADY been running is not a forecast. That
    # is what a watcher restarting mid-step produces, and scoring it would credit whichever
    # rung happens to read elapsed with the answer's own clock. Recorded, flagged, left out.
    late_log = mod.load_tasklog()
    late_log["tasks"][mod.task_key("E", "late-one")] = {"started_ms": t0, "model": "m/e"}
    put_tasklog(mod, late_log)
    mod.track_tasks(
        dict(E, todos=[{"task": "late-one", "completed": False}], **hturn({"edited": 5}, t0)),
        now_ms=t0 + 300_000)
    l_rec = mod.load_tasklog()["tasks"][mod.task_key("E", "late-one")]
    assert l_rec["fc"].get("late") == 300_000, l_rec["fc"]
    # ...and once flagged it changes no rung's score — only the count of what was set aside
    ft_late = dict(ft)
    ft_late[mod.task_key("E", "late-row")] = {
        "started_ms": t0, "done_ms": t0 + 100_000, "model": "m/e",
        "fc": {"pace": 100_000, "late": 300_000}}
    fe_late = mod.forecast_error(ft_late, t0 + 1000, "m/e")
    assert fe_late["pace"] == fe["pace"] and fe_late["shape"] == fe["shape"], fe_late
    assert fe_late["late"] == {"n": 1, "med": 0.0, "mean": 0.0, "worst": 0.0}, fe_late
    say("estimates: the first-poll forecast is kept and scored per rung: ok")

    # ...and a rung carries the SPREAD of its misses, not only the typical one: a median of
    # ×1.1 off a tight distribution and the same median off a coin toss are different
    # predictors, and `lo`/`hi` is what tells them apart. The tails are dropped rather than
    # reported as the whole story — that is what `worst` is for — and the quantile is a
    # nearest-rank pick, because with eleven steps an interpolated one would be arithmetic on
    # nothing.
    assert (fe["pace"]["lo"], fe["pace"]["hi"]) == (1.0, 2.0), fe   # the two ratios, half each
    assert (fe["shape"]["lo"], fe["shape"]["hi"]) == (2.0, 2.0), fe  # one sample: no spread
    spread_ft = {
        mod.task_key("E", f"s{n}"): {
            "started_ms": t0, "done_ms": t0 + 100_000, "model": "m/e",
            "fc": {"pace": 100_000 * (n + 1) if n < 9 else 100_000},
        }
        for n in range(10)
    }
    span_fe = mod.forecast_error(spread_ft, t0 + 1000, "m/e")
    assert span_fe["pace"]["n"] == 10 and span_fe["pace"]["lo"] < span_fe["pace"]["med"], span_fe
    assert span_fe["pace"]["hi"] > span_fe["pace"]["med"], span_fe
    assert "lo" not in span_fe.get("late", {}), "the count of set-aside rows is not a spread"

    # ---- the verdict: does one rung really beat another? Both are scored on the SAME steps
    #      (paired, so a rung cannot win by being asked an easier set of them), and the
    #      resampling is over SESSIONS rather than steps — the steps inside one session are not
    #      independent, and resampling them separately would treat one session that ran long
    #      steps as twelve pieces of evidence. 0.5 is a coin toss; 0.98 is a verdict.
    duel_log = {}
    for sess, factor in (("S1", 1.0), ("S2", 1.0), ("S3", 1.0), ("S4", 1.0), ("S5", 1.0),
                         ("S6", 1.0)):
        for n in range(4):
            duel_log[mod.task_key(sess, f"step {n}")] = {
                "started_ms": t0, "done_ms": t0 + 100_000, "model": "m/e",
                "fc": {"pace": 100_000, "blend": int(100_000 * 3 * factor)},
            }
    rows = mod.ledger_rows(duel_log, t0 + 1000, None, "m/e")
    duel = mod.rung_duel(rows, "pace", "blend")
    assert duel["steps"] == 24 and duel["sessions"] == 6, duel
    assert duel["share_steps"] == 1.0 and duel["p"] <= 0.05, duel
    assert duel["winner"] == "pace" and duel["flips"] == 64 and duel["exact"], duel
    assert duel["effect"] < 0, duel
    # ...and the exact sign test has no seed: the same rows give the same p and winner twice
    assert mod.rung_duel(rows, "pace", "blend") == duel, "the duel is not reproducible"
    # ...and a rung nobody has scored is not a winner by default: no pairing, no verdict
    odd = mod.rung_duel(rows, "pace", "shape")
    assert odd["steps"] == 0 and odd["winner"] is None, odd
    # a coin toss reads as one: the intervals overlap and the shares sit near a half. The split
    # is written out rather than drawn, because the point of THIS check is that a null result
    # is reported as a null result — a fixture that hashed its way to 12/4 across four sessions
    # would be testing the hash, and would fail once in every thirteen runs.
    tie_log = {}
    for sess, wins in (("S1", 3), ("S2", 1), ("S3", 3), ("S4", 1), ("S5", 3), ("S6", 1)):
        for n in range(4):
            tie_log[mod.task_key(sess, f"step {n}")] = {
                "started_ms": t0, "done_ms": t0 + 100_000, "model": "m/e",
                "fc": {"pace": 100_000 if n < wins else 200_000, "blend": 150_000},
            }
    tie_rows = mod.ledger_rows(tie_log, t0 + 1000, None, "m/e")
    tied = mod.rung_duel(tie_rows, "pace", "blend")
    assert tied["steps"] == 24 and tied["share_steps"] == 0.5, tied
    assert tied["winner"] is None, tied
    assert tied["p"] > 0.2, tied
    # ...and the exact count is meet-in-the-middle now: two halves of signed sums, one sorted
    #     and searched, instead of a walk over all 2^n. The walk is kept here — written out the
    #     way it was — and both counts run over the same logs, six sessions up to twenty. The
    #     ceiling moved with the method (`DUEL_EXACT_MAX` 20 -> 40); the numbers did not. A
    #     count one low would move a p, and at the floor a p moves a verdict.
    def walk_duel(rows, a, b):
        """The `2^n` enumeration `rung_duel` used to run, as the reference to check against."""
        pairs = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            errs = row.get("errors") or {}
            ea, eb = errs.get(a), errs.get(b)
            if ea is None or eb is None or a == b:
                continue
            pairs.append((row.get("session") or "", ea, eb))
        by_session = {}
        for session, ea, eb in pairs:
            by_session.setdefault(session, []).append((ea, eb))
        diffs = []
        for session in sorted(by_session):
            ds = [math.log(ea) - math.log(eb) for ea, eb in by_session[session]
                  if ea > 0 and eb > 0]
            if ds:
                diffs.append(sum(ds) / len(ds))
        n = len(diffs)
        observed = sum(diffs) / n
        compare = abs(observed) * n - 1e-9
        flips, extreme = 1 << n, 0
        for bits in range(flips):
            stat = 0.0
            for i, d in enumerate(diffs):
                stat += d if (bits >> i) & 1 else -d
            if abs(stat) >= compare:
                extreme += 1
        p = extreme / flips
        winner = None
        if n >= mod.DUEL_MIN_SESSIONS and p <= 1 - mod.DUEL_RESOLVED and observed != 0:
            winner = a if observed < 0 else b
        return {"p": round(p, 4), "flips": flips, "winner": winner,
                "diffs": diffs, "compare": compare, "extreme": extreme}

    for n_sessions in (6, 9, 12, 17, 20):
        walk_log = {}
        for s in range(n_sessions):
            # a deterministic mix, not a uniform effect: some sessions favour one rung and some
            # the other, by different factors, so the count cannot be right by symmetry alone
            factor = (2, 3, 0.5, 4)[s % 4]
            for k in range(3):
                walk_log[mod.task_key(f"W{s}", f"step {k}")] = {
                    "started_ms": t0, "done_ms": t0 + 100_000, "model": "m/e",
                    "fc": {"pace": 100_000, "blend": int(100_000 * factor)},
                }
        walk_rows = mod.ledger_rows(walk_log, t0 + 1000, None, "m/e")
        want = walk_duel(walk_rows, "pace", "blend")
        got = mod.rung_duel(walk_rows, "pace", "blend")
        assert len(want["diffs"]) == n_sessions, (n_sessions, len(want["diffs"]))
        assert got["flips"] == want["flips"] and got["p"] == want["p"], (n_sessions, got, want)
        assert got["winner"] == want["winner"], (n_sessions, got, want)
        # the counts themselves rather than the rounded share: one assignment in a million
        # rounds away to the same four places
        assert mod.tasks._sign_flip_extreme(want["diffs"], want["compare"]) == want["extreme"], \
            (n_sessions, "the meet-in-the-middle count differs from the walk")
    assert mod.rung_duel([], "pace", "blend")["steps"] == 0
    say("estimates: a rung's spread is reported, and a duel over sessions resolves it: ok")

    # ---- `fbtodo ledger` prints the rows behind that scoreboard: each rung's prediction
    #      beside the span it was scored against, and — the half a bare scoreboard cannot
    #      carry — why a row that could not be scored is not a miss.
    lr = mod.ledger_rows(ft_late, t0 + 1000, None, "m/e")
    assert [r["label"] for r in lr] == ["a", "b", "late-row"], lr
    assert lr[0]["errors"] == {"shape": 2.0, "blend": 2.0, "pace": 1.0}, lr[0]
    assert lr[1]["errors"] == {"pace": 2.0}, lr[1]     # only a rung that predicted is scored
    assert lr[2]["scored"] is False and "late" in lr[2]["why"], lr[2]
    assert mod.ledger_rows(ft_late, t0 + 1000, None, "m/z") == [], "model-filtered"
    assert mod.ledger_rows(ft, t0 + 30 * 86_400_000, 7.0) == [], "outside the age window"
    # a step too short to be evidence is shown and NOT scored, with the reason on the row
    tiny = mod.ledger_rows({mod.task_key("E", "flip"): {
        "started_ms": t0, "done_ms": t0 + 2_000, "model": "m/e",
        "fc": {"at": t0 + 500, "v": "9.9.9", "pace": 100_000}}}, t0 + 1000, None, "m/e")
    assert tiny[0]["scored"] is False and "floor" in tiny[0]["why"], tiny[0]
    # a step still in flight has a vector and no outcome yet, and says exactly that
    inflight = mod.ledger_rows({mod.task_key("E", "going"): {
        "started_ms": t0, "model": "m/e",
        "fc": {"at": t0, "v": "9.9.9", "pace": 100_000, "pick": "pace"}}},
        t0 + 1000, None, "m/e")
    assert inflight[0]["span_ms"] is None and inflight[0]["why"] == "no outcome yet", inflight[0]
    # ...and a vector written BEFORE the run it is scored against began (a step ticked,
    # unticked, then worked again restarts its clock but keeps its first vector) reads as
    # what it is rather than as a stamp that somehow precedes the work
    pre = mod.ledger_rows({mod.task_key("E", "pre"): {
        "started_ms": t0 + 60_000, "done_ms": t0 + 200_000, "model": "m/e",
        "fc": {"at": t0, "v": "9.9.9", "pace": 100_000}}}, t0 + 1000, None, "m/e")
    assert pre[0]["stamp_in_ms"] == -60_000, pre[0]
    text = mod.fmt_ledger(pre, t0 + 1000, None, 0)
    assert "stamped 1m00s before this run" in text, text
    assert "pace 1m40s ×1.40" in text, text
    # ...while the ordinary case — the poll that started the clock wrote the vector on the
    # same pass — reads as prose rather than as an empty duration ("stamped  in")
    same = mod.ledger_rows({mod.task_key("E", "same"): {
        "started_ms": t0, "model": "m/e",
        "fc": {"at": t0, "v": "9.9.9", "pace": 100_000, "pick": "pace"}}},
        t0 + 1000, None, "m/e")
    assert same[0]["stamp_in_ms"] == 0, same[0]
    assert "stamped on the first poll" in mod.fmt_ledger(same, t0 + 1000, None, 0)
    # the report counts what it set aside, and the tail names the way to widen it
    text = mod.fmt_ledger(lr, t0 + 1000, None, 2)
    assert "1 stamped late (not scored)" in text, text
    assert "1 more (--limit 0 for all" in text, text
    assert "no step carries a forecast yet" in mod.fmt_ledger([], t0, None, 20)
    say("estimates: `fbtodo ledger` prints the vector beside the outcome: ok")

    # ---- and the verdict is not a number a reader has to take on faith: `doctor` prints the
    #      pair, who won, how lopsided the draws were and the sample they came from, and
    #      `fbtodo ledger` prints the same sentence over the rows underneath it. A pair nobody
    #      has scored is left out rather than printed as a draw.
    note = mod.duel_note("pace", "blend", duel)
    assert note == ("pace beats blend — p=0.03 over 64 sign flips, 24 step(s) in 6 session(s)"), note
    assert mod.duel_note("pace", "blend", tied).startswith("pace vs blend — unresolved, "), note
    assert mod.duel_note("pace", "blend", tied).endswith("24 step(s) in 6 session(s)"), note
    assert "64 sign flips" in note and mod.DUEL_EXACT_MAX == 40, note
    # ...and a bootstrap over ONE session cannot resample anything: every draw is the sample
    # it started from, so the share comes back at 1.0 and would crown whichever rung led by
    # an accident of one session. Below the floor the duel reports the sample, not a winner.
    one = mod.rung_duel(mod.ledger_rows(
        {k: v for k, v in duel_log.items() if k.startswith("S1\x1f")}, t0 + 1000, None, "m/e"),
        "pace", "blend")
    assert one["steps"] == 4 and one["sessions"] == 1 and one["winner"] is None, one
    assert f"needs {mod.DUEL_MIN_SESSIONS} session(s)" in mod.duel_note("pace", "blend", one), one
    # the ledger carries the sentence, once, and only for a pair that was scored
    led = mod.fmt_ledger(rows, t0 + 1000, None, 0)
    assert note in led, led
    assert led.count("rung duel") == 1, led
    only_pace = mod.ledger_rows({mod.task_key("P", "one"): {
        "started_ms": t0, "done_ms": t0 + 100_000, "model": "m/e", "fc": {"pace": 100_000}}},
        t0 + 1000, None, "m/e")
    assert "rung duel" not in mod.fmt_ledger(only_pace, t0 + 1000, None, 0)
    # ...and the surface a reader actually runs: a real `status` over a real log of its own,
    # where the interval beside the median has to appear and the duel has to name its winner.
    # Its own FBTODO_HOME, because the task log is read from a path decided at import and this
    # check is about what the command prints, not about what the suite's log happens to hold.
    duel_home = os.path.join(TEST_HOME, "duel-home")
    shutil.rmtree(duel_home, ignore_errors=True)
    os.makedirs(duel_home, exist_ok=True)
    # a wall-clock base rather than the synthetic `t0` this block scores against: the child is
    # the real program, and its log is read inside a 60-day window, so a 1970 fixture is
    # correctly invisible to it — which is exactly how this check first failed.
    fresh = int(time.time() * 1000)
    duel_log_rows = {}
    for sess in ("D1", "D2", "D3", "D4", "D5", "D6"):
        for n in range(4):
            duel_log_rows[mod.task_key(sess, f"step {n}")] = {
                "started_ms": fresh - 100_000, "done_ms": fresh, "model": "m/e",
                "fc": {"at": fresh - 100_000, "v": "9.9.9",
                       "pace": 100_000 * (n + 1), "blend": 500_000,
                       "recent": 60_000 + 20_000 * n},
            }
    # the file name because `mod`'s own TASKS_PATH is pointed at the suite's unit fixture,
    # and the child decides its path at import — the same reason the status-home checks above
    # name the file rather than reading it off the module
    with open(os.path.join(duel_home, "fbtodo-tasks.json"), "w") as fh:
        json.dump({"schema": mod.TASKLOG_SCHEMA, "tasks": duel_log_rows}, fh)
    sproc = subprocess.run(
        [sys.executable, FB, "status"], capture_output=True, text=True, cwd=CWD,
        env=dict(env, FBTODO_HOME=duel_home), timeout=90,
    )
    sout = sproc.stdout
    assert "task records      : 24 kept" in sout, sout[-800:]
    # the spread of the pace rung — ratios 1..4, so the 10th percentile is its own sample's
    # smallest and the 90th its largest — printed beside the median it belongs to, while a
    # rung whose every sample is the same ratio prints no bracket at all
    assert "[1.00–4.00]" in sout, sout[-800:]
    assert "blend 5.00x over 24" in sout, sout[-800:]
    assert "rung duel         : pace beats blend" in sout, sout[-800:]
    # ...and the rung that is scored but never picked: its own line, so a reader can see that
    # the pane is carrying an experiment and that nothing about it moves a number on screen
    # the score is a symmetric factor, so a rung predicting half the span is as wrong as one
    # predicting double: 60s/80s/100s/120s against a 100s span read 1.67/1.25/1.00/1.20
    assert "shadow rungs      : recent 1.23x [1.00–1.67] over 24" in sout, sout[-800:]
    assert "(scored, never picked)" in sout, sout[-800:]
    assert "rung duel         : recent beats pace" in sout, sout[-800:]
    say("estimates: `status` shows each rung's spread, and who won the duel: ok")

    # ---- and a shadow rung is scored but never picked: it rides in the same forecast vector,
    #      is judged by the same functions, and no code path may return it as an estimate. An
    #      idea has to earn its place from the log rather than from an argument, and `recent` —
    #      the list's own last few spans, the pace that has just delivered — is the one thing
    #      the shipped, whole-list pace cannot be compared against by itself.
    assert mod.SHADOW_RUNGS == ("recent",), mod.SHADOW_RUNGS
    assert "recent" not in mod.SHIPPED_RUNGS, mod.SHIPPED_RUNGS
    assert set(mod.SCORED_RUNGS) == set(mod.SHIPPED_RUNGS) | set(mod.SHADOW_RUNGS), mod.SCORED_RUNGS
    # what it is: the median of the TAIL of the list, not of the whole of it. Five spans of
    # 1m/3m/5m/7m/9m have a middle of 5m and a last-three middle of 7m, and it is the second
    # number this rung is claiming to be a better guess from.
    tail_times = {
        name: {"started_ms": t0 + i * 60_000, "elapsed_ms": span}
        for i, (name, span) in enumerate(
            (("a", 60_000), ("b", 180_000), ("c", 300_000), ("d", 420_000), ("e", 540_000)))
    }
    tail_todos = [{"task": n, "completed": True} for n in "abcde"]
    assert mod.recent_pace_ms(tail_times, tail_todos, t0 + 600_000) == 420_000, "the tail, not the middle"
    # ...and one span is that step's own time, not a pace, so there is no shadow rung to score
    assert mod.recent_pace_ms(tail_times, [{"task": "a", "completed": True}],
                              t0 + 600_000) is None
    # stamped beside the shipped rungs on the first poll that sees a step running, while the
    # pick stays one of the three the pane may actually choose
    put_tasklog(mod, {"schema": mod.TASKLOG_SCHEMA, "session": "SH", "tasks": {}})

    def four(*done):
        return [{"task": n, "completed": d} for n, d in zip("abcd", done)]

    mod.track_tasks({"session": "SH", "todos": four(False, False, False, False)}, now_ms=t0)
    mod.track_tasks({"session": "SH", "todos": four(True, False, False, False)}, now_ms=t0 + 60_000)
    sh = mod.track_tasks({"session": "SH", "todos": four(True, True, False, False)}, now_ms=t0 + 240_000)
    sh_rec = mod.load_tasklog()["tasks"][mod.task_key("SH", "c")]
    assert sh_rec["fc"]["recent"] == 120_000, sh_rec["fc"]   # a 60s step and a 180s one
    assert sh_rec["fc"]["pick"] in mod.SHIPPED_RUNGS, sh_rec["fc"]
    assert sh_rec["est_src"] in mod.SHIPPED_RUNGS, sh_rec
    assert sh["task_times"]["c"]["started_ms"] == t0 + 240_000, sh["task_times"]
    # the ledger prints it beside the shipped rungs — but only on a row that carries one, so a
    # row recorded before the rung existed is not shown a rung it never had
    shadow_rows = mod.ledger_rows({mod.task_key("SH", "shadow"): {
        "started_ms": t0, "done_ms": t0 + 100_000, "model": "m/e",
        "fc": {"at": t0, "v": "9.9.9", "pace": 100_000, "recent": 50_000}}},
        t0 + 1000, None, "m/e")
    assert shadow_rows[0]["errors"]["recent"] == 2.0, shadow_rows[0]
    assert "recent 50s ×2.00" in mod.fmt_ledger(shadow_rows, t0 + 1000, None, 0)
    plain_rows = mod.ledger_rows({mod.task_key("SH", "no shadow"): {
        "started_ms": t0, "done_ms": t0 + 100_000, "model": "m/e",
        "fc": {"at": t0, "v": "9.9.9", "pace": 100_000}}}, t0 + 1000, None, "m/e")
    assert "recent" not in mod.fmt_ledger(plain_rows, t0 + 1000, None, 0), "a rung the row never had"
    # ...and it can be dueled against the rung it would replace, which is the only reason to
    # keep its score at all
    assert mod.rung_duel(shadow_rows, "pace", "recent")["steps"] == 1, shadow_rows
    say("estimates: a shadow rung is scored beside the shipped ones and never picked: ok")

    # ---- a GENERIC source: anything that can write a state JSON can drive the pane.
    #      `fbtodo push` takes one on stdin and makes it the live state — through the same
    #      `finish_state` a watched list goes through, so its counts, its session and its list
    #      number are the tool's, not the pusher's — and `-s file:PATH` reads a state back off
    #      disk. Together they are the seam that does not go through a Freebuff at all.
    live_state = mod.STATE_PATH
    try:
        os.unlink(live_state)     # a probe with no state file is the honest starting point
    except OSError:
        pass
    pushed = {"session": "PUSH1", "goal": "pushed by hand",
              "todos": [{"task": "one", "completed": True}, {"task": "two"}]}

    def push(payload, *argv, **kw):
        body = kw.pop("body", json.dumps(payload) if payload is not None else "")
        return subprocess.run(
            [sys.executable, FB, "push", *argv], input=body, text=True, capture_output=True,
            cwd=CWD, env=env, timeout=30,
        )

    pr = push(pushed)
    assert pr.returncode == 0, (pr.returncode, pr.stderr)
    back = json.loads(pr.stdout)
    assert back["backend"] == "push" and back["source"] == "push", back
    assert back["tool_version"] == mod.VERSION and back["session"] == "PUSH1", back
    assert (back["done"], back["total"]) == (1, 2), back
    assert back["list_version"] == 1 and back["list_id"], back
    # ...and the pushed list is what the machine now reports: the readers that prefer a live
    # state file answer from it, so a hand-pushed list needs no watcher to be shown
    assert run("bar").stdout.strip() == "todos 1/2", run("bar").stdout
    assert json.loads(run("json").stdout)["session"] == "PUSH1"
    # a second push of the SAME list does not renumber it; a different list does
    again = json.loads(push(pushed).stdout)
    assert again["list_version"] == 1, again
    other = json.loads(push({"session": "PUSH1",
                             "todos": [{"task": "fresh", "completed": True}]}).stdout)
    assert other["list_version"] == 2 and other["total"] == 1, other
    # a dry run says what it would write and writes nothing: the live state is untouched
    dry = push({"session": "DRY", "todos": [{"task": "nope"}]}, "--dry-run")
    assert dry.returncode == 0 and json.loads(dry.stdout)["session"] == "DRY", dry.stdout
    assert run("bar").stdout.strip() == "todos 1/1", run("bar").stdout
    assert json.loads(run("json").stdout)["session"] == "PUSH1", run("json").stdout
    # --quiet writes the state and prints nothing at all; the readers still see it
    quiet = push({"session": "QUIET1", "todos": [{"task": "q", "completed": True}]}, "--quiet")
    assert quiet.returncode == 0 and quiet.stdout == "", (quiet.returncode, quiet.stdout)
    assert json.loads(run("json").stdout)["session"] == "QUIET1", run("json").stdout
    assert run("bar").stdout.strip() == "todos 1/1", run("bar").stdout
    # what it will not take: no JSON, not an object, no list where a list goes, nothing at all
    for bad, code in ((None, 66), ("oops", 65), ('[1, 2]', 65),
                      (json.dumps({"todos": "nope"}), 65)):
        r = push(None, body=bad if isinstance(bad, str) else "")
        assert r.returncode == code, (bad, r.returncode, r.stderr[-200:])
    # Each ingress cap is a refusal with the data error (65), never a truncated state, and
    # the state the refusal would have replaced is untouched.
    capped = [
        ("stdin over 1 MiB", push(None, body=json.dumps(pushed) + " " * (1 << 20))),
        ("more than 200 steps",
         push({"todos": [{"task": "s%d" % i} for i in range(mod.PUSH_MAX_STEPS + 1)]})),
        ("a task longer than 500 chars",
         push({"todos": [{"task": "x" * (mod.PUSH_MAX_STRING + 1)}]})),
        ("a goal longer than 500 chars",
         push({"goal": "x" * (mod.PUSH_MAX_STRING + 1), "todos": [{"task": "g"}]})),
        ("a task that is not a string", push({"todos": [{"task": 7}]})),
        # `completed` must be a real boolean: the coercion `bool("false")` is True, which
        # would count a pusher's string as a finished step instead of refusing it.
        ("a completed that is a string", push({"todos": [{"task": "c", "completed": "false"}]})),
        ("a completed that is a number", push({"todos": [{"task": "c", "completed": 1}]})),
        # A pathological run of openers is under the byte cap but recurses the parser past its
        # limit; that is the same data error (65), not a crash and not a truncated state.
        ("deeply nested JSON", push(None, body="[" * 500_000)),
    ]
    for why, r in capped:
        assert r.returncode == 65, (why, r.returncode, r.stderr[-200:])
    # an explicit null is "not done", not a malformed field — the row is taken, uncounted
    # (written aside so it does not become the live state the next assertions read)
    nulldone = json.loads(push({"session": "NULLD", "todos": [{"task": "n", "completed": None}]},
                               "--to", os.path.join(TEST_HOME, "null-done.json")).stdout)
    assert (nulldone["done"], nulldone["total"]) == (0, 1), nulldone
    # the boundary itself is allowed: exactly the caps, written aside with --to
    okfile = os.path.join(TEST_HOME, "at-the-cap.json")
    boundary = push({"todos": [{"task": "y" * mod.PUSH_MAX_STRING}]
                              + [{"task": "s%d" % i} for i in range(mod.PUSH_MAX_STEPS - 1)]},
                    "--to", okfile)
    assert boundary.returncode == 0, (boundary.returncode, boundary.stderr[-200:])
    assert json.loads(boundary.stdout)["total"] == mod.PUSH_MAX_STEPS, boundary.stdout[:200]# ...and the failure leaves the state it refused to replace alone. BOTH of the sources are
    #      named here, for the reason given above this block's first read: with the machine's
    #      own store in the question, `bar` at the home directory answers from the operator's
    #      LIVE session (measured 2026-10-04: `todos 3/8`) instead of the state this block
    #      just pushed, and the check is then about the wrong list. Naming only the desktop
    #      glob was not enough — the CLI half globs `~/.config/manicode/projects` the same
    #      way, so the operator's running chats answered too, and this check went red with
    #      `todos 1/6` as the very thread doing the work added todos under it.
    assert run("bar", "--cli-root", cli_root, "--db", no_desk).stdout.strip() == "todos 1/1", \
        run("bar", "--cli-root", cli_root, "--db", no_desk).stdout
    say("push: a state JSON on stdin becomes the live state, and a bad one does not: ok")

    # ---- `-s file:PATH`: the same state, read back off disk. A directory means the state
    #      file inside it, so `-s file:$FBTODO_HOME` reads that home's own state.
    fdir = os.path.join(TEST_HOME, "pushed")
    shutil.rmtree(fdir, ignore_errors=True)
    os.makedirs(fdir, mode=0o700, exist_ok=True)
    fpath = os.path.join(fdir, "mine.json")
    pr = push({"session": "FILE1", "goal": "from a file",
               "todos": [{"task": "a", "completed": True}, {"task": "b", "completed": True}]},
              "--to", fpath)
    assert pr.returncode == 0, (pr.returncode, pr.stderr)
    assert json.loads(pr.stdout)["total"] == 2, pr.stdout
    assert run("bar", "-s", f"file:{fpath}").stdout.strip() == "todos 2/2"
    assert json.loads(run("json", "-s", f"file:{fpath}").stdout)["session"] == "FILE1"
    # --to did not touch the live state: the machine still reports the state it had
    assert run("bar").stdout.strip() == "todos 1/1", run("bar").stdout
    # a directory is that home's state file, and a recorded list number is left alone
    homedir = os.path.join(TEST_HOME, "file-home")
    os.makedirs(homedir, mode=0o700, exist_ok=True)
    mod.atomic_write_json(os.path.join(homedir, "fbtodo-state.json"), {
        "schema": 1, "backend": "cli", "session": "INFILE", "list_version": 7,
        "list_id": "abc123", "goal": "recorded", "todos": [{"task": "x", "completed": True}],
    })
    fjson = json.loads(run("json", "-s", f"file:{homedir}").stdout)
    assert fjson["session"] == "INFILE" and fjson["list_version"] == 7, fjson
    assert run("bar", "-s", f"file:{homedir}").stdout.strip() == "todos 1/1"
    # a file that is not there says so, rather than reading this machine's own list
    missing = os.path.join(fdir, "not-here.json")
    assert run("bar", "-s", f"file:{missing}").stdout.strip() == "todos -", missing
    assert "no state file" in run("json", "-s", f"file:{missing}").stdout
    # ...and a `file:PATH` state that breaks the same caps is refused with the same data
    # error: the file is an ingress too, not a trusted shortcut
    for why, payload in (
        ("too many steps", {"todos": [{"task": "s%d" % i}
                                       for i in range(mod.PUSH_MAX_STEPS + 1)]}),
        ("a task that is not a string", {"todos": [{"task": 7}]}),
        ("a completed that is not a boolean", {"todos": [{"task": "c", "completed": "no"}]}),
        ("a goal longer than 500 chars",
         {"goal": "x" * (mod.PUSH_MAX_STRING + 1), "todos": [{"task": "g"}]}),
    ):
        badfile = os.path.join(fdir, "bad-%s.json" % why.split()[0])
        with open(badfile, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        r = run("bar", "-s", f"file:{badfile}")
        assert r.returncode == 65, (why, r.returncode, r.stderr[-200:])
    # ...and the byte cap and the parser's recursion limit are refusals on disk too, so an
    # oversized or pathological file is the data error rather than a load the cap forbade
    bigfile = os.path.join(fdir, "too-big.json")
    with open(bigfile, "wb") as fh:
        fh.write(b"[" + b" " * mod.PUSH_MAX_BYTES)
    assert run("bar", "-s", f"file:{bigfile}").returncode == 65, "a file over the byte cap"
    deepfile = os.path.join(fdir, "deep.json")
    with open(deepfile, "w", encoding="utf-8") as fh:
        fh.write("[" * 500_000)
    assert run("bar", "-s", f"file:{deepfile}").returncode == 65, "a file that recurses the parser"
    # ...and the flag is still a usage error when it names neither a source nor a path
    for bad in ("file:", "nonsense"):
        assert run("bar", "-s", bad).returncode == 64, bad
    say("source: `-s file:PATH` reads a state off disk, and a bad one is a usage error: ok")
    try:
        os.unlink(live_state)
    except OSError:
        pass

    # ---- `fbtodo board`: every live session in one frame. Three questions, and each is
    #      checked where it is decided rather than through the frame it lands in: which
    #      sessions count as live, whether the board tells a CLI session from a desktop one,
    #      and whether a session that has gone quiet drops off by itself.
    bdir = os.path.join(TEST_HOME, "boardstore")
    shutil.rmtree(bdir, ignore_errors=True)
    broot = os.path.join(bdir, "cliprojects")
    now_s = time.time()

    def board_chat(project: str, age_s: float, tasks: list, goal=None, prompts=(),
                    ended: bool = False) -> str:
        """A CLI chat whose journal is `age_s` seconds old, with the given list in it.

        `ended` closes the turn the way the agent does — a `shouldEndTurn` record — without
        making the FILE old, which is the shape the desktop app leaves behind when it touches
        a chat nobody is working in any more.
        """
        chat = os.path.join(broot, project, "chats", "2026-10-03T10-00-00.000Z")
        os.makedirs(chat, exist_ok=True)
        rows = [{"level": "DEBUG", "timestamp": "2026-10-03T10:00:00.000Z",
                 "data": {"prompt": "do the thing"}}]
        if goal:
            rows.append({"level": "DEBUG", "timestamp": "2026-10-03T10:00:01.000Z",
                         "data": {"fullResponse": f"Goal: {goal}"}})
        rows.append({"level": "DEBUG", "timestamp": "2026-10-03T10:00:03.000Z",
                     "data": {"toolCalls": [{"toolName": "write_todos",
                                              "input": {"todos": tasks}}]}})
        # ...and only THEN the newer requests, in the order a journal writes them: a request
        # that came after the list is what makes it `now` (or a `nudge`, when it is a
        # continuation), and one written before the list would be that list's own ask.
        for at, text in prompts:
            rows.append({"level": "DEBUG", "timestamp": at, "data": {"prompt": text}})
        if ended:
            rows.append({"level": "DEBUG", "timestamp": "2026-10-03T10:00:09.000Z",
                         "data": {"fullResponse": "that is all of it", "shouldEndTurn": True}})
        log = os.path.join(chat, "log.jsonl")
        with open(log, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        os.utime(log, (now_s - age_s, now_s - age_s))
        return chat

    board_chat("alpha", 30, [{"task": "one", "completed": True},
                              {"task": "two", "completed": False}], goal="board it")
    board_chat("beta", 600, [{"task": "x", "completed": False}],
               prompts=[("2026-10-03T10:00:04.000Z", "keep going")])
    # ...and one that stopped two days ago: its store has not moved, so it is not live
    board_chat("gone", 2 * 86400, [{"task": "z", "completed": False}], goal="long over")
    # ...and one that STOPPED ten minutes ago: the file is fresh (the app has touched it since),
    # but the agent's last word was `shouldEndTurn`, so it is a finished session and not a live
    # one. This is the same question the single-session pane asks, through the same code
    # (`journal_liveness`) — the board used to answer it from the file's mtime alone and so
    # counted every chat the app had touched, running or not (measured 2026-10-04, the reason
    # `journal_liveness` exists: a chat that ended at 11:20 read as "just written" at 12:04).
    board_chat("stopped", 600, [{"task": "s", "completed": False}], goal="finished",
               ended=True)
    bargs = ["--cli-root", broot, "--db", os.path.join(bdir, "none", "*.db")]
    # the parsed flags, for the checks that ask the renderer rather than the command line
    bns = module.build_parser().parse_args(["board", *bargs])
    rows = json.loads(run("board", "--json", *bargs).stdout)
    assert rows["count"] == 2, rows
    assert [s["label"] for s in rows["sessions"]] == ["alpha", "beta"], rows["sessions"]
    # the newest store first, and the two clocks are the board's own fields
    assert rows["sessions"][0]["store_mtime_ms"] > rows["sessions"][1]["store_mtime_ms"]
    assert rows["sessions"][0]["running"] and not rows["sessions"][1]["running"], rows
    assert rows["sessions"][0]["goal"] == "board it", rows["sessions"][0]
    assert rows["sessions"][1]["nudge"] == "keep going", rows["sessions"][1]
    # a finished turn is not a live session, however recently the file moved — and no window
    # brings it back, because this is not a window question
    assert "stopped" not in [s["label"] for s in rows["sessions"]], rows["sessions"]
    still_gone = json.loads(run("board", "--json", "--board-live", "4320", *bargs).stdout)
    assert "stopped" not in [s["label"] for s in still_gone["sessions"]], still_gone
    # a row is the SAME list the single-session pane draws, read through the same reader
    one = json.loads(run("json", "--chat", os.path.join(broot, "alpha", "chats",
                                                         "2026-10-03T10-00-00.000Z")).stdout)
    assert [t["task"] for t in rows["sessions"][0]["todos"]] == [t["task"] for t in one["todos"]]
    assert rows["sessions"][0]["goal"] == one["goal"], (rows["sessions"][0], one)
    say("board: a live session is one whose store moved AND whose agent has not stopped, newest "
        "first, and the row is the same list the pane draws: ok")

    # ...and the window is the whole of "live": the quiet chat comes back when it is widened,
    # so nothing was dropped for being unreadable — only for being old.
    wide = json.loads(run("board", "--json", "--board-live", "4320", *bargs).stdout)
    assert wide["count"] == 3 and "gone" in [s["label"] for s in wide["sessions"]], wide
    assert wide["window_ms"] == 4320 * 60_000, wide
    # ...and `--board-max` is a real cap, not a hint: the two newest are what fit
    capped = json.loads(run("board", "--json", "--board-live", "4320",
                            "--board-max", "2", *bargs).stdout)
    assert capped["count"] == 2, capped
    assert [s["label"] for s in capped["sessions"]] == ["alpha", "beta"], capped["sessions"]
    # ...and `--board-rows` decides how many of a session's steps the frame draws
    tall = STRIP(run("board", "--board-rows", "1", *bargs).stdout)
    assert "[ ] two" not in tall and "… 1 more step" in tall, tall
    say("board: the window, the cap and the row count are the caller's, and a widened window "
        "brings the quiet session back: ok")

    # ...and a DESKTOP store is a board row too, with the app's own live threads: the same
    # store the single-session pane reads, asked for every thread it holds rather than one.
    bdb = os.path.join(bdir, "desktop", "proj-abc", "desktop-v2.db")
    os.makedirs(os.path.dirname(bdb), exist_ok=True)
    with open(os.path.join(os.path.dirname(bdb), "project.json"), "w") as fh:
        json.dump({"projectPath": os.path.join(TEST_HOME, "Proj")}, fh)
    con = sqlite3.connect(bdb)
    con.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, status TEXT,"
                " sidebar_archived_at INTEGER, turn_state TEXT, turn_alive_at INTEGER,"
                " last_prompt_at INTEGER, harness_state TEXT)")
    con.execute("CREATE TABLE messages (seq INTEGER, thread_id TEXT, role TEXT,"
                " parts_json TEXT, ts INTEGER)")
    bnow = int(now_s * 1000)
    for tid, title in (("T1", "first tab"), ("T2", "second tab")):
        con.execute("INSERT INTO threads VALUES (?, ?, 'open', NULL, 'idle', NULL, NULL, NULL)",
                    (tid, title))
    # The rows a real thread holds: the request that opened its turn, the agent's prose and
    # its `write_todos` (T1's row carries TWO lists — the turn's list is the last of them),
    # and, for T2, a heading left one row back with a newer ask after it (the stale shape).
    # `role` is here because the prose read asks for it: a fixture without it made the read
    # fail softly and the invariant below pass VACUOUSLY — nothing to disagree about.
    for seq, tid, role, parts, ts in (
        (0, "T1", "user", [{"kind": "text", "text": "the request that opened this list"}],
         bnow - 16_000),
        (1, "T1", "assistant",
         [{"toolName": "write_todos",
           "input": {"todos": [{"task": "step T1 opening", "completed": False}]}},
          {"kind": "text", "text": "Goal: first tab's list"},
          {"toolName": "write_todos",
           "input": {"todos": [{"task": "step T1", "completed": True},
                                {"task": "step T1 second", "completed": False}]}}],
         bnow - 10_000),
        (2, "T1", "user", [{"kind": "text", "text": "a newer ask for tab one"}],
         bnow - 5_000),
        (3, "T2", "assistant", [{"kind": "text", "text": "Goal: stated once, one turn back"}],
         bnow - 25_000),
        (4, "T2", "user", [{"kind": "text", "text": "the older ask"}], bnow - 24_000),
        (5, "T2", "user", [{"kind": "text", "text": "a newer ask for tab two"}], bnow - 23_000),
        (6, "T2", "assistant",
         [{"toolName": "write_todos",
           "input": {"todos": [{"task": "step T2", "completed": False},
                                {"task": "step T2 second", "completed": False}]}}],
         bnow - 20_000),
        # T3 is RUNNING: the store holds the previous turn's list, and the in-flight one
        # lives in `harness_state`. A reader that stops at the committed rows answers the
        # wrong list (the old one), which is the divergence the invariant below is for —
        # the live list and its own heading are what all three must show.
        (8, "T3", "assistant",
         [{"toolName": "write_todos",
           "input": {"todos": [{"task": "committed step T3", "completed": False}]}}],
         bnow - 30_000),
    ):
        con.execute("INSERT INTO messages VALUES (?, ?, ?, ?, ?)",
                    (seq, tid, role, json.dumps(parts), ts))
    live_history = {"sessionState": {"mainAgentState": {"messageHistory": [
        {"role": "user", "sentAt": bnow - 35_000, "tags": ["USER_PROMPT"],
         "content": [{"type": "text", "text": "the request that opened tab three"}]},
        {"role": "assistant", "sentAt": bnow - 8_000,
         "content": [{"type": "text", "text": "Goal: tab three's live list"}]},
        {"role": "assistant", "sentAt": bnow - 6_000,
         "content": [{"type": "tool-call", "toolName": "write_todos",
                      "input": {"todos": [{"task": "live step one", "completed": True},
                                           {"task": "live step two", "completed": False}]}}]},
    ]}}}
    con.execute("INSERT INTO threads VALUES ('T3', 'running tab', 'open', NULL, 'running', ?, ?, ?)",
                (bnow - 1_000, bnow - 40_000, json.dumps(live_history)))
    con.commit()
    con.close()
    both = json.loads(run("board", "--json", "--cli-root", broot,
                          "--db", os.path.join(bdir, "desktop", "*", "desktop-v2.db")).stdout)
    kinds = {s["backend"] for s in both["sessions"]}
    assert kinds == {"cli", "desktop"}, both["sessions"]
    desk = [s for s in both["sessions"] if s["backend"] == "desktop"]
    assert {s["session"] for s in desk} == {"T1", "T2", "T3"}, desk
    assert desk[0]["label"] == "Proj", desk[0]
    # the desktop row is a THREAD, so its own list is the thread's — read by `live_threads`,
    # the same answer the single-session pane stacks under a heading. T3 is running, so its
    # row is the LIVE list, not the committed one the store has closed.
    assert {s["todos"][0]["task"] for s in desk} == {"step T1", "step T2", "live step one"}, desk
    say("board: the desktop store contributes every live thread it holds, labelled by project: ok")

    # ---- ...and the three readers of ONE store — the pane's own read (which `json` prints),
    #      the pane's STACKED rows and the board's desktop rows — must agree about every
    #      thread: its `done/total`, its heading, and whether that heading is stale. They are
    #      three separate walks today (the followed-thread query, `live_threads`, and the
    #      board's own call into it), and the bug class this pane has already had once — a
    #      list drawn as `0/6` because one walk took the turn's FIRST `write_todos` instead of
    #      its last — is exactly a disagreement between walks. The fixture holds a fresh
    #      heading, a stale one and a heading-less neighbour, so a reader that loses
    #      staleness, or a heading, cannot pass by reporting nothing for everybody.
    inv_now = int(now_s * 1000)
    bpattern = os.path.join(bdir, "desktop", "*", "desktop-v2.db")
    bstate = os.path.join(bdir, "desktop", "workspace.json")

    def inv_facts(todos, goal, stale):
        """(done, total, heading, stale) — the four facts every reader answers."""
        todos = todos or []
        return (sum(1 for t in todos if isinstance(t, dict) and t.get("completed")),
                len(todos), goal, bool(stale))

    pinned = {tid: module.read_desktop(bdb, thread_id=tid, source="pinned", now_ms=inv_now)
              for tid in ("T1", "T2", "T3")}
    stacked = module.read_desktop(bdb, thread_id="T1", source="pinned", others=9,
                                 now_ms=inv_now)
    stacked_rows = {row["id"]: row for row in stacked["threads"]}
    for tid, st in pinned.items():
        want = inv_facts(st.get("todos"), st.get("goal"), st.get("goal_stale"))
        row = stacked_rows.get(tid)
        assert row is not None, stacked_rows
        assert inv_facts(row.get("todos"), row.get("goal"), row.get("goal_stale")) == want, \
            (tid, "stacked row", row, want)
        drawn = json.loads(run("json", "-s", "desktop", "--db", bdb, "-t", tid,
                               "--state", bstate).stdout)
        assert inv_facts(drawn.get("todos"), drawn.get("goal"), drawn.get("goal_stale")) \
            == want, (tid, "json", drawn, want)
        # ...the declared totals too, not only the list they are counted from
        assert (drawn.get("done"), drawn.get("total")) == want[:2], (tid, "json totals", drawn)
    board_rows = json.loads(run("board", "--json", "--cli-root", broot,
                                "--db", bpattern).stdout)["sessions"]
    by_session = {r["session"]: r for r in board_rows if r["backend"] == "desktop"}
    for tid, st in pinned.items():
        want = inv_facts(st.get("todos"), st.get("goal"), st.get("goal_stale"))
        row = by_session.get(tid)
        assert row is not None, sorted(by_session)
        assert inv_facts(row.get("todos"), row.get("goal"), row.get("goal_stale")) == want, \
            (tid, "board row", row, want)
    assert pinned["T1"].get("goal") and not pinned["T1"].get("goal_stale"), pinned["T1"]
    assert pinned["T2"].get("goal_stale") is True, pinned["T2"]
    # ...and the running thread reads its LIVE list in all three, heading included: a reader
    # that stopped at the committed rows would answer `0/1` about a thread that is at `1/2`.
    assert pinned["T3"].get("live_list") and len(pinned["T3"]["todos"]) == 2, pinned["T3"]
    assert pinned["T3"].get("goal") == "tab three's live list", pinned["T3"]
    say("three readers of one store — json, the pane's stacked rows and the board — agree "
        "on done/total, heading and staleness for every thread: ok")

    # ...and the frame is a frame: one box, a heading row per session, the last divider
    # closing the last session rather than opening one that never comes
    # (drawn with colour asked FOR, since a piped run is the plain renderer by design — the
    # same split `render` makes, and the reason a script gets the stable text)
    framed = STRIP(module.render_board(module.board_sessions(bns, int(now_s * 1000)), True,
                                       width=80, now_ms=int(now_s * 1000), height=24))
    assert framed.splitlines()[0].startswith("╭── ") and "FREEBUFF BOARD" in framed, framed
    assert framed.splitlines()[-1].startswith("╰"), framed
    assert "├" not in framed.splitlines()[-2], framed.splitlines()[-3:]
    assert framed.count("╭") == 1 and framed.count("╰") == 1, framed
    # ...and no row of it is wider than the pane it was drawn for — asked of the renderer
    # directly, at a width the frame's own budgets have to give way to
    wide_frame = module.render_board(module.board_sessions(bns, int(now_s * 1000)), True,
                                     width=44, now_ms=int(now_s * 1000), height=24)
    assert max(module._cell_width(line) for line in wide_frame.split("\n")) <= 44, wide_frame
    say("board: one box, a heading per session, and no row wider than the pane: ok")

    # ...and the two things that are refusals rather than output: a document and a pane are
    # not the same request, and the flag set is still checked by the parser's own rules
    assert run("board", "--live", "--json", *bargs).returncode == 2, "--live with --json"
    assert run("snap", "--live").returncode == 2, "--live outside the board"
    say("board: --json with --live is a usage error, and --live belongs to `board`: ok")

    # ---- the frame's grid, which is the one thing a reader sees without reading: every row
    #      is the same width, so the right border is a straight line. A single glyph whose
    #      width the code and the terminal disagree about breaks that ON SCREEN while every
    #      width check here still passes — measured 2026-10-04, `🎯` in the goal heading is two
    #      cells to `wcwidth` and one in the terminal the pane was read in, so every goal row
    #      sat a column short of its neighbours and the border stepped sideways under it.
    assert module.frame_chrome_is_single_cell() == [], module.frame_chrome_is_single_cell()
    grid_now = int(time.time())
    for probe_w in (32, 40, 48, 56, 72, 96):
        probe = STRIP(module.render(
            {"backend": "cli", "session": "S", "goal": "pane inherits host colors, UI-only "
             "rendering, and the overflow menu as one vertical labelled menu",
             "todos": [{"task": "a step long enough to wrap at the narrowest probe width here",
                        "completed": False}], "done": 0, "total": 1},
            True, watching=None, width=probe_w, now_ms=grid_now, height=24,
            theme={}, truecolor=False)).split("\n")
        for line in probe:
            assert module._cell_width(line) == probe_w, (probe_w, module._cell_width(line), line)
            assert line[:1] in "╭│├╰" and line[-1:] in "╮│┤╯", (probe_w, line)
    say("the frame is a grid: every row the width it was asked for, both edges kept, and "
        "every glyph one cell wide: ok")

    # ---- ...and the width assertion above CANNOT catch that class of bug on its own, which is
    #      why the pane was still a column short after it was written. `_cell_width` decides a
    #      glyph is two cells because `unicodedata.east_asian_width` says `W`, and the terminal
    #      that drew the frame said one: the ruler and the thing it measures disagree, so
    #      asserting the ruler's answer is asserting the bug. The check that can fail is the one
    #      that does not consult the ruler — it reads the rendered frame's own glyphs and asks
    #      Unicode directly, which is the same table the terminal's font metrics come from. A
    #      frame may only ever draw characters that table calls one cell wide: `Ambiguous` and
    #      `Wide` are exactly the two answers a terminal is free to disagree with, and `🎯`
    #      (U+1F3AF) is `Wide` here and one cell there.
    grid_frame = STRIP(module.render(
        {"backend": "cli", "session": "S",
         "goal": "pane inherits host colors, UI-only",
         "todos": [{"task": "a step that wraps at the narrowest probe width", "completed": False},
                   {"task": "a finished step", "completed": True}],
         "done": 1, "total": 2, "now": "measure the real widths"},
        True, watching=True, width=32, now_ms=grid_now, height=24,
        theme={}, truecolor=False))
    # `frame_chrome_is_single_cell` is the inventory's own claim about the declared chrome; this
    # is the claim about what a real render actually emitted, which is the one a reader sees.
    emitted_wide = sorted({
        ch for ch in grid_frame if not unicodedata.combining(ch)
        and unicodedata.east_asian_width(ch) in ("W", "F")
    })
    assert emitted_wide == [], (
        "the frame draws glyphs a terminal may draw at a different width than the code "
        f"pads for: {emitted_wide!r} — every one of these steps the right border sideways "
        "on screen while `_cell_width` still agrees with itself"
    )
    say("the frame's own glyphs are one cell to Unicode, not just to the code's ruler: ok")

    # ---- and how far the log is from re-choosing its own constants: the clip is consulted
    #      only on a young list, and moves the number only sometimes, so the count that
    #      governs it is the second one. Measured over the 161-span replay, 157 of those spans
    #      wrote the same number either way.
    rc = {
        mod.task_key("R", "first"): {
            "started_ms": t0, "done_ms": t0 + 60_000, "model": "m/e",
            "fc": {"at": t0, "pace": 60_000}},                     # no prior: not consulted
        mod.task_key("R", "clipped"): {
            "started_ms": t0 + 60_000, "done_ms": t0 + 120_000, "model": "m/e",
            "fc": {"at": t0 + 60_000, "pace": 240_000}},           # 1 prior at 60s: MOVED
        mod.task_key("R", "same"): {
            "started_ms": t0 + 120_000, "done_ms": t0 + 180_000, "model": "m/e",
            "fc": {"at": t0 + 120_000, "pace": 60_000}},           # 2 priors at 60s: unchanged
        mod.task_key("R", "old enough"): {
            "started_ms": t0 + 180_000, "done_ms": t0 + 240_000, "model": "m/e",
            "fc": {"at": t0 + 180_000, "pace": 60_000}},           # 3 priors: not consulted
        mod.task_key("R", "flip"): {
            "started_ms": t0 + 240_000, "done_ms": t0 + 242_000, "model": "m/e",
            "fc": {"at": t0 + 240_000, "pace": 60_000}},           # under the floor
    }
    rr = mod.refit_readiness(rc, t0 + 300_000, "m/e")
    assert rr["scored"] == 4, rr
    assert rr["eligible"] == 2, rr
    assert rr["decided"] == 1, rr
    assert rr["spans_needed"] is None, "a rate from one decided step is not a rate"
    # ...and once there are enough decided steps, the extrapolation is just the rate. Six
    # lists of two, each with its second step clipped, plus one step nothing was decided about.
    many = {}
    for n in range(6):
        s0 = t0 + n * 200_000
        many[mod.task_key("S2", f"l{n}a")] = {
            "started_ms": s0, "done_ms": s0 + 60_000, "model": "m/e", "lv": n,
            "fc": {"at": s0, "pace": 60_000}}
        many[mod.task_key("S2", f"l{n}b")] = {
            "started_ms": s0 + 60_000, "done_ms": s0 + 120_000, "model": "m/e", "lv": n,
            "fc": {"at": s0 + 60_000, "pace": 240_000}}
    many[mod.task_key("S2", "tail")] = {
        "started_ms": t0 + 1_400_000, "done_ms": t0 + 1_460_000, "model": "m/e", "lv": 99,
        "fc": {"at": t0 + 1_400_000, "pace": 60_000}}
    rr2 = mod.refit_readiness(many, t0 + 2_000_000, "m/e")
    assert rr2["scored"] == 13 and rr2["decided"] == 6 and rr2["eligible"] == 6, rr2
    assert rr2["spans_needed"] == int(mod.REFIT_MIN_DECIDED / (6 / 13)), rr2
    assert mod.refit_readiness({}, t0) == {
        "scored": 0, "eligible": 0, "decided": 0, "spans_needed": None}
    # ...and the pane says it too, on the same terms as PATCH and ALERT: only when the state
    # carries the fact, so an older state (or a fixture) draws exactly as it always did, and
    # only once a median could be read — below that the row would count towards a number
    # nobody can use yet, and every row of chrome is a step the list loses.
    assert mod.refit_row({}) == [] and mod.refit_row({"todos": []}) == []
    assert mod.refit_row({"refit": {"scored": 29, "decided": 9}}) == [], "shows too early"
    row = mod.refit_row({"refit": {"scored": 31, "decided": 12}})
    assert row and row[0][0] == "REFIT" and "12/78" in row[0][1], row
    ready = mod.refit_row({"refit": {"scored": 214, "decided": mod.REFIT_MIN_DECIDED}})
    assert ready and "ready" in ready[0][1], ready
    narrow = mod.refit_row({"refit": {"scored": 31, "decided": 12}}, 40)
    assert mod._cell_width("  " + mod.patch_row_text(narrow)) <= 40, narrow
    # ...and its row is charged to the height budget, so a pane never loses its bottom border
    pane = {
        "todos": [{"task": f"s{i}", "completed": i < 3} for i in range(12)],
        "done": 3, "total": 12, "list_version": 1, "model": "m/e",
        "task_times": {f"s{i}": {"started_ms": t0 - 600_000, "done_ms": t0 - 300_000}
                       for i in range(3)},
        "patch": {"outcome": "ok", "version": "9.9.9", "at_ms": t0, "severity": "ok"},
    }
    for h in (10, 12, 14):
        bare = ansi.sub("", mod.render(dict(pane), False, width=100, height=h, now_ms=t0))
        drawn = ansi.sub(
            "",
            mod.render(dict(pane, refit={"scored": 31, "decided": 12}),
                       False, width=100, height=h, now_ms=t0),
        )
        assert len(drawn.split("\n")) <= h, (h, len(drawn.split("\n")))
        assert any("REFIT" in ln for ln in drawn.split("\n")), h
        assert "REFIT" not in bare, "a state without the fact grew a row"
    say("estimates: the log says how far it is from refitting the rate constants: ok")

    # ---- and the memory it is scored against now spans a month, not a week: the old cap
    #      pruned records while they were still the only evidence there was (measured
    #      2026-09-29: the oldest record in a real log was 6.68 days old, one day from
    #      being thrown away, and the shape memory held one sample)
    assert mod.MAX_TASK_AGE_DAYS >= 30, mod.MAX_TASK_AGE_DAYS
    assert "recent" in mod.task_history_from_log({
        mod.task_key("R", "recent"): {
            "started_ms": now_ms - 30 * day, "done_ms": now_ms - 30 * day + 60_000},
    }, now_ms)
    assert mod.task_history_from_log({
        mod.task_key("R", "ancient"): {
            "started_ms": now_ms - 400 * day, "done_ms": now_ms - 400 * day + 60_000},
    }, now_ms) == {}
    say("per-task timing: one clock per step, counting up then freezing: ok")

    # ---- the CLI process must be identified by argv, not by stray text
    assert mod.is_freebuff_cmd("node /usr/bin/bin/freebuff")
    assert not mod.is_freebuff_cmd('python3 -c "x bin/freebuff y"')
    assert not mod.is_freebuff_cmd("/usr/bin/grep bin/freebuff")
    assert mod.parse_etime("09:17:21") == 33_441
    assert mod.parse_etime("2-03:34:49") == 185_689
    say("instance detection matches argv tokens and parses ps etime: ok")


    # ---- --instance-of finds the CLI launched by a given shell
    # A copy of /bin/sleep is SIGKILLed on macOS (AMFI: the copy loses its
    # signature), so the stand-in has to be a script — whose command line is
    # "/bin/sh …/bin/freebuff", i.e. a token ending in bin/freebuff.
    bin_dir = os.path.join(TEST_HOME, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    fake = os.path.join(bin_dir, "freebuff")
    with open(fake, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\nsleep 600 & wait\n")
    os.chmod(fake, 0o755)
    fake_proc = spawn_quiet(fake)
    # Waited for, not slept through: what this check needs from the stand-in is that the
    # PROCESS TABLE can see it (`instance_of_parent` reads `ps`), and a fixed 0.4 s is a
    # guess that only holds on an idle machine — the same assumption that made a start-up
    # race read as a flaky check.
    assert wait_for(lambda: fake_proc.poll() is None
                    and fake_proc.pid in mod.freebuff_pids()), (
        "the stand-in freebuff never reached the process table"
    )
    resolved = mod.instance_of_parent(os.getpid())
    assert resolved == fake_proc.pid, (resolved, fake_proc.pid)
    snap_of = json.loads(run("json", "--instance-of", str(os.getpid())).stdout)
    assert snap_of.get("instance_pid") == fake_proc.pid, snap_of.get("instance_pid")
    kill_tree(fake_proc)
    say("resolves the freebuff launched by a given shell pid: ok")

    # ---- a frozen list closes the pane instead of lingering
    frozen = os.path.join(TEST_HOME, "frozen-chat")
    os.makedirs(frozen, exist_ok=True)
    line = {
        "level": "DEBUG",
        "timestamp": "2026-09-21T06:44:29.043Z",
        "pid": 1,
        "data": {
            "iteration": 7,
            "toolCalls": [
                {
                    "toolName": "write_todos",
                    "input": {"todos": [{"task": "old step", "completed": False}]},
                }
            ],
        },
    }
    with open(os.path.join(frozen, "log.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")
    json.dump({"messageCount": 1, "firstPrompt": "frozen"},
              open(os.path.join(frozen, "chat-meta.json"), "w"))

    pid_s, fd_s = pty.fork()
    if pid_s == 0:
        os.environ["FBTODO_HOME"] = TEST_HOME
        os.execv(
            sys.executable,
            [sys.executable, FB, "pane", "--no-daemon", "--chat", frozen,
             "--stale-after", "0.05", "-i", "0.2"],
        )
    out_s, deadline = b"", time.time() + stale_window_bound()
    os.set_blocking(fd_s, False)
    status_s = None
    while time.time() < deadline:
        try:
            out_s += os.read(fd_s, 65536)
        except (BlockingIOError, OSError):
            pass
        wpid, st_s = os.waitpid(pid_s, os.WNOHANG)
        if wpid:
            status_s = st_s
            break
        time.sleep(0.2)
    assert status_s is not None, "pane did not exit on a stale list"
    text_s = STRIP(out_s.decode("utf-8", "replace"))
    assert "idle" in text_s and "closing" in text_s, text_s
    assert os.waitstatus_to_exitcode(status_s) == 0, os.waitstatus_to_exitcode(status_s)
    say("a stale list closes the pane: ok (exit 0, 'idle … closing')")

    # ---- shell integration: PATH alias-free command + watcher autostart hook. The keepers
    #      alive before it are remembered, so what the hook adds can be told from what the
    #      machine already had (an old build's stray is a real thing to report, but not a
    #      failure of this run — see the check below the hook).
    # The autostart adopts a running Freebuff, so the check needs one: a runner has no
    # session at all, and without a stand-in the hook correctly starts nothing. The stand-in
    # is reaped with the check.
    autostart_fake = spawn_quiet(fake)
    # waited for, not slept through: the hook adopts a Freebuff it can see in `ps`, so the
    # stand-in has to be THERE before the count that measures what it adopts is taken
    assert wait_for(lambda: autostart_fake.poll() is None
                    and autostart_fake.pid in mod.freebuff_pids()), (
        "the stand-in freebuff never reached the process table"
    )
    keepers_before = keeper_aim()
    ptypid, ptyfd = pty.fork()
    if ptypid == 0:
        os.environ["FBTODO_HOME"] = TEST_HOME
        # ...and no pane keeper. The hook's `fbtodo daemon` asks for one, and a keeper keeps
        # the panes of the tmux server in ITS environment — this child runs inside whatever
        # pane the suite was started from, so that server is the owner's. The keeper it left
        # behind was therefore aimed at the owner's real panes with the throwaway root: it
        # moved the owner's list pane back to the default side every 3s, forever, and its
        # log lived in a home this suite wipes (found 2026-09-29, from the pane that would
        # not stay where the owner dragged it). The watcher this check is about still starts.
        os.environ["FBTODO_NO_PANE"] = "1"
        # The autostart hook lives in the operator's `~/.zshrc`, which a runner does not have,
        # so this child reads a throwaway `ZDOTDIR` instead: it puts this checkout on PATH and
        # SOURCES the hook the repository ships (`examples/zshrc-autostart.zsh`) — the rule
        # under test is the shipped one, not the machine's. FBTODO_NO_AUTOSTART is cleared
        # for this child alone, since CI sets it to keep the other phases quiet.
        zdot = os.path.join(TEST_HOME, "zdot")
        os.makedirs(zdot, exist_ok=True)
        with open(os.path.join(zdot, ".zshrc"), "w", encoding="utf-8") as fh:
            fh.write(
                f'path=({os.path.dirname(FB)} $path)\n'
                f'. {os.path.join(ROOT, "examples", "zshrc-autostart.zsh")}\n'
            )
        os.environ["ZDOTDIR"] = zdot
        os.environ.pop("FBTODO_NO_AUTOSTART", None)
        # `-d` (no global rcs) keeps the distribution's `/etc/zsh` out of the way: on Ubuntu it
        # runs `compinit`, which prompts about "insecure directories" on a runner and blocks
        # the shell before `command -v fbtodo` ever runs. Our `ZDOTDIR/.zshrc` still loads.
        os.execv("/bin/zsh", ["/bin/zsh", "-d", "-i", "-c", "command -v fbtodo && fbtodo bar"])
    out = b""
    os.set_blocking(ptyfd, False)
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            chunk = os.read(ptyfd, 65536)
            if chunk:
                out += chunk
        except (BlockingIOError, OSError):
            pass
        if b"todos " in out:
            break
        time.sleep(0.2)
    try:
        os.waitpid(ptypid, os.WNOHANG)
    except ChildProcessError:
        pass
    text = out.decode("utf-8", "replace")
    assert os.path.basename(FB) in text, text
    assert "todos " in text, text
    say("interactive zsh resolves `fbtodo` and prints `fbtodo bar`: ok")

    # the hook spawns asynchronously, so wait for its lock rather than racing it
    deadline = time.time() + 15
    while time.time() < deadline and not os.path.exists(lock_path):
        time.sleep(0.2)
    assert os.path.exists(lock_path), "zshrc hook did not ensure a watcher"
    lock = json.load(open(lock_path))
    assert lock.get("instance_pid"), lock
    run("stop")
    say("zshrc hook auto-starts a watcher for the live instance: ok")
    # ---- ...and it must start a WATCHER and nothing else. `fbtodo daemon` asks for a pane
    #      keeper as well, and a keeper is aimed at the tmux server in its environment: this
    #      hook runs inside the owner's own pane, so the keeper it used to leave behind went
    #      off keeping the owner's panes with the test state root — the pane that would not
    #      stay where the owner put it. `FBTODO_NO_PANE=1` in the child is the fix; this is
    #      the check that it holds.
    stray = keeper_leaks(keepers_before, TEST_HOME, os.environ.get("TMUX"))
    assert not stray, (
        "the zshrc hook's autostart left a pane keeper keeping THIS server's panes with "
        f"the test state root: {stray}"
    )
    say("the zshrc hook keeps no pane keeper of its own (FBTODO_NO_PANE): ok")
    kill_tree(autostart_fake)

    # ---- retention: old records, the count cap, temp files, the log cap
    saved_home = os.environ.get("FBTODO_HOME")
    os.environ["FBTODO_HOME"] = TEST_HOME
    try:
        mp = load_fbtodo()
        assert mp.SCRATCH == TEST_HOME, mp.SCRATCH

        now = int(time.time() * 1000)
        day = 86400 * 1000
        log = {
            "schema": 1,
            "session": "S",
            "tasks": {
                # ages are expressed against the retention window itself, not a hardcoded
                # week: the rule under test is "outside the window, gone", and it has to
                # keep holding whichever way the window is set
                "ancient": {
                    "started_ms": now - int((mp.MAX_TASK_AGE_DAYS + 30) * day),
                    "done_ms": now - int((mp.MAX_TASK_AGE_DAYS + 30) * day),
                },
                "stale": {
                    "started_ms": now - int((mp.MAX_TASK_AGE_DAYS + 1) * day), "done_ms": None,
                },
                "a": {"started_ms": now - 3000, "done_ms": now - 2000},
                "b": {"started_ms": now - 2000, "done_ms": None},
                "c": {"started_ms": now - 1000, "done_ms": None},
            },
        }
        put_tasklog(mp, log)

        rep = mp.prune_scratch(force=True)
        assert rep["records_removed"] == 2, rep
        kept = json.load(open(mp.TASKS_PATH))["tasks"]
        # a log from before the (session, task) change: prune keeps the same records,
        # re-keyed under the session they were recorded for
        assert set(kept) == {mp.task_key("S", k) for k in ("a", "b", "c")}, kept
        assert mp.prune_scratch()["skipped"] is True, "prune ignored its own interval"
        rep_cap = mp.prune_scratch(force=True, max_records=1)
        assert rep_cap["records_kept"] == 1, rep_cap
        assert os.path.getsize(mp.TASKS_PATH) < 512, "record cap did not shrink the file"
        say("retention drops aged and over-cap task records: ok")

        stale_tmp = os.path.join(mp.SCRATCH, ".fbtodo.stale.tmp")
        fresh_tmp = os.path.join(mp.SCRATCH, ".fbtodo.fresh.tmp")
        for p in (stale_tmp, fresh_tmp):
            open(p, "w").write("x")
        old = time.time() - 7200
        os.utime(stale_tmp, (old, old))
        with open(mp.LOG_PATH, "wb") as fh:
            fh.write(b"x" * 2_000_000)
        rep2 = mp.prune_scratch(force=True, log_cap=1024)
        assert rep2["temp_removed"] == 1 and not os.path.exists(stale_tmp), rep2
        assert os.path.exists(fresh_tmp), "a fresh temp file was swept"
        assert rep2["log_truncated_bytes"] > 0, rep2
        assert os.path.getsize(mp.LOG_PATH) == 0, "log cap did not truncate"
        os.unlink(fresh_tmp)
        say("retention sweeps killed temp files and caps the daemon log: ok")

        out = run("prune", "--json").stdout
        assert json.loads(out)["scratch"].startswith(TEST_HOME), out
        assert "task records" in run("status").stdout
        say("prune subcommand and status footprint line: ok")

        # ---- the task log is an append-only event stream, and the JSON beside it is a memo
        #      of that stream. A poll appends the records it CHANGED rather than rewriting
        #      every record it did not, and a crash between the two costs a re-read of the
        #      bytes after the memo's own cursor — never a step.
        view_path = mp.TASKS_PATH
        ev_path = mp.events_path()
        assert ev_path == view_path + "l", ev_path
        for p in (ev_path, view_path):
            if os.path.exists(p):
                os.unlink(p)
        evt0 = int(time.time() * 1000)

        def ev_poll(tasks, at_ms, session="EV"):
            return mp.track_tasks(
                {"session": session,
                 "todos": [{"task": t, "completed": c} for t, c in tasks]},
                now_ms=at_ms)

        ev_poll([("one", False), ("two", False)], evt0)
        with open(ev_path, "rb") as fh:
            stream = fh.read()
        events = [json.loads(ln) for ln in stream.splitlines() if ln.strip()]
        assert events, "a new task record was not written as an event"
        keys = {e.get("k") for e in events}
        assert mp.task_key("EV", "one") in keys and mp.task_key("EV", "two") in keys, events
        view = json.load(open(view_path))
        assert int(view["events"]["off"]) == len(stream), view.get("events")
        # the memo does not lie: folding the stream from scratch gives the same records
        folded = mp.fold_task_events({"schema": mp.TASKLOG_SCHEMA, "session": None, "tasks": {}})
        assert folded["tasks"] == view["tasks"], (folded["tasks"], view["tasks"])
        # a poll that changed nothing appends nothing and rewrites nothing
        ev_poll([("one", False), ("two", False)], evt0 + 1000)
        with open(ev_path, "rb") as fh:
            assert fh.read() == stream, "a no-op poll touched the stream"
        # ...and a poll that DID change something appends to it, prefix intact
        ev_poll([("one", True), ("two", False)], evt0 + 5000)
        with open(ev_path, "rb") as fh:
            grown = fh.read()
        assert grown.startswith(stream) and len(grown) > len(stream), "the stream was rewritten"
        # ONE event per record the poll touched — two steps moved, so two events: the second
        # step's clock started when the first was ticked, which is a change to its record
        one, two = mp.task_key("EV", "one"), mp.task_key("EV", "two")
        added = [json.loads(ln) for ln in grown[len(stream):].splitlines() if ln.strip()]
        assert added, "a change was not appended as an event"
        for row in added:
            assert set(row) & {"k", "session", "drop", "pruned_ms"}, row
            assert row.get("k", one) in (one, two), row
        assert any(row.get("k") == one and row.get("r", {}).get("done_ms") for row in added), added
        # the crash window: an event that landed while the memo did not is not lost
        mp.append_task_events([{"v": mp.TASKLOG_SCHEMA, "k": one,
                                "r": {"started_ms": evt0, "done_ms": evt0 + 4242,
                                      "model": "m/e"}}])
        assert mp.load_tasklog()["tasks"][one]["done_ms"] == evt0 + 4242, (
            "an event that landed after the memo was thrown away")
        # retention applies to the stream too: a prune folds it down to the records it kept
        before = os.path.getsize(ev_path)
        mp.prune_scratch(force=True)
        kept = mp.load_tasklog()["tasks"]
        assert os.path.getsize(ev_path) < before, "prune did not compact the event stream"
        assert mp.load_tasklog()["tasks"] == kept, "compaction changed the records"
        assert json.load(open(view_path))["tasks"] == kept, "the memo and the stream disagree"
        # a drop is an event too: a step that leaves the list with no span to keep goes
        ev_poll([("two", True), ("three", False)], evt0 + 9000)
        ev_poll([("two", True)], evt0 + 10_000)
        three = mp.task_key("EV", "three")
        rows = [json.loads(ln) for ln in open(ev_path, "rb").read().splitlines() if ln.strip()]
        assert any(three in (r.get("drop") or []) for r in rows), rows
        assert three not in mp.load_tasklog()["tasks"], "a dropped record came back"
        # a torn tail — a crash mid-append — is not read as a record, and does not hide the
        # whole lines before it. This is the last check on the file because the half line
        # stays where it is: the stream is appended to and never rewritten.
        intact = mp.load_tasklog()["tasks"]
        with open(ev_path, "ab") as fh:
            fh.write(b'{"v":2,"k":"torn","r":{"started_ms":1')
        after = mp.load_tasklog()
        assert "torn" not in after["tasks"], "a half-written event was folded"
        assert after["tasks"] == intact, "a torn tail hid the events before it"
        for p in (ev_path, view_path):
            if os.path.exists(p):
                os.unlink(p)
        say("task events: appended per change, folded from a cursor, torn tail ignored: ok")
    finally:
        if saved_home is None:
            os.environ.pop("FBTODO_HOME", None)
        else:
            os.environ["FBTODO_HOME"] = saved_home


    # ---- the freebuff() wrapper opens a todo pane with the session and closes it
    #@phase local-session
    if shutil.which("tmux"):
        # A private server on its own socket: the pane then inherits THIS process's
        # PATH (a shared server would overwrite PATH from the attaching client via
        # update-environment, and the stub freebuff would never be found), and the
        # user's real server is never touched.
        #      Named `check_sock`, not `sock`: this is module level, and rebinding the NAME
        #      would turn the helper into a string for every later line — including the
        #      teardown that kills this server, which then died of `TypeError` instead and
        #      left the server (and its error) behind.
        check_sock = sock("fbtchecksock")
        sess = "fbtcheck"
        tmux = ["tmux", "-L", check_sock]
        subprocess.run(tmux + ["kill-server"], capture_output=True)
        stub_bin = os.path.join(TEST_HOME, "session", "bin")
        os.makedirs(stub_bin, exist_ok=True)
        stub = os.path.join(stub_bin, "freebuff")
        # The stand-in session ends when THIS script says so, not on a clock: the checks
        # below (a killed pane that has to come back, one pushed out of place, a pin that
        # moves and resizes it) have to finish before the session ends, and a fixed
        # `sleep 40` made the suite wait out whatever was left of it — 34.7 s of the
        # 107 s run, measured 2026-09-23 with FBTODO_SELFCHECK_TIME=1. A release file is
        # both faster (the close check starts the moment the checks are done) and more
        # deterministic (no "long enough for a slow machine" guess to get wrong).
        release = os.path.join(TEST_HOME, "end-session")
        if os.path.exists(release):
            os.unlink(release)
        with open(stub, "w", encoding="utf-8") as fh:
            # a script (not a copy of /bin/sleep, which macOS SIGKILLs) so `ps` shows a
            # path ending in bin/freebuff
            fh.write(f"#!/bin/sh\nwhile [ ! -e {release} ]; do sleep 0.2; done\n")
        os.chmod(stub, 0o755)
        path = stub_bin + os.pathsep + os.environ.get("PATH", "")
        # The stall watch is asked about a LOCAL session, so its stub — and the check for
        # it below — live here. A stub, not the real notifier: this run must
        # not push the fixture's lists to the owner's phone.
        pause_log = os.path.join(TEST_HOME, "pause-calls.log")
        pause_stub = os.path.join(TEST_HOME, "pause-stub.sh")
        with open(pause_stub, "w", encoding="utf-8") as fh:
            fh.write(
                "#!/bin/sh\n"
                f'printf "%s\\n" "$*" >> "{pause_log}"\n'
                "exit 0\n"
            )
        os.chmod(pause_stub, 0o755)
        # The pane watch, same reason and same shape: it reports whether the keeper is
        # doing its job, and the real one reads THIS Mac's tmux — so an unstubbed run
        # would scan the owner's own panes and, on a pane it judged missing, push.
        pane_bell_log = os.path.join(TEST_HOME, "pane-bell-calls.log")
        pane_bell_stub = os.path.join(TEST_HOME, "pane-bell-stub.sh")
        with open(pane_bell_stub, "w", encoding="utf-8") as fh:
            fh.write(
                "#!/bin/sh\n"
                f'printf "%s\\n" "$*" >> "{pane_bell_log}"\n'
                "echo 'silent: every session that wants a pane has one'\n"
                "exit 0\n"
            )
        os.chmod(pane_bell_stub, 0o755)
        # The wrapper's own notifications, moved into the fixture: `freebuff()` reads
        # FREEBUFF_NOTIFY_DIR for its drop watch, its session timer and its stderr log.
        # Without this the stand-in session ending looks like a death to the REAL drop
        # watch and pushes "freebuff dropped" to the owner's phone — measured
        # 2026-09-22, one push per run until this line existed.
        fake_notify = os.path.join(TEST_HOME, ".config", "freebuff-notify")
        os.makedirs(fake_notify, exist_ok=True)
        for name, body in (
            ("drop-bell.py", "#!/bin/sh\nexit 0\n"),
            ("bell.sh", "#!/bin/sh\nexit 0\n"),
            ("session-timer.sh", "#!/bin/sh\nexit 0\n"),
        ):
            with open(os.path.join(fake_notify, name), "w", encoding="utf-8") as fh:
                fh.write(body)
            os.chmod(os.path.join(fake_notify, name), 0o755)
        # A watcher left over from an earlier phase would be ADOPTED by the pane below
        # (same version, so not replaced), and it carries THAT phase's environment: no
        # stall-watch stub, so the check below would ask a process that cannot answer.
        # Stop it first — the session opened here has to start its own watcher.
        run("stop")
        # The wrapper that opens the pane is the SHELL's, not this checkout's: on the
        # machine this suite was written on, `~/.zshrc` defines a `freebuff()` that splits
        # the pane and then runs the CLI. A runner has no such file — the session there ran
        # the bare stub and never opened a pane — so the shell is given a throwaway
        # `ZDOTDIR` and runs the wrapper the REPOSITORY ships, `examples/fb.sh`, with its
        # npm refresh off (that refresh is a network round trip on every launch, and this
        # is a test). `-d` below keeps the distribution's `/etc/zsh` out of it.
        zdot_sess = os.path.join(TEST_HOME, "zdot-session")
        os.makedirs(zdot_sess, exist_ok=True)
        with open(os.path.join(zdot_sess, ".zshrc"), "w", encoding="utf-8") as fh:
            fh.write(
                f'path=({ROOT} $path)\n'
                'export FREEBUFF_NO_REFRESH=1\n'
                f'. {os.path.join(ROOT, "examples", "fb.sh")}\n'
            )
        created = subprocess.run(
            tmux
            + [
                "new-session", "-d", "-s", sess, "-x", "100", "-y", "30",
                "-e", f"FBTODO_HOME={TEST_HOME}",
                # The same named server the `why`/`status` below talk to (`FBTODO_TMUX`).
                # Without it the session's pane and the CLI disagree about a pinned key
                # neither should, and `stale_pin` reports the false positive its own
                # uniform-environment promise rules out.
                "-e", f"FBTODO_TMUX=tmux -L {check_sock}",
                "-e", f"PATH={path}",
                "-e", f"ZDOTDIR={zdot_sess}",
                # ...and no autostart daemon left behind either
                "-e", "FBTODO_NO_AUTOSTART=1",
                # notice a killed pane quickly: the shipped default is deliberately calm
                "-e", "FBTODO_PANE_SECONDS=1",
                "-e", f"FREEBUFF_NOTIFY_DIR={fake_notify}",
                "-e", f"FBTODO_PAUSE={pause_stub}",
                "-e", "FBTODO_PAUSE_SECONDS=1",
                "-e", f"FBTODO_PANE_BELL={pane_bell_stub}",
                "-e", "FBTODO_PANE_BELL_SECONDS=1",
                # ONE command string, so tmux runs it through a shell: given
                # separate argv elements tmux execs them directly, the quotes land
                # in zsh's command text, and the pane dies without a word. PATH is
                # prepended here, after the `.zshrc` above has put the launcher's own
                # directory on it, so `command freebuff` still resolves to the stub
                # session while `command -v fbtodo` finds the launcher.
                f"zsh -d -i -c 'export PATH={stub_bin}:$PATH; fb'",
            ],
            capture_output=True,
        )

        def pane_cmds() -> list[str]:
            p = subprocess.run(
                tmux + ["list-panes", "-t", sess, "-F", "#{pane_current_command}"],
                capture_output=True, text=True,
            )
            return p.stdout.split() if p.returncode == 0 else []

        def is_todo_pane(cmd: str) -> bool:
            """Whether a pane's current command is the todo pane.

            `#{pane_current_command}` is the OS's own name for the process, and it is not
            the same string on both platforms: macOS reports `Python` for the interpreter a
            script is run by, Linux reports `python3`, and a system that answers with the
            script's own name would say `fbtodo`. All three name the same pane, so the test
            is "an interpreter, or the launcher itself", matched case-insensitively, rather
            than one OS's spelling of it.
            """
            return cmd.lower().startswith(("python", "fbtodo"))

        def panes_detail() -> str:
            s = subprocess.run(tmux + ["list-sessions"], capture_output=True, text=True)
            p = subprocess.run(
                tmux + ["list-panes", "-a", "-F", "#{session_name} #{pane_current_command}"],
                capture_output=True, text=True,
            )
            return (
                f"new-session rc={created.returncode} err={created.stderr.strip()!r}; "
                f"sessions={s.stdout.strip()!r}/{s.stderr.strip()!r}; "
                f"panes={p.stdout.strip()!r}/{p.stderr.strip()!r}"
            )

        deadline = time.time() + 20
        while time.time() < deadline and not (
            len(pane_cmds()) >= 2 and any(is_todo_pane(c) for c in pane_cmds())
        ):
            time.sleep(0.3)
        assert any(is_todo_pane(c) for c in pane_cmds()), (
            f"the wrapper did not open a todo pane: {pane_cmds()} | {panes_detail()}"
        )
        say("the shell's wrapper opens a fbtodo pane alongside the session: ok")

        # The stall watch is asked on its own clock while a LOCAL session runs: it is the
        # one notifier that needs `shouldEndTurn`, which only this build writes.
        def pause_calls() -> int:
            try:
                with open(pause_log) as handle:
                    return len([ln for ln in handle if ln.strip()])
            except OSError:
                return 0

        def pause_diag() -> str:
            bits = [f"stub={pause_stub} exec={os.access(pause_stub, os.X_OK)}"]
            for name in ("fbtodo-daemon.log", "fbtodo-pane.log", "fbtodo-state.json"):
                p = os.path.join(TEST_HOME, name)
                try:
                    with open(p) as fh:
                        bits.append(f"{name}=" + fh.read()[-400:].replace("\n", " | "))
                except OSError as exc:
                    bits.append(f"{name}={exc.__class__.__name__}")
            return " ".join(bits)

        deadline = time.time() + 15
        while time.time() < deadline and pause_calls() < 1:
            time.sleep(0.3)
        assert pause_calls() >= 1, (
            f"the watcher never asked the stall watch: {pane_cmds()} | {panes_detail()}"
            f" | {pause_diag()}"
        )
        asked = open(pause_log).read()
        assert "--watch-pid" in asked, (
            f"the stall watch was not given the session: {asked!r}"
        )
        say("the stall watch is asked, with the session, while it runs: ok")

        # The pane watch is asked on its own clock too, and about something else again: a
        # session with no todo pane, or no keeper to put one back. It is given NO session,
        # because the failure it reports is often one this daemon cannot see from inside.
        def pane_bell_calls() -> int:
            try:
                with open(pane_bell_log) as handle:
                    return len([ln for ln in handle if ln.strip()])
            except OSError:
                return 0

        deadline = time.time() + 15
        while time.time() < deadline and pane_bell_calls() < 1:
            time.sleep(0.3)
        assert pane_bell_calls() >= 1, (
            f"the watcher never asked the pane watch: {pane_cmds()} | {panes_detail()}"
        )
        asked = open(pane_bell_log).read()
        assert "--keeper" in asked, (
            f"the pane watch was not told where the keeper's record is: {asked!r}"
        )
        keeper_rec = os.path.join(TEST_HOME, "fbtodo-pane-keeper.pid")
        assert os.path.basename(keeper_rec) in asked, (
            f"the pane watch was pointed at the wrong keeper record: {asked!r} (want {keeper_rec})"
        )
        say("the pane watch is asked, with the keeper's record, while it runs: ok")

        # ...and `status` says which of the three states it is in, like the other three.
        # "Is the pane watcher working" was otherwise only answerable by killing a pane and
        # waiting to see whether it came back.
        assert "pane watch        : on" in run("status").stdout, run("status").stdout
        assert "pane watch        : off" in run("status", "--pane-bell-seconds", "0").stdout, (
            "--pane-bell-seconds 0 did not turn the pane watch off"
        )
        env_no_bell = dict(env, FBTODO_PANE_BELL=os.path.join(TEST_HOME, "nope-bell.py"))
        gone = subprocess.run(
            [sys.executable, FB, "status"], capture_output=True, text=True,
            env=env_no_bell, cwd=CWD, timeout=30,
        )
        assert "pane watch        : none installed" in gone.stdout, gone.stdout
        say("status says whether the pane watch is installed, on, or off: ok")

        # Kill that pane while the session lives. The wrapper cannot put it back — it is
        # inside the CLI by now — so the watcher does, which is the only process here that
        # both outlives the pane and follows the instance.
        def pane_ids() -> list[str]:
            p = subprocess.run(
                tmux + ["list-panes", "-t", sess, "-F", "#{pane_id} #{pane_current_command}"],
                capture_output=True, text=True,
            )
            out = []
            for line in (p.stdout or "").splitlines():
                parts = line.split()
                if len(parts) == 2 and is_todo_pane(parts[1]):
                    out.append(parts[0])
            return out

        def pane_geometry() -> dict:
            return tmux_geometry(tmux)

        def daemon_diag() -> str:
            pid_file = os.path.join(TEST_HOME, "fbtodo-daemon.pid")
            log = os.path.join(TEST_HOME, "fbtodo-daemon.log")
            bits = []
            try:
                with open(pid_file) as fh:
                    bits.append("pid=" + fh.read().strip()[:140])
            except OSError as exc:
                bits.append(f"pid file: {exc.__class__.__name__}")
            try:
                with open(log) as fh:
                    bits.append("log=" + fh.read()[-300:].replace("\n", " | "))
            except OSError:
                bits.append("no log")
            keeper = os.path.join(TEST_HOME, "fbtodo-pane-keeper.pid")
            try:
                with open(keeper) as fh:
                    bits.append("keeper=" + fh.read().strip()[:120])
            except OSError as exc:
                bits.append(f"keeper file: {exc.__class__.__name__}")
            try:
                with open(keeper) as fh:
                    recorded = json.load(fh)
                alived = subprocess.run(
                    ["ps", "-o", "pid=", "-p", str(recorded.get("pid"))],
                    capture_output=True, text=True,
                ).stdout.strip()
                bits.append(f"keeper alive={bool(alived)} {alived}")
            except Exception as exc:  # noqa: BLE001 - diagnostics only
                bits.append(f"keeper alive: {exc.__class__.__name__}")
            try:
                with open(os.path.join(TEST_HOME, "fbtodo-pane.log")) as fh:
                    bits.append("pane log=" + fh.read()[-1500:].replace("\n", " | "))
            except OSError:
                bits.append("no pane log")
            return " ".join(bits)

        first = pane_ids()
        assert first, f"no todo pane to kill: {pane_cmds()} | {panes_detail()} | {daemon_diag()}"
        subprocess.run(tmux + ["kill-pane", "-t", first[0]], capture_output=True)
        deadline = time.time() + 12
        back: list[str] = []
        while time.time() < deadline and not back:
            back = [p for p in pane_ids() if p != first[0]]
            time.sleep(0.3)
        assert back, (
            f"the todo pane was killed and never came back: {pane_cmds()} | {panes_detail()}"
            f" | {daemon_diag()}"
        )
        say("a killed todo pane is put back while the session runs: ok")

        # ...and it comes back in the right PLACE, directly under the pane its own session
        # is drawn in. Asked for 2026-09-22, after a live window showed the list a column
        # over and three rows down, under another pane: the wrapper had split a WINDOW, and
        # tmux answered that with the window's active pane, which is not necessarily the
        # one running freebuff.
        panes_mod = load_fbtodo()
        set_knob(panes_mod, "TMUX_BIN", list(tmux))  # the private server, as the keeper has it
        set_knob(panes_mod, "TASKS_PATH", os.path.join(TEST_HOME, "tasks-unit.json"))

        def todo_pane_and_instance() -> tuple:
            """(pane id, instance pid) of the list pane, read from its own start command."""
            p = subprocess.run(
                tmux + ["list-panes", "-t", sess, "-F", "#{pane_id} #{pane_start_command}"],
                capture_output=True, text=True,
            )
            for line in (p.stdout or "").splitlines():
                parts = line.split(None, 1)
                if len(parts) != 2:
                    continue
                match = re.search(r"--watch-pid\s+(\d+)", parts[1])
                if match:
                    return parts[0], int(match.group(1))
                match = re.search(r"--instance-of\s+(\d+)", parts[1])
                if match:
                    inst = panes_mod.descendant_instance(
                        panes_mod.process_table(), int(match.group(1))
                    )
                    if inst:
                        return parts[0], inst
            return "", 0

        def placement() -> str:
            pane_now, inst_now = todo_pane_and_instance()
            if not pane_now:
                return "no list pane"
            inst_now_pane = panes_mod.freebuff_pane_id(inst_now)
            rects_now = panes_mod.pane_rects()
            where = (
                "under"
                if inst_now_pane
                and panes_mod.placed_beside(inst_now_pane, pane_now, rects_now, "v")
                else "NOT under"
            )
            # Only ever built for a failure message: which tmux the module was pointed at,
            # what a raw call to that server says, and what the module's own readers see.
            raw = subprocess.run(
                tmux + ["list-panes", "-a", "-F", "#{pane_id} #{pane_pid} #{pane_start_command}"],
                capture_output=True, text=True,
            )
            rects_raw = subprocess.run(
                tmux + ["list-panes", "-a", "-F",
                        "#{pane_id}|#{window_id}|#{pane_left}|#{pane_top}"
                        "|#{pane_width}|#{pane_height}"],
                capture_output=True, text=True,
            )
            version = subprocess.run(
                tmux + ["-V"], capture_output=True, text=True
            ).stdout.strip()
            return (
                f"{pane_now} {where} {inst_now_pane}: {rects_now}"
                f" | inst={inst_now} pane={pane}"
                f" | TMUX_BIN={panes_mod.TMUX_BIN!r} tmux={tmux!r}"
                f" | raw rc={raw.returncode} out={raw.stdout.strip()!r} err={raw.stderr.strip()!r}"
                f" | rects rc={rects_raw.returncode} out={rects_raw.stdout.strip()!r}"
                f" | {version}"
                f" | rows={panes_mod.pane_rows()}"
            )

        pane, inst = todo_pane_and_instance()
        assert pane and inst, f"no list pane with an instance: {panes_detail()}"
        inst_pane = panes_mod.freebuff_pane_id(inst)
        assert inst_pane, f"the pane the session is drawn in was not found: {placement()}"
        assert panes_mod.placed_beside(inst_pane, pane, panes_mod.pane_rects(), "v"), (
            f"the list pane is not under its session's pane: {placement()}"
        )
        say("the todo pane sits under the pane its session is drawn in: ok")

        # Drift it for real: a third pane split into the session's own pane pushes the list
        # a row further down, which is the shape the geometry check has to notice. `sleep`
        # on purpose — a shell here would inherit this fixture and could start a watcher.
        drifted = subprocess.run(
            tmux + ["split-window", "-v", "-l", "4", "-d", "-t", inst_pane, "sleep 60"],
            capture_output=True,
        )
        assert drifted.returncode == 0, f"could not create the drifting pane: {drifted.stderr}"
        deadline = time.time() + 10
        while time.time() < deadline:
            if panes_mod.placed_beside(inst_pane, pane, panes_mod.pane_rects(), "v"):
                break
            time.sleep(0.3)
        assert panes_mod.placed_beside(inst_pane, pane, panes_mod.pane_rects(), "v"), (
            f"the keeper did not put the drifted list pane back under its session: {placement()}"
        )
        say("a list pane pushed out of place is moved back under its session: ok")

        # ---- a per-window pin (`fbtodo pin`): the side it opens on and the size it keeps,
        #      filed by session:index and enforced by the keeper like placement is.
        #      FBTODO_TMUX is set for these: run() is a child of THIS shell, whose $TMUX is
        #      the owner's server, and the pin has to land on the private one.
        env["FBTODO_TMUX"] = f"tmux -L {check_sock}"
        # Deliberately NOT aligned with the session here: this CLI's own environment may
        # spell the pin however it likes, because `why`/`status` judge a pane by the
        # SESSION's values (`session_pinned_env`), not the shell they were typed in. The
        # `why` assertions below hold the pane current despite the client's placeholders.
        # The window's own name, asked of tmux rather than assumed: `base-index` is a user
        # setting, and the pin is filed under what the window actually is.
        window_target = subprocess.run(
            tmux + ["display-message", "-t", sess, "-p", "#{session_name}:#{window_index}"],
            capture_output=True, text=True,
        ).stdout.strip()
        assert window_target, panes_detail()

        # ---- the side is the owner's answer too, not only the size. A list dragged off the
        #      bottom of its session and into the column beside it — the LEFT column, which is
        #      the move this was reported with — used to be back under the session inside one
        #      keeper pass, and could never be remembered, because the keeper wrote down the
        #      side IN FORCE rather than the side it saw. Two rules instead: a side fixes the
        #      AXIS and not the edge of it, and a pane found on the other axis is an owner's
        #      arrangement rather than a drift to repair (only a pin still decides a side).
        def rect(left, top, width, height):
            return {"window": "@w", "left": left, "top": top, "width": width, "height": height}

        above = rect(60, -10, 40, 10)
        anchor = rect(60, 1, 40, 10)
        diagonal = rect(101, 12, 40, 8)
        left_of = rect(19, 1, 40, 10)
        right_of = rect(101, 1, 40, 10)
        under = rect(60, 12, 40, 8)
        geom = {"%d": diagonal, "%l": left_of, "%r": right_of, "%u": anchor,
                "%o": above, "%n": under}
        assert panes_mod.placed_beside("%u", "%r", geom, "h")
        assert panes_mod.placed_beside("%u", "%l", geom, "h"), (
            "a list to the LEFT of its session is not accepted as beside it"
        )
        assert not panes_mod.placed_beside("%u", "%n", geom, "h")
        assert panes_mod.placed_beside("%u", "%o", geom, "v"), (
            "a strip ABOVE its session is not accepted as under it"
        )
        assert not panes_mod.placed_beside("%u", "%l", geom, "v")
        assert panes_mod.pane_before(anchor, left_of, "h") and panes_mod.pane_before(
            anchor, above, "v"
        )
        assert not panes_mod.pane_before(anchor, right_of, "h") and not (
            panes_mod.pane_before(anchor, under, "v")
        )
        assert panes_mod.pane_axis(anchor, left_of) == "h"
        assert panes_mod.pane_axis(anchor, under) == "v"
        assert panes_mod.pane_axis(anchor, diagonal) is None, (
            "a pane in the far corner of a grid is on no axis of its anchor"
        )
        # A side in force that nobody pinned stops being enforced on its axis — the pane is
        # `seen` there — and a pin keeps every tooth it had.
        laid = {"side": "v", "side_source": "last", "size": 8, "size_source": "last"}
        kept = panes_mod.kept_layout("%l", "%u", geom, laid)
        assert (kept["side"], kept["side_source"]) == ("h", "seen"), kept
        pinned = dict(laid, side_source="pin:local")
        assert panes_mod.kept_layout("%l", "%u", geom, pinned)["side"] == "v", (
            "a pin no longer decides the side"
        )
        assert panes_mod.kept_layout("%n", "%u", geom, laid)["side_source"] == "last", (
            "a pane still on the side in force was treated as an arrangement"
        )
        assert panes_mod.source_note("seen", "main:1") == "seen (kept, not filed yet)", (
            "the side a pane is kept on has no wording of its own in `why`"
        )
        say("a side fixes the axis, not the edge of it; a pin decides, a kept side is kept: ok")

        # ...and it holds on a real server: the list goes to the LEFT column, and the
        # keeper's next passes have to leave it there — and file it, so the pane a later pass
        # opens in that window opens on the same side.
        moved_left = subprocess.run(
            tmux + ["move-pane", "-d", "-b", "-h", "-s", pane, "-t", inst_pane],
            capture_output=True,
        )
        assert moved_left.returncode == 0, f"could not move the list to the left: {moved_left.stderr}"

        def kept_left() -> bool:
            rects_now = panes_mod.pane_rects()
            a = rects_now.get(inst_pane)
            b = rects_now.get(pane)
            return bool(
                a and b and b["left"] + b["width"] + 1 == a["left"]
                and panes_mod.pane_axis(a, b) == "h"
            )

        assert kept_left(), f"tmux did not put the pane left of its session: {placement()}"

        def filed_local() -> dict:
            """This fixture window's entry in the remembered layout, or {}."""
            try:
                with open(os.path.join(TEST_HOME, "fbtodo-last.json")) as handle:
                    doc = json.load(handle)
            except (OSError, ValueError):
                return {}
            for key, entry in (doc if isinstance(doc, dict) else {}).items():
                if key.startswith(f"{sess}:") and isinstance(entry, dict):
                    return entry
            return {}

        deadline = time.time() + 15
        while time.time() < deadline and not (kept_left() and filed_local().get("side") == "h"):
            time.sleep(0.3)
        assert kept_left(), (
            f"the keeper put a hand-moved list pane back where it was opened: {placement()}"
        )
        assert filed_local().get("side") == "h", (
            f"the other side was kept but never filed for the next pane: {filed_local()!r}"
        )
        assert filed_local().get("before") is True, (
            f"the edge the pane was on is not in the remembered layout: {filed_local()!r}"
        )
        why_left = run("why", "--window", window_target).stdout
        assert f"side=h (remembered)" in why_left and "MISPLACED" not in why_left, why_left
        rows_left = json.loads(run("why", "--json", "--window", window_target).stdout)
        mine_left = [rec for rec in rows_left["panes"] if rec["pane"] == pane]
        assert mine_left and mine_left[0]["placed"] is True, mine_left
        assert (mine_left[0]["side"], mine_left[0]["side_source"]) == ("h", "last"), mine_left
        say("a list pane moved to the other side of its session stays there and is filed: ok")

        # ---- and that remembered edge is what makes the NEXT pane open where this one is:
        #      `split-window -h` puts a new pane on the right and `-v` under, so a list the
        #      owner keeps in the left column (or above the session) comes back there only
        #      because the opener is told. Asserted on the argv the opener would run — a live
        #      reopen is one more split of this fixture's window, and it moves the panes the
        #      checks after this one are about.
        calls = []
        real_tmux = panes_mod.tmux_run

        def record(*argv):
            # Only the split is answered from here: `pane_layout` asks tmux for the window
            # key on its way in, and an answer of "" would read as an unset pin AND an
            # unremembered window, which is how this check first passed the default side.
            calls.append([str(part) for part in argv])
            return "%99" if argv and argv[0] == "split-window" else real_tmux(*argv)

        set_knob(panes_mod, "tmux_run", record)
        try:
            panes_mod.local_pane_open(TEST_HOME, inst, window_target)
        finally:
            set_knob(panes_mod, "tmux_run", real_tmux)
        split = [call for call in calls if call[0] == "split-window"]
        assert split and "-b" in split[0] and "-h" in split[0], (
            f"the opener was not told the edge the pane was left on: {calls}"
        )
        say("the opener splits on the edge the pane was left on, not the trailing one: ok")

        # ...and when a pass DOES have to move it back, it returns the pane to the edge it was
        # already on: a pane split in BETWEEN it and its session pushes it off the anchor, and
        # the repair has to bring it back to the left rather than to the right, which is where
        # a fresh `split-window` would have put it.
        between = subprocess.run(
            tmux + ["split-window", "-b", "-h", "-l", "10", "-d", "-t", inst_pane,
                    "sleep", "60"],
            capture_output=True,
        )
        assert between.returncode == 0, f"could not split a pane in between: {between.stderr}"
        assert not kept_left(), f"the pane was still placed — nothing to repair: {placement()}"
        deadline = time.time() + 12
        while time.time() < deadline and not kept_left():
            time.sleep(0.3)
        assert kept_left(), (
            f"the repair did not keep the edge the pane was on: {placement()}"
        )
        say("a repair puts the pane back on the edge it was on, not on the default one: ok")
        # ...and the fixture goes back to the arrangement the rest of the block expects. The
        # remembered side follows it on its own (nothing here waits for that).
        subprocess.run(
            tmux + ["move-pane", "-d", "-v", "-s", pane, "-t", inst_pane], capture_output=True
        )
        # ...with the two stand-ins the checks above split in taken back out. Each is a
        # `sleep 60` the keeper may move about on its own, and a window crowded with them is
        # a size tmux can refuse to give a pinned pane: the boundary it would resize against
        # is already at the other pane's minimum, so the pin lands at a width nobody asked
        # for (seen on a runner: the session squeezed to one column, the list stuck at six).
        # What a pin promises is the arrangement, and that is what this block is about.
        for row in subprocess.run(
            tmux + ["list-panes", "-t", sess, "-F", "#{pane_id} #{pane_start_command}"],
            capture_output=True, text=True,
        ).stdout.splitlines():
            if "sleep 60" in row:
                subprocess.run(tmux + ["kill-pane", "-t", row.split()[0]], capture_output=True)

        def pinned_ok(side: str, size: int) -> bool:
            inst_pane_now = panes_mod.freebuff_pane_id(inst)
            rects = pane_geometry()
            a, b = rects.get(inst_pane_now), rects.get(pane)
            if not a or not b:
                return False
            if side == "h":
                return b[0] == a[0] + a[2] + 1 and b[2] == size
            return b[1] == a[1] + a[3] + 1 and b[3] == size

        set_pin = run("pin", "--window", window_target, "--side", "h", "--size", "10", "--json")
        assert set_pin.returncode == 0, (set_pin.returncode, set_pin.stderr)
        assert json.loads(set_pin.stdout)["pin"] == {"side": "h", "size": 10}, set_pin.stdout
        deadline = time.time() + 12
        while time.time() < deadline and not pinned_ok("h", 10):
            time.sleep(0.3)
        assert pinned_ok("h", 10), (
            f"a pinned side/size was not applied to the list pane: {pane_geometry()}"
            f" pin={set_pin.stdout.strip()!r}"
            f" | list={pane} inst_pane={inst_pane} window={window_target!r}"
            f" | {placement()}"
            f" | cmds={pane_cmds()}"
            f" | {daemon_diag()}"
        )
        say("a window pin puts the list pane where it says, at the size it says: ok")

        assert json.loads(run("pin", "--list", "--json").stdout) == {
            window_target: {"side": "h", "size": 10}
        }, "pin --list does not show what was pinned"
        assert run("pin", "--window", window_target, "--clear").returncode == 0
        assert json.loads(run("pin", "--list", "--json").stdout) == {}, (
            "--clear dropped the pin but --list still shows one"
        )
        assert run("pin", "--side", "sideways").returncode != 0, "a bad --side was accepted"
        say("pin --list shows the pin, --clear drops it, a bad side is refused: ok")

        # ---- one pin, one window. The flat halves are the window's answer; a pin file
        #      written while a window held two list panes may carry its answer under the
        #      `local` role instead, and that half is read as the window's own — the role
        #      it was filed for is the only role there is now, so nothing is lost and an
        #      existing pins file keeps working. Asserted against the module's own lookup,
        #      not through tmux: it is a rule.
        set_knob(panes_mod, "PINS_PATH", os.path.join(TEST_HOME, "pins-unit.json"))
        set_knob(panes_mod, "LAST_PATH", os.path.join(TEST_HOME, "last-unit.json"))
        with open(panes_mod.PINS_PATH, "w") as fh:
            json.dump({window_target: {"side": "h", "size": 10}}, fh)
        flat = panes_mod.pane_layout(window_target)
        assert (flat["side"], flat["side_source"]) == ("h", "pin"), flat
        assert (flat["size"], flat["size_source"]) == (10, "pin"), flat
        with open(panes_mod.PINS_PATH, "w") as fh:
            json.dump({window_target: {"side": "h", "size": 10, "local": {"size": 6}}}, fh)
        legacy = panes_mod.pane_layout(window_target)
        assert (legacy["side"], legacy["side_source"]) == ("h", "pin"), legacy
        assert (legacy["size"], legacy["size_source"]) == (6, "pin:local"), legacy
        say("a pin's flat halves answer for the window; a legacy role half still applies: ok")

        # ---- and a pin works through the live path: filed for the window, applied to the
        #      pane, which has to be moved AND resized by it.
        scoped = run("pin", "--window", window_target, "--side", "v", "--size", "8", "--json")
        assert scoped.returncode == 0, (scoped.returncode, scoped.stderr)
        doc = json.loads(scoped.stdout)
        assert doc["pin"] == {"side": "v", "size": 8}, doc
        assert doc["layout"]["side_source"] == "pin", doc
        deadline = time.time() + 12
        while time.time() < deadline and not pinned_ok("v", 8):
            time.sleep(0.3)
        assert pinned_ok("v", 8), (
            f"a pin did not move/resize the pane: {pane_geometry()}"
            f" pin={scoped.stdout.strip()!r}"
        )
        say("a pin is filed for the window and moves that pane: ok")

        # ---- `fbtodo why`: the side and size in force WITH the source that supplied them,
        #      the pane it is placed against, and whether it is actually there.
        why = json.loads(run("why", "--json", "--window", window_target).stdout)
        mine = [rec for rec in why["panes"] if rec["pane"] == pane]
        assert mine, why
        mine = mine[0]
        assert mine["window"] == window_target, mine
        assert mine["stale_pin"] is False, mine      # a pane the keeper just opened is on the pin
        # ...and it stays current even when the shell running `why` carries values the
        # session never had: the judgment is the SESSION's (`session_pinned_env`), not the
        # client's, so two keys this shell invents cannot make the keeper's own pane stale.
        odd_env = dict(env, FBTODO_LOCKS_BELL="/client-only", FBTODO_ASK="/client-only")
        why_odd = json.loads(subprocess.run(
            [sys.executable, FB, "why", "--json", "--window", window_target],
            capture_output=True, text=True, env=odd_env, cwd=CWD, timeout=30,
        ).stdout)
        odd_mine = [rec for rec in why_odd["panes"] if rec["pane"] == pane][0]
        assert odd_mine["stale_pin"] is False, odd_mine
        assert mine["side_source"] == "pin" and mine["size_source"] == "pin", mine
        assert mine["anchor"] == inst_pane and mine["placed"] is True, mine
        why_text = run("why", "--window", window_target).stdout
        assert f"pin ({window_target})" in why_text, why_text
        assert "placed" in why_text and str(mine["size"]) in why_text, why_text
        say("why names the source that decided each half, whether the pane is placed, and "
            "whether it predates the pin: ok")
        assert run("why", "--window", "nosuch:99").returncode != 0, "a bad --window was accepted"
        run("pin", "--window", window_target, "--clear")
        assert json.loads(run("pin", "--list", "--json").stdout) == {}, (
            "the window's pin survived --clear"
        )
        say("why refuses a window that does not exist; --clear drops the window's pin too: ok")
        env.pop("FBTODO_TMUX", None)

        # ...and now the session may end (see the release file at `stub`). The pane must NOT
        # go with it: a pane is the reader's window on a list, and this is the rule the whole
        # pane-lifetime change rests on — no process exit and no ended session takes it away.
        # What does end it is named elsewhere in this suite and checked there: `--stale-after`
        # (the `a stale list closes the pane` check), Ctrl-C, and the window itself. So the
        # pane here is waited out — it has to still be there when the session's process is
        # gone — and the tmux server is taken down after, which is the one thing that does
        # close it.
        with open(release, "w"):
            pass
        gone = None
        deadline = time.time() + 20
        while time.time() < deadline:
            cmds = pane_cmds()
            if not any(is_todo_pane(c) for c in cmds):
                gone = cmds
                break
            time.sleep(0.3)
        assert gone is None, f"todo pane closed itself when the session ended: {gone}"
        say("the pane outlives the session that opened it: ok")
        subprocess.run(tmux + ["kill-server"], capture_output=True)




    print(f"\n{len(ok)} checks passed")
finally:
    # stop any watcher this script started (never leave one behind following the
    # user's real freebuff process), then delete only our own directory
    try:
        subprocess.run(
            [sys.executable, FB, "stop", "--quiet"], env=env, cwd=CWD, timeout=15,
            capture_output=True,
        )
    except Exception:
        pass
    # ...and only the servers THIS run started: the names used to be fixed strings, so this
    # cleanup reached into a concurrent run's tmux and killed the panes it was inspecting.
    subprocess.run(["tmux", "-L", sock("fbtchecksock"), "kill-server"], capture_output=True)
    subprocess.run(["tmux", "-L", sock("fbtchecksockremote"), "kill-server"], capture_output=True)
    subprocess.run(["tmux", "-L", sock("fbtchecksockwait"), "kill-server"], capture_output=True)
    if os.path.realpath(TEST_HOME).startswith(os.path.realpath(REAL_HOME) + os.sep):
        shutil.rmtree(TEST_HOME, ignore_errors=True)
