#!/usr/bin/env python3
"""Every mood's balloon and every mood's pose, on ONE page you can look at.

Her window is twenty stickers, one balloon shaped by the mood she is in and a table relating the two,
and a change to any of those three is a change you cannot review by reading the code: the pose is a
picture, the balloon is measured against a line that changes with the words, and which sticker a mood
wears is a row of a table. `fbtodo-pip --art` renders one of them, which is the right tool for one and
the wrong one for thirty-seven — this renders the lot and writes them into a single HTML file.

The page is SELF-CONTAINED: every picture is inlined as a data URI, because the file is meant to be
opened from anywhere — the Preview tab serves the single HTML file rather than the workspace around
it, and a sheet that needed its PNGs beside it would be a sheet nobody could send to anybody.

Per mood the sheet shows two cells, because the balloon and the pose are two things: the mood WITH a
line in it (the balloon, its shape and the words that fit it) and the mood alone (the pose the pane's
word means). Then the surprise, which is not a mood, and — unless `--no-frames` — the frames on disk,
so a sheet added to the art is a sheet you can see arrives.

    scripts/buffy-sheet.py --out ~/fb-moods.html         # every mood, then the frames
    scripts/buffy-sheet.py --no-frames --open            # just the moods, and open it
    scripts/buffy-sheet.py --out /tmp/s.html --json      # one machine-readable line

stdout is the path (or that JSON); everything else is progress on stderr, so `--out -` is not a thing
and neither is piping the page.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from fbtodo import BUFFY_CYCLE  # noqa: E402  — the pane's own vocabulary, so the sheet cannot invent one

# The line the balloons are measured against when nothing else is asked for: short enough to fit one
# line of any mood's cloud, long enough that the balloon is a balloon rather than a pill.
DEFAULT_LINE = "on it!"
# How big each cell is drawn, in points. 192 is the size she actually is on this machine, which is the
# size a look at a sheet is really about; 96 halves the file and keeps the shape recognisable.
DEFAULT_SIZE = 192


def pip_binary() -> str:
    """Where her window is: the same resolution the suite uses, so a sheet is of the build on this box."""
    return os.path.expanduser(os.environ.get("FBTODO_PIP_BIN")
                              or "~/.cache/fbtodo/bin/fbtodo-pip")


def frames_dir() -> str:
    """The frames on disk: the owner's home copy when there is one, and the checkout otherwise."""
    home = os.path.expanduser("~/.config/fbtodo/buffy")
    if os.path.isdir(home):
        return home
    for name in ("assets/buffy/pip", "assets/buffy"):
        path = os.path.join(ROOT, name)
        if os.path.isdir(path):
            return path
    return os.path.join(ROOT, "assets", "buffy")


def render(pip: str, mood: str, size: int, line: str | None, surprise: bool,
           out: str) -> dict:
    """One render, and the record line it prints: the mood, the frame she is wearing and the balloon's own
    geometry — which is the half of a sheet a picture cannot show."""
    cmd = [pip, "--art", out, "--art-size", str(size), "--art-mood", mood]
    if line:
        cmd += ["--art-say", line]
    if surprise:
        cmd.append("--art-surprise")
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if done.returncode != 0:
        raise SystemExit(f"buffy-sheet: {mood}: {done.stdout.strip()} {done.stderr.strip()}")
    record = {}
    for part in done.stdout.split():
        if "=" in part:
            key, _, value = part.partition("=")
            record[key] = value
    return record


def mood_rows(pip: str, env: dict) -> dict:
    """The mood table as her window runs it: frames and pace per mood, from `--moods`."""
    done = subprocess.run([pip, "--moods"], capture_output=True, text=True, env=env, timeout=60)
    if done.returncode != 0:
        raise SystemExit(f"buffy-sheet: --moods: {done.stdout.strip()} {done.stderr.strip()}")
    table = {}
    for line in done.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            table[parts[0]] = (parts[1], float(parts[2]))
    return table


def cell(path: str, caption: str, note: str) -> str:
    with open(path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode()
    return (f'<figure><img src="data:image/png;base64,{b64}" alt="{caption}">'
            f'<figcaption><b>{caption}</b><span>{note}</span></figcaption></figure>')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="buffy-sheet.py",
        description="render every mood's balloon and pose into one reviewable HTML sheet")
    parser.add_argument("--out", "-o", default=os.path.expanduser("~/fb-moods.html"),
                        help="where the page goes (default: ~/fb-moods.html)")
    parser.add_argument("--size", type=int, default=DEFAULT_SIZE,
                        help=f"how big each cell is drawn, in points (default: {DEFAULT_SIZE})")
    parser.add_argument("--line", default=DEFAULT_LINE,
                        help=f"the words the balloons are measured against (default: {DEFAULT_LINE!r})")
    parser.add_argument("--no-frames", action="store_true",
                        help="leave the frames on disk off the page")
    parser.add_argument("--no-surprise", action="store_true", help="leave the surprise off the page")
    parser.add_argument("--open", action="store_true", help="open the page when it is written")
    parser.add_argument("--json", action="store_true", help="one JSON object on stdout instead of the path")
    parser.add_argument("--quiet", "-q", action="store_true", help="no progress on stderr")
    args = parser.parse_args(argv)

    if args.size < 32:
        print("buffy-sheet: --size under 32 points is not a picture of her", file=sys.stderr)
        return 2
    pip = pip_binary()
    if not os.access(pip, os.X_OK):
        print(f"buffy-sheet: no window to draw ({pip}) — build it with: "
              f"swiftc -O scripts/buffy-pip.swift -o {pip}", file=sys.stderr)
        return 66                                   # EX_NOINPUT
    out = os.path.abspath(os.path.expanduser(args.out))

    def note(text: str) -> None:
        if not args.quiet:
            print(text, file=sys.stderr)

    env = dict(os.environ)
    table = mood_rows(pip, env)
    moods = sorted(set(BUFFY_CYCLE) & set(table)) or sorted(BUFFY_CYCLE)
    work = tempfile.mkdtemp(prefix="buffy-sheet-")
    cells, records = [], []
    try:
        for mood in moods:
            frames, pace = table.get(mood, ("?", 0.0))
            hold = "holds" if pace == 0 else f"{int(pace)}ms a pose"
            note(f"buffy-sheet: {mood}")
            with_line = render(pip, mood, args.size, args.line, False, os.path.join(work, f"{mood}.png"))
            alone = render(pip, mood, args.size, None, False, os.path.join(work, f"{mood}-pose.png"))
            cells.append(cell(os.path.join(work, f"{mood}.png"), mood,
                              f"frames {frames} · {hold} · balloon {with_line.get('balloon', '?')} "
                              f"· {with_line.get('shape', '?')}"))
            cells.append(cell(os.path.join(work, f"{mood}-pose.png"), f"{mood} (pose)",
                              f"frame {int(alone.get('frame', '0')) + 1}"))
            records.append(dict(mood=mood, frames=frames, pace=pace,
                                balloon=with_line.get("balloon"), shape=with_line.get("shape"),
                                pose=int(alone.get("frame", "0")) + 1))
        if not args.no_surprise:
            note("buffy-sheet: surprise")
            path = os.path.join(work, "surprise.png")
            record = render(pip, "done", args.size, args.line, True, path)
            cells.append(cell(path, "surprise", f"not a mood · balloon {record.get('balloon', '?')} "
                                                f"· pose {int(record.get('frame', '0')) + 1}"))
            records.append(dict(mood="surprise", frames="", pace=0.0,
                                balloon=record.get("balloon"), shape="surprise",
                                pose=int(record.get("frame", "0")) + 1))
        if not args.no_frames:
            where = frames_dir()
            names = sorted(n for n in os.listdir(where) if n.lower().endswith(".png"))
            note(f"buffy-sheet: {len(names)} frames from {where}")
            for name in names:
                cells.append(cell(os.path.join(where, name), name[:-4], "on disk"))
    finally:
        for name in os.listdir(work):
            os.unlink(os.path.join(work, name))
        os.rmdir(work)

    html = SHEET.format(title=f"buffy-chan — {len(moods)} moods",
                        count=len(cells), size=args.size, cells="".join(cells))
    try:
        # Atomic, in the target's own directory: a half-written page is not a page, and /tmp is a
        # different filesystem from wherever the owner keeps it.
        directory = os.path.dirname(out) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".sheet.", dir=directory)
        with os.fdopen(fd, "w") as fh:
            fh.write(html)
        os.replace(tmp, out)
    except OSError as exc:
        print(f"buffy-sheet: cannot write {out}: {exc}", file=sys.stderr)
        return 73                                   # EX_CANTCREAT
    if args.open:
        subprocess.run(["open", out], check=False)
    if args.json:
        print(json.dumps(dict(path=out, size=args.size, line=args.line, cells=len(cells),
                              moods=records), sort_keys=True))
    else:
        print(out)
    return 0


SHEET = """<!doctype html><meta charset="utf-8"><title>{title}</title>
<style>
 body{{background:#111418;color:#e6edf3;font:13px -apple-system,sans-serif;margin:12px}}
 h1{{font-size:15px;font-weight:600;margin:0 0 10px}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}}
 figure{{margin:0;background:#1b2027;border-radius:8px;padding:4px}}
 img{{width:100%;display:block;border-radius:5px}}
 figcaption{{padding-top:3px;font-size:11px;line-height:1.35;color:#8b949e;text-align:center}}
 figcaption b{{display:block;color:#e6edf3;font-size:12px}}
</style>
<h1>{title} · {count} cells at {size}pt</h1>
<div class="grid">{cells}</div>
"""


if __name__ == "__main__":
    sys.exit(main())
