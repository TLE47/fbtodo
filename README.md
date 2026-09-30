# fbtodo

**Watch your coding agent work — its checklist, live, in a pane beside it.**

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org)
[![No dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)](#start-here)
[![Platform: macOS | Linux](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey)](#start-here)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

```
╭──  FREEBUFF TODOS  ──────────────── watcher: pid 4813 · v4-flash ──╮
│ 🎯 Goal: fit the pane heading whole                                │
│   ➔  Split the drawing code into roles                9m38s [~10m] │
│                                                                    │
│   [█████▊░░░░░░░░░░░░░░]  29% (2/7) | EST REM 11m21s | ETA 12:09   │
│  ⠏ WORKING  │ LIST: #47 · 11m ago │ LIVE: 11:57:59                 │
╰───────────────────────────────────────────────────────────────────[...]
```

fbtodo mirrors the todo list a [Freebuff](https://freebuff.com) session is working through — the
list the agent keeps for itself — in a small framed pane **next to the session that owns it**. It
times each step from the moment it starts, estimates what is left, and can ring your phone when a
session finishes, stalls, stops on a question, or loses its pane.

[Start here](#start-here) · [Commands](#everyday-commands) · [Settings](docs/SETTINGS.md) ·
[FAQ](#faq) · deep detail: [INTERNALS](docs/INTERNALS.md) · [SOURCES](docs/SOURCES.md) ·
[ESTIMATES](docs/ESTIMATES.md) · [decisions](docs/decisions/README.md) · [roadmap](docs/ROADMAP.md).

---

## Start here

Two commands and one habit. Only the pane needs tmux.

```sh
brew install tmux                                       # macOS: `apt install tmux` on Debian/Ubuntu
git clone https://github.com/TLE47/fbtodo ~/Projects/fbtodo
mkdir -p ~/.local/bin && ln -sf ~/Projects/fbtodo/fbtodo ~/.local/bin/fbtodo
. ~/Projects/fbtodo/examples/fb.sh                      # add to ~/.zshrc: `fb` = agent + pane

tmux new -s work                                        # 1. a tmux session
fb                                                      # 2. your agent, and its pane, in one word
# 3. the FBTODO pane opens next to it, on its own
```

**Use `fb`.** Sourcing [`examples/fb.sh`](examples/fb.sh) once in your shell startup file turns
`fb` into the whole setup: it refreshes the agent, opens the pane beside the session it is about
to start, then runs `freebuff` in the space you were already in. One word instead of three steps
(`FREEBUFF_NO_REFRESH=1` skips the update round trip). Plain `freebuff` works too — the pane
opens all the same.

No config file, no hook, nothing to add to a prompt: fbtodo reads the todo list the agent
**already keeps for itself**. You need **Python 3.9+** and **tmux**. The one thing worth doing
on day one is making sure the agent keeps a list at all — see [How it works](#how-it-works).

**Pane did not appear?** Not in tmux, `~/.local/bin` not on `PATH`, no list written yet, or the
pane is somewhere odd — the four fixes are in
[docs/decisions](docs/decisions/README.md#the-pane-did-not-appear).

**tmux** is a *terminal multiplexer*: sessions survive a closed window, and one window holds
several **panes** (`Ctrl-b "` splits top-and-bottom, `Ctrl-b %` side-by-side). fbtodo wants only
that, and only for the pane — `snap`, `json` and `bar` work anywhere, so a status line is a fine
tmux-free setup ([`examples/tmux.conf`](examples/tmux.conf)). The pane is drawn next to the
actual *pane* your agent runs in, not just the same window ([why](docs/decisions/README.md)).

---

## What it does

If you have watched an agent chew through a long task, you know the feeling: the terminal
scrolls, and you have no idea whether it is two minutes or twenty from done. The agent keeps a
plan — a todo list it ticks off — in a transcript file, not on your screen. fbtodo puts it where
you are already looking.

| You want to know | fbtodo shows |
|---|---|
| What is it doing right now? | the list — `✓` done, `➔` running, `○` to come — with a clock per step |
| How much is left? | `EST REM` and an `ETA`, learned from this project's own history |
| Is it stuck, done, or waiting on me? | `[STUCK?]` past twice the estimate; the bell only when the list is done **and** the turn ended; an `ask` watch for a question |

Nothing is guessed from the model's intent: every number comes from a file being written anyway.
The ladder of evidence behind `~2m`, the range when it disagrees, and the ledger that scores the
result are in [docs/ESTIMATES.md](docs/ESTIMATES.md).

---

## What it reads

No `.cursorrules` snippet, no hook: if the agent is working through a list, the pane sees it.

| Your setup | What it reads | Live? |
|---|---|---|
| **Freebuff CLI** | `~/.config/manicode/projects/<project>/chats/<ISO>/log.jsonl` | **yes — mid-turn** |
| **Freebuff Desktop** | the app's own SQLite store | per turn, while the app runs |
| anything that writes a state JSON | `fbtodo push`, or `-s file:PATH` | **yes — as often as it is written** |
| Claude Code, Aider, Cursor, … | — | not yet — [SOURCES.md](docs/SOURCES.md) |

`--source auto` (the default) picks the right store for the current directory; `-s cli|desktop` says
so explicitly. **One self-contained Python file**, no dependencies (`export PATH="$HOME/.local/bin:$PATH"`
if `fbtodo: command not found`).

**`fb` is the convenient way in** — two letters that keep your agent updated, make room for the
list, start the agent, and tidy the pane away when the session ends
([`examples/fb.sh`](examples/fb.sh), sourced once from your shell startup file). By hand, the
same dance is `Ctrl-b "` then `fbtodo --instance-of $$`.

---

## Everyday commands

```sh
fbtodo                 # the live pane (default) — starts the watcher, exits with the session
fbtodo snap | json     # one snapshot: plain text, or clean JSON to pipe anywhere
fbtodo bar             # "todos 3/5", or "todos -" with no list — a tmux status bar
fbtodo status          # instance, watcher, state file, pace, who won the duel
fbtodo ledger          # each step's forecast beside what it actually took
fbtodo why | pin       # where a pane is, and forcing its side or size per window
fbtodo stop | prune    # stop the watcher · enforce retention now
```

| Command | What it is for |
|---|---|
| `bar` | a `todos 3/5` string for a status line, and `todos -` with no list (`FBTODO_NO_PANE=1` if you only want this) |
| `push` | make a state JSON on stdin the live list — [SOURCES.md](docs/SOURCES.md) |
| `status` | everything the tool thinks: instance, watcher, pace, `refit readiness`, why there is no list |
| `ledger` | the rows behind the scoreboard: each step's forecast, what each rung predicted, the span it was scored against, each rung's miss. `--days N`, `--limit N`, `--model M`, `--json` |

`fbtodo -h` prints the full flag list, `fbtodo -V` the version; notable flags are
`-s auto|cli|desktop|file:PATH`, `-i`, `--tick`, `--stale-after MIN`, `--goal-lines N`.

---

## Make it yours

### Theme it

The palette is data, not code — layout never names a colour. Drop a
`~/.config/fbtodo/theme.json` (or a `./.fbtodo-theme.json` per project), or export:

```sh
export FBTODO_ACCENT="#89b4fa"   # badge, the running row, the bar's left stop
export FBTODO_ACTIVE="#cdd6f4"   # the row being worked on, and its goal
export FBTODO_SUCCESS="#a6e3a1"  # a finished tick, a clean patch, the bar's right stop
export FBTODO_MUTED="#9399b2"    # durations, labels, the status strip
export FBTODO_FAINT="#6c7086"    # the frame's own ink, pid, model
export FBTODO_TRACK="#313244"    # the progress bar's empty cells
```

Six roles, plus two gradient stops defaulting to the accent and the success colour. A value is a
`#rrggbb` colour or a raw SGR list such as `1;36` — the only two forms, since it goes *inside* an
escape sequence; anything else is ignored (`theme_problems` in `fbtodo status`). Three presets in
[`examples/`](examples). Everything the pane prints that is not its own — a step's name, the goal,
a command string — is filtered of escape, control, delimiter and bidi characters, at the source
and again as the frame is built ([INTERNALS.md](docs/INTERNALS.md)).

### Size and placement

```sh
fbtodo pin --size 9 --side h --size 30   # this window's pane, or beside the session
fbtodo pin --list                        # what is set, and where each half came from
```

Without a pin the pane is **remembered, not argued with**: the keeper records the size you drag
it to and opens the next one that way (`FBTODO_SPLIT` — `left`/`right`/`top`/`bottom`, or `h`/`v` —
and `FBTODO_PANE_SIZE`, default 12 lines). Any
other layout you arrange is a decision, not drift; to turn panes off: `FBTODO_NO_PANE=1`.

---

## Alerts on your phone

`notify/` holds the programs that make the alerts real — five watches, a `bell.sh` for the local
chime, and a `phone.sh` sending to **iMessage and/or [ntfy](https://ntfy.sh)** — all optional:
fbtodo skips any that are missing.

| Watch | Question it answers |
|---|---|
| finish | the list is complete **and** the turn ended — ring, and push to the phone |
| ask | the agent is stopped on a question, waiting on you |
| stall / drop | the agent stopped without ending its turn and work remains / the session died rather than ending |
| pane | a session has no todo pane the keeper failed to put back |

Install: `mkdir -p ~/.config/freebuff-notify && cp -R notify/* ~/.config/freebuff-notify/ &&
~/.config/freebuff-notify/phone.sh --init` (writes `phone.conf`, chmod 600);
[notify/README.md](notify/README.md) has the transports and tests. Mute the phone with
`FREEBUFF_PHONE=off` or `echo off > ~/.config/freebuff-notify/phone-state`; one watch off is
`FBTODO_ASK_SECONDS=0` and friends.

---

## How it works

**Getting a list in the first place.** The list is the agent's own: it calls a tool called
`write_todos`, and that call is what fbtodo draws — it never invents one. Ask once in the session
(*"plan this as a todo list, and tick items off as you go"*), or make it a standing rule in
`AGENTS.md`; [`examples/AGENTS.md`](examples/AGENTS.md) is that snippet on its own.

**Telling whether there is a list:** `fbtodo bar` prints `todos -` with no list and `todos 3/5`
with one, and `fbtodo status` says why there is none (`no write_todos call yet in this session`,
`new session — old list dropped`, `last turn's list is done — waiting for this turn's list`).

With no list the pane is not blind: the transcript records every call the session made, so it
draws the newest of them (`edited fbtodo · 2m ago`) — facts, not a guess, so no bar and no ticks.
The state-file contract, the frame's guarantees and the pane's lifecycle are in
[docs/INTERNALS.md](docs/INTERNALS.md).

---

## Troubleshooting

| Symptom | First thing to try |
|---|---|
| Pane is empty | `fbtodo bar` prints `todos -` when there is no list at all, and `fbtodo status` says why. If that is the answer, [ask the agent for one](#how-it-works) — otherwise the pane is bound to the wrong session or the list is done. |
| Pane exits or vanishes | exit `66` is no running instance or no store for the cwd (try `--instance-of PID` or `-p`); a pane that goes after a while is `--stale-after` (default 60 min of store silence). |
| Pane not where you want it | `fbtodo why` — read the `source` on the line, it is usually the answer. Then `fbtodo pin`. A pane killed mid-session comes back within ~3 s. |
| List shows old progress | Check the list's own age (`LIST: #7 · 12m ago`, or `list written` in `status`). `[STALE?]` means the list is finished and the session worked on — the agent owes a new list. |
| A step with no duration | The watcher never saw it running — a step's clock starts at the tick, not at the list's mtime. And a pane running old code holds what it started with: `fbtodo stop`, then restart. |

---

## FAQ

<details open>
<summary><strong>Do I need Freebuff?</strong></summary>

For the built-in stores, yes — but a state JSON is enough: `fbtodo push` and `-s file:PATH` take any agent or job that can write one file ([docs/SOURCES.md](docs/SOURCES.md)).
</details>

<details>
<summary><strong>Does this send my code or my data anywhere?</strong></summary>

No — it reads local transcript files; the only outbound traffic is the notification kit, and only if you install it.
</details>

<details>
<summary><strong>Will it slow the agent down?</strong></summary>

No: it reads files that are being written anyway.
</details>

<details>
<summary><strong>Why does the pane show nothing?</strong></summary>

Either the session has not written a todo list yet (`fbtodo status`), or the pane is bound to the wrong session (`fbtodo why`).
</details>

<details>
<summary><strong>Several sessions at once?</strong></summary>

Yes — one pane per session, each bound to its own.
</details>

<details>
<summary><strong>Windows?</strong></summary>

WSL only; tmux is the missing piece.
</details>

<details>
<summary><strong>Just want a status-bar number?</strong></summary>

`fbtodo bar` prints `todos 3/5`; set `FBTODO_NO_PANE=1`.
</details>

---

## Development

The self-check drives the real thing: a throwaway `FBTODO_HOME`, fake instances whose death must
stop the watcher, and private tmux servers so it never touches yours.

```sh
python3 fbtodo-selfcheck.py              # all of it, ~90-160 s
python3 fbtodo-selfcheck.py --list       # the phases, with line numbers and check counts
python3 fbtodo-selfcheck.py --only local-session
bash notify/test-freebuff-notify.sh      # the notification kit's own suite
```

`--only` re-runs the file with the phases you did not name cut out (`pass`, line numbers
preserved), keeping the cheap body every phase depends on — one phase in ~20 s. Two habits the
suite encodes, because both have bitten: **mute the notifiers** (some phases start a *real*
watcher, and one reading a fixture's dead-looking session pushes "freebuff dropped" to your
phone), and **private tmux servers** (every tmux call names a socket).

## License

MIT — see [LICENSE](LICENSE).
