#!/bin/sh
# Demo harness — one script, no vhs required, that drives the fixture the way the tape does.
#
# It replays a scripted session into docs/demo/fixture/log.jsonl, so a paned fbtodo has a
# live list to draw. Everything else in the demo is the real tool: the pane you see is
# `fbtodo pane` rendering the real file.
#
#   sh docs/demo/drive.sh            # replay the session (~20 s)
#
# Read docs/demo/README.md first if you want the why rather than the what.

set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
FIXTURE="$HERE/fixture"
LOG="$FIXTURE/log.jsonl"
HEAD="$FIXTURE/head.jsonl"
SLEEP=${DEMO_STEP_SECONDS:-4}
# The pairing clip (docs/demo/side-by-side.sh) runs this on the left-hand pane, where the point is
# that the *same* eight steps appear on the right: DEMO_NARRATE=1 prints each one as it is ticked.
# Unset, as in the pane-only demo, this driver writes the journal and says nothing.
NARRATE=${DEMO_NARRATE:-0}

# The PATCH and ALERT rows read a log apiece. Give them fixture logs with a timestamp of
# now, so a recorded demo shows a live-looking pane — and so nothing personal from a real
# `phone.log` or patch log can end up on screen. Export the same two paths when you run the
# pane by hand (docs/demo/README.md).
now() { date -u +'%Y-%m-%d %H:%M:%S'; }
printf '%s patched — the demo fixture is in place (0000000:0000000000:000000000)\n' "$(now)" >"$FIXTURE/patch.log"
printf '%s phone: sent ntfy freebuff done · demo\n' "$(now)" >"$FIXTURE/phone.log"

# The tasks, one per line. `emit` turns the first N into a done list, so the whole demo is
# the same eight steps being ticked off — which is exactly what the pane is watching for.
tasks() {
    cat <<'EOF'
read the pane renderer
write a fixture journal
replay a scripted session
draw the gradient bar
clip the title to the frame
run the self-check
record the demo
update the docs
EOF
}

# emit <n-done> [ended] — append one write_todos record, with the first n-done steps ticked.
emit() {
    todos=$(tasks | awk -v n="$1" '
        BEGIN { printf "[" }
        { printf "%s{\"task\":\"%s\",\"completed\":%s}", (NR > 1 ? "," : ""), $0, (NR <= n ? "true" : "false") }
        END { printf "]" }')
    printf '{"level":"DEBUG","timestamp":"%s","pid":1,"data":{"iteration":%s,"shouldEndTurn":%s,"toolCalls":[{"toolName":"write_todos","input":{"todos":%s}}]}}\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(( $1 + 10 ))" "${2:-false}" "$todos" >>"$LOG"
}

# narrate <from> <to> — the steps this emit has just ticked, for the pane on the left.
ticked() {
    [ "$NARRATE" = 1 ] || return 0
    tasks | awk -v a="$1" -v b="$2" 'NR > a && NR <= b { printf "  \342\234\224 %s\n", $0 }'
}

# Start from the committed beginning, so the demo is the same every time it is run.
cat "$HEAD" >"$LOG"

if [ "$NARRATE" = 1 ]; then
    printf 'freebuff session (scripted) \302\267 %s steps\n' "$(tasks | awk 'END { print NR }')"
fi

# The committed start already has the first step ticked, so the narration begins from one.
ticked_to=1
for done in 4 5 6; do
    sleep "$SLEEP"
    emit "$done"
    ticked "$ticked_to" "$done"
    ticked_to=$done
done

# The last one ends the turn as well as the list — which is the pair the bell waits for.
sleep "$SLEEP"
emit 8 true
ticked "$ticked_to" 8
