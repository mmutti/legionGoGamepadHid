# Configure Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `--configure` interactive CLI to `legion_go_mapper.py` that lets the user rebind all Legion Go controls and persists the config to JSON, replacing all hardcoded button/axis mappings.

**Architecture:** All changes live in `legion_go_mapper.py`. A new JSON config file (`~/.config/legion-go-mapper/config.json`) replaces hardcoded dicts. `handle_event()` and `lock_hidraw_reader()` dispatch via the loaded config at runtime. Default config reproduces the current hardcoded behaviour exactly, so existing installs need no migration.

**Tech Stack:** Python 3, python3-evdev, pytest (tests only), gsettings (GNOME OSK toggle), systemctl (service restart).

**Spec:** `docs/superpowers/specs/2026-03-22-configure-mode-design.md`

---

### Task 1: Config data structures and load/save

**Files:**
- Modify: `legion_go_mapper.py` — add config section after the `# ── Configuration ──` block
- Create: `tests/test_mapper.py`

- [ ] **Step 1: Install pytest (if not already done)**

```bash
pip install pytest --break-system-packages -q
python3 -m pytest --version   # expect: pytest 9.x
```

- [ ] **Step 2: Write failing tests for load_config() and save_config()**

Create `tests/test_mapper.py`:

```python
"""Tests for legion_go_mapper config system."""
import json
import os
import sys
import tempfile
import pytest

# legion_go_mapper imports evdev at module level; mock it before import
import unittest.mock as mock
sys.modules.setdefault("evdev", mock.MagicMock())
sys.modules.setdefault("evdev.ecodes", mock.MagicMock())

# Provide minimal ecodes attrs that the module uses at import time
import evdev
evdev.ecodes.EV_KEY = 1
evdev.ecodes.EV_ABS = 3
evdev.ecodes.EV_REL = 2
evdev.ecodes.ABS_X = 0
evdev.ecodes.ABS_Y = 1
evdev.ecodes.ABS_RX = 3
evdev.ecodes.ABS_RY = 4
evdev.ecodes.ABS_HAT0X = 16
evdev.ecodes.ABS_HAT0Y = 17
evdev.ecodes.REL_X = 0
evdev.ecodes.REL_Y = 1
for name in ["BTN_Y","BTN_A","BTN_X","BTN_B","BTN_TL","BTN_TR",
             "BTN_START","BTN_SELECT","BTN_THUMBL","BTN_THUMBR",
             "BTN_LEFT","BTN_RIGHT",
             "KEY_UP","KEY_DOWN","KEY_LEFT","KEY_RIGHT",
             "KEY_Y","KEY_ENTER","KEY_ESC"]:
    setattr(evdev.ecodes, name, hash(name) % 1000)

import importlib, legion_go_mapper as m
importlib.reload(m)  # pick up the mocked ecodes values


def test_load_config_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "CONFIG_PATH", str(tmp_path / "config.json"))
    cfg = m.load_config()
    assert cfg == m.DEFAULT_CONFIG


def test_load_config_partial_json(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"btn_y": "key_return"}))
    monkeypatch.setattr(m, "CONFIG_PATH", str(p))
    cfg = m.load_config()
    assert cfg["btn_y"] == "key_return"
    assert cfg["left_stick"] == m.DEFAULT_CONFIG["left_stick"]


def test_load_config_invalid_json(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text("not json {{{")
    monkeypatch.setattr(m, "CONFIG_PATH", str(p))
    cfg = m.load_config()
    assert cfg == m.DEFAULT_CONFIG


def test_load_config_unknown_action_loaded_as_is(tmp_path, monkeypatch):
    # Unknown actions are loaded as-is (not replaced at load time).
    # They are silently treated as "none" at dispatch time.
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"btn_y": "future_unknown_action_xyz"}))
    monkeypatch.setattr(m, "CONFIG_PATH", str(p))
    cfg = m.load_config()
    assert cfg["btn_y"] == "future_unknown_action_xyz"


def test_save_config_round_trip(tmp_path, monkeypatch):
    p = tmp_path / "sub" / "config.json"
    monkeypatch.setattr(m, "CONFIG_PATH", str(p))
    cfg = dict(m.DEFAULT_CONFIG)
    cfg["btn_y"] = "key_esc"
    m.save_config(cfg)
    assert p.exists()
    loaded = json.loads(p.read_text())
    assert loaded["btn_y"] == "key_esc"
```

- [ ] **Step 3: Run tests — expect ImportError or AttributeError (functions don't exist yet)**

```bash
cd /home/matteo/src/legionGoGamepadHid
python3 -m pytest tests/test_mapper.py -v 2>&1 | head -40
```

Expected: failures referencing missing `load_config`, `save_config`, `DEFAULT_CONFIG`.

- [ ] **Step 4: Add config data structures and load/save to `legion_go_mapper.py`**

Add `import json` to the imports block (after `import fcntl`). Remove `import re` (unused).

Add a new section after the `# ── Configuration ──` block, before `# ── Button / axis assignments ──`:

```python
# ── Button configuration ───────────────────────────────────────────────────────

CONFIG_PATH = os.path.expanduser("~/.config/legion-go-mapper/config.json")

# All controls on the Legion Go and their type.
# LT/RT are analog (ABS_Z/ABS_RZ) and are not configurable here.
# Types: "axis" = thumbstick, "dpad" = hat switch,
#        "button" = digital button, (legion/settings handled as "button" via HID)
CONTROLS = [
    ("left_stick",   "axis",   "Left thumbstick"),
    ("right_stick",  "axis",   "Right thumbstick"),
    ("dpad",         "dpad",   "D-pad"),
    ("btn_y",        "button", "Y button"),
    ("btn_a",        "button", "A button"),
    ("btn_x",        "button", "X button"),
    ("btn_b",        "button", "B button"),
    ("btn_lb",       "button", "Left bumper (LB)"),
    ("btn_rb",       "button", "Right bumper (RB)"),
    ("btn_view",     "button", "View/Back button"),
    ("btn_menu",     "button", "Menu/Start button"),
    ("btn_l3",       "button", "L3 (left stick click)"),
    ("btn_r3",       "button", "R3 (right stick click)"),
    ("legion_btn",   "button", "Legion L button"),
    ("settings_btn", "button", "Settings button"),
]

AXIS_ACTIONS = [
    ("mouse",      "Mouse cursor"),
    ("arrow_keys", "Arrow keys"),
]

DPAD_ACTIONS = [
    ("arrow_keys", "Arrow keys"),
]

BUTTON_ACTIONS = [
    ("lock_screen",  "Lock screen"),
    ("osk",          "Toggle on-screen keyboard"),
    ("arrow_up",     "Arrow key: Up"),
    ("arrow_down",   "Arrow key: Down"),
    ("arrow_left",   "Arrow key: Left"),
    ("arrow_right",  "Arrow key: Right"),
    ("mouse_left",   "Mouse left click"),
    ("mouse_right",  "Mouse right click"),
    ("key_y",        "Key: Y"),
    ("key_return",   "Key: Return/Enter"),
    ("key_esc",      "Key: Esc"),
]

ACTIONS_FOR_TYPE = {
    "axis":   AXIS_ACTIONS,
    "dpad":   DPAD_ACTIONS,
    "button": BUTTON_ACTIONS,
}

# "none" is not in any action list (it is the "0. Disabled" option on the rebind screen),
# but it must be in ACTION_LABELS so current bindings display correctly.
ACTION_LABELS = {a: l for actions in ACTIONS_FOR_TYPE.values() for a, l in actions}
ACTION_LABELS["none"] = "Disabled"

DEFAULT_CONFIG = {
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
    "settings_btn": "lock_screen",
}


def load_config():
    """Load config from JSON, merging with DEFAULT_CONFIG for any missing keys."""
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(data)
        return cfg
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)


def save_config(cfg):
    """Write config to JSON, creating the directory if needed. Raises OSError on failure."""
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"Configuration saved to {CONFIG_PATH}")
```

- [ ] **Step 5: Run tests — expect all 5 to pass**

```bash
python3 -m pytest tests/test_mapper.py -v
```

Expected output: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add legion_go_mapper.py tests/test_mapper.py
git commit -m "feat: add config data structures and load/save"
```

---

### Task 2: configure_mode() interactive menu

**Files:**
- Modify: `legion_go_mapper.py` — add `configure_mode()` after `save_config()`
- Modify: `tests/test_mapper.py` — add menu tests

- [ ] **Step 1: Write failing tests for configure_mode()**

Append to `tests/test_mapper.py`:

```python
# ── configure_mode tests ──────────────────────────────────────────────────────

def test_configure_quit_no_save(tmp_path, monkeypatch, capsys):
    p = tmp_path / "config.json"
    monkeypatch.setattr(m, "CONFIG_PATH", str(p))
    inputs = iter(["q"])
    monkeypatch.setattr("builtins.input", lambda _="": next(inputs))
    m.configure_mode()
    assert not p.exists()
    assert "no changes" in capsys.readouterr().out.lower()


def test_configure_save_writes_json(tmp_path, monkeypatch, capsys):
    p = tmp_path / "cfg" / "config.json"
    monkeypatch.setattr(m, "CONFIG_PATH", str(p))
    # Pick control 4 (btn_y), choose action 9 (key_return = index 8 in BUTTON_ACTIONS),
    # then save.  We skip the service restart by patching subprocess.run.
    inputs = iter(["4", "9", "s"])
    monkeypatch.setattr("builtins.input", lambda _="": next(inputs))
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **kw: mock.MagicMock(returncode=0))
    m.configure_mode()
    assert p.exists()
    saved = json.loads(p.read_text())
    assert saved["btn_y"] == "key_return"


def test_configure_invalid_input_reprompts(tmp_path, monkeypatch, capsys):
    p = tmp_path / "config.json"
    monkeypatch.setattr(m, "CONFIG_PATH", str(p))
    inputs = iter(["999", "abc", "q"])
    monkeypatch.setattr("builtins.input", lambda _="": next(inputs))
    m.configure_mode()
    out = capsys.readouterr().out
    assert out.count("Invalid input") >= 2


def test_configure_makedirs_failure_returns_to_menu(tmp_path, monkeypatch, capsys):
    # Simulate save_config raising OSError (e.g. permission denied)
    monkeypatch.setattr(m, "save_config", mock.MagicMock(side_effect=OSError("Permission denied")))
    inputs = iter(["s", "q"])
    monkeypatch.setattr("builtins.input", lambda _="": next(inputs))
    m.configure_mode()
    out = capsys.readouterr().out
    assert "Error" in out or "error" in out
```

- [ ] **Step 2: Run tests — expect failures (configure_mode not defined)**

```bash
python3 -m pytest tests/test_mapper.py -v -k "configure"
```

Expected: 4 failures.

- [ ] **Step 3: Implement configure_mode()**

Add after `save_config()` in `legion_go_mapper.py`:

```python
def configure_mode():
    """Interactive CLI to rebind all Legion Go controls."""
    cfg = load_config()

    while True:
        print("\nLegion Go Gamepad — Button Configuration")
        print("=" * 43)
        print(f"Config: {CONFIG_PATH}\n")

        for i, (key, ctype, name) in enumerate(CONTROLS, 1):
            label = ACTION_LABELS.get(cfg.get(key, "none"), cfg.get(key, "none"))
            print(f"  {i:2d}.  {name:<28}  [{label}]")

        print("\n  s.  Save and restart service")
        print("  q.  Quit without saving\n")
        choice = input("Enter number to reconfigure, 's' to save, 'q' to quit: ").strip().lower()

        if choice == "q":
            print("Quit — no changes saved.")
            return

        if choice == "s":
            _save_and_restart(cfg)
            return

        try:
            idx = int(choice) - 1
        except ValueError:
            print("  Invalid input.")
            continue

        if not (0 <= idx < len(CONTROLS)):
            print("  Invalid input.")
            continue

        key, ctype, name = CONTROLS[idx]
        actions = ACTIONS_FOR_TYPE[ctype]

        while True:
            print(f"\n  {name} — choose action:")
            for j, (akey, alabel) in enumerate(actions, 1):
                marker = "  ← current" if cfg.get(key) == akey else ""
                print(f"  {j:3d}. {alabel}{marker}")
            print(f"    0. Disabled{('  ← current' if cfg.get(key) == 'none' else '')}")
            achoice = input("\n  Enter number: ").strip()

            if achoice == "0":
                cfg[key] = "none"
                print(f"  → {name} set to: Disabled")
                break

            try:
                aidx = int(achoice) - 1
            except ValueError:
                print("  Invalid input.")
                continue

            if not (0 <= aidx < len(actions)):
                print("  Invalid input.")
                continue

            cfg[key] = actions[aidx][0]
            print(f"  → {name} set to: {actions[aidx][1]}")
            break


def _save_and_restart(cfg):
    """Write config to disk via save_config() and restart the systemd service."""
    try:
        save_config(cfg)   # handles makedirs + json write, prints confirmation
    except OSError as e:
        print(f"  {e}")
        print("  Error: could not save config.")
        return

    result = subprocess.run(
        ["systemctl", "--user", "restart", "legion-go-mapper"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print("Service restarted.")
    else:
        print(result.stderr.strip())
        print("  Warning: could not restart service — run: systemctl --user restart legion-go-mapper")
```

- [ ] **Step 4: Run tests — expect all configure tests to pass**

```bash
python3 -m pytest tests/test_mapper.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add legion_go_mapper.py tests/test_mapper.py
git commit -m "feat: add configure_mode interactive menu"
```

---

### Task 3: New lookup tables, StickKeys, and toggle_osk()

**Files:**
- Modify: `legion_go_mapper.py` — add after the axis/dpad constants section
- Modify: `tests/test_mapper.py` — add StickKeys and toggle_osk tests

- [ ] **Step 1: Write failing tests**

Append to `tests/test_mapper.py`:

```python
# ── StickKeys tests ───────────────────────────────────────────────────────────

def make_mock_ui():
    ui = mock.MagicMock()
    ui.write = mock.MagicMock()
    ui.syn  = mock.MagicMock()
    return ui


def test_stickkeys_no_output_inside_deadzone():
    ui = make_mock_ui()
    sk = m.StickKeys(ui, m.ABS_LS_X, m.ABS_LS_Y)
    # deflection well below threshold (0.5 * 32767 ≈ 16383)
    sk.update_axis(m.ABS_LS_X, 5000)
    ui.write.assert_not_called()


def test_stickkeys_right_key_on_threshold_cross():
    ui = make_mock_ui()
    sk = m.StickKeys(ui, m.ABS_LS_X, m.ABS_LS_Y)
    # cross threshold to the right
    sk.update_axis(m.ABS_LS_X, 20000)
    ui.write.assert_called_once_with(evdev.ecodes.EV_KEY, evdev.ecodes.KEY_RIGHT, 1)
    ui.syn.assert_called_once()


def test_stickkeys_release_on_return_to_centre():
    ui = make_mock_ui()
    sk = m.StickKeys(ui, m.ABS_LS_X, m.ABS_LS_Y)
    sk.update_axis(m.ABS_LS_X, 20000)   # press
    ui.reset_mock()
    sk.update_axis(m.ABS_LS_X, 1000)    # release
    ui.write.assert_called_once_with(evdev.ecodes.EV_KEY, evdev.ecodes.KEY_RIGHT, 0)


def test_stickkeys_diagonal_presses_two_keys():
    ui = make_mock_ui()
    sk = m.StickKeys(ui, m.ABS_LS_X, m.ABS_LS_Y)
    sk.update_axis(m.ABS_LS_X, 20000)
    sk.update_axis(m.ABS_LS_Y, -20000)
    calls = ui.write.call_args_list
    pressed_keys = {c.args[1] for c in calls if c.args[2] == 1}
    assert evdev.ecodes.KEY_RIGHT in pressed_keys
    assert evdev.ecodes.KEY_UP    in pressed_keys


def test_stickkeys_ignores_wrong_axis():
    ui = make_mock_ui()
    sk = m.StickKeys(ui, m.ABS_LS_X, m.ABS_LS_Y)
    sk.update_axis(m.ABS_RS_X, 30000)   # wrong axes
    ui.write.assert_not_called()


# ── toggle_osk tests ──────────────────────────────────────────────────────────

def test_toggle_osk_enables_when_off(monkeypatch):
    calls = []
    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "gsettings" and cmd[1] == "get":
            r = mock.MagicMock()
            r.stdout = "false\n"
            return r
        return mock.MagicMock()
    monkeypatch.setattr(m.subprocess, "run", fake_run)
    m.toggle_osk()
    set_call = next(c for c in calls if "set" in c)
    assert set_call[-1] == "true"


def test_toggle_osk_disables_when_on(monkeypatch):
    calls = []
    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "gsettings" and cmd[1] == "get":
            r = mock.MagicMock()
            r.stdout = "true\n"
            return r
        return mock.MagicMock()
    monkeypatch.setattr(m.subprocess, "run", fake_run)
    m.toggle_osk()
    set_call = next(c for c in calls if "set" in c)
    assert set_call[-1] == "false"
```

- [ ] **Step 2: Run tests — expect failures (StickKeys, toggle_osk not defined)**

```bash
python3 -m pytest tests/test_mapper.py -v -k "stickkeys or toggle_osk"
```

Expected: all fail.

- [ ] **Step 3: Add lookup tables, StickKeys, and toggle_osk() to legion_go_mapper.py**

Replace the `# ── Button / axis assignments ──` section. Remove `FACE_TO_ARROW`, `MOUSE_LEFT_BTN`, `MOUSE_RIGHT_BTN` entirely. Keep the axis and dpad constants (`ABS_LS_X` etc.). Add after them:

```python
# Map evdev button code → config key
EVCODE_TO_CONFIG_KEY = {
    ecodes.BTN_Y:      "btn_y",
    ecodes.BTN_A:      "btn_a",
    ecodes.BTN_X:      "btn_x",
    ecodes.BTN_B:      "btn_b",
    ecodes.BTN_TL:     "btn_lb",
    ecodes.BTN_TR:     "btn_rb",
    ecodes.BTN_START:  "btn_view",
    ecodes.BTN_SELECT: "btn_menu",
    ecodes.BTN_THUMBL: "btn_l3",
    ecodes.BTN_THUMBR: "btn_r3",
}

# Map action string → evdev key code (for keyboard actions)
ACTION_TO_EVKEY = {
    "arrow_up":    ecodes.KEY_UP,
    "arrow_down":  ecodes.KEY_DOWN,
    "arrow_left":  ecodes.KEY_LEFT,
    "arrow_right": ecodes.KEY_RIGHT,
    "key_y":       ecodes.KEY_Y,
    "key_return":  ecodes.KEY_ENTER,
    "key_esc":     ecodes.KEY_ESC,
}
```

Add `StickKeys` class after `DpadKeys`:

```python
class StickKeys:
    """Converts thumbstick deflection into directional key presses."""

    THRESHOLD = 0.5

    def __init__(self, ui: UInput, x_axis_code: int, y_axis_code: int):
        self.ui = ui
        self._x_axis = x_axis_code
        self._y_axis = y_axis_code
        self._x = 0.0
        self._y = 0.0
        self._active: dict[int, bool] = {}

    def update_axis(self, code: int, raw_value: int):
        norm = raw_value / AXIS_MAX
        if code == self._x_axis:
            self._x = norm
        elif code == self._y_axis:
            self._y = norm
        else:
            return
        t = self.THRESHOLD
        self._set(ecodes.KEY_LEFT,  self._x < -t)
        self._set(ecodes.KEY_RIGHT, self._x >  t)
        self._set(ecodes.KEY_UP,    self._y < -t)
        self._set(ecodes.KEY_DOWN,  self._y >  t)

    def _set(self, key: int, pressed: bool):
        was = self._active.get(key, False)
        if pressed == was:
            return
        self._active[key] = pressed
        self.ui.write(ecodes.EV_KEY, key, 1 if pressed else 0)
        self.ui.syn()
```

Add `toggle_osk()` next to `lock_screen()`:

```python
def toggle_osk():
    """Toggle the GNOME on-screen keyboard via gsettings."""
    _KEY = "org.gnome.desktop.a11y.applications"
    _PROP = "screen-keyboard-enabled"
    try:
        result = subprocess.run(
            ["gsettings", "get", _KEY, _PROP],
            capture_output=True, text=True, check=False,
        )
        current = result.stdout.strip() == "true"
        subprocess.run(
            ["gsettings", "set", _KEY, _PROP, "false" if current else "true"],
            check=False,
        )
    except OSError:
        pass
```

- [ ] **Step 4: Run tests — expect all new tests to pass**

```bash
python3 -m pytest tests/test_mapper.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add legion_go_mapper.py tests/test_mapper.py
git commit -m "feat: add StickKeys, toggle_osk, and lookup tables"
```

---

### Task 4: Update create_virtual_device() and handle_event()

**Files:**
- Modify: `legion_go_mapper.py`

- [ ] **Step 1: Update create_virtual_device()**

Add `ecodes.KEY_Y`, `ecodes.KEY_ENTER`, `ecodes.KEY_ESC` to the `EV_KEY` capability list:

```python
def create_virtual_device():
    capabilities = {
        ecodes.EV_KEY: [
            ecodes.KEY_UP, ecodes.KEY_DOWN, ecodes.KEY_LEFT, ecodes.KEY_RIGHT,
            ecodes.BTN_LEFT, ecodes.BTN_RIGHT,
            ecodes.KEY_Y, ecodes.KEY_ENTER, ecodes.KEY_ESC,
        ],
        ecodes.EV_REL: [
            ecodes.REL_X, ecodes.REL_Y,
        ],
    }
    return UInput(capabilities, name="LegionGo-Mapper", version=0x3)
```

- [ ] **Step 2: Replace handle_event()**

Remove the old `handle_event()` entirely and replace with:

```python
def handle_event(event, state: State, ui: UInput, dpad: DpadKeys,
                 ls_keys, rs_keys, cfg: dict):
    """Process a single evdev event using the loaded config."""

    if event.type == ecodes.EV_ABS:
        code = event.code
        if code in (ABS_LS_X, ABS_LS_Y):
            action = cfg["left_stick"]
            if action == "mouse":
                state.update_axis(code, event.value)
            elif action == "arrow_keys" and ls_keys is not None:
                ls_keys.update_axis(code, event.value)
        elif code in (ABS_RS_X, ABS_RS_Y):
            action = cfg["right_stick"]
            if action == "mouse":
                state.update_axis(code, event.value)
            elif action == "arrow_keys" and rs_keys is not None:
                rs_keys.update_axis(code, event.value)
        elif code in (ABS_DPAD_X, ABS_DPAD_Y):
            if cfg["dpad"] == "arrow_keys":
                dpad.update(code, event.value)

    elif event.type == ecodes.EV_KEY:
        cfg_key = EVCODE_TO_CONFIG_KEY.get(event.code)
        if cfg_key is None:
            return
        action = cfg.get(cfg_key, "none")
        val    = event.value   # 0=release, 1=press, 2=repeat
        if val == 2:
            return   # ignore autorepeat

        if action == "mouse_left":
            ui.write(ecodes.EV_KEY, ecodes.BTN_LEFT, val)
            ui.syn()
        elif action == "mouse_right":
            ui.write(ecodes.EV_KEY, ecodes.BTN_RIGHT, val)
            ui.syn()
        elif action == "lock_screen":
            if val == 1:
                lock_screen()
        elif action == "osk":
            if val == 1:
                toggle_osk()
        elif action in ACTION_TO_EVKEY:
            ui.write(ecodes.EV_KEY, ACTION_TO_EVKEY[action], val)
            ui.syn()
        # "none" or unrecognised: do nothing
```

- [ ] **Step 3: Run full test suite**

```bash
python3 -m pytest tests/test_mapper.py -v
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add legion_go_mapper.py
git commit -m "feat: replace hardcoded handle_event with config-driven dispatch"
```

---

### Task 5: Update lock_hidraw_reader() and main()

**Files:**
- Modify: `legion_go_mapper.py`

- [ ] **Step 1: Replace lock_hidraw_reader()**

Update the signature to `lock_hidraw_reader(stop_event, cfg, ui)` and replace the body:

```python
def lock_hidraw_reader(stop_event: threading.Event, cfg: dict, ui: UInput):
    """
    Watch the Legion Go HID report for Legion L / Settings button events.
    Dispatches the configured action for each button on both press and release.

    Byte 18 of the 64-byte report ID 0x74:
      bit 7 (0x80) = Legion L button   → cfg["legion_btn"]
      bit 6 (0x40) = Settings button   → cfg["settings_btn"]
    """
    path = find_legion_hidraw()
    if path is None:
        print("Lock-screen: Legion Go HID device not found — feature disabled.")
        print("  Make sure the controller is connected and you have read access to /dev/hidraw*.")
        return
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except (PermissionError, OSError) as e:
        print(f"Lock-screen: cannot open {path}: {e}")
        print("  Try adding a udev rule: KERNEL==\"hidraw*\", ATTRS{idVendor}==\"17ef\", MODE=\"0660\", GROUP=\"input\"")
        return

    _HID_BTNS = (
        (0x80, cfg.get("legion_btn",   "none")),
        (0x40, cfg.get("settings_btn", "none")),
    )

    print(f"Lock-screen: monitoring {path} (Legion L + Settings)")
    prev_btns = 0
    try:
        while not stop_event.is_set():
            ready, _, _ = select.select([fd], [], [], 0.5)
            if not ready:
                continue
            try:
                pkt = os.read(fd, 64)
            except OSError:
                break
            if len(pkt) < _LEGION_BTN_BYTE + 1 or pkt[2] != _LEGION_REPORT_ID:
                continue
            btns = pkt[_LEGION_BTN_BYTE] & _LEGION_BTN_MASK
            if btns == prev_btns:
                prev_btns = btns
                continue

            for mask, action in _HID_BTNS:
                rising  = bool(btns & mask) and not bool(prev_btns & mask)
                falling = not bool(btns & mask) and bool(prev_btns & mask)
                if not rising and not falling:
                    continue
                val = 1 if rising else 0

                if action == "lock_screen":
                    if rising:
                        lock_screen()
                elif action == "osk":
                    if rising:
                        toggle_osk()
                elif action == "mouse_left":
                    ui.write(ecodes.EV_KEY, ecodes.BTN_LEFT, val)
                    ui.syn()
                elif action == "mouse_right":
                    ui.write(ecodes.EV_KEY, ecodes.BTN_RIGHT, val)
                    ui.syn()
                elif action in ACTION_TO_EVKEY:
                    ui.write(ecodes.EV_KEY, ACTION_TO_EVKEY[action], val)
                    ui.syn()

            prev_btns = btns
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
```

- [ ] **Step 2: Update main()**

In `main()`, after the existing `--detect-all` / `--watch-hidraw` / `--detect` checks, add:

```python
    if "--configure" in sys.argv:
        configure_mode()
        return
```

Then after creating the virtual device `ui`, load the config and set up StickKeys:

```python
    cfg = load_config()

    ls_keys = (StickKeys(ui, ABS_LS_X, ABS_LS_Y)
               if cfg["left_stick"] == "arrow_keys" else None)
    rs_keys = (StickKeys(ui, ABS_RS_X, ABS_RS_Y)
               if cfg["right_stick"] == "arrow_keys" else None)
```

Replace the locker thread start block. Old:
```python
    if LOCK_BTN_ENABLED:
        locker = threading.Thread(target=lock_hidraw_reader, args=(stop_event,), daemon=True)
        locker.start()
```
New:
```python
    if cfg.get("legion_btn", "none") != "none" or cfg.get("settings_btn", "none") != "none":
        locker = threading.Thread(
            target=lock_hidraw_reader, args=(stop_event, cfg, ui), daemon=True
        )
        locker.start()
```

Update the `handle_event` call in the event loop. Old:
```python
            handle_event(event, state, ui, dpad, pressed)
```
New:
```python
            handle_event(event, state, ui, dpad, ls_keys, rs_keys, cfg)
```

Remove the `pressed = {}` line above the loop (it fed the now-removed `pressed_keys` parameter).

- [ ] **Step 3: Remove LOCK_BTN_ENABLED and the old lock-screen config block**

Delete these lines from the top of the file (they are now replaced by the config system):
```python
LOCK_BTN_ENABLED = True
```
Also remove the old `# ── Lock-screen buttons ──` comment block and `# ── Legion Go HID constants ──` block preamble if it still references `LOCK_BTN_ENABLED`. Keep the `_LENOVO_VID`, `_LEGION_GO_PIDS`, etc. constants — they are still used by `find_legion_hidraw()`.

- [ ] **Step 4: Run full test suite**

```bash
python3 -m pytest tests/test_mapper.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Manual smoke test — run the mapper**

```bash
sudo PYTHONPATH=/home/matteo/.local/lib/python3.13/site-packages python3 ~/src/legionGoGamepadHid/legion_go_mapper.py
```

Expected: starts cleanly, prints `Lock-screen: monitoring /dev/hidrawN`. Thumbsticks move mouse, face buttons send arrow keys, Legion button locks screen — same as before.

- [ ] **Step 6: Manual smoke test — run configure mode**

```bash
python3 ~/src/legionGoGamepadHid/legion_go_mapper.py --configure
```

Expected: shows the 15-control menu. Navigate a few items, save. Service restarts.

- [ ] **Step 7: Commit**

```bash
git add legion_go_mapper.py
git commit -m "feat: wire config into main, lock_hidraw_reader, and --configure flag"
```

---

### Task 6: Update README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add configure usage to README**

Add a new section after `## Useful commands`:

```markdown
## Configuring button bindings

Run the interactive configurator:

```bash
python3 legion_go_mapper.py --configure
```

Use the numbered menu to rebind any control. Press `s` to save and auto-restart the service, `q` to quit without saving.

Config is stored at `~/.config/legion-go-mapper/config.json`.
```

Also update the `## Button mapping` table to note that bindings are configurable, and remove the hardcoded list or add a note that defaults are shown.

Update the `## Tuning` section — remove the now-deleted constants (`DEADZONE`, `MOUSE_SPEED`, `ACCEL_EXPONENT`, `POLL_HZ` are still present, but `MOUSE_LEFT_BTN` and `MOUSE_RIGHT_BTN` are gone; update accordingly).

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add --configure usage to README"
```
