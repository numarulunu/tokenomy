"""Phase 2 validation tests for the OAuth usage fetcher."""
from __future__ import annotations

import logging

from hooks.usage_fetcher import _parse, _validate_usage_entry, burn_pct_per_hour


def test_validate_usage_entry_accepts_well_formed():
    assert _validate_usage_entry({"utilization": 42, "resets_at": "2026-04-17T12:00:00Z"}) is True
    assert _validate_usage_entry({"utilization": 0.5, "resets_at": "x"}) is True


def test_validate_usage_entry_rejects_missing_fields():
    assert _validate_usage_entry({}) is False
    assert _validate_usage_entry({"utilization": 10}) is False
    assert _validate_usage_entry({"resets_at": "x"}) is False
    assert _validate_usage_entry(None) is False
    assert _validate_usage_entry("not a dict") is False


def test_validate_usage_entry_rejects_wrong_types():
    # utilization must be numeric
    assert _validate_usage_entry({"utilization": "50", "resets_at": "x"}) is False
    # resets_at must be a non-empty string
    assert _validate_usage_entry({"utilization": 50, "resets_at": ""}) is False
    assert _validate_usage_entry({"utilization": 50, "resets_at": 123}) is False


def test_parse_drops_bad_window_and_logs(caplog):
    """A malformed five_hour window is dropped; a valid seven_day survives.
    The drop is surfaced as a WARNING so schema drift doesn't hide."""
    payload = {
        "five_hour": {"utilization": "broken"},  # bad
        "seven_day": {"utilization": 20, "resets_at": "2026-04-17T12:00:00Z"},
        "tier": "max_20x",
    }
    with caplog.at_level(logging.WARNING, logger="hooks.usage_fetcher"):
        out = _parse(payload)
    assert "sess_pct_left" not in out
    assert "sess_pct_used" not in out
    assert "sess_resets_at" not in out
    assert out["week_pct_left"] == 80
    assert out["week_pct_used"] == 20
    assert out["week_resets_at"] == "2026-04-17T12:00:00Z"
    assert out["tier"] == "max_20x"
    assert any("five_hour" in r.message for r in caplog.records)


def test_parse_missing_window_is_silent(caplog):
    """A window that's entirely absent is not a schema error — no log noise."""
    payload = {"seven_day": {"utilization": 10, "resets_at": "x"}}
    with caplog.at_level(logging.WARNING, logger="hooks.usage_fetcher"):
        out = _parse(payload)
    assert "week_pct_left" in out
    assert caplog.records == []


def test_parse_both_windows_valid():
    payload = {
        "five_hour": {"utilization": 42, "resets_at": "T1"},
        "seven_day": {"utilization": 17, "resets_at": "T2"},
    }
    out = _parse(payload)
    assert out["sess_pct_left"] == 58
    assert out["sess_pct_used"] == 42
    assert out["sess_resets_at"] == "T1"
    assert out["week_pct_left"] == 83
    assert out["week_pct_used"] == 17
    assert out["week_resets_at"] == "T2"


# ─────────────── burn_pct_per_hour rollover handling ───────────────


def test_burn_pct_ignores_history_before_rollover():
    """History spanning a quota reset must slice to the post-reset segment,
    else delta goes negative and clamps to 0."""
    # Previous window climbed 87→97, rolled over to 0, then climbed 0→11
    # over 15 minutes (900s) → 11 * 3600 / 900 = 44 %/hr
    cache = {
        "history": [
            {"at": 1000, "sess_used": 87, "week_used": 12},
            {"at": 1500, "sess_used": 97, "week_used": 12},
            {"at": 2000, "sess_used": 0, "week_used": 13},   # rollover
            {"at": 2900, "sess_used": 11, "week_used": 13},
        ]
    }
    rate = burn_pct_per_hour(cache)
    assert rate is not None
    assert 40 <= rate <= 50


def test_burn_pct_no_rollover_normal_series():
    """Monotonic series uses full history, returns positive rate."""
    cache = {
        "history": [
            {"at": 0, "sess_used": 10, "week_used": 5},
            {"at": 1800, "sess_used": 20, "week_used": 5},
            {"at": 3600, "sess_used": 30, "week_used": 5},
        ]
    }
    rate = burn_pct_per_hour(cache)
    assert rate is not None
    assert 19 <= rate <= 21  # ~20 %/hr


def test_burn_pct_post_rollover_too_short_returns_none():
    """If the only post-rollover data spans less than the min window, bail."""
    cache = {
        "history": [
            {"at": 0, "sess_used": 50, "week_used": 5},
            {"at": 5000, "sess_used": 90, "week_used": 5},
            {"at": 5060, "sess_used": 0, "week_used": 6},   # reset 60s ago
            {"at": 5100, "sess_used": 2, "week_used": 6},
        ]
    }
    assert burn_pct_per_hour(cache) is None
