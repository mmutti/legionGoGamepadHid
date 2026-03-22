# Configure Mode Design
**Date:** 2026-03-22
**Project:** legion_go_mapper — Legion Go Gamepad Mapper

## Overview

Add an interactive `--configure` CLI mode to `legion_go_mapper.py` that lets the user rebind every control on the Legion Go controller, persisting the config to JSON and auto-restarting the systemd service on save.

---

## Config Data Model

**File:** `~/.config/legion-go-mapper/config.json`

A flat JSON object mapping each control key to an action string. If the file is absent or a key is missing, the value from `DEFAULT_CONFIG` is used. If the file contains an unrecognised action string for a key (e.g. written by a newer version of the tool), that key is loaded as-is and silently treated as `none` at dispatch time — it does not prevent the rest of the file from loading.

### Controls

Left and right triggers (LT/RT) report as analog axes (`ABS_Z` / `ABS_RZ`) on the Legion Go, not as digital button events. They are not included in the config system.

| Key | Type | Display Name |
|---|---|---|
| `left_stick` | `axis` | Left thumbstick |
| `right_stick` | `axis` | Right thumbstick |
| `dpad` | `dpad` | D-pad |
| `btn_y` | `button` | Y button |
| `btn_a` | `button` | A button |
| `btn_x` | `button` | X button |
| `btn_b` | `button` | B button |
| `btn_lb` | `button` | Left bumper (LB) |
| `btn_rb` | `button` | Right bumper (RB) |
| `btn_view` | `button` | View/Back button (BTN_START, code 315) |
| `btn_menu` | `button` | Menu/Start button (BTN_SELECT, code 314) |
| `btn_l3` | `button` | L3 (left stick click) |
| `btn_r3` | `button` | R3 (right stick click) |
| `legion_btn` | `button` | Legion L button (raw HID, bit 7 of byte 18) |
| `settings_btn` | `button` | Settings button (raw HID, bit 6 of byte 18) |

`legion_btn` and `settings_btn` are read from the raw HID interface (not evdev) but expose the same action set as regular buttons.

### Allowed Actions per Control Type

| Type | Allowed Actions |
|---|---|
| `axis` | `mouse`, `arrow_keys`, `none` |
| `dpad` | `arrow_keys`, `none` |
| `button` (all, including `legion_btn` / `settings_btn`) | `lock_screen`, `osk`, `arrow_up`, `arrow_down`, `arrow_left`, `arrow_right`, `mouse_left`, `mouse_right`, `key_y`, `key_return`, `key_esc`, `none` |

### Default Config (preserves current behaviour)

```json
{
  "left_stick":   "mouse",
  "right_stick":  "mouse",
  "dpad":         "arrow_keys",
  "btn_y":        "arrow_up",
  "btn_a":        "arrow_down",
  "btn_x":        "arrow_left",
  "btn_b":        "arrow_right",
  "btn_lb":       "none",
  "btn_rb":       "none",
  "btn_view":     "mouse_left",
  "btn_menu":     "mouse_right",
  "btn_l3":       "none",
  "btn_r3":       "none",
  "legion_btn":   "lock_screen",
  "settings_btn": "lock_screen"
}
```

---

## Interactive Menu UI

Launched via `python3 legion_go_mapper.py --configure`.

### Main screen

All 15 controls are always shown (no truncation).

```
Legion Go Gamepad — Button Configuration
=========================================
Config: ~/.config/legion-go-mapper/config.json

  1.  Left thumbstick            [Mouse cursor]
  2.  Right thumbstick           [Mouse cursor]
  3.  D-pad                      [Arrow keys]
  4.  Y button                   [Arrow key: Up]
  5.  A button                   [Arrow key: Down]
  6.  X button                   [Arrow key: Left]
  7.  B button                   [Arrow key: Right]
  8.  Left bumper (LB)           [Disabled]
  9.  Right bumper (RB)          [Disabled]
 10.  View/Back button           [Mouse left click]
 11.  Menu/Start button          [Mouse right click]
 12.  L3 (left stick click)      [Disabled]
 13.  R3 (right stick click)     [Disabled]
 14.  Legion L button            [Lock screen]
 15.  Settings button            [Lock screen]

  s.  Save and restart service
  q.  Quit without saving

Enter number to reconfigure:
```

**Invalid input** (non-numeric text, or a number less than 1 or greater than 15): print `  Invalid input.` and re-display the prompt. `s` and `q` are the only non-numeric valid inputs on this screen.

### Rebind screen (example: Y button)

```
  Y button — choose action:
    1. Lock screen
    2. Toggle on-screen keyboard
    3. Arrow key: Up    ← current
    4. Arrow key: Down
    5. Arrow key: Left
    6. Arrow key: Right
    7. Mouse left click
    8. Mouse right click
    9. Key: Y
   10. Key: Return/Enter
   11. Key: Esc
    0. Disabled

  Enter number:
```

The `← current` marker indicates the active binding. Valid inputs are `0` (Disabled) and `1` through the number of listed actions. Choosing a valid number updates the binding in memory and returns to the main screen. Any other input (including `s`, `q`, or out-of-range numbers) prints `  Invalid input.` and re-displays the action list.

### Save flow

On `s`:
1. Create `~/.config/legion-go-mapper/` with `os.makedirs(exist_ok=True)`. If this fails (e.g. permission error), print the OS error and `  Error: could not create config directory.` then return to the main screen without saving.
2. Write JSON to `~/.config/legion-go-mapper/config.json`. If the write fails, print the OS error and return to the main screen.
3. Print `Configuration saved to ~/.config/legion-go-mapper/config.json`
4. Run `systemctl --user restart legion-go-mapper`
5. If the command succeeds: print `Service restarted.` and exit
6. If the command fails (non-zero exit, service not found, systemd unavailable): print the captured stderr and `  Warning: could not restart service — run: systemctl --user restart legion-go-mapper` then exit. Config is already saved; the warning is advisory only. Exit code 0 in both cases.

On `q`: exit without writing, print `Quit — no changes saved.`

---

## Runtime Changes

### Removed

- `FACE_TO_ARROW` dict
- `MOUSE_LEFT_BTN` / `MOUSE_RIGHT_BTN` constants
- `LOCK_BTN_ENABLED` flag — replaced by checking cfg values at thread start. There is no longer a global disable path; to disable HID monitoring, set both `legion_btn` and `settings_btn` to `none` in the config.
- `pressed_keys` parameter from `handle_event()` — it was passed but never used; it is dropped in this refactor.
- `import re` — unused, removed.

### New constants (module-level)

- `EVCODE_TO_CONFIG_KEY` — maps evdev button codes to config keys:
  `BTN_Y → "btn_y"`, `BTN_A → "btn_a"`, `BTN_X → "btn_x"`, `BTN_B → "btn_b"`,
  `BTN_TL → "btn_lb"`, `BTN_TR → "btn_rb"`,
  `BTN_START → "btn_view"`, `BTN_SELECT → "btn_menu"`,
  `BTN_THUMBL → "btn_l3"`, `BTN_THUMBR → "btn_r3"`
  (All 10 entries — `btn_l3` and `btn_r3` are included so their config bindings take effect.)
- `ACTION_TO_EVKEY` — maps action strings to evdev key codes:
  `"arrow_up" → KEY_UP`, `"arrow_down" → KEY_DOWN`, `"arrow_left" → KEY_LEFT`, `"arrow_right" → KEY_RIGHT`,
  `"key_y" → KEY_Y`, `"key_return" → KEY_ENTER`, `"key_esc" → KEY_ESC`

### New `StickKeys` class

When a thumbstick is bound to `arrow_keys`, a `StickKeys` instance converts analog deflection to directional key presses:
- Threshold: 0.5 (fraction of full deflection)
- X axis: `KEY_LEFT` (negative) / `KEY_RIGHT` (positive)
- Y axis: `KEY_UP` (negative) / `KEY_DOWN` (positive)
- Key pressed when threshold crossed; released when stick returns inside threshold
- X and Y are independent — diagonals produce two simultaneous keys

**`StickKeys` method:** `update_axis(code: int, raw_value: int)` — `code` is one of the axis ecodes the instance was constructed with (`ABS_LS_X`/`ABS_LS_Y` or `ABS_RS_X`/`ABS_RS_Y`); `raw_value` is the raw evdev axis value (−32767 to 32767). Codes not matching the instance's axes are ignored. Returns nothing.

### Updated `handle_event()`

Signature: `handle_event(event, state, ui, dpad, ls_keys, rs_keys, cfg)`

`ls_keys` and `rs_keys` are `StickKeys` instances or `None`. `handle_event()` null-checks before calling them.

**Axis events:**
- Left stick (`ABS_X` / `ABS_Y`): if `cfg["left_stick"] == "mouse"` → update `State`; if `"arrow_keys"` → call `ls_keys.update_axis()`; if `"none"` → do nothing. State is **only** updated for sticks configured as `"mouse"` — sticks in other modes do not feed `State`, preventing ghost mouse movement.
- Right stick (`ABS_RX` / `ABS_RY`): same logic with `right_stick` / `rs_keys`
- D-pad (`ABS_HAT0X` / `ABS_HAT0Y`): if `cfg["dpad"] == "arrow_keys"` → call `dpad.update()`; if `"none"` → do nothing

**Key events:**
1. Look up `event.code` in `EVCODE_TO_CONFIG_KEY`; if not found, ignore
2. Read action from `cfg`
3. Ignore autorepeat (`value == 2`)
4. Dispatch:
   - `mouse_left` → write `BTN_LEFT` press/release to UInput
   - `mouse_right` → write `BTN_RIGHT` press/release to UInput
   - `lock_screen` → call `lock_screen()` on press only (value == 1)
   - `osk` → call `toggle_osk()` on press only (value == 1)
   - key in `ACTION_TO_EVKEY` → write the mapped key press/release to UInput
   - `none` → do nothing

### Updated `lock_hidraw_reader()`

Signature: `lock_hidraw_reader(stop_event, cfg, ui)`

Receives the UInput device to emit key/mouse events for non-lock actions.

If `find_legion_hidraw()` returns `None`, prints a warning and returns — same error handling as current code.

Detects **both rising edge** (bit newly set: `btns & ~prev_btns`) and **falling edge** (bit newly cleared: `~btns & prev_btns`) from byte 18 of the HID report.

For each edge, identifies which HID button changed (bit 7 = `legion_btn`, bit 6 = `settings_btn`) and dispatches:
- `lock_screen`: call `lock_screen()` on rising edge only
- `osk`: call `toggle_osk()` on rising edge only
- `mouse_left` / `mouse_right`: write `BTN_LEFT` / `BTN_RIGHT` to UInput (value=1 on rising, value=0 on falling)
- key in `ACTION_TO_EVKEY`: write key to UInput (value=1 on rising, value=0 on falling)
- `none`: ignore

### Updated `main()`

- Calls `load_config()` at startup
- Creates `StickKeys(ui, ABS_LS_X, ABS_LS_Y)` as `ls_keys` only if `cfg["left_stick"] == "arrow_keys"`, else `ls_keys = None`; same for `rs_keys`
- Starts `lock_hidraw_reader` thread only when `cfg["legion_btn"] != "none"` or `cfg["settings_btn"] != "none"`
- Passes cfg, ls_keys, rs_keys to `handle_event()`
- Passes cfg, ui to `lock_hidraw_reader()`

### New `toggle_osk()`

Toggles the GNOME on-screen keyboard by reading and flipping the gsettings key:

```
gsettings get org.gnome.desktop.a11y.applications screen-keyboard-enabled
gsettings set org.gnome.desktop.a11y.applications screen-keyboard-enabled <flipped>
```

Called on button press only (rising edge), same as `lock_screen()`. Runs via `subprocess`. Errors are silently ignored (non-blocking, same pattern as `lock_screen()`).

### Updated `create_virtual_device()`

Adds `KEY_Y`, `KEY_ENTER`, `KEY_ESC` to the EV_KEY capabilities list.

---

## Action Display Labels

| Action string | Display label |
|---|---|
| `mouse` | Mouse cursor |
| `arrow_keys` | Arrow keys |
| `lock_screen` | Lock screen |
| `arrow_up` | Arrow key: Up |
| `arrow_down` | Arrow key: Down |
| `arrow_left` | Arrow key: Left |
| `arrow_right` | Arrow key: Right |
| `mouse_left` | Mouse left click |
| `mouse_right` | Mouse right click |
| `key_y` | Key: Y |
| `key_return` | Key: Return/Enter |
| `key_esc` | Key: Esc |
| `osk` | Toggle on-screen keyboard |
| `none` | Disabled |

---

## File Changes Summary

All changes are in `legion_go_mapper.py` (single file). No new files except the generated config JSON.

- Add `import json`; remove `import re`
- Add config section: `CONFIG_PATH`, `CONTROLS`, action lists, `DEFAULT_CONFIG`, `load_config()`, `save_config()`, `configure_mode()`
- Add `EVCODE_TO_CONFIG_KEY`, `ACTION_TO_EVKEY` constants
- Add `StickKeys` class
- Update `create_virtual_device()`, `handle_event()`, `lock_hidraw_reader()`, `main()`
- Remove `FACE_TO_ARROW`, `MOUSE_LEFT_BTN`, `MOUSE_RIGHT_BTN`, `LOCK_BTN_ENABLED`, `pressed_keys` parameter
