# fbtodo

**Watch your coding agent work — its checklist, live, in a pane under the session.**

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org)
[![No dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)](#requirements)
[![Platform: macOS | Linux](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey)](#requirements)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Tests: self-check](https://img.shields.io/badge/tests-self--check%20%2B%20notify%20suite-blueviolet)](#development)

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

[fbtodo](.) mirrors the todo list a [Freebuff](https://freebuff.com) session is working
through — the very list the agent keeps for itself — and draws it in a small framed pane
**directly underneath the session that owns it**, refreshing while the agent works. It
times each step from the moment the step actually starts, estimates what is left, and can
ring your phone when a session finishes, stalls, stops on a question, or loses its pane.

Quick links: [What it does](#what-it-does) · [Does it work with my setup?](#does-it-work-with-my-setup) ·
[Install](#install-60-seconds) · [Commands](#everyday-commands) · [Settings](#settings) ·
[FAQ](#faq) · [Ideas & roadmap](#ideas--roadmap)

---

## What it does

If you have ever watched an agent chew through a long task, you know the feeling: the
terminal scrolls, you have no idea whether it is two minutes or twenty from done, and you
refresh the window just to feel something. The agent *does* keep a plan — it writes a todo
list and ticks items off — but that list lives in a transcript file, not on your screen.

fbtodo puts that list where you are already looking.

| You want to know | fbtodo shows |
|---|---|
| What is it doing right now? | the list, with `✓` done, `▸` running, `·` still to come |
| How long has this step taken? | a clock per step, right-aligned so the numbers line up |
| How much is left? | `EST REM` and an `ETA`, learned from this project's own history |
| Is it stuck? | `[STUCK?]` once a step runs past twice its estimate |
| Is it finished, or just thinking? | the bell rings only when the list is done **and** the turn has ended |
| Is it waiting on me? | an `ask` watch that pushes the question to your phone |

Everything is **one self-contained Python file** with no third-party dependencies — three
stores, four watches, a pane, and a lot of care about what "the clock" means.

---

## Does it work with my setup?

fbtodo reads the todo list the agent **already writes for itself**. There is nothing to add
to your prompt, no `.cursorrules` snippet, no JSON file to maintain and no hook to install —
if the agent is working through a list, the pane can see it.

| Your setup | What it reads | Live? |
|---|---|---|
| **Freebuff CLI**, in a terminal | `~/.config/manicode/projects/<project>/chats/<ISO>/log.jsonl` | **yes — mid-turn** |
| **Freebuff Desktop** app | the app's own SQLite store | per turn, while the app runs |
| Claude Code, Aider, Cursor, … | — | not yet — a generic source is the [first roadmap item](#ideas--roadmap) worth landing |

`--source auto` (the default) picks the right one for the current directory; `-s cli|desktop`
says so explicitly.

---

## Install (60 seconds)

### Requirements

- **Python 3.9+** — already on macOS and most Linux boxes; no `pip install` of anything.
- **tmux** — only for the pane itself. `snap`, `json` and `bar` work fine without it.
- **macOS or Linux**, and Freebuff running somewhere to watch.

```sh
# tmux, if you do not have it yet
brew install tmux            # macOS
sudo apt install tmux        # Debian / Ubuntu
```

### Install it

```sh
git clone https://github.com/TLE47/fbtodo ~/Projects/fbtodo
mkdir -p ~/.local/bin && ln -sf ~/Projects/fbtodo/fbtodo ~/.local/bin/fbtodo
```

Two commands — the clone keeps the executable bit, so there is nothing to `chmod`.

Make sure `~/.local/bin` is on your `PATH` (add
`export PATH="$HOME/.local/bin:$PATH"` to `~/.zshrc` or `~/.bashrc` if it is not), then:

```sh
fbtodo -V          # prints the version — you are installed
fbtodo snap        # one plain-text snapshot of the list (works with no tmux)
```

### Or try it in 30 seconds, with no tmux at all

With a session running in this directory:

```sh
fbtodo snap     # the list as plain text
fbtodo json     # the same state, as clean JSON — pipe it anywhere
fbtodo bar      # "todos 3/5", what a status line shows
```

Those three need nothing but Python. The pane is the pretty one; these are the ones you can
script.

### Use it

The intended setup is *be in tmux*: the pane keeper opens a todo pane by itself for any
local session that is in tmux and does not have one yet.

```sh
tmux new -s work           # 1. start a tmux session
freebuff                   # 2. run your agent in it
#                           3. a FBTODO pane appears underneath, on its own
```

If you would rather place it yourself, split the pane and bind it to *that* shell:

```
Ctrl-b "                   # split the current pane downwards
fbtodo --instance-of $$    # the list for the agent running in this pane
```

`--instance-of` is how a wrapper — or you — attaches a pane to a specific session rather
than to whatever is newest in the directory.

### One word: `fb`

Typing `freebuff` *and* remembering to open a pane is friction, and friction is what decides
whether a pane ever gets used. Source [`examples/fb.sh`](examples/fb.sh) from `~/.zshrc` or
`~/.bashrc` and `fb` does both — the pane first, the agent second:

```sh
fb() {
    case "$1" in
        -h|--help|-V|--version) command freebuff "$@"; return $? ;;   # one-shots: no pane
    esac

    if [ -n "${TMUX:-}" ] && [ -z "${FBTODO_NO_PANE:-}" ] && command -v fbtodo >/dev/null 2>&1; then
        tmux split-window "-${FBTODO_SPLIT:-v}" -l "${FBTODO_PANE_SIZE:-12}" -d \
            "fbtodo --instance-of $$ --stale-after 0"
    fi

    command freebuff "$@"
}
```

Four things in there are worth knowing:

- **`--instance-of $$`** binds the pane to the session *this shell* launches, not to the
  newest one in the directory — the difference between your pane and somebody else's list.
- **`-d`** places the pane without stealing the cursor, so the agent still starts here.
- **`--stale-after 0`** says a quiet transcript is never the reason to close: `0` is the
  tool's "never" for store silence, so the session's own lifecycle decides.
- **`FBTODO_NO_PANE=1`** turns the pane off; `FBTODO_SPLIT=h` and `FBTODO_PANE_SIZE=N` size
  it. A pane killed by hand comes back on its own while the session lives.

If you would rather have the short name on the tool itself, the alternative is one line:
`alias ft=fbtodo` — then `ft snap`, `ft bar`, `ft why`. Or `alias fb=fbtodo` if you would
rather launch the agent with `freebuff` and keep the short word for the pane.

### See it move

[`docs/demo`](docs/demo) is a demo harness: a fixture session, a driver that replays it, and
a [vhs](https://github.com/charmbracelet/vhs) tape that records the GIF over the top of the
real tool — the pane in your recording is `fbtodo pane`, reading a real file.

```sh
brew install vhs && docs/demo/record.sh     # → docs/demo/demo.gif
```

<details>
<summary><b>New to tmux? The four keys you need</b></summary>

| Keys | What it does |
|---|---|
| `tmux new -s work` | start a named session |
| `Ctrl-b "` | split the current pane, top and bottom |
| `Ctrl-b %` | split the current pane, side by side |
| `Ctrl-b` then arrow | move between panes |
| `Ctrl-b d` | detach (everything keeps running) · `tmux a -t work` re-attaches |

A pane is a rectangle inside a window; a window holds the panes; a session holds the
windows. fbtodo cares about that hierarchy because "underneath the session" has to mean
underneath the actual pane, not merely somewhere in the same window.
</details>

---

## Everyday commands

```sh
fbtodo                 # the live pane (default) — starts the watcher, exits with the session
fbtodo snap            # one snapshot, plain text
fbtodo json            # one snapshot, clean JSON
fbtodo bar             # "todos 3/5" — for a tmux status bar
fbtodo status          # instance, watcher, state file, scratch footprint, pace
fbtodo why             # why each pane is where it is
fbtodo pin --size 9    # pin this window's list pane
fbtodo stop            # stop the watcher
fbtodo prune           # enforce retention now
```

| Command | What it is for |
|---|---|
| *(no argument)* | the live pane; also starts the watcher for you |
| `snap` / `json` | the list as text or JSON — script it, or read it once |
| `bar` | a `todos 3/5` string for your tmux status line (`FBTODO_NO_PANE=1` if you only want this) |
| `status` | everything the tool thinks: which instance, which watcher, which state file, remembered pace |
| `why` | the first thing to run when a pane is somewhere unexpected |
| `pin` | force a pane's side or size, per window |
| `daemon` / `pane-watch` | the two background processes, usually started for you (`-f` keeps one in the foreground) |
| `stop` / `prune` | stop the watcher · enforce retention now |

`fbtodo -h` prints the full flag list, `fbtodo -V` the version.
Useful flags: `-s auto|cli|desktop` (which store), `-i` (poll interval), `--tick`
(how often the clock repaints), `--stale-after MIN`, `--goal-lines N`, `--once`.

---

## Make it yours

### Theme it

The palette is data, not code — layout never names a colour. Drop a
`~/.config/fbtodo/theme.json` (or a `./.fbtodo-theme.json` for one project), or set the
variables directly:

```sh
export FBTODO_ACCENT="#89b4fa"        # the title badge
export FBTODO_GRADIENT_START="#89b4fa"  # progress bar, left
export FBTODO_GRADIENT_END="#a6e3a1"    # progress bar, right
export FBTODO_FAINT="#6c7086"         # the frame's own ink
export FBTODO_MUTED="#9399b2"         # step durations, labels, the status strip
export FBTODO_TRACK="#313244"         # the progress bar's empty cells
```

Colours are 24-bit where the terminal takes them and the nearest 256-colour entry where it
does not; `FBTODO_TRUECOLOR=0|1` forces the choice.

### Size and placement

```sh
fbtodo pin --size 9                 # this window, both roles
fbtodo pin --side h --size 30       # beside the session, 30 columns wide
fbtodo pin --list                   # what is set, and where each half came from
fbtodo pin --clear                  # drop it
```

Without a pin, the pane is **remembered, not argued with**: the keeper records the size you
drag it to and opens the next one that way. `FBTODO_SPLIT` (`v` = below, `h` = beside) and
`FBTODO_PANE_SIZE` (default `12`) set the fallback.

### Turn parts off

```sh
export FBTODO_NO_PANE=1        # no panes at all (keep `fbtodo bar` in your status line)
export FBTODO_GOAL_LINES=0     # hide the goal heading
```

---

## Alerts on your phone

`notify/` holds the little programs that make the alerts real — five watches, a `bell.sh`
for the local chime, and a `phone.sh` that sends to **iMessage and/or
[ntfy](https://ntfy.sh)**. They are optional: fbtodo runs fine without them and skips any
that are missing.

| Watch | Question it answers |
|---|---|
| finish | the list is complete **and** the turn ended — ring, and push to the phone |
| ask | the agent is stopped on a question, waiting on you |
| stall | the agent stopped without ending its turn and work remains |
| pane | a session has no todo pane the keeper failed to put back |
| drop | the session died rather than ending |

Install by copying the directory into place and letting `phone.sh --init` generate the
config:

```sh
mkdir -p ~/.config/freebuff-notify
cp -R notify/* ~/.config/freebuff-notify/
~/.config/freebuff-notify/phone.sh --init     # writes phone.conf (chmod 600) for you
```

See [notify/README.md](notify/README.md) for the transports, the topics and the tests.

---

## How it works

Enough to be useful, without the tour of every corner. The genuinely deep detail — the
state-file contract, the notifier contracts and the pane
lifecycle — is in **[docs/INTERNALS.md](docs/INTERNALS.md)**.

### Where the list comes from: three stores

A coding agent's todo list is not a side channel — it is written to whatever transcript
store the client keeps. Freebuff keeps two, and picking the wrong one is the usual reason a
pane looks broken:

| Source | Store | Granularity |
|---|---|---|
| **cli** (`freebuff` in a terminal) | `~/.config/manicode/projects/<project>/chats/<ISO>/log.jsonl` | **live — mid-turn** |
| **desktop** (the app) | `~/.config/freebuff-desktop/projects/<slug>/desktop-v2.db` (SQLite) | per turn, while the app runs |

The CLI journal is the good one: append-only, written *during* the turn. Each record is a
JSON line, and a `write_todos` call lands in it the moment the agent makes it. fbtodo tails
that file, keeps every `write_todos` it has seen, and renders the newest. A new list
**replaces** the old one wholesale — state is never merged — and when the session changes,
the old list is dropped immediately instead of lingering.

`--source auto` (the default) prefers a live CLI chat for the current directory. An explicit
`-s cli|desktop` is never answered from cached watcher state unless that state describes the
same backend.

The instance to follow is found in this order:

1. `--instance-of PID` — the agent process launched by that shell.
2. `--watch-pid PID` — take this pid as the instance, outright.
3. Otherwise the agent processes are enumerated (`ps`), a cwd match on the current directory
   is preferred, and the **youngest** wins. A directory can host several sessions, and "the
   one started here" is what a human means.

### The two background processes

A pane cannot watch itself, so the work is split:

- **`fbtodo daemon`** follows one running instance: while it lives, the todo state is
  refreshed into `~/.freebuff/fbtodo-state.json`; when the process exits the daemon shuts
  itself down and removes its lock (`fbtodo-daemon.pid`). Anything that needs it starts it
  for you; `-f` keeps it in the foreground.
- **`fbtodo pane-watch`** is the pane keeper: one process per tmux *server*, 3 s cadence. It
  opens a pane for every local session that is in tmux and has none, and exits when the last
  one goes. It has its own lock and log, because being kept out by the watcher's lock *was*
  the bug (a watcher for another source holding `fbtodo-daemon.pid` left a local pane
  unopened).

`fbtodo status` reports both, plus the state file, the scratch footprint, and the pace it has
remembered for this project.

### Where the pane goes

The rule is: **a list pane opens under the pane its session is drawn in.** That is where a
glance looks for it. Two consequences, both learned the hard way:

- The pane is split off the *pane* running the session, never off its *window*: given a
  window, tmux uses that window's **active** pane, which is not necessarily the one running
  the agent.
- Every keeper pass re-derives the geometry (`tmux list-panes -a`) and `move-pane`s a drifted
  pane back — *in place*, so the pane keeps its process, its scrollback and its step clocks,
  and a step that is counting is not restarted.

A pane in **another window** than its session is left alone on purpose: that is an
arrangement the operator made. So is a pane wider than the session's (a full-width strip
under two panes).

### What the pane draws

A frame with a status strip, in a themeable palette.

- **Title** — `FBTODO` on an accent badge, in reverse video (so no background selector is
  needed), with the `done/total` count on the right.
- **Progress bar** — a gradient of `█` over a visible `░` track, with an eighth block for the
  one cell at the boundary.
- **Steps** — `✓` done, `▸` running, `·` pending; durations right-aligned against the frame's
  right wall so the numbers form a column.
- **Status strip** — badges the live state (`working 9m38s`, `waiting`, `done`) and spins
  while a step runs.
- **`PATCH` / `ALERT` row** — when there is something to say: the last outcome of an optional
  CLI-patch step, and the last thing the phone was told. Both are read from a log the
  producing step already writes, so neither costs a probe of its own.

The **plain** renderer is deliberately untouched — it is the machine-readable path, and the
`PATCH`/`ALERT` row appears there too, but only when the state carries it.

### Clocks, estimates, ETA

Which is where most of the care in this tool has gone.

- A step's clock starts when the watcher **sees it running**, not when the list was written,
  and it is measured from the tick. A step the watcher never saw running has no duration of
  its own — a quiet gap reads as a broken pane, so the pane names the reason instead.
- **Paint and poll are separate clocks.** The store is polled on `-i` (1 s); the *clock*
  repaints far more often (default every `--tick` 5 s, 1 s while a step is counting) so the
  seconds move even when nothing else does.
- **Estimates** ride the same row as the duration: `~1m` for a pending step, `4m10s / ~3m`
  for the active one. They come from this project's own history of completed steps
  (`~/.freebuff/fbtodo-tasks.json`), with the list's own pace as the fallback.
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
waiting to see is whether the agent rewrote its list. `--goal-lines N` (default 3, 1 in a
narrow strip) sets how many lines the heading may take; `0` hides it.

### Ending a turn: the bell

The journal records whether a turn **ended** (`shouldEndTurn`). A finished list is therefore
two facts, not one: every step ticked *and* the turn over. Only then does something ring.
The point is to distinguish "the agent is done" from "the agent is thinking", which a
`done/total` count alone cannot.

### The watches

Each is an **optional** script in `~/.config/freebuff-notify/`. fbtodo only times them and
reads nothing back — the notifier owns the decision *and* the "already pushed" record, so
nothing has to be kept in sync and a machine without the directory simply never asks.

The pane watch exists because every other failure here is quiet by construction: the keeper
has no stderr anybody reads, and its log records a pane that came **back**, never one that did
not. Ask it directly with
`pane-bell.py --print --keeper ~/.freebuff/fbtodo-pane-keeper.pid`.

Cadences: `--ask-seconds` (3 s), `--pause-seconds` (30 s), `--pane-bell-seconds` (60 s).
`0` switches any of them off. A fifth script, `drop-bell.py`, is
asked when a session **dies** rather than ending — a different question from "did it finish".

### Retention

Nothing grows without a cap. About hourly, and on demand with `fbtodo prune`, fbtodo enforces
retention on its own records: **500 task records**, **7 days**, and **1 MiB** of daemon log.
Override with `--max-records`, `--max-age-days`, `--log-cap-kb`.

---

## Settings

Precedence is the usual one: a command-line flag, then the environment, then a file.

<details>
<summary><b>Every environment variable</b></summary>

| Variable | Default | Meaning |
|---|---|---|
| `FBTODO_HOME` | `~/.freebuff` | where state, locks and logs live |
| `FBTODO_NOTIFY` / `_DROP` / `_ASK` / `_PAUSE` / `_PANE_BELL` | `~/.config/freebuff-notify/*.py` | the five watches |
| `FBTODO_ASK_SECONDS` / `_PAUSE_SECONDS` / `_PANE_BELL_SECONDS` | 3 / 30 / 60 | their cadences (0 = never) |
| `FBTODO_PANE_SECONDS` | 3 | how often the keeper looks |
| `FBTODO_SPLIT` / `FBTODO_PANE_SIZE` | `v` / `12` | default pane geometry |
| `FBTODO_NO_PANE` | — | set to disable panes entirely |
| `FBTODO_PATCH_LOG` / `_META` / `_ALERT_LOG` | `~/.config/freebuff-patch-watch/watch.log`, `~/.config/manicode/freebuff-metadata.json`, `~/.config/freebuff-notify/phone.log` | the optional `PATCH`/`ALERT` row |
| `FBTODO_ACCENT` / `_FAINT` / `_MUTED` / `_TRACK` | theme | palette overrides |
| `FBTODO_GRADIENT_START` / `_END` | theme | `#rrggbb`, or a raw SGR code like `1;36` for the accent |
| `FBTODO_TRUECOLOR` | auto | force 24-bit colour on or off |
| `FBTODO_TMUX` | `tmux` | the tmux binary/args to drive (a test knob) |

</details>

Theming is a file, not a flag: `~/.config/fbtodo/theme.json`, overridden by
`./.fbtodo-theme.json` in the working directory, overridden by the environment above.

Exit codes: `0` ok · `2` usage (including polling without a TTY) · `66` no instance or no
store · `75` the watcher failed to start.

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
| Different code running | The pane and the watcher hold what they started with: `fbtodo stop`, then re-open the pane. |

---

## FAQ

**Do I need Freebuff?**
Today, yes — see [Does it work with my setup?](#does-it-work-with-my-setup). The same idea
generalises to any agent that publishes a checklist, which is what
[the roadmap](#ideas--roadmap) is for.

**Do I need tmux?**
Only for the pane. `fbtodo snap`, `json` and `bar` are plain programs that work anywhere —
`bar` in a status line is a nice tmux-free setup.

**Does this send my code or my data anywhere?**
No. It reads local transcript files. The only outbound traffic is the notification kit, and
only if you install it.

**Will it slow the agent down?**
No — it reads files that are being written anyway.

**Why does the pane show nothing?**
The agent has not written a todo list yet, or the pane is bound to the wrong session. Run
`fbtodo status`, then `fbtodo why`.

**Can I use it for several sessions at once?**
Yes — one pane per session, each bound to its own. The keeper manages them all.

**Does it work on Windows?**
Not today; tmux is the missing piece. WSL works if you install it there.

**What if I just want a number in my status bar?**
`fbtodo bar` prints `todos 3/5`. Set `FBTODO_NO_PANE=1` so nothing else opens.

---

## Ideas & roadmap

Not built yet — this is the list of things that would make fbtodo appeal to a lot more
people, roughly in the order I would build them. Ideas, arguments and pull requests are all
welcome; each item below is scoped so one person could land it.

| Idea | Why it would matter |
|---|---|
| **`brew install fbtodo` + `uvx fbtodo` + a one-line installer** | Removing "clone and symlink" is the single biggest adoption lever. |
| **`fb` as a first-class command** — ship the launcher, or a `fbtodo init` that writes it into your shell startup file | One word to launch an agent *and* its pane; today it is a snippet to paste. |
| **`fbtodo board` — all live sessions in one pane** | People run two or three agents at once. One pane showing every session, its list and its clock beats switching windows. |
| **`fbtodo serve` — a read-only web mirror** | Watch from your phone or another machine, no ssh. Pairs with the notify kit you already have. |
| **Theme presets and `fbtodo theme`** (`catppuccin`, `gruvbox`, `nord`, `dracula`, `--preview`) | [`examples/`](examples) ships three hand-written presets today; a picker would make it a gallery. |
| **More transports: Slack, Discord, Telegram, Pushover, native macOS notifications** | iMessage + ntfy covers two platforms; a room of teammates is one webhook away. |
| **`fbtodo stats` — history and retro** | Per-project step times, slowest step types, a "where did the session actually spend its time" summary. This is the feature that makes people keep it installed. |
| **Generic sources: a JSON file, an MCP tool, another agent's todo format** | Drops the Freebuff requirement and makes the tool useful to everyone. |
| **zellij (and other multiplexers), plus a no-multiplexer fallback** | Same pane, more terminals — and somewhere to land for people who do not want tmux. |
| **Step-change hooks (`--on-step`, `--on-complete`)** | Lets people wire in their own scripts instead of asking for a transport. |
| **Accessibility modes: ASCII-only, no-emoji, high contrast** | `[x]`/`[>]`/`[ ]` and a plain palette are easy wins for terminals and screen readers. |

Good first issues, if you want something small: a `--no-emoji` renderer, a `zellij` probe
next to the tmux one, a new theme file, or one more transport in the notify kit (each has
tests to copy). The self-check below is the contract — a change is done when it still passes.

---

## Limits

- **The CLI journal is the only live source.** The Desktop store is read per turn.
- **A turn is the unit of "ended".** A long turn with a finished list rings nothing until the
  turn actually closes.
- **The pane needs tmux.** There is no terminal-UI fallback; `snap`/`json`/`bar` are the
  non-tmux interface.
- **The notification kit is macOS-leaning.** The chime uses macOS system sounds and the
  iMessage transport uses `osascript`; the ntfy transport is portable.
- **Not included:** the CLI-patch step whose log the `PATCH` row reads. It is glue around
  this tool — its contract is documented above, so you can write your own or ignore it.

---

## Examples

[`examples/`](examples) is a folder of copy-paste-able starting points, none of them
required: the [`fb` launcher](examples/fb.sh), a tmux status line, and three theme presets
(Catppuccin, Gruvbox, Nord). See [examples/README.md](examples/README.md).

---

## Development

The self-check drives the real thing: it runs against a throwaway `FBTODO_HOME`, spawns fake
instances (plain `sleep` processes) whose death must stop the watcher, and starts private tmux
servers so it never touches yours.

```sh
python3 fbtodo-selfcheck.py              # all of it, ~90-160 s
python3 fbtodo-selfcheck.py --list       # the phases, with line numbers and check counts
python3 fbtodo-selfcheck.py --only local-session
python3 fbtodo-selfcheck.py --only 0 --only "pane"
FBTODO_SELFCHECK_TIME=1 python3 fbtodo-selfcheck.py   # per-check cost
bash notify/test-freebuff-notify.sh      # the notification kit's own suite
```

`--only` re-runs the file with the phases you did not name cut out (`pass`, line numbers
preserved), keeping the cheap body every phase depends on — fixture setup, the unit checks.
That is the way to run one phase in ~20 s instead of the whole thing.

Two habits the suite encodes, because both have bitten:

- **Mute the notifiers.** Some phases start a *real* watcher, and a real watcher reading a
  fixture's dead-looking session pushes "freebuff dropped" to your phone.
- **Private tmux servers.** Every tmux call names a socket (`FBTODO_TMUX`), so a test can
  never move a pane in the server you are sitting in.

After changing the code you also have to restart anything already running: the pane and the
watcher hold the old code until they exit.

## License

MIT — see [LICENSE](LICENSE).
