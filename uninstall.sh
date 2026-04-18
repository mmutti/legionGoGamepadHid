#!/bin/bash
# Legion Go Gamepad Mapper — uninstaller

set -e

SERVICE_FILE="$HOME/.config/systemd/user/legion-go-mapper.service"

echo "=== Legion Go Gamepad Mapper — Uninstall ==="
echo

echo "[1/3] Stopping and disabling systemd service..."
systemctl --user stop    legion-go-mapper.service 2>/dev/null || true
systemctl --user disable legion-go-mapper.service 2>/dev/null || true
if [ -f "$SERVICE_FILE" ]; then
    rm "$SERVICE_FILE"
    systemctl --user daemon-reload
    echo "      Removed $SERVICE_FILE"
else
    echo "      Service file not found — skipping"
fi

echo "[2/3] Removing legion-notifier CLI..."
NOTIFIER_DST="$HOME/.local/bin/legion-notifier"
if [ -f "$NOTIFIER_DST" ]; then
    rm "$NOTIFIER_DST"
    echo "      Removed $NOTIFIER_DST"
else
    echo "      legion-notifier not found — skipping"
fi

echo "[3/3] Removing udev rule..."
UDEV_FILE="/etc/udev/rules.d/99-legion-go-mapper.rules"
if [ -f "$UDEV_FILE" ]; then
    sudo rm "$UDEV_FILE"
    sudo udevadm control --reload-rules
    echo "      Removed $UDEV_FILE"
else
    echo "      udev rule not found — skipping"
fi

echo
echo "Done. The mapper will no longer start on login."
echo "(python3-evdev and the 'input' group membership were not removed.)"
