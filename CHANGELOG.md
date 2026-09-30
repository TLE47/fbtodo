# Changelog

Versions are the `VERSION` constant in `fbtodo` and the git tag of the same name.
Entries start at the newest release; each one is a contract change, not a diff.

## Unreleased

### Changed
- `render` takes the palette and the colour depth as arguments (resolved from the environment only
  when they are left out), so a frame is a function of the state, the clock and the size it was
  asked for — what a recorded frame and a row-by-row repaint both need. The depth still changes
  only the ink.
- The task log is an **append-only stream** (`fbtodo-tasks.jsonl`); `fbtodo-tasks.json` is a
  fold of it carrying the offset it was folded to (`events`). A poll appends the records it
  actually changed instead of rewriting the log, and an append that lands without the rewrite
  is recovered by the next read rather than lost. `prune` folds the stream down to the records
  it kept. The stream's name is derived from the view's, so the two cannot be mismatched.
- The watcher writes `fbtodo-state.json` only when the **evidence** changes or the heartbeat
  is due, and skips the fsyncs on a rewrite whose only change is the clock (live: 0.96 → 0.50
  writes/s, 26.8 → 14.9 MB/h).
- A journal scan remembers its chunks by content, so a journal that only **grew** is folded
  from its cursor instead of re-walked: the poll after an append re-parses the tail chunk
  (live 112 MB journal: 35.5 ms → 5.5 ms, 6.9 MB → 0.6 MB of parsed JSON), and the folded
  answer is the walked one. A rewrite that also grew, a rotation and a truncation are all
  misses, because the remembered bytes are checked rather than trusted.
- Scan keys are `(chunk, -line index)` with chunks numbered from the **start** of the journal
  (`journal_scan`), because an append moves every boundary counted back from the end.
- The forecast vector gains a `recent` key (a shadow rung's value — see Added), so a step's
  `fc` is no longer exactly the three shipped rungs; readers iterate `SCORED_RUNGS` instead of
  a literal list. `status` gains two lines (`shadow rungs`, and one `rung duel` per judged
  pair) and `forecast error` prints a rung's bracket when its samples differ.

### Moved
- The program is a package (`src/fbtodo/`) behind a thin launcher (`fbtodo`). Nothing
  changes for anyone who runs, links or copies it: the launcher is the same path, takes
  the same arguments, and a copy of the package without it still runs
  (`python3 src/fbtodo/__init__.py …`).
- The one file is now **ten modules** — `base` (paths, settings, generic tools), `locks`,
  `alerts`, `scan` (the CLI journal), `desktop`, `nas`, `tasks` (the log, the clocks and the
  estimates), `sources`, `panes`, `render` — with `__init__.py` left as the front door (the
  commands and the daemon). Each module star-imports the layers under it and lists what it
  holds in `__all__`, so the package is still one namespace: `fbtodo.<name>` and every patched
  global in the self-check answer exactly as before.

### Added
- A rung's score carries its **spread**: `lo`/`hi` are the 10th and 90th percentile of its
  ratios by nearest rank, printed beside the median by `status` and `fbtodo ledger` only when
  the samples actually differ — a range of one value is not a range. The `late` count keeps
  its bare shape, because rounding a set-aside fault into an interval is the flattery that
  flag exists to prevent.
- `rung_duel` — the verdict the scoreboard cannot give: paired over the same steps, resampled
  over **sessions** (the steps in one session are not independent), and silent (`winner` is
  None) unless the draws are past 0.95 in either direction **and** there are at least four
  sessions to draw from, since a bootstrap over one session can only redraw the sample it
  started from. `status` prints one `rung duel` line per pair it can judge and `fbtodo ledger`
  the same sentence above the rows it came from.
- A **shadow rung**: scored, dueled, and pickable by nothing. `recent` — the median of the
  current list's own last three finished spans — is stamped into the same forecast vector as
  the shipped rungs, shown on its own `shadow rungs` line tagged *scored, never picked*, and
  dueled against the pace it would replace; `pick_estimate` may return a shipped rung and
  nothing else, so an idea has to earn its place from the log first.
- `tests/golden.py` plus recorded `json` / `bar` / `snap` / frame output: the display
  contract, checked against fixtures with a frozen clock, and the checker itself checked by
  the suite pointing it at a mutated copy. Recorded frames also cover a **finished** list, a list
  longer than the pane and a narrow strip, and the environment now pins `TZ` — the frames tick a
  local clock, so without it a golden only matched on the machine that recorded it.
- The self-check reads the package statically and fails when a module loads a name that nothing
  under `fbtodo/` provides — a forgotten layer, caught before the line that needs it runs.

### Performance
- The pane repaints only the rows that changed, in place, and writes **nothing** when the frame
  is unchanged (it used to clear the screen and write the whole frame on every tick: measured on
  a live pane, 3,384 → 773 B/s and one screen clear instead of one per second). A frame whose
  shape moved is still painted whole. `pane_repaint` is the whole of it, and the contract is in
  the self-check.

### Fixed
- No frame is wider than the pane it was asked for: the goal and `now` lines wrapped at a floor
  of 16 columns, the plain strip's bar kept a six-cell minimum, and a 12-column pane was drawn
  14–16 cells wide (which wraps, and takes the frame's shape with it). Nor taller: a five-row pane
  showing a long list stayed nine rows tall, and three early returns skipped the fit-down pass
  entirely. `_clamp_widths`/`_clamp_rows` are the guarantee now, and the self-check sweeps seven
  widths by seven heights over a long list, an empty one and both renderers.
- The phone's summary line had its markdown left in (`**Goal:**`, backticks, emphasis): two
  helpers were both called `_plain` and the renderer's ANSI-stripper, defined later in the file,
  won every call. The prose one is `prose_text` now.
- `read_theme` filled `THEME_PROBLEMS` by rebinding the list, so a reader in another module (it
  is `status` that prints it) saw "no problems" for a theme whose values had just been refused.
  The list is filled in place.

## 4.29.0

### Added
- `fbtodo doctor` (and `doctor --json`): answers "can this machine show a pane?"
  as a gate — python, tmux, colour depth, locale, `ps`, `lsof`, where `cwd` comes
  from, the scratch directory, the watcher, the notifier kit, and whether a live
  instance was found. Exits non-zero only on a `fail`.
- `.github/workflows/ci.yml`: both suites on Ubuntu and macOS, Python 3.9 and
  latest, in a UTF-8 256-colour environment.
- `clean_text()` / `clean_observation()`: escape sequences, control characters
  and bidirectional overrides are removed from session text at ingestion (the CLI
  reader and all `read_nas` paths) and again in `render()`, so a journal line
  cannot move the cursor, set a title, or reach the clipboard.
- Theme values are validated (`THEME_VALUE_RE`); a value that is not a colour is
  refused and reported as `theme_problems` by `status` and `status --json`, and
  the role falls back to the next source down rather than to the built-in.
- A claim on `LOCK_PATH` is now held by `flock` on the record's own descriptor:
  `lock_holder` probes it instead of trusting a stale pid, `clear_lock` refuses
  to unlink a claim it does not hold, and `--force` takes over deliberately.
- Cwd discovery asks `/proc/<pid>/cwd` first and falls back to one batched
  `lsof` call; freebuff is recognised by argv shape and executable, not by an
  absolute path.
- The size rung has a floor (`SHAPE_MIN_BUCKET = 4`): fewer than four calls is
  reported as remembered sizes, not as a measured bucket, and `status` says so.

### Changed
- Pane rendering: four inks (accent, active, success, muted) plus warn and error
  for state; a finished step is a dim tick; the active row carries its estimate
  as a badge; `GOAL` reads spent · total; the `PROGRESS` label and the pace/model
  strip are gone; the notes block is budgeted so a frame never exceeds `height`.
- Pane bell/notification text is metadata only by default (list summary, state,
  counts). The agent's own words are sent only with `FREEBUFF_PHONE_TEXT=agent`.
- `phone.sh --init` mints a 128-bit topic, and the macOS notification target is
  passed to osascript as argv rather than interpolated into a script.

### Fixed
- Two latent notify-suite defects: a commented-out line that still ran, and a
  race that counted a detached push.
- `#fff` is no longer accepted as a theme value (three-digit hex was never one).

<!-- Earlier releases: 4.29.0 is the first tagged release. Versions 4.19.0 through
     4.27.0 were bumped in-tree without a tag, so their changes are their commit
     subjects — `git log --oneline`. This file starts here rather than invent a
     history it cannot date. -->
