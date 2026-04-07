"""Recency-weighted percentile math. Pure functions, no I/O."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Sequence, Tuple

HALF_LIFE_DAYS = 14.0
MIN_WEIGHT = 0.01  # batch-user mitigation: ancient samples still count a little


def session_weight(age_days: float) -> float:
    """Exponential decay. Negative ages (clock skew) clamp to 1.0."""
    if age_days <= 0:
        return 1.0
    w = 0.5 ** (age_days / HALF_LIFE_DAYS)
    return max(w, MIN_WEIGHT)


def age_days(ts: str, now: datetime | None = None) -> float:
    """Parse ISO timestamp, return age in days vs now (UTC)."""
    if now is None:
        now = datetime.now(timezone.utc)
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return 0.0
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (now - t).total_seconds() / 86400.0


def weighted_percentile(samples: Sequence[Tuple[float, float]], p: float) -> float:
    """Weighted percentile. samples = [(value, weight), ...]. p in [0,1]."""
    pairs = sorted((float(v), float(w)) for v, w in samples if w > 0)
    if not pairs:
        return 0.0
    total = sum(w for _, w in pairs)
    if total <= 0:
        return 0.0
    target = p * total
    cum = 0.0
    for v, w in pairs:
        cum += w
        if cum >= target:
            return v
    return pairs[-1][0]


def confidence(effective_n: float) -> float:
    return min(1.0, max(0.0, effective_n) / 5_000.0)


def aggressive_percentile(conf: float) -> float:
    return 0.99 - 0.04 * conf


def margin(conf: float) -> float:
    return 1.5 - 0.25 * conf


def compute_cap(samples_with_weights: Iterable[Tuple[float, float]], floor: int) -> int:
    pairs = list(samples_with_weights)
    eff_n = sum(w for _, w in pairs)
    conf = confidence(eff_n)
    pct = aggressive_percentile(conf)
    m = margin(conf)
    p = weighted_percentile(pairs, pct)
    return max(int(p * m), floor)
