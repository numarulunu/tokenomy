"""Loss detector tests with paired good/bad fixtures."""
from __future__ import annotations

from analyzer.extractors import Event
from tuner.losses import (
    detect_compact_after_big_result,
    detect_error_after_cap,
    detect_mid_code_endings,
    detect_truncation_requery,
    detect_user_pinned,
)


def _tool_use(name, tid="t1"):
    return Event(kind="tool_use", tool_name=name, tool_use_id=tid)


def _tool_result(size=100, truncated=False, tid="t1"):
    return Event(kind="tool_result", response_size_bytes=size, truncated=truncated, tool_use_id=tid)


def _assistant(tail=""):
    return Event(kind="assistant_usage", text_tail=tail)


# 1. truncation_requery
def test_truncation_requery_fires():
    evs = [
        _tool_use("Read", "t1"),
        _tool_result(size=1000, truncated=True, tid="t1"),
        _assistant("ok"),
        _tool_use("Read", "t2"),  # requery within 2
    ]
    assert len(detect_truncation_requery(evs)) == 1


def test_truncation_requery_silent_on_clean():
    evs = [
        _tool_use("Read", "t1"),
        _tool_result(size=1000, truncated=False, tid="t1"),
        _tool_use("Bash", "t2"),
    ]
    assert detect_truncation_requery(evs) == []


# 2. mid_code_endings
def test_mid_code_continue_phrase():
    evs = [_assistant("done with part 1, let me continue")]
    assert len(detect_mid_code_endings(evs)) == 1


def test_mid_code_clean():
    evs = [_assistant("all done. final answer above.")]
    assert detect_mid_code_endings(evs) == []


# 3. compact_after_big_result
def test_compact_after_big_result_fires():
    evs = [
        _tool_use("Bash"),
        _tool_result(size=50_000),
        _assistant(),
        Event(kind="compact"),
    ]
    assert len(detect_compact_after_big_result(evs)) == 1


def test_compact_after_small_result_silent():
    evs = [
        _tool_use("Bash"),
        _tool_result(size=200),
        _assistant(),
        Event(kind="compact"),
    ]
    assert detect_compact_after_big_result(evs) == []


# 4. error_after_cap
def test_error_after_cap_fires():
    evs = [
        _tool_use("mcp__serena__find_symbol", "t1"),
        _tool_result(size=100, truncated=True, tid="t1"),
    ]
    out = detect_error_after_cap(evs, capped_tools={"mcp__serena__find_symbol"})
    assert len(out) == 1
    assert out[0]["server"] == "serena"


def test_error_after_cap_silent_when_not_capped():
    evs = [
        _tool_use("mcp__serena__find_symbol", "t1"),
        _tool_result(size=100, truncated=True, tid="t1"),
    ]
    assert detect_error_after_cap(evs, capped_tools=set()) == []


# 5. user_pinned
def test_user_pinned_detected():
    env = {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": "16000", "FOO": "bar"}
    assert detect_user_pinned(env) == ["CLAUDE_CODE_MAX_OUTPUT_TOKENS"]


def test_user_pinned_empty():
    assert detect_user_pinned({}) == []
    assert detect_user_pinned(None) == []
