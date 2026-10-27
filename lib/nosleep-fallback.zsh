# Internal nosleep outage policy. The caller owns the dynamically scoped state;
# only the child PID is global, because nosleep's EXIT trap outlives its locals.
# A connection backend must define _nosleep_hotspot_connect <timeout-seconds> and exec its helper
# directly. That helper must bound/cancel its own children and enforce its own
# hard timeout; loop cancellation can follow a synchronous connectivity probe.
# Never detach work.
# Its exit status is not proof of recovery: only _nosleep_online establishes that.

_nosleep_hotspot_connect() {
  exec python3 "$_DOTS_SOURCE_ROOT/lib/nosleep_hotspot.py" connect --ssid="$hotspot_ssid" --timeout "$1"
}

_nosleep_fallback_owned() {
  [[ -n $_NOSLEEP_HOTSPOT_PID && -n ${jobstates[(r)*:${_NOSLEEP_HOTSPOT_PID}=*]} ]]
}

_nosleep_fallback_cancel() {
  if _nosleep_fallback_owned; then
    kill -TERM "$_NOSLEEP_HOTSPOT_PID" 2>/dev/null
    # Give the owned helper time to terminate/reap its own command group first.
    local tries=0
    zmodload zsh/zselect
    while _nosleep_fallback_owned && (( ++tries <= 20 )); do
      zselect -t 5
    done
    _nosleep_fallback_owned && kill -KILL "$_NOSLEEP_HOTSPOT_PID" 2>/dev/null
    wait "$_NOSLEEP_HOTSPOT_PID" 2>/dev/null
  fi
  _NOSLEEP_HOTSPOT_PID=''
  return 0
}

_nosleep_fallback_observe() {
  (( hotspot && monitoring && internet )) || return 0
  if (( online )); then
    _nosleep_fallback_cancel
    fallback_outage_at=-1 fallback_started_at=-1 fallback_deadline=0 fallback_done=0
  elif (( fallback_outage_at < 0 )); then
    # Begin at the first observed failure, independently of startup/grace and the
    # last success. A recovered connection starts a fresh outage next time.
    fallback_outage_at=$now
  fi
}

_nosleep_fallback_tick() {
  (( _NOSLEEP_DONE )) && return 0
  (( hotspot && monitoring && internet && ! online && fallback_outage_at >= 0 && ! fallback_done )) || return 0
  if (( fallback_started_at < 0 && now - fallback_outage_at >= hotspot_after )); then
    # A stale failed probe must not trigger network switching after recovery.
    # Request a fresh probe first, even when --every/backoff is much longer.
    next_check=$now
    [[ $1 == observed ]] || return 0
    fallback_started_at=$now
    fallback_deadline=$(( now + hotspot_timeout ))
    _nosleep_status_clear
    print -r -- 'nosleep: connection still down — attempting hotspot fallback'
    if (( $+functions[_nosleep_hotspot_connect] )); then
      # Clear inherited teardown traps: this child owns no power assertions.
      ( trap - EXIT INT TERM; _nosleep_hotspot_connect "$hotspot_timeout" ) >/dev/null 2>&1 &
      _NOSLEEP_HOTSPOT_PID=$!
      # A trap may have fired between starting the child and registering its PID.
      (( _NOSLEEP_DONE )) && { _nosleep_fallback_cancel; return 0; }
    fi
  fi
  if (( fallback_started_at >= 0 )); then
    if [[ -n $_NOSLEEP_HOTSPOT_PID ]] && ! _nosleep_fallback_owned; then
      wait "$_NOSLEEP_HOTSPOT_PID" 2>/dev/null
      if (( $? != 0 )); then
        _nosleep_status_clear
        print -r -- 'nosleep: hotspot join failed — confirm broadcasting Wi-Fi, permissions and unlocked Keychain; run nosleep hotspot setup'
      fi
      _NOSLEEP_HOTSPOT_PID=''
      next_check=$now
    fi
    if (( now >= fallback_deadline )); then
      _nosleep_fallback_cancel
      fallback_done=1
      # Always verify internet once more before a pending automatic release.
      next_check=$now
    fi
  fi
  # Observe recovery during a long --every or exponential retry interval. The
  # attempt deadline is checked each loop tick, independently of next_check.
  (( next_check > now + 30 )) && next_check=$(( now + 30 ))
  return 0
}

_nosleep_fallback_holds() {
  (( hotspot && monitoring && internet && ! online && ! fallback_done ))
}
