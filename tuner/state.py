"""Atomic read/write of applied.json. Fail-open."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any, Callable, Dict, List, Tuple

log = logging.getLogger(__name__)

SCHEMA_VERSION = "0.6.0"

# Ordered migration chain. Each entry transforms `data` from `from_v` to `to_v`;
# mutate in place, dispatcher stamps the version. Walk is ordered so a multi-hop
# upgrade (e.g. 0.5 → 0.6 → 0.7) resolves automatically. Empty today because
# v0.6.0 → v0.7.0 added no schema fields; kept so the next schema break has a
# ready home and doesn't need to introduce the infrastructure simultaneously.
_MIGRATIONS: List[Tuple[str, str, Callable[[Dict[str, Any]], None]]] = []


def _migrate(data: Dict[str, Any]) -> Dict[str, Any]:
    """Walk the migration chain from data's version to SCHEMA_VERSION.
    Unknown/unreachable versions log WARNING and get stamped to current —
    fail-open so a stale applied.json never blocks the tuner."""
    current = data.get("version") or "0.0.0"
    if current == SCHEMA_VERSION:
        return data
    # Walk forward; allow multi-hop by re-scanning after each applied step.
    changed = True
    while changed and current != SCHEMA_VERSION:
        changed = False
        for from_v, to_v, fn in _MIGRATIONS:
            if current == from_v:
                log.info("migrating applied.json %s -> %s", from_v, to_v)
                fn(data)
                data["version"] = to_v
                current = to_v
                changed = True
                break
    if current != SCHEMA_VERSION:
        log.warning("no migration path from %r to %s — keeping data, stamping version",
                    data.get("version"), SCHEMA_VERSION)
        data["version"] = SCHEMA_VERSION
    return data


def empty_state() -> Dict[str, Any]:
    return {
        "version": SCHEMA_VERSION,
        "last_tune_at": None,
        "effective_n": 0.0,
        "confidence": 0.0,
        "caps": {},
        "cooldowns": {},
        "freezes": {},
        "user_pinned": [],
        "estimated_savings_usd_per_month": 0.0,
        "caps_savings": {},
        "rolling_mean_output": 0.0,
        "rolling_mean_seeded": False,
        # Phase 3b per-project overrides. Shape:
        #   {<abs_path>: {"caps": {...}, "cooldowns": {...}, "freezes": {...},
        #                 "effective_n": float, "last_tune_at": str}}
        # Projects below MIN_EFFECTIVE_N_PER_PROJECT are not populated; they
        # inherit user-level caps. Entries for projects no longer in the
        # corpus are left alone — garbage collection is deferred.
        "projects": {},
    }


def load_state(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return empty_state()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            log.warning("applied.json not a dict — resetting")
            return empty_state()
        data = _migrate(data)
        # Strict schema boundary: only keys defined by empty_state() survive.
        # Previously any stray field from a newer version or manual edit was
        # round-tripped to disk indefinitely. Log unknowns at DEBUG so ops can
        # see what was dropped during an upgrade/downgrade cycle.
        base = empty_state()
        unknown = [k for k in data if k not in base]
        if unknown:
            log.debug("dropping unknown applied.json keys: %s", unknown)
        for k in base:
            if k in data:
                base[k] = data[k]
        return base
    except (OSError, json.JSONDecodeError) as e:
        log.warning("corrupt applied.json (%s) — resetting", e)
        return empty_state()


def save_state(path: str, state: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".applied.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, sort_keys=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
