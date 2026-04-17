---
name: codex-token-audit
description: Use when the user asks to audit Codex token usage, set up Tokenomy for Codex, or says "Codex Tokenomy this project". Checks for AGENTS.md guidance, .gitignore coverage, and obvious token bloat without touching source code.
---

# Codex Token Audit

Audit the current project for Codex token efficiency. Scope is configuration only.

## Steps

1. Locate the project root. Use the current working directory. If it is not a git repo, ask before creating files.
2. Check for `AGENTS.md`.
   - If missing: create it from `templates/AGENTS.md`.
   - If present: do not overwrite it. Report whether it includes token discipline, filtered logs, and large-file reading rules.
3. Check `.gitignore`.
   - If missing: create it from `templates/codex-gitignore-additions`.
   - If present: compare it with `templates/codex-gitignore-additions` and report missing patterns.
   - Ask before patching an existing `.gitignore`.
4. Scan for token bloat.
   - Count generated folders: `node_modules/`, `__pycache__/`, `.pytest_cache/`, `.venv/`, `venv/`, `dist/`, `build/`.
   - Count generated files: `*.log`, `*.db`, `*.sqlite*`, lockfiles, archives, media.
   - List the largest tracked files if the repo uses git.
5. Report under 15 lines.

## Rules

- Never overwrite an existing file without explicit confirmation.
- Never edit source code during the audit.
- Never commit changes.
- Never write to `.claude` or `~/.claude`.
- Prefer `rg` or native shell filters with bounded output.
