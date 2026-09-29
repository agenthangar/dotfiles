# dotfiles

Personal macOS and zsh configuration: shell utilities, machine sync, clipboard
bridging, sleep management, and PII scanning. Linux machines are supported as
remote nodes; macOS-only features skip themselves.

The coding-agent session toolkit now lives in **[agenthangar/t](https://github.com/agenthangar/t)**.
Install t independently for sessions, worktrees, remote handoff, setup/config,
agent settings, and desktop handoff. This repository consumes that project.

## Everyday commands

| Command | Purpose |
| --- | --- |
| `dots` | Update canonical dotfiles main and standalone t, reconcile links, reload |
| `dots --all` | Update here, then run dots on every configured remote host |
| `dots --dev` | Make the dotfiles session worktree you are standing in live |
| `dots --relink` | Reconcile the selected dotfiles links without fetching |
| `help` / `h` | List shell and installed commands |
| `csync` | Optional two-way iCloud sync of agent transcripts and plans |
| `prview` | Inspect a pull request from the terminal |
| `nosleep` | Keep the Mac awake while work is active |
| `sleep-manager` | Explicit macOS sleep control |
| `clip-bridge` | Send copies from remote tmux back to the Mac you are using |
| `pii-scan` | Scan tracked or staged files with a private identifier denylist |

## Install

```sh
git clone https://github.com/agenthangar/dotfiles.git ~/code/dotfiles
~/code/dotfiles/install.sh
```

The installer keeps the canonical clone on `main`, links its managed files into
HOME, and bootstraps `https://github.com/agenthangar/t.git` into `~/code/t` when
needed. t owns its executable links, prompts, and additive agent integrations.
Homebrew tools come from [Brewfile](Brewfile); the brew step skips on other platforms.

Existing shell files are backed up before linking. SSH config is preserved: an
Include for this repository's snippet is appended rather than replacing the file.
Machine configuration is a private regular file at `~/.zshrc.local`.

`DOTFILES_NO_T=1` skips standalone t setup for shell-only or sandbox installations.
`DOTFILES_T_HOME` selects a different supported t checkout. A normal offline
links-only refresh never clones anything; use full `install.sh` for first setup.

## Two repositories, independent live checkouts

| Source | Managed surface |
| --- | --- |
| `~/code/dotfiles` on main | `~/.zshrc`, `~/.tmux.conf`, personal utility bins, SSH snippet |
| `~/code/t` on main | `~/bin/t`, hook/transfer helpers, t prompts and shell plugin |
| `~/code/.worktrees/<repo>/<slot>` | Isolated development, never canonical main |

The `$HOME` symlinks point into the canonical clones. Editing a development
worktree is not live until its PR merges and you update, or you explicitly select
it with `dots --dev` / `t update --dev`. Ordinary `dots` returns dotfiles and t to
their released main branches. `dots --dev` and `--relink` leave t's selection alone.
An ordinary `t update` returns just t to main and preserves dirty worktree edits.

`dots --all` is the rollout step after a merged PR; nothing polls GitHub for merges.
Inspect its per-host results: an unreachable host is reported and must be retried.
Open shells reload when the selected code changes. Never develop or commit in a
canonical main checkout: those files are your running configuration.

The t migration bridge validates `.t-install-version` before allowing t to own
its links. Before pulling a dotfiles release that removes bundled t, it confirms
the standalone installation works. If it cannot, it stops before removing the
old executable. Recovery does not require a working command: invoke
`T_LOCAL_RC="$HOME/.zshrc.local" ~/code/t/install.sh` directly, then rerun dots.

## Private configuration and policy

The t plugin reads `~/.zshrc.local` on this installation. `t setup` registers repos
and SSH hosts; `t config` manages tools/models and session settings. See
[t's configuration guide](https://github.com/agenthangar/t#configuration).

`lib/t-integration.sh` preserves this dotfiles setup's existing behavior: the
personal `agents/permissions.allow` and `.retire` policy, automatic trust for
registered repos, and agent mode/subagent defaults. Standalone t installations
have separate opt-in defaults. Existing `DOTFILES_NO_MCP`, `DOTFILES_NO_TRUST`,
`DOTFILES_NO_PERMISSIONS`, `DOTFILES_NO_AGENT_MODES`, `DOTFILES_NO_SUBAGENT_MODEL`,
and `DOTFILES_NO_CODEX_HOOKS` switches map to the corresponding t switches.
`DOTFILES_T_PERMISSIONS_DIR` can override the personal policy directory.

Do not commit real hosts, repositories, tokens, agent settings, or transcripts.
`~/.claude/settings.json`, agent auth, and the t config files stay per-machine.

## PII guard

`bin/pii-scan` uses a private denylist in `~/.config/pii-scan/scrub-rules.json`
(or `PII_RULES`) plus tracked false-positive patterns and a repository allowlist.
It scans staged blobs from the pre-commit hook and tracked files otherwise. Locally
it warns and skips when no denylist is present; `--require-rules` fails closed.
CI materializes the private `PII_SCRUB_RULES` secret and runs that stricter mode.
The denylist is never committed. Add intended public exceptions to the tracked
allowlist rather than weakening the private denylist.

## Tests

```sh
python3 -m pip install -r requirements-dev.txt
python3 -m pytest
bash -n install.sh
zsh -n .zshrc
```

Tests exercise the shell utilities and installation/migration under sandbox HOME,
XDG directories, and tmux sockets. CI also runs shellcheck, gitleaks, the private
PII scan, and dependency audit behind the required `CI` gate. The Python t suite
and its 97% coverage requirement moved to the standalone repository.

MIT — [LICENSE](LICENSE). This remains a personal setup; [contributing](CONTRIBUTING.md).
