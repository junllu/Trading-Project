"""Generate today's trade plan from the command line.

    python -m app.agent

Runs the portal brain (quotes -> technicals -> events -> analyst -> blended
conviction -> campaign doctrine) and writes a TRADE PLAN to data/trade_plan.json:
the order intents plus the guardrails and limits the executor must honor.

This is what the local Claude (with the robinhood-trading MCP) reads to execute.
It does NOT place any orders itself — execution is the MCP's job, after your
approval. Ideal for cron / Task Scheduler ahead of the market open.
"""
from __future__ import annotations

import json

from ..portal import Portal


def main() -> None:
    portal = Portal().build()
    plan = portal.build_trade_plan()

    c = plan.get("campaign", {})
    g = plan.get("guardrails", {})
    print("=" * 60)
    print("TRADE PLAN —", plan.get("generated"), f"({plan.get('mode')} mode)")
    if c:
        print(f"Campaign: ${c.get('equity', 0):,.0f} -> $1M "
              f"({c.get('progress_pct', 0)}%), {c.get('days_remaining', '?')}d left, "
              f"needs {c.get('required_cagr_pct', 0)}%/yr, pace {c.get('pace', '?')}.")
    if g.get("drawdown_halt_active") or g.get("kill_switch"):
        print("⛔ HALT ACTIVE — place NO orders (capital preservation).")
    print(f"Phase: {g.get('phase')}  |  focus: {plan.get('limits', {}).get('focus_symbols')}")
    print("-" * 60)
    orders = plan.get("orders", [])
    if not orders:
        print("No orders met the conviction threshold today.")
    for o in orders:
        note = f"  [{', '.join(o['guardrail_notes'])}]" if o.get("guardrail_notes") else ""
        print(f"  {o['symbol']:6} {o['side']:4} ${o['order_value']:>7.0f} "
              f"(~{o['est_shares']} sh)  conv {o['conviction']:+.2f}"
              f"{'  ★focus' if o.get('focus') else ''}{note}")
    print("-" * 60)
    print(f"Written to: {plan.get('_written_to')}")
    print("Next: hand this to Claude with the robinhood-trading MCP (/trade) to")
    print("reconcile against your live account, approve, and execute.")


if __name__ == "__main__":
    main()
