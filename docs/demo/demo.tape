# docs/demo/demo.tape — the README's demo GIF, recorded with vhs.
#
#     brew install vhs
#     docs/demo/record.sh          # writes docs/demo/demo.gif
#
# What is on screen is the real tool: `fbtodo pane` rendering the fixture journal in
# docs/demo/fixture, while drive.sh replays a scripted session into that same file. Nothing
# here is a screenshot, and nothing touches your own sessions: FBTODO_HOME is pointed at a
# scratch directory so the demo's synthetic steps never end up in your pace history, and the
# PATCH/ALERT rows are read from fixture logs rather than from yours.

Output docs/demo/demo.gif

Set Shell bash
Set FontFamily "Menlo"
Set FontSize 13
Set Width 1080
Set Height 460
Set Padding 16
Set Margin 0
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
Show

Type "fbtodo pane --no-daemon --chat docs/demo/fixture -i 0.5 --tick 0.5 --stale-after 0"
Enter

# Long enough to watch the bar fill and the bell's pair arrive: the driver ticks steps 4, 5,
# 6 and then the last two with the turn ended.
Sleep 22s
