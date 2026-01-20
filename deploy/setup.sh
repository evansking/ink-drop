#!/bin/bash
set -e

echo "=== Updating system ==="
apt update && apt upgrade -y

echo "=== Installing Python and dependencies ==="
apt install -y python3-pip python3-venv git

echo "=== Installing Playwright system dependencies ==="
apt install -y libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libxcomposite1 \
    libxrandr2 libxdamage1 libpangocairo-1.0-0 libpango-1.0-0 libgbm1 libasound2t64 \
    libxshmfence1 libglu1-mesa

echo "=== Creating app directory ==="
mkdir -p /opt/ink-drop
cd /opt/ink-drop

echo "=== Setting up Python venv ==="
python3 -m venv .venv
source .venv/bin/activate

echo "=== Installing uv ==="
pip install uv

echo "=== Setup complete! ==="
echo "Now run: cd /opt/ink-drop && source .venv/bin/activate"
