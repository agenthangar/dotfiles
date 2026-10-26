#!/usr/bin/env bash
# Optional, narrowly scoped passwordless sleep controls for macOS.
install_sudo_trust() {
    local uid rule destination temporary answer
    [[ -z "${DOTFILES_NO_SUDO:-}" && -z "${DOTFILES_LINKS_ONLY:-}" ]] || return 0
    [[ "$(uname -s)" == Darwin && -t 0 && -t 1 ]] || return 0
    uid=$(id -u)
    [[ "$uid" != 0 ]] || return 0
    command -v sudo >/dev/null 2>&1 || return 0
    destination="/etc/sudoers.d/dotfiles-sleep-$uid"
    rule="#$uid ALL=(root) NOPASSWD: /usr/bin/pmset -a disablesleep 1, /usr/bin/pmset -a disablesleep 0, /usr/bin/pmset -a disablesleep 0 sleep 10 displaysleep 10 disksleep 10"
    if [[ -r "$destination" ]] && [[ "$(cat "$destination")" == "$rule" ]]; then
        return 0
    fi
    echo
    echo "Trust dotfiles sleep controls with passwordless sudo?"
    echo "This lets your account (including programs running as you) disable sleep,"
    echo "re-enable sleep, and restore sleep-manager's default power settings."
    echo "Only those exact /usr/bin/pmset commands are allowed."
    echo "The persistent rule is saved in $destination."
    echo "Remove it with: sudo rm $destination"
    printf 'Enable this access? [y/N] '
    IFS= read -r answer || answer=""
    case "$answer" in y|Y|yes|YES) ;; *) echo "Skipped sudo trust."; return 0 ;; esac

    temporary=$(mktemp) || return 1
    printf '%s\n' "$rule" > "$temporary"
    # Validate before installing a root-owned regular file, never a checkout link.
    if sudo /usr/sbin/visudo -cf "$temporary" \
        && sudo /usr/bin/install -d -o root -g wheel -m 0755 /etc/sudoers.d \
        && sudo /usr/bin/install -o root -g wheel -m 0440 "$temporary" "$destination"; then
        echo "Enabled passwordless sudo for dotfiles sleep controls."
    else
        echo "Could not enable sudo trust; sleep controls will still request your password." >&2
    fi
    rm -f "$temporary"
}
