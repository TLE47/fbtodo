# fbtodo

**Watch your coding agent work — its checklist, live, in a pane beside it.**

[![CI](https://github.com/TLE47/fbtodo/actions/workflows/ci.yml/badge.svg)](https://github.com/TLE47/fbtodo/actions/workflows/ci.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org)
[![No dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)](#installation)
[![Platform: macOS | Linux](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey)](#installation)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](https://github.com/TLE47/fbtodo/blob/main/LICENSE)

fbtodo displays your [Freebuff](https://freebuff.com) agent's todo list in a side pane while it works. It shows:
- **What's done, running, and next** — with live timers
- **How much time is left** — estimates based on your project history
- **If it's stuck or waiting** — with optional alerts to your phone
- **One keypress to hush it** — press `m` in the pane and the phone stays quiet until the list finishes

No configuration: it reads the list your agent already keeps. Freebuff's list turns up on its own;
anything else can push one over stdin — see [docs/SOURCES.md](https://github.com/TLE47/fbtodo/blob/main/docs/SOURCES.md).

**[Installation](#installation)** · **[Commands](#commands)** · **[FAQ](#faq)** · [Install guide](https://github.com/TLE47/fbtodo/blob/main/docs/INSTALL.md) · [Settings](https://github.com/TLE47/fbtodo/blob/main/docs/SETTINGS.md) · [Deep docs](https://github.com/TLE47/fbtodo/blob/main/docs/INTERNALS.md)

---

## See it work

![fbtodo's pane working through a scripted session](https://raw.githubusercontent.com/TLE47/fbtodo/main/docs/demo/demo.webp)

The clip above shows - `fbtodo pane` in real time. It monitors a scripted session through eight steps, updating continuously until all tasks are marked complete *and* the session ends—the exact trigger required for the notification bell.

### Side-by-Side View 
To see how the pane mirrors the active session, here is the side-by-side pairing: the scripted session on the left, and the fbtodo pane tracking it on the right:

![the scripted session and the pane, side by side](https://raw.githubusercontent.com/TLE47/fbtodo/main/docs/demo/side-by-side.webp)

>**Note**: The recording script exports animations as lossless WebP files rather than standard video formats. This ensures crisp, pixel-perfect inline rendering directly within GitHub Markdown.

---

## Installation

**One line** — takes the first method your machine already has (Homebrew, `uv`,
`pipx`, a plain venv, or a clone), and never runs as root:

```sh
curl -fsSL https://raw.githubusercontent.com/TLE47/fbtodo/main/install.sh | sh
```

**Homebrew**

```sh
brew install TLE47/tap/fbtodo     # brew taps it for you
```

**uv / pipx** — an isolated venv, nothing to manage on your `PATH`:

```sh
uvx fbtodo                        # run it once, install nothing
uv tool install fbtodo            # ...or keep it
pipx install fbtodo
```

**From the checkout** — no install at all:

```sh
brew install tmux                 # or: apt install tmux
git clone https://github.com/TLE47/fbtodo ~/Projects/fbtodo
mkdir -p ~/.local/bin && ln -sf ~/Projects/fbtodo/fbtodo ~/.local/bin/fbtodo
tmux new -s work
fbtodo                            # opens the pane automatically
```

Requirements: **Python 3.9+** and **tmux**. If the pane does not appear, run
`fbtodo doctor` — it names what is missing. Every route, with pinned versions and
how to uninstall, is in [docs/INSTALL.md](https://github.com/TLE47/fbtodo/blob/main/docs/INSTALL.md).

### Using the `fb` shortcut (optional)

For a one-word launcher that updates and manages everything:

```sh
fbtodo init          # writes the launcher + adds the source line to your shell rc
fb                   # now use 'fb' instead of 'fbtodo'
```

`fbtodo init` detects your shell (`$SHELL`, then the launching process), writes the
`fb` function into `~/.config/fbtodo/`, and adds the `source` line to your startup
file — all idempotently, so it's safe to re-run after upgrading. It knows the
**Bourne family** (`bash`, `zsh`, `ksh`, `mksh`, `dash`, `sh` → a POSIX body and
`fb.sh`) and **fish** (a native body and `fb.fish`), and picks the right startup
file for each. You can still source the example file by hand if you prefer:

```sh
. ~/Projects/fbtodo/examples/fb.sh    # manual install — adds 'fb' to this shell only
fb                                    # now use 'fb' instead of 'fbtodo'
```

`--shell SHELL` overrides auto-detection, `--startup-file PATH` names one for a
shell not in the table, and `--dry-run` previews without writing.

### Updating and pinning

Each installer updates itself — `brew upgrade fbtodo`, `uv tool upgrade fbtodo`,
`pipx upgrade fbtodo`. To pin a release, name it; the tag is the version:

```sh
uv tool install fbtodo==4.30.0
pipx install fbtodo==4.30.0
pipx install "git+https://github.com/TLE47/fbtodo@4.30.0"   # from the tag
```

> No Python dependencies are required, though displaying the pane still needs tmux.

---

## How it works

Your agent writes a todo list (by calling `write_todos`). fbtodo watches that list and displays it in a side pane. The pane updates in real time as the agent works through tasks.

**No configuration needed** — fbtodo reads the list your agent already creates. Just make sure your agent is keeping one. You can ask it once per session: *"Plan this as a todo list and check off items as you go."*

---

## Commands

```sh
fbtodo              # watch the pane (default)
fbtodo bar          # show "todos 3/5" in your status bar
fbtodo snap         # print one snapshot
fbtodo board        # every live session in one frame (--live keeps redrawing)
fbtodo status       # show pane info and why it might be empty
```

### Pane keys

While you are looking at a pane, the keyboard is the quickest way to mute the phone:

| key | what it does |
|---|---|
| `m` | quiet until this list finishes, then it rings again on its own |
| `M` | quiet until you press `u` |
| `u` | loud again |

`--no-keys` (or `FBTODO_PANE_KEYS=off`) turns the keys off in a pane whose terminal it should not
read, and the same switch is `fbtodo mute` for a script, a status row or any shell that is not a
pane — see [Quiet while you read](#quiet-while-you-read).

Running more than one agent? `fbtodo board` draws them all at once — one row per live session
with its list, its heading and its clock, newest activity first, instead of one pane per window.

**Other commands:** `ledger` (forecast vs. actual), `why` (pane location), `pin` (resize pane), `keep` (pane-repair switch), `mute` (the notification switch, without a keystroke: `on` / `off` / `list` / `until-done`), `locks` (claim-file audit; `--fix` clears leftovers and ends untied processes, `--fix --restart` re-claims the watcher and keeper through the normal ask afterwards, `--watch` streams a line per finding and rings the kit's locks bell), `stop` (close watcher), `prune` (clean up old data).

Run `fbtodo -h` for all flags.

---

## Customization

### Colors

Create `~/.config/fbtodo/theme.json`:

```json
{
  "accent": "#89b4fa",
  "active": "#cdd6f4",
  "success": "#a6e3a1"
}
```

Or use environment variables:

```sh
export FBTODO_ACCENT="#89b4fa"
export FBTODO_SUCCESS="#a6e3a1"
```

Three presets are included in `examples/` (Catppuccin, Gruvbox, Nord).

### Pane size and position

```sh
fbtodo pin --size 24 --side h    # 24 columns wide, beside the session
fbtodo pin --size 12 --side v    # 12 lines tall, below the session
fbtodo pin --list                # show current settings
```

`--size` is counted along the split: **columns** for `--side h` (the pane sits beside the
session) and **lines** for `--side v` (below it). The pane remembers your last size and opens
that way next time. Default: 12 lines below.

### Quiet while you read

If you use the notification kit (below), the pane can turn it off for you:

| key | what it does |
|---|---|
| `m` | quiet until this list finishes — then the phone rings again on its own |
| `M` | quiet until you press `u` |
| `u` | loud again |

Nothing is invented for this: the key writes the kit's own switch words — `phone-state`, which
`phone.sh` reads, and `state`, which `bell.sh` reads — so muting from the pane and muting from a
shell (`FREEBUFF_PHONE=off`) are the same state. `u` puts back what was there before, including
removing a `phone-state` that did not exist. The pane's title says which switch is in force and
which key lifts it (`quiet until done · u`). A pane that closes while muted gives the
notifications back, so a phone is never left quiet with nothing left holding the key.

`--no-keys` (or `FBTODO_PANE_KEYS=off`) turns the keys off in a pane whose terminal it should not
read.

The keys are for a pane you are looking at. Everything else — a script, a status row, another
program's button, a shell that is not a pane at all — uses the same switch as a command:

```sh
fbtodo mute              # or `fbtodo mute list` — what is in force
fbtodo mute on           # quiet until you say otherwise
fbtodo mute until-done   # quiet until the list in this directory finishes
fbtodo mute off          # loud again, putting back what was there
fbtodo mute --json       # the same facts, for a script to read
```

`until-done` reads the list first and declines, writing nothing, if that list is already
finished. Who asked decides who lifts the mute: one a key took ends when the pane closes, and
one a command took does not — the finished list, or `fbtodo mute off`, ends that one.

### Disable the pane

If you only want the status bar:

```sh
export FBTODO_NO_PANE=1
fbtodo bar        # just show "todos 3/5"
```

---

## Alerts (optional)

Get notifications when your agent finishes, gets stuck, or asks for input:

```sh
# Install the notification kit
mkdir -p ~/.config/freebuff-notify
cp scripts/notify/*.py scripts/notify/*.sh ~/.config/freebuff-notify/
chmod +x ~/.config/freebuff-notify/*.sh
~/.config/freebuff-notify/phone.sh --init
```

See [scripts/notify/README.md](https://github.com/TLE47/fbtodo/blob/main/scripts/notify/README.md) for details on iMessage and ntfy alerts.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| **Pane is empty** | Run `fbtodo status` — it'll tell you why. Usually the agent hasn't written a list yet. |
| **Pane won't appear** | Make sure you're in tmux and `fbtodo` is in your PATH. |
| **Pane closed** | It'll reopen automatically. If it doesn't, try `fbtodo stop` then run `fbtodo` again. |
| **Wrong size/position** | Use `fbtodo pin --side h` (beside) or `--side v` (below), with `--size N` in columns or lines respectively. |
| **List looks old** | Run `fbtodo status` to check how long ago it was written. |

---

## FAQ

<details open>
<summary><strong>Do I need Freebuff?</strong></summary>

For the built-in stores, yes. But you can use any agent that writes a JSON state file — see [docs/SOURCES.md](https://github.com/TLE47/fbtodo/blob/main/docs/SOURCES.md).
</details>

<details>
<summary><strong>Does this send my data anywhere?</strong></summary>

No. fbtodo reads local files only. The only outbound traffic is optional phone notifications (if you install them).
</details>

<details>
<summary><strong>Will it slow my agent down?</strong></summary>

No. It reads files that are being written anyway — the overhead is negligible.
</details>

<details>
<summary><strong>Can I run multiple sessions?</strong></summary>

Yes. Each session gets its own pane, bound to its task list.
</details>

<details>
<summary><strong>Works on Windows?</strong></summary>

WSL only. Windows Terminal + WSL works fine. Native Windows won't work (tmux isn't available).
</details>

<details>
<summary><strong>I just want a status bar, no pane.</strong></summary>

Set `FBTODO_NO_PANE=1` and use `fbtodo bar`. It prints `todos 3/5` and updates every few seconds.
</details>

---

## For developers

Run the test suite:

```sh
python3 scripts/fbtodo-selfcheck.py      # full suite (~90-160 seconds)
python3 scripts/fbtodo-selfcheck.py --only local-session   # one test
bash scripts/notify/test-freebuff-notify.sh   # notification tests
```

---

## License

MIT — see [LICENSE](https://github.com/TLE47/fbtodo/blob/main/LICENSE).
