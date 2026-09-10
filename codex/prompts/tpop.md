---
description: Pull this tmux'd Codex session back to a foreground terminal (t pop)
---

The user wants to move this session **out of tmux** and back into a normal
foreground terminal (kill the tmux slot, resume the thread with `codex resume`
in a plain shell).

A Codex running inside tmux **cannot move itself** into the user's other
terminal — the foreground resume has to happen in a shell the user controls. So:

1. Check whether this session is actually in tmux: run `echo "$TMUX"` and
   `tmux display-message -p '#S' 2>/dev/null` with the shell tool.
   - If `$TMUX` is empty, this session is **not** in tmux — tell the user there
     is nothing to pop, and stop.
2. If it is in tmux, give the user the exact command to run **in a different,
   plain terminal** (not inside this tmux session):

   ```
   t pop <session-name>
   ```

   Fill in `<session-name>` from `tmux display-message -p '#S'` (for example
   `t pop dev-api-3`). `t pop` kills the tmux slot and resumes this exact thread
   in that terminal's foreground with `codex resume`.
3. Explain that once they run it, this in-tmux instance is terminated and the
   conversation continues in their foreground terminal.

Do not run `tmux kill-session` yourself — that would kill this session before
the user has resumed it elsewhere.
