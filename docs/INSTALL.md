# Installing fbtodo

fbtodo is one command, `fbtodo`, that draws a todo pane beside your agent. It has
no Python dependencies — just **Python 3.9+** — and it needs **tmux** to show the
pane. Everything below puts the same command on your `PATH` in a different way;
pick whichever your machine already does.

- [One line](#one-line)
- [Homebrew](#homebrew)
- [uv or pipx](#uv-or-pipx)
- [From the checkout](#from-the-checkout)
- [Which one to pick](#which-one-to-pick)
- [Pinned versions](#pinned-versions)
- [Uninstalling](#uninstalling)
- [The notification kit](#the-notification-kit)
- [Releasing (maintainers)](#releasing-maintainers)

## One line

```sh
curl -fsSL https://raw.githubusercontent.com/TLE47/fbtodo/main/install.sh | sh
```

The script tries, in order, the first method your machine already has — Homebrew,
then `uv`, then `pipx`, then a plain venv, then a clone — and stops at the first
that works. It never runs as root, and it only writes under `$HOME/.local` (or
`$PREFIX`) and its clone directory. Nothing is published to PyPI as you read
this, so each method falls back to the git checkout automatically; the same
command keeps working once the package is live.

It takes flags, for the install that needs to be scripted:

```sh
sh install.sh --method clone --prefix ~/.local          # force one method
sh install.sh --version 4.30.0                          # a pinned release
curl -fsSL …/install.sh | sh -s -- --dry-run            # print, do nothing
sh install.sh --help                                    # every flag
```

The same knobs are environment variables (`FBTODO_METHOD`, `FBTODO_PREFIX`,
`FBTODO_DIR`, `FBTODO_VERSION`, `FBTODO_NO_TMUX`), so a scripted install needs no
arguments at all.

## Homebrew

```sh
brew install TLE47/tap/fbtodo     # brew taps it for you
```

After the first install the tap is known, so `brew install fbtodo` and
`brew upgrade fbtodo` work from then on. The formula builds the PyPI source
distribution into a private venv under Homebrew's prefix and links the console
script; `packaging/homebrew/README.md` explains the tap, the `sha256`, and the
path to homebrew-core (which is what a bare `brew install fbtodo` needs on a
machine that has never seen the tap).

## uv or pipx

Both run fbtodo in an isolated environment and put `fbtodo` on your `PATH`.

```sh
uvx fbtodo                 # run it once, install nothing
uv tool install fbtodo     # ...or keep it
pipx install fbtodo        # the same idea with pipx
```

`uvx` needs no install step at all, which makes it the fastest way to try the
pane: `uvx fbtodo` in a tmux session opens it immediately.

## From the checkout

The zero-install path — the launcher puts `src/` on the import path beside
itself, so it needs no build and no venv:

```sh
brew install tmux                                    # or: apt install tmux
git clone https://github.com/TLE47/fbtodo ~/Projects/fbtodo
mkdir -p ~/.local/bin && ln -sf ~/Projects/fbtodo/fbtodo ~/.local/bin/fbtodo
tmux new -s work
fbtodo
```

This is also what the `fb` shortcut and the notification kit are written
against, since both live in the checkout.

## Which one to pick

| You have | Use | Where it lands |
|---|---|---|
| Homebrew | `brew install TLE47/tap/fbtodo` | Homebrew's prefix, upgraded by `brew upgrade` |
| `uv` (or want zero install) | `uvx fbtodo` / `uv tool install fbtodo` | `~/.local/share/uv/tools` |
| `pipx` | `pipx install fbtodo` | `~/.local/pipx/venvs` |
| Neither, but Python 3.9+ | the one-liner | a venv under `$PREFIX` |
| A checkout you want to edit | from the checkout | wherever you cloned it |

To confirm the pane can actually appear on this machine:

```sh
fbtodo doctor          # python, tmux, colour, locale, watchers — and what is missing
```

## Pinned versions

Every route can pin, except Homebrew, which pins through the tap:

```sh
sh install.sh --version 4.30.0
uv tool install fbtodo==4.30.0
pipx install fbtodo==4.30.0
pipx install "git+https://github.com/TLE47/fbtodo@4.30.0"
```

The git tag is the version — `4.30.0` — with no leading `v`.

## Uninstalling

```sh
brew uninstall fbtodo
uv tool uninstall fbtodo
pipx uninstall fbtodo
rm -rf ~/.local/share/fbtodo ~/.local/bin/fbtodo     # the one-liner's venv method
rm -f ~/.local/bin/fbtodo                            # the checkout method
```

State lives in `$XDG_STATE_HOME/fbtodo` (`~/.local/state/fbtodo`) and is never
removed by an uninstall — `fbtodo prune` trims it, and deleting the directory by
hand is the whole of it.

## The notification kit

The optional phone/ntfy watches live in `scripts/notify/` in the checkout and are
not installed by any of the package routes above (they are shells and helpers
you place yourself). `scripts/notify/README.md` has the one command that copies
them into `~/.config/freebuff-notify/`.

## Releasing (maintainers)

**[docs/RELEASING.md](RELEASING.md) is the click-by-click setup** — the PyPI
pending publisher, the tap repository, the token, and what to click for each.
The short version follows.

The tag is the release, and the tag name is the version in
`src/fbtodo/base.py`:

```sh
python3 scripts/preflight.py --version 4.30.0  # say whether the release can succeed at all
python3 scripts/release.py 4.30.0 --dry-run    # show the plan
python3 scripts/release.py 4.30.0              # bump, roll CHANGELOG, build, commit, tag
git push origin main && git push origin 4.30.0 # starts the publish workflow
```

`preflight.py` is worth running on its own: the tag is what publishes, PyPI
never accepts a version twice, and the tap job fails *quietly* when its token is
missing — so the things that go wrong are the things you want named before the
tag exists rather than after.

Pushing the tag runs `.github/workflows/publish.yml`, which refuses a tag that
disagrees with `VERSION`, builds the sdist and wheel, installs the wheel and runs
`fbtodo doctor` against it, then publishes to PyPI. Two one-time setup steps
belong to the maintainer, not to the code:

1. **PyPI** — add a trusted publisher for this repository and workflow at
   <https://pypi.org/manage/account/publishing/>. No token is stored in the repo.
2. **Homebrew tap** — create `TLE47/homebrew-tap` with this formula in
   `Formula/fbtodo.rb`, and add a `HOMEBREW_TAP_TOKEN` secret (write access to
   that repository) so the release keeps it in step. Without the secret the tap
   job skips and the formula is updated by hand with
   `packaging/homebrew/update-formula.py`.
