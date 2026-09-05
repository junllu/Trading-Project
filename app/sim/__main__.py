"""Run a self-correcting simulation from the command line.

    python -m app.sim                 # default: focus names, ~300 sim days
    python -m app.sim --full          # trade the whole current book instead
    python -m app.sim --days 500      # longer horizon

Results (equity curve, trades, events, weight evolution) persist to
data/history.db and print as a summary. Use it to compare concentration
strategies with evidence instead of opinion.
"""
from __future__ import annotations

import argparse

from ..portfolio.holdings import load_holdings
from .simulator import SimConfig, Simulator

FOCUS = ["MRVL", "NVDA", "TSLA"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="trade the whole current book, not just focus names")
    ap.add_argument("--days", type=int, default=300)
    ap.add_argument("--cash", type=float, default=50_000.0)
    ap.add_argument("--no-self-correct", action="store_true")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    holdings = {str(h["symbol"]).upper(): (float(h["shares"]), float(h.get("avg_price", 0)))
                for h in load_holdings()}
    if args.full and holdings:
        symbols = sorted(holdings.keys())
        starting = holdings
    else:
        symbols = FOCUS
        starting = {}

    cfg = SimConfig(symbols=symbols, focus_symbols=FOCUS, days=args.days,
                    starting_cash=args.cash, starting_positions=starting,
                    self_correct=not args.no_self_correct, seed=args.seed)
    result = Simulator(cfg).run()
    d = result.to_dict()
    print("=" * 60)
    print(f"SIMULATION {d['run_id']}  ({'FULL BOOK' if args.full else 'FOCUS: ' + ','.join(FOCUS)})")
    print(f"  Horizon:        {args.days} sim days   phases={d['phases']}")
    print(f"  Equity:         ${d['start_equity']:,.0f}  ->  ${d['final_equity']:,.0f}")
    print(f"  Total return:   {d['total_return_pct']:+.1f}%")
    print(f"  Max drawdown:   {d['max_drawdown_pct']:.1f}%   halted days: {d['halted_days']}")
    print(f"  Trades:         {d['trades']}")
    print(f"  Source hit-rates (did it predict direction?): {d['source_hit_rates']}")
    print(f"  Self-corrected weights: {d['final_weights']}")
    print("=" * 60)
    print("History stored in data/history.db (equity curve, trades, events, weights).")


if __name__ == "__main__":
    main()
