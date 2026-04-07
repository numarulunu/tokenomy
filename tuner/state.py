"""Atomic read/write of applied.json. Fail-open."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any, Dict

log = logging.getLogger(__name__)

SCHEMA_VERSION = "0.3.0"


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
        # schema migration stub
        if data.get("version") != SCHEMA_VERSION:
            log.info("schema mismatch %s != %s — keeping data", data.get("version"), SCHEMA_VERSION)
            data["version"] = SCHEMA_VERSION
        # ensure required keys
        base = empty_state()
        base.update(data)
        return base
    except (OSError, json.JSONDecodeError) as e:
        log.warning("corrupt applied.json (%s) — resetting", e)
        return empty_state()


def save_state(path: str, state: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".applied.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
