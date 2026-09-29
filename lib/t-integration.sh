#!/usr/bin/env bash
# Shared by install.sh, dots and dots-sync. Keep this file valid in bash and zsh.
# The marker is a versioned ownership contract with agenthangar/t, not a directory probe.
_dots_t_valid() {
    [ -r "$1/.t-install-version" ] && [ "$(cat "$1/.t-install-version")" = 1 ] &&
        [ -r "$1/t.plugin.zsh" ] && [ -x "$1/install.sh" ] &&
        [ -x "$1/bin/t" ] && [ -x "$1/bin/claude-stamp-tmux" ] &&
        [ -x "$1/bin/cursor-beam" ]
}

_dots_t_find() {
    local candidate
    if [ -n "${DOTFILES_T_HOME:-}" ]; then
        _dots_t_valid "$DOTFILES_T_HOME" || return 1
        printf '%s\n' "$DOTFILES_T_HOME"
        return
    fi
    if [ -L "$HOME/bin/t" ] && command -v python3 >/dev/null 2>&1; then
        candidate=$(python3 -c 'import os,sys; print(os.path.dirname(os.path.dirname(os.path.realpath(sys.argv[1]))))' "$HOME/bin/t")
        if _dots_t_valid "$candidate"; then
            printf '%s\n' "$candidate"
            return
        fi
    fi
    candidate="$HOME/code/t"
    _dots_t_valid "$candidate" || return 1
    printf '%s\n' "$candidate"
}

_dots_t_environment() {
    export T_LOCAL_RC="${DOTFILES_T_LOCAL_RC:-$HOME/.zshrc.local}"
    export T_PERMISSIONS_DIR="${DOTFILES_T_PERMISSIONS_DIR:-$1/agents}"
    export T_NEW_OWNER="${T_NEW_OWNER:-agenthangar}"
    export T_AUTO_TRUST="${T_AUTO_TRUST:-1}"
    export T_PERMISSION_DEFAULTS="${T_PERMISSION_DEFAULTS:-1}"
    export T_DOTFILES_LIVE_TREE="$1"
    export T_NO_MCP="${T_NO_MCP:-${DOTFILES_NO_MCP:-}}"
    export T_NO_CODEX_HOOKS="${T_NO_CODEX_HOOKS:-${DOTFILES_NO_CODEX_HOOKS:-}}"
    export T_NO_PERMISSIONS="${T_NO_PERMISSIONS:-${DOTFILES_NO_PERMISSIONS:-}}"
    export T_NO_TRUST="${T_NO_TRUST:-${DOTFILES_NO_TRUST:-}}"
    export T_NO_AGENT_MODES="${T_NO_AGENT_MODES:-${DOTFILES_NO_AGENT_MODES:-}}"
    export T_NO_SUBAGENT_MODEL="${T_NO_SUBAGENT_MODEL:-${DOTFILES_NO_SUBAGENT_MODEL:-}}"
}

_dots_t_bootstrap() {
    local dotroot="$1" target
    [ -z "${DOTFILES_NO_T:-}" ] || return 0
    if ! _dots_t_find >/dev/null; then
        target="${DOTFILES_T_HOME:-$HOME/code/t}"
        if [ -e "$target" ]; then
            printf 'dots: %s exists but is not a supported standalone t checkout\n' "$target" >&2
            return 1
        fi
        mkdir -p "$(dirname "$target")" || return 1
        git clone -- https://github.com/agenthangar/t.git "$target" || return 1
        _dots_t_valid "$target" || return 1
    fi
    target=$(_dots_t_find) || return 1
    _dots_t_environment "$dotroot"
    "$target/install.sh"
}

_dots_t_is_live() {
    local here active common canonical
    here=$(cd "$1" 2>/dev/null && pwd -P) || return 1
    active=$(_dots_t_find) || return 1
    active=$(cd "$active" && pwd -P) || return 1
    [ "$here" = "$active" ] && return 0
    common=$(git -C "$active" rev-parse --git-common-dir 2>/dev/null) || return 1
    case "$common" in /*) ;; *) common="$active/$common" ;; esac
    canonical=$(cd "$common/.." && pwd -P) || return 1
    [ "$here" = "$canonical" ]
}

_dots_t_install() {
    local dotroot="$1" troot
    [ -z "${DOTFILES_NO_T:-}" ] || return 1
    troot=$(_dots_t_find) || return 1
    _dots_t_environment "$dotroot"
    # The active t source owns its links, even during dots --dev/--relink.
    T_LINKS_ONLY=1 T_LINK_DEV=1 "$troot/install.sh"
}

# Called before a fast-forward that removes the bundled implementation. Failure
# leaves the old checkout and its executable links intact, so offline recovery works.
_dots_t_preflight() {
    local dotroot="$1"
    [ -z "${DOTFILES_NO_T:-}" ] || return 0
    if git -C "$dotroot" cat-file -e origin/main:bin/t 2>/dev/null; then
        return 0
    fi
    if _dots_t_find >/dev/null && _dots_t_install "$dotroot"; then
        return 0
    fi
    printf '%s\n' 'dots: standalone t must be installed before this update.' >&2
    printf '%s\n' '  git clone https://github.com/agenthangar/t.git ~/code/t' >&2
    # shellcheck disable=SC2016
    printf '%s\n' '  T_LOCAL_RC="$HOME/.zshrc.local" ~/code/t/install.sh' >&2
    return 1
}
