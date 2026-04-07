"""Settings writer tests."""
from __future__ import annotations

import json

from tuner.settings_writer import build_env_block, write_settings


def test_floor_enforced():
    env = build_env_block({"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 100})
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "4000"


def test_per_server_collapsed_to_max():
    env = build_env_block({"MAX_MCP_OUTPUT_TOKENS": {"serena": 6000, "playwright": 200000}})
    assert env["MAX_MCP_OUTPUT_TOKENS"] == "200000"


def test_per_server_supported_emits_each():
    env = build_env_block(
        {"MAX_MCP_OUTPUT_TOKENS": {"serena": 6000, "playwright": 200000}},
        per_server_supported=True,
    )
    assert env["MAX_MCP_OUTPUT_TOKENS__serena"] == "6000"
    assert env["MAX_MCP_OUTPUT_TOKENS__playwright"] == "200000"


def test_write_settings_atomic(tmp_path):
    p = str(tmp_path / "auto-settings.json")
    write_settings(p, {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 8000})
    data = json.loads(open(p, encoding="utf-8").read())
    assert data["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "8000"
