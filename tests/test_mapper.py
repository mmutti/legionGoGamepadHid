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
    # Pick control 4 (btn_y), choose action 11 (key_return = index 10 in BUTTON_ACTIONS),
    # then "0" for long-press (Disabled), then save.
    # We skip the service restart by patching subprocess.run.
    inputs = iter(["4", "11", "0", "s"])
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


# ── transport_mode action dispatch ────────────────────────────────────────────

def test_transport_mode_action_in_button_actions():
    action_keys = [k for k, _ in m.BUTTON_ACTIONS]
    assert "transport_mode" in action_keys


def test_dispatch_transport_mode_calls_toggle_on_press():
    leds = _FakeLeds()
    transport = m.TransportMode(leds)
    ui = mock.MagicMock()
    m._dispatch_button_action("transport_mode", 1, ui, transport)
    assert transport.locked is True


def test_dispatch_transport_mode_ignores_release():
    leds = _FakeLeds()
    transport = m.TransportMode(leds)
    ui = mock.MagicMock()
    # val=0 is a release; transport_mode must not toggle on release
    m._dispatch_button_action("transport_mode", 0, ui, transport)
    assert transport.locked is False


# ── handle_event gating ───────────────────────────────────────────────────────

def _make_locked_transport():
    leds = _FakeLeds()
    t = m.TransportMode(leds)
    t.toggle()  # now locked
    return t


def test_handle_event_dropped_when_locked():
    transport = _make_locked_transport()
    ui = mock.MagicMock()
    cfg = dict(m.DEFAULT_CONFIG)
    ev = mock.MagicMock()
    ev.type = evdev.ecodes.EV_KEY
    ev.code = evdev.ecodes.BTN_Y
    ev.value = 1

    m.handle_event(ev, m.State(), ui, m.DpadKeys(ui),
                   None, None, m.TriggerKey(), m.TriggerKey(),
                   cfg, transport)

    ui.write.assert_not_called()


def test_handle_event_processed_when_unlocked():
    leds = _FakeLeds()
    transport = m.TransportMode(leds)   # unlocked
    ui = mock.MagicMock()
    cfg = dict(m.DEFAULT_CONFIG)
    cfg["btn_y"] = "arrow_up"
    ev = mock.MagicMock()
    ev.type = evdev.ecodes.EV_KEY
    ev.code = evdev.ecodes.BTN_Y
    ev.value = 1

    m.handle_event(ev, m.State(), ui, m.DpadKeys(ui),
                   None, None, m.TriggerKey(), m.TriggerKey(),
                   cfg, transport)

    assert ui.write.called


# ── mouse_mover gating ────────────────────────────────────────────────────────

def test_mouse_mover_tick_locked_emits_nothing():
    """Single iteration of mouse_mover with locked=True emits no writes."""
    leds = _FakeLeds()
    transport = m.TransportMode(leds)
    transport.toggle()   # locked
    state = m.State()
    # Simulate stick deflection so the unlocked path WOULD emit writes
    state.update_axis(m.ABS_LS_X, int(0.8 * m.AXIS_MAX))
    ui = mock.MagicMock()

    m._mouse_mover_tick(state, ui, transport)
    ui.write.assert_not_called()


def test_mouse_mover_tick_unlocked_emits_when_deflected():
    leds = _FakeLeds()
    transport = m.TransportMode(leds)   # unlocked
    state = m.State()
    state.update_axis(m.ABS_LS_X, int(0.8 * m.AXIS_MAX))
    ui = mock.MagicMock()

    m._mouse_mover_tick(state, ui, transport)
    assert ui.write.called


# ── lock_hidraw_reader selective gating ───────────────────────────────────────

# ── LongPressDispatcher ───────────────────────────────────────────────────────

class _FakeTimer:
    """Stand-in for threading.Timer with manual fire control."""
    instances = []
    def __init__(self, interval, func):
        self.interval = interval
        self.func = func
        self.started = False
        self.cancelled = False
        _FakeTimer.instances.append(self)
    def start(self):
        self.started = True
    def cancel(self):
        self.cancelled = True
    def fire(self):
        if not self.cancelled:
            self.func()


def _new_dispatcher(monkeypatch, long_press_ms=500):
    _FakeTimer.instances = []
    monkeypatch.setattr(m.threading, "Timer", _FakeTimer)
    ui = mock.MagicMock()
    leds = _FakeLeds()
    transport = m.TransportMode(leds)
    return m.LongPressDispatcher(ui, transport, long_press_ms), ui, transport


def test_longpress_no_long_action_fires_instantly(monkeypatch):
    disp, ui, transport = _new_dispatcher(monkeypatch)
    disp.press("btn_y", short="key_y", long_action="none")
    # No timer started; action dispatched on the press
    assert _FakeTimer.instances == []
    assert ui.write.called    # key_y fires on press


def test_longpress_with_long_action_defers_short(monkeypatch):
    disp, ui, transport = _new_dispatcher(monkeypatch)
    disp.press("btn_y", short="key_y", long_action="transport_mode")
    assert len(_FakeTimer.instances) == 1
    assert _FakeTimer.instances[0].started is True
    # Short action NOT fired yet
    ui.write.assert_not_called()


def test_longpress_release_before_timeout_fires_short(monkeypatch):
    disp, ui, transport = _new_dispatcher(monkeypatch)
    disp.press("btn_y", short="key_y", long_action="transport_mode")
    disp.release("btn_y")
    # Timer cancelled; short action fired
    assert _FakeTimer.instances[0].cancelled is True
    # ui.write called for key_y press+release (two EV_KEY writes + syns)
    assert ui.write.call_count >= 2


def test_longpress_timeout_fires_long_and_suppresses_release(monkeypatch):
    disp, ui, transport = _new_dispatcher(monkeypatch)
    disp.press("legion_btn", short="lock_screen", long_action="transport_mode")
    # Simulate timer firing
    _FakeTimer.instances[0].fire()
    # Long action fired — transport toggled
    assert transport.locked is True
    # Release now should NOT re-fire anything
    prev_call_count = ui.write.call_count
    disp.release("legion_btn")
    assert ui.write.call_count == prev_call_count


def test_handle_event_uses_longpress_dispatcher(monkeypatch):
    """EV_KEY events flow through LongPressDispatcher when one is provided."""
    _FakeTimer.instances = []
    monkeypatch.setattr(m.threading, "Timer", _FakeTimer)

    leds = _FakeLeds()
    transport = m.TransportMode(leds)
    ui = mock.MagicMock()
    cfg = dict(m.DEFAULT_CONFIG)
    cfg["btn_y"] = "key_y"
    cfg["btn_y_long"] = "transport_mode"

    disp = m.LongPressDispatcher(ui, transport, long_press_ms=500)

    ev = mock.MagicMock()
    ev.type = evdev.ecodes.EV_KEY
    ev.code = evdev.ecodes.BTN_Y
    ev.value = 1

    m.handle_event(ev, m.State(), ui, m.DpadKeys(ui),
                   None, None, m.TriggerKey(), m.TriggerKey(),
                   cfg, transport, disp)

    # Since btn_y_long is set, timer must be started, and short not yet fired
    assert len(_FakeTimer.instances) == 1
    assert _FakeTimer.instances[0].started is True
    ui.write.assert_not_called()


# ── _hid_button_edge tests ─────────────────────────────────────────────────────

def test_hid_button_edge_with_long_action_defers(monkeypatch):
    _FakeTimer.instances = []
    monkeypatch.setattr(m.threading, "Timer", _FakeTimer)

    leds = _FakeLeds()
    transport = m.TransportMode(leds)
    ui = mock.MagicMock()
    disp = m.LongPressDispatcher(ui, transport, long_press_ms=500)
    cfg = dict(m.DEFAULT_CONFIG)
    cfg["legion_btn"] = "lock_screen"
    cfg["legion_btn_long"] = "transport_mode"

    # Simulate a rising edge (press) on legion_btn
    m._hid_button_edge(
        cfg_key="legion_btn", rising=True, falling=False,
        cfg=cfg, ui=ui, transport=transport, long_dispatcher=disp,
    )
    # Timer started, nothing dispatched yet
    assert len(_FakeTimer.instances) == 1
    # Fire the long timer
    _FakeTimer.instances[0].fire()
    assert transport.locked is True

    # Now falling edge (release) — must NOT re-trigger anything
    prev = ui.write.call_count
    m._hid_button_edge(
        cfg_key="legion_btn", rising=False, falling=True,
        cfg=cfg, ui=ui, transport=transport, long_dispatcher=disp,
    )
    assert ui.write.call_count == prev


def test_hid_button_edge_no_long_action_fires_instantly(monkeypatch):
    leds = _FakeLeds()
    transport = m.TransportMode(leds)
    ui = mock.MagicMock()
    disp = m.LongPressDispatcher(ui, transport, long_press_ms=500)
    cfg = dict(m.DEFAULT_CONFIG)
    cfg["btn_y1"] = "key_y"
    cfg["btn_y1_long"] = "none"

    m._hid_button_edge(
        cfg_key="btn_y1", rising=True, falling=False,
        cfg=cfg, ui=ui, transport=transport, long_dispatcher=disp,
    )
    assert ui.write.called     # fired on press


# ── config defaults ───────────────────────────────────────────────────────────

def test_default_config_has_long_press_keys():
    for btn in ["btn_y", "btn_a", "btn_x", "btn_b", "btn_lb", "btn_rb",
                "btn_view", "btn_menu", "btn_l3", "btn_r3",
                "legion_btn", "settings_btn",
                "btn_y1", "btn_y2", "btn_y3", "btn_m3"]:
        assert f"{btn}_long" in m.DEFAULT_CONFIG, f"missing {btn}_long"
    assert m.DEFAULT_CONFIG["long_press_ms"] == 500
    assert m.DEFAULT_CONFIG["gnome_auto_unlock"] is False


def test_default_legion_btn_long_is_transport_mode():
    assert m.DEFAULT_CONFIG["legion_btn_long"] == "transport_mode"
    # All other long keys default to "none"
    for btn in ["btn_y", "btn_a", "btn_x", "btn_b", "btn_lb", "btn_rb",
                "btn_view", "btn_menu", "btn_l3", "btn_r3",
                "settings_btn", "btn_y1", "btn_y2", "btn_y3", "btn_m3"]:
        assert m.DEFAULT_CONFIG[f"{btn}_long"] == "none"


def test_load_config_preserves_new_keys(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"btn_y": "key_return"}))
    monkeypatch.setattr(m, "CONFIG_PATH", str(p))
    cfg = m.load_config()
    # New keys come from DEFAULT_CONFIG even for partial user configs
    assert cfg["long_press_ms"] == 500
    assert cfg["legion_btn_long"] == "transport_mode"


# ── configure_mode long-press prompt ──────────────────────────────────────────

def test_configure_mode_sets_long_action(tmp_path, monkeypatch):
    """Calling _prompt_button_binding for a discrete button returns both
    short and long actions from scripted inputs."""
    inputs = iter(["1", "1"])  # both prompts: pick first action
    outputs = []

    short, long_action = m._prompt_button_binding(
        name="Y button", ctype="button",
        current_short="arrow_up", current_long="none",
        input_fn=lambda: next(inputs),
        print_fn=lambda *a, **kw: outputs.append(" ".join(str(x) for x in a)),
    )
    expected_first = m.ACTIONS_FOR_TYPE["button"][0][0]
    assert short == expected_first
    assert long_action == expected_first


# ── GnomeScreenSaverWatcher ───────────────────────────────────────────────────

def test_gnome_watcher_does_nothing_when_disabled(monkeypatch):
    leds = _FakeLeds()
    transport = m.TransportMode(leds)
    cfg = {"gnome_auto_unlock": False}

    w = m.GnomeScreenSaverWatcher(transport, cfg)
    w.start()
    w.join(timeout=0.5)
    # thread exited without raising (no dbus access attempted)
    assert not w.is_alive()


def test_gnome_watcher_unlocks_on_active_false():
    leds = _FakeLeds()
    transport = m.TransportMode(leds)
    transport.toggle()   # locked

    # Directly invoke the handler — bypasses D-Bus setup entirely
    w = m.GnomeScreenSaverWatcher(transport, {"gnome_auto_unlock": True})
    w._on_active_changed(False)
    assert transport.locked is False


def test_gnome_watcher_ignores_active_true():
    leds = _FakeLeds()
    transport = m.TransportMode(leds)  # unlocked

    w = m.GnomeScreenSaverWatcher(transport, {"gnome_auto_unlock": True})
    w._on_active_changed(True)
    assert transport.locked is False   # unchanged


# ── LED custom colors ────────────────────────────────────────────────────────

def test_default_config_has_led_color_keys():
    assert m.DEFAULT_CONFIG["led_color_enabled"] == [255, 180, 0]
    assert m.DEFAULT_CONFIG["led_color_locked"] == [255, 0, 0]


def test_led_controller_uses_custom_color_enabled(monkeypatch):
    writes = []
    monkeypatch.setattr(m.os, "open", lambda p, f: 99)
    monkeypatch.setattr(m.os, "write", lambda fd, d: writes.append(bytes(d)) or len(d))
    monkeypatch.setattr(m.os, "close", lambda fd: None)

    led = m.LedController("/dev/hidrawX", color_enabled=[0, 255, 128])
    led.set_enabled()
    # First write is the set_profile packet; RGB bytes are at [6:9]
    assert writes[0][6:9] == bytes([0, 255, 128])


def test_led_controller_uses_custom_color_locked(monkeypatch):
    writes = []
    monkeypatch.setattr(m.os, "open", lambda p, f: 99)
    monkeypatch.setattr(m.os, "write", lambda fd, d: writes.append(bytes(d)) or len(d))
    monkeypatch.setattr(m.os, "close", lambda fd: None)

    led = m.LedController("/dev/hidrawX", color_locked=(128, 0, 255))
    led.set_locked()
    assert writes[0][6:9] == bytes([128, 0, 255])


def test_rich_main_menu_renders_non_empty_string():
    """Smoke test: rich path doesn't crash and emits non-empty ANSI output."""
    pytest.importorskip("rich")
    cfg = dict(m.DEFAULT_CONFIG)
    out = m._rich_main_menu(cfg)
    assert isinstance(out, str)
    assert len(out) > 0
    # ANSI escape codes use the ESC character (0x1B)
    assert "\x1b[" in out


def test_rich_action_list_renders_short_and_long():
    pytest.importorskip("rich")
    short_out = m._rich_action_list("Y button", "button", "arrow_up", "SHORT")
    long_out = m._rich_action_list("Y button", "button", "none", "LONG")
    assert "\x1b[" in short_out and "\x1b[" in long_out
    # The title reflects which prompt it is
    assert "SHORT" in short_out
    assert "LONG" in long_out


def test_main_menu_items_has_save_and_exit_rows():
    items = m._main_menu_items()
    assert items[-2] == ("__save__", "meta", "Save and restart service")
    assert items[-1] == ("__exit__", "meta", "Exit without saving")
    # Controls come first, meta rows last
    assert items[: len(m.CONTROLS)] == m.CONTROLS


def test_arrow_pick_action_enter_picks_preselected_current(monkeypatch):
    """Pre-selection matches `current`; Enter returns it without moving."""
    pytest.importorskip("rich")
    keys = iter([m._KEY_ENTER_CODES[0]])   # just Enter
    out = m._arrow_pick_action(
        name="Y button", ctype="button",
        current="arrow_up", which="SHORT",
        read_key_fn=lambda: next(keys),
    )
    assert out == "arrow_up"


def test_arrow_pick_action_down_then_enter_picks_next(monkeypatch):
    pytest.importorskip("rich")
    actions = m.ACTIONS_FOR_TYPE["button"]
    # Start at index 0 (current="none" → pre-selects Disabled row at end)
    # Navigate: DOWN wraps around to index 0 (first action)
    keys = iter([m._KEY_DOWN, m._KEY_ENTER_CODES[0]])
    out = m._arrow_pick_action(
        name="btn_y1", ctype="button",
        current="none", which="SHORT",
        read_key_fn=lambda: next(keys),
    )
    # Disabled pre-selected at end; DOWN wraps to index 0 (first action)
    assert out == actions[0][0]


def test_arrow_pick_action_esc_returns_none():
    pytest.importorskip("rich")
    keys = iter([m._KEY_ESC])
    out = m._arrow_pick_action(
        name="btn_y", ctype="button",
        current="arrow_up", which="SHORT",
        read_key_fn=lambda: next(keys),
    )
    assert out is None


def test_arrow_pick_action_left_also_returns_none():
    """Left arrow acts as Esc (back one level) in sub-menus."""
    pytest.importorskip("rich")
    keys = iter([m._KEY_LEFT])
    out = m._arrow_pick_action(
        name="btn_y", ctype="button",
        current="arrow_up", which="SHORT",
        read_key_fn=lambda: next(keys),
    )
    assert out is None


def test_arrow_configure_left_exits(monkeypatch, tmp_path):
    """Left at main menu acts as Esc → exits without saving."""
    pytest.importorskip("rich")
    monkeypatch.setattr(m, "CONFIG_PATH", str(tmp_path / "config.json"))
    cfg = dict(m.DEFAULT_CONFIG)
    cfg_before = dict(cfg)
    m._arrow_configure(cfg, read_key_fn=lambda: m._KEY_LEFT)
    assert cfg == cfg_before
    assert not (tmp_path / "config.json").exists()


def test_arrow_pick_action_digit_shortcut():
    pytest.importorskip("rich")
    actions = m.ACTIONS_FOR_TYPE["button"]
    # "3" is the 3rd action in ACTIONS_FOR_TYPE["button"]
    out = m._arrow_pick_action(
        name="btn_y", ctype="button",
        current="none", which="SHORT",
        read_key_fn=lambda: "3",
    )
    assert out == actions[2][0]


def test_arrow_pick_action_zero_picks_disabled():
    pytest.importorskip("rich")
    out = m._arrow_pick_action(
        name="btn_y", ctype="button",
        current="arrow_up", which="SHORT",
        read_key_fn=lambda: "0",
    )
    assert out == "none"


def test_arrow_configure_exit_row_via_arrows(monkeypatch, tmp_path):
    """Arrow down to the Exit row and press Enter; cfg is not saved."""
    pytest.importorskip("rich")
    monkeypatch.setattr(m, "CONFIG_PATH", str(tmp_path / "config.json"))
    cfg = dict(m.DEFAULT_CONFIG)
    cfg_before = dict(cfg)
    items = m._main_menu_items()
    exit_idx = next(i for i, it in enumerate(items) if it[0] == "__exit__")

    # Press DOWN enough times to land on exit row, then ENTER
    keys = [m._KEY_DOWN] * exit_idx + [m._KEY_ENTER_CODES[0]]
    keys_iter = iter(keys)
    m._arrow_configure(cfg, read_key_fn=lambda: next(keys_iter))

    # cfg unchanged, no file written
    assert cfg == cfg_before
    assert not (tmp_path / "config.json").exists()


def test_arrow_configure_q_shortcut_exits(monkeypatch, tmp_path):
    pytest.importorskip("rich")
    monkeypatch.setattr(m, "CONFIG_PATH", str(tmp_path / "config.json"))
    cfg = dict(m.DEFAULT_CONFIG)
    cfg_before = dict(cfg)
    m._arrow_configure(cfg, read_key_fn=lambda: "q")
    assert cfg == cfg_before


def test_arrow_configure_rebinds_btn_y_short_via_arrows(monkeypatch, tmp_path):
    """Press '4' (btn_y hotkey), ENTER opens it, arrow sub-menu picks Disabled."""
    pytest.importorskip("rich")
    monkeypatch.setattr(m, "CONFIG_PATH", str(tmp_path / "config.json"))
    cfg = dict(m.DEFAULT_CONFIG)

    # Keys:
    #   "4"           → activate btn_y (index 3)
    #   "0"           → sub-menu SHORT: Disabled
    #   m._KEY_ESC    → sub-menu LONG: cancel (don't change long binding)
    #   "q"           → main menu: quit
    keys = iter(["4", "0", m._KEY_ESC, "q"])
    m._arrow_configure(cfg, read_key_fn=lambda: next(keys))
    assert cfg["btn_y"] == "none"
    assert cfg["btn_y_long"] == m.DEFAULT_CONFIG["btn_y_long"]   # unchanged


def test_prompt_button_binding_rich_path_collects_output(monkeypatch):
    """Same test as test_configure_mode_sets_long_action but confirms that
    print_fn received ANSI-containing strings (proof the rich path was used)."""
    pytest.importorskip("rich")
    inputs = iter(["1", "1"])
    outputs = []
    short, long_action = m._prompt_button_binding(
        name="Y button", ctype="button",
        current_short="arrow_up", current_long="none",
        input_fn=lambda: next(inputs),
        print_fn=lambda *a, **kw: outputs.append(" ".join(str(x) for x in a)),
    )
    expected_first = m.ACTIONS_FOR_TYPE["button"][0][0]
    assert short == expected_first
    assert long_action == expected_first
    # At least one captured output line contains ANSI codes
    assert any("\x1b[" in line for line in outputs)


def test_led_controller_falls_back_to_defaults_when_colors_none(monkeypatch):
    writes = []
    monkeypatch.setattr(m.os, "open", lambda p, f: 99)
    monkeypatch.setattr(m.os, "write", lambda fd, d: writes.append(bytes(d)) or len(d))
    monkeypatch.setattr(m.os, "close", lambda fd: None)

    led = m.LedController("/dev/hidrawX")   # no color overrides
    led.set_enabled()
    assert writes[0][6:9] == bytes([255, 180, 0])   # default yellow
    writes.clear()
    led.set_locked()
    assert writes[0][6:9] == bytes([255, 0, 0])     # default red
