# A rule you can paste

fbtodo draws a list the agent writes **itself**, with its own `write_todos` tool. Nothing in
this repository can add an item to a list or start one, so the one prerequisite is that the
agent writes one. The snippet below is the whole of it: paste it into your own `AGENTS.md` —
the file an agent reads at the start of every session, in every project — and every session
keeps a list without being asked. If you already have an `AGENTS.md`, drop the bullet into it
rather than replacing what is there.

```md
## Progress
- Track every task with a todo list: write it before starting work, and tick each step off as
  it lands — a one-line fix as much as a refactor. It is the only progress signal the operator
  can see while you run, and it is what a live pane draws.
```

Two smaller conventions make a list much better on screen, and both cost the agent one line:

- write the list **before** the work, not after it. A clock is built by *watching* a step run,
  so a list that arrives with every step already ticked has no durations in it at all.
- open the task with a `Goal:` line of six words or fewer. It becomes the heading above the
  list. Without one the pane warns in the warn yellow — `big goal · no heading`, naming the
  `Goal:` line the agent owes — which is the honest answer to "what is this list for?" but a
  worse one to glance at.

If you would rather not edit a file, the same thing works as an ask inside a session: *plan
this as a todo list, and tick items off as you go.*

To check it took, open a pane and start something. `fbtodo status` says what it sees either
way — including the sessions that never called `write_todos`, which is a fact about the
agent's habits and not about the watcher.
