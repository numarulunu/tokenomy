# Tokenomy for Codex

These rules ship with the Codex side of Tokenomy. They complement higher-priority user, system, developer, and repo instructions.

## Goal

Keep Codex sessions concise, verifiable, and cheap without blocking real work.

## Reading

- Never read a generated, cache, lock, database, media, or log file unless it is directly needed.
- For files over 200 lines, locate the target first with `rg`, then read only the needed range.
- For logs, read a filtered tail or matching error lines first. Full logs require an explicit reason.
- After editing, verify with `git diff` or targeted tests instead of re-reading the file.

## Output

- Start with the direct answer or outcome.
- Keep routine prose short. Expand only when the user asks for depth or the task requires it.
- Reference files by path instead of pasting large snippets.
- Report command failures plainly and include the next useful action.

## Tool Use

- Prefer `rg` and `rg --files` for search.
- Use structured parsers for JSON, TOML, YAML, XML, and code when available.
- Avoid raw dumps from unbounded commands. Filter logs, API responses, and large listings before reading them.
- Do not install dependencies, run network commands, deploy, or mutate user-level config without approval.

## Codex Project Bootstrap

When asked to set up Tokenomy for a Codex project, add these files only if missing:

1. `AGENTS.md` from `templates/AGENTS.md`
2. `.gitignore` additions from `templates/codex-gitignore-additions`

Do not overwrite existing files. If a file exists, report the missing pieces and ask before patching.

## Hooks

Tokenomy's Codex hooks are fail-open. If hook input is missing, unsupported, or malformed, approve the action and log the error under `~/.codex/tokenomy/`.

## Boundaries

- Do not edit Claude-specific files as part of Codex setup unless the user asks.
- Do not write to `~/.claude`.
- Do not change source code during a token audit. Token audits are configuration-only.
- Preserve user changes in dirty worktrees.
