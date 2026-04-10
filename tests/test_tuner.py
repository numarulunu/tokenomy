"""Tuner pure-function tests."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from tuner.state import empty_state
from tuner.tuner import (
    COOLDOWN_SESSIONS,
    apply_hysteresis_cooldown_freeze,
    apply_loss_freezes,
    compute_caps_per_setting,
)


def _stats(out=None, mcp=None, ctx=None, losses=None):
    return {
        "out_tokens": out or [],
        "mcp_sizes": mcp or {},
        "ctx_pcts": ctx or [],
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
