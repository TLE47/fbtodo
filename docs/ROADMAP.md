# Ideas & roadmap

Not built yet — things that would make fbtodo appeal to a lot more people, roughly in the
order I would build them. Ideas, arguments and pull requests are all welcome; each item is
scoped so one person could land it. The same list written out in sentences is
[ROADMAP-PLAIN.md](ROADMAP-PLAIN.md) — read that one if you want what each idea would change
rather than a line per idea.

| Idea | Why it would matter |
|---|---|
| **`brew install fbtodo` + `uvx fbtodo` + a one-line installer** | Removing "clone and symlink" is the single biggest adoption lever. |
| **`fbtodo board` — all live sessions in one pane** | People run two or three agents at once. One pane showing every session, its list and its clock beats switching windows. |
| **Watch the desktop app live, not per turn** | The app commits a message only when a turn closes, so its list is up to a turn behind — measured, with the mechanism and the fix directions in [OPEN-PROBLEMS.md](OPEN-PROBLEMS.md). |
| **`fb --one-step` — a working mode that keeps the list honest by construction** | The desktop store commits per turn, so the lag is the turn's length. Completing one step per turn makes every boundary carry a fresh list: a working convention, no app change, and the cheapest way to make the pane trustworthy on the app. |
| **`fbtodo serve` — a read-only web mirror** | Watch from your phone or another machine, no ssh. Pairs with the notify kit you already have. |
| **Freebucks before you spend them — how many are left, and what the next session may cost** | Freebucks are the meter people watch and the pane shows none of it. A balance read plus a rough projection (the last turn's cost, or a per-step estimate as in [ESTIMATES.md](ESTIMATES.md)) would say "about N more turns" before a session starts. The desktop store cannot supply it — its `sponsored_*` columns are ad-run bookkeeping, empty on ordinary threads — so the number comes from the app's own UI/API: the same missing channel [OPEN-PROBLEMS.md](OPEN-PROBLEMS.md) describes. |
| **Theme presets and `fbtodo theme`** (`catppuccin`, `gruvbox`, `nord`, `dracula`, `--preview`) | [`examples/`](../examples) ships three hand-written presets today; a picker would make it a gallery. |
| **More transports: Slack, Discord, Telegram, Pushover, native macOS notifications** | iMessage + ntfy covers two platforms; a room of teammates is one webhook away. |
| **An ntfy mute switch inside the pane** | Turning notifications off today means knowing about `FREEBUFF_PHONE=off` and the `phone-state` file; a key in the pane that writes that file (and `bell.sh`'s own switch) makes "quiet while I read this" one keystroke — and pairs with a "mute until this list finishes" default. |
| **An MCP tool, another agent's todo format** | `push` and `file:PATH` are the seam ([SOURCES.md](SOURCES.md)); a shipped adapter for a named format is the next step. |
| **zellij (and other multiplexers), plus a no-multiplexer fallback** | Same pane, more terminals — and somewhere to land for people who do not want tmux. |
| **Step-change hooks (`--on-step`, `--on-complete`)** | Lets people wire in their own scripts instead of asking for a transport. |
| **Accessibility modes: ASCII-only, no-emoji, high contrast** | `[x]`/`[>]`/`[ ]` and a plain palette are easy wins for terminals and screen readers. |

Good first issues, if you want something small: a `--no-emoji` renderer, a `zellij` probe
next to the tmux one, a new theme file, or one more transport in the notify kit (each has
tests to copy). The self-check is the contract — a change is done when it still passes.