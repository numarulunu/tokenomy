# firebreak — Implementation Plan

**Project:** firebreak — a Claude Code plugin that cuts token burn 80%+ via env vars, hooks, statusline, and project templates.
**Owner:** Ionuț Roșu (GitHub: numarulunu)
**License:** MIT
**Format:** Claude Code plugin (`.claude-plugin/plugin.json`), distributed via GitHub marketplace source.
**Repo path:** `C:\Users\Gaming PC\Desktop\Claude\firebreak\`
**Target install command for friends:**
```
/plugin marketplace add github:numarulunu/firebreak
/plugin install firebreak
```

---

## Context (read this first if compacted)

This plan exists because the user (Ionuț, Kontext project) is consolidating ~10 token-optimization techniques into a single shareable plugin. Already-applied optimizations live in his `~/.claude/CLAUDE.md`, `~/.claude/settings.json`, and `Kontext/.claude/settings.json` — those are the *source of truth* for what firebreak should ship. The plugin is the portable version of his personal stack.

Already done in his global config (do NOT re-implement, just port into plugin):
- 5 env vars: `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=70`, `MAX_THINKING_TOKENS=8000`, `CLAUDE_CODE_MAX_OUTPUT_TOKENS=8000`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, `ENABLE_TOOL_SEARCH=true`
- ccusage statusline wired
- Serena MCP installed globally
- Kontext `.claudeignore` template
- Kontext `.claude/settings.json` template (respectGitignore: true + permissions)
- CLAUDE.md additions: output discipline, model routing, subagent delegation, /rewind preference, project bootstrap rule, "Serena first for code"

Hooks C and D (this plan) are the headline new feature — they're what turns firebreak from "config snippets" into a real token-saving runtime layer.

---

## Hook C — read-once

### Goal
Block redundant Read tool calls. If Claude tries to read a file it has already read in the current session AND the file's mtime hasn't changed, return a stub message instead of re-injecting the file content.

### Mechanism
- **Hook type:** `PreToolUse`
- **Matcher:** `Read`
- **Implementation:** bash script that maintains a per-session JSON cache at `~/.claude/firebreak/read-cache-${SESSION_ID}.json` mapping `{path: mtime}`.
- On invocation:
  1. Parse `$ARGUMENTS` JSON to extract the file path Claude wants to read.
  2. `stat` the file to get current mtime.
  3. Look up path in cache. If present and mtime matches → return JSON stub: `{"decision": "block", "reason": "[firebreak] You already read this file at <timestamp> and it has not changed since. Use the version already in your context. If you genuinely need a fresh view, prepend '!fresh' to your read intent."}`
  4. Otherwise: write `{path: mtime}` to cache, return `{"decision": "approve"}`.
- **Session isolation:** session ID comes from Claude Code's `$CLAUDE_SESSION_ID` env var (verify this exists; if not, fall back to a hash of the cwd + start time).
- **Cache cleanup:** on SessionEnd hook, delete the per-session cache file.

### Edge cases to handle
- Read with `offset`/`limit` — different ranges of the same file are *different reads*. Cache key must be `(path, offset, limit)` not just `path`.
- File doesn't exist (Claude is trying to read a typo) — pass through, let native tool fail naturally.
- Symlinks — resolve to canonical path before caching.
- Windows paths — git-bash on Windows uses forward-slash paths; normalize before keying.
- User wants to bypass — check if the read intent contains a literal `!fresh` token (rare, escape hatch).
- Cache file corruption — on parse error, delete cache and approve (fail-open, never block legitimate reads).

### Test plan
1. Throwaway test repo with 3 files (a.py, b.py, c.py).
2. Spawn Claude Code session, ask it to read all three. Verify cache file populated.
3. Ask it to read a.py again. Verify hook blocks with stub message.
4. Modify a.py externally (touch -m). Ask it to read a.py. Verify hook approves (mtime changed).
5. Read a.py with offset/limit, then full read. Verify both succeed (different cache keys).
6. End session. Verify cache file deleted.
7. Run on Windows + Linux to confirm path normalization.

### Known failure mode
If `$CLAUDE_SESSION_ID` is empty or unstable across hook invocations within the same session, cache becomes worthless. Step 0 of implementation: write a one-line probe hook that just `echo $CLAUDE_SESSION_ID >> /tmp/probe.log` and confirm it's stable. If unstable, fall back to PID of parent or to a session-detection heuristic.

### Estimated tokens saved
30-40% of total session burn in long sessions. Highest impact in debugging sessions where the same files get re-read 5-10 times.

---

## Hook D — log-grep preprocessor

### Goal
Intercept Read calls targeting log files. Instead of returning the full log, return only error/warning lines + the last 50 lines for trailing context.

### Mechanism
- **Hook type:** `PreToolUse`
- **Matcher:** `Read`
- **Implementation:** bash script.
- On invocation:
  1. Extract path from `$ARGUMENTS`.
  2. Match path against log glob: `*.log`, `_*.log`, `**/log/**`, `**/logs/**`. If no match → approve, exit.
  3. Check user prompt for escape token `!fulllog` — if present, approve, exit.
  4. Run: `grep -nE "(ERROR|WARN|FAIL|Exception|Traceback)" "$PATH" | tail -200 > /tmp/errors`
  5. Run: `tail -50 "$PATH" > /tmp/tail`
  6. Build a synthetic file content: header `[firebreak: log filtered. Showing matched errors + last 50 lines. Use !fulllog in your prompt to bypass.]` + errors + separator + tail.
  7. Return `{"decision": "modify", "modified_args": {"file_path": "/tmp/firebreak-filtered.log"}}` — write the synthetic content to that path first.
- Cleanup on SessionEnd.

### Edge cases
- Log is small (<200 lines) — pass through, no filtering needed.
- Log has zero error lines — return just the tail (last 50).
- Binary file with `.log` extension — detect via `file` command or first-byte check, pass through.
- Path doesn't exist — pass through.
- Hook can't write to /tmp on Windows — use `$TEMP` or `~/.claude/firebreak/tmp/`.
- Claude Code's `decision: "modify"` may not support modifying file_path directly — VERIFY this in the hook docs before relying on it. If unsupported, fallback approach: hook intercepts, runs the grep, returns the filtered content as a `decision: "block"` with a `reason` field containing the filtered text. Claude reads the reason as if it were the file content.

### Test plan
1. Generate a 10K-line synthetic log with 5 ERROR lines scattered throughout.
2. Ask Claude to read the log. Verify it gets the 5 ERROR lines + last 50.
3. Verify token count is ~500 instead of ~30K.
4. Ask Claude to read the log with `!fulllog` in the prompt. Verify full file returned.
5. Test with a 50-line tiny log. Verify pass-through.
6. Test with a binary file named `kontext.log` (edge case). Verify pass-through.

### Known failure mode
The `decision: "modify"` mechanic may not exist or may behave differently than assumed. The fallback "block + return filtered content as reason" is uglier but reliably works because all hooks support `decision: "block"` with a `reason` string. Plan A: try modify. Plan B: fall back to block-with-reason. Plan C if both fail: log a warning and approve, so at minimum the hook never blocks legitimate reads.

### Estimated tokens saved
15-25% of session burn for any session that touches logs (debugging, sync runs, dream cycles). Zero impact on sessions that don't read logs.

---

## Plugin file layout

```
firebreak/
├── .claude-plugin/
│   └── plugin.json              # manifest: name, version, description, author
├── hooks/
│   ├── read-once.sh             # hook C
│   ├── log-grep.sh              # hook D
│   └── cleanup.sh               # SessionEnd: clear caches
├── settings.json                # env vars + statusline + hook registrations
├── templates/
│   ├── .claudeignore            # universal exclude list
│   └── project-settings.json    # respectGitignore + permissions skeleton
├── skills/
│   └── token-audit/
│       └── SKILL.md             # on-demand: audit current project, create missing templates
├── CLAUDE.md                    # behavioral rules (output discipline, Serena-first, etc.)
├── README.md                    # before/after numbers, install, what each piece does
├── INSTALL.md                   # step-by-step + troubleshooting
├── LICENSE                      # MIT
└── .gitignore
```

---

## Build order (execute in this sequence on a clean session)

1. **Probe `$CLAUDE_SESSION_ID`** — write a 3-line PreToolUse hook that logs the env var, run one tool call, confirm it's stable. Decide cache-keying strategy based on result.
2. **Write `read-once.sh`** — implement, hook into a throwaway test repo first, run the 7-step test plan.
3. **Write `log-grep.sh`** — try `decision: modify` first, fall back to block-with-reason if unsupported. Run the 6-step test plan.
4. **Write `cleanup.sh`** — SessionEnd handler.
5. **Build plugin manifest** (`.claude-plugin/plugin.json`) — Claude Code plugin schema, name=firebreak, version=0.1.0.
6. **Port settings.json** — copy env vars + statusline from `~/.claude/settings.json`, register hooks.
7. **Copy templates** — `.claudeignore` and project settings from Kontext.
8. **Write `token-audit` skill** — checks for `.claudeignore` + project settings in cwd, creates if missing, reports findings.
9. **Write README** — include the bird's-eye-view table from this conversation (before / now / after C+D), install instructions, "what each piece does" section, screenshot of statusline.
10. **Write CLAUDE.md** — behavioral rules ported from `~/.claude/CLAUDE.md` (output discipline, model routing, Serena-first, project bootstrap).
11. **Init git, commit, create GitHub repo via `gh repo create numarulunu/firebreak --public --source=. --remote=origin --push`.**
12. **Test install on a second machine or fresh user dir** — `/plugin marketplace add github:numarulunu/firebreak && /plugin install firebreak`. Confirm everything wires up.
13. **Tag v0.1.0**, write release notes.

---

## What to tell Claude when starting the clean session

> Read `C:\Users\Gaming PC\Desktop\Claude\firebreak\docs\IMPLEMENTATION_PLAN.md` and execute it from step 1. Stop after step 4 (hooks built and tested) for review before proceeding to packaging.

That's it. The plan is self-contained.

---

## Risks and explicit non-goals

- **Risk:** hooks that block tool calls can hard-break a session if buggy. Mitigation: every hook fails open (on error → approve). Test on throwaway repos before merging into firebreak.
- **Risk:** ccusage dependency means npm/npx must be on user's PATH. Document in INSTALL.md.
- **Risk:** Serena dependency means uv must be installed. Document, but don't bundle (too heavy).
- **Non-goal:** firebreak does NOT ship Serena, ccusage, or any third-party tool. It only configures Claude Code to use them if present.
- **Non-goal:** firebreak does NOT touch the user's existing CLAUDE.md or settings.json. The plugin loads its config alongside, doesn't overwrite. (Plugin scope handles this automatically.)
- **Non-goal:** no Windows-specific or Linux-specific code paths if avoidable. Bash hooks must work on git-bash (Windows) and standard bash (Linux/macOS).
