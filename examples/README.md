# Examples

Small, copy-paste-able starting points. None of them is required — fbtodo works with no
configuration at all — and none of them touches the tool's code.

| File | What it is |
|---|---|
| [`fb.sh`](fb.sh) | the `fb` launcher: agent and its pane in one word |
| [`tmux.conf`](tmux.conf) | `fbtodo bar` in your tmux status line — progress at a glance, no pane |
| [`theme-catppuccin-mocha.json`](theme-catppuccin-mocha.json) | a theme preset (24-bit colour) |
| [`theme-gruvbox-dark.json`](theme-gruvbox-dark.json) | a theme preset |
| [`theme-nord.json`](theme-nord.json) | a theme preset |

## Themes

Copy one into place and the pane picks it up — no restart of the tool needed, the palette is
re-read when it changes:

```sh
mkdir -p ~/.config/fbtodo
cp examples/theme-catppuccin-mocha.json ~/.config/fbtodo/theme.json
```

Or per project, so one repo can look different from the rest:

```sh
cp examples/theme-nord.json ./.fbtodo-theme.json
```

Keys are `accent`, `gradient_start`, `gradient_end`, `muted`, `track` and `faint`. `accent`
also takes a raw SGR code (`"1;36"`) if you want the terminal's own colour; the rest are
`#rrggbb`. Anything unrecognised is ignored, and `FBTODO_ACCENT` / `FBTODO_GRADIENT_START` /
… still win over a file.

The presets here are plain hand-written files: a two-line change is all a pull request needs,
and new ones are very welcome.

## `fb`: the launcher

`fb` is a nickname you teach your terminal once, so that one word does the whole dance. In
plain words: it updates your agent if a new release is out, makes room for the todo list,
starts the agent in the space you were already in, and closes the list when that session ends.
Nothing else in this repository depends on it, and skipping it is fine.

Source [`fb.sh`](fb.sh) from your shell startup file and `fb` replaces `freebuff`. It does
three things in the order that matters: refreshes the released CLI (`npm i -g freebuff`,
quiet unless the version moved — `FREEBUFF_NO_REFRESH=1` skips it, which scripts and tests
want), opens the todo pane bound to the session it is about to start, then runs the agent in
the current pane. `--instance-of $$` is what makes the pane follow *that* session rather than
whichever one is newest in the directory, and `FBTODO_NO_PANE=1` still turns the pane off.

If you would rather have the short name on the tool instead, the same file ends with the
one-line alternative: `alias ft=fbtodo` (`ft snap`, `ft bar`, `ft why`).

## The status line

`fbtodo bar` prints one short string, `todos 3/5`, and reads the cached state file rather
than the transcript — cheap enough for a status line that refreshes every few seconds. See
[`tmux.conf`](tmux.conf) for the two lines to append.

If you only want the status line, set `FBTODO_NO_PANE=1` so no pane is ever opened.

