"""The pane's key for the notifications it cannot hear.

In plain words: the pane is a picture of a list, and the phone is somewhere else in the
house. Muting it today means knowing a file's name, a variable's name and which of six
words it takes; this module is the one place that knows, so a keypress inside the pane
writes the same words the notify kit's own scripts read, and a second keypress puts back
exactly what was there before.

The contract, read from the kit rather than guessed at (`~/.config/freebuff-notify`):
`phone.sh` answers to `FREEBUFF_PHONE`, else the one-word file `phone-state`; `bell.sh`
answers to `FREEBUFF_BELL`, else the one-word file `state`; and in both scripts the same
six words mean off (`off 0 false no disable disabled`, casefolded) and anything else means
on. No new switch, no new argument: writing those two files IS the mute, so a pane that
muted and a shell that muted are the same state, and the two cannot disagree about it.
"""

from __future__ import annotations

import os
import sys
import time

from .base import *  # noqa: F401,F403 — the package is one namespace


# The six words both scripts read as "off" (phone.sh L84-86, bell.sh L46-49). Mirrored here
# rather than invented, because the pane's job includes saying what those scripts WILL do —
# a chip that claims "muted" over an env var that outranks the file would be a lie.
MUTE_OFF_WORDS = ("off", "0", "false", "no", "disable", "disabled")

# The switches, in the order they are reported: `(file, environment, script, what it is)`.
MUTE_SWITCHES = (
    ("phone-state", "FREEBUFF_PHONE", "phone.sh", "the ntfy push"),
    ("state", "FREEBUFF_BELL", "bell.sh", "the chime"),
)

# The keys. `m` is the default this feature is for — quiet while you read this, until the
# list finishes — because a mute that needs a second visit to undo is not a mute. `M` is the
# same switch with no automatic end, for a read that outlasts the list. `u` gives the
# notifications back. Anything else the pane reads is not a switch key and does nothing.
PANE_MUTE_KEYS = {
    "m": ("mute", "list"),
    "M": ("mute", "sticky"),
    "u": ("unmute", ""),
}

# What the chip says while each kind of mute is in force. The undo key is IN the chip: a
# switch nobody can find is a switch nobody trusts, and the chip is the only place a pane
# can say one exists.
MUTE_CHIPS = {
    "list": "quiet until done · u",
    "sticky": "muted · u",
    "elsewhere": "notifications off · m",
    "env": "notifications off by env · u",
}


# ================================================================== the switches
def notify_dir() -> str:
    """Where the notify kit lives — `NOTIFY_DIR`, unless a test or a second kit names it."""
    return NOTIFY_DIR


def switch_path(name: str) -> str:
    """The file one switch word lives in."""
    return os.path.join(notify_dir(), name)


def read_switch(name: str) -> str | None:
    """The switch file's contents, stripped, or None when there is no file to read.

    None and "" are different answers on purpose: no file is the kit's own default (loud),
    while a file holding nothing is a writer caught mid-sentence — which both scripts also
    read as loud, but which the restore has to be able to tell apart, because only one of
    the two is a file this program created.
    """
    try:
        with open(switch_path(name), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def switch_is_off(name: str, env_var: str | None = None) -> bool:
    """Is this switch off, as the script that reads it would say?

    Environment first, then the file — the same order both scripts use, and the reason a
    `FREEBUFF_PHONE=off` in a wrapper cannot be lifted by a file: the chip has to name the
    thing that is actually holding the mute, or pressing `u` would look broken.
    """
    value = os.environ.get(env_var, "") if env_var else ""
    if not value:
        value = read_switch(name) or ""
    return str(value).strip().casefold() in MUTE_OFF_WORDS


def switch_held_by_env(name: str, env_var: str | None = None) -> bool:
    """Is the ENVIRONMENT holding this switch off, rather than the file?"""
    if not env_var:
        return False
    return str(os.environ.get(env_var, "")).strip().casefold() in MUTE_OFF_WORDS


def muted_now() -> bool:
    """Is either switch off right now — by this pane, by a wrapper, or by hand?"""
    return any(switch_is_off(name, env) for name, env, _script, _what in MUTE_SWITCHES)


# ===================================================================== the record
def read_mute_record() -> dict:
    """What the last mute keypress did: `{until, was, at_ms, session, list_id}`, or `{}`.

    The pane re-execs itself into every build that appears under it (`BUILD_CHECK_S`), so
    this memory has to outlive the process that took it — see `MUTE_PATH`. A record that is
    not a dict (a truncated write by something else, an older layout) is no record.
    """
    rec = read_json(MUTE_PATH, {})
    return rec if isinstance(rec, dict) else {}


def write_mute_record(rec: dict) -> None:
    atomic_write_json(MUTE_PATH, rec)


def clear_mute_record() -> None:
    try:
        os.unlink(MUTE_PATH)
    except OSError:
        pass


# ==================================================================== the chip
def mute_chip() -> str | None:
    """What the pane's title chip says about the notifications, or nothing at all.

    The chip is the pane's own slot for facts about the PROCESS rather than the list, and a
    mute is exactly that: the list on screen is unchanged by it, and the reader has to be
    able to tell whether the phone will ring. Scope comes from the record (which key was
    pressed, and so whether the mute ends by itself), and the "off" itself from the switches
    — so a mute set by a wrapper shows up here too, labelled as not ours.
    """
    rec = read_mute_record()
    scope = rec.get("until")
    if muted_now():
        if scope in ("list", "sticky"):
            return MUTE_CHIPS[scope]
        # Off, but the record does not say this pane did it. Either the environment holds it
        # (which no file can lift) or a hand edited a file — and the chip must not then
        # promise an undo key that would appear to fail.
        held = any(switch_held_by_env(name, env) for name, env, _s, _w in MUTE_SWITCHES)
        return MUTE_CHIPS["env"] if held else MUTE_CHIPS["elsewhere"]
    return None


def describe_mute() -> dict:
    """Everything `fbtodo mute list` reports: what is off, by what, and who asked for it.

    The switches come first and the record second, because they answer different questions:
    "is my phone quiet" is the switches — read the same way the scripts read them, env ahead
    of file — while "will it come back on its own, and who decided" is the record. A report
    that merged them would say `muted: true` for a switch an environment variable holds and
    promise an undo that cannot work.
    """
    rec = read_mute_record()
    switches = []
    for name, env, script, what in MUTE_SWITCHES:
        switches.append({
            "what": what, "script": script, "env": env, "file": switch_path(name),
            "value": read_switch(name), "off": switch_is_off(name, env),
            "held_by_env": switch_held_by_env(name, env),
        })
    return {
        "muted": any(s["off"] for s in switches),
        "scope": {"list": "until-done", "sticky": "sticky"}.get(rec.get("until")),
        "by": rec.get("by", "key") if rec.get("until") else None,
        "chip": mute_chip(),
        "switches": switches,
        "record": rec or None,
        "record_path": MUTE_PATH,
    }


# ================================================================== the switching
def list_is_finished(state: dict | None) -> bool:
    """Has the list this pane is showing been finished — every step done?

    `done`/`total` are the pane's own counters when the state carries them, and the steps
    are counted here when it does not, because a list with nothing outstanding is finished
    in both readings. A list that is not THERE is not finished: a pane asked to be quiet
    before its agent has planned anything must stay quiet, or the mute would last less than
    the keypress that set it. The end of the session unmutes on its own (`mute_release`).
    """
    state = state if isinstance(state, dict) else {}
    total, done = state.get("total"), state.get("done")
    todos = state.get("todos") or []
    if total is None:
        total = len(todos)
    if done is None:
        done = sum(1 for t in todos if isinstance(t, dict) and t.get("completed"))
    try:
        return int(total) > 0 and int(done) >= int(total)
    except (TypeError, ValueError):
        return False


def _remembered(rec: dict, name: str, env: str) -> str | None:
    """What this pane would have to put back for `name`: a word, `""`, or None.

    Three cases, and the difference between them is the whole of an honest undo:
      * the word the file held — write it back;
      * `""`, there was no file — remove the one this pane created, which is what puts the
        kit back to its own default;
      * None, it was already off — somebody else's mute, which this pane must not lift and
        must not tidy up either.

    The first branch (a record already holding the name) is what makes pressing `m` twice
    still undo correctly: without it the second press would find the switch off, decide it
    was somebody else's, and remember nothing to put back.
    """
    was = rec.get("was")
    if isinstance(was, dict) and name in was:
        remembered = was[name]
        return remembered if isinstance(remembered, str) else None
    if switch_is_off(name, env):
        return None            # already off before this pane touched it
    return read_switch(name) or ""


def set_muted(until: str = "list", state: dict | None = None, by: str = "key") -> tuple[str | None, str]:
    """Turn both switches off and remember what each was: `(chip, note)`.

    The remembered `was` is what makes the undo honest. `on` is not the same answer as
    "whatever it was": a `state` file saying `off` because you muted it by hand yesterday
    must read `off` again when you undo today's mute, and a `phone-state` that was never
    there must go back to not being there rather than be left behind as `on`.

    `by` is WHO took it, and it decides who ends it: a mute a key took is the pane's own
    promise to itself, so the pane lifts it when its read is over (`mute_release`), while a
    mute `fbtodo mute` took belongs to the person who ran it — a pane closing is not their
    reason. A record with no `by` is a key's: that is what every record before this field
    was.
    """
    rec = read_mute_record()
    was = rec.get("was") if isinstance(rec.get("was"), dict) else {}
    written = []
    for name, env, _script, _what in MUTE_SWITCHES:
        was.setdefault(name, _remembered(rec, name, env))
        try:
            atomic_write_text(switch_path(name), "off\n")
            written.append(name)
        except OSError:
            pass
    if not written:
        return None, f"cannot write the switch: {notify_dir()}"
    write_mute_record({
        "until": until,
        "was": dict(was),
        "by": by,
        "at_ms": int(time.time() * 1000),
        "session": (state or {}).get("session"),
        "list_id": (state or {}).get("list_id"),
        "pane": os.environ.get("TMUX_PANE"),
    })
    scope = "until this list is done" if until == "list" else "until you turn it back on"
    scripts = " + ".join(script for _n, _e, script, _w in MUTE_SWITCHES)
    return mute_chip(), f"notifications quiet {scope} ({scripts})"


def set_unmuted() -> tuple[str | None, str]:
    """Give the notifications back exactly as they were: `(chip, note)`.

    With no record there is nothing this pane muted, so nothing is written: an `u` pressed
    at a phone somebody else silenced must not delete their switch file. Each recorded
    switch is otherwise restored to its remembered word — unless it no longer holds the
    word this pane wrote, which means somebody else has been at it since.
    """
    rec = read_mute_record()
    was = rec.get("was") if isinstance(rec.get("was"), dict) else {}
    if not rec.get("until"):
        # Nothing of ours to undo. Saying "notifications back" here would be a claim about a
        # switch this pane never touched — and if a phone IS quiet the chip beside it says by
        # whom, which is the answer the reader actually needs.
        return mute_chip(), "no mute of this pane to undo"
    for name, _env, _script, _what in MUTE_SWITCHES:
        if (read_switch(name) or "").strip().casefold() != "off":
            continue        # somebody else's answer now: not ours to overwrite
        remembered = was.get(name)
        if remembered is None:
            continue        # it was off before we muted: not ours to lift
        try:
            if remembered == "":
                os.unlink(switch_path(name))
            else:
                atomic_write_text(switch_path(name), f"{remembered}\n")
        except OSError:
            pass
    clear_mute_record()
    return mute_chip(), "notifications back"


def mute_release(reason: str = "", only_mine: bool = True) -> str:
    """Unmute when the promise is over — the list finished, or this pane is closing.

    The end of the session is as much a reason as a finished list is a state, and both must
    give the notifications back: a pane that exits holding a mute leaves a phone that never
    rings again, with nothing on the machine left holding the key that fixes it. Returns a
    note for the pane log, or "" when nothing was muted.

    `only_mine` is what keeps a closing pane from lifting a mute somebody else took: a
    `fbtodo mute until-done` outlives the pane that happens to be on screen, because the
    person who ran it did not ask that pane for anything.
    """
    rec = read_mute_record()
    if not rec.get("until"):
        return ""
    if only_mine and rec.get("by", "key") != "key":
        return ""
    _chip, note = set_unmuted()
    return f"{note} ({reason})" if reason else note


def auto_unmute(state: dict) -> str | None:
    """The default's other half: a mute taken "until this list finishes" ends itself.

    Asked on every tick with the state on screen, so a pane that comes back after a reload
    honours the promise the pane before it made — and a pane watching an agent tick the last
    box rings the phone again on its own, which is the point of defaulting to it.
    """
    rec = read_mute_record()
    if rec.get("until") != "list" or not muted_now() or not list_is_finished(state):
        return None
    # `only_mine=False`, and the difference is the whole of `until-done` from a shell: the
    # promise belongs to the LIST, not to the pane that happens to be watching it, so the
    # pane that keeps it is the one whose key was never pressed. (The pane closing is the
    # other kind of promise — the reader's own — and that one stays the pane's.)
    return mute_release("the list finished", only_mine=False)


def apply_pane_key(key: str, state: dict | None = None) -> tuple[str | None, str]:
    """One keypress: `(chip, note)`, or `(None, "")` for a key that is not a switch key.

    Pure enough to test without a terminal — the terminal is `PaneKeys`' half, this is the
    decision — and total enough that a key which is not a switch key changes nothing at all,
    so a stray byte in a pane's input cannot silence a phone.
    """
    action = PANE_MUTE_KEYS.get(key)
    if not action:
        return None, ""
    verb, scope = action
    if verb == "mute":
        return set_muted(scope or "sticky", state)
    return set_unmuted()


# ================================================================ the keyboard
class PaneKeys:
    """The pane's key reader: raw bytes off its own terminal, terminal state given back.

    The pane owns a tmux pane, so nothing else is reading this terminal — but the pane is
    not allowed to leave it changed either. `open()` takes the modes a keypress needs (no
    canonical line, no echo, no waiting for Enter) and keeps the attributes it found;
    `close()` puts them back and is safe to call twice, because `cmd_pane` reaches it from
    its signal handler, its `finally` and its normal return alike.

    Three ways it says no instead of guessing: no `termios` (a platform without it), no
    terminal on stdin (a pane started with its input closed — the keeper does not promise
    one), or a `tcgetattr` that fails. A pane with no key reader is the pane it was before
    this, which is the right answer on a machine whose keyboard is not the point.
    """

    def __init__(self, enabled: bool = True):
        self.fd: int | None = None
        self.saved = None
        if not enabled:
            return
        try:
            import termios  # noqa: F401 — availability is the import; the methods use it
        except ImportError:
            return
        try:
            if not sys.stdin or not sys.stdin.isatty():
                return
            self.fd = sys.stdin.fileno()
        except (AttributeError, ValueError, OSError):
            self.fd = None

    def open(self) -> bool:
        """Take the terminal into key-reading mode. False when there is nothing to take."""
        if self.fd is None:
            return False
        import termios

        try:
            self.saved = termios.tcgetattr(self.fd)
            mode = list(self.saved)
            # [3] = lflag: no echo (a mute must not be typed into the frame behind it) and no
            # canonical line (one keystroke, no Enter). ISIG stays ON, so Ctrl-C still
            # interrupts and Ctrl-Z still suspends.
            mode[3] &= ~(termios.ECHO | termios.ICANON)
            # [6] = cc: VMIN 1 / VTIME 0 is "hand me a byte as soon as there is one"; the
            # reader also selects on the fd first, so a byte is only taken when one is there.
            mode[6] = list(mode[6])
            mode[6][termios.VMIN] = 1
            mode[6][termios.VTIME] = 0
            # TCSANOW, never TCSADRAIN: TCSADRAIN waits for pending OUTPUT to be transmitted
            # first, and this pane writes to that output every second — so with nobody
            # draining it (a pane whose reader has stopped, a pty whose master gone away)
            # both `open()` and `close()` block, and the one inside the SIGINT handler stops
            # Ctrl-C from reaching the pane at all (measured 2026-10-04: a pane with the key
            # reader open ignored SIGINT and had to be killed — caught by this suite's own
            # "must still leave cleanly on Ctrl-C", which is why that word is here). The modes
            # this touches are INPUT modes, for which the drain is worth nothing anyway.
            termios.tcsetattr(self.fd, termios.TCSANOW, mode)
        except (termios.error, ValueError, OSError):
            self.fd, self.saved = None, None
            return False
        return True

    def poll(self) -> str | None:
        """One keypress as a character, or None for nothing (or for bytes that are not keys).

        Escape sequences are swallowed whole — an arrow key is three bytes and the last two
        are letters that would otherwise mute a phone. End of input disables the reader
        instead of spinning on it: a closed stdin stays "readable" forever, and a pane
        spinning at 4Hz on a dead descriptor is worse than a pane with no keys.
        """
        if self.fd is None:
            return None
        import select

        try:
            if not select.select([self.fd], [], [], 0)[0]:
                return None
            chunk = os.read(self.fd, 1)
        except (OSError, ValueError):
            self.fd = None
            return None
        if not chunk:
            self.fd = None            # stdin is done; stop asking it
            return None
        char = chunk.decode("utf-8", "replace")
        if char == "\x1b":
            self._drain()
        return char if char.isprintable() else None

    def _drain(self) -> None:
        """Take what followed an escape byte and throw it away."""
        import select

        while self.fd is not None:
            try:
                if not select.select([self.fd], [], [], 0)[0]:
                    return
                more = os.read(self.fd, 32)
            except (OSError, ValueError):
                self.fd = None
                return
            if not more:
                self.fd = None
                return

    def close(self) -> None:
        """Give the terminal its own attributes back. Safe to call more than once."""
        if self.fd is None or self.saved is None:
            return
        import termios

        try:
            # TCSANOW for the same reason as `open`, and more sharply here: this runs on
            # Ctrl-C, where blocking is the one thing the handler must not do.
            termios.tcsetattr(self.fd, termios.TCSANOW, self.saved)
        except (termios.error, ValueError, OSError):
            pass
        finally:
            self.fd, self.saved = None, None


def pane_mute_help() -> str:
    """The keys, in one line, for `--help` and the docs."""
    return "pane keys: m quiet until this list finishes · M quiet until u · u loud again"


__all__ = [
    "MUTE_OFF_WORDS", "MUTE_SWITCHES", "PANE_MUTE_KEYS", "MUTE_CHIPS",
    "notify_dir", "switch_path", "read_switch", "switch_is_off", "switch_held_by_env",
    "muted_now", "read_mute_record", "write_mute_record", "clear_mute_record", "mute_chip",
    "describe_mute",
    "list_is_finished", "set_muted", "set_unmuted", "mute_release", "auto_unmute",
    "apply_pane_key", "PaneKeys", "pane_mute_help",
]