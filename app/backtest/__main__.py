"""Run a real-data backtest and A/B compare setups (max 5 symbols).

    python -m app.backtest --symbols MRVL,NVDA,TSLA --start 2022-01-01
    python -m app.backtest --symbols MRVL,NVDA,TSLA --setups A,B,C,D
    python -m app.backtest --symbols MRVL,NVDA --synthetic       # offline demo

Data comes from local CSV cache -> yfinance -> stooq (install yfinance locally
for real data). Results persist to data/history.db.
"""
from __future__ import annotations

import argparse

from ..sim.store import SimStore
from .data import MAX_SYMBOLS, load_prices, synthetic
from .engine import compare
from .setups import SETUPS, build_setup


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="MRVL,NVDA,TSLA", help=f"up to {MAX_SYMBOLS}, comma-separated")
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--setups", default="A,B,C,D", help="which setups to compare")
    ap.add_argument("--cash", type=float, default=100_000.0)
    ap.add_argument("--synthetic", action="store_true", help="use synthetic prices (offline)")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    data = synthetic(symbols) if args.synthetic else load_prices(symbols, args.start, args.end)
    setups = [build_setup(k.strip().upper()) for k in args.setups.split(",") if k.strip()]

    print(f"Backtest: {symbols}  |  {len(data)} bars  |  source={data.source}")
    print("=" * 78)
    results = compare(setups, data, starting_cash=args.cash, store=SimStore())
    hdr = f"{'setup':<32}{'return':>9}{'CAGR':>8}{'maxDD':>8}{'Sharpe':>8}{'trades':>8}"
    print(hdr)
    print("-" * 78)
    for r in results:
        print(f"{r['setup']:<32}{r['total_return_pct']:>8.1f}%{r['cagr_pct']:>7.1f}%"
              f"{r['max_drawdown_pct']:>7.1f}%{r['sharpe']:>8.2f}{r['trades']:>8}")
    print("=" * 78)
    best = results[0]
    print(f"Best by CAGR: {best['setup']}  ({best['cagr_pct']}%/yr, maxDD {best['max_drawdown_pct']}%, "
          f"Sharpe {best['sharpe']})")
    print("Note: synthetic data shows mechanics only. On REAL data this ranking is decision-grade.")


if __name__ == "__main__":
    main()
