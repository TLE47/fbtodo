# Estimates

The estimator is where most of the care in fbtodo has gone, so it is documented here rather
than in the README. Nothing in this file is needed to use the tool; it is the reasoning behind
the numbers the pane puts beside a step, and the way those numbers are scored against what
actually happened.

## Clocks, estimates, ETA

- A step's clock starts when the watcher **sees it running**, not when the list was written,
  and it is measured from the tick. A step the watcher never saw running has no duration of
  its own — a quiet gap reads as a broken pane, so the pane names the reason instead.
- **Paint and poll are separate clocks.** The store is polled on `-i` (1 s); the *clock*
  repaints far more often (default every `--tick` 5 s, 1 s while a step is counting) so the
  seconds move even when nothing else does.
- **Estimates** ride the same row as the duration: `~1m` for a pending step, `4m10s / ~3m`
  for the active one. The number walks a ladder of evidence: what steps of the same **size**
  took — the calls a step has made so far, log-binned (`calls2` is 4-7 calls) — else what
  steps of the same **kind** took, read from the wording (a `run`-ish step's usual number of
  calls times what a call costs), blended half-and-half with the list's own pace. The size
  rung only speaks once the step has made **16 calls** (`calls4`): the memory is built from
  finished steps' *full* tallies and read with a *running* one, so a smaller running tally
  names the steps that stopped that small — at the first call the rung was off by a median
  14× and lost to the pace on 83% of steps. Past 16 calls it is off by a median 2.0× against
  the pace's 2.5× and wins on 79% of the steps it fires on, so a step still under the floor is
  priced by the blend or the pace instead. That blend
  is a tail-smoother rather than a clear win — measured on your own replay it is a coin flip
  on the typical step and only plainly better on the worst ones — so `FBTODO_BLEND_WEIGHT`
  turns it down if you would rather have the pace alone. All of it
  lives in the task log beside it: `fbtodo-tasks.jsonl`, an append-only stream of events,
  and `fbtodo-tasks.json` as its fold — the JSON is what is read, the stream is what is
  believed. A step that has made no calls *and* has nothing
  to blend still falls back to the pace, so a fresh log behaves exactly as it always did.
- **A step under 10 s is a list flip, not work.** It is shown on its row, but it sets no pace
  and enters no memory: measured 2026-09-29, a 2 s flip had once projected a whole list at
  `~2s` while the next step took 1m53s.
- **When the evidence disagrees, you get a range.** Three finished steps of 10 s, 2 m and
  20 m do not entitle the pane to say `~2m` and stop there, so it says `~2m (10s–20m)`
  instead — on the step rows, on `EST REM`, and in `fbtodo status`. Below a 3× spread the
  plain number is kept (2 m from 2 m *is* 2 m), and `fbtodo status` prints the sample count
  behind every number, so `~2m, 1 sample` and `~2m, 9 samples` are told apart. A piped
  `snap` deliberately keeps the bare `~2m`: that token is a published contract.
- **Every estimate is scored once the step closes**, and `fbtodo status` shows the running
  result by source (e.g. `shape 1.8x median over 9 · blend 2.2x median over 40 · pace 3.4x
  median over 31`; the numbers are whatever your own steps did).
  A factor of 1.0x would be exact; two steps that took twice their estimate and half of it
  count the same. That line is the only honest answer to "are these numbers getting
  better?", and it is also what tells you whether the size memory is pulling its weight on
  your own work.
- **The score has a second line, and it is the one to trust**: `forecast error`. Each step's
  prediction is written down **once**, on the first poll that sees it running — before
  anything about its size is known — and every rung is scored on every step, so the rungs are
  compared on the same population. The `estimate error` line above scores the number the pane
  was showing as the step *closed*, whose size key is built from calls the step had already
  made by then; that can flatter a rung that recognises a step rather than predicting it.
  A step the watcher only picked up mid-flight (a restart) is flagged and left out of the
  score rather than counted as a forecast it never was, and `status` says how many were set
  aside. The memory behind it is kept for 60 days / 2000 records — about a month of
  real use — because the estimates are the one thing here meant to improve with use. A step
  that has finished is kept even after the agent rewrites its list: a measured span *is* the
  evidence both score lines are computed from, and dropping it left the live scores looking
  at nothing but the list on screen.
- **`fbtodo ledger` is those rows themselves**: one per step, newest first, with what each
  rung predicted, the span it was scored against and each rung's miss — and the honest note
  when a row could not be scored (still running, too short to be evidence, or stamped after
  the step had already started). `status` gives the average; `ledger` gives the argument.
- **`status` also says when the numbers could be re-chosen**, as `refit readiness`: how many
  closed steps carry a forecast (a median wants ~30 before one step stops being the whole
  distribution), and how often the young-list bound actually *changed* a number — the bound is
  consulted on the first steps of a list and moves nothing on most of them, so that second
  count is the one that governs it, and the line extrapolates the spans it would take.
- Past twice the estimate a step is marked `[STUCK?]` — a hint, not a verdict.
- The **list's own age** rides on `LIST:` (`LIST: #7 · 12m ago`), so a list the agent has
  stopped re-writing is visible while a step's clock is still counting. A narrow strip spends
  the model's pace first, then that age, and only then the whole `LIST:` field.
- A **finished list is dropped the moment the next turn starts**, so it can never be read as
  the new turn's progress: the bar, the steps and the heading it was written under all go at
  once, and the pane says `last turn's list is done — waiting for this turn's list` until a new
  one arrives. What the age can still catch is therefore work continuing *inside* the turn that
  wrote the list: every step ticked, and the session writing for ten minutes past it. Then the
  age gains `[STALE?]` and `fbtodo status` says `list behind : yes` — the one case fbtodo can
  actually prove, and stated as a question because it is still a guess: the agent may be
  tidying up, or it may have decided the rest of the work needed no list at all. A list with
  steps *left* on it is never flagged, because a long step is not a stale list — its own clock,
  and `[STUCK?]` past twice its estimate, are the record there.
- The bar carries `EST REM` and an `ETA`. Where a row has room for only one of them, the
  spread takes the place of the ETA — it says something the bare number cannot, while the
  ETA is that same number told as a clock. `fbtodo status` shows the remembered pace, its
  spread, and where each step's number came from. The pace and the model are **not** on the
  strip: the pace is already on every step row, where it is a prediction about that step,
  and the model is on the top border.

## The spread: an estimate carries its range

A rung's score is a distribution, not a point. `forecast_error` reports, for each rung that
fired, `n`, `med`, `mean`, `worst` — and `lo`/`hi`: the nearest-rank quantiles of the ratio
distribution with the tails dropped. So `status` and `ledger` can say `~2m (10s–20m)` from
the same numbers they were already counting, and a rung whose median looks fine but whose
upper tail is enormous is visible as a spread rather than hidden behind the median. The
`late` pseudo-rung — the steps the watcher only picked up mid-flight, which are counted so
they can be *excluded* — deliberately has no interval: it is a count of "not a forecast", not
a forecast to score.

## The duel: two rungs, compared properly

Comparing two rungs by their medians alone would be wrong twice over. Two runs of the same
strategy produce different medians by luck, and **steps inside one session are not
independent** — the same list, the same model, the same day. `rung_duel(rows, a, b)` pairs
the two rungs over the *same steps* (a step is only in the comparison if both rungs have a
score for it) and then reduces each **session** to one number: the mean of
`log(err_a) - log(err_b)` over that session's paired steps. Session, not step, because a
session that ran twelve long steps is one story and not twelve pieces of evidence.

The test is the **exact sign-flip (Fisher randomization) test** on those session means. Under
the null each session's difference is a fair coin, so all `2^n` sign assignments are counted
and `p` is the share whose mean is at least as far from zero as the observed one. Exact rather
than asymptotic, and with no bootstrap and no seed: the same log reports the same
`p` and the same winner on every run and every machine. It reports `steps`, `sessions`,
`share_steps` (the raw paired win rate, kept for the reader), `effect` (the mean log-ratio
difference), `p`, `flips`, and `winner` — which stays `None` unless `p <= 0.05` **and** there
are at least `DUEL_MIN_SESSIONS` (six): below that the sign test has too few signs to resolve
anything, and a duel that does not resolve says the log is too small rather than crowning the
rung that leads today. A duel with no pairing reports `steps == 0, winner is None`, which is the
honest answer rather than a coin flip dressed as a result. The assignments are not walked one
by one: `_sign_flip_extreme` splits the session differences in two, enumerates each half's
`2^(n/2)` signed sums and binary-searches one against the other, which is the same integer for
`2^(n/2)` work instead of `2^n` — so the exact arm reaches `DUEL_EXACT_MAX` (40) sessions
rather than 20. Past that even the halves stop being cheap, the signs are sampled and `exact`
says so; a sampled `p` is `(k+1)/(m+1)` rather than `k/m`, so a share that landed on zero
reports a small probability instead of an impossible one. Real logs sit far below the ceiling.

`fbtodo status` shows the duel's winner beside the rungs' spreads; `fbtodo ledger` prints the
paired vector next to each step's outcome. The whole thing is deterministic, so a run is
reproducible and the golden frames do not move.

## Shadow rungs: scoring a strategy that is not shipped

A strategy can be *scored* before it is trusted. A **shadow rung** is a rung that is not in
the ladder the pane picks from — it never prices a step — but is written into the forecast on
the same first poll and scored on the same population as the real ones, so `ledger` and
`status` can say what it *would* have done. `recent_pace_ms` is the one shipped shadow rung
today: the list's own recent pace, asked beside the shipped rungs and never selected. Its
score is visible in exactly the places a shipped rung's is, which is the point — a strategy
earns a place in the ladder by beating the ladder in the ledger, not by being added and
hoped for.

## Re-scoring the size floor on the ledger (2026-09-30)

The size floor `SHAPE_MIN_BUCKET = 4` (`calls4`, 16 calls so far) was chosen on 2026-09-29 by
replaying the CLI journals over 171 ticked steps. Re-scored on the **forecasts written after
that refit** — the honest ledger rather than the harness — the picture is thinner and less
flattering, and it is reported here rather than smoothed away.

Method: every `fc` vector whose `at` falls on or after 2026-09-29, its session's journal, the
calls the journal records between the turn's start and `fc.at`, less the calls already
credited to earlier finished steps of the same turn (what `track_tasks` calls `left`); the
size memory rebuilt from every step finished before `fc.at`; the rung scored against the
actual span and against the `pace` that same vector recorded. As a control, the replay
reproduces the one row the runtime did size (`fc.shape = 372793` at 13:42 on 09-29) exactly.

- 25 post-refit forecasts carry a journal. On the first poll a step has made a median of
  **1 call**, so the rung stands aside on 20 of the 25.
- At the shipped floor **4**, it fires on **5** of them: median **2.30×** / mean **2.36×** off,
  worst 3.9×, and it beats the pace that rode the same vector on **1 of 5** (that pace's median
  is **1.51×**).
- Lower floors fire on more rows and score no better: floors 1–3 fire on 6, median 2.40×, a win
  on 2 of 6; floor 5 fires on 4, median 2.13×, 1 of 4; floor 6 on 2, median 2.95×, 0 of 2.

So the ledger does **not** reproduce the earlier replay's advantage for the rung at any floor,
and the sample (five scorable rows) is too small to move a shipped constant. The floor is left
at 4. The two readings are not in contradiction: the ledger samples the **first poll**, which
is exactly the regime the earlier replay measured the rung to be at its worst in (at the first
call it misses by a median 14.1×; only past half the clock does it draw level). The rung is a
mid-flight tool and the first-poll ledger is the wrong clock to judge it on; the journal replay
samples the whole clock instead. What would settle it is a ledger that records the running tally
at each poll, not only at the first — until then the journal replay stands and this re-score is
the caveat on it.
