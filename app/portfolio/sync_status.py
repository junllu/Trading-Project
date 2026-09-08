"""Consolidated book across every broker — the one view the campaign runs on.

The portal's guardrails (position caps, the trailing drawdown halt, required
CAGR to target) are all computed from total equity. When one account is missing,
every one of those numbers is wrong — quietly, and in the dangerous direction.
This prints the merged book so that mistake is visible instead of silent.

It also rolls exposure up by sector, because concentration is the risk that
hides across accounts: an AI-infrastructure bet spread over two brokers looks
diversified in each and isn't.

    python -m app.portfolio.sync_status
    python -m app.portfolio.sync_status --target 1000000
"""
from __future__ import annotations

import argparse
from collections import defaultdict

from ..macro.timeline import sector_of
from .holdings import load_cash, load_holdings

# Brokers we expect to see. A broker with no rows is reported as MISSING rather
# than silently contributing zero.
EXPECTED_BROKERS = ("robinhood", "webull")


def consolidate(holdings: list[dict] | None = None) -> dict:
    rows = holdings if holdings is not None else load_holdings()
    by_broker: dict[str, float] = defaultdict(float)
    by_sector: dict[str, float] = defaultdict(float)
    by_symbol: dict[str, dict] = defaultdict(lambda: {"shares": 0.0, "value": 0.0,
                                                      "cost": 0.0, "brokers": set()})
    total = cost_total = 0.0

    for h in rows:
        sym = str(h["symbol"]).upper()
        shares = float(h["shares"])
        avg = float(h.get("avg_price", 0.0) or 0.0)
        last = float(h.get("last", avg) or avg)
        broker = str(h.get("broker", "unknown"))
        value = shares * last
        cost = shares * avg

        by_broker[broker] += value
        by_sector[sector_of(sym)] += value
        e = by_symbol[sym]
        e["shares"] += shares
        e["value"] += value
        e["cost"] += cost
        e["brokers"].add(broker)
        total += value
        cost_total += cost

    return {"total_value": total, "total_cost": cost_total,
            "unrealized": total - cost_total,
            "by_broker": dict(by_broker), "by_sector": dict(by_sector),
            "by_symbol": {k: {**v, "brokers": sorted(v["brokers"])}
                          for k, v in by_symbol.items()},
            "missing_brokers": [b for b in EXPECTED_BROKERS if b not in by_broker]}


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=float, default=1_000_000.0)
    ap.add_argument("--top", type=int, default=12)
    args = ap.parse_args()

    c = consolidate()
    cash = load_cash()
    equity = c["total_value"] + (cash or 0.0)

    print("=" * 72)
    print("CONSOLIDATED BOOK")
    print("=" * 72)
    for b, v in sorted(c["by_broker"].items(), key=lambda kv: -kv[1]):
        print(f"  {b:12} ${v:>12,.0f}   {v / c['total_value']:>6.1%}")
    if cash is not None:
        print(f"  {'cash':12} ${cash:>12,.0f}")
    print(f"  {'TOTAL':12} ${equity:>12,.0f}")
    if c["missing_brokers"]:
        print(f"  !! MISSING broker(s): {c['missing_brokers']} — equity above is UNDERSTATED")

    print(f"\n  cost basis ${c['total_cost']:>12,.0f}   unrealized "
          f"${c['unrealized']:>+12,.0f}  ({c['unrealized'] / c['total_cost']:+.1%})")

    print(f"\n  progress to ${args.target:,.0f}: {equity / args.target:.1%}   "
          f"multiple still needed: {args.target / equity:.1f}x")

    print("\nSECTOR CONCENTRATION (this is what hides across accounts)")
    for s, v in sorted(c["by_sector"].items(), key=lambda kv: -kv[1]):
        bar = "#" * int(40 * v / c["total_value"])
        print(f"  {s:16} ${v:>11,.0f}  {v / c['total_value']:>6.1%}  {bar}")

    print(f"\nTOP {args.top} POSITIONS")
    top = sorted(c["by_symbol"].items(), key=lambda kv: -kv[1]["value"])[:args.top]
    for sym, e in top:
        pnl = (e["value"] / e["cost"] - 1) if e["cost"] else 0.0
        split = "+".join(e["brokers"])
        print(f"  {sym:6} ${e['value']:>10,.0f}  {e['value'] / c['total_value']:>5.1%}  "
              f"{pnl:>+7.1%}  [{split}]")


if __name__ == "__main__":
    _main()
