#!/bin/sh
# The one place the chime is played, so every caller — the session wrapper at exit, the
# todo bell when the agent finishes and the drop watch when a session dies on its own —
# honours the same pick, volume and on/off.
#
#   bell.sh [--print] [--drop] [tty] [reason]
#
# --drop means "the session dropped", not "it finished": it resolves its own voice
# (FREEBUFF_BELL_SOUND_DROP, else a `drop-sound` file, else Basso) so the two are told
# apart by ear without looking at the screen.
#
# --print resolves and reports without playing (and without honouring the off switch):
# "it did not ring" and "it rang the wrong thing" are then distinguishable.
#
# Resolution order matches the zsh functions in ~/.zshrc: FREEBUFF_BELL / state, then
# FREEBUFF_BELL_SOUND / sound, then the first generated voice, then Glass; volume from
# FREEBUFF_BELL_VOLUME / volume, else 0.5. A --drop call replaces the sound step with
# FREEBUFF_BELL_SOUND_DROP / drop-sound, defaulting to Basso.
set -u
dir=$HOME/.config/freebuff-notify

print_only=0
drop=0
while [ $# -gt 0 ]; do
  case $1 in
    --print) print_only=1 ;;
    --drop) drop=1 ;;
    -h | --help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    --) shift; break ;;
    *) break ;;
  esac
  shift
done
tty=${1:-/dev/tty}
reason=${2:-freebuff finished}

enabled=on
value=${FREEBUFF_BELL:-}
if [ -z "$value" ] && [ -r "$dir/state" ]; then
  value=$(cat "$dir/state")
fi
case "$(printf %s "${value:-}" | tr 'A-Z' 'a-z')" in
  off | 0 | false | no | disable | disabled) enabled=off ;;
esac

name=${FREEBUFF_BELL_SOUND:-}
if [ -z "$name" ] && [ -r "$dir/sound" ]; then
  name=$(cat "$dir/sound")
fi
if [ -z "$name" ]; then
  for kind in piano steel nylon; do
    if [ -r "$dir/$kind.wav" ]; then
      name=$kind
      break
    fi
  done
fi
[ -n "$name" ] || name=Glass

if [ "$drop" = 1 ]; then
  # A drop is not a finish: it gets its own voice, and a low one by default.
  name=${FREEBUFF_BELL_SOUND_DROP:-}
  if [ -z "$name" ] && [ -r "$dir/drop-sound" ]; then
    name=$(cat "$dir/drop-sound")
  fi
  [ -n "$name" ] || name=Basso
fi

# Where the fallback system sounds live; FREEBUFF_SOUNDS_DIR points at another directory
# (the test suite uses one, so its chime checks run without macOS).
sounds=${FREEBUFF_SOUNDS_DIR:-/System/Library/Sounds}
fallback=$sounds/Glass.aiff
case $name in
  piano | f4) file=$dir/piano.wav ; [ -r "$file" ] || file=$fallback ;;
  steel | guitar | pluck | b-string | b) file=$dir/steel.wav ; [ -r "$file" ] || file=$fallback ;;
  nylon) file=$dir/nylon.wav ; [ -r "$file" ] || file=$fallback ;;
  */*) file=$name ;; # a path to any audio file
  *)
    if [ -r "$dir/$name.wav" ]; then
      file=$dir/$name.wav # any generated voice, so a new one cannot fall through
    else
      file=$sounds/$name.aiff
    fi
    ;;
esac

volume=${FREEBUFF_BELL_VOLUME:-}
if [ -z "$volume" ] && [ -r "$dir/volume" ]; then
  volume=$(cat "$dir/volume")
fi
[ -n "$volume" ] || volume=0.5

if [ "$print_only" = 1 ]; then
  printf 'sound=%s enabled=%s volume=%s file=%s drop=%s\n' \
    "$name" "$enabled" "$volume" "$file" "$drop"
  exit 0
fi

[ "$enabled" = on ] || exit 0

if command -v afplay >/dev/null 2>&1 && [ -r "$file" ]; then
  # Backgrounded at the configured volume so the prompt (or the next tick) comes
  # straight back. $reason is kept for anything that wants to log why it rang.
  AFPLAY_REASON=$reason afplay -v "$volume" "$file" &
  exit 0
fi

# Never fail silently: a shell that predates a voice resolves it to a nonexistent file
# and just rings the bell, which is indistinguishable from "it played a Mac sound".
printf "freebuff-bell: cannot play '%s' (%s) — using the plain bell\n" "$name" "$file" >&2
printf '\a' >>"$tty" 2>/dev/null
