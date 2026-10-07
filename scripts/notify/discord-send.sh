#!/bin/sh
# The one place freebuff activity is posted into Discord — the second sink beside `phone.sh`.
#
# WHY IT EXISTS
#   The phone (iMessage/ntfy) is where a notification goes when you are away from the Mac. It is
#   not where anything can be *looked up*: a push is a line on a lock screen, and a second
#   reader — the Hermes agent sitting in Discord — has no way to see what freebuff has been
#   doing. This posts the same events into a Discord channel, through `hermes send` on the NAS,
#   which is the supported path for a script to speak on Discord: it reuses the gateway's own
#   bot credentials and, in the tool's own words, runs "no LLM, no agent loop" — so a post is a
#   post and never becomes a turn Hermes answers.
#
# WHY IT REACHES THE NAS ITSELF
#   There is no Discord token on this Mac and there must not be one: the bot's token lives in
#   Hermes' /opt/data/.env on the NAS, and copying it here would be a second place to rotate.
#   So the delivery is `ssh <host> docker exec -i <container> hermes send`, which needs no
#   agent (see ~/.ssh/config's `IdentityFile ~/.ssh/nas_bridge_key` — the default key is
#   passphrase-protected, and anything launched outside a login shell has no ssh-agent, which
#   is exactly how the Hermes MCP server used to die with "Permission denied (publickey)").
#
# WHERE IT IS CALLED FROM
#   `phone.sh --discord`, and only there — a caller asks for Discord by name, so the bells that
#   are not wanted in the channel (ask, pause, pane, locks) never reach it. Today that is the
#   drop bell and the finish bell.
#
# CONFIG
#   FREEBUFF_DISCORD_TARGET   delivery target (default discord:#freebuff). `discord` alone
#                             means the home channel; `discord:#name` and
#                             `discord:<chat_id>[:<thread_id>]` are the other shapes.
#   FREEBUFF_DISCORD          off / 0 / false / no / disabled mutes the sink (the switch
#                             `phone.sh` has for the phone, one level out)
#   FREEBUFF_DISCORD_CMD      the whole delivery command, with the text on stdin — the test
#                             hook, and the escape hatch for a different host or a webhook
#   NAS_HOST                  ssh destination (default billthuan1@192.168.68.58)
#   NAS_CONTAINER_HERMES      container name (default hermes)
#   FREEBUFF_DISCORD_CONNECT_TIMEOUT   ssh connect bound in seconds (default 8)
#
# Usage:  discord-send.sh --title TITLE --message BODY [--target discord:#chan] [--dry-run]
#         discord-send.sh --test
#         discord-send.sh --print
#
# Exit: 0 sent or muted · 2 usage · 69 delivery failed · 78 not configured.
set -u

dir=${FREEBUFF_NOTIFY_DIR:-$HOME/.config/freebuff-notify}
log=$dir/discord.log
version=1

title= message= target= print_only=0 dry_run=0 quiet=0

usage() {
  sed -n '2,42p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case $1 in
    --title) title=${2:-}; [ $# -ge 2 ] && shift ;;
    --message) message=${2:-}; [ $# -ge 2 ] && shift ;;
    --target) target=${2:-}; [ $# -ge 2 ] && shift ;;
    --test) title='freebuff'; message='Discord delivery is wired up — this is a test'; ;;
    --print) print_only=1 ;;
    --dry-run) dry_run=1 ;;
    -q | --quiet) quiet=1 ;;
    -h | --help) usage; exit 0 ;;
    -V | --version) echo "$version"; exit 0 ;;
    --) shift; break ;;
    *)
      printf 'discord-send.sh: unknown argument %s\n' "$1" >&2
      exit 2
      ;;
  esac
  shift
done

# The title is a header line by construction, so it is flattened the way phone.sh flattens its
# own. The BODY keeps its newlines: a finish note is several lines on purpose.
title=$(printf %s "$title" | tr -d '\r\n')
target=${target:-${FREEBUFF_DISCORD_TARGET:-discord:#freebuff}}
target=$(printf %s "$target" | tr -d '\r\n')

# The mute switch, the same shape as phone.sh's: environment, then a state file.
enabled=on
value=${FREEBUFF_DISCORD:-}
if [ -z "$value" ] && [ -r "$dir/discord-state" ]; then
  value=$(cat "$dir/discord-state")
fi
case "$(printf %s "${value:-}" | tr 'A-Z' 'a-z')" in
  off | 0 | false | no | disable | disabled) enabled=off ;;
esac

host=${NAS_HOST:-billthuan1@192.168.68.58}
container=${NAS_CONTAINER_HERMES:-hermes}
hermes_bin=${FREEBUFF_HERMES_BIN:-/opt/hermes/bin/hermes}
connect_timeout=${FREEBUFF_DISCORD_CONNECT_TIMEOUT:-8}

if [ "$print_only" = 1 ]; then
  printf 'target=%s enabled=%s host=%s container=%s\n' \
    "$target" "$enabled" "$host" "$container"
  printf 'hermes=%s cmd=%s\n' "$hermes_bin" "${FREEBUFF_DISCORD_CMD:+set}"
  exit 0
fi

[ "$enabled" = on ] || exit 0
[ -n "$message" ] || {
  printf 'discord-send.sh: nothing to send (--message is required)\n' >&2
  exit 2
}

# A target is interpolated into the remote command line, so a quote, a space or a `$` in it
# would be a second command rather than a channel name. Discord targets are
# `platform`, `platform:id` and `platform:#name`; anything outside that alphabet is a config
# error, not something to pass along and hope.
case $target in
  *[!A-Za-z0-9_:#@/-]* | '' | *\'* | *\"*)
    printf 'discord-send.sh: FREEBUFF_DISCORD_TARGET looks wrong: %s\n' "$target" >&2
    exit 78
    ;;
esac
# Every delivery is logged the same way phone.sh logs its own, and for the same reason: "did it
# actually go?" is otherwise unanswerable from this side, and a silent success looks exactly
# like an hour in which nothing happened. Bounded: a record, not a history.
log_discord() {
  printf '%s discord: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" >>"$log" 2>/dev/null
  if [ -f "$log" ] && [ "$(wc -c <"$log")" -gt 65536 ]; then
    tail -c 16384 "$log" >"$log.cut" && mv "$log.cut" "$log"
  fi
}

# title and body in one message, the way the phone receives them.
text=$(printf '%s\n%s' "${title:-freebuff}" "$message")

send_discord() {
  if [ -n "${FREEBUFF_DISCORD_CMD:-}" ]; then
    printf '%s\n' "$text" | sh -c "$FREEBUFF_DISCORD_CMD" >/dev/null 2>&1
    return $?
  fi
  command -v ssh >/dev/null 2>&1 || return 69
  # Bounded on both ends: ConnectTimeout for the handshake, and a keepalive pair so a peer that
  # went away mid-session is noticed in ~30 s instead of holding the pipe until the TCP timeout.
  printf '%s\n' "$text" | ssh -o BatchMode=yes -o ConnectTimeout="$connect_timeout" \
    -o ServerAliveInterval=15 -o ServerAliveCountMax=2 -o LogLevel=ERROR \
    "$host" "docker exec -i $container $hermes_bin send --to '$target' -f -" \
    >/dev/null 2>&1
}

if [ "$dry_run" = 1 ]; then
  printf 'would send: discord to %s — %s\n' "$target" \
    "$(printf %s "$text" | tr '\n' ' ')"
  exit 0
fi

send_discord
rc=$?
if [ "$rc" = 0 ]; then
  log_discord "sent $target ${title:-freebuff}"
  [ "$quiet" = 1 ] || printf 'discord-send.sh: sent to %s\n' "$target"
  exit 0
fi
log_discord "FAILED $target ${title:-freebuff} · exit ${rc}"
printf 'discord-send.sh: delivery to %s failed (exit %s) — see %s\n' \
  "$target" "$rc" "$log" >&2
exit 69
