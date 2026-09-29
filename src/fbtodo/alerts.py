"""The two lines no store knows: what the last patch pass did, and who the phone told.

Both are read out of the log the producing step already writes, so neither costs a probe.
"""

from __future__ import annotations

import calendar
import os
import re
import subprocess
import time
from .base import *  # noqa: F401,F403 (the package is one namespace)

# ------------------------------------------- the patches themselves, and the alerts
# The pane says two things about the CLI patches that no store knows: what the last
# patch pass DID, and when the phone was last told something. Both are already in a log,
# so the reader is a tail plus one classification.
# A log line's own stamp: the Mac watcher writes local time (`2026-09-27 12:44:09`), the
# NAS hook writes UTC (`2026-09-27T19:53:48Z`), and the separator is what tells them apart.
_STAMP_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})")


# The local watcher's own words -> an outcome. `patched` and `FAILED` close a pass;
# `binary changed` opens one, which is why it is not read as a failure. A line matching
# none of these is not an outcome at all (a lock takeover, a `reanchor:` note) and is
# skipped rather than guessed at — those can be the newest line while a pass is still
# running, and reading one as the outcome would report a failure as a success.
LOCAL_PATCH_WORDS = (
    ("patched", "ok"),
    ("FAILED", "failed"),
    ("binary changed", "pending"),
    ("converge reported", "pending"),
)


# The NAS hook writes one line per pass — `…Z converge=ok patch=ok binary=0.1.1 [size …]` —
# with anything wrong on the indented lines under it.
NAS_PATCH_RE = re.compile(r"patch=(\w+)\s+binary=(\S+)")


# An alert's own words -> the colour it gets. Everything the kit sends is a fact; `muted`
# means the phone was deliberately NOT told, which is worth an amber row.
ALERT_KIND_SEVERITY = {"sent": "ok", "resolved": "ok", "duplicate": "ok", "muted": "warn"}


PATCH_SEVERITY = {"ok": "ok", "pending": "warn", "incomplete": "bad", "failed": "bad"}


def entry_ms(text: str, utc: bool = False) -> int | None:
    """The epoch-ms a log entry starts with, or None when it starts with no stamp.

    A continuation line (the NAS hook indents its complaints) has no stamp, which is how
    the reader tells one entry from the next.

    Two clocks write the space-separated form: this Mac's kit logs LOCAL time and the NAS
    container logs UTC (`TZ` unset over there), so the SOURCE has to say which — `utc` is
    passed by the NAS readers. The `T…Z` form is unambiguous and decides itself. Guessing
    from the format alone made a 28-minute-old NAS alert read as `0s ago`: its UTC stamp
    was a future time on a PDT clock, and the age was clamped to zero.
    """
    m = _STAMP_RE.match(text or "")
    if not m:
        return None
    y, mo, d, h, mi, s = (int(g) for g in m.groups())
    secs = (
        calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0))
        if utc or (text or "")[10:11] == "T"
        else time.mktime((y, mo, d, h, mi, s, 0, 0, -1))
    )
    return int(secs * 1000)


def read_tail(path: str, limit: int = PATCH_TAIL_BYTES) -> list[str]:
    """The last few non-empty lines of a file, read from its TAIL.

    Both logs are append-only and grow without bound, and this is asked on every poll of
    the pane it feeds, so the file is seeked rather than read.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - limit))
            blob = fh.read()
    except OSError:
        return []
    lines = blob.decode("utf-8", "replace").splitlines()
    if size > limit and lines:
        lines = lines[1:]  # the seek landed mid-line: that first fragment is not an entry
    return [ln.rstrip() for ln in lines if ln.strip()]


def _age_s(at_ms: int | None) -> int | None:
    return None if not at_ms else max(0, int(time.time() - at_ms / 1000.0))


def _tidy(text: str, limit: int = PATCH_REASON_MAX) -> str:
    """One entry's payload, collapsed to one line and one row's worth of columns."""
    return _clip(" ".join(str(text or "").split()), limit)


# In plain words: the PATCH and ALERT rows are borrowed from two log files that other
# parts of this setup write to. Rather than watch them, this reads their last few lines
# whenever the pane is drawn and reports the newest line it understands.
def local_patch_alert(patch_log: str | None = None, alert_log: str | None = None) -> dict:
    """The last patch outcome and the last alert, from this Mac's own logs.

    Both paths are read from the module constants at CALL time, so a fixture (the
    self-check) can point them somewhere else without a subprocess.
    """
    patch_log = patch_log or PATCH_LOG
    alert_log = alert_log or ALERT_LOG
    lines = read_tail(patch_log)
    patch = None
    for line in reversed(lines):
        m = _STAMP_RE.match(line)
        if not m:
            continue
        body = line[m.end():].strip()
        head, _dash, tail = body.partition(" — ")
        word = head.strip()
        outcome = next((o for needle, o in LOCAL_PATCH_WORDS if word.startswith(needle)), None)
        if not outcome:
            continue
        at = entry_ms(line)
        # `(86192720:1790538247:388406469)` is the binary's identity, which the row does not
        # need (the version is shown instead) and which would spend its whole width.
        reason = re.sub(r"\s*\([^()]*\)\s*$", "", (tail or word).strip())
        patch = {
            "outcome": outcome,
            "severity": PATCH_SEVERITY.get(outcome, "warn"),
            "reason": _tidy(reason),
            "version": read_json(PATCH_META, {}).get("version"),
            "at_ms": at,
            "age_s": _age_s(at),
            "source": patch_log,
        }
        break
    return {
        "patch": patch,
        "alert": alert_from_lines(read_tail(alert_log), alert_log),
    }


def alert_from_lines(lines: list[str], source: str, utc: bool = False) -> dict | None:
    """The newest alert line, in either host's format, as {kind, text, at_ms, severity}.

    `source` and `utc` are passed in rather than read from the constants because the NAS
    answer arrives inside the probe: its path is the honest one to report, and its clock
    is the container's (UTC), not this Mac's.
    """
    for line in reversed(lines):
        m = _STAMP_RE.match(line)
        if not m:
            continue
        body = re.sub(r"^phone:\s*", "", line[m.end():].strip())
        kind = (body.split(" ", 1)[0] or "").lower()
        if kind not in ("sent", "muted", "duplicate", "resolved"):
            continue
        at = entry_ms(line, utc)
        # ` — http 200 {"id":…}` is the transport's own reply: not what this row is about,
        # and long. The digest in `(2f14bfcf14a4d794)` is the dedupe key, not news either.
        text = re.split(r"\s+—\s+http\s", body)[0]
        text = re.sub(r"\s*\([0-9a-f]{8,}\)", "", text).rstrip(" —-\t").strip()
        return {
            "kind": kind,
            "severity": ALERT_KIND_SEVERITY.get(kind, "warn"),
            "text": _tidy(text),
            "at_ms": at,
            "age_s": _age_s(at),
            "source": source,
        }
    return None


def nas_patch_from_tail(blob: str) -> dict | None:
    """The NAS patch tail, as it came back in the probe: entries split on the \\x1c the
    far side joins them with.

    The outcome entry is the last one that starts with a stamp; the indented lines under
    it are its own account of what went wrong, which is the text worth showing when the
    news is bad. Parsed here rather than over there because BusyBox is a poor place to
    classify anything.
    """
    lines = (blob or "").split("\x1c")
    idx = None
    for i, line in enumerate(lines):
        if entry_ms(line, True) is not None and NAS_PATCH_RE.search(line):
            idx = i
    if idx is None:
        return None
    m = NAS_PATCH_RE.search(lines[idx])
    outcome = (m.group(1) or "").lower()
    # `freebuff: local CLI patches incomplete (no window for …); the rest were applied` is
    # the hook's own wording: the program's tag and the outcome word are what the row
    # already says, and the parenthetical IS the reason, so all three are taken off.
    reasons = [
        _tidy(re.sub(
            r"^\((.*?)\)",
            r"\1",
            re.sub(r"^(incomplete|failed|ok)\s+", "",
                   re.sub(r"^\s*local CLI patches\s+", "", re.sub(r"^\s*freebuff:\s*", "", ln))),
        ))
        for ln in lines[idx + 1:] if ln.strip() and entry_ms(ln, True) is None
    ]
    at = entry_ms(lines[idx], True)
    return {
        "outcome": outcome,
        "severity": PATCH_SEVERITY.get(outcome, "warn"),
        "reason": reasons[0] if reasons else "",
        "version": m.group(2),
        "at_ms": at,
        "age_s": _age_s(at),
        "source": NAS_PATCH_LOG,
    }


def patch_row_text(pairs: list) -> str:
    """One row's plain text, `PATCH  …   ALERT  …`, without the leading indent."""
    return "   ".join(f"{label}  {text}" for label, text, _sev in pairs)


def patch_detail_forms(state: dict) -> tuple:
    """The two facts in every width they can be written: (patch forms, alert forms).

    Patch, widest first: `outcome · version · age · reason` (the reason only when the news
    is bad — `patched — the collapse and the session-end reason are in place` says nothing
    the `ok` beside it does not), then the same without the reason, then without the
    version. Alert: its own words with the age, then just the kind with the age, then the
    kind alone.
    """
    patch = state.get("patch") or {}
    alert = state.get("alert") or {}
    patch_forms, alert_forms = [], []
    if patch.get("outcome"):
        bits = [str(patch["outcome"])]
        if patch.get("version"):
            bits.append(str(patch["version"]))
        if patch.get("at_ms"):
            bits.append(fmt_age(patch["at_ms"]))
        seen = list(bits)
        if patch.get("outcome") != "ok" and patch.get("reason"):
            bits.append(_tidy(str(patch["reason"])))
        patch_forms = [" · ".join(bits), " · ".join(seen)]
        tight = [str(patch["outcome"])]
        if patch.get("at_ms"):
            tight.append(fmt_age(patch["at_ms"]))
        # ...and the tightest form the row can fall back to: the outcome and how long ago.
        patch_forms.append(" · ".join(tight))
    if alert.get("kind"):
        text = str(alert.get("text") or alert["kind"])
        kind = str(alert["kind"])
        age = fmt_age(alert["at_ms"]) if alert.get("at_ms") else ""
        alert_forms = [" · ".join(b for b in (text, age) if b),
                       " · ".join(b for b in (kind, age) if b),
                       kind]
    # `patch`/`alert` are the severities for colouring; kept beside the forms so a caller
    # does not have to re-read the state.
    return (patch_forms, alert_forms, patch.get("severity") or "warn",
            alert.get("severity") or "warn")


def _shrink(pair: tuple, width: int) -> list:
    """One fact, made to fit `width`: first its tail goes, then it is clipped."""
    label, text, sev = pair
    bits = text.split(" · ")
    while len(bits) > 3 and _cell_width(
        "  " + patch_row_text([(label, " · ".join(bits), sev)])
    ) > width:
        bits.pop()
    room = max(4, width - _cell_width(f"  {label}  "))
    return [(label, _clip(" · ".join(bits), room), sev)]


def refit_row(state: dict, width: int | None = None) -> list:
    """The refit progress as one row of [(label, text, severity)] — or nothing at all.

    Nothing until the log carries `REFIT_MIN_SCORED` closed steps: below that a "progress"
    row would be counting towards a number nobody can read yet, and every row of chrome is a
    step the list loses. Once it shows, it counts the steps the young-list clip actually
    DECIDED — the only ones that can judge the clip, since on most steps the two configs write
    the same number — against what the 2026-09-29 refit says it takes (see `REFIT_MIN_DECIDED`).
    `ok` throughout: this is the pane's quiet chrome, and the words say whether it is ready.
    """
    rr = state.get("refit") or {}
    scored = int(rr.get("scored") or 0)
    if scored < REFIT_MIN_SCORED:
        return []
    decided = int(rr.get("decided") or 0)
    if decided >= REFIT_MIN_DECIDED:
        text = f"ready · {decided} decided over {scored} scored — the clip can be judged"
    else:
        pct = int(round(100.0 * decided / REFIT_MIN_DECIDED))
        text = f"{decided}/{REFIT_MIN_DECIDED} decided · {scored} scored ({pct}% to judging the clip)"
    room = None if width is None else max(8, width - _cell_width("  REFIT  "))
    return [("REFIT", text if room is None else _clip(text, room), "ok")]


def patch_row(state: dict, width: int | None = None) -> list:
    """The two facts as ONE row of [(label, text, severity)], laddered to `width`.

    One row and never two: the pane's height is fixed, so every row of chrome is a step the
    list loses. The ladder therefore trades DETAIL — the alert's own words first, then the
    patch's version — rather than spending another row on the second fact. Empty when the
    state carries neither fact, which is what lets a state written by a build that did not
    read them (or a fixture) render exactly as it always did.
    """
    patch_forms, alert_forms, psev, asev = patch_detail_forms(state)
    if not patch_forms:
        return []
    # Every pair form first, and the patch alone only after all of them: detail is traded
    # for detail (the alert's words, then the version), so the alert is given up only when
    # no form carrying both facts fits at all.
    cands: list = [
        [("PATCH", p, psev), ("ALERT", a, asev)] for p in patch_forms for a in alert_forms
    ]
    cands += [[("PATCH", p, psev)] for p in patch_forms]
    if width is None:
        return cands[0]
    for cand in cands:
        if _cell_width("  " + patch_row_text(cand)) <= width:
            return cand
    return _shrink(("PATCH", patch_forms[-1], psev), width)


__all__ = [
    "_STAMP_RE", "LOCAL_PATCH_WORDS", "NAS_PATCH_RE", "ALERT_KIND_SEVERITY", "PATCH_SEVERITY",
    "entry_ms", "read_tail", "_age_s", "_tidy", "local_patch_alert", "alert_from_lines",
    "nas_patch_from_tail", "patch_row_text", "patch_detail_forms", "_shrink", "refit_row",
    "patch_row",
]
