"""Analyze exported Robinhood trade history.

    python -m app.portfolio.trade_history      (module entry below)
    python -m app.portfolio                     (this file)

Reads data/trade_history.json (exported by the /trade-history command via the
robinhood-trading MCP) and prints the analysis.
"""
from __future__ import annotations

import json

from .trade_history import analyze, load_trade_history


def main() -> None:
    orders = load_trade_history()
    if not orders:
        print("No data/trade_history.json found. Run /trade-history in your local Claude "
              "(with the robinhood-trading MCP) to export it first.")
        return
    a = analyze(orders)
    print("=" * 60)
    print("TRADE HISTORY ANALYSIS")
    print(f"  Matched round-trips : {a['trades_matched']}")
    print(f"  Realized P&L        : ${a['realized_pnl']:,.2f}")
    print(f"  Win rate            : {a['win_rate_pct']}%")
    print(f"  Avg win / avg loss  : ${a['avg_win']:,.2f} / ${a['avg_loss']:,.2f}")
    print(f"  Avg holding days    : {a['avg_holding_days']}")
    print("-" * 60)
    print("  Per-symbol realized P&L (worst first):")
    for sym, v in a["per_symbol"].items():
        print(f"    {sym:6} ${v['pnl']:>10,.2f}  ({v['trips']} trips, {v['win_rate_pct']}% win)")
    if a["late_exits"]:
        print("-" * 60)
        print("  ⚠ LATE EXITS (sold after the policy regime turned negative):")
        for le in a["late_exits"]:
            print(f"    {le['symbol']:6} {le['buy_date']} -> {le['sell_date']}  "
                  f"{le['return_pct']:+.1f}%  (regime tilt {le['exit_regime_tilt']})")
    print("=" * 60)


if __name__ == "__main__":
    main()
