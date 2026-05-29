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

  # auto-pick next free slot (1-4) if not specified
  if [[ -z "$slot" ]]; then
    for n in 1 2 3 4; do
      local sname="dev-${repo}-${n}"
      if ! tmux has-session -t "$sname" 2>/dev/null; then
        slot=$n
        break
      fi
    done
    # if all slots exist, reattach slot 1
    [[ -z "$slot" ]] && slot=1
  fi

  local session="dev-${repo}-${slot}"

  if tmux has-session -t "$session" 2>/dev/null; then
    echo "Reattaching $session"
    tmux attach-session -t "$session"
  else
    echo "Starting $session in $dir"
    tmux new-session -s "$session" -c "$dir" -x 220 -y 50 \; send-keys "claude" Enter
  fi
}
