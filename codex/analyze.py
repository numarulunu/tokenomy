"""Analyze Codex session token_count events."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

VERSION = "1.0.1"


def tokenomy_home() -> Path:
    return Path(os.environ.get("TOKENOMY_CODEX_HOME", Path.home() / ".codex" / "tokenomy"))


def log(message: str) -> None:
    home = tokenomy_home()
    try:
        home.mkdir(parents=True, exist_ok=True)
        with (home / "_codex-analyze.log").open("a", encoding="utf-8") as handle:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            handle.write(f"{stamp} {message}\n")
    except Exception:
        pass


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def iter_session_files(root: Path, cutoff: datetime | None) -> list[Path]:
    if not root.exists():
        return []
    files: list[Path] = []
    for path in root.rglob("*.jsonl"):
        try:
            if cutoff is not None:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                if mtime < cutoff:
                    continue
            files.append(path)
        except OSError:
            continue
    return sorted(files)


def latest_token_count(path: Path) -> tuple[datetime | None, dict[str, Any] | None]:
    latest_time: datetime | None = None
    latest_payload: dict[str, Any] | None = None
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict) or payload.get("type") != "token_count":
                continue
            latest_time = parse_timestamp(event.get("timestamp"))
            latest_payload = payload
    return latest_time, latest_payload


def summarize(root: Path, days: int) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days > 0 else None
    sessions = []
    totals = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
        "total_tokens": 0,
    }

    for path in iter_session_files(root, cutoff):
        timestamp, payload = latest_token_count(path)
        if payload is None:
            continue
        info = payload.get("info") or {}
        total_usage = info.get("total_token_usage") or {}
        session_total = int(total_usage.get("total_tokens") or 0)
        for key in totals:
            try:
                totals[key] += int(total_usage.get(key) or 0)
            except Exception:
                pass
        sessions.append(
            {
                "path": str(path),
                "timestamp": timestamp.isoformat() if timestamp else None,
                "total_tokens": session_total,
                "input_tokens": int(total_usage.get("input_tokens") or 0),
                "cached_input_tokens": int(total_usage.get("cached_input_tokens") or 0),
                "output_tokens": int(total_usage.get("output_tokens") or 0),
                "reasoning_output_tokens": int(total_usage.get("reasoning_output_tokens") or 0),
            }
        )

    sessions.sort(key=lambda item: item["total_tokens"], reverse=True)
    return {
        "version": VERSION,
        "root": str(root),
        "days": days,
        "session_count": len(sessions),
        "totals": totals,
        "top_sessions": sessions[:20],
    }


def compact(number: int) -> str:
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if number >= 1_000:
        return f"{number / 1_000:.1f}k"
    return str(number)


def render_report(data: dict[str, Any]) -> str:
    totals = data["totals"]
    lines = [
        "Tokenomy Codex report",
        f"Sessions: {data['session_count']} in last {data['days']} days",
        f"Total tokens: {compact(totals['total_tokens'])}",
        f"Input tokens: {compact(totals['input_tokens'])}",
        f"Cached input: {compact(totals['cached_input_tokens'])}",
        f"Output tokens: {compact(totals['output_tokens'])}",
        f"Reasoning output: {compact(totals['reasoning_output_tokens'])}",
    ]
    if data["top_sessions"]:
        top = data["top_sessions"][0]
        lines.append(f"Largest session: {compact(top['total_tokens'])} tokens at {top['path']}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Analyze Codex session token usage.")
    parser.add_argument("--days", type=int, default=30, help="number of days to scan, or 0 for all")
    parser.add_argument("--root", default=str(Path.home() / ".codex" / "sessions"), help="Codex sessions root")
    parser.add_argument("--json-out", default=str(tokenomy_home() / "codex-insights.json"), help="JSON output path")
    parser.add_argument("--no-report", action="store_true", help="write JSON only")
    args = parser.parse_args(argv[1:])

    try:
        data = summarize(Path(args.root).expanduser(), args.days)
        out = Path(args.json_out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        log(f"analyzed sessions={data['session_count']} root={args.root}")
        if not args.no_report:
            print(render_report(data))
            print(f"Full insights written to: {out}")
        return 0
    except Exception as exc:
        log(f"analyze failed: {exc}")
        print(f"Tokenomy could not analyze Codex sessions: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
