# Settings

Precedence is the usual one: a command-line flag, then the environment, then a file.

| Variable | Default | Meaning |
|---|---|---|
| `FBTODO_HOME` | — | one directory for state, locks and logs, overriding the XDG default below |
| `XDG_STATE_HOME` | `~/.local/state` | state lives in `$XDG_STATE_HOME/fbtodo`; a legacy `~/.freebuff` is moved there once, when no watcher holds it |
| `FBTODO_FB_MARKER` | `$HOME/.fb-session` | the session marker the watcher reads to find live sessions (the `--fb-marker` default) |
| `FBTODO_NOTIFY` / `_DROP` / `_ASK` / `_PAUSE` / `_PANE_BELL` | `~/.config/freebuff-notify/*.py` | the five watches |
| `FBTODO_ASK_SECONDS` / `_PAUSE_SECONDS` / `_PANE_BELL_SECONDS` | 3 / 30 / 60 | their cadences (0 = never) |
| `FBTODO_PANE_SECONDS` | 3 | how often the keeper looks |
| `FBTODO_SPLIT` / `FBTODO_PANE_SIZE` | `v` / `12` | where the pane opens — `left`/`right`/`top`/`bottom` (which fix the edge), or `h`/`v` for the splitter's own trailing edge (right / below) — and its size |
| `FBTODO_NO_PANE` | — | set to disable panes entirely |
| `FBTODO_PATCH_LOG` / `_META` / `_ALERT_LOG` | `~/.config/freebuff-patch-watch/watch.log`, `~/.config/manicode/freebuff-metadata.json`, `~/.config/freebuff-notify/phone.log` | the optional `PATCH`/`ALERT` row |
| `FBTODO_ACCENT` / `_ACTIVE` / `_SUCCESS` / `_FAINT` / `_MUTED` / `_TRACK` | theme | palette overrides |
| `FBTODO_GRADIENT_START` / `_END` | theme | `#rrggbb`, or a raw SGR code like `1;36` for the accent |
| `FBTODO_TRUECOLOR` | auto | force 24-bit colour on or off |
| `FBTODO_TMUX` | `tmux` | the tmux binary/args to drive (a test knob) |
| `FBTODO_PATH` | the opener's `PATH` | the base of the `PATH` handed to a pane in its own command line, instead of whatever the tmux server would rebuild (the interpreter's own directory is always put first, so anything the pane resolves by name is the same Python). Set it when a machine needs a specific one; a pane that was opened pinned passes this value on |
| `FBTODO_LABEL_FLOOR` | `10` | estimates: a finished span under this many seconds is not evidence — it sets no pace and moves no memory (`0` keeps every span, the pre-4.23.0 behaviour) |
| `FBTODO_BLEND_WEIGHT` | `0.5` | estimates: how much a *waiting* step's number comes from its wording rather than the list's pace, `0`–`1` (`0` = pace only; `1` = wording only, which drops the pace and mis-sizes the tail) |
| `FBTODO_GOAL_LINES` | `3` | how many lines the goal heading may take (`0` hides it) |
| `FREEBUFF_PHONE` | on | `off` / `0` / `false` / `no` / `disabled` mutes the phone, read when a bell sends; the notify kit's `phone-state` file, the pane's `m`/`u` keys and `fbtodo mute on|off` write the same word to the same place |
| `FBTODO_NOTIFY_DIR` | `~/.config/freebuff-notify` | where the pane's mute key and `fbtodo mute` write the kit's two switch files (`phone-state` for `phone.sh`, `state` for `bell.sh`) |
| `FBTODO_PANE_KEYS` | on | `off` / `0` / `false` / `no` mutes the pane's KEYBOARD: `m`, `M` and `u` stop acting, and a pane opens with the terminal as it found it (`--no-keys` says the same thing) |
| `FREEBUFF_NO_REFRESH` | — | the `fb` launcher skips its `npm i -g freebuff` round trip |

A pane is handed the settings that decide **where it works and where its bells go**, not left to
inherit them: tmux starts a pane from its server's environment, so `FBTODO_HOME`, `XDG_STATE_HOME`,
`FBTODO_TMUX`, `FBTODO_FB_MARKER` and the six watch paths (`FBTODO_NOTIFY`, `_DROP`, `_ASK`,
`_PAUSE`, `_PANE_BELL`, `_LOCKS_BELL`) are written into the pane's own command line — beside the
pinned interpreter and `PATH` — by both `pane_command` and the `fb` launcher. Each rides only
when it is set, and because the value is in the command string a `tmux respawn-pane` (or the
keeper's repair) brings it back rather than the server's. Without that, a server started before
the owner exported one of them would leave the pane reading another state root, following another
set of sessions, or ringing the default bells instead of the ones this machine installed. A pane
whose recorded command is an **older pin** — one of these keys missing or carrying an old value —
is upgraded by the keeper on the next pass, so an answer the build no longer writes does not
outlive it; only the carried values are compared, so an `fb` pane on `--instance-of` that names
all of them is left alone.

Pane repair has a per-pane switch that is not an environment variable, because the pane is
the scope: the tmux user option **`@fbtodo_repair`** turns the keeper's automatic respawn of a
pane whose interpreter drifted off for that pane — `tmux set -p -t %3 @fbtodo_repair off`, or
`-w` / `-g` for a window or the whole server, since tmux resolves the option up the chain.
`off` / `no` / `0` / `false` disable it; anything else, or unset, leaves the repair running.
The pane is diagnosed either way — the keeper logs what it saw and the running pane shows
`KEPT (was on …)` on its title chip for a few seconds — but it is not respawned, and `on`
resumes the repair on the next pass with no keeper restart. `fbtodo keep off` / `on` /
`default` is that option as a command: the pane you are in, or one named with `--pane %3`;
one window with `--window TARGET` (a session, `session:index` or a window id — resolved
through tmux, and it outranks the server while a pane's own choice outranks it); and
`--server` for every pane at once (`keep` with no verb prints what is in force). The tmux
incantation is never needed.

The two estimate knobs are flags as well, so they can be set per run rather than per shell:
`--label-floor SEC` and `--blend-weight W`. Both are forwarded to the watcher when it is
started, so `fbtodo --label-floor 0 daemon` really does keep every span. Raising the floor
makes the estimates calmer (short flips stop setting the pace); lowering the weight makes the
pane trust the list's own pace over what a step's wording suggests. The reasoning behind both
is in [ESTIMATES.md](ESTIMATES.md).

Theming is a file, not a flag: `~/.config/fbtodo/theme.json`, overridden by
`./.fbtodo-theme.json` in the working directory, overridden by the environment above.

## Exit codes

`0` ok · `2` usage (argparse, including polling without a TTY) · `64` a bad `-s` value or
unexpected arguments · `65` a `push` payload that is not a state · `66` no instance, no
store, or nothing on stdin · `73` cannot create · `74` I/O error · `75` the watcher failed to
start · `78` a config or notifier error · `130` SIGINT · `141` SIGPIPE.
