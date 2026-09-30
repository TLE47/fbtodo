#!/bin/sh
# Record everything in this directory from the two tapes beside it:
#
#     demo.mp4                 the pane-only demo, trimmed to the pane's box
#     demo.gif                 the same frames as a GIF, for the README to render inline
#     demo-start/mid/done.png  three stills from it, at 1 s, 13 s and 20 s
#     side-by-side.mp4/.gif    the pairing: the scripted session and the pane, two panes
#
# Needs vhs (https://github.com/charmbracelet/vhs), which pulls ttyd and ffmpeg, and tmux — the
# pairing clip is two panes of one tmux window:
#
#     brew install vhs
#
# Then, from anywhere:
#
#     docs/demo/record.sh
#
# A tape writes a raw MP4; the frame is then trimmed to the pane's own box (FRAME_*, below) and the
# trimmed file is the master. The GIFs and the stills are derived from the masters, never the other
# way round, so the GIF's 256 colours cannot cost the video anything and the video's colour depth
# cannot make a GIF band. The fixture is rebuilt each run, so recording twice gives the same demo —
# the clock in the frame is the only thing that differs.

set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)

if ! command -v vhs >/dev/null 2>&1; then
    printf '%s\n' "vhs is not installed — brew install vhs" >&2
    exit 69    # EX_UNAVAILABLE
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
    printf '%s\n' "ffmpeg is not installed — brew install ffmpeg" >&2
    exit 69    # EX_UNAVAILABLE
fi

if ! command -v tmux >/dev/null 2>&1; then
    printf '%s\n' "tmux is not installed — the pairing clip needs it" >&2
    exit 69    # EX_UNAVAILABLE
fi

# The pane draws its box two rows shorter than the terminal — the last rows belong to the shell it
# sits over — and it collapses a long list rather than overflow a short terminal, so an untrimmed
# frame ends in a band of empty terminal under the box. Each recording is therefore trimmed to the
# box it actually drew, which frame.py measures; the padding is the tapes' own `Set Padding`. The
# trimmed master is then brought to exactly WIDTH px across (the tapes record a little wider, so
# this is a small downscale and never a blur), which is what "1080p-class" means for a terminal
# clip: full HD width, and the height the box actually needs rather than 1080 rows of background.
PADDING=16
WIDTH=1920

cd "$ROOT"

# ---- the pane-only demo: the real renderer, reading the fixture
vhs "$HERE/demo.tape"
FRAME_DEMO=$(python3 "$HERE/frame.py" "$HERE/.demo-raw.mp4" "$PADDING")
ffmpeg -v error -y -i "$HERE/.demo-raw.mp4" -vf "crop=$FRAME_DEMO,scale=$WIDTH:-2" \
    -pix_fmt yuv420p -crf 18 -movflags +faststart "$HERE/demo.mp4"
rm -f "$HERE/.demo-raw.mp4"
printf 'wrote %s (framed %s)\n' "docs/demo/demo.mp4" "$FRAME_DEMO"

# The GIF is a palette reduction of the master: a two-pass palette built from the frames themselves
# (`stats_mode=diff`, because only the list is moving), and bayer dithering kept at its finest
# setting so the dark UI stays flat instead of noisy. It is decimated to 10 fps first — the pane
# repaints twice a second, so the video's 20 fps buys the GIF nothing and costs it megabytes at
# 1920 px wide.
ffmpeg -v error -y -i "$HERE/demo.mp4" \
    -vf "fps=10,split[a][b];[a]palettegen=max_colors=256:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5" \
    -loop 0 "$HERE/demo.gif"
printf 'wrote %s\n' "docs/demo/demo.gif"

# Three stills, at the seconds the docs name: one step in, six of the eight, and the last frame.
# Straight from the master, so a still is full colour rather than a frame of the GIF's palette.
for still in "1 demo-start" "13 demo-mid" "20 demo-done"; do
    # shellcheck disable=SC2086
    set -- $still
    ffmpeg -v error -y -ss "$1" -i "$HERE/demo.mp4" -frames:v 1 "$HERE/$2.png"
    printf 'wrote %s\n' "docs/demo/$2.png"
done

# ---- the pairing: the scripted session and the pane, two panes of one tmux window
vhs "$HERE/side-by-side.tape"
# The clip's own window is taken down by docs/demo/side-by-side.sh; this is the net under a
# recording that ended before that script's own timer did.
tmux -L demo-pair kill-server 2>/dev/null || true
FRAME_PAIR=$(python3 "$HERE/frame.py" "$HERE/.side-by-side-raw.mp4" "$PADDING")
ffmpeg -v error -y -i "$HERE/.side-by-side-raw.mp4" -vf "crop=$FRAME_PAIR,scale=$WIDTH:-2" \
    -pix_fmt yuv420p -crf 18 -movflags +faststart "$HERE/side-by-side.mp4"
rm -f "$HERE/.side-by-side-raw.mp4"
printf 'wrote %s (framed %s)\n' "docs/demo/side-by-side.mp4" "$FRAME_PAIR"

ffmpeg -v error -y -i "$HERE/side-by-side.mp4" \
    -vf "fps=10,split[a][b];[a]palettegen=max_colors=256:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5" \
    -loop 0 "$HERE/side-by-side.gif"
printf 'wrote %s\n' "docs/demo/side-by-side.gif"
