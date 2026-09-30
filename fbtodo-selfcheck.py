#!/usr/bin/env python3
"""Self-check for `fbtodo`, the copy sitting next to this file.

Runs everything against a throwaway FBTODO_HOME (`.freebuff/fbtodo-test/`) and a
fake "freebuff instance" (a sleep process) whose death must stop the watcher:

    python3 fbtodo-selfcheck.py

The suite drives real tmux servers it starts itself and takes ~90-160 s. It cleans
up only that one directory — created by this script, verified by path — never a
parent.
"""
import ast
import builtins
import importlib
import json
import os
import re
import signal
import sqlite3
import subprocess
import shutil
import sys
import time

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
FB = os.path.join(HERE, "fbtodo")
TEST_HOME = os.path.join(HOME, ".freebuff", "fbtodo-test")
REAL_HOME = os.path.join(HOME, ".freebuff")
PANES = os.path.join(HERE, "fbtodo")
# The program is the package under `src/`; `FB` above is the launcher beside it, which is
# what every subprocess phase runs and what `self_argv` names back to us.
SRC = os.path.join(HERE, "src")
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
# for phases that only assert a watcher came up; the NAS block below points its own
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
NAS_PATCH_FIXTURE = os.path.join(PATCH_HOME, "nas-patch.log")
NAS_ALERT_FIXTURE = os.path.join(PATCH_HOME, "nas-notify.log")
for _key, _val in (
    ("FBTODO_PATCH_LOG", PATCH_FIXTURE),
    ("FBTODO_ALERT_LOG", ALERT_FIXTURE),
    ("FBTODO_PATCH_META", META_FIXTURE),
    ("FBTODO_NAS_PATCH_LOG", NAS_PATCH_FIXTURE),
    ("FBTODO_NAS_ALERT_LOG", NAS_ALERT_FIXTURE),
):
    os.environ[_key] = _val
    env[_key] = _val
PATCH_FIXTURE_STAMP = ""  # filled in with the fixtures themselves, below


def keeper_aim() -> dict:
    """Live `pane-watch` processes → the tmux server each one is aimed at, from its own env.

    The environment is the only place that decision is written down (`tmux_identity()`
    takes FBTODO_TMUX, then TMUX, then the default server), and it is what makes a keeper
    started by a TEST hook inside the owner's own pane dangerous: it goes off keeping the
    OWNER's panes with the test state root — invisible (its log is a file in a home the
    suite wipes) and immortal (the owner's server always has panes and a freebuff, so it
    never reaches the pass that would let go), so it went on moving the owner's list pane
    back to the default side for as long as it lived. Keyed by pid, so a caller can tell
    what THIS run added from what was already on the machine.
    """
    aims = {}
    listing = subprocess.run(
        ["ps", "-eo", "pid=,args="], capture_output=True, text=True
    ).stdout or ""
    for line in listing.splitlines():
        pid, _, args = line.strip().partition(" ")
        if not pid.isdigit() or "pane-watch" not in args or "grep" in args:
            continue
        try:
            with open(f"/proc/{pid}/environ", "rb") as handle:  # linux
                blob = handle.read().decode("utf-8", "replace")
        except OSError:  # macOS: `ps -E` appends the environment to the args column
            blob = subprocess.run(
                ["ps", "-Eww", "-p", pid], capture_output=True, text=True
            ).stdout
        fields = {}
        for token in blob.replace("\0", " ").split():
            key, _, value = token.partition("=")
            if value and key in ("FBTODO_HOME", "FBTODO_TMUX", "TMUX"):
                fields[key] = value
        aims[pid] = fields
    return aims


def keeper_leaks(before: dict, home: str, server: str | None) -> list:
    """Keepers this run ADDED that keep `server`'s panes while running with `home`.

    Nothing should ever be one: a keeper reads the state root it was given and it keeps
    whatever panes the tmux server in its environment has, so a test home pointed at a
    live server is a keeper that edits the owner's layout from a scratch file.
    """
    out = []
    for pid, fields in keeper_aim().items():
        if pid in before or fields.get("FBTODO_HOME") != home:
            continue
        if (fields.get("FBTODO_TMUX") or fields.get("TMUX") or None) == server:
            out.append((pid, fields.get("TMUX"), fields.get("FBTODO_TMUX")))
    return out


ok = []
START = time.monotonic()  # the suite's clock; `say()` reports each check's cost against it


def run(*args, timeout=30, check=False):
    p = subprocess.run(
        [sys.executable, FB, *args], capture_output=True, text=True, env=env,
        cwd=CWD, timeout=timeout,
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
    servers, keeper passes, NAS fixtures), so the elapsed seconds since the last check go
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
#   python3 fbtodo-selfcheck.py --list
#   python3 fbtodo-selfcheck.py --only local-session
#   python3 fbtodo-selfcheck.py --only 1 --only "nas pane"
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

if os.path.exists(TEST_HOME):
    shutil.rmtree(TEST_HOME)
os.makedirs(TEST_HOME, mode=0o700)
# ...but a wiped test home is not a fresh machine: a NAS pane watcher started by an earlier
# run's fixture ssh is not tied to the directory, and a run that dies before that phase's own
# cleanup leaves it polling forever against a path this run has just recreated. Found
# 2026-09-29: an 8-minute-old `fbtodo nas -f` (pid 15961, fixture ssh under the test home) was
# still rewriting fbtodo-nas-pane.json on every poll, so `nas --stop` said "not running" while
# `--status` read a live-looking state and the nas-live phase went red on a healthy machine —
# with four runs spent proving it was not the code under test. Only watchers whose argv names
# the fixture ssh are touched; the owner's real nas watcher does not mention this path.
for _line in subprocess.run(
    ["ps", "-Ao", "pid=,args="], capture_output=True, text=True
).stdout.splitlines():
    _pid, _, _args = _line.strip().partition(" ")
    if TEST_HOME in _args and "fbtodo nas" in _args:
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
with open(NAS_PATCH_FIXTURE, "w") as fh:
    fh.write("2026-01-01T00:00:00Z converge=ok patch=incomplete binary=9.9.9-nas [1 bytes]\n")
    fh.write("    freebuff: local CLI patches incomplete (no window for fixture-window);"
             " the rest were applied\n")
with open(NAS_ALERT_FIXTURE, "w") as fh:
    fh.write('2026-01-01 00:00:01 sent ntfy incomplete (2f14bfcf14a4d794) — http 200 {"id":"x"}\n')

try:
    state_path = os.path.join(TEST_HOME, "fbtodo-state.json")
    lock_path = os.path.join(TEST_HOME, "fbtodo-daemon.pid")

    # A CLI root of our own. This block proves the watcher reads a real CLI journal, and it
    # used to read the OPERATOR's live one — which made it pass or fail with whatever the
    # owner's session happened to be doing at that second. A session that has just dropped a
    # finished list is legitimately list-less, and that is not the watcher's fault.
    cli_root = os.path.join(TEST_HOME, "cliwatch")
    chat_dir = os.path.join(cli_root, "billthuan1", "chats", "2026-01-01T00-00-00.000Z")
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
    daemon = subprocess.Popen(
        [sys.executable, FB, "daemon", "--foreground", "--quiet",
         "--instance-pid", str(victim.pid), "--cwd", CWD, "-i", "0.2",
         "--cli-root", cli_root],
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

    # ---- a watcher whose lock vanishes stops instead of re-creating its home
    victim_l = spawn_quiet("sleep", "600")
    daemon_l = subprocess.Popen(
        [sys.executable, FB, "daemon", "--foreground", "--quiet",
         "--instance-pid", str(victim_l.pid), "--cwd", CWD, "-i", "0.2"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, start_new_session=True, env=env, cwd=CWD,
    )
    deadline = time.time() + 10
    while time.time() < deadline and not os.path.exists(lock_path):
        time.sleep(0.1)
    os.unlink(lock_path)
    assert daemon_l.wait(timeout=15) == 0, "watcher ignored a removed lock"
    assert not os.path.exists(lock_path), "a stopped watcher left a lock"
    kill_tree(victim_l)
    say("a removed lock stops the watcher without resurrecting its home: ok")

    # ---- json/bar/snap contracts, against the same fixture root the watcher read: the
    # operator's live session is not a fixture and cannot be asserted about.
    j = json.loads(run("json", "--cli-root", cli_root).stdout)
    assert j.get("backend") == "cli" and j.get("todos"), j
    bar_out = run("bar", "--cli-root", cli_root).stdout
    assert bar_out.strip().startswith("todos "), bar_out
    assert "no conversation DB found" not in run("snap", "--cli-root", cli_root).stderr
    say("json / bar / snap subcommands: ok")

    # ---- the golden files: the same three contracts, byte for byte. Their own fixture
    #      state is fed through the same functions the commands call, with the clock frozen
    #      and the state directory empty, so a change to what the pane prints is a DIFF to
    #      read rather than a surprise in the terminal.
    gold = os.path.join(HERE, "tests", "golden.py")
    run_gold = lambda where=None: subprocess.run(  # noqa: E731
        [sys.executable, gold] + (["--golden", where] if where else []),
        capture_output=True, text=True, env=env, cwd=CWD, timeout=180,
    )
    g = run_gold()
    assert g.returncode == 0, (g.returncode, g.stdout[-400:], g.stderr[-2000:])
    # ...and it CAN fail: a checker nobody has seen fail is a checker nobody can trust.
    mutated = os.path.join(TEST_HOME, "golden-mutated")
    shutil.rmtree(mutated, ignore_errors=True)
    shutil.copytree(os.path.join(HERE, "tests", "golden"), mutated)
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
    say("doctor: a healthy machine passes, and a missing tool fails with a non-zero exit: ok")

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
    # record nothing reads, and a second keeper would start on top of it.
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

    # ---- the source seam: three readers of three machines' transcripts, one protocol, and
    #      a dispatch that is a loop. What matters is not that the loop exists but that the
    #      contract holds for every source — a name, the backend it reports, its own answer
    #      for "there is nothing here" — and that `-s` picks the sources it documents.
    class _Ask:
        def __init__(self, mode: str):
            self.source = mode

    def asked(mode: str) -> list:
        return [src.name for src in module.sources_for(_Ask(mode))]

    assert sorted(module.SOURCES) == ["cli", "desktop", "nas"], sorted(module.SOURCES)
    for name, cls in (("cli", "CliSource"), ("desktop", "DesktopSource"),
                      ("nas", "NasSource")):
        src = module.SOURCES[name]
        assert isinstance(src, getattr(module, cls)), (name, type(src))
        assert (src.name, src.backend) == (name, name), (src.name, src.backend)
        for method in ("find", "describe", "miss"):
            assert getattr(type(src), method) is not getattr(module.Source, method), (name, method)
        assert src.miss().get("error"), (name, src.miss())
    assert asked("auto") == ["cli", "desktop"], asked("auto")
    assert asked("cli") == ["cli"] and asked("desktop") == ["desktop"] and asked("nas") == ["nas"]
    # `-s cli` asks ONE source: with no journal in this directory the answer is an error,
    # not the desktop store — which is what the whole seam has to keep true
    assert module.SOURCES["cli"].miss()["error"] == "no CLI chat for this directory"
    assert module.SOURCES["desktop"].miss()["error"] == "no conversation DB found"
    say("sources: one protocol, three readers, and the order `-s` asks them in: ok")

    old = module.finish_state(
        {"session": "S1", "todos": [{"task": "a", "completed": True}]}, None
    )
    new_session_empty = module.finish_state({"session": "S2", "todos": []}, old)
    assert new_session_empty["cleared"] is True, new_session_empty
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
            "now": None, "nudge": None}
    was = dict(done, list_version=4, list_id="x")
    after = module.finish_state(dict(done, turn={"start_ms": 2000}), was)
    assert after["todos"] == [] and after["total"] == 0 and after["done"] == 0, after
    assert after["cleared"] is True and after["cleared_turn"] is True, after
    assert after["goal"] is None, after          # the heading described the dropped list
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
    # a list written INSIDE this turn is this turn's own, however finished it looks
    inside = dict(done, ts=3000, source_updated_ms=3000, turn={"start_ms": 2000})
    kept = module.finish_state(inside, was)
    assert len(kept["todos"]) == 1 and not kept["cleared_turn"], kept
    # the NAS and desktop paths carry no turn clock; a newer request is enough there
    nas_ish = module.finish_state(dict(done, now="something new"), was)
    assert nas_ish["todos"] == [] and nas_ish["cleared_turn"] is True, nas_ish
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
    say("desktop: a pane stacks the live threads, each under its own heading: ok")

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
    assert "big goal · — none stated" in text_none, text_none
    say("big goal: with no agent line the heading says so instead of quoting you: ok")

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
        ("continue with the NAS work", False),
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

    # without the rule in ~/AGENTS.md there is no line at all: the convention IS the
    # feature, so the file and the tool's budget are checked together
    agents_md = os.path.join(HOME, "AGENTS.md")
    agents_text = open(agents_md, encoding="utf-8").read() if os.path.isfile(agents_md) else ""
    assert "`Goal:`" in agents_text, "~/AGENTS.md lost the `Goal:` convention the pane reads"
    assert "concise rewrite" in agents_text, "~/AGENTS.md no longer asks for a concise goal"
    assert f"{module.GOAL_MAX_CHARS} characters" in agents_text, (
        f"~/AGENTS.md and GOAL_MAX_CHARS ({module.GOAL_MAX_CHARS}) disagree on the budget"
    )
    say("big goal: ~/AGENTS.md asks for a line that fits, at the tool's own budget: ok")

    # ...and the other half of the convention: a list is only as true as its last call, so
    # the rule has to ask for the update as each step lands rather than in a batch
    assert "the moment a step lands" in agents_text, (
        "~/AGENTS.md no longer asks for the list to be updated as steps land"
    )
    assert "batch" in agents_text, "~/AGENTS.md dropped the reason it must not be batched"
    assert "On a bare continuation" in agents_text and "nudge" in agents_text, (
        "~/AGENTS.md no longer asks for a fresh list on `continue` — the pane's nudge line "
        "would then nag about a rule nobody has"
    )
    assert "steps in the list, not an epilogue" in agents_text, (
        "~/AGENTS.md no longer asks for the checks to BE steps in the list — a list that "
        "stops at the implementation hides the verification from the pane"
    )
    say("the list rule: ~/AGENTS.md asks for the update the moment a step lands: ok")
    say("the list rule: and for a re-written list before continuing from a nudge: ok")
    say("the list rule: and for the checks themselves to be steps of it: ok")

    # the NAS program carries its own copy of the heading rule (it runs standalone out
    # there), so both patterns are compared on the forms that matter
    # Only the regex line is executed: the program itself wants argv and a store.
    nas_ns = {}
    exec(
        "import re\n"
        + "\n".join(
            line for line in module.NAS_EXTRACT.splitlines() if line.startswith("GOAL_RE = ")
        ),
        nas_ns,
    )
    for sample in ("Goal: x", "**Goal:** y", "- Goal: z", "we discussed the goal: no"):
        assert bool(module.GOAL_LINE_RE.match(sample)) == bool(
            nas_ns["GOAL_RE"].match(sample)
        ), sample
    say("big goal: the NAS extractor's rule matches the local one: ok")

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
    assert module.label_class("Deploy to the NAS") == "deploy"
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

    # ---- the pane exits when its instance disappears
    victim2 = spawn_quiet("sleep", "600")
    time.sleep(0.2)
    import pty
    import select

    pid, fd = pty.fork()
    if pid == 0:
        os.environ["FBTODO_HOME"] = TEST_HOME
        os.execv(sys.executable, [sys.executable, PANES, "pane", "--watch-pid",
                                 str(victim2.pid), "--no-daemon", "-i", "0.2"])
    time.sleep(1.5)
    kill_tree(victim2)
    # Drain the pane's pty while waiting. The pane paints into this pty and a terminal
    # always reads it; nobody here did, so once the 64 KB buffer filled the pane sat in
    # write() and could not reach the top-of-loop liveness check — measured 2026-09-23:
    # the same pane, pty drained, exits 0.20 s after its instance dies (that is the
    # behaviour under test); undrained, this check spent 11.8 s and looked like a slow
    # pane. The test, not the tool, was the slow part.
    deadline = time.time() + 10
    status = None
    while time.time() < deadline:
        while select.select([fd], [], [], 0)[0]:
            try:
                if not os.read(fd, 65536):
                    break
            except OSError:  # the child closed its side
                break
        wpid, status = os.waitpid(pid, os.WNOHANG)
        if wpid:
            break
        time.sleep(0.05)
    os.close(fd)
    assert status is not None, "pane did not exit after its instance died"
    assert os.waitstatus_to_exitcode(status) == 0, os.waitstatus_to_exitcode(status)
    say("pane closes itself when the instance dies: ok (exit 0)")

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
    assert module.render(wide_state, False, width=80, now_ms=1000) != module.render(
        wide_state, False, width=80, now_ms=2000
    ), "footer clock does not move — a still pane would look frozen"

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
    assert "🎯 Goal:" in rich and "✔" in rich and "➔" in rich, rich
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
    # the NAS formats: the hook's own line with its complaint under it, and the notifier's,
    # whose ` — http 200 {…}` transport reply is not the row's business
    nas_p = module.nas_patch_from_tail("\x1c".join([
        "2026-01-01T00:00:00Z converge=ok patch=incomplete binary=9.9.9-nas [1 bytes]",
        "    freebuff: local CLI patches incomplete (no window for fixture-window);"
        " the rest were applied",
    ]))
    assert nas_p["outcome"] == "incomplete" and nas_p["severity"] == "bad", nas_p
    assert "no window for fixture-window" in nas_p["reason"], nas_p
    assert module.nas_patch_from_tail("") is None
    nas_p = module.nas_patch_from_tail(
        "2026-01-01T00:00:00Z converge=ok patch=ok binary=9.9.9-nas [1 bytes]"
    )
    assert nas_p["outcome"] == "ok" and nas_p["reason"] == "", nas_p
    nas_a = module.alert_from_lines([
        '2026-01-01 00:00:01 sent ntfy incomplete (2f14bfcf14a4d794) — http 200 {"id":"x"}',
    ], "fixture")
    assert nas_a["kind"] == "sent" and nas_a["text"] == "sent ntfy incomplete", nas_a
    nas_a = module.alert_from_lines([
        "2026-01-01 00:00:02 resolved — the patches are in place again",
    ], "fixture")
    assert nas_a["kind"] == "resolved" and nas_a["severity"] == "ok", nas_a
    nas_a = module.alert_from_lines([
        "2026-01-01 00:00:03 muted — failed (7be3655000448c52) not sent",
    ], "fixture")
    assert nas_a["kind"] == "muted" and nas_a["severity"] == "warn", nas_a
    assert module.alert_from_lines([], "fixture") is None
    # The NAS container logs UTC while this Mac's kit logs LOCAL time, and both use the
    # space-separated form — so the source has to say which. Guessing made a 28-minute-old
    # NAS alert read as `0s ago`: its UTC stamp is a future time on a PDT clock.
    utc_stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 1800))
    utc_alert = module.alert_from_lines(
        [f"{utc_stamp} sent ntfy incomplete"], "fixture", utc=True
    )
    assert 1740 < utc_alert["age_s"] < 1890, utc_alert
    say("patch row: the NAS notifier's clock is UTC, and its ages are read as such: ok")
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
    # no heading written means no heading row at all — the old `— none stated` spent one
    # of a fixed-height strip's rows saying nothing
    bare = ansi.sub("", module.render(
        dict(rich_state, goal=None), True, watching=999, width=46, height=20,
        now_ms=SWEEP_NOW,
    ))
    assert "🎯" not in bare and "none stated" not in bare, bare
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
    goal_row = next(line for line in est_lines if "GOAL" in line)
    # Two numbers, not three: what this row has and nowhere else has is how much is behind
    # the owner and how much the whole thing is. The third it used to print — the time
    # left — is the `EST REM` on the bar row above, and saying it twice was the repetition
    # the footer was carrying.
    assert "30s spent" in goal_row and "4m00s total" in goal_row, goal_row
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
        goal_row = next(line for line in painted.splitlines() if "🎯" in line)
        assert "\x1b[38;2;230;237;243m" in goal_row, repr(goal_row)
        active_row = next(line for line in painted.splitlines() if "➔" in line)
        assert "\x1b[1;38;2;230;237;243m" in active_row, repr(active_row)
        # ...with its estimate as an accent badge, which is what tells the row apart from
        # `○ ~2m` on the steps that have not started
        assert "\x1b[1;36m" in active_row and "[" in active_row, repr(active_row)
        assert "\x1b[2;38;2;255;136;0mDiagnose" in grey_row, repr(grey_row)
        assert "\x1b[38;2;255;136;0m🎯 Goal:" in painted, repr(painted)
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
        #      someone had thought of, so a thread's title, a NAS `fb_dir`, a tool name in
        #      `tool_calls` and every `turn` field were printed as written. A fully populated
        #      state (stacked threads, a patch, an alert, the action feed, a NAS observation, a
        #      turn) is copied once per string leaf, that one string replaced by a payload, and
        #      rendered plain and rich: nothing but SGR may come out. The enum fields the tool
        #      itself owns (backend/source/status/goal_source/schema) are left alone by design.
        enum_keys = {"backend", "source", "status", "goal_source", "schema"}
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
            nas={"dir": "/volume1/proj", "live": True, "fb": "1", "fb_dir": "/volume1/proj",
                 "fb_project": "proj", "alive": True, "size": 10, "mtime_ms": SWEEP_NOW,
                 "has_transcript": True, "unchanged": False},
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
            tool does not own. A value under an enum key is offered to nobody — the filter is
            told to leave those alone, so injecting there would test the skip list, not the
            walk.
            """
            out = []
            if isinstance(obj, str):
                return [payload]
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if isinstance(key, str) and key not in enum_keys:
                        renamed = dict(obj)
                        del renamed[key]
                        renamed[payload] = value
                        out.append(renamed)
                    if key in enum_keys:
                        continue
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
    for sess, factor in (("S1", 1.0), ("S2", 1.0), ("S3", 1.0), ("S4", 1.0)):
        for n in range(4):
            duel_log[mod.task_key(sess, f"step {n}")] = {
                "started_ms": t0, "done_ms": t0 + 100_000, "model": "m/e",
                "fc": {"pace": 100_000, "blend": int(100_000 * 3 * factor)},
            }
    rows = mod.ledger_rows(duel_log, t0 + 1000, None, "m/e")
    duel = mod.rung_duel(rows, "pace", "blend")
    assert duel["steps"] == 16 and duel["sessions"] == 4, duel
    assert duel["share_steps"] == 1.0 and duel["share_resamples"] >= 0.95, duel
    assert duel["winner"] == "pace" and duel["resamples"] == mod.DUEL_RESAMPLES, duel
    # ...and a rung nobody has scored is not a winner by default: no pairing, no verdict
    odd = mod.rung_duel(rows, "pace", "shape")
    assert odd["steps"] == 0 and odd["winner"] is None, odd
    # a coin toss reads as one: the intervals overlap and the shares sit near a half. The split
    # is written out rather than drawn, because the point of THIS check is that a null result
    # is reported as a null result — a fixture that hashed its way to 12/4 across four sessions
    # would be testing the hash, and would fail once in every thirteen runs.
    tie_log = {}
    for sess, wins in (("S1", 3), ("S2", 1), ("S3", 3), ("S4", 1)):
        for n in range(4):
            tie_log[mod.task_key(sess, f"step {n}")] = {
                "started_ms": t0, "done_ms": t0 + 100_000, "model": "m/e",
                "fc": {"pace": 100_000 if n < wins else 200_000, "blend": 150_000},
            }
    tie_rows = mod.ledger_rows(tie_log, t0 + 1000, None, "m/e")
    tied = mod.rung_duel(tie_rows, "pace", "blend")
    assert tied["steps"] == 16 and tied["share_steps"] == 0.5, tied
    assert tied["winner"] is None, tied
    assert 0.2 <= tied["share_resamples"] <= 0.8, tied
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
    assert note == ("pace beats blend — 1.00 of 4,000 draws over 16 step(s) in 4 session(s)"), note
    assert mod.duel_note("pace", "blend", tied).startswith("pace vs blend — unresolved, "), note
    assert mod.duel_note("pace", "blend", tied).endswith("16 step(s) in 4 session(s)"), note
    assert "4,000" in note and mod.DUEL_RESAMPLES == 4_000, note
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
    for sess in ("D1", "D2", "D3", "D4"):
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
    assert "task records      : 16 kept" in sout, sout[-800:]
    # the spread of the pace rung — ratios 1..4, so the 10th percentile is its own sample's
    # smallest and the 90th its largest — printed beside the median it belongs to, while a
    # rung whose every sample is the same ratio prints no bracket at all
    assert "[1.00–4.00]" in sout, sout[-800:]
    assert "blend 5.00x over 16" in sout, sout[-800:]
    assert "rung duel         : pace beats blend" in sout, sout[-800:]
    # ...and the rung that is scored but never picked: its own line, so a reader can see that
    # the pane is carrying an experiment and that nothing about it moves a number on screen
    # the score is a symmetric factor, so a rung predicting half the span is as wrong as one
    # predicting double: 60s/80s/100s/120s against a 100s span read 1.67/1.25/1.00/1.20
    assert "shadow rungs      : recent 1.23x [1.00–1.67] over 16" in sout, sout[-800:]
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
    # ...and the failure leaves the state it refused to replace alone
    assert run("bar").stdout.strip() == "todos 1/1", run("bar").stdout
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
    # ...and the flag is still a usage error when it names neither a source nor a path
    for bad in ("file:", "nonsense"):
        assert run("bar", "-s", bad).returncode == 64, bad
    say("source: `-s file:PATH` reads a state off disk, and a bad one is a usage error: ok")
    try:
        os.unlink(live_state)
    except OSError:
        pass

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

    # ---- the NAS session's pane: an ssh, told apart from the things that look like one.
    #      A tunnel, a control master and a `docker exec` over ssh all name the same host
    #      and all sit in a pane; none of them is a login shell with a freebuff prompt in it.
    for good in (
        "ssh -t remote@nas.local cd / && exec $SHELL -l",
        "/usr/bin/ssh remote@nas.local",
        "/bin/sh /tmp/fixture/bin/ssh -t remote@nas.local x",
    ):
        assert mod.is_ssh_cmd(good), good
    for bad in ("/usr/bin/ssh-agent -l", "python3 -c 'x ssh y'", "scp a b"):
        assert not mod.is_ssh_cmd(bad), bad
    assert mod.nas_host_tokens("remote@nas.local") == {
        "remote@nas.local", "nas.local"
    }
    assert mod.nas_host_tokens("/tmp/fixture/fake-ssh") == set(), "a path is not a host"

    table = {
        500: (1, "-zsh"),
        501: (500, "ssh -t remote@nas.local cd / && exec $SHELL -l"),
        900: (1, "-zsh"),
        901: (900, "ssh -N -L 8080:localhost:8080 remote@nas.local"),
        902: (900, "ssh -t remote@nas.local cd / && docker exec -w / -it dsh x"),
    }
    rows = [
        {"pane": "%7", "window": "@1", "pid": 500, "start": ""},
        {"pane": "%9", "window": "@1", "pid": 900, "start": ""},
    ]
    cands = mod.ssh_session_candidates("remote@nas.local", rows, table)
    assert [c["pane"] for c in cands] == ["%7"], cands
    # a fingerprint of another box is nobody's NAS session
    foreign = {1: (0, "-zsh"), 2: (1, "ssh other@example.com")}
    one_row = [{"pane": "%9", "window": "@1", "pid": 1, "start": ""}]
    assert mod.ssh_session_candidates("remote@nas.local", one_row, foreign) == []
    # ...while a stand-in that names a *program* (a fixture's fake transport) leaves no
    # host to match, and then a session-like ssh is accepted instead of refusing to place
    assert len(mod.ssh_session_candidates("/tmp/fixture/fake-ssh", one_row, foreign)) == 1

    # several logins open: the session's own start time decides (an ssh cannot host a
    # session that was already running when it logged in)
    began = {"%7": 0.0, "%8": 6000.0}
    assert mod.pick_ssh_pane(began, 6500.0) == "%8", "the newest login before the session"
    assert mod.pick_ssh_pane(began, 900.0) == "%7", "only the older one was open in time"
    assert mod.pick_ssh_pane(began, -9000.0) == "%7", "every login is after it: the earliest"
    assert mod.pick_ssh_pane(began, None) == "%8", "no timestamp at all: the newest"
    assert mod.pick_ssh_pane({}, 6500.0) is None
    say("nas: the ssh a session runs in is found by ancestry, flags and start time: ok")

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
    time.sleep(0.4)
    assert fake_proc.poll() is None, "stand-in freebuff exited immediately"
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
    out_s, deadline = b"", time.time() + 25
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
        os.execv("/bin/zsh", ["/bin/zsh", "-i", "-c", "command -v fbtodo && fbtodo bar"])
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

    # ---- NAS backend: the remote protocol, against a fixture store
    # FBTODO_NAS may hold a whole command, so the stand-in runs the very script the
    # `--source nas` path would ssh. Only the transport is faked — the header protocol,
    # the quoting of the extractor program, its argv contract and the mtime skip all
    # execute for real.
    stub = os.path.join(TEST_HOME, "fake-ssh")
    with open(stub, "w") as fh:
        fh.write('#!/bin/sh\nexec /bin/sh -c "$1"\n')
    os.chmod(stub, 0o755)
    # A second name for it: `stub` is reused further down for the stand-in freebuff
    # binary, and pointing FBTODO_NAS at THAT made every probe a six-second sleep.
    fake_ssh = stub
    store = os.path.join(TEST_HOME, "nasstore")
    chats = os.path.join(store, "demo", "chats")
    nas_args = ["--nas-root", store, "--nas-project", "demo"]
    # `env` (the copy run() hands to the child), not os.environ: mutating os.environ
    # alone left the real ssh target in place and the checks quietly tested the NAS.
    saved_nas = env.get("FBTODO_NAS")
    env["FBTODO_NAS"] = stub
    try:
        def nas_snapshot(*extra):
            # the `json` subcommand, not `snap --json` (that flag only means anything
            # to `status`), and NOT the state file: `-s nas` must never be answered
            # from a cached local-CLI state, which is exactly what it used to do.
            p = run("-s", "nas", *nas_args, *extra, "json", check=True)
            return json.loads(p.stdout)

        def write_session(name, blocks, age_s):
            """A session directory whose mtime decides whether it is 'the newest'.

            Content writes bump the directory's own mtime, so the age is stamped last:
            relying on creation order made these checks depend on how fast the loop ran.
            """
            d = os.path.join(chats, name)
            os.makedirs(d, exist_ok=True)
            open(os.path.join(d, "log.jsonl"), "w").write("{}\n")
            if blocks is not None:
                with open(os.path.join(d, "chat-messages.json"), "w") as fh:
                    if isinstance(blocks, str):
                        fh.write(blocks)
                    else:
                        json.dump([{"id": "m1", "variant": "ai", "content": "", "blocks": blocks}], fh)
            stamp = time.time() - age_s
            os.utime(d, (stamp, stamp))
            return d

        def tool(name, todos=None):
            return {
                "type": "tool", "toolCallId": "t", "toolName": name,
                "input": {"todos": todos} if todos is not None else {"pattern": "x"},
                "agentId": "main-agent", "includeToolCall": True, "output": "...",
            }

        # an older session that DID write a list...
        write_session(
            "2026-01-01T00-00-01.000Z",
            [tool("code_search"), tool("write_todos", [{"task": "old", "completed": True}])],
            300,
        )
        # ...and a newer one with no transcript yet (it appears per completed turn).
        # The newer session is what gets described: showing the old list as if it were
        # current would be worse than showing nothing.
        write_session("2026-01-01T00-00-02.000Z", None, 60)
        st = nas_snapshot()
        assert st["backend"] == "nas", st
        assert st["nas"]["has_transcript"] is False, st
        assert st["todos"] == [] and not st.get("error"), st
        assert st["target"].endswith("2026-01-01T00-00-02.000Z/"), st["target"]
        say("nas: the newest session wins; one with no transcript is named, not missing: ok")

        # a real list arrives, in the shape the NAS store actually uses
        write_session(
            "2026-01-01T00-00-02.000Z",
            [
                tool("run_terminal_command"),
                tool("write_todos", [{"task": "do a", "completed": True}, {"task": "do b", "completed": False}]),
                tool("write_todos", [{"task": "do a", "completed": True}, {"task": "do b", "completed": True}]),
            ],
            60,
        )
        st = nas_snapshot()
        assert [t["task"] for t in st["todos"]] == ["do a", "do b"], st["todos"]
        assert st["todos"][1]["completed"] is True, st["todos"]
        assert st["source_updated_ms"], st
        say("nas: the newest write_todos wins, extracted from the store: ok")

        # `-s nas status` has to describe the NAS. It read the local watcher's state file
        # and printed the Mac's own list beside "nas session: — no session found" — about a
        # box that was running a freebuff at the time.
        status_nas = STRIP(run("-s", "nas", *nas_args, "status").stdout)
        assert "nas session       : 2026-01-01T00-00-02.000Z/" in status_nas, status_nas
        assert "todos             : 2/2 done" in status_nas, status_nas
        # the state-file line still describes the local file (that is honest); the NAS
        # lines must not borrow its session or its list
        local_sess = (json.load(open(state_path)) if os.path.exists(state_path) else {}).get("session")
        nas_line = next(
            line for line in status_nas.splitlines() if line.strip().startswith("nas session")
        )
        assert local_sess and local_sess not in nas_line, (local_sess, nas_line)
        say("nas: `-s nas status` reports the NAS session, not the local list: ok")

        # unchanged transcript => the remote parse is skipped (the pane's idle poll is a stat)
        st2 = nas_snapshot()
        assert st2["nas"]["unchanged"] is True, st2["nas"]
        assert [t["task"] for t in st2["todos"]] == ["do a", "do b"], st2["todos"]
        say("nas: an unmoved transcript skips the parse instead of re-reading it: ok")

        # The patch row's two facts come back in the SAME poll: the hook's patch log and
        # its notifier's, gathered at the far end of the ssh the pane already makes. The
        # real paths are the NAS's own, so both are pointed at fixtures — and asserted
        # through the `-s nas` path a pane uses rather than by calling the readers here.
        env["FBTODO_NAS_PATCH_LOG"] = NAS_PATCH_FIXTURE
        env["FBTODO_NAS_ALERT_LOG"] = NAS_ALERT_FIXTURE
        st3 = nas_snapshot()
        assert st3["patch"]["outcome"] == "incomplete", st3.get("patch")
        assert st3["patch"]["version"] == "9.9.9-nas", st3.get("patch")
        assert "no window for fixture-window" in st3["patch"]["reason"], st3.get("patch")
        assert st3["patch"]["severity"] == "bad", st3.get("patch")
        assert st3["alert"]["kind"] == "sent", st3.get("alert")
        assert st3["alert"]["text"] == "sent ntfy incomplete", st3.get("alert")
        say("nas: the patch log and the alert log ride the poll the pane already makes: ok")
        # ...and a NAS pane that has no session yet still answers the question, because the
        # strip is drawn from the state and the state carries the two facts either way
        nas_status = STRIP(run("-s", "nas", *nas_args, "status").stdout)
        assert "cli patches       : incomplete · 9.9.9-nas" in nas_status, nas_status
        assert "last alert        : sent ntfy incomplete" in nas_status, nas_status
        say("nas: `-s nas status` answers for the NAS's patches, not this Mac's: ok")

        # the goal of a NAS list comes from the AGENT's line too — the transcript keeps
        # user requests, and those are never the heading. Two fixtures: one where the ai
        # message carries prose (the rule followed), one like the real store, where it
        # carries none at all (measured: content length 0, tool blocks only).
        dg = os.path.join(store, "goalproj", "chats", "2026-04-04T00-00-00.000Z")
        os.makedirs(dg, exist_ok=True)
        open(os.path.join(dg, "log.jsonl"), "w").write("{}\n")
        with open(os.path.join(dg, "chat-messages.json"), "w") as fh:
            json.dump(
                [
                    {"id": "m0", "variant": "user", "content": "wire voicevox to wake on demand"},
                    # Work LEFT on the list, deliberately: a FINISHED list with a newer request
                    # is dropped (see finish_state), and this check is about the heading, so it
                    # needs a list that is still the standing plan.
                    {"id": "m1", "variant": "ai", "content": "Goal: wake voicevox on demand",
                     "blocks": [tool("write_todos", [{"task": "deploy", "completed": False}])]},
                    {"id": "m2", "variant": "user", "content": "and also fix the pane timeout"},
                    {"id": "m3", "variant": "ai", "content": "Goal: fix the pane timeout",
                     "blocks": []},
                ],
                fh,
            )
        stg = nas_snapshot("--nas-project", "goalproj")
        assert [t["task"] for t in stg["todos"]] == ["deploy"], stg["todos"]
        assert stg["goal"] == "wake voicevox on demand", stg
        assert stg["now"] == "fix the pane timeout", stg
        say("nas: an agent Goal: line heads the list, and a newer one is `now`: ok")

        with open(os.path.join(dg, "chat-messages.json"), "w") as fh:
            json.dump(
                [
                    {"id": "m0", "variant": "user", "content": "wire voicevox to wake on demand"},
                    {"id": "m1", "variant": "ai", "content": "",
                     "blocks": [tool("write_todos", [{"task": "deploy", "completed": True}])]},
                ],
                fh,
            )
        # The far side skips its parse while the transcript's mtime is unchanged, and that
        # mtime has one-second resolution — so a rewrite in the same second needs a nudge
        # for the new content to be read at all.
        path_g = os.path.join(dg, "chat-messages.json")
        os.utime(path_g, (time.time() + 2, time.time() + 2))
        stg = nas_snapshot("--nas-project", "goalproj")
        assert stg["goal"] is None, stg
        assert stg["now"] is None, stg
        say("nas: a transcript with no agent prose yields no heading, as measured: ok")

        # ...and the `fb` marker, which is the sharper "which session is live" answer
        shim = os.path.join(TEST_HOME, "shim")
        os.makedirs(shim, exist_ok=True)
        with open(os.path.join(shim, "pgrep"), "w") as fh:
            fh.write("#!/bin/sh\nexit 1\n")  # no freebuff process, so only the marker can say live
        os.chmod(os.path.join(shim, "pgrep"), 0o755)
        saved_path = env.get("PATH", "")
        env["PATH"] = shim + os.pathsep + saved_path
        marker = os.path.join(TEST_HOME, "fb-session")
        # a second project, as `fb` run from another directory would have: the marker's
        # dir must select THAT store, or the pane shows a different project's list
        other = "otherproj"
        d2 = os.path.join(store, other, "chats", "2026-02-02T00-00-00.000Z")
        os.makedirs(d2, exist_ok=True)
        with open(os.path.join(d2, "chat-messages.json"), "w") as fh:
            json.dump(
                [{"id": "m", "variant": "ai", "content": "", "blocks": [
                    tool("write_todos", [{"task": "from otherproj", "completed": False}])]}],
                fh,
            )

        live_pid = subprocess.Popen(["sleep", "30"]).pid
        with open(marker, "w") as fh:
            fh.write(f"{live_pid} 2026-01-01T00:00:00Z {store}/{other}\n")
        st4 = nas_snapshot("--fb-marker", marker)
        assert st4["nas"]["fb"] == "1" and st4["instance_alive"], st4["nas"]
        assert st4["nas"]["fb_project"] == other, st4["nas"]
        assert st4["target"].endswith("2026-02-02T00-00-00.000Z/"), st4["target"]
        assert [t["task"] for t in st4["todos"]] == ["from otherproj"], st4["todos"]
        say("nas: a live fb marker names the session, and reads ITS project's store: ok")

        # ...but the marker's dir is where the session STARTED, which is not always where
        # its store lands (measured: `fb` maps a cwd the container cannot see, and a
        # container cwd of `/` makes the project nameless — `projects/chats/<thread>`, not
        # `projects/host`). Trusting the marker then read a two-day-old session and the
        # pane could never show the live one, however often it was re-listed. A live
        # session is followed by the store it writes.
        host_store = os.path.join(store, "host", "chats", "2026-09-19T09-01-35.987Z")
        os.makedirs(host_store, exist_ok=True)
        with open(os.path.join(host_store, "chat-messages.json"), "w") as fh:
            json.dump(
                [{"id": "m", "variant": "ai", "content": "", "blocks": [
                    tool("write_todos", [{"task": "two days old", "completed": True}])]}],
                fh,
            )
        stale = time.time() - 3600 * 48
        os.utime(host_store, (stale, stale))
        live_store = os.path.join(store, "chats", "2026-09-21T19-40-05.908Z")
        os.makedirs(live_store, exist_ok=True)
        with open(os.path.join(live_store, "chat-messages.json"), "w") as fh:
            json.dump(
                [{"id": "m", "variant": "ai", "content": "", "blocks": [
                    tool("write_todos", [{"task": "the session you are in", "completed": False}])]}],
                fh,
            )
        with open(marker, "w") as fh:
            fh.write(f"{live_pid} 2026-09-21T19:40:01Z {store}/host/\n")
        st5 = nas_snapshot("--nas-project", "host", "--fb-marker", marker)
        assert st5["target"].endswith("2026-09-21T19-40-05.908Z/"), st5["target"]
        assert st5["nas"]["fb_project"] == "", st5["nas"]
        assert [t["task"] for t in st5["todos"]] == ["the session you are in"], st5["todos"]
        say("nas: a live session is followed by the store it writes, not the marker's guess: ok")

        # ---- a `continue` on the NAS: the transcript carries user messages, so the same
        #      continuation rule applies there — 
        nudge_proj = os.path.join(store, "nudgeproj", "chats", "2026-03-03T00-00-00.000Z")
        os.makedirs(nudge_proj, exist_ok=True)
        open(os.path.join(nudge_proj, "log.jsonl"), "w").write("{}\n")

        def write_transcript(prompt):
            with open(os.path.join(nudge_proj, "chat-messages.json"), "w") as fh:
                json.dump(
                    [
                        {"id": "m0", "variant": "user", "content": "track the deploy"},
                        {"id": "m1", "variant": "ai", "content": "", "blocks": [
                            tool("write_todos", [{"task": "deploy", "completed": True}])]},
                        {"id": "m2", "variant": "user", "content": prompt},
                    ],
                    fh,
                )

        for i, (phrase, is_cont) in enumerate((
            ("continue", True),
            ("ok, keep going with it", True),
            ("continue with the NAS work", False),
        )):
            write_transcript(phrase)
            # One-second mtime resolution: the far side skips its parse while the
            # transcript has not moved, so each rewrite needs a DISTINCT second — a
            # shared `+2` made the loop read the first phrase three times.
            path_n = os.path.join(nudge_proj, "chat-messages.json")
            stamp = time.time() + 3 + 2 * i
            os.utime(path_n, (stamp, stamp))
            stn = nas_snapshot("--nas-project", "nudgeproj")
            assert bool(stn["nudge"]) == is_cont, (phrase, stn.get("nudge"), stn.get("now"))
            if is_cont:
                assert stn["nudge"] == phrase and stn["now"] is None, stn
                rendered_n = STRIP(module.render(dict(stn, done=1, total=1), False, width=60))
                assert "nudge · " in rendered_n and "rewrite the list" in rendered_n, rendered_n
            else:
                assert stn["now"] == phrase, stn
        say("nas: a `continue` in the transcript is a nudge, and real work is still `now`: ok")

        # the far side has no fbtodo to import, so the word list is injected into its
        # source — this is the check that the injection happened and carries the same words
        injected = repr(tuple(sorted(module.NUDGE_WORDS)))
        assert injected in module.NAS_EXTRACT, "the NAS extractor did not get the nudge words"
        assert str(module.NUDGE_MAX_CHARS) in module.NAS_EXTRACT, module.NUDGE_MAX_CHARS
        say("nas: the extractor carries the same continuation rule, injected not guessed: ok")

        # ---- why a NAS list looks plainer than the local one, said out loud.
        # Measured 2026-09-26: a NAS session that called write_todos ONCE, at the end, with
        # every todo already completed. The pane could show the list and nothing else — no row
        # clock, no pace, no EST REM — because a clock is only ever started for a step seen
        # unfinished. A silent gap reads as a broken pane; the pane now names the reason, and a
        # list-less one names the tools the session did call.
        def tool2(name, todos=None, pattern="x"):
            return {
                "type": "tool", "toolCallId": "t", "toolName": name,
                "input": {"todos": todos} if todos is not None else {"pattern": pattern},
                "agentId": "main-agent", "includeToolCall": True, "output": "...",
            }

        # its OWN project, not `demo`: a fixture left in the project every later
        # check probes becomes "the newest session" for them, and the unreadable-store
        # check then reads this list instead of the broken file it means to read
        census = os.path.join(store, "censusproj", "chats", "2026-09-21T20-00-04.000Z")
        os.makedirs(census, exist_ok=True)
        open(os.path.join(census, "log.jsonl"), "w").write("{}\n")
        with open(os.path.join(census, "chat-messages.json"), "w") as fh:
            json.dump([{"id": "m", "variant": "ai", "content": "", "blocks": [
                tool2("run_terminal_command"), tool2("run_terminal_command"),
                tool2("code_search"),
                tool2("write_todos", [
                    {"task": "one", "completed": True},
                    {"task": "two", "completed": True}])]}], fh)
        # the census survives the round trip: it is the only thing there is to say about a
        # session that has written no list at all
        # an explicit marker path that does not exist: a live marker left by an earlier check
        # would send the probe to THAT project, and this fixture is not in it
        st6 = nas_snapshot(
            "--nas-project", "censusproj",
            "--fb-marker", os.path.join(TEST_HOME, "no-marker-for-census"),
        )
        assert st6["tool_calls"].get("run_terminal_command") == 2, json.dumps(
            {k: v for k, v in st6.items() if k not in ("todos",)}, default=str)[:500]
        assert st6["tool_calls"].get("code_search") == 1, st6["tool_calls"]
        say("nas: the transcript's tool census reaches the state, counts and all: ok")

        ticked_state = dict(st6, task_times={})
        frame = ansi.sub("", module.render(
            ticked_state, True, watching=999, width=68, height=12, now_ms=SWEEP_NOW,
        ))
        assert "no per-step times" in frame, frame
        # the reason is longer than a 68-column frame, so assert the part that fits
        assert "arrived with every step" in frame, frame
        # ...and a list that DID get a clock is not told it has none
        timed = dict(ticked_state, task_times={
            "one": {"started_ms": SWEEP_NOW - 60_000, "done_ms": SWEEP_NOW - 30_000,
                    "elapsed_ms": 30_000}})
        assert "no per-step times" not in ansi.sub("", module.render(
            timed, True, watching=999, width=68, height=12, now_ms=SWEEP_NOW)), timed
        say("nas: a list with no clocks says why, and one with clocks is left alone: ok")

        empty_proj = os.path.join(store, "censusproj", "chats", "2026-09-21T20-00-05.000Z")
        os.makedirs(empty_proj, exist_ok=True)
        open(os.path.join(empty_proj, "log.jsonl"), "w").write("{}\n")
        with open(os.path.join(empty_proj, "chat-messages.json"), "w") as fh:
            json.dump(
                [{"id": "m", "variant": "ai", "content": "", "blocks": [
                    tool2("run_terminal_command"), tool2("read_url", pattern="y"),
                    tool2("run_terminal_command")]}],
                fh,
            )
        # The census reached the state over the wire (the check above); this one is about
        # the pane's sentence, so it renders a state directly rather than negotiating the
        # probe's liveness a second time.
        st7 = {"backend": "nas", "instance_alive": True, "todos": [], "task_times": {},
               "tool_calls": {"run_terminal_command": 2, "read_url": 1, "skill": 1},
               "source": "nas-journal", "session": "x"}
        frame7 = ansi.sub("", module.render(
            st7, True, watching=999, width=68, height=12, now_ms=SWEEP_NOW))
        assert "no write_todos call yet" in frame7, frame7
        assert "called so far: run_terminal_command 2, read_url 1, skill 1" in frame7, frame7
        # and with nothing called, there is nothing to add: the pane keeps its old shape
        st7["tool_calls"] = {}
        frame7b = ansi.sub("", module.render(
            st7, True, watching=999, width=68, height=12, now_ms=SWEEP_NOW))
        assert "called so far" not in frame7b, frame7b
        say("nas: a session with no list is told which tools it has called: ok")

        # The SECOND basket: with no list, the pane names what the session has actually
        # DONE. The feed REPLACES the tool counts rather than joining them — both answer
        # "and what has it been doing instead?", and rows are the answer that fits.
        st8 = dict(st7, tool_calls={"run_terminal_command": 2, "skill": 1}, observed=[
            {"verb": "edited", "what": "fbtodo", "ts_ms": SWEEP_NOW - 120_000},
            {"verb": "ran", "what": "python3 fbtodo-selfcheck.py", "ts_ms": SWEEP_NOW - 60_000},
        ])
        feed = ansi.sub("", module.render(
            st8, True, watching=999, width=68, height=14, now_ms=SWEEP_NOW))
        assert "edited fbtodo  ·  2m ago" in feed, feed
        assert "ran python3 fbtodo-selfcheck.py  ·  1m ago" in feed, feed
        assert "called so far" not in feed, feed
        # ...and a state carrying no calls keeps the line it had: the feed is an addition,
        # never a blank where the old answer used to be.
        nofeed = ansi.sub("", module.render(
            dict(st7, tool_calls={"run_terminal_command": 2}), True,
            watching=999, width=68, height=12, now_ms=SWEEP_NOW))
        assert "called so far: run_terminal_command 2" in nofeed, nofeed
        say("nas: with no list the pane names what the session has done: ok")



        # the same marker with a dead pid: a stale marker must not pin a phantom session,
        # and a pane with nothing to show says it is waiting for `fb`
        os.kill(live_pid, signal.SIGKILL)
        time.sleep(0.2)
        waitproj = "waitproj"
        write_session_at_project = os.path.join(store, waitproj, "chats", "2026-03-03T00-00-00.000Z")
        os.makedirs(write_session_at_project, exist_ok=True)
        open(os.path.join(write_session_at_project, "log.jsonl"), "w").write("{}\n")
        with open(marker, "w") as fh:
            fh.write(f"{live_pid} 2026-01-01T00:00:00Z {store}/{waitproj}\n")
        st5 = nas_snapshot("--fb-marker", marker)
        assert st5["nas"]["fb"] == "0", st5["nas"]
        assert not st5["instance_alive"], st5["nas"]
        assert st5["nas"]["fb_project"] == waitproj, st5["nas"]
        assert st5["todos"] == [], st5["todos"]
        text = STRIP(run("-s", "nas", *nas_args, "--fb-marker", marker, "snap").stdout)
        assert "waiting for a NAS freebuff session" in text, text
        say("nas: a stale marker is not a live session, and the pane says it is waiting: ok")
        os.unlink(marker)

        # a store the far side cannot read is an error, not an empty list
        write_session("2026-01-01T00-00-03.000Z", "not json", 10)
        st3 = nas_snapshot()
        assert st3["todos"] == [] and st3["nas"].get("error"), st3
        assert "JSONDecodeError" in st3["nas"]["error"], st3
        say("nas: an unreadable store reports an error instead of a zero: ok")
    finally:
        if saved_nas is None:
            env.pop("FBTODO_NAS", None)
        else:
            env["FBTODO_NAS"] = saved_nas

        # ---- a NAS pane's lifetime: --wait keeps it for the shell that owns it, so a
    #      remote pane can outlive one freebuff run (and is killed when the ssh returns)
    #@phase nas-pane-wait
    if shutil.which("tmux"):
        sess2 = "fbtwait"
        sock2 = "fbtchecksockwait"
        tmux2 = ["tmux", "-L", sock2]
        # the NAS block restored FBTODO_NAS on its way out; this pane needs it again
        env["FBTODO_NAS"] = fake_ssh
        short = subprocess.Popen(["sleep", "2"])
        marker2 = os.path.join(TEST_HOME, "fb-session-2")
        with open(marker2, "w") as fh:
            fh.write(f"{short.pid} 2026-01-01T00:00:00Z {store}/otherproj\n")
        base = (
            f"FBTODO_HOME={TEST_HOME} FBTODO_NAS={env['FBTODO_NAS']} "
            f"PATH={shim}:{saved_path} "
            f"{sys.executable} {FB} -s nas --nas-root {store} --nas-project demo "
            f"--fb-marker {marker2} -i 2.5 --stale-after 0 --tick 2"
        )
        for name, extra in (("wait", " --wait"), ("nowait", "")):
            subprocess.run(
                tmux2 + ["new-session", "-d", "-s", name, "-x", "70", "-y", "12", f"{base}{extra}"],
                capture_output=True,
            )
        time.sleep(6)  # the marker's pid is dead by now

        def alive(sess) -> bool:
            p = subprocess.run(tmux2 + ["list-panes", "-t", sess], capture_output=True)
            return p.returncode == 0

        assert alive("wait"), "--wait pane closed when its session ended"
        assert not alive("nowait"), "a plain nas pane lingered after its session ended"
        shot = subprocess.run(tmux2 + ["capture-pane", "-p", "-t", "wait"], capture_output=True, text=True)
        assert "waiting for a NAS freebuff session" in STRIP(shot.stdout), shot.stdout
        subprocess.run(tmux2 + ["kill-server"], capture_output=True)
        short.kill()
        os.unlink(marker2)
        env.pop("FBTODO_NAS", None)
        say("nas pane: --wait outlives one fb run, a plain pane closes with it: ok")
        env["PATH"] = saved_path

    # ---- painting is not tied to polling: the clock moves every second while the
    #      store is only reached once per `-i` (on the NAS every poll is an ssh)
    #@phase nas-pane-repaint
    if shutil.which("tmux"):
        sock3, sess3 = "fbtchecksockcad", "fbtcad"
        tmux3 = ["tmux", "-L", sock3]
        subprocess.run(tmux3 + ["kill-server"], capture_output=True)
        # a list that is MID-flight: step 1 ticked off, step 2 running
        write_session(
            "2026-01-01T00-00-09.000Z",
            [
                tool(
                    "write_todos",
                    [
                        {"task": "tick me", "completed": True},
                        {"task": "still running", "completed": False},
                        {"task": "later", "completed": False},
                    ],
                )
            ],
            # a FUTURE directory mtime: the earlier blocks leave sessions age-0 seconds
            # old, and `ls -1dt` would otherwise make one of those the newest
            -5,
        )
        alive = subprocess.Popen(["sleep", "60"])
        marker3 = os.path.join(TEST_HOME, "fb-session-3")
        with open(marker3, "w") as fh:
            fh.write(f"{alive.pid} 2026-01-01T00:00:00Z {store}/demo\n")
        # the transport, wrapped so every poll is counted: one ssh per poll is the
        # cost the slow interval exists to pay for
        calls = os.path.join(TEST_HOME, "nas-calls.log")
        spliced = os.path.join(TEST_HOME, "counting-ssh")
        with open(spliced, "w") as fh:
            fh.write(f'#!/bin/sh\necho x >> {calls}\nexec /bin/sh -c "$1"\n')
        os.chmod(spliced, 0o755)
        env["FBTODO_NAS"] = spliced
        # `--no-daemon` because that is what the watcher opens, and a scratch home of its
        # own because a nas daemon left running by an earlier block writes a state file
        # this pane would be right to trust — and that state describes the dead marker.
        cad_home = os.path.join(TEST_HOME, "cad-home")
        os.makedirs(cad_home, exist_ok=True)
        cmd = (
            f"FBTODO_HOME={cad_home} FBTODO_NAS={spliced} PATH={shim}:{saved_path} "
            f"{sys.executable} {FB} -s nas --no-daemon --nas-root {store} "
            f"--nas-project demo --fb-marker {marker3} -i 5 --stale-after 0 --tick 5"
        )
        subprocess.run(
            tmux3 + ["new-session", "-d", "-s", sess3, "-x", "60", "-y", "12", cmd],
            capture_output=True,
        )
        time.sleep(2.5)  # the first poll lands immediately; the next is 5s out

        def cad_shot() -> str:
            return STRIP(
                subprocess.run(
                    tmux3 + ["capture-pane", "-p", "-t", sess3], capture_output=True, text=True
                ).stdout
            )

        shots = [cad_shot()]
        for _ in range(4):  # ~2.4s of watching, well inside one poll
            time.sleep(0.6)
            shots.append(cad_shot())
        polls = len(open(calls).read().split()) if os.path.exists(calls) else 0
        assert "still running" in shots[0], shots[0]
        # One poll costs one ssh (the extractor's mtime skip needs no second), and the
        # window is shorter than the interval — so a pane that painted only when it
        # polled would have exactly one shot here, not three.
        assert len(set(shots)) >= 3, f"the pane did not repaint between polls: {shots}"
        assert polls <= 2, f"the pane polled {polls} times in ~2.4s at -i 5: {shots}"
        say("nas pane: 1Hz repaint while polling the store once per -i: ok")
        subprocess.run(tmux3 + ["kill-server"], capture_output=True)
        alive.kill()
        os.unlink(marker3)
        env["FBTODO_NAS"] = fake_ssh

    # ---- the freebuff() wrapper opens a todo pane with the session and closes it
    #@phase local-session
    if shutil.which("tmux"):
        # A private server on its own socket: the pane then inherits THIS process's
        # PATH (a shared server would overwrite PATH from the attaching client via
        # update-environment, and the stub freebuff would never be found), and the
        # user's real server is never touched.
        sock = "fbtchecksock"
        sess = "fbtcheck"
        tmux = ["tmux", "-L", sock]
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
        # The stall watch is asked about a LOCAL session (the NAS build writes no
        # end-of-turn record), so its stub — and the check for it below — live here
        # rather than in the NAS block. A stub, not the real notifier: this run must
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
        created = subprocess.run(
            tmux
            + [
                "new-session", "-d", "-s", sess, "-x", "100", "-y", "30",                "-e", f"FBTODO_HOME={TEST_HOME}",
                "-e", f"PATH={path}",
                # ...and no autostart daemon left behind either (see the NAS block)
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
                # in zsh's command text, and the pane dies without a word.
                # PATH is prepended inside zsh, after the .zshrc that prepends
                # nvm's node bin (where the real freebuff lives) — otherwise the
                # real CLI runs and the stand-in 'session' never ends.
                f"zsh -i -c 'export PATH={stub_bin}:$PATH; freebuff'",
            ],
            capture_output=True,
        )

        def pane_cmds() -> list[str]:
            p = subprocess.run(
                tmux + ["list-panes", "-t", sess, "-F", "#{pane_current_command}"],
                capture_output=True, text=True,
            )
            return p.stdout.split() if p.returncode == 0 else []

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
            len(pane_cmds()) >= 2 and any(c.startswith("Python") for c in pane_cmds())
        ):
            time.sleep(0.3)
        assert any(c.startswith("Python") for c in pane_cmds()), (
            f"freebuff() did not open a todo pane: {pane_cmds()} | {panes_detail()}"
        )
        say("freebuff() opens a fbtodo pane alongside the session: ok")

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
                if len(parts) == 2 and parts[1].startswith("Python"):
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
        # over and three rows down, under a NAS pane: the wrapper had split a WINDOW, and
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
            return f"{pane_now} {where} {inst_now_pane}: {rects_now}"

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
        env["FBTODO_TMUX"] = f"tmux -L {sock}"
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
            """This fixture window's `local` half of the remembered layout, or {}."""
            try:
                with open(os.path.join(TEST_HOME, "fbtodo-last.json")) as handle:
                    doc = json.load(handle)
            except (OSError, ValueError):
                return {}
            for key, entry in (doc if isinstance(doc, dict) else {}).items():
                local = entry.get("local") if isinstance(entry, dict) else None
                if key.startswith(f"{sess}:") and isinstance(local, dict):
                    return local
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

        # ---- one pin, two roles. A pin written without `--role` is the window's SHARED
        #      answer; a role's own half outranks it for that role only, which is what lets
        #      a window holding a session AND a NAS ssh size its two lists differently.
        #      Asserted against the module's own lookup, not through tmux: it is a rule.
        set_knob(panes_mod, "PINS_PATH", os.path.join(TEST_HOME, "pins-unit.json"))
        set_knob(panes_mod, "LAST_PATH", os.path.join(TEST_HOME, "last-unit.json"))
        with open(panes_mod.PINS_PATH, "w") as fh:
            json.dump({window_target: {"side": "h", "size": 10, "nas": {"size": 6}}}, fh)
        shared = panes_mod.pane_layout(window_target, "local")
        assert (shared["side"], shared["side_source"]) == ("h", "pin"), shared
        assert (shared["size"], shared["size_source"]) == (10, "pin"), shared
        theirs = panes_mod.pane_layout(window_target, "nas")
        assert (theirs["side"], theirs["side_source"]) == ("h", "pin"), theirs
        assert (theirs["size"], theirs["size_source"]) == (6, "pin:nas"), theirs
        say("a pin without a role is shared; a role's own half outranks it for that role: ok")

        # ---- and that role half works through the live path: filed under the role, applied
        #      to the pane, which has to be moved AND resized by it.
        scoped = run("pin", "--window", window_target, "--role", "local", "--side", "v",
                     "--size", "8", "--json")
        assert scoped.returncode == 0, (scoped.returncode, scoped.stderr)
        doc = json.loads(scoped.stdout)
        assert doc["pin"] == {"local": {"side": "v", "size": 8}}, doc
        assert doc["layouts"]["local"]["side_source"] == "pin:local", doc
        deadline = time.time() + 12
        while time.time() < deadline and not pinned_ok("v", 8):
            time.sleep(0.3)
        assert pinned_ok("v", 8), (
            f"a pin filed for one role did not move/resize the pane: {pane_geometry()}"
            f" pin={scoped.stdout.strip()!r}"
        )
        say("a pin for one role is filed under it and moves that pane only: ok")

        # ---- `fbtodo why`: the side and size in force WITH the source that supplied them,
        #      the pane it is placed against, and whether it is actually there.
        why = json.loads(run("why", "--json", "--window", window_target).stdout)
        mine = [rec for rec in why["panes"] if rec["pane"] == pane]
        assert mine, why
        mine = mine[0]
        assert mine["role"] == "local" and mine["window"] == window_target, mine
        assert mine["side_source"] == "pin:local" and mine["size_source"] == "pin:local", mine
        assert mine["anchor"] == inst_pane and mine["placed"] is True, mine
        why_text = run("why", "--window", window_target).stdout
        assert f"pin ({window_target} local)" in why_text, why_text
        assert "placed" in why_text and str(mine["size"]) in why_text, why_text
        say("why names the source that decided each half, and whether the pane is placed: ok")
        assert run("why", "--window", "nosuch:99").returncode != 0, "a bad --window was accepted"
        run("pin", "--window", window_target, "--clear")
        assert json.loads(run("pin", "--list", "--json").stdout) == {}, (
            "a role's half survived --clear"
        )
        say("why refuses a window that does not exist; --clear drops a role's half too: ok")
        env.pop("FBTODO_TMUX", None)

        # ...and now the session may end (see the release file at `stub`): the keeper has
        # to notice and take the pane away on its own, FBTODO_PANE_SECONDS=1 apart.
        with open(release, "w"):
            pass
        deadline = time.time() + 20
        while time.time() < deadline and pane_cmds() and any(
            c.startswith("Python") for c in pane_cmds()
        ):
            time.sleep(0.3)
        assert not any(c.startswith("Python") for c in pane_cmds()), (
            f"todo pane outlived the session: {pane_cmds()}"
        )
        say("the pane closes when the session ends: ok")
        subprocess.run(tmux + ["kill-server"], capture_output=True)

    # ---- the `nas` subcommand itself: reachable, and honest when nothing runs
    #@phase nas-live
    if shutil.which("tmux"):
        sock3, sess3 = "fbtchecksockremote", "fbtremote"
        tmux3 = ["tmux", "-L", sock3]
        subprocess.run(tmux3 + ["kill-server"], capture_output=True)
        # ...and this process looks at THAT server too, so the answer cannot depend on
        # whatever the owner happens to have open (a live NAS pane would break it)
        env["FBTODO_TMUX"] = f"tmux -L {sock3}"
        run("nas", "--stop")
        p = run("nas", "--status", "--json")
        st = json.loads(p.stdout)
        assert p.returncode == 0 and st["watcher_pid"] is None, (p.returncode, st)
        assert st["panes"] == [] and st["state"] == {}, st
        assert "not running" in run("nas", "--stop").stderr
        say("nas: the subcommand answers (status / stop / json) instead of a usage error: ok")
        nas_bin = os.path.join(TEST_HOME, "nas", "bin")
        os.makedirs(nas_bin, exist_ok=True)
        with open(os.path.join(nas_bin, "ssh"), "w") as fh:
            # stands in for the login: long enough to type freebuff inside
            fh.write("#!/bin/sh\nsleep 60 & wait\n")
        os.chmod(os.path.join(nas_bin, "ssh"), 0o755)
        # The fake ssh runs the probe HERE, so its `pgrep` looks at this Mac's process
        # table — where a stray argv could make a session out of nothing. A shim keeps
        # liveness a fact about the marker, which is what these checks are about.
        with open(os.path.join(nas_bin, "pgrep"), "w") as fh:
            fh.write("#!/bin/sh\nexit 1\n")
        os.chmod(os.path.join(nas_bin, "pgrep"), 0o755)
        path3 = os.pathsep.join([nas_bin, shim, saved_path])
        marker3 = os.path.join(TEST_HOME, "fb-session-3")
        # The phone notifier this build asks about, as a stub: what is under test here is
        # that the watcher asks it while a session runs and stops when it goes.
        notify_log = os.path.join(TEST_HOME, "notify-calls.log")
        notify_dir = os.path.join(TEST_HOME, ".config", "freebuff-notify")
        os.makedirs(notify_dir, exist_ok=True)
        with open(os.path.join(notify_dir, "todo-bell.py"), "w") as fh:
            fh.write(
                "#!/bin/sh\n"
                f'printf "%s\\n" "$*" >> "{notify_log}"\n'
                "exit 0\n"
            )
        os.chmod(os.path.join(notify_dir, "todo-bell.py"), 0o755)
        # ...and the drop watch it asks when a session STOPS instead of ending. Also a
        # stub: a real one would push the fixture's death to the owner's phone.
        drop_log = os.path.join(TEST_HOME, "drop-calls.log")
        with open(os.path.join(notify_dir, "drop-bell.py"), "w") as fh:
            fh.write(
                "#!/bin/sh\n"
                f'printf "%s\\n" "$*" >> "{drop_log}"\n'
                "exit 10\n"
            )
        os.chmod(os.path.join(notify_dir, "drop-bell.py"), 0o755)
        # ...and the ask watch, which reads this Mac's PANES — asked on its own clock
        # while a session runs. A stub for the same reason as the two above, and one that
        # says "nobody is asking", because a real one here would be scanning the owner's
        # own tmux server.
        ask_log = os.path.join(TEST_HOME, "ask-calls.log")
        with open(os.path.join(notify_dir, "ask-bell.py"), "w") as fh:
            fh.write(
                "#!/bin/sh\n"
                f'printf "%s\\n" "$*" >> "{ask_log}"\n'
                "echo silent: nobody is asking\n"
                "exit 0\n"
            )
        os.chmod(os.path.join(notify_dir, "ask-bell.py"), 0o755)
        # ...and the stall watch, which asks the real fbtodo about this session's own
        # store: also a stub, and one that stays silent.
        pause_log = os.path.join(TEST_HOME, "pause-calls.log")
        with open(os.path.join(notify_dir, "pause-bell.py"), "w") as fh:
            fh.write(
                "#!/bin/sh\n"
                f'printf "%s\\n" "$*" >> "{pause_log}"\n'
                "echo silent: nothing to report\n"
                "exit 0\n"
            )
        os.chmod(os.path.join(notify_dir, "pause-bell.py"), 0o755)
        # HOME is the real one even here (only the scratch dir is redirected), so the
        # notifier path has to be pointed at the stub explicitly — otherwise this run
        # would push the fixture's lists to the owner's phone.
        env["FBTODO_NOTIFY"] = os.path.join(notify_dir, "todo-bell.py")
        env["FBTODO_DROP"] = os.path.join(notify_dir, "drop-bell.py")
        env["FBTODO_ASK"] = os.path.join(notify_dir, "ask-bell.py")
        env["FBTODO_PAUSE"] = os.path.join(notify_dir, "pause-bell.py")
        env["FBTODO_NAS"] = fake_ssh
        env["FBTODO_FB_MARKER"] = marker3
        created3 = subprocess.run(
            tmux3
            + [
                "new-session", "-d", "-s", sess3, "-x", "100", "-y", "30",
                "-e", f"FBTODO_HOME={TEST_HOME}",
                # FBTODO_NAS* point the watcher and its pane at the fixture store, so this
                # asserts what the pane RENDERS, not what the real NAS happens to hold.
                "-e", f"FBTODO_NAS={fake_ssh}",
                "-e", f"FBTODO_NAS_ROOT={store}",
                "-e", "FBTODO_NAS_PROJECT=demo",
                "-e", f"FBTODO_FB_MARKER={marker3}",
                # the private server, or the watcher would split a pane in the owner's
                "-e", f"FBTODO_TMUX=tmux -L {sock3}",
                # no .zshrc autostart daemon: it is detached, survives the server being
                # killed below, and then writes stale lists into the NEXT run's home
                "-e", "FBTODO_NO_AUTOSTART=1",
                # poll fast — the shipped default is deliberately gentle
                "-e", "FBTODO_NAS_POLL=1",
                "-e", "FBTODO_NAS_POLL_IDLE=1",
                "-e", "FBTODO_NAS_POLL_LIVE=1",
                "-e", "FBTODO_NOTIFY_SECONDS=1",
                "-e", f"FBTODO_NOTIFY={os.path.join(notify_dir, 'todo-bell.py')}",
                "-e", f"FBTODO_DROP={os.path.join(notify_dir, 'drop-bell.py')}",
                "-e", f"FBTODO_ASK={os.path.join(notify_dir, 'ask-bell.py')}",
                "-e", "FBTODO_ASK_SECONDS=1",
                "-e", f"FBTODO_PAUSE={os.path.join(notify_dir, 'pause-bell.py')}",
                "-e", "FBTODO_PAUSE_SECONDS=1",
                # PATH inside zsh, not via -e: tmux's own env handling and .zshrc's
                # prepends both fight it, and the real ssh would then be used
                # The fixture's stand-in for whatever function a user wraps a remote login in:
            # it asks fbtodo for the pane watcher, then execs an ssh. The argv has to LOOK
            # like an ssh on the process table, because that is what placement anchors on.
            rf"zsh -i -c 'export PATH={path3}:\$PATH; "
            rf"remote() {{ fbtodo nas --quiet >/dev/null 2>&1 &!; "
            rf"exec ssh -t remote@nas.local \"cd / && exec \$SHELL -l\"; }}; remote'",
            ],
            capture_output=True,
        )
        nas_state = os.path.join(TEST_HOME, "fbtodo-nas-pane.json")
        NAS_LOG = os.path.join(TEST_HOME, "fbtodo-nas-pane.log")

        def nas_listing() -> str:
            p = subprocess.run(
                tmux3 + ["list-panes", "-a", "-F", "#{pane_id} #{pane_start_command}"],
                capture_output=True, text=True,
            )
            return p.stdout if p.returncode == 0 else ""

        def nas_panes() -> list[str]:
            return [ln.split()[0] for ln in nas_listing().splitlines() if "-s nas" in ln]

        # the wrapper on its own must NOT open one: the pane belongs to the session over there
        deadline = time.time() + 8
        while time.time() < deadline:
            assert not nas_panes(), f"the remote shell opened a pane with no NAS session: {nas_listing()}"
            time.sleep(0.3)
        assert json.loads(run("nas", "--status", "--json").stdout)["watcher_pid"], (
            f"the remote shell did not start the NAS watcher: rc={created3.returncode} "
            f"err={created3.stderr.strip()!r}"
        )
        say("a remote shell carries you over and starts the watcher, without opening a pane: ok")

        # the liveness probe asked about the NAS and counted ITSELF as a match, so a NAS
        # with no session read as live forever. This is the lie that decided the pane.
        stt = {}
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                stt = json.load(open(nas_state))
            except (OSError, ValueError):
                stt = {}
            if stt.get("polls"):
                break
            time.sleep(0.3)
        assert stt.get("alive") is False, stt
        say("nas: no session, no marker reads idle — the probe cannot match itself: ok")

        def nas_diag() -> str:
            st = json.load(open(nas_state)) if os.path.exists(nas_state) else {}
            try:
                os.kill(st.get("watcher_pid") or 0, 0)
                alive = True
            except Exception:
                alive = False
            log = open(NAS_LOG).read()[-400:] if os.path.exists(NAS_LOG) else ""
            return f"watcher_alive={alive} state={st} panes={nas_listing()!r} log={log!r}"

        # `fb` starts over there -> the pane appears here, showing THAT session's list.
        # A fresh fixture session, because the newest one is what the pane must surface
        # (and the earlier checks deliberately left a readable-and-newest one behind).
        write_session(
            "2026-01-01T00-00-09.000Z",
            [tool("write_todos", [{"task": "pane check", "completed": False}])],
            1,
        )
        # Where the list belongs: the pane the session's ssh runs in. The fixture's ssh
        # is in the session's own pane, and a DECOY pane is split into it and made active
        # first — which is exactly the shape that used to misplace a pane, because a window
        # target makes tmux split whichever pane is active in it.
        def pane_geometry() -> dict:
            return tmux_geometry(tmux3)

        def directly_below(upper: str, lower: str) -> bool:
            """The rule fbtodo places by, restated from raw tmux geometry."""
            rects = pane_geometry()
            a, b = rects.get(upper), rects.get(lower)
            if not a or not b:
                return False
            return b[0] == a[0] and b[2] == a[2] and b[1] == a[1] + a[3] + 1

        ssh_pane = subprocess.run(
            tmux3 + ["list-panes", "-t", sess3, "-F", "#{pane_id}"],
            capture_output=True, text=True,
        ).stdout.split()
        assert ssh_pane, nas_diag()
        ssh_pane = ssh_pane[0]
        decoy = subprocess.run(
            tmux3 + ["split-window", "-v", "-l", "4", "-d", "-P", "-F", "#{pane_id}",
                     "-t", ssh_pane, "sleep 120"],
            capture_output=True, text=True,
        ).stdout.strip()
        assert decoy, nas_diag()
        subprocess.run(tmux3 + ["select-pane", "-t", decoy], capture_output=True)

        fb_proc = spawn_quiet("sleep", "60")
        with open(marker3, "w") as fh:
            fh.write(f"{fb_proc.pid} 2026-01-01T00:00:00Z {store}/demo\n")
        shot, shots = None, []
        deadline = time.time() + 30
        while time.time() < deadline:
            opened = nas_panes()
            if opened:
                frame = subprocess.run(
                    tmux3 + ["capture-pane", "-p", "-t", opened[0]], capture_output=True, text=True
                )
                shots.append((opened[0], frame.returncode, len(frame.stdout),
                              frame.stderr.strip()[:60], STRIP(frame.stdout)[:60]))
                if "pane check" in STRIP(frame.stdout):
                    shot = frame
                    break
            time.sleep(0.3)
        assert shot, (
            "a NAS session starting did not open a pane with its list: " + nas_diag()
            + f" frames={shots[-4:]!r}"
        )
        say("nas: `fb` starting opens the pane, showing that session's own list: ok")

        # ...and it opens UNDER the pane that session runs in, not under the pane that
        # happened to be active (the decoy), which is what a window target would have done.
        assert directly_below(ssh_pane, opened[0]), (
            f"the NAS pane did not open under the pane its ssh runs in: {pane_geometry()}"
            f"  ssh={ssh_pane} decoy={decoy}"
        )
        say("nas: the pane opens under the session's own pane, not the active one: ok")

        # ...and it is put back there when it drifts: a third pane split into the ssh
        # pushes the list a row down, the shape the placement pass has to notice.
        subprocess.run(
            tmux3 + ["split-window", "-v", "-l", "3", "-d", "-t", ssh_pane, "sleep 60"],
            capture_output=True,
        )
        deadline = time.time() + 15
        while time.time() < deadline and not directly_below(ssh_pane, opened[0]):
            time.sleep(0.3)
        assert directly_below(ssh_pane, opened[0]), (
            f"the drifted NAS pane was not moved back under its ssh: {pane_geometry()}"
        )
        say("nas: a NAS pane that drifted is moved back under its ssh: ok")

        # `nas --status` names the pane it placed against, so a wrong one is visible
        status_text = run("nas", "--status").stdout
        assert "ssh pane" in status_text and ssh_pane in status_text.split("ssh pane", 1)[1], (
            status_text
        )
        say("nas: --status names the ssh pane the list is placed against: ok")

        # ---- the NAS pane gets the same pin support, filed per window AND per role: this
        #      window holds the ssh, and a pin for `nas` is that pane's own answer.
        nas_window = subprocess.run(
            tmux3 + ["display-message", "-t", sess3, "-p", "#{session_name}:#{window_index}"],
            capture_output=True, text=True,
        ).stdout.strip()
        assert nas_window, nas_diag()
        nas_pin = run("pin", "--window", nas_window, "--role", "nas", "--size", "7", "--json")
        assert nas_pin.returncode == 0, (nas_pin.returncode, nas_pin.stderr)
        assert json.loads(nas_pin.stdout)["pin"] == {"nas": {"size": 7}}, nas_pin.stdout
        deadline = time.time() + 15
        while time.time() < deadline and pane_geometry().get(opened[0], [0, 0, 0, 0])[3] != 7:
            time.sleep(0.3)
        assert pane_geometry().get(opened[0], [0, 0, 0, 0])[3] == 7, (
            f"a pin for the nas role did not hold the NAS pane at that size: {pane_geometry()}"
            + nas_diag()
        )
        assert directly_below(ssh_pane, opened[0]), (
            f"resizing the NAS pane to its pinned size moved it off the ssh: {pane_geometry()}"
        )
        say("nas: a pin for the nas role holds the NAS pane at that size, still under its ssh: ok")

        nas_why = json.loads(run("why", "--json", "--window", nas_window).stdout)["panes"]
        mine = [rec for rec in nas_why if rec["pane"] == opened[0]]
        assert mine, nas_why
        mine = mine[0]
        assert mine["role"] == "nas" and mine["size_source"] == "pin:nas", mine
        assert mine["size"] == 7 and mine["anchor"] == ssh_pane, mine
        assert mine["placed"] is True, mine
        assert mine["anchor_note"], mine
        say("nas: why says which ssh the NAS pane is placed against, and its pinned size: ok")
        run("pin", "--window", nas_window, "--clear")
        assert json.loads(run("pin", "--list", "--json").stdout) == {}, "the NAS pin survived --clear"
        say("nas: the NAS pin clears like the local one: ok")

        # the phone notifier is asked on its own clock while a session is live: the push
        # must not depend on the pane being open, which is the whole point of it.
        def notify_calls() -> int:
            try:
                with open(notify_log) as handle:
                    return len([ln for ln in handle if ln.strip()])
            except OSError:
                return 0

        deadline = time.time() + 15
        while time.time() < deadline and notify_calls() < 1:
            time.sleep(0.3)
        assert notify_calls() >= 1, (
            "the watcher never asked the phone notifier while a session was live: " + nas_diag()
        )
        say("nas: the phone notifier is asked while the session runs: ok")

        # The ask watch is asked on its own clock too, and for a different question: a
        # question on screen stops the session, wherever the pane is being drawn.
        def ask_calls() -> int:
            try:
                with open(ask_log) as handle:
                    return len([ln for ln in handle if ln.strip()])
            except OSError:
                return 0

        deadline = time.time() + 15
        while time.time() < deadline and ask_calls() < 1:
            time.sleep(0.3)
        assert ask_calls() >= 1, (
            "the watcher never asked the ask watch while a session was live: " + nas_diag()
        )
        say("nas: the ask watch is asked while the session runs: ok")

        kill_tree(fb_proc)
        deadline = time.time() + 30
        while time.time() < deadline and nas_panes():
            time.sleep(0.3)
        assert not nas_panes(), f"the pane outlived the NAS session: {nas_listing()}"
        say("nas: the pane goes when the session goes (and no ssh close was involved): ok")

        # A session that STOPS is not a session that ended: the fixture's pid was killed, so
        # nothing removed the marker and it is still there with a dead pid — the witness the
        # drop watch is asked about, ONCE, after a second pass agrees the death is real.
        def drop_calls() -> list[str]:
            try:
                with open(drop_log) as handle:
                    return [ln.strip() for ln in handle if ln.strip()]
            except OSError:
                return []

        deadline = time.time() + 15
        while time.time() < deadline and not drop_calls():
            time.sleep(0.3)
        assert drop_calls(), (
            "the watcher never asked the drop watch after the session stopped: " + nas_diag()
        )
        assert "--fb 0" in drop_calls()[0] and "--live 0" in drop_calls()[0], (
            f"the drop was described with the wrong witness: {drop_calls()}"
        )
        time.sleep(2)
        assert len(drop_calls()) == 1, f"one death was asked about more than once: {drop_calls()}"
        say("nas: a session that stops (not ends) is asked about once, with its marker: ok")

        # ...and it stops being asked once the session goes: a finished session must not
        # keep paying an ssh every interval for a list nobody is producing.
        quiet_at, ask_at = notify_calls(), ask_calls()
        time.sleep(4)
        assert notify_calls() == quiet_at, (
            f"the notifier was still asked after the session went: "
            f"{notify_calls()} calls vs {quiet_at}"
        )
        assert ask_calls() == ask_at, (
            f"the ask watch was still asked after the session went: "
            f"{ask_calls()} calls vs {ask_at}"
        )
        say("nas: and not after it — a gone session stops asking: ok")

        run("nas", "--stop")
        assert json.loads(run("nas", "--status", "--json").stdout)["watcher_pid"] is None
        say("nas: the watcher stops on request and leaves no lock behind: ok")
        subprocess.run(tmux3 + ["kill-server"], capture_output=True)
        for k in ("FBTODO_NAS", "FBTODO_FB_MARKER", "FBTODO_TMUX", "FBTODO_NOTIFY", "FBTODO_DROP", "FBTODO_ASK", "FBTODO_PAUSE", "FBTODO_PANE_BELL", "FBTODO_PANE_BELL_SECONDS"):
            env.pop(k, None)

    # ---- the liveness pattern: it must match a real session and never its own probe.
    #      `pgrep -f` saw the probe's own script line, which carried the pattern, so a NAS
    #      with no session at all read as live — and the pane never closed.
    _mod = load_fbtodo()
    pat = _mod.nas_pgrep("manicode/freebuff")
    assert not re.search(pat, f"sh -c \"pgrep -f {pat} >/dev/null && echo 1\""), pat
    assert re.search(pat, "node /root/.config/manicode/freebuff/index.js"), pat
    say("nas: the liveness pattern matches a real session and never its own probe: ok")



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
    subprocess.run(["tmux", "-L", "fbtchecksock", "kill-server"], capture_output=True)
    subprocess.run(["tmux", "-L", "fbtchecksockremote", "kill-server"], capture_output=True)
    subprocess.run(["tmux", "-L", "fbtchecksockwait", "kill-server"], capture_output=True)
    # the NAS watcher is detached and outlives this script's tmux panes: it is killed by
    # its own lock, which is also the only handle on it
    try:
        with open(os.path.join(TEST_HOME, "fbtodo-nas-pane.pid")) as fh:
            os.kill(int(json.load(fh)["pid"]), signal.SIGTERM)
    except Exception:
        pass
    if os.path.realpath(TEST_HOME).startswith(os.path.realpath(REAL_HOME) + os.sep):
        shutil.rmtree(TEST_HOME, ignore_errors=True)
