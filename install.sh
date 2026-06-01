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
link "$DOTFILES_DIR/claude/settings.json" "$HOME/.claude/settings.json"
link "$DOTFILES_DIR/claude/commands/tpush.md" "$HOME/.claude/commands/tpush.md"
link "$DOTFILES_DIR/claude/commands/tpop.md"  "$HOME/.claude/commands/tpop.md"
link "$DOTFILES_DIR/ssh/config"           "$HOME/.ssh/config"

# Periodic csync — a launchd user agent runs `csync` every 15 minutes so session
# history converges without anyone remembering to do it by hand. The plist can't
# expand $HOME and its program path is machine-specific, so we generate it here
# (rather than symlink a static file) and (re)load it. Output goes to a log so
# silent failures are debuggable. macOS-only; skipped where launchctl is absent.
if command -v launchctl >/dev/null 2>&1; then
    label="com.example.csync"
    plist="$HOME/Library/LaunchAgents/$label.plist"
    mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
    cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$label</string>
    <key>ProgramArguments</key>
    <array>
        <string>$HOME/bin/csync</string>
    </array>
    <key>StartInterval</key>
    <integer>900</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$HOME/Library/Logs/csync.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/Library/Logs/csync.log</string>
</dict>
</plist>
PLIST
    # Reload so edits to the interval/path take effect on re-run. bootout may
    # fail if it isn't currently loaded — that's fine, hence the `|| true`.
    domain="gui/$(id -u)"
    launchctl bootout "$domain/$label" 2>/dev/null || true
    launchctl bootstrap "$domain" "$plist"
    echo "Loaded launchd agent $label (csync every 15 min -> ~/Library/Logs/csync.log)"
    # One-time, can't be scripted: iCloud Drive is TCC-protected, and a launchd
    # agent has no iCloud access until its interpreter is in Full Disk Access.
    # Without this the run logs "Operation not permitted" on the iCloud path.
    echo "  ⚠ One-time setup required for the agent to reach iCloud:"
    echo "    System Settings → Privacy & Security → Full Disk Access → +"
    echo "    then add /bin/bash (in the file picker press ⌘⇧G and enter /bin/bash)."
    echo "    Verify after granting:  launchctl kickstart -k $domain/$label && cat ~/Library/Logs/csync.log"
else
    echo "launchctl not found — skipping periodic csync agent."
fi

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
