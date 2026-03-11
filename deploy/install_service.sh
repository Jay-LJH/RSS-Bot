#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/yachiyo"
SERVICE_NAME="yachiyo-bot.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"

echo "[1/6] Create app directory"
sudo mkdir -p "${APP_DIR}"
sudo chown -R "$USER":"$USER" "${APP_DIR}"

echo "[2/6] Sync project files"
rsync -av --delete ./ "${APP_DIR}/" --exclude '.git' --exclude '__pycache__' --exclude '.venv'

echo "[3/6] Create virtualenv and install deps"
python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/pip" install --upgrade pip
"${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

echo "[4/6] Install systemd service"
sudo cp "${APP_DIR}/deploy/${SERVICE_NAME}" "${SERVICE_PATH}"
sudo sed -i "s|^User=.*|User=${USER}|" "${SERVICE_PATH}"
sudo sed -i "s|^Group=.*|Group=${USER}|" "${SERVICE_PATH}"

if [[ ! -f "${APP_DIR}/.env" ]]; then
  echo "[WARN] ${APP_DIR}/.env not found. Please create it before starting service."
fi

echo "[5/6] Reload and enable service"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"

echo "[6/6] Restart service"
sudo systemctl restart "${SERVICE_NAME}"

echo "Done. Check status with: sudo systemctl status ${SERVICE_NAME}"
echo "See logs with: sudo journalctl -u ${SERVICE_NAME} -f"
