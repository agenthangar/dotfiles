---
description: Show or edit this dev slot's task list
argument-hint: "[add <text…> | done <id…> | rm <id…> | mv <id> <slot>]"
allowed-tools: Bash(t todo:*)
---

The user's scratch task list for the slot this session is running in. This is the
`t todo` shell command, run on their behalf — you are a pass-through, not an editor
of the list's contents.

Do this:

1. Run exactly `t todo $ARGUMENTS` in the Bash tool, from the session's cwd (do not
   `cd` first — `t todo` resolves the slot from the working directory, falling back to
   the tmux session name, so moving changes which list you hit).
2. Print its output back essentially verbatim. Do not renumber, reword, reorder, or
   "tidy" the items — the ids are how the user addresses them in the next command, and
   the text is theirs.
3. Add at most one short line of your own, and only if it is actually useful (e.g.
   noting that an id did not exist). No summaries of a list the user can already see.

Notes:
- With no arguments it lists this slot's open items. `t todo -A` shows every slot's
  open items at once, which is the right call when the user asks what they have going
  on generally rather than here.
- `done` and `rm` only flag an item; finished items leave the view at once and are
  dropped a week later on their own. There is no `clear`, `edit` or `undone` verb — to
  reword something, `rm` it and `add` it again.
- Do NOT use your own TodoWrite tool for this — that list is per-turn and vanishes;
  this one is the durable one the user came here for.
- If `t todo` reports the key as `scratch`, this session is not inside a registered
  repo or a `dev-*` tmux session. Say so, since the item landed in a shared list.
