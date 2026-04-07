"""Write auto-settings.json from a caps dict."""
from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict

FLOORS = {
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS": 4000,
    "MAX_THINKING_TOKENS": 2000,
    "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": 25,
    "MAX_MCP_OUTPUT_TOKENS": 5000,
}


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
            if per_server_supported:
                # future format
                for server, sv in v.items():
                    env[f"MAX_MCP_OUTPUT_TOKENS__{server}"] = str(_enforce_floor(k, sv))
            else:
                vals = [int(x) for x in v.values() if isinstance(x, (int, float))]
                if vals:
                    env[k] = str(_enforce_floor(k, max(vals)))
        elif isinstance(v, (int, float)):
            env[k] = str(_enforce_floor(k, v))
    return env


def write_settings(path: str, caps: Dict[str, Any], per_server_supported: bool = False) -> Dict[str, str]:
    env = build_env_block(caps, per_server_supported)
    payload = {"env": env}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".auto-settings.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return env
