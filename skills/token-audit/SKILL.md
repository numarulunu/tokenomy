---
name: token-audit
description: Use when the user asks to audit token usage in a project, set up tokenomy in a new repo, or says "tokenomy this project" / "token audit". Checks for .claudeignore and .claude/settings.json, creates them from tokenomy templates if missing, and reports the findings.
---

# token-audit

Audit the current project for token-efficiency setup. Create missing config files from tokenomy's templates. Report what changed.

## Steps

1. **Locate the project root.** Use the current working directory. If it is not a git repo, ask the user to confirm they want to proceed here.

2. **Check for `.claudeignore`.**
   - If missing: copy `${CLAUDE_PLUGIN_ROOT}/templates/.claudeignore` into the project root.
   - If present: read it, compare against the tokenomy template, and list any missing standard exclude patterns. Offer to merge (do not overwrite without asking).

3. **Check for `.claude/settings.json`.**
   - If missing: create `.claude/` and copy `${CLAUDE_PLUGIN_ROOT}/templates/project-settings.json` into `.claude/settings.json`.
   - If present: check that `respectGitignore: true` is set. If not, point this out and offer to patch.

4. **Scan for obvious token bloat.** Run these checks and report findings:
   - Any file >1 MB tracked in git (`git ls-files | xargs -I{} stat -c '%s {}' 2>/dev/null | sort -rn | head -5`)
   - Count of files matching `*.log`, `*.db`, `*.sqlite*`, `node_modules/`, `__pycache__/`, `venv/`
   - Whether a `CLAUDE.md` exists at the project root

5. **Report.** Give a short summary:
   - What was created
   - What was skipped (and why)
   - Top 3 findings / recommendations
   - Estimated token savings (rough: "50-80% reduction in project discovery cost" if both files were created from scratch).

## Rules

- Never overwrite an existing file without explicit confirmation.
- Never commit changes. Leave staging to the user.
- Do not install dependencies, run tests, or modify source files. Scope is configuration only.
- Keep the final report under 15 lines.
