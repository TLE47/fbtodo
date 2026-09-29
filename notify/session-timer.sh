#!/bin/sh
# Keeps the terminal tab title on the freebuff session: a live timer plus what
# the session is working on. The title is the one place this stays visible while
# freebuff's full-screen UI owns the terminal, so it doubles as the "is it still
# running, and on what?" indicator.
#
# usage:
#   session-timer.sh run    <start-epoch> [tty-file]   live "⏱ freebuff 1m04s · task"
#   session-timer.sh finish <start-epoch> [tty-file]   "✓ Done freebuff 1m04s · task"
#
# run loops until the wrapper kills it; finish writes the stamp once, so it is in
# place before the shell prompt (and the chime) come back. The prompt is re-read
# every few seconds, so a new prompt mid-session shows up too.
set -u

mode=${1:-run}
start=${2:-$(date +%s)}
tty=${3:-/dev/tty}
root=${FREEBUFF_TIMER_ROOT_PID:-${PPID:-0}} # the shell that launched freebuff (not set under every sh)
# ...and optionally the freebuff process itself: a timer restarted mid-session belongs to
# that session, so it should go when the process does rather than outlive it.
watch=${FREEBUFF_TIMER_WATCH_PID:-0}
label=freebuff
here=$(dirname "$0")
task_chars=44
task=

format_elapsed() {
  secs=$1
  if [ "$secs" -ge 3600 ]; then
    printf '%dh%02dm%02ds' $((secs / 3600)) $(((secs % 3600) / 60)) $((secs % 60))
  elif [ "$secs" -ge 60 ]; then
    printf '%dm%02ds' $((secs / 60)) $((secs % 60))
  else
    printf '%ds' "$secs"
  fi
}

# session-task.py decides which session is ours by walking up from the pid of
# the freebuff process in its log until it reaches this shell ($PPID).
refresh_task() {
  task=$("$here/session-task.py" "$root" "$task_chars" 2>/dev/null)
}

# The chime for "the agent has finished the task" is decided from the todo list — every
# todo done AND the journal's own end-of-turn record — never from a quiet terminal: a
# five-minute command leaves the journal silent while the agent is still working.
# FREEBUFF_TODO_BELL=off disables it; todo-bell.py --print says what it decided.
ring_when_done() {
  [ "$root" = 0 ] && return 0
  [ "${FREEBUFF_TODO_BELL:-on}" = off ] && return 0
  "$here/todo-bell.py" "$root" --tty "$tty" 2>/dev/null
}

set_title() { # mark
  suffix=
  [ -n "$task" ] && suffix=" · $task"
  printf '\033]0;%s %s %s%s\007' "$1" "$label" \
    "$(format_elapsed "$(($(date +%s) - start))")" "$suffix" >>"$tty" 2>/dev/null
}

refresh_task

if [ "$mode" = finish ]; then
  set_title '✓ Done'
  exit 0
fi

# The tick is a knob only so a test can drive the same loop in a fraction of a second:
# the task refresh and the ring-when-done decision are counted in TICKS (3 and 5), so a
# faster tick shortens every wait in the suite without changing what is exercised. The
# shipped default is 1 s, which is what the cadences were chosen against.
tick=${FREEBUFF_TIMER_SECONDS:-1}
ticks=0
while :; do
  set_title '⏱'
  sleep "$tick"
  ticks=$((ticks + 1))
  [ $((ticks % 3)) -eq 0 ] && refresh_task
  [ $((ticks % 5)) -eq 0 ] && ring_when_done
  [ "$root" = 0 ] || kill -0 "$root" 2>/dev/null || exit 0 # launching shell is gone
  [ "$watch" = 0 ] || kill -0 "$watch" 2>/dev/null || exit 0 # its session is over
done
