---
description: Pull this tmux'd Claude session back to a foreground terminal
---

The user wants to migrate this session **out of tmux** and back into a normal
foreground terminal (kill the tmux session, resume the conversation with
`claude -r` in a plain shell).

A Claude running inside tmux **cannot move itself** into the user's other
terminal — the foreground resume has to happen in a shell the user controls. So:

1. Check whether this session is actually in tmux: run `echo "$TMUX"` and
   `tmux display-message -p '#S' 2>/dev/null` in the Bash tool.
   - If `$TMUX` is empty, this session is **not** in tmux — tell the user there's
     nothing to pop, and stop.
2. If it is in tmux, give the user the exact command to run **in a different,
   plain terminal** (not inside this tmux session):

   ```
   t pop <session-name>
   ```

   Fill in `<session-name>` with the value from `tmux display-message -p '#S'`
   (e.g. `t pop dev-api-3`). `t pop` kills the tmux session and resumes this exact
   conversation in that terminal's foreground.
3. Explain that once they run it, this in-tmux instance will be terminated and
   the conversation continues in their foreground terminal.

Do not run `tmux kill-session` yourself — that would kill this session before
the user has resumed it elsewhere.
