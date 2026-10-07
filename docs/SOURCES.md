# Where the list comes from

fbtodo never invents a todo list: it reads one something else wrote. There are four sources —
two built into the tool (the stores a Freebuff writes) and two generic ones (`push` and
`file:PATH`) that make no assumption about Freebuff at all.

## The built-in stores

A coding agent's todo list is not a side channel — it is written to whatever transcript store
the client keeps. Freebuff keeps two, and picking the wrong one is the usual reason a pane
looks broken:

| Source | Store | Granularity |
|---|---|---|
| **cli** (`freebuff` in a terminal) | `~/.config/manicode/projects/<project>/chats/<ISO>/log.jsonl` | **live — mid-turn** |
| **desktop** (the app) | `~/.config/freebuff-desktop/projects/<slug>/desktop-v2.db` (SQLite) | per turn, while the app runs |

The CLI journal is the good one: append-only, written *during* the turn. Each record is a JSON
line, and a `write_todos` call lands in it the moment the agent makes it. fbtodo tails that
file, keeps every `write_todos` it has seen, and renders the newest. A new list **replaces**
the old one wholesale — state is never merged — and when the session changes, the old list is
dropped immediately instead of lingering.

`--source auto` (the default) follows the session **working** in the current directory: the CLI
journal if one is live here, else the desktop store. A chat directory keeps its last chat
forever, so a finished journal is *held* rather than chosen — it answers only when nothing
fresher is here. Without that rule a directory whose last CLI session ended days ago kept
rendering that session's list, frozen, while the desktop app's live thread went unseen: a pane
pinned to a two-day-old chat reading `ALL DONE`. "Live" is a Freebuff process behind the chat,
a journal whose newest record says the agent is **mid-turn**, or one that moved inside the last
90 minutes. That mid-turn answer is read from the journal itself (`journal_liveness`), never
from the file's mtime: the desktop app appends its *own* records to the same `log.jsonl`
(`cli.feedback_button_hovered`, a note saved, a tab closed), so an mtime says a session that
ended its turn an hour ago is "just written" — and a pane inside the app then sits on that
finished list while a thread works in front of you, unchanged across every tab (measured
2026-10-04: a frozen `ALL DONE` 6/6 under two live threads). The record that decides is the
turn boundary the agent writes on every iteration: `shouldEndTurn: true` means it has finished
and is waiting for you, and a `prompt` record means you asked again, which puts the session back
to work with no clock involved. That boundary outranks everything except a **Freebuff process
behind that chat**, which is working it whatever the boundary says — evidence about the chat
rather than about the pane, and the reason `--watch-pid` on its own does not: `find_instance`
takes that pid as given whenever it is alive, and every pane the shell wrapper opens carries
one, so in a directory the app also works in a live pid used to keep an ENDED chat answered as
the live session (measured 2026-10-04 on the pane-close fixture: with a live non-Freebuff pid
the first frame was the ENDED chat, without one it was the live thread). What any pid is still
good for is the other half: a chat that is merely quiet — one long step outlasting the window —
is rescued by it, because the journal is appended every iteration and a tail can still go stale
between two of them.
An explicit `-s cli|desktop` is never
answered from cached watcher state unless that state describes the same backend, and never
falls through at all — asking for one source still means exactly that.

A desktop project answers a directory the way the CLI answers one: **the project whose path is
that directory**. The store's `project.json` records the folder the app opened, and the app's
projects nest — the home directory is the one path that contains every path there is — so
walking up to a parent project let a repo under home be answered by the home project's session,
the list of a thread the repository had nothing to do with. Depth decides instead (the deepest
project containing the directory wins, so a broad project can never shadow a narrow one) and
`auto` narrows it further to a project that *is* the directory, mirroring `cli_chat_dir`, which
reads `basename(cwd)` and never a parent. An explicit `-s desktop` was not told a directory, so
it keeps the wider walk, deepest first.

The same rule is why a pane does not stay on a dead session. The watcher heartbeats the state
file it owns, so a cached state stays *fresh* after the chat behind it has finished; a pane that
trusted freshness alone kept drawing that ended list until the file aged out or somebody
reloaded it. A cached CLI state whose journal has gone quiet with no process behind it is
instead dropped, and the pane re-asks `snapshot` on that same poll — which is where `auto`
re-chooses its source, so it lands on the live thread within one interval. It keeps answering
from `snapshot` until the file itself names a live session again: there is no "already handled"
shortcut, because a memo of the dropped session only means trusting the same unchanged file on
the next poll, which hands back the list the pane just let go of. Only a CLI state can be
called *over*: a `file:` state has no process to outlive it, and the desktop store windows its
own threads on every read. The look-only reads are held to the same rule — `fbtodo json` and
`fbtodo bar` may answer from the cached state while it names a live session, and scan the
stores when it does not, so the command people run to see what the pane is about to draw never
prints a session that has ended.

`auto` also says what it did. Picking a source is a choice, and a pane showing a finished chat's
list is the visible half of one, so the chain records its own account of itself as `source_why`
and both renderers draw it where they already say what the pane is showing — the framed title
chip and the plain heading:

```
┌──  FREEBUFF TODOS · cli finished 31h  ───────────── desktop · dad7b13e ──┐
Freebuff todos  desktop · dad7b13e… · cli finished 31h → desktop
```

Only `auto` writes one: a chain of one source has nothing to explain away, so an explicit
`-s cli|desktop|file` is unchanged, and a state carrying no note draws exactly the title it
always did. The clauses are the sources it passed, in the order it asked them — `cli finished
31h` (a journal past the live window with no process behind it), `cli none here`, `desktop older`
(the finished chat's store really was the fresher one) — with the source that answered last, so a
narrow title clips the part the right slot already repeats. Below the width where the note can
still say something it is dropped rather than truncated to nothing, and the right slot keeps the
session it is naming.

### The desktop app's live threads

**Which thread is followed.** The app's own answer is `workspace.activeId` — the tab in front of
you — read from the file it persists. That file is written when the layout changes and not
otherwise (measured 2026-10-04: a switch lands in 0–1 s, but a ten-minute stretch passed with no
write at all), and it names the **tab**, while the work can be running in a split opened from that
tab — so a pane reading only it draws a tab you are not working in. No live channel replaces it.
The orchestrator API and the CDP bridge (both on 127.0.0.1) answer `401` to anything outside the
app's own process, which is where their tokens are minted; the store carries no focus column, no
table and no receipt naming the tab in front of you; and `turn_alive_at` — the one column that
looks like a heartbeat — is written **one value for all threads**, so it cannot say which tab is
yours. So the file stays the authority and is corrected by the only per-thread clock there is,
the ask: `focused_thread` prefers the thread most recently worked in while the persisted tab is
quiet, and the pane's title names what it did (`b2422dbe behind → 53d4cd37 asked 13m ago`). The
correction is timid on purpose — the app's tab wins whenever it is itself among the live threads,
an ask outside `DESKTOP_LIVE_MS` is not "where you are", the newest ask wins when several are
live, and a store without those columns answers nothing, leaving `activeId` exactly as it was.

The app can hold several threads on one project at once, each writing its own `write_todos`, so
the READS take them all: `-s desktop` reads the store's *other* live threads too and stacks them
in one answer — the followed thread first, then every open thread whose newest list is inside
`--thread-live` minutes (default 90; `0` disables the window). `--threads N` (default 4) caps how
many are shown, the followed one included, and `--threads 0` restores the single-list answer.
That is the right shape for a survey you asked for.

**A pane is not a survey.** It latches: the first poll it accepts decides which thread it is
about, and every poll after that asks for that one by name (`latch_pane_subject`, which narrows
the pane's own arguments to `--thread` + one source). Two reasons, both measured. A reader asks a
pane "how far is *this* list", and a stacked frame answers about three while the set changes
under them. And a following pane moves on its own: the picker above reads "you typed in another
tab" as "that is the thread you are working in", so typing in another tab silently swapped the
list on screen mid-read — both frames valid, nothing drawn to say so. A finished turn does NOT
release the latch (a finished list is still a list somebody is reading), a second thread going
live does not steal it, and the pane holds its last list until you close it. The latch is held
twice: in the pane's own process (`pane_locked`, so no later poll can re-latch) and in
`FBTODO_PANE_LOCK` for the reload. The shared watcher's cached state is used only when it names
that same subject, so a pane pinned to one chat or thread is never handed the list the watcher
wrote for another. The latch does not
choose the first list, though: that is still `auto`'s chain, and a cached state whose session has
ended is still dropped before anything latches, so a pane still lands on the live thread rather
than the finished chat.

A thread the owner has filed away — `status = closed`, or sitting in the sidebar archive — is
never drawn, because a pane that kept showing it would be arguing with the sidebar.

When only one thread qualifies, the `threads` key is left off the state entirely rather than set
empty: a renderer tells "show one list" from "stack these" by its presence, so a single-list frame
is byte-for-byte what it always was, and no other source changes shape. The other threads are a
bonus on top of a list that has already been read, so a store this build cannot answer the second
question about returns the first list rather than none.

The store commits a message row only when a turn **closes**, so a thread that is visibly working
has no committed list to read yet — and `no write_todos call yet in this session` read as "the
agent forgot" when the truth was "the store has not committed". Two live signals close that
gap. The `threads` row moves mid-turn (`turn_state = running`, with `turn_alive_at` as the
heartbeat that proves the turn is still alive), so a listless thread whose turn is alive says
`turn running · no list yet`. And `threads.harness_state` — the agent's own state, rewritten as
it works — carries the tool calls in `mainAgentState.messageHistory` **as they are made**, so
the newest `write_todos` there is the in-flight list: `-s desktop` reads it (only while the
turn is running, and only for a list written at or after this turn's start, `last_prompt_at`)
and shows it the way a CLI pane mid-turn does. A COMMITTED row is a whole turn, so its parts
carry every `write_todos` the agent made in it — updating a list means writing it many times —
and the turn's list is the LAST of them: all three reads (the followed thread, the newest-list
fallback and `live_threads`) order by `m.ts DESC, m.seq DESC, part.key DESC`, because
`json_each` promises no order and taking the first part drew a finished turn as `0/6`
(measured 2026-10-03).

The heading and the requests of a FINISHED turn are read the same way, from the same rows
(`_committed_prose`): the app's own `Goal:` text parts and its user rows, in the `(seq, part.key)`
positions `pick_goal`, `goal_stale_at` and `_now_and_nudge` compare, bounded to 24 rows back
from the list they head. That is the live read's other half — a pane whose agent state carries
no history used to draw a heading-less list under `no heading — the agent owes a Goal: line`
while the row it was reading held the line. It is asked only when the committed list is what the
pane is showing: the live history's heading belongs to the live history's list, and carrying it
over a committed one would head that list with the wrong turn's objective. Positions are
`(message, part)` pairs in BOTH local stores for the same reason the list read needs
`part.key` — one message is a whole turn, so two headings in it would otherwise tie. The same history carries the app's `USER_PROMPT`
messages, so `now` / `nudge` are read from it too, by the CLI journal's own continuation rules
(a request after the list, a bare `continue`, a `Goal:` heading written for the newer request).
The agent's own `Goal:` heading (`goal`, `goal_source`) rides on the observation as well — read
from the same text parts by `goal_line` and matched to its list by `pick_goal`, the journal's own
rule shared rather than rewritten, so a mid-turn desktop pane is headed as a CLI pane would be.
See [OPEN-PROBLEMS.md](OPEN-PROBLEMS.md) #1.

A committed row says WHO asked in a receipt rather than a tag, and that receipt is the committed
half of the live history's `USER_PROMPT`: every user message the app writes carries the
`input_id` of a `queue_items` row, whose `source` is the app's own word for the asker — `user`
for the person (typed, queued or steered), `assistant` for a prompt the app wrote for itself (an
auto-run decision, a suggestion, a sponsored task), `mission-*` for a campaign and `skill` for a
skill activation — and whose `kind` says whether the row is a request at all (`prompt` versus
`skill-context` or `close-tab`). `_committed_prose` joins the receipt when the store has one and
lets only a `user` `prompt` feed `now`/`nudge` and the heading's staleness bracket; a store older
than the receipts has no such table and keeps reading every user row as a request. Measured
2026-10-04, on a live thread whose newest row after the shown list was an auto-run step: without
the join the pane offered that step as the newest ask. One prompt the receipt cannot mark: the
app's Continue button (shown while a turn is interrupted) sends one fixed resume line through the
ordinary send path, so its row is marked exactly like a typed prompt — it is recognised by its
words (`DESKTOP_RESUME_PROMPTS`), in the committed rows and in the live history, whose
`USER_PROMPT` tag it carries as well. A resume is not an ask: it must not become the pane's
`now` or `nudge`, or every relaunch would redraw the pane as if the user had asked for the work
already running. And a stacked row is no longer only a list and a clock: each row carries the
thread's own heading and staleness too (`live_threads` runs the same committed-prose read per
thread, anchored to that thread's list), which is what lets `board` draw a desktop session's
objective the way it draws a CLI one's — and what the self-check's cross-reader invariant holds
together: `json`, the pane's stacked rows and the board must answer done/total, heading and
staleness identically for every thread of a store. One fixture holds a fresh heading, a stale
one and a heading-less thread; a walk that drifts fails the suite instead of the pane.

The instance to follow is found in this order:

1. `--instance-of PID` — the agent process launched by that shell.
2. `--watch-pid PID` — take this pid as the instance, outright.
3. Otherwise the agent processes are enumerated (`ps`), a cwd match on the current directory
   is preferred, and the **youngest** wins. A directory can host several sessions, and "the
   one started here" is what a human means.

What CLOSES a pane is nothing but its window: `--stale-after`, Ctrl-C, a `--once` that has
drawn its frame, and the window going away. Not the process it was opened beside, and not the
session it is drawing. Both of those were rules once (see `SOURCE_LIVE_MS` above for the
second), and both were wrong for the same reason: what the pane draws is a LIST, and a list
whose session is over is a list somebody is finishing, waiting on, or re-reading. A pane that
leaves on its own takes the reader's eyes with it at the moment they are most likely to be
looking. The turn boundary decides which SOURCE answers and when a cached state is dropped
for a re-resolve; it never decides that a pane closes.

### Watching every live session at once — `fbtodo board`

A pane answers about **one** list: the cwd's journal, one thread. Running two or three
agents at once is the normal case, and the board answers the other question with the same two
local stores and no new reader — one row per live session, newest activity first, with its
project, its backend, its `done/total`, how long ago its store moved, and its first few steps.

* `--board-max N` (default 6) — how many sessions are drawn.
* `--board-live MIN` (default 90) — how quiet a session's store may be and still count as live;
  `0` means no window. A session that stopped drops off by itself.
* `--board-rows N` (default 2) — steps shown per session.
* `--live` — keep redrawing in place instead of printing once; `--json` is the rows themselves.

A row is `read_cli`'s answer for a CLI chat and `live_threads`' for a desktop thread, so the
board cannot disagree with a pane about what a list says. Both stores are local, and the CLI
half stats every journal before it reads any of them, so a store with a hundred finished chats
costs a hundred stats and one scan.

## The generic sources

Anything that can write one JSON object can drive the pane. Neither of these knows what a
Freebuff is, and neither watches a journal, a process or a database.

### `fbtodo push` — a state on stdin

```sh
echo '{"session": "CI7", "goal": "land the release",
       "todos": [{"task": "run the suite", "completed": true},
                 {"task": "tag it"}]}' | fbtodo push
```

The object is normalized to the fields a list is made of — `session`, `title`, `summary`,
`goal`, `goal_source`, `now`, `nudge`, `todos`, `model`, `turn_ended`, `iteration`, `list_id`,
`list_version`, `ts` — and put through the same `finish_state` a watched list goes through.
So the counts, the session and the list number are the tool's, not the pusher's: a pusher that
sends nothing but `todos` gets a first list, and one that sends the whole object `fbtodo json`
printed is not punished for the extra keys.

- `--to PATH` writes that file instead of the live state — nothing else reads it, which is how
  a demo or a test drives a pane without touching yours.
- `--dry-run` validates and prints, writing nothing.
- `--quiet` prints nothing.
- Anything that is not a JSON object with a list of `{task, completed}` steps is refused, and
  the state it refused to replace is left alone. Empty stdin, non-JSON, a non-object and a
  malformed `todos` each exit non-zero (66 / 65). A `completed` must be a real boolean; a
  string like `"false"` is refused rather than counted as done.
- The payload is **capped**, not truncated: 1 MiB of stdin, 200 steps and 500 characters per
  string, and a run of `[` deep enough to recurse the parser past its limit is a refusal too.
  Over any cap is the data error (65).

Because a pushed state is written through the same path a watched one is, the readers that
prefer a live state file answer from it: a hand-pushed list needs no watcher to be shown.

### `-s file:PATH` — a state read off disk

```sh
fbtodo bar -s file:/srv/agent/state.json     # "todos 3/5"
fbtodo json -s file:$FBTODO_HOME             # a directory means that home's state file
```

The prefix is required — `-s some/dir` stays the usage error it has always been — and a
directory expands to the `fbtodo-state.json` inside it, so `-s file:$FBTODO_HOME` reads the
state a run with that home wrote.

This is a **re-player, not a re-numberer**. Nothing here is watching a process, so the state
inside is taken at its word, including the list number it recorded: a file that says `#7`
prints `#7`, and one that recorded no number starts at the first. A file that is not there
reports that, rather than reading this machine's own list. `instance_alive` is always true —
there is no process behind a file to outlive the pane — so a file source never reads as "the
session went away".

The pair together is the seam for anything that is not a Freebuff: an agent on another
machine, a CI job, a script, or a hand-typed JSON can publish a list, and the pane draws it.
