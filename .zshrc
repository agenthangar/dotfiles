export PATH="$HOME/bin:$HOME/.local/bin:$PATH"

# Initialize the completion system before sourcing anything that calls `compdef`
# (e.g. the example completion below). Without this, compdef is undefined and
# sourcing the completion errors with "command not found: compdef".
autoload -Uz compinit && compinit

# OpenClaw Completion
source "$HOME/.example/completions/example.zsh"

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
