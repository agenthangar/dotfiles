#!/usr/bin/env bash
# Start dockerd on Cloud Agent VMs where systemd is unavailable, and
# materialize the private PII denylist when $PII_SCRUB_RULES is set.
# Idempotent — safe to run from .cursor/environment.json as both `install`
# (blocking, so dockerd is ready before the agent runs gitleaks) and `start`
# (per-boot, so a rotated PII_SCRUB_RULES secret lands even when the cached
# install snapshot is reused without re-running install.sh).
set -euo pipefail

# Materialize the private PII denylist when supplied (Cloud Agents / CI parity).
# Mirrors the block in install.sh, but runs per-boot so secret rotation works
# even when the install snapshot is cached. pii-scan reads PII_RULES / the
# default file path, not $PII_SCRUB_RULES directly — so the on-disk write is
# what makes fail-closed local scans see the current denylist.
if [[ -n "${PII_SCRUB_RULES:-}" ]]; then
  mkdir -p "$HOME/.config/pii-scan"
  printf '%s' "$PII_SCRUB_RULES" > "$HOME/.config/pii-scan/scrub-rules.json"
  chmod 600 "$HOME/.config/pii-scan/scrub-rules.json"
  echo "cloud-docker-ready: materialized PII denylist -> $HOME/.config/pii-scan/scrub-rules.json"
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "cloud-docker-ready: docker not installed (see .cursor/Dockerfile or AGENTS.md)" >&2
  exit 0
fi

if docker info >/dev/null 2>&1 || sudo docker info >/dev/null 2>&1; then
  exit 0
fi

if ! sudo -n true 2>/dev/null; then
  echo "cloud-docker-ready: dockerd not running and passwordless sudo unavailable" >&2
  exit 1
fi

sudo sh -c 'dockerd >/tmp/dockerd.log 2>&1 &'
for _ in $(seq 1 20); do
  if docker info >/dev/null 2>&1 || sudo docker info >/dev/null 2>&1; then
    exit 0
  fi
  sleep 1
done

echo "cloud-docker-ready: dockerd failed to start (see /tmp/dockerd.log)" >&2
tail -20 /tmp/dockerd.log >&2 || true
exit 1
