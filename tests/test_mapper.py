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
             "KEY_Y","KEY_ENTER","KEY_ESC","KEY_BACKSPACE","KEY_DELETE"]:
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
    # Verify round-trip through load_config, not just raw JSON
    reloaded = m.load_config()
    assert reloaded["btn_y"] == "key_esc"
    assert reloaded["left_stick"] == m.DEFAULT_CONFIG["left_stick"]


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
    # Pick control 4 (btn_y), choose action 10 (key_return = index 9 in BUTTON_ACTIONS),
    # then save.  We skip the service restart by patching subprocess.run.
    inputs = iter(["4", "10", "s"])
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
    # Simulate save_config raising OSError (e.g. permission denied).
    # After the error the menu must re-display (not exit), so the user can
    # retry or quit explicitly — verified by providing "q" after "s" fails.
    monkeypatch.setattr(m, "save_config", mock.MagicMock(side_effect=OSError("Permission denied")))
    inputs = iter(["s", "q"])
    monkeypatch.setattr("builtins.input", lambda _="": next(inputs))
    m.configure_mode()
    out = capsys.readouterr().out
    assert "Error" in out or "error" in out
    # Menu re-displayed after the failed save (heading appears twice)
    assert out.count("Legion Go Gamepad") >= 2


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
    ui.syn.assert_called_once()


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
            r.returncode = 0
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
            r.returncode = 0
            r.stdout = "true\n"
            return r
        return mock.MagicMock()
    monkeypatch.setattr(m.subprocess, "run", fake_run)
    m.toggle_osk()
    set_call = next(c for c in calls if "set" in c)
    assert set_call[-1] == "false"


# ── rotate_for_orientation tests ──────────────────────────────────────────────

def test_rotate_normal_is_identity():
    assert m.rotate_for_orientation(1.0, 0.5, "normal") == (1.0, 0.5)

def test_rotate_right_up():
    # 90° CW: (x, y) → (y, -x)
    x, y = m.rotate_for_orientation(1.0, 0.0, "right-up")
    assert abs(x - 0.0) < 1e-9
    assert abs(y - (-1.0)) < 1e-9

def test_rotate_left_up():
    # 90° CCW: (x, y) → (-y, x)
    x, y = m.rotate_for_orientation(1.0, 0.0, "left-up")
    assert abs(x - 0.0) < 1e-9
    assert abs(y - 1.0) < 1e-9

def test_rotate_bottom_up():
    # 180°: (x, y) → (-x, -y)
    x, y = m.rotate_for_orientation(1.0, 0.5, "bottom-up")
    assert abs(x - (-1.0)) < 1e-9
    assert abs(y - (-0.5)) < 1e-9

def test_rotate_unknown_orientation_falls_back_to_normal():
    assert m.rotate_for_orientation(1.0, 0.5, "unknown-value") == (1.0, 0.5)

def test_rotate_preserves_magnitude():
    import math
    x, y = m.rotate_for_orientation(0.6, 0.8, "right-up")
    assert abs(math.hypot(x, y) - 1.0) < 1e-9


# ── State.orientation tests ───────────────────────────────────────────────────

def test_state_orientation_defaults_to_normal():
    s = m.State()
    assert s.orientation == "normal"

def test_state_set_orientation():
    s = m.State()
    s.set_orientation("right-up")
    assert s.orientation == "right-up"

def test_state_set_orientation_is_threadsafe():
    """set_orientation acquires the lock (smoke test — not a race detector)."""
    import threading
    s = m.State()
    results = []
    def setter():
        s.set_orientation("left-up")
        results.append(s.orientation)

    with s.lock:
        t = threading.Thread(target=setter)
        t.start()
        # Thread is now blocked waiting for the lock

    # Lock is released, thread can now proceed
    t.join(timeout=1)
    assert results == ["left-up"]


# ── OrientationWatcher tests ──────────────────────────────────────────────────

def test_orientation_watcher_sets_initial_orientation(monkeypatch):
    """Watcher reads initial orientation from proxy and updates State."""
    s = m.State()
    fake_proxy = mock.MagicMock()
    fake_proxy.Get.return_value = "right-up"

    fake_dbus = mock.MagicMock()
    fake_iface = mock.MagicMock(return_value=fake_proxy)
    fake_dbus.Interface = fake_iface
    monkeypatch.setitem(sys.modules, "dbus", fake_dbus)
    monkeypatch.setitem(sys.modules, "dbus.mainloop.glib", mock.MagicMock())

    monkeypatch.setattr(m.OrientationWatcher, "_subscribe", lambda self: None)

    w = m.OrientationWatcher(s)
    w._read_initial()
    assert s.orientation == "right-up"


def test_orientation_watcher_updates_on_signal(monkeypatch):
    """_on_properties_changed updates State.orientation."""
    s = m.State()
    w = m.OrientationWatcher.__new__(m.OrientationWatcher)
    w.state = s
    w._on_properties_changed(
        "net.hadess.SensorProxy",
        {"AccelerometerOrientation": "left-up"},
        [],
    )
    assert s.orientation == "left-up"


def test_orientation_watcher_ignores_irrelevant_signals(monkeypatch):
    """_on_properties_changed ignores signals without AccelerometerOrientation."""
    s = m.State()
    s.set_orientation("right-up")
    w = m.OrientationWatcher.__new__(m.OrientationWatcher)
    w.state = s
    w._on_properties_changed("net.hadess.SensorProxy", {"SomeOtherProp": "x"}, [])
    assert s.orientation == "right-up"


def test_orientation_watcher_missing_dbus_is_graceful(monkeypatch):
    """If dbus import fails, OrientationWatcher.__init__ returns without crash."""
    s = m.State()
    monkeypatch.setitem(sys.modules, "dbus", None)
    try:
        w = m.OrientationWatcher(s)
    except Exception as e:
        pytest.fail(f"OrientationWatcher raised unexpectedly: {e}")
    assert s.orientation == "normal"


# ── LED protocol byte builders ────────────────────────────────────────────────

def test_rgb_controller_code_both():
    assert m._rgb_controller_code("both") == 0x03
    assert m._rgb_controller_code("left") == 0x02
    assert m._rgb_controller_code("right") == 0x01


def test_rgb_build_set_profile_solid_yellow():
    # Yellow = (255, 180, 0), solid mode, full brightness, both rings, profile 1
    pkt = m._rgb_build_set_profile(
        controller="both", profile=1, mode="solid",
        r=255, g=180, b=0, brightness=1.0, speed=1.0,
    )
    assert pkt == bytes([
        0x05, 0x0C, 0x72, 0x01,
        0x03,        # controller=both
        1,           # mode=solid
        255, 180, 0, # RGB
        63,          # brightness (int(64*1.0)=64 clamped to 63)
        0,           # period (int(64*(1-1.0))=0)
        1,           # profile
        0x01,
    ])


def test_rgb_build_set_profile_pulse_red_slow():
    # Red breathing at ~0.25 Hz (4s cycle). Speed=0.0 → period=63 (slowest).
    pkt = m._rgb_build_set_profile(
        controller="both", profile=1, mode="pulse",
        r=255, g=0, b=0, brightness=1.0, speed=0.0,
    )
    assert pkt[5] == 2          # mode=pulse
    assert pkt[6:9] == bytes([255, 0, 0])
    assert pkt[10] == 63        # period=63 means slowest


def test_rgb_build_load_profile():
    assert m._rgb_build_load_profile(controller="both", profile=1) == bytes(
        [0x05, 0x06, 0x73, 0x02, 0x03, 1, 0x01]
    )


def test_rgb_build_enable():
    assert m._rgb_build_enable(controller="both", enable=True) == bytes(
        [0x05, 0x06, 0x70, 0x02, 0x03, 1, 0x01]
    )
    assert m._rgb_build_enable(controller="both", enable=False) == bytes(
        [0x05, 0x06, 0x70, 0x02, 0x03, 0, 0x01]
    )

# ── LedController ─────────────────────────────────────────────────────────────

def test_led_controller_no_path_is_noop(monkeypatch):
    # Constructor with None path must not raise and must make all methods no-op
    led = m.LedController(None)
    led.set_enabled()    # must not raise
    led.set_locked()     # must not raise
    led.set_off()        # must not raise
    led.close()


def test_led_controller_set_enabled_writes_yellow(tmp_path, monkeypatch):
    writes = []

    def fake_open(path, flags):
        return 99  # sentinel fd

    def fake_write(fd, data):
        assert fd == 99
        writes.append(bytes(data))
        return len(data)

    def fake_close(fd):
        pass

    monkeypatch.setattr(m.os, "open", fake_open)
    monkeypatch.setattr(m.os, "write", fake_write)
    monkeypatch.setattr(m.os, "close", fake_close)

    led = m.LedController("/dev/hidrawX")
    led.set_enabled()

    # Expect 3 writes: set_profile, load_profile, enable (for "both" rings).
    assert len(writes) == 3
    # First write: set_profile with solid mode, yellow, brightness=1.0, speed=1.0
    first = writes[0]
    assert first[0] == 0x05
    assert first[2] == 0x72      # command byte for set_profile
    assert first[5] == 1          # mode=solid
    assert first[6:9] == bytes([255, 180, 0])  # RGB yellow


def test_led_controller_set_locked_writes_red_pulse(tmp_path, monkeypatch):
    writes = []
    monkeypatch.setattr(m.os, "open", lambda p, f: 99)
    monkeypatch.setattr(m.os, "write", lambda fd, d: writes.append(bytes(d)) or len(d))
    monkeypatch.setattr(m.os, "close", lambda fd: None)

    led = m.LedController("/dev/hidrawX")
    led.set_locked()

    assert writes[0][5] == 2              # mode=pulse
    assert writes[0][6:9] == bytes([255, 0, 0])  # RGB red
    assert writes[0][10] == 63            # period=63 means slowest (~0.25 Hz)


def test_led_controller_set_off_sends_disable(tmp_path, monkeypatch):
    writes = []
    monkeypatch.setattr(m.os, "open", lambda p, f: 99)
    monkeypatch.setattr(m.os, "write", lambda fd, d: writes.append(bytes(d)) or len(d))
    monkeypatch.setattr(m.os, "close", lambda fd: None)

    led = m.LedController("/dev/hidrawX")
    led.set_off()
    # set_off issues a single rgb_enable(False)
    assert len(writes) == 1
    assert writes[0][2] == 0x70       # enable command byte
    assert writes[0][5] == 0           # enable=False


def test_led_controller_swallows_oserror(tmp_path, monkeypatch):
    def raising_write(fd, data):
        raise OSError("device gone")

    monkeypatch.setattr(m.os, "open", lambda p, f: 99)
    monkeypatch.setattr(m.os, "write", raising_write)
    monkeypatch.setattr(m.os, "close", lambda fd: None)

    led = m.LedController("/dev/hidrawX")
    # Must not raise — errors are logged and swallowed
    led.set_enabled()
    led.set_locked()
    led.set_off()


# ── TransportMode ─────────────────────────────────────────────────────────────

class _FakeLeds:
    def __init__(self):
        self.calls = []
    def set_enabled(self): self.calls.append("enabled")
    def set_locked(self): self.calls.append("locked")
    def set_off(self): self.calls.append("off")
    def close(self): self.calls.append("close")


def test_transport_mode_starts_unlocked():
    t = m.TransportMode(_FakeLeds())
    assert t.locked is False


def test_transport_toggle_locks_and_sets_red():
    leds = _FakeLeds()
    t = m.TransportMode(leds)
    t.toggle()
    assert t.locked is True
    assert leds.calls == ["locked"]


def test_transport_toggle_twice_unlocks_and_sets_yellow():
    leds = _FakeLeds()
    t = m.TransportMode(leds)
    t.toggle()
    t.toggle()
    assert t.locked is False
    assert leds.calls == ["locked", "enabled"]


def test_transport_unlock_when_already_unlocked_is_noop():
    leds = _FakeLeds()
    t = m.TransportMode(leds)
    t.unlock()
    assert t.locked is False
    assert leds.calls == []     # no LED write


def test_transport_unlock_when_locked_sets_yellow():
    leds = _FakeLeds()
    t = m.TransportMode(leds)
    t.toggle()      # locked
    t.unlock()
    assert t.locked is False
    assert leds.calls == ["locked", "enabled"]
