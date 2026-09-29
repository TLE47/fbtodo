# Internals

Reference notes for working on `fbtodo` itself. Read [the README](../README.md) first — this
is the layer underneath it.

- [Files under `FBTODO_HOME`](#files-under-fbtodo_home)
- [The state file](#the-state-file)
- [The remote probe protocol](#the-remote-probe-protocol)
- [Notifier contracts](#notifier-contracts)
- [The pane lifecycle](#the-pane-lifecycle)
- [Conventions worth keeping](#conventions-worth-keeping)

## Files under `FBTODO_HOME`

Defaults to `~/.freebuff`; `FBTODO_HOME` moves the whole set (which is how the self-check
stays out of the real one).

| File | Written by | What it is |
|---|---|---|
| `fbtodo-state.json` | watcher | the current rendered state (below) |
| `fbtodo-tasks.json` | watcher | the task log: every step it saw run, by `(session, task)`, with the model that ran it |
| `fbtodo-daemon.pid` / `.log` | watcher | the watcher's lock record and its log |
| `fbtodo-pane-keeper.pid` | keeper | the keeper's claim, carrying the tmux server it belongs to |
| `fbtodo-pane.log` | keeper | one line per pane that came **back** — never one that did not |
| `fbtodo-pins.json` | `pin` | per-window, per-role side/size |
| `fbtodo-last.json` | keeper | the layout each pane was last left at, per window and role |
| `fbtodo-nas-pane.pid` / `.json` / `.log` | remote watcher | its lock, its last probe state, its log |
| `fbtodo-nas.json` | remote source | the cached last list, so an `UNCHANGED` reply can be served |

Locks are pid files that are validated, not trusted: a lock whose process is gone, or whose
record does not name this server/build, is replaced rather than respected.

## The state file

`fbtodo-state.json` is the contract between the watcher and everything that renders. The
fields that matter:

| Field | Meaning |
|---|---|
| `schema` | state layout version; a state from another build is ignored, not trusted |
| `instance_pid` | the freebuff process this state describes |
| `cwd` | the directory the session runs in |
| `backend` | `cli` \| `desktop` \| `nas` |
| `target` | the chat directory, database or remote store that was read |
| `source` | how it was read (`cli-journal`, `desktop-db`, `nas-journal`, `fallback`, `last`) |
| `session` | the session/thread identifier |
| `title`, `first_prompt`, `summary` | what the session is about, when the store carries it |
| `goal`, `goal_source` | the agent's `Goal:` line and where it was found |
| `now`, `nudge` | a newer request since the list was written; `nudge` when it is a continuation |
| `todos` | the list, newest `write_todos` only — state is never merged |
| `done`, `total`, `list_id`, `list_version` | progress, and the identity of *this* list |
| `turn_ended` | the journal's `shouldEndTurn` — the other half of "finished" |
| `task_times` | per-step `started_ms` / `done_ms` / `elapsed_ms`, from the tick that saw it |
| `task_history` | per-step remembered span, model-aware |
| `model` | the model the session names; the pace is quoted with it |
| `patch`, `alert` | the two optional footer facts, read from the logs that produce them |
| `store_mtime_ms`, `source_updated_ms`, `probed_ms`, `heartbeat_ms` | the clocks |
| `status`, `stop_reason` | why the watcher is where it is |
| `tool_version` | the build that wrote it |

`snap` / `json` / `bar` read this file when a watcher is live and re-derive it otherwise,
and an explicit `-s cli|nas|desktop` is never answered from it unless the state describes
that backend.

## The remote probe protocol

One `ssh` round trip per poll, multiplexed (`ControlMaster`, `ControlPersist 60`), 12 s
timeout. The remote script prints these lines in order, and the local parser reads them back
**by prefix**, so a reply from a build that sends fewer of them still parses:

| Line | Content |
|---|---|
| `DIR <name>` | the chat directory chosen (`NODIR` and nothing else when there is none) |
| `SIZE <bytes>` | size of `chat-messages.json` |
| `MTIME <epoch-s>` | its mtime — the far side skips its parse entirely when this has not moved |
| `LIVE <0\|1>` | a freebuff process exists somewhere on the host |
| `FB <1\|0\|->\|<stale-dir>\|<started>` | the wrapper marker's verdict, and the project it names |
| state | `NONE`, `UNCHANGED`, `ERR <msg>`, or one compact JSON object (`todos`, `goal`, `now`, `nudge`, `calls`, `tools`, `ts`) |
| `PATCH <blob>` | the tail of the patch log, entries joined with `\x1c` — optional |
| `ALERT <blob>` | the same for the notifier log — optional |

Two details that are easy to get wrong:

- The parse runs **on the remote host** with `python3`; the conversation store is megabytes
  and pulling it across per poll would be absurd. `UNCHANGED` is what an idle poll costs.
- `LIVE` is answered by `pgrep -f` on the session process, and the pattern's first character
  is **bracketed** (`[m]anicode/freebuff`) — the probe's own `sh -c` argv contains the
  pattern, so an unbracketed one matched *itself* and reported a live session on a host with
  no session at all.

The marker is a one-line file on the remote host, three fields:

```
<pid> <started> <dir>
```

…written by whatever wrapper the operator uses, and read for a sharper answer than the
process probe can give: which session is live, and where it started.

## Notifier contracts

`fbtodo` invokes each watch as a subprocess and reads **nothing** back — the notifier owns
the decision and the "already sent" record. Each is optional: a missing script is skipped.

| Watch | Invocation | Cadence flag |
|---|---|---|
| finish | `todo-bell.py --nas-watch --once --quiet` | `--notify-seconds` (15) |
| ask | `ask-bell.py --quiet` | `--ask-seconds` (3) |
| stall | `pause-bell.py --watch-pid PID --quiet` | `--pause-seconds` (30) |
| pane | `pane-bell.py --quiet --keeper PATH` | `--pane-bell-seconds` (60) |
| drop | `drop-bell.py --nas --quiet` | on session death |

Every one of them also takes `--print` (resolve and report, send nothing) plus
`--title`/`--message`/`--priority`/`--tags` to send something specific, which is how you
debug one by hand. Sends are handed to `phone.sh` detached, so a poll never waits on a
network.

## The pane lifecycle

1. Something opens a pane: the shell wrapper (`fbtodo --instance-of $$`), or the keeper
   noticing a session in tmux with no pane.
2. The pane's `ensure_daemon` starts a watcher for that instance, if there is not one.
3. The watcher polls the store, writes `fbtodo-state.json`, and runs the watches on their
   clocks. The pane repaints from the state file, on its own faster clock.
4. When the instance exits, the pane exits; the watcher drops its lock and stops; the keeper
   stops once no freebuff is left (after a grace period, because the wrapper splits the pane
   *before* the CLI exists).
5. The keeper's other job is the repair: every 3 s, re-derive the pane geometry and
   `move-pane` a drifted pane back **in place** — same process, same scrollback, same step
   clocks.

The keeper has its own lock on purpose: sharing the watcher's meant a `-s nas` watcher could
hold the lock while a local window's pane was gone, and the pane never came back.

## Conventions worth keeping

- **Layout never names a colour.** Styles are resolved into roles once, and the drawing code
  asks for a role — that is what makes a theme a data change rather than a code change.
- **The plain renderer is the machine-readable path.** `snap`, `json` and `bar` stay
  line-oriented and uncoloured; colour belongs to the framed pane.
- **Every subprocess has a timeout**, and every network or ssh call is bounded. A pane is
  useless if a poll can outlive its interval.
- **A lock is validated, not trusted** — pid, build and (for the keeper) tmux server.
- **Nothing grows without a cap.** Retention is enforced about hourly as well as on demand.
- **The tests drive private tmux servers** (`FBTODO_TMUX`) and a throwaway `FBTODO_HOME`,
  and mute the notifiers, because some phases start a *real* watcher.

### Testing

```sh
python3 fbtodo-selfcheck.py --list          # the phases, with line numbers
python3 fbtodo-selfcheck.py --only nas-live # the cheap body + that phase, ~20 s
bash notify/test-freebuff-notify.sh         # the notification kit
```
