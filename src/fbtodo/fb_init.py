"""fbtodo init — teach your shell the `fb` launcher.

`fb` is a shell function (see `examples/fb.sh`): it refreshes the agent, opens the
todo pane bound to this shell via `--instance-of $$`, then starts the agent. It
*cannot* be a standalone binary on PATH, because `$$` must be the interactive
shell's pid, not a wrapper script's — a wrapper would bind the pane to itself and
the session it started would be "whichever one is newest in this directory",
not "the one this shell launched".  So `fbtodo init` writes the function into
your shell's startup file — one word (`fb`), zero copy-paste.

Shells do not share one function syntax, so there are two bodies: a POSIX one
(`FB_SCRIPT`) for the Bourne family — bash, zsh, ksh, mksh, dash, sh — and a
native one (`FISH_SCRIPT`) for fish.  `SHELLS` maps each to its startup file and
its dialect; `--shell` overrides detection and `--startup-file` names a file for
a shell not in the table.

The POSIX body is written to run in all of them: it avoids `local` (which ksh93
lacks) and never relies on unquoted word-splitting (which zsh does not do), the
two places the old bash-only version quietly broke.

The functions live here (in the package) so they stay in step with the CLI;
`examples/fb.sh` is the human-readable twin.
"""
from __future__ import annotations

import os
import subprocess
import sys

from .base import EX_CODES, HOME

__all__ = ["cmd_init"]


def _config_home() -> str:
    """$XDG_CONFIG_HOME, or ~/.config."""
    return os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config")


# Where user config lives: $XDG_CONFIG_HOME/fbtodo.  The same directory fbtodo
# already uses for theme.json, so one dotdir serves both.
CONFIG_DIR = os.path.join(_config_home(), "fbtodo")

# shell name -> (dialect, startup file).  A startup file of None means "resolved
# from a path, see `_startup_for`" (fish keeps its config under XDG_CONFIG_HOME).
SHELLS = {
    "bash": ("posix", "~/.bashrc"),
    "zsh": ("posix", "~/.zshrc"),
    "ksh": ("posix", "~/.kshrc"),
    "mksh": ("posix", "~/.mkshrc"),
    "dash": ("posix", "~/.profile"),
    "sh": ("posix", "~/.profile"),
    "fish": ("fish", None),
}

# Each dialect's launcher file and the line that sources it.  The path is
# absolute (resolved when `init` runs) so the same line works in every dialect
# without a shell-specific `$XDG_CONFIG_HOME` expansion.
_LAUNCHER_FILE = {"posix": "fb.sh", "fish": "fb.fish"}
_SOURCE_PREFIX = {
    "posix": '. "',          # POSIX: `. path`
    "fish": 'source "',      # fish:  source path
}

# Substring we look for to decide the startup file already sources us.  Matches
# both fb.sh and fb.fish, under any config home.
_SOURCE_MARKER = "fbtodo/fb."


# The POSIX-family launcher.  A raw string: shell escapes (``\\``) and printf
# ``\\n`` must reach the file literally, not become Python escapes.
FB_SCRIPT = r'''# fbtodo: the `fb` launcher. Managed by `fbtodo init`.
# Source this from your shell startup and `fb` replaces `freebuff` — the agent
# starts with its todo pane beside it. See examples/fb.sh for full comments.
#
# Written to run under bash, zsh, ksh, mksh, dash and sh: no `local`
# (ksh93 has none), and no reliance on unquoted word-splitting (zsh does none).

fb() {
    case "$1" in
        -h|--help|-V|--version) command freebuff "$@"; return $? ;;
    esac

    _fb_refresh                      # stay on the current release

    if [ -n "${TMUX:-}" ] && [ -z "${FBTODO_NO_PANE:-}" ] && command -v fbtodo >/dev/null 2>&1; then
        # -d splits without stealing the cursor, so the agent still starts in this pane.
        # Each arm calls tmux itself: a variable holding "-h -b" would split into one
        # word under zsh and two under bash.
        _fb_size="${FBTODO_PANE_SIZE:-12}"
        _fb_cmd="FBTODO_SPLIT=${FBTODO_SPLIT:-v} fbtodo --instance-of $$ --stale-after 0"
        case "${FBTODO_SPLIT:-v}" in
            left)    tmux split-window -h -b -l "$_fb_size" -d "$_fb_cmd" ;;
            right|h) tmux split-window -h    -l "$_fb_size" -d "$_fb_cmd" ;;
            top)     tmux split-window -v -b -l "$_fb_size" -d "$_fb_cmd" ;;
            *)       tmux split-window -v    -l "$_fb_size" -d "$_fb_cmd" ;;
        esac
    fi

    # `command` so the function does not recurse into itself.
    command freebuff "$@"
}

# `npm i -g freebuff`, quietly: the version moving is the point, the install is not.
_fb_refresh() {
    [ -n "${FREEBUFF_NO_REFRESH:-}" ] && return 0
    command -v npm >/dev/null 2>&1 || return 0

    # No `local`: ksh93 has no such builtin.  Prefixed names are harmless if they leak.
    _fb_pkg="$(command npm root -g 2>/dev/null)/freebuff/package.json"
    _fb_before="$(_fb_version "$_fb_pkg")"

    # --no-fund / --no-audit: neither adds anything to a global CLI install.
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

# Want a short name for the tool itself as well? Put one of these *outside* the
# function — `ft` reads a snapshot, asks why a pane is where it is, and so on:
#     alias ft=fbtodo        # ft snap · ft json · ft bar · ft why · ft status
'''


# The fish launcher.  Fish is not a Bourne shell: functions end with `end`, there
# is no `local`, no `[ ]` test and no word-splitting of `$var`, so this is a
# native translation of the same three steps rather than the same text.
FISH_SCRIPT = r'''# fbtodo: the `fb` launcher (fish). Managed by `fbtodo init`.
# Source this from config.fish and `fb` replaces `freebuff` — the agent starts
# with its todo pane beside it. See examples/fb.sh for the shape of the thing.

function fb
    switch "$argv[1]"
        case -h --help -V --version
            command freebuff $argv
            return $status
    end

    _fb_refresh                      # stay on the current release

    if set -q TMUX; and not set -q FBTODO_NO_PANE; and command -q fbtodo
        set -l _fb_size 12
        if set -q FBTODO_PANE_SIZE
            set _fb_size $FBTODO_PANE_SIZE
        end
        set -l _fb_where v
        if set -q FBTODO_SPLIT
            set _fb_where $FBTODO_SPLIT
        end
        # $fish_pid is the interactive shell's pid — the analogue of `$$`.
        set -l _fb_cmd "FBTODO_SPLIT=$_fb_where fbtodo --instance-of $fish_pid --stale-after 0"
        switch $_fb_where
            case left
                tmux split-window -h -b -l $_fb_size -d "$_fb_cmd"
            case right h
                tmux split-window -h -l $_fb_size -d "$_fb_cmd"
            case top
                tmux split-window -v -b -l $_fb_size -d "$_fb_cmd"
            case '*'
                tmux split-window -v -l $_fb_size -d "$_fb_cmd"
        end
    end

    command freebuff $argv
end

function _fb_refresh
    set -q FREEBUFF_NO_REFRESH; and return 0
    command -q npm; or return 0

    set -l _fb_pkg (command npm root -g 2>/dev/null)/freebuff/package.json
    set -l _fb_before (_fb_version "$_fb_pkg")

    if command npm i -g freebuff --no-fund --no-audit >/dev/null 2>&1
        set -l _fb_after (_fb_version "$_fb_pkg")
        if test -z "$_fb_before"; and test -n "$_fb_after"
            printf 'fb: installed freebuff %s\n' "$_fb_after" >&2
        else if test -n "$_fb_after"; and test "$_fb_before" != "$_fb_after"
            printf 'fb: freebuff updated %s -> %s\n' "$_fb_before" "$_fb_after" >&2
        end
    else
        printf '%s\n' "fb: npm i -g freebuff failed; launching the installed version" >&2
    end
end

function _fb_version
    test -r "$argv[1]"; or return 0
    sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$argv[1]" | head -n 1
end
'''


_BODY = {"posix": FB_SCRIPT, "fish": FISH_SCRIPT}


def _detect_shell() -> str:
    """Best-effort shell name from $SHELL, then from the parent process."""
    shell = os.environ.get("SHELL", "")
    if shell:
        return os.path.basename(shell)
    # $SHELL is the *login* shell and can be stale (a user who launched fish by
    # hand still has SHELL=/bin/bash), so fall back to whoever launched fbtodo.
    try:
        r = subprocess.run(
            ["ps", "-o", "comm=", "-p", str(os.getppid())],
            capture_output=True, text=True, timeout=5,
        )
        name = r.stdout.strip().split()[0] if r.stdout.strip() else ""
        if name:
            return os.path.basename(name)
    except (OSError, IndexError, subprocess.SubprocessError):
        pass
    return ""


def _startup_for(name: str, dialect: str, override: str | None) -> str | None:
    """The startup file to edit: an explicit override, fish's XDG path, or the table."""
    if override:
        return os.path.expanduser(override)
    if dialect == "fish":
        return os.path.join(_config_home(), "fish", "config.fish")
    path = SHELLS.get(name, (None, None))[1]
    return os.path.expanduser(path) if path else None


def cmd_init(args) -> int:
    """Write the `fb` launcher into the shell startup file."""
    raw = (args.shell or _detect_shell() or "").strip()
    name = os.path.basename(raw).lower()
    dialect, _default = SHELLS.get(name, ("posix", None))

    launcher = os.path.join(CONFIG_DIR, _LAUNCHER_FILE[dialect])
    startup = _startup_for(name, dialect, args.startup_file)

    print("fbtodo init")
    print(f"  config dir   : {CONFIG_DIR}")
    print(f"  launcher     : {launcher}")

    known = name in SHELLS
    if known:
        print(f"  shell        : {name} ({dialect})")
    elif name:
        print(f"  shell        : {name} — not in the table; assuming a POSIX shell")
        print(f"                 (bash, zsh, ksh, mksh, dash, sh, fish are known)")
    else:
        print("  shell        : (not detected — set SHELL, or pass --shell NAME)",
              file=sys.stderr)

    if startup:
        print(f"  startup file : {startup}")

    if args.dry_run:
        print("  dry run      : no files written")
        if not startup:
            print("  note         : no startup file known for this shell — pass",
                  file=sys.stderr)
            print("                 --startup-file, or source the launcher yourself",
                  file=sys.stderr)
            return EX_CODES["config"]
        return EX_CODES["ok"]

    # Nothing to source it from: keep the file for manual use, and say how.
    if not startup:
        _write_launcher(launcher, dialect)
        print("  note         : no startup file known for this shell — source it",
              file=sys.stderr)
        print(f"                 yourself:  {_syntax_hint(dialect)} {launcher}",
              file=sys.stderr)
        return EX_CODES["config"]

    _write_launcher(launcher, dialect)
    _source_in_startup(startup, launcher, dialect)

    print()
    print(f"Done.  Either restart your shell or run:  . {startup}"
          if dialect == "posix" else
          f"Done.  Either restart your shell or run:  source {startup}")
    print("Then `fb` starts the agent with its todo pane.")
    return EX_CODES["ok"]


def _syntax_hint(dialect: str) -> str:
    return "source" if dialect == "fish" else "."


def _write_launcher(path: str, dialect: str) -> None:
    """Create the config dir and write this dialect's body to *path*."""
    try:
        os.makedirs(CONFIG_DIR, mode=0o755, exist_ok=True)
        with open(path, "w") as f:
            f.write(_BODY[dialect])
    except OSError as e:
        print(f"  error        : could not write {path}: {e}", file=sys.stderr)
        raise
    print("  launcher     : written")


def _source_in_startup(startup: str, launcher: str, dialect: str) -> None:
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
        print("  startup file : already sources fb")
        return

    block = (
        "# fbtodo: fb launcher — one-word agent + pane start (see examples/fb.sh)\n"
        + _SOURCE_PREFIX[dialect] + launcher + '"\n'
    )
    try:
        with open(startup, "a") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write(block)
    except OSError as e:
        print(f"  error        : could not write {startup}: {e}", file=sys.stderr)
        raise
    print("  startup file : added source line")
