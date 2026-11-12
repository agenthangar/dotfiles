export PATH="$HOME/bin:$HOME/.local/bin:$PATH"

# Shared picker design (same palette as the inline t wizards).
_t_fzf() { command fzf "$@"; }
[[ -r "${${(%):-%x}:A:h}/ui/fzf.sh" ]] && source "${${(%):-%x}:A:h}/ui/fzf.sh"

# Hardened hosts can ship a root-only /tmp (the openclaw gateway does: drwx------
# root). That breaks every non-root temp-file user — zsh heredocs (TMPPREFIX),
# mktemp, and tmux's socket dir — so fall back to a per-user tmp. TMUX_TMPDIR
# matters most: the whole `t` slot machinery rides on tmux, and every t-driven
# tmux call goes through `zsh -lic`, so setting it here keeps client + server on
# one socket dir. No-op on a standard 1777 /tmp.
# Gate on /tmp ITSELF, not $TMPDIR: TMPDIR is exported and inherited, but
# TMPPREFIX is shell-local — a nested `zsh -lic` (how bin/t runs every helper)
# inherits the writable TMPDIR, and a TMPDIR-based gate would skip this block
# there, leaving the nested shell's heredocs pointed at the unwritable /tmp/zsh.
if [[ ! -w /tmp ]]; then
  export TMPDIR="${TMPDIR:-$HOME/.cache/tmp}"
  mkdir -p "$TMPDIR" 2>/dev/null
  TMPPREFIX="$TMPDIR/zsh"
  export TMUX_TMPDIR="${TMUX_TMPDIR:-$TMPDIR}"
fi

# Initialize the completion system before sourcing anything that calls `compdef`
# (e.g. a completion sourced from ~/.zshrc.local below). Without this, compdef is
# undefined and sourcing such a completion errors with "command not found: compdef".
autoload -Uz compinit && compinit

# _help_for <name> — render the doc-comment block directly above the zsh function
# <name> as styled help. The comment that documents each command IS its help text,
# so there's a single source of truth: every command's `-h`/`--help` just calls
# this. Captures only the contiguous `#` lines immediately preceding `name() {`; a
# *blank* line resets the block (a bare `#` is kept as a spacer), so design
# rationale can sit above the help separated by one empty line. The renderer styles
# a light, gh-style convention — write plain text in the comment, get structure for
# free:
#   line 1                     "<name> — <tagline>"     → name bold
#   Usage: …                   the "Usage:" label bold
#   Section name:              a capitalized heading ending in a colon → header
#   SECTION NAME               an uppercase heading → header
#   <2sp><term>  <2+sp><desc>  two-column row → term cyan, desc dim, auto-aligned
#   <6sp>--flag               long-only flag aligned below short/long flag pairs
#   anything else              printed verbatim (paragraphs, blank spacer lines)
# Color is emitted only when stdout is a TTY (so `cmd -h | cat` stays plain).
_help_for() {
  local name="${1:?_help_for: need a function name}"
  awk -v fn="$name" '
    /^#/                  { blk = blk $0 ORS; next }
    $0 ~ "^" fn "\\(\\)"  { printf "%s", blk; found = 1; exit }
                          { blk = "" }
    END                   { exit !found }
  ' "${_DOTS_PACKAGE_ROOT:-$_DOTS_SOURCE_ROOT}/.zshrc" | sed 's/^# \{0,1\}//' | _help_style
}

# _help_style — the gh-style RENDERER, split out from _help_for so the dynamic
# no-arg/error paths (which list the actual ${(k)DEV_REPOS}, not a static comment)
# render identically to `-h`. Reads plain text on stdin and styles it by the same
# conventions documented above _help_for: line 1 "<name> — <tagline>" bolds the
# name; a "Usage:" line bolds the label; a bare "Section name:" is a header;
# 2-space-indented "term  <2+sp>desc" rows become cyan/dim two-column rows, their
# descriptions auto-aligned to the widest term. Color only when stdout is a TTY —
# and because this is the LAST stage of every help pipe, `-t 1` here is the real
# terminal test (so `cmd -h | cat` still comes out plain).
_help_style() {
  local b='' d='' c='' r=''
  [[ -t 1 && -z ${NO_COLOR:-} && ${TERM:-} != dumb ]] && { b=$'\e[1m' d=$'\e[2m' c=$'\e[36m' r=$'\e[0m'; }
  awk -v b="$b" -v d="$d" -v c="$c" -v r="$r" '
    # Pass 1: buffer every line, detecting two-column rows and the widest term so
    # descriptions can align to a common column regardless of input spacing. A line
    # indented 3+ spaces that is NOT a 2-space kv row is a description CONTINUATION
    # (a wrapped second line of a row description) — remembered de-indented so pass 2
    # can hang it under the aligned description column, not its hand-typed indent.
    {
      raw[NR] = $0
      kvline = 0; contline = 0
      if ($0 ~ /^  [^ ]/ || $0 ~ /^      --/) {
        body = substr($0, 3)
        prefix = ""
        if (body ~ /^    --/) { prefix = "    "; body = substr(body, 5) }
        if (match(body, / {2,}/)) {
          iskv[NR]  = 1; kvline = 1
          kterm[NR] = prefix substr(body, 1, RSTART - 1)
          kdesc[NR] = substr(body, RSTART + RLENGTH)
          if (length(kterm[NR]) > maxw) maxw = length(kterm[NR])
        }
      } else if (eligible && $0 ~ /^   +[^ ]/) {
        # an indented line right after a kv row (or its continuation) is a wrapped
        # second line of that description — re-indented in pass 2. A Usage: line or
        # paragraph is NOT eligible, so its indented follow-on stays verbatim.
        iscont[NR] = 1; contline = 1
        ct = $0; sub(/^ +/, "", ct); cont[NR] = ct
      }
      eligible = (kvline || contline)
    }
    # Pass 2: classify and colorize each line.
    END {
      for (n = 1; n <= NR; n++) {
        line = raw[n]
        if (n == 1 && index(line, " — ")) {
          i = index(line, " — ")
          printf "%s%s%s%s\n", b, substr(line, 1, i - 1), r, substr(line, i)
        } else if (iskv[n]) {
          printf "  %s%s%s%*s%s%s%s\n", \
            c, kterm[n], r, maxw - length(kterm[n]) + 2, "", d, kdesc[n], r
        } else if (iscont[n]) {
          printf "%*s%s%s%s\n", maxw + 4, "", d, cont[n], r
        } else if (line ~ /^Usage:/) {
          printf "%s%s%s%s\n", b, "Usage:", r, substr(line, 7)
        } else if (line ~ /^[A-Z][A-Za-z &-]*:$/ || line ~ /^[A-Z][A-Z &-]+$/) {
          printf "%s%s%s\n", b, line, r
        } else {
          print line
        }
      }
    }
  '
}

# --- ssh-agent: one persistent agent reachable from every shell ----------
# macOS only hands its launchd ssh-agent to GUI apps, so inbound SSH sessions
# (and some terminals) start with SSH_AUTH_SOCK unset and `ssh-add` dies with
# "Could not open a connection to your authentication agent". Pin the socket to
# a stable path and start one agent only if none is reachable; every shell then
# reuses it, so a key added once stays loaded until reboot. An empty agent is
# refilled from the macOS Keychain (--apple-load-keychain), so a reboot does
# not bring back per-connection passphrase prompts — background ssh users
# (worktree-sweep fetches) prompt-spam the console otherwise.
# Requires the passphrase to be IN the Keychain: one-time per machine, run
#   ssh-add --apple-use-keychain ~/.ssh/id_ed25519
# (UseKeychain in ~/.ssh/config only READS the Keychain; it never writes it.)
# The load fails silently where the Keychain is locked (inbound ssh sessions).
export SSH_AUTH_SOCK="$HOME/.ssh/agent.sock"
ssh-add -l >/dev/null 2>&1
case $? in
  2)                             # no agent reachable: spawn one, then fill it
    rm -f "$SSH_AUTH_SOCK"       # clear any stale socket from a dead agent
    ssh-agent -a "$SSH_AUTH_SOCK" >/dev/null 2>&1
    ssh-add --apple-load-keychain >/dev/null 2>&1
    ;;
  1)                             # agent up but empty (e.g. keys were cleared)
    ssh-add --apple-load-keychain >/dev/null 2>&1
    ;;
esac

# prview — PR status at a glance: mergeability, merge state, per-check verdicts
#
# Usage: prview [pr#]
#
#   pr#   PR number to inspect; omit to use the current branch's PR
#
# Hides body/comments/diff — shows just mergeability, merge state, and per-check
# counts (pass/fail/neutral/pending) plus a sorted per-check verdict list.
prview() {
  [[ "$1" == -h || "$1" == --help ]] && { _help_for prview; return 0; }
  gh pr view "$@" --json mergeable,mergeStateStatus,statusCheckRollup | jq '
    {
      mergeable,
      state: .mergeStateStatus,
      pass:    [.statusCheckRollup[] | select(.conclusion == "SUCCESS")] | length,
      fail:    [.statusCheckRollup[] | select(.conclusion == "FAILURE")] | length,
      neutral: [.statusCheckRollup[] | select(.conclusion == "NEUTRAL")] | length,
      pending: [.statusCheckRollup[] | select(.status != "COMPLETED")] | length,
      checks:  [.statusCheckRollup[] | "\(if .status != "COMPLETED" then .status else .conclusion end): \(.name)"] | sort
    }
  '
}

# nosleep — prevent system sleep (installed with t)
#
# Run nosleep --help for monitoring, saved defaults, and hotspot setup.
# t's plugin replaces this fallback with the shared runtime when available.
nosleep() {
  if (( $+commands[nosleep] )); then
    command nosleep "$@"
  else
    print -u2 -- 'nosleep: install or update t; nosleep is now bundled with agenthangar/t'
    return 127
  fi
}
# Preserve associative overrides in local config even when t is not installed.
typeset -gA NOSLEEP_APPS

# _dots_tmux_apply — push ~/.tmux.conf into an already-running tmux server. tmux
# reads the file only at SERVER START, so a released config change is silently
# inert for the long-lived server until re-sourced — which read as "the fix
# didn't work" after a dots. Called from dots' default path; install.sh has its
# own copy of this step (covers --dev, migration, and manual installs), so no
# rollout ever needs a manual `tmux source-file`. No server → no-op (and no
# stray server: one auto-started with no sessions exits immediately).
# _dots_relink — reconcile the install.sh-managed $HOME symlinks with a checkout.
# $1 = the tree to link FROM; we run that tree's own install.sh, and its
# DOTFILES_LINKS_ONLY mode links from wherever it lives, so the arg picks the source
# with no path guessing (dots already holds both paths in locals).
#
# Why unconditional rather than "detect drift, then fix": the fixer IS the checker, so
# they cannot disagree and there is no second copy of the link list to keep in sync.
# It is offline, sub-10ms, and silent unless a link actually changed, because
# install.sh's link() no-ops on an already-correct symlink. A diff-based check ("did
# this dots pull a commit touching install.sh?") would NOT have caught the case this
# exists for: a machine that already fast-forwarded past the commit adding a link and
# simply never relinked.
_dots_relink() {
  local wt="$1" out
  [[ -x "$wt/install.sh" ]] || return 0
  # Capability check, NOT decoration. During the rollout window this .zshrc can be live
  # (via dots --dev) while $wt/install.sh is still the pre-merge copy that has never
  # heard of DOTFILES_LINKS_ONLY — it would ignore the var and run the FULL install:
  # brew bundle, the launchd restart, and the PII branch that DELETES the denylist when
  # PII_SCRUB_RULES is unset. Verified by hitting exactly that in a sandbox. An
  # install.sh that cannot do links-only simply does not get relinked here.
  grep -q 'DOTFILES_LINKS_ONLY' "$wt/install.sh" 2>/dev/null || return 0
  if ! out=$(DOTFILES_LINKS_ONLY=1 "$wt/install.sh" 2>&1); then
    print -r -- "dots — ⚠ relink failed:" >&2
    print -r -- "$out" >&2
    return 1
  fi
  [[ -n "$out" ]] && print -r -- "$out"
  return 0
}

_dots_tmux_apply() {
  [[ -r ~/.tmux.conf ]] && command -v tmux >/dev/null 2>&1 && tmux has-session 2>/dev/null || return 0
  tmux source-file ~/.tmux.conf 2>/dev/null \
    || print -r -- "dots — ⚠ tmux source-file ~/.tmux.conf failed (check the config)" >&2
}

# _dots_live_tree — the checkout the $HOME symlinks currently point at, empty if the
# machine has never been installed. THE single source of truth for "which tree is
# LIVE", and deliberately repo-agnostic: it protects whatever tree happens to be live
# rather than "dotfiles" by name. Used by dots, by _dev_repo_prepare (never switch the
# live tree's branch) and by the worktree sweep (never reap the live tree).
_dots_live_tree() {
  if [[ -n ${_DOTS_PACKAGE_ROOT:-} ]]; then
    print -r -- "$_DOTS_PACKAGE_ROOT"
    return
  fi
  local z=$HOME/.zshrc d
  [[ -L $z ]] || return 1
  d=${z:A:h}
  [[ -d $d ]] || return 1
  git -C "$d" rev-parse --show-toplevel 2>/dev/null
}

# _dots_resolve — set $live (the tree $HOME points at) and $primary (the ONE canonical
# checkout = parent of the shared .git). Returns 1 when there is no usable repo at all.
#
# The validation is not defensive padding. The old inline version did:
#     commondir=$(git -C "$live" rev-parse --git-common-dir 2>/dev/null)
#     [[ $commondir == /* ]] || commondir="$live/$commondir"
# so when $live was NOT a git checkout (~/.zshrc a real file, or a dangling link
# mid-migration) commondir came back EMPTY, became "$live/", and $primary silently
# resolved to the PARENT DIRECTORY of live — then everything downstream operated on
# garbage. Masked under the two-tree layout; reachable now.
_dots_resolve() {
  live=""; primary=""
  local z=$HOME/.zshrc d cdir
  if [[ -L $z ]]; then
    d=${z:A:h}
    if [[ -d $d ]] && cdir=$(git -C "$d" rev-parse --git-common-dir 2>/dev/null) && [[ -n $cdir ]]; then
      live=$(git -C "$d" rev-parse --show-toplevel 2>/dev/null)
      [[ $cdir == /* ]] || cdir="$d/$cdir"
      primary="${cdir:A:h}"
    fi
  fi
  # Never installed, or the live tree is gone: fall back to the configured repo dir.
  if [[ -z $primary ]]; then
    primary="${DEV_REPOS[dotfiles]:-${DEV_REPOS[dot]:-$HOME/code/dotfiles}}"
  fi
  [[ -d $primary/.git ]]
}

# _dots_legacy_present — is a pre-migration two-tree layout still (partly) here?
# Either the legacy worktree dir survives, or some non-primary worktree still holds
# refs/heads/main. Needed because once the links have flipped, `live != primary` and
# `primary is off main` are both FALSE while the old tree is still registered.
_dots_legacy_present() {
  local primary="$1" legacy="${DOTFILES_MAIN_WT:-$HOME/.local/share/dotfiles-main}" h
  [[ -e $legacy && ${legacy:A} != ${primary:A} ]] && return 0
  # Collect into an array first: a `... | while read` body is a SUBSHELL in zsh, so a
  # `return 0` inside it is swallowed and the function always reports "clean".
  local -a holders
  holders=(${(f)"$(git -C "$primary" worktree list --porcelain 2>/dev/null \
    | awk '/^worktree /{w=substr($0,10)} /^branch refs\/heads\/main$/{print w}')"})
  for h in $holders; do
    [[ -n $h && ${h:A} != ${primary:A} ]] && return 0
  done
  return 1
}

# dots — update your LIVE dotfiles to origin/main and reload zsh
#
# Usage: dots system update [--all]
#        dots system integrate [--relink]
#        dots install [DIRECTORY]
#        dots update [--all | --dev | --relink]
#        dots [--all | --dev | --relink] (compatible update alias)
#
# There is ONE canonical checkout (normally ~/code/dotfiles), parked on `main`, and it
# IS the live surface — the $HOME symlinks point straight at it. Default `dots`
# fast-forwards it to origin/main and re-sources ~/.zshrc, so your live config becomes
# exactly what is published on main. It cannot blast away in-progress work because no
# work lives there: every session develops in its own worktree via `t open dotfiles`.
# (A machine still on the old two-tree layout is migrated in place on the first run.)
#
# It also RECONCILES the $HOME symlinks every run, so a released change that adds a
# managed file lands by itself. Before this, dots only fast-forwarded, and a new `link`
# call in install.sh silently never applied — that is how a fresh ~/.tmux.conf failed
# to land and how `t resume` once vanished. Silent when nothing changed.
#
# Flags:
#   --all, -a     dots here, then on every REMOTE_HOSTS host in parallel (dots-sync)
#   --dev, -d     make the session worktree you are STANDING IN live (see below)
#   --relink      reconcile the live symlinks now, without fetching
#
# --dev (-d): flip the live surface to the dotfiles checkout you are STANDING IN —
# re-links from it (catching new/renamed files) and reloads, so its in-progress edits
# go live for testing without merging. The cwd is the choice: a per-session worktree
# (`t cd dot <slot>`, then `dots --dev`). Anywhere else it errors — no fallback, no
# guessing, because the old implicit fallback silently linked a stale tree while
# reporting ✓. Refused as sources: any non-dotfiles dir, a checkout with no runnable
# install.sh, and the canonical checkout itself (it is already live and plain `dots`
# manages it — the error lists your session worktrees instead). A later plain `dots`
# flips live back to the canonical checkout. Skips brew bundle.
# Load the standalone t bridge from this shell's own dotfiles source.
_DOTS_SOURCE_ROOT=${${(%):-%N}:A:h}
_DOTS_PACKAGE_ROOT=
_DOTS_PACKAGE_VERSION=
if [[ -r $_DOTS_SOURCE_ROOT/.dotfiles-package ]]; then
  _DOTS_PACKAGE_ROOT=$(<"$_DOTS_SOURCE_ROOT/.dotfiles-package")
  _DOTS_PACKAGE_VERSION=$(<"$_DOTS_PACKAGE_ROOT/.dotfiles-version")
fi
[[ -r $_DOTS_SOURCE_ROOT/lib/t-integration.sh ]] && source "$_DOTS_SOURCE_ROOT/lib/t-integration.sh"

dots() {
  if [[ -n ${_DOTS_PACKAGE_ROOT:-} ]]; then
    "$_DOTS_PACKAGE_ROOT/bin/dots" "$@"
    local result=$?
    if (( result == 0 )); then
      case "$*" in *help*|*-h*) ;; *) source ~/.zshrc ;; esac
    fi
    return $result
  fi
  if [[ "$1" == system && "$2" == update ]]; then
    shift 2
    if (( $# > 1 )) || { (( $# == 1 )) && [[ "$1" != --all && "$1" != -a && "$1" != -h && "$1" != --help ]]; }; then
      print -u2 -- 'Usage: dots system update [--all]'
      return 2
    fi
    set -- update "$@"
  fi
  if [[ "$1" == system && "$2" == integrate && "$3" == --relink && $# == 3 ]]; then
    set -- --relink
  fi
  case "$1" in
    system)
      "$_DOTS_SOURCE_ROOT/bin/dots" "$@"
      local result=$?
      if (( result == 0 )) && [[ "$2" == integrate && $# == 2 ]]; then
        source ~/.zshrc
      fi
      return $result ;;
    install|help|-h|--help)
      "$_DOTS_SOURCE_ROOT/bin/dots" "$@"
      return $? ;;
    update) shift ;;
  esac
  case "$1" in
    -h|--help) "$_DOTS_SOURCE_ROOT/bin/dots" update --help; return $? ;;
    ""|--all|-a|--dev|-d|--relink) ;;
    *) print -u2 -r -- "dots: unknown command or option: $1 (see dots --help)"; return 2 ;;
  esac
  if (( $# > 1 )); then
    print -u2 -r -- "dots: unexpected argument: $2"
    return 2
  fi
  # --all: the local run first (it re-sources ~/.zshrc, so what fans out is the
  # just-updated dots-sync), then `dots` on every host. dots-sync never runs `dots
  # --all` remotely, so the fan-out cannot echo back.
  if [[ "$1" == --all || "$1" == -a ]]; then
    dots || return 1
    # if/else, not `A && B || C`: C runs when B merely returns nonzero too (SC2015),
    # which would report a missing dots-sync every time a host was unreachable.
    if command -v dots-sync >/dev/null 2>&1; then
      dots-sync --hosts-only
    else
      print -r -- "dots --all: no dots-sync on PATH — \`dots --relink\` links it" >&2
    fi
    return
  fi

  local g c y r0=
  if [[ -t 1 ]]; then g=$'\e[32m'; c=$'\e[36m'; y=$'\e[2m'; r0=$'\e[0m'; fi

  local live primary
  if ! _dots_resolve; then
    print -r -- "dots: no dotfiles checkout found (looked for the tree ~/.zshrc links to, then \$DEV_REPOS[dotfiles])." >&2
    return 1
  fi
  local relinked   # hoisted: a 2nd assignmentless `local` in another branch prints it

  if [[ "$1" == --relink ]]; then
    # Explicit, fetch-free reconcile of the live surface — the escape hatch that
    # replaces hand-typing a path to install.sh. Uses $live, not $primary, so it is
    # correct after `dots --dev` too (relink whatever is live right now).
    local out target="${live:-$primary}" relink_failed=0
    if out=$(_dots_relink "$target"); then
      if [[ -n "$out" ]]; then
        print -r -- "${g}✓${r0} ${y}relinked from ${c}${target/#$HOME/~}${r0}"
        print -r -- "$out"
      else
        print -r -- "${g}✓${r0} ${y}links already up to date (${c}${target/#$HOME/~}${r0}${y})${r0}"
      fi
    else
      print -u2 -r -- "dots: relink failed: $out"
      relink_failed=1
    fi
    source ~/.zshrc
    return $relink_failed
  fi

  if [[ "$1" == --dev || "$1" == -d ]]; then
    if [[ "$2" == --force || "$2" == -f ]]; then
      # --force used to mean "yes, I really am working in the parked dev clone" — a
      # state that cannot exist now that there is one tree and it is the live one.
      # Silently accepting it would read as success, so say what changed.
      print -r -- "dots --dev: --force is gone — there is only one canonical checkout now, and it IS the live surface." >&2
      print -r -- "  work in a session worktree (\`t open dot\`); \`dots --relink\` re-links the live tree without fetching." >&2
      return 1
    fi
    # Link the live symlinks from the dotfiles checkout $PWD is in — the cwd IS the
    # choice, so `t cd dot 1` + `dots --dev` tests that session's edits live. No
    # fallback: anywhere else it ERRORS instead of guessing (the old implicit
    # dev-clone fallback silently linked a stale tree — that is how `t resume` once
    # vanished with a ✓).
    local src pwdcommon primarycommon
    src=$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null)
    if [[ -z $src ]]; then
      print -r -- "dots --dev: not inside a git checkout — cd into a dotfiles session worktree first (\`t cd dot <slot>\`)." >&2
      return 1
    fi
    pwdcommon=$(git -C "$src" rev-parse --git-common-dir 2>/dev/null)
    [[ $pwdcommon == /* ]] || pwdcommon="$src/$pwdcommon"
    primarycommon="$primary/.git"
    if [[ ${pwdcommon:A} != ${primarycommon:A} ]]; then
      print -r -- "dots --dev: $src is not a dotfiles checkout — cd into a dotfiles session worktree first (\`t cd dot <slot>\`)." >&2
      return 1
    fi
    if [[ ! -x $src/install.sh ]]; then
      print -r -- "dots --dev: $src has no runnable install.sh — malformed/ancient checkout, nothing to install." >&2
      return 1
    fi
    # The one refusal that matters: $primary is parked on main and IS the live
    # surface, so "make it live" is a no-op at best and, if it has drifted off main,
    # actively wrong. Under worktree-per-session no in-progress work lives here —
    # and ~/code/dotfiles is the cd-shortcut landing spot, so standing here by habit
    # is exactly how the mistake happens. Point at the real work instead.
    if [[ ${src:A} == ${primary:A} ]]; then
      print -r -- "dots --dev: $src is the ONE canonical checkout — it is parked on main and already IS the live surface (plain \`dots\` manages it)." >&2
      local -a _wts; _wts=("${DEV_WORKTREE_ROOT:-$HOME/code/.worktrees}/${primary:t}"/*(N/))
      local _w
      if (( ${#_wts} )); then
        print -r -- "  session worktrees with real work:" >&2
        for _w in "${(@)_wts}"; do
          print -r -- "    cd $_w && dots --dev    [$(git -C "$_w" branch --show-current 2>/dev/null || echo '?')]" >&2
        done
      else
        print -r -- "  no session worktrees yet — \`t open ${primary:t}\` starts one (then cd its worktree + dots --dev)" >&2
      fi
      return 1
    fi
    local branch=$(git -C "$src" symbolic-ref --short -q HEAD)
    local out dev_failed=0
    if out=$(DOTFILES_NO_BREW=1 DOTFILES_LINK_DEV=1 "$src/install.sh" 2>&1); then
      print -r -- "${g}✓${r0} ${y}live = worktree ${src:t2} (${c}${branch:-detached}${r0}${y}) — in-progress edits are live, reloaded${r0}"
    else
      dev_failed=1
      print -r -- "${y}dots --dev — install.sh failed on ${c}${branch:-detached}${r0}${y}:${r0}"
      print -r -- "$out"
    fi
    source ~/.zshrc
    return $dev_failed
  fi

  # Default: fast-forward the canonical checkout (the live surface) to origin/main.
  local before=""
  [[ -n $live ]] && before=$(git -C "$live" rev-parse --short HEAD 2>/dev/null)

  if ! git -C "$primary" fetch -q origin main 2>/dev/null; then
    print -r -- "${y}dots — fetch failed (offline?), reloaded only${r0}"
    # relinking needs no network, and "already pulled but never relinked" is exactly
    # the state this fixes — so do it even when the fetch failed.
    local offline_failed=0
    if relinked=$(_dots_relink "${live:-$primary}"); then
      if [[ -n "$relinked" ]]; then
        print -r -- "${g}✓${r0} ${y}relinked newly managed file(s):${r0}"
        print -r -- "$relinked"
      fi
    else
      print -u2 -r -- "dots: relink failed: $relinked"
      offline_failed=1
    fi
    source ~/.zshrc
    return $offline_failed
  fi

  _dots_t_preflight "$primary" || return 1

  # Run install.sh when the layout needs repairing. Three distinct triggers:
  #   live != primary   — a legacy two-tree machine, or a flip back from --dev
  #   primary off main  — SELF-HEAL: something switched the live tree's branch;
  #                       without this the machine stays silently wrong forever
  #   legacy present    — a half-finished migration, which the first two miss once
  #                       the links have already flipped
  # Always run "$live/install.sh", NEVER "$primary/install.sh": $live is by
  # construction the tree whose .zshrc defined the dots you are running, so it is the
  # freshest copy. On a legacy machine $primary is the old parked dev clone, whose
  # install.sh may predate this change entirely — running it would rebuild the very
  # worktree we are removing, forever. install.sh is location-independent
  # (BASH_SOURCE) and resolves $PRIMARY itself, so running it from anywhere is safe.
  local pbr=$(git -C "$primary" symbolic-ref --short -q HEAD)
  if [[ ${live:A} != ${primary:A} || $pbr != main ]] || _dots_legacy_present "$primary"; then
    local out
    if ! out=$(DOTFILES_NO_BREW=1 "${live:-$primary}/install.sh" 2>&1); then
      print -r -- "${y}dots — layout setup failed:${r0}"
      print -r -- "$out"
      source ~/.zshrc
      return 1
    fi
    [[ -n "$out" ]] && print -r -- "$out"
    _dots_resolve   # the links just moved; re-read where live points
  fi

  if ! git -C "$primary" diff --quiet HEAD 2>/dev/null; then
    # A dirty tree blocks the fast-forward, so flag it instead of silently doing
    # nothing. This tree IS the live surface now, so an edit here changed $HOME.
    print -r -- "${y}dots — ${c}${primary/#$HOME/~}${r0}${y} has local edits — it is the LIVE surface now, not a dev tree.${r0}"
    print -r -- "${y}      work in a session worktree (\`t open dot\`), then merge + dots. Reloaded only.${r0}"
  elif git -C "$primary" merge --ff-only origin/main >/dev/null 2>&1; then
    local after=$(git -C "$primary" rev-parse --short HEAD)
    if [[ "$before" == "$after" ]]; then
      print -r -- "${g}✓${r0} ${y}live = ${c}main${r0} ${y}at ${after} — already latest, reloaded${r0}"
    else
      print -r -- "${g}✓${r0} ${y}updated live ${c}main${r0} ${y}${before} → ${after}, reloaded${r0}"
    fi
  else
    print -r -- "${y}dots — ${c}${primary/#$HOME/~}${r0}${y} can't fast-forward origin/main; reloaded only${r0}"
  fi

  # Reconcile the managed links with what we just fast-forwarded to. Must come AFTER
  # the ff (the new install.sh and its new link set only exist on disk once the merge
  # above ran) and BEFORE _dots_tmux_apply, so a newly linked ~/.tmux.conf is what
  # gets sourced into the running server.
  local update_failed=0
  if relinked=$(_dots_relink "$primary"); then
    if [[ -n "$relinked" ]]; then
      print -r -- "${g}✓${r0} ${y}relinked newly managed file(s):${r0}"
      print -r -- "$relinked"
    fi
  else
    print -u2 -r -- "dots: relink failed: $relinked"
    update_failed=1
  fi
  _dots_tmux_apply
  # Standalone t has its own updater. Only the bundled-to-standalone migration
  # above belongs to dots; routine dotfiles updates must not invoke t or Homebrew.
  source ~/.zshrc
  return $update_failed
}

# _dots_reload_if_moved — precmd: when the live checkout's `main` moved under this
# shell (a `dots` in another terminal, a `dots --all` fan-out from another host),
# re-source ~/.zshrc before the next prompt, so an open shell never keeps
# running the functions of an older release. Costs one fork-free file read per
# prompt: the loose ref git rewrites on every fast-forward. Only armed when the live
# tree is the canonical checkout (a DIRECTORY .git) — after `dots --dev` the links
# point into a session worktree and that shell is testing edits, not tracking main.
# DOTS_NO_AUTORELOAD=1 in ~/.zshrc.local turns it off.
_DOTS_REF_FILE=${${:-$HOME/.zshrc}:A:h}/.git/refs/heads/main
[[ -L $HOME/.zshrc && -d ${_DOTS_REF_FILE%/refs/heads/main} ]] || _DOTS_REF_FILE=
_DOTS_LOADED_REF=
[[ -n $_DOTS_REF_FILE && -r $_DOTS_REF_FILE ]] && _DOTS_LOADED_REF=$(<$_DOTS_REF_FILE)
_dots_reload_if_moved() {
  if [[ -n ${_DOTS_PACKAGE_ROOT:-} ]]; then
    [[ -z ${DOTS_NO_AUTORELOAD:-} && -r $_DOTS_PACKAGE_ROOT/.dotfiles-version ]] || return 0
    if [[ $(<"$_DOTS_PACKAGE_ROOT/.dotfiles-version") != $_DOTS_PACKAGE_VERSION ]]; then
      source ~/.zshrc
    fi
    return 0
  fi
  [[ -z ${DOTS_NO_AUTORELOAD:-} && -n $_DOTS_REF_FILE && -r $_DOTS_REF_FILE ]] || return 0
  local now=$(<$_DOTS_REF_FILE)
  [[ $now == $_DOTS_LOADED_REF ]] && return 0
  print -r -- "dots — live main moved ${_DOTS_LOADED_REF:0:7} → ${now:0:7}; reloaded ~/.zshrc" >&2
  source ~/.zshrc
}
autoload -Uz add-zsh-hook
add-zsh-hook precmd _dots_reload_if_moved

# Personal t integration. The validated standalone checkout owns the shell helpers
# and generated shortcuts; this file keeps local policy and the unrelated dotfiles
# commands. No t checkout is required to use dots or nosleep.
typeset -gA DEV_REPOS DEV_BRANCHES REMOTE_HOSTS DEV_WORKTREE DEV_AGENT DEV_MODEL DEV_EFFORT DEV_FAST
_dots_t_environment "$_DOTS_SOURCE_ROOT"
export T_DOTFILES_LIVE_TREE="$_DOTS_SOURCE_ROOT"
_DOTS_T_ROOT=$(_dots_t_find 2>/dev/null)
if [[ -n $_DOTS_T_ROOT && -r $_DOTS_T_ROOT/t.plugin.zsh ]]; then
  source "$_DOTS_T_ROOT/t.plugin.zsh"
else
  DEV_REPOS=() DEV_BRANCHES=() REMOTE_HOSTS=() DEV_WORKTREE=() DEV_AGENT=() DEV_MODEL=() DEV_EFFORT=() DEV_FAST=()
  [[ -f $T_LOCAL_RC ]] && source "$T_LOCAL_RC"
  t() {
    print -u2 -r -- "t: standalone shell integration is missing. Run install.sh to install a release, then open a new shell."
    return 127
  }
fi

# Personal script checkout. Local config can override the directory or disable it
# with an empty value. Re-sourcing through dots must not duplicate the PATH entry.
DOTFILES_SCRIPTS_DIR="${DOTFILES_SCRIPTS_DIR-$HOME/code/personal-scripts}"
if [[ -d "$DOTFILES_SCRIPTS_DIR" ]] && (( ${path[(Ie)$DOTFILES_SCRIPTS_DIR]} == 0 )); then
  path=("$DOTFILES_SCRIPTS_DIR" "${path[@]}")
fi

# Remove the retired transcript sync hook and historical function when a running
# shell reloads its configuration. Keep existing transcript and cache data.
precmd_functions=(${precmd_functions:#_csync_periodic})
unfunction _csync_periodic csync 2>/dev/null

# Shared support for the workspace Git updater below.
mkdir -p "$HOME/.cache" "$HOME/Library/Logs" 2>/dev/null
zmodload zsh/datetime 2>/dev/null                     # $EPOCHSECONDS + strftime, no `date` fork
# Only the zstat builtin (-F), NOT the module's `stat` — that would shadow the
# system stat the rest of this file (and Linux hosts) call. zstat is the portable
# mtime source: BSD `stat -f %m` and GNU `stat -c %Y` disagree, zstat is neither.
zmodload -F zsh/stat b:zstat 2>/dev/null
autoload -Uz add-zsh-hook
# openclaw-workspace auto-pull — keep this machine's clone of the agent workspace
# (DEV_REPOS[cw]) tracking the shared GitHub remote. The openclaw gateway auto-commits
# + pushes the live workspace to that remote; every clone (laptop, mini) just rides
# along by fast-forwarding. Run from the prompt at most once per
# interval, IF the repo exists AND its tracked tree is clean, `git pull --ff-only` in a
# detached background job. Pull-only + clean-only + ff-only is the safety: a local edit
# is never clobbered or merge-committed (a dirty/ahead clone simply skips — commit and
# push it yourself, or let the gateway reconcile). Never blocks the prompt. See the
# [[openclaw-workspace-sync]] memory.
_clawsync_periodic() {
  local dir=${DEV_REPOS[cw]:-} interval=600 stamp="$HOME/.cache/clawsync-last-run" now=$EPOCHSECONDS last=0
  [[ -n $dir && -d $dir/.git ]] || return                     # no clone here → no-op
  [[ -r "$stamp" ]] && last=$(<"$stamp")
  (( now - last >= interval )) || return
  print -r -- "$now" >| "$stamp"                              # stamp BEFORE the run (overlap guard)
  # &! (background AND disown), not plain & — a plain & leaves the job in THIS
  # interactive shell's job table, so monitor mode prints `[1] PID` at launch and
  # `[1] + done  ( git -C … )` on the next prompt. &! keeps it out of the table
  # entirely.
  ( git -C "$dir" diff --quiet && git -C "$dir" diff --cached --quiet \
      && git -C "$dir" pull --ff-only --quiet ) >>"$HOME/Library/Logs/clawsync.log" 2>&1 &!
}
add-zsh-hook precmd _clawsync_periodic

# help — show this command list, grouped by purpose
# Each command's name + description are parsed live from the leading
# `# name … — description` comment above each ~/.zshrc function and the header
# line of each ~/bin script, so descriptions stay current as you add commands.
# Grouping is the `groups` list below; anything not placed there shows under
# "Other" so it's never hidden — except the few internal/automatic commands in
# the `_hide` list (a transparent wrapper, a hook, a guard), which are dropped
# entirely since you never invoke them by hand.
# (zsh's own help is `run-help` / ESC-h; this doesn't touch it.)
help() {
  emulate -L zsh
  [[ "$1" == -h || "$1" == --help ]] && { _help_for help; return 0; }

  # Build:  name -> "signature — description"  from functions and bin scripts.
  # (Functions whose name starts with `_` — completion helpers — are skipped.)
  typeset -A info
  local line sig name f n g title
  for line in ${(f)"$(awk '
      /^#/ { if (!c) { h=$0; sub(/^#[ ]?/, "", h); c=1 } next }
      /^[A-Za-z_][A-Za-z0-9_-]*\(\)/ {
        n=$0; sub(/\(\).*/, "", n)
        if (n !~ /^_/) print (c ? h : n)
        c=0; next
      }
      { c=0 }
    ' "${_DOTS_PACKAGE_ROOT:-$_DOTS_SOURCE_ROOT}/.zshrc")"}; do
    sig=${line%% — *}; name=${sig%% *}; info[$name]=$line
  done
  for f in ~/bin/*(N); do
    [[ -x $f ]] || continue
    line="$(sed -n '2s/^# *//p' "$f")"
    sig=${line%% — *}; name=${sig%% *}; info[$name]=$line
  done

  # The bare `<repo>` cd shortcuts are aliases generated from DEV_REPOS, so the
  # function/script parser above never sees them — synthesise
  # one entry per repo (:t = basename of the target dir) so they show in help.
  local _r
  for _r in ${(k)DEV_REPOS}; do
    info[$_r]="$_r — cd straight to ${DEV_REPOS[$_r]:t}"
  done

  # Same for the per-host shortcuts generated from REMOTE_HOSTS — also not real
  # text in this file, so synthesise an entry per alias.
  local _hk
  for _hk in ${(k)REMOTE_HOSTS}; do
    info[$_hk]="$_hk — run a command on ${REMOTE_HOSTS[$_hk]} (≡ t on $_hk)"
  done

  # Internal/automatic commands — you never type these: `claude` is a transparent
  # wrapper around the real CLI, `claude-stamp-tmux` is a SessionStart hook, and
  # `pii-scan` runs from the git pre-commit hook + CI. Drop them from the list.
  local _hide
  for _hide in claude claude-stamp-tmux pii-scan; do unset "info[$_hide]"; done

  # Grouping by purpose.  "Title:cmd cmd …" — drop a command's name into a group
  # to file it; anything uncategorized falls through to "Other" at the end. The
  # Claude-session family is now the single `t` command (its verbs show in `t -h`).
  local -a groups=(
    "Dotfiles & shell:dots help"
    "Repo shortcuts (cd):${(kj: :)DEV_REPOS}"
    "Remote machines:${(kj: :)REMOTE_HOSTS}"
    "Git & PRs:prview"
    "Claude:t"
    "Agents (claude · codex · cursor):cursor-beam"
    "Keep the Mac awake:nosleep sleep-manager"
  )

  # Per-section "how to add" hints (keyed by group title) — printed dim under the
  # rows for the generated sections, since those come from ~/.zshrc.local arrays.
  local -A hints=(
    "Repo shortcuts (cd)" "+ add a repo: t setup (or DEV_REPOS[key]=~/code/repo in ~/.zshrc.local)"
    "Remote machines"     "+ add a host: t setup (or REMOTE_HOSTS[key]=user@host in ~/.zshrc.local)"
    "Agents (claude · codex · cursor)" "+ install / log in an agent CLI: t install · per-verb support: t install --status · the shared allow list + default mode: t permissions · trust a folder: t trust"
  )

  # Palette — bold, UPPERCASE section headers (man-page / `gh` convention; bold is
  # the real separator, colour just a hint). Suppressed when stdout isn't a
  # terminal, so piped/grep'd output stays plain.
  local H C M D R
  if [[ -t 1 ]]; then
    H=$'\e[1;38;5;214m'   # bold amber  — section headers (the accent)
    C=$'\e[38;5;180m'     # warm tan    — command names
    M=$'\e[38;5;245m'     # muted grey  — descriptions
    D=$'\e[2;38;5;245m'   # dim grey    — intro line
    R=$'\e[0m'
  fi

  _help_group() {                       # $1 = title, $2… = command names
    local title=$1; shift
    local n w=0; local -a have
    for n in "$@"; do
      [[ -n ${info[$n]} ]] || continue
      have+=$n; (( ${#${info[$n]%% — *}} > w )) && w=${#${info[$n]%% — *}}
    done
    (( ${#have} )) || return
    print -r -- "${H}${(U)title}${R}"             # UPPERCASE, bold header
    for n in $have; do
      printf '  %s%-*s%s  %s%s%s\n' "$C" $w "${info[$n]%% — *}" "$R" "$M" "${info[$n]#* — }" "$R"
    done
    [[ -n ${hints[$title]} ]] && print -r -- "  ${D}${hints[$title]}${R}"
    print
  }

  print -r -- "${D}Custom commands — run 'help' (or 'h') to list; '<cmd> -h' for details.${R}"; print
  typeset -A shown
  local -a names
  for g in $groups; do
    title=${g%%:*}; names=(${(s: :)${g#*:}})
    _help_group "$title" "${names[@]}"
    for n in $names; do shown[$n]=1; done
  done
  local -a leftover
  for name in ${(k)info}; do [[ -z ${shown[$name]} ]] && leftover+=$name; done
  (( ${#leftover} )) && _help_group "Other" ${(o)leftover}

  unfunction _help_group
}
alias h=help   # `h` is a shorthand for `help`

# Completion for the dotfiles-owned sleep manager. t owns its own completion.
_sleepmgr_cmd() { _arguments '1:command:(status disable enable help)' }
(( $+functions[compdef] )) && compdef _sleepmgr_cmd sleep-manager
