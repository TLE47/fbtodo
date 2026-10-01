# The notification kit

The little programs that turn "the agent wants your attention" into something you can hear
and something that reaches your phone. **All optional**: `fbtodo` runs without them, skips
any that are missing, and never reads anything back from them — a notifier owns both the
decision *and* the "already sent" record, so there is nothing to keep in sync.

| File | What it does |
|---|---|
| `todo-bell.py` | the **finish bell**: the list is complete *and* the turn ended |
| `ask-bell.py` | the **ask watch**: the agent is stopped on a question, waiting on you |
| `pause-bell.py` | the **stall watch**: the agent stopped without ending its turn and the list still has work |
| `pane-bell.py` | the **pane watch**: a session has no list pane the keeper failed to put back, or there is no keeper at all |
| `drop-bell.py` | the **drop watch**: the session died by itself, rather than ending |
| `phone.sh` | the one place a phone notification is sent, over iMessage and/or ntfy |
| `bell.sh` | the one place the local chime is played |
| `make-sound.py` | generates a chime from a SoundFont (optional; `bell.sh` falls back to a system sound) |
| `session-timer.sh` | keeps the terminal tab title on the session: a live timer plus what it is working on |
| `session-task.py` | the caption the timer shows — a short description of what the session is working on |
| `funcs.zsh` | the kit's shell integration: the chime picker and the `freebuff` wrapper that runs the tab-title timer and the drop watch |
| `phone.conf.example` | the transport config, with placeholders |
| `test-freebuff-notify.sh` | this kit's own test suite |

## Install

The bells find each other by their own directory, and `fbtodo` looks for them in
`~/.config/freebuff-notify/` by default:

```sh
mkdir -p ~/.config/freebuff-notify
cp scripts/notify/*.py scripts/notify/*.sh ~/.config/freebuff-notify/
chmod +x ~/.config/freebuff-notify/*.py ~/.config/freebuff-notify/*.sh
~/.config/freebuff-notify/phone.sh --init      # writes phone.conf with a fresh topic
. ~/Projects/fbtodo/scripts/notify/funcs.zsh   # the shell side: the wrapper and the bell commands
```

`funcs.zsh` is the shell half — the `freebuff` wrapper that runs the title timer and the
drop watch, plus `freebuff-bell`. Source it from `~/.zshrc`; the test suite drives this copy,
so the wrapper the bells hang off is the one in the repository rather than whatever a
particular machine happens to define.

Nothing else is required. If you keep them somewhere else, point `fbtodo` at them with
`FBTODO_NOTIFY`, `FBTODO_DROP`, `FBTODO_ASK`, `FBTODO_PAUSE`, `FBTODO_PANE_BELL`.

## The transport

`phone.sh` is the only sender; every bell calls *it*, so the switch, the server and the
topic resolve the same way everywhere. Two transports, chosen by
`FREEBUFF_PHONE_TRANSPORT`:

- **`imessage`** — AppleScript → Messages.app → `IMESSAGE_TO`. Lands in a thread the phone
  already notifies about, with nothing to subscribe to. Needs the Mac awake and signed in.
  Your own handle means a note-to-self thread.
- **`ntfy`** — a POST to `https://ntfy.sh` (or your own instance, same API) with
  `Title`/`Priority`/`Tags` headers. No account: **the topic is the secret**, so it lives in
  a `0600` file and is never a command-line argument; a self-hosted token travels in a
  `0600` `curl --config` file for the same reason.
- **`auto`** (the default) tries iMessage first and falls back to ntfy; **`both`** sends
  down both pipes every time, for when either one alone can go quiet at the far end.

```sh
phone.sh --init                    # create the config with a fresh topic
phone.sh --test                    # send a fixed "it is wired up" notification
phone.sh --print                   # resolve and report, send nothing
phone.sh --dry-run                 # print the exact request, send nothing
phone.sh --title T --message M [--priority P] [--tags a,b]
```

Environment wins over the file: `NTFY_URL`, `NTFY_TOPIC`, `NTFY_TOKEN`, `IMESSAGE_TO`,
`FREEBUFF_PHONE_TRANSPORT`. `FREEBUFF_PHONE=off` (or a `phone-state` file) mutes sending
without unsetting anything. `FREEBUFF_PHONE_CONF` points at a different config file.

`--init` mints the topic from `/dev/urandom` — 128 bits, as hex — because on a public ntfy
server the topic *is* the authentication; it never reaches an argument, a shell history or
the log, and nothing guesses one if the read fails (exit `69`, no config written).

**The bells send metadata by default.** A push says which session, which state and how much
of the list finished — never the agent's own words: a model's sentence arriving on a phone
reads as real, links and numbers included. `FREEBUFF_PHONE_TEXT=agent` puts the prose back
(the `Goal:` line, the sentence it finished on, the question on screen) for an owner who
wants it. The iMessage text is passed to `osascript` as an argument, so a message containing
quotes, `&` or newlines arrives as written and never becomes AppleScript source.

Exit codes: `0` sent or muted · `2` usage · `69` delivery failed · `78` not configured.

## The chime

`bell.sh` is the one place the chime is played, so every caller honours the same pick,
volume and on/off switch.

```sh
bell.sh [--print] [--drop] [tty] [reason]
```

- `--print` resolves and reports without playing (and without honouring the off switch), so
  "it did not ring" and "it rang the wrong thing" are distinguishable.
- `--drop` means "the session dropped", not "it finished": it resolves its own voice
  (`FREEBUFF_BELL_SOUND_DROP`, else a `drop-sound` file, else a macOS system sound), so the
  two are told apart by ear without looking at the screen.

Resolution order: `FREEBUFF_BELL` / `state`, then `FREEBUFF_BELL_SOUND` / `sound`, then the
first voice generated by `make-sound.py`, then a system sound from
`${FREEBUFF_SOUNDS_DIR:-/System/Library/Sounds}`. Volume from `FREEBUFF_BELL_VOLUME` /
`volume`, default `0.5`.

The generated chimes are not shipped — they are built from a SoundFont on your own machine,
and the license of that bank is yours to check:

```sh
python3 make-sound.py --voice steel      # writes steel.wav next to this file
```

## The tab title

While freebuff's full-screen UI owns the terminal, the tab title is the one place a
running session stays visible, so it doubles as the "is it still running, and on what?"
indicator:

```sh
session-timer.sh run    <start-epoch> [tty-file]   # live "⏱ freebuff 1m04s · task"
session-timer.sh finish <start-epoch> [tty-file]   # "✓ Done freebuff 1m04s · task"
```

`run` loops until the wrapper kills it and re-reads the prompt every few seconds, so a new
prompt mid-session shows up too; `finish` writes the stamp once, so it is in place before
the shell prompt (and the chime) come back. The caption itself comes from
`session-task.py <root-pid> [max-chars]`, which matches a session to *this* shell by the pid
in the session's `log.jsonl` — a session running in another terminal can never caption this
tab.

Both are called by the shell wrapper, not by `fbtodo`; they are here because they are part
of the same kit (and the same test suite).

## The contracts

`fbtodo` asks each watch the same way — a subprocess, no return value read. What it passes:

| Watch | Invocation |
|---|---|
| ask | `ask-bell.py --quiet` |
| stall | `pause-bell.py --watch-pid PID --quiet` |
| pane | `pane-bell.py --quiet --keeper PATH` |

Each one is also runnable by hand, which is how you debug it: every bell takes `--print`
(resolve and report, send nothing) plus `--title`, `--message`, `--priority`, `--tags` to
send something specific. `pane-bell.py --print` (it looks the keeper's claim up itself:
`$FBTODO_HOME` or `$XDG_STATE_HOME/fbtodo`, else the legacy `~/.freebuff`) answers "is any
session missing its pane?" directly, and `pause-bell.py --watch-pid PID
--print` says whether a session has stopped mid-list and why not, if not.

Every read is bounded. A notification never blocks a watcher's poll: the push is handed to
`phone.sh` detached, so nothing waits on a network.

## Testing

```sh
bash test-freebuff-notify.sh
```

The suite drives the real scripts against a throwaway `HOME` and stub senders, and covers
the ring matrix, the mute switch, `bell.sh` agreeing with the picker, the double-send race
between two timers, and the per-transport resolution. It sends nothing: the iMessage sender
and the HTTP transport are both stubs in the fixture environment.
