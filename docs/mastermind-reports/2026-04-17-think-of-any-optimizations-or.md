# Mastermind Report: Optimizations/Features for Tokenomy

**Generated:** 2026-04-17 | **Council:** 7 (3 fixed + 4 dynamic) | **Challengers:** 7 | **Spec agents:** 3

> ⚠ **Note:** Mid-run compaction truncated full council reports. Reconstructed briefs passed to challengers from preserved top-findings + confidences. Scoring & synthesis reflect this degraded fidelity — treat scores as directional.

## The Idea
Think of any optimizations or features worth adding for Tokenomy.

## Ranked Approaches

| # | Approach | Score | Supported By | Verdict |
|---|----------|-------|-------------|---------|
| 1 | **Harden the Core — Round 2** | **2.32** | 4 members (Tech, Critical, DX, Cost) | VIABLE |
| 2 | Close the Tuner Loop | 2.09 | 3 members (Telemetry, Cost, Product) | VIABLE |
| 3 | Make Visible & Trustworthy | 2.05 | 3 members (DX, Product, Tech) | VIABLE |
| 4 | Ecosystem Expansion | 1.76 | 3 members (Ecosystem, Cost, Product) | RISKY |

## Winning Approach: Harden the Core — Round 2

### Summary
Round 1 (2026-04-10) fixed **behavioral correctness** — control loop, first-run consent, pre-cap baseline, backup rotation. All shipped. Round 2 fixes **observability trust**: the tool tunes fine, but fails silently in enough places that the user can't tell whether the numbers on screen are real. Ten gaps surfaced; six are MVP for v0.7.0.

### Why This Won
Highest-scored items survived challenger scrutiny with STRONG verdicts and appeared across multiple council angles (e.g., silent pricing fallback flagged by both Tech Architect and DX Specialist — breadth bonus). Approach #2 ("Close the Tuner Loop") was close but hit FLAWED verdicts on its signature items (fetch-to-result utilization ratio, user-revert detection via raw file diff) — the telemetry thesis is right but the specific signals proposed need more design work. Round 2 is the faster path to shippable value.

---

## Product Spec

### MVP (v0.7.0)

| # | Item | Why non-negotiable |
|---|------|--------------------|
| 1 | OAuth `_parse` schema validation | One Anthropic API shape change silently breaks statusline for every user |
| 2 | Pricing staleness indicator + log | Silent fallback to stale defaults distorts every cost signal |
| 3 | `detect_error_after_cap` — scope cap-related errors only | False positives on ENOENT/timeout inflate the tuner's core signal; corrupts control loop |
| 4 | `insights.json` atomic write | Half-written file crashes the statusline mid-session |
| 5 | `⚠ [ERR]` indicator in statusline | Silent partial renders are the #1 trust erosion vector |
| 6 | Global `TOKENOMY_OFF=1` killswitch | Debug escape valve; must preserve `TOKENOMY_DISABLE_USAGE_FETCH` compat |

### V2 (deferred)
- Schema migration dispatcher (low urgency — stub holds until real schema break)
- Burn thresholds per-model (quality-of-life — needs model-detection probe first)
- `DEFAULT_MCP_ALLOW` vs `detect_unused_mcp` interaction cleanup
- Savings attribution in statusline (baseline validation problem — blocked)
- Per-project caps (from Product Strategist — unblocked when classifier heuristic designed)
- Subagent cost attribution (from Cost Analyst — needs parent-session tagging spec)

### User Stories
1. As a solo developer, I want OAuth parse errors to surface so I know whether statusline cost figures are live or stale.
2. As a solo developer, I want `[ERR]` on any module failure so I never read a silent partial render as truth.
3. As a tuner author, I want cap-detection to ignore ENOENT/timeout so the control loop only loosens on real cap events.
4. As a debugger, I want a single `TOKENOMY_OFF=1` to disable the entire plugin without editing JSON.
5. As a cost-conscious user, I want a staleness tag on pricing so I know the age of fallback data.
6. As a support engineer, I want OAuth schema validation errors in the log with raw payload so I can file a bug without guessing.

### Success Metrics
| Metric | Target |
|---|---|
| Silent partial statusline renders in next 30 days dogfood | 0 |
| Cap-detection false positive on injected ENOENT/timeout test suite | 0 |
| `insights.json` corruption after 10-run parallel stress test | 0 |
| Pricing fallback events reaching user without a log line | 0 |
| OAuth schema validation rejects malformed fixture (unit test) | PASS |

### Competitive Edge vs Round 1
Round 1: "does it work?" Round 2: "does it tell the truth about itself?" `[ERR]` statusline, pricing staleness, parse schema validation, error-scope narrowing — all answer the same question for the user. That is the new trust vector.

---

## Technical Spec

### Phase 1 — LIGHT (zero-risk, surgical)

| # | File | Change |
|---|------|--------|
| 1 | `analyzer/pricing.py` | Add `log.warning` at `DEFAULT_PRICING_KEY` fallback with `"pricing table is %d months old — override with --pricing-file"`. One line. |
| 2 | `analyzer/extractors.py` + `analyzer/analyze.py` | Tag error events within 30s of a compaction as `source="post_cap_noise"`; filter in `analyze.py` before scoring. |
| 3 | Global killswitch: `TOKENOMY_OFF=1` | Early-return gates at `tuner/tuner.py:main()`, `analyzer/analyze.py` main, `hooks/statusline.py` main (before `render()`), `hooks/usage_fetcher.py` fetch path. Four files, four lines. |
| 4 | `analyzer/analyze.py:571` (insights.json) | Replace `open('w')` with `write tmp + os.replace()`. Two lines. |

### Phase 2 — MODERATE (schema + logic)

**5. OAuth schema validation** (`hooks/usage_fetcher.py:76`)
Recommendation: **hand-rolled dict guard** (zero new deps — consistent with existing `isinstance` pattern). Add `_validate_usage_entry(u: dict) -> bool` asserting required keys + types. Reject whole entry (return `None`) rather than silent zero-fill. Log raw payload on reject. ~15 lines.

**6. Statusline `⚠ [ERR]` indicator** (`hooks/statusline.py`)
Module-level `_RENDER_ERROR: str | None`. Assign in each `except` block. In `render()`, append `⚠` glyph + short code to output. **Alert-fatigue mitigation:** show only after N≥3 consecutive failures in a rolling window — single transients stay silent.

### Phase 3 — V2 (after MVP ships)

**7. Schema migration dispatcher** (`tuner/state.py`)
```python
_MIGRATIONS: dict[str, Callable[[dict], dict]] = {}

def migrate(data, from_version, to_version):
    for step in [s for s in _MIGRATIONS if from_version < s <= to_version]:
        data = _MIGRATIONS[step](data)
    data["version"] = to_version
    return data
```
Populated by `@migration` decorators; empty until a real schema break. Call from `load_state()` at the version mismatch branch. Test lives in `tests/test_state.py`.

**8. Burn thresholds per-model** (`tuner/tuner.py`)
`MODEL_BURN_THRESHOLDS: dict[str, float]` keyed by model-family prefix (`"claude-opus"`, `"claude-sonnet"`, `"claude-haiku"`). Requires model-detection probe first (see Risk).

**9. DEFAULT_MCP_ALLOW cleanup** (`tuner/auto_rules.py`)
Wire `session_events_map` into `auto_rules.py`; compute allow-list from actual usage rather than hardcoded constant.

### Stack
Pure Python + bash. No new deps. Windows-clean (`pathlib.Path.home()` only, no bare `~/`).

### Implementation Phase Graph
```
Phase 1 (1,2,3,4) → independent, ship together as a single PR
Phase 2 (5,6)     → independent of Phase 1, ship as second PR
Phase 3 (7,8,9)   → deferred to v0.8; 8 depends on model-detection probe
```

---

## Risk Analysis

### Risk Register
| Risk | L | I | Mitigation |
|---|---|---|---|
| OAuth schema validation false-rejects real API response | HIGH | HIGH | Defensive validation: only assert *required* keys/types; unknown fields pass through. Log schema hash on each fetch so drift is detectable in logs. |
| `detect_error_after_cap` filter over-broad — swallows real errors | MED | HIGH | Filter by specific error codes/patterns, not a catch-all. Explicit allowlist, not denylist. |
| `insights.json` concurrent write still corrupts despite atomic swap | LOW | HIGH | Atomic write sufficient because only `analyze.py` writes, `statusline.py` reads. Verify with 10-run parallel stress test before ship. |
| `TOKENOMY_OFF` breaks existing `TOKENOMY_DISABLE_USAGE_FETCH` users | MED | MED | Killswitch reads both keys; legacy maps to granular scope. No breaking change. |
| `[ERR]` statusline fires on transient network blips → alert fatigue | MED | MED | N≥3 consecutive failures in rolling window before showing. Single failures silent. |
| Pricing staleness log becomes noise | LOW | LOW | Log once per fallback event, not per render. |
| Windows path regression in new Round 2 code | MED | HIGH | Ban bare `~/` and `os.path.expanduser` in new code. `pathlib.Path.home()` only. |
| CLAUDE.md/config edits during Round 2 nuke prompt cache mid-session | HIGH | MED | Document "restart required" for any BASELINE_ENV change. No mid-session writes. |
| Schema migration dispatcher corrupts state during future upgrade | MED | HIGH | Dispatcher is v0.8 work; test with populated fixture before first real migration lands. |

### Kill Conditions
1. `insights.json` corruption rate > 0 in Windows stress test → revert atomic write, redesign with portalocker.
2. OAuth schema validation false-rejects a production response → revert to unvalidated pass-through + logging-only.
3. `[ERR]` indicator fires in >5% of statusline renders during first week dogfood → threshold too low, retune or ship silent again.

### Biggest Unknown
**Model detection source of truth.** No documented reliable signal at hook time (env var may lag, transcript header may be absent). Burn thresholds per-model depends on this. **Pre-ship probe (30 min):** print detected model from env, transcript, CLAUDE_MODEL header across 5 invocation modes (CLI, MCP, background hook, `--resume`, piped stdin). Kills or validates per-model thresholds before Round 2 ships any dependent code.

### Scope Creep Traps (defer)
- Per-project caps — needs project classifier; unblocked later.
- Savings attribution in statusline — baseline validation unsolved (Challenger: "trend of a bad metric is a worse metric").
- Automated schema hash alerting — log it, don't build alert plumbing.
- Migration rollback UI — one-way + backup is enough.
- Granular `[ERR]` taxonomy — threshold gating sufficient until users ask.

---

## Challenger Highlights

1. **vs Product Strategist:** Savings attribution and historical chart called FLAWED — "a trend of a bad metric is a worse metric. Build after baseline is validated." Moved savings attribution to V2 with baseline dependency.
2. **vs Critical Challenger:** `statusline.py 862 lines` called FLAWED — "line count is not a risk; it's a smell." Refactor dropped from scope.
3. **vs Cost Economics Analyst:** Auto model-routing called FLAWED — "you'd spend Haiku tokens to decide whether to spend Haiku tokens." Manual routing stays in CLAUDE.md; no automation proposed.
4. **vs Telemetry Loop Architect:** MCP fetch-to-result ratio and user-revert detection both FLAWED — informed deferral of Approach #2.
5. **vs DX Specialist:** Sticky daily feature called FLAWED — "Tokenomy is a passive plugin, not an app. Reliability drives retention, not engagement loops." Confirmed MVP focus on trust signals, not engagement hooks.
6. **vs Ecosystem Strategist:** UserPromptSubmit complexity classifier FLAWED — conflicts with CLAUDE.md "no /model mid-session." Prometheus endpoint WEAK — no collector. Both dropped.
7. **vs Technical Architect:** CI/mypy without test runner = vaporware. Scoped to "standalone mypy run" if pursued; dropped from MVP.

## Coverage Gaps
| Role | Status | Impact |
|------|--------|--------|
| Product Strategist | Challenged | 2 items in MVP (MCP truncation via `[ERR]`, killswitch rollback via `TOKENOMY_OFF`) |
| Technical Architect | Challenged | 3 items in MVP (pricing log, insights atomic, schema migration deferred V2) |
| Critical Challenger | Challenged | 4 items in MVP (OAuth validation, detect_error filter, killswitch, `[ERR]` indicator) |
| Telemetry Loop Architect | Challenged | Mostly deferred — signals need design work |
| Cost Economics Analyst | Challenged | Cache hit alerting in V2; premature compact already partially shipped |
| DX Specialist | Challenged | `[ERR]` indicator, pricing staleness, killswitch all direct hits |
| Ecosystem Strategist | Challenged | Deferred — most items conflict with stated policy or need infra |

## Post-Compaction Notes
Full council reports were lost when context compacted mid-run. Challenger inputs reconstructed from preserved top-findings + confidence scores. A fresh rerun with preserved council text would likely promote 1-2 deferred items based on richer proposal detail; current MVP is the conservative reading.
