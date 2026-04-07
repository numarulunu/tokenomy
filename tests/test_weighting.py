"""Unit tests for tuner.weighting."""
from __future__ import annotations

from tuner.weighting import (
    HALF_LIFE_DAYS,
    MIN_WEIGHT,
    aggressive_percentile,
    compute_cap,
    confidence,
    margin,
    session_weight,
    weighted_percentile,
)


def test_session_weight_today():
    assert session_weight(0) == 1.0


def test_session_weight_negative_clock_skew():
    assert session_weight(-3) == 1.0


def test_session_weight_half_life():
    assert abs(session_weight(HALF_LIFE_DAYS) - 0.5) < 1e-9


def test_session_weight_ancient_floor():
    # 100 days → 0.5 ** ~7.14 ≈ 0.007 → clamped to MIN_WEIGHT
    assert session_weight(1000) == MIN_WEIGHT


def test_weighted_percentile_empty():
    assert weighted_percentile([], 0.95) == 0.0


def test_weighted_percentile_single():
    assert weighted_percentile([(42.0, 1.0)], 0.95) == 42.0


def test_weighted_percentile_uniform():
    # 1..10 uniform weights, p95 → ~10
    samples = [(float(i), 1.0) for i in range(1, 11)]
    assert weighted_percentile(samples, 0.95) == 10.0
    assert weighted_percentile(samples, 0.5) == 5.0


def test_weighted_percentile_skewed_recent_dominates():
    # Old samples are tiny, recent samples are large with high weight.
    samples = [(1.0, 0.01)] * 100 + [(1000.0, 1.0)] * 5
    p = weighted_percentile(samples, 0.95)
    assert p == 1000.0


def test_weighted_percentile_zero_weights():
    assert weighted_percentile([(5.0, 0.0), (10.0, 0.0)], 0.95) == 0.0


def test_confidence_curve():
    assert confidence(0) == 0.0
    assert confidence(2500) == 0.5
    assert confidence(10_000) == 1.0


def test_aggressive_percentile_curve():
    assert aggressive_percentile(0.0) == 0.99
    assert abs(aggressive_percentile(1.0) - 0.95) < 1e-9


def test_margin_curve():
    assert margin(0.0) == 1.5
    assert margin(1.0) == 1.25


def test_compute_cap_respects_floor():
    # Tiny values, low confidence → floor wins
    samples = [(10.0, 1.0)] * 3
    assert compute_cap(samples, floor=5000) == 5000


def test_compute_cap_above_floor():
    # Big distribution at high confidence
    samples = [(8000.0, 1.0)] * 6000
    cap = compute_cap(samples, floor=4000)
    # p95 ~8000, margin ~1.25 → ~10000
    assert 9000 <= cap <= 12000
