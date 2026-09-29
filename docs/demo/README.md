# The demo

Everything here exists so the README can show a real pane instead of a drawing, and so anyone
can record that demo themselves in one command.

| File | What it is |
|---|---|
| [`demo.tape`](demo.tape) | the [vhs](https://github.com/charmbracelet/vhs) script for the GIF |
| [`record.sh`](record.sh) | `brew install vhs` then `docs/demo/record.sh` → `demo.gif` |
| [`drive.sh`](drive.sh) | replays a scripted session into the fixture (~20 s) |
| [`fixture/`](fixture) | the transcript the pane reads: `head.jsonl` (the committed start), `log.jsonl` (what the driver writes), `chat-meta.json`, and the two logs the `PATCH`/`ALERT` rows read |

## Record it

```sh
brew install vhs
docs/demo/record.sh
```

The tape points `FBTODO_HOME` at a scratch directory (`/tmp/fbtodo-demo`) on purpose: the
driver's synthetic steps would otherwise be remembered as this project's pace history and
skew the real estimates. It also points `FBTODO_PATCH_LOG` and `FBTODO_ALERT_LOG` at the
fixture logs — without that, those two rows would read **your** notifier log, and your own
name or handle would be published in the GIF.

## Watch it without vhs

The pane does not care where the transcript came from, so you can run the same thing by hand.
In one shell:

```sh
sh docs/demo/drive.sh
```

…and in a tmux pane below it:

```sh
export FBTODO_HOME=/tmp/fbtodo-demo \
       FBTODO_PATCH_LOG=$PWD/docs/demo/fixture/patch.log \
       FBTODO_ALERT_LOG=$PWD/docs/demo/fixture/phone.log
fbtodo pane --no-daemon --chat docs/demo/fixture -i 0.5 --tick 0.5 --stale-after 0
```

`--chat` takes a directory, `--no-daemon` skips instance detection (there is no Freebuff
process behind this list), and `--stale-after 0` says "never treat it as stale, keep
drawing". A plain `fbtodo -s cli` pointed at a real session needs none of those flags —
they exist for exactly this: driving the renderer by hand. The two log paths are only there
so the `PATCH` and `ALERT` rows show the demo's own fixture instead of yours.

## What is real here

Everything except the session. `drive.sh` appends the same kind of `write_todos` records an
agent's own calls leave in its journal, `fbtodo pane` is the real renderer reading the real
file, and the clocks, the estimates, the bar and the `done/total` count are the tool's own,
not a mock-up. The tape's 22 seconds are four ticks of the list plus the final pair — a list
finishing **and** its turn ending, which is what the bell waits for.

If you record a demo of your own real session too, say so in the caption — a five-second
clip of an actual agent working is the most convincing thing this repository can have.
