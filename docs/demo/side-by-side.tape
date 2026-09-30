# docs/demo/side-by-side.tape — the pairing: the session and its pane, side by side.
#
#     docs/demo/record.sh          # records this and the pane-only demo
#
# One tmux window: the scripted session on the left, `fbtodo pane` on the right. The left pane is
# docs/demo/drive.sh with DEMO_NARRATE=1, so the steps it ticks off are the steps the right pane
# shows — which is the whole claim the clip is making. As in the pane-only tape, the session is
# scripted and the pane is not: the renderer, the clocks, the estimates and the bar are the tool's
# own, reading the journal the left pane writes.
#
# The width is spent on two panes rather than one, so the font is a step smaller than the
# pane-only demo's; record.sh trims the frame to the boxes afterwards, so neither clip ends in a
# band of empty terminal.

Output docs/demo/.side-by-side-raw.mp4

Set Shell bash
Set FontFamily "Menlo"
Set FontSize 15
Set Width 1400
Set Height 400
Set Padding 16
Set Margin 0
Set Framerate 20
Set TypingSpeed 0ms
Set Theme "Catppuccin Mocha"

Hide
Type "cd $(git rev-parse --show-toplevel)"
Enter
Sleep 200ms
# The script brings up the window and attaches to it; the sleep is the time that takes, so the
# recording starts on the two panes rather than on the commands that made them.
Type "docs/demo/side-by-side.sh"
Enter
Sleep 1600ms
Show

# Long enough for all four ticks (3.5 s apart) and the last pair; the window is taken down after
# the recording ends, so the clip finishes on the finished list.
Sleep 18s
