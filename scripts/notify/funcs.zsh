# The kit's shell integration: the chime picker and the `freebuff` wrapper that runs the
# tab-title timer and the drop watch. Source it from `~/.zshrc` (this is the file
# `test-freebuff-notify.sh` drives), so a runner — which has no personal shell setup — still
# exercises the wrapper the bells are wired into.
#
# freebuff: keep the session in the tab title — a timer plus what it is working
# on — and play a soft piano F4 once the session is fully over (no more prompts,
# the CLI has exited). The title reads "⏱ freebuff 1m04s · <prompt>" while running
# and "✓ Done freebuff 1m04s · <prompt>" after.
# Commands: freebuff-bell [on|off|toggle|status|test|sound [name]|volume [n]]
# Undo with: unset -f freebuff
#
# A session can also STOP instead of ending: it crashes, the connection goes, the terminal
# is killed. That is the drop watch — the exit status this wrapper sees, plus a disowned
# watchdog for when the whole terminal goes and takes the wrapper with it — and it plays a
# different note (Basso, or $HOME/.config/freebuff-notify/drop-sound) and pushes to the
# phone with the session's last stderr. `drop-bell.py --help` says what counts as a drop;
# FREEBUFF_DROP_INTERRUPT=off silences a session you interrupted yourself.
# Where the fallback system sounds live. FREEBUFF_SOUNDS_DIR points the picker somewhere
# else, which is how the test suite exercises the chime on a machine that is not a Mac.
_FREEBUFF_SOUNDS=${FREEBUFF_SOUNDS_DIR:-/System/Library/Sounds}

_freebuff-bell-enabled() {
  local state="$HOME/.config/freebuff-notify/state" value=${FREEBUFF_BELL:-}
  [[ -z $value && -r $state ]] && value=$(< $state)
  case ${value:l} in
    off|0|false|no|disable|disabled) return 1 ;;
    *) return 0 ;;
  esac
}

# Which chime to play: a recorded note from the SoundFont ("piano" F4, "steel"
# or "nylon" guitar D), a name from /System/Library/Sounds, or a path to any
# audio file.
# NB: every path here is built straight from $HOME — "local a=x b=$a/..." expands
# $a before the assignment runs, which silently produced "/sound".
_freebuff-bell-sound() {
  local name=${FREEBUFF_BELL_SOUND:-} kind
  if [[ -z $name && -r $HOME/.config/freebuff-notify/sound ]]; then
    name=$(< $HOME/.config/freebuff-notify/sound)
  fi
  if [[ -z $name ]]; then
    for kind in piano steel nylon; do
      [[ -r $HOME/.config/freebuff-notify/$kind.wav ]] && name=$kind && break
    done
  fi
  print -r -- ${name:-Glass}
}

_freebuff-bell-label() { # how each recorded voice is described in listings
  case $1 in
    piano) print -r -- 'recorded piano F4' ;;
    steel) print -r -- 'recorded steel guitar open D' ;;
    nylon) print -r -- 'recorded nylon guitar open D' ;;
    *) print -r -- $1 ;;
  esac
}

_freebuff-bell-file() { # name -> the file to play
  local name=$1 dir="$HOME/.config/freebuff-notify"
  case $name in
    piano|f4) [[ -r $dir/piano.wav ]] && print -r -- $dir/piano.wav || print -r -- $_FREEBUFF_SOUNDS/Glass.aiff ;;
    steel|guitar|pluck|b-string|b) [[ -r $dir/steel.wav ]] && print -r -- $dir/steel.wav || print -r -- $_FREEBUFF_SOUNDS/Glass.aiff ;;
    nylon) [[ -r $dir/nylon.wav ]] && print -r -- $dir/nylon.wav || print -r -- $_FREEBUFF_SOUNDS/Glass.aiff ;;
    */*) print -r -- $name ;;
    *) # any generated voice works, so a voice added later can't silently fall
       # through to a missing <name>.aiff and ring the Mac bell instead
       if [[ -r $dir/$name.wav ]]; then print -r -- $dir/$name.wav
       else print -r -- $_FREEBUFF_SOUNDS/$name.aiff
       fi ;;
  esac
}

# Playback volume: FREEBUFF_BELL_VOLUME wins, then the saved value, else 0.5.
_freebuff-bell-volume() {
  local saved="$HOME/.config/freebuff-notify/volume" value=${FREEBUFF_BELL_VOLUME:-}
  [[ -z $value && -r $saved ]] && value=$(< $saved)
  print -r -- ${value:-0.5}
}

_freebuff-ring() { # tty to fall back to; WHY it rang is optional
  # Plays through the shared script so the todo bell, the session wrapper and the
  # interactive `freebuff-bell` commands cannot drift apart: one resolution of
  # sound/volume/on-off, one playback.
  "${FREEBUFF_NOTIFY_DIR:-$HOME/.config/freebuff-notify}/bell.sh" "${1:-/dev/tty}" "${2:-freebuff finished}"
}

freebuff-bell() {
  local state="$HOME/.config/freebuff-notify/state" soundfile="$HOME/.config/freebuff-notify/sound"
  local value=${1:l} current=on name candidate wanted
  _freebuff-bell-enabled || current=off
  case $value in
    volume)
      local volfile="$HOME/.config/freebuff-notify/volume"
      wanted=${2:-}
      if [[ -z $wanted ]]; then
        print "freebuff volume: $(_freebuff-bell-volume)"
        return 0
      fi
      if [[ $wanted != <-> && $wanted != <->.<-> ]]; then
        print -u2 "usage: freebuff-bell volume <number, 1 = normal>"; return 2
      fi
      print -r -- $wanted >| $volfile
      _freebuff-ring
      print "freebuff volume: $wanted"
      return 0
      ;;
    sound)
      local dir="$HOME/.config/freebuff-notify" picked=''
      wanted=${2:-}
      if [[ -z $wanted ]]; then
        name=$(_freebuff-bell-sound)
        local kind
        for kind in piano steel nylon; do
          [[ -r $dir/$kind.wav ]] || continue
          [[ $name == $kind ]] && print "$kind  <- current  ($(_freebuff-bell-label $kind))" \
                              || print "$kind           ($(_freebuff-bell-label $kind))"
        done
        for candidate in $_FREEBUFF_SOUNDS/*.aiff(:t:r); do
          [[ $candidate == $name ]] && print "$candidate  <- current" || print $candidate
        done
        print "pick one with: freebuff-bell sound <name>"
        return 0
      fi
      case ${wanted:l} in
        piano|f4|steel|guitar|pluck|b-string|b|nylon)
          picked=${wanted:l}
          [[ $picked == f4 ]] && picked=piano
          [[ $picked == guitar || $picked == pluck || $picked == b-string || $picked == b ]] && picked=steel
          [[ -r $dir/$picked.wav ]] || {
            print -u2 "no $picked.wav yet — build it with: python3 $dir/make-sound.py --voice $picked"
            return 2
          }
          ;;
        *)
          if [[ $wanted == */* ]]; then
            [[ -r $wanted ]] || { print -u2 "no such file: $wanted"; return 2 }
            picked=$wanted
          else
            for candidate in $_FREEBUFF_SOUNDS/*.aiff(:t:r); do
              [[ ${candidate:l} == ${wanted:l} ]] && picked=$candidate && break
            done
            [[ -n $picked ]] || { print -u2 "unknown sound: $wanted (try: freebuff-bell sound)"; return 2 }
          fi
          ;;
      esac
      print -r -- $picked >| $soundfile
      _freebuff-ring
      print "freebuff sound: $picked"
      return 0
      ;;
    test)
      # print the resolved file too: the usual reason a note "doesn't sound like
      # the one I picked" is that the name resolved somewhere else entirely
      _freebuff-ring
      print "played: $(_freebuff-bell-sound) ($(_freebuff-bell-file "$(_freebuff-bell-sound)"))"
      return 0
      ;;
    on|enable|enabled|start|1|true|yes) value=on ;;
    off|disable|disabled|stop|0|false|no) value=off ;;
    ''|status) value='' ;;
    toggle) value=on; [[ $current == on ]] && value=off ;;
    *) print -u2 "usage: freebuff-bell [on|off|toggle|status|test|sound [name]|volume [n]]"; return 2 ;;
  esac
  if [[ -n $value ]]; then
    print -r -- $value >| $state
    current=$value
  fi
  print "freebuff bell: $current"
}

freebuff() {
  # FREEBUFF_NOTIFY_DIR moves the whole notifier kit (the drop watch, the session timer,
  # the stderr log, the records) somewhere else. Tests set it so a fixture session's
  # death cannot reach the real phone, and phone.sh reads the same variable for its own
  # config — one name, one meaning.
  local dir="${FREEBUFF_NOTIFY_DIR:-$HOME/.config/freebuff-notify}"
  local timer="$dir/session-timer.sh"
  local drop="$dir/drop-bell.py"
  local tty=${FREEBUFF_TTY:-/dev/tty} start=$(date +%s) tpid= ret= rc= wpid=
  # FBTODO_SPLIT names where the list opens: `left`/`right`/`top`/`bottom` (which fix the
  # edge too), or the bare `h`/`v` for `split-window`'s own trailing edge (right / below).
  # A bare `freebuff` keeps the documented default — under the session; `fb` sets `left`
  # for its own launch. The place is handed to the pane as well, so the pane's own keeper
  # reopens in the same corner instead of at the code's default.
  local pane= split=${FBTODO_SPLIT:-v} size=${FBTODO_PANE_SIZE:-12}
  local -a split_flags=()
  local errlog="$dir/session-stderr.log" record="$dir/drop-session-$$-$start.report"

  # A todo pane that lives exactly as long as this session: opened here, and it
  # closes itself too — it follows --instance-of this shell, so it dies with the
  # freebuff process that launched it. --stale-after 0 because the session, not
  # idleness, is what should close a pane that belongs to a running instance.
  # Killing one by hand is not a way to lose the list: `fbtodo pane-watch` (its own
  # process, one per tmux server, started by the pane) puts it back within a few
  # seconds while the session lives — see ~/.agents/skills/freebuff-todo-pane.
  # FBTODO_NO_PANE=1 disables; FBTODO_SPLIT and FBTODO_PANE_SIZE tune it.
  case "$1" in
    -h|--help|-V|--version) command freebuff "$@"; return $? ;;  # no pane for one-shots
  esac

  # The released CLI is patched locally (the ads collapsed, the session-end log kept), and the
  # launcher downloads a fresh binary whenever a release lands — which takes the patches with it.
  # So the patch is re-checked here, before the TUI takes the terminal, and a build that moved the
  # windows is re-anchored on the spot: freebuff-reanchor.mjs --auto derives them from the binary
  # in hand, drills the result on a copy, re-applies, and proves the patched file still runs. It
  # answers from the patcher's size+mtime cache when nothing has changed (one node start, ~80ms,
  # no output), and a heal that cannot be proven changes nothing at all — either way the session
  # starts. Best-effort on purpose: what it could not do it says on stderr, which this wrapper is
  # already tapping into $errlog.
  # Set FREEBUFF_WRAPPER_NO_PATCH=1 to skip it.
  #
  # CONVERGE FIRST: npm refreshes the launcher package and the first launcher run after that
  # replaces the binary — both inside one launch, and the INSTALL is the later step. Patching
  # here first therefore patched a file that was deleted moments later, and a session that
  # installed a release always ran unpatched ("the ads are back").
  # freebuff-converge.cjs asks the launcher for its install now — two JSON reads and no network
  # when nothing is pending — so the re-anchor below lands on the binary this session will run.
  if [[ ${FREEBUFF_WRAPPER_NO_PATCH:-0} != 1 ]] && (( $+commands[node] )); then
    if [[ -r $HOME/Scripts/freebuff-converge.cjs ]]; then
      node "$HOME/Scripts/freebuff-converge.cjs" \
        || print -u2 "freebuff: the launcher's own install did not finish; patching what is there"
    fi
    if [[ -r $HOME/Scripts/freebuff-reanchor.mjs ]]; then
      node "$HOME/Scripts/freebuff-reanchor.mjs" --auto \
        || print -u2 "freebuff: see what moved with node $HOME/Scripts/freebuff-reanchor.mjs --print"
    fi
  fi

  # Preflight for the agent's own shell commands, in the two arms it needs (the shared rule
  # and the numbers: ~/.config/freebuff-preflight-lib.sh). BASH_ENV reaches the `bash -c` a
  # broker spawns; the PATH shims reach a shell that reads no startup file at all (`sh -c`).
  # Both are prefix assignments on the launch below rather than exports: they must reach the
  # CLI process and its children, not linger in the owner's interactive shell, where a
  # shimmed grep would be a surprise. The hook arm costs no fork, and bash shadows the shims
  # (they stay on PATH so a `sh -c` or a `#!/bin/sh` script can still reach them, which costs
  # a bash command nothing); the shim arm's fork is paid only where the hook did not load.
  local -a preflight_env=()
  [[ -r $HOME/.config/freebuff-preflight.sh ]] && \
    preflight_env+=(BASH_ENV="$HOME/.config/freebuff-preflight.sh")
  [[ -x $HOME/.config/freebuff-preflight-bin/grep ]] && \
    preflight_env+=(PATH="$HOME/.config/freebuff-preflight-bin:$PATH")

  if [[ -n $TMUX && -z $FBTODO_NO_PANE ]] && (( $+commands[fbtodo] )); then
    local h=$(tmux display -p '#{window_height}' 2>/dev/null)
    if [[ ${h:-0} -ge 20 ]]; then
      case $split in
        left)     split_flags=(-h -b) ;;   # a column on the session's leading edge
        right|h)  split_flags=(-h) ;;
        top)      split_flags=(-v -b) ;;
        bottom|v) split_flags=(-v) ;;
        *)        split_flags=(-v) ;;       # unknown: the old default
      esac
      pane=$(tmux split-window "${split_flags[@]}" -l $size -d -P -F '#{pane_id}' \
        "FBTODO_SPLIT=$split fbtodo --instance-of $$ --stale-after 0" 2>/dev/null)
    fi
  fi

  # Alternate screen off for this window, before the CLI starts. tmux gives an alt
  # screen no scrollback, and the CLI's TUI uses one and grabs the mouse: the wheel
  # went to the app and did nothing, and a question taller than the pane was lost
  # off the top with no way back. Off, its frames land in real history, which the
  # wheel route in tmux.conf.local scrolls. tmux reads the option only when an app
  # asks for the switch, so a session already running keeps its alt screen -- this
  # takes effect on the next launch. Undo: tmux set -w alternate-screen on.
  [[ -n $TMUX ]] && tmux set-option -w alternate-screen off 2>/dev/null

  # The drop watch, before the session starts: a terminal that dies takes this shell with
  # it mid-`command freebuff`, so the exit status below is exactly what a killed session
  # never reports. The watchdog is disowned, watches THIS shell, and stays silent unless
  # the shell goes with no report written — the record below is how it is told it can go.
  if [[ -x $drop && -w $dir ]]; then
    : >"$errlog"                    # this session's stderr, kept for the report it may need
    "$drop" --watch $$ --record "$record" --cwd "$PWD" \
      --stderr-log "$errlog" --tty "$tty" >/dev/null 2>&1 &!
    wpid=$!
  fi

  if [[ -x $timer ]]; then
    "$timer" run "$start" "$tty" &!
    tpid=$!
  fi
  if [[ -w $dir ]]; then
    # Only stderr is tapped: stdout stays the terminal freebuff draws its UI on (a piped
    # stdout would stop it being a full-screen app), while whatever it logs about a
    # failure is kept where the drop report can quote it.
    { env "${preflight_env[@]}" freebuff "$@" } 2> >(tee -a "$errlog" >&2)
  else
    env "${preflight_env[@]}" freebuff "$@"
  fi
  ret=$?
  # close the pane with the session — belt and braces with the pane's own exit
  [[ -n $pane ]] && tmux kill-pane -t "$pane" 2>/dev/null
  [[ -n $tpid ]] && kill $tpid 2>/dev/null
  [[ -x $timer ]] && "$timer" finish "$start" "$tty"  # stamps the total time

  # Report the exit, then ring: the record first, so the watchdog stands down instead of
  # reporting a session that this wrapper has already accounted for. Exit 10 says the
  # session dropped — it chimed and pushed already, so the usual exit chime is skipped.
  if [[ -x $drop && -w $dir ]]; then
    print -r -- "pid=$$ exit=$ret at=$(date '+%Y-%m-%dT%H:%M:%S')" >|"$record"
    "$drop" --local --exit "$ret" --cwd "$PWD" --stderr-log "$errlog" \
      --shell-pid $$ --tty "$tty"
    rc=$?
    # the watchdog is done either way: killed here, or standing down because the record
    # told it this session was accounted for. The record goes with it.
    [[ -n $wpid ]] && kill $wpid 2>/dev/null
    rm -f "$record"
  fi
  case $rc in
    10) : ;;  # a drop: it rang its own note and pushed; the exit chime would double it
    *) _freebuff-ring "$tty" "the session exited" ;;
  esac
  return $ret
}
