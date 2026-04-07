# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

A Python daemon that maps the Lenovo Legion Go's built-in gamepad HID device to mouse and keyboard input via Linux's `uinput` / `evdev` stack. It runs as a systemd user service. All button bindings are configurable at runtime via an interactive CLI.

## Key commands

```bash
# Run the mapper (normally managed by systemd)
python3 legion_go_mapper.py

# Debug: print raw events from the gamepad
python3 legion_go_mapper.py --detect

# Debug: print raw events from ALL input devices
python3 legion_go_mapper.py --detect-all

# Interactive button rebinder
python3 legion_go_mapper.py --configure

# Service management
systemctl --user status legion-go-mapper
systemctl --user restart legion-go-mapper
journalctl --user -u legion-go-mapper -f
```

## Running tests

```bash
PYTHONPATH=. pytest tests/
PYTHONPATH=. pytest tests/test_mapper.py::test_stickkeys_right_key_on_threshold_cross  # single test
```

`evdev` and `dbus` are mocked at the top of the test file — no hardware or D-Bus daemon needed.

## Architecture

Everything lives in a single file: `legion_go_mapper.py`.

**Configuration layer** (`CONFIG_PATH`, `DEFAULT_CONFIG`, `load_config`, `save_config`, `configure_mode`): JSON config at `~/.config/legion-go-mapper/config.json`. `load_config` merges the file with `DEFAULT_CONFIG` so partial files and missing files both work. `configure_mode` is the interactive TUI; `_save_and_restart` writes config and calls `systemctl --user restart`.

**Control dispatch** (`CONTROLS`, `ACTIONS_FOR_TYPE`, `BUTTON_ACTIONS`, etc.): Each control has a type (`"axis"`, `"dpad"`, `"button"`) that constrains which actions it can be bound to. Unknown action strings stored in config are silently treated as `"none"` at dispatch time — this is intentional for forward compatibility.

**StickKeys class**: Translates analog stick axis values into discrete key presses/releases. Threshold is 0.5 × AXIS_MAX. Handles both axes independently; diagonal input presses two keys simultaneously.

**Mouse movement**: Runs in a dedicated polling thread at `POLL_HZ` Hz. Applies dead zone (`DEADZONE`) and acceleration curve (`ACCEL_EXPONENT`) to raw axis values before emitting `REL_X`/`REL_Y` via `UInput`. When both sticks are bound to mouse, whichever has greater deflection wins. Before the deadzone/curve step, `rotate_for_orientation()` applies a 2×2 rotation matrix so the cursor direction matches the physical screen orientation.

**`OrientationWatcher`**: Daemon thread that subscribes to `net.hadess.SensorProxy` D-Bus signals (`AccelerometerOrientation` property). Updates `State.orientation` on every change. If `python3-dbus` or iio-sensor-proxy is unavailable it exits silently and `State.orientation` stays `"normal"`. `rotate_for_orientation(x, y, orientation)` is a pure function mapping `"normal"` / `"right-up"` / `"left-up"` / `"bottom-up"` to the corresponding 2×2 rotation.

**Special buttons** (Legion L, Settings): Detected via raw HID (`/dev/hidraw*`) on a separate thread, not through evdev, because the kernel does not expose these as standard input events.

**`toggle_osk`**: Toggles the GNOME on-screen keyboard via `gsettings`.

## Tuning constants

At the top of `legion_go_mapper.py`:

| Constant | Default | Effect |
|---|---|---|
| `DEADZONE` | `0.12` | Fraction of full deflection to ignore |
| `MOUSE_SPEED` | `800.0` | Pixels/second at max deflection |
| `ACCEL_EXPONENT` | `1.8` | Curve shape (1.0 = linear) |
| `POLL_HZ` | `120` | Mouse update rate |
