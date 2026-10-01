#!/usr/bin/env python3
"""Cut a release: bump the version, roll the changelog, build, commit, tag.

A release is the tag, and the tag is the version (see CHANGELOG.md), which is
why this does the whole mechanical part in one place rather than leaving three
files to be edited by hand and one of them forgotten:

    python3 scripts/release.py 4.30.0 --dry-run     # say what it would do
    python3 scripts/release.py 4.30.0               # do it, stop before pushing
    python3 scripts/release.py 4.30.0 --push        # ...and push the tag

Pushing the tag is what starts `.github/workflows/publish.yml`: it checks the
tag against `VERSION`, builds, publishes to PyPI (so `uvx fbtodo` and
`pipx install fbtodo` have something to fetch), and updates the Homebrew tap.
Nothing here pushes unless `--push` is given.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_PY = ROOT / "src" / "fbtodo" / "base.py"
CHANGELOG = ROOT / "CHANGELOG.md"

VERSION_RE = re.compile(r'^(?P<lead>VERSION = ")[^"]+(?P<tail>"\s*)$', re.M)
VERSION_FORMAT = re.compile(r"^\d+\.\d+\.\d+$")
UNRELEASED_RE = re.compile(r"^## Unreleased[ \t]*$", re.M)


def git(*args: str, capture: bool = True, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=ROOT, text=True,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.STDOUT if capture else None)
    if proc.returncode != 0 and check:
        sys.exit(f"release: git {' '.join(args)} failed:\n{proc.stdout or ''}".rstrip())
    return (proc.stdout or "").strip()


def die(message: str) -> "NoReturn":  # noqa: F821 - doc-only hint
    sys.exit(f"release: {message}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="cut a fbtodo release")
    ap.add_argument("version", help="the new version, e.g. 4.30.0")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, change nothing")
    ap.add_argument("--push", action="store_true", help="push the commit and the tag")
    ap.add_argument("--branch", default="main", help="the branch to release from (main)")
    ap.add_argument("--allow-dirty", action="store_true", help="skip the clean-tree check")
    args = ap.parse_args(argv)

    if not VERSION_FORMAT.match(args.version):
        die(f"{args.version!r} is not X.Y.Z")

    text = BASE_PY.read_text(encoding="utf-8")
    match = VERSION_RE.search(text)
    if not match:
        die(f"no VERSION line in {BASE_PY}")
    current = text[match.start("lead") + len(match.group("lead")):match.end("tail") - len(match.group("tail"))]
    if current == args.version:
        die(f"VERSION is already {args.version} — nothing to bump")

    # A guard, not a formality: the workflow publishes whatever tag is pushed,
    # and a tag that already exists would either fail there or, worse, be a
    # re-publish of a version PyPI will not accept.
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch != args.branch:
        die(f"on branch {branch!r}, expected {args.branch!r} (--branch to override)")
    if not args.allow_dirty and git("status", "--porcelain"):
        die("the tree is dirty — commit or stash first (--allow-dirty to override)")
    if git("tag", "--list", args.version):
        die(f"tag {args.version} already exists")
    # Not fatal when the remote is unreachable (a release cut offline still has
    # to fail on the local checks below): an empty answer and an error both mean
    # "no evidence it is already published".
    if git("ls-remote", "--tags", "origin", args.version, check=False):
        die(f"tag {args.version} already exists on origin")

    changed = CHANGELOG.read_text(encoding="utf-8")
    if not UNRELEASED_RE.search(changed):
        die(f"no '## Unreleased' section in {CHANGELOG}")

    print(f"release {args.version}   (was {current})")
    print(f"  edit  {BASE_PY.relative_to(ROOT)}            VERSION -> {args.version}")
    print(f"  edit  {CHANGELOG.relative_to(ROOT)}   '## Unreleased' rolls under a new '## {args.version}'")
    print(f"  build sdist + wheel")
    print(f"  commit 'Release {args.version}'   tag {args.version}")

    if args.dry_run:
        print("dry run — nothing written")
        return 0

    new_text = VERSION_RE.sub(lambda m: f'{m.group("lead")}{args.version}{m.group("tail")}', text, count=1)
    BASE_PY.write_text(new_text, encoding="utf-8")
    # A fresh empty Unreleased on top, the released section beneath it — the
    # file's own convention, so a later release is the same edit again.
    CHANGELOG.write_text(
        UNRELEASED_RE.sub(f"## Unreleased\n\n## {args.version}", changed, count=1),
        encoding="utf-8")

    # Prefer the project's own builder, fall back to the standard one, and never
    # fail the release because a builder is missing here — CI builds it again.
    if shutil.which("uv"):
        build = ["uv", "build"]
    else:
        build = [sys.executable, "-m", "build"]
    print("release: building")
    subprocess.run(build, cwd=ROOT, check=False)

    git("add", "src/fbtodo/base.py", "CHANGELOG.md")
    git("commit", "-m", f"Release {args.version}", capture=False)
    git("tag", args.version)

    print()
    print(f"tagged {args.version}.")
    if args.push:
        git("push", "origin", args.branch, capture=False)
        git("push", "origin", args.version, capture=False)
        print("pushed — .github/workflows/publish.yml will publish it")
    else:
        print("nothing pushed. When ready:")
        print(f"  git push origin {args.branch} && git push origin {args.version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
