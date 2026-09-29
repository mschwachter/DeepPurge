#!/usr/bin/env bash
set -e

echo "=== 1. Installing System Dependencies ==="
sudo apt-get update -qq
sudo apt-get install -y python3 python3-gi python3-gi-cairo gir1.2-gtk-3.0

echo "=== 2. Deploying DeepPurge ==="
mkdir -p ~/.local/bin/deep-purge
mkdir -p ~/.local/share/applications

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "$SCRIPT_DIR/deep-purge.py" ]; then
    cp "$SCRIPT_DIR/deep-purge.py" ~/.local/bin/deep-purge/deep-purge.py
else
    curl -sSL "https://raw.githubusercontent.com/mschwachter/DeepPurge/main/deep-purge.py" -o ~/.local/bin/deep-purge/deep-purge.py
fi
chmod +x ~/.local/bin/deep-purge/deep-purge.py

sed "s|~|$HOME\vert{}g" "$SCRIPT_DIR/deep-purge.desktop" > ~/.local/share/applications/deep-purge.desktop
chmod +x ~/.local/share/applications/deep-purge.desktop
update-desktop-database ~/.local/share/applications 2>/dev/null

echo "=== Installation Complete! ==="
echo "DeepPurge is ready in your Application Menu."
