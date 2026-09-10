# dotfiles

> Personal macOS + zsh dotfiles, built around a toolkit for running **Claude Code**
> in tmux — teleport, search, and sync sessions across machines. Linux hosts are
> supported as remote nodes (macOS-only pieces skip themselves).

The everyday shell config is here (aliases, PATH, completions), but the
distinctive part is the **Claude Code session tooling**, unified under a single
GitHub-CLI-style command: **`t`** (a Python core in `bin/t` plus a thin `t()`
shim in `.zshrc` for verbs that must run in your shell).

## The `t` command

| Command | What it does |
| --- | --- |
| `t open <repo> [slot]` | Open or reattach a session in a per-repo detached tmux slot (`--new`, `--fg`, `--remote`, `--here`; `--codex` runs OpenAI's Codex CLI in the slot instead of Claude — `DEV_AGENT[repo]=codex` makes that the repo's default) |
| `t ls [-r] [-a]` | List live sessions, optionally across every machine (`-r`) and repo (`-a`) |
| `t cd [repo] [slot]` | `cd` this shell into a slot's worktree (bare `t cd`: fzf pick across all worktrees) |
| `t push` / `t pop` | Move a session between a foreground terminal and a detached tmux slot — one-live-owner guarantee |
| `t beam <repo> [slot] --host <h>` | Teleport a running session to another machine; pull one back with `t open … --here` |
| `t find <query>` | Semantic search across saved sessions ("which one was working on X?"), reranked by Claude |
| `t install [agent…]` | Install and log in the agent CLIs — Claude Code, Codex, Cursor — on this machine and every remote host, `t setup`-style: one checklist with the present entries locked, the vendors' commands shown before anything runs ([details](#agents)) |
| `t new [name\|--prompt TEXT]` | Wizard: create `~/code/<name>` + a GitHub repo (owner picked from your orgs; squash-only, auto-merge), register it here and clone + register it on every remote host. No name in mind? Describe it (a sentence at the name step, or `--prompt`) and Claude suggests names to pick from. Re-running resumes; on an existing repo it just finishes the wiring |
| `t mcp` | The `sessions` MCP server Claude Code spawns, so any Claude session can answer "which session is/was working on X?" from every saved transcript and the live slots. `--install` registers it (`dots` does), `--call <tool> '<json>'` runs one tool by hand |

Run `t -h` for the full verb list.

## Other commands

| Command | What it does |
| --- | --- |
| `dots [--dev]` | Sync the live checkout to `origin/main` HEAD and reload zsh; `--dev` makes the session worktree you are standing in live instead ([details](#keeping-machines-in-sync)) |
| `csync` | Two-way sync of Claude session history and plans, Codex rollouts, and Cursor chats across machines via iCloud Drive |
| `sleep-manager` | Block or restore macOS sleep (`status`, `disable`, `enable`) |
| `pii-scan` | Keep personal data out of this public repo ([details](#pii-guard)) |
| `help` / `h` | Auto-generated, grouped list of every command ([details](#the-help-command)) |

## Layout & the symlink model

The files **in this repo are the source of truth.** `install.sh` symlinks them
into `$HOME`, so editing either side edits both — there is no copy or sync step.
Specifically it links from the **canonical checkout** (the one parked on `main`),
which is why that tree is the live surface and session worktrees are not — until
you point them there yourself with `dots --dev`.

| Repo file | Symlinked to |
| --- | --- |
| `.zshrc` | `~/.zshrc` |
| `bin/<script>` | `~/bin/<script>` |
| `claude/commands/*.md` | `~/.claude/commands/*.md` |

`bin/` holds the utility scripts (added to PATH):

| Script | Role |
| --- | --- |
| `t` | The single Claude-session command (Python); paired with the `t()` shim in `.zshrc` |
| `csync` | Two-way sync of Claude session history via iCloud Drive |
| `sleep-manager` | Manage macOS sleep behavior |
| `claude-stamp-tmux` | Claude SessionStart hook — records each session's id so `t pop`/`t plan` target the exact session |
| `pii-scan` | Fail if any PII appears in tracked/staged files |

Two files are deliberately **real copies, not symlinks**, so machine-specific or
sensitive config never lands in the repo (`install.sh` seeds each from a template
on first run and never clobbers an existing one):

- **`~/.zshrc.local`** — your real repo list (`DEV_REPOS`), remote hosts
  (`REMOTE_HOSTS`), and private completions. `.zshrc` sources it if present.
- **`~/.claude/settings.json`** — see [Claude plugins & MCP](#claude-plugins--mcp).

## Keeping machines in sync

There is **one canonical checkout** of this repo (normally `~/code/dotfiles`),
parked on `main`, and it **is** the live surface — the `$HOME` symlinks point
straight at it. All development happens in **per-session worktrees**
(`~/code/.worktrees/<repo>/<slot>` on `dev/<repo>-<slot>`), created by
`t open <repo>` and reaped once their PR merges.

**`dots`** syncs that checkout to **`origin/main` HEAD** and reloads your shell in
one step: it fetches, fast-forwards to `origin/main`, and re-sources `~/.zshrc`.
Your live dotfiles become exactly what's published on `main` — nothing is done
locally (no merge, no commit), and the symlink model means it takes effect
immediately. It's safe, stopping and only reloading if the working tree has
uncommitted edits.

Because that checkout is live, **don't edit or commit in it** — a save there changes
your `$HOME` config instantly, and `main` is protected. A pre-commit hook refuses
commits on `main` in the live tree, and `dots --dev` refuses to take it as a source.

`dots` also **reconciles the managed symlinks every run**, so a released change that
adds a managed file (a new `bin/` script, a new dotfile) lands without a manual
install — the failure it fixes was a fast-forwarded machine whose `~/.tmux.conf` link
had simply never been made. It is offline and prints nothing unless a link changed.
**`dots --relink`** does just that step, without fetching.

**`dots --dev`** flips the live symlinks to the **session worktree you are standing
in**, so its in-progress edits go live for testing before they merge — useful for a
new `bin/` script that needs a fresh symlink. `cd` into the worktree
(`t cd dot <slot>`) and run it; a later plain `dots` flips back. Anywhere else it
errors rather than guessing. It skips `brew bundle` for speed.

Start a dotfiles session with `t open dotfiles`. Session history syncs separately,
in the background, via `csync`.

## Agents

The session tooling was built around Claude Code, and now takes **three agent
CLIs**: Claude Code (`claude`), OpenAI's Codex CLI (`codex`), and the Cursor CLI
(`cursor-agent`). `t install` installs and logs in whichever are missing, here
and on every `REMOTE_HOSTS` host, using each vendor's published installer
(`brew install --cask codex` on a Mac, their `curl … | sh` scripts elsewhere) and
each one's login command — device-code variants over ssh. It is a checklist like
`t setup`: installed and logged-in entries are locked under a ✓, the rest are
pre-marked, and the review screen shows the exact commands before `y` runs any.
`t install --status` prints this machine's state; `t doctor` reports an agent that
is installed but not logged in.

A dev slot runs either agent: `t open <repo> --codex` starts Codex CLI in a fresh
slot (`--claude` forces Claude), `DEV_AGENT[repo]=codex` in `~/.zshrc.local` makes
it a repo's default and `DEV_AGENT_DEFAULT` the global one. `t ls` marks a codex
slot with `⬡` in its STATUS column and adds the glyph to the legend only when one
is on screen, so a claude-only listing looks exactly as it always did. A codex
slot's title comes from its rollout (the same cached parser as Claude's
transcripts), `t pop`/`t push` move it with `codex resume`, and `t resume` lists
a dead codex slot's conversations from Codex's own thread index, marked `⬡` in
the picker. Codex mints its thread id at the first prompt (there is no
`--session-id`), so an untouched codex slot reads as idle until you type.
Across machines, `t beam` ships a codex slot's rollout (and its origin stamp)
with its date path intact and `csync` mirrors `~/.codex/sessions` to iCloud as
`codex-sessions` — Codex indexes a copied-in rollout on the first resume, so only
the append-only rollouts travel, never its sqlite state.

Not every verb supports every agent yet. The matrix below is **generated from
`bin/t`** (a test pins this block to it), so a verb cannot gain or lose agent
support without the README saying so:

```text
  surface                                           claude                codex                                  cursor
  ------------------------------------------------  --------------------  -------------------------------------  -------------------------------
  t install (install · login · update)              ✓                     ✓                                      ✓
  dev slots: t open / ls / kill / read / paste      ✓                     ✓ t open --codex · DEV_AGENT           ✗ no slot — t cursor ls
  t push / t pop                                    ✓                     ✓                                      ✗ no slot
  t resume (dead slots)                             ✓                     ✓ (its sqlite thread index)            → t cursor resume
  t beam / --from (move a session)                  ✓                     ✓ (rollout + .origin)                  → t cursor [id] --host / --from
  csync (iCloud union of transcripts)               ✓ projects + plans    ✓ codex-sessions                       ✓ cursor-chats
  SessionStart stamps (registry · opened · origin)  ✓ settings.json hook  ✓ hooks.json + /hooks trust            ✗ no hook wired
  t plan                                            ✓                     ✗ codex keeps no plan files (says so)  ✗
  t find / t mcp (transcript search)                ✓                     planned                                ✗
  t doctor agent row (version · login · hook)       ✓                     ✓                                      ✓ version · login
```

Codex needs one manual step after install: its SessionStart hook (the same
`claude-stamp-tmux` script, registered in `~/.codex/hooks.json` by `install.sh`/
`dots`) must be trusted once under `/hooks` inside codex — Codex has no supported
way for an installer to pre-trust a hook.

## The `help` command

`help` (or `h`) prints every custom command, grouped by purpose. Names and
descriptions are **generated at call time**, not stored — read from the leading
`# name <args> — description` comment above each `.zshrc` function and the header
line of each `bin/` script. Give a new command that one-line comment and it shows
up automatically.

Grouping lives in the `groups` list inside the `help` function; uncategorized
commands fall under **Other** so nothing is hidden, except a short `_hide` list of
internal/automatic commands (a wrapper, a hook, a guard) you never invoke by hand.
The generated sections (repo + host shortcuts) also print a one-line "how to add"
hint. Output is self-contained — plain ANSI, colored only on a terminal.

## Claude plugins & MCP

Claude settings install as a **real copy**, never clobbering an existing
`~/.claude/settings.json`. The seed is **`settings.json.example`** — conservative:
only the session-stamping hook the tmux tooling needs; you approve Bash yourself.

The live file is **per-machine and untracked by design**: Claude Code writes to
it at runtime (`/model` saves your default model, "always allow" appends
permission rules, plugin toggles land there), so tracking or symlinking it keeps
the repo dirty and risks committing private allow-rules. An earlier version
shipped the author's tuned config as a symlink option; `install.sh` now
materializes such a legacy symlink into a real copy and the path is gitignored.

MCP is two separate things:

- **claude.ai connectors** (Gmail, Calendar, Drive, Canva, Hugging Face, …) are
  bound to your Anthropic account and sync automatically on login. Nothing to copy.
- **Local/stdio MCP servers** live in `~/.claude.json`, a stateful file (OAuth
  tokens, history) that is **not** symlinked and never committed. The one this repo
  ships — `sessions`, i.e. `t mcp` — is registered by `install.sh`/`dots` through
  `claude mcp add` (add-only; `DOTFILES_NO_MCP=1` opts a machine out). Any other
  local server you add stays your own business.

## PII guard

`pii-scan` keeps personal data out of this public repo. **This documents my own
setup** — to reuse it in a fork, point `$PII_RULES` at your own denylist JSON
(mine lives in a private repo). Three layers:

1. **Denylist** — `scrub-rules.json`: literal personal identifiers (names, emails,
   phones, private hosts). **Private, never committed here** (gitignored); read
   locally from `~/.config/pii-scan/scrub-rules.json` (override with `$PII_RULES`),
   in CI from the `PII_SCRUB_RULES` secret.
2. **Ignore patterns** — `pii-ignore-patterns.txt`: regexes for known
   false-positive *shapes* (no PII; tracked).
3. **Allowlist** — `pii-allowlist.txt`: values intentionally public in *this* repo
   (your GitHub handle, generic vendor names). A denylist hit clears only when an
   allowlist entry appears on the same line. Don't edit the shared denylist to
   silence a dotfiles false positive — add it here.

It runs two ways, both wired by `install.sh`:

- **Pre-commit hook** (`.githooks/pre-commit`) — scans staged content. **Fails
  open** if the denylist is absent (a machine without it can still commit; CI is
  the backstop). Bypass once with `git commit --no-verify`.
- **GitHub Action** (the `Scan tracked files for PII` job in
  `.github/workflows/ci.yml`) — runs on push/PR to `main` and **fails closed**, so
  a missing secret is loud. Fork/Dependabot PRs can't read secrets, so that job
  skips there; the push-to-`main` run is the backstop. Set the secret once:

  ```sh
  gh secret set PII_SCRUB_RULES < ~/.config/pii-scan/scrub-rules.json
  ```

Run it by hand anytime: `pii-scan` (all tracked files) or `pii-scan --staged`.

## Tests

The Python CLIs (`bin/t`, `bin/pr-watch`) have a `pytest` suite covering their
pure logic (config parsing, path→repo resolution, the capture-pane ANSI stripper,
PR triage). The test deps are **dev/CI-only** — `install.sh` never installs them.

```sh
pip install -r requirements-dev.txt
python3 -m pytest                       # run the suite
python3 -m pytest --cov --cov-report=term-missing   # with coverage
```

CI runs it as the `pytest` job, gated by `--cov-fail-under` (a ratchet that only
goes up). The gate feeds the single required `CI` check like every other job.

## Install on a new machine

**Requirements:** macOS · zsh · [Homebrew](https://brew.sh) (for the `Brewfile`
tools). The agent CLIs ([Claude Code](https://claude.com/claude-code), Codex,
Cursor) are installed by `t install` afterwards.

`install.sh` is location-independent — clone the repo anywhere and the symlink
*targets* follow:

```sh
git clone git@github.com:chrisobrien-ai/dotfiles.git path/to/dotfiles
path/to/dotfiles/install.sh
```

It creates the symlinks (backing up anything in the way to `*.bak`), seeds
`~/.zshrc.local` and `~/.claude/settings.json` from their templates when absent,
then runs `brew bundle` (skipped if Homebrew is absent). It is non-interactive and
idempotent — an already-correct symlink is left alone and prints nothing. You rarely
need to run it by hand after the first time: `dots` reconciles the symlinks itself.

**SSH config** is the one exception to the symlink model: rather than replacing
`~/.ssh/config`, `install.sh` links the snippet to `~/.ssh/dotfiles.conf` and
appends an `Include dotfiles.conf` line to the bottom of `~/.ssh/config` (creating
it if needed). The include goes last so the snippet's `Host *` defaults never
override your per-host settings (OpenSSH is "first value wins"). Your existing
config is left intact.

## License & contributing

MIT — see [LICENSE](LICENSE). A personal, opinionated setup published so others
can borrow the patterns, not a general-purpose framework; see
[CONTRIBUTING.md](CONTRIBUTING.md) for what that means for issues and PRs.
