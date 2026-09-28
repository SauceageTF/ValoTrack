#!/usr/bin/env bash
# Install (or update) ValoTrack on a Debian/Ubuntu server and run it as a systemd service.
#
#   curl -fsSL https://raw.githubusercontent.com/SauceageTF/ValoTrack/main/deploy/setup.sh | bash
#
# Run it again any time to pull the latest code and restart the bot.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/SauceageTF/ValoTrack.git}"
APP_DIR="${APP_DIR:-$HOME/ValoTrack}"
SERVICE=valotrack

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this as your normal user, not root. It uses sudo where it needs to." >&2
  exit 1
fi

echo "==> Installing system packages"
sudo apt-get update -qq
sudo apt-get install -y -qq git python3 python3-venv >/dev/null

if [ -d "$APP_DIR/.git" ]; then
  echo "==> Updating code in $APP_DIR"
  git -C "$APP_DIR" pull --ff-only
else
  echo "==> Downloading ValoTrack to $APP_DIR"
  git clone -q "$REPO_URL" "$APP_DIR"
fi

echo "==> Installing Python dependencies"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
fi
chmod 600 "$APP_DIR/.env"

if grep -qE '^(DISCORD_TOKEN|HENRIK_API_KEY)=[[:space:]]*$' "$APP_DIR/.env"; then
  echo
  echo "Almost done. Put your Discord token and HenrikDev key in the config file:"
  echo "    nano $APP_DIR/.env"
  echo "Save with Ctrl+O, Enter, then exit with Ctrl+X, and run this script again to start the bot."
  exit 0
fi

echo "==> Installing the $SERVICE service"
sudo tee "/etc/systemd/system/$SERVICE.service" >/dev/null <<EOF
[Unit]
Description=ValoTrack Discord bot
After=network-online.target
Wants=network-online.target

[Service]
User=$(id -un)
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python -m valotrack
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable -q "$SERVICE"
sudo systemctl restart "$SERVICE"

sleep 5
if systemctl is-active -q "$SERVICE"; then
  echo "==> ValoTrack is running and will restart automatically after crashes and reboots."
else
  echo "==> ValoTrack failed to start. Recent logs:" >&2
fi
sudo journalctl -u "$SERVICE" -n 15 --no-pager
echo
echo "Follow the logs live with:  journalctl -u $SERVICE -f"
