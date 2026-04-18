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
  python3 legion_go_mapper.py --detect-hid  # auto-find Legion hidraw device and
                                            # show a byte-diff view — press a
                                            # button to see which byte/bit changes
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
# Types: "axis" = thumbstick, "dpad" = hat switch,
#        "button" = digital button (legion/settings handled as "button" via HID),
#        "trigger" = analog shoulder trigger (ABS_Z/ABS_RZ, range 0-255)
CONTROLS = [
    ("left_stick",   "axis",    "Left thumbstick"),
    ("right_stick",  "axis",    "Right thumbstick"),
    ("dpad",         "dpad",    "D-pad"),
    ("btn_y",        "button",  "Y button"),
    ("btn_a",        "button",  "A button"),
    ("btn_x",        "button",  "X button"),
    ("btn_b",        "button",  "B button"),
    ("btn_lb",       "button",  "Left bumper (LB)"),
    ("btn_rb",       "button",  "Right bumper (RB)"),
    ("lt",           "trigger", "Left trigger (LT)"),
    ("rt",           "trigger", "Right trigger (RT)"),
    ("btn_view",     "button",  "View/Back button"),
    ("btn_menu",     "button",  "Menu/Start button"),
    ("btn_l3",       "button",  "L3 (left stick click)"),
    ("btn_r3",       "button",  "R3 (right stick click)"),
    ("legion_btn",   "button",  "Legion L button"),
    ("settings_btn", "button",  "Settings button"),
    ("btn_y1",       "button",  "Y1 button"),
    ("btn_y2",       "button",  "Y2 button"),
    ("btn_y3",       "button",  "Y3 button"),
    ("btn_m3",       "button",  "M3 button"),
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
    ("transport_mode", "Transport mode (toggle controller lock)"),
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
    ("key_super",      "Key: Super (Meta/Win)"),
    ("key_ctrl",       "Key: Ctrl (Left)"),
    ("key_c",          "Key: C"),
    ("key_d",          "Key: D"),
    ("key_q",          "Key: Q"),
    ("key_tab",        "Key: Tab"),
]

TRIGGER_ACTIONS = BUTTON_ACTIONS  # triggers act as digital buttons past threshold

ACTIONS_FOR_TYPE = {
    "axis":    AXIS_ACTIONS,
    "dpad":    DPAD_ACTIONS,
    "button":  BUTTON_ACTIONS,
    "trigger": TRIGGER_ACTIONS,
}

# "none" is not in any action list (it is the "0. Disabled" option on the rebind screen),
# but it must be in ACTION_LABELS so current bindings display correctly.
ACTION_LABELS = {a: l for actions in ACTIONS_FOR_TYPE.values() for a, l in actions}
ACTION_LABELS["none"] = "Disabled"

# ── FiraCode Nerd Font icon maps for the --configure TUI ──────────────────────
# Requires a terminal with a Nerd Font configured (e.g. FiraCode Nerd Font).
# Non-NF terminals will render boxes; behavior is unaffected.

_CONTROL_ICONS = {
    "left_stick":   "\U000f04d7",  # nf-md-joystick
    "right_stick":  "\U000f04d7",
    "dpad":         "\U000f1192",  # nf-md-gamepad (directional)
    "btn_y":        "\U000f0b26",  # nf-md-controller_classic
    "btn_a":        "\U000f0b26",
    "btn_x":        "\U000f0b26",
    "btn_b":        "\U000f0b26",
    "btn_lb":       "\uf077",      # nf-fa-chevron_up
    "btn_rb":       "\uf077",
    "lt":           "\U000f0616",  # nf-md-arrow_expand_up
    "rt":           "\U000f0616",
    "btn_view":     "\uf0c9",      # nf-fa-bars
    "btn_menu":     "\uf0c9",
    "btn_l3":       "\U000f0d86",  # nf-md-gesture_tap_button
    "btn_r3":       "\U000f0d86",
    "legion_btn":   "\uf17c",      # nf-fa-linux
    "settings_btn": "\uf013",      # nf-fa-cog
    "btn_y1":       "\uf02b",      # nf-fa-tag (back paddle)
    "btn_y2":       "\uf02b",
    "btn_y3":       "\uf02b",
    "btn_m3":       "\uf02b",
}

_ACTION_ICONS = {
    "mouse":          "\U000f037d",  # nf-md-mouse
    "mouse_left":     "\U000f037d",
    "mouse_right":    "\U000f037d",
    "arrow_keys":     "\uf0b2",      # nf-fa-arrows (compass)
    "arrow_up":       "\uf062",      # nf-fa-arrow_up
    "arrow_down":     "\uf063",
    "arrow_left":     "\uf060",
    "arrow_right":    "\uf061",
    "key_y":          "\U000f030c",  # nf-md-keyboard
    "key_return":     "\uf149",      # nf-fa-level-down (enter)
    "key_esc":        "\U000f030c",
    "key_backspace":  "\U000f030c",
    "key_delete":     "\U000f030c",
    "key_super":      "\U000f030c",
    "key_ctrl":       "\U000f030c",
    "key_c":          "\U000f030c",
    "key_d":          "\U000f030c",
    "key_q":          "\U000f030c",
    "key_tab":        "\U000f030c",
    "transport_mode": "\uf023",      # nf-fa-lock
    "lock_screen":    "\uf023",
    "osk":            "\U000f0b13",  # nf-md-keyboard_outline
    "none":           "\uf05e",      # nf-fa-ban
}


def _try_import_rich():
    """Return True if rich is importable; used by --configure TUI."""
    try:
        import rich  # noqa: F401
        return True
    except ImportError:
        return False


def _try_import_readchar():
    """Return True if readchar is importable; enables arrow-key navigation."""
    try:
        import readchar  # noqa: F401
        return True
    except ImportError:
        return False


# Key constants match readchar.key.* on Linux. Defined here so scripted
# tests don't need to import readchar, and so the code doesn't require
# readchar to be present at module-import time.
_KEY_UP    = "\x1b[A"
_KEY_DOWN  = "\x1b[B"
_KEY_LEFT  = "\x1b[D"
_KEY_ENTER_CODES = ("\r", "\n")
_KEY_ESC   = "\x1b"


_MAIN_MENU_META_ITEMS = [
    ("__save__", "meta", "Save and restart service"),
    ("__exit__", "meta", "Exit without saving"),
]


def _main_menu_items():
    return list(CONTROLS) + _MAIN_MENU_META_ITEMS


def _render_rich(renderable) -> str:
    """Render a rich renderable to a string with ANSI codes embedded.

    Uses `Console(file=StringIO, force_terminal=True)` so the resulting
    string carries colors; when printed to a real terminal the ANSI
    escape sequences are interpreted. Tests that capture via print_fn
    see the raw string — no test assertions depend on ANSI codes.
    """
    import io
    from rich.console import Console
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, color_system="truecolor", width=100)
    console.print(renderable)
    return buf.getvalue()


def _build_main_menu_table(cfg, selected: int = -1):
    """Build a rich Table for the main menu. If `selected >= 0`, that row
    is highlighted with a cursor marker and reverse-video style.
    """
    from rich.table import Table
    from rich.text import Text

    items = _main_menu_items()
    t = Table(title="Legion Go Gamepad — Button Configuration",
              title_style="bold yellow",
              header_style="bold yellow",
              border_style="yellow",
              expand=False)
    t.add_column("", width=1)   # cursor marker
    t.add_column("#", justify="right", style="dim", width=3)
    t.add_column("", width=2)
    t.add_column("Control", style="white")
    t.add_column("Short-press", style="cyan")
    t.add_column("Long-press", style="cyan")

    for i, item in enumerate(items):
        key, ctype, name = item[:3]
        is_sel = (i == selected)
        cursor = Text("▸", style="bold yellow") if is_sel else Text(" ")
        row_style = "on grey23" if is_sel else None

        if key == "__save__":
            icon = "\uf0c7"  # nf-fa-save
            row = (cursor, Text("S", style="bold green"), icon,
                   Text(name, style="bold green"),
                   Text(""), Text(""))
        elif key == "__exit__":
            icon = "\uf011"  # nf-fa-power_off
            row = (cursor, Text("Q", style="bold"), icon,
                   Text(name, style="dim red"),
                   Text(""), Text(""))
        else:
            icon = _CONTROL_ICONS.get(key, " ")
            short_action = cfg.get(key, "none")
            short_label = ACTION_LABELS.get(short_action, short_action)
            if short_action == "transport_mode":
                short_text = Text(short_label, style="bold red")
            elif short_action == "none":
                short_text = Text(short_label, style="dim")
            else:
                short_text = Text(short_label)

            if ctype == "button":
                long_action = cfg.get(f"{key}_long", "none")
                long_label = ACTION_LABELS.get(long_action, long_action)
                if long_action == "transport_mode":
                    long_text = Text(long_label, style="bold red")
                elif long_action == "none":
                    long_text = Text(long_label, style="dim")
                else:
                    long_text = Text(long_label)
            else:
                long_text = Text("—", style="dim")

            row = (cursor, Text(str(i + 1), style="dim"), icon,
                   Text(name, style="white"), short_text, long_text)

        t.add_row(*row, style=row_style)

    return t


def _rich_main_menu(cfg) -> str:
    """Backwards-compat wrapper — returns the plain (non-highlighted) main
    menu rendered to an ANSI-containing string. Used by non-interactive tests
    and the rich-without-arrows code path.
    """
    return _render_rich(_build_main_menu_table(cfg, selected=-1))


def _build_action_menu_table(name: str, ctype: str, current: str, which: str,
                             selected: int = -1):
    """Build a rich Table for action selection. If `selected >= 0`, that
    row is highlighted. The list includes all actions plus a trailing
    "Disabled" row; the "Disabled" row is at index len(actions).
    """
    from rich.table import Table
    from rich.text import Text

    actions = ACTIONS_FOR_TYPE[ctype]
    title_color = "yellow" if which == "SHORT" else "red"
    t = Table(title=f"{name} — choose {which}-press action",
              title_style=f"bold {title_color}",
              header_style=f"bold {title_color}",
              border_style=title_color,
              expand=False)
    t.add_column("", width=1)
    t.add_column("#", justify="right", style="dim", width=3)
    t.add_column("", width=2)
    t.add_column("Action", style="white")
    t.add_column("", style="green")

    for j, (akey, alabel) in enumerate(actions):
        is_sel = (j == selected)
        cursor = Text("▸", style="bold yellow") if is_sel else Text(" ")
        row_style = "on grey23" if is_sel else None
        icon = _ACTION_ICONS.get(akey, " ")
        label_style = "bold red" if akey == "transport_mode" else ""
        label = Text(alabel, style=label_style) if label_style else Text(alabel)
        marker = Text("← current", style="bold yellow") if current == akey else Text("")
        t.add_row(cursor, str(j + 1), icon, label, marker, style=row_style)

    # "Disabled" row appended at index len(actions); hotkey "0"
    disabled_idx = len(actions)
    is_sel = (disabled_idx == selected)
    cursor = Text("▸", style="bold yellow") if is_sel else Text(" ")
    row_style = "on grey23" if is_sel else None
    none_marker = Text("← current", style="bold yellow") if current == "none" else Text("")
    t.add_row(cursor, "0", _ACTION_ICONS.get("none", " "),
              Text("Disabled", style="dim"), none_marker, style=row_style)

    return t


def _rich_action_list(name: str, ctype: str, current: str, which: str) -> str:
    """Backwards-compat wrapper — non-highlighted action list as a string."""
    return _render_rich(_build_action_menu_table(name, ctype, current, which, selected=-1))


# ── Arrow-key driven menus (rich Live + readchar) ─────────────────────────────
# The loops below call `read_key_fn()` which returns a keypress string matching
# the _KEY_* constants. Tests pass a scripted callable; production uses
# `readchar.readkey`.

def _match_enter(key: str) -> bool:
    return key in _KEY_ENTER_CODES


def _arrow_pick_action(name: str, ctype: str, current: str, which: str,
                       read_key_fn, console=None):
    """Interactive action-selection loop. Returns the picked action string,
    or None if the user pressed Esc (cancel).

    Pre-selects the row matching `current` so arrow-up/down moves from the
    existing binding. Typing a digit (1-N for actions, 0 for Disabled)
    activates that row immediately. Enter activates the current row.
    """
    from rich.live import Live

    actions = ACTIONS_FOR_TYPE[ctype]
    n = len(actions) + 1   # +1 for "Disabled" row

    # Pre-select the row matching the current binding
    selected = len(actions) if current == "none" else 0
    for j, (akey, _) in enumerate(actions):
        if akey == current:
            selected = j
            break

    def picked(idx: int):
        return "none" if idx == len(actions) else actions[idx][0]

    def render():
        return _build_action_menu_table(name, ctype, current, which, selected)

    with Live(render(), console=console, refresh_per_second=30,
              screen=False, transient=True) as live:
        while True:
            key = read_key_fn()
            if key == _KEY_UP:
                selected = (selected - 1) % n
            elif key == _KEY_DOWN:
                selected = (selected + 1) % n
            elif _match_enter(key):
                return picked(selected)
            elif key == _KEY_ESC or key == _KEY_LEFT:
                return None
            elif key == "0":
                return "none"
            elif key.isdigit():
                idx = int(key) - 1
                if 0 <= idx < len(actions):
                    return picked(idx)
            else:
                continue   # unknown key — ignore, don't re-render
            live.update(render())


def _arrow_configure(cfg, read_key_fn=None, console=None) -> None:
    """Interactive arrow-driven configure loop. Returns when the user saves
    or exits. Mutates `cfg` in place as bindings change.
    """
    from rich.live import Live
    if read_key_fn is None:
        import readchar
        read_key_fn = readchar.readkey

    items = _main_menu_items()
    n = len(items)
    save_idx = next(i for i, it in enumerate(items) if it[0] == "__save__")
    exit_idx = next(i for i, it in enumerate(items) if it[0] == "__exit__")
    selected = 0

    def render():
        return _build_main_menu_table(cfg, selected)

    while True:
        activated_idx = None
        with Live(render(), console=console, refresh_per_second=30,
                  screen=False, transient=True) as live:
            while True:
                key = read_key_fn()
                if key == _KEY_UP:
                    selected = (selected - 1) % n
                elif key == _KEY_DOWN:
                    selected = (selected + 1) % n
                elif _match_enter(key):
                    activated_idx = selected
                    break
                elif key == _KEY_ESC or key == _KEY_LEFT or key == "q":
                    activated_idx = exit_idx
                    break
                elif key == "s":
                    activated_idx = save_idx
                    break
                elif key.isdigit() and key != "0":
                    idx = int(key) - 1
                    if 0 <= idx < len(CONTROLS):
                        activated_idx = idx
                        break
                else:
                    continue
                live.update(render())

        # Activation happens AFTER exiting the Live context so nested
        # Live loops (sub-menu) don't conflict.
        item_key, ctype, name = items[activated_idx][:3]
        if item_key == "__save__":
            if _save_and_restart(cfg):
                return
            selected = activated_idx
            continue
        if item_key == "__exit__":
            return

        # Regular control — open sub-menu(s)
        short = _arrow_pick_action(name, ctype, cfg.get(item_key, "none"),
                                   "SHORT", read_key_fn, console)
        if short is None:
            selected = activated_idx
            continue
        cfg[item_key] = short
        if ctype == "button":
            long_action = _arrow_pick_action(
                name, ctype, cfg.get(f"{item_key}_long", "none"),
                "LONG", read_key_fn, console)
            if long_action is not None:
                cfg[f"{item_key}_long"] = long_action
        selected = activated_idx

DEFAULT_CONFIG = {
    "left_stick":        "mouse",
    "right_stick":       "mouse",
    "dpad":              "arrow_keys",
    "btn_y":             "arrow_up",
    "btn_a":             "arrow_down",
    "btn_x":             "arrow_left",
    "btn_b":             "arrow_right",
    "btn_lb":            "none",
    "btn_rb":            "none",
    "lt":                "none",
    "rt":                "none",
    "btn_view":          "mouse_left",
    "btn_menu":          "mouse_right",
    "btn_l3":            "none",
    "btn_r3":            "none",
    "legion_btn":        "lock_screen",
    "settings_btn":      "lock_screen",
    "btn_y1":            "none",
    "btn_y2":            "none",
    "btn_y3":            "none",
    "btn_m3":            "none",
    # Long-press actions — opt-in per button. "none" = no long-press deferral.
    "btn_y_long":        "none",
    "btn_a_long":        "none",
    "btn_x_long":        "none",
    "btn_b_long":        "none",
    "btn_lb_long":       "none",
    "btn_rb_long":       "none",
    "btn_view_long":     "none",
    "btn_menu_long":     "none",
    "btn_l3_long":       "none",
    "btn_r3_long":       "none",
    "legion_btn_long":   "transport_mode",
    "settings_btn_long": "none",
    "btn_y1_long":       "none",
    "btn_y2_long":       "none",
    "btn_y3_long":       "none",
    "btn_m3_long":       "none",
    "long_press_ms":     500,
    "gnome_auto_unlock": False,
    # Stick-ring LED colors as [R, G, B] (0..255 each).
    "led_color_enabled": [255, 180, 0],   # yellow when mapper is running / unlocked
    "led_color_locked":  [255,   0, 0],   # red when transport mode is locked
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


def _prompt_button_binding(name: str, ctype: str, current_short: str,
                           current_long: str, input_fn=None, print_fn=None):
    """Prompt for a button's short-press action and (if discrete-button)
    optional long-press action. Returns (short_action, long_action).

    Uses rich for colored tables with Nerd Font icons when rich is
    importable; falls back to plain text otherwise.
    """
    if input_fn is None:
        input_fn = input
    if print_fn is None:
        print_fn = print
    actions = ACTIONS_FOR_TYPE[ctype]
    use_rich = _try_import_rich()

    def _render_list(which: str, current: str) -> None:
        if use_rich:
            print_fn(_rich_action_list(name, ctype, current, which))
            return
        print_fn(f"\n  {name} — choose {which}-press action:")
        for j, (akey, alabel) in enumerate(actions, 1):
            marker = "  ← current" if current == akey else ""
            print_fn(f"  {j:3d}. {alabel}{marker}")
        print_fn(f"    0. Disabled{('  ← current' if current == 'none' else '')}")

    def _pick(which: str, current: str) -> str:
        while True:
            _render_list(which, current)
            choice = input_fn().strip()
            if choice == "0":
                return "none"
            try:
                idx = int(choice) - 1
            except ValueError:
                print_fn("  Invalid input.")
                continue
            if not (0 <= idx < len(actions)):
                print_fn("  Invalid input.")
                continue
            return actions[idx][0]

    short_action = _pick("SHORT", current_short)
    if ctype != "button":
        return short_action, "none"
    long_action = _pick("LONG", current_long)
    return short_action, long_action


def configure_mode():
    """Interactive CLI to rebind all Legion Go controls.

    When a TTY is attached and both rich + readchar are installed, uses
    the arrow-key driven TUI. Otherwise falls back to line-input mode.
    """
    cfg = load_config()
    use_rich = _try_import_rich()
    use_arrows = (
        use_rich
        and _try_import_readchar()
        and sys.stdin.isatty()
        and sys.stdout.isatty()
    )

    if use_arrows:
        _arrow_configure(cfg)
        return

    while True:
        if use_rich:
            print(_rich_main_menu(cfg))
            print(f"  Config file: \033[2m{CONFIG_PATH}\033[0m")
            print("  \033[33ms\033[0m) Save and restart service   "
                  "\033[33mq\033[0m) Quit without saving")
            prompt = "Enter number to reconfigure, 's' to save, 'q' to quit: "
        else:
            print("\nLegion Go Gamepad — Button Configuration")
            print("=" * 43)
            print(f"Config: {CONFIG_PATH}\n")
            for i, (key, ctype, name) in enumerate(CONTROLS, 1):
                label = ACTION_LABELS.get(cfg.get(key, "none"), cfg.get(key, "none"))
                print(f"  {i:2d}.  {name:<28}  [{label}]")
            print("\n  s.  Save and restart service")
            print("  q.  Quit without saving\n")
            prompt = "Enter number to reconfigure, 's' to save, 'q' to quit: "

        choice = input(prompt).strip().lower()

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

        short, long_action = _prompt_button_binding(
            name=name, ctype=ctype,
            current_short=cfg.get(key, "none"),
            current_long=cfg.get(f"{key}_long", "none"),
        )
        cfg[key] = short
        if ctype == "button":
            cfg[f"{key}_long"] = long_action
        print(f"  → {name} set to: short={short}, long={long_action}")


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

# Trigger axes (unipolar, 0..TRIGGER_MAX)
ABS_LT = ecodes.ABS_Z
ABS_RT = ecodes.ABS_RZ
TRIGGER_MAX = 255.0   # Xbox/HID gamepad standard; adjust if your device differs

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
    "key_super":     ecodes.KEY_LEFTMETA,
    "key_ctrl":      ecodes.KEY_LEFTCTRL,
    "key_c":         ecodes.KEY_C,
    "key_d":         ecodes.KEY_D,
    "key_q":         ecodes.KEY_Q,
    "key_tab":       ecodes.KEY_TAB,
}

# ── Legion Go HID constants (from hhd-dev/hhd) ─────────────────────────────────
_LENOVO_VID        = 0x17EF
_LEGION_GO_PIDS    = {0x6182, 0x6183, 0x6184, 0x6185,   # original Legion Go
                      0x61EB, 0x61EC, 0x61ED, 0x61EE}    # 2025 firmware variants
_LEGION_REPORT_ID  = 0x74   # pkt[2] in a raw hidraw read
_LEGION_BTN_BYTE   = 18     # byte18: bit7=Legion L, bit6=Settings
_LEGION_BTN_MASK   = 0xC0
_EXTRA_BTN_BYTE    = 20     # byte20: bit7=Y1, bit6=Y2, bit5=Y3, bit2=M3
_EXTRA_BTN_MASK    = 0xE4
_HIDIOCGRAWINFO    = 0x80084803  # ioctl: get bus/VID/PID

# ── Legion Go LED HID protocol (tablet variant, PIDs 0x6182-0x6185) ──────────
# Ported from hhd-dev/hhd: src/hhd/device/legion_go/tablet/hid.py
# Slim variant (PIDs 0x61EB-0x61EE) uses a different protocol (report 0x10)
# — not implemented yet; LED feature degrades to a no-op on slim devices.

_RGB_MODES = {"solid": 1, "pulse": 2, "dynamic": 3, "spiral": 4}
_RGB_CONTROLLER_CODES = {"both": 0x03, "left": 0x02, "right": 0x01}


def _rgb_controller_code(controller: str) -> int:
    return _RGB_CONTROLLER_CODES[controller]


def _rgb_build_set_profile(controller: str, profile: int, mode: str,
                           r: int, g: int, b: int,
                           brightness: float, speed: float) -> bytes:
    """Build a Legion Go tablet RGB 'set profile' HID output report.

    brightness, speed are 0.0..1.0 floats; internally scaled to 0..63.
    Higher speed → shorter period (faster animation); speed=0.0 is slowest.
    """
    r_mode = _RGB_MODES[mode]
    r_bright = min(max(int(64 * brightness), 0), 63)
    r_period = min(max(int(64 * (1 - speed)), 0), 63)
    r_ctrl = _rgb_controller_code(controller)
    return bytes([
        0x05, 0x0C, 0x72, 0x01,
        r_ctrl, r_mode,
        r & 0xFF, g & 0xFF, b & 0xFF,
        r_bright, r_period,
        profile & 0xFF, 0x01,
    ])


def _rgb_build_load_profile(controller: str, profile: int) -> bytes:
    """Build a Legion Go tablet RGB 'load profile' HID output report."""
    r_ctrl = _rgb_controller_code(controller)
    return bytes([0x05, 0x06, 0x73, 0x02, r_ctrl, profile & 0xFF, 0x01])


def _rgb_build_enable(controller: str, enable: bool) -> bytes:
    """Build a Legion Go tablet RGB 'enable' HID output report."""
    r_ctrl = _rgb_controller_code(controller)
    return bytes([0x05, 0x06, 0x70, 0x02, r_ctrl, 1 if enable else 0, 0x01])


# Default colors if the user hasn't overridden them in config.
_LED_YELLOW = (255, 180, 0)
_LED_RED = (255, 0, 0)
_LED_PROFILE = 1   # we always write to profile 1


class LedController:
    """Drives the Legion Go stick-ring LEDs via HID output reports.

    All HID writes swallow OSError — LED failures never break input gating.
    Construct with None path to get a no-op controller (useful when the
    Legion Go hidraw device cannot be found).

    color_enabled / color_locked override the default yellow/red. Accept
    any 3-element iterable of ints (e.g. list from JSON, tuple).
    """

    def __init__(self, hidraw_path, color_enabled=None, color_locked=None):
        self._fd = None
        self._color_enabled = tuple(color_enabled) if color_enabled is not None else _LED_YELLOW
        self._color_locked = tuple(color_locked) if color_locked is not None else _LED_RED
        if hidraw_path is None:
            return
        try:
            self._fd = os.open(hidraw_path, os.O_WRONLY)
        except OSError as e:
            print(f"[led] cannot open {hidraw_path} for write: {e}")
            self._fd = None

    def _write(self, pkt: bytes) -> None:
        if self._fd is None:
            return
        try:
            os.write(self._fd, pkt)
        except OSError as e:
            print(f"[led] write failed: {e}")

    def _issue_profile(self, mode: str, rgb, brightness: float, speed: float) -> None:
        r, g, b = rgb
        self._write(_rgb_build_set_profile(
            controller="both", profile=_LED_PROFILE, mode=mode,
            r=r, g=g, b=b, brightness=brightness, speed=speed,
        ))
        self._write(_rgb_build_load_profile(controller="both", profile=_LED_PROFILE))
        self._write(_rgb_build_enable(controller="both", enable=True))

    def set_enabled(self) -> None:
        """Solid color — mapper running, transport mode unlocked."""
        self._issue_profile("solid", self._color_enabled, brightness=1.0, speed=1.0)

    def set_locked(self) -> None:
        """Breathing color at the slowest hardware-supported rate (~0.25 Hz)."""
        self._issue_profile("pulse", self._color_locked, brightness=1.0, speed=0.0)

    def set_off(self) -> None:
        """Disable the rings. Best-effort; errors are swallowed."""
        self._write(_rgb_build_enable(controller="both", enable=False))

    def close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None


class TransportMode:
    """Tracks the mapper's 'locked / unlocked' state and drives the LEDs.

    locked=True → all input dispatch paths short-circuit except the button
    bound to 'transport_mode' (which must still reach toggle() to unlock).
    """

    def __init__(self, leds: LedController):
        self._locked = False
        self._leds = leds
        self._lock = threading.Lock()

    @property
    def locked(self) -> bool:
        return self._locked

    def toggle(self) -> None:
        with self._lock:
            if self._locked:
                self._locked = False
                self._leds.set_enabled()
            else:
                self._locked = True
                self._leds.set_locked()

    def lock(self) -> None:
        with self._lock:
            if not self._locked:
                self._locked = True
                self._leds.set_locked()

    def unlock(self) -> None:
        with self._lock:
            if self._locked:
                self._locked = False
                self._leds.set_enabled()


class LongPressDispatcher:
    """Per-button short-vs-long press dispatch.

    If long_action == 'none': fire short on press (legacy behavior, zero latency).
    Otherwise: start a timer on press; release-before-timeout fires short,
    timeout-while-held fires long and marks button 'consumed' so the release
    event does nothing.
    """

    def __init__(self, ui, transport, long_press_ms: int = 500):
        self._ui = ui
        self._transport = transport
        self._timeout_s = long_press_ms / 1000.0
        # key → {"short": str, "timer": Timer, "consumed": bool}
        self._state: dict = {}

    def press(self, key: str, short: str, long_action: str) -> None:
        if long_action == "none" or long_action is None:
            # legacy fast path — behaves exactly like current code
            _dispatch_button_action(short, 1, self._ui, self._transport)
            return

        # Long-press-capable path: defer short action
        def _fire_long():
            _dispatch_button_action(long_action, 1, self._ui, self._transport)
            # Fire a synthetic release for the long action (long_action was
            # a momentary press from the user's perspective)
            _dispatch_button_action(long_action, 0, self._ui, self._transport)
            st = self._state.get(key)
            if st is not None:
                st["consumed"] = True

        timer = threading.Timer(self._timeout_s, _fire_long)
        self._state[key] = {"short": short, "timer": timer, "consumed": False}
        timer.start()

    def release(self, key: str) -> None:
        st = self._state.pop(key, None)
        if st is None:
            # No deferred state — this was a long_action=="none" button; the
            # caller handles release separately via the legacy fast path.
            return
        st["timer"].cancel()
        if st["consumed"]:
            return   # long already fired; swallow the release
        # Early release: fire short press + release
        _dispatch_button_action(st["short"], 1, self._ui, self._transport)
        _dispatch_button_action(st["short"], 0, self._ui, self._transport)

# ── Virtual output device ──────────────────────────────────────────────────────

def create_virtual_device():
    capabilities = {
        ecodes.EV_KEY: [
            ecodes.KEY_UP, ecodes.KEY_DOWN, ecodes.KEY_LEFT, ecodes.KEY_RIGHT,
            ecodes.BTN_LEFT, ecodes.BTN_RIGHT,
            ecodes.KEY_Y, ecodes.KEY_ENTER, ecodes.KEY_ESC, ecodes.KEY_BACKSPACE, ecodes.KEY_DELETE,
            ecodes.KEY_LEFTMETA, ecodes.KEY_LEFTCTRL, ecodes.KEY_C, ecodes.KEY_D, ecodes.KEY_Q, ecodes.KEY_TAB,
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
                # Print D-pad, triggers; skip thumbstick noise
                if event.code in (ABS_DPAD_X, ABS_DPAD_Y, ABS_LT, ABS_RT):
                    print(f"  ABS  value={event.value:6d}  code={event.code:3d}  {name}")
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


def detect_hid_mode():
    """Auto-find the Legion Go hidraw device and show a labelled byte-diff view.

    Press each button (Y1, Y2, Y3, M3 …) while watching to identify which
    byte index and bit mask changes. Bold bytes indicate what changed.
    Output format:
      byte:  00  01  02  03  …
      value: xx  xx  xx  xx  …  (changed bytes in bold)
    """
    path = find_legion_hidraw()
    if path is None:
        print("Legion Go HID device not found. Make sure the controller is connected.")
        print("You can also pass the path explicitly: --watch-hidraw=/dev/hidrawN")
        return

    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except (PermissionError, OSError) as e:
        print(f"Cannot open {path}: {e}")
        print("Try: sudo chmod a+r /dev/hidraw*  (or add a udev rule)")
        return

    print(f"Legion Go HID device: {path}")
    print("Press buttons to identify byte/bit positions. Ctrl+C to stop.\n")
    # Print fixed header showing byte indices 0..63
    header = "  byte:  " + "  ".join(f"{i:02d}" for i in range(64))
    print(header)
    print("  " + "-" * (len(header) - 2))

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
            if pkt == prev:
                continue
            if prev is not None and len(pkt) == len(prev):
                changed = [i for i, (a, b) in enumerate(zip(prev, pkt)) if a != b]
                parts = []
                for i, b in enumerate(pkt):
                    s = f"{b:02x}"
                    parts.append(f"\033[1;33m{s}\033[0m" if i in changed else s)
                print("  value:  " + "  ".join(parts))
                # Print a summary line for changed bytes
                for i in changed:
                    old, new = prev[i], pkt[i]
                    bits_set   = new & ~old
                    bits_clear = old & ~new
                    print(f"    → byte[{i:2d}]  {old:#04x} → {new:#04x}"
                          + (f"  bits SET:   {bits_set:#04x}  ({bits_set:08b})" if bits_set   else "")
                          + (f"  bits CLEAR: {bits_clear:#04x}  ({bits_clear:08b})" if bits_clear else ""))
            else:
                print("  value:  " + "  ".join(f"{b:02x}" for b in pkt))
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


def lock_hidraw_reader(stop_event: threading.Event, cfg: dict, ui: UInput, transport=None, long_dispatcher=None):
    """
    Watch the Legion Go HID report for special button events.
    Dispatches the configured action for each button on both press and release,
    respecting the transport-lock gate and long-press dispatcher.

    Byte 18 of the 64-byte report ID 0x74:
      bit 7 (0x80) = Legion L button   → cfg["legion_btn"]
      bit 6 (0x40) = Settings button   → cfg["settings_btn"]
    Byte 20 of the 64-byte report ID 0x74:
      bit 7 (0x80) = Y1 button         → cfg["btn_y1"]
      bit 6 (0x40) = Y2 button         → cfg["btn_y2"]
      bit 5 (0x20) = Y3 button         → cfg["btn_y3"]
      bit 2 (0x04) = M3 button         → cfg["btn_m3"]
    """
    path = find_legion_hidraw()
    if path is None:
        print("HID reader: Legion Go HID device not found — feature disabled.")
        print("  Make sure the controller is connected and you have read access to /dev/hidraw*.")
        return
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except (PermissionError, OSError) as e:
        print(f"HID reader: cannot open {path}: {e}")
        print("  Try adding a udev rule: KERNEL==\"hidraw*\", ATTRS{idVendor}==\"17ef\", MODE=\"0660\", GROUP=\"input\"")
        return

    _BYTE18_KEYS = (
        (0x80, "legion_btn"),
        (0x40, "settings_btn"),
    )
    _BYTE20_KEYS = (
        (0x80, "btn_y1"),
        (0x40, "btn_y2"),
        (0x20, "btn_y3"),
        (0x04, "btn_m3"),
    )

    print(f"HID reader: monitoring {path}")
    prev18 = 0
    prev20 = 0
    try:
        while not stop_event.is_set():
            ready, _, _ = select.select([fd], [], [], 0.5)
            if not ready:
                continue
            try:
                pkt = os.read(fd, 64)
            except OSError:
                break
            if len(pkt) < _EXTRA_BTN_BYTE + 1 or pkt[2] != _LEGION_REPORT_ID:
                continue

            b18 = pkt[_LEGION_BTN_BYTE] & _LEGION_BTN_MASK
            b20 = pkt[_EXTRA_BTN_BYTE]  & _EXTRA_BTN_MASK
            if b18 == prev18 and b20 == prev20:
                continue

            for btns, prev, pairs in (
                (b18, prev18, _BYTE18_KEYS),
                (b20, prev20, _BYTE20_KEYS),
            ):
                for mask, cfg_key in pairs:
                    rising  = bool(btns & mask) and not bool(prev & mask)
                    falling = not bool(btns & mask) and bool(prev & mask)
                    _hid_button_edge(
                        cfg_key=cfg_key, rising=rising, falling=falling,
                        cfg=cfg, ui=ui, transport=transport,
                        long_dispatcher=long_dispatcher,
                    )

            prev18 = b18
            prev20 = b20
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


class GnomeScreenSaverWatcher(threading.Thread):
    """Subscribes to org.gnome.ScreenSaver.ActiveChanged on the session bus.

    On active=False (session unlocked), calls transport.unlock(). On
    active=True (session locked), does nothing — asymmetric by design.
    Gated by cfg['gnome_auto_unlock']. Degrades silently if dbus or
    GNOME isn't available.
    """

    def __init__(self, transport, cfg: dict):
        super().__init__(daemon=True)
        self._transport = transport
        self._cfg = cfg

    def _on_active_changed(self, active: bool) -> None:
        if not active:
            self._transport.unlock()

    def run(self) -> None:
        if not self._cfg.get("gnome_auto_unlock", False):
            return
        try:
            import dbus
            from dbus.mainloop.glib import DBusGMainLoop
            from gi.repository import GLib
        except ImportError:
            print("[gnome-watcher] python3-dbus or gi not available — disabled.")
            return
        try:
            DBusGMainLoop(set_as_default=True)
            bus = dbus.SessionBus()
            bus.add_signal_receiver(
                self._on_active_changed,
                signal_name="ActiveChanged",
                dbus_interface="org.gnome.ScreenSaver",
            )
            GLib.MainLoop().run()
        except Exception as e:
            print(f"[gnome-watcher] setup failed ({e}) — auto-unlock disabled.")
            return


# ── Mouse mover thread ────────────────────────────────────────────────────────

def _mouse_mover_tick(state: State, ui: UInput, transport, remainder_x: float = 0.0, remainder_y: float = 0.0):
    """One iteration of the mouse_mover loop. Gated by transport.locked.

    Returns updated (remainder_x, remainder_y) for accumulation across iterations.
    """
    if transport is not None and transport.locked:
        return remainder_x, remainder_y

    interval = 1.0 / POLL_HZ
    raw_x, raw_y, _ = state.combined_mouse_vector()
    with state.lock:
        orientation = state.orientation
    rot_x, rot_y = rotate_for_orientation(raw_x, raw_y, orientation)
    nx, ny = apply_deadzone_and_curve(rot_x, rot_y, math.hypot(rot_x, rot_y))

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

    return remainder_x, remainder_y


def mouse_mover(state: State, ui: UInput, stop_event: threading.Event, transport=None):
    interval = 1.0 / POLL_HZ
    remainder_x = 0.0
    remainder_y = 0.0
    while not stop_event.is_set():
        t0 = time.monotonic()
        remainder_x, remainder_y = _mouse_mover_tick(state, ui, transport, remainder_x, remainder_y)
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


class TriggerKey:
    """Converts a unipolar trigger axis (0..TRIGGER_MAX) into press/release events.

    A press is fired when value crosses above 50% of TRIGGER_MAX; released when
    it drops back below the threshold.
    """

    THRESHOLD = TRIGGER_MAX * 0.5

    def __init__(self):
        self.pressed = False


# ── Event processing ──────────────────────────────────────────────────────────

def _dispatch_button_action(action: str, val: int, ui, transport=None):
    """Emit output for a digital button press (val=1) or release (val=0)."""
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
    elif action == "transport_mode":
        if val == 1 and transport is not None:
            transport.toggle()
    elif action in ACTION_TO_EVKEY:
        ui.write(ecodes.EV_KEY, ACTION_TO_EVKEY[action], val)
        ui.syn()
    # "none" or unknown: do nothing


def _hid_button_edge(cfg_key: str, rising: bool, falling: bool, *,
                     cfg: dict, ui, transport, long_dispatcher) -> None:
    """Handle a rising/falling edge for a HID-read button, respecting
    the transport-lock gate and long-press dispatcher."""
    if not rising and not falling:
        return
    action = cfg.get(cfg_key, "none")
    long_action = cfg.get(f"{cfg_key}_long", "none")

    # Transport-lock gate: while locked, suppress unless action OR long_action
    # is transport_mode (the unlock path).
    if transport is not None and transport.locked:
        if action != "transport_mode" and long_action != "transport_mode":
            return

    if long_dispatcher is None or long_action == "none":
        _dispatch_button_action(action, 1 if rising else 0, ui, transport)
        return

    if rising:
        long_dispatcher.press(cfg_key, short=action, long_action=long_action)
    elif falling:
        long_dispatcher.release(cfg_key)


def _dispatch_trigger(key: TriggerKey, value: int, action: str, ui, transport=None):
    """Update trigger press state and fire press/release when it crosses the threshold."""
    if action == "none":
        return
    now_pressed = value > TriggerKey.THRESHOLD
    if now_pressed == key.pressed:
        return
    key.pressed = now_pressed
    _dispatch_button_action(action, 1 if now_pressed else 0, ui, transport)


def handle_event(event, state: State, ui: UInput, dpad: DpadKeys,
                 ls_keys, rs_keys, lt_key: TriggerKey, rt_key: TriggerKey,
                 cfg: dict, transport=None, long_dispatcher=None):
    """Process a single evdev event using the loaded config."""
    if transport is not None and transport.locked:
        return

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
        elif code == ABS_LT:
            _dispatch_trigger(lt_key, event.value, cfg.get("lt", "none"), ui, transport)
        elif code == ABS_RT:
            _dispatch_trigger(rt_key, event.value, cfg.get("rt", "none"), ui, transport)

    elif event.type == ecodes.EV_KEY:
        cfg_key = EVCODE_TO_CONFIG_KEY.get(event.code)
        if cfg_key is None:
            return
        action = cfg.get(cfg_key, "none")
        long_action = cfg.get(f"{cfg_key}_long", "none")
        val = event.value
        if val == 2:
            return
        if long_dispatcher is None or long_action == "none":
            # Legacy fast path — no deferral
            _dispatch_button_action(action, val, ui, transport)
        else:
            if val == 1:
                long_dispatcher.press(cfg_key, short=action, long_action=long_action)
            else:
                long_dispatcher.release(cfg_key)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if "--detect-all" in sys.argv:
        detect_all_mode()
        return

    if "--detect-hid" in sys.argv:
        detect_hid_mode()
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

    hidraw_path = find_legion_hidraw()
    leds = LedController(
        hidraw_path,
        color_enabled=cfg.get("led_color_enabled"),
        color_locked=cfg.get("led_color_locked"),
    )
    leds.set_enabled()           # go solid color immediately
    transport = TransportMode(leds)
    long_dispatcher = LongPressDispatcher(
        ui, transport, long_press_ms=cfg.get("long_press_ms", 500)
    )

    gnome_watcher = GnomeScreenSaverWatcher(transport, cfg)
    gnome_watcher.start()

    ls_keys = (StickKeys(ui, ABS_LS_X, ABS_LS_Y)
               if cfg["left_stick"] == "arrow_keys" else None)
    rs_keys = (StickKeys(ui, ABS_RS_X, ABS_RS_Y)
               if cfg["right_stick"] == "arrow_keys" else None)
    lt_key  = TriggerKey()
    rt_key  = TriggerKey()

    print("Mapper running. Ctrl+C to stop.")
    print(f"  Thumbsticks → mouse  (speed={MOUSE_SPEED} px/s, deadzone={DEADZONE})")
    print("  Bindings loaded from config — run with --configure to change.")
    print()

    state      = State()
    OrientationWatcher(state)
    dpad       = DpadKeys(ui)
    stop_event = threading.Event()

    if cfg.get("left_stick", "none") == "mouse" or cfg.get("right_stick", "none") == "mouse":
        mover = threading.Thread(
            target=mouse_mover, args=(state, ui, stop_event, transport), daemon=True
        )
        mover.start()
    else:
        mover = None

    locker = None
    _HID_BTN_KEYS = ("legion_btn", "settings_btn", "btn_y1", "btn_y2", "btn_y3", "btn_m3")
    if any(cfg.get(k, "none") != "none" for k in _HID_BTN_KEYS):
        locker = threading.Thread(
            target=lock_hidraw_reader,
            args=(stop_event, cfg, ui, transport, long_dispatcher),
            daemon=True,
        )
        locker.start()

    try:
        for event in dev.read_loop():
            handle_event(event, state, ui, dpad, ls_keys, rs_keys, lt_key, rt_key,
                         cfg, transport, long_dispatcher)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        stop_event.set()
        if mover is not None:
            mover.join(timeout=1)
        if locker is not None:
            locker.join(timeout=1)
        leds.set_off()
        leds.close()
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
