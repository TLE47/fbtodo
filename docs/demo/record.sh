#!/bin/sh
# Record docs/demo/demo.gif from docs/demo/demo.tape.
#
# Needs vhs (https://github.com/charmbracelet/vhs):
#     brew install vhs
#
# Then, from anywhere:
#     docs/demo/record.sh
#
# The tape writes the GIF and nothing else; the fixture is rebuilt each run, so recording
# twice gives the same demo.

set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)

if ! command -v vhs >/dev/null 2>&1; then
    printf '%s\n' "vhs is not installed — brew install vhs" >&2
    exit 69    # EX_UNAVAILABLE
fi

# The tape's paths are relative to the repo root, which is also where `fbtodo` is invoked
# from, so the pane and the driver agree on what "docs/demo/fixture" means.
cd "$ROOT"
vhs "$HERE/demo.tape"
printf 'wrote %s\n' "docs/demo/demo.gif"
