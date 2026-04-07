# tokenomy v0.3.0 — Auto-Tuner Implementation Plan

**Project:** tokenomy (Claude Code plugin, `github.com/numarulunu/tokenomy`)
**Local path:** `C:\Users\Gaming PC\Desktop\Claude\tokenomy\`
**Owner:** Ionuț Roșu
**Goal:** Make tokenomy a true set-and-forget plugin that continuously tunes settings to the most aggressive values the user's data supports, dynamically tracking how their habits evolve.

---

## Context (read first if compacted)

- v0.1.1 shipped: 4 DISABLE flags + custom statusline.
- v0.2.0 shipped: read-only retrospective analyzer (`analyzer/`). 13 tests passing. Verified on 2,217 real session files. Headline cost calc excludes cache_read tokens (cumulative double-count).
- v0.3.0 = the auto-tuner. Reads the analyzer's outputs + a rolling per-session log, picks optimal caps with **recency-weighted percentiles over the full corpus**, and **merges its env caps directly into `~/.claude/settings.json`** (fenced by a `__tokenomy__` sentinel block, backed up once to `settings.json.tokenomy.bak`). Claude Code does NOT auto-load files under `~/.claude/<subdir>/`, and plugin `settings.json` only honors the `agent` key per the plugins doc — so a sidecar `auto-settings.json` would be inert. Async background, fail-open, self-correcting.

The user is a heavy Claude Code user with 647 sessions / 30 days. The plugin must work for him on day-one of v0.3.0 install (apply aggressive caps immediately) AND for a brand-new user with 5 sessions (apply only safe defaults until data accumulates).

---

## Design principles

1. **Servo, not ratchet.** Caps move in both directions as data evolves. Tighter when behavior shrinks, looser when behavior grows.
2. **Recency weighting via exponential decay.** Half-life = 14 days. New behavior dominates within ~10 days, old behavior fades within ~60.
3. **Confidence-driven aggression.** Low effective sample → conservative percentile + large margin. High effective sample → aggressive percentile + tight margin.
4. **Hysteresis prevents oscillation.** Tighten requires ≥10% change. Loosen requires ≥5%.
5. **Loss detection freezes settings.** Any detected loss → loosen the offending setting and freeze it for 14 days.
6. **Floors are absolute.** No setting ever crosses its hardcoded floor regardless of data.
7. **Fail-open hooks.** Tuner errors never block Claude Code. Worst case: defaults remain active.
8. **Per-MCP-server caps.** Don't apply Serena's distribution to Playwright. Group by server prefix.
9. **First-run aware.** If `applied.json` exists → incremental retune. If missing → full corpus scan in background.

---

## Tunable settings table

| Setting | Metric source | Floor | Notes |
|---|---|---|---|
| `MAX_MCP_OUTPUT_TOKENS` (per server) | tool_result sizes for `mcp__<server>__*` | 5000 | Per-server, not global |
| `CLAUDE_CODE_MAX_OUTPUT_TOKENS` | assistant `output_tokens` | 4000 | Whole-corpus distribution |
| `MAX_THINKING_TOKENS` | assistant thinking blocks if available | 2000 | Optional — may not be observable |
| `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` | per-session `max_context_tokens` as % of 200k | 25 | Lower = compact earlier |

Settings always-on regardless of data (Stage 0 baseline, already in v0.1.1):
- `DISABLE_TELEMETRY`, `DISABLE_AUTOUPDATER`, `DISABLE_ERROR_REPORTING`, `CLAUDE_CODE_DISABLE_BUG_COMMAND`
- `ENABLE_TOOL_SEARCH=true`
- read-once + log-grep hooks

---

## Recency weighting

```python
HALF_LIFE_DAYS = 14

def session_weight(age_days: float) -> float:
    return 0.5 ** (age_days / HALF_LIFE_DAYS)

def weighted_percentile(values_with_weights, p: float) -> float:
    pairs = sorted(values_with_weights, key=lambda x: x[0])
    total = sum(w for _, w in pairs)
    if total == 0:
        return 0.0
    target = p * total
    cum = 0.0
    for v, w in pairs:
        cum += w
        if cum >= target:
            return v
    return pairs[-1][0]
```

Confidence dial (drives aggression):

```python
def confidence(effective_n: float) -> float:
    return min(1.0, effective_n / 5_000)

def aggressive_percentile(conf: float) -> float:
    # 0.99 at low confidence → 0.95 at full confidence
    return 0.99 - 0.04 * conf

def margin(conf: float) -> float:
    # ×1.5 at low confidence → ×1.25 at full confidence
    return 1.5 - 0.25 * conf
```

Cap formula per setting:

```python
def compute_cap(samples_with_weights, floor: int) -> int:
    eff_n = sum(w for _, w in samples_with_weights)
    conf = confidence(eff_n)
    pct = aggressive_percentile(conf)
    m = margin(conf)
    p = weighted_percentile(samples_with_weights, pct)
    return max(int(p * m), floor)
```

---

## Hysteresis + cooldown + freeze

```python
TIGHTEN_THRESHOLD = 0.10   # new cap must be ≥10% smaller to apply
LOOSEN_THRESHOLD  = 0.05   # new cap must be ≥5% larger to apply
COOLDOWN_SESSIONS = 5      # after any change, that setting frozen for 5 sessions
LOSS_FREEZE_DAYS  = 14     # after a detected loss, that setting frozen 14 days

def should_apply(old: int, new: int) -> bool:
    if old == 0:
        return True
    delta = (old - new) / old  # positive = tightening
    if delta >= TIGHTEN_THRESHOLD:
        return True
    if delta <= -LOOSEN_THRESHOLD:
        return True
    return False
```

---

## Loss detection v2 (must be airtight)

Five detectors. Each ships with a paired (good fixture, bad fixture) pytest test that proves it fires on bad and stays silent on good.

1. **Truncation requery.** Tool A returns `is_error: true` OR contains truncation marker → assistant calls Tool A again within 2 turns with broader/different params.
2. **Mid-code endings.** Assistant message ends with unclosed `\`\`\``, unmatched braces (≥2 unclosed), or trailing "let me continue" / "...continuing" / "to be continued".
3. **Compact within 3 turns of cap-eligible event.** A tool_result that would have been capped is followed by an autocompact event within 3 assistant turns.
4. **Error tool_result after applied cap.** `is_error: true` on any tool_result whose tool_name matches a currently-capped setting.
5. **User manual override.** If user explicitly sets a tunable env var in their personal `~/.claude/settings.json`, tuner records it as "user-pinned" and never touches it.

Loss is recorded with:
```json
{"ts":"...","setting":"MAX_MCP_OUTPUT_TOKENS","server":"playwright","detector":"truncation_requery","value_at_loss":8000}
```

---

## File layout additions

```
tokenomy/
├── analyzer/                   # existing, v0.2.0
├── tuner/
│   ├── __init__.py
│   ├── weighting.py            # session_weight, weighted_percentile, confidence
│   ├── tuner.py                # compute_caps + apply logic + main entrypoint
│   ├── losses.py               # 5 loss detectors + paired fixtures wiring
│   ├── state.py                # applied.json read/write, atomic
│   └── settings_writer.py      # merges env caps into ~/.claude/settings.json
├── hooks/
│   ├── hooks.json              # plugin hook registrations (SessionStart, etc.)
│   └── session-start.sh        # spawns tuner if stale
├── tests/
│   ├── test_analyzer.py        # existing
│   ├── test_weighting.py       # new
│   ├── test_tuner.py           # new
│   └── test_losses.py          # new
└── docs/
    ├── ANALYZER_PLAN.md        # existing
    └── TUNER_PLAN.md           # this file
```

---

## State files (in `~/.claude/tokenomy/`)

### `applied.json` (atomic, written by tuner)

```json
{
  "version": "0.3.0",
  "last_tune_at": "2026-04-08T14:35:00Z",
  "effective_n": 4823.5,
  "confidence": 0.96,
  "caps": {
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS": 6200,
    "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": 25,
    "MAX_MCP_OUTPUT_TOKENS": {
      "default": 8000,
      "playwright": 200000,
      "serena": 6000,
      "context7": 12000
    }
  },
  "cooldowns": {
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS": {"sessions_remaining": 3}
  },
  "freezes": {
    "MAX_MCP_OUTPUT_TOKENS.playwright": {"until": "2026-04-22T00:00:00Z", "reason": "truncation_requery"}
  },
  "user_pinned": ["MAX_THINKING_TOKENS"],
  "estimated_savings_usd_per_month": 42.18
}
```

### `~/.claude/settings.json` (merged in-place by tuner)

Tokenomy edits the user's real settings file atomically:

- One-time backup to `~/.claude/settings.json.tokenomy.bak` before the first mutation.
- Tuned caps + static baselines are written into the `env` block.
- A `__tokenomy__` sentinel records `managed_env_keys` so subsequent runs (and `--reset`) can prune exactly the keys tokenomy claimed without touching user-authored keys.
- Anything listed in `state.user_pinned` is never written, even if the tuner computed a value for it.

### `losses.jsonl` (append-only, written by tuner)

Loss events for audit + reporting.

### `tuner.log` (rotating)

Background tuner output. Errors land here, never on stderr.

---

## Hook specifications

### SessionStart hook (`hooks/session-start.sh`)

Logic:
1. If `~/.claude/tokenomy/applied.json` missing → spawn `python -m tuner.tuner --first-run` in background, exit 0.
2. Else if `applied.json` mtime is >3 days old → spawn `python -m tuner.tuner` in background, exit 0.
3. Else exit 0.

Spawn mechanism: unconditional `nohup python -m tuner.tuner ... </dev/null >> tuner.log 2>&1 &` inside an outer subshell, with `disown` where available. No `start /b` branching. Hook returns in <50ms always. Concurrency is gated by an atomic `mkdir tuner.lock.d`; the tuner clears its own lock dir in a `finally` block.

A previous design also tracked sessions via a `sessions.jsonl` log written by a `SessionEnd` hook. That pipeline was triple-broken (orphan hook, wrong input channel, no reader) and is removed: incremental retune is now driven solely by `applied.json` mtime, since the analyzer already walks `~/.claude/projects/**/*.jsonl` directly when it runs.

---

## Tuner main loop

```python
def main(first_run: bool = False) -> int:
    state = load_state()  # applied.json or empty
    if first_run or state is None:
        corpus = full_corpus_scan()  # uses analyzer.extractors
    else:
        corpus = incremental_scan(since=state["last_tune_at"])

    sessions = load_sessions_jsonl()
    losses = detect_losses(corpus, current_caps=state.get("caps", {}))
    apply_loss_freezes(state, losses)

    proposed = compute_caps_per_setting(corpus, sessions)
    final = apply_hysteresis_cooldown_freeze(state, proposed)

    write_settings_file(final)
    write_state_file(state, final, losses)
    return 0
```

Pure functions for `compute_caps_per_setting` and `apply_hysteresis_cooldown_freeze` — fully unit-testable without I/O.

---

## CLI commands

```
python -m tuner.tuner               # background-safe retune
python -m tuner.tuner --first-run   # full corpus scan (slower)
python -m tuner.tuner --dry-run     # compute, print diff vs current, do not write
python -m tuner.tuner --reset       # strip tokenomy-managed env keys from ~/.claude/settings.json, delete applied.json + losses.jsonl
python -m tuner.tuner --status      # human-readable current state, last 5 changes, frozen settings, savings est
```

`tokenomy reset` and `tokenomy status` shell aliases ship as a thin bash wrapper or as a Claude Code slash command if that's supported.

---

## Build order (clean session execution)

1. **Read this plan.** Verify v0.2.0 state in `tokenomy/`. Run existing tests to confirm green baseline.
2. **Create `tuner/` package** with empty `__init__.py`.
3. **`weighting.py` + tests.** Pure math. Hand-verify expected values for 6+ test cases including edge cases (empty input, single sample, all-same-age, all-same-value, age = exactly half-life, ancient samples → near-zero weight).
4. **`losses.py` v2 + tests.** Five detectors with paired good/bad fixtures. Each test asserts the detector fires on the bad fixture and stays silent on the good fixture. Reuse synthetic JSONL builders from `tests/test_analyzer.py`.
5. **`state.py` + tests.** Atomic read/write of `applied.json`. Tempfile + rename. Handles missing file, corrupt file (returns empty state, logs warning), schema migration stub.
6. **`settings_writer.py` + tests.** Writes `auto-settings.json` from a caps dict. Validates floors. Atomic. Includes `MAX_MCP_OUTPUT_TOKENS` per-server format if supported by Claude Code, else falls back to global = `max(per_server_caps.values())`.
7. **`tuner.py` `compute_caps_per_setting` + tests.** Pure function. Input: corpus stats (already weighted) + applied state. Output: proposed caps dict. Test against synthetic distributions where the right answer is hand-computable.
8. **`tuner.py` `apply_hysteresis_cooldown_freeze` + tests.** Pure function. Input: proposed + applied state. Output: final caps + new state. Test all branches: tighten allowed, tighten blocked by hysteresis, tighten blocked by cooldown, tighten blocked by freeze, loosen allowed, loosen blocked by hysteresis.
9. **`tuner.py` `main()` end-to-end** wired together. Integration test: run on synthetic corpus, verify produced `auto-settings.json` is reasonable.
11. **SessionStart hook** (`hooks/session-start.sh`). Test the spawn-and-detach logic on Windows + WSL/Linux. Confirm hook returns in <50ms even on cold start.
12. **First-run path.** Delete all state files. Trigger a SessionStart. Confirm tuner runs in background, writes state, and second SessionStart picks up the new caps.
13. **Real-data dry run.** `python -m tuner.tuner --dry-run` against Ionuț's 647 sessions. Verify proposed caps look sane: `MAX_MCP_OUTPUT_TOKENS.playwright` should be very high (~200k), `serena` should be ~6-8k, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` should reflect his recent compact-early behavior (likely 25-40, not 85).
14. **Iterate on the cap formula** if real data produces obviously-bad caps. The constants (`HALF_LIFE_DAYS`, confidence saturation, margin curve) are tunable based on what real data shows.
15. **Stop here for review.** Show the dry-run diff. Get user approval before applying.
16. **Apply for real.** Remove `--dry-run`. Confirm `auto-settings.json` written. Restart Claude Code. Run a session normally. Verify no breakage.
17. **Bump `plugin.json` to `0.3.0`.** Update `README.md` with an "Auto-tuner" section. Commit `feat: v0.3.0 — recency-weighted auto-tuner with self-correcting caps`. Tag `v0.3.0`. Push. Release.

---

## Test plan

### Unit tests

- `test_weighting.py` — 8+ cases on weighted percentile and decay function
- `test_losses.py` — 5 paired good/bad fixture tests, one per detector
- `test_tuner.py` — 12+ cases on compute_caps and apply_hysteresis (each branch + edge cases)
- `test_state.py` — atomic write, corrupt-file recovery, missing-file default
- `test_settings_writer.py` — floor enforcement, per-server format

### Integration tests

1. Synthetic corpus → expected caps (hand-computed)
2. Real-data dry run on Ionuț's corpus → manual sanity check
3. Hook spawn test → SessionStart returns in <50ms
4. First-run path → empty state → tuner produces valid `applied.json`
5. Loss-triggered freeze → inject loss → confirm freeze written
6. User-pinned setting → confirm tuner skips it

---

## Edge cases and gotchas

1. **Empty corpus on first run.** Tuner must produce a valid `auto-settings.json` with only baseline (Stage 0) settings. Floors apply. No crashes.
2. **All sessions older than ~60 days.** Effective_n approaches zero → confidence = 0 → most conservative caps. Floors take over.
3. **Single very recent session with weird outliers.** Hysteresis blocks tightening based on noise. Cooldown prevents oscillation.
4. **Clock skew on session timestamps.** Negative ages → clamp weight to 1.0.
5. **Concurrent tuner runs.** SessionStart hook checks for a `tuner.lock` file (PID + start time). If locked and lock <5min old → skip. Else stale → take it.
6. **Background tuner crashes mid-write.** Atomic tempfile+rename means `applied.json` is either old or new, never corrupt. `tuner.log` captures the traceback.
7. **User edits `auto-settings.json` manually.** Tuner overwrites on next run. Document this in README — manual edits go in personal settings, where they take precedence.
8. **`MAX_MCP_OUTPUT_TOKENS` per-server format unsupported.** If Claude Code only accepts a global value, tuner uses `max()` of per-server caps. Detect via probe at install time, store decision in `applied.json.format_version`.
9. **Half-life inappropriate for batch users.** A user who runs Claude Code in 1-week bursts every 2 months will have all sessions decay before next burst. Mitigation: clamp minimum weight to 0.01 so old data still has nonzero influence. Floors prevent disasters anyway.
10. **Sessions in future timestamps.** Skip with warning. Don't crash.
11. **Hook script not executable on Unix.** README install instructions mention `chmod +x hooks/*.sh`. Hook itself is bash, not sh, for arrays.

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Tuner picks too-aggressive cap → user hit by truncation | Hysteresis + floors + loss freeze + 14-day cooldown |
| Background tuner spam (runs every session) | "5 new sessions OR 3 days" gate in SessionStart hook |
| User loses trust after one bad recommendation | `tokenomy reset` is one command, restores `.bak` |
| Tuner runs in foreground accidentally and slows Claude Code startup | Hook always uses background spawn; integration test asserts <50ms hook return |
| Bug in weighting math produces NaN | All math goes through `weighted_percentile` which returns 0.0 for empty/zero-weight input; floors prevent zero from being applied |
| Settings format changes in future Claude Code versions | `format_version` in `applied.json`; tuner warns on mismatch |
| User has personal `MAX_OUTPUT_TOKENS` set higher | User-pinned detection — tuner records and skips |
| Real-data dry run shows nonsensical numbers | Step 14 (iterate on formula) is a planned step, not an emergency |

---

## Non-goals (v0.3.0)

- **No GUI.** CLI + status command only.
- **No network calls.** Fully local, no telemetry, no model pricing API fetch.
- **No multi-user / shared config.** Single-user local install only.
- **No auto-rollback of `settings.json.bak`** beyond `tokenomy reset`. Tuner only manages `auto-settings.json`.
- **No tuning of `MAX_THINKING_TOKENS` if not observable** in JSONL. Mark as static.
- **No cross-machine sync.** State files are local.
- **No A/B testing of caps.** Single global config per user.

---

## What to tell Claude in the next session

> Read `C:\Users\Gaming PC\Desktop\Claude\tokenomy\docs\TUNER_PLAN.md` and execute it from step 1. Run all existing v0.2.0 tests first to confirm green baseline, then build the tuner per the plan. Stop after step 14 (iterate on cap formula based on real-data dry run) for review before applying for real and tagging v0.3.0.

That's it. Plan is self-contained.
