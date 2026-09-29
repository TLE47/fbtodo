#!/usr/bin/env python3
"""Golden files for the three snapshot contracts: `json`, `bar` and the plain frame.

The pane's output IS the product, and every change to it was argued for in a commit
message and then never checked again. These files make a change to the display a DIFF:
the fixture state (state.json) is fed through the same functions the commands call —
`finish_state` -> `track_tasks` -> `json.dump` / `bar_text` / `render` — and the result is
compared byte for byte with what is on disk.

    python3 tests/golden.py            # check (what the suite runs)
    python3 tests/golden.py --write    # re-record, after reading the diff

Nothing here is allowed to depend on the clock, the machine or the terminal: time is
frozen before the module is imported, the state directory is a throwaway, the patch and
alert sources point at nothing, the locale is UTF-8 and the frame's width comes from
COLUMNS. A check that flakes is a check nobody trusts, so `--twice` runs the whole thing
twice and asserts the two runs agree.
"""

import argparse
import difflib
import json
import importlib
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GOLDEN = os.path.join(HERE, "golden")

# 2026-09-29T12:00:00Z. The fixture's own timestamps are anchored to it, so "the list was
# written 4m ago" in a golden file means exactly that, forever.
NOW_MS = 1790683200000
WIDTH = 68
HEIGHT = 18


def frozen_env(home: str) -> dict:
    """The environment the golden files were recorded in — and the only one they match.

    Built rather than inherited, and `HOME` included: a theme file, a notifier kit or a
    patch log in the recorder's own home would otherwise decide part of the output.
    """
    missing = os.path.join(home, "absent")
    env = dict(
        {k: v for k, v in os.environ.items()
         if k in ("PATH", "SHELL", "TMPDIR", "USER", "LOGNAME")},
        HOME=home,
        FBTODO_HOME=home,
        COLUMNS=str(WIDTH),
        LINES=str(HEIGHT),
        TERM="xterm-256color",
        FBTODO_TRUECOLOR="1",
        LANG="en_US.UTF-8",
        LC_ALL="en_US.UTF-8",
        # No patch row and no alert line: their ages would be relative to the frozen clock
        # anyway, but a real log on the recording machine must never leak into a golden.
        FBTODO_PATCH_LOG=missing,
        FBTODO_ALERT_LOG=missing,
        FBTODO_PATCH_META=missing,
    )
    env.pop("NO_COLOR", None)
    env.pop("CLICOLOR", None)
    return env


def load_module():
    """The program as a module: the package beside the launcher (`ROOT/src/fbtodo`)."""
    sys.path.insert(0, os.path.join(ROOT, "src"))
    for name in [n for n in sys.modules if n == "fbtodo" or n.startswith("fbtodo.")]:
        del sys.modules[name]
    return importlib.import_module("fbtodo")


def products(module, home: str, golden: str = GOLDEN) -> dict:
    """The contract texts, from the fixture state, exactly as the commands build them."""
    with open(os.path.join(golden, "state.json")) as fh:
        raw = json.load(fh)
    state = module.adopt_version(module.finish_state(dict(raw), None))
    state = module.track_tasks(state, now_ms=NOW_MS, persist=False)
    return {
        # `cmd_json` writes the state itself, with these two flags.
        "json.txt": json.dumps(state, ensure_ascii=False) + "\n",
        # `cmd_bar`.
        "bar.txt": module.bar_text(state) + "\n",
        # `cmd_snap` at a fixed width, with no colour: what `fbtodo snap` prints.
        "snap.txt": module.render(state, False, width=WIDTH, now_ms=NOW_MS) + "\n",
        # The pane's rich frame at a fixed size and 24-bit ink: the display contract.
        "frame.txt": module.render(state, True, width=WIDTH, height=HEIGHT, now_ms=NOW_MS) + "\n",
    }


def check(module, home: str, write: bool, golden: str = GOLDEN) -> int:
    made = products(module, home, golden)
    bad = 0
    for name, text in made.items():
        path = os.path.join(golden, name)
        old = None
        if os.path.exists(path):
            with open(path) as fh:
                old = fh.read()
        if write or old is None:
            with open(path, "w") as fh:
                fh.write(text)
            note = "wrote" if write else "created"
            print(f"{note} {os.path.relpath(path, ROOT)}")
            continue
        if old != text:
            bad += 1
            print(f"CHANGED {os.path.relpath(path, ROOT)}", file=sys.stderr)
            sys.stderr.writelines(
                difflib.unified_diff(
                    old.splitlines(True), text.splitlines(True),
                    fromfile=f"golden/{name}", tofile=f"{name} (now)",
                )
            )
        else:
            print(f"same    {os.path.relpath(path, ROOT)}")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true", help="re-record the golden files")
    ap.add_argument("--twice", action="store_true",
                    help="run twice and fail if the two runs differ (determinism)")
    ap.add_argument("--golden", default=GOLDEN,
                    help="where the fixture and the recorded files live (the suite points "
                         "this at a mutated copy to prove a change is actually caught)")
    args = ap.parse_args()
    golden = os.path.abspath(args.golden)

    home = tempfile.mkdtemp(prefix="fbtodo-golden-")
    try:
        # The clock, before the module that reads it is imported.
        time.time = lambda: NOW_MS / 1000.0
        os.environ.clear()
        os.environ.update(frozen_env(home))
        os.makedirs(home, mode=0o700, exist_ok=True)
        tasks = os.path.join(golden, "tasks.json")
        if os.path.exists(tasks):
            shutil.copy(tasks, os.path.join(home, "fbtodo-tasks.json"))
        module = load_module()
        first = products(module, home, golden)
        if args.twice:
            # A fresh process's worth of state: the module's caches are the only thing that
            # could differ between two calls, and a golden file must not depend on them.
            again = products(load_module(), home, golden)
            if again != first:
                for name in first:
                    if again[name] != first[name]:
                        print(f"NOT DETERMINISTIC: {name}", file=sys.stderr)
                return 1
            print("deterministic: two runs agree")
        return check(module, home, args.write, golden)
    finally:
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
