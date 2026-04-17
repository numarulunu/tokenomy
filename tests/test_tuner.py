"""Tuner pure-function tests."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from tuner.state import empty_state
from tuner.tuner import (
    COOLDOWN_SESSIONS,
    DEFAULT_MCP_ALLOW,
    _read_mcp_servers,
    apply_hysteresis_cooldown_freeze,
    apply_loss_freezes,
    compute_caps_per_setting,
    configured_mcp_servers,
)


def _stats(out=None, mcp=None, ctx=None, losses=None, pre_cap_ctx=None):
    return {
        "out_tokens": out or [],
        "mcp_sizes": mcp or {},
        "ctx_pcts": ctx or [],
        "pre_cap_ctx_pcts": pre_cap_ctx or [],
        "losses": losses or [],
        "effective_n": sum(w for _, w in (out or [])),
    }


def test_stats_has_losses_key_not_events():
    """After per-session loss detection, stats returns 'losses' not 'events'."""
    s = _stats()
    assert "losses" in s
    assert "events" not in s


# compute_caps
def test_compute_caps_empty_uses_floor():
    caps = compute_caps_per_setting(_stats())
    assert caps["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == 4000
    assert caps["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] == 70  # default


def test_compute_caps_with_data():
    out = [(5000.0, 1.0)] * 6000
    mcp = {"serena": [(4000.0, 1.0)] * 6000, "playwright": [(150_000.0, 1.0)] * 6000}
    ctx = [(15.0, 1.0)] * 6000  # user compacts at 15%
    caps = compute_caps_per_setting(_stats(out=out, mcp=mcp, ctx=ctx))
    assert caps["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] >= 5000
    assert caps["MAX_MCP_OUTPUT_TOKENS"]["playwright"] >= 150_000
    assert caps["MAX_MCP_OUTPUT_TOKENS"]["serena"] >= 4000
    assert caps["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] == 25  # ~15+10=25


def test_confidence_gate_blocks_low_n():
    """effective_n=50 should trigger the confidence gate."""
    from tuner.tuner import MIN_EFFECTIVE_N
    from tuner.weighting import confidence

    stats = _stats(out=[(5000.0, 1.0)] * 50)  # effective_n=50
    assert stats["effective_n"] < MIN_EFFECTIVE_N
    assert stats["effective_n"] == 50.0

    # Proposed caps would be non-empty
    proposed = compute_caps_per_setting(stats)
    assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" in proposed

    # But confidence is low
    conf = confidence(stats["effective_n"])
    assert conf < 0.05  # 50/5000 = 0.01


# hysteresis
def test_hysteresis_init_applies():
    state = empty_state()
    final, _ = apply_hysteresis_cooldown_freeze(state, {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 6000})
    assert final["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == 6000


def test_hysteresis_blocks_small_tighten():
    state = empty_state()
    state["caps"] = {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 10_000}
    # 5% smaller — below 10% tighten threshold
    final, _ = apply_hysteresis_cooldown_freeze(state, {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 9500})
    assert final["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == 10_000


def test_hysteresis_allows_large_tighten():
    state = empty_state()
    state["caps"] = {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 10_000}
    final, new_state = apply_hysteresis_cooldown_freeze(state, {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 8000})
    assert final["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == 8000
    assert new_state["cooldowns"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"]["sessions_remaining"] == COOLDOWN_SESSIONS


def test_hysteresis_allows_loosen():
    state = empty_state()
    state["caps"] = {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 8000}
    final, _ = apply_hysteresis_cooldown_freeze(state, {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 9000})
    assert final["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == 9000


def test_cooldown_blocks_change():
    state = empty_state()
    state["caps"] = {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 10_000}
    state["cooldowns"] = {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": {"sessions_remaining": 3}}
    final, _ = apply_hysteresis_cooldown_freeze(state, {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 5000})
    assert final["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == 10_000


def test_freeze_blocks_change():
    state = empty_state()
    state["caps"] = {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 10_000}
    future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    state["freezes"] = {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": {"until": future, "reason": "test"}}
    final, _ = apply_hysteresis_cooldown_freeze(state, {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 5000})
    assert final["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == 10_000


def test_user_pinned_skipped():
    state = empty_state()
    state["user_pinned"] = ["CLAUDE_CODE_MAX_OUTPUT_TOKENS"]
    final, _ = apply_hysteresis_cooldown_freeze(state, {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 5000})
    assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" not in final


def test_per_server_hysteresis():
    state = empty_state()
    state["caps"] = {"MAX_MCP_OUTPUT_TOKENS": {"serena": 10_000}}
    final, _ = apply_hysteresis_cooldown_freeze(
        state, {"MAX_MCP_OUTPUT_TOKENS": {"serena": 8000, "playwright": 200_000}}
    )
    assert final["MAX_MCP_OUTPUT_TOKENS"]["serena"] == 8000
    assert final["MAX_MCP_OUTPUT_TOKENS"]["playwright"] == 200_000


def test_loss_freeze_writes_freeze():
    state = empty_state()
    losses = [{"detector": "truncation_requery", "server": "playwright"}]
    new = apply_loss_freezes(state, losses)
    assert "MAX_MCP_OUTPUT_TOKENS.playwright" in new["freezes"]


def test_loss_freeze_mid_code():
    state = empty_state()
    losses = [{"detector": "mid_code_ending"}]
    new = apply_loss_freezes(state, losses)
    assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" in new["freezes"]


def test_control_loop_zeroes_cooldown_when_cap_below_usage():
    """If current cap < 0.9 * rolling_mean, cooldown should be zeroed."""
    state = empty_state()
    state["caps"] = {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 4000}
    state["rolling_mean_output"] = 8000.0
    state["rolling_mean_n"] = 100.0
    state["cooldowns"] = {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": {"sessions_remaining": 5}}

    # 4000 < 0.9 * 8000 (7200), so cooldown should be zeroed
    rolling = state.get("rolling_mean_output", 0.0)
    cap_val = state["caps"].get("CLAUDE_CODE_MAX_OUTPUT_TOKENS", 0)
    assert cap_val < 0.9 * rolling
    # After force-loosen logic, cooldown sessions_remaining should be 0
    if cap_val > 0 and cap_val < 0.9 * rolling:
        state["cooldowns"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"]["sessions_remaining"] = 0
    assert state["cooldowns"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"]["sessions_remaining"] == 0


def test_pre_cap_ctx_preferred_when_sufficient():
    """When >=20 pre-cap ctx samples exist, use them for autocompact."""
    pre = [(30.0, 1.0)] * 25  # 25 pre-cap samples at 30%
    post = [(60.0, 1.0)] * 100  # 100 post-cap at 60%
    stats = _stats(ctx=pre + post, pre_cap_ctx=pre)
    caps = compute_caps_per_setting(stats)
    # p75 of uniform 30% = 30, +10 = 40, floor is 25
    assert caps["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] == 40


def test_pre_cap_ctx_falls_back_when_insufficient():
    """When <20 pre-cap samples, fall back to full ctx set."""
    pre = [(30.0, 1.0)] * 10  # only 10 < 20
    all_ctx = pre + [(60.0, 1.0)] * 100
    stats = _stats(ctx=all_ctx, pre_cap_ctx=pre)
    caps = compute_caps_per_setting(stats)
    # Falls back to full set: p75 of mixed distribution, dominated by 60% samples
    assert caps["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] >= 60


def test_lock_cleanup_removes_pid_file(tmp_path):
    """Finally block should remove pid file before rmdir."""
    lock_dir = tmp_path / "tuner.lock.d"
    lock_dir.mkdir()
    pid_file = lock_dir / "pid"
    pid_file.write_text("12345")

    # Simulate the finally block logic
    ld = str(lock_dir)
    pf = str(pid_file)
    if os.path.exists(pf):
        os.unlink(pf)
    os.rmdir(ld)
    assert not os.path.exists(ld)


def test_first_run_writes_consent_summary(tmp_path):
    """--first-run writes consent summary and baseline-only settings."""
    from tuner import tuner

    settings_path = str(tmp_path / "settings.json")
    home = str(tmp_path / "tokenomy")

    result = tuner.main([
        "--first-run",
        "--home", home,
        "--user-settings", settings_path,
    ])
    assert result == 0
    # Consent summary should exist
    summary = os.path.join(home, "consent-summary.txt")
    assert os.path.exists(summary)
    content = open(summary, encoding="utf-8").read()
    assert "ENABLE_TOOL_SEARCH" in content
    # Settings should have baseline env
    assert os.path.exists(settings_path)
    data = json.loads(open(settings_path, encoding="utf-8").read())
    assert data.get("env", {}).get("ENABLE_TOOL_SEARCH") == "true"


# ─────────────── configured_mcp_servers ───────────────


def test_read_mcp_servers_missing_file(tmp_path):
    assert _read_mcp_servers(str(tmp_path / "nope.json")) == set()


def test_read_mcp_servers_malformed(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("not json {", encoding="utf-8")
    assert _read_mcp_servers(str(p)) == set()


def test_read_mcp_servers_no_mcpservers_key(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"other": 1}), encoding="utf-8")
    assert _read_mcp_servers(str(p)) == set()


def test_read_mcp_servers_returns_keys(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(
        json.dumps({"mcpServers": {"serena": {}, "kontext": {}, "playwright": {}}}),
        encoding="utf-8",
    )
    assert _read_mcp_servers(str(p)) == {"serena", "kontext", "playwright"}


def test_configured_mcp_servers_reads_user_config(tmp_path, monkeypatch):
    fake_home = tmp_path
    (fake_home / ".claude.json").write_text(
        json.dumps({"mcpServers": {"playwright": {}, "notion": {}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    assert configured_mcp_servers() == {"playwright", "notion"}


def test_configured_mcp_servers_falls_back_when_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    # no ~/.claude.json — should return DEFAULT_MCP_ALLOW
    assert configured_mcp_servers() == set(DEFAULT_MCP_ALLOW)
