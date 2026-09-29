#!/usr/bin/env python3
"""Print a short description of what a freebuff session is currently working on.

Freebuff keeps each session under

    ~/.config/manicode/projects/<project>/chats/<session>/chat-messages.json

The session that belongs to a given shell is the one whose log.jsonl carries the
pid of a freebuff process descended from that shell, so a session running in
another terminal can never caption this tab. The text shown is the newest user
message in the session, falling back to its first prompt.

usage: session-task.py <root-pid> [max-chars]

Environment: FREEBUFF_PROJECTS_DIR overrides the sessions root (used by tests).
"""

import glob
import json
import os
import re
import subprocess
import sys

PROJECTS = os.environ.get("FREEBUFF_PROJECTS_DIR") or os.path.expanduser(
    "~/.config/manicode/projects"
)
DEFAULT_LIMIT = 44
CANDIDATES = 12
# The newest user message sits near the end of chat-messages.json, but the
# assistant's reply is written after it and can be large, so widen if needed.
WINDOWS = (256 * 1024, 1024 * 1024, 4 * 1024 * 1024, 16 * 1024 * 1024)
# A user message looks like {"id":"user-1789769735145","variant":"user",
# "content":"...","timestamp":"03:15 PM"} — allow any scalar fields in between.
USER_MESSAGE = re.compile(
    rb'"id"\s*:\s*"user-[^"]*"[^{}]*?"content"\s*:\s*"((?:[^"\\]|\\.)*)"'
)
PID = re.compile(rb'"pid":(\d+)')


def process_parents():
    """pid -> ppid for every process, so ancestry can be walked offline."""
    try:
        listing = subprocess.run(
            ["ps", "-eo", "pid=,ppid="], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    parents = {}
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) >= 2:
            try:
                parents[int(fields[0])] = int(fields[1])
            except ValueError:
                continue
    return parents


def descends_from(pid, root, parents):
    hops = 0
    while pid > 1 and hops < 64:
        if pid == root:
            return True
        pid = parents.get(pid, 0)
        hops += 1
    return False


def sessions():
    """Session directories, newest first."""
    found = glob.glob(os.path.join(PROJECTS, "*", "chats", "*", "chat-messages.json"))

    def modified(path):
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0

    found.sort(key=modified, reverse=True)
    return [os.path.dirname(path) for path in found[:CANDIDATES]]


def log_pid(session):
    try:
        with open(os.path.join(session, "log.jsonl"), "rb") as handle:
            first = handle.readline()
    except OSError:
        return None
    match = PID.search(first)
    return int(match.group(1)) if match else None


def last_user_message(session):
    path = os.path.join(session, "chat-messages.json")
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    for window in WINDOWS:
        try:
            with open(path, "rb") as handle:
                if size > window:
                    handle.seek(size - window)
                chunk = handle.read()
        except OSError:
            return None
        found = USER_MESSAGE.findall(chunk)
        if found:
            try:
                return json.loads(b'"' + found[-1] + b'"')
            except ValueError:
                return None
        if window >= size:
            break
    return None


def first_prompt(session):
    try:
        with open(os.path.join(session, "chat-meta.json")) as handle:
            return json.load(handle).get("firstPrompt")
    except (OSError, ValueError):
        return None


def to_int(value, default):
    """Shells can hand us an empty or missing pid; never crash on it."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def tidy(text, limit):
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rstrip()
    if " " in cut:  # prefer cutting between words, unless that costs half the text
        trimmed = cut[: cut.rfind(" ")].rstrip()
        if len(trimmed) >= limit // 2:
            cut = trimmed
    return cut + "…"


def main():
    root = to_int(sys.argv[1] if len(sys.argv) > 1 else None, 0)
    limit = to_int(sys.argv[2] if len(sys.argv) > 2 else None, DEFAULT_LIMIT)
    parents = process_parents()
    for session in sessions():
        pid = log_pid(session)
        if root and (pid is None or not descends_from(pid, root, parents)):
            continue
        text = tidy(last_user_message(session) or first_prompt(session) or "", limit)
        if text:
            print(text)
            return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
