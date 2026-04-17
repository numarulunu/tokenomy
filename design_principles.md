# Design Principles

## Tokenomy

- Additive migrations first. Do not remove working Claude files while building the Codex side.
- Codex state belongs under `~/.codex/tokenomy`.
- Claude state belongs under `~/.claude/tokenomy`.
- Hooks fail open. A token-saving tool must not block real work when its input is missing or malformed.
- Human output stays short. Machine output stays complete.
- Configuration changes are explicit and reversible.
- Existing user worktree changes are preserved.
