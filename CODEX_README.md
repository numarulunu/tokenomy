# Tokenomy for Codex

Tokenomy for Codex is an additive port of the Claude Code plugin. It leaves the Claude files in place and adds Codex-native files beside them.

## What Maps To What

| Claude side | Codex side |
|---|---|
| `.claude-plugin/plugin.json` | `.codex-plugin/plugin.json` |
| `CLAUDE.md` | `AGENTS.md` |
| `hooks/hooks.json` | `codex-hooks.json` |
| `hooks/*.sh` | `codex/hook.py` |
| `templates/.claudeignore` | `templates/codex-gitignore-additions` |
| `skills/token-audit` | `skills/codex-token-audit` |
| `analyzer/analyze.py` for `~/.claude/projects` | `codex/analyze.py` for `~/.codex/sessions` |

## Install Shape

Codex discovers the plugin through `.codex-plugin/plugin.json`. The manifest points to:

- `codex-hooks.json` for hooks
- `skills/` for skills, including `codex-token-audit`
- `AGENTS.md` for repo-local Codex behavior

No Claude file is deleted or replaced.

## Hooks

The Codex hook entrypoint is:

```bash
python ./codex/hook.py <action>
```

Supported actions:

- `session-start`
- `log-grep`
- `read-once`
- `cleanup`

State and logs live under `~/.codex/tokenomy/`. Hooks fail open.

## Analyzer

Run:

```bash
python ./codex/analyze.py --days 30
```

It writes `~/.codex/tokenomy/codex-insights.json` and prints a short token report.

## Limits

Codex does not use Claude-only variables such as `CLAUDE_PLUGIN_ROOT`, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`, or `CLAUDE_CODE_MAX_OUTPUT_TOKENS`. The Codex port avoids writing those settings.
