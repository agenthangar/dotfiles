# dotfiles

Personal shell config and utility scripts.

## Layout

- `.zshrc` — zsh shell config (aliases, functions, PATH, tab completions). Run
  `help` for a live, auto-generated list of every command defined here.
- `bin/` — utility scripts (added to PATH)
  - `sleep-manager` — manage macOS sleep behavior (`status`, `disable`, `enable`)
  - `pii-scan` — fail if any PII appears in tracked/staged files (see **PII guard** below)
- `Brewfile` — third-party CLI tools the config depends on (`gh`, `jq`, `tmux`);
  installed by `install.sh` via `brew bundle`
- `claude/` — Claude Code config
  - `settings.json` — symlinked to `~/.claude/settings.json`. Carries `enabledPlugins`,
    `extraKnownMarketplaces`, and `permissions`, so plugins reproduce on a new machine
    (Claude re-clones the marketplaces and reinstalls whatever's enabled on first run).

The files in this repo are the source of truth. `~/.zshrc`, `~/bin/<script>`, and
`~/.claude/settings.json` are symlinks back into this repo, so editing either side edits both.

Per-machine config — your real repo list (`DEV_REPOS`), the default `tbeam` host
(`TBEAM_HOST`), and any private shell completions — lives in `~/.zshrc.local`, which
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

`pii-scan` keeps personal data out of this public repo. It reuses the cashfwd
two-layer ruleset plus a dotfiles-specific allowlist:

1. **Denylist** — `scrub-rules.json`, the literal list of real personal
   identifiers (names, emails, phones, account numbers, private hosts). It is
   **private and never committed here** (gitignored). Locally it's read from
   `~/code/cashfwd-private/scrub-rules.json` (override with `$PII_RULES`); in CI
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
  without `cashfwd-private` can still commit; CI is the backstop. Bypass once
  with `git commit --no-verify`.
- **GitHub Action** (`.github/workflows/pii-scan.yml`) — runs on push/PR to
  `main` and **fails closed**, so a missing secret is loud. Set the secret once:

  ```sh
  gh secret set PII_SCRUB_RULES < ~/code/cashfwd-private/scrub-rules.json
  ```

Run it by hand anytime: `pii-scan` (all tracked files) or `pii-scan --staged`.

## Install on a new machine

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
