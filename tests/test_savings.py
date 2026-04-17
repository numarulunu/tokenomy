"""Savings attribution tests — Phase 3a of v0.8.

Verifies attribute_caps_savings produces sensible per-setting USD figures
from the tuner's weighted sample shape, and that zero-contribution settings
are omitted from the output.
"""
from __future__ import annotations

import json

from tuner.savings import attribute_caps_savings


def _stats(out=None, mcp=None):
    return {
        "out_tokens": out or [],
        "mcp_sizes": mcp or {},
        "ctx_pcts": [],
        "pre_cap_ctx_pcts": [],
        "losses": [],
        "effective_n": 0.0,
    }


def test_no_samples_returns_empty():
    assert attribute_caps_savings({"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 5000}, _stats()) == {}


def test_no_caps_returns_empty():
    s = _stats(out=[(10_000.0, 1.0)] * 100)
    assert attribute_caps_savings({}, s) == {}


def test_cap_above_all_samples_omitted():
    # All output tokens under the cap → zero savings → key omitted
    s = _stats(out=[(3000.0, 1.0)] * 50)
    result = attribute_caps_savings({"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 5000}, s)
    assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" not in result


def test_output_cap_attribution_positive():
    # 100 samples at 10k tokens, cap at 5k → 5k excess × 100 weighted samples
    s = _stats(out=[(10_000.0, 1.0)] * 100)
    result = attribute_caps_savings({"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 5000}, s)
    assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" in result
    assert result["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] > 0


def test_output_cap_weight_scales_attribution():
    s_half = _stats(out=[(10_000.0, 0.5)] * 100)
    s_full = _stats(out=[(10_000.0, 1.0)] * 100)
    r_half = attribute_caps_savings({"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 5000}, s_half)
    r_full = attribute_caps_savings({"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 5000}, s_full)
    # Half weight → half the savings
    assert abs(r_half["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] * 2 - r_full["CLAUDE_CODE_MAX_OUTPUT_TOKENS"]) < 0.02


def test_mcp_cap_attribution_per_server():
    # serena at 8000 bytes × 50 samples, cap 1000 tokens (~4000 bytes) → 4000 bytes over
    s = _stats(mcp={"serena": [(8000.0, 1.0)] * 50, "playwright": [(2000.0, 1.0)] * 50})
    caps = {"MAX_MCP_OUTPUT_TOKENS": {"serena": 1000, "playwright": 1000}}
    result = attribute_caps_savings(caps, s)
    assert "MAX_MCP_OUTPUT_TOKENS.serena" in result
    # playwright samples (2000 bytes) under the 4000-byte cap_chars → zero, omitted
    assert "MAX_MCP_OUTPUT_TOKENS.playwright" not in result


def test_cap_zero_or_missing_is_skipped():
    # Zero or non-int cap should not crash and should be skipped
    s = _stats(out=[(10_000.0, 1.0)] * 100)
    assert attribute_caps_savings({"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 0}, s) == {}
    assert attribute_caps_savings({"CLAUDE_CODE_MAX_OUTPUT_TOKENS": None}, s) == {}


def test_combined_output_and_mcp():
    s = _stats(
        out=[(10_000.0, 1.0)] * 100,
        mcp={"serena": [(8000.0, 1.0)] * 50},
    )
    caps = {
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS": 5000,
        "MAX_MCP_OUTPUT_TOKENS": {"serena": 1000},
    }
    result = attribute_caps_savings(caps, s)
    assert set(result.keys()) == {"CLAUDE_CODE_MAX_OUTPUT_TOKENS", "MAX_MCP_OUTPUT_TOKENS.serena"}
    assert all(isinstance(v, float) for v in result.values())


# ─────────────── MCP server caps_savings handler ───────────────


def test_mcp_caps_savings_missing_applied(tmp_path, monkeypatch):
    from tokenomy_mcp import server
    monkeypatch.setattr(server, "APPLIED_PATH", tmp_path / "missing.json")
    r = server.caps_savings()
    assert r["savings"] == {}
    assert r.get("error") == "not found"


def test_mcp_caps_savings_reads_applied(tmp_path, monkeypatch):
    from tokenomy_mcp import server
    p = tmp_path / "applied.json"
    p.write_text(json.dumps({
        "last_tune_at": "2026-04-17T00:00:00+00:00",
        "caps_savings": {
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS": 12.50,
            "MAX_MCP_OUTPUT_TOKENS.serena": 3.20,
        },
    }), encoding="utf-8")
    monkeypatch.setattr(server, "APPLIED_PATH", p)
    r = server.caps_savings()
    assert r["savings"] == {
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS": 12.50,
        "MAX_MCP_OUTPUT_TOKENS.serena": 3.20,
    }
    assert r["total_usd"] == 15.70
    assert r["last_tune_at"] == "2026-04-17T00:00:00+00:00"


def test_mcp_caps_savings_malformed_file(tmp_path, monkeypatch):
    from tokenomy_mcp import server
    p = tmp_path / "applied.json"
    p.write_text("not json{", encoding="utf-8")
    monkeypatch.setattr(server, "APPLIED_PATH", p)
    r = server.caps_savings()
    assert r["savings"] == {}
    assert "error" in r


def test_mcp_caps_savings_missing_key_returns_empty(tmp_path, monkeypatch):
    from tokenomy_mcp import server
    p = tmp_path / "applied.json"
    p.write_text(json.dumps({"last_tune_at": "2026-04-17T00:00:00+00:00"}), encoding="utf-8")
    monkeypatch.setattr(server, "APPLIED_PATH", p)
    r = server.caps_savings()
    assert r["savings"] == {}
    assert r["total_usd"] == 0
