"""Every live session in one pane: `fbtodo board`.

People run two or three agents at once — one per project, or two in the same one — and each
has its own list, its own clock and its own pane. This module answers the other question:
what is EVERY live session doing, in one frame.

The sessions come from the same two local stores the single-session pane already reads: the
CLI journals under `--cli-root`, and the desktop app's live threads across every project
store. Nothing here reads a new kind of store and nothing here invents a state — a CLI row is
`read_cli`'s own answer and a desktop row is `live_threads`' own — so the board cannot
disagree with a pane about what a list says. There is no third store here: the board reads
the two that live on this machine, and the board's whole promise is that one look is cheap.
"""

from __future__ import annotations

import glob
import json
import os
import shutil as _shutil
import signal
import sqlite3
import sys
import time
from .base import *  # noqa: F401,F403 — the package is one namespace
from .scan import *  # noqa: F401,F403 — the package is one namespace
from .desktop import *  # noqa: F401,F403 — the package is one namespace
from .sources import *  # noqa: F401,F403 — the package is one namespace
from .tasks import *  # noqa: F401,F403 — the package is one namespace
from .render import *  # noqa: F401,F403 — the package is one namespace
from .panes import *  # noqa: F401,F403 — the package is one namespace

# In plain words: which sessions belong on the board. A session is live when its own store
# moved recently — the journal for a CLI chat (appended every iteration while the agent works)
# and the newest `write_todos` for a desktop thread (the app commits per turn, so a list is the
# only clock it has). Ninety minutes is the same generous window the desktop source already
# gives its stacked threads, because the question is the same one: is this session one a person
# would still want to see, or is it yesterday's?
#
# That window is the SECOND half of the answer, not the whole of it, and the board now asks the
# first half the same way the single-session pane does: a journal whose last agent record is a
# `shouldEndTurn` boundary has ENDED, whatever has touched the file since (see
# `journal_liveness`, and the measured failure in its own note — the desktop app appends its own
# records to the same journal, so a chat that finished at 11:20 read as "just written" at 12:04
# and stayed on the board, one of several sessions that were not running at all).
BOARD_LIVE_MS = 90 * 60 * 1000

# ...and "working right now", which is a much shorter question: a store that moved in the last
# few minutes is a session mid-turn, and that is what the board's arrow marks.
BOARD_RUNNING_MS = 3 * 60 * 1000

# How many sessions one board draws. Six is a frame a person reads; the rest are counted
# (`+2 more live`), never silently dropped, and `--board-max` raises it.
BOARD_MAX = 6

# How many candidate journals the board will WALK, as opposed to merely stat. Reading a journal
# is the expensive half (a chunked backward scan), so a store with fifty chats that all moved
# today must not turn one `board` into fifty scans: the newest `BOARD_SCAN_MAX` are read and the
# rest are counted. Nothing is lost that a wider window would not find again.
BOARD_SCAN_MAX = 12

# Steps drawn per session. The board is a glance across sessions, not a replacement for the
# session's own pane, so two rows each is what keeps six sessions readable.
BOARD_ROWS = 2


def board_activity(row: dict) -> int:
    """When this session's store last moved — what the board sorts and ages by."""
    return int(row.get("store_mtime_ms") or row.get("ts") or 0)


def board_slug(db: str) -> str:
    """A project's name from its store's own directory name.

    The app names a project directory `<name>-<uuid>`, so the name is everything before the
    first dash. A project whose own name holds a dash loses the tail of it here, which is a
    label's problem and not a lookup's: `project.json` beside the store is asked first (see
    `project_path_of`), and this is only the fallback for a store that has none.
    """
    return os.path.basename(os.path.dirname(db)).split("-")[0] or "?"


def board_label(chat: str) -> str:
    """The project a CLI chat belongs to: the directory that holds its `chats`."""
    return os.path.basename(os.path.dirname(os.path.dirname(chat.rstrip("/")))) or "?"


def board_row_cli(chat: str, st: dict, mtime: int | None, now_ms: int) -> dict:
    """One CLI session as a board row, from `read_cli`'s own answer.

    The row is deliberately a SUBSET of the state the pane draws — the list, its heading, its
    clock and the two newer-request lines — because the board's rows are one line each and a
    state carries a dozen fields that only make sense at full width.
    """
    return {
        "backend": "cli",
        "label": clean_text(board_label(chat)),
        "session": os.path.basename(chat.rstrip("/")),
        "target": chat,
        "title": clean_text(st.get("title") or ""),
        "goal": st.get("goal"),
        "goal_stale": bool(st.get("goal_stale")),
        "now": st.get("now") if not st.get("nudge") else None,
        "nudge": st.get("nudge"),
        "todos": st.get("todos") or [],
        "ts": st.get("ts"),
        "store_mtime_ms": mtime,
        "turn": st.get("turn") or {},
        "turn_ended": bool(st.get("turn_ended")),
        "turn_running": False,
        "running": bool(mtime and now_ms - mtime <= BOARD_RUNNING_MS),
    }


def board_cli_sessions(root: str, window_ms: int, now_ms: int, limit: int) -> list:
    """Every CLI chat that is still WORKING inside the window, newest activity first.

    The walk is `glob` + `stat` first and `read_cli` only for the few that qualify, in that
    order: a store holding a hundred finished chats costs a hundred stats and one scan.

    "Live" is the single-session pane's own question, asked the same way and by the same code
    (`journal_liveness`): the agent's last record decides, and only a journal with no boundary
    in its tail falls back to the quiet window. A chat whose turn ENDED is not a session on
    this board however recently its file was written, which is the whole of what was wrong —
    the board counted finished chats as running sessions while the pane beside it had already
    let them go.

    The two halves keep their budgets apart, because they cost different amounts. The window
    test is a `stat` per chat and the boundary test reads only the journal's tail (cached per
    file identity, and it stops at the first agent record walking backwards), while `read_cli`
    is a full parse: so `BOARD_SCAN_MAX` still bounds the parses, and a chat the boundary ends
    costs a tail read rather than one of them.
    """
    pattern = os.path.join(os.path.expanduser(root or DEFAULT_CLI_ROOT), "*", "chats", "*")
    cands = []
    for chat in glob.glob(pattern):
        if not os.path.isdir(chat):
            continue
        mtime = store_mtime_ms(chat, "cli")
        if not mtime:
            continue
        if window_ms and now_ms - mtime > window_ms:
            continue
        cands.append((mtime, chat))
    cands.sort(key=lambda mc: -mc[0])
    out: list = []
    scans = 0
    for mtime, chat in cands:
        if len(out) >= limit or scans >= BOARD_SCAN_MAX:
            break
        if journal_liveness(chat)[1]:
            continue
        scans += 1
        out.append(board_row_cli(chat, read_cli(chat), mtime, now_ms))
    return out


def board_desktop_sessions(dbs: list, window_ms: int, now_ms: int, limit: int) -> list:
    """Every live desktop thread across these stores, newest list first.

    `live_threads` is the desktop source's own answer to "which threads are live", asked here
    with no followed thread (the board follows none: every row is somebody else's session).
    """
    out: list = []
    for db in dbs:
        if len(out) >= limit:
            break
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        except sqlite3.Error:
            continue
        try:
            rows = live_threads(con.cursor(), None, limit - len(out), now_ms, window_ms)
        except sqlite3.Error:
            rows = []
        finally:
            con.close()
        project = project_path_of(db)
        label = os.path.basename(str(project).rstrip("/")) if project else board_slug(db)
        for row in rows:
            out.append({
                "backend": "desktop",
                "label": clean_text(label),
                "session": row.get("id"),
                "target": db,
                "title": clean_text(row.get("title") or ""),
                # The thread's own heading and staleness, read by `live_threads` with the same
                # committed-prose rules the pane uses: a desktop row says `goal:`/`stale goal:`
                # like a CLI one, and the three readers agree about the same thread by
                # construction — the invariant check in the self-check holds them to it.
                "goal": row.get("goal"),
                "goal_stale": bool(row.get("goal_stale")),
                "now": None,
                "nudge": None,
                "todos": row.get("todos") or [],
                "ts": row.get("source_updated_ms"),
                "store_mtime_ms": row.get("source_updated_ms"),
                "turn": {},
                "turn_ended": False,
                "turn_running": False,
                "running": bool(row.get("running")),
            })
    return out


def board_sessions(args, now_ms: int | None = None) -> list:
    """Every live session this machine has, as rows, most recently active first.

    Two local stores, one order: the CLI journals under `--cli-root` and the desktop app's
    stores under `--db`. A row is live by its own store's clock (see `BOARD_LIVE_MS`) and, for
    a CLI chat, only while its agent is still working (see `board_cli_sessions`), so a finished
    session drops off the board by itself — there is no bookkeeping to go stale, and nothing to
    clean up.
    """
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    window_ms = max(0, int(getattr(args, "board_live", BOARD_LIVE_MS // 60_000) or 0)) * 60_000
    limit = max(1, int(getattr(args, "board_max", BOARD_MAX) or BOARD_MAX))
    rows = board_cli_sessions(
        getattr(args, "cli_root", DEFAULT_CLI_ROOT), window_ms, now_ms, limit
    )
    if len(rows) < limit:
        dbs = sorted(
            glob.glob(getattr(args, "db", DEFAULT_DB_GLOB) or DEFAULT_DB_GLOB),
            key=os.path.getmtime, reverse=True,
        )
        rows += board_desktop_sessions(dbs, window_ms, now_ms, limit - len(rows))
    rows.sort(key=board_activity, reverse=True)
    return rows[:limit]


def board_counts(row: dict) -> tuple:
    """(done, total) for a row's list — counted from the list being drawn, as everywhere."""
    todos = row.get("todos") or []
    return sum(1 for t in todos if isinstance(t, dict) and t.get("completed")), len(todos)


def board_head(row: dict, now_ms: int, width: int, color: bool = False) -> str:
    """The line that names a session: its project, where it comes from, and its clock.

    One line per session, and the same fields in the same order in both renderers, so the
    plain text a script reads and the frame a person reads say the same thing.
    """
    c = paint(color)
    done, total = board_counts(row)
    name = row.get("label") or row.get("title") or (row.get("session") or "")[:8] or "?"
    mark = "\u2794" if row.get("running") else "\u25cb"
    bits = [
        f"{row.get('backend') or '?'}",
        name,
        f"{done}/{total}" if total else "no list",
        fmt_age(row.get("ts") or row.get("store_mtime_ms"), now_ms),
    ]
    if row.get("goal_stale"):
        bits.append("stale heading")
    if row.get("nudge"):
        bits.append(f"nudge · {row['nudge']}")
    elif row.get("now"):
        bits.append(f"now · {row['now']}")
    return _board_clip(
        c("33" if row.get("running") else "2", mark) + "  " + "  ·  ".join(bits), width
    )


# Named apart from `base._clip` on purpose: that one counts characters for an unstyled line,
# and these rows carry colour — a clip that counted the escape sequences would cut a line long
# before its width, or paint past the pane's edge. `_clip_cells` measures the visible cells.
def _board_clip(text: str, width: int) -> str:
    """A styled line cut to the pane's width, measured in cells rather than characters."""
    return text if _cell_width(_plain(text)) <= width else _clip_cells(text, width)


def board_plain(rows: list, now_ms: int, width: int = 80, color: bool = False,
                steps: int = BOARD_ROWS) -> str:
    """The machine-readable board: one heading per session, then its first few steps.

    Deliberately the boring renderer, as everywhere else: a script reading the board wants a
    heading line per session and an indented step under it, and no borders to strip.
    """
    c = paint(color)
    lines = [c("2", f"board  {len(rows)} live session{'' if len(rows) == 1 else 's'}"
                    f"  ·  live {time.strftime('%H:%M:%S', time.localtime(now_ms / 1000))}")]
    for row in rows:
        lines.append("  " + board_head(row, now_ms, max(16, width - 2), color))
        goal = str(row.get("goal") or "")
        if goal:
            label = "stale goal: " if row.get("goal_stale") else "goal: "
            lines.append("      " + c("2", label + goal))
        todos = row.get("todos") or []
        for t in todos[:steps]:
            if not isinstance(t, dict):
                continue
            task = " ".join(str(t.get("task") or "").split())
            mark = c("32", "[x]") if t.get("completed") else c("33", "[ ]")
            lines.append("      " + mark + " " + task)
        if not todos:
            lines.append("      " + c("2", no_list_reason(row)))
        if len(todos) > steps:
            rest = len(todos) - steps
            lines.append("      " + c("2", f"… {rest} more step{'s' if rest > 1 else ''}"))
    if not rows:
        lines.append(c("2", "  no live sessions — nothing moved inside the window"))
    return "\n".join(_clamp_widths(lines, width))


def board_frame(rows: list, now_ms: int, width: int = 80, height: int | None = None,
                steps: int = BOARD_ROWS, theme: dict | None = None,
                truecolor: bool | None = None) -> str:
    """The framed board: one box, a heading per session, and that session's first steps.

    `theme` and `truecolor` are the same pair every other frame takes, and passing them in is
    what makes this a function of its arguments alone — the same rows and the same clock give
    the same bytes, which is what lets a live board diff one paint against the last.
    """
    c = paint(True)
    theme = read_theme() if theme is None else theme
    truecolor = _supports_truecolor() if truecolor is None else truecolor
    st = _styles(theme, truecolor)
    accent, muted = st["accent"], st["muted"]

    def frame(text: str) -> str:
        return c(st["faint"], text)

    def badge(text: str) -> str:
        return c(st["badge"], text)

    inner = max(10, width - 4)
    clock = time.strftime("%H:%M:%S", time.localtime(now_ms / 1000))
    right = f"{len(rows)} live · {clock}"
    out = [_top_border(width, frame, "FREEBUFF BOARD", right, badge)]
    if not rows:
        out.append(_divider_row(width, frame))
        out.append(_frame_row(c(muted, "no live sessions — nothing moved inside the window"),
                              width, frame))
        out.append(_bottom_row(width, frame))
        return "\n".join(_clamp_rows(out, height, head=1, tail=1))
    for row in rows:
        done, total = board_counts(row)
        running = bool(row.get("running"))
        ink = accent if running else muted
        mark = "\u2794" if running else "\u25cb"
        name = row.get("label") or row.get("title") or (row.get("session") or "")[:8] or "?"
        count = f"{done}/{total}" if total else "no list"
        age = fmt_age(row.get("ts") or row.get("store_mtime_ms"), now_ms)
        head = (f"  {c(ink, mark)}  {c(st['active'] if running else muted, name)}"
                f"  {c(muted, '· ' + str(row.get('backend')) + ' · ' + age)}")
        tail = c(st["success"] if total and done == total else muted, count)
        pad = max(1, inner - _cell_width(_plain(head)) - _cell_width(_plain(tail)))
        out.append(_frame_row(head + " " * pad + tail, width, frame))
        goal = str(row.get("goal") or "")
        if goal:
            label = "stale goal: " if row.get("goal_stale") else "goal: "
            out.append(_frame_row("      " + c(muted, label) + c(st["active"], goal),
                                  width, frame))
        todos = row.get("todos") or []
        for t in todos[:steps]:
            if not isinstance(t, dict):
                continue
            task = " ".join(str(t.get("task") or "").split())
            done_flag = bool(t.get("completed"))
            marker = c(st["tick"], "[x]") if done_flag else c(ink, "[ ]")
            out.append(_frame_row(
                "      " + marker + " " + c(st["done"], task) if done_flag
                else "      " + marker + " " + task, width, frame))
        if not todos:
            out.append(_frame_row("      " + c(muted, no_list_reason(row)), width, frame))
        if len(todos) > steps:
            rest = len(todos) - steps
            out.append(_frame_row(
                "      " + c(muted, f"… {rest} more step{'s' if rest > 1 else ''}"),
                width, frame))
        if row.get("nudge"):
            out.append(_frame_row("      " + c("33", f"nudge · {row['nudge']}"), width, frame))
        elif row.get("now"):
            out.append(_frame_row("      " + c("33", f"now · {row['now']}"), width, frame))
        out.append(_divider_row(width, frame))
    # The last divider closes the last session rather than opening a new one: a frame that ends
    # on a rule above its bottom border reads as a session that got cut off.
    out.pop()
    out.append(_bottom_row(width, frame))
    return "\n".join(_clamp_rows(out, height, head=1, tail=1))


def render_board(rows: list, color: bool, width: int = 80, height: int | None = None,
                 now_ms: int | None = None, steps: int = BOARD_ROWS,
                 theme: dict | None = None, truecolor: bool | None = None) -> str:
    """The board: the framed one on a colour terminal, the plain one everywhere else.

    The same split `render` makes for a single list, and for the same reason — a pipe gets the
    stable text, a TTY gets the frame.
    """
    now_ms = now_ms or int(time.time() * 1000)
    if color and width >= 30:
        text = board_frame(rows, now_ms, width=width, height=height, steps=steps,
                           theme=theme, truecolor=truecolor)
    else:
        text = board_plain(rows, now_ms, width=width, color=color, steps=steps)
    return "\n".join(_clamp_widths(text.split("\n"), width))


def cmd_board(args) -> int:
    """`fbtodo board` — every live session in one frame, or a live board that keeps redrawing.

    One-shot by default, so the command composes with a pipe, a log or `json`; `--live` is the
    pane: the same frame redrawn in place, diffed row by row (`pane_repaint`), with the cursor
    hidden and always restored — the discipline every other long-running command here keeps.

    Which flags may be combined is the parser's business, not this function's: `main` refuses
    `--live --json` (a document and a pane are not the same request) and `--live` on any other
    command, the way it already refuses `--watch` outside `status`.
    """
    now_ms = int(time.time() * 1000)
    rows = board_sessions(args, now_ms)
    if args.json:
        json.dump({
            "version": VERSION,
            "probed_ms": now_ms,
            "window_ms": max(0, int(getattr(args, "board_live", 0) or 0)) * 60_000,
            "count": len(rows),
            "sessions": rows,
        }, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return EX_CODES["ok"]
    color = use_color()
    if not args.live:
        print(render_board(rows, color, width=width_of_default(), now_ms=now_ms,
                           steps=args.board_rows))
        return EX_CODES["ok"]
    hide = False

    def restore(*_a):
        if hide:
            sys.stderr.write("\x1b[?25h")
            sys.stderr.flush()
        raise SystemExit(130)

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, restore)
    last = None
    try:
        sys.stdout.write("\x1b[?25l")
        sys.stdout.flush()
        hide = True
        while True:
            size = _shutil.get_terminal_size(fallback=(80, 24))
            text = render_board(
                board_sessions(args), color, width=size.columns, height=size.lines,
                now_ms=int(time.time() * 1000), steps=args.board_rows,
            )
            frame = text.splitlines()
            out = pane_repaint(last, frame, size.lines)
            last = frame
            if out:
                sys.stdout.write(out)
                sys.stdout.flush()
            time.sleep(max(1.0, args.interval))
    except KeyboardInterrupt:
        restore()
        return EX_CODES["ok"]
    finally:
        if hide:
            sys.stderr.write("\x1b[?25h")
            sys.stderr.flush()


__all__ = [
    "BOARD_LIVE_MS", "BOARD_RUNNING_MS", "BOARD_MAX", "BOARD_SCAN_MAX", "BOARD_ROWS",
    "board_activity", "board_slug", "board_label", "board_row_cli", "board_cli_sessions",
    "board_desktop_sessions", "board_sessions", "board_counts", "board_head", "board_plain",
    "board_frame", "render_board", "cmd_board",
]