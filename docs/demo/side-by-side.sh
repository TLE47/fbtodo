#!/bin/sh
# The pairing: one tmux window, the session on the left and the pane on the right.
#
#   sh docs/demo/side-by-side.sh          # ~20 s, then it cleans up after itself
#
# `fbtodo pane` normally works out for itself which pane your agent runs in and places itself
# beside it. There is no agent process to find here — the session is docs/demo/drive.sh narrating
# the fixture — so the arrangement is made by hand for the recording, and it is the arrangement
# that is the point of the clip: the same eight steps, ticking on the left as they are written and
# on the right as they are read.
#
# docs/demo/side-by-side.tape records this; docs/demo/README.md has the why.

set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
FIXTURE=$HERE/fixture
SOCK=demo-pair
UP=${DEMO_UP:-22}
PANE_COLUMNS=${DEMO_PANE_COLUMNS:-85}

# A socket of our own, so nothing here touches a tmux server you are using. The pane and the
# driver share this window's environment: the scratch home keeps the synthetic steps out of the
# pace history, the two log paths keep your own notifier log off the screen, and the five /none
# watches keep a recorded demo from ringing anybody's phone.
#
# `-f /dev/null` starts the server with tmux's own defaults and none of the machine's config: a
# recording should not depend on whose dotfiles ran. It is not just tidiness. A config that turns
# `pane-border-status` on (oh-my-tmux's does) paints a border above every pane, and its usual
# format is `#{pane_current_path}` — so the clip would open on a row reading
# `/Users/someone/Projects/fbtodo` above each pane, which is exactly the "terminal command" a demo
# should not show. Both panes are also addressed by the id tmux prints for them rather than by
# `session:window.pane`: a window index is whatever the config says it is (oh-my-tmux's
# `base-index 1` makes the first window 1, not 0).
tmux -L "$SOCK" kill-server 2>/dev/null || true
left=$(tmux -L "$SOCK" -f /dev/null new-session -d -s pair -c "$ROOT" -P -F '#{pane_id}' \
    -e FBTODO_HOME=/tmp/fbtodo-demo \
    -e FBTODO_PATCH_LOG="$FIXTURE/patch.log" \
    -e FBTODO_ALERT_LOG="$FIXTURE/phone.log" \
    -e FBTODO_NOTIFY=/none -e FBTODO_DROP=/none -e FBTODO_ASK=/none \
    -e FBTODO_PAUSE=/none -e FBTODO_PANE_BELL=/none \
    -e DEMO_NARRATE=1 -e DEMO_STEP_SECONDS="${DEMO_STEP_SECONDS:-3.5}" \
    "printf '\\033[2J\\033[H'; sh $HERE/drive.sh; sleep 30")

# No status bar: the boxes are the only chrome the clip needs, and the bar would cost a row of
# the pane's own height.
tmux -L "$SOCK" set -g status off
tmux -L "$SOCK" split-window -h -t "$left" -l "$PANE_COLUMNS" -c "$ROOT" \
    "fbtodo pane --no-daemon --chat $FIXTURE -i 0.5 --tick 0.5 --stale-after 0"
tmux -L "$SOCK" select-pane -t "$left"

# The window is taken down after UP seconds whether or not anything detaches, so a recording that
# ends mid-attach cannot leave a tmux server (and a pane process) behind. The reaper is backgrounded
# because it needs no terminal — which is exactly what the attach cannot do: in a non-interactive
# shell `cmd &` hands the child /dev/null for stdin, and a tmux client whose stdin is not a tty
# refuses to start ("open terminal failed: not a terminal"). So the attach is the foreground job.
( sleep "$UP"; tmux -L "$SOCK" kill-server 2>/dev/null || true ) &

# Wipe the shell (and the line that started this script) off the screen before attaching, so a slow
# attach cannot leave a command visible in the first frames of the recording. This is `clear` and
# not `printf '\033[2J\033[H'` on purpose: vhs's terminal wants the whole thing gone, and it is
# `clear`'s extra `\033[3J` (erase scrollback) that takes the old prompt with it. With the plain
# two-sequence version the prompt survives above the tmux window in the recording.
clear
tmux -L "$SOCK" attach -t pair

tmux -L "$SOCK" kill-server 2>/dev/null || true
