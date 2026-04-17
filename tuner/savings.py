"""Attribute counterfactual USD savings to each applied cap.

Runs after the tuner decides final caps and sums, per setting, the value
that exceeded the cap across the weighted sample corpus. Converts to USD
via DEFAULT_PRICING_KEY — this is a magnitude heuristic, not accounting.

Scope: output-token cap (CLAUDE_CODE_MAX_OUTPUT_TOKENS) and per-server MCP
caps. Context/autocompact settings are skipped because their savings
attribution would require reconstructing the counterfactual compact chain
per session, which the tuner's aggregated sample lists don't carry.
"""
from __future__ import annotations

from typing import Any, Dict

from analyzer import pricing as P

CHARS_PER_TOKEN = 4


def attribute_caps_savings(caps: Dict[str, Any], stats: Dict[str, Any]) -> Dict[str, float]:
    """Flat dict of {setting_key: usd_saved_over_corpus}.

    Keys:
      - "CLAUDE_CODE_MAX_OUTPUT_TOKENS"          — output-token cap attribution
      - "MAX_MCP_OUTPUT_TOKENS.<server>"         — per-server MCP cap attribution

    Values are rounded to cents. Settings contributing $0 are omitted so the
    consumer can cleanly render "no attributable savings" instead of a clutter
    of zeros. Caller is responsible for deciding presentation (monthly scale,
    total, etc.) — this function only reports the raw corpus attribution.
    """
    out: Dict[str, float] = {}

    cap_out = caps.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS")
    samples_out = stats.get("out_tokens") or []
    if isinstance(cap_out, int) and cap_out > 0 and samples_out:
        tokens_saved = sum(max(0.0, v - cap_out) * w for v, w in samples_out)
        usd = P.cost_for_usage(
            P.DEFAULT_PRICING_KEY,
            input_tokens=0,
            output_tokens=int(tokens_saved),
        )
        if usd > 0:
            out["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = round(usd, 2)

    per_server = caps.get("MAX_MCP_OUTPUT_TOKENS") or {}
    mcp_sizes = stats.get("mcp_sizes") or {}
    if isinstance(per_server, dict):
        for server, cap_tokens in per_server.items():
            if not isinstance(cap_tokens, int) or cap_tokens <= 0:
                continue
            samples = mcp_sizes.get(server) or []
            cap_chars = cap_tokens * CHARS_PER_TOKEN
            bytes_over = sum(max(0.0, v - cap_chars) * w for v, w in samples)
            tokens_saved = int(bytes_over // CHARS_PER_TOKEN)
            if tokens_saved <= 0:
                continue
            usd = P.cost_for_usage(
                P.DEFAULT_PRICING_KEY,
                input_tokens=tokens_saved,
                output_tokens=0,
            )
            if usd > 0:
                out[f"MAX_MCP_OUTPUT_TOKENS.{server}"] = round(usd, 2)

    return out
