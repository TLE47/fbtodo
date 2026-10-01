#!/usr/bin/env bash
# Self-test for the freebuff tab-title timer, the finish bell, the freebuff-bell toggle
# and the phone push (decision matrix + phone.sh mechanics, sender stubbed).
# Run with: bash ~/.config/freebuff-notify/test-freebuff-notify.sh
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
ZRC="$HOME/.zshrc"
# ---- the suite's clock. Nothing is asserted against wall time here, so the two clocks the
# suite has to wait for are turned down and every fixed `sleep` that waited one out is
# correspondingly short (see session-timer.sh's FREEBUFF_TIMER_SECONDS):
#   * the tab-title timer ticks at 0.2 s instead of 1 s (its task refresh and ring-when-done
#     are counted in ticks, so 3 and 5 ticks go from 3 s / 5 s to 0.6 s / 1 s)
#   * the stub session lives 0.6 s instead of 2 s (FB_SLEEP; 0.4 measured the same wall
#     time — the cost is the ~285 checks' own waits, not the stub)
# Between them these were ~70 s of the suite's 72 s (measured 2026-09-23).
export FREEBUFF_TIMER_SECONDS=0.2
FB_SLEEP=${FB_SLEEP:-0.6}
export FB_SLEEP
SANDBOX="$(mktemp -d)"
TIMERS=""
cleanup() {
  [ -n "$TIMERS" ] && kill $TIMERS 2>/dev/null
  rm -rf "$SANDBOX"
}
trap cleanup EXIT
fails=0

# Per-check timing, opt-in with FBTODO_NOTIFY_TIME=1: `PASS  …` then `  [+0.4s 12s]`.
# The suite costs 72 s and no single sleep explains it — a hundred-odd checks, each with a
# stub and a wait, do. That is the same instrument fbtodo-selfcheck.py carries, and it is
# how the expensive checks were found instead of guessed at (measured 2026-09-23).
# EPOCHREALTIME needs bash 5 (this Mac's PATH bash); the fallback is whole seconds.
SUITE_START=${EPOCHREALTIME:-$(date +%s)}
LAST_STAMP=$SUITE_START

stamp() {
  local now=${EPOCHREALTIME:-$(date +%s)}
  if [ -n "${FBTODO_NOTIFY_TIME:-}" ]; then
    awk -v a="$now" -v b="$LAST_STAMP" -v s="$SUITE_START" \
      'BEGIN { printf "  [+%.1fs %.0fs]\n", a - b, a - s }'
  fi
  LAST_STAMP=$now
}

check() { # label, actual, expected
  if [ "$2" = "$3" ]; then
    echo "PASS  $1"
  else
    echo "FAIL  $1: got [$2] want [$3]"
    fails=$((fails + 1))
  fi
  stamp
}

check_match() { # label, actual, regex
  if printf '%s' "$2" | grep -Eq "$3"; then
    echo "PASS  $1"
  else
    echo "FAIL  $1: [$2] does not match /$3/"
    fails=$((fails + 1))
  fi
  stamp
}

# Every sandbox home that sources the wrapper's functions needs bell.sh, which is what
# `_freebuff-ring` plays through, and drop-bell.py, which the wrapper's end-of-session
# decision is asked of.
install_bell() {
  mkdir -p "$1/.config/freebuff-notify"
  cp "$HERE/bell.sh" "$HERE/drop-bell.py" "$1/.config/freebuff-notify/"
  chmod +x "$1/.config/freebuff-notify/bell.sh" "$1/.config/freebuff-notify/drop-bell.py"
}

# Every one of these is RUN BY PATH — from the wrapper, from fbtodo's watcher, from the
# skill's commands — so a missing exec bit is a feature that silently does nothing (the
# sandbox copies are chmod'ed, which is exactly how that hid for a whole session).
for f in bell.sh drop-bell.py phone.sh session-timer.sh session-task.py todo-bell.py ask-bell.py pause-bell.py pane-bell.py; do
  check "$f is executable where it lives" "$([ -x "$HERE/$f" ] && echo yes || echo no)" "yes"
done

titles() { tr '\007' '\n' <"$1" 2>/dev/null | grep -oE '(⏱|✓ Done) freebuff [0-9hms]+( · .*)?'; }
bells() { tr -cd '\007' <"$1" 2>/dev/null | wc -c | tr -d ' '; }

# ---- the only two waits in this suite -------------------------------------------------
# Every positive check is "wait until X, then assert X". A fixed `sleep` is either too short
# — it fails on a loaded machine, which is this suite's one known flake, and why `pb_wait`
# exists below — or too long, and wastes the run. So there are no fixed waits left: a side
# effect is polled until it lands, and a check that asserts NO side effect polls the same
# cap and asserts the counter never moved, which gives a wrong play MORE chances to show up
# than the 0.4 s sleep it replaced. Caps are whole seconds (bash's SECONDS is an integer).
wait_for() { # cap-s, predicate, args...
  local cap=$1 pred=$2
  shift 2
  local deadline=$((SECONDS + cap))
  while ! "$pred" "$@"; do
    [ "$SECONDS" -ge "$deadline" ] && return 1
    sleep 0.05
  done
  return 0
}

wait_for_change() { # cap-s, counter, was — returns as soon as the count differs
  local cap=$1 counter=$2 was=$3
  local deadline=$((SECONDS + cap))
  while [ "$("$counter")" = "$was" ]; do
    [ "$SECONDS" -ge "$deadline" ] && return 1
    sleep 0.05
  done
  return 0
}

wait_counter_is() { # cap-s, counter, want
  wait_for "$1" counter_is "$2" "$3"
}
counter_is() { [ "$("$1")" = "$2" ]; }

# The tab-title timer reports its OWN elapsed seconds, so assert the number is right at the
# moment the title is read rather than that it falls inside a window a stalled machine can
# step out of — `^⏱ freebuff 1m0[6-9]s$` failed under load for exactly that reason.
title_ready() { [ -n "$(titles "$1" | tail -1)" ]; }

title_seconds() { # "1m07s", "1h02m05s", "12s" -> 67, 3725, 12
  local v=$1 h=0 m=0 s=0
  case $v in *h*) h=${v%%h*}; v=${v#*h} ;; esac
  case $v in *m*) m=${v%%m*}; v=${v#*m} ;; esac
  case $v in *s*) s=${v%%s*} ;; esac
  printf '%s' "$((10#${h:-0} * 3600 + 10#${m:-0} * 60 + 10#${s:-0}))"
}

check_elapsed() { # label, start-epoch, tty, marker, [slack-s]
  local label=$1 start=$2 tty=$3 marker=$4 slack=${5:-2} title want got shape
  wait_for 3 title_ready "$tty"
  title=$(titles "$tty" | tail -1)
  want=$(($(date +%s) - start))
  got=$(title_seconds "${title##*freebuff }")
  shape="^$(printf '%s' "$marker" | sed 's/[][\.*^$]/\\&/g') freebuff [0-9hms]+( · .*)?\$"
  if printf '%s' "$title" | grep -Eq "$shape" && [ -n "$got" ] \
     && [ "$got" -ge $((want - slack)) ] && [ "$got" -le $((want + slack + 1)) ]; then
    echo "PASS  $label"
  else
    echo "FAIL  $label: [$title] reports ${got:-?}s while ${want}s have passed"
    fails=$((fails + 1))
  fi
  stamp
}

# No freebuff session descends from this test shell, so titles stay bare here.
export FREEBUFF_PROJECTS_DIR="$SANDBOX/no-sessions"
mkdir -p "$FREEBUFF_PROJECTS_DIR"

echo "== session-timer.sh =="
TTY="$SANDBOX/titles.log"
START=$(($(date +%s) - 67))
sh "$HERE/session-timer.sh" run "$START" "$TTY" &
TIMERS=$!
check_elapsed "live running title" "$START" "$TTY" '⏱'
kill $TIMERS 2>/dev/null
wait $TIMERS 2>/dev/null
TIMERS=""
sh "$HERE/session-timer.sh" finish "$START" "$TTY"
check_elapsed "done stamp on finish" "$START" "$TTY" '✓ Done'

HOURS="$SANDBOX/hours.log"
HOURS_START=$(($(date +%s) - 3725))
sh "$HERE/session-timer.sh" run "$HOURS_START" "$HOURS" &
TIMERS=$!
check_elapsed "hours format past 1h" "$HOURS_START" "$HOURS" '⏱'
kill $TIMERS 2>/dev/null
wait $TIMERS 2>/dev/null
TIMERS=""

echo
echo "== what the session is working on =="
FAKE="$SANDBOX/projects"
SESSION="$FAKE/demo/chats/2026-01-01T00-00-00.000Z"
mkdir -p "$SESSION"
sleep 30 &
OUR_PID=$!
TIMERS="$TIMERS $OUR_PID"
printf '{"level":"DEBUG","pid":%s,"data":{}}\n' "$OUR_PID" >"$SESSION/log.jsonl"
echo '{"messageCount":1,"firstPrompt":"the very first prompt"}' >"$SESSION/chat-meta.json"

write_messages() { # prompt, bytes of assistant text that follow it
  python3 - "$SESSION" "$1" "$2" <<'PY'
import json, os, sys
session, prompt, tail = sys.argv[1], sys.argv[2], int(sys.argv[3])
# same shape freebuff writes, including the variant field between id and content
messages = [
    {"id": "user-1", "variant": "user", "content": "the very first prompt", "timestamp": "01:00 PM"},
    {"id": "ai-2", "variant": "ai", "content": "x" * tail, "timestamp": "01:01 PM"},
    {"id": "user-3", "variant": "user", "content": prompt, "timestamp": "01:02 PM"},
]
open(os.path.join(session, "chat-messages.json"), "w").write(json.dumps(messages))
PY
}
task() { FREEBUFF_PROJECTS_DIR="$FAKE" python3 "$HERE/session-task.py" "$1" 44; }

write_messages "make the tab title shorter" 10
touch -t 202601010000 "$SESSION/chat-messages.json"
check "reports the current prompt" "$(task $$)" "make the tab title shorter"

# a newer session from another terminal must not caption this tab
FOREIGN="$FAKE/other/chats/2026-01-01T00-05-00.000Z"
mkdir -p "$FOREIGN"
printf '{"level":"DEBUG","pid":1,"data":{}}\n' >"$FOREIGN/log.jsonl"
python3 - "$FOREIGN" <<'PY'
import json, os, sys
open(os.path.join(sys.argv[1], "chat-messages.json"), "w").write(json.dumps(
    [{"id": "user-9", "variant": "user", "content": "someone elses prompt",
      "timestamp": "01:05 PM"}]))
PY
touch -t 202601010005 "$FOREIGN/chat-messages.json"
check "ignores other terminals" "$(task $$)" "make the tab title shorter"

# a long prompt with tabs and newlines, buried behind a big assistant message
write_messages $'show  the current\tprompt   in the tab\ntitle with newlines and tabs and a lot more text after it' 400000
check_match "truncates at a word boundary" "$(task $$)" '^show the current prompt in the tab title…$'

write_messages "a brand new prompt" 10
check "follows the latest prompt" "$(task $$)" "a brand new prompt"

TASK_TTY="$SANDBOX/task-titles.log"
FREEBUFF_PROJECTS_DIR="$FAKE" sh "$HERE/session-timer.sh" run "$(date +%s)" "$TASK_TTY" &
TASK_TIMER=$!
TIMERS="$TIMERS $TASK_TIMER"
wait_for 3 title_matches "$TASK_TTY" 'a brand new prompt'
check_match "running title carries the prompt" "$(titles "$TASK_TTY" | tail -1)" \
  '^⏱ freebuff [0-9]s · a brand new prompt$'
FREEBUFF_PROJECTS_DIR="$FAKE" sh "$HERE/session-timer.sh" finish "$(date +%s)" "$TASK_TTY"
check_match "done title carries the prompt" "$(titles "$TASK_TTY" | tail -1)" \
  '^✓ Done freebuff [0-9]s · a brand new prompt$'
kill $TASK_TIMER 2>/dev/null
wait $TASK_TIMER 2>/dev/null
TIMERS=""

echo
echo "== wrapper: tab-title timer =="
STUB="$SANDBOX/stub"
mkdir -p "$STUB/bin" "$STUB/.config/freebuff-notify"
# FB_EXIT is what the drop tests make the session die with; the default is a clean quit.
printf '#!/bin/sh\necho "stub-freebuff $*"\nsleep ${FB_SLEEP:-2}\nexit ${FB_EXIT:-0}\n' >"$STUB/bin/freebuff"
# A stub fbtodo, so the end-of-session decision reads the sandbox's store and never the
# real machine's (a live session here is mid-turn, which would look like a drop).
printf '#!/bin/sh\ncat "%s" 2>/dev/null\n' "$STUB/state.json" >"$STUB/bin/fbtodo"
printf '%s\n' '{"backend":"cli","cwd":"/tmp","session":"s1","list_id":"L1","done":1,"total":1,"turn_ended":true}' >"$STUB/state.json"
cp "$HERE/session-timer.sh" "$STUB/.config/freebuff-notify/session-timer.sh"
install_bell "$STUB"
chmod +x "$STUB/bin/freebuff" "$STUB/bin/fbtodo" "$STUB/.config/freebuff-notify/session-timer.sh"
sed -n '/^# freebuff:/,$p' "$ZRC" >"$STUB/funcs.zsh"

WRAP_TTY="$SANDBOX/wrap.log"
run_wrapper() { # HOME, tty
  # FBTODO_NO_PANE: this suite is about the notify path, and the wrapper's pane would
  # otherwise open in the OWNER's tmux (this shell inherits $TMUX) and cost a fork and a
  # `fbtodo` startup that has nothing to do with what is under test.
  PATH="$STUB/bin:$PATH" HOME="$1" FREEBUFF_TTY="$2" FBTODO_NO_PANE=1 \
    zsh -c 'source "$1/funcs.zsh"; freebuff a b' zsh "$STUB"
}

# This phase keeps the long stand-in session: the check below reads the session's
# WHOLE-SECOND duration out of the title (`✓ Done freebuff [23]s`), so the session has to
# last seconds — the 0.6 s the rest of the suite runs on can land in the same wall second
# as the start and report `0s`. One phase, ~2 s, not one cost per check.
export FB_SLEEP=2
run_wrapper "$STUB" "$WRAP_TTY" >"$SANDBOX/out.log" 2>&1 &
WRAPPER=$!
# Read the title while the stub session is still alive, but stop as soon as it is there:
# the fixed 1.2 s this used to be had to fit inside the stub's lifetime, which is what
# kept that lifetime at 2 s. Polling is both faster and less timing-sensitive.
RUNNING=""
for _ in $(seq 1 20); do
  RUNNING="$(titles "$WRAP_TTY" | tail -1)"
  case "$RUNNING" in "⏱ freebuff "*) break ;; esac
  sleep 0.05
  kill -0 "$WRAPPER" 2>/dev/null || break
done
check_match "timer visible while running" "$RUNNING" '^⏱ freebuff [0-9]s$'
wait $WRAPPER
export FB_SLEEP=0.6  # back to the fast stub for the rest of the suite
check "args reach freebuff" "$(cat "$SANDBOX/out.log")" "stub-freebuff a b"
check_match "done title after exit" "$(titles "$WRAP_TTY" | tail -1)" '^✓ Done freebuff [23]s$'

echo
echo "== wrapper: chime on finish only =="
PLAIN="$SANDBOX/plain"                 # no session-timer.sh, so only the chime writes
mkdir -p "$PLAIN/.config/freebuff-notify" "$PLAIN/bin"
cp "$STUB/bin/freebuff" "$PLAIN/bin/freebuff"
printf '#!/bin/sh\ncat "%s" 2>/dev/null\n' "$PLAIN/state.json" >"$PLAIN/bin/fbtodo"
printf '%s\n' '{"backend":"cli","cwd":"/tmp","session":"s1","list_id":"L1","done":1,"total":1,"turn_ended":true}' >"$PLAIN/state.json"
# a sender that records what it was asked to send (one line per push), so a session that
# dies on its own can be shown to push and a plain quit to stay silent
printf '#!/bin/sh\nprintf "%%s\\n" "$*" | tr "\\n" "~" >>"%s"\nprintf "\\n" >>"%s"\n' \
  "$PLAIN/sends.log" "$PLAIN/sends.log" >"$PLAIN/.config/freebuff-notify/phone.sh"
chmod +x "$PLAIN/bin/fbtodo" "$PLAIN/.config/freebuff-notify/phone.sh"
install_bell "$PLAIN"
# stub afplay so the tests stay silent while proving what would be played
cat >"$PLAIN/bin/afplay" <<EOF
#!/bin/sh
echo "afplay \$*" >>"$SANDBOX/afplay.log"
EOF
chmod +x "$PLAIN/bin/afplay"
BELL_TTY="$SANDBOX/bell.log"
CHIMES="$SANDBOX/afplay.log"

run_plain() { PATH="$PLAIN/bin:$PATH" HOME="$PLAIN" FREEBUFF_TTY="$BELL_TTY" \
  FBTODO_NO_PANE=1 zsh -c 'source "$1/funcs.zsh"; freebuff' zsh "$STUB"; }
plays() { if [ -r "$CHIMES" ]; then grep -c afplay "$CHIMES"; else echo 0; fi; }
bells_at_least() { [ "$(bells "$BELL_TTY")" -ge "$1" ]; }
title_matches() { titles "$1" | tail -1 | grep -q "$2"; }
# the chime is backgrounded, so wait for the stub to log rather than guess
wait_plays() { # want, up to 4s
  wait_counter_is 4 plays "$1"
}

echo on >"$PLAIN/.config/freebuff-notify/state"
echo Glass >"$PLAIN/.config/freebuff-notify/sound"
run_plain >/dev/null 2>&1
wait_plays 1
check "chimes once when enabled" "$(plays)" "1"
check "plays the saved sound, quietly" "$(tail -1 "$CHIMES" 2>/dev/null)" \
  "afplay -v 0.5 /System/Library/Sounds/Glass.aiff"

echo off >"$PLAIN/.config/freebuff-notify/state"
run_plain >/dev/null 2>&1
wait_for_change 1 plays 1   # a wrong ring has one whole second to show up
check "silent while muted" "$(plays)" "1"

(FREEBUFF_BELL=on run_plain) >/dev/null 2>&1
wait_plays 2
check "FREEBUFF_BELL=on overrides state" "$(plays)" "2"

(FREEBUFF_BELL=off run_plain) >/dev/null 2>&1
wait_for_change 1 plays 2
check "FREEBUFF_BELL=off overrides state" "$(plays)" "2"

(FREEBUFF_BELL=on FREEBUFF_BELL_SOUND=/nope/missing.aiff run_plain) >/dev/null 2>&1
wait_for 2 bells_at_least 1   # the fallback bell itself is the condition now
check "unplayable sound falls back to the bell" "$(bells "$BELL_TTY")" "1"
check "and does not chime" "$(plays)" "2"
# silence here is what made a stale shell look like "it played a Mac sound"
warn=$((FREEBUFF_BELL=on FREEBUFF_BELL_SOUND=/nope/missing.aiff run_plain) 2>&1 >/dev/null)
check_match "and says why it fell back" "$warn" "cannot play '/nope/missing.aiff'"

echo
echo "== freebuff-bell toggle =="
FAKE_HOME="$SANDBOX/toggle-home"
mkdir -p "$FAKE_HOME/.config/freebuff-notify"
install_bell "$FAKE_HOME"
out=$(HOME="$FAKE_HOME" zsh -c '
  source "$1/funcs.zsh"
  freebuff-bell status             # no state file yet -> on
  freebuff-bell off
  freebuff-bell                    # bare = status, must not change
  freebuff-bell toggle
  FREEBUFF_BELL=off freebuff-bell status
  FREEBUFF_BELL=on freebuff-bell status
' zsh "$STUB")
check "toggle sequence" "$(printf '%s' "$out" | tr '\n' '|')" \
  "freebuff bell: on|freebuff bell: off|freebuff bell: off|freebuff bell: on|freebuff bell: off|freebuff bell: on"
check "saved state after toggle" "$(cat "$FAKE_HOME/.config/freebuff-notify/state")" "on"
HOME="$FAKE_HOME" zsh -c 'source "$1/funcs.zsh"; freebuff-bell bogus' zsh "$STUB" 2>/dev/null
check "bogus arg exits 2" "$?" "2"

echo
echo "== freebuff-bell sound =="
SOUND_HOME="$SANDBOX/sound-home"
mkdir -p "$SOUND_HOME/.config/freebuff-notify"
install_bell "$SOUND_HOME"
sound_cmd() {
  local before
  before=$(plays)
  PATH="$PLAIN/bin:$PATH" HOME="$SOUND_HOME" zsh -c \
    'source "$1/funcs.zsh"; freebuff-bell "${@:2}"' zsh "$STUB" "$@"
  local ret=$?
  wait_for_change 1 plays "$before" # the preview it plays is backgrounded
  return $ret
}

check_match "lists the system sounds" "$(sound_cmd sound)" '^Glass  <- current$'
out=$(sound_cmd sound hero)          # matches case-insensitively
check "sets and previews the pick" "$out" "freebuff sound: Hero"
check "pick is saved" "$(cat "$SOUND_HOME/.config/freebuff-notify/sound")" "Hero"
check "preview played it" "$(tail -1 "$CHIMES")" \
  "afplay -v 0.5 /System/Library/Sounds/Hero.aiff"

# resolved from a fresh shell with no caller locals in scope: zsh's dynamic
# scoping once hid a saved pick that only worked inside freebuff-bell
check "a saved system sound is honoured on its own" \
  "$(HOME="$SOUND_HOME" zsh -c 'source "$1/funcs.zsh"; _freebuff-bell-file "$(_freebuff-bell-sound)"' zsh "$STUB")" \
  "/System/Library/Sounds/Hero.aiff"

before=$(plays)
sound_cmd sound Nope >/dev/null 2>&1
check "unknown sound exits 2" "$?" "2"
check "unknown sound changes nothing" "$(cat "$SOUND_HOME/.config/freebuff-notify/sound")" "Hero"
check "unknown sound plays nothing" "$(plays)" "$before"

out=$(FREEBUFF_BELL_VOLUME=0.25 sound_cmd test)
check "test previews the current sound" "$(tail -1 "$CHIMES")" \
  "afplay -v 0.25 /System/Library/Sounds/Hero.aiff"
check_match "test reports the pick and the file it resolved to" "$out" \
  '^played: Hero \(/System/Library/Sounds/Hero.aiff\)$'

echo
echo "== alert sound =="
gen=$(python3 "$HERE/make-sound.py" "$SANDBOX/gen.wav" 2>&1)
check_match "generator defaults to the piano F4" "$gen" 'fundamental confirmed at F4 \(piano F4\)'
check_match "generator uses a real recorded piano note" "$gen" 'source: .*"Piano MF E3\(L\)"'
check_match "generator takes one mic of the stereo pair" "$gen" 'left mic of the stereo pair'
check_match "generator shifts the E4 sample up to F4" "$gen" 'measured E4 → shifted \+1\.00 semitones to F4'
check_match "generator makes a quarter-second tap" "$gen" '0\.25s kept from'
check_match "generator reports the decay" "$gen" 'decays [0-9.]+ dB across the note'
# the GM piano holds full level for its first 150ms, so a plain cut reads as a
# held blip; the tap must lose real level by halfway and be well down by the end
check_match "generator shows it falls away like a struck note" "$gen" \
  'falls -[0-9.]+ / -[0-9.]+ / -2[0-9]\.[0-9] dB'
check "generator writes a wav" "$(head -c 4 "$SANDBOX/gen.wav")" "RIFF"
check "the tap really is a quarter second long" \
  "$(python3 -c 'import sys, wave; w = wave.open(sys.argv[1]); print(round(w.getnframes() / w.getframerate(), 2))' "$SANDBOX/gen.wav")" \
  "0.25"

# the note must decay cleanly: no hole or second attack, and a silent tail
envelope=$(python3 - "$SANDBOX/gen.wav" <<'PY'
import array, math, sys, wave
handle = wave.open(sys.argv[1])
rate = handle.getframerate()
pcm = array.array("h")
pcm.frombytes(handle.readframes(handle.getnframes()))
block = rate // 20
levels = []
for start in range(0, len(pcm) - block, block):
    chunk = pcm[start:start + block]
    levels.append(math.sqrt(sum(v * v for v in chunk) / len(chunk)))
peak = max(levels) or 1.0
# a hole in the tail or a second attack shows up as a jump back up mid-note;
# from block 2 on, a rise is never legitimate (the first blocks are the attack)
jumps = sum(1 for i in range(2, len(levels))
            if levels[i] > 0.4 * peak and levels[i] > 1.6 * levels[i - 1])
tail = math.sqrt(sum(v * v for v in pcm[-rate // 200:]) / max(1, rate // 200))
print("clean" if not jumps and tail < 0.02 * peak else f"dirty jumps={jumps} tail={tail / peak:.3f}")
# a held blip would still be near full level in its last block; a tap is not
print(f"last block {20 * math.log10(levels[-1] / peak):.1f} dB below peak")
PY
)
check "decays cleanly and ends in silence" "$(printf '%s' "$envelope" | sed -n 1p)" "clean"
# the final 50 ms must be well down (a held blip would still be near full level)
check_match "and is percussive, not a held blip" "$(printf '%s' "$envelope" | sed -n 2p)" \
  '^last block -([1-9][0-9]+)\.[0-9] dB below peak$'

steel=$(python3 "$HERE/make-sound.py" --voice steel "$SANDBOX/gen-steel.wav" 2>&1)
check_match "the steel string is still available" "$steel" 'source: .*"Steel D4"'
check_match "the steel D is already at pitch, so barely shifted" "$steel" 'shifted [-+]0\.0[01] semitones to D3'
check_match "the guitar voices keep their longer note" "$steel" '1\.00s kept from'
tap=$(python3 "$HERE/make-sound.py" --seconds 0.15 "$SANDBOX/gen-tap.wav" 2>&1)
check_match "--seconds overrides the length" "$tap" '0\.15s kept from'
nylon=$(python3 "$HERE/make-sound.py" --voice nylon "$SANDBOX/gen-nylon.wav" 2>&1)
check_match "the nylon string is still available" "$nylon" 'source: .*"Nylon D4"'

VOICE_HOME="$SANDBOX/voice-home"
mkdir -p "$VOICE_HOME/.config/freebuff-notify"
install_bell "$VOICE_HOME"
cp "$SANDBOX/gen.wav" "$VOICE_HOME/.config/freebuff-notify/piano.wav"
cp "$SANDBOX/gen-steel.wav" "$VOICE_HOME/.config/freebuff-notify/steel.wav"
cp "$SANDBOX/gen-nylon.wav" "$VOICE_HOME/.config/freebuff-notify/nylon.wav"
voice_cmd() {
  local before
  before=$(plays)
  PATH="$PLAIN/bin:$PATH" HOME="$VOICE_HOME" zsh -c \
    'source "$1/funcs.zsh"; freebuff-bell "${@:2}"' zsh "$STUB" "$@"
  local ret=$?
  wait_for_change 1 plays "$before" # the preview it plays is backgrounded
  return $ret
}

out=$(voice_cmd sound)
check_match "the piano note is the default once generated" "$out" '^piano  <- current'
check_match "and the voice list says which note it is" "$out" 'piano  <- current  \(recorded piano F4\)'
check_match "the guitar voices are still offered" "$out" '^steel           \(recorded steel guitar open D\)'

# a voice with a wav must resolve even if no case entry names it, so adding a
# voice can never silently ring the Mac bell through a missing <name>.aiff
cp "$SANDBOX/gen.wav" "$VOICE_HOME/.config/freebuff-notify/trumpet.wav"
check "any generated voice resolves without a case entry" \
  "$(HOME="$VOICE_HOME" zsh -c 'source "$1/funcs.zsh"; _freebuff-bell-file trumpet' zsh "$STUB")" \
  "$VOICE_HOME/.config/freebuff-notify/trumpet.wav"

before=$(plays)
voice_cmd sound steel >/dev/null 2>&1
check "plays the steel string" "$(tail -1 "$CHIMES")" \
  "afplay -v 0.5 $VOICE_HOME/.config/freebuff-notify/steel.wav"
check "steel pick is saved" "$(cat "$VOICE_HOME/.config/freebuff-notify/sound")" "steel"
check "one chime per preview" "$(plays)" "$((before + 1))"

before=$(plays)
voice_cmd sound nylon >/dev/null 2>&1
check "switches to the nylon string" "$(tail -1 "$CHIMES")" \
  "afplay -v 0.5 $VOICE_HOME/.config/freebuff-notify/nylon.wav"
check "nylon pick is saved" "$(cat "$VOICE_HOME/.config/freebuff-notify/sound")" "nylon"
check "one chime per preview" "$(plays)" "$((before + 1))"
check "the old guitar alias still resolves" "$(voice_cmd sound guitar)" "freebuff sound: steel"

check "volume defaults to 0.5" "$(voice_cmd volume)" "freebuff volume: 0.5"
check "volume can be halved" "$(voice_cmd volume 0.25)" "freebuff volume: 0.25"
check "volume is saved" "$(cat "$VOICE_HOME/.config/freebuff-notify/volume")" "0.25"
check "the new volume is used" "$(tail -1 "$CHIMES")" \
  "afplay -v 0.25 $VOICE_HOME/.config/freebuff-notify/steel.wav"
voice_cmd volume loud >/dev/null 2>&1
check "invalid volume exits 2" "$?" "2"

before=$(plays)
check "switches to the piano F4" "$(voice_cmd sound piano)" "freebuff sound: piano"
check "and plays it at the saved volume" "$(tail -1 "$CHIMES")" \
  "afplay -v 0.25 $VOICE_HOME/.config/freebuff-notify/piano.wav"
check "piano pick is saved" "$(cat "$VOICE_HOME/.config/freebuff-notify/sound")" "piano"
check "the f4 alias resolves to the piano note" "$(voice_cmd sound f4)" "freebuff sound: piano"
check "one chime per preview" "$(plays)" "$((before + 2))"

echo
echo "== bell.sh resolves what the picker resolves =="
# The todo bell, the session wrapper and the interactive picker must not drift: two
# resolutions of one setting is how "it rang the wrong thing" happens.
CMP="$SANDBOX/cmp-home"
mkdir -p "$CMP/.config/freebuff-notify"
cp "$HERE/bell.sh" "$CMP/.config/freebuff-notify/bell.sh"
chmod +x "$CMP/.config/freebuff-notify/bell.sh"
cp "$VOICE_HOME/.config/freebuff-notify/"*.wav "$CMP/.config/freebuff-notify/" 2>/dev/null
cmp_zsh() {
  HOME="$CMP" zsh -c \
    'source "$1/funcs.zsh"; printf "%s %s" "$(_freebuff-bell-file "$(_freebuff-bell-sound)")" "$(_freebuff-bell-volume)"' \
    zsh "$STUB"
}
cmp_sh() {
  HOME="$CMP" "$CMP/.config/freebuff-notify/bell.sh" --print |
    awk '{for(i=1;i<=NF;i++){split($i,kv,"=");v[kv[1]]=kv[2]}} END{print v["file"], v["volume"]}'
}
for pick in piano steel nylon trumpet Hero; do
  printf '%s\n' "$pick" >"$CMP/.config/freebuff-notify/sound"
  check "bell.sh resolves $pick as the picker does" "$(cmp_sh)" "$(cmp_zsh)"
done
printf '%s\n' 0.25 >"$CMP/.config/freebuff-notify/volume"
check "and picks up a saved volume the same way" "$(cmp_sh)" "$(cmp_zsh)"
check "and --print reports through the off switch" "$(FREEBUFF_BELL=off cmp_sh)" "$(cmp_zsh)"

echo
echo "== the todo bell: done means the task finished, not that the terminal went quiet =="
TB="$SANDBOX/todo-bell"
mkdir -p "$TB/bin" "$TB/home/.config/freebuff-notify" "$TB/notify"
cp "$HERE/todo-bell.py" "$HERE/bell.sh" "$TB/notify/"
chmod +x "$TB/notify/todo-bell.py" "$TB/notify/bell.sh"
printf '#!/bin/sh\ncat "%s" 2>/dev/null\n' "$TB/state.json" >"$TB/bin/fbtodo"
printf '#!/bin/sh\necho "afplay $*" >>"%s"\n' "$TB/plays.log" >"$TB/bin/afplay"
chmod +x "$TB/bin/fbtodo" "$TB/bin/afplay"
tb_plays() { if [ -r "$TB/plays.log" ]; then grep -c afplay "$TB/plays.log"; else echo 0; fi; }
tb_state() { printf '%s\n' "$1" >"$TB/state.json"; }
tb_clear() { rm -f "$TB/rang.json"; }
tb_todo_bell() { # one invocation of the bell under test, in its sandbox
  PATH="$TB/bin:$PATH" HOME="$TB/home" FREEBUFF_TODO_BELL_STATE="$TB/rang.json" \
    python3 "$TB/notify/todo-bell.py" "$$" --tty "$TB/tty.log" "$@"
}
# bell.sh backgrounds the actual playback, so the play lands a moment after python exits.
# Wait for it (bounded) rather than sleeping a fixed guess: this returns the moment the
# count moves — faster than the flat 0.4 s it replaced, and the check can no longer pass
# by luck on a warm cache or fail on a cold one (measured 2026-09-23: the first ring of the
# phase needed ~0.4 s, which is exactly what the guess was).
tb_run() {
  local before after i=0
  before=$(tb_plays)
  tb_todo_bell "$@"
  case " $* " in *" --print "*) return 0 ;; esac
  while [ "$i" -lt 20 ]; do
    after=$(tb_plays)
    [ "$after" != "$before" ] && return 0
    sleep 0.05
    i=$((i + 1))
  done
}
# ...and this for the checks that assert NO ring: there is nothing to wait for, so the
# evidence is a short grace in which a wrong ring would have shown up.
tb_run_silent() {
  local before
  before=$(tb_plays)
  tb_todo_bell "$@" >/dev/null 2>&1
  wait_for_change 1 tb_plays "$before"   # as above: silence is polled for, not slept on
}
DONE='{"backend":"cli","session":"s1","list_id":"L1","done":3,"total":3,"turn_ended":true}'
BUSY="{\"backend\":\"cli\",\"session\":\"s1\",\"list_id\":\"L1\",\"done\":3,\"total\":3,\"turn_ended\":false}"
MID='{"backend":"cli","session":"s1","list_id":"L1","done":2,"total":3,"turn_ended":true}'
NOLIST='{"backend":"cli","session":"s1","list_id":"x","done":0,"total":0,"turn_ended":true}'
NAS='{"backend":"nas","session":"s1","list_id":"L1","done":3,"total":3,"turn_ended":true}'

tb_clear
tb_state "$DONE"
tb_run >/dev/null
check "rings when every todo is done and the turn ended" "$(tb_plays)" "1"
check_match "and remembers which list it rang for" "$(cat "$TB/rang.json")" '"rung": {"L1"'
tb_run_silent
check "a finished list on screen rings only once" "$(tb_plays)" "1"

tb_state "${DONE/L1/L2}"
tb_run >/dev/null
check "a new list can ring again" "$(tb_plays)" "2"

tb_state "$MID"
tb_run_silent
check "silent while tasks remain" "$(tb_plays)" "2"

# the case that rules out a quiet-window heuristic: all the todos are ticked, and the
# agent is four minutes into a command that writes nothing to the journal
tb_state "$BUSY"
out=$(tb_run --print)
check "silent through a long command, with every box ticked" "$(tb_plays)" "2"
check_match "--print names the turn, not the tick boxes" "$out" \
  'all 3 done, but the agent has not finished its turn'

tb_state "$NOLIST"
tb_run_silent
check "silent before there is a list" "$(tb_plays)" "2"

tb_state "$NAS"
tb_run_silent
check "silent for a NAS session (no journal to ask)" "$(tb_plays)" "2"

tb_clear
echo off >"$TB/home/.config/freebuff-notify/state"
tb_state "$DONE"
out=$(tb_run --print)
check_match "muted still reports it would ring" "$out" '^RING:'
check "...and plays nothing" "$(tb_plays)" "2"
rm -f "$TB/home/.config/freebuff-notify/state"
tb_run >/dev/null
check "unmuted, one ring for the list that was muted" "$(tb_plays)" "3"

# a dead fbtodo must not turn a completed task into a silent nothing
tb_state ''
tb_run >/dev/null
check "an unreadable state is silent, not an error" "$?" "0"
tb_state "$DONE"
check_match "a NAS-shaped state is asked about locally, never invented" \
  "$(tb_run --print)" '^(RING|silent):'

before=$(tb_plays)
rm -f "$TB/notify/todo-bell.state"
cp "$HERE/session-timer.sh" "$TB/notify/session-timer.sh"
chmod +x "$TB/notify/session-timer.sh"
PATH="$TB/bin:$PATH" HOME="$TB/home" FREEBUFF_PROJECTS_DIR="$TB/no-sessions" \
  sh "$TB/notify/session-timer.sh" run "$(date +%s)" "$TB/timer.log" &
TB_TIMER=$!
TIMERS="$TIMERS $TB_TIMER"
tb_plays_at_least() { [ "$(tb_plays)" -ge "$1" ]; }
wait_for 20 tb_plays_at_least "$((before + 1))"
kill $TB_TIMER 2>/dev/null
wait $TB_TIMER 2>/dev/null
TIMERS=""
check "the running session rings the moment the task is finished" "$(tb_plays)" "$((before + 1))"
check "and the tab title kept its clock meanwhile" \
  "$(titles "$TB/timer.log" | tail -1 | grep -cE '^⏱ freebuff [0-9]+s$')" "1"

echo
echo "== the phone push: the bell's question, answered for the NAS too =="
PB="$SANDBOX/phone"
mkdir -p "$PB/bin" "$PB/notify" "$PB/home"
cp "$HERE/todo-bell.py" "$HERE/bell.sh" "$PB/notify/"
chmod +x "$PB/notify/todo-bell.py" "$PB/notify/bell.sh"
# A sender that records what it was asked to send: the DECISION is what is under test
# here (phone.sh's own mechanics get their own block below).
# A stand-in for phone.sh: --print answers the readiness question (the notifier asks it
# before following a NAS session), and anything else is recorded as ONE line — the body
# carries a newline (goal, then count), so it is flattened: lines = pushes.
printf '#!/bin/sh\ncase "$*" in *--print*) printf "url=stub topic=set token= enabled=on\\n"; exit 0;; esac\nprintf "%%s\\n" "$*" | tr "\\n" "~" >>"%s"\nprintf "\\n" >>"%s"\n' \
  "$PB/sends.log" "$PB/sends.log" >"$PB/notify/phone.sh"
chmod +x "$PB/notify/phone.sh"
printf '#!/bin/sh\ncat "%s" 2>/dev/null\n' "$PB/state.json" >"$PB/bin/fbtodo"
chmod +x "$PB/bin/fbtodo"
pb_sends() { if [ -r "$PB/sends.log" ]; then wc -l <"$PB/sends.log" | tr -d ' '; else echo 0; fi; }
# The push is DETACHED (the timer must not wait on a network), so a count read straight
# after the decision can beat the sender to it — which made this suite fail under load.
# Bounded, and only where a push is due: a silent decision pays nothing.
pb_sends_at_least() { [ "$(pb_sends)" -ge "$1" ]; }
pb_wait() { # expected sends
  wait_for 5 pb_sends_at_least "$1"; n=0  # `n` is read by callers below
}
pb_run() { # json, then extra args; the push is detached, so wait for it to land
  local before
  before=$(pb_sends)
  printf '%s\n' "$1" >"$PB/state.json"
  shift
  # TZ is pinned so a rendered finish time is asserted exactly, not approximated.
  PATH="$PB/bin:$PATH" HOME="$PB/home" TZ=UTC FREEBUFF_BELL=off \
    FREEBUFF_TODO_BELL_STATE="$PB/bell.json" FREEBUFF_PHONE_SH="$PB/notify/phone.sh" \
    python3 "$PB/notify/todo-bell.py" "$$" "$@"
  case " $* " in *" --print "*) : ;; *) wait_for_change 1 pb_sends "$before" ;; esac
  return 0
}
NOW=$(date +%s)
PDONE='{"backend":"cli","cwd":"/Users/x/proj","session":"s1","list_id":"P1","done":3,"total":3,"turn_ended":true,"store_mtime_ms":1000,"goal":"make the push land"}'
PBUSY='{"backend":"cli","session":"s1","list_id":"P1","done":3,"total":3,"turn_ended":false,"store_mtime_ms":1000}'
PMID='{"backend":"cli","session":"s1","list_id":"P1","done":2,"total":3,"turn_ended":true,"store_mtime_ms":1000}'
PNAS="{\"backend\":\"nas\",\"session\":\"s2\",\"list_id\":\"N1\",\"done\":4,\"total\":4,\"instance_alive\":true,\"store_mtime_ms\":$(((NOW - 600) * 1000)),\"goal\":\"finish the NAS run\"}"
PNASFRESH="{\"backend\":\"nas\",\"session\":\"s2\",\"list_id\":\"N1\",\"done\":4,\"total\":4,\"instance_alive\":true,\"store_mtime_ms\":$((NOW * 1000))}"
PNASDEAD="{\"backend\":\"nas\",\"session\":\"s2\",\"list_id\":\"N1\",\"done\":4,\"total\":4,\"instance_alive\":false,\"store_mtime_ms\":$(((NOW - 600) * 1000))}"

pb_run "$PDONE" >/dev/null
pb_wait 1
check "pushes when the local task is finished" "$(pb_sends)" "1"
check_match "and the push carries the count" "$(cat "$PB/sends.log")" '3/3 steps done'
check_match "...with the agent's own words left out by default" \
  "$(printf %s "$(cat "$PB/sends.log")" | grep -c 'make the push land' || true)" '^0$'
pb_run "$PDONE" >/dev/null
check "a finished list that just sits there pushes once" "$(pb_sends)" "1"
# The same list with the store moved on is still the SAME list: keying the claim on the
# mtime as well pushed a live NAS session's finished list every couple of minutes (its
# transcript is rewritten per turn), which is what gets a topic muted on the phone.
pb_run "${PDONE/1000/2000}" >/dev/null
check "the same list with the store moved stays silent" "$(pb_sends)" "1"
pb_run "${PDONE/P1/P2}" >/dev/null
pb_wait 2
check "but a new list that finishes pushes again" "$(pb_sends)" "2"
pb_run "$PMID" >/dev/null
check "silent while tasks remain" "$(pb_sends)" "2"
out=$(pb_run "$PBUSY" --print)
check "silent through a long command, with every box ticked" "$(pb_sends)" "2"
check_match "--print names the turn for the push too" "$out" \
  'no push: all 3 done, but the agent has not finished its turn'
pb_run "$PNASFRESH" >/dev/null
check "silent while the NAS store is still moving" "$(pb_sends)" "2"
out=$(pb_run "$PNASFRESH" --print)
check_match "...and says why" "$out" 'the store is still moving'
pb_run "$PNAS" >/dev/null
pb_wait 3
check "pushes for a NAS session whose list is done and store gone quiet" "$(pb_sends)" "3"
check_match "the NAS push carries its metadata" "$(tail -1 "$PB/sends.log")" '4/4 steps done'
check_match "...and not its prose" \
  "$(printf %s "$(tail -1 "$PB/sends.log")" | grep -c 'finish the NAS run' || true)" '^0$'
pb_run "$PNAS" >/dev/null
check "and not twice for the same quiet store" "$(pb_sends)" "3"
pb_run "$PNASDEAD" >/dev/null
check "silent when the NAS session is gone" "$(pb_sends)" "3"
out=$(pb_run "$PNASDEAD" --print)
check_match "...and says so" "$out" 'no NAS session is running'
# A fresh record on purpose: P1 was pushed earlier in this block, and the claim now
# remembers every list it has pushed (that memory is the fix), so reusing P1 here would
# probe the repeat branch instead of the wording this check is about.
rm -f "$PB/sends.log" "$PB/bell.json"
out=$(pb_run "$PDONE" --print)
check "report-only pushes nothing" "$(pb_sends)" "0"
check_match "report-only says PUSH and why" "$out" '^PUSH: all 3 todos done and the turn ended'

# Two NAS watchers can be alive at once (a stale lock, an upgrade race) and ask at
# their own phase: the second must see the first's claim rather than send again.
printf '%s\n' "$PNAS" >"$PB/state.json"
rm -f "$PB/sends.log" "$PB/bell.json" "$PB/bell.json.lock"
for _ in 1 2; do
  PATH="$PB/bin:$PATH" HOME="$PB/home" FREEBUFF_BELL=off \
    FREEBUFF_TODO_BELL_STATE="$PB/bell.json" FREEBUFF_PHONE_SH="$PB/notify/phone.sh" \
    python3 "$PB/notify/todo-bell.py" --nas-watch --once --quiet &
done
wait
pb_wait 1
check "two racing passes push once" "$(pb_sends)" "1"
PATH="$PB/bin:$PATH" HOME="$PB/home" FREEBUFF_BELL=off \
  FREEBUFF_TODO_BELL_STATE="$PB/bell.json" FREEBUFF_PHONE_SH="$PB/notify/phone.sh" \
  python3 "$PB/notify/todo-bell.py" --nas-watch --once --quiet >/dev/null 2>&1
wait_for_change 1 pb_sends 1
check "and a later pass stays silent for the same finish" "$(pb_sends)" "1"

# The same race on the LOCAL path, which is the one the timer drives every 5s and the one
# that had no lock at all. One shell can carry two timers (the wrapper kills its timer when
# `command freebuff` returns, but an interrupted launch never gets there, and a timer's own
# exit condition is the launching shell — still alive), and both ask at their own phase.
# Measured in the real log before this was locked: two iMessage and two ntfy in the SAME
# second for one finish (`freebuff done · myproj`, 2026-09-24 14:33:38).
printf '%s\n' "${PDONE/P1/P4}" >"$PB/state.json"
rm -f "$PB/sends.log" "$PB/bell.json" "$PB/bell.json.lock"
for _ in 1 2; do
  PATH="$PB/bin:$PATH" HOME="$PB/home" TZ=UTC FREEBUFF_BELL=off \
    FREEBUFF_TODO_BELL_STATE="$PB/bell.json" FREEBUFF_PHONE_SH="$PB/notify/phone.sh" \
    python3 "$PB/notify/todo-bell.py" "$$" >/dev/null 2>&1 &
done
wait
pb_wait 1
check "two racing LOCAL passes push once" "$(pb_sends)" "1"
PATH="$PB/bin:$PATH" HOME="$PB/home" TZ=UTC FREEBUFF_BELL=off \
  FREEBUFF_TODO_BELL_STATE="$PB/bell.json" FREEBUFF_PHONE_SH="$PB/notify/phone.sh" \
  python3 "$PB/notify/todo-bell.py" "$$" >/dev/null 2>&1
wait_for_change 1 pb_sends 1
check "and a later local pass stays silent for the same finish" "$(pb_sends)" "1"

# THE BUG THIS PINS: the claim was ONE slot, so the NAS watcher and a local session
# overwrote each other's — and then each pushed again on every pass, which is the
# "freebuff done" landing every ~15s until the thread gets muted. Two instances must
# each hold their own claim, however often the other one asks.
rm -f "$PB/sends.log" "$PB/bell.json"
pb_run "$PDONE" >/dev/null
pb_run "$PNAS" --nas-watch --once >/dev/null
pb_wait 2
check "two instances push once each" "$(pb_sends)" "2"
check_match "the claims live in a map, not one shared slot" "$(cat "$PB/bell.json")" '"pushed"'
pb_run "$PNAS" --nas-watch --once >/dev/null
pb_run "$PDONE" >/dev/null
pb_run "$PNAS" --nas-watch --once >/dev/null
pb_run "$PDONE" >/dev/null
check "and alternating passes do not resurrect either claim" "$(pb_sends)" "2"

# An upgrade must not itself fire one more push: the deployed state file has the old
# single slot, so it is folded into the map rather than ignored.
printf '%s\n' '{"pushed_list_id":"P1","pushed_ms":1}' >"$PB/bell.json"
rm -f "$PB/sends.log"
pb_run "$PDONE" >/dev/null
check "an old single-slot claim is honoured, not re-pushed" "$(pb_sends)" "0"

# A push that cannot be dated is useless hours later, and the count says how much of the
# list it got through. store_mtime_ms 1790038500000 renders as 2026-09-22 00:55 under
# TZ=UTC. The DEFAULT IS METADATA: the goal, the summary and the question are written by a
# model and arrive on a phone, where a link, a number or an instruction reads as real — so
# they are opt-in (`FREEBUFF_PHONE_TEXT=agent`), and the metadata always goes.
PWHEN='{"backend":"cli","cwd":"/Users/x/proj","session":"s9","list_id":"P9","done":3,"total":3,"turn_ended":true,"store_mtime_ms":1790038500000,"goal":"ship the fix","summary":"the watchdog now leaves a live session alone"}'
rm -f "$PB/sends.log" "$PB/bell.json"
pb_run "$PWHEN" >/dev/null
pb_wait 1
out=$(tail -1 "$PB/sends.log")
check_match "the default push carries the count" "$out" '3/3 steps done'
check_match "...the date and time it finished" "$out" '2026-09-22 00:55'
check_match "...and the session it was, so a quiet push can still be traced" "$out" \
  'session s9'
check_match "...and not one word the agent wrote" \
  "$(printf %s "$out" | grep -c 'ship the fix' || true)" '^0$'
check_match "...nor what it said it did" \
  "$(printf %s "$out" | grep -c 'leaves a live session alone' || true)" '^0$'

# ...and the prose comes back only when the owner asks for it
rm -f "$PB/sends.log" "$PB/bell.json"
FREEBUFF_PHONE_TEXT=agent pb_run "$PWHEN" >/dev/null
pb_wait 1
out=$(tail -1 "$PB/sends.log")
check_match "FREEBUFF_PHONE_TEXT=agent carries the big goal" "$out" 'Goal: ship the fix'
check_match "below it, what the agent said it did" "$out" \
  'Goal: ship the fix~the watchdog now leaves a live session alone'
check_match "...and the count last" "$out" 'the watchdog now leaves a live session alone~3/3 steps done'

# An empty summary must not leave a blank line between the heading and the count.
PNOSUM='{"backend":"cli","cwd":"/Users/x/proj","session":"s9x","list_id":"P9b","done":1,"total":1,"turn_ended":true,"store_mtime_ms":1790038500000,"goal":"no prose over there","summary":null}'
rm -f "$PB/sends.log" "$PB/bell.json"
FREEBUFF_PHONE_TEXT=agent pb_run "$PNOSUM" >/dev/null
pb_wait 1
check_match "a run with no summary keeps the two-line body" \
  "$(tail -1 "$PB/sends.log")" 'Goal: no prose over there~1/1 steps done'

# A run with no `Goal:` line must not have its opening request labelled as one.
PNOWHEN='{"backend":"cli","cwd":"/Users/x/proj","session":"s9","list_id":"P8","done":3,"total":3,"turn_ended":true,"store_mtime_ms":1790038500000,"first_prompt":"why is the bell silent"}'
rm -f "$PB/sends.log"
FREEBUFF_PHONE_TEXT=agent pb_run "$PNOWHEN" >/dev/null
pb_wait 1
check_match "a list with no goal falls back to the request, unlabelled" \
  "$(tail -1 "$PB/sends.log")" 'why is the bell silent~3/3 steps done'
rm -f "$PB/sends.log"
pb_run "$PNOWHEN" >/dev/null
pb_wait 1
check_match "...and off by default that request is not sent either" \
  "$(printf %s "$(tail -1 "$PB/sends.log")" | grep -c 'why is the bell silent' || true)" '^0$'

PNASRUN="{\"backend\":\"nas\",\"session\":\"/srv/app/state/manicode/projects/chats/2026-09-21T21-49-13.448Z\",\"list_id\":\"N9\",\"done\":2,\"total\":2,\"instance_alive\":true,\"store_mtime_ms\":$(((NOW - 600) * 1000)),\"goal\":\"tidy the settings panel\"}"
rm -f "$PB/sends.log"
pb_run "$PNASRUN" --nas-watch --once >/dev/null
pb_wait 1
check_match "a NAS push names the run, not just the clock" "$(tail -1 "$PB/sends.log")" \
  'NAS freebuff done · run 21:49:13'

# the NAS watcher asks the notifier on its own clock
out=$(PATH="$PB/bin:$PATH" HOME="$PB/home" FREEBUFF_BELL=off \
  FREEBUFF_TODO_BELL_STATE="$PB/bell.json" FREEBUFF_PHONE_SH="$PB/notify/phone.sh" \
  python3 "$PB/notify/todo-bell.py" --nas-watch --once --print 2>&1)
check_match "--nas-watch answers for the NAS store" "$out" '^(PUSH|no push):'

echo
echo "== phone.sh: the topic never reaches argv, and failures stay bounded =="
PH="$SANDBOX/phonemech"
mkdir -p "$PH/bin" "$PH/home/.config/freebuff-notify"
cp "$HERE/phone.sh" "$PH/home/.config/freebuff-notify/phone.sh"
chmod +x "$PH/home/.config/freebuff-notify/phone.sh"
# One line per argument, preceded by a #call marker: the args can then be grepped for
# what must NOT be there, and the calls counted.
PCTL="$PH/curl.log"
printf '#!/bin/sh\nprintf "%%s\\n" "#call" "$@" >>"%s"\nprintf "%%s" "${PH_CURL_CODE:-200}"\n' "$PCTL" >"$PH/bin/curl"
chmod +x "$PH/bin/curl"
# ...and the AppleScript sender, stubbed the same way: one line per call, and it can be
# made to fail, which is the whole fallback question.
OSA="$PH/osa.log"
printf '#!/bin/sh\nprintf "%%s\\n" "#osa" "$@" >>"%s"\nprintf "stub Messages said no\\n" >&2\nexit ${PH_OSA_CODE:-0}\n' \
  "$OSA" >"$PH/bin/osa"
chmod +x "$PH/bin/osa"
ph() { PATH="$PH/bin:$PATH" HOME="$PH/home" FREEBUFF_OSASCRIPT="$PH/bin/osa" \
  "$PH/home/.config/freebuff-notify/phone.sh" "$@"; }
ph_conf() { # topic, then an optional iMessage handle and transport
  f="$PH/home/.config/freebuff-notify/phone.conf"
  : >"$f"
  chmod 600 "$f"
  printf 'NTFY_URL=https://ntfy.example\n' >>"$f"
  if [ -n "$1" ]; then printf 'NTFY_TOPIC=%s\n' "$1" >>"$f"; fi
  if [ -n "${2:-}" ]; then printf 'IMESSAGE_TO=%s\n' "$2" >>"$f"; fi
  if [ -n "${3:-}" ]; then printf 'FREEBUFF_PHONE_TRANSPORT=%s\n' "$3" >>"$f"; fi
}
pcalls() { if [ -r "$PCTL" ]; then grep -c '^#call' "$PCTL"; else echo 0; fi; }
osa_calls() { if [ -r "$OSA" ]; then grep -c '^#osa' "$OSA"; else echo 0; fi; }

ph_conf "topic-secret-abc"
out=$(ph --dry-run --title t --message m)
check_match "dry-run shows the request it would make" "$out" '^would send: curl'
check "dry-run keeps the topic out of the output" \
  "$(printf '%s' "$out" | grep -c 'topic-secret-abc')" "0"
check "dry-run sends nothing" "$(pcalls)" "0"
out=$(ph --print)
check_match "--print reports the resolved target" "$out" 'topic=set'
check "--print sends nothing" "$(pcalls)" "0"

ph --title t --message m >/dev/null 2>&1
check "a configured phone.sh sends" "$?" "0"
check_match "curl read the url from a config file" "$(cat "$PCTL")" '^--config$'
check "the topic stays out of curl's argv" "$(grep -c 'topic-secret-abc' "$PCTL")" "0"
check "the request file is cleaned up" \
  "$(ls -a "$PH/home/.config/freebuff-notify" | grep -c 'phone-request')" "0"
check_match "with bounded timeouts" "$(cat "$PCTL")" '^--max-time$'
check_match "and curl's own retry of the transient classes" "$(cat "$PCTL")" '^--retry$'

# A message that starts with `@` is DATA, not a path: curl's `-d @file` READS THE FILE and
# posts its contents, so `@/etc/hostname` would have leaked the host's name to the topic.
# `--data-raw` says "this is the body", and the body must arrive as the literal text. A CR
# or LF in a header value is header injection, so the three header fields lose both first.
raw_before=$(grep -c '^--data-raw$' "$PCTL")
ph --title $'t\nX-Injected: yes' --message "@/etc/hostname" \
  --tags $'a\nb' --priority $'default\nX-Evil: 1' >/dev/null 2>&1
check "a message that starts with @ still sends" "$?" "0"
check "the body is taken raw, not read from a file" \
  "$(grep -c '^--data-raw$' "$PCTL")" "$((raw_before + 1))"
check "the @ message is the literal request body" \
  "$(grep -cxF '@/etc/hostname' "$PCTL")" "1"
check "a newline in the title cannot inject a header" \
  "$(grep -cx 'X-Injected: yes' "$PCTL")" "0"
check "a newline in the tags cannot inject a header" \
  "$(grep -cx 'b' "$PCTL")" "0"
check "a newline in the priority cannot inject a header" \
  "$(grep -cx 'X-Evil: 1' "$PCTL")" "0"

n=$(pcalls)
PH_CURL_CODE=500 ph --title t --message m >/dev/null 2>&1
check "a 500 is reported as a failure" "$?" "69"
check_match "and logged with the code" "$(tail -1 "$PH/home/.config/freebuff-notify/phone.log")" 'http=500'
check "...after exactly one request, no queueing" "$(pcalls)" "$((n + 1))"

echo off >"$PH/home/.config/freebuff-notify/phone-state"
PH_CURL_CODE=500 ph --title t --message m >/dev/null 2>&1
check "the mute switch exits 0" "$?" "0"
check "and sends nothing at all" "$(pcalls)" "$((n + 1))"
rm -f "$PH/home/.config/freebuff-notify/phone-state"

ph_conf ""
err=$(ph --title t --message m 2>&1 >/dev/null)
check "no topic is a config error" "$?" "78"
check_match "...that names the missing setting" "$err" 'NTFY_TOPIC'
ph_conf "bad topic!"
ph --title t --message m >/dev/null 2>&1
check "an unusable topic is refused" "$?" "78"

echo
echo "== phone.sh: iMessage first, ntfy when Messages cannot send =="
# The transport question is decided once, in phone.sh, so every caller (the finish bell
# and the drop watch) gets the same answer without knowing either one exists.
ph_conf "topic-secret-abc" "someone@icloud.com"        # auto, the default
n=$(pcalls)
o=$(osa_calls)
ph --title t --message m >/dev/null 2>&1
check "auto sends by iMessage when it can" "$?" "0"
check "...without touching ntfy" "$(pcalls)" "$n"
check "...exactly one send" "$(osa_calls)" "$((o + 1))"
check_match "the sender was given the handle" "$(cat "$OSA")" '^someone@icloud\.com$'
check_match "and the title as the first line" "$(cat "$OSA")" '^t$'
# The body is an ARGUMENT too — `on run {target, body}` reads it from argv — so a message
# carrying quotes, `&` or AppleScript of its own arrives as written instead of becoming
# source. H8 asked whether phone.sh builds the script by interpolation: it does not, and
# this is what keeps it that way.
tricky='say "hi" & do shell script "rm -rf /"'
ph --title 'a "title"' --message "$tricky" >/dev/null 2>&1
check_match "a hostile body reaches osascript as one argument" "$(tail -1 "$OSA")" \
  '^say "hi" & do shell script "rm -rf /"$'
check_match "...and never as script text" "$(grep -c '^-e.*rm -rf /' "$OSA" || true)" '^0$'
check_match "and logged as an iMessage, handle masked" \
  "$(tail -1 "$PH/home/.config/freebuff-notify/phone.log")" 'sent imessage to so…@icloud.com'

n=$(pcalls)
o=$(osa_calls)
out=$(PH_OSA_CODE=1 ph --title t --message m 2>&1 >/dev/null)
check "a Messages that cannot send still delivers" "$?" "0"
check "...by ntfy" "$(pcalls)" "$((n + 1))"
check "...after one attempt" "$(osa_calls)" "$((o + 1))"
check_match "...saying it fell back" "$out" 'falling back to ntfy'

# pinned: a failure is a failure, because ntfy must not send what was not asked for
ph_conf "topic-secret-abc" "someone@icloud.com" "imessage"
n=$(pcalls)
o=$(osa_calls)
PH_OSA_CODE=1 ph --title t --message m >/dev/null 2>&1
check "iMessage-only fails loudly when Messages cannot send" "$?" "69"
check "...without falling back to ntfy" "$(pcalls)" "$n"
check "...after trying" "$(osa_calls)" "$((o + 1))"
check_match "...and says so in the log" \
  "$(tail -1 "$PH/home/.config/freebuff-notify/phone.log")" 'FAILED imessage'
ph --title t --message m >/dev/null 2>&1
check "and sends when it can" "$?" "0"

ph_conf "topic-secret-abc" "someone@icloud.com" "ntfy"
o=$(osa_calls)
n=$(pcalls)
ph --title t --message m >/dev/null 2>&1
check "transport=ntfy never calls Messages" "$(osa_calls)" "$o"
check "...and sends by ntfy as before" "$(pcalls)" "$((n + 1))"

err=$(ph_conf "topic-secret-abc" "" "imessage"; ph --title t --message m 2>&1 >/dev/null)
check "pinning iMessage without a handle is a config error" "$?" "78"
check_match "...that names the missing setting" "$err" 'IMESSAGE_TO'

# both: down two independent pipes every time, for when either one alone goes quiet at the
# far end — a hidden self-thread silent while ntfy still lands, or the reverse.
ph_conf "topic-secret-abc" "someone@icloud.com" "both"
o=$(osa_calls)
n=$(pcalls)
ph --title t --message m >/dev/null 2>&1
check "both delivers by iMessage and by ntfy" "$?" "0"
check "...once by iMessage" "$(osa_calls)" "$((o + 1))"
check "...and once by ntfy, not instead" "$(pcalls)" "$((n + 1))"
check_match "the log records both deliveries, in one line each" \
  "$(tail -2 "$PH/home/.config/freebuff-notify/phone.log" | tr '\n' ' ')" \
  'sent imessage to so…@icloud.com t.*sent ntfy t'

# A Messages that cannot send must not take the ntfy half down with it, and the other way
# round: one landing is one notification.
o=$(osa_calls)
n=$(pcalls)
PH_OSA_CODE=1 ph --title t --message m >/dev/null 2>&1
check "both still delivers when Messages cannot send" "$?" "0"
check "...after trying iMessage" "$(osa_calls)" "$((o + 1))"
check "...and sending by ntfy anyway" "$(pcalls)" "$((n + 1))"
PH_CURL_CODE=500 ph --title t --message m >/dev/null 2>&1
check "both tolerates a refused ntfy when iMessage landed" "$?" "0"
check_match "...and still logs the half that failed" \
  "$(tail -1 "$PH/home/.config/freebuff-notify/phone.log")" 'FAILED rc=0 http=500'

# Half the pair is not the pair: asked for by name, so a missing half is reported rather
# than quietly downgraded to iMessage-only.
o=$(osa_calls)
err=$(ph_conf "" "someone@icloud.com" "both"; ph --title t --message m 2>&1 >/dev/null)
check "both without a topic is a config error" "$?" "78"
check_match "...that names the missing setting" "$err" 'NTFY_TOPIC'
check "...and does not send the half it could" "$(osa_calls)" "$o"
check_match "--print reports both, with the handle masked" \
  "$(ph_conf "topic-secret-abc" "someone@icloud.com" "both"; ph --print)" \
  'transport=both imessage=so…@icloud.com'
o=$(osa_calls)
n=$(pcalls)
out=$(ph --dry-run --title t --message m)
check_match "dry-run shows the iMessage it would send" "$out" 'would send: iMessage'
check_match "...and the ntfy request too" "$out" 'would send: curl'
check "dry-run sends neither way" "$(osa_calls)-$(pcalls)" "$o-$n"
echo off >"$PH/home/.config/freebuff-notify/phone-state"
ph --title t --message m >/dev/null 2>&1
check "the mute switch mutes both halves" "$(osa_calls)-$(pcalls)" "$o-$n"
rm -f "$PH/home/.config/freebuff-notify/phone-state"

ph_conf "topic-secret-abc" "someone@icloud.com"
check_match "--print reports the transport and a masked handle" "$(ph --print)" \
  'transport=auto imessage=so…@icloud.com'
check_match "dry-run shows the iMessage it would send" "$(ph --dry-run --title t --message m)" \
  '^would send: iMessage to so…@icloud\.com — t m$'
o=$(osa_calls)
n=$(pcalls)
ph --dry-run --title t --message m >/dev/null 2>&1
check "dry-run sends neither way" "$(osa_calls)-$(pcalls)" "$o-$n"
echo off >"$PH/home/.config/freebuff-notify/phone-state"
ph --title t --message m >/dev/null 2>&1
check "the mute switch mutes iMessage too" "$(osa_calls)" "$o"
rm -f "$PH/home/.config/freebuff-notify/phone-state"

# the topic has to be revealed exactly as far as the app needs it, and no further
ph_conf "topic-secret-abc"
out=$(ph --init 2>&1)
check_match "--init refuses to overwrite an existing config" "$out" 'already exists'

# ...and a fresh config's topic is 128 bits of hex, drawn from /dev/urandom: on a public
# ntfy server the TOPIC is the authentication, so it is never a typed word and never a
# guess, and it is never an argument (argv is world-readable in `ps`).
rm -f "$PH/home/.config/freebuff-notify/phone.conf"
out=$(ph --init 2>&1)
check_match "--init mints a new topic" "$out" 'subscribe in the ntfy app to:  freebuff-'
check_match "...128 random bits of it" "$(sed -n 's/^NTFY_TOPIC=freebuff-\([0-9a-f]*\)$/\1/p' \
  "$PH/home/.config/freebuff-notify/phone.conf" | tr -d '\n' | wc -c | tr -d ' ')" '32'

echo
echo "== the drop watch: it died on its own, and what it said =="
# `drop-bell.py` answers the question the finish bell cannot: did this session end, or
# DROP? What is under test is the DECISION and the body it composes, so the sender is a
# stub that records what it was asked to send, one line per push.
DW="$SANDBOX/dropwatch"
mkdir -p "$DW/bin" "$DW/notify" "$DW/home/.config/freebuff-notify"
cp "$HERE/drop-bell.py" "$HERE/bell.sh" "$DW/notify/"
chmod +x "$DW/notify/drop-bell.py" "$DW/notify/bell.sh"
printf '#!/bin/sh\ncat "%s" 2>/dev/null\n' "$DW/state.json" >"$DW/bin/fbtodo"
printf '#!/bin/sh\necho "afplay $*" >>"%s"\n' "$DW/plays.log" >"$DW/bin/afplay"
# ...with the body's newlines flattened to ~ and a newline of its own, so one push is one
# line (tr eats the trailing newline otherwise, and wc -l then counts zero for one push)
printf '#!/bin/sh\nprintf "%%s\\n" "$*" | tr "\\n" "~" >>"%s"\nprintf "\\n" >>"%s"\n' \
  "$DW/sends.log" "$DW/sends.log" >"$DW/notify/phone.sh"
chmod +x "$DW/bin/fbtodo" "$DW/bin/afplay" "$DW/notify/phone.sh"
dw_plays() { if [ -r "$DW/plays.log" ]; then grep -c afplay "$DW/plays.log"; else echo 0; fi; }
dw_sends() { if [ -r "$DW/sends.log" ]; then wc -l <"$DW/sends.log" | tr -d ' '; else echo 0; fi; }
# The chime and the push are both backgrounded, so wait for the stub to log rather than
# guess with a sleep — a fixed delay is a flake waiting to happen on a loaded machine.
dw_wait() { # helper, want, up to 5s
  wait_counter_is 5 "$1" "$2"
}
mkdir -p "$DW/proj"
dw_run() { # state json, then drop-bell args; the chime is backgrounded, so wait for it
  printf '%s\n' "$1" >"$DW/state.json"
  shift
  local before
  before=$(dw_sends)
  PATH="$DW/bin:$PATH" HOME="$DW/home" FREEBUFF_DROP_STATE="$DW/claim.json" \
    FREEBUFF_DROP_LOG="$DW/drop.log" FREEBUFF_PHONE_SH="$DW/notify/phone.sh" \
    python3 "$DW/notify/drop-bell.py" "$@"
  local ret=$?
  case " $* " in *" --print "*) : ;; *) wait_for_change 1 dw_sends "$before" ;; esac
  return $ret
}
# What a killed TUI leaves on stderr: an escape, a spinner frame, a carriage return, then
# the error itself. The report has to survive the first three to be worth anything.
dd_errlog() {
  printf '\033[90m\xe2\xa0\x8b\033[0m\r\033[2KAPIError: socket hang up after 30s\n' >"$DW/stderr.log"
}

DMID='{"backend":"cli","cwd":"/Users/x/proj","session":"s1","list_id":"L1","done":2,"total":5,"turn_ended":false,"goal":"ship the drop watch"}'
# The same shape in other sessions: a death is reported once per SESSION, so a case that
# must reach its own decision needs its own session rather than a second look at s1.
DMID3=$(printf '%s' "$DMID" | sed 's/"s1"/"s3"/')
DMID4=$(printf '%s' "$DMID" | sed 's/"s1"/"s4"/')
DDONE='{"backend":"cli","cwd":"/Users/x/proj","session":"s1","list_id":"L1","done":5,"total":5,"turn_ended":true,"goal":"ship the drop watch"}'

dd_errlog
out=$(dw_run "$DMID" --local --exit 143 --cwd "$DW/proj" --stderr-log "$DW/stderr.log" --print)
check "a killed session reports a drop" "$?" "10"
check_match "and names the signal" "$out" 'died on its own \(SIGTERM\)'
check_match "the report carries the goal and the count" "$out" 'ship the drop watch ~ 2/5 steps done'
check_match "and the last thing the session printed" "$out" 'stderr: APIError: socket hang up after 30s'
check "no escape sequence or spinner frame survives into it" \
  "$(printf '%s' "$out" | LC_ALL=C grep -Ec $'\033|\xe2\xa0')" "0"

n=$(dw_sends)
p=$(dw_plays)
dw_run "$DMID" --local --exit 143 --cwd "$DW/proj" --stderr-log "$DW/stderr.log" >/dev/null
dw_wait dw_plays "$((p + 1))"
dw_wait dw_sends "$((n + 1))"
check "a drop rings, on its own note" "$(tail -1 "$DW/plays.log")" \
  "afplay -v 0.5 /System/Library/Sounds/Basso.aiff"
check_match "is pushed at high priority" "$(tail -1 "$DW/sends.log")" 'priority high' 
check_match "named for the session's directory" "$(tail -1 "$DW/sends.log")" \
  'freebuff dropped · proj --message died on its own'
check "and lands in the log with its error message" \
  "$(grep -c 'drop local session=s1 .*stderr="APIError: socket hang up after 30s"' "$DW/drop.log")" "1"
check "one push for the death" "$(dw_sends)" "$((n + 1))"

# the same session is never reported twice: the wrapper, a retry and a watcher all ask
out=$(dw_run "$DMID" --local --exit 143 --cwd "$DW/proj" --print)
check "a second look at it is silent" "$?" "0"
check_match "...and says it already reported it" "$out" 'already reported'
check "...without a second push" "$(dw_sends)" "$((n + 1))"

# A plain quit is not a drop, and must stay that way: that is the ending the wrapper's
# usual exit chime is for.
n=$(dw_sends)
b=$(dw_plays)
out=$(dw_run "$DDONE" --local --exit 0 --cwd "$DW/proj" --print)
check "a quit after the turn ended is not a drop" "$?" "0"
check_match "...it is just a quit" "$out" 'no drop: the user quit'
dw_run "$DDONE" --local --exit 0 --cwd "$DW/proj" >/dev/null
check "and it neither chimes nor pushes" "$(dw_plays)-$(dw_sends)" "$b-$n"

# ...but an ending the agent did not reach is one, whatever the exit status said
out=$(dw_run "$DMID3" --local --exit 0 --cwd "$DW/proj" --print)
check "a quit mid-turn is a drop" "$?" "10"
check_match "...and says which kind of ending it was" "$out" 'quit mid-turn — the agent had not finished'
out=$(dw_run "$DMID4" --local --exit 130 --cwd "$DW/proj" --print)
check "an interrupt mid-turn is one too" "$?" "10"
check_match "...named as an interrupt" "$out" 'interrupted mid-turn'

# The store is picked by DIRECTORY and a directory can host more than one session, so the
# list a clean exit is judged against may be somebody else's. A state whose session is
# still running cannot witness an ending: 2026-09-21 18:17 pushed "quit mid-turn" for a
# live session that was mid-turn because it was working (it finished its turn a minute
# later and is still running) when a short-lived session in the same directory quit.
GONEPID=$(sh -c 'echo $$')   # that shell has exited by the time this returns
LIVESESSION="{\"backend\":\"cli\",\"cwd\":\"/Users/x/proj\",\"session\":\"s5\",\"list_id\":\"L1\",\"done\":2,\"total\":5,\"turn_ended\":false,\"instance_pid\":$$}"
DEADSESSION="{\"backend\":\"cli\",\"cwd\":\"/Users/x/proj\",\"session\":\"s6\",\"list_id\":\"L1\",\"done\":2,\"total\":5,\"turn_ended\":false,\"instance_pid\":$GONEPID}"
out=$(dw_run "$LIVESESSION" --local --exit 0 --cwd "$DW/proj" --print)
check "a quit judged against a still-running session is not a drop" "$?" "0"
check_match "...and says whose list it read" "$out" 'belongs to a session still running'
out=$(dw_run "$LIVESESSION" --local --exit 130 --cwd "$DW/proj" --print)
check "nor is an interrupt judged against one" "$?" "0"
out=$(dw_run "$DEADSESSION" --local --exit 0 --cwd "$DW/proj" --print)
check "a quit judged against a session that is gone still is one" "$?" "10"
check_match "...named as a quit mid-turn" "$out" 'quit mid-turn'

# the user's own interrupt is the one signature that can be silenced, because it is the
# only one where they were the cause
out=$( (FREEBUFF_DROP_INTERRUPT=off dw_run "$DMID4" --local --exit 130 --cwd "$DW/proj" --print) )
check "the interrupt switch silences exactly that" "$?" "0"
check_match "...saying so out loud" "$out" 'interruptions are muted'
out=$( (FREEBUFF_DROP_INTERRUPT=off dw_run "$DMID4" --local --exit 137 --cwd "$DW/proj" --print) )
check "and cannot silence a session that was killed" "$?" "10"
check_match "...which still reports" "$out" 'SIGKILL'

echo
echo "== the drop watch: the session the wrapper never got to report =="
# A terminal that dies takes the wrapper with it mid-`command freebuff`, so the exit
# status it would have reported is never written. The watchdog outlives it, and what it
# has is the shell's disappearance and the absence of a report.
dw_watch() { # claim file, then drop-bell args
  local claim="$1"
  shift
  local before
  before=$(dw_sends)
  PATH="$DW/bin:$PATH" HOME="$DW/home" FREEBUFF_DROP_STATE="$claim" \
    FREEBUFF_DROP_LOG="$DW/watch.log" FREEBUFF_PHONE_SH="$DW/notify/phone.sh" \
    FREEBUFF_DROP_POLL=0.2 python3 "$DW/notify/drop-bell.py" "$@"
  local ret=$?
  case " $* " in *" --print "*) : ;; *) wait_for_change 1 dw_sends "$before" ;; esac
  return $ret
}

printf '%s\n' "$DMID" >"$DW/state.json"
sleep 30 &
LIVE_SHELL=$!
TIMERS="$TIMERS $LIVE_SHELL"
n=$(dw_sends)
dw_watch "$DW/watch.json" --watch $LIVE_SHELL --record "$DW/report.json" \
  --cwd "$DW/proj" --grace 0.5 >/dev/null 2>&1 &
WATCH=$!
wait_for_change 2 dw_sends "$n"   # two seconds in which a wrong push would have landed
check "silent while the shell it watches is alive" "$(dw_sends)" "$n"
: >"$DW/report.json"          # the wrapper's own report: this session is accounted for
wait $WATCH
check "and it stands down the moment that report lands" "$?" "0"
check "...without pushing anything" "$(dw_sends)" "$n"

sleep 0.2 &
GONE_SHELL=$!
wait $GONE_SHELL 2>/dev/null
out=$(dw_watch "$DW/watch-print.json" --watch $GONE_SHELL --record "$DW/no-report.json" \
  --cwd "$DW/proj" --grace 0.2 --print)
check "a shell that leaves no report at all is a drop" "$?" "10"
check_match "...reported as a vanishing" "$out" 'vanished mid-turn'
n=$(dw_sends)
dw_watch "$DW/watch.json" --watch $GONE_SHELL --record "$DW/no-report.json" \
  --cwd "$DW/proj" --grace 0.2 >/dev/null 2>&1
dw_wait dw_sends "$((n + 1))"
check "the vanishing is pushed" "$(dw_sends)" "$((n + 1))"
check_match "...naming the death, not the store" "$(tail -1 "$DW/sends.log")" 'the session vanished mid-turn'
check "and kept on disk with it" "$(grep -c 'drop vanished session=s1' "$DW/watch.log")" "1"

echo
echo "== the drop watch on the NAS: a marker that outlived its session =="
# The marker is the witness: `fb` removes it on the way out, so a marker whose pid is
# gone was left by a session that was killed (an ssh or a window going away). No marker
# at all is what a clean exit looks like — and what a NAS without the hook looks like —
# so it is not called a drop.
DDONE2=$(printf '%s' "$DDONE" | sed 's/"s1"/"s2"/')
out=$(dw_run "$DDONE" --nas --fb - --live 0 --print)
check "a NAS session that closed normally is not a drop" "$?" "0"
check_match "...and says so" "$out" 'no drop: the session closed normally'
n=$(dw_sends)
dw_run "$DDONE" --nas --fb 0 --live 0 --cwd /srv/app >/dev/null
rc=$?
check "a marker whose pid is gone is a drop" "$rc" "10"
dw_wait dw_sends "$((n + 1))"
check_match "pushed as a NAS session" "$(tail -1 "$DW/sends.log")" 'NAS freebuff dropped · app'
check_match "...carrying the far store's goal" "$(tail -1 "$DW/sends.log")" 'ship the drop watch'
check "...once" "$(dw_sends)" "$((n + 1))"
out=$(dw_run "$DDONE" --nas --fb 0 --live 0 --print)
check_match "a second look at that death is silent" "$out" 'already reported'
n=$(dw_sends)
dw_run "$DDONE2" --nas --fb 1 --live 0 >/dev/null
dw_wait dw_sends "$((n + 1))"
check "a CLI that went while its session was still up is a drop too" "$(dw_sends)" "$((n + 1))"
check_match "...named for what vanished" "$(tail -1 "$DW/sends.log")" 'freebuff process disappeared'

echo
echo "== the drop note: its own voice, and still the one off switch =="
DH="$SANDBOX/dropsound"
mkdir -p "$DH/.config/freebuff-notify"
cp "$HERE/bell.sh" "$DH/.config/freebuff-notify/bell.sh"
chmod +x "$DH/.config/freebuff-notify/bell.sh"
ds_key() { # key, then bell.sh flags
  local key=$1
  shift
  HOME="$DH" "$DH/.config/freebuff-notify/bell.sh" --print "$@" |
    awk -v k="$key" '{for(i=1;i<=NF;i++){split($i,kv,"=");v[kv[1]]=kv[2]}} END{print v[k]}'
}
check "a drop is heard as Basso by default" "$(ds_key file --drop)" "/System/Library/Sounds/Basso.aiff"
check "and bell.sh was told it is a drop" "$(ds_key drop --drop)" "1"
check "the finish note is untouched by that" "$(ds_key file)" "/System/Library/Sounds/Glass.aiff"
printf '%s\n' Hero >"$DH/.config/freebuff-notify/drop-sound"
check "drop-sound picks another voice for it" "$(ds_key file --drop)" "/System/Library/Sounds/Hero.aiff"
check "and does not change the finish note" "$(ds_key file)" "/System/Library/Sounds/Glass.aiff"
check "FREEBUFF_BELL_SOUND_DROP wins over the file" \
  "$(FREEBUFF_BELL_SOUND_DROP=Submarine ds_key file --drop)" "/System/Library/Sounds/Submarine.aiff"
check "the drop honours the same off switch" "$(FREEBUFF_BELL=off ds_key enabled --drop)" "off"

echo
echo "== wrapper: a session that dies on its own pushes, a plain quit does not =="
plain_sends() { if [ -r "$PLAIN/sends.log" ]; then wc -l <"$PLAIN/sends.log" | tr -d ' '; else echo 0; fi; }
# The bell was left muted by the block above, so every run here says so explicitly — and
# the sound is the sandbox's saved pick (Glass), which is what makes the drop note visible.
b=$(plays)
n=$(plain_sends)
(FREEBUFF_BELL=on run_plain) >/dev/null 2>&1
wait_plays $((b + 1))
check "a clean quit chimes once, with the picked sound" "$(tail -1 "$CHIMES")" \
  "afplay -v 0.5 /System/Library/Sounds/Glass.aiff"
check "...and pushes nothing" "$(plain_sends)" "$n"

b=$(plays)
n=$(plain_sends)
(FREEBUFF_BELL=on FB_EXIT=143 run_plain) >/dev/null 2>&1
wait_plays $((b + 1))
dw_wait plain_sends "$((n + 1))"
check "a killed session chimes the drop note" "$(tail -1 "$CHIMES")" \
  "afplay -v 0.5 /System/Library/Sounds/Basso.aiff"
check "...instead of the finish note, not as well as" "$(plays)" "$((b + 1))"
check "and the wrapper pushed it" "$(plain_sends)" "$((n + 1))"
check_match "with what it died of" "$(tail -1 "$PLAIN/sends.log")" 'died on its own \(SIGTERM\)'
check "and left a record of it in the log" \
  "$(grep -c 'drop local' "$PLAIN/.config/freebuff-notify/drop.log")" "1"

# the record the watchdog reads is written by the wrapper, and old ones are swept: a
# report file per session would otherwise pile up in the notify directory forever
dw_run "$DDONE" --local --exit 0 --print >/dev/null 2>&1
touch -t 202001010000 "$DW/notify/drop-session-999-1.report"
: >"$DW/notify/drop-session-998-1.report"
dw_run "$DDONE" --local --exit 0 --print >/dev/null 2>&1
check "a stale report file is swept away" "$(ls "$DW/notify" | grep -c 'drop-session-999')" "0"
check "and a current one is left alone" "$(ls "$DW/notify" | grep -c 'drop-session-998')" "1"

echo
echo "== the ask watch: the pane is the only place a pending question is visible =="
# The journal cannot answer this one — the CLI writes `ask_user` only when the iteration
# ENDS, i.e. after the answer arrived (measured: one record holding the tool call and its
# `answers`, `duration` 385767ms). The screen can, and it draws its own title. So the
# frames below are real captures off a NAS session mid-question, and tmux is a stub: what
# is under test is the decision and the once-per-question record, not tmux's own plumbing.
AB="$SANDBOX/ask-bell"
mkdir -p "$AB/bin" "$AB/notify" "$AB/home" "$AB/panes"
cp "$HERE/ask-bell.py" "$AB/notify/"
chmod +x "$AB/notify/ask-bell.py"
cat >"$AB/notify/phone.sh" <<PHONE
#!/bin/sh
# A sender that records what it was asked to send: the DECISION is what is under test.
case "\$1" in
  --print) echo "url=x topic=set token= enabled=on" ;;
  *) printf '%s\n' "\$*" >>"$AB/sends.log" ;;
esac
exit 0
PHONE
chmod +x "$AB/notify/phone.sh"
cat >"$AB/panes/%7.txt" <<'FRAME'
 ┌──────────────────────────────────────────────────┐
 └──────────────────────────────────────────────────┘
╭────────────── Some questions for you ──────────────╮
│                                            Close ✕ │
│                                                    │
│ ▼ Put the four taskboard items back? I have their  │
│ text from your message, but not their status or    │
│ ordering.                                          │
│   ○ Restore all four, marked done                  │
│   ○ Restore all four, left as to-do                │
│   ○ Leave the board empty                          │
│   ○ Custom                                         │
╰────────────────────────────────────────────────────╯
FRAME
cp "$AB/panes/%7.txt" "$AB/panes/%9.txt"   # the same modal, under a pane that is not a TUI
printf ' Working...  1m 12s   ■ Esc\n╭────────────────────╮\n│ Enter a coding task\n' >"$AB/panes/%8.txt"
printf '%s\n' '%7|node|/home/me/proj|win' >"$AB/panes.list"
# A stub tmux: the panes list and the frames are files, and both are the exact shape the
# real one prints. The separator is `|`, not a tab: a tab inside a `-F` format does not
# survive every tmux (see panes.py's pane_rows).
cat >"$AB/bin/tmux" <<TMUX
#!/bin/sh
case "\$1" in
  list-panes) cat "$AB/panes.list" ;;
  display-message)
    id=
    for a in "\$@"; do [ "\$b" = "-t" ] && id="\$a"; b="\$a"; done
    while read -r one; do
      case "\$one" in "\$id"*) printf '%s\\n' "\$one" ;; esac
    done <"$AB/panes.list"
    ;;
  capture-pane)
    id=
    for a in "\$@"; do [ "\$b" = "-t" ] && id="\$a"; b="\$a"; done
    cat "$AB/panes/\$id.txt" 2>/dev/null
    ;;
esac
exit 0
TMUX
chmod +x "$AB/bin/tmux"

ab_run() { # ask-bell args...; the push is detached, so wait for it to land (this is the
  # check that failed under load: the phone.sh line was not in the log yet when it was read)
  local before
  before=$(ab_sends)
  PATH="$AB/bin:$PATH" HOME="$AB/home" \
    FREEBUFF_TMUX="$AB/bin/tmux" FREEBUFF_PHONE_SH="$AB/notify/phone.sh" \
    FREEBUFF_ASK_BELL_STATE="$AB/state.json" \
    python3 "$AB/notify/ask-bell.py" "$@"
  case " $* " in *" --print "*) : ;; *) wait_for_change 1 ab_sends "$before" ;; esac
  return 0
}
ab_sends() { if [ -r "$AB/sends.log" ]; then grep -c -e '^--title' "$AB/sends.log"; else echo 0; fi; }

out=$(ab_run --print)
check_match "the modal on screen is read as a pending question" "$out" '^ASK: %7'
check_match "...with the question, not the options" "$out" 'Put the four taskboard items back\?'

ab_run >/dev/null
check "a pending question pushes once" "$(ab_sends)" "1"
check_match "...saying a session is stopped and waiting, and how many options are up" \
  "$(cat "$AB/sends.log")" 'a question is on screen, waiting \(4 options\)'
check_match "...naming the pane it is waiting in" "$(cat "$AB/sends.log")" 'at %7'
check_match "...but not the question itself, which a model wrote" \
  "$(printf %s "$(cat "$AB/sends.log")" | grep -c 'Put the four taskboard items back' || true)" '^0$'
check_match "...high priority, because a waiting agent is a stopped session" \
  "$(cat "$AB/sends.log")" 'priority high'

# ...and the question itself comes back only when the owner asks for it. The claim is set
# aside for the run and put back after, because the checks below count on it (`pushes only
# once` for a question that stays up).
cp "$AB/state.json" "$AB/state.json.claim"
rm -f "$AB/sends.log" "$AB/state.json"
FREEBUFF_PHONE_TEXT=agent ab_run >/dev/null
check "...and it is asked for explicitly" "$(ab_sends)" "1"
check_match "FREEBUFF_PHONE_TEXT=agent puts the question back" \
  "$(cat "$AB/sends.log")" 'Put the four taskboard items back\?'
check_match "...with the answer options under it" "$(cat "$AB/sends.log")" '3\) Leave the board empty'
mv "$AB/state.json.claim" "$AB/state.json"

ab_run >/dev/null
ab_run >/dev/null
check "a question that stays up for twenty minutes pushes only once" "$(ab_sends)" "1"
check_match "...and says so" "$(ab_run --print)" '^silent: already pushed'

# the same screen wording, and a different question: that is not the same question
sed 's/Put the four taskboard items back?/Put the four taskboard cards back?/' \
  "$AB/panes/%7.txt" >"$AB/panes/%7.new"
mv "$AB/panes/%7.new" "$AB/panes/%7.txt"
ab_run >/dev/null
check "the next question is a new one and pushes again" "$(ab_sends)" "2"

# a pane that is not a TUI: the same words, drawn by something that is not freebuff
printf '%s\n' '%9|bash|/tmp|win' >>"$AB/panes.list"
out=$(ab_run --print)
check_match "the same box under a shell pane is not a question" "$out" '^silent:'

# ...and a TUI pane with no modal on it is not one either
printf '%s\n' '%8|node|/home/me/proj|win' '%9|bash|/tmp|win' >"$AB/panes.list"
check_match "an ordinary freebuff screen is not a question" "$(ab_run --print)" '^silent:'

# no sender and no topic: the caller's back-off signal, not a crash. A pending
# question is needed to ask it at all — with nothing on screen there is nothing to
# report, and a watcher must not be told to back off over a quiet screen.
printf '%s\n' '%7|node|/home/me/proj|win' >"$AB/panes.list"
out=$(PATH="$AB/bin:$PATH" HOME="$AB/home" FREEBUFF_TMUX="$AB/bin/tmux" \
  FREEBUFF_PHONE_SH="$AB/notify/nope.sh" FREEBUFF_ASK_BELL_STATE="$AB/fresh.json" \
  python3 "$AB/notify/ask-bell.py" 2>&1); rc=$?
check "asking with nothing configured to send to exits 78" "$rc" "78"
check "...and the question is not claimed, so it can still push later" \
  "$([ -e "$AB/fresh.json" ] && echo claimed || echo pending)" "pending"

# a modal whose header has scrolled off was NOT read as a question: the frame's first
# rows, without the title line, are what a wrapped output looks like
tail -n +5 "$AB/panes/%7.txt" >"$AB/panes/%8.txt"
printf '%s\n' '%8|node|/home/me/proj|win' >"$AB/panes.list"
check_match "a frame with no header on it is silent" "$(ab_run --print)" '^silent:'

echo
echo "== the stall watch: quiet alone is not a stop, the pane is =="
# The signal has to survive the case `todo-bell.py` already rejected: a five-minute step
# leaves the journal silent while the agent is emphatically working. The CLI knows, and it
# says so on the pane — so the state says "quiet", and the pane says "busy" or not.
SB="$SANDBOX/pause-bell"
mkdir -p "$SB/bin" "$SB/notify" "$SB/home" "$SB/panes"
cp "$HERE/pause-bell.py" "$SB/notify/"
chmod +x "$SB/notify/pause-bell.py"
cat >"$SB/notify/phone.sh" <<PHONE
#!/bin/sh
case "\$1" in
  --print) echo "url=x topic=set token= enabled=on" ;;
  *) printf '%s\n' "\$*" >>"$SB/sends.log" ;;
esac
exit 0
PHONE
chmod +x "$SB/notify/phone.sh"
# the state comes from fbtodo, the status line from the pane:
printf '#!/bin/sh\ncat "%s" 2>/dev/null\n' "$SB/state.json" >"$SB/bin/fbtodo"
chmod +x "$SB/bin/fbtodo"
# the CLI's own spelling, measured off a live pane: lowercase, with the Esc hint
printf ' working...                 3m 10s  ■ Esc\n╭──────╮\n│ Enter a coding task\n' >"$SB/panes/%7.working"
printf ' ❯ Enter a coding task\n' >"$SB/panes/%7.idle"
cp "$AB/panes/%7.txt" "$SB/panes/%7.modal"
cat >"$SB/bin/tmux" <<TMUX
#!/bin/sh
case "\$1" in
  capture-pane)
    id=
    for a in "\$@"; do [ "\$b" = "-t" ] && id="\$a"; b="\$a"; done
    cat "$SB/panes/\$id.txt" 2>/dev/null
    ;;
esac
exit 0
TMUX
chmod +x "$SB/bin/tmux"

sb_run() { # pause-bell args...; the push is detached, so wait for it to land (a count
  # read straight after the decision beat the sender to it and made `a stop pushes once`
  # fail about one run in four — measured 2026-09-29 while the self-check ran beside it)
  local quiet="${SB_QUIET:-150}"
  local before
  before=$(sb_sends)
  PATH="$SB/bin:$PATH" HOME="$SB/home" \
    FREEBUFF_FBTODO="$SB/bin/fbtodo" FREEBUFF_TMUX="$SB/bin/tmux" \
    FREEBUFF_PHONE_SH="$SB/notify/phone.sh" FREEBUFF_PAUSE_QUIET="$quiet" \
    FREEBUFF_PAUSE_BELL_STATE="$SB/state-bell.json" \
    python3 "$SB/notify/pause-bell.py" "$@"
  case " $* " in *" --print "*) : ;; *) wait_for_change 1 sb_sends "$before" ;; esac
}
sb_sends() { if [ -r "$SB/sends.log" ]; then grep -c -e '^--title' "$SB/sends.log"; else echo 0; fi; }
sb_state() { # turn_ended, quiet_seconds, done, total
  local ms; ms=$(( ($(date +%s) - $2) * 1000 ))
  printf '{"backend":"cli","session":"s1","cwd":"/tmp/stall-demo","list_id":"L1","goal":"wire the stall watch","done":%s,"total":%s,"turn_ended":%s,"store_mtime_ms":%s}\n' \
    "$3" "$4" "$1" "$ms" >"$SB/state.json"
}

cp "$SB/panes/%7.idle" "$SB/panes/%7.txt"
sb_state false 600 1 3
out=$(sb_run --watch-pid $$ --pane %7 --print)
check_match "a quiet journal and no working line is a stop" "$out" '^STALL:'
check_match "...and the push says how much was left" "$(sb_run --watch-pid $$ --pane %7 --print)" '1/3 steps done'

sb_run --watch-pid $$ --pane %7 >/dev/null
check "a stop pushes once" "$(sb_sends)" "1"
check_match "...by iMessage and ntfy (phone.sh picks), high priority" \
  "$(cat "$SB/sends.log")" 'priority high'
check_match "...naming the project it stopped in" "$(cat "$SB/sends.log")" 'freebuff stalled · stall-demo'
check_match "...and not the goal the agent wrote, while it says how long and how much" \
  "$(printf %s "$(cat "$SB/sends.log")" | grep -c 'wire the stall watch' || true)" '^0$'
# ...the goal is opt-in, like the other bells: the claim is set aside and put back, so the
# `stays quiet` count below still has the push it expects
cp "$SB/state-bell.json" "$SB/state-bell.json.claim"
rm -f "$SB/sends.log" "$SB/state-bell.json"
FREEBUFF_PHONE_TEXT=agent sb_run --watch-pid $$ --pane %7 >/dev/null
check_match "FREEBUFF_PHONE_TEXT=agent puts the goal back on it" \
  "$(cat "$SB/sends.log")" 'Goal: wire the stall watch'
mv "$SB/state-bell.json.claim" "$SB/state-bell.json"
sb_run --watch-pid $$ --pane %7 >/dev/null
check "the same stop stays quiet" "$(sb_sends)" "1"
check_match "...and says why" "$(sb_run --watch-pid $$ --pane %7 --print)" '^silent: already pushed'

# a step that is still running: the case a quiet-window heuristic gets wrong
cp "$SB/panes/%7.working" "$SB/panes/%7.txt"
sb_state false 600 1 3
check_match "a long step is not a stop, however quiet the journal is" \
  "$(sb_run --watch-pid $$ --pane %7 --print)" 'the pane still says a step is running'

cp "$SB/panes/%7.idle" "$SB/panes/%7.txt"
sb_state true 600 1 3
check_match "a turn that ended is waiting for you, not stuck" \
  "$(sb_run --watch-pid $$ --pane %7 --print)" 'the turn ended'
sb_state false 5 1 3
check_match "a journal that just moved is not a stop" \
  "$(sb_run --watch-pid $$ --pane %7 --print)" 'the journal moved'
sb_state false 600 0 0
check_match "a session with no list has nothing unfinished" \
  "$(sb_run --watch-pid $$ --pane %7 --print)" 'no list yet'

# the question modal: the ask watch owns that one, and two pushes for one question is
# exactly the repeat that gets a topic muted
cp "$SB/panes/%7.modal" "$SB/panes/%7.txt"
sb_state false 600 1 3
check_match "a question on screen is the ask watch's, not a stall" \
  "$(sb_run --watch-pid $$ --pane %7 --print)" 'the ask watch has that one'

cp "$SB/panes/%7.idle" "$SB/panes/%7.txt"
check_match "without a pane there is no honest answer" \
  "$(sb_run --watch-pid $$ --print)" 'no pane to read'
check_match "a gone session is not a stall" \
  "$(sb_run --watch-pid 999999 --pane %7 --print)" 'the session is gone'

# no sender and no topic: the caller's back-off signal, not a crash
printf '%s\n' '{"backend":"cli","session":"s9","cwd":"/tmp/stall-demo","list_id":"L9","done":1,"total":3,"turn_ended":false,"store_mtime_ms":1}' >"$SB/state.json"
out=$(PATH="$SB/bin:$PATH" HOME="$SB/home" FREEBUFF_FBTODO="$SB/bin/fbtodo" \
  FREEBUFF_TMUX="$SB/bin/tmux" FREEBUFF_PHONE_SH="$SB/notify/nope.sh" \
  FREEBUFF_PAUSE_QUIET=150 FREEBUFF_PAUSE_BELL_STATE="$SB/fresh.json" \
  python3 "$SB/notify/pause-bell.py" --watch-pid $$ --pane %7 2>&1); rc=$?
check "a stop with nothing configured to send to exits 78" "$rc" "78"
check "...and is not claimed, so it can still push later" \
  "$([ -e "$SB/fresh.json" ] && echo claimed || echo pending)" "pending"

# a state the watcher could not answer for at all
sb_state false 600 1 3
: >"$SB/state.json"
check_match "an unanswerable state is silent, not an error" \
  "$(sb_run --watch-pid $$ --pane %7 --print)" '^silent: fbtodo could not answer'

echo
echo "== the pane watch: the pane keeper failing is invisible, so it is reported =="
# Every other failure in this suite is a session WAITING. This one is a broken repair: a
# session with no todo pane the keeper has not put back, or no keeper at all while one is
# needed. Both are silent by construction — the keeper has no stderr anybody reads, and
# its log records a pane that came BACK, never one that did not — so this block is the only
# way they are ever seen. The claim is the OCCURRENCE, which a cooldown gets wrong in both
# directions: a condition that has been true for an hour has not stopped being true, and
# one that cleared and came back is new news.
PN="$SANDBOX/pane-watch"
mkdir -p "$PN/bin" "$PN/notify" "$PN/home"
cp "$HERE/pane-bell.py" "$PN/notify/"
chmod +x "$PN/notify/pane-bell.py"
# The body carries newlines, so it is flattened to `~`: one line per push, and the count is
# a `wc -l`. Same shape as the push watch's stub, for the same reason.
printf '#!/bin/sh\ncase "$*" in *--print*) printf "url=stub topic=set token= enabled=on\\n"; exit 0;; esac\nprintf "%%s\\n" "$*" | tr "\\n" "~" >>"%s"\nprintf "\\n" >>"%s"\n' \
  "$PN/sends.log" "$PN/sends.log" >"$PN/notify/phone.sh"
chmod +x "$PN/notify/phone.sh"
# fbtodo is what knows which sessions want a pane and have none — `why --json` says so per
# session. Stubbed, because the suite must not depend on this machine's tmux or sessions.
printf '#!/bin/sh\ncat "%s" 2>/dev/null\n' "$PN/why.json" >"$PN/bin/fbtodo"
chmod +x "$PN/bin/fbtodo"
pn_why() { # pane-or-none, window, pid  -> what `fbtodo why --json` would say
  local pane="$1" win="$2" pid="$3"
  case "$pane" in
    none) pane=null ;;
    *) pane="\"$pane\"" ;;
  esac
  printf '{"panes":[{"pane":%s,"role":"local","window":"%s","window_id":"@9","anchor":"%%4","anchor_note":"freebuff pid %s","placed":true}],"pins":{},"last":{}}\n' \
    "$pane" "$win" "$pid" >"$PN/why.json"
}
pn_keeper() { # alive|dead — the record the keeper writes for exactly this purpose
  if [ "$1" = alive ]; then
    printf '{"pid":%s,"tmux":"default","version":"x"}\n' "$$" >"$PN/keeper.json"
  else
    : >"$PN/keeper.json"
  fi
}
pn_sends() { if [ -r "$PN/sends.log" ]; then wc -l <"$PN/sends.log" | tr -d ' '; else echo 0; fi; }
pn_sends_at_least() { [ "$(pn_sends)" -ge "$1" ]; }
pn_sends_still() { [ "$(pn_sends)" -ge "$1" ]; }
pn_run() { # grace, args...  — the push is detached, so wait for it to land
  local grace="$1" before
  shift
  before=$(pn_sends)
  PATH="$PN/bin:$PATH" HOME="$PN/home" \
    FREEBUFF_FBTODO="$PN/bin/fbtodo" FREEBUFF_PHONE_SH="$PN/notify/phone.sh" \
    FREEBUFF_PANE_GRACE="$grace" FREEBUFF_PANE_BELL_STATE="$PN/state-bell.json" \
    python3 "$PN/notify/pane-bell.py" --keeper "$PN/keeper.json" "$@"
  case " $* " in *--print*) : ;; *) wait_for_change 1 pn_sends "$before" ;; esac
  return 0
}

# a healthy server: every session has a pane, and something is watching
pn_why '%5' main 501
pn_keeper alive
check_match "every session has a pane, and a keeper is watching" \
  "$(pn_run 30 --print)" '^silent: every session that wants a pane has one, and a keeper is watching'
check "nothing to report, nothing sent" "$(pn_sends)" "0"

# With no --keeper, the claim is looked for where fbtodo actually keeps it: FBTODO_HOME,
# then the XDG state directory, then the legacy `~/.freebuff`. This copy of the rule is the
# bell's own, so the test drives the bell rather than trusting that the two agree.
mkdir -p "$PN/homestate"
cp "$PN/keeper.json" "$PN/homestate/fbtodo-pane-keeper.pid"
check_match "with no --keeper it finds the claim where fbtodo keeps it" \
  "$(FBTODO_HOME="$PN/homestate" HOME="$PN/home" FREEBUFF_FBTODO="$PN/bin/fbtodo" \
    FREEBUFF_PHONE_SH="$PN/notify/phone.sh" FREEBUFF_PANE_BELL_STATE="$PN/state-bell.json" \
    python3 "$PN/notify/pane-bell.py" --print)" \
  '^silent: every session that wants a pane has one, and a keeper is watching'

# The startup case, and the reason the grace exists: a session whose CLI has only just
# started has no pane, and the keeper's first pass is what gives it one. A watch that
# reported here would fire on every session, every time.
pn_why none main 502
pn_keeper alive
check_match "a pane missing for a moment is the keeper's business, not an alert" \
  "$(pn_run 600 --print)" '^silent: a pane is missing for 0s .needs 600s.'
check "the startup case stays quiet" "$(pn_sends)" "0"

# ...and the same session once the grace has passed
rm -f "$PN/state-bell.json"
check_match "a pane the keeper never put back is reported" \
  "$(pn_run 0 --print)" '^PANE: panes — main: no list pane for freebuff pid 502'
pn_run 0 --quiet >/dev/null
check "it pushes once" "$(pn_sends)" "1"
check_match "...through phone.sh, high priority" "$(cat "$PN/sends.log")" 'priority high'
# `.*` rather than `.`: the title carries a `·`, which under a C locale grep sees as two
# bytes and a single-dot pattern cannot match. The assertion is about the window name.
check_match "...naming the window it is missing from" "$(cat "$PN/sends.log")" 'fbtodo pane missing .* main'
check_match "...and saying what to run" "$(cat "$PN/sends.log")" 'pane-watch --once'
check_match "...and the log beside the claim it read, not a legacy path" \
  "$(cat "$PN/sends.log")" 'its log: .*pane-watch/fbtodo-pane.log'
pn_run 0 --quiet >/dev/null
check "the same failure does not repeat" "$(pn_sends)" "1"
check_match "...and says why" "$(pn_run 0 --print)" '^silent: already announced'

# the recurrence a cooldown gets wrong: the pane came back, and went again. A 6h cooldown
# would still be inside its window and say nothing at all.
pn_why '%5' main 502
pn_run 0 --quiet >/dev/null
pn_why none main 502
pn_run 0 --quiet >/dev/null
check "a pane that goes missing again is new news" "$(pn_sends)" "2"

# the keeper itself. With nothing to repair, its absence is not a failure — and a watch
# that announced that anyway is a watch whose line gets ignored.
pn_why '%5' main 502
pn_keeper dead
rm -f "$PN/state-bell.json"
check_match "no keeper with nothing to repair is not a failure" \
  "$(pn_run 30 --print)" 'no keeper, and none needed'
check "a dead keeper alone sends nothing" "$(pn_sends)" "2"

# ...but a session waiting for a pane that has no keeper is both halves at once
pn_why none main 503
rm -f "$PN/state-bell.json"
out=$(pn_run 0 --print)
check_match "a session with no pane and no keeper says both" "$out" '^PANE: both —'
check_match "...naming the dead keeper" "$out" 'nothing will put the pane back'
pn_run 0 --quiet >/dev/null
check "and pushes once" "$(pn_sends)" "3"
check_match "...as the watcher being down" "$(tail -1 "$PN/sends.log")" 'fbtodo pane watcher down'

# no sender: the caller's back-off signal, and nothing claimed so it can still push later
pn_why none main 504
pn_keeper alive
out=$(PATH="$PN/bin:$PATH" HOME="$PN/home" FREEBUFF_FBTODO="$PN/bin/fbtodo" \
  FREEBUFF_PHONE_SH="$PN/notify/nope.sh" FREEBUFF_PANE_GRACE=0 \
  FREEBUFF_PANE_BELL_STATE="$PN/fresh.json" \
  python3 "$PN/notify/pane-bell.py" --keeper "$PN/keeper.json" 2>&1); rc=$?
check "a failure with nothing configured to send to exits 78" "$rc" "78"
check "...and is not claimed" "$([ -e "$PN/fresh.json" ] && echo claimed || echo pending)" "pending"

# a layout fbtodo could not answer for at all is silence, not a crash
: >"$PN/why.json"
check_match "an unanswerable layout is silent, not an error" \
  "$(pn_run 30 --print)" '^silent: fbtodo could not answer'
echo
[ "$fails" = 0 ] && echo "ALL PASS" || echo "$fails CHECK(S) FAILED"
exit "$fails"
