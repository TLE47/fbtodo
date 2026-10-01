#!/usr/bin/env python3
"""Write a release's `url` and `sha256` into the Homebrew formula.

Homebrew checks the sha256 of the exact bytes it downloads, and a source
distribution's bytes are only fixed once PyPI has it — a tarball built here and
a tarball built on a runner are two different files. So the formula cannot be
hand-filled at packaging time: this reads the release that is *actually*
published and writes those two lines into `Formula/fbtodo.rb`.

    packaging/homebrew/update-formula.py                 # the VERSION in the source
    packaging/homebrew/update-formula.py --version 4.30.0
    packaging/homebrew/update-formula.py --formula /tmp/tap/Formula/fbtodo.rb

Exit codes are the program's own: 0 ok, 1 the help, 2 a usage error, 3 the
release is not on PyPI, 4 the formula did not look like one (so nothing was
written), 5 a network or JSON failure.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FORMULA = REPO_ROOT / "packaging" / "homebrew" / "Formula" / "fbtodo.rb"
BASE_PY = REPO_ROOT / "src" / "fbtodo" / "base.py"
# The *versioned* endpoint: `/pypi/{name}/json` answers with the latest release
# whatever version was asked for, which is how a formula ends up pointing at
# the wrong tarball.
PYPI_JSON = "https://pypi.org/pypi/{name}/{version}/json"

VERSION_RE = re.compile(r'^VERSION = "([^"]+)"', re.M)
URL_RE = re.compile(r'^(?P<lead>\s*url ")[^"]*(?P<tail>"\s*)$', re.M)
SHA_RE = re.compile(r'^(?P<lead>\s*sha256 ")[^"]*(?P<tail>"\s*)$', re.M)


def die(code: int, message: str) -> "NoReturn":  # noqa: F821 - doc-only hint
    print(f"update-formula: {message}", file=sys.stderr)
    raise SystemExit(code)


def source_version() -> str:
    """The version the checkout is on — the same single source of truth the build reads."""
    try:
        return VERSION_RE.search(BASE_PY.read_text(encoding="utf-8")).group(1)
    except (OSError, AttributeError) as exc:
        die(4, f"could not read VERSION from {BASE_PY}: {exc}")


def fetch_sdist(name: str, version: str, attempts: int = 12, pause: float = 10.0) -> tuple[str, str]:
    """The (url, sha256) of the sdist for a version, retrying while PyPI settles.

    Right after an upload the JSON index can 404 for a little while even though
    the files are served; a handful of retries turns that into a delay rather
    than a failed release.
    """
    last = ""
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(PYPI_JSON.format(name=name, version=version), timeout=30) as fh:
                data = json.load(fh)
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = str(exc)
        else:
            for entry in data.get("urls", []):
                if entry.get("packagetype") == "sdist":
                    return entry["url"], entry["digests"]["sha256"]
            last = f"{name} {version} has no source distribution on PyPI"
        if attempt < attempts:
            print(f"  waiting for PyPI ({last}) — {attempt}/{attempts}", file=sys.stderr)
            time.sleep(pause)
    die(3, f"could not read {name} {version} from PyPI: {last}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="fill a Homebrew formula from a PyPI release")
    ap.add_argument("--version", default=None,
                    help="the release to read (default: the VERSION in the source)")
    ap.add_argument("--name", default=None, help="the PyPI project name (default: fbtodo)")
    ap.add_argument("--formula", default=str(DEFAULT_FORMULA),
                    help=f"the formula to rewrite (default: {DEFAULT_FORMULA})")
    args = ap.parse_args(argv)

    version = args.version or source_version()
    name = args.name or "fbtodo"
    formula = Path(args.formula)

    try:
        text = formula.read_text(encoding="utf-8")
    except OSError as exc:
        die(4, f"could not read {formula}: {exc}")

    # Only touch a file that is the formula: rewriting a stray path with a
    # downloaded url would be a quiet way to mangle something.
    if "class Fbtodo < Formula" not in text or not URL_RE.search(text) or not SHA_RE.search(text):
        die(4, f"{formula} does not look like the fbtodo formula; nothing was written")

    url, sha = fetch_sdist(name, version)
    text = URL_RE.sub(lambda m: f'{m["lead"]}{url}{m["tail"]}', text, count=1)
    text = SHA_RE.sub(lambda m: f'{m["lead"]}{sha}{m["tail"]}', text, count=1)

    # The URL carries the version, so this also proves the release is the one
    # the tag promised rather than whatever else PyPI happened to return.
    if f"fbtodo-{version}.tar.gz" not in url:
        die(4, f"PyPI returned {url}, which is not fbtodo-{version}.tar.gz; nothing was written")

    formula.write_text(text, encoding="utf-8")
    print(f"{formula}: fbtodo {version}")
    print(f"  url    {url}")
    print(f"  sha256 {sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
