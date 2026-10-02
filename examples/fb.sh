# fb — one word to launch the agent with its todo pane, on the current release.
#
# The recommended way to install this is `fbtodo init` — it writes this file into
# your config dir and adds the source line to your shell startup for you:
#
#     fbtodo init                     # writes ~/.config/fbtodo/fb.sh + sources it
#
# You can still do it by hand — source this file from your shell startup, then run
# `fb` instead of `freebuff`:
#
#     . ~/Projects/fbtodo/examples/fb.sh       # bash, zsh, ksh, mksh, dash, sh
#     source ~/Projects/fbtodo/examples/fb.sh  # fish: see FB_SCRIPT's fish twin below
#
# `fbtodo init` knows the Bourne family (bash, zsh, ksh, mksh, dash, sh) and fish;
# this file is the Bourne body, written to run in all of them — no `local` (ksh93
# has none), and no reliance on unquoted word-splitting (zsh does none).
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
#     FBTODO_SPLIT=PLACE     where the pane opens: left|right|top|bottom (which fix the
#                            edge too), or h|v for the splitter's own trailing edge
#                            (right / below). The default is v — below the session.
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
        #
        # Each arm calls tmux itself rather than passing a `$_fb_split` string: zsh
        # does not split an unquoted variable into words, so "-h -b" would arrive as
        # ONE argument there and as two in bash. Same flags, no splitting to rely on.
        _fb_size="${FBTODO_PANE_SIZE:-12}"
        # The place rides into the pane, so the keeper reopens in the same corner.
        _fb_cmd="FBTODO_SPLIT=${FBTODO_SPLIT:-v} fbtodo --instance-of $$ --stale-after 0"
        case "${FBTODO_SPLIT:-v}" in
            left)    tmux split-window -h -b -l "$_fb_size" -d "$_fb_cmd" ;;
            right|h) tmux split-window -h    -l "$_fb_size" -d "$_fb_cmd" ;;
            top)     tmux split-window -v -b -l "$_fb_size" -d "$_fb_cmd" ;;
            *)       tmux split-window -v    -l "$_fb_size" -d "$_fb_cmd" ;;   # bottom, v, unknown
        esac
    fi

    # `command` so the function does not recurse into itself.
    command freebuff "$@"
}

# `npm i -g freebuff`, quietly: the version moving is the point, the install is not.
_fb_refresh() {
    [ -n "${FREEBUFF_NO_REFRESH:-}" ] && return 0
    command -v npm >/dev/null 2>&1 || return 0

    # No `local`: ksh93 has no such builtin. Prefixed names are harmless if they leak.
    _fb_pkg="$(command npm root -g 2>/dev/null)/freebuff/package.json"
    _fb_before="$(_fb_version "$_fb_pkg")"

    # --no-fund / --no-audit: neither adds anything to a global CLI install, and both would
    # spend a network round trip this is already paying for.
    if command npm i -g freebuff --no-fund --no-audit >/dev/null 2>&1; then
        _fb_after="$(_fb_version "$_fb_pkg")"
        if [ -z "$_fb_before" ] && [ -n "$_fb_after" ]; then
            printf 'fb: installed freebuff %s\n' "$_fb_after" >&2
        elif [ -n "$_fb_after" ] && [ "$_fb_before" != "$_fb_after" ]; then
            printf 'fb: freebuff updated %s -> %s\n' "$_fb_before" "$_fb_after" >&2
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
