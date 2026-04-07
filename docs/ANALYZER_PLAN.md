# tokenomy analyzer — Implementation Plan

**Project:** tokenomy (Claude Code plugin, `github.com/numarulunu/tokenomy`)
**Local path:** `C:\Users\Gaming PC\Desktop\Claude\tokenomy\`
**Owner:** Ionuț Roșu
**Purpose:** Retroactively analyze all of Ionuț's past Claude Code sessions to produce real token + dollar waste reports, and recommend provably-safe settings based on actual usage distributions.

---

## Context (read first if compacted)

tokenomy v0.1.0 is already shipped: 6 env vars, ccusage statusline, read-once hook, log-grep hook, cleanup hook, templates, token-audit skill, CLAUDE.md rules. See `README.md`.

This plan adds v0.1.1 (trivial) and v0.2.0 (the analyzer).

**v0.1.1** = four zero-risk `DISABLE_*` env vars. Ship first as warm-up. 5 minutes.

**v0.2.0** = a one-shot Python script that walks `~/.claude/projects/**/*.jsonl`, computes token + dollar waste distributions, and writes an insights report. Headline feature: **counterfactual savings in real USD** — "setting X would have saved you 2.3M tokens / $34.50 over the last 30 days."

### Why this is possible

Claude Code stores every session as JSONL at `~/.claude/projects/<project-hash>/*.jsonl`. Probe already confirmed:
- **2,214 files**, **469 MB**, **1,796 real sessions** >50KB.
- Line format: `{type, message, timestamp, sessionId, ...}`. Types: `user`, `assistant`, `system`, `file-history-snapshot`, `queue-operation`, `attachment`, `last-prompt`.
- Assistant messages include exact `message.usage`: `input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`, plus `message.model` (e.g. `claude-opus-4-6`).
- Tool calls live in `message.content[]` as `tool_use` items; results as `tool_result` items with full `content`.

This means per-message exact pricing is possible, not estimates.

---

## v0.1.1 — `DISABLE_*` flags (warm-up, ~5 min)

Add to `settings.json` `env` block:

```json
"CLAUDE_CODE_DISABLE_BUG_COMMAND": "1",
"DISABLE_ERROR_REPORTING": "1",
"DISABLE_AUTOUPDATER": "1",
"DISABLE_TELEMETRY": "1"
```

Commit message: `feat: v0.1.1 — disable non-essential features (bug command, error reporting, autoupdater, telemetry)`. Bump `plugin.json` version to `0.1.1`. Tag `v0.1.1`. Push. Create release with short notes.

Done. Move to v0.2.0.

---

## v0.2.0 — analyzer

### File layout additions

```
tokenomy/
├── analyzer/
│   ├── __init__.py
│   ├── analyze.py          # entrypoint: walks sessions, writes insights
│   ├── pricing.py          # Anthropic model pricing table
│   ├── extractors.py       # parse jsonl lines, extract events
│   ├── report.py           # human-readable console report
│   └── counterfactual.py   # "what would X have saved" math
├── tests/
│   └── test_analyzer.py    # unit tests on synthetic jsonl fixtures
└── docs/
    └── ANALYZER_PLAN.md    # this file
```

### Entrypoint

```
python -m tokenomy.analyzer.analyze [--days 30] [--project PATH] [--json-out FILE] [--no-report]
```

Defaults: scan all sessions in last 30 days, print human report to stdout, write full insights to `~/.claude/tokenomy/insights.json`.

### Data extraction (`extractors.py`)

Stream jsonl line-by-line (never load whole files; one 16 MB file exists already). For each line:

1. Parse JSON; skip on error.
2. Extract `type`, `timestamp`, `sessionId`, `message`.
3. For `type == "assistant"`:
   - Record `(ts, session_id, project_hash, model, usage)` as a **usage event**.
   - Walk `message.content[]`. For each `tool_use` item, record `(ts, session_id, tool_name, input_summary)`. `input_summary` for `Read` = `(file_path, offset, limit)`; for `Bash` = command first 100 chars; for MCP tools = server + tool name.
4. For `type == "user"` with tool results:
   - Walk `message.content[]`. For each `tool_result` item, record `(ts, session_id, tool_use_id, response_size_bytes, truncated_flag)`. Size = `len(str(content))` after flattening list content. Truncation flag = substring match on "Response truncated" or "[truncated]".
5. For `type == "system"`:
   - Detect compact events (look for "compact" in content).

Project hash = the immediate parent directory name under `~/.claude/projects/`. Keep it as-is; no need to reverse.

### Pricing (`pricing.py`)

Hardcode Anthropic published pricing, USD per 1M tokens. Include knowledge-cutoff note + a `PRICING_UPDATED_AT = "2026-04"` constant so future Claude knows whether to refresh it.

```python
PRICING = {
    "claude-opus-4-6":       {"input": 15.00, "output": 75.00, "cache_write": 18.75, "cache_read":  1.50},
    "claude-opus-4-6[1m]":   {"input": 15.00, "output": 75.00, "cache_write": 18.75, "cache_read":  1.50},
    "claude-sonnet-4-6":     {"input":  3.00, "output": 15.00, "cache_write":  3.75, "cache_read":  0.30},
    "claude-haiku-4-5":      {"input":  1.00, "output":  5.00, "cache_write":  1.25, "cache_read":  0.10},
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00, "cache_write": 1.25, "cache_read": 0.10},
}
DEFAULT_PRICING_KEY = "claude-sonnet-4-6"  # fallback for unknown models
```

Cost per message:
```
cost = (input_tokens       * pricing["input"]       +
        cache_creation_tok * pricing["cache_write"] +
        cache_read_tok     * pricing["cache_read"]  +
        output_tokens      * pricing["output"]) / 1_000_000
```

**IMPORTANT:** the knowledge-cutoff model list may be stale by the time this runs. If a model name is missing from `PRICING`, log a warning and fall back to `DEFAULT_PRICING_KEY`. Also expose a `--pricing-file` flag so the user can override with their own JSON.

### Aggregations (`analyze.py`)

After streaming all sessions, compute:

1. **Per-tool response size distributions**: p50, p95, p99, max, count, total_bytes. Group by `tool_name`. Separate bucket for MCP tools (`mcp__*`).
2. **Duplicate read detection**: count of `Read(path, offset, limit)` calls where the identical key appears ≥2 times in a single session. This proves read-once hook value retroactively.
3. **Log read bloat**: count of `Read` calls where `file_path` matches log glob AND result size > 20,000 chars. Proves log-grep hook value.
4. **Per-tool total cost**: sum `output_tokens * output_price` for each assistant turn attributable to a tool call (approximate — use the next assistant message after a tool_result as the "reaction cost").
5. **Top 50 waste events**: biggest individual tool_result sizes with metadata (tool, project, ts).
6. **Time-of-day patterns**: bucket tool_result sizes by hour-of-day.
7. **Per-project aggregates**: everything above, grouped by project_hash.
8. **Session-level stats**: avg tool calls per session, compact count, total tokens, total cost per session.
9. **Trend over time**: 7-day rolling total spend.

### Counterfactual engine (`counterfactual.py`)

For each proposed setting change, compute what it would have saved on the historical record. Input: list of `(setting_name, proposed_value)`. Output: `{tokens_saved, dollars_saved, losses_incurred}` where losses = count of responses that would have been cut mid-useful-content.

Rules per setting:

- **`MAX_MCP_OUTPUT_TOKENS = N`** — for each MCP tool_result with size > N*4 chars (4 chars ≈ 1 token rough estimate, refine via tiktoken if available), savings = `(size - N*4)`. Losses = count where Claude's next message re-queried the same tool (signal that truncation broke the flow).
- **`CLAUDE_CODE_MAX_OUTPUT_TOKENS = N`** — for each assistant message where `output_tokens > N`, savings = `(output_tokens - N) * output_price`. Losses = count where assistant message ends in partial code (heuristic: ends with unclosed backtick, unmatched brace, or "let me continue").
- **`CLAUDE_AUTOCOMPACT_PCT_OVERRIDE = P`** — harder to counterfactually compute without the exact ctx-window behavior. Report only: "current autocompact events = X, at P=50 would have been Y earlier compacts." Do not estimate savings for this one. Mark as advisory.
- **`read-once` hook enabled** — sum of bytes in duplicate Read calls within same session → direct savings figure. No losses.
- **`log-grep` hook enabled** — sum of `(size - 5000)` for each log read where size > 5000 chars (5000 is rough filtered-content ceiling). Losses = 0 because `!fulllog` is the escape.

All savings converted to dollars using the model that was actually used for the message where the save would have occurred.

### Human report (`report.py`)

Print a clean terminal report in sections. Keep it under 60 lines.

```
════════════════════════════════════════════════════════
 tokenomy analyzer — 30-day insights
════════════════════════════════════════════════════════

 Period:       2026-03-08 → 2026-04-07
 Sessions:     342
 Total tokens: 187.2M (in: 162M · out: 25.2M · cached: 145M)
 Total spend:  $184.73
 Daily avg:    $6.16

 TOP 5 TOKEN SINKS (by total bytes returned)
 ─────────────────────────────────────────────────
  1. mcp__serena__find_symbol         42.1M   $6.31
  2. Read                             31.8M   $4.77
  3. mcp__context7__query-docs        22.4M   $3.36
  4. Bash                             18.2M   $2.73
  5. Grep                              9.1M   $1.36

 COUNTERFACTUAL — "what you would have saved"
 ─────────────────────────────────────────────────
  MAX_MCP_OUTPUT_TOKENS=8000    tokens: 14.2M   $  21.30   losses: 3
  MAX_MCP_OUTPUT_TOKENS=5000    tokens: 22.8M   $  34.20   losses: 18
  MAX_OUTPUT_TOKENS=6000        tokens:  1.4M   $  10.50   losses: 0
  read-once (already on)        tokens:  8.9M   $  13.35   losses: 0
  log-grep (already on)         tokens:  3.2M   $   4.80   losses: 0

 RECOMMENDATIONS
 ─────────────────────────────────────────────────
  ✓ Set MAX_MCP_OUTPUT_TOKENS=8000 (saves ~$21/mo, only 3 losses)
  ✗ Do NOT set MAX_MCP_OUTPUT_TOKENS=5000 (18 losses — hurts quality)
  ✓ Set CLAUDE_CODE_MAX_OUTPUT_TOKENS=6000 (zero losses, ~$10/mo)

 TOP 5 OUTLIER TOOL CALLS (single responses >15k tokens)
 ─────────────────────────────────────────────────
  1. mcp__serena__get_symbols_overview  38.2k tokens  Kontext  2026-04-02
  2. Read  /kontext/docs/smac-reports.md  31.1k tokens  Kontext  2026-04-01
  …

 Full insights written to: ~/.claude/tokenomy/insights.json
```

### Output JSON schema (`insights.json`)

```json
{
  "generated_at": "2026-04-07T23:45:00Z",
  "tokenomy_version": "0.2.0",
  "period": {"start": "2026-03-08", "end": "2026-04-07", "sessions": 342},
  "totals": {"input_tokens": 162000000, "output_tokens": 25200000, "cache_read_tokens": 145000000, "cache_creation_tokens": 12000000, "cost_usd": 184.73},
  "by_tool": {
    "mcp__serena__find_symbol": {"count": 412, "total_bytes": 42100000, "p50": 820, "p95": 4200, "p99": 12000, "max": 38200, "est_cost_usd": 6.31},
    "...": {}
  },
  "by_project": {"Kontext": {...}, "tokenomy": {...}},
  "by_hour": {"0": 1200, "1": 800, "...": 0},
  "counterfactuals": [
    {"setting": "MAX_MCP_OUTPUT_TOKENS", "value": 8000, "tokens_saved": 14200000, "dollars_saved": 21.30, "losses": 3}
  ],
  "outliers": [{"tool": "...", "size": 38200, "project": "Kontext", "ts": "..."}],
  "duplicate_reads": {"count": 1240, "bytes": 8900000, "dollars": 13.35},
  "recommendations": [
    {"setting": "MAX_MCP_OUTPUT_TOKENS", "value": 8000, "reason": "...", "confidence": "high"}
  ]
}
```

---

## Build order (clean session execution)

1. **Read this plan.** Check `C:\Users\Gaming PC\Desktop\Claude\tokenomy\` exists and has the v0.1.0 files.
2. **Ship v0.1.1.** Edit `settings.json` to add the 4 DISABLE flags. Bump `plugin.json` to `0.1.1`. Commit `feat: v0.1.1 — disable non-essential features`. Tag `v0.1.1`. Push. Release.
3. **Create `analyzer/` dir** with empty `__init__.py`.
4. **Write `pricing.py`** — the table + cost function. Trivial.
5. **Write `extractors.py`** — streaming jsonl parser. Write a test fixture file first (synthetic 5-line jsonl covering assistant w/ usage, user w/ tool_result, tool_use, compact event). Unit test extractor on fixture.
6. **Write `analyze.py`** — walk + aggregate. CLI flags. Test on a single real session file first (pick one <1 MB), then small sample, then full scan.
7. **Write `counterfactual.py`** — the savings math. Unit test each setting rule on fixture data.
8. **Write `report.py`** — format console output. Test by redirecting to stdout.
9. **Wire it up.** Run full `python -m tokenomy.analyzer.analyze` on Ionuț's real 469 MB corpus. Expect runtime <60s (pure stream, no LLM calls). Memory should stay <200 MB — if it doesn't, the aggregation is loading too much; switch to incremental updates.
10. **Verify numbers sanity-check.** Total tokens reported should roughly match ccusage history if available. Dollar total should match his Anthropic billing roughly (within ±15% — exact match impossible without API Console export).
11. **Iterate on report format** based on what the real data shows. The table columns above are my guesses — let the data shape the report.
12. **Bump `plugin.json` to `0.2.0`.** Update `README.md` with an "Analyzer" section. Commit `feat: v0.2.0 — session history analyzer with USD counterfactuals`. Tag `v0.2.0`. Push. Release.

---

## Test plan

### Unit tests (`tests/test_analyzer.py`)

Use pytest fixtures containing hand-built synthetic jsonl with:
- 1 session, 3 assistant messages, 2 tool calls, 1 duplicate Read, 1 log read >20k, 1 compact event.
- Known total token counts so counterfactual math can be asserted exactly.

Test cases:
1. `test_extractor_handles_malformed_lines` — inject garbage, assert skipped.
2. `test_pricing_unknown_model_falls_back` — model name not in table → uses default.
3. `test_counterfactual_mcp_cap` — synthetic fixture with known sizes, assert dollar delta.
4. `test_counterfactual_max_output` — assert loss detection catches mid-code endings.
5. `test_read_once_savings_dedup` — 3 identical Reads in one session → savings = 2x size of one.
6. `test_log_grep_savings_threshold` — only counts reads >5000 chars.
7. `test_project_aggregation` — two synthetic project dirs, stats separated.
8. `test_empty_corpus` — no files → clean report, no crash.
9. `test_corrupt_session_file` — truncated jsonl mid-line → processes valid lines, skips bad.

### Integration tests

1. Run against a single real session file of Ionuț's. Verify no crashes, output sensible.
2. Run against the full 469 MB corpus. Assert runtime <60s, memory <200 MB.
3. Run twice — verify deterministic output (same insights.json both runs).

---

## Edge cases and gotchas

1. **Windows paths in session data.** File paths in Read tool inputs are Windows-style (`C:\\...` or `C:/...`). Normalize before dedup: lowercase + forward slashes + realpath where possible.
2. **Huge single file.** Already confirmed 16 MB files exist. Never load whole files. Use `for line in open(...)` streaming. Don't `f.readlines()`. Don't `json.loads(f.read())`.
3. **Memory pressure on aggregation.** 2214 sessions × hundreds of events each = millions of events. Don't keep them all in memory as dicts. For distributions, use running percentile approximations (e.g., `numpy.percentile` on sampled chunks, or a `heapq` top-N tracker) rather than sorting all sizes.
4. **Tool result content can be a list of blocks.** `tool_result.content` is sometimes a string, sometimes a list of `{type: text, text: "..."}` blocks. Flatten both before sizing.
5. **Model name drift.** New models will appear. Log unknowns. Don't crash.
6. **Truncation detection is heuristic.** We only catch obvious cases. Document this — counterfactual losses are a floor estimate, not exact.
7. **Session boundary.** `sessionId` in jsonl lines identifies the conversation. Use it for within-session dedup (read-once logic), not filename.
8. **Cache pricing is discounted.** Don't count cache reads at full input price. That would 10x the reported spend. The pricing table above uses the discounted rate.
9. **`<synthetic>` model** — saw 12 events with `model: "<synthetic>"` in probe. These are system-injected messages. Skip for cost calc.
10. **Cross-project tool calls.** A tool call happens in a specific session, which lives in a specific project dir. Attribution is clean. No ambiguity.
11. **tiktoken optional.** If `tiktoken` is installed, use it for accurate char→token conversion in counterfactual math. If not, use `chars / 4` rough estimate and note the uncertainty in the report.
12. **Pricing freshness.** Hardcoded prices can rot. The `PRICING_UPDATED_AT` constant flags this. On first run, warn if constant is >90 days old.

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Memory blowup on 469 MB corpus | Stream everything, running percentiles, top-N heaps |
| Wrong pricing → wrong dollars | `PRICING_UPDATED_AT` warning + `--pricing-file` override |
| Counterfactual loss detection too naive | Document as "floor estimate", tune heuristics on real data in iteration |
| Session format changes in future Claude Code versions | Extractor fails gracefully per-line; report "skipped N malformed events" |
| User reads this as exact billing ledger | Print disclaimer: "estimate, not authoritative — compare to console.anthropic.com for exact" |
| Analyzer accidentally modifies session files | **Read-only.** Never open jsonl files in write mode. Enforce via unit test that asserts no writes happen to `~/.claude/projects`. |
| Privacy / sensitive content in reports | Outlier sample shows tool + size + project + ts ONLY. Never dump tool_result content into the report. |

---

## Non-goals (v0.2.0)

- **No auto-tuning.** Reports only. Applying recommendations is v0.3.0.
- **No per-project tuned env files.** v0.3.0.
- **No live daemon / watcher.** One-shot script only. Scheduled re-analysis is v0.2.1.
- **No upload / sharing.** Fully local.
- **No LLM-based analysis of content.** Pure statistics. No API calls.
- **No GUI.** Terminal report + JSON file.
- **No autocompact counterfactual dollars.** Too hard to model accurately — report as advisory only.

---

## What to tell Claude in the next session

> Read `C:\Users\Gaming PC\Desktop\Claude\tokenomy\docs\ANALYZER_PLAN.md` and execute it from step 1. Ship v0.1.1 first (trivial warm-up), then build the analyzer per the plan. Stop after step 11 (real-data dry run) for review before tagging v0.2.0.

That's it. The plan is self-contained.
