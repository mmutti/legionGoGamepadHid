#!/bin/bash
# Legion Go Gamepad Mapper — installer
# Installs a systemd user service that starts the mapper on graphical login.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAPPER="$SCRIPT_DIR/legion_go_mapper.py"
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SERVICE_DIR/legion-go-mapper.service"
PYTHON="$(which python3)"

echo "=== Legion Go Gamepad Mapper — Install ==="
echo

# ── 1. Dependency check ────────────────────────────────────────────────────────
echo "[1/4] Checking python3-evdev..."
if ! "$PYTHON" -c "import evdev" 2>/dev/null; then
    echo "      Installing python3-evdev via pip..."
    "$PYTHON" -m pip install evdev --break-system-packages
else
    echo "      OK"
fi

echo "      Checking python3-dbus..."
if ! "$PYTHON" -c "import dbus" 2>/dev/null; then
    echo "      Installing python3-dbus via apt..."
    sudo apt-get install -y python3-dbus
else
    echo "      OK"
fi

echo "      Checking rich (used by --configure TUI)..."
if ! "$PYTHON" -c "import rich" 2>/dev/null; then
    echo "      Installing rich via pip..."
    "$PYTHON" -m pip install rich --break-system-packages
else
    echo "      OK"
fi

echo "      Checking readchar (arrow-key support in --configure TUI)..."
if ! "$PYTHON" -c "import readchar" 2>/dev/null; then
    echo "      Installing readchar via pip..."
    "$PYTHON" -m pip install readchar --break-system-packages
else
    echo "      OK"
fi

# ── 2. uinput kernel module ────────────────────────────────────────────────────
echo "[2/4] Configuring uinput kernel module..."
if ! lsmod | grep -q uinput; then
    sudo modprobe uinput
fi
if [ ! -f /etc/modules-load.d/uinput.conf ]; then
    echo uinput | sudo tee /etc/modules-load.d/uinput.conf > /dev/null
    echo "      Created /etc/modules-load.d/uinput.conf"
else
    echo "      OK (already configured)"
fi

# ── 3. udev rules (input group gets rw on uinput) ─────────────────────────────
echo "[3/4] Installing udev rules..."
UDEV_RULE='KERNEL=="uinput", MODE="0660", GROUP="input", TAG+="uaccess"'
UDEV_FILE="/etc/udev/rules.d/99-legion-go-mapper.rules"
if [ ! -f "$UDEV_FILE" ] || ! grep -qF "$UDEV_RULE" "$UDEV_FILE"; then
    echo "$UDEV_RULE" | sudo tee "$UDEV_FILE" > /dev/null
    sudo udevadm control --reload-rules
    sudo udevadm trigger
    echo "      Created $UDEV_FILE"
else
    echo "      OK (already installed)"
fi

# Ensure user is in the input group
if ! id -nG | grep -qw input; then
    sudo usermod -aG input "$USER"
    echo "      Added $USER to the 'input' group."
    echo "      NOTE: You must log out and back in for group membership to take effect."
    echo "            The service will work correctly after that."
else
    echo "      $USER is already in the 'input' group — OK"
fi

# ── 4. systemd user service ────────────────────────────────────────────────────
echo "[4/4] Installing systemd user service..."
mkdir -p "$SERVICE_DIR"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Legion Go Gamepad → Mouse/Keyboard Mapper
# Start after the graphical session is up so /dev/input devices are ready
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=$PYTHON $MAPPER
Restart=on-failure
RestartSec=3

[Install]
WantedBy=graphical-session.target
EOF

systemctl --user daemon-reload
systemctl --user enable legion-go-mapper.service
systemctl --user start  legion-go-mapper.service

echo
STATUS=$(systemctl --user is-active legion-go-mapper.service 2>/dev/null || true)
if [ "$STATUS" = "active" ]; then
    echo "✓ Service is running now and will auto-start on every graphical login."
else
    echo "  Service installed and enabled (status: $STATUS)."
    echo "  If it didn't start yet, log out/in so the input group takes effect, then it will start automatically."
fi

echo
echo "=== Useful commands ==="
echo "  Status : systemctl --user status legion-go-mapper"
echo "  Logs   : journalctl --user -u legion-go-mapper -f"
echo "  Stop   : systemctl --user stop   legion-go-mapper"
echo "  Disable: systemctl --user disable legion-go-mapper"
echo "  Uninstall: $SCRIPT_DIR/uninstall.sh"
