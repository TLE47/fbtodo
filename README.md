# fbtodo

**Watch your coding agent work — its checklist, live, in a pane beside it.**

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org)
[![No dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)](#installation)
[![Platform: macOS | Linux](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey)](#installation)
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

fbtodo displays your [Freebuff](https://freebuff.com) agent's task list in a side pane while it works. It shows:
- **What's done, running, and next** — with live timers
- **How much time is left** — estimates based on your project history
- **If it's stuck or waiting** — with optional alerts to your phone

No setup needed. Works with any agent that keeps a todo list.

**[Installation](#installation)** · **[Commands](#commands)** · **[FAQ](#faq)** · [Settings](docs/SETTINGS.md) · [Deep docs](docs/INTERNALS.md)

---

## See it work

![fbtodo's pane working through a scripted session](docs/demo/demo.gif)

That clip is the real pane — `fbtodo pane`, drawing a scripted session as it ticks through eight
steps and finishes with the list done *and* the turn ended, which is the pair the bell waits for.
The recording, three stills from it and the same frames as an MP4 are in
[`docs/demo`](docs/demo), and `docs/demo/record.sh` reproduces them all.

---

## Installation

### Quick start

```sh
# 1. Install tmux (shows the pane)
brew install tmux                # or: apt install tmux

# 2. Clone fbtodo
git clone https://github.com/TLE47/fbtodo ~/Projects/fbtodo

# 3. Add to your PATH
mkdir -p ~/.local/bin && ln -sf ~/Projects/fbtodo/fbtodo ~/.local/bin/fbtodo

# 4. Start working
tmux new -s work
fbtodo                           # opens the pane automatically
```

That's it. Requirements: **Python 3.9+** and **tmux**.

### Using the `fb` shortcut (optional)

For a one-word launcher that updates and manages everything:

```sh
. ~/Projects/fbtodo/examples/fb.sh    # add this line to ~/.zshrc or ~/.bashrc
fb                                    # now use 'fb' instead of 'fbtodo'
```

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
fbtodo status       # show pane info and why it might be empty
```

**Other commands:** `ledger` (forecast vs. actual), `why` (pane location), `pin` (resize pane), `stop` (close watcher), `prune` (clean up old data).

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
fbtodo pin --size 12 --side h    # 12 lines, left side
fbtodo pin --list                # show current settings
```

The pane remembers your last size and opens that way next time. Default: 12 lines.

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
cp notify/*.py notify/*.sh ~/.config/freebuff-notify/
chmod +x ~/.config/freebuff-notify/*.sh
~/.config/freebuff-notify/phone.sh --init
```

See [notify/README.md](notify/README.md) for details on iMessage and ntfy alerts.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| **Pane is empty** | Run `fbtodo status` — it'll tell you why. Usually the agent hasn't written a list yet. |
| **Pane won't appear** | Make sure you're in tmux and `fbtodo` is in your PATH. |
| **Pane closed** | It'll reopen automatically. If it doesn't, try `fbtodo stop` then run `fbtodo` again. |
| **Wrong size/position** | Use `fbtodo pin --size N --side h` (or `v` for vertical). |
| **List looks old** | Run `fbtodo status` to check how long ago it was written. |

---

## FAQ

<details open>
<summary><strong>Do I need Freebuff?</strong></summary>

For the built-in stores, yes. But you can use any agent that writes a JSON state file — see [docs/SOURCES.md](docs/SOURCES.md).
</details>

<details>
<summary><strong>Does this send my data anywhere?</strong></summary>

No. fbtodo reads local files only. The only outbound traffic is optional phone notifications (if you install them).
</details>

<details>
<summary><strong>Will it slow my agent down?</strong></summary>

No. It reads files that are being written anyway — zero overhead.
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
python3 fbtodo-selfcheck.py              # full suite (~90-160 seconds)
python3 fbtodo-selfcheck.py --only local-session   # one test
bash notify/test-freebuff-notify.sh      # notification tests
```

---

## License

MIT — see [LICENSE](LICENSE).
