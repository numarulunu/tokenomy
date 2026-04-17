#!/usr/bin/env python3
"""Fetch Anthropic Claude Code quota usage via OAuth endpoint.

Mirrors OmniRoute's approach (open-sse/services/usage.ts:1000-1067):
calls GET api.anthropic.com/api/oauth/usage with the OAuth access token
from ~/.claude/.credentials.json. Returns the real quota state Claude Code
uses for throttling — more accurate than repricing local transcripts
against guessed USD caps, since Anthropic's limits are opaque fair-use
buckets (not USD-denominated).

Cache: ~/.claude/tokenomy/usage.json with 120s TTL.
Fail-open: any error (no token, network, 4xx/5xx, timeout) returns the
stale cache or None so callers can gracefully fall back.

Opt-out: set TOKENOMY_DISABLE_USAGE_FETCH=1 to skip outbound calls.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
HEADERS = {
    "anthropic-beta": "oauth-2025-04-20",
    "anthropic-version": "2023-06-01",
}
CACHE_TTL_SEC = 120
FETCH_TIMEOUT_SEC = 1.5
HISTORY_MAX_AGE_SEC = 70 * 60   # keep ~1h of samples for slope math
HISTORY_MAX_ENTRIES = 40        # safety cap on cache file size
BURN_MIN_SPAN_SEC = 5 * 60      # need ≥5 min of samples before trusting slope


def cache_path() -> Path:
    return Path.home() / ".claude" / "tokenomy" / "usage.json"


def credentials_path() -> Path:
    return Path.home() / ".claude" / ".credentials.json"


def read_token() -> str | None:
    """Pull the OAuth access token Claude Code persists after login.
    Returns None if the file is missing or malformed."""
    try:
        d = json.loads(credentials_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    oauth = d.get("claudeAiOauth") or {}
    tok = oauth.get("accessToken")
    return tok if isinstance(tok, str) and tok else None


def _fetch(token: str) -> dict | None:
    req = urllib.request.Request(USAGE_URL, method="GET")
    for k, v in HEADERS.items():
        req.add_header(k, v)
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SEC) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode("utf-8"))
            return data if isinstance(data, dict) else None
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            json.JSONDecodeError, TimeoutError):
        return None


def _validate_usage_entry(w: Any) -> bool:
    """Assert required shape for one usage window: `utilization` numeric and
    `resets_at` non-empty string. Used to detect upstream schema drift so we
    surface it in logs instead of silently zero-filling."""
    if not isinstance(w, dict):
        return False
    u = w.get("utilization")
    r = w.get("resets_at")
    return isinstance(u, (int, float)) and isinstance(r, str) and bool(r)


def _parse(payload: dict) -> dict:
    """Reshape Anthropic's response to the minimal fields we render.

    `utilization` is % USED in the upstream payload — flip to % LEFT so the
    statusline convention "high number = healthy" holds (green when plenty
    of quota remains). Also stash the raw used values so history-based burn
    rate math doesn't have to invert pct_left → used on every read.

    Malformed windows are dropped (not zero-filled) and logged at WARNING so
    a schema drift in the upstream endpoint is visible instead of quietly
    producing a bogus 0%-used reading."""
    out: dict[str, Any] = {"fetched_at": int(time.time())}
    for k_in, k_pct, k_reset, k_used in (
        ("five_hour", "sess_pct_left", "sess_resets_at", "sess_pct_used"),
        ("seven_day", "week_pct_left", "week_resets_at", "week_pct_used"),
    ):
        w = payload.get(k_in)
        if not _validate_usage_entry(w):
            if w is not None:
                log.warning(
                    "usage payload %r window has unexpected shape — dropping. raw=%r",
                    k_in, w,
                )
            continue
        used = max(0, min(100, int(round(w["utilization"]))))
        out[k_pct] = 100 - used
        out[k_used] = used
        out[k_reset] = w["resets_at"]
    tier = payload.get("tier")
    if isinstance(tier, str):
        out["tier"] = tier
    return out


def _merge_history(old_cache: dict | None, parsed: dict) -> list[dict]:
    """Append the current sample to a rolling history for slope-based burn
    rate. Prunes anything older than HISTORY_MAX_AGE_SEC and caps the list
    at HISTORY_MAX_ENTRIES so the cache file stays small."""
    now = parsed.get("fetched_at") or int(time.time())
    history: list[dict] = []
    if isinstance(old_cache, dict):
        prev = old_cache.get("history")
        if isinstance(prev, list):
            history = [h for h in prev if isinstance(h, dict)]
    sample = {"at": now}
    if "sess_pct_used" in parsed:
        sample["sess_used"] = parsed["sess_pct_used"]
    if "week_pct_used" in parsed:
        sample["week_used"] = parsed["week_pct_used"]
    history.append(sample)
    cutoff = now - HISTORY_MAX_AGE_SEC
    history = [h for h in history if isinstance(h.get("at"), (int, float)) and h["at"] >= cutoff]
    if len(history) > HISTORY_MAX_ENTRIES:
        history = history[-HISTORY_MAX_ENTRIES:]
    return history


def burn_pct_per_hour(cache: dict | None, window: str = "sess") -> float | None:
    """Return %/hr of the quota window being consumed, derived from the
    rolling history. `window` is "sess" (5h) or "week" (7d). Returns None
    when there aren't enough samples to estimate reliably."""
    if not isinstance(cache, dict):
        return None
    history = cache.get("history")
    if not isinstance(history, list) or len(history) < 2:
        return None
    key = f"{window}_used"
    pts = [
        (h["at"], h[key])
        for h in history
        if isinstance(h, dict)
        and isinstance(h.get("at"), (int, float))
        and isinstance(h.get(key), (int, float))
    ]
    if len(pts) < 2:
        return None
    pts.sort(key=lambda p: p[0])
    span = pts[-1][0] - pts[0][0]
    if span < BURN_MIN_SPAN_SEC:
        return None
    delta = pts[-1][1] - pts[0][1]
    # Quota can tick down when a sub-window rolls off; treat that as zero
    # burn rather than negative (which would render nonsensically).
    if delta < 0:
        delta = 0
    return delta * 3600.0 / span


def _write_cache(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def load_cache() -> dict | None:
    p = cache_path()
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def is_fresh(cache: dict | None, max_age: int = CACHE_TTL_SEC) -> bool:
    if not cache:
        return False
    fa = cache.get("fetched_at")
    return isinstance(fa, (int, float)) and (time.time() - fa) < max_age


def refresh_if_stale(max_age: int = CACHE_TTL_SEC) -> dict | None:
    """Return fresh cache, refreshing synchronously on miss.

    Returns stale cache on fetch failure so the statusline has a sane
    value to render. Returns None only when no cache exists and the
    fetch also fails — callers must treat this as 'fall back to heuristic'."""
    # TOKENOMY_OFF is the global killswitch; TOKENOMY_DISABLE_USAGE_FETCH is
    # the narrower legacy opt-out (kept for backwards compat). Either disables.
    if os.environ.get("TOKENOMY_OFF") or os.environ.get("TOKENOMY_DISABLE_USAGE_FETCH"):
        return None
    cache = load_cache()
    if is_fresh(cache, max_age):
        return cache
    token = read_token()
    if not token:
        return cache
    payload = _fetch(token)
    if not payload:
        return cache
    parsed = _parse(payload)
    parsed["history"] = _merge_history(cache, parsed)
    _write_cache(cache_path(), parsed)
    return parsed


def main() -> int:
    data = refresh_if_stale(max_age=0)
    if data:
        print(json.dumps(data, indent=2))
        return 0
    print("failed to fetch usage", file=sys.stderr)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
