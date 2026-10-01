# Ideas & roadmap

Not built yet — things that would make fbtodo appeal to a lot more people, roughly in the
order I would build them. Ideas, arguments and pull requests are all welcome; each item is
scoped so one person could land it.

| Idea | Why it would matter |
|---|---|
| **`brew install fbtodo` + `uvx fbtodo` + a one-line installer** | Removing "clone and symlink" is the single biggest adoption lever. |
| **`fb` as a first-class command** — ship the launcher, or a `fbtodo init` that writes it into your shell startup file | One word to launch an agent *and* its pane; today it is a snippet to paste. |
| **`fbtodo board` — all live sessions in one pane** | People run two or three agents at once. One pane showing every session, its list and its clock beats switching windows. |
| **Watch the desktop app live, not per turn** | The app commits a message only when a turn closes, so its list is up to a turn behind — measured, with the mechanism and the fix directions in [OPEN-PROBLEMS.md](OPEN-PROBLEMS.md). |
| **`fbtodo serve` — a read-only web mirror** | Watch from your phone or another machine, no ssh. Pairs with the notify kit you already have. |
| **Theme presets and `fbtodo theme`** (`catppuccin`, `gruvbox`, `nord`, `dracula`, `--preview`) | [`examples/`](../examples) ships three hand-written presets today; a picker would make it a gallery. |
| **More transports: Slack, Discord, Telegram, Pushover, native macOS notifications** | iMessage + ntfy covers two platforms; a room of teammates is one webhook away. |
| **`fbtodo stats` — history and retro** | Per-project step times, slowest step types, a "where did the session actually spend its time" summary. This is the feature that makes people keep it installed. |
| **An MCP tool, another agent's todo format** | `push` and `file:PATH` are the seam ([SOURCES.md](SOURCES.md)); a shipped adapter for a named format is the next step. |
| **zellij (and other multiplexers), plus a no-multiplexer fallback** | Same pane, more terminals — and somewhere to land for people who do not want tmux. |
| **Step-change hooks (`--on-step`, `--on-complete`)** | Lets people wire in their own scripts instead of asking for a transport. |
| **Accessibility modes: ASCII-only, no-emoji, high contrast** | `[x]`/`[>]`/`[ ]` and a plain palette are easy wins for terminals and screen readers. |

Good first issues, if you want something small: a `--no-emoji` renderer, a `zellij` probe
next to the tmux one, a new theme file, or one more transport in the notify kit (each has
tests to copy). The self-check is the contract — a change is done when it still passes.
