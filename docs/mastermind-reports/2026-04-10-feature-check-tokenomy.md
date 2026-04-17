# Mastermind Report: Feature Check for Tokenomy

**Generated:** 2026-04-10 | **Council:** 7 (3 fixed + 4 dynamic) | **Challengers:** 7 | **Spec agents:** 3

## The Idea
Feature check for Tokenomy: flag blind spots, suggest improvements, optimize it to the maximum.

## Ranked Approaches

| # | Approach | Score | Supported By | Verdict |
|---|----------|-------|-------------|---------|
| 1 | Harden the Core | 1.95 | 3 members (Tech Architect, Security Analyst, Critical Challenger) | VIABLE |
| 2 | Trust & Observability Layer | 1.81 | 4 members (DX Specialist, Product Strategist, Critical Challenger, Challengers) | VIABLE |
| 3 | Expand Interception Coverage | 1.37 | 2 members (Hook Specialist, Stats Expert) | RISKY |

## Winning Approach: Harden the Core

### Summary
Fix critical bugs and reliability gaps in Tokenomy's existing tuner, loss detectors, state management, and first-run behavior before adding features or improving UX. The tuner operates as an open-loop controller with no feedback on cap effectiveness, a key loss detector is dead code (empty `capped_tools=()`), cross-session event concatenation causes false-positive loss detections, and caps are written at confidence levels as low as 0.02 with no floor gate. Additionally, BASELINE_ENV vars (disabling auto-memory, telemetry, autoupdater) are injected on first run without user consent.

### Why This Won
The highest-scored individual points (open-loop tuner: 2.61, confidence gate: 2.34) received **STRONG** verdicts from challengers, meaning the reasoning survived adversarial scrutiny without meaningful holes. Approach C (Trust & Observability) scored close at 1.81 but its strongest points were more mixed under challenge. The core reliability fixes are prerequisites: no amount of observability helps if the tuner silently produces bad caps.

---

## Product Spec

### MVP Features (v0.4.0)

| # | Fix | File(s) | Why MVP |
|---|-----|---------|---------|
| 1 | Close the control loop — store rolling mean output tokens, compare pre/post-cap | `tuner/tuner.py`, `tuner/state.py` | Tuner has no evidence its caps helped |
| 2 | Pass capped_tools to detect_all — fix dead detector | `tuner/tuner.py:377`, `tuner/losses.py` | 1-line fix; loss detection is 0% effective |
| 3 | Per-session loss detection — run detectors inside iter_corpus loop | `tuner/tuner.py` | Cross-session false positives cause spurious 14-day freezes |
| 4 | Confidence floor — skip computed caps when effective_n < 200 | `tuner/tuner.py` | Prevents statistically meaningless caps |
| 5 | First-run consent — write summary of BASELINE_ENV changes | `tuner/consent.py` (new), `hooks/session-start.sh` | Disabling auto-memory/telemetry without consent is a distribution blocker |
| 6 | Lock stale-detection via PID — write PID+timestamp in lock dir | `hooks/session-start.sh`, `tuner/tuner.py` | Python crash leaves lock forever, permanently blocking tuner |
| 7 | Backup rotation on version bump | `tuner/settings_writer.py` | One-shot backup is useless after the first install |
| 8 | Autocompact confound — use pre-cap context% as baseline | `tuner/tuner.py`, `tuner/state.py` | Cap changes the signal it measures (feedback loop) |

### V2 Features (deferred)
- Savings attribution in statusline ("Tokenomy saved: $X today")
- /tokenomy-status on-demand summary command
- First-run notification visible in terminal
- Bypass instructions in block messages
- Behavioral contract document
- Expanded hook coverage (Grep, Bash, Write/Edit no-op guard)
- Structured JSON log support in log-grep
- Silent MCP truncation detector
- Per-project cap differentiation
- CLAUDE.md behavioral eval harness

### User Stories
1. As a user with < 5 sessions, I want Tokenomy to withhold cap writes until enough data exists so my settings aren't degraded by meaningless caps.
2. As a developer distributing Tokenomy, I want first-run env changes to require explicit opt-in so I'm not silently altering users' environments.
3. As a user with concurrent sessions, I want loss events detected per-session so truncation in one session doesn't freeze working caps in another.
4. As a user whose cap was tightened, I want the tuner to compare pre/post output averages so it can detect when a cap broke workflows.
5. As a user after a Python crash, I want the lock to detect staleness via PID so I'm not permanently locked out.
6. As a user upgrading Tokenomy, I want a versioned backup so I have a restore point.
7. As a user with loss detectors, I want detect_error_after_cap to actually fire on capped tools.
8. As a developer reading tuner logs, I want to see "confidence too low -- skipping write" when the gate blocks.

### Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| False-positive freeze rate | 0 cross-session false positives | Inject synthetic 2-session transcripts, assert no cross-session freezes |
| Confidence gate coverage | 100% of writes blocked at effective_n < 200 | Unit test: tuner with n=0,1,2,4 -> no settings mutation |
| Dead detector activation | detect_error_after_cap fires on real transcripts | Log counter; assert > 0 in integration test |
| Lock recovery | 0 permanent lockouts in 1000 crash cycles | Fuzz test: kill tuner randomly, assert lock recovery |
| Backup versioning | New .tokenomy.v{prev}.bak on each upgrade | Integration test across version boundaries |

### Competitive Edge
Other token tools are read-only dashboards. Tokenomy's edge is the closed-loop feedback controller: measure -> cap -> verify -> adjust. After v0.4: trustworthy caps (confidence-gated), consent-clean distribution, self-healing sessions (lock recovery + per-session loss detection), and verifiable improvement (rolling output baseline). The moat is feedback loop integrity.

---

## Technical Spec

### Architecture
Post-fix data flow:
```
session-start.sh
  |-- write PID+ts into tuner.lock.d/pid            [Fix 6]
  +-- spawns tuner.py
       |-- first-run: consent gate -> summary file   [Fix 5]
       |-- collect_samples()
       |    +-- iter_corpus() with per-session loss detection   [Fix 3]
       |         |-- detect_all(session_events, capped_tools=current_servers)
       |         +-- accumulate per_session_losses[]
       |-- compute_caps_per_setting()
       |    +-- autocompact uses pre-cap ctx% baseline   [Fix 8]
       |-- apply_loss_freezes()
       |-- tick_cooldowns()
       |-- apply_hysteresis_cooldown_freeze()
       |-- confidence floor gate (skip if eff_n < 200)   [Fix 4]
       |-- control loop: compare caps vs rolling mean   [Fix 1]
       |-- merge_into_user_settings()
       +-- save_state() with rolling_mean, pre_cap_ctx   [Fix 1, 8]
       +-- backup rotation on version bump   [Fix 7]
```

### Stack
Pure Python + bash. No new dependencies. One new file: `tuner/consent.py` (~40 lines).

### Build vs Buy

| Decision | Choice | Reason |
|----------|--------|--------|
| Rolling mean storage | Custom field in applied.json | Already have atomic state writer |
| PID liveness check | `kill -0 $PID` in bash | POSIX portable, zero deps |
| Consent summary | Plain .txt file | Survives without terminal |
| Backup rotation | `os.replace()` | Already in codebase |
| Pre-cap baseline split | Timestamp split on existing data | No new corpus fields needed |

### Implementation Phases

| Phase | What | Complexity | Dependencies |
|-------|------|-----------|-------------|
| 1 | Fix 2 (capped_tools), Fix 3 (per-session losses), Fix 6 (lock PID) | LIGHT | None |
| 2 | Fix 4 (confidence floor), Fix 7 (backup rotation) | LIGHT | None |
| 3 | Fix 1 (control loop feedback) | MODERATE | Phase 1 |
| 4 | Fix 8 (autocompact confound) | MODERATE | Phase 1 |
| 5 | Fix 5 (first-run consent) | LIGHT | Do last (stable BASELINE_ENV) |

Phases 1+2 can run in parallel. Phases 3+4 both depend on Phase 1 but not each other. Phase 5 is independent but do last.

---

## Risk Analysis

### Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Tuner makes things worse before baseline exists | HIGH | HIGH | Capture 5-session rolling baseline before tuner param changes; gate merge on >=1 post-fix session |
| Dead detector fix starts blocking valid rules | MED | HIGH | List every rule type, confirm whitelist before writing tuple; ship behind flag |
| Lock leaves stale file on process kill (Windows no fcntl) | MED | MED | PID-in-lockfile with process existence check; cross-platform |
| Confidence gate rejects valid cold-start data | MED | MED | Gate at N>=200 with pass-through below threshold |
| Backup rotation races on rapid restarts | LOW | MED | os.replace() atomic; test with 3 rapid restarts |
| Silent false-block erodes trust | HIGH | MED | Add log line on every block: filename, reason, session ID |
| BASELINE_ENV persistence creates hidden dependency | MED | MED | Write expiry timestamp alongside env snapshot |

### Kill Conditions
1. Token cost rises >15% across 3 consecutive post-fix sessions -> stop, revert, redesign.
2. Any user reports unexpected behavior in non-Tokenomy project after settings write -> full stop on all writes.
3. Filling empty tuple causes any hook to silently drop a previously-handled rule type -> requires full inventory first.

### Biggest Unknown
Whether the tuner's adjustments correlate with actual token reduction at all. De-risk: instrument one week with read-only telemetry (tokens in/out, truncation count, adjustment applied). Verify r > 0.3 before shipping tuner logic changes.

### Scope Creep Traps
- Adaptive half-life / vacation gap handling -- cold-start coherence unsolved; not Phase 1
- MCP server collapse detection -- mechanism misdiagnosed; needs separate audit
- CLAUDE.md eval framework -- valid long-term, zero bearing on current fixes
- Per-project caps -- project attribution reliability unverified
- PostToolUse hook expansion -- API capability unconfirmed

---

## Challenger Highlights

**Most valuable challenges that shaped the spec:**

1. **vs Technical Architect:** _MSG_CACHE memory growth premise was FLAWED (process dies each render) -- corrected the fix rationale from memory to I/O. MCP cap collapse mechanism was misidentified (not max() in code, but env var collapse in build_env_block). Lock stale-detection was misdescribed (no mtime logic exists; real issue is killed process leaving dir forever).

2. **vs Critical Challenger:** CLAUDE.md "untestable" reframed as "currently untested but testable via evals" -- conflating was corrected. log-grep block+reason mechanism needs empirical verification before HIGH severity. settings.json corruption mechanism was backwards (load returning {} drops user settings, doesn't overwrite them).

3. **vs Hook Specialist:** read-once mtime fix was FLAWED -- code already uses mtime+size, and st_mtime_ns doesn't solve same-content-different-mtime. PostToolUse output mutation capability unverified -- 4 of 10 proposals depend on it. Edit tool has different input schema than Write (old_string/new_string vs content).

4. **vs Stats Expert:** Confidence ramp sqrt(n/1000) was FLAWED -- preference assertion not calibration. Cold-start coherence gap identified across 3 independent proposals (adaptive decay, confidence ramp, CV-scaling) -- none address first 2-4 weeks. No interaction effects modeled between proposals.

5. **vs DX Specialist:** "--observe mode" was FLAWED -- placebo, not trust ramp. Real trust comes from behavioral contract + visible first-run summary + easy undo. Version bump changelog reframed: the problem is silent re-tune, not missing changelog.

6. **vs Security Analyst:** !fulllog MCP exploit was FLAWED -- MCP servers don't write into Claude's response stream. Fail-open endorsed in opening but flagged as risk later -- internal contradiction. Lock mtime bug (Point 7) identified as highest ROI fix (2 lines, prevents permanent lockout).

7. **vs Product Strategist:** Installation friction called WEAK -- skepticism, not friction, is the real barrier. 80% savings claim needs methodology footnote. Competitive window argument buries the real moat (Anthropic has no incentive to reduce token consumption).

## Coverage Gaps

| Role | Status | Impact |
|------|--------|--------|
| Product Strategist | Full report + challenged | Deferred to V2 (trust/observability layer) |
| Technical Architect | Full report + challenged | 7/10 points directly in winning approach |
| Critical Challenger | Full report + challenged | 6/10 points directly in winning approach |
| Hook & Interception Specialist | Full report + challenged | Deferred -- PostToolUse API unverified |
| Statistical Modeling Expert | Full report + challenged | Silent truncation detector promoted to V2 |
| DX Specialist | Full report + challenged | Deferred to V2 (trust/observability layer) |
| Security & Reliability Analyst | Full report + challenged | Lock fix + backup rotation in winning approach |
