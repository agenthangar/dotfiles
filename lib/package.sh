#!/usr/bin/env bash
# Homebrew owns the runtime and upgrades; the account owns its loader/settings.
dots_package_main() {
    local root="$1" stable command mode=update all=0
    shift
    stable=$(cat "$root/.dotfiles-package")
    [[ "$stable" == /* && -x "$stable/install.sh" ]] || {
        echo 'dots: Homebrew package is missing; reinstall agenthangar/tap/dotfiles' >&2
        return 1
    }
    command=${1:-}
    case "$command" in
        help|-h|--help) usage; return ;;
        install)
            shift
            case "${1:-}" in
                -h|--help)
                    echo 'Usage: dots install'
                    echo 'Apply the installed Homebrew package to this account; no repository is cloned.'
                    return ;;
            esac
            mode=install ;;
        update) shift ;;
        --package-apply) shift; mode=apply ;;
        ""|--all|-a|--relink|--dev|-d) ;;
        *) echo "dots: unknown command: $command" >&2; return 2 ;;
    esac
    if [[ "$mode" == install ]]; then
        [[ $# == 0 ]] || { echo 'dots install: Homebrew manages the installation directory; no checkout is needed.' >&2; return 2; }
    else
        case "${1:-}" in
            --all|-a) all=1; shift ;;
            --relink) mode=relink; shift ;;
            --dev|-d)
                echo 'dots --dev: use DOTFILES_LINK_DEV=1 ./install.sh inside a dotfiles development worktree.' >&2
                return 2 ;;
            -h|--help) usage; return ;;
        esac
        [[ $# == 0 ]] || { echo "dots: unexpected argument: $1" >&2; return 2; }
    fi
    # The first run applies what was just installed. Subsequent updates let
    # Homebrew select the release, then exec through opt after Cellar cleanup.
    if [[ "$mode" == update && -f "$HOME/.config/dotfiles/installed" ]]; then
        brew upgrade agenthangar/tap/dotfiles || return $?
        if [[ "$all" == 1 ]]; then
            exec "$stable/bin/dots" --package-apply --all
        else
            exec "$stable/bin/dots" --package-apply
        fi
    fi
    if [[ "$mode" == install || ( "$mode" != relink && ! -f "$HOME/.config/dotfiles/installed" ) ]]; then
        bash "$stable/install.sh" || return $?
    else
        DOTFILES_LINKS_ONLY=1 bash "$stable/install.sh" || return $?
        if [[ -z "${DOTFILES_NO_TMUX:-}" ]] && command -v tmux >/dev/null 2>&1 && tmux has-session 2>/dev/null; then
            tmux source-file "$HOME/.tmux.conf" || return $?
        fi
    fi
    echo "✓ Homebrew dotfiles applied — $(cat "$stable/.dotfiles-version")"
    if [[ "$all" == 1 ]]; then
        "$stable/bin/dots-sync" --hosts-only
    fi
}
