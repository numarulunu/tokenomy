"""Guard against version drift between plugin.json, state schema, and the hook.

If these three ever disagree, the SessionStart version-gate will either
silently skip the auto-migration (false-negative: user stays on old caps) or
re-run the tuner every single session (false-positive: wasted cycles).
"""
from __future__ import annotations

import json
import os
import re

from tuner.state import SCHEMA_VERSION

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _plugin_version() -> str:
    with open(os.path.join(ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
        return json.load(f)["version"]


def _hook_version() -> str:
    with open(os.path.join(ROOT, "hooks", "session-start.sh"), encoding="utf-8") as f:
        src = f.read()
    m = re.search(r'TOKENOMY_VERSION="([^"]+)"', src)
    assert m, "TOKENOMY_VERSION not found in session-start.sh"
    return m.group(1)


def test_plugin_version_matches_state_schema():
    assert _plugin_version() == SCHEMA_VERSION


def test_hook_version_matches_plugin():
    assert _hook_version() == _plugin_version()
