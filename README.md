# fbtodo

**Watch your coding agent work — its checklist, live, in a pane beside it.**

[![CI](https://github.com/TLE47/fbtodo/actions/workflows/ci.yml/badge.svg)](https://github.com/TLE47/fbtodo/actions/workflows/ci.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org)
[![No dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)](#installation)
[![Platform: macOS | Linux](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey)](#installation)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](https://github.com/TLE47/fbtodo/blob/main/LICENSE)

fbtodo displays your [Freebuff](https://freebuff.com) agent's todo list in a side pane while it is working. It shows:
- **What's done, running, and next** — with live timers
- **How much time is left** — estimates based on your project history
- **If it's stuck or waiting** — with optional alerts to your phone
- **One keypress to hush it** — press `m` in the pane and the phone stays quiet until the list finishes

No configuration: it reads the list your agent already keeps. Freebuff's list turns up on its own;
anything else can push one over stdin — see [docs/SOURCES.md](https://github.com/TLE47/fbtodo/blob/main/docs/SOURCES.md).

**[Installation](#installation)** · **[Commands](#commands)** · **[FAQ](#faq)** · [Install guide](https://github.com/TLE47/fbtodo/blob/main/docs/INSTALL.md) · [Settings](https://github.com/TLE47/fbtodo/blob/main/docs/SETTINGS.md)

---

## See it work

![fbtodo's pane working through a scripted session](https://raw.githubusercontent.com/TLE47/fbtodo/main/docs/demo/demo.webp)

The clip above shows `fbtodo pane` in real time. It monitors a scripted session through eight steps, updating continuously until all tasks are marked complete and the session ends.

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

**No configuration needed** — fbtodo reads the list your agent already creates. Just make sure your agent is keeping one.

---

## Commands

```sh
fbtodo              # watch the pane (default)
fbtodo bar          # show "todos 3/5" in your status bar
fbtodo snap         # print one snapshot
fbtodo board        # every live session in one frame (--live keeps redrawing)
fbtodo status       # show pane info and why it might be empty
fbtodo pip start    # float buffy-chan in a window of her own, pinned over the pane (pip stop)
```

### Pane keys

While you are looking at a pane, use these keys to control phone notifications:

| key | what it does |
|---|---|
| `m` | quiet until this list finishes, then ring again |
| `M` | quiet until you press `u` |
| `u` | loud again |
| `n` | toggle the notification button |
| `p` | her floating window: release her so she can be dragged, or pin her back over the pane's own middle |

The last one is only read while her window is RUNNING (`fbtodo pip start`): it is the button for
`fbtodo pip free` / `pip stuck`, and the title chip says which state is in force (`pip stuck · p`)
for exactly as long as there is a window to report. With no window, `p` says so and changes
nothing.

She finds the pane by herself. Her window photographs the window the pane lives in and looks for the
one thing in it shaped like a pane — the frame fbtodo draws is the only pair of tall vertical lines
in the picture — then stands in that pane's own empty space, the rows the frame left blank above its
state row (the place the ASCII picture used to be drawn). The pane helps: it publishes its own grid
(`rows cols start count`), so her window knows which rows are empty without reading a background
that is transparent onto a wallpaper, and knows the pane's shape well enough to pass over the one
other thing in that window that is a pair of vertical lines — the explorer sidebar. It re-measures
every few seconds and whenever the window changes, so resizing the window or the pane carries her
with it.

The empty space is HERS rather than whatever happened to be left over: while her window is running
the pane reserves rows for it ( `FBTODO_PIP_ROWS`, by default as many as her drawn panel would have
taken, and never at the steps' expense), so a busy list shows a few rows fewer rather than a
character sitting on top of them. She is fitted into that slot — at a whole number of art pixels, so
nothing is resampled — and never drawn outside the pane: centred in the slot and clamped inside the
frame's own borders, ordered out while the pane has no room for her, and not shown at all until a pane
has been found, or while the app's window is on another Space. A taught pin is the exception — where
you put her is where she stays.

A pin you TEACH is an override: press `p` to release her, drag her where you want her, press `p`
again, and that offset from the window is what she follows from then on. `fbtodo pip forget` drops
it and hands her back to the measured fit.

She moves like furniture rather than like a slideshow: when the pane's layout puts her somewhere
new she eases over there instead of hopping, she fades in and out instead of popping when the list
fills her slot and gives it back, and she cross-dissolves from one pose to the next. One number is
the length of all of it — `FBTODO_PIP_TRANSITION`, 700 ms by default, `0` for the hard cuts she
started with.

She is drawn at the resolution she exists at. The owner's stickers are 900px square, the frames the
PANE averages into cells are 128px, and the frames her window floats are the same twenty poses at
384px (`assets/buffy/pip`, `scripts/buffy-thumbs.py --size 384`) — which is exactly her size on a 2x
display, 192pt, so what the screen shows is the drawing and not a resampling of it. Where a resample
is needed (a smaller slot, a checkout with only the small frames) it happens once, off the screen,
with a Lanczos filter and a light unsharp mask, and the result is copied to the screen 1:1.
`scripts/buffy-artcheck.py` measures any two renders side by side — sharpness, ringing and how far
the round trip back to the original art lands — because "sharper" is otherwise an opinion.

And she is in a MOOD rather than a loop. The pane already decides one word for how things are going
— the same word that chooses the face on its top border — and publishes it where her window can read
it (`~/.cache/fbtodo/pip-mood`): so she sits at the laptop while a step is being worked on and thinks
it over, throws both arms up when the list finishes, dozes with the cat once the session has gone
quiet, peers about puzzled when there is no list at all, and HOLDS still — hands to her face,
annoyed, sitting with it — for the failure, the rewrite-the-list nudge and a heading left over from
an earlier turn. All twenty of her stickers are worn by one mood or another, which the suite checks as
a union against the art on disk. Which sticker means which mood is one table (`moodCycles` in
`scripts/buffy-pip.swift`), checked against the pane's own vocabulary and against the frames on disk —
and the whole table of conditions, poses and paces, with how to see and retune one, is
[docs/MOODS.md](docs/MOODS.md).

When she says something, it is a MANGA balloon rather than a card: the line is measured first and the
cloud is the scallops that wrap it, so a longer line is a wider cloud and one that has to wrap sits
lower; the mood picks the shape — a soft pink cloud, a spangled one for a finished list, a row of
detached thought dots while she is only thinking or waiting, a jagged shock balloon for a failure or a
nudge — and one of them, now and then (12% of mood changes, `FBTODO_PIP_SURPRISE`), is the surprise:
words that belong to no mood at all, in the one balloon that is all of them at once. It is drawn inside
her own square and never past it, so a line can never land on the pane's list, and `--art-say` renders
one where you can measure it.

The status row also has a **clickable button** (`[ ntfy on · n ]`) to toggle notifications. It adapts to fit your pane's width.

`--no-mouse` (or `FBTODO_PANE_MOUSE=off`) disables the button and keeps only the `n` key.

### Quiet while you read

To mute notifications **without a pane**, use commands:

```sh
fbtodo mute              # check current state
fbtodo mute on           # stay quiet
fbtodo mute until-done   # quiet until this list finishes
fbtodo mute off          # loud again
fbtodo mute --json       # output as JSON for scripts
```

The pane's `m` key and these commands both control the same switches (`phone-state` and `state`), so muting from either place keeps the whole system in sync. When you close a muted pane, notifications turn back on automatically.

`--no-keys` (or `FBTODO_PANE_KEYS=off`) disables keyboard input in a pane whose terminal should not be read.

---

### Multiple sessions

Running more than one agent? Use `fbtodo board` to see them all at once — one row per session with its list, heading, and live clock.

**Other commands:** `ledger` (forecast vs. actual), `why` (pane location), `pin` (resize pane), `keep` (pane-repair switch).

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
