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
- `python3-evdev` and `python3-dbus` (installed automatically by the install script)
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
