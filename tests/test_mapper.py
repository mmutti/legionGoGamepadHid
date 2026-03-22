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
    # Verify round-trip through load_config, not just raw JSON
    reloaded = m.load_config()
    assert reloaded["btn_y"] == "key_esc"
    assert reloaded["left_stick"] == m.DEFAULT_CONFIG["left_stick"]
