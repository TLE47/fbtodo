# Changelog

Versions are the `VERSION` constant in `fbtodo` and the git tag of the same name.
Entries start at the newest release; each one is a contract change, not a diff.

## Unreleased

### Changed
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
- `tests/golden.py` plus recorded `json` / `bar` / `snap` / frame output: the display
  contract, checked against fixtures with a frozen clock, and the checker itself checked by
  the suite pointing it at a mutated copy.
- The self-check reads the package statically and fails when a module loads a name that nothing
  under `fbtodo/` provides — a forgotten layer, caught before the line that needs it runs.

### Fixed
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
