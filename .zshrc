export PATH="$HOME/bin:$HOME/.local/bin:$PATH"

# Initialize the completion system before sourcing anything that calls `compdef`
# (e.g. the openclaw completion below). Without this, compdef is undefined and
# sourcing the completion errors with "command not found: compdef".
autoload -Uz compinit && compinit

# OpenClaw Completion
source "$HOME/.openclaw/completions/openclaw.zsh"

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

# Quick PR status — usage: prview [pr-number]
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

nosleep() { trap 'sudo pmset -a disablesleep 0' EXIT INT; sudo pmset -a disablesleep 1 && caffeinate -dimsu; }

# dots — pull latest dotfiles and reload zsh
dots() { cd ~/code/dotfiles && git pull && source ~/.zshrc && cd - > /dev/null; }

# tgo [repo[slot]] — attach to an existing dev tmux session
# tgo        → list all dev sessions
# tgo ff     → attach to first ff session
# tgo ff3    → attach to dev-ff-3
tgo() {
  local arg="$1"

  # no args — list sessions
  if [[ -z "$arg" ]]; then
    tmux list-sessions -F '#S' 2>/dev/null | grep '^dev-' || echo "No dev sessions running."
    return
  fi

  # parse repo + optional slot: ff, ff1, ff2, cfp, cfp2, cf, cf1 ...
  local repo slot
  if [[ "$arg" =~ ^(cfp)([0-9]+)?$ ]]; then
    repo="cfp"; slot="${match[2]}"
  elif [[ "$arg" =~ ^(ff|cf)([0-9]+)?$ ]]; then
    repo="${match[1]}"; slot="${match[2]}"
  else
    echo "Unknown repo: $arg. Use ff, ff2, cfp, cfp2, cf, cf2 ..."
    return 1
  fi

  # default slot: find first existing session for this repo
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

# dev <repo> [slot] — open/reattach a Claude Code tmux session
# repos: ff (financial-forecast), cfp (cashfwd-private), cf (cashfwd)
# slot: optional 1-4, auto-picks next free slot if omitted
dev() {
  local repo="$1"
  local slot="$2"

  local -A repo_paths
  repo_paths[ff]="$HOME/code/financial-forecast"
  repo_paths[cfp]="$HOME/code/cashfwd-private"
  repo_paths[cf]="$HOME/code/cashfwd"

  if [[ -z "$repo" || -z "${repo_paths[$repo]}" ]]; then
    echo "Usage: dev <ff|cfp|cf> [slot]"
    echo "  ff  → financial-forecast"
    echo "  cfp → cashfwd-private"
    echo "  cf  → cashfwd"
    return 1
  fi

  local dir="${repo_paths[$repo]}"

  if [[ ! -d "$dir" ]]; then
    echo "Repo dir not found: $dir"
    return 1
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
    tmux new-session -d -s "$session" -c "$dir" -x 220 -y 50
    tmux pipe-pane -t "$session" -o "cat >> $logfile"
    tmux send-keys -t "$session" "git stash; git fetch origin; git checkout dev/claude-1 2>/dev/null || git checkout -b dev/claude-1; git pull origin dev/claude-1; claude" Enter
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
