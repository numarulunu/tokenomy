# Codex Migration Notes

Date: 2026-04-14
Version: 1.0.1

## Scope

The Codex migration is additive. Claude Code files remain untouched. New Codex files sit beside the original plugin files.

## Created Files

- `.codex-plugin/plugin.json`
- `codex-hooks.json`
- `codex/hook.py`
- `codex/analyze.py`
- `AGENTS.md`
- `templates/AGENTS.md`
- `templates/codex-gitignore-additions`
- `skills/codex-token-audit/SKILL.md`
- `CODEX_README.md`
- `INSTALL_CODEX.md`

## Behavior Changes

No existing behavior changes. The Codex side reads `~/.codex/sessions`, stores state under `~/.codex/tokenomy`, and avoids Claude-only settings.

## Known Non-Ports

- Claude `statusLine` config has no matching field in the local Codex plugin examples, so the Codex port does not include a status feature.
- Claude env caps are not ported because Codex does not use `CLAUDE_*` env vars.
- The old auto-tuner writes `~/.claude/settings.json`; the Codex port does not run it.
