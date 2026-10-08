#!/usr/bin/env python3
"""Derive buffy-chan's pane frames from the full-size art.

The Discord stickers are 900x900 and 600 KB each; the pane shows her at sixteen to forty
pixels. The frames the pane reads are therefore MADE from the originals rather than committed
beside them: this script crops each sticker to its own content (the character is centred in a
lot of transparent margin), averages it down to `--size` square, and writes the small PNGs
into `assets/buffy/`, numbered in the order the owner gave them.

    python3 scripts/buffy-thumbs.py --src ~/Downloads/Buffy-chan_Discord_Stickers
    python3 scripts/buffy-thumbs.py --src DIR --out assets/buffy

The averaging is the point: a sixteen-pixel portrait sampled with "nearest" is a lottery of
single source pixels, and one that is averaged is the character's real colours. `sips` is used
to pre-shrink the source where it is available (it is a system tool on macOS and it is fast);
without it the full image is decoded here, which gives the same picture and takes longer.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from fbtodo import buffy_pixels  # noqa: E402

# The margin left around the character, as a fraction of the crop. The stickers have a sparkle
# or a fist that can sit close to the edge, and a crop that touches her outline looks like a
# mistake at this size.
PAD = 0.04


def content_box(img: tuple, threshold: int = 24) -> tuple:
    """The pixels that are not just transparency: `(x0, y0, x1, y1)`, padded a little.

    Read at a coarse stride — every fourth row and column — because this only has to find the
    edges of a drawing, and the full picture is being averaged anyway.
    """
    width, height, rgba = img
    x0, y0, x1, y1 = width, height, 0, 0
    for y in range(0, height, 4):
        base = y * width * 4
        for x in range(0, width, 4):
            if rgba[base + x * 4 + 3] >= threshold:
                x0, y0 = min(x0, x), min(y0, y)
                x1, y1 = max(x1, x), max(y1, y)
    if x1 <= x0 or y1 <= y0:
        return (0, 0, width, height)
    # A square crop around the content's centre, so the aspect ratio cannot change on the way
    # down (the source is square; the character inside it is not, and a stretched chibi is a
    # different chibi).
    span = max(x1 - x0, y1 - y0)
    pad = int(span * PAD)
    span += 2 * pad
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    left, top = max(0, cx - span // 2), max(0, cy - span // 2)
    right, bottom = left + span, top + span
    if right > width:
        left, right = width - span, width
    if bottom > height:
        top, bottom = height - span, height
    return (max(0, left), max(0, top), min(width, right), min(height, bottom))


def shrink(img: tuple, size: int, box: tuple) -> bytes:
    """The cropped picture averaged into `size` x `size` pixels, as RGBA bytes."""
    x0, y0, x1, y1 = box
    out = bytearray(size * size * 4)
    for row in range(size):
        sy0 = y0 + (y1 - y0) * row // size
        sy1 = max(sy0 + 1, y0 + (y1 - y0) * (row + 1) // size)
        for col in range(size):
            sx0 = x0 + (x1 - x0) * col // size
            sx1 = max(sx0 + 1, x0 + (x1 - x0) * (col + 1) // size)
            rgb = buffy_pixels._band(img, sx0, sx1, sy0, sy1)
            at = (row * size + col) * 4
            out[at:at + 3] = bytes(rgb[:3])
            out[at + 3] = min(255, int(rgb[3] * 255 + 0.5))
    return bytes(out)


PRE_SHRINK_PX = 192


def read_source(path: str, work: str, want: int = 0) -> tuple:
    """One source sticker, decoded — through a system pre-shrink when there is one.

    The pre-shrink is not just for speed: 900x900 decoded in Python is a second a file, and
    the averaging below is what quality depends on, not the size of the intermediate.

    `want` is the frame size being asked for, and the pre-shrink never goes below it: a 192px
    intermediate behind a 384px frame is an UPSCALE of a downscale, which is the one way to
    lose detail this script has (measured 2026-10-07: the frames her window draws were 128px,
    and the owner's own words for the result were "still a bit low resolution" — the fix is
    frames at the size she is drawn, out of the 900px originals that were on disk all along).
    """
    sips = shutil.which("sips")
    if sips:
        small = os.path.join(work, os.path.basename(path))
        done = subprocess.run(
            [sips, "-Z", str(max(PRE_SHRINK_PX, want)), path, "--out", small],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if done.returncode == 0 and os.path.exists(small):
            return buffy_pixels.read_png(small)
    return buffy_pixels.read_png(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", required=True, help="directory of full-size PNG frames")
    ap.add_argument("--out", default=os.path.join(ROOT, "assets", "buffy"),
                    help="where the small frames are written")
    ap.add_argument("--size", type=int, default=128, help="side of the small frames, in pixels")
    # 128 rather than 64: the panel zooms in on her face by default (`FBTODO_BUFFY_ZOOM`), which
    # spends fewer source pixels per cell, so the frames have to be bigger than the whole-character
    # grid ever needed. At `--size 64` a zoomed panel would upscale and blur.
    ap.add_argument("--dry-run", action="store_true", help="report what would be written")
    args = ap.parse_args()

    sources = sorted(n for n in os.listdir(args.src) if n.lower().endswith(".png"))
    if not sources:
        print(f"buffy-thumbs: no PNGs in {args.src}", file=sys.stderr)
        return 66
    if not args.dry_run:
        os.makedirs(args.out, exist_ok=True)
    work = tempfile.mkdtemp(prefix="buffy-thumbs-")
    total = 0
    try:
        for i, name in enumerate(sources, 1):
            img = read_source(os.path.join(args.src, name), work, args.size)
            box = content_box(img)
            blob = shrink(img, args.size, box)
            path = os.path.join(args.out, f"{i:02d}.png")
            if args.dry_run:
                print(f"{path}: {args.size}x{args.size} from {name} crop {box}")
                continue
            buffy_pixels.write_png(path, args.size, args.size, blob)
            size = os.path.getsize(path)
            total += size
            print(f"{os.path.relpath(path, ROOT)}  {size:5d} B  <- {name}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if not args.dry_run:
        print(f"{len(sources)} frames, {total} bytes in {os.path.relpath(args.out, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
