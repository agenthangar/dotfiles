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
| `dots` | Set up a new account, or update an existing installation |
| `dots install` | Install dotfiles into this account |
| `dots update` / `dots` | Update dotfiles, reconcile its links, reload |
| `t update` | Update the independently installed session toolkit |
| `dots --all` | Update here, then run dots on every configured remote host |
| `dots --relink` | Reapply installed configuration without network access |
| `dots --relink` | Reconcile the selected dotfiles links without fetching |
| `help` / `h` | List shell and installed commands |
| `prview` | Inspect a pull request from the terminal |
| `nosleep` | Keep the Mac awake while work is active |
| `sleep-manager` | Explicit macOS sleep control |
| `clip-bridge` | Send copies from remote tmux back to the Mac you are using |
| `pii-scan` | Scan tracked or staged files with a private identifier denylist |

## Sleep and connection recovery

`nosleep` is now installed alongside [t](https://github.com/agenthangar/t).
The command name, manual start, flags, saved defaults under
`${XDG_CONFIG_HOME:-~/.config}/nosleep/config.json`, and local Keychain credentials
are unchanged. Run `nosleep --help`, `nosleep config`, or `nosleep config --show`.
See the [nosleep guide](https://github.com/agenthangar/t/blob/main/docs/nosleep.md).
Dotfiles delegates to the same runtime; it no longer maintains a second copy.
`sleep-manager` remains a dotfiles utility with its existing explicit controls.

Update t before updating from a dotfiles version that bundles nosleep. The source
migration preflight checks the replacement before fast-forwarding; a missing or
older t stops the cutover and explains how to update it. Versions predating this
preflight need t updated first: new guards cannot run inside an already-loaded
older updater. Packaged installs require the t release containing nosleep.
`DOTFILES_NO_T=1` still opts out of t installation; nosleep then requires a separate
installation on PATH. Loading the shell never starts a sleep monitor.

Full interactive macOS installs retain the optional, narrowly scoped dotfiles
sleep sudo rule for nosleep and sleep-manager. Existing grants remain valid;
this migration does not create or broaden them. `DOTFILES_NO_SUDO=1` skips setup.

## Install

```sh
brew tap agenthangar/tap
brew install dots
dots
```

Homebrew installs the complete configuration, utilities, and templates. No Git
checkout is required. The first `dots` applies that installed package to your
account; subsequent `dots` or `dots update` commands upgrade it through Homebrew
and reconcile your configuration. `dots --relink` applies the installed files
without network access. `dots install` reruns full account setup, including the
optional sudo-trust prompt. Homebrew manages the runtime dependencies.

After setup, open a new shell or run `source ~/.zshrc`. Your `.zshrc` is a real
file containing a small loader; it sources the package through Homebrew's stable
`opt` path. Managed utility links use the same stable path, so Homebrew can remove
old package versions safely. Local settings remain in `~/.zshrc.local` and other
private configuration files. Existing SSH configuration is preserved.

To migrate an older checkout-based installation, upgrade the package and invoke
its command explicitly once (an older shell function may otherwise take priority):

```sh
brew upgrade agenthangar/tap/dotfiles
"$(brew --prefix agenthangar/tap/dotfiles)/bin/dots" install
source ~/.zshrc
```

The old `.zshrc` symlink is backed up before creating the loader. Existing regular
shell files keep their contents. The old repository and its local edits are left
in place, but are no longer needed to run or update dotfiles. The package does not
accept a checkout destination. `dotfiles-setup` is an alias for `dots install`.

`brew upgrade dots` updates the same runtime used by `dots`; `dots --relink`
reconciles any new managed links afterward. `brew uninstall dots` removes the
runtime but leaves your private settings and loader in place.

The full installer also configures the standalone [t release](https://github.com/agenthangar/t/releases)
when needed, without requiring a t checkout. Existing supported t installations
are preserved. `DOTFILES_NO_T=1` skips that integration; `DOTFILES_T_HOME` selects
an existing t installation and `T_INSTALL_DIR` can move its release directory.
Routine dotfiles updates leave t's installation alone.

## Distribution and development

| Source | Managed surface |
| --- | --- |
| Homebrew dotfiles package | Shell configuration, utility bins, tmux and SSH snippets |
| Standalone t installation | t command, hooks, prompts and shell plugin |
| Optional dotfiles checkout/worktrees | Source development only |

`dots --all` updates locally and on the configured t hosts. Inspect every host's
result. Open shells reload when the installed package version changes.

Developers may still install from a Git checkout using `./install.sh`; that legacy
mode retains its canonical-main and worktree protections. To preview a development
worktree from a package-based installation, run
`DOTFILES_LINK_DEV=1 DOTFILES_NO_BREW=1 ./install.sh` there. Return to the package
with the explicit Homebrew `dots install` command above. Checkouts are optional,
never created by the packaged command.

Runtime changes must be released in the
[Homebrew tap](https://github.com/agenthangar/homebrew-tap) with a new formula
version and verified source archive digest before `dots --all` can distribute them.

## Private configuration and policy

The t plugin reads `~/.zshrc.local` on this installation. `t setup` registers repos
and SSH hosts; `t config` manages tools/models and session settings. See
[t's configuration guide](https://github.com/agenthangar/t#configuration).

Shell setup adds `~/code/personal-scripts` to PATH when that directory exists.
Set `DOTFILES_SCRIPTS_DIR` in `~/.zshrc.local` to use a different directory, or
set it to an empty string to disable the addition. This applies in new shells
and when `dots` reloads the configuration; repeated reloads do not add duplicates.
The setting controls additions and does not remove an entry already in PATH.

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
XDG directories, and tmux sockets. The standalone t repository owns nosleep tests,
including opt-in native macOS assertion checks. CI runs shellcheck,
gitleaks, the private PII scan, and dependency audit behind the required `CI` gate.
The Python t suite and its 97% coverage requirement moved to the standalone repository.

MIT — [LICENSE](LICENSE). This remains a personal setup; [contributing](CONTRIBUTING.md).
