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
# reuses it, so a key added once stays loaded until reboot. An empty agent is
# refilled from the macOS Keychain (--apple-load-keychain), so a reboot does
# not bring back per-connection passphrase prompts — background ssh users
# (worktree-sweep fetches, csync) prompt-spam the console otherwise.
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

# nosleep — keep the Mac awake with the lid open; monitor agent work behind a shut lid
#
# Usage: nosleep [-f|--forever] [-d|--dim] [--grace <secs>] [--every <secs>]
#                [--retries <count>] [--backoff <secs>]
#
# Options:
#   -f, --forever    hold sleep off until Ctrl-C, unconditionally (the old behaviour)
#   -d, --dim        stay logged in with the lid shut too: on lid close dim the built-in
#                    panel instead of locking (for computer-use agents)
#   --grace <secs>   how long a signal may be absent before retries start (default 900)
#   --every <secs>   normal interval between signal checks (default 30)
#   --retries <n>    extra checks after grace expires (0–10, default 3)
#   --backoff <secs> first retry delay (1–300, default 30); doubles up to 300s
#
# Blocks SYSTEM sleep via `pmset disablesleep 1` + a background caffeinate, and holds
# the DISPLAY on while the lid is open (no idle dim, sleep or screensaver — the screen
# stays on for as long as nosleep runs), then LOCKS the screen and turns the display
# OFF the moment the lid closes — with sleep disabled a closed lid no longer sleeps, so it no longer locks,
# and the panel would stay lit behind the lid until the displaysleep timer; the Mac
# keeps running throughout (not when docked to an external display: macOS never
# slept on that lid close, so there is no lock to replace, and the closed lid is
# simply how the Mac sits). With --dim the session stays logged in behind the lid too:
# the display hold and the user-activity ping carry on while it is shut, and a lid close dims the
# built-in panel to NOSLEEP_DIM_LEVEL (default 0) and restores it when the lid opens —
# computer-use agents need an unlocked, lit session to see and click. With the lid
# open, failed checks are advisories and nosleep runs until Ctrl-C. With the lid shut,
# it keeps holding only while BOTH signals stay fresh: internet (an HTTPS exchange with
# api.anthropic.com or api.openai.com) and tokens burning — a local claude, codex, cursor-agent or
# Claude/ChatGPT app mid-turn (Claude Code runs its own caffeinate while a turn is
# in flight; for every agent, bytes moving on its sockets since the last check — at least
# NOSLEEP_NET_MIN a probe, 48 KiB, and NOSLEEP_NET_BPS over longer gaps, 1 KiB/s,
# both overridable in ~/.zshrc.local). Network activity needs two samples: the first
# check records counters, and the next (after --every seconds) can detect work.
# Sleep stays blocked while sampling and retrying. Behind a shut lid, after grace
# expires, checks retry after 30, 60 and 120 seconds by default before restoring
# sleep and exiting. Recovery resets the retry budget; reconnecting also restarts
# activity sampling and its grace.
# Lid handling and sudo refresh continue throughout the backoff.
#
# Reconnection uses macOS auto-join: in System Settings > Wi-Fi, enable Automatically
# join this network for trusted networks. On macOS 26+, set Ask to join hotspots to
# Automatic for a nearby iPhone/iPad on the same Apple Account (Wi-Fi and Bluetooth on).
# Hotspot auto-join requires no known Wi-Fi network to be available; it may not switch
# away from connected Wi-Fi with a broken internet uplink. nosleep does not toggle the
# Wi-Fi radio, change saved networks, or store passwords.
# For a persistent, unconditional block use `sleep-manager disable` instead.
nosleep() {
  [[ "$1" == -h || "$1" == --help ]] && { _help_for nosleep; return 0; }
  local forever=0 dim=0 grace=900 every=30 retries=3 backoff=30
  while (( $# )); do
    case $1 in
      -f|--forever) forever=1 ;;
      -d|--dim) dim=1 ;;
      --grace|--every|--retries|--backoff)
        (( $# >= 2 )) || { echo "nosleep: $1 needs a value" >&2; return 2; }
        case $1 in
          --grace) grace=$2 ;;
          --every) every=$2 ;;
          --retries) retries=$2 ;;
          --backoff) backoff=$2 ;;
        esac
        shift ;;
      *) echo "nosleep: unknown option '$1' (see nosleep -h)" >&2; return 2 ;;
    esac
    shift
  done
  [[ $grace == <-> && $every == <-> && $every -gt 0 ]] || { echo "nosleep: --grace/--every take a number of seconds" >&2; return 2; }
  [[ $retries == <-> && $retries -le 10 && $backoff == <-> && $backoff -ge 1 && $backoff -le 300 ]] || {
    echo "nosleep: --retries must be 0–10 and --backoff must be 1–300 seconds" >&2; return 2
  }

  # Teardown is idempotent: it runs from the INT trap, the EXIT trap (zsh scopes a
  # function's EXIT trap to the function, so it fires on every return path), and
  # the let-go branch below — whichever gets there first does the work. Its state
  # is GLOBAL on purpose: the EXIT trap fires after the function's locals are
  # unwound (verified — a local pid read as empty there, leaving caffeinate running
  # and the restore firing twice).
  typeset -g _NOSLEEP_CAF='' _NOSLEEP_DONE=0 _NOSLEEP_WHO='' _NOSLEEP_BRIGHT=''
  _NOSLEEP_NET=() _NOSLEEP_NET_AT=()
  _nosleep_restore() {
    (( _NOSLEEP_DONE )) && return 0; _NOSLEEP_DONE=1
    [[ -n $_NOSLEEP_CAF ]] && kill "$_NOSLEEP_CAF" 2>/dev/null
    # a --dim run ending behind a closed lid: put the brightness back for whoever opens it,
    # and sleep the display — staying logged in was this run's promise, not the next one's
    if [[ -n $_NOSLEEP_BRIGHT ]]; then
      _nosleep_undim
      _nosleep_lid_closed && pmset displaysleepnow 2>/dev/null
    fi
    sudo -n pmset -a disablesleep 0 2>/dev/null || sudo pmset -a disablesleep 0
  }
  trap '_nosleep_restore' EXIT
  trap '_nosleep_restore; return 130' INT TERM

  # A caffeinate -dimsu that outlived its shell (an old unconditional nosleep whose
  # terminal closed) holds the Mac awake no matter what this run decides — two were
  # found from days earlier, and a mere warning left a third running. Stop it: it
  # is exactly the hold this run exists to end. sleep-manager's own caffeinate is
  # the one deliberate persistent hold, so it is named, not killed.
  local -a strays; local pid keep
  keep=''; [[ -f /tmp/sleep-manager-caffeinate.pid ]] && keep=$(</tmp/sleep-manager-caffeinate.pid)
  strays=( ${(f)"$(ps -Axo pid=,ppid=,command= 2>/dev/null | awk '$2 == 1 && $3 ~ /caffeinate$/ && $4 == "-dimsu" {print $1}')"} )
  for pid in "${strays[@]}"; do
    [[ -n $pid ]] || continue
    if [[ $pid == "$keep" ]]; then
      echo "nosleep: sleep-manager's caffeinate (pid $pid) is holding sleep off too — \`sleep-manager enable\` releases it" >&2
    elif kill "$pid" 2>/dev/null; then
      echo "nosleep: stopped an orphaned caffeinate -dimsu (pid $pid) left by an earlier run"
    fi
  done
  # nomonitor: the backgrounded caffeinate is a plain child, not a job — no "[2] 82804"
  # notice on the terminal, and it stays parented to this shell, so it never reads
  # as an orphan to the sweep above.
  setopt localoptions nomonitor
  sudo pmset -a disablesleep 1 || return 1
  # The display is held on while the lid is open (-dims) — "the screen stays on unless
  # the lid is closed" (2026-09-25: the old -ims let pmset's 10-min battery displaysleep
  # blank the screen mid-turn). A lid close swaps to -ims so the display assertion never
  # fights _nosleep_lock's display sleep behind the lid; --dim keeps -dims throughout.
  _nosleep_hold -dims

  # LAST-SEEN timestamps preserve grace across failed probes. Once a signal expires,
  # schedule bounded retries without sleeping through lid changes or sudo refresh.
  # Always take two activity samples, even with --grace 0, before judging idleness.
  local now busy_at online_at=$EPOCHSECONDS why checked_at=0 next_check=0 lid_was=0 lid_shut=0 lid_shut_was=0 pinged_at=0 probes=0
  local retry_count=0 retry_delay=$backoff retry_for='' missing online=1 was_online=1 refreshed_at=0
  local lid_does='display held on, lid close locks'
  (( dim )) && lid_does='staying logged in, lid close dims'
  if (( forever )); then
    echo "nosleep: holding sleep off until Ctrl-C ($lid_does)"
  else
    echo "nosleep: holding sleep off with the lid open; agent and network checks are advisories (lid shut: grace ${grace}s + $retries retries, $lid_does, Ctrl-C to stop)"
  fi
  busy_at=$online_at
  while :; do
    # A signal delivered inside a helper returns from that helper, not this loop.
    # Teardown has already run; never re-arm power assertions on the next tick.
    (( _NOSLEEP_DONE )) && return 130
    now=$EPOCHSECONDS
    lid_shut=0
    _nosleep_lid_shut && lid_shut=1
    if (( lid_shut != lid_shut_was )); then
      # Recheck immediately on closure; opening the lid cancels a pending retry.
      next_check=0 retry_count=0 retry_delay=$backoff retry_for=''
      lid_shut_was=$lid_shut
    fi
    if _nosleep_lid_closed; then
      if (( ! lid_was )); then
        if (( dim )); then _nosleep_dim; else _nosleep_hold -ims; _nosleep_lock; fi
        lid_was=1
      fi
    else
      if (( lid_was )); then
        if (( dim )); then _nosleep_undim; else _nosleep_hold -dims; fi
      fi
      lid_was=0
    fi
    # the ping runs while the display is meant to be on: lid open, or any time under --dim
    # (behind a shut lid a user-activity ping would relight the panel _nosleep_lock slept)
    if (( (dim || ! lid_was) && now - pinged_at >= every )); then
      pinged_at=$now
      # the display assertion holds display sleep, not the screensaver's idle timer —
      # a user-activity ping resets that (and would relight a panel that slept anyway)
      caffeinate -u -t 1 2>/dev/null &!
      # auto-brightness (the ambient sensor behind a shut lid) can move the level back
      (( lid_was )) && _nosleep_brightness "${NOSLEEP_DIM_LEVEL:-0}" >/dev/null
    fi
    # Keep the sudo timestamp warm even during long retries (or --forever).
    if (( now - refreshed_at >= 30 )); then
      sudo -n -v 2>/dev/null
      refreshed_at=$now
      if ! _nosleep_pmset_held; then
        # Cleared under us — another nosleep's exit resets it for everyone (its restore
        # is global, and the survivor's caffeinate still holds IDLE sleep off, so nothing
        # looks wrong until the lid closes and the Mac sleeps). Re-arm rather than fight
        # over exits: while this run lives, the flag is its invariant.
        [[ -t 1 ]] && printf '\r\e[K'
        if sudo -n pmset -a disablesleep 1 2>/dev/null; then
          echo "nosleep: the sleep-disable flag was reset under this run (another nosleep exiting?) — re-armed"
        else
          echo "nosleep: the sleep-disable flag was reset under this run and sudo could not re-arm it — a closed lid would sleep the Mac" >&2
        fi
      fi
    fi
    if (( ! forever && now >= next_check )); then
      checked_at=$now
      next_check=$(( checked_at + every ))
      online=0
      if _nosleep_online; then
        online=1
        online_at=$EPOCHSECONDS
        if (( ! was_online )); then
          # Offline silence cannot establish that the task finished. Give a resumed
          # agent fresh baselines, grace and retries instead of exiting on stale work.
          busy_at=$online_at
          _NOSLEEP_NET=() _NOSLEEP_NET_AT=()
          probes=0 retry_count=0 retry_delay=$backoff retry_for=''
          [[ -t 1 ]] && printf '\r\e[K'
          echo "nosleep: network recovered — restarting agent activity sampling and grace"
        fi
      elif (( was_online )); then
        [[ -t 1 ]] && printf '\r\e[K'
        echo "nosleep: connection lost — holding awake while macOS auto-join can reconnect"
      fi
      was_online=$online
      _nosleep_busy_at; (( REPLY )) && busy_at=$REPLY
      (( _NOSLEEP_DONE )) && return 130
      now=$EPOCHSECONDS
      (( ++probes ))
      missing=''
      if (( ! online && (! lid_shut || now - online_at > grace) )); then
        missing=network why="the network has been down for $(( now - online_at ))s"
      elif (( probes > 1 && ! REPLY && (! lid_shut || now - busy_at > grace) )); then
        missing=activity why="no local agent activity for $(( now - busy_at ))s"
      fi
      if [[ -n $missing ]]; then
        if (( ! lid_shut )); then
          [[ -t 1 ]] && printf '\r\e[K'
          echo "nosleep: advisory — $why; lid open, sleep stays blocked"
          retry_count=0 retry_delay=$backoff retry_for=''
        else
          # A different missing signal gets its own retry budget. Network recovery
          # above also resets activity, since agents often stop sending while offline.
          if [[ $retry_for != $missing ]]; then
            retry_count=0 retry_delay=$backoff retry_for=$missing
          fi
          [[ -t 1 ]] && printf '\r\e[K'
          if (( retry_count >= retries )); then
            echo "nosleep: letting go — $why; $retries retries exhausted"
            _nosleep_restore
            return 0
          fi
          (( ++retry_count ))
          next_check=$(( now + retry_delay ))
          echo "nosleep: $why — retry $retry_count/$retries in ${retry_delay}s (sleep stays blocked)"
          retry_delay=$(( retry_delay < 150 ? retry_delay * 2 : 300 ))
        fi
      else
        if (( retry_count )); then
          [[ -t 1 ]] && printf '\r\e[K'
          echo "nosleep: activity recovered — resuming normal checks"
        fi
        retry_count=0 retry_delay=$backoff retry_for=''
      fi
      if [[ -t 1 ]]; then
        if (( retry_count )); then
          printf '\r\e[K  waiting for %s · retry %d/%d in %ds · sleep stays blocked' "$retry_for" "$retry_count" "$retries" $(( next_check - now ))
        elif [[ -n $missing ]]; then
          printf '\r\e[K  advisory: %s · lid open · sleep stays blocked' "$why"
        elif [[ -n $_NOSLEEP_WHO ]]; then
          printf '\r\e[K  %s active %ds ago · network ok %ds ago' "$_NOSLEEP_WHO" $(( now - busy_at )) $(( now - online_at ))
        elif (( probes == 1 )); then
          # The first network probe only records baselines, even mid-turn. It is
          # not evidence of idleness (and sleep is already held during sampling).
          printf '\r\e[K  sampling agent activity (next check in %ds) · network ok %ds ago' "$every" $(( now - online_at ))
        else
          printf '\r\e[K  no agent activity detected yet (grace remaining %ds) · network ok %ds ago' $(( grace > now - busy_at ? grace - (now - busy_at) : 0 )) $(( now - online_at ))
        fi
      fi
    fi
    sleep 2
  done
}
# _nosleep_lid_shut — physical lid state, including docked clamshell mode.
# Macs without a lid are treated like an open laptop: checks remain advisories.
_nosleep_lid_shut() {
  local out
  out=$(ioreg -r -k AppleClamshellState -d 1 2>/dev/null) || return 1
  [[ $out == *'"AppleClamshellState" = Yes'* ]]
}
# _nosleep_hold <caffeinate flags> — (re)start nosleep's own caffeinate with these flags,
# stopping the previous one; its pid lives in the global _NOSLEEP_CAF for the restore.
# The gap between the kill and the start is covered by the pmset disablesleep flag.
_nosleep_hold() {
  [[ -n $_NOSLEEP_CAF ]] && kill "$_NOSLEEP_CAF" 2>/dev/null
  caffeinate "$1" &
  _NOSLEEP_CAF=$!
}
# _nosleep_lid_closed — true while the lid is shut AND macOS would sleep on that closure
# (both keys sit on IOPMrootDomain: one ~10ms ioreg). AppleClamshellCausesSleep is the
# kernel's own verdict — shouldSleepOnClamshellClosed(): no external display on power,
# no clamshell-disable — and it does NOT consult `pmset disablesleep` (that flag lives
# in the sleep-allowed gate, userDisabledAllSleep). So a docked Mac (lid shut, an
# external display driving it) reads No and nosleep leaves it alone: macOS never slept
# on that lid close, so there is no lock to replace — the first version locked the
# external display the moment nosleep started. A plain laptop stays Yes under nosleep's
# disablesleep, and the lock still lands.
_nosleep_lid_closed() {
  local out
  out=$(ioreg -r -k AppleClamshellState -d 1 2>/dev/null) || return 1
  [[ $out == *'"AppleClamshellState" = Yes'* && $out == *'"AppleClamshellCausesSleep" = Yes'* ]]
}
# _nosleep_lock — lock the screen now. SACLockScreenImmediate is the call behind the
# Apple-menu Lock Screen item: instant, and it needs no Accessibility grant (the
# ctrl-cmd-q keystroke route does). It is a private framework; if the call fails, the
# display sleep below still lands and the screen-lock delay turns it into a lock.
_nosleep_lock() {
  python3 -c 'import ctypes; ctypes.CDLL("/System/Library/PrivateFrameworks/login.framework/login").SACLockScreenImmediate()' 2>/dev/null
  # Then sleep the display: with sleep disabled a closed lid neither sleeps the Mac nor
  # darkens its panel — the backlight stays lit behind the lid until pmset's displaysleep
  # timer (10 min). displaysleepnow puts it to sleep at once while the Mac keeps running
  # (caffeinate + the flag hold system sleep; opening the lid wakes the display). Lock
  # first, so what the lid-open wake shows is the login window. No "is the panel lit?"
  # re-check while closed: IOMobileFramebufferShim's CurrentPowerState reads 1 for the
  # unconnected external ports too, so it tracks the driver, not the backlight — a mouse
  # nudge that relights the panel behind the lid falls back to the timer.
  pmset displaysleepnow 2>/dev/null
  [[ -t 1 ]] && printf '\r\e[K'                      # nosleep's status line leaves no newline
  echo "nosleep: lid closed — screen locked, display off"
}
# _nosleep_brightness [level] — the BUILT-IN display's brightness (0–1): prints it, and
# with a level sets it first (prints the level it was at before). DisplayServices is the
# private framework behind the brightness keys — no CLI ships one — called through python
# ctypes as _nosleep_lock calls login.framework. rc 1 with no built-in display or no call.
_nosleep_brightness() {
  python3 - "$@" 2>/dev/null <<'PY'
import ctypes, sys
cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
ds = ctypes.CDLL("/System/Library/PrivateFrameworks/DisplayServices.framework/DisplayServices")
ds.DisplayServicesSetBrightness.argtypes = [ctypes.c_uint32, ctypes.c_float]
ids = (ctypes.c_uint32 * 16)(); n = ctypes.c_uint32()
cg.CGGetOnlineDisplayList(16, ids, ctypes.byref(n))
for d in ids[:n.value]:
    if not cg.CGDisplayIsBuiltin(d):
        continue
    b = ctypes.c_float()
    if ds.DisplayServicesGetBrightness(d, ctypes.byref(b)) != 0:
        sys.exit(1)
    if len(sys.argv) > 1 and ds.DisplayServicesSetBrightness(d, float(sys.argv[1])) != 0:
        sys.exit(1)
    print("%.4f" % b.value)
    sys.exit(0)
sys.exit(1)
PY
}
# _nosleep_dim / _nosleep_undim — --dim's lid close and lid open: dim the built-in panel
# to NOSLEEP_DIM_LEVEL remembering the level it was at (_NOSLEEP_BRIGHT, global so the
# EXIT-trap restore sees it), then put that level back. No lock, no display sleep: the
# session stays logged in and lit for whatever agent is driving it.
_nosleep_dim() {
  local was
  was=$(_nosleep_brightness "${NOSLEEP_DIM_LEVEL:-0}") && [[ -z $_NOSLEEP_BRIGHT ]] && _NOSLEEP_BRIGHT=$was
  [[ -t 1 ]] && printf '\r\e[K'
  if [[ -n $was ]]; then
    echo "nosleep: lid closed — staying logged in, display dimmed"
  else
    echo "nosleep: lid closed — staying logged in (could not dim the built-in display)" >&2
  fi
}
_nosleep_undim() {
  [[ -n $_NOSLEEP_BRIGHT ]] || return 0
  _nosleep_brightness "$_NOSLEEP_BRIGHT" >/dev/null
  _NOSLEEP_BRIGHT=''
}
# _nosleep_pmset_held — true while pmset's disablesleep flag is set (one ~10 ms read).
# The flag is what keeps a CLOSED lid from sleeping the Mac; caffeinate alone holds
# only idle sleep. It is global state that any nosleep's restore clears.
_nosleep_pmset_held() { pmset -g 2>/dev/null | grep -qE '^ *SleepDisabled[[:space:]]+1'; }
# _nosleep_online — true when an HTTPS exchange with either agent provider completes.
# Any HTTP status counts (no -f): a 404 proves the network path; only DNS/connect/
# TLS failures — the outages that actually stop tokens burning — return nonzero.
_nosleep_online() {
  local endpoint
  for endpoint in https://api.anthropic.com/ https://api.openai.com/; do
    curl -s -o /dev/null --max-time 4 "$endpoint" 2>/dev/null && return 0
  done
  return 1
}
# NOSLEEP_NET_MIN / NOSLEEP_NET_BPS — what an agent CLI must move on its sockets (in +
# out) between two probes to count as working: at least NOSLEEP_NET_MIN bytes, AND at
# least NOSLEEP_NET_BPS × the seconds since its last probe. Two bounds because idle
# traffic is bursty, not a rate — measured at the prompt (5 s samples over 150 s):
# a claude keeps a 39 B keepalive every 15 s and opens a short-lived telemetry
# connection (~2.4 KB in / 3.1 KB out) every 20–60 s; a codex posts ~1.5 KB in /
# 14–17 KB out on its one persistent connection every ~56 s plus a ~7 KB short
# connection — so one 30 s window can hold ~25 KB of idle bytes, and the first
# version's single rate floor (256 B/s × 30 s = 7.7 KB) read every idle codex as
# working. A working agent is another scale: each request carries the whole context
# out (60–300 KB bodies here) and the stream answers at 8–10 KB per 5 s sustained.
# 48 KiB a probe clears the idle worst case ~2×; the 1 KiB/s rate keeps a long
# --every from letting idle bursts add up to it. Override either in ~/.zshrc.local.
typeset -g NOSLEEP_NET_MIN=${NOSLEEP_NET_MIN:-49152}
typeset -g NOSLEEP_NET_BPS=${NOSLEEP_NET_BPS:-1024}
# _nosleep_net_working <agent> <bytes> <elapsed> — true when <bytes> moved on an agent's
# sockets over <elapsed> seconds reads as a turn rather than idle chatter. The one place
# the network signal's POLICY lives (the accounting in _nosleep_busy_at is mechanical),
# so it can be reshaped — per-agent floors, a direction test — without touching the
# probe; test_zsh_nosleep_idle_bursts_stay_under_the_floor pins the measured idle
# patterns any rule must keep reading as idle.
_nosleep_net_working() {
  local agent=$1 bytes=$2 elapsed=$3 floor
  (( elapsed < 1 )) && elapsed=1
  floor=$(( NOSLEEP_NET_BPS * elapsed ))
  (( floor < NOSLEEP_NET_MIN )) && floor=$NOSLEEP_NET_MIN
  (( bytes >= floor ))
}
# _nosleep_agent_of_comm <comm> — REPLY = the agent a process name belongs to (claude ·
# codex · cursor), empty for anything else. Keep the CLI process-name match here
# because nosleep also works when the standalone t plugin is absent. (ps
# reports argv[0], and cursor-agent's launcher `exec -a`s its own path — verified.)
# The desktop apps run the same agent cores from inside a bundle, and those count too
# (their work is tokens burning like any CLI's): the ChatGPT app's Codex is
# ChatGPT.app/…/CodexCLI.app/Contents/MacOS/codex, the Claude app's Code sessions run
# ~/Library/Application Support/Claude/claude-code/<ver>/claude.app/Contents/MacOS/claude.
# Any OTHER bundled binary (the apps' node helpers, Computer Use, renderers) is not an agent.
_nosleep_agent_of_comm() {
  REPLY=''
  if [[ $1 == *.app/Contents/* ]]; then
    if [[ $1 == */ChatGPT.app/Contents/* && ${1:t} == codex ]]; then REPLY=ChatGPT
    elif [[ $1 == */Application\ Support/Claude/claude-code/* && ${1:t} == claude ]]; then REPLY='Claude app'
    fi
  else
    case "${1:t}" in
      claude) REPLY=claude ;;
      codex|codex-aarch64-*|codex-x86_64-*) REPLY=codex ;;
      cursor-agent) REPLY=cursor ;;
    esac
  fi
}
# _nosleep_busy_at — REPLY = now when a local agent CLI is working, else 0; _NOSLEEP_WHO
# names the agents last seen at it (the status line). Three signals, any suffices:
#   1. a `caffeinate` whose parent is `claude` — Claude Code holds a `caffeinate -i -t 300`
#      under itself while a turn is in flight (a refcount in the binary: respawned every
#      240 s while held, killed 30 s after the count drops to zero — so a claude reads
#      working for ≤30 s past its turn, a tail the grace absorbs), read off one ps.
#      Instant, no baseline; claude only — codex and cursor-agent spawn nothing of the kind.
#   2. bytes moved on the agent's sockets since the previous probe, judged by
#      _nosleep_net_working. nettop lists each process's open sockets with their LIFETIME
#      byte counts, and the accounting is PER SOCKET (_NOSLEEP_NET, keyed "<pid> <socket>"):
#      a socket seen before contributes its growth, one that appeared since the last probe
#      contributes all of it, a socket that closed drops its baseline, and a pid's first
#      sighting only records baselines. NOT the -P per-process total the first version
#      read as cumulative: it is a sum over the sockets open RIGHT NOW, so it drops when
#      a pooled connection is replaced (idle claudes' totals were seen falling 7 KB
#      between probes) and reads a busy turn on a fresh connection as negative.
# Rejected on a live machine, both wrong the same way (an IDLE agent reads busy):
#   - an ESTABLISHED :443 connection — idle agents keep pooled ones open (a codex at
#     its prompt held eight, a claude two) and they outlive the turn indefinitely;
#   - CPU time — an idle codex TUI burned 0.85 s in 47 s while a claude waiting on a
#     tool burned 1.36 s: the separation is inside the noise.
#   (And transcript mtime before both: csync bulk-touches IDLE transcripts, and a
#   session mid-way through a long tool call had not written its transcript in 31 min.)
# Returns through $REPLY, never stdout: a $(…) caller runs in a SUBSHELL, where the
# baselines written to _NOSLEEP_NET would vanish and every probe would be a first
# sighting (the _pr_state_tag lesson). `nettop -n` is load-bearing — with name
# resolution a probe took 5.1 s, without it 40 ms — and a process with no socket
# gets NO row, not a zero one. The desktop apps' agent cores (the ChatGPT app's codex,
# the Claude app's claude — see _nosleep_agent_of_comm) are counted like any CLI: an
# early version skipped every *.app/Contents binary as _dev_ps_snapshot does for t ls,
# and nosleep let the Mac sleep under a ChatGPT/Claude app mid-task (2026-09-25). The
# byte floor is what keeps their idle chatter out — the ChatGPT app's codex held no
# socket at all while idle.
#   3. a power assertion the Claude or ChatGPT APP itself holds (_nosleep_app_asserting)
#      — the Claude app takes an Electron NoIdleSleep assertion while it works (5-min
#      refcounted, like Claude Code's caffeinate), which covers work whose process is
#      invisible here (Cowork runs its claude inside a VM).
# NOSLEEP_APPS — owner name (as `pmset -g assertions` prints it) → status-line label for
# the desktop apps whose own idle-sleep assertion reads as "working". Override or extend
# in ~/.zshrc.local.
typeset -gA NOSLEEP_APPS
(( ${#NOSLEEP_APPS} )) || NOSLEEP_APPS=( Claude 'Claude app' ChatGPT ChatGPT )
# _nosleep_app_asserting — true when a NOSLEEP_APPS app holds a system-sleep assertion
# right now; reply = their labels. One ~10 ms `pmset -g assertions`, read from its
# "Listed by owning process" lines: `pid 81176(Claude): [0x…] 00:03:35
# PreventUserIdleSystemSleep named: "Electron"`. Display-only assertions (the ChatGPT
# app's "Capturing") and user-activity pings are not work and do not count.
_nosleep_app_asserting() {
  # Match pmset's ASCII structure as bytes; descriptions need not be valid UTF-8.
  local LC_ALL=C line owner; reply=()
  while IFS= read -r line; do
    [[ $line =~ '^[[:space:]]*pid [0-9]+\(([^)]+)\):.*(PreventUserIdleSystemSleep|PreventSystemSleep|NoIdleSleepAssertion) named' ]] || continue
    owner=${match[1]}
    [[ -n ${NOSLEEP_APPS[$owner]:-} ]] && reply+=("${NOSLEEP_APPS[$owner]}")
  done < <(pmset -g assertions 2>/dev/null)
  reply=( "${(@u)reply}" )
  (( ${#reply} ))
}
typeset -gA _NOSLEEP_NET _NOSLEEP_NET_AT
typeset -g _NOSLEEP_WHO=''
_nosleep_busy_at() {
  local pid ppid comm prev now=$EPOCHSECONDS; local -A pcomm agent moved socks at; local -a caf args who
  while read -r pid ppid comm; do
    [[ $pid == <-> ]] || continue
    pcomm[$pid]=${comm:t}
    [[ ${comm:t} == caffeinate ]] && caf+=("$ppid")
    _nosleep_agent_of_comm "$comm"
    [[ -n $REPLY ]] && { agent[$pid]=$REPLY; args+=(-p "$pid"); }
  done < <(ps -Axo pid=,ppid=,comm= 2>/dev/null)
  for ppid in "${caf[@]}"; do
    [[ ${pcomm[$ppid]:-} == claude ]] && who+=("${agent[$ppid]:-claude}")
  done
  _nosleep_app_asserting && who+=("${reply[@]}")
  if (( ${#args} && $+commands[nettop] )); then
    local name bin bout rest cur='' key bytes
    while IFS=, read -r name bin bout rest; do
      if [[ $name == (tcp|udp)* ]]; then               # a socket of the process row above it
        [[ -n $cur && $bin == <-> && $bout == <-> ]] || continue
        key="$cur $name"; bytes=$(( bin + bout )); prev=${_NOSLEEP_NET[$key]:-}
        socks[$key]=$bytes
        if [[ -z $prev ]]; then (( moved[$cur] += bytes ))             # appeared since the last probe
        elif (( bytes > prev )); then (( moved[$cur] += bytes - prev )); fi
      else                                              # "<name>.<pid>" — an agent we asked about, or the header
        pid=${name##*.}; cur=''
        [[ $pid == <-> && -n ${agent[$pid]:-} ]] && cur=$pid
      fi
    done < <(nettop -x -n -s 1 -L 1 "${args[@]}" -J bytes_in,bytes_out 2>/dev/null)
  fi
  for pid in "${(k)agent[@]}"; do
    prev=${_NOSLEEP_NET_AT[$pid]:-}; at[$pid]=$now
    [[ -n $prev ]] || continue                          # first sighting: baselines only
    _nosleep_net_working "${agent[$pid]}" "${moved[$pid]:-0}" $(( now - prev )) && who+=("${agent[$pid]}")
  done
  # only what this probe saw survives: a closed socket, or a pid that is no longer an
  # agent (exited; the number may be reused), drops its baseline
  _NOSLEEP_NET=( "${(@kv)socks}" ); _NOSLEEP_NET_AT=( "${(@kv)at}" )
  if (( ${#who} )); then
    _NOSLEEP_WHO=${(j:, :)${(u)who}}; REPLY=$now
  else
    REPLY=0
  fi
}

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
# Usage: dots [--all | --dev | --relink]
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
[[ -r $_DOTS_SOURCE_ROOT/lib/t-integration.sh ]] && source "$_DOTS_SOURCE_ROOT/lib/t-integration.sh"

dots() {
  [[ "$1" == -h || "$1" == --help ]] && { _help_for dots; return 0; }
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
    local out
    if out=$(DOTFILES_NO_BREW=1 DOTFILES_LINK_DEV=1 "$src/install.sh" 2>&1); then
      print -r -- "${g}✓${r0} ${y}live = worktree ${src:t2} (${c}${branch:-detached}${r0}${y}) — in-progress edits are live, reloaded${r0}"
    else
      print -r -- "${y}dots --dev — install.sh failed on ${c}${branch:-detached}${r0}${y}:${r0}"
      print -r -- "$out"
    fi
    source ~/.zshrc
    return
  fi

  # Default: fast-forward the canonical checkout (the live surface) to origin/main.
  local before=""
  [[ -n $live ]] && before=$(git -C "$live" rev-parse --short HEAD 2>/dev/null)

  if ! git -C "$primary" fetch -q origin main 2>/dev/null; then
    print -r -- "${y}dots — fetch failed (offline?), reloaded only${r0}"
    # relinking needs no network, and "already pulled but never relinked" is exactly
    # the state this fixes — so do it even when the fetch failed.
    if relinked=$(_dots_relink "${live:-$primary}") && [[ -n "$relinked" ]]; then
      print -r -- "${g}✓${r0} ${y}relinked newly managed file(s):${r0}"
      print -r -- "$relinked"
    fi
    source ~/.zshrc
    return
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
      return
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
  # A normal dots update refreshes the standalone release or canonical main
  # checkout before the new shell integration is sourced. Development and relink
  # modes above intentionally keep the selected t source.
  if [[ -z ${DOTFILES_NO_T:-} ]] && ! command t update; then
    print -u2 -r -- "dots: t update failed; run the standalone t installer or its checkout's install.sh to repair it."
    update_failed=1
  fi
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
# commands. No t checkout is required to use dots, csync, or nosleep.
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
zmodload zsh/datetime 2>/dev/null                     # $EPOCHSECONDS + strftime, no `date` fork
# Only the zstat builtin (-F), NOT the module's `stat` — that would shadow the
# system stat the rest of this file (and Linux hosts) call. zstat is the portable
# mtime source: BSD `stat -f %m` and GNU `stat -c %Y` disagree, zstat is neither.
zmodload -F zsh/stat b:zstat 2>/dev/null
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
