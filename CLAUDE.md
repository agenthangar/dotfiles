# Dotfiles architecture

Follow [AGENTS.md](AGENTS.md) for the worktree, PR, auto-merge, and rollout
workflow. Never develop or commit in the canonical checkout on `main`: it is
the live source for files linked into `$HOME`.

This repository owns personal zsh configuration, machine utilities, tmux and
clipboard setup, optional iCloud sync, and PII scanning. The session manager
`t` lives in [agenthangar/t](https://github.com/agenthangar/t), with its own
canonical checkout, installer, zsh plugin, hooks, prompts, agent settings
engine, tests, and release process. Dotfiles installs and consumes it through
`lib/t-integration.sh`; do not add a second implementation here.

## Live checkouts

| Checkout | Role |
| --- | --- |
| `~/code/dotfiles` on `main` | Canonical dotfiles checkout and usual live target of `~/.zshrc` and other managed links |
| `~/code/t` on `main` | Canonical standalone t checkout; its installer owns `~/bin/t`, hook/transfer links, and agent integrations |
| `~/code/.worktrees/<repo>/<slot>` | Per-session development work; never use a canonical checkout for a task |

`dots --dev` temporarily points dotfiles links at the dotfiles worktree in
`$PWD`. `t update --dev` independently selects a t worktree. Plain `dots`
returns dotfiles to canonical `main` and invokes `command t update` before
reloading the shell, returning t to its canonical `main`. `dots --relink`
only reconciles current links and leaves the selected t source alone.

These are separate live surfaces. `_dots_live_tree` resolves the dotfiles
target from the actual `~/.zshrc` link. The dotfiles adapter exports that
resolved source as `T_DOTFILES_LIVE_TREE`; t's own worktree guard protects
both its selected and canonical code trees as well as that dotfiles tree.
The dotfiles pre-commit hook refuses a commit on canonical `main`.

## What `dots` must preserve

`dots` resolves both the current live tree and canonical checkout, fetches
`origin/main`, fast-forwards only when safe, reconciles links, reapplies
`~/.tmux.conf` to an existing tmux server, and reloads `~/.zshrc`. A dirty,
diverged, or non-main canonical tree is reported rather than reset. On
`--all`, the local run finishes before `bin/dots-sync` fans out to configured
hosts; inspect each host result because one unavailable host does not imply
success.

`dots --dev` accepts only a real dotfiles session worktree containing a
runnable installer. It refuses the canonical checkout and other repositories.
`DOTFILES_LINK_DEV=1` selects that worktree; an ordinary install or update
selects the canonical checkout. `DOTFILES_LINKS_ONLY=1` stays offline and
relinks from the selected source without running full setup. Keep new dotfiles
links inside `link_all()` so both full installs and link reconciliation see
them. `DOTFILES_NO_TMUX=1` prevents sandbox install tests from touching a
real tmux server through an inherited socket.

An older two-tree layout used a separate `main` worktree at
`~/.local/share/dotfiles-main` (or `$DOTFILES_MAIN_WT`). The migration in
`install.sh` must keep the old linked tree readable until the new links are
ready: salvage edits and untracked files, detach holders of `main`, move
`main` to the canonical checkout, fast-forward, relink, then clean up the
legacy worktree last. Its capability probe prevents an old released
`install.sh` from attempting that migration during a `--dev` preview.
Do not replace this sequence with a destructive worktree removal or reset.

## Standalone t bridge

`lib/t-integration.sh` validates a t checkout with the
`.t-install-version` marker and required files. The full dotfiles installer
can bootstrap `~/code/t`; links-only refresh remains offline. Before
fast-forwarding to a release without bundled t, `_dots_t_preflight`
installs or repairs standalone t. If that fails, it stops while the outgoing
dotfiles code and links still work. A recovery path is the absolute
`~/code/t/install.sh`, followed by `dots`.

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
- `csync` and `_csync_periodic`: optional iCloud transcript sync. Preserve
  origin stamps and newest-file behavior; do not sync auth or live SQLite
  databases.
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
