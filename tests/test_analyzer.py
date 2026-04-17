"""Unit tests for the tokenomy analyzer.

Run with:  cd tokenomy && python -m pytest tests/test_analyzer.py -q
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Make the `analyzer` package importable when running from the repo root.
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from analyzer import analyze, counterfactual, extractors, pricing, report  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _line(obj: dict) -> str:
    return json.dumps(obj) + "\n"


def _assistant(model: str, in_tok: int, out_tok: int, cache_r: int = 0,
               content: list | None = None, ts: str = "2026-04-07T10:00:00Z",
               sid: str = "s1") -> dict:
    return {
        "type": "assistant",
        "timestamp": ts,
        "sessionId": sid,
        "message": {
            "model": model,
            "content": content or [{"type": "text", "text": "hello"}],
            "usage": {
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": cache_r,
            },
        },
    }


def _tool_use(tool: str, tid: str, tool_input: dict, sid: str = "s1",
              ts: str = "2026-04-07T10:00:01Z") -> dict:
    return {
        "type": "assistant",
        "timestamp": ts,
        "sessionId": sid,
        "message": {
            "model": "claude-sonnet-4-6",
            "usage": {"input_tokens": 1, "output_tokens": 1,
                      "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
            "content": [{"type": "tool_use", "id": tid, "name": tool, "input": tool_input}],
        },
    }


def _tool_result(tid: str, text: str, sid: str = "s1",
                 ts: str = "2026-04-07T10:00:02Z") -> dict:
    return {
        "type": "user",
        "timestamp": ts,
        "sessionId": sid,
        "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": text}]},
    }


@pytest.fixture
def synthetic_session(tmp_path) -> Path:
    project_dir = tmp_path / "proj-abc"
    project_dir.mkdir()
    f = project_dir / "session.jsonl"

    lines = [
        _line(_assistant("claude-sonnet-4-6", 100_000, 50_000)),
        _line(_tool_use("Read", "t1", {"file_path": "/tmp/foo.py", "offset": 0, "limit": 100})),
        _line(_tool_result("t1", "x" * 500)),
        # Duplicate Read (same key) → savings eligible
        _line(_tool_use("Read", "t2", {"file_path": "/tmp/foo.py", "offset": 0, "limit": 100})),
        _line(_tool_result("t2", "x" * 500)),
        # Large log read
        _line(_tool_use("Read", "t3", {"file_path": "/tmp/server.log", "offset": None, "limit": None})),
        _line(_tool_result("t3", "x" * 30_000)),
        # MCP big result
        _line(_tool_use("mcp__serena__find_symbol", "t4", {"name": "foo"})),
        _line(_tool_result("t4", "x" * 60_000)),
        _line(_assistant("claude-sonnet-4-6", 200_000, 80_000)),
        {"type": "system", "content": "autocompact triggered"},
    ]
    # The last dict isn't JSON-lined above — convert
    raw = "".join(l if isinstance(l, str) else _line(l) for l in lines)
    # Add a malformed line
    raw += "{ not json at all\n"
    f.write_text(raw, encoding="utf-8")
    return f


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------
def test_pricing_known_model():
    cost = pricing.cost_for_usage("claude-sonnet-4-6", input_tokens=1_000_000, output_tokens=0)
    assert cost == pytest.approx(3.00)


def test_pricing_unknown_model_falls_back(caplog):
    cost = pricing.cost_for_usage("totally-made-up-model", input_tokens=1_000_000, output_tokens=0)
    # Falls back to default (sonnet-4-6)
    assert cost == pytest.approx(3.00)


def test_pricing_synthetic_model_skipped():
    assert pricing.cost_for_usage("<synthetic>", 1000, 1000) == 0.0


# ---------------------------------------------------------------------------
# Extractors
# ---------------------------------------------------------------------------
def test_extractor_handles_malformed_lines(synthetic_session):
    evs = list(extractors.iter_session_file(str(synthetic_session)))
    assert any(e.kind == "assistant_usage" for e in evs)
    assert any(e.kind == "tool_use" for e in evs)
    assert any(e.kind == "tool_result" for e in evs)
    assert any(e.kind == "compact" for e in evs)


def test_extractor_tool_result_size(synthetic_session):
    evs = [e for e in extractors.iter_session_file(str(synthetic_session)) if e.kind == "tool_result"]
    sizes = sorted(e.response_size_bytes for e in evs)
    assert sizes == [500, 500, 30_000, 60_000]


def test_extractor_flattens_list_content():
    assert extractors._flatten_content([{"type": "text", "text": "ab"}, {"type": "text", "text": "cd"}]) == "abcd"
    assert extractors._flatten_content("plain") == "plain"
    assert extractors._flatten_content(None) == ""


# ---------------------------------------------------------------------------
# Counterfactuals
# ---------------------------------------------------------------------------
def test_counterfactual_mcp_cap_saves_tokens():
    tool_results = [
        {"tool_name": "mcp__foo__bar", "size_bytes": 40_000, "tool_use_id": "t1"},
        {"tool_name": "Read", "size_bytes": 40_000, "tool_use_id": "t2"},  # not mcp — ignored
    ]
    reactions = {"t1": {"model": "claude-sonnet-4-6", "requeried_same_tool": False}}
    r = counterfactual.mcp_output_cap(tool_results, reactions, cap_tokens=5000)
    # 40000 chars - 20000 cap_chars = 20000 chars over → 5000 tokens saved
    assert r["tokens_saved"] == 5000
    assert r["losses"] == 0
    assert r["dollars_saved"] > 0


def test_counterfactual_max_output_detects_partial():
    usages = [
        {"model": "claude-sonnet-4-6", "output_tokens": 10_000,
         "text_tail": "```python\ndef foo():"},  # unclosed code block
        {"model": "claude-sonnet-4-6", "output_tokens": 3000, "text_tail": "done."},
    ]
    r = counterfactual.max_output_cap(usages, cap_tokens=6000)
    assert r["tokens_saved"] == 4000  # 10000 - 6000
    assert r["losses"] == 1  # the partial one


def test_read_once_savings_positive():
    r = counterfactual.read_once_savings(duplicate_read_bytes=8_000, duplicate_read_count=4)
    assert r["tokens_saved"] == 2000
    assert r["dollars_saved"] > 0
    assert r["losses"] == 0


def test_log_grep_savings_positive():
    r = counterfactual.log_grep_savings(log_read_bytes_over_threshold=40_000)
    assert r["tokens_saved"] == 10_000


# ---------------------------------------------------------------------------
# End-to-end
# ---------------------------------------------------------------------------
def test_full_pipeline_on_synthetic(tmp_path, synthetic_session):
    # The synthetic_session fixture put the file in tmp_path/proj-abc/.
    root = synthetic_session.parent.parent
    out = tmp_path / "insights.json"
    rc = analyze.main(
        [
            "--days", "0",  # no date filter
            "--root", str(root),
            "--json-out", str(out),
            "--no-report",
        ]
    )
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["period"]["sessions"] >= 1
    assert data["totals"]["cost_usd"] > 0
    # tool_result sizes were accumulated
    assert "by_tool" in data
    assert data["files_scanned"] == 1
    # Compact event detected
    assert data["compact_events"] == 1
    # Outliers include the biggest tool_result
    top_sizes = [o["size"] for o in data["outliers"]]
    assert 60_000 in top_sizes


def test_empty_corpus(tmp_path):
    out = tmp_path / "insights.json"
    rc = analyze.main(["--days", "0", "--root", str(tmp_path), "--json-out", str(out), "--no-report"])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["period"]["sessions"] == 0
    assert data["totals"]["cost_usd"] == 0


def test_report_renders(tmp_path):
    insights = {
        "period": {"start": "2026-03-08", "end": "2026-04-07", "days": 30, "sessions": 5},
        "totals": {"input_tokens": 1_000_000, "output_tokens": 500_000,
                   "cache_read_tokens": 2_000_000, "cache_creation_tokens": 0,
                   "cost_usd": 12.34},
        "by_tool": {"Read": {"total_bytes": 100_000, "est_cost_usd": 0.10}},
        "counterfactuals": [{"setting": "MAX_MCP_OUTPUT_TOKENS", "value": 8000,
                             "tokens_saved": 1_000_000, "dollars_saved": 3.0, "losses": 1}],
        "recommendations": [{"setting": "MAX_MCP_OUTPUT_TOKENS", "value": 8000,
                             "reason": "saves money", "confidence": "high"}],
        "outliers": [{"tool": "Read", "size": 50_000, "project": "x", "ts": "2026-04-07"}],
        "output_path": str(tmp_path / "ins.json"),
    }
    text = report.render(insights)
    assert "tokenomy analyzer" in text
    assert "$12.34" in text
    assert "MAX_MCP_OUTPUT_TOKENS" in text


# ─────────────── project-path decoder (Phase 3b) ───────────────


def test_decode_project_path_exact_match(tmp_path):
    # Build a real dir tree, then run _probe_path with segments that match
    # exactly (no spaces → no ambiguity).
    proj = tmp_path / "MyProject"
    proj.mkdir()
    from analyzer.extractors import _probe_path
    # Walk tmp_path's parents to get pre-encoded segments (split every dir with
    # spaces into multiple segments, since that's what Claude Code's encoder does).
    # Claude Code encodes `-`, spaces, and path separators identically, so any
    # dashes/spaces inside a real dir name become individual segments.
    def _encode_segments(parts):
        out = []
        for p in parts:
            out.extend(p.replace("-", " ").split())
        return out
    if sys.platform == "win32":
        root = tmp_path.drive + "\\"
        tail_parts = _encode_segments(list(tmp_path.parts[1:]) + ["MyProject"])
        assert _probe_path(root, tail_parts) == str(proj)
    else:
        tail_parts = _encode_segments([s for s in str(proj).split("/") if s])
        assert _probe_path("/", tail_parts) == str(proj)


def test_decode_project_path_missing_returns_none():
    assert extractors.decode_project_path("C--Users-doesnotexist-nowhere") is None
    assert extractors.decode_project_path("") is None
    assert extractors.decode_project_path("no-drive-marker") is None


def test_decode_project_path_space_in_dirname(tmp_path):
    # "Gaming PC" style: encoded segment `Gaming-PC` must resolve via filesystem probe.
    parent = tmp_path / "Foo Bar"
    parent.mkdir()
    (parent / "child").mkdir()
    from analyzer.extractors import _probe_path
    # target = "Foo-Bar-child" must resolve to parent/child
    result = _probe_path(str(tmp_path), ["Foo", "Bar", "child"])
    assert result == str(parent / "child")


def test_decode_project_path_prefers_longest_match(tmp_path):
    # Both "Foo" and "Foo-Bar" exist at the same level; when encoded target is
    # "Foo-Bar-leaf", the decoder must consume via "Foo-Bar", not "Foo".
    (tmp_path / "Foo").mkdir()
    (tmp_path / "Foo-Bar").mkdir()
    (tmp_path / "Foo-Bar" / "leaf").mkdir()
    from analyzer.extractors import _probe_path
    result = _probe_path(str(tmp_path), ["Foo", "Bar", "leaf"])
    assert result == str(tmp_path / "Foo-Bar" / "leaf")
