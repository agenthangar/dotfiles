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
| `dots` | Update dotfiles, reconcile its links, reload |
| `t update` | Update the independently installed session toolkit |
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

## Sleep and connection recovery

```sh
nosleep                       # keep awake; closing the laptop locks the screen
nosleep --no-lock              # keep unlocked when the laptop closes; dim display
nosleep --no-lock --forever    # keep awake and unlocked until Ctrl-C
```

`--no-lock` preserves an already-unlocked session; it cannot unlock an existing
lock. `-d` and `--dim` still work as compatibility aliases. `nosleep --help`
shows the short option reference.

`nosleep` shows a live snapshot instead of a long startup message:

```text
nosleep

Live status
  Agents    Sampling activity… need a second check
  Internet  Online · checked 0s ago
  Next      Check in 30s

Configuration
            Current                  │ Change on next run
  Sleep     Blocked                  │ Ctrl-C to release
  Display   On; off when closed      │ --no-lock to dim
  Dim       Off                      │ --no-lock to enable
  Locking   When closed              │ --no-lock to disable

  Mode      Auto sleep (lid closed)  │ --forever for manual stop
  Grace     15m idle/offline         │ --grace <seconds>
  Checks    30s                      │ --every <seconds>
  Retries   3                        │ --retries <count>
  Backoff   30s, doubles to 5m       │ --backoff <seconds>

  Stop      Ctrl-C · release sleep hold
```

Live status refreshes every two seconds with current activity and connection checks
and a countdown to the next check. Configuration shows the sleep, display, dimming, and
locking behavior selected for this run. An aligned, muted Change column keeps each
helper beside its setting. Timing controls have individual rows; restart with the
shown flag to change them. In `--forever` mode, unused timing controls are marked inactive.
Agents shows activity observed at the last check, then switches to quiet
with a last-seen time when activity stops. Internet shows the latest connection
result; Next shows remaining grace or the pending retry when needed. Failures to
hold sleep off or dim the display appear as a Notice when relevant.
`--forever` explicitly marks agent and internet checks as disabled. Redirected
output and basic terminals get plain snapshots at each check, without cursor codes.
Interactive terminals use green for healthy states, amber for sampling, quiet
periods, grace and retries, and red for lost connectivity or sleep-protection
warnings. Bold labels and muted helper text keep the current state prominent.
Use `NO_COLOR=1 nosleep` for an unstyled snapshot with live updates.

While the laptop is open, `nosleep` keeps the display on and inhibits idle locking,
so an unlocked session can stay unlocked. It never unlocks an existing lock.
Closing the laptop normally requests a screen lock and turns the display off;
opening it restores the display hold. Unlocking is manual.
`nosleep --no-lock` instead dims the built-in display and requests no lock, keeping
an already-unlocked session available. Docked clamshell mode keeps the external
display on and skips the lock/dim action when you close the laptop. The Configuration
rows show the selected display, dimming, and locking behavior alongside the sleep policy.

`nosleep` keeps the Mac awake until Ctrl-C while the laptop is open. Failed activity
or internet checks only produce warnings in that state. Automatic sleep is allowed
only when the laptop is closed and activity or internet connectivity remains absent
through the 15-minute grace period and three extra checks (after 30, 60, and 120 seconds).
Closing the laptop still locks or dims its display during retries.
Opening the laptop cancels pending retries; closing it starts a fresh retry
budget. Recovery resets the retry budget; a restored connection also restarts
activity sampling and grace so an agent has time to resume.

```sh
nosleep --grace 900 --retries 3 --backoff 30
```

`--every` sets the normal check interval (30 seconds). `--backoff` sets the first
retry delay (1–300 seconds), which doubles up to five minutes; `--retries` accepts
0–10. `--forever` holds until Ctrl-C. Connectivity checks try both Anthropic and
OpenAI, so a single provider being unreachable does not mean the network is down.

For native reconnection, enable **System Settings → Wi-Fi → Details → Automatically
join this network** for each trusted Wi-Fi network. On macOS Tahoe 26 or later,
set **Wi-Fi → Ask to join hotspots → Automatic**. Keep the nearby iPhone/iPad's
Wi-Fi and Bluetooth on and use the same Apple Account (or configured Family
Sharing), with a cellular plan that supports Personal Hotspot. See Apple's
[Wi-Fi settings](https://support.apple.com/guide/mac-help/mh11935/mac) and
[Instant Hotspot guide](https://support.apple.com/en-us/109321).

macOS attempts auto-join while `nosleep` waits. Hotspot auto-join applies when no
known Wi-Fi is available; it may not leave connected Wi-Fi whose internet uplink
is broken. `nosleep` does not toggle Wi-Fi, edit saved networks, or store passwords.
These auto-join settings are per-machine and are not changed by `dots --all`.

## Install

```sh
git clone https://github.com/agenthangar/dotfiles.git ~/code/dotfiles
~/code/dotfiles/install.sh
```

The installer keeps the canonical clone on `main`, links its managed files into
HOME, and installs the latest verified [t release](https://github.com/agenthangar/t/releases)
when needed. The release lives under `~/.local/share/t/releases/` by default;
an existing supported t checkout is preserved. t owns its executable links,
prompts, and additive agent integrations.
Homebrew tools come from [Brewfile](Brewfile); the brew step skips on other platforms.

Existing shell files are backed up before linking. SSH config is preserved: an
Include for this repository's snippet is appended rather than replacing the file.
Machine configuration is a private regular file at `~/.zshrc.local`.

`DOTFILES_NO_T=1` skips standalone t setup for shell-only or sandbox installations.
`DOTFILES_T_HOME` selects an existing supported t installation. A normal
links-only refresh stays offline; the sole exception migrates a dangling
`~/bin/t` link owned by the former bundled dotfiles command to a verified t
release. Use full `install.sh` for first setup. `T_INSTALL_DIR` can move the
release installation base.

## Two repositories, independent installations

| Source | Managed surface |
| --- | --- |
| `~/code/dotfiles` on main | `~/.zshrc`, `~/.tmux.conf`, personal utility bins, SSH snippet |
| Selected t release or `~/code/t` checkout | `~/bin/t`, hook/transfer helpers, t prompts and shell plugin |
| `~/code/.worktrees/<repo>/<slot>` | Isolated development, never canonical main |

The `$HOME` symlinks point into the selected installations. Editing a development
worktree is not live until its PR merges and you update, or you explicitly select
it with `dots --dev` / `t update --dev`. Ordinary `dots` updates dotfiles only;
standalone t keeps its selected version and owns its updates. Run `t update`
separately to refresh its release or checkout while preserving dirty worktree edits.

`dots --all` is the rollout step after a merged PR; nothing polls GitHub for merges.
Inspect its per-host results: an unreachable host is reported and must be retried.
Open shells reload when the selected code changes. Never develop or commit in a
canonical main checkout: those files are your running configuration.

The t migration bridge validates `.t-install-version` before allowing t to own
its links. Before pulling a dotfiles release that removes bundled t, it confirms
the standalone installation works. If it cannot, it stops before removing the
old executable. Recovery does not require a working command: run the public
[`t` release installer](https://github.com/agenthangar/t#install), then rerun
`dots`. A machine running an intermediate dotfiles bridge that refuses its
preflight before updating also needs this one-time installation. Once migrated,
normal `dots` updates and relinks do not run the t installer, `t update`, or Homebrew.

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
XDG directories, and tmux sockets. A macOS CI job also creates real sleep assertions
and checks `nosleep` against live `pmset` output on PRs and weekly. CI runs shellcheck,
gitleaks, the private PII scan, and dependency audit behind the required `CI` gate.
The Python t suite and its 97% coverage requirement moved to the standalone repository.

MIT — [LICENSE](LICENSE). This remains a personal setup; [contributing](CONTRIBUTING.md).
