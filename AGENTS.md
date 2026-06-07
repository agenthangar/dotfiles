# AGENTS.md

Guidance for AI agents working in this repository.

## What this is

Personal macOS dotfiles: zsh config, shell utilities, and Claude Code session tooling (`t`). There is no web app, build system, or package manager lockfile. See `CLAUDE.md` and `README.md` for architecture details.

## Cursor Cloud specific instructions

### Environment shape

Cloud Agent VMs run **Linux**, not macOS. Full session workflows (`t open`, tmux slots, `t beam`, `sleep-manager disable`, `csync` via iCloud) require a real Mac with Homebrew, tmux, Claude Code, and configured `~/.zshrc.local`. On Linux, treat **CI-equivalent validation + CLI smoke tests** as the development loop.

### First-time VM setup (outside the update script)

Install lint tooling once per VM image (not in the startup update script):

```sh
sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y shellcheck zsh
```

Optional for the `gitleaks` CI job locally: Docker (see `.github/workflows/ci.yml`).

### Startup / refresh

The update script runs `./install.sh`, which is idempotent: it relinks `~/.zshrc`, `~/bin/*`, Claude config symlinks, seeds `~/.zshrc.local` on first run, and sets `git config core.hooksPath .githooks`.

Ensure `~/bin` is on PATH (`export PATH="$HOME/bin:$PATH"`) before invoking `t` or other bin scripts.

### Validation (no formal test suite)

| Check | Command |
| --- | --- |
| Install / relink | `./install.sh` |
| Bash lint (CI) | Discover shell scripts by shebang, then `shellcheck install.sh bin/* .githooks/*` (same logic as `.github/workflows/ci.yml`) |
| Python CLI syntax | `python3 -c "import py_compile; py_compile.compile('bin/t', cfile='/tmp/t.pyc', doraise=True)"` |
| PII scan (local, fail-open) | `./bin/pii-scan` |
| Shell syntax | `bash -n install.sh` and `zsh -n .zshrc` |
| CLI smoke test | `t -h`, `t ls`, `sleep-manager --help` |
| Help renderer | `zsh -lic 'source ~/.zshrc; help'` |

`pii-scan --require-rules` needs the private denylist (`PII_RULES` / `PII_SCRUB_RULES` secret) and is CI-only for most agents.

### Editing shell files

After changing `.zshrc` or a `bin/` script, validate by sourcing or running it. For `bin/t`: compile to `/tmp` so `__pycache__/` is never committed. There is no eslint/ruff step.

### Git workflow

Work on `dev/claude-1` (or a `cursor/*` feature branch for cloud agents). `main` is protected via PR. Pre-commit runs `pii-scan --staged` (fails open without denylist).

### Services

No long-running services. Nothing to start before lint or CLI checks.
