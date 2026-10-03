"""The claim files: who is watching, held by the kernel rather than by a number."""

from __future__ import annotations

try:
    import fcntl
except ImportError:  # pragma: no cover - no flock, so a claim is the record alone
    fcntl = None
try:
    import ctypes
    import ctypes.util
except ImportError:  # pragma: no cover - no ctypes, so no KERN_PROCARGS2 to ask
    ctypes = None
import json
import os
import re
import signal
import subprocess
import sys
import time
from .base import *  # noqa: F401,F403 — the package is one namespace

# In plain words: a claim is a file HOLDING A LOCK THE OPERATING SYSTEM OWNS. The number
# is written inside the file for a person reading the scratch dir, but the exclusion comes
# from the kernel, because a number is not evidence: a pid can come round again, and a
# process that answers `kill -0` may never have been the watcher at all — which is how a
# record left behind by a crash could hold the next watcher off, and how a pid reused by
# something else could make a dead watcher look alive. The kernel drops the lock when the
# process that took it ends, however it ends, so nothing has to be cleaned up to be
# correct: a claim that cannot be taken is live, and one that can be taken is not.
_LOCK_FDS: dict[str, int] = {}

# How many times a claim question, or a claim, re-opens the name it is asking about. Each
# retry means the name moved between the open and the lock — which takes another process
# doing the moving — so a handful is generous, and bounded, so churn cannot spin a loop.
_CLAIM_LOOKS = 5


def _same_file(fd: int, path: str) -> bool:
    """Does the NAME `path` still point at the very file this fd is open on?

    The one question the kernel can answer and a path cannot: after an open, the name can
    be replaced (a claim file rewritten) or removed, and a lock on the fd then guards a
    file no reader can find. Same device as well as same inode — two filesystems can reuse
    an inode number.
    """
    try:
        st_fd = os.fstat(fd)
        st_path = os.stat(path)
    except OSError:
        return False
    return (st_fd.st_dev, st_fd.st_ino) == (st_path.st_dev, st_path.st_ino)


def lock_open(path: str, create: bool = True) -> int:
    """A 0600 fd for the claim file, created if it is not there yet — when asked.

    A probe (`lock_holder`) passes `create=False`: it asks a question, and it must not
    leave an empty claim file behind — a reader that takes the path's existence as a
    claim would believe one that nobody holds.
    """
    d = os.path.dirname(path)
    if create and d:
        os.makedirs(d, mode=0o700, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR if create else os.O_RDWR
    fd = os.open(path, flags, 0o600)
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
    return _same_file(fd, path)


def lock_holder(path: str) -> int | None:
    """The pid holding the claim on `path`, or None when nobody does.

    `flock` cannot say WHO holds a lock, so this asks the one question that does have an
    answer: can the claim be taken? If it can, nobody holds it — and a record sitting
    there that NAMES A PID is a leftover, removed instead of believed (a reader that
    believed it would report a watcher that is not running). An EMPTY file is not a
    record: it is a claim being born — `write_lock` creates the file a breath before it
    can lock it — and removing it strands the claim on an unlinked inode, a keeper
    running with nothing on disk to name it. (Measured 2026-10-02: two asks arriving
    together each removed the other's newborn claim, and each then logged "never claimed
    the lock" while keepers were running.) A probe also does not CREATE a file where none
    exists: no file is no claim, and an empty one left by a question is
    indistinguishable from a claim being born. If the claim cannot be taken, the pid
    inside the record is the holder's: it was written by whoever took the claim, and the
    claim is still held.

    Every answer is tied to the NAME: the file is opened, then locked, and the name can be
    replaced in between — by this very cleanup, running in another process — so the lock and
    the record are only believed while the name still points at the file this probe locked
    (`_same_file`). When it does not, the probe asks again, bounded: the answer must be
    about the file a reader would find, not one that has been renamed away. And a record is
    removed only under that same tie — never a name that is not the file this probe holds,
    which could be somebody's just-made claim.
    """
    for _ in range(_CLAIM_LOOKS):
        try:
            fd = lock_open(path, create=False)
        except FileNotFoundError:
            return None  # no file, no claim — and a question leaves no claim file behind
        taken = False
        try:
            taken = lock_take(fd)
            if not _same_file(fd, path):
                # The name moved between the open and the lock: this fd is no longer the
                # claim a reader would find. Ask again — about the file that is there now.
                continue
            if taken:
                leftover = bool((read_json(path, {}) or {}).get("pid"))
                if leftover:
                    try:
                        os.unlink(path)  # free, and naming a pid: the record is a leftover
                    except OSError:
                        pass
                return None
            rec = read_json(path, {}) or {}
            pid = rec.get("pid")
            # 0 means "held, and the record says nothing": the caller refuses to start
            # rather than run a second watcher over one scratch dir, but has no number to
            # print.
            return int(pid) if pid else 0
        finally:
            if taken and fcntl is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(fd)
    # The name kept moving under every look: no answer is better than one about a file
    # that is already gone — every caller acts through `write_lock`, which decides on the
    # name as it is then.
    return None


def lock_peek(path: str) -> tuple[bool, int]:
    """(held, pid) — the claim's state, asked without touching anything on disk.

    `lock_holder` is a probe with a cleanup: a free claim whose record names a pid is a
    leftover, and asking removes it. That is right for a start or a stop — the record is
    dead weight — and wrong for a diagnostic that was only asked to look (`fbtodo doctor`),
    which must leave the state root exactly as it found it: the record is what the move
    decision (`_claim_live`) and the keeper's own pid read are read FROM, so a look that
    erased it would erase the fact it was reporting on. Same two steps as the probe — open,
    then try the lock — with no unlink and no write anywhere: held means the lock could not
    be taken (and the pid the record names, or 0 when it names none, is the holder's
    answer); free means nobody holds it, whatever a record left inside says.
    """
    for _ in range(_CLAIM_LOOKS):
        try:
            fd = lock_open(path, create=False)
        except FileNotFoundError:
            return False, 0
        try:
            taken = lock_take(fd)
            if not _same_file(fd, path):
                # The name moved between the open and the lock: ask about the one there now.
                continue
            if taken:
                return False, 0
            rec = read_json(path, {}) or {}
            pid = rec.get("pid")
            return True, int(pid) if pid else 0
        finally:
            os.close(fd)
    return False, 0  # a name that kept moving: answer "nothing to say", never a write


def claim_audit(path: str) -> dict:
    """One claim file read — never cleaned — and what clearing it would take.

    In plain words: `lock_holder` answers a question and cleans up after itself (a free
    record it finds is a leftover, and it removes it); an AUDIT must not change what it is
    auditing, so this is `lock_peek` plus the record and the verdicts, and it never unlinks
    and never writes. Four facts per file. The HOLDER: a held claim's record pid, and
    whether it is alive. The NAME↔INODE tie: a lock that blocks this process was taken
    through a file opened BY NAME, so a held claim IS the name's own inode claimed
    (`"match"`); a record naming a live pid while the name's file is FREE is the tie broken
    (`"mismatch"`) — a leftover, a claim being born, or a claim left on an inode the name
    has been moved off — and this process's own registry is checked exactly (`lock_ours`),
    which is the same mismatch seen from the inside. A STALE RECORD: free with a pid inside
    (the leftover `lock_holder` removes; the pid is tested, so a reused one can be judged).
    And WHAT CLEARING TAKES: a held claim only clears by ending its holder — the kernel
    drops the lock with it, and unlinking the file does not — a leftover by the next ask,
    and an empty free file by nobody: a claim may be about to be locked, so it is left
    alone.
    """
    fd_ours = _LOCK_FDS.get(path)
    entry = {
        "path": path,
        "state": "absent",
        "record": {},
        "pid": None,
        "pid_alive": False,
        "version": None,
        "ours_claimed": fd_ours is not None,
        "ours_named": _same_file(fd_ours, path) if fd_ours is not None else False,
        "name_inode": None,
        "leftover": False,
        "note": "no file — no claim",
        "clearing": "nothing to clear",
    }
    for _ in range(_CLAIM_LOOKS):
        try:
            fd = lock_open(path, create=False)
        except FileNotFoundError:
            return entry
        try:
            held = not lock_take(fd)
            if not _same_file(fd, path):
                # the name moved between the open and the lock: ask the file there now
                continue
            rec = read_json(path, {}) or {}
            pid = rec.get("pid")
            pid_i = int(pid) if pid else None
            alive = bool(pid_i and pid_alive(pid_i))
            entry.update({
                "state": "held" if held else "free",
                "record": rec,
                "pid": pid_i,
                "pid_alive": alive,
                "version": rec.get("version"),
            })
            if held:
                entry["name_inode"] = "match"
                entry["note"] = (
                    f"held by pid {pid_i} ({'alive' if alive else 'no longer alive'})"
                    if pid_i else "held, and the record names no pid"
                )
                if entry["version"] and entry["version"] != VERSION:
                    entry["note"] += f", recorded by v{entry['version']}"
                entry["clearing"] = (
                    "end the holder (SIGTERM) — the kernel drops the lock with it; "
                    "unlinking the file does not clear a held claim"
                )
            elif pid_i and alive:
                entry["leftover"] = True
                entry["name_inode"] = "mismatch"
                entry["note"] = (
                    f"free, but the record names live pid {pid_i}, which does not hold "
                    "this name's file — a leftover, a claim being born, or a claim orphaned "
                    "by a replaced file"
                )
                entry["clearing"] = (
                    "safe to remove — a claim being born re-checks and takes the name "
                    "again; the next ask removes it anyway"
                )
            elif pid_i:
                entry["leftover"] = True
                entry["note"] = f"free with a record naming dead pid {pid_i} — a leftover"
                entry["clearing"] = "safe to remove; the next ask removes it anyway"
            else:
                entry["note"] = "free and empty — a claim being born, or an empty leftover"
                entry["clearing"] = (
                    "nothing holds it; leave it — a claim may be about to be locked"
                )
            if entry["ours_claimed"] and not entry["ours_named"]:
                # the same mismatch from the inside: this process's own claim is not the
                # file the name points at, so the writer must re-claim, not keep the orphan
                entry["name_inode"] = "mismatch"
                entry["note"] += (
                    "; this process's own registry entry no longer points at the name"
                )
                entry["clearing"] = (
                    "re-claim with `write_lock` — the name moved under this process"
                )
            return entry
        finally:
            os.close(fd)
    entry["note"] = "the name kept moving under the look — no steady answer"
    return entry


def daemon_pid() -> int | None:
    """The running watcher's pid — asked with the PROBE, cleanup and all, not a peek.

    In plain words: this is `lock_holder` on the watcher's claim, so a free record that
    names a pid is a leftover and asking REMOVES it. That is exactly what the paths that
    ACT need — `spawn_daemon` waits on this answer and must not be handed the pid of a
    watcher the kernel is not locking, `cmd_stop` has to clear a dead record before it
    says it stopped something, the daemon loop's start guard must not stand down for a
    leftover, and a keeper pass reads the watcher's interpreter from it — and it is
    exactly what a command that only LOOKS must not do. The look-only family (`status`,
    `doctor`, `nas --status`, and the audit) reads the same claims through `lock_peek` or
    `claim_audit`, because the record it was asked to report on is also what `_claim_live`
    decides the state root from: a cleanup would erase the fact being reported, or (for a
    watcher from an older build) kill the very process the row is about. A new caller that
    only looks reaches for `lock_peek`; the self-check seeds a dead-pid record and runs
    every look-only command over it to keep this true.
    """
    return lock_holder(LOCK_PATH)


# ---------------------------------------------------------------- the roles, in the table
# What each role is, for the process-table half of the audit: the SUBCOMMAND each one is
# spawned with, immediately after the launcher token. A role is not "any process whose
# line contains the word" — `/usr/sbin/distnoted daemon` is not a watcher, and matching on
# the word alone said it was (measured the first time this ran) — so the line has to BE an
# fbtodo invocation: a token named `fbtodo` (the launcher, or a symlink to it) or the
# package's own `fbtodo/__init__.py`, and the subcommand right after it. The state root is
# then read from the process's environment, the way the program itself reads it. And the
# subcommand alone is not enough either: a bare `fbtodo daemon` only SPAWNS the watcher and
# exits, so the row has to be the process that runs the loop and holds the claim — the one
# started with `--foreground` (or its `-f` alias; either counts, and a bare spawn has
# neither).
ROLE_PROCS = {
    "watcher": ("daemon", ("--foreground",)),
    "keeper": ("pane-watch", ()),
    "nas pane": ("nas", ("-f", "--foreground")),
}

# How long a process must have been running before the cross-check calls it untied. A
# watcher or keeper that has just started has not claimed yet (the file may even be absent
# for the first breath), and a second watcher asked to stand down lives for a moment —
# neither is an orphan. Real ones run for minutes; two seconds of patience costs nothing.
ORPHAN_MIN_AGE_S = 2.0


def _fbtodo_subcommand(cmd: str) -> str | None:
    """The fbtodo subcommand a command line runs, or None when it is not fbtodo at all.

    One rule for every spawn shape this program has: `spawn_daemon`, `ensure_pane_keeper`
    and `spawn_nas_daemon` all build `[python, <launcher>, <subcommand>, …]`, the panes are
    `[env, PATH=…, python, <launcher>, pane|snap, …]`, and a launcher-less copy names
    `<pkg>/fbtodo/__init__.py` instead. The token AFTER the program is the subcommand —
    never a flag, because the flags come after it — so a process that merely mentions the
    word (a system daemon, a grep, this very command being typed) cannot match.
    """
    toks = cmd.split()
    for i, tok in enumerate(toks):
        base = os.path.basename(tok)
        if base == "fbtodo" or tok.endswith(os.path.join("fbtodo", "__init__.py")):
            return toks[i + 1] if i + 1 < len(toks) else None
    return None


def _proc_root(blob: str) -> str:
    """The state root a process's environment names, by the same rules the program uses.

    FBTODO_HOME first, then XDG_STATE_HOME, then the platform default — the three answers
    `_state_root` can give for a root that is not the legacy one. A blob with none of them
    is the default root, which is also what a legacy-root watcher reports: it decided
    `~/.freebuff` from a LIVE claim, and the audit is about what the process was told.
    """
    fields = {}
    for token in blob.replace("\0", " ").split():
        key, _, value = token.partition("=")
        if value and key in ("FBTODO_HOME", "XDG_STATE_HOME"):
            fields[key] = value
    if fields.get("FBTODO_HOME"):
        return os.path.expanduser(fields["FBTODO_HOME"])
    base = os.path.expanduser(fields["XDG_STATE_HOME"]) if fields.get("XDG_STATE_HOME") \
        else os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "fbtodo")


# ---------------------------------------------------------------- a process's environment
# A process is placed by the STATE ROOT its environment names, and the environment exists in
# exactly two places: `/proc/<pid>/environ` where the platform keeps one, and the kernel's
# argv+environment copy that `ps -Eww` prints.
#
# Measured on this machine 2026-10-02 (macOS 26, /bin/ps): the copy is NOT clipped by `ps` —
# a process of ours with a 250 KB environment printed all of it, and ~39 KB and ~3 KB ones
# came back whole — but the KERNEL caps what it hands over PER PROCESS. Every GUI process
# launched by LaunchServices (19 in one scan: Gemini, Brave, Obsidian, Karabiner's daemons…)
# came back with a kernel copy of 1068–1132 bytes — about 1 KB, the classic small-ARG_MAX
# buffer — while `ps` printed only 542–974 of those same bytes, because `ps` also drops the
# system-injected variables it knows (`OSLogRateLimit`, `MallocNanoZone`, `XPC_SERVICE_NAME`,
# `security_config`, `th_port`…), and it prints NOTHING of the environment for a platform
# binary such as `/usr/sbin/distnoted`. Two consequences, and they are the whole contract
# here: `ps -Eww` answering with a command line is not an environment (reading it as one
# places a process on the DEFAULT root it never named), and a copy that came back capped must
# not be believed to be rootless — it may have been cut off before the variable being looked
# for.
#
# What marks a capped copy is its SIZE, and nothing else compares: `ps`'s share of one runs
# from 542 to 974 bytes (ratios of 1.16 to 2.09 against the kernel's own count) and overlaps
# the 1.10–1.15x a COMPLETE copy of ours shows, so the ps line cannot be the measure — the
# kernel has to be asked how many bytes it copied. At or under this, it copied the small
# kind; our own processes got 3044–3844, and a 250 KB environment got all 257612.
KERNEL_COPY_CAP = 1200   # at or under this, the kernel's copy is the small kind
KERNEL_COPY_FLOOR = 512  # ...and a copy this small is a short one, not a capped one: a
# process with a two-line command and no environment to speak of (`/bin/sh <script>`, 78
# bytes measured) is not a process whose environment was cut off, and calling it clipped
# would only cost `locks --fix` the processes it can still place.
ENV_ROOT_KEYS = ("FBTODO_HOME", "XDG_STATE_HOME")

# `KERN_PROCARGS2`: the kernel's copy of a process's argv and environment, the value `ps -E`
# prints. Not a name any `sysctl(8)` line takes, which is why it is asked for by number.
_KERN_PROCARGS2 = 49
_KERN_CTL = 1
_KERN_ARGMAX = 262144
_KERN_LIBC = None  # the C library handle, built on first use
_ENV_TOKEN = re.compile(r"(?:^|\s)([A-Za-z_][A-Za-z0-9_]*=\S*)")


def _env_tokens(blob: str) -> list[str]:
    """The `KEY=value` tokens in a blob — what an environment has and a command line has not."""
    return [m.group(1) for m in _ENV_TOKEN.finditer(blob)]


def _env_names(blob: str) -> set[str]:
    """The variable NAMES a blob carries: the only part of it `_proc_root` reads."""
    return {tok.partition("=")[0] for tok in _env_tokens(blob)}


def _env_read(blob: str) -> bool:
    """Was an environment obtained at all, or only a process's command line?

    In plain words: `ps -Eww` answers with the command line whether or not the kernel let it
    copy the environment, so "the answer is not empty" is not the question. A platform binary
    comes back with nothing but its own name, and reading that as an environment is how a
    process gets placed on a root it never named.
    """
    return bool(_env_tokens(blob))


def _env_root(blob: str) -> bool:
    """Does the blob itself NAME the state root, rather than only imply the default one?"""
    return bool(_env_names(blob) & set(ENV_ROOT_KEYS))


def _environ_proc(pid: int) -> str:
    """`/proc/<pid>/environ` where the platform keeps one (linux), NULs as spaces.

    The first source where it exists: the kernel's own copy as a file, no fork, and not
    subject to the cap `KERN_PROCARGS2` has for processes it will not expose. "" elsewhere,
    which is not a failure — it is the other source's turn.
    """
    try:
        with open(f"{PROC_ROOT}/{pid}/environ", "rb") as handle:
            return handle.read().decode("utf-8", "replace").replace("\0", " ")
    except OSError:
        return ""


def _environ_ps(pid: int) -> str:
    """`ps -Eww` for one pid: its command line, then whatever environment it may print.

    The LAST line, because the first is the column header. Deliberately not split into
    "command" and "environment": there is no separator to trust. The caller asks `_env_read`
    whether an environment came back at all, and `_environ_clipped` whether more of it exists.
    """
    try:
        out = subprocess.run(
            ["ps", "-Eww", "-p", str(pid)], capture_output=True, text=True, timeout=5
        ).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""
    lines = out.rstrip("\n").splitlines()
    return lines[-1] if lines else ""


def _kernel_fields(raw: bytes) -> str:
    """The environment part of a KERN_PROCARGS2 buffer: argv and padding dropped.

    The layout is `argc`, the executable path, the argv block, then the environment, all
    NUL-separated; bytes the kernel did NOT copy keep whatever the buffer already held, and
    past the real end that can be another copy's leftovers (measured: `th_port=`,
    `security_config=0x0` — exactly the kind `ps` filters out). So the fields are read the
    way a shell would spell them and no further: a fragment has to look like an assignment to
    be kept, and a variable the kernel did not copy cannot be invented from padding.
    """
    if len(raw) < 4:
        return ""
    fields = raw[4:].split(b"\x00")
    idx = 0
    while idx < len(fields) and not fields[idx]:
        idx += 1
    idx += 1  # the executable path
    while idx < len(fields) and not fields[idx]:
        idx += 1
    idx += max(int.from_bytes(raw[:4], "little"), 0)  # the argv block, this process's own
    return " ".join(f.decode("utf-8", "replace") for f in fields[idx:])


def _kernel_copy(pid: int) -> tuple[str, int]:
    """The kernel's own copy of a process's environment — (fields as text, bytes copied).

    In plain words: `ps -E` is a consumer of `sysctl KERN_PROCARGS2`, and this asks the kernel
    for the same copy without the middleman. The byte count comes back with it because it is
    the one thing the copy cannot be asked twice for cheaply and the thing that says whether
    the kernel stopped early (`_environ_clipped`). This is the SECOND source because the first
    can be short: measured 2026-10-02, a process the kernel will not fully expose (a GUI app
    started by LaunchServices) gave 1068–1132 bytes here while `ps` printed 542–974 of them,
    and a root variable `ps` never showed can be in the part it dropped. Linux has no such
    sysctl and needs none — `/proc/<pid>/environ` is the same copy as a file. Anything that
    goes wrong is `("", 0)`: a missing second source must read as "nothing more to say",
    never as an empty environment, which would be an answer.
    """
    global _KERN_LIBC
    if sys.platform != "darwin" or ctypes is None:
        return "", 0
    try:
        if _KERN_LIBC is None:
            _KERN_LIBC = ctypes.CDLL(
                ctypes.util.find_library("c") or "libc.dylib", use_errno=True
            )
        buf = ctypes.create_string_buffer(_KERN_ARGMAX)
        size = ctypes.c_size_t(_KERN_ARGMAX)
        mib = (ctypes.c_int * 3)(_KERN_CTL, _KERN_PROCARGS2, int(pid))
        if _KERN_LIBC.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) != 0:
            return "", 0
        raw = buf.raw[:size.value]
    except (OSError, AttributeError, ValueError, TypeError):
        return "", 0
    return " ".join(_env_tokens(_kernel_fields(raw))), len(raw)


def _environ_clipped(ps_blob: str, kernel_bytes: int) -> bool:
    """Did the kernel stop early — is this copy the small kind it gives a hidden process?

    The question neither read can answer alone: `ps` prints a capped copy without saying so
    (its share of one runs from 542 to 974 bytes, which overlaps the share of a whole copy),
    and the direct read knows only how much it GOT. So the signal is the second source's own
    byte count, and it is a BAND — the cap is ~1 KB, so a copy inside
    `KERNEL_COPY_FLOOR`…`KERNEL_COPY_CAP` is the size every launchd/GUI process came back with
    (1012–1132) and the size nothing of ours did (1644 for a launcher run with two variables,
    3044–3844 for the roles, 257612 for a 250 KB environment). False without a second source
    and without a ps answer: not noticing a clip is acceptable, inventing one is not. A tiny
    copy below the floor is a short command line, not a cut-off environment, and is left to
    the ordinary rule — a copy that answers with nothing is unread, which is safe on its own.
    """
    if not ps_blob.strip():
        return False
    return KERNEL_COPY_FLOOR <= kernel_bytes <= KERNEL_COPY_CAP


def _joined(*blobs: str) -> str:
    """The non-empty sources, one per line — a union to look through, never a parse."""
    return "\n".join(blob for blob in blobs if blob.strip())


def _proc_environ(pid: int) -> tuple[str, bool]:
    """A process's environment — every source that will answer — and whether it looks clipped.

    `/proc` first, then `ps -Eww`, and the kernel's own copy (macOS) only when the first two
    named no root at all, which is the case a clip would hide it in. The blob is the sources
    JOINED, so a variable any of them kept is found; `clipped` says a capped copy is in play,
    and the caller must then refuse to PLACE a process that named no root — an environment
    that stopped early is not an environment without `FBTODO_HOME`. A test can hand
    `claim_processes` its blobs instead (`environs`), so the placement rule is pinned without
    depending on what happens to be running.

    This is the ONE read of a process's environment in the program, and every caller that
    needs to place a process by it comes here — `claim_processes` for the audit, and the
    self-check's `keeper_aim` for the guard that keeps a test keeper from keeping the
    owner's panes. Reading a process's environment any other way (a bare `ps -Eww`) inherits
    the two traps this function closes: a command line is not an environment, and a kernel
    copy that came back capped may have lost the variable being looked for.
    """
    proc = _environ_proc(pid)
    if _env_root(proc):
        return proc, False
    ps = _environ_ps(pid)
    if _env_root(ps):
        return _joined(proc, ps), False
    kernel, copied = _kernel_copy(pid)
    return _joined(proc, ps, kernel), _environ_clipped(ps, copied)


def claim_processes(root: str = "", table: dict | None = None,
                    environs: dict | None = None) -> list[dict]:
    """Every fbtodo watcher, keeper and NAS watcher RUNNING with the state root `root`.

    In plain words: a claim file can only name a holder that still ties to its name, so a
    process whose name was replaced is invisible to every read of the file — the audit
    needs the other end. One `ps` gives every command line; each candidate's environment is
    read through `_proc_environ` (the program's one two-source read) and believed only when
    the state root it names is this one. Each
    row: pid, role, ppid, argv, the root its environment named, whether that environment
    was PLACED at all (`root_named`: an environment that could not be read, or one that came
    back clipped with no root variable in it, is assumed to be ours for the audit's sentence,
    but `locks --fix` will not END a process it could not place — `env_clipped` says which of
    the two it was), and its Python when its command line is a pinned pane one (kept because
    it was paid for, not shown).

    `root` defaults to this run's SCRATCH. An EMPTY environment blob counts as this root:
    a process whose environment cannot be read (a `ps -E` that came back short) is far more
    likely to be ours than a stranger's, and a false positive is a sentence, not a kill.
    `table` and `environs` are the injection points: a test hands both, so the rule is
    pinned without depending on what is running on the machine.
    """
    root = os.path.realpath(root or SCRATCH)
    table = process_table() if table is None else table
    out = []
    for pid, (_ppid, cmd) in sorted(table.items()):
        sub = _fbtodo_subcommand(cmd)
        label = None
        for lbl, (word, need) in ROLE_PROCS.items():
            if word == sub and (not need or any(flag in cmd.split() for flag in need)):
                label = lbl
                break
        if label is None:
            continue
        if environs is not None:
            blob, clipped = environs.get(pid, ""), False
        else:
            blob, clipped = _proc_environ(pid)
        # Whether an ENVIRONMENT was read at all, and whether what was read can PLACE the
        # process: a blob with no root variable that came back clipped may have lost that
        # variable to the kernel's cap, so it is still reported — this root is assumed for
        # the audit's sentence — and never placed, which is what keeps `locks --fix` from
        # ending a process whose root it could not read.
        read = _env_read(blob)
        placed = read and (_env_root(blob) or not clipped)
        named = _proc_root(blob) if placed else ""
        if placed and os.path.realpath(named) != root:
            continue  # another state root's process: not one of ours to answer for
        out.append({
            "pid": pid,
            "role": label,
            "ppid": _ppid,
            "argv": cmd,
            "root": named or root,
            "root_named": placed,
            "env_clipped": clipped,
            "python": python_of(cmd),
        })
    ages = ages_for([p["pid"] for p in out])
    for proc in out:
        proc["age_s"] = ages.get(proc["pid"])
    return out


def claim_orphans(audit: dict, processes: list[dict]) -> list[dict]:
    """The processes a claim file cannot name: a LIVE role process with no tie to its name.

    The name-side audit sees three states — absent, free, held — and only the held one
    names its holder, because a held lock is what the record was written through. A process
    whose claim file was replaced under it holds a lock no reader can find, and the file it
    left behind can read as `absent` (removed) or `free` (an empty replacement, or an old
    record the name no longer means), with the process still running the whole time. So the
    process table is asked the other half: a HELD claim names its holder, and every other
    running process of that role is untied — the record naming no pid at all leaves nothing
    to compare against, and guessing would invent a finding. A FREE or ABSENT file ties no
    process to anything: a record inside it names a pid that does NOT hold the file — the
    tie-broken case `claim_audit` reports as `name_inode: mismatch`, and the same answer
    for a dead pid's leftover — so every running process of the role is untied.

    A process younger than `ORPHAN_MIN_AGE_S` is left out: a watcher between its start and
    its claim, and a second watcher standing down, both look untied for a moment, and a
    diagnostic that cried wolf at every start would be noise. `None` age (ps could not
    answer) counts as old: an unreadable age must not hide a real orphan.
    """
    if audit.get("state") == "held":
        holder = audit.get("pid")
        out = [] if holder is None else [p for p in processes if p["pid"] != holder]
    else:
        out = list(processes)
    return [p for p in out
            if p.get("age_s") is None or p["age_s"] >= ORPHAN_MIN_AGE_S]


def _lock_named(path: str) -> int | None:
    """Take the claim under `path` — the file the NAME points at, not one it left behind.

    In plain words: opening and locking are two steps, and the file can be replaced or
    removed between them — by another process's leftover cleanup, or by a hand-over whose
    exec never landed. Locking a file the name no longer points at would leave this process
    holding a claim no reader can find: a keeper running invisibly, while a second one
    could claim a fresh file of the same name. So every lock is followed by the name's own
    answer (`_same_file`), and a lock on a file the name has left behind is dropped and
    asked for again — bounded, because each pass can lose at most one file to a racing
    probe, and a probe removes a record only once.

    None means someone else holds the name (or the name moved through every look).
    """
    for _ in range(_CLAIM_LOOKS):
        fd = lock_open(path)
        taken = lock_take(fd)
        named = _same_file(fd, path)
        if taken and named:
            return fd  # a claim the name points at: a claim any reader can see
        os.close(fd)
        if named:
            return None  # the name is there and held: someone else leads this scratch dir
        # not named: the lock (if it was taken) guards a file the name has left behind
    return None


def write_lock(cwd: str, instance_pid, path: str | None = None,
               extra: dict | None = None) -> bool:
    """Claim `path` for this process, and write the record a reader sees.

    `path` exists for the NAS watcher and the pane keeper, whose claims on the scratch dir
    are their own files: three roles, three claims, and they must not block each other.
    `version` lets a reader tell a live watcher from one left behind by an upgrade.

    False when someone else holds it — the caller must stop rather than run a second
    watcher over one scratch dir. The record is written THROUGH the locked fd and never by
    rename: replacing the file would leave the new copy unlocked and the claim lost. And
    the name is re-checked after the lock, and on every later write (`_lock_named`,
    `_same_file`): a claim is only worth holding while the name still points at it.
    """
    target = path or LOCK_PATH
    fd = _LOCK_FDS.get(target)
    if fd is not None and not _same_file(fd, target):
        # The name no longer points at the file this process holds — replaced or removed
        # while the claim was nominally ours. A claim the name does not point at is not a
        # claim: the keeper would go on running invisibly, and a second one could start
        # over the fresh name. Let the orphan go and take the name as it is now.
        _LOCK_FDS.pop(target, None)
        try:
            os.close(fd)
        except OSError:
            pass
        fd = None
    if fd is None:
        fd = _lock_named(target)
        if fd is None:
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
    OURS to remove: held by this process, and still the file this process's name points at
    (`_same_file`), or free (a leftover). A name that has moved on is not ours either.
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
    if _same_file(fd, target):  # only the name that still IS this file is ours to remove
        try:
            os.unlink(target)
        except OSError:
            pass
    try:
        os.close(fd)
    except OSError:
        pass


# The claim's name across an `exec`, for the watcher's own self-reload (see `daemon_loop`).
# `_LOCK_FDS` is memory, and an exec is exactly the thing that throws memory away, so the fd
# number travels in the environment instead — and the fd itself has to be made inheritable
# first, because Python opens descriptors close-on-exec and a claim that vanished at the exec
# would leave the watcher restarting with nothing to stand on.
LOCK_FD_ENV = "FBTODO_DAEMON_LOCK_FD"


def lock_handoff(path: str = "") -> int | None:
    """Hand this process's claim to the image an `exec` is about to put in its place.

    In plain words: an exec keeps the pid and the descriptor table, so the same open file
    description — and the kernel lock on it — is still there on the other side; what it does
    not keep is this registry. So the claim is written down where the next image can find it:
    the number in the environment, with the pid it belongs to, and the fd made inheritable.
    Returns the fd, or None when this process holds no claim — the exec is still safe then,
    the new image just claims from nothing the way any start does.
    """
    target = path or LOCK_PATH
    fd = _LOCK_FDS.get(target)
    if fd is None:
        return None
    try:
        os.set_inheritable(fd, True)
    except OSError:
        return None
    os.environ[LOCK_FD_ENV] = f"{os.getpid()}:{fd}"
    return fd


def lock_adopt(path: str = "") -> bool:
    """Take back the claim `lock_handoff` left for the image after an exec.

    True means this process now holds that claim; False means there was nothing to take — a
    plain start, or a hand-over that did not survive — and the caller should ask after the
    claim the ordinary way. The fd is believed only after the questions the kernel answers:
    it is open, the name still points at its file (the same name-versus-inode test
    `lock_ours` makes), and it STILL holds the exclusive lock — with the name checked once
    more after that, so a file replaced while the exec ran is let go rather than adopted
    unseen. The pid in the name must also be ours: an exec keeps the pid, so a name from
    any other process is a leftover — a stale variable, or a hand-over whose exec never
    happened — and not evidence of a claim. The variable is popped whatever the answer, so
    nothing this process starts inherits it by accident.
    """
    target = path or LOCK_PATH
    raw = os.environ.pop(LOCK_FD_ENV, None)
    if raw is None:
        return False
    try:
        pid_s, _, fd_s = raw.partition(":")
        fd = int(fd_s)
        if int(pid_s) != os.getpid():
            return False
        if not _same_file(fd, target):
            return False  # not this name's file: there is no claim here to take back
        if not lock_take(fd):
            return False
        if not _same_file(fd, target):
            # The name was replaced between the checks — while the exec ran, or just now.
            # The lock is on a file the name has left behind, a claim no reader can find,
            # so it is let go rather than adopted; the caller claims fresh from nothing.
            try:
                os.close(fd)
            except OSError:
                pass
            return False
    except (OSError, ValueError):
        return False
    _LOCK_FDS[target] = fd
    return True


__all__ = [
    "_LOCK_FDS", "_same_file", "_lock_named", "lock_open", "lock_take", "lock_ours",
    "lock_holder", "lock_peek", "claim_audit", "ROLE_PROCS", "ORPHAN_MIN_AGE_S",
    "_fbtodo_subcommand", "_proc_root", "_proc_environ", "KERNEL_COPY_CAP",
    "KERNEL_COPY_FLOOR", "ENV_ROOT_KEYS", "_env_tokens", "_env_names", "_env_read",
    "_env_root", "_environ_proc", "_environ_ps",
    "_kernel_copy", "_kernel_fields", "_environ_clipped", "_joined", "claim_processes",
    "claim_orphans", "daemon_pid", "write_lock", "claim_or_force",
    "clear_lock", "LOCK_FD_ENV", "lock_handoff", "lock_adopt",
]
