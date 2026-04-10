"""Loss detectors v2. Operate on streams of analyzer Events.

Each detector is a pure function: takes a list of events from one session,
returns a list of loss dicts.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

from analyzer.extractors import Event

CONTINUE_PHRASES = ("let me continue", "...continuing", "to be continued", "…continuing")
CODE_FENCE = "```"


def _server_of(tool_name: str | None) -> str | None:
    if not tool_name or not tool_name.startswith("mcp__"):
        return None
    parts = tool_name.split("__")
    return parts[1] if len(parts) >= 3 else None


def detect_truncation_requery(events: List[Event]) -> List[Dict[str, Any]]:
    """A truly-truncated tool_result (marker match) followed within 2 turns by another call to the same tool.

    `is_error` is deliberately NOT treated as truncation — ~99.5% of is_error
    results in real corpora are benign (ENOENT, schema errors, cancelled calls)
    and would flood the loss log with false positives.
    """
    out: List[Dict[str, Any]] = []
    # Build a tool_use_id → tool_name map
    name_by_id: Dict[str, str] = {}
    for e in events:
        if e.kind == "tool_use" and e.tool_use_id:
            name_by_id[e.tool_use_id] = e.tool_name or ""
    for i, e in enumerate(events):
        if e.kind != "tool_result" or not e.truncated:
            continue
        bad_tool = name_by_id.get(e.tool_use_id or "", "")
        if not bad_tool:
            continue
        # next 2 tool_use events
        seen = 0
        for j in range(i + 1, len(events)):
            if events[j].kind != "tool_use":
                continue
            seen += 1
            if events[j].tool_name == bad_tool:
                out.append({
                    "ts": e.ts,
                    "detector": "truncation_requery",
                    "tool": bad_tool,
                    "server": _server_of(bad_tool),
                })
                break
            if seen >= 2:
                break
    return out


_UNCLOSED_BRACE_RE = re.compile(r"[{}]")


def _is_mid_code(text: str) -> bool:
    # text_tail is only the last ~80 chars of an assistant message — fence/brace
    # counting across that window is unreliable, so we only trust explicit
    # "to be continued" style phrases.
    if not text:
        return False
    low = text.lower()
    return any(p in low for p in CONTINUE_PHRASES)


def detect_mid_code_endings(events: List[Event]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for e in events:
        if e.kind == "assistant_usage" and _is_mid_code(e.text_tail or ""):
            out.append({
                "ts": e.ts,
                "detector": "mid_code_ending",
                "setting": "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
            })
    return out


def detect_compact_after_big_result(events: List[Event], big_threshold: int = 30_000) -> List[Dict[str, Any]]:
    """Big tool_result (would have been capped) followed by compact within 3 assistant turns."""
    out: List[Dict[str, Any]] = []
    for i, e in enumerate(events):
        if e.kind != "tool_result" or e.response_size_bytes < big_threshold:
            continue
        turns = 0
        for j in range(i + 1, len(events)):
            if events[j].kind == "assistant_usage":
                turns += 1
            if events[j].kind == "compact":
                out.append({
                    "ts": e.ts,
                    "detector": "compact_after_big_result",
                    "size": e.response_size_bytes,
                    "setting": "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE",
                })
                break
            if turns >= 3:
                break
    return out


def detect_error_after_cap(events: List[Event], capped_tools: Iterable[str] = ()) -> List[Dict[str, Any]]:
    """Error tool_result for any currently-capped tool.

    Gated on `is_error` (the raw tool_result flag), not `truncated` — the
    signal we want here is "our cap broke this tool", and tools report that
    via is_error, not via a truncation marker.
    """
    capped = set(capped_tools)
    if not capped:
        return []
    out: List[Dict[str, Any]] = []
    name_by_id: Dict[str, str] = {}
    for e in events:
        if e.kind == "tool_use" and e.tool_use_id:
            name_by_id[e.tool_use_id] = e.tool_name or ""
    for e in events:
        if e.kind != "tool_result" or not e.is_error:
            continue
        tname = name_by_id.get(e.tool_use_id or "", "")
        if tname in capped or _server_of(tname) in capped:
            out.append({
                "ts": e.ts,
                "detector": "error_after_cap",
                "tool": tname,
                "server": _server_of(tname),
            })
    return out


def detect_user_pinned(personal_settings_env: Dict[str, Any] | None) -> List[str]:
    """Return list of tunable env vars the user has pinned in personal settings."""
    if not personal_settings_env:
        return []
    tunable = {
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
        "MAX_THINKING_TOKENS",
        "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE",
        "MAX_MCP_OUTPUT_TOKENS",
    }
    return sorted(k for k in personal_settings_env.keys() if k in tunable)


def detect_all(events: List[Event], capped_tools: Iterable[str] = ()) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    out.extend(detect_truncation_requery(events))
    out.extend(detect_mid_code_endings(events))
    out.extend(detect_compact_after_big_result(events))
    out.extend(detect_error_after_cap(events, capped_tools))
    return out
