#!/usr/bin/env bash
# install.sh — link dotfiles into $HOME on a fresh machine.
# Existing files are backed up to <name>.bak before being replaced.

set -euo pipefail

DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Worktree-separation model (read the "symlink model" note in CLAUDE.md) ---
# The LIVE surface (what $HOME symlinks point at) is a dedicated git worktree pinned
# to `main`, NOT the clone you develop in. install.sh sets that worktree up and points
# the symlinks at it; `dots` keeps it fast-forwarded. The clone at $DOTFILES_DIR stays
# on the dev branch for in-progress work — and because `main` is checked out in the
# worktree, git physically REFUSES to check it out in the dev clone, so a stray
# `git checkout main` / old-style `dots` can never swap the live files out from under
# in-progress work. Override the worktree location with $DOTFILES_MAIN_WT.
#
# Two link modes:
#   default              LINK_SRC = the main worktree   (live = released `main`)
#   DOTFILES_LINK_DEV=1  LINK_SRC = $DOTFILES_DIR        (live = your current dev branch;
#                        the fast inner-loop, set by `dots --dev`)
MAIN_WT="${DOTFILES_MAIN_WT:-$HOME/.local/share/dotfiles-main}"
DEV_BRANCH="${DEV_BRANCH:-dev/claude-1}"

# setup_main_worktree — idempotently make $MAIN_WT a worktree checked out on `main`,
# migrating an old single-tree layout in place (aggressive, per design): if the dev
# clone is sitting on `main`, move it to $DEV_BRANCH first so `main` is free for the
# worktree.
setup_main_worktree() {
    git -C "$DOTFILES_DIR" fetch -q origin 2>/dev/null || true

    local cur
    cur="$(git -C "$DOTFILES_DIR" symbolic-ref --short -q HEAD || true)"
    if [[ "$cur" == "main" ]]; then
        if git -C "$DOTFILES_DIR" show-ref --verify -q "refs/heads/$DEV_BRANCH"; then
            git -C "$DOTFILES_DIR" checkout -q "$DEV_BRANCH"
        elif git -C "$DOTFILES_DIR" show-ref --verify -q "refs/remotes/origin/$DEV_BRANCH"; then
            git -C "$DOTFILES_DIR" checkout -q -b "$DEV_BRANCH" "origin/$DEV_BRANCH"
        else
            git -C "$DOTFILES_DIR" checkout -q -b "$DEV_BRANCH"
        fi
        echo "Moved dev clone off main -> $DEV_BRANCH (main now lives in the worktree)"
    fi

    # Make sure a local `main` exists for the worktree to check out.
    git -C "$DOTFILES_DIR" show-ref --verify -q refs/heads/main \
        || git -C "$DOTFILES_DIR" branch -q --track main origin/main 2>/dev/null || true

    # Ensure $MAIN_WT is a worktree. Probe its own .git (a linked worktree has a .git
    # FILE pointing back into the repo) rather than matching `git worktree list` output,
    # which reports canonicalized paths (/tmp -> /private/tmp) that would not compare
    # equal and would wrongly trigger a recreate.
    if [[ -e "$MAIN_WT/.git" ]] && git -C "$MAIN_WT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        :
    else
        # Aggressive: clear anything squatting at the path, drop stale admin, recreate.
        [[ -e "$MAIN_WT" ]] && rm -rf "$MAIN_WT"
        git -C "$DOTFILES_DIR" worktree prune 2>/dev/null || true
        mkdir -p "$(dirname "$MAIN_WT")"
        git -C "$DOTFILES_DIR" worktree add -q "$MAIN_WT" main 2>/dev/null \
            || git -C "$DOTFILES_DIR" worktree add -q --force "$MAIN_WT" main
        echo "Created main worktree -> $MAIN_WT"
    fi
    git -C "$MAIN_WT" merge --ff-only origin/main -q 2>/dev/null || true
}

if [[ -n "${DOTFILES_LINK_DEV:-}" ]]; then
    LINK_SRC="$DOTFILES_DIR"
    echo "Linking from the DEV clone ($(git -C "$DOTFILES_DIR" symbolic-ref --short -q HEAD || echo '?')) — in-progress edits are live."
else
    setup_main_worktree
    LINK_SRC="$MAIN_WT"
fi

link() {
    local src="$1" dst="$2"
    if [[ -L "$dst" ]]; then
        rm "$dst"
    elif [[ -e "$dst" ]]; then
        mv "$dst" "$dst.bak"
        echo "Backed up existing $dst -> $dst.bak"
    fi
    mkdir -p "$(dirname "$dst")"
    ln -s "$src" "$dst"
    echo "Linked $dst -> $src"
}

link "$LINK_SRC/.zshrc"               "$HOME/.zshrc"
link "$LINK_SRC/bin/sleep-manager"    "$HOME/bin/sleep-manager"
link "$LINK_SRC/bin/csync"            "$HOME/bin/csync"
link "$LINK_SRC/bin/cursor-beam"      "$HOME/bin/cursor-beam"
link "$LINK_SRC/bin/pii-scan"         "$HOME/bin/pii-scan"
link "$LINK_SRC/bin/claude-stamp-tmux" "$HOME/bin/claude-stamp-tmux"
link "$LINK_SRC/bin/t"                "$HOME/bin/t"
link "$LINK_SRC/bin/pr-watch"         "$HOME/bin/pr-watch"
link "$LINK_SRC/claude/commands/tpush.md" "$HOME/.claude/commands/tpush.md"
link "$LINK_SRC/claude/commands/tpop.md"  "$HOME/.claude/commands/tpop.md"

# SSH config via Include, NOT a wholesale symlink. Symlinking ~/.ssh/config would
# replace any existing host entries (backed up to .bak, but still a surprise). So
# link our snippet to ~/.ssh/dotfiles.conf and make ~/.ssh/config pull it in with
# an `Include` at the very bottom. OpenSSH uses "first value wins" semantics, and
# our snippet's `Host *` defaults must lose to any per-host settings already in
# the user's config — so the include goes after them, not before. Non-destructive
# and idempotent.
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
link "$LINK_SRC/ssh/config" "$HOME/.ssh/dotfiles.conf"
ssh_main="$HOME/.ssh/config"
include_line="Include dotfiles.conf"
# An older install symlinked ~/.ssh/config straight at the repo; drop that link so
# we manage a real file (writing through the symlink would edit the repo copy).
[[ -L "$ssh_main" ]] && rm "$ssh_main"
if [[ ! -e "$ssh_main" ]]; then
    printf '%s\n' "$include_line" > "$ssh_main"
    chmod 600 "$ssh_main"
    echo "Created $ssh_main with '$include_line'"
elif ! grep -qE '^[[:space:]]*Include[[:space:]]+dotfiles\.conf[[:space:]]*$' "$ssh_main"; then
    printf '%s\n\n%s\n' "$(cat "$ssh_main")" "$include_line" > "$ssh_main.tmp"
    mv "$ssh_main.tmp" "$ssh_main"
    chmod 600 "$ssh_main"
    echo "Added '$include_line' to the bottom of $ssh_main"
else
    echo "$ssh_main already includes dotfiles.conf"
fi

# Per-machine config (real repo paths, default tbeam host, private completions)
# lives in ~/.zshrc.local, which .zshrc sources if present. It's a real copy (not
# a symlink) so it stays machine-specific and out of the repo. Seed it from the
# template on first run; never clobber an existing one.
if [[ ! -e "$HOME/.zshrc.local" ]]; then
    cp "$LINK_SRC/.zshrc.local.example" "$HOME/.zshrc.local"
    echo "Created ~/.zshrc.local from template — edit it with your repos (DEV_REPOS) and TBEAM_HOST."
fi

# Claude Code settings — ~/.claude/settings.json is a REAL COPY seeded from
# claude/settings.json.example (only the session-stamping hook the tmux tooling
# needs), the same pattern as ~/.zshrc.local. It is deliberately per-machine and
# untracked: Claude Code WRITES to this file at runtime (/model saves the default
# model, "always allow" appends permission rules, plugin toggles land here), so a
# tracked or symlinked copy keeps the repo dirty and risks committing private
# allow-rules. A legacy author-mode symlink into the repo (the old install
# choice 2) is materialized into a real copy of its current content. Never
# clobber an existing real file.
install_claude_settings() {
    local dst="$HOME/.claude/settings.json"
    local example="$LINK_SRC/claude/settings.json.example"

    mkdir -p "$HOME/.claude"

    if [[ -L "$dst" ]]; then
        if [[ -e "$dst" ]]; then
            # Live symlink — copy through it, then atomically replace the link.
            # mv -f does the rename in one step so a failure can't leave $dst gone.
            cp "$dst" "$dst.tmp"
            mv -f "$dst.tmp" "$dst"
            echo "Materialized $dst as a real copy (settings are per-machine now)"
        else
            rm "$dst"   # dangling link (target gone) — reseed from the example
        fi
    fi

    if [[ -e "$dst" ]]; then
        echo "Keeping existing $dst"
        return
    fi

    cp "$example" "$dst"
    echo "Created $dst from settings.json.example"
}
install_claude_settings

# Global git default — a pull reconciliation strategy so `git pull` never emits
# the "divergent branches" hint and never silently merges or rebases. ff-only: a
# pull that cannot fast-forward aborts and tells you to choose (git rebase / git
# merge) explicitly. This writes ~/.gitconfig, a REAL per-machine file (git also
# stores user.name/email and other runtime state there) — NOT a symlink, same
# reasoning as ~/.claude/settings.json. Only set it when neither pull.ff nor
# pull.rebase is already configured, so a deliberate per-machine choice is kept.
if [[ -z "$(git config --global --get pull.ff || true)" \
   && -z "$(git config --global --get pull.rebase || true)" ]]; then
    git config --global pull.ff only
    echo "Set global pull.ff -> only (git pull fast-forwards or aborts; no divergent-branch hint)"
else
    echo "Global pull strategy already set — leaving ~/.gitconfig as-is"
fi

# Point this repo's git at the tracked hooks so the PII pre-commit guard runs.
# Repo-local config (not a $HOME symlink); safe to re-run. The hook fails open
# when the private denylist is absent, so machines without it still commit.
git -C "$DOTFILES_DIR" config core.hooksPath .githooks
echo "Set core.hooksPath -> .githooks (PII pre-commit guard)"

# Materialize the private PII denylist when supplied (Cloud Agents / CI parity).
# Set PII_SCRUB_RULES to the scrub-rules.json contents in the agent environment.
# When the secret is unset/empty, remove any previously-materialized file so a
# revoked secret reverts to the documented fail-open behavior instead of leaving
# a stale denylist on disk. A sibling .from-secret marker records provenance so
# we never delete a hand-maintained local denylist (the documented macOS path).
if [[ -n "${PII_SCRUB_RULES:-}" ]]; then
    mkdir -p "$HOME/.config/pii-scan"
    printf '%s' "$PII_SCRUB_RULES" > "$HOME/.config/pii-scan/scrub-rules.json"
    chmod 600 "$HOME/.config/pii-scan/scrub-rules.json"
    : > "$HOME/.config/pii-scan/scrub-rules.json.from-secret"
    echo "Materialized PII denylist -> $HOME/.config/pii-scan/scrub-rules.json"
elif [[ -f "$HOME/.config/pii-scan/scrub-rules.json.from-secret" ]]; then
    rm -f "$HOME/.config/pii-scan/scrub-rules.json" \
          "$HOME/.config/pii-scan/scrub-rules.json.from-secret"
    echo "Removed stale PII denylist -> $HOME/.config/pii-scan/scrub-rules.json (PII_SCRUB_RULES unset)"
fi

# Periodic csync is handled by a precmd hook in .zshrc (linked above), not a
# launchd agent: iCloud Drive is TCC-protected and background agents are denied,
# whereas the shell runs in the Terminal's already-approved context. Nothing to
# set up here — the hook fires csync at most every 15 min from your prompt.

# pr-watch LaunchAgent — the autonomous PR fixer. Unlike csync, a launchd agent IS
# right here: pr-watch only talks to gh/git/tmux, none of them TCC-protected. We
# materialize the plist with real paths (launchctl can fail to bootstrap a symlinked
# plist, same reasoning as ~/.claude/settings.json) but leave it INERT — poll() is a
# no-op until `pr-watch enable` creates ~/.config/pr-watch/enabled, so a fresh clone
# never silently arms an agent that pushes code. Only (re)load it when already opted
# in, so re-running install.sh picks up plist changes without arming a new machine.
install_pr_watch() {
    local plist="$HOME/Library/LaunchAgents/$1"
    local label="${1%.plist}"
    mkdir -p "$HOME/Library/LaunchAgents"
    sed "s|__HOME__|$HOME|g" "$LINK_SRC/launchd/$1" > "$plist"
    if [[ -e "$HOME/.config/pr-watch/enabled" ]]; then
        launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
        if launchctl bootstrap "gui/$(id -u)" "$plist" 2>/dev/null; then
            echo "Reloaded pr-watch LaunchAgent (this machine is opted in)"
        else
            echo "Installed $plist but could not bootstrap it — run 'pr-watch enable'"
        fi
    else
        echo "Installed $plist (inert — run 'pr-watch enable' to arm the PR watcher)"
    fi
}
install_pr_watch "com.chrisobrien-ai.pr-watch.plist"

# Install the Homebrew tools the shell config depends on (gh, jq, tmux, fzf, glow).
# Idempotent — brew bundle skips anything already installed. Skipped entirely if
# Homebrew is absent; the config degrades gracefully without these. Set
# DOTFILES_NO_BREW=1 to skip it too (used by `dots --dev` for a fast relink).
if [[ -n "${DOTFILES_NO_BREW:-}" ]]; then
    echo "DOTFILES_NO_BREW set — skipping Brewfile."
elif command -v brew >/dev/null 2>&1; then
    echo "Installing Homebrew packages from Brewfile..."
    brew bundle --file="$LINK_SRC/Brewfile"
else
    echo "Homebrew not found — skipping Brewfile. Install it from https://brew.sh,"
    echo "then re-run this script (or 'brew bundle') to get gh/jq/tmux/fzf/glow."
fi

echo "Done."
