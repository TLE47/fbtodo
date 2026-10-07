# The notification kit

The little programs that turn "the agent wants your attention" into something you can hear
and something that reaches your phone. **All optional**: `fbtodo` runs without them, skips
any that are missing, and never reads anything back from them — a notifier owns both the
decision *and* the "already sent" record, so there is nothing to keep in sync.

| File | What it does |
|---|---|
| `todo-bell.py` | the **finish bell**: the list is complete *and* the turn ended — in the source's own words (a CLI journal's boundary, or the desktop store's `turn_running`) |
| `ask-bell.py` | the **ask watch**: the agent is stopped on a question, waiting on you |
| `pause-bell.py` | the **stall watch**: the agent stopped without ending its turn and the list still has work |
| `pane-bell.py` | the **pane watch**: a session has no list pane the keeper failed to put back, or there is no keeper at all |
| `drop-bell.py` | the **drop watch**: the session died by itself, rather than ending |
| `locks-bell.py` | the **locks watch**: a watcher or keeper is running with a claim no reader can find, or a claim is free while its record names a live process (`fbtodo locks --watch` asks it) |
| `phone.sh` | the one place a phone notification is sent, over iMessage and/or ntfy |
| `discord-send.sh` | the second sink: the same events posted into a Discord channel through `hermes send` on the NAS |
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
bash scripts/notify/test-freebuff-notify.sh    # (optional) its last section proves the copy landed
```

`funcs.zsh` is the shell half — the `freebuff` wrapper that runs the title timer and the
drop watch, plus `freebuff-bell`. Source it from `~/.zshrc`; the test suite drives this copy,
so the wrapper the bells hang off is the one in the repository rather than whatever a
particular machine happens to define.

Nothing else is required. If you keep them somewhere else, point `fbtodo` at them with
`FBTODO_NOTIFY`, `FBTODO_DROP`, `FBTODO_ASK`, `FBTODO_PAUSE`, `FBTODO_PANE_BELL`,
`FBTODO_LOCKS_BELL`.

**The install is wholesale, and that is the contract.** Every `.py` and `.sh` in the
installed copy is replaced by this directory's — so an edit made only in the installed copy
is lost the next time somebody installs, and a bell that arrives here (or a mode that leaves
here) reaches the machine only when the install is re-run. Keep local edits *here*, and let
the suite tell you when the two have parted: it compares the two directories file by file
(`== the installed kit is this kit ==`) and fails with the `cp` that fixes it, skipping out
loud both on a machine with no kit installed and when the suite *is* the installed copy.
Measured 2026-10-05: the copy on this Mac was a month behind — `locks-bell.py` was never
installed at all, and three bells still carried the remote (`--nas`) half the repository had
retired, one documented `cp` away from silently changing what every watch does.

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

## The second sink: Discord

The phone is where a notification goes when you are away from the Mac; it is not where anything
can be *looked up*. `discord-send.sh` posts the same events into a Discord channel, so a reader
there — the Hermes agent, or the owner's own phone through Discord — can see what freebuff has
been doing.

It is a **second sink, not a transport**, and it is asked for by name: only `phone.sh --discord`
reaches it, and only two bells pass that flag — the finish bell (`todo-bell.py`) and the drop
watch (`drop-bell.py`). Ask, pause, pane and locks never do, which is what keeps the channel to
finishes and drops. The phone push is unchanged by it, and a machine whose kit predates the sink
is silent rather than broken.

The two sinks do not carry the same TEXT, because they are read differently. A finish sent to the
phone is a nudge: metadata only by default (`3/3 steps done · 2026-10-06 14:05`, session), with the
agent's own prose opt-in behind `FREEBUFF_PHONE_TEXT=agent`. A finish in the channel answers "what
was this, and what came of it" — and it is read out of context, by the agent living there and by
the owner scrolling past it later — so the finish bell labels it, one field per line:

    Goal: shrink the pane heading to one line
    Summary: the heading now fits and the two failing checks were fixed
    3/3 steps done · 2026-10-06 18:14

`Goal` is the agent's own `Goal:` heading and nothing else — the session's opening request is never
promoted into it, because a quote labelled as a goal is a claim the agent never made. `Summary` is
the first line of the agent's last answer (the same `state["summary"]` fbtodo already extracts),
flattened to one line; the goal is flattened the same way, so a heading wrapped over two lines is
still one field here.

A field with nothing in it is left out rather than shown empty, and a run with neither is left to
the phone's body — nothing is invented. A goal with no summary is still sent, because it says more
than the phone's metadata does, and that case is the one the first version of this split got wrong:
it dropped the goal on the floor with the summary. All of it travels as `phone.sh
--discord-message TEXT`, which is the second sink's body alone, so the phone's message is
byte-for-byte what it always was and `FREEBUFF_PHONE_TEXT` does not touch it. A drop still posts
the phone's message (a drop has neither a goal nor a summary). `FREEBUFF_DISCORD_SUMMARY=off`
returns the finish push to that same metadata-only body.

Delivery runs on the NAS, because that is where the bot's token already lives and a copy of it
here would be a second place to rotate:

    discord-send.sh --title T --message M      # ssh <NAS_HOST> docker exec -i <container>
                                               #   hermes send --to <target> -f -

`hermes send` is the supported way for a script to speak on Discord — it reuses the gateway's own
credentials and, in its own words, runs "no LLM, no agent loop", so a post never becomes a turn
Hermes answers.

| Setting | Meaning |
|---|---|
| `FREEBUFF_DISCORD_TARGET` | delivery target, default `discord:#freebuff` (`discord`, `discord:#name`, `discord:<chat_id>[:<thread_id>]`) |
| `FREEBUFF_DISCORD` | `off` / `0` / `false` / `no` / `disabled` mutes the sink (or a `discord-state` file) |
| `FREEBUFF_DISCORD_SUMMARY` | `off` / `0` / `false` / `no` / `disabled` keeps the agent's own goal and one-line outcome out of the channel — the finish push falls back to the phone's metadata-only body |
| `FREEBUFF_DISCORD_CMD` | the whole delivery command, with the text on stdin — the test seam and the escape hatch (a different host, a webhook) |
| `NAS_HOST` / `NAS_CONTAINER_HERMES` | the ssh destination and the container, default `billthuan1@192.168.68.58` / `hermes` |

The ssh is **agent-less** on purpose: `~/.ssh/config` names `IdentityFile ~/.ssh/nas_bridge_key`
for that host, because the default key is passphrase-protected and a bell launched outside a
login shell has no ssh-agent. Without it the delivery fails with `Permission denied
(publickey)` — which is also how the Hermes MCP server used to die before it ever started.

`discord-send.sh --print` resolves and reports without sending, `--dry-run` prints the delivery
without making it, and `--test` posts one fixed line. Every outcome is logged to `discord.log`
(bounded, like `phone.log`), so "the channel stayed quiet" has an answer rather than a guess.

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

### Who asks the finish bell

The list's finish is not a watch on the watcher's clocks: it is asked for by whoever is in a
position to see it, and the bell keeps the record so two askers cannot double-send.

```sh
todo-bell.py <shell-pid> [--print] [--tty PATH]        # the session timer's ask, once per 5s
todo-bell.py --state -|PATH [--print] [--push-only]    # an asker that HAS the state
```

The shell wrapper's `session-timer.sh` uses the first form: it names the shell that launched
the session, fbtodo resolves the `freebuff` under it, and the journal's own `shouldEndTurn`
says when the turn ended.

A **pane** uses the second: it is the process drawing the list, so it is the one that knows a
turn ended here and now, and it hands the state over rather than making the bell re-resolve a
session nobody is looking at. This is the only ask there can be for the **desktop app**, whose
turns are the app's own — no `freebuff` process runs them, so there is no pid for the timer's
form to resolve, and its store writes no journal to read. The desktop store does say whether a
turn is alive (`threads.turn_state`, which fbtodo carries as `turn_running`), and a thread
with no turn running and every step ticked is the same news a journal's boundary gives.
The pane asks once per list, only for a finish it WATCHED (it remembers the list while it still
has work in it, so a pane opened on a thread that was already over says nothing), and passes
`--push-only`: the chime belongs to the session's own timer, and two ringers for one finish is
a Mac that rings twice. It also passes `--note`: the decision is kept beside the kit's switches
(`finish.log`), because nothing that SENDS nothing ever reaches `phone.log` — so a refusal had
no answer anywhere. One line per ask, newest kept and bounded:

```
2026-10-05 23:24:03  no push  list 8f1c0a4d2e77  already pushed for this list (10/10)
2026-10-05 23:24:07  no push  list 8f1c0a4d2e77  all 3 done, but the turn is still running
2026-10-05 23:26:41  push  list 3d22a5c2a67c  all 10 todos done and the turn ended
```

Because the pane passes `--push-only`, the line it records is the PHONE's half (`push`/
`no push`). An asker that owns the chime as well would write the chime's half too (`ring`/
`silent`) beside it — none does today, since the session timer asks every 5s and passes no
`--note` at all, which is why the flag exists: a record of the clock is not a record of the
finish. The same lines are repeated on stdout, so the asker can log them too (a pane's own log
carries the reason on the line that records the ask), and `fbtodo status` prints the newest one
— which is where an operator who does not know this file exists will look. Nothing is written
without `--note`, and `--print` writes nothing at all.

## Testing

```sh
bash test-freebuff-notify.sh
```

The suite drives the real scripts against a throwaway `HOME` and stub senders, and covers
the ring matrix, the mute switch, `bell.sh` agreeing with the picker, the double-send race
between two timers, and the per-transport resolution. It sends nothing: the iMessage sender
and the HTTP transport are both stubs in the fixture environment.

Run it from a checkout: it drives the fixtures beside it — `funcs.zsh`, which the install
deliberately does not copy, is one of them — so the copy in `~/.config/freebuff-notify/` is
not a suite that can run on its own either. Its last section checks the one thing no fixture
can: that the kit *installed* on this machine is this kit (see [Install](#install)); running
the suite from the installed copy skips that comparison, since there is nothing to compare.
