# Agent Instructions

## Token Discipline

- Start with the answer or result.
- Keep routine prose short.
- Use `rg` before reading large files.
- Never read a file over 200 lines without narrowing the range first.
- Filter logs before reading them. Read full logs only when the user asks or the filtered view is insufficient.
- After editing, verify with `git diff` or tests instead of re-reading the file.

## Project Hygiene

- Respect `.gitignore`.
- Avoid generated folders: `node_modules/`, `__pycache__/`, `.pytest_cache/`, `dist/`, `build/`, `.venv/`, `venv/`.
- Avoid generated file types: `*.log`, `*.db`, `*.db-*`, `*.sqlite*`, lockfiles, media files, archives.
- Do not install dependencies or run network commands without approval.

## Reporting

- Report what changed, what was verified, and what remains.
- Use file paths instead of pasting large file contents.
- Keep final answers compact unless the user asks for a full breakdown.
