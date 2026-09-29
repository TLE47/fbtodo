"""The remote host: one ssh per poll, answered by a script that runs at the far end.

The extractor (`NAS_EXTRACT`) is a Python program in its own right, sent over the wire and
run there, so the chunk that walks the remote journal never crosses the socket.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from .base import *  # noqa: F401,F403 — the package is one namespace
from .alerts import *  # noqa: F401,F403 — the package is one namespace
from .scan import *  # noqa: F401,F403 — the package is one namespace

# ==================================================================== NAS backend
def nas_ssh(script: str, host: str, timeout: float = NAS_TIMEOUT) -> bytes:
    """Run one script on the NAS, returning its stdout.

    FBTODO_NAS may hold a bare ssh target or a whole command (the self-check points
    it at a fake that speaks this same protocol), so it is split rather than assumed.
    """
    target = shlex.split(host)
    if target and (os.path.sep in target[0] or target[0] in ("sh", "bash", "zsh")):
        attempts = [target + [script]]
    else:
        base = [
            "ssh",
            *target,
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=6",
            "-o",
            "LogLevel=ERROR",
        ]
        attempts = [
            # One handshake, then reuse: a fresh handshake per poll costs about an
            # order of magnitude more than the read it carries.
            base
            + [
                "-o",
                "ControlMaster=auto",
                "-o",
                f"ControlPath={NAS_CONTROL}",
                "-o",
                "ControlPersist=60",
                script,
            ],
            # ...but a stale or unusable control socket must not cost us the poll.
            base + [script],
        ]
    failure: RuntimeError | None = None
    for argv in attempts:
        try:
            proc = subprocess.run(argv, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"ssh {host} timed out after {timeout:.0f}s")
        except OSError as exc:
            raise RuntimeError(f"cannot run ssh: {exc}")
        if proc.returncode == 0:
            return proc.stdout
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        failure = RuntimeError(f"ssh exited {proc.returncode}: {(detail[-1] if detail else '')[:140]}")
    raise failure or RuntimeError("ssh failed")


def nas_probe(
    host: str,
    root: str,
    project: str,
    chat: str | None = None,
    prev_mtime: int | None = None,
    marker: str = "$HOME/.fb-session",
    timeout: float = NAS_TIMEOUT,
) -> dict:
    """ONE ssh round trip: which chat, how big, is the session alive, and its last todo list.

    Five header lines — DIR, SIZE, MTIME, LIVE, FB — then the extractor's single line,
    which is either `NONE`, `UNCHANGED` (the conversation file has not moved since
    `prev_mtime` — the parse is skipped outright, so an idle poll costs a stat), `ERR …`,
    or one compact JSON object. Two more lines ride after it, `PATCH …` and `ALERT …`: the
    tails of the hook's patch log and its notifier's log, so the pane's patch row costs
    nothing extra over the ssh it is already making.

    `chat` pins one session (the name of a chat directory under …/chats) instead of the
    newest, which is also what lets the self-check point this at a fixture.

    LIVE is "a freebuff process exists somewhere on the NAS". FB is the sharper answer
    from the host's `fb` wrapper marker (`pid started dir`): 1 = that session is live,
    0 = the marker is stale, `-` = no marker, i.e. the hook is not installed. The marker
    also names the directory `fb` started in, which is the project whose store is then
    read — `fb` is run from all over the NAS, so guessing `--nas-project` would show
    another project's list.
    """
    q = shlex.quote
    marker_expr = q(marker) if marker != "$HOME/.fb-session" else '"$HOME/.fb-session"'
    if chat:
        name = os.path.basename(str(chat).rstrip("/"))
        head = (
            f"root={q(root)}; p={q(project)}; "
            f"d={q(root)}/{q(project)}/chats/{q(name)}/"
        )
    else:
        head = (
            f"root={q(root)}; p={q(project)}; "
            # The fb marker, if there is one: pid, start time, container cwd.
            f"fbpid=; fbt=\"-\"; fbd=\"\"; m={marker_expr}; "
            "if [ -s \"$m\" ]; then read -r fbpid fbt fbd < \"$m\" 2>/dev/null; "
            "if [ -n \"$fbpid\" ] && kill -0 \"$fbpid\" 2>/dev/null; then FB=1; "
            "else FB=0; fi; else FB='-'; fi; "
            # A marker that names a project with a store says where the session STARTED,
            # which is not always where its store lands: `fb` maps a cwd the container
            # cannot see, and a container cwd of `/` makes the project nameless — that
            # session writes `projects/chats/<thread>` while the marker names `host`, so
            # trusting the marker read a two-day-old session and could never show the live
            # one. A live session settles it: follow the store that was written last,
            # wherever it is (the marker is what is used when nothing is live).
            "mp=\"\"; [ -n \"$fbd\" ] && { case \"$fbd\" in */|*//) fbd=${fbd%/};; esac; "
            "[ -d \"$root/${fbd##*/}\" ] && mp=${fbd##*/}; }; "
            "d=\"\"; if [ \"$FB\" = 1 ]; then "
            "d=$(ls -1dt $root/*/chats/*/ $root/chats/*/ 2>/dev/null | head -1); "
            "case \"$d\" in $root/chats/*) p=\"\";; $root/*) rest=${d#$root/}; p=${rest%%/*};; esac; "
            "fi; "
            "if [ -z \"$d\" ]; then [ -n \"$mp\" ] && p=$mp; "
            "cd $root/$p/chats 2>/dev/null || { echo NODIR; exit 0; }; "
            "d=$(ls -1dt */ 2>/dev/null | head -1); fi; "
            "[ -n \"$d\" ] || { echo NODIR; exit 0; }"
        )
    msgs = '"${d}chat-messages.json"'
    # Pipe-separated, because an empty field would otherwise collapse and shift the
    # project into the directory slot (a marker-less probe has an empty directory).
    fb_line = (
        f"printf 'FB %s|%s|%s\\n' \"${{FB:--}}\" \"${{fbd:-}}\" \"${{p:-}}\"; "
        if not chat
        else "printf 'FB -||\\n'; "
    )
    script = (
        f"{head}; "
        f'[ -n "${{d}}" ] || {{ echo NODIR; exit 0; }}; '
        "printf 'DIR %s\\n' \"$d\"; "
        # A session is reportable the moment its directory exists, transcript or not:
        # the transcript only appears once a turn has completed, and calling a live
        # session "no session" during its first turn would be a lie.
        f"if [ -f {msgs} ]; then "
        f"printf 'SIZE %s\\n' \"$(wc -c < {msgs} 2>/dev/null || echo 0)\"; "
        # -c is the Linux/BusyBox spelling; -f is BSD. The NAS needs the first, the
        # self-check's local stand-in needs the second.
        f"printf 'MTIME %s\\n' \"$(stat -c %Y {msgs} 2>/dev/null || stat -f %m {msgs} 2>/dev/null || echo 0)\"; "
        "else printf 'SIZE 0\\nMTIME 0\\n'; fi; "
        f"printf 'LIVE %s\\n' \"$(pgrep -f {q(nas_pgrep(NAS_PROC))} >/dev/null 2>&1 && echo 1 || echo 0)\"; "
        f"{fb_line}"
        f"if [ -f {msgs} ]; then "
        # PATH is spelled out because a non-login ssh shell has no login PATH, and the
        # extractor is python (the NAS ships python3; the freebuff image does not matter).
        "export PATH=/usr/local/bin:/usr/bin:/bin:$PATH; "
        f"command -v python3 >/dev/null 2>&1 || {{ echo 'ERR no python3 on the NAS'; exit 0; }}; "
        # Whole seconds, matching `stat %Y` in the header and the extractor's own check:
        # comparing an ms value against a second-resolution mtime never matched, so the
        # skip never fired.
        f"python3 -c {q(NAS_EXTRACT)} {msgs} {q(str(int(prev_mtime // 1000) if prev_mtime else 0))}; "
        "else echo NONE; fi; "
        # ...and the two facts about the patches themselves, gathered in the same round
        # trip: the tails are a few hundred bytes each, and the far side does no
        # classifying — BusyBox sh is a poor place for that. They ride AFTER the state line
        # (the parser reads them back by prefix, so a reply without them still parses).
        f"PT=$(tail -n {PATCH_TAIL_LINES} {q(NAS_PATCH_LOG)} 2>/dev/null | tr -d '\\r'"
        f" | cut -c1-220 | tr '\\n' '\\034'); "
        f"AT=$(tail -n 2 {q(NAS_ALERT_LOG)} 2>/dev/null | tr -d '\\r'"
        f" | cut -c1-220 | tr '\\n' '\\034'); "
        "printf 'PATCH %s\\nALERT %s\\n' \"$PT\" \"$AT\"; exit 0"
    )
    try:
        out = nas_ssh(script, host, timeout)
    except RuntimeError as exc:
        return {"dir": None, "error": str(exc)}
    if not out.startswith(b"DIR "):
        return {"dir": None, "error": "no session on the NAS"}
    parts = out.split(b"\n", 6)
    if len(parts) < 6:
        return {"dir": None, "error": "malformed reply from the NAS"}

    def num(line: bytes, prefix: str) -> int:
        try:
            return int(line[len(prefix):].strip() or 0)
        except ValueError:
            return 0

    name = parts[0][4:].decode("utf-8", "replace").strip()
    fb_bits = parts[4][3:].decode("utf-8", "replace").strip().split("|")
    fb_flag = (fb_bits[0] if fb_bits else "-") or "-"
    fb_dir = fb_bits[1] if len(fb_bits) > 1 else ""
    fb_project = fb_bits[2] if len(fb_bits) > 2 else ""
    state_line = parts[5].strip().decode("utf-8", "replace")
    state = {"kind": "none"}
    if state_line.startswith("{"):
        try:
            state = {"kind": "list", **json.loads(state_line)}
        except Exception:
            state = {"kind": "error", "error": "unparsable reply from the NAS"}
    elif state_line == "NONE" or state_line.startswith("NONE "):
        # `NONE {json}` carries the tool names this session did call, which is the only
        # thing there is to say when it has written no list.
        state = {"kind": "none"}
        if len(state_line) > 5:
            try:
                state["tools"] = json.loads(state_line[5:]).get("tools") or {}
            except Exception:
                pass
    elif state_line == "UNCHANGED":
        state = {"kind": "unchanged"}
    elif state_line.startswith("ERR"):
        state = {"kind": "error", "error": state_line[4:].strip() or "the NAS could not read its store"}
    # The patch tail, read back by PREFIX rather than by position: a reply from a build
    # that does not send it (or one whose logs are both empty) parses exactly as it did.
    tail = parts[6].decode("utf-8", "replace") if len(parts) > 6 else ""
    patch_blob = alert_blob = ""
    # split on "\n" and NOT splitlines(): the far side joins its log entries with \x1c, and
    # `str.splitlines` treats that as a line boundary too — it cut every complaint off the
    # entry it belonged to, leaving a failure with no reason to show.
    for line in tail.split("\n"):
        if line.startswith("PATCH "):
            patch_blob = line[6:]
        elif line.startswith("ALERT "):
            alert_blob = line[6:]
    return {
        "dir": name,
        "thread": name.rstrip("/") or None,
        "size": num(parts[1], b"SIZE "),
        "mtime_ms": num(parts[2], b"MTIME ") * 1000 or None,
        "live": parts[3][5:].strip() == b"1",
        "fb": fb_flag,                # 1 live, 0 stale marker, - no marker at all
        "fb_dir": fb_dir,
        "fb_project": fb_project,
        "state": state,
        "patch": nas_patch_from_tail(patch_blob),
        "alert": alert_from_lines(alert_blob.split("\x1c"), NAS_ALERT_LOG, utc=True),
    }


def nas_liveness(args) -> dict:
    """The cheapest NAS question there is: is a freebuff SESSION running over there?

    The pane-open watcher asks this and nothing else, so the transcript is never touched —
    no parse, no 2.8 MB read, just the marker the host's `fb` keeps and one pgrep. `fb`
    runs inside the container and cannot reach this Mac's tmux, so the only way a pane can
    appear when a NAS session STARTS is for something local to watch for exactly this.
    """
    if not args.nas_host:
        return {
            "error": NAS_UNCONFIGURED, "alive": False, "fb": "-", "dir": "",
            "live": False, "started": "",
        }
    marker = getattr(args, "fb_marker", None) or "$HOME/.fb-session"
    expr = shlex.quote(marker) if marker != "$HOME/.fb-session" else '"$HOME/.fb-session"'
    script = (
        f"m={expr}; fbpid=; fbd=; "
        # A marker whose pid is gone was orphaned by a killed ssh: not a live session.
        "if [ -s \"$m\" ]; then read -r fbpid fbt fbd < \"$m\" 2>/dev/null; fi; "
        "FB='-'; if [ -n \"$fbpid\" ]; then "
        "if kill -0 \"$fbpid\" 2>/dev/null; then FB=1; else FB=0; fi; fi; "
        f"LIVE=$(pgrep -f {shlex.quote(nas_pgrep(NAS_PROC))} >/dev/null 2>&1 && echo 1 || echo 0); "
        # The marker's start time comes back too: it is how a pane is placed when several
        # ssh sessions to this NAS are open (see `nas_ssh_pane`).
        "printf 'FB %s|%s|%s\\n' \"$FB\" \"${fbd:-}\" \"${fbt:-}\"; printf 'LIVE %s\\n' \"$LIVE\""
    )
    try:
        out = nas_ssh(script, args.nas_host)
    except RuntimeError as exc:
        return {
            "error": str(exc), "alive": False, "fb": "-", "dir": "",
            "live": False, "started": "",
        }
    fb, directory, started, live = "-", "", "", False
    for line in out.decode("utf-8", "replace").splitlines():
        if line.startswith("FB "):
            bits = line[3:].split("|", 2)
            fb = (bits[0].strip() or "-") if bits else "-"
            directory = bits[1].strip() if len(bits) > 1 else ""
            started = bits[2].strip() if len(bits) > 2 else ""
        elif line.startswith("LIVE "):
            live = line[5:].strip() == "1"
    return {
        "fb": fb,
        "dir": directory,
        "started": started,          # the NAS's clock, ISO UTC; "" when there is no marker
        "live": live,
        "alive": fb == "1" or (fb == "-" and live),
        "error": None,
    }


def read_nas(args) -> dict:
    """The last todo list the NAS session wrote, from its conversation store.

    Turn-granular, and honest about it: the NAS build writes no tool inputs to its
    journal (see NAS_EXTRACT), so `chat-messages.json` — rewritten per completed turn —
    is the only place a list can be found. The same extractor that would run remotely
    here runs inside the self-check against a fixture, so both paths are the same code.
    """
    host, root, project = args.nas_host, args.nas_root, args.nas_project
    if not host or not root or not project:
        # No built-in target (see NAS_HOST): say which knob is missing rather than letting
        # ssh fail with "could not resolve hostname" three frames down.
        return clean_observation({
            "todos": [], "no_log": True, "title": "", "iteration": None, "ts": None,
            "goal": None, "now": None, "nudge": None, "tool_calls": {},
            "nas": {"dir": None, "error": NAS_UNCONFIGURED},
        })
    prev = read_json(os.path.join(SCRATCH, "fbtodo-nas.json"), {}) or {}
    prev_mtime = prev.get("mtime_ms") if prev.get("dir") else None
    marker = getattr(args, "fb_marker", None) or "$HOME/.fb-session"
    probe = nas_probe(host, root, project, args.chat or None, prev_mtime, marker)
    # What the remote session has called, as counted by the extractor over there.
    tools = (probe.get("state") or {}).get("tools") or {}
    # The `fb` marker is the sharper signal: it is written by the wrapper the operator
    # runs, so it says WHICH session is live and where it started. Without one (no wrapper
    # hook installed) the process probe still answers "is anything running".
    fb_flag = probe.get("fb") or "-"
    nas = {
        "dir": probe.get("dir"),
        "live": bool(probe.get("live")),
        "fb": fb_flag,
        "fb_live": fb_flag == "1",
        "fb_dir": probe.get("fb_dir") or "",
        "fb_project": probe.get("fb_project") or "",
        "alive": fb_flag == "1" or (fb_flag == "-" and bool(probe.get("live"))),
        "size": probe.get("size"),
        "mtime_ms": probe.get("mtime_ms"),
        # A session directory exists before its transcript does (the transcript is written
        # per completed turn), so "no transcript" and "no list" are different answers.
        "has_transcript": bool(probe.get("size")),
        "unchanged": False,
    }
    empty = {
        "todos": [], "no_log": True, "title": "", "iteration": None, "ts": None,
        "goal": None, "now": None, "nudge": None, "nas": nas, "tool_calls": tools,
        # The patch row's two facts come back on every probe, list or no list: a NAS pane
        # waiting for a session is exactly when "did the hook's patch step come out
        # clean?" is worth showing.
        "patch": probe.get("patch"), "alert": probe.get("alert"),
    }
    if not probe.get("dir"):
        nas["error"] = probe.get("error") or "no session on the NAS"
        return clean_observation(empty)
    state = probe.get("state") or {}
    # The far side skips its parse while the transcript has not moved, so an "unchanged"
    # answer carries no list: serve the one we already extracted, or the pane would blank
    # out every idle poll. A missing cache (wiped scratch dir) forces a real read.
    if state.get("kind") == "unchanged":
        if (prev.get("dir") or "") == nas["dir"] and prev.get("todos") is not None:
            nas["unchanged"] = True
            return {
                "todos": prev.get("todos") or [],
                "ts": prev.get("ts"),
                "calls": prev.get("calls"),
                "goal": prev.get("goal"),
                "now": prev.get("now"),
                "nudge": prev.get("nudge"),
                "title": "",
                "iteration": None,
                "thread": probe.get("thread"),
                "tool_calls": prev.get("tool_calls") or {},
                "nas": nas,
                "patch": probe.get("patch"), "alert": probe.get("alert"),
            }
        probe = nas_probe(host, root, project, args.chat or None, None, marker)
        state = probe.get("state") or {}
        tools = (state.get("tools") or {}) or tools
        nas["dir"] = probe.get("dir") or nas["dir"]
        nas["size"], nas["mtime_ms"] = probe.get("size"), probe.get("mtime_ms")
        nas["has_transcript"] = bool(probe.get("size"))
        nas["fb"] = probe.get("fb") or nas["fb"]
        nas["fb_live"] = nas["fb"] == "1"
        nas["alive"] = nas["fb_live"] or (nas["fb"] == "-" and bool(probe.get("live")))
    if state.get("kind") == "error":
        nas["error"] = state["error"]
        return empty
    if state.get("kind") == "list":
        ts = state.get("ts")
        ms = _iso_ms(ts) or (ts if isinstance(ts, int) else None)
        todos = state.get("todos") or []
        goal = state.get("goal") or None
        now = state.get("now") or None
        # The transcript's newest user message being a continuation is a nudge: the NAS
        # path has no prompt records, but it does have the requests themselves.
        nudge = state.get("nudge") or None
        atomic_write_json(
            os.path.join(SCRATCH, "fbtodo-nas.json"),
            {"dir": nas["dir"], "mtime_ms": nas["mtime_ms"], "todos": todos, "ts": ms,
             "calls": state.get("calls"), "goal": goal, "now": now, "nudge": nudge,
             "tool_calls": tools},
        )
        return clean_observation({
            "todos": todos,
            "ts": ms,
            "calls": state.get("calls"),
            "goal": goal,
            "now": None if nudge else (now if now and now != goal else None),
            "nudge": nudge,
            "title": "",
            "iteration": None,
            "thread": probe.get("thread"),
            "tool_calls": tools,
            "nas": nas,
            "patch": probe.get("patch"), "alert": probe.get("alert"),
        })
    # `NONE` means this session has written no list yet. That is not an error and not a
    # zero: the pane says "no write_todos call yet", rather than claiming an empty list.
    empty["thread"] = probe.get("thread")
    return clean_observation(empty)


__all__ = [
    "nas_ssh", "nas_probe", "nas_liveness", "read_nas",
]
