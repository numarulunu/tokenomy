"""Anthropic model pricing table — USD per 1M tokens.

Prices are hardcoded and can rot. `PRICING_UPDATED_AT` is the freshness marker;
the analyzer warns if it's more than 90 days stale. Users can override the
entire table via `--pricing-file path.json`.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Dict

log = logging.getLogger(__name__)

PRICING_UPDATED_AT = "2026-04"  # YYYY-MM — bump when refreshing table

# USD per 1,000,000 tokens. Kept in sync with hooks/pricing.json — when a
# model is added there, mirror it here. Field name `cache_write_5m` matches
# the statusline table so both subsystems can eventually share one source.
# `[1m]` variants carry the elevated >200k-context rates (tier_1m in the
# hooks table) so long-context sessions price correctly in the analyzer.
PRICING: Dict[str, Dict[str, float]] = {
    "claude-opus-4-7":           {"input": 15.00, "output": 75.00, "cache_write_5m": 18.75, "cache_write_1h": 30.00, "cache_read":  1.50},
    "claude-opus-4-6":           {"input": 15.00, "output": 75.00, "cache_write_5m": 18.75, "cache_write_1h": 30.00, "cache_read":  1.50},
    "claude-opus-4-5":           {"input": 15.00, "output": 75.00, "cache_write_5m": 18.75, "cache_write_1h": 30.00, "cache_read":  1.50},
    "claude-opus-4-1":           {"input": 15.00, "output": 75.00, "cache_write_5m": 18.75, "cache_write_1h": 30.00, "cache_read":  1.50},
    "claude-opus-4":             {"input": 15.00, "output": 75.00, "cache_write_5m": 18.75, "cache_write_1h": 30.00, "cache_read":  1.50},
    "claude-sonnet-4-6":         {"input":  3.00, "output": 15.00, "cache_write_5m":  3.75, "cache_write_1h":  6.00, "cache_read":  0.30},
    "claude-sonnet-4-6[1m]":     {"input":  6.00, "output": 22.50, "cache_write_5m":  7.50, "cache_write_1h": 12.00, "cache_read":  0.60},
    "claude-sonnet-4-5":         {"input":  3.00, "output": 15.00, "cache_write_5m":  3.75, "cache_write_1h":  6.00, "cache_read":  0.30},
    "claude-sonnet-4":           {"input":  3.00, "output": 15.00, "cache_write_5m":  3.75, "cache_write_1h":  6.00, "cache_read":  0.30},
    "claude-haiku-4-5":          {"input":  1.00, "output":  5.00, "cache_write_5m":  1.25, "cache_write_1h":  2.00, "cache_read":  0.10},
    "claude-haiku-4-5-20251001": {"input":  1.00, "output":  5.00, "cache_write_5m":  1.25, "cache_write_1h":  2.00, "cache_read":  0.10},
    "claude-3-5-sonnet":         {"input":  3.00, "output": 15.00, "cache_write_5m":  3.75, "cache_write_1h":  6.00, "cache_read":  0.30},
    "claude-3-5-haiku":          {"input":  0.80, "output":  4.00, "cache_write_5m":  1.00, "cache_write_1h":  1.60, "cache_read":  0.08},
}

DEFAULT_PRICING_KEY = "claude-sonnet-4-6"

# Skip these "models" entirely for cost calculation.
SYNTHETIC_MODELS = {"<synthetic>", "synthetic"}


def load_pricing_file(path: str) -> Dict[str, Dict[str, float]]:
    """Load and merge a user pricing override file into PRICING."""
    with open(path, "r", encoding="utf-8") as f:
        overrides = json.load(f)
    merged = {**PRICING, **overrides}
    return merged


def get_model_pricing(model: str, table: Dict[str, Dict[str, float]] | None = None) -> Dict[str, float] | None:
    """Resolve a model name to its pricing dict. Returns None for synthetic models.

    Logs a one-time warning (per unknown model) and falls back to DEFAULT_PRICING_KEY.
    """
    if model in SYNTHETIC_MODELS:
        return None
    table = table if table is not None else PRICING
    if model in table:
        return table[model]
    _warn_unknown_model(model)
    return table[DEFAULT_PRICING_KEY]


_warned_models: set[str] = set()
_warned_staleness = False


def _warn_unknown_model(model: str) -> None:
    if model in _warned_models:
        return
    _warned_models.add(model)
    log.warning("unknown model %r — falling back to %s pricing", model, DEFAULT_PRICING_KEY)


def warn_if_stale(threshold_months: int = 3) -> None:
    """One-shot stderr warning when the embedded pricing table is stale.
    Prices drift; without a fetch mechanism, the freshness marker is the
    only signal. Fires once per process to avoid log spam."""
    global _warned_staleness
    if _warned_staleness:
        return
    age = pricing_age_months()
    if age > threshold_months:
        _warned_staleness = True
        log.warning(
            "pricing table is %d months stale (updated %s) — override with --pricing-file",
            age, PRICING_UPDATED_AT,
        )


def cost_for_usage(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    table: Dict[str, Dict[str, float]] | None = None,
) -> float:
    """Compute USD cost for a single assistant message usage block."""
    p = get_model_pricing(model, table)
    if p is None:
        return 0.0
    return (
        input_tokens * p["input"]
        + cache_creation_tokens * p["cache_write_5m"]
        + cache_read_tokens * p["cache_read"]
        + output_tokens * p["output"]
    ) / 1_000_000


def pricing_age_months() -> int:
    """Months elapsed since PRICING_UPDATED_AT."""
    y, m = map(int, PRICING_UPDATED_AT.split("-"))
    today = date.today()
    return (today.year - y) * 12 + (today.month - m)
