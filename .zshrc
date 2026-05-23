export PATH="$HOME/bin:$HOME/.local/bin:$PATH"

# OpenClaw Completion
source "/Users/chrisobrien/.openclaw/completions/openclaw.zsh"

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
