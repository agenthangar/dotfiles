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

# DEV_REPOS — single source of truth for the repos `dev` and the cd shortcuts
# below both understand. Add a repo here and it gains a `dev <key>` session AND a
# bare `<key>` cd shortcut, with no second list to keep in sync.
typeset -gA DEV_REPOS
DEV_REPOS[ff]="$HOME/code/financial-forecast"
DEV_REPOS[cfp]="$HOME/code/cashfwd-private"
DEV_REPOS[cf]="$HOME/code/cashfwd"

# Generate a cd shortcut per repo: `ff`, `cfp`, `cf` jump straight to the dir.
for _repo in ${(k)DEV_REPOS}; do
  alias "$_repo"="cd ${DEV_REPOS[$_repo]}"
done
unset _repo

# csync — two-way sync of Claude Code session history with iCloud Drive.
# One command, no direction: merges both ways (newest file wins, nothing
# deleted) so every machine converges to the union of all sessions. Just run
# `csync` on each machine. Safe to run anytime; --update means it never
# clobbers a newer copy and --no-delete means it never removes sessions.
csync() {
  local icloud="$HOME/Library/Mobile Documents/com~apple~CloudDocs/claude-sessions"
  local local_dir="$HOME/.claude/projects"

  if [[ ! -d "$HOME/Library/Mobile Documents/com~apple~CloudDocs" ]]; then
    echo "iCloud Drive not found on this machine."
    return 1
  fi

  mkdir -p "$icloud" "$local_dir"

  # iCloud may keep file *contents* evicted (dataless placeholders) even though
  # the names show up. rsync reads via mmap, which times out on those ("mmap:
  # Operation timed out") instead of fetching them — and aborts. So force
  # evicted files local first: ask the daemon to fetch (brctl), then a plain
  # read() blocks until the bytes land.
  echo "⟳ Materialising cloud-only files in iCloud…"
  find "$icloud" -type f ! -name '.DS_Store' -exec brctl download {} + 2>/dev/null
  local f
  find "$icloud" -type f ! -name '.DS_Store' -print0 2>/dev/null | while IFS= read -r -d '' f; do
    [[ "$(stat -f '%b' "$f" 2>/dev/null)" -eq 0 && "$(stat -f '%z' "$f" 2>/dev/null)" -gt 0 ]] \
      && cat "$f" >/dev/null 2>&1
  done

  # Merge both directions: iCloud → local, then local → iCloud. --update keeps
  # the newer side on each file; no --delete, so sessions only ever accumulate.
  echo "⟳ Syncing iCloud ↔ $local_dir"
  rsync -a --update --exclude='.DS_Store' "$icloud/" "$local_dir/"
  rsync -a --update --exclude='.DS_Store' "$local_dir/" "$icloud/"
  echo "✓ csync complete"
}

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
  # repo→path map: see the global DEV_REPOS (defined near the cd shortcuts)

  if [[ -z "${DEV_REPOS[$repo]}" ]]; then
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
  _dev_new_session "$session" "${DEV_REPOS[$repo]}"

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

# _dev_session_has_claude <session> — true if any pane in the session has a live
# `claude` child process. We deliberately do NOT use pane_current_command:
# Claude sets its process title to its version string (e.g. "2.1.159"), so it
# never reads as "claude"/"node" there. The pane shell's direct children are the
# reliable signal (dev sessions launch `claude` straight off the shell), and
# this survives whatever Claude calls itself next version.
_dev_session_has_claude() {
  local s="$1" pane_pid child comm
  for pane_pid in ${(f)"$(tmux list-panes -t "$s" -F '#{pane_pid}' 2>/dev/null)"}; do
    for child in ${(f)"$(pgrep -P "$pane_pid" 2>/dev/null)"}; do
      comm=$(ps -o comm= -p "$child" 2>/dev/null)
      case "${comm:t}" in (claude|node) return 0 ;; esac
    done
  done
  return 1
}

# _dev_list — print every dev-<repo>-<slot> tmux session on two axes:
#   ● attached / ○ detached   (any client viewing it)
#   ✓ active context          (a live claude process in the session = Claude
#                              alive with its conversation loaded; blank once it
#                              exits to a shell). Independent of attach state: a
#                              detached session can still hold a live Claude.
# Shared by `dev list` and bare `tgo`.
_dev_list() {
  local names
  names=$(tmux list-sessions -F '#{session_name}' 2>/dev/null | grep '^dev-' | sort)
  if [[ -z "$names" ]]; then
    echo "No dev sessions running."
    return 0
  fi
  local g c r0=
  if [[ -t 1 ]]; then g=$'\e[32m'; c=$'\e[36m'; r0=$'\e[0m'; fi
  print -r -- "dev sessions  (● attached · ✓ Claude has active context)"
  local s state dir amark cmark
  while IFS= read -r s; do
    state=$(tmux display-message -p -t "$s" '#{?session_attached,attached,detached}' 2>/dev/null)
    dir=$(tmux display-message -p -t "$s" '#{session_path}' 2>/dev/null)
    if [[ $state == attached ]]; then amark="${g}●${r0}"; else amark='○'; fi
    if _dev_session_has_claude "$s"; then cmark="${c}✓${r0}"; else cmark=' '; fi
    printf '  %s %s %-13s %-9s %s\n' "$amark" "$cmark" "$s" "$state" "${dir/#$HOME/~}"
  done <<< "$names"
}

# tgo [repo] [slot] — attach to an existing dev tmux session
# tgo        → list all dev sessions (with attached state)
# tgo ff     → attach to first ff session
# tgo ff 3   → attach to dev-ff-3
tgo() {
  local repo="$1"
  local slot="$2"

  # no args — list sessions
  if [[ -z "$repo" ]]; then
    _dev_list
    return
  fi

  # repo→path map: see the global DEV_REPOS (defined near the cd shortcuts)

  if [[ -z "${DEV_REPOS[$repo]}" ]]; then
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
# dev list | dev ls — show all dev sessions, marking attached + active context
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

  # `dev list` (or `ls`) — show sessions + state, then stop.
  if [[ "$repo" == list || "$repo" == ls ]]; then
    _dev_list
    return
  fi

  # repo→path map: see the global DEV_REPOS (defined near the cd shortcuts)

  if [[ -z "$repo" || -z "${DEV_REPOS[$repo]}" ]]; then
    echo "Usage: dev <ff|cfp|cf> [slot] [--no-tmux]"
    echo "       dev list | dev ls   → show all sessions (attached + active context)"
    echo "  ff  → financial-forecast"
    echo "  cfp → cashfwd-private"
    echo "  cf  → cashfwd"
    echo "  --no-tmux → run git setup + claude inline (no tmux session)"
    return 1
  fi

  local dir="${DEV_REPOS[$repo]}"

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

  # repo→path map: see the global DEV_REPOS (defined near the cd shortcuts)

  if [[ -z "$repo" || -z "${DEV_REPOS[$repo]}" ]]; then
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

# _claude_sessions_fzf — fzf-pick a saved Claude transcript across all projects.
# Echoes the chosen row as "<session-id>\t<cwd>\t<display>" (fzf only shows the
# display column). Newest-first by transcript mtime; the JSONL is parsed once in
# python for each file's real cwd (the `cwd` field, not the lossy folder name)
# plus the first human message as a label. Returns nonzero on no pick / no fzf.
_claude_sessions_fzf() {
  # Diagnostics go to stderr: this function's stdout is captured by the caller's
  # $(...), so a stdout error would be swallowed silently instead of shown.
  command -v fzf     >/dev/null 2>&1 || { echo "fzf not installed (brew install fzf)" >&2; return 1; }
  command -v python3 >/dev/null 2>&1 || { echo "python3 not found" >&2; return 1; }
  local projects="$HOME/.claude/projects"
  [[ -d $projects ]] || { echo "No Claude sessions at $projects" >&2; return 1; }

  python3 - "$projects" <<'PY' | fzf --delimiter=$'\t' --with-nth=3 --no-hscroll \
        --prompt='resume claude > ' --height=60% --reverse
import json, os, sys, glob, datetime
root = sys.argv[1]
rows = []
for f in glob.glob(os.path.join(root, '*', '*.jsonl')):
    sid = os.path.basename(f)[:-6]
    cwd = label = None
    try:
        for line in open(f, errors='ignore'):
            try: d = json.loads(line)
            except ValueError: continue
            if cwd is None and d.get('cwd'): cwd = d['cwd']
            if label is None and d.get('type') == 'user':
                c = d.get('message', {}).get('content')
                t = c if isinstance(c, str) else (
                    ' '.join(x.get('text', '') for x in c if isinstance(x, dict))
                    if isinstance(c, list) else '')
                t = t.strip()
                if t and not t.startswith('<'): label = t
            if cwd and label: break
    except OSError:
        continue
    mtime = os.path.getmtime(f)
    short = os.path.basename(cwd) if cwd else '?'
    label = ' '.join((label or '(no message)').split())[:70]
    rows.append((mtime, sid, cwd or '?', short, label))
rows.sort(reverse=True)
for mtime, sid, cwd, short, label in rows:
    when = datetime.datetime.fromtimestamp(mtime).strftime('%m-%d %H:%M')
    print(f"{sid}\t{cwd}\t{when}  {short:<20}  {label}")
PY
}

# _dev_resume_session <session> <dir> <session-id> — sibling of _dev_new_session:
# create a detached, logged tmux session in <dir>, but RESUME an existing Claude
# conversation (claude -r) rather than starting fresh on dev/claude-1. Same name
# + log path convention so tgo/tread/tpaste/dev treat it like any dev session.
_dev_resume_session() {
  local session="$1" dir="$2" sid="$3"
  local logfile="$HOME/.tmux-logs/${session}.log"
  mkdir -p "$HOME/.tmux-logs"
  tmux new-session -d -s "$session" -c "$dir" -x 220 -y 50
  tmux pipe-pane -t "$session" -o "cat >> $logfile"
  # Record the resumed id on the session so `tpop` can pull the exact same
  # conversation back to the foreground (it also falls back to the dir's newest
  # transcript, but this is the precise signal when we know it).
  tmux set-environment -t "$session" CLAUDE_RESUME_ID "$sid"
  tmux send-keys -t "$session" "claude -r $sid" Enter
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
#     is resumable. tgo/tread only validate DEV_REPOS keys, so tpush prints a
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
  # in `dev list`/`tgo`, but tgo/tread validate against DEV_REPOS and won't know
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
# one-shot sentinel file (path passed to Claude via CLAUDE_TPUSH_ATTACH). If
# tpush — run as /tpush from inside the session — writes a tmux session name
# there, then the moment you leave Claude (/exit or Ctrl-D) we attach you
# straight into that backgrounded session instead of dropping you at a bare
# prompt. No sentinel written → behaves exactly like plain `claude`. tpush
# can't do this itself: it runs in Claude's Bash subprocess, which has no TTY
# to attach and can't exit its own parent — the attach must happen out here.
claude() {
  local sentinel="${TMPDIR:-/tmp}/claude-tpush-attach.$$"
  rm -f "$sentinel"
  CLAUDE_TPUSH_ATTACH="$sentinel" command claude "$@"
  local rc=$?
  if [[ -s "$sentinel" ]]; then
    local target="$(<"$sentinel")"
    rm -f "$sentinel"
    if [[ -n "$target" ]] && tmux has-session -t "$target" 2>/dev/null; then
      echo "Attaching to backgrounded $target…"
      exec tmux attach -t "$target"
    fi
  fi
  return $rc
}

# tpush [-p] — push a Claude session into a detached background tmux session
# Resumes via `claude -r`, named to fit the dev/tgo/tread family.
#   • Run from INSIDE Claude (CLAUDE_CODE_SESSION_ID set): grabs THIS session +
#     $PWD automatically — this is how `/tmux` backgrounds the current chat.
#   • Run from a plain shell: fzf-pick any saved session.
#   • -p / --pick: force the picker even when a current session is detectable.
# Attach afterward with the printed command. Refusing to nest if already in tmux.
tpush() {
  if [[ -n $TMUX && "$1" != -p && "$1" != --pick ]]; then
    echo "Already inside tmux ($(tmux display-message -p '#S')). Nothing to do." >&2
    return 1
  fi

  local sid cwd
  if [[ -n $CLAUDE_CODE_SESSION_ID && "$1" != -p && "$1" != --pick ]]; then
    sid=$CLAUDE_CODE_SESSION_ID; cwd=$PWD          # current-session mode
  else
    local row
    row=$(_claude_sessions_fzf) || return 1
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

  # tgo/tread only understand DEV_REPOS keys; for a derived key, point at raw tmux.
  local attach_hint
  if [[ -n "${DEV_REPOS[$repo]}" ]]; then
    attach_hint="Attach: tgo $repo $slot    Read log: tread $repo $slot"
  else
    attach_hint="Attach: tmux attach -t $session"
  fi

  if [[ -n $existing ]]; then
    echo "This conversation is already backgrounded in $session."
  elif tmux has-session -t "$session" 2>/dev/null; then
    # _dev_slot_for_cwd picks a free slot, so this only trips on a race.
    echo "$session already exists for another session — ${attach_hint#Attach: }"
    return 1
  else
    _dev_resume_session "$session" "$cwd" "$sid"
    echo "Resumed ${sid[1,8]}… in detached $session ($cwd)"
  fi

  # Land you in the session. Three cases:
  if [[ -z $CLAUDE_CODE_SESSION_ID ]]; then
    # Plain-shell picker mode: we own a real terminal, so attach straight in.
    echo "$attach_hint"
    tmux attach-session -t "$session"
  elif [[ -n $CLAUDE_TPUSH_ATTACH ]]; then
    # Inside Claude via the claude() wrapper: can't attach from this Bash
    # subprocess, so arm the one-shot sentinel — leaving Claude attaches for us.
    print -r -- "$session" > "$CLAUDE_TPUSH_ATTACH"
    echo "→ Type /exit (or Ctrl-D) and you'll drop into $session automatically."
  else
    # Inside Claude, but launched without the wrapper (older shell): manual.
    echo "$attach_hint"
    echo "(Exit this foreground Claude — the tmux copy now owns the conversation.)"
  fi
}

# tpop [repo|session] [slot] — migrate a tmux'd Claude session back to foreground
# Kills the tmux session and resumes its conversation here with `claude -r` —
# the inverse of tpush. Run from a plain shell, not inside the session you pop.
#   tpop            → the dev session for the current dir
#   tpop ff 3       → dev-ff-3
#   tpop dev-cf-1   → that session by full name
tpop() {
  local session
  if [[ "$1" == dev-* ]]; then
    session="$1"
  elif [[ -n "$1" ]]; then
    local repo="$1" slot="$2"
    if [[ -z "$slot" ]]; then                      # first existing slot for repo
      local n=1
      while (( n <= 20 )); do
        tmux has-session -t "dev-${repo}-${n}" 2>/dev/null && { slot=$n; break; }
        (( n++ ))
      done
    fi
    session="dev-${repo}-${slot}"
  else
    # no args — find the dev session whose working dir is $PWD
    local s
    for s in ${(f)"$(tmux list-sessions -F '#{session_name}' 2>/dev/null | grep '^dev-')"}; do
      [[ "$(tmux display-message -p -t "$s" '#{session_path}')" == "$PWD" ]] && { session="$s"; break; }
    done
    [[ -n $session ]] || { echo "No dev session for $PWD. Pass a repo/slot or session name."; return 1; }
  fi

  tmux has-session -t "$session" 2>/dev/null || { echo "No such session: $session"; return 1; }
  if [[ -n $TMUX && "$(tmux display-message -p '#S')" == "$session" ]]; then
    echo "You're inside $session right now — run tpop from a different terminal."; return 1
  fi

  # Resume id: the precise CLAUDE_RESUME_ID we stashed, else the dir's newest
  # transcript (the live Claude in there keeps it freshest).
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
  tmux kill-session -t "$session"
  cd "$dir" && claude -r "$sid"
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

  # The bare `<repo>` shortcuts (ff, cfp, cf) are aliases generated from
  # DEV_REPOS, so the function/script parser above never sees them — synthesise
  # one entry per repo (:t = basename of the target dir) so they show in help.
  local _r
  for _r in ${(k)DEV_REPOS}; do
    info[$_r]="$_r — cd straight to ${DEV_REPOS[$_r]:t}"
  done

  # `dev list` is a subcommand, not its own function, so the parser only captured
  # `dev`'s first comment line — synthesise an entry so the subcommand is listed.
  info[dev-list]="dev list|ls — list dev sessions, marking attached + active Claude context"

  # Grouping by purpose.  "Title:cmd cmd …" — drop a command's name into a group
  # to file it; anything uncategorized falls through to "Other" at the end.
  local -a groups=(
    "Dotfiles & shell:dots help"
    "Git & PRs:prview"
    "Claude dev sessions (tmux):dev dev-list tgo tread tpaste tpush tpop ${(kj: :)DEV_REPOS}"
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
# `dev <Tab>` → ff cfp cf, etc. These helper names start with `_` so the `help`
# parser above skips them. (csync takes no args, so it needs no completion.)
_ff_repos()     { _arguments '1:repo:(ff cfp cf)' '2:slot:(1 2 3 4)' }
_dev_repos()    { _arguments '1:repo:(ff cfp cf)' '2:slot:(1 2 3 4)' '*:flag:(--no-tmux)' }
_sleepmgr_cmd() { _arguments '1:command:(status disable enable help)' }
compdef _dev_repos    dev
compdef _ff_repos     tgo tpaste tread
compdef _sleepmgr_cmd sleep-manager
