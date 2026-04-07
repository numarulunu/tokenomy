"""Atomic state read/write tests."""
from __future__ import annotations

import json
import os

from tuner.state import empty_state, load_state, save_state


def test_load_missing_returns_empty(tmp_path):
    s = load_state(str(tmp_path / "nope.json"))
    assert s == empty_state()


def test_save_then_load(tmp_path):
    p = str(tmp_path / "applied.json")
    s = empty_state()
    s["caps"] = {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 6000}
    save_state(p, s)
    loaded = load_state(p)
    assert loaded["caps"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == 6000


def test_load_corrupt_returns_empty(tmp_path):
    p = tmp_path / "applied.json"
    p.write_text("not json{", encoding="utf-8")
    assert load_state(str(p)) == empty_state()


def test_load_non_dict_returns_empty(tmp_path):
    p = tmp_path / "applied.json"
    p.write_text("[1,2,3]", encoding="utf-8")
    assert load_state(str(p)) == empty_state()


def test_save_atomic_no_partial(tmp_path):
    p = str(tmp_path / "applied.json")
    save_state(p, empty_state())
    assert os.path.exists(p)
    # no leftover .tmp files
    leftovers = [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]
    assert leftovers == []
