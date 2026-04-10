"""Merge tokenomy-managed env caps into the user's ~/.claude/settings.json.

Claude Code only auto-loads ~/.claude/settings.json (+ project/local variants);
any file under ~/.claude/<subdir>/ is ignored, and plugin settings.json only
honors the `agent` key per the plugins doc. So tokenomy writes its env caps
directly into the user's settings file, fenced by a sentinel block so we can
update/remove them cleanly on each run and on uninstall.

Public API:
- build_env_block(caps, per_server_supported=False) -> {ENV_NAME: "str"}
- merge_into_user_settings(path, caps, user_pinned=None, ...) -> dict
- write_settings(path, caps, ...) -> dict   # legacy standalone writer
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from typing import Any, Dict, Iterable, Optional, Set

log = logging.getLogger(__name__)

FLOORS = {
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS": 4000,
    "MAX_THINKING_TOKENS": 2000,
    "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": 25,
    "MAX_MCP_OUTPUT_TOKENS": 5000,
}

# Static baselines that used to live in the plugin's settings.json (where the
# `env` key is silently ignored). Merged into the user's settings.json alongside
# tuned caps so they actually take effect.
BASELINE_ENV: Dict[str, str] = {
    "ENABLE_TOOL_SEARCH": "true",
    "MAX_THINKING_TOKENS": "8000",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "CLAUDE_CODE_DISABLE_BUG_COMMAND": "1",
    "DISABLE_ERROR_REPORTING": "1",
    "DISABLE_AUTOUPDATER": "1",
    "DISABLE_TELEMETRY": "1",
}

SENTINEL_KEY = "__tokenomy__"
BACKUP_SUFFIX = ".tokenomy.bak"


def _enforce_floor(name: str, value: int) -> int:
    floor = FLOORS.get(name, 0)
    return max(int(value), floor)


def build_env_block(caps: Dict[str, Any], per_server_supported: bool = False) -> Dict[str, str]:
    """Convert caps dict → env var block. MAX_MCP_OUTPUT_TOKENS is collapsed
    to max() across servers unless per_server_supported (Claude Code currently
    accepts a single global value)."""
    env: Dict[str, str] = {}
    for k, v in caps.items():
        if k == "MAX_MCP_OUTPUT_TOKENS" and isinstance(v, dict):
            # NOTE: per_server_supported is dormant scaffolding. As of the
            # current Claude Code docs (code.claude.com/docs/en/settings),
            # MAX_MCP_OUTPUT_TOKENS is global-only — no `__<server>` suffix
            # form is documented. Do not flip this on speculation; verify in
            # the docs first or it will silently produce env keys Claude Code
            # ignores.
            if per_server_supported:
                for server, sv in v.items():
                    env[f"MAX_MCP_OUTPUT_TOKENS__{server}"] = str(_enforce_floor(k, sv))
            else:
                vals = [int(x) for x in v.values() if isinstance(x, (int, float))]
                if vals:
                    env[k] = str(_enforce_floor(k, max(vals)))
        elif isinstance(v, (int, float)):
            env[k] = str(_enforce_floor(k, v))
    return env


def _atomic_write_json(path: str, data: Any, sort_keys: bool = False) -> None:
    """Atomic JSON write. Defaults to sort_keys=False because this is used
    for the user's settings.json — alphabetizing on every run created noisy
    cosmetic diffs in dot-file repos. Callers writing machine-only files
    (like applied.json) can opt in with sort_keys=True."""
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".settings.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=sort_keys)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        log.warning("could not parse %s (%s) — treating as empty", path, e)
        return {}


def merge_into_user_settings(
    path: str,
    caps: Dict[str, Any],
    user_pinned: Optional[Iterable[str]] = None,
    per_server_supported: bool = False,
    baseline: Optional[Dict[str, str]] = None,
    version: str = "0.4.0",
) -> Dict[str, str]:
    """Atomically merge tokenomy-managed env keys into the user's settings.json.

    - Creates a one-time backup at `<path><BACKUP_SUFFIX>` (never overwritten).
    - Writes tuned caps + baselines into the `env` block.
    - Records managed keys under the `__tokenomy__` sentinel so the next run
      can prune keys we no longer manage.
    - Never touches keys listed in `user_pinned` (nor keys the user already set
      that tokenomy never claimed).

    Returns the env dict that was actually merged in.
    """
    pinned: Set[str] = set(user_pinned or ())
    base = dict(baseline if baseline is not None else BASELINE_ENV)

    tuned = build_env_block(caps, per_server_supported=per_server_supported)

    # Compose the tokenomy-managed env. Tuned values win over static baselines.
    managed: Dict[str, str] = {}
    for k, v in base.items():
        if k in pinned:
            continue
        managed[k] = v
    for k, v in tuned.items():
        if k in pinned:
            continue
        managed[k] = v

    settings = _load_json(path)

    # Backup: create on first run, rotate on version bump.
    if os.path.exists(path):
        backup = path + BACKUP_SUFFIX
        prev_meta = settings.get(SENTINEL_KEY) if isinstance(settings.get(SENTINEL_KEY), dict) else {}
        prev_version = prev_meta.get("version")
        if os.path.exists(backup) and prev_version and prev_version != version:
            # Version bump: archive old backup with version suffix
            archived = f"{backup}.{prev_version}"
            try:
                os.rename(backup, archived)
                log.info("rotated backup to %s", archived)
            except OSError as e:
                log.warning("could not rotate backup: %s", e)
        if not os.path.exists(backup):
            try:
                shutil.copy2(path, backup)
            except OSError as e:
                log.warning("could not create backup %s: %s", backup, e)

    env_block = settings.get("env") if isinstance(settings.get("env"), dict) else {}
    env_block = dict(env_block)

    # Prune previously-managed keys that we no longer set (e.g. server removed,
    # user pinned it since last run). Leaves any user-authored keys alone.
    prev_meta = settings.get(SENTINEL_KEY) if isinstance(settings.get(SENTINEL_KEY), dict) else {}
    prev_managed = prev_meta.get("managed_env_keys") or []
    for k in prev_managed:
        if k in pinned:
            continue
        if k not in managed and k in env_block:
            del env_block[k]

    # Write current managed keys, respecting pins (never stomp a pinned key).
    for k, v in managed.items():
        if k in pinned:
            continue
        env_block[k] = v

    settings["env"] = env_block
    settings[SENTINEL_KEY] = {
        "version": version,
        "managed_env_keys": sorted(managed.keys()),
    }

    _atomic_write_json(path, settings)
    return managed


def write_settings(
    path: str,
    caps: Dict[str, Any],
    per_server_supported: bool = False,
) -> Dict[str, str]:
    """Legacy standalone writer: emits `{"env": {...}}` to `path`.

    Kept for tests and for any caller that wants a plain settings-shaped dump.
    Production code should call `merge_into_user_settings` instead — a
    standalone file at `~/.claude/tokenomy/auto-settings.json` is not loaded
    by Claude Code.
    """
    env = build_env_block(caps, per_server_supported)
    _atomic_write_json(path, {"env": env})
    return env
