#!/usr/bin/env python3
"""Tokenomy fetch-audit hook.

Fires on Claude Code PreToolUse and PostToolUse. Appends one JSONL record per
event to ~/.claude/tokenomy/fetch-log.jsonl. Never blocks the parent call —
any failure is swallowed and the hook exits 0.

Record schema:
    {ts, session_id, tool_name, input_hash, phase, output_bytes, duration_ms}

- phase="pre"   → output_bytes/duration_ms are null.
- phase="post"  → measurements populated from the PostToolUse payload.

input_hash is the first 16 hex chars of sha256(json-canonical(tool_input)), so
the analyzer can pair pre/post entries within a session without storing the
raw inputs (which can contain secrets).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone


LOG_PATH = os.path.expanduser("~/.claude/tokenomy/fetch-log.jsonl")
MAX_LOG_BYTES = 50 * 1024 * 1024  # 50MB — rotate past this to keep disk bounded
MAX_ROTATIONS = 3  # keep .1 / .2 / .3 (oldest dropped)


def _rotate_if_large(path: str) -> None:
    """If `path` exceeds MAX_LOG_BYTES, shift .N → .(N+1) and start fresh.
    Silently no-ops on any OSError so the hook never blocks its tool call.
    """
    try:
        if os.path.getsize(path) < MAX_LOG_BYTES:
            return
    except OSError:
        return
    # Drop the oldest, then cascade: .2 → .3, .1 → .2, main → .1
    try:
        oldest = f"{path}.{MAX_ROTATIONS}"
        if os.path.exists(oldest):
            os.remove(oldest)
    except OSError:
        pass
    for i in range(MAX_ROTATIONS - 1, 0, -1):
        src = f"{path}.{i}"
        dst = f"{path}.{i + 1}"
        try:
            if os.path.exists(src):
                os.replace(src, dst)
        except OSError:
            pass
    try:
        os.replace(path, f"{path}.1")
    except OSError:
        pass


def _hash_input(tool_input: object) -> str:
    try:
        s = json.dumps(tool_input, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        s = str(tool_input)
    return hashlib.sha256(s.encode("utf-8", "replace")).hexdigest()[:16]


def _output_bytes(tool_response: object) -> int:
    if tool_response is None:
        return 0
    try:
        if isinstance(tool_response, (bytes, bytearray)):
            return len(tool_response)
        if isinstance(tool_response, str):
            return len(tool_response.encode("utf-8", "replace"))
        return len(json.dumps(tool_response, ensure_ascii=False, default=str).encode("utf-8", "replace"))
    except Exception:
        return 0


def _detect_phase(payload: dict) -> str:
    # Claude Code sets hook_event_name; fall back to payload shape if absent.
    ev = (payload.get("hook_event_name") or payload.get("event") or "").strip()
    if "PostToolUse" in ev:
        return "post"
    if "PreToolUse" in ev:
        return "pre"
    # Shape fallback: PostToolUse payloads carry tool_response.
    if "tool_response" in payload:
        return "post"
    return "pre"


def main() -> int:
    try:
        raw = sys.stdin.read()
    except Exception:
        return 0
    if not raw:
        return 0

    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return 0
    except Exception:
        return 0

    try:
        phase = _detect_phase(payload)
        tool_name = payload.get("tool_name") or ""
        tool_input = payload.get("tool_input")
        session_id = payload.get("session_id") or ""

        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "tool_name": tool_name,
            "input_hash": _hash_input(tool_input) if tool_input is not None else "",
            "phase": phase,
            "output_bytes": _output_bytes(payload.get("tool_response")) if phase == "post" else None,
            "duration_ms": int(payload.get("duration_ms") or 0) if phase == "post" else None,
        }

        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        _rotate_if_large(LOG_PATH)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # fail-open: never break the tool call
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
