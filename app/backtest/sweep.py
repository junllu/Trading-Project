"""Cross-sectional backtest sweep — run independent Backtests across many
5-symbol groups drawn from a broader universe (NASDAQ100 / S&P 500 mega-caps),
sequentially, writing each group's result to a manifest for later review.

Independence, by construction:
  - Each group gets a fresh Backtest instance per setup (fresh ConvictionEngine,
    forecaster, MacroEngine — see app/backtest/engine.py). Nothing carries over
    between groups.
  - Groups run SEQUENTIALLY, not concurrently, so there's no SQLite contention
    on data/history.db and no risk of one group's timing affecting another's.
  - A group that fails to fetch (delisted ticker, no data) is skipped and
    logged — it never partially contaminates another group's run.

    python -m app.backtest.sweep --universe nasdaq100 --setups E
    python -m app.backtest.sweep --universe sp500 --setups A,E --start 2023-01-01
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from ..config import ROOT
from ..sim.store import SimStore
from .data import load_prices
from .engine import compare
from .setups import build_setup
from .universe import NASDAQ100, SP500_EX_NASDAQ, chunk

MANIFEST_DIR = ROOT / "data" / "sweeps"

UNIVERSES = {
    "nasdaq100": NASDAQ100,
    "sp500": NASDAQ100 + SP500_EX_NASDAQ,
}


def run_sweep(universe_name: str, setup_keys: list[str], start: str = "2022-01-01",
              end: str | None = None, starting_cash: float = 100_000.0,
              chunk_size: int = 5, pause_seconds: float = 1.0) -> Path:
    symbols = UNIVERSES[universe_name]
    groups = chunk(symbols, chunk_size)
    setups = [build_setup(k) for k in setup_keys]

    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = MANIFEST_DIR / f"{universe_name}_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"

    print(f"Sweep: universe={universe_name} ({len(symbols)} symbols, {len(groups)} groups of "
          f"<= {chunk_size})  setups={setup_keys}  window={start}..{end or 'now'}")
    print(f"Manifest: {manifest_path}")

    store = SimStore()   # one shared store; safe because groups run sequentially, not concurrently
    done, skipped = 0, 0
    with manifest_path.open("a", encoding="utf-8") as mf:
        for gi, group in enumerate(groups):
            try:
                data = load_prices(group, start, end)
            except Exception as exc:
                print(f"[{gi + 1}/{len(groups)}] SKIP {group} — {exc}")
                mf.write(json.dumps({"group_index": gi, "symbols": group, "skipped": True,
                                     "reason": str(exc)}) + "\n")
                mf.flush()
                skipped += 1
                continue

            results = compare(setups, data, starting_cash=starting_cash, store=store)
            record = {"group_index": gi, "symbols": group, "source": data.source,
                      "bars": len(data), "results": results}
            mf.write(json.dumps(record) + "\n")
            mf.flush()
            best = results[0]
            print(f"[{gi + 1}/{len(groups)}] {group}  best={best['setup']}  "
                  f"CAGR={best['cagr_pct']}%  Sharpe={best['sharpe']}  maxDD={best['max_drawdown_pct']}%")
            done += 1
            if pause_seconds > 0:
                time.sleep(pause_seconds)

    print(f"Sweep complete: {done} groups run, {skipped} skipped. Manifest: {manifest_path}")
    return manifest_path


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", choices=sorted(UNIVERSES), default="nasdaq100")
    ap.add_argument("--setups", default="E", help="comma-separated setup keys, e.g. A,E")
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--cash", type=float, default=100_000.0)
    ap.add_argument("--chunk-size", type=int, default=5)
    ap.add_argument("--pause", type=float, default=1.0, help="seconds between groups (rate-limit courtesy)")
    args = ap.parse_args()

    setup_keys = [k.strip().upper() for k in args.setups.split(",") if k.strip()]
    run_sweep(args.universe, setup_keys, args.start, args.end, args.cash, args.chunk_size, args.pause)


if __name__ == "__main__":
    _main()
