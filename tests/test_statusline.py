"""Phase 2 tests for statusline render-error indicator."""
from __future__ import annotations

import json
import time
from pathlib import Path

import hooks.statusline as statusline


def _point_error_log_at(tmp_path: Path, monkeypatch) -> Path:
    p = tmp_path / "render_errors.json"
    monkeypatch.setattr(statusline, "_RENDER_ERROR_LOG", p)
    return p


def test_indicator_silent_when_no_errors(tmp_path, monkeypatch):
    _point_error_log_at(tmp_path, monkeypatch)
    assert statusline._render_error_indicator() == ""


def test_indicator_silent_below_threshold(tmp_path, monkeypatch):
    p = _point_error_log_at(tmp_path, monkeypatch)
    p.write_text(json.dumps([time.time()]), encoding="utf-8")
    # one recent error < threshold=3 → silent
    assert statusline._render_error_indicator() == ""


def test_indicator_fires_at_threshold(tmp_path, monkeypatch):
    p = _point_error_log_at(tmp_path, monkeypatch)
    now = time.time()
    p.write_text(json.dumps([now - 10, now - 5, now - 1]), encoding="utf-8")
    out = statusline._render_error_indicator()
    assert "\u26A0" in out
    assert "F3" in out


def test_indicator_ignores_old_entries(tmp_path, monkeypatch):
    p = _point_error_log_at(tmp_path, monkeypatch)
    now = time.time()
    # three errors, all older than the 5-min window
    p.write_text(json.dumps([now - 400, now - 500, now - 600]), encoding="utf-8")
    assert statusline._render_error_indicator() == ""


def test_record_appends_and_caps(tmp_path, monkeypatch):
    p = _point_error_log_at(tmp_path, monkeypatch)
    for _ in range(15):
        statusline._record_render_error()
    entries = json.loads(p.read_text(encoding="utf-8"))
    assert isinstance(entries, list)
    # cap at _ERROR_LOG_MAX
    assert len(entries) == statusline._ERROR_LOG_MAX
    assert all(isinstance(t, (int, float)) for t in entries)


def test_record_then_indicator_fires(tmp_path, monkeypatch):
    _point_error_log_at(tmp_path, monkeypatch)
    for _ in range(3):
        statusline._record_render_error()
    out = statusline._render_error_indicator()
    assert "\u26A0" in out


def test_indicator_tolerates_corrupt_file(tmp_path, monkeypatch):
    p = _point_error_log_at(tmp_path, monkeypatch)
    p.write_text("not json", encoding="utf-8")
    # must not raise, returns empty string
    assert statusline._render_error_indicator() == ""
