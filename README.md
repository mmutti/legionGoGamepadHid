# Legion Go Gamepad Mapper

Maps the Legion Go built-in gamepad to mouse and keyboard input outside of Steam.

## Button mapping

All bindings are configurable via `--configure` (see below). Defaults:

| Input | Output |
|---|---|
| Left thumbstick | Mouse cursor |
| Right thumbstick | Mouse cursor (fallback — whichever stick has greater deflection wins) |
| Y / A / X / B | ↑ / ↓ / ← / → arrow keys |
| D-pad | ↑ / ↓ / ← / → arrow keys |
| View/Back button | Left mouse click |
| Menu/Start button | Right mouse click |
| Legion L button | Lock screen |
| Settings button | Lock screen |

## Requirements

- Debian (latest stable) with Python 3
- `python3-evdev`, `python3-dbus`, `rich`, and `readchar` (installed automatically by the install script)
- A terminal configured with a Nerd Font (e.g. FiraCode Nerd Font) if you want the `--configure` TUI icons to render — non-NF terminals show boxes but the menu still works
- The `--configure` TUI supports arrow-key navigation (Up/Down to move, Enter to activate, Esc/Q to exit) in addition to number-key hotkeys. Falls back to number-only line input if run without a TTY or without `readchar`.
- `iio-sensor-proxy` recommended for automatic orientation correction (usually pre-installed on GNOME)

## Install

```bash
cd ~/src/legionGoGamepadHid
./install.sh
```

The installer will:
1. Install `python3-evdev` if not already present
2. Load the `uinput` kernel module and configure it to load at boot
3. Install a udev rule so your user can create virtual input devices
4. Create and start a systemd user service that auto-starts on every graphical login

> **Note:** if the installer adds you to the `input` group for the first time, log out and back in — the service will then start automatically on next login.

## Uninstall

```bash
./uninstall.sh
```

Removes the systemd service and udev rule. Does not remove `python3-evdev` or group membership.

## Configuring button bindings

Run the interactive configurator:

```bash
python3 legion_go_mapper.py --configure
```

Use the numbered menu to rebind any control. Press `s` to save and auto-restart the service, `q` to quit without saving.

Config is stored at `~/.config/legion-go-mapper/config.json`.

## Useful commands

```bash
systemctl --user status legion-go-mapper   # check if running
journalctl --user -u legion-go-mapper -f   # live logs
systemctl --user stop   legion-go-mapper   # stop temporarily
systemctl --user start  legion-go-mapper   # start manually
```

## Orientation

When the Legion Go is held in portrait or upside-down orientation, the mouse cursor direction is corrected automatically. The mapper reads the device orientation from `iio-sensor-proxy` via D-Bus — the same service GNOME uses to auto-rotate the display — so no configuration is needed.

If `python3-dbus` or `iio-sensor-proxy` is unavailable, a warning is printed at startup and the mapper falls back to landscape (normal) orientation.

## Transport mode

Disables all controller input for transport. Default binding: long-press the Legion L button (`legion_btn`) for ~500 ms to toggle. LED feedback:

- Solid yellow = mapper running, controls active
- Breathing red = transport-mode lock; all inputs ignored except the unlock button

Optional: set `"gnome_auto_unlock": true` in `~/.config/legion-go-mapper/config.json` to auto-unlock when the GNOME session is unlocked.

Tune the hold duration via `"long_press_ms"` (default 500). Bind `transport_mode` to any button's short or long press via `python3 legion_go_mapper.py --configure`.

LED colors are configurable as `[R, G, B]` arrays (each 0–255) in the same config file:

```json
"led_color_enabled": [255, 180, 0],
"led_color_locked":  [255,   0, 0]
```

## LED notifications (`legion-notifier`)

The mapper exposes a session-bus service (`net.legiongo.Mapper`) that flashes the stick-ring LEDs on demand. A small CLI wrapper, `legion-notifier`, calls that service via `gdbus` and also sends a GNOME desktop notification for visibility.

```bash
# Flash green × 2 on success, red × 2 on failure (+ banner)
long-build.sh; legion-notifier $?

# Custom notification: blue × 3 flashes
legion-notifier --color blue --count 3 "Deploy complete"

# LED only, no desktop banner
legion-notifier --silent --color yellow --count 5 "disk almost full"
```

Cycle behaviour:

- Up to 5 distinct pending notifications (deduped by color+count; extras dropped silently)
- The notifier cycles through pending items forever: flash burst → 2 s pause → next item → 2 s pause → loop to first, so you can't miss one even if you weren't looking when it arrived
- Between bursts, LEDs return to the base state (solid yellow when unlocked, breathing red when transport-locked)
- Cycle stops only on **dismiss** — bind it to a button via `--configure`, action name `notifier_dismiss`

Config keys in `~/.config/legion-go-mapper/config.json`:

```json
"notifications_enabled": true,
"notification_colors": {
    "green":  [0, 255,   0],
    "red":    [255, 0,   0],
    "blue":   [0,   0, 255],
    "yellow": [255, 180, 0],
    "white":  [255, 255, 255]
}
```

Add new entries to `notification_colors` to expand the palette. Unknown color names passed to `legion-notifier --color` are silently ignored by the mapper.

### Binding dismiss

The `notifier_dismiss` action clears all pending notifications and stops the cycle. Bind it to any unused button:

```bash
python3 legion_go_mapper.py --configure
# Pick a button → choose "Dismiss pending LED notifications"
```

A single short press on that button resets the cycle. Good candidates: View/Back button, an unused paddle (Y1/Y2/Y3/M3), or the Settings button short-press.

The feature degrades silently on non-GNOME sessions or when the session bus is unreachable — a warning is printed at startup, input mapping keeps working.

## Tuning

Open `legion_go_mapper.py` and adjust the constants at the top:

```python
DEADZONE       = 0.12    # ignore stick movement below this fraction (0.0–1.0)
MOUSE_SPEED    = 800.0   # cursor pixels per second at full deflection
ACCEL_EXPONENT = 1.8     # 1.0 = linear, higher = more acceleration curve
POLL_HZ        = 120     # mouse update rate in Hz
```

## Identifying buttons

If a button does not behave as expected, run detect mode to see the raw event codes:

```bash
python3 legion_go_mapper.py --detect
```

Press each button and note the `code` values printed.
