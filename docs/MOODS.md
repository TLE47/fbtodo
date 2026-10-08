# Her moods

buffy-chan is in two places at once: six ASCII cells on the pane's top border, and a picture in a
window of her own floating over that pane. One source of moods serves both, or the reader learns two
characters that disagree.

- The **pane** decides the mood: `buffy_mood()` in `render.py`, from the same facts the rows under her
  are drawn from, in one precedence order.
- The pane **publishes** that word to `~/.cache/fbtodo/pip-mood` (`FBTODO_PIP_MOOD`), rewritten only
  when it changes, and again if the file has gone missing.
- Her **window** reads it twice a second and wears the pose that word means: `moodCycles` in
  `scripts/buffy-pip.swift`, one row per mood, with the frame numbers `assets/buffy` gives them
  (`20` is `20.png`). The same word chooses the SHAPE of the balloon she says her lines in
  (`bubbleShapes`, one row per mood as well — see *Her balloon* below).

She animates inside a mood rather than one frozen picture, at the mood's own pace, and **holds
completely still** for the three moods that are over by the time they are drawn — the pane holds
still on those faces too (`BUFFY_CYCLE`). `--moods` prints the table; `fbtodo pip status` and her own
log name the mood she is in.

## When she shows which emotion

Precedence runs from the loudest fact to the quietest, so a failure outranks a finished list and an
unfinished step outranks a stale heading. The conditions are `buffy_mood`'s, in its own order:

| mood | when she shows it (the pane's condition) | what she wears (frames) | pace | pane's face |
|---|---|---|---|---|
| `error` | `state["error"]` is set — a failure is about to be printed under her | 11 — hands to her face | **hold** | `~(x_x)` |
| `nudge` | `state["nudge"]` is non-empty — the nudge row, "rewrite the list, then continue" | 7 — the cross | **hold** | `~(>_<)` |
| `done` | a list whose drawn steps are all ticked (`done >= total`, `total > 0`) | 12, 18, 9, 1, 15, 14 — arms up, the cards, the cheer, the thumbs up, the heart | 900ms | `~(^o^)` cycle |
| `none` | no list at all — no group has a todo | 19, 10, 5, 6 — puzzled, peering out from behind the frame's edge, waving hello, sunglasses on | 1800ms | `~(o_O)` cycle |
| `wait` | the turn is OVER — `turn_ended` and no new turn running: "turn ended — waiting for you", the state the ask and stall watches exist for, and the ball is in the owner's court | 2 — hugging the cat, patient | **hold** | `~(^.^)` |
| `idle` | the pane's own idle threshold passed (`idle_s > stale_after_s`) — a quiet session | 3, 2, 16, 13 — dozing, hugging the cat, standing there, blushing | 3000ms | `~(-_-)` cycle |
| `stale` | `goal_stale_note(state)` — a heading left over from an earlier turn | 17 — sitting with it | **hold** | `~(._.)` |
| `work` | anything else: a list with an unfinished step (the ordinary working frame) | 20, 17, 8, 4 — at the laptop, thinking it over, working out of the box, smug at the desk | 2500ms | `~(o_o)` cycle |

**All twenty of her frames are worn by some mood.** The four that move carry the extra poses and the
four that hold keep the one picture each is; the suite checks the UNION of the table against the art
on disk, so a frame no mood names — art that exists and is never on screen — fails the run rather than
quietly sitting there. (The owner, 2026-10-07: "trying to use all of buffy-chan images".)

`wait` is the one mood that is about the *owner* rather than the list: it arrives at the end of
every turn and stays until the next message, which is exactly what "the agent is waiting on me"
means, and she HOLDS it — nothing is running, so there is nothing to be seen fidgeting through. It
sits below `done` on purpose (a finished list still celebrates: the finished-task bell rings on that
and it is the news) and above `none` (a turn that ended with no list at all is still *waiting for
you*, not *nothing to point at*). `turn_running` is asked alongside `turn_ended` because both come
from the same transcript: the new turn can arrive a moment before the old flag clears, and waiting
must not be shown over work that is already under way.

Everything else about her is in [SETTINGS.md](SETTINGS.md): where the note is written
(`FBTODO_PIP_MOOD`), where the frames come from (`FBTODO_BUFFY_DIR`), how a mood with no room for her
steps out (`FBTODO_PIP_ROWS`).

## Her balloon

When she says something, she says it in a balloon built **around the line itself** rather than in a
card the size of her square: the words are measured first (`bubbleLayout`), and the cloud is the ring
of scallops that wraps that measurement — so a longer line is a wider cloud, and one that has to wrap
is a taller one that sits lower. The MOOD decides what those scallops are, which is what makes the
same words read differently in every state:

| mood | her balloon |
|---|---|
| `work` | a soft pink cloud with two little stars, and a puff tail |
| `done` | the roundest, pinkest one — four stars, a puff tail |
| `idle` | a lilac cloud with no stars and three DETACHED dots for a tail: a thought balloon |
| `none` | amber, one star, thought dots |
| `wait` | teal, one star, thought dots (she is only waiting) |
| `error` | the same puffs with eleven SPIKES around them, in red: a shock balloon |
| `nudge` | seven spikes, orange |
| `stale` | grey, flat, no stars, thought dots — the dullest one she has |
| *surprise* | not a mood: violet, spiked AND spangled AND dotted, the whole table at once |

...and now and then the balloon is a **surprise** instead: `FBTODO_PIP_SURPRISE` (`--surprise`, 12% by
default, `0` never, `1` always) is the chance that a mood change gets one — words that belong to no mood
("boo!", "still cute?", "*pomf*") in the one balloon that is all of them at once, and her log names it
(`says=boo! mood=done surprise=yes pose=15`). A surprise is a POSE as well as a word: the sticker is one
her mood would never have shown her (the pool is every frame NOT in that mood's cycle), it arrives as a CUT
— the one cut she makes on purpose, because a surprise you can see coming is not one — and it dissolves
back into the step of her cycle she was on, which held still while the surprise was on. It is nobody's mood,
so it is deliberately not a row of
`bubbleShapes`; `--art-surprise` renders one on demand rather than waiting for luck. A surprise is
allowed past `FBTODO_PIP_BUBBLE_GAP` on a mood change, because a line nobody expected is the whole of
it.

She does not only speak when her mood moves. A mood-change line was one line in twenty minutes, so the
bubble is on a CLOCK too: whenever the line she is wearing has come down and she has been quiet for
`bubble-gap`, she says another one. The cadence is that same knob reused — the silence after a line is
how often she may break it — and the line is the CURRENT mood's (`bubbleLine(mood, …)`), so the clock
cannot put words in her mouth she is not feeling. She stays quiet while a line is up, while the finished
burst is running, and with `--bubble 0`. Her log tells the two apart: a mood change prints
`says=all done! mood=done balloon=left` and the clock adds `on=tick`.

Every mood the pane can put her in carries a POOL (four lines at least, six for `work` and `idle`, because
those two are where whole turns are spent), and one line is shared on purpose: "one more step" is `work`'s,
reused by `nudge`, since a nudge is her asking for exactly that. The pools are what keeps a talking
companion from reading as a tape loop; the suite pins the floor, the no-duplicates rule and the one shared
line.

Two more tables are chosen from at the same moment, one row per mood each, because both are FLAVOURS rather
than moods: `--cringe` (default 0.35) makes the line one of her dorky ones — "notice me, senpai", "*nuzzles
the keyboard*", "i didn't break it, it was already like that" — and `--sentence` (default 0.15) makes it a
whole short clause instead of a tag ("this list is from a turn that already ended, ugh"). `pickLine` is the
one place a line is chosen, and BOTH places that speak go through it, so the flavour fits the mood whichever
roll won and whichever path asked. Her log says which table it came from: `kind=say`, `kind=cringe`,
`kind=sentence`.

None of those tables repeats itself: she remembers her last six lines (`recentDepth`), every table picks
through that memory, and both places that speak write back to it — so a mood she stays in can go a whole
cycle without saying the same thing twice. A pool with nothing fresh left falls back to "not the line just
said", and then to the pool itself, because repeating a line is better than going silent.

What none of them may be is GENERIC. A line is about the thing she is looking at — the step, the tick box,
the leftovers, the cat — and it would be absurd coming out of a support bot: no reassurance, no "i'm here",
no "let me know". The suite holds that denylist (`GENERIC_LINES`) across all three tables, so a later
"friendlier" sentence fails the build instead of quietly turning her back into an assistant. The lines FILE
is the escape hatch for taste: it still sets a mood's own pool (`work<TAB>chop chop`), the flavours stay
built-in, and a mood the file empties stays SILENT — flavours included, or "she says nothing in this one"
would be a lie.

## Retuning what she says — the same deal, a file

Her lines are a file her window re-reads while it runs, exactly as the moods are:

```sh
fbtodo-pip --lines > ~/.cache/fbtodo/pip-lines     # the table, in the shape the file takes
$EDITOR ~/.cache/fbtodo/pip-lines                  # work<TAB>chop chop   ← a mood and the words
```

One row per line she may say: the mood, a TAB, the words (a TAB rather than a space, so a line can be a
sentence). `#` starts a comment. A mood the file does not name keeps the built-in lines, so adding a joke
cannot quietly delete the other seven; EVERY row for a mood is a line she may say, in the file's order; a
row with no words CLEARS that mood's lines, which is how "she says nothing in this one" is written down;
and `surprise` is a key here too, for the lines that belong to no mood. The path is
`FBTODO_PIP_LINES` (`--lines-file`), the change is noticed on her next pin tick, and her log says so —
`buffy-pip lines=/Users/you/.cache/fbtodo/pip-lines moods=2`.

## Every knob in a file, and a page of every mood

Her window's knobs are a file too — `~/.cache/fbtodo/pip-tune`, written for you by `fbtodo pip tune`
(see [SETTINGS.md](SETTINGS.md)): `transition`, `pose-min`, `bubble`, `bubble-gap`, `surprise`, `pop`,
`fit-every`, `fit-retry`, `side`, `tick`, with the usual precedence (a flag beats the environment, the
environment beats the file, the file beats the default). `fbtodo pip tune` shows every knob with the
value she would run with and WHERE it came from, so "my edit did nothing" has an answer, and a knob set
there is worn without a restart for everything except the window's own size.

And the whole cast can be looked at in one place — every mood's balloon, every mood's pose, the surprise
and the frames on disk — as one self-contained page:

```sh
scripts/buffy-sheet.py --out ~/fb-moods.html --no-frames   # or: fbtodo pip sheet --out ... --no-frames
scripts/buffy-sheet.py --json                              # the same, machine-readable
```

Two invariants survive every shape: nothing it draws reaches past her own square (every circle goes
through `puff`, which clips), so a line can never land over the pane's list; and the balloon is built
from the line's own rect, so it can never be a size of its own. `--art-say` renders one without a
window and the record line measures it — `balloon=169x74 r=16 box=29,135 spikes=0` — which is how the
wrap-around-text part is checked as a number and not by squinting at a picture. The line comes up on a
mood change, at most once every `FBTODO_PIP_BUBBLE_GAP` seconds, for `FBTODO_PIP_BUBBLE` seconds, and it
is never the line she just said.

## Seeing one, and testing one

Each row of the table can be checked without a window, a pane or a mood:

```sh
fbtodo-pip --moods                                    # the table itself, one line per mood
fbtodo-pip --art /tmp/work.png --art-size 192 --art-mood work    # the pose it wears
fbtodo-pip --art /tmp/done.png --art-size 192 --art-mood done    # ...and the next one
fbtodo-pip --art /tmp/balloon.png --art-size 192 --art-mood error --art-say "oops"
fbtodo-pip --art /tmp/shock.png --art-size 192 --art-mood done --art-say "boo!" --art-surprise
scripts/buffy-artcheck.py /tmp/work.png /tmp/done.png            # they are different pictures
```

Live, on the machine with her window up, a mood can be worn on demand by writing the word the pane
would have written — the file is the whole interface, so that is the same path the pane uses:

```sh
echo done > ~/.cache/fbtodo/pip-mood      # she celebrates within half a second
echo work > ~/.cache/fbtodo/pip-mood      # ...and is back at the laptop
tail -2 ~/.cache/fbtodo/pip.log           # buffy-pip mood=done frames=12,18,9,1 step=900ms
```

The states that produce each mood are exercised in `scripts/fbtodo-selfcheck.py` (the "buffy-chan's
mood reaches her WINDOW" check), which asks every mood in the pane's vocabulary whether the file it
just wrote says the same word, then asks her window's table whether it has a pose for that word and
whether the frames it names exist — so the two ends of this feature cannot drift apart.

## Retuning the poses and the paces — a file, not a build

Her table is DATA, in a file her window re-reads while it runs:

```sh
fbtodo-pip --moods > ~/.cache/fbtodo/pip-moods      # the table, in the shape the file takes
$EDITOR ~/.cache/fbtodo/pip-moods                   # work 6,20 1200   ← pose, pose, pace
```

One row per mood: `name frames ms`, whitespace-separated, `frames` a comma-separated list of the
numbers `assets/buffy` gives them and `ms` the pace — `0` HOLDS that pose, which is what a moment
already over wants. The path is `FBTODO_PIP_MOODS` (or `--moods-file`), and her window notices a
change on its next half-second tick: the mood she is in is re-worn with the new numbers and a line
says so —

```
buffy-pip moods=/Users/you/.cache/fbtodo/pip-moods rows=2 moods-on-disk=20
```

Three rules make a half-edited file safe, and they are the reason this can be a live knob at all:

- a file that names only SOME moods is an **override** — every mood it does not mention keeps its
  built-in row, so retuning `work` cannot quietly drop the six you did not think about;
- a line naming a mood the pane cannot produce, or with the wrong number of fields, is **ignored**;
- a row whose frames are all past the end of the art on disk is ignored **whole**, because losing a
  pose is worse than ignoring a typo — a mood the pane can put her in must always have one.

`--moods` prints the EFFECTIVE table, so a redirect seeds a complete, valid file to edit. Delete the
file and the built-in table comes back (a missing file is not an error — and neither is a rubbish
one). `moodCycles` in `scripts/buffy-pip.swift` remains the default the file overrides, and the
suite checks both ends: every mood the pane can produce has a row, every frame a row names is on
disk, and a half-edited file keeps the moods it never mentions.

Changing pose is a TRANSITION, not a cut: her window cross-dissolves the picture she is leaving over
the one she is arriving at, over `FBTODO_PIP_TRANSITION` (`--transition`, 10 s, and any smaller value
is raised to 10 s) — the same number that eases her from one slot to the next. ARRIVING is not a fade:
her window is ordered in at FULL opacity, and the fade is only the way off the screen (so the two ends
are not the same event told twice). It is easier-in-and-out rather than linear, because a dissolve that starts at
full speed reads as a flicker, and `--transition 0` is the old behaviour exactly: every one of them a
hard cut. The clock that drives it runs only while a dissolve is in flight, so a still pose — the held
moods, and any mood at all while nothing changes — costs nothing.

EVERY pose takes that whole length, including the ones that move on their own: `done` steps every
900 ms, so at a 10 s dissolve it is dissolving for most of its cycle and reads as a slow morph rather
than a snap between stickers — that is the length the owner asked for ("make transition between pose
take 10s"), and `--transition 0` is the way back to a cut. A mood that HOLDS (`wait`, `error`, `nudge`,
`stale`) is where it is easiest to watch: the pose settles into the new one over ten seconds and then
stays there. To see the slow dissolve again after it has settled, change the mood — the dissolve is per
pose, not per session.

## She stands IN the pane, and the log says which pose, which side

Her window is placed by a SCREENSHOT of the app's window, so "which rectangle is the fbtodo pane" is a
question about pixels rather than about a number the app would tell anyone. The pane answers it from its
own end: every frame whose numbers change writes `~/cache/…/pip-slack` — its rows, its columns, and which
of those rows the frame left blank — and her window uses that hint twice. It says WHERE to stand (the
middle of the blank rows, clamped inside the pane's own border) and, since 2026-10-08, it decides WHICH
rectangle is the pane at all: a candidate whose blank rows are not blank — or whose rows above them are
blank too — is not a list, it is a hole in the window, and that is how the app's preview panel (a taller,
pane-shaped, entirely blank column) stopped winning the choice and leaving her outside the fbtodo pane.

The other half of that rule is the saved measurement: a fit is in the WINDOW's own points, so a window
that only MOVED still has its pane exactly where it was, while a window that was RESIZED (the app opens
its preview panel, you drag an edge) does not — and a stale fit there is her standing at the old pane's
coordinates. So the saved placement is reused only for the window it was measured in, at the size it was
measured at; a size change costs her one capture (a fraction of a second) and she waits rather than
standing somewhere she does not belong. If she is ever outside the pane, the log says why: `fit=refused
…` is a capture with no pane-shaped thing in it, `fit=no screenshot of window N` is a refused capture,
`hidden=waiting for the pane` is a pane with no room for her.

When a placement is wrong — or you just want to know where she decided to stand — ask the verb that
measured it:

```sh
fbtodo pip doctor                 # one capture, explained: candidates, reasons, the pane, her square
fbtodo pip doctor --watch 60      # ...or watch it for a minute and hear about every CHANGE
fbtodo-pip --shot /tmp/shot.png                 # ...or keep the capture it would have measured
fbtodo-pip --fit /tmp/shot.png --fit-width 1228 --trace --slack ~/.cache/fbtodo/pip-slack
#                                  ^ the same explanation over a screenshot on disk, no screen involved
```

**`--watch [SECONDS]` is the answer to a hop that comes and goes.** A single capture catches an answer
and never the jump, so the same measurement is taken again every second for that long (bare `--watch` is
a minute) and only what changes is printed — the pane rectangle, her square in it, or the window they
were read out of, with the wall clock and BOTH readings:

```
watch=0.1s at=02:23:25 start window=71608 pid=99995 owner=Freebuff 1228x690pt onscreen=true pane=1907,125-2405,1374 anchor=982,494 size=192 inside=yes slot=953,569 249x84
watch=5.8s at=02:23:31 changed=anchor pane=1907,125-2405,1374 anchor=982,448 size=192 inside=yes slot=953,434 249x219 was pane=1907,125-2405,1374 anchor=982,494 size=192 inside=yes slot=953,569 249x84
 watch=10.8s at=02:23:36 samples=14 changes=1 outside=0 refused=0 no-pane=0 pane=1907,… anchor=982,448 …
```

The summary counts the samples, the changes, and the two failures (`refused=`, `no-pane=`) — each of
those is printed once when it STARTS rather than once a second, because a line a second would bury the
change the verb is about. It exits **0** when every sample found her inside the pane, **1** when any
sample did not — or when a capture came back with no pane-shaped thing in it, the single-shot verb's own
answer to that — and **69** when no capture came back at all (permission, or six timeouts), so a watch is
a gate as well as a report: an intermittent hop makes the command fail.
(`0` with `changes=2` is a resize you did; `1` is the bug.) The duration has to sit between 0 and 3600s,
and a value that is not one is a usage error rather than a default — `--watch 0` would be a watch that
reports nothing. A file can be watched too (`--fit <png> --watch 30`, with no screen at all), which is
how the suite checks the reporting: the picture is re-read every tick, so a hint rewritten mid-watch is a
placement that moved while the screen stood still.

**`--json` and `--stop-on-change` make the watch something a program can use.** `--json` prints one object per
sample on stdout (`t`, `elapsed_s`, `state`, `pane` as `[left, top, right, bottom]` in window points, `anchor`,
`size`, `inside`, `changed`, `note`, `window`), with the summary on stderr, so a hop can be charted or diffed
across runs. `--stop-on-change` ends the watch at the first accepted change and files the evidence under
`~/.cache/fbtodo/pip-hops/<stamp>/`: both captures, both explanations, and `readings.json` with the two readings
and the hint they were measured with. A half-drawn frame is HELD, not believed: a reading that disagrees with the
saved pane by more than a border (8pt) waits for the next reading to agree, so a hop needs two readings in a row.

Her pace and her talk are two knobs: `--pose-hold` (default 8s) is the least time a mood's pose stays up before
it moves on (the celebration burst is not held), and `--bubble-seconds` (default 9) is how long a line stays up.
How OFTEN she talks is `--bubble-gap` (default 12s), the silence after a line — the same knob that gates a
mood-change line, reused as the clock's cadence.
`--pose-min` (default 2s, was 10) is the floor under the DISSOLVE, and it has to stay UNDER `--pose-hold`: the
fade is `min(transition, max(the pose's own step, pose-min))`, so a floor above the step leaves her dissolving
longer than the pose she is arriving at — mid-morph for the whole cycle rather than standing on a frame.
All of them are in `fbtodo pip tune`, and all are worn without a restart.

`--shot` needs her window DOWN (`fbtodo pip stop` then `fbtodo pip start`): a second pip process cannot
take a capture while she is up, which is also why `pip doctor` measures with her stopped and puts her
back. Both one-shot verbs take their capture from inside the running app — from the top level, before
`NSApplication` is up, every capture was refused (measured 2026-10-08: `--shot` exited 69 on every run
from a shell while its own scheduled capture measured the pane perfectly).

It prints the window it measured (number, pid, owner, rect, onscreen), the hint the pane published, every
long column in the capture with its ink run, every PAIR of those columns with the reason it was kept or
thrown away, the pane that won, the blank band she was given and `anchor=`/`inside=` — so "she is outside
the pane" is answered by one command instead of by reading the log around a line that only reports the
answer. It exits 0 when she would be inside the pane, 1 when the pixels say otherwise, 66 with no window
to look at and 69 when a capture was refused. Two wrinkles are worth knowing: a second pip process cannot
take a capture while her window is up, so the verb measures with her down and puts her back (it says so
on stderr), and `--fit <png> --trace` runs the same explanation over a screenshot on disk — which is how
the suite checks it, and how you check one you kept. Her window's own log shares stdout with those lines
(the one-shot verbs run the app for a moment, so her startup banner is printed too): the answer is the
`window=`, `capture=`, `pane (pt)=`, `fit=` and `anchor=` lines, and `pip doctor | grep -v '^buffy-pip'`
is the diagnosis alone.

Two more lines in the same log make the art checkable without watching her window:

```
buffy-pip says=boo! mood=work surprise=yes pose=1 balloon=right   # the pose she borrowed, and her side
buffy-pip surprise=over back=pose=20                             # ...and the pose she came back to
```

`balloon=` alternates `right`/`left` on every line, which is the alternation this file explains; `pose=`
is the borrowed sticker and `back=pose=` is the one from her mood's own cycle she returns to, so the
round trip — a sticker from outside the mood, then back — is one line each way. To check the geometry
with no screen involved, `fbtodo-pip --fit <png> [--fit-width PT] [--fit-verbose]` runs the same
detection over a screenshot on disk and prints `pane=`, `anchor=` and `inside=yes|no`; it exits 1 when
she is not inside the pane it found, which is how the suite pins this against a synthetic window.
