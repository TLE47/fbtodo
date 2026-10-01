#!/usr/bin/env bash
# Run a suite under a wall-clock cap, teeing its output to a log the reporter can read.
#
# Usage: ci-timeout.sh <cap-seconds> <log-file> <command> [args...]
#
# Why not `timeout(1)`: it is not on a macOS runner's PATH, and the suites are the one thing
# here that can hang (a blocked child keeps a piped step open even after the script exits).
# A hung suite must not spend the job's whole 30-minute budget: the cap kills it, `ci-report`
# then annotates the last lines the log has, which is where the hang was.
#
# Exit: the command's own status, 124 when the cap killed it.
set -u

cap=${1:?cap in seconds}
log=${2:?log file}
shift 2

: >"$log"
timed_out="$log.timedout"
rm -f "$timed_out"

"$@" > >(tee -a "$log") 2>&1 &
pid=$!

(
  sleep "$cap"
  : >"$timed_out"
  kill -TERM "$pid" 2>/dev/null
  sleep 5
  kill -KILL "$pid" 2>/dev/null
) &
watch=$!

wait "$pid"
rc=$?
kill "$watch" 2>/dev/null
wait "$watch" 2>/dev/null

if [ -e "$timed_out" ]; then
  rm -f "$timed_out"
  exit 124
fi
rm -f "$timed_out" 2>/dev/null || true
exit "$rc"
