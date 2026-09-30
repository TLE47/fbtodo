# docs/demo/demo.tape — the recording behind the README's demo.
#
#     brew install vhs webp
#     docs/demo/record.sh          # writes demo.webp and the three stills
#
# This tape writes the raw capture — vhs' own MP4, the only format it has — and record.sh throws it
# away: it trims the frame to the pane's own box, brings it to 1920 px across and lands the frames
# as PNGs, which are the master. The published demo is an animated lossless WebP cut from those
# frames, so nothing lossy sits between the pane and the reader. The geometry matters and is worth
# keeping honest: it is recorded a
# little wider than 1920 so that, once trimmed to the pane's box, record.sh can bring it to
# exactly 1920 px wide (`scale=1920:-2`) — 1080p-class, and just as readable when GitHub shows it
# at README width. The font is scaled to match the wider viewport so the pane still gets the same
# number of columns, and the height is the pane's own height plus the padding — a taller frame is
# mostly empty terminal, which is what the first recording wasted.
#
# What is on screen is the real tool: `fbtodo pane` rendering the fixture journal in
# docs/demo/fixture, while drive.sh replays a scripted session into that same file. Nothing
# here is a screenshot, and nothing touches your own sessions: FBTODO_HOME is pointed at a
# scratch directory so the demo's synthetic steps never end up in your pace history, and the
# PATCH/ALERT rows are read from fixture logs rather than from yours.

Output docs/demo/.demo-raw.mp4

Set Shell bash
Set FontFamily "Menlo"
Set FontSize 32
Set Width 1970
Set Height 800
Set Padding 16
Set Margin 0
Set Framerate 20
Set TypingSpeed 0ms
Set Theme "Catppuccin Mocha"

Hide
Type "cd $(git rev-parse --show-toplevel)"
Enter
Sleep 200ms
# FBTODO_HOME keeps the demo out of your pace history; the two logs are what the PATCH and
# ALERT rows read, and pointing them at the fixture is what keeps YOUR notifier log — and
# the name in it — out of a published GIF.
Type "export FBTODO_HOME=/tmp/fbtodo-demo FBTODO_PATCH_LOG=$PWD/docs/demo/fixture/patch.log FBTODO_ALERT_LOG=$PWD/docs/demo/fixture/phone.log"
Enter
Sleep 200ms
# A pane watching this fixture would reach "done and the turn ended" — which is a real bell
# on a machine with the notify kit installed. Point the watches at a path that does not
# exist and they are skipped, so recording a demo cannot ring anyone's phone.
Type "export FBTODO_NOTIFY=/none FBTODO_DROP=/none FBTODO_ASK=/none FBTODO_PAUSE=/none FBTODO_PANE_BELL=/none"
Enter
Sleep 400ms
Type "docs/demo/drive.sh &"
Enter
Sleep 600ms
# Start the pane on a cleared screen. `clear` wipes everything above it — including the line that
# started it, and the setup before that — so the recording opens on the box, not on a shell full
# of export statements. The sleep is the pane's first paint; `Show` lands on a drawn box.
Type "clear && fbtodo pane --no-daemon --chat docs/demo/fixture -i 0.5 --tick 0.5 --stale-after 0"
Enter
Sleep 1200ms
Show

# Long enough to watch the bar fill and the bell's pair arrive: the driver ticks steps 4, 5,
# 6 and then the last two with the turn ended.
Sleep 22s
