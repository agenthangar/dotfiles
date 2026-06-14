export PATH="$HOME/bin:$HOME/.local/bin:$PATH"

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
#   Word:                      a capitalized word + colon on its own line → header
#   <2sp><term>  <2+sp><desc>  two-column row → term cyan, desc dim, auto-aligned
#   anything else              printed verbatim (paragraphs, blank spacer lines)
# Color is emitted only when stdout is a TTY (so `cmd -h | cat` stays plain).
_help_for() {
  local name="${1:?_help_for: need a function name}"
  awk -v fn="$name" '
    /^#/                  { blk = blk $0 ORS; next }
    $0 ~ "^" fn "\\(\\)"  { printf "%s", blk; found = 1; exit }
                          { blk = "" }
    END                   { exit !found }
  ' ~/.zshrc | sed 's/^# \{0,1\}//' | _help_style
}

# _help_style — the gh-style RENDERER, split out from _help_for so the dynamic
# no-arg/error paths (which list the actual ${(k)DEV_REPOS}, not a static comment)
# render identically to `-h`. Reads plain text on stdin and styles it by the same
# conventions documented above _help_for: line 1 "<name> — <tagline>" bolds the
# name; a "Usage:" line bolds the label; a bare "Word:" line is a section header;
# 2-space-indented "term  <2+sp>desc" rows become cyan/dim two-column rows, their
# descriptions auto-aligned to the widest term. Color only when stdout is a TTY —
# and because this is the LAST stage of every help pipe, `-t 1` here is the real
# terminal test (so `cmd -h | cat` still comes out plain).
_help_style() {
  local b='' d='' c='' r=''
  [[ -t 1 ]] && { b=$'\e[1m' d=$'\e[2m' c=$'\e[36m' r=$'\e[0m'; }
  awk -v b="$b" -v d="$d" -v c="$c" -v r="$r" '
    # Pass 1: buffer every line, detecting two-column rows and the widest term so
    # descriptions can align to a common column regardless of input spacing. A line
    # indented 3+ spaces that is NOT a 2-space kv row is a description CONTINUATION
    # (a wrapped second line of a row description) — remembered de-indented so pass 2
    # can hang it under the aligned description column, not its hand-typed indent.
    {
      raw[NR] = $0
      kvline = 0; contline = 0
      if ($0 ~ /^  [^ ]/) {
        body = substr($0, 3)
        if (match(body, / {2,}/)) {
          iskv[NR]  = 1; kvline = 1
          kterm[NR] = substr(body, 1, RSTART - 1)
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
        } else if (line ~ /^[A-Z][A-Za-z]*:$/) {
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
# reuses it, so a key added once stays loaded until reboot. Passphrase + key
# loading are handled lazily by ~/.ssh/config (AddKeysToAgent + UseKeychain).
export SSH_AUTH_SOCK="$HOME/.ssh/agent.sock"
ssh-add -l >/dev/null 2>&1
if [ $? -eq 2 ]; then            # 2 = no agent reachable (1 = up but no keys)
  rm -f "$SSH_AUTH_SOCK"         # clear any stale socket from a dead agent
  ssh-agent -a "$SSH_AUTH_SOCK" >/dev/null 2>&1
fi

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

# nosleep — keep the Mac awake until Ctrl-C (interactive)
#
# Usage: nosleep
#
# Blocks sleep via `pmset disablesleep 1` + a foreground caffeinate, restoring on
# exit/Ctrl-C. For a backgrounded, persistent block use `sleep-manager` instead.
nosleep() { [[ "$1" == -h || "$1" == --help ]] && { _help_for nosleep; return 0; }; trap 'sudo pmset -a disablesleep 0' EXIT INT; sudo pmset -a disablesleep 1 && caffeinate -dimsu; }

# dots — sync your dotfiles to origin/main HEAD and reload zsh
#
# Usage: dots [--dev]
#
# Default: fetches origin/main, switches to the main branch, fast-forwards it to
# origin/main HEAD, and re-sources ~/.zshrc — so your live dotfiles become exactly
# what is published on main, with nothing done locally (no merge, no commit). main
# is protected (you never commit to it directly), so the fast-forward always
# applies. Leaves you on main; develop on the dev branch via `t open dotfiles`.
# Safe: stops and only reloads if the working tree has uncommitted edits, so it
# never discards work — commit or stash first.
#
# --dev (-d): install from whatever branch is checked out NOW instead — re-runs
# install.sh (relinks any new/renamed files) and reloads, without fetching or
# switching branches. Use it to apply in-progress dev work (e.g. a new bin script
# that needs a fresh symlink) before it lands on main. Skips brew bundle.
dots() {
  [[ "$1" == -h || "$1" == --help ]] && { _help_for dots; return 0; }
  # Resolve the repo from the ~/.zshrc symlink (:A follows it to the real path,
  # :h takes its dir) so this works wherever the repo was cloned — install.sh is
  # location-independent, so don't hardcode ~/code/dotfiles.
  local dotdir="${${:-$HOME/.zshrc}:A:h}"
  cd "$dotdir" || return

  local g c y b r0=
  if [[ -t 1 ]]; then g=$'\e[32m'; c=$'\e[36m'; y=$'\e[2m'; b=$'\e[1m'; r0=$'\e[0m'; fi

  if [[ "$1" == --dev || "$1" == -d ]]; then
    # Install from the current branch: relink (catches new files), no git, no brew.
    local branch=$(git symbolic-ref --short -q HEAD)
    local out
    if out=$(DOTFILES_NO_BREW=1 ./install.sh 2>&1); then
      print -r -- "${g}✓${r0} ${y}installed from ${c}${branch}${r0} ${y}(current branch) — reloaded${r0}"
    else
      print -r -- "${y}dots --dev — install.sh failed on ${c}${branch}${r0}${y}:${r0}"
      print -r -- "$out"
    fi
    source ~/.zshrc
    cd - > /dev/null
    return
  fi

  local prev_branch=$(git symbolic-ref --short -q HEAD)
  if ! git fetch -q origin main 2>/dev/null; then
    print -r -- "${y}dots — fetch failed (offline?), reloaded only${r0}"
  elif ! git diff --quiet HEAD 2>/dev/null; then
    print -r -- "${y}dots — uncommitted edits on ${c}$(git symbolic-ref --short -q HEAD)${r0}${y}; commit or stash first, reloaded only${r0}"
  elif ! git checkout -q main 2>/dev/null; then
    print -r -- "${y}dots — could not switch to main, reloaded only${r0}"
  else
    local before=$(git rev-parse --short main)
    if git merge --ff-only origin/main >/dev/null 2>&1; then
      local after=$(git rev-parse --short main)
      if [[ $before == $after ]]; then
        print -r -- "${g}✓${r0} ${y}on ${c}main${r0} ${y}at ${after} — already latest, reloaded${r0}"
      else
        print -r -- "${g}✓${r0} ${y}updated ${c}main${r0} ${y}${before} → ${after}, reloaded${r0}"
      fi
    else
      # Fast-forward failed: don't strand the user on a stale main — return to
      # the branch they were on before the checkout above.
      if [[ -n $prev_branch && $prev_branch != main ]]; then
        git checkout -q "$prev_branch" 2>/dev/null
      fi
      print -r -- "${y}dots — main diverged from origin/main, can't fast-forward; reloaded only${r0}"
    fi
  fi

  source ~/.zshrc
  cd - > /dev/null
}

# DEV_REPOS — single source of truth for the repos `dev` and the cd shortcuts
# below both understand. Add a repo here and it gains a `dev <key>` session AND a
# bare `<key>` cd shortcut, with no second list to keep in sync. The real entries
# are machine-specific, so they live in ~/.zshrc.local (not committed); this file
# just declares the array and sources that override. See .zshrc.local.example.
#   DEV_REPOS[api]="$HOME/code/my-api"
typeset -gA DEV_REPOS DEV_BRANCHES REMOTE_HOSTS
[[ -f "$HOME/.zshrc.local" ]] && source "$HOME/.zshrc.local"

# DEV_BRANCH — the global default branch `dev`/`_dev_new_session` check out (and
# create) when starting a fresh Claude session. Override in ~/.zshrc.local;
# defaults here so the committed config works standalone. `:=` lets the local
# file win if it set it.
: ${DEV_BRANCH:=dev/claude-1}

# DEV_BRANCHES — per-repo branch overrides, keyed by the same alias as DEV_REPOS.
# A repo with no entry falls back to $DEV_BRANCH; e.g. a repo whose workflow
# commits straight to main wants DEV_BRANCHES[myrepo]=main. Set in
# ~/.zshrc.local (declared above so the local file can just add keys).
# _dev_branch_for <repo> — branch `dev` uses for <repo>: its DEV_BRANCHES
# override if set, else the global $DEV_BRANCH.
_dev_branch_for() { print -r -- "${DEV_BRANCHES[$1]:-$DEV_BRANCH}" }

# _dev_repo_prepare <branch> — sync a NEW session's checkout to <branch> without
# TRAMPLING sibling sessions. Every dev-<repo>-* slot cd's into the SAME working tree,
# so the old `git stash; checkout; pull` dance was destructive: `git stash` silently
# pocketed a sibling's uncommitted work, and `git pull` (a merge by default) could move
# the branch out from under a session mid-conversation. Policy now:
#   • tree DIRTY (uncommitted changes — a sibling likely owns them) → do NOTHING, leave
#     the checkout exactly as it is. Never stash. The new session just starts here.
#   • tree CLEAN → fetch, switch to <branch> (creating it if missing), and pull
#     --ff-only (never a merge/reset; matches the global pull.ff=only). A fast-forward
#     on a clean tree is non-destructive — no sibling has local edits to lose.
# Runs in the session's own shell (cwd = the repo, set by tmux -c / the -f `cd`).
_dev_repo_prepare() {
  local branch="$1"
  if [[ -n "$(git status --porcelain 2>/dev/null)" ]]; then
    echo "↷ branch sync skipped — this shared checkout has uncommitted changes (a sibling session may own them)."
    return 0
  fi
  git fetch -q origin 2>/dev/null
  git checkout -q "$branch" 2>/dev/null || git checkout -qb "$branch" 2>/dev/null
  git pull -q --ff-only origin "$branch" 2>/dev/null || true
}

# Generate a cd shortcut per repo: each key jumps straight to its dir.
for _repo in ${(k)DEV_REPOS}; do
  alias "$_repo"="cd ${DEV_REPOS[$_repo]}"
done
unset _repo

# REMOTE_HOSTS — single source of truth for machines `on` (and its generated
# per-host shortcuts) can reach. Key = short alias, value = ssh target. Real
# entries are machine-specific, so they live in ~/.zshrc.local (declared above so
# the local file can just add keys). Back-compat: an old MINI_HOST/TBEAM_HOST
# seeds a `mini` alias ONLY when the whole registry is empty (a pre-REMOTE_HOSTS
# config), so those configs keep working — but a machine that already populates
# REMOTE_HOSTS and sets TBEAM_HOST purely as a `t beam` send default never gets a
# phantom `mini` pointing at the beam target. (Gating on REMOTE_HOSTS[mini] alone
# seeded `mini` even on a configured host the moment TBEAM_HOST was set.)
(( ${#REMOTE_HOSTS} == 0 )) && [[ -n ${MINI_HOST:-${TBEAM_HOST:-}} ]] \
  && REMOTE_HOSTS[mini]="${MINI_HOST:-$TBEAM_HOST}"

# _t_sync_config — derive ~/.config/t/config.sh from the live DEV_* / REMOTE_HOSTS
# arrays so `bin/t` (a Python executable that can't read zsh assoc arrays) has a
# single source of truth. Emits plain `NAME[key]=value` / `NAME=value` data lines;
# bin/t parses them with a strict line regex and NEVER sources the file (and never
# sources ~/.zshrc.local, which can hold arbitrary shell — completions, conditionals).
# zsh itself keeps using the live arrays (cd/host shortcuts, completions) and does not
# read this cache. mtime-gated: rewritten only when ~/.zshrc.local or ~/.zshrc is newer
# than the cache, so it is ~2 stats on a normal prompt; `dots` re-sources .zshrc (whose
# repo mtime bumps on pull) and so refreshes it. Absent ~/.zshrc.local → an empty-array
# cache, mirroring zsh's own graceful "no repos configured" degradation.
_t_sync_config() {
  local cache="${XDG_CONFIG_HOME:-$HOME/.config}/t/config.sh"
  local src="$HOME/.zshrc.local"
  [[ -f $cache && $cache -nt $src && $cache -nt $HOME/.zshrc ]] && return
  mkdir -p "${cache:h}" 2>/dev/null || return
  local k
  {
    for k in ${(k)DEV_REPOS};    do print -r -- "DEV_REPOS[$k]=${(q)DEV_REPOS[$k]}"; done
    for k in ${(k)DEV_BRANCHES}; do print -r -- "DEV_BRANCHES[$k]=${(q)DEV_BRANCHES[$k]}"; done
    for k in ${(k)REMOTE_HOSTS}; do print -r -- "REMOTE_HOSTS[$k]=${(q)REMOTE_HOSTS[$k]}"; done
    print -r -- "DEV_BRANCH=${(q)DEV_BRANCH}"
  } >| "$cache"
}
_t_sync_config

# Generate a shorthand function per host: `mini …` ≡ `on mini …`. A function (not
# an alias) so it forwards "$@" and also works bare (`mini` → a shell on it). `on`
# is defined further down; function bodies bind late, so order doesn't matter.
for _host in ${(k)REMOTE_HOSTS}; do
  functions[$_host]="t on ${(q)_host} \"\$@\""
done
unset _host

# csync — two-way sync of Claude Code session history with iCloud Drive.
# Lives in bin/csync (install.sh symlinks it onto PATH; `help` lists it by
# scanning ~/bin). Run `csync` on any machine to converge.
#
# Periodic csync, the shell way. A launchd agent *can't* do this: iCloud Drive
# is TCC-protected and background agents are denied — granting /bin/bash Full
# Disk Access has no effect on recent macOS (the grant won't pin to a platform
# interpreter running an arbitrary script). The shell, though, runs in the
# Terminal's already-approved context, so we piggyback on the prompt: at most
# once every 15 min, fire csync detached in the background. A stamp file gates
# the interval (written *before* the run so overlapping shells don't double-fire).
mkdir -p "$HOME/.cache" "$HOME/Library/Logs" 2>/dev/null
zmodload zsh/datetime 2>/dev/null                     # $EPOCHSECONDS, no `date` fork
autoload -Uz add-zsh-hook
_csync_periodic() {
  local interval=900 stamp="$HOME/.cache/csync-last-run" now=$EPOCHSECONDS last=0
  [[ -d "$HOME/Library/Mobile Documents/com~apple~CloudDocs" ]] || return  # no iCloud here
  [[ -r "$stamp" ]] && last=$(<"$stamp")
  (( now - last >= interval )) || return
  print -r -- "$now" >| "$stamp"
  ( csync >>"$HOME/Library/Logs/csync.log" 2>&1 & )   # detached; never blocks the prompt
}
add-zsh-hook precmd _csync_periodic

# openclaw-workspace auto-pull — keep this machine's clone of the agent workspace
# (DEV_REPOS[cw]) tracking the shared GitHub remote. The openclaw gateway auto-commits
# + pushes the live workspace to that remote; every clone (laptop, mini) just rides
# along by fast-forwarding. Same prompt-piggyback trick as csync: at most once per
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
  # entirely (csync above stays silent the same way, via its `( … & )` subshell form).
  ( git -C "$dir" diff --quiet && git -C "$dir" diff --cached --quiet \
      && git -C "$dir" pull --ff-only --quiet ) >>"$HOME/Library/Logs/clawsync.log" 2>&1 &!
}
add-zsh-hook precmd _clawsync_periodic

# _tpaste_claude_ready <session> — return 0 once Claude is accepting input in
# the session's pane, else return 1. tpaste polls this after launching a fresh
# session so it knows when the path can be delivered.
_tpaste_claude_ready() {
  local session="$1"
  # The session bootstraps with `git …; claude`. While the git dance runs the
  # pane's foreground command is git/zsh; once Claude (a Node CLI) takes over it
  # becomes node. That process hand-off is a more robust readiness signal than
  # matching Claude's TUI text, which changes between versions. To gate on the
  # actual prompt instead, swap in a `tmux capture-pane -p` string match.
  local cmd
  cmd=$(tmux display-message -p -t "$session" '#{pane_current_command}' 2>/dev/null)
  [[ $cmd == node || $cmd == claude ]] && return 0
  return 1
}

# tpaste — paste an iCloud Drive screenshot/doc path into a dev tmux session
#
# Usage: tpaste [-n] [repo] [slot]
#
# Commands:
#   tpaste          fzf-pick a file, new session for the repo you're standing in
#   tpaste 3        pick a file, paste into this repo's slot 3 (repo from $PWD)
#   tpaste api      pick a file, start a new api session and queue it
#   tpaste api 3    pick a file, paste into dev-api-3 (creating it if needed)
#
# Options:
#   -n, --newest   skip the picker; take the newest file by mtime (fast path)
#   -p, --pick     accepted for back-compat (the picker is now the default)
#
# Covers images (png/jpg/jpeg/heic) and docs (pdf/txt/md/csv/docx) in the iCloud
# Drive root, newest first. The picker opens whenever there is a TTY + fzf; with
# neither (or -n) it falls back to the newest file. Press Enter to send it.
_t_paste() {
  [[ "$1" == -h || "$1" == --help ]] && { _help_for _t_paste; return 0; }
  # The picker is the default; -n/--newest forces the no-prompt fast path.
  # -p/--pick is kept as a no-op so old muscle memory/scripts still work.
  local newest=0 arg
  local -a _pos
  for arg in "$@"; do
    case "$arg" in
      -n|--newest) newest=1 ;;
      -p|--pick)   ;;
      *)           _pos+=("$arg") ;;
    esac
  done
  local repo="${_pos[1]}"
  local slot="${_pos[2]}"
  # Repo-aware: `t paste 4` ≡ `t paste <cwd-repo> 4`; bare `t paste` targets the
  # $PWD repo's next free slot (see _t_infer_repo).
  if [[ "$repo" == <-> && -z "$slot" ]]; then slot=$repo; repo=$(_t_infer_repo "$slot"); fi
  [[ -z "$repo" ]] && repo=$(_t_infer_repo)
  if [[ -z "$repo" ]]; then
    echo "Usage: tpaste [-n] [repo] [slot]   (no repo: the one \$PWD is in; repo: one of ${(k)DEV_REPOS:-(none configured — see ~/.zshrc.local)})"
    return 1
  fi

  local icloud="$HOME/Library/Mobile Documents/com~apple~CloudDocs"
  # verify iCloud Drive is accessible
  if [[ ! -d "$icloud" ]]; then
    echo "iCloud Drive not found at: $icloud"
    return 1
  fi
  # collect pasteable files in the iCloud Drive root: images + docs, one flat
  # set, newest-by-mtime wins. (Screenshots used to be a priority tier, but that
  # made a just-exported PDF unreachable whenever any screenshot existed — the
  # newest file IS the one you just saved, so the tiering bought nothing.)
  # (N) is the nullglob qualifier: unmatched globs expand to nothing instead of
  # raising zsh's "no matches found" error. Collect into an array first so an
  # empty result never makes `ls` fall back to listing the current directory.
  # extended_glob is needed for the parenthesized alternation in the filename
  # pattern; local_options restores the caller's setopts on return.
  setopt local_options extended_glob
  local src
  local -a files
  files=("$icloud"/*.(png|jpg|jpeg|heic|pdf|txt|md|csv|docx)(N))

  if (( ${#files} == 0 )); then
    echo "No images or docs found in iCloud Drive ($icloud)"
    return 1
  fi

  # Picker by default: open fzf whenever we have a TTY + fzf, unless -n forced
  # the fast path. No TTY/fzf (or -n) falls back to the newest file by mtime.
  if (( ! newest )) && [[ -t 0 && -t 1 ]] && command -v fzf >/dev/null 2>&1; then
    # ls -t sorts newest-first; show just the basename but return the full path
    src=$(ls -t "${files[@]}" | fzf --prompt='tpaste> ' --height=40% --reverse \
          --delimiter=/ --with-nth=-1) || { echo "Cancelled."; return 1; }
  else
    src=$(ls -t "${files[@]}" | head -1)
  fi

  echo "Using: $src"

  # find session
  # repo→path map: see the global DEV_REPOS (defined near the cd shortcuts)

  if [[ -z "${DEV_REPOS[$repo]}" ]]; then
    echo "Unknown repo: $repo. Use one of: ${(k)DEV_REPOS:-(none configured — see ~/.zshrc.local)}"
    return 1
  fi

  # no slot → pick the next FREE slot, so the default is always a fresh session
  if [[ -z "$slot" ]]; then
    local n=1
    while (( n <= 20 )); do
      if ! tmux has-session -t "dev-${repo}-${n}" 2>/dev/null; then
        slot=$n; break
      fi
      (( n++ ))
    done
    if [[ -z "$slot" ]]; then
      echo "All 20 slots for '$repo' are in use."
      return 1
    fi
  fi

  local session="dev-${repo}-${slot}"

  # existing session → Claude is already live, so paste straight in (no attach)
  if tmux has-session -t "$session" 2>/dev/null; then
    tmux send-keys -t "$session" "$src"
    echo "Pasted path into $session — press Enter in that session to send to Claude."
    return
  fi

  # new session → bootstrap Claude, wait for it to come up, queue the path, attach
  echo "Starting $session for the file…"
  _dev_new_session "$session" "${DEV_REPOS[$repo]}" "$(_dev_branch_for "$repo")"

  # wait (up to ~30s) for Claude's process to take over the pane; if the
  # readiness check never matches this degrades to a plain 30s wait
  local waited=0
  while (( waited < 30 )); do
    _tpaste_claude_ready "$session" && break
    sleep 1
    (( waited++ ))
  done
  # brief settle: the node process exists a beat before its input box mounts,
  # and keystrokes sent into that gap get dropped
  sleep 1

  tmux send-keys -t "$session" "$src"
  echo "Queued path in $session — attaching; press Enter to send to Claude."
  tmux attach-session -t "$session"
}

# _dev_session_has_claude <session> — true if a live `claude` process exists
# ANYWHERE in the session: the pane leader itself, a direct child, or deeper.
# We deliberately do NOT use pane_current_command: Claude sets its process title
# to its version string (e.g. "2.1.159"), so it never reads as "claude"/"node"
# there. `ps -o comm=` still reports "claude" (comm is the executable name, fixed
# at exec — argv/title rewrites don't touch it), which is the reliable signal.
#
# Why the whole subtree and not just direct children (this was an intermittent
# false-negative — a live, detached Claude rendering with a blank ✓):
#   • Claude can be the pane LEADER (e.g. `exec claude`): then pgrep -P pane_pid
#     returns only Claude's OWN children (python/caffeinate tool procs), none of
#     which read as claude — so the old direct-children-only scan missed it.
#   • Claude can be a GRANDCHILD (resumed / nested-shell launches), one level
#     below the direct child the old scan stopped at.
# The `node` hedge (Claude launched as `node`) is kept only for a DIRECT child of
# the pane shell — a node straight off a dev pane is claude-as-node; a node buried
# deeper is more likely an MCP server or dev tool, so we match only the
# unambiguous `claude` name there.
_dev_session_has_claude() {
  local s="$1" pane_pid kid comm
  for pane_pid in ${(f)"$(tmux list-panes -t "$s" -F '#{pane_pid}' 2>/dev/null)"}; do
    comm=$(ps -o comm= -p "$pane_pid" 2>/dev/null)
    [[ "${comm:t}" == claude ]] && return 0
    for kid in ${(f)"$(pgrep -P "$pane_pid" 2>/dev/null)"}; do
      comm=$(ps -o comm= -p "$kid" 2>/dev/null)
      case "${comm:t}" in (claude|node) return 0 ;; esac
      _dev_pid_tree_has_claude "$kid" && return 0
    done
  done
  return 1
}

# _dev_pid_tree_has_claude <pid> — true if <pid> or any descendant has comm
# `claude`. Recursive deep-search helper for _dev_session_has_claude.
_dev_pid_tree_has_claude() {
  local pid="$1" comm kid
  comm=$(ps -o comm= -p "$pid" 2>/dev/null)
  [[ "${comm:t}" == claude ]] && return 0
  for kid in ${(f)"$(pgrep -P "$pid" 2>/dev/null)"}; do
    _dev_pid_tree_has_claude "$kid" && return 0
  done
  return 1
}

# _dev_session_at_welcome <session> — true if the session's Claude is parked on
# its startup splash (launched but never given a prompt), i.e. a LIVE claude with
# NO loaded conversation. _dev_session_has_claude can't tell these apart: a fresh
# `claude` sitting at the welcome screen is just as live a process as one mid-
# conversation. The "Welcome back" banner is only rendered before the first user
# message and scrolls away after it, so its presence in the visible pane is the
# reliable "nothing's actually happening here" signal. Used by _dev_list to
# withhold the ✓ active-context mark from idle splash sessions (this was the
# "dev ls says cfp-2 is active but it isn't" false positive — an old-style plain
# `claude` with no transcript to map, so pane content is the only signal).
_dev_session_at_welcome() {
  tmux capture-pane -t "$1" -p 2>/dev/null | grep -q 'Welcome back'
}

# _dev_session_claude_pid <session> — print the pid of the live `claude` in the
# session (first match, same subtree scan as _dev_session_has_claude), or nothing.
# Used to map OLD sessions (no CLAUDE_RESUME_ID) to their transcript by start time.
_dev_session_claude_pid() {
  local s="$1" pane_pid kid comm found
  for pane_pid in ${(f)"$(tmux list-panes -t "$s" -F '#{pane_pid}' 2>/dev/null)"}; do
    comm=$(ps -o comm= -p "$pane_pid" 2>/dev/null)
    [[ "${comm:t}" == claude ]] && { print -r -- "$pane_pid"; return 0; }
    for kid in ${(f)"$(pgrep -P "$pane_pid" 2>/dev/null)"}; do
      found=$(_dev_pid_tree_claude_pid "$kid") && { print -r -- "$found"; return 0; }
    done
  done
  return 1
}
# _dev_pid_tree_claude_pid <pid> — print first pid in the subtree whose comm is
# `claude` (companion to _dev_pid_tree_has_claude, but returns the pid).
_dev_pid_tree_claude_pid() {
  local pid="$1" comm kid found
  comm=$(ps -o comm= -p "$pid" 2>/dev/null)
  [[ "${comm:t}" == claude ]] && { print -r -- "$pid"; return 0; }
  for kid in ${(f)"$(pgrep -P "$pid" 2>/dev/null)"}; do
    found=$(_dev_pid_tree_claude_pid "$kid") && { print -r -- "$found"; return 0; }
  done
  return 1
}

# _transcript_title <transcript.jsonl> — print the one-line title of a Claude
# transcript: customTitle (set by /rename) wins, else the generated aiTitle, else the
# first real user prompt; trimmed to 50 chars. Factored out so dev-slot summaries and
# foreground-session summaries share one parser.
_transcript_title() {
  python3 - "$1" <<'PY'
import json, sys
title = ctitle = msg = None
try:
    for line in open(sys.argv[1], errors='ignore'):
        if '"custom-title"' in line:                    # /rename — wins
            try:
                t = json.loads(line).get('customTitle')
                if t: ctitle = t                        # keep the most recent
            except ValueError: pass
        if '"ai-title"' in line:
            try:
                t = json.loads(line).get('aiTitle')
                if t: title = t                         # keep the most recent
            except ValueError: pass
        if msg is None and '"type":"user"' in line:     # first real prompt
            try:
                c = json.loads(line).get('message', {}).get('content')
                txt = c if isinstance(c, str) else (
                    ' '.join(x.get('text', '') for x in c if isinstance(x, dict))
                    if isinstance(c, list) else '')
                txt = txt.strip()
                if txt and not txt.startswith('<'): msg = txt
            except ValueError: pass
except OSError:
    pass
print(' '.join((ctitle or title or msg or '').split())[:50])
PY
}

# _dev_summary_for_pid <dir> <claude-pid> — title of the transcript a LIVE claude
# (in <dir>, pid <claude-pid>) is driving, when there's no recorded session id. The
# dir's newest transcript is WRONG when two live claudes share a repo (both resolve
# to it, identical summaries); each `claude` creates its transcript within seconds of
# launch, so disambiguate by birthtime closest to (and at/after) the process start.
# Falls back to newest-by-mtime if the pid/birthtimes can't be read. Shared by
# unstamped dev slots and foreground sessions (neither has a CLAUDE_RESUME_ID).
_dev_summary_for_pid() {
  setopt local_options null_glob bare_glob_qual
  local dir="$1" cpid="$2"
  local proj="$HOME/.claude/projects/${dir//\//-}" start
  local -a tx
  if [[ -n $cpid ]]; then
    start=$(ps -o lstart= -p "$cpid" 2>/dev/null)
    start=$(date -j -f '%a %b %d %T %Y' "${start## #}" +%s 2>/dev/null)
  fi
  if [[ -n $start ]]; then
    local f b best bestdiff diff
    for f in "$proj"/*.jsonl(N); do
      b=$(stat -f %B "$f" 2>/dev/null) || continue
      (( b < start - 2 )) && continue                  # born before this claude → not ours
      diff=$(( b - start ))
      if [[ -z $bestdiff ]] || (( diff < bestdiff )); then bestdiff=$diff; best=$f; fi
    done
    [[ -n $best ]] && tx=( "$best" )
  fi
  [[ -n ${tx[1]} ]] || tx=( "$proj"/*.jsonl(Nom[1]) )  # fallback: newest by mtime
  [[ -n ${tx[1]} ]] || return 0
  _transcript_title "${tx[1]}"
}

# _dev_session_summary <session> <dir> — one-line "what it's working on" for a dev
# session: the title of its Claude transcript, resolved by the id stashed on the
# session (CLAUDE_RESUME_ID, set by _dev_new_session / _dev_resume_session / the
# claude-stamp-tmux SessionStart hook). For OLD (pre-hook) sessions with no stashed
# id it defers to _dev_summary_for_pid (birthtime match). Prints nothing if none.
_dev_session_summary() {
  setopt local_options null_glob bare_glob_qual
  local session="$1" dir="$2" sid
  sid=$(tmux show-environment -t "$session" CLAUDE_RESUME_ID 2>/dev/null | cut -d= -f2)
  if [[ -n $sid ]]; then
    local -a tx=( "$HOME/.claude/projects"/*/"$sid".jsonl(N) )
    [[ -n ${tx[1]} ]] || return 0
    _transcript_title "${tx[1]}"
  else
    _dev_summary_for_pid "$dir" "$(_dev_session_claude_pid "$session")"
  fi
}

# _dev_fg_rows — emit FOREGROUND claude sessions (ones you ran directly in a terminal,
# NOT inside a dev-<repo>-<slot> tmux pane) in the same tab format as
# _dev_session_rows: "<sid>\t<cwd>\t<slot>\t<state>\t<context>\t<summary>". A
# foreground claude is a live process with comm `claude` that isn't the claude of any
# dev-* tmux pane. The session id + cwd come from the registry the claude-stamp-tmux
# SessionStart hook writes per live claude pid (~/.cache/claude-sessions/<pid>) — the
# reliable pid→id link, since macOS hides another process's env and csync scrambles
# transcript birthtimes; with the id we read the exact transcript title. A session
# started BEFORE the hook recorded it has no entry → cwd via `lsof`, summary
# "(foreground claude)". The slot label is "<repo>:fg" (repo from cwd, else basename).
# The claude THIS shell runs under is skipped so a session doesn't list itself; dead-pid
# registry entries are pruned here. Appended to _dev_session_rows (so `dev ls -r` shows
# them per host) and rendered by _dev_list.
_dev_fg_rows() {
  setopt local_options null_glob
  local reg="${XDG_CACHE_HOME:-$HOME/.cache}/claude-sessions"
  # claude pids already owned by a dev-* tmux slot — exclude (listed as slots already).
  local -A inslot; local s p
  for s in ${(f)"$(tmux list-sessions -F '#{session_name}' 2>/dev/null | grep '^dev-')"}; do
    p=$(_dev_session_claude_pid "$s") && [[ -n $p ]] && inslot[$p]=1
  done
  # the claude THIS shell is running under (walk up $$), so we don't list ourselves.
  local me up=$$
  while [[ -n $up && $up != 1 ]]; do
    [[ "$(ps -o comm= -p $up 2>/dev/null)" == claude ]] && { me=$up; break; }
    up=$(ps -o ppid= -p $up 2>/dev/null | tr -d ' ')
  done
  local -A live
  local pid cwd repo k label sid title summary context
  for pid in ${(f)"$(ps -Axo pid,comm 2>/dev/null | awk '{n=$2; sub(/.*\//,"",n)} n=="claude"{print $1}')"}; do
    live[$pid]=1
    [[ -n ${inslot[$pid]} || $pid == $me ]] && continue
    sid= cwd=
    [[ -r $reg/$pid ]] && IFS=$'\t' read -r sid cwd < "$reg/$pid"
    [[ -n $cwd ]] || cwd=$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)
    [[ -n $cwd ]] || continue
    repo=${cwd:t}
    for k in ${(k)DEV_REPOS}; do [[ ${DEV_REPOS[$k]} == $cwd ]] && { repo=$k; break; }; done
    # Label is "<repo>:<short-sid>" — the short Claude session id makes each
    # foreground row UNIQUE (two `dot` foreground claudes were both "dot:fg" before)
    # and is the handle `t open <id>` reattaches by. The colon marks an fg row (tmux
    # slots use "<repo>-<num>"). A pre-registry session (no id) falls back to ":fg".
    if [[ -n $sid ]]; then
      label="${repo}:${sid[1,8]}"
      local -a tx=( "$HOME/.claude/projects"/*/"$sid".jsonl(N) )
      title=$([[ -n ${tx[1]} ]] && _transcript_title "${tx[1]}")
      if [[ -n $title ]]; then context=active; summary=$title
      else context=idle; summary='(idle — no conversation)'; fi
    else
      label="${repo}:fg"; sid='-'; context=unknown; summary='(foreground claude)'
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$sid" "$cwd" "$label" attached "$context" "$summary"
  done
  # prune registry entries whose pid is no longer a live claude (sessions that ended)
  local f bpid
  for f in "$reg"/*(N.); do bpid=${f:t}; [[ -z ${live[$bpid]} ]] && rm -f "$f"; done
  # the prune loop exits 1 when its last entry is live ([[ ]] && short-circuit), and
  # as the last command here that became _dev_session_rows' status — making remote
  # scans look failed to _dev_rows_all, which drops their (good) rows.
  return 0
}

# _dev_pid_for_sid <sid> — print the live `claude` pid that owns session <sid>, via
# the claude-stamp-tmux registry (~/.cache/claude-sessions/<pid> = "<sid>\t<cwd>").
# The reverse of _dev_fg_rows' pid→sid read: _dev_adopt_fg needs the pid to STOP the
# foreground owner before resuming. Empty if no live pid maps to <sid> (already gone
# → resuming is safe anyway). A `-` sid (pre-registry) never matches, by design.
_dev_pid_for_sid() {
  local sid="$1" reg="${XDG_CACHE_HOME:-$HOME/.cache}/claude-sessions" f bsid bcwd
  [[ -n $sid && $sid != - ]] || return 1
  for f in "$reg"/*(N.); do
    IFS=$'\t' read -r bsid bcwd < "$f"
    [[ $bsid == $sid ]] && kill -0 ${f:t} 2>/dev/null && { print -r -- ${f:t}; return 0; }
  done
  return 1
}

# _dev_fg_handle <arg> — is <arg> shaped like a foreground-session handle? True for
# the displayed `repo:id` label (any `:`) and for a short session-id prefix: 4+ chars,
# all hex, at least one letter — pure digits stay tmux slot numbers, so `t open dot
# 1234` can never be stolen by a (rare) all-digit id prefix; type more chars or the
# repo:id form for those. The dispatcher `_t_dev` uses this to tell slots from ids.
_dev_fg_handle() {
  local h="$1"
  [[ $h == *:* ]] && return 0
  (( ${#h} >= 4 )) || return 1
  [[ $h != *[^0-9a-f]* && $h == *[a-f]* ]]
}

# _dev_adopt_fg [repo|id] — reattach a FOREGROUND (:fg) session by MOVING it here. A
# foreground claude is bound to its own terminal (no tmux to attach to), so the only
# way to "get into it" from elsewhere is to stop that owner and resume the conversation
# in THIS terminal — matching how it is running (the tmux-slot analog is `t open <repo>
# <slot>` / `t beam … --from`). One-live-owner: Claude takes no transcript lock, so two live
# resumers of one id diverge — hence the SIGTERM-then-wait before `claude -r`, mirroring
# t pop. Only :fg rows whose id the registry recorded (sid != `-`) are resumable; a
# pre-registry one must be reattached from its own terminal. All entry points are
# `t open` (the one get-into-a-session verb; the old `t fg` is gone):
#   • `t open <id>`      — the short session id shown in `t ls` (e.g. `t open a4aa5f6a`,
#                          or the displayed `dot:a4aa5f6a`) → that exact session.
#                          `t open <repo> <id>` works too — the id alone decides.
#   • `t open <repo> fg` — that repo's foreground sessions (one → use it, several →
#                          fzf-pick); the `fg` slot keyword mirrors the `:fg` label.
_dev_adopt_fg() {
  local arg="$1" rows
  # all foreground rows (the colon in the slot label marks fg; tmux slots use a dash)
  rows=$(_dev_fg_rows 2>/dev/null | awk -F'\t' '$3 ~ /:/')
  [[ -n $rows ]] || { echo "t open: no foreground claude running (see \`t ls\`)." >&2; return 1; }
  local resumable; resumable=$(print -r -- "$rows" | awk -F'\t' '$1!="-"')
  [[ -n $resumable ]] || { echo "t open: foreground claude(s) predate the session registry (no id recorded) — reattach from their own terminal." >&2; return 1; }
  if [[ -n $arg ]]; then
    local sel
    if [[ -n ${DEV_REPOS[$arg]} ]]; then            # an exact repo key → that repo's rows
      sel=$(print -r -- "$resumable" | awk -F'\t' -v r="$arg" 'index($3, r":")==1')
    else                                            # else a session id / short id (maybe "<repo>:<id>")
      local idpart="${arg##*:}"
      sel=$(print -r -- "$resumable" | awk -F'\t' -v p="$idpart" 'index($1,p)==1')
    fi
    [[ -n $sel ]] || { echo "t open: no foreground session matching '$arg' (see \`t ls\`)." >&2; return 1; }
    resumable=$sel
  else
    # Repo-aware: no-arg adoption prefers this repo's foreground claudes (row cwd in
    # field 2, under the $PWD repo dir); outside a repo — or no match — all of them.
    local scope; scope=$(_dev_cwd_repo_dir)
    if [[ -n $scope ]]; then
      local insc; insc=$(print -r -- "$resumable" | awk -F'\t' -v d="$scope" '$2==d || index($2, d"/")==1')
      [[ -n $insc ]] && resumable=$insc
    fi
  fi
  local sid cwd
  local -a lines=( ${(f)resumable} )
  if (( ${#lines} == 1 )); then
    IFS=$'\t' read -r sid cwd _ <<< "${lines[1]}"
  else
    [[ -t 0 && -t 1 ]] || { echo "t open: several foreground sessions — name one (\`t open <id>\`) or pick from a terminal:" >&2; print -r -- "$resumable" | awk -F'\t' '{printf "  %s  %s\n",$3,$6}' >&2; return 1; }
    local pick
    pick=$(print -r -- "$resumable" | awk -F'\t' '{printf "%s\t%s\t%s\n", $1, $3, $6}' \
             | fzf --with-nth=2.. --delimiter='\t' --prompt="t open > ") || return 1
    sid=${pick%%$'\t'*}
    cwd=$(print -r -- "$resumable" | awk -F'\t' -v s="$sid" '$1==s{print $2; exit}')
  fi
  [[ -n $sid ]] || return 1

  echo "Adopting foreground session → here (claude -r ${sid[1,8]}… in $cwd)"
  # Stop the foreground owner and wait for it to actually exit (≤5s) before resuming,
  # so only one live claude ever holds the id — same race tpop guards against.
  local pid; pid=$(_dev_pid_for_sid "$sid")
  if [[ -n $pid ]]; then
    kill -TERM "$pid" 2>/dev/null
    local n=0
    while kill -0 "$pid" 2>/dev/null && (( n++ < 100 )); do sleep 0.05; done
    kill -0 "$pid" 2>/dev/null && \
      echo "warning: foreground owner ($pid) didn't exit; resuming anyway — transcript may interleave." >&2
  fi
  cd "$cwd" && claude -r "$sid"
}

# _dev_list — print every dev-<repo>-<slot> tmux session, compact enough to read
# on a phone (Termius). One line per session: a two-glyph STATUS field + short
# name + what it's working on. The `dev-` prefix and the redundant
# "attached/detached" word are dropped (the glyph already says it) and the full
# repo path is dropped (it's in the name) so the line fits a narrow screen; the
# summary is truncated to $COLUMNS so it never wraps. The two glyphs are
# space-separated ("○ ✓", not "○✓") so the orthogonal states read as two columns,
# under a STATUS header. Glyphs:
#   ● attached / ○ detached   (any client viewing it)
#   ✓ active context          a live claude that has actually loaded a conversation.
#                             Blank for: a claude parked on its startup splash
#                             (_dev_session_at_welcome — "idle, no conversation"),
#                             and for a session that's exited to a shell ("no active
#                             session"). Independent of attach state.
# Shared by `dev list` and `dev ls`. With a <scope> dir arg, only sessions whose
# working dir is that dir (or below) are listed — `dev ls` passes the current repo.
#
# _dev_cwd_repo_dir — print the DEV_REPOS directory that contains $PWD (exact match
# or an ancestor; longest/most-specific wins), else nothing. The scope source for
# `dev ls` — path-based, so it catches every slot in that repo regardless of which
# alias keyed it (dot-* and dotfiles-* both root at ~/code/dotfiles).
_dev_cwd_repo_dir() {
  local k d best=
  for k in ${(k)DEV_REPOS}; do
    d=${DEV_REPOS[$k]}
    [[ $PWD == $d || $PWD == $d/* ]] && (( ${#d} > ${#best} )) && best=$d
  done
  [[ -n $best ]] && print -r -- "$best"
}

# _t_infer_repo [slot] — the repo ALIAS implied by $PWD, for the repo-aware verb
# defaults (`t paste 4` in ~/code/financial-forecast → that repo's slot 4; bare
# `t open` → the repo you're standing in). _dev_cwd_repo_dir gives the DIR, but
# slot verbs target session NAMES (dev-<alias>-<slot>) and several aliases can key
# one dir (dot-* and dotfiles-* both root at ~/code/dotfiles) — so a LIVE dev-*
# session rooted in this repo dir wins and the alias is read off the actual
# session name: the exact <slot>'s session when one is given, else the dir's
# first live session (so a bare `t open`/`t paste` joins the alias already in
# use here instead of minting a sibling slot under a second alias). No live
# session → the DEV_REPOS key for the dir: the key matching its basename, else
# the shortest (the customary shorthand). Prints nothing / rc 1 outside any
# DEV_REPOS dir, so callers can drop to their usage text.
_t_infer_repo() {
  local slot="$1" dir; dir=$(_dev_cwd_repo_dir)
  [[ -n $dir ]] || return 1
  # Two passes when a slot is given: that exact slot's session first, then any
  # live session here (a not-yet-live slot still joins the in-use alias).
  local -a pats=("[0-9]\{1,\}")
  [[ $slot == <-> ]] && pats=("$slot" "[0-9]\{1,\}")
  local pat s p
  for pat in $pats; do
    for s in ${(f)"$(tmux list-sessions -F '#{session_name}' 2>/dev/null | grep -- "^dev-.*-${pat}\$")"}; do
      p=$(tmux display-message -p -t "$s" '#{session_path}' 2>/dev/null)
      if [[ $p == $dir || $p == $dir/* ]]; then
        s=${s#dev-}; print -r -- "${s%-*}"; return 0   # last dash splits off the slot
      fi
    done
  done
  local k best=
  for k in ${(k)DEV_REPOS}; do
    [[ ${DEV_REPOS[$k]} == $dir ]] || continue
    [[ $k == ${dir:t} ]] && { print -r -- "$k"; return 0 }
    if [[ -z $best ]] || (( ${#k} < ${#best} )); then best=$k; fi
  done
  [[ -n $best ]] || return 1
  print -r -- "$best"
}

_dev_list() {
  local scope="$1"
  local names
  names=$(tmux list-sessions -F '#{session_name}' 2>/dev/null | grep '^dev-' | sort)
  # Scope to the current repo dir (path-based) when asked: keep only sessions whose
  # session_path is <scope> or below it.
  if [[ -n $scope ]]; then
    local kept=() _s _d
    while IFS= read -r _s; do
      [[ -n $_s ]] || continue
      _d=$(tmux display-message -p -t "$_s" '#{session_path}' 2>/dev/null)
      [[ $_d == $scope || $_d == $scope/* ]] && kept+=("$_s")
    done <<< "$names"
    names=${(F)kept}
  fi
  # Foreground (non-tmux) claudes, same scope (rows carry cwd in field 2).
  local fgrows; fgrows=$(_dev_fg_rows 2>/dev/null)
  [[ -n $scope && -n $fgrows ]] && fgrows=$(print -r -- "$fgrows" | awk -F'\t' -v d="$scope" '$2==d || index($2, d"/")==1')
  if [[ -z "$names" && -z "$fgrows" ]]; then
    echo "No dev sessions running${scope:+ in ${scope:t} (dev ls --all for every repo)}."
    return 0
  fi
  local g c y b r0=
  if [[ -t 1 ]]; then g=$'\e[32m'; c=$'\e[36m'; y=$'\e[2m'; b=$'\e[1m'; r0=$'\e[0m'; fi
  # widest name (dev slot sans dev-, plus foreground "<repo>:fg" labels) so WORKING ON lines up
  local s short name_w=7
  while IFS= read -r s; do [[ -n $s ]] || continue; short="${s#dev-}"; (( ${#short} > name_w )) && name_w=${#short}; done <<< "$names"
  local _fsid _fcwd _fslot _frest
  while IFS=$'\t' read -r _fsid _fcwd _fslot _frest; do [[ -n $_fslot ]] || continue; (( ${#_fslot} > name_w )) && name_w=${#_fslot}; done <<< "$fgrows"
  # prefix before the summary = 2 (indent) + 8 (STATUS field) + name_w + 1 (gap)
  local avail=$(( ${COLUMNS:-80} - 11 - name_w ))
  (( avail < 12 )) && avail=$(( 80 - 11 - name_w ))
  print -r -- "dev sessions   ${g}●${r0} attached · ${c}✓${r0} active context${scope:+   ${y}(repo: ${r0}${b}${c}${scope:t}${r0}${y} — --all for all)${r0}}"
  print -r -- ""
  printf '  %s%-8s%-*s %s%s\n' "$y" 'STATUS' $name_w 'SESSION' 'WORKING ON' "$r0"
  local state dir amark cmark summary
  while IFS= read -r s; do
    [[ -n $s ]] || continue
    short="${s#dev-}"
    state=$(tmux display-message -p -t "$s" '#{?session_attached,attached,detached}' 2>/dev/null)
    dir=$(tmux display-message -p -t "$s" '#{session_path}' 2>/dev/null)
    if [[ $state == attached ]]; then amark="${g}●${r0}"; else amark='○'; fi
    if ! _dev_session_has_claude "$s"; then
      cmark=' '; summary='(no active session)'
    elif _dev_session_at_welcome "$s"; then
      cmark=' '; summary='(idle — no conversation)'
    else
      cmark="${c}✓${r0}"
      summary=$(_dev_session_summary "$s" "$dir")
      [[ -n $summary ]] || summary='(untitled session)'
    fi
    (( ${#summary} > avail )) && summary="${summary[1,avail-1]}…"
    # STATUS field (8 cols): "<amark> <cmark>" = 3 visible glyph cols + 5 pad
    printf '  %s %s     %-*s %s%s%s\n' "$amark" "$cmark" $name_w "$short" "$y" "$summary" "$r0"
  done <<< "$names"
  # foreground rows: always ● (you're in the terminal); ✓ when context is active
  local fstate fcontext fsummary
  while IFS=$'\t' read -r _fsid _fcwd _fslot fstate fcontext fsummary; do
    [[ -n $_fslot ]] || continue
    [[ $fcontext == active ]] && cmark="${c}✓${r0}" || cmark=' '
    (( ${#fsummary} > avail )) && fsummary="${fsummary[1,avail-1]}…"
    printf '  %s %s     %-*s %s%s%s\n' "${g}●${r0}" "$cmark" $name_w "$_fslot" "$y" "$fsummary" "$r0"
  done <<< "$fgrows"
  # reattach legend: tmux slots via `t open <repo> <slot>`; a foreground (:fg) row is
  # bound to its terminal, so it comes back foreground via `t open <id>` (shown
  # only when the list actually has a :fg row).
  local foot="reattach: t open <repo> <slot>"
  [[ -n $fgrows ]] && foot+=" · foreground: t open <id>"
  print -r -- ""
  print -r -- "  ${y}${foot}${r0}"
}

# _dev_session_rows — machine-readable sibling of _dev_list: one tab-separated
# "<sid>\t<cwd>\t<slot>\t<state>\t<context>\t<summary>" row per live dev-<repo>-<slot>
# tmux session (sid = the stamped CLAUDE_RESUME_ID; slot = the name minus `dev-`;
# state = attached|detached; context = active|idle|none — the same distinction the
# ✓ glyph draws; summary = the "what it's working on" line). No glyphs/colors/headers
# — it's meant to be *collected* (locally and over ssh, like _claude_session_rows)
# and re-rendered. This is the per-host scan behind `dev ls -r`: live dev slots are
# what genuinely differ machine-to-machine (transcripts already converge via csync),
# so the cross-host view lists these, not transcripts.
_dev_session_rows() {
  local names
  names=$(tmux list-sessions -F '#{session_name}' 2>/dev/null | grep '^dev-' | sort)
  local s short sid dir state context summary
  while IFS= read -r s; do
    [[ -n $s ]] || continue
    short="${s#dev-}"
    dir=$(tmux display-message -p -t "$s" '#{session_path}' 2>/dev/null)
    sid=$(tmux show-environment -t "$s" CLAUDE_RESUME_ID 2>/dev/null | cut -d= -f2)
    # `-` sentinel for an unstamped slot (idle / no conversation): keeps every
    # field non-empty so a tab is never a *leading/consecutive* IFS-whitespace
    # delimiter that `read` would collapse, sliding the columns.
    [[ -n $sid ]] || sid='-'
    state=$(tmux display-message -p -t "$s" '#{?session_attached,attached,detached}' 2>/dev/null)
    if ! _dev_session_has_claude "$s"; then
      context=none; summary='(no active session)'
    elif _dev_session_at_welcome "$s"; then
      context=idle; summary='(idle — no conversation)'
    else
      context=active; summary=$(_dev_session_summary "$s" "$dir"); [[ -n $summary ]] || summary='(untitled session)'
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$sid" "$dir" "$short" "$state" "$context" "$summary"
  done <<< "$names"
  # plus any FOREGROUND (non-tmux) claudes on this machine, same row format.
  _dev_fg_rows
}

# _dev_rows_all — fan _dev_session_rows out over THIS machine + every $REMOTE_HOSTS
# entry and print each row prefixed with its host ("<host>\t<sid>\t<cwd>\t<slot>\t
# <state>\t<context>\t<summary>"; host = the REMOTE_HOSTS key, or "local"). The data
# source behind `dev ls -r`. Remote hosts are scanned IN PARALLEL over ssh
# (background jobs → temp files → `wait`) with a short ConnectTimeout + BatchMode so
# a sleeping/offline Mac is skipped fast and noted on stderr, never waited on. Each
# host's ssh exit code is stashed in a `.rc` file so we can tell "unreachable"
# (rc 255) from "reachable but the scan errored" (e.g. rc 127 → stale dotfiles), and
# warn accordingly. local-first; within a host, _dev_session_rows' own order holds.
# Rows are prefixed verbatim (no re-splitting), so empty fields can't collapse.
_dev_rows_all() {
  # no_monitor/no_notify: this fans out with `&` + `wait`; in a monitor-mode shell that
  # would print in-run job-control notices ("[2] 67794", "exit 1 …", "done"). Silences
  # them for a *direct* interactive call. local_options restores the caller's settings
  # on return — which is why it canNOT silence the "N jobs SIGHUPed" warning printed at
  # SHELL EXIT (monitor is back on by then): the `t ls -r` path spawns `zsh -lic
  # _dev_rows_all` as the top-level command, and that exit warning is killed by bin/t's
  # ZSH_PRELUDE (top-level `setopt no_monitor`, which persists to exit). The `$(…)`
  # subshell callers below already run job-control-off, so they never warn either.
  setopt local_options no_monitor no_notify
  local tmpd; tmpd=$(mktemp -d) || return 1
  _dev_session_rows > "$tmpd/.local" 2>/dev/null &
  local h
  for h in ${(k)REMOTE_HOSTS}; do
    ( ssh -o ConnectTimeout=3 -o BatchMode=yes "${REMOTE_HOSTS[$h]}" 'zsh -lic _dev_session_rows' \
        > "$tmpd/$h" 2>/dev/null
      print -r -- $? > "$tmpd/$h.rc" ) &
  done
  wait

  local host file rc line
  for host in local ${(k)REMOTE_HOSTS}; do
    if [[ $host == local ]]; then
      file="$tmpd/.local"
    else
      file="$tmpd/$host"
      rc=$(< "$tmpd/$host.rc" 2>/dev/null)
      if [[ -z $rc || $rc == 255 ]]; then          # ssh-level failure = unreachable
        print -u2 -r -- "dev: $host unreachable — skipped"
        continue
      elif [[ $rc == 127 ]]; then                  # command not found = stale dotfiles
        print -u2 -r -- "dev: $host scan failed (rc=127; stale dotfiles? run \`dots\` there) — skipped"
        continue
      elif [[ $rc != 0 ]]; then                    # reachable, but the scan errored
        print -u2 -r -- "dev: $host scan failed (rc=$rc) — skipped"
        continue
      fi
    fi
    [[ -s $file ]] || continue
    while IFS= read -r line; do
      printf '%s\t%s\n' "$host" "$line"
    done < "$file"
  done
  rm -rf "$tmpd"
}

# _dev_list_remote — `dev ls -r`: _dev_list across THIS machine AND every
# $REMOTE_HOSTS host. Same rendering as _dev_list (the "● attached · ✓ active
# context" header, $COLUMNS-truncated summary), but driven by _dev_rows_all instead
# of a direct tmux scan, and with a dedicated HOST column (STATUS/HOST/SESSION/
# WORKING ON). A row on THIS machine leaves HOST *blank* (so "here" reads as absence,
# not a peer host named `local`) while remote rows show their $REMOTE_HOSTS key — that
# is the local/host disambiguation, and `local` never widens the column. A trailing
# footer spells out the two verbs: `t open <repo> <slot>` (auto-attaches it in place on
# its host) and `t beam <repo> <slot> --from <host>` (pulls it down here — a move).
# Read-only itself.
_dev_list_remote() {
  local scope="$1"
  local rows; rows=$(_dev_rows_all)
  # Scope to the current repo dir (rows carry cwd in field 3; repos share the same
  # ~/code path on every machine, so a local scope filters remote rows too).
  [[ -n $scope ]] && rows=$(print -r -- "$rows" | awk -F'\t' -v d="$scope" '$3==d || index($3, d"/")==1')
  if [[ -z $rows ]]; then
    echo "No dev sessions running${scope:+ in ${scope:t} (dev ls -r --all for every repo)}${scope:+,} on this machine or any reachable host."
    return 0
  fi
  local g c y b r0=
  if [[ -t 1 ]]; then g=$'\e[32m'; c=$'\e[36m'; y=$'\e[2m'; b=$'\e[1m'; r0=$'\e[0m'; fi
  # widest HOST and SESSION cells so both columns line up (headers are the floor:
  # "HOST"=4, "SESSION"=7). HOST is its own column, SESSION stays the bare slot.
  local host sid cwd slot state context summary host_w=4 name_w=7
  while IFS=$'\t' read -r host sid cwd slot state context summary; do
    # local rows render with a BLANK host cell (see below), so they never widen it.
    [[ $host != local ]] && (( ${#host} > host_w )) && host_w=${#host}
    (( ${#slot} > name_w )) && name_w=${#slot}
  done <<< "$rows"
  # prefix before WORKING ON = 2 indent + 8 STATUS + host_w + 1 gap + name_w + 1 gap
  local avail=$(( ${COLUMNS:-80} - 12 - host_w - name_w ))
  (( avail < 12 )) && avail=$(( 80 - 12 - host_w - name_w ))
  print -r -- "dev sessions   ${g}●${r0} attached · ${c}✓${r0} active context${scope:+   ${y}(repo: ${r0}${b}${c}${scope:t}${r0}${y} — --all for all)${r0}}"
  print -r -- ""
  printf '  %s%-8s%-*s %-*s %s%s\n' "$y" 'STATUS' $host_w 'HOST' $name_w 'SESSION' 'WORKING ON' "$r0"
  local amark cmark hostcell
  while IFS=$'\t' read -r host sid cwd slot state context summary; do
    [[ $state == attached ]] && amark="${g}●${r0}" || amark='○'
    [[ $context == active ]] && cmark="${c}✓${r0}" || cmark=' '
    (( ${#summary} > avail )) && summary="${summary[1,avail-1]}…"
    # "this machine" rows leave HOST blank so they read as local, not a peer host.
    hostcell=$host; [[ $host == local ]] && hostcell=
    printf '  %s %s     %-*s %-*s %s%s%s\n' "$amark" "$cmark" $host_w "$hostcell" $name_w "$slot" "$y" "$summary" "$r0"
  done <<< "$rows"
  # reattach legend: `t open` auto-attaches a slot on its host; `t beam … --from <host>`
  # pulls it here (a move). A foreground (:fg) row is bound to its terminal, so it is
  # reattached on its host via `t on <host> t open <id>` (shown only when a :fg row is
  # present — slot is field 4 of the prefixed rows).
  local foot="attach (auto-finds its host): t open <repo> <slot> · pull here: t beam <repo> <slot> --from <host>"
  print -r -- "$rows" | awk -F'\t' '$4 ~ /:/{f=1} END{exit !f}' \
    && foot+=" · foreground: t on <host> t open <id>"
  print -r -- ""
  print -r -- "  ${y}${foot}${r0}"
}

# _dev_kill_one <session> <force> — kill a single dev tmux session. When it holds
# a live Claude (active context) and <force> is empty, confirm first: killing only
# SIGHUPs Claude and the transcript is appended live (so the conversation stays
# resumable via tpop/dev), but we still guard against a typo dropping a live turn.
# The confirm is gated on ACTIVE CONTEXT, not merely a live process: a claude
# parked on its startup splash (_dev_session_at_welcome — what `dev ls` reports as
# "idle — no conversation") has no conversation to interrupt, so kill it without
# the prompt. Mirrors _dev_list's two-signal distinction; without it `dev kill`
# prompted "Claude is live there" for the very sessions ls calls idle.
_dev_kill_one() {
  local session="$1" force="$2"
  if [[ -z "$force" ]] && _dev_session_has_claude "$session" \
       && ! _dev_session_at_welcome "$session"; then
    read -q "REPLY?Kill $session? Claude is live there (context interrupted). [y/N] " \
      || { print; echo "Skipped $session."; return 1; }
    print
  fi
  tmux kill-session -t "$session" 2>/dev/null && echo "Killed $session"
}

# _dev_kill <repo> <slot|all> [force] — tear down dev-<repo>-<slot> sessions.
# Keyed on session NAMES, not DEV_REPOS, so it also reaches orphaned sessions
# whose repo alias is gone. A slot (or `all`) is REQUIRED — with no slot we list
# the repo's sessions and bail rather than guess which to kill.
_dev_kill() {
  local repo="$1" slot="$2" force="$3"

  # Repo-aware: `t kill 4` / `t kill all` mean the repo $PWD is in (live-session
  # name wins — see _t_infer_repo); bare `t kill` infers the repo and then lists
  # its slots below (a slot is still required — no guessing on a kill).
  if [[ -z ${DEV_REPOS[$repo]:-} && -z $slot && ( $repo == <-> || $repo == all ) ]]; then
    slot=$repo; repo=$(_t_infer_repo "$slot")
  elif [[ -z $repo ]]; then
    repo=$(_t_infer_repo)
  fi

  # A DEV_REPOS dir can carry several aliases (`dot` and `dotfiles` both key
  # ~/code/dotfiles); a live local session is named after whichever alias started
  # it. `t kill dotfiles 2` must therefore also reach `dev-dot-2` in the same
  # tree — otherwise the name-prefix scan below finds nothing, the local live
  # check in _dev_remote_delegate/_dev_session_remote_fallback (which both key
  # off `dev-${repo}-${slot}`) also misses it, and kill delegates remotely while
  # the local same-numbered session keeps running (violating one-live-owner).
  # For a specific slot, canonicalize $repo to the sibling alias running it.
  # For `all`/no-slot, union every sibling alias keyed to this dir so a mass
  # kill reaches `dev-dot-1` AND `dev-dotfiles-2` in one tree (picking a single
  # alias would silently leave sessions running under the others).
  local -a _repos=( "$repo" )
  if [[ -n ${DEV_REPOS[$repo]:-} ]]; then
    local _kdir=${DEV_REPOS[$repo]} _kk
    if [[ -n $slot && $slot != all ]]; then
      for _kk in ${(k)DEV_REPOS}; do
        [[ $_kk == $repo || ${DEV_REPOS[$_kk]} != $_kdir ]] && continue
        tmux has-session -t "dev-${_kk}-${slot}" 2>/dev/null && { repo=$_kk; _repos=( $_kk ); break; }
      done
    else
      for _kk in ${(k)DEV_REPOS}; do
        [[ $_kk == $repo || ${DEV_REPOS[$_kk]} != $_kdir ]] && continue
        _repos+=( $_kk )
      done
    fi
  fi

  if [[ -z "$repo" ]]; then
    {
      print -r -- "t kill — tear down a dev session (or all of a repo's)"
      print -r -- ""
      print -r -- "Usage: t kill [repo] <slot|all> [-y]   (no repo: the one \$PWD is in; --remote kills on its host)"
    } | _help_style
    return 1
  fi

  # collect this repo's live sessions by name (dev-<repo>-<N>), numerically sorted.
  # For `all`/no-slot, $_repos is the union of sibling aliases sharing this dir.
  local -a sessions
  sessions=( ${(f)"$(tmux list-sessions -F '#{session_name}' 2>/dev/null \
    | grep -E "^dev-(${(j:|:)_repos})-[0-9]+\$" | sort -t- -k3 -n)"} )

  if (( ! ${#sessions} )); then
    # None live HERE. If a specific slot was named and it is live on another host, tear
    # it down there over ssh -t (the remote `t kill` still prompts unless -y); -r forces
    # this explicitly. With no slot, never auto-pick a kill target — just point at where
    # to look.
    if [[ -n $slot && $slot != all ]]; then
      _dev_remote_delegate "$repo" "$slot" kill ${force:+-y} && return
    fi
    echo "No sessions for '$repo' here."
    [[ -z $slot || $slot == all ]] && (( ${#REMOTE_HOSTS} )) && \
      echo "(check other hosts: \`t ls -r\`; kill there with \`t kill -r $repo${slot:+ $slot}\`)"
    return 1
  fi

  # `all` — kill every slot for the repo (explicit opt-in to a mass kill).
  if [[ "$slot" == all ]]; then
    local s
    for s in $sessions; do _dev_kill_one "$s" "$force"; done
    return
  fi

  # no slot — refuse to guess; show what's there so the user can pick one.
  if [[ -z "$slot" ]]; then
    echo "Specify a slot to kill (or 'all'). Sessions for '$repo':"
    local s
    for s in $sessions; do
      if _dev_session_has_claude "$s"; then echo "  $s  ✓ (Claude live)"; else echo "  $s"; fi
    done
    return 1
  fi

  local session="dev-${repo}-${slot}"
  if ! tmux has-session -t "$session" 2>/dev/null; then
    # Live on another host? Tear it down there (shared fallback; remote `t kill` still
    # confirms unless -y). Same remote detection pop/plan get; -r forces it explicitly.
    _dev_session_remote_fallback "$session" kill ${force:+-y} && return
    echo "No session: $session"
    return 1
  fi
  _dev_kill_one "$session" "$force"
}

# _dev_new_session <session> <dir> [branch] — create a detached tmux session in
# <dir>, start logging, and launch Claude on <branch> (default $DEV_BRANCH).
# Callers pass the repo's resolved branch (see _dev_branch_for) since the repo
# alias isn't recoverable from <session> reliably. Shared by `dev` and `tpaste`
# so the bootstrap (branch dance, geometry, logging) lives in one place; callers
# attach (or not) and deliver input themselves afterwards.
#
# We *pre-assign* Claude's session id (a lowercased uuidgen) and pass it as
# `claude --session-id`, then stash it on the tmux session as CLAUDE_RESUME_ID —
# the same precise signal _dev_resume_session records. Without it, every slot in
# a repo shares one fallback (the dir's newest transcript), so `dev ls` showed
# identical summaries for sibling slots and `tpop` couldn't target a specific
# one. uuidgen is uppercase but Claude stores ids lowercase, so we lowercase to
# keep the transcript filename glob (`<sid>.jsonl`) matching.
_dev_new_session() {
  local session="$1" dir="$2" branch="${3:-$DEV_BRANCH}"
  local logfile="$HOME/.tmux-logs/${session}.log"
  local sid; sid="$(uuidgen | tr 'A-Z' 'a-z')"
  mkdir -p "$HOME/.tmux-logs"
  # No fixed -x/-y geometry: a session seeded oversized (was 220x50) stays bigger
  # than a narrow client until it resizes, so attaching from a phone (Termius)
  # showed tmux's status-right pan indicator ([x,y], reads like "20") and the UI
  # overflowed the screen. window-size latest makes the window track whichever
  # client is active, so it fits the phone on attach. (latest is tmux's default,
  # but we set it explicitly so it holds on machines with a different default.)
  tmux new-session -d -s "$session" -c "$dir"
  tmux set-option -t "$session" window-size latest 2>/dev/null
  tmux pipe-pane -t "$session" -o "cat >> $logfile"
  tmux set-environment -t "$session" CLAUDE_RESUME_ID "$sid"
  # `; exit` so quitting Claude closes the pane's shell and tears down the
  # (single-window) tmux session instead of leaving an idle prompt behind. Fires
  # on any exit (clean or crash); crash output survives in the pipe-pane logfile
  # (`t read`). `t pop` kill-sessions the slot itself, so the exit is moot there.
  tmux send-keys -t "$session" "_dev_repo_prepare ${(q)branch}; claude --session-id $sid; exit" Enter
}

# dev — open/reattach a Claude Code tmux session (local or on another host)
#
# Usage: dev <repo> [slot|new] [-f]
#        dev -r [repo [slot]]
#
# Commands:
#   dev <repo> [slot|new]        open/reattach (slot 1-4, or 'new' to force fresh)
#   dev <id> | <repo> fg|<id>    reattach a foreground (:fg) session here (claude -r;
#                                stops its terminal-bound owner — matches how it runs);
#                                <id> is the short hex id `t ls` shows (dot:a4aa5f6a)
#   dev -r [repo [slot]]         attach a slot that's live on another $REMOTE_HOSTS
#                                host (host auto-inferred; fzf-pick if several match)
#   dev list | ls [-r] [-a]      list dev sessions (attached + active context),
#                                scoped to the repo you're in; -a/--all for every
#                                repo; -r spans every $REMOTE_HOSTS host (cross-host)
#   dev kill <repo> <slot|all>   tear down a session  [-y to skip the confirm]
#   dev -r kill <repo> [slot]    tear down a slot on its $REMOTE_HOSTS host instead
#
# Options:
#   -f, --fg     no tmux: foreground-RESUME the named slot if it's live (≡ tpop),
#                else git setup + a fresh claude inline (alias: --no-tmux)
#   -y, --yes    dev kill: skip the "Claude is live" confirm (alias: --force)
#                A slot that is not live HERE but is live on a $REMOTE_HOSTS host is
#                found automatically and attached IN PLACE there (host inferred) — no
#                flag needed; a live LOCAL slot always wins. open never MOVES a session
#                between machines — that is `tbeam` (send) / `tbeam --from` (receive).
#   -r, --remote  FORCE the remote branch even when a local slot exists; with `ls`
#                 list every host; with `kill` kill it there; bare `t open -r` picks
#   -l, --local   FORCE this machine: skip the remote auto-attach probe, so a slot
#                 live only on another host is left there and a fresh LOCAL slot is
#                 opened instead (alias: --here; --here --fg = local foreground)
#
# repo is a key of DEV_REPOS (configured in ~/.zshrc.local). The checked-out branch
# is per-repo (DEV_BRANCHES[repo], else $DEV_BRANCH). dev kill matches session names,
# so it also reaches orphaned sessions whose repo alias is gone (dev kill dotfiles 1).
# Surveying what's live everywhere is `dev ls -r`; sending a session away is `tbeam`.
_t_dev() {
  local no_tmux= force= remote= all= local_only=
  local -a pos
  local arg
  # -f/--fg = foreground/no-tmux EVERYWHERE (matches tbeam -f). The kill-confirm
  # skip moved off -f onto -y/--yes (--force kept as a long alias) so -f never
  # means two things. `dev kill` returns before the no_tmux check below, so a
  # stray -f on a kill is just an inert no-op rather than a silent force.
  for arg in "$@"; do
    case "$arg" in
      -h|--help)         _help_for _t_dev; return 0 ;;
      -f|--fg|--no-tmux) no_tmux=1 ;;
      -y|--yes|--force)  force=1 ;;
      -r|--remote)       remote=1 ;;
      -l|--local|--here) local_only=1 ;;
      -a|--all)          all=1 ;;
      *)                 pos+=("$arg") ;;
    esac
  done
  local repo="${pos[1]}"
  local slot="${pos[2]}"

  # `dev list` (or `ls`) — show sessions + state, then stop. `-r` spans every
  # $REMOTE_HOSTS host too (a cross-host view), else just this machine. By default
  # it's SCOPED to the repo you're in (path-based, so dot-*/dotfiles-* both show in
  # the dotfiles dir); `-a`/`--all` widens to every repo, as does standing outside
  # any DEV_REPOS dir (nothing to scope to).
  if [[ "$repo" == list || "$repo" == ls ]]; then
    local scope=; [[ -z $all ]] && scope=$(_dev_cwd_repo_dir)
    if [[ -n $remote ]]; then _dev_list_remote "$scope"; else _dev_list "$scope"; fi
    return
  fi

  # `dev kill <repo> <slot|all>` — tear down a session (or all of a repo's).
  # Operates on session NAMES, not DEV_REPOS keys, so it can also clean up
  # orphaned sessions whose repo alias no longer exists (e.g. dev-dotfiles-*).
  # With -r, kill it on its $REMOTE_HOSTS host instead (host auto-inferred).
  if [[ "$repo" == kill ]]; then
    if [[ -n $remote ]]; then
      _dev_remote_kill "${pos[2]}" "${pos[3]}" "$force"
    else
      _dev_kill "${pos[2]}" "${pos[3]}" "$force"
    fi
    return
  fi

  # Remote-aware open (explicit half): a slot can live on THIS machine or another.
  # -r/--remote FORCES the remote branch — attach in place on its host (the session
  # stays put; MOVING it between machines is `t beam`'s job), and a bare `t open -r` is
  # the all-remote picker. The AUTO half runs further down, once <repo> is resolved from
  # the cwd, so even a bare `t open` in a repo dir can find a slot that is live only on
  # another host. To pull a remote session HERE (a move), use `t beam … --from <host>`.
  # -l/--local/--here and -r/--remote are opposite intents (force local vs. force
  # remote), so reject the combination rather than silently letting -r win.
  if [[ -n $remote && -n $local_only ]]; then
    echo "t open: --local/--here and -r/--remote are mutually exclusive" >&2
    return 2
  fi
  if [[ -n $remote ]]; then
    _dev_remote "$repo" "$slot" "$no_tmux"     # explicit -r: force attach in place on its host
    return
  fi

  # An id-shaped lone arg (`t open f0f1bbef`, or the displayed `dot:f0f1bbef` label)
  # is a foreground-session handle, not a repo — adopt that session here. The id
  # alone identifies it, so no repo is needed; a real DEV_REPOS key always wins
  # (checked first), and _dev_fg_handle never matches slot numbers or `new`.
  if [[ -n "$repo" && -z "$slot" && -z "${DEV_REPOS[$repo]}" ]] && _dev_fg_handle "$repo"; then
    _dev_adopt_fg "$repo"
    return
  fi

  # Repo-aware defaults: bare `t open` targets the repo $PWD is in, and a lone
  # numeric/slot-keyword first arg is a SLOT of it (`t open 4` ≡ `t open <cwd-repo> 4`,
  # `t open --new` → a fresh slot here, `t open fg` → adopt this repo's :fg session)
  # — see _t_infer_repo. Deliberately AFTER the -r branch (so the documented bare
  # `t open -r` every-slot picker survives) and the id-shaped fg adoption above;
  # the remote auto-probe runs further down, once <repo> is resolved, so even a
  # bare `t open` can find a slot that is live only on another host. Outside every
  # DEV_REPOS dir this leaves repo empty and the usage below explains.
  # `t open 4 --new` lands here as `repo=4 slot=new` (--new became a positional in
  # _t_open) — the explicit numeric slot still wins, the redundant keyword drops.
  if [[ -z ${DEV_REPOS[$repo]:-} && ( $repo == <-> || $repo == new || $repo == fg ) \
        && ( -z $slot || $slot == new || $slot == fg ) ]]; then
    slot=$repo; repo=
  fi
  [[ -z $repo ]] && repo=$(_t_infer_repo "$slot")

  # repo→path map: see the global DEV_REPOS (defined near the cd shortcuts)

  if [[ -z "$repo" || -z "${DEV_REPOS[$repo]}" ]]; then
    # Styled gh-style (piped through _help_style), but the Repos: section is built
    # from the ACTUAL ${(k)DEV_REPOS} — the dynamic bit static help can't show.
    {
      print -r -- "t open — open or reattach a Claude session in a per-repo tmux slot"
      print -r -- ""
      print -r -- "Usage: t open [${(kj:|:)DEV_REPOS:-repo}] [slot] [--new] [--fg]"
      print -r -- ""
      print -r -- "Commands:"
      print -r -- "  t open                      no repo: the one \$PWD is in (t open 4 = its slot 4)"
      print -r -- "  t open <repo> [slot]        open/reattach (slot 1-4, --new to force fresh)"
      print -r -- "  t open <id> | <repo> fg     adopt a foreground (:fg) session here (id from t ls)"
      print -r -- "  t open <repo> [slot] --fg   no tmux: foreground-resume / run inline"
      print -r -- "  t open <repo> [slot] --here force this machine (skip remote probe); --here --fg = local foreground"
      print -r -- "  t open <repo> [slot]        auto-attaches on another host if the slot is live there"
      print -r -- "  t open <repo> [slot] -r --new        start a fresh session on the default remote host"
      print -r -- "  t open <repo> [slot] --host <host>   start/attach on a specific host"
      print -r -- "  t beam <repo> [slot] --from <host>   pull that session HERE (move); omit --from to send"
      print -r -- "  t ls [-r] [-a]              list sessions (attached + active context)"
      print -r -- "  t kill <repo> <slot|all>    tear down a session (-y to skip the confirm)"
      print -r -- "  t -h                        full verb list + per-verb help"
      print -r -- ""
      print -r -- "Repos:"
      if (( ${#DEV_REPOS} )); then
        local _k
        for _k in ${(ok)DEV_REPOS}; do print -r -- "  $_k  ${DEV_REPOS[$_k]}"; done
      else
        print -r -- "  (none configured — add them to ~/.zshrc.local; see .zshrc.local.example)"
      fi
    } | _help_style
    return 1
  fi

  local dir="${DEV_REPOS[$repo]}"
  local branch="$(_dev_branch_for "$repo")"

  if [[ ! -d "$dir" ]]; then
    echo "Repo dir not found: $dir"
    return 1
  fi

  # `t open <repo> fg|<id>` — reattach a FOREGROUND (:fg) session listed by `t ls`.
  # It is bound to its own terminal, so reattaching means MOVING it here: stop that
  # owner and `claude -r` in THIS terminal. Matches how it is running — the tmux-slot
  # analog is plain `t open <repo> <slot>`. (`fg` as the slot keyword mirrors the
  # `:fg` label and scopes to the repo; an id-shaped slot — see _dev_fg_handle —
  # names the exact session. -f is irrelevant here: fg adoption is inherently a
  # foreground resume.)
  if [[ "$slot" == fg ]]; then
    _dev_adopt_fg "$repo"
    return
  elif _dev_fg_handle "$slot"; then
    _dev_adopt_fg "$slot"
    return
  fi

  # Same-dir sibling aliases (see _dev_kill / _t_pop): a `dev-dot-2` answers
  # `t open dotfiles 2` when `dot` and `dotfiles` both key ~/code/dotfiles.
  # Without this canonicalization the local live check below misses it and we
  # either ssh to a remote slot of the same number or mint a duplicate local
  # `dev-dotfiles-2`, violating the one-live-owner invariant for the slot.
  if [[ -n $slot && $slot != new && $slot != fg && -n ${DEV_REPOS[$repo]:-} ]] \
     && ! tmux has-session -t "dev-${repo}-${slot}" 2>/dev/null; then
    local _odir=${DEV_REPOS[$repo]} _ok
    for _ok in ${(k)DEV_REPOS}; do
      [[ $_ok == $repo || ${DEV_REPOS[$_ok]} != $_odir ]] && continue
      tmux has-session -t "dev-${_ok}-${slot}" 2>/dev/null && { repo=$_ok; break; }
    done
  fi

  # Remote-aware open (auto half): <repo> is a valid key now (cwd-defaulted if bare) and
  # fg adoption already returned, so a slot that is NOT live HERE but IS live on a
  # $REMOTE_HOSTS host gets attached IN PLACE there (host inferred) — a live LOCAL slot
  # always wins (cheap tmux check, no ssh). Skipped with no hosts, for -f/`new` (a
  # foreground/fresh request stays local), and when a local slot is already live; if
  # nothing is live remotely either, fall through to the local fresh-start path.
  # -l/--local/--here forces this machine: skip the probe entirely so a slot that
  # is live only on another host is NOT attached — a fresh local slot is opened
  # instead (combine with --fg for a local foreground resume / inline claude).
  if (( ${#REMOTE_HOSTS} )) && [[ -z $no_tmux && -z $local_only && $slot != new ]] && ! _dev_local_slot_live "$repo" "$slot"; then
    local res; res=$(_dev_remote_resolve "$repo" "$slot" 2>/dev/null)   # quiet probe
    if [[ -n $res ]]; then
      echo "(not live here — attaching on ${res%%$'\t'*}; Ctrl-b d to detach)"
      _dev_remote_attach "$res" "$no_tmux"
      return
    fi
    # nothing live remotely either → fall through to the local path (fresh start)
  fi

  # -f/--fg (a.k.a. --no-tmux): run claude inline, no tmux. If a SPECIFIC slot is
  # named and it's live, foreground-RESUME that conversation (`claude -r`) — matching
  # what -f means in tbeam / `dev -r`; that's exactly `tpop`, so delegate to it (it
  # kills the slot first, honoring the one-live-owner invariant). Otherwise (no slot,
  # `new`, or that slot doesn't exist yet) start a FRESH claude inline after the
  # branch dance — slot is a tmux concept, so the fresh path has none.
  if [[ -n "$no_tmux" ]]; then
    if [[ -n "$slot" && "$slot" != new ]] && tmux has-session -t "dev-${repo}-${slot}" 2>/dev/null; then
      _t_pop "$repo" "$slot"
      return
    fi
    echo "Starting claude in $dir (no tmux)"
    cd "$dir" || return 1
    _dev_repo_prepare "$branch"
    claude
    return
  fi

  # `dev <repo> new` — force the next never-used slot (skip reattaching to an
  # existing unattached session); always spins up a fresh Claude.
  if [[ "$slot" == new ]]; then
    local n=1
    while tmux has-session -t "dev-${repo}-${n}" 2>/dev/null; do (( n++ )); done
    slot=$n
  fi

  # auto-pick slot: REATTACH to the lowest-numbered existing-but-unattached
  # session before ever spawning a fresh one. A free *gap* (e.g. slot 1 was
  # killed, leaving 2 live) is only a fallback — so `t open <repo>` lands on the
  # session that's actually there (slot 2) instead of creating a new slot 1.
  # Bounded scan (free is a non-breaking fallback now, so `while true` would
  # spin past the highest slot forever); 20 matches the cap `t paste`/`t kill` use.
  if [[ -z "$slot" ]]; then
    local n=1 free=
    while (( n <= 20 )); do
      local sname="dev-${repo}-${n}"
      if ! tmux has-session -t "$sname" 2>/dev/null; then
        [[ -z $free ]] && free=$n                                  # first free gap → fallback
      elif ! tmux list-clients -t "$sname" 2>/dev/null | grep -q .; then
        slot=$n; break                                            # existing + unattached → reattach (wins)
      fi
      (( n++ ))
    done
    if [[ -z $slot ]]; then
      if [[ -n $free ]]; then
        slot=$free                                                 # nothing to reattach → first free gap
      else
        # all 1..20 live → keep scanning unbounded, still preferring an
        # existing-but-unattached session above 20 over a fresh slot.
        while tmux has-session -t "dev-${repo}-${n}" 2>/dev/null; do
          if ! tmux list-clients -t "dev-${repo}-${n}" 2>/dev/null | grep -q .; then
            slot=$n; break
          fi
          (( n++ ))
        done
        [[ -z $slot ]] && slot=$n                                  # nothing to reattach → next never-used slot
      fi
    fi
  fi

  local session="dev-${repo}-${slot}"

  local logdir="$HOME/.tmux-logs"
  local logfile="$logdir/${session}.log"
  mkdir -p "$logdir"

  if tmux has-session -t "$session" 2>/dev/null; then
    echo "Reattaching $session"
    # resume logging if it stopped (e.g. after server restart)
    tmux pipe-pane -t "$session" -o "cat >> $logfile"
    tmux attach-session -t "$session"
  else
    echo "Starting $session in $dir (logging to $logfile)"
    _dev_new_session "$session" "$dir" "$branch"
    tmux attach-session -t "$session"
  fi
}

# _dev_remote_resolve <repo> <slot> — resolve a live REMOTE dev slot to one
# "<host>\t<repo>\t<slot>" line on stdout (host AUTO-INFERRED). Scans every
# $REMOTE_HOSTS host (via _dev_rows_all, minus local) for the live candidates:
# `<repo> <slot>` → just that slot; `<repo>` (no slot) → every slot of that repo;
# empty → every remote slot. One candidate → it; several → **fzf-pick** (host/slot +
# summary; needs a TTY+fzf, else it lists them and returns 1); none → return 1 with a
# `dev ls -r` hint. The chosen slot name "<repo>-<num>" is split on the LAST dash
# (repos like `dotfiles` have none) so the repo+slot come back fully resolved. Shared
# by `t open` (explicit -r and the auto-detect attach) and `dev -r kill`. Diagnostics
# → stderr (stdout is captured).
_dev_remote_resolve() {
  local repo="$1" slot="$2"
  # _dev_rows_all columns: host(1) sid(2) cwd(3) slot(4) state(5) context(6) summary(7)
  local rows; rows=$(_dev_rows_all 2>/dev/null | awk -F'\t' '$1 != "local"')
  [[ -n $rows ]] || { echo "dev: no live dev sessions on any remote host (\`dev ls -r\`)." >&2; return 1; }
  # Match on the repo DIRECTORY (field 3 = session cwd), NOT the alias-derived
  # slot label (field 4): the same repo dir can carry different DEV_REPOS aliases
  # on different machines — a slot is `dev-dot-2` on mini but `dev-dotfiles-2`
  # here, both keying ~/code/dotfiles — so an alias-string compare silently MISSES
  # a slot that is genuinely live remotely, and `t open 2` then mints a duplicate
  # LOCAL session (the bug this fixes). Repos live at the same ~/code/<name> path
  # on every host, so the dir is the stable cross-host key; the slot NUMBER is
  # field 4's trailing -N. Fall back to the legacy alias-string match only when
  # the local alias has no dir (not a DEV_REPOS key here). The dir match also
  # excludes FOREGROUND rows (labels like `<repo>:<id>` / `<repo>:fg`, marked by
  # `:`) — they share the cwd but are bound to their terminal, not a tmux slot;
  # admitting them would make the trailing-dash split below yield a bogus
  # repo/slot pair (`dot:a4aa5f6a` has no `-`) and `_dev_remote_attach`/
  # `_dev_remote_delegate` would ssh with a malformed `t` command.
  local dir="${DEV_REPOS[$repo]:-}"
  local match
  if [[ -z $repo ]]; then
    match=$rows                                                  # bare → all remote
  elif [[ -n $dir && -n $slot ]]; then
    match=$(print -r -- "$rows" | awk -F'\t' -v d="$dir" -v s="$slot" '$3==d && $4 !~ /:/ && $4 ~ ("-" s "$")')
  elif [[ -n $dir ]]; then
    match=$(print -r -- "$rows" | awk -F'\t' -v d="$dir" '$3==d && $4 !~ /:/')
  elif [[ -n $slot ]]; then
    match=$(print -r -- "$rows" | awk -F'\t' -v w="${repo}-${slot}" '$4==w')
  else
    match=$(print -r -- "$rows" | awk -F'\t' -v w="${repo}-" 'index($4,w)==1')
  fi
  local n; n=$(print -r -- "$match" | grep -c .)
  (( n )) || { echo "dev: no live '$repo${slot:+ $slot}' session on any remote host (\`dev ls -r\` to see what's live where)." >&2; return 1; }

  local host hostslot
  if (( n == 1 )); then
    host=${match%%$'\t'*}
    hostslot=$(print -r -- "$match" | awk -F'\t' '{print $4}')
  elif [[ -t 1 ]] && command -v fzf >/dev/null 2>&1; then
    local picked
    picked=$(print -r -- "$match" | awk -F'\t' '{printf "%s\t%s\t%s/%-12s %s\n", $1, $4, $1, $4, $7}' \
          | fzf --delimiter=$'\t' --with-nth=3 --no-hscroll \
                --prompt="dev -r ${repo:-pick} > " --height=40% --reverse) || return 1
    [[ -n $picked ]] || return 1
    host=${picked%%$'\t'*}
    hostslot=${${picked#*$'\t'}%%$'\t'*}
  else
    echo "dev: '$repo${slot:+ $slot}' is live in more than one place (install fzf or name a slot):" >&2
    print -r -- "$match" | awk -F'\t' '{printf "  %s  %s  %s\n", $1, $4, $7}' >&2
    return 1
  fi
  printf '%s\t%s\t%s\n' "$host" "${hostslot%-*}" "${hostslot##*-}"
}

# _term_title <text> — set the terminal/tab title via an OSC escape. When Terminal.app
# honors it, it STOPS auto-titling from the foreground process's argv — which is why
# a remote attach otherwise reads as the raw `ssh -t mini zsh -lic dev\ dotfiles\ 2`
# (nothing set a title, so Terminal fell back to the ssh command line). Empty <text>
# clears it so process tracking resumes. No-op when stdout is not a tty (cmd | cat).
# CAVEAT: a Terminal profile (or recent macOS) that composes the title from "active
# process name AND arguments" appends the ssh argv REGARDLESS of this OSC. The robust
# defense is therefore the ssh-call quoting itself: every remote `ssh -t … zsh -lic
# ${(qq)rcmd}` uses (qq) (single-quote), NOT (q) (backslash) — so even when the argv
# leaks into the title it reads `zsh -lic 't open ff 1'`, not the ugly `t\ open\ ff\ 1`.
_term_title() { [[ -t 1 ]] && printf '\e]0;%s\a' "$1" }

# _term_reset_mouse — disable every terminal mouse-tracking + bracketed-paste mode.
# WHY: a remote attach (`t open`/`t beam`/`on`, all `ssh -t … zsh -lic`) runs a TUI
# (tmux/claude) that turns mouse reporting ON. When that session dies UNCLEANLY — broken
# pipe / "Connection reset by peer" on a sleeping/dropped host — the remote tmux never
# sends its mouse-mode reset back, so the LOCAL Terminal is left in mouse-reporting mode:
# the scroll wheel then emits SGR mouse events that print as literal `35;..M` garbage and
# scrollback is dead until you `reset`. Registered as a precmd hook below so EVERY return
# to the prompt clears it — this is the only catch-all that also covers `on`, which
# `os.execvp`s into ssh in bin/t and so cannot clean up in-process (same reason its tab
# title is refreshed by precmd, not cleared in bin/t). At the zsh prompt mouse mode is
# always meant to be off (TUIs that want it enable+disable it themselves), so an
# unconditional reset here is safe; the sequences are invisible (no cursor move/output).
# Modes: 1000 press · 1002 drag · 1003 any-motion (the spammer) · 1005/1006/1015 encodings · 2004 bracketed paste.
_term_reset_mouse() { [[ -t 1 ]] && printf '\e[?1000l\e[?1002l\e[?1003l\e[?1005l\e[?1006l\e[?1015l\e[?2004l' }
add-zsh-hook precmd _term_reset_mouse

# _dev_local_slot_live <repo> <slot> — true if a dev-<repo>-<slot> tmux session is
# live on THIS machine (any dev-<repo>-* when <slot> is empty). Cheap (tmux only, no
# ssh): the gate that lets `t open` prefer a local slot before paying for a remote
# probe. The new/fg keywords are never "a live slot" (they mean fresh / foreground).
_dev_local_slot_live() {
  local repo="$1" slot="$2"
  [[ -n $repo ]] || return 1
  if [[ -n $slot && $slot != new && $slot != fg ]]; then
    tmux has-session -t "dev-${repo}-${slot}" 2>/dev/null
  else
    tmux list-sessions -F '#{session_name}' 2>/dev/null | grep -q "^dev-${repo}-"
  fi
}

# _dev_default_host — the host `t open -r --new` starts a fresh remote session on when
# no --host is given. REMOTE_HOSTS is an UNORDERED assoc array, so "first remote host"
# is made deterministic as the alphabetically-first key (with one or two machines, the
# common case, there is nothing to disambiguate). Override per-invocation with --host.
# Returns 1 (and prints nothing) when no remote hosts are configured.
_dev_default_host() {
  (( ${#REMOTE_HOSTS} )) || return 1
  local -a hk; hk=(${(ok)REMOTE_HOSTS})
  print -r -- "$hk[1]"
}

# _dev_remote <repo> <slot> <fg> — `dev -r [repo [slot]]`: resolve a live REMOTE slot
# (host auto-inferred, _dev_remote_resolve) then ATTACH IN PLACE on its host. The
# explicit `-r` entry — `t open` also auto-detects a remote slot when none is live
# locally and calls _dev_remote_attach directly with an already-resolved row (no second
# scan). Moving a session between machines is `tbeam`, not this. <fg> forwards `-f`.
_dev_remote() {
  local repo="$1" slot="$2" fg="$3"
  # Repo-aware: a lone slot-shaped arg (`t open -r 4`) means slot 4 of the repo $PWD is
  # in — mirroring the local `t open 4` and `t kill -r 4` (see _dev_remote_kill). Without
  # this the bare `4` is mis-read as a remote repo alias and never matches. Only triggers
  # for a pure-digit positional that is not itself a DEV_REPOS key, and only when cwd maps
  # to a repo; a truly bare `t open -r` (no positional) stays the documented all-remote
  # picker. Inference failure leaves $repo as the raw digit so the resolver still errors.
  if [[ -z ${DEV_REPOS[$repo]:-} && -z $slot && $repo == <-> ]]; then
    local inferred; inferred=$(_t_infer_repo "$repo") && { slot=$repo; repo=$inferred; }
  fi
  local res
  if ! res=$(_dev_remote_resolve "$repo" "$slot"); then
    # Nothing live to attach. `-r` is attach-only; starting one is `-r --new`.
    local dh; dh=$(_dev_default_host) \
      && echo "  to START one remotely: t open ${repo:-<repo>}${slot:+ $slot} -r --new   (on $dh; --host <h> to choose)" >&2
    return 1
  fi
  _dev_remote_attach "$res" "$fg"
}

# _dev_remote_attach <res> <fg> — ATTACH IN PLACE on an already-resolved remote slot
# ("<host>\t<repo>\t<slot>", from _dev_remote_resolve): ssh -t + remote `t open`, so the
# session stays put on its host (the old `tgo`). `zsh -lic` for the usual reason
# (Homebrew/tmux login PATH, claude interactive PATH). <fg> forwards `-f`. Shared by the
# explicit `_dev_remote` entry and `t open`'s auto-detect path (slot resolved once).
_dev_remote_attach() {
  local res="$1" fg="$2"
  local host=${res%%$'\t'*} prepo=${${res#*$'\t'}%%$'\t'*} pslot=${res##*$'\t'}
  local target="${REMOTE_HOSTS[$host]:-$host}"

  if [[ ! -t 1 ]]; then
    echo "dev: attaching to a remote session needs a terminal." >&2
    echo "  From a terminal: t open $prepo $pslot   (it attaches on $host; \`t beam $prepo $pslot --from $host\` pulls it here)" >&2
    return 1
  fi
  echo "→ Attaching $host:dev-${prepo}-${pslot} (stays on $host; Ctrl-b d to detach)"
  local rcmd="t open ${(q)prepo} ${(q)pslot}"
  [[ -n $fg ]] && rcmd+=" --fg"
  _term_title "$host: $prepo $pslot"
  local rc=0
  ssh -t "$target" "zsh -lic ${(qq)rcmd}" || rc=$?
  _term_title ""
  return $rc
}

# _dev_remote_open <host> [open-args…] — start/attach a session on a SPECIFIC host
# (the `t open --host <h>` path). Unlike the `-r`/auto-detect attach — which only
# reaches an ALREADY-LIVE remote slot (host inferred via _dev_remote_resolve) — this
# forces <host>, so it can start a FRESH session there: `t open ff --host mini` opens a
# new mini session, `t open ff 3 --host mini` its slot 3. ssh -t + remote `t open`
# (zsh -lic for the Homebrew-tmux / interactive-claude PATH split); the session stays
# on <host> (Ctrl-b d to detach). The repo must exist at the same ~/code path there.
_dev_remote_open() {
  local host="$1"; shift
  local target="${REMOTE_HOSTS[$host]:-$host}"
  if [[ ! -t 1 ]]; then
    echo "t open --host: starting a session on $host needs a terminal." >&2
    return 1
  fi
  local rcmd="t open"; local a
  for a in "$@"; do rcmd+=" ${(q)a}"; done
  echo "→ $host: $rcmd (stays on $host; Ctrl-b d to detach)"
  _term_title "$host: open ${(j: :)@}"
  local rc=0
  ssh -t "$target" "zsh -lic ${(qq)rcmd}" || rc=$?
  _term_title ""
  return $rc
}

# _dev_remote_delegate <repo> <slot> <verb> [extra…] — remote-aware shim for the
# per-slot read verbs (plan/read/…). If dev-<repo>-<slot> is NOT live locally but IS
# live on a $REMOTE_HOSTS host, run `t <verb> <repo> <slot> [extra]` there over ssh -t
# and return 0 (handled); else return 1 so the caller falls back to its local path.
# Needed because a beamed/remote slot's artifacts are host-local — the plan .md lives
# in ~/.claude/plans and the tmux log in ~/.tmux-logs on the slot's host, neither of
# which csync syncs — so the verb must run THERE. Host auto-inferred (_dev_remote_
# resolve); no TTY → print a hint and still return 0 (do not fall through to a local
# "No such session"). Mirrors _t_dev's auto-detect attach, but for non-attach verbs.
_dev_remote_delegate() {
  local repo="$1" slot="$2" verb="$3"; shift 3
  (( ${#REMOTE_HOSTS} )) || return 1
  [[ -n $repo ]] || return 1
  # An empty <slot> means the caller already failed to find its specific local target
  # (bare `t read` whose default-slot log is absent, bare `t pop` with no cwd-matched
  # session) and is asking us to probe ANY remote slot of <repo>. Don't gate on a
  # sibling local slot in that case — _dev_local_slot_live with an empty slot matches
  # any `dev-<repo>-*`, which would block delegation whenever an unrelated slot of the
  # same repo happens to be live here.
  [[ -n $slot ]] && _dev_local_slot_live "$repo" "$slot" && return 1
  local res; res=$(_dev_remote_resolve "$repo" "$slot") || return 1
  [[ -n $res ]] || return 1
  local host=${res%%$'\t'*} prepo=${${res#*$'\t'}%%$'\t'*} pslot=${res##*$'\t'}
  local target="${REMOTE_HOSTS[$host]:-$host}"
  if [[ ! -t 1 ]]; then
    echo "t $verb: dev-${prepo}-${pslot} is live on $host — run it from a terminal (or \`t beam $prepo $pslot --from $host\` to pull it here)." >&2
    return 0
  fi
  local rcmd="t $verb ${(q)prepo} ${(q)pslot}"; local a
  for a in "$@"; do rcmd+=" ${(q)a}"; done
  echo "→ $host:dev-${prepo}-${pslot}" >&2
  _term_title "$host: $verb $prepo $pslot"
  # Once the slot has resolved on a remote host, treat the delegation as handled
  # regardless of ssh's exit — ssh/the remote verb prints its own errors, and we
  # must not let the caller fall back to a misleading "No such session" message.
  ssh -t "$target" "zsh -lic ${(qq)rcmd}"
  _term_title ""
  return 0
}

# _dev_session_remote_fallback <session-name> <verb> [extra…] — the ONE shared
# "not live HERE, maybe live on another host" check that every per-slot verb routes
# its local miss through, so remote detection is uniform (pop/plan/kill all call this;
# t open uses the attach-flavoured _dev_remote_attach; t read is bin-native and calls
# _try_remote_delegate). Splits repo/slot off the dev-<repo>-<slot> name on the LAST
# dash (so multi-dash repos like dotfiles resolve) and hands them to
# _dev_remote_delegate. Returns 0 = handled on the slot's host over ssh -t (caller
# should `return`); 1 = no remote slot (caller falls through to its local error).
_dev_session_remote_fallback() {
  local session="$1" verb="$2"; shift 2
  local repo=${${session#dev-}%-*} slot=${session##*-}
  _dev_remote_delegate "$repo" "$slot" "$verb" "$@"
}

# _dev_remote_kill <repo> <slot> <force> — `dev -r kill <repo> [slot]`: resolve a live
# REMOTE slot (host auto-inferred / fzf-picked, _dev_remote_resolve) and tear it down
# ON that host by running `dev kill <repo> <slot>` there over ssh -t (so its confirm
# prompt — unless <force>/-y — works through the TTY). The mirror of a local dev kill.
_dev_remote_kill() {
  local repo="$1" slot="$2" force="$3"
  # Repo-aware: `t kill -r 4` / `t kill -r all` mean the repo $PWD is in, mirroring
  # the local `t kill` slot-only forms (see _dev_kill / _t_infer_repo). Without this
  # the raw `4`/`all` would be resolved as a remote repo alias.
  if [[ -z ${DEV_REPOS[$repo]:-} && -z $slot && ( $repo == <-> || $repo == all ) ]]; then
    slot=$repo; repo=$(_t_infer_repo "$slot")
  elif [[ -z $repo ]]; then
    repo=$(_t_infer_repo)
  fi
  # `all` can't be resolved as a single row (slot names are `<repo>-<N>`, never
  # `<repo>-all`); enumerate the remote hosts with any live dev-<repo>-* slot and
  # delegate `t kill <repo> all` to each — the remote _dev_kill iterates its own.
  if [[ $slot == all ]]; then
    [[ -n $repo ]] || { echo "dev: kill --remote all needs a repo (none inferred from \$PWD)." >&2; return 1; }
    # Discover hosts by repo DIRECTORY (field 3), not the alias-derived slot label
    # (field 4) — same reason _dev_remote_resolve does: a slot is `dev-dot-2` on
    # mini but `dev-dotfiles-2` here, both keying ~/code/dotfiles, so an alias
    # compare silently misses cross-host slots. Exclude foreground rows (`:` in
    # field 4). For each match keep the REMOTE's alias (slot field's prefix, last
    # dash split) so the delegated `t kill` keys off a name that exists there —
    # passing our local alias would have remote `_dev_kill` grep for sessions it
    # does not have. (Remote `_dev_kill` already unions sibling aliases for `all`
    # — see lines 1162–1175 — so one alias per host is enough to reach every slot
    # in that tree.) Fall back to the alias-string match when the local alias has
    # no DEV_REPOS dir.
    local dir="${DEV_REPOS[$repo]:-}"
    local pairs
    if [[ -n $dir ]]; then
      pairs=$(_dev_rows_all 2>/dev/null \
        | awk -F'\t' -v d="$dir" '$1 != "local" && $3==d && $4 !~ /:/ {
            a=$4; sub(/-[0-9]+$/, "", a);
            print $1 "\t" a
          }' | awk -F'\t' '!seen[$1]++')
    else
      pairs=$(_dev_rows_all 2>/dev/null \
        | awk -F'\t' -v r="$repo" -v w="${repo}-" '$1 != "local" && index($4,w)==1 {print $1 "\t" r}' \
        | awk -F'\t' '!seen[$1]++')
    fi
    [[ -n $pairs ]] || { echo "dev: no live '$repo' sessions on any remote host (\`dev ls -r\`)." >&2; return 1; }
    local pair h ralias rtarget rcmd rc=0
    for pair in ${(f)pairs}; do
      h=${pair%%$'\t'*}
      ralias=${pair#*$'\t'}
      rtarget="${REMOTE_HOSTS[$h]:-$h}"
      rcmd="t kill ${(q)ralias} all"
      [[ -n $force ]] && rcmd+=" -y"
      echo "→ Killing all dev-${ralias}-* on $h"
      _term_title "$h: kill $ralias all"
      # rc is a sticky failure flag — set on any host failure and never reset on
      # a later success — so the final exit is a clean boolean over the whole
      # fan-out (any host failed → non-zero). Was rc=$?: that captured the last
      # failure's exact code but never cleared on success, which made the value
      # arbitrary across iterations.
      ssh -t "$rtarget" "zsh -lic ${(qq)rcmd}" || rc=1
    done
    _term_title ""
    return $rc
  fi
  local res; res=$(_dev_remote_resolve "$repo" "$slot") || return 1
  local host=${res%%$'\t'*} prepo=${${res#*$'\t'}%%$'\t'*} pslot=${res##*$'\t'}
  local target="${REMOTE_HOSTS[$host]:-$host}"
  echo "→ Killing $host:dev-${prepo}-${pslot}"
  local rcmd="t kill ${(q)prepo} ${(q)pslot}"
  [[ -n $force ]] && rcmd+=" -y"
  _term_title "$host: kill $prepo $pslot"
  local rc=0
  ssh -t "$target" "zsh -lic ${(qq)rcmd}" || rc=$?
  _term_title ""
  return $rc
}

# _dev_pull <host> <target> <repo> <slot> <fg> — pull a remote dev slot's session
# onto THIS machine and land it (the engine behind `tbeam <repo> <slot> --from <host>` —
# the RECEIVE direction, mirror of the default send). Finds the live dev-<repo>-<slot> on <host> via its
# _dev_session_rows (sid + cwd; slot omitted → the repo's first live slot), rsync's
# the transcript back, then stops the origin copy there (the MOVE — _tbeam_kill_owner,
# so exactly one live claude owns the id) and resumes it locally: a dev-<repo>-<slot>
# by default, or this terminal with <fg>. Reuses a local slot already running that id.
_dev_pull() {
  local host="$1" target="$2" repo="$3" slot="$4" fg="$5"
  command -v rsync >/dev/null 2>&1 || { echo "dev: rsync not found" >&2; return 1; }
  local rows; rows=$(ssh "$target" "zsh -lic _dev_session_rows" 2>/dev/null)
  [[ -n $rows ]] || { echo "dev: no live dev sessions on $host (or its dotfiles are stale — run \`dots\` there)" >&2; return 1; }
  # Match the slot column (field 3 = "<repo>-<num>"): exact when slot given, else the
  # repo's first live slot. Empty fields can't collapse — _dev_session_rows sentinels.
  local row
  if [[ -n $slot ]]; then
    row=$(print -r -- "$rows" | awk -F'\t' -v w="${repo}-${slot}" '$3==w {print; exit}')
    [[ -n $row ]] || { echo "dev: no live slot dev-${repo}-${slot} on $host" >&2; return 1; }
  else
    row=$(print -r -- "$rows" | awk -F'\t' -v w="${repo}-" 'index($3,w)==1 {print; exit}')
    [[ -n $row ]] || { echo "dev: no live slot for '$repo' on $host" >&2; return 1; }
  fi
  local sid=${row%%$'\t'*}
  local cwd=${${row#*$'\t'}%%$'\t'*}
  local fslot=${${${row#*$'\t'}#*$'\t'}%%$'\t'*}
  [[ -n $sid && $sid != - ]] || { echo "dev: $host/$fslot has no active conversation to pull" >&2; return 1; }
  [[ -d $cwd ]] || { echo "dev: $cwd doesn't exist here — clone/sync the repo first." >&2; return 1; }

  echo "⟳ Pulling ${sid[1,8]}… ($cwd) from $host → here"
  _tbeam_pull_transcript "$cwd" "$target" || return 1

  # It's a MOVE: stop the origin copy on <host> (the dev slot stamped with this id)
  # so two live owners don't diverge — the same invariant tpush/tpop/tbeam protect.
  local killed
  killed=$(ssh "$target" "TB_SID=${(q)sid} zsh -lic _tbeam_kill_owner" 2>/dev/null | tail -1)
  [[ $killed == dev-* ]] && echo "✂ Stopped the live copy on $host ($killed)"

  # If this exact id is already running in a local dev slot, reuse it (one owner).
  local existing s
  for s in ${(f)"$(tmux list-sessions -F '#{session_name}' 2>/dev/null | grep '^dev-')"}; do
    if [[ "$(tmux show-environment -t "$s" CLAUDE_RESUME_ID 2>/dev/null | cut -d= -f2)" == "$sid" ]]; then
      existing="$s"; break
    fi
  done

  if [[ -n $fg ]]; then
    [[ -n $existing ]] && { echo "dev: already running locally in $existing — attaching."; tmux attach-session -t "$existing"; return; }
    echo "✓ Resuming ${sid[1,8]}… here"
    ( cd "$cwd" && exec claude -r "$sid" )
    return
  fi

  local lrepo lslot session
  if [[ -n $existing ]]; then
    session="$existing"
  else
    read -r lrepo lslot < <(_dev_slot_for_cwd "$cwd")
    [[ -n $lrepo && -n $lslot ]] || { echo "dev: couldn't map $cwd to a dev slot" >&2; return 1; }
    session="dev-${lrepo}-${lslot}"
    _dev_resume_session "$session" "$cwd" "$sid"
  fi
  echo "✓ Landed here as $session"
  if [[ -t 1 && -z $CLAUDE_CODE_SESSION_ID ]]; then
    tmux attach-session -t "$session"
  else
    echo "  Attach: tmux attach -t $session"
  fi
}

# (tread removed — `t read` is reimplemented natively in bin/t.)

# tplan — render the plan a Claude session wrote
#
# Usage: tplan [repo|session] [slot] | --all
#
# Commands:
#   tplan            inside Claude: THIS session; else fzf-pick scoped to $PWD
#   tplan api 1      the session running in tmux dev-api-1
#   tplan dev-web-2  that tmux session by full name
#   tplan --all      fzf-pick across every project
#
# Resolves a session like the dev/tpop family, then renders the last plan it
# saved (an absolute ~/.claude/plans/<slug>.md path in the transcript). glow
# word-wraps for narrow mobile terminals (Termius), falling back to less.
_t_plan() {
  [[ "$1" == -h || "$1" == --help ]] && { _help_for _t_plan; return 0; }
  local sid
  if [[ "$1" == "--all" || "$1" == "-a" ]]; then
    local row
    row=$(_claude_sessions_fzf "") || return 1     # every project
    [[ -n $row ]] || return 1
    sid=${row%%$'\t'*}
  elif [[ -n "$1" ]]; then
    # repo/slot or full session name → tmux session → stashed CLAUDE_RESUME_ID
    # (stamped by _dev_new_session/_dev_resume_session and, for every other launch
    # path, by the claude-stamp-tmux SessionStart hook), falling back to the dir's
    # newest transcript only for pre-hook sessions. That fallback is ambiguous when
    # several slots share one repo dir (dev-api-1..5 all root at the same project,
    # so one ~/.claude/projects/<enc>/) — it returns whichever sibling wrote last,
    # not the slot you asked for. The hook is what makes this reliable now.
    # Mirrors tpop's resolution so `tplan api 1` lines up with `tpop api 1`.
    local session
    if [[ "$1" == dev-* ]]; then
      session="$1"
    else
      local repo="$1" slot="$2"
      # Repo-aware: a lone numeric arg is a SLOT of the repo $PWD is in
      # (`t plan 4` ≡ `t plan <cwd-repo> 4` — see _t_infer_repo).
      if [[ "$repo" == <-> && -z "$slot" ]]; then
        slot=$repo
        repo=$(_t_infer_repo "$slot") || { echo "Not inside a DEV_REPOS dir — name the repo (t plan <repo> $slot)." >&2; return 1; }
      fi
      if [[ -z "$slot" ]]; then                    # first existing slot for repo
        local n=1
        while (( n <= 20 )); do
          tmux has-session -t "dev-${repo}-${n}" 2>/dev/null && { slot=$n; break; }
          (( n++ ))
        done
      fi
      session="dev-${repo}-${slot}"
    fi
    if ! tmux has-session -t "$session" 2>/dev/null; then
      # Not live here — maybe beamed to / started on another host. Delegate the whole
      # `t plan` over there (shared _dev_session_remote_fallback): the plan .md lives in
      # that host's ~/.claude/plans (csync syncs transcripts, not plan files), so it
      # must be rendered on the slot's host.
      _dev_session_remote_fallback "$session" plan && return
      echo "No such session: $session" >&2; return 1
    fi
    sid=$(tmux show-environment -t "$session" CLAUDE_RESUME_ID 2>/dev/null | cut -d= -f2)
    if [[ -z $sid ]]; then
      local dir; dir=$(tmux display-message -p -t "$session" '#{session_path}')
      local -a tx=( "$HOME/.claude/projects/${dir//\//-}"/*.jsonl(Nom[1]) )
      sid=${${tx[1]:t}%.jsonl}
    fi
    [[ -n $sid ]] || { echo "Couldn't find a session id for $session." >&2; return 1; }
  elif [[ -n $CLAUDE_CODE_SESSION_ID ]]; then
    sid=$CLAUDE_CODE_SESSION_ID                     # current-session mode
  else
    local row
    row=$(_claude_sessions_fzf "$PWD") || return 1  # scoped to this dir
    [[ -n $row ]] || return 1
    sid=${row%%$'\t'*}
  fi

  local transcript=("$HOME"/.claude/projects/*/"$sid".jsonl(N))
  [[ -n $transcript ]] || { echo "No transcript found for session $sid" >&2; return 1; }

  # Last plan path referenced in the transcript — the plan as finally written.
  local plan
  plan=$(grep -ho "$HOME/.claude/plans/[^\"]*\.md" "$transcript[1]" 2>/dev/null | tail -1)
  [[ -n $plan && -f $plan ]] || { echo "This session has no saved plan." >&2; return 1; }

  if command -v glow &>/dev/null; then
    glow -p "$plan"
  else
    less "$plan"
  fi
}

# tfind — find the Claude session working on something you describe
#
# Usage: tfind [-k] <query…>
#
# Options:
#   -k, --keyword   skip Sonnet; fast offline keyword ranking (works with no CLI)
#
# Semantic search, not grep: a keyword pass over titles + your prompts gathers
# candidates (padded with recent sessions so divergent phrasing still gets a shot),
# then Sonnet ranks the genuinely-relevant ones and your fzf pick foreground-resumes
# (the same landing as tpop). Falls back to keyword order if the claude CLI is
# unreachable. Searches every project — use dev/tpush/tpop when you know the slot.
_t_find() {
  [[ "$1" == -h || "$1" == --help ]] && { _help_for _t_find; return 0; }
  local keyword=
  [[ "$1" == "-k" || "$1" == "--keyword" ]] && { keyword=1; shift; }
  [[ -n "$1" ]] || { echo "Usage: tfind [-k] <words describing the session>"; return 1; }
  if [[ -n $TMUX && -n $CLAUDE_CODE_SESSION_ID ]]; then
    echo "Run tfind from a plain shell — it resumes a session in the foreground." >&2
    return 1
  fi
  local row
  if [[ -n $keyword ]]; then
    row=$(_claude_sessions_fzf "" "$*") || return 1       # offline keyword rank
  else
    row=$(_claude_sessions_semantic "$*") || return 1     # Sonnet-reranked
  fi
  [[ -n $row ]] || return 1
  local sid cwd
  sid=${row%%$'\t'*}
  cwd=${${row#*$'\t'}%%$'\t'*}
  [[ -d $cwd ]] || { echo "Session's directory no longer exists: $cwd"; return 1; }
  echo "Resuming claude -r ${sid[1,8]}… in $cwd"
  cd "$cwd" && claude -r "$sid"
}

# _claude_sessions_semantic <query> — fzf-pick a session by what it's ABOUT.
# Echoes the chosen "<sid>\t<cwd>\t<display>" row (same contract as
# _claude_sessions_fzf, so tfind handles either). Pipeline: a keyword pass builds
# a recall-oriented candidate pool, Sonnet (via `claude -p`, run as a subprocess
# so it bypasses the zsh claude wrapper) reranks them with reasons, and the
# ranked rows feed fzf. Any failure — no CLI, timeout, unparseable reply — falls
# back to keyword order so the picker always populates.
_claude_sessions_semantic() {
  command -v fzf     >/dev/null 2>&1 || { echo "fzf not installed (brew install fzf)" >&2; return 1; }
  command -v python3 >/dev/null 2>&1 || { echo "python3 not found" >&2; return 1; }
  local projects="$HOME/.claude/projects"
  [[ -d $projects ]] || { echo "No Claude sessions at $projects" >&2; return 1; }
  local query="$1"
  echo "↻ Asking Sonnet which session matches \"$query\"…  (-k to skip)" >&2

  python3 - "$projects" "$query" <<'PY' | fzf --delimiter=$'\t' --with-nth=3 --no-hscroll \
        --prompt="resume claude (sonnet: $query) > " --height=60% --reverse
import json, os, sys, glob, datetime, subprocess, re
root, query = sys.argv[1], sys.argv[2]
STOP = {'of','the','a','an','to','on','in','for','and','is','it','with','at'}
qterms = [t for t in query.lower().split() if t not in STOP]

# ── Scan: title + your first few prompts per session (the "about" signal). ──
sessions = []
for f in glob.glob(os.path.join(root, '*', '*.jsonl')):
    sid = os.path.basename(f)[:-6]
    cwd = title = ctitle = None
    prompts = []
    try:
        for line in open(f, errors='ignore'):
            if cwd is None and '"cwd"' in line:
                try: cwd = json.loads(line).get('cwd')
                except ValueError: pass
            if '"custom-title"' in line:
                try:
                    t = json.loads(line).get('customTitle')
                    if t: ctitle = t
                except ValueError: pass
            if '"ai-title"' in line:
                try:
                    t = json.loads(line).get('aiTitle')
                    if t: title = t
                except ValueError: pass
            if len(prompts) < 6 and '"type":"user"' in line:
                try:
                    c = json.loads(line).get('message', {}).get('content')
                    txt = c if isinstance(c, str) else (
                        ' '.join(x.get('text', '') for x in c if isinstance(x, dict))
                        if isinstance(c, list) else '')
                    txt = txt.strip()
                    if txt and not txt.startswith('<'): prompts.append(txt)
                except ValueError: pass
    except OSError:
        continue
    head = ctitle or title or (prompts[0] if prompts else '') or '(no message)'
    sessions.append({'sid': sid, 'cwd': cwd or '?', 'mtime': os.path.getmtime(f),
                     'head': head, 'prompts': prompts})

# ── Retrieve: keyword pass for recall. Pad thin matches with recent sessions
# so a query whose words diverge from the transcript still reaches the model. ──
def kw(s):
    head = s['head'].lower(); body = ' '.join(s['prompts']).lower()
    return sum(body.count(t) + 4 * head.count(t) for t in qterms)
for s in sessions: s['kw'] = kw(s)
cands = sorted((s for s in sessions if s['kw'] > 0),
               key=lambda s: (s['kw'], s['mtime']), reverse=True)[:30]
if len(cands) < 8:
    have = {s['sid'] for s in cands}
    for s in sorted(sessions, key=lambda s: s['mtime'], reverse=True):
        if s['sid'] not in have: cands.append(s)
        if len(cands) >= 20: break

def emit(rows):                                       # rows: (session, reason)
    for s, why in rows:
        when = datetime.datetime.fromtimestamp(s['mtime']).strftime('%m-%d %H:%M')
        short = os.path.basename(s['cwd']) if s['cwd'] != '?' else '?'
        disp = f"{when}  {short:<16}  {' '.join(s['head'].split())[:60]}"
        if why: disp += f"   ⟵ {why}"
        print(f"{s['sid']}\t{s['cwd']}\t{disp}")

if not cands:
    sys.exit(0)

# ── Rerank: hand Sonnet the candidates' context + the query, get a ranking. ──
def digest(i, s):
    when = datetime.datetime.fromtimestamp(s['mtime']).strftime('%Y-%m-%d')
    ps = ' / '.join(p[:200] for p in s['prompts'][:4])
    return f"[{i}] ({when}, {os.path.basename(s['cwd'])}) {s['head'][:120]}\n    {ps[:600]}"

prompt = (
    f'I am looking for a past coding session — the one working on:\n"{query}"\n\n'
    f'Candidate sessions, each with its title and my opening prompts:\n\n'
    + '\n'.join(digest(i, s) for i, s in enumerate(cands))
    + '\n\nReturn ONLY a JSON array (no prose, no code fence) of the genuinely '
      'relevant sessions, best match first, at most 8, each {"i": <index>, '
      '"why": "<reason in 8 words or fewer>"}. If none fit, return [].')

order = None
try:
    p = subprocess.run(['claude', '-p', '--model', 'sonnet', '--output-format', 'json'],
                       input=prompt, capture_output=True, text=True, timeout=60)
    res = json.loads(p.stdout).get('result', '')
    m = re.search(r'\[.*\]', res, re.S)               # tolerate stray prose/fences
    if m: order = json.loads(m.group(0))
except Exception:
    order = None

if order:                                             # Sonnet's ranking, with reasons
    rows = []
    for o in order:
        try: i = int(o['i'])
        except (KeyError, ValueError, TypeError): continue
        if 0 <= i < len(cands):
            rows.append((cands[i], str(o.get('why', '')).strip()))
    if rows:
        emit(rows); sys.exit(0)
emit([(s, '') for s in cands])                        # fallback: keyword order
PY
}

# _claude_session_rows [cwd] [query] — scan saved Claude transcripts, printing
# one "<session-id>\t<cwd>\t<display>" row per session to stdout (no fzf — see
# _claude_sessions_fzf for the picker). Split out so the rows can be generated on
# a *remote* host over ssh and fzf'd locally (tbeam --from): fzf can't be driven
# interactively through a captured ssh pipe, but a row dump travels fine.
# Newest-first by transcript mtime; the JSONL is parsed once in
# python for each file's real cwd (the `cwd` field, not the lossy folder name),
# its title — preferring a /rename `customTitle`, then the generated `aiTitle`,
# then the first human message. With a <cwd> arg, only sessions
# whose cwd is that dir or below are shown; omit it to list every project.
# With a <query> arg, every session is scored by how well the query terms match
# its title + your prompts; non-matches are dropped and the list is ranked by
# relevance, then recency (this is what `tfind` drives). Returns nonzero on no
# pick / no fzf.
_claude_session_rows() {
  # Diagnostics go to stderr: this function's stdout is captured by the caller's
  # $(...), so a stdout error would be swallowed silently instead of shown.
  command -v python3 >/dev/null 2>&1 || { echo "python3 not found" >&2; return 1; }
  local projects="$HOME/.claude/projects"
  [[ -d $projects ]] || { echo "No Claude sessions at $projects" >&2; return 1; }
  local filter="${1:-}" query="${2:-}"

  python3 - "$projects" "$filter" "$query" <<'PY'
import json, os, sys, glob, datetime
root = sys.argv[1]
filt = sys.argv[2] if len(sys.argv) > 2 else ''
query = sys.argv[3] if len(sys.argv) > 3 else ''
# Query terms, minus a few stopwords so "redesign of portfolio tab" matches on
# the words that carry meaning, not "of". Short tokens like "ui"/"db" survive.
STOP = {'of','the','a','an','to','on','in','for','and','is','it','with','at'}
qterms = [t for t in query.lower().split() if t not in STOP]
# Whole-file scan, but only JSON-parse the lines we need (cheap substring gate
# first) so grabbing the *latest* aiTitle stays fast over hundreds of sessions.
rows = []
for f in glob.glob(os.path.join(root, '*', '*.jsonl')):
    sid = os.path.basename(f)[:-6]
    cwd = msg = title = ctitle = None
    umsgs = []                                          # all your prompts (search mode)
    try:
        for line in open(f, errors='ignore'):
            if cwd is None and '"cwd"' in line:
                try: cwd = json.loads(line).get('cwd')
                except ValueError: pass
            if '"custom-title"' in line:                # set by /rename — wins
                try:
                    t = json.loads(line).get('customTitle')
                    if t: ctitle = t                    # keep the most recent
                except ValueError: pass
            if '"ai-title"' in line:
                try:
                    t = json.loads(line).get('aiTitle')
                    if t: title = t                     # keep the most recent
                except ValueError: pass
            # First user message is enough for the display title; in search mode
            # keep parsing to collect every prompt to match the query against.
            if (msg is None or qterms) and '"type":"user"' in line:
                try:
                    c = json.loads(line).get('message', {}).get('content')
                    txt = c if isinstance(c, str) else (
                        ' '.join(x.get('text', '') for x in c if isinstance(x, dict))
                        if isinstance(c, list) else '')
                    txt = txt.strip()
                    if txt and not txt.startswith('<'):
                        if msg is None: msg = txt
                        if qterms: umsgs.append(txt.lower())
                except ValueError: pass
    except OSError:
        continue
    if filt and not (cwd == filt or (cwd or '').startswith(filt + os.sep)):
        continue
    # ── Relevance scoring (search mode) ──────────────────────────────────────
    # The tunable heart of `tfind`: how much each match counts. A term in the
    # session's headline (its title, or opening prompt if untitled) is weighted
    # 4x a term buried mid-conversation — a session *about* X beats one that
    # merely mentions X in passing. Drop sessions with no match at all.
    score = 0
    if qterms:
        hay  = ' '.join(umsgs)
        head = (ctitle or title or msg or '').lower()
        score = sum(hay.count(t) + 4 * head.count(t) for t in qterms)
        if score == 0:
            continue
    # ─────────────────────────────────────────────────────────────────────────
    mtime = os.path.getmtime(f)
    short = os.path.basename(cwd) if cwd else '?'
    title = ' '.join((ctitle or title or msg or '(no message)').split())[:80]
    rows.append((score, mtime, sid, cwd or '?', short, title))
# Primary key = relevance (0 for every row when not searching, so it collapses
# to pure newest-first); tiebreak = recency.
rows.sort(reverse=True)
for score, mtime, sid, cwd, short, title in rows:
    when = datetime.datetime.fromtimestamp(mtime).strftime('%m-%d %H:%M')
    print(f"{sid}\t{cwd}\t{when}  {short:<18}  {title}")
PY
}

# _claude_sessions_fzf [cwd] [query] — fzf-pick a saved Claude transcript, echoing
# the chosen "<session-id>\t<cwd>\t<display>" row (fzf shows only the display
# column). Thin picker over _claude_session_rows; all the scan/scoring logic lives
# there. Returns nonzero on no pick / no fzf.
_claude_sessions_fzf() {
  command -v fzf >/dev/null 2>&1 || { echo "fzf not installed (brew install fzf)" >&2; return 1; }
  local filter="${1:-}" query="${2:-}"
  local prompt='resume claude (all) > '
  [[ -n $filter ]] && prompt="resume claude (${filter:t}) > "
  [[ -n $query  ]] && prompt="resume claude (search: $query) > "
  _claude_session_rows "$filter" "$query" | fzf --delimiter=$'\t' --with-nth=3 --no-hscroll \
        --prompt="$prompt" --height=60% --reverse
}

# _dev_resume_session <session> <dir> <session-id> — sibling of _dev_new_session:
# create a detached, logged tmux session in <dir>, but RESUME an existing Claude
# conversation (claude -r) rather than starting fresh on $DEV_BRANCH. Same name
# + log path convention so dev/tread/tpaste treat it like any dev session.
_dev_resume_session() {
  local session="$1" dir="$2" sid="$3"
  local logfile="$HOME/.tmux-logs/${session}.log"
  mkdir -p "$HOME/.tmux-logs"
  # No fixed geometry / window-size latest: fit the active client so attaching from
  # a phone doesn't pan a too-wide window (see _dev_new_session for the full why).
  tmux new-session -d -s "$session" -c "$dir"
  tmux set-option -t "$session" window-size latest 2>/dev/null
  tmux pipe-pane -t "$session" -o "cat >> $logfile"
  # Record the resumed id on the session so `tpop` can pull the exact same
  # conversation back to the foreground (it also falls back to the dir's newest
  # transcript, but this is the precise signal when we know it).
  tmux set-environment -t "$session" CLAUDE_RESUME_ID "$sid"
  # `; exit` so quitting Claude tears the session down rather than leaving an idle
  # shell (see _dev_new_session for the full rationale).
  tmux send-keys -t "$session" "claude -r $sid; exit" Enter
}

# _dev_slot_for_cwd <cwd> — map a transcript's working dir to a dev session slot.
# Echo "<repo> <slot>" (space-separated) on success, or nothing on failure.
# This is the glue that makes a resumed session "supported by dev": pick the
# DEV_REPOS key whose path matches <cwd>, then choose a free slot for it.
#
# Mapping rules:
#   • Match: exact path, or a worktree/subdir of a repo (cwd under "$path/").
#     Longest matching path wins, so a nested repo beats its parent.
#   • No match: fall back to a key derived from the dir's basename so ANY repo
#     is resumable. dev/tread only validate DEV_REPOS keys, so tpush prints a
#     raw `tmux attach` hint for these derived keys.
#   • Slot: first dev-<repo>-<n> with no running session (mirrors `dev`).
_dev_slot_for_cwd() {
  local cwd="$1" key match
  # Find the DEV_REPOS key for this cwd. Prefer an exact path match; otherwise
  # accept a subdir/worktree of a repo (cwd under "$path/"). Longest matching
  # path wins so a nested repo beats its parent.
  local best_len=0
  for key in ${(k)DEV_REPOS}; do
    local repodir="${DEV_REPOS[$key]}"
    if [[ "$cwd" == "$repodir" || "$cwd" == "$repodir"/* ]]; then
      (( ${#repodir} > best_len )) && { match="$key"; best_len=${#repodir}; }
    fi
  done
  # No DEV_REPOS match → derive a key from the dir's basename so ANY session is
  # resumable (e.g. ~/code/dotfiles → "dotfiles"). Such a session still appears
  # in `dev list`, but dev/tread validate against DEV_REPOS and won't know
  # the key — so tpush prints a raw `tmux attach` hint for it instead.
  if [[ -z "$match" ]]; then
    match="${cwd:t}"                       # :t = basename
    match="${match//[^A-Za-z0-9_-]/-}"     # sanitise for a tmux session name
  fi
  [[ -n "$match" ]] || return 1

  # Next free slot: first dev-<repo>-<n> with no running session (mirrors `dev`).
  local n=1
  while (( n <= 20 )); do
    tmux has-session -t "dev-${match}-${n}" 2>/dev/null || { print -r -- "$match $n"; return 0; }
    (( n++ ))
  done
  return 1
}

# claude — thin wrapper around the real `claude` CLI that makes tpush's
# "background this conversation" flow seamless. Before launching, it arms a
# one-shot sentinel file (path passed to Claude via CLAUDE_TPUSH_ATTACH). When
# tpush — run as /tpush from inside the session — writes an instruction there,
# the wrapper acts on it the moment you leave Claude (/exit or Ctrl-D):
#   • SPAWN<TAB>session<TAB>cwd<TAB>sid — resume $sid into a fresh detached tmux
#     session, THEN attach. The spawn is deferred to here ON PURPOSE: doing it
#     from inside the live session (as tpush used to) means two claude processes
#     own $sid at once, and with no transcript lock they diverge — the
#     backgrounded copy looks frozen. By the time this runs the foreground has
#     fully exited, so $sid has exactly one owner.
#   • ATTACH<TAB>session — the conversation was already backgrounded; just attach.
# No sentinel written → behaves exactly like plain `claude`. The wrapper has to
# own this: tpush runs in Claude's Bash subprocess, which has no TTY to attach
# and can't exit (let alone outlive) its own parent.
claude() {
  local sentinel="${TMPDIR:-/tmp}/claude-tpush-attach.$$"
  rm -f "$sentinel"
  CLAUDE_TPUSH_ATTACH="$sentinel" command claude "$@"
  local rc=$?
  if [[ -s "$sentinel" ]]; then
    local payload="$(<"$sentinel")"
    rm -f "$sentinel"
    local verb="${payload%%$'\t'*}" rest="${payload#*$'\t'}" target cwd sid
    case "$verb" in
      SPAWN)
        target="${rest%%$'\t'*}"; rest="${rest#*$'\t'}"
        cwd="${rest%%$'\t'*}"; sid="${rest#*$'\t'}"
        tmux has-session -t "$target" 2>/dev/null || _dev_resume_session "$target" "$cwd" "$sid"
        ;;
      ATTACH) target="$rest" ;;
      *)      target="$payload" ;;   # legacy: whole line is a bare session name
    esac
    if [[ -n "$target" ]] && tmux has-session -t "$target" 2>/dev/null; then
      echo "Attaching to backgrounded $target…"
      exec tmux attach -t "$target"
    fi
  fi
  return $rc
}

# _tpush_claude_pid — walk up from this shell to the controlling `claude` process
# and echo its PID (empty on miss). tpush runs inside Claude's Bash-tool shell,
# whose ancestry is …→ claude → login zsh → tmux; the nearest ancestor named
# `claude` is the live foreground session. tpush signals it to exit so the user
# doesn't have to type /exit — quitting hands control back to the claude() wrapper,
# whose post-exit block resumes this session into tmux. Capped walk; stops at init.
_tpush_claude_pid() {
  local pid=$$ comm
  while (( pid > 1 )); do
    comm=$(ps -o comm= -p "$pid" 2>/dev/null) || return 1
    [[ ${comm:t} == claude* ]] && { print -r -- "$pid"; return 0; }
    pid=$(ps -o ppid= -p "$pid" 2>/dev/null) || return 1
    pid=${pid//[[:space:]]/}
    [[ -n $pid ]] || return 1
  done
  return 1
}

# tpush — push a Claude session into a detached background tmux session
#
# Usage: tpush [-p] [-a]
#
# Options:
#   -p, --pick   force the picker even when a current session is detectable
#   -a, --all    picker across every project (default: scoped to $PWD)
#
# Inside Claude (CLAUDE_CODE_SESSION_ID set): grabs THIS session + $PWD — how /tmux
# backgrounds the current chat. From a plain shell: fzf-pick a session. Resumes via
# `claude -r` into a dev-named slot; attach with the printed command. Refuses to
# nest when already in tmux. Inverse of tpop.
_t_push() {
  local pick= all= a
  for a in "$@"; do
    case "$a" in
      -h|--help) _help_for _t_push; return 0 ;;
      -p|--pick) pick=1 ;;
      -a|--all)  pick=1; all=1 ;;   # --all implies the picker, unfiltered
    esac
  done

  if [[ -n $TMUX && -z $pick ]]; then
    echo "Already inside tmux ($(tmux display-message -p '#S')). Nothing to do." >&2
    return 1
  fi

  local sid cwd
  if [[ -n $CLAUDE_CODE_SESSION_ID && -z $pick ]]; then
    sid=$CLAUDE_CODE_SESSION_ID; cwd=$PWD          # current-session mode
  else
    local row filter="$PWD"
    [[ -n $all ]] && filter=""                     # --all: every project
    row=$(_claude_sessions_fzf "$filter") || return 1
    [[ -n $row ]] || return 1
    sid=${row%%$'\t'*}
    cwd=${${row#*$'\t'}%%$'\t'*}
  fi

  [[ -d $cwd ]] || { echo "Session's directory no longer exists: $cwd"; return 1; }

  # Already backgrounded? If this exact conversation is running under any slot,
  # reuse it rather than spawning a duplicate. _dev_resume_session stashes
  # CLAUDE_RESUME_ID on each session, so we match on that.
  local repo slot session existing s
  for s in ${(f)"$(tmux list-sessions -F '#{session_name}' 2>/dev/null | grep '^dev-')"}; do
    if [[ "$(tmux show-environment -t "$s" CLAUDE_RESUME_ID 2>/dev/null | cut -d= -f2)" == "$sid" ]]; then
      existing="$s"; break
    fi
  done

  if [[ -n $existing ]]; then
    session="$existing"
    local rest=${existing#dev-}; slot=${rest##*-}; repo=${rest%-*}
  else
    read -r repo slot < <(_dev_slot_for_cwd "$cwd")
    [[ -n $repo && -n $slot ]] || { echo "Couldn't map $cwd to a dev slot."; return 1; }
    session="dev-${repo}-${slot}"
  fi

  # dev/tread only understand DEV_REPOS keys; for a derived key, point at raw tmux.
  local attach_hint
  if [[ -n "${DEV_REPOS[$repo]}" ]]; then
    attach_hint="Attach: dev $repo $slot    Read log: tread $repo $slot"
  else
    attach_hint="Attach: tmux attach -t $session"
  fi

  # Defer the resume spawn when we're inside Claude and the claude() wrapper is
  # present to do it post-exit. Spawning `claude -r $sid` now — while THIS
  # foreground Claude is still alive on $sid — puts two processes on one
  # transcript with no lock, and they diverge (the backgrounded copy freezes).
  # The wrapper spawns once we've exited and $sid is free; see claude() above.
  local defer=
  [[ -n $CLAUDE_CODE_SESSION_ID && -n $CLAUDE_TPUSH_ATTACH && -z $existing ]] && defer=1

  if [[ -n $existing ]]; then
    echo "This conversation is already backgrounded in $session."
  elif tmux has-session -t "$session" 2>/dev/null; then
    # _dev_slot_for_cwd picks a free slot, so this only trips on a race.
    echo "$session already exists for another session — ${attach_hint#Attach: }"
    return 1
  elif [[ -n $defer ]]; then
    echo "Will resume ${sid[1,8]}… into detached $session ($cwd) on exit."
  else
    _dev_resume_session "$session" "$cwd" "$sid"
    echo "Resumed ${sid[1,8]}… in detached $session ($cwd)"
  fi

  # Land you in the session. Three cases:
  if [[ -z $CLAUDE_CODE_SESSION_ID ]]; then
    # Plain-shell picker mode: we own a real terminal and the picked session
    # isn't live, so spawn (above) + attach straight in. No overlap to worry about.
    echo "$attach_hint"
    tmux attach-session -t "$session"
  elif [[ -n $CLAUDE_TPUSH_ATTACH ]]; then
    # Inside Claude via the claude() wrapper: can't attach (or safely spawn) from
    # this Bash subprocess, so hand the wrapper the intent. ATTACH for an already
    # running copy; SPAWN (session+cwd+sid) so it resumes once we've exited.
    if [[ -n $existing ]]; then
      print -r -- "ATTACH"$'\t'"$session" > "$CLAUDE_TPUSH_ATTACH"
    else
      print -r -- "SPAWN"$'\t'"$session"$'\t'"$cwd"$'\t'"$sid" > "$CLAUDE_TPUSH_ATTACH"
    fi
    # Auto-exit: signal the controlling `claude` to quit so you don't have to type
    # /exit. The sentinel above is already written and closed (the `>` redirection
    # flushes on completion), so the wrapper's post-exit block will read it and
    # resume this session into tmux. SIGTERM exits Claude cleanly (~2s, terminal
    # restored on the wrapper's tmux attach); the transcript is appended live so
    # only the in-flight tpush turn may be lost (cosmetic). Killing BEFORE any
    # spawn preserves the one-live-owner invariant. If the process can't be found,
    # fall back to the manual hint rather than leaving you stuck.
    local cpid; cpid=$(_tpush_claude_pid)
    if [[ -n $cpid ]]; then
      echo "Backgrounding into $session… (exiting this foreground copy now)"
      kill -TERM "$cpid"
    else
      echo "→ Type /exit (or Ctrl-D) and you'll drop into $session automatically."
    fi
  else
    # Inside Claude without the wrapper (older shell): can't defer, so spawn now
    # and warn — exit immediately, two live copies of one session diverge.
    [[ -z $existing ]] && _dev_resume_session "$session" "$cwd" "$sid"
    echo "$attach_hint"
    echo "(Exit this foreground Claude NOW — two live copies of one session diverge.)"
  fi
}

# tpop — pull a tmux'd Claude session back to the foreground
#
# Usage: tpop [repo|session] [slot]
#
# Commands:
#   tpop            the dev session for the current dir
#   tpop api 3      dev-api-3
#   tpop dev-web-1  that session by full name
#
# Kills the tmux session and resumes its conversation here with `claude -r` (the
# inverse of tpush). Run from a plain shell, not inside the session you're popping.
_t_pop() {
  [[ "$1" == -h || "$1" == --help ]] && { _help_for _t_pop; return 0; }
  local session
  if [[ "$1" == dev-* ]]; then
    session="$1"
  elif [[ -n "$1" ]]; then
    local repo="$1" slot="$2"
    # Repo-aware: a lone numeric arg is a SLOT of the repo $PWD is in
    # (`t pop 4` ≡ `t pop <cwd-repo> 4` — see _t_infer_repo).
    if [[ "$repo" == <-> && -z "$slot" ]]; then
      slot=$repo
      repo=$(_t_infer_repo "$slot") || { echo "Not inside a DEV_REPOS dir — name the repo (t pop <repo> $slot)."; return 1; }
    fi
    # Same-dir sibling aliases (see _dev_kill): a `dev-dot-2` answers `t pop
    # dotfiles 2` when `dot` and `dotfiles` both key ~/code/dotfiles. Without this
    # the lookup misses the local slot and _dev_session_remote_fallback can pop
    # it on another host while the local copy keeps running.
    local -a _palias=( "$repo" )
    if [[ -n ${DEV_REPOS[$repo]:-} ]]; then
      local _pdir=${DEV_REPOS[$repo]} _pk
      for _pk in ${(k)DEV_REPOS}; do
        [[ $_pk == $repo || ${DEV_REPOS[$_pk]} != $_pdir ]] && continue
        _palias+=( $_pk )
      done
    fi
    if [[ -z "$slot" ]]; then                      # first existing slot for repo
      local n=1 a
      while (( n <= 20 )); do
        for a in $_palias; do
          tmux has-session -t "dev-${a}-${n}" 2>/dev/null && { repo=$a; slot=$n; break 2; }
        done
        (( n++ ))
      done
    elif (( ${#_palias} > 1 )) && ! tmux has-session -t "dev-${repo}-${slot}" 2>/dev/null; then
      local a
      for a in $_palias; do
        [[ $a == $repo ]] && continue
        tmux has-session -t "dev-${a}-${slot}" 2>/dev/null && { repo=$a; break; }
      done
    fi
    session="dev-${repo}-${slot}"
  else
    # no args — find the dev session rooted in this repo (its dir or below; the
    # repo dir from _dev_cwd_repo_dir so a subdir works too, else $PWD itself)
    local s d scope; scope=$(_dev_cwd_repo_dir); scope=${scope:-$PWD}
    for s in ${(f)"$(tmux list-sessions -F '#{session_name}' 2>/dev/null | grep '^dev-')"}; do
      d=$(tmux display-message -p -t "$s" '#{session_path}')
      [[ $d == $scope || $d == $scope/* ]] && { session="$s"; break; }
    done
    if [[ -z $session ]]; then
      # Nothing live HERE — a slot of this repo may be live on another host. Infer the
      # repo from $PWD and let _dev_remote_delegate resolve/pop it over there (empty
      # slot → it picks the repo's remote slot, fzf-picks if several). Same remote
      # detection the explicit `t pop <repo> <slot>` path gets, now for bare `t pop`.
      local repo; repo=$(_t_infer_repo) && _dev_remote_delegate "$repo" "" pop && return
      echo "No dev session for $scope (here or on any remote host). Pass a repo/slot or session name."; return 1
    fi
  fi

  if ! tmux has-session -t "$session" 2>/dev/null; then
    # Not live locally — the slot may be on a remote host. Delegate the pop to its host
    # over ssh -t (shared _dev_session_remote_fallback): it un-tmuxes THERE and you drive
    # it through the ssh TTY — the same semantics as a local pop (closing ssh ends the
    # foreground claude, exactly like a no-tmux local pop). Falls through to the local
    # error only when the slot is live nowhere.
    _dev_session_remote_fallback "$session" pop && return
    echo "No such session: $session"; return 1
  fi
  if [[ -n $TMUX && "$(tmux display-message -p '#S')" == "$session" ]]; then
    echo "You're inside $session right now — run tpop from a different terminal."; return 1
  fi

  # Resume id: the precise CLAUDE_RESUME_ID stamped on the session (by
  # _dev_new_session/_dev_resume_session, or the claude-stamp-tmux SessionStart
  # hook for any other launch path), else the dir's newest transcript. The
  # newest-fallback is only for pre-hook sessions and is ambiguous when several
  # slots share one repo dir (it returns whichever sibling wrote last); the hook
  # is what makes targeting a specific slot reliable.
  local dir sid
  dir=$(tmux display-message -p -t "$session" '#{session_path}')
  sid=$(tmux show-environment -t "$session" CLAUDE_RESUME_ID 2>/dev/null | cut -d= -f2)
  if [[ -z $sid ]]; then
    # newest transcript for the dir: (N)ullglob, (om) order by mtime, [1] = first
    local -a tx=( "$HOME/.claude/projects/${dir//\//-}"/*.jsonl(Nom[1]) )
    sid=${${tx[1]:t}%.jsonl}
  fi
  [[ -n $sid ]] || { echo "Couldn't find a session id for $session ($dir)."; return 1; }

  echo "Popping $session → foreground (claude -r ${sid[1,8]}… in $dir)"
  # Capture the live claude PID inside the session so we can wait for it to fully
  # exit before resuming. Claude Code takes NO lock on a session's transcript: if
  # the old (tmux) process and the new (foreground) one are both live on $sid they
  # each append to one .jsonl with no coordination, the conversation diverges, and
  # the copy you aren't driving looks frozen. kill-session only sends SIGHUP, so
  # the old claude needs a beat to trap it, flush, and exit — racing it here was
  # the "popped session stops updating" bug.
  local pane_pid cpid
  pane_pid=$(tmux list-panes -t "$session" -F '#{pane_pid}' 2>/dev/null | head -1)
  [[ -n $pane_pid ]] && cpid=$(pgrep -P "$pane_pid" 2>/dev/null | head -1)   # claude = pane shell's child
  tmux kill-session -t "$session"
  if [[ -n $cpid ]]; then
    local n=0
    while kill -0 "$cpid" 2>/dev/null && (( n++ < 100 )); do sleep 0.05; done   # wait ≤5s for it to die
    kill -0 "$cpid" 2>/dev/null && \
      echo "warning: $session's claude ($cpid) didn't exit; resuming anyway — transcript may interleave." >&2
  fi
  cd "$dir" && claude -r "$sid"
}

# _tbeam_sync_transcript <cwd> <host> — copy a session's transcript dir to <host>
# before it's resumed there. The conversation lives in
# ~/.claude/projects/<cwd-with-slashes-as-dashes>/, and `claude -r <sid>` on the
# far side can only resume what's already on its disk — so the bytes must land
# first. csync/iCloud is the background convergence path; this is the immediate,
# deterministic push for "beam it *now*".
#
# Conflict policy (the one real knob): rsync --update, no --delete. Newer mtime
# wins per file, nothing is removed. The machine you're beaming FROM holds the
# live, freshest copy, so it wins — but if the host somehow had a newer copy
# (you'd worked there more recently) it's preserved rather than clobbered.
_tbeam_sync_transcript() {
  local cwd="$1" host="$2"
  local enc="${cwd//\//-}"                       # /a/b → -a-b, Claude's dir scheme
  local src="$HOME/.claude/projects/$enc/"
  [[ -d $src ]] || { echo "tbeam: no transcript dir for $cwd ($src)" >&2; return 1; }
  rsync -az --update --exclude='.DS_Store' -e ssh "$src" "$host:.claude/projects/$enc/"
}

# _tbeam_pull_transcript <cwd> <host> — the mirror of _tbeam_sync_transcript: pull
# a session's transcript dir FROM <host> down to here before it's resumed locally
# (tbeam --from). Same conflict policy — rsync --update, no --delete — only the
# direction flips, so the freshest copy of each file survives whichever way the
# beam flows.
_tbeam_pull_transcript() {
  local cwd="$1" host="$2"
  local enc="${cwd//\//-}"                          # /a/b → -a-b, Claude's dir scheme
  local dst="$HOME/.claude/projects/$enc/"
  mkdir -p "$dst"
  rsync -az --update --exclude='.DS_Store' -e ssh "$host:.claude/projects/$enc/" "$dst"
}

# _tbeam_transcript_cwd <transcript.jsonl> — print the working dir a session ran
# in, read from the first `"cwd"` line of its transcript (the real path, not the
# lossy dash-encoded folder name). Used when `tbeam -s <id>` resolves an explicit
# id locally and needs that session's cwd to verify it on the host and cd there.
_tbeam_transcript_cwd() {
  python3 - "$1" <<'PY'
import json, sys
for line in open(sys.argv[1], errors='ignore'):
    if '"cwd"' in line:
        try:
            c = json.loads(line).get('cwd')
            if c: print(c); break
        except ValueError: pass
PY
}

# _tbeam_kill_owner — stop the dev-<repo>-<slot> tmux session on THIS machine that
# owns the session id in $TB_SID (matched on the CLAUDE_RESUME_ID stamp), if one
# is live. This is what turns tbeam from a copy into a MOVE: once a session has
# landed on the far side, its origin-side owner is killed so exactly one live
# claude owns the id — two live owners of one transcript have no lock and diverge
# (the same invariant tpush/tpop protect). Shared dotfiles code so the caller can
# run it on the *remote* origin over ssh; the id rides in $TB_SID (env, not args)
# to dodge nested ssh-quoting, exactly like _tbeam_land. kill-session only SIGHUPs
# claude, but the transcript is appended live and already synced, so nothing is
# lost. Echoes the killed session name as its last line (caller reports it);
# silent, returns nonzero, if the id isn't live in any local dev slot.
_tbeam_kill_owner() {
  local sid="$TB_SID" s
  [[ -n $sid ]] || return 1
  for s in ${(f)"$(tmux list-sessions -F '#{session_name}' 2>/dev/null | grep '^dev-')"}; do
    if [[ "$(tmux show-environment -t "$s" CLAUDE_RESUME_ID 2>/dev/null | cut -d= -f2)" == "$sid" ]]; then
      tmux kill-session -t "$s" 2>/dev/null && { print -r -- "$s"; return 0; }
    fi
  done
  return 1
}

# _tbeam_land — runs ON the destination host. It's defined in the shared dotfiles
# (so it exists on every machine); the laptop invokes it over ssh with the work
# passed in the environment: TB_CWD, TB_SID, TB_MODE (tmux|fg), TB_ATTACH.
# tmux mode reuses the host's own _dev_resume_session, so what lands is a
# first-class dev-<repo>-<slot> that dev/tread/tpop already understand. fg mode
# just resumes the conversation in this ssh session's foreground.
_tbeam_land() {
  cd "$TB_CWD" 2>/dev/null || { echo "tbeam: $TB_CWD not found on ${HOST:-this host}" >&2; return 1; }
  if [[ "$TB_MODE" == fg ]]; then
    exec claude -r "$TB_SID"                     # owns this ssh TTY; dies with it
  fi
  local repo slot session
  read -r repo slot < <(_dev_slot_for_cwd "$TB_CWD")
  [[ -n $repo && -n $slot ]] || { echo "tbeam: couldn't map $TB_CWD to a dev slot" >&2; return 1; }
  session="dev-${repo}-${slot}"
  _dev_resume_session "$session" "$TB_CWD" "$TB_SID"
  if [[ -n $TB_ATTACH ]]; then
    exec tmux attach -t "$session"              # drop the ssh caller straight in
  fi
  print -r -- "$session"                        # last line: caller reads it for the hint
}

# tbeam — move a Claude session between machines (send by default; --from receives)
#
# Usage: tbeam [flags] [repo [slot]] [host]
#        tbeam <repo> [slot] --from <host>        (pull a session HERE)
#        tbeam [repo [slot]] --here               (pull HERE, auto-find the host)
#
# Arguments:
#   repo        a DEV_REPOS key → beam that dev slot's session (like tpop/tplan);
#               omit to beam THIS conversation (inside Claude) or fzf-pick
#   slot        which dev-<repo>-<slot> (default: its first live slot)
#   host        destination machine (default: $TBEAM_HOST). A bare positional is
#               read as a host only when it isn't a DEV_REPOS key.
#
# Options:
#   --from <host>       RECEIVE: pull <repo> [slot] from <host> onto THIS machine
#                       (the mirror of the default send; repo required)
#   --here              RECEIVE, auto-host: pull it back here without naming the host
#                       (auto-detects where the slot is live; bare → pick any remote)
#   -f, --fg            resume in the foreground, no tmux slot (dies if the shell
#                       drops; needs a terminal, so not usable from inside Claude)
#   -d, --detach        leave it running on <host>, just print how to attach
#   -p, --pick          force the picker even when a session is detectable
#   -a, --all           picker across every project
#   -s, --session <id>  target a session by id or unique prefix, not the picker
#
# Like tpush, but across machines — and a MOVE, not a copy: after the session lands on
# the far side, the origin's live owner is stopped so exactly one claude owns the id
# (the same invariant tpush/tpop protect). Both directions are here: by default tbeam
# SENDS THIS conversation (or an fzf pick) to <host>, lands it in a detached dev slot,
# and ssh's you in; `--from <host>` RECEIVES — pulls a session that lives on <host> down
# into a local dev slot. (To merely ATTACH a remote slot without moving it, use `t open
# <repo> <slot>` — it auto-finds and attaches in place on its host.) The repo must exist
# at the same ~/code path on both; the transcript is rsync'd before it resumes.
_t_beam() {
  # while/shift (not for-in) so -s/--session can consume the following token as
  # its value; the `=`-joined forms (-s=… / --session=…) work too.
  local fg= detach= pick= all= host= sid_arg= from_host= here=
  local -a pos=()
  while (( $# )); do
    case "$1" in
      -h|--help)            _help_for _t_beam; return 0 ;;
      -f|--fg)              fg=1 ;;
      -d|--detach)          detach=1 ;;
      -p|--pick)            pick=1 ;;
      -a|--all)             pick=1; all=1 ;;
      -s|--session|--id)    shift; sid_arg="$1" ;;
      -s=*|--session=*|--id=*) sid_arg="${1#*=}" ;;
      --from)               shift; from_host="$1" ;;
      --from=*)             from_host="${1#*=}" ;;
      --here)               here=1 ;;
      -*)                   echo "tbeam: unknown flag $1" >&2; return 1 ;;
      *)                    pos+=("$1") ;;
    esac
    shift
  done

  # RECEIVE (auto-host): --here is "bring it back" without naming the host — it
  # auto-detects which $REMOTE_HOSTS box the slot is live on (the same probe `t open`
  # uses for remote attach, _dev_remote_resolve) and then runs the --from pull. The
  # inverse of a send when you do not want to remember where it went. Repo/slot are
  # optional and just SCOPE the probe: bare `t beam --here` fzf-picks among EVERY live
  # remote session; `t beam <repo> [slot]` (or a lone slot in a repo dir) narrows it;
  # one match → no prompt. Resolves to a concrete host+repo+slot, then shares the
  # --from machinery below by setting from_host (so the MOVE + one-live-owner hold).
  if [[ -n $here && -z $from_host ]]; then
    command -v rsync >/dev/null 2>&1 || { echo "tbeam: rsync not found" >&2; return 1; }
    local repo_arg=${pos[1]} slot_arg=
    [[ ${pos[2]} == <-> ]] && slot_arg=${pos[2]}
    # Repo-aware (mirrors --from): a lone numeric positional is a SLOT of the cwd repo.
    # Bare `--here` (no positionals) stays empty so _dev_remote_resolve probes ALL remotes —
    # do NOT infer from $PWD here, or standing in any DEV_REPOS dir would silently narrow
    # the picker to that repo and hide every other remote session.
    if [[ $repo_arg == <-> && -z $slot_arg ]]; then slot_arg=$repo_arg; repo_arg=$(_t_infer_repo); fi
    # _dev_remote_resolve returns "<host>\t<repo>\t<slot>" (fzf-picks if several live).
    local res; res=$(_dev_remote_resolve "$repo_arg" "$slot_arg") || return 1
    from_host=${res%%$'\t'*}
    repo_arg=${${res#*$'\t'}%%$'\t'*}
    slot_arg=${res##*$'\t'}
    [[ -n $CLAUDE_CODE_SESSION_ID ]] && fg=
    local target="${REMOTE_HOSTS[$from_host]:-$from_host}"
    _dev_pull "$from_host" "$target" "$repo_arg" "$slot_arg" "$fg"
    return
  fi

  # RECEIVE: --from <host> pulls a session FROM that host onto THIS machine — the exact
  # mirror of the default send, and a MOVE for the same one-live-owner reason (_dev_pull
  # stops the origin copy on <host> once the transcript lands here). Positionals are
  # [repo [slot]] only (the host is named by --from, never a positional); the repo is
  # required (it names which remote slot to pull). -f resumes inline here instead of in
  # a detached dev slot — but inside Claude there is no TTY to attach, so force a slot.
  if [[ -n $from_host ]]; then
    command -v rsync >/dev/null 2>&1 || { echo "tbeam: rsync not found" >&2; return 1; }
    local repo_arg=${pos[1]} slot_arg=
    [[ ${pos[2]} == <-> ]] && slot_arg=${pos[2]}
    # Repo-aware: bare/numeric positionals mean the repo $PWD is in (`t beam 4
    # --from mini` pulls ITS slot 4 here). Alias-only inference (no slot passed to
    # _t_infer_repo): the slot lives on the REMOTE host, so a same-numbered local
    # session's name would be a coincidence, not evidence.
    if [[ $repo_arg == <-> && -z $slot_arg ]]; then slot_arg=$repo_arg; repo_arg=$(_t_infer_repo); fi
    [[ -z $repo_arg ]] && repo_arg=$(_t_infer_repo)
    [[ -n $repo_arg ]] || { echo "tbeam: --from needs a <repo> to pull (e.g. t beam dot 1 --from $from_host)" >&2; return 1; }
    [[ -n $CLAUDE_CODE_SESSION_ID ]] && fg=
    local target="${REMOTE_HOSTS[$from_host]:-$from_host}"
    _dev_pull "$from_host" "$target" "$repo_arg" "$slot_arg" "$fg"
    return
  fi

  # Positional grammar: [repo [slot]] [host], matching the dev/tplan/tpop family so
  # `tbeam api 1` lines up with `tpop api 1`. The first positional is a <repo> only
  # when it's a DEV_REPOS key — that's what disambiguates it from a bare host
  # (`tbeam mini` still means host 'mini'). An optional numeric slot follows, then
  # an optional explicit host. (This is the SEND path; the OTHER way — pull a session
  # FROM a host onto this machine — is `--from <host>`, handled just above.)
  local repo_arg= slot_arg=
  if [[ -n ${pos[1]} && -n ${DEV_REPOS[${pos[1]}]} ]]; then
    repo_arg=${pos[1]}
    if [[ ${pos[2]} == <-> ]]; then    # numeric → slot, then optional host
      slot_arg=${pos[2]}; host=${pos[3]}
    else                               # no slot → second positional is the host
      host=${pos[2]}
    fi
  elif [[ ${pos[1]} == <-> ]]; then
    # Repo-aware: a lone numeric first positional is a SLOT of the repo $PWD is in
    # (`t beam 4` ≡ `t beam <cwd-repo> 4`, optional host after — see _t_infer_repo).
    slot_arg=${pos[1]}
    repo_arg=$(_t_infer_repo "$slot_arg") || { echo "tbeam: not inside a DEV_REPOS dir — name the repo (t beam <repo> ${pos[1]})" >&2; return 1; }
    host=${pos[2]}
  else
    host=${pos[1]}
  fi
  host="${host:-${TBEAM_HOST:-}}"
  if [[ -z "$host" ]]; then
    echo "tbeam: no host given and TBEAM_HOST is unset (set it in ~/.zshrc.local)" >&2
    return 1
  fi
  command -v rsync >/dev/null 2>&1 || { echo "tbeam: rsync not found" >&2; return 1; }

  # Resolve the session id + its working dir (mirrors tpush). Four ways:
  #   • <repo> [slot] — a dev slot, resolved like tplan/tpop: tmux session →
  #     its stamped CLAUDE_RESUME_ID (newest-transcript fallback for pre-hook
  #     sessions). The origin is a local dev slot, so this is a MOVE that
  #     kill-sessions it once landed (the self_move=… block below).
  #   • -s <id>  — an explicit id (full, or a unique prefix like the 8 chars the
  #     picker/tbeam show): resolve it locally to its transcript + recorded cwd,
  #     skipping both the picker and current-session mode. Lets you re-beam a
  #     known id without picking.
  #   • inside Claude (no -p) — THIS conversation + $PWD.
  #   • otherwise — fzf-pick (scoped to $PWD; -a for every repo).
  # self_move = "the origin is THIS foreground claude" (true current-session
  # move): only then do we SIGTERM ourselves to complete the move. For any other
  # origin (a dev slot) we kill-session it instead — see the two blocks below.
  local sid cwd self_move=
  if [[ -n $repo_arg ]]; then
    local slot=$slot_arg
    if [[ -z $slot ]]; then                         # first existing slot for repo
      local n=1
      while (( n <= 20 )); do
        tmux has-session -t "dev-${repo_arg}-${n}" 2>/dev/null && { slot=$n; break; }
        (( n++ ))
      done
    fi
    local session="dev-${repo_arg}-${slot}"
    tmux has-session -t "$session" 2>/dev/null || { echo "tbeam: no such session: $session" >&2; return 1; }
    sid=$(tmux show-environment -t "$session" CLAUDE_RESUME_ID 2>/dev/null | cut -d= -f2)
    if [[ -z $sid ]]; then                          # pre-hook fallback: newest transcript in the dir
      local dir; dir=$(tmux display-message -p -t "$session" '#{session_path}')
      local -a tx=( "$HOME/.claude/projects/${dir//\//-}"/*.jsonl(Nom[1]) )
      sid=${${tx[1]:t}%.jsonl}
    fi
    [[ -n $sid ]] || { echo "tbeam: couldn't find a session id for $session" >&2; return 1; }
    cwd=${DEV_REPOS[$repo_arg]}
  elif [[ -n $sid_arg ]]; then
    setopt local_options null_glob
    local -a tx=( "$HOME/.claude/projects"/*/"$sid_arg"*.jsonl )
    (( ${#tx} ))      || { echo "tbeam: no local session matching '$sid_arg'" >&2; return 1; }
    (( ${#tx} == 1 )) || { echo "tbeam: '$sid_arg' matches ${#tx} sessions — use a longer prefix" >&2; return 1; }
    sid=${${tx[1]:t}%.jsonl}
    cwd=$(_tbeam_transcript_cwd "$tx[1]")
    [[ -n $cwd ]] || { echo "tbeam: couldn't read the working dir for $sid" >&2; return 1; }
  elif [[ -n $CLAUDE_CODE_SESSION_ID && -z $pick ]]; then
    sid=$CLAUDE_CODE_SESSION_ID; cwd=$PWD          # current-session mode
  else
    local row filter="$PWD"
    [[ -n $all ]] && filter=""                      # --all: every project
    row=$(_claude_sessions_fzf "$filter") || return 1
    [[ -n $row ]] || return 1
    sid=${row%%$'\t'*}
    cwd=${${row#*$'\t'}%%$'\t'*}
  fi
  [[ $sid == "$CLAUDE_CODE_SESSION_ID" && -n $CLAUDE_CODE_SESSION_ID ]] && self_move=1
  [[ -n $CLAUDE_CODE_SESSION_ID ]] && detach=1      # no TTY in Claude's Bash subprocess to ssh -t into
  [[ -d $cwd ]] || { echo "tbeam: session's directory no longer exists: $cwd" >&2; return 1; }

  # The far side resumes by cd'ing into the same path — bail early if it's absent.
  if ! ssh "$host" "test -d ${(q)cwd}" 2>/dev/null; then
    echo "tbeam: $cwd doesn't exist on $host — clone/sync the repo there first." >&2
    return 1
  fi

  echo "⟳ Beaming ${sid[1,8]}… ($cwd) → $host"
  _tbeam_sync_transcript "$cwd" "$host" || return 1

  # It's a MOVE, not a copy: now that the transcript is on $host, stop the origin
  # (here) so the id has one live owner. Two cases for "the origin":
  #   • a LOCAL dev slot (picker mode, or an -s id that's live in a slot) — kill it
  #     now, before the blocking ssh -t branches below take over the terminal. We
  #     aren't attached to it, so this is safe. (self_move excludes the case where
  #     that slot is the very claude we're running inside.)
  #   • THIS foreground claude (self_move — a current-session move) — can't
  #     kill-session it; instead we SIGTERM it at the very end (see the detached
  #     branch), mirroring tpush's auto-exit.
  if [[ -z $self_move ]]; then
    local killed; killed=$(TB_SID=$sid _tbeam_kill_owner)
    [[ $killed == dev-* ]] && echo "✂ Stopped the local copy ($killed) — moved to $host"
  fi

  # Foreground mode: resume straight in the ssh session (needs a real terminal).
  if [[ -n $fg ]]; then
    [[ -z $detach ]] || { echo "tbeam: -f needs a terminal; can't combine with -d / inside Claude." >&2; return 1; }
    ssh -t "$host" "TB_CWD=${(q)cwd} TB_SID=${(q)sid} TB_MODE=fg zsh -lic _tbeam_land"
    return
  fi

  # tmux mode + auto-attach: -t lets _tbeam_land exec us into the landed session.
  if [[ -z $detach ]]; then
    ssh -t "$host" "TB_CWD=${(q)cwd} TB_SID=${(q)sid} TB_MODE=tmux TB_ATTACH=1 zsh -lic _tbeam_land"
    return
  fi

  # tmux mode, detached: capture the landed session name, print an attach hint.
  local session
  session=$(ssh "$host" "TB_CWD=${(q)cwd} TB_SID=${(q)sid} TB_MODE=tmux zsh -lic _tbeam_land" | tail -1)
  [[ -n $session ]] || { echo "tbeam: landing on $host failed." >&2; return 1; }
  echo "✓ Running on $host as $session"
  local rest=${session#dev-} repo slot
  slot=${rest##*-}; repo=${rest%-*}
  if [[ -n "${DEV_REPOS[$repo]}" ]]; then
    echo "  Attach: t open $repo $slot    (or pull it back: t beam $repo $slot --from $host)"
  else
    echo "  Attach: ssh $host -t \"zsh -lic 'tmux attach -t $session'\""
  fi

  # current-session move (self_move): the origin is THIS foreground claude, so the
  # kill-block up top skipped it. Now that the session is live on $host, exit here
  # so the id keeps one owner — mirrors tpush's auto-exit: SIGTERM the controlling
  # claude (it flushes and quits in ~2s; transcript is appended live, so only the
  # in-flight turn is lost). The hints above are already flushed. If the process
  # can't be found, fall back to asking for a manual /exit. (An explicit -s id that
  # isn't our own session is NOT self_move, so we never kill the wrong claude.)
  if [[ -n $self_move ]]; then
    local cpid; cpid=$(_tpush_claude_pid)
    if [[ -n $cpid ]]; then
      echo "Moved to $host — exiting this copy so it has one owner."
      kill -TERM "$cpid"
    else
      echo "→ This session now lives on $host. Type /exit here so two copies don't diverge."
    fi
  fi
}

# (on removed — `t on <host> [cmd…]` is reimplemented natively in bin/t, with the
# same two-layer quoting and `zsh -lic` remote contract. The per-host shorthand
# functions below now forward to `t on`.)

# t — Claude session manager: open/list/move tmux'd Claude sessions (gh-style)
# The single front door (replacing the dev/tpush/tpop/tbeam/tread/tplan/tpaste/
# tfind/on family). This zsh function is the thin SHIM over the bin/t executable:
#   • bin-native verbs (ls, read, plan, paste, kill, on, beam orchestration, and the
#     internal session-rows/land/kill-owner) run straight through via `command t`
#     (the `command` builtin reaches ~/bin/t past this function, dodging the
#     name collision — same trick the claude() wrapper uses).
#   • shell-bound verbs (open/pop/push/find) map to the existing zsh functions,
#     which ALREADY do the cd + `claude -r` in the current terminal and the tpush
#     sentinel handoff correctly *because they run in the calling shell* — a bin
#     subprocess cannot. No resolve protocol is needed: the shim just calls them.
# `t <verb> -h` always shows the bin's gh-style help (forwarded below). Full per-verb
# help + the verb list live in bin/t.
t() {
  emulate -L zsh
  # -h/--help anywhere (and the bare `t`) → the bin's gh-style help/usage.
  local a
  for a in "$@"; do [[ $a == -h || $a == --help ]] && { command t "$@"; return; }; done
  local verb="$1"
  [[ -z $verb ]] && { command t; return; }
  shift
  case "$verb" in
    open)  _t_open "$@" ;;            # → _t_dev (local / -r or auto-detect remote attach / -f / fg adopt)
    pop)   _t_pop "$@" ;;             # → cd + claude -r in THIS terminal
    push)  _t_push "$@" ;;            # → sentinel handoff; claude() wrapper spawns post-exit
    find)  _t_find "$@" ;;            # → rank/pick then cd + claude -r here
    beam)  _t_beam_xlate "$@" ;;      # → _t_beam (host moves from --host to a positional)
    *)     command t "$verb" "$@" ;;  # ls/read/plan/paste/kill/on/session-rows/land/kill-owner
  esac
}

# _t_open — map gh-grammar `t open <repo> [slot] [--new|--fg|--remote] [--host H]`
# onto the existing `dev` grammar: --new→the `new` slot keyword, --fg→-f, --remote→-r;
# repo/slot/-r/-f pass through (dev parses flags in any position).
#
# Remote model (--remote and --host are CONSOLIDATED, two angles on one thing):
#   --host H names WHICH host; -r/--remote means "remote, pick the host for me".
#   • -r --new (no --host)  → START a fresh session on the default host (_dev_default_host,
#                             the first $REMOTE_HOSTS); name one with --host to override.
#   • --host H [--new]      → start/attach on H (the explicit-host path; -r is redundant
#                             here and is dropped before forwarding).
#   • -r (no --new)         → cross-host ATTACH of a live slot (host auto-inferred); handled
#                             by _t_dev/_dev_remote below, NOT here.
# The host paths dispatch via _dev_remote_open with the original gh-style args (minus
# --host / -r), so H's own `t open` re-parses them from ITS $PWD.
_t_open() {
  local -a a rest; local arg want_host= host= remote= isnew= local_only=
  for arg in "$@"; do
    if [[ -n $want_host ]]; then host=$arg; want_host=; continue; fi
    case "$arg" in
      --host)   want_host=1; continue ;;
      --host=*) host=${arg#--host=}; continue ;;
    esac
    rest+=("$arg")
    case "$arg" in
      --new|new)         a+=(new); isnew=1 ;;
      --fg)              a+=(-f) ;;
      -r|--remote)       remote=1; a+=(-r) ;;
      -l|--local|--here) local_only=1; a+=(--local) ;;
      *)                 a+=("$arg") ;;
    esac
  done
  [[ -n $want_host ]] && { echo "t open: --host requires a value" >&2; return 2; }

  # -l/--local/--here and -r/--remote are opposite intents — reject the combination
  # here too, so the `-r --new` default-host shortcut below cannot silently win over a
  # user-forced local. (_t_dev re-checks the same for its fall-through path.)
  if [[ -n $local_only && -n $remote ]]; then
    echo "t open: --local/--here and -r/--remote are mutually exclusive" >&2
    return 2
  fi

  # `-r --new` with no --host: START fresh on the default remote host. (Plain `-r`
  # with no --new falls through to _t_dev's cross-host ATTACH; --host below wins if set.)
  if [[ -z $host && -n $remote && -n $isnew ]]; then
    host=$(_dev_default_host) || {
      echo "t open -r --new: no remote hosts configured (set REMOTE_HOSTS in ~/.zshrc.local)." >&2
      return 1
    }
    echo "(no --host given — starting on $host; pass --host <h> to choose another)"
  fi

  if [[ -n $host ]]; then
    # The remote `t open` re-parses these positionals from $host's own $PWD
    # (login dir, not this laptop's repo tree), so apply _t_dev's repo-aware
    # rewrite HERE — a lone slot/keyword or bare `t open --host h` would
    # otherwise hit the wrong repo (or none) on the far side. -r/--remote is
    # dropped: the host is already chosen, and forwarding it would make the far
    # side try its OWN remote attach instead of acting locally.
    local -a flags pos
    for arg in "${rest[@]}"; do
      case "$arg" in
        -r|--remote) ;;
        -l|--local|--here) ;;   # --host already names the machine; "force local" is moot
        --*) flags+=("$arg") ;;
        *)   pos+=("$arg") ;;
      esac
    done
    local p1=${pos[1]:-} p2=${pos[2]:-}
    if [[ -z ${DEV_REPOS[$p1]:-} && ( $p1 == <-> || $p1 == new || $p1 == fg ) \
          && ( -z $p2 || $p2 == new || $p2 == fg ) ]]; then
      p2=$p1; p1=
    fi
    [[ -z $p1 ]] && p1=$(_t_infer_repo "$p2")
    rest=()
    [[ -n $p1 ]] && rest+=("$p1")
    [[ -n $p2 ]] && rest+=("$p2")
    (( ${#pos} > 2 )) && rest+=("${pos[@]:2}")
    rest+=("${flags[@]}")
    _dev_remote_open "$host" "${rest[@]}"
    return
  fi
  _t_dev "${a[@]}"
}

# _t_beam_xlate — map gh-grammar `t beam [repo] [slot] [--host H] [flags]` onto the
# _t_beam impl's `[repo [slot]] [host]` positional grammar (--host's value moves to the
# end as the trailing host positional); -f/--fg/-d/-p/-a/-s and --from <h> (the receive
# flag) pass through unchanged — _t_beam parses --from itself.
_t_beam_xlate() {
  local -a a; local arg host want_host=
  for arg in "$@"; do
    if [[ -n $want_host ]]; then host=$arg; want_host=; continue; fi
    case "$arg" in
      --host) want_host=1 ;;
      *)      a+=("$arg") ;;
    esac
  done
  _t_beam "${a[@]}" ${host:+"$host"}
}

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
    ' ~/.zshrc)"}; do
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
    "Claude:t csync"
    "Keep the Mac awake:nosleep sleep-manager"
  )

  # Per-section "how to add" hints (keyed by group title) — printed dim under the
  # rows for the generated sections, since those come from ~/.zshrc.local arrays.
  local -A hints=(
    "Repo shortcuts (cd)" "+ add a repo: DEV_REPOS[key]=~/code/repo in ~/.zshrc.local"
    "Remote machines"     "+ add a host: REMOTE_HOSTS[key]=user@host in ~/.zshrc.local"
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

# --- tab completion for our commands -------------------------------------
# compinit already ran at the top of this file, so compdef is available here.
# These helper names start with `_` so the `help` parser above skips them. (csync
# takes no args, so it needs no completion.)
#
# `_t` — subcommand-aware completion for the single `t` command: verbs at position
# 1; then the per-verb positional (a DEV_REPOS key for repo verbs, a REMOTE_HOSTS
# key for `on`), and slot/flags after. Pulls live from the ${(k)DEV_REPOS} /
# ${(k)REMOTE_HOSTS} arrays so it stays current with ~/.zshrc.local.
_t() {
  local -a verbs=(open ls kill push pop beam read plan paste find on)
  if (( CURRENT == 2 )); then
    _describe -t verbs 't verb' verbs
    return
  fi
  case ${words[2]} in
    open|kill|read|plan|paste|beam)
      if   (( CURRENT == 3 )); then _values 'repo' ${(k)DEV_REPOS}
      elif (( CURRENT == 4 )); then _values 'slot' 1 2 3 4 new fg
      else _values 'flag' --new --fg --remote -y --yes -a --all --host --from -d --detach -p --pick -s --session -h --help; fi ;;
    on)
      (( CURRENT == 3 )) && _values 'host' ${(k)REMOTE_HOSTS} || _normal ;;
    ls)
      _values 'flag' -r --remote -a --all -h --help ;;
    push)
      _values 'flag' -p --pick -a --all -h --help ;;
    find)
      _values 'flag' -k --keyword -h --help ;;
  esac
}
_sleepmgr_cmd() { _arguments '1:command:(status disable enable help)' }
compdef _t t
compdef _sleepmgr_cmd sleep-manager
