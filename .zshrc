export PATH="$HOME/bin:$HOME/.local/bin:$PATH"

# Initialize the completion system before sourcing anything that calls `compdef`
# (e.g. the example completion below). Without this, compdef is undefined and
# sourcing the completion errors with "command not found: compdef".
autoload -Uz compinit && compinit

# OpenClaw Completion
source "$HOME/.example/completions/example.zsh"

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

# prview [pr#] — quick PR status: mergeability, merge state, per-check verdicts
# (no arg → current branch's PR). Hides body/comments/diff; shows just
# mergeability, merge state, and per-check verdicts.
prview() {
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

# nosleep — keep the Mac awake until Ctrl-C (interactive; sleep-manager for background)
nosleep() { trap 'sudo pmset -a disablesleep 0' EXIT INT; sudo pmset -a disablesleep 1 && caffeinate -dimsu; }

# dots — pull latest dotfiles and reload zsh
dots() { cd ~/code/dotfiles && git pull && source ~/.zshrc && cd - > /dev/null; }

# csync [push|pull] — sync Claude Code session history to/from iCloud Drive
# csync push  → upload ~/.claude/projects/ to iCloud (default if no arg)
# csync pull  → download from iCloud to ~/.claude/projects/
csync() {
  local direction="${1:-push}"
  local icloud="$HOME/Library/Mobile Documents/com~apple~CloudDocs/claude-sessions"
  local local_dir="$HOME/.claude/projects"

  if [[ ! -d "$HOME/Library/Mobile Documents/com~apple~CloudDocs" ]]; then
    echo "iCloud Drive not found on this machine."
    return 1
  fi

  mkdir -p "$icloud"
  mkdir -p "$local_dir"

  case "$direction" in
    push)
      echo "↑ Pushing $local_dir → iCloud (merge, no delete)"
      rsync -av --update \
        --exclude='.DS_Store' \
        "$local_dir/" "$icloud/"
      ;;
    pull)
      echo "↓ Pulling iCloud → $local_dir"
      rsync -av --update \
        --exclude='.DS_Store' \
        "$icloud/" "$local_dir/"
      ;;
    *)
      echo "Usage: csync [push|pull]"
      return 1
      ;;
  esac
}

# _tpaste_claude_ready <session> — return 0 once Claude is accepting input in
# the session's pane, else return 1. tpaste polls this after launching a fresh
# session so it knows when the path can be delivered. `tmux capture-pane -p`
# dumps the pane's current visible text; decide which marker means "Claude has
# finished booting (past the git checkout) and its input box is live."
_tpaste_claude_ready() {
  local session="$1"
  local pane
  pane=$(tmux capture-pane -p -t "$session" 2>/dev/null)
  # TODO(you): return 0 when $pane shows Claude is ready for input, else 1.
  #   Capture a freshly-started session with `tmux capture-pane -p -t dev-ff-1`
  #   and pick a string that appears ONLY once the prompt is live — e.g. the
  #   "? for shortcuts" footer or the input-box border — then match it here,
  #   for example:  [[ $pane == *"? for shortcuts"* ]] && return 0
  return 1
}

# tpaste [repo] [slot] — paste latest iCloud Drive image path into a dev tmux session
# tpaste ff     → start a new ff session and queue the path into it
# tpaste ff 3   → paste into dev-ff-3 (creating it if it doesn't exist)
tpaste() {
  local repo="${1:-ff}"
  local slot="$2"

  local icloud="$HOME/Library/Mobile Documents/com~apple~CloudDocs"
  # verify iCloud Drive is accessible
  if [[ ! -d "$icloud" ]]; then
    echo "iCloud Drive not found at: $icloud"
    return 1
  fi
  # find latest screenshot in iCloud Drive root.
  # (N) is the nullglob qualifier: unmatched globs expand to nothing instead of
  # raising zsh's "no matches found" error. Collect into an array first so an
  # empty result never makes `ls` fall back to listing the current directory.
  local src
  local -a imgs
  imgs=("$icloud"/Screenshot*.png(N) "$icloud"/Screenshot*.jpg(N))
  # fall back to any image in root
  if (( ${#imgs} == 0 )); then
    imgs=("$icloud"/*.png(N) "$icloud"/*.jpg(N) "$icloud"/*.jpeg(N) "$icloud"/*.heic(N))
  fi

  if (( ${#imgs} == 0 )); then
    echo "No images found in iCloud Drive ($icloud)"
    return 1
  fi

  # newest by mtime
  src=$(ls -t "${imgs[@]}" | head -1)

  echo "Using: $src"

  # find session
  local -A repo_paths
  repo_paths[ff]="$HOME/code/financial-forecast"
  repo_paths[cfp]="$HOME/code/cashfwd-private"
  repo_paths[cf]="$HOME/code/cashfwd"

  if [[ -z "${repo_paths[$repo]}" ]]; then
    echo "Unknown repo: $repo. Use ff, cfp, or cf."
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
  echo "Starting $session for the screenshot…"
  _dev_new_session "$session" "${repo_paths[$repo]}"

  # wait (up to ~30s) for Claude to be ready before delivering the path; if the
  # readiness check is left unimplemented this degrades to a plain 30s wait
  local waited=0
  while (( waited < 30 )); do
    _tpaste_claude_ready "$session" && break
    sleep 1
    (( waited++ ))
  done

  tmux send-keys -t "$session" "$src"
  echo "Queued path in $session — attaching; press Enter to send to Claude."
  tmux attach-session -t "$session"
}

# tgo [repo] [slot] — attach to an existing dev tmux session
# tgo        → list all dev sessions
# tgo ff     → attach to first ff session
# tgo ff 3   → attach to dev-ff-3
tgo() {
  local repo="$1"
  local slot="$2"

  # no args — list sessions
  if [[ -z "$repo" ]]; then
    tmux list-sessions -F '#S' 2>/dev/null | grep '^dev-' || echo "No dev sessions running."
    return
  fi

  local -A repo_paths
  repo_paths[ff]="$HOME/code/financial-forecast"
  repo_paths[cfp]="$HOME/code/cashfwd-private"
  repo_paths[cf]="$HOME/code/cashfwd"

  if [[ -z "${repo_paths[$repo]}" ]]; then
    echo "Unknown repo: $repo. Use ff, cfp, or cf."
    return 1
  fi

  # no slot — find first existing session for this repo
  if [[ -z "$slot" ]]; then
    local n=1
    while (( n <= 20 )); do
      if tmux has-session -t "dev-${repo}-${n}" 2>/dev/null; then
        slot=$n; break
      fi
      (( n++ ))
    done
    if [[ -z "$slot" ]]; then
      echo "No sessions for '$repo'. Use 'dev $repo' to start one."
      return 1
    fi
  fi

  local session="dev-${repo}-${slot}"
  if tmux has-session -t "$session" 2>/dev/null; then
    tmux attach-session -t "$session"
  else
    echo "No session: $session (use 'dev $repo $slot' to create it)"
    return 1
  fi
}

# _dev_new_session <session> <dir> — create a detached tmux session in <dir>,
# start logging, and launch Claude on the dev/claude-1 branch. Shared by `dev`
# and `tpaste` so the bootstrap (branch dance, geometry, logging) lives in one
# place; callers attach (or not) and deliver input themselves afterwards.
_dev_new_session() {
  local session="$1" dir="$2"
  local logfile="$HOME/.tmux-logs/${session}.log"
  mkdir -p "$HOME/.tmux-logs"
  tmux new-session -d -s "$session" -c "$dir" -x 220 -y 50
  tmux pipe-pane -t "$session" -o "cat >> $logfile"
  tmux send-keys -t "$session" "git stash; git fetch origin; git checkout dev/claude-1 2>/dev/null || git checkout -b dev/claude-1; git pull origin dev/claude-1; claude" Enter
}

# dev <repo> [slot] [--no-tmux] — open/reattach a Claude Code tmux session
# repos: ff (financial-forecast), cfp (cashfwd-private), cf (cashfwd)
# slot: optional 1-4, auto-picks next free slot if omitted
# --no-tmux: run the git setup + claude inline in this terminal, no tmux session
dev() {
  local no_tmux=
  local -a pos
  local arg
  for arg in "$@"; do
    case "$arg" in
      --no-tmux) no_tmux=1 ;;
      *)         pos+=("$arg") ;;
    esac
  done
  local repo="${pos[1]}"
  local slot="${pos[2]}"

  local -A repo_paths
  repo_paths[ff]="$HOME/code/financial-forecast"
  repo_paths[cfp]="$HOME/code/cashfwd-private"
  repo_paths[cf]="$HOME/code/cashfwd"

  if [[ -z "$repo" || -z "${repo_paths[$repo]}" ]]; then
    echo "Usage: dev <ff|cfp|cf> [slot] [--no-tmux]"
    echo "  ff  → financial-forecast"
    echo "  cfp → cashfwd-private"
    echo "  cf  → cashfwd"
    echo "  --no-tmux → run git setup + claude inline (no tmux session)"
    return 1
  fi

  local dir="${repo_paths[$repo]}"

  if [[ ! -d "$dir" ]]; then
    echo "Repo dir not found: $dir"
    return 1
  fi

  # --no-tmux: cd into the repo, do the same branch setup, run claude inline.
  # No session/slot/logging — slot is a tmux concept, so skip it entirely.
  if [[ -n "$no_tmux" ]]; then
    echo "Starting claude in $dir (no tmux)"
    cd "$dir" || return 1
    git stash; git fetch origin; git checkout dev/claude-1 2>/dev/null || git checkout -b dev/claude-1; git pull origin dev/claude-1
    claude
    return
  fi

  # auto-pick slot: find a free or unattached slot, or create the next one
  if [[ -z "$slot" ]]; then
    local n=1
    while true; do
      local sname="dev-${repo}-${n}"
      if ! tmux has-session -t "$sname" 2>/dev/null; then
        # free slot — use it
        slot=$n
        break
      elif ! tmux list-clients -t "$sname" 2>/dev/null | grep -q .; then
        # exists but not attached — reattach
        slot=$n
        break
      fi
      (( n++ ))
    done
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
    _dev_new_session "$session" "$dir"
    tmux attach-session -t "$session"
  fi
}

# tread <repo> [slot] — read the scrollable log for a dev tmux session
# tread ff      → opens log for first ff session in less
# tread ff 2    → opens log for dev-ff-2
tread() {
  local repo="$1"
  local slot="${2:-1}"

  local -A repo_paths
  repo_paths[ff]="$HOME/code/financial-forecast"
  repo_paths[cfp]="$HOME/code/cashfwd-private"
  repo_paths[cf]="$HOME/code/cashfwd"

  if [[ -z "$repo" || -z "${repo_paths[$repo]}" ]]; then
    echo "Usage: tread <ff|cfp|cf> [slot]"
    # list available logs
    echo "Available logs:"
    ls "$HOME/.tmux-logs/" 2>/dev/null || echo "  (none)"
    return 1
  fi

  local logfile="$HOME/.tmux-logs/dev-${repo}-${slot}.log"

  if [[ ! -f "$logfile" ]]; then
    echo "No log found: $logfile"
    echo "(start a session with 'dev $repo $slot' first)"
    return 1
  fi

  # open at bottom, follow live output, strip ANSI escape codes for readability
  less -R +G "$logfile"
}

# help — show this command list, grouped by purpose
# Each command's name + description are parsed live from the leading
# `# name … — description` comment above each ~/.zshrc function and the header
# line of each ~/bin script, so descriptions stay current as you add commands.
# Grouping is the `groups` list below; anything not placed there shows under
# "Other" so it's never hidden.
# (zsh's own help is `run-help` / ESC-h; this doesn't touch it.)
help() {
  emulate -L zsh

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

  # Grouping by purpose.  "Title:cmd cmd …" — drop a command's name into a group
  # to file it; anything uncategorized falls through to "Other" at the end.
  local -a groups=(
    "Dotfiles & shell:dots help"
    "Git & PRs:prview"
    "Claude dev sessions (tmux):dev tgo tread tpaste"
    "Claude session sync:csync"
    "Keep the Mac awake:nosleep sleep-manager"
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
    print
  }

  print -r -- "${D}Custom commands — 'help' to list, 'dots' to edit & reload.${R}"; print
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

# --- tab completion for our commands -------------------------------------
# compinit already ran at the top of this file, so compdef is available here.
# `dev <Tab>` → ff cfp cf, `csync <Tab>` → push pull, etc. These helper names
# start with `_` so the `help` parser above skips them.
_ff_repos()     { _arguments '1:repo:(ff cfp cf)' '2:slot:(1 2 3 4)' }
_dev_repos()    { _arguments '1:repo:(ff cfp cf)' '2:slot:(1 2 3 4)' '*:flag:(--no-tmux)' }
_csync_dir()    { _arguments '1:direction:(push pull)' }
_sleepmgr_cmd() { _arguments '1:command:(status disable enable help)' }
compdef _dev_repos    dev
compdef _ff_repos     tgo tpaste tread
compdef _csync_dir    csync
compdef _sleepmgr_cmd sleep-manager
