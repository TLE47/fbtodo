"""The task log, the clocks and the numbers.

Two files: the stream is the evidence (one JSON object per line, appended to and never
edited), the JSON beside it is a memo of the fold carrying the byte offset it was folded to.
Everything the pane claims about time — how long a step ran, how much is left, how wrong the
last forecast was — is derived here from spans that were actually seen running.
"""

from __future__ import annotations

import json
import os
import random
import re
import tempfile
import time
import zlib
from .base import *  # noqa: F401,F403 — the package is one namespace

def scratch_size() -> int:
    total = 0
    try:
        for name in os.listdir(SCRATCH):
            try:
                total += os.path.getsize(os.path.join(SCRATCH, name))
            except OSError:
                pass
    except OSError:
        pass
    return total


def prune_scratch(
    now_ms: int | None = None,
    max_records: int | None = None,
    max_age_days: float | None = None,
    log_cap: int | None = None,
    force: bool = False,
) -> dict:
    """Bound the scratch footprint and report what was dropped.

    Throttled with a marker inside the task log, so callers can invoke it on every
    poll or write without turning into a filesystem chore.
    """
    now_ms = now_ms or int(time.time() * 1000)
    max_records = MAX_TASK_RECORDS if max_records is None else max_records
    max_age_days = MAX_TASK_AGE_DAYS if max_age_days is None else max_age_days
    log_cap = LOG_CAP_BYTES if log_cap is None else log_cap
    report = {
        "records_removed": 0,
        "records_kept": 0,
        "temp_removed": 0,
        "log_truncated_bytes": 0,
        "skipped": False,
    }

    log = load_tasklog()
    last = log.get("pruned_ms") or 0
    if not force and (now_ms - last) < PRUNE_INTERVAL_S * 1000:
        report["skipped"] = True
        report["records_kept"] = len(log.get("tasks") or {})
        return report

    tasks = log.setdefault("tasks", {})
    cutoff = now_ms - max_age_days * 86400 * 1000 if max_age_days else 0
    for key, rec in list(tasks.items()):
        stamp = (rec or {}).get("done_ms") or (rec or {}).get("started_ms") or 0
        if cutoff and stamp and stamp < cutoff:
            tasks.pop(key, None)
            report["records_removed"] += 1
    if max_records and len(tasks) > max_records:  # cap: keep the most recent
        order = sorted(
            tasks.items(),
            key=lambda kv: (kv[1].get("done_ms") or kv[1].get("started_ms") or 0),
        )
        for key, _rec in order[: len(tasks) - max_records]:
            tasks.pop(key, None)
            report["records_removed"] += 1
    report["records_kept"] = len(tasks)
    log["pruned_ms"] = now_ms
    # ...and the stream the view came from: this is the one moment the events about records
    # that are gone can be dropped, and it is why the stream cannot grow without bound
    # behind the caps above. Compaction carries the cursor with it, so the memo written
    # below is the fold of what is left rather than of everything ever appended.
    try:
        compact_task_events(log)
    except OSError:
        pass
    try:
        atomic_write_json(TASKS_PATH, log)
    except OSError:
        pass

    # temp files orphaned by a kill mid-write (atomic writes clean up on exception,
    # not on SIGKILL)
    try:
        for name in os.listdir(SCRATCH):
            if not (name.startswith(".fbtodo.") and name.endswith(".tmp")):
                continue
            path = os.path.join(SCRATCH, name)
            try:
                if time.time() - os.path.getmtime(path) > TEMP_MAX_AGE_S:
                    os.unlink(path)
                    report["temp_removed"] += 1
            except OSError:
                pass
    except OSError:
        pass

    # the daemon's stdio is append-only; truncating an append-mode fd is safe
    try:
        size = os.path.getsize(LOG_PATH)
        if log_cap and size > log_cap:
            os.truncate(LOG_PATH, 0)
            report["log_truncated_bytes"] = size
    except OSError:
        pass
    return report


TASKLOG_SCHEMA = 2  # 2 = records keyed by (session, task)


def short_duration(ms, seconds: bool = False) -> str:
    """'45s', '3m', '1h02m' — coarse by default, for ages and idle counters.

    `seconds=True` is for a clock the reader is watching: the seconds stay visible
    past the minute (`2m35s`, zero-padded so the field does not jitter) instead of
    a step that took 2m35s reading as `2m`. Above an hour they go: `1h02m` is the
    length a duration can spend on a step line, and a step that long is rare.
    """
    if not ms:
        return ""
    secs = max(0, int(ms // 1000))
    if secs < 60:
        return f"{secs}s"
    mins = secs // 60
    if mins < 60 and seconds:
        return f"{mins}m{secs % 60:02d}s"
    if mins < 60:
        return f"{mins}m"
    return f"{mins // 60}h{mins % 60:02d}m"


# ---- time estimation --------------------------------------------------------------
# The pane does not know how long a step will take, so it learns a pace from the steps
# already finished and projects the rest with it. The number is deliberately coarse — a
# projection, not a promise — and rides inline on a row (`~3m`) rather than claiming a
# line of its own: the frame's height is fixed, and every extra row costs a step.
DEFAULT_PACE_MS = 120_000       # nothing finished yet: fall back to a couple of minutes


PACE_BOUND_FACTOR = 4.0         # how far the list's own pace may sit from the remembered one


PACE_BOUND_SAMPLES = 3          # ...and only while the list has fewer finished steps than this


STUCK_FACTOR = 2.0              # running time / pace at which a step reads as stuck


EST_CAP_MS = 6 * 3_600_000      # clamp one span, so a runaway clock cannot skew the ETA


LIST_BEHIND_MS = 10 * 60_000   # a FINISHED list this far behind a still-writing session


MIN_TASK_TEXT = 18              # columns a tagged row keeps for the task text itself


# A rung's score is a distribution, not a number: two rungs can share a median off very
# different spreads, and a median alone cannot say whether the next step will land where the
# last one did. `lo`/`hi` are the 10th and 90th percentile of the ratios by nearest rank —
# the sample's own values, no interpolation, because with eleven steps an interpolated
# quantile would be arithmetic on nothing. The tails are deliberately dropped rather than
# reported: what a rung did on its very worst step is `worst`, and letting that one step
# define the interval would make every rung look equally uncertain.
SPREAD_LO_PCT = 10              # percentile a rung's low end is read at
SPREAD_HI_PCT = 90              # ...and its high end

# Judging a duel. Both rungs are scored on the SAME steps — paired, so neither can win by
# being asked an easier set of them — and the resampling is over SESSIONS rather than steps:
# the steps inside one session are not independent, and drawing them separately would treat
# one session of long steps as a dozen pieces of evidence. `DUEL_RESOLVED` is how lopsided
# the resampled share has to be before the gap is called rather than reported.
DUEL_RESAMPLES = 4_000          # session-level bootstrap draws
DUEL_RESOLVED = 0.95            # share of draws one side must take to be the winner
DUEL_MIN_SESSIONS = 4           # below this there is nothing to resample: every draw is the sample

# The rungs the pane may PICK from, and the ones it only keeps score for. A shadow rung is
# stamped into the same forecast vector and judged by the same functions, and no code path may
# ever return it as an estimate: an idea has to earn its place from the log before it is
# allowed to move a number somebody is looking at. `recent` is the list's own last few spans —
# the pace that has just delivered — which is the one thing the whole-list pace cannot be
# compared against by itself.
SHIPPED_RUNGS = ("shape", "blend", "pace")
SHADOW_RUNGS = ("recent",)
SCORED_RUNGS = SHIPPED_RUNGS + SHADOW_RUNGS
SHADOW_SAMPLES = 3              # finished spans of the current list that `recent` is read from


def step_spans_ms(times: dict, todos: list, now_ms: int) -> list[int]:
    """The measured duration of every finished step that was actually seen running."""
    spans: list[int] = []
    for t in todos or []:
        if not t.get("completed"):
            continue
        rec = (times or {}).get(str(t.get("task", ""))) or {}
        span = live_elapsed(rec, now_ms)
        # A sub-10s span is a list flip, not a measurement: it is shown on its row but it
        # must not set the pace for everything after it (see `label_floor_ms`).
        if span and span >= label_floor_ms():
            spans.append(min(span, EST_CAP_MS))
    return spans


# In plain words: the middle value, not the average. Five steps of 1, 2, 3, 4 and 40
# minutes average about 10 and have a middle of 3 — and 3 is the better guess for the next
# one. A single freak-slow step must not drag every estimate with it.
def _median_ms(values: list[int]) -> int | None:
    """The middle of a list of spans, robust to one value running away."""
    if not values:
        return None
    spans = sorted(values)
    mid = len(spans) // 2
    return spans[mid] if len(spans) % 2 else (spans[mid - 1] + spans[mid]) // 2


# In plain words: the pace of the last few steps rather than of the whole list. A list that
# started with two slow steps and has settled has a whole-list pace that lags what is actually
# happening; a list that is slowing down has one that flatters it. This is the shadow rung's
# whole content, and it reads the tail of the CURRENT list only — another list's steps are not
# this list's pace.
def recent_pace_ms(
    times: dict,
    todos: list,
    now_ms: int,
    samples: int | None = None,
) -> int | None:
    """The median of the list's own last few finished spans, or None if there are not two.

    Two, not one: a single span is that step's own time rather than a pace, and a rung built
    on it would just be the last outcome quoted back as a forecast. Nothing here is exported
    — the value is stamped into the ledger's vector and scored there; see `SHADOW_RUNGS`.
    """
    keep = SHADOW_SAMPLES if samples is None else max(1, int(samples))
    tail = [s for s in step_spans_ms(times, todos, now_ms) if s >= label_floor_ms()][-keep:]
    return _median_ms(tail) if len(tail) >= 2 else None


def rank_quantile(values: list[float], pct: int) -> float:
    """The sample's own value at `pct` — nearest rank, on a SORTED list.

    No interpolation on purpose: an estimate is scored on a handful of steps, and a
    interpolated value between two of them would be a number no step ever produced. The rank
    is computed in whole percent with integer arithmetic so a float multiply cannot land the
    index one place off on a sample of exactly ten.
    """
    if not values:
        return 0.0
    rank = (max(1, min(pct, 100)) * len(values) + 99) // 100
    return values[max(0, min(rank - 1, len(values) - 1))]


# In plain words: a memory of how long each named step took, kept between sessions and kept
# per model — the same step costs very different time under different models. It is filed
# under the step's own wording, so "Run the tests" is estimated from the last "Run the
# tests" and not from the average of whatever this list happens to hold.
def task_history_from_log(tasks: dict, now_ms: int | None = None, model: str | None = None) -> dict:
    """Per-task remembered span (median), read from the log's finished records.

    Kept ACROSS sessions, so a later list projects a step from what it took last time
    rather than from the average of an unrelated list. Two pruning rules keep a stale
    wording from skewing a pace it is never asked about:

    * a record outside the retention window (`HISTORY_MAX_AGE_DAYS`, the same as the log's
      own prune) contributes nothing, so an ancient fluke drops out immediately instead of
      waiting for the hourly prune to remove the record;
    * the map is capped to `HISTORY_MAX_ENTRIES` of the most-recently-seen names, so the
      fallback median is not diluted by a long tail of one-off titles.

    Median again, for the same reason as the pace: one slow run must not poison the
    estimate forever.

    MODEL-AWARE: a step's span is only comparable to a step taken by the SAME model —
    measured 2026-09-26, `deepseek/deepseek-v4-flash` and `stealth/space-bunny-alpha` on
    the same machine differed by more than the whole estimate. So when the session names a
    model, records carrying that model win, and records from any other model are used only
    if it has none of its own (a model seen for the first time is better served by a rough
    number than by none, and its own records take over as soon as it has them).

    Each entry is `{"med": int, "n": int}` rather than a bare number: the median is still
    what a caller reads, and `n` is how many samples stand behind it — which is the whole
    difference between a guess from one run and a settled number. Older state files hold a
    bare int and read the same way (see `hist_med`).
    """
    by_task, last_seen = _history_gather(
        tasks, now_ms, model, lambda rec, label: label
    )
    history: dict[str, dict] = {}
    for label, spans in by_task.items():
        entry = _hist_entry(spans)
        if entry:
            history[label] = entry
    if len(history) > HISTORY_MAX_ENTRIES:
        recent = sorted(history, key=lambda k: last_seen.get(k, 0), reverse=True)
        history = {k: history[k] for k in recent[:HISTORY_MAX_ENTRIES]}
    return history


# In plain words: the same memory, keyed by what a step DID instead of what it was called.
# This is the one that can actually get better with use: the wording space grows without
# bound (172 of 173 labels seen once) while the shape space is small — a handful of verbs
# binned three ways — so samples accumulate in the same bucket and the number sharpens.
def shape_history_from_log(
    tasks: dict, now_ms: int | None = None, model: str | None = None
) -> dict:
    """Per-SHAPE remembered span: {`edited3+ ran2`: {med, n}} from the log's finished steps.

    Same retention and model rules as `task_history_from_log` (one gatherer serves both),
    so a shape learned on a fast model never projects a slow one. A step whose calls were
    never recorded contributes nothing rather than a bucket of its own.
    """
    by_shape, last_seen = _history_gather(
        tasks, now_ms, model, lambda rec, label: shape_of(rec.get("shape"))
    )
    shapes: dict[str, dict] = {}
    for bucket, spans in by_shape.items():
        entry = _hist_entry(spans)
        if entry:
            shapes[bucket] = entry
    if len(shapes) > HISTORY_MAX_ENTRIES:
        recent = sorted(shapes, key=lambda k: last_seen.get(k, 0), reverse=True)
        shapes = {k: shapes[k] for k in recent[:HISTORY_MAX_ENTRIES]}
    return shapes


# In plain words: one remembered number together with the evidence behind it — how many
# samples, and how far apart they were. A number without its `n` cannot be told from a
# guess, and one without its spread cannot be told from a promise.
def _hist_entry(spans: list[int]) -> dict | None:
    """One history entry: the median, its sample count, and its quartiles when there are two."""
    mid = _median_ms(spans)
    if not mid:
        return None
    entry: dict = {"med": mid, "n": len(spans)}
    spread = _spread_ms(spans)
    if spread:
        entry["lo"], entry["hi"] = spread
    return entry


# In plain words: one pass over the task log, read either way — by step wording for the
# per-step memory, or by call shape for the one that accumulates. One gatherer, so both
# memories age out, cap and split by model identically.
def _history_gather(tasks: dict, now_ms: int | None, model: str | None, key_of):
    """({key: [spans]}, {key: last stamp}) for this model, or for every model if it has none."""
    now_ms = now_ms or int(time.time() * 1000)
    cutoff = now_ms - HISTORY_MAX_AGE_DAYS * 86400 * 1000
    mine: dict[str, list[int]] = {}      # spans this model took
    every: dict[str, list[int]] = {}     # spans of any model
    last_seen: dict[str, int] = {}
    for key, rec in (tasks or {}).items():
        if not isinstance(rec, dict):
            continue
        label = key.split("\x1f", 1)[1] if "\x1f" in key else key
        bucket = key_of(rec, label)
        if not bucket:
            continue
        started, stopped = rec.get("started_ms"), rec.get("done_ms")
        stamp = stopped or started or 0
        if stamp and stamp < cutoff:
            continue
        last_seen[bucket] = max(last_seen.get(bucket, 0), stamp)
        if not started or not stopped or stopped <= started:
            continue
        span = min(stopped - started, EST_CAP_MS)
        if span < label_floor_ms():
            continue  # a list flip is shown, not remembered (see `label_floor_ms`)
        every.setdefault(bucket, []).append(span)
        if model and rec.get("model") == model:
            mine.setdefault(bucket, []).append(span)
    # this model's own spans when it has any, every model's when it has none yet
    return (mine or every), last_seen


# In plain words: the SHAPE of a step is what it DID — how many calls it made — rather than
# what it was called. The first build signed the MIX (`edited3+ ran2`); measured 2026-09-29
# over the 258 steps recovered from the CLI journals, the COUNT alone carries more of the
# duration than the mix does — out-of-sample R^2 0.645 against 0.390, and present for 100% of
# steps against 59% — so the signature is the total call count, binned by powers of two.
# That is also why "three edits and nine edits" finally land in different buckets.
def shape_of(shape: dict | None) -> str:
    """A step's size as its signature: `calls0`, `calls1`, ... = log2 of the call count.

    Log2 bins, not exact counts: 1, 2-3, 4-7, 8-15 calls. Few enough buckets that samples
    accumulate (the verb mix needed 68 keys for the same 170 steps; this needs 7), and fine
    enough that a step of one call is never confused with one of twelve.
    """
    total = 0
    for n in (shape or {}).values():
        try:
            total += int(n)
        except (TypeError, ValueError):
            continue
    if total <= 0:
        return ""
    return f"calls{total.bit_length() - 1}"


def step_shape_bucket(times: dict, task) -> str:
    """The size the step being worked on has revealed so far, or "" for one that has none."""
    return shape_of(((times or {}).get(str(task)) or {}).get("shape"))


def bucket_ordinal(bucket: str) -> int | None:
    """The log2 call bucket's ordinal (`calls4` = 16-31 calls -> 4), or None if not a bucket."""
    name = str(bucket or "")
    if not name.startswith("calls"):
        return None
    try:
        return int(name[len("calls"):])
    except ValueError:
        return None


def sized_entry(shapes: dict | None, bucket: str) -> dict | None:
    """The size memory's entry for `bucket`, or None while that step is too young to have one.

    The one place the sample gate and the `SHAPE_MIN_BUCKET` floor are applied, so no caller
    can look a size up on its own terms and drift from the ladder `pick_estimate` walks.
    """
    ordinal = bucket_ordinal(bucket)
    if ordinal is None or ordinal < SHAPE_MIN_BUCKET:
        return None
    entry = (shapes or {}).get(str(bucket))
    if isinstance(entry, dict) and int(entry.get("n") or 0) >= SHAPE_MIN_SAMPLES:
        return entry
    return None


# In plain words: what a WAITING step is probably going to be. It has made no calls, so it
# has no size and the size memory cannot price it — but it does have its wording. Measured
# 2026-09-29 over the 170 ticked steps recovered from the CLI journals, a keyword class of
# that wording predicts the call count well enough that `class x seconds-per-call`, blended
# 50/50 in log space with the list's pace, beats the pace ALONE on every column: median
# 2.10x against 2.89x, mean 3.63x against 4.71x, p90 6.37x against 8.48x, and it wins on 74%
# of steps. Used by itself the label model fixes the median and wrecks the tail, which is
# exactly why it is only ever a half of the answer.
CLASS_RULES = (
    ("deploy", ("deploy", "nas", "ssh", "container", "docker", "publish", "push")),
    ("run", ("run", "test", "tests", "verify", "execute", "build", "sweep", "selfcheck",
             "check", "profile", "measure", "bench")),
    ("docs", ("document", "docs", "readme", "doc", "comment", "comments", "explain")),
    ("edit", ("add", "write", "fix", "implement", "update", "edit", "change", "refactor",
              "create", "remove", "delete", "rename", "move", "wire", "retire", "switch")),
    ("inspect", ("read", "look", "check", "confirm", "find", "search", "examine", "review",
                 "inspect", "locate", "understand", "explore", "map", "recon", "see")),
)


CLASS_WORDS = 64   # words of a label the class test looks at, so a whole paragraph is cheap


STOP_WORDS = frozenset(
    "the a an to of and or for in on with into from it its this that is are be as at by "
    "not no new so if then else my your our their".split()
)


def label_class(task: str) -> str:
    """The coarse kind of work a step's wording names: deploy / run / docs / edit / inspect."""
    text = re.sub(r"\([^)]*\)", " ", str(task or "")).lower()
    words = {w for w in re.findall(r"[a-z0-9]+", text)
             if len(w) > 1 and w not in STOP_WORDS}
    for name, needles in CLASS_RULES:
        if words.intersection(needles):
            return name
    return "other"


def call_memory_from_log(tasks: dict, now_ms: int | None = None, model: str | None = None) -> dict:
    """{classes: {cls: calls}, calls: int, rate_ms: int} learned from finished steps.

    The one memory a waiting step can use. Model-aware and age-gated like the span memories,
    and it counts only real steps (a sub-10 s flip has no shape worth learning from).
    """
    now_ms = now_ms or int(time.time() * 1000)
    cutoff = now_ms - HISTORY_MAX_AGE_DAYS * 86400 * 1000
    mine: dict[str, list[int]] = {}
    every: dict[str, list[int]] = {}
    mine_rate: list[int] = []
    every_rate: list[int] = []
    for key, rec in (tasks or {}).items():
        if not isinstance(rec, dict):
            continue
        started, stopped = rec.get("started_ms"), rec.get("done_ms")
        if not started or not stopped or stopped <= started or stopped < cutoff:
            continue
        span = min(stopped - started, EST_CAP_MS)
        if span < label_floor_ms():
            continue
        total = 0
        for n in (rec.get("shape") or {}).values():
            try:
                total += int(n)
            except (TypeError, ValueError):
                continue
        if total <= 0:
            continue
        label = key.split("\x1f", 1)[1] if "\x1f" in key else key
        cls = label_class(label)
        every.setdefault(cls, []).append(total)
        every_rate.append(max(1, round(span / total)))
        if model and rec.get("model") == model:
            mine.setdefault(cls, []).append(total)
            mine_rate.append(max(1, round(span / total)))
    classes, rates = (mine, mine_rate) if mine else (every, every_rate)
    if not classes:
        return {}
    return {
        "classes": {c: _median_ms(v) for c, v in classes.items()},
        "calls": _median_ms([n for v in classes.values() for n in v]),
        "rate_ms": _median_ms(rates) or 0,
    }


def pending_blend_ms(
    task: str, pace_ms: int, calls_mem: dict | None, weight: float | None = None
) -> int | None:
    """A waiting step's projection: label-class calls x seconds-per-call, blended with the pace.

    Blended in log space, `w` on the label model and `1 - w` on the pace (0.5 by default,
    which is the geometric mean). `w` was not fitted — it is the midpoint, and the honest
    reading of the replay is that the blend smooths the tail rather than beating the pace on
    the typical step (see `blend_weight` for the numbers). None when there is nothing to blend
    with, and then the pace stands alone exactly as it did before, so a fresh log cannot
    regress.
    """
    w = blend_weight() if weight is None else weight
    if not calls_mem or not pace_ms or w <= 0:
        return None
    calls = (calls_mem.get("classes") or {}).get(label_class(task)) or calls_mem.get("calls")
    rate = calls_mem.get("rate_ms")
    if not calls or not rate:
        return None
    predicted = int(calls) * int(rate)
    if predicted <= 0:
        return None
    blended = (predicted ** w) * (pace_ms ** (1.0 - w))
    return min(int(blended), EST_CAP_MS)


# In plain words: one remembered span, read the same way whether it was stored as a bare
# number (an older build's task log) or as a small record of the samples behind it.
def hist_med(entry) -> int | None:
    """The median inside one history entry: a bare int, or a dict with `med`."""
    if isinstance(entry, dict):
        val = entry.get("med")
    else:
        val = entry
    try:
        val = int(val)
    except (TypeError, ValueError):
        return None
    return val if val > 0 else None


# In plain words: the rate the estimates borrow from, for steps with no history of their
# own. The middle duration of the steps finished in THIS list; failing that, the middle of
# the remembered ones; and only with no past at all, a flat couple of minutes.
def step_pace_ms(times: dict, todos: list, now_ms: int, history: dict | None = None) -> int:
    """The pace the estimates ride on: the median duration of the steps already finished.

    Median, not mean: one step that ran away (a long build, a stuck probe) must not drag
    every other projection with it. With nothing finished in THIS list there is no
    distribution to speak of, so the remembered history takes over — the median of what
    every step in the log has taken before — and only a session with no past at all falls
    back to the default that keeps the numbers in the order of minutes.

    BOUNDED while the list is still young, and that bound was measured, not guessed: a median
    of ONE OR TWO finished steps is not a distribution, and it is where the estimates go
    badly wrong — one 2s step projected the whole list at `~2s` while the next step took
    1m53s (45x out), and a `~18m` pace came from a single fast step (22x out). Blending the
    pace toward history instead was tried and REJECTED: it made the typical case worse,
    because a list is homogeneous within itself and the log's other sessions are not.

    So the list's own pace still wins whenever it has `PACE_BOUND_SAMPLES` or more finished
    steps behind it; below that it is clipped to within `PACE_BOUND_FACTOR` of what this
    model's steps are remembered to cost, and never replaced by it.

    REFIT 2026-09-29 over the 161 real spans recovered from the CLI journals, leave-one-
    session-out, through this function. It replaces a 17-prediction table whose harness walked
    each session's steps by SPAN instead of by time, which is what made the bound look better
    than it is; the honest numbers are:

        config                     median    mean     p90     worst
        no bound at all             2.18x    3.80x   7.56x    33.9x
        4x, while n < 3 (ships)     2.18x    3.70x   7.56x    29.9x
        2x, while n < 3             2.41x    3.66x   7.46x    29.9x
        8x, while n < 3             2.18x    3.80x   7.56x    33.9x

    The median does not move at ANY factor — a tighter bound is worse on it (2.41x at 2x) and no
    bound at all is worse only in the tail. That much of the original reasoning survives. What
    does not is how much the bound is buying: it is consulted on FOUR steps of the whole replay
    (every other step has the same number either way), and on those four the no-bound reading
    wins three — the bound turns one 45x miss into an 18x one and makes three 1.2-2.8x steps
    about a tenth of a factor worse. So it trades one large save for three small losses: better
    on the mean and the worst case, worse on a bare step count, identical on the median and p90,
    and a bootstrap over sessions cannot separate the two readings (73% of resamples, on four
    steps). Four steps do not settle that, so the bound stays as a JUDGMENT CALL rather than a
    finding — and neither constant is worth refitting again until there are several hundred
    spans, because at 161 a median difference under about 0.15x is noise and screening a grid
    for something smaller is how noise gets shipped as a result.
    """
    spans = step_spans_ms(times, todos, now_ms)
    pace = _median_ms(spans)
    remembered = [
        min(m, EST_CAP_MS)
        for m in (hist_med(v) for v in (history or {}).values())
        if m
    ]
    reminded = _median_ms(remembered)
    if pace is None:
        return reminded or DEFAULT_PACE_MS
    if reminded and len(spans) < PACE_BOUND_SAMPLES:
        low = max(1, int(reminded / PACE_BOUND_FACTOR))
        high = int(reminded * PACE_BOUND_FACTOR)
        return min(max(pace, low), high)
    return pace


# In plain words: one step's estimate, in the order the evidence deserves — what this exact
# step took before, then what steps of the same SHAPE took, then the list's pace.
def estimate_for(
    times: dict, task: str, pace_ms: int, shapes: dict | None = None,
    calls_mem: dict | None = None,
) -> int:
    """One step's projection, from the best evidence available about it."""
    return task_estimate_ms(
        task, pace_ms, shapes, step_shape_bucket(times, task), calls_mem
    )


# In plain words: the ladder the estimate walks, written once so that no caller can drift
# from it. `None` for the value means "nothing better than the list's pace was available",
# and the source it returns is what the error report files the result under.
def pick_estimate(
    task: str, shapes: dict | None = None, bucket: str = "",
    pace_ms: int | None = None, calls_mem: dict | None = None,
) -> tuple[int | None, str]:
    """(value or None, source) — the size memory, then the label blend, then the pace.

    The size rung answers only for a step that has shown `SHAPE_MIN_BUCKET` worth of size, so
    `bucket` being non-empty is not enough to fire it; below that floor the blend or the pace
    carries the number (see the constant for why the floor exists at all).

    There is deliberately no `own wording` rung any more. It was the first rung for a long
    time and, measured 2026-09-29, it never once fired: replayed over the 258 steps recovered
    from the CLI journals, 0 of 170 ticked steps met their own wording in another session
    (172 of the 173 labels ever written have been seen exactly once). A rung that cannot
    answer is not a safety net; it is a branch that only hides the pace under it.

    What replaced it is the other direction: not "has this step run before", but "what kind
    of step is this" — which is the only question a row that has not STARTED can answer.
    """
    entry = sized_entry(shapes, bucket)
    if entry:
        seen = hist_med(entry)
        if seen:
            return min(seen, EST_CAP_MS), "shape"
    blended = pending_blend_ms(task, pace_ms or 0, calls_mem)
    if blended:
        return blended, "blend"
    return None, "pace"


def task_estimate_ms(
    task: str,
    pace_ms: int,
    shapes: dict | None = None,
    bucket: str = "",
    calls_mem: dict | None = None,
) -> int:
    """The estimate for one step: the size memory, else the label blend, else the pace.

    The log remembers every finished step by how big it was — the calls it made (see
    `shape_of`) — so a step already running, whose calls are being counted as it works, is
    projected from steps of the same size rather than from the average of whatever this list
    happens to contain. A size is only allowed to answer once `SHAPE_MIN_SAMPLES` remembered
    steps of that size stand behind it, so one coincidence never overrides the list's pace —
    and only once the step's own tally has passed `SHAPE_MIN_BUCKET`, because until then its
    bucket names a smaller step than the one it is going to be (see the constant).

    A waiting row has no calls yet and so no size: it is priced at the pace. That is the
    honest limit of this memory, and the reason `EST REM` — mostly waiting rows — moves
    little as the memory learns.
    """
    value, _source = pick_estimate(task, shapes, bucket, pace_ms, calls_mem)
    return value if value else pace_ms


# In plain words: how far apart the samples behind a number were. Two finished steps of
# 10 seconds and 10 minutes is not a pace of five minutes with a bit of noise on it — it is
# a list where the next step is genuinely unknowable to within a factor of 60, and saying
# `~5m` alone would be claiming a precision nobody has.
def _spread_ms(spans: list[int]) -> tuple[int, int] | None:
    """(low, high) of the samples behind a number — quartiles, or None with too few."""
    vals = sorted(s for s in (spans or []) if s and s > 0)
    if len(vals) < SPREAD_MIN_SAMPLES:
        return None
    mid = len(vals) // 2
    low = _median_ms(vals[:mid]) or vals[0]
    high = _median_ms(vals[mid + (len(vals) % 2):]) or vals[-1]
    return low, high


def fmt_range(lo: int, hi: int) -> str:
    """`1m–8m` — the low and high of the samples behind an estimate."""
    return f"{short_duration(lo) or '0s'}–{short_duration(hi) or '0s'}"


def is_wide(lo: int, hi: int) -> bool:
    """Is one number the wrong shape for this spread? 3x or more, measured either side."""
    return bool(lo and hi and hi >= lo * SPREAD_MIN_RATIO)


def fmt_estimate_spread(point: int, spread: tuple[int, int] | None) -> str:
    """`~3m`, or `~3m (1m–8m)` when the samples behind the 3m disagree by 3x or more.

    The point estimate is never thrown away — it is what the eye wants — and the range is
    only added where the spread is genuinely wide, so an ordinary list keeps reading `~3m`.
    """
    token = fmt_estimate(point)
    if not spread:
        return token
    lo, hi = spread
    return f"{token} ({fmt_range(lo, hi)})" if is_wide(lo, hi) else token


# In plain words: the spread behind the pace — this list's own finished steps when there are
# two or more, else the remembered ones. Also the honest bound on `EST REM`, which is a sum
# of projections: if a step is 10s-or-20m, so is the total.
def pace_spread_ms(
    times: dict, todos: list, now_ms: int, history: dict | None = None
) -> tuple[int, int] | None:
    """The spread the pace rides on, from the same samples that set it."""
    mine = step_spans_ms(times, todos, now_ms)
    if len(mine) >= SPREAD_MIN_SAMPLES:
        return _spread_ms(mine)
    remembered = [m for m in (hist_med(v) for v in (history or {}).values()) if m]
    return _spread_ms(remembered)


def entry_spread(entry) -> tuple[int, int] | None:
    """The `lo`/`hi` inside a history entry, when both are there."""
    if not isinstance(entry, dict):
        return None
    lo, hi = entry.get("lo"), entry.get("hi")
    try:
        lo, hi = int(lo), int(hi)
    except (TypeError, ValueError):
        return None
    return (lo, hi) if lo > 0 and hi > 0 else None


# In plain words: did the estimates get better? The task log already holds the pair needed
# to answer that — what a step was projected to take at the moment it closed, and what it
# actually took — so the error is computed from the records rather than kept in a tally that
# could drift away from them. Reported per SOURCE, because that is the only way to see
# whether the shape memory is earning its place on this machine's real work.
def forecast_error(
    tasks: dict,
    now_ms: int | None = None,
    model: str | None = None,
    days: float | None = None,
) -> dict:
    """{rung: {n, med, mean, worst, lo, hi}} — each rung scored on its FIRST-poll forecast.

    Rungs whose one vector was stamped too late to be a forecast (see `LEDGER_FRESH_MS`) are
    left out of every rung and counted under `late`, so a watcher restart mid-step cannot
    quietly improve the numbers it appears in.

    The honest scoreboard, and the reason the ledger exists. `estimate_error` scores the
    estimate the pane was showing when a step CLOSED, which for the size rung is a key built
    from calls the step had already made — so it is partly scored with the answer in hand and
    can flatter a rung that recognises rather than predicts. This scores the vector written
    on the first poll that saw the step running (see the `fc` stamp in `track_tasks`), when
    nothing about the step's size was known, and scores every rung on every step — which is
    what makes the numbers comparable to each other rather than to their own sample.
    """
    now_ms = now_ms or int(time.time() * 1000)
    cutoff = now_ms - int((days if days is not None else HISTORY_MAX_AGE_DAYS) * 86400 * 1000)
    out: dict[str, list[float]] = {}
    late = 0
    for key, rec in (tasks or {}).items():
        if not isinstance(rec, dict):
            continue
        started, stopped = rec.get("started_ms"), rec.get("done_ms")
        if not started or not stopped or stopped <= started or stopped < cutoff:
            continue
        if model and rec.get("model") and rec.get("model") != model:
            continue
        actual = stopped - started
        if actual < label_floor_ms():
            continue
        fc = rec.get("fc") or {}
        if not fc:
            continue
        # A vector whose own stamp says the step was already running when the watcher first
        # saw it (a restart mid-step) is not a forecast: it carries the step's clock inside
        # it. Scored as one it would flatter whichever rung reads elapsed, which is the exact
        # flattery the ledger exists to prevent, so it is left out and counted instead.
        if fc.get("late"):
            late += 1
            continue
        for rung in SCORED_RUNGS:
            try:
                pred = int(fc.get(rung))
            except (TypeError, ValueError):
                continue
            if pred <= 0:
                continue
            out.setdefault(rung, []).append(max(pred / actual, actual / pred))
    report = {}
    for rung, fs in out.items():
        fs = sorted(fs)
        mid = len(fs) // 2
        med = fs[mid] if len(fs) % 2 else (fs[mid - 1] + fs[mid]) / 2
        report[rung] = {
            "n": len(fs),
            "med": round(med, 2),
            "mean": round(sum(fs) / len(fs), 2),
            "worst": round(max(fs), 1),
            # the spread the median sits in — see SPREAD_LO_PCT
            "lo": round(rank_quantile(fs, SPREAD_LO_PCT), 2),
            "hi": round(rank_quantile(fs, SPREAD_HI_PCT), 2),
        }
    if late:
        # guarded, not scored: rounding a fault to zero is what keeps a rung from being
        # improved by the very rows the flag set aside. No `lo`/`hi` here — this is a count.
        report["late"] = {"n": late, "med": 0.0, "mean": 0.0, "worst": 0.0}
    return report


def rung_duel(
    rows: list[dict],
    a: str,
    b: str,
    resamples: int | None = None,
) -> dict:
    """Does rung `a` really beat rung `b`, or does this log not say?

    The scoreboard answers "which rung is closest on the median". That is not the same
    question as "is one of them better", because a median gap of a few percent on twenty
    steps is as likely to be the sample as the rung. This pairs the two rungs over the SAME
    steps — a rung cannot win by being asked an easier set of them — and resamples to see
    how much the answer depends on WHICH steps happened to be logged.

    The resampling is over SESSIONS, not steps. The steps inside one session are not
    independent: one session that ran twelve long steps is one story, not twelve pieces of
    evidence, and drawing steps separately would let it vote twelve times. So whole sessions
    are drawn with replacement, which is the coarsest unit the log actually has.

    `share_steps` is the raw share of paired steps `a` won; `share_resamples` is the share of
    bootstrap draws it won, and that is the number that decides. `winner` is None unless the
    draws are lopsided past `DUEL_RESOLVED` over at least `DUEL_MIN_SESSIONS` sessions, which
    is the whole point: a duel that does not resolve says the log is too small, rather than
    crowning the rung that leads today. A rung with nothing to score it against is not a
    winner by default — no pairing, no verdict.

    The draws are seeded from the two rung names and the sample shape, so the same log
    reports the same verdict twice in a row (`--twice` and the goldens depend on it) without
    the answer being a constant.
    """
    resamples = DUEL_RESAMPLES if resamples is None else max(1, int(resamples))
    pairs: list[tuple[str, float, float]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        errs = row.get("errors") or {}
        ea, eb = errs.get(a), errs.get(b)
        if ea is None or eb is None or a == b:
            continue
        pairs.append((row.get("session") or "", ea, eb))
    sessions = sorted({s for s, _ea, _eb in pairs})
    steps = len(pairs)
    if not steps or not sessions:
        return {"steps": steps, "sessions": len(sessions), "share_steps": None,
                "share_resamples": None, "winner": None, "resamples": resamples}
    by_session: dict[str, list[tuple[float, float]]] = {}
    for session, ea, eb in pairs:
        by_session.setdefault(session, []).append((ea, eb))
    share_steps = sum(1 for _s, ea, eb in pairs if ea < eb) / steps
    rng = random.Random(zlib.crc32(f"{a}\x1f{b}\x1f{steps}\x1f{len(sessions)}".encode()))
    wins = 0
    for _ in range(resamples):
        drew = took = 0
        for session in rng.choices(sessions, k=len(sessions)):
            for ea, eb in by_session[session]:
                drew += 1
                took += ea < eb
        if took * 2 >= drew:  # a draw won by a half or better goes to `a`
            wins += 1
    share_resamples = wins / resamples
    # A bootstrap over ONE session cannot resample anything: every draw is the sample it
    # started from, so the share comes back at 1.0 and would crown whichever rung happened to
    # lead in that one session. Below the floor the duel reports the sample instead.
    winner = None
    if len(sessions) >= DUEL_MIN_SESSIONS:
        if share_resamples >= DUEL_RESOLVED:
            winner = a
        elif share_resamples <= 1 - DUEL_RESOLVED:
            winner = b
    return {
        "steps": steps,
        "sessions": len(sessions),
        "share_steps": round(share_steps, 4),
        "share_resamples": round(share_resamples, 4),
        "winner": winner,
        "resamples": resamples,
    }


def fmt_rung(stat: dict) -> str:
    """`1.05x [0.98–1.30] over 22` — one rung's median with its spread and its sample size.

    The bracket is dropped when every sample gave the same ratio. A range of one value is not
    a range, and printing `[1.05–1.05]` would suggest a spread the samples do not have.
    """
    text = f"{stat['med']:.2f}x"
    if stat.get("hi") != stat.get("lo"):
        text += f" [{stat['lo']:.2f}–{stat['hi']:.2f}]"
    return f"{text} over {stat['n']}"


def duel_note(a: str, b: str, verdict: dict) -> str:
    """One duel's verdict in words — who won, how lopsided the draws were, on what sample.

    The share printed is the WINNER's, not `a`'s: a pair read right-to-left should not turn
    a 0.03 into a 0.03-in-favour-of-the-other-one. An unresolved duel says so and keeps the
    numbers, because "these two are equivalent" and "this log cannot tell them apart" are
    different claims and only the second one is true of a small sample.
    """
    steps = verdict.get("steps") or 0
    sessions = verdict.get("sessions") or 0
    resamples = verdict.get("resamples") or 0
    share = verdict.get("share_resamples") or 0.0
    where = f"{steps} step(s) in {sessions} session(s)"
    if sessions < DUEL_MIN_SESSIONS:
        return f"{a} vs {b} — unresolved, {where}, needs {DUEL_MIN_SESSIONS} session(s)"
    winner = verdict.get("winner")
    if winner:
        loser = b if winner == a else a
        won = share if winner == a else 1 - share
        return f"{winner} beats {loser} — {won:.2f} of {resamples:,} draws over {where}"
    return f"{a} vs {b} — unresolved, {share:.2f} of {resamples:,} draws over {where}"


def refit_readiness(
    tasks: dict,
    now_ms: int | None = None,
    model: str | None = None,
    days: float | None = None,
) -> dict:
    """{scored, eligible, decided, spans_needed} — how close the log is to refitting itself.

    The score lines say what the rungs are doing; this says whether there is enough of it to
    re-choose the constants, and it is computed from the log alone, no replay needed:

    * `scored` — closed steps carrying a forecast: the sample a median would stand on. One
      step is not a distribution, so this is compared against `REFIT_MIN_SCORED`.
    * `eligible` — steps that started while their own LIST (the `lv` stamped on the record,
      or the session on older records) had fewer than `PACE_BOUND_SAMPLES` FINISHED steps,
      which is the only time the clip is consulted.
    * `decided` — steps where it actually MOVED the number. The pace stored in the step's own
      vector is the clipped one, and the median of its list's earlier finished spans — read
      from the log, the same way `step_spans_ms` reads it, floor and cap included — is what
      it would have used WITHOUT the clip. When those two differ, the clip decided this step.
      Measured over the 161-span replay, 157 spans were identical either way, so this count
      is the one that governs how long it takes to judge the clip at all.

    `spans_needed` extrapolates the observed decide rate to `REFIT_MIN_DECIDED`, and is None
    while fewer than five steps have been decided: a rate built on one or two is not a rate.
    """
    now_ms = now_ms or int(time.time() * 1000)
    cutoff = now_ms - int((days if days is not None else HISTORY_MAX_AGE_DAYS) * 86400 * 1000)
    rows: list[tuple[str, int, int, dict]] = []
    for key, rec in (tasks or {}).items():
        if not isinstance(rec, dict) or not isinstance(rec.get("fc"), dict):
            continue
        started, stopped = rec.get("started_ms"), rec.get("done_ms")
        if not started or not stopped or stopped <= started or started < cutoff:
            continue
        if model and (rec.get("model") or "") != model:
            continue
        # The LIST it was in when it was last seen, falling back to the session for records
        # written before `lv` existed — a coarser grouping, and the only one available then.
        group = f"{str(key).partition(chr(31))[0]}\x1f{rec.get('lv')}"
        rows.append((group, started, stopped - started, rec["fc"]))
    rows.sort(key=lambda r: r[1])
    by_list: dict[str, list[tuple[int, int]]] = {}
    scored = eligible = decided = 0
    for group, started, span, fc in rows:
        prior = [
            sp
            for st, sp in by_list.get(group, [])
            if st < started and sp >= label_floor_ms()
        ]
        by_list.setdefault(group, []).append((started, span))
        if span < label_floor_ms():
            continue
        scored += 1
        unclipped = _median_ms([min(sp, EST_CAP_MS) for sp in prior])
        if unclipped is None or len(prior) >= PACE_BOUND_SAMPLES:
            continue
        eligible += 1
        try:
            clipped = int(fc.get("pace") or 0)
        except (TypeError, ValueError):
            continue
        if clipped and abs(clipped - unclipped) > 500:
            decided += 1
    rate = (decided / scored) if scored else 0.0
    return {
        "scored": scored,
        "eligible": eligible,
        "decided": decided,
        "spans_needed": (
            int(REFIT_MIN_DECIDED / rate) if decided >= 5 and rate > 0 else None
        ),
    }


def estimate_error(
    tasks: dict,
    now_ms: int | None = None,
    model: str | None = None,
    days: float | None = None,
) -> dict:
    """{source: {n, med, mean, worst}} — how far closed steps missed, by where the number came from.

    Factor error (`max(pred/actual, actual/pred)`), so a step that took twice its estimate
    and one that took half of it count the same. Records written before this build stamped
    an estimate carry none, so the report fills in as steps close.
    """
    now_ms = now_ms or int(time.time() * 1000)
    cutoff = now_ms - int((days if days is not None else HISTORY_MAX_AGE_DAYS) * 86400 * 1000)
    factors: dict[str, list[float]] = {}
    for key, rec in (tasks or {}).items():
        if not isinstance(rec, dict):
            continue
        started, stopped = rec.get("started_ms"), rec.get("done_ms")
        if not started or not stopped or stopped <= started or stopped < cutoff:
            continue
        if model and rec.get("model") and rec.get("model") != model:
            continue
        try:
            pred = int(rec.get("est_ms"))
        except (TypeError, ValueError):
            continue
        if pred <= 0:
            continue
        actual = stopped - started
        if actual < label_floor_ms():
            continue  # a list flip is not a step, and scoring it only adds noise
        factors.setdefault(str(rec.get("est_src") or "pace"), []).append(
            max(pred / actual, actual / pred)
        )
    report = {}
    for src, fs in factors.items():
        fs = sorted(fs)
        mid = len(fs) // 2
        med = fs[mid] if len(fs) % 2 else (fs[mid - 1] + fs[mid]) / 2
        report[src] = {
            "n": len(fs),
            "med": round(med, 2),
            "mean": round(sum(fs) / len(fs), 2),
            "worst": round(max(fs), 1),
        }
    return report


def estimate_spread_ms(
    times: dict,
    task: str,
    shapes: dict | None = None,
    bucket: str = "",
) -> tuple[int, int] | None:
    """The spread belonging to whichever source supplied this step's number."""
    entry = sized_entry(shapes, bucket)
    if entry:
        return entry_spread(entry)
    return None


# In plain words: how much is left — each remaining step at its usual duration, less the
# time the step in progress has already spent. So the number falls as work lands rather
# than sitting still until the next list.
def remaining_estimate_ms(
    times: dict,
    todos: list,
    now_ms: int,
    pace_ms: int,
    shapes: dict | None = None,
    calls_mem: dict | None = None,
) -> int:
    """Projected time still to run, at the pace (and history) learned from finished steps.

    The step being worked on is credited for the time it has already spent, so a step
    four minutes into a three-minute pace has nothing left to project: `EST REM` shrinks
    as work lands instead of sitting flat until the next write. Each waiting step is
    projected from its own remembered duration where there is one.
    """
    idx = current_index(todos)
    total = 0
    for i, t in enumerate(todos or []):
        if t.get("completed"):
            continue
        est = estimate_for(times, t.get("task", ""), pace_ms, shapes, calls_mem)
        if i == idx:
            elapsed = live_elapsed(
                (times or {}).get(str(t.get("task", ""))) or {}, now_ms
            )
            if elapsed is not None:
                est = max(0, est - elapsed)
        total += est
    return total


def elapsed_total_ms(times: dict, todos: list, now_ms: int) -> int | None:
    """What the list has already spent, from the first clock it ever started.

    A clock is only ever started for a step seen *unfinished*, so the earliest
    `started_ms` on the list is when the work began — not `turn_started`, which
    moves every time the owner says something, and not the store's mtime, which
    moves on every poll. None means no step was ever seen running, and a zero
    there would read as "this list took no time" (see NO_TIMES_TICKED).
    """
    starts = [
        rec.get("started_ms")
        for rec in ((times or {}).get(str(t.get("task", ""))) or {} for t in todos or [])
        if rec.get("started_ms")
    ]
    if not starts:
        return None
    return max(0, now_ms - min(starts))


def total_estimate_ms(
    times: dict,
    todos: list,
    now_ms: int,
    pace_ms: int,
    shapes: dict | None = None,
    calls_mem: dict | None = None,
) -> int | None:
    """The overall time to reach the goal: what the list has spent plus what is left.

    `EST REM` answers "how much longer", which is the operational question; this
    answers "how long is this whole thing", which is the one asked when deciding
    whether to let a long job run. It is a projection, not a promise: the spent
    half is measured and the remaining half is projected at the same pace, so it
    moves as work lands instead of sitting still. None when the list was never
    seen running, because there is no start to measure a total from.
    """
    spent = elapsed_total_ms(times, todos, now_ms)
    if spent is None:
        return None
    return spent + remaining_estimate_ms(times, todos, now_ms, pace_ms, shapes, calls_mem)


def run_variance_ms(times: dict, todos: list, now_ms: int, pace_ms: int) -> int | None:
    """How far a run of finished steps ran from the pace, or None with too little data.

    Three or more measured spans: with one or two the pace IS those steps, so the number
    would be zero by construction or pure noise.
    """
    spans = step_spans_ms(times, todos, now_ms)
    if len(spans) < 3:
        return None
    return sum(spans) - pace_ms * len(spans)


def fmt_estimate(ms: int) -> str:
    """`~3m` — the coarse inline projection."""
    return "~" + (short_duration(max(0, int(ms))) or "0s")


def fmt_variance(ms: int) -> str:
    """`-1m20s` / `+40s` — finished work set against the pace."""
    return ("-" if ms < 0 else "+") + (
        short_duration(abs(int(ms)), seconds=True) or "0s"
    )


def fmt_eta(rem_ms: int, now_ms: int) -> str:
    """The wall clock the list projects to finish at, `18:06`."""
    return time.strftime("%H:%M", time.localtime((now_ms + max(0, int(rem_ms))) / 1000))


def task_key(session, task: str) -> str:
    """A task record belongs to a (session, task) pair.

    Keyed by task text alone, a session flip — a thread switch, a watcher restart
    that resolved another thread — reset the whole log, and every step of the list
    you were watching then read as completed in one instant, with no duration at
    all. Keyed this way a flip is invisible: each session keeps its own clocks, and
    going back to one restores them instead of restarting them.
    """
    return f"{session or ''}\x1f{task}"


# In plain words: a record changes once in a while, and the log has thousands of them. An
# observation used to rewrite every record to change one; now it appends a line, and the
# JSON the readers use is a memo of those lines with the offset it was folded to. The
# events are absolute (last write wins per key), so folding them again is harmless — which
# is what makes the window between the append and the rewrite cost a re-read, not a step.
def _task_event(row: dict) -> bytes:
    """One event, one line — the stream's only writer."""
    return (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _events_size() -> int:
    try:
        return os.path.getsize(events_path())
    except OSError:
        return 0


def _stamp_events_cursor(log: dict) -> None:
    """Record how much of the stream this memo has folded.

    Sound because the append that just happened was this process's own and was the last
    thing written: the file's size now is the end of what the memo holds.
    """
    try:
        st = os.stat(events_path())
    except OSError:
        return
    log["events"] = {"dev": st.st_dev, "ino": st.st_ino, "off": st.st_size}


def append_task_events(rows: list) -> int:
    """Append events to the stream, one JSON object per line; the new size back.

    One `O_APPEND` write per call, so a reader that saw the bytes before this call still
    sees them after it, and a crash in the middle of a line leaves a prefix rather than a
    file with a hole in it.
    """
    if not rows:
        return _events_size()
    blob = b"".join(_task_event(r) for r in rows)
    try:
        fd = os.open(events_path(), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    except OSError:
        return 0
    try:
        view = memoryview(blob)
        while view:
            wrote = os.write(fd, view)
            if wrote <= 0:
                break
            view = view[wrote:]
        try:
            os.fsync(fd)
        except OSError:
            pass
    except OSError:
        pass
    finally:
        os.close(fd)
    return _events_size()


def fold_task_events(view: dict) -> dict:
    """Bring a memo of the task log up to date with the stream.

    A record is set by `k` and removed by `drop`, so the fold is last-write-wins per key
    and running it twice says the same thing as running it once. Starting from the
    offset the memo carries and starting from the beginning therefore agree, which is what
    makes the window between an append and the rewrite harmless: the next reader folds the
    events the memo missed. A torn last line (a crash mid-append) is left for the rest of
    itself to arrive rather than guessed at.
    """
    stream = events_path()
    try:
        st = os.stat(stream)
    except OSError:
        return view
    cur = view.get("events") if isinstance(view.get("events"), dict) else {}
    start = 0
    if (cur.get("dev") == st.st_dev and cur.get("ino") == st.st_ino
            and 0 <= int(cur.get("off") or 0) <= st.st_size):
        start = int(cur["off"])
    try:
        with open(stream, "rb") as fh:
            fh.seek(start)
            blob = fh.read()
    except OSError:
        return view
    cut = blob.rfind(b"\n")
    if cut < 0:
        return view
    tasks = view.setdefault("tasks", {})
    for line in blob[: cut + 1].splitlines():
        try:
            row = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        if "k" in row and isinstance(row.get("r"), dict):
            tasks[row["k"]] = row["r"]
        elif isinstance(row.get("drop"), list):
            for key in row["drop"]:
                tasks.pop(key, None)
        if "session" in row:
            view["session"] = row["session"]
        if "pruned_ms" in row:
            view["pruned_ms"] = row["pruned_ms"]
        if row.get("v"):
            view["schema"] = max(int(view.get("schema") or 0), int(row["v"]))
    view["events"] = {"dev": st.st_dev, "ino": st.st_ino, "off": start + cut + 1}
    return view


def compact_task_events(log: dict) -> int:
    """Rewrite the stream as one event per record the log still holds.

    A stream appended to for a month is mostly events about records the prune has since
    dropped: this is the fold point, run by the prune and only by it. Written to a temp
    name and renamed, because a reader mid-fold must see the old stream or the new one.
    """
    rows = [{"v": TASKLOG_SCHEMA, "session": log.get("session")}]
    for key, rec in (log.get("tasks") or {}).items():
        rows.append({"v": TASKLOG_SCHEMA, "k": key, "r": rec})
    if log.get("pruned_ms"):
        rows.append({"v": TASKLOG_SCHEMA, "pruned_ms": log["pruned_ms"]})
    blob = b"".join(_task_event(r) for r in rows)
    stream = events_path()
    d = os.path.dirname(stream)
    os.makedirs(d, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".fbtodo.", suffix=".tmp", dir=d)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(blob)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, stream)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    st = os.stat(stream)
    log["events"] = {"dev": st.st_dev, "ino": st.st_ino, "off": len(blob)}
    return len(blob)


def load_tasklog() -> dict:
    log = read_json(TASKS_PATH, None)
    if not isinstance(log, dict) or "tasks" not in log:
        log = {"schema": TASKLOG_SCHEMA, "session": None, "tasks": {}}
    if log.get("schema", 1) < TASKLOG_SCHEMA and log.get("tasks"):
        # flat task-text keys from before the (session, task) change: re-key them
        # under the session they were recorded for, so an upgrade does not throw
        # away the clocks of the list being watched at the time
        session = log.get("session")
        log["tasks"] = {task_key(session, k): v for k, v in log["tasks"].items()}
        log["schema"] = TASKLOG_SCHEMA
    return fold_task_events(log)


def current_index(todos: list) -> int | None:
    """The step being worked on: the first one not ticked off.

    The list is sequential, which is what makes one clock per task meaningful — a
    task is running only while it is this one.
    """
    for i, t in enumerate(todos or []):
        if not t.get("completed"):
            return i
    return None


# In plain words: a number written down is not the same as work happening. A step with a
# recorded start and no recorded finish is, as far as this record knows, still running —
# and that is all this answers. Whether it is running *now* is a different question; the
# next function asks it.
def live_clock(rec: dict | None) -> bool:
    """Is this step's clock counting, as the record stands?

    A number in the record is not the same as work in progress. A span that has been
    closed — by the step being ticked, or by the turn ending with it still current — is a
    *measurement*, and reading it as "working" is what left a finished turn counting up in
    the pane for as long as the list sat there: the strip said WORKING with a number that
    grew past the estimate, and nothing was being worked on at all.

    This is the record's own answer, so it is deliberately blind to the turn: a file the
    watcher has not rewritten yet (a stale state, or a stopped watcher) still says
    "counting". That is why the callers also ask the state — see `step_is_running`.
    """
    if not rec:
        return False
    return rec.get("started_ms") is not None and rec.get("done_ms") is None


# In plain words: the pane's real question — is anything being worked on at this moment?
# Two sources must agree. The record says a clock is counting; the transcript says whether
# the agent has finished its turn. Once the turn has ended nothing is in progress, whatever
# a number left behind by the last write may suggest.
def step_is_running(state: dict, rec: dict | None) -> bool:
    """The question the pane actually asks: is a step in progress *right now*?

    Two things have to agree, and the state is the one that settles it. The record says a
    clock is counting; the journal says whether the agent has finished the turn. When the
    turn has ended, nothing in the list is in progress — and since the pane is a separate
    process reading a file, a record the watcher has not yet closed must not be able to
    keep saying WORKING on its own.
    """
    if state.get("turn_ended"):
        return False
    return live_clock(rec)


# In plain words: the clock is worked out from the recorded start and the current time,
# rather than copied from the last update, so the number keeps moving between the watcher's
# writes instead of standing still and then jumping.
def live_elapsed(rec: dict, now_ms: int) -> int | None:
    """The clock as of *now*, not as of the watcher's last write.

    The state file carries `elapsed_ms` from the poll that produced it, so the pane
    re-derives the running step's number from its recorded start and lets its own
    tick move it. None means "no clock": the task was never seen running, and a
    zero there would read as "this took no time".
    """
    started = rec.get("started_ms")
    if not started:
        return rec.get("elapsed_ms")
    return max(0, (rec.get("done_ms") or now_ms) - started)


def has_running_clock(state: dict, now_ms: int | None = None) -> bool:
    """True while a step's clock is counting up.

    A counting number is the difference between "alive and working on step 4" and
    "stuck"; the pane redraws on a 1s tick while this is true (instead of the 5s
    idle tick) so it visibly moves rather than jumping in 5-second steps. The pane
    sleeps in short wakes, not until its next poll, so this holds for the NAS pane
    too — where a poll is an ssh round trip.
    """
    todos = state.get("todos") or []
    idx = current_index(todos)
    if idx is None:
        return False
    rec = (state.get("task_times") or {}).get(str(todos[idx].get("task", ""))) or {}
    return step_is_running(state, rec)


def track_tasks(state: dict, now_ms: int | None = None, persist: bool = True) -> dict:
    """Per-task elapsed time, measured by observation, counted one step at a time.

    Freebuff stores no per-task timestamps — a snapshot is text plus a completed
    flag — so a clock is built from what the watcher sees:

    * it starts when the task becomes the step being worked on, not when the list
      is written: otherwise every task bills the time spent on the ones before it,
      and the last step of a six-step list shows the whole turn's duration;
    * it stops the first time that task is seen completed, and keeps that number;
    * it stops when the TURN ends with the step still current — the work is over, so a
      clock left running bills the gap and the pane counts a step nobody is on. The span
      so far is kept, and the clock restarts if the agent comes back to that step;
    * a task never seen running (already ticked off at first sight, or fbtodo
      attached late) gets no number at all rather than a zero;
    * a re-opened task restarts, since it is being worked on again.

    Elapsed time is a floor: a transition is timestamped by the poll that noticed
    it, so a step is over-counted by at most one polling interval. Reopening a
    completed task restarts its clock. Records are kept per session, so a thread
    flip or a list that momentarily reads as empty cannot wipe them.
    """
    now_ms = now_ms or int(time.time() * 1000)
    log = load_tasklog()
    session = state.get("session")
    prior_session = log.get("session")
    log["session"] = session
    tasks = log.setdefault("tasks", {})
    todos = state.get("todos") or []
    # What this poll may touch, as text, before anything is mutated: the records of the
    # steps on this list. The write below then appends the events that differ instead of
    # rewriting the records that do not, which is most of them — the log is a history and a
    # list is a handful of steps.
    touched = [task_key(session, str(t.get("task", ""))) for t in todos]
    before_recs = {key: json.dumps(tasks.get(key), sort_keys=True) for key in touched}
    running = current_index(todos)
    # The agent's own answer to "is anything being worked on right now", from the
    # journal's end-of-turn record. Everything below hangs off it: a turn that has ended
    # is waiting for you, so no step of it is in progress — and the strip's WORKING/IDLE
    # question has to be answered the same way, or it says the opposite of the truth.
    ended = bool(state.get("turn_ended"))
    times: dict[str, dict] = {}
    seen: set[str] = set()
    prefix = task_key(session, "")
    # The calls this TURN has made, and which of its steps they belong to (see below).
    turn = state.get("turn") or {}
    turn_ms = int(turn.get("start_ms") or 0)
    turn_verbs = dict(turn.get("verbs") or {})

    for i, t in enumerate(todos):
        label = str(t.get("task", ""))
        key = task_key(session, label)
        seen.add(key)
        rec = tasks.get(key)
        if not isinstance(rec, dict):
            rec = {"started_ms": None, "done_ms": None}
            tasks[key] = rec
        # The model the step was worked on by, so its span can be compared with like
        # spans later (see task_history_from_log). Stamped on every sighting, so a
        # record written before this field existed picks it up on the next poll.
        if state.get("model"):
            rec["model"] = state["model"]
        # Which LIST this step was last seen in. The pace a step was given came from the
        # steps finished before it IN ITS OWN LIST, so that is what the clip's own readiness
        # count has to group by — a session can hold many lists, and grouping by session
        # would count steps from earlier turns as if this step had inherited their pace.
        if state.get("list_version") is not None:
            rec["lv"] = state["list_version"]
        # What this step has DONE, as opposed to what it was called: the calls this turn
        # has made, less what the steps BEFORE it in the same turn were credited with.
        # Order decides who did what — no timestamps needed, and no call is counted twice.
        # Only the step in flight is credited: a waiting step has made no calls, and a
        # finished one keeps the shape it earned. Nothing is credited once the turn ends,
        # so the last step's signature is whatever it had when the work stopped.
        if i == running and not ended and turn_verbs and turn_ms:
            credited: dict[str, int] = {}
            for other, other_rec in tasks.items():
                if other == key or not isinstance(other_rec, dict):
                    continue
                if not (other.startswith(prefix) and prefix):
                    continue
                if int(other_rec.get("turn_ms") or 0) != turn_ms:
                    continue
                for verb, n in (other_rec.get("shape") or {}).items():
                    credited[verb] = credited.get(verb, 0) + int(n or 0)
            left = {v: n - credited.get(v, 0) for v, n in turn_verbs.items()}
            left = {v: n for v, n in left.items() if n > 0}
            if left:
                rec["shape"] = left
            rec["turn_ms"] = turn_ms
        if t.get("completed"):
            if rec.get("started_ms"):
                rec["done_ms"] = rec.get("done_ms") or now_ms
            else:
                rec["done_ms"] = None  # never seen running: no duration to show
        elif rec.get("done_ms") and not ended:
            rec["started_ms"] = now_ms  # re-opened: the clock restarts
            rec["done_ms"] = None
        if not t.get("completed"):
            if i != running:
                # only the step being worked on has a running clock; one that is waiting
                # its turn (or was left behind because an earlier step was unticked) has
                # none, so its number can never include someone else's time
                rec["started_ms"] = None
            elif ended:
                # The turn ended with this step still current: close the clock ONCE and
                # keep the span. Gated on `ended` rather than stamped every pass, or the
                # next poll would re-open it (the re-opened branch above) and the pair
                # would flap, and the number would grow again by exactly the idle gap it
                # had just been stopped for.
                if rec.get("started_ms") and not rec.get("done_ms"):
                    rec["done_ms"] = now_ms
            else:
                rec["started_ms"] = rec.get("started_ms") or now_ms
        started = rec.get("started_ms")
        times[label] = {
            "started_ms": started,
            "done_ms": rec.get("done_ms") if started else None,
            "elapsed_ms": None
            if not started
            else (rec.get("done_ms") or now_ms) - started,
            # carried out to the renderers so a running step can be projected from the kind
            # of work it is visibly doing, without a second pass over the task log
            "shape": rec.get("shape") or {},
        }

    # Only this session's tasks, and never on a snapshot that reads as empty: a momentary
    # no-list poll means "nothing to look at", not "those tasks are gone".
    # FINISHED records are then deliberately spared. A record with a span is evidence — it is
    # what `estimate_error` and `forecast_error` score, and what the pace, size and call
    # memories learn from — and this session's key is (session, label), so a step that was
    # reworded becomes another sample rather than a duplicate. Dropping them made the two
    # score lines self-erasing: measured 2026-09-29, a rewritten list deleted the very step
    # whose forecast was the only scored row in the log, so `fbtodo status` could never show
    # more than the current list and "is this getting better?" was unanswerable live. What is
    # still dropped is a record that never finished (no `done_ms`): its `started_ms` would
    # keep a clock running for a step that is no longer on any list.
    dropped: list = []
    if seen:
        prefix = task_key(session, "")
        dropped = [
            k
            for k, rec in tasks.items()
            if k.startswith(prefix)
            and k not in seen
            and not (isinstance(rec, dict) and rec.get("done_ms"))
        ]
        for gone in dropped:
            tasks.pop(gone, None)

    # Per-task memory, kept ACROSS sessions: every finished step in the log contributes its
    # measured span under its own text, so a later list projects a step from what it took
    # last time instead of from the average of an unrelated list. Age-gated and capped
    # inside, so a stale wording falls out rather than skewing today's projections.
    state["task_history"] = task_history_from_log(tasks, now_ms, state.get("model"))
    # ...and the same memory keyed by what each step DID. This is the half that can improve
    # with use: wordings never repeat (172 of 173 seen once) while shapes do, so samples
    # accumulate in a bucket instead of scattering one per step name.
    state["task_shapes"] = shape_history_from_log(tasks, now_ms, state.get("model"))
    # ...and the one memory a WAITING step can use, since it has no calls to size it by: the
    # calls a step of its KIND makes, and the seconds each of those calls costs (see
    # call_memory_from_log). This is what prices a pending row, blended with the pace.
    state["task_calls"] = call_memory_from_log(tasks, now_ms, state.get("model"))
    # ...and how close the log is to being able to re-choose these constants, so the pane's
    # REFIT row and `fbtodo status` report the same count from the same function.
    state["refit"] = refit_readiness(tasks, now_ms, state.get("model"))
    # What the pane is projecting for the step in flight at this moment, stamped onto its
    # record. The moment that step closes, its estimate and its actual span are a matched
    # pair, and `estimate_error` reads the pair straight back out of the log — which is the
    # only way "are these numbers getting better?" can be answered with evidence rather than
    # with faith. Written before the persist below, so the stamp is what lands on disk.
    idx = current_index(todos)
    if idx is not None and not ended:
        label = str(todos[idx].get("task", ""))
        rec = tasks.get(task_key(session, label))
        if isinstance(rec, dict) and rec.get("started_ms"):
            pace = step_pace_ms(times, todos, now_ms, state["task_history"])
            bucket = step_shape_bucket(times, label)
            value, src = pick_estimate(
                label,
                state["task_shapes"],
                bucket,
                pace,
                state.get("task_calls"),
            )
            rec["est_ms"] = value or pace
            rec["est_src"] = src
            # The forecast LEDGER, written once, on the first poll that sees this step
            # running — the moment at which nothing about its size is known yet. `est_ms`
            # above is restamped every poll and is therefore scored with the answer partly
            # in hand (the shape rung's key is the calls the step has ALREADY made by the
            # time it closes), so it cannot say whether the shape rung predicts or merely
            # recognises. This vector can: every rung is recorded as it stood before the
            # work started, and `forecast_error` scores them all on every step.
            if "fc" not in rec:
                fc = {"at": now_ms, "v": VERSION, "model": state.get("model") or ""}
                late = now_ms - int(rec.get("started_ms") or now_ms)
                if late > LEDGER_FRESH_MS:
                    fc["late"] = late  # seen first at this many ms in — not a forecast
                entry = sized_entry(state["task_shapes"], bucket)
                if entry:
                    seen = hist_med(entry)
                    if seen:
                        fc["shape"] = seen
                blended = pending_blend_ms(label, pace, state.get("task_calls"))
                if blended:
                    fc["blend"] = blended
                # ...and the shadow rung, scored beside them and never returned by any rung
                # chooser: this stamp is the whole of its contract with the pane.
                shadow = recent_pace_ms(times, todos, now_ms)
                if shadow:
                    fc["recent"] = shadow
                fc["pace"] = pace
                fc["pick"] = src
                rec["fc"] = fc
    if persist:
        rows = []
        if log.get("session") != prior_session:
            rows.append({"v": TASKLOG_SCHEMA, "session": log.get("session")})
        for key in touched:
            if json.dumps(tasks.get(key), sort_keys=True) != before_recs.get(key):
                rows.append({"v": TASKLOG_SCHEMA, "k": key, "r": tasks.get(key)})
        if dropped:
            rows.append({"v": TASKLOG_SCHEMA, "drop": sorted(dropped)})
        if rows:
            try:
                append_task_events(rows)
            except OSError:
                pass
            # The memo carries the offset it was folded to. Stamped after the append and
            # never before it: an event that landed without a memo is folded again next
            # time, which costs a read, where the other order would lose the record.
            _stamp_events_cursor(log)
            try:
                atomic_write_json(TASKS_PATH, log)
            except OSError:
                pass
            prune_scratch(now_ms=now_ms)  # throttled internally; keeps the record count capped
    state["task_times"] = times
    return state


__all__ = [
    "scratch_size", "prune_scratch", "TASKLOG_SCHEMA", "short_duration", "DEFAULT_PACE_MS",
    "PACE_BOUND_FACTOR", "PACE_BOUND_SAMPLES", "STUCK_FACTOR", "EST_CAP_MS", "LIST_BEHIND_MS",
    "MIN_TASK_TEXT", "step_spans_ms", "_median_ms", "task_history_from_log",
    "shape_history_from_log", "_hist_entry", "_history_gather", "shape_of",
    "step_shape_bucket", "bucket_ordinal", "sized_entry", "CLASS_RULES", "CLASS_WORDS",
    "STOP_WORDS", "label_class", "call_memory_from_log", "pending_blend_ms", "hist_med",
    "step_pace_ms", "estimate_for", "pick_estimate", "task_estimate_ms", "_spread_ms",
    "fmt_range", "is_wide", "fmt_estimate_spread", "pace_spread_ms", "entry_spread",
    "forecast_error", "rung_duel", "DUEL_RESAMPLES", "DUEL_RESOLVED", "rank_quantile",
    "SPREAD_LO_PCT", "SPREAD_HI_PCT", "DUEL_MIN_SESSIONS", "duel_note", "fmt_rung",
    "SHIPPED_RUNGS", "SHADOW_RUNGS", "SCORED_RUNGS", "SHADOW_SAMPLES", "recent_pace_ms",
    "refit_readiness",
    "estimate_error", "estimate_spread_ms",
    "remaining_estimate_ms", "elapsed_total_ms", "total_estimate_ms", "run_variance_ms",
    "fmt_estimate", "fmt_variance", "fmt_eta", "task_key", "_task_event", "_events_size",
    "_stamp_events_cursor", "append_task_events", "fold_task_events", "compact_task_events",
    "load_tasklog", "current_index", "live_clock", "step_is_running", "live_elapsed",
    "has_running_clock", "track_tasks",
]
