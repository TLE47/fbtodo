#!/bin/bash
# Buffy as a picture-in-picture window: build her once, then float her over everything.
#
#     scripts/buffy-pip.sh                     # ~192pt, 900ms a frame, stuck in the pane's middle
#     scripts/buffy-pip.sh 260 1200            # a bigger her, a slower walk
#     scripts/buffy-pip.sh 200 900 SWITCH APP PIDS PIN
#
# The pane can only draw her in CELLS (see `buffy_pixels`): a terminal has no other way to show a
# picture, and a cell is worth two pixels, so on a short pane she is a small blob by geometry. This
# is the same art at its real resolution, in a frameless always-on-top window — grab her anywhere
# to move her, Escape or a right-click to close.
#
# The four optional arguments are what she is STUCK to: the pin's switch file, the app whose window
# is her home, the pane's own process ancestry (comma-separated pids, which is how the window in
# front of the pane is found on any machine — the app's name is only the fallback), and the file
# her TAUGHT pin lives in (the offset from that window's top-left corner that she is pinned to).
# The defaults are the ones in the Swift; `fbtodo pip start` passes all four.
#
# `swiftc` builds it, and it builds to the cache rather than into the checkout: the binary is a
# build product, and the source beside this script is the thing that belongs in the repository.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(dirname "$here")"
bin="${FBTODO_PIP_BIN:-$HOME/.cache/fbtodo/bin/fbtodo-pip}"
src="$here/buffy-pip.swift"
frames="${FBTODO_BUFFY_DIR:-$root/assets/buffy/pip}"
# ...and the 128px set the pane reads is the fallback: a checkout without the large frames still gets
# her, only softer (see the same choice in the Swift).
if [ ! -d "$frames" ]; then frames="$root/assets/buffy"; fi

if ! command -v swiftc >/dev/null 2>&1; then
    echo "buffy-pip: swiftc not found (install the Xcode command line tools)" >&2
    exit 69   # EX_UNAVAILABLE
fi
if [ ! -d "$frames" ]; then
    echo "buffy-pip: no frames in $frames" >&2
    exit 66   # EX_NOINPUT
fi

# Rebuild when the source is newer than the binary, so editing the Swift is enough.
if [ ! -x "$bin" ] || [ "$src" -nt "$bin" ]; then
    mkdir -p "$(dirname "$bin")"
    swiftc -O "$src" -o "$bin"
fi

# Only the arguments that were given are passed: the Swift's own defaults are the defaults, and an
# empty string is not a way to ask for one (it would be taken as a value — a switch file named "").
opts=(--frames "$frames" --side "${1:-192}" --tick "${2:-900}")
if [ -n "${3:-}" ]; then opts+=(--free "$3"); fi
if [ -n "${4:-}" ]; then opts+=(--host "$4"); fi
if [ -n "${5:-}" ]; then opts+=(--pids "$5"); fi
if [ -n "${6:-}" ]; then opts+=(--pin "$6"); fi
# Where the PANE publishes its own row grid (see `note_pane_slack`): the same file the pane writes.
if [ -n "${FBTODO_PIP_SLACK:-}" ]; then opts+=(--slack "$FBTODO_PIP_SLACK"); fi

# ...and the seventh word is `doctor`: take one measurement and EXPLAIN it instead of opening a window
# (see `--doctor` in the Swift).  It is asked for here rather than by the CLI so that the argv she is
# measured with — her frames, her size, the app whose window is her home, the pane's own process chain,
# the hint the pane publishes — is built in exactly one place, and a diagnosis can never describe a
# window other than the one the window itself would be placed in.
if [ "${7:-}" = "doctor" ]; then opts+=(--doctor); fi
# ...and the eighth word is how long a doctor WATCHES, in seconds: for that long it reports every change
# of the pane or of her square in it rather than measuring once (see `watchRun` in the Swift).  Absent is
# the single-shot doctor, exactly as before.
if [ -n "${8:-}" ]; then opts+=(--watch "$8"); fi
# ...and anything after the seconds is a switch for that watch (`--json`, `--stop-on-change`), passed as given.
for flag in "${@:9}"; do opts+=("$flag"); done

exec "$bin" "${opts[@]}"
