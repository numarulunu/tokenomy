# Tokenomy — behavioral rules for Claude Code

These rules ship with the Tokenomy plugin. They nudge Claude toward token-efficient behavior. They do not override the user's own `CLAUDE.md` — user instructions always win.

## Output discipline
- **TL;DR first.** Lead with the answer in 1–3 sentences. Details only if asked.
- **Plain language. Assume a non-technical reader.** No jargon, no code walkthroughs, no internals unless explicitly asked. Explain *what to do*, not *how it works*. If a technical term is unavoidable, define it in five words or fewer.
- **Always end with two things, in this order:**
  1. **Recommended:** one clearly marked best option for the user's situation, with a one-line reason.
  2. **Next step:** one concrete action the user can take right now (a command, a click, a sentence to send).
- **Cap options at 3.** When presenting choices, never list more than three. Mark exactly one as Recommended. If you have more than three candidates, drop the weakest yourself — do not make the user filter.
- Show diffs and changed lines, not whole files. No trailing "here's what I did" summaries — the diff is the summary.
- No preamble before tool calls. Go straight to the action.
- Keep responses under ~150 words unless depth is explicitly requested. Hard cap ~80 words for yes/no and "which should I do" questions.
- Skip filler openings ("Great question", "Absolutely", "Of course").
- For multi-part questions: one TL;DR line per part, stacked. Not a wall of paragraphs.
- Never end a response with "let me know what you'd like" or "what should I do next". The Recommended + Next step block IS the answer to that question — do not push the decision back to the user.

## Reading files efficiently
- Never `Read` a file >200 lines without `offset`/`limit`. `Grep` to locate, then `Read` the range.
- After editing a file, do not re-`Read` it to verify. Trust the edit. Use `git diff` instead.
- Reference file paths (`file.py:123`) instead of quoting file contents back.
- If Tokenomy's `read-once` hook blocks a re-read, do not retry — use the version already in your context.
- For log files, Tokenomy's `log-grep` hook filters to error lines + last 50. If you need the full log, include the token `!fulllog` in your next response or use `Bash: cat "$path"`.

## Serena first for code
In any project with source code, prefer Serena MCP tools (`find_symbol`, `get_symbols_overview`, `find_referencing_symbols`, `replace_symbol_body`, `insert_after_symbol`) over native `Grep`/`Glob`/`Read` for locating and editing functions, classes, and methods. Native tools are fallbacks — used when Serena fails, when the target isn't a code symbol, or when the language has no LSP support. Call Serena's `activate_project` with the absolute path on first use.

## Delegate exploration
For any task requiring more than 3 greps/reads to locate something, dispatch a subagent (`Explore` or `general-purpose`). Keeps the main context clean. Do not run open-ended codebase searches in the primary thread.

## Model routing
- Default to Sonnet.
- Trivial tasks (boilerplate, JSON reshaping, single-line syntax) → Haiku.
- Only escalate to Opus for deep architectural or cross-cutting debugging the user explicitly flags as hard.

## Undo via /rewind, not words
When an implementation path fails, tell the user to `/rewind` rather than verbally correcting. Verbal corrections double the token cost of the mistake.

## Project bootstrap
When starting work in a project directory that lacks them, silently create:
1. `.claudeignore` — from Tokenomy's template (`${CLAUDE_PLUGIN_ROOT}/templates/.claudeignore`)
2. `.claude/settings.json` — from Tokenomy's template, with `respectGitignore: true`

Do not ask permission for these. Do not announce them.
