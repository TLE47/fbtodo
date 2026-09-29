# fbtodo

**Watch your coding agent work — its checklist, live, in a pane beside it.**

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org)
[![No dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)](#start-here)
[![Platform: macOS | Linux](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey)](#start-here)
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
**next to the session that owns it**: below it by default, beside it with one flag, or
wherever you move it to. It
times each step from the moment the step actually starts, estimates what is left, and can
ring your phone when a session finishes, stalls, stops on a question, or loses its pane.

There is no single side for it. Below and beside are the two fbtodo opens itself; the rest of
the terminal is yours to arrange, and a layout you chose is left alone:

```
┌──────────┐┌──────────┐   ┌──────────────────┐   ┌────┬─────┐
│  agent   ││  FBTODO  │   │      agent       │   │todo│ any │
│          ││   4/7    │   ├──────────────────┤   │    │ way │
│          ││          │   │      FBTODO      │   │    │ you │
└──────────┘└──────────┘   └──────────────────┘   └────┴─────┘
   beside the session          below it             your own layout
```

Quick links: [Start here](#start-here) · [What is tmux?](#what-is-tmux) · [What it does](#what-it-does) ·
[Commands](#everyday-commands) · [Settings](#settings) · [FAQ](#faq) ·
[Ideas & roadmap](#ideas--roadmap)

Everything past the first two sections is optional: read it when you want it. If something
does not work, [troubleshooting](#troubleshooting) and the [FAQ](#faq) are the two places to
look.

---

## Start here

Two commands and one habit.

```sh
# 1. tmux — only the pane needs it
brew install tmux                     # macOS · `sudo apt install tmux` on Debian/Ubuntu

# 2. fbtodo: one clone, one symlink
git clone https://github.com/TLE47/fbtodo ~/Projects/fbtodo
mkdir -p ~/.local/bin && ln -sf ~/Projects/fbtodo/fbtodo ~/.local/bin/fbtodo
```

Then, in tmux, start your agent the way you always do:

```sh
tmux new -s work      # 1. a tmux session
freebuff              # 2. your agent, in it
#                      3. a FBTODO pane opens next to it, on its own
```

That is the whole setup. No config file, no hook, nothing to add to a prompt: fbtodo reads
the todo list the agent **already keeps for itself**. You need **Python 3.9+** (it is
already on macOS and most Linux) and **tmux**, and something to watch.

The one thing worth doing on day one is making sure the agent keeps one at all, since a
session that never writes a list has nothing to draw — that is the first thing in
[How it works](#getting-a-list-in-the-first-place).

<details>
<summary><b>The pane did not appear — the four usual reasons</b></summary>

| What you see | What it is |
|---|---|
| nothing at all | you are not in tmux: run `tmux new -s work`, then start the agent inside it |
| `fbtodo: command not found` | `~/.local/bin` is not on your `PATH` — the one line to add is in [Install, in detail](#is-it-on-your-path) |
| a pane, but no list | the agent has not written a todo list yet — [ask it for one](#getting-a-list-in-the-first-place); `fbtodo status` says what it sees, and the pane still shows the turn's clock and the newest calls the session *did* make |
| the pane is somewhere odd | `fbtodo why` — it names the pane, the anchor and where the placement came from |

</details>

## What is tmux?

tmux is a *terminal multiplexer*: it keeps terminal sessions alive when a window closes, and
it lets one window hold several **panes** — rectangles you split, each running its own
program. That is the only reason fbtodo wants it: the list is drawn in a small pane
**next to the pane your agent runs in** — below it by default, beside it with one flag — which
is where a glance looks for it.

| Keys | What it does |
|---|---|
| `tmux new -s work` | start a session called `work` |
| `Ctrl-b "` | split the current pane, top and bottom |
| `Ctrl-b %` | split the current pane, side by side |
| `Ctrl-b` then an arrow key | move between panes |
| `Ctrl-b d` | detach — everything keeps running · `tmux a -t work` comes back |

A pane is a rectangle inside a **window**, and a window lives in a **session**. fbtodo cares
about that hierarchy for one reason: "next to the session" has to mean next to the actual
pane, not merely somewhere in the same window.

**Do you have to use it?** Only for the pane. `fbtodo snap`, `json` and `bar` are plain
commands that work anywhere, and a status line is a perfectly good tmux-free setup — see
[`examples/tmux.conf`](examples/tmux.conf). Pane and no-pane are equally supported.

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

## Install, in detail

[Start here](#start-here) is the whole install; this is the same thing with the reasoning,
and the parts you only need if the pane is not quite what you wanted.

### Is it on your `PATH`?

`~/.local/bin` is not on `PATH` everywhere. If `fbtodo: command not found` was what you got,
this is the line to add to `~/.zshrc` or `~/.bashrc`:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

Then say hello — this needs no tmux and no session:

```sh
fbtodo -V          # the version
fbtodo snap        # one plain-text snapshot, when a session is running in this directory
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

### Placing the pane yourself

You do not have to: the pane keeper opens a pane by itself for any local session in tmux
that has none (that is the habit in [Start here](#start-here)). If you would rather place it
yourself, split the pane and bind it to *that* shell:

```
Ctrl-b "                   # split the current pane downwards
fbtodo --instance-of $$    # the list for the agent running in this pane
```

`--instance-of` is how a wrapper — or you — attaches a pane to a specific session rather
than to whatever is newest in the directory.

### One word: `fb` — optional, and the easy way in

**In plain words.** `fb` is a nickname you teach your terminal once. After that, typing those
two letters does four things for you:

1. **Keeps your agent up to date.** If a newer version was released, it installs it. You get a
   one-line note only when the version actually changed, so most launches look like nothing
   happened. Without this, you keep running an old version until you remember to update by hand.
2. **Makes room for the list.** It splits your terminal and puts the checklist in the new
   space, next to the agent — below it by default, beside it if you prefer (see
   [size and placement](#size-and-placement)).
3. **Starts the agent** in the space you were already in — exactly like typing `freebuff`.
4. **Tidies up after itself.** The list belongs to that one session: it goes away when the
   session does, and you can close it by hand at any time without losing anything.

**You do not have to do this.** If you are already in tmux, the list opens on its own — `fb`
only saves you the typing and keeps the agent current. Nothing else in this README depends on
it.

**To get it:** copy the box below into the file your terminal reads when it opens — `~/.zshrc`
on most Macs, `~/.bashrc` on many Linux machines — at the very end. Open a new terminal window,
type `fb`, and you are done. (If you would rather not touch that file, skip this whole
section.)

<details>
<summary><b>Show the code, and what each part of it does</b></summary>

This is [`examples/fb.sh`](examples/fb.sh) — the same code, as a file you can source.

```sh
fb() {
    case "$1" in
        -h|--help|-V|--version) command freebuff "$@"; return $? ;;   # one-shots: no pane
    esac

    _fb_refresh                      # stay on the current release

    if [ -n "${TMUX:-}" ] && [ -z "${FBTODO_NO_PANE:-}" ] && command -v fbtodo >/dev/null 2>&1; then
        tmux split-window "-${FBTODO_SPLIT:-v}" -l "${FBTODO_PANE_SIZE:-12}" -d \
            "fbtodo --instance-of $$ --stale-after 0"
    fi

    command freebuff "$@"
}

# `npm i -g freebuff`, quietly: the version moving is the point, the install is not.
_fb_refresh() {
    [ -n "${FREEBUFF_NO_REFRESH:-}" ] && return 0
    command -v npm >/dev/null 2>&1 || return 0

    local pkg before after
    pkg="$(command npm root -g 2>/dev/null)/freebuff/package.json"
    before=$(_fb_version "$pkg")

    if command npm i -g freebuff --no-fund --no-audit >/dev/null 2>&1; then
        after=$(_fb_version "$pkg")
        if [ -z "$before" ] && [ -n "$after" ]; then
            printf 'fb: installed freebuff %s\n' "$after" >&2
        elif [ -n "$after" ] && [ "$before" != "$after" ]; then
            printf 'fb: freebuff updated %s -> %s\n' "$before" "$after" >&2
        fi
    else
        printf '%s\n' "fb: npm i -g freebuff failed; launching the installed version" >&2
    fi
}

_fb_version() {                      # the version in a package.json, or nothing
    [ -r "$1" ] || return 0
    sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$1" | head -n 1
}
```

Five things in there are worth knowing:

- **`_fb_refresh`** is `npm i -g freebuff --no-fund --no-audit` before anything else, and it
  speaks only when the version moved (`fb: freebuff updated 1.2.0 -> 1.2.1` on stderr). A
  launch with no npm, or a registry having a bad day, still starts the installed version.
  Scripts and tests want `FREEBUFF_NO_REFRESH=1` — this is a network round trip per launch.
- **`--instance-of $$`** binds the pane to the session *this shell* launches, not to the
  newest one in the directory — the difference between your pane and somebody else's list.
- **`-d`** places the pane without stealing the cursor, so the agent still starts here.
- **`--stale-after 0`** says a quiet transcript is never the reason to close: `0` is the
  tool's "never" for store silence, so the session's own lifecycle decides.
- **`FBTODO_NO_PANE=1`** turns the pane off; `FBTODO_SPLIT=h` and `FBTODO_PANE_SIZE=N` size
  it. A pane killed by hand comes back on its own while the session lives.

Nothing here is required, and every name in it is a switch you can set before launching: if a
line of it scares you, ignore it — the two defaults do the useful thing already.

</details>

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

---

## Everyday commands

```sh
fbtodo                 # the live pane (default) — starts the watcher, exits with the session
fbtodo snap            # one snapshot, plain text
fbtodo json            # one snapshot, clean JSON
fbtodo bar             # "todos 3/5", or "todos -" with no list — a tmux status bar
fbtodo status          # instance, watcher, state file, scratch footprint, pace
fbtodo ledger          # each step's forecast beside what it actually took
fbtodo why             # why each pane is where it is
fbtodo pin --size 9    # pin this window's list pane
fbtodo stop            # stop the watcher
fbtodo prune           # enforce retention now
```

| Command | What it is for |
|---|---|
| *(no argument)* | the live pane; also starts the watcher for you |
| `snap` / `json` | the list as text or JSON — script it, or read it once |
| `bar` | a `todos 3/5` string for your tmux status line, and `todos -` when there is no list yet (`FBTODO_NO_PANE=1` if you only want this) |
| `status` | everything the tool thinks: which instance, which watcher, which build, which state file, remembered pace, which turn (`turn`), how far the evidence is from re-choosing the estimates' own constants (`refit readiness`) — and, with no list, why there is none and what the session has been doing instead (`last actions`) |
| `ledger` | the rows behind the scoreboard: every step's forecast vector, what each rung predicted, the span it was scored against and each rung's miss — or why a row could not be scored. `--days N`, `--limit N`, `--model M`, `--json` |
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

**Anywhere else in the terminal** is yours: those two are the sides fbtodo opens itself, and
any other layout you arrange — a strip along the top, a column of your own in its own window —
is treated as a decision, not as a pane that has drifted.

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

### Turning the alerts off

The phone is written by exactly one program — `phone.sh` — and it has a mute switch that
costs nothing to flip:

| You want | Do this |
|---|---|
| no phone alerts, bells still chime and log | `FREEBUFF_PHONE=off` |
| …and to keep it that way | `echo off > ~/.config/freebuff-notify/phone-state` |
| one launch silenced | `FREEBUFF_PHONE=off freebuff` |
| one watch off | `FBTODO_ASK_SECONDS=0`, `FBTODO_PAUSE_SECONDS=0`, `FBTODO_PANE_BELL_SECONDS=0` |
| one watch gone | point its path at a file that does not exist (`FBTODO_ASK=/none`) — a missing script is skipped, not an error |
| nothing leaving the machine at all | do not install the kit — that is also the default state |

`FREEBUFF_PHONE` accepts `off`, `0`, `false`, `no` or `disabled`, and it is read when a bell
sends, so a launch can silence itself. Quiet hours are not implemented yet; they are on the
[roadmap](#ideas--roadmap).

---

## How it works

Enough to be useful, without the tour of every corner. The genuinely deep detail — the
state-file contract, the notifier contracts and the pane
lifecycle — is in **[docs/INTERNALS.md](docs/INTERNALS.md)**.

### Getting a list in the first place

The list is the agent's own: it calls a tool called `write_todos`, and that call is what
fbtodo draws. fbtodo never invents a list and cannot add an item to one — so if a session has
never written one, there is nothing to show, and it is worth setting up before you look at an
empty pane and wonder what is broken.

**Ask once, in the session:**

```text
plan this as a todo list, and tick items off as you go
```

**Or make it a standing rule.** `AGENTS.md` — `~/AGENTS.md` for every project, or one inside a
project that should behave differently — is read at the start of every session. One bullet
there means every session keeps a list without being asked:

```md
## Progress
- Track every task with a todo list: write it before starting work and tick each step off as
  it lands — a one-line fix as much as a refactor.
```

[`examples/AGENTS.md`](examples/AGENTS.md) is that snippet on its own, plus the two smaller
conventions that make a list worth watching.

**How to tell whether there is a list at all** — four checks, none of which needs the pane:

| Ask | No list yet | With a list |
|---|---|---|
| `fbtodo bar` | `todos -` | `todos 3/5` |
| `fbtodo status` | `todos : — none yet (no write_todos call yet in this session)` | `todos : 3/6 done, list #6` |
| `fbtodo snap` | the reason, in words | the list itself |
| the pane | the same sentence | the list itself |

`bar` is the one built for a status line, and the dash is deliberate: `todos -` is something
you can read at a glance, where an empty string would look like the command failed.

**When a session has no list**, the pane says so in words rather than drawing an empty frame,
and the words differ by cause:

| The pane says | What happened |
|---|---|
| `no write_todos call yet in this session` | nothing has been written yet. Early in a session that is normal; a session deep into the work without one is not planning, which is the more interesting thing to notice |
| `new session — old list dropped, waiting for a new one` | the session changed, so the previous list was dropped rather than left up as if it were current |
| `last turn's list is done — waiting for this turn's list` | the previous turn's list was finished and your next request arrived. A finished list is dropped rather than left standing as *this* turn's 100%: the bar, the steps and the heading it was written under all go at once, and the turn clock and call feed below say what the new turn is doing meanwhile |
| `called so far: run_terminal_command 20, skill 3` | a second line, on a session that has been busy: what it *has* been doing instead. A pane that only says "no list yet" is shrugging; this one is diagnosing. Drawn when there are no calls it can name, below |
| `edited fbtodo  ·  2m ago` (up to three rows) | the newest calls the session actually made, newest first — the file it edited, the command it ran. This is the second source: the transcript records every call whether or not a list was written, so the pane has something factual to draw even when the model never calls `write_todos`. It is a record of work done, never a plan, so it carries no bar, no estimate and no ticks — nothing here is guessed |

**What a list-free session can still be measured by.** A plan supplies a *denominator* — seven
steps — and nothing in the store has one, so a session that never wrote a list gets no bar, no
percentage and no estimate from this pane: any of those would be a guess by construction. What
the store does have is a **boundary**. The journal logs your request on its own record as the
turn starts, so everything newer than it is *this* turn, and that gives one measured line:

```
turn 11m · 9 iterations · 2 files edited · 24 calls
```

The clock starts when your request arrived, the iterations are the model's own reported
turn-end flags, and the tally is the calls this turn actually made. `fbtodo status` prints the
same line as `turn`. It is status, not progress, and the two are deliberately different
things. Two honest edges: if the scan's own cap is reached before it finds your request, the
counts are lower bounds and say so (`9+ iterations`); and a transcript with no new record for
three minutes picks up `· quiet 4m` while the turn is still open — a fact about the file, not a
verdict on the work, because a ten-minute command writes nothing either.

**A list with no numbers in it** is the other half of the same case. A step gets a clock only
if the watcher saw it unfinished, so a list whose steps were never seen that way — every one
already ticked when it appeared, or a watcher that attached after the work had started — has no
durations to show. The pane says which of the two happened
(`no per-step times · the list arrived with every step already ticked`, or `… · no step was
ever seen running`) instead of looking like a stopped clock.

**A list that stops being re-written** is the third way a pane can look stuck while the agent
is working — and the reason it is worth understanding is that nothing is wrong with fbtodo. The
list carries its own age, on the pane (`LIST: #7 · 12m ago`) and in `fbtodo status`
(`list written : 12m ago`). When that number grows, the progress on screen is no longer the
agent's progress:
`write_todos` is the only record fbtodo ever sees, so a session that stops publishing leaves its
last list up, with its last percentages, and nothing can tell that a step has moved on. Two
things do still move, and both are worth reading: the step that is nominally running keeps its
clock, so it turns red and gains `[STUCK?]` once it passes twice its estimate, and a `now` or
`NUDGE` line appears the moment a request is waiting on a list that never answered it. The fix is on the agent's side — ask for the rewrite, or keep the
rule in your [`AGENTS.md`](examples/AGENTS.md) so it never needs asking.

With **no list at all** the pane is not blind either, and this is the point of having two
sources rather than one: `write_todos` is the only place a *plan* exists, but the transcript
records every call the session made, so the pane falls back to the newest of them
(`edited fbtodo  ·  2m ago`, `ran python3 fbtodo-selfcheck.py  ·  1m ago`) — facts, not a
guess at a plan, which is why they carry no bar and no ticks. A model that never calls
`write_todos` therefore costs you the plan and the estimates, not the pane.

### Where the list comes from: two stores

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

The rule is: **a list pane opens next to the pane its session is drawn in** — below it by
default, beside it with `FBTODO_SPLIT=h` or `fbtodo pin --side h`. That is where a glance
looks for it. Two consequences, both learned the hard way:

- The pane is split off the *pane* running the session, never off its *window*: given a
  window, tmux uses that window's **active** pane, which is not necessarily the one running
  the agent.
- Every keeper pass re-derives the geometry (`tmux list-panes -a`) and `move-pane`s a drifted
  pane back — *in place*, so the pane keeps its process, its scrollback and its step clocks,
  and a step that is counting is not restarted.

A pane in **another window** than its session is left alone on purpose: that is an
arrangement the operator made. So is a pane wider than the session's — a full-width strip
across the window is accepted wherever it sits, top or bottom.

That leniency is the escape hatch for the rest of the terminal: a strip along the top, a
column of your own in another window, a pane you moved somewhere deliberate. fbtodo opens
the two sides it knows itself (below and beside) and treats anything else it cannot read as a
mistake as a decision — it is your terminal, and the pane goes where you put it.

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
- **`REFIT` row** — and only once the log has 30 closed steps behind it, because before that
  a progress bar toward a number nobody can read is just a row the list loses:

  ```
  REFIT  12/78 decided · 31 scored (15% to judging the clip)
  REFIT  ready · 78 decided over 214 scored — the clip can be judged
  ```

  It counts the steps the young-list **pace bound actually moved** rather than all of them,
  since on most steps the bounded and unbounded paces are the same number — so the left number
  is the one that governs, and `fbtodo status` prints the same count in words.

The **plain** renderer is deliberately untouched — it is the machine-readable path, and the
`PATCH`/`ALERT` rows appear there too, but only when the state carries them.

### Clocks, estimates, ETA

Which is where most of the care in this tool has gone.

- A step's clock starts when the watcher **sees it running**, not when the list was written,
  and it is measured from the tick. A step the watcher never saw running has no duration of
  its own — a quiet gap reads as a broken pane, so the pane names the reason instead.
- **Paint and poll are separate clocks.** The store is polled on `-i` (1 s); the *clock*
  repaints far more often (default every `--tick` 5 s, 1 s while a step is counting) so the
  seconds move even when nothing else does.
- **Estimates** ride the same row as the duration: `~1m` for a pending step, `4m10s / ~3m`
  for the active one. The number walks a ladder of evidence: what steps of the same **size**
  took — the calls a step has made so far, log-binned (`calls2` is 4-7 calls) — else what
  steps of the same **kind** took, read from the wording (a `run`-ish step's usual number of
  calls times what a call costs), blended half-and-half with the list's own pace. That blend
  is a tail-smoother rather than a clear win — measured on your own replay it is a coin flip
  on the typical step and only plainly better on the worst ones — so `FBTODO_BLEND_WEIGHT`
  turns it down if you would rather have the pace alone. All of it
  lives in `~/.freebuff/fbtodo-tasks.json`. A step that has made no calls *and* has nothing
  to blend still falls back to the pace, so a fresh log behaves exactly as it always did.
- **A step under 10 s is a list flip, not work.** It is shown on its row, but it sets no pace
  and enters no memory: measured 2026-09-29, a 2 s flip had once projected a whole list at
  `~2s` while the next step took 1m53s.
- **When the evidence disagrees, you get a range.** Three finished steps of 10 s, 2 m and
  20 m do not entitle the pane to say `~2m` and stop there, so it says `~2m (10s–20m)`
  instead — on the step rows, on `EST REM`, and in `fbtodo status`. Below a 3× spread the
  plain number is kept (2 m from 2 m *is* 2 m), and `fbtodo status` prints the sample count
  behind every number, so `~2m, 1 sample` and `~2m, 9 samples` are told apart. A piped
  `snap` deliberately keeps the bare `~2m`: that token is a published contract.
- **Every estimate is scored once the step closes**, and `fbtodo status` shows the running
  result by source (e.g. `shape 1.8x median over 9 · blend 2.2x median over 40 · pace 3.4x
  median over 31`; the numbers
  are whatever your own steps did).
  A factor of 1.0x would be exact; two steps that took twice their estimate and half of it
  count the same. That line is the only honest answer to "are these numbers getting
  better?", and it is also what tells you whether the size memory is pulling its weight on
  your own work.
- **The score has a second line, and it is the one to trust**: `forecast error`. Each step's
  prediction is written down **once**, on the first poll that sees it running — before
  anything about its size is known — and every rung is scored on every step, so the rungs are
  compared on the same population. The `estimate error` line above scores the number the pane
  was showing as the step *closed*, whose size key is built from calls the step had already
  made by then; that can flatter a rung that recognises a step rather than predicting it.
  A step the watcher only picked up mid-flight (a restart) is flagged and left out of the
  score rather than counted as a forecast it never was, and `status` says how many were set
  aside. The memory behind it is kept for 60 days / 2000 records — about a month of
  real use — because the estimates are the one thing here meant to improve with use. A step
  that has finished is kept even after the agent rewrites its list: a measured span *is* the
  evidence both score lines are computed from, and dropping it left the live scores looking
  at nothing but the list on screen.
- **`fbtodo ledger` is those rows themselves**: one per step, newest first, with what each
  rung predicted, the span it was scored against and each rung's miss — and the honest note
  when a row could not be scored (still running, too short to be evidence, or stamped after
  the step had already started). `status` gives the average; `ledger` gives the argument.
- **`status` also says when the numbers could be re-chosen**, as `refit readiness`: how many
  closed steps carry a forecast (a median wants ~30 before one step stops being the whole
  distribution), and how often the young-list bound actually *changed* a number — the bound is
  consulted on the first steps of a list and moves nothing on most of them, so that second
  count is the one that governs it, and the line extrapolates the spans it would take.
- Past twice the estimate a step is marked `[STUCK?]` — a hint, not a verdict.
- The **list's own age** rides on `LIST:` (`LIST: #7 · 12m ago`), so a list the agent has
  stopped re-writing is visible while a step's clock is still counting. A narrow strip spends
  the model's pace first, then that age, and only then the whole `LIST:` field.
- With **no list at all** the pane falls back to the calls the session actually made
  (`edited fbtodo · 2m ago`, newest first, three rows). The transcript records every call
  whether or not one of them was a `write_todos`, so a model that never writes a list costs
  you the plan and the estimates — not the pane. It is a record of work done and never a
  plan, so nothing on it is guessed: no bar, no estimate, no tick.
- A **finished list is dropped the moment the next turn starts**, so it can never be read as
  the new turn's progress: the bar, the steps and the heading it was written under all go at
  once, and the pane says `last turn's list is done — waiting for this turn's list` until a new
  one arrives. What the age can still catch is therefore work continuing *inside* the turn that
  wrote the list: every step ticked, and the session writing for ten minutes past it. Then the
  age gains `[STALE?]` and `fbtodo status` says `list behind : yes` — the one case fbtodo can
  actually prove, and stated as a question because it is still a guess: the agent may be
  tidying up, or it may have decided the rest of the work needed no list at all. A list with
  steps *left* on it is never flagged, because a long step is not a stale list — its own clock,
  and `[STUCK?]` past twice its estimate, are the record there.
- The bar carries `EST REM` and an `ETA`. Where a row has room for only one of them, the
  spread takes the place of the ETA — it says something the bare number cannot, while the
  ETA is that same number told as a clock. `fbtodo status` shows the remembered pace, its
  spread, and where each step's number came from.

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
retention on its own records: **2000 task records**, **60 days**, and **1 MiB** of daemon log.
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
| `FBTODO_LABEL_FLOOR` | `10` | estimates: a finished span under this many seconds is not evidence — it sets no pace and moves no memory (`0` keeps every span, the pre-4.23.0 behaviour) |
| `FBTODO_BLEND_WEIGHT` | `0.5` | estimates: how much a *waiting* step's number comes from its wording rather than the list's pace, `0`–`1` (`0` = pace only; `1` = wording only, which drops the pace and mis-sizes the tail) |

</details>

The two estimate knobs above are flags as well, so they can be set per run rather than per
shell: `--label-floor SEC` and `--blend-weight W`. Both are forwarded to the watcher when it
is started, so `fbtodo --label-floor 0 daemon` really does keep every span. Raising the floor
makes the estimates calmer (short flips stop setting the pace); lowering the weight makes the
pane trust the list's own pace over what a step's wording suggests.

Theming is a file, not a flag: `~/.config/fbtodo/theme.json`, overridden by
`./.fbtodo-theme.json` in the working directory, overridden by the environment above.

Exit codes: `0` ok · `2` usage (including polling without a TTY) · `66` no instance or no
store · `75` the watcher failed to start.

---

## Troubleshooting

| Symptom | First thing to try |
|---|---|
| Pane is empty | `fbtodo bar` prints `todos -` when there is no list at all, and `fbtodo status` says why. If that is the answer, [ask the agent for one](#getting-a-list-in-the-first-place), or add the line to your `AGENTS.md` so it never needs asking — otherwise the pane is bound to the wrong session, and `fbtodo why` is next. |
| Pane exits immediately | exit `66`: no running instance, or no store found for the cwd. Try `--instance-of PID` or `-p`. |
| Pane gone after a while | `--stale-after` (default: 60 min of store silence). `0` = never. |
| Pane not where you want it | `fbtodo why` — read the `source` on the line, it is usually the answer. Then `fbtodo pin`. |
| Pane killed mid-session | It comes back within ~3 s. If it does not, the pane watch is what tells you. |
| List shows old progress | Check the list's own age: `LIST: #7 · 12m ago` in the strip, or `list written` in `fbtodo status`. `[STALE?]` beside that age (and `list behind : yes` in `status`) means the list is finished and the session has worked on — the agent owes a new list. If it is growing, the agent has stopped calling `write_todos` — count them in the journal, and [ask for the rewrite](#getting-a-list-in-the-first-place). A `goal`/`now` pair that disagree is the pane saying the same thing about a *request* that is waiting. |
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
Two different problems, and the pane tells you which. Either the session has not written a
todo list yet — the pane says so in those words, and [asking for one](#getting-a-list-in-the-first-place)
fills it — or the pane is bound to the wrong session. `fbtodo status` answers the first,
`fbtodo why` the second.

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
required: the [`fb` launcher](examples/fb.sh), a tmux status line, three theme presets
(Catppuccin, Gruvbox, Nord), and
[the rule that makes an agent keep a list at all](examples/AGENTS.md). See
[examples/README.md](examples/README.md).

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
