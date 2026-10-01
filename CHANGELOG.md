# Changelog

Versions are the `VERSION` constant in `fbtodo` and the git tag of the same name.
Entries start at the newest release; each one is a contract change, not a diff.

## Unreleased

### Changed
- `render` takes the palette and the colour depth as arguments (resolved from the environment only
  when they are left out), so a frame is a function of the state, the clock and the size it was
  asked for — what a recorded frame and a row-by-row repaint both need. The depth still changes
  only the ink.
- `rung_duel`'s exact arm counts the `2^n` sign assignments **meet-in-the-middle** instead of walking
  them: two halves of signed sums, one sorted and binary-searched, giving the same `p` and the same
  winner for `2^(n/2)` work — so `DUEL_EXACT_MAX` rises from 20 sessions to 40. Past it the signs are
  sampled as before, and a sampled `p` is `(k+1)/(m+1)` rather than `k/m`, because a few hundred draws
  that all land on one side is not evidence that no assignment could.
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
- The text filter is a **recursive walk over every string** in a state, not an allow-list of
  remembered prose keys: a thread's title, a NAS `fb_dir`, a tool name in `tool_calls` and
  every `turn` field are cleaned too. The walk runs at each source's ingest (desktop,
  file/push, NAS, CLI) as well as at the top of `render()`, and only the tool's own enum
  fields (`backend`, `source`, `status`, `goal_source`, `schema`) are left alone. The drop
  set gains U+061C, U+2028/29, U+2060-2064 and the tag characters, and a tab becomes one
  space.
- The `push` / `-s file:PATH` ingress is capped: **1 MiB** of stdin, **200** steps, **500**
  characters per string, a `task` must be a string, and a `completed` must be a **boolean**
  (`bool("false")` counted a pushed string as a finished step). The `file:PATH` read is held to
  the same 1 MiB before it is parsed, and a payload that recurses the parser past its limit is
  the data error (`65`) rather than an uncaught `RecursionError` or a silent "no state file".
  Over a cap is the data error (`65`), never a truncation; the `file:PATH` source is refused at
  the front door the same way.
- The state root is chosen at **import** but acted on by `main()` (`init_state_root`), so a
  module import creates no directory and moves no file; the legacy `~/.freebuff` migration
  happens on the first command.

### Fixed
- The `local-session` phase opens its pane with the wrapper the repository ships
  (`examples/fb.sh`), sourced from a throwaway `ZDOTDIR` and invoked as `fb`, instead of the
  `freebuff()` function a developer's own `~/.zshrc` happens to define. A runner has no such
  function, so its session ran the bare stand-in and never opened a pane. Identifying that
  pane is case-insensitive too, because Linux names the interpreter `python3` where macOS
  names it `Python`.
- The NAS-status check writes the local state file's session itself instead of reading whatever
  the last local watcher recorded. That watcher finds the operator's own journal and fills in
  their live session on a workstation, and finds no journal at all on a runner — so the same
  assertion passed on one and had nothing to compare against on the other.
- `phone.sh` sends the ntfy body with `--data-raw`, so a message that starts with `@` is text
  rather than a file curl would read and POST; `--title`/`--tags`/`--priority` lose any CR/LF,
  which would otherwise inject a header.
- `render` no longer crashes on a goal or `now` that wraps to nothing (a single space, which
  the filter leaves of a lone tab) — the heading is drawn or skipped, never indexed.

### Moved
- The suite and the notifier kit left the repository root: `scripts/fbtodo-selfcheck.py` and
  `scripts/notify/`. The top level is now only the launcher, `src/`, `tests/`, `docs/`,
  `examples/`, the packaging and the docs that describe them, and the suite resolves the
  checkout it drives from its own directory's parent rather than from a file beside it.
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
- [`examples/zshrc-autostart.zsh`](examples/zshrc-autostart.zsh) is the interactive-shell
  autostart hook, shipped rather than left in the maintainer's `~/.zshrc`: sourced from your
  `~/.zshrc`, it runs `fbtodo daemon` once so a watcher exists before any pane opens, honours
  `FBTODO_NO_AUTOSTART=1`, and starts nothing when no Freebuff is running.
- `fbtodo` is **packagable**: a `pyproject.toml` (src layout, a `fbtodo` console script →
  `fbtodo:main`, `requires-python >=3.9`, and the version read from `base.VERSION` rather than a
  second number to keep in step) makes `pipx install git+https://github.com/TLE47/fbtodo` a
  first-class way in, with no clone on `PATH`. The zero-install launcher is unchanged.
- The self-check's static read of the package now also refuses a **captured state path**: no
  function default or module-level expression outside `base.py` may read `SCRATCH`, `STATE_PATH`,
  `TASKS_PATH`, `LOCK_PATH`, `LOG_PATH`, `NAS_LOCK_PATH`, `PANE_KEEPER_PATH`, `PANE_LOG_PATH`,
  `PINS_PATH`, `LAST_PATH`, `NAS_STATE_PATH` or `NAS_LOG_PATH`. `base` picks the root at import
  and `init_state_root` may move it before the first command, so a frozen copy is how the pane
  and the daemon come to disagree about where the state lives; every read must be at call time.
- The **desktop** source shows every live thread of a store at once. When more than one thread of
  a project's `desktop-v2.db` carries a recent `write_todos`, the pane stacks them in one list,
  each under its own heading with its own `done/total`. `--threads N` (default 4; `0` restores
  the single-thread answer every earlier build gave) is how many are shown including the one
  being followed, and `--thread-live MINUTES` (default 90; `0` for no window) is how quiet a
  thread's own list may be and still count. Closed and sidebar-archived threads are never drawn,
  the followed thread is always first and is read from the state's own list, and the `threads`
  key is absent when only one qualifies — so a single-list frame is byte-identical to what it
  was and no other source changes shape.
- **`fbtodo push`** makes a state JSON on stdin the live state: the object is normalized to the
  fields a list is made of, put through the same `finish_state` a watched list goes through, and
  written where the readers look — so a script, a CI job, another agent or a hand-typed JSON
  needs no watcher to be shown. `--to PATH` writes that file instead, `--dry-run` writes
  nothing, `--quiet` prints nothing, and a payload that is not an object with a list of
  `{task, completed}` steps is refused with the state it would have replaced left alone (empty
  stdin `66`, otherwise `65`).
- **`-s file:PATH`** reads a state off disk, in every reader: a directory expands to the
  `fbtodo-state.json` inside it, so `-s file:$FBTODO_HOME` reads that home's own state. It is a
  re-player, not a re-numberer — the list number the file recorded is what the pane prints — and
  `instance_alive` is always true, since there is no process behind a file to outlive the pane.
  The prefix is required: `-s some/dir` stays the usage error it has always been, and any
  unknown `-s` value now exits `64` rather than being rejected by argparse.
- The README's demo is **recorded, not drawn**: two clips of the real pane reading the
  `docs/demo/fixture` transcript — [`demo.webp`](docs/demo/demo.webp) (~1920×700, ~21 s) for the
  pane on its own, and [`side-by-side.webp`](docs/demo/side-by-side.webp) (~1920×456, ~18 s) for
  the pane beside the scripted session that writes the list — plus three stills.
  `docs/demo/record.sh` reproduces all of it from the two tapes in one command, and each frame is
  trimmed to the box the pane actually drew ([`frame.py`](docs/demo/frame.py) measures it, because
  a frame short enough to have no empty row under the box is a frame that collapses the list) and
  then scaled to exactly 1920 px wide, so the clips are full-HD across at the height the pane
  needs. The stills are frames of that master, never the other way round.
- The demo is published as **animated lossless WebP, not an MP4 and not a GIF**. A video renders in
  a Markdown page through a codec, with a poster frame and a player, while an image renders at full
  pixel-for-pixel accuracy; a GIF renders the same way but 256 colours dithers the pane's gradient
  bar and its antialiased text. WebP is animated in GitHub's and VS Code's Markdown previews and in
  every current browser, is exact, and is smaller than the GIF it replaced — which is why `vhs`'s
  raw MP4 is now decoded to PNG frames and thrown away instead of being published. `ffmpeg -i
  demo.webp -loop 0 demo.gif` still produces a GIF for anything that needs one.
- The demo clips **show the tool, not the shell that started it**: both tapes open on a cleared
  screen, so the `cd`, the `export`s and the long `fbtodo pane` line stay inside the tape's `Hide`
  block and never reach a published frame. The pairing's tmux server is started with `-f /dev/null`
  for the same reason — a `pane-border-status` from the recorder's own tmux config would otherwise
  print `#{pane_current_path}` above each pane, which is a `/Users/…` line in every frame of the
  clip.
- Docs: [docs/SOURCES.md](docs/SOURCES.md) (every way a list can arrive),
  [docs/ESTIMATES.md](docs/ESTIMATES.md) (the estimator's methodology, moved out of the README),
  [docs/SETTINGS.md](docs/SETTINGS.md) (the variable and exit-code reference),
  [docs/ROADMAP.md](docs/ROADMAP.md), and [docs/decisions/](docs/decisions/README.md) (the
  long-form reasoning and the failures behind the mechanisms). The README is 250 lines and links
  into them.
- A rung's score carries its **spread**: `lo`/`hi` are the 10th and 90th percentile of its
  ratios by nearest rank, printed beside the median by `status` and `fbtodo ledger` only when
  the samples actually differ — a range of one value is not a range. The `late` count keeps
  its bare shape, because rounding a set-aside fault into an interval is the flattery that
  flag exists to prevent.
- `rung_duel` — the verdict the scoreboard cannot give: paired over the same steps, reduced to
  one mean log-ratio difference per **session** (the steps in one session are not independent),
  and judged by the **exact sign-flip test** over those session differences (`2^n` sign
  assignments, no bootstrap and no seed), silent (`winner` is None) unless `p <= 0.05` **and**
  there are at least six sessions. `status` prints one `rung duel` line per pair it can judge
  and `fbtodo ledger` the same sentence above the rows it came from.
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
- A list pane dragged to the other side of its session stays there — including after the pane is
  killed and kept open again. A side fixes the **axis** the list hangs off and not the edge of it:
  a strip above the session counts as `v` and a column to its **left** as `h` exactly as the two
  the splitter would pick do, a repair returns a drifted pane to the edge it was on (`move-pane
  -b`) rather than to the default one (`same-axis` panes only — an edge read off the other axis is
  not an edge), and — with no pin — a pane found on the other axis is the owner's arrangement
  rather than drift, filed as the remembered side after the same two passes a hand-resize gets.
  The edge is remembered with it (`before`), since the splitter only opens panes on the trailing
  edge: killing a list kept in the left column used to bring it back on the right. Before this,
  `move-pane -b -h` was back under the session inside one 3 s keeper pass, and `remember_layout`
  was handed the side *in force*, so the remembered side could never change from its first value
  at all. `fbtodo why` says `seen (kept, not filed yet)` while a kept side is on its way to the
  file, and a pin keeps every tooth: a pinned window is put back on the pinned axis every pass.
- The suite's zsh-integration check no longer leaves a pane keeper behind. It runs `zsh -i` inside
  a PTY, the `.zshrc` autostart hook calls `fbtodo daemon`, and `daemon` asks for a pane keeper —
  which keeps the panes of the tmux server in its **environment**: the owner's own server, since
  the check runs in the owner's pane, while the state root was the throwaway one. That keeper was
  invisible (its log is a file in a home the suite wipes) and immortal (the owner's server always
  has panes and a freebuff, so it never reached the pass that lets go), and it moved the owner's
  list pane back to the default side every 3 s for as long as it lived — the report this was found
  from. The hook is run with `FBTODO_NO_PANE=1` now, which still starts the watcher the check is
  about, and a phase asserts that no keeper it added is aimed at the server it is running in
  (`keeper_leaks`, which reads a keeper's own environment to tell the two apart).

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
