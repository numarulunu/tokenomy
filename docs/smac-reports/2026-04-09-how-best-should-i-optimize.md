# SMAC Report: How best should I optimize this codebase?

**Generated:** 2026-04-09 | **Project:** Tokenomy | **Agents:** 4R + 4V
**Overall confidence:** ~82% weighted
**Scope:** hooks/statusline.py, hooks/*.sh, tuner/*, analyzer/pricing.py, analyzer/analyze.py, hooks/pricing.json

## Ranked Findings

| #  | Finding | Impact | Effort | Conf | Verified | Score |
|----|---------|--------|--------|------|----------|-------|
| 1  | `analyze.py` zeros cache_read_tokens — silently undercounts spend | HIGH | LOW | 99% | CONFIRMED | 2.97 |
| 2  | `all_time_cost` uncached, unbounded walk every render | HIGH | MED | 99% | CONFIRMED | 2.97 |
| 3  | Tuner cooldown counter never decrements — permanent freeze | HIGH | LOW | 98% | CONFIRMED | 2.94 |
| 4  | `all_time_cost` vs `today_cost` cost-source asymmetry | HIGH | LOW | 98% | CONFIRMED | 2.94 |
| 5  | `stat -c %Y` GNU-only — stale-lock misfires on macOS/BSD | HIGH | LOW | 97% | CONFIRMED | 2.91 |
| 6  | `analyzer/pricing.py` missing 7 models — Opus priced as Sonnet | HIGH | LOW | 97% | CONFIRMED | 2.91 |
| 7  | Tuner `apply_loss_freezes` mutates input state dict | MED | LOW | 99% | CONFIRMED | 1.98 |
| 8  | Tuner `save_currency` non-atomic write — crash corrupts file | MED | LOW | 99% | CONFIRMED | 1.98 |
| 9  | Tuner `load_state` promotes unknown fields silently | MED | LOW | 97% | CONFIRMED | 1.94 |
| 10 | `today_cost` has no mtime pre-filter | MED | LOW | 97% | CONFIRMED | 1.94 |
| 11 | `cost_from_usage` treats all cache_creation as 5m rate | MED | LOW | 96% | CONFIRMED | 1.92 |
| 12 | `pricing_for` substring matcher fragile on future model IDs | MED | LOW | 95% | CONFIRMED | 1.90 |
| 13 | `seen` dedup set not shared across aggregators | MED | MED | 95% | CONFIRMED | 1.90 |
| 14 | `analyzer/pricing.py` `[1m]` key has wrong rates | HIGH | LOW | 97% | PARTIAL | 1.75 |
| 15 | Lock race between hook trap and tuner `finally` | HIGH | MED | 90% | PARTIAL | 1.62 |
| 16 | `find -mtime` unreliable on Git Bash | MED | LOW | 93% | PARTIAL | 1.12 |
| 17 | Version docstring drift (`tuner.py` says 0.3.0) | LOW | LOW | 100% | CONFIRMED | 1.00 |
| 18 | `cache_write` vs `cache_write_5m` field name mismatch | LOW | LOW | 99% | CONFIRMED | 0.99 |
| 19 | `read-once.sh` mtime-only cache — same-second stale reads | MED | MED | 80% | PARTIAL | 0.96 |
| 20 | `log-grep.sh` missing trailing `exit 0` after block path | LOW | LOW | 95% | CONFIRMED | 0.95 |
| 21 | `last_context_tokens` 256KB tail may miss last usage | LOW | LOW | 85% | CONFIRMED | 0.85 |
| 22 | `read-once.sh` cache write has no stall timeout | LOW | LOW | 75% | CONFIRMED | 0.75 |
| 23 | Tuner writes use `sort_keys=True` — cosmetic diff noise | LOW | LOW | 99% | PARTIAL | 0.59 |

---

## Finding 1: `analyze.py` zeros `cache_read_tokens` — silently undercounts spend
**Researchers:** Pricing | **Verified:** CONFIRMED | HIGH / LOW / 99%
**Evidence:** `analyzer/analyze.py:163-170` — `cost = pricing.cost_for_usage(..., cache_read_tokens=0, ...)` with comment "Exclude cache_read from the headline cost: Claude Code reports the cumulative cache per turn, so summing across turns double-counts massively."
**Description:** Cache-read is real money ($0.30/M Sonnet, $1.50/M Opus). On Sonnet 4.6 heavy users cache reads are 30-50% of token volume. The `insights.json` report reads $0 for that column.
**Recommendation:** Verify whether transcripts emit cumulative or per-turn cache counts. If cumulative → subtract previous turn. If per-turn → re-enable the argument. At minimum annotate `insights.json` as cache-read-excluded.

## Finding 2: `all_time_cost` uncached, unbounded walk every render
**Researchers:** Statusline, Pricing | **Verified:** CONFIRMED | HIGH / MED / 99%
**Evidence:** `hooks/statusline.py:201-214,395` — no cache/TTL, called unconditionally from `render()`.
**Description:** Statusline renders every few seconds. `all_time_cost` walks every `~/.claude/projects/**/*.jsonl` file, parses every line, on every render. Unbounded in transcript history size. No mtime filter.
**Recommendation:** Add a module-level `{"total", "computed_at", "file_mtimes"}` cache. On each render recompute only files whose mtime changed. 30-60s TTL.

## Finding 3: Tuner cooldown counter never decrements — permanent freeze
**Researchers:** Tuner | **Verified:** CONFIRMED | HIGH / LOW / 98%
**Evidence:** `tuner/tuner.py:227,235` — `cooldowns[key] = {"sessions_remaining": COOLDOWN_SESSIONS}` sets 5; `tuner.py:189` reads; nowhere in `main()` (lines 330-382) decrements.
**Description:** Any setting that ever hits the threshold is frozen forever (until another tighten/loosen event overwrites the key). Effective cooldown = ∞, not 5 sessions.
**Recommendation:** Add `_tick_cooldowns(state)` step before `apply_hysteresis_cooldown_freeze` in `main()`. Decrement all `sessions_remaining`, delete entries at 0. Add a regression test.

## Finding 4: `all_time_cost` vs `today_cost` cost-source asymmetry
**Researchers:** Statusline, Pricing | **Verified:** CONFIRMED | HIGH / LOW / 98%
**Evidence:** `statusline.py:197` uses embedded `costUSD` when present; `statusline.py:212-213` (`all_time_cost`) ignores `_embedded` and always reprices.
**Description:** Three figures render side-by-side (total / today / block). Today honors server-side costUSD, total reprices. Any past pricing bug gets baked into `today` but not `total`, making the two diverge.
**Recommendation:** Pick one source of truth. Either always reprice (preferred — invariant across pricing updates) or always prefer embedded. Apply uniformly to `today_cost`, `all_time_cost`, and `collect_recent_messages`.

## Finding 5: `stat -c %Y` GNU-only — stale-lock misfires on macOS/BSD
**Researchers:** Hooks | **Verified:** CONFIRMED | HIGH / LOW / 97%
**Evidence:** `hooks/session-start.sh:30` — `AGE=$(( $(date +%s) - $(stat -c %Y "$LOCKDIR" || echo 0) ))`
**Description:** `-c %Y` is GNU coreutils. On macOS `stat` takes `-f %m`. The `|| echo 0` fallback fires on error → AGE becomes ~1.7B → always ≥ 300 → every lock check evaluates as stale. Two parallel sessions can both see "stale", both rmdir + re-create, both spawn tuner → concurrent `applied.json`/`settings.json` writes.
**Recommendation:** Replace with Python one-liner: `AGE=$(python -c "import os,time; print(int(time.time()-os.path.getmtime('$LOCKDIR')))" 2>/dev/null || echo 0)`. Python is already a hard dep.

## Finding 6: `analyzer/pricing.py` missing 7 models — Opus priced as Sonnet
**Researchers:** Pricing | **Verified:** CONFIRMED | HIGH / LOW / 97%
**Evidence:** `analyzer/pricing.py:19-26` only has 6 keys; `hooks/pricing.json` has 10. Missing: `claude-opus-4`, `claude-opus-4-1`, `claude-opus-4-5`, `claude-sonnet-4`, `claude-sonnet-4-5`, `claude-3-5-sonnet`, `claude-3-5-haiku`. Fallback at `analyzer/pricing.py:52-53` → `DEFAULT_PRICING_KEY = "claude-sonnet-4-6"`.
**Description:** Historical Opus 4 sessions are reported at ~5× too cheap ($3/$15 vs $15/$75). Silent. Sonnet 3.5 sessions similar.
**Recommendation:** Sync `analyzer/pricing.py::PRICING` to `hooks/pricing.json`. Better: generate the dict from the JSON file at import time. Loud warning on any unknown model, not silent Sonnet fallback.

## Finding 7: Tuner `apply_loss_freezes` mutates input state dict
**Verified:** CONFIRMED | MED / LOW / 99% | `tuner/tuner.py:262` — `state["freezes"] = freezes`. Other reducers (`apply_hysteresis_cooldown_freeze`) shallow-copy first. Hidden side-effect, fragile to call-order changes and test reordering.
**Fix:** `new_state = dict(state); new_state["freezes"] = freezes; return new_state`.

## Finding 8: `save_currency` non-atomic write
**Verified:** CONFIRMED | MED / LOW / 99% | `tuner/currency.py:89-91` — direct `open("w") + json.dump`. Crash mid-write → zero-byte file → `load_currency` silently resets to USD. Every other writer in the codebase uses temp-file + `os.replace`.
**Fix:** Use `_atomic_write_json` from `settings_writer.py` (extract to shared util, or duplicate 3-line pattern).

## Finding 9: `load_state` promotes unknown fields silently
**Verified:** CONFIRMED | MED / LOW / 97% | `tuner/state.py:43-45` — `base = empty_state(); base.update(data)`. Any stray key from a newer version or manual edit is round-tripped to disk indefinitely.
**Fix:** `return {k: base[k] for k in empty_state()}` — strict schema boundary. Or at minimum debug-log unknowns.

## Finding 10: `today_cost` has no mtime pre-filter
**Verified:** CONFIRMED | MED / LOW / 97% | `hooks/statusline.py:192-197` — walks every transcript regardless of age. `collect_recent_messages` (lines 223-226) already has the correct pattern.
**Fix:** Skip files with `mtime < day_start`. 2-line change.

## Finding 11: `cost_from_usage` treats all cache_creation as 5m rate
**Verified:** CONFIRMED | MED / LOW / 96% | `statusline.py:93-96` acknowledges "Treat cc as 5m". 1h cache is 60% more expensive ($30 vs $18.75 Opus). Users on long-lived dev sessions are undercharged.
**Fix:** Check for `cache_creation.ephemeral_1h_input_tokens` nested field. If unavailable, add a visible `*` annotation on the statusline when cache_creation is nonzero.

## Finding 12: `pricing_for` substring matcher fragile
**Verified:** CONFIRMED | MED / LOW / 95% | `statusline.py:74-85` uses `if k in key` — any pricing key that is a substring of a received model ID matches. Future `claude-opus-4-7` will fall through to `claude-opus-4` silently. Analyzer uses incompatible exact-lookup + Sonnet fallback.
**Fix:** Log a WARN when falling back to a non-exact longest match. Unify analyzer and statusline lookups into one shared `pricing_utils.py`.

## Finding 13: `seen` dedup set not shared across aggregators
**Verified:** CONFIRMED | MED / MED / 95% | `statusline.py:191,210,221` — each aggregator creates its own local `seen: set = set()`. Cross-file duplicates (same message in multiple transcripts) are counted once per aggregator, not once per render.
**Fix:** Pass a shared set from `render()` — or (preferred) do one unified walk and fan out in memory. Folds into Finding 2's cache refactor.

## Finding 14: `analyzer/pricing.py` `[1m]` key has wrong rates (PARTIAL)
**Verified:** PARTIAL | HIGH / LOW / 97% | Verifier correction: the key exists but has the *same* rates as the standard tier instead of the elevated >200k rates. `analyzer/pricing.py:22-23` shows `"claude-sonnet-4-6[1m]": {"input": 3.00, ...}` vs correct `6.00/22.50`.
**Fix:** Populate `[1m]` entries with the `tier_1m` values from `hooks/pricing.json`.

## Finding 15: Lock race between hook trap and tuner `finally` (PARTIAL)
**Verified:** PARTIAL | HIGH / MED / 90% | `session-start.sh:40` has `trap 'rmdir "$LOCKDIR"' EXIT`; hook backgrounds the tuner then exits → trap removes the dir while tuner is still running. Tuner's own `finally` also removes it (no-ops on OSError). Narrow but real window: a second session-start spawned within the gap sees no lock and launches a second tuner.
**Fix:** Delete the EXIT trap from `session-start.sh`. Tuner's `finally` is the sole release. If tuner crashes before reaching `finally`, the 5-min stale-lock gate handles recovery.

## Finding 16: `find -mtime` unreliable on Git Bash (PARTIAL)
**Verified:** PARTIAL | MED / LOW / 93% | `session-start.sh:23` and `cleanup.sh:23`. Fail-open guards keep it non-catastrophic but stale-cache cleanup silently no-ops on Windows Git Bash.
**Fix:** Replace with a one-line Python call (pattern from Finding 5).

## Finding 17: Version docstring drift
**Verified:** CONFIRMED | LOW / LOW / 100% | `tuner/tuner.py:1` says `v0.3.0`, `state.py:12` and `session-start.sh:5` say `0.3.1`. `test_version_sync` apparently doesn't cover the docstring.
**Fix:** Single-source the version (read from `plugin.json`) or add `tuner.py` docstring to the sync test.

## Finding 18: `cache_write` vs `cache_write_5m` field mismatch
**Verified:** CONFIRMED | LOW / LOW / 99% | `analyzer/pricing.py:20` vs `hooks/pricing.json:9`. Each file is internally consistent, but any future attempt to share the pricing source silently zeros cache-write cost.
**Fix:** Standardize on `cache_write_5m` in both.

## Finding 19: `read-once.sh` same-second stale cache (PARTIAL)
**Verified:** PARTIAL | MED / MED / 80% | mtime-only cache key. After an Edit, a same-second re-Read can be blocked because mtime integer hasn't advanced.
**Fix:** Include file size as secondary cache key: `{"mtime": m, "size": s}`.

## Finding 20: `log-grep.sh` missing trailing `exit 0`
**Verified:** CONFIRMED | LOW / LOW / 95% | Script ends with a `printf | python -c` pipeline (lines 117-121) with no explicit `exit 0`. If that final python call fails, hook exits non-zero.
**Fix:** Append `|| true; exit 0` or wrap in a function with `|| approve` fallback.

## Finding 21: `last_context_tokens` 256KB tail may miss last usage
**Verified:** CONFIRMED | LOW / LOW / 85% | `statusline.py:314` reads last 262144 bytes. A huge inline tool result can push the last usage record off the tail → underreported context %.
**Fix:** Bump to 512KB or read in reverse chunks until a valid usage record is found.

## Finding 22: `read-once.sh` cache write has no stall timeout
**Verified:** CONFIRMED | LOW / LOW / 75% | `read-once.sh:68-72` writes cache via blocking `open("w")`. On slow disk / AV scan / NFS the 5s hook timeout can be consumed.
**Fix:** Write via `daemon=True` thread, or accept existing `except Exception: pass` as "write is best-effort" and document it.

## Finding 23: Tuner writes use `sort_keys=True` (cosmetic)
**Verified:** PARTIAL | LOW / LOW / 99% | `settings_writer.py:86`, `state.py:56`. Creates cosmetic diff noise in dot-file repos. No functional impact.
**Fix:** Drop `sort_keys=True` on the `settings.json` path only.

---

## Disputed / Rejected Findings
| Finding | Researcher | Verifier reason |
|---|---|---|
| Statusline "triple walk" severity overstated | Statusline | Only 2 full walks + 1 mtime-filtered, not 3 full |
| `burn_rate` shim double-walks `render()` | Statusline | Shim not called from `render()` at all |
| `fmt_money(0)` dead branch | Statusline | Branch actually reachable when rate=0 |
| Windows stdout `reconfigure` "incomplete" | Statusline | Standard pattern, no concrete gap shown |
| `cleanup.sh` `set -u` crash | Hooks | No unset variables actually referenced |
| `cleanup.sh` SID path traversal | Hooks | `session_id` is Claude Code-controlled, theoretical only |
| `_delta` returns 1.0 on old==0 | Tuner | Unreachable defensive branch, not a bug |
| `tier_1m` threshold uses per-request ctx | Pricing | Per-request ctx is the correct Anthropic billing signal |
| `session_cost` variable name misleading | Pricing | Display label is "total", output correct |

## Coverage Gaps
| Role | Status |
|---|---|
| Test coverage & dead code audit | Not dispatched — budget/latency trade-off under active rate-limit overage |
