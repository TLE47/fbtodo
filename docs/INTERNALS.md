# Internals

Reference notes for working on `fbtodo` itself. Read [the README](../README.md) first — this
is the layer underneath it.

- [Files under `FBTODO_HOME`](#files-under-fbtodo_home)
- [The state file](#the-state-file)
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
| `backend` | `cli` \| `desktop` |
| `target` | the chat directory or database that was read |
| `source` | how it was read (`cli-journal`, `desktop-db`, `fallback`, `last`) |
| `session` | the session/thread identifier |
| `title`, `first_prompt`, `summary` | what the session is about, when the store carries it |
| `goal`, `goal_source` | the agent's `Goal:` line and where it was found |
| `now`, `nudge` | a newer request since the list was written; `nudge` when it is a continuation |
| `todos` | the list, newest `write_todos` only — state is never merged |
| `observed` | the newest calls the session actually made, `{verb, what, ts_ms}` newest first, capped at `ACTION_KEEP`. The second source: `write_todos` is the only *plan* in the transcript, so a model that skips it leaves `todos` empty — and the calls it did make are a fact, which is what the pane draws instead (`edited fbtodo · 2m ago`). Never a plan, so never a progress bar, an estimate or a tick |
| `done`, `total`, `list_id`, `list_version` | progress, and the identity of *this* list |
| `turn_ended` | the journal's `shouldEndTurn` — the other half of "finished" |
| `task_times` | per-step `started_ms` / `done_ms` / `elapsed_ms`, from the tick that saw it |
| `task_history` | per-step remembered span, model-aware |
| `model` | the model the session names; the pace is quoted with it |
| `patch`, `alert` | the two optional footer facts, read from the logs that produce them |
| `ts`, `source_updated_ms`, `store_mtime_ms`, `probed_ms`, `heartbeat_ms` | the clocks. `ts` is when the drawn `write_todos` record was written (and `source_updated_ms` mirrors it), which is what the pane's `LIST: #7 · 12m ago` and `status`'s `list written` report; `store_mtime_ms` is the transcript's mtime, and `store_mtime_ms - ts` past `LIST_BEHIND_MS` in a session with every step ticked is the `[STALE?]` marker and `status`'s `list behind` |
| `status`, `stop_reason` | why the watcher is where it is |
| `tool_version` | the build that wrote it |

`snap` / `json` / `bar` read this file when a watcher is live and re-derive it otherwise,
and an explicit `-s cli|desktop` is never answered from it unless the state describes
that backend.

## Notifier contracts

`fbtodo` invokes each watch as a subprocess and reads **nothing** back — the notifier owns
the decision and the "already sent" record. Each is optional: a missing script is skipped.

| Watch | Invocation | Cadence flag |
|---|---|---|
| ask | `ask-bell.py --quiet` | `--ask-seconds` (3) |
| stall | `pause-bell.py --watch-pid PID --quiet` | `--pause-seconds` (30) |
| pane | `pane-bell.py --quiet --keeper PATH` | `--pane-bell-seconds` (60) |

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

The keeper has its own lock on purpose: sharing the watcher's meant a watcher for another
source could hold the lock while a local window's pane was gone, and the pane never came
back.

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
python3 fbtodo-selfcheck.py --only local-session  # the cheap body + that phase, ~20 s
bash notify/test-freebuff-notify.sh         # the notification kit
```
