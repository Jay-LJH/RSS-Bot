#!/bin/bash
set -e

# Update package lists and install required tools.
sudo apt update
sudo apt install -y python3-pip python3-venv git curl cron

# Define Project directory as parent of this script's location
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"

echo "Setting up Virtual Environment"
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
pip install --upgrade pip
pip install -r "${PROJECT_DIR}/requirements.txt"
deactivate

echo "Deploying systemd service..."

# Update service template with correct paths and user
SERVICE_FILE="${PROJECT_DIR}/deploy/yachiyo.service"
TEMP_SERVICE="/tmp/yachiyo.service"

cp "$SERVICE_FILE" "$TEMP_SERVICE"
sed -i "s|/path/to/Yachiyo|${PROJECT_DIR}|g" "$TEMP_SERVICE"
sed -i "s|User=ubuntu|User=${USER}|g" "$TEMP_SERVICE"
sed -i "s|Group=ubuntu|Group=$(id -gn)|g" "$TEMP_SERVICE"

sudo cp "$TEMP_SERVICE" "/etc/systemd/system/yachiyo.service"
sudo systemctl daemon-reload
sudo systemctl enable yachiyo.service
sudo systemctl start yachiyo.service

echo "Yachiyo Service Started"

echo "Setting up Cron Job for Auto Update"
chmod +x "${PROJECT_DIR}/deploy/update.sh"
CRON_JOB="*/15 * * * * ${PROJECT_DIR}/deploy/update.sh >> ${PROJECT_DIR}/deploy/update.log 2>&1"

(crontab -l | grep -v "${PROJECT_DIR}/deploy/update.sh"; echo "$CRON_JOB") | crontab -

echo "Setup Completed Successfully."
echo "- Check service status: sudo systemctl status yachiyo.service"
echo "- Check application logs: sudo journalctl -u yachiyo.service -f"
