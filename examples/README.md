# Examples

Small, copy-paste-able starting points. None of them is required — fbtodo works with no
configuration at all — and none of them touches the tool's code.

| File | What it is |
|---|---|
| [`tmux.conf`](tmux.conf) | `fbtodo bar` in your tmux status line — progress at a glance, no pane |
| [`theme-catppuccin-mocha.json`](theme-catppuccin-mocha.json) | a theme preset (24-bit colour) |
| [`theme-gruvbox-dark.json`](theme-gruvbox-dark.json) | a theme preset |
| [`theme-nord.json`](theme-nord.json) | a theme preset |
| [`remote-wrapper.sh`](remote-wrapper.sh) | the marker hook a remote session needs, as a file you can read |

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

## The status line

`fbtodo bar` prints one short string, `todos 3/5`, and reads the cached state file rather
than the transcript — cheap enough for a status line that refreshes every few seconds. See
[`tmux.conf`](tmux.conf) for the two lines to append.

If you only want the status line, set `FBTODO_NO_PANE=1` so no pane is ever opened.

## The remote marker

`-s nas` reads a session on another host. It works without any hook (it falls back to a
process probe), but the marker makes it exact: it says which pid is the session, when it
started and which directory it started in. [`remote-wrapper.sh`](remote-wrapper.sh) is the
wrapper from the README as a runnable file, with the `FBTODO_NAS*` variables it expects.
