# Contributing

This is a **personal, opinionated dotfiles repo** — one person's macOS + zsh +
Claude Code setup, published in case the patterns are useful to borrow. It is not
a framework and isn't trying to be portable, configurable, or general-purpose.

## What that means for you

- **Fork freely.** The most useful thing you can do is copy whatever's handy into
  your own dotfiles. That's the point. No attribution needed (it's MIT).
- **Issues** for genuine bugs (a script breaks on a clean machine, a portability
  assumption that's easy to fix) are welcome and appreciated.
- **Pull requests**: small, focused fixes — portability, a clear bug, a typo — are
  welcome. Larger changes, new features, or "you should do it this way" reworks
  will usually be declined, not because they're wrong but because this repo tracks
  one person's preferences. Open an issue first if you're unsure.
- This is a single-author repo; the history prioritizes my workflow over a stable
  public API. `main` is branch-protected — changes land via PRs from `dev/claude-1`.

## Before you open a PR

- **Don't commit personal data.** A `pii-scan` pre-commit hook and a CI check guard
  against it (see the **PII guard** section in the README). Run `./install.sh` to
  wire up the hook locally.
- **No build or test step** — validate shell changes by sourcing the file
  (`source ~/.zshrc`) or running the script. Keep `set -euo pipefail` Bash scripts
  passing `zsh -n` / `bash -n`.
- Match the surrounding style: each command carries a leading
  `# name <args> — description` comment so `help` can list it automatically.

Thanks for looking.
