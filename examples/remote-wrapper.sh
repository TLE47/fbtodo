#!/bin/sh
# The marker hook a remote session wants, as a file you can read and copy.
#
# Define this function in the shell you use on the REMOTE host (the one you ssh into), then
# call it instead of typing `ssh -t …` or `docker exec -it … freebuff` yourself. It writes
# one line to $HOME/.fb-session on that host:
#
#     <pid> <started> <dir>
#
# …so fbtodo's remote source can tell, exactly, whether the session is still alive and which
# directory (and therefore which project's transcript) it belongs to. Without the marker it
# falls back to a process probe, which works but is guesswork when the same host runs
# several sessions from the same directory.
#
# On this machine, point fbtodo at that host and those variables:
#
#     export FBTODO_NAS=user@host                              # or --nas-host
#     export FBTODO_NAS_ROOT=/srv/app/state/manicode/projects  # or --nas-root
#     export FBTODO_NAS_PROJECT=myproject                      # or --nas-project
#     fbtodo -s nas

remote() {
    # The pid of the session, when it started, and the directory it started in — one line,
    # three fields, read back by fbtodo as 1 (live), 0 (stale) or - (no marker).
    printf '%s %s %s\n' "$$" "$(date -u +%FT%TZ)" "$PWD" >"$HOME/.fb-session"
    trap 'rm -f "$HOME/.fb-session"' EXIT INT TERM HUP

    # Either flavour works: a login shell over ssh, or straight into a container.
    command ssh -t user@host "cd / && exec \$SHELL -l"
    # command docker exec -it freebuff-cli freebuff
}
