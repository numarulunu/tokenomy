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

# USD per 1,000,000 tokens.
PRICING: Dict[str, Dict[str, float]] = {
    "claude-opus-4-6":           {"input": 15.00, "output": 75.00, "cache_write": 18.75, "cache_read":  1.50},
    "claude-opus-4-6[1m]":       {"input": 15.00, "output": 75.00, "cache_write": 18.75, "cache_read":  1.50},
    "claude-sonnet-4-6":         {"input":  3.00, "output": 15.00, "cache_write":  3.75, "cache_read":  0.30},
    "claude-sonnet-4-6[1m]":     {"input":  3.00, "output": 15.00, "cache_write":  3.75, "cache_read":  0.30},
    "claude-haiku-4-5":          {"input":  1.00, "output":  5.00, "cache_write":  1.25, "cache_read":  0.10},
    "claude-haiku-4-5-20251001": {"input":  1.00, "output":  5.00, "cache_write":  1.25, "cache_read":  0.10},
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


def _warn_unknown_model(model: str) -> None:
    if model in _warned_models:
        return
    _warned_models.add(model)
    log.warning("unknown model %r — falling back to %s pricing", model, DEFAULT_PRICING_KEY)


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
        + cache_creation_tokens * p["cache_write"]
        + cache_read_tokens * p["cache_read"]
        + output_tokens * p["output"]
    ) / 1_000_000


def pricing_age_months() -> int:
    """Months elapsed since PRICING_UPDATED_AT."""
    y, m = map(int, PRICING_UPDATED_AT.split("-"))
    today = date.today()
    return (today.year - y) * 12 + (today.month - m)
