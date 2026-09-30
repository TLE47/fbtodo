# The AGENTS.md rule fbtodo is built around

fbtodo draws the list an agent writes with its own `write_todos` tool, so every number the
pane shows is only as good as the convention the agent follows. This is that convention.

It is kept here, in the repository, because the tool's contract and this text have to agree
and a runner has no operator `~/AGENTS.md`: the self-check reads the operator's file when it
exists and this copy when it does not, so CI still enforces the same phrases against
`GOAL_MAX_CHARS` and the rest of the tool's constants. The short user-facing snippet to paste
into your own file is [`examples/AGENTS.md`](../examples/AGENTS.md); this is the fuller text
behind it.

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
