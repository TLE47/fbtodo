"""The claim files: who is watching, held by the kernel rather than by a number."""

from __future__ import annotations

try:
    import fcntl
except ImportError:  # pragma: no cover - no flock, so a claim is the record alone
    fcntl = None
import json
import os
import signal
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


def lock_open(path: str) -> int:
    """A 0600 fd for the claim file, created if it is not there yet."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
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
    try:
        return os.fstat(fd).st_ino == os.stat(path).st_ino
    except OSError:
        return False


def lock_holder(path: str) -> int | None:
    """The pid holding the claim on `path`, or None when nobody does.

    `flock` cannot say WHO holds a lock, so this asks the one question that does have an
    answer: can the claim be taken? If it can, nobody holds it — and the record sitting
    there is a leftover, so it is removed instead of believed (a reader that believed it
    would report a watcher that is not running). If it cannot, the pid inside the record
    is the holder's: it was written by whoever took the claim, and the claim is still held.
    """
    fd = lock_open(path)
    try:
        if lock_take(fd):
            if fcntl is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                os.unlink(path)  # free, so the record is a leftover
            except OSError:
                pass
            return None
        rec = read_json(path, {}) or {}
        pid = rec.get("pid")
        # 0 means "held, and the record says nothing": the caller refuses to start rather
        # than run a second watcher over one scratch dir, but has no number to print.
        return int(pid) if pid else 0
    finally:
        os.close(fd)


def daemon_pid() -> int | None:
    return lock_holder(LOCK_PATH)


def write_lock(cwd: str, instance_pid, path: str | None = None,
               extra: dict | None = None) -> bool:
    """Claim `path` for this process, and write the record a reader sees.

    `path` exists for the NAS watcher and the pane keeper, whose claims on the scratch dir
    are their own files: three roles, three claims, and they must not block each other.
    `version` lets a reader tell a live watcher from one left behind by an upgrade.

    False when someone else holds it — the caller must stop rather than run a second
    watcher over one scratch dir. The record is written THROUGH the locked fd and never by
    rename: replacing the file would leave the new copy unlocked and the claim lost.
    """
    target = path or LOCK_PATH
    fd = _LOCK_FDS.get(target)
    if fd is None:
        fd = lock_open(target)
        if not lock_take(fd):
            os.close(fd)
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
    OURS to remove: held by this process, or free (a leftover).
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
    try:
        os.unlink(target)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass


__all__ = [
    "_LOCK_FDS", "lock_open", "lock_take", "lock_ours", "lock_holder", "daemon_pid",
    "write_lock", "claim_or_force", "clear_lock",
]
