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


# ─────────────── per-model burn thresholds (Phase 2 of v0.8) ───────────────


def test_model_family_regex_forms():
    assert statusline.model_family("claude-opus-4-7") == "opus"
    assert statusline.model_family("claude-sonnet-4-6") == "sonnet"
    assert statusline.model_family("claude-haiku-4-5-20251001") == "haiku"


def test_model_family_legacy_form():
    # Legacy IDs where family follows the version, e.g. claude-3-5-haiku.
    assert statusline.model_family("claude-3-5-haiku") == "haiku"
    assert statusline.model_family("claude-3-5-sonnet") == "sonnet"


def test_model_family_unknown_returns_empty():
    assert statusline.model_family("") == ""
    assert statusline.model_family("gpt-5") == ""


def test_burn_thresholds_default_opus(monkeypatch):
    for k in ("TOKENOMY_BURN_YELLOW", "TOKENOMY_BURN_RED",
              "TOKENOMY_BURN_YELLOW_OPUS", "TOKENOMY_BURN_RED_OPUS"):
        monkeypatch.delenv(k, raising=False)
    y, r = statusline._burn_thresholds("opus")
    assert y == 10.0 and r == 25.0


def test_burn_thresholds_scales_for_sonnet(monkeypatch):
    for k in ("TOKENOMY_BURN_YELLOW", "TOKENOMY_BURN_RED",
              "TOKENOMY_BURN_YELLOW_SONNET", "TOKENOMY_BURN_RED_SONNET"):
        monkeypatch.delenv(k, raising=False)
    y, r = statusline._burn_thresholds("sonnet")
    assert y == 5.0 and r == 12.5


def test_burn_thresholds_scales_for_haiku(monkeypatch):
    for k in ("TOKENOMY_BURN_YELLOW", "TOKENOMY_BURN_RED",
              "TOKENOMY_BURN_YELLOW_HAIKU", "TOKENOMY_BURN_RED_HAIKU"):
        monkeypatch.delenv(k, raising=False)
    y, r = statusline._burn_thresholds("haiku")
    assert y == 2.5 and r == 6.25


def test_burn_thresholds_unknown_family_uses_opus_base(monkeypatch):
    for k in ("TOKENOMY_BURN_YELLOW", "TOKENOMY_BURN_RED"):
        monkeypatch.delenv(k, raising=False)
    y, r = statusline._burn_thresholds("")
    assert y == 10.0 and r == 25.0


def test_burn_thresholds_family_env_wins(monkeypatch):
    monkeypatch.setenv("TOKENOMY_BURN_YELLOW", "8")  # generic
    monkeypatch.setenv("TOKENOMY_BURN_YELLOW_HAIKU", "3")  # family-specific
    monkeypatch.delenv("TOKENOMY_BURN_RED", raising=False)
    monkeypatch.delenv("TOKENOMY_BURN_RED_HAIKU", raising=False)
    y, _r = statusline._burn_thresholds("haiku")
    assert y == 3.0


def test_burn_thresholds_generic_env_wins_over_scaled(monkeypatch):
    monkeypatch.setenv("TOKENOMY_BURN_YELLOW", "7")
    monkeypatch.delenv("TOKENOMY_BURN_YELLOW_HAIKU", raising=False)
    monkeypatch.delenv("TOKENOMY_BURN_RED", raising=False)
    monkeypatch.delenv("TOKENOMY_BURN_RED_HAIKU", raising=False)
    y, _r = statusline._burn_thresholds("haiku")
    # Generic env is absolute, not further scaled
    assert y == 7.0


def test_color_burn_respects_family_scale(monkeypatch):
    for k in ("TOKENOMY_BURN_YELLOW", "TOKENOMY_BURN_RED",
              "TOKENOMY_BURN_YELLOW_HAIKU", "TOKENOMY_BURN_RED_HAIKU",
              "TOKENOMY_BURN_YELLOW_OPUS", "TOKENOMY_BURN_RED_OPUS"):
        monkeypatch.delenv(k, raising=False)
    # $5/hr: green for Opus (< $10 yellow), red for Haiku (≥ $2.5 yellow and also ≥ $6.25 red? no, 5 < 6.25)
    # Let's use unambiguous values: $3/hr is green for Opus, yellow for Haiku (≥ 2.5 but < 6.25)
    assert statusline._color_burn(3.0, "opus") == statusline._GREEN
    assert statusline._color_burn(3.0, "haiku") == statusline._YELLOW
    # $20/hr is yellow for Opus (≥ $10 but < $25), red for Haiku (≥ $6.25)
    assert statusline._color_burn(20.0, "opus") == statusline._YELLOW
    assert statusline._color_burn(20.0, "haiku") == statusline._RED
