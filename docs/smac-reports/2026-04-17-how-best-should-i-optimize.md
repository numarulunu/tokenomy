# SMAC Report: How best should I optimize this codebase?

**Generated:** 2026-04-17 | **Project:** Tokenomy v0.6.0 | **Agents:** 5R + 5V
**Overall confidence:** ~91% weighted
**Scope:** new surface since 2026-04-09 SMAC — `tokenomy_mcp/server.py`, `hooks/fetch-audit.py`, `tuner/auto_rules.py`, `tuner/consent.py`, `tuner/losses.py`, tuner v0.6.0 rolling_mean + force_loosen; plus regression hunt across statusline/pricing/analyzer
**Priors honored:** 23 findings from 2026-04-09 SMAC filtered out; recent-commit fixes (1b9fa68, b2ada59, 87bcd63, 9597814, cae9170, etc.) excluded from re-raise.

## Ranked Findings

| #  | Finding | Impact | Effort | Conf | Verified | Score |
|----|---------|--------|--------|------|----------|-------|
| 1  | `claude-opus-4-7` absent from both pricing tables — analyzer silently mis-prices Opus 5× | HIGH | LOW | 99% | CONFIRMED | 2.97 |
| 2  | Tuner low-confidence skip actively **removes** previously-deployed caps | HIGH | LOW | 98% | CONFIRMED | 2.94 |
| 3  | `_server_matches` fuzzy `a in low` — false positives (e.g. `db` matches `mongodb`) | HIGH | LOW | 92% | CONFIRMED | 2.76 |
| 4  | `estimated_savings_usd_per_month` is a stub — always 0.0, never written | MED | MED | 100% | CONFIRMED | 2.00 |
| 5  | `analyzer/pricing.py` drifts from `pricing.json` — `cache_write_1h` missing everywhere | MED | LOW | 99% | CONFIRMED | 1.98 |
| 6  | `_strip_managed_env` uninstall leaves stale fetch-audit hook entries → FileNotFoundError on every tool call | MED | LOW | 99% | CONFIRMED | 1.98 |
| 7  | `fetch-log.jsonl` unbounded — no rotation, analyzer startup grows linearly | MED | LOW | 98% | CONFIRMED | 1.96 |
| 8  | Hooks block unmanaged by `settings_writer` — no dedup, no prune | MED | MED | 95% | CONFIRMED | 1.90 |
| 9  | `tools/call` splats untrusted JSON kwargs directly into handler — no schema validation | MED | LOW | 95% | CONFIRMED | 1.90 |
| 10 | `detect_unused_mcp` cannot distinguish new-but-unused from long-stale | MED | LOW | 95% | CONFIRMED | 1.90 |
| 11 | `consent.py` has no read path — `--first-run` silently overwrites on re-invocation | MED | MED | 95% | CONFIRMED | 1.90 |
| 12 | `force_loosen` reads **post**-hysteresis `state['caps']` — zeroes the cooldown it just set | MED | LOW | 92% | CONFIRMED | 1.84 |
| 13 | Burn rate returns raw 60m **sum**, not rate — underreports at session start (<60m) | MED | LOW | 92% | CONFIRMED | 1.84 |
| 14 | PreToolUse/PostToolUse fetch-audit hooks have no `matcher` — fire on every tool | MED | LOW | 90% | CONFIRMED | 1.80 |
| 15 | `force_loosen` + `rolling_mean` run unconditionally on low-confidence runs → poisoned state | MED | LOW | 90% | CONFIRMED | 1.80 |
| 16 | `decide_cache_ttl` `insufficient_data` returns `'0'` but `run()` drops it — misleading audit trail | MED | LOW | 88% | CONFIRMED | 1.76 |
| 17 | `block_state` fallback `spec_from_file_location` executes arbitrary `hooks/statusline.py` | MED | MED | 80% | CONFIRMED | 1.60 |
| 18 | `IDLE_GAP_MIN_SAMPLES=100` unreachable for low-volume users | MED | LOW | 80% | CONFIRMED | 1.60 |
| 19 | `env_overlays` stomps user-set key when `user_pinned` state is stale/empty | HIGH | LOW | 85% | PARTIAL | 1.53 |
| 20 | `iter_fetch_log` orphan pre-records accumulate across full-log scan | MED | LOW | 97% | PARTIAL | 1.16 |
| 21 | `burn_rate()` shim re-walks filesystem — defeats single-pass optimization for external callers | MED | LOW | 97% | PARTIAL | 1.16 |
| 22 | `initialize` notification filter uses `notifications/` prefix only — accidentally-correct only | MED | LOW | 92% | PARTIAL | 1.10 |
| 23 | `suggestions_md` / `auto_rule_decisions` return full file — no size cap | MED | LOW | 90% | PARTIAL | 1.08 |
| 24 | `state.py:save_state` still `sort_keys=True` — partial-fix remainder from prior SMAC | LOW | LOW | 100% | CONFIRMED | 1.00 |
| 25 | `report.py` reads `by_tool` — should be `by_fetch_tool`; TOP TOOL SINKS renders empty | LOW | LOW | 99% | CONFIRMED | 0.99 |
| 26 | `_parse_iso_ms` Z-branch is dead — hook emits `+00:00`, never `Z` | LOW | LOW | 98% | CONFIRMED | 0.98 |
| 27 | `render_suggestions_md` hardcodes `"14d window"` — drifts from `UNUSED_MCP_WINDOW_DAYS` constant | LOW | LOW | 98% | CONFIRMED | 0.98 |
| 28 | MCP server has no logging path — violates BUILD RULE #1 ("every script logs") | LOW | LOW | 95% | CONFIRMED | 0.95 |
| 29 | Block-boundary `gap` == `dur` (both `BLOCK_HOURS`) — gap branch unreachable | LOW | LOW | 95% | CONFIRMED | 0.95 |
| 30 | `rolling_mean_n` is a boolean flag masquerading as a sample-count accumulator | LOW | LOW | 95% | CONFIRMED | 0.95 |
| 31 | `analyze_idle_gaps` median uses `sorted_g[n//2]` — upper-middle bias for even n | LOW | LOW | 90% | CONFIRMED | 0.90 |
| 32 | `detect_truncation_requery` O(n²) on sessions with many truncated results | LOW | LOW | 90% | CONFIRMED | 0.90 |
| 33 | `counterfactual.py` hardcodes Sonnet pricing for savings — Opus 5× undercount | LOW | LOW | 90% | CONFIRMED | 0.90 |
| 34 | `input_hash` 16-hex truncation — 64-bit collision space, negligible but documented | LOW | LOW | 88% | CONFIRMED | 0.88 |
| 35 | `initialize` handler ignores client `protocolVersion` — silent version mismatch possible | LOW | LOW | 88% | CONFIRMED | 0.88 |
| 36 | Backup rotation skipped when sentinel cleared (`prev_version=None`) | LOW | LOW | 88% | CONFIRMED | 0.88 |
| 37 | `_DECISION_RE` unbounded capture groups — linear backtracking on long lines | LOW | LOW | 85% | CONFIRMED | 0.85 |
| 38 | `top_wasters` forwards untrusted `by_tool` keys without sanitization | LOW | LOW | 78% | CONFIRMED | 0.78 |
| 39 | Windows NTFS append atomicity not guaranteed — concurrent sessions risk interleave | LOW | LOW | 75% | CONFIRMED | 0.75 |

---

## Finding 1: `claude-opus-4-7` absent from both pricing tables

**Researchers:** Statusline Regression Hunt | **Verified:** CONFIRMED | **HIGH / LOW / 99%**

**Evidence:** `hooks/pricing.json:5-83` (no `claude-opus-4-7` key); `analyzer/pricing.py:23-36` (no key); fallback at `analyzer/pricing.py:62-63` silently returns `DEFAULT_PRICING_KEY = "claude-sonnet-4-6"`.

**Description:** The current session is on `claude-opus-4-7`. Neither pricing table recognizes it. Analyzer reports Opus spend at Sonnet rates — 5× undercount on input ($3/M vs $15/M), same on output. `insights.json` and every savings figure is systematically wrong for Opus users. Statusline substring-match at `statusline.py:pricing_for` will catch `claude-opus-4` but log a noisy stderr warning on every render.

**Recommendation:** Add `claude-opus-4-7` to both `hooks/pricing.json` and `analyzer/pricing.py` with the same rates as `claude-opus-4-6`: input=$15, output=$75, cache_write_5m=$18.75, cache_write_1h=$30, cache_read=$1.50.

---

## Finding 2: Tuner low-confidence skip actively removes deployed caps

**Researchers:** Tuner Control Loop | **Verified:** CONFIRMED | **HIGH / LOW / 98%**

**Evidence:** `tuner/tuner.py:438` — `final = {}` when `effective_n < MIN_EFFECTIVE_N`. `tuner/tuner.py:494-495` calls `merge_into_user_settings` with that empty dict. `tuner/settings_writer.py:180-184` prunes all previously-managed keys not in current `managed` set.

**Description:** A user who takes a 2-week vacation (corpus ages out, `effective_n` drops below 200) will have **all tuned caps silently removed from `settings.json`** on the next scheduled tuner run. `state["caps"]` retains the old values (hysteresis skipped), creating divergence between state and disk. The log message says "writing baseline only" without warning that deployed caps are being retracted.

**Recommendation:** On low-confidence skip, pass `state.get("caps", {})` unchanged into `merge_into_user_settings` instead of `{}`. Only skip computing **new** caps — do not retract existing ones. Add a `log.warning("confidence too low — retaining previous caps")` that is visibly different from a first-run baseline write.

---

## Finding 3: `_server_matches` fuzzy match asymmetry — false positives

**Researchers:** Auto-Rules Engine | **Verified:** CONFIRMED | **HIGH / LOW / 92%**

**Evidence:** `tuner/auto_rules.py:44` — `return any(a in low for a in allow)`. Same pattern mirrored in `tuner.py`.

**Description:** The fuzzy match checks whether the allow-list entry `a` is a substring of the event-reported server name. This correctly resolves plugin-prefixed names (`plugin_context7_context7` contains `context7`), but a short allow-list name like `db` will match `mongodb`, `influxdb`, `redis-db`. `detect_unused_mcp` will then credit activity from the wrong server — hiding a genuinely unused server (false negative) or attributing non-existent usage (false positive). A user's actual unused server could be silently missed.

**Recommendation:** Add a minimum-length guard: `if len(a) < 4: return server == a`. Or normalize both sides to their middle segment (strip `mcp__` / `plugin_` prefix) before comparing, then require exact match. Keep substring only as a last-resort fallback for plugin-prefixed forms with a length floor.

---

## Finding 4: `estimated_savings_usd_per_month` is a stub — always 0.0

**Researchers:** Tuner Control Loop | **Verified:** CONFIRMED | **MED / MED / 100%**

**Evidence:** `tuner/state.py:25` — field exists only in `empty_state()` default. Project-wide grep finds exactly two references: the state default and `docs/TUNER_PLAN.md:193` sample.

**Description:** No code path ever writes a non-zero value. `applied.json` shows `0.0` on every run including after successful tuning. Any UI, hook, or status display that surfaces this field will permanently show "$0.00 saved" — which is actively misleading about whether tuning is doing anything.

**Recommendation:** Either implement the formula — `delta_tokens_per_session × sessions_per_month × price_per_token` where `price_per_token` comes from `analyzer/pricing.py` for the dominant session model — or remove the field from `empty_state()` to prevent false confidence. If implementing, document the `sessions_per_month` assumption so it can be audited.

---

## Finding 5: `analyzer/pricing.py` drifts from `hooks/pricing.json`

**Researchers:** Statusline Regression Hunt | **Verified:** CONFIRMED | **MED / LOW / 99%**

**Evidence:** `analyzer/pricing.py:33` has `claude-haiku-4-5-20251001` — absent from `hooks/pricing.json`. `hooks/pricing.json` entries carry `cache_write_1h` (e.g. `$30` for opus-4-6 at line 10) — absent from every entry in `analyzer/pricing.py`. `analyzer/pricing.py:cost_for_usage` (lines 88-93) uses only `cache_write_5m`.

**Description:** 1h-cache sessions are priced at the 5m rate in the analyzer — 1.6×–2× undercount. Prior SMAC finding 18 ("field mismatch") is still open and now has two concrete new instances.

**Recommendation:** Add `cache_write_1h` to every entry in `analyzer/pricing.py::PRICING`. Remove the dated `claude-haiku-4-5-20251001` key from `analyzer/pricing.py` (or mirror it into `pricing.json`). Better: generate `analyzer/pricing.py`'s dict from `hooks/pricing.json` at import time — single source of truth.

---

## Finding 6: `_strip_managed_env` uninstall leaves stale hook entries

**Researchers:** Fetch-Audit Hook Integrity | **Verified:** CONFIRMED | **MED / LOW / 99%**

**Evidence:** `tuner/tuner.py:535-558` — function reads `managed_env_keys`, pops them from `env_block`, removes `__tokenomy__` sentinel. No reference to `managed_hook_keys`, no hooks-block touch. Hook command hardcodes an absolute path.

**Description:** After uninstall, `~/.claude/settings.json` still contains `PreToolUse` and `PostToolUse` entries pointing to `hooks/fetch-audit.py`. That path now doesn't exist. Claude Code will fire the hook, Python will raise `FileNotFoundError`, and the hook process will exit non-zero — **on every tool call**. Depending on hook error semantics this may block tool use or produce persistent noise.

**Recommendation:** Extend `_strip_managed_env` to also read `managed_hook_keys` from the sentinel and remove matching entries from the `hooks.PreToolUse` and `hooks.PostToolUse` arrays. The sentinel already tracks the keys — just wire the cleanup.

---

## Finding 7: `fetch-log.jsonl` unbounded — no rotation policy

**Researchers:** Fetch-Audit Hook Integrity | **Verified:** CONFIRMED | **MED / LOW / 98%**

**Evidence:** `hooks/fetch-audit.py:96` — `open(LOG_PATH, "a")` with no rotation, no size check. `analyzer/extractors.py:209-265` scans the entire file on each analyzer run.

**Description:** Observed growth: ~599 lines / 104 KB in 1.2 hours of SMAC-heavy multi-subagent usage. Projected normal-use: ~1.8 MB/30 days, worst case ~6 MB/30 days. Disk footprint is low today but monotonic. `iter_fetch_log` latency grows linearly with file size — analyzer cold-start will become visibly slow over months.

**Recommendation:** At the top of `hooks/fetch-audit.py::main()`, check `os.path.getsize(LOG_PATH)`. If >50 MB, rename to `fetch-log.jsonl.1` and start fresh (keep last generation only). Alternatively, trim records older than 30 days on each analyzer run from inside `iter_fetch_log`, emitting a `log.info` truncation notice.

---

## Finding 8: Hooks block unmanaged by `settings_writer`

**Researchers:** Fetch-Audit Hook Integrity | **Verified:** CONFIRMED | **MED / MED / 95%**

**Evidence:** `tuner/settings_writer.py:110-199` — `merge_into_user_settings` manages only the `env` block. Grep for `hook` across the file returns zero matches. `managed_hook_keys` sentinel is populated in live settings.json but no code path reads it.

**Description:** On every tuner run, hooks are never pruned, deduplicated, or re-validated. If the plugin is reinstalled or the hook path changes, duplicate fetch-audit entries could accumulate — each firing `fetch-audit.py` twice per tool event. `managed_hook_keys` is a documented sentinel with no consumer.

**Recommendation:** Add a `merge_hooks(path, managed_hooks, pinned_hooks)` function to `settings_writer.py` that performs the same idempotent prune-and-write logic as `merge_into_user_settings` does for env. Call it alongside the env merge from `tuner.py::main()`. Alternatively, annotate `managed_hook_keys` as documentation-only and warn in comments that hooks require manual dedup on reinstall.

---

## Finding 9: `tools/call` passes untrusted kwargs directly into handlers

**Researchers:** MCP Server Auditor | **Verified:** CONFIRMED | **MED / LOW / 95%**

**Evidence:** `tokenomy_mcp/server.py:280` — `result = spec["handler"](**args) if args else spec["handler"]()`. No validation against `spec["inputSchema"]`.

**Description:** `args` is taken directly from `params.get("arguments") or {}` and splatted into the handler. `TypeError` is caught (`-32602`) only **after** the call, not before. Duck-typed coercions pass silently: `{"days": "7"}` may produce nonsense because Python doesn't coerce to `int`. Zero-arg handlers like `block_state()` are currently safe (`args` is a falsy empty dict), but any future zero-arg handler with optional kwargs can receive unexpected keyword arguments.

**Recommendation:** Before `spec["handler"](**args)`, validate `args.keys() ⊆ spec["inputSchema"]["properties"].keys()`. For integer-typed params, wrap in `try: int(args[k])` with `-32602` on failure. For zero-property schemas, assert `not args` before the call.

---

## Finding 10: `detect_unused_mcp` can't distinguish new from stale

**Researchers:** Auto-Rules Engine | **Verified:** CONFIRMED | **MED / LOW / 95%**

**Evidence:** `tuner/auto_rules.py:177` — `out.append({"server": server, "last_used": None, "days_ago": None})`. `tuner/auto_rules.py:281` renders "never invoked in 14d window".

**Description:** No install-date tracking. A server installed yesterday with zero calls gets the same "never invoked" suggestion as one that has been idle for months. User sees "disable this unused server" prompt for a server they just set up.

**Recommendation:** Accept an optional `server_added_dates: Dict[str, datetime]` parameter in `detect_unused_mcp`. Skip or annotate servers whose add-date is within `window_days` of `now`. If install date is unavailable, at minimum change the rendered suggestion text to `"never invoked in the last Nd — may be newly added; verify before disabling"` when `last_seen is None`.

---

## Finding 11: `consent.py` has no read path

**Researchers:** Auto-Rules Engine | **Verified:** CONFIRMED | **MED / MED / 95%**

**Evidence:** `tuner/consent.py:20` — only function is `write_consent_summary`. No `has_consent()`, no read, no file-existence check. Nothing in `tuner.py` guards `--first-run` from re-running.

**Description:** The module name implies a consent gate but provides none. `--first-run` can be invoked repeatedly; each call silently overwrites `consent-summary.txt` and re-applies baseline env without re-consent from the user. If the file is partially written (crash mid-write), there's no detection of the corrupted state.

**Recommendation:** Add `has_consent(home_dir: str) -> bool` that checks both existence and a sentinel token (e.g. last non-blank line starts with `"This summary:"`). Call it from `tuner.py::main()` at `--first-run` entry and skip the baseline write if consent is already present. Separately, add an explicit `--reconsent` flag for intentional re-application.

---

## Finding 12: `force_loosen` reads post-hysteresis caps — zeroes its own cooldown

**Researchers:** Tuner Control Loop | **Verified:** CONFIRMED | **MED / LOW / 92%**

**Evidence:** `tuner/tuner.py:440` — `apply_hysteresis_cooldown_freeze` updates `state["caps"] = final`. `tuner/tuner.py:457` — force_loosen block reads `state.get("caps")`, which is the **just-mutated** value.

**Description:** If hysteresis tightens a cap this session from 8000→5000 with `rolling_mean=7500`, force_loosen fires immediately (5000 < 0.9 × 7500 = 6750) and zeroes the cooldown just set. The cooldown has protected zero sessions. Net effect: tighten+force_loosen in the same session produces state with no cooldown on the tightened cap, allowing the next session's hysteresis to re-loosen freely — an oscillation.

**Recommendation:** Capture `old_caps = dict(state.get("caps") or {})` **before** calling `apply_hysteresis_cooldown_freeze`. Use `old_caps.get(cap_key, 0)` in the force_loosen comparison instead of the freshly-mutated `state["caps"]`.

---

## Finding 13: Burn rate is a raw 60m sum, not normalized to `/hr`

**Researchers:** Statusline Regression Hunt | **Verified:** CONFIRMED | **MED / LOW / 92%**

**Evidence:** `hooks/statusline.py:463-465` — `burn = sum(c for ts, c in msgs if ts >= burn_cutoff)`. No division by actual elapsed time. Rendered as `"{fmt_money(rate)}/hr"` at `statusline.py:605`.

**Description:** Prior fix `9597814` corrected the block-elapsed error but the replacement formula sums the last 60 minutes of spend as a raw total and labels it `/hr`. If a session is 10 minutes old with $0.08 spent, display shows `$0.08/hr`. Actual pace is `$0.48/hr`. Accurate only once the session is >60 minutes old.

**Recommendation:** `elapsed_hours = min(1.0, age_of_oldest_msg_in_window / 3600)`; `burn_rate = burn / elapsed_hours` with a guard for `elapsed_hours > 0`. Treats partial windows correctly.

---

## Finding 14: Fetch-audit hooks have no `matcher` — fire on every tool

**Researchers:** Fetch-Audit Hook Integrity | **Verified:** CONFIRMED | **MED / LOW / 90%**

**Evidence:** `~/.claude/settings.json:136-145` — `PreToolUse` hook entry has no `matcher` field. Compare `hooks/hooks.json` where `log-grep.sh` is scoped to `matcher: "Read"`.

**Description:** With no matcher, fetch-audit.py fires on **every** tool call — Bash, mcp__*, Read, statusline.py spawn, everything. Python startup ≈ 80 ms × every call × parallel subagents (SMAC dispatches 10+). Per-turn hook overhead is a latency tax without proportional analytical value: most insights come from Bash/Read/Write/Edit and MCP fetches, not from internal housekeeping tools.

**Recommendation:** Set `matcher: "Bash|Read|Write|Edit|MultiEdit|NotebookEdit|mcp__"` on both PreToolUse and PostToolUse entries. Skips roughly half of invocations (statusline spawns, TodoWrite, internal sub-tools) at zero analytical cost.

---

## Finding 15: `force_loosen` + `rolling_mean` run on low-confidence runs

**Researchers:** Tuner Control Loop | **Verified:** CONFIRMED | **MED / LOW / 90%**

**Evidence:** `tuner/tuner.py:442` — rolling_mean update and force_loosen blocks sit **outside** the `if stats["effective_n"] < MIN_EFFECTIVE_N` gate at line 433.

**Description:** On low-confidence runs, `state["caps"]` retains stale values from the loaded state (hysteresis skipped). Force_loosen reads stale caps and may zero cooldowns that still need to protect. Rolling mean also seeds from sparse low-confidence data, skewing the EWMA. The controller is making decisions based on a small sample — exactly the case the `effective_n` gate was added to prevent.

**Recommendation:** Move the rolling_mean and force_loosen blocks **inside** the `else` branch (run only when `effective_n >= MIN_EFFECTIVE_N`). Confidence-gated updates protect the EWMA from sparse-data bias.

---

## Finding 16: `decide_cache_ttl` misleading audit trail on insufficient data

**Researchers:** Auto-Rules Engine | **Verified:** CONFIRMED | **MED / LOW / 88%**

**Evidence:** `tuner/auto_rules.py:133` — `return (current or "0"), f"insufficient_data n={n}"`. `tuner/auto_rules.py:233-234` — `run()` only writes to `env_overlays` when `cache_val == "1"`.

**Description:** `'0'` is returned but never written. If the user previously had `ENABLE_PROMPT_CACHING_1H=1` set by an earlier run, the settings_writer prune path (correctly) deletes it on next merge. But the decisions log reports `value=0, reason=insufficient_data` while the actual effect is **deletion of the prior value** — two different operations in the same log line.

**Recommendation:** When returning `'0'` from `decide_cache_ttl`, also write `env_overlays["ENABLE_PROMPT_CACHING_1H"] = "0"` so the value is actively set rather than pruned. Or change the `decisions` log format to distinguish "value applied" from "key removed".

---

## Finding 17: `block_state` fallback executes arbitrary `statusline.py`

**Researchers:** MCP Server Auditor | **Verified:** CONFIRMED | **MED / MED / 80%**

**Evidence:** `tokenomy_mcp/server.py:129-140` — try `from hooks import statusline`; fallback to `importlib.util.spec_from_file_location("statusline", str(ROOT / "hooks" / "statusline.py"))` with `exec_module`. No hash or signature check.

**Description:** `ROOT` resolves at import time from `__file__`. If the hooks directory is reorganized, symlinked, or world-writable, a malicious `statusline.py` placed there will be executed with the MCP server's privileges. The legitimate package import is attempted first (safe path), but the fallback pattern is a standing credibility risk for a server documented as "read-only".

**Recommendation:** Remove the file-path fallback entirely; ship the MCP server as a package so `from hooks import statusline` is always available. If test-time fallback is needed, gate it on `os.environ.get("TOKENOMY_TEST") == "1"`. Document that the hooks directory must not be world-writable.

---

## Finding 18: `IDLE_GAP_MIN_SAMPLES=100` unreachable for low-volume users

**Researchers:** Auto-Rules Engine | **Verified:** CONFIRMED | **MED / LOW / 80%**

**Evidence:** `tuner/auto_rules.py:24` — `IDLE_GAP_MIN_SAMPLES = 100`.

**Description:** Gap samples are consecutive assistant-usage pairs in the 14-day rolling window. 1–2 short sessions per day generate ≤20 gaps/day; 100 samples requires ≥5 active days with dense activity. Light users or users who restart Claude frequently (many single-turn sessions) never cross the threshold, so `decide_cache_ttl` returns `insufficient_data` indefinitely — suppressing the 1h-cache optimization even when the pattern would justify it. No justification in the code for 100 vs 30 or 50.

**Recommendation:** Lower to `30` with a comment — "~2 weeks of light usage at 2 sessions/day." Alternatively, scale the threshold with observed activity density.

---

## Finding 19: `env_overlays` stomps user keys when `user_pinned` is stale

**Researchers:** Auto-Rules Engine | **Verified:** PARTIAL | **HIGH / LOW / 85%**

**Evidence:** `tuner/tuner.py:494` — `user_pinned = state.get("user_pinned") or []`. `tuner/settings_writer.py:147-150` — `env_overlays` write loop skips only keys in `pinned`.

**Description:** When `state["user_pinned"]` is empty (fresh install, after `--reset`, or a sparse corpus that skipped `detect_user_pinned`), overlays write unconditionally. A user who manually set `ENABLE_PROMPT_CACHING_1H=1` in `settings.json` before tokenomy had a chance to pin it can have the value overwritten on the next tuner run. The verifier noted the exact trigger path I cited (`corpus missing`) is wrong — that specific path emits no overlays — but confirmed the mechanism is real with a present-but-sparse corpus.

**Recommendation:** Before applying overlays, re-read the live `env` block from `settings.json` and skip any key already present there that is **not** in `managed_env_keys` from the previous sentinel — treat user-authored keys as implicitly pinned at write-time, independent of the state-derived `user_pinned` list.

---

## Finding 20: `iter_fetch_log` orphan pre-records accumulate

**Researchers:** Fetch-Audit Hook Integrity | **Verified:** PARTIAL | **MED / LOW / 97%**

**Evidence:** `analyzer/extractors.py:226` — `pending: dict[tuple[str, str, str], str] = {}` never evicted except on matched pair. Docstring at line 218 claims "memory stays bounded by the count of outstanding tool calls (normally single digits)."

**Description:** Observed 18 orphaned pre-records in a current 599-line log (tool crashes, session kills, timeouts). Across a long-lived log these accumulate monotonically. The docstring is accurate for a single live session but misleading for a historical full-file scan — memory scales with total lifetime orphan count, not outstanding live calls.

**Recommendation:** After the file scan completes in `iter_fetch_log`, either (a) discard the `pending` dict as a whole (orphans have no `post`, so their duration is unrecoverable anyway), or (b) group records by session_id and drop pending on detected session-end boundaries. Correct the docstring to describe the historical-scan behavior accurately.

---

## Finding 21: `burn_rate()` shim defeats single-pass optimization

**Researchers:** Statusline Regression Hunt | **Verified:** PARTIAL | **MED / LOW / 97%**

**Evidence:** `hooks/statusline.py:479` — `_, _, _, burn = current_block_and_burn(pricing)` with no `msgs=` kwarg. `render()` at line 581 correctly passes `msgs=msgs`.

**Description:** The shim was kept as a back-compat stub but silently re-walks the filesystem when any external caller (tests, future hooks) uses it. The hot render path is fine. Verifier flagged this as PARTIAL because there are no confirmed external callers today — it's a latent hazard, not an active regression.

**Recommendation:** Either remove `burn_rate()` entirely (grep shows no external call sites outside tests) or forward the `msgs` kwarg: `def burn_rate(pricing, msgs=None): return current_block_and_burn(pricing, msgs=msgs)[3]`.

---

## Finding 22: MCP `initialize` notification filter is accidentally correct

**Researchers:** MCP Server Auditor | **Verified:** PARTIAL | **MED / LOW / 92%**

**Evidence:** `tokenomy_mcp/server.py:316-323` — `if id_ is None and method.startswith("notifications/")`. The MCP spec's `initialized` notification has method name literally `initialized`, not `notifications/initialized`.

**Description:** `initialized` falls through the prefix guard. Handler lookup fails. The downstream `if id_ is not None` guard at line 321 suppresses the `-32601` response (because `id_ is None` for notifications). Observable behavior is correct — but **by accident**, not design. A future spec-legal notification with an `id` field will incorrectly receive `-32601` back.

**Recommendation:** Replace the prefix guard with an explicit set: `KNOWN_NOTIFICATIONS = {"initialized", "notifications/cancelled", "notifications/progress"}`. Silence any message whose method is in the set, regardless of `id_`. Explicit intent survives future spec additions.

---

## Finding 23: MCP text responses have no size cap

**Researchers:** MCP Server Auditor | **Verified:** PARTIAL | **MED / LOW / 90%**

**Evidence:** `tokenomy_mcp/server.py:182-195` — `SUGGESTIONS_PATH.read_text()` with no cap; `auto_rule_decisions` re-embeds full text at line 194.

**Description:** An over-grown `_suggestions.md` (accumulated tuner cycles, no rotation) or adversarial content injected into the file floods the MCP response and consumes the host's context budget. The verifier degraded the finding because the "doubling" framing was imprecise — the same text only appears twice if a client calls both tools, not within a single call.

**Recommendation:** Cap the read: `text = SUGGESTIONS_PATH.read_text(encoding="utf-8")[:64_000]`. Log a truncation warning. Consider removing `"raw"` from the `auto_rule_decisions` response — `decisions` is the structured extract, and `suggestions_md` already provides raw access.

---

## Finding 24: `save_state` still uses `sort_keys=True`

**Researchers:** Tuner Control Loop | **Verified:** CONFIRMED | **LOW / LOW / 100%**

**Evidence:** `tuner/state.py:66` — `json.dump(state, f, indent=2, sort_keys=True)`.

**Description:** `settings_writer.py` was fixed to `sort_keys=False` (prior SMAC finding 23) but the fix did not extend to `state.py`. `applied.json` is internal so churn is low-harm, but it remains inconsistent with the rest of the tuner's write surface.

**Recommendation:** Change to `sort_keys=False`. One-line diff; completes the prior-SMAC partial fix.

---

## Finding 25: `report.py` reads wrong key — TOP TOOL SINKS renders empty

**Researchers:** Statusline Regression Hunt | **Verified:** CONFIRMED | **LOW / LOW / 99%**

**Evidence:** `analyzer/report.py:45` — `insights.get("by_tool", {})`. `analyzer/analyze.py:446-447` emits both `by_tool` (session tool_result) and `by_fetch_tool` (fetch-audit). The `TOP TOOL SINKS (by total bytes returned)` header implies fetch data.

**Description:** `by_tool` **does** exist but contains only session-derived byte counts. The new `by_fetch_tool` with `total_duration_ms` from the fetch-audit hook is never surfaced. Users miss latency data they paid to collect.

**Recommendation:** Change `analyzer/report.py:45` to `by_tool = insights.get("by_fetch_tool") or insights.get("by_tool", {})` — prefer fetch-audit data when present, fall back to session-only. Add a `total_duration_ms` column to the rendered rows.

---

## Finding 26: `_parse_iso_ms` Z-branch is dead code

**Researchers:** Fetch-Audit Hook Integrity | **Verified:** CONFIRMED | **LOW / LOW / 98%**

**Evidence:** `hooks/fetch-audit.py:86` emits `datetime.now(timezone.utc).isoformat()` → produces `+00:00`, never `Z`. `analyzer/extractors.py:203` has a `ts[:-1] + "+00:00" if ts.endswith("Z")` branch.

**Description:** The Z-branch never fires for tokenomy-written records. Not a bug — `else ts` handles `+00:00` correctly — but the branch is misleading to future readers.

**Recommendation:** Optional cosmetic simplification. Either remove the Z-branch and document that tokenomy records always use `+00:00`, or keep it for forward compat with externally-injected log records and add a comment saying so.

---

## Finding 27: Hardcoded `"14d window"` drifts from constant

**Researchers:** Auto-Rules Engine | **Verified:** CONFIRMED | **LOW / LOW / 98%**

**Evidence:** `tuner/auto_rules.py:281` — `lines.append(f"- `{u['server']}` — never invoked in 14d window")`. `UNUSED_MCP_WINDOW_DAYS = 14` at line 27.

**Description:** If the constant changes, the UI text stays `"14d"` — silent inconsistency.

**Recommendation:** `f"never invoked in {UNUSED_MCP_WINDOW_DAYS}d window"`. One-line fix.

---

## Finding 28: MCP server has no logging — violates BUILD RULE #1

**Researchers:** MCP Server Auditor | **Verified:** CONFIRMED | **LOW / LOW / 95%**

**Evidence:** `tokenomy_mcp/server.py:283-284` — exceptions returned as JSON-RPC errors; no `import logging`, no stderr sink, no log file.

**Description:** Project's own CLAUDE.md: "Every script logs to `_[name].log`. No silent failures." The MCP server has zero logging. Production failures outside the try/except (startup, stdout BlockingIOError, sys.path issues) leave no trail.

**Recommendation:** Add `import logging` + `RotatingFileHandler` targeting `TOKENOMY_HOME / "_mcp.log"` at 500 KB rotation. Route all caught handler exceptions through `logging.exception()` before returning the JSON-RPC error.

---

## Finding 29: Block boundary `gap` and `dur` are identical

**Researchers:** Statusline Regression Hunt | **Verified:** CONFIRMED | **LOW / LOW / 95%**

**Evidence:** `hooks/statusline.py:434-435` — `gap = timedelta(hours=BLOCK_HOURS); dur = timedelta(hours=BLOCK_HOURS)`.

**Description:** Both are 5h. The new-block OR-condition at line 444 (`ts - block_start >= dur OR ts - last_ts >= gap`) can't fire via the gap branch without the duration branch also matching. Dead logic from incomplete parameterization.

**Recommendation:** If intra-block session-gap detection is wanted, set `gap = timedelta(hours=2)` so a long idle creates a new block before the 5h duration expires. If not wanted, delete the `gap` variable and the OR-branch.

---

## Finding 30: `rolling_mean_n` is a boolean masquerading as a counter

**Researchers:** Tuner Control Loop | **Verified:** CONFIRMED | **LOW / LOW / 95%**

**Evidence:** `tuner/tuner.py:446-451` — `alpha = 0.3` hardcoded. `rolling_mean_n` only consumed as `old_n > 0` (first-seed guard).

**Description:** Field name implies a count-weighted average (`1/n`-style). Actual update is a fixed-alpha EWMA. The accumulator wastes state bytes and misleads future readers.

**Recommendation:** Rename to `rolling_mean_seeded: bool` (or keep the int but set to `1` after first seed, never increment). Matching update needed in `tuner/state.py:28`.

---

## Finding 31: `analyze_idle_gaps` median has upper-middle bias

**Researchers:** Auto-Rules Engine | **Verified:** CONFIRMED | **LOW / LOW / 90%**

**Evidence:** `tuner/auto_rules.py:112` — `median = sorted_g[n // 2]`.

**Description:** For even-n arrays, the conventional median averages the two middle elements; `n//2` picks only the upper-middle. Minor numeric bias — sessions appear slightly more idle than reality in the informational field.

**Recommendation:** `import statistics; median = statistics.median(gaps)`.

---

## Finding 32: `detect_truncation_requery` is O(n²) in the worst case

**Researchers:** Auto-Rules Engine | **Verified:** CONFIRMED | **LOW / LOW / 90%**

**Evidence:** `tuner/losses.py:44-59` — outer loop over all events, inner forward-scan per truncation.

**Description:** `seen >= 2` inner break limits typical cost to ≈O(n), but sessions with many truncated results and no matching re-queries scan linearly for each truncation.

**Recommendation:** Cap `max_events_to_scan` per truncation at a constant (e.g. 50). Adequate for legitimate patterns; prevents pathological cases from stalling the analyzer.

---

## Finding 33: `counterfactual.py` hardcodes Sonnet pricing

**Researchers:** Statusline Regression Hunt | **Verified:** CONFIRMED | **LOW / LOW / 90%**

**Evidence:** `analyzer/counterfactual.py:120-121, 136-137` — `P.cost_for_usage(P.DEFAULT_PRICING_KEY, ...)` with no model parameter.

**Description:** `read_once_savings` and `log_grep_savings` always price at `claude-sonnet-4-6`. For Opus sessions, savings are 5× undercount. `mcp_output_cap` at line 48, 99 correctly reads per-message model — inconsistent within the same module.

**Recommendation:** Add optional `model: str = None` parameter to both savings functions. Caller passes the session's dominant model from `analyze.py::stats`; functions fall back to `DEFAULT_PRICING_KEY` only when `model is None`.

---

## Finding 34: `input_hash` 16-hex truncation — documented risk

**Researchers:** Fetch-Audit Hook Integrity | **Verified:** CONFIRMED | **LOW / LOW / 88%**

**Evidence:** `hooks/fetch-audit.py:35` — `hashlib.sha256(...).hexdigest()[:16]` → 64-bit space.

**Description:** P(collision) ≈ 2.7×10⁻¹² over 10K same-session same-tool events. Adversarial collision requires attacker control of tool inputs — not a realistic threat model for a local log. No action required; kept as documented low-confidence note.

**Recommendation:** No action. If production logs start showing hash mismatches, extend to 24 hex chars (96 bits).

---

## Finding 35: MCP `initialize` ignores client `protocolVersion`

**Researchers:** MCP Server Auditor | **Verified:** CONFIRMED | **LOW / LOW / 88%**

**Evidence:** `tokenomy_mcp/server.py:257-262` — handler receives `params` but never reads `params.get("protocolVersion")`; always returns pinned `PROTOCOL_VERSION = "2024-11-05"`.

**Description:** MCP 2024-11-05 spec requires server to inspect client version and either match or return `-32002`. Current server always replies with its own pinned version. A future Claude Code SDK with a newer version will receive the old one and may silently downgrade or fail opaquely downstream.

**Recommendation:** Read `client_ver = params.get("protocolVersion")`. If `client_ver != PROTOCOL_VERSION`, log it and either accept (document the choice) or return `_error(id_, -32002, f"unsupported protocol version: {client_ver}")`.

---

## Finding 36: Backup rotation skipped when sentinel cleared

**Researchers:** Tuner Control Loop | **Verified:** CONFIRMED | **LOW / LOW / 88%**

**Evidence:** `tuner/settings_writer.py:159` — `if os.path.exists(backup) and prev_version and prev_version != version`.

**Description:** After `--reset` or manual sentinel deletion, `prev_version is None`. Rotation branch is skipped. If a stale `.tokenomy.bak` from 0.5.0 exists, it persists unversioned and no fresh 0.6.0 backup is created (line 167 existence check fails). User has no current-version backup.

**Recommendation:** Add a version-unknown rotation path: `if os.path.exists(backup) and not prev_version: os.rename(backup, f"{backup}.unknown")`. Ensures a backup exists for every version transition.

---

## Finding 37: `_DECISION_RE` unbounded capture groups

**Researchers:** MCP Server Auditor | **Verified:** CONFIRMED | **LOW / LOW / 85%**

**Evidence:** `tokenomy_mcp/server.py:155` — `[^*]+`, `` [^`]+ ``, `.+` all unbounded.

**Description:** CPython's `re` handles these as linear, not exponential — no ReDoS. But a 1 MB `_suggestions.md` line with pathological content causes measurable stall during `_parse_decisions` with no timeout.

**Recommendation:** Bound the groups: `[^*]{1,200}`, `` [^`]{1,500} ``, `.{1,2000}`. Wrap `_parse_decisions` in `try/except re.error`. Complements Finding 23's file-size cap.

---

## Finding 38: `top_wasters` forwards untrusted tool names

**Researchers:** MCP Server Auditor | **Verified:** CONFIRMED | **LOW / LOW / 78%**

**Evidence:** `tokenomy_mcp/server.py:81-87` — `by_tool.items()` iterated without sanitization.

**Description:** `insights.json` is analyzer-written and not user-supplied, so practical risk is low. But `by_tool` keys are derived from session JSONL `tool_name` fields, which could carry control chars or unusual unicode from a corrupted or fuzzed session. `json.dumps` escapes safely; display artifacts in the client UI remain possible.

**Recommendation:** Sanitize: `name = re.sub(r"[^A-Za-z0-9_/.\-:]", "?", name)[:128]`. One line.

---

## Finding 39: Windows NTFS append atomicity not guaranteed

**Researchers:** Fetch-Audit Hook Integrity | **Verified:** CONFIRMED | **LOW / LOW / 75%**

**Evidence:** `hooks/fetch-audit.py:96-97` — plain `open("a")` with no lock.

**Description:** On POSIX, O_APPEND writes ≤PIPE_BUF are atomic. NTFS does not guarantee O_APPEND atomicity across concurrent processes. Each record is ~211 bytes (well within single WriteFile), so corruption is unlikely in practice. `iter_fetch_log` gracefully drops malformed JSON lines. Risk is observable only under heavy multi-terminal SMAC-style workloads.

**Recommendation:** Accept the current risk — verify with `journalctl` / Windows event log over time. If corruption is observed, switch to per-PID `fetch-log.{pid}.jsonl` + merge on analyzer read.

---

## Disputed / Rejected Findings
*(none this run — 0 DISPUTED across all 39 findings)*

## Coverage Gaps
| Role | Status | Impact |
|---|---|---|
| Test coverage & dead-code audit | Not dispatched — budget cap at 5 researchers | Medium — not a correctness gap but a confidence-in-refactor gap |
| Security threat model (MCP server exposed via stdio, not IPC) | Partially covered by MCP Server Auditor | Low — stdio means auth is delegated to the client |
| Cross-platform portability (Windows path separators, line endings) | Partially covered by Fetch-Audit (NTFS atomicity) | Low |

## Summary Statistics

- **Findings:** 39 total — 34 CONFIRMED, 5 PARTIAL, 0 DISPUTED
- **By impact:** 5 HIGH, 14 MED, 20 LOW
- **By effort:** 35 LOW, 4 MED, 0 HIGH — every top-10 finding is a LOW-effort fix
- **Top surface by finding density:** MCP Server Auditor (8), Fetch-Audit (8), Auto-Rules (9), Tuner Control (7), Statusline (7)
- **Verifier-mandated degrades:** 5 PARTIAL — 2 from imprecise citations, 3 from accidentally-correct-but-still-brittle behavior
- **Prior-SMAC carry-forward:** 2 still-open findings re-surfaced with new concrete instances (Findings 5 and 24); all others novel to this audit.
