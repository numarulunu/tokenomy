# Tokenomy — Install & Troubleshooting

## Install

```
/plugin marketplace add github:numarulunu/tokenomy
/plugin install tokenomy
```

Restart Claude Code after install so env vars and statusline pick up.

## Verify

1. **Statusline:** you should see a ccusage token/cost counter at the bottom of the terminal. If not, check `npx` is on PATH: `npx --version`.
2. **Env vars:** ask Claude to run `env | grep CLAUDE` — you should see `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=70`, `MAX_THINKING_TOKENS=8000`, etc.
3. **read-once hook:** ask Claude to read the same file twice in one session. The second read should be blocked with a `[tokenomy]` message.
4. **log-grep hook:** create a 500-line `test.log` with a few `ERROR` lines, ask Claude to read it. You should see a `[tokenomy] Log filtered:` header.

## Troubleshooting

**Statusline is blank.** ccusage needs `npx` (ships with Node.js). Install Node, restart Claude Code.

**Hooks don't fire.** Check plugin is enabled: `/plugin list`. Hooks are registered in the plugin's `settings.json` — if a PreToolUse/Read matcher from another plugin conflicts, Tokenomy's hooks still run (all matching hooks execute).

**Hook errors break my session.** They shouldn't — every hook fails open. If you see unexpected blocks, check `~/.claude/tokenomy/` for cache files and delete them. Report the case as a GitHub issue with the blocked message text.

**I need to force-read a log.** Include `!fulllog` anywhere in your next prompt, or use `Bash: cat "$path"`.

**I need to force-re-read a file.** Either edit the file (mtime changes, cache invalidates) or use `Bash: cat "$path"`.

**ENABLE_TOOL_SEARCH breaks something.** A few community tools assume all tools are always loaded. Unset it: add `ENABLE_TOOL_SEARCH=false` to your own `~/.claude/settings.json` — user settings override plugin settings.

## Uninstall

```
/plugin uninstall tokenomy
```

Cache files in `~/.claude/tokenomy/` can be removed manually: `rm -rf ~/.claude/tokenomy`.
