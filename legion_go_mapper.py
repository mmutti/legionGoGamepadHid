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

Usage:
  python3 legion_go_mapper.py            # run mapper
  python3 legion_go_mapper.py --detect   # print raw events to identify buttons

Requirements:
  sudo apt install python3-evdev
  sudo usermod -aG input $USER           # then log out/in, or run with sudo
  # For uinput (virtual device creation):
  sudo modprobe uinput
  echo 'uinput' | sudo tee /etc/modules-load.d/uinput.conf
  sudo chmod 0660 /dev/uinput
  # Or add a udev rule (see setup instructions at bottom of this file)
"""

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

# ── Button / axis assignments ──────────────────────────────────────────────────

# Right face buttons → arrow keys (standard Xbox/South-facing layout)
FACE_TO_ARROW = {
    ecodes.BTN_Y: ecodes.KEY_UP,
    ecodes.BTN_A: ecodes.KEY_DOWN,
    ecodes.BTN_X: ecodes.KEY_LEFT,
    ecodes.BTN_B: ecodes.KEY_RIGHT,
}

# Left-side buttons below D-pad → mouse buttons
# BTN_SELECT = View/Back button (upper)
# BTN_MODE   = Legion L / mode button (lower)
# Run with --detect to confirm which physical buttons these are.
MOUSE_LEFT_BTN  = ecodes.BTN_START    # upper button (code 315) → left click
MOUSE_RIGHT_BTN = ecodes.BTN_SELECT   # lower button (code 314) → right click

# Thumbstick axes
ABS_LS_X = ecodes.ABS_X
ABS_LS_Y = ecodes.ABS_Y
ABS_RS_X = ecodes.ABS_RX
ABS_RS_Y = ecodes.ABS_RY

# D-pad hat axes
ABS_DPAD_X = ecodes.ABS_HAT0X
ABS_DPAD_Y = ecodes.ABS_HAT0Y

# ── Virtual output device ──────────────────────────────────────────────────────

def create_virtual_device():
    capabilities = {
        ecodes.EV_KEY: [
            ecodes.KEY_UP, ecodes.KEY_DOWN, ecodes.KEY_LEFT, ecodes.KEY_RIGHT,
            ecodes.BTN_LEFT, ecodes.BTN_RIGHT,
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


# ── Mapper state ───────────────────────────────────────────────────────────────

class State:
    def __init__(self):
        self.ls_x = 0.0   # left stick X  (–1.0 to 1.0)
        self.ls_y = 0.0
        self.rs_x = 0.0   # right stick X
        self.rs_y = 0.0
        self.lock = threading.Lock()

    def update_axis(self, code, raw_value):
        norm = raw_value / AXIS_MAX
        with self.lock:
            if   code == ABS_LS_X: self.ls_x = norm
            elif code == ABS_LS_Y: self.ls_y = norm
            elif code == ABS_RS_X: self.rs_x = norm
            elif code == ABS_RS_Y: self.rs_y = norm

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


# ── Mouse mover thread ────────────────────────────────────────────────────────

def mouse_mover(state: State, ui: UInput, stop_event: threading.Event):
    interval = 1.0 / POLL_HZ
    remainder_x = 0.0
    remainder_y = 0.0
    while not stop_event.is_set():
        t0 = time.monotonic()

        raw_x, raw_y, mag = state.combined_mouse_vector()
        nx, ny = apply_deadzone_and_curve(raw_x, raw_y, mag)

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


# ── Event processing ──────────────────────────────────────────────────────────

def handle_event(event, state: State, ui: UInput, dpad: DpadKeys, pressed_keys: dict):
    """Process a single evdev event."""

    if event.type == ecodes.EV_ABS:
        code = event.code
        if code in (ABS_LS_X, ABS_LS_Y, ABS_RS_X, ABS_RS_Y):
            state.update_axis(code, event.value)
        elif code in (ABS_DPAD_X, ABS_DPAD_Y):
            dpad.update(code, event.value)

    elif event.type == ecodes.EV_KEY:
        code = event.code
        val  = event.value  # 1=press, 0=release, 2=repeat

        # Face buttons → arrow keys
        if code in FACE_TO_ARROW:
            key = FACE_TO_ARROW[code]
            if val != 2:   # ignore autorepeat — let kernel handle it
                ui.write(ecodes.EV_KEY, key, val)
                ui.syn()

        # Left side buttons → mouse clicks
        elif code == MOUSE_LEFT_BTN:
            if val != 2:
                ui.write(ecodes.EV_KEY, ecodes.BTN_LEFT, val)
                ui.syn()

        elif code == MOUSE_RIGHT_BTN:
            if val != 2:
                ui.write(ecodes.EV_KEY, ecodes.BTN_RIGHT, val)
                ui.syn()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
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

    print("Mapper running. Ctrl+C to stop.")
    print(f"  Thumbsticks → mouse  (speed={MOUSE_SPEED} px/s, deadzone={DEADZONE})")
    print("  Y/A/X/B     → Up/Down/Left/Right arrows")
    print("  D-pad       → Up/Down/Left/Right arrows")
    print("  View btn    → Left mouse click")
    print("  Legion L    → Right mouse click")
    print()

    state      = State()
    dpad       = DpadKeys(ui)
    pressed    = {}
    stop_event = threading.Event()

    mover = threading.Thread(target=mouse_mover, args=(state, ui, stop_event), daemon=True)
    mover.start()

    try:
        for event in dev.read_loop():
            handle_event(event, state, ui, dpad, pressed)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        stop_event.set()
        mover.join(timeout=1)
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
