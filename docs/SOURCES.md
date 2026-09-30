# Where the list comes from

fbtodo never invents a todo list: it reads one something else wrote. There are four sources —
two built into the tool (the stores a Freebuff writes) and two generic ones (`push` and
`file:PATH`) that make no assumption about Freebuff at all.

## The built-in stores

A coding agent's todo list is not a side channel — it is written to whatever transcript store
the client keeps. Freebuff keeps two, and picking the wrong one is the usual reason a pane
looks broken:

| Source | Store | Granularity |
|---|---|---|
| **cli** (`freebuff` in a terminal) | `~/.config/manicode/projects/<project>/chats/<ISO>/log.jsonl` | **live — mid-turn** |
| **desktop** (the app) | `~/.config/freebuff-desktop/projects/<slug>/desktop-v2.db` (SQLite) | per turn, while the app runs |

The CLI journal is the good one: append-only, written *during* the turn. Each record is a JSON
line, and a `write_todos` call lands in it the moment the agent makes it. fbtodo tails that
file, keeps every `write_todos` it has seen, and renders the newest. A new list **replaces**
the old one wholesale — state is never merged — and when the session changes, the old list is
dropped immediately instead of lingering.

`--source auto` (the default) prefers a live CLI chat for the current directory. An explicit
`-s cli|desktop` is never answered from cached watcher state unless that state describes the
same backend.

The instance to follow is found in this order:

1. `--instance-of PID` — the agent process launched by that shell.
2. `--watch-pid PID` — take this pid as the instance, outright.
3. Otherwise the agent processes are enumerated (`ps`), a cwd match on the current directory
   is preferred, and the **youngest** wins. A directory can host several sessions, and "the
   one started here" is what a human means.

## The generic sources

Anything that can write one JSON object can drive the pane. Neither of these knows what a
Freebuff is, and neither watches a journal, a process or a database.

### `fbtodo push` — a state on stdin

```sh
echo '{"session": "CI7", "goal": "land the release",
       "todos": [{"task": "run the suite", "completed": true},
                 {"task": "tag it"}]}' | fbtodo push
```

The object is normalized to the fields a list is made of — `session`, `title`, `summary`,
`goal`, `goal_source`, `now`, `nudge`, `todos`, `model`, `turn_ended`, `iteration`, `list_id`,
`list_version`, `ts` — and put through the same `finish_state` a watched list goes through.
So the counts, the session and the list number are the tool's, not the pusher's: a pusher that
sends nothing but `todos` gets a first list, and one that sends the whole object `fbtodo json`
printed is not punished for the extra keys.

- `--to PATH` writes that file instead of the live state — nothing else reads it, which is how
  a demo or a test drives a pane without touching yours.
- `--dry-run` validates and prints, writing nothing.
- `--quiet` prints nothing.
- Anything that is not a JSON object with a list of `{task, completed}` steps is refused, and
  the state it refused to replace is left alone. Empty stdin, non-JSON, a non-object and a
  malformed `todos` each exit non-zero (66 / 65).

Because a pushed state is written through the same path a watched one is, the readers that
prefer a live state file answer from it: a hand-pushed list needs no watcher to be shown.

### `-s file:PATH` — a state read off disk

```sh
fbtodo bar -s file:/srv/agent/state.json     # "todos 3/5"
fbtodo json -s file:$FBTODO_HOME             # a directory means that home's state file
```

The prefix is required — `-s some/dir` stays the usage error it has always been — and a
directory expands to the `fbtodo-state.json` inside it, so `-s file:$FBTODO_HOME` reads the
state a run with that home wrote.

This is a **re-player, not a re-numberer**. Nothing here is watching a process, so the state
inside is taken at its word, including the list number it recorded: a file that says `#7`
prints `#7`, and one that recorded no number starts at the first. A file that is not there
reports that, rather than reading this machine's own list. `instance_alive` is always true —
there is no process behind a file to outlive the pane — so a file source never reads as "the
session went away".

The pair together is the seam for anything that is not a Freebuff: an agent on another
machine, a CI job, a script, or a hand-typed JSON can publish a list, and the pane draws it.
