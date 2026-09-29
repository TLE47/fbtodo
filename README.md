# fbtodo — watch a coding agent's todo list, live, in a tmux pane

`fbtodo` renders the todo list a [Freebuff](https://freebuff.com) session is working
through — the `write_todos` calls the agent makes — as a small framed pane that sits
**underneath the session it belongs to**, refreshing as the agent works. It also times each
step, estimates what is left, and can push to your phone when a session finishes, stalls,
asks you a question, or loses its pane.

```
┌ FBTODO ────────────────────────────── 4/7 ─·····──┐
│ big goal  fit the pane heading whole              │
│                                                   │
│ ✓  read the existing renderer            1m12s    │
│ ✓  resolve the palette into roles          44s    │
│ ✓  draw the gradient bar                   29s    │
│ ▸  clip the title to the frame          1m04s…    │   ~1m
│ ·  split the drawing code into roles              │
│ ·  run the self-check                             │
│ ·  update the docs and land it                    │
├───────────────────────────────────────────────────┤
│ ● working 9m38s   4/7 steps · EST REM ~3m         │
└───────────────────────────────────────────────────┘
```

Everything is one Python file with no third-party dependencies. Three stores, four
watches, a pane, and a lot of care about what "the clock" means.

---

## Contents

- [Install](#install)
- [Quick start](#quick-start)
- [How it works](#how-it-works)
  - [The three stores](#the-three-stores)
  - [Which store gets read](#which-store-gets-read)
  - [The watcher](#the-watcher)
  - [The pane, and where it goes](#the-pane-and-where-it-goes)
  - [Pins, remembered layout, and `why`](#pins-remembered-layout-and-why)
  - [What the pane draws](#what-the-pane-draws)
  - [Clocks, estimates, ETA](#clocks-estimates-eta)
  - [The goal heading](#the-goal-heading)
  - [Ending a turn: the bell](#ending-a-turn-the-bell)
  - [The four watches](#the-four-watches)
  - [The remote (`nas`) source](#the-remote-nas-source)
  - [Retention](#retention)
- [Configuration](#configuration)
- [Exit codes](#exit-codes)
- [Notification kit](#notification-kit)
- [Development](#development)
- [Internals](docs/INTERNALS.md) — the state file, the remote probe protocol, the notifier contracts
- [Troubleshooting](#troubleshooting)
- [Limits](#limits)

---

## Install

The whole tool is two files (`fbtodo` and `fbtodo-selfcheck.py`) plus an optional
notification kit in `notify/`.

```sh
git clone https://github.com/<you>/fbtodo ~/Projects/fbtodo
ln -s ~/Projects/fbtodo/fbtodo ~/.local/bin/fbtodo     # or anywhere on $PATH
chmod +x ~/Projects/fbtodo/fbtodo
```

Requirements:

- **Python 3.9+** (no packages — standard library only).
- **tmux** for the pane itself. `snap`, `json` and `bar` work without it.
- **Linux or macOS**, and a Freebuff CLI session to watch.

For the notification kit, see [notify/README.md](notify/README.md).

---

## Quick start

```sh
fbtodo                 # the live pane (default); starts the watcher, exits with the session
fbtodo snap            # one snapshot, plain text
fbtodo json            # one snapshot, clean JSON
fbtodo bar             # "todos 3/5" — for a tmux status bar
fbtodo status          # instance, watcher, state file, scratch footprint, pace
fbtodo why             # why each pane is where it is
fbtodo pin --size 9    # pin this window's list pane
fbtodo stop            # stop the watcher
fbtodo prune           # enforce retention now
```

Run `fbtodo -h` for the full flag list and the same prose you are reading here, or
`fbtodo -V` for the version.

The normal way to use it is to **put it next to the session**:

```sh
tmux new-session -s work
# … run the agent in this pane …
# Ctrl-b "  → split below → run:  fbtodo --instance-of <the shell's pid>
```

Inside tmux, `fbtodo` with no arguments does exactly that. The pane keeper (below) will
also open one for you for any local session that is in tmux and has no list, so the
minimal setup is "be in tmux".

---

## How it works

### The three stores

A coding agent's todo list is not a side channel — it is written to whatever transcript
store the client keeps. Freebuff has three, and picking the wrong one is the usual reason
a pane looks broken:

| Source | Store | Granularity |
|---|---|---|
| **cli** (`freebuff` in a terminal) | `~/.config/manicode/projects/<project>/chats/<ISO>/log.jsonl` | **live — mid-turn** |
| **desktop** (the app) | `~/.config/freebuff-desktop/projects/<slug>/desktop-v2.db` (SQLite) | per turn, and only while the app runs |
| **nas** (a session on another host, reached over ssh) | `<root>/<project>/chats/<ISO>/chat-messages.json` | per completed turn |

The CLI journal is the good one, because it is append-only and written *during* the turn:
each record is a JSON line, and a `write_todos` call appears in it the moment the agent
makes it. `fbtodo` tails that file, keeps every `write_todos` it has seen, and renders the
newest one. A new list **replaces** the previous one wholesale — state is never merged —
and when the session changes, the old list is dropped immediately instead of lingering.

The Desktop store is read per *turn* from its SQLite database, so it is a photograph, not a
live view; it is what makes an app-only workflow work at all.

### Which store gets read

`--source auto` (the default) prefers a live CLI chat for the current directory. An
explicit `-s cli|nas|desktop` is never answered from the cached watcher state unless that
state describes the same backend — a status bar asking for the remote list used to print
the local one, which read as "the remote host has one too".

The instance to follow is found like this:

1. `--instance-of PID` — the freebuff process launched by that shell (how a wrapper binds a
   pane to *its* session rather than to whatever is newest in the directory).
2. `--watch-pid PID` — take this pid as the instance, outright.
3. Otherwise the freebuff processes are enumerated (`ps`), a cwd match on the current
   directory is preferred, and the **youngest** wins. A directory can host several
   sessions, and "the one started here" is what a human means.

### The watcher

A pane cannot watch itself, so the work is split:

- **`fbtodo daemon`** follows one running instance: while it lives, the todo state is
  refreshed into `~/.freebuff/fbtodo-state.json`; when the process exits the daemon shuts
  itself down and removes its lock (`fbtodo-daemon.pid`). It is started for you by anything
  that needs it; `-f` keeps it in the foreground.
- **`fbtodo pane-watch`** is the pane keeper: one process per tmux *server*, 3 s cadence. It
  opens a pane for every local session that is in tmux and has none, and exits when the last
  one goes. It has its own lock and log, because being kept out by the watcher's lock *was*
  the bug (a `-s nas` daemon holding `fbtodo-daemon.pid` left a killed local pane unopened).

`fbtodo status` reports both, plus the state file, the scratch footprint, and the pace it
has remembered for this project.

### The pane, and where it goes

The rule is: **a list pane opens under the pane its session is drawn in.** That is where a
glance looks for it. Two consequences, both learned the hard way:

- The pane is split off the *pane* running the session, never off its *window*: given a
  window, tmux uses that window's **active** pane, which is not necessarily the one running
  the agent. One remote ssh pane sharing a window with a session pane was enough to land a
  fresh pane in the wrong column.
- Every keeper pass re-derives the geometry (`tmux list-panes -a`) and `move-pane`s a
  drifted pane back — *in place*, so the pane keeps its process, its scrollback and its step
  clocks, and a step that is counting is not restarted.

A pane that is in **another window** than its session is left alone on purpose: that is an
arrangement the operator made. So is a pane that is wider than the session's (a full-width
strip under two panes).

For a remote session the anchor is the ssh the session runs in, found by matching the ssh
login on the far host — see [the remote source](#the-remote-nas-source).

Two knobs set the default geometry, used when there is no pin and nothing remembered:

| Variable | Default | Meaning |
|---|---|---|
| `FBTODO_SPLIT` | `v` | split direction: `v` opens the list **below**, `h` beside |
| `FBTODO_PANE_SIZE` | `12` | lines (or columns) for a fresh pane |

### Pins, remembered layout, and `why`

Where a pane goes can be overruled **per window, per role**:

```sh
fbtodo pin --size 9                    # this window, both roles
fbtodo pin --role nas --size 7         # this window, the remote pane only
fbtodo pin --side h --size 30          # beside, 30 wide
fbtodo pin --list                      # what is set, and where each half came from
fbtodo pin --clear                     # drop it
```

A pin is keyed `<session>:<index>` (e.g. `main:2`) — the window you would *type*, not the
`@71` id that a tmux restart takes with it — and stored in `~/.freebuff/fbtodo-pins.json`.
A role is `local` (the pane beside a local session) or `nas` (the pane beside an ssh). One
window can hold both, so one `{side, size}` cannot answer for both: a `local`/`nas` half
outranks the window's shared half.

A pane that has no pin is **remembered, not argued with**: the keeper records what it sees
in `~/.freebuff/fbtodo-last.json`, and the next pane is opened at that size. Two deliberate
limits — the remembered size is applied *once* (a standing instruction would make
hand-resizing impossible) and it is only written after the same numbers have been seen
twice, so a drag through intermediate sizes is not recorded as a choice.

`fbtodo why` is the first thing to run when a pane is somewhere unexpected. It names the
side and size in force **with the source that supplied each** (`pin (main:1 nas)`,
`remembered`, `FBTODO_SPLIT/FBTODO_PANE_SIZE`, `default`), the pane it is placed against and
what that anchor is, the geometry now, and whether it is actually placed. `--json` carries
the same records plus the pins file, the remembered layout and the two knobs.

### What the pane draws

The colour pane is a frame with a status strip, and its palette is themeable. Layout never
names a colour: the styles are resolved into roles (`accent`, `faint`, `muted`, `track`,
gradient ends) and the drawing code asks for a role, so a theme is a data change.

- **Title** — `FBTODO` on an accent badge, drawn in reverse video so no background selector
  is needed, with the `done/total` count on the right.
- **Progress bar** — a gradient of `█` over a visible `░` track, with an eighth block for
  the one cell at the boundary. Drawn in 24-bit colour where the terminal takes it and in
  the nearest 256-colour entry where it does not, always as a **glyph in a foreground
  colour** rather than a filled background cell.
- **Steps** — `✓` done, `▸` running, `·` pending. A step's duration is right-aligned against
  the frame's right wall, so the numbers form a column.
- **Status strip** — badges the live state (`working 9m38s`, `waiting`, `done`) and spins
  while a step runs.
- **`PATCH` / `ALERT` row** — when there is something to say: the last outcome of an optional
  CLI-patch step and the last thing the phone was told. Both are read from a log the
  producing step already writes, so neither costs a probe. Neither present renders as before.

Everything about the **plain** renderer is deliberately untouched — it is the machine-readable
path (`snap`, `json`, `bar`).

### Clocks, estimates, ETA

Which is where most of the care in this tool has gone.

- A step's clock starts when the watcher **sees it running**, not when the list was written,
  and it is measured from the tick. A step the watcher never saw running has no duration of
  its own — a quiet gap reads as a broken pane, so the pane names the reason instead.
- **Paint and poll are separate clocks.** The store is polled on `-i` (1 s locally, 5 s for
  the remote source, one ssh each); the *clock* repaints far more often (default every
  `--tick` 5 s, 1 s while a step is counting) so the seconds move even when nothing else does.
- **Estimates** ride the same row as the duration: `~1m` for a pending step, `4m10s / ~3m`
  for the active one. They come from this project's own history of completed steps (kept in
  `~/.freebuff/fbtodo-tasks.json`), with the list's own pace as the fallback.
- Past twice the estimate a step is marked `[STUCK?]` — a hint, not a verdict.
- The bar carries `EST REM` and an `ETA`, and `fbtodo status` shows the remembered pace and
  where each step's number came from.

### The goal heading

Each list is headed by the agent's own one-line `Goal:` statement, which the list's first
request is *not*: a quote is the phrasing, not the objective. A session that never wrote one
draws no heading row in the framed pane (and `big goal · — none stated` in the plain
renderer) rather than repeating the request back at you.

When a newer request has arrived since the list was written, a `now` line says so — a list
can sit unchanged for hours while the session works on. A request that is a continuation
("continue", "keep going") is shown as a **nudge** instead: the one thing the operator is
waiting to see is whether the agent rewrote its list.

`--goal-lines N` (default 3, 1 in a narrow strip) sets how many lines the heading may take;
`0` hides it.

### Ending a turn: the bell

The journal records whether a turn **ended** (`shouldEndTurn`). A finished list is therefore
two facts, not one: every step ticked *and* the turn over. Only then does something ring.
The point is to distinguish "the agent is done" from "the agent is thinking", which a
`done/total` count alone cannot.

The remote build writes no `shouldEndTurn`, so a remote list can never claim its turn ended;
the bell is a local-session feature.

### The four watches

Each of these is an **optional** script in `~/.config/freebuff-notify/`. `fbtodo` only times
them and reads nothing back — the notifier owns the decision *and* the "already pushed"
record, so nothing has to be kept in sync and a machine without the directory simply never
asks. Each is skipped when its script is missing.

| Watch | Script | Question it answers |
|---|---|---|
| finish | `todo-bell.py` | the list is complete and the turn ended — ring, and push to the phone |
| ask | `ask-bell.py` | the agent is **stopped on a question** and waiting on you |
| stall | `pause-bell.py` | the agent stopped *without* ending its turn and the list still has work (local only) |
| pane | `pane-bell.py` | a session has no todo pane the keeper failed to put back, or there is no keeper at all |

A fifth, `drop-bell.py`, is asked when a session **dies** rather than ending — the marker's
pid is gone while the marker is still there, which is a different question from "did it
finish".

The pane watch exists because every other failure here is quiet by construction: the keeper
has no stderr anybody reads, and its log records a pane that came **back**, never one that
did not. Ask it directly with
`pane-bell.py --print --keeper ~/.freebuff/fbtodo-pane-keeper.pid`.

Cadences: `--notify-seconds` (15 s), `--ask-seconds` (3 s), `--pause-seconds` (30 s),
`--pane-bell-seconds` (60 s). `0` switches any of them off.

### The remote (`nas`) source

`-s nas` reads a session that is running on another host — typically a container on a NAS,
reached over ssh — and follows **that host's** process rather than a local pid, so the pane
lives exactly as long as the remote session does.

There is no built-in target. Set:

```sh
export FBTODO_NAS=user@host                              # or --nas-host
export FBTODO_NAS_ROOT=/srv/app/state/manicode/projects   # or --nas-root
export FBTODO_NAS_PROJECT=myproject                       # or --nas-project
```

Reading it is **one ssh round trip per poll**, and the probe is careful about cost:

- The connection is multiplexed (`ControlMaster`/`ControlPersist 60`), so the first poll pays
  the handshake and the rest ride it — the difference between a 1.5 s poll and a 0.2 s one.
  Each poll has a 12 s timeout: a pane is useless if a poll can outlive its interval.
- The reply is five header lines (`DIR`, `SIZE`, `MTIME`, `LIVE`, `FB`), one state line, and
  optionally two more: the tails of the remote patch log and notifier log, read at the far
  end so the pane's `PATCH`/`ALERT` row costs nothing extra.
- The state line is `NONE`, `UNCHANGED`, `ERR …` or a compact JSON object. `UNCHANGED` means
  the far side skipped its parse because the transcript had not moved — an idle poll costs a
  `stat`, and the pane serves the list it already has instead of blanking out.
- The parse itself runs **on the remote host** (`python3` must be there): the conversation
  store is a few megabytes, and pulling it across per poll would be absurd.

Two things are worth knowing about the remote build:

- Its journal does **not** record tool inputs, so the only place a todo list exists over
  there is the per-turn conversation store. That is why the remote source is turn-granular.
- The remote build's patch/alert log paths have no default; set `FBTODO_NAS_PATCH_LOG` and
  `FBTODO_NAS_ALERT_LOG` if you want that row.

**The marker.** If you wrap your remote login in a shell function, have it write a marker to
`$HOME/.fb-session` on the remote host:

```
<pid> <started> <dir>
```

…one line, three fields: the pid of the session, when it started, and the directory it
started in. `fbtodo` reads it as `1` (that session is live), `0` (the marker is stale) or
`-` (no marker — the hook is not installed, and the process probe is used instead). The
directory also names the project whose store gets read, which matters because the wrapper
runs from all over the host. `--fb-marker PATH` points at a different path.

A minimal wrapper, on the remote host's shell:

```sh
remote() {
  printf '%s %s %s\n' "$$" "$(date -u +%FT%TZ)" "$PWD" > "$HOME/.fb-session"
  trap 'rm -f "$HOME/.fb-session"' EXIT
  command ssh -t user@host "cd / && exec \$SHELL -l"     # or docker exec -it …
}
```

### Retention

Nothing grows without a cap. About hourly, and on demand with `fbtodo prune`, `fbtodo`
enforces retention on its own records: **500 task records**, **7 days**, and **1 MiB** of
daemon log. Override with `--max-records`, `--max-age-days`, `--log-cap-kb`.

---

## Configuration

Precedence is the usual one: a command-line flag, then the environment, then a file.

| Variable | Default | Meaning |
|---|---|---|
| `FBTODO_HOME` | `~/.freebuff` | where state, locks and logs live |
| `FBTODO_NAS` / `_ROOT` / `_PROJECT` | *(empty)* | the remote store; required by `-s nas` |
| `FBTODO_NAS_PROC` | `manicode/freebuff` | the remote process that *is* a session |
| `FBTODO_NAS_POLL` / `_IDLE` / `_LIVE` | 5 / 60 / 2.5 | remote watcher cadence (s) |
| `FBTODO_FB_MARKER` | `$HOME/.fb-session` | the remote wrapper's marker path |
| `FBTODO_NOTIFY` / `_DROP` / `_ASK` / `_PAUSE` / `_PANE_BELL` | `~/.config/freebuff-notify/*.py` | the five watches |
| `FBTODO_NOTIFY_SECONDS` / `_ASK_SECONDS` / `_PAUSE_SECONDS` / `_PANE_BELL_SECONDS` | 15 / 3 / 30 / 60 | their cadences (0 = never) |
| `FBTODO_PANE_SECONDS` | 3 | how often the keeper looks |
| `FBTODO_SPLIT` / `FBTODO_PANE_SIZE` | `v` / `12` | default pane geometry |
| `FBTODO_NO_PANE` | — | set to disable panes entirely |
| `FBTODO_PATCH_LOG` / `_META` / `_ALERT_LOG` | `~/.config/freebuff-patch-watch/watch.log`, `~/.config/manicode/freebuff-metadata.json`, `~/.config/freebuff-notify/phone.log` | the optional `PATCH`/`ALERT` row |
| `FBTODO_NAS_PATCH_LOG` / `_NAS_ALERT_LOG` | *(empty)* | the same two facts, remote |
| `FBTODO_ACCENT` / `_FAINT` / `_MUTED` / `_TRACK` | theme | palette overrides |
| `FBTODO_GRADIENT_START` / `_END` | theme | `#rrggbb`, or a raw SGR code like `1;36` for the accent |
| `FBTODO_TRUECOLOR` | auto | force 24-bit colour on or off |
| `FBTODO_TMUX` | `tmux` | the tmux binary/args to drive (a test knob) |

Theming is a file, not a flag: `~/.config/fbtodo/theme.json`, overridden by
`./.fbtodo-theme.json` in the working directory, overridden by the environment above.

Exit codes: `0` ok · `2` usage (including polling without a TTY) · `66` no instance or no
store · `75` the watcher failed to start.

---

## Notification kit

`notify/` holds the little programs that make the phone alerts real: the five watches, a
`bell.sh` for the local chime, a `phone.sh` that sends to iMessage and/or [ntfy](https://ntfy.sh),
and their test suite. They are **optional** — `fbtodo` runs fine without them, and skips any
that are missing. See [notify/README.md](notify/README.md).

---

## Development

The self-check drives the real thing: it runs against a throwaway `FBTODO_HOME`, spawns fake
instances (plain `sleep` processes) whose death must stop the watcher, and starts private
tmux servers so it never touches yours.

```sh
python3 fbtodo-selfcheck.py              # all of it, ~90-160 s
python3 fbtodo-selfcheck.py --list       # the phases, with line numbers and check counts
python3 fbtodo-selfcheck.py --only nas-live
python3 fbtodo-selfcheck.py --only 1 --only "nas pane"
FBTODO_SELFCHECK_TIME=1 python3 fbtodo-selfcheck.py   # per-check cost
bash notify/test-freebuff-notify.sh      # the notification kit's own suite
```

`--only` re-runs the file with the phases you did not name cut out (`pass`, line numbers
preserved), keeping the cheap body every phase depends on — fixture setup, the unit checks.
That is the way to run one phase in ~20 s instead of the whole thing.

[`docs/INTERNALS.md`](docs/INTERNALS.md) has the state-file contract, the wire format of the
remote probe, the notifier contracts and the pane lifecycle.

Two habits the suite encodes, because both have bitten:

- **Mute the notifiers.** Some phases start a *real* watcher, and a real watcher reading a
  fixture's dead-looking session pushes "freebuff dropped" to your phone.
- **Private tmux servers.** Every tmux call names a socket (`FBTODO_TMUX`), so a test can
  never move a pane in the server you are sitting in.

After changing the code you also have to restart anything already running: the pane and the
watcher hold the old code until they exit.

---

## Troubleshooting

| Symptom | First thing to try |
|---|---|
| Pane is empty | `fbtodo status`. If the backend is `cli` with no list, the agent has not called `write_todos` yet — the pane shows nothing until it does. |
| Pane exits immediately | exit `66`: no running instance, or no store found for the cwd. Try `--instance-of PID` or `-p`. |
| Pane gone after a while | `--stale-after` (default: 60 min of store silence). `0` = never. |
| Pane not where you want it | `fbtodo why` — read the `source` on the line, it is usually the answer. Then `fbtodo pin`. |
| Pane killed mid-session | It comes back within ~3 s. If it does not, the pane watch is what tells you. |
| List looks frozen | Count `write_todos` calls in the journal. A `goal`/`now` pair that disagree is the pane saying the same thing. |
| A step with no duration | The watcher never saw it running. A step's clock starts at the tick, not at the list's mtime. |
| No bell on the remote source | By design: the remote build records no turn end. |
| Different code running | The pane and the watcher hold what they started with: `fbtodo stop`, then re-open the pane. |

---

## Limits

- **The CLI journal is the only live source.** The Desktop store is per turn; the remote
  store is per turn and needs a parse on the far host.
- **A turn is the unit of "ended".** A long turn with a finished list rings nothing until
  the turn actually closes.
- **The pane needs tmux.** There is no terminal-UI fallback; `snap`/`json`/`bar` are the
  non-tmux interface.
- **The notification kit is macOS-leaning.** The chime uses macOS system sounds and the
  iMessage transport uses `osascript`; the ntfy transport is portable.
- **Not included:** the shell wrapper that opens the pane on launch, the remote launch hook,
  and the CLI-patch step whose log the `PATCH` row reads. Each is glue around this tool —
  their contracts are documented above, so you can write your own or ignore them.

## License

MIT — see [LICENSE](LICENSE).
