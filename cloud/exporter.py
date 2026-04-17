"""Walks Claude + Codex corpora and streams normalized event rows into SQLite.

Replaces the old flat-JSON analyzer output as the dashboard's source of truth.
Each (device_id, dedupe_key) pair is unique, so re-runs are idempotent — the
DB never double-counts regardless of how often this is invoked.

Watermarks in ~/.claude/tokenomy/watermarks.json cut the walk by mtime: files
older than the last successful run are skipped. Dedupe still catches the
edge case where a stale file was partially edited.

Cost calculation:
  Claude: uses analyzer.pricing — *includes* cache_read in cost_usd. This
          differs from the legacy insights.json which zeroed cache_read out.
          Result: dashboard totals will read slightly higher, which is the
          actual billable figure per Anthropic's pricing table.
  Codex:  cost_usd=0 for now. OpenAI pricing for GPT-5 family isn't in any
          shared table yet; adding it requires a separate pricing file.

Run with: python -m cloud.exporter [--claude-only|--codex-only] [--full]
  --full: ignore watermarks and re-walk everything (dedupe still applies).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

# Make the tokenomy package importable when run as `python -m cloud.exporter`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cloud import db as cloud_db  # noqa: E402

log = logging.getLogger("cloud.exporter")

TOKENOMY_DIR = Path.home() / ".claude" / "tokenomy"
DEVICE_FILE = TOKENOMY_DIR / "device.json"
WATERMARK_FILE = TOKENOMY_DIR / "watermarks.json"
LOG_FILE = TOKENOMY_DIR / "_exporter.log"

CLAUDE_ROOT = Path.home() / ".claude" / "projects"
CODEX_ROOT = Path.home() / ".codex" / "sessions"

BATCH_SIZE = 500
# Watermark buffer: the corpus may have files whose mtime is newer than their
# last event (e.g. sync copies). Rewind the cutoff by this delta so we don't
# miss events on the edge. UNIQUE-constraint dedupe absorbs the overlap.
WATERMARK_BUFFER_SEC = 15 * 60


def _log(msg: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"{stamp} {msg}\n")
    except OSError:
        pass


# ─────────────────── device id ───────────────────

def load_device_id(label: str | None = None) -> tuple[str, str | None]:
    """Return (device_id, label). Creates a new uuid on first call."""
    TOKENOMY_DIR.mkdir(parents=True, exist_ok=True)
    if DEVICE_FILE.exists():
        try:
            data = json.loads(DEVICE_FILE.read_text(encoding="utf-8"))
            return data["device_id"], data.get("label")
        except (OSError, json.JSONDecodeError, KeyError):
            pass
    dev_id = str(uuid.uuid4())
    payload = {"device_id": dev_id, "label": label}
    DEVICE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return dev_id, label


# ─────────────────── watermarks ───────────────────

def load_watermarks() -> dict[str, str]:
    if not WATERMARK_FILE.exists():
        return {}
    try:
        data = json.loads(WATERMARK_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_watermarks(marks: dict[str, str]) -> None:
    WATERMARK_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = WATERMARK_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(marks, indent=2), encoding="utf-8")
    os.replace(tmp, WATERMARK_FILE)


def _watermark_epoch(marks: dict[str, str], platform: str) -> float:
    raw = marks.get(platform, "")
    if not raw:
        return 0.0
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.timestamp() - WATERMARK_BUFFER_SEC
    except ValueError:
        return 0.0


# ─────────────────── Claude extraction ───────────────────

def _iter_claude_events(path: Path, device_id: str) -> Iterator[dict]:
    # Lazy-import so `python -m cloud.db` doesn't pull analyzer in.
    from analyzer import pricing as pricing_mod
    from analyzer.extractors import decode_project_path, iter_session_file

    session_id = path.stem
    project_encoded = path.parent.name
    decoded = decode_project_path(project_encoded)
    project = decoded if decoded else project_encoded

    # Materialize to build tool_use_id -> tool_name map. Single-file size is
    # small (<a few MB); memory pressure is negligible vs the SQLite insert.
    events_raw = list(iter_session_file(str(path)))
    tool_names: dict[str, str] = {}
    for e in events_raw:
        if e.kind == "tool_use" and e.tool_use_id and e.tool_name:
            tool_names[e.tool_use_id] = e.tool_name

    for e in events_raw:
        if e.kind == "assistant_usage":
            model = e.model or pricing_mod.DEFAULT_PRICING_KEY
            cost = pricing_mod.cost_for_usage(
                model,
                e.input_tokens,
                e.output_tokens,
                cache_creation_tokens=e.cache_creation_tokens,
                cache_read_tokens=e.cache_read_tokens,
            )
            dedupe = (
                f"claude:t:{session_id}:{e.ts}:"
                f"{e.input_tokens}:{e.output_tokens}:{e.cache_read_tokens}"
            )
            yield {
                "device_id": device_id,
                "platform": "claude",
                "kind": "turn",
                "session_id": e.session_id or session_id,
                "ts_utc": e.ts,
                "model": e.model,
                "project": project,
                "input_tokens": e.input_tokens,
                "output_tokens": e.output_tokens,
                "cache_read_tokens": e.cache_read_tokens,
                "cache_creation_tokens": e.cache_creation_tokens,
                "cost_usd": round(cost, 6),
                "tool_name": None,
                "tool_bytes": None,
                "tool_is_error": None,
                "tool_truncated": None,
                "dedupe_key": dedupe,
            }
        elif e.kind == "tool_result" and e.tool_use_id:
            dedupe = f"claude:r:{session_id}:{e.tool_use_id}"
            yield {
                "device_id": device_id,
                "platform": "claude",
                "kind": "tool",
                "session_id": e.session_id or session_id,
                "ts_utc": e.ts,
                "model": None,
                "project": project,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "cost_usd": 0.0,
                "tool_name": tool_names.get(e.tool_use_id),
                "tool_bytes": int(e.response_size_bytes or 0),
                "tool_is_error": 1 if e.is_error else 0,
                "tool_truncated": 1 if e.truncated else 0,
                "dedupe_key": dedupe,
            }


# ─────────────────── Codex extraction ───────────────────

def _iter_codex_events(path: Path, device_id: str) -> Iterator[dict]:
    session_id = path.stem
    ord_counter = 0
    try:
        fh = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = rec.get("payload") if isinstance(rec, dict) else None
            if not isinstance(payload, dict) or payload.get("type") != "token_count":
                continue
            info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
            # last_token_usage is the delta for this turn; total_token_usage is
            # cumulative-across-session and would double-count if summed.
            last = info.get("last_token_usage") if isinstance(info, dict) else None
            if not isinstance(last, dict):
                continue
            ts = rec.get("timestamp")
            in_tok = int(last.get("input_tokens") or 0)
            out_tok = int(last.get("output_tokens") or 0)
            cache_read = int(last.get("cached_input_tokens") or 0)
            reasoning = int(last.get("reasoning_output_tokens") or 0)
            if in_tok == 0 and out_tok == 0 and cache_read == 0 and reasoning == 0:
                continue
            ord_counter += 1
            dedupe = f"codex:t:{session_id}:{ord_counter}"
            yield {
                "device_id": device_id,
                "platform": "codex",
                "kind": "turn",
                "session_id": session_id,
                "ts_utc": ts,
                "model": None,
                "project": None,
                "input_tokens": in_tok,
                # Reasoning tokens are billed like output on GPT-5. Fold them
                # in so the single column reflects true billable output.
                "output_tokens": out_tok + reasoning,
                "cache_read_tokens": cache_read,
                "cache_creation_tokens": 0,
                # Cost left 0 until a Codex pricing table lands (out of scope
                # for Phase 1 — dashboard will show tokens, not USD, for Codex).
                "cost_usd": 0.0,
                "tool_name": None,
                "tool_bytes": None,
                "tool_is_error": None,
                "tool_truncated": None,
                "dedupe_key": dedupe,
            }


# ─────────────────── walk + batch upsert ───────────────────

def _batched(it: Iterable[dict], n: int) -> Iterator[list[dict]]:
    buf: list[dict] = []
    for item in it:
        buf.append(item)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf


def _walk_platform(
    root: Path,
    cutoff_epoch: float,
    extract: Any,
    device_id: str,
) -> Iterator[dict]:
    if not root.exists():
        return
    for path in root.rglob("*.jsonl"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if cutoff_epoch and mtime < cutoff_epoch:
            continue
        try:
            yield from extract(path, device_id)
        except Exception as exc:  # pragma: no cover — per-file resilience
            _log(f"extract failed {path}: {exc}")
            continue


def export(
    device_id: str,
    label: str | None,
    *,
    claude: bool = True,
    codex: bool = True,
    full: bool = False,
) -> dict[str, int]:
    # Always preserve the other platform's watermark — `--full` only forces
    # a cutoff-less walk for the platforms actually being run this invocation.
    marks = load_watermarks()
    now_iso = datetime.now(timezone.utc).isoformat()

    conn = cloud_db.connect()
    try:
        cloud_db.init_schema(conn)
        cloud_db.upsert_device(conn, device_id, label=label)

        totals = {"claude_inserted": 0, "codex_inserted": 0, "claude_seen": 0, "codex_seen": 0}

        if claude:
            cutoff = _watermark_epoch(marks, "claude") if not full else 0.0
            stream = _walk_platform(CLAUDE_ROOT, cutoff, _iter_claude_events, device_id)
            for batch in _batched(stream, BATCH_SIZE):
                totals["claude_seen"] += len(batch)
                with cloud_db.tx(conn):
                    inserted = cloud_db.bulk_upsert_events(conn, batch)
                totals["claude_inserted"] += inserted
            marks["claude"] = now_iso

        if codex:
            cutoff = _watermark_epoch(marks, "codex") if not full else 0.0
            stream = _walk_platform(CODEX_ROOT, cutoff, _iter_codex_events, device_id)
            for batch in _batched(stream, BATCH_SIZE):
                totals["codex_seen"] += len(batch)
                with cloud_db.tx(conn):
                    inserted = cloud_db.bulk_upsert_events(conn, batch)
                totals["codex_inserted"] += inserted
            marks["codex"] = now_iso

        save_watermarks(marks)
        return totals
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tokenomy-cloud-exporter")
    ap.add_argument("--claude-only", action="store_true")
    ap.add_argument("--codex-only", action="store_true")
    ap.add_argument("--full", action="store_true", help="ignore watermarks; re-walk all files")
    ap.add_argument("--label", default=None, help="human-readable device label (saved on first run)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    dev_id, existing_label = load_device_id(args.label)
    label = args.label or existing_label

    do_claude = not args.codex_only
    do_codex = not args.claude_only

    t0 = time.time()
    _log(f"start device={dev_id} claude={do_claude} codex={do_codex} full={args.full}")
    try:
        totals = export(dev_id, label, claude=do_claude, codex=do_codex, full=args.full)
    except Exception as exc:
        _log(f"FAIL: {exc}")
        print(f"[exporter] failed: {exc}", file=sys.stderr)
        return 1
    dt = time.time() - t0
    _log(f"ok {totals} elapsed={dt:.1f}s")
    print(
        f"[exporter] device={dev_id} "
        f"claude seen={totals['claude_seen']} inserted={totals['claude_inserted']} | "
        f"codex seen={totals['codex_seen']} inserted={totals['codex_inserted']} | "
        f"elapsed={dt:.1f}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
