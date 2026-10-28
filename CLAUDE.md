# Dotfiles architecture

Follow [AGENTS.md](AGENTS.md) for the worktree, PR, auto-merge, and rollout
workflow. Never develop or commit in the canonical checkout on `main`: it is
the live source for files linked into `$HOME`.

This repository owns personal zsh configuration, machine utilities, tmux and
clipboard setup, and PII scanning. The session manager
`t` lives in [agenthangar/t](https://github.com/agenthangar/t), with its own
canonical checkout, installer, zsh plugin, hooks, prompts, agent settings
engine, tests, and release process. Dotfiles installs and consumes it through
`lib/t-integration.sh`; do not add a second implementation here.

## Packaged runtime (default)

Homebrew distributes the complete runtime under its `opt/dotfiles/libexec` path.
The formula writes `.dotfiles-package` (that stable absolute path) and
`.dotfiles-version`. They are package metadata, never tracked in this repository.
No user-side checkout is required or created by the packaged command.

`bin/dots` recognizes that metadata before loading any existing shell functions.
`lib/package.sh` runs full account setup on first use, delegates updates to
`brew upgrade agenthangar/tap/dotfiles`, then execs the new command through the
stable path after Homebrew may have removed the old Cellar directory. `--relink`
is offline; `--all` fans out through the configured t hosts after local success.

`install.sh` skips Git resolution, migration, and repository configuration for
packages. It writes a real `.zshrc` loader, preserving existing regular contents
and backing up old checkout symlinks. The loader and managed utility links target
Homebrew's stable path. A real loader prevents t or another integration from
appending to files inside the Cellar. `~/.config/dotfiles/package` records the
runtime location; `installed` records completion of full account setup. Relinking
alone must not mark full setup complete. `.zshrc` reloads when the package version
changes and reads command help from the runtime, not the small loader.

Private configuration remains in `~/.zshrc.local` and other account-owned files.
Migrating to the package must not reset or delete an existing source checkout.
Package dependencies belong in the Homebrew formula; packaged setup does not
run `brew bundle`. Standalone t keeps its separate installation and updater.

Every runtime release needs a corresponding version/URL/SHA-256 bump in
`agenthangar/homebrew-tap` before rollout. Its formula tests must install the real
payload in a sandbox HOME with no checkout. Merge and monitor both repositories,
then use `dots --all` and inspect each configured host's result.

## Optional source development

Continue development in per-session worktrees, never canonical main. Source
installs without package metadata retain the legacy checkout workflow:
`./install.sh` selects canonical main, `DOTFILES_LINK_DEV=1` selects the worktree,
and `DOTFILES_LINKS_ONLY=1` reconciles links offline. Source-installed `dots`
fast-forwards canonical main only when clean and safe. Preserve its legacy layout
migration, salvage, and bundled-t cutover protections.

From a packaged installation, preview a source worktree with
`DOTFILES_LINK_DEV=1 DOTFILES_NO_BREW=1 ./install.sh`. Return via the explicit
Homebrew `dots install` command. `t` remains an independent installation.

## Standalone t bridge

`lib/t-integration.sh` validates a t installation with the
`.t-install-version` marker and required files. The full dotfiles installer
downloads the public t release bootstrap script to a temporary file; that
script verifies the release archive's SHA-256 checksum before installing under
`~/.local/share/t/releases/` (or `$T_INSTALL_DIR`). An existing valid checkout
stays selected. An explicit invalid `DOTFILES_T_HOME` is an error during setup or
migration. Only when the outgoing checkout still contains bundled `bin/t` and
the incoming release removes it does `_dots_t_preflight` install or repair
standalone t. If it fails, it stops while the outgoing
dotfiles code and links still work. A machine already running an intermediate
bridge with an older preflight can need one manual run of the public installer
before `dots`; no new dotfiles code can execute until that preflight passes.

Dotfiles sets `T_LOCAL_RC=~/.zshrc.local`, the personal permission-policy
directory in `agents/`, the preferred GitHub owner, automatic trust, and
legacy opt-out translations. It loads the validated installed
`t.plugin.zsh`, which sources the selected local file once and owns the
`DEV_REPOS` / `REMOTE_HOSTS` shortcuts and config cache. If t is missing,
`.zshrc` still loads local config for dotfiles utilities and the temporary
`t` function prints a repair hint. `bin/dots-sync` parses t's
`config.sh` cache as data; never source that file as shell code.

`~/.zshrc.local` and agent settings are private regular files, not tracked
symlinks. Dotfiles never replaces hand-written agent settings; t manages its
own additive hooks, MCP entries, trust, and permission policy. Keep the
personal policy files here and public defaults in t. Do not commit hosts,
tokens, transcripts, real settings, or the private PII denylist.

## What remains here

- `nosleep` and `sleep-manager`: macOS activity and sleep controls.
  `nosleep` recognizes agent processes itself, so it also works when t is
  missing.
- `clip-bridge`, `.tmux.conf`, and the SSH snippet: clipboard routing for
  remote tmux sessions. Sandbox tests must not start the user's live listener.
- `help`, `prview`, `pii-scan`, shell utilities, and local aliases. The PII
  scanner fails open locally without a denylist; configured and CI scans fail
  closed.
- `retire_pr_watch`: the cleanup guard for the retired autonomous fixer.
  Do not restore its binary or launchd timer.

Run `pytest`, `bash -n install.sh`, `zsh -n .zshrc`, and shellcheck for
changed shell scripts. Sandbox HOME, XDG directories, and tmux sockets.
The standalone t repository owns its Python coverage gate and its own tests.
