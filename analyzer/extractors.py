"""Streaming JSONL extractor for Claude Code session files.

Session files live at `~/.claude/projects/<project-hash>/<session-id>.jsonl`.
Each line is a JSON object. We never load a whole file into memory — we
iterate line-by-line and yield normalized events.

Event kinds:
- `assistant_usage`: token usage + model + session context
- `tool_use`:        tool call emitted by assistant
- `tool_result`:     tool result (size, truncation flag)
- `compact`:         autocompact marker
- `fetch_call`:      paired tool invocation from the Tokenomy fetch-audit hook
                     log (carries wall-clock duration + precise output bytes)
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator, List, Optional

log = logging.getLogger(__name__)


@dataclass
class Event:
    kind: str                       # assistant_usage | tool_use | tool_result | compact | fetch_call
    ts: Optional[str] = None
    session_id: Optional[str] = None
    project: Optional[str] = None
    # assistant_usage
    model: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    # tool_use
    tool_name: Optional[str] = None
    tool_use_id: Optional[str] = None
    input_summary: dict = field(default_factory=dict)
    # tool_result
    response_size_bytes: int = 0
    truncated: bool = False   # real mid-response truncation (marker matched)
    is_error: bool = False    # tool_result.is_error — benign 99.5% of the time
    # unended assistant text (used for counterfactual loss heuristic)
    text_tail: Optional[str] = None
    # fetch_call (from Tokenomy fetch-audit hook log)
    duration_ms: int = 0


_TRUNCATION_MARKERS = ("Response truncated", "[truncated]", "…(truncated)")


# ─────────────────── project-path decoding ───────────────────


def _probe_path(current: str, segments: List[str]) -> Optional[str]:
    """Recursive filesystem probe. Matches real directory names against the
    canonical form (spaces collapsed to `-`) of a prefix of `segments`."""
    if not segments:
        return current
    try:
        entries = [e for e in os.listdir(current)
                   if os.path.isdir(os.path.join(current, e))]
    except OSError:
        return None
    # Longer matches first: if both "Foo" and "Foo-Bar" exist and the target
    # is "Foo-Bar-Baz", we want to consume two segments via "Foo-Bar", not one.
    entries.sort(key=lambda e: len(e.replace(" ", "-")), reverse=True)
    target = "-".join(segments).lower()
    for entry in entries:
        canon = entry.replace(" ", "-").lower()
        if canon == target:
            return os.path.join(current, entry)
        if target.startswith(canon + "-"):
            consumed = len(canon.split("-"))
            result = _probe_path(os.path.join(current, entry), segments[consumed:])
            if result:
                return result
    return None


def decode_project_path(encoded: str) -> Optional[str]:
    """Recover the source cwd from Claude Code's encoded transcript dirname.

    Encoding collapses `:`, path separators, and spaces all to `-`, so the
    decode is inherently lossy. We probe the filesystem to disambiguate.
    Returns None if no matching directory exists — caller should treat that
    as "project moved/renamed/deleted" and skip per-project caps (better to
    miss attribution than to write settings into the wrong project)."""
    if not encoded:
        return None
    if sys.platform == "win32":
        if "--" not in encoded:
            return None
        drive, _, rest = encoded.partition("--")
        if len(drive) != 1 or not drive.isalpha():
            return None
        root = f"{drive.upper()}:\\"
        segments = rest.split("-") if rest else []
    else:
        # POSIX: the leading `/` becomes a single `-`. Any empty segments
        # from the split (doubled separators) are ignored.
        if not encoded.startswith("-"):
            return None
        root = "/"
        segments = [s for s in encoded.split("-") if s]
    return _probe_path(root, segments)


def _flatten_content(content: Any) -> str:
    """Return a string representation of tool_result/assistant content."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                t = block.get("type")
                if t == "text":
                    parts.append(str(block.get("text", "")))
                elif t == "tool_result":
                    parts.append(_flatten_content(block.get("content")))
                else:
                    # other block types (image, etc.) — count their repr length
                    parts.append(json.dumps(block, ensure_ascii=False))
        return "".join(parts)
    return str(content)


def _summarize_tool_input(tool_name: str, tool_input: Any) -> dict:
    """Cheap, privacy-safe summary of a tool_use input for dedup / analysis."""
    if not isinstance(tool_input, dict):
        return {}
    if tool_name == "Read":
        return {
            "file_path": str(tool_input.get("file_path", "")),
            "offset": tool_input.get("offset"),
            "limit": tool_input.get("limit"),
        }
    if tool_name == "Bash":
        cmd = str(tool_input.get("command", ""))[:100]
        return {"command": cmd}
    if tool_name and tool_name.startswith("mcp__"):
        # mcp__<server>__<tool>
        return {"mcp": tool_name}
    return {}


def iter_session_file(path: str) -> Iterator[Event]:
    """Stream events from one session JSONL file.

    Skips malformed lines with a debug log. Never writes.
    """
    project = os.path.basename(os.path.dirname(path))
    session_id = os.path.splitext(os.path.basename(path))[0]

    try:
        f = open(path, "r", encoding="utf-8", errors="replace")
    except OSError as e:
        log.warning("cannot open %s: %s", path, e)
        return

    with f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                log.debug("malformed line %s:%d", path, lineno)
                continue
            if not isinstance(rec, dict):
                continue

            ts = rec.get("timestamp")
            sid = rec.get("sessionId") or session_id
            rtype = rec.get("type")
            msg = rec.get("message") or {}

            if rtype == "assistant" and isinstance(msg, dict):
                usage = msg.get("usage") or {}
                model = msg.get("model")
                if model and not (isinstance(model, str) and model.startswith("<")):
                    yield Event(
                        kind="assistant_usage",
                        ts=ts,
                        session_id=sid,
                        project=project,
                        model=model,
                        input_tokens=int(usage.get("input_tokens", 0) or 0),
                        output_tokens=int(usage.get("output_tokens", 0) or 0),
                        cache_creation_tokens=int(usage.get("cache_creation_input_tokens", 0) or 0),
                        cache_read_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
                        text_tail=_tail_text(msg.get("content")),
                    )

                # tool_use items
                for block in msg.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tname = block.get("name") or ""
                        yield Event(
                            kind="tool_use",
                            ts=ts,
                            session_id=sid,
                            project=project,
                            tool_name=tname,
                            tool_use_id=block.get("id"),
                            input_summary=_summarize_tool_input(tname, block.get("input")),
                        )

            elif rtype == "user" and isinstance(msg, dict):
                for block in msg.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        flat = _flatten_content(block.get("content"))
                        size = len(flat)
                        trunc = any(m in flat for m in _TRUNCATION_MARKERS)
                        is_err = bool(block.get("is_error"))
                        yield Event(
                            kind="tool_result",
                            ts=ts,
                            session_id=sid,
                            project=project,
                            tool_use_id=block.get("tool_use_id"),
                            response_size_bytes=size,
                            truncated=trunc,
                            is_error=is_err,
                        )

            elif rtype == "system":
                content = rec.get("content") or msg.get("content") if isinstance(msg, dict) else None
                text = _flatten_content(content) if content else ""
                if "compact" in text.lower():
                    yield Event(kind="compact", ts=ts, session_id=sid, project=project)


def _tail_text(content: Any, n: int = 80) -> Optional[str]:
    """Last n chars of assistant text content — for partial-output heuristic."""
    text = _flatten_content(content)
    if not text:
        return None
    return text[-n:]


DEFAULT_FETCH_LOG = os.path.expanduser("~/.claude/tokenomy/fetch-log.jsonl")


def _parse_iso_ms(ts: str) -> Optional[float]:
    """Return epoch-ms for an ISO 8601 string, or None on parse failure."""
    if not ts:
        return None
    try:
        s = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
        return datetime.fromisoformat(s).timestamp() * 1000.0
    except (ValueError, AttributeError):
        return None


def iter_fetch_log(path: str) -> Iterator[Event]:
    """Stream `fetch_call` Events from the Tokenomy fetch-audit hook log.

    Only `phase == "post"` records are emitted. When a matching `phase == "pre"`
    line appeared earlier (same session_id + input_hash + tool_name), we subtract
    its timestamp from the post ts to recover wall-clock duration — the hook
    payload's own `duration_ms` field is zero for most Claude Code tool calls
    because the harness doesn't populate it.

    The pending-pre dict is pruned as matches land, so memory stays bounded
    by the count of outstanding tool calls (normally single digits).
    """
    try:
        f = open(path, "r", encoding="utf-8", errors="replace")
    except OSError as e:
        log.warning("cannot open fetch-log %s: %s", path, e)
        return
    pending: dict[tuple[str, str, str], str] = {}
    with f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                log.debug("malformed fetch-log line %s:%d", path, lineno)
                continue
            if not isinstance(rec, dict):
                continue
            key = (
                rec.get("session_id") or "",
                rec.get("input_hash") or "",
                rec.get("tool_name") or "",
            )
            phase = rec.get("phase")
            if phase == "pre":
                pending[key] = rec.get("ts") or ""
                continue
            if phase != "post":
                continue
            duration_ms = int(rec.get("duration_ms") or 0)
            pre_ts = pending.pop(key, "")
            if pre_ts:
                t0 = _parse_iso_ms(pre_ts)
                t1 = _parse_iso_ms(rec.get("ts") or "")
                if t0 is not None and t1 is not None and t1 > t0:
                    duration_ms = int(t1 - t0)
            yield Event(
                kind="fetch_call",
                ts=rec.get("ts"),
                session_id=rec.get("session_id") or None,
                tool_name=rec.get("tool_name") or None,
                response_size_bytes=int(rec.get("output_bytes") or 0),
                duration_ms=duration_ms,
                input_summary={"input_hash": rec.get("input_hash", "")},
            )


def iter_corpus(
    root: str,
    fetch_log: Optional[str] = None,
) -> Iterator[tuple[str, Iterator[Event]]]:
    """Yield (path, event_iter) for every *.jsonl session file under root.

    If `fetch_log` is provided (and the file exists), it is yielded as a
    parallel virtual stream keyed by session_id. Events from the fetch log
    can be merged into per-session aggregation just like native session
    events — their `session_id` matches the Claude Code sessions that
    produced them.
    """
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.endswith(".jsonl"):
                path = os.path.join(dirpath, name)
                yield path, iter_session_file(path)
    if fetch_log and os.path.exists(fetch_log):
        yield fetch_log, iter_fetch_log(fetch_log)
