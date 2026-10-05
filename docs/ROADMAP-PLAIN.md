# Where fbtodo is going

This is the same list as [ROADMAP.md](ROADMAP.md), written out in sentences. The table there is
the short form — one line per idea, meant for scanning. This one explains what each idea would
actually change about using the tool, and is the version to read if you are deciding where to
help.

Nothing here is built. The order is roughly the order I would build it in, grouped by what the
work is *for* rather than by importance. Every item is scoped so one person could land it on
their own.

## Getting people to try it at all

Most people who could use this tool never will, because installing it means cloning a
repository and making a symlink. Everything in this section is about removing a step between
"that sounds useful" and "I am running it".

**`brew install fbtodo`, `uvx fbtodo`, and a one-line installer.** This is the single biggest
adoption lever in the whole list. The tool has no dependencies and is a handful of Python
files; there is nothing technical stopping it from being a package. The launcher half of this
is already done — `fbtodo init` writes the `fb` function into your shell startup file, with
separate bodies for POSIX shells and fish. What is missing is the packaging around it.

## Trusting what the pane says

The list is the only progress signal you get while an agent works, so when it is stale or
missing, everything else the pane shows is undermined. This section is about the pane being
honest.

**Watch the desktop app live, not per turn.** This is a measured limitation, not a guess. The
app writes a message row only when a turn closes, and the todo list lives inside that row as a
`write_todos` tool call — so on the desktop app the list can be a whole turn behind. The
mechanism, what was ruled out, and four or five directions for a fix are written up in
[OPEN-PROBLEMS.md](OPEN-PROBLEMS.md). The terminal CLI does not have this problem: its journal
is appended mid-turn.

**`fb --one-step` — a working mode.** Because the desktop store commits per turn, the size of
the lag is the length of a turn. Finishing one step per turn makes every boundary carry a fresh
list. It is a convention, not a code change, and it is the cheapest thing on this list to try
tomorrow.

## Seeing everything at once

**`fbtodo serve` — a read-only web mirror.** Watch from your phone or another machine without
ssh. It pairs naturally with the notify kit that already exists.

## Talking to you where you are

The notify kit today covers iMessage and ntfy. Both are ways of getting a message to you; these
are about that messaging being easier to control, and reaching the people you work with.

**An ntfy mute switch inside the pane.** Muting today means knowing about the
`FREEBUFF_PHONE=off` environment variable or the `phone-state` file
([scripts/notify/README.md](../scripts/notify/README.md)). A key inside the pane that writes
that file would make "quiet while I read this" a single keystroke, and could pair with a
"mute until this list finishes" default — which is the actual behaviour people want, since the
annoying case is being woken up by a session you are already watching.

**More transports: Slack, Discord, Telegram, Pushover, native macOS notifications.** iMessage
and ntfy cover two platforms; a team channel is one webhook away.

**Step-change hooks (`--on-step`, `--on-complete`).** Lets people wire in their own scripts
rather than asking for a transport to be added. This is the escape hatch that makes the
previous item less necessary for anyone with a specific need.

## Making it look and feel right

**Theme presets and `fbtodo theme`** — `catppuccin`, `gruvbox`, `nord`, `dracula`, with a
`--preview`. [`examples/`](../examples) ships three hand-written presets today, so a picker
would turn what exists into a gallery. Note that the palette is already fully themeable through
environment variables and a `theme.json`; what is missing is the picker and shipping more
presets than the three handwritten ones.

**Accessibility modes: ASCII-only, no-emoji, high contrast.** Different box characters
(`[x]` / `[>]` / `[ ]`) and a plain palette. Easy, and it makes the tool usable in terminals
and screen readers that the current output does not serve well.

## Fitting more of your setup

**zellij and other multiplexers, plus a no-multiplexer fallback.** The same pane in more
terminals — and somewhere to land for people who do not want tmux at all.

**An MCP tool, or another agent's todo format.** `fbtodo push` and the `file:PATH` source are
already the seam ([SOURCES.md](SOURCES.md)), so shipping an adapter for one specific named
format is a contained job.

## Small things, if you want a first contribution

A `--no-emoji` renderer. A `zellij` probe next to the existing tmux one. A new theme file. One
more transport in the notify kit — each of these has tests you can copy.

The self-check is the contract: `python3 scripts/fbtodo-selfcheck.py`. A change is done when it
still passes.