#!/usr/bin/env python3
"""Say whether a release can actually succeed, before a tag is pushed.

A tag push publishes to PyPI, and PyPI never accepts the same version twice —
so a tag that is pushed by mistake does not cost a retry, it costs a **version
number**. Everything that can be wrong about a release is checkable *before*
that push, and almost none of it is checkable *after*: a missing PyPI publisher,
a tap repository that was never created, a token that was never stored, a
version that is already gone. Each of those fails somewhere different and some
of them fail *quietly* (the tap job reports a skip and the release still looks
green), which is exactly the shape of a mistake worth catching here.

    python3 scripts/preflight.py                    # the VERSION in the source
    python3 scripts/preflight.py --version 4.30.0   # the release about to be cut
    python3 scripts/preflight.py --offline          # local checks only, no network

FAIL is something that will break the release. WARN is something that will make
it incomplete or need a manual follow-up. NOTE is information, including the
things that are simply not checkable from a machine with no GitHub credentials.
Exit is 0 when nothing FAILed, so it can gate a script:

    python3 scripts/preflight.py && python3 scripts/release.py 4.30.0 --push
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_PY = ROOT / "src" / "fbtodo" / "base.py"
CHANGELOG = ROOT / "CHANGELOG.md"
FORMULA = ROOT / "packaging" / "homebrew" / "Formula" / "fbtodo.rb"
WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"

VERSION_RE = re.compile(r'^VERSION = "([^"]+)"', re.M)
VERSION_FORMAT = re.compile(r"^\d+\.\d+\.\d+$")
UNRELEASED_RE = re.compile(r"^## Unreleased[ \t]*$", re.M)
SHA_RE = re.compile(r'^\s*sha256 "([^"]+)"', re.M)

OK, NOTE, WARN, FAIL = "ok", "note", "warn", "FAIL"

# Ordered by severity so the summary can lead with the worst thing found.
RANK = {OK: 0, NOTE: 1, WARN: 2, FAIL: 3}


class Report:
    def __init__(self, offline: bool = False) -> None:
        self.offline = offline
        self.rows: list[tuple[str, str, str]] = []

    def add(self, status: str, message: str, hint: str = "") -> None:
        self.rows.append((status, message, hint))

    def http(self, url: str, timeout: int = 20) -> tuple[int, object]:
        """(status, parsed-json-or-None). A 404 is a *result* here, not an error.

        Returns (-1, None) for anything that is not an HTTP answer — offline, DNS,
        TLS, a timeout. Those must never read as a FAIL: "the network is down" and
        "you forgot to create the tap repository" are different problems and only
        one of them is fixed by clicking something.
        """
        if self.offline:
            return -1, None
        request = urllib.request.Request(url, headers={"Accept": "application/json",
                                                       "User-Agent": "fbtodo-preflight"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as fh:
                body = fh.read().decode("utf-8", "replace")
                status = fh.status
        except urllib.error.HTTPError as exc:
            return exc.code, None
        except (urllib.error.URLError, TimeoutError, OSError):
            return -1, None
        try:
            return status, json.loads(body)
        except json.JSONDecodeError:
            return status, None

    def git(self, *args: str, check: bool = False) -> str:
        proc = subprocess.run(["git", *args], cwd=ROOT, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if proc.returncode != 0 and check:
            return ""
        return (proc.stdout or "").strip()

    @property
    def worst(self) -> str:
        return max((row[0] for row in self.rows), key=lambda s: RANK[s], default=OK)


def source_version() -> str:
    match = VERSION_RE.search(BASE_PY.read_text(encoding="utf-8"))
    return match.group(1) if match else ""


def check_local(rep: Report, version: str, branch: str, valid: bool) -> None:
    # The pieces the tag will need to exist at all. A missing workflow is not a
    # broken release, it is a release that publishes nothing — which looks like
    # success from the Actions page.
    if not WORKFLOW.is_file():
        rep.add(FAIL, f"{WORKFLOW.relative_to(ROOT)} is missing",
                "without it a tag push publishes nothing")
    else:
        text = WORKFLOW.read_text(encoding="utf-8")
        # The four strings PyPI matches the workflow's identity against. The
        # environment is the one that bites: a job declaring an environment the
        # publisher does not name fails with invalid-pending-publisher.
        if "id-token: write" not in text:
            rep.add(FAIL, "the workflow never asks for id-token: write",
                    "trusted publishing needs that permission to mint a token")
        elif "name: pypi" not in text:
            rep.add(WARN, "the workflow declares no 'pypi' environment",
                    "if PyPI's publisher names one, publishing will be refused")
        else:
            rep.add(OK, "publish.yml is wired for trusted publishing (pypi, id-token: write)")

    if not valid:
        # Worth continuing rather than bailing: the branch, the tree and the
        # changelog are all independent of the version, and a report that hides
        # them behind one bad string sends you round the loop twice.
        rep.add(FAIL, f"VERSION in base.py is {version!r}, not X.Y.Z")
    else:
        rep.add(OK, f"version {version} reads as X.Y.Z")

    current = rep.git("rev-parse", "--abbrev-ref", "HEAD")
    if current != branch:
        rep.add(FAIL, f"on branch {current!r}, not {branch!r}",
                "release.py refuses to tag off the release branch")
    else:
        rep.add(OK, f"on branch {branch}")

    if not rep.git("status", "--porcelain"):
        rep.add(OK, "the tree is clean")
    else:
        # Not a FAIL: the changes are usually the very work being released, and
        # they are one `git commit` away from being fine.
        rep.add(WARN, "the tree has uncommitted changes",
                "commit them first — release.py refuses a dirty tree")

    if not UNRELEASED_RE.search(CHANGELOG.read_text(encoding="utf-8")):
        rep.add(FAIL, "CHANGELOG.md has no '## Unreleased' section",
                "release.py rolls that heading into the version heading")
    else:
        rep.add(OK, "CHANGELOG.md has an '## Unreleased' section to roll")

    # The tag is the version, so an existing tag of that name is a collision.
    if not valid:
        rep.add(NOTE, "not checked: the tag's availability (the version is not X.Y.Z)")
    elif rep.git("tag", "--list", version):
        rep.add(FAIL, f"tag {version} already exists locally")
    elif rep.git("ls-remote", "--tags", "origin", version):
        rep.add(FAIL, f"tag {version} already exists on origin")
    elif rep.offline:
        rep.add(OK, f"tag {version} is free locally")
        rep.add(NOTE, f"not checked: whether tag {version} exists on origin")
    else:
        rep.add(OK, f"tag {version} is free locally and on origin")

    origin = rep.git("config", "--get", "remote.origin.url")
    if "TLE47/fbtodo" in origin:
        rep.add(OK, f"origin is {origin}")
    elif origin:
        # Worth a hard stop: PyPI's publisher is matched to an owner/repository
        # pair, so publishing from a fork of the same checkout fails.
        rep.add(WARN, f"origin is {origin}, not TLE47/fbtodo",
                "the PyPI publisher is configured for TLE47/fbtodo")
    else:
        rep.add(NOTE, "origin is not set — cannot check the remote tag or push")

    if FORMULA.is_file():
        match = SHA_RE.search(FORMULA.read_text(encoding="utf-8"))
        sha = match.group(1) if match else ""
        if not match:
            rep.add(WARN, f"no sha256 line in {FORMULA.name}")
        elif set(sha) == {"0"}:
            # Expected before a release, and harmless *if* the tap job runs: it
            # rewrites these two lines from the published sdist. If the token is
            # missing instead, this placeholder is what gets pushed by hand.
            rep.add(NOTE, "the formula still has the placeholder sha256",
                    "the release's tap job fills it from PyPI; without "
                    "HOMEBREW_TAP_TOKEN, run update-formula.py by hand")
        else:
            rep.add(OK, f"the formula carries a real sha256 ({sha[:12]}…)")
    else:
        rep.add(FAIL, f"{FORMULA.relative_to(ROOT)} is missing")


def check_pypi(rep: Report, name: str, version: str, valid: bool) -> None:
    # The versioned endpoint, deliberately: `/pypi/{name}/json` answers with the
    # latest release no matter which version was asked for, which is how a
    # formula ends up pointing at the wrong tarball.
    if not valid:
        rep.add(NOTE, "not checked: whether this version is already on PyPI")
    else:
        status, data = rep.http(f"https://pypi.org/pypi/{name}/{version}/json")
        if status == -1:
            rep.add(NOTE, f"not checked: whether {name} {version} is already on PyPI")
        elif status == 200:
            # The one check that cannot be recovered from: PyPI rejects a re-upload
            # of an existing filename permanently, so this version is spent.
            rep.add(FAIL, f"{name} {version} is already on PyPI",
                    "PyPI never accepts a filename twice — pick a new version")
        else:
            rep.add(OK, f"{name} {version} is not on PyPI yet")

    status, data = rep.http(f"https://pypi.org/pypi/{name}/json")
    if status == -1:
        rep.add(NOTE, f"not checked: whether {name} exists on PyPI")
    elif status == 200:
        latest = (data or {}).get("info", {}).get("version", "?")
        rep.add(OK, f"the PyPI project exists (latest is {latest})")
    else:
        # Not a problem at all — it is the whole point of a pending publisher,
        # which creates the project on the first successful upload. Only the
        # fact that it is *pending* and unverifiable from here matters.
        rep.add(NOTE, f"{name} does not exist on PyPI yet",
                "a pending publisher is what creates it — confirm it is in "
                "https://pypi.org/manage/account/publishing/")


def check_github(rep: Report, tap: str) -> None:
    """The tap repository, via the API so both its existence and its default branch are known."""
    # Emitted first and unconditionally: this is the one thing about the release
    # that nothing here can see, and a check that silently disappears whenever an
    # earlier check fails is worse than no check at all.
    rep.add(NOTE, "HOMEBREW_TAP_TOKEN cannot be checked without GitHub credentials",
            f"confirm it exists at https://github.com/{rep.repo}/settings/secrets/actions "
            "— if it is missing the tap job skips and the release still looks green")

    status, data = rep.http(f"https://api.github.com/repos/{tap}")
    if status == -1:
        # A rate-limited (403) or absent answer is not evidence of anything.
        rep.add(NOTE, f"not checked: whether {tap} exists")
        return
    if status != 200:
        rep.add(WARN, f"{tap} does not exist",
                "create it (with a README, so it has a branch) or the release's "
                "tap job fails where it clones it")
        return
    default = (data or {}).get("default_branch", "main")
    rep.add(OK, f"{tap} exists (default branch {default})")

    # An empty repository clones to an unborn branch, and the job's `git push`
    # then fails with nothing useful said. One commit — a README — is enough.
    status, commits = rep.http(f"https://api.github.com/repos/{tap}/commits?per_page=1")
    if status == -1:
        rep.add(NOTE, f"not checked: whether {tap} has any commits")
    elif status == 409 or (status == 200 and not commits):
        rep.add(FAIL, f"{tap} has no commits",
                "the release clones it and pushes — add a README so it has a branch")
    elif status == 200:
        rep.add(OK, f"{tap} has at least one commit on {default}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="check a fbtodo release before pushing its tag")
    ap.add_argument("--version", default=None, help="the release about to be cut (default: base.py's VERSION)")
    ap.add_argument("--name", default="fbtodo", help="the PyPI project name (default: fbtodo)")
    ap.add_argument("--repo", default="TLE47/fbtodo", help="the GitHub repository (default: TLE47/fbtodo)")
    ap.add_argument("--tap", default="TLE47/homebrew-tap", help="the Homebrew tap (default: TLE47/homebrew-tap)")
    ap.add_argument("--branch", default="main", help="the branch to release from (main)")
    ap.add_argument("--offline", action="store_true", help="local checks only")
    args = ap.parse_args(argv)

    version = args.version or source_version()
    valid = bool(VERSION_FORMAT.match(version or ""))
    rep = Report(offline=args.offline)
    rep.repo = args.repo

    check_local(rep, version, args.branch, valid)
    check_pypi(rep, args.name, version, valid)
    check_github(rep, args.tap)

    width = max(len(status) for status, _, _ in rep.rows)
    for status, message, hint in rep.rows:
        print(f"{status:>{width}}  {message}")
        if hint:
            print(f"{'':>{width}}  ↳ {hint}")

    failures = sum(1 for status, _, _ in rep.rows if status == FAIL)
    warnings = sum(1 for status, _, _ in rep.rows if status == WARN)
    print()
    if failures:
        print(f"{failures} FAIL, {warnings} warn — fix the FAILs before pushing a tag")
        return 1
    if warnings:
        print(f"no FAILs, {warnings} warn — a release can go ahead; read the warnings")
    else:
        print(f"ready: push the tag to release {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
