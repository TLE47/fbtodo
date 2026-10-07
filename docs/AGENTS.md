# The AGENTS.md rule fbtodo is built around

fbtodo draws the list an agent writes with its own `write_todos` tool, so every number the
pane shows is only as good as the convention the agent follows. This is that convention.

It is kept here, in the repository, because the tool's contract and this text have to agree
and a runner has no operator `~/AGENTS.md`: the self-check reads the operator's file when it
exists and this copy when it does not, so CI still enforces the same phrases against
`GOAL_MAX_CHARS` and the rest of the tool's constants. The short user-facing snippet to paste
into your own file is [`examples/AGENTS.md`](../examples/AGENTS.md); this is the fuller text
behind it.

The file is also **budgeted**, because it is injected into every system prompt: at or under
12 KB and 14 sections, with no more than 8 subsections. The self-check measures whichever
file it resolves and fails on the first byte or heading over, so the instruction a model
actually reads cannot quietly grow back into a document.

## Output

- Head the first reply of a task with one `Goal:` line: a **concise rewrite** of the
  objective in your own words — a heading for the list, not a copy of the request and not a
  summary of your plan. Keep it to <= 6 words / 38 characters, one line, no trailing period:
  `Goal: fit the pane heading whole`. `fbtodo` shows it whole above the steps, so a list stays
  readable hours later; anything longer stops being a heading.

## Execution

- Track every task with `write_todos`, and **update it the moment a step lands**: marking a
  step done is part of finishing it, not a batch for later, and that holds for a one-line fix
  as much as for a refactor. The list is the only progress signal the operator sees while you
  run, so a stale list reports work as unfinished that is already done.
- On a bare continuation — `continue`, `go on`, `keep going`, `yes`, in whatever phrasing of
  "keep going" — re-write the todo list **before** carrying on: mark what landed, drop what is
  moot, add what is next, and make the first action of the new turn that `write_todos` call. A
  nudge means the list you have is already behind you, and `fbtodo` says so
  (`nudge · continue — rewrite the list, then continue`) until a newer list exists.
- **Testing and verification are steps in the list, not an epilogue.** Write the checks the
  work will need — the suite, the typecheck, the live run, the negative path — as their own
  todos when the list is written, and tick each one as it lands.

## Commits

- Keep the subject line **under 50 characters**, and put the reasoning in the body. GitHub
  prints the subject beside every file in the commit's tree, so a longer one is truncated
  mid-word exactly where the history is read most often.
- One idea per commit. A subject that needs "and" is two commits that were not separated.
