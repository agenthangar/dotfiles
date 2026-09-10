# Brewfile — third-party tools this dotfiles setup depends on.
#
# Install / refresh:   brew bundle --file=Brewfile   (install.sh runs this too)
# It's idempotent: anything already present is skipped.
#
# Only tools the config actually calls are listed. macOS built-ins it also uses
# (caffeinate, pmset, rsync, sed, awk, column, less, git) are intentionally left
# out — they ship with the OS / Xcode CLT.

brew "gh"    # prview() / pr-watch            — PR status + polling via the GitHub CLI
brew "jq"    # prview()                       — parse gh's JSON output
brew "tmux"  # t open / t pop / t paste / t read / pr-watch — the Claude Code dev-session workflow
brew "fzf"   # t push / t open / t find       — fuzzy-pick a Claude session
brew "glow"  # t plan                         — render Claude plan markdown in-terminal

# The agent CLIs: `t install` is the cross-platform, interactive path (it also logs
# in); the cask is here so a plain install.sh on a Mac lands codex too. claude and
# cursor-agent ship as vendor install scripts, not casks — t install runs those.
cask "codex" # t open --codex / t install     — OpenAI Codex CLI
