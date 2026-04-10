"""Settings writer tests."""
from __future__ import annotations

import json
import os

from tuner.settings_writer import (
    BACKUP_SUFFIX,
    SENTINEL_KEY,
    build_env_block,
    merge_into_user_settings,
    write_settings,
)


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


def _read(p):
    return json.loads(open(p, encoding="utf-8").read())


def test_merge_creates_settings_and_backs_up(tmp_path):
    p = str(tmp_path / "settings.json")
    existing = {
        "env": {"USER_KEY": "keep-me", "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "99999"},
        "statusLine": {"type": "command", "command": "foo"},
    }
    with open(p, "w", encoding="utf-8") as f:
        json.dump(existing, f)

    merge_into_user_settings(p, {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 8000})

    data = _read(p)
    # Tuned cap overwrites prior value
    assert data["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "8000"
    # User-authored env key is preserved
    assert data["env"]["USER_KEY"] == "keep-me"
    # Other top-level keys untouched
    assert data["statusLine"] == existing["statusLine"]
    # Sentinel records managed keys
    assert SENTINEL_KEY in data
    assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" in data[SENTINEL_KEY]["managed_env_keys"]
    assert "USER_KEY" not in data[SENTINEL_KEY]["managed_env_keys"]
    # Backup exists and matches original
    backup = p + BACKUP_SUFFIX
    assert os.path.exists(backup)
    assert _read(backup) == existing


def test_merge_respects_user_pinned(tmp_path):
    p = str(tmp_path / "settings.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"env": {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": "4321"}}, f)

    merge_into_user_settings(
        p,
        {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 8000},
        user_pinned=["CLAUDE_CODE_MAX_OUTPUT_TOKENS"],
    )
    data = _read(p)
    # Pinned key is not overwritten
    assert data["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "4321"
    # And not recorded as managed
    assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" not in data[SENTINEL_KEY]["managed_env_keys"]


def test_merge_prunes_previously_managed_keys(tmp_path):
    p = str(tmp_path / "settings.json")
    # First run: manages CLAUDE_CODE_MAX_OUTPUT_TOKENS
    merge_into_user_settings(
        p,
        {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 8000},
        baseline={},
    )
    assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" in _read(p)["env"]

    # Second run: user pinned that key, tuner no longer manages it
    merge_into_user_settings(
        p,
        {},
        user_pinned=[],
        baseline={},
    )
    data = _read(p)
    assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" not in data["env"]
    assert data[SENTINEL_KEY]["managed_env_keys"] == []


def test_merge_backup_is_one_time(tmp_path):
    p = str(tmp_path / "settings.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"env": {"USER_KEY": "v1"}}, f)
    merge_into_user_settings(p, {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 8000})
    backup = p + BACKUP_SUFFIX
    first_mtime = os.path.getmtime(backup)
    # Second merge must NOT overwrite the backup
    merge_into_user_settings(p, {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 9000})
    assert os.path.getmtime(backup) == first_mtime
    # Backup still reflects the original pre-tokenomy state
    assert _read(backup) == {"env": {"USER_KEY": "v1"}}


def test_backup_rotated_on_version_bump(tmp_path):
    """Version bump archives old backup with version suffix, creates fresh one."""
    p = str(tmp_path / "settings.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"env": {"USER_KEY": "original"}}, f)

    # First merge at v0.3.1
    merge_into_user_settings(p, {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 8000}, version="0.3.1")
    backup = p + BACKUP_SUFFIX
    assert os.path.exists(backup)

    # Second merge at v0.4.0 — should rotate
    merge_into_user_settings(p, {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 9000}, version="0.4.0")
    archived = backup + ".0.3.1"
    assert os.path.exists(archived), "Old backup should be archived with version suffix"
    assert os.path.exists(backup), "Fresh backup should exist"


def test_merge_missing_file_creates_it(tmp_path):
    p = str(tmp_path / "settings.json")
    merge_into_user_settings(p, {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": 8000}, baseline={})
    data = _read(p)
    assert data["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "8000"
    # No prior file → no backup expected
    assert not os.path.exists(p + BACKUP_SUFFIX)
