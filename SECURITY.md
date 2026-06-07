# Security Policy

This is a personal dotfiles repo, not a supported product or framework. Security
fixes for the published scripts and install flow are still welcome.

## Reporting a vulnerability

If the issue involves a secret, token, private host, or other sensitive detail,
do not open a public issue. Use GitHub's private vulnerability reporting for
this repository if it is enabled.

For non-sensitive security bugs, open a GitHub issue with:

- the affected file or command,
- the behavior you expected,
- the behavior you saw,
- the smallest reproduction steps you can share safely.

## Scope

In scope:

- committed scripts and shell config in this repository,
- `install.sh` behavior,
- CI and PII-scanning guardrails.

Out of scope:

- private local files such as `~/.zshrc.local`, `~/.claude.json`, SSH keys, or
  the private `scrub-rules.json` denylist,
- third-party tools installed by Homebrew or Claude Code,
- support requests for adapting these dotfiles to another machine.
