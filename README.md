# dotfiles

Personal shell config and utility scripts.

## Layout

- `.zshrc` — zsh shell config (aliases, functions, PATH)
- `bin/` — utility scripts (added to PATH)
  - `sleep-manager` — manage macOS sleep behavior (`status`, `disable`, `enable`)

The files in this repo are the source of truth. `~/.zshrc` and `~/bin/<script>`
are symlinks back into this repo, so editing either side edits both.

## Install on a new machine

```sh
git clone git@github.com:chrisjob1021/dotfiles.git ~/dotfiles
~/dotfiles/install.sh
```

`install.sh` creates the symlinks (backing up anything in the way to `*.bak`).
