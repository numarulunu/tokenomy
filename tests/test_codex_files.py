from __future__ import annotations

import json
import os
import re


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _read(path: str) -> str:
    with open(os.path.join(ROOT, path), encoding="utf-8") as handle:
        return handle.read()


def test_codex_plugin_json_is_valid():
    with open(os.path.join(ROOT, ".codex-plugin", "plugin.json"), encoding="utf-8") as handle:
        data = json.load(handle)
    assert data["name"] == "tokenomy"
    assert data["version"] == "1.0.1"
    assert data["hooks"] == "./codex-hooks.json"
    assert data["skills"] == "./skills/"


def test_codex_hooks_json_is_valid():
    with open(os.path.join(ROOT, "codex-hooks.json"), encoding="utf-8") as handle:
        data = json.load(handle)
    assert "SessionStart" in data["hooks"]
    assert "PreToolUse" in data["hooks"]
    assert "SessionEnd" in data["hooks"]


def test_codex_versions_match():
    with open(os.path.join(ROOT, ".codex-plugin", "plugin.json"), encoding="utf-8") as handle:
        version = json.load(handle)["version"]
    hook = _read(os.path.join("codex", "hook.py"))
    analyze = _read(os.path.join("codex", "analyze.py"))
    assert re.search(r'VERSION = "([^"]+)"', hook).group(1) == version
    assert re.search(r'VERSION = "([^"]+)"', analyze).group(1) == version
