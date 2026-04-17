"""Counterfactual savings: "what would setting X have saved on the historical record?"

Each function consumes the aggregated event streams produced by analyze.py
and returns a uniform result dict:

    {"setting": ..., "value": ..., "tokens_saved": int, "dollars_saved": float, "losses": int, "notes": str}

Heuristics are documented inline. Losses are floor estimates — we only catch
obvious signals (re-query after truncation, unclosed code blocks, etc.).
"""
from __future__ import annotations

from typing import Iterable, List, Dict, Any

from . import pricing as P

CHARS_PER_TOKEN = 4  # rough fallback


def _chars_to_tokens(chars: int) -> int:
    return max(0, chars // CHARS_PER_TOKEN)


def mcp_output_cap(
    tool_results: List[Dict[str, Any]],
    reactions: Dict[str, Dict[str, Any]],
    cap_tokens: int,
) -> Dict[str, Any]:
    """Counterfactual savings from `MAX_MCP_OUTPUT_TOKENS = cap_tokens`.

    `tool_results` entries: {tool_name, size_bytes, session_id, tool_use_id, reaction_model}
    `reactions[tool_use_id]`: {requeried_same_tool: bool, model: str}
    """
    cap_chars = cap_tokens * CHARS_PER_TOKEN
    tokens_saved = 0
    dollars_saved = 0.0
    losses = 0
    for tr in tool_results:
        if not (tr.get("tool_name") or "").startswith("mcp__"):
            continue
        size = tr.get("size_bytes", 0)
        if size <= cap_chars:
            continue
        over_tokens = _chars_to_tokens(size - cap_chars)
        tokens_saved += over_tokens
        # Attribute cost to the reacting assistant message's model (input side).
        r = reactions.get(tr.get("tool_use_id") or "", {})
        model = r.get("model") or P.DEFAULT_PRICING_KEY
        dollars_saved += P.cost_for_usage(model, input_tokens=over_tokens, output_tokens=0)
        if r.get("requeried_same_tool"):
            losses += 1
    return {
        "setting": "MAX_MCP_OUTPUT_TOKENS",
        "value": cap_tokens,
        "tokens_saved": tokens_saved,
        "dollars_saved": round(dollars_saved, 2),
        "losses": losses,
        "notes": "losses = responses where Claude re-queried the same tool after truncation",
    }


_PARTIAL_ENDINGS = ("let me continue", "continuing", "...to be continued")


def _looks_partial(text_tail: str | None) -> bool:
    if not text_tail:
        return False
    t = text_tail.strip().lower()
    if not t:
        return False
    if any(e in t for e in _PARTIAL_ENDINGS):
        return True
    # unclosed triple-backtick: odd count in tail
    if t.count("```") % 2 == 1:
        return True
    # trailing unmatched opening brace (very rough)
    opens = t.count("{") + t.count("[") + t.count("(")
    closes = t.count("}") + t.count("]") + t.count(")")
    if opens - closes >= 2:
        return True
    return False


def max_output_cap(
    assistant_usages: Iterable[Dict[str, Any]],
    cap_tokens: int,
) -> Dict[str, Any]:
    """Counterfactual savings from `CLAUDE_CODE_MAX_OUTPUT_TOKENS = cap_tokens`."""
    tokens_saved = 0
    dollars_saved = 0.0
    losses = 0
    for u in assistant_usages:
        out = u.get("output_tokens", 0)
        if out <= cap_tokens:
            continue
        delta = out - cap_tokens
        tokens_saved += delta
        dollars_saved += P.cost_for_usage(
            u.get("model") or P.DEFAULT_PRICING_KEY,
            input_tokens=0,
            output_tokens=delta,
        )
        # Loss check: if the *original* message already looked partial, truncating it
        # further would definitely have broken the flow.
        if _looks_partial(u.get("text_tail")):
            losses += 1
    return {
        "setting": "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
        "value": cap_tokens,
        "tokens_saved": tokens_saved,
        "dollars_saved": round(dollars_saved, 2),
        "losses": losses,
        "notes": "losses = messages that already looked partial (unclosed code blocks, 'continuing...')",
    }


def read_once_savings(
    duplicate_read_bytes: int,
    duplicate_read_count: int,
    model: str | None = None,
) -> Dict[str, Any]:
    """Already-observed savings from the read-once hook (or what it WOULD save).

    `model` controls the pricing key; defaults to the analyzer's mid-tier
    baseline when callers don't know which model produced the reads.
    """
    tokens_saved = _chars_to_tokens(duplicate_read_bytes)
    dollars_saved = P.cost_for_usage(
        model or P.DEFAULT_PRICING_KEY, input_tokens=tokens_saved, output_tokens=0
    )
    return {
        "setting": "read-once hook",
        "value": True,
        "tokens_saved": tokens_saved,
        "dollars_saved": round(dollars_saved, 2),
        "losses": 0,
        "notes": f"{duplicate_read_count} duplicate Read calls in same session",
    }


def log_grep_savings(
    log_read_bytes_over_threshold: int,
    threshold_chars: int = 5000,
    model: str | None = None,
) -> Dict[str, Any]:
    """Savings if all log reads were filtered to ~threshold chars."""
    tokens_saved = _chars_to_tokens(log_read_bytes_over_threshold)
    dollars_saved = P.cost_for_usage(
        model or P.DEFAULT_PRICING_KEY, input_tokens=tokens_saved, output_tokens=0
    )
    return {
        "setting": "log-grep hook",
        "value": True,
        "tokens_saved": tokens_saved,
        "dollars_saved": round(dollars_saved, 2),
        "losses": 0,
        "notes": f"bytes in log reads >{threshold_chars} chars",
    }


def autocompact_advisory(current_compacts: int, suggested_pct: int) -> Dict[str, Any]:
    return {
        "setting": "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE",
        "value": suggested_pct,
        "tokens_saved": 0,
        "dollars_saved": 0.0,
        "losses": 0,
        "notes": f"advisory only — {current_compacts} compact events observed in period",
    }
