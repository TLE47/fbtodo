#!/bin/sh
# The one place a phone notification is sent — the finish bell's counterpart for when
# you are not at the Mac. Every caller goes through it, so the switch, the server and
# the topic resolve the same way everywhere.
#
#   phone.sh --title T --message M [--priority P] [--tags a,b]
#   phone.sh --test        send a fixed "it is wired up" notification
#   phone.sh --print       resolve and report, send nothing
#   phone.sh --dry-run     print the exact request, send nothing
#   phone.sh --init        create the config with a fresh topic, send nothing
#
# Two transports, chosen by FREEBUFF_PHONE_TRANSPORT (`auto` by default):
#
#   imessage  AppleScript → Messages.app → IMESSAGE_TO. Lands in a thread the phone
#             already notifies about, with nothing to subscribe to and nothing to
#             configure on the device. Needs the Mac awake and signed in to iMessage.
#   ntfy      a POST whose body is the message, with Title/Priority/Tags headers, to
#             https://ntfy.sh or your own instance (same API). No account: the TOPIC is
#             the secret, so it lives in a 0600 file and is never a command-line argument
#             (argv is world-readable in `ps`, and so is the Authorization header of a
#             self-hosted setup — that one travels in a 0600 curl --config file instead).
#
# `auto` sends by iMessage and falls back to ntfy when Messages cannot (signed out,
# asleep, no Automation permission), so a Mac-side outage is not a silent notification.
#
# `both` sends down BOTH pipes every time, for the case where either one alone can go
# quiet at the far end (a hidden or muted self-thread, a phone that stops notifying about
# one app): two alerts, on two independent apps, and no fallback to reason about. It needs
# a topic as well as a handle — half the transport pair is a config error, not a guess.
#
# Config (0600) at ~/.config/freebuff-notify/phone.conf, shell syntax:
#   FREEBUFF_PHONE_TRANSPORT=auto    # auto | imessage | ntfy | both
#   IMESSAGE_TO=you@icloud.com       # the iMessage target (your own handle = note to self)
#   NTFY_URL=https://ntfy.sh
#   NTFY_TOPIC=freebuff-0123456789abcdef
#   NTFY_TOKEN=tk_...                # optional, self-hosted instances with access control
# Environment wins over the file: NTFY_URL / NTFY_TOPIC / NTFY_TOKEN / IMESSAGE_TO /
# FREEBUFF_PHONE_TRANSPORT, and FREEBUFF_PHONE=off (or a `phone-state` file) mutes sending
# without unsetting anything. FREEBUFF_OSASCRIPT points at a different iMessage sender
# (the test drives a stub with it).
#
# Exit: 0 sent or muted · 2 usage · 69 delivery failed · 78 not configured.
set -u

dir=${FREEBUFF_NOTIFY_DIR:-$HOME/.config/freebuff-notify}
conf=${FREEBUFF_PHONE_CONF:-$dir/phone.conf}
log=$dir/phone.log
version=2

title= message= priority=default tags=white_check_mark
print_only=0 dry_run=0

usage() {
  sed -n '2,44p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case $1 in
    --title) title=${2:-}; [ $# -ge 2 ] && shift ;;
    --message) message=${2:-}; [ $# -ge 2 ] && shift ;;
    --priority) priority=${2:-}; [ $# -ge 2 ] && shift ;;
    --tags) tags=${2:-}; [ $# -ge 2 ] && shift ;;
    --test) title='freebuff'; message='phone notifications are wired up — this is a test'; ;;
    --print) print_only=1 ;;
    --dry-run) dry_run=1 ;;
    --init) init=1 ;;
    -q | --quiet) : ;;
    -h | --help) usage; exit 0 ;;
    -V | --version) echo "$version"; exit 0 ;;
    --) shift; break ;;
    *)
      printf 'phone.sh: unknown argument %s\n' "$1" >&2
      exit 2
      ;;
  esac
  shift
done

# The mute switch, same shape as bell.sh's: environment, then a state file.
enabled=on
value=${FREEBUFF_PHONE:-}
if [ -z "$value" ] && [ -r "$dir/phone-state" ]; then
  value=$(cat "$dir/phone-state")
fi
case "$(printf %s "${value:-}" | tr 'A-Z' 'a-z')" in
  off | 0 | false | no | disable | disabled) enabled=off ;;
esac

# The Apple ID this Mac is signed in as — the default iMessage target, so a note to self
# needs no typing. Read from the account plist; there is no API for "my own handle".
discover_appleid() {
  for src in MobileMeAccounts com.apple.imservice.ids; do
    found=$(defaults read "$src" 2>/dev/null |
      sed -n 's/.*AccountID[ =>"]*"\([^"@]*@[^"]*\)".*/\1/p' | head -1)
    [ -n "$found" ] && { printf '%s' "$found"; return 0; }
  done
  return 1
}

# --init: write a fresh config. The topic is generated here rather than typed by hand:
# it is the only thing authenticating the pushes, and it must never reach `ps` or a
# shell history.
if [ "${init:-0}" = 1 ]; then
  if [ -s "$conf" ]; then
    printf 'phone.sh: %s already exists — edit it, or remove it and re-run --init\n' "$conf" >&2
    exit 78
  fi
  hex=$(LC_ALL=C tr -dc 'a-f0-9' </dev/urandom 2>/dev/null | head -c 24)
  [ ${#hex} -eq 24 ] || hex=$(printf '%s%s' "$(date +%s)" "$$")
  mkdir -p "$dir"
  (umask 077; : >"$conf")
  chmod 600 "$conf"
  appleid=$(discover_appleid || true)
  {
    printf '# Where the freebuff notifications go.\n'
    printf '# auto = iMessage first, ntfy when Messages cannot send.\n'
    printf '# both = send down both pipes every time (belt and braces).\n'
    printf 'FREEBUFF_PHONE_TRANSPORT=auto\n'
    if [ -n "$appleid" ]; then
      printf '# The Mac is signed in as this Apple ID: a note-to-self thread, which every\n'
      printf '# device already notifies about.\n'
      printf 'IMESSAGE_TO=%s\n' "$appleid"
    else
      printf '# IMESSAGE_TO=you@icloud.com   # set this to turn on the iMessage transport\n'
    fi
    printf '\n# ntfy, the fallback: subscribe to this topic in the ntfy app (iOS/Android);\n'
    printf '# the topic IS the secret — anyone who knows it can read the pushes.\n'
    printf 'NTFY_URL=https://ntfy.sh\n'
    printf 'NTFY_TOPIC=freebuff-%s\n' "$hex"
    printf '# NTFY_TOKEN=tk_...   # only for a self-hosted instance with access control\n'
  } >"$conf"
  printf 'wrote %s\n' "$conf"
  [ -n "$appleid" ] && printf 'iMessage target:  %s\n' "$appleid"
  printf 'subscribe in the ntfy app to:  freebuff-%s\n' "$hex"
  printf 'or open:  https://ntfy.sh/freebuff-%s\n' "$hex"
  exit 0
fi

# Config: the file first, then the environment, so env wins.
env_url=${NTFY_URL:-} env_topic=${NTFY_TOPIC:-} env_token=${NTFY_TOKEN:-}
env_to=${IMESSAGE_TO:-} env_transport=${FREEBUFF_PHONE_TRANSPORT:-}
NTFY_URL= NTFY_TOPIC= NTFY_TOKEN= IMESSAGE_TO= FREEBUFF_PHONE_TRANSPORT=
if [ -r "$conf" ]; then
  # shellcheck disable=SC1090
  . "$conf"
fi
[ -n "$env_url" ] && NTFY_URL=$env_url
[ -n "$env_topic" ] && NTFY_TOPIC=$env_topic
[ -n "$env_token" ] && NTFY_TOKEN=$env_token
[ -n "$env_to" ] && IMESSAGE_TO=$env_to
[ -n "$env_transport" ] && FREEBUFF_PHONE_TRANSPORT=$env_transport
url=${NTFY_URL:-https://ntfy.sh}
topic=${NTFY_TOPIC:-}
token=${NTFY_TOKEN:-}

# Which pipe the notification takes: `auto` prefers iMessage and falls back to ntfy,
# `both` takes each of them in turn, and the other two pin it. Anything else is a config
# error rather than a guess.
transport=$(printf %s "${FREEBUFF_PHONE_TRANSPORT:-auto}" | tr 'A-Z' 'a-z')
case $transport in
  auto | ntfy | imessage | both) ;;
  *)
    printf 'phone.sh: FREEBUFF_PHONE_TRANSPORT must be auto, ntfy, imessage or both\n' >&2
    exit 78
    ;;
esac
imessage_to=${IMESSAGE_TO:-}
# Bounded like the curl path: osascript can sit waiting on a permission dialog, and a
# dropped notification beats a hung session. FREEBUFF_OSASCRIPT lets the test drive a stub.
osascript_bin=${FREEBUFF_OSASCRIPT:-osascript}
imessage_timeout=${FREEBUFF_IMESSAGE_TIMEOUT:-20}

mask_handle() { # recognisable in a report, not reusable as an address
  case $1 in
    *@*) printf '%s…@%s' "$(printf %s "${1%%@*}" | cut -c1-2)" "${1#*@}" ;;
    *) printf '%s…' "$(printf %s "$1" | cut -c1-4)" ;;
  esac
}

send_imessage() { # handle, text — 0 only when Messages took it
  [ -n "$1" ] || return 1
  command -v "$osascript_bin" >/dev/null 2>&1 || return 1
  "$osascript_bin" \
    -e 'on run {target, body}' \
    -e 'tell application "Messages"' \
    -e 'set svc to 1st service whose service type = iMessage' \
    -e 'set theBuddy to participant target of svc' \
    -e 'send body to theBuddy' \
    -e 'end tell' \
    -e 'end run' "$1" "$2" >/dev/null 2>>"$dir/phone.err" &
  osa=$!
  ( sleep "$imessage_timeout"; kill -0 "$osa" 2>/dev/null && kill "$osa" 2>/dev/null ) 2>/dev/null &
  wait "$osa"
}

# Every delivery is logged the same way, whichever transport made it: "did it actually go?"
# is otherwise unanswerable from this side, and a silent success looks exactly like a quiet
# hour. Bounded: a record, not a history.
log_phone() {
  printf '%s phone: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" >>"$log" 2>/dev/null
  if [ -f "$log" ] && [ "$(wc -c <"$log")" -gt 65536 ]; then
    tail -c 16384 "$log" >"$log.cut" && mv "$log.cut" "$log"
  fi
}

# Does this transport start with an iMessage? `both` implies ntfy as well, so it also has
# to have a topic to be configured at all (`todo-bell.py` asks first by reading topic=set).
want_imessage=0
case $transport in
  imessage | both) want_imessage=1 ;;
  auto) want_imessage=1 ;;
esac

if [ "$print_only" = 1 ]; then
  printf 'url=%s topic=%s token=%s enabled=%s title=%s\n' \
    "$url" "${topic:+set}" "${token:+set}" "$enabled" "${title:-freebuff}"
  printf 'transport=%s imessage=%s osascript=%s\n' \
    "$transport" "${imessage_to:+$(mask_handle "$imessage_to")}" "$osascript_bin"
  exit 0
fi

# Which transports are in play, resolved before ntfy's own config is validated: an
# iMessage that lands makes the topic irrelevant, and a Messages that cannot send must not
# take the ntfy path down with it.
imessage_text=$(printf '%s\n%s' "${title:-freebuff}" "$message")
# `auto` with no handle is simply an ntfy-only setup; `both` was asked for by name, so a
# missing half is reported rather than quietly downgraded.
[ "$transport" = auto ] && [ -z "$imessage_to" ] && want_imessage=0
if [ "$want_imessage" = 1 ] && [ -z "$imessage_to" ]; then
  printf 'phone.sh: FREEBUFF_PHONE_TRANSPORT=imessage needs IMESSAGE_TO (set one in %s)\n' \
    "$conf" >&2
  exit 78
fi

[ "$enabled" = on ] || exit 0
[ -n "$message" ] || {
  printf 'phone.sh: nothing to send (--message is required)\n' >&2
  exit 2
}

if [ "$transport" = both ] && [ -z "$topic" ]; then
  printf 'phone.sh: FREEBUFF_PHONE_TRANSPORT=both needs NTFY_TOPIC too (set one in %s)\n' \
    "$conf" >&2
  exit 78
fi

delivered=0
tried_imessage=0
if [ "$want_imessage" = 1 ]; then
  if [ "$dry_run" = 1 ]; then
    printf 'would send: iMessage to %s — %s\n' "$(mask_handle "$imessage_to")" \
      "$(printf %s "$imessage_text" | tr '\n' ' ')"
  else
    tried_imessage=1
    if send_imessage "$imessage_to" "$imessage_text"; then
      log_phone "sent imessage to $(mask_handle "$imessage_to") ${title:-freebuff}"
      delivered=1
    else
      detail=$(tr -d '\n' <"$dir/phone.err" 2>/dev/null | head -c 160)
      if [ "$transport" = imessage ]; then
        # Pinned: a failure is a failure. Falling through to ntfy here would send something
        # the owner asked not to send.
        log_phone "FAILED imessage to $(mask_handle "$imessage_to") ${title:-freebuff} · $detail"
        printf 'phone.sh: iMessage failed, and no fallback was asked for — see %s\n' "$log" >&2
        exit 69
      fi
      log_phone "imessage failed, trying ntfy ${title:-freebuff} · $detail"
      printf 'phone.sh: iMessage to %s failed (%s) — falling back to ntfy\n' \
        "$(mask_handle "$imessage_to")" "$detail" >&2
    fi
  fi
  # One delivery is the whole job unless BOTH was asked for: ntfy stays a fallback, so a
  # landed iMessage never turns into two notifications. A FAILED iMessage must fall
  # through to ntfy rather than leave here.
  if [ "$transport" != both ] && [ "$dry_run" != 1 ] && [ "$delivered" = 1 ]; then
    exit 0
  fi
fi

if [ -z "$topic" ]; then
  if [ "$tried_imessage" = 1 ]; then
    printf 'phone.sh: nothing was delivered — iMessage failed and no NTFY_TOPIC is set\n' >&2
    exit 69
  fi
  printf 'phone.sh: no NTFY_TOPIC — run `phone.sh --init` (or set one in %s)\n' "$conf" >&2
  exit 78
fi
# ntfy topics are [A-Za-z0-9_-]{1,64}; anything else would silently land on a
# different URL than the app is subscribed to.
case $topic in
  *[!A-Za-z0-9_-]*)
    printf 'phone.sh: NTFY_TOPIC must be letters, digits, - or _ only\n' >&2
    exit 78
    ;;
esac
if [ ${#topic} -gt 64 ]; then
  printf 'phone.sh: NTFY_TOPIC is longer than ntfy allows (64 chars)\n' >&2
  exit 78
fi

tmp=
cleanup() {
  [ -n "$tmp" ] && rm -f "$tmp"
  tmp=
}
trap cleanup EXIT INT TERM HUP

# The request URL and the bearer token both go in a 0600 curl config file, read with
# `--config`: argv is world-readable in `ps`, and the topic is as much a secret as the
# token. The message (not a secret) stays on the command line, where nothing reads it.
tmp=$dir/.phone-request.$$
(
  umask 077
  {
    printf 'url = "%s/%s"\n' "$url" "$topic"
    if [ -n "$token" ]; then
      printf 'header = "Authorization: Bearer %s"\n' "$token"
    fi
  } >"$tmp"
) || {
  printf 'phone.sh: cannot write %s\n' "$tmp" >&2
  exit 78
}

set -- curl -sS -o "$dir/phone.reply" -w '%{http_code}' \
  --connect-timeout 10 --max-time 30 \
  --retry 3 --retry-delay 1 --retry-max-time 45 \
  --config "$tmp" \
  -H "Title: ${title:-freebuff}" -H "Priority: $priority" -H "Tags: $tags" \
  -d "$message"

if [ "$dry_run" = 1 ]; then
  printf 'would send: %s (url and any token from %s)\n' \
    "$(printf %s "$*" | tr '\n' ' ')" "${conf:-none}"
  exit 0
fi

reply=$("$@" 2>"$dir/phone.err")
rc=$?

if [ "$rc" != 0 ] || [ "${reply:-}" != 200 ]; then
  log_phone "FAILED rc=$rc http=${reply:-none} ${title:-freebuff} · $(tr -d '\n' <"$dir/phone.err" 2>/dev/null | head -c 160)"
else
  log_phone "sent ntfy ${title:-freebuff}"
  delivered=1
fi

if [ "$delivered" = 1 ]; then
  exit 0
fi
# Bounded and logged, never queued: a missed notification is not worth a backlog
# that arrives an hour later. The next finished task pushes again.
printf 'phone.sh: delivery failed (rc=%s http=%s) — see %s\n' \
  "$rc" "${reply:-none}" "$log" >&2
exit 69
