#!/bin/sh
# Record docs/demo/demo.gif — plus the same frames as demo.mp4 and three stills — from
# docs/demo/demo.tape.
#
# Needs vhs (https://github.com/charmbracelet/vhs), which pulls ttyd and ffmpeg:
#     brew install vhs
#
# Then, from anywhere:
#     docs/demo/record.sh
#
# The tape writes the GIF; the MP4 and the stills are transcoded from that same GIF, so there is
# one renderer to keep honest rather than two. The fixture is rebuilt each run, so recording twice
# gives the same demo — the clock in the frame is the only thing that differs.

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

# The tape's paths are relative to the repo root, which is also where `fbtodo` is invoked
# from, so the pane and the driver agree on what "docs/demo/fixture" means.
cd "$ROOT"
vhs "$HERE/demo.tape"
printf 'wrote %s\n' "docs/demo/demo.gif"

# The MP4 is the GIF's frames again, with a real player's colour depth and a fast start so it
# streams from the first byte.
ffmpeg -v error -y -i "$HERE/demo.gif" -pix_fmt yuv420p -crf 23 -movflags +faststart \
    "$HERE/demo.mp4"
printf 'wrote %s\n' "docs/demo/demo.mp4"

# Three stills, at the seconds the docs name: one step in, six of the eight, and the last frame.
for still in "1 demo-start" "13 demo-mid" "20 demo-done"; do
    # shellcheck disable=SC2086
    set -- $still
    ffmpeg -v error -y -ss "$1" -i "$HERE/demo.gif" -frames:v 1 "$HERE/$2.png"
    printf 'wrote %s\n' "docs/demo/$2.png"
done
