# Internals

Reference notes for working on `fbtodo` itself. Read [the README](../README.md) first — this
is the layer underneath it.

- [Layout](#layout)
- [Files under `FBTODO_HOME`](#files-under-fbtodo_home)
- [The state file](#the-state-file)
- [The scoreboard, the spread and the duel](#the-scoreboard-the-spread-and-the-duel)
- [The sources](#the-sources)
- [`fbtodo push`](#fbtodo-push)
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

The program is one package in ten modules, and it is **one namespace** still: each module
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
nas.py       the remote host: one ssh per poll, carrying the extractor that runs there
tasks.py     the task log (event stream + folded memo), the clocks, the estimates, the pruning
sources.py   the `Source` protocol and the loop that asks the four readers in order
panes.py     tmux: the pane, its layout and pins, the keeper, and the watcher loops
render.py    one state -> lines: the plain renderer, the framed pane, the theme
__init__.py  the front door: the commands, the daemon, and the import of every layer above
```

A module's own imports are the promise it keeps: `from .scan import *` at the top of
`nas.py` is why its liveness probe can read a journal timestamp. The self-check reads the
package statically and fails if any module loads a name nothing under `fbtodo/` provides —
which is what a forgotten layer looks like before the line that needs it ever runs.

The same static read keeps the **state paths** from being captured at import: `base` chooses
the root at import and `init_state_root` may move it before the first command, so a function
default or a module-level expression outside `base` that reads `SCRATCH`, `STATE_PATH`,
`TASKS_PATH`, `LOCK_PATH`, `LOG_PATH`, `NAS_LOCK_PATH`, `PANE_KEEPER_PATH`, `PANE_LOG_PATH`,
`PINS_PATH`, `LAST_PATH`, `NAS_STATE_PATH` or `NAS_LOG_PATH` would freeze the path this process
started with — the pane and the daemon disagreeing about where the state lives. Every read must
happen at call time, through `base`'s namespace.

`self_argv(here="")` is how the program re-invokes itself — the panes, the pane keeper, the
NAS watcher, and the daemon re-execing itself in the foreground: `sys.executable` plus the
**launcher**, never the asking file's own path, because a package's `__init__.py` run as a
script is not the package (every module loaded twice, no relative import resolvable). It
takes the asking file so the answer does not depend on which module holds the function; a copy
that carries the package without the launcher falls back to `__init__.py` beside the asker, and
the `__main__` guard at the top of `__init__.py` re-enters as the package, so that path works.

## The state directory

`$XDG_STATE_HOME/fbtodo` (`~/.local/state/fbtodo`, as the XDG spec says), or `FBTODO_HOME`
when it is set — one directory for the whole set, which is also how the self-check stays out
of the real one. A machine that ran from the legacy `~/.freebuff` is moved there **once**, at
import, and only when nothing is still writing it: a live watcher's pid or claim keeps the old
root (and `fbtodo doctor` says so), because copying a store out from under the process that
updates it would leave the pane reading a file nobody writes. `FBTODO_HOME` is never migrated.

| File | Written by | What it is |
|---|---|---|
| `fbtodo-state.json` | watcher | the current rendered state (below) |
| `fbtodo-tasks.jsonl` | watcher | the task log's evidence: **one event per line, appended and never edited** |
| `fbtodo-tasks.json` | watcher | the fold of that stream — every step it saw run, by `(session, task)`, with the model that ran it, and the offset it was folded to (`events`) |
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
| `backend` | `cli` \| `desktop` \| `nas` \| `file` \| `push` — who the state describes |
| `target` | the chat directory, database or state file that was read |
| `source` | how it was read (`cli-journal`, `desktop-db`, `state-file`, `push`, `fallback`, `last`) |
| `session` | the session/thread identifier |
| `title`, `first_prompt`, `summary` | what the session is about, when the store carries it |
| `goal`, `goal_source` | the agent's `Goal:` line and where it was found |
| `now`, `nudge` | a newer request since the list was written; `nudge` when it is a continuation |
| `todos` | the list, newest `write_todos` only — state is never merged |
| `threads` | present **only** when the desktop store holds more than one live thread: `[{id, title, todos, current, running, source_updated_ms}]`, the followed thread first and marked `current`. `list_groups` reads it — or, with the key absent, makes one untitled group from `todos` — and a renderer with more than one group prints each thread's heading on its first step. The key is left off a single-thread answer rather than set empty, which is what keeps every other source, a single-list frame and the recorded goldens byte-identical |
| `observed` | the newest calls the session actually made, `{verb, what, ts_ms}` newest first, capped at `ACTION_KEEP`. The second source: `write_todos` is the only *plan* in the transcript, so a model that skips it leaves `todos` empty — and the calls it did make are a fact, which is what the pane draws instead (`edited fbtodo · 2m ago`). Never a plan, so never a progress bar, an estimate or a tick |
| `done`, `total`, `list_id`, `list_version` | progress, and the identity of *this* list |
| `cleared`, `cleared_turn` | no list is drawn, and which drop caused it. `cleared` is a dropped list: a new session, or a **finished** list the next turn replaced. `cleared_turn` names the second and is what `no_list_reason` prints for it. A drop never touches `list_version` — the number identifies a list, and the new turn's list is what increments it |
| `turn_ended` | the journal's `shouldEndTurn` — the other half of "finished" |
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
| `turn` | `{start_ms, iterations, verbs, files, truncated}` — this turn, bounded by the request that opened it (the journal logs it on its own record). A boundary and a numerator only: nothing in the store is a denominator, so nothing here is progress. `iterations` counts records carrying `shouldEndTurn`; `verbs` is the tally of the calls `observed` is a slice of; `files` is the distinct files edited (empty on the NAS, which records no inputs); `truncated` means the walk never reached the request, so every count is a lower bound and the pane prints `9+` |
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
four branches.

| Class | `-s` / `backend` | `find()` reads | `miss()` says |
|---|---|---|---|
| `CliSource` | `cli` | this directory's chat journal (`log.jsonl`) | `no CLI chat for this directory` |
| `DesktopSource` | `desktop` | one thread of the desktop app's sqlite store | `no conversation DB found` |
| `NasSource` | `nas` | a session on the NAS, over ssh | `no NAS session` (a probe never misses) |
| `FileSource` | `file:PATH` | the state JSON at `PATH` (a directory means `fbtodo-state.json` inside it) | `no state file at PATH` |

The contract is three methods: `find(args, cwd)` fetches and returns the raw observation
or `None`; `describe(args, cwd, ob)` maps that observation onto the state; `miss()` is that
source's own answer when it was the one asked and found nothing. `name` is the `-s` flag's
word for a source, `backend` is the state's.

`-s auto` asks `cli` then `desktop`; every other value asks exactly one source, and the
**last source asked** speaks for the chain when none found anything (`-s cli` in a
directory with no journal is an error, never a fall-through into the desktop store).

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
   clocks. The pane repaints from the state file, on its own faster clock — and only the rows
   that **moved** are written (`pane_repaint`): an unchanged frame writes nothing at all, a
   changed row is one addressed rewrite, and only a frame whose shape moved (a resize, the
   first paint) is painted whole. Measured on a pane whose step clock and footer are running:
   3,384 → 773 B/s, and one screen clear instead of one per tick.
4. When the instance exits, the pane exits; the watcher drops its lock and stops; the keeper
   stops once no freebuff is left (after a grace period, because the wrapper splits the pane
   *before* the CLI exists).
5. The keeper's other job is the repair: every 3 s, re-derive the pane geometry and
   `move-pane` a drifted pane back **in place** — same process, same scrollback, same step
   clocks.

The keeper has its own lock on purpose: sharing the watcher's meant a watcher for another
source could hold the lock while a local window's pane was gone, and the pane never came
back.

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
push the list the pane was pointed at out of view.

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
- **A source reads, `describe` translates.** Nothing that fetches (an ssh, a DB open, a
  file stat) may happen inside `describe`, and nothing that knows the state's field names
  may happen inside `find`: that split is what makes a source's cost visible at the call
  site and keeps the three mappings comparable. Adding a field means adding it to the
  source that can supply it — the others leave it unset on purpose, which is the contract
  (`turn_ended` is false for NAS because the NAS build records no end-of-turn).
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
  and a version-stale holder is stopped and replaced rather than adopted.
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
