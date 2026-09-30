# Settings

Precedence is the usual one: a command-line flag, then the environment, then a file.

| Variable | Default | Meaning |
|---|---|---|
| `FBTODO_HOME` | — | one directory for state, locks and logs, overriding the XDG default below |
| `XDG_STATE_HOME` | `~/.local/state` | state lives in `$XDG_STATE_HOME/fbtodo`; a legacy `~/.freebuff` is moved there once, when no watcher holds it |
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
| `FBTODO_LABEL_FLOOR` | `10` | estimates: a finished span under this many seconds is not evidence — it sets no pace and moves no memory (`0` keeps every span, the pre-4.23.0 behaviour) |
| `FBTODO_BLEND_WEIGHT` | `0.5` | estimates: how much a *waiting* step's number comes from its wording rather than the list's pace, `0`–`1` (`0` = pace only; `1` = wording only, which drops the pace and mis-sizes the tail) |
| `FBTODO_GOAL_LINES` | `3` | how many lines the goal heading may take (`0` hides it) |
| `FREEBUFF_PHONE` | on | `off` / `0` / `false` / `no` / `disabled` mutes the phone, read when a bell sends |
| `FREEBUFF_NO_REFRESH` | — | the `fb` launcher skips its `npm i -g freebuff` round trip |

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
