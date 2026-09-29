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
| `cleared`, `cleared_turn` | no list is drawn, and which drop caused it. `cleared` is a dropped list: a new session, or a **finished** list the next turn replaced. `cleared_turn` names the second and is what `no_list_reason` prints for it. A drop never touches `list_version` — the number identifies a list, and the new turn's list is what increments it |
| `turn_ended` | the journal's `shouldEndTurn` — the other half of "finished" |
| `lv` (in the task log) | the list version the step was last seen in. Read by `refit_readiness`, which reconstructs whether the young-list clip was consulted on a step: the pace a step inherited came from the steps finished before it in its OWN list, and a session holds many lists — grouping by session would count earlier turns as this step's past. Records written before the field existed group by session, which undercounts and so is the safe direction |
| `task_times` | per-step `started_ms` / `done_ms` / `elapsed_ms` / `shape`, from the tick that saw it. `shape` is the call tally the step has revealed so far, credited from the turn's own tally by order, and only for the step in flight |
| `est_ms` / `est_src` (in the task log) | the estimate the pane was showing for the step in flight, and which rung of the ladder produced it (`shape` / `blend` / `pace`), stamped on every poll while it runs. When the step closes, the pair (projection, outcome) is what `fbtodo status` scores as `estimate error`, per source — so "is this getting better?" is answered from records rather than from a tally that could drift |
| `task_history` | per-step remembered span, model-aware: `{label: {med, n}}`, where `n` is how many samples the median stands on. A state written by an older build holds a bare int and is read the same way. It no longer prices a step directly — the `own wording` rung was retired 2026-09-29 after firing 0 times in 170 replayed steps — it is read only as the remembered pace the bound leans on |
| task-log pruning | a record of THIS session that is no longer in the current list is dropped — unless it has a `done_ms`. A finished record is evidence (`estimate error`, `forecast error`, and all three memories read it), and a step that was reworded simply becomes another sample under its own key; a record that never finished is dropped because its `started_ms` would keep a clock running for a step that is on no list any more. Dropping the finished ones too (what builds ≤ 4.26.0 did) made the two score lines self-erasing: a rewritten list deleted the very step whose forecast was the only scored row in the log. Records of other sessions are never touched |
| `fc` (in the task log) | the **forecast ledger**, written once on the first poll that saw the step running: `{at, v, model, shape?, blend?, pace, pick}`. Scored by `forecast_error`, and printed by `fbtodo status` as `forecast error` — one row per rung, all three scored on every step. A vector stamped after the step had already been running (a watcher restarting mid-step — `LEDGER_FRESH_MS`) carries that step's clock inside it, so it is flagged `late` and left out of every rung, counted instead. `fbtodo ledger` prints these rows (`ledger_rows` / `fmt_ledger`): the vector, the span, each rung's factor error, and a `why` for any row that could not be scored, so a row is never left to read as a miss. Note a step ticked, unticked and worked again restarts `started_ms` while KEEPING the vector it earned on its first sighting, so `fc.at` can precede `started_ms` — the ledger says so on the row. It is the leak-free half of the scoreboard: the size rung's key on the *closing* poll is built from calls the step has already made, so `estimate_error` (`estimate error` in `status`) alone can flatter a rung that recognises rather than predicts |
| `task_calls` | `{classes: {kind: calls}, calls, rate_ms}` — the one memory a *waiting* step can use, since it has no calls to size it by: the calls a step of that kind usually takes (kind read from the wording by `label_class`) and the seconds each call costs. It is blended half-and-half, in log space, with the list's pace (`pending_blend_ms`). What that buys, re-measured 2026-09-29 on the shipped code (leave-one-session-out over 160 spans, scored against the same step's pace): a **coin flip on the typical step** — better on 77, worse on 83, median ratio 1.01x — and a real gain only in the tail (mean 3.31x against the pace's 3.75x, worst 14.9x against 33.9x). The earlier "beats the pace on every column, on 74% of steps" was a harness bug: it ordered each session's steps by span instead of by time, so the "pace so far" it compared against was the session's smallest steps. Treat the blend as a tail-smoother; `forecast error` is what settles it on real traffic |
| `task_shapes` | the memory that prices a step that has STARTED, keyed by its **size** — the calls it has made, log-binned (`calls0` … `calls4` …: 1, 2-3, 4-7, 8-15, 16-31 calls) — with `{med, n}` entries. Re-measured 2026-09-30 over the 171 ticked steps whose journals carry a per-call trail, the log-binned count is the best of the keys tried (leave-one-session-out R²(log) 0.657): the verb mix 0.378, exact call counts 0.293, and ADDING a dimension makes it worse — calls + distinct verbs 0.480, calls + a network share 0.401 — because an extra key fragments the samples rather than informing them. A fitted power law in the count matches it (0.655) and is better in the tail (mean 2.13x against 2.23x, worst 8.4x against 10.6x, within 2x on 62% against 58%), but only in 91% of session resamples: a different estimator on the same variable, not a better key. Lookups read through `sized_entry`, which applies `SHAPE_MIN_SAMPLES` and a **floor of `SHAPE_MIN_BUCKET` (`calls4`, 16 calls)** — the memory is keyed by finished steps' FULL tallies and read with a running step's PARTIAL one, so below the floor it prices the steps that stopped that small: at the first call a step sits a median of 3 buckets under the one it ends in, the rung misses by a median 14.1x and loses to the list's pace on 83% of steps. At and above the floor it misses by a median 1.96x / 3.38x mean against the pace's 2.45x / 4.39x, beats that pace on 79% of the steps it fires on (68 of the 171), and scores better in 100% of session bootstraps (2000 resamples over 14 sessions) where floors of 2 and 4 lose outright and 8 and 12 merely tie. Below the floor the rung stands aside — the blend or the pace answers — and a step that has made no calls at all has no size |
| `model` | the model the session names; the pace is quoted with it |
| `patch`, `alert` | the two optional footer facts, read from the logs that produce them |
| `ts`, `source_updated_ms`, `store_mtime_ms`, `probed_ms`, `heartbeat_ms` | the clocks. `ts` is when the drawn `write_todos` record was written (and `source_updated_ms` mirrors it), which is what the pane's `LIST: #7 · 12m ago` and `status`'s `list written` report; `store_mtime_ms` is the transcript's mtime, and `store_mtime_ms - ts` past `LIST_BEHIND_MS` in a session with every step ticked is the `[STALE?]` marker and `status`'s `list behind` |
| `turn` | `{start_ms, iterations, verbs, files, truncated}` — this turn, bounded by the request that opened it (the journal logs it on its own record). A boundary and a numerator only: nothing in the store is a denominator, so nothing here is progress. `iterations` counts records carrying `shouldEndTurn`; `verbs` is the tally of the calls `observed` is a slice of; `files` is the distinct files edited (empty on the NAS, which records no inputs); `truncated` means the walk never reached the request, so every count is a lower bound and the pane prints `9+` |
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
  Task records are the one thing kept deliberately long — 2000 records / 60 days, roughly
  365 KB — because the estimate memory is the only part of the tool that is supposed to get
  better with use, and a 7-day window was pruning records while they were still the only
  evidence there was (measured 2026-09-29: the oldest record in a real log was 6.68 days
  old). The wording map is capped by recency; the size map is keyed by log2 call-count
  buckets — seven distinct keys over the same 170 steps that needed 68 verb-mix keys — so
  that cap never evicts a bucket. Every bucket is still RECORDED, small ones included: the
  floor on the size rung (`SHAPE_MIN_BUCKET`) is on the lookup, not on what is kept, so the
  evidence for ever moving the floor stays in the log. A span under 10 s (`MIN_LABEL_MS`) is
  shown but never remembered: it is a list flip, not work.
- **The tests drive private tmux servers** (`FBTODO_TMUX`) and a throwaway `FBTODO_HOME`,
  and mute the notifiers, because some phases start a *real* watcher.

### Testing

```sh
python3 fbtodo-selfcheck.py --list          # the phases, with line numbers
python3 fbtodo-selfcheck.py --only local-session  # the cheap body + that phase, ~20 s
bash notify/test-freebuff-notify.sh         # the notification kit
```
