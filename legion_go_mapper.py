#!/usr/bin/env python3
"""
Legion Go Gamepad → Mouse/Keyboard Mapper
==========================================
Maps:
  Left & Right thumbsticks  → mouse cursor movement
  Right XYAB buttons        → arrow keys (Y=Up, A=Down, X=Left, B=Right)
  Left D-pad                → arrow keys
  Left button above D-pad   → left mouse click  (BTN_SELECT by default)
  Left button below D-pad   → right mouse click (BTN_MODE   by default)
  Legion L + Settings btns  → lock screen (loginctl lock-session, auto-detected)

Usage:
  python3 legion_go_mapper.py               # run mapper
  python3 legion_go_mapper.py --detect      # print raw events from gamepad
  python3 legion_go_mapper.py --detect-all  # print raw events from ALL input
                                            # devices (use this to find the
                                            # Legion / Settings button codes)
  python3 legion_go_mapper.py --watch-hidraw=/dev/hidrawN
                                            # show raw packet changes on one
                                            # hidraw device (diagnostic)

Requirements:
  sudo apt install python3-evdev
  sudo usermod -aG input $USER           # then log out/in, or run with sudo
  # For uinput (virtual device creation):
  sudo modprobe uinput
  echo 'uinput' | sudo tee /etc/modules-load.d/uinput.conf
  sudo chmod 0660 /dev/uinput
  # Or add a udev rule (see setup instructions at bottom of this file)
"""

import fcntl
import glob
import json
import os
import select
import struct
import subprocess
import sys
import time
import math
import threading
import evdev
from evdev import ecodes, UInput

# ── Configuration ──────────────────────────────────────────────────────────────

# Gamepad device (auto-detected, but can be forced)
GAMEPAD_DEVICE = None  # e.g. "/dev/input/event10"

# Deadzone: stick deflection fraction below which input is ignored (0.0–1.0)
DEADZONE = 0.12

# Mouse sensitivity: pixels per second at full stick deflection
MOUSE_SPEED = 800.0

# Acceleration curve exponent (1.0 = linear, 2.0 = squared, feels more natural)
ACCEL_EXPONENT = 1.8

# Poll rate for mouse movement (Hz)
POLL_HZ = 120

# Axis ranges — standard Linux gamepad reports –32767 to 32767
AXIS_MAX = 32767.0

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
    ("key_return",     "Key: Return/Enter"),
    ("key_esc",        "Key: Esc"),
    ("key_backspace",  "Key: Backspace"),
    ("key_delete",     "Key: Delete"),
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
            if _save_and_restart(cfg):
                return
            continue

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
    """Write config to disk via save_config() and restart the systemd service.

    Returns True on success (caller should exit the menu), False on save failure
    (caller should stay in the menu so the user can retry or quit explicitly).
    """
    try:
        save_config(cfg)   # handles makedirs + json write, prints confirmation
    except OSError as e:
        print(f"  {e}")
        print("  Error: could not save config.")
        return False

    result = subprocess.run(
        ["systemctl", "--user", "restart", "legion-go-mapper"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print("Service restarted.")
    else:
        print(result.stderr.strip())
        print("  Warning: could not restart service — run: systemctl --user restart legion-go-mapper")
    return True


# ── Button / axis assignments ──────────────────────────────────────────────────

# Thumbstick axes
ABS_LS_X = ecodes.ABS_X
ABS_LS_Y = ecodes.ABS_Y
ABS_RS_X = ecodes.ABS_RX
ABS_RS_Y = ecodes.ABS_RY

# D-pad hat axes
ABS_DPAD_X = ecodes.ABS_HAT0X
ABS_DPAD_Y = ecodes.ABS_HAT0Y

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

# Map action string → evdev key code (for keyboard actions only).
# Non-key actions (mouse_left, mouse_right, lock_screen, osk) are dispatched
# via explicit branches in handle_event() and lock_hidraw_reader().
ACTION_TO_EVKEY = {
    "arrow_up":    ecodes.KEY_UP,
    "arrow_down":  ecodes.KEY_DOWN,
    "arrow_left":  ecodes.KEY_LEFT,
    "arrow_right": ecodes.KEY_RIGHT,
    "key_y":       ecodes.KEY_Y,
    "key_return":    ecodes.KEY_ENTER,
    "key_esc":       ecodes.KEY_ESC,
    "key_backspace": ecodes.KEY_BACKSPACE,
    "key_delete":    ecodes.KEY_DELETE,
}

# ── Legion Go HID constants (from hhd-dev/hhd) ─────────────────────────────────
_LENOVO_VID        = 0x17EF
_LEGION_GO_PIDS    = {0x6182, 0x6183, 0x6184, 0x6185,   # original Legion Go
                      0x61EB, 0x61EC, 0x61ED, 0x61EE}    # 2025 firmware variants
_LEGION_REPORT_ID  = 0x74   # pkt[2] in a raw hidraw read
_LEGION_BTN_BYTE   = 18     # byte index within the 64-byte report
_LEGION_BTN_MASK   = 0xC0   # bit7=Legion L, bit6=Settings
_HIDIOCGRAWINFO    = 0x80084803  # ioctl: get bus/VID/PID

# ── Virtual output device ──────────────────────────────────────────────────────

def create_virtual_device():
    capabilities = {
        ecodes.EV_KEY: [
            ecodes.KEY_UP, ecodes.KEY_DOWN, ecodes.KEY_LEFT, ecodes.KEY_RIGHT,
            ecodes.BTN_LEFT, ecodes.BTN_RIGHT,
            ecodes.KEY_Y, ecodes.KEY_ENTER, ecodes.KEY_ESC, ecodes.KEY_BACKSPACE, ecodes.KEY_DELETE,
        ],
        ecodes.EV_REL: [
            ecodes.REL_X, ecodes.REL_Y,
        ],
    }
    return UInput(capabilities, name="LegionGo-Mapper", version=0x3)


# ── Device discovery ───────────────────────────────────────────────────────────

def find_gamepad():
    """Return the first evdev device that looks like the Legion Go gamepad.

    Matches on capabilities (dual sticks + face buttons) rather than name,
    because the kernel may expose the device as 'Generic X-Box pad'.
    """
    KNOWN_NAMES = ("legion", "x-box pad", "xbox pad", "gamepad")
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except (PermissionError, OSError):
            continue
        caps = dev.capabilities()
        has_abs = ecodes.EV_ABS in caps
        has_key = ecodes.EV_KEY in caps
        if not (has_abs and has_key):
            continue
        abs_codes = [a[0] for a in caps[ecodes.EV_ABS]]
        key_codes = caps[ecodes.EV_KEY]
        # Must have both thumbstick axes and at least the A/B face buttons
        has_sticks = ecodes.ABS_X in abs_codes and ecodes.ABS_RX in abs_codes
        has_face   = ecodes.BTN_A in key_codes and ecodes.BTN_B in key_codes
        name_match = any(n in dev.name.lower() for n in KNOWN_NAMES)
        if has_sticks and has_face and name_match:
            return dev
    return None


# ── Detect mode ───────────────────────────────────────────────────────────────

def detect_mode(dev):
    print(f"Listening on: {dev.path} ({dev.name})")
    print("Press buttons / move sticks to identify event codes. Ctrl+C to stop.\n")
    # Build a code→name map covering both KEY_* and BTN_* ranges
    key_names = {}
    for name, code in ecodes.ecodes.items():
        if isinstance(code, int) and (name.startswith("KEY_") or name.startswith("BTN_")):
            key_names.setdefault(code, name)
    abs_names = {v: k for k, v in ecodes.ABS.items() if isinstance(v, int)}
    try:
        for event in dev.read_loop():
            if event.type == ecodes.EV_KEY:
                name = key_names.get(event.code, f"0x{event.code:03x}")
                state = {0: "released", 1: "pressed ", 2: "repeat  "}.get(event.value, str(event.value))
                print(f"  KEY  {state}  code={event.code:3d} (0x{event.code:03x})  {name}")
            elif event.type == ecodes.EV_ABS:
                name = abs_names.get(event.code, f"0x{event.code:03x}")
                # Only print D-pad and hat changes to avoid stick noise
                if event.code in (ABS_DPAD_X, ABS_DPAD_Y):
                    print(f"  ABS  value={event.value:+6d}  code={event.code:3d}  {name}")
    except KeyboardInterrupt:
        print("\nDone.")


# ── Detect-all mode ───────────────────────────────────────────────────────────

def _detect_device(path, key_names, abs_names):
    """Read events from one device and print them; runs in its own thread."""
    try:
        dev = evdev.InputDevice(path)
    except PermissionError:
        print(f"  [SKIP — permission denied] {path}")
        return
    except OSError as e:
        print(f"  [SKIP — {e}] {path}")
        return
    print(f"  [listening] {dev.path}  ({dev.name})")
    try:
        for event in dev.read_loop():
            if event.type == ecodes.EV_KEY:
                name  = key_names.get(event.code, f"0x{event.code:03x}")
                state = {0: "released", 1: "pressed ", 2: "repeat  "}.get(event.value, str(event.value))
                print(f"  [{dev.path}] ({dev.name})  KEY {state}  code={event.code:3d}  {name}")
            elif event.type == ecodes.EV_ABS:
                name = abs_names.get(event.code, f"0x{event.code:03x}")
                if event.code in (ABS_DPAD_X, ABS_DPAD_Y):
                    print(f"  [{dev.path}] ({dev.name})  ABS value={event.value:+6d}  code={event.code:3d}  {name}")
    except OSError:
        pass  # device disconnected


def detect_all_mode():
    """Listen on every accessible input device and print events."""
    print("Scanning input devices…\n")

    key_names = {}
    for name, code in ecodes.ecodes.items():
        if isinstance(code, int) and (name.startswith("KEY_") or name.startswith("BTN_")):
            key_names.setdefault(code, name)
    abs_names = {v: k for k, v in ecodes.ABS.items() if isinstance(v, int)}

    threads = []
    for path in evdev.list_devices():
        t = threading.Thread(target=_detect_device, args=(path, key_names, abs_names), daemon=True)
        t.start()
        threads.append(t)

    # Let threads print their open/skip status before the prompt
    time.sleep(0.3)
    print("\nPress buttons to identify them. Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nDone.")


# ── Watch-hidraw diagnostic ───────────────────────────────────────────────────

def watch_hidraw_mode(path):
    """Print only packets that differ from the previous one. Ctrl+C to stop."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except (PermissionError, OSError) as e:
        print(f"Cannot open {path}: {e}")
        return
    print(f"Watching {path} — only changed packets are printed.  Ctrl+C to stop.\n")
    print("   t(s)   raw bytes (hex)")
    t0   = time.monotonic()
    prev = None
    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0.5)
            if not ready:
                continue
            try:
                pkt = os.read(fd, 64)
            except OSError:
                break
            if pkt != prev:
                diff = ""
                if prev is not None and len(pkt) == len(prev):
                    # Mark bytes that changed with ▶
                    parts = []
                    for a, b in zip(prev, pkt):
                        parts.append(f"\033[1m{b:02x}\033[0m" if a != b else f"{b:02x}")
                    diff = " ".join(parts)
                else:
                    diff = pkt.hex(" ")
                print(f"  {time.monotonic()-t0:6.2f}  {diff}")
                prev = pkt
    except KeyboardInterrupt:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


# ── Legion Go HID device discovery ────────────────────────────────────────────

def find_legion_hidraw():
    """
    Return the path of the Legion Go's main HID interface, or None.

    The controller exposes several hidraw nodes with the same VID/PID.  We open
    all Lenovo ones simultaneously and return whichever first sends a 64-byte
    packet with report ID 0x74 — the one that carries button state.
    Falls back to the first VID match if none produce that report within 2 s.
    """
    # Collect all hidraw paths that belong to a Lenovo device
    candidates = []   # list of (path, fd)
    fallback   = None
    for path in sorted(glob.glob("/dev/hidraw*")):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except (PermissionError, OSError):
            continue
        try:
            raw = fcntl.ioctl(fd, _HIDIOCGRAWINFO, b'\x00' * 8)
            _, vid, _ = struct.unpack('<IHH', raw)
            if vid == _LENOVO_VID:
                candidates.append((path, fd))
                if fallback is None:
                    fallback = path
                continue   # keep fd open for sniffing
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass

    if not candidates:
        return None

    # Race: whichever candidate sends a 0x74 report first wins
    fd_to_path = {fd: path for path, fd in candidates}
    result   = None
    deadline = time.monotonic() + 2.0
    try:
        while time.monotonic() < deadline and result is None:
            remaining = max(0.0, deadline - time.monotonic())
            ready, _, _ = select.select(list(fd_to_path), [], [], remaining)
            for fd in ready:
                try:
                    pkt = os.read(fd, 64)
                except OSError:
                    continue
                if len(pkt) >= 3 and pkt[2] == _LEGION_REPORT_ID:
                    result = fd_to_path[fd]
                    break
    finally:
        for fd in fd_to_path:
            try:
                os.close(fd)
            except OSError:
                pass

    return result or fallback


# ── Screen lock ────────────────────────────────────────────────────────────────

def lock_screen():
    subprocess.run(["loginctl", "lock-session"], check=False)


def toggle_osk():
    """Toggle the GNOME on-screen keyboard via gsettings."""
    _KEY = "org.gnome.desktop.a11y.applications"
    _PROP = "screen-keyboard-enabled"
    try:
        result = subprocess.run(
            ["gsettings", "get", _KEY, _PROP],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            return
        current = result.stdout.strip() == "true"
        subprocess.run(
            ["gsettings", "set", _KEY, _PROP, "false" if current else "true"],
            check=False,
        )
    except OSError:
        pass


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


# ── Mapper state ───────────────────────────────────────────────────────────────

class State:
    def __init__(self):
        self.ls_x = 0.0   # left stick X  (–1.0 to 1.0)
        self.ls_y = 0.0
        self.rs_x = 0.0   # right stick X
        self.rs_y = 0.0
        self.orientation = "normal"
        self.lock = threading.Lock()

    def update_axis(self, code, raw_value):
        norm = raw_value / AXIS_MAX
        with self.lock:
            if   code == ABS_LS_X: self.ls_x = norm
            elif code == ABS_LS_Y: self.ls_y = norm
            elif code == ABS_RS_X: self.rs_x = norm
            elif code == ABS_RS_Y: self.rs_y = norm

    def set_orientation(self, orientation):
        with self.lock:
            self.orientation = orientation

    def combined_mouse_vector(self):
        """Return (dx, dy) combining both sticks — whichever has larger magnitude wins."""
        with self.lock:
            ls_mag = math.hypot(self.ls_x, self.ls_y)
            rs_mag = math.hypot(self.rs_x, self.rs_y)
            if ls_mag >= rs_mag:
                return self.ls_x, self.ls_y, ls_mag
            else:
                return self.rs_x, self.rs_y, rs_mag


def apply_deadzone_and_curve(x, y, magnitude):
    """Apply deadzone then acceleration curve; return scaled (x, y)."""
    if magnitude < DEADZONE:
        return 0.0, 0.0
    # Rescale so deadzone edge = 0, full deflection = 1
    scaled = (magnitude - DEADZONE) / (1.0 - DEADZONE)
    scaled = min(scaled, 1.0)
    # Apply acceleration curve
    curved = scaled ** ACCEL_EXPONENT
    factor = curved / magnitude if magnitude > 0 else 0
    return x * factor, y * factor


def rotate_for_orientation(x, y, orientation):
    """Rotate stick vector to match screen orientation.

    iio-sensor-proxy orientation strings → 2×2 rotation applied to (x, y):
      normal    → identity
      right-up  → 90° CW:  (x, y) → ( y, -x)
      left-up   → 90° CCW: (x, y) → (-y,  x)
      bottom-up → 180°:    (x, y) → (-x, -y)
    """
    if orientation == "right-up":
        return y, -x
    if orientation == "left-up":
        return -y, x
    if orientation == "bottom-up":
        return -x, -y
    return x, y  # "normal" or any unknown value


# ── Orientation watcher (iio-sensor-proxy via D-Bus) ─────────────────────────

_SENSOR_BUS_NAME  = "net.hadess.SensorProxy"
_SENSOR_IFACE     = "net.hadess.SensorProxy"
_SENSOR_OBJ_PATH  = "/net/hadess/SensorProxy"
_PROPS_IFACE      = "org.freedesktop.DBus.Properties"
_ORIENTATION_PROP = "AccelerometerOrientation"


class OrientationWatcher:
    """Tracks device orientation via iio-sensor-proxy and updates State.

    Runs in a daemon thread.  If dbus or iio-sensor-proxy is unavailable,
    __init__ logs a warning and returns without starting the thread.
    """

    def __init__(self, state):
        self.state = state
        try:
            import dbus
            import dbus.mainloop.glib

            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
            bus = dbus.SystemBus()
            raw_proxy = bus.get_object(_SENSOR_BUS_NAME, _SENSOR_OBJ_PATH)
            self._proxy = dbus.Interface(raw_proxy, _PROPS_IFACE)
            self._bus   = bus

            sensor_iface = dbus.Interface(raw_proxy, _SENSOR_IFACE)
            sensor_iface.ClaimAccelerometer()
        except (ImportError, TypeError):
            print("[orientation] python3-dbus not available — orientation tracking disabled.")
            return
        except Exception as e:
            print(f"[orientation] D-Bus setup failed ({e}) — orientation tracking disabled.")
            return

        t = threading.Thread(target=self._run, daemon=True)
        t.start()

    def _run(self):
        try:
            from gi.repository import GLib  # type: ignore

            self._read_initial()
            self._subscribe()

            GLib.MainLoop().run()
        except Exception as e:
            print(f"[orientation] D-Bus watcher failed ({e}) — orientation tracking disabled.")

    def _read_initial(self):
        val = self._proxy.Get(_SENSOR_IFACE, _ORIENTATION_PROP)
        self.state.set_orientation(str(val))

    def _subscribe(self):
        self._bus.add_signal_receiver(
            self._on_properties_changed,
            dbus_interface="org.freedesktop.DBus.Properties",
            signal_name="PropertiesChanged",
            path=_SENSOR_OBJ_PATH,
        )

    def _on_properties_changed(self, interface, changed, invalidated):
        if _ORIENTATION_PROP in changed:
            self.state.set_orientation(str(changed[_ORIENTATION_PROP]))


# ── Mouse mover thread ────────────────────────────────────────────────────────

def mouse_mover(state: State, ui: UInput, stop_event: threading.Event):
    interval = 1.0 / POLL_HZ
    remainder_x = 0.0
    remainder_y = 0.0
    while not stop_event.is_set():
        t0 = time.monotonic()

        raw_x, raw_y, mag = state.combined_mouse_vector()
        with state.lock:
            orientation = state.orientation
        rot_x, rot_y = rotate_for_orientation(raw_x, raw_y, orientation)
        nx, ny = apply_deadzone_and_curve(rot_x, rot_y, mag)

        pixels_per_tick = MOUSE_SPEED * interval
        dx_f = nx * pixels_per_tick + remainder_x
        dy_f = ny * pixels_per_tick + remainder_y
        dx = int(dx_f)
        dy = int(dy_f)
        remainder_x = dx_f - dx
        remainder_y = dy_f - dy

        if dx != 0 or dy != 0:
            ui.write(ecodes.EV_REL, ecodes.REL_X, dx)
            ui.write(ecodes.EV_REL, ecodes.REL_Y, dy)
            ui.syn()

        elapsed = time.monotonic() - t0
        sleep_time = interval - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)


# ── D-pad arrow key emitter ───────────────────────────────────────────────────

class DpadKeys:
    """Tracks D-pad hat state and emits key press/release events."""

    def __init__(self, ui: UInput):
        self.ui = ui
        self.active = {}   # key_code → bool

    def update(self, axis_code, value):
        if axis_code == ABS_DPAD_X:
            self._set(ecodes.KEY_LEFT,  value < 0)
            self._set(ecodes.KEY_RIGHT, value > 0)
        elif axis_code == ABS_DPAD_Y:
            self._set(ecodes.KEY_UP,    value < 0)
            self._set(ecodes.KEY_DOWN,  value > 0)

    def _set(self, key, pressed):
        was = self.active.get(key, False)
        if pressed == was:
            return
        self.active[key] = pressed
        self.ui.write(ecodes.EV_KEY, key, 1 if pressed else 0)
        self.ui.syn()


class StickKeys:
    """Converts thumbstick deflection into directional key presses."""

    THRESHOLD = 0.5

    def __init__(self, ui: UInput, x_axis_code: int, y_axis_code: int):
        self.ui = ui
        self._x_axis = x_axis_code
        self._y_axis = y_axis_code
        self._x = 0.0
        self._y = 0.0
        self.active = {}   # key_code → bool

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

    def _set(self, key, pressed):
        was = self.active.get(key, False)
        if pressed == was:
            return
        self.active[key] = pressed
        self.ui.write(ecodes.EV_KEY, key, 1 if pressed else 0)
        self.ui.syn()


# ── Event processing ──────────────────────────────────────────────────────────

def handle_event(event, state: State, ui: UInput, dpad: DpadKeys,
                 ls_keys, rs_keys, cfg: dict):
    """Process a single evdev event using the loaded config."""

    if event.type == ecodes.EV_ABS:
        code = event.code
        if code in (ABS_LS_X, ABS_LS_Y):
            action = cfg.get("left_stick", "none")
            if action == "mouse":
                state.update_axis(code, event.value)
            elif action == "arrow_keys" and ls_keys is not None:
                ls_keys.update_axis(code, event.value)
        elif code in (ABS_RS_X, ABS_RS_Y):
            action = cfg.get("right_stick", "none")
            if action == "mouse":
                state.update_axis(code, event.value)
            elif action == "arrow_keys" and rs_keys is not None:
                rs_keys.update_axis(code, event.value)
        elif code in (ABS_DPAD_X, ABS_DPAD_Y):
            if cfg.get("dpad", "none") == "arrow_keys":
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


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if "--detect-all" in sys.argv:
        detect_all_mode()
        return

    watch_args = [a for a in sys.argv if a.startswith("--watch-hidraw=")]
    if watch_args:
        watch_hidraw_mode(watch_args[0].split("=", 1)[1])
        return

    if "--configure" in sys.argv:
        configure_mode()
        return

    detect = "--detect" in sys.argv

    # Find gamepad
    if GAMEPAD_DEVICE:
        try:
            dev = evdev.InputDevice(GAMEPAD_DEVICE)
        except (PermissionError, OSError) as e:
            print(f"Cannot open {GAMEPAD_DEVICE}: {e}")
            print("Try running with sudo or add yourself to the 'input' group.")
            sys.exit(1)
    else:
        dev = find_gamepad()
        if dev is None:
            print("Legion Go gamepad not found. Ensure it is connected.")
            print("Available devices:")
            for path in evdev.list_devices():
                try:
                    d = evdev.InputDevice(path)
                    print(f"  {path}: {d.name}")
                except Exception:
                    pass
            sys.exit(1)

    print(f"Using gamepad: {dev.path} ({dev.name})")

    if detect:
        detect_mode(dev)
        return

    # Grab the device so events don't also reach the desktop
    # (comment out dev.grab() if you want events to pass through too)
    try:
        dev.grab()
    except Exception as e:
        print(f"Warning: could not grab device exclusively: {e}")

    try:
        ui = create_virtual_device()
    except PermissionError:
        print(
            "Cannot create virtual device (/dev/uinput not writable).\n"
            "Run:  sudo chmod 0660 /dev/uinput\n"
            "Or add a udev rule (see bottom of this script).\n"
            "Or run the script with sudo."
        )
        dev.ungrab()
        sys.exit(1)

    cfg = load_config()

    ls_keys = (StickKeys(ui, ABS_LS_X, ABS_LS_Y)
               if cfg["left_stick"] == "arrow_keys" else None)
    rs_keys = (StickKeys(ui, ABS_RS_X, ABS_RS_Y)
               if cfg["right_stick"] == "arrow_keys" else None)

    print("Mapper running. Ctrl+C to stop.")
    print(f"  Thumbsticks → mouse  (speed={MOUSE_SPEED} px/s, deadzone={DEADZONE})")
    print("  Bindings loaded from config — run with --configure to change.")
    print()

    state      = State()
    OrientationWatcher(state)
    dpad       = DpadKeys(ui)
    stop_event = threading.Event()

    if cfg.get("left_stick", "none") == "mouse" or cfg.get("right_stick", "none") == "mouse":
        mover = threading.Thread(target=mouse_mover, args=(state, ui, stop_event), daemon=True)
        mover.start()
    else:
        mover = None

    locker = None
    if cfg.get("legion_btn", "none") != "none" or cfg.get("settings_btn", "none") != "none":
        locker = threading.Thread(
            target=lock_hidraw_reader, args=(stop_event, cfg, ui), daemon=True
        )
        locker.start()

    try:
        for event in dev.read_loop():
            handle_event(event, state, ui, dpad, ls_keys, rs_keys, cfg)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        stop_event.set()
        if mover is not None:
            mover.join(timeout=1)
        if locker is not None:
            locker.join(timeout=1)
        try:
            dev.ungrab()
        except Exception:
            pass
        ui.close()


if __name__ == "__main__":
    main()


# ── Setup instructions ─────────────────────────────────────────────────────────
#
# 1. Install dependency:
#      sudo apt install python3-evdev
#
# 2. Allow your user to read input devices (needs re-login after):
#      sudo usermod -aG input $USER
#
# 3. Allow your user to create virtual input devices:
#      Create file /etc/udev/rules.d/99-uinput.rules with content:
#        KERNEL=="uinput", MODE="0660", GROUP="input"
#      Then: sudo udevadm control --reload-rules && sudo udevadm trigger
#      (After step 2 re-login your user is in the 'input' group so this covers both.)
#
# 4. Load uinput at boot:
#      echo 'uinput' | sudo tee /etc/modules-load.d/uinput.conf
#      sudo modprobe uinput
#
# 5. Run:
#      python3 legion_go_mapper.py
#
# 6. To identify which physical buttons map to which codes:
#      python3 legion_go_mapper.py --detect
#    Then press each button and note the code printed.
#    Update MOUSE_LEFT_BTN / MOUSE_RIGHT_BTN at the top of this file.
#
# 7. Optional — run as a systemd user service so it starts automatically:
#    Create ~/.config/systemd/user/legion-mapper.service:
#      [Unit]
#      Description=Legion Go Gamepad Mapper
#      After=graphical-session.target
#
#      [Service]
#      ExecStart=/usr/bin/python3 /home/YOUR_USER/src/legionGoGamepadHid/legion_go_mapper.py
#      Restart=on-failure
#
#      [Install]
#      WantedBy=graphical-session.target
#
#    Then: systemctl --user enable --now legion-mapper.service
