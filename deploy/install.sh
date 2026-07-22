#!/usr/bin/env bash
# One-shot installer for the Mac Studio availability watcher.
#
#   ./deploy/install.sh            # set up venv + deps + install & start service
#   ./deploy/install.sh --no-service   # just venv + deps, run it yourself
#
# Idempotent: safe to re-run after a git pull to update deps or the service.
# Detection-only phase — no browser/playwright needed.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="$HOME/.mac_studio_sniper"
ENV_FILE="$STATE_DIR/env"
INSTALL_SERVICE=1
[ "${1:-}" = "--no-service" ] && INSTALL_SERVICE=0

echo "==> repo:  $REPO_DIR"
echo "==> state: $STATE_DIR"
mkdir -p "$STATE_DIR"

# 1. venv + deps (no playwright — watch-only).
if [ ! -d "$REPO_DIR/.venv" ]; then
  echo "==> creating venv"
  python3 -m venv "$REPO_DIR/.venv"
fi
echo "==> installing deps"
"$REPO_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$REPO_DIR/.venv/bin/pip" install --quiet httpx pyyaml curl_cffi

# 2. env file scaffold.
if [ ! -f "$ENV_FILE" ]; then
  cp "$REPO_DIR/deploy/env.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "==> wrote $ENV_FILE (edit it to add Telegram creds; optional)"
fi

# 3. smoke-test the watcher can import + parse before we daemonize it.
echo "==> smoke test"
"$REPO_DIR/.venv/bin/python" -m mac_studio_sniper flightplan >/dev/null \
  && echo "    ok: package imports"

if [ "$INSTALL_SERVICE" = "0" ]; then
  echo
  echo "Done (no service). Run it yourself with:"
  echo "  SNIPER_REPO_DIR=$REPO_DIR bash $REPO_DIR/deploy/run-watch.sh"
  exit 0
fi

# 4. install the platform service.
OS="$(uname -s)"
if [ "$OS" = "Linux" ]; then
  UNIT_DIR="$HOME/.config/systemd/user"
  mkdir -p "$UNIT_DIR"
  sed "s#__REPO_DIR__#$REPO_DIR#g" \
    "$REPO_DIR/deploy/mac-studio-sniper.service.template" \
    > "$UNIT_DIR/mac-studio-sniper.service"
  # Keep running after logout (headless box).
  loginctl enable-linger "$USER" 2>/dev/null || \
    echo "    (could not enable-linger; run: sudo loginctl enable-linger $USER)"
  systemctl --user daemon-reload
  systemctl --user enable --now mac-studio-sniper.service
  echo
  echo "==> installed + started (systemd user service)."
  echo "    status: systemctl --user status mac-studio-sniper"
  echo "    logs:   journalctl --user -u mac-studio-sniper -f"
elif [ "$OS" = "Darwin" ]; then
  PLIST="$HOME/Library/LaunchAgents/com.macstudiosniper.watch.plist"
  LABEL="com.macstudiosniper.watch"
  UID_NUM="$(id -u)"
  ERRLOG="$STATE_DIR/launchd-install.err"
  mkdir -p "$HOME/Library/LaunchAgents"
  sed -e "s#__REPO_DIR__#$REPO_DIR#g" -e "s#__HOME__#$HOME#g" \
    "$REPO_DIR/deploy/com.macstudiosniper.watch.plist.template" \
    > "$PLIST"
  # Modern bootout/bootstrap, tolerant of "not loaded" and of the legacy
  # `launchctl load` returning non-zero (which under set -e silently killed
  # a prior version of this script before it could report anything).
  launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
  : > "$ERRLOG"
  if launchctl bootstrap "gui/$UID_NUM" "$PLIST" 2>>"$ERRLOG"; then
    echo "==> installed + started (launchd agent, bootstrap)."
  elif launchctl load "$PLIST" 2>>"$ERRLOG"; then
    echo "==> installed + started (launchd agent, legacy load)."
  else
    echo "!! launchd could not start the agent. Details: $ERRLOG"
    echo "   The plist is written at $PLIST. Try manually:"
    echo "     launchctl bootstrap gui/$UID_NUM $PLIST"
    echo "   Or just run it in the foreground / under tmux:"
    echo "     SNIPER_REPO_DIR=$REPO_DIR bash $REPO_DIR/deploy/run-watch.sh"
  fi
  echo "    logs:  tail -f $STATE_DIR/watch.log"
  echo "    stop:  launchctl bootout gui/$UID_NUM/$LABEL"
  echo
  echo "    NOTE: keep this Mac awake/plugged in, or it won't poll while asleep."
  echo "    Recommended: System Settings > Battery/Energy > prevent sleep on power,"
  echo "    or run: sudo pmset -c sleep 0"
else
  echo "Unsupported OS '$OS' for auto-service. Run manually:"
  echo "  SNIPER_REPO_DIR=$REPO_DIR bash $REPO_DIR/deploy/run-watch.sh"
  exit 0
fi

echo
echo "Next:"
echo "  1. (optional) add Telegram creds: edit $ENV_FILE then restart the service"
echo "  2. watch what shows up:  $REPO_DIR/.venv/bin/python -m mac_studio_sniper report"
echo "  3. poll health:          $REPO_DIR/.venv/bin/python -m mac_studio_sniper status"
