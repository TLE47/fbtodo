# fb — one word to launch the agent with its todo pane, on the current release.
#
# Source this file from ~/.zshrc or ~/.bashrc, then run `fb` instead of `freebuff`:
#
#     . ~/Projects/fbtodo/examples/fb.sh
#
# It does three things, in the order that matters:
#
#   1. refreshes the released CLI (`npm i -g freebuff`) — a launcher sees every launch
#      anyway, and running yesterday's build is the quiet way to stay missing a fix,
#   2. opens the todo pane FIRST, bound to this shell,
#   3. starts the agent in the current pane.
#
# The pane half is the part fbtodo cares about. `--instance-of $$` is the whole trick: it
# says "the session this shell launched", not "whichever session is newest in this
# directory" — and a directory with two agents in it is why that distinction exists.
# Without it a wrapper's pane can end up watching somebody else's list.
#
# `--stale-after 0` is deliberate too: `0` is the tool's "never" for store silence, so a
# pause in the transcript cannot be mistaken for the session being over.
#
# Tunables, all optional:
#     FREEBUFF_NO_REFRESH=1  skip the npm refresh. Set this in scripts and tests: the
#                            refresh is a network round trip on every launch.
#     FBTODO_NO_PANE=1       launch with no pane at all (the status-line-only setup).
#     FBTODO_SPLIT=h|v       open the pane beside (h) or below (v, the default).
#     FBTODO_PANE_SIZE=N     how many lines (or columns) the pane gets (12).
#
# The refresh is best-effort on purpose: no npm, or a registry having a bad day, still
# launches the version that is installed — and it says something only when the version
# actually moved, so a normal launch stays quiet. It assumes freebuff came from npm; if
# yours came from somewhere else, set FREEBUFF_NO_REFRESH=1 and drop `_fb_refresh`.

fb() {
    case "$1" in
        -h|--help|-V|--version) command freebuff "$@"; return $? ;;   # one-shots: no pane, no refresh
    esac

    _fb_refresh                      # stay on the current release

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

# `npm i -g freebuff`, quietly: the version moving is the point, the install is not.
_fb_refresh() {
    [ -n "${FREEBUFF_NO_REFRESH:-}" ] && return 0
    command -v npm >/dev/null 2>&1 || return 0

    local pkg before after
    pkg="$(command npm root -g 2>/dev/null)/freebuff/package.json"
    before=$(_fb_version "$pkg")

    # --no-fund / --no-audit: neither adds anything to a global CLI install, and both would
    # spend a network round trip this is already paying for.
    if command npm i -g freebuff --no-fund --no-audit >/dev/null 2>&1; then
        after=$(_fb_version "$pkg")
        if [ -z "$before" ] && [ -n "$after" ]; then
            printf 'fb: installed freebuff %s\n' "$after" >&2
        elif [ -n "$after" ] && [ "$before" != "$after" ]; then
            printf 'fb: freebuff updated %s -> %s\n' "$before" "$after" >&2
        fi
    else
        printf '%s\n' "fb: npm i -g freebuff failed; launching the installed version" >&2
    fi
}

# The version in a package.json, or nothing — no node needed for the report.
_fb_version() {
    [ -r "$1" ] || return 0
    sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$1" | head -n 1
}

# Want a short name for the tool itself as well? Put one of these *outside* the function —
# `ft` reads a snapshot, asks why a pane is where it is, and so on:
#
#     alias ft=fbtodo        # ft snap · ft json · ft bar · ft why · ft status
