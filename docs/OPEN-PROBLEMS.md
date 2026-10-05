# Open problems

Real, measured, not yet fixed. Each entry is: the symptom, the mechanism with the
measurement that shows it, what it costs today, and the directions a fix could take.

---

## 1. The desktop app's todo list is only readable at turn boundaries

**Status:** **addressed 2026-10-03 (fbtodo 4.30.x)** · measured 2026-09-30 on Freebuff Desktop,
fbtodo 4.29.0 · the local API re-measured 2026-10-01 (fbtodo 4.30.2): **direction 1 is
answered, and the answer is no** — see *Ruled out: no LOCAL channel either*. The transcript
really is deferred, but the in-flight list was in the store's OTHER column all along:
`threads.harness_state` — see *Shipped: the list was in `harness_state`*, below.
**Affects:** `-s desktop` (and therefore the tail of `-s auto`, which asks `cli` first)

### Symptom

`fbtodo -s desktop -A` reports `no write_todos call yet in this session` for a thread that is
visibly working, and keeps saying so for the **whole turn** — 35 minutes in the sample below.
`-s cli` shows the same session's list updating normally while the agent works.

### Mechanism

The desktop store (`~/.config/freebuff-desktop/projects/<slug>/desktop-v2.db`) keeps **one row
per message**, and the *assistant* row is inserted when the turn settles, not while it streams.
The todo list is not a table of its own: it is a `write_todos` **tool-call part** inside that
row's `parts_json`. So a list cannot be read before the row that contains it exists — however
long the turn runs.

Measured **while a turn was running**:

```
thread 70821b2e  turn_state=running  updated=21:54:22  turn_alive_at=21:54:48
  messages for 70821b2e:  seq=2 user, seq=3 assistant, seq=5 user
                          ^ seq=5 is the in-flight request — no assistant row yet
```

Measured on the **settled** rows, same store:

```
21:50:43  seq=3  assistant 70821b2e  turn lasted 152s after the request
      tools in that ONE row: 25
21:51:24  seq=4  assistant dad7b13e  turn lasted 1091s after the request
      tools in that ONE row: 85
10:37:53  seq=2  assistant c081553e  turn lasted 2097s after the request
      tools in that ONE row: 98  (…, write_todos, …)
```

One row per turn, timestamped at the turn's close, carrying every tool call of that turn.

### Ruled out: this is not a visibility artefact

The store is WAL-mode SQLite and the probe reads it live — the *same* read that found no
assistant row for the in-flight turn reported `turn_state=running` and a fresh `turn_alive_at`
for that very thread. So the app is writing to this DB mid-turn (the `threads` row moves); it is
the **transcript** that is deferred. There is no uncheckpointed row waiting to be seen: a fresh
`messages` row for the in-flight turn genuinely does not exist.

### Why the app writes it that way (inference, not stated anywhere)

The store is the durable transcript plus an FTS index over it (`freebuff_conversation_search`).
Streaming a partial assistant message would mean either rewriting a growing multi-MB
`parts_json` on every delta, or inventing a row-per-delta scheme — and it would let the search
index and the transcript observe half-finished turns. Committing once at settle keeps every
message row immutable and complete. The app's own live signal (`turn_state`, `turn_alive_at`)
lives on the `threads` row, which *does* move mid-turn.

### Shipped: the list was in `harness_state` (2026-10-03)

The transcript is not the whole store. `threads.harness_state` is a JSON blob the app rewrites
as the agent works, and `sessionState.mainAgentState.messageHistory` inside it holds the tool
calls **as they are made** — `write_todos` among them — before any of them is committed as a
`messages` row. Measured while a turn was running: the newest `write_todos` in that history was
the live list (a step ticked off seconds earlier), and its `sentAt` is the call's own clock.

So no channel was needed, only another column. `-s desktop` now reads it
(`desktop.harness_turn`), with SQLite's own `json_each` walk so a blob that grows with the whole
session is never materialized in Python. Three rules keep it the CURRENT list: read only while
the turn is running, only when the list was written at or after the turn's own start
(`last_prompt_at`), and only when it is not older than what is committed. The turn's start also
rides on the state, so `finish_state` drops a **finished** previous list — the pane says
`last turn's list is done — waiting for this turn's list` rather than showing steps already
over. `turn_running` and `turn` are carried on the observation now too, so the `turn running`
sentence from direction 2 actually reaches the renderers it was written for.

The same history also carries the **requests**: every user message the app tagged
`USER_PROMPT` (compaction summaries and tool-error injections are tagged otherwise and stay
out). `desktop._now_and_nudge` reads them with the CLI journal's own `is_nudge` / `pick_prompt`
/ `_newest`, so a running desktop turn shows `now`/`nudge` for a request newer than the list —
the same line a CLI pane shows — and a `Goal:` heading written for that request wins the slot.

The agent's own `Goal:` heading for the list is read from the same `harness_state` text parts
(`goal_line`) and matched to its list by `pick_goal`, the CLI journal's own rule factored out
and shared, so a mid-turn desktop pane is headed exactly as a CLI pane would be — `goal` and
`goal_source` now ride on the desktop observation. `turn.verbs`/`turn.files` (the counts in
`turn 8m · 106 iterations · 4 files edited`) are still the journal's; a desktop turn line
names its age only.

### Ruled out: no LOCAL channel either — the door a bridge would use is closed on purpose

The orchestrator is not only a store writer: it is a local HTTP server, so a mid-turn channel
was worth looking for. It has one, and it is closed to every process but the app itself.
Measured **2026-10-01** (Freebuff Desktop launched that morning, fbtodo 4.30.2, macOS):

- the orchestrator (`bun …/Freebuff.app/Contents/Resources/orchestrator/orchestrator.js`, pid
  21409 in this session) listens on **127.0.0.1:61171** — the port is per launch
  (`lsof -nP -p <pid>`) — and serves an **SSE event bus**: `GET /api/events` replays the app's
  state and then subscribes to `app.bus` (the app's own path, `src/server/http/events.controller.ts`,
  which its bundle names in a comment — none of this is in *this* repo), so the live
  `{type: "agent", seq, event}` traffic the message row is later built from is one loopback hop
  away…
- …and every `/api/` path is refused without the launch id: `/api/threads` → **401**,
  `/api/status` → **401**, `/api/events` → **401**. (`/health` and `/api` answer 200 only
  because the guard is `pathname.startsWith("/api/")` and those do not match it.) The token is
  presented as header `x-freebuff-launch-id` or cookie `freebuff_launch_<port>`
  (`requiresLaunchToken`, `presentedLaunchTokens`).
- and that id cannot be read from outside **by design**. Electron main hands the launch id and
  the bridge tokens to the orchestrator as one JSON line on its **stdin**
  (`launchBootstrapLine`), and the app's own source says why: *"The launch id and the bridge
  bearer tokens reach the orchestrator as ONE JSON line on its stdin, never in its environment:
  `ps eww <pid>` (macOS) and `/proc/<pid>/environ` (Linux) show a process's starting environment
  to any other process of the same user, and published bridges read `FREEBUFF_LAUNCH_ID` from
  there to drive the local API from outside the app."* The keys are also stripped from the
  child's environment (`LAUNCH_SECRET_ENV_KEYS`) and redacted from the echoed stdout
  (`redactLaunchId`). This is the door a bridge would have used, closed deliberately.
- the **CDP bridges are gated the same way**: the app's own listeners here are
  127.0.0.1:61163–61165, and `/json/version` on 61164/61165 answers
  `{"error":{"kind":"bad_request","message":"missing or invalid token"}}`; that token ships
  in the same stdin line (`FREEBUFF_CDP_BRIDGE_TOKEN`).
- and the live turn is not quietly available anywhere else. The orchestrator *does* fold the
  agent events into parts as they arrive (`foldAgentEvent`) and flush them on a throttle
  (`MirrorService.flushLive` → `setLive`) — but that transport is built from a
  `ConvexTokenProvider` and `machineId()`, i.e. **the cloud mirror, what the phone sees**. The
  local bus carries the raw `agent` events plus a thread's *metadata* (`withLive(thread)`:
  `stopToken`, `willContinue`, `compacting`, `pendingWorkCount`), never the streaming parts.

So the first half of direction 1 is answered — *does the app expose the in-flight turn anywhere
at all?* Yes, on loopback, behind a per-launch secret it deliberately keeps from other
processes — and the second half with it: the parts that do leave the app mid-turn leave for the
cloud, not for a local reader.

### What it costs today

- Any turn longer than a few minutes shows **nothing** on the desktop source until it closes,
  even though the agent has a list and is ticking steps off it.
- Work can land entirely unlisted: the store has no way to notice that files moved without a
  matching `write_todos`, so a pane that shows a complete list can still be describing an agent
  that kept editing after it wrote them. (Direction 4 below, and the shipped boundary guard
  from direction 5, address this directly.)
- fbtodo's desktop logic was built around this: `DESKTOP_LIVE_MS` / `DESKTOP_RUNNING_MS`
  (`src/fbtodo/desktop.py:13-24`) decide "is this thread live?" from *when a list was last
  written*, because that is the only clock the store offers. `--thread-live` (default 90 min)
  is the same compromise.
- It is documented as a limit rather than a bug — `docs/SOURCES.md:15-16`,
  `docs/decisions/README.md:94` (*"The CLI journal is the only live Freebuff source"*).

### Directions a fix could take

1. ~~**Publish from this side — needs a mid-turn channel that may not exist.**~~ **Answered
   2026-10-01: the channel exists and it is closed to us.** A bridge can only push what it can
   read, and the transcript is the deferred part; the other tables offered nothing either
   (`thread_deliveries` is PR/merge bookkeeping — `branch`, `merge_commit_sha`, `url`, `number`
   — and held **0 rows** in all three stores on this machine; `queue_items` is the queued-prompt
   list, 2–4 rows, not todo state). The real question was whether the app exposes the in-flight
   turn anywhere at all: it does, on **127.0.0.1**'s SSE bus, and every route to it is behind a
   per-launch id that arrives on the orchestrator's stdin and is stripped from its environment —
   closed to other processes on purpose. The measurements are in *Ruled out: no LOCAL channel
   either* above. Nothing to build here that would not first require the app to open one of
   those doors.
2. **Say "turn running" instead of looking stale — shipped 2026-10-01.** `threads.turn_state`
   and `turn_alive_at` are live and they are now read: a thread whose turn is alive gets
   *"turn running · no list yet"* instead of `no write_todos call yet in this session` — which
   read as "the agent forgot" when it actually meant "the store has not committed yet". A
   stale heartbeat is a turn that died rather than one in flight, so it is not called running.
   **Extended 2026-10-03:** the signal now leads to the list itself — the newest in-flight
   `write_todos` from `harness_state`, on the state as this turn's list, so a running desktop
   turn shows what the CLI shows (see *Shipped: the list was in `harness_state`*).
3. **Ask for the change upstream — the only fix left that needs code.** Have the app append
   tool-call parts as they happen (or write the assistant row at turn start and update it).
   This needs the app's storage layer, which is not in this repo. Two smaller things are now
   done and do not repeat them: direction 2 shipped (`turn running · no list yet` —
   `desktop.py`'s `turn_running`, the `threads` row's own live signal) and direction 5 shipped
   (`STEPS OPEN` / `files_unlisted`, the boundary guard). Direction 4 is not code at all; it is
   a working convention the owner can adopt today.

4. **Shorten the loop — one step per turn.** The lag is as long as the *turn*, so the cheapest
   fix is to make the turn short: an agent that completes exactly **one** step per turn makes
   the boundary the update point, and every turn closes with a list that already reflects what
   it did. `-s desktop` is then at most one step behind instead of one task behind. No code
   change at all — it is a working convention (the list rule in `docs/AGENTS.md`), which is also
   the cheapest thing to measure: compare the desktop lag on a session run this way against one
   that batches its work.

   Cost, stated honestly: each step pays a turn's overhead (prompt, preamble, a new
   `write_todos`), so the owner sees more, smaller updates. And a "step" has to be a real unit
   of work — a step that is one tool call trades a stale list for a noisy one.

5. **Guard the boundary — refuse completion without a fresh list — shipped 2026-10-01.** The
   bell already refused to ring unless the list is complete *and* `turn_ended`; the third
   condition — the turn that just ended must have re-published a list after its own last file
   change — is now in place. fbtodo computed both halves already:

   - the *did work land* half is `turn.verbs` / `turn.files` — `EDIT_VERBS = ("edited",
     "wrote", "patched")` in `src/fbtodo/scan.py`, rendered today as `3 files edited`
     (`docs/INTERNALS.md:134`);
   - the *was the list told* half is the newest `write_todos` record's position in the journal
     (`list_id` / `list_version`);
   - `turn_ended` marks the boundary itself.

   When edits landed in a turn that published no new list, the pane says
   `steps still open — N file(s) changed, list not rewritten` and the completion bell and phone
   push are **withheld** (or the nag repeats, louder), instead of announcing a finish the list
   cannot vouch for. The same signal can drive the stall/`--stale-after` nag rather than a
   separate one.

   Caveat: on a store read per turn this is only detectable *at* the boundary — it cannot warn
   mid-turn. That is fine: the boundary is exactly where the bell makes its decision. The
   desktop store records no `shouldEndTurn`, so a guard on that source would have to lean on
   `verbs` plus the quiet-window rule the bell already uses. It does carry inputs — fbtodo
   already extracts `$.input.todos` from `parts_json` — so both halves are available on the
   source this guard is for.

### To verify a fix

Re-run the probe below **during** a long turn. Before a fix: the running thread has no
assistant row and no row containing `write_todos`. After: either a row exists mid-turn, or the
pane's status for a running turn names the turn rather than blaming the agent.

The **local API** half needs no turn at all — it is a property of the launch, so it is a
five-second check:

```sh
ORCH=$(ps -eo pid,command | awk '/orchestrator\/orchestrator\.js/ {print $1; exit}')
PORT=$(lsof -nP -p "$ORCH" | sed -n 's/.*TCP 127\.0\.0\.1:\([0-9]*\) (LISTEN).*/\1/p' | head -1)
for p in /health /api /api/threads /api/events; do
    printf '%s -> ' "$p"; curl -s -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:$PORT$p"
done
```

(`ps`, not `pgrep -f`: on this macOS the orchestrator does not show up under `pgrep -f` at all,
which is how the first version of this probe answered `000` to everything.)

Expected (2026-10-01): `/health` 200, `/api` 200 — the guard is `startsWith("/api/")` and
`/api` does not match it — and `/api/threads` and `/api/events` **401**. **If `/api/events`
ever answers 200 without the launch id, the door has been opened and direction 1 becomes
buildable**: that is the single signal to watch, and it is cheaper to check than any of this.

The boundary guard (direction 5) is testable offline, no app needed: a fixture turn whose record
edits files and publishes no `write_todos` must **not** ring the bell, and one that ticks every
step and re-publishes must. `scripts/notify/test-freebuff-notify.sh` already pins the ring
matrix, so the new condition is one more row in it.

```python
# probe: is the in-flight turn's list readable yet?  (read-only)
import sqlite3, glob, os, datetime

def hm(ms): return datetime.datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S")

db = max(glob.glob(os.path.expanduser("~/.config/freebuff-desktop/projects/*/desktop-v2.db")),
         key=os.path.getmtime)
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
cur = con.cursor()

print("threads whose turn is alive now:")
for tid, st in cur.execute("SELECT id, turn_state FROM threads ORDER BY updated_at DESC LIMIT 5"):
    rows = cur.execute(
        "SELECT role, ts FROM messages WHERE thread_id=? ORDER BY seq", (tid,)).fetchall()
    todos = cur.execute(
        "SELECT COUNT(*) FROM messages WHERE thread_id=? AND parts_json LIKE '%write_todos%'",
        (tid,)).fetchone()[0]
    print(f"  {tid[:8]}  turn_state={st:<8} "
          f"msgs={[r for r, _ in rows]}  write_todos rows={todos}")
    if rows:
        print(f"        last message {rows[-1][0]} at {hm(rows[-1][1])}")
con.close()
```

A running thread printing `msgs=['user']` (or ending on `user`) while `turn_state=running` is
the bug: the agent's work is underway and unreadable.

### Related code

- `src/fbtodo/desktop.py` — the desktop source (schema moves between app versions; see
  `thread_columns`).
- `src/fbtodo/sources.py:58` — the CLI journal, *"appended mid-turn"*, for contrast.
- `.freebuff/desktop-probe.py` — summarizes tabs/spaces/threads across every store (scratch,
  gitignored).
