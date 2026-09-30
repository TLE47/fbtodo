#!/usr/bin/env python3
"""Print the ffmpeg crop that trims a raw demo recording to the pane's own box.

    python3 docs/demo/frame.py docs/demo/.demo-raw.mp4 [padding]

A tape records the terminal it was given, and the pane does not fill it: it draws its box two rows
shorter than the terminal — the last rows belong to the shell it sits over — and it *collapses* a
long list into "N earlier steps completed" rather than overflow a short one. So a frame tall enough
for all eight steps always ends in empty rows, and a frame short enough to have none hides steps.
`record.sh` therefore records the list and crops the slack back, and this is what measures it.

The box is what is drawn above the terminal's own background, so the ink's bounding box over a few
frames of the clip *is* the box: the union of a second in, mid-list and the last frame, so no state
of the pane can lose a row to a crop measured from another. The width comes out symmetric for the
same reason — the box is centred in the tape's padding — and only the size is rounded down to even
numbers, which is what ffmpeg's crop wants and what h264 requires.
"""

import subprocess
import sys

SAMPLES = (1.0, 8.0, 16.0)  # a second in, mid-list, and the finished frame
LUMA = 70                   # above the dark terminal background, below the box's own lines


def frame_ink_box(path, when):
    """(width, height, (x0, y0, x1, y1)) of the ink at `when`, or None if that frame is empty."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
        check=True, capture_output=True, text=True).stdout.strip()
    w, h = (int(value) for value in probe.split(","))
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(when), "-i", path, "-frames:v", "1",
         "-pix_fmt", "gray", "-f", "rawvideo", "-"],
        check=True, capture_output=True).stdout
    if len(raw) < w * h:
        return None                     # a sample past the end of a short clip
    rows = [y for y in range(h)
            if sum(1 for px in raw[y * w:(y + 1) * w] if px > LUMA) > 4]
    cols = [x for x in range(w)
            if sum(1 for y in range(h) if raw[y * w + x] > LUMA) > 4]
    if not rows or not cols:
        return None
    return w, h, (min(cols), min(rows), max(cols), max(rows))


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        sys.exit(__doc__.strip())
    path = sys.argv[1]
    padding = int(sys.argv[2]) if len(sys.argv) > 2 else 16

    boxes = [box for box in (frame_ink_box(path, when) for when in SAMPLES) if box]
    if not boxes:
        sys.exit("frame.py: no ink in any sampled frame — is the recording empty?")

    w, h = boxes[0][0], boxes[0][1]
    x0 = min(box[2][0] for box in boxes)
    y0 = min(box[2][1] for box in boxes)
    x1 = max(box[2][2] for box in boxes)
    y1 = max(box[2][3] for box in boxes)

    crop_x = max(0, x0 - padding)
    crop_y = max(0, y0 - padding)
    crop_w = (min(w - 1, x1 + padding) - crop_x + 1) // 2 * 2
    crop_h = (min(h - 1, y1 + padding) - crop_y + 1) // 2 * 2
    print(f"{crop_w}:{crop_h}:{crop_x}:{crop_y}")


if __name__ == "__main__":
    main()
