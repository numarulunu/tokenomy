"""tokenomy v0.3.1 auto-tuner main entrypoint.

Pure functions:
- compute_caps_per_setting(corpus_stats) -> proposed caps
- apply_hysteresis_cooldown_freeze(state, proposed) -> final caps + new state

I/O wrappers:
- main(): orchestrates load → compute → apply → write
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Tuple

from analyzer.extractors import Event, iter_corpus
from tuner.losses import detect_all, detect_user_pinned
from tuner.settings_writer import FLOORS, merge_into_user_settings
from tuner.state import empty_state, load_state, save_state
from tuner.weighting import age_days, compute_cap, confidence, session_weight

log = logging.getLogger("tuner")

TIGHTEN_THRESHOLD = 0.10
LOOSEN_THRESHOLD = 0.05
COOLDOWN_SESSIONS = 5
LOSS_FREEZE_DAYS = 14

DEFAULT_HOME = os.path.expanduser("~/.claude/tokenomy")
DEFAULT_CORPUS_ROOT = os.path.expanduser("~/.claude/projects")
DEFAULT_USER_SETTINGS = os.path.expanduser("~/.claude/settings.json")


# ─────────────────── corpus → samples ───────────────────


DEFAULT_MCP_ALLOW = {"context7", "kontext", "sequential-thinking", "serena", "plugin_context7_context7"}

MIN_EFFECTIVE_N = 200


def _server_matches(server: str, allow: set[str]) -> bool:
    if not allow:
        return True
    if server in allow:
        return True
    # fuzzy: "plugin_context7_context7" counts as "context7"
    low = server.lower()
    return any(a in low for a in allow)


def collect_samples(
    corpus_root: str,
    now: datetime | None = None,
    mcp_allow: set[str] | None = None,
    capped_tools: Iterable[str] = (),
) -> Dict[str, Any]:
    """Walk corpus, return weighted samples per setting + per-MCP-server."""
    if now is None:
        now = datetime.now(timezone.utc)
    out_tokens: List[Tuple[float, float]] = []
    mcp_sizes: Dict[str, List[Tuple[float, float]]] = {}
    ctx_pcts: List[Tuple[float, float]] = []
    all_losses: List[Dict[str, Any]] = []

    name_by_id: Dict[str, str] = {}
    session_max_ctx: Dict[str, int] = {}

    for path, events in iter_corpus(corpus_root):
        try:
            ev_list = list(events)
        except Exception as e:
            log.debug("skipping %s: %s", path, e)
            continue
        if not ev_list:
            continue
        # weight from most recent ts in this session
        last_ts = next((e.ts for e in reversed(ev_list) if e.ts), None)
        w = session_weight(age_days(last_ts, now)) if last_ts else 1.0

        local_name_by_id: Dict[str, str] = {}
        max_ctx = 0
        for e in ev_list:
            if e.kind == "tool_use" and e.tool_use_id:
                local_name_by_id[e.tool_use_id] = e.tool_name or ""
            elif e.kind == "assistant_usage":
                if e.output_tokens > 0:
                    out_tokens.append((float(e.output_tokens), w))
                ctx = e.input_tokens + e.cache_creation_tokens + e.cache_read_tokens
                if ctx > max_ctx:
                    max_ctx = ctx
            elif e.kind == "tool_result":
                tname = local_name_by_id.get(e.tool_use_id or "", "")
                if tname.startswith("mcp__"):
                    parts = tname.split("__")
                    server = parts[1] if len(parts) >= 3 else "unknown"
                    if mcp_allow is None or _server_matches(server, mcp_allow):
                        mcp_sizes.setdefault(server, []).append((float(e.response_size_bytes), w))
        if max_ctx > 0:
            # treat as % of 200k baseline; use a shorter half-life (7d) for
            # context habits since the user is actively changing compact behavior
            from tuner.weighting import session_weight as _sw
            age_d = age_days(last_ts, now) if last_ts else 0.0
            ctx_w = 0.5 ** (age_d / 7.0) if age_d > 0 else 1.0
            ctx_w = max(ctx_w, 0.01)
            pct = min(100.0, (max_ctx / 200_000.0) * 100.0)
            ctx_pcts.append((pct, ctx_w))
        # Run loss detectors per-session to avoid cross-session false positives
        session_losses = detect_all(ev_list, capped_tools=capped_tools)
        all_losses.extend(session_losses)

    eff_n = sum(w for _, w in out_tokens) if out_tokens else 0.0
    return {
        "out_tokens": out_tokens,
        "mcp_sizes": mcp_sizes,
        "ctx_pcts": ctx_pcts,
        "losses": all_losses,
        "effective_n": eff_n,
    }


# ─────────────────── pure: compute caps ───────────────────


def compute_caps_per_setting(stats: Dict[str, Any]) -> Dict[str, Any]:
    caps: Dict[str, Any] = {}

    # CLAUDE_CODE_MAX_OUTPUT_TOKENS
    if stats["out_tokens"]:
        caps["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = compute_cap(
            stats["out_tokens"], floor=FLOORS["CLAUDE_CODE_MAX_OUTPUT_TOKENS"]
        )
    else:
        caps["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = FLOORS["CLAUDE_CODE_MAX_OUTPUT_TOKENS"]

    # CLAUDE_AUTOCOMPACT_PCT_OVERRIDE — p75 captures *typical* compact point,
    # not worst-case. Plus 10% headroom. Context samples use a shorter half-life
    # (7d) via pre-reweighting upstream; here we just take p75.
    if stats["ctx_pcts"]:
        from tuner.weighting import weighted_percentile
        p = weighted_percentile(stats["ctx_pcts"], 0.75)
        caps["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] = max(int(p + 10), FLOORS["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"])
    else:
        caps["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] = 70

    # MAX_MCP_OUTPUT_TOKENS per server
    per_server: Dict[str, int] = {}
    for server, samples in stats["mcp_sizes"].items():
        per_server[server] = compute_cap(samples, floor=FLOORS["MAX_MCP_OUTPUT_TOKENS"])
    if per_server:
        caps["MAX_MCP_OUTPUT_TOKENS"] = per_server

    return caps


# ─────────────────── pure: hysteresis ───────────────────


def _delta(old: int, new: int) -> float:
    if old == 0:
        return 1.0
    return (old - new) / old  # positive = tightening


def _apply_one(
    name: str,
    old: int,
    new: int,
    cooldowns: Dict[str, Any],
    freezes: Dict[str, Any],
    now: datetime,
) -> Tuple[int, str]:
    """Return (chosen, reason)."""
    # No prior value -> always initialize (freezes/cooldowns can't preserve nothing)
    if old == 0:
        return new, "init"
    # freeze check
    fr = freezes.get(name)
    if fr:
        until = fr.get("until")
        try:
            until_dt = datetime.fromisoformat(until.replace("Z", "+00:00")) if until else None
        except (AttributeError, ValueError):
            until_dt = None
        if until_dt and until_dt > now:
            return old, "frozen"
    # cooldown check
    cd = cooldowns.get(name)
    if cd and cd.get("sessions_remaining", 0) > 0:
        return old, "cooldown"
    d = _delta(old, new)
    if d >= TIGHTEN_THRESHOLD:
        return new, "tighten"
    if d <= -LOOSEN_THRESHOLD:
        return new, "loosen"
    return old, "hysteresis"


def apply_hysteresis_cooldown_freeze(
    state: Dict[str, Any],
    proposed: Dict[str, Any],
    now: datetime | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if now is None:
        now = datetime.now(timezone.utc)
    old_caps = state.get("caps", {}) or {}
    cooldowns = dict(state.get("cooldowns", {}) or {})
    freezes = state.get("freezes", {}) or {}
    user_pinned = set(state.get("user_pinned", []) or [])

    final: Dict[str, Any] = {}
    for k, v in proposed.items():
        if k in user_pinned:
            continue
        if isinstance(v, dict):
            # per-server
            sub_old = old_caps.get(k, {}) if isinstance(old_caps.get(k), dict) else {}
            sub_final: Dict[str, int] = {}
            for server, new_val in v.items():
                key = f"{k}.{server}"
                if key in user_pinned:
                    continue
                old_val = int(sub_old.get(server, 0))
                chosen, reason = _apply_one(key, old_val, int(new_val), cooldowns, freezes, now)
                sub_final[server] = chosen
                if reason in ("tighten", "loosen", "init") and chosen != old_val:
                    cooldowns[key] = {"sessions_remaining": COOLDOWN_SESSIONS}
            if sub_final:
                final[k] = sub_final
        else:
            old_val = int(old_caps.get(k, 0)) if isinstance(old_caps.get(k), (int, float)) else 0
            chosen, reason = _apply_one(k, old_val, int(v), cooldowns, freezes, now)
            final[k] = chosen
            if reason in ("tighten", "loosen", "init") and chosen != old_val:
                cooldowns[k] = {"sessions_remaining": COOLDOWN_SESSIONS}

    new_state = dict(state)
    new_state["caps"] = final
    new_state["cooldowns"] = cooldowns
    return final, new_state


def apply_loss_freezes(
    state: Dict[str, Any],
    losses: List[Dict[str, Any]],
    now: datetime | None = None,
) -> Dict[str, Any]:
    if now is None:
        now = datetime.now(timezone.utc)
    until = (now + timedelta(days=LOSS_FREEZE_DAYS)).isoformat()
    freezes = dict(state.get("freezes", {}) or {})
    for loss in losses:
        det = loss.get("detector", "")
        if det == "truncation_requery" or det == "error_after_cap":
            server = loss.get("server")
            if server:
                freezes[f"MAX_MCP_OUTPUT_TOKENS.{server}"] = {"until": until, "reason": det}
        elif det == "mid_code_ending":
            freezes["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = {"until": until, "reason": det}
        elif det == "compact_after_big_result":
            freezes["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] = {"until": until, "reason": det}
    # Return a new dict instead of mutating the caller's state — matches
    # apply_hysteresis_cooldown_freeze and prevents hidden side-effects in
    # tests that pass the same state through multiple reducers.
    new_state = dict(state)
    new_state["freezes"] = freezes
    return new_state


def tick_cooldowns(state: Dict[str, Any]) -> Dict[str, Any]:
    """Decrement every cooldown's sessions_remaining by 1; drop entries at 0.

    Without this, a setting that hits the tighten/loosen threshold once is
    frozen in cooldown forever — sessions_remaining was being set but never
    decremented, turning a '5-session cooldown' into an effective permanent
    freeze. Must run before apply_hysteresis_cooldown_freeze each session.
    """
    cd = dict(state.get("cooldowns", {}) or {})
    next_cd: Dict[str, Any] = {}
    for key, entry in cd.items():
        if not isinstance(entry, dict):
            continue
        remaining = int(entry.get("sessions_remaining", 0)) - 1
        if remaining > 0:
            next_cd[key] = {**entry, "sessions_remaining": remaining}
        # else: entry expires, drop it
    new_state = dict(state)
    new_state["cooldowns"] = next_cd
    return new_state


# ─────────────────── main ───────────────────


def _print_diff(old_caps: Dict[str, Any], new_caps: Dict[str, Any]) -> None:
    print("\n-- proposed cap diff --")
    keys = sorted(set(old_caps) | set(new_caps))
    for k in keys:
        o = old_caps.get(k)
        n = new_caps.get(k)
        if isinstance(n, dict) or isinstance(o, dict):
            o = o or {}
            n = n or {}
            servers = sorted(set(o) | set(n))
            for s in servers:
                ov = o.get(s, "—")
                nv = n.get(s, "—")
                marker = " " if ov == nv else "*"
                print(f"  {marker} {k}.{s}: {ov} → {nv}")
        else:
            marker = " " if o == n else "*"
            print(f"  {marker} {k}: {o} -> {n}")
    print()


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tuner")
    ap.add_argument("--first-run", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--corpus-root", default=DEFAULT_CORPUS_ROOT)
    ap.add_argument("--home", default=DEFAULT_HOME)
    ap.add_argument(
        "--user-settings",
        default=DEFAULT_USER_SETTINGS,
        help="Path to ~/.claude/settings.json to merge tokenomy env caps into.",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    home = args.home
    os.makedirs(home, exist_ok=True)
    applied_path = os.path.join(home, "applied.json")
    lock_dir = os.path.join(home, "tuner.lock.d")

    if args.reset:
        # Also strip tokenomy's managed env keys from the user's settings.json
        # so reset actually returns the user to a pre-tuner state.
        try:
            _strip_managed_env(args.user_settings)
        except Exception as e:  # fail-open: reset must always succeed
            log.warning("could not strip managed env from %s: %s", args.user_settings, e)
        for fn in ("applied.json", "losses.jsonl"):
            p = os.path.join(home, fn)
            if os.path.exists(p):
                os.unlink(p)
        print("[tuner] reset complete")
        return 0

    state = load_state(applied_path)

    if args.status:
        print(f"version: {state.get('version')}")
        print(f"last_tune_at: {state.get('last_tune_at')}")
        print(f"effective_n: {state.get('effective_n', 0):.1f}")
        print(f"confidence: {state.get('confidence', 0):.2f}")
        print(f"caps: {state.get('caps')}")
        print(f"freezes: {state.get('freezes')}")
        print(f"user_pinned: {state.get('user_pinned')}")
        return 0

    try:
        log.info("scanning corpus at %s", args.corpus_root)
        if not os.path.exists(args.corpus_root):
            log.warning("corpus root missing — writing baseline-only settings")
            stats = {"out_tokens": [], "mcp_sizes": {}, "ctx_pcts": [], "losses": [], "effective_n": 0.0}
        else:
            _mcp_caps = state.get("caps", {}).get("MAX_MCP_OUTPUT_TOKENS", {})
            _capped_servers = set(_mcp_caps.keys()) if isinstance(_mcp_caps, dict) else set()
            stats = collect_samples(args.corpus_root, mcp_allow=DEFAULT_MCP_ALLOW, capped_tools=_capped_servers)

        proposed = compute_caps_per_setting(stats)
        losses = stats["losses"]
        state = apply_loss_freezes(state, losses)
        # Tick cooldowns BEFORE the hysteresis pass reads them, so yesterday's
        # cooldown expires on schedule instead of persisting forever.
        state = tick_cooldowns(state)

        state["effective_n"] = stats["effective_n"]
        state["confidence"] = confidence(stats["effective_n"])

        if stats["effective_n"] < MIN_EFFECTIVE_N:
            log.info(
                "confidence too low (effective_n=%.1f < %d) — writing baseline only",
                stats["effective_n"], MIN_EFFECTIVE_N,
            )
            final = {}
        else:
            final, state = apply_hysteresis_cooldown_freeze(state, proposed)
        state["last_tune_at"] = datetime.now(timezone.utc).isoformat()

        if args.dry_run:
            _print_diff(load_state(applied_path).get("caps", {}), final)
            print(f"effective_n: {stats['effective_n']:.1f}  confidence: {state['confidence']:.2f}")
            print(f"losses detected: {len(losses)}")
            return 0

        user_pinned = state.get("user_pinned") or []
        merged = merge_into_user_settings(
            args.user_settings,
            final,
            user_pinned=user_pinned,
        )
        save_state(applied_path, state)
        log.info("merged %d env keys into %s; wrote %s", len(merged), args.user_settings, applied_path)
        return 0
    finally:
        # Always release the SessionStart hook's lock dir, even on crash.
        # Remove pid file first (rmdir fails on non-empty dirs).
        try:
            if os.path.isdir(lock_dir):
                pid_file = os.path.join(lock_dir, "pid")
                if os.path.exists(pid_file):
                    os.unlink(pid_file)
                os.rmdir(lock_dir)
        except OSError:
            pass


def _strip_managed_env(user_settings_path: str) -> None:
    """Remove tokenomy-managed env keys from the user's settings.json.

    Reads the `__tokenomy__.managed_env_keys` sentinel and deletes each listed
    key from the `env` block (unless it's been user-pinned externally — we
    can't know that here, so we prune everything we claimed). Also removes the
    sentinel itself. Leaves a `.tokenomy.bak` file untouched for manual restore.
    """
    import json as _json
    if not os.path.exists(user_settings_path):
        return
    with open(user_settings_path, "r", encoding="utf-8") as f:
        data = _json.load(f)
    if not isinstance(data, dict):
        return
    meta = data.get("__tokenomy__") or {}
    managed = meta.get("managed_env_keys") or []
    env_block = data.get("env") if isinstance(data.get("env"), dict) else {}
    for k in managed:
        env_block.pop(k, None)
    data["env"] = env_block
    data.pop("__tokenomy__", None)
    from tuner.settings_writer import _atomic_write_json
    _atomic_write_json(user_settings_path, data)


if __name__ == "__main__":
    sys.exit(main())
