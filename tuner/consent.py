"""First-run consent summary writer."""
from __future__ import annotations

import os
from typing import Dict


_LABELS = {
    "ENABLE_TOOL_SEARCH": "enables tool search for better results",
    "MAX_THINKING_TOKENS": "caps thinking tokens at 8000",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "disables non-essential network traffic",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "disables automatic memory writes",
    "CLAUDE_CODE_DISABLE_BUG_COMMAND": "disables the /bug command",
    "DISABLE_ERROR_REPORTING": "disables error reporting to Anthropic",
    "DISABLE_AUTOUPDATER": "disables automatic updates",
    "DISABLE_TELEMETRY": "disables telemetry",
}


def has_consent(home_dir: str) -> bool:
    """True once `consent-summary.txt` has been written in `home_dir`.

    Used to make `--first-run` idempotent: subsequent invocations skip the
    baseline-only write-and-explain step so repeated `SessionStart` triggers
    don't clobber tuned caps with baselines.
    """
    return os.path.exists(os.path.join(home_dir, "consent-summary.txt"))


def write_consent_summary(home_dir: str, baseline_env: Dict[str, str]) -> str:
    """Write a human-readable summary of what Tokenomy manages. Returns path."""
    path = os.path.join(home_dir, "consent-summary.txt")
    lines = [
        "Tokenomy — First-Run Summary",
        "=" * 40,
        "",
        "Tokenomy optimizes Claude Code token usage by managing",
        "environment variables in ~/.claude/settings.json:",
        "",
    ]
    for key in sorted(baseline_env):
        label = _LABELS.get(key, "")
        lines.append(f"  {key}={baseline_env[key]}  ({label})" if label else f"  {key}={baseline_env[key]}")
    lines.extend([
        "",
        "After collecting enough session data (200+ effective samples),",
        "the auto-tuner will also manage computed caps for output tokens,",
        "MCP result sizes, and autocompact threshold.",
        "",
        "To reset all Tokenomy changes: python -m tuner.tuner --reset",
        f"This summary: {path}",
    ])
    os.makedirs(home_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path
