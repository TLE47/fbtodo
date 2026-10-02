"""fbtodo init — teach your shell the `fb` launcher.

`fb` is a shell function (see `examples/fb.sh`): it refreshes the agent, opens the
todo pane bound to this shell via `--instance-of $$`, then starts the agent. It
*cannot* be a standalone binary on PATH, because `$$` must be the interactive
shell's pid, not a wrapper script's — a wrapper would bind the pane to itself and
the session it started would be "whichever one is newest in this directory",
not "the one this shell launched".  So `fbtodo init` writes the function into
your shell's startup file — one word (`fb`), zero copy-paste.

The function lives here (in the package) so it stays in step with the CLI;
`examples/fb.sh` is the human-readable twin.
"""
from __future__ import annotations

import os
import sys

from .base import EX_CODES, HOME

__all__ = ["cmd_init"]


# Where user config lives, per XDG: $XDG_CONFIG_HOME/fbtodo or ~/.config/fbtodo.
# Same directory fbtodo already uses for theme.json, so one dotdir serves both.
CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config"),
    "fbtodo",
)

# The launcher itself.  A raw string: shell escapes (``\\``) and printf ``\\n``
# must reach the file literally, not become Python escapes.
FB_SCRIPT = r'''# fbtodo: the `fb` launcher. Managed by `fbtodo init`.
# Source this from your shell startup and `fb` replaces `freebuff` — the agent
# starts with its todo pane beside it. See examples/fb.sh for full comments.

fb() {
    case "$1" in
        -h|--help|-V|--version) command freebuff "$@"; return $? ;;
    esac

    _fb_refresh                      # stay on the current release

    if [ -n "${TMUX:-}" ] && [ -z "${FBTODO_NO_PANE:-}" ] && command -v fbtodo >/dev/null 2>&1; then
        # -d splits without stealing the cursor, so the agent still starts in this pane.
        case "${FBTODO_SPLIT:-v}" in
            left)    _fb_split="-h -b" ;;
            right|h) _fb_split="-h" ;;
            top)     _fb_split="-v -b" ;;
            *)       _fb_split="-v" ;;   # bottom, v, and anything unknown
        esac
        # The place rides into the pane, so the keeper reopens in the same corner.
        tmux split-window $_fb_split -l "${FBTODO_PANE_SIZE:-12}" -d \
            "FBTODO_SPLIT=${FBTODO_SPLIT:-v} fbtodo --instance-of $$ --stale-after 0"
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

    # --no-fund / --no-audit: neither adds anything to a global CLI install.
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

# Want a short name for the tool itself as well? Put one of these *outside* the
# function — `ft` reads a snapshot, asks why a pane is where it is, and so on:
#     alias ft=fbtodo        # ft snap · ft json · ft bar · ft why · ft status
'''


# The line placed in the shell startup file.  Uses $XDG_CONFIG_HOME so it stays
# correct at shell-startup time even if the user's config home changes.
_SOURCE_LINE = (
    '# fbtodo: fb launcher — one-word agent + pane start (see examples/fb.sh)\n'
    '. "${XDG_CONFIG_HOME:-$HOME/.config}/fbtodo/fb.sh"\n'
)

# Marker we search for to decide the startup file already sources fb.sh.
_SOURCE_MARKER = "fbtodo/fb.sh"

# Shells we know how to configure, mapped to their startup file.
_STARTUP_FILES = {
    "zsh": "~/.zshrc",
    "bash": "~/.bashrc",
}


def _detect_shell() -> str | None:
    """Best-effort shell detection from $SHELL."""
    shell = os.environ.get("SHELL", "")
    if shell:
        return os.path.basename(shell)
    return None


def _startup_path(shell: str) -> str | None:
    """Return the expanded startup file for *shell*, or None if unsupported."""
    path = _STARTUP_FILES.get(shell.lower())
    return os.path.expanduser(path) if path else None


def cmd_init(args) -> int:
    """Write the `fb` launcher into the shell startup file."""
    fb_path = os.path.join(CONFIG_DIR, "fb.sh")
    shell = args.shell or _detect_shell()

    print("fbtodo init")
    print(f"  config dir   : {CONFIG_DIR}")
    print(f"  launcher     : {fb_path}")

    if not shell:
        print(f"  shell        : (not detected — set SHELL or pass --shell bash|zsh)",
              file=sys.stderr)
        return EX_CODES["config"]

    startup = _startup_path(shell)
    if startup is None:
        print(f"  shell        : {shell} (unsupported)", file=sys.stderr)
        print(f"  supported    : {', '.join(sorted(_STARTUP_FILES))}",
              file=sys.stderr)
        print(f"  note         : fb.sh is still written — source it manually:",
              file=sys.stderr)
        print(f"                 . {fb_path}", file=sys.stderr)
        # Still drop the file so manual sourcing works.
        _write_launcher(fb_path)
        return EX_CODES["config"]

    print(f"  shell        : {shell}")
    print(f"  startup file : {startup}")

    if args.dry_run:
        print("  dry run      : no files written")
        return EX_CODES["ok"]

    _write_launcher(fb_path)
    _source_in_startup(startup)

    print()
    print(f"Done.  Either restart your shell or run:  . {startup}")
    print(f"Then `fb` starts the agent with its todo pane.")
    return EX_CODES["ok"]


def _write_launcher(fb_path: str) -> None:
    """Create the config dir and write FB_SCRIPT to *fb_path*."""
    try:
        os.makedirs(CONFIG_DIR, mode=0o755, exist_ok=True)
        with open(fb_path, "w") as f:
            f.write(FB_SCRIPT)
    except OSError as e:
        print(f"  error        : could not write {fb_path}: {e}", file=sys.stderr)
        raise
    print("  launcher     : written")


def _source_in_startup(startup: str) -> None:
    """Append the source line to *startup*, idempotently."""
    existing = ""
    if os.path.exists(startup):
        try:
            with open(startup) as f:
                existing = f.read()
        except OSError as e:
            print(f"  error        : could not read {startup}: {e}", file=sys.stderr)
            raise

    if _SOURCE_MARKER in existing:
        print("  startup file : already sources fb.sh")
        return

    try:
        with open(startup, "a") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write(_SOURCE_LINE)
    except OSError as e:
        print(f"  error        : could not write {startup}: {e}", file=sys.stderr)
        raise
    print("  startup file : added source line")
