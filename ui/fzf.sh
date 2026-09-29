#!/bin/sh
# Shared visual defaults for every dotfiles fzf picker. Call-specific arguments
# come last, so selection behavior, previews, and requested heights still win.
_t_fzf() {
  command fzf --height=60% --layout=reverse --border=rounded --margin=1,2 \
    --pointer='▸' --marker='✓' \
    --color='fg+:black,bg+:cyan,hl:cyan,hl+:black:underline,pointer:black,marker:green,header:cyan,prompt:cyan,border:bright-black' \
    "$@"
}
