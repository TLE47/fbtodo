# Decisions

The long-form reasoning behind things that look arbitrary in the code: why a mechanism exists,
what it cost to learn, and what would break if it were changed back. The README states what the
tool does; this folder is why.

## The pane did not appear

| What you see | What it is |
|---|---|
| nothing at all | you are not in tmux: run `tmux new -s work`, then start the agent inside it |
| `fbtodo: command not found` | `~/.local/bin` is not on your `PATH` |
| a pane, but no list | the agent has not written a todo list yet; `fbtodo status` says what it sees, and the pane still shows the turn's clock and the newest calls the session *did* make |
| the pane is somewhere odd | `fbtodo why` names the pane, the anchor and where the placement came from |

## The pane is split off the *pane*, never off the window

Given a window, tmux splits its **active** pane, which is not necessarily the one running the
agent — so the rule is "next to the pane its session is drawn in", resolved through
`tmux list-panes -a`. And every keeper pass re-derives the geometry and `move-pane`s a drifted
pane back **in place**, so the pane keeps its process, its scrollback and its step clocks: a
step that is counting is not restarted by a layout correction.

A pane in another window, or one wider than the session's, is left alone on purpose — that is
an arrangement the operator made. Only the two sides fbtodo opens itself (below, beside) are
managed.

## Two background processes, two locks

`fbtodo daemon` follows one running instance and refreshes the state file. `fbtodo pane-watch`
is the pane keeper: one process per tmux *server*, 3 s cadence, opening a pane for every local
session that lacks one. They have separate lock files because **sharing the watcher's lock was
the bug**: a watcher for another source holding `fbtodo-daemon.pid` left a local pane unopened.
Two processes with two responsibilities must not block each other.

## The frame is clamped, in both axes

Height: the three early-return frames (the error frame, the no-list NAS frame, the plain strip's
no-list branch) skipped the fit-down pass, so a 5-row pane showing a 14-step list produced 9
rows and scrolled. `_clamp_rows(rows, height, head, tail)` keeps the title row and the
bar/footer or bottom border and cuts the middle.

Width: at 12 columns, `textwrap.wrap(..., width=max(16, width))` and `bar_w = max(6, ...)` and
character-counted session/clock rows all overflowed. The floors are `max(1, width)`, and
`_clamp_widths(lines, width)` is the final guarantee: `render` joins the frame through it.
Below 30 columns the frame is not drawn at all — the plain strip is, because a 24-column frame
is not a frame. No recorded golden row moved when the clamp landed; `tests/golden` proves it.

## The repaint is a line diff

The pane used to clear and repaint every tick. `pane_repaint(previous, frame, rows)` returns a
whole-frame paint only when the shape changed (or there is no previous frame); otherwise it
rewrites **only the rows that changed** and parks the cursor below the frame. Measured on a real
pty with a frozen state and two clocks moving: 3,384 → 773 B/s, and 8 full clears → 1. The
returned string is `""` when nothing moved, which is what lets the loop skip the write
entirely.

## The plain renderer is untouched

`_render_plain` is the machine-readable path — `snap`'s output, and the token contract (`~2m`,
`todos 3/5`) other programs read. New rows (the `PATCH`/`ALERT` rows) appear there only when the
state carries them, and a piped `snap` keeps the bare `~2m` even where a frame would print the
spread. A prettier plain renderer would be a breaking change to a published contract.

## Themes are data, and problems are filled in place

Layout never names a colour: six roles plus two gradient stops are resolved once, in `render`,
and a value is either `#rrggbb` or a raw SGR parameter list — the value ends up *inside* an
escape sequence, so nothing else is accepted. Anything refused is recorded in `THEME_PROBLEMS`,
which is *filled in place* (`THEME_PROBLEMS[:] = problems`) because the cached tuple holds that
same list; rebinding the name left `status` reading an empty one.

## The package is ten modules, one namespace

`src/fbtodo/` is split bottom-up — `base`, `locks`, `alerts`, `scan`, `desktop`, `nas`, `tasks`,
`sources`, `panes`, `render`, and the front door `__init__` — with each module's `__all__` and a
chain of `from .X import *`. That keeps **one** namespace, so `fbtodo.X` and the suite's patched
globals still resolve. Mutable module state that `main` or `read_theme` has to reach (the
estimate knobs, the theme problems) lives in a container rather than a rebound global, because a
`global` statement cannot reach a copy in another module.

The split exposed five real bugs, each now pinned by a test: two functions named `_plain` (the
renderer's ANSI-stripper shadowed the prose one), the estimate knobs rebound by `main`,
`THEME_PROBLEMS` rebound by `read_theme`, a dropped `import shutil as _shutil` (the blocker keyed
on the module name, the code read the alias), and `nas.py` missing the import that brought
`_iso_ms`. A static AST check in the self-check now walks every module and asserts no global is
loaded that nothing provided — which is how a future split fails fast instead of at runtime. A
second AST check refuses the other import-time trap: a function default or module-level
expression outside `base.py` that reads one of the state paths, which would freeze the root
`init_state_root` is allowed to move.

## Limits

- **The CLI journal is the only live Freebuff source.** The Desktop store is read per turn.
- **A turn is the unit of "ended".** A long turn with a finished list rings nothing until the
  turn actually closes.
- **The pane needs tmux.** There is no terminal-UI fallback; `snap`/`json`/`bar` are the
  non-tmux interface.
- **The notification kit is macOS-leaning.** The chime uses macOS system sounds and the iMessage
  transport uses `osascript`; the ntfy transport is portable.
- **Not included:** the CLI-patch step whose log the `PATCH` row reads. Its contract is in
  [INTERNALS.md](../INTERNALS.md), so you can write your own or ignore it.

## The watcher's counter is not the file's

A one-shot probe has no list history of its own, so `adopt_version` borrows the counter from a
fresh watcher state describing the same list. A **file** source is the exception: it is a
re-player, so the number it recorded is taken at its word rather than compared against a
fingerprint and incremented (`#7` must not print `#8`). See [SOURCES.md](../SOURCES.md).
