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
| `csync` | Optional two-way iCloud sync of agent transcripts and plans |
| `prview` | Inspect a pull request from the terminal |
| `nosleep` | Keep the Mac awake while work is active |
| `sleep-manager` | Explicit macOS sleep control |
| `clip-bridge` | Send copies from remote tmux back to the Mac you are using |
| `pii-scan` | Scan tracked or staged files with a private identifier denylist |

## Sleep and connection recovery

Full interactive macOS installs ask whether to trust dotfiles sleep controls
with persistent passwordless sudo (default: No). Accepting allows only the exact
`pmset` commands used to disable sleep, re-enable sleep, and restore
`sleep-manager` defaults. Programs running as your account can also use these
permissions. The installer validates the rule with `visudo` and installs it as
a root-owned file at `/etc/sudoers.d/dotfiles-sleep-$(id -u)`; removing that file
with sudo revokes the grant. Authentication may be requested during setup.
Declining leaves the usual password prompts in place. Unattended installs,
Linux installs, and links-only updates skip this prompt; `DOTFILES_NO_SUDO=1`
also skips it. Re-running the installer leaves an identical rule alone.

```sh
nosleep                       # lock and turn the display off on lid close
nosleep -d on                  # display on, normal brightness
nosleep -d dim -l off          # dim without requesting a lock
nosleep -c none                # keep awake until Ctrl-C; skip all checks
nosleep -d dim -l off -c agent # dim, no lock, check only agent activity
nosleep -c internet            # check only internet connectivity
nosleep --require one          # keep awake while busy OR online
nosleep --require 2            # require both enabled checks to pass
nosleep config                # arrow-key menu for this machine's defaults
nosleep config --show          # print effective defaults
nosleep config --edit          # edit defaults with VISUAL/EDITOR
```

Related choices use one mode flag per setting:

| Setting | Flag | Modes |
| --- | --- | --- |
| Display on lid close | `-d`, `--display` | `off`, `on`, `dim` |
| Lock on lid close | `-l`, `--lock` | `on`, `off` |
| Automatic checks | `-c`, `--checks` | `all`, `agent`, `internet`, `none` |

Use a separate value or `=`: `-d dim`, `-d=dim`, and `--display=dim` are equivalent.
A display mode replaces the whole display choice: `on` clears saved or earlier
dimming, `off` turns the display off, and `dim` keeps it awake and dimmed.
`--checks agent` selects only agent activity, replacing saved check choices and
clearing forever; `none` disables both checks and holds until Ctrl-C.
Repeating a mode applies the last selection. Display and locking remain independent.
`--lock off` skips nosleep's lock request; it never unlocks an existing lock or
changes macOS's own automatic locking settings. Dimming only changes brightness.

Check settings remain separate: `-e` / `--every`, `-g` / `--grace`,
`-r` / `--retries`, and `-b` / `--backoff`, each taking a number.
Only enabled checks can trigger retries or allow automatic sleep. Lid/display
handling, sleep protection, sudo refresh, and cleanup continue with checks disabled.

Existing switches and groups still work. `-mLI` means dim, no lock, and no internet
check; `-dML` means display on, no saved dimming, and no lock. Bare `-d` preserves
saved dimming, while `-d on` explicitly clears it. Bare `-l` / `--lock` enables
locking, and bare `-c` / `--checks` enables both checks and clears forever.
The individual `-a` / `-A` and `-i` / `-I` check toggles are also retained.
`-f` / `--forever` and `-C` / `--no-checks` override individual check settings;
`-F` / `--no-forever` resumes the selected checks.

Grouped numeric options accept attached or separate values: `-mLIe10` and
`-mLIe 10` both use a 10-second interval. Mode values need a separate argument
or `=`: `-Ld=dim` and `-Ld dim` combine no-lock with display dimming.
Put further flags in a new argument after a value. The legacy `-dim` spelling
still means only `--dim`; it does not enable the internet check.

Among legacy display switches, `-m` / `--dim` / `-dim` wins regardless of order.
`-M` / `--undim` clears saved dimming without changing the display mode;
`-D` / `--display-off` / `--no-dim` turns the display off. A later
`--display MODE` replaces the whole choice, including an earlier explicit dim
request. A later legacy `--dim` requests dimming again.

`nosleep config` follows `t config`: select a setting with arrow keys, stage
changes, then review and save. Cancel leaves defaults unchanged. Defaults live
in `${XDG_CONFIG_HOME:-~/.config}/nosleep/config.json`, apply to the next run,
and stay local to that machine. Command-line flags override them for one run.
The display setting cycles through Off, On, and Dim (`display_on` and `dim`
in JSON); dimming implies display-on. Use `--display-off` to override saved
dimming, or `--display-off -d` to keep the display on without saved dimming.
An explicit `--dim` still takes precedence over legacy overrides; `--display on`
or `--display off` replaces it. Use `--lock on` /
`--no-forever` to override the other modes.
Timing defaults use `--grace`, `--every`, `--retries`, and
`--backoff`. `nosleep --help` highlights options that change the built-in defaults.
Configuration commands never change power settings or an already-running instance.
The menu groups display and locking under **On close**, the automatic-check
switch and individual agent/internet toggles under **Checks**, and their
interval, grace threshold, retry limit, and backoff under **Check settings**.
The toggles are `agent` and `internet` booleans in JSON, both defaulting to true.
Automatic checks can be disabled without forgetting those preferences.
Help, saved defaults, and live status use the same separation. Check settings
are marked inactive when neither check runs.

`nosleep` shows a live snapshot instead of a long startup message:

```text
nosleep

Live status
  Sleep     Prevented
  Agents    Sampling activity… need a second check
  Internet  Online · checked 0s ago
  Next      Check in 30s

On close
  Display   Off
  Locking   Lock screen

Checks
  Mode      Activity and internet checks

Check settings
  Interval  30s
  Grace     15m before retrying
  Retries   3
  Backoff   30s, doubles to 5m

  Options   nosleep --help · nosleep config
  Stop      Ctrl-C
```

Live status refreshes every two seconds with current activity and connection checks
and a countdown to the next check. Sleep reports whether sleep prevention is
active. On close shows the selected display and locking behavior. Checks
shows the monitoring mode; Check settings shows its timing and retry thresholds.
Use `nosleep --help` for one-run
options or `nosleep config` to save defaults. In `--forever` mode, unused timing
controls are marked inactive.
Agents shows activity observed at the last check, then switches to quiet
with a last-seen time when activity stops. Internet shows the latest connection
result; Next shows remaining grace or the pending retry when needed. Failures to
hold sleep off, lock, dim, or restore brightness appear as a Notice when relevant.
`--forever` explicitly marks agent and internet checks as disabled. Redirected
output and basic terminals get plain snapshots at each check, without cursor codes.
Interactive terminals use green for healthy states, amber for sampling, quiet
periods, grace and retries, and red for lost connectivity or sleep-protection
warnings. Bold labels, cyan section headings, and a muted footer keep the current
state prominent.
Use `NO_COLOR=1 nosleep` for an unstyled snapshot with live updates.

While the laptop is open, `nosleep` keeps the display on and inhibits idle locking,
so an unlocked session can stay unlocked. It never unlocks an existing lock.
Closing the laptop normally requests a screen lock and turns the display off;
opening it restores the display hold. Unlocking is manual.
`nosleep --display-on` keeps the display awake on close without dimming;
`nosleep --dim` keeps it awake and dims the built-in panel. The independent
locking setting controls whether it also requests a lock; `--no-lock` skips it. Docked clamshell mode keeps the external
display on and skips the lock/dim action when you close the laptop. The On close
section shows the selected display and locking behavior.

`nosleep` keeps the Mac awake until Ctrl-C while the laptop is open. Failed activity
or internet checks only produce warnings in that state. Automatic sleep is allowed
only when the laptop is closed and, by default, activity or internet connectivity remains absent
through the 15-minute grace period and three extra checks (after 30, 60, and 120 seconds).
Closing the laptop still locks or dims its display during retries.
Opening the laptop cancels pending retries; closing it starts a fresh retry
budget. Recovery resets the retry budget; a restored connection also restarts
activity sampling and grace so an agent has time to resume.

```sh
nosleep --grace 900 --retries 3 --backoff 30
```

`--require all` (the default) keeps awake while all enabled checks pass.
`--require one` keeps awake while any enabled check passes; `--require N`
requires at least N enabled checks to pass. Passing means agent activity is busy
or internet connectivity is online. Each failing check retains its grace period
(and activity needs two samples) before it counts against the requirement.
All enabled checks continue to run, even after enough pass. Counts must be positive
and cannot exceed the final enabled check count when monitoring is active.
The requirement is a one-run CLI option; it is inactive with `--checks none` or
`--forever`, which keep awake until Ctrl-C.

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
is broken. `nosleep` does not toggle Wi-Fi or change those auto-join settings.
These auto-join settings are per-machine and are not changed by `dots --all`.

For an explicit fallback to an already broadcasting iPhone hotspot, run this once
in an unlocked terminal on each Mac:

```sh
nosleep hotspot setup
nosleep --hotspot-after 300     # five minutes of continuous internet outage
nosleep --no-hotspot            # disable the saved fallback for this run
```

Setup asks for the exact Wi-Fi name and lets macOS `security` prompt for the
password locally. It saves the password in this Mac's Keychain and enables the
machine's private configuration only after setup succeeds. Passwords never appear
in shell arguments, logs, or the JSON config. `nosleep config` can change the
fallback toggle, Wi-Fi name, delay and attempt limit; changing the Wi-Fi name may
require running setup again for its credentials. Flags override saved settings:
`--hotspot`, `--no-hotspot`, `--hotspot-ssid NAME`, `--hotspot-after SECONDS`, and
`--hotspot-timeout SECONDS` (1–300, default 30).

After the delay, `nosleep` makes one directed join to that exact SSID with
`networksetup`, even if the current Wi-Fi uplink is broken. This can work with the
lid closed when the hotspot is advertising, Wi-Fi is already on, Keychain is
accessible and macOS permits the Wi-Fi change. It does not scan all nearby phones
or wake a dormant Instant Hotspot. Turn on Personal Hotspot's **Allow Others to
Join** and keep it broadcasting nearby. A locked Keychain or permission policy
can prevent joining; unlock the Mac and rerun setup to repair credentials.

Internet checks verify recovery independently of a successful association.
Automatic sleep waits for the outage delay and bounded attempt/recovery window,
including when activity retries finish early. Recovery resets the timer for the
next outage. Ctrl-C cancels the owned helper and releases the sleep hold.
Fallback is inactive with `--forever`, `--no-internet`, or `--checks none`.
It is off by default and is never enabled on other Macs by `dots --all`.

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
XDG directories, and tmux sockets. A macOS CI job also creates real sleep assertions
and checks `nosleep` against live `pmset` output on PRs and weekly. CI runs shellcheck,
gitleaks, the private PII scan, and dependency audit behind the required `CI` gate.
The Python t suite and its 97% coverage requirement moved to the standalone repository.

MIT — [LICENSE](LICENSE). This remains a personal setup; [contributing](CONTRIBUTING.md).
