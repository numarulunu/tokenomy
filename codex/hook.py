"""Codex hook entrypoint for Tokenomy.

All handlers fail open. State stays under ~/.codex/tokenomy unless
TOKENOMY_CODEX_HOME is set.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any

VERSION = "1.0.1"


def tokenomy_home() -> Path:
    return Path(os.environ.get("TOKENOMY_CODEX_HOME", Path.home() / ".codex" / "tokenomy"))


def log(message: str) -> None:
    home = tokenomy_home()
    try:
        home.mkdir(parents=True, exist_ok=True)
        with (home / "_codex-hook.log").open("a", encoding="utf-8") as handle:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            handle.write(f"{stamp} {message}\n")
    except Exception:
        pass


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, separators=(",", ":")))


def approve() -> None:
    emit({"decision": "approve"})


def quiet() -> None:
    emit({"suppressOutput": True})


def read_input() -> dict[str, Any]:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        log(f"input parse failed: {exc}")
        return {}


def clean_session_id(value: Any) -> str:
    sid = str(value or "nosession")
    return re.sub(r"[^A-Za-z0-9_-]", "", sid) or "nosession"


def path_from_tool_input(data: dict[str, Any]) -> Path | None:
    tool_input = data.get("tool_input") or data.get("toolInput") or {}
    if not isinstance(tool_input, dict):
        return None
    raw_path = tool_input.get("file_path") or tool_input.get("path")
    if not raw_path:
        return None
    path = Path(str(raw_path)).expanduser()
    try:
        return path.resolve()
    except OSError:
        return path


def session_start() -> None:
    home = tokenomy_home()
    try:
        home.mkdir(parents=True, exist_ok=True)
        log(f"session-start version={VERSION}")
        quiet()
    except Exception as exc:
        log(f"session-start failed: {exc}")
        quiet()


def cleanup() -> None:
    data = read_input()
    home = tokenomy_home()
    try:
        sid = clean_session_id(data.get("session_id") or data.get("sessionId"))
        cache = home / f"read-cache-{sid}.json"
        if sid != "nosession" and cache.exists():
            cache.unlink()
        cutoff = time.time() - 86400 * 7
        for path in home.glob("read-cache-*.json"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass
        tmp = home / "tmp"
        if tmp.exists():
            for path in tmp.rglob("*"):
                try:
                    if path.is_file() and path.stat().st_mtime < time.time() - 86400:
                        path.unlink()
                except OSError:
                    pass
        log("cleanup complete")
        quiet()
    except Exception as exc:
        log(f"cleanup failed: {exc}")
        quiet()


def read_once() -> None:
    data = read_input()
    try:
        path = path_from_tool_input(data)
        if path is None or not path.is_file():
            approve()
            return

        tool_input = data.get("tool_input") or data.get("toolInput") or {}
        offset = tool_input.get("offset", "")
        limit = tool_input.get("limit", "")
        sid = clean_session_id(data.get("session_id") or data.get("sessionId"))

        stat = path.stat()
        signature = f"{int(stat.st_mtime)}:{stat.st_size}"
        key = f"{path.as_posix()}|{offset}|{limit}"

        home = tokenomy_home()
        home.mkdir(parents=True, exist_ok=True)
        cache_path = home / f"read-cache-{sid}.json"
        cache: dict[str, Any] = {}
        if cache_path.exists():
            try:
                loaded = json.loads(cache_path.read_text(encoding="utf-8"))
                cache = loaded if isinstance(loaded, dict) else {}
            except Exception:
                cache = {}

        previous = cache.get(key)
        if isinstance(previous, dict) and previous.get("signature") == signature:
            when = previous.get("time", "earlier")
            reason = (
                f"[tokenomy] You already read this file at {when} this session "
                "and it has not changed. Use the version already in context."
            )
            emit({"decision": "block", "reason": reason})
            return

        cache[key] = {"signature": signature, "time": time.strftime("%H:%M:%S")}
        cache_path.write_text(json.dumps(cache, sort_keys=True), encoding="utf-8")
        approve()
    except Exception as exc:
        log(f"read-once failed: {exc}")
        approve()


def is_log_path(path: Path) -> bool:
    normalized = path.as_posix().lower()
    return (
        normalized.endswith(".log")
        or ".log." in normalized
        or "/log/" in normalized
        or "/logs/" in normalized
    )


def line_count(path: Path, cap: int = 201) -> int:
    count = 0
    with path.open("rb") as handle:
        for count, _line in enumerate(handle, start=1):
            if count >= cap:
                break
    return count


def log_grep() -> None:
    data = read_input()
    try:
        path = path_from_tool_input(data)
        if path is None or not path.is_file() or not is_log_path(path):
            approve()
            return
        if "!fulllog" in json.dumps(data):
            approve()
            return
        if line_count(path) < 200:
            approve()
            return

        with path.open("rb") as handle:
            sample = handle.read(1024)
        if b"\x00" in sample:
            approve()
            return

        pattern = re.compile(r"(ERROR|WARN|FAIL|Exception|Traceback|CRITICAL|FATAL)", re.IGNORECASE)
        matches: deque[tuple[int, str]] = deque(maxlen=200)
        tail: deque[str] = deque(maxlen=50)
        total_lines = 0
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for total_lines, line in enumerate(handle, start=1):
                text = line.rstrip()
                if pattern.search(text):
                    matches.append((total_lines, text))
                tail.append(text)

        output: list[str] = [
            f"[tokenomy] Log filtered: {total_lines} total lines. Showing {len(matches)} matched lines plus last 50.",
            f"[tokenomy] Source: {path}",
            "[tokenomy] To bypass, include !fulllog in your next prompt.",
            "",
            "=== MATCHED LINES ===",
        ]
        if matches:
            output.extend(f"{line_no}: {text}" for line_no, text in matches)
        else:
            output.append("(none)")
        output.append("")
        output.append("=== LAST 50 LINES ===")
        output.extend(tail)
        emit({"decision": "block", "reason": "\n".join(output)})
    except Exception as exc:
        log(f"log-grep failed: {exc}")
        approve()


def main(argv: list[str]) -> int:
    action = argv[1] if len(argv) > 1 else ""
    if action == "session-start":
        session_start()
    elif action == "cleanup":
        cleanup()
    elif action == "read-once":
        read_once()
    elif action == "log-grep":
        log_grep()
    else:
        log(f"unknown action: {action}")
        quiet()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
