# Install Tokenomy for Codex

This file documents the Codex-side install shape. It does not change or remove the Claude Code install.

## Local Use

1. Keep this repo as the plugin root.
2. Confirm `.codex-plugin/plugin.json` exists.
3. Confirm `codex-hooks.json` exists.
4. Start a fresh Codex session from this repo or install the plugin through your Codex plugin marketplace flow.

## Verify

Run:

```bash
python ./codex/analyze.py --days 30
python ./codex/hook.py session-start
```

Expected results:

- Analyzer writes `~/.codex/tokenomy/codex-insights.json`.
- Session-start hook prints JSON with `suppressOutput`.
- Logs appear under `~/.codex/tokenomy/`.

## Troubleshooting

**Analyzer finds zero sessions.** Check that `~/.codex/sessions` exists and contains `.jsonl` files.

**Hooks do nothing.** That is expected when Codex does not emit Claude-style `Read` tool hook payloads. Hooks fail open by design.
