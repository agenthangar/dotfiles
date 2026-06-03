#!/usr/bin/env bash
# install.sh — link dotfiles into $HOME on a fresh machine.
# Existing files are backed up to <name>.bak before being replaced.

set -euo pipefail

DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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

link "$DOTFILES_DIR/.zshrc"               "$HOME/.zshrc"
link "$DOTFILES_DIR/bin/sleep-manager"    "$HOME/bin/sleep-manager"
link "$DOTFILES_DIR/bin/csync"            "$HOME/bin/csync"
link "$DOTFILES_DIR/bin/pii-scan"         "$HOME/bin/pii-scan"
link "$DOTFILES_DIR/claude/settings.json" "$HOME/.claude/settings.json"
link "$DOTFILES_DIR/claude/commands/tpush.md" "$HOME/.claude/commands/tpush.md"
link "$DOTFILES_DIR/claude/commands/tpop.md"  "$HOME/.claude/commands/tpop.md"
link "$DOTFILES_DIR/ssh/config"           "$HOME/.ssh/config"

# Per-machine config (real repo paths, default tbeam host, private completions)
# lives in ~/.zshrc.local, which .zshrc sources if present. It's a real copy (not
# a symlink) so it stays machine-specific and out of the repo. Seed it from the
# template on first run; never clobber an existing one.
if [[ ! -e "$HOME/.zshrc.local" ]]; then
    cp "$DOTFILES_DIR/.zshrc.local.example" "$HOME/.zshrc.local"
    echo "Created ~/.zshrc.local from template — edit it with your repos (DEV_REPOS) and TBEAM_HOST."
fi

# Point this repo's git at the tracked hooks so the PII pre-commit guard runs.
# Repo-local config (not a $HOME symlink); safe to re-run. The hook fails open
# when the cashfwd-private denylist is absent, so machines without it still commit.
git -C "$DOTFILES_DIR" config core.hooksPath .githooks
echo "Set core.hooksPath -> .githooks (PII pre-commit guard)"

# Periodic csync is handled by a precmd hook in .zshrc (linked above), not a
# launchd agent: iCloud Drive is TCC-protected and background agents are denied,
# whereas the shell runs in the Terminal's already-approved context. Nothing to
# set up here — the hook fires csync at most every 15 min from your prompt.

# Install the Homebrew tools the shell config depends on (gum, glow, tmux, …).
# Idempotent — brew bundle skips anything already installed. Skipped entirely if
# Homebrew is absent; the config degrades gracefully without these.
if command -v brew >/dev/null 2>&1; then
    echo "Installing Homebrew packages from Brewfile..."
    brew bundle --file="$DOTFILES_DIR/Brewfile"
else
    echo "Homebrew not found — skipping Brewfile. Install it from https://brew.sh,"
    echo "then re-run this script (or 'brew bundle') to get gum/glow/tmux/gh/jq."
fi

echo "Done."
