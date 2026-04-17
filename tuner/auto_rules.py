"""Auto-optimization rules that extend tuner based on usage patterns.

Pure detectors + orchestrator. Called from tuner.main after loss detection.
Rules emit either env overlays (auto-applied, gated by hysteresis) or
suggestions (human-reviewed, written to _suggestions.md).

Design principle: only auto-apply when the decision is reversible in one
env-var flip and the signal is low-noise. Anything that affects tool
availability (disabling an MCP server) stays as a suggestion.
"""
from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Tuple

from analyzer.extractors import Event, iter_corpus

log = logging.getLogger(__name__)

IDLE_GAP_5M_SEC = 300
IDLE_GAP_1H_SEC = 3600
IDLE_GAP_MIN_SAMPLES = 30
IDLE_GAP_FLIP_TO_1H_PCT = 0.40
IDLE_GAP_FLIP_TO_5M_PCT = 0.20
UNUSED_MCP_WINDOW_DAYS = 14
BIG_LOG_SIZE_BYTES = 100_000
BIG_LOG_MIN_OCCURRENCES = 2
AUTO_RULES_MAX_AGE_DAYS = 14


def _server_matches(server: str, allow: set[str]) -> bool:
    """Fuzzy match: allow-list entry is a substring of the event-reported name.

    Mirrors tuner.tuner._server_matches so "plugin_context7_context7" (from
    event tool_name) counts as "context7" (from active-server allow-list).
    Exact match wins; only fuzzy-match on entries ≥4 chars to avoid short
    tokens like "k" or "rg" collapsing every server into a single bucket.
    """
    if not allow:
        return True
    if server in allow:
        return True
    low = server.lower()
    return any(len(a) >= 4 and a.lower() in low for a in allow)


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def collect_recent_events(
    corpus_root: str,
    max_age_days: int = AUTO_RULES_MAX_AGE_DAYS,
    now: datetime | None = None,
) -> Dict[str, List[Event]]:
    """Return {session_id: [events]} for sessions active within max_age_days.

    Filters by the session's most recent event timestamp so we only keep
    corpora relevant to the current usage pattern.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max_age_days)
    out: Dict[str, List[Event]] = {}
    for path, events in iter_corpus(corpus_root):
        try:
            ev_list = list(events)
        except Exception as e:
            log.debug("auto_rules: skip %s: %s", path, e)
            continue
        if not ev_list:
            continue
        last_ts = next((_parse_ts(e.ts) for e in reversed(ev_list) if e.ts), None)
        if last_ts is None or last_ts < cutoff:
            continue
        sid = ev_list[0].session_id or path
        out[sid] = ev_list
    return out


def analyze_idle_gaps(session_events_map: Dict[str, List[Event]]) -> Dict[str, Any]:
    """Idle-gap distribution across all sessions.

    Gap = seconds between consecutive assistant_usage events within one session.
    Long gaps imply the user stepped away — the signal that decides 1h vs 5m cache.
    """
    gaps: List[float] = []
    for events in session_events_map.values():
        prev_dt: datetime | None = None
        for e in events:
            if e.kind != "assistant_usage":
                continue
            dt = _parse_ts(e.ts)
            if not dt:
                continue
            if prev_dt is not None:
                delta = (dt - prev_dt).total_seconds()
                if delta > 0:
                    gaps.append(delta)
            prev_dt = dt
    if not gaps:
        return {"pct_over_5m": 0.0, "pct_over_1h": 0.0, "n_gaps": 0, "median_gap_sec": 0.0}
    n = len(gaps)
    over_5m = sum(1 for g in gaps if g > IDLE_GAP_5M_SEC)
    over_1h = sum(1 for g in gaps if g > IDLE_GAP_1H_SEC)
    median = statistics.median(gaps)
    return {
        "pct_over_5m": over_5m / n,
        "pct_over_1h": over_1h / n,
        "n_gaps": n,
        "median_gap_sec": median,
    }


def decide_cache_ttl(idle_stats: Dict[str, Any], current_flag: str | None) -> Tuple[str, str]:
    """Decide ENABLE_PROMPT_CACHING_1H value from idle-gap stats.

    Returns "1" to enable, "0" to leave unset (Claude Code default = 5m).
    Hysteresis: flip to 1h at ≥40%, revert to 5m only at ≤20%. The 20% gap
    prevents week-to-week oscillation as workflow varies. Default is 5m when
    no signal — 1h costs 2× input on every write, only worth it with evidence.
    """
    n = idle_stats["n_gaps"]
    pct = idle_stats["pct_over_5m"]
    current = (current_flag or "").strip()
    if n < IDLE_GAP_MIN_SAMPLES:
        return (current or "0"), f"insufficient_data n={n}"
    if pct >= IDLE_GAP_FLIP_TO_1H_PCT:
        return "1", f"idle>5m_pct={pct:.1%}"
    if pct <= IDLE_GAP_FLIP_TO_5M_PCT:
        return "0", f"continuous_pct={pct:.1%}"
    return (current or "0"), f"hysteresis_band_pct={pct:.1%}"


def detect_unused_mcp(
    session_events_map: Dict[str, List[Event]],
    active_servers: Iterable[str],
    now: datetime | None = None,
    window_days: int = UNUSED_MCP_WINDOW_DAYS,
) -> List[Dict[str, Any]]:
    """Return MCP servers with no tool_use events in the last window_days."""
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)
    last_seen: Dict[str, datetime] = {}
    for events in session_events_map.values():
        for e in events:
            if e.kind != "tool_use":
                continue
            tname = e.tool_name or ""
            if not tname.startswith("mcp__"):
                continue
            parts = tname.split("__")
            if len(parts) < 3:
                continue
            server = parts[1]
            dt = _parse_ts(e.ts)
            if not dt:
                continue
            prev = last_seen.get(server)
            if prev is None or dt > prev:
                last_seen[server] = dt
    out: List[Dict[str, Any]] = []
    for server in active_servers:
        # Fuzzy match: event tool_names carry plugin prefixes (e.g.
        # "plugin_context7_context7") while active_servers lists canonical
        # names ("context7"). Collect every last_seen key that matches.
        matched = [dt for key, dt in last_seen.items() if _server_matches(key, {server})]
        seen = max(matched) if matched else None
        if seen is None:
            out.append({"server": server, "last_used": None, "days_ago": None})
        elif seen < cutoff:
            out.append({"server": server, "last_used": seen.isoformat(), "days_ago": (now - seen).days})
    return out


def detect_big_file_reads(
    session_events_map: Dict[str, List[Event]],
    size_threshold: int = BIG_LOG_SIZE_BYTES,
    min_occurrences: int = BIG_LOG_MIN_OCCURRENCES,
) -> List[Dict[str, Any]]:
    """Return Read paths whose tool_result exceeded threshold repeatedly.

    These are `.claudeignore` candidates — files the user keeps reading that
    bloat context. The counterfactual cost of excluding them is low (always
    accessible via Bash cat) so we only suggest, never auto-apply.
    """
    path_by_id: Dict[str, str] = {}
    for events in session_events_map.values():
        for e in events:
            if e.kind == "tool_use" and e.tool_name == "Read" and e.tool_use_id:
                fp = (e.input_summary or {}).get("file_path", "")
                if fp:
                    path_by_id[e.tool_use_id] = fp
    counts: Dict[str, int] = defaultdict(int)
    total_bytes: Dict[str, int] = defaultdict(int)
    for events in session_events_map.values():
        for e in events:
            if e.kind != "tool_result" or e.response_size_bytes < size_threshold:
                continue
            fp = path_by_id.get(e.tool_use_id or "")
            if fp:
                counts[fp] += 1
                total_bytes[fp] += e.response_size_bytes
    return [
        {"file_path": fp, "count": c, "total_bytes": total_bytes[fp]}
        for fp, c in sorted(counts.items(), key=lambda x: -x[1])
        if c >= min_occurrences
    ]


def run(
    session_events_map: Dict[str, List[Event]],
    current_env: Dict[str, str] | None = None,
    active_servers: Iterable[str] | None = None,
    now: datetime | None = None,
) -> Dict[str, Any]:
    """Run all auto-rules, return env overlays + human-readable suggestions."""
    current_env = current_env or {}
    idle = analyze_idle_gaps(session_events_map)
    cache_val, cache_reason = decide_cache_ttl(idle, current_env.get("ENABLE_PROMPT_CACHING_1H"))

    env_overlays: Dict[str, str] = {}
    # Only write the flag when data supports "1". For "0" we leave it unset
    # (Claude Code default = 5m). Saves a useless env entry and matches the
    # semantics of "no signal → no override".
    if cache_val == "1":
        env_overlays["ENABLE_PROMPT_CACHING_1H"] = "1"

    decisions = [{
        "rule": "cache_ttl",
        "value": cache_val,
        "reason": cache_reason,
        "idle_stats": idle,
    }]
    unused = detect_unused_mcp(session_events_map, active_servers or [], now=now) if active_servers else []
    big_reads = detect_big_file_reads(session_events_map)
    return {
        "env_overlays": env_overlays,
        "suggestions": {
            "unused_mcp": unused,
            "big_file_reads": big_reads,
        },
        "decisions": decisions,
    }


def render_suggestions_md(results: Dict[str, Any], now: datetime | None = None) -> str:
    if now is None:
        now = datetime.now(timezone.utc)
    lines = [
        "# Tokenomy auto-rule suggestions",
        "",
        f"_Generated: {now.isoformat()}_",
        "",
        "## Decisions applied",
    ]
    for d in results.get("decisions", []):
        lines.append(f"- **{d['rule']}**: `{d['value']}` — {d['reason']}")
        if d.get("idle_stats"):
            s = d["idle_stats"]
            lines.append(
                f"  - n_gaps={s['n_gaps']}, pct>5m={s['pct_over_5m']:.1%}, "
                f"pct>1h={s['pct_over_1h']:.1%}, median={s['median_gap_sec']:.0f}s"
            )
    lines.append("")
    sugg = results.get("suggestions", {})
    unused = sugg.get("unused_mcp", [])
    if unused:
        lines.append("## Unused MCP servers (consider disabling via /mcp)")
        for u in unused:
            if u.get("last_used"):
                lines.append(f"- `{u['server']}` — last used {u['days_ago']}d ago")
            else:
                lines.append(f"- `{u['server']}` — never invoked in {UNUSED_MCP_WINDOW_DAYS}d window")
        lines.append("")
    big = sugg.get("big_file_reads", [])
    if big:
        lines.append("## Large file reads (consider adding to `.claudeignore`)")
        for b in big[:10]:
            kb = b["total_bytes"] // 1024
            lines.append(f"- `{b['file_path']}` — {b['count']}× over threshold, {kb}KB total")
        lines.append("")
    return "\n".join(lines)
