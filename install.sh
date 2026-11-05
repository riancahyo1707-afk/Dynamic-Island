#!/usr/bin/env bash
# Installs Dynamic Island as a background service for your user (KDE / Kali).
# Usage: bash install.sh      (run it from the folder that contains island.py)
set -e

APP="$HOME/.local/share/dynamic-island"
UNIT_DIR="$HOME/.config/systemd/user"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "==> Installing system packages"
sudo apt update
sudo apt install -y playerctl libxcb-cursor0 python3-venv pulseaudio-utils x11-xserver-utils network-manager iproute2 || true
sudo apt install -y dbus-bin || sudo apt install -y dbus-x11 || true   # provides dbus-monitor

echo "==> Setting up Python environment"
mkdir -p "$APP"
python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install --upgrade pip PySide6-Essentials
cp "$HERE/island.py" "$APP/island.py"

# convenient command:  island timer 5m | island toast "hi" | island quit ...
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/island" << EOF
#!/usr/bin/env bash
exec "$APP/venv/bin/python" "$APP/island.py" "\$@"
EOF
chmod +x "$HOME/.local/bin/island"

echo "==> Adding 'Add to Island Shelf' to the Dolphin right-click menu"
SM="$HOME/.local/share/kio/servicemenus"
mkdir -p "$SM"
cat > "$SM/island-shelf.desktop" << EOF
[Desktop Entry]
Type=Service
MimeType=all/all;all/allfiles;inode/directory;
X-KDE-ServiceTypes=KonqPopupMenu/Plugin
Actions=addToIslandShelf;
X-KDE-Priority=TopLevel

[Desktop Action addToIslandShelf]
Name=Add to Island Shelf
Icon=bookmark-new
Exec=$HOME/.local/bin/island shelf add %F
EOF
chmod +x "$SM/island-shelf.desktop"

echo "==> Creating systemd user service"
mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/dynamic-island.service" << EOF
[Unit]
Description=Dynamic Island
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Environment=QT_QPA_PLATFORM=xcb
ExecStart=$APP/venv/bin/python $APP/island.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=graphical-session.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now dynamic-island.service

echo
echo "Done. The island should appear at the top of your screen."
echo "  status : systemctl --user status dynamic-island"
echo "  logs   : journalctl --user -u dynamic-island -f"
echo "  stop   : systemctl --user stop dynamic-island"
echo "  remove : systemctl --user disable --now dynamic-island"
echo "  control: island timer 5m | toast \"Hello\" | page system | pomodoro | ctf NAME WHEN | remind NAME WHEN | quit"
echo "  config : ~/.config/dynamic-island/config.json  (events + launcher buttons; run: island reload)"
echo "  (if 'island' is not found, log out and in once so ~/.local/bin is in PATH)"
