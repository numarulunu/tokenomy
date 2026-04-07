# tokenomy

**Cut Claude Code token burn by 80%+ with zero code changes.**

tokenomy is a Claude Code plugin that bundles ~10 token-optimization techniques into a single install: environment variables, runtime hooks that block redundant file reads and filter log files, a ccusage statusline, project templates, and a behavioral ruleset that nudges Claude toward efficient output.

---

## Bird's-eye view

| Layer | Before | With tokenomy |
|---|---|---|
| Env vars | Default (200k ctx, verbose thinking, auto-memory, telemetry) | 70% autocompact, 8k thinking, 8k output cap, telemetry off, auto-memory off |
| File reads | Same file re-read 5–10x per session | `read-once` hook blocks re-reads of unchanged files |
| Log reads | Full 10k-line log injected into context | `log-grep` hook returns errors + last 50 lines (~95% reduction) |
| Project discovery | Claude walks `node_modules/`, `__pycache__/`, `.venv/`, lockfiles, DBs | `.claudeignore` + `respectGitignore: true` skip them |
| Output | "Here's what I did…" + full file re-quoted | Diffs only, no preamble |
| Statusline | Nothing | ccusage — live token/cost counter |
| Behavior | Re-reads, re-verifies, over-explains | CLAUDE.md rules: Serena-first, delegate exploration, /rewind not verbal corrections |

---

## Install

```
/plugin marketplace add github:numarulunu/tokenomy
/plugin install tokenomy
```

Restart Claude Code. That's it. All hooks, env vars, and the statusline wire up automatically.

### Dependencies (optional but recommended)

- **ccusage statusline** — needs `npx` on PATH (ships with Node.js)
- **Serena MCP** — install separately if you want LSP-powered code navigation. tokenomy's CLAUDE.md tells Claude to prefer it when available.

tokenomy does **not** bundle Serena or ccusage. It only configures Claude Code to use them if present.

---

## What each piece does

### Hooks (the headline feature)

**`read-once`** — `PreToolUse` hook on `Read`. Maintains a per-session cache of `(file_path, offset, limit) → mtime`. If Claude tries to read the same range of an unchanged file twice, the hook blocks the second call with a stub message pointing back to the version already in context. Mtime changes bypass the cache automatically.

**`log-grep`** — `PreToolUse` hook on `Read`. When the target matches `*.log`, `*/log/*`, or `*/logs/*`, the hook returns only lines matching `ERROR|WARN|FAIL|Exception|Traceback|CRITICAL|FATAL` (capped at last 200) plus the last 50 lines of the file. Include the token `!fulllog` in your prompt to bypass. Files under 200 lines pass through unchanged. Binary files named `*.log` pass through unchanged.

**`cleanup`** — `SessionEnd` hook. Removes per-session cache files and sweeps stale tmp.

All three hooks **fail open**. On parse error, missing file, corrupt cache, or any other edge case, the hook approves the read. A buggy hook will never hard-break your session.

### Env vars

Set via plugin `settings.json`:

- `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=70` — compact at 70% ctx instead of 92%, preventing the "fell off a cliff" moment
- `MAX_THINKING_TOKENS=8000` — caps extended thinking at 8k instead of unlimited
- `CLAUDE_CODE_MAX_OUTPUT_TOKENS=8000` — caps output per turn
- `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` — no telemetry pings
- `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` — no silent memory injections
- `ENABLE_TOOL_SEARCH=true` — deferred tool schemas, loaded on demand

### Statusline

ccusage — live token usage and cost counter pinned to the bottom of the terminal.

### Templates

- `templates/.claudeignore` — universal exclude list (Python, Node, DBs, logs, media, lockfiles)
- `templates/project-settings.json` — minimal `.claude/settings.json` with `respectGitignore: true` and sane permission scopes

Use these via the `token-audit` skill (see below) or copy them manually into new projects.

### `token-audit` skill

Say "audit tokens" or "tokenomy this project" and Claude will run the `token-audit` skill: check for `.claudeignore` and `.claude/settings.json` in the current project, create them from templates if missing, scan for obvious bloat (big tracked files, leaked caches), and report findings. Scope is configuration only — it never touches source code.

### CLAUDE.md

Plugin-scoped behavioral rules:
- Diffs only, no preamble, no trailing summaries
- Never re-read a file after editing it
- Grep first, read the range, not the whole file
- Serena-first for code symbols
- Delegate >3-query explorations to subagents
- Sonnet default, Haiku for trivial, Opus only when the user flags it
- Tell the user to `/rewind` instead of verbally correcting failed paths

User's own `CLAUDE.md` always takes precedence.

---

## Auto-tuner (v0.3.0)

Recency-weighted auto-tuner that reads your entire session corpus and picks the
most aggressive caps your data actually supports. Runs in the background via a
SessionStart hook, fail-open, self-correcting.

```
python -m tuner.tuner --dry-run     # preview proposed caps vs current state
python -m tuner.tuner                # apply (writes auto-settings.json)
python -m tuner.tuner --status       # current caps, freezes, pinned settings
python -m tuner.tuner --reset        # wipe tuner state, restore defaults
```

Highlights:
- **Recency weighting** (half-life 14d; 7d for context-compact behavior) —
  recent habits dominate, old sessions fade.
- **Confidence-driven aggression** — new users get conservative caps, heavy
  users get tight ones.
- **Hysteresis** — no oscillation. Tighten needs >=10% change, loosen >=5%.
- **Loss detection** — truncation requeries, "to be continued" endings,
  autocompact after big tool results all freeze the offending setting for 14
  days.
- **Per-MCP-server awareness** — scoped to the servers you actually have
  installed, not your whole history.
- **Floors** — no setting can ever go below a hardcoded safe floor.
- **User pinning** — anything you set in your personal `~/.claude/settings.json`
  is detected and never touched.

State lives in `~/.claude/tokenomy/`: `applied.json`, `auto-settings.json`,
`sessions.jsonl`, `losses.jsonl`, `tuner.log`.

## Analyzer (v0.2.0)

Retrospective analysis of every Claude Code session you've ever run. Walks
`~/.claude/projects/**/*.jsonl`, computes per-tool token waste, dollar spend,
and counterfactual savings ("setting X would have saved you $N").

```
python -m analyzer.analyze --days 30
```

Flags: `--days N` (default 30), `--project NAME`, `--root DIR`,
`--json-out FILE` (default `~/.claude/tokenomy/insights.json`),
`--pricing-file FILE`, `--no-report`, `-v`.

Outputs a console report and a full JSON file with per-tool/per-project
breakdowns, top-50 outliers, and recommendation list. Cache reads are excluded
from the headline cost (Claude Code reports them cumulatively per turn, which
double-counts when summed across sessions). Costs are estimates — see
`console.anthropic.com` for exact billing.

---

## Verifying it works

After install:
1. Check the statusline — should show a live token counter.
2. Ask Claude to read the same file twice. Second read should be blocked with a `[tokenomy]` message.
3. Ask Claude to read any `.log` file >200 lines. It should see a `[tokenomy] Log filtered:` header instead of the full file.
4. Run `env | grep CLAUDE` in a Bash tool call — should show the 6 tokenomy env vars.

---

## Uninstall

```
/plugin uninstall tokenomy
```

All hooks and env vars are scoped to the plugin. Uninstalling removes them cleanly. Your own `~/.claude/settings.json` is never touched.

---

## License

MIT. See `LICENSE`.

## Author

Ionuț Roșu — [github.com/numarulunu](https://github.com/numarulunu)
