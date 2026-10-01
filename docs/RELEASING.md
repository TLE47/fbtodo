# Publishing fbtodo (maintainers)

Everything here is done once, by hand, in a browser — except the release itself,
which is one command. It takes about twenty minutes, and at the end
`uvx fbtodo`, `pipx install fbtodo`, and `brew install TLE47/tap/fbtodo` all
work, and every later release is just `python3 scripts/release.py X.Y.Z --push`.

The order matters. PyPI must be configured **before** the first tag is pushed,
because a tag with no trusted publisher fails in the middle of a release.

Nothing here needs `gh`, and nothing here needs a PyPI password stored anywhere:
PyPI trusts the GitHub workflow itself, and the one token that does exist is a
GitHub token that only the repository's Actions can read.

---

## 0. What you need before starting

- You can sign in to GitHub as the owner of `TLE47/fbtodo`.
- A PyPI account. If you do not have one, make it now:
  <https://pypi.org/account/register/> — then confirm the email from PyPI, or the
  upload in step 4 is rejected.
- The checkout, on `main`, with the packaging work committed (step 3).

---

## 1. PyPI — let the workflow publish, and create the project

The project does not exist on PyPI yet, so this uses a **pending publisher**: you
name the project before it exists, and the first successful upload creates it.
PyPI converts the pending publisher into a normal one at that moment, so it is
never configured again.

1. Sign in at <https://pypi.org/account/login/>.
2. Go to <https://pypi.org/manage/account/publishing/>.
3. Scroll to **Add a new pending publisher** and fill in exactly these values —
   they are matched against the workflow's identity, so a typo here means
   `invalid-pending-publisher` in step 4:

   | Field | Value |
   |---|---|
   | PyPI Project Name | `fbtodo` |
   | Owner | `TLE47` |
   | Repository name | `fbtodo` |
   | Workflow name | `publish.yml` |
   | Environment name | `pypi` |

   The last two are the ones easy to get wrong: **`publish.yml`** is the file's
   name including the extension, not a path, and **`pypi`** is the environment
   the workflow's `pypi` job declares. If the environment field is left blank
   while the job declares an environment, publishing fails.
4. Click **Add**. PyPI shows it under "Pending publishers" — it is not a project
   yet, and it does not reserve the name.

> The name is only claimed on the first real upload. If someone else registers
> `fbtodo` on PyPI before then, the pending publisher is invalidated and you have
> to pick a different distribution name. Nothing else on this page changes.

---

## 2. GitHub — the tap repository

Homebrew only finds a formula in a repository named `homebrew-<something>`. Ours
is `TLE47/homebrew-tap`, and it holds nothing but the formula.

1. Create it: <https://github.com/new>
   - **Owner**: `TLE47`
   - **Repository name**: `homebrew-tap` (exactly — Homebrew strips the
     `homebrew-` prefix, giving the tap name `TLE47/tap`)
   - **Visibility**: Public
   - Tick **Add a README file** — the repository needs one commit, or the
     release's `git clone` has no `main` branch to push to.
2. Click **Create repository**.

That is all. Do **not** add `Formula/fbtodo.rb` by hand yet: the version in this
checkout still carries the placeholder `sha256` of sixty-four zeros, and a
formula whose checksum does not match its download fails to install. The first
release writes the real `url` and `sha256` and pushes the file here (step 5).

---

## 3. GitHub — a token so the release can update the tap

PyPI is handled by OIDC and stores no secret. The tap is not, so it needs one
token — scoped to the tap alone.

**Make the token**

1. Go to <https://github.com/settings/personal-access-tokens/new>
   (this is a *fine-grained* token; the classic page will not do — it cannot be
   scoped to one repository's contents).
2. Fill in:
   - **Token name**: `fbtodo tap` (anything memorable)
   - **Expiration**: `1 year`, or Custom if you would rather it not lapse
   - **Resource owner**: `TLE47`
   - **Repository access**: *Only select repositories* → `TLE47/homebrew-tap`
3. Under **Permissions → Repository permissions**, find **Contents** and set it
   to **Read and write**. Nothing else is required; leave the rest as *No
   access*.
4. Click **Generate token** and copy it — GitHub shows it once.

**Store it on the fbtodo repository**

5. Go to <https://github.com/TLE47/fbtodo/settings/secrets/actions/new>
6. **Name**: `HOMEBREW_TAP_TOKEN` (exactly — the workflow tests for this name)
7. **Secret**: paste the token. Leave the environment field alone.
8. Click **Add secret**.

> If you skip this, everything still works: the release publishes to PyPI and
> the tap job reports that it skipped. The formula is then updated by hand — see
> *If the tap job skipped* below.

---

## 4. Commit the packaging work, then cut the release

The release script refuses to run on a dirty tree, so commit first. In the
checkout:

```sh
git status --porcelain          # whatever is yours to commit
git add install.sh packaging .github/workflows/publish.yml scripts/release.py scripts/preflight.py docs/INSTALL.md docs/RELEASING.md CHANGELOG.md README.md pyproject.toml
git commit -m "Add PyPI, Homebrew, and one-line install routes"
git push origin main
```

Then pick the next version. `VERSION` is `4.29.0` and tag `4.29.0` already
exists, so the first published release has to be a new number — `4.30.0`.

Before pushing anything, check the release can actually succeed. A tag push
publishes to PyPI and PyPI never accepts the same version twice, so a tag pushed
by mistake does not cost a retry — it costs a version number:

```sh
python3 scripts/preflight.py --version 4.30.0
```

It checks the local side (clean tree, right branch, the tag is still free, an
`## Unreleased` section to roll), PyPI (whether that version is already gone,
whether the project exists), and the tap repository you just created — and it
distinguishes a real problem from a thing it simply cannot see without GitHub
credentials. A `note` is not a failure. `FAIL` is.

```sh
python3 scripts/release.py 4.30.0 --dry-run     # prints the plan, writes nothing
python3 scripts/release.py 4.30.0 --push
```

`--push` pushes the commit and then the tag. Pushing the tag is what starts
`.github/workflows/publish.yml`. Without `--push` you get the commit and the tag
locally, and the workflow stays dormant until you push them:

```sh
git push origin main && git push origin 4.30.0
```

---

## 5. Watch it land

1. Open <https://github.com/TLE47/fbtodo/actions> and open the `publish` run.
   Four things happen in order:
   - **build** — refuses a tag that disagrees with `VERSION`, builds the sdist
     and wheel, installs the wheel, and runs `fbtodo doctor` against it.
   - **publish to PyPI** — uploads via OIDC. This is the step that creates the
     `fbtodo` project. A permission prompt here means the environment `pypi`
     has required reviewers; approve it.
   - **update the Homebrew tap** — waits fifteen seconds for PyPI's index to
     catch up, reads the published sdist's `url` and `sha256`, and pushes
     `Formula/fbtodo.rb` to `TLE47/homebrew-tap`.
2. Confirm the project page exists: <https://pypi.org/p/fbtodo/>
3. Confirm the tap got the formula:
   <https://github.com/TLE47/homebrew-tap/blob/main/Formula/fbtodo.rb> — the
   `sha256` must no longer be all zeros, and the `url` must end in
   `fbtodo-4.30.0.tar.gz`.

Then, on any machine:

```sh
uvx fbtodo --version                                  # straight from PyPI
brew install TLE47/tap/fbtodo && brew test fbtodo     # the tap, tapped for you
```

After that first install the tap is known to that machine, so plain
`brew install fbtodo` and `brew upgrade fbtodo` work there too. A machine that
has never seen the tap still needs the `TLE47/tap/` prefix — see step 6.

---

## If the tap job skipped

Without `HOMEBREW_TAP_TOKEN` the workflow leaves the tap alone. Fill the formula
from your machine instead. The values must come from PyPI, not from a tarball
built locally: a same-source tarball built in this checkout is not
byte-identical to the one a runner built, and Homebrew checks the exact bytes.

```sh
python3 packaging/homebrew/update-formula.py --version 4.30.0
```

It reads `https://pypi.org/pypi/fbtodo/4.30.0/json`, rewrites the `url` and
`sha256` lines in place, and retries for a couple of minutes if the index is
lagging. Then copy the result into the tap:

```sh
git clone https://github.com/TLE47/homebrew-tap /tmp/homebrew-tap
mkdir -p /tmp/homebrew-tap/Formula
cp packaging/homebrew/Formula/fbtodo.rb /tmp/homebrew-tap/Formula/fbtodo.rb
cd /tmp/homebrew-tap && git add Formula/fbtodo.rb && git commit -m "fbtodo 4.30.0" && git push
```

Test it before trusting it:

```sh
brew install TLE47/tap/fbtodo
brew test fbtodo
brew audit --strict --new /tmp/homebrew-tap/Formula/fbtodo.rb
```

---

## Later releases

Nothing above is repeated. A release is:

```sh
# edit CHANGELOG.md: write the release notes under '## Unreleased'
python3 scripts/preflight.py --version 4.31.0 --offline   # fast local sanity check
python3 scripts/release.py 4.31.0 --push
```

The script bumps `VERSION` in `src/fbtodo/base.py` (the single source of truth
the build and the tag both read), rolls `## Unreleased` into `## 4.31.0`, builds,
commits, tags, and pushes. The workflow republishes to PyPI and rewrites the tap
formula for the new version.

---

## When something goes wrong

| Symptom | Cause |
|---|---|
| `invalid-pending-publisher` | A field in step 1 does not match the workflow. Most often the workflow name (`publish.yml`), the environment (`pypi`), or the owner/repository casing. |
| `Non-user identities cannot create new projects` | The project name in the pending publisher and the name in `pyproject.toml` differ. Both must be exactly `fbtodo`. |
| `400 File already exists` | That version is already on PyPI — PyPI never allows re-uploading a version. Yank it or bump. |
| `tag X does not match VERSION Y` | The build job caught it. The tag name and `src/fbtodo/base.py`'s `VERSION` must be the same string, with no leading `v`. |
| The tap job says it skipped | `HOMEBREW_TAP_TOKEN` is unset — step 3. Use the by-hand path above. |
| `brew` reports a checksum mismatch | The formula was filled from a tarball built locally rather than from PyPI. Re-run `update-formula.py` and commit the new `sha256`. |
| `brew install TLE47/tap/fbtodo` says the formula is not found | The file is not at `Formula/fbtodo.rb` on the tap's default branch, or the repository is not named `homebrew-*`. |
| Any of it is unclear | `python3 scripts/preflight.py --version <new version>` says which of these is true, and which of them it cannot see from here. |

## 6. Optional — making bare `brew install fbtodo` work

`brew install fbtodo`, with no tap name, only resolves from **homebrew-core**.
That is a pull request to [Homebrew/homebrew-core](https://github.com/Homebrew/homebrew-core),
not something this repository can arrange. The formula is already written to
that house style — no `resource` blocks, because fbtodo has no Python
dependencies, `depends_on "python@3.13"`, and a `test do` block — so the
submission is mostly a copy into `Formula/f/fbtodo.rb`, plus a `livecheck` block
pointing at PyPI. Core maintainers generally expect a project to have some
users and a few releases behind it first, so treat this as a later step, not a
launch step. Until then, `brew install TLE47/tap/fbtodo` is the Homebrew route.
