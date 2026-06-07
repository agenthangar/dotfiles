# dotfiles

Personal macOS + zsh dotfiles, built around a toolkit for running **Claude Code**
in tmux — teleport, search, and sync Claude Code sessions across machines.

## Highlights

The everyday shell config (aliases, PATH, completions) is here, but the
distinctive part is the Claude Code session tooling, unified under a single
GitHub-CLI-style command **`t`** (`bin/t`, a Python core, plus a thin `t()` shim
in `.zshrc` for the verbs that must run in your shell):

- **`t open <repo> [slot]`** — open or reattach a Claude Code session in a
  per-repo detached tmux slot (`--new`, `--fg`, `--remote`, `--here`).
- **`t push` / `t pop`** — move a session between a foreground terminal and a
  detached background tmux slot, with a one-live-owner guarantee.
- **`t ls [-r] [-a]`** — list live sessions, optionally across every machine.
- **`t beam <repo> [slot] --host <h>`** — teleport a running session to another
  machine and resume it there; pull one back with `t open … --here`.
- **`t find <query>`** — semantic search across your saved sessions ("which
  session was working on X?"), reranked by Claude.
- **`csync`** — two-way sync of Claude Code session history across machines via
  iCloud Drive.

Plus the usual macOS helpers: **`sleep-manager`** (block/restore sleep) and
**`pii-scan`** (keep personal data out of this public repo). Run `t -h` for the
full verb list and `help` for the auto-generated command list.

## Layout

- `.zshrc` — zsh shell config (aliases, functions, PATH, tab completions). Run
  `help` for a live, auto-generated list of every command defined here.
- `bin/` — utility scripts (added to PATH)
  - `t` — the single Claude-session command (Python); paired with the `t()` shim in `.zshrc`
  - `sleep-manager` — manage macOS sleep behavior (`status`, `disable`, `enable`)
  - `csync` — two-way sync of Claude Code session history across machines via iCloud Drive
  - `claude-stamp-tmux` — Claude Code SessionStart hook; records each session's id
    (tmux + a pid registry) so `t pop`/`t plan` can target the exact session
  - `pii-scan` — fail if any PII appears in tracked/staged files (see **PII guard** below)
- `Brewfile` — third-party CLI tools the config depends on (`gh`, `jq`, `tmux`,
  `fzf`, `glow`); installed by `install.sh` via `brew bundle`
- `claude/` — Claude Code config
  - `settings.json` — symlinked to `~/.claude/settings.json`. Carries `enabledPlugins`,
    `extraKnownMarketplaces`, and `permissions`, so plugins reproduce on a new machine
    (Claude re-clones the marketplaces and reinstalls whatever's enabled on first run).
    > **Heads-up before you symlink this:** it is tuned for *my* low-friction,
    > mostly-unattended workflow — `"defaultMode": "auto"`, `skipAutoPermissionPrompt`,
    > and a Bash allowlist (`kill`, `python3`, `node -e`, `grep`, `rg`, `lsof`). That
    > lets Claude run those without prompting. If you reuse this file, review those
    > keys and dial them back (drop `auto`/`skipAutoPermissionPrompt`, trim the
    > allowlist) unless you actively want that posture.

The files in this repo are the source of truth. `~/.zshrc`, `~/bin/<script>`, and
`~/.claude/settings.json` are symlinks back into this repo, so editing either side edits both.

Per-machine config — your real repo list (`DEV_REPOS`), remote hosts
(`REMOTE_HOSTS`), and any private shell completions — lives in `~/.zshrc.local`, which
`.zshrc` sources if present. It's a real copy (not a symlink) so it never lands in the
repo; `install.sh` seeds it from `.zshrc.local.example` on first run. Edit that copy.

## The `help` command

`help` prints every custom command, grouped by purpose. Each command's name and
description are **generated at call time**, not stored: they're read from the
leading `# name <args> — description` comment above each `.zshrc` function and
the header line of each `bin/` script. So a new command shows up automatically —
just give it that one-line comment in the same format.

Grouping lives in the `groups` list inside the `help` function — add a command's
name to a group to file it; anything uncategorized appears under **Other**, so a
new command is never hidden. Output is self-contained (plain ANSI, colored on a
terminal); no external renderer required.

## Claude plugins & MCP

Plugins sync via `claude/settings.json` above — no extra step.

MCP is two separate things:

- **claude.ai connectors** (Gmail, Calendar, Drive, Canva, Hugging Face, …) are bound
  to your Anthropic account and sync automatically when you log in on a new machine.
  There is nothing to copy.
- **Local/stdio MCP servers** live inside `~/.claude.json`, which is a stateful file
  (OAuth tokens, project history, caches) and is **not** symlinked. We have none today.
  If you add one, sync it with a merge step rather than committing `~/.claude.json`.

## PII guard

`pii-scan` keeps personal data out of this public repo. **This documents my own
setup** — to reuse the pattern in your fork, point `$PII_RULES` at your own
denylist JSON (mine lives in a private repo). It uses a two-layer ruleset plus a
dotfiles-specific allowlist:

1. **Denylist** — `scrub-rules.json`, the literal list of real personal
   identifiers (names, emails, phones, account numbers, private hosts). It is
   **private and never committed here** (gitignored). Locally it's read from
   `~/.config/pii-scan/scrub-rules.json` (override with `$PII_RULES`); in CI
   it comes from the `PII_SCRUB_RULES` secret.
2. **Ignore patterns** — `pii-ignore-patterns.txt`, regexes for known
   false-positive *shapes* (no PII; tracked).
3. **Allowlist** — `pii-allowlist.txt`, values that are intentionally public in
   *this* repo (your GitHub handle, generic vendor names like `Anthropic`). A
   denylist hit clears only when an allowlist entry actually appears on the same
   line. Don't edit the shared denylist to silence a dotfiles false positive —
   add it here instead.

It runs two ways, both wired up by `install.sh`:

- **Pre-commit hook** (`.githooks/pre-commit`) — scans staged content before
  every commit. Enabled via `git config core.hooksPath .githooks` (repo-local,
  set by `install.sh`). It **fails open** when the denylist is absent — a machine
  without the denylist can still commit; CI is the backstop. Bypass once
  with `git commit --no-verify`.
- **GitHub Action** (the `Scan tracked files for PII` job in
  `.github/workflows/ci.yml`) — runs on push/PR to `main` and **fails closed**,
  so a missing secret is loud. Set the secret once:

  ```sh
  gh secret set PII_SCRUB_RULES < ~/.config/pii-scan/scrub-rules.json
  ```

Run it by hand anytime: `pii-scan` (all tracked files) or `pii-scan --staged`.

## Install on a new machine

**Requirements:** macOS · zsh · [Claude Code](https://claude.com/claude-code) ·
[Homebrew](https://brew.sh) (for the `Brewfile` tools).

Clone the repo anywhere — `install.sh` is location-independent (it resolves its own
path), so the symlink *targets* follow wherever you checked it out:

```sh
git clone git@github.com:chrisjob1021/dotfiles.git path/to/dotfiles
path/to/dotfiles/install.sh
```

It creates the symlinks (backing up anything in the way to `*.bak`), then runs
`brew bundle` to install the `Brewfile` tools (skipped if Homebrew isn't present).
Both steps are idempotent. If you ever move the repo, just re-run `install.sh`
from the new location to relink.

SSH config is the one exception to the symlink model: rather than replacing
`~/.ssh/config` (which would shadow any host entries you already have),
`install.sh` links the snippet to `~/.ssh/dotfiles.conf` and adds an
`Include dotfiles.conf` line to the top of `~/.ssh/config`, creating that file if
it doesn't exist. Your existing config is left intact.

## License & contributing

MIT — see [LICENSE](LICENSE). This is a personal, opinionated setup published so
others can borrow the patterns, not a general-purpose framework; see
[CONTRIBUTING.md](CONTRIBUTING.md) for what that means for issues and PRs.
