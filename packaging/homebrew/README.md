# Homebrew

`Formula/fbtodo.rb` is the Homebrew formula for fbtodo. For the end-to-end
setup — PyPI project, tap, token, first release — see
[`docs/RELEASING.md`](../../docs/RELEASING.md).

Homebrew only looks for a
formula in a **tap** — a repository named `homebrew-<something>` — so this file is
the tap's copy, kept here so the release can rewrite it and push it (see
[`.github/workflows/publish.yml`](../../.github/workflows/publish.yml)).

## The tap

The tap is `TLE47/homebrew-tap`: a repository containing `Formula/fbtodo.rb` and
nothing else that matters. Once it exists and holds this formula:

```sh
brew install TLE47/tap/fbtodo     # brew taps it for you on the first install
brew install fbtodo               # ...and this works afterwards
brew upgrade fbtodo
```

`brew install fbtodo` *without* the tap name only works from **homebrew-core**,
which is a separate submission to [Homebrew/homebrew-core](https://github.com/Homebrew/homebrew-core)
rather than something a repository can arrange for itself. The formula here is
written to that house style (`desc`, `homepage`, `url`, `sha256`, `license`,
`head`, a `test do` block) so that a core pull request is mostly a copy — but the
tap is what makes it installable today. See *Submitting to homebrew-core* below.

## Filling in `url` and `sha256`

Homebrew verifies the sha256 of the exact bytes it downloads, and the source
distribution's bytes are fixed only once PyPI publishes it — a tarball built in
this checkout and one built on a runner are different files. So the two values
are written **after** the upload, from the release PyPI actually has:

```sh
# inside the tap, or here, before copying:
python3 packaging/homebrew/update-formula.py --version 4.30.0
```

It reads `https://pypi.org/pypi/fbtodo/<version>/json`, takes the `sdist` entry,
and rewrites the `url` and `sha256` lines in place (retrying for a couple of
minutes, because the JSON index can lag the upload). The publish workflow runs it
for you and pushes the result to the tap whenever a `HOMEBREW_TAP_TOKEN` secret
is configured; without the secret that job skips and the tap is left alone.

## Testing locally

```sh
brew install --build-from-source ./packaging/homebrew/Formula/fbtodo.rb
brew test fbtodo
brew audit --strict --new ./packaging/homebrew/Formula/fbtodo.rb
```

## Submitting to homebrew-core

Core wants a tool that a maintainer would be expected to keep alive, and it
builds from the same PyPI sdist. The pieces are already in place: no Python
dependencies, so no `resource` blocks to track; `depends_on "python@3.13"`; and
the `test do` block asserts the version. A pull request to
`Homebrew/homebrew-core` adds `Formula/f/fbtodo.rb` with the same body and a
`livecheck` once it is in.
