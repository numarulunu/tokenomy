#!/usr/bin/env python3
"""
Tokenomy native statusline — minimalist ccusage-inspired.

Output format:
  🤖 <Model> | 💰 €<session> session / €<today> today / €<block> block (Xh left) | 🔥 €<rate>/hr | 🧠 <Nk> (<pct>%)

Design:
  - Reads session JSON from stdin (Claude Code statusLine contract).
  - Walks all transcripts under ~/.claude/projects/**/*.jsonl.
  - Computes per-message cost from token usage × pricing table (pricing.json).
  - Uses embedded costUSD if present (cost-source: auto).
  - Today = sum of message costs with timestamp in local today.
  - Block = 5-hour rolling session block per ccusage spec:
      * Blocks are aligned to the hour.
      * A block starts at floor-hour(first message) and lasts 5h.
      * New message ≥5h after block start OR ≥5h gap → starts new block.
      * "Current" block = the block containing now (if still active).
  - Burn rate = current block cost ÷ hours elapsed in block.
  - Context = most recent assistant turn's total input tokens (input + cache_read + cache_creation).
  - Fail open: any error prints minimal fallback.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Optional tokenomy currency override. Imported defensively so a missing
# tuner package (e.g. statusline run outside the plugin) still works.
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tuner.currency import load_currency  # type: ignore
except Exception:  # pragma: no cover - fail-open
    def load_currency() -> dict:
        return {"code": "USD", "symbol": "$", "rate_to_usd": 1.0}

# --- stdout utf-8 (Windows cp1252 chokes on emoji) ---
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

HERE = Path(__file__).resolve().parent
PRICING_PATH = HERE / "pricing.json"
PROJECTS_DIR = Path.home() / ".claude" / "projects"
BLOCK_HOURS = 5
CTX_LIMIT_DEFAULT = 200_000
CTX_LIMIT_1M = 1_000_000

MODEL_DISPLAY = {
    "claude-opus-4-6": "Opus 4.6",
    "claude-sonnet-4-6": "Sonnet 4.6",
    "claude-haiku-4-5": "Haiku 4.5",
    "claude-opus-4": "Opus 4",
    "claude-sonnet-4": "Sonnet 4",
    "claude-3-5-sonnet": "Sonnet 3.5",
    "claude-3-5-haiku": "Haiku 3.5",
}


# ---------- pricing ----------

def load_pricing() -> dict:
    try:
        return json.loads(PRICING_PATH.read_text(encoding="utf-8")).get("models", {})
    except (OSError, json.JSONDecodeError):
        return {}


def pricing_for(model_id: str, pricing: dict, over_200k: bool) -> dict | None:
    if not model_id:
        return None
    key = model_id.lower()
    # Longest-substring match so "claude-opus-4-6" beats "claude-opus-4"
    best = None
    for k in pricing:
        if k in key and (best is None or len(k) > len(best)):
            best = k
    if best is None:
        return None
    entry = pricing[best]
    if over_200k and "tier_1m" in entry:
        return entry["tier_1m"]
    return entry


def cost_from_usage(usage: dict, model_id: str, pricing: dict) -> float:
    if not usage:
        return 0.0
    in_tok = int(usage.get("input_tokens", 0) or 0)
    out_tok = int(usage.get("output_tokens", 0) or 0)
    cc5 = int(usage.get("cache_creation_input_tokens", 0) or 0)
    cr = int(usage.get("cache_read_input_tokens", 0) or 0)
    # 1h cache tokens live under cache_creation.ephemeral_1h_input_tokens in some APIs;
    # Claude Code transcripts typically only have the flat field. Treat cc as 5m.
    total_ctx = in_tok + cc5 + cr
    over_200k = total_ctx > 200_000
    p = pricing_for(model_id, pricing, over_200k)
    if not p:
        return 0.0
    return (
        in_tok * p.get("input", 0) / 1_000_000
        + out_tok * p.get("output", 0) / 1_000_000
        + cc5 * p.get("cache_write_5m", 0) / 1_000_000
        + cr * p.get("cache_read", 0) / 1_000_000
    )


# ---------- transcript iteration ----------

def iter_transcript_messages(path: Path, seen: set):
    """
    Yield (timestamp_dt, model_id, usage_dict, embedded_cost_or_none) per assistant turn.
    Dedupes across transcripts via (message_id, request_id). Mutates `seen`.
    """
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = rec.get("message") if isinstance(rec.get("message"), dict) else None
                usage = None
                model = ""
                msg_id = ""
                if msg:
                    usage = msg.get("usage")
                    model = msg.get("model", "") or rec.get("model", "")
                    msg_id = msg.get("id", "") or ""
                if not usage:
                    usage = rec.get("usage")
                    model = model or rec.get("model", "")
                if not usage:
                    continue
                req_id = rec.get("requestId") or rec.get("request_id") or ""
                # Dedupe: ccusage keys on (message.id + requestId). Fall back to
                # (uuid) when message.id missing.
                dedupe_key = (
                    f"{msg_id}:{req_id}" if msg_id or req_id else rec.get("uuid", "")
                )
                if dedupe_key:
                    if dedupe_key in seen:
                        continue
                    seen.add(dedupe_key)
                ts_raw = rec.get("timestamp") or rec.get("time") or ""
                ts = parse_ts(ts_raw)
                if ts is None:
                    continue
                embedded = rec.get("costUSD")
                if embedded is None and msg:
                    embedded = msg.get("costUSD")
                yield ts, model, usage, embedded
    except OSError:
        return


def parse_ts(s: str):
    if not s:
        return None
    try:
        # Accept "2026-04-07T13:45:00.000Z" and "2026-04-07T13:45:00+00:00"
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def all_transcripts():
    if not PROJECTS_DIR.is_dir():
        return []
    return list(PROJECTS_DIR.rglob("*.jsonl"))


# ---------- aggregators ----------

def today_cost(pricing: dict) -> float:
    # Local calendar day, matches `ccusage daily` report exactly.
    # Note: `ccusage statusline` shows a slightly higher figure (possibly rolling 24h
    # or a different windowing heuristic); we match the daily report which is the
    # documented aggregate.
    now_local = datetime.now().astimezone()
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    total = 0.0
    seen: set = set()
    for p in all_transcripts():
        for ts, model, usage, embedded in iter_transcript_messages(p, seen):
            ts_local = ts.astimezone(now_local.tzinfo)
            if ts_local < day_start or ts_local >= day_end:
                continue
            total += embedded if isinstance(embedded, (int, float)) else cost_from_usage(usage, model, pricing)
    return total


def collect_recent_messages(since_hours: int, pricing: dict):
    """Return list of (ts_utc, cost) for messages in the last `since_hours`, sorted ascending."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    items = []
    seen: set = set()
    for p in all_transcripts():
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                continue
        except OSError:
            continue
        for ts, model, usage, embedded in iter_transcript_messages(p, seen):
            ts_utc = ts.astimezone(timezone.utc)
            if ts_utc < cutoff:
                continue
            c = embedded if isinstance(embedded, (int, float)) else cost_from_usage(usage, model, pricing)
            items.append((ts_utc, c))
    items.sort(key=lambda x: x[0])
    return items


def current_block(pricing: dict):
    """
    Compute current 5h block per ccusage spec.
    Returns (block_start_utc, block_cost, time_left_seconds) or (None, 0.0, 0).
    """
    # Pull a generous window so we catch the block start even if it began >5h ago
    # (ccusage aligns to hour so we look back up to ~10h to be safe).
    msgs = collect_recent_messages(BLOCK_HOURS * 2, pricing)
    if not msgs:
        return None, 0.0, 0

    # Walk messages forward, grouping into blocks.
    block_start = None
    block_cost = 0.0
    last_ts = None
    gap = timedelta(hours=BLOCK_HOURS)
    dur = timedelta(hours=BLOCK_HOURS)

    def floor_hour(dt: datetime) -> datetime:
        return dt.replace(minute=0, second=0, microsecond=0)

    for ts, c in msgs:
        new_block = (
            block_start is None
            or ts - block_start >= dur
            or (last_ts is not None and ts - last_ts >= gap)
        )
        if new_block:
            block_start = floor_hour(ts)
            block_cost = 0.0
        block_cost += c
        last_ts = ts

    if block_start is None:
        return None, 0.0, 0
    now = datetime.now(timezone.utc)
    elapsed = now - block_start
    if elapsed >= dur:
        # Block expired, no current block
        return None, 0.0, 0
    time_left = int((dur - elapsed).total_seconds())
    return block_start, block_cost, time_left


def burn_rate(block_start, block_cost) -> float:
    if not block_start or block_cost <= 0:
        return 0.0
    elapsed = (datetime.now(timezone.utc) - block_start).total_seconds() / 3600
    if elapsed <= 0:
        return 0.0
    return block_cost / elapsed


# ---------- context ----------

def last_context_tokens(transcript_path: str) -> int:
    if not transcript_path or not os.path.exists(transcript_path):
        return 0
    try:
        with open(transcript_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 262_144))
            tail = f.read().decode("utf-8", errors="ignore")
    except OSError:
        return 0
    for line in reversed(tail.splitlines()):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = rec.get("message") if isinstance(rec.get("message"), dict) else None
        usage = (msg or {}).get("usage") or rec.get("usage")
        if not usage:
            continue
        total = (
            int(usage.get("input_tokens", 0) or 0)
            + int(usage.get("cache_read_input_tokens", 0) or 0)
            + int(usage.get("cache_creation_input_tokens", 0) or 0)
        )
        if total:
            return total
    return 0


def context_limit(model_id: str) -> int:
    return CTX_LIMIT_1M if model_id and "1m" in model_id.lower() else CTX_LIMIT_DEFAULT


# ---------- formatting ----------

_CURRENCY = {"code": "USD", "symbol": "$", "rate_to_usd": 1.0}


def fmt_money(v: float) -> str:
    rate = float(_CURRENCY.get("rate_to_usd", 1.0))
    symbol = str(_CURRENCY.get("symbol", "$"))
    amt = v * rate
    # Minimalist: one decimal under 10 (€1.4, €0.8), integer at/above 10 (€92).
    # Integer-only stripped too much precision from small values like burn rate.
    if amt < 10:
        return f"{symbol}{amt:.1f}"
    return f"{symbol}{int(round(amt))}"


def fmt_time_left(seconds: int) -> str:
    if seconds <= 0:
        return "0m"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    # Minimalist: prefer single-unit (5h or 30m), never "3h 7m".
    if h >= 1:
        return f"{h}h"
    return f"{m}m"


def fmt_tokens(n: int) -> str:
    # Minimalist: 180,349 → "180k", 1,250,000 → "1.2M".
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n // 1_000}k"
    return str(n)


def model_display(model_id: str, fallback: str) -> str:
    if not model_id:
        return fallback or "Claude"
    key = model_id.lower()
    best = None
    for k, v in MODEL_DISPLAY.items():
        if k in key and (best is None or len(k) > len(best[0])):
            best = (k, v)
    return best[1] if best else (fallback or model_id)


def render(payload: dict, pricing: dict) -> str:
    model = payload.get("model") or {}
    model_id = model.get("id", "")
    name = model_display(model_id, model.get("display_name", ""))

    session_cost = float((payload.get("cost") or {}).get("total_cost_usd") or 0)
    today = today_cost(pricing)
    block_start, block_cost, time_left = current_block(pricing)
    rate = burn_rate(block_start, block_cost)

    ctx_tokens = last_context_tokens(payload.get("transcript_path", ""))
    limit = context_limit(model_id)
    pct = int(round(100 * ctx_tokens / limit)) if limit else 0
    ctx_str = f"{fmt_tokens(ctx_tokens)} ({pct}%)" if ctx_tokens else "N/A"

    cost_section = (
        f"{fmt_money(session_cost)} session"
        f" / {fmt_money(today)} today"
        f" / {fmt_money(block_cost)} block ({fmt_time_left(time_left)})"
    )
    burn_section = f"{fmt_money(rate)}/hr" if rate > 0 else f"{fmt_money(0)}/hr"

    return (
        f"\U0001F916 {name} | "
        f"\U0001F4B0 {cost_section} | "
        f"\U0001F525 {burn_section} | "
        f"\U0001F9E0 {ctx_str}"
    )


# ---------- main ----------

def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.stdout.write("Tokenomy")
        return 0
    pricing = load_pricing()
    global _CURRENCY
    try:
        _CURRENCY = load_currency()
    except Exception:
        pass  # fail-open to USD
    sys.stdout.write(render(payload, pricing))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.stdout.write("Tokenomy")
        sys.exit(0)
