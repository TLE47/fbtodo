# fb — one word to launch the agent with its todo pane.
#
# Source this file from ~/.zshrc or ~/.bashrc, then run `fb` instead of `freebuff`:
#
#     . ~/Projects/fbtodo/examples/fb.sh
#
# It does two things, in the order that matters: it opens the todo pane FIRST, bound to
# this shell, and then starts the agent in the current pane.
#
# `--instance-of $$` is the whole trick. It says "the session this shell launched", not
# "whichever session is newest in this directory" — and a directory with two agents in it
# is exactly why that distinction exists. Without it a wrapper's pane can end up watching
# somebody else's list.
#
# Tunables, all optional:
#     FBTODO_NO_PANE=1     launch with no pane at all (the status-line-only setup)
#     FBTODO_SPLIT=h|v     open the pane beside (h) or below (v, the default)
#     FBTODO_PANE_SIZE=N   how many lines (or columns) the pane gets (12)
#
# `--stale-after 0` is deliberate too: `0` is the tool's "never" for store silence, so a
# pause in the transcript cannot be mistaken for the session being over.

fb() {
    case "$1" in
        -h|--help|-V|--version) command freebuff "$@"; return $? ;;   # one-shots: no pane
    esac

    if [ -n "${TMUX:-}" ] && [ -z "${FBTODO_NO_PANE:-}" ] && command -v fbtodo >/dev/null 2>&1; then
        # -d splits without stealing the cursor, so the agent still starts in this pane.
        # A pane killed by hand comes back on its own: the keeper `fbtodo pane-watch`
        # re-opens it while the session lives.
        tmux split-window "-${FBTODO_SPLIT:-v}" -l "${FBTODO_PANE_SIZE:-12}" -d \
            "fbtodo --instance-of $$ --stale-after 0"
    fi

    # `command` so the function does not recurse into itself.
    command freebuff "$@"
}

# Want a short name for the tool itself as well? Put one of these *outside* the function —
# `ft` reads a snapshot, asks why a pane is where it is, and so on:
#
#     alias ft=fbtodo        # ft snap · ft json · ft bar · ft why · ft status
