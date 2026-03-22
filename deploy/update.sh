#!/bin/bash
set -e

# Path to your Yachiyo project directory
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"

echo "Checking for updates in ${PROJECT_DIR}..."
cd "${PROJECT_DIR}"

# Fetch the latest code
git fetch origin

# Check if we are behind the remote
HEAD_HASH=$(git rev-parse HEAD)
UPSTREAM_HASH=$(git rev-parse @{u})

if [ "$HEAD_HASH" != "$UPSTREAM_HASH" ]; then
    echo "Found new updates. Pulling recent changes..."
    
    # Check if requirements.txt changes between HEAD and UPSTREAM
    REQ_CHANGED=$(git diff --name-only HEAD @{u} | grep -c "requirements.txt" || true)
    
    git pull origin main

    if [ "$REQ_CHANGED" -gt 0 ]; then
        echo "requirements.txt has changed. Updating dependencies..."
        if [ ! -d "${VENV_DIR}" ]; then
            python3 -m venv "${VENV_DIR}"
        fi
        
        source "${VENV_DIR}/bin/activate"
        pip install --upgrade pip
        pip install -r requirements.txt
        deactivate
        echo "Dependencies updated successfully."
    fi

    echo "Restarting Yachiyo service to apply changes..."
    sudo systemctl restart yachiyo.service
    echo "Update complete."
else
    echo "No updates available."
fi
