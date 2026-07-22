#!/usr/bin/env bash
# Launcher used by both systemd and launchd. Sources the operator env file
# (Telegram creds etc.) then execs the watcher in availability/detection
# mode. Keeping env handling here means the service definitions stay
# identical across Linux and macOS.
set -euo pipefail

REPO_DIR="${SNIPER_REPO_DIR:?SNIPER_REPO_DIR must be set by the service definition}"
ENV_FILE="${SNIPER_ENV_FILE:-$HOME/.mac_studio_sniper/env}"
TARGETS="${SNIPER_TARGETS:-$REPO_DIR/mac_studio_sniper/targets.availability.yaml}"

# Load operator secrets/config if present (never committed).
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

cd "$REPO_DIR"
exec "$REPO_DIR/.venv/bin/python" -m mac_studio_sniper \
  watch --targets "$TARGETS"
