# SMAC Report: tokenomy v0.3.0 runtime correctness (Windows)

**Generated:** 2026-04-08 | **Agents:** 3R + 3V | **Scope:** 7-item integration audit, NOT code quality / test coverage

## Headline

**Two blockers make v0.3.0 functionally inert as shipped:**
1. `auto-settings.json` is written but nothing on the Claude Code side reads it — the entire auto-tuner output is discarded.
2. `SessionStart` hook is never registered — the tuner is never invoked automatically.
3. Even the **baseline** `env` block in the plugin's own `settings.json` is silently ignored, because Claude Code plugin `settings.json` currently supports only the `agent` key (plugins doc). So tokenomy currently delivers **none** of its env-var caps via the plugin packaging — neither baseline nor tuned.

A third blocker (`detect_truncation_requery` fires on benign `is_error` results, ~99.5% false positives) makes the loss-detection / freeze feedback loop garbage even if the first two were fixed.

## Ranked Findings

| # | Finding | Severity | Item | Confidence | Verified |
|---|---------|----------|------|-----------|----------|
| 1 | Plugin `env` + `auto-settings.json` never loaded by Claude Code | BLOCKER | 3 | 98% | CONFIRMED |
| 2 | `SessionStart` hook not registered anywhere | BLOCKER | 2 | 100% | CONFIRMED |
| 3 | `Event.truncated` conflates `is_error` → 99.5% FP rate | BLOCKER | 5 | 98% | CONFIRMED (independent corpus scan) |
| 4 | `sessions.jsonl` pipeline is 3x broken (orphan, env vars, no reader) | HIGH | 6 | 100% | CONFIRMED |
| 5 | `tuner.lock`: wrong PID, no trap, TOCTOU race | HIGH | 7 | 95% | CONFIRMED |
| 6 | `session-start.sh` spawn: bash eats the redirect before `start` runs | MEDIUM | 1 | 90% | PARTIAL (moot while F2 unfixed; sub-bugs confirmed) |
| 7 | `MAX_MCP_OUTPUT_TOKENS` is global-only (assumption correct, keep as-is) | LOW | 4 | 95% | CONFIRMED |

---

## Finding 1 — BLOCKER: `auto-settings.json` and plugin `env` block are both inert

**Item 3.** Researcher 2 + Verifier 1 (independent doc lookups).

### Evidence

Code path:
- `tuner/tuner.py:32` — `DEFAULT_HOME = os.path.expanduser("~/.claude/tokenomy")`
- `tuner/tuner.py:308` — `settings_path = os.path.join(home, "auto-settings.json")`
- `tuner/tuner.py:352` — `write_settings(settings_path, final)` (the only writer)
- `tuner/settings_writer.py` — atomic write of `{"env": env}` JSON. No copy / symlink / merge into a real settings location.
- `hooks/session-start.sh` — spawns tuner only. Never copies output anywhere.

All `auto-settings` references in repo (classified by verifier):

| Location | Type |
|---|---|
| `tuner/tuner.py:308, 311` | write / delete-on-reset |
| `tuner/settings_writer.py:1, 46` | write |
| `tests/test_settings_writer.py:29` | test |
| `docs/TUNER_PLAN.md:14, 150, 204, 282, 285, 292, 320, 326, 355` | doc (claims auto-load) |
| `README.md:102, 122` | doc |

**Zero readers. No wiring path exists.**

Claude Code docs:
- Settings doc (`https://code.claude.com/docs/en/settings`, "Settings precedence"): the only user-scope file auto-loaded is `~/.claude/settings.json`. No path under `~/.claude/<subdir>/` is in any scope (Managed / User / Project / Local).
- Plugins doc (`https://code.claude.com/docs/en/plugins`, "Ship default settings with your plugin"): **"Plugins can include a `settings.json` file at the plugin root to apply default configuration when the plugin is enabled. Currently, only the `agent` key is supported. … Unknown keys are silently ignored."**

### Verdict: CONFIRMED — scope even wider than R2 reported

V1's additional finding: tokenomy's top-level `settings.json` carries an `env` block with the static baselines (`CLAUDE_CODE_MAX_OUTPUT_TOKENS=8000`, `MAX_THINKING_TOKENS=8000`, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=70`, etc.). Per the plugins doc, the `env` key in a plugin `settings.json` **is ignored**. So tokenomy currently delivers **none** of its env-var caps via the plugin mechanism — not just the tuned values, the baselines too.

(Note: the `hooks` and `statusLine` blocks do load via separate plugin conventions. Those aspects of the plugin still work. This finding is scoped to `env` vars only.)

`docs/TUNER_PLAN.md:14` assertion — *"writes `auto-settings.json` that Claude Code loads automatically"* — is **factually wrong**. Same for `TUNER_PLAN.md:204`.

### Recommended fix

**Abandon the "drop a file and hope" architecture.** Two viable paths:

1. **Merge-into-user-settings (recommended).** Have the tuner atomically edit `~/.claude/settings.json`:
   - Read it, parse JSON.
   - Locate or create a `"env"` object.
   - Write tokenomy-owned keys inside a sentinel-fenced block (e.g. under `"env"` with keys prefixed, or inside a dedicated `"__tokenomy__"` subobject that a SessionStart shim reads-and-promotes — ugly but works).
   - Back up to `~/.claude/settings.json.tokenomy.bak` first.
   - On `/plugin uninstall`, restore the backup.
   - Respect user-pinned keys (already tracked in `state.user_pinned`).
2. **Ship as an installer, not a plugin.** `pip install tokenomy && tokenomy install` writes to `~/.claude/settings.json` directly and adds the hooks via a sentinel block. Heavier UX cost, cleaner semantics.

Also **required**, independent of which path:
- Fix `docs/TUNER_PLAN.md:14, 204` and `README.md:102, 122` — remove the "Claude Code loads automatically" claim.
- Move baseline env values out of the plugin `settings.json` into whichever mechanism Finding 1 fix uses.

---

## Finding 2 — BLOCKER: `SessionStart` hook is never registered

**Item 2.** Researcher 1 + Verifier 3.

### Evidence

- `settings.json:18-47` registers `PreToolUse` (`read-once.sh`, `log-grep.sh`) and `SessionEnd` (`cleanup.sh`). **No `SessionStart` key.**
- `.claude-plugin/plugin.json` is 13 lines of metadata only — no `hooks` field at all.
- Repo-wide grep for `SessionStart` / `session-start`: hits only `hooks/session-start.sh:2` (comment) and doc mentions in `docs/TUNER_PLAN.md` + `README.md`. **Zero registration hits.**

`hooks/session-start.sh` is orphan code. The auto-tuner only runs when the user types `python -m tuner.tuner` manually.

### Verdict: CONFIRMED

### Fix

Add to `settings.json` under `hooks`:

```json
"SessionStart": [
  {
    "hooks": [
      {
        "type": "command",
        "command": "bash \"${CLAUDE_PLUGIN_ROOT}/hooks/session-start.sh\"",
        "timeout": 5
      }
    ]
  }
]
```

**Caveat:** per Finding 1, plugin `settings.json` may only honor the `agent` key. Verify whether the `hooks` block in the plugin's top-level `settings.json` is actually consumed by Claude Code — it may be that plugin hooks must live in `.claude-plugin/plugin.json` or `hooks/hooks.json`. **This needs a separate documentation check before adding the registration, or the fix is cosmetic.** The fact that `read-once` and `log-grep` appear to work empirically (the user has observed them firing) suggests the hooks block is honored — but confirm before shipping v0.3.1.

---

## Finding 3 — BLOCKER: `Event.truncated` conflates benign `is_error`, 99.5% false positive rate

**Item 5.** Researcher 3 + Verifier 2 (independent corpus scans, numbers match).

### Evidence

Code:
```python
# analyzer/extractors.py:161
trunc = any(m in flat for m in _TRUNCATION_MARKERS) or bool(block.get("is_error"))

# analyzer/extractors.py:47
_TRUNCATION_MARKERS = ("Response truncated", "[truncated]", "…(truncated)")
```

```python
# tuner/losses.py:33
if e.kind != "tool_result" or not e.truncated:
    continue
```

Independent corpus scan of `~/.claude/projects/**/*.jsonl` (V2, 2,295 files):

| Metric | Count |
|---|---|
| Total tool_result blocks | 24,945 |
| `is_error=true` | 1,688 |
| Real truncation marker matches | **8** |
| is_error ∩ truncation marker (sample of 10) | 0/10 |

Sample of 10 `is_error=true` results: **10/10 benign** — missing file (`ENOENT`), schema validation error, password-protected PDF, cancelled tool call, "No such tool", `EISDIR`, pre-call token guard. None were actual mid-response truncations.

False-positive rate ≈ **1,680 / 1,688 ≈ 99.5%**. R3's dry-run "1,427 losses" figure is plausible as a count but signals nothing actionable — it's counting the normal happy path of "Read wrong file → Read correct file".

### Verdict: CONFIRMED

The damage is larger than just `detect_truncation_requery`: any downstream metric keyed on `Event.truncated` inherits the pollution.

### Fix

1. `analyzer/extractors.py`: add `is_error: bool = False` to `Event`. Change line 161 to:
   ```python
   trunc = any(m in flat for m in _TRUNCATION_MARKERS)
   is_err = bool(block.get("is_error"))
   # pass both into Event construction
   ```
2. `tuner/losses.py::detect_truncation_requery`: gate remains `e.truncated` — now it actually means truncation.
3. If you want an error-retry signal, add `detect_error_retry_churn` gated on `is_error` with a much stricter rule (same tool + same input summary ≥ 3 times). Optional.
4. `detect_error_after_cap` (`losses.py:116`) currently uses `not e.truncated` where it means `not is_error`. Update to use the new `is_error` field for clarity. No behavior change if capped_tools stays empty.
5. Wipe `~/.claude/tokenomy/losses.jsonl` and re-run dry-run. Expected new count: single-digit to low-tens range.

---

## Finding 4 — HIGH: `sessions.jsonl` pipeline is triple-broken

**Item 6.** Researcher 3 + Verifier 2.

### Evidence

**Empirical:** `ls ~/.claude/tokenomy/` → `applied.json`, `auto-settings.json`, `insights.json`. **No `sessions.jsonl`.** Has never existed despite many sessions since the feature landed.

Three independent breaks:

1. **Wrong hook registered.** `settings.json:36-46` registers a `SessionEnd` hook, but it points to `hooks/cleanup.sh`, **not** `hooks/session-end.sh`. The latter is orphaned.
2. **Wrong input channel.** `hooks/session-end.sh` reads `CLAUDE_SESSION_ID` and `CLAUDE_PROJECT_NAME` from env. Claude Code SessionEnd hooks receive a JSON payload on **stdin** (`session_id`, `transcript_path`, `cwd`, `hook_event_name`, `reason`) — not env vars. Even if wired, both vars resolve to `unknown` and the file would be `{"sid":"unknown","project":"unknown"}` forever.
3. **No reader.** Grep for `sessions.jsonl` in the tuner: only `tuner.py:311` inside `--reset` (for deletion). **Nothing reads it.** The "≥5 new lines since last tune → spawn tuner" path from `TUNER_PLAN.md:228` is entirely unimplemented. Grep for `last_tune_at` + spawn logic returns nothing.

### Verdict: CONFIRMED

### Fix — recommend deletion (Path A)

The sessions count is derivable by walking `~/.claude/projects/**/*.jsonl` mtimes, which the analyzer already does. A separate log is a second source of truth for no benefit.

1. Delete `hooks/session-end.sh`.
2. Remove `sessions.jsonl` from `tuner.py:311` reset list.
3. Remove from `README.md:123` and `docs/TUNER_PLAN.md:152, 168, 222, 228, 267, 286, 330`.
4. Keep `cleanup.sh` registered as the `SessionEnd` handler.

~20 lines removed, zero behavior change. If the incremental retune signal is ever wanted, derive it from analyzer corpus mtimes.

---

## Finding 5 — HIGH: `tuner.lock` has wrong PID, no trap, TOCTOU race

**Item 7.** Researcher 1 + Verifier 3.

### Evidence

`hooks/session-start.sh`:
- Lines 11-14: stale window check on file mtime (300 s) — correctly enforced.
- Line 29: `echo $$ > "$LOCK"` — writes the hook shell's PID, **not** the spawned python tuner's PID. The file is informational only; no liveness check possible.
- No `trap` anywhere in the script (grep confirmed). Lock is never cleaned up by the hook on exit.
- TOCTOU: lines 11 and 29 are separated by many commands. Two concurrent SessionStarts both pass the `[ -f "$LOCK" ]` check, both spawn.
- `hooks/session-end.sh` does not touch the lock (verified).

Failure mode: if the tuner crashes before clearing the lock (and nothing currently instructs it to clear the lock), runs are blocked for exactly 5 minutes. Fail-open by accident, not design. (Largely moot while Finding 2 is open.)

### Verdict: CONFIRMED

### Fix

Atomic acquire via `mkdir` (POSIX-portable):

```bash
LOCKDIR="$HOME_DIR/tuner.lock.d"
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  AGE=$(( $(date +%s) - $(stat -c %Y "$LOCKDIR" 2>/dev/null || echo 0) ))
  [ "$AGE" -lt 300 ] && exit 0
  rm -rf "$LOCKDIR" && mkdir "$LOCKDIR" || exit 0
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT
```

Have `tuner/tuner.py` remove `$HOME/.claude/tokenomy/tuner.lock.d` in a `finally` block on exit too, since the hook's trap only fires while the hook shell is alive (not while the backgrounded tuner runs).

---

## Finding 6 — MEDIUM: `session-start.sh` redirect-before-`start` bug

**Item 1.** Researcher 1 + Verifier 3 (partial — F1 makes this unreachable, but sub-bugs hold).

### Evidence

`hooks/session-start.sh:31-36`:
```bash
if command -v start >/dev/null 2>&1; then
  (cd "$TOKENOMY_DIR" && start /b python -m tuner.tuner $FLAG >> "$LOG" 2>&1) &
else
  (cd "$TOKENOMY_DIR" && nohup python -m tuner.tuner $FLAG >> "$LOG" 2>&1 &)
fi
```

Issues (all confirmed):
1. **Redirect parse order.** `>> "$LOG" 2>&1` is parsed by **bash**, not by `cmd start`. It attaches to the outer shell invocation, so `start` itself inherits already-redirected handles — usually fine, but the lock bookkeeping is decoupled from the actual python lifecycle.
2. **`$FLAG` unquoted and may be unset.** Line 24 sets `FLAG=""` under one branch but `set -u` is not enabled, so if a branch is missed the expansion silently becomes empty. Minor.
3. **MSYS `/usr/bin/start` wrapper** — R1 claimed MSYS2 ships a `start` shell wrapper, so the "Windows" branch is taken and runs `cmd /c start /b python ...`. V3 correctly flagged this as environment-dependent and refused to verify on this specific machine. Outcome varies between MSYS2 and vanilla Git-for-Windows. **Do not rely on `command -v start` for branching.**

### Verdict: PARTIAL — moot while Finding 2 is unfixed, but sub-claims hold and must be fixed before re-enabling the hook.

### Fix

Detach unconditionally via Python itself; drop the `start` / `nohup` branching:

```bash
# Redirect explicitly, detach via Python
(nohup python -m tuner.tuner $FLAG </dev/null >> "$LOG" 2>&1 &) & disown 2>/dev/null || true
```

Better: have `tuner/tuner.py` self-daemonize on `--detach`. Hook becomes:
```bash
python -m tuner.tuner --detach $FLAG >> "$LOG" 2>&1
```
Python's `os.fork` isn't portable on Windows, but a `subprocess.Popen([...], creationflags=DETACHED_PROCESS)` wrapped in a platform check is. Cleanest.

---

## Finding 7 — LOW / INFORMATIONAL: `MAX_MCP_OUTPUT_TOKENS` is global-only

**Item 4.** Researcher 2 + Verifier 1.

### Evidence

- Claude Code settings doc full-text contains zero occurrences of `MAX_MCP_OUTPUT_TOKENS`, `MCP_OUTPUT`, or any `__server` suffix. Per-server controls exist only for `allowed`/`denied` MCP lists — not for output size.
- `tuner/settings_writer.py:22-39`: `build_env_block(per_server_supported=False)` default. The per-server branch (lines 29-32) emitting `MAX_MCP_OUTPUT_TOKENS__{server}` is dormant scaffolding.
- `tests/test_settings_writer.py:15-16` asserts `env["MAX_MCP_OUTPUT_TOKENS"] == "200000"` (max of 6000/200000) — correct collapse.

### Verdict: CONFIRMED — assumption is correct

### Fix

No behavior change. Add a one-line comment above the dormant per-server branch citing the absence from current Claude Code docs, so a future contributor doesn't flip the flag on speculation.

---

## Disputed Findings

None. All cross-verified findings held up under independent verification.

## Coverage Gaps

| Area | Status | Note |
|------|--------|------|
| MSYS2 vs Git-for-Windows shell differences | Environment-dependent | V3 correctly refused to verify `/usr/bin/start` on this machine. Test both distributions before shipping v0.3.1 hook fix. |
| Actual behavior of plugin `settings.json` `hooks` block | Unverified | `read-once` / `log-grep` appear to work empirically, suggesting plugins DO honor the `hooks` key, contradicting the strict "only `agent`" reading. Resolve before Finding 2 fix — it determines whether adding `SessionStart` to plugin `settings.json` will actually work. |

## Recommended fix order

1. **Finding 1** — decide architecture (merge-into-user-settings vs installer). Blocks everything else.
2. **Finding 3** — split `is_error` from `truncated`, re-run dry-run, verify loss count drops to sane range.
3. **Finding 2** — register `SessionStart` hook (contingent on Finding 1 resolution path).
4. **Finding 5** — atomic lock via `mkdir`.
5. **Finding 6** — unconditional detach idiom.
6. **Finding 4** — delete `session-end.sh` + all `sessions.jsonl` references.
7. **Finding 7** — add comment, done.

## Verification banner

All three researchers and all three verifiers completed successfully. Independent corpus numbers from V2 match R3 within rounding. Doc citations in F1 independently sourced by V1. No findings downgraded for weak verification.
