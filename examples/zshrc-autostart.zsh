# Start the fbtodo watcher from an interactive shell.
#
# Put this in your `~/.zshrc` (after `fbtodo` is on PATH) so the first terminal of the day
# starts a watcher even when nothing opens a pane in it — the pane keeper then has something
# to keep, and `fbtodo bar` in your status line has a live state from the first second:
#
#     command -v fbtodo >/dev/null && . ~/Projects/fbtodo/examples/zshrc-autostart.zsh
#
# `FBTODO_NO_AUTOSTART=1` skips it (scripts and tmux panes set it so they start nothing of
# their own), and it only runs for an INTERACTIVE shell on a terminal (`-o interactive`,
# `-t 1`), so a non-interactive `zsh -c` is never surprised by a background watcher.
#
# With no Freebuff running, `fbtodo daemon` correctly starts nothing — the watcher needs a
# session to follow, and the next interactive shell will try again.
if [[ -o interactive && -t 1 && -z $FBTODO_NO_AUTOSTART ]] && (( $+commands[fbtodo] )); then
  fbtodo daemon --quiet >/dev/null 2>&1 &!
fi
