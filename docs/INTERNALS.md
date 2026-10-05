# Internals

Reference notes for working on `fbtodo` itself. Read [the README](../README.md) first — this
is the layer underneath it.

- [Layout](#layout)
- [Files under `FBTODO_HOME`](#files-under-fbtodo_home)
- [The state file](#the-state-file)
- [The scoreboard, the spread and the duel](#the-scoreboard-the-spread-and-the-duel)
- [The sources](#the-sources)
- [`fbtodo push`](#fbtodo-push)
- [`fbtodo board`](#fbtodo-board)
- [Notifier contracts](#notifier-contracts)
- [The pane lifecycle](#the-pane-lifecycle)
- [Conventions worth keeping](#conventions-worth-keeping)

Related, on demand: [ESTIMATES.md](ESTIMATES.md) — the estimator's methodology;
[SOURCES.md](SOURCES.md) — every way a list can arrive; [decisions/](decisions/README.md) — the
reasoning and the failures behind the mechanisms here.

## Layout

```
fbtodo                 the launcher: `src/` on the import path beside its own realpath,
                       then `main()`. This is the path everything names — a PATH
                       symlink, the pane command lines, the keeper, the daemon's own
                       foreground re-exec
src/fbtodo/            the program, a layer per module (below)
scripts/fbtodo-selfcheck.py
                       the suite: it imports the package (`load_fbtodo`) rather than
                       loading a file by path, so its patch sites patch module globals
                       the same way they always did
scripts/notify/        the watches, each invoked as a subprocess
```

The program is one package in eleven modules, and it is **one namespace** still: each module
lists what it holds in `__all__` and `__init__.py` imports it back with `from .base import *`,
so `fbtodo.<anything>` reaches the same name it always did, and the self-check's patched knobs
keep working. The layers only ever import downwards:

```
base.py      the floor: the state directory and its migration, every path, the settings read
             at import, the exit codes, and the generic tools (atomic writes, the process
             table, the clocks, ANSI/cell-width/wrapping)
locks.py     the claim files: who is watching, held by the kernel rather than by a pid
alerts.py    the two rows no store holds: the last patch outcome, the last phone alert
scan.py      the CLI journal: its record parsers, the chunk memory, `read_cli`
desktop.py   the desktop app's SQLite conversation
tasks.py     the task log (event stream + folded memo), the clocks, the estimates, the pruning
sources.py   the `Source` protocol and the loop that asks the readers in order
panes.py     tmux: the pane, its layout and pins, the keeper, and the watcher loops
board.py     every live session in one frame: discovery across both local stores, and its
             renderers (`fbtodo board`)
render.py    one state -> lines: the plain renderer, the framed pane, the theme
__init__.py  the front door: the commands, the daemon, and the import of every layer above
```

A module's own imports are the promise it keeps: `from .scan import *` at the top of
`sources.py` is why its `cli` reader can read a journal timestamp. The self-check reads the
package statically and fails if any module loads a name nothing under `fbtodo/` provides —
which is what a forgotten layer looks like before the line that needs it ever runs.

The same static read keeps the **state paths** from being captured at import: `base` chooses
the root at import and `init_state_root` may move it before the first command, so a function
default or a module-level expression outside `base` that reads `SCRATCH`, `STATE_PATH`,
`TASKS_PATH`, `LOCK_PATH`, `LOG_PATH`, `PANE_KEEPER_PATH`, `PANE_LOG_PATH`, `PINS_PATH`
or `LAST_PATH` would freeze the path this process
started with — the pane and the daemon disagreeing about where the state lives. Every read must
happen at call time, through `base`'s namespace.

`self_argv(here="")` is how the program re-invokes itself — the panes, the pane keeper, the
pane keeper, and the daemon re-execing itself in the foreground: `sys.executable` plus the
**launcher**, never the asking file's own path, because a package's `__init__.py` run as a
script is not the package (every module loaded twice, no relative import resolvable). It
takes the asking file so the answer does not depend on which module holds the function; a copy
that carries the package without the launcher falls back to `__init__.py` beside the asker, and
the `__main__` guard at the top of `__init__.py` re-enters as the package, so that path works.

`pinned_path()` is the other half of that: the PATH a pane — and every child of this program —
is handed **explicitly**, because tmux rebuilds a pane's environment from its own server's and
the interpreter of anything resolved by name follows from it (see
[The pane's environment](#the-panes-environment-is-pinned-not-inherited)). It is the opener's
PATH, `FBTODO_PATH` overrides it, and `python_of(command)` is the reader that says which
interpreter a running process ended up on — the two together are what `fbtodo status` prints
as `pane python`.

`STARTED_AT` is when this process read the package off disk, and it is what makes "am I still
the build that is there?" answerable at all: nothing re-imports a running Python process, so a
long-running pane holds the code it started with however the checkout moves under it — the one
way this tool has ever misled its owner. `source_newer_than(started, ...)` compares that stamp
against the package's own sources (`_package_sources`), `source_syntax_error` says whether the
newer build would even parse, `reload_probe_error` says whether it would actually LOAD (a parse
is not enough — the reload EXECS, so a tree that compiles but dies at import would become a
corpse where a running image was), and every long-running loop — the pane's, the watcher's and
the keeper's — asks both before starting itself over (see
[The pane lifecycle](#the-pane-lifecycle)).
`SOURCE_SETTLE_S` is the two seconds a fresh write is left alone first, so a save still landing
is never read as a finished one, and `BUILD_CHECK_S` is how often they ask at all: one listing
a second is nothing beside what either of them is already doing.

## The state directory

`$XDG_STATE_HOME/fbtodo` (`~/.local/state/fbtodo`, as the XDG spec says), or `FBTODO_HOME`
when it is set — one directory for the whole set, which is also how the self-check stays out
of the real one. A machine that ran from the legacy `~/.freebuff` is moved there **once**, at
import, and only when nothing is still writing it: a live watcher's pid or claim keeps the old
root (and `fbtodo doctor` says so), because copying a store out from under the process that
updates it would leave the pane reading a file nobody writes. `FBTODO_HOME` is never migrated.
A pane does not rediscover this root for itself: tmux starts it from the **server's**
environment, so whichever of `FBTODO_HOME` / `XDG_STATE_HOME` chose it here is written into the
pane's own command line (`pinned_env`, below), and a respawn or a login shell keeps the opener's
answer rather than the server's.

| File | Written by | What it is |
|---|---|---|
| `fbtodo-state.json` | watcher | the current rendered state (below) |
| `fbtodo-tasks.jsonl` | watcher | the task log's evidence: **one event per line, appended and never edited** |
| `fbtodo-tasks.json` | watcher | the fold of that stream — every step it saw run, by `(session, task)`, with the model that ran it, and the offset it was folded to (`events`) |
| `fbtodo-daemon.pid` / `.log` | watcher | the watcher's lock record and its log — including its own reloads, and the builds it held on |
| `fbtodo-pane-keeper.pid` | keeper | the keeper's claim, carrying the tmux server it belongs to |
| `fbtodo-pane.log` | keeper, pane | one line per pane that came **back**, and per pane that **reloaded** itself into a newer build — never one that did not. The pane itself also says which build, on its title chip, for the few seconds after the reload |
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
| `backend` | `cli` \| `desktop` \| `file` \| `push` — who the state describes |
| `target` | the chat directory, database or state file that was read |
| `source` | how it was read (`cli-journal`, `desktop-db`, `state-file`, `push`, `fallback`, `last`) |
| `session` | the session/thread identifier |
| `title`, `first_prompt`, `summary` | what the session is about, when the store carries it |
| `goal`, `goal_source` | the agent's `Goal:` line and where it was found. On the CLI it is the journal's prose (`scan_live_log`), on the desktop source the app's live `harness_state` text parts; both read it with `goal_line` and choose which heading belongs to which list with the shared `pick_goal` |
| `now`, `nudge` | a newer request since the list was written; `nudge` when it is a continuation. On the CLI both come from the journal's request records; on the desktop source from the app's live history — every user message tagged `USER_PROMPT`, with the CLI's own `is_nudge` / `pick_prompt` / `_newest` rules applied (`desktop._now_and_nudge`), so the two sources cannot drift |
| `todos` | the list, newest `write_todos` only — state is never merged |
| `threads` | present **only** when the desktop store holds more than one live thread: `[{id, title, todos, current, running, source_updated_ms}]`, the followed thread first and marked `current`. `list_groups` reads it — or, with the key absent, makes one untitled group from `todos` — and a renderer with more than one group prints each thread's heading on its first step. The key is left off a single-thread answer rather than set empty, which is what keeps every other source, a single-list frame and the recorded goldens byte-identical |
| `observed` | the newest calls the session actually made, `{verb, what, ts_ms}` newest first, capped at `ACTION_KEEP`. The second source: `write_todos` is the only *plan* in the transcript, so a model that skips it leaves `todos` empty — and the calls it did make are a fact, which is what the pane draws instead (`edited fbtodo · 2m ago`). Never a plan, so never a progress bar, an estimate or a tick |
| `done`, `total`, `list_id`, `list_version` | progress, and the identity of *this* list |
| `cleared`, `cleared_turn` | no list is drawn, and which drop caused it. `cleared` is a dropped list: a new session, or a **finished** list the next turn replaced. `cleared_turn` names the second and is what `no_list_reason` prints for it. A drop never touches `list_version` — the number identifies a list, and the new turn's list is what increments it |
| `turn_ended` | the journal's `shouldEndTurn` — the other half of "finished" |
| `turn_running` | the desktop app's own live signal — `threads.turn_state = running` with a fresh `turn_alive_at` heartbeat — carried on the observation (`DesktopSource.describe`) so `no_list_reason` says `turn running · no list yet` instead of `no write_todos call yet in this session`. A stale heartbeat is a turn that DIED, not one in flight, so it is not called running. Only the desktop source sets it |
| `files_unlisted` | the **boundary guard**: true when the turn ENDED having edited files but having published no `write_todos` since its own last edit, so the list does not account for the work that just landed. Both halves come from the CLI journal (the newest edit's position against the newest list's), so it is the CLI scan's answer alone — the desktop store carries no turn. Three consumers: the status chip says `STEPS OPEN` instead of `ALL DONE`, `unlisted_note` names the files in the strip / `status` / `snap`, and the completion bell and phone push are withheld (`todo-bell.decide`). Mid-turn it is false by construction: the boundary is where the bell decides |
| `lv` (in the task log) | the list version the step was last seen in. Read by `refit_readiness`, which reconstructs whether the young-list clip was consulted on a step: the pace a step inherited came from the steps finished before it in its OWN list, and a session holds many lists — grouping by session would count earlier turns as this step's past. Records written before the field existed group by session, which undercounts and so is the safe direction |
| `task_times` | per-step `started_ms` / `done_ms` / `elapsed_ms` / `shape`, from the tick that saw it. `shape` is the call tally the step has revealed so far, credited from the turn's own tally by order, and only for the step in flight |
| `est_ms` / `est_src` (in the task log) | the estimate the pane was showing for the step in flight, and which rung of the ladder produced it (`shape` / `blend` / `pace`), stamped on every poll while it runs. When the step closes, the pair (projection, outcome) is what `fbtodo status` scores as `estimate error`, per source — so "is this getting better?" is answered from records rather than from a tally that could drift |
| `task_history` | per-step remembered span, model-aware: `{label: {med, n}}`, where `n` is how many samples the median stands on. A state written by an older build holds a bare int and is read the same way. It no longer prices a step directly — the `own wording` rung was retired 2026-09-29 after firing 0 times in 170 replayed steps — it is read only as the remembered pace the bound leans on |
| task-log pruning | a record of THIS session that is no longer in the current list is dropped — unless it has a `done_ms`. A finished record is evidence (`estimate error`, `forecast error`, and all three memories read it), and a step that was reworded simply becomes another sample under its own key; a record that never finished is dropped because its `started_ms` would keep a clock running for a step that is on no list any more. Dropping the finished ones too (what builds ≤ 4.26.0 did) made the two score lines self-erasing: a rewritten list deleted the very step whose forecast was the only scored row in the log. Records of other sessions are never touched |
| `fc` (in the task log) | the **forecast ledger**, written once on the first poll that saw the step running: `{at, v, model, shape?, blend?, pace, recent?, pick}`. Scored by `forecast_error`, and printed by `fbtodo status` as `forecast error` — one row per rung, every rung scored on every step. A vector stamped after the step had already been running (a watcher restarting mid-step — `LEDGER_FRESH_MS`) carries that step's clock inside it, so it is flagged `late` and left out of every rung, counted instead. `fbtodo ledger` prints these rows (`ledger_rows` / `fmt_ledger`): the vector, the span, each rung's factor error, and a `why` for any row that could not be scored, so a row is never left to read as a miss. Note a step ticked, unticked and worked again restarts `started_ms` while KEEPING the vector it earned on its first sighting, so `fc.at` can precede `started_ms` — the ledger says so on the row. It is the leak-free half of the scoreboard: the size rung's key on the *closing* poll is built from calls the step has already made, so `estimate_error` (`estimate error` in `status`) alone can flatter a rung that recognises rather than predicts |
| `task_calls` | `{classes: {kind: calls}, calls, rate_ms}` — the one memory a *waiting* step can use, since it has no calls to size it by: the calls a step of that kind usually takes (kind read from the wording by `label_class`) and the seconds each call costs. It is blended half-and-half, in log space, with the list's pace (`pending_blend_ms`). What that buys, re-measured 2026-09-29 on the shipped code (leave-one-session-out over 160 spans, scored against the same step's pace): a **coin flip on the typical step** — better on 77, worse on 83, median ratio 1.01x — and a real gain only in the tail (mean 3.31x against the pace's 3.75x, worst 14.9x against 33.9x). The earlier "beats the pace on every column, on 74% of steps" was a harness bug: it ordered each session's steps by span instead of by time, so the "pace so far" it compared against was the session's smallest steps. Treat the blend as a tail-smoother; `forecast error` is what settles it on real traffic |
| `task_shapes` | the memory that prices a step that has STARTED, keyed by its **size** — the calls it has made, log-binned (`calls0` … `calls4` …: 1, 2-3, 4-7, 8-15, 16-31 calls) — with `{med, n}` entries. Re-measured 2026-09-30 over the 171 ticked steps whose journals carry a per-call trail, the log-binned count is the best of the keys tried (leave-one-session-out R²(log) 0.657): the verb mix 0.378, exact call counts 0.293, and ADDING a dimension makes it worse — calls + distinct verbs 0.480, calls + a network share 0.401 — because an extra key fragments the samples rather than informing them. A fitted power law in the count matches it (0.655) and is better in the tail (mean 2.13x against 2.23x, worst 8.4x against 10.6x, within 2x on 62% against 58%), but only in 91% of session resamples: a different estimator on the same variable, not a better key. Lookups read through `sized_entry`, which applies `SHAPE_MIN_SAMPLES` and a **floor of `SHAPE_MIN_BUCKET` (`calls4`, 16 calls)** — the memory is keyed by finished steps' FULL tallies and read with a running step's PARTIAL one, so below the floor it prices the steps that stopped that small: at the first call a step sits a median of 3 buckets under the one it ends in, the rung misses by a median 14.1x and loses to the list's pace on 83% of steps. At and above the floor it misses by a median 1.96x / 3.38x mean against the pace's 2.45x / 4.39x, beats that pace on 79% of the steps it fires on (68 of the 171), and scores better in 100% of session bootstraps (2000 resamples over 14 sessions) where floors of 2 and 4 lose outright and 8 and 12 merely tie. Below the floor the rung stands aside — the blend or the pace answers — and a step that has made no calls at all has no size |
| `model` | the model the session names; the pace is quoted with it |
| `patch`, `alert` | the two optional footer facts, read from the logs that produce them |
| `ts`, `source_updated_ms`, `store_mtime_ms`, `probed_ms`, `heartbeat_ms` | the clocks. `ts` is when the drawn `write_todos` record was written (and `source_updated_ms` mirrors it), which is what the pane's `LIST: #7 · 12m ago` and `status`'s `list written` report; `store_mtime_ms` is the transcript's mtime, and `store_mtime_ms - ts` past `LIST_BEHIND_MS` in a session with every step ticked is the `[STALE?]` marker and `status`'s `list behind` |
| `turn` | `{start_ms, iterations, verbs, files, truncated}` — this turn, bounded by the request that opened it (the journal logs it on its own record). A boundary and a numerator only: nothing in the store is a denominator, so nothing here is progress. `iterations` counts records carrying `shouldEndTurn`; `verbs` is the tally of the calls `observed` is a slice of; `files` is the distinct files edited; `truncated` means the walk never reached the request, so every count is a lower bound and the pane prints `9+`. The scan also tracks `last_edit_key` while the turn is open — the newest edit's walk position, compared with the newest `write_todos` to set `files_unlisted` — but it is a walk key (a tuple), so it is popped before the turn is written to the state. On the **desktop** source it carries `start_ms` alone, read from `threads.last_prompt_at` while the turn runs: it is the boundary `finish_state` drops a finished previous list against, and the age `turn_note` names — the store records no iterations, verbs or files |
| `status`, `stop_reason` | why the watcher is where it is |
| `tool_version` | the build that wrote it |

`snap` / `json` / `bar` read this file when a watcher is live and re-derive it otherwise, and
an explicit `-s X` is never answered from it unless the state describes that backend — compared
as a **word**, so `-s file:PATH` is `file` (`source_kind`) and never matches the live `push`
state. A `-s file:PATH` probe is finished against itself (`finish_probe`), so the file's recorded
list number is printed rather than incremented.

## The scoreboard, the spread and the duel

The task log holds a projection and an outcome for every step that was ever seen running,
and four functions read it. They answer four different questions, and keeping them apart is
the whole design.

* `estimate_error` — the estimate the pane was *showing* as a step closed. Cheap, and
  readable by a human step by step, but the size rung's key on the closing poll is built
  from calls the step has already made: it can flatter a rung that recognises rather than
  predicts. `status` prints it as `estimate error`.
* `forecast_error` — the same rungs scored on the vector written on the **first** poll that
  saw the step running, when nothing about its size was known. One entry per rung:
  `{n, med, mean, worst, lo, hi}`. `lo`/`hi` are the 10th and 90th percentile of that rung's
  ratio by **nearest rank** — a sample's own value, no interpolation, because with eleven
  steps an interpolated quantile would be arithmetic on nothing — and the tails are dropped
  on purpose: what a rung did on its very worst step is `worst`, and letting that one step
  define the interval would make every rung look equally uncertain. A rung whose samples all
  agree prints no bracket at all (`fmt_rung`): a range of one value is not a range. The
  `late` pseudo-rung is a count, not a spread, and carries no `lo`/`hi`.
* `rung_duel(rows, a, b)` — is `a` actually better than `b`, or is this the sample? Both are
  scored on the **same** steps (paired, so neither can win by being asked an easier set), and
  the evidence is reduced to **one mean log-ratio difference per session**: the steps inside
  one session are not independent, and counting them separately would let one long session
  vote twelve times. The test is the **exact sign-flip (Fisher randomization) test** over
  those session differences — all `2^n` sign assignments counted, no bootstrap and no seed, so
  `p` is a property of the log rather than of a draw. `share_steps` is the raw share of paired
  steps won and `effect` the mean difference; `p` and `flips` are what decide. `winner` stays
  `None` unless `p <= 1 - DUEL_RESOLVED` (0.05) **and** there are at least `DUEL_MIN_SESSIONS`
  (6) sessions — below that the sign test has too few signs to resolve anything. The count is
  meet-in-the-middle (`_sign_flip_extreme`: two halves of signed sums, one sorted and
  binary-searched), so the exact arm is `2^(n/2)` work and `DUEL_EXACT_MAX` is 40; past that
  the signs are sampled, `p` is `(k+1)/(m+1)`, and `exact` says so. The result is fully
  deterministic, so a re-run reports the same verdict (`--twice` and the goldens depend on it).
* `SHIPPED_RUNGS` / `SHADOW_RUNGS` — which rungs the pane may **pick**, and which it only
  keeps score for. `pick_estimate` and everything downstream of it may return a shipped rung
  and nothing else; a shadow rung is stamped into the same vector, scored by the same two
  functions and dueled against the rung it would replace, and that is all it may do. `recent`
  is the first of them: the median of the current list's last `SHADOW_SAMPLES` finished spans,
  i.e. the pace that has just delivered, which is the one thing the whole-list pace cannot be
  compared against by itself. An idea earns its place from the log before it moves a number
  somebody is looking at.

`status` prints `forecast error` (shipped rungs, median with its bracket), `shadow rungs`
(one line, tagged *scored, never picked*) and one `rung duel` line per pair `DUEL_PAIRS` can
judge — a pair nobody has scored is left out rather than shown as a draw. `fbtodo ledger`
prints the same sentence (`duel_note`) above the rows it was computed from, and shows a
shadow rung on a row only when that row carries one, so a row recorded before the rung
existed is not shown a `recent —` it never had. Both surfaces share `fmt_rung` and `duel_note`,
so a rung's spread cannot read differently in two places.

## The sources

Every reader downstream — the pane, `snap`, `json`, the estimates — reads **one** state,
and a list can come from a journal, a database, a remote host, or a plain file. That seam is
`Source`: each one answers "is there anything of this kind here?" in its own vocabulary and
then translates what it found into the state's fields, so `_snapshot` is a loop rather than
two branches.

| Class | `-s` / `backend` | `find()` reads | `miss()` says |
|---|---|---|---|
| `CliSource` | `cli` | this directory's chat journal (`log.jsonl`) | `no CLI chat for this directory` |
| `DesktopSource` | `desktop` | one thread of the desktop app's sqlite store | `no conversation DB found` |
| `FileSource` | `file:PATH` | the state JSON at `PATH` (a directory means `fbtodo-state.json` inside it) | `no state file at PATH` |

The contract is three methods: `find(args, cwd)` fetches and returns the raw observation
or `None`; `describe(args, cwd, ob)` maps that observation onto the state; `miss()` is that
source's own answer when it was the one asked and found nothing. `name` is the `-s` flag's
word for a source, `backend` is the state's.

`-s auto` asks `cli` then `desktop`; every other value asks exactly one source, and the
**last source asked** speaks for the chain when none found anything (`-s cli` in a
directory with no journal is an error, never a fall-through into the desktop store).

The desktop store commits a `messages` row only at the turn's close, so `DesktopSource`
reads the in-flight turn from the one live place the store keeps — `threads.harness_state`, a
JSON blob the app rewrites as the agent works, whose
`sessionState.mainAgentState.messageHistory` holds the tool calls as they are made. The
newest `write_todos` there is the live list (`desktop.harness_turn`, walked with SQLite's own
`json_each` so a blob that grows with the whole session is never pulled into Python — a pane
ticks once a second). `desktop._live_turn` folds it in under three rules: only while
`turn_running`, only when the list was written at or after the turn's own start
(`threads.last_prompt_at`), and only when it is not older than the committed list — so a
finished previous list is not read as this turn's progress and `finish_state` drops it the way
the CLI does. The same history carries the REQUESTS (`mainAgentState.messageHistory`'s user
messages the app tagged `USER_PROMPT`) and the agent's `Goal:` headings, so
`desktop._now_and_nudge` can say `now`/`nudge` for a request newer than the list — reusing the
CLI journal's own `is_nudge` / `pick_prompt` / `_newest`, so the two sources cannot drift
apart. The same prose carries the agent's `Goal:` heading, read with `goal_line` and chosen
by the shared `pick_goal` — the CLI journal's rule, factored out so the two sources cannot
differ — so a mid-turn desktop pane is headed exactly as a CLI pane would be; a heading
written for a newer request becomes that line's `now` instead. The two readers are held to
that promise by a **replay**: the self-check writes each case to a journal and to a harness
history at the same positions, reads both (`read_cli` and `read_desktop`), and asserts the
same `goal`, `now` and `nudge` — so a rule that holds in one store and not the other fails
as a diff between the two, not as a surprise in a pane.

`-s` is a word or a `file:PATH`, so the word set is validated by `main` rather than by
argparse's `choices`: a typo exits `64` (a usage error), and the `file:` prefix has to reach
`sources_for`. `source_path()` splits the prefix off, `source_kind()` is what a cached state is
compared against (a state's `backend` is a word, so `file:/tmp/s.json` has to compare as
`file`), and `state_file_in()` expands a directory. `SOURCE_ORDER` is unchanged.

A file source is a **re-player, not a re-numberer**: `finish_probe` gives it its own
`list_id`/`list_version` (from what it recorded, else the list's first) and finishes it against
itself, so `#7` prints `#7` instead of being incremented by a fingerprint mismatch. It also
sets `instance_alive: true`, because there is no process behind a file to outlive the pane.

## `fbtodo push`

The writer that has no watcher: `push` reads one JSON object from stdin, normalizes it through
`state_from_push` (only the fields a list is made of — `PUSH_FIELDS` — with everything else
stamped by the tool), and puts it through the **same** `finish_state` a watched list goes
through, with `claim_first=True` so an unseen list is `#1`. So a pusher cannot lie about the
counts, the session or the list number; a second push of the same list does not renumber it,
and a different list increments it.

`--to PATH` writes that file instead of the live state (what `-s file:PATH` then reads back),
`--dry-run` writes nothing, `--quiet` prints nothing, and every refusal — empty stdin (`66`),
not JSON (`65`), not an object (`65`), `todos` not a list of `{task, completed}` objects (`65`,
with `completed` a real boolean rather than anything `bool()` would coerce) — returns before the
write, so the state it refused to replace is untouched. The ingress caps live in `check_ingress`:
1 MiB of bytes, 200 steps, 500 characters per string, and a payload that recurses the parser
past its limit is the same data error. `check_file_ingress` gives the `file:PATH` door the same
treatment — the same byte cap before the read, the same `check_ingress` after it, and a missing
file left to the source's own "no state file" answer. `--source
file:PATH` makes the readers answer from it: because the pushed state carries `backend: push`,
a cached live state never satisfies a `file:` request, so a hand-pushed list needs no watcher to
be shown.

## `fbtodo board`

The other question a pane cannot answer: what is **every** live session doing, at once. A pane
follows one list — one cwd, one thread, one ssh — and people run two or three agents at a time,
so the board is a second frame over the same two local stores, not a new reader of them.

`board_sessions` walks `--cli-root` and `--db` and returns rows, newest activity first:

* **CLI** — every chat under `<root>/<project>/chats/*` whose `log.jsonl` mtime is inside the
  window. The order matters: `glob` + `store_mtime_ms` first, `read_cli` only for the few that
  qualify and only for the newest `BOARD_SCAN_MAX` (12) of them. A store holding a hundred
  finished chats therefore costs a hundred stats and one scan, which is what keeps a board
  cheap enough to redraw once a second.
* **Desktop** — every live thread of every project store, through `live_threads` with no
  followed thread (the board follows none: every row is somebody else's session).

Both rows are the readers' own answers — `read_cli`'s state and `live_threads`' dict — so the
board cannot disagree with a pane about what a list says; it is a subset of the fields, because
a row is one line and a state is a screenful. `board_row_cli` picks the list, the heading, the
clock and the two newer-request lines.

**Live** is a store clock, not a process: a journal is appended every iteration while the agent
works, and a desktop thread's only clock is its newest `write_todos` (the app commits per turn).
Both use the same 90-minute window (`BOARD_LIVE_MS`), the window the desktop source already
gives its stacked threads, and `--board-live MIN` overrides it (0 = no window). A session that
stopped drops off by itself, so there is no bookkeeping to go stale and nothing to clean up.
`BOARD_RUNNING_MS` (3 minutes) is the shorter question the arrow marks.

There is no third store here: the board reads the two that live on this machine, because its
whole promise is that one look is cheap.

Two renderers, split exactly as `render` splits them: `board_plain` for a pipe (a heading line
per session, its steps indented under it, no borders) and `board_frame` for a TTY — one box,
a heading row per session with the project and the age, then `--board-rows` of its steps. The
frame's `theme` and `truecolor` are arguments like every other frame's, so the same rows and the
same clock give the same bytes; `_clamp_widths` and `_clamp_rows` are the same two guarantees
behind it, and `--live` diffs one paint against the last with `pane_repaint` (cursor hidden, and
restored on every exit path). `--json` is the rows themselves, with the window it was asked for
and the count — a document, never a pane, so `--live --json` is refused at the parser (`2`).

## Notifier contracts

`fbtodo` invokes each watch as a subprocess and reads **nothing** back — the notifier owns
the decision and the "already sent" record. Each is optional: a missing script is skipped.

| Watch | Invocation | Cadence flag |
|---|---|---|
| ask | `ask-bell.py --quiet` | `--ask-seconds` (3) |
| stall | `pause-bell.py --watch-pid PID --quiet` | `--pause-seconds` (30) |
| pane | `pane-bell.py --quiet --keeper PATH` | `--pane-bell-seconds` (60) |
| locks | `locks-bell.py --quiet` | `locks --watch -i` (5) |

Every one of them also takes `--print` (resolve and report, send nothing) plus
`--title`/`--message`/`--priority`/`--tags` to send something specific, which is how you
debug one by hand. Sends are handed to `phone.sh` detached, so a poll never waits on a
network.

The `locks` notifier is the fifth and the only one whose subject is a CLAIM, so it is not
run on the watcher's clocks: it rides `fbtodo locks --watch`, which reads the audit once per
`-i` and hands a **new finding** to the ball. The keying matters as much as the send —
findings are keyed by the thing that is wrong (`orphan:<role>:<pid>`, `tie:<role>:<pid>`),
so a standing finding is printed once and never re-announced, a claim being born draws
nothing, and the watch suppresses a finding about its own pid. The bell reads
`fbtodo locks --json` for itself and keeps its OWN record of what it pushed (`--print`
resolves and sends nothing, exit 78 when there is nothing configured), so a watch restarted
mid-incident cannot double-send. Two facts make this the only surface that can report
either finding: a process whose claim name was replaced holds a lock on an inode no reader
can find, and a free file whose record names a live pid is the same name↔inode split — both
are invisible in every store, and both are things that HAPPEN rather than states, which is
what a snapshot cannot see.

## The pane lifecycle

1. Something opens a pane: the shell wrapper (`fbtodo --instance-of $$`), or the keeper
   noticing a session in tmux with no pane.
2. The pane's `ensure_daemon` starts a watcher for that instance, if there is not one.
3. The watcher polls the store, writes `fbtodo-state.json`, and runs the watches on their
   clocks. The pane repaints from the state file, on its own faster clock — and only the rows
   that **moved** are written (`pane_repaint`): an unchanged frame writes nothing at all, a
   changed row is one addressed rewrite, and only a frame whose shape moved (a resize, the
   first paint) is painted whole. Measured on a pane whose step clock and footer are running:
   3,384 → 773 B/s, and one screen clear instead of one per tick.
4. The pane keeps itself **current**: once a second it asks whether any source is newer than
   the moment this process started (`source_newer_than`), and if one is, it re-execs itself
   through `self_argv` — same pane, same pid, same tty, new code. It is deferred while
   `source_syntax_error` finds a file that will not parse (a reload into half-saved code would
   take the pane away exactly when its owner is looking at it) or while `reload_probe_error`
   finds a tree that parses but will not load (a pane that execs into a corpse is a pane the
   keeper reopens, into the same broken tree, over and over), and it pays a compile and a
   probe only when a source actually changed. Neither check can see a tree whose call sites no
   longer agree with its definitions — that parses and imports perfectly — so the poll is
   guarded too: an exception in a tick is logged once per distinct message and drawn on the
   frame's own error row (`poll failed reading desktop — OperationalError: …`), and the next
   tick polls again. Measured 2026-10-03, the unguarded version lost a working pane to a
   `TypeError` raised by an edit that was half-applied on disk; guarded, the same pane stays
   up, says so, and recovers by itself once the build is fixed. Before this, an upgrade left the list drawn by the build
   the pane had imported until somebody respawned it by hand: measured 2026-10-01, a pane
   started the day before a release still rendering the day-before's fields beside a watcher
   that had already been replaced. The watcher and the keeper now replace themselves the same
   way (below), and `live_watcher_pid` is the backstop for a watcher that cannot — a holder
   whose build is gone.
   The reload also says so **on the pane**: for `RELOAD_NOTE_S` (5 s) the title chip reads what
   `reload_note` makes of the hand-over — `RELOADED 4.30.2 → 4.30.3`, or just the build when
   the version did not move — and then it is `FREEBUFF TODOS` again. The note rides on the frame
   rather than beside it because the chip is the one slot that belongs to the process instead
   of the list: no data row is spent, the frame's rows and widths do not move, and the only row
   that changes is the top border. What one image cannot know about the other crosses the exec
   in the environment (`FBTODO_PANE_RELOADED`: the old `VERSION` and the file that changed),
   since memory is exactly what an exec throws away; the note's clock starts at the first frame
   it can be part of, so a reload whose first poll is a slow read still gets to show it.
5. A pane exits on **its window**, and on nothing else. It was told otherwise for a long
   time, in two steps: first when the process it was opened beside died, then (2026-10-03)
   when the *session* it followed was judged over — which fixed `auto` drawing the app's
   thread behind an exited CLI and still closed mid-run, and then fixed the store's clock
   reading a session the reader was still working through as ended. Every rule for it was
   still a rule about a clock, and what the pane draws is a LIST: a list whose session is
   over is a list somebody is finishing, waiting on, or re-reading. So the whole question is
   gone (2026-10-04). What ends a pane is named in `cmd_pane`: `--stale-after`, Ctrl-C, a
   `--once` that has drawn its frame, and the window going away. `--stale-after` is the
   reader's OWN window (minutes; `0`, the default, never fires) measured on the store the
   pane is following, so the one clock left is one the reader asked for. An `auto` pane has
   no session to close on at all: its subject is fixed (below), so a chat ending is a finished
   LIST on screen rather than a hand-over, and nothing about the session is left to ask. The
   watcher below it drops
   its lock and stops when its process does, and the keeper stops once no freebuff is left
   (after a grace period, because the wrapper splits the pane *before* the CLI exists).
6. The keeper's other job is the repair, in two kinds. Every 3 s it re-derives the pane
   geometry and `move-pane` a drifted pane back **in place** — same process, same scrollback,
   same step clocks. And a pane found on a different interpreter than its watcher's is
   reopened in place with the pinned command (`repair_drifted_panes`): same pane id, same
   slot, a new process on the pin (`tmux respawn-pane -k`), so a pane that drifted fixes
   itself instead of waiting for a hand — and it says so on its title chip for a few seconds,
   so the process replaced under the owner's eyes is not a silent one. The interpreter read is
   the pane's whole **tree** (`tree_pythons`), not only its own line: a watcher, a bell or a
   tmux child under it on another Python is the same drift one level down. The keeper is also the third
   long-running image to start itself over when the build under it changes (below).

The keeper has its own lock on purpose: sharing the watcher's meant a watcher for another
source could hold the lock while a local window's pane was gone, and the pane never came
back.

A keeper is one per tmux **server**, and a server has one name: the socket path, asked of
the server itself (`tmux display-message -p '#{socket_path}'`), which answers the same string
inside a session (where `TMUX` also carries the server's pid and the session id) and from a
process outside tmux (where an ask from the autostart that spawns the watcher, or from the
desktop integration, has no `TMUX` at all). Measured
2026-10-02: spelled as the raw `TMUX` value on one side and `-default` on the other, the two
asks read as two servers and each killed the other's keeper several times an hour. `FBTODO_TMUX`
is still the name verbatim — a forced server is named deliberately — and `None` is the honest
answer when nothing can be asked: an ask that cannot name a server does not evict a keeper it
cannot judge (`keeper_serves`), so only a keeper for a genuinely different server, or from an
older build, is replaced.

Naming is not comparing. `same_tmux_server` decides whether two written names are one server
by reducing each to the socket it denotes (`tmux_socket_of`): the raw `TMUX` value without
its last two comma fields, `-default` as the default server's own socket (`$TMUX_TMPDIR` or
`/tmp`, under `tmux-<uid>/`), a forced `tmux -L name` / `-S path`, and the canonical path
itself — compared by real path, so a symlinked spelling is one file too. A record written by
an older build, or by a context that forced its server, is therefore a spelling of the same
server rather than a reason to replace the keeper; only names that reduce to different
sockets are different servers.

Two asks can also arrive together, and each starts a keeper before either has claimed: the
kernel's claim decides (`write_lock`), and the loser sees the winner and stands down. What
the loser's ask must not do is call that a failure. `ensure_pane_keeper` waits for the CLAIM,
not for its own child — the pid it returns is the holder's, whoever that is — for up to
`KEEPER_CLAIM_WAIT_S`, so a second ask's keeper has time to claim. The subtle half is
`lock_holder`: it takes a free file to ask whether anyone holds it, and it used to remove
whatever it found there as a stale record. A claim is created empty and locked a breath
later, so a poll could remove the winner's newborn claim and leave it keeping every pane on
an unlinked inode, nothing on disk to name it — both asks then logged `never claimed the
lock` while keepers ran. Only a free record that names a pid is a leftover now; an empty
file is a claim being born, and a probe leaves it alone — and asks without creating one
where no file exists at all, because a question's empty claim file was itself read as a
claim by the next existence check. `fbtodo status` answers the same question before anyone
asks: `keeper server : …` prints the name the record denotes and whether the pane's ask
(from the status context, when it is a pane) and the watcher's (`tmux_identity_outside`,
the autostart's/desktop's ask from outside tmux) agree with it — a disagreement names the
ask that would replace the keeper. `--json` carries the same answer as a `keeper` object —
`running`, `pid`, `server`, the record's own spelling `recorded`, both `asks` reduced to the
sockets they name (`null` when that context cannot name one), `agree`, `would_replace` and
the row's `note` — so a script can watch for the churn instead of parsing the sentence.
`status --watch` is that watching as a command: it polls the same reader, prints the state
once (`now`), and then only moves — `pid` when the keeper is replaced (or `gone` when it
stops answering, `appeared` when one starts), `churn` when the asks stop naming its server,
`agree` when they name it again — staying silent between them (`-i` sets the poll; Ctrl-C
ends it; `--json` is one event object per line, the same fields).

The last window in that moment is the file itself: opening it and locking it are two steps,
and a probe can slip between them. It takes the free file — the leftover cleanup above —
and removes it, and the claimer then locks an inode the name no longer points at: a keeper
running invisibly, while a second one could claim a fresh file of the same name. So every
answer about a claim is tied to the NAME, not just the file. `_same_file` compares the
device and inode of the locked fd with what the path resolves to now; `lock_holder`
believes a lock or a record only while that tie holds, removes a record only under it —
never a name that has become somebody else's claim — and re-asks, bounded, when the name
moves under it. On the claimer's side, `_lock_named` re-checks after every lock and drops a
lock the name has left behind instead of holding it, `write_lock` re-checks on every later
write through a held fd, and `lock_adopt` checks the name once more after confirming the
inherited lock, so a name replaced while the exec ran is let go rather than adopted unseen.
The self-check holds the window open with a wrapped `lock_open` and threads: the claim is
taken again, visible by name, and a probe never removes a name that is not the file it
locked.

The keeper and the local watcher are the holders that can lose their name and keep running,
so each watches for it. The file can be removed or replaced under it (a probe's leftover
sweep, an admin's `rm`, a test), and the kernel lock survives on the unlinked inode while
`lock_peek` reports the role as not running and the process half of the audit names the
holder an orphan — the very finding `locks --fix` ends. So the keeper asks `lock_ours` about its own name every
`KEEPER_CLAIM_CHECK_S` (1 s), beside pane passes that `--pane-seconds` still paces: a short
claim tick must not turn each wake into a tmux survey, so the pane work waits for its own
pass while the claim is asked on the shorter clock. A name that has moved costs one `stat`
and, when it moved, a re-claim (`keeper_reclaim`): `write_lock` drops the registry entry that
no longer points at the name and takes the name as it is now — FREE or absent is the same
keeper claiming again, and a name a live process HOLDS is another keeper's, so this one
stands down on the rule its start uses. The rewritten record carries the original
`started_ms`, so a keeper of several hours is not reported as one that just began, and the
re-claim is written to the pane log (`keeper re-claimed its name`), where a wound in a
background process is otherwise invisible.

The watcher asks the same question on its own clock — its render loop already ticks at
`-i`, so `lock_ours` is checked each pass and no second clock is needed — and heals the same
way: `write_lock` takes a free or replaced name again, and a name a live process holds is
that process's, so the watcher stands down on the rule its start uses. It keeps one case the
keeper does not distinguish: a name that moved is judged against the directory's IDENTITY,
not its existence (`st_dev`, `st_ino` of the claim's own directory, captured at claim time).
A claim file that was removed or replaced leaves the directory's inode alone, so the name is
taken back; a state ROOT that was removed — even one this watcher's own state write puts
back, because `atomic_write` re-creates the directory — is a different inode, so the watcher
stops and leaves the tombstone unwritten rather than resurrect a home somebody removed.
Existence alone would be a race: the write that lands alongside the removal would re-create
the directory and the watcher would re-claim into it. The re-claim is written to the daemon
log (`watcher re-claimed its name`).

The questions a claim can be asked are a command now: `fbtodo locks` audits every claim file
this root knows — the watcher's, the keeper's, and the legacy root's
while they are still there — and answers with the same machinery the readers use, with no
write anywhere (the probe `lock_holder` removes a leftover it finds; an audit exists to say
so instead). Holder: the record's pid, tested for life. Name↔inode: a held claim blocks this
process's own open of the name, so the name's own file is what is claimed; a record naming a
LIVE pid while the file is free is the tie broken — a leftover, a claim being born, or a claim
left on an inode the name has moved off — and this process's own registry is checked exactly
(`lock_ours`), the same mismatch seen from the inside. Stale record: free with a pid inside,
which is what `lock_holder` sweeps. Clearing: end a held claim's holder (the kernel drops the
lock with it; unlinking does not), wait for the next ask on a leftover, leave an empty free
file alone — it may be about to be locked. The self-check pins each state, the command
through text and `--json`, and that not a byte of a claim file changes.

A claim file can only name a holder that still ties to its name, so the audit has a second
half the file cannot produce: a watcher or keeper whose name was replaced under it holds a
lock on an unlinked inode, and the file it left behind reads `absent` or `free` while the
process keeps running. `claim_processes` asks the process table instead: every watcher,
keeper whose command line is an fbtodo invocation of that subcommand (the
token right after the launcher, or the package's own `__init__.py`; the `--foreground` that
distinguishes a running watcher from a bare spawn that only starts one) and whose
environment names this state root. The read is the interesting half. It is the copy the
kernel made at `exec` — the root a process was STARTED with, which is the question being
asked, not whatever a later `putenv` left in its own memory — and this platform will not
always hand it over. It is asked of `/proc/<pid>/environ` first (the same copy as a file),
then `ps -Eww`, and only if neither names a root is the kernel's own copy added: `sysctl
KERN_PROCARGS2`, read directly by number instead of through a fork, and carrying the one
thing `ps` cannot report — how many bytes the kernel copied. That count is what a clip looks
like here: measured 2026-10-02, `ps` does not clip (a 250 KB environment printed whole), but
the kernel does, per process — every GUI process launched by LaunchServices came back with a
copy of 1012–1132 bytes while `ps` printed 516–974 of it, against 1644 for a launcher run
with two variables and 3044–3844 for the roles. So a copy in the `KERNEL_COPY_FLOOR` (512)
to `KERNEL_COPY_CAP` (1200) band is marked clipped, `ps`'s own share of one (1.16–2.09)
overlapping a complete copy's (1.10–1.15) too closely to stand in for the count. Two rules follow, and
both keep a guess out of a kill: a blob with no `KEY=value` in it is a command line, not an
environment, so it names no root and is never placed on the default one; and a blob that
names no root after coming back clipped is not placed either — it is reported with this root
ASSUMED, and `locks --fix` will not end it. A root that IS named is reduced the way the
program reads it (FBTODO_HOME, XDG_STATE_HOME, the default). `claim_orphans`
then keeps the ones no claim names (a held claim names its holder; a free or absent file
ties nobody; anything younger than `ORPHAN_MIN_AGE_S` is a start or a stand-down, not an
orphan), and `fbtodo locks` prints them under the claim that cannot see them, with the one
clearing that applies — end the process, because there is no file left to unlink. The
self-check pins the argv rule, the root filter, each claim state's answer and the age
guard on injected tables — plus the environment read itself: which blobs count as an
environment at all, a command line that is not one, the clip band and the floor below it, and
that a missing second source is never an invented clip. It also reads two real processes: a
child started with `FBTODO_HOME` in its environment, which is placed by it, and pid 1, whose
environment this platform will not hand over and which therefore reads as NO environment
rather than as the default root. `_proc_environ` is the program's ONE such read, and the
self-check's own reader of a process's environment goes through it too: `keeper_aim` asks
each live `pane-watch` process for its state root and tmux server to keep a test keeper from
going off keeping the OWNER's panes, and reading that with a bare `ps -Eww` would have made a
command-line-only answer (no `FBTODO_HOME`, so "a keeper elsewhere") and a kernel-clipped
copy (the root cut off) both look like the leak it exists to catch. It now carries `_read`
and `_clipped` for the shapes it cannot place, and the guard flags a keeper whose environment
it could not account for instead of skipping it. Then it drives both real cases on a private server: a CURRENT
keeper whose name is removed and then replaced by a foreign file takes the name back within
a tick — same pid, same server, same `started_ms` — so `locks` has no orphan to offer and
`--fix` no kill to plan, while a keeper that CANNOT heal (the stand-in for one from a build
older than the re-claim, down to the argv shape the rule reads) is still named under a
claim that reads `absent`, and still ended.

Every reader that only LOOKS goes through `lock_peek` for the same reason, not just the
audit: `doctor`'s watcher row, and `status` (which used to read the daemon claim through
`daemon_pid()`, the destructive probe, and would empty a dead watcher's leftover as it
reported on it). The self-check seeds the claim records with
a dead pid and runs the whole look-only family — `status`, `why`, `ledger`, `locks`,
`doctor`, `bar`, `pin --list` — asserting each leaves every record
byte-identical and still present, and nothing else under the state root moved; the
task-log writers `snap` and `json` are held to the claim files only, since recording
tracking events in the log is what they are for. Then it holds the daemon claim and checks
the row and `--json` name the holder, with no byte moving either.

Every remaining `lock_holder` caller is a path that ACTS, and each says so in place: the
start guard (`spawn_daemon`'s wait, `daemon_loop`, `ensure_pane_keeper`), the stop
(`cmd_stop`), the pane's own poll, a keeper pass (`ensure_local_panes`),
`claim_or_force` (`--force` is ending the holder) and `live_watcher_pid` (which replaces a
watcher left on another build, killing it). They must probe because they are *doing*
something to the claim: a free record naming a dead pid is a leftover to clear, and a start
that read it as a live watcher would report a keeper that is not running or stand down for
one that is not there. The self-check pins both halves — the looks leave the records
byte-identical, then `daemon_pid` removes the very leftover they refused to touch.

`fbtodo locks --fix` is that audit with the hand that acts, and it is the one command here
that ends processes. What it will do is computed first (`locks_fix_plan`) and shown before
anything moves: free leftovers are cleared through the same probe the next ask uses, so a
claim born between the audit and the fix is not deleted, and untied role processes are
ended — their lock is on an unlinked inode, so ending them is the only repair, and the next
ask re-claims. Only two things are ever planned. A process whose environment could not be
read is *named*, never ended: `claim_processes` assumes an unreadable blob is ours so the
audit can say something true, and `root_named` is the line that keeps that assumption out of
a kill. Anything that would end a process asks first — terminal or `--yes` — and a run that
cannot ask exits 66 having changed nothing; `--dry-run` changes nothing by design. A kill is
confirmed with `pid_running`, not `pid_alive`, because the process ended here is usually
somebody else's child, and an unreaped one answers `kill(pid, 0)` from beyond the grave.

On its own, a fix starts nothing: it frees the claims and the next ask re-claims them — and
that ask can be hours away, on a machine where no session starts. `--restart` is the opt-in
that runs it now (`restart_claims`): the same `ensure_daemon` a session start uses, which
asks for the keeper before it consults the watcher's lock, so one command can leave the root
watched again. It is gated exactly like the act it follows — only once something was actually
applied (a declined or unconfirmed fix changed nothing, and a start on top of that would be a
change nobody approved), and never under `--dry-run` — and it reports each role by what it
looked like BEFORE and after (`before` in `--json`, `already running` in the text), so a
no-op where a watcher was already there is visible rather than mistaken for a spawn. It never
ends anything to make room: any ending was `--fix`'s own job, already done. The flag belongs
to `locks --fix`, not to `locks`, and the guard says so.

### The pane's environment is pinned, not inherited

tmux rebuilds a pane's `PATH` from the **server's** environment, not the client's, so a pane
command that names `fbtodo` (a PATH lookup) and leans on `#!/usr/bin/env python3` can come up
on a different interpreter than the process that opened the pane. Measured 2026-10-01 on this
machine: a pane on `/usr/bin/python3` 3.9.6 beside a watcher on Homebrew's 3.14, because the
pane had been respawned into a server whose PATH no longer had Homebrew on it. It is not only a
version question — the same PATH decides the interpreter for `scripts/notify/*.py` (their
shebangs are `env python3`), so a bell and the watcher that rang it could be two different
Pythons.

So every pane command is built by `pane_command`: `/usr/bin/env PATH=<the opener's, interpreter
first>` in front of `self_argv()`, which already names the interpreter and the launcher
absolutely. The interpreter's own directory comes **first** in that PATH, because the pane is on
`sys.executable` by construction while the scripts it starts by name (`env python3` shebangs in
`scripts/notify/`) are not: without that, the pane runs one Python and its own bells run another.
`env`
carries the assignment rather than a bare `VAR=value command` prefix because the login shell
that runs a pane command may be fish, which has no such prefix form; and the assignment is
written into the command string on purpose, because it rides along in `pane_start_command` —
what the keeper, `status` and `why` read back — so a `tmux respawn-pane` of that same string
brings the pin with it. The shell wrapper (`fb`) does the same thing from the other side: it
resolves the interpreter, the launcher and the PATH in the owner's own shell, which is the last
place that PATH is known.

The same rebuild covers more than PATH, and the rest of the pin is `pinned_env`: the values the
program reads from its OWN environment to decide **where** it works. They are the state root
(`FBTODO_HOME` first, `XDG_STATE_HOME` second — the pair `_state_root` reads), the tmux server a
pane drives (`FBTODO_TMUX`), the session marker it counts live sessions by (`FBTODO_FB_MARKER`),
and the six notify-watch paths its bells are sent to (`FBTODO_NOTIFY`, `_DROP`, `_ASK`, `_PAUSE`,
`_PANE_BELL`, `_LOCKS_BELL`); they ride in the same command line as `KEY=value` arguments to
`env`. A pane whose server predates one of them — the server was started before the owner
exported `FBTODO_HOME`, or a login shell's profile set a different `XDG_STATE_HOME` — would
otherwise read another store, follow another set of sessions, or send every bell to
`~/.config/freebuff-notify/` (absent, or somebody else's, on the machine that pointed its watches
elsewhere), with nothing on screen to say so. Only values that are actually SET are
carried: an empty `FBTODO_HOME=` in the command line would read as "this process names a root"
to `_proc_environ` in `locks.py`, which has to keep a command line and an environment apart.
Every value a pane or daemon needs is carried either way: the ones that are SET ride in
the command line, which a respawn re-runs verbatim.

With the pin in place a pane and its watcher agree about the interpreter, and `fbtodo status`
prints what came of it: `pane python : … (same as the watcher)`. The pane's is
read from its process line and the nearest descendants under it (`pane_python` — a login shell
does not always hand its number over for a command that starts with an assignment), the
watcher's from its own line (`python_of`). It is a diagnostic first: this whole pin was found
by noticing two processes that disagreed, and this is the line that says so.

What that line reports, the keeper now repairs. A pane whose interpreter differs from its
watcher's **by real path** (`pane_drifted` — two spellings of one interpreter are not a drift)
is reopened in place with the pin the keeper itself would split with (`repair_drifted_panes`,
`respawn-pane -k`): the pane keeps its id and its row of the layout, and the geometry pass in
the same sweep does not have to re-place it. Nothing is respawned on a guess — no watcher to
compare against, or a pane still starting, answers "leave it alone" — and a pane whose command
is **already this build's pin** is left alone too: respawning it would re-run what it is
running, so the pair that still disagrees is the keeper's own interpreter against the
watcher's, which the pane log says once rather than acting on every pass. One attempt per
pane is remembered for `DRIFT_RETRY_S`, so a tmux that refused is asked again later, not in a
loop. Reading the pin back needed one correction: tmux reports `pane_start_command` quoted the
way a shell would quote it (`'X Y'`, `"X 'Y'"`), so `pane_start_command()` decodes what
`pane_rows` hands out — before this, comparing the reported command with the pin it was
started from was false forever.

What the repair leaves behind is a note the pane it reopened reads on its first breath. It
cannot ride the environment the way a reload's does: `respawn-pane` starts a fresh command in
the SERVER's environment, with no exec of the keeper's to carry anything, so the reason waits
at a path instead (`PANE_NOTE_PATH`, keyed by pane id) — written before the respawn, claimed
once by the pane whose own `TMUX_PANE` matches (`pane_note_take`), and dropped unshown after
`PANE_NOTE_S` so a later process taking that id is never told it was repaired. The chip says
`REOPENED (was on /usr/bin/python3)` — or `(a child was on …)` for the tree case — for the
same few seconds `RELOAD_NOTE_S` shows a reload's note, and then the pane's own name is back.

The repair is the keeper's, not the operator's, so it can be told to stand down: a pane marked
with the tmux user option `@fbtodo_repair off` (`pane_repair_off` — read per pane, and
inherited from a window or the whole server, since tmux resolves the option up the chain) is
kept as it is. The diagnosis still runs — the log names the interpreter the keeper saw and the
knob it obeyed, once per pane rather than once per pass — and so does the telling: with no
`respawn-pane` coming to deliver a note, the note is written as `kept` and claimed by the
RUNNING pane's own poll (`cmd_pane`, once a second, `sweep=False` so it touches no other
pane's entry), so the title chip says `KEPT (was on …)` where a repaired pane says
`REOPENED (was on …)`. The kinds are filtered at the claim, which is what keeps the two from
stealing each other's note: a running pane asks for `kept` only, so it can never swallow the
`reopen` note waiting for the process that will replace it. Turning the option back on (or
unsetting it) resumes the repair on the next pass — the option is read every pass, not
remembered, because the switch belongs to the operator and not to the keeper's uptime.
`fbtodo keep` is the switch as a command — `off`/`on`/`default`, the pane you are in or one
named with `--pane %3`, one window with `--window TARGET` (`window_of` resolves a session,
`session:index` or id to the id the write lands on, and `window_repair_value` reads the same
rung back), `--server` for every pane, and no verb to print what is in force — so the tmux
incantation is not something to remember.

The read is over the pane's whole tree (`tree_pythons`), not only its own line, because a pane
is not one process: the watcher it started, a bell through either, a tmux child. The pane's own
answer (`pane_python`'s — first in tree order) is the reference and every other member is
compared with it by real path (`tree_drift`), so a child on another Python is the same
disagreement one level down and gets the same repair: the pane is reopened on the pin, and the
log names the child that was on the other one. A pane whose command is already the pin is the
case a respawn cannot mend — the pin is what ran, so the child's interpreter came from
somewhere else — and the keeper says so once (`held %id: a child (pid N) is on …`) instead of
respawning on every pass.

The interpreter is not the only thing that can be behind. The rest of the pin (`pinned_env`)
arrived one key at a time across several builds, and the pin is re-run only when a pane is
respawned — which happens for a drift, or by hand, and otherwise never — so a pane opened by an
earlier build keeps its old answer about the state root, the tmux server and the bells, with
its recorded command still naming the old values. The keeper's third repair reads that recorded
command and, when it predates this build, reopens the pane once on the current pin
(`stale_pin` decides, `upgrade_stale_panes` acts): same pane id, same window, and the recorded
command becomes this build's, which is the point — it is what a later respawn would re-run.
Only the carried values are compared: `stale_pin` asks that every value this build hands on
appears as a `KEY=value` token in the line, and nothing else. The interpreter, the `PATH` and
the arguments after them are deliberately not compared, because the `fb` launcher opens a pane
that names the same values under `--instance-of` while a fresh split says `--watch-pid`, and
calling that stale would replace a working pane to change an argument nobody disagreed about;
with no values to hand on, no command is stale at all (the server's environment is the answer).
The key set is read off `wanted` rather than matched by a prefix, so `XDG_STATE_HOME` — which
rides with the `FBTODO_*` keys but is not one — counts too. The repair has the drift repair's
brakes and its memory, kept separate so one cannot spend the other's retry budget: the pane's
`@fbtodo_repair` switch keeps it, one attempt per `DRIFT_RETRY_S` (`_STALE_TRIED`), and the
`reopen` note tells the person watching (`REOPENED ON THE PIN`) rather than changing a pane
under them. A pass settles: after one respawn the recorded command is the pin and the next pass
is a no-op.

The keeper's upgrade would rewrite a stale pane on the next pass, so the pane it is about to
change is named first: `status` prints a `pane pin` line and `why` sets `stale_pin` on the
record — both from `stale_pane_ids`, the same predicate (`stale_pin`) the upgrade acts on, read
from the same rows, so the diagnosis and the repair cannot disagree about which panes are
behind. The line splits by the knob: a pane the keeper will reopen reads `on an older pin — the
keeper reopens it on the current one`, and one marked `@fbtodo_repair off` reads `kept`,
because nothing else will change it and that is the state somebody asked for. The values the
diagnostic compares against are the SESSION's, not the shell it happens to be typed in
(`session_pinned_env`): the KEEPER's own environment is read first — it is the process that
would rewrite the pane, so agreeing with it is the point — and, with no live keeper, the shell
the session is drawn in (`freebuff_pane_id`'s pane); only when neither can be read does the
reader fall back to its own `pinned_env`. A variable exported only in the CLI's shell can no
longer make a current pane read as behind, and `status`/`why` answer the same however they
were launched.

A bell is the one member of that tree no repair can ever reach: it lives for as long as it
takes to send and is gone by the time the keeper reads the process table, so its interpreter is
fixed at the launch instead. Every bell in `scripts/notify/` is an `env python3` script whose
shebang resolves in the PATH of whoever ran it, and that PATH is not always the pane's — a
watcher started by the shell autostart, or a keeper started from the desktop integration,
carries its own. `notify_argv` runs a bell AS ITSELF (never `python3 <script>`, so a
replacement written in any other language keeps working) behind an explicit `PATH=<this
process's pin>`: `/usr/bin/env PATH=… <script> …`. The shebang then resolves in the pin of the
process that rang it, wherever that process came from.

The question is not only asked while someone reads `status`: `doctor` puts it to the machine on
purpose, as one row (`one python`) — the pane, its watcher and the keeper, each read from the
processes (`pane_python`, `python_of`) and compared by real path. All of them on one
interpreter is `ok` with that interpreter shortened (`_short_python`); a disagreement is a
**warn** that prints every role with its whole path, because the two heads are the diagnosis a
shortened line would hide, and the words say what happens next (`the keeper reopens a pane on
its watcher's pin`). Fewer than two roles running is `ok` as well — `nothing to compare yet` —
because a question that cannot be answered must not look like a failure. The keeper's pid here
comes from the record it writes and whether that pid is alive, which is the pane bell's read
(`keeper_alive`); it deliberately does not go through `lock_holder`, whose contract is
destructive by design — a claim it finds free means the record is a leftover, and it removes
it — and `doctor` was only asked to look. The state root's own "a live watcher or keeper owns
this" answer is read from that same record (`_claim_live`), so emptying it would erase the fact
the line above had just reported (the self-check's state-root case is what caught it).
`lock_holder` is also what the WATCHER row used to ask, and there the removal is just as wrong:
a doctor run over a dead watcher's leftovers deleted `fbtodo-daemon.pid` — the record
`_claim_live` decides the state root from and `one python` reads a pid from. The watcher row
now asks `lock_peek`, the read-only twin of the probe: open and try the lock, with no unlink
and no write anywhere, answering held-or-free and the pid the record names (0 when it names
none). The self-check pins the whole contract: the daemon and keeper records seeded with
a dead pid — free, the exact case a cleanup removes — and a held daemon claim are all
byte-identical after a `doctor` run.

### The watcher reloads itself too, across its claim

The watcher is a long-running process for the same reason the pane is, and it was the other
half of the same problem: an upgrade left the old build **polling**, writing the old layout
into the state file until the next `fbtodo` start noticed the version (`live_watcher_pid`) and
killed it. It now asks the same question the pane asks — `source_newer_than` against `STARTED_AT`,
every `BUILD_CHECK_S` — and re-execs itself through `self_argv` on the same terms: same pid,
same log, and it holds while `source_syntax_error` finds a file that will not parse or
`reload_probe_error` finds one that parses but will not load.

What the pane does not have to solve is the claim. The watcher's whole standing is the `flock`
it holds on `fbtodo-daemon.pid`, and a gap — even the moment of an exec — is a moment a second
watcher could claim it, so the claim is carried THROUGH the exec instead of being dropped and
re-taken. An exec keeps the pid and the descriptor table (the open file description, and with
it the kernel lock), but `_LOCK_FDS` is memory, and memory is what an exec throws away; Python
also opens descriptors close-on-exec, so the lock would be dropped on the way through. So
`lock_handoff` makes the claim's fd inheritable and writes `<pid>:<fd>` into
`FBTODO_DAEMON_LOCK_FD`; `lock_adopt`, at the top of `daemon_loop`, pops that name, checks the
fd is open, is the claim file (the same inode test `lock_ours` makes) and STILL holds the lock,
and registers it again. Only then is the start guard skipped: `daemon_pid()` with the carried
claim in hand names the restarting process itself — an `flock` is per open file description, so
a second fd in the same process is refused — and the guard would stand down the watcher it had
just restarted. A hand-over that fails any check is ignored rather than obeyed: that is then a
plain start, and the ordinary guard and claim decide.

The keeper reloads itself on the same terms, and its claim has its own reason not to blink: the
keeper is what puts a pane back, so a keeper that stood down between the hand-off and the
take-back would leave the panes unwatched while its record still named it. So
`cmd_pane_watch` takes its claim back at the top (`lock_adopt(PANE_KEEPER_PATH)`), and skips
the "already running" record guard only when it did (`carried` — a fresh `fbtodo pane-watch`
still goes through the guard and the claim as before); its record, `tmux` and all, is then
re-written through the adopted fd. The build check runs in the keeper's own loop on the same
`BUILD_CHECK_S` clock, with the same hold while `source_syntax_error` finds a file that will
not parse or `reload_probe_error` finds a tree that parses but will not load: `keeper
reloading: …`, `keeper holding, source does not parse: …` or `keeper holding, the new build
does not load: …` goes to `fbtodo-pane.log`, the file `--help` points at.

The second of those checks is the one a parse cannot make. A self-reload does not re-import;
it EXECS, so the honest question is not "does the new build compile" but "can it BECOME a
process". A tree can compile file by file and still be internally inconsistent — a name
imported from a module that no longer defines it, an arity changed at a call site that runs at
import, a `raise` left at module scope — and then the exec replaces a running image with one
that dies on its own first line. `reload_probe_error` asks the new build itself: the same
command line the reload is about to exec (`self_argv`), run once in a child with
`FBTODO_RELOAD_PROBE` set, in which `main` hands off to `probe_answer` — BEFORE
`init_state_root`, the first thing that touches disk, so a probe creates nothing, moves
nothing and claims nothing. `probe_answer` does more than let the import happen. Importing runs
the module-level code and NOTHING else: a function body is name-resolved only when it runs, so
a global that was renamed or deleted hides until the one command that reaches that line is
called — and the exec has already replaced a working image with it by then. So the probe
exercises the entry point every invocation goes through — it BUILDS THE COMMAND PARSER, so
`build_parser`'s own body and every default it computes run — and then asks
`undefined_global_names` the deep question: for every scope of every module of the package,
resolved with `symtable`, does every name the code LOADS as a global still exist in the
module's live namespace (which already holds what its imports and `from .x import *` bound) or
in the builtins? A missing name is printed with its module and enclosing function and the exit
is 70, so the caller holds; zero is a yes. It is not a linter — attribute names are not
symbols, locals and closure variables are not globals, and a name bound at run time passes,
because the lookup is the live namespace.

The parse is asked the other half of the same question. A name that no longer exists is one
way a build is quietly wrong; code its own source proves will never execute is the other, and
an import cannot see either. So `unreachable_code` walks each module's AST and fails the build
on the first stretch that cannot run: a statement after the one that ends its suite (`return`,
`raise`, `break`, `continue` — nothing after it in that same list can execute) or the dead arm
of a constant conditional (`if False:`, `if True: … else:`, `while False:`, an `if`
expression with a literal test). The test is also folded when it **compares literals**
(`if 1 > 2:`, `while 0 == 1:`, a chained `0 < 1 < 0`), which `_compare_value` walks link by
link the way Python does — stopping at the first false link, so a later one that would raise
is never asked. It only ever speaks from certainty: a test is reduced only when it is a
literal, a `not` around one, or a comparison whose EVERY operand is a literal (`ast.literal_eval`
answers for each operand or the whole thing is declined), and a body is called dead only when
its test is a constant that rules it out. So a comparison against a name (`if x > 2:`), an
`in` that cannot be made (`1 in 2`), and `is`/`is not` (identity is not a value question) are
left alone, and so is a live `while True:` with a `break` or an ordinary `if x:` — the
self-check holds all of them as controls. That matters because the probe itself runs the
build: a check that called live code dead would refuse to reload a perfectly good tree. Like
the name pass it is pinned both ways — this checkout must pass, and a copy carrying a
statement after a `return`, a constant-false branch, or a literal comparison that is false
must fail and name it.

The parse is asked one more thing, and it is the one that needs an argument rather than a
table: `_guarded_in` follows a **guard**. Inside one function, `if P: return` at the top means
P is FALSE for every line below it — control only gets past the guard by not entering it — so a
later `if P:` can never run and a later `if not P:` can never take its `else`; an `assert P`
makes the same promise by the other route, since control continues past it only when P held, so
the same test is already TRUE below it and its negation already false. (`assert` is believed on
purpose: the pass runs on a build about to be exec'd, never under `-O`, which strips asserts.)
Each half of that
sentence is a place to be wrong, so each is refused unless it is certain. The guard's body must
leave on EVERY path (`_always_terminates`: the four terminators, an `if`/`else` where both arms
leave, a `with` whose body does; a `for` or `while` is never assumed to run). The two tests must
be the same expression read twice, or one of them its exact negation — and only `not`, `is` /
`is not` and `in` / `not in` are taken as negations, because `__ne__` and `__ge__` are free to
answer something other than the negation of `__eq__` and `__lt__`. The test may read nothing but
names this function binds for itself (`_function_locals`): a global or a closure cell is another
pass's to change, an attribute or a call could do anything at all, and a walrus binds — all four
are declined rather than guessed. And no statement between the two may bind one of the names the
sentence reads (`_assigned_names`, deliberately over-inclusive), because the sentence would then
no longer mean what the guard proved.

A fact is carried into the blocks the statement DOMINATES — the body of a later `if`/`with`/`try`,
its handlers, and a loop's body and `else` (`_nested_guards`) — so the same test looked at a
third time inside one of them is caught. A LOOP body is the one place a repeat, not a dominated
run, could justify the fact, so it is entered with only the facts the loop cannot disturb: any
fact whose names the loop ASSIGNS anywhere is dropped first (the same `_assigned_names` filter
every statement gets, and over the whole loop here — target, body and `else`), leaving facts a
previous pass through the body could not have changed. It is never carried across a SCOPE
boundary (a nested `def` or `class`, whose own turn it gets with its own locals). That keeps the
rule small enough to check by eye, and the self-check holds the refusals as controls (a
rebinding, a global, an attribute, a call, a loop that assigns the name) next to the baits a
guard does catch — a repeated test, a negated one, an `assert` read both ways, a repetition
nested in a dominated block, and a repetition inside a `for`/`while` body the loop does not
disturb.

The probe is a search, not a census: `unreachable_code` returns the FIRST finding across the
package, because one is enough to refuse a build. `fbtodo dead` is the same two passes with
the findings KEPT instead of cut short — `_unreachable_all` (literals and what follows a
terminator) and `_guarded_suites` (a guard's promise) are generators, the first-only readers
are their first element, and `dead_code` collects `{path, line, why}` for every file. So the
subcommand and the probe cannot disagree: the first row `fbtodo dead` prints is exactly what
`unreachable_code` would have held the build for. `--json` is the same rows, and the exit is
nonzero once anything is found.

Exit 0 is a yes; a nonzero exit, a signal, or a hang past
`RELOAD_PROBE_TIMEOUT_S` is a reason to hold. The child's last stderr line is what the log
names, so the hold says WHY (`… the new build does not load: RuntimeError: …`). Holding is the
fallback the name promises: the process keeps the build it is running — the last one that
provably loaded — and re-asks on the next `BUILD_CHECK_S`, so a tree fixed a moment later is
reloaded into by the very same pid. The probe runs only after a parse passed and a source has
actually changed, so it is one child process per real reload, not per tick.

### What a side decides, and what it does not

A layout's `side` fixes the **axis** the list hangs off, never the edge of it. `placed_beside`
accepts a strip above its session as `v` and a column to the left of it as `h` exactly as it
accepts the two the splitter would have chosen, and `place_pane_beside` sends a drifted pane
back to the edge it was already on (`pane_before`, `move-pane -b`) — but only when it was
already on that axis, because `pane_before` asked about a pane on the *other* one answers from
a position with nothing to do with the side being applied. Only the axis is policy. The edge
travels with the side into the remembered layout (`before`), because the splitter only knows
how to open a pane on the trailing edge: `split-window -h` puts it on the right, so a list the
owner keeps in the left column comes back there on the strength of that one flag.

Where that axis comes from is the second half, and it is where a keeper used to argue with its
owner: `pane_layout` gives the side the pane was **opened** on (pin, remembered, `FBTODO_SPLIT`,
default), while `kept_layout` gives the side it is **held** on. Without a pin, a pane found on
the other axis of its session is the owner's move, not drift, and travels on as `side_source`
`seen` — `fbtodo why` prints it as `seen (kept, not filed yet)` — and `remember_layout` files
it after the same two passes a hand-resize gets, so the next pane opens on it. A pin keeps
every tooth it had: it was typed, so it decides the axis and the pane is put back on it.

A keeper reads the state root it was given and keeps the panes of the tmux server in its
**environment** (`tmux_identity`), which is why a keeper started by a test hook inside the
owner's own pane is a keeper that edits the owner's layout from a scratch file. `FBTODO_NO_PANE=1`
for that hook is the guard, and the suite asserts the hook adds no keeper aimed at the server
it runs in (`keeper_leaks`).

## The frame's contract

`render(state, color, watching, width, height, now_ms, theme, truecolor)` is the whole of it. Two
of those are the reason a frame can be a recorded contract at all: the **palette** and the
**colour depth** are arguments, resolved by `render` only when they are left out, so with a state
and a frozen clock the same bytes come back whatever the terminal, the env or the theme files say
(what `tests/golden.py` checks). The depth changes the ink, never the layout: a 256-colour pane
and a 24-bit one disagree about escape sequences and nothing else.

The frame also fits the pane it was asked for, always, and that is enforced rather than intended:
`_clamp_widths` cuts every row of whichever renderer ran to `width`, and `_clamp_rows` cuts the
frame to `height`, keeping the ends (the title row and the bar/footer, or the bottom border) and
letting the middle — the list — give way. The rows each have their own budget; these two are the
guarantee behind them, and they are no-ops for a frame that already fits (`tests/golden` proves
it: not one recorded row moved when they were added). Below 30 columns there is no room for a
box, so the plain strip is drawn even on a colour terminal — the same rule that decides what
`snap` prints.

A frame can carry more than one list: `list_groups(state)` returns one group per thread (or a
single untitled group when the state has only its own `todos`), and both renderers build one
`plan` of `(group, index, todo, heading)` rows from it, printing a heading only when there is more
than one group. The heading is added **after** the `TASK_MAX_LINES` cap — it costs no step's line
— and the fit is anchored on the followed thread's current step, so stacking a second list cannot
push the list the pane was pointed at out of view. A PANE never gets there: `cmd_pane` sets
`--threads 1` from the first poll and `latch_pane_subject` then narrows the pane's own arguments
to one source plus `--thread`/`--chat`, so every later poll asks for that subject by name. The
first accepted poll is what decides — and it is still `auto`'s chain doing the deciding, after
`pane_cached_state` has dropped any cached state whose session ended, so a pane still lands on
the live thread rather than the finished chat. A finished turn does not release the lock, a second
thread going live cannot steal it, and the lock rides out in `FBTODO_PANE_LOCK` so a pane that
`exec`s itself into a new build comes back holding the same list. The stacking above is for the
reads (`json`, `snap`, `board`), which are surveys rather than somebody's window.

The bar's two numbers are **not** taken from the groups: `drawn_counts(state)` counts the
list the pane FOLLOWS (`state["todos"]`) whenever it is there, and only when it is not — a
finished list dropped by `finish_state` for a newer request, while the other threads' lists go
on being drawn — does it fall back to `done/total` summed over the groups being painted. Without
that fallback a stacked pane printed `0% (0/0)` (and `0/0 done`, and `todos -`) directly under a
heading reading `11/11`. The estimates on that row (`EST REM`, `ETA`, `GOAL`) are still measured
from the followed list, because they are still about it.

## Conventions worth keeping

- **Layout never names a colour.** Styles are resolved into roles once, and the drawing code
  asks for a role — that is what makes a theme a data change rather than a code change. The
  roles are `accent` (what to touch: the badge, the running row's marker and estimate, the
  bar's left stop), `active` (the one thing being worked on, and the goal it belongs to),
  `success` (done, dimmed for a finished step's tick, full for a clean patch and the bar's
  right stop), `muted` and `faint` (secondary and skippable), and a `track` for the bar's
  empty cells. `2` + a role is how "quiet but still that colour" is said (`done`, `tick`).
  SGR 33/31 stay in the code as `warn`/`error` because they are STATES, not palette choices.
- **One ink per job, on every row.** A finished step is one dim unit (tick and text), the
  running row is the only text with weight and the only one whose number is a badge, and a
  fact is stated once: the running clock is on the row, not repeated on the status chip;
  `EST REM` is on the bar row, and the `GOAL` row carries only what it alone has (spent and
  total).
- **Text from a source is filtered, twice.** `clean_text()` strips escape sequences whole
  (an OSC 52 leaves no `52;c;…` behind to read), then every other C0/C1 control, DEL and
  bidi/zero-width character, keeping newlines. It is applied where a source's prose enters
  the state — so the state file, `json` and `status` are clean — and again in `render()`,
  because a state file an older build wrote is still on disk. H1 measured all of it reaching
  the terminal verbatim before (OSC 52, OSC 0, CSI moves, U+202E) in the rich pane, in
  `snap` and in `json`.
- **A source reads, `describe` translates.** Nothing that fetches (a DB open, a file stat)
  may happen inside `describe`, and nothing that knows the state's field names may happen
  inside `find`: that split is what makes a source's cost visible at the call site and keeps
  the mappings comparable. Adding a field means adding it to the source that can supply it —
  the others leave it unset on purpose, which is the contract (`turn_ended` is false for the
  desktop store because it records no end of turn).
- **A theme value is validated where it is read.** `THEME_VALUE_RE` accepts a `#rrggbb`
  colour or a raw SGR parameter list and nothing else, because the value is interpolated into
  an escape sequence. A refused value leaves the role at whatever the next source down gave
  it, and is named by `status` / `status --json` (`theme_problems`): silently ignoring it is
  how a bad value survives. `_color_sgr` still understands 3-digit shorthand when called
  directly; the palette no longer feeds it any.
- **The plain renderer is the machine-readable path.** `snap`, `json` and `bar` stay
  line-oriented and uncoloured; colour belongs to the framed pane.
- **Every subprocess has a timeout**, and every network or ssh call is bounded. A pane is
  useless if a poll can outlive its interval.
- **A claim is held by the KERNEL, and the number inside it is for humans.** `write_lock`
  takes an `flock` on the file and writes the record through that same fd (a rename would
  leave the new copy unlocked and the claim lost); `lock_holder` decides who is live by
  trying to take it, because a pid can be reused and a leftover record can name a process
  that is alive and never was a watcher; `clear_lock` refuses to unlink a claim it does not
  hold (unlinking does not lift the holder's lock, and a third process would then claim a
  fresh file of the same name — two watchers, one scratch dir). The build is still checked,
  and a version-stale holder is stopped and replaced rather than adopted. The one claim that is
  carried rather than re-taken is the watcher's own across its self-reload: an exec keeps the
  open file description, so `lock_handoff`/`lock_adopt` pass the locked fd over it (see
  [The watcher reloads itself too](#the-watcher-reloads-itself-too-across-its-claim)).
- **Discovery asks `/proc` first, then one batched `lsof`** — `/proc/<pid>/cwd` is a readlink
  with no subprocess (Linux), and a machine with no `lsof` at all can still follow its own
  session. A process is the CLI when a TOKEN is one: `argv[0]` a bare `freebuff` that PATH
  resolves, a path-like token ending in `bin/freebuff` (the original rule, still a subset),
  or a path-like token that mentions `freebuff` and resolves to one. A `grep` or a `python3
  -c` whose argument names the path is not the CLI, however it is worded.
- **The task log is a stream; the JSON beside it is a memo.** One JSON object per line in
  `fbtodo-tasks.jsonl` — a record set by `k`, records removed by `drop`, the session and the
  prune marker as rows of their own — appended and never edited. `fbtodo-tasks.json` is a
  fold of that stream and carries the byte offset it was folded to (`events`), so the fold is
  last-write-wins per key and running it again says the same thing as running it once. That
  is what makes the window between an append and the memo's rewrite harmless: the next
  reader folds the events the memo missed rather than losing the step. Only the records a
  poll actually touched are appended — a list is a handful of steps and the log is a history,
  so rewriting it to change one record was the largest write the watcher made. `prune` is the
  stream's fold point (one event per record it kept), which is how it stays bounded behind
  the caps below. The stream's name is *derived* from the view's (`events_path()`), so a
  caller that points the view elsewhere (the self-check's unit blocks, a restore from a
  backup) gets its own stream by construction.
- **A journal that only grew is folded, not re-walked.** `scan_live_log` numbers its 1 MiB
  chunks from the START of the file — an append shifts nothing there, where counting back
  from the end shifts every boundary — and remembers each full chunk's parse against its
  bytes' CRC (checked, not trusted, so a rewrite that also grew is a miss, and a rotated or
  truncated file is a fresh walk). A poll re-reads its window, which the OS has in cache, and
  re-PARSES only the chunk at the end that moved: measured on the live 112 MB journal, the
  poll after an append went 35.5 ms → 5.5 ms and 6.9 MB → 0.6 MB of parsed JSON, with the
  folded answer identical to the walked one. The scan's keys are `(chunk, -line index)` — one
  tuple comparison is a time comparison — which is why the chunk numbering has to survive the
  next append.
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
python3 scripts/fbtodo-selfcheck.py --list          # the phases, with line numbers
python3 scripts/fbtodo-selfcheck.py --only local-session  # the cheap body + that phase, ~20 s
bash scripts/notify/test-freebuff-notify.sh         # the notification kit
```
