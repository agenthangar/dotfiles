---
description: Push this Codex session into a detached background tmux slot (t push)
---

Push the **current** Codex session into a detached tmux slot so it keeps running
in the background and can be managed with the `t` tooling (`t open`, `t ls`,
`t pop`). This is the CLI `t push` command, run on your behalf.

Do this:

1. Run `t push` with the shell tool. It finds this session on its own: the
   dotfiles SessionStart hook recorded this codex process's thread id, and
   `t push` walks up from its shell to that process. It picks a
   `dev-<repo>-<slot>` name, writes the wrapper's resume sentinel, and then
   **signals this foreground `codex` to exit** — you do NOT need to tell the user
   to quit. On that exit the `codex()` shell wrapper spawns the detached
   `codex resume <id>` and drops the user straight into the tmux pane. The kill
   happens *before* any spawn, so there is never more than one live process on
   the thread. Expect the shell call to be cut off mid-run — that is this codex
   being terminated on purpose, not an error.
2. If `t push` instead printed a fallback hint (it could not locate the process,
   or the hook never stamped this thread — `t doctor` says whether the hook is
   trusted), relay that hint and the attach command it printed (for example
   `t open api 3`).

Notes:
- If `t push` says "Already inside tmux", this session is already backgrounded —
  tell the user that and stop (no auto-exit happens in that case).
- To pull it back to a normal terminal later, that is `/tpop` (or `t pop`).
