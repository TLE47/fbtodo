# Open problems

Real, measured, not yet fixed. Each entry is: the symptom, the mechanism with the
measurement that shows it, what it costs today, and the directions a fix could take.

---

## 1. The desktop app's todo list is only readable at turn boundaries

**Status:** open · measured 2026-09-30 on Freebuff Desktop, fbtodo 4.29.0
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

### What it costs today

- Any turn longer than a few minutes shows **nothing** on the desktop source until it closes,
  even though the agent has a list and is ticking steps off it.
- fbtodo's desktop logic was built around this: `DESKTOP_LIVE_MS` / `DESKTOP_RUNNING_MS`
  (`src/fbtodo/desktop.py:13-24`) decide "is this thread live?" from *when a list was last
  written*, because that is the only clock the store offers. `--thread-live` (default 90 min)
  is the same compromise.
- It is documented as a limit rather than a bug — `docs/SOURCES.md:15-16`,
  `docs/decisions/README.md:94` (*"The CLI journal is the only live Freebuff source"*).

### Directions a fix could take

1. **Publish from this side — needs a mid-turn channel that may not exist.** A bridge can only
   push what it can read, and the transcript is the deferred part. Checked already: the other
   tables offer nothing. `thread_deliveries` is PR/merge bookkeeping
   (`branch`, `merge_commit_sha`, `url`, `number`) and held **0 rows** in all three stores on
   this machine; `queue_items` is the queued-prompt list (2–4 rows), not todo state. So before
   building a bridge, answer the real question: **does the app expose the in-flight turn
   anywhere at all** — a socket, an event log, or a renderer-side store on disk? Nothing
   obvious under `~/.config/freebuff-desktop/` yet, and the app's code is not in this repo.
2. **Say "turn running" instead of looking stale.** `threads.turn_state` and `turn_alive_at`
   are live. The pane could report *"turn running · no list yet"* for a thread whose turn is
   alive, instead of the current `no write_todos call yet in this session` — which reads as
   "the agent forgot" when it actually means "the store has not committed yet". Cheap,
   honest, and it does not need the app changed.
3. **Ask for the change upstream.** Have the app append tool-call parts as they happen (or
   write the assistant row at turn start and update it). This is the real fix and it needs the
   app's storage layer, which is not in this repo.

### To verify a fix

Re-run the probe below **during** a long turn. Before a fix: the running thread has no
assistant row and no row containing `write_todos`. After: either a row exists mid-turn, or the
pane's status for a running turn names the turn rather than blaming the agent.

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
