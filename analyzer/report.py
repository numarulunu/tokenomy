"""Human-readable console report."""
from __future__ import annotations

from typing import Any, Dict


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}k"
    return str(n)


def _fmt_usd(x: float) -> str:
    return f"${x:,.2f}"


def render(insights: Dict[str, Any]) -> str:
    lines: list[str] = []
    add = lines.append

    p = insights.get("period", {})
    t = insights.get("totals", {})

    add("=" * 60)
    add(" tokenomy analyzer — session history insights")
    add("=" * 60)
    add("")
    add(f" Period:       {p.get('start','?')} -> {p.get('end','?')}")
    add(f" Sessions:     {p.get('sessions', 0)}")
    add(
        f" Tokens:       in: {_fmt_tokens(t.get('input_tokens',0))} | "
        f"out: {_fmt_tokens(t.get('output_tokens',0))} | "
        f"cache write: {_fmt_tokens(t.get('cache_creation_tokens',0))} | "
        f"cache read: {_fmt_tokens(t.get('cache_read_tokens',0))}"
    )
    add(f" Total spend:  {_fmt_usd(t.get('cost_usd', 0.0))}  (cache reads excluded)")
    days = max(1, p.get("days", 30))
    add(f" Daily avg:    {_fmt_usd(t.get('cost_usd', 0.0)/days)}")
    add("")

    add(" TOP TOOL SINKS (by total bytes returned)")
    add(" " + "-" * 55)
    by_tool = insights.get("by_tool", {})
    ranked = sorted(by_tool.items(), key=lambda kv: kv[1].get("total_bytes", 0), reverse=True)
    for i, (name, stats) in enumerate(ranked[:5], 1):
        add(
            f"  {i}. {name[:36]:<36} "
            f"{_fmt_tokens(stats.get('total_bytes',0)):>7}  "
            f"{_fmt_usd(stats.get('est_cost_usd',0.0)):>8}"
        )
    add("")

    add(" COUNTERFACTUAL — what you would have saved")
    add(" " + "-" * 55)
    for cf in insights.get("counterfactuals", []):
        setting = f"{cf['setting']}={cf['value']}" if cf.get("value") not in (None, True) else cf["setting"]
        add(
            f"  {setting[:32]:<32} "
            f"{_fmt_tokens(cf.get('tokens_saved',0)):>7}  "
            f"{_fmt_usd(cf.get('dollars_saved',0.0)):>8}  "
            f"losses: {cf.get('losses',0)}"
        )
    add("")

    recs = insights.get("recommendations", [])
    if recs:
        add(" RECOMMENDATIONS")
        add(" " + "-" * 55)
        for r in recs:
            mark = "[+]" if r.get("confidence") in ("high", "medium") else "[-]"
            add(f"  {mark} {r.get('reason','')}")
        add("")

    outliers = insights.get("outliers", [])
    if outliers:
        add(" TOP OUTLIER TOOL CALLS (largest single responses)")
        add(" " + "-" * 55)
        for i, o in enumerate(outliers[:5], 1):
            add(
                f"  {i}. {o.get('tool','?')[:34]:<34} "
                f"{_fmt_tokens(o.get('size',0)):>7}  "
                f"{o.get('project','?')[:14]:<14}  {(o.get('ts') or '')[:10]}"
            )
        add("")

    add(f" Full insights written to: {insights.get('output_path','')}")
    add("")
    add(" NOTE: costs are estimates from a hardcoded pricing table.")
    add(" For exact billing see console.anthropic.com.")
    return "\n".join(lines)
