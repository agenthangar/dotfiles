# dotfiles

> Personal macOS + zsh dotfiles, built around a toolkit for running **Claude Code**
> in tmux — teleport, search, and sync sessions across machines.

The everyday shell config is here (aliases, PATH, completions), but the
distinctive part is the **Claude Code session tooling**, unified under a single
GitHub-CLI-style command: **`t`** (a Python core in `bin/t` plus a thin `t()`
shim in `.zshrc` for verbs that must run in your shell).

## The `t` command

| Command | What it does |
| --- | --- |
| `t open <repo> [slot]` | Open or reattach a session in a per-repo detached tmux slot (`--new`, `--fg`, `--remote`, `--here`) |
| `t ls [-r] [-a]` | List live sessions, optionally across every machine (`-r`) and repo (`-a`) |
| `t push` / `t pop` | Move a session between a foreground terminal and a detached tmux slot — one-live-owner guarantee |
| `t beam <repo> [slot] --host <h>` | Teleport a running session to another machine; pull one back with `t open … --here` |
| `t find <query>` | Semantic search across saved sessions ("which one was working on X?"), reranked by Claude |

Run `t -h` for the full verb list.

## Other commands

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
  - `settings.json.example` — conservative defaults seeded to `~/.claude/settings.json`
    on install (only the session-stamping hook; you approve Bash yourself). This is the
    default; `install.sh` prompts before applying anything else.
  - `settings.json` — the author's tuned config: `enabledPlugins`, `extraKnownMarketplaces`,
    `"defaultMode": "auto"`, `skipAutoPermissionPrompt`, and a Bash allowlist. Opt in
    at install time (choice 2) or later with `CLAUDE_SETTINGS=author ./install.sh` on a
    machine that does not yet have `~/.claude/settings.json`. Symlinking this reproduces
    plugins on a new machine (Claude re-clones marketplaces on first run).

The files in this repo are the source of truth. `~/.zshrc` and `~/bin/<script>` are
symlinks back into this repo, so editing either side edits both.

The files **in this repo are the source of truth.** `install.sh` symlinks them
into `$HOME`, so editing either side edits both — there is no copy or sync step.

| Repo file | Symlinked to |
| --- | --- |
| `.zshrc` | `~/.zshrc` |
| `bin/<script>` | `~/bin/<script>` |
| `claude/settings.json` | `~/.claude/settings.json` |

`bin/` holds the utility scripts (added to PATH):

| Script | Role |
| --- | --- |
| `t` | The single Claude-session command (Python); paired with the `t()` shim in `.zshrc` |
| `csync` | Two-way sync of Claude session history via iCloud Drive |
| `sleep-manager` | Manage macOS sleep behavior |
| `claude-stamp-tmux` | Claude SessionStart hook — records each session's id so `t pop`/`t plan` target the exact session |
| `pii-scan` | Fail if any PII appears in tracked/staged files |

**Per-machine config** — your real repo list (`DEV_REPOS`), remote hosts
(`REMOTE_HOSTS`), and private completions — lives in `~/.zshrc.local`, a real
copy (not a symlink) that `.zshrc` sources if present. `install.sh` seeds it from
`.zshrc.local.example`; it is never committed. Edit that copy.

## Keeping machines in sync

**`dots`** syncs your dotfiles to **`origin/main` HEAD** and reloads your shell in
one step: it fetches, checks out `main`, fast-forwards it to `origin/main`, and
re-sources `~/.zshrc`. Your live dotfiles become exactly what's published on
`main` — nothing is done locally (no merge, no commit), and the symlink model
means it takes effect immediately.

Develop on the standing dev branch via `t open dotfiles`; `dots` is the "just
give me the latest" command and leaves you on `main`. It's safe: if the working
tree has uncommitted edits it stops and only reloads, so it never discards
in-progress work — commit or stash first.

Session history syncs separately, in the background, via `csync`.

## The `help` command

`help` (or `h`) prints every custom command, grouped by purpose. Names and
descriptions are **generated at call time**, not stored — read from the leading
`# name <args> — description` comment above each `.zshrc` function and the header
line of each `bin/` script. Give a new command that one-line comment and it shows
up automatically.

Grouping lives in the `groups` list inside the `help` function; uncategorized
commands fall under **Other** so nothing is hidden, except a short `_hide` list
of internal/automatic commands (a wrapper, a hook, a guard) you never invoke by
hand. Output is self-contained — plain ANSI, colored only on a terminal.

## Claude plugins & MCP

Plugins sync via `claude/settings.json` when you opt into the author's settings at
install time (or symlink it yourself). The default `settings.json.example` carries
only the session-stamping hook — no plugin bundle.

MCP is two separate things:

- **claude.ai connectors** (Gmail, Calendar, Drive, Canva, Hugging Face, …) are bound
  to your Anthropic account and sync automatically when you log in on a new machine.
  There is nothing to copy.
- **Local/stdio MCP servers** live inside `~/.claude.json`, which is a stateful file
  (OAuth tokens, project history, caches) and is **not** symlinked. If you add one,
  sync it with a merge step rather than committing `~/.claude.json`.

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
3. **Allowlist** — `pii-allowlist.txt`: values intentionally public in *this*
   repo (your GitHub handle, generic vendor names). A denylist hit clears only
   when an allowlist entry appears on the same line. Don't edit the shared
   denylist to silence a dotfiles false positive — add it here.

It runs two ways, both wired by `install.sh`:

- **Pre-commit hook** (`.githooks/pre-commit`) — scans staged content before
  every commit. Enabled via `git config core.hooksPath .githooks` (repo-local,
  set by `install.sh`). It **fails open** when the denylist is absent — a machine
  without the denylist can still commit; CI is the backstop. Bypass once
  with `git commit --no-verify`.
- **GitHub Action** (the `Scan tracked files for PII` job in
  `.github/workflows/ci.yml`) — runs on push/PR to `main` and **fails closed**,
  so a missing secret is loud. Fork and Dependabot PRs can't read repository
  secrets, so that one job skips there; the push-to-`main` run remains the
  backstop. Set the secret once:

  ```sh
  gh secret set PII_SCRUB_RULES < ~/.config/pii-scan/scrub-rules.json
  ```

Run it by hand anytime: `pii-scan` (all tracked files) or `pii-scan --staged`.

## Install on a new machine

**Requirements:** macOS · zsh · [Claude Code](https://claude.com/claude-code) ·
[Homebrew](https://brew.sh) (for the `Brewfile` tools).

`install.sh` is location-independent — clone the repo anywhere and the symlink
*targets* follow:

```sh
git clone git@github.com:chrisobrien-ai/dotfiles.git path/to/dotfiles
path/to/dotfiles/install.sh
```

It creates the symlinks (backing up anything in the way to `*.bak`), seeds
`~/.zshrc.local` and `~/.claude/settings.json` from their templates when absent (with
an interactive prompt for Claude settings — example by default), then runs
`brew bundle` to install the `Brewfile` tools (skipped if Homebrew isn't present).
Both steps are idempotent. If you ever move the repo, just re-run `install.sh`
from the new location to relink.

SSH config is the one exception to the symlink model: rather than replacing
`~/.ssh/config` (which would shadow any host entries you already have),
`install.sh` links the snippet to `~/.ssh/dotfiles.conf` and adds an
`Include dotfiles.conf` line to the bottom of `~/.ssh/config`, creating that file
if it doesn't exist. The include goes last so the snippet's `Host *` defaults
don't override any per-host settings already in your config (OpenSSH uses
"first value wins" semantics). Your existing config is left intact.

## License & contributing

MIT — see [LICENSE](LICENSE). A personal, opinionated setup published so others
can borrow the patterns, not a general-purpose framework; see
[CONTRIBUTING.md](CONTRIBUTING.md) for what that means for issues and PRs.
