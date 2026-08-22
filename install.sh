#!/usr/bin/env bash
# install.sh — link dotfiles into $HOME on a fresh machine.
# Existing files are backed up to <name>.bak before being replaced.

set -euo pipefail

DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- One canonical checkout (read the "symlink model" note in CLAUDE.md) ---
# There is exactly ONE checkout of this repo: $PRIMARY (normally ~/code/dotfiles),
# parked on `main`. It IS the live surface — the $HOME symlinks point straight at it
# and `dots` fast-forwards it. All development happens in per-session worktrees
# ($DEV_WORKTREE_ROOT/<repo>/<slot> on dev/<repo>-<slot>), never here.
#
# This replaced an older TWO-tree layout (a dev clone plus a separate `main` worktree
# at ~/.local/share/dotfiles-main). That split existed because `dots` used to
# `git checkout main` inside the one tree that also held in-progress work, so a single
# `dots` reverted everyone's live config. Worktree-per-session removed the work from
# that tree, so the second tree bought nothing. The structural guarantee survives:
# git refuses to check out a branch already held by another worktree, so with $PRIMARY
# holding `main`, no session worktree can ever land on it.
#
# migrate_to_single_tree() below converts a legacy two-tree machine in place.
#
# Three link modes:
#   default              LINK_SRC = $PRIMARY      (live = the canonical checkout on main)
#   DOTFILES_LINK_DEV=1  LINK_SRC = $DOTFILES_DIR (live = the session worktree you are
#                        standing in; the fast inner-loop, set by `dots --dev`)
#   DOTFILES_LINKS_ONLY=1  link from $DOTFILES_DIR and stop (the offline relink `dots`
#                        runs every invocation; never migrates, never fetches)

# Legacy-only: where a pre-migration `main` worktree may still be sitting. Read in
# exactly two places (here and _dots_legacy_present in .zshrc) so a host that
# overrode the path still gets cleaned up. It is NOT a link source any more.
LEGACY_MAIN_WT="${DOTFILES_MAIN_WT:-$HOME/.local/share/dotfiles-main}"
LEGACY_TREES=""   # newline-separated, filled by migrate_to_single_tree

# samepath — compare two dirs by their PHYSICAL path. `git worktree list --porcelain`
# canonicalizes (/tmp -> /private/tmp), so a literal string compare gives false
# negatives and would wrongly treat $PRIMARY as a legacy tree.
samepath() {
    [ "$(cd "$1" 2>/dev/null && pwd -P)" = "$(cd "$2" 2>/dev/null && pwd -P)" ]
}

# resolve_primary — the ONE canonical checkout: the parent of the shared .git.
# Resolved from the COMMON dir (not $DOTFILES_DIR) so that running a session
# worktree's ./install.sh still links the canonical tree instead of itself.
# `--git-common-dir` is relative to the dir git RAN IN (".git" from the repo root,
# "../.git" from bin/, absolute from a linked worktree), so prefix with the -C dir.
resolve_primary() {
    local cdir
    cdir="$(git -C "$DOTFILES_DIR" rev-parse --git-common-dir 2>/dev/null)" || return 1
    [ -n "$cdir" ] || return 1
    case "$cdir" in /*) ;; *) cdir="$DOTFILES_DIR/$cdir" ;; esac
    ( cd "$cdir/.." && pwd )
}

PRIMARY="$(resolve_primary || true)"
if [[ -z "$PRIMARY" || ! -d "$PRIMARY/.git" ]]; then
    # A directory .git is exactly what distinguishes the primary checkout from a
    # linked worktree (whose .git is a FILE). Without this we could silently link
    # $HOME at something that is not a checkout at all.
    echo "install.sh: cannot resolve the primary checkout from $DOTFILES_DIR" >&2
    echo "  (need a normal clone with a .git directory; --separate-git-dir/bare is unsupported)" >&2
    exit 1
fi

# salvage_tree <wt> — never block, never lose data. Dump any local edits in an
# outgoing legacy tree to a patch BESIDE it (a sibling, so the later rm -rf cannot
# take the patch with it) and carry on. `git diff HEAD` covers staged AND unstaged
# changes; untracked files are copied separately since no diff carries them.
salvage_tree() {
    local w="$1" stamp patch f dir n
    if git -C "$w" diff --quiet HEAD 2>/dev/null \
       && [ -z "$(git -C "$w" ls-files --others --exclude-standard 2>/dev/null)" ]; then
        return 0
    fi
    stamp="$(date +%Y%m%d-%H%M%S)"
    patch="${w%/}.salvage-$stamp.patch"
    [ -w "$(dirname "$w")" ] || patch="$HOME/dotfiles-salvage-$stamp.patch"
    git -C "$w" diff HEAD > "$patch" 2>/dev/null || true
    n="$(wc -l < "$patch" | tr -d ' ')"
    echo "Salvaged $n line(s) of local edits from $w"
    echo "  -> $patch"
    echo "  re-apply later with: git -C \"$PRIMARY\" apply -3 \"$patch\""
    git -C "$w" ls-files --others --exclude-standard 2>/dev/null | while IFS= read -r f; do
        [ -n "$f" ] || continue
        dir="$patch.untracked/$(dirname "$f")"
        mkdir -p "$dir"
        cp -p "$w/$f" "$dir/" 2>/dev/null || true
        echo "  kept untracked $f -> $patch.untracked/$f"
    done
}

# migrate_to_single_tree — convert a legacy two-tree machine in place, WITHOUT ever
# leaving $HOME pointing at a directory that does not exist.
#
# The ordering is the whole trick. We do NOT need to remove the worktrees holding
# `main` before $PRIMARY can check it out — we only need to release the ref.
# `checkout --detach` frees refs/heads/main while leaving every file in that tree
# byte-identical on disk, so the $HOME symlinks keep resolving the entire time. The
# actual rm happens LAST, after link_all has already repointed everything at
# $PRIMARY. Die at any earlier step and the machine still works on the old links;
# re-running resumes idempotently.
migrate_to_single_tree() {
    local line w="" c seen="|"

    git -C "$PRIMARY" worktree prune 2>/dev/null || true

    # The set to act on is the UNION of (1) every worktree != $PRIMARY holding
    # refs/heads/main and (2) the legacy path if it still exists. (2) is not
    # optional: after a partial run the legacy tree is already DETACHED, so rule (1)
    # alone would miss it and orphan it forever.
    while IFS= read -r line; do
        case "$line" in
            "worktree "*) w="${line#worktree }" ;;
            "branch refs/heads/main")
                samepath "$w" "$PRIMARY" && continue
                c="$(cd "$w" 2>/dev/null && pwd -P)" || continue
                [ -n "$c" ] || continue
                case "$seen" in *"|$c|"*) continue ;; esac
                seen="$seen$c|"
                LEGACY_TREES="$LEGACY_TREES$w
" ;;
        esac
    done < <(git -C "$PRIMARY" worktree list --porcelain 2>/dev/null)

    if [ -e "$LEGACY_MAIN_WT" ] && ! samepath "$LEGACY_MAIN_WT" "$PRIMARY"; then
        c="$(cd "$LEGACY_MAIN_WT" 2>/dev/null && pwd -P)" || c=""
        case "$seen" in
            *"|$c|"*) : ;;
            *) LEGACY_TREES="$LEGACY_TREES$LEGACY_MAIN_WT
" ;;
        esac
    fi

    git -C "$PRIMARY" fetch -q origin 2>/dev/null || true

    # Capability probe. Only when there IS something to migrate, so a fresh clone and
    # an offline install never trip it. Without this, running a feature branch's
    # install.sh BEFORE the change is merged would migrate, then link from $PRIMARY @
    # main (which lacks the new code) — silently rolling the machine back, and the
    # next `dots` would rebuild the very worktree we just removed.
    if [ -n "$LEGACY_TREES" ] \
       && ! git -C "$PRIMARY" show origin/main:install.sh 2>/dev/null \
            | grep -q 'migrate_to_single_tree'; then
        echo "Legacy two-tree layout LEFT IN PLACE: origin/main does not carry the"
        echo "single-tree change yet. Merge it, then re-run. (Nothing was touched.)"
        return 1
    fi

    while IFS= read -r w; do
        [ -n "$w" ] || continue
        [ -d "$w" ] || continue
        salvage_tree "$w"
        # Detach at the SAME commit: index and working tree are preserved verbatim,
        # only the branch ref is released.
        git -C "$w" checkout -q --detach 2>/dev/null \
            || echo "warning: could not detach $w — main may still be held there"
    done <<EOF
$LEGACY_TREES
EOF

    git -C "$PRIMARY" show-ref --verify -q refs/heads/main \
        || git -C "$PRIMARY" branch -q --track main origin/main 2>/dev/null || true

    # set -e is load-bearing here: if this fails because $PRIMARY has conflicting
    # uncommitted edits, we die BEFORE link_all and before any deletion. Untouched.
    if [ "$(git -C "$PRIMARY" symbolic-ref --short -q HEAD || true)" != "main" ]; then
        echo "Moving $PRIMARY onto main (was $(git -C "$PRIMARY" symbolic-ref --short -q HEAD || echo detached))"
        git -C "$PRIMARY" checkout -q main
    fi
    git -C "$PRIMARY" merge --ff-only origin/main -q 2>/dev/null \
        || echo "note: $PRIMARY cannot fast-forward origin/main — linking local main as-is"
    return 0
}

# cleanup_legacy_main_worktrees — the actual removal, deliberately the LAST action of
# a full install: by now $HOME already points at $PRIMARY, so deleting these cannot
# strand anything. On a legacy machine `dots` runs "$live/install.sh" where $live IS
# one of these trees; bash keeps reading from its open fd, but cd to $PRIMARY first so
# nothing runs with a deleted cwd.
cleanup_legacy_main_worktrees() {
    local w
    [ -n "$LEGACY_TREES" ] || return 0
    cd "$PRIMARY" || return 0
    while IFS= read -r w; do
        case "$w" in ""|/|"$HOME") continue ;; esac
        samepath "$w" "$PRIMARY" && continue
        git -C "$PRIMARY" worktree remove --force "$w" 2>/dev/null \
            || { [ -e "$w" ] && rm -rf "$w"; }
        echo "Removed legacy main worktree -> $w"
    done <<EOF
$LEGACY_TREES
EOF
    git -C "$PRIMARY" worktree prune 2>/dev/null || true
}

# DOTFILES_LINKS_ONLY=1 is the fast, OFFLINE relink `dots` runs on every invocation
# (see _dots_relink in .zshrc): link from THIS tree — whichever copy of install.sh the
# caller chose to run — and stop right after link_all. Deliberately skips the
# migration, so it never fetches and never removes anything.
if [[ -n "${DOTFILES_LINKS_ONLY:-}" ]]; then
    LINK_SRC="$DOTFILES_DIR"
elif [[ -n "${DOTFILES_LINK_DEV:-}" ]]; then
    LINK_SRC="$DOTFILES_DIR"
    echo "Linking from the session worktree ($(git -C "$DOTFILES_DIR" symbolic-ref --short -q HEAD || echo '?')) — in-progress edits are live."
else
    # A refusal from the capability probe is a deliberate no-op, not a failure.
    migrate_to_single_tree || exit 0
    LINK_SRC="$PRIMARY"
fi


link() {
    local src="$1" dst="$2"
    # Already pointing where we want it: say nothing and touch nothing. This is what
    # lets `dots` relink unconditionally on every run and stay silent unless something
    # actually changed. Compare the RAW readlink, not a resolved realpath, so a link
    # whose target is not checked out yet still counts as correct instead of being
    # rewritten every time. It also closes the window where the old unconditional
    # rm + ln left ~/.zshrc briefly absent.
    if [[ -L "$dst" && "$(readlink "$dst")" == "$src" ]]; then
        return 0
    fi
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

# EVERY managed link lives in here, including the ssh one that used to sit further
# down with the Include rewrite. That matters: `t doctor` discovers the managed set by
# parsing these `link` lines (bin/t's _INSTALL_LINK_RE), so a link outside link_all
# would be a link doctor reports as drifted and the links-only relink never fixes —
# a relink that fires on every single `dots` forever.
link_all() {
    link "$LINK_SRC/.zshrc"               "$HOME/.zshrc"
    link "$LINK_SRC/.tmux.conf"           "$HOME/.tmux.conf"
    link "$LINK_SRC/bin/sleep-manager"    "$HOME/bin/sleep-manager"
    link "$LINK_SRC/bin/csync"            "$HOME/bin/csync"
    link "$LINK_SRC/bin/cursor-beam"      "$HOME/bin/cursor-beam"
    link "$LINK_SRC/bin/pii-scan"         "$HOME/bin/pii-scan"
    link "$LINK_SRC/bin/claude-stamp-tmux" "$HOME/bin/claude-stamp-tmux"
    link "$LINK_SRC/bin/t"                "$HOME/bin/t"
    link "$LINK_SRC/bin/pr-watch"         "$HOME/bin/pr-watch"
    link "$LINK_SRC/claude/commands/tpush.md" "$HOME/.claude/commands/tpush.md"
    link "$LINK_SRC/claude/commands/tpop.md"  "$HOME/.claude/commands/tpop.md"
    link "$LINK_SRC/claude/commands/todo.md"  "$HOME/.claude/commands/todo.md"
    # ~/.ssh must exist and be 0700 before the snippet can land in it. The Include
    # rewrite that pairs with this link stays below, in the full-install section.
    mkdir -p "$HOME/.ssh"
    chmod 700 "$HOME/.ssh"
    link "$LINK_SRC/ssh/config" "$HOME/.ssh/dotfiles.conf"
}

link_all

# Claude Code statusline — the open-task count for the slot, always on screen.
#
# This runs in the LINKS-ONLY path, above the exit below, so a plain `dots` applies
# it: the README's contract is "you rarely need to run install.sh by hand — dots
# reconciles", and a step only reachable by a manual full install breaks that (it is
# how a released change lands on one machine and silently not on another). Safe here
# for the same reasons link_all is: offline, idempotent, additive, and it never
# deletes — unlike the brew/launchd/PII steps the exit exists to skip.
#
# Seeding statusLine into settings.json.example only helps a FRESH machine:
# install_claude_settings never clobbers an existing settings.json (Claude writes to
# it at runtime), so every box that already has one would never get the key. Hence
# this targeted merge — it adds statusLine ONLY when absent, so a hand-set statusline
# is always kept, and it rewrites via tmp + os.replace so a crash cannot truncate the
# live settings file. Silent unless it actually changes something, because it runs on
# every single `dots`.
install_claude_statusline() {
    local dst="$HOME/.claude/settings.json"
    [[ -e "$dst" ]] || return 0          # nothing to merge into; the seed already has it
    command -v python3 >/dev/null 2>&1 || return 0
    python3 - "$dst" <<'PY'
import json, os, sys

dst = sys.argv[1]
try:
    with open(dst, encoding="utf-8") as fh:
        data = json.load(fh)
except (OSError, ValueError):
    sys.exit(0)          # not ours to repair, and never block a relink over it
if not isinstance(data, dict) or data.get("statusLine"):
    sys.exit(0)          # already set (or hand-set) — silent no-op, this runs every dots
# $HOME, not an expanded path: Claude runs these through a shell (the SessionStart
# hook uses the same form), so the setting survives a moved home directory.
data["statusLine"] = {"type": "command", "command": "$HOME/bin/t todo --statusline"}
tmp = dst + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
os.replace(tmp, dst)
print("Set statusLine in %s -> t todo --statusline (open task count for this slot)" % dst)
PY
}
install_claude_statusline

# Everything below is the FULL install. The links-only relink stops here, before the
# tmux source-file, the ssh Include rewrite, the ~/.zshrc.local and settings.json
# seeds, the global gitconfig/hooksPath writes, the PII denylist branch (which would
# DELETE the denylist when PII_SCRUB_RULES is unset — always true from a shell hook),
# the launchd agent restart, and brew bundle.
if [[ -n "${DOTFILES_LINKS_ONLY:-}" ]]; then
    exit 0
fi

# tmux reads ~/.tmux.conf only at SERVER START, so the link above is inert for an
# already-running server until re-sourced. Apply it here (dots' default path does
# the same via _dots_tmux_apply) so no rollout needs a manual `tmux source-file`.
# `tmux has-session` never leaves a stray server behind: one auto-started with no
# sessions exits immediately.
#
# DOTFILES_NO_TMUX=1 skips it, and that is not cosmetic: `tmux has-session` reaches the
# REAL server through the inherited $TMUX/socket regardless of $HOME, so a sandboxed run
# (the migration tests, which drive install.sh under a fake HOME) would source the
# SANDBOX's ~/.tmux.conf into the developer's live tmux server. Verified, not theorised —
# a fake-HOME install printed "Applied .tmux.conf to the running tmux server". It is also
# the one step here whose behaviour depends on whether a tmux server happens to exist,
# so it makes an otherwise hermetic test environment-dependent.
if [[ -z "${DOTFILES_NO_TMUX:-}" ]] \
   && command -v tmux >/dev/null 2>&1 && tmux has-session 2>/dev/null; then
    if tmux source-file "$HOME/.tmux.conf" 2>/dev/null; then
        echo "Applied .tmux.conf to the running tmux server"
    else
        echo "WARNING: tmux source-file ~/.tmux.conf failed - check the config" >&2
    fi
fi

# SSH config via Include, NOT a wholesale symlink. Symlinking ~/.ssh/config would
# replace any existing host entries (backed up to .bak, but still a surprise). So
# link our snippet to ~/.ssh/dotfiles.conf and make ~/.ssh/config pull it in with
# an `Include` at the very bottom. OpenSSH uses "first value wins" semantics, and
# our snippet's `Host *` defaults must lose to any per-host settings already in
# the user's config — so the include goes after them, not before. Non-destructive
# and idempotent.
ssh_main="$HOME/.ssh/config"
include_line="Include dotfiles.conf"
# An older install symlinked ~/.ssh/config straight at the repo; drop that link so
# we manage a real file (writing through the symlink would edit the repo copy).
[[ -L "$ssh_main" ]] && rm "$ssh_main"
if [[ ! -e "$ssh_main" ]]; then
    printf '%s\n' "$include_line" > "$ssh_main"
    chmod 600 "$ssh_main"
    echo "Created $ssh_main with '$include_line'"
elif ! grep -qE '^[[:space:]]*Include[[:space:]]+dotfiles\.conf[[:space:]]*$' "$ssh_main"; then
    printf '%s\n\n%s\n' "$(cat "$ssh_main")" "$include_line" > "$ssh_main.tmp"
    mv "$ssh_main.tmp" "$ssh_main"
    chmod 600 "$ssh_main"
    echo "Added '$include_line' to the bottom of $ssh_main"
else
    echo "$ssh_main already includes dotfiles.conf"
fi

# Per-machine config (real repo paths, default tbeam host, private completions)
# lives in ~/.zshrc.local, which .zshrc sources if present. It's a real copy (not
# a symlink) so it stays machine-specific and out of the repo. Seed it from the
# template on first run; never clobber an existing one.
if [[ ! -e "$HOME/.zshrc.local" ]]; then
    cp "$LINK_SRC/.zshrc.local.example" "$HOME/.zshrc.local"
    echo "Created ~/.zshrc.local from template — run 't setup' in a new shell to register your repos and remote hosts (or edit it by hand)."
fi

# Claude Code settings — ~/.claude/settings.json is a REAL COPY seeded from
# claude/settings.json.example (only the session-stamping hook the tmux tooling
# needs), the same pattern as ~/.zshrc.local. It is deliberately per-machine and
# untracked: Claude Code WRITES to this file at runtime (/model saves the default
# model, "always allow" appends permission rules, plugin toggles land here), so a
# tracked or symlinked copy keeps the repo dirty and risks committing private
# allow-rules. A legacy author-mode symlink into the repo (the old install
# choice 2) is materialized into a real copy of its current content. Never
# clobber an existing real file.
install_claude_settings() {
    local dst="$HOME/.claude/settings.json"
    local example="$LINK_SRC/claude/settings.json.example"

    mkdir -p "$HOME/.claude"

    if [[ -L "$dst" ]]; then
        if [[ -e "$dst" ]]; then
            # Live symlink — copy through it, then atomically replace the link.
            # mv -f does the rename in one step so a failure can't leave $dst gone.
            cp "$dst" "$dst.tmp"
            mv -f "$dst.tmp" "$dst"
            echo "Materialized $dst as a real copy (settings are per-machine now)"
        else
            rm "$dst"   # dangling link (target gone) — reseed from the example
        fi
    fi

    if [[ -e "$dst" ]]; then
        echo "Keeping existing $dst"
        return
    fi

    cp "$example" "$dst"
    echo "Created $dst from settings.json.example"
}
install_claude_settings

# Global git default — a pull reconciliation strategy so `git pull` never emits
# the "divergent branches" hint and never silently merges or rebases. ff-only: a
# pull that cannot fast-forward aborts and tells you to choose (git rebase / git
# merge) explicitly. This writes ~/.gitconfig, a REAL per-machine file (git also
# stores user.name/email and other runtime state there) — NOT a symlink, same
# reasoning as ~/.claude/settings.json. Only set it when neither pull.ff nor
# pull.rebase is already configured, so a deliberate per-machine choice is kept.
if [[ -z "$(git config --global --get pull.ff || true)" \
   && -z "$(git config --global --get pull.rebase || true)" ]]; then
    git config --global pull.ff only
    echo "Set global pull.ff -> only (git pull fast-forwards or aborts; no divergent-branch hint)"
else
    echo "Global pull strategy already set — leaving ~/.gitconfig as-is"
fi

# Point this repo's git at the tracked hooks so the PII pre-commit guard runs.
# Repo-local config (not a $HOME symlink); safe to re-run. The hook fails open
# when the private denylist is absent, so machines without it still commit.
git -C "$PRIMARY" config core.hooksPath .githooks
echo "Set core.hooksPath -> .githooks (PII pre-commit guard)"

# Materialize the private PII denylist when supplied (Cloud Agents / CI parity).
# Set PII_SCRUB_RULES to the scrub-rules.json contents in the agent environment.
# When the secret is unset/empty, remove any previously-materialized file so a
# revoked secret reverts to the documented fail-open behavior instead of leaving
# a stale denylist on disk. A sibling .from-secret marker records provenance so
# we never delete a hand-maintained local denylist (the documented macOS path).
if [[ -n "${PII_SCRUB_RULES:-}" ]]; then
    mkdir -p "$HOME/.config/pii-scan"
    printf '%s' "$PII_SCRUB_RULES" > "$HOME/.config/pii-scan/scrub-rules.json"
    chmod 600 "$HOME/.config/pii-scan/scrub-rules.json"
    : > "$HOME/.config/pii-scan/scrub-rules.json.from-secret"
    echo "Materialized PII denylist -> $HOME/.config/pii-scan/scrub-rules.json"
elif [[ -f "$HOME/.config/pii-scan/scrub-rules.json.from-secret" ]]; then
    rm -f "$HOME/.config/pii-scan/scrub-rules.json" \
          "$HOME/.config/pii-scan/scrub-rules.json.from-secret"
    echo "Removed stale PII denylist -> $HOME/.config/pii-scan/scrub-rules.json (PII_SCRUB_RULES unset)"
fi

# Periodic csync is handled by a precmd hook in .zshrc (linked above), not a
# launchd agent: iCloud Drive is TCC-protected and background agents are denied,
# whereas the shell runs in the Terminal's already-approved context. Nothing to
# set up here — the hook fires csync at most every 15 min from your prompt.

# pr-watch LaunchAgent — the autonomous PR fixer. Unlike csync, a launchd agent IS
# right here: pr-watch only talks to gh/git/tmux, none of them TCC-protected. We
# materialize the plist with real paths (launchctl can fail to bootstrap a symlinked
# plist, same reasoning as ~/.claude/settings.json) but leave it INERT — poll() is a
# no-op until `pr-watch enable` creates ~/.config/pr-watch/enabled, so a fresh clone
# never silently arms an agent that pushes code. Only (re)load it when already opted
# in, so re-running install.sh picks up plist changes without arming a new machine.
install_pr_watch() {
    local plist="$HOME/Library/LaunchAgents/$1"
    local label="${1%.plist}"
    mkdir -p "$HOME/Library/LaunchAgents"
    sed "s|__HOME__|$HOME|g" "$LINK_SRC/launchd/$1" > "$plist"
    if [[ -e "$HOME/.config/pr-watch/enabled" ]]; then
        launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
        if launchctl bootstrap "gui/$(id -u)" "$plist" 2>/dev/null; then
            echo "Reloaded pr-watch LaunchAgent (this machine is opted in)"
        else
            echo "Installed $plist but could not bootstrap it — run 'pr-watch enable'"
        fi
    else
        echo "Installed $plist (inert — run 'pr-watch enable' to arm the PR watcher)"
    fi
}
if [[ "$(uname)" == "Darwin" ]]; then
    install_pr_watch "com.chrisobrien-ai.pr-watch.plist"
else
    echo "Skipping pr-watch LaunchAgent (launchd is macOS-only)."
fi

# Install the Homebrew tools the shell config depends on (gh, jq, tmux, fzf, glow).
# Idempotent — brew bundle skips anything already installed. Skipped entirely if
# Homebrew is absent; the config degrades gracefully without these. Set
# DOTFILES_NO_BREW=1 to skip it too (used by `dots --dev` for a fast relink).
if [[ -n "${DOTFILES_NO_BREW:-}" ]]; then
    echo "DOTFILES_NO_BREW set — skipping Brewfile."
elif command -v brew >/dev/null 2>&1; then
    echo "Installing Homebrew packages from Brewfile..."
    brew bundle --file="$LINK_SRC/Brewfile"
elif [[ "$(uname)" != "Darwin" ]]; then
    # Linux host: no Brewfile — the shell config needs zsh + tmux (the remote
    # `zsh -lic` contract) and degrades gracefully without the rest.
    echo "Linux host — install the shell deps with your package manager, e.g.:"
    echo "  sudo apt-get install -y zsh tmux fzf jq   (gh: https://cli.github.com)"
else
    echo "Homebrew not found — skipping Brewfile. Install it from https://brew.sh,"
    echo "then re-run this script (or 'brew bundle') to get gh/jq/tmux/fzf/glow."
fi

# LAST real action: by now $HOME points at $PRIMARY, so removing the legacy trees
# cannot strand a symlink. No-op unless migrate_to_single_tree found something.
cleanup_legacy_main_worktrees

echo "Done."
