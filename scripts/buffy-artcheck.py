#!/usr/bin/env python3
"""Compare two renders of the same buffy-chan frame — the answer to "is it sharper?".

    scripts/buffy-artcheck.py A.png B.png --source assets/buffy/04.png

Three numbers per image, none of them an opinion:

  edge   mean |gradient| of luminance over the pixels she covers — SHARPNESS.  Goes up with a better
         resample and with any amount of over-sharpening, which is why it is never read alone.
  ring   mean |gradient| in the 2px OUTSIDE her silhouette — HALOS.  A sharpener strong enough to
         grow a bright rim on line-art moves this number, and a render that only moved `edge` is the
         one that is actually sharper rather than harder.
  rmse   the round trip: scale back down to the source's own size and compare with the source
         (mean abs error, 0..255).  The fidelity check — a resample that scores well on `edge` by
         inventing detail scores badly here.

`versus` reports the same statistics for A against B as a percentage, so the direction and the size
of a difference are both visible without arithmetic.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from fbtodo.buffy_pixels import read_png  # noqa: E402


def luma(rgba: bytes, i: int) -> float:
    r, g, b, a = rgba[i], rgba[i + 1], rgba[i + 2], rgba[i + 3]
    return (0.299 * r + 0.587 * g + 0.114 * b) * (a / 255.0)


def stats(path: str) -> dict:
    w, h, rgba = read_png(path)
    inside = []
    outside = []
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            i = (y * w + x) * 4
            a = rgba[i + 3]
            if a < 8:
                continue
            dx = abs(luma(rgba, (y * w + x + 1) * 4) - luma(rgba, (y * w + x - 1) * 4))
            dy = abs(luma(rgba, ((y + 1) * w + x) * 4) - luma(rgba, ((y - 1) * w + x) * 4))
            g = (dx + dy) / 2
            if a > 200:
                inside.append(g)
            else:
                outside.append(g)
    return {"w": w, "h": h, "rgba": rgba,
            "edge": sum(inside) / max(1, len(inside)),
            "ring": sum(outside) / max(1, len(outside))}


def downscale(s: dict, size: int) -> list:
    """Box-average the render back to `size` square, as premultiplied RGBA floats."""
    w, h, rgba = s["w"], s["h"], s["rgba"]
    step = w / size
    out = [0.0] * (size * size * 4)
    for y in range(size):
        for x in range(size):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sy in range(int(y * step), max(int(y * step) + 1, int((y + 1) * step))):
                for sx in range(int(x * step), max(int(x * step) + 1, int((x + 1) * step))):
                    i = (sy * w + sx) * 4
                    a = rgba[i + 3] / 255.0
                    acc[0] += rgba[i] * a
                    acc[1] += rgba[i + 1] * a
                    acc[2] += rgba[i + 2] * a
                    acc[3] += a
            n = ((max(int((y + 1) * step), int(y * step) + 1) - int(y * step)) *
                 (max(int((x + 1) * step), int(x * step) + 1) - int(x * step)))
            for c in range(4):
                out[(y * size + x) * 4 + c] = acc[c] / n
    return out


def rmse(s: dict, source_path: str) -> float:
    _, size, src = read_png(source_path)
    small = downscale(s, size)
    total = 0.0
    for i in range(0, len(src), 4):
        a = src[i + 3] / 255.0
        for c in range(3):
            total += abs(small[i + c] - src[i + c] * a)
    return total / (len(src) / 4 * 3)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("a", help="render to describe (sharp)")
    ap.add_argument("b", nargs="?", help="render to compare it with (plain)")
    ap.add_argument("--source", help="the 128px art the frame came from, for the round trip")
    args = ap.parse_args()

    sa = stats(args.a)
    rows = []
    for label, s in (("A " + os.path.basename(args.a), sa),
                     *([("B " + os.path.basename(args.b), stats(args.b))] if args.b else [])):
        rm = rmse(s, args.source) if args.source else float("nan")
        rows.append((label, s["w"], s["edge"], s["ring"], rm))
    for label, w, edge, ring, rm in rows:
        print(f"{label:28s} {w:4d}px edge={edge:6.3f} ring={ring:6.3f} rmse={rm:6.2f}")
    if len(rows) == 2:
        for name, i in (("edge", 2), ("ring", 3), ("rmse", 4)):
            a, b = rows[0][i], rows[1][i]
            if b:
                print(f"{name:5s} A/B = {100.0 * a / b:6.2f}%  ({a:.3f} vs {b:.3f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
