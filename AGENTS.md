# AGENTS.md

Guidance for AI agents working in this repository.

## What this is

Personal macOS dotfiles: zsh config, shell utilities, and Claude Code session tooling (`t`). There is no web app, build system, or package manager lockfile. See `CLAUDE.md` and `README.md` for architecture details.

## Cursor Cloud specific instructions

### Environment shape

Cloud Agent VMs run **Linux**, not macOS. Full session workflows (`t open`, tmux slots, `t beam`, `sleep-manager disable`, `csync` via iCloud) require a real Mac with Homebrew, tmux, Claude Code, and configured `~/.zshrc.local`. On Linux, treat **CI-equivalent validation + CLI smoke tests** as the development loop.

Committed **`.cursor/environment.json`** builds from `.cursor/Dockerfile` (shellcheck, zsh, Docker CE), runs `./install.sh` on every session start, and starts dockerd via `./scripts/cloud-docker-ready.sh` (systemd is unavailable in nested VMs).

### Secrets (Cloud Agent environment)

| Secret | Purpose |
| --- | --- |
| **`PII_SCRUB_RULES`** | Full contents of your private `scrub-rules.json` denylist. `./install.sh` writes it to `~/.config/pii-scan/scrub-rules.json` so `pii-scan` and the pre-commit hook run fail-closed locally. Same value as the GitHub Actions repo secret. |

Without `PII_SCRUB_RULES`, `pii-scan` still **fails open** locally; CI remains the backstop.

### First-time VM setup (outside the update script)

If the environment is **not** built from `.cursor/Dockerfile`, install lint tooling once per VM image:

```sh
sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y shellcheck zsh
```

For Docker + gitleaks without the committed Dockerfile, install Docker CE with the `fuse-overlayfs` storage driver (see `.cursor/Dockerfile` for the apt steps), then start the daemon each session:

```sh
./scripts/cloud-docker-ready.sh
```

### Startup / refresh

The update script runs `./install.sh`, which is idempotent: it relinks `~/.zshrc`, `~/bin/*`, Claude config symlinks, seeds `~/.zshrc.local` on first run, sets `git config core.hooksPath .githooks`, and materializes the PII denylist when `PII_SCRUB_RULES` is set.

Ensure `~/bin` is on PATH (`export PATH="$HOME/bin:$PATH"`) before invoking `t` or other bin scripts.

Docker is started by `./scripts/cloud-docker-ready.sh` (wired in `.cursor/environment.json` `start`). Verify with `docker info` or `sudo docker info`.

### Validation (no formal test suite)

| Check | Command |
| --- | --- |
| Install / relink | `./install.sh` |
| Bash lint (CI) | Discover shell scripts by shebang, then `shellcheck install.sh bin/* .githooks/* scripts/*` (same logic as `.github/workflows/ci.yml`) |
| Python CLI syntax | `python3 -c "import py_compile; py_compile.compile('bin/t', cfile='/tmp/t.pyc', doraise=True)"` |
| PII scan (local, fail-open) | `./bin/pii-scan` |
| PII scan (fail-closed) | `./bin/pii-scan --require-rules` (needs `PII_SCRUB_RULES` secret / materialized denylist) |
| Gitleaks (CI) | `docker run --rm -v "$PWD:/repo" ghcr.io/gitleaks/gitleaks:v8.21.2 detect --source=/repo --redact --verbose` |
| Shell syntax | `bash -n install.sh` and `zsh -n .zshrc` |
| CLI smoke test | `t -h`, `t ls`, `sleep-manager --help` |
| Help renderer | `zsh -lic 'source ~/.zshrc; help'` |

### Editing shell files

After changing `.zshrc` or a `bin/` script, validate by sourcing or running it. For `bin/t`: compile to `/tmp` so `__pycache__/` is never committed. There is no eslint/ruff step.

### Git workflow

Work in a per-session worktree on `dev/<repo>-<slot>` (`t open <repo>`), or a `cursor/*` feature branch for cloud agents. `main` is protected via PR — and it is checked out in the ONE canonical clone, which is the live surface, so never commit there. Pre-commit runs `pii-scan --staged` (fails open without denylist; fail-closed when `PII_SCRUB_RULES` is configured).

### Services

Docker (`dockerd`) must be running for the gitleaks CI job locally. Use `./scripts/cloud-docker-ready.sh` if `docker info` fails. No other long-running services are required for lint or CLI checks.
