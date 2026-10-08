# Changelog

Versions are the `VERSION` constant in `fbtodo` and the git tag of the same name.
Entries start at the newest release; each one is a contract change, not a diff.

## Unreleased

### Fixed
- **The pane imports on the Python it promises again (3.9).** `base.py` named `ast.MatchAs`, `ast.MatchStar`
  and `ast.MatchMapping` directly, and those do not exist before 3.10 — so on 3.9 the module raised
  `AttributeError: module 'ast' has no attribute 'MatchAs'` the moment it LOADED, not on some slow path
  (measured by running the suite under 3.9, where it aborted with "the new build does not load"). The
  `match`-statement nodes are now asked for by name with an empty fallback, which is a no-op on 3.9 (`isinstance`
  against `()` is False, and a 3.9 parser cannot produce such a node) and the same binding analysis on 3.10+.
  `pyproject.toml`'s `requires-python = ">=3.9"` and the matrix's 3.9 leg are the contract this keeps.
- **A half-drawn frame no longer moves her.** A fresh reading of the pane that disagrees with the saved one by more
  than a border (`paneBorderPt`, 8pt) on any edge is HELD: the saved placement stays, and a change is accepted only
  when the next reading agrees. The live fit and the watch share that rule (`settle`). The log that showed it had
  the pane alternating between two rectangles, and her window with it.
- **She shows only while Freebuff is the app in front.** `onscreen` stays true while another app covers the
  window, so she floated over Discord with the Freebuff window behind it. She now hides whenever the host app is
  not frontmost (measured: Finder in front hides her, Freebuff in front shows her).

### Added
- **A watch can be READ by a program and STOPPED at its first hop: `fbtodo pip doctor --watch N --json [--stop-on-change]`.**
  `--json` prints ONE object per sample on stdout (`t`, `elapsed_s`, `state` — start, same, changed, held, no-pane,
  refused — the pane as `[left, top, right, bottom]` in window points, `anchor`, `size`, `inside`, `changed`,
  `note`, `window`), with the summary on stderr, so stdout is only the samples and can be charted or diffed
  across runs. `--stop-on-change` ends the watch at the first accepted change and FILES the evidence under
  `~/.cache/fbtodo/pip-hops/<stamp>/`: both captures (`before.png`, `after.png`), the full explanation of each
  (`explain-before.txt`, `explain-after.txt` — the words `--doctor` prints), and `readings.json` with the two
  readings, their times and the slack hint they were measured with. `FBTODO_PIP_HOPS` moves the filing. Exit 1 when it stops on a hop.
- **A mood's pose is held for a minimum time: `--pose-hold` (seconds, default 8).** Without it a mood that steps
  every 600–900ms changed pose faster than the eye could settle on one; 1.5 stopped the strobe but still moved her
  to a new pose every second and a half at the quickest, 4 halved that, and neither fixed it — the held number only
  ever bit the two quick moods, because a mood's own `ms` is what it says (work steps every 2.5s). At 8 every mood
  takes one pose per eight seconds whatever its own pace says. The finished burst keeps its own pace, and the knob
  is live: `fbtodo pip tune pose-hold 15` slows her further without a restart.
- **...and the pose floor no longer outlasts the pose: `--pose-min` (default 2, was 10).** The dissolve is
  `min(transition, max(pose's own step, pose-min))`, so a floor ABOVE the step made the fade longer than the
  picture it was arriving at: with the old ten she was never once settled on a frame for the whole cycle, which is
  what "she's currently change her pose too much" was looking at. The floor keeps its job — a fade is never
  instant — while the POSE is the ceiling again, and the suite pins that the floor stays under the hold, because
  two knobs that contradict each other are a bug in the defaults rather than a taste.
- **She talks on the CLOCK, and not only when her mood moves.** A mood lasts a whole turn, so a line per mood
  change was one line in twenty minutes — a caption rather than company. She now says a line whenever her bubble
  has come down and she has been quiet for `bubble-gap`, and the line is always the CURRENT mood's, so the clock
  cannot say something she is not feeling. Two knobs were already there and are REUSED rather than joined by a
  third: `bubble-gap` was the silence after a line and is now also the cadence she breaks it on (default 20 → 12,
  because 20 was tuned for a job it no longer has), and `bubble-seconds` is still how long a line stays up. Her log
  names the clock's lines (`says=hm hm hm mood=work balloon=right on=tick`) so "she talks more often" is checkable
  from outside, and she still never talks over a line that is up or over the finished burst.
- **...and she has two more FLAVOURS of every line: `--cringe` and `--sentence`.** `--cringe` (default 0.35 —
  "make her say cringe things often as well") is the chance a line is one of her dorky ones ("notice me, senpai",
  "*nuzzles the keyboard*", "i didn't break it, it was already like that"), and `--sentence` (default 0.15 —
  "once in a while she should say something a bit longer like a short sentence") is the chance it is a whole
  clause instead of a tag ("this list is from a turn that already ended, ugh"). Both are tables of their own,
  one row per mood, so the flavour still fits the emotion; `pickLine` is the ONE place a line is chosen, and
  both places that speak — a mood change and the clock — go through it, so "she is cringe sometimes" cannot
  come true on only one of them. Her log names the table (`kind=cringe`, `kind=sentence`, `kind=say`).
- **...and she does not repeat herself.** "Never the one she just said" was enough while a line only arrived
  on a mood change; with the clock talking three times a minute, a pool of four tags came back around inside
  the minute. Her memory of what she has said is now a LIST of the last six (`recentDepth`), every table she
  says lines from rolls through it — the mood's own, cringe, sentences, surprises — and both places that
  speak write back to it. A pool with nothing fresh left falls back rather than going silent: the pool minus
  only the last line, and the pool itself if even that is empty, because repeating a line beats saying
  nothing.
- **...and a line may not sound like an AI wrote it.** Every row is about the thing she is looking at — the
  step, the tick box, the leftovers, the cat — and the suite holds a denylist of the phrases that put an
  assistant in her mouth ("i'm here", "let me know", "take your time", "i'm proud", …), so a later
  "friendlier" sentence cannot quietly turn her back into one. `wait` lost "take your time" to it.
- **...and every mood she can be in has a POOL of lines, not one.** Eight for `work`, six for `done`, `error` and
  `idle`, five for `wait` and `none`, four for the two she only flashes through (`nudge`, `stale`): with the clock
  talking again a thin row is a line she repeats within the minute, which is the tape
  loop the table exists to avoid. The new lines are hers in the same voice, and ONE of them is a REUSE — "one more
  step" is `work`'s own line, shared with `nudge` rather than copied into it, because the two moods mean the same
  thing about it. The suite pins the pools, the no-duplicates rule and the single shared line.
- **The chat bubble stays up longer: `--bubble-seconds` (default 9, was 5).** Five seconds was gone before a line
  could be read. Both knobs are in `fbtodo pip tune`.
- **...and every knob her window reads is a FILE, set from the shell: `fbtodo pip tune`.** `transition`,
  `pose-min`, `bubble`, `bubble-gap`, `surprise`, `pop`, `fit-every`, `fit-retry`, `side` and `tick` live in
  `~/.cache/fbtodo/pip-tune` (`FBTODO_PIP_TUNE`), which her window re-reads on its own pin tick — so a knob
  set here is worn WITHOUT a restart, which was the whole ask ("so I never have to export an environment
  variable again"). The precedence is the usual one — a flag beats an environment variable, the environment
  beats the file, the file beats the built-in default — and `fbtodo pip tune` shows every knob with the
  value she would run with, WHERE that value came from (`cli`/`env`/`file`/`default`) and whether her
  running window wears a change to it. The list of knobs is asked of the PROGRAM (`fbtodo-pip --tune`)
  rather than kept in the CLI, so the two ends cannot drift; setting one writes the file atomically (0600,
  same directory, rename).
- **...and WHAT SHE SAYS is a file too: her lines are retunable without rebuilding her window.**
  `~/.cache/fbtodo/pip-lines` (`FBTODO_PIP_LINES`), in exactly the shape `fbtodo-pip --lines` prints
  (`work<TAB>chop chop`), re-read on the pin tick: a mood the file names is REPLACED, a mood it does not
  name keeps its built-in lines (a file naming one mood is an override, so a new joke cannot delete the
  other seven), every row for a mood is a line she may say in the file's order, a row with NO words clears
  that mood's lines — how "she says nothing in this one" is written down — and `surprise` is a key here as
  well, for the lines that belong to no mood. `fbtodo-pip --lines` prints the effective table, so
  `--lines > ~/.cache/fbtodo/pip-lines` seeds a complete file to edit, and her log names the reload
  (`buffy-pip lines=… moods=2`).
- **...and any placement can be INTERROGATED: `fbtodo pip doctor` takes one capture and explains it.**
  Where she stands is decided from a screenshot — the strongest vertical lines in the app's window, the
  pane's own published row grid, and the blank rows its frame left — and when that goes wrong the answers
  are all in pixels. The verb says all of them out loud: the window it measured, the hint the pane
  published, every long column with its ink run, every PAIR of them with the reason it was kept or
  thrown away ("nothing above the slack at all — a blank panel, not the pane", "the pane's slack band is
  NOT blank … kept, against a candidate that wears it", "wider than 60% of the window"), the pane that
  won, the blank band she was given, and `anchor=`/`inside=` — her square and whether it is inside that
  pane. Exit 0 when she would be inside it, 1 when the pixels say otherwise, 66 with no window to look
  at, 69 when a capture is refused; `--fit <png> --trace` is the same explanation over a file, which is
  how the suite checks it with no screen involved (and a capture is a second process's problem: while
  her window is up this verb measures with her down and puts her back, saying so on stderr).
- **...and a placement that comes and goes can be WATCHED: `fbtodo pip doctor --watch [SECONDS]`.** A
  hop is intermittent by nature — one capture catches an answer and never the jump — so the same
  measurement is taken again every second for that long (bare `--watch` is a minute) and only what
  CHANGES is reported: the pane rectangle, her square in it, or the window they were read out of, with
  the wall clock and both readings, then one summary counting samples, changes, refusals and captures
  with no pane in them (each failure printed once, when it starts, rather than once a second over the
  change it is about). The exit code is the gate the request implied: 0 when every sample found her
  inside the pane, 1 when any did not (or when a capture came back with no pane-shaped thing in it, the
  single-shot verb's own answer to that), 69 when no capture came back at all — so an intermittent hop
  makes the command fail; a duration outside 0..3600s is a usage error rather than a default.  One
  capture attempt a tick, deliberately: a watch IS the retry (the next sample is a second away), while
  the doctor's six attempts with 800ms between them would cost half a minute per sample. Measured live (2026-10-08): 14 samples in 10.8s at
  about a second a tick, and a change caught on the real window — `changed=anchor … was pane=1907,…
  anchor=982,494` one second after the pane's own hint moved. The duration is the verb's own word
  (`args.rest`), because `--watch` is `status`'s flag: `split_verb_words` now cuts after `pip doctor`
  too, exactly as it does for `tune` and `sheet`. `--fit <png> --watch 30` watches a capture on disk
  with no screen at all, which is how the suite checks the reporting — the file is re-read every tick,
  so a hint rewritten mid-watch is a placement that moved while the screen stood still.
- **...and `--shot` keeps a capture at last.** `--shot <png>` ("the same capture written to disk, so
  what the live path actually saw can be looked at") was left at the top level when `--doctor` was moved
  inside the app, and from there it was refused on EVERY run: measured 2026-10-08, exit 69 with "no
  screenshot for --shot" from a shell, while the same binary's own scheduled capture measured the pane
  perfectly. It is a `shotRun` now, scheduled after the run loop like the doctor, and both take their
  capture through one `captureWithRetries` (6 attempts, 800ms apart, refusals on stderr) so the two
  verbs cannot disagree about how a window is captured. Verified live: with her window down,
  `--shot /tmp/shot-final.png` wrote 2456x1380 for window 71608 by pid 99995 (rc=0) and
  `--fit /tmp/shot-final.png --fit-width 1228 --trace --slack …` over that file put her at
  `anchor=982,489 size=192 inside=yes`. It needs her window down — a second pip process cannot capture
  while she is up, the same reason `pip doctor` measures with her stopped and puts her back.
 `scripts/buffy-sheet.py` renders
  the whole cast into ONE self-contained HTML file — a mood with a line in it (the balloon, its shape and
  the words that fit it) and the mood alone (the pose the pane's word means) for all eight, then the
  surprise and the frames on disk — because a change to the art, the shapes or the table is a change you
  cannot review by reading the code. `--out`, `--size`, `--line`, `--no-frames`, `--no-surprise`,
  `--open`, `--json`; every picture is a data URI, so the page travels; the path is the one line on
  stdout, progress is on stderr, and it exits 66 with a build command when there is no window to draw.
- **...and the surprise changes her POSE, not just her words.** It borrows a sticker from OUTSIDE the
  mood's own cycle (the pool is every frame that mood would never show) for as long as the line lasts, and
  then dissolves back to the step she was on — her cycle HOLDS for the surprise, so the picture she
  returns to is the one she left. It arrives as a CUT rather than a dissolve, the one cut she makes on
  purpose: a surprise you can see coming is not one. `--art-surprise` renders the borrowed sticker on
  demand, the log names it both ways — which pose she borrowed (`surprise=yes pose=15`) and which of her
  own she came back to (`surprise=over back=pose=20`) — and the suite checks for every mood that the frame it
  borrows is not one that mood already shows ("make the surprise change her pose as well: a sticker from
  outside the mood's own cycle for a few seconds, then back").
- **...and her balloon hangs on the OTHER side of her head for the next line, and POPS into place.** The
  sides alternate per line (a still render is always the same side, so the sheet and the suite stay
  repeatable), which is what stops every line landing on her ahoge — and the log names the side it landed
  on (`balloon=left`/`right`), so the alternation is something a reader can check without watching her
  window; the tail mirrors with it, pointing at
  her head from whichever side she is on. And the line arrives with a squash-and-stretch pop
  (`FBTODO_PIP_POP`, `--pop`, 0.34 s, `0` off) instead of fading up: it lands flat and springs open, and it
  is anchored at the balloon's top edge and never scales wider than the settled cloud, so a pop can only
  grow inward and can never leave her square.
- **...and she ARRIVES rather than fades in, and a pose is never the fast one.** Two more asks, both about
  the same thing — the transition was still too quick to watch somewhere: her window is now ordered in at
  FULL OPACITY (the way off the screen is still a fade, and the balloon's arrival is the pop instead of a
  ramp), and the pose floor `FBTODO_PIP_POSE_MIN` defaults to **10 s** — the transition's own length — so
  a pose change is the slow morph whatever pace its mood steps at ("increase default transition per pose
  up, i dont want it to be too fast", "also remove fade in").
- **...and her bubble is a MANGA BALLOON whose shape is her mood: the cloud wraps the LINE, not her
  square.** The words are measured first (`bubbleLayout`) and the balloon is the ring of scallops that
  wraps that measurement, so a longer line is a wider cloud and one that has to wrap is a taller one
  that sits lower — measured, not claimed: `--art-say "hi"` reports `balloon=69x74`, `"waiting on you"`
  `balloon=169x74`, and `"the list needs a rewrite"` `balloon=183x99` twenty-three points higher up.
  Which scallops those are is the MOOD's (`bubbleShapes`, one row per mood): a soft pink cloud with two
  stars while she works, the roundest, pinkest, four-star one when the list is done, a lilac cloud with
  no stars and three DETACHED dots for a tail where she is only thinking, waiting or left over — a
  thought balloon — and a jagged SHOCK balloon for `error` (eleven spikes, red) and `nudge` (seven,
  orange). No puff is a circle of its own size: a fixed wobble of the radius, index by index, so the edge
  undulates instead of reading as a rounded rectangle with beads on it ("the bubble cloud text looks a
  bit too rectangle"), and the card underneath is inset exactly as far as the puffs can hide it, so the
  visible boundary is the arcs. Two invariants survive every shape: every circle is clipped to her own
  square (`puff` is the only way in), so a line can never reach the pane's list, and the balloon is
  never more than half her height. `--art-say` renders one and the same record line measures it
  (`balloon=169x74 r=16 box=29,135 spikes=0`), which is how the wrap is a number in the suite rather
  than a squint at a picture (the owner, 2026-10-07: "using a bubble cloud box like in manga wrap
  around text to show her emotion").
- **...and now and then she SURPRISES you.** `FBTODO_PIP_SURPRISE` (`--surprise`, 12% by default, `0`
  never and `1` every time) is the chance that a mood change gets the surprise instead of the line the
  mood would have said: words that belong to no mood ("boo!", "still cute?", "*pomf*"), in the one
  balloon that is all of them at once — spiked, spangled and dotted, in violet (`surpriseShape`, which is
  deliberately NOT a row of the mood table: a surprise is nobody's mood) — and her log names it,
  `surprise=yes`. A surprise is allowed past `FBTODO_PIP_BUBBLE_GAP` on a mood change, because a line
  nobody expected is the whole of it, and `--art-surprise` renders one on demand instead of waiting for
  luck ("you can have some mood sometimes appear to surprise users").
- **...and every one of her twenty frames is worn by some mood.** Frames 4, 5, 6, 8, 13, 14 and 15 were
  in no cycle at all — art that existed and was never on screen. The four moods that MOVE carry them
  (`work` 20, 17, 8, 4; `done` 12, 18, 9, 1, 15, 14; `idle` 3, 2, 16, 13; `none` 19, 10, 5, 6) and the
  four that HOLD keep their single picture each, and the suite now checks the UNION of the table against
  the art on disk rather than only that the frames it names exist — a frame no mood names fails the run
  ("trying to use all of buffy-chan images").
- **...and she TALKS, and CELEBRATES: a speech bubble in her own square, and a burst when the list is
  finished.** The bubble is drawn INSIDE her square — never over the pane's list, never outside the pane
  —  as a CLOUD with a pair of puffs for a tail — a union of circles drawn as one path, and drawn twice so
  the outline cannot show the arcs the union ate ("more round and a bit cloudy form") — sized to her own
  width and never more than half her height, so a line cannot reach anywhere she does not already stand
  (the owner, 2026-10-07: "add a chat bubble so sometimes she says something cute and cool, or even
  silly").  `--art-say "we did it"` renders one without a window, which is how the shape is looked at
  and how the suite measures it. One short line per
  mood, picked at random and never the one she just said — a companion program that repeats itself is a
  tape loop — put up on a MOOD change and at most once every `FBTODO_PIP_BUBBLE_GAP` (20 s), for 5 s,
  fading in and out over 0.35 s on its OWN clock rather than the transition's: two words are read at a
  glance. `--bubble 0` (`FBTODO_PIP_BUBBLE`) takes it away, and a mood with no line says nothing.
  The celebration is the exception the other way: a FINISHED list always gets a line AND a burst — she
  rattles through her own poses at 220ms each for three and a half seconds instead of the mood's
  two-and-a-half seconds a pose, so "every step is ticked" is something you can see rather than read
  ("add transition after she's done with all the job"). Verified both ways: her log says
  `says=we did it mood=done` when the list finishes, and the same square captured 1.3 s later and 6 s
  later reads `188.5` against `62.7` on the top strip of her art — the card is on the screen and then it
  is not.
- **...and her transitions are SLOWED DOWN: she eases, fades and dissolves instead of teleporting.**
  Every change of state she makes used to be drawn inside a single tick — a slot the pane's layout
  moved under her, a window that had to go away because the list filled the pane, a pose following
  the pane's mood — so she hopped to the new corner, popped in and out, and blinked between two
  stickers. There is now ONE number for the length of all three (`FBTODO_PIP_TRANSITION`,
  `--transition`, 10 s by default and a FLOOR under any smaller value, because a transition is meant to
  be SEEN — "i let the transition take at least 10s"): the move is eased (smoothstep, so the corner and
  the size travel together and both ends of it are at rest), the window fades in and out, and a change
  of pose is a cross-dissolve of the picture she is leaving over the one she is arriving at. The ramps
  are driven by her OWN 30 Hz clock rather than AppKit's, and that is a measurement rather than a
  preference: her window's animation behaviour is `.none` (she is furniture, and a window that animates
  its own ordering-in flashes in the middle of the chat), and against that a 700ms `animator().setFrame`
  landed as ONE step of a 60ms sampler — a cut wearing a transition's name. `0` is the old behaviour
  exactly — every one of them a hard cut, and the one value the floor does not touch — which is how the
  two are compared rather than argued about. THE SLIDE IS A SLIDE: her size is settled the moment a move
  begins and only the corner travels, because a window that grows on the way across is one the pane's
  own rows cannot account for (the owner, 2026-10-07: "don't resize the window for slide in
  transition"). A POSE takes the same length too ("make transition between pose take 10s"), capped at
  one step of the mood it is arriving at and never made fast by that cap ("you can have the cap, but
  sometimes i don't want it to be too fast transition"): `min(transition, max(pace, POSE_MIN))`, with
  `FBTODO_PIP_POSE_MIN` at 6 s, so a mood that steps every 900ms still settles while a held pose takes
  the whole of it.
  The visibility flip the slow fade made loud: a single refused measurement between two good ones used
  to be a one-frame blink and is a twenty-second pulse at this length, so the way OUT now waits one pin
  tick (`hideDebounceS`, 0.5 s) — a measurement that found nothing is mostly a pane mid-relayout —
  while the way back IN is still immediate.
  Three rules the ease had to keep: the move is compared against the slot she is GOING to rather than
  the frame she is sitting at (the pin's clock is half a second apart, and a re-issued move would
  restart the ease on every tick and never arrive), a slot that got SHORTER takes her SIZE at once and
  eases only her position (easing a shrink would hang her over the pane's own border for the length of
  the ease, which is the one thing the containment rule is about), and there is no resample of her
  frames for the sizes she merely passes through — the pin prepares her for the slot she is arriving
  at, because twenty Core Image renders per intermediate size is the one thing that could make the
  move itself stutter.
  Measured live on this display (2026-10-07), off the window server rather than off the source: her
  window's own alpha, sampled every 100ms as she was shown, reads `0.01 → 0.03 → 0.08 → 0.13 → 0.19 →
  0.26 → 0.34 → 0.42 → 0.50 → 0.58 → 0.66 → 0.73 → 0.80 → 0.86 → 0.92 → 0.96 → 0.99 → 1.00` — a
  ~9.4s fade with the ease visible in the spacing (and, at `--transition 5000` before the floor existed,
  seventy distinct values over the same ramp); a 1229→560 resize of the app's window while she was
  shown moved her `1032 → … → 592` through the whole of a ten-second glide with the alpha ramping
  underneath it (and at the old 700ms length, `1034 → 1025 → 985 → 920 → 844 → 794 → 711 → 678 → 675`,
  nine intermediate positions), `size=192` throughout; and a recording of her own square through one
  mood change is `1026 1026 1082` — a single frame — at `--transition 0`, against `1026 … 1060 1061
  1062 1063 1064 1066 1067 1069 1071 1073 1075 1076 1077 1078 1079 1080 1082` with the dissolve on, and
  at the ten-second length the same measurement is a ramp of some two hundred frames rather than a
  handful. She
  was also caught doing the thing the ordering is for: on the tick she came back from a failed
  measurement she was ALREADY at `1034,461` while her alpha was `0.02`.
- **...and she is a SUBCOMMAND, pinned where the drawing used to stand.** `fbtodo pip` owns the
  floating window: `pip start` (idempotent, launched in its own session so a short-lived shell
  cannot take her down with it), `pip stop`, `pip status`, and the two that are the button —
  `pip free` releases her so she is draggable and stays where she is put, `pip stuck` puts the pin
  back, and `pip forget` drops the pin she was taught so she stands in the middle again. The pin is
  anchored IN the window the PANE lives in — the place the drawn buffy-chan stood — and it is
  re-checked twice a second rather than read once: the host window is resized, moved between Spaces
  and dragged, and a pin that only held at launch would be over the wrong thing by teatime. Which window that is, is read off the PANE's OWN PROCESS CHAIN
  (`--pids`, the ancestry `fbtodo pip start` walks while it is still a child of the pane's shell —
  the detached window itself is reparented to `launchd` and can no longer see it), so a window
  whose OWNER pid is on that chain is her home and the app's NAME (`--host`, `FBTODO_PIP_HOST`) is
  only the fallback; a SET-BUT-EMPTY name (`FBTODO_PIP_HOST=`, or `-`/`none`/`off`) is how a caller
  asks for the middle of the display, which is also what she falls back to when no window is found,
  and her log says which of the two she got.
  The search is not restricted to the on-screen windows: measured 2026-10-07, the desktop app's own
  window reported `onscreen=false` while a pane was live in it, so an on-screen-only search answered
  `none` and pinned her to the screen — the on-screen pass is simply tried first, because it is the
  cheap one, and an on-screen window wins over an off-screen one so a Space nobody is looking at
  cannot take the pin.  The switch is a FILE (`~/.cache/fbtodo/pip-free`), the same shape the notify kit uses for the
  phone, so a shell command, the pane's own key and the running window can never disagree; the window prints a line on every state CHANGE, which is the only way anything outside
  it can tell that the button it just pressed was read. Verified end to end: `pip start` → a
  window at `layer=3` (the window server's own list) whose log line names the host it found
  (`hostBounds=51,30 1229x690 pid=3294 owner=Freebuff`), `pip free` → `buffy-pip mode=free` in her
  log within half a second, `pip stuck` → `mode=stuck` and her snapped back.
- **...and the draw-in-the-pane is GONE: the pane does not draw her at all any more.** Two
  drawings of one character, one of them standing in the rows the steps want, is worse than one,
  and the window is the better one — so the pane draws neither of them now. Both halves came out:
  `buffy_panel` used to fall back to the ASCII portrait when the terminal could not show her
  picture (`return []`), AND the picture itself is no longer drawn by default, because it was the
  second copy of a character who is now in a window of her own over this very frame. The switch
  that brings the drawing back is `FBTODO_BUFFY_ROWS`, and its grammar changed with the default:
  unset (or `0`/`off`) is a pane that is only the list and a picture in the middle of the frame is
  an explicit `-1`/`auto` (sized to the pane, as before) or a row count, which is also now the
  CEILING of the slack she may stand in rather than a hint about it — the pane used to draw her in
  leftover rows even with the knob at `0`, which is how a picture could still appear on a pane that
  had been told not to draw one. A value that is not a number is read as "not asked for her"
  rather than as "size her to the pane": guessing is what a pane does with a list it cannot read,
  not with a knob. `BUFFY_ART` and `buffy_art` stay in the module with their own checks as the
  thing `fbtodo pip` can print for a machine with no window to float her in, and the list gets
  every row the panel used to reserve — 6 to 15 of a 24-row pane, measured on a 46-column pane -
  back. The pane's own six-cell border face is NOT part of this: it is the state, not a drawing.
- **...and the pin is TAUGHT by dragging, not guessed from the window.** The middle of a window is
  only the pane's own middle when the pane IS the middle of its window, and it often is not:
  measured 2026-10-07, the desktop app's window keeps a chat and a file list on the left and the
  terminal panel on the right, so the window's centre put her over the chat — the owner's words,
  "it's in the middle of my screen, not in the fbtodo pane". Nothing outside the app can be told
  where that panel is (the app's own UI geometry lives in its renderer, behind a launch token it
  strips on purpose), so the pin is taught instead: `p` releases her, you drag her where she
  belongs, `p` again — and wherever she was left is remembered as an offset from the pane's
  window's TOP-LEFT corner, which is what makes it survive that window being moved, resized, or
  carried to another Space. The file is `~/.cache/fbtodo/pip-pin` (a plain `x,y`, `FBTODO_PIP_PIN`
  to move it), re-read twice a second so a rewrite moves her on the next tick, and `fbtodo pip
  forget` drops it. With no pin taught she stands in the middle, which is the best anyone outside
  the app can know — and either way her CENTRE is kept inside the window, so a pin taught in a
  bigger window cannot park her off it. Verified against the window server: pin `620,150` on a host
  at `51,30` → `at=(671,180)`; free with the file rewritten → she does not move; stuck again → the
  file is rewritten to her actual position (`taught pin=620,150` in her log); rewritten live to
  `300,60` → `at=(351,90)`; `forget` → back to the window's middle.
- **...and she finds the pane by HERSELF, from the pane's own drawing.** A taught pin only ever
  existed because nothing outside the app can be told where the terminal panel is; it can, however,
  be SEEN. Her window now captures its host window (ScreenCaptureKit — measured 2026-10-07: 112ms a
  shot plus 19ms to RGBA, against `screencapture`'s 52 SECONDS, which is why the CLI is not used)
  and finds the one thing in it shaped like a pane: the frame fbtodo draws is the window's only
  PAIR of solid vertical ink lines, so the detector takes the columns carrying the most ink, pairs
  them by how long they run in parallel, and keeps the pair whose rectangle is a column of the
  window rather than the window. She stands at the centre of that pane's LAST BLANK BLOCK — the
  space the ASCII picture used to be drawn in, above the state row — or at the pane's middle when
  the list fills it. Re-measured every four seconds and again the moment the window she was
  measured in is not the window in front of her, which is what makes a resize move her with the
  pane; a pin you TAUGHT still wins, and `fbtodo pip forget` hands her back to the drawing.
- **...and the pane publishes the one thing pixels cannot tell: which of its rows are empty.** The
  pane's background is transparent onto a textured wallpaper (`.terminal-panel{--terminal-canvas:
  #00000000}`), so on a real screenshot a blank row reads exactly like the wallpaper's own pattern
  — forty rows of "ink" where the pane is empty. The pane therefore writes `rows cols start count
  epoch` (its grid, the first row of its slack, how many rows that is) to
  `~/.cache/fbtodo/pip-slack` (`FBTODO_PIP_SLACK`), rewritten only when the numbers change, and her
  window turns that into a place with the cell height it reads off the frame it found. The COLUMN
  count is the second half of the note: her window has to pick the pane out of a screenshot, and the
  shapes in that window are not all panes — measured 2026-10-07, the explorer sidebar was a
  better-looking pair of vertical lines than the pane was — so a candidate rectangle whose width is
  nothing like `cols` cell-widths for the height it claims is passed over. Hint first, pixels
  second, refused rather than guessed: a hint that does not fit the frame's row count is ignored, and
  a window in which no pane is found leaves her hidden rather than parked somewhere plausible.
- **...and the pane makes ROOM for her window rather than letting her sit on a step.** The rows the
  note publishes above are the pane's own reservation, not just whatever the list left over: while
  her window is RUNNING (the pid file, so a stale one reserves nothing) `fbtodo pane` reserves
  `pip_slot_rows()` rows for it — `FBTODO_PIP_ROWS`, whose default is "as many as her drawn panel
  would have taken" (`buffy_panel_rows`), `0`/off is a pane that reserves none, and a number is
  that many — through the same guards the drawn panel always had, so the steps keep
  `BUFFY_LIST_FLOOR` rows and a pane too short for both loses her before it loses the list. A pane
  with her over it therefore shows a shorter list and an empty slot, instead of a character covering
  three steps. Her window is FIT to that slot and never outside it: the size is the largest whole
  number of ART PIXELS that fits (the frames are 128px and the display is 2x, so 64pt units — 192pt
  is exactly three screen pixels per art pixel, where the 201pt she was drawn at before resampled
  her at 3.125), drawn with the context's high-quality filter; the square is centred in the slot and
  then CLAMPED inside the pane's borders; a pane that published no room at all orders her out
  instead of drawing her over the list; and with no measurement for the host window — at launch, or
  after a refusal — she is not shown at all rather than put at the middle of the app's window, which
  is the chat, not the pane. She is also not shown while the host window is not on screen: her
  window joins every Space, so a fit measured for a window the reader is not looking at would float
  her on the desktop by herself. Verified live, window server against `fbtodo pip status`: pane
  `1831,125-2407,1369` px (915.5..1203.5 x 62.5..684.5 pt), published slot `rows 22..34 of 37`,
  `size=192 anchor=963,445 inside=yes` → her window at `1014,476 192x192`, and the host window's own
  capture blank from `783..1297` where she stands. The pixels were being read UPSIDE DOWN until this
  and she stood 52pt above her slot: a `CGContext`'s buffer counts rows from the BOTTOM, so the
  frame's own border row came out at the top of the array in one reading and the bottom in another —
  the pane's top edge read as 5pt below the window's when a `screencapture -R` region and the
  project's own PNG reader both put it at 62.5pt. `captureWindow` and `screenshotOnDisk` now flip
  once, where the pixels are made, and `--shot <png>` (the same capture written to disk, for looking
  at what the live path actually saw) agrees with the fit and with a screenshot of the same window.
- **...and the pin has a button on the pane, the way the phone does.** `p` releases her (and pins
  her again), and the pane's TITLE CHIP carries the switch while it is in force — `pip stuck · p`
  or `pip free · p`, the same slot and the same rule as the mute chip: the chip is the one place
  the pane says a switch exists at all, and it names its own key, because a switch nobody can find
  is a switch nobody trusts. The chip appears only while her window is RUNNING (a pid file naming a
  live process is the test): with no window there is no pin, and a chip offering to release one
  would be the pane describing something that is not on the screen. `p` is asked only when the mute
  kit has nothing to say about the key, so one byte can never mean two things, and the chip is part
  of the pane's repaint signature — a keypress changes no row of the list, so without that the
  frame would sit there showing the state she just left.
- **...and she can float over the whole screen, the way a video sits in a picture-in-picture
  corner.** Everything above is a CEILING and not a limitation of effort: the pane can only ever
  draw her in cells, and a cell is worth two pixels, so on a short pane she is a small blob by
  geometry — no rendering rule changes that. `scripts/buffy-pip.swift` is the other half: the SAME
  frames the pane averages down, at their real resolution, in a borderless window of her own that
  is always on top (`level = .floating`, which the window server reports as layer 3 against the
  layer-0 windows everything else is drawn in). She cycles the frames on the pane's own tick, she
  is draggable from anywhere on her body (`isMovableByWindowBackground`), she leaves on Escape or a
  right-click, and she is an ACCESSORY app: no Dock tile, and she never takes focus from what is
  being typed. Built by `scripts/buffy-pip.sh` (`swiftc`, which is already on this machine), which
  compiles to `~/.cache/fbtodo/bin/fbtodo-pip` rather than into the checkout, and rebuilds only
  when the Swift is newer than the binary. Two things learned building it, both recorded because
  they cost time: a launch from a shell that then exits takes her with it unless she is started in
  her OWN session (the script is launched with `start_new_session`), and the only trustworthy
  check that a floating window exists is the window server's own list — a screenshot crop was
  believed once and was the YouTube video behind the crop.
- **...and she is drawn at the resolution she exists at, not resampled into it.** The frames her
  window draws were 128px and she is 192pt on a 2x display — 384 device pixels, a 3x upscale of
  every pixel of the owner's drawing, filtered by whatever the window server does to a bitmap — and
  the owner's words for the result were "still a bit low resolution". Both halves are fixed. The
  900px Discord stickers the owner made the frames from were on disk all along, so
  `scripts/buffy-thumbs.py --size 384` derives a second set from them (`assets/buffy/pip`, next to
  the 128px set the PANE reads; the pre-shrink it feeds `sips` now follows the size asked for,
  because a 192px intermediate behind a 384px frame is an upscale of a downscale), and 384 is
  exactly her size: **the render is byte-identical to the art** (`rmse 0.00` against
  `assets/buffy/pip/20.png` at 192pt, where the 128px frames come out `rmse 7.16` of the same
  picture — a number, from `scripts/buffy-artcheck.py`, rather than an opinion). Where a resample
  does happen — a slot smaller than 192pt, the fallback frames, or the 128px set on a checkout that
  has not regenerated the large one — it is done ONCE, off the screen: `renderedArt` pre-renders
  every frame at the exact device pixel size with Core Image's Lanczos scale plus a light unsharp
  mask (radius 1.4, intensity 0.5, and only when pixels were actually put back), the view draws the
  result 1:1 with interpolation off, and `--sharp=0` / `FBTODO_PIP_SHARP=0` restores the old drawing
  path for an A/B. Measured, 192pt against the same frame's `.high` drawing path: edge energy 137%,
  and the round trip back down to the 128px source is 37% closer (a sharper resampler and a truer
  one at the same time). `--art <png> [--art-size PT] [--art-mood NAME]` renders one frame through
  that same path without a window at all, which is how the two are compared.
- **...and she wears a POSE for what the agent is doing, so her face and the pane's agree.** The
  pane already has one word for her mood — `buffy_mood`, the same word that picks the face on its
  top border (a failure, the rewrite-the-list nudge, a finished list, no list at all, idle past the
  pane's own threshold, a heading left over from an earlier turn, or the ordinary working frame) —
  and her window draws a picture rather than text, so it can only be TOLD. The pane now publishes
  that word to `~/.cache/fbtodo/pip-mood` (`FBTODO_PIP_MOOD`), rewritten only when it changes and
  again if the file has gone missing, and her window wears the pose it means, on her own clock:
  sitting at the laptop and then thinking it over while a step is in progress (`work`, 20 and 17),
  both arms up twice over when every step is ticked (`done`), dozing with the cat and then just
  standing there when the session has gone quiet (`idle`), puzzled and then peering out from behind
  the frame's edge when there is no list to point at (`none`), and HOLDING still for the three that
  are over by the time they are drawn (`error`, `nudge`, `stale` — the pane holds still on those
  faces too). Which pose means what is one table in the Swift (`moodCycles`, with the frame numbers
  `assets/buffy` gives them), every mood the pane can produce has to have one, and every frame it
  names has to exist — both checked, so the two ends of the feature cannot drift apart. The whole
  table, with the condition behind each mood, what she wears for it and how to see one without a
  window, is [docs/MOODS.md](docs/MOODS.md); `fbtodo-pip --moods` prints it, `--art-mood NAME`
  draws one pose, and the suite asks her window for both (a stale or missing binary is said out
  loud rather than assumed). Live:
  `echo done > ~/.cache/fbtodo/pip-mood` moves her to the celebration within half a second and says
  `buffy-pip mood=done frames=12,18,9,1 step=900ms` in her log.
- **...and the window server's own `onscreen` flag is not to be believed about this app.** It is a
  per-Space flag, and measured 2026-10-07 the desktop app's window reported `onscreen=false` while
  its pane was live, capturable and in front of the reader — so the gate that hides her off-Space
  hid her over a pane she belongs in, which is half of "she should be always in there". It is a HINT
  now: the ACTIVE application settles it (`NSWorkspace.frontmostApplication`, refreshed on every pin
  tick), so a window belonging to the app in front counts as one the reader can see whatever its
  Space says, and only a window that is neither on screen nor the app in front hides her. Verified
  live with the app in front and the flag false: `buffy-pip shown=… size=192` and her window
  `onscreen=true 1009,461 192x192` over the pane.
- **...and she FOLLOWS A RESIZE, measured.** The app window was resized under her twice
  (1229x690 → 560 → 520 → back, through accessibility, restored before the tool exits) with her log
  watched to the millisecond: at 560 the capture came back `2458x1120`, the pane was re-read as
  `1805,108-2413,1119 cell=28px slack=rows 20..32 of 35`, and she moved to `anchor=958,341` — then
  re-measured again when the pane settled (`cell=32px rows 17..26 of 29`, `anchor=958,328`) — with
  her window at `1009,371` and then `1009,359`, both `size=192`. At 520 the whole thing again, and
  the contract check on the live capture her own window took (`FBTODO_PIP_DUMP`):
  `fit=pane=1805,125-2413,1011 slack=rows 16..24 of 27 cell=32px size=192 anchor=959,303
  inside=yes scale=2.000`. Restoring the window put her back at `1009,461` — the same numbers as
  before the resize, to the point. The pane's published grid was also changed under her on its own
  (a fake slack note, a hint from a pane three rows off), which is what a resize looks like from her
  side: she refit inside a second, and refit back when the real note returned.
- **...and she HOLDS a pose while the agent is waiting on you, and her whole table is a file.** Two
  halves of one ask (2026-10-07: "make her hold a mood's pose still while the agent is waiting on me,
  and give the mood table a knob so I can retune paces without rebuilding").

  The first is a new MOOD rather than a new mechanism: `turn_ended` is a state the pane has always
  known — it prints it as "turn ended — waiting for you", the ask and stall watches exist for it, and
  it is the one moment the ball is in the owner's court — and the pane now says it on her face too
  (`wait`, a patient `~(^.^)`) and publishes it like any other mood. She HOLDS it: a turn nobody is
  driving is not a moment to be seen cycling through poses. Four of the eight moods hold still now
  and they are exactly the pane's own still faces, which the suite asserts both ways. Precedence is
  argued rather than guessed: below `done` (a finished list still celebrates — that is the news, and
  what the finished-task bell rings on) and above `none` (a turn that ended with no list at all is
  still *waiting for you*), with `turn_running` asked as well, because both flags come from one
  transcript and a new turn can arrive before the old one clears.

  The second: the table is `FBTODO_PIP_MOODS` (or `--moods-file`) — one line per mood, `name frames
  ms`, which is exactly what `--moods` prints, so `fbtodo-pip --moods > ~/.cache/fbtodo/pip-moods`
  and an editor IS the retune. Her window re-reads it on its next half-second tick (one `stat`
  otherwise), re-wears the mood she is in with the new numbers, and says so —
  `buffy-pip moods=… rows=2 moods-on-disk=20` — so "did my edit land" is answerable from outside.
  Three rules make a live knob safe, and each is a check: a file naming only SOME moods is an
  override (the rest keep their built-in rows); a line naming a mood the pane cannot produce, or
  with the wrong field count, is ignored; and a row whose frames are all past the end of the art is
  ignored whole, because losing a pose is worse than ignoring a typo. `--moods` prints the EFFECTIVE
  table, a missing or rubbish file leaves the defaults alone, and deleting it puts them back.
- **...and she does not VANISH — not on a fresh pane, and not on a minimize.** Two snapshots were
  being treated as live state. The pane's process chain is a snapshot taken when `pip start` ran, so
  a pane reopened, a session restarted or the app relaunched left every pid on it dead and a window
  that insisted on the chain answered "no window" for a window that was right there: `findHost` now
  falls back to the owner NAME, which is what it was always documented as. The measurement is the
  other half — a capture of a window that is minimized or mid-redraw can come back empty, and an
  empty measurement used to overwrite the only placement she had: a FAILED attempt no longer counts
  as "none yet" while the window is the same window (the last good fit for that number stands until
  a new one lands), a state that measured nothing is retried on its own 1.5s clock
  (`--fit-retry`) instead of waiting out `--fit-every`, and a window coming BACK ON SCREEN forces a
  re-measure on that tick ("she does have quite a bit delay to show up back if i minimize the window
  then come back" — the delay was that clock, not the capture). She is also placed BEFORE she is
  shown: the ordering flip and the `setFrame` happen in the same tick, so there is no frame where
  she appears at the corner she had while she was away, and with no host window at all she is
  hidden rather than dropped at the middle of the screen — the middle of the screen is the chat
  behind the pane, and "always in there" also means "never anywhere else".


### Added
- **The pane has a supporting character, and her whole face is the state.** buffy-chan is six
  ASCII cells and an ahoge (`~(o_o)`) standing at the left of the top border, and her face is
  chosen from the facts the rows under her are drawn from: `(>_<)` is the nudge row, `(._.)` is a
  heading left over from an earlier turn, `(-_-)` is a list nobody has moved in the pane's own
  idle window, `(x_x)` is the failure about to be printed under her, `(^o^)` is a list that
  finished. Her mood is also her INK, resolved out of `_styles` rather than invented — the
  accent at work, the success green at the finish, the warning yellow on a nudge, the error red
  on a failure, and the frame's own grey only while she dozes. She animates, and all of her
  does: every living mood is a cycle of whole faces (eyes and mouth together — eight seconds a
  loop for the work mood), stepped once a second on the pane's OWN clock (`now_ms // 1000`, the
  tick the status strip's spinner already runs on), so she keeps no timer of her own and a frame
  remains a pure function of the state and the clock. The three moments hold still on purpose: a
  failure is already over by the time it is drawn, and a squint and a doubt are instants rather
  than states. At the finish she SPEAKS: one line (`BUFFY_DONE_LINE`, `every step done — nice
  work`) on the pane's spare row — the blank line between the list and the footer, which exists
  because no step wanted it — so she is heard at the moment the work ends and the list pays no
  row for it, while a pane with no spare row simply has her stay quiet. Her six columns come out
  of the METADATA's budget rather than the list's: that slot is a truncation by construction (it
  clips to whatever the title leaves it), so what she shortens is a tail  the border was already
  clipping, and where her cells would take the tag off the border entirely she is the one who
  goes. When the chip is carrying a process note (a mute, or a reload) that note gives up its
  own tail first: a note clipped six cells early still says the phone is off, while those six
  columns are the difference between the pane's character being on the border and not. Every face is the same six cells and pure ASCII, so the pane cannot reflow because she
  blinked or because a terminal disagrees with the code's ruler, and she belongs to the framed
  pane alone — the plain, machine-readable frame carries no character. `BUFFY_CYCLE`,
  `BUFFY_REST`, `BUFFY_FRAMES`, `BUFFY_INK`, `BUFFY_CELLS`, `buffy_mood`, `buffy_face` and
  `buffy_line` are the whole of her.
- **...and she is drawn out, below the list.** `BUFFY_ART` is her at nine rows and twenty-six
  columns — the ahoge, the hair over her head and falling past her shoulders, the bow, the face,
  the hoodie with the F on it and a fist with a spark — drawn in the pane's own margin between the
  last fact and the state strip, in her mood's ink. It is the same character at a second size:
  the row where her face is holds `{face}`, the border's own six cells without their ahoge (the
  portrait draws the hair around her), so the mood and the tick the border is wearing are the ones
  she is wearing, and a check asserts the two forms agree mood for mood. She is drawn only with the
  families the frame already trusts — ASCII, box drawing and the block elements the progress bar is
  made of — every row padded to `BUFFY_ART_W`, so a portrait can never be the thing that breaks the
  grid. She stands in the rows the frame has LEFT OVER and in nothing else: the room is measured on
  the finished frame (after the list, the heading, the bar, the facts and the state have taken
  theirs), she is not in the row budget at all, and turning her portrait off draws the same steps,
  the same bar and the same state — asserted as a property. `buffy_art(face, room)` returns the
  WINDOW that contains her face, from one row of room up, so the pane that can only afford two rows
  of her gets her face rather than two rows of hair; the whole portrait is returned when it fits,
  so her ahoge is there too. A pane with no slack has her on the top border alone, and an unbounded
  frame — a pipe, a fixture that asked for no height — draws no portrait at all.
- **...and she is a PICTURE, where the terminal can show one.** The drawn portrait is the fallback,
  not the character: with room for `BUFFY_PIXEL_MIN` rows, the pane draws the art itself out of
  `assets/buffy`, averaged down to pixel cells; in the half-block grid a cell is one pixel wide and
  TWO tall, the upper pixel in the cell's foreground and the lower in its background, so a `▀` is a
  picture every terminal already knows how to show and no graphics protocol is needed. `src/fbtodo/buffy_pixels.py`
  is the whole of it, standard library only: a PNG reader that handles every row filter and refuses
  a 16-bit or interlaced file OUT LOUD rather than decoding a smear (it was also checked pixel for
  pixel against `sips`' own decoder on the 900×900 originals); a cell is the ALPHA-WEIGHTED average
  of the pixels under it, because a plain mean drags every edge of her white hair towards black and
  one source pixel picked out of a twenty-pixel cell is a lottery; transparency is a HOLE rather
  than a black pixel, and a blank run after ink drops the colour first so the cutout does not carry
  her hair's colour out to the row's end; equal neighbouring cells collapse into ONE escape, which
  is what keeps a row that is rewritten every second cheap. Her walk is the clock and nothing else —
  `clock_ms // tick_ms`, one tick per mood (`BUFFY_TICK_MS`: 900ms at work, 600 at the finish, two
  seconds while she dozes) — so two panes at the same instant show the same pose, a frame that
  cannot be read is stepped OVER rather than left as a hole, and a machine with no frames at all
  falls back to the drawn portrait. Frames are found the way the pane finds everything else:
  `FBTODO_BUFFY_DIR`, then `~/.config/fbtodo/buffy/`, then the checkout's own `assets/buffy` — the
  twenty 128×128 frames `scripts/buffy-thumbs.py` derives from the owner's own stickers (`sips` to
  pre-shrink, an alpha-derived content box so the crop is on the character rather than the canvas,
  and an averaged down-scale). She stands in rows RESERVED out of the pane's height before the list
  is fitted (`buffy_panel_rows`), never more than half of what is left over and never below the
  steps' own `BUFFY_LIST_FLOOR`, so a pane with the room always has her and a pane without it has
  exactly the list it would have had; `FBTODO_BUFFY_ROWS` is the owner's say, and its `0` gives
  every row back to the steps. The ink depth is deliberately NOT one of the conditions: a
  256-colour pane gets the same picture posterised rather than a different one, because the frame's
  geometry has to be identical at every colour depth — that is the property the bar's own check
  asserts for the frame as a whole. All of it is pinned: the filter round trip and the two
  refusals, the average and the hole, the one-escape-per-run rule, the clock's stepping, its wrap
  and its fallback, the shipped frames' shape and palette, the reservation's arithmetic and its
  knob, and a full 46×24 pane of fourteen steps where her picture is below the list, the pane keeps
  its height and its width, and the knob gets the steps back.
- **...and she is drawn in BRAILLE by default, at four times the pixels.** A half-block cell spends
  a whole cell on two pixels; a braille glyph is U+2800 plus a bitmask, so one cell is a 2×4 block
  — eight sub-pixels — and the same `cols × rows` panel is a `2*cols × 4*rows` pixel picture
  instead of `cols × 2*rows`. On the pane this character is actually judged on (nine to thirteen
  rows of panel) that is the difference between a blob and a silhouette: her ahoge, the sweep of
  her hair and the line of her hood resolve, where the two-pixel grid averages each of them into a
  chunk. What the finer grid trades away is colour — a cell carries one ink, the coverage-weighted
  mean of the dots that are ON, so a dot below `BUFFY_ALPHA_MIN` contributes neither a mask bit nor
  a colour — and it puts the shape partly in the FONT's hands. That would have been the end of it,
  and it was, for one afternoon: the braille grid did carry four times the pixels and still looked
  WORSE in the pane, because a braille glyph's dots do not FILL the cell they stand in. Measured on
  the owner's own pane, a `⣿` lights about a third of its cell, so a solid area of her hair drew as
  a dim grey stipple rather than as hair — more resolution, harder to see. So a cell whose block is
  FULL is drawn as a solid `█` instead of its eight dots: the same ink and no extra escape, because
  a block paints every pixel of the cell, and it is exactly the cells that are solid in the art
  that were paying the most for the dots. A part-covered cell keeps its braille glyph, which is the
  half of the job only braille can do, so the silhouette keeps its four-times detail AND the solid
  parts of her are solid — on the owner's pane, 122 of the 191 lit cells of a thirteen-row panel
  are full, so roughly two and a half times the panel's lit area. It is the default rather than an
  opt-in because the pane already requires those glyphs: the status strip's spinner is `⠋`, so a
  terminal that can draw this frame at all can draw U+2800. `FBTODO_BUFFY_STYLE=blocks` is the way
  back to the two-colour grid, for a terminal whose monospace face draws the dots small; anything
  that is not `blocks`/`block`/`half` is read as braille, because a typo in a knob must not be the
  reason a pane draws nothing. The cell cache is keyed by the grid as well as the frame and the
  panel size — the two grids have the same SHAPE, so a shared key would hand one of them the
  other's cells — and a caller's own `style=` beats the environment. Pinned in the suite: the dot
  order (1,2,3 down the left of the cell, 4,5,6 down the right, 7,8 beneath them — get it wrong and
  she is mirrored and upside down), a hole spending no escapes, one escape a run, the knob's
  parsing and its precedence, the cache's key, and a WITNESS for the four-times claim rather than
  arithmetic on it: four single-pixel stripes in one cell survive braille as a four-dot mask and
  vanish into a hole in the half-block grid, which has only two pixels to put them in. The
  framed-pane check now runs in BOTH grids — the default one among them — and finds her picture by
  each grid's own signature (the lower pixel in the background, or two braille glyphs where the
  strip's spinner is one), because a picture found by the half-block grid's escapes alone is a
  check that goes quiet the moment the default changes.
- **...and the panel's pixels are spent on her FACE, not on her canvas.** A frame is a square canvas
  with a chibi hung in the middle of it, so the whole character in a twenty-six-column panel is a
  body whose face is three pixels across: more resolution and still nothing a reader recognises,
  which is what the pane was actually reporting after both of the changes above. `buffy_pixels` now
  takes its window from the alpha content box (`content_box` — the canvas is no guide to the
  character), squares it, and anchors it at `BUFFY_FACE_AT` of the way down her (`zoomed`). That is
  the same rule `buffy_art` already keeps — a crop takes her feet, never her face — and the anchor
  was measured as a PICTURE rather than assumed: hung from the TOP of the box the window zooms past
  her face and shows a head of hair and a white onesie, and at 0.30 it is hair and no face at all,
  where 0.42 puts her eyes and mouth in the middle of the panel. `BUFFY_ZOOM_DEFAULT` is now `1.0`
  — the WHOLE character — and that is a retreat with a measurement behind it: the closer window was
  checked at every panel height the pane will reserve (9, 12 and 16 rows, against the whole
  character at each) and it lost at all of them, for a reason worth writing down. A head that fills
  the panel is mostly FLAT colour, and a flat area has no detail for a bigger window to reveal, so
  the zoom bought bands of hair and onesie while giving up the ahoge, the hood and the spark that
  make her recognisable in the first place. `FBTODO_BUFFY_ZOOM=2` is still the owner's say and the
  window, the anchor and the content box are still there for it; what changed is which one is the
  default. The shipped frames stay at 128×128 to feed a closer window (`scripts/buffy-thumbs.py
  --size 128`, 556 KB for the twenty), the zoom is part of the cell cache's key, and the suite pins
  the knob's parsing and its clamping, the default, the window's squareness, the anchor's effect on
  the real art, the cache key, and the panel's answer at each zoom.
- **...and inside her outline a braille cell carries TWO colours, so her line art survives.** The
  braille grid took its dots from the ALPHA channel, so every cell inside her silhouette held one
  averaged ink — the eyes, the mouth and the seams of the hood averaged into the skin around them.
  On the owner's pane that is what the picture was: her face as unbroken horizontal bands of skin
  and cream, described in the pane's own words before it was diagnosed. A cell she FULLY covers is
  now split at an ABSOLUTE ink level (`BUFFY_INK_LEVEL`): the sub-pixels darker than it are the
  dots, in their own colour, and the lighter rest of the cell is set as the glyph's GROUND as well
  (`paint_braille` writes both, ink first), so an eye is dark against the skin it sits in rather
  than a slightly darker grey. A cell with nothing dark in it, or nothing light, is still one solid
  colour, which is what keeps the flat areas of her bright; a cell she only partly covers is still
  her silhouette, one ink and no ground, because what is not her is the PANE. The level is absolute
  rather than measured against the cell, and that is the measured part: a split at the cell's own
  midpoint fires wherever the art has any gradient at all — 56 of the 200 cells at the owner's
  panel size — so her face came out as a field of dots. Two further findings are recorded here
  because they bound what the pane can ever draw. The desktop's terminal is xterm.js (`@xterm` in
  the app bundle, and no sixel, kitty, iTerm2 or image protocol of any kind), so there is no path
  to real pixels in that pane and cell art is the ceiling; and among the cell grids, braille's 2×4
  is already the densest there is — sextants (U+1FB00) and the quadrant block are ruled out by the
  FONT, since the app ships DM Mono and its charset has no legacy-computing block. The suite pins
  the three rules, the two-colour escape order, the level's effect on a cell of mid-tones, and the
  two-ink cell that the grid is now built around.
- **...and the grid she is drawn in is the HALF-BLOCK one, which is the smaller of the two.**
  `buffy_style()` defaulted to braille for its four-times pixel count, and at the size this pane
  actually gives her that trade is a loss: checked as pictures side by side, at the panel's own
  nine-row cap the two grids are 18×18 and 36×36 pixels and the 18×18 half-block one is the one a
  reader recognises — a round white head with a dark hood and a spark, against a pale field of dots
  with the same features smeared through it. The reason is the art: a chibi is FLAT COLOUR with
  thin dark lines in it, which is exactly the case where a cell's second COLOUR (half-block: two
  exact pixels a cell) beats its second pair of pixels (braille: a 1-bit dot pattern whose ink and
  ground are each one averaged colour). Braille is not removed and is not a fallback — it is one
  `FBTODO_BUFFY_STYLE=braille` away, it carries two colours a cell as well, and it is the grid to
  reach for on a pane with the rows to spend for four times the sub-pixels; on THIS pane it is the
  second-best picture. The suite keeps both grids' rules, and `buffy_style` now takes an explicit
  value as well as reading the environment, so a caller can name the grid it wants.
- **...and her panel is sized to the pane's WIDTH, the way a terminal image renderer sizes an
  image.** `BUFFY_PANEL_MAX` was a flat nine rows, and a nine-row panel is eighteen columns
  wide, because her picture is SQUARE in pixels and a cell is one column wide and two rows tall
  in the grid she is drawn in. On a forty-six-column pane that spent eighteen columns on her and
  left twenty-eight of the row empty — the same waste a terminal renderer would never make, and
  the reason a `chafa` or `timg` preview of the same art looks different from this panel: those
  spend the whole cell grid. `buffy_panel_rows` now takes the pane's width and lets the cap grow
  to the square the pane could hold (`width // 2` rows), still bounded by half the room the steps
  left and by their own floor, so a SHORT pane is unchanged by it. Measured on a real frame:
  forty-six columns by forty rows gives her a 32×32-pixel panel where it used to give her 18×18 —
  three times the pixels, which is the difference our own renders show between a blob and a face —
  while the same pane at twenty-four rows still reserves eight. The suite pins the growth, the
  half-of-the-room bound, the square never outgrowing the pane, and the short pane that must not
  move. Not fixed, and now known: on a narrow pane the WRAPPED heading and steps are not part of
  the `chrome` the reservation is computed from, so at thirty columns a long list can still push
  her reserved rows off the bottom of the frame. She is dropped rather than drawn wrong, and the
  frame keeps its exact height, but she is not drawn.
- **Idle dead time is reported, not assumed.** AGENTS.md tells a session never to `sleep`
  merely to wait and never to poll in a loop with no useful work between the polls, and the
  journal already records every call a session made, in order, with its timestamp — so the rule
  became askable of the transcript rather than taken on the session's word for itself. `scan.py`
  gains `journal_calls`, `idle_dead_time`, `journal_dead_time` and `dead_time_report`, beside
  `_record_actions` — whose action list is deliberately the WRONG substrate for this: `read_files`
  is left out of `ACTION_TOOLS` on purpose (reading is not progress), so a poll loop with reads
  between the polls is invisible to it, and that is exactly the session that was working. Two
  shapes are violations. A **bare sleep** — the command is nothing but a sleep (`sleep 30`), so
  the window it takes is dead time by construction; `sleep 5 && curl …` is work that happens to
  wait and is not one, and a backgrounded `sleep 600 &` leaves a process behind rather than
  holding anyone. A **poll loop** — `poll_run` (3) or more calls of the SAME command in a row
  with no other call between them, a read included. The walk covers the newest 2 MiB of the
  journal, needle-gated the way the pane's own walk is, so a megabyte of prose costs one `in`
  test per line and no json. Teeth: a selfcheck phase (`--only idle-dead-time`) drives three
  fixture journals — a busy session, and a poll loop broken by reads, both of which must come
  back clean, and a session that sleeps bare and then polls one command three times, which must
  come back as exactly those two violations in order, the report naming `sleep 30` and `3x … over
  2s` — plus the rule's own edges: the answer does not depend on the order the calls are handed
  over in (the sort is stable, so one record's calls keep the order they were made in), and
  `min_sleep_s` is the knob that drops a sleep below the cutoff.
- **The pane can say why it did not ring.** A finish bell that decides "no" leaves nothing
  behind: a decision that sends nothing never reaches `phone.log`, so "the list finished and my
  phone stayed quiet" had no answer anywhere — the pane's log said only that it had asked. Now
  the ask carries a record with it (`todo-bell.py --note PATH`, which the pane passes): one line
  per ask, `<time>  <action>  list <id>  <why>`, where the refusals are the point — `no push  list
  8f1c…  already pushed for this list (10/10)`, `no push  list 8f1c…  all 3 done, but the turn is
  still running`. The same lines are repeated on stdout, so the pane's own log names
  the reason on the line that records the ask, and `fbtodo status` prints the newest one
  (`finish push : … — 12m ago`) for an operator who arrives later and does not know the file
  exists. The bell writes nothing without the flag — the session timer asks every 5s, and a
  record of that would be a record of the clock — and `--print` writes nothing at all. The record
  is bounded (the newest 500 lines, rewritten atomically). Teeth: the kit's suite pins the
  refusal line, the stdout echo, the report-only silence, a send written down beside the
  refusals, and the bound; fbtodo's suite pins that the ask carries `--note` and that the reason
  lands on the pane's own log line.
- **A finished turn in the desktop app reaches the phone.** The finish bell was asked in
  exactly one place — the shell wrapper's session timer, which names a shell pid and resolves
  the `freebuff` process under it — and the app's turns have no such process behind them: its
  agent runs inside the app and its store writes no journal at all. Measured 2026-10-05 on this
  machine: four desktop threads worked all evening with not one push, while the single ask that
  existed answered about a finished CLI chat sitting in its shell's cwd and reported "already
  pushed" for a finish hours old — a list nobody was looking at, and silence for every finish
  after it. The pane is the process that can see such a finish, because it is drawing the list,
  so the pane asks: once per list, only for a finish it WATCHED (the list is remembered while
  it still has work in it, so a pane opened on work that was already over says nothing, which is
  what keeps a glance at an old thread from buzzing a phone), and it hands the state over
  (`todo-bell.py --state -`) rather than making the bell re-resolve a session nobody is looking
  at. The bell still owns the decision and the one-push-per-list record, so two askers cannot
  double-send, and `--push-only` leaves the chime to the session's own timer — two ringers for
  one finish is a Mac that rings twice. The desktop's boundary is the store's own word rather
  than a quiet window: `threads.turn_state` says whether a turn is alive (fbtodo carries it as
  `turn_running`), so a thread with no turn running and every step ticked is the same news a
  journal's `shouldEndTurn` gives. No new knob, and nothing guessed from a clock. Teeth: both
  boundaries' truth tables (`list_has_ended`), the bell's decision matrix for a desktop state (a
  running turn is silent and `--print` names it), a state handed over on stdin deciding
  identically, and end to end a real pane on a pty pointed at a fixture store — silent while the
  turn runs, one ask when the store's turn goes idle with the list complete, that ask carrying
  the state on screen (`backend`, `turn_running`, the count, the list's own fingerprint), and
  nothing at all from a second pane opened on the same finished thread.
- **The pane has a button: the ntfy push, on the frame's status row, turned off and on by a
  click on it or by the `n` key.** The pane is a picture of a list and the phone is somewhere
  else in the house: the push had three keys and no handle you could see, and all three move
  the CHIME as well — which is a different wish, because "my phone is ringing in a meeting" is
  not "stop chiming at me while I read this". The button is the switch asked for and nothing
  else: it writes `phone-state` alone and leaves `state`, the word `bell.sh` reads, where it
  was. It is drawn from the state the pane HANDS the renderer rather than read by it (a frame
  is a function of its arguments, and a golden file has no notify kit behind it), and it is drawn
  only in what the status row has left OVER its own floor — the state chip, the rule and the live
  clock — so the two things that row is FOR cannot be pushed off it by a control, while the
  readings that follow the clock (the LIST number, the list's age) step aside for it. The floor
  is measured from the state in hand, not from a constant: at the 43 columns a desktop pane gets,
  that is the tight `[ntfy on]`, where a reserve measured on a wide split would have shown
  nothing. Every form is measured on the wider word (`off` is a cell wider than `on`), so the
  button is drawn in the same shape with the push off as with it on — it is the way back, and a
  control that vanished in the state it exists to undo would be no way back at all. A pane too
  narrow for even that keeps the key. A click means asking the terminal for the
  mouse: SGR reporting is turned on while the pane runs, asked for again with every frame it
  paints (a desktop window that reloads comes back as a fresh emulator with no mouse mode and no
  memory of what a still-running pane asked of it, and a button that dies with the window that
  opened it is a failure with nothing on screen to explain it), and handed back on every way out of it
  — Ctrl-C, a stale exit, the self-reload's own exec — because a terminal left reporting a
  mouse nothing is reading cannot select text in any window until something restarts it. A
  reader who would rather keep their mouse asks for that with `--no-mouse`
  (`FBTODO_PANE_MOUSE=off`) and keeps the key, and a push this pane did not mute is not this
  pane's to unmute — the rule `u` already keeps — with one the ENVIRONMENT holds refused by
  name, because a control that does nothing silently reads as broken. Teeth: the switch (the
  chime untouched throughout, the record that makes it outlive both the pane and the list, the
  env and not-ours refusals), the mouse reader's truth table (a press is a click; a release, a
  drag, the wheel, a right-click and an arrow key are not), the frame (drawn only from the
  switch it was handed, named with the key that works it too, giving way to the state chip and
  the clock, and findable in the painted frame at the cells it occupies), and end to end a real
  pane on a pty whose own painted screen is replayed to find that cell, written a click there,
  and asked whether the push moved — a click beside it must do nothing, and the mouse must be
  handed back on the way out.
- **`fbtodo panes` lists every todo pane on the machine, with the subject it holds and the
  interpreter it runs.** `why` answers where ONE session's list pane is, for a window you name;
  nothing answered *which* todo panes exist — so after restarting three of them by hand the
  only way to see all three was `tmux list-panes` plus two `ps` calls, and the two the desktop
  app holds are not tmux panes at all. This walks the PROCESS table rather than tmux, which is
  what makes it every pane on the machine: a tmux pane's process is tied back to its pane id,
  window and tty by ancestry, and a pane outside tmux is named by its tty instead. Each row is
  the pane's tmux pane and window (its tty when it has none), its pid, the subject it latched
  itself to, and the interpreter its own command line names; `--json` is the same document for
  a script. The subject needed recording to be readable: the lock lives in the pane's arguments
  and in its own environment, and the kernel snapshots an environment at exec, so a value
  written at runtime is invisible to `ps` — the pane now also writes `pane-subjects/<pid>.json`
  beside its state root (one file per process, so panes starting in a burst cannot lose each
  other's entry to a race) and `fbtodo panes` pairs those records with the processes it finds.
  A pane pointed at another `FBTODO_HOME` is read from its OWN root, taken from the pin in its
  command line. Teeth: the suite pins the command-line rule (a pinned pane and a bare `fbtodo`
  are panes; a watcher, a one-shot read and a `grep fbtodo` are not), the pairing of a record
  with a process and with a tmux pane, and end to end a real pane on a pty, whose recorded
  subject and interpreter `fbtodo panes --json` must name.
- **`fbtodo mute` drives the same switch without a keystroke — `on`, `off`, `list`, `until-done`.**
  The pane keys are the fast way when the pane is what you are looking at, and the wrong way
  for everything else: a script, a status row, another program's button, a shell that is not a
  pane at all. So the switch is also a command, over the SAME two files and the SAME record —
  there is no second implementation to drift. `fbtodo mute`, or `fbtodo mute list`, reports the
  row the pane would be showing (`  notifications : on`, or `off — <scope>, by <who>`) and names
  which of the kit's two switches are actually off; `--json` gives the same facts as a document
  a script can parse (`muted`, `scope`, `by`, `chip`, `switches`, `record`). `on` mutes until
  told otherwise, `off` gives them back by restoring the word that was there (a `phone-state`
  that never existed is removed again) and is a no-op rather than an overwrite when there is
  nothing of ours to undo, and `until-done` mutes until the list this directory is working on
  is finished. That last one is a PROMISE about a list, so it reads the list instead of assuming
  one, and asked when the list is already finished it says so and writes nothing: a mute whose
  condition is already met is a mute nobody would have wanted. The promise is kept by the
  program, not by the pane that was asked — `auto_unmute` lifts it when the list is finished,
  which means by the NEXT pane to run if none is open now. WHO asked decides who lifts it: a
  mute a key took is the pane's promise to itself and a pane that closes hands the phone back;
  a mute a command took is the person's promise to themselves, so a pane closing leaves it alone
  and only the finished list (or `fbtodo mute off`) ends it. A word that is not one of the four
  verbs is a usage error (2) that prints the verbs it did find, a verb on a command that takes
  none still is, and a switch that could not be written is 73 — the file is the switch, and a
  switch that was not written is not a mute. Teeth: dropping `mute` from the verb routing and
  pointing the dispatch at another command each fail the new check.
- **A key in the pane mutes the phone, and gives it back when the list is finished.** `m` is
  quiet until this list finishes (the default, because a mute that needs a second visit to undo
  is not a mute), `M` is quiet until you say, and `u` is loud again — and none of them means
  leaving the pane to learn a file's name: the key writes the notify kit's OWN two switch words,
  `phone-state` (which `phone.sh` reads) and `state` (which `bell.sh` reads), the same six words
  (`off 0 false no disable disabled`) the scripts already accept. No new switch and no new
  argument in the kit: a pane that muted and a shell that muted are then the same state and
  cannot disagree about it. The undo RESTORES rather than overwrites — the word that was there
  goes back, a `phone-state` that was never there is removed again, and a switch that was already
  off before the keypress (yours, or `FREEBUFF_PHONE=off` in a wrapper, which no file can lift) is
  left exactly as it is and the chip says so rather than pretending an undo worked. The pane's
  title chip carries the switch and the key that lifts it (`quiet until done · u`, `muted · u`),
  next to a reload note if both are true. What the key promised is kept by the program, not by
  the pane: the record of the switch is on disk, because a pane re-execs itself into every new
  build under it (`BUILD_CHECK_S`) and a mute held in a variable would be dropped by the upgrade
  it was pressed a minute earlier — so the next pane honours it, lifts it when the list finishes,
  and lifts it when the pane closes (a pane that exits holding a mute leaves a phone that never
  rings again, and the only key that fixes it is on the pane that just closed). `--no-keys` (or
  `FBTODO_PANE_KEYS=off`) is the opt-out for a pane whose terminal is not its own to read.
  Found while building it: `termios` with `TCSADRAIN` BLOCKS in the pane's SIGINT handler — it
  waits for output to drain first, and a pane writes output every second — so a pane with the key
  reader open could not be Ctrl-C'd and had to be killed; `TCSANOW`, which is all these input
  modes ever needed, is why the check exists. The self-check drives the real pane on a pty: `m`
  writes both files and the chip names the undo key, `u` puts back exactly what was there, `q`
  changes nothing, `--no-keys` ignores `m`, and the terminal attributes are back after Ctrl-C.
  Teeth: dropping the chip from the title, dropping the key reader, and reverting to `TCSADRAIN`
  each fail on their own check (the last one on the suite's pre-existing "must still leave
  cleanly on Ctrl-C").
- **Every reader of a desktop store answers the same four facts about every thread — and the
  suite now holds them to it.** A stacked row (`--threads`), `json`'s `threads` array and
  `board`'s desktop rows all carry each thread's own heading and staleness now, with the same
  read the followed thread's own read makes: `live_threads` runs `_live_turn` then
  `_committed_prose` per thread, anchored to that thread's list — so a running thread's row is
  the LIVE list from `harness_state` (as the followed thread's is) and a finished one's is its
  committed list, and a glance at another tab says whose objective the list carries instead of
  only how far it got. The board draws `goal:` / `stale goal:` for a desktop thread exactly as
  it already did for a CLI one. The self-check adds the cross-reader invariant: over one
  fixture store (a fresh heading, a stale heading left one row back, a heading-less thread and
  a running one whose live list differs from its committed list), `json`, the pane's stacked
  rows and the board must agree per thread on done/total, heading and staleness. Teeth verified
  both ways — dropping the board's heading and dropping the stacked staleness each fail on the
  exact thread — and the fixture had to be given a `role` column: without one the prose read
  fails softly and the invariant passes VACUOUSLY, which is its own warning about testing
  agreement (the sanity assertions pin a heading, a stale one and a live list so it cannot).
  Measured 2026-10-04 on a real three-thread store: the stacked read is 119 ms against 55 ms
  for the followed thread alone, on a 1 s pane tick.
- **The pane's title says which source answered, and what it passed over to do it.** A list
  that looks wrong is nearly always the same question — *why is this list here and not the other
  one* — and only `fbtodo why` could answer it. `_snapshot` now records the `auto` chain's own
  account of itself (`source_why`, e.g. `cli finished 31h → desktop`, or `cli none here` when a
  source had nothing for the directory), and both renderers carry it in the place that says what
  the pane is showing: the framed title chip (`FREEBUFF TODOS · cli finished 31h → desktop`) and
  the plain heading, beside the existing `backend · stamp`. Only `auto` writes a note — a chain
  of one source has nothing to explain away, so `-s cli|desktop|nas|file` frames are untouched —
  and a state without one draws exactly the title it always did. The note is clipped, and
  dropped outright below the width where it can still say something, so the right slot keeps the
  session it is naming: on a narrow pane the pane keeps its name and the session its columns.
- **`fbtodo board` — every live session in one frame.** A pane follows one list; people run two
  or three agents at once, and the question "what is everything doing" had no answer short of
  switching windows. The board asks the same two local stores the pane already reads and draws
  one row per live session: the project, where it came from (`cli`/`desktop`), its `done/total`,
  how long ago its store moved, and the first `--board-rows` (2) of its steps. `--board-max N`
  caps how many sessions are drawn, `--board-live MIN` (90) is how quiet one may be and still
  count as live — a store clock, so a session that stopped drops off by itself — and `--live`
  keeps redrawing in place, diffed row by row, with the cursor always restored. `--json` is the
  rows for a script. A CLI session is read through `read_cli` and a desktop one through
  `live_threads`, so a row is the same list the pane would draw, not a second opinion about it;
  the remote store is left out on purpose, since an ssh per poll is the opposite of one cheap
  look. Newest activity first, and the CLI half stats before it reads: a store with a hundred
  finished chats costs a hundred stats and one journal scan.
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
- **`fbtodo dead` lists EVERY piece of unreachable code, not just the first.** The reload
  probe holds a build the moment its own source proves one branch can never run
  (`unreachable_code`), but it only ever names the FIRST finding — enough to refuse the
  build, not enough to fix it. The two passes behind that check (a literal or a terminator,
  and a guard's promise) are now the collection as well as the search: `dead_code` asks the
  same `_unreachable_all` / `_guarded_suites` over every file and keeps all findings, and
  `unreachable_code` is its first element, so the probe and the list can never disagree. The  subcommand prints `path:line` with the reason for each (compact and `--json`), and exits
  nonzero when it finds any — the same fact the probe acts on.
- **A replay holds the CLI journal and the desktop's live history to one answer.** `pick_goal`
  and `_now_and_nudge` are shared code, but the two stores are not — the CLI reads
  `log.jsonl`, the desktop reads `threads.harness_state` — so a rule right in one and wrong
  in the other would only ever be caught by reading the SAME turn twice. The self-check now
  writes each case (a heading before the list, a request after it, a bare continuation, the
  same words again, a heading with no list, a nudge with nothing to anchor on, a compaction
  summary) to both a journal and a harness history at the same positions, reads both, and
  asserts identical `goal`, `now` and `nudge` — a drift fails as a diff between the two
  readers, naming the case and the field, not as a surprise in a pane.
- **A finish or a drop can be posted to a Discord channel, where the Hermes agent can read it.**
  The kit's sinks were both a phone, so "is freebuff actually doing anything?" had nowhere to
  look: the agent in Discord answers out of what reached Discord, and nothing this kit sent ever
  did. `discord-send.sh` posts one message through the gateway's own CLI on the NAS (`hermes send
  --to discord:#freebuff -f -`) — a bot-token path, so no model, no agent loop and no running
  gateway turn is needed and a push costs one ssh. It is a **second sink, not a third transport**:
  `phone.sh --discord` runs the phone push exactly as before and posts beside it, and a caller
  that does not pass the flag never reaches Discord at all — which is how "finishes and drops
  only" is enforced per bell, and the two callers are `todo-bell.py` and `drop-bell.py`.
  `FREEBUFF_DISCORD_TARGET` picks the channel (default `discord:#freebuff`) and
  `FREEBUFF_DISCORD=off` (or a `discord-state` file) mutes it without unsetting anything; a
  target that is not a plain channel is exit 78 and a delivery that fails is exit 69 with a line
  in `discord.log`. The target is validated before use, so a config value can never become a
  second command on the far side of the ssh, and the ssh is the **agent-less** one
  (`BatchMode=yes`): the app that rings the bells has no `SSH_AUTH_SOCK`, the same reason the
  Hermes MCP server over ssh had been failing to start. Teeth: the kit's suite drives the sink
  through a stub command, and pins the title/body split, the target used and the one logged, the
  mute, the refused target, the usage error, the exit-69 line, that exactly the two bells ask for
  the channel, that `phone.sh --help` documents the flag, and that a plain `phone.sh` never
  reaches Discord.


### Changed
- **The finish push tells the Discord channel what the run was for and what it did, where the
  phone keeps its metadata-only nudge.** A finish in the channel was the phone's message verbatim
  — `3/3 steps done · 2026-10-06 11:20`, session — which answers "is it over" and nothing else, so
  the one reader there who is not the owner (the Hermes agent in the channel, answering questions
  out of what reached it) could ever report only that a list had ended. The channel's body is now
  labelled, one field per line, because it is read out of context:

      Goal: shrink the pane heading to one line
      Summary: the heading now fits and the two failing checks were fixed
      3/3 steps done · 2026-10-06 18:14

  `Goal` is the agent's own `Goal:` heading (`state["goal"]`) and never the session's opening
  request, which would be a claim the agent did not make; `Summary` is the first line of its last
  answer — the same `state["summary"]` fbtodo already extracts, capped by `SUMMARY_MAX_CHARS` —
  flattened to ONE line and capped again for the push, with the count and clock under it. Each
  field is flattened and capped on its own, so a heading the agent wrapped over two lines cannot
  swallow the summary below it. A field with nothing in it is left out rather than shown empty;
  a run with a goal but no prose is STILL sent (it says more than the phone's metadata does, and
  the first cut of this dropped the goal along with the missing summary — the suite now pins
  that); and with neither, nothing is invented: the channel falls back to the phone's body, which
  is what a drop still posts (a drop has neither). All of it travels as a new `phone.sh
  --discord-message TEXT`, which is the second sink's body alone, so the phone's message is
  byte-for-byte what it was and `FREEBUFF_PHONE_TEXT` does not change the channel's;
  `--discord` without the option still posts one message to both sinks.
  `FREEBUFF_DISCORD_SUMMARY=off` takes the labels away for an owner who would rather the channel
  stayed quiet about it. Teeth: the kit's suite asserts the labelled body (goal, summary, count,
  clock), that a multi-line goal and a multi-line summary each arrive as one labelled line, that a
  goal with no summary is still sent while neither means no body of its own and the request is not
  promoted into a heading, that `FREEBUFF_PHONE_TEXT=agent` leaves the channel's body
  byte-identical, that `FREEBUFF_DISCORD_SUMMARY=off` restores the older message, that the phone's
  `--message` carries no agent prose either way, and that `phone.sh` documents and honours the
  option.
- **A pane locks to the list it first resolved, and never moves again.** It used to follow the
  work: re-choosing per poll meant that typing in another tab — which is exactly what the app's
  own thread picker reads as "the thread you are working in" — moved the reader's window to
  another list, silently and mid-read, with both frames valid and nothing drawn to say so.
  "It keeps switching threads" is that. The first successful poll now decides, and the decision
  is made in the ARGUMENTS every later poll already goes through: one source, one `--thread` or
  `--chat`, so nothing has to be remembered between polls and a pane that reloads itself into a
  new build comes back locked the same way. A finished turn does NOT release the lock (a finished
  list is still a list somebody is reading) and a second thread going live does not steal it —
  the pane holds its last list until you close it, which is what was asked for. What the lock
  does not do is choose the FIRST list: that is still `auto`'s chain, and a pane still drops a
  cached state whose session has ended before it latches anything (`pane_cached_state`), so the
  2026-10-04 freeze fix is untouched — a pane still reaches the live thread rather than the
  finished chat, and then stays there.
- **A pane draws one list, not a stack of live threads.** `--threads N` gave a pane a heading
  per live thread, so the question a reader asks of a pane — how far is THIS list — got an
  answer about several, and the set changed under them. A pane now asks for one thread from the
  first poll, before it has anything to latch to (latching after the first resolve left one
  stacked frame on screen, which is the flinch this is meant to remove). The reads keep the
  flag: `fbtodo json`, `snap` and `board` still stack on request, which is the right place for a
  survey. Teeth: the pane beside the source-switch check is now asserted to REACH the live
  thread and STAY on it (the reads there are still asserted to switch, so the freeze fix is
  untouched), and a new check starts a pane on one thread, makes a SECOND thread the newest
  running ask, and requires the pane to still be drawing the first.
- **The remote (`nas`) source is gone, and with it the second pane role.** There are two
  stores, `cli` and `desktop`, and both are on this machine. What goes: `-s nas` and
  `--nas-host`/`--nas-root`/`--nas-project`/`--fb-marker`; the `fbtodo nas` subcommand and the
  `fbtodo-nas-pane.{pid,json,log}` claims it kept; `nas.py` with the ssh probe, the standalone
  extractor it shipped over the wire and the `NAS_EXTRACT`/`nas_ssh`/`nas_probe`/`nas_pgrep`
  around them; `NasSource` and the `nas` backend; the second list pane a window could hold,
  so `pin --role` and the `role` on every layout record are gone and one window has one
  answer again; the notifier's `--nas-watch` mode and the drop watch's `--nas`/`--fb`/`--live`;
  the `FBTODO_NAS*` settings; the three self-check phases that drove it. Pins and remembered
  layouts written by an older build keep working: a `local` half in `fbtodo-pins.json` is read
  as the window's own answer (and dropped on the next write), and the old `fbtodo-nas-pane.*`
  names stay in the legacy-migration list so an install that has them is still cleaned up.
  `NAS_NOTIFY` — which was the local finish bell all along, wearing the remote source's name —
  is `TODO_NOTIFY`. Every other command keeps its shape: `status`, `why`, `locks --json` and
  `pin --json` each lose their remote row and role field, and say the same thing about what is
  left. The self-check drops to 215 checks, all green.
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
  `FBTODO_PATH` overrides the base value for a machine that needs a specific one. The pin
  covers **where** the pane works as well as what it runs: tmux starts a pane from its server's
  environment, so the values the program reads from its own environment to choose a state root
  (`FBTODO_HOME`, else `XDG_STATE_HOME`), a tmux server (`FBTODO_TMUX`) and the session marker
  it counts live sessions by (`FBTODO_FB_MARKER`) are carried in the same command line
  (`pinned_env`), along with the six notify-watch paths (`FBTODO_NOTIFY`, `_DROP`, `_ASK`,
  `_PAUSE`, `_PANE_BELL`, `_LOCKS_BELL`) — the first four decide where the pane works, the
  watch paths where its bells GO, so a machine that points its watches at its own scripts
  keeps them in the pane instead of falling back to `~/.config/freebuff-notify/`. A server started
  before the owner exported one of them — or a login shell's own profile — would otherwise
  leave the pane, and the watcher under it, reading another store, following another set of
  sessions, or ringing the default bells, with nothing on screen to say so. Only values that
  are set are carried, so a command line never claims a state root it does not have (`locks`
  keeps a command line and an environment apart); both builders do it, fbtodo's own pane
  commands and the `fb` launcher. `FBTODO_NAS*` is not carried: it names a project too, but
  every pane and daemon that needs it is handed it on ARGV, which a respawn re-runs verbatim.
- **A pane on an older pin is upgraded instead of left behind.** Those carried values were
  added to the pin over several builds, and the pin is re-run only when a pane is respawned —
  which nobody has a reason to do — so a pane opened by an earlier build keeps its old
  answer forever: another state root, another tmux server, the old bells. The keeper now reads
  each pane's **recorded** command and, when it predates this build, reopens it once on the
  current pin (`upgrade_stale_panes`) — same pane id, same window, and the recorded command
  becomes this one, so what a later respawn re-runs is current too. Only the carried values
  are compared, never the interpreter, the `PATH` or the arguments after them: an `fb` pane
  that names `--instance-of` carries every value and is not stale for disagreeing with a
  split's `--watch-pid`, and with no values to hand on no command is stale at all. The drift
  repair's brakes apply: the pane's own `@fbtodo_repair` switch keeps it, it is tried once per
  `DRIFT_RETRY_S`, and it is told on its chip (`REOPENED ON THE PIN`) rather than changed
  under the person watching it. Because that upgrade rewrites the pane on the next pass, the
  pane is **named first**: `fbtodo status` prints a `pane pin` line and `fbtodo why` sets
  `stale_pin` on the pane's record, both from the same predicate the upgrade acts on
  (`stale_pane_ids`), so the diagnosis and the repair cannot disagree. The values it compares
  against are the SESSION's — read from the keeper's own process, or the shell the session is
  drawn in when no keeper is live — rather than the shell the command was typed in, so a
  variable exported only in that shell no longer reads a current pane as behind. The line
  splits by the knob — `on an older pin — the keeper reopens it on the current one` versus
  `kept (@fbtodo_repair off)`, which nothing else will change.
- A **running desktop turn says so**: the app commits a message row only when a turn closes,
  so a thread that is working has no list to read yet — and `no write_todos call yet in this
  session` read as "the agent forgot" when the store had simply not committed. `-s desktop`
  now reads the store's own live signal (`threads.turn_state` / `turn_alive_at`) and says
  `turn running · no list yet` instead; a stale heartbeat means a turn that died, not one in
  flight, so it is not called running.
- **A running desktop turn shows its list, like a CLI pane mid-turn.** The app commits a
  message row only at the turn's close, so a `-s desktop` pane had nothing to read mid-turn
  and stood on the previous turn's list (or on "no list yet"). The app does keep the agent's
  own live state — `threads.harness_state`, rewritten as it works, whose
  `mainAgentState.messageHistory` carries the tool calls as they are made — and the newest
  `write_todos` there is the in-flight list. `-s desktop` now reads it, with SQLite's own
  JSON walk so a blob that grows with the whole session is never pulled into Python (a pane
  ticks once a second). Three rules keep it the CURRENT list: only while the turn is running,
  only when the list was written at or after the turn's own start (`last_prompt_at`), and only
  when it is not older than what is committed. The turn's start rides along, so `finish_state`
  drops a **finished** previous list rather than reading it as this turn's progress — the pane
  says `last turn's list is done — waiting for this turn's list` instead of showing steps that
  are already over. `turn_running` and `turn` are now carried on the observation as well, so
  the `turn running` sentence reaches the renderers it was written for.
- **The same live history gives the desktop source a `now`/`nudge` line.** A request that
  arrived after the list is the newest work the list does not describe, and the CLI journal
  already says so (`NOW · …`, or `NUDGE · …` for a bare `continue`). `-s desktop` now reads
  the requests from `harness_state` too — every user message the app tagged `USER_PROMPT`,
  which is its own word for a real prompt (a compaction summary or a tool-error injection is
  tagged differently and stays out) — and applies the **CLI's own rules**, reusing `is_nudge`,
  `pick_prompt` and `_newest` rather than re-writing them, so the two sources cannot drift:
  the request after the list is `now`, a `Goal:` heading written for it wins the line, a bare
  `continue` is a nudge, and a request the current list already answers is not drift. With no
  list to measure against there is no `now`, but a nudge can still be said.
- **The desktop source now heads its list with the agent's own `Goal:` line.** A desktop pane
  could say *what* a running turn was doing but not the one-line objective the CLI pane puts
  at the top, because the app's store only ever wrote a heading into the transcript at the
  turn's close. The same `harness_state` prose the `now`/`nudge` line already reads carries
  it mid-turn, so `-s desktop` reads the heading from those text parts (`goal_line`) and
  decides which heading belongs to which list by the **CLI's own rule**, factored out as
  `pick_goal` and shared with the journal rather than rewritten — a heading at or before the
  list belongs to it, a heading written as the turn opens belongs to its prompt, and with no
  list at all the newest heading is what is going on. `goal` and `goal_source` ride on the
  observation, so a desktop pane mid-turn is headed exactly as a CLI pane would be.

### Fixed
- **A frame is exactly the height the pane asked for, whatever the list is worth.** The pane is a
  FIXED grid and it repaints in place, so a frame one row short leaves that row showing whatever
  was painted there before — and the step area reached exactly that state on its own, with no
  character involved: measured 2026-10-07, a 14-step list in a 24-row pane came out 23 rows tall
  (the fit held back a spare row for the goal line, the goal line had no total to print, and the
  row was then simply never painted), while the SAME pane with her panel came out 24 because the
  slack drawing happened to fill the row. The frame is now padded to `height` after the list, her
  art first and blank content rows after it, so the two halves of the drawing can never disagree
  about how tall the pane is. Caught by a new check that walks a step count against a pane height
  with her panel on and off — the case the old "she must not change the frame" check could not see,
  because it only compared the two frames to each other.
- **A pane the desktop app opens holds the thread it is drawn beside, not the CLI chat that
  happened to be live.** The app spawns one terminal per thread and runs a bare `fbtodo` in it;
  that pane asked `auto`, which prefers a live CLI chat, so every desktop pane latched the SAME
  chat — measured 2026-10-05: `ttys000` (pid 70764) and `ttys001` (pid 21248) both holding
  `cli 2026-10-05T17-52-39.193Z` while the thread each was drawn beside went unshown. The app
  hands its terminal no thread id (it marks the process with `FREEBUFF_DESKTOP_STATE_PATH` and
  nothing per-thread, and the orchestrator keeps the per-terminal id in memory behind a launch
  token it strips on purpose), so the binding is inferred from the app's own store: the live
  threads of THIS project, the one the app is working in first (the store's own focus rule),
  then the others — skipping a thread another live pane already holds
  (`held_pane_subjects`), which is what makes two panes hold two threads. A subject named on
  the pane's OWN command line (`-s cli`, `--chat`, `--thread`) is the person's and is never
  overridden, and outside the app nothing changes at all (a pane in TMUX never counts as the
  app's, so a tmux server that inherited the app's environment cannot claim its panes). Teeth:
  the suite pins the app-marker
  rule, the argv guard, the thread chosen for a two-thread fixture store, the second pane's
  different thread, and the lock the binding applies (arguments, environment and the
  `pane-subjects/` record).
- **A pane pinned to one thread no longer draws another's list.** The lock `latch_pane_subject`
  puts in the arguments was never made to STICK: it wrote `--chat`/`--thread` and the
  environment the reload reads, but never set the `pane_locked` flag that its own first line
  checks, so every poll re-latched to whatever subject that poll happened to resolve. This
  machine's own pane log shows the result — `pane locked to the cli chat …` at 10:52:37, :40,
  :41 and :57, four different chats inside twenty seconds, each one replacing the list the
  reader was on. Beside it, `state_matches_request` compared only the BACKEND, so a pane
  locked to one chat was still handed the shared watcher's state about another: the watcher is
  ONE process serving whoever asks first, and "it answers `cli`" is not the same answer as "it
  answers `cli` about the chat you asked for". A cached state now has to name the subject the
  caller named — `target` for a chat, `session` for a desktop thread — and may not carry a
  STACK of threads when the caller asked for one, which is the same report in its other shape:
  a pane asks for one list while the watcher it shares with `fbtodo json --threads 4` writes
  four, and the pane drew every live thread of the store under one frame. Teeth, from the
  probe that found it (all three now assertions in the suite): a read pinned to one chat, with
  a fresh cached state about another sitting right beside it, must answer with its OWN chat's
  list — it answered with the other's; the latch must set its own flag and must not move when a
  later poll resolves a second subject; and a stacked state must be refused at `--threads 1`
  while `--threads 4` still stacks. The same defect had one more door: `read_desktop` fell back
  to "the newest list any thread wrote" whenever the thread a caller NAMED was not in the store,
  so closing the tab a pane was pinned to handed the reader the other tab's steps — a named
  subject now answers about itself (no list here yet) instead of about somebody else.
- **The frame's border lines up again.** The goal heading was drawn with `🎯`, which measures
  TWO cells to `wcwidth` and ONE cell in the terminal the pane was being read in (measured
  2026-10-04), so every row carrying it came out a column short on screen: the right edge
  stepped left under the heading while the dividers above and below carried on to the full
  width. Every width check in this suite still passed, because they compare the renderer's own
  arithmetic against itself — which is the whole blind spot: a frame is a grid of fixed-width
  rows, and one glyph the code and the terminal disagree about breaks the grid on screen only.
  The mark is now `▸`, the one the state chip already uses, and the column the emoji used to
  waste goes back to the heading text (which is why the recorded frames' goal rows gained a
  space and their continuation lines lost one indent). Teeth: `FRAME_CHROME` inventories every
  glyph the framed pane draws for itself — box, markers, spinner, bar, the punctuation used
  inside the frame — and `frame_chrome_is_single_cell()` must come back empty, so the next
  ambiguous character is caught by a check instead of by a reader. Beside it, a frame rendered
  at six widths must be exactly that wide on EVERY row and keep both its edges, which is the
  invariant the old checks only bounded (`<= width`) instead of stating. Four golden frames
  were re-recorded for the marker; the diff is the marker and its indent, nothing else.
- **The width check that was supposed to catch that, now can.** The six-width grid check
  measures each row with `_cell_width`, which is the same ruler that called `🎯` two cells:
  `unicodedata.east_asian_width` says `Wide`, and `frame_chrome_is_single_cell` asks the same
  table the same question. Both therefore agree with each other and disagree with the
  terminal, which is why the pane stayed a column short after the fix shipped — verified by
  rendering the pre-fix code again, which still reports every row exactly the width it was
  asked for while emitting the one glyph that broke the grid. The check beside them reads a
  real render's glyphs and asks Unicode directly, so it is not consulting the thing it is
  measuring: a frame may only draw characters Unicode calls one cell, and `Wide`/`Fullwidth`
  are precisely the two answers a terminal is free to disagree with. It fails on the pre-fix
  render and passes on this one, which is the only evidence that it is a check and not a
  decoration.
- **The frame with no list in it is a grid too — it is now checked, and it was not.** Every
  width check above renders a pane that HAS a list. A pane spends real time without one (a
  turn opens before the agent writes its first `write_todos`), and that frame comes out of a
  different branch of the renderer entirely, which had no width assertion at all. It is also
  the branch that wraps a SENTENCE rather than a step, so it is where a wrap bug would show:
  two note lines, a `NOW` line and both footer rows inside one frame. Measured from a pane
  photographed in exactly that state on 2026-10-04: all eight of its row bands draw both
  borders at the same column, so the frame itself was sound and nothing needed repairing
  there. The gap was in the SUITE, not the frame, and that is what this closes. The no-list
  frame is now rendered at the same six widths and must be exactly that wide on every row and
  keep both its edges — and must still BE that frame, with the note and the `REFIT` row
  asserted present, so the check cannot pass by quietly rendering nothing. Each assertion was
  shown to fail on a copy of the renderer with that one defect put back, and the width check
  still passed on the wide-glyph copy, which is the reason the Unicode one is not redundant.
- **The frame's corners join the way its dividers do.** The outer corners were drawn with
  ARCS (`╭ ╮ ╰ ╯`) while the dividers inside the same box have always been sharp (`├ ┤`),
  and an arc does not meet a rule the way a corner does. Measured on a pane read on
  2026-10-05: the top rule stopped three pixels short of its own corner on the rule's outer
  row and reached the border's column only on its core row, so the corner read as a notch
  beside the crisp T-junctions a few rows below it — a box with two kinds of join in it. The
  outer corners are now `┌ ┐ └ ┘`, so every junction in a frame is flush. Note what this was
  NOT: the corners were never misplaced, and no row was a cell out — at a threshold that
  includes the font's antialiasing the rule meets the border column exactly, which is how
  the earlier "the border is broken" readings were ruled out. The frame's own shape is a
  property of a render now, not a list someone has to remember: any arc anywhere in a frame
  fails the check, at three widths. The notifier's own modal boxes keep their rounded style —
  a different surface, drawn in a different program. Teeth shown by loading the pre-change
  renderer beside this one: the check fails on it and passes here.
- **The two states a pane's close used to be decided by are pinned again, by the code that
  owns them now.** `session_still_live` — the helper that answered "is this session still
  live?" for the close rule — was deleted with that rule (2026-10-04: a pane's lifetime is its
  window's), so there is nothing left to pin by name, and the two states it was asked about had
  no check between them: an ERROR state and an ENDED JOURNAL. Both are now pinned, and they are
  pinned as a pair because the deleted helper collapsed them into one question while they are in
  fact opposite answers. An error is not a finished session: nothing was read, nothing was
  watched, and no journal behind it could have ended, so `followed_session_over` must answer
  False — True would send a pane back to `snapshot`, which resolves the same error and costs a
  re-read to learn nothing — and the frame must say `no conversation DB found` INSTEAD of a
  list, which is what distinguishes "no source here" from "a source with nothing written yet".
  An ended journal IS a finished session: every reader agrees (`followed_session_over` drops the
  cached list however fresh the heartbeat, `_chat_is_live` will not choose the chat again), and
  the pane still draws that list at 100% with `ALL DONE`, because a finished session is not a
  finished pane.
- **The self-check's remaining blind waits are waits now, and three source paths nobody
  reaches have checks.** The locks/reload/keeper families were audited earlier; the rest of the
  suite was not, and it had the same shape in three places. Two `spawn_quiet` stand-ins were
  followed by `time.sleep(0.4)` — a guess that the process table can see a process that has
  just been spawned, which only holds on an idle machine; both now `wait_for` the condition the
  next line needs (alive AND in `freebuff_pids()`), so a slow start costs time instead of
  failing. A keeper stand-in's `time.sleep(2.5)` said "past the cross-check's grace" and is now
  `KEEPER_CLAIM_CHECK_S * 2.5` — the keeper's own claim-check interval, so it cannot stop
  meaning "one pass" when that constant moves. And the two pane checks that wait for a
  `--stale-after` window use `stale_window_bound()` (the window the test itself sets, plus
  start-up) rather than a flat `+ 25` that said nothing about which subject it belonged to.
  On the source side, reading the branches of `sources.py` against the suite found three with
  no coverage at all: `--project/-p` (never appears in the suite, so the way a pane is pointed
  at a sibling project was untested), `FileSource.miss()` (its `no state file at PATH` wording
  appears nowhere), and the held-chat fallback — the one branch where `auto` deliberately
  answers a FINISHED chat because nothing fresher exists, which is where the 2026-10-04 freeze
  fix pushed the work and which no check read. All three are now pinned, each against the
  behaviour measured live first (`--project` answering with the named project's chat while the
  cwd has none; `auto` reporting `cli finished 18h30m · desktop none here → cli`).
  Honest limit on this one: the audit was meant to be measured, not read. A throwaway
  instrumented copy that recorded every bounded wait's headroom was built and run, and the
  numbers it produced for the waits it could attribute were healthy (the tightest attributed
  wait used 21% of its bound), but two of its three runs died in the harness itself and its
  remaining rows could not be attributed to a bound — so no change above is justified by a
  measurement, only by reading the call site and naming the clock it should have used.
- **A live `--watch-pid` no longer makes `auto` follow a chat that has ended.** `_chat_is_live`
  asked the pid FIRST and returned on it: a resolved pid is evidence that the pane has a
  session to sit beside, and every pane the shell wrapper opens carries `--watch-pid`, so in a
  directory the desktop app also works in, an ENDED CLI chat was answered as if it were the
  live session — the pane painting a finished list over a thread actually being worked on,
  which is the freeze the rule (and `journal_liveness`, added 2026-10-04) exists to prevent. The
  switch check missed it because its pane is given a pid that cannot be alive, which is the one
  shape where the shortcut does not fire. Measured on the fixture the pane-close check now
  builds — one directory, an ended chat and a live desktop thread: with a live `--watch-pid` the
  first frame was `cli · 2026-02-02…` and the ended step's list; with none it was
  `desktop · AU · cli finished 2h00m → desktop`. What outranks a turn boundary is now a
  **Freebuff process behind that chat** and nothing else — the check's own sentence, which the
  bare integer in its fixture never earned: a Freebuff process is working that chat whatever the
  boundary says, because it is evidence about the chat; a pid that is merely alive is evidence
  about the pane. So the boundary is asked first, a live pid rescues a merely QUIET chat exactly
  as before, and the pid's identity is consulted only when the boundary has already said no
  (the one time it changes an answer). Teeth, two halves: the rule check now spawns a stand-in
  whose command line ends in `bin/freebuff` and requires it to keep the ended chat live, then
  requires the suite's own pid — alive, not Freebuff — not to; and the pane-close check's new
  `auto` case runs a pane in that directory with a live `--watch-pid`, kills its instance, and
  requires the ended chat's step to appear nowhere in what it drew.
- **The progress bar no longer reads `0% (0/0)` under a list that is all ticked.** The bar
  counted `state["todos"]` — the list the pane FOLLOWS — while the step area draws whatever
  `list_groups` hands it. Those are the same list until they are not: `finish_state` drops a
  FINISHED list the moment a newer request or turn arrives, and a stacked pane (`--threads N`)
  goes on drawing the other live threads, each under its own `done/total` heading. The
  followed list was then gone and the bar was counting nothing, so a desktop pane could show a
  heading reading `11/11` over eleven ticked steps and print `0% (0/0)` on the row underneath —
  the frame contradicting itself, which reads as a bar stuck at zero rather than as an absent
  list. `fbtodo snap` said the same thing (`[---…] 0/0 done`), and so did `fbtodo bar`
  (`todos -`). The rule now lives in one place, `drawn_counts`: the followed list when it is
  there, and otherwise `done/total` summed over the lists the frame is actually painting — a
  number the steps above it add up to. A single-list pane is byte-for-byte unchanged (it has
  one list either way), the estimates on that row still describe the followed list because
  they are still measured from it, and a state with nothing to draw at all still gets the
  no-list frame rather than a bar. Teeth: the stacked-frame check now pins both halves —
  `drawn_counts` is the followed list's `(1, 2)` while it exists, `(2, 3)` once it is dropped —
  and asserts `67% (2/3)` / `2/3 done` with no `0/0` anywhere in the frame.
- **A pane no longer closes itself because the session it is drawing ended.** `fbtodo pane`
  printed `freebuff instance exited — fbtodo pane closing` and left, and the notice was only
  half true: what it had actually asked was whether the SESSION was over, and it asked it the
  way every earlier version did — from a clock. First the pid the pane had resolved (wrong for
  `auto`, which can follow the app's thread while the CLI that opened the pane has exited);
  then, from 2026-10-03, the journal's own turn boundary and the quiet window around it. Both
  rules were about a clock, and both were the same mistake: what the pane draws is a LIST, and
  a list whose session is over is a list somebody is finishing, waiting on, or re-reading. A
  pane that leaves on its own takes the reader's eyes with it at the moment they are most
  likely to be looking, which is why "it keeps closing mid-run" outlived both fixes. The
  question is gone. What ends a pane is named in `cmd_pane` and nowhere else: `--stale-after`,
  Ctrl-C, a `--once` that has drawn its frame, and the window going away. `--wait`, the switch
  that used to opt out, is still accepted and says nothing: its meaning became the default, so
  a command line already carrying it must not start failing over it. `session_still_live` — the
  helper the close decision used, `nas` arm and all — is deleted rather than left unused.
  Teeth: a pane watching a journal older
  than the live window, whose instance is then killed, must still be drawing that list; and the
  keeper's end-of-session check now asserts the pane is STILL there when its session's process
  is gone, which is the check that could not be made to pass before.
  What an `auto` pane closes on is now decided rather than left to the same question asked
  again per source: **nothing it did not ask for.** `auto` re-picks its subject every poll, so
  a session ending is a hand-over — to whatever the directory is working on now — and not an
  event a pane can be closed by; the only clock left is the reader's own `--stale-after`
  window, measured on the store being followed. The close check's fixture said otherwise: it
  announced a rule ("a finished journal must still let the pane go") its assertions had
  stopped testing, and it had no `auto` case at all, though `auto` is the default source. The
  fixture now states a finished session the way one is finished — a `shouldEndTurn` boundary
  in the journal's tail, separate from the mtime `--stale-after` reads — and two cases pin the
  decision: an `auto` pane in a directory with an ended chat and a live desktop thread, whose
  instance is then killed, must go on drawing (the thread, not the chat's list, and never
  `instance exited`); and the same pane asked for `--stale-after 0.05` must exit 0 with
  `idle … closing`, which is the whole of what an `auto` pane can be closed by.
- **`fbtodo board` no longer counts a finished session as a live one.** The board asked the
  same question the single-session pane asks and answered it the old way — from the journal
  file's mtime — so every chat the desktop app had touched counted as a session in progress
  (it appends its own records to the same journal: `cli.feedback_button_hovered`, a note
  saved, a tab closed). It now asks `journal_liveness`, the same code and the same rule the
  pane uses: the agent's last word decides, so a chat whose turn ended drops off the board
  however recently its file was written, and only a journal with no boundary in its tail falls
  back to the quiet window. The two budgets stay apart — a `stat` per chat for the window, a
  cached tail read for the boundary, and `BOARD_SCAN_MAX` still bounds the full `read_cli`
  parses, so a store full of finished chats costs less reading than it did before, not more.
- **The self-check no longer depends on being the only run on the machine.** Its state root is
  per RUN (`~/.freebuff/fbtodo-test-<pid>`) and every private tmux server it drives carries the
  pid in its socket name, so a second run — a second shell's, or the one this session starts
  while the first is still going — cannot delete the other's state mid-check or kill the
  server its sibling is about to inspect (measured: `FileNotFoundError` on
  `fbtodo-state.json`, a keeper `print`ing no repair line because the pane it named was gone,
  and, under three concurrent runs, two of them dying in the same block on a FIXED socket name
  `fbstalesock` this sweep had missed). A run killed mid-phase now leaves a directory only it
  owned, and a sweep clears the ones demonstrably stale rather than a live run's.
- **The waits in the locks, reload and keeper checks are their subject's own clocks.** A fixed
  20 s only holds on an idle machine: a child told to poll every six seconds needs two ticks
  for `open` and two for `clear`, and under load its ticks arrive after the bound did. The
  bound is now derived from the interval the child was launched with (`poll_bound`), a
  "did NOT reload" window is the child's settle time plus several of its build checks
  (`hold_window`) instead of a guessed `sleep(2.5)`, and the reload count is relative to a
  baseline taken inside the check rather than absolute — the log is this run's, so an absolute
  `== 2` was waiting for a number that would never arrive once an earlier phase had reloaded.
- **A check that named only the desktop store was still reading the operator's chats.** The
  CLI half globs `~/.config/manicode/projects` exactly the way the desktop half globs its
  stores, so the `push` block's own `todos 1/1` assertion was answered by whatever the
  operator's running session held — measured 2026-10-04, `todos 1/6`, from the very thread
  doing the work as it added todos. Both sources are named now, fixture roots both, the way the
  `json`/`bar`/`snap` block beside it already did.
- **The check that every name the code loads is defined reads a SNAPSHOT of the tree.** It
  parses every source file, so anything writing the checkout while the suite runs — an agent
  turn in this very repo, an IDE save — could leave a half-written file under the reader and
  fail a check about the build rather than about the code. The names are still resolved against
  the live namespaces: the question is still "does the code we RUN name everything it uses".
- **The pane follows the tab you are working in NOW, not the one the app's file last wrote.**
  Following the app's `workspace.activeId` was only ever as good as that file. It is written when
  the layout changes and not otherwise — measured 2026-10-04, a switch lands in 0–1 s but a
  ten-minute stretch passed with no write at all — and it names the **tab**, while the work can be
  running in a split opened from that tab, which is exactly the case this fixes. Every live channel
  the app has was tried first and none of them answers: its
  orchestrator API and its CDP bridge (both on 127.0.0.1, ports handed to the child process)
  return `401` to anything that is not the app itself, which is where their tokens are minted;
  the store has no focus column, no table and no receipt naming the tab in front of you; and
  `turn_alive_at`, the one column that looks like a heartbeat, is written ONE VALUE FOR ALL
  THREADS — three threads, three identical beats — so a liveness test built on it would call
  every tab live at once and say nothing about which one is yours. What IS per-thread is the
  ask, so `focused_thread` corrects the persisted tab with the thread most recently worked in,
  and the pane's title says so (`… · b2422dbe behind → 53d4cd37 asked 13m ago`). It is a
  correction, not a replacement, and deliberately timid: the app's own tab still wins whenever
  it is itself among the live threads, a thread asked outside `DESKTOP_LIVE_MS` is not "where
  you are", the newest ask wins when several are live, and a store written by an older app
  carries neither column and answers nothing at all — `activeId` is then the whole answer, as
  before. The window is that same `DESKTOP_LIVE_MS` because it is sized by the lag being
  covered and by a long turn alike: a turn running longer than a narrow window would otherwise
  leave the pane falling back to the tab you had left half-way through it. Its note and the `auto` chain's own note (WHICH source
  answered) are joined rather than sharing a key, which had the second silently overwrite the
  first. Teeth: with the correction disabled the fixture's pane follows the stale tab, which is
  the bug.
- **The pane inside the desktop app follows the thread you are looking at.** It did not, and the
  list it drew never changed: `-s auto` asks the CLI journal before the app's store, and the
  journal's LIVENESS was judged by the FILE's mtime — which the desktop app moves, because it
  appends its own records (`cli.feedback_button_hovered`, a note saved, a tab closed) to the
  same `log.jsonl`. So a session that had ended its turn at 11:20 still read as "just written"
  at 12:04, `auto` kept handing the pane that finished list, and the pane sat frozen on one
  session's `ALL DONE` while a thread worked in the app (measured 2026-10-04: `Goal: fix
  Freebuff Desktop black screen`, 6/6, unchanged across two tabs). The journal is now asked
  instead of statted: `journal_liveness` reads the tail for the AGENT's own turn boundary — a
  `shouldEndTurn: true` record is the agent saying it has finished and is waiting for you, so
  that chat is not the session WORKING in this directory however fresh its bytes are, and a
  `prompt` record puts it back to work with no clock at all (a bare `continue`). The same
  answer governs `followed_session_over`, so the pane's cached watcher state is dropped the
  moment the chat behind it ended rather than drawn until the state file aged out, and the
  store-vs-store freshness comparison uses the agent's clock rather than the app's appends.
  With the CLI chat handed over, the desktop store answers `auto` — and `read_desktop`
  re-reads the app's workspace on every poll, so the pane follows the active tab from then on
  (verified live: one frame per tab over a three-tab space, each with its own heading, list and
  count, and a title reading `desktop · <thread> · cli finished 1h48m → desktop`). The quiet
  window is untouched and still decides sessions that ended without saying so: this changes
  WHICH source answers, never whether a pane closes.
- **Only a request the app's receipt attributes to the user becomes `now`/`nudge` — the app's
  own prompts and its resume line do not.** The committed rows carry no `USER_PROMPT` tag; the
  app keeps that fact in a receipt instead, and ignoring it let anything the app wrote for
  itself stand in for the user's ask. Every user message the app writes carries the `input_id`
  of a `queue_items` row whose `source` is its own word for the asker (`user` for the person,
  `assistant` for an auto-run decision, a suggestion or a sponsored task, `mission-*`,
  `skill`), and whose `kind` says whether the row is a request at all (`prompt`, versus
  `skill-context` or `close-tab`). `_committed_prose` now joins that receipt and lets only a
  `user` `prompt` feed `now`/`nudge` (a store older than the receipts, with no such table,
  keeps every row a request the way it always read). Measured 2026-10-04 on a live thread whose
  newest row after the list was an auto-run step: the pane offered it as the newest ask; it now
  stays out. One prompt no receipt can mark: the app's Continue button sends
  `Continue the interrupted request from where you left off.` through the ordinary send path,
  so its row is marked exactly like a typed prompt — it is recognised by its words
  (`DESKTOP_RESUME_PROMPTS`), in the committed rows and in the live history whose
  `USER_PROMPT` tag it also carries, because a resume is not an ask and the app pressing its
  own button must not redraw the pane as if the user had asked for the work already running.
- **A finished desktop turn has a heading, a `now` and a `nudge` — read from its own rows.** The
  app commits a whole turn as one `messages` row, and the prose in it (the agent's `Goal:`
  line, the requests around it) was never read at all: only `harness_state` was, and that holds
  the LIVE turn. So a pane following the app whose agent state carried no history drew the
  row's list under `no heading — the agent owes a Goal: line` while the very row it was reading
  held the line beside its `write_todos` parts (measured 2026-10-03, on the pane following this
  session). The committed row is now read by the same rules the live history and the CLI's
  journal use — `pick_goal` picks the heading that belongs to this list, `_now_and_nudge` the
  request after it, `goal_stale_at` says when the heading is a leftover — anchored to the
  list's own position in that row. Two consequences worth knowing: a heading written one row
  back still heads the list (the agent states its goal once and re-publishes the list in later
  turns), carried with `stale heading — the list moved on` when the list's own turn opened
  after it; and the LIVE history's heading is never carried over a committed list, where it
  would head work it was not written for. The read is bounded (`COMMITTED_PROSE_ROWS`, 24 rows
  back from the list) and measured: 3.6 ms on this session's own thread, whose rows run to
  350 KB, beside 44 ms for the read it rides on. An older store whose `messages` rows carry no
  `role` answers nothing here, and a thread whose heading is further back than the bound gets
  the honest `no heading` rather than an answer about a turn nobody can see.
- **A heading is told from another heading in the SAME message.** The live history is one
  message per turn, each holding that turn's parts — so two `Goal:` lines in one message used
  to share a position, and `_newest` took the FIRST of them: the pane drew the turn's opening
  objective instead of the one it finished under. Positions are `(message, part)` pairs now,
  in the app's live history and in the committed rows both (`part.key` is already how a turn's
  last `write_todos` is found), and both readers order by them — the CLI's own positions are
  journal byte offsets, so each store orders its own and the two are never compared.
- **A desktop turn's list is its LAST `write_todos`, not its first.** The app commits a whole
  turn as ONE message row whose parts carry every call the agent made in it — and updating a
  todo list means writing it many times — so a single row holds several lists and only the last
  of them is that turn's. The store reader took whichever part SQLite happened to hand over
  (`json_each` promises no order), in practice the turn's OPENING list: measured 2026-10-03 on
  the pane following this very session, which drew a finished turn as `0/6` with a live clock
  and an arrow on a step that had long been ticked, while the row's last part said `6/6`. All
  three reads are ordered now (`m.ts DESC, m.seq DESC, part.key DESC`) — the followed thread,
  the newest-list fallback, and `live_threads`, so `fbtodo board` and the stacked-thread rows
  read the same list the pane does. Verified against the live store: the same turn now reads
  `6/6` through `json`, the pane, and the board.
- **The pane closes on the session, not on the process it was opened beside.** `freebuff
  instance exited — fbtodo pane closing` was printed the moment the pid the pane had resolved
  went away, and that rule was written before `auto` could follow the app: a pane drawing a live
  desktop thread closed mid-run because the CLI that opened it had exited (measured 2026-10-03 —
  the reported "it keeps closing even in mid run"). The close now asks the STATE on the screen
  (`session_still_live`, `followed_session_over`'s positive half): a CLI journal that moved
  inside the live window keeps the pane, and a `file:` state and the app's store are never
  called over at all — neither has a process that outlives it, which is the same reason
  `followed_session_over` only ever answers for a CLI chat. A NAS pane still closes on the
  remote session's own `instance_alive`, a finished CLI chat still lets its pane go, and a
  state whose poll just failed keeps the pane, because a broken read is not an ended session.
  Driven live on this Mac: the pane that used to die here now re-resolves onto the app's live
  thread and stays up.
- **A poll that raises becomes the frame, not the end of the pane.** The pane re-execs itself
  into whatever is on disk ("A pane keeps itself current"), and the reload probe it asks first
  can prove a tree PARSES and IMPORTS without proving its call sites still agree — so an edit
  that changes a signature in one step and its call site in the next execs a live pane into the
  between-state: measured 2026-10-03, a `TypeError` from `pane_cached_state()` took a working
  pane down mid-turn, leaving a traceback where the list had been. A failed poll now costs one
  tick: the exception is logged once per distinct message, drawn on the error row the renderer
  already has (`poll failed reading desktop — OperationalError: …`, keeping the backend and
  session so the title still names what it had been reading), and the next tick polls again —
  which is how a pane left running a broken build comes back by itself the moment that build is
  fixed. The drawing half of the tick is guarded the same way, for the same reason. Only
  `Exception` is caught, so Ctrl-C still restores the cursor and exits 130.
- **`auto` answers a directory with its OWN desktop project, never a parent's.** The store's
  `project.json` records the folder the app opened, and projects nest: the home directory is
  the one path that contains every path there is, so `auto`'s search for "the project this
  directory belongs to" found home for every repo under it and answered with the home
  project's session — measured 2026-10-03, a pane in a repository drawing the home thread's
  list, over a repository the app had never opened. The store is now picked by depth (the
  deepest project containing the directory wins, so a broad project can never shadow a narrow
  one), and `auto` narrows it further to the project that *is* the directory — mirroring
  `cli_chat_dir`, which reads `basename(cwd)` and never walks up. An explicit `-s desktop`
  was not told a directory, so it keeps the wider walk, deepest first. A directory with no
  project of its own now falls through to its own CLI chat (or to "no conversation DB
  found") instead of borrowing a parent's session.
- **A pane notices its session ended and re-resolves, instead of waiting for a reload.** The
  watcher heartbeats the state file it owns, so a cached state stays *fresh* after the chat
  behind it has finished — and the pane kept rendering that ended session until the file aged
  out or somebody reloaded it by hand. Measured 2026-10-03: a pane reading `ALL DONE` over a
  two-day-old chat whose journal had been silent since `09-30 07:12` (the `LIVE` clock beside
  it only ticked because the watcher was still writing). A cached state must now also name a
  session that is still live (`pane_cached_state` → `followed_session_over`), and when it does
  not the pane asks `snapshot` again on that same poll — which is where `auto` re-chooses its
  source, so it lands on the live desktop thread within one interval — and it keeps answering
  from `snapshot` until the file itself names a live session again (a running watcher rewrites
  it within a poll; with no watcher, one probe per poll is what this pane already does). There
  is deliberately no "already handled" memo: remembering the session the pane re-resolved AWAY
  from means trusting the still-unchanged file that names it, and the pane dropped a list only
  to pick it straight back up one frame after the switch (found by the end-to-end check below,
  which is why that check watches several polls instead of one). Only a CLI state can be
  called over: a `file:` state has no process to outlive it, and the desktop store windows its
  own threads on every read.
- **The look-only reads had the pane's freeze of their own.** `fbtodo json` and `fbtodo bar`
  serve the cached watcher state while it is fresh and describes what was asked for, and that
  rule was missing the half the pane was missing too: a watcher heartbeats the file it owns
  after the chat behind it has finished, so both answered with an ended session's list from a
  directory whose live work had moved into the app (`cached_state_ok`, same
  `followed_session_over` test, the instance resolved only once the file is otherwise an
  answer so a live session still costs no `pgrep`). Found by the self-check that drives a
  cached CLI chat into a live desktop thread through the real CLI.
- **`auto` follows the live session, not a finished chat's last list.** A chat directory keeps
  its last chat forever, and `--source auto` took it for "there is a session here" — so a pane
  in a directory whose CLI session had ended days ago rendered that session's list, frozen, with
  the live session in the desktop store never seen. Measured 2026-10-03: a pane pinned to a
  two-day-old `09-30 07:12` chat reading `ALL DONE`, while two desktop threads in the same
  project moved every minute. A journal is now *live* — and so answers `auto` — only when a
  Freebuff process is behind it or it moved inside the live window (90 min); a finished chat is
  held, and answers only when nothing fresher is here. `-s cli` is unchanged: asking for one
  source still means exactly that.
- **A list with no heading is named loudly, not drawn blank.** The heading is the agent's own
  `Goal:` line, and the framed pane drew NO heading row when one was missing — so a session
  that skipped the rule looked identical to one that had obeyed it, and the skip went
  unnoticed until the list was read carefully. A shown list with no heading now draws the
  same warning the plain renderer prints — `no heading — the agent owes a `Goal:` line` — in
  the warn yellow, on the row the heading would have taken, in both renderers. The warning is
  tied to the list: a heading silences it, and a list `finish_state` dropped (no todos) has
  nothing to head, so a cleared state never warns about a list that is not there. The gap it
  names is real and measurable: of every CLI session carrying the AGENTS.md rule, the twelve
  that wrote `Goal:` lines all also had the `freebuff-todo-pane` skill in context, while the
  two sessions that wrote none of 1464 and 49 replies carried only the AGENTS.md bullet.
- **A dropped list takes its clocks with it.** `finish_state` empties a finished list when the
  next turn (or a new session) arrives, but left `task_times` — the per-step start/stop records
  for that very list — standing in the state. The timings then had no list to describe, and the
  pane, which rebuilds them only when they are MISSING, never rebuilt them for the next list
  written into the same session; a state cleared by hand could even render the old session's
  clocks. The list and its clocks now go together, so a cleared state has nothing left to time,
  while the cross-session memories a new list projects from stay put.
- **A finished list's clock stops, instead of billing the idle hours.** The pane's `GOAL` row
  (`TOT` on the flat renderer) measured `spent` from the list's first start to *now*, so a
  list that had been done for days and a pane that had simply been left open read as a
  two-and-a-half-day job — `65h59m spent · 65h59m total` on a five-step list that ran for one
  hour. `elapsed_total_ms` now ends the span at the last stopped clock once every clocked step
  has stopped: while a step is still in flight it measures to now and keeps moving, and once
  nothing is running the number freezes at the work's real span. A fresh pane reopened on an
  old finished state shows the hour it took, not the days since.
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
  still fails the pre-flight. The parse is asked the other half of the same question too:
  `unreachable_code` fails a build whose own source proves some of it can never run — a
  statement after a `return` in the same suite, a whole `if False:` branch, a body under
  `while False:` — because that is code an import accepts happily and no run will ever
  reach, which is exactly how a path that should have executed goes missing quietly. A test
  is also folded when it COMPARES literals (`if 1 > 2:`, `while 0 == 1:`, a chained
  `0 < 1 < 0`), which is the same kind of certainty one step out; only certainties count,
  so a test with any operand that is not a literal is left alone, as is `is`/`is not`
  (identity is not a value question) and a comparison that cannot be made (`1 in 2`). Live
  shapes (`while True:` with a `break`, an ordinary `if x:`) are the controls. And the parse
  follows a GUARD, not only a literal: inside one function `if P: return` at the top means P
  is false for every line below it, so a later `if P:` can never run and a later `if not P:`
  can never take its `else`; an `assert P` makes the same promise by the other route — control
  continues past it only when P held — so the same test is already true below it and its
  negation already false. The fact is carried into any block the guard DOMINATES (the body of
  a later `if`/`with`/`try`, a handler, and a loop body), so a third look at the test inside one
  is caught too. A loop body is the one place a repeat matters, so it is entered with only the
  facts the loop cannot disturb: any fact whose names the loop ASSIGNS anywhere (target, body
  or `else`) is dropped first, so a test settled before the loop and repeated inside a
  `for`/`while` body is caught, while one the body could have moved is not. That promise is
  only made where it cannot be wrong — the guard's body must leave on every
  path (`return`/`raise`/`break`/`continue`, an `if`/`else` that both leave, a `with`
  whose body does), the test may read nothing but locals THIS function binds (a global or a
  closure cell is another pass's to move, and an attribute or a call is refused outright), and
  no statement between the two may rebind one of those names. It never crosses a scope, so no
  guard is ever asked to speak for a nested `def` or a variable it does not own; a rebinding, a
  module global, a closure cell, an attribute, a call, a guard that does not always leave, and a
  loop that assigns the name are all pinned as controls. Attribute
  names, locals and closure variables are not globals and are not asked about; a name bound
  at run time passes, because the lookup is the live module namespace. Both checks are
  pinned both ways: this checkout must pass, and a copied tree whose uncalled function names
  a global that does not exist, or whose code sits after a `return` or behind a constant
  false, must fail and name it.
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
- **The two reload clocks are settable, so a build under test is not waited out at an
  editor's speed.** A pane, the watcher and the keeper each re-exec themselves into a new
  build, and two module-level numbers decide how they wait for one: `SOURCE_SETTLE_S` (2 s — a
  save caught halfway through must not be exec'd into, because the pane is the one process
  that has to survive the person editing it) and `BUILD_CHECK_S` (1 s — how often each asks
  whether it is still the build on disk). Both are now read from `FBTODO_SOURCE_SETTLE` /
  `FBTODO_BUILD_CHECK` when `base` is imported, the way `FBTODO_PANE_SECONDS` already is. They
  are deliberately **not** in `PINNED_ENV_KEYS`: a cadence is not a location, and the same
  argument that keeps the bells' clocks out of a pane's command line applies here. The
  defaults are unchanged, and the default is what protects an editor; the caller that turns
  them down is the self-check, which drives seven reloads through COPIES of the build whose
  files it writes complete, and was paying an editor's settle window plus a whole check
  interval for each answer it already knew. It sets `0.2` / `0.1` — and since `hold_window`
  derives the hold it demands from the same two numbers the child was given, the number of
  looks a child gets at a tree it must *not* reload is unchanged, so the assertion keeps its
  teeth while the seconds go.
- **The rectangle she stands in is the PANE, not merely something pane-shaped.** The pane's own
  hint already said which of its rows its frame left blank (`FBTODO_PIP_SLACK`); that hint now decides
  the CHOICE as well as the slot, because a candidate whose blank rows are not blank — or whose half
  above them is blank too — is not a list at all, it is a hole in the window. A panel with a pane's
  proportions and a bigger rectangle therefore no longer wins it, which is the case that was
  photographed: the app's own preview panel took her and left her standing outside the fbtodo pane
  ("sometimes it still appear out of the fbtodo pane", 2026-10-08). A saved measurement is also reused
  only for the window it was taken in **at the size it was taken at** — a fit is window-relative, so a
  window that only MOVED still has its pane where it was, while a RESIZED one does not, and she waits
  for the next capture (a fraction of a second) rather than standing at the old pane's points. The
  suite pins both ends against a synthetic window: a real pane beside a taller blank panel, where the
  answer must be the pane's own two borders, with her inside them.

### Removed
- **`FBTODO_FB_MARKER` is no longer carried into a pane.** It named the file the watcher read
  to find live sessions on another host, and the remote source that wrote those markers is
  gone — nothing has read the setting since, so it rode in every pane command line as a value
  with no reader, and the pages that listed it still described marker-based session discovery
  that no longer happens. The pin is now the state root, the tmux server and the six watch
  paths (`base.PINNED_ENV_KEYS`), and the two launchers, `docs/SETTINGS.md` and
  `docs/INTERNALS.md` name the same set. A pane whose recorded command still carries the dead
  key is not called stale for it: only the values this build hands on are compared, so an
  older pin is read the way it always was. The pages that still counted a remote list among a
  pane's answers (`docs/SOURCES.md`, `docs/INTERNALS.md`) say what a pane follows now: the
  cwd's journal and one thread, both local.

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
