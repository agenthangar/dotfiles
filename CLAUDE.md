# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Workflow

After every change you make in this repo, commit and push it (directly to `main` — this is a personal single-author repo). No need to ask first.

## What this is

Personal macOS dotfiles: zsh config, utility scripts, and Claude Code config. No build system, no tests, no dependencies — just shell files and a Bash install script.

## The symlink model (read this first)

The files **in this repo are the source of truth**. `install.sh` creates symlinks from `$HOME` back into the repo, so the home-directory copies and the repo copies are literally the same file:

| Repo file | Symlinked to |
| --- | --- |
| `.zshrc` | `~/.zshrc` |
| `bin/sleep-manager` | `~/bin/sleep-manager` |
| `claude/settings.json` | `~/.claude/settings.json` |

Consequences:
- Editing either side edits both. There is no "sync" or "copy back" step.
- `install.sh` is location-independent (it resolves its own dir via `BASH_SOURCE`), so the repo can live anywhere; re-run it after moving the repo to relink.
- Running `install.sh` backs up any real file in the way to `<name>.bak` before linking, and `rm`s a pre-existing symlink. Adding a new managed file means adding a `link` call in `install.sh`.

## Commands

```sh
./install.sh            # link all dotfiles into $HOME (idempotent; re-run to relink)
sleep-manager status    # show pmset timeouts, active sleep assertions, script-managed caffeinate
sleep-manager disable   # block sleep (sudo): pmset disablesleep 1 + backgrounded caffeinate
sleep-manager enable    # restore sleep (sudo): kill caffeinate + reset pmset defaults
```

There is no lint/test step. After editing a shell file, validate by sourcing it (`source ~/.zshrc`) or running the script.

## Component notes

- **`bin/csync`** — `set -euo pipefail` Bash that two-way-merges Claude session history between `~/.claude/projects` and the iCloud Drive `claude-sessions` folder (`rsync -a --update`, no `--delete`, so machines converge to the union). Was a `.zshrc` function; extracted to a script (`help` still lists it — it scans `~/bin`). `brctl download` is wrapped in `|| true` so its exit status never aborts the sync under `set -e`. **Periodic runs come from a `precmd` hook in `.zshrc` (`_csync_periodic`), not launchd** — this is load-bearing and a dead end was hit getting there: iCloud Drive is TCC-protected and *background launchd agents are denied*, and on recent macOS (Tahoe) granting `/bin/bash` Full Disk Access has **no effect** (verified — even a pure-bash `readdir` is denied under a launchd agent), because TCC won't pin an FDA grant to a platform interpreter running an arbitrary script. The shell hook sidesteps this entirely by running `csync` in the Terminal's already-approved context. The hook gates on a `~/.cache/csync-last-run` stamp (written *before* the run so overlapping shells don't double-fire), uses `$EPOCHSECONDS` (no `date` fork per prompt), and launches `csync` detached (`( csync … & )`) so it never blocks the prompt; background runs log to `~/Library/Logs/csync.log`. Don't reintroduce a launchd agent for this without a code-signed helper binary.
- **`bin/sleep-manager`** — `set -euo pipefail` Bash with a `case` dispatcher. Sleep blocking is two layers: `pmset -a disablesleep 1` (OS-level, survives logout/reboot) plus a backgrounded `caffeinate -dimsu` whose PID is tracked in `/tmp/sleep-manager-caffeinate.pid`. Key gotcha documented in its `--help`: the caffeinate process dies on reboot but the pmset flag does **not** — always run `enable` to fully restore; don't rely on a reboot. `status` is the source of truth for current state.
- **`.zshrc`** — PATH (`~/bin` first), then `compinit` is run **before** sourcing anything that calls `compdef` (the example completion) — order matters, this was a fix for a "command not found: compdef" error. Also defines `prview` (gh PR check summary via jq) and the interactive `nosleep` one-liner (the inline cousin of `sleep-manager`).
- **`tfind` (in `.zshrc`)** — semantic search across saved Claude sessions: "which session was working on X?". Pipeline is **retrieve-then-rerank** — a keyword pass over each transcript's title + your prompts builds a candidate pool (the same scan `_claude_sessions_fzf` uses; padded with recent sessions when keyword matches are thin, so divergent wording still reaches the model), then **Sonnet via `claude -p --model sonnet --output-format json`** reranks the candidates and returns a JSON `[{i, why}]` ranking. Two load-bearing details: (1) the model call is a **Python `subprocess.run(['claude', …])`**, which execs the real `~/.local/bin/claude` binary and so **bypasses the zsh `claude` wrapper** (the wrapper's tpush sentinel would otherwise interfere); (2) **every failure path — no CLI, timeout, unparseable reply, empty `[]` — falls back to keyword order** so the fzf picker always populates and the command works offline. `tfind -k` forces the offline keyword path (no Sonnet). The keyword scorer (`hay.count(t) + 4*head.count(t)`) is recall-only here — Sonnet does the precision — so don't over-tune it.
- **`tbeam` (in `.zshrc`)** — teleports a Claude session to another machine (default `mini`) and resumes it there; a **remote `tpush`**. Resolves a session exactly like `tpush` (current chat via `$CLAUDE_CODE_SESSION_ID`, else fzf-pick scoped to `$PWD`, `-a` for all), `rsync`s its `~/.claude/projects/<enc>/` dir over (`_tbeam_sync_transcript`, `--update`/no-`--delete` so the live source wins but a newer remote is never clobbered — csync still reconciles in the background), then has the **host land it**. Landing logic (`_tbeam_land`) lives in the *shared* dotfiles so it runs on the far side via `ssh "$host" "TB_CWD=… TB_SID=… TB_MODE=… zsh -lic _tbeam_land"` — work passed in **`TB_*` env vars** (sidesteps nested-quote hell) and a **login+interactive** shell (`zsh -lic`) because Homebrew `tmux`/`fzf` are on the *login* PATH (`.zprofile`) while `claude` is on the *interactive* PATH — plain `ssh host '…'` sees neither. Default mode reuses the host's own `_dev_resume_session` to make a first-class `dev-<repo>-<slot>` (so `tgo`/`tread`/`tpop` work there), then auto-attaches via `ssh -t`; `-f` resumes in the ssh foreground, `-d` leaves it detached with an attach hint. Two load-bearing constraints: (1) the repo must exist at the **same path** on the host (all repos live in `~/code` on every machine) — `tbeam` guards with `ssh host test -d`; (2) from *inside* Claude it forces `-d` (a Bash subprocess has no TTY to `ssh -t` into). `_tbeam_land` is shared code, so after adding/changing it run `dots` on the mini to pull it.
- **`claude/settings.json`** — symlinking this reproduces Claude Code setup on a new machine: `enabledPlugins`, `extraKnownMarketplaces`, and `permissions` are re-applied on first run (Claude re-clones marketplaces and reinstalls enabled plugins). MCP is *not* managed here: claude.ai connectors sync via the Anthropic account automatically, and local/stdio MCP servers live in the stateful, non-symlinked `~/.claude.json` (none today — sync via a merge step if added, never commit `~/.claude.json`).
