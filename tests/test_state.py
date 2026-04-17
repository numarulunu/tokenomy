"""Atomic state read/write + schema migration tests."""
from __future__ import annotations

import json
import logging
import os

from tuner.state import SCHEMA_VERSION, _migrate, empty_state, load_state, save_state


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


# ─────────────── migration dispatcher ───────────────


def test_migrate_noop_when_current():
    data = {"version": SCHEMA_VERSION, "caps": {"X": 1}}
    out = _migrate(data)
    assert out["version"] == SCHEMA_VERSION
    assert out["caps"] == {"X": 1}


def test_migrate_unknown_version_stamps_with_warning(caplog):
    data = {"version": "99.99.99", "caps": {}}
    with caplog.at_level(logging.WARNING, logger="tuner.state"):
        out = _migrate(data)
    assert out["version"] == SCHEMA_VERSION
    assert any("no migration path" in r.message for r in caplog.records)


def test_migrate_missing_version_stamps():
    data = {"caps": {"MAX_THINKING_TOKENS": 8000}}
    out = _migrate(data)
    assert out["version"] == SCHEMA_VERSION
    assert out["caps"]["MAX_THINKING_TOKENS"] == 8000


def test_migrate_applies_registered_chain(monkeypatch):
    """Dispatcher must walk a multi-hop chain and invoke each migrator in order."""
    from tuner import state

    calls: list[str] = []

    def up_a(d):
        calls.append("a")
        d["stage_a"] = True

    def up_b(d):
        calls.append("b")
        d["stage_b"] = True

    fake_chain = [("0.5.0", "0.5.5", up_a), ("0.5.5", SCHEMA_VERSION, up_b)]
    monkeypatch.setattr(state, "_MIGRATIONS", fake_chain)

    data = {"version": "0.5.0", "caps": {"Z": 42}}
    out = state._migrate(data)
    assert calls == ["a", "b"]
    assert out["version"] == SCHEMA_VERSION
    assert out["stage_a"] is True
    assert out["stage_b"] is True
    assert out["caps"] == {"Z": 42}


def test_load_state_preserves_caps_on_mismatched_version(tmp_path):
    p = tmp_path / "applied.json"
    p.write_text(json.dumps({"version": "0.0.1", "caps": {"A": 1}}), encoding="utf-8")
    loaded = load_state(str(p))
    assert loaded["version"] == SCHEMA_VERSION
    assert loaded["caps"] == {"A": 1}
