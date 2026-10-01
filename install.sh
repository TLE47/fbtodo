#!/bin/sh
# fbtodo — the one-line installer.
#
#     curl -fsSL https://raw.githubusercontent.com/TLE47/fbtodo/main/install.sh | sh
#
# It puts one command, `fbtodo`, on your PATH, choosing the first method your
# machine already has, in this order:
#
#     brew   `brew install TLE47/tap/fbtodo` — a real package, no Python chosen for you
#     uv     `uv tool install fbtodo` — an isolated venv, installs in a second
#     pipx   `pipx install fbtodo` — the same idea with pipx
#     pip    a venv under $PREFIX and a symlink into $PREFIX/bin
#     git    clone the checkout and symlink the shipped launcher — the zero-install path
#
# Every method past `git` wants the package from PyPI; if that is not published
# yet (or the network is down) each one falls back to the same thing from git, so
# the installer works today and keeps working once the package is live. Nothing
# runs as root and nothing outside $PREFIX (and the clone directory) is touched.
#
# Flags, all optional:
#     --version X.Y.Z   install a pinned release (git methods use the tag of that name)
#     --method NAME     force one: brew | uv | pipx | pip | clone
#     --prefix DIR      where `fbtodo` lands (default: $HOME/.local)
#     --dir DIR         where the clone method keeps its checkout
#                       (default: $XDG_DATA_HOME/fbtodo)
#     --force           reinstall even when a fbtodo is already on PATH
#     --no-tmux         skip the tmux check and its hint entirely
#     --dry-run         print the commands instead of running them
#     -h, --help        this text
#
# The environment carries the same knobs, so a scripted install needs no flags:
# FBTODO_VERSION, FBTODO_METHOD, FBTODO_PREFIX, FBTODO_DIR, FBTODO_NO_TMUX=1.

set -eu

REPO="TLE47/fbtodo"
TAP="TLE47/tap/fbtodo"
GIT_URL="https://github.com/${REPO}.git"
RAW_URL="https://raw.githubusercontent.com/${REPO}/main/install.sh"

# These two are the only places a URL is built, so a fork changes them and nothing else.
PYPI_SPEC="fbtodo"
GIT_SPEC="git+${GIT_URL}"

METHOD="${FBTODO_METHOD:-auto}"
VERSION="${FBTODO_VERSION:-}"
PREFIX="${FBTODO_PREFIX:-$HOME/.local}"
CLONE_DIR="${FBTODO_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/fbtodo}"
FORCE=0
NO_TMUX="${FBTODO_NO_TMUX:-}"
DRY_RUN=0

# ---------------------------------------------------------------------------
# Words on the terminal. Everything interesting goes to stderr except the "how
# to start it" block at the end, so `curl … | sh` shows progress as it happens
# and a piped stdout stays clean for anything wrapping the script.
# ---------------------------------------------------------------------------
say()  { printf '%s\n' "$*" >&2; }
ok()   { printf '  \033[32m*\033[0m %s\n' "$*" >&2; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*" >&2; }
die()  { printf 'fbtodo installer: %s\n' "$*" >&2; exit 1; }

run() {
    if [ "$DRY_RUN" = 1 ]; then
        printf '  \033[36m>\033[0m %s\n' "$*" >&2
        return 0
    fi
    "$@"
}

have() { command -v "$1" >/dev/null 2>&1; }

usage() {
    # When the script is a file (the usual case, and what --help is for), the
    # header above *is* the help: print it back so the two cannot drift apart.
    # Piped through `curl | sh` there is no file to read ($0 is the shell), so
    # fall back to the short form below.
    if [ -f "$0" ] && sed -n '2,/^$/p' "$0" 2>/dev/null | grep -q .; then
        sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
    else
        cat <<'HELP'
Install fbtodo: a `fbtodo` command on your PATH.

  --version X.Y.Z   install a pinned release
  --method NAME     brew | uv | pipx | pip | clone
  --prefix DIR      where `fbtodo` lands (default: $HOME/.local)
  --dir DIR         where the clone method keeps its checkout
  --force           reinstall even when one is already on PATH
  --no-tmux         skip the tmux check and its hint
  --dry-run         print the commands instead of running them
  -h, --help        this text
HELP
    fi
}

# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        --version) VERSION="${2:-}"; [ -n "$VERSION" ] || die "--version needs a value"; shift 2 ;;
        --version=*) VERSION="${1#*=}"; shift ;;
        --method) METHOD="${2:-}"; [ -n "$METHOD" ] || die "--method needs a value"; shift 2 ;;
        --method=*) METHOD="${1#*=}"; shift ;;
        --prefix) PREFIX="${2:-}"; [ -n "$PREFIX" ] || die "--prefix needs a value"; shift 2 ;;
        --prefix=*) PREFIX="${1#*=}"; shift ;;
        --dir) CLONE_DIR="${2:-}"; [ -n "$CLONE_DIR" ] || die "--dir needs a value"; shift 2 ;;
        --dir=*) CLONE_DIR="${1#*=}"; shift ;;
        --force) FORCE=1; shift ;;
        --no-tmux) NO_TMUX=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown argument: $1 (try --help)" ;;
    esac
done

case "$METHOD" in
    auto|brew|uv|pipx|pip|clone) ;;
    *) die "unknown --method $METHOD (brew, uv, pipx, pip, clone)" ;;
esac

[ -n "$PREFIX" ] || die "--prefix is empty"

# The symlink the pip and clone methods leave behind stores its target string
# verbatim, so a relative --prefix/--dir would make a symlink that resolves
# against the *link's own* directory and is then broken. Resolve both to an
# absolute path before anything is built from them.
abspath() {
    case "$1" in
        /*) printf '%s\n' "$1" ;;
        *)  printf '%s/%s\n' "$(pwd)" "$1" ;;
    esac
}
PREFIX="$(abspath "$PREFIX")"
CLONE_DIR="$(abspath "$CLONE_DIR")"
BIN_DIR="$PREFIX/bin"

# A pinned version: pip/pipx/uv take `spec==X`, the git specs take `@X`.
if [ -n "$VERSION" ]; then
    PYPI_SPEC="fbtodo==${VERSION}"
    GIT_SPEC="git+${GIT_URL}@${VERSION}"
fi

# ---------------------------------------------------------------------------
# Already here? Say so and stop, unless --force. The check is the same one the
# shell would do, so "already installed" means exactly "typing fbtodo works".
# ---------------------------------------------------------------------------
if [ "$FORCE" != 1 ] && [ "$METHOD" != "clone" ] && have fbtodo; then
    found="$(command -v fbtodo)"
    say "fbtodo is already installed: $found"
    "$found" --version 2>/dev/null | sed 's/^/  version /' >&2 || true
    say "re-run with --force to replace it."
    exit 0
fi

say "fbtodo installer — method: $METHOD${VERSION:+, version $VERSION}"
say ""

# ---------------------------------------------------------------------------
# The methods. Each returns 0 only when `fbtodo` is actually runnable, so the
# `auto` ladder can move on rather than trust that a command that exited 0 did
# what it said.
# ---------------------------------------------------------------------------

# A PyPI-first install with the git checkout as the fallback, for the three
# installer-driven methods. `uv tool install fbtodo` fails loudly when nothing
# is published; the retry is the zero-install path that always exists.
install_via_tool() {
    tool="$1"   # uv | pipx
    if have "$tool"; then
        if run "$tool" install --force "$PYPI_SPEC"; then
            return 0
        fi
        warn "$tool could not install $PYPI_SPEC — trying the git checkout"
        run "$tool" install --force "$GIT_SPEC" && return 0
    fi
    return 1
}

method_brew() {
    have brew || return 1
    # The tap is what makes a three-part name work; brew auto-taps it. Pinning a
    # version is a tap concern (`brew pin`), not this flag's, so --version is
    # honoured by refusing cleanly rather than silently installing the latest.
    if [ -n "$VERSION" ]; then
        warn "--version is not supported with --method brew; brew pins by tap"
        return 1
    fi
    run brew install "$TAP"
}

method_uv()  { install_via_tool uv; }
method_pipx() { install_via_tool pipx; }

method_pip() {
    have python3 || return 1
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || {
        warn "python3 is $(python3 -V 2>&1) — fbtodo needs 3.9 or newer"
        return 1
    }
    venv="$PREFIX/share/fbtodo/venv"
    run python3 -m venv "$venv" || return 1
    # Always `python -m pip`: a venv's python is the one path guaranteed to
    # exist, and `-m pip` reaches it whether or not pip's console script did.
    py="$venv/bin/python"
    if ! run "$py" -m pip install --upgrade --quiet "$PYPI_SPEC"; then
        warn "$PYPI_SPEC is not on PyPI yet — installing from git"
        run "$py" -m pip install --upgrade --quiet "$GIT_SPEC" || return 1
    fi
    run mkdir -p "$BIN_DIR"
    run ln -sf "$venv/bin/fbtodo" "$BIN_DIR/fbtodo"
}

method_clone() {
    have git || return 1
    if [ -d "$CLONE_DIR/.git" ]; then
        say "updating the checkout in $CLONE_DIR"
        run git -C "$CLONE_DIR" fetch --depth 1 origin "${VERSION:-main}" || return 1
        run git -C "$CLONE_DIR" checkout --quiet FETCH_HEAD || return 1
    else
        run mkdir -p "$(dirname "$CLONE_DIR")"
        run git clone --depth 1 ${VERSION:+--branch "$VERSION"} "$GIT_URL" "$CLONE_DIR" || return 1
    fi
    run mkdir -p "$BIN_DIR"
    run ln -sf "$CLONE_DIR/fbtodo" "$BIN_DIR/fbtodo"
}

# The ladder. `auto` takes the first that both exists and succeeds.
chosen=""
if [ "$METHOD" != auto ]; then
    case "$METHOD" in
        brew)  method_brew ;;
        uv)    method_uv ;;
        pipx)  method_pipx ;;
        pip)   method_pip ;;
        clone) method_clone ;;
    esac || die "--method $METHOD failed (see above)"
    chosen="$METHOD"
else
    for m in brew uv pipx pip clone; do
        case "$m" in
            brew)  method_brew ;;
            uv)    method_uv ;;
            pipx)  method_pipx ;;
            pip)   method_pip ;;
            clone) method_clone ;;
        esac && { chosen="$m"; break; }
    done
    [ -n "$chosen" ] || die "no install method worked; see the messages above"
fi

say ""
ok "installed with the $chosen method"

# ---------------------------------------------------------------------------
# The two things a fresh install still needs: the binary reachable, and tmux to
# draw a pane in. Both are told, never done for you.
# ---------------------------------------------------------------------------
if [ "$METHOD" != brew ] && [ "$METHOD" != uv ] && [ "$METHOD" != pipx ]; then
    case ":${PATH}:" in
        *":$BIN_DIR:"*) ;;
        *)
            say ""
            warn "$BIN_DIR is not on your PATH — add it, then open a new shell:"
            say '        export PATH="'"$BIN_DIR"':$PATH"'
            ;;
    esac
fi

if [ -z "$NO_TMUX" ]; then
    if ! have tmux; then
        say ""
        warn "tmux is not installed — it is what shows the pane"
        say "        macOS: brew install tmux      Debian/Ubuntu: apt install tmux"
    fi
fi

say ""
say "Start it:"
say "        tmux new -s work        # fbtodo draws beside your session"
say "        fbtodo                  # opens the pane"
say ""
say "Then check it:  fbtodo doctor      docs: https://github.com/${REPO}"
say "(installer: $RAW_URL)"
