#!/usr/bin/env python3
"""Tokenomy MCP server — read-only window into tokenomy telemetry.

Transport: JSON-RPC 2.0 over stdio, newline-delimited (per the MCP spec at
https://modelcontextprotocol.io). We hand-roll the transport because the
`mcp` Python SDK may not be installed on the user's system; if it *is*
installed the server still works — we just use the vendored loop below.

Tools (all read-only in v1):
- top_wasters(days, limit)        : tools with the largest aggregate byte spend
- cache_hit_rate(days)            : cache-read/creation ratio + avg write size
- block_state()                   : current 5h ccusage block cost + burn rate
- auto_rule_decisions()           : parsed decisions from _suggestions.md
- suggestions_md()                : raw _suggestions.md text

v2 (not implemented — protocol-level hazard without sandboxed writes):
- apply_fix(setting, value)       : push a cap to settings.json
- pin_cap(setting)                : mark a key user_pinned
- run_tuner(dry_run)              : trigger tuner.main()

Design notes:
- top_wasters/cache_hit_rate read ~/.claude/tokenomy/insights.json produced
  by `python -m analyzer.analyze`. If the file is missing we return an empty
  result with a hint rather than blocking for 20+ seconds to recompute.
- block_state reuses hooks/statusline.py:current_block_and_burn so the MCP
  figure matches the live statusline exactly.
- Every handler is wrapped in try/except; failures surface as JSON-RPC errors
  rather than crashing the stdio loop.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Resolve project root so we can import tokenomy modules when the server is
# launched with `python tokenomy_mcp/server.py` (no package install).
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ──────────────────────── paths ────────────────────────

TOKENOMY_HOME = Path(os.path.expanduser("~/.claude/tokenomy"))
INSIGHTS_PATH = TOKENOMY_HOME / "insights.json"
APPLIED_PATH = TOKENOMY_HOME / "applied.json"
SUGGESTIONS_PATH = TOKENOMY_HOME / "_suggestions.md"
SUGGESTIONS_MAX_BYTES = 64_000
LOG_PATH = TOKENOMY_HOME / "_mcp.log"

SERVER_NAME = "tokenomy"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"

# Build rule #1: every script logs to `_[name].log`. Keep the MCP loop's
# own log at WARNING so stdio stays clean — we only need failures.
log = logging.getLogger("tokenomy_mcp")
if not log.handlers:
    try:
        TOKENOMY_HOME.mkdir(parents=True, exist_ok=True)
        _h = logging.handlers.RotatingFileHandler(
            str(LOG_PATH), maxBytes=1_000_000, backupCount=2, encoding="utf-8"
        )
        _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(_h)
        log.setLevel(logging.INFO)
    except OSError:
        log.addHandler(logging.NullHandler())

# MCP notifications we accept silently. Explicit set prevents any "notifications/"-
# prefixed method name from being swallowed as a no-op (spec guarantee, but we're
# also the server — a typo from the client should surface an error, not vanish).
KNOWN_NOTIFICATIONS = {
    "notifications/initialized",
    "notifications/cancelled",
    "notifications/progress",
    "notifications/roots/list_changed",
}

# ──────────────────────── tool handlers ────────────────────────


def _load_insights() -> Dict[str, Any]:
    try:
        with INSIGHTS_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def top_wasters(days: int = 7, limit: int = 10) -> Dict[str, Any]:
    """Rank tools by total_bytes from the most recent analyzer run.

    `days` is informational — the figure is whatever period the last
    `python -m analyzer.analyze --days N` call covered. Re-run the analyzer
    with the target window to change it.
    """
    data = _load_insights()
    if not data:
        return {"error": "no insights.json — run `python -m analyzer.analyze` first",
                "items": []}
    by_tool = data.get("by_tool") or {}
    items = []
    for name, stats in by_tool.items():
        items.append({
            "tool": name,
            "waste_bytes": int(stats.get("total_bytes") or 0),
            "count": int(stats.get("count") or 0),
            "est_cost_usd": float(stats.get("est_cost_usd") or 0),
        })
    items.sort(key=lambda r: -r["waste_bytes"])
    return {
        "period_days": data.get("period", {}).get("days"),
        "items": items[: max(1, int(limit))],
    }


def cache_hit_rate(days: int = 7) -> Dict[str, Any]:
    """Ratio of cache-read tokens to cache-eligible input tokens.

    `total_reads` = cache_read_tokens (input tokens served from cache).
    `total_writes` = cache_creation_tokens (new cache entries written).
    `hit_rate` = reads / (reads + input_tokens)  —  fraction of prompt
        input that came out of cache on the input side of each turn.
    `avg_write_bytes` proxies via cache_creation_tokens / compact_events
        when available (cache writes happen once per cache-eligible boundary).
    """
    data = _load_insights()
    totals = data.get("totals") or {}
    if not totals:
        return {"error": "no insights.json — run `python -m analyzer.analyze` first"}
    reads = int(totals.get("cache_read_tokens") or 0)
    writes = int(totals.get("cache_creation_tokens") or 0)
    inp = int(totals.get("input_tokens") or 0)
    denom = reads + inp
    hit_rate = (reads / denom) if denom > 0 else 0.0
    compact_events = int(data.get("compact_events") or 0) or 1
    avg_write_bytes = (writes // compact_events) if writes else 0
    return {
        "period_days": data.get("period", {}).get("days"),
        "hit_rate": round(hit_rate, 4),
        "total_reads": reads,
        "total_writes": writes,
        "input_tokens": inp,
        "avg_write_bytes": avg_write_bytes,
    }


def block_state() -> Dict[str, Any]:
    """Reuse hooks/statusline.py for the current 5h ccusage block."""
    try:
        from hooks import statusline  # type: ignore
    except ImportError:
        # Not on path when server runs standalone; fall back to an explicit
        # file import so we don't hard-require a package install.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "statusline", str(ROOT / "hooks" / "statusline.py")
        )
        if spec is None or spec.loader is None:
            return {"error": "cannot load statusline module"}
        statusline = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(statusline)  # type: ignore

    pricing = statusline.load_pricing()
    block_start, block_cost, time_left, burn = statusline.current_block_and_burn(pricing)

    projected = burn * statusline.BLOCK_HOURS if (burn and time_left > 0) else 0.0
    return {
        "block_start": block_start.isoformat() if block_start else None,
        "block_cost_usd": round(float(block_cost or 0), 4),
        "time_left_seconds": int(time_left or 0),
        "burn_rate_usd_per_hour": round(float(burn or 0), 4),
        "projected_block_cost_usd": round(float(projected), 4),
    }


_DECISION_RE = re.compile(r"^\s*-\s+\*\*(?P<rule>[^*]+)\*\*:\s*`(?P<value>[^`]+)`\s*—\s*(?P<reason>.+)$")


def _parse_decisions(md: str) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    in_section = False
    for raw in md.splitlines():
        line = raw.rstrip()
        if line.startswith("## Decisions applied"):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section:
            continue
        m = _DECISION_RE.match(line)
        if m:
            out.append({
                "rule": m.group("rule").strip(),
                "value": m.group("value").strip(),
                "reason": m.group("reason").strip(),
            })
    return out


def suggestions_md() -> Dict[str, Any]:
    """Return _suggestions.md capped at SUGGESTIONS_MAX_BYTES to keep
    client-side context bounded. Callers that truly need the full file can
    read it from `path` on disk.
    """
    try:
        text = SUGGESTIONS_PATH.read_text(encoding="utf-8")
    except OSError:
        return {"error": f"not found: {SUGGESTIONS_PATH}", "raw": ""}
    truncated = False
    if len(text) > SUGGESTIONS_MAX_BYTES:
        text = text[:SUGGESTIONS_MAX_BYTES]
        truncated = True
    out: Dict[str, Any] = {"raw": text, "path": str(SUGGESTIONS_PATH)}
    if truncated:
        out["truncated"] = True
        out["bytes_returned"] = SUGGESTIONS_MAX_BYTES
    return out


def auto_rule_decisions() -> Dict[str, Any]:
    # Re-read the file here (instead of delegating to suggestions_md) so the
    # decisions parse uses the full text while the caller doesn't get the raw
    # file embedded twice. The 64K truncation is a client-surface concern only.
    try:
        raw = SUGGESTIONS_PATH.read_text(encoding="utf-8")
    except OSError:
        return {"path": str(SUGGESTIONS_PATH), "decisions": [], "error": "not found"}
    return {
        "path": str(SUGGESTIONS_PATH),
        "decisions": _parse_decisions(raw),
    }


def caps_savings() -> Dict[str, Any]:
    """Per-cap USD savings attributed on the last tuner run.

    Reads applied.json produced by the tuner. Values are the raw corpus
    attribution over whatever sample window the tuner used (typically ~14d
    with a recency-weighted decay). Settings with zero attributable savings
    are omitted, so an empty `savings` map means the current caps aren't
    binding on recent activity.
    """
    try:
        with APPLIED_PATH.open("r", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"path": str(APPLIED_PATH), "savings": {}, "error": "not found"}
    if not isinstance(state, dict):
        return {"path": str(APPLIED_PATH), "savings": {}, "error": "malformed"}
    savings = state.get("caps_savings") or {}
    if not isinstance(savings, dict):
        savings = {}
    total = round(sum(v for v in savings.values() if isinstance(v, (int, float))), 2)
    return {
        "path": str(APPLIED_PATH),
        "last_tune_at": state.get("last_tune_at"),
        "savings": savings,
        "total_usd": total,
    }


# ──────────────────────── tool registry ────────────────────────

TOOLS: Dict[str, Dict[str, Any]] = {
    "top_wasters": {
        "description": "Rank tools by aggregate byte spend from the most recent analyzer run.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Period in days (informational)", "default": 7},
                "limit": {"type": "integer", "description": "Max results", "default": 10},
            },
        },
        "handler": top_wasters,
    },
    "cache_hit_rate": {
        "description": "Prompt-cache hit rate, total reads/writes, and average write size.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "default": 7},
            },
        },
        "handler": cache_hit_rate,
    },
    "block_state": {
        "description": "Current 5h ccusage block: cost, time left, burn rate, projected cost.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": block_state,
    },
    "auto_rule_decisions": {
        "description": "Parsed auto-rule decisions from _suggestions.md (rule, value, reason).",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": auto_rule_decisions,
    },
    "suggestions_md": {
        "description": "Raw text of ~/.claude/tokenomy/_suggestions.md.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": suggestions_md,
    },
    "caps_savings": {
        "description": "USD savings attributed to each active cap from the last tuner run.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": caps_savings,
    },
}


# ──────────────────────── JSON-RPC transport ────────────────────────


def _send(msg: Dict[str, Any]) -> None:
    line = json.dumps(msg, ensure_ascii=False) + "\n"
    sys.stdout.write(line)
    sys.stdout.flush()


def _error(id_: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _ok(id_: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _handle_initialize(id_: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _ok(id_, {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
    })


def _handle_tools_list(id_: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    tools = [
        {"name": name, "description": spec["description"], "inputSchema": spec["inputSchema"]}
        for name, spec in TOOLS.items()
    ]
    return _ok(id_, {"tools": tools})


def _handle_tools_call(id_: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    name = params.get("name")
    args = params.get("arguments") or {}
    spec = TOOLS.get(name)
    if spec is None:
        return _error(id_, -32601, f"unknown tool: {name}")
    # Guard against arbitrary kwargs slipping into handler signatures. The MCP
    # client should only send keys declared in inputSchema, but we can't trust
    # that — a misbehaving client splatted into handler(**args) crashes the
    # server with TypeError: unexpected keyword argument.
    if isinstance(args, dict):
        allowed = set(((spec.get("inputSchema") or {}).get("properties") or {}).keys())
        extra = set(args.keys()) - allowed
        if extra:
            return _error(id_, -32602, f"unknown arguments for {name}: {sorted(extra)}")
    try:
        result = spec["handler"](**args) if args else spec["handler"]()
    except TypeError as e:
        return _error(id_, -32602, f"bad arguments for {name}: {e}")
    except Exception as e:
        log.exception("tool %s failed", name)
        return _error(id_, -32000, f"{name} failed: {e}")
    return _ok(id_, {
        "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2, default=str)}],
    })


HANDLERS = {
    "initialize": _handle_initialize,
    "tools/list": _handle_tools_list,
    "tools/call": _handle_tools_call,
}


def serve() -> int:
    # Newline-delimited JSON-RPC per MCP spec. Any parse error → JSON-RPC
    # parse error (-32700). Notifications (id missing) are handled silently.
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        if not isinstance(msg, dict):
            continue
        method = msg.get("method", "")
        id_ = msg.get("id")
        params = msg.get("params") or {}

        # `initialized` etc. are one-way notifications — no response.
        # Explicit set (KNOWN_NOTIFICATIONS) so a stray "notifications/foo"
        # from a buggy client surfaces as method-not-found instead of silently
        # vanishing. Unknown notifications with id=None still can't be
        # answered (spec), so we just log and continue.
        if id_ is None:
            if method in KNOWN_NOTIFICATIONS:
                continue
            log.warning("unhandled notification: %s", method)
            continue

        handler = HANDLERS.get(method)
        if handler is None:
            if id_ is not None:
                _send(_error(id_, -32601, f"method not found: {method}"))
            continue
        try:
            _send(handler(id_, params))
        except Exception as e:
            if id_ is not None:
                _send(_error(id_, -32000, f"internal error: {e}"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(serve())
    except KeyboardInterrupt:
        sys.exit(0)
