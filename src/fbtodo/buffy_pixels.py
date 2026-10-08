"""Buffy-chan as pixels: the pane draws her picture out of glyphs a terminal already has.

A terminal pane cannot put a PNG on screen without an image protocol, and the pane's own
output is a diffed grid of cells — so the picture is built out of cells instead, in one of
the two grids a character cell can carry a pixel in (`buffy_style` chooses, and the panel is
square in pixels either way, because a `cols x rows` panel is read as an image of
`subpixel_w*cols x subpixel_h*rows`):

  * `blocks` — the default — is `▀`: TWO pixels stacked in one cell, the upper in the cell's
    foreground and the lower in its background, `cols x 2*rows` pixels a panel. Two full
    colours a cell, so it keeps flat colour and her own shading, which is what a chibi IS.
    It is the default because it was checked as a picture at the size the pane actually gives
    her and it is the one that reads there: at the nine-row cap the two grids are 18x18 and
    36x36 pixels, and the smaller one wins, because her art is flat colour with thin dark
    lines in it — exactly the case where a cell's second COLOUR is worth more than its second
    pair of pixels.
  * `braille` is ONE braille glyph a cell, U+2800 plus a bitmask: a 2x4 pixel block,
    `2*cols x 4*rows` pixels a panel, four times the pixels of the other grid. It carries two
    colours a cell as well (see `braille_cells`), and it is the grid for a panel with rows to
    spend — on the pane's own short panel its dots are finer than the picture they have to
    describe. A cell whose block is FULL and flat is drawn as a solid `█` instead of its
    eight dots: the dots of a braille glyph do not fill the cell it stands in, so without
    that rule the solid parts of her come out a dim stipple (see `paint_braille`).

Braille is not the fallback either — the pane ALREADY depends on those glyphs, because the
status strip's spinner is `⠋`, so a terminal that can draw this frame at all can draw U+2800.

Whether she is shown whole or in a closer window on her head is `buffy_zoom`'s business, and it
defaults to the whole character: the closer window was checked at every panel height the pane
will reserve and lost at all of them (see `BUFFY_ZOOM_DEFAULT`).

The frames are the Discord stickers the owner drew (see `scripts/buffy-thumbs.py`, which
re-derives the small copies this module reads). Only the small copies are in the repository,
and only the frames the panel is about to show are ever decoded: the pane repaints every
second, so a decode per repaint would be a decode per second, and the cells are cached per
frame, panel size and grid instead.

Nothing here needs an image library. The PNG reader is `zlib` and the five row filters, and
it exists for the same reason the renderer does: the pane is a plain CLI with no dependencies
it has to install, and a picture is not worth an install.
"""

from __future__ import annotations

import os
import struct
import zlib

# Where the frames live: a directory of PNGs, in order. The repository's own copy is the
# default so the pane works out of the box, but the owner's home wins when it is there —
# `~/.config/fbtodo/buffy/` lets a set of frames be replaced without touching the checkout,
# which is how an art change stays out of a code change.
BUFFY_DIR_ENV = "FBTODO_BUFFY_DIR"
BUFFY_HOME_DIR = ("fbtodo", "buffy")

# How long a frame is held, per mood. The mood does not choose WHICH drawings she is shown —
# the frames are one character and the pane walks all of them — it chooses the pace, so a
# finished list flicks through her poses and an idle pane lets each one breathe.
BUFFY_TICK_MS = {
    "work": 900,
    "done": 600,
    "idle": 2000,
    "none": 1500,
    "stale": 1500,
    "error": 1200,
    "nudge": 1200,
}

# Alpha at or above this is a pixel; below it is the sticker's own transparency (the art has
# soft edges, and a half-lit edge pixel at this size is a stray speck, not a soft edge).
BUFFY_ALPHA_MIN = 128

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}


# ------------------------------------------------------------------ reading and writing
def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _unfilter(raw: bytes, width: int, height: int, channels: int) -> bytearray:
    """The PNG's rows after the filters, which is the picture's bytes and nothing else.

    Every row is prefixed by its filter type and each type reaches back to the row above, so
    this is the one pass that cannot be skipped or reordered. Filter 0 (None) rows — what
    `write_png` emits, and what most encoders emit for small flat art — cost one copy.
    """
    stride = width * channels
    out = bytearray(stride * height)
    prior = bytearray(stride)
    at = 0
    for y in range(height):
        kind = raw[at]
        at += 1
        line = bytearray(raw[at:at + stride])
        at += stride
        if kind == 0:
            pass
        elif kind == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif kind == 2:
            for i in range(stride):
                line[i] = (line[i] + prior[i]) & 0xFF
        elif kind == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prior[i]) >> 1)) & 0xFF
        elif kind == 4:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                up = prior[i]
                upleft = prior[i - channels] if i >= channels else 0
                line[i] = (line[i] + _paeth(left, up, upleft)) & 0xFF
        else:
            raise ValueError(f"png: unknown row filter {kind}")
        out[y * stride:(y + 1) * stride] = line
        prior = line
    return out


def read_png(path: str) -> tuple:
    """`(width, height, rgba)` for an 8-bit PNG, alpha filled in for the formats without it.

    Interlaced and sub-byte depths are refused rather than guessed at: both would be silent
    corruption at sixteen pixels, which is worse than an error naming the file.
    """
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:8] != _PNG_SIG:
        raise ValueError(f"not a png: {path}")
    pos, width, height = 8, 0, 0
    depth = kind = interlace = None
    chunks = bytearray()
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        name = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if name == b"IHDR":
            width, height, depth, kind, _comp, _filt, interlace = struct.unpack(">IIBBBBB", body)
        elif name == b"IDAT":
            chunks += body
        elif name == b"IEND":
            break
    if depth != 8 or kind not in _CHANNELS or interlace:
        raise ValueError(f"png: unsupported header in {path} (depth={depth} colour={kind} "
                         f"interlace={interlace})")
    channels = _CHANNELS[kind]
    raw = zlib.decompress(bytes(chunks))
    stride = width * channels
    if len(raw) < (stride + 1) * height:
        raise ValueError(f"png: truncated image data in {path}")
    flat = _unfilter(raw, width, height, channels)
    if channels == 4:
        return width, height, flat
    rgba = bytearray(width * height * 4)
    for i in range(width * height):
        at = i * channels
        out = i * 4
        if channels == 1:
            grey = flat[at]
            rgba[out:out + 4] = bytes((grey, grey, grey, 255))
        elif channels == 3:
            rgba[out:out + 3] = flat[at:at + 3]
            rgba[out + 3] = 255
        else:  # grey + alpha
            grey, alpha = flat[at], flat[at + 1]
            rgba[out:out + 4] = bytes((grey, grey, grey, alpha))
    return width, height, rgba


def write_png(path: str, width: int, height: int, rgba: bytes) -> None:
    """An 8-bit RGBA PNG, filter 0 on every row — the format `read_png` reads back.

    Written by the thumbnail builder and by the suite's own fixtures: a decoder that is only
    ever pointed at files nobody made is a decoder nobody has tested.
    """
    rows = bytearray()
    stride = width * 4
    for y in range(height):
        rows.append(0)
        rows += rgba[y * stride:(y + 1) * stride]

    def chunk(name: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + name + body
                + struct.pack(">I", zlib.crc32(name + body) & 0xFFFFFFFF))

    blob = (_PNG_SIG
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
            + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(blob)


# ------------------------------------------------------------------ the picture, as cells
def _band(img: tuple, x0: int, x1: int, y0: int, y1: int) -> tuple:
    """One rectangle of the source, averaged: `(r, g, b, coverage)`.

    Averaged with the alpha folded in (`r*a`, not `r`), because the stickers are transparent
    around the character and a plain mean would drag every edge pixel towards black — her
    white hair would come out with grey fur. `coverage` is the mean alpha, which is what
    decides whether the half-cell is a pixel at all.
    """
    width, height, rgba = img
    x0, x1 = max(0, min(x0, width)), max(1, min(x1, width))
    y0, y1 = max(0, min(y0, height)), max(1, min(y1, height))
    if x1 <= x0 or y1 <= y0:
        return (0, 0, 0, 0.0)
    r = g = b = 0.0
    alpha_sum = 0.0
    n = 0
    for y in range(y0, y1):
        base = y * width * 4
        for x in range(x0, x1):
            at = base + x * 4
            a = rgba[at + 3]
            alpha_sum += a
            r += rgba[at] * a
            g += rgba[at + 1] * a
            b += rgba[at + 2] * a
            n += 1
    if not n or alpha_sum <= 0:
        return (0, 0, 0, 0.0)
    return (int(r / alpha_sum + 0.5), int(g / alpha_sum + 0.5), int(b / alpha_sum + 0.5),
            alpha_sum / (255.0 * n))


def cells(img: tuple, cols: int, rows: int) -> list:
    """The picture as `rows` lists of `cols` cell pairs: `((upper or None), (lower or None))`.

    Colour only, so this is what gets cached: the ink the caller paints them in (24-bit, a
    256-colour approximation, anything) is not part of the picture and must not be part of
    the cache key.
    """
    width, height, _ = img
    out = []
    for row in range(rows):
        line = []
        for col in range(cols):
            x0 = col * width // cols
            x1 = max(x0 + 1, (col + 1) * width // cols)
            # The cell is split into the two pixel rows the `▀` shows: the top half is the
            # foreground, the bottom half the background.
            y0 = (2 * row) * height // (2 * rows)
            ym = (2 * row + 1) * height // (2 * rows)
            y1 = (2 * row + 2) * height // (2 * rows)
            up = _band(img, x0, x1, y0, ym)
            down = _band(img, x0, x1, ym, max(ym + 1, y1))
            line.append((up[:3] if up[3] * 255 >= BUFFY_ALPHA_MIN else None,
                         down[:3] if down[3] * 255 >= BUFFY_ALPHA_MIN else None))
        out.append(line)
    return out


def paint_cells(grid: list, paint) -> list:
    """The cell pairs as row strings: `▀`, `▄` or a space, in the ink `paint` gives them.

    `paint(rgb, background)` returns the SGR parameters for one colour (the caller's colour
    depth, its theme, its own idea of what colour she is in) — the glyphs and the escapes stay
    here, so the picture never has to know how the pane spells a colour and the pane never has
    to know where a pixel came from.

    Neighbouring cells wanting the same two colours are written as ONE escape and several
    glyphs, which is most of the point of drawing this way: flat colour costs a handful of
    bytes a row instead of a sequence a cell, and this row is rewritten every second.
    """
    rows = []
    for line in grid:
        specs = []
        for upper, lower in line:
            if upper is None and lower is None:
                specs.append((None, None, " "))
            elif upper is None:
                specs.append((paint(lower, False), None, "▄"))
            elif lower is None or upper == lower:
                specs.append((paint(upper, False), None, "▀"))
            else:
                specs.append((paint(upper, False), paint(lower, True), "▀"))
        parts = []
        at = 0
        # Is a colour LIVE that a hole would be painted with? A hole is the PANE's own background,
        # so a blank run after ink has to drop the colour first — otherwise the gap right of her
        # carries her hair's colour out to the row's end, which is a smear rather than a cutout.
        inked = False
        while at < len(specs):
            fg, bg, glyph = specs[at]
            run = 1
            while at + run < len(specs) and specs[at + run] == (fg, bg, glyph):
                run += 1
            if bg is None and fg is None:
                if inked:
                    parts.append("\x1b[0m")
                    inked = False
                parts.append(" " * run)
            elif bg is None:
                parts.append(f"\x1b[{fg}m{glyph * run}")
                inked = True
            else:
                parts.append(f"\x1b[{fg}m\x1b[{bg}m{glyph * run}")
                inked = True
            at += run
        if inked:
            parts.append("\x1b[0m")   # the row ends where she ends: no ink leaks right of her
        rows.append("".join(parts))
    return rows


# ----------------------------------------------------------------- her picture, in braille
# A braille cell is a 2x4 pixel block in ONE glyph (U+2800 + a bitmask) - eight pixels where a
# half-block carries two - so at the SAME panel size her picture is `4*rows` square rather than
# `2*rows`: four times the pixels. A braille cell carries two colours too (see `braille_cells`),
# so what it trades against the half-block grid is colour RESOLUTION rather than colour: four
# sub-pixels of one dot pattern against two exact pixels, and on this pane's panel that trade
# loses (see `buffy_style`). Which one the pane draws is `buffy_style()`, and the knob exists
# because braille lives in the FONT: a terminal whose monospace face renders U+2800 badly has to
# be able to say so.
BUFFY_STYLE_ENV = "FBTODO_BUFFY_STYLE"
# The dot bit for each of the eight sub-pixels, in the Unicode braille order: dots 1,2,3 down
# the left of the cell, 4,5,6 down the right, then 7 and 8 beneath them.
BRAILLE_DOTS = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))
# ...and the two ends of the grid: every dot lit, and the glyph a full cell is drawn with instead
# (a braille cell's dots do not fill it, so a solid area has to be a block to look solid).
BRAILLE_FULL = 0xFF
BRAILLE_SOLID = "█"
# How DARK a sub-pixel has to be, on the 0-255 scale, before it counts as her line art inside a
# cell she fully covers: the eyes, the mouth, the seams of the hood. Absolute rather than measured
# against the cell, and that is the measured part - a cell split against its own midpoint splits
# wherever the art has any gradient at all, which for these stickers is 56 of the 200 cells at the
# owner's panel size, so her face came out as a field of dots. Her line art is dark in an absolute
# sense and everything else is not. Under this level there is no ink to draw and none is drawn.
BUFFY_INK_LEVEL = 96


def buffy_style(value: str | None = None) -> str:
    """Which glyph grid her picture is drawn with: `blocks` (the default) or `braille`.

    Blocks is the default because it is the one that WINS at the size this pane gives her, which
    was checked as pictures rather than argued: at the panel's own cap of nine rows the two grids
    are 18x18 and 36x36 pixels, and the smaller one is the one that reads - a chibi is flat
    colour with thin dark lines in it, exactly the case where a cell's second COLOUR is worth
    more than its second pair of pixels. Braille keeps the panel for a pane with the rows to
    spend (`FBTODO_BUFFY_STYLE=braille`), where its four-times block finally pays. Anything
    unrecognised is the default rather than an error: a typo in a knob must not be the reason a
    pane draws nothing."""
    raw = ((os.environ.get(BUFFY_STYLE_ENV) if value is None else value) or "").strip().lower()
    return "braille" if raw in ("braille", "dots") else "blocks"


def _luma(rgb: tuple) -> int:
    """Perceived brightness of one colour, 0-255: what decides dark ink from light ink.

    Integer Rec. 601 weights rather than a float matrix: this runs on every sub-pixel of every
    cell of every repaint, and a tenth of a shade is not worth a division a pixel.
    """
    r, g, b = rgb[:3]
    return (299 * r + 587 * g + 114 * b) // 1000


def _mean(samples: list) -> tuple | None:
    """The coverage-weighted mean colour of `(r, g, b, cover)` samples, or `None` for none."""
    red = green = blue = seen = 0.0
    for r, g, b, cover in samples:
        red += r * cover
        green += g * cover
        blue += b * cover
        seen += cover
    if seen <= 0:
        return None
    return (int(red / seen + 0.5), int(green / seen + 0.5), int(blue / seen + 0.5))


def _luma(rgb: tuple) -> int:
    """Perceived brightness of one colour, 0-255: what decides ink from background.

    Integer Rec. 601 weights rather than a float matrix: this runs on every sub-pixel of every
    cell of every repaint, and a tenth of a shade is not worth a division a pixel.
    """
    r, g, b = rgb[:3]
    return (299 * r + 587 * g + 114 * b) // 1000


def _mean(samples: list) -> tuple | None:
    """The coverage-weighted mean colour of `(r, g, b, cover[, bit])` samples, or `None`."""
    red = green = blue = seen = 0.0
    for sample in samples:
        r, g, b, cover = sample[:4]
        red += r * cover
        green += g * cover
        blue += b * cover
        seen += cover
    if seen <= 0:
        return None
    return (int(red / seen + 0.5), int(green / seen + 0.5), int(blue / seen + 0.5))


def braille_cells(img: tuple, cols: int, rows: int) -> list:
    """The picture as `rows` lists of `(ink, back, glyph)`: one braille glyph per 2x4 block.

    A cell carries TWO colours - the glyph in `ink` over `back` - and which of three rules fills
    them is the whole of this function. The rules matter because a braille cell is where the
    picture is won or lost:

      * a cell with nothing opaque in it is a HOLE: `(None, None, " ")`, exactly as in `cells`.
      * a cell that is PARTLY opaque IS her edge, and the dots are the opaque sub-pixels: the
        alpha here is the silhouette, so the shape she is cut out of is drawn at four times the
        resolution of the half-block grid. One ink and no background, because what is not her is
        the PANE - painting her average out to the row's end is a smear rather than a cutout.
      * a cell that is FULLY opaque is her INSIDE, and its dots are the sub-pixels DARKER than
        `BUFFY_INK_LEVEL`, drawn in their own colour over the lighter rest of the cell: the eyes,
        the mouth, the seam of the hood. This is the rule the pane was missing. Reading only the
        alpha, every cell inside her outline held ONE averaged ink, so the features that say who
        she is were averaged into flat bands of skin and cream - on the owner's pane, 2026-10-07,
        at 24x10 cells, a face of unbroken horizontal stripes. A cell with nothing dark in it, or
        nothing light, is drawn as a SOLID block in its own mean colour, which is what keeps the
        large flat areas of her bright instead of a dim stipple of dots.

    The threshold is ABSOLUTE rather than relative to the cell, and that is the measured part: a
    cell split against its own midpoint splits wherever the art has any gradient at all, which for
    these three-tone stickers is nearly every cell of her (56 of 200 at the owner's size), so her
    face came out as a field of dots. Her line art is dark in an absolute sense - eyes and outlines
    under a hundred - and everything else is not, so the level is a property of the DRAWING and
    the test is the same in every cell of it.
    """
    width, height, _ = img
    out = []
    for row in range(rows):
        line = []
        for col in range(cols):
            mask = 0
            samples = []
            for sy in range(4):
                y0 = (4 * row + sy) * height // (4 * rows)
                y1 = max(y0 + 1, (4 * row + sy + 1) * height // (4 * rows))
                for sx in range(2):
                    x0 = (2 * col + sx) * width // (2 * cols)
                    x1 = max(x0 + 1, (2 * col + sx + 1) * width // (2 * cols))
                    r, g, b, cover = _band(img, x0, x1, y0, y1)
                    if cover * 255 < BUFFY_ALPHA_MIN:
                        continue
                    mask |= BRAILLE_DOTS[sy][sx]
                    samples.append((r, g, b, cover, BRAILLE_DOTS[sy][sx]))
            if not samples:
                line.append((None, None, " "))
                continue
            if mask != BRAILLE_FULL:
                line.append((_mean(samples), None, chr(0x2800 + mask)))
                continue
            dark = [sample for sample in samples if _luma(sample) < BUFFY_INK_LEVEL]
            if not dark or len(dark) == len(samples):
                line.append((_mean(samples), None, BRAILLE_SOLID))
                continue
            light = [sample for sample in samples if _luma(sample) >= BUFFY_INK_LEVEL]
            bits = 0
            for sample in dark:
                bits |= sample[4]
            line.append((_mean(dark), _mean(light), chr(0x2800 + bits)))
        out.append(line)
    return out


def paint_braille(grid: list, paint) -> list:
    """The braille grid as row strings: one glyph per cell over its own colour, runs collapsed.

    A hole is a space with the colour DROPPED first, and a row with no ink at all spends no
    escapes - the same two rules the half-block rows keep, for the same reasons. A cell with a
    background sets both colours; a cell without one sets only the ink, so the pane's own
    background shows through her edge instead of a colour of ours.

    A cell too flat to split is drawn as the SOLID BLOCK rather than as its eight dots, and that
    is not a flourish: the dots of a braille glyph are drawn small and apart, so a dotted cell
    lights perhaps a third of the cell it stands in and a solid area of her hair comes out a grey
    stipple that is hard to see at all - measured on the pane the owner reads, 2026-10-07. A full
    block paints every pixel of the cell, so the areas that are SOLID in the art are solid in the
    pane, and the dots keep the two jobs only they can do: her silhouette, and the dark line art
    inside her.
    """
    rows = []
    for line in grid:
        # A blank glyph is a hole whatever comes with it: `braille_cells` never writes an ink into
        # a blank cell (its blank is U+2800, not a space), and a caller that hands one in must not
        # get an escape spent on a cell the pane already backgrounds itself.
        specs = [((paint(ink, False), paint(back, True) if back else None, glyph)
                  if ink and glyph != " " else (None, None, " "))
                 for ink, back, glyph in line]
        parts = []
        at = 0
        inked = False
        while at < len(specs):
            fg, bg, glyph = specs[at]
            run = 1
            while at + run < len(specs) and specs[at + run] == (fg, bg, glyph):
                run += 1
            if fg is None:
                if inked:
                    parts.append("\x1b[0m")
                    inked = False
                parts.append(" " * run)
            elif bg is None:
                parts.append(f"\x1b[{fg}m{glyph * run}")
                inked = True
            else:
                parts.append(f"\x1b[{fg}m\x1b[{bg}m{glyph * run}")
                inked = True
            at += run
        if inked:
            parts.append("\x1b[0m")   # the row ends where she ends: no ink leaks right of her
        rows.append("".join(parts))
    return rows

def frame_dir(given: str | None = None) -> str | None:
    """Where her frames are: the caller's choice, the owner's home, else the checkout's own.

    Precedence is the pane's (a flag, then the environment, then the home directory, then what
    shipped), and `None` means this machine has no frames at all — which is a case the pane
    still has to draw, and does, with the character it always had.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    seen = [given or "", os.path.join(os.path.expanduser("~"), ".config", *BUFFY_HOME_DIR),
            os.path.join(root, "assets", "buffy")]
    env = os.environ.get(BUFFY_DIR_ENV)
    if env:
        seen.insert(0, env)
    for path in seen:
        if path and os.path.isdir(path):
            return path
    return None


def frames(given: str | None = None) -> list:
    """Her frames, in order: the PNGs in the frame directory, by name.

    Named so that the order is the artist's (`01.png` first) rather than the filesystem's,
    because the order is the animation and an animation is not a directory listing.
    """
    where = frame_dir(given)
    if not where:
        return []
    try:
        names = sorted(n for n in os.listdir(where) if n.lower().endswith(".png"))
    except OSError:
        return []
    return [os.path.join(where, n) for n in names]


# How much of her the picture shows: the whole character at `1.0`, which is the default, and a
# window on her HEAD at `2.0`, for a pane that wants the face. The whole of her is the default
# because the closer window was measured and lost - at every panel height the pane will reserve,
# a head that fills the panel is mostly FLAT colour, and a flat area has no detail for a bigger
# window to reveal, so the zoom traded the ahoge, the hood and the spark for bands of skin and
# onesie (checked as pictures at 20x10 cells, 2026-10-07, and again at 24x12 and 32x16). The
# window hangs at `BUFFY_FACE_AT` of the way down her content box, and takes her feet rather than
# her face when it must. The knob is the owner's either way.
BUFFY_ZOOM_ENV = "FBTODO_BUFFY_ZOOM"
BUFFY_ZOOM_DEFAULT = 1.0
BUFFY_ZOOM_MAX = 4.0
# How far down her content box her FACE sits, for the zoom window's anchor: these are chibis, so
# the head is a big share of the whole and the ahoge is above it. Only used at a zoom above 1.
BUFFY_FACE_AT = 0.42
# The alpha at or above which a pixel says where she IS, for the content box alone: looser than
# `BUFFY_ALPHA_MIN`, because the box wants the whole of her soft edge and the dots want only the
# part of it that is really her.
BUFFY_BOX_ALPHA = 24


def buffy_zoom() -> float:
    """How far the picture is zoomed in on her: `FBTODO_BUFFY_ZOOM`, defaulted and clamped.

    A number out of range or not a number at all is the default rather than an error, for the
    same reason the style's typo is: a knob is not allowed to be the reason a pane draws nothing.
    The default is the whole character, and that is a measurement rather than a taste: see the
    note on `BUFFY_ZOOM_DEFAULT`.
    """
    try:
        want = float((os.environ.get(BUFFY_ZOOM_ENV) or "").strip() or BUFFY_ZOOM_DEFAULT)
    except ValueError:
        return BUFFY_ZOOM_DEFAULT
    return want if 1.0 <= want <= BUFFY_ZOOM_MAX else BUFFY_ZOOM_DEFAULT


def content_box(img: tuple) -> tuple | None:
    """Where she actually IS: the bounding box of the pixels the art covers, or `None`.

    The stickers are square canvases and she is not, so the canvas is no guide to the character —
    and it is the character the panel is spending its pixels on.
    """
    width, height, data = img
    lo_x, hi_x, lo_y, hi_y = width, -1, height, -1
    for y in range(height):
        row = y * width * 4
        for x in range(width):
            if data[row + x * 4 + 3] >= BUFFY_BOX_ALPHA:
                lo_x = min(lo_x, x)
                hi_x = max(hi_x, x)
                lo_y = min(lo_y, y)
                hi_y = max(hi_y, y)
    return None if hi_x < lo_x else (lo_x, hi_x + 1, lo_y, hi_y + 1)


def zoomed(img: tuple, zoom: float, face_at: float = BUFFY_FACE_AT) -> tuple:
    """The square window `zoom` times closer on her FACE, as a new image.

    Centred horizontally on her and hung at `face_at` of the way down her content box, because
    that is where a chibi's face is: her content is mostly ahoge and hair above it and body
    below, so a window hung from the TOP (the first rule here) zoomed past her face and showed
    a head of hair and a white onesie instead — checked as a picture, 2026-10-07. At `1.0` (or
    with nothing in the frame to hang a window on) this is the image it was given.
    """
    box = content_box(img) if zoom > 1.0 else None
    if not box:
        return img
    width, height, data = img
    lo_x, hi_x, lo_y, hi_y = box
    side = int(round(max(hi_x - lo_x, hi_y - lo_y) / zoom))
    side = max(1, min(side, width, height))
    x0 = int(min(max((lo_x + hi_x) / 2.0 - side / 2.0, 0), width - side))
    y0 = int(min(max(lo_y + (hi_y - lo_y) * face_at - side / 2.0, 0), height - side))
    out = bytearray()
    for y in range(y0, y0 + side):
        run = (y * width + x0) * 4
        out += data[run:run + side * 4]
    return (side, side, out)


_CELLS: dict = {}
_CELLS_MAX = 64


def frame_cells(path: str, cols: int, rows: int, kind: str = "blocks",
                zoom: float = 1.0) -> list | None:
    """`cells()`/`braille_cells()` for one frame, remembered. The kind and the zoom are part of
    the key: the two grids have the same shape, so a shared key would hand a braille row a
    half-block grid, and the same frame at two zooms is two different pictures. A broken frame is
    remembered as broken: the pane repaints once a second and an unreadable file must not cost a
    decode attempt every one."""
    key = (path, cols, rows, kind, round(float(zoom), 3))
    if key in _CELLS:
        return _CELLS[key]
    try:
        picture = zoomed(read_png(path), float(zoom))
        grid = (braille_cells if kind == "braille" else cells)(picture, cols, rows)
    except (OSError, ValueError, zlib.error):
        grid = None
    if len(_CELLS) >= _CELLS_MAX:
        _CELLS.clear()
    _CELLS[key] = grid
    return grid


def panel(rows: int, paint, clock_ms: int = 0, tick_ms: int = 900,
          given: str | None = None, cols: int | None = None,
          style: str | None = None, zoom: float | None = None) -> list | None:
    """Her picture in a `rows`-tall panel: the frame the clock is on, or `None` for no frames.

    The width follows the height (`cols = 2 * rows`) so the pixels stay square and the drawing
    keeps its proportions — a portrait stretched to fill a panel is a different character.

    Which frame is showing is a function of the clock and nothing else: `clock_ms // tick_ms`
    walks the frames and wraps, so two panes at the same instant show the same pose, a repaint
    that changed nothing changes no pixels, and she keeps no timer of her own. A frame that
    cannot be read falls forward to the next one rather than to a hole in the pane.

    `zoom` is how much of her the panel spends its pixels on (see `zoomed`): the whole character
    or her head. Left out, it is `buffy_zoom()` — the pane's own choice, from `FBTODO_BUFFY_ZOOM`.
    """
    if rows < 1:
        return None
    paths = frames(given)
    if not paths:
        return None
    cols = cols if cols else 2 * rows
    kind = buffy_style(style)
    close = buffy_zoom() if zoom is None else float(zoom)
    step = max(0, int(clock_ms)) // max(1, int(tick_ms))
    for i in range(len(paths)):
        grid = frame_cells(paths[(step + i) % len(paths)], cols, rows, kind, close)
        if grid is not None:
            return (paint_braille if kind == "braille" else paint_cells)(grid, paint)
    return None
