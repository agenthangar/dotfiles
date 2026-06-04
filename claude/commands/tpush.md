---
description: Push this Claude session into a detached background tmux session
---

Push the **current** Claude session into a detached tmux session so it keeps
running in the background and can be managed with the `dev`/`tgo`/`dev list`
tooling. This is the CLI `tpush` command, run on your behalf.

Do this:

1. Run `tpush` in the Bash tool. It auto-detects the current session via
   `$CLAUDE_CODE_SESSION_ID` and `$PWD`, picks a `dev-<repo>-<slot>` name, writes
   the wrapper's resume sentinel, and then **signals this foreground `claude` to
   exit automatically** — you do NOT need to tell the user to type `/exit`. On
   that exit the `claude()` wrapper spawns the detached `claude -r` and drops the
   user straight into the tmux pane. Killing happens *before* any spawn, so there
   is never more than one live process on the transcript (no-divergence
   invariant). Expect the Bash call to be cut off mid-run — that's this Claude
   being terminated on purpose, not an error.
2. Because this session is being killed, there's usually no chance to report
   back. If `tpush` instead printed a fallback hint (it couldn't locate the
   process, so it asked for a manual `/exit`), relay that and the attach command
   it printed (e.g. `tgo ff 3` or `tmux attach -t dev-dotfiles-1`).

Notes:
- If `tpush` says "Already inside tmux", this session is already backgrounded —
  just tell the user that and stop (no auto-exit happens in that case).
- To later pull it back to a normal terminal, that's `/tpop` (or the `tpop`
  shell command).
