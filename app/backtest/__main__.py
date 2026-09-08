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
    ap.add_argument("--local-llm", action="store_true",
                     help="enable the local Ollama analyst for setups that weight 'analyst' (e.g. F)")
    ap.add_argument("--release", metavar="NAME", default=None,
                     help="save the best-by-CAGR setup from this run as a versioned release "
                          "(data/releases/NAME_vN.json), always at stage=backtest_only")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    data = synthetic(symbols) if args.synthetic else load_prices(symbols, args.start, args.end)
    setups = [build_setup(k.strip().upper()) for k in args.setups.split(",") if k.strip()]

    analyst = None
    if args.local_llm:
        from ..intel.local_llm_analyst import LocalLLMAnalyst
        analyst = LocalLLMAnalyst()
        status = "reachable" if analyst.live else "UNREACHABLE (falling back to heuristic ratings)"
        print(f"Local LLM analyst ({analyst.model} @ {analyst.base_url}): {status}")

    print(f"Backtest: {symbols}  |  {len(data)} bars  |  source={data.source}")
    print("=" * 98)
    results = compare(setups, data, starting_cash=args.cash, store=SimStore(), analyst=analyst)
    hdr = (f"{'setup':<32}{'return':>9}{'CAGR':>8}{'maxDD':>8}{'Sharpe':>8}{'trades':>8}"
           f"{'harvested':>12}{'events':>8}")
    print(hdr)
    print("-" * 98)
    for r in results:
        print(f"{r['setup']:<32}{r['total_return_pct']:>8.1f}%{r['cagr_pct']:>7.1f}%"
              f"{r['max_drawdown_pct']:>7.1f}%{r['sharpe']:>8.2f}{r['trades']:>8}"
              f"{r['harvested_losses']:>11.0f}$" f"{r['harvest_events']:>8}")
    print("=" * 98)
    best = results[0]
    print(f"Best by CAGR: {best['setup']}  ({best['cagr_pct']}%/yr, maxDD {best['max_drawdown_pct']}%, "
          f"Sharpe {best['sharpe']})")
    print("Note: synthetic data shows mechanics only. On REAL data this ranking is decision-grade.")

    if args.release:
        from .release import AnalystConfig, RELEASES_DIR, StrategyRelease
        winner = next(s for s in setups if s.name == best["setup"])
        n_existing = len(list(RELEASES_DIR.glob(f"{args.release}_v*.json"))) if RELEASES_DIR.exists() else 0
        analyst_cfg = (AnalystConfig(kind="local_llm", model=analyst.model, base_url=analyst.base_url)
                       if analyst is not None else AnalystConfig(kind="none"))
        release = StrategyRelease(
            name=args.release, version=f"v{n_existing + 1}", setup=winner, analyst=analyst_cfg,
            validation_metrics=best, validation_symbols=symbols,
            validation_window=f"{data.dates[0]}..{data.dates[-1]}",
        )
        path = release.save()
        print(f"Released '{winner.name}' as {path.name} (stage=backtest_only). "
              f"Promote explicitly with: python -m app.backtest.release promote {release.name} {release.version} <stage>")


if __name__ == "__main__":
    main()
