"""Entrypoint: walks ~/.claude/projects/**, aggregates, writes insights.json.

Usage:
    python -m analyzer.analyze [--days 30] [--project PATH] [--json-out FILE]
                               [--no-report] [--pricing-file FILE] [--root DIR]

Design: single pass, streaming. Bounded memory: we keep top-N heaps for
outliers, running sums for totals, a small per-tool dict, and a per-session
dedup set that's dropped when the session finishes streaming.
"""
from __future__ import annotations

import argparse
import heapq
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from . import __version__, counterfactual, pricing, report
from .extractors import Event, iter_session_file

log = logging.getLogger("tokenomy.analyzer")

DEFAULT_ROOT = os.path.expanduser("~/.claude/projects")
DEFAULT_OUT = os.path.expanduser("~/.claude/tokenomy/insights.json")

LOG_PATH_HINTS = (".log", "_log", "/logs/", "\\logs\\", ".log.")
LOG_SIZE_THRESHOLD = 20_000  # chars — a "bloated" log read
LOG_GREP_CEILING = 5_000     # what filtered content would have cost

OUTLIER_TOP_N = 50
TRUNCATION_RESCAN_WINDOW = 5  # assistant turns


# ---------------------------------------------------------------------------
# Running percentile (light, approximate) — we keep the raw list per tool but
# cap it at MAX_SAMPLES_PER_TOOL to bound memory. For populations beyond that,
# we reservoir-sample.
# ---------------------------------------------------------------------------
MAX_SAMPLES_PER_TOOL = 10_000


class ToolStats:
    __slots__ = ("count", "total_bytes", "max_size", "samples", "_rng_counter")

    def __init__(self) -> None:
        self.count = 0
        self.total_bytes = 0
        self.max_size = 0
        self.samples: list[int] = []
        self._rng_counter = 0

    def add(self, size: int) -> None:
        self.count += 1
        self.total_bytes += size
        if size > self.max_size:
            self.max_size = size
        if len(self.samples) < MAX_SAMPLES_PER_TOOL:
            self.samples.append(size)
        else:
            # simple reservoir sampling
            self._rng_counter += 1
            import random
            j = random.randint(0, self.count - 1)
            if j < MAX_SAMPLES_PER_TOOL:
                self.samples[j] = size

    def percentiles(self) -> Dict[str, int]:
        if not self.samples:
            return {"p50": 0, "p95": 0, "p99": 0}
        s = sorted(self.samples)
        def pct(p: float) -> int:
            idx = min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))
            return s[idx]
        return {"p50": pct(0.50), "p95": pct(0.95), "p99": pct(0.99)}


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------
class Aggregator:
    def __init__(self, pricing_table: Dict[str, Dict[str, float]], since: Optional[datetime]):
        self.pricing_table = pricing_table
        self.since = since
        # Totals
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.cache_creation_tokens = 0
        self.cost_usd = 0.0
        self.sessions_seen: set[str] = set()
        self.compact_count = 0
        # Per-tool
        self.by_tool: Dict[str, ToolStats] = defaultdict(ToolStats)
        self.tool_cost: Dict[str, float] = defaultdict(float)
        # Per-project
        self.by_project: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"sessions": set(), "tokens": 0, "cost": 0.0}
        )
        # Per-hour
        self.by_hour: Dict[int, int] = defaultdict(int)
        # Outliers: min-heap of (size, seq, event_dict), capped at OUTLIER_TOP_N
        self.outliers: list[tuple[int, int, dict]] = []
        self._outlier_seq = 0
        # Session-scoped state (flushed between sessions)
        self._current_session: Optional[str] = None
        self._read_keys_in_session: Dict[tuple, int] = {}
        self._log_read_bytes = 0
        self._log_read_over_ceiling = 0  # bytes above LOG_GREP_CEILING
        self._dup_read_bytes = 0
        self._dup_read_count = 0
        # Tool result attribution: pair tool_use_id → next assistant turn.
        # We record {tool_use_id: (tool_name, size_bytes, session_id)} until the
        # *next* assistant_usage event in the same session arrives.
        self._pending_tool_results: list[dict] = []
        # Counterfactual raw streams
        self.tool_results_for_cf: list[dict] = []
        self.reactions: Dict[str, dict] = {}
        self.assistant_usages_for_cf: list[dict] = []
        # Track what tool the last assistant called, per session — for requery detection
        self._session_last_tools: Dict[str, list[str]] = defaultdict(list)
        # Read attribution for dedup
        self._last_read_by_session: Dict[str, list[tuple]] = defaultdict(list)

    # ---- main ingest ----
    def process_event(self, ev: Event) -> None:
        # Date filter
        if self.since and ev.ts:
            try:
                t = _parse_ts(ev.ts)
                if t and t < self.since:
                    return
            except Exception:
                pass

        if ev.session_id:
            self.sessions_seen.add(ev.session_id)
            if self._current_session and ev.session_id != self._current_session:
                self._flush_session()
            self._current_session = ev.session_id

        if ev.kind == "assistant_usage":
            self._on_assistant_usage(ev)
        elif ev.kind == "tool_use":
            self._on_tool_use(ev)
        elif ev.kind == "tool_result":
            self._on_tool_result(ev)
        elif ev.kind == "compact":
            self.compact_count += 1

    def _on_assistant_usage(self, ev: Event) -> None:
        self.input_tokens += ev.input_tokens
        self.output_tokens += ev.output_tokens
        self.cache_read_tokens += ev.cache_read_tokens
        self.cache_creation_tokens += ev.cache_creation_tokens
        # Exclude cache_read from the headline cost: Claude Code reports the
        # cumulative cache per turn, so summing across turns double-counts
        # massively. We still record cache_read_tokens in totals for visibility.
        cost = pricing.cost_for_usage(
            ev.model or pricing.DEFAULT_PRICING_KEY,
            ev.input_tokens,
            ev.output_tokens,
            ev.cache_creation_tokens,
            cache_read_tokens=0,
            table=self.pricing_table,
        )
        self.cost_usd += cost

        # Per-hour
        if ev.ts:
            t = _parse_ts(ev.ts)
            if t:
                self.by_hour[t.hour] += 1

        # Per-project
        if ev.project:
            p = self.by_project[ev.project]
            p["sessions"].add(ev.session_id)
            p["tokens"] += ev.input_tokens + ev.output_tokens + ev.cache_read_tokens + ev.cache_creation_tokens
            p["cost"] += cost

        # Attribute any pending tool_results to this reaction.
        for tr in self._pending_tool_results:
            self.reactions[tr["tool_use_id"]] = {
                "model": ev.model,
                "requeried_same_tool": False,  # filled in when we see next tool_use
            }
        self._pending_tool_results = []

        self.assistant_usages_for_cf.append(
            {
                "model": ev.model,
                "output_tokens": ev.output_tokens,
                "text_tail": ev.text_tail,
            }
        )

    def _on_tool_use(self, ev: Event) -> None:
        tname = ev.tool_name or "unknown"

        # Requery detection: if the last tool_result in this session matches
        # this tool name, mark its reaction as requeried.
        hist = self._session_last_tools[ev.session_id or ""]
        if hist and hist[-1] == tname:
            # find most recent unreviewed reaction with this tool
            # (cheap: iterate backwards over a short window)
            count = 0
            for tid, r in reversed(list(self.reactions.items())):
                count += 1
                if count > TRUNCATION_RESCAN_WINDOW:
                    break
                if not r.get("requeried_same_tool") and r.get("_last_tool") == tname:
                    r["requeried_same_tool"] = True
                    break
        hist.append(tname)

        # Dedup Reads
        if tname == "Read":
            key = (
                _norm_path(ev.input_summary.get("file_path", "")),
                ev.input_summary.get("offset"),
                ev.input_summary.get("limit"),
            )
            prev = self._read_keys_in_session.get(key)
            if prev is not None:
                # duplicate read — credit the dup once we see its size.
                self._pending_dup_key = key
            self._read_keys_in_session[key] = self._read_keys_in_session.get(key, 0) + 1

    def _on_tool_result(self, ev: Event) -> None:
        size = ev.response_size_bytes
        # tool_name is unknown from the tool_result alone; we attribute by
        # scanning the last tool_use in this session.
        hist = self._session_last_tools.get(ev.session_id or "") or []
        tname = hist[-1] if hist else "unknown"

        stats = self.by_tool[tname]
        stats.add(size)

        # Log read detection
        if tname == "Read" and size > LOG_SIZE_THRESHOLD:
            # Look at the corresponding file_path from the last Read tool_use.
            # Approximation: assume the latest Read tool_use matches.
            if _looks_like_log_path_hist(ev.session_id or "", self._read_keys_in_session):
                self._log_read_bytes += size
                self._log_read_over_ceiling += max(0, size - LOG_GREP_CEILING)

        # Duplicate reads: if any key in this session has count >= 2, credit
        # the duplicates (n-1 copies).
        if tname == "Read":
            # We credit at flush time to avoid double counting — record nothing now.
            pass

        # Outliers (top-N by size)
        rec = {
            "tool": tname,
            "size": size,
            "project": ev.project,
            "ts": ev.ts,
            "session_id": ev.session_id,
        }
        self._outlier_seq += 1
        if len(self.outliers) < OUTLIER_TOP_N:
            heapq.heappush(self.outliers, (size, self._outlier_seq, rec))
        elif size > self.outliers[0][0]:
            heapq.heapreplace(self.outliers, (size, self._outlier_seq, rec))

        # Counterfactual raw
        if ev.tool_use_id:
            self.tool_results_for_cf.append(
                {
                    "tool_name": tname,
                    "size_bytes": size,
                    "session_id": ev.session_id,
                    "tool_use_id": ev.tool_use_id,
                }
            )
            # Mark this reaction-waiting slot with the tool name for requery detection
            self._pending_tool_results.append({"tool_use_id": ev.tool_use_id})
            # stash _last_tool for later reaction update
            self.reactions.setdefault(ev.tool_use_id, {})["_last_tool"] = tname

    def _flush_session(self) -> None:
        # Credit duplicate reads: for each key seen >1 times, the extra copies
        # are wasted. We don't know per-read sizes here, so approximate by
        # scanning outliers? Simpler: use the total_bytes for Reads divided by
        # count to get mean, times extras.
        extra = 0
        for _k, c in self._read_keys_in_session.items():
            if c > 1:
                extra += c - 1
                self._dup_read_count += c - 1
        if extra:
            # Estimate wasted bytes as extra * mean-Read-size (bounded).
            rstats = self.by_tool.get("Read")
            if rstats and rstats.count:
                mean = rstats.total_bytes // rstats.count
                self._dup_read_bytes += extra * mean
        self._read_keys_in_session.clear()

    # ---- finalize ----
    def finalize(self, since: Optional[datetime], until: datetime) -> Dict[str, Any]:
        self._flush_session()

        # Build by_tool output
        by_tool_out: Dict[str, Any] = {}
        for name, s in self.by_tool.items():
            pct = s.percentiles()
            by_tool_out[name] = {
                "count": s.count,
                "total_bytes": s.total_bytes,
                "p50": pct["p50"],
                "p95": pct["p95"],
                "p99": pct["p99"],
                "max": s.max_size,
                # Rough cost attribution: treat bytes as input tokens for the
                # default model. This is an order-of-magnitude figure only.
                "est_cost_usd": round(
                    pricing.cost_for_usage(
                        pricing.DEFAULT_PRICING_KEY,
                        input_tokens=s.total_bytes // 4,
                        output_tokens=0,
                    ),
                    2,
                ),
            }

        # By project
        by_project_out: Dict[str, Any] = {}
        for name, p in self.by_project.items():
            by_project_out[name] = {
                "sessions": len(p["sessions"]),
                "tokens": p["tokens"],
                "cost_usd": round(p["cost"], 2),
            }

        # Outliers (descending)
        outliers_sorted = sorted(self.outliers, key=lambda t: -t[0])
        outliers_out = [
            {
                "tool": rec["tool"],
                "size": size,
                "project": rec.get("project"),
                "ts": rec.get("ts"),
            }
            for size, _seq, rec in outliers_sorted
        ]

        # Counterfactuals
        cfs: List[Dict[str, Any]] = []
        for cap in (8000, 5000, 3000):
            cfs.append(
                counterfactual.mcp_output_cap(
                    self.tool_results_for_cf, self.reactions, cap
                )
            )
        for cap in (8000, 6000, 4000):
            cfs.append(counterfactual.max_output_cap(self.assistant_usages_for_cf, cap))
        cfs.append(counterfactual.read_once_savings(self._dup_read_bytes, self._dup_read_count))
        cfs.append(counterfactual.log_grep_savings(self._log_read_over_ceiling))
        cfs.append(counterfactual.autocompact_advisory(self.compact_count, 70))

        # Recommendations: pick MCP cap with <5 losses and highest dollars;
        # max-output cap with 0 losses and highest dollars.
        recs: List[Dict[str, Any]] = []
        best_mcp = max(
            (c for c in cfs if c["setting"] == "MAX_MCP_OUTPUT_TOKENS" and c["losses"] < 5),
            key=lambda c: c["dollars_saved"],
            default=None,
        )
        if best_mcp and best_mcp["dollars_saved"] > 0:
            recs.append({
                "setting": "MAX_MCP_OUTPUT_TOKENS",
                "value": best_mcp["value"],
                "reason": f"Set MAX_MCP_OUTPUT_TOKENS={best_mcp['value']} (saves ~${best_mcp['dollars_saved']:.2f}, {best_mcp['losses']} losses)",
                "confidence": "high" if best_mcp["losses"] == 0 else "medium",
            })
        best_out = max(
            (c for c in cfs if c["setting"] == "CLAUDE_CODE_MAX_OUTPUT_TOKENS" and c["losses"] == 0),
            key=lambda c: c["dollars_saved"],
            default=None,
        )
        if best_out and best_out["dollars_saved"] > 0:
            recs.append({
                "setting": "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
                "value": best_out["value"],
                "reason": f"Set CLAUDE_CODE_MAX_OUTPUT_TOKENS={best_out['value']} (saves ~${best_out['dollars_saved']:.2f}, zero losses)",
                "confidence": "high",
            })

        # Strip internal keys from reactions before emit
        for r in self.reactions.values():
            r.pop("_last_tool", None)

        insights = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "tokenomy_version": __version__,
            "period": {
                "start": (since.date().isoformat() if since else None),
                "end": until.date().isoformat(),
                "days": (until - since).days if since else None,
                "sessions": len(self.sessions_seen),
            },
            "totals": {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cache_read_tokens": self.cache_read_tokens,
                "cache_creation_tokens": self.cache_creation_tokens,
                "cost_usd": round(self.cost_usd, 2),
            },
            "by_tool": by_tool_out,
            "by_project": by_project_out,
            "by_hour": {str(h): self.by_hour.get(h, 0) for h in range(24)},
            "counterfactuals": cfs,
            "outliers": outliers_out,
            "duplicate_reads": {
                "count": self._dup_read_count,
                "bytes": self._dup_read_bytes,
                "dollars": round(
                    pricing.cost_for_usage(
                        pricing.DEFAULT_PRICING_KEY,
                        input_tokens=self._dup_read_bytes // 4,
                        output_tokens=0,
                    ),
                    2,
                ),
            },
            "recommendations": recs,
            "compact_events": self.compact_count,
        }
        return insights


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_ts(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        # Handle trailing Z
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _norm_path(path: str) -> str:
    if not path:
        return ""
    p = path.replace("\\", "/").lower()
    # Strip trailing slash
    while p.endswith("/"):
        p = p[:-1]
    return p


def _looks_like_log_path_hist(session_id: str, keys: Dict[tuple, int]) -> bool:
    # Cheap heuristic — check whether any recent Read key in this session matches log hints.
    for (fp, _o, _l) in keys.keys():
        if any(h in fp for h in LOG_PATH_HINTS):
            return True
    return False


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tokenomy-analyze")
    ap.add_argument("--days", type=int, default=30, help="scan this many days back (default 30)")
    ap.add_argument("--project", help="limit to a single project directory (name under ~/.claude/projects)")
    ap.add_argument("--root", default=DEFAULT_ROOT, help="root dir (default ~/.claude/projects)")
    ap.add_argument("--json-out", default=DEFAULT_OUT)
    ap.add_argument("--pricing-file", help="JSON file overriding the pricing table")
    ap.add_argument("--no-report", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    # Pricing freshness check
    age = pricing.pricing_age_months()
    if age > 3:
        log.warning("pricing table is %d months old (PRICING_UPDATED_AT=%s)", age, pricing.PRICING_UPDATED_AT)

    table = pricing.PRICING
    if args.pricing_file:
        table = pricing.load_pricing_file(args.pricing_file)

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=args.days) if args.days > 0 else None

    root = args.root
    if args.project:
        root = os.path.join(args.root, args.project)
    if not os.path.isdir(root):
        log.error("root not found: %s", root)
        return 2

    agg = Aggregator(pricing_table=table, since=since)
    files = 0
    events = 0
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            if not name.endswith(".jsonl"):
                continue
            files += 1
            for ev in iter_session_file(os.path.join(dirpath, name)):
                events += 1
                agg.process_event(ev)

    insights = agg.finalize(since=since, until=now)
    insights["output_path"] = args.json_out
    insights["files_scanned"] = files
    insights["events_processed"] = events

    os.makedirs(os.path.dirname(args.json_out), exist_ok=True)
    with open(args.json_out, "w", encoding="utf-8") as f:
        json.dump(insights, f, indent=2, ensure_ascii=False, default=str)

    if not args.no_report:
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # py3.7+
        except Exception:
            pass
        print(report.render(insights))

    return 0


if __name__ == "__main__":
    sys.exit(main())
