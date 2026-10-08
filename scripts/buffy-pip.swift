// buffy-chan, floating over everything, the way a video sits in a picture-in-picture corner.
//
// The pane can only ever draw her in CELLS — a terminal has no other way to show a picture (see
// `buffy_pixels`) — and a cell is worth two pixels, which is why she reads as a blob on a short
// pane. This is the other half of that: the same frames the pane averages down, drawn at their
// real resolution in a window of her own, frameless and always on top.
//
//     scripts/buffy-pip.swift            built by scripts/buffy-pip.sh, or:
//     swiftc -O scripts/buffy-pip.swift -o ~/.cache/fbtodo/bin/fbtodo-pip
//     fbtodo-pip [--frames DIR] [--side PT] [--tick MS] [--free PATH] [--pin PATH] [--host NAME]
//                [--pids LIST] [--mood PATH] [--moods-file PATH] [--sharp 0|1]
//                [--fit-every S] [--fit-retry S] [--transition MS]
//     fbtodo-pip --moods                 # her moods, her poses and their pace, one line each —
//                                        # the same shape the moods file takes, so redirecting this
//                                        # into `~/.cache/fbtodo/pip-moods` and editing a number is
//                                        # how a pace or a pose is retuned (picked up live).
//     fbtodo-pip --art <png> [--art-size PT] [--art-mood NAME]   # one frame, drawn, no window
//
// She is STUCK IN THE MIDDLE by default, and stays there: a picture-in-picture window that drifts
// is a window you have to tidy. A switch FILE releases her — the same shape the notify kit uses
// for the phone, so the pane can own the button and this program only has to read it. While the
// file exists she is draggable and stays where she is put; when it goes away she is snapped back
// and pinned. `fbtodo pip free` and `fbtodo pip stuck` write it, and the pane's own key flips it
// (see the pane's title chip).
//
// STUCK MEANS THE MIDDLE OF THE PANE'S OWN WINDOW — the place the drawn buffy-chan stood, not the
// middle of the screen. Which window that is, is the one question this program cannot answer from
// its own process: it is started detached, so by the time it looks, its parent is `launchd`. The
// answer therefore arrives as arguments, read ONCE by `fbtodo pip start` while it is still a child
// of the pane: `--pids` is that process's ancestry (the shell, then the app hosting the terminal —
// iTerm2, the desktop app, anything), and a window whose OWNER PID is on that chain is her host.
// `--host NAME` is the fallback for a caller that has no ancestry to offer.
//
// AND SHE FINDS THE PANE HERSELF. "The middle of the window" was only ever right when the pane IS
// the middle of its window, and on the desktop app it is not (measured 2026-10-07: the window keeps
// a chat and a file list on the left and the terminal panel on the right, and the owner's words for
// the result were "it's in the middle of my screen, not in the fbtodo pane"). Nothing outside the
// app can be TOLD the panel's rectangle — its UI geometry lives in its renderer, behind a launch
// token it strips on purpose, and its accessibility tree stops at four nested groups. So the pane's
// own drawing is read back instead: `screencapture` the host window, and find the one thing in it
// that is a TALL RECTANGLE OF THIN GLYPHS — two solid vertical lines of border ink, far apart, with
// text rows and blank rows between them. That rectangle IS the pane (the frame is drawn across the
// pane's whole grid), and the last blank block above the state row is the space the ASCII picture
// used to stand in. She is put there, re-measured every few seconds and after any window change, so
// resizing the window or the pane moves her with it.
//
// A pin you TAUGHT still wins over all of it: drag her while she is free, pin her again, and the
// offset you left her at is what she follows (see above) — `fbtodo pip forget` hands her back to
// the drawing-based fit.
//
// She leaves on Escape or a right-click. The app is an accessory, so she takes no Dock tile and
// never steals focus from what you are typing.
//
//     fbtodo-pip --fit <png> [--fit-width PT] [--side PT]   # just the detection, for a test

import AppKit
import CoreImage
import ImageIO
import ScreenCaptureKit

let args = Array(CommandLine.arguments.dropFirst())

/// One option, either `--name value` or `--name=value`. `nil` when it was not given: the
/// defaults below are the program's, and an empty string is not a way to ask for nothing.
func option(_ name: String) -> String? {
    for (i, arg) in args.enumerated() {
        if arg == name, i + 1 < args.count { return args[i + 1] }
        if arg.hasPrefix(name + "=") { return String(arg.dropFirst(name.count + 1)) }
    }
    return nil
}

let homeDir = FileManager.default.homeDirectoryForCurrentUser
// HER FRAMES, at the resolution she is drawn at.  `assets/buffy` holds the 128px frames the PANE
// reads (buffy-thumbs.py averages them into cells); `assets/buffy/pip` holds the same twenty poses at
// 384px out of the same 900px stickers, and 384 is exactly the pixel size she is drawn at on this
// display (192pt at 2x) — so her window shows her with NO resampling at all, which is the whole
// answer to "still a bit low resolution" (2026-10-07).  The small set is the fallback: a checkout
// that has not regenerated the large one still gets her, only softer.
let smallDir = homeDir.appendingPathComponent("Projects/fbtodo/assets/buffy").path
let hiDir = homeDir.appendingPathComponent("Projects/fbtodo/assets/buffy/pip").path
let defaultDir = FileManager.default.fileExists(atPath: hiDir + "/01.png") ? hiDir : smallDir
let defaultFree = homeDir.appendingPathComponent(".cache/fbtodo/pip-free").path
let frameDir = ((option("--frames") ?? defaultDir) as NSString).expandingTildeInPath
// 192, not 190: her own size is snapped DOWN to a whole number of ART pixels (see `artUnit`) and the
// unit is 64pt, so a default of 190 is a default of 128 — two art pixels up where three fit.  192 is
// exactly three of them, which is the size the pane's own 200 asks for anyway.
// EVERY KNOB HER WINDOW READS, in a FILE: `--tune-file PATH`, `FBTODO_PIP_TUNE`, or the cache beside her
// mood.  One `name value` per row (`#` starts a comment, blank rows are ignored), the names being the
// flags' own short names — and the PRECEDENCE is the one every program has: a command-line flag beats an
// environment variable, an environment variable beats this file, and this file beats the built-in default.
// The file is the only one of the four that can change while she runs, which is the whole point of it: a
// knob set from the shell should not mean exporting a variable into whatever shell happened to start her
// window (`fbtodo pip tune`, which reads and writes exactly this file — see `--tune`).  The knobs that are
// the window's own SIZE rather than its behaviour (`side`, `tick`) are read once at launch; every other one
// is re-read on her pin tick and worn without a restart, and `tuneKnobs` says which is which.
var tuneTable: [String: String] = [:]
var tuneFrom: [String: String] = [:]
let tuneFile = ((option("--tune-file")
                 ?? ProcessInfo.processInfo.environment["FBTODO_PIP_TUNE"]
                 ?? homeDir.appendingPathComponent(".cache/fbtodo/pip-tune").path)
                as NSString).expandingTildeInPath
var tuneStamp: String? = nil

/// The knobs `--tune` prints and `fbtodo pip tune` sets, in the order they are shown: the name, the flag
/// that sets it, the environment variable it stands for, the built-in default, and whether her window can
/// wear a change WHILE SHE RUNS (`false` is her own geometry, read at launch).
let tuneKnobs: [(name: String, flag: String, env: String, off: String, live: Bool)] = [
    ("transition", "--transition", "FBTODO_PIP_TRANSITION", "10000", true),
    ("pose-min", "--pose-min", "FBTODO_PIP_POSE_MIN", "2", true),
    ("pose-hold", "--pose-hold", "FBTODO_PIP_POSE_HOLD", "8", true),
    ("bubble", "--bubble", "FBTODO_PIP_BUBBLE", "1", true),
    ("bubble-gap", "--bubble-gap", "FBTODO_PIP_BUBBLE_GAP", "12", true),
    ("bubble-seconds", "--bubble-seconds", "FBTODO_PIP_BUBBLE_SECONDS", "9", true),
    ("surprise", "--surprise", "FBTODO_PIP_SURPRISE", "0.12", true),
    ("cringe", "--cringe", "FBTODO_PIP_CRINGE", "0.35", true),
    ("sentence", "--sentence", "FBTODO_PIP_SENTENCE", "0.15", true),
    ("pop", "--pop", "FBTODO_PIP_POP", "0.34", true),
    ("fit-every", "--fit-every", "FBTODO_PIP_FIT_EVERY", "4", true),
    ("fit-retry", "--fit-retry", "FBTODO_PIP_FIT_RETRY", "1.5", true),
    ("side", "--side", "FBTODO_PIP_SIDE", "192", false),
    ("tick", "--tick", "FBTODO_PIP_TICK", "900", false),
]

/// Read the tune file into `tuneTable` (see `tuneFile`): `name value`, whitespace between them, `#` and
/// blank rows ignored, a name that is not one of the knobs carried through as nothing at all.
func readTune() -> [String: String] {
    var table: [String: String] = [:]
    guard let text = try? String(contentsOfFile: tuneFile, encoding: .utf8) else { return table }
    for raw in text.split(separator: "\n", omittingEmptySubsequences: false) {
        let row = raw.prefix { $0 != "#" }
        let parts = row.split(whereSeparator: { " \t".contains($0) }).map(String.init)
        guard parts.count == 2, tuneKnobs.contains(where: { $0.name == parts[0].lowercased() }) else {
            continue
        }
        table[parts[0].lowercased()] = parts[1]
    }
    return table
}

/// One knob's value, WITH where it came from: the flag, then the environment, then the tune file, then the
/// built-in default in `tuneKnobs` — and the source is recorded in `tuneFrom`, which is what lets `--tune`
/// answer the question a knob you cannot find the setting for really asks.
func tuneValue(_ name: String) -> String {
    let knob = tuneKnobs.first { $0.name == name }
    if let flag = knob?.flag, let value = option(flag) { tuneFrom[name] = "cli"; return value }
    if let env = knob?.env, let value = ProcessInfo.processInfo.environment[env], !value.isEmpty {
        tuneFrom[name] = "env"
        return value
    }
    if let value = tuneTable[name] { tuneFrom[name] = "file"; return value }
    tuneFrom[name] = "default"
    return knob?.off ?? ""
}

/// A knob that is a number, floored — the one thing every numeric knob does with its value.
func tuneNumber(_ name: String, _ fallback: Double, _ floor: Double = 0) -> Double {
    max(floor, Double(tuneValue(name)) ?? fallback)
}

/// Put the file on the knobs that can change while she runs.  Called once at launch and again on every pin
/// tick that finds the file changed; a knob whose value came from a flag or the environment is NOT moved by
/// the file, because that is the precedence — and it is also how one launch can be experimented with
/// without touching what the next launch will read.  Returns how many knobs the FILE set, for the line
/// that says so in her log.
@discardableResult
func applyTune() -> Int {
    tuneTable = readTune()
    let ms = max(0.0, Double(tuneValue("transition")) ?? 10000)
    transitionS = ms <= 0 ? 0 : max(transitionFloorS, ms / 1000)   // `0` is the hard cut, unfloored
    poseFloorS = tuneNumber("pose-min", 2)
    poseHoldS = tuneNumber("pose-hold", 8.0)
    bubbleOn = !["0", "off", "no", "false", "none"].contains(tuneValue("bubble").lowercased())
    bubbleGapS = tuneNumber("bubble-gap", 12)
    bubbleSeconds = tuneNumber("bubble-seconds", 9, 1)
    surpriseChance = min(1, max(0, Double(tuneValue("surprise")) ?? 0.12))
    cringeChance = min(1, max(0, Double(tuneValue("cringe")) ?? 0.35))
    sentenceChance = min(1, max(0, Double(tuneValue("sentence")) ?? 0.15))
    bubblePopS = tuneNumber("pop", 0.34)
    fitSeconds = tuneNumber("fit-every", 4, 1)
    fitRetrySeconds = tuneNumber("fit-retry", 1.5, 0.5)
    return tuneFrom.values.filter { $0 == "file" }.count
}

let side = max(48.0, Double(tuneValue("side")) ?? 192)
let tickMs = tuneNumber("tick", 900, 50)
let freeFile = ((option("--free") ?? defaultFree) as NSString).expandingTildeInPath
let defaultPin = homeDir.appendingPathComponent(".cache/fbtodo/pip-pin").path
let pinFile = ((option("--pin") ?? defaultPin) as NSString).expandingTildeInPath
// The pane's own numbers, published by the pane itself: its row count and which rows its frame left
// blank (see `note_pane_slack` in render.py). A screenshot gives the pane's PIXELS; this gives the
// one thing pixels cannot — how many rows the grid has, and which of them are empty.
let slackFile = ((option("--slack")
                  ?? homeDir.appendingPathComponent(".cache/fbtodo/pip-slack").path)
                 as NSString).expandingTildeInPath
// WHERE she is stuck: the middle of the window the PANE lives in. `--pids` is the pane's own
// process ancestry, which is what identifies that window on any machine; `--host` names the app
// for a caller that could not work one out (and falls back to the middle of the screen when
// neither finds a window).
let hostOwner = option("--host") ?? "Freebuff"
let hostPids = Set((option("--pids") ?? "").split(separator: ",").compactMap { Int($0) })
// How often the pane is re-measured from a screenshot of that window, and how much of the window's
// height has to be a blank block before it counts as the space she stands in.
var fitSeconds = 4.0                     // ...resolved by `applyTune`, which the tick re-runs
// ...and the clock for a measurement that FOUND NOTHING — a refused capture, a pane that filled the
// frame, an app window that had no terminal panel in it yet.  Retried on its own, much shorter,
// clock: this is the state she comes BACK from (a window that closed and reopened, a minimize and a
// restore), so waiting a whole `fitSeconds` on the state that means "nowhere to stand" is exactly
// the delay the owner saw (2026-10-07: "she does have quite a bit delay to show up back if i
// minimize the window then come back").
var fitRetrySeconds = 1.5                // ...and the same clock, resolved the same way
// HOW SHARP SHE IS DRAWN.  The frames are 128 pixels and she can be 192pt on a 2x display, which is
// six screen pixels per art pixel: the drawing path's own filter averages what it cannot know, and
// the one thing that fixes a resample that big is to do it ONCE, off the screen, with a real filter
// (Lanczos) and a light unsharp mask on top, at exactly the pixel size she will be drawn at — so the
// screen's own scaler has nothing left to do.  `--sharp=0` (or FBTODO_PIP_SHARP=0) puts the old
// drawing path back, which is how the two are A/B-ed rather than argued about.
let sharpSetting = (option("--sharp")
                    ?? ProcessInfo.processInfo.environment["FBTODO_PIP_SHARP"]
                    ?? "1").lowercased()
let sharpen = !["0", "off", "no", "false", "none"].contains(sharpSetting)
let sharpRadius = max(0.0, Double(option("--sharp-radius") ?? "1.4") ?? 1.4)
let sharpIntensity = max(0.0, Double(option("--sharp-intensity") ?? "0.5") ?? 0.5)

// HOW SHE GETS FROM ONE STATE TO THE NEXT, and the one number that is all of it.  Everything she does
// is a change of state — a slot the pane's layout moved under her, a window that has to go away
// because the list filled the pane and come back when it has room again, a pose that follows the
// pane's mood — and every one of them used to be drawn inside a single tick: she teleported to the new
// slot, popped in and out, and blinked between two pictures.  This is the length of all three now: the
// move is EASED over it, the window FADES over it, and a pose change is a CROSS-DISSOLVE of the same
// length, so "slow her transitions down" is one number rather than three behaviours to hunt for
// (2026-10-07).  `0` is the old behaviour exactly — every one of them a hard cut — which is how the
// two are compared rather than argued about; a value that is not a number is the default.
var transitionS = 10.0                   // the one number all of it hangs on: see `applyTune`
// WHAT SHE SAYS, and how often she is allowed to: a speech bubble drawn INSIDE her own square — never
// over the pane's list, which is the one thing her window must not cover — so a line costs the pane
// nothing and cannot end up anywhere she does not already stand (2026-10-07: "add a chat bubble so
// sometimes she says something cute and cool, or even silly"). `--bubble 0` / `FBTODO_PIP_BUBBLE=0`
// takes the bubble away entirely and the gap is how long she keeps quiet after a line.
//
// The gap is REUSED as her talking cadence ("use bubble cloud talk more often", 2026-10-08): it gates
// both the line a mood change brings and the line the CLOCK brings, so there is one silence to tune
// rather than two that can disagree — how long she keeps quiet after a line IS how often she is allowed
// to break it. 20 was too long for that second job: a mood lasts a whole turn, so a mood-change-only
// line is one line per twenty minutes, which is why the default is 12 now (a line every ~12 seconds: the
// 9 she stays up, then the gap).
var bubbleOn = true                      // whether she talks at all, and how long she keeps quiet:
var bubbleGapS = 12.0                    // both resolved by `applyTune`
/// HOW OFTEN SHE SURPRISES YOU: the chance that a mood change gets the surprise balloon and one of the
/// lines that belong to no mood at all, instead of the line that mood would have said ("you can have
/// some mood sometimes appear to surprise users", 2026-10-07).  `0` is never and `1` is every time — the
/// two ends a test can pin — and it is a rate rather than a mood on purpose: the pane never asks for it,
/// so it can arrive on any word the pane publishes, which is the one thing about her that is not a
/// function of the list.
var surpriseChance = 0.12                // ...resolved by `applyTune`, like every knob above
/// HOW OFTEN SHE IS CRINGE, and how often she says a whole SENTENCE instead of a tag: two more rolls at
/// the same moment the line is picked (see `pickLine`), both resolved by `applyTune` from `--cringe`
/// (`FBTODO_PIP_CRINGE`, 0.35) and `--sentence` (`FBTODO_PIP_SENTENCE`, 0.15) — 2026-10-08: "make her
/// say cringe things often as well, once in a while she should say something a bit longer like a short
/// sentence". They are rates rather than rows because both are FLAVOURS of every mood: each mood has
/// cringe lines and sentences of its own, in the same table shape, so the emotion still fits whichever
/// roll won.
var cringeChance = 0.35
var sentenceChance = 0.15
/// How long a line stays up, and how long it takes to appear and go: her own, much shorter, clock (see
/// `PipView.say`), because two words are read at a glance and ten seconds to read them is not a
/// transition.
/// Resolved by `applyTune` from `--bubble-seconds`: a line stays up this long before its fade (2026-10-08: the
/// five-second line was gone before it could be read, so it is nine by default).
var bubbleSeconds = 9.0
let bubbleFadeS = 0.35
/// How much the balloon's outline is grown before the body is drawn over it: the outline is a FILL in
/// her window, never a stroke, so it cannot draw the arcs the puffs' union ate inside it.
let bubbleOutline: CGFloat = 1.6
/// How long the balloon takes to POP into place, in seconds (`--pop`, `FBTODO_PIP_POP`): it arrives
/// squashed and springs open in a third of a second, which is what tells the eye something NEW appeared —
/// the fade it used to have is the same fade as going away, so the two were told apart only by watching
/// the whole half-second.  `0` is no pop at all (the plain fade, and the A/B arm), and a still render has
/// no arrival time to pop against, so an `--art` frame is always the settled shape.
var bubblePopS = 0.34                    // ...resolved by `applyTune`, like the rest of the knobs
/// The FINISHED burst: how long she rattles through her own poses after the last step is ticked, and
/// how fast — four poses of celebration rather than the mood's own two-and-a-half seconds each.
let celebrateS = 3.4
let celebrateMs = 220.0
/// The CAP under a pose, and the floor under the cap: a dissolve is at most one step of the mood it is
/// arriving at — a mood that steps every 900ms cannot be dissolving for ten seconds without being a
/// permanent smear — but never shorter than this either, because the point of the whole knob is a
/// transition you can WATCH (2026-10-07: "you can have the cap, but sometimes i don't want it to be too
/// fast transition").  A mood that HOLDS has no step to cap against and takes the whole transition.
/// 10 was the default until 2026-10-08, and it was the reason the pose looked like it never stopped
/// changing ("she's currently change her pose too much"): the dissolve is `min(transition, max(step,
/// pose-min))`, so a floor ABOVE every mood's own step — work steps every 2.5s, and 10 is bigger than
/// that — made the dissolve longer than the pose it was arriving at, and she spent the whole cycle between
/// two frames instead of settling on the one she had. 2 keeps the job the knob exists for (a fade is never
/// instant) while letting the POSE be the ceiling it was always meant to be: the floor must stay UNDER
/// `pose-hold`, or the two knobs contradict each other, and the suite pins that.
var poseFloorS = 2.0                     // ...resolved by `applyTune`, below
/// The MINIMUM TIME A POSE STAYS UP before the mood moves her to the next one (`--pose-hold`, seconds, `0`
/// for none). Without it a mood that steps every 600–900ms changes pose faster than the eye can settle on
/// one (2026-10-08: "she won't change her pose too quick"). It holds the mood's own steps only: the finished
/// burst keeps its own pace, because the burst IS the fast pace on purpose.
///
/// 8 seconds, not the 1.5 this started at (2026-10-08: "i want change pose become less often", then "she's
/// currently change her pose too much" at 4): at 1.5 the held number only ever bit the two quick moods,
/// because a mood's own `ms` is what it says (work steps every 2.5s) — so the pose still changed every
/// two and a half seconds, and 4 halved that rather than fixing it. At 8 every mood takes one pose per
/// eight seconds whatever its own pace says, which is a picture you can look at instead of one you have to
/// catch. The knob is live, so `fbtodo pip tune pose-hold 15` slows her further without a restart, and `0`
/// is the old mood's-own-pace behaviour.
var poseHoldS = 8.0                      // ...resolved by `applyTune`, like every knob here
/// A transition is meant to be SEEN, so a number below this is raised to it rather than believed
/// (2026-10-07: "i let the transition take at least 10s").  `0` is the one value that is not a
/// length at all — it is the cut she started with, which is how the two ends of this knob are
/// compared — so it is passed through untouched rather than floored.
let transitionFloorS = 10.0

// ...and every knob above is resolved HERE, once, in the order this file has now declared them: the flag,
// the environment, the tune FILE, the default — with the transition's own floor and the bubble's
// on/off words applied the same way they always were.  Every launch goes through `applyTune`, which is what
// makes `--art`, `--shot`, `--fit` and the window itself all read the same values, and the pin tick re-runs
// it (see `tuneStamp`) so a knob set from the shell is worn without a restart.
applyTune()

// WHAT SHE IS DOING, and how she feels about it.  The pane computes one word for this already —
// `buffy_mood` in render.py, the same word that chooses the face on the pane's own top border (a
// failure, the rewrite-the-list nudge, a finished list, no list at all, idle past the pane's own
// threshold, a heading left over from an earlier turn, or the ordinary working frame) — and
// publishes it where her window can read it (`note_pane_mood`).  Her window then wears the POSE
// that word means, so the picture she is and the face the pane draws cannot disagree about the same
// list.
//
// The frames are the owner's twenty Discord stickers, numbered the way `assets/buffy` numbers them
// (1 is `01.png`), and each mood is a short cycle at its own pace rather than one frozen picture:
// she sits at the laptop and thinks about the step (`work`, 20 and 17), throws her arms up when
// every step is ticked (`done`), dozes with the cat when the session has gone quiet (`idle`), peers
// about puzzled when there is no list to point at (`none`), and HOLDS still for the three that are
// already over by the time they are drawn (`error`, `nudge`, `stale` — the pane holds still on those
// faces too, see BUFFY_CYCLE).  A pose nobody can reach is impossible: an unknown mood falls back to
// `work`, and a frame number past the end of the frames on disk is skipped.
//
// Every one of the twenty is worn by some mood (2026-10-07: "trying to use all of buffy-chan
// images"): the four moods that MOVE carry the extra poses — the box she works out of, the smug one
// at the desk, the wave, the sunglasses, the thumbs up, the heart, the blush — and the four that
// HOLD keep the single picture each one is.  Which frames are in which cycle is data, so the suite
// checks the union of the whole table against the art on disk rather than trusting this comment.
let moodCycles: [String: (frames: [Int], ms: Double)] = [
    "work":  ([20, 17, 8, 4], 2500),      // at the laptop, thinking, the box, smug at the desk
    "done":  ([12, 18, 9, 1, 15, 14], 900),  // arms up, cards, cheer, thumbs up, the heart
    "idle":  ([3, 2, 16, 13], 3000),      // dozing, the cat, standing there, blushing
    "none":  ([19, 10, 5, 6], 1800),      // puzzled, peering, waving, sunglasses on
    "wait":  ([2], 0),                    // hugging the cat: the turn is over, the ball is yours — HELD
    "error": ([11], 0),                   // hands to her face: it went wrong, and it is over
    "nudge": ([7], 0),                    // the cross: rewrite the list, then continue
    "stale": ([17], 0),                   // sitting with it: a heading left over from an earlier turn
]
/// WHAT SHE SAYS, one short line per mood — the bubble is drawn inside her own square, so a line has to
/// fit in a couple of words rather than a sentence, and the point of it is company rather than
/// information (the pane is where the facts are). A mood with no lines says nothing at all, and a mood
/// with several picks one at random, never the one she just said: the repetition is what makes a
/// companion program read as a tape loop.
///
/// The table is the whole of "does it fit her emotion": a line is only ever reachable through the mood
/// she is IN (`bubbleLine(mood, …)`, from both the mood-change path and the clock), so a line written
/// into the wrong row is a line she would say while feeling something else — which is why every mood the
/// pane can put her in now carries at least four, and the two she spends whole turns in (`work`, `idle`)
/// carry six: with the clock talking again the pool has to be wider than a mood change needed (2026-10-08:
/// "use bubble cloud talk more often, and make sure it fits her emotion").
///
/// `nudge`'s last line is a REUSE — the words are already `work`'s, and they are shared rather than copied
/// because the two moods mean the same thing about it: "one more step" is what she says while she takes
/// one, and a nudge is her asking for the next one. One row deliberately shared is one fewer thing to
/// keep in step, and the suite pins it (a line two moods may say).
let bubbleLines: [String: [String]] = [
    "work":  ["head down", "one more step", "on it!", "thinking…",
              "typing…", "hm hm hm", "*focused*", "shipping it"],
    "done":  ["all done!", "we did it", "ta-da!", "look at that",
              "went well!", "that was fun"],
    "wait":  ["your move", "poke me when", "waiting on you",
              "no rush", "*idles*"],
    "error": ["oops", "that went wrong", "hmm…", "not my fault",
              "*sweats*", "try again?"],
    "nudge": ["continue?", "the list!", "still here?",
              "one more step"],                 // <- the reuse: `work`'s own line, and what a nudge asks for
    "stale": ["old list", "leftover…", "that one's stale",
              "still a list?"],
    "none":  ["no list?", "where's the list", "nothing to do?",
              "i'll wait", "*looks around*"],
    "idle":  ["so quiet", "*dozes*", "zZZ", "all mine",
              "*stretches*", "no thoughts"],
]

/// WHICH MOODS THE CLOCK MAY TALK IN — and this is not every mood, because two of them mean opposite
/// things about being here. `work`, `done`, `idle`, `none` and `wait` are her COMPANY: she is at the
/// desk, she just finished, she is dozing, she cannot find the list, the ball is in your court — a
/// mood you sit in for minutes, where one line at the start and nothing after it is a caption.
/// `error`, `nudge` and `stale` are a FACT rather than a feeling: something went wrong, the list needs
/// rewriting, the heading is left over from a turn that ended. Each already says its line when it
/// arrives, and her POSE holds still for exactly those three (see `moodCycles`), so the line holds with
/// it — a companion that keeps reminding you the list is stale is not company, it is nagging. The rest
/// of the table is unchanged: any mood may still speak on a mood change, and `surprise` is nobody's
/// mood at all.
let bubbleClockMoods: Set<String> = ["work", "done", "idle", "none", "wait"]

/// THE LINES SHE HAS SAID RECENTLY, newest last, and the depth of that memory.
///
/// "Never the one she just said" was enough while a line only arrived on a mood change; the clock talks
/// three times a minute now, so a pool of four tags came back around inside the minute and the joke
/// stopped landing (2026-10-08: "also avoid her to repeat the same thing too often"). Six is one more
/// than the widest SHORT pool (`work`'s eight minus the flavours, the four-tag moods exactly), so a mood
/// she stays in can go a full cycle without a repeat while still never running dry — see the fallback in
/// `pickFresh`, which is what keeps "no repeats" from becoming "no line".
let recentDepth = 6
var recentLines: [String] = []

/// Remember a line she has just said, dropping the oldest past `recentDepth`.
func noteSaid(_ text: String) {
    recentLines.append(text)
    if recentLines.count > recentDepth { recentLines.removeFirst(recentLines.count - recentDepth) }
}

/// The part of a pool she has NOT said recently — what `pickFresh` rolls in, exposed because the choice of
/// which TABLE answers also depends on it (see `pickLine`): a flavour table is smaller than the memory, so
/// "is there anything fresh in here" is the question that decides, not the roll alone.
func freshIn(_ pool: [String], avoid: String = "") -> [String] {
    pool.filter { $0 != avoid && !recentLines.contains($0) }
}

/// One line out of a pool, never one she has said recently — and never the one she just said even when
/// the whole pool is recent (a line she said two moods ago is the oldest of the crimes).
///
/// Three steps, in order, because a small pool must not be able to silence her: the pool minus her recent
/// lines; failing that, the pool minus ONLY the last one (the rest of her history is older than the line
/// she is replacing); failing that — a one-line pool, or a pool the lines FILE shrank to one — the pool,
/// because repeating a line is better than saying nothing at all.
func pickFresh(_ pool: [String], avoid: String = "") -> String? {
    guard !pool.isEmpty else { return nil }
    if let said = freshIn(pool, avoid: avoid).randomElement() { return said }
    let notLast = avoid.isEmpty ? pool : pool.filter { $0 != avoid }
    return (notLast.isEmpty ? pool : notLast).randomElement()
}

/// One of her lines for a mood, never one she has said recently (and any of them when that is the only
/// one there is).  `avoid` is the caller's own exclusion — the line still on screen — on top of that
/// history, so `pickLine` and the surprise table both go through the same memory.
func bubbleLine(_ mood: String, avoid: String) -> String? {
    guard let all = bubbleTable[mood], !all.isEmpty else { return nil }
    return pickFresh(all, avoid: avoid)
}

/// WHAT SHE IS WHEN SHE IS CRINGE, one row per mood (2026-10-08: "make her say cringe things often as
/// well").  Cringe is a FLAVOUR rather than a mood, so it is a table of its own instead of rows spliced
/// into `bubbleLines`: the ordinary line stays exactly what the mood is about, the cringe one is her
/// being a dork about it, and which she is is the roll in `pickLine` — never a line that has to fit two
/// jobs at once.  Every row is still the mood's own, so the dorking is IN character: she is "notice me,
/// senpai" when the list is finished, not when it broke.
/// Three rows and not two, because cringe is the flavour she wears MOST often (a third of her lines by
/// default): with two, the same dorky line came back within three rolls and the joke stopped landing —
/// the pool is the joke's whole life, so it has to be wider than the rate.
let cringeLines: [String: [String]] = [
    "work":  ["uwu", "*nuzzles the keyboard*", "nyaa~ let me work"],
    "done":  ["notice me, senpai", "did i do good? *sparkles*", "*twirls* reward me"],
    "wait":  ["i'm not clingy, baka", "*stares at you*", "i can wait. i'm very patient. i'm not."],
    "error": ["i meant to do that", "i didn't break it, it was already like that", "*nervous uwu*"],
    "nudge": ["senpai… the list", "*pokes you*", "notice the list, notice it"],
    "stale": ["this list is so last turn", "uwu, still here", "*sniffs the old list*"],
    "none":  ["senpai has no list?", "*sad uwu*", "where list… give list"],
    "idle":  ["let's be lazy together", "*rolls around*", "*wiggles* nothing to do"],
]

/// ...and A WHOLE SENTENCE, once in a while: the balloon is measured and wraps (`bubbleLayout`), so a
/// line may be several words long as long as it still FITS her square — the cap is half her height and
/// the scallops shrink rather than the words being clipped, which is why these are short clauses and not
/// paragraphs.  One row per mood, for the same reason `cringeLines` has one: a sentence she could only
/// say while feeling one thing is a sentence in the wrong mouth every other time.
///
/// WHAT MAKES A LINE HERS, and the one rule every row here is written to (2026-10-08: "i don't want her
/// to say something too generic and sounds like an AI"): a line is about the THING SHE IS LOOKING AT —
/// the step she is on, the tick box, the leftovers, the cat — and it would be absurd in a support bot's
/// mouth. No reassurance, no "i'm here", no "let me know", no feelings-talk about the reader: the
/// bubble is her being a dork about the list, not an assistant managing a customer. The suite holds a
/// denylist of the phrases that make a line sound like one (`GENERIC_LINES`) so a later "friendlier"
/// sentence cannot quietly put the assistant back in her mouth.
let longLines: [String: [String]] = [
    "work":  ["one more step and i'll stop staring at it", "typing so fast my ahoge is vibrating"],
    "done":  ["every step is ticked, i'm doing a little dance", "look, no steps left — tell me i did good"],
    "wait":  ["i'm sitting on the list until you say something", "the ball is yours, i'm not picking it up"],
    "error": ["that one crashed and i'm pretending it didn't", "it broke. i would like a snack about it."],
    "nudge": ["this list hasn't been touched in ages, you know", "say continue and i'll pretend i wasn't waiting"],
    "stale": ["this list is from a turn that already ended, ugh", "nothing is running, it's just me and the leftovers"],
    "none":  ["there's no list and i'm just standing here", "give me a todo, i have nothing to stare at"],
    "idle":  ["nothing is happening and i've made peace with the cat", "i'm going to sit here and think about snacks"],
]

/// THE LINE SHE SAYS NEXT, out of the three tables above, and WHICH table it came from — her mood's
/// ordinary line, a cringe one, or a whole short sentence — with `cringe` and `sentence` as the rates that
/// decide (see `cringeChance`).  One function rather than a roll at each of the two places that speak
/// (the mood change and the clock), because otherwise the two would drift and "she is cringe sometimes"
/// would be true on only one of them.
///
/// A mood the lines FILE emptied says nothing at all, cringe and sentence included: clearing a mood's
/// lines is how "she says nothing in this one" is written down, and a flavour table that ignored it would
/// make that promise false.  The kind is returned rather than swallowed so her log can name it —
/// `kind=cringe` is the difference between "she said a weird thing" and "she is broken".
func pickLine(_ mood: String, avoid: String, cringe: Double, sentence: Double) -> (String, String)? {
    guard let ordinary = bubbleTable[mood], !ordinary.isEmpty else { return nil }
    let roll = Double.random(in: 0 ..< 1)
    let cringeTable: [(kind: String, pool: [String])] = [("cringe", cringeLines[mood] ?? [])]
    let sentenceTable: [(kind: String, pool: [String])] = [("sentence", longLines[mood] ?? [])]
    let sayTable: [(kind: String, pool: [String])] = [("say", ordinary)]
    // The ROLL asks for a table; FRESHNESS decides who answers. The flavours are smaller than the memory
    // (three cringe lines against six recent ones), so a flavour that has nothing fresh left hands over to
    // the other two rather than repeating itself a third time — which is what "avoid her to repeat the same
    // thing too often" asks for, and it is why the memory and the pools are consulted together here instead
    // of the pool being rolled and then filtered.
    var order: [(kind: String, pool: [String])]
    if roll < cringe { order = cringeTable }
    else if roll < cringe + sentence { order = sentenceTable }
    else { order = sayTable }
    order += (cringeTable + sentenceTable + sayTable).filter { $0.kind != order[0].kind }
    for table in order {
        if let said = freshIn(table.pool, avoid: avoid).randomElement() { return (said, table.kind) }
    }
    // Nothing fresh in any table: the roll's own table repeats, by the fallback ladder in `pickFresh`.
    return pickFresh(order[0].pool, avoid: avoid).map { ($0, order[0].kind) }
}

/// WHAT HER BALLOON IS SHAPED LIKE, and in what colour, one row per mood — because she does not say a
/// line inside one card with the words changed: the owner asked for "a bubble cloud box like in manga
/// wrap around text to show her emotion", so the words are measured first and the balloon is the ring
/// of scallops that wraps that measurement (see `drawBubble`), while this table decides what those
/// scallops are — soft and cloudy while she works, rounder and spangled when the list is finished, a
/// row of detached DOTS when she is only thinking, waiting or left over, and a jagged shock balloon
/// when something went wrong or the list needs rewriting.  Every size here is a fraction of the LINE's
/// own height rather than of her square, which is exactly what makes the cloud follow the text instead
/// of the window, and the colour is the mood's own so the same line reads differently in every one.
struct BubbleShape {
    var puff: CGFloat                   // the scallops: radius, as a share of the line's height
    var gap: CGFloat                    // how far apart they sit, as a multiple of that radius
    var spikes: Int                     // points around the ring: 0 is a cloud, more is a shock
    var sparkles: Int                   // little stars printed on it
    var thought: Bool                   // a trail of detached dots (thinking) instead of a tail
    var border: (red: CGFloat, green: CGFloat, blue: CGFloat)
}

let bubbleShapes: [String: BubbleShape] = [
    "work":  BubbleShape(puff: 0.38, gap: 1.45, spikes: 0, sparkles: 2, thought: false,
                         border: (red: 0.86, green: 0.44, blue: 0.62)),
    "done":  BubbleShape(puff: 0.44, gap: 1.40, spikes: 0, sparkles: 4, thought: false,
                         border: (red: 0.95, green: 0.36, blue: 0.52)),
    "idle":  BubbleShape(puff: 0.34, gap: 1.60, spikes: 0, sparkles: 0, thought: true,
                         border: (red: 0.62, green: 0.56, blue: 0.86)),
    "none":  BubbleShape(puff: 0.36, gap: 1.50, spikes: 0, sparkles: 1, thought: true,
                         border: (red: 0.86, green: 0.62, blue: 0.28)),
    "wait":  BubbleShape(puff: 0.34, gap: 1.55, spikes: 0, sparkles: 1, thought: true,
                         border: (red: 0.36, green: 0.70, blue: 0.72)),
    "error": BubbleShape(puff: 0.38, gap: 1.30, spikes: 11, sparkles: 0, thought: false,
                         border: (red: 0.88, green: 0.24, blue: 0.28)),
    "nudge": BubbleShape(puff: 0.36, gap: 1.40, spikes: 7, sparkles: 0, thought: false,
                         border: (red: 0.90, green: 0.52, blue: 0.22)),
    "stale": BubbleShape(puff: 0.32, gap: 1.55, spikes: 0, sparkles: 0, thought: true,
                         border: (red: 0.55, green: 0.55, blue: 0.58)),
]

/// ...and the ONE balloon that is not a mood, because it is not a state: the SURPRISE.  Now and then she
/// wears this one instead of the mood's — the same kind of line, a shape that is all the moods at once
/// (spikes AND stars AND thought dots, in violet) — which is what "you can have some mood sometimes
/// appear to surprise users" is worth in pixels.  It is deliberately NOT a row of `bubbleShapes`: that
/// table is one row per mood the PANE can put her in, and a surprise is nobody's mood.
let surpriseShape = BubbleShape(puff: 0.42, gap: 1.35, spikes: 9, sparkles: 4, thought: true,
                                border: (red: 0.68, green: 0.28, blue: 0.88))
/// What she says when it is one: a silly thing that belongs to no mood at all, on purpose, because the
/// point of a surprise is that it is not the mood.
let surpriseLines: [String] = ["boo!", "just saying hi", "still cute?", "surpriiise", "*pomf*",
                               "did you see that?"]

/// What she actually SAYS: the defaults above, then whatever the lines FILE says (see
/// `applyLineOverrides`) — the same deal the mood table has, because the two are the same wish: a joke or
/// a pace you want to change should be a text file she re-reads, not a rebuild.
var bubbleTable = bubbleLines
var surpriseTable = surpriseLines
/// ...and the FILE itself: `--lines-file PATH`, or `FBTODO_PIP_LINES`, or the cache beside her mood. Its
/// shape is exactly what `--lines` prints — `mood` + a TAB + the words — so a redirect seeds a complete,
/// valid file to edit and `--lines > ~/.cache/fbtodo/pip-lines` IS how a retune starts.
let linesFile = ((option("--lines-file")
                  ?? ProcessInfo.processInfo.environment["FBTODO_PIP_LINES"]
                  ?? homeDir.appendingPathComponent(".cache/fbtodo/pip-lines").path)
                 as NSString).expandingTildeInPath
/// The lines file's identity as last read, so a joke added by hand is noticed and an untouched file costs
/// one `stat` per pin tick (the same shape `moodsStamp` has, and for the same reason).
var linesStamp: String? = nil

/// What she actually wears: the defaults above, then whatever the override FILE says (see
/// `applyMoodOverrides`) — so the table is data, and retuning a pace or a pose is editing a text file
/// rather than building her window again.
var moodTable = moodCycles
let moodFile = ((option("--mood")
                 ?? homeDir.appendingPathComponent(".cache/fbtodo/pip-mood").path)
                as NSString).expandingTildeInPath
// ...and THE TABLE ITSELF, in a file: `--moods-file PATH`, or `FBTODO_PIP_MOODS`, or the cache
// beside her mood. The shape is exactly what `--moods` prints, so the way to retune her is a
// redirect and an editor: `fbtodo-pip --moods > ~/.cache/fbtodo/pip-moods`, change a number, save,
// and she is wearing it on the next press of her clock — no rebuild, no restart (see
// `applyMoodOverrides` for what a half-edited file does).
let moodsFile = ((option("--moods-file")
                  ?? ProcessInfo.processInfo.environment["FBTODO_PIP_MOODS"]
                  ?? homeDir.appendingPathComponent(".cache/fbtodo/pip-moods").path)
                 as NSString).expandingTildeInPath
/// The override file's identity as last read, so a retuned table is noticed and an untouched one
/// costs one `stat` per pin tick. A string rather than the pair it is made of: a missing file is
/// `nil`, which is also what a DELETED file returns, so dropping the file puts the defaults back.
var moodsStamp: String? = nil

// `--moods` prints the table above and exits: one TAB-separated line per mood, `name frames ms`
// (`ms` 0 is a mood that HOLDS), which is how the table is checked against the pane's own
// vocabulary, against the frames on disk and against what she actually renders — by a test rather
// than by reading the Swift. It needs no window, no frames and no pane, so it is safe to call from
// anywhere, and it is placed before the frames are loaded for the same reason.
// (`args.contains`, not `option`: this flag takes no VALUE, and an option helper that insists on
// one reads a bare `--moods` as "not given" and opens a window instead of printing the table).
if args.contains("--moods") {
    // The EFFECTIVE table — the defaults with the override file applied — in the shape that file
    // takes, which is what makes `--moods > pip-moods` a retune rather than a transcript. The frame
    // count is listed here rather than loaded: this is a query about a table, and it stays usable on
    // a machine whose frames are elsewhere or missing.
    let onDisk = ((try? FileManager.default.contentsOfDirectory(atPath: frameDir)) ?? [])
        .filter { $0.lowercased().hasSuffix(".png") }.count
    _ = applyMoodOverrides(framesOnDisk: onDisk)
    for name in moodTable.keys.sorted() {
        let mood = moodTable[name]!
        print("\(name)\t\(mood.frames.map { String($0) }.joined(separator: ","))\t\(Int(mood.ms))")
    }
    exit(0)
}

// `--lines` prints WHAT SHE SAYS and exits: one row per line, the mood, a TAB, the words — the shape the
// lines FILE takes, so a redirect seeds a complete, valid file to edit. It is also how a check can read the
// words her window would really use out of the program rather than out of its source, and it needs no
// window, no frames and no pane, which is why it sits here beside `--moods`.
if args.contains("--lines") {
    _ = applyLineOverrides()
    for name in bubbleTable.keys.sorted() {
        for said in bubbleTable[name]! { print("\(name)\t\(said)") }
    }
    for said in surpriseTable { print("surprise\t\(said)") }
    exit(0)
}

// `--tune` prints EVERY KNOB and exits: `name`, the value she would run with, where that value came from
// (`cli`/`env`/`file`/`default`) and whether her running window can wear a change to it.  It is what
// `fbtodo pip tune` shows — the CLI asks the program rather than keeping a second copy of the table — and it
// needs no window, no frames and no pane, so it sits here beside `--moods` and `--lines`.
if args.contains("--tune") {
    _ = applyTune()
    for knob in tuneKnobs {
        print("\(knob.name)\t\(tuneValue(knob.name))\t\(tuneFrom[knob.name] ?? "default")\t"
              + "\(knob.live ? "live" : "restart")")
    }
    exit(0)
}

/// The mood the pane last wrote, or "work" when there is no pane to say — the one state that is
/// always true of a session that is running at all.
func readMood() -> String {
    guard let text = try? String(contentsOfFile: moodFile, encoding: .utf8) else { return "work" }
    let word = text.split(whereSeparator: { " \n\t\r".contains($0) }).first.map(String.init) ?? ""
    return moodTable[word] == nil ? "work" : word
}

/// The file's identity — modification time and size — or nil when there is no file.
func fileStamp(_ path: String) -> String? {
    guard let attrs = try? FileManager.default.attributesOfItem(atPath: path) else { return nil }
    let when = ((attrs[.modificationDate] as? Date) ?? Date.distantPast).timeIntervalSince1970
    return "\(when):\((attrs[.size] as? Int) ?? -1)"
}

/// Retune the table from a FILE, without rebuilding her window.
///
/// One row per mood, whitespace-separated, in exactly the shape `--moods` prints — `name frames ms`,
/// with `frames` a comma-separated list of the numbers `assets/buffy` gives them and `ms` the pace
/// (`0` holds that pose) — so printing the table and saving it IS how a retune starts. Three rules
/// make a half-edited file safe: a file that names only some moods is an OVERRIDE and every mood it
/// does not mention keeps the built-in row; a line naming a mood that does not exist, or with the
/// wrong number of fields, is ignored; and a row whose frames are all past the end of the art on
/// disk is ignored whole rather than leaving her with no pose for a mood the pane can put her in.
/// A missing file is not an error either: it just means the defaults.
///
/// Returns how many rows the file set, for the line that says so in her log.
/// Retune WHAT SHE SAYS from a FILE, without rebuilding her window.
///
/// One row per line she may say: the mood, a TAB, the words — a TAB and not a space, so a line can be a
/// sentence, and exactly the shape `--lines` prints. `#` starts a comment, blank rows are ignored, and a row
/// for a mood the pane cannot produce is ignored like any other typo. Four rules make a half-edited file
/// safe, and they mirror the mood table's: a mood the file does not name keeps the built-in lines (a file
/// naming ONE mood is an override, so adding a joke cannot quietly delete the other seven); EVERY row for a
/// mood is a line she may say, in the file's order; a row with no words CLEARS that mood's lines, which is
/// how "she says nothing in this one" is written down; and `surprise` is a key here too, for the lines that
/// belong to no mood. A missing file is not an error — it is the defaults.
///
/// Returns how many moods the file set, for the line that says so in her log.
func applyLineOverrides() -> Int {
    var table = bubbleLines
    var surprises = surpriseLines
    guard let text = try? String(contentsOfFile: linesFile, encoding: .utf8) else {
        bubbleTable = table
        surpriseTable = surprises
        return 0
    }
    var picked: [String: [String]] = [:]
    var touched: Set<String> = []
    for raw in text.split(separator: "\n", omittingEmptySubsequences: false) {
        let row = raw.prefix { $0 != "#" }
        guard let tab = row.firstIndex(of: "\t") else { continue }
        let name = row[row.startIndex..<tab].trimmingCharacters(in: .whitespaces)
        let said = String(row[row.index(after: tab)...]).trimmingCharacters(in: .whitespaces)
        guard bubbleLines[name] != nil || name == "surprise" else { continue }
        touched.insert(name)
        if !said.isEmpty { picked[name, default: []].append(said) }
    }
    for name in touched {
        if name == "surprise" { surprises = picked[name] ?? [] } else { table[name] = picked[name] ?? [] }
    }
    bubbleTable = table
    surpriseTable = surprises
    return touched.count
}

func applyMoodOverrides(framesOnDisk: Int) -> Int {
    var table = moodCycles
    guard let text = try? String(contentsOfFile: moodsFile, encoding: .utf8) else {
        moodTable = table
        return 0
    }
    var applied = 0
    for raw in text.split(separator: "\n") {
        let line = raw.prefix { $0 != "#" }
        let parts = line.split(whereSeparator: { " \t".contains($0) }).map(String.init)
        guard (2...3).contains(parts.count), let base = moodCycles[parts[0]] else { continue }
        let frames = parts[1].split(separator: ",").compactMap { Int($0) }
            .filter { framesOnDisk > 0 ? ($0 >= 1 && $0 <= framesOnDisk) : $0 >= 1 }
        guard !frames.isEmpty else { continue }
        var ms = base.ms
        if parts.count == 3, let wanted = Double(parts[2]), wanted >= 0 { ms = wanted }
        table[parts[0]] = (frames, ms)
        applied += 1
    }
    moodTable = table
    return applied
}

struct Host {
    var rect: CGRect
    var pid: Int
    var owner: String
    var onscreen: Bool
    var number: Int

    /// The same window, judged from two measurements: used to throw away a fit taken before the
    /// window moved, was resized, or was replaced.
    func sameWindow(as other: Host?) -> Bool {
        guard let other else { return false }
        return number == other.number && rect == other.rect
    }
}

/// The window the pane is drawn in, in SCREEN coordinates from the top-left corner — the same
/// numbers the window server hands out, and the same ones `screencapture -R` takes.
///
/// `anchored` is the pane's own chain (see the header): a window it does NOT own is not hers, so
/// when the caller supplied one, only those windows are candidates. With no chain, the owner NAME
/// is all there is.
///
/// A window qualifies at layer 0 (a normal, focusable window — not a menu, a tooltip or the Dock)
/// and over 200pt on both sides. Of those, an ON-SCREEN window wins over an off-screen one — a
/// Space the user is not looking at must not take the pin from the one they are — and the largest
/// wins among equals, because the app's main window is the one a pane is in, not its popover.
func hostBounds(onscreenOnly: Bool, byName: Bool = false) -> Host? {
    let options: CGWindowListOption = onscreenOnly ? [.optionOnScreenOnly, .excludeDesktopElements]
                                                   : [.optionAll]
    let windows = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: Any]] ?? []
    var best: Host? = nil
    for window in windows {
        let owner = (window[kCGWindowOwnerName as String] as? String) ?? ""
        let pid = (window[kCGWindowOwnerPID as String] as? Int) ?? -1
        let layer = (window[kCGWindowLayer as String] as? Int) ?? 0
        guard layer == 0, pid > 0 else { continue }
        let mine = (!byName && !hostPids.isEmpty) ? hostPids.contains(pid)
                                                 : (!hostOwner.isEmpty && owner == hostOwner)
        guard mine, let box = window[kCGWindowBounds as String] as? [String: Any] else { continue }
        let number = (window[kCGWindowNumber as String] as? Int) ?? 0
        let rect = CGRect(x: box["X"] as? Double ?? 0, y: box["Y"] as? Double ?? 0,
                          width: box["Width"] as? Double ?? 0,
                          height: box["Height"] as? Double ?? 0)
        guard rect.width > 200 && rect.height > 200 else { continue }
        let onscreen = (window[kCGWindowIsOnscreen as String] as? Bool) ?? false
        let better: Bool
        if let now = best {
            better = (onscreen && !now.onscreen)
                || (onscreen == now.onscreen
                    && rect.width * rect.height > now.rect.width * now.rect.height)
        } else {
            better = true
        }
        if better {
            best = Host(rect: rect, pid: pid, owner: owner, onscreen: onscreen, number: number)
        }
    }
    return best
}

/// The pane's window if it can be found — the on-screen pass first, which is the cheap one, then
/// every window there is, because the pane's window is often on the Space the user is NOT on
/// (measured 2026-10-07: the desktop app's window reported `onscreen=false` while its pane was
/// live in it, and an on-screen-only search answered `none`).
///
/// ...and then by NAME, which is the difference between a window and no window for a FRESH fbtodo:
/// the chain `pip start` recorded is a snapshot of the process tree at that moment, so a pane
/// reopened, a session restarted or an app relaunched leaves every pid on it dead — and a search
/// that insists on the chain answers `none` for a window that is right there, which is how she
/// came to disappear when the reader opened a fresh pane (their words, 2026-10-07: "she should be
/// always in there"). The owner name is the fallback it has always been documented as.
func findHost() -> Host? {
    if let host = hostBounds(onscreenOnly: true) ?? hostBounds(onscreenOnly: false) { return host }
    guard !hostOwner.isEmpty, !hostPids.isEmpty else { return nil }
    return hostBounds(onscreenOnly: true, byName: true)
        ?? hostBounds(onscreenOnly: false, byName: true)
}

/// Where in that window she stands — TAUGHT first, then MEASURED off the pane's own drawing.
///
/// A taught pin is an offset from the window's own TOP-LEFT corner, so it survives the window
/// moving, being resized, or the Space changing: drag her where she belongs while she is free, pin
/// her again, and where she was left is what she follows. `fbtodo pip forget` drops it and `fbtodo
/// pip status` reports it — and she goes back to the measured fit below.
func readPin() -> CGPoint? {
    guard let text = try? String(contentsOfFile: pinFile, encoding: .utf8) else { return nil }
    let parts = text.split(whereSeparator: { ", \n\t".contains($0) })
    guard parts.count >= 2, let x = Double(parts[0]), let y = Double(parts[1]) else { return nil }
    return CGPoint(x: x, y: y)
}

func writePin(_ offset: CGPoint) {
    let url = URL(fileURLWithPath: pinFile)
    try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(),
                                             withIntermediateDirectories: true,
                                             attributes: [.posixPermissions: 0o700])
    try? "\(Int(offset.x.rounded())),\(Int(offset.y.rounded()))\n"
        .write(to: url, atomically: true, encoding: .utf8)
}

/// A Cocoa frame's own TOP edge in the window server's coordinates (which count down from the
/// primary display's top-left): the one flip between the two ways of saying where a window is.
func topEdge(_ rect: NSRect) -> CGFloat { fullHeight - rect.maxY }

// ====================================================== the pane, read back off the screen
/// One screenshot of a window, as RGBA bytes with row 0 at the TOP (the window server's own way of
/// counting, so every number in here can be compared with `host.rect` without a flip).
struct Screenshot {
    var width: Int
    var height: Int
    var px: [UInt8]
}

/// The same pixels with the rows reversed. A CGContext's buffer counts rows from the BOTTOM, while
/// everything in this file — the pane hunt, the PNG writer, `buffy_pixels` on the Python side —
/// counts from the top, so a capture is flipped exactly once, here, where it is made.
///
/// Measured 2026-10-07, and it was placing her wrongly: the frame's own border row came out at the
/// top of the array in one reading and the bottom in another, so the pane's top edge read as 5pt below
/// the window's when a screen capture (`screencapture -R`) and the project's own PNG reader both put
/// it at 62.5pt — and she stood 52pt above the slot the pane had left for her, over three step rows.
func flippedRows(_ px: [UInt8], width: Int, height: Int) -> [UInt8] {
    var out = [UInt8](repeating: 0, count: px.count)
    let stride = width * 4
    px.withUnsafeBufferPointer { src in
        out.withUnsafeMutableBufferPointer { dst in
            for y in 0..<height {
                let from = (height - 1 - y) * stride
                let to = y * stride
                for i in 0..<stride { dst[to + i] = src[from + i] }
            }
        }
    }
    return out
}

/// A screen image off disk, as RGBA bytes with row 0 at the TOP (the window server's own way of
/// counting, so every number in here can be compared with `host.rect` without a flip).
func screenshotOnDisk(_ path: String) -> Screenshot? {
    let url = URL(fileURLWithPath: (path as NSString).expandingTildeInPath)
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
          let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { return nil }
    let w = image.width, h = image.height
    guard w > 40, h > 40 else { return nil }
    var px = [UInt8](repeating: 0, count: w * h * 4)
    guard let ctx = CGContext(data: &px, width: w, height: h, bitsPerComponent: 8,
                              bytesPerRow: w * 4, space: CGColorSpaceCreateDeviceRGB(),
                              bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
        return nil
    }
    // A CGContext counts y from the BOTTOM; flipping it here is what makes row 0 the image's top
    // row, which is the only orientation the rest of this file talks about.
    ctx.translateBy(x: 0, y: CGFloat(h))
    ctx.scaleBy(x: 1, y: -1)
    ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
    return Screenshot(width: w, height: h, px: flippedRows(px, width: w, height: h))
}

/// Those bytes back out as a PNG, for `--shot`: one screenshot of the window she belongs to, so the
/// pane hunt can be looked at by hand instead of being guessed at.
func writePNG(_ shot: Screenshot, to path: String) -> Bool {
    guard let provider = CGDataProvider(data: Data(shot.px) as CFData),
          let image = CGImage(width: shot.width, height: shot.height, bitsPerComponent: 8,
                              bitsPerPixel: 32, bytesPerRow: shot.width * 4,
                              space: CGColorSpaceCreateDeviceRGB(),
                              bitmapInfo: CGBitmapInfo(
                                  rawValue: CGImageAlphaInfo.premultipliedLast.rawValue),
                              provider: provider, decode: nil, shouldInterpolate: false,
                              intent: .defaultIntent),
          let dest = CGImageDestinationCreateWithURL(URL(fileURLWithPath: path) as CFURL,
                                                     "public.png" as CFString, 1, nil)
    else { return false }
    // Row 0 is the image's top row (see `screenshotOnDisk`), which is also how CGImage reads a
    // provider's bytes, so the file comes out the same way up as the screen.
    CGImageDestinationAddImage(dest, image, nil)
    return CGImageDestinationFinalize(dest)
}

/// A window's pixels, through ScreenCaptureKit: the WINDOW's own content (a
/// `desktopIndependentWindow` filter and `ignoreShadowsSingleWindow` mean no windows on top of it,
/// so her own window never ends up in the picture she is being placed by, and no shadow, so the
/// pixels map onto the window's rectangle by one scale factor).
///
/// Not the `screencapture` CLI, which measured FIFTY-TWO SECONDS a shot on this machine (2026-10-07,
/// three runs) and was therefore useless for a measurement that has to be cheap enough to repeat:
/// the same window through this is 112ms plus 19ms to RGBA, measured the same day.
/// The hand-over between the capture's Task and the caller waiting for it, under a lock.
///
/// This was a plain global read after the semaphore wait, which is a DATA RACE the optimizer may
/// lose, and did: measured 2026-10-08, the same binary on the same window answered with a full
/// capture for `--doctor` and then "no screenshot" for the next three runs, from the same shell —
/// and the semaphore itself was a global too, so two measurements in flight could hand each other's
/// signal around.  One box and one semaphore PER CALL: the wait is then the only ordering that
/// matters, and there is nothing for a second caller to steal.
final class CaptureBox: @unchecked Sendable {
    private let lock = NSLock()
    private var value: Screenshot? = nil

    func put(_ shot: Screenshot?) {
        lock.lock()
        value = shot
        lock.unlock()
    }

    func get() -> Screenshot? {
        lock.lock()
        defer { lock.unlock() }
        return value
    }
}

func captureWindow(_ number: Int, timeout: Double = 4) -> Screenshot? {
    let box = CaptureBox()
    let done = DispatchSemaphore(value: 0)
    Task {
        defer { done.signal() }
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(
                false, onScreenWindowsOnly: false)
            guard let window = content.windows.first(where: { $0.windowID == CGWindowID(number) })
            else { return }
            let filter = SCContentFilter(desktopIndependentWindow: window)
            let config = SCStreamConfiguration()
            // Twice the window's own size: a point is worth two pixels on this display, and the
            // frame's border glyphs are thin enough that a half-scale capture loses them.
            config.width = max(1, Int(window.frame.width) * 2)
            config.height = max(1, Int(window.frame.height) * 2)
            config.showsCursor = false
            config.ignoreShadowsSingleWindow = true
            let image = try await SCScreenshotManager.captureImage(contentFilter: filter,
                                                                  configuration: config)
            let w = image.width, h = image.height
            guard w > 40, h > 40 else { return }
            var px = [UInt8](repeating: 0, count: w * h * 4)
            guard let ctx = CGContext(data: &px, width: w, height: h, bitsPerComponent: 8,
                                      bytesPerRow: w * 4, space: CGColorSpaceCreateDeviceRGB(),
                                      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
            else { return }
            ctx.translateBy(x: 0, y: CGFloat(h))
            ctx.scaleBy(x: 1, y: -1)
            ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
            box.put(Screenshot(width: w, height: h, px: flippedRows(px, width: w, height: h)))
        } catch {
            box.put(nil)
        }
    }
    // Bounded, because a measurement that can hang is a measurement that never runs again: the tick
    // that asked for it is long past and the next one is refused while this is in flight.
    if done.wait(timeout: .now() + timeout) == .timedOut { return nil }
    return box.get()
}

/// Everything the pane hunt needs from one screenshot, worked out in ONE pass over the pixels: per
/// row, the colour most of that row is (the wallpaper and the chat panel are not flat, so "the
/// row's own background" is what keeps their inks from being read as the pane's), and the ink mask
/// that falls out of it.
struct ShotInk {
    var width: Int
    var height: Int
    var bg: [(Int, Int, Int)]
    var mask: [Bool]
    var colInk: [Int32]
    var rowInk: [Int32]

    func ink(_ x: Int, _ y: Int) -> Bool { mask[y * width + x] }
}

func inkOf(_ shot: Screenshot, tol: Int = 40) -> ShotInk {
    let w = shot.width, h = shot.height
    var bg = [(Int, Int, Int)](repeating: (0, 0, 0), count: h)
    var mask = [Bool](repeating: false, count: w * h)
    var colInk = [Int32](repeating: 0, count: w)
    var rowInk = [Int32](repeating: 0, count: h)
    var hist = [Int32](repeating: 0, count: 4096)
    for y in 0..<h {
        let base = y * w * 4
        for i in 0..<4096 { hist[i] = 0 }
        for x in 0..<w {
            let at = base + x * 4
            hist[(Int(shot.px[at] >> 4) << 8) | (Int(shot.px[at + 1] >> 4) << 4)
                 | Int(shot.px[at + 2] >> 4)] += 1
        }
        var best = 0, bestN: Int32 = -1
        for i in 0..<4096 where hist[i] > bestN { bestN = hist[i]; best = i }
        let mr = (best >> 8) << 4, mg = ((best >> 4) & 15) << 4, mb = (best & 15) << 4
        bg[y] = (mr, mg, mb)
        for x in 0..<w {
            let at = base + x * 4
            let d = max(abs(Int(shot.px[at]) - mr),
                        max(abs(Int(shot.px[at + 1]) - mg), abs(Int(shot.px[at + 2]) - mb)))
            if d > tol {
                mask[y * w + x] = true
                colInk[x] += 1
                rowInk[y] += 1
            }
        }
    }
    return ShotInk(width: w, height: h, bg: bg, mask: mask, colInk: colInk, rowInk: rowInk)
}

/// The pane inside that window: `(left, right, top, bottom)` in screenshot pixels, or nil when
/// nothing in the window has the shape of a pane.
///
/// The shape it looks for is the frame fbtodo draws: a vertical border glyph on EVERY row of the
/// pane, at the pane's first and last column. So it takes the columns carrying the most ink (a
/// solid line, hundreds of rows long), tries them in pairs — far enough apart to be a pane, near
/// enough to be one — and keeps the pair with the longest run of rows where BOTH are inked. That
/// run is the pane's height: the frame is drawn top to bottom of the grid, so the two lines start
/// and end with it. Everything between them is the pane's own cells.
///
/// Measured 2026-10-07 against the desktop app's window: the two border columns carried ink on
/// 1220 and 1092 of 1604 rows, versus 1344 for the window's own edges — which is why the edges are
/// dropped before the pairs are tried, and why the answer was the pane and not the window.
///
/// The columns are taken one at a time, not together: a border glyph whose ink lands on a colour
/// the row already has (a rule crossing it, a filled chip beside it) goes unseen for a few rows, so
/// each column keeps its longest run with small gaps BRIDGED (`gap`), and the pane's height is
/// where the two runs OVERLAP — measured on that same window, the left border was inked 200..1426
/// and the right one 200..1426 with nineteen-row holes, and requiring them to be inked on the very
/// same row answered 325px and refused the pane it was looking at.
func longestInkRun(_ ink: ShotInk, _ x: Int, gap: Int) -> (Int, Int, Int) {
    let h = ink.height
    var start = -1, last = -1, bestStart = 0, bestEnd = 0
    for y in 0..<h where ink.ink(x, y) {
        if start < 0 {
            start = y
        } else if y - last > gap {
            if last - start > bestEnd - bestStart { bestStart = start; bestEnd = last }
            start = y
        }
        last = y
    }
    if last - start > bestEnd - bestStart { bestStart = start; bestEnd = last }
    return (bestStart, bestEnd, bestEnd - bestStart)
}

/// The columns carrying the most ink, one per group of neighbouring columns: what the pane hunt
/// works from, and what `--fit-verbose` prints to show why it decided what it decided.
func inkPeaks(_ ink: ShotInk) -> [(x: Int, n: Int32)] {
    let w = ink.width
    var cmax: Int32 = 0
    for x in 6..<(w - 6) where ink.colInk[x] > cmax { cmax = ink.colInk[x] }
    // A LINE, not a column with text in it: at least a quarter of the window's height, and at
    // least half of the longest line there is. Without the first half of that every column of a
    // paragraph qualifies, and the pane's borders — which are the answer — never get looked at.
    let threshold = max(Int32(ink.height / 4), cmax / 2)
    var peaks: [(x: Int, n: Int32)] = []
    var x = 6
    while x < w - 6 {
        if ink.colInk[x] >= threshold {
            var bestX = x, bestN = ink.colInk[x], last = x
            x += 1
            while x < w - 6 && x - last <= 6 {     // one glyph wide, plus its antialiasing
                if ink.colInk[x] >= threshold {
                    if ink.colInk[x] > bestN { bestN = ink.colInk[x]; bestX = x }
                    last = x
                }
                x += 1
            }
            peaks.append((bestX, bestN))
        } else {
            x += 1
        }
    }
    peaks.sort { $0.n > $1.n }
    return peaks
}

/// Whether a row of a candidate rectangle carries INK anywhere among a few sampled columns inside its
/// borders. Sampled rather than dense because this runs for every candidate pair on every measurement,
/// and three pixels of every cell-wide step is the same answer a full scan gives for a glyph.
func rowHasInk(_ ink: ShotInk, _ y: Int, _ a: Int, _ b: Int, _ step: Int) -> Bool {
    var x = a + 3
    while x <= b - 3 {
        if ink.ink(x, y) { return true }
        x += step
    }
    return false
}

func paneRect(_ ink: ShotInk, minRun: Int = 150,
              shapeHint: (rows: Int, cols: Int, start: Int, count: Int)? = nil,
              scale: CGFloat = 1,
              trace: ((String) -> Void)? = nil)
    -> (Int, Int, Int, Int)? {
    let w = ink.width, h = ink.height
    let gap = max(24, h / 24)                        // px: one row of a Retina pane, near enough
    // A window's own edge is a vertical line too: drop the outer few pixels, then keep the
    // strongest lines, one per group of neighbouring columns.
    let peaks = inkPeaks(ink)
    var best: (left: Int, right: Int, top: Int, bottom: Int, overlap: Int, area: Int,
               slackOk: Bool)? = nil
    // Sixteen strong columns, not eight: the pane's own left border is only the strongest column while
// nothing else in the window is taller, and a window with the file list open has plenty of long
// vertical lines to push it out of a short list (measured 2026-10-07: a live capture picked the
// pane's INNER line at x1936 because x1831 had fallen off the end of a `prefix(8)`). Pairs over a
// few more columns cost a few hundred more scans of a 1400-row column, which is nothing next to the
// 112ms capture they follow.
let candidates = Array(peaks.prefix(16))
    trace?("  candidates (strong columns): "
           + candidates.map { "\($0.x)(\($0.n))" }.joined(separator: " "))
    // Every rule below ends in a `why`: the choice is what `doctor` has to be able to explain, and a
    // reason string that is only written when the guard is INVERTED is a reason nobody can print.
    for i in 0..<candidates.count {
        for j in (i + 1)..<candidates.count {
            let a = min(candidates[i].x, candidates[j].x)
            let b = max(candidates[i].x, candidates[j].x)
            var rejected: String? = nil
            if b - a < 120 { rejected = "only \(b - a)px apart: too narrow to be a panel" }
            if rejected == nil && b - a > w - 40 { rejected = "touches the window's own edge" }
            if rejected == nil, ink.colInk[a] < Int32(h / 4) || ink.colInk[b] < Int32(h / 4) {
                rejected = "one edge is not a line (a column of text, not a border)"
            }
            if rejected != nil {
                trace?("  pair \(a)..\(b) → rejected: \(rejected!)")
                continue
            }
            let runA = longestInkRun(ink, a, gap: gap), runB = longestInkRun(ink, b, gap: gap)
            let top = max(runA.0, runB.0), bottom = min(runA.1, runB.1)
            let overlap = bottom - top
            // The SHAPE is part of the choice, not a check afterwards: a window whose own panel
            // borders make the biggest pair in the picture must not stop the pane behind them from
            // being looked at (measured 2026-10-07: a 47..2450 pair was the longest by overlap and
            // is the window, so the pane was never scored).
            if Double(b - a) > Double(w) * 0.6 {
                trace?("  pair \(a)..\(b) → rejected: wider than 60% of the window, so it is the "
                       + "window's own panel")
                continue
            }
            if Double(overlap) < Double(h) * 0.12 {
                trace?("  pair \(a)..\(b) → rejected: overlap is only \(overlap)px (not a column)")
                continue
            }
            // ...and with the pane's own numbers in hand, the strongest shape test there is: the
            // pane is `cols` by `rows` CELLS, so its rectangle's width has to be about `cols` cell
            // widths — a row is `overlap / rows` px tall, and a monospace cell is a little over half
            // as wide as it is tall. That is what tells a pane from the explorer sidebar, which is
            // otherwise a very convincing pair of vertical lines (measured 2026-10-07: the sidebar
            // beat the pane and put her over the file list).
            // Whether this candidate wears the pane's own slack band as BLANK — the strongest agreement
            // there is between a rectangle and the pane that published it, and a PREFERENCE rather than a
            // veto.  The band is a ROW RANGE mapped into pixels through the candidate's own rectangle, so
            // a pane whose list got long can have its published band land on rows the pixels call inked
            // (measured 2026-10-08 on the live window: the real pane was the only candidate left standing
            // and it was thrown away for it, which would have hidden her over her own pane).  What stays a
            // VETO is the blank-above test below: a panel with nothing above its slack is not a list at
            // all, and that is the case the owner photographed.
            var slackOk = true
            if let hint = shapeHint, hint.start + hint.count <= hint.rows {
                let rowPx = Double(overlap) / Double(hint.rows)
                let want = Double(hint.cols) * rowPx * 0.55
                let got = Double(b - a)
                if got < want * 0.6 || got > want * 1.7 {
                    trace?("  pair \(a)..\(b) → rejected: \(Int(got))px wide against \(Int(want))px for "
                           + "\(hint.cols) columns of a \(String(format: "%.1f", rowPx))px row")
                    continue
                }
                // ...AND the row it claims has to be a row a terminal could have. Every test above
                // is scale-free — a small rectangle with the pane's own proportions passes them all
                // — so a pair of long lines in the chat panel or the explorer sidebar was still a
                // candidate, and picking it put her in the sidebar at her smallest size (measured
                // 2026-10-07: `pane=205,794-427,1375 cell=15px`, which is a 7.5pt row, and no
                // terminal in the world is set in a seven-point font). `scale` is the window's own
                // pixels per point, so this is the ONE test that is about the picture's real size.
                let rowPt = rowPx / Double(scale)
                if rowPt < 10 || rowPt > 34 {
                    trace?("  pair \(a)..\(b) → rejected: a \(String(format: "%.1f", rowPt))pt row is "
                           + "no terminal's font")
                    continue
                }
                // ...and the pane's OWN SLACK, judged in the candidate's own pixels: the pane published
                // which of its rows are EMPTY (`start`..`start+count-1`), and a real pane shows content
                // above that slack and nothing at all in the slack itself. That is the difference
                // between the pane and a panel with a pane's shape — the preview panel is a tall blank
                // column, and its whole height reads as slack (measured 2026-10-08, the owner's
                // screenshot: she stood in the preview panel because it was the biggest pane-shaped
                // rectangle in the window). A candidate whose slack is not blank, or whose half above
                // it is blank too, is not a list: it is a hole in the window.
                if hint.count > 0 {
                    let slackTop = top + Int((Double(hint.start) * rowPx).rounded())
                    let slackBottom = top + Int((Double(hint.start + hint.count) * rowPx).rounded())
                    let step = max(3, (b - a) / 6)
                    var blankInSlack = 0, slackRows = 0, inkedAbove = 0, rowsAbove = 0
                    var y = top
                    while y < min(slackTop, bottom) {
                        rowsAbove += 1
                        if rowHasInk(ink, y, a, b, step) { inkedAbove += 1 }
                        y += 1
                    }
                    y = max(top, min(slackTop, bottom))
                    while y <= min(slackBottom, bottom) {
                        slackRows += 1
                        if !rowHasInk(ink, y, a, b, step) { blankInSlack += 1 }
                        y += 1
                    }
                    if slackRows == 0 {
                        trace?("  pair \(a)..\(b) → rejected: no slack rows of its own to judge")
                        continue
                    }
                    let blankShare = Double(blankInSlack) / Double(slackRows)
                    slackOk = blankShare >= 0.6
                    if !slackOk {
                        trace?("  pair \(a)..\(b): the pane's slack band is NOT blank "
                               + "(\(blankInSlack) of \(slackRows) rows) — kept, against a candidate "
                               + "that wears it")
                    }
                    if inkedAbove == 0 {
                        trace?("  pair \(a)..\(b) → rejected: nothing above the slack at all — "
                               + "a blank panel, not the pane")
                        continue
                    }
                }
            }
            // The pane is the BIGGEST thing in the window with this shape, not merely the one with
            // the longest pair of lines: a sidebar is tall and narrow, and choosing by run length
            // is what let one be mistaken for the pane (see the row-height test above).
            let area = (b - a) * overlap
            // ...and the order of preference: a rectangle that wears the pane's slack as blank beats one
            // that does not, whatever their sizes; between two that agree (or two that do not), the BIGGER
            // one wins, because the pane is the biggest thing in the window with this shape.
            let beats = best == nil
                || (slackOk && !best!.slackOk)
                || (slackOk == best!.slackOk && area > best!.area && overlap >= minRun)
            trace?("  pair \(a)..\(b) → kept: overlap=\(overlap)px area=\(area) slack="
                   + (slackOk ? "matches" : "does not match") + " "
                   + (beats ? "(the best so far)"
                            : "(loses to area \(best!.area), slack="
                              + (best!.slackOk ? "matches" : "does not match") + ")"))
            if beats {
                best = (a, b, top, bottom, overlap, area, slackOk)
            }
        }
    }
    guard let found = best else {
        trace?("  nothing kept: no pair of lines had a pane's shape, a real row height and its slack")
        return nil
    }
    trace?("  pane=\(found.left),\(found.top)-\(found.right),\(found.bottom) "
           + "overlap=\(found.overlap)px area=\(found.area)")
    return (found.left, found.right, found.top, found.bottom)
}

/// ONE measurement, said out loud — `fbtodo pip doctor` on a live capture, `--fit <png> --trace` on a
/// file.  One function so the two can never explain the same pixels differently: the hint she used,
/// every long column in the window, every pair of them and why each was kept or thrown away, the pane
/// that won, and where she would stand in it.  The detection runs twice (once for the trace, once for
/// the placement inside `paneAnchor`) on purpose: it is a pure function of the pixels and the hint, and
/// a one-shot verb costs nothing for the certainty that the explanation describes the placement.
func diagnose(_ shot: Screenshot, windowWidth: CGFloat,
              hint: (rows: Int, cols: Int, start: Int, count: Int)?) -> Bool {
    let ink = inkOf(shot)
    let scale = CGFloat(shot.width) / max(1, windowWidth)
    let gap = max(24, ink.height / 24)
    print("  capture=\(shot.width)x\(shot.height)px window=\(Int(windowWidth))pt "
          + "scale=\(String(format: "%.3f", Double(scale)))")
    let said = hint.map {
        "\($0.rows) rows x \($0.cols) cols, blank rows \($0.start)..\($0.start + $0.count - 1)"
    } ?? "none (no pane has published one — the pixels decide alone)"
    print("  slack hint: \(said)\n  from: \(slackFile)")
    for peak in inkPeaks(ink).prefix(16) {
        let run = longestInkRun(ink, peak.x, gap: gap)
        print("  line x=\(peak.x) ink=\(peak.n) run=\(run.0)..\(run.1) (\(run.2)px)")
    }
    guard let rect = paneRect(ink, shapeHint: hint, scale: scale, trace: { print($0) }) else {
        print("  no pane: nothing in this window has a pane's shape, a terminal's row height and its "
              + "slack at once")
        return false
    }
    // Same order as every other pane rectangle this program prints: left, TOP, right, bottom.
    print("  pane (px)=\(rect.0),\(rect.2)-\(rect.1),\(rect.3)")
    // ...and where inside it she would stand, worked out by the same code the window runs. NOT the
    // verbose drawing of that code: the raw blank-block dump belongs to `--fit-verbose`, and a doctor's
    // output is read by a person — what matters here is the rectangle she is given and whether she fits.
    guard let fit = paneAnchor(shot, windowWidth: windowWidth, side: side) else {
        print("  no SLOT in that rectangle: she would be hidden rather than shown outside it")
        return false
    }
    let pane = fit.pane, slot = fit.slot
    print("  pane (pt)=\(Int(pane.origin.x)),\(Int(pane.origin.y)) "
          + "\(Int(pane.width))x\(Int(pane.height)) "
          + "slot=\(Int(slot.origin.x)),\(Int(slot.origin.y)) "
          + "\(Int(slot.width))x\(Int(slot.height))"
          + (fit.hidden ? " hidden=yes (the pane has no room for her)" : ""))
    print("  fit=\(fit.note)")
    print("  anchor=\(Int(fit.origin.x.rounded())),\(Int(fit.origin.y.rounded())) "
          + "size=\(Int(fit.size)) inside=" + (fit.inside ? "yes" : "no"))
    return fit.inside
}

/// ONE measurement, silent: the rectangle `paneRect` picked out of the pixels and the whole answer
/// `paneAnchor` gives for it (the pane in the window's own points, her square in it, whether she is
/// inside it). `diagnose` — the single-shot verb — makes these same two calls with the trace on, and
/// keeping the answer apart from the explanation is what lets a WATCH print sixty records without
/// sixty explanations.
func measure(_ shot: Screenshot, windowWidth: CGFloat,
             hint: (rows: Int, cols: Int, start: Int, count: Int)?
) -> (fit: Fit, panePx: (Int, Int, Int, Int))? {
    let scale = CGFloat(shot.width) / max(1, windowWidth)
    guard let rect = paneRect(inkOf(shot), shapeHint: hint, scale: scale) else { return nil }
    guard let fit = paneAnchor(shot, windowWidth: windowWidth, side: side) else { return nil }
    return (fit, rect)
}

/// Her place, in the words a record uses: the pane in capture pixels, then her square in the window's
/// own points and whether it is inside the pane — the same numbers the single-shot doctor prints.
/// The pane is printed left, TOP, right, bottom — the order her own `fit=` line has always used (and
/// the one a rectangle is easiest to read as).  `paneRect` hands it back left, right, top, bottom,
/// because that is how the pair of border columns is found; the swap is here so one verb's `pane=` can
/// be compared with the other verb's without a conversion in the reader's head.
func paneWords(_ fit: Fit, _ panePx: (Int, Int, Int, Int)) -> String {
    "pane=\(panePx.0),\(panePx.2)-\(panePx.1),\(panePx.3) "
    + "anchor=\(Int(fit.origin.x.rounded())),\(Int(fit.origin.y.rounded())) "
    + "size=\(Int(fit.size)) inside=\(fit.inside ? "yes" : "no") "
    + "slot=\(Int(fit.slot.origin.x)),\(Int(fit.slot.origin.y)) "
    + "\(Int(fit.slot.width))x\(Int(fit.slot.height))"
}

/// What changed between two samples, and nothing when nothing did: her place IS the pane's, so a hop is
/// a change of the pane rectangle, of her square in it, or of the window they were read out of.  The
/// same pixels measured twice are the same answer, and a watch that reported those would report once a
/// second — which is precisely the noise that would hide the hop it exists to catch.
func placementChanged(_ before: (fit: Fit, panePx: (Int, Int, Int, Int), context: String),
                      _ after: (fit: Fit, panePx: (Int, Int, Int, Int), context: String)) -> String {
    var what: [String] = []
    if before.panePx != after.panePx { what.append("pane") }
    if before.fit.pane != after.fit.pane { what.append("pane-pt") }
    if before.fit.origin != after.fit.origin || before.fit.size != after.fit.size {
        what.append("anchor")
    }
    if before.context != after.context { what.append("window") }
    return what.joined(separator: "+")
}

/// How far, in points, a fresh reading of the pane may sit from the SAVED one on any edge before it is taken
/// as a half-drawn frame rather than the pane moving. A border is about this wide; the hop that set the number
/// (2026-10-08) moved the pane's left edge ~99pt, which is twelve borders, so the line sits well clear of both.
let paneBorderPt: CGFloat = 8

/// Whether two readings of the same pane disagree by more than a border on any one edge.
func paneDisagrees(_ a: CGRect, _ b: CGRect) -> Bool {
    abs(a.minX - b.minX) > paneBorderPt || abs(a.maxX - b.maxX) > paneBorderPt
        || abs(a.minY - b.minY) > paneBorderPt || abs(a.maxY - b.maxY) > paneBorderPt
}

/// The rule that stops a hop, shared by the live fit and the watch. A reading that disagrees with the SAVED
/// pane by more than a border is HELD: it is not written, and the saved placement stays. It is accepted only
/// when the NEXT reading agrees with it. A frame caught half-drawn is followed by one back on the saved pane
/// (the held reading is dropped); a pane that really moved gives two readings in a row that say so, and she
/// follows. Returns whether to accept the fresh reading, and the reading to remember as pending.
func settle(kept: CGRect?, pending: CGRect?, fresh: CGRect) -> (accept: Bool, pending: CGRect?) {
    guard let kept = kept, paneDisagrees(kept, fresh) else { return (true, nil) }
    if let pending = pending, !paneDisagrees(pending, fresh) { return (true, nil) }
    return (false, fresh)
}

/// A pane's edges in window points, as one word for a line: left,top-right,bottom.
func pointsWords(_ r: CGRect) -> String {
    "\(Int(r.minX.rounded())),\(Int(r.minY.rounded()))-\(Int(r.maxX.rounded())),\(Int(r.maxY.rounded()))"
}

/// A number as a JSON value: a tenth of a point is finer than any pixel she is drawn at.
func ptRound(_ v: CGFloat) -> Double { (Double(v) * 10).rounded() / 10 }

/// One measurement the watch keeps: the placement, the window's own pixels and what it was called.
typealias WatchReading = (fit: Fit, panePx: (Int, Int, Int, Int), context: String)

/// `--json` on a watch: one object per sample on stdout, and nothing else there (the summary goes to stderr).
func watchJSON() -> Bool { args.contains("--json") }

/// `--stop-on-change` on a watch: the first accepted change ends the watch and is filed as evidence.
func watchStopOnChange() -> Bool { args.contains("--stop-on-change") }

let isoClock: ISO8601DateFormatter = {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return f
}()

/// One reading as JSON: the clock, the pane as [left, top, right, bottom] in window points, her square's
/// top-left and size, whether she is inside, and the window it was read from. `null` where there is none.
func readingObject(_ reading: WatchReading?, at: Date) -> [String: Any] {
    guard let r = reading else {
        return ["t": isoClock.string(from: at), "pane": NSNull(), "anchor": NSNull(), "size": NSNull(),
                "inside": NSNull(), "window": NSNull()]
    }
    let p = r.fit.pane
    return ["t": isoClock.string(from: at),
            "pane": [ptRound(p.minX), ptRound(p.minY), ptRound(p.maxX), ptRound(p.maxY)],
            "anchor": [ptRound(r.fit.origin.x), ptRound(r.fit.origin.y)],
            "size": ptRound(r.fit.size),
            "inside": r.fit.inside,
            "window": r.context]
}

/// One sample as one JSON object on a single line: the reading plus its `state` (start, same, changed, held,
/// no-pane, refused), the change that moved it (`changed`) and a note for the states that have no reading.
func watchJSONLine(_ state: String, at now: Date, since started: Date, _ reading: WatchReading?,
                   changed: String = "", note: String = "") -> String {
    var object = readingObject(reading, at: now)
    object["elapsed_s"] = ptRound(CGFloat(now.timeIntervalSince(started)))
    object["state"] = state
    object["changed"] = changed.isEmpty ? NSNull() as Any : changed
    object["note"] = note.isEmpty ? NSNull() as Any : note
    guard let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]),
          let text = String(data: data, encoding: .utf8) else { return "{}" }
    return text
}

/// One sample, said once: the line a reader sees, or with `--json` the object a chart reads — never both on
/// stdout. A nil `human` is a sample that says nothing new to a reader (a repeat, or a refusal already reported).
func emitWatch(_ json: Bool, human: String?, object: String) {
    if json { print(object) } else if let human = human { print(human) }
    fflush(stdout)
}

/// Whatever `body` prints to stdout, written to `path` instead: `diagnose` explains by printing, and the filed
/// evidence needs the same words in a file. The descriptor is put back whatever `body` does.
func stdoutText(_ path: String, _ body: () -> Void) {
    fflush(stdout)
    let saved = dup(1)
    let fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0o600)
    if fd >= 0 { dup2(fd, 1); close(fd) }
    body()
    fflush(stdout)
    if saved >= 0 { dup2(saved, 1); close(saved) }
}

/// `--stop-on-change`'s evidence: both captures either side of the hop, the explanation of each (the words
/// `--doctor` prints), the readings and the hint they were measured with — filed under
/// `~/.cache/fbtodo/pip-hops/<stamp>/`, so an intermittent hop arrives with everything needed to fix it rather
/// than as a line to reconstruct it from. Returns the directory, or nil when it could not be made.
func fileHop(before: (shot: Screenshot, width: CGFloat, reading: WatchReading, at: Date),
             after: (shot: Screenshot, width: CGFloat, reading: WatchReading, at: Date),
             changed: String) -> String? {
    let stamp = DateFormatter()
    stamp.dateFormat = "yyyyMMdd-HHmmss-SSS"
    // `FBTODO_PIP_HOPS` moves the filing (the suite files its hops under its own home, not the owner's cache).
    let hopsRoot = ((ProcessInfo.processInfo.environment["FBTODO_PIP_HOPS"] ?? "~/.cache/fbtodo/pip-hops") as NSString)
        .expandingTildeInPath
    let dir = hopsRoot + "/" + stamp.string(from: Date())
    do {
        try FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
    } catch {
        FileHandle.standardError.write("buffy-pip: cannot file the hop in \(dir): \(error)\n".data(using: .utf8)!)
        return nil
    }
    _ = writePNG(before.shot, to: dir + "/before.png")
    _ = writePNG(after.shot, to: dir + "/after.png")
    stdoutText(dir + "/explain-before.txt") {
        _ = diagnose(before.shot, windowWidth: before.width, hint: readSlackHint())
    }
    stdoutText(dir + "/explain-after.txt") {
        _ = diagnose(after.shot, windowWidth: after.width, hint: readSlackHint())
    }
    var hint: Any = NSNull()
    if let h = readSlackHint() { hint = ["rows": h.rows, "cols": h.cols, "start": h.start, "count": h.count] }
    let readings: [String: Any] = [
        "changed": changed,
        "before": readingObject(before.reading, at: before.at),
        "after": readingObject(after.reading, at: after.at),
        "hint": hint,
    ]
    if let data = try? JSONSerialization.data(withJSONObject: readings, options: [.sortedKeys, .prettyPrinted]) {
        _ = try? data.write(to: URL(fileURLWithPath: dir + "/readings.json"))
    }
    return dir
}

/// `--watch <seconds>`: the duration the doctor samples for. `nil` when it was not asked to watch, and a
/// refused value is a usage error rather than a default — `--watch 0` is a watch that reports nothing,
/// which is worse than being told so. Bare `--watch` is the minute the owner asked for.
func watchSeconds() -> Double? {
    guard args.contains("--watch") || args.contains(where: { $0.hasPrefix("--watch=") }) else {
        return nil
    }
    let raw = option("--watch") ?? ""
    let seconds = raw.isEmpty ? 60 : (Double(raw) ?? -1)
    guard seconds > 0, seconds <= 3600 else {
        FileHandle.standardError.write(
            ("buffy-pip: --watch wants seconds between 0 and 3600, not "
             + "\(raw.isEmpty ? "(nothing)" : raw)\n").data(using: .utf8)!)
        exit(64)                        // EX_USAGE
    }
    return seconds
}

/// ...and the watch itself: ONE measurement taken again and again, reporting only what CHANGES.  A hop
/// is intermittent by nature — a single capture catches an answer and never the jump — so the record is
/// the state it starts in (`start`), one line per change with the wall clock and BOTH readings, and a
/// summary that counts them.  A refusal, or a capture with no pane-shaped thing in it, is reported when
/// it STARTS rather than once a second, and counted in the summary: a line per second would bury the
/// change the verb is about.
/// The exit code is the gate the request implied — 0 when every sample found her inside the pane, 1 when
/// any sample did not (so an intermittent hop makes the command FAIL rather than merely say so), and 1 as
/// well when a capture came back with no pane-shaped thing in it (the single-shot verb's own answer to
/// that).  69 is narrower: NO capture came back at all, which is a permission or a timeout rather than a
/// placement.  66 is the verb's answer to having no window to look at in the first place.
func watchRun(seconds: Double,
              sample: () -> (shot: Screenshot, width: CGFloat, context: String)?) -> Int32 {
    let clock = DateFormatter()
    clock.dateFormat = "HH:mm:ss"
    let started = Date()
    let end = started.addingTimeInterval(seconds)
    let json = watchJSON()
    let stopAtChange = watchStopOnChange()
    // The SAVED placement (where she would be standing) with its capture and time: a filed hop needs both
    // sides. Only an accepted reading moves them (see `settle`); a half-drawn frame is held.
    var kept: WatchReading? = nil
    var keptCapture: (shot: Screenshot, width: CGFloat, at: Date)? = nil
    var pending: CGRect? = nil
    var samples = 0, changes = 0, held = 0, outside = 0, refused = 0, noPane = 0
    var failing = ""                     // which failure is already being reported
    // The summary, said once: a line on stdout, or on stderr under `--json` so stdout is only the objects.
    func finish(_ code: Int32, filed: String?) -> Int32 {
        let at = String(format: "%.1fs", Date().timeIntervalSince(started))
        var line = " watch=\(at) at=\(clock.string(from: Date())) samples=\(samples) changes=\(changes)"
        line += " held=\(held) outside=\(outside) refused=\(refused) no-pane=\(noPane)"
        if let last = kept { line += " " + paneWords(last.fit, last.panePx) }
        if let filed = filed { line += " filed=\(filed)" }
        if json {
            FileHandle.standardError.write((line + "\n").data(using: .utf8)!)
        } else {
            print(line)
        }
        fflush(stdout)
        return code
    }
    while true {
        if let got = sample() {
            // The clock is read AFTER the sample, not before: a capture costs a fraction of a second
            // (an unoptimised build of this over a big file took SEVEN, measured 2026-10-08), and a
            // record stamped before its own measurement would name a time the placement was not true at.
            let now = Date()
            let elapsed = String(format: "%.1fs", now.timeIntervalSince(started))
            let stamp = "watch=\(elapsed) at=\(clock.string(from: now))"
            if let place = measure(got.shot, windowWidth: got.width, hint: readSlackHint()) {
                let here: WatchReading = (place.fit, place.panePx, got.context)
                samples += 1
                let sameWindow = kept?.context == got.context
                let decision = settle(kept: sameWindow ? kept?.fit.pane : nil,
                                      pending: pending, fresh: place.fit.pane)
                pending = decision.pending
                if !decision.accept {
                    held += 1
                    failing = ""
                    let saved = kept?.fit.pane ?? place.fit.pane
                    emitWatch(json, human: stamp + " held pane=\(pointsWords(place.fit.pane)) kept=\(pointsWords(saved))"
                              + " — more than a border (\(Int(paneBorderPt))pt) on one edge, so the saved"
                              + " placement stays until the next reading agrees",
                              object: watchJSONLine("held", at: now, since: started, here))
                } else {
                    failing = ""
                    outside += place.fit.inside ? 0 : 1
                    if let was = kept, sameWindow {
                        let moved = placementChanged(was, here)
                        if moved.isEmpty {
                            emitWatch(json, human: nil,
                                      object: watchJSONLine("same", at: now, since: started, here))
                        } else {
                            changes += 1
                            emitWatch(json, human: stamp + " changed=\(moved) " + paneWords(here.fit, here.panePx)
                                      + " was " + paneWords(was.fit, was.panePx),
                                      object: watchJSONLine("changed", at: now, since: started, here,
                                                            changed: moved))
                            if stopAtChange, let before = keptCapture {
                                // The first hop ends the watch, and what it saw is FILED (see `fileHop`).
                                let filed = fileHop(before: (shot: before.shot, width: before.width, reading: was, at: before.at),
                                                    after: (shot: got.shot, width: got.width, reading: here, at: now),
                                                    changed: moved)
                                return finish(1, filed: filed ?? "(not filed: see stderr)")
                            }
                        }
                    } else {
                        emitWatch(json, human: stamp + " start \(got.context) " + paneWords(here.fit, here.panePx),
                                  object: watchJSONLine("start", at: now, since: started, here))
                    }
                    kept = here
                    keptCapture = (shot: got.shot, width: got.width, at: now)
                }
            } else {
                noPane += 1
                emitWatch(json, human: failing != "no-pane"
                          ? stamp + " no pane: nothing in this window has a pane's shape, a terminal's row height and its slack at once"
                          : nil,
                          object: watchJSONLine("no-pane", at: now, since: started, nil, note: "no pane"))
                failing = "no-pane"
            }
        } else {
            let now = Date()
            refused += 1
            let stamp = "watch=\(String(format: "%.1fs", now.timeIntervalSince(started))) at=\(clock.string(from: now))"
            emitWatch(json, human: failing != "refused"
                      ? stamp + " refused: no capture (the attempts above say whether it is permission, a missing window, or a timeout)"
                      : nil,
                      object: watchJSONLine("refused", at: now, since: started, nil, note: "refused"))
            failing = "refused"
        }
        if Date() >= end { break }
        usleep(600_000)                   // plus the capture itself: about the clock her own fit runs on
    }
    // ...and the code: nothing came back at all is 69 (a refused capture, not a placement), anything
    // else that is not a clean run of insides is 1 — the same answer the single-shot verb gives for a
    // capture with no pane in it, so the two cannot disagree about the same pixels.
    if samples == 0 && refused > 0 && noPane == 0 { return finish(69, filed: nil) }
    return finish(samples == 0 ? 1 : (outside > 0 || noPane > 0 ? 1 : 0), filed: nil)
}

/// Where in the pane she stands: the centre of its LAST blank block — the space the ASCII picture
/// used to stand in, above the state row — or the pane's own middle when the pane has no blank
/// block left to stand in (a list that fills it).
/// The pane's grid, as the pane published it: rows, columns, the first row of its slack, and how
/// many rows that is. `nil` when the pane has not written one — then the pixels are all there is.
/// (The pane's own file, written by `note_pane_slack` on every frame whose numbers changed.)
func readSlackHint() -> (rows: Int, cols: Int, start: Int, count: Int)? {
    guard let text = try? String(contentsOfFile: slackFile, encoding: .utf8) else { return nil }
    let parts = text.split(whereSeparator: { ", \n\t".contains($0) })
    guard parts.count >= 4, let rows = Int(parts[0]), let cols = Int(parts[1]),
          let start = Int(parts[2]), let count = Int(parts[3]),
          rows > 2, cols > 2, start >= 0, count >= 0 else { return nil }
    return (rows, cols, start, count)
}

/// The smallest she is drawn at all: below this she is a smudge, and a pane that cannot hold even
/// this much of her gets none of her over the list it filled up with (see `Fit.hidden`).
let pipMinSide: CGFloat = 48

/// Where she stands and how big she is, in the HOST WINDOW's own points: the top-left of her
/// square of `size`, the pane she belongs to, and the empty rows she was placed in.
struct Fit {
    var origin: CGPoint
    var size: CGFloat
    var pane: CGRect
    var slot: CGRect
    var note: String
    /// True when the pane has no room for her: she is ordered out until it has some.
    var hidden: Bool = false

    /// The square she actually occupies, in the window's own points.
    var rect: CGRect {
        CGRect(origin: origin, size: CGSize(width: size, height: size))
    }

    /// All of her inside the pane's borders — the user's requirement (2026-10-07: "i want it to be
    /// a part of the pane and not showing outside of the pane"), and what the suite checks against
    /// a real capture rather than against this code's own arithmetic.
    var inside: Bool { pane.contains(rect) }
}

func clamp(_ value: CGFloat, _ low: CGFloat, _ high: CGFloat) -> CGFloat {
    min(max(value, low), high)
}

/// Where in the pane she stands, how big, and which empty rows she is standing in.
func paneAnchor(_ shot: Screenshot, windowWidth: CGFloat, side: CGFloat, verbose: Bool = false)
    -> Fit? {
    let ink = inkOf(shot)
    let hint = readSlackHint()
    let pixelScale = CGFloat(ink.width) / max(1, windowWidth)
    guard let (left, right, top, bottom) = paneRect(ink, shapeHint: hint, scale: pixelScale),
          right - left >= 120 else { return nil }
    // Blank judged INSIDE the borders, and by STRUCTURE rather than by colour: a pane whose
    // background is transparent (the app paints a wallpaper through it) has no single background
    // colour to compare against, so a blank row is one where nothing has a pair of edges — which is
    // what a glyph is and what a gradient is not.
    var blank = [Bool](repeating: true, count: ink.height)
    let fw = ink.width
    for y in top...bottom {
        let base = y * fw * 4
        var structured = false
        var x = left + 4
        while x + 3 <= right - 4 {
            let at = base + x * 4, next = base + (x + 3) * 4
            let d = max(abs(Int(shot.px[at]) - Int(shot.px[next])),
                        max(abs(Int(shot.px[at + 1]) - Int(shot.px[next + 1])),
                            abs(Int(shot.px[at + 2]) - Int(shot.px[next + 2]))))
            if d > 30 { structured = true; break }
            x += 1
        }
        blank[y] = !structured
    }
    var blocks: [(Int, Int)] = []
    var y = top
    while y <= bottom {
        if blank[y] {
            let start = y
            while y <= bottom && blank[y] { y += 1 }
            blocks.append((start, y - 1))
        } else {
            y += 1
        }
    }
    // At least a cell tall (a cell is around 22 px on a Retina pane): the frame's own slack, which
    // is where the picture panel drew. Shorter gaps are the padding between rows, not a space.
    let big = blocks.filter { $0.1 - $0.0 >= 20 }
    if verbose {
        print("  blank blocks (px, all of them): "
              + blocks.map { "\($0.0)..\($0.1)" }.joined(separator: " "))
        print("  of which at least a cell tall: "
              + big.map { "\($0.0)..\($0.1)" }.joined(separator: " "))
        print("  slack hint: \(String(describing: hint)) from \(slackFile)")
    }
    let scale = CGFloat(ink.width) / max(1, windowWidth)
    let pane = CGRect(x: CGFloat(left) / scale, y: CGFloat(top) / scale,
                      width: CGFloat(right - left) / scale,
                      height: CGFloat(bottom - top + 1) / scale)
    // THE PANE'S OWN NUMBERS FIRST: it knows how tall its grid is and how many rows its frame
    // filled, so its slack is a range of rows, not a guess about blank pixels — and the frame's
    // pixel height over that row count is the cell height, which is what turns the range into a
    // place. The sanity check is that the two agree: a hint whose rows would make a cell outside
    // any real terminal font is a hint from another pane (or an older one) and is ignored.
    let frameRows = CGFloat(bottom - top + 1)
    var slotTop: CGFloat? = nil, slotBottom: CGFloat? = nil
    var note = "pane=\(left),\(top)-\(right),\(bottom)"
    // A hint is only used when it agrees with the frame twice over: the rows it names have to FIT
    // the row count it names (a pane two panes ago published a slack starting below its own grid),
    // and the cell size that falls out of the two has to be a real font's.
    if let hint, hint.count > 0, hint.start + hint.count <= hint.rows {
        let cell = frameRows / CGFloat(hint.rows)
        if cell >= 6, cell <= 44 {
            slotTop = (CGFloat(top) + CGFloat(hint.start) * cell) / scale
            slotBottom = (CGFloat(top) + CGFloat(hint.start + hint.count) * cell) / scale
            note += " slack=rows \(hint.start)..\(hint.start + hint.count - 1) of \(hint.rows)"
                + " cell=\(Int(cell))px"
        }
    }
    if slotTop == nil, let last = big.last {
        slotTop = CGFloat(last.0) / scale
        slotBottom = CGFloat(last.1 + 1) / scale
        note += " blank=\(last.0)..\(last.1)"
    }
    if slotTop == nil { note += " blank=none" }
    let slot = CGRect(x: pane.minX, y: slotTop ?? pane.minY, width: pane.width,
                      height: (slotBottom ?? pane.maxY) - (slotTop ?? pane.minY))
    // SHE IS PART OF THE PANE, NOT SOMETHING OVER IT: her square is FITTED to the empty rows — eight
    // rows of a 33px cell is 134pt against her 201 — and then CLAMPED, so that no edge of her
    // crosses the pane's own border whatever the slot said. `room` is the pane without its border
    // glyphs, which is what "inside the pane" means to the eye.
    let margin: CGFloat = 2
    let room = pane.insetBy(dx: margin, dy: margin)
    var size = min(side, min(slot.width, max(0, slot.height)) - 2 * margin)
    size = min(size, max(8, min(room.width, room.height)))
    // ...and a whole number of ART PIXELS (see `artUnit`): the largest exact scale that still fits
    // the room, so she is never drawn at a fractional one.
    let roomSide = max(8, min(room.width, room.height))
    size = min(artSnapped(size), roomSide)
    let originX = clamp(slot.midX - size / 2, room.minX, max(room.minX, room.maxX - size))
    let originY = clamp(slot.midY - size / 2, room.minY, max(room.minY, room.maxY - size))
    if verbose {
        print("  pane (pt)=\(pane) slot (pt)=\(slot) room (pt)=\(room) size=\(Int(size))")
    }
    // ...and the pane decides whether she is here at all. A pane with no room for her — a list that
    // filled the frame, so it published a slack of nothing — gets none of her over it: she STEPS
    // ASIDE until there is room again (the user's words, 2026-10-07: "give her space so she doesn't
    // block any information"). The window is hidden rather than shrunk to a pixel, so coming back
    // costs one order-in and nothing else. The pixel-only path — no hint at all, a machine whose
    // panes never published a grid — is left at her own size in the pane's middle instead: hiding
    // her because a hint file is missing would look like a broken window, not a full pane.
    let hidden = slotTop != nil && size < pipMinSide
    return Fit(origin: CGPoint(x: originX, y: originY), size: max(1, size), pane: pane, slot: slot,
               note: note + " size=\(Int(max(1, size)))" + (hidden ? " hidden=yes" : ""),
               hidden: hidden)
}

// The latest measurement, taken on its own queue so a 200ms capture never stutters her animation,
// and stamped with the window it was taken in so one window's pane is never applied to another.
let fitLock = NSLock()
var fitHost: Host? = nil
var fitNote = "none yet"
var fitValue: Fit? = nil
// ...and the last measurement that actually FOUND a pane, with the window NUMBER it was taken in.
// Kept BECAUSE THE LATEST ONE CAN FAIL: a capture of a minimized window, a window that has not
// redrawn yet, a pane mid-redraw — and a failed attempt that overwrites the only placement she has
// is a buffy who vanishes on a minimize and comes back a few seconds later, which is precisely what
// the owner saw (2026-10-07).  The geometry she already had for that window stands until a new one
// replaces it; only a DIFFERENT window (a resize, another app) discards it — see `measuredFit`.
var lastGoodNumber = 0
/// ...and the SAME window's rectangle when that measurement was taken. Its SIZE is what decides whether
/// the saved placement may still be used at all: a fit is window-relative, so a window that only MOVED
/// has its pane exactly where it was, while a RESIZED one (the app opens its preview panel, the reader
/// drags an edge) has a pane that is elsewhere — and a stale fit there is her standing at the old pane's
/// coordinates, which is the owner's "sometimes it still appear out of the fbtodo pane" (2026-10-08).
var lastGoodRect = CGRect.zero
var lastGoodFit: Fit? = nil

func readFit() -> (host: Host?, fit: Fit?, note: String) {
    fitLock.lock()
    defer { fitLock.unlock() }
    return (fitHost, fitValue, fitNote)
}

func readLastGood() -> (number: Int, rect: CGRect, fit: Fit?) {
    fitLock.lock()
    defer { fitLock.unlock() }
    return (lastGoodNumber, lastGoodRect, lastGoodFit)
}

func storeLastGood(_ host: Host, _ fit: Fit) {
    fitLock.lock()
    lastGoodNumber = host.number
    lastGoodRect = host.rect
    lastGoodFit = fit
    fitLock.unlock()
}

func storeFit(_ host: Host, _ fit: Fit?, _ note: String) {
    fitLock.lock()
    fitHost = host
    fitValue = fit
    fitNote = note
    fitLock.unlock()
}

func measureFit(_ host: Host) {
    guard let shot = captureWindow(host.number) else {
        storeFit(host, nil, "no screenshot")
        print("buffy-pip fit=no screenshot of window \(host.number)")
        fflush(stdout)
        return
    }
    // `FBTODO_PIP_DUMP=<png>` writes every measurement's own capture there as well. It is the only
    // way to look at what the LIVE path actually captured when its numbers disagree with a
    // one-shot's (measured 2026-10-07: the same window, the same size, the pane 57.5pt apart), and
    // a capture nothing can be compared with is a capture nobody can check.
    if let dump = ProcessInfo.processInfo.environment["FBTODO_PIP_DUMP"] {
        _ = writePNG(shot, to: dump)
    }
    guard let found = paneAnchor(shot, windowWidth: host.rect.width, side: side) else {
        storeFit(host, nil, "no pane found in a \(shot.width)x\(shot.height) capture")
        // Said out loud, and once per attempt: a REFUSAL is the interesting case — it means she is
        // still where she was because nothing in the window had the shape of a pane, and that has
        // to be visible from outside or the difference between "no pane" and "not measured" is
        // invisible.
        print("buffy-pip fit=refused \(shot.width)x\(shot.height) capture of window "
              + "\(host.number)")
        fflush(stdout)
        return
    }
    let before = readFit()
    // A HALF-DRAWN FRAME is not a move: a fresh pane that disagrees with the saved one by more than a border is
    // held, and the saved placement stays until the next reading agrees (see `settle`). The log that showed this
    // (2026-10-08) had the pane alternate between two rectangles, and her window with it.
    let kept = before.host?.sameWindow(as: host) == true ? before.fit?.pane : nil
    let decision = settle(kept: kept, pending: livePending, fresh: found.pane)
    livePending = decision.pending
    if !decision.accept {
        print("buffy-pip held pane=\(pointsWords(found.pane)) kept=\(pointsWords(kept ?? found.pane)) "
              + "(more than a border on one edge; the saved placement stays until the next reading agrees)")
        fflush(stdout)
        return
    }
    // The capture's own size rides along in the note: the pixels she is placed by are only
    // comparable with the window's rectangle if the image IS the window's rectangle, and a capture
    // that came back 57.5pt shorter than the window (measured 2026-10-07, live against one-shot)
    // has to say so out loud rather than place her by the wrong origin.
    let noted = found.note + " cap=\(shot.width)x\(shot.height)"
    storeFit(host, found, noted)
    storeLastGood(host, found)
    // One line per MEASUREMENT that moved her — or answered for the first time: this is the only
    // way anything outside this process can see where she decided to stand, and it is what the
    // suite reads when it asks her to measure a screenshot rather than a screen. A move is a move
    // of her SQUARE: size counts, or a pane that got taller while the same corner stayed put would
    // never be redrawn at the size that fits it now.
    let moved = before.fit == nil || abs(before.fit!.origin.x - found.origin.x) > 2
        || abs(before.fit!.origin.y - found.origin.y) > 2
        || abs(before.fit!.size - found.size) > 0.5
    if moved {
        print("buffy-pip fit=\(noted) anchor=\(Int(found.origin.x)),\(Int(found.origin.y))")
        fflush(stdout)
    }
}

let names = ((try? FileManager.default.contentsOfDirectory(atPath: frameDir)) ?? [])
    .filter { $0.lowercased().hasSuffix(".png") }
    .sorted()
let frames = names.compactMap { NSImage(contentsOfFile: frameDir + "/" + $0) }
guard !frames.isEmpty else {
    FileHandle.standardError.write("buffy-pip: no frames in \(frameDir)\n".data(using: .utf8)!)
    exit(66)                        // EX_NOINPUT: the one thing this program could not find
}
// Her table as the override file has it, read before the first frame is drawn and again on every
// pin tick that finds the file changed (see `applyMoodOverrides`). Note the ORDER: the overrides
// need to know how many frames are on disk, so this waits for them rather than the other way round.
moodsStamp = fileStamp(moodsFile)
_ = applyMoodOverrides(framesOnDisk: frames.count)
// ...and the tune file is stamped here rather than on the first tick, so a launch does not report a change
// that happened before it started (`applyTune` itself has already run, above).
linesStamp = fileStamp(linesFile)
_ = applyLineOverrides()
tuneStamp = fileStamp(tuneFile)

// ONE ART PIXEL, in points, and the sizes that are a whole number of them.  The frames are 128
// pixels square (the owner's drawing at the size it exists) and a window on this display is backed
// at 2x, so a 64pt square draws her with exactly one screen pixel per art pixel, 128pt is two, 192pt
// three.  Anything between them — the 201pt she was drawn at before this — resamples the art at a
// fraction like 3.125, which is what "increase the quality of the pixel" was about (2026-10-07):
// the panes' rows decide her size (see `paneAnchor`), and this makes every size she can have an
// exact one.
let backingScale = max(1, (NSScreen.main ?? NSScreen.screens.first)?.backingScaleFactor ?? 2)
let artPixels = max(8, frames[0].size.width)
let artUnit = max(8, artPixels / backingScale)

/// The largest size at or below `size` that is a whole number of art pixels, never less than one.
func artSnapped(_ size: CGFloat) -> CGFloat {
    max(artUnit, (size / artUnit).rounded(.down) * artUnit)
}

/// How many DEVICE pixels a square of `points` covers on this display — the size her art has to be
/// rendered at for the screen to have nothing left to scale (192pt at 2x is 384px).
func deviceSide(for points: CGFloat) -> Int {
    max(8, Int((points * backingScale).rounded()))
}

let ciContext = CIContext(options: [.useSoftwareRenderer: false, .cacheIntermediates: false])

/// One frame at a given pixel size, through a real resampler instead of the screen's.
///
/// A 128px drawing shown at 384 device pixels is a 3x upscale, and the question is only which filter
/// does it: the window server's own is a bilinear pass tuned for photographs, which is where the
/// softness the owner saw comes from ("still a bit low resolution", 2026-10-07).  Lanczos is the
/// standard windowed-sinc resampler — it keeps the edges of line-art edges instead of smearing them
/// over two pixels — and a light unsharp mask puts back the little bit of contrast the interpolation
/// still costs.  Both are LIGHT on purpose: the art is a drawing with a thin dark outline, and a
/// sharpener turned up on line-art grows halos along that outline, which reads worse than soft.  The
/// radius is in DEVICE pixels (1.4 is a smile wide on a 384px square), the intensity 0.5 is half a
/// stop of contrast at the edges and nothing anywhere flat.
func renderedArt(_ frame: NSImage, pixels: Int) -> CGImage? {
    guard let source = frame.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        return nil
    }
    var image = CIImage(cgImage: source)
    if pixels != source.width, let lanczos = CIFilter(name: "CILanczosScaleTransform") {
        lanczos.setValue(image, forKey: kCIInputImageKey)
        lanczos.setValue(CGFloat(pixels) / CGFloat(source.width), forKey: kCIInputScaleKey)
        lanczos.setValue(1.0, forKey: kCIInputAspectRatioKey)
        if let out = lanczos.outputImage { image = out }
    }
    // ...and only when something was actually UPSCALED: at her 384px frames she is drawn 1:1 with
    // the art, and an unsharp mask on art that was never softened does not make it sharper, it makes
    // it ring (a downscale is the other case where there is nothing to put back).
    if pixels > source.width, sharpIntensity > 0, let unsharp = CIFilter(name: "CIUnsharpMask") {
        unsharp.setValue(image, forKey: kCIInputImageKey)
        unsharp.setValue(sharpRadius, forKey: kCIInputRadiusKey)
        unsharp.setValue(sharpIntensity, forKey: kCIInputIntensityKey)
        if let out = unsharp.outputImage { image = out }
    }
    let box = CGRect(x: 0, y: 0, width: CGFloat(pixels), height: CGFloat(pixels))
    return ciContext.createCGImage(image, from: box)
}

final class PipView: NSView {
    var frames: [NSImage] = []
    var index = 0
    /// Which of `frames` her current MOOD wears, in order, and how long each one lasts.  Empty is
    /// "no mood told": her frames then run in their own order on the window's own tick, which is
    /// what she did before the pane started publishing a mood.  `cycleMs == 0` is a mood that HOLDS
    /// (see `moodCycles`) — a failure is over by the time it is drawn, so she stops moving for it.
    var cycle: [Int] = []
    var cycleMs: Double = 0
    private var step = 0
    /// The pane's own word for what she is doing, kept beside the cycle it was turned into: the balloon
    /// she says her lines inside is SHAPED by it (see `bubbleShapes`), so the words in her mouth and the
    /// shape around them are the same emotion rather than two features that happen to share a window.
    private var moodName = "work"
    /// Whether the balloon she is wearing right now is the SURPRISE one rather than her mood's, and until
    /// when: the words are the same kind of thing, the shape and the colour are not (see `surpriseShape`),
    /// and it lasts exactly as long as the line it arrived with.
    private var surpriseUntil = Date.distantPast
    private var surpriseOn: Bool { Date() < surpriseUntil }

    /// The sticker a surprise borrowed, as the number `assets/buffy` gives it, or nil: the log names it,
    /// which is what makes "a pose from outside her mood" something a reader can check afterwards.
    private var surprisePose = -1
    var surpriseFrame: Int? { surprisePose >= 0 ? surprisePose + 1 : nil }
    /// Whether a surprise has run out while the picture it borrowed is still on her — the fine clock's cue
    /// to put her own back (see `endSurprise`).
    var surpriseExpired: Bool { surprisePose >= 0 && !surpriseOn }

    /// Put the surprise on: WHETHER she gets one, and which line comes with it, is the pin tick's call (see
    /// `surpriseChance`) — this is the wearing, and it is a POSE as well as a balloon.  She wears a sticker
    /// from OUTSIDE the mood's own cycle for as long as the line lasts and then dissolves back to the step
    /// she was on, so the surprise is something you can see her be rather than only read.
    func surprise() {
        surpriseUntil = Date().addingTimeInterval(bubbleSeconds)
        wearSurprise()
    }

    /// Pick a sticker the mood she is in would never show her and put it on.  The pool is every frame that
    /// is NOT in her current cycle, so "outside the mood" is the definition rather than a hope; the longest
    /// cycle she has is six of twenty frames, so the pool is never empty in practice and a mood with no
    /// cycle at all ("nothing told") falls back to any frame rather than to none.
    private func wearSurprise() {
        guard !frames.isEmpty else { return }
        let worn = Set(cycle)
        let pool = (0..<frames.count).filter { !worn.contains($0) }
        surprisePose = (pool.isEmpty ? Array(0..<frames.count) : pool).randomElement() ?? 0
        cutTo(surprisePose)
    }

    /// ...and take it off again: back to the step of her own cycle she was on, through the ordinary
    /// dissolve — the cut is for the surprise, and the way back is not a surprise.  Her step was held while
    /// the surprise was on (see `advanceStep`), so this is the picture she left, not a new one.
    func endSurprise() {
        surpriseUntil = Date.distantPast
        surprisePose = -1
        guard !cycle.isEmpty else { return }
        let back = cycle[min(step, cycle.count - 1)]
        if back != index {
            beginPoseFade(back)
            index = back
        }
        needsDisplay = true
    }

    /// Put a picture on with NO dissolve: the picture she was wearing is dropped, so the draw has nothing
    /// to blend and the change lands in one frame.  This is the ONE cut she makes on purpose — a surprise
    /// you can see coming is not one — and it is used for arriving at the surprise pose, never for leaving
    /// it.
    private func cutTo(_ next: Int) {
        wasArt = nil
        wasFrame = nil
        poseFadeAt = Date.distantPast
        index = next
        needsDisplay = true
    }
    /// Her frames, RESAMPLED ONCE, at the pixel size of the window she is being drawn into: a draw
    /// then copies pixels 1:1 and the screen has nothing to filter (see `renderedArt`).  Empty means
    /// "not prepared" (or `--sharp=0`), which falls back to the drawing path below.
    private var art: [CGImage] = []
    private var artPixels = 0
    /// The picture she was wearing a moment ago and when the change began: changing pose is a
    /// TRANSITION like the pin moving her is, and it used to be the one the owner could still see as a
    /// cut.  The dissolve is the OLD picture drawn over the new one — the same square, the art's own
    /// alpha, nothing else — and both of these stay unused at `--transition 0`, which is the old
    /// behaviour (see `transitionMs`).
    private var wasArt: CGImage? = nil
    private var wasFrame: NSImage? = nil
    private var poseFadeAt = Date.distantPast
    /// Whether she has been DRAWN at least once: a launch is not a pose change, and there is no
    /// picture to dissolve away from until there is one on screen.
    private var drawn = false
    /// Her BUBBLE: the line, when it went up, and when it comes down — drawn inside her own square over
    /// her art, so it cannot end up over the pane's list or outside the pane whatever the layout does.
    /// It fades on and off on its OWN short clock rather than on `transitionS`: a line is not a state
    /// change, and ten seconds to read two words is not a transition, it is a hostage situation.
    private var line = ""
    private var lineAt = Date.distantPast
    private var lineUntil = Date.distantPast
    /// Whether a line is on screen (fade included), which is what the fine clock redraws for.
    var bubbleOpen: Bool { !line.isEmpty && Date() < lineUntil }
    /// FINISHED: the one transition that is not a state change — she runs her own poses through at a
    /// burst's pace for a few seconds, so "every step is ticked" is something you can SEE rather than
    /// something you have to read (2026-10-07: "add transition after she's done with all the job").
    private var burstUntil = Date.distantPast
    var bursting: Bool { Date() < burstUntil }

    func celebrate() { burstUntil = Date().addingTimeInterval(celebrateS) }

    func say(_ text: String, for seconds: Double = bubbleSeconds) {
        line = text
        lineAt = Date()
        lineUntil = lineAt.addingTimeInterval(seconds)
        // ...and the next line hangs on the OTHER side of her head: the balloon is measured against a square
        // she fills from the top down, and her ahoge comes off the left of her head, so a balloon that always
        // sat over the same corner spent every line covering it ("make the balloon alternate sides of her
        // head between lines so it stops covering her ahoge").  Only a LIVE line flips the side: a rendered
        // frame (`--art-say`) is always the same picture, or the sheet and the suite would not be repeatable.
        bubbleRight.toggle()
        needsDisplay = true
    }

    /// Which side of her head the balloon is hanging on this line (see `say`).
    private var bubbleRight = false

    /// Which side the balloon on screen is on, for the RECORD — `none` when she is saying nothing.  The
    /// flip is a picture and not a fact on either end (the balloon's rect is measured at draw time), so
    /// the side is named here the way the surprise pose is: the alternation is then something her log
    /// tells you, not something you have to catch in the pixels.
    var balloonSide: String { line.isEmpty ? "none" : (bubbleRight ? "right" : "left") }

    /// Put a line up at FULL strength, for a one-shot render (`--art-say`): a still picture has no
    /// clock, so there is nothing to fade against — and a bubble that can be rendered is a bubble whose
    /// own size and wrapping can be measured rather than watched.
    func showLine(_ text: String) {
        line = text
        lineAt = Date.distantPast
        lineUntil = Date.distantFuture
        needsDisplay = true
    }

    /// How solid the bubble is right now: in over `bubbleFadeS`, out over the same at the end of it, and
    /// nothing at all once the line is over.
    private func bubbleAlpha() -> CGFloat {
        let now = Date()
        guard !line.isEmpty, now < lineUntil else { return 0 }
        // ...and the balloon does not fade IN either (the same "remove fade in"): it is there at full
        // strength the instant she says it, and its arrival is the POP instead — a ramp and a pop together
        // were two answers to one question, and the pop is the one you can see.  Going away still fades.
        let down = min(1, lineUntil.timeIntervalSince(now) / bubbleFadeS)
        return CGFloat(down)
    }
    /// How long a pose change takes: the same number as the move and the fade, for every mood (2026-10-07:
    /// "make transition between pose take 10s").  A mood whose own pace is shorter than this is then
    /// dissolving for most of its cycle — a slow morph rather than a snap between stickers — which is
    /// the length the owner asked for and is why `0` is worth having.
    private var dissolveS: Double = 0
    /// Whether a dissolve is still in flight.  The fine clock in `main` redraws while this is true and
    /// does nothing at all while it is false, which is most of a session.
    var posing: Bool { dissolveS > 0 && Date().timeIntervalSince(poseFadeAt) < dissolveS }

    /// Start a dissolve from whatever she is wearing now.  Called at the moment the pose changes and
    /// BEFORE `index` moves, so the picture left behind is the one that was on screen.
    private func beginPoseFade(_ next: Int) {
        guard drawn, dissolveS > 0, next != index else { return }
        if art.isEmpty {
            guard !frames.isEmpty else { return }
            wasFrame = frames[index % frames.count]
            wasArt = nil
        } else {
            wasArt = art[index % art.count]
            wasFrame = nil
        }
        poseFadeAt = Date()
    }

    /// How much of the picture she is LEAVING is still in this draw: 1 at the instant the pose changed,
    /// 0 when the dissolve is over (and 0 the whole time at `--transition 0`, which is the hard cut).
    /// Eased rather than linear — a dissolve that starts and ends at full speed reads as a flicker —
    /// which is the same ease-in-out the pin's own move uses.
    private func poseFade() -> CGFloat {
        guard dissolveS > 0 else { return 0 }
        let t = Date().timeIntervalSince(poseFadeAt) / dissolveS
        guard t >= 0, t < 1 else { return 0 }
        return CGFloat(1 - t * t * (3 - 2 * t))          // smoothstep, over the fade
    }

    /// Render every frame for a window this many points across. Cheap to call every tick — it
    /// returns immediately unless the size changed, which is what makes it safe on the pin clock.
    func prepare(points: CGFloat) {
        let pixels = deviceSide(for: points)
        if pixels == artPixels { return }
        art = sharpen ? frames.compactMap { renderedArt($0, pixels: pixels) } : []
        // Recorded even when nothing came back: a render that failed must fall through to the
        // drawing path once, not be retried on every one of the draws that follow.
        artPixels = pixels
    }

    /// Wear a mood: the pane's word becomes the pose (see `moodCycles`).  A mood whose frames are
    /// not on disk falls back to her frames in their own order rather than to nothing at all.
    func setMood(_ name: String) {
        moodName = name
        // ...and a mood change ENDS a surprise: the sticker she borrowed belongs to the mood it arrived
        // on, so the new mood is worn the ordinary way rather than under a picture from an older one.
        surpriseUntil = Date.distantPast
        surprisePose = -1
        guard let mood = moodTable[name], !frames.isEmpty else {
            cycle = []
            return
        }
        let picked = mood.frames.map { $0 - 1 }.filter { $0 >= 0 && $0 < frames.count }
        cycle = picked
        cycleMs = mood.ms
        dissolveS = transitionS <= 0 ? 0
            : min(transitionS, max(mood.ms > 0 ? mood.ms / 1000 : transitionS, poseFloorS))
        step = 0
        if !cycle.isEmpty {
            beginPoseFade(cycle[0])
            index = cycle[0]
            needsDisplay = true
        }
    }

    /// One step of the mood's own cycle — how she MOVES, as opposed to how she looks.  A step is a
    /// change of picture too, so it dissolves like any other; a mood whose pace is shorter than the
    /// dissolve then spends most of its cycle between two of its frames, which is what the knob is FOR
    /// — `--transition 0` puts the cut back, and the paces themselves are the moods FILE.
    func advanceStep() {
        guard !cycle.isEmpty, cycleMs > 0, cycle.count > 1 else { return }
        // ...but not while the SURPRISE is on: she is wearing a picture that is not in this cycle at all,
        // so the cycle HOLDS, and the step she comes back to is the one she left (see `endSurprise`).
        guard !surpriseOn else { return }
        step = (step + 1) % cycle.count
        beginPoseFade(cycle[step])
        index = cycle[step]
        needsDisplay = true
    }

    override func draw(_ dirtyRect: NSRect) {
        NSColor.clear.set()
        dirtyRect.fill()
        // The art has its own alpha, so the draw IS the silhouette: no window chrome, no
        // background, nothing between her and the desktop behind her.
        if art.isEmpty, sharpen, artPixels == 0 { prepare(points: bounds.width) }
        // The dissolve, if one is in flight: the picture she is ARRIVING at goes down first and the one
        // she is leaving is drawn over it at a shrinking alpha, so what the screen shows between the
        // two is the two of them and nothing else.
        let fade = poseFade()
        if !art.isEmpty {
            // Nothing to interpolate: the bitmap IS the size of the square it is drawn into.
            NSGraphicsContext.current?.imageInterpolation = .none
            let ctx = NSGraphicsContext.current?.cgContext
            ctx?.draw(art[index % art.count], in: bounds)
            if fade > 0, let was = wasArt {
                ctx?.setAlpha(fade)
                ctx?.draw(was, in: bounds)
                ctx?.setAlpha(1)
            }
            drawn = true
        } else {
            // The old path, kept as the one she had before this and as the A/B arm: the frame is handed
            // to AppKit at its own 128px and SCALED ON THE SCREEN.  The interpolation is asked for by
            // name rather than left to the default, and `.high` is the best of what that path offers.
            NSGraphicsContext.current?.imageInterpolation = .high
            frames[index % frames.count].draw(in: bounds, from: .zero, operation: .sourceOver,
                                              fraction: 1.0)
            if fade > 0, let was = wasFrame {
                was.draw(in: bounds, from: .zero, operation: .sourceOver, fraction: fade)
            }
            drawn = true
        }
        // ...and her bubble LAST, over whichever of the two drew the picture: it is the one thing that
        // always goes on top — and always inside her own square, which is what keeps a line from ever
        // reaching the pane's list.
        if !line.isEmpty { drawBubble(bubbleAlpha()) }
    }

    /// Her line, worn as a MANGA BALLOON: the words are measured FIRST, and the cloud is the ring of
    /// scallops that wraps that measurement — soft, spangled, dotted or SPIKED according to the mood (see
    /// `bubbleShapes`) — so what she says is one shape with a mood in it rather than one card with
    /// different words in it (2026-10-07: "a bubble cloud box like in manga wrap around text to show her
    /// emotion").  It is drawn OVER her art on purpose: her head is what the tail points at, and the rows
    /// behind her stay the pane's.
    ///
    /// Two invariants survive every shape.  The balloon is built around the line's own bounds, so a
    /// longer line is a wider cloud and one that wraps is a taller one — the bubbles she has had until
    /// now were a card the width of her square with the words centred in it.  And nothing it draws reaches
    /// past her own square (`puff` is the only way a circle gets into it, and it intersects), because a
    /// line over the pane's list is the one thing a bubble must never do.
    private func drawBubble(_ alpha: CGFloat) {
        guard alpha > 0, let ctx = NSGraphicsContext.current?.cgContext,
              let layout = bubbleLayout() else { return }
        let (ring, r, pad, font, shape) = layout
        ctx.setAlpha(alpha)
        let paragraph = NSMutableParagraphStyle()
        paragraph.alignment = .center
        paragraph.lineBreakMode = .byWordWrapping
        let attrs: [NSAttributedString.Key: Any] = [
            .font: font,
            .foregroundColor: NSColor(calibratedRed: 0.16, green: 0.07, blue: 0.12, alpha: 1),
            .paragraphStyle: paragraph,
        ]
        // The balloon twice: the same silhouette grown in the mood's own colour, then the real one over
        // it, so the outline is a FILL and cannot draw the arcs the union ate inside it.  The text goes
        // on last, over both.
        let border = NSColor(calibratedRed: shape.border.red, green: shape.border.green,
                             blue: shape.border.blue, alpha: 1)
        // The POP: everything below is drawn through a scale about the balloon's own top edge and its
        // horizontal centre, so it arrives squashed and springs open DOWNWARD, toward her head.  Downward is
        // the only direction with room — the balloon is measured against the top of her square — and the pop
        // never scales wider than the settled balloon, so a pop can only ever grow inward.
        let pop = bubblePop()
        let out = r + (shape.spikes > 0 ? r * 0.45 : 0) + bubbleOutline
        let box = ring.insetBy(dx: -out, dy: -out)
        ctx.saveGState()
        ctx.translateBy(x: box.midX, y: box.maxY)
        ctx.scaleBy(x: pop.x, y: pop.y)
        ctx.translateBy(x: -box.midX, y: -box.maxY)
        defer { ctx.restoreGState() }
        for (grow, fill) in [(bubbleOutline, border),
                             (0, NSColor(calibratedRed: 1.0, green: 0.97, blue: 0.99, alpha: 0.97))] {
            fill.setFill()
            cloudBody(ring, r: r, pad: pad, gap: shape.gap, grow: grow).fill()
            cloudPuffs(ring, r: r, gap: shape.gap, grow: grow).fill()
            cloudSpikes(ring, r: r, count: shape.spikes, grow: grow).fill()
            cloudTail(ring, r: r, thought: shape.thought, grow: grow).fill()
        }
        if shape.sparkles > 0 {
            border.setFill()
            cloudSparkles(ring, r: r, count: shape.sparkles).fill()
        }
        (line as NSString).draw(in: ring.insetBy(dx: pad, dy: pad), withAttributes: attrs)
        ctx.setAlpha(1)
    }

    /// Where the balloon for the line she is wearing actually IS: the ring the scallops sit on, the radius
    /// they are drawn at, the padding the words get, the font they were measured in and the mood's own
    /// shape.  Drawing and REPORTING share it, which is what lets "the cloud wraps the text" be measured
    /// — `--art` prints `bubbleBox` (see the record line) — rather than squinted at in a picture.
    private func bubbleLayout() -> (ring: NSRect, r: CGFloat, pad: CGFloat, font: NSFont,
                                    shape: BubbleShape)? {
        guard !line.isEmpty, bounds.width > 8, bounds.height > 8 else { return nil }
        let shape = surpriseOn ? surpriseShape : (bubbleShapes[moodName] ?? bubbleShapes["work"]!)
        let font = NSFont.systemFont(ofSize: max(10, bounds.width * 0.088), weight: .semibold)
        let pad = max(5, bounds.width * 0.05)
        // Half her height, as it has always been, and the words are measured with the padding, the
        // outline and a scallop's own reach taken off the width first, so the ring can never come out
        // wider than her square and clip the line it is wrapping.
        let cap = bounds.height * 0.5
        let reach = pad + 1 + bubbleOutline + shape.puff * cap * 0.5
        let room = (line as NSString).boundingRect(
            with: NSSize(width: max(24, bounds.width - 2 * reach), height: bounds.height),
            options: [.usesLineFragmentOrigin], attributes: [.font: font])
        let text = NSSize(width: ceil(room.width) + 2 * pad, height: ceil(room.height) + 2 * pad)
        // The scallops scale with the LINE and not with her square, which is what makes the cloud follow
        // the words; and where the whole balloon will not fit in half her height the radius gives way
        // (never past a third of itself) rather than the line being clipped.
        var r = shape.puff * text.height
        var spikeOut = shape.spikes > 0 ? r * 0.45 : 0
        let over = text.height + 2 * (r + spikeOut)
        if over > cap {
            let scale = max(0.34, (cap - text.height) / max(1, over - text.height))
            r *= scale
            spikeOut *= scale
        }
        // The ring the puffs sit on — the line's own rect — inset by everything the silhouette reaches
        // beyond it, so bumps, spikes and the outline all land inside her square.
        let inset = 1 + bubbleOutline + r + spikeOut
        // ...and it hangs on the side `say` chose, flush to that edge rather than centred: the whole point of
        // alternating is to be somewhere else this time, and the edge is somewhere else (see `bubbleRight`).
        let x = bubbleRight ? bounds.width - inset - text.width : inset
        let ring = NSRect(x: x, y: bounds.height - inset - text.height,
                          width: text.width, height: text.height)
            .intersection(bounds.insetBy(dx: inset, dy: inset))
        return (ring, r, pad, font, shape)
    }

    /// The whole silhouette of the balloon she is wearing, grown past the ring by everything the cloud
    /// draws outside it: the record line `--art` prints, and the number a test measures the wrap with.
    var bubbleBox: NSRect? {
        guard let (ring, r, _, _, shape) = bubbleLayout() else { return nil }
        let out = r + (shape.spikes > 0 ? r * 0.45 : 0) + bubbleOutline
        return ring.insetBy(dx: -out, dy: -out)
    }

    /// Which balloon she is wearing, by name, for the record and the log: the mood's own shape, or the
    /// surprise — so a run that surprised you can be told from one that did not.
    var bubbleShapeName: String { surpriseOn ? "surprise" : moodName }

    /// ...and the same geometry as the one short line the record prints: how wide and tall the balloon came
    /// out, the radius its scallops were drawn at, and how many spikes it got — three numbers that say
    /// which shape and which line produced what is in the picture.
    var bubbleMeasure: String {
        guard let box = bubbleBox, let (ring, r, _, _, shape) = bubbleLayout() else { return "-" }
        return "\(Int(box.width.rounded()))x\(Int(box.height.rounded()))"
            + " r=\(Int(r.rounded())) box=\(Int(ring.minX.rounded())),\(Int(ring.minY.rounded()))"
            + " spikes=\(shape.spikes)"
    }

    /// The balloon's BODY, under the scallops: a ring of puffs with a hole in the middle is a doughnut,
    /// and the line inside it has to be readable, so the rect the words were measured into is filled as
    /// well — inset as far as it can go and still be HIDDEN.  Two neighbouring scallops of radius `r` sit
    /// `gap` radii apart, so their union only reaches `r * sqrt(1 - (gap/2)²)` in from the ring, and the
    /// smallest of the wobbled puffs is 0.75 of a radius: 0.72 of that is the deepest this card may go
    /// before its straight edge becomes the silhouette — which is exactly the "too rectangle" the owner
    /// saw.  The other limit is the line's own padding, so the card can never eat into the words.  What
    /// shows between two bumps, then, is the puffs' own arcs, and that is the cloud.
    private func cloudBody(_ ring: NSRect, r: CGFloat, pad: CGFloat, gap: CGFloat,
                           grow: CGFloat) -> NSBezierPath {
        let hidden = 0.72 * r * max(0.05, (1 - (gap / 2) * (gap / 2)).squareRoot())
        let bite = max(0.5, min(pad * 0.92, hidden) - grow)
        let box = ring.insetBy(dx: bite, dy: bite)
        let corner = min(box.height / 2, r * 0.8)
        return NSBezierPath(roundedRect: box, xRadius: corner, yRadius: corner)
    }

    /// The balloon's POP, as the pair of scales it is still squashed by at this instant (1, 1 is settled).
    /// A damped spring rather than a ramp — it arrives flat and springs open with a small wobble, which is
    /// what makes it read as something appearing rather than something being faded up — evaluated in closed
    /// form, so there is no state to keep and no timer to start.  It never scales above its settled size
    /// sideways (`x` is clamped at 1) and never past 4% below it (a pop that grew further would show a flat
    /// edge where her square clips it); the vertical squash is the complement of that stretch.
    private func bubblePop() -> (x: CGFloat, y: CGFloat) {
        guard bubblePopS > 0, lineAt != Date.distantPast else { return (1, 1) }
        let t = Date().timeIntervalSince(lineAt)
        guard t >= 0, t < bubblePopS else { return (1, 1) }
        let u = CGFloat(t / bubblePopS)                  // 0 the instant it appears, 1 settled
        let wobble = (1 - u) * cos(u * 12)               // +1 at the instant, damping through zero
        return (x: 1 - max(0, 0.06 * wobble), y: min(1.04, 1 - 0.34 * wobble))
    }

    /// The scallops of the cloud: a row of puffs along the top of the measured line and a row along its
    /// bottom, with one at each end, all in one path — the fill of overlapping subpaths is their union
    /// (non-zero winding), so what comes out is one soft blob the size of the words.  `grow` inflates
    /// every radius, which is how the outline is drawn: the same cloud in the mood's colour, then the
    /// real one over it.
    private func cloudPuffs(_ ring: NSRect, r: CGFloat, gap: CGFloat, grow: CGFloat) -> NSBezierPath {
        let path = NSBezierPath()
        let step = max(2, r * gap)
        let n = max(1, Int((ring.width / step).rounded(.up)))
        for i in 0...n {
            let x = ring.minX + ring.width * CGFloat(i) / CGFloat(n)
            // ...and they are not all the same size: a row of equal circles at equal spacing is a rounded
            // RECTANGLE with beads on it, which is what the owner saw ("the bubble cloud text looks a bit
            // too rectangle", 2026-10-07).  The wobble is a fixed function of the puff's own index — no
            // randomness, so the same word is the same cloud frame for frame — and it makes the edge
            // undulate the way a drawn cloud does.
            let wobble = 1 + 0.25 * sin(CGFloat(i) * 2.3)
            _ = puff(path, NSPoint(x: x, y: ring.maxY), r * wobble, grow: grow)
            _ = puff(path, NSPoint(x: x, y: ring.minY), r * wobble, grow: grow)
        }
        _ = puff(path, NSPoint(x: ring.minX, y: ring.midY), r, grow: grow)
        _ = puff(path, NSPoint(x: ring.maxX, y: ring.midY), r, grow: grow)
        return path
    }

    /// ...and a SHOCK balloon, for the moods that need one: points evenly spaced around the ring, each a
    /// thin triangle whose base sits inside the cloud and whose apex reaches a little past it, so the
    /// silhouette comes out jagged — the way a manga balloon says "!" without a word in it.  Its own
    /// path rather than the puffs': every triangle here is wound the same way, and two fills of one
    /// colour cannot show the join.
    private func cloudSpikes(_ ring: NSRect, r: CGFloat, count: Int, grow: CGFloat) -> NSBezierPath {
        let path = NSBezierPath()
        guard count > 0 else { return path }
        let a = ring.width / 2 + r * 0.9, b = ring.height / 2 + r * 0.9
        let cx = ring.midX, cy = ring.midY
        for i in 0..<count {
            let t = 2 * CGFloat.pi * CGFloat(i) / CGFloat(count)
            let half = (2 * CGFloat.pi / CGFloat(count)) * 0.22
            let px = cx + a * cos(t), py = cy + b * sin(t)
            let nx = cos(t) / a, ny = sin(t) / b
            let m = max(0.0001, (nx * nx + ny * ny).squareRoot())
            let len = r * 0.45 + grow
            path.move(to: NSPoint(x: cx + a * cos(t - half), y: cy + b * sin(t - half)))
            path.line(to: NSPoint(x: px + nx / m * len, y: py + ny / m * len))
            path.line(to: NSPoint(x: cx + a * cos(t + half), y: cy + b * sin(t + half)))
            path.close()
        }
        return path
    }

    /// The tail, which is what makes a cloud a BALLOON: two puffs growing off its bottom left towards her
    /// head for the things she SAYS, and three DETACHED dots for the moods where she is only thinking,
    /// waiting or left over — the manga difference between a speech balloon and a thought one, drawn
    /// inside her own square like everything else.
    private func cloudTail(_ ring: NSRect, r: CGFloat, thought: Bool, grow: CGFloat) -> NSBezierPath {
        let path = NSBezierPath()
        // ...and the tail points at her head from whichever side the balloon is hanging on (see
        // `bubbleRight`): a tail that always came off the balloon's left corner would point away from her on
        // every other line.
        let heel: CGFloat = bubbleRight ? 0.70 : 0.30
        let tip: CGFloat = bubbleRight ? 0.80 : 0.20
        if thought {
            let scales: [CGFloat] = [0.50, 0.34, 0.22]
            for (i, scale) in scales.enumerated() {
                let n = CGFloat(i)
                _ = puff(path, NSPoint(x: ring.minX + ring.width * (heel + (bubbleRight ? -0.03 : 0.03) * n),
                                       y: ring.minY - r * (0.55 + 0.95 * n)),
                         r * scale, grow: grow)
            }
        } else {
            _ = puff(path, NSPoint(x: ring.minX + ring.width * heel, y: ring.minY - r * 0.45),
                     r * 0.52, grow: grow)
            _ = puff(path, NSPoint(x: ring.minX + ring.width * tip, y: ring.minY - r * 1.05),
                     r * 0.30, grow: grow)
        }
        return path
    }

    /// ...and a few stars for the moods that get them, printed in the balloon's own colour in the padding
    /// the line leaves at its corners: the manga shorthand for "!" rather than a word for it.  They sit
    /// inside the ring, so they cannot push the silhouette past her square however wide the line wraps.
    private func cloudSparkles(_ ring: NSRect, r: CGFloat, count: Int) -> NSBezierPath {
        let path = NSBezierPath()
        let spots: [(CGFloat, CGFloat, CGFloat)] = [(0.11, 0.86, 0.60), (0.89, 0.78, 0.48),
                                                    (0.22, 0.12, 0.38), (0.78, 0.10, 0.32)]
        for (fx, fy, size) in spots.prefix(count) {
            let cx = ring.minX + ring.width * fx, cy = ring.minY + ring.height * fy
            let s = r * size
            for k in 0..<8 {
                let t = CGFloat(k) * CGFloat.pi / 4
                let len = k % 2 == 0 ? s : s * 0.30            // four points, four notches
                let point = NSPoint(x: cx + len * cos(t), y: cy + len * sin(t))
                if k == 0 { path.move(to: point) } else { path.line(to: point) }
            }
            path.close()
        }
        return path
    }

    /// One circle of the balloon, clipped to her own square.  Every puff, dot, tail and outline bump goes
    /// through here, which is what makes "the bubble never leaves her slot" a property of the drawing
    /// rather than a claim about the arithmetic above it — the clip only ever bites if a future shape is
    /// measured wrong.
    private func puff(_ path: NSBezierPath, _ centre: NSPoint, _ radius: CGFloat, grow: CGFloat) -> Bool {
        let r = radius + grow
        let box = NSRect(x: centre.x - r, y: centre.y - r, width: 2 * r, height: 2 * r)
            .intersection(bounds.insetBy(dx: 0.5, dy: 0.5))
        guard box.width > 1, box.height > 1 else { return false }
        path.appendOval(in: box)
        return true
    }
}

final class PipWindow: NSWindow {
    override var canBecomeKey: Bool { true }

    override func keyDown(with event: NSEvent) {
        if event.keyCode == 53 {    // Escape
            NSApp.terminate(nil)
        } else {
            super.keyDown(with: event)
        }
    }

    override func rightMouseDown(with event: NSEvent) {
        NSApp.terminate(nil)
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)

// Where a PiP corner goes: bottom-right, clear of the screen's own edges and the Dock, on the
// screen the mouse is on rather than always the first one.
let screens = NSScreen.screens
let mouse = NSEvent.mouseLocation
let screen = screens.first { NSMouseInRect(mouse, $0.frame, false) } ?? NSScreen.main
    ?? screens.first!
let visible = screen.visibleFrame
let fullHeight = (screens.first ?? screen).frame.height     // window-server coords count down from
                                                            // the primary display's top-left
/// Where she is stuck: the taught pin when there is one, else the pane found in the window's own
/// drawing, else the host's centre while that measurement is still on its way — and either way her
/// own CENTRE is kept inside the host window, so a pin taught in a bigger window (or one the app
/// has since moved the panel out from under) cannot park her off the window entirely.
@Sendable func pinned(in host: Host) -> (origin: NSPoint, size: CGFloat, hidden: Bool) {
    var offset: CGPoint
    var size = side
    var hidden = false
    if let taught = readPin() {
        offset = taught
    } else if let measured = measuredFit(for: host) {
        offset = measured.origin
        size = measured.size               // she is drawn at the size the slot could hold
        hidden = measured.hidden           // ...or not drawn at all, when it could hold nothing
    } else {
        // NOTHING MEASURED FOR THIS WINDOW, and nothing taught: she is HIDDEN, not put at the
        // window's own middle.  The middle of the app's window is not the pane — it is the chat, or
        // the file list, or the wallpaper — and "she appears only in the Freebuff terminal's fbtodo
        // pane, not outside" (the owner, 2026-10-07) means that until a pane has been found there is
        // nowhere to stand rather than somewhere wrong.  This is also what a REFUSED measurement
        // does now (a window with no pane-shaped thing in it): she steps out until one is found,
        // which the .5s pin timer does on the next tick, so the gap is a fraction of a second.
        offset = CGPoint(x: host.rect.midX - side / 2 - host.rect.origin.x,
                         y: host.rect.midY - side / 2 - host.rect.origin.y)
        hidden = true
    }
    // ...and she is never shown on a Space her pane is not on.  Her window joins every Space, so a
    // fit measured while the app's window sat on another one puts her on the desktop by herself,
    // which is the owner's complaint exactly (2026-10-07: "make her appear only in freebuff terminal
    // fbtodo only, not outside").  The measurement still happens while the window is away — that is
    // the whole reason `findHost` has its off-screen pass — so she is in the right place the moment
    // the window comes back, and she is simply not on screen until then.
    //
    // THE FLAG IS A HINT, NOT THE FACT.  `kCGWindowIsOnscreen` is per-Space, and the desktop app's
    // own window reports FALSE while its pane is live and visible in it (measured twice on
    // 2026-10-07, once at launch and again after a restore: `onscreen=false` in the window server's
    // own list, with a full capture of the pane and the reader's eyes on it) — so a gate that
    // believed it alone hid her over a pane she belongs in, which is the other half of "she should
    // be always in there".  The ACTIVE application is what settles it: a window belonging to the app
    // in front is one the reader is looking at, whatever its Space says, and a window on a Space
    // nobody is on does not belong to the app in front.
    //
    // ...and ON-SCREEN IS NOT FRONT, EITHER (2026-10-08: she floated over Discord with the Freebuff window behind
    // it). `kCGWindowIsOnscreen` stays true while another app COVERS the window, so the flag alone let her show
    // over whatever was in front. She is shown only while the host app is the one in front; the flag is no longer
    // consulted here.
    if frontPid != pid_t(host.pid) { hidden = true }
    let half = size / 2
    let centreX = min(max(host.rect.origin.x + offset.x + half, host.rect.minX), host.rect.maxX)
    let centreY = min(max(host.rect.origin.y + offset.y + half, host.rect.minY), host.rect.maxY)
    offset = CGPoint(x: centreX - half - host.rect.origin.x, y: centreY - half - host.rect.origin.y)
    // The host's rectangle is in top-left coordinates and Cocoa counts from the bottom, so the
    // pin's y is flipped once, here, and nowhere else.
    return (NSPoint(x: host.rect.origin.x + offset.x,
                    y: fullHeight - (host.rect.origin.y + offset.y) - size), size, hidden)
}

/// The middle of the pane's own window (or where she was taught to stand in it) when the window
/// can be found, else NOWHERE — which is the whole of "only in the pane": with no window there is no
/// pane, and the middle of the SCREEN is the chat behind it, the desktop, another app.  She is
/// hidden until there is a window again (her own .5s clock orders her back in), never parked on the
/// desktop in the meantime.
func home() -> (origin: NSPoint, size: CGFloat, hidden: Bool) {
    guard let host = findHost() else {
        return (NSPoint(x: visible.midX - side / 2, y: visible.midY - side / 2), side, true)
    }
    return pinned(in: host)
}
let start = home()

// A test that needs no window at all: `--fit <png>` runs the detection over a screenshot on disk and
// prints what it found. It is how the suite checks the geometry (a synthetic pane of known size is
// a known answer) and how this was measured against a real window by hand.

/// One capture of a window, retried: a capture can refuse once and work the next time, and a verb that
/// gave up on one refusal would report "not allowed" when the truth was "not yet". The retries are
/// LOGS, so they go to stderr and a reader of stdout never sees an attempt as an answer.
func captureWithRetries(_ number: Int, label: String, attempts: Int = 6) -> Screenshot? {
    for attempt in 1...attempts {
        if let shot = captureWindow(number) { return shot }
        FileHandle.standardError.write(
            "buffy-pip: capture \(attempt) of \(attempts) for \(label) refused; retrying\n"
            .data(using: .utf8)!)
        if attempt < attempts { usleep(800_000) }
    }
    return nil
}

// ...and the same capture written to disk: `--shot <png>` takes one screenshot of the window she
// belongs to and saves it, so the detection can be looked at by hand (and a fixture for the suite
// can be taken from a real pane) without `screencapture`'s fifty-two seconds.
/// The whole of `--shot`, as a function for the same reason `--doctor` is one: a capture needs a
/// running GUI application. Measured 2026-10-08 — the top-level version, before `NSApplication` is up,
/// exited 69 with "no screenshot for --shot" on EVERY run from a shell, while the same binary's own
/// scheduled capture measured the pane perfectly; `--doctor` was moved inside the app for exactly this
/// and `--shot` was left behind, so the one verb that keeps a capture was the one that never had one.
func shotRun(_ out: String) -> Int32 {
    guard let host = findHost() else {
        FileHandle.standardError.write(
            ("buffy-pip: no host window to capture — nothing named Freebuff (--host) or from the "
             + "pane's own chain (--pids) is on screen\n").data(using: .utf8)!)
        return 66                        // EX_NOINPUT: no window to look at
    }
    guard let shot = captureWithRetries(host.number, label: "--shot") else {
        FileHandle.standardError.write(
            ("buffy-pip: no screenshot of window \(host.number) in 6 attempts — a capture this "
             + "process is not allowed to take (Screen Recording), or six that timed out\n")
            .data(using: .utf8)!)
        return 69                        // EX_UNAVAILABLE
    }
    guard writePNG(shot, to: (out as NSString).expandingTildeInPath) else {
        FileHandle.standardError.write("buffy-pip: cannot write \(out)\n".data(using: .utf8)!)
        return 73                        // EX_CANTCREAT
    }
    // The window number is part of the answer: `--shot` is how a capture is compared with the one
    // the fit took, and "the same window" is the first thing that has to be true for the comparison
    // to mean anything (measured 2026-10-07: two processes captured the same-looking window and put
    // its top edge 57.5pt apart, which was two different windows, not two different captures).
    print("shot=\(out) \(shot.width)x\(shot.height) window=\(host.number) "
          + "at=\(Int(host.rect.origin.x)),\(Int(host.rect.origin.y)) "
          + "\(Int(host.rect.width))x\(Int(host.rect.height)) by=\(host.pid) "
          + "onscreen=\(host.onscreen)")
    return 0
}

// The whole measurement, as one verb: `fbtodo pip doctor`. It captures the window she belongs to and
// says everything a reader would otherwise have to infer — the hint it used, every long column, every
// pair of them with the reason it was kept or thrown away, the pane that won, and where she would
// stand in it — so "she is standing outside the pane" is answerable in one command rather than by
// reading her log around a line that only reports the answer.
/// The whole of `--doctor`, as a function rather than a block, because it has to run INSIDE the app:
/// a capture needs a running GUI application, and the top-level version — before `NSApplication` is up
/// — refused every attempt (measured 2026-10-08: three tries, three refusals, while her window on the
/// very same app window measured it perfectly). The exit code is the CLI's: 0 when she would be inside
/// the pane, 1 when the pixels say otherwise, 66 with no window to look at, 69 when three captures were
/// all refused.
func doctorRun() -> Int32 {
    // A watch is the same measurement taken again for a while, so the verb that has one goes there
    // first: the single-shot record below is a watch of exactly one sample, printed in full.
    if let seconds = watchSeconds() {
        return watchRun(seconds: seconds) {
            guard let host = findHost() else { return nil }
            // ONE attempt a tick, not `captureWithRetries`: a watch IS the retry (the next sample is a
            // second away), and six attempts with 800ms between them would make a refused capture cost
            // half a minute per sample — a watch that reports four times a minute is not a watch.
            guard let shot = captureWindow(host.number) else { return nil }
            return (shot, host.rect.width,
                    "window=\(host.number) pid=\(host.pid) owner=\(host.owner) "
                    + "\(Int(host.rect.width))x\(Int(host.rect.height))pt "
                    + "onscreen=\(host.onscreen)")
        }
    }
    guard let host = findHost() else {
        FileHandle.standardError.write(
            ("buffy-pip: no host window to measure — nothing named Freebuff (--host) or from the "
             + "pane's own chain (--pids) is on screen\n").data(using: .utf8)!)
        return 66                        // EX_NOINPUT: no window to look at
    }
    print("window=\(host.number) pid=\(host.pid) owner=\(host.owner) "
          + "at=\(Int(host.rect.origin.x)),\(Int(host.rect.origin.y)) "
          + "\(Int(host.rect.width))x\(Int(host.rect.height)) onscreen=\(host.onscreen)")
    // Up to three captures: a capture can refuse once and work the next time, which is what her
    // window's own retry clock exists for, and a doctor that gave up on one refusal would report "not
    // allowed" when the truth was "not yet". The retries are LOGS, so they go to stderr and a reader of
    // stdout never sees an attempt as an answer.
    guard let shot = captureWithRetries(host.number, label: "--doctor") else {
        FileHandle.standardError.write(
            ("buffy-pip: no screenshot of window \(host.number) in 6 attempts — a capture this "
             + "process is not allowed to take (Screen Recording), or six that timed out\n")
            .data(using: .utf8)!)
        return 69                        // EX_UNAVAILABLE
    }
    return diagnose(shot, windowWidth: host.rect.width, hint: readSlackHint()) ? 0 : 1
}

if let png = option("--fit") {
    let width = Double(option("--fit-width") ?? "") ?? 0
    guard let shot = screenshotOnDisk(png) else {
        FileHandle.standardError.write("buffy-pip: cannot read \(png)\n".data(using: .utf8)!)
        exit(65)
    }
    let windowWidth = width > 0 ? CGFloat(width) : CGFloat(shot.width) / 2
    // ...and a FILE can be watched too, which is how the watch itself is checked with no screen
    // involved: the picture is re-read every tick, so what changes is what the pixels or the HINT mean
    // — a hint rewritten mid-watch is a placement that moved while the screen stood still.
    if let seconds = watchSeconds() {
        exit(watchRun(seconds: seconds) {
            guard let fresh = screenshotOnDisk(png) else { return nil }
            return (fresh, windowWidth, "file=\(png) \(fresh.width)x\(fresh.height)px")
        })
    }
    // ...and the same explanation over a FILE, which is how it is checked with no screen involved.
    if args.contains("--trace") {
        exit(diagnose(shot, windowWidth: windowWidth, hint: readSlackHint()) ? 0 : 1)
    }
    if option("--fit-verbose") != nil {
        let ink = inkOf(shot)
        let gap = max(24, ink.height / 24)
        for peak in inkPeaks(ink).prefix(8) {
            let run = longestInkRun(ink, peak.x, gap: gap)
            print("  peak x=\(peak.x) ink=\(peak.n) run=\(run.0)..\(run.1) (\(run.2) px)")
        }
        let scale = CGFloat(shot.width) / CGFloat(windowWidth)
        print("  rect=\(String(describing: paneRect(ink, shapeHint: readSlackHint(), scale: scale)))")
    }
    guard let found = paneAnchor(shot, windowWidth: windowWidth, side: side,
                                 verbose: option("--fit-verbose") != nil) else {
        print("fit=refused \(shot.width)x\(shot.height)")
        exit(1)
    }
    // `anchor` is her top-left in the window's own points, `size` is the square she was fitted to,
    // and `inside` is the answer to the only question that matters to the eye: recomputed here from
    // the pane rectangle and her own, not read back out of the arithmetic that placed her.
    print("fit=\(found.note) anchor=\(Int(found.origin.x.rounded())),"
          + "\(Int(found.origin.y.rounded())) "
          + "inside=\(found.inside ? "yes" : "no") "
          + "scale=\(String(format: "%.3f", Double(shot.width) / Double(windowWidth)))")
    exit(found.inside ? 0 : 1)
}

// A test that needs no window either: `--art <png>` draws ONE of her frames exactly the way the
// screen gets her — same view, same resample, same interpolation — into a PNG, at any size.  It is
// how the two render paths are compared, by number and by eye, without a 52-second `screencapture`
// of a window that is animating: `--art a.png --art-size 192` against the same call with `--sharp=0`
// IS the A/B.
if let out = option("--art") {
    let points = max(8, Double(option("--art-size") ?? "") ?? side)
    let pixels = deviceSide(for: CGFloat(points))
    guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
                                     bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                                     isPlanar: false, colorSpaceName: .deviceRGB,
                                     bytesPerRow: 0, bitsPerPixel: 0) else {
        FileHandle.standardError.write("buffy-pip: no bitmap for --art\n".data(using: .utf8)!)
        exit(70)                        // EX_SOFTWARE: nothing to draw into
    }
    rep.size = NSSize(width: points, height: points)
    let canvas = PipView(frame: NSRect(x: 0, y: 0, width: points, height: points))
    canvas.frames = frames
    // ...and `--art-mood work` draws the pose that mood wears instead of a frame number: it is how
    // the mood table is checked against the art without a window and without a sticker review.
    if let name = option("--art-mood") {
        canvas.setMood(name)
    } else {
        let wanted = Int(option("--art-frame") ?? "") ?? 0
        canvas.index = ((wanted % frames.count) + frames.count) % frames.count
    }
    // ...and `--art-say "all done!"` puts her bubble over it at full strength, so a line can be looked
    // at without a window (and its own card measured rather than watched).
    if let what = option("--art-say") { canvas.showLine(what) }
    // ...and `--art-surprise` renders the balloon she wears when it is a surprise instead of the mood's,
    // so the shape she usually only gets by luck can be looked at (and measured) on demand.
    if args.contains("--art-surprise") { canvas.surprise() }
    canvas.prepare(points: CGFloat(points))
    // The record carries the BALLOON's own geometry when she has a line up: its width and height in
    // points, the radius its scallops were drawn at and how many spikes they got — so "the cloud wraps
    // the text, in the mood's own shape" is a number this program prints rather than a claim about it.
    let balloon = canvas.bubbleBox.map { _ in canvas.bubbleMeasure }
        ?? "-"
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    canvas.draw(canvas.bounds)
    NSGraphicsContext.restoreGraphicsState()
    guard let data = rep.representation(using: .png, properties: [:]) else {
        FileHandle.standardError.write("buffy-pip: cannot encode --art\n".data(using: .utf8)!)
        exit(70)
    }
    let path = (out as NSString).expandingTildeInPath
    do {
        try data.write(to: URL(fileURLWithPath: path))
    } catch {
        FileHandle.standardError.write("buffy-pip: cannot write \(path)\n".data(using: .utf8)!)
        exit(73)                        // EX_CANTCREAT
    }
    print("art=\(path) \(pixels)x\(pixels) points=\(Int(points)) frame=\(canvas.index) "
          + "sharp=\(sharpen ? "on" : "off") radius=\(sharpRadius) intensity=\(sharpIntensity) "
          + "transition=\(Int(transitionS * 1000))ms bubble=\(bubbleOn ? "on" : "off") "
          + "surprise=\(Int(surpriseChance * 100))% pop=\(Int(bubblePopS * 1000))ms "
          + "balloon=\(balloon) "
          + "shape=\(canvas.bubbleShapeName) "
          + "mood=\(option("--art-mood") ?? "-")")
    exit(0)
}

let window = PipWindow(contentRect: NSRect(origin: start.origin,
                                           size: NSSize(width: start.size, height: start.size)),
                       styleMask: [.borderless], backing: .buffered, defer: false)
window.level = .floating                     // above normal windows, below nothing that matters
window.isOpaque = false
window.backgroundColor = .clear
window.hasShadow = true
window.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary]
window.animationBehavior = .none             // no order-in animation: she is furniture

// HER TRANSITIONS, in one place, because all three of them answer the same question: how long does she
// take to get from the state she is in to the one she has just been asked for (see `transitionMs`).
//
// THEY ARE DRIVEN BY HER OWN CLOCK, not AppKit's.  Her window's animation behaviour is `.none` — she is
// furniture, and a window that animates its own ordering-in is a window that flashes in the middle of
// the chat — and a frame change that goes through `animator()` while that is set arrives in ONE step:
// measured 2026-10-07, a glide of 700ms landed as a single step of a 60ms sampler, which is a cut
// wearing a transition's name.  So the ramp is ours — where she is, where she is going, when it began,
// and a clock to ask (`stepTransitions`, on the fine timer below).  They are CLOSURES rather than
// `func`s for a reason that has nothing to do with taste: a closure written here is already main-actor
// (this is the program's top level), while a `func` that touches AppKit from outside one is four
// concurrency warnings about a program that has exactly one thread.
/// The slot she is being moved TO, as last asked for (see `glide`).  The pin's clock is half a second
/// apart: comparing against the frame she is sitting at instead would re-issue the same move on every
/// tick, restarting the ramp from wherever it had got to and never arriving.
var glideTo = NSRect(origin: start.origin, size: NSSize(width: start.size, height: start.size))
/// ...and when the move in flight will be over.  That is what tells the resample below to leave her
/// alone while she MOVES: re-making twenty frames through Core Image for a size she is only passing
/// through is the one thing that could make the move itself stutter.
var glideUntil = Date.distantPast
/// The move in flight: the frame she left, the one she is arriving at, and when it began.
var move: (from: NSRect, to: NSRect, started: Date, seconds: Double)? = nil
/// The fade in flight, the same shape, over her window's opacity.
var fade: (from: CGFloat, to: CGFloat, started: Date, seconds: Double)? = nil
/// What the pin last ASKED of her (the pane has room), and what has been DONE about it (her window is
/// ordered in).  Two flags where there used to be one question asked of the window itself, because the
/// ordering now lags the ask by the length of a fade — and `isVisible` cannot answer either of them:
/// measured 2026-10-07, the tick that first placed her found it TRUE for a window that had never been
/// ordered in at all, so the fade-in was skipped and she arrived with no `shown=` line and no fade.
var wantShown = !start.hidden
var ordered = false
/// When the pin last asked her to go AWAY, so a single failed measurement does not start a fade: the
/// pane is re-measured mid-relayout, and one refused capture between two good ones used to be a
/// one-frame blink — with a ten-second fade it is a twenty-second pulse, which is worse than either.
/// A state that is still "out" one tick later is a state worth transitioning to; going back IN is
/// immediate, because that is the direction nobody wants to wait for.
var outSince: Date? = nil
let hideDebounceS = 0.5

/// The eased progress of a ramp: smoothstep, so it starts and ends at rest rather than at full speed.
/// One ease for the move, the fade and the pose dissolve, because they are one transition.
func eased(_ t: Double) -> CGFloat {
    let p = CGFloat(min(max(t, 0), 1))
    return p * p * (3 - 2 * p)
}

/// Move her (and resize her) to a slot: EASED over `transitionS`, or in one step when the transition is
/// off.  A slot that got SHORTER takes her size at once and eases only her position — easing a shrink
/// would hang her over the pane's own border for the length of the ease, which is the one thing her
/// containment rule is about (a slot that GROWS is eased whole: she is inside it on every frame).
let glide: (NSRect) -> Void = { rect in
    glideTo = rect
    let now = window.frame
    // THE SLIDE IS A SLIDE: her size is taken AT ONCE and only the corner travels (2026-10-07: "don't
    // resize the window for slide in transition").  A window that grows on the way across is a window
    // the pane's own rows cannot account for — she would be wider or taller than her slot for the whole
    // of the move — and the one case where the size must change FAST is a slot that got shorter.
    if rect.size != now.size {
        window.setFrame(NSRect(origin: now.origin, size: rect.size), display: true)
    }
    guard transitionS > 0 else {
        window.setFrame(rect, display: true)
        move = nil
        return
    }
    glideUntil = Date().addingTimeInterval(transitionS + 0.15)
    move = (from: window.frame, to: rect, started: Date(), seconds: transitionS)
}

/// Order her in, fading up: the window that popped was the last hard cut in the pin.  The fade starts
/// from nothing, so no frame of her is ever drawn at full opacity before it, and the ordering-in comes
/// first, which is what makes the first frame of the fade visible at all.
let showWindow: () -> Void = {
    wantShown = true
    if !ordered {
        // ARRIVING is not a fade (2026-10-07: "also remove fade in"): she is put up at FULL OPACITY in the
        // frame she is ordered in, because ten seconds of her appearing out of nothing was the one part of
        // the transition that could be mistaken for a screensaver.  Going AWAY is still a fade (`hideWindow`)
        // — a window that pops out is a flash in the corner of the eye — and a window that is already on
        // screen mid-fade keeps the alpha it has.
        window.alphaValue = 1
        window.orderFrontRegardless()
        ordered = true
    }
    fade = nil
    print("buffy-pip shown=\(readFit().note)")
    fflush(stdout)
}

/// ...and out, with the window itself taken away by `stepTransitions` once the fade has finished and
/// never mid-fade: the tick that brings her BACK sets `wantShown` true again and restarts the fade
/// upward, so the take-away has to be the last thing that a fade ALL THE WAY DOWN does, not something
/// scheduled when the fade began.
let hideWindow: () -> Void = {
    wantShown = false
    fade = (from: window.alphaValue, to: 0, started: Date(), seconds: transitionS)
    print("buffy-pip hidden=\(readFit().note)")
    fflush(stdout)
}

/// The ramps themselves, on the fine clock below: one eased frame and one eased opacity per step, and
/// the END of each is where the two things that cannot be interpolated happen — a slot she has arrived
/// at exactly, and a window ordered away once it is invisible.
let stepTransitions: () -> Void = {
    let now = Date()
    if let m = move {
        let done = m.seconds <= 0 || now.timeIntervalSince(m.started) >= m.seconds
        let p = done ? 1 : eased(now.timeIntervalSince(m.started) / m.seconds)
        // Position only: the size was settled when the move began (see `glide`).
        window.setFrame(done ? m.to
                             : NSRect(x: m.from.origin.x + (m.to.origin.x - m.from.origin.x) * p,
                                      y: m.from.origin.y + (m.to.origin.y - m.from.origin.y) * p,
                                      width: m.to.width, height: m.to.height),
                        display: true)
        if done { move = nil }
    }
    if let f = fade {
        let done = f.seconds <= 0 || now.timeIntervalSince(f.started) >= f.seconds
        window.alphaValue = done ? f.to
                                 : f.from + (f.to - f.from) * eased(now.timeIntervalSince(f.started) / f.seconds)
        if done {
            fade = nil
            if f.to == 0, ordered {
                window.orderOut(nil)
                ordered = false
            }
        }
    }
}

let view = PipView(frame: NSRect(x: 0, y: 0, width: start.size, height: start.size))
view.frames = frames
view.prepare(points: start.size)             // her pixels, before the first frame is drawn
// ...and what she is wearing when she arrives: whatever the pane last said it was doing.
var mood = readMood()
view.setMood(mood)
window.contentView = view
// Ordered in only when she has somewhere to be: a window put on screen before the pane has been
// measured is a window that flashes in the middle of the chat and then jumps, which is exactly what
// "only in the fbtodo pane" rules out.  The pin timer below orders her in on the first measurement
// that lands her inside the pane — usually the first or second tick, 0.5s and 4s at the outside.
if start.hidden {
    print("buffy-pip hidden=waiting for the pane")
    fflush(stdout)
} else {
    showWindow()
    window.makeKey()
}

// Her clock.  A quarter of a second, not the frame time, because the frame time is now a property of
// her MOOD (a held failure does not tick at all, `done` runs four times as fast as `idle`) and a
// timer that has to be rebuilt per mood is a timer that can be left behind by one.  The resample
// follows the window SIZE on the same clock: a pane that grew — or a window dragged to another
// display, which changes the backing scale — gets her pixels re-rendered for it here as well as on
// the pin's, because a free, dragged window is a window the pin is not managing.
var lastLine = ""                        // the bubble's memory, so she does not repeat herself
var lastLineAt = Date.distantPast
var lastStep = Date()
let timer = Timer(timeInterval: 0.25, repeats: true) { _ in
    // ...but NOT while she is moving: the resample is twenty Core Image renders, and a size she is
    // only passing through on the way to her slot is not a size to make them for (see `glideUntil`) —
    // the move prepares her for the slot she is arriving at, which is the one she is drawn at.
    if Date() >= glideUntil { view.prepare(points: view.bounds.width) }
    // A CELEBRATION runs on its own, much faster, pace: the poses of the mood are the same, they just
    // come round in a burst and then settle back to the mood's own (see `celebrateS`).
    // ...and a mood's own steps are held for at least `poseHoldS` (see `poseHoldS`); the burst is not.
    let want = view.bursting ? celebrateMs
        : (view.cycle.isEmpty ? tickMs : max(view.cycleMs, poseHoldS * 1000))
    if want > 0, Date().timeIntervalSince(lastStep) * 1000 >= want {
        lastStep = Date()
        view.advanceStep()
    }
}
RunLoop.current.add(timer, forMode: .common)

// ...and the finer clock her TRANSITIONS need: a quarter of a second on a half-second dissolve is two
// frames of it, and the move and the fade are ramps of the same length (see `stepTransitions`).  It
// costs three `nil` comparisons per 33ms while nothing is happening — and a redraw only while a
// dissolve is really in flight (`posing`) — so a still pose draws nothing at all.
let transTimer = Timer(timeInterval: 1.0 / 30.0, repeats: true) { _ in
    // ...and the surprise comes OFF on this clock rather than on the mood's: the sticker it borrowed lasts
    // as long as the line does, and the picture she returns to is the one she was wearing before it.
    if view.surpriseExpired {
        view.endSurprise()
        // ...and the way back is NAMED, so the round trip is one line each way in her log rather than
        // something only the picture could tell you: the pose she borrowed, and the pose of her own she
        // came back to (which is in her mood's cycle by construction — see `endSurprise`).
        print("buffy-pip surprise=over back=pose=\(view.index + 1)")
        fflush(stdout)
    }
    if view.posing || view.bubbleOpen { view.needsDisplay = true }
    if move != nil || fade != nil { stepTransitions() }
}
RunLoop.current.add(transTimer, forMode: .common)

// The pin. Read twice a second rather than watched with a file monitor: this is one `stat` on a
// path that almost never exists, against a window that repaints every tick anyway, and a switch
// the pane flips must take effect on the next frame rather than on the next launch.
var free = FileManager.default.fileExists(atPath: freeFile)
window.isMovableByWindowBackground = free

// ...and the measurement itself. Taken once at launch, then every `fitSeconds`, and again the
// moment the host window is not the one the last measurement was taken in (moved, resized, or
// replaced) — the two reasons a pin can be right at launch and wrong by teatime.
/// The measured offset for THIS window, or nil when there is none yet (or the last one was taken
/// in a different window, or in this one before it moved).
///
/// A FAILED LATEST ATTEMPT DOES NOT COUNT AS "none yet": when the measurement for this very window
/// came back empty — the screenshot refused, no pane in it — the window in front of her is still the
/// window the last good measurement was taken in, so that is where she stands until a new one lands.
///  ...AND "the same window" is its NUMBER and its SIZE, never its number alone: the saved placement is
/// in the window's own points, so a window that MOVED still has its pane in the same place while a
/// window that was RESIZED does not — and believing a number by itself put her outside the pane the
/// owner was looking at (2026-10-08, "sometimes it still appear out of the fbtodo pane").  A window
/// whose size changed therefore has NO placement until a capture lands (a fraction of a second on the
/// retry clock), which is the one answer that is never wrong: she waits rather than stands somewhere
/// she does not belong.
@Sendable func measuredFit(for host: Host) -> Fit? {
    let fit = readFit()
    if fit.host?.sameWindow(as: host) == true, let value = fit.fit { return value }
    guard fit.fit == nil else { return nil }
    let good = readLastGood()
    guard good.number == host.number, let value = good.fit,
          abs(good.rect.width - host.rect.width) <= 1,
          abs(good.rect.height - host.rect.height) <= 1 else { return nil }
    return value
}

/// Whether the window in front of her is still waiting for a measurement: nothing measured at all,
/// one taken in another window — or one that FAILED, which is retried on the short clock rather
/// than the long one (see `fitRetrySeconds`).
@Sendable func needsFit(for host: Host) -> Bool {
    let fit = readFit()
    if let value = fit.fit, fit.host?.sameWindow(as: host) == true, value.size > 0 { return false }
    return true
}

let fitQueue = DispatchQueue(label: "fbtodo-pip-fit")
var fitting = false
var lastFitAt = Date.distantPast
/// The pid of the application in front, refreshed on every pin tick: half of the answer to "is the
/// reader looking at the window she belongs to" (see `pinned`).  Read off the main thread only, and
/// only from the tick that refreshes it, so no snapshot is ever handed between threads.
var frontPid: pid_t = 0
/// Whether the window she belongs to was on screen the last time it was found: the flip from off to
/// on is the one event that means "she is being looked at again", and it is what forces an
/// immediate re-measure (see the pin timer).
var hostOnscreen: Bool? = nil
/// The reading the live fit is holding back (see `settle`): the next measurement decides whether it was a
/// half-drawn frame or the pane really moving. Only ever touched from `measureFit`, on the fit queue.
var livePending: CGRect? = nil

func refreshFit(_ host: Host) {
    if fitting { return }
    fitting = true
    fitQueue.async {
        measureFit(host)
        fitting = false
    }
}


let pinTimer = Timer(timeInterval: 0.5, repeats: true) { _ in
    // HER MOOD, from the pane's own word (see `moodCycles`): one small file, read twice a second, so
    // a mood the pane changes is worn on the next press of the list's clock rather than on the next
    // launch — and said out loud once per change, because a pose nothing can explain from outside is
    // a pose nobody can debug.
    let wantedMood = readMood()
    if wantedMood != mood {
        mood = wantedMood
        view.setMood(wantedMood)
        let cycle = moodTable[wantedMood] ?? ([], 0)
        print("buffy-pip mood=\(wantedMood) "
              + "frames=\(cycle.frames.map { String($0) }.joined(separator: ",")) "
              + "step=\(Int(cycle.ms))ms")
        fflush(stdout)
        // ...and SOMETIMES she says something about it: a line from her own table, on a mood change, at
        // most once every `bubbleGapS` — except for the one that is worth interrupting her silence for,
        // a finished list, which also gets the burst. A mood nothing is written for says nothing.
        if bubbleOn {
            let finished = wantedMood == "done"
            // ...and SOMETIMES the line is a SURPRISE instead: not the mood's own, not even about the
            // list, in the one balloon that is not a mood's (see `surpriseShape`).  A surprise is allowed
            // past `bubbleGapS` on a mood change, because a line nobody expected is the whole of it.
            let surprised = Double.random(in: 0 ..< 1) < surpriseChance
            if finished || surprised || Date().timeIntervalSince(lastLineAt) >= bubbleGapS {
                // ...and WHICH of the three tables it comes from is `pickLine`'s roll: her mood's own
                // line, a cringe one, or a whole short sentence (see `cringeChance`).
                let picked = surprised ? nil : pickLine(wantedMood, avoid: lastLine,
                                                       cringe: cringeChance, sentence: sentenceChance)
                let text = surprised ? pickFresh(surpriseTable) : picked?.0
                if let text = text {
                    lastLine = text
                    lastLineAt = Date()
                    noteSaid(text)          // into the memory `pickFresh` reads, so she cannot repeat it
                    if surprised { view.surprise() }
                    view.say(text)
                    let borrowed = view.surpriseFrame.map { String($0) } ?? "-"
                    print("buffy-pip says=\(text) mood=\(wantedMood)"
                          + (surprised ? " surprise=yes pose=\(borrowed)" : " kind=\(picked?.1 ?? "say")")
                          // ...and WHICH SIDE of her head it landed on, for the same reason the surprise
                          // pose is named: the alternation is then a fact in her log (`balloon=left`,
                          // `right`, `left`…) rather than something only the pixels could tell you.
                          + " balloon=\(view.balloonSide)")
                    fflush(stdout)
                }
            }
            if finished { view.celebrate() }
        }
    }
    // ...and she TALKS ON THE CLOCK, and not only when her mood moves: a mood lasts a whole turn, so a
    // line per mood change is one line in twenty minutes — a caption, not company (2026-10-08: "use
    // bubble cloud talk more often"). The cadence is the SAME `bubble-gap` the line above is gated on,
    // reused rather than a second silence to keep in step with: how long she keeps quiet after a line IS
    // how often she may break it. The line is the CURRENT mood's, so the clock cannot say something she
    // is not feeling, and it says nothing when the bubble is off, when a line is still up (she never
    // talks over herself), or while the finished burst is running (that burst IS the line).
    if bubbleOn && bubbleClockMoods.contains(mood) && !view.bubbleOpen && !view.bursting
        && Date().timeIntervalSince(lastLineAt) >= bubbleGapS {
        if let (text, kind) = pickLine(mood, avoid: lastLine,
                                      cringe: cringeChance, sentence: sentenceChance) {
            lastLine = text
            lastLineAt = Date()
            noteSaid(text)              // the same memory the mood-change path writes (see `pickFresh`)
            view.say(text)
            // ...and WHICH line it was, for WHICH mood and out of WHICH table (`kind`), named `on=tick`
            // so "the clock talks too" is something her log can answer rather than something you have to
            // catch on screen — and so a cringe line or a whole sentence can be told from an ordinary one.
            print("buffy-pip says=\(text) mood=\(mood) balloon=\(view.balloonSide) on=tick kind=\(kind)")
            fflush(stdout)
        }
    }
    // ...and the TABLE is watched on the same tick, so a pace or a pose retuned by hand is worn
    // without a restart — which is the whole point of the table being a file. One `stat` per tick, a
    // re-read only when the identity changed (or the file went away, which puts the defaults back),
    // and one line of evidence, because "did my edit land" has to be answerable from outside.
    let stamp = fileStamp(moodsFile)
    if stamp != moodsStamp {
        moodsStamp = stamp
        let rows = applyMoodOverrides(framesOnDisk: frames.count)
        view.setMood(mood)                       // this mood, with the numbers as they now are
        print("buffy-pip moods=\(moodsFile) rows=\(rows) moods-on-disk=\(frames.count)")
        fflush(stdout)
    }
    // ...and WHAT SHE SAYS is watched on the same tick, for the same reason: a joke added by hand should be
    // one she can say at the next mood change rather than one she says after a restart.
    let linesNow = fileStamp(linesFile)
    if linesNow != linesStamp {
        linesStamp = linesNow
        print("buffy-pip lines=\(linesFile) moods=\(applyLineOverrides())")
        fflush(stdout)
    }
    // ...and the KNOBS, on the same tick and for the same reason: `fbtodo pip tune transition 2000` should be
    // a length she is wearing a moment later rather than one the next launch will wear.  Only the knobs that
    // came from the FILE move (a flag and an environment variable outrank it), and the mood is re-worn so a
    // transition or a pose floor that changed takes effect on the dissolve about to happen.
    let tuneNow = fileStamp(tuneFile)
    if tuneNow != tuneStamp {
        tuneStamp = tuneNow
        let knobs = applyTune()
        view.setMood(mood)
        print("buffy-pip tune=\(tuneFile) knobs=\(knobs) "
              + "transition=\(Int(transitionS * 1000))ms "
              + "bubble=\(bubbleOn ? "on" : "off") gap=\(Int(bubbleGapS))s")
        fflush(stdout)
    }
    frontPid = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
    let nowFree = FileManager.default.fileExists(atPath: freeFile)
    // Followed every tick, not read once: the pane's window MOVES — it is resized when the list
    // is long, the app is on another Space, the reader drags it — and a pin that only holds at
    // launch is a window that ends up over the wrong thing by teatime.
    let target = home()
    let want = NSRect(origin: target.origin, size: NSSize(width: target.size, height: target.size))
    // ...compared against the slot she is GOING to rather than the frame she is sitting at: a move
    // that is already in flight IS the answer, and asking for it again every tick would restart the
    // ease and never arrive (see `glideTo`).
    let drifted = abs(glideTo.origin.x - want.origin.x) > 2
        || abs(glideTo.origin.y - want.origin.y) > 2
        || abs(glideTo.width - want.width) > 0.5
    if nowFree != free {
        let wasFree = free
        free = nowFree
        window.isMovableByWindowBackground = free
        // ...and the way back is where she is TAUGHT: free is the only state she can be dragged in,
        // so whatever position she was left in is the pin from now on, recorded against the
        // window's own top-left corner.
        if wasFree, !free, let host = findHost() {
            let taught = CGPoint(x: window.frame.origin.x - host.rect.origin.x,
                                 y: topEdge(window.frame) - host.rect.origin.y)
            writePin(taught)
            print("buffy-pip taught pin=\(Int(taught.x)),\(Int(taught.y))")
        }
        // A state CHANGE is worth a line on stdout: it is the only way anything outside this
        // process can tell that the pin it just released was read and acted on, and it costs
        // one line per press of the button.
        print("buffy-pip mode=\(free ? "free" : "stuck") at=\(Int(window.frame.origin.x)),\(Int(window.frame.origin.y))")
        fflush(stdout)
    }
    // She is ordered OUT when the pane has no room for her — never drawn over the list that filled
    // it — and back IN the moment it has some, which is why this is a visibility flip and not a
    // teardown. Both events say so on stdout, once each: a window that is not there has to be
    // explainable from outside this process.
    //
    // PLACED FIRST, SHOWN SECOND.  The flip used to come before the move, so the tick that brought
    // her back ordered her in at whatever corner she had while she was away and moved her a frame
    // later — a visible hop, and on the restore-from-minimize path the whole of "she takes a while
    // to come back".  One tick does both now, and there is nothing to see in between.
    if !free {
        // PLACED FIRST, SHOWN SECOND, and EASED IN BETWEEN.  A move is drawn over `transitionS` now
        // (see `glide`), which is what the old code bought with its ordering: the flip used to come
        // before the move, so the tick that brought her back ordered her in at whatever corner she had
        // while she was away and moved her a frame later — a visible hop, and on the restore-from-
        // minimize path the whole of "she takes a while to come back".  A window nobody can see has
        // nothing to ease — she is arriving from nowhere — so it is PLACED, with the destination
        // recorded, and only a window the reader is already looking at glides.
        let asked = !target.hidden
        if asked {
            if !ordered {
                // She is coming back: PLACED here, at the slot she will be seen in, because a window
                // nobody can see has nothing to ease — she would be arriving from the last place she
                // stood, which is a fence-post rather than a transition ("placed first, shown second").
                if want != glideTo {
                    window.setFrame(want, display: false)
                    glideTo = want
                }
            } else if drifted {
                glide(want)
            }
        } else if !ordered, want != glideTo {
            // On her way out, or already gone: the slot she will come back to is set DIRECTLY, and only
            // once she is off the screen — a window still fading out must not jump under the fade.
            window.setFrame(want, display: false)
            glideTo = want
        }
        // She is asked OUT when the pane has no room for her — never drawn over the list that filled it
        // — and back IN the moment it has some, which is why this is a visibility flip and not a
        // teardown. Both events say so on stdout, once each, and both are FADES now: a window that is
        // not there has to be explainable from outside this process, and a window that vanishes between
        // one look and the next is the one thing this program was asked not to do.  The way OUT waits a
        // tick (`outSince`): a measurement that found nothing is mostly a pane mid-relayout.
        if asked {
            outSince = nil
            if !wantShown { showWindow() }
        } else if wantShown {
            if let since = outSince {
                if Date().timeIntervalSince(since) >= hideDebounceS {
                    outSince = nil
                    hideWindow()
                }
            } else {
                outSince = Date()
            }
        }
        if asked {
            // Both the corner and the size: a slot that got shorter has to shrink her, not just move
            // her, or she hangs over the pane's own border and stops looking like part of the pane.
            // The view is told too, because it draws the art into its own `bounds`.
            view.frame = NSRect(x: 0, y: 0, width: want.width, height: want.height)
            view.prepare(points: want.width)           // her pixels, at the size she is drawn at
        }
    }
    // Where she should stand is measured off the pane's own drawing, on a slow clock and again the
    // moment the window she was measured in is not the window in front of her any more (moved,
    // resized, replaced) — which is the whole of "she follows a resize". A taught pin makes all of
    // it unnecessary.
    if !free, readPin() == nil, let host = findHost() {
        // A window that just came BACK ON SCREEN (a restore from the Dock, the app returning to this
        // Space) is measured on this tick rather than on the next slow one: what she is standing on
        // was measured before it went away, and "quite a bit delay" is that clock, not the capture.
        if let was = hostOnscreen, !was, host.onscreen { lastFitAt = Date.distantPast }
        hostOnscreen = host.onscreen
        let wait = needsFit(for: host) ? fitRetrySeconds : fitSeconds
        if Date().timeIntervalSince(lastFitAt) >= wait {
            lastFitAt = Date()
            refreshFit(host)
        }
    } else {
        hostOnscreen = nil                   // no host to remember: the next one is a fresh look
    }
}
RunLoop.current.add(pinTimer, forMode: .common)

// One line of evidence on stdout — the geometry and the window LEVEL, which is what "on top of
// everything" means and the only part of this that cannot be seen in the source.
DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
    let f = window.frame
    // The screen's own height comes with it, because this line is also the RECORD of where she
    // was put: Cocoa counts from the bottom and `screencapture -R` counts from the top, so a
    // capture of her corner needs both numbers to know which way up it is.
    let host = findHost()
    let anchored = hostPids.isEmpty ? "name" : "pids:\(hostPids.sorted().map { String($0) }.joined(separator: ","))"
    let pin = readPin()
    // The fields are built one string at a time rather than as one long `+` chain: the chain stopped
    // type-checking in reasonable time the moment the knobs above became mutable globals, and a record is
    // not worth a compile that takes a minute.
    var fields: [String] = []
    fields.append("buffy-pip frames=\(frames.count)")
    fields.append("side=\(Int(f.width))")
    fields.append("at=(\(Int(f.origin.x)),\(Int(f.origin.y)))")
    fields.append("level=\(window.level.rawValue)")
    fields.append("visible=\(window.isVisible)")
    fields.append("mode=\(free ? "free" : "stuck")")
    fields.append("mood=\(mood)")
    fields.append("switch=\(freeFile)")
    fields.append("pin=\(pin.map { "\(Int($0.x)),\(Int($0.y))" } ?? "centre (taught: none)")")
    fields.append("fit=\(readFit().note)")
    fields.append("host=\(hostOwner)")
    fields.append("by=\(anchored)")
    if let box = host {
        fields.append("hostBounds=\(Int(box.rect.origin.x)),\(Int(box.rect.origin.y)) "
                      + "\(Int(box.rect.width))x\(Int(box.rect.height)) pid=\(box.pid) "
                      + "owner=\(box.owner) onscreen=\(box.onscreen)")
    } else {
        fields.append("hostBounds=none (screen centre)")
    }
    fields.append("screenFrame=\(Int(screen.frame.width))x\(Int(screen.frame.height))")
    fields.append("transition=\(Int(transitionS * 1000))ms")
    fields.append("alpha=\(Int(window.alphaValue * 100))%")
    print(fields.joined(separator: " "))
    fflush(stdout)
}

// ...and the two verbs that take a capture by hand, `--doctor` and `--shot`: LIVE measurements, so they
// are scheduled here rather than run as top-level code above — half a second after the run loop starts,
// which is long enough for the capture to be allowed and short enough that the command still feels like
// a command (see `doctorRun` and `shotRun`, and the measurement in `shotRun`'s own comment: run from
// the top level, `--shot` was refused on every attempt).
if args.contains("--doctor") {
    DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { exit(doctorRun()) }
}
if let out = option("--shot") {
    DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { exit(shotRun(out)) }
}

app.run()
