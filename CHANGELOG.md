# Changelog

Versions are the `VERSION` constant in `fbtodo` and the git tag of the same name.
Entries start at the newest release; each one is a contract change, not a diff.

## Unreleased

### Added
- `fbtodo init` — installs the `fb` launcher: detects the shell (`$SHELL`, then the launching
  process), writes the function into `~/.config/fbtodo/`, and adds the `source` line to the
  startup file (idempotent; `--shell`, `--startup-file`, `--dry-run`). One word after a
  one-time command. It carries two bodies because shells share no one function syntax: a
  POSIX body for `bash`, `zsh`, `ksh`, `mksh`, `dash` and `sh` (each with its own startup
  file), and a native body for fish.
- **The boundary guard:** a turn that ends having edited files but having published no
  `write_todos` since its own last edit is no longer announced as a finish. fbtodo already
  tallies a turn's edited files (the CLI journal gives both halves — the newest edit's
  position and the newest list's), so the pane's chip reads `STEPS OPEN` instead of
  `ALL DONE` and names the files (`3 files changed, list not rewritten`) in `snap`,
  `status` and the framed strip, while the completion bell and the phone push are withheld.
  Mid-turn it is silent by construction: the boundary is where the bell decides.
- **A pane keeps itself current.** A long-running pane keeps the build it imported, so an
  upgrade used to leave the list drawn by the old build until somebody respawned the pane by
  hand — measured on a pane running a build a day out of date. The pane now asks whether any
  source is newer than the moment it started (`source_newer_than`, against `STARTED_AT`) and
  re-execs itself through `self_argv`: same pane, same pid, same tty, new code. It waits while
  the sources do not parse (`source_syntax_error`), because the pane dies on the import and it
  would die in front of the person editing it, and it leaves the first two seconds after a
  write alone (`SOURCE_SETTLE_S`) so a save still landing is never read. The new build then
  says so **on the pane itself**, briefly and without disturbing the frame: its own title chip
  reads `RELOADED 4.30.2 → 4.30.3` for a few seconds (`RELOAD_NOTE_S`) and goes back to
  `FREEBUFF TODOS`. The chip is the one slot of the frame that belongs to the process rather
  than the list, so the note costs no row and moves nothing — the frame's only change is that
  label. The build it came FROM crosses the exec in the environment (nothing else does), the
  file that triggered it still goes to the pane log.
- **The watcher keeps itself current too.** A watcher is a long-running process as well, and it
  was the other half of the same problem: an upgrade left the old build polling — writing the
  old layout into the state file until the next `fbtodo` start noticed the version
  (`live_watcher_pid`) and killed it. It now asks the same question the pane asks
  (`source_newer_than` against `STARTED_AT`, every `BUILD_CHECK_S`) and re-execs itself through
  `self_argv` the same way: same pid, same log, and it holds while the sources do not parse.
  What the pane does not have to solve is the claim — a watcher's whole standing is the claim,
  and a gap in it is a gap a second watcher could walk into — so the locked fd is carried
  THROUGH the exec (`lock_handoff` makes it inheritable and names it; `lock_adopt` takes it back
  and bypasses the start guard, which with the claim in hand would stand down the watcher it had
  just restarted). The reload or the hold is recorded in `fbtodo-daemon.log`, a file that had
  nothing to say before.
- `fbtodo status` reports which Python a pane is **actually** on — `pane python : … (same as
  the watcher)`, read from the processes themselves (`pane_python` walks the pane's own line
  and the nearest descendants under it; the watcher's is read from its own). A pane and its
  watcher disagreeing about the interpreter is invisible from the outside — the divergence
  below was found by accident — so when they differ the line names both, and the keeper
  reopens the pane on its pin (next).
- **Automatic pane repair can be turned off per pane — `@fbtodo_repair`.** A machine can mix
  interpreters on purpose (a pane held on an older Python while everything else runs the pin),
  and the drift repair would fight that choice on every pass. The knob is a tmux user option,
  so it is per pane by construction and needs no list of ids: `tmux set -p -t %3
  @fbtodo_repair off` — or `-w` / `-g` for a window or the whole server, since tmux resolves
  the option up the chain. A marked pane is still DIAGNOSED (the keeper logs the drift and
  names the knob, once per pane and not once per pass), and it is still TOLD: with no respawn
  coming to deliver a note, the running pane picks it up on its own poll, and its title chip
  reads `KEPT (was on /usr/bin/python3)` — `KEPT (a child was on …)` for the tree case — where
  a repaired pane reads `REOPENED …`. Unset, or `on`, resumes the repair on the next pass with
  no keeper restart. `fbtodo keep off/on/default` is the same knob as a command — the pane you
  are in, or one named with `--pane %3`; `--window TARGET` for the middle rung, resolved
  through tmux so a session, `session:index` and a window id all work and the report names the
  id the write landed on (a pane's own choice still outranks its window's, which outranks the
  server's); `--server` for every pane at once; no verb prints what is in force — so nobody has
  to remember the tmux incantation.
- **`fbtodo status` names the keeper's server, and whether the asks agree with it.** The row
  reads `keeper server : /private/tmp/tmux-501/default  (the pane's and the watcher's asks
  name it)`: the name the keeper's record holds, canonicalised (`tmux_socket_of`) — a record
  written by an older build spells the socket as the raw `TMUX` value, and that is a
  spelling, not a different server, so it is noted as `recorded as …` rather than reported
  as a disagreement. The pane's ask is read from the status context when it IS a pane
  (`TMUX` set); the watcher's is the ask that spawns one, made outside tmux
  (`tmux_identity_outside`) — the shell autostart and the desktop integration. A
  disagreement is the churn in advance: the row names which ask names another server,
  because that ask is the one that would kill this keeper and start its own. The same
  answer is in `--json` as a `keeper` object, for a script that watches for the churn
  without parsing the sentence: `running`, `pid`, `server`, the record's own `recorded`
  spelling, both `asks` reduced to the sockets they name (`null` when that context cannot
  name one), `agree`, `would_replace` — the ask labels — and the row's `note`.
- **`fbtodo status --watch` reports the keeper's changes, not a snapshot.** A keeper's
  failure mode is history — a pid that was replaced, asks that would replace it — and a
  snapshot cannot show it. The watch prints the state it starts in once, and after that
  only moves: `pid` (the keeper replaced, or `gone` when it stops answering and `appeared`
  when one starts), `churn` (the asks reduced to the server they name, when they stop
  naming this keeper's), and `agree` when the churn clears. Between moves it is silent, so
  it can sit in a terminal or a script's pipe for as long as the session does; `--json` is
  one event object per line, each carrying the same facts the snapshot's `keeper` object
  does (`pid`, `previous_pid`, `server`, `agree`, `would_replace`, `note`, `at_ms`). `-i`
  sets the poll; Ctrl-C ends it.
- **No look-only command probes a claim — audited, documented, and pinned.** `daemon_pid()`
  is `lock_holder`: the probe whose free answer removes the record it finds. Every remaining
  call site is a path that ACTS — the start guard (`spawn_daemon`'s wait, the daemon loop,
  `ensure_pane_keeper`), the stop (`cmd_stop`, `nas --stop`), the pane's own poll, a keeper
  pass (`ensure_local_panes`), `claim_or_force` (`--force` ends the holder) and
  `live_watcher_pid` (which replaces a watcher left on another build, killing it) — and
  each now says in place why it must probe: a start must not be handed the pid of a watcher
  the kernel is not locking, and a stop has to clear a dead record rather than signal
  nothing. The commands that only look (`status`, `doctor`, `nas --status`, the `locks`
  audit) read the same claims through `lock_peek`/`claim_audit`. The self-check runs
  `pin --list` with the rest of the look-only family over a seeded dead record, proves none
  of them touches it, and then asserts `daemon_pid` still clears it.
- **`fbtodo locks --fix` resolves what the audit found.** The audit says what clearing each
  claim would take; this is the same read with the hand that acts. It clears the free
  leftovers it reports — through the ask's own probe (`lock_holder`), which re-opens the
  file, takes it and re-checks the name under the lock, so a claim born between the audit
  and the fix is never deleted — and ends an UNTIED role process (SIGTERM, bounded wait,
  a survivor reported rather than escalated), whose lock lives on an inode no name points
  at, which is the only repair there is. Nothing else is touched, and no process is ever
  started: the claims it frees are what the next ask re-claims. A fix that would end
  anything asks first — on the terminal, or with `--yes` — and refuses with 66 and no
  changes at all when it cannot ask (`--no-input`, no terminal); `--dry-run` prints the
  plan and touches nothing. A process whose environment could not be read is only named,
  never ended (`root_named`): the audit's deliberate false-positive rule is "a sentence,
  not a kill", and the fix is the other side of it. `--json` carries the plan, the result
  and the audit rows.
- **`fbtodo locks` audits every claim file.** One row per role — watcher, keeper, NAS pane,
  plus the legacy root's own claims while they are still there — with the four facts the
  claim machinery is built on: the HOLDER (the record's pid, and whether it is alive), the
  NAME↔INODE tie (a held claim blocks this process's own open of the name, so the tie
  holds; a record naming a LIVE pid while the file is free is the tie broken — a leftover,
  a claim being born, or a claim orphaned by a replaced file — and this process's own
  registry is checked exactly), whether the record is STALE (free with a pid inside: the
  leftover `lock_holder` removes), and WHAT CLEARING TAKES (a held claim: end the holder —
  the kernel drops the lock with it and unlinking does not; a free leftover: the next ask;
  an empty free file: nothing, it may be about to be locked). `--json` is the same rows for
  a script, and a run never writes: the probe would clean up, the audit says so instead.
- **`fbtodo locks` cross-checks the claim files against the processes that are actually
  running.** A claim file can only name a holder that still ties to its name, so a watcher
  or keeper whose file was replaced under it — the leftover cleanup took the name, and it
  went on running with a lock no reader can find — is invisible to every read of the file:
  the audit said `absent` while the process kept working. The second half is the process
  table: `claim_processes` finds every watcher, keeper and NAS watcher running with THIS
  state root, recognized by its launcher token and the subcommand right after it (never a
  word that merely appears in a line — `/usr/sbin/distnoted daemon` is not a watcher) and
  filtered by the root its environment names; `claim_orphans` then matches them against
  what the claim's own audit says — a held claim names its holder, a free or absent file
  ties nobody, and every other running process of that role is untied. Those rows print
  under the claim that cannot see them (`running: pid 35522 (keeper) with this state root,
  holding a claim no name points at`), and `--json` carries them as an `orphans` list per
  claim. A process younger than 2 s is left out: a watcher between its start and its claim
  looks untied for a moment, and a diagnostic that cried wolf at every start would be
  noise. Still a look: no signal is sent, and no byte of any claim file changes.
- **`fbtodo locks --watch` watches the audit and rings the phone.** The two things the
  audit finds are both things that HAPPEN — a role process whose claim name was replaced
  under it, and a claim file whose name and inode have parted — and both are invisible in
  every store, so nothing else will ever mention them: a watch is the only way an operator
  hears without typing `locks` at the right moment. It reads the same rows once per `-i`
  (5 s by default), prints each finding once as it appears and one line when it clears, and
  is silent in between however long it runs. The findings are keyed by the THING that is
  wrong (`orphan:<role>:<pid>`, `tie:<role>:<pid>`) so a standing finding is not re-announced
  and a claim being born — which is not a finding — draws nothing; the watch never reports
  itself. When a new finding appears it asks the fifth notifier (`locks-bell.py` in the kit,
  `LOCKS_NOTIFY`, absent on a machine without it), which reads `fbtodo locks --json` and
  keeps its OWN record of what it has pushed, so a restart cannot double-send and hours of
  watching push once per occurrence. `--json` streams the same events a line at a time, a
  closed reader (`| head`) ends it quietly, and Ctrl-C is a normal end.
- **The keeper reopens a pane that drifted onto another interpreter.** A pane whose Python
  differs from its watcher's by real path — two spellings of one interpreter are not a drift —
  is respawned **in place** with the pinned command (`tmux respawn-pane -k`: same pane id,
  same slot, the restart a hand would have done), and the pane log names both interpreters.
  The decision is read from the processes (`pane_python` against the watcher's own line),
  never asked of the pane, and it refuses to guess: no watcher to compare with, or a pane
  still starting, is left alone. A pane whose command is already this build's pin is left
  alone too — respawning it would re-run what it is running, so the odd one out is the
  keeper's own interpreter, which the log says once instead of respawning on every pass; an
  attempt is remembered (`DRIFT_RETRY_S`) so a repair that could not be delivered is retried,
  not hammered. Reading the pin back also got honest: tmux reports `pane_start_command`
  quoted the way a shell would quote it, so `pane_start_command()` decodes what `pane_rows`
  hands out, and comparing a reported command with the pin it was started from now works.
  A reopened pane also says so **on its own title chip** for a few seconds (`REOPENED (was on
  /usr/bin/python3)`, or `(a child was on …)` when the tree was the drift) — the log was the
  only place the repair spoke, which is the wrong way round when the person watching is
  looking at the pane as its process is replaced under them. The reason cannot ride the
  environment the way a reload's does (`respawn-pane` starts a fresh command in the server's
  environment), so the keeper leaves it in a file before the respawn, the pane claims it once
  on its first breath by its own id (`TMUX_PANE`), and a note never claimed is dropped when
  stale (`PANE_NOTE_S`) rather than shown to whatever process takes that pane id next.
- **The drift repair reads the pane's whole tree — and a bell runs behind the pin.** A pane is
  not one process: under it run the watcher it started, a notify bell through either, a tmux
  child, and one of those on another Python is the same disagreement one level down.
  `tree_pythons` walks the subtree from the pane's own line — the answer `pane_python` gives,
  first — and `tree_drift` names the first member that differs by real path. The keeper treats
  it as the same drift and reopens the pane on the pin, so the tree it starts next starts from
  that pin, and the log names the child (`a child, pid N, was on …`); where the pane's command
  is already the pin, re-running it cannot change what the child resolved, so it is said once
  (`held %id: a child (pid N) is on …`) instead of respawning on every pass. Bells are the half
  of the tree no repair can reach — a bell is gone by the time the keeper reads the process
  table — so the fix is at the launch: all five notify launchers run the script through its own
  shebang behind an explicit `PATH=<the pin>` (`notify_argv`), so an `env python3` bell resolves
  in the pin of the process that rang it, whatever PATH that process inherited.
- **The keeper keeps itself current too.** The pane and the watcher both replace themselves when
  the build under them changes; the keeper is the third long-running process, and it was the one
  that could be left behind — a keeper on an older build would keep reopening panes with its
  OLDER pin, so every repair it made would be made by code it no longer is. It asks the same
  question (`source_newer_than` against `STARTED_AT`, every `BUILD_CHECK_S`) and holds while
  the sources do not parse (`keeper holding, source does not parse: …` in `fbtodo-pane.log`),
  then re-execs through `self_argv`: same process, same pid, same tmux server. Its claim crosses
  the exec the way the watcher's does (`lock_handoff`/`lock_adopt` on
  `fbtodo-pane-keeper.pid`): the keeper is the process that must not stand down in between, or
  every pane would go unwatched while its record still named it. The reload is recorded where
  the rest of the keeper's work is (`keeper reloading: …`).
- **`fbtodo doctor` asks the interpreter question on purpose.** One interpreter for the pane,
  its watcher and the keeper is the whole point of the pin, and until now nothing put the
  question to the machine: a disagreement has no symptom until someone reads `status`. The new
  `one python` check reads all three from the processes themselves (`pane_python`, `python_of`)
  and compares them by real path — the same rule `pane_drifted` uses — and a disagreement
  prints each role with its WHOLE path, because the two heads are the diagnosis and a
  shortened path would hide exactly the part that differs. Fewer than two roles running is
  `ok` said out loud (`nothing to compare yet`), and a disagreement is a `warn`, not a `FAIL`:
  the keeper reopens a drifted pane on its pin, so nothing here stops a pane. The keeper's pid
  is read from the record it writes and whether that pid is alive (the pane bell's read,
  `keeper_alive`) rather than through `lock_holder`, whose free-claim answer REMOVES the record
  instead of believing it — and that record is what the state root's "a live keeper owns this"
  answer is read from (`_claim_live`), so a diagnostic that emptied it would erase the fact it
  had just reported on (found by the self-check's state-root case).

### Changed
- **A pane's interpreter and PATH are pinned, not inherited.** tmux rebuilds a pane's
  environment from its own server's, so a pane command that resolved `fbtodo` and `python3`
  through `PATH` could come up on a different interpreter than the process that opened it —
  measured 2026-10-01, a pane on `/usr/bin/python3` 3.9.6 beside a watcher on Homebrew's 3.14.
  The same `PATH` decides the interpreter for `scripts/notify/*.py`, so it was never only a
  version question. Every pane command now names the interpreter absolutely and carries the
  opener's `PATH` explicitly (`/usr/bin/env PATH=… python …`), with that interpreter's own
  directory first so the scripts a pane starts by name (`env python3` shebangs under
  `scripts/notify/`) are the same Python the pane is on. Both sides do it: fbtodo's own pane
  commands (`pane_command`) and the `fb` launcher, which resolves the interpreter and the
  launcher in the owner's own shell — the last place that `PATH` is still known. The
  assignment rides in the command string, so a `tmux respawn-pane` of it brings the pin back.
  `FBTODO_PATH` overrides the base value for a machine that needs a specific one.
- A **running desktop turn says so**: the app commits a message row only when a turn closes,
  so a thread that is working has no list to read yet — and `no write_todos call yet in this
  session` read as "the agent forgot" when the store had simply not committed. `-s desktop`
  now reads the store's own live signal (`threads.turn_state` / `turn_alive_at`) and says
  `turn running · no list yet` instead; a stale heartbeat means a turn that died, not one in
  flight, so it is not called running.

### Fixed
- **A self-reload can no longer exec into a build that parses but will not load.** The pane,
  the watcher and the keeper all start themselves over by EXEC (`os.execv`), and the only
  guard was a parse (`source_syntax_error`). A tree can compile file by file and still be
  internally inconsistent — a name imported from a module that no longer defines it, a raise
  at module scope — and then the exec replaced a running image with one that died on its own
  first line: the watcher or keeper simply disappeared, and for the pane the keeper reopened
  it into the same broken tree and the two looped. Now the build on disk is asked to LOAD
  first (`reload_probe_error`): the same command line the reload is about to exec, run once
  in a child with `FBTODO_RELOAD_PROBE=1`, in which `main` answers through `probe_answer` —
  before `init_state_root`, so nothing is claimed, created or moved. Exit 0 means the tree is
  safe to BECOME; anything else (a nonzero exit, a hang past 20 s) is a reason to HOLD, and
  the process keeps running the last build that provably loaded. The hold is a wait, not a
  surrender: once the tree is coherent again the same process reloads into it. The probe runs
  only after a parse has already passed and a source has actually changed, so it costs one
  child process per real reload, not per tick. And it is deeper than an import: a function
  body is name-resolved only when it RUNS, so a global that was renamed or deleted hides
  until the one command that reaches that line is called — which, after an exec, is a crash
  in a fresh image. `probe_answer` therefore exercises the entry point every invocation goes
  through (it builds the command parser, so `build_parser`'s body and its defaults run) and
  then resolves every global the package's functions LOAD (`undefined_global_names`, over
  each module's `symtable`), so a name used only inside a command that was never called
  still fails the pre-flight. Attribute names, locals and closure variables are not globals
  and are not asked about; a name bound at run time passes, because the lookup is the live
  module namespace. It is pinned both ways: this checkout must pass, and a copied tree whose
  uncalled function names a global that does not exist must fail with that name.
- **A process's environment is read from two sources, and a clipped copy is never believed.**
  The cross-check places each role process by the state root its environment names, and it
  read that environment through `ps -Eww` alone. Verified on this machine 2026-10-02: `ps`
  does not clip — a 250 KB environment printed in full — but the KERNEL does, and per
  process: every GUI process launched by LaunchServices came back with a copy of 1012–1132
  bytes while `ps` printed 516–974 of it (19 of them in one scan), and for a platform binary
  like `/usr/sbin/distnoted` it prints no environment at all. Two real faults came of that. A
  `ps -Eww` answer that is only a command line was read as an environment, and so placed a
  process on the DEFAULT root it never named. And a root variable in the part the kernel cut
  off read as "this process runs with no root" — the wrong direction for `locks --fix`, which
  would end an untied process of a root it could not read. The environment is now asked of
  `/proc/<pid>/environ` first, then `ps -Eww`, and — only when neither names a root — the
  kernel's own copy (`sysctl KERN_PROCARGS2`, read directly instead of through a fork, macOS
  only) joins the union together with the byte count only it can report. That count is the
  clip signal: inside `KERNEL_COPY_FLOOR` (512)…`KERNEL_COPY_CAP` (1200) the copy is the
  small kind, because `ps`'s share of one (1.16–2.09 of it) overlaps a complete copy's
  (1.10–1.15) too closely to be the measure. `claim_processes` sets `root_named` from what
  was actually PLACED, carries `env_clipped` in the row, and `locks` prints the assumption
  instead of claiming a root it never read — naming the cause (an environment that could not
  be read, or a copy that came back clipped) and saying that `locks --fix` will not end it.
  The self-check pins each shape on synthetic blobs (a command line, a full environment, the
  cap band and the floor below it, a missing second source) and reads two real processes: a
  child started
  with `FBTODO_HOME` in its environment is placed by it, and pid 1 — whose environment this
  platform will not hand over — reads as NO environment rather than as the default root.
- **Every other reader of a process's environment uses that same two-source read.** The
  audit was not the only place that learned a process's state root from its environment: the
  self-check's keeper-aim guard — the one that keeps a test keeper from going off keeping the
  OWNER's panes with the throwaway root — read each `pane-watch` process the same `ps -Eww`
  way, and so inherited both traps. A command-line-only answer read as "no `FBTODO_HOME`" and
  a copy the kernel clipped before the root both made a real leak look like a keeper aimed
  somewhere else, which is the one failure that guard cannot have. It now goes through the
  one two-source read (`_proc_environ`), carries `_read`/`_clipped` for the shapes it cannot
  place, and the guard flags an environment it could not account for instead of waving it
  through. Pinned with a stand-in `pane-watch` process and the command-line-only, clipped and
  single-source shapes forced through the read, plus the guard rule on injected aims.
- **The keeper re-claims its own name instead of running invisibly.** A claim is a lock AND a
  name, and the name half can go without the lock going: the claim file is removed or
  replaced under a running keeper (a probe's leftover sweep, an admin's `rm`, a test), and
  the kernel lock survives on the unlinked inode. The keeper then kept every pane with
  nothing on disk to name it — the look-only readers report the role as not running, the
  process half of `fbtodo locks` names it an orphan, and `locks --fix` ends it as dead
  weight: a working keeper killed for a file it was holding correctly. It now asks
  `lock_ours` about its own name every second (`KEEPER_CLAIM_CHECK_S`, next to pane passes
  paced by `--pane-seconds`, so the claim tick never turns every wake into a tmux survey),
  and a name that moved is taken back by the keeper itself (`keeper_reclaim`): free or
  absent is the same keeper claiming again, with `started_ms` carried into the rewritten
  record so an hours-long watch is not reported as newly begun, and a name a live process
  HOLDS is another keeper's, so this one stands down — its start's own rule. The short tick
  is what closes the window: the orphan rule has a two-second grace, so a replaced name is
  normally gone again before any audit could name it. The self-check drives both accidents
  on a private server (removed, then replaced by a foreign file) and pins that the keeper
  is the same pid with its server and start time intact, that `locks` afterwards offers no
  orphan and `--fix` no kill — while a keeper that CANNOT heal (one from a build older than
  this rule) is still named from the process table and still ended.
- **`fbtodo locks --fix --restart` leaves the machine watched again.** The fix repairs the
  claims — it ends untied role processes and clears dead leftovers — and then, without this,
  leaves them FREE: nothing re-claims them until the next session start or the next shell
  hook happens to run, which on a quiet machine can be hours of panes unguarded and a watcher
  that is not writing. The opt-in `--restart` runs the same ask a session start uses
  (`ensure_daemon`, which asks for the keeper before it consults the watcher's lock) right
  after the repair, so one command can leave the root watched. It is firmly opt-in and it
  never ends anything to make room (`--fix` already did any ending); `--dry-run` still only
  plans, and the flag belongs to `locks --fix`, not to `locks`. A second run is a no-op — the
  ask returns the holder rather than spawning another — which the self-check pins: a dry run
  plans a restart and starts nothing, the real run re-claims the watcher (before `none`,
  after a live pid held on this root), and a second run keeps that same pid.
- **The watcher re-claims its own name too — and stops for a root that was removed.** The
  same hole the keeper had (above), one role over: a watcher whose claim file is removed or
  replaced under it keeps the kernel lock on the unlinked inode, so every reader calls the
  role dead — `lock_peek` says not running, the process half of `locks` names it an orphan,
  `locks --fix` ends it — while it goes on writing state nobody ties to a watcher. Instead
  of the old `lock-removed` stop, the loop now takes the NAME back the way the keeper does
  (`write_lock` drops the registry entry that no longer points at the name): a free or
  replaced name is claimed again by the same pid, and a name a live process HOLDS belongs to
  that process, so the watcher stands down — its start's own rule. The one case it still
  stops for is a state ROOT that was removed, which is judged by the directory's IDENTITY
  (device and inode), not its existence: a claim file that merely moved leaves the
  directory's inode alone, while a root removed and re-created — even by this watcher's own
  state write, whose `atomic_write` makes the directory again — is a different inode, so a
  flaky race with the next write cannot make it resurrect a home somebody removed. The
  self-check drives both accidents on a private root (removed, then replaced by a fresh
  inode), pins that the pid is unchanged and the name held again, drives a helper that HOLDS
  the name to pin the stand-down, and removes a root of its own to pin the stop.
- **One keeper, one identity: the pane and the desktop integration share it.** A pane spells
  its tmux as the `TMUX` value — socket, server pid, session id — while an ask from outside
  tmux (the shell autostart that spawns the watcher, the desktop integration) has no `TMUX`
  at all and could only say `-default`;
  both name the same server, and compared raw they read as two, so each ask killed the
  other's keeper (`replacing keeper …` — measured 2026-10-02, several times an hour, leaving
  the panes unwatched until the next ask). The name is now the server's own answer
  (`display-message -p '#{socket_path}'`), which is the same string inside a session and
  outside one; `FBTODO_TMUX` stays a verbatim override. Names are compared as SERVERS, not
  strings (`same_tmux_server`): the raw `TMUX` value, `-default`, a forced `-L`/`-S` and the
  canonical socket all reduce to the socket they denote, so a record written by an older
  build is a spelling of the same server rather than a stranger. And an ask that cannot name
  a server — no server to ask, no `TMUX` to read — no longer evicts a keeper it cannot
  judge: only a keeper for a genuinely different server, or from an older build, is replaced
  (`keeper_serves`).
- **Two asks arriving together converge on one keeper, and the ask that loses adopts the
  winner.** The pane's own `ensure_daemon` and the shell autostart can ask in the same breath,
  and each started a keeper before either had claimed. `write_lock` creates the claim file a
  breath before it can lock it, and an ask's poll (`lock_holder`) removed a free file it found
  there as a stale record: racing the winner's newborn claim, it unlinked the claim file, so
  the winner went on keeping every pane on an unlinked inode — nothing on disk to name it —
  and the ask logged `keeper … never claimed the lock` while a keeper was running. An empty
  claim file is now left alone (only a free record that NAMES a pid is a leftover), and a
  probe asks without creating one where none exists — its own empty file was itself read
  as a claim by the next existence check. The loser's keeper stands down on the winner it
  sees (`keeper_serves`, `write_lock`), and the ask returns the holder — the winner's pid —
  waiting up to `KEEPER_CLAIM_WAIT_S` for it: a loss is an answer, not a failure.
- **No claim can be orphaned by the moment between opening and locking.** A claim's file
  is opened and locked a breath apart, and in that gap a probe could take the free file and
  remove it as a leftover — or the name could be replaced — leaving the claimer holding a
  lock on an inode no reader can find: a keeper running invisibly, while a second one could
  claim a fresh file of the same name. Every answer about a claim is now tied to the NAME,
  not just the file: `lock_holder` believes a lock or a record only while the name still
  points at the file it opened (`_same_file`, device and inode), removes a record only under
  that same tie — never a name that has become somebody else's claim — and re-asks, bounded,
  when the name moves under it. `write_lock` re-checks the name against the file it just
  locked, and on every later write, dropping a lock the name has left behind and claiming
  the name as it is now (`_lock_named`); `lock_adopt` checks once more after confirming the
  inherited lock, so a name replaced while the exec ran is let go rather than adopted
  unseen. The self-check races the window with threads: a claim paused between open and
  lock loses its name to a probe's leftover cleanup and comes back visible under a fresh
  one, and a probe that opened an old file never removes the name that has become somebody
  else's claim.
- **`fbtodo doctor` is a look, never a cleanup: a run leaves every claim record
  byte-identical.** Its watcher row asked `lock_holder`, whose free-claim answer removes the
  record it finds — so a doctor run over a dead watcher's leftovers deleted
  `fbtodo-daemon.pid`, the record `_claim_live` decides the state root from and `one python`
  reads the watcher's pid from (the keeper's own read was already non-destructive for the
  same reason). The watcher row now reads through `lock_peek`, the read-only twin of the
  probe: the same two steps — open, try the lock — with no unlink and no write anywhere,
  answering whether a claim is held and the pid the record names (0 when it names none). The
  self-check seeds records for the daemon, the keeper and the NAS pane — dead pids, the free
  case a cleanup would have removed — runs `doctor`, and asserts every one is byte-identical
  afterwards; then holds the daemon claim and does it again, so the held answer is pinned
  too.
- **The same look-only contract for `fbtodo status` and `fbtodo nas --status`.** `status`
  was the last reader left on the destructive probe: `daemon_pid()` is `lock_holder`, whose
  free-claim answer removes the record it then reports on, so a status run over a dead
  watcher's leftovers deleted `fbtodo-daemon.pid`; `nas --status` read its claim through
  `nas_pane_daemon_pid` — `lock_holder` again, which for a watcher from another build even
  kills it and clears the claim. Both now read through `lock_peek`, and the output is
  unchanged: a held claim names its holder (`watcher : 35522`), a free one says `— not
  running`, and `--json` carries the same pid — only the leftover record now survives the
  look. The self-check seeds the daemon, keeper and NAS records with a dead pid and runs
  every look-only command — `status`, `why`, `ledger`, `locks`, `doctor`, `nas --status`,
  `bar`, and the tracking `snap`/`json` (which keep their intended task-log write, so they
  are held to the claim files only) — asserting every record is byte-identical and still
  present afterwards, and that nothing else under the state root moved; then it holds the
  daemon claim and checks the row and `--json` name the holder.
- The `fb` launcher's POSIX body now runs unmodified under **zsh**, **ksh93** and **dash**,
  not just bash: it no longer passes split flags through an unquoted `$_fb_split` (zsh does
  not word-split an unquoted expansion, so `-h -b` arrived as one argument and only the
  default split worked), and no longer declares `local` (which ksh93 lacks, silently
  emptying `_fb_refresh`'s variables there). Each split arm now calls `tmux` directly.

## 4.30.2

### Fixed
- README.md's image and doc links render on the PyPI project page: they pointed at repo-relative paths (`docs/demo/*.webp`, `docs/*.md`, `LICENSE`) that 404 on pypi.org/p/fbtodo. They are now absolute GitHub URLs (`raw.githubusercontent.com` for images, `blob/main/` for docs), which also work on GitHub's own rendering.

## 4.30.1

## 4.30.0

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
- The notification suite stamps its done title **after** stopping the running timer, the order
  the wrapper itself uses: a live `run` loop overwrote the stamp on its next tick, which made
  "the last title is Done" a race (it failed on a runner, passed here). Its `title_matches`
  helper is defined where it is first waited on rather than an hundred lines later, so that
  wait no longer runs out its cap waiting for a function that did not exist yet.
- `phone.sh --init` bounds its Apple ID lookup, and does not wait on it. `defaults` talks to
  cfprefsd and can block for minutes where there is no preferences session at all (a CI
  runner), which hung the whole config write — the topic already minted — for one line of
  convenience; the read is now capped at five seconds per domain, its fds are the temp file
  and `/dev/null` so a survivor holds nothing open, and neither `kill` nor `wait` is given a
  chance to hang in its place. The topic is minted with `od -N16` rather than a `tr … | head`
  pair, so nothing in this path depends on a pipeline ending on a signal.
- The NAS pane-lifetime fixture waits for its **own** session to be reported live in the
  state the panes read, instead of a fixed six seconds or a glance at the pane's text:
  `alive` only says tmux made the pane, and a NAS state that is not alive still draws the
  last list it has, so its text says nothing about whether a pane has seen a session at all.
- A `-s nas` pane's watcher follows the session that pane named. `spawn_daemon` never
  forwarded `--fb-marker`, so the pane asked a daemon about `$HOME/.fb-session` and then read
  its answer about a different session: a pane for a live marker was shown another store's
  list, and one whose own marker had ended could stay up while the default marker's pid was
  still alive.
- The NAS pane-lifetime fixture no longer races the phases before it. It stops the watcher an
  earlier phase left behind (a leftover state answers `-s nas` whatever session it watched),
  drops that watcher's state file, and waits for the state to report its own marker live
  rather than reading the pane's text — a NAS state that is not alive still draws the last
  list, so the text cannot tell a live session from a finished one. When the stand-in session
  ends it is **reaped** and its marker **removed**: `kill -0` answers for a zombie, and a
  freed pid can be handed to another process on a busy runner, so a probe that trusts the
  pid alone says the session is alive again at random.
- The notification kit's suite is no longer tied to the maintainer's machine: it sourced the
  `freebuff` shell functions out of `$HOME/.zshrc` (a runner has none, so every wrapper check
  failed), and it required MuseScore's SoundFont for its chime checks. It now sources the
  shipped `funcs.zsh`, and skips the chime section with a stated reason when no bank is
  available. Its wrapper phase also stubs `afplay` like every other phase, so the chime the
  real wrapper rings at exit is silent and leaves the same log on every machine.
- The pin check's fixture clears the two `sleep 60` stand-ins the checks above split in. A
  window crowded with them is a size tmux can refuse to give a pinned pane — the boundary it
  resizes against is already at the other pane's minimum — so on a runner the list landed at
  six columns with the session squeezed to one. The failure message carries the panes, the
  keeper's log and the tmux version now.
- The NAS phase's session runs under a throwaway `ZDOTDIR` holding one `.zshrc`: an
  interactive zsh that finds no startup file at all runs `zsh-newuser-install` and blocks on
  its prompt, so on a runner (whose `HOME` has no `~/.zshrc`) the session never reached the
  command that starts the watcher.
- tmux is asked for a pane's fields with a `|` separator rather than a tab: a tab inside a
  `-F` format does not survive every tmux — on one Linux runner under a C locale it came
  back as `_` — and then every row failed to parse, which read as "no panes at all" and left
  the placement check and the ask watch looking at an empty pane list.
- The `local-session` phase opens its pane with the wrapper the repository ships
  (`examples/fb.sh`), sourced from a throwaway `ZDOTDIR` and invoked as `fb`, instead of the
  `freebuff()` function a developer's own `~/.zshrc` happens to define. A runner has no such
  function, so its session ran the bare stand-in and never opened a pane. Identifying that
  pane is case-insensitive too, because Linux names the interpreter `python3` where macOS
  names it `Python`.
- The NAS phase's remote shell puts the checkout on its own PATH, so `fbtodo` is found even
  where the launcher is not installed (a runner has it only in the working copy), and it runs
  with `-d` so Ubuntu's `/etc/zsh` — which prompts from `compinit` there — cannot block it.
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
  `examples/`, the packaging, the installer and the docs that describe them, and the suite resolves the
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
- **fbtodo is installable without a checkout.** Three new ways in, all of them the same
  command on the `PATH`: `install.sh` (served at
  `https://raw.githubusercontent.com/TLE47/fbtodo/main/install.sh`, for `curl … | sh`)
  takes the first of Homebrew, `uv`, `pipx`, a venv or a clone that the machine already
  has, never runs as root, and pins with `--version` (and the matching `FBTODO_VERSION`
  and friends); a Homebrew formula (`packaging/homebrew/Formula/fbtodo.rb`) makes
  `brew install TLE47/tap/fbtodo`, built from the PyPI sdist with no `resource` blocks
  because fbtodo has no dependencies; and a tag now **publishes the package** —
  `.github/workflows/publish.yml` checks the tag against `VERSION`, builds the sdist and
  wheel, runs the installed wheel's own `fbtodo doctor`, and uploads to PyPI through a
  trusted publisher, so `uvx fbtodo` and `pipx install fbtodo` have something to fetch.
  `scripts/release.py` does the mechanical half of a release (bump `VERSION`, roll the
  changelog, build, commit, tag) and
  `packaging/homebrew/update-formula.py` writes a release's `url` and `sha256` into the
  formula from the versioned PyPI index — the two values cannot be filled earlier,
  because a sdist built here is not the sdist it will serve. `scripts/preflight.py`
  reports whether a release can succeed *before* its tag exists — the tree, the branch,
  the tag's availability, whether that version is already spent on PyPI, whether the tap
  repository has a branch to push to, and which of those it cannot see without GitHub
  credentials — because a mistyped tag costs a version number rather than a retry, and
  the tap job's failure mode is to skip quietly.
- [`scripts/notify/funcs.zsh`](scripts/notify/funcs.zsh) is the notification kit's shell half —
  the chime picker and the `freebuff` wrapper that runs the title timer and the drop watch —
  shipped rather than left in the maintainer's `~/.zshrc`. The kit's suite sources this copy,
  so the wrapper it drives is the repository's, not whatever one machine happens to define.
- `make-sound.py --check` reports the SoundFonts it can see (and says so on stderr when there
  are none) without generating anything, so a caller can tell "no bank here" from "the
  generator failed" — which is what the suite uses to skip its chime checks on a machine with
  no MuseScore.
- `FREEBUFF_SOUNDS_DIR` points the chime picker (and `bell.sh`'s fallback) at a directory of
  system sounds instead of macOS's `/System/Library/Sounds`, so a machine that is not a Mac
  can ring a sound it actually has — which is how the kit's suite runs its chime checks off a
  Mac.
- `.github/ci-timeout.sh` runs a suite under a wall-clock cap and tees it to a log, and the CI
  steps use it (720 s) instead of piping straight to `tee`: a suite that blocks must not spend
  the job's whole 30-minute budget, and the last lines in its log are what names the hang.
  `timeout(1)` is not on a macOS runner's PATH, which is why this is a script and not a flag.
- `.github/ci-report.sh` annotates both ends of a failed suite's log: its first `FAIL` lines,
  because a suite that fails dozens of checks ends its tail in `PASS`es and a cascade's cause
  is at its start, and the last lines, because that is the only clue a capped (hung) run
  leaves behind. The job summary still carries the full 80-line tail.
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
