#!/bin/sh
# Record everything in this directory from the two tapes beside it:
#
#     demo.webp                the pane-only demo, trimmed to the pane's box
#     demo-start/mid/done.png  three stills from it, at 1 s, 13 s and 20 s
#     side-by-side.webp        the pairing: the scripted session and the pane, two panes
#
# Needs vhs (https://github.com/charmbracelet/vhs), which pulls ttyd and ffmpeg, the WebP tools
# (`img2webp`), and tmux — the pairing clip is two panes of one tmux window:
#
#     brew install vhs webp
#
# Then, from anywhere:
#
#     docs/demo/record.sh
#
# The published file is an animated **lossless** WebP, not an MP4 and not a GIF. A video would be
# smaller, but a Markdown/HTML page renders an image at full pixel-for-pixel accuracy while it
# renders a video through a codec, with a poster frame, a player and a play button. A GIF renders
# the same way and is supported everywhere, but 256 colours is 256 colours: the pane's gradient bar
# and its antialiased text both get dithered, which is exactly what this recording is supposed to
# show off. WebP is animated in every current browser and in GitHub's and VS Code's Markdown
# previews, and lossless means what is decoded is the frame we measured — no palette, no ringing.
# It is also smaller than the equivalent GIF. If you need a GIF anyway (an old chafa, an old
# engine), ffmpeg can read the WebP: `ffmpeg -i demo.webp -loop 0 demo.gif`. Decode it from the
# start like that; ffmpeg cannot seek into an animated WebP and will hand you a blank frame.
#
# The tape writes a raw MP4 — vhs' own capture format, and the only one it has — and that file is
# decoded and thrown away: one ffmpeg pass trims the frame to the pane's own box (see The frame in
# docs/demo/README.md), brings it to WIDTH px across and decimates it to FPS, landing the frames as
# PNGs. Those PNGs are the master. The WebP and the stills both come from them, so neither is a
# re-encode of the other. The fixture is rebuilt each run, so recording twice gives the same demo —
# the clock in the frame is the only thing that differs.

set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)

need() {
    if ! command -v "$1" >/dev/null 2>&1; then
        printf '%s is not installed — %s\n' "$1" "$2" >&2
        exit 69    # EX_UNAVAILABLE
    fi
}

need vhs "brew install vhs"
need ffmpeg "brew install ffmpeg"
need img2webp "brew install webp"
need tmux "the pairing clip needs it"

# The pane draws its box two rows shorter than the terminal — the last rows belong to the shell it
# sits over — and it collapses a long list rather than overflow a short terminal, so an untrimmed
# frame ends in a band of empty terminal under the box. Each recording is therefore trimmed to the
# box it actually drew, which frame.py measures; the padding is the tapes' own `Set Padding`. The
# trimmed frame is then brought to exactly WIDTH px across (the tapes record a little wider, so
# this is a small downscale and never a blur), which is what "1080p-class" means for a terminal
# clip: full HD width, and the height the box actually needs rather than 1080 rows of background.
PADDING=16
WIDTH=1920
# The pane repaints twice a second, so 10 fps is all the motion there is. It is also what the
# lossless WebP costs: every frame is a full, exact frame.
FPS=10

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM HUP

cd "$ROOT"

# ---- the pane-only demo: the real renderer, reading the fixture
vhs "$HERE/demo.tape"
FRAME_DEMO=$(python3 "$HERE/frame.py" "$HERE/.demo-raw.mp4" "$PADDING")
ffmpeg -v error -y -i "$HERE/.demo-raw.mp4" \
    -vf "crop=$FRAME_DEMO,scale=$WIDTH:-2,fps=$FPS" "$WORK/f%04d.png"
rm -f "$HERE/.demo-raw.mp4"
img2webp -loop 0 -d "$((1000 / FPS))" -lossless "$WORK"/*.png -o "$HERE/demo.webp"
printf 'wrote docs/demo/demo.webp (framed %s, %s frames)\n' "$FRAME_DEMO" "$(ls "$WORK" | wc -l | tr -d ' ')"

# Three stills, at the seconds the docs name: one step in, six of the eight, and the last frame.
# They are the frames themselves, copied out of the same master the WebP was built from.
for still in "11 demo-start" "131 demo-mid" "201 demo-done"; do
    # shellcheck disable=SC2086
    set -- $still
    cp "$WORK/$(printf 'f%04d.png' "$1")" "$HERE/$2.png"
    printf 'wrote docs/demo/%s.png\n' "$2"
done
rm -f "$WORK"/*.png

# ---- the pairing: the scripted session and the pane, two panes of one tmux window
vhs "$HERE/side-by-side.tape"
# The clip's own window is taken down by docs/demo/side-by-side.sh; this is the net under a
# recording that ended before that script's own timer did.
tmux -L demo-pair kill-server 2>/dev/null || true
FRAME_PAIR=$(python3 "$HERE/frame.py" "$HERE/.side-by-side-raw.mp4" "$PADDING")
ffmpeg -v error -y -i "$HERE/.side-by-side-raw.mp4" \
    -vf "crop=$FRAME_PAIR,scale=$WIDTH:-2,fps=$FPS" "$WORK/f%04d.png"
rm -f "$HERE/.side-by-side-raw.mp4"
img2webp -loop 0 -d "$((1000 / FPS))" -lossless "$WORK"/*.png -o "$HERE/side-by-side.webp"
printf 'wrote docs/demo/side-by-side.webp (framed %s, %s frames)\n' "$FRAME_PAIR" "$(ls "$WORK" | wc -l | tr -d ' ')"
