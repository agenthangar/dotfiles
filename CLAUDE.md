# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

- **`bin/sleep-manager`** — `set -euo pipefail` Bash with a `case` dispatcher. Sleep blocking is two layers: `pmset -a disablesleep 1` (OS-level, survives logout/reboot) plus a backgrounded `caffeinate -dimsu` whose PID is tracked in `/tmp/sleep-manager-caffeinate.pid`. Key gotcha documented in its `--help`: the caffeinate process dies on reboot but the pmset flag does **not** — always run `enable` to fully restore; don't rely on a reboot. `status` is the source of truth for current state.
- **`.zshrc`** — PATH (`~/bin` first), then `compinit` is run **before** sourcing anything that calls `compdef` (the example completion) — order matters, this was a fix for a "command not found: compdef" error. Also defines `prview` (gh PR check summary via jq) and the interactive `nosleep` one-liner (the inline cousin of `sleep-manager`).
- **`claude/settings.json`** — symlinking this reproduces Claude Code setup on a new machine: `enabledPlugins`, `extraKnownMarketplaces`, and `permissions` are re-applied on first run (Claude re-clones marketplaces and reinstalls enabled plugins). MCP is *not* managed here: claude.ai connectors sync via the Anthropic account automatically, and local/stdio MCP servers live in the stateful, non-symlinked `~/.claude.json` (none today — sync via a merge step if added, never commit `~/.claude.json`).
