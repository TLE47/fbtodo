"""What the pane says, in plain text and in a frame.

`render` turns one state into the pane's lines, and knows nothing about how they get there:
the same call draws the strip, the framed pane and the golden fixtures. The theme (colours,
gradient, caps) resolves here too, from the environment and one small JSON file.
"""

from __future__ import annotations


import os
import re
import shutil as _shutil
import textwrap
import time
from .base import *  # noqa: F401,F403 (the package is one namespace)
from .alerts import *  # noqa: F401,F403 (the package is one namespace)
from .scan import *  # noqa: F401,F403 (the package is one namespace)
from .tasks import *  # noqa: F401,F403 (the package is one namespace)

def _window_anchor(cur_index: int | None, count: int) -> int:
    """Which step the elided window keeps on screen when the list is longer than the pane.

    The step being worked on — and, with none in progress because the list is finished,
    the LAST one. That puts the collapsed run at the TOP: the steps that fall out are the
    earlier, completed ones, and their `┊ N earlier steps completed` marker sits above the
    visible steps in list order. Anchoring a finished list at its first step collapsed the
    tail instead, which put a completed marker below the steps it stood for.
    """
    if cur_index is not None:
        return cur_index
    return max(0, count - 1)


# In plain words: "there is no list, and here is why" — one sentence, in the words the pane
# shows. Four situations produce no list at all: a session that has not written one yet, a
# session that changed (so the old list was dropped rather than left up as if it were
# current), a turn that began after the previous list was finished (the same misreading one
# turn earlier — 100% of work that is already over), and a remote session whose pane is open
# before the session is. Both renderers and `fbtodo status` ask this one function, so the four
# can never disagree about it.
def no_list_reason(state: dict) -> str:
    """Why there is no list to draw, in the pane's own words."""
    if state.get("backend") == "nas" and not state.get("instance_alive"):
        return "waiting for a NAS freebuff session (run `fb` there)"
    if state.get("cleared_turn"):
        return "last turn's list is done — waiting for this turn's list"
    if state.get("cleared"):
        return "new session — old list dropped, waiting for a new one"
    # The desktop store commits a row only at the turn's close, so a thread that is
    # visibly working has no list to read yet. Say which one it is: the store has not
    # committed (this) rather than the agent never called `write_todos` (below).
    if state.get("turn_running"):
        return "turn running · no list yet"
    return "no write_todos call yet in this session"


# In plain words: "is the list behind the work?" — usually this cannot be answered, and it is
# worth being precise about why. A step takes as long as it takes and the list is not supposed
# to change while one runs, so the transcript moving on is NORMAL, not evidence. What is
# evidence is a list with nothing left on it — every step ticked — inside a session that is
# still writing: then the agent is doing work the list does not describe at all. Only that case
# is flagged. The ones that cannot be proved are left to the step's own clock (`[STUCK?]`, past
# twice its estimate) and to `now`/`NUDGE` when a request is waiting on the list.
def list_behind(state: dict, now_ms: int) -> bool:
    """A finished list, in a session that has kept writing past it."""
    todos = state.get("todos") or []
    written = int(state.get("ts") or state.get("source_updated_ms") or 0)
    moved = int(state.get("store_mtime_ms") or 0)
    if not todos or written <= 0 or moved <= 0:
        return False
    if any(not t.get("completed") for t in todos):
        return False          # work left on the list: the step's own clock is the record
    if state.get("turn_ended"):
        return False          # waiting for you, not working past it
    return moved - written >= LIST_BEHIND_MS


# In plain words: the boundary guard's sentence (see `files_unlisted`). When a turn ends
# having edited files but having published no list since its last edit, the work landed
# unlisted — so the pane must not read as a clean finish. The count is the distinct files
# the scan tallied, which is a floor (the walk keeps only the newest few).
def unlisted_note(state: dict, short: bool = False) -> str:
    """`3 files changed, list not rewritten` — or "" when the list accounts for the work.

    `short` is the pane's form (`3 files unlisted`): a fixed-width strip drops the whole
    LIST field to make the chip fit, and the count is worth more than the wording there.
    The full sentence stays for the machine-readable paths — `snap`, `status`.
    """
    if not state.get("files_unlisted"):
        return ""
    files = (state.get("turn") or {}).get("files") or []
    n = len(files)
    plural = "" if n == 1 else "s"
    if short:
        return f"{n} file{plural} unlisted"
    return f"{n} file{plural} changed, list not rewritten"


# In plain words: a pane with no list should not just shrug at you. This lists the tools the
# session has actually called — "called so far: run_terminal_command 20, skill 3" — which turns
# "no list yet" from a mystery into a diagnosis: it is working, and it is not planning out loud.
def tools_note(tools: dict, limit: int = 3) -> str:
    """`called so far: run_terminal_command 20, skill 3 +2 more` — what the session has
    actually called. A list-less pane that names the tools is diagnosing; one that only
    says "no write_todos call yet" is shrugging."""
    items = sorted(
        ((n, c) for n, c in (tools or {}).items() if n != "write_todos" and c),
        key=lambda nc: (-nc[1], nc[0]),
    )
    if not items:
        return ""
    head = ", ".join(f"{n} {c}" for n, c in items[:limit])
    more = len(items) - limit
    return f"called so far: {head}" + (f" +{more} more" if more > 0 else "")


# In plain words: the second basket. `write_todos` is the only place in the transcript that
# holds a PLAN (measured: no other tool, and no other field, carries one), so a model that
# skips the call leaves this pane with nothing to draw. What the transcript does hold is
# every call the session did make — the file it edited, the command it ran — and that is a
# fact, not a plan. So when there is no list, the pane says what the session has actually
# DONE, newest first, and never dresses it up as a list of steps: no progress bar over it,
# no estimate, no ticks.
# In plain words: what a TURN can honestly say, as opposed to what a list would say. A plan
# supplies a denominator — seven steps — and nothing in these stores has one, so a bar, a
# percentage or an estimate would be a guess by construction, and this pane does not guess.
# What the store does have is a boundary and a numerator: the request that opened the turn
# (the journal logs it on its own record), how long ago that was, how many model iterations
# have run since, and what they have done. That is status, not progress, and the two are
# deliberately separate things.
def turn_note(state: dict, now_ms: int) -> str:
    """`turn 11m · 9 iterations · 3 files edited · 24 calls`, or "" with no turn to name."""
    turn = state.get("turn")
    if not isinstance(turn, dict) or not turn.get("start_ms"):
        return ""
    # Past the scan's cap the counts are lower bounds, and a `+` says so rather than leaving
    # a number that reads as exact.
    plus = "+" if turn.get("truncated") else ""
    bits = [f"turn {short_duration(max(0, now_ms - int(turn['start_ms']))) or '0s'}"]
    iters = int(turn.get("iterations") or 0)
    if iters:
        bits.append(f"{iters}{plus} iteration" + ("" if iters == 1 else "s"))
    files = turn.get("files") or []
    if files:
        bits.append(f"{len(files)}{plus} file" + ("" if len(files) == 1 else "s") + " edited")
    calls = sum(int(n or 0) for n in (turn.get("verbs") or {}).values())
    if calls:
        bits.append(f"{calls}{plus} call" + ("" if calls == 1 else "s"))
    note = " · ".join(bits)
    # The slot `[STUCK?]` takes when a step's clock has a list to sit on. A turn with no list
    # has no clock, so a transcript that has gone quiet is what is left to report — and it
    # says exactly that, because a long command writes no records either.
    quiet_ms = now_ms - int(state.get("store_mtime_ms") or 0)
    if not state.get("turn_ended") and quiet_ms >= QUIET_MS:
        note += "  ·  quiet " + (short_duration(quiet_ms) or "0m")
    return note


def observed_rows(state: dict, limit: int = ACTION_ROWS, now_ms: int | None = None) -> list:
    """`edited fbtodo · 2m ago` — the newest calls, for a session that wrote no list.

    Empty for a state with no calls recorded in it (a fixture, a brand-new session, or a
    build that did not carry them), so the renderers that draw this feed fall back to the
    line they drew before rather than drawing nothing.
    """
    acts = state.get("observed")
    if not isinstance(acts, list):
        return []
    out = []
    for act in acts[:limit]:
        if not isinstance(act, dict):
            continue
        what = " ".join(str(act.get("what") or "").split())
        verb = " ".join(str(act.get("verb") or "did").split()) or "did"
        line = f"{verb} {what}".strip()
        age = fmt_age(act.get("ts_ms"), now_ms)
        out.append(f"{line}  ·  {age}" if age != "?" else line)
    return out


# In plain words: every duration in the pane is measured by watching, never read from the
# agent. A step gets a clock only if fbtodo saw it unfinished at least once. So a list that
# turns up already finished — or one fbtodo attached to after the work had started — has no
# numbers at all, and the pane says which of those happened rather than showing a clock that
# looks broken.
#
# Every per-step number the pane shows — the row's clock, the list's pace, `EST REM`,
# `ETA` — is measured by WATCHING the list change from one poll to the next, and a step
# only gets a clock if it was seen *unfinished* at some point. So a list that arrives with
# every step already ticked — an agent that wrote it once, at the end — has no numbers to
# show, and says so rather than looking like a broken clock. (Measured 2026-09-26 on a NAS
# session: one `write_todos` call, five todos, all completed, so nothing was ever running
# to time. The build was identical to the Mac's, version 0.0.199; this was the agent, not
# the store.)
NO_TIMES_TICKED = "no per-step times · the list arrived with every step already ticked"


NO_TIMES_UNSEEN = "no per-step times · no step was ever seen running"


# In plain words: why are the numbers missing? There are two honest answers, and the pane
# names the one that applies; an empty answer means there is nothing to explain.
def no_times_note(state: dict) -> str:
    """Why the per-step numbers are missing, when they are — or None-ish when they are not."""
    todos = state.get("todos") or []
    if not todos:
        return ""
    times = state.get("task_times") or {}
    if any((rec or {}).get("started_ms") for rec in times.values()):
        return ""   # a step does carry a clock: there is nothing to explain
    if all(t.get("completed") for t in todos):
        return NO_TIMES_TICKED
    return NO_TIMES_UNSEEN


# In plain words: the renderer a script gets — plain text, one line per thing, no frame and
# no colour unless asked for. It is deliberately the boring one: other programs read it, so it
# stays stable while the framed pane is free to be pretty.
def _clamp_widths(lines: list, width: int) -> list:
    """Cut every row to the width the frame was asked for, styled or not.

    Each row above has its own budget, and that budget is the frame's real clipping. This is the
    guarantee BEHIND them: a row that slipped past its own budget — a very narrow pane, a prefix
    that is wider than the strip, an emoji counted as one cell somewhere — would wrap in the
    terminal and take the frame's shape with it, which is what a pane that has gone wrong looks
    like. Rows already inside the width come back untouched, so a recorded frame does not move.
    """
    return [line if _cell_width(line) <= width else _clip_cells(line, width) for line in lines]


def _clamp_rows(rows: list, height: int | None, head: int, tail: int) -> list:
    """A frame cut to `height` rows, keeping `head` at the top and `tail` at the end.

    A pane is a fixed grid: one row too many scrolls the top border — and the title with it —
    off the screen, which is what a pane that has gone wrong looks like. The ends are what a
    frame IS (which store and session, and the bar/state row), so the middle gives way; the
    count on the bar still says how much list is not being shown. Only a pane short enough
    for no box at all loses an end: then it gets the top `height` rows.
    """
    if not height or len(rows) <= height:
        return rows
    if height < 2:
        return rows[:height]
    head = min(max(0, head), height - 1)
    tail = min(max(0, tail), height - head)
    room = max(0, height - head - tail)
    return rows[:head] + rows[head:len(rows) - tail][:room] + rows[len(rows) - tail:]


def _wrap_segs(
    text: str,
    width: int,
    *,
    initial_indent: str = "",
    subsequent_indent: str = "",
    max_lines: int | None = None,
) -> list[str]:
    """`textwrap.wrap` that cannot raise on a too-narrow width.

    Python refuses to wrap when the indent plus the `…` placeholder do not fit the width,
    and the exact cutoff moves between versions — a pane can be asked for any width, so the
    budget is raised to the smallest legal one here. `render` clips every line to `width`
    afterwards, so a wider line cannot paint past the pane's edge.
    """
    indent = subsequent_indent if (max_lines or 1) > 1 else initial_indent
    floor = len(indent) + len(" …".lstrip()) + 1
    return textwrap.wrap(
        text,
        width=max(width, floor),
        initial_indent=initial_indent,
        subsequent_indent=subsequent_indent,
        max_lines=max_lines,
        placeholder=" …",
    )


def _render_plain(
    state: dict,
    color: bool,
    watching: int | None = None,
    width: int = 80,
    now_ms: int | None = None,
    idle_s: float | None = None,
    stale_after_s: float = 0.0,
    goal_lines: int = 3,
    height: int | None = None,
) -> str:
    c = paint(color)
    dim, bold, green, yellow, red = "2", "1", "32", "33", "31"
    now_ms = now_ms or int(time.time() * 1000)
    compact = width < 60
    backend = state.get("backend") or "?"
    session = str(state.get("session") or "")
    lines: list[str] = []

    if compact:
        # Keep the identity, backend, and session on one identity line in a narrow
        # strip.  A separate session row used to consume scarce height before any
        # step had appeared.
        identity = f"fbtodo  {backend}"
        if session:
            identity += "  " + _clip(session, max(1, width - len(identity) - 2))
        lines.append(c(bold, "fbtodo") + "  " + c(dim, identity[8:]))
    else:
        context = f"{backend} · {session}" if session else backend
        lines.append(
            c(bold, "Freebuff todos")
            + "  "
            + c(dim, _clip(context, max(1, width - len("Freebuff todos  "))))
        )

    if state.get("error"):
        lines += [c(red, seg) for seg in _wrap(str(state["error"]), width, 2)]
        return "\n".join(_clamp_rows(lines, height, head=1, tail=0))

    if watching:
        # What is being followed differs by backend: a local pid here, or the remote
        # session for the NAS store (where there is no local pid at all).
        followed = (
            "the NAS session"
            if backend == "nas"
            else f"freebuff pid {state.get('instance_pid')}"
        )
        if compact:
            # Short form on a narrow pane: the long one wraps mid-word at 22 columns,
            # which is the whole reason the layout is width-aware.
            short = "the NAS session" if backend == "nas" else f"pid {state.get('instance_pid')}"
            lines += [c(dim, seg) for seg in _wrap(f"watch {watching} → {short}", width, 2)]
        else:
            lines.append(c(dim, f"watcher: pid {watching} following {followed}"))
    if state.get("title"):
        lines += [c(dim, seg) for seg in _wrap(str(state["title"])[:90], width, 2)]

    # The goal heads the list: it is the request the list was written for, so the tasks
    # below it read as steps towards something. `now` appears only when a newer request
    # has arrived since — the case where the list is old but the session is not idle,
    # instead of leaving the pane to look frozen.
    goal = str(state.get("goal") or "")
    now_txt = str(state.get("now") or "")
    if compact:
        # A strip this narrow has ~8 rows for the steps themselves, so the heading gets at
        # most two of them. It is not clipped for length: AGENTS.md asks for a rewrite
        # short enough to fit whole (GOAL_MAX_CHARS), and a `Goal:` line that overruns is
        # reported by `status` rather than quietly losing its end here.
        goal_lines = min(goal_lines, 2)
    if goal_lines > 0:
        if not goal and state.get("todos"):
            # Said, not silently replaced by a quote of the request: the heading is the
            # agent's to write, and a missing one is a rule that was skipped. Wrapped like the
            # heading itself, at the width it was given: a floor here (16 columns, once) drew
            # rows wider than a 12-column pane, which is the frame wrapping as it is printed.
            segs = _wrap_segs(
                "— none stated", max(1, width),
                initial_indent="big goal · ",
                subsequent_indent="           ",
                max_lines=goal_lines,
            )
            lines += [c(dim, seg) for seg in segs]
        if goal:
            # `initial_indent` is counted INSIDE `width`, so the full pane width goes
            # here: subtracting the label as well wrapped headings 11 columns early.
            segs = _wrap_segs(
                goal, max(1, width),
                initial_indent="big goal · ",
                subsequent_indent="           ",
                max_lines=goal_lines,
            )
            # A goal that is non-empty but wraps to nothing — a single space, which is what
            # the filter leaves of a lone tab — must draw no heading at all, not crash on
            # `segs[0]`.
            if segs:
                lines.append(c(bold, segs[0]))
                lines += [c(dim, seg) for seg in segs[1:]]
        if now_txt:
            segs = _wrap_segs(
                now_txt, max(1, width),
                initial_indent="now · ",
                subsequent_indent="      ",
                max_lines=2,
            )
            lines += [c(yellow, seg) for seg in segs]
        nudge = str(state.get("nudge") or "")
        if nudge and nudge != now_txt:
            # "continue" is not a new task, but it does ask for a fresh list: AGENTS.md
            # says re-write it before carrying on. Shown only while the list has NOT been
            # rewritten since (a newer write_todos clears `nudge`), so it is the pane
            # reporting a skipped rule rather than a permanent nag.
            segs = _wrap_segs(
                "rewrite the list, then continue", max(16, width),
                initial_indent=f"nudge · {nudge} — ",
                subsequent_indent="        ",
                max_lines=2,
            )
            lines += [c(yellow, seg) for seg in segs]

    # In plain words: a session with no list is the one case where the pane has nothing to
    # draw, so it says why in words instead — and the words differ by cause rather than being
    # one catch-all shrug.
    groups = list_groups(state)
    todos = state.get("todos") or []
    if not any(g["todos"] for g in groups):
        if state.get("first_prompt") and not goal:
            lines += [c(dim, seg) for seg in _wrap(str(state["first_prompt"]), width, 2)]
        why = no_list_reason(state)
        lines += [c(dim, seg) for seg in _wrap(why, width, 2)]
        # The turn first — a boundary and a numerator, never a denominator — and the second
        # basket after it: what the session has done. Both are absent from a state that
        # carries neither, so this snapshot is unchanged there.
        turn_line = turn_note(state, now_ms)
        if turn_line:
            lines += [c(dim, s) for s in _wrap("  " + turn_line, width, 1)]
        for seg in observed_rows(state, 1 if turn_line else 2, now_ms):
            lines += [c(dim, s) for s in _wrap("  " + seg, width, 1)]
        lines.append(c(dim, f"  live {time.strftime('%H:%M:%S', time.localtime(now_ms / 1000))}"))
        # The no-list strip: the last row is the clock, so one of these is always kept.
        return "\n".join(_clamp_rows(lines, height, head=1, tail=1))
    # No note here, deliberately: a plain snapshot is parsed by scripts, and the suite
    # holds its line count still whatever the timings are (fbtodo-selfcheck: "a snapshot's
    # line count must not move because an estimate exists"). Both notes below are the
    # pane's to say — it is the thing a person reads, and its frame is height-managed.

    times = state.get("task_times") or {}
    history = state.get("task_history") or {}
    shapes = state.get("task_shapes") or {}
    calls_mem = state.get("task_calls") or {}
    blocks: list[list[str]] = []
    cur_index = current_index(todos)
    pace_ms = step_pace_ms(times, todos, now_ms, history)
    # The same grouping the frame draws: one list for every state but a desktop store asked
    # to stack its live threads, and a heading row riding on each thread's first step. The
    # strip is the machine-readable path, but it is also what a narrow pane falls back to,
    # so it says the same thing in its own ink rather than dropping the other threads.
    multi = len(groups) > 1
    prefer = 0
    plan: list[tuple] = []
    for group in groups:
        heading = (
            _thread_heading(
                group, width, c,
                dim if not group["current"] else yellow,
                yellow if group["running"] else dim,
            ) if multi else None
        )
        for i, t in enumerate(group["todos"]):
            if group["current"] and i == cur_index:
                prefer = len(plan)
            plan.append((group, i, t, heading if i == 0 else None))
    for group, i, t, heading in plan:
        task = str(t.get("task", ""))
        done_flag = bool(t.get("completed"))
        active = i == cur_index and not done_flag
        rec = times.get(task) or {}
        # '· 3m…' while the step being worked on counts up, '· 2m' once it is done and
        # frozen, nothing at all for a task that was never seen running (a zero there
        # would claim it took no time). Re-derived from the recorded start/stop against
        # `now_ms`, so the number moves on the pane's own tick instead of only when the
        # watcher writes — the watcher's poll is 2.5s at best, and 60s when idle.
        elapsed = live_elapsed(rec, now_ms)
        suffix = ""
        if elapsed is not None:
            suffix = " · " + (short_duration(elapsed, seconds=True) or "0s")
            if not rec.get("done_ms"):
                suffix += "…"
        # Keep the duration attached when it fits; otherwise give it its own quiet
        # continuation line.  Reserving suffix width on every wrapped line made an
        # ordinary task unnecessarily short and was the source of the old mid-word
        # looking breaks.
        segs = _wrap(task, width, 6)
        if suffix:
            if len(segs[-1]) + len(suffix) <= width - 6:
                segs[-1] += suffix
            else:
                segs.append(suffix)
        # The projection rides the SAME line, never one of its own: a piped `snap` is
        # parsed by scripts, so its line count must not move because an estimate exists.
        # When the token does not fit, it is simply dropped — the duration above, when
        # there is one, has already had its say.
        if not done_flag:
            est_ms = estimate_for(times, task, pace_ms, shapes, calls_mem)
            spread = estimate_spread_ms(
                times, task, shapes, step_shape_bucket(times, task)
            ) or pace_spread_ms(times, todos, now_ms, history)
            # The range is offered FIRST and the plain number second: a row with room says
            # `~2m (10s–20m)`, and a row without it keeps the `~2m` it would have had, so a
            # narrow pane never trades its estimate away for detail it cannot show.
            for cand in (fmt_estimate_spread(est_ms, spread), fmt_estimate(est_ms)):
                token = (" · " + cand) if elapsed is None else (" / " + cand)
                if len(segs[-1]) + len(token) <= width - 6:
                    segs[-1] += token
                    break
        block = []
        for j, seg in enumerate(segs):
            if j == 0:
                mark = c(green, "[x]") if done_flag else c(yellow if active else dim, "[ ]")
                block.append(f"  {mark} " + (c(dim, seg) if done_flag else seg))
            else:
                block.append(" " * 6 + (c(dim, seg) if done_flag else seg))
        if height and len(block) > TASK_MAX_LINES:
            # In the pane only: one verbose step must not become the whole pane, and the
            # full text is always in `fbtodo json` (and in any piped `snap`).
            block = block[:TASK_MAX_LINES]
            block[-1] += c(dim, " …")
        if heading:
            block = [heading] + block
        blocks.append(block)

    # The footer is built first so the list can be given exactly the room that is left
    # over. A pane has a fixed height: without this, a six-step list pushed the goal and
    # the progress bar clean off the top of a 16-row strip.
    # counted from the list being drawn, as in the framed pane: the state's own tally is
    # the same number while this build wrote it, and a stale one must never let the bar
    # contradict the steps printed right above it
    total = len(todos)
    done = sum(1 for t in todos if t.get("completed"))
    # the bar shares its line with "  [" + "]" + " n/m done"; size it to fit
    label = f"{done}/{total} done" if width >= 40 else f"{done}/{total}"
    bar_w = max(1, min(20, width - (len(label) + 6)))
    filled = int(bar_w * done / total) if total else 0
    bar = c(green, "#" * filled) + c(dim, "-" * (bar_w - filled))
    meta = []
    current = todos[cur_index] if cur_index is not None else None
    cur_rec = times.get(str(current.get("task", ""))) if current else None
    # the same liveness rule as the framed pane: a closed clock is a measurement, and a
    # finished turn is idle even if the file still says the clock is counting
    cur_elapsed = live_elapsed(cur_rec or {}, now_ms) if step_is_running(state, cur_rec) else None
    cur_est_ms = estimate_for(
        times, str(current.get("task", "")) if current else "", pace_ms, shapes, calls_mem
    )
    if cur_elapsed is not None:
        working = "working " + (short_duration(cur_elapsed, seconds=True) or "0s")
        if cur_elapsed > cur_est_ms:
            # the step has outrun its estimate: name the overrun where the clock is
            working += " (+" + (
                short_duration(cur_elapsed - cur_est_ms, seconds=True) or "0s"
            ) + ")"
        meta.append(working)
    meta.append(fmt_age(state.get("source_updated_ms")))
    if state.get("list_version"):
        meta.append(f"list #{state['list_version']}")
    if total and done == total:
        gap = unlisted_note(state)
        # The boundary guard reads the same in the machine-readable path: a finished list
        # that does not account for the turn's edits is not a finish.
        meta.append(f"steps still open — {gap}" if gap else "all steps done")
    clock = time.strftime("%H:%M:%S", time.localtime(now_ms / 1000))
    # The footer is intentionally tight: in a fixed-height strip, a blank separator
    # costs a visible step.  The bar and status remain the visual anchor, while the
    # metadata and clock are kept on separate rows only when the pane is narrow.
    footer = []
    progress = f"  [{bar}] {c(bold, label)}"
    # Same two projections as the framed pane, measured on the plain text before any styling
    # is added, and each one taken only if the row still has room: `EST REM`/`ETA` keep the
    # all-or-nothing rule they had, and the overall time to the goal is appended after them
    # so a narrow strip gains the total rather than losing the remaining count.
    extra = ""
    rem_ms = remaining_estimate_ms(times, todos, now_ms, pace_ms, shapes, calls_mem)
    if rem_ms > 0:
        suffix = " | EST REM: " + (short_duration(rem_ms, seconds=True) or "0s")
        suffix += " | ETA " + fmt_eta(rem_ms, now_ms)
        if _cell_width(progress) + _cell_width(extra) + len(suffix) <= width:
            extra += suffix
    tot_ms = total_estimate_ms(times, todos, now_ms, pace_ms, shapes, calls_mem)
    if tot_ms is not None:
        tot_suffix = " | TOT " + (short_duration(tot_ms) or "0s")
        if _cell_width(progress) + _cell_width(extra) + len(tot_suffix) <= width:
            extra += tot_suffix
    if extra:
        progress = progress + c(dim, extra)
    footer.append(progress)
    meta_text = " · ".join(x for x in meta if x)
    if compact:
        footer.append(c(dim, "  " + _clip(meta_text, max(1, width - 2))))
        footer.append(c(dim, f"  live {clock}"))
    else:
        live = f"live {clock}"
        room = max(1, width - len(live) - 5)
        left = _clip(meta_text, room)
        footer.append(f"  {c(dim, left)}   {c(dim, live)}")
    # The patch rows — what the last patch pass did and when the phone was last told
    # something — drawn only when the state actually carries those two facts. A state from
    # a build that did not read them (or a fixture) renders exactly as it always did, which
    # is what keeps this machine-readable path's line count stable.
    row = patch_row(state, width)
    if row:
        footer.append("  " + patch_row_text(row))
    # ...and the refit progress, when the log has enough closed steps for it to mean anything.
    # The plain path is the machine-readable one, so this is the same text the pane draws.
    refit = refit_row(state, width)
    if refit:
        footer.append("  " + patch_row_text(refit))
    if idle_s and idle_s >= 60:
        txt = f"  idle {short_duration(idle_s * 1000)}"
        if stale_after_s:
            txt += f" · closes in {short_duration(max(0.0, stale_after_s - idle_s) * 1000)}"
        footer.append(c(dim, txt[: max(16, width)]))

    avail = None if not height else max(1, height - len(lines) - len(footer))
    # With no step in progress the list is finished, and the window anchors at its END: the
    # run that gets collapsed is then the EARLIER, completed work, so its marker sits at the
    # top of the list body, above the visible steps, in list order. Anchored at the top it
    # collapsed the tail instead and put a completed marker under the steps it belonged to.
    start, end = _fit_blocks(blocks, avail, _window_anchor(prefer if multi else cur_index, len(blocks)))
    if start:
        lines.append(c(dim, f"  … {start} earlier step{'s' if start > 1 else ''}"))
    for block in blocks[start:end]:
        lines += block
    if end < len(blocks):
        rest = len(blocks) - end
        lines.append(c(dim, f"  … {rest} more step{'s' if rest > 1 else ''}"))
    lines += footer
    # This path is normally given no height at all — `snap`, `json`, a pipe — so its line count
    # only follows a pane that asked for a strip with colour turned off or a very narrow width.
    lines = _clamp_rows(lines, height, head=1, tail=len(footer))
    return "\n".join(lines)


# ============================================================ rich pane layout
def _frame_row(text: str, width: int, frame) -> str:
    """One bordered content row, padded to the pane's inner width.

    `frame` is the border's role-bound painter (see `_styles`), not the raw colour
    function: the edges, the rules and the connectors are one ink, and `text` carries its
    own styling. One cell of padding inside each edge.
    """
    inner = max(0, width - 4)
    return frame("│") + " " + _pad_cells(_clip_cells(text, inner), inner) + " " + frame("│")


def _divider_row(width: int, frame) -> str:
    return frame("├" + "─" * max(0, width - 2) + "┤")


def _bottom_row(width: int, frame) -> str:
    return frame("╰" + "─" * max(0, width - 2) + "╯")


_SESSION_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})")


def _session_label(state: dict) -> str:
    """A short name for the session a pane is showing.

    Session ids are ISO stamps (`2026-09-24T07-05-26.585Z`) and the top border has
    room for a tag, not for that: month-day and clock, with the backend for context.
    """
    backend = str(state.get("backend") or "")
    session = str(state.get("session") or "")
    m = _SESSION_RE.match(session)
    stamp = f"{m.group(2)}-{m.group(3)} {m.group(4)}:{m.group(5)}" if m else session
    if backend and stamp:
        return f"{backend} · {stamp}"
    return stamp or backend or "no session"


def _top_border(width: int, frame, title: str, right: str, badge) -> str:
    """A rounded top border: the title on an accent badge, faint metadata right.

    `frame` is the border's painter and `badge` the title's chip — both role-bound, so
    this stays layout. The chip is reverse video (`7;<accent>`), which needs no
    background selector and so survives a terminal that ignores `dim`. The right slot
    gets whatever columns the title leaves, so a long session label shrinks rather than
    pushing the corner off the pane — and drops out entirely when there is not room for
    a readable tag.
    """
    label = f" {title} "
    left = f"╭── {label} "
    room = width - _cell_width(left) - 2  # corner cell, plus the space before it
    if right and room >= 12:
        tail = f" {_clip_cells(right, room - 4)} ──╮"
    else:
        tail = "╮"
    fill = max(0, width - _cell_width(left) - _cell_width(tail))
    return frame("╭── ") + badge(label) + frame(" ") + frame("─" * fill) + frame(tail)


# The frame's palette. `accent` may be a `#rrggbb` colour or a raw SGR code (`1;36`);
# the gradient stops are colours. Precedence is the tool's usual one: FBTODO_ACCENT /
# FBTODO_GRADIENT_START / FBTODO_GRADIENT_END beat a project `.fbtodo-theme.json`,
# which beats `~/.config/fbtodo/theme.json`, which beats these defaults.
# The four inks the pane paints in, and nothing else but the two STATE colours kept in the
# code (`warn` 33 for a step past its estimate or a patch that did not come out clean,
# `error` 31 for a failure): a palette is not a licence to hide a failure in grey.
#
#   accent   the interactive ink — the title chip, the active row's marker and estimate
#            badge, the bar's first stop
#   active   the one thing being worked on: the active row and the goal it belongs to
#   success  done: a finished step's tick, a patch that came out clean, the bar's last stop
#   muted    everything secondary — a step's description, durations, the age, the strip;
#            with `faint` for metadata that is not even claiming to be read: the frame's
#            edges, the section labels, the watcher pid, the model
THEME_DEFAULTS = {
    "accent": "1;36",
    "active": "#e6edf3",
    "success": "#2ea043",
    "gradient_start": "#22d3ee",
    # the bar ramps accent -> success, so the two ends of the one piece of data on the
    # frame are the same two hues as everything else on it
    "gradient_end": "#2ea043",
    "muted": "#8b949e",   # secondary text: durations, the goal line, the age
    # The progress bar's empty cells. Mid grey on purpose: dark enough to read as empty
    # next to the gradient fill, light enough that the track's full length is visible on
    # a dark pane — a near-background grey is the same as no track at all.
    "track": "#6b6b73",
    # The frame's own ink: the drawn edges, the collapsed-run rule and the right-hand
    # metadata. 242 in xterm-256, and a real colour rather than the `dim` attribute, which
    # a themed terminal is free to render onto the background.
    "faint": "#6b6b73",
}


THEME_KEYS = tuple(THEME_DEFAULTS)


THEME_FILE_LOCAL = ".fbtodo-theme.json"


THEME_FILE_GLOBAL = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config"),
    "fbtodo", "theme.json",
)


def _hex_rgb(value) -> tuple | None:
    """`#22d3ee` / `22d3ee` / `#2ee` as an (r, g, b) triple, or None if it is not one."""
    m = re.fullmatch(r"#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", str(value or "").strip())
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_256(rgb) -> str:
    """The nearest xterm-256 colour: the 6x6x6 cube, or the grey ramp for neutrals."""
    r, g, b = rgb
    if max(rgb) - min(rgb) < 12:
        grey = (r + g + b) // 3
        if grey < 8:
            return "16"
        if grey > 238:
            return "231"
        return str(232 + min(23, (grey - 8) // 10))
    cube = lambda v: 0 if v < 48 else 1 if v < 115 else min(5, (v - 35) // 40)  # noqa: E731
    return str(16 + 36 * cube(r) + 6 * cube(g) + cube(b))


def _color_sgr(value, truecolor: bool) -> str:
    """A theme colour as SGR parameters: hex is drawn in 24-bit where it can be, and in
    the nearest 256-colour entry where it cannot. A plain SGR code is used verbatim."""
    rgb = _hex_rgb(value)
    if rgb is None:
        return str(value or "").strip() or "0"
    if truecolor:
        return f"38;2;{rgb[0]};{rgb[1]};{rgb[2]}"
    return f"38;5;{_rgb_256(rgb)}"


def _theme_stamp() -> tuple:
    """What a resolved theme depends on: the env overrides, and both files' mtimes."""
    return (
        *(os.environ.get("FBTODO_" + k.upper(), "") for k in THEME_KEYS),
        *(
            (p, os.path.getmtime(p) if os.path.exists(p) else 0.0)
            for p in (THEME_FILE_GLOBAL, THEME_FILE_LOCAL)
        ),
    )


_THEME_CACHE: tuple | None = None


# A theme value ends up INSIDE an escape sequence, which makes it code the terminal
# runs: H2 measured `FBTODO_ACCENT='1;36<BEL><ESC>]52;c;…<BEL>'` painting a real escape
# into the frame, because a value without a `#` is passed through as an SGR list. Two
# forms are accepted — a raw SGR parameter list and a `#rrggbb` colour — and nothing
# else, so the value can only ever be parameters.
THEME_VALUE_RE = re.compile(r"^(?:#[0-9a-fA-F]{6}|\d{1,3}(?:;\d{1,3})*)$")


# [(source, value)] the last `read_theme()` refused. `status` prints it: a theme that
# silently does nothing is exactly how a bad value got this far unnoticed.
# Filled IN PLACE rather than rebound: `status` reads this list from the front door, and what
# `import *` handed it is the same object. Rebinding would leave that reader on the old list —
# which is how a refused value came back as "no problems" for a reader in another module.
THEME_PROBLEMS: list = []


def _theme_value(value) -> str | None:
    """The value when it is a colour or an SGR list, and None when it is neither."""
    raw = str(value or "").strip()
    return raw if THEME_VALUE_RE.match(raw) else None


def read_theme() -> dict:
    """The frame's palette, resolved once per env/file state — a pane repaints often.

    Each value is validated as it is read (`_theme_value`): an unusable one leaves the
    role at its default rather than at a value that would carry whatever it contained
    into the frame, and is remembered for `status`.
    """
    global _THEME_CACHE
    stamp = _theme_stamp()
    if _THEME_CACHE and _THEME_CACHE[0] == stamp:
        return _THEME_CACHE[1]
    theme = dict(THEME_DEFAULTS)
    problems: list = []
    for source, path in (("theme.json", THEME_FILE_GLOBAL),
                         ("project theme", THEME_FILE_LOCAL)):
        data = read_json(path, None)
        if isinstance(data, dict):
            for k, v in data.items():
                if k not in THEME_KEYS or not str(v).strip():
                    continue
                good = _theme_value(v)
                if good is None:
                    problems.append((f"{source}: {k}", str(v)[:40]))
                else:
                    theme[k] = good
    for key in THEME_KEYS:
        value = (os.environ.get("FBTODO_" + key.upper()) or "").strip()
        if not value:
            continue
        good = _theme_value(value)
        if good is None:
            problems.append((f"FBTODO_{key.upper()}", value[:40]))
        else:
            theme[key] = good
    THEME_PROBLEMS[:] = problems  # in place: see the note on the list
    _THEME_CACHE = (stamp, theme, THEME_PROBLEMS)
    return theme


def _theme_gradient(theme) -> tuple:
    """The two gradient stops as RGB, falling back to the default stop by stop."""
    fallback = [_hex_rgb(THEME_DEFAULTS[k]) for k in ("gradient_start", "gradient_end")]
    return tuple(
        _hex_rgb(theme.get(k)) or fallback[i]
        for i, k in enumerate(("gradient_start", "gradient_end"))
    )


def _supports_truecolor() -> bool:
    """24-bit colour, or a terminal that advertises it. `FBTODO_TRUECOLOR` wins."""
    override = os.environ.get("FBTODO_TRUECOLOR", "").strip().lower()
    if override:
        return override not in ("0", "off", "false", "no")
    if (os.environ.get("COLORTERM") or "").lower() in ("truecolor", "24bit", "24-bit"):
        return True
    term = (os.environ.get("TERM") or "").lower()
    return "truecolor" in term or "direct" in term or "24bit" in term


def _styles(theme: dict, truecolor: bool) -> dict:
    """The pane's style declarations: role -> SGR parameters, resolved once per paint.

    This is the only place a colour is decided. The drawing code asks for a ROLE, so
    changing the palette — or how a role is expressed — never reaches into layout.

    * `accent`  the title chip, the active row's marker and estimate badge
    * `active`  the ink of the one thing being worked on: the active row, the goal
    * `strong`  that ink with weight: the first line of the active row
    * `success` a finished step's tick, a clean patch, the bar's last stop
    * `faint`   the frame's own ink: edges, rules, collapsed-run connectors, metadata
    * `muted`   secondary text: durations, the goal's label, the status strip
    * `done`    the description of a finished step (muted, plus the dim attribute)
    * `tick`    a finished step's marker: the success hue, dimmed so the row recedes
    * `track`   the progress bar's empty cells
    * `badge`   the accent as a chip: reverse video, so no background selector is used
    """
    sgr = lambda key: _color_sgr(theme.get(key), truecolor)  # noqa: E731
    accent, muted = sgr("accent"), sgr("muted")
    return {
        "accent": accent,
        "active": sgr("active"),
        "strong": f"1;{sgr('active')}",
        "success": sgr("success"),
        "faint": sgr("faint"),
        "muted": muted,
        "done": f"2;{muted}",
        "tick": f"2;{sgr('success')}",
        "track": sgr("track"),
        "badge": f"7;{accent}",
    }


# The progress bar's boundary cell: the nearest of these to the fraction left over, so a
# third of a list draws as a third of a bar instead of rounding away to a whole cell.
BAR_EIGHTHS = " ▏▎▍▌▋▊▉█"


# The status strip's spinner while a step is running: one frame per second, advanced by the
# pane's own repaint clock rather than a timer of its own.
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _progress_bar(
    done: int, total: int, bar_w: int, c, truecolor: bool, theme: dict | None = None
) -> str:
    """`████▊░░░` — `█` filled, the nearest eighth at the boundary, `░` on the track.

    The count beside the bar carries the exact number, and the eighth block puts the
    fractional cell where it belongs; the empty cells are drawn in an explicit `track`
    colour rather than the `dim` attribute, because dim is the terminal's own idea of dim
    and where it lands on the background the bar reads as ending at the fill, with no
    length to judge against.

    Every cell is the GLYPH painted with a foreground colour (`38;2;…` where the terminal
    takes 24-bit colour, `38;5;…` where it does not) — never a background-filled block.
    A background block would depend on the terminal's own background scheme to read at all,
    which is the same trap the track fell into, and it would stop being a character.
    """
    theme = read_theme() if theme is None else theme
    start, end = _theme_gradient(theme)
    track = _color_sgr(theme.get("track"), truecolor)
    ratio = (done / total) if total else 0.0
    ratio = min(1.0, max(0.0, ratio))
    cells = ratio * bar_w
    full = min(bar_w, int(cells))
    frac = cells - full  # the one cell the eighths are for
    out = []
    for i in range(bar_w):
        if i > full or (i == full and frac <= 0):
            out.append(f"\x1b[{track}m░\x1b[0m")
            continue
        # The ramp spans the FILLED run, not the whole bar: sampled across the bar the
        # gradient only reached its emerald end at 100%, so a half-done list showed a
        # flat cyan-teal run. Here the fill always reads cyan at its left edge and emerald
        # at its leading edge, and the count beside it says how far along it is.
        t = min(1.0, (i + 0.5) / max(1, full))
        rgb = [int(round(start[k] + (end[k] - start[k]) * t)) for k in range(3)]
        # the same ramp in both: a flat accent made the 256-colour terminal's bar one solid
        # cyan while the 24-bit one ran cyan to green, which is not the same bar. The 256
        # form needs its `38;5;` prefix — a bare index is a BACKGROUND colour in SGR, so
        # `\x1b[45m█` paints a magenta block instead of a cyan character.
        colour = (
            f"38;2;{rgb[0]};{rgb[1]};{rgb[2]}" if truecolor
            else "38;5;" + _rgb_256(rgb)
        )
        glyph = "█" if i < full else BAR_EIGHTHS[min(8, max(1, int(round(frac * 8))))]
        out.append(f"\x1b[{colour}m{glyph}\x1b[0m")
    return "".join(out)


def _thread_heading(group: dict, width: int, c, ink, count_ink) -> str:
    """One row saying whose steps follow: the thread's title, and how far that list got.

    Drawn only when a pane stacks several threads (`list_groups`): the heading carries the
    same five cells of indent a step row does, so a thread's steps read as a list under its
    own name, and its `done/total` sits in the column the steps' clocks are right-aligned in.

    Two markers, in the same voice the step rows use: `\u2794` for a thread that wrote a list
    in the last few minutes (it is working), `\u25cb` for one that is merely still open. The ink
    says the rest — the followed thread's heading is the bright one, so "which of these is my
    pane about" is answerable at a glance — and the count is bright on a running thread for
    the same reason the active step's number is.
    """
    todos = group.get("todos") or []
    done = sum(1 for t in todos if t.get("completed"))
    count = f"{done}/{len(todos)}"
    marker = "\u2794" if group.get("running") else "\u25cb"
    room = max(4, width - 5 - _cell_width(count) - 2)
    line = "  " + c(ink, marker) + "  " + c(ink, _clip_cells(str(group.get("title") or ""), room))
    return line + " " * max(1, width - _cell_width(line) - _cell_width(count)) + c(count_ink, count)


def _elision_note(count: int, flags: list, where: str) -> str:
    """The marker for steps the window left out, worded from THOSE steps.

    `│ 3 earlier steps completed` / `│ 4 more steps pending`, the rule drawn in the same
    column as the step markers so a collapsed run reads as a rule down the list. The word
    used to come from
    the side of the window the marker sits on, which contradicted the bar above it: a
    finished list whose last steps were elided read `2 more steps pending` at 100% (4/4),
    and `fbtodo status` said `All done` in the same breath. A run of hidden steps that is
    all done is past work whatever side it sits on; `more … pending` is kept for a run
    that still has something left in it, which is also when `done < total`.
    """
    plural = "s" if count > 1 else ""
    if count and all(flags):
        return f"  │ {count} earlier step{plural} completed"
    return f"  │ {count} {where} step{plural} pending"


# In plain words: the framed pane. This is the drawing code, and the longest part of the file,
# because a terminal is not a canvas: every row is measured in screen columns, cut to a frame
# whose height depends on the pane it is in, and the colours are roles resolved from a theme
# rather than colours sprinkled through the drawing. Read it as "decide what fits, then draw".
def _render_rich(
    state: dict,
    color: bool,
    watching: int | None,
    width: int,
    now_ms: int,
    idle_s: float | None,
    stale_after_s: float,
    goal_lines: int,
    height: int | None,
    theme: dict | None = None,
    truecolor: bool | None = None,
    reloaded: str | None = None,
) -> str:
    """The framed, high-density pane shown on a colour terminal.

    The plain renderer above stays the machine-readable path (pipes, `snap`,
    self-checks): it has no borders, so line counts and first-column markers stay
    stable for scripts.  A live pane is a TTY with colour, and gets this layout.
    """
    c = paint(color)
    # Only the two STATE colours are literals any more; everything else the drawing asks
    # for is a role from `_styles`. `warn` is a step past its estimate, a nudge or a patch
    # that did not come out clean; `red` is a failure or an overrun.
    yellow, red = "33", "31"
    theme = read_theme() if theme is None else theme
    truecolor = _supports_truecolor() if truecolor is None else truecolor
    # Every colour decision lives in `_styles`; this function only asks for roles, so the
    # drawing below reads the same whatever the palette resolves to.
    st = _styles(theme, truecolor)
    accent, muted = st["accent"], st["muted"]
    active_ink, success = st["active"], st["success"]
    done_style, tick_style = st["done"], st["tick"]
    # A finished step's description is the muted grey plus the dim attribute: dim is what
    # `muted` means on a terminal with no theme of its own — and its tick is the success
    # hue dimmed the same way, so a row that is over recedes instead of shouting.
    # The one row that is not over is the only one painted in `active` ink; the accent is
    # spent on what the eye should find first (the title chip, the active marker, the
    # estimate badge, the bar), never on prose.

    def frame(text: str) -> str:
        """The frame's own ink: edges, rules and the collapsed-run connectors."""
        return c(st["faint"], text)

    def badge(text: str) -> str:
        """A chip: the accent as reverse video, so no background selector is used."""
        return c(st["badge"], text)

    inner = max(10, width - 4)
    # The patches' own two facts, each on a row of its own when the pane is too narrow to
    # share one: the outcome the owner would otherwise ssh in to read, and when the phone
    # was last told something. They cost rows of the fixed height, which the budgets below
    # are told about through `patch_rows`.
    patch_row_here = patch_row(state, inner)
    refit_row_here = refit_row(state, inner)

    def patch_row_styled(pairs: list) -> str:
        """A row as drawn: labels in the frame's grey, values in their own severity."""
        # the label is metadata (faint), the value carries its own severity — and a clean
        # patch reads as `success`, which is what the green is FOR
        ink = {"ok": success, "warn": yellow, "bad": red}
        return "  " + "   ".join(
            c(st["faint"], f"{label}  ") + c(ink.get(sev, muted), text)
            for label, text, sev in pairs
        )

    # With a watcher, name it; without one the pane still has to say which session
    # it is showing, so the top border carries the (short) session title instead.
    # Which model this session runs, on the border: the estimate memory is per model (see
    # task_history_from_log), so a pane that quotes a pace should name the model that
    # taught it. AFTER the watcher pid on purpose — the border clips the right end, and
    # the pid is what the pane has always said it is showing (asserted in two checks);
    # the model is the newcomer, so on a narrow pane the model is what goes.
    model = _clip_cells((state.get("model") or "").split("/")[-1], 14) if state.get("model") else ""
    who = f"watcher: pid {watching}" if watching else _session_label(state)
    right = f"{who} · {model}" if model else who
    # A pane that has just replaced itself says which build it is now (`reloaded`, from
    # `cmd_pane`) on its own title chip for a few seconds. The chip is the one slot on the
    # frame that belongs to the PROCESS rather than to the list, so the note spends no data
    # row and moves nothing: the ruler beside it just gives the longer label its columns,
    # and the chip is the pane's own name again when the note expires. Clipped to a budget
    # that keeps the right-hand metadata its room — the note is transient, the watcher's pid
    # is not — so a narrow pane loses the note's tail, not the whole right slot, for five
    # seconds.
    title = _clip_cells(reloaded, max(8, width - 22)) if reloaded else "FREEBUFF TODOS"
    rows = [_top_border(width, frame, title, right, badge)]

    if state.get("error"):
        rows.append(_divider_row(width, frame))
        for seg in _wrap(str(state["error"]), inner, 0):
            rows.append(_frame_row(c(red, seg), width, frame))
        rows.append(_bottom_row(width, frame))
        return "\n".join(_clamp_rows(rows, height, head=1, tail=1))

    # The heading is the agent's own `Goal:` line, in muted grey so the steps below it
    # are what the eye lands on. A session that wrote none gets no heading ROW at all —
    # the strip is fixed-height, and `— none stated` spent one of its rows saying
    # nothing; `fbtodo status` reports the missing line instead. The emoji is two cells
    # but one character, which is why the wrap budget is a column short.
    goal = str(state.get("goal") or "")
    now_txt = str(state.get("now") or "")
    nudge = str(state.get("nudge") or "")
    head: list[str] = []
    max_goal_lines = min(goal_lines, 2) if width < 60 else goal_lines
    goal_prefix = "🎯 Goal: "
    if max_goal_lines > 0 and goal:
        segs = _wrap_segs(
            goal, max(12, inner - 1),
            initial_indent=goal_prefix,
            subsequent_indent=" " * _cell_width(goal_prefix),
            max_lines=max_goal_lines,
        )
        # The icon and its label carry the identity in the muted grey; the goal itself is
        # the frame's heading, so it is drawn in the readable ink rather than being dimmed
        # along with the label — and without weight, which belongs to the active row.
        pad = " " * _cell_width(goal_prefix)
        # The label stays the muted grey; the goal itself is `active` ink WITHOUT the bold
        # attribute — it is the list's heading, not its subject, and weight spent here was
        # weight the active row could not have.
        if segs:  # a whitespace-only goal wraps to nothing: draw no heading, do not crash
            head.append(c(muted, goal_prefix) + c(active_ink, segs[0][len(goal_prefix):]))
            head += [c(active_ink, pad + seg[len(pad):]) for seg in segs[1:]]
    if now_txt:
        segs = _wrap_segs(
            now_txt, inner, initial_indent="NOW · ", subsequent_indent="      ",
            max_lines=1,
        )
        # The macro goal and the micro step are separated by INK, not by two bright colours
        # competing above the list: the goal's label is the muted grey, `NOW` is the accent
        # (the one thing on this row that is a pointer), and both carry `active` prose. The
        # yellow that used to be here is now only ever a warning.
        if segs:  # as above: an `now` of one space wraps to nothing
            body = segs[0][len("NOW · "):]
            indent = segs[0][:len("NOW · ")] if segs[0].startswith("NOW · ") else "NOW · "
            head += [c(accent, indent.rstrip(" ·")) + c(st["faint"], " · ")
                     + c(active_ink, body)]
    if nudge and nudge != now_txt:
        segs = _wrap_segs(
            "rewrite the list, then continue", inner,
            initial_indent=f"NUDGE · {nudge} — ", subsequent_indent="        ",
            max_lines=1,
        )
        head += [c(yellow, seg) for seg in segs]
    no_times = no_times_note(state)
    if height:
        # Seven rows of chrome sit below the list — eight when the patch row is drawn — and
        # the no-times note borrows one more when it is there, so the frame still fits the
        # pane rather than pushing its top border off the screen on every repaint.
        head = head[: max(1, min(
            len(head), height - 7 - (1 if patch_row_here else 0)
            - (1 if refit_row_here else 0) - (1 if no_times else 0)
        ))]

    # In plain words: the same situations the plain renderer names, plus the one line the
    # framed pane can afford and a script's snapshot cannot — what the session has been doing
    # instead of writing a list.
    # The lists this state carries: one for a journal, a NAS session or a file, and one per
    # live thread for a desktop store asked to stack them (`--threads`). `todos` stays the
    # list the pane FOLLOWS — the bar, the totals and the footer are about it — while the
    # groups are what the step area draws (`list_groups`).
    groups = list_groups(state)
    todos = state.get("todos") or []
    if not any(g["todos"] for g in groups):
        rows += [_frame_row(h, width, frame) for h in head]
        rows.append(_divider_row(width, frame))
        why = no_list_reason(state)
        for seg in _wrap(why, inner, 0):
            rows.append(_frame_row(c(muted, seg), width, frame))
        # The second basket first, then the tool counts only when there is no feed to show:
        # both say "and here is what it has been doing instead", and the feed says it with
        # less room — which matters, because this branch has no height management of its own.
        note = tools_note(state.get("tool_calls") or {})
        # The turn is the frame the calls sit in, so it is drawn first; both are clipped to a
        # single row each, which is what keeps this branch's height countable. Nine rows of
        # chrome and the no-list explanation are already spent above this point — plus the
        # refit row when it has something to say, which is charged here too.
        room = max(0, (height - 9 - (1 if refit_row_here else 0)) if height
                   else ACTION_ROWS + 1)
        turn_line = turn_note(state, now_ms)
        body = [turn_line] if turn_line else []
        body += observed_rows(state, ACTION_ROWS, now_ms)[: max(0, room - len(body))]
        if body:
            for seg in body:
                rows.append(
                    _frame_row(c(muted, "  " + _clip_cells(seg, inner - 4)), width, frame)
                )
        else:
            for seg in _wrap(note, inner, 1) if note else []:
                rows.append(_frame_row(c(muted, seg), width, frame))
        if patch_row_here:
            # A frame with no list at all is exactly the NAS pane waiting for `fb` — when
            # "did the hook's patch step come out clean?" is the question being asked.
            rows.append(_frame_row(patch_row_styled(patch_row_here), width, frame))
        if refit_row_here:
            rows.append(_frame_row(patch_row_styled(refit_row_here), width, frame))
        rows.append(_bottom_row(width, frame))
        return "\n".join(_clamp_rows(rows, height, head=1, tail=1))

    times = state.get("task_times") or {}
    history = state.get("task_history") or {}
    shapes = state.get("task_shapes") or {}
    calls_mem = state.get("task_calls") or {}
    cur_index = current_index(todos)
    # The pace the whole list is projected at, learned from the steps already finished —
    # and from what those steps took in earlier sessions.
    pace_ms = step_pace_ms(times, todos, now_ms, history)
    blocks: list[list[str]] = []
    steps: list[dict] = []  # per block: the step it draws, for the notes ABOUT steps
    # Which block the elided window keeps on screen. With one list that is the step being
    # worked on (`_window_anchor`); with several, it is that step in the thread the pane
    # follows — or the top, when that thread has nothing in flight, because the other
    # threads are drawn BELOW it and anchoring on a tail would elide exactly the threads the
    # pane was asked to show. A thread's heading rides on the block of its FIRST step rather
    # than being a block of its own: the fit works in whole blocks, and a heading the window
    # could leave behind would leave steps standing under somebody else's name.
    multi = len(groups) > 1
    prefer = 0
    plan: list[tuple] = []
    for group in groups:
        heading = (
            _thread_heading(
                group, inner, c,
                accent if group["current"] else muted,
                accent if group["running"] else muted,
            ) if multi else None
        )
        for i, t in enumerate(group["todos"]):
            if group["current"] and i == current_index(group["todos"]):
                prefer = len(plan)
            plan.append((group, i, t, heading if i == 0 else None))
    for group, i, t, heading in plan:
        task = str(t.get("task", ""))
        done_flag = bool(t.get("completed"))
        rec = times.get(task) or {}
        elapsed = live_elapsed(rec, now_ms)
        active = i == cur_index and not done_flag
        # One symbol per row, never a box glued to an arrow: `➔ [ ]` and `○ [ ]` said the
        # same thing twice, and the box made an active row six cells wider than a finished
        # one. The three markers are one cell each, so every row's text starts in the same
        # column, and a wrapped line hangs under it.
        prefix = "  ✔  " if done_flag else "  ➔  " if active else "  ○  "
        # Every row's right-hand token carries the row's own state: muted for a step that is
        # over or not started, the accent for the one in flight (its estimate is bracketed
        # there, a badge rather than a second bare number), and red when it has run past it.
        tag_ink = muted
        indent = _cell_width(prefix)
        # The row's right-margin token, in one voice per state: the frozen span of a
        # finished step, the running clock against the pace for the step in flight, or
        # just the pace for one still ahead. A step never seen running gets no number
        # rather than a zero, which would read as "this took no time".
        running = elapsed is not None and not rec.get("done_ms")
        clock = (
            (short_duration(elapsed, seconds=True) or "0s") + ("…" if running else "")
            if elapsed is not None else ""
        )
        # This step's own estimate: its remembered history when there is one, else the
        # pace the list has settled on. The PLAIN path keeps the bare `~3m` on purpose: a
        # piped `snap` is a published, script-parsed contract, and a range would change the
        # shape of a token those scripts already match. The framed pane and `fbtodo status`
        # are where the spread is shown (see `fmt_estimate_spread`).
        est_ms = estimate_for(times, task, pace_ms, shapes, calls_mem)
        est = fmt_estimate(est_ms)
        budget = max(0, inner - indent - 8)
        if done_flag:
            tag = clock
        elif active and clock:
            stuck = est_ms > 0 and elapsed > STUCK_FACTOR * est_ms
            # When the estimate rides along, the running number drops its own `…`: the
            # pair already says the clock has not stopped, and `4m10s… / ~3m` reads as
            # two things happening to one number.
            base = clock[:-1] if running else clock
            # Widest form first, but a row never trades away so much of the task that it
            # stops being readable: `[STUCK?]` appears where the flag still leaves
            # MIN_TASK_TEXT columns of text, and on a tight pane the running/estimate pair
            # carries the alert on its own.
            cands = ([f"{base} [{est}] [STUCK?]"] if stuck else []) + [
                f"{base} [{est}]", clock,
            ]
            tag_ink = "7;31" if stuck else accent
            tag = clock
            for cand in cands:
                if _cell_width(cand) > budget:
                    continue
                if inner - (_cell_width(cand) + 2) - indent >= MIN_TASK_TEXT:
                    tag = cand
                    break
        else:
            tag = clock or est
        # The duration belongs against the frame's right wall, not glued to the last word:
        # line 1 reserves room for it, and the gap is padded out to the inner edge.
        tag_w = _cell_width(tag)
        reserve = min(tag_w + 2, max(0, inner - indent - 8)) if tag_w else 0
        segs = _wrap(task, inner - reserve, indent)
        block = []
        for j, seg in enumerate(segs):
            if j == 0:
                if done_flag:
                    # both halves of a finished row are dim: the tick no longer out-shines
                    # the description it belongs to, so the row recedes as one unit
                    line = c(tick_style, "  ✔") + "  " + c(done_style, seg)
                elif active:
                    # the one row in the frame with the accent on its marker and its
                    # number: bright ink, and the estimate as a badge
                    line = c(accent, "  ➔") + "  " + c(st["strong"], seg)
                else:
                    line = c(muted, "  ○") + "  " + c(muted, seg)
                if tag:
                    line += " " * max(1, inner - _cell_width(line) - tag_w) + c(tag_ink, tag)
            else:
                # a wrapped line carries the same voice as its first line
                if done_flag:
                    line = " " * indent + c(done_style, seg)
                elif active:
                    line = " " * indent + c(st["strong"], seg)
                else:
                    line = " " * indent + c(muted, seg)
            block.append(line)
        if height and len(block) > TASK_MAX_LINES:
            # The cap is on the STEP's lines: a heading is never one of them, because it is
            # prepended after the cap (`heading`), and one that could be cut would leave a
            # thread's steps unnamed.
            block = block[:TASK_MAX_LINES]
            block[-1] += c(muted, " …")
        if heading:
            block = [heading] + block
        blocks.append(block)
        steps.append(t)

    # Six rows of chrome, plus the optional ones this frame is actually carrying: the patch
    # and refit facts, and the no-times explanation. That last one was missing here — it was
    # subtracted where the HEADING was clipped but not from the step area's budget, so a
    # session with no clocks to report painted one row past its pane and lost the title.
    avail = None if not height else max(
        1, height - len(head) - 6 - (1 if patch_row_here else 0)
        - (1 if refit_row_here else 0) - (1 if no_times else 0)
    )
    anchor = _window_anchor(prefer if multi else cur_index, len(blocks))
    start, end = _fit_blocks(blocks, avail, anchor)

    def _rows_used(s: int, e: int) -> int:
        """Rows the task area paints: both elision notes plus the LINES of the steps kept.

        Counting blocks rather than lines is what let the goal row push this pane past its
        height: a step that wraps to two lines is two rows, and the budget is in rows.
        """
        return ((1 if s else 0)
                + sum(len(blocks[i]) for i in range(s, e))
                + (1 if e < len(blocks) else 0))

    # Rows the steps did not need, held back one at a time: the first buys the total its own
    # row (where the pane is tall enough to hand one over), the second buys the vertical
    # breath between the list and the footer. The fit is re-run with each row held back and
    # given straight back if the steps turn out to want it, so the frame can never grow past
    # `height` and no step is ever elided to make room for a number ABOUT the list.
    spare = 0
    if avail is None:
        spare = 1
    else:
        for hold in (1, 2):
            if avail - hold < 1:
                break
            if _rows_used(*_fit_blocks(blocks, avail - hold, anchor)) < avail - hold:
                spare = hold
                start, end = _fit_blocks(blocks, avail - hold, anchor)

    # Counted from the list being drawn, not from the state's own tally: the two agree
    # when this build wrote the state, and a state left by another one must never paint
    # 100% over a step list that still has unticked steps in it.
    total = len(todos)
    done = sum(1 for t in todos if t.get("completed"))
    pct = int(round(100.0 * done / total)) if total else 0
    label = f"{pct}% ({done}/{total})"
    # The bar keeps its size; the projection only borrows space the row already has left
    # over, so a narrow pane shows a shorter suffix (or none) rather than a squeezed bar.
    # `EST REM` and `ETA` are one step apart, and on a tight row the clock goes first.
    # The bar row is labelled by the bar: `PROGRESS` cost eight columns of a 68-column row
    # and said what `[████░░]` already says, and it started one column left of `PATCH`,
    # `GOAL`, `ALERT` and `REFIT` — the one footer label out of the fixed slot the others
    # keep. It is not drawn any more, and the two spaces of that slot are.
    base_w = _cell_width("  []  ") + _cell_width(label)
    bar_w = max(4, min(20, inner - base_w))
    rem_ms = remaining_estimate_ms(times, todos, now_ms, pace_ms, shapes, calls_mem)
    # Vertical room for a row of its own is decided above, where the step rows were fitted.
    spent_ms = elapsed_total_ms(times, todos, now_ms)
    tot_ms = (
        spent_ms + rem_ms if spent_ms is not None
        else None
    )
    suffix = ""
    room = inner - base_w - bar_w
    # The pace and the model that learned it ride HERE, on the row the estimates are on,
    # because the status strip is full while a step runs (the chip carries the clock and
    # the overrun) and the owner asked to see it. Same discipline as the suffix above: the
    # bar keeps its size, the text borrows what the row has left, and the first candidate
    # that fits wins — so a tight strip shows EST REM and says the pace on the strip
    # instead, where there is room when no step is running.
    cands: list[str] = []
    if rem_ms > 0:
        rem_txt = short_duration(rem_ms, seconds=True) or "0s"
        rem_spread = pace_spread_ms(times, todos, now_ms, history)
        # The remaining time is a sum of projections, so it inherits the spread of the
        # steps it sums: `EST REM: 12m (2m–1h)` says what `EST REM: 12m` alone cannot.
        # Widest first — the whole range with the clock and the pace, then the same range
        # on its own, and only then the bare number.
        ranged = (
            [f" | EST REM {rem_txt} ({fmt_range(*rem_spread)})"]
            if rem_spread and is_wide(*rem_spread) else []
        )
        # Widest form first, and the pace is not among them: the list's own pace used to
        # ride here as `~8m · model`, which is the same number every step row already
        # carries, on the rows where it is a prediction about THAT step. The model stayed
        # on the top border, where there is room for it at any width.
        cands += [
            (ranged[0] + f" | ETA {fmt_eta(rem_ms, now_ms)}") if ranged else None,
            (ranged[0]) if ranged else None,
            f" | EST REM {rem_txt} | ETA {fmt_eta(rem_ms, now_ms)}",
            f" | EST REM {rem_txt}",
        ]
        cands = [c for c in cands if c]
    for cand in cands:
        if _cell_width(cand) <= room:
            suffix = cand
            break
    # The overall time to the goal rides AFTER that ladder rather than inside it: `EST REM`
    # and `ETA` keep the priority they already had, and the total only takes columns the row
    # has left over. It borrows the progress row only when there is no spare line to carry
    # it on its own — a row of its own is the better answer, and it is what the owner asked
    # for after watching a 64-column strip drop the field entirely.
    if tot_ms is not None and not spare:
        tot_suffix = f" | TOT {short_duration(tot_ms) or '0s'}"
        if not suffix and _cell_width(tot_suffix) <= room:
            suffix = tot_suffix
        elif suffix and _cell_width(suffix + tot_suffix) <= room:
            suffix += tot_suffix
    bar = _progress_bar(done, total, bar_w, c, truecolor, theme)
    # The ratio is the row's primary datum: `active` ink (bright, no bold attribute — the
    # bold white that used to be here was one of the weights the frame could not spend),
    # and the projection is metadata, so it is muted.
    progress = (
        "  [" + bar + "]  " + c(active_ink, label)
        + (c(muted, suffix) if suffix else "")
    )

    current = todos[cur_index] if cur_index is not None else None
    cur_rec = (times.get(str(current.get("task", ""))) or {}) if current else {}
    # A closed clock is a measurement, not work in progress — and a turn that ended with a
    # step still current is idle however long ago the list was written, even if the record
    # on disk has not been rewritten yet.
    cur_elapsed = live_elapsed(cur_rec, now_ms) if step_is_running(state, cur_rec) else None
    # One state, not three tags: a finished list IS idle, so `ALL DONE` beside `IDLE 4m`
    # spent half the footer saying the same thing twice. The list's own age rides on the
    # LIST field and the LIVE clock stays rightmost; the running step's clock is not here
    # at all, because it is on the row this strip is about.
    # Uppercase, like the title chip: the pane's chrome is one voice, so a badge never
    # mixes `FREEBUFF TODOS` with `All done` in the same frame.
    cur_est_ms = estimate_for(
        times, current.get("task", "") if current else "", pace_ms, shapes, calls_mem
    )
    over_ms = (cur_elapsed - cur_est_ms) if cur_elapsed is not None else 0
    if cur_elapsed is not None:
        spin = SPINNER[int(now_ms // 1000) % len(SPINNER)] + " "
        # The footer says the STATE; the clock that goes with it is on the step's own row,
        # and it is the same number — `WORKING 11m07s` here and `11m07s [~10m]` there was the
        # footer repeating the row it is the footer for. The chip still turns red on an
        # overrun, because the footer is where a glance lands.
        word = f"{spin}WORKING"
    elif total and done == total:
        # The boundary guard: a complete list that does not account for this turn's edits
        # is not a finish, so the strip says so instead of `ALL DONE`.
        word = "STEPS OPEN" if state.get("files_unlisted") else "ALL DONE"
    else:
        word = "IDLE"
    # The age belongs to a state that is waiting: beside a running step its own counter
    # is the honest number, and the two would read as a contradiction ("Working 42s
    # (2m ago)").
    # The list's OWN age, carried by the LIST field rather than by the chip: `LIST: #3 ·
    # 12m ago`. On the chip it had to be hidden whenever a step was running — `Working 42s
    # (2m ago)` read as the STATE's age, and the two looked like a contradiction — and that
    # hid it on exactly the panes that need it, because a list an agent has stopped
    # re-writing looks current for as long as a clock is on it. On the LIST field there is
    # nothing to misread, so it is drawn whatever the state is doing. The minute's gate
    # stays: below it the number would jitter on every repaint and say nothing new.
    updated = int(state.get("source_updated_ms") or state.get("ts") or 0)
    age_text = (
        f"{short_duration(now_ms - updated) or '0m'} ago"
        if (updated and now_ms - updated >= 60_000)
        else ""
    )
    clock = time.strftime("%H:%M:%S", time.localtime(now_ms / 1000))
    live_field = "LIVE: " + clock
    listed = f"LIST: #{state['list_version']}" if state.get("list_version") else ""
    listed_age = f"{listed} · {age_text}" if (listed and age_text) else listed
    if listed_age and list_behind(state, now_ms):
        # A finished list in a session that has written well past it: the one case worth a
        # verdict, and a question mark because it is still a guess (see `list_behind`).
        listed_age += " [STALE?]"
    # The boundary guard's own field: the work that landed after the list. Its own slot so
    # the tiers below keep it past the number it is about — the warning outlives the LIST
    # number and its age on a strip too narrow for all three.
    gap = unlisted_note(state, short=True)

    # An over-budget step keeps the same chip shape but turns it red, so the overrun reads
    # at a glance without adding a word to the strip.
    chip = badge if over_ms <= 0 else (lambda text: c("7;31", text))

    def strip(text: str, keep_list: bool, keep_age: bool, keep_gap: bool) -> str:
        """The status strip: the state on a badge, then the fields ` │ ` apart."""
        body = chip(f" {text} ")
        fields: list[str] = []
        if listed:
            # the age is what a narrow strip spends first, and it goes whole
            if keep_list:
                fields.append(listed_age if keep_age else listed)
        elif keep_age and age_text:
            # No LIST field exists to carry the age — a one-shot probe has no list NUMBER to
            # show (measured on this machine: a bare `json` reports `list #0`) — so the age
            # stays where it used to live rather than going missing.
            body += c(muted, age_text)
        if gap and keep_gap:
            fields.append(gap)
        return body + "".join(
            c(st["faint"], " │ ") + c(muted, field) for field in fields + [live_field]
        )

    # On a strip too narrow for all of it the list's own age goes first, then LIST: itself —
    # the state chip and the live clock are what the row is for. The tiers are (keep_list,
    # keep_age, keep_gap). The age is the last field to go because it is the only thing on
    # the row that says the list has fallen behind the work; the guard's field goes even
    # later, because dropping the warning is exactly how the strip would lie.
    for tier in ((word, True, True, True), (word, True, False, True),
                 (word, False, False, True), (word, False, False, False)):
        status = strip(*tier)
        if _cell_width(status) <= inner:
            break
    else:
        status = _clip_cells(strip(word, False, False, False), inner)

    rows += [_frame_row(h, width, frame) for h in head]
    rows.append(_divider_row(width, frame))
    # The two summaries are rows of the step AREA, and at a budget of one or two there is no
    # window that fits both of them and a step: the frame then grew past its pane and its top
    # scrolled away, which is how a 9-line pane came to show a list with no title over it at
    # all — the height the pane gave the steps had already been spent on the notes about the
    # steps. A note is a courtesy to the steps it hides; the title and the goal are not, so
    # the notes are drawn only while the area has rows left for them (the count of what is
    # still AHEAD wins the one row, if only one is left).
    def rows_free(s: int, e: int) -> int:
        """Rows of the step area left over once the steps it keeps have painted."""
        if avail is None:
            return 2
        return max(0, avail - sum(len(blocks[i]) for i in range(s, e)))

    # ...and when the summaries were the thing that did not fit, the heading's LAST row is
    # what buys them back: at this height `NOW` is a nicety, while `4 more steps pending` is
    # the list itself. One row, one retry — never the goal, never the title.
    note_room = rows_free(start, end)
    if (height and note_room == 0 and len(head) > 1
            and (start or end < len(blocks)) and avail is not None):
        head = head[:-1]
        avail = max(1, height - len(head) - 6 - (1 if patch_row_here else 0)
                    - (1 if refit_row_here else 0) - (1 if no_times else 0))
        start, end = _fit_blocks(blocks, avail, anchor)
        note_room = rows_free(start, end)
    # With one row to spend, it goes to the LARGER hidden run: that is where the work the
    # window is not showing is, and on a finished list it is the completed steps above.
    show_above = start > 0
    show_below = end < len(blocks)
    if note_room < 2:
        if (len(blocks) - end) > start:
            show_above = False
        else:
            show_below = False
    if show_above:
        # The note counts STEPS, so it is worded from the steps those blocks drew rather
        # than from the blocks: a thread's heading is a row in a block, not a step, and a
        # count that included one would be a count of rows.
        hidden = steps[:start]
        note = _elision_note(len(hidden), [bool(s.get("completed")) for s in hidden], "earlier")
        # A run of finished work that collapses away can carry its net variance — how far
        # those steps ran from the pace — when there is one to speak of. It rides in the
        # same parentheses the idle age uses, so the row keeps its shape.
        if hidden and all(s.get("completed") for s in hidden):
            variance = run_variance_ms(times, hidden, now_ms, pace_ms)
            if variance:
                note += f" ({fmt_variance(variance)})"
        rows.append(_frame_row(c(muted, note), width, frame))
    for block in blocks[start:end]:
        rows += [_frame_row(line, width, frame) for line in block]
    if show_below:
        rest = steps[end:]
        rows.append(_frame_row(
            c(muted, _elision_note(len(rest), [bool(s.get("completed")) for s in rest], "more")),
            width, frame,
        ))
    rows.append(_divider_row(width, frame))
    rows.append(_frame_row(progress, width, frame))
    # The overall time to reach the goal, on a row of its own where the pane is tall enough
    # to hand one over. Two numbers, not three: spent and total are what this row has and
    # nowhere else has, while the THIRD it used to print — `3m30s left` — is the `EST REM`
    # on the bar row above, which is where the remaining work is projected from. The label
    # is metadata like `PATCH`'s, so it is drawn in the frame's faint ink and stops
    # competing with the number it introduces.
    goal_at = len(rows)
    if spare:
        if tot_ms is not None:
            goal_bits = [f"{short_duration(tot_ms, seconds=True) or '0s'} total"]
            if spent_ms:
                goal_bits.insert(0, f"{short_duration(spent_ms, seconds=True) or '0s'} spent")
            rows.append(_frame_row(
                "  " + c(st["faint"], "GOAL  ") + c(muted, "  ·  ".join(goal_bits)),
                width, frame,
            ))
        # ...and a spare row past that is spent on a breath rather than on nothing: the one
        # blank line between the list and the footer is the vertical padding a fixed-height
        # pane can afford, because it is the row the steps did not want — never one taken
        # off a step to buy it.
        if spare >= 2 or tot_ms is None:
            rows.append(_frame_row("", width, frame))
    goal_n = len(rows) - goal_at
    if no_times:
        rows.append(_frame_row(c(muted, no_times), width, frame))
    facts_at = len(rows)
    if patch_row_here:
        rows.append(_frame_row(patch_row_styled(patch_row_here), width, frame))
    if refit_row_here:
        rows.append(_frame_row(patch_row_styled(refit_row_here), width, frame))
    facts_n = len(rows) - facts_at
    rows.append(_frame_row(status, width, frame))
    rows.append(_bottom_row(width, frame))
    # ...and the frame still FITS: a row that scrolls off the top takes the title with it,
    # which is what a short pane used to show — a list with nothing over it. What is given
    # up is given up in the order of how little it changes what the pane is FOR: the patch
    # and alert facts first (they are news, not state, and `fbtodo status` has them), then
    # the total's own row, then the heading — never the list, the bar or the state, which
    # are what the frame exists to say at all.
    if height and len(rows) > height:
        over = len(rows) - height
        for at, count in ((facts_at, facts_n), (goal_at, goal_n), (1, len(head))):
            if over <= 0:
                break
            take = min(over, count)
            del rows[at + count - take:at + count]  # the LAST of them first: NOW, then goal
            over -= take
    rows = _clamp_rows(rows, height, head=1, tail=2)
    return "\n".join(rows)


def render(
    state: dict,
    color: bool,
    watching: int | None = None,
    width: int = 80,
    now_ms: int | None = None,
    idle_s: float | None = None,
    stale_after_s: float = 0.0,
    goal_lines: int = 3,
    height: int | None = None,
    theme: dict | None = None,
    truecolor: bool | None = None,
    reloaded: str | None = None,
) -> str:
    """Pick the framed pane (colour terminal) or the plain machine-readable text.

    `theme` and `truecolor` are the palette and the colour depth. Left out, they are resolved
    here from the environment and the theme files, which is how every command calls this; passed
    in, the frame is a function of the arguments alone — the same state and clock give the same
    bytes, whatever the terminal says. That is what makes a recorded frame a contract and lets
    the pane diff one paint against the last (see `pane_repaint`). `reloaded` is a transient
    note for the framed pane's title chip only — a plain frame has no chrome to say it on.
    """
    # The second half of the text filter (see `clean_text`): a state that came off disk —
    # this process's own cache, or a file an older build wrote — is filtered here, so no
    # frame can be painted from prose that never went through a source.
    state = clean_observation(state)
    now_ms = now_ms or int(time.time() * 1000)
    if color and width >= 30:
        frame = _render_rich(
            state, color, watching, width, now_ms, idle_s, stale_after_s, goal_lines, height,
            theme=theme, truecolor=truecolor, reloaded=reloaded,
        )
    else:
        frame = _render_plain(
            state, color, watching, width, now_ms, idle_s, stale_after_s, goal_lines, height,
        )
    return "\n".join(_clamp_widths(frame.split("\n"), width))


def width_of_default() -> int:
    return _shutil.get_terminal_size(fallback=(80, 24)).columns


def adopt_version(state: dict) -> dict:
    """Borrow the list counter from a fresh watcher state file describing the same
    list — a standalone probe has no history of its own."""
    if state.get("list_version"):
        return state
    prev = read_json(STATE_PATH, None)
    if (
        state_is_fresh(prev)
        and prev.get("session") == state.get("session")
        and prev.get("list_id") == state.get("list_id")
    ):
        state["list_version"] = prev.get("list_version")
    return state


def bar_text(state: dict) -> str:
    todos = state.get("todos") or []
    if not todos:
        return "todos -"
    done = sum(1 for t in todos if t.get("completed"))
    return f"todos {done}/{len(todos)}"# ======================================================================== lock


__all__ = [
    "_window_anchor", "no_list_reason", "list_behind", "unlisted_note", "tools_note",
    "turn_note",
    "observed_rows", "NO_TIMES_TICKED", "NO_TIMES_UNSEEN", "no_times_note", "_render_plain",
    "_frame_row", "_divider_row", "_bottom_row", "_SESSION_RE", "_session_label",
    "_top_border", "THEME_DEFAULTS", "THEME_KEYS", "THEME_FILE_LOCAL", "THEME_FILE_GLOBAL",
    "_hex_rgb", "_rgb_256", "_color_sgr", "_theme_stamp", "_THEME_CACHE", "THEME_VALUE_RE",
    "THEME_PROBLEMS", "_theme_value", "read_theme", "_theme_gradient", "_supports_truecolor",
    "_styles", "BAR_EIGHTHS", "SPINNER", "_progress_bar", "_elision_note", "_thread_heading",
    "_render_rich",
    "render", "width_of_default", "adopt_version", "bar_text",
]
