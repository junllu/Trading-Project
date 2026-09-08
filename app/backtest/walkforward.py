"""Walk-forward validation — the honest version of "tune it until it improves".

Every backtest in this project so far scored a strategy on the same window used
to choose it. That is how Setup E came to look like a 504% winner and then lost
to buy-and-hold in 10 of 11 unseen symbol groups. Tuning on a fixed window and
reporting the best result measures nothing except how many parameters you tried.

This does it properly:

    |--------- train ---------|--- test ---|
                              |--------- train ---------|--- test ---|
                                                        |------ ...

For each fold: pick the best setup on TRAIN only, then score that one choice on
TEST, which the selection never saw. Buy-and-hold is scored on the same TEST
window as the benchmark that actually matters.

The number to look at is the IN-SAMPLE minus OUT-OF-SAMPLE gap. A strategy with
real edge holds up out of sample. A curve-fit one posts a great train CAGR and
collapses on test — and that collapse is the finding, not a failure of the run.

    python -m app.backtest.walkforward --symbols MRVL,NVDA,TSLA
    python -m app.backtest.walkforward --symbols AAPL,MSFT,AMZN --train 378 --test 126
"""
from __future__ import annotations

import argparse
import statistics as st

from .data import PriceData, load_prices
from .engine import Backtest
from .setups import SETUPS, build_setup


def slice_data(data: PriceData, start: int, end: int) -> PriceData:
    """A contiguous window of the price history, as its own PriceData."""
    return PriceData(dates=data.dates[start:end],
                     closes={s: v[start:end] for s, v in data.closes.items()},
                     source=f"{data.source}[{start}:{end}]")


def _cagr_of(setup, window: PriceData, cash: float, warmup: int) -> tuple[float, float]:
    r = Backtest(setup, window, starting_cash=cash, warmup=warmup).run()
    return r.cagr * 100, r.max_drawdown * 100


def _run_of(setup, window: PriceData, cash: float, warmup: int):
    """Full result, including the equity curve a true Sharpe needs."""
    return Backtest(setup, window, starting_cash=cash, warmup=warmup).run()


def _returns_from(curve: list[float]) -> list[float]:
    return [curve[i] / curve[i - 1] - 1.0
            for i in range(1, len(curve)) if curve[i - 1]]


def _record(strategy: str, params: dict, cagr: float, max_drawdown: float,
            returns: list[float], window: str, note: str) -> None:
    """Log one examined configuration. Never let bookkeeping break a run.

    The Sharpe here is computed from the engine's actual equity curve, not from
    a CAGR/drawdown ratio. That distinction matters: a return/risk proxy of ~2.0
    is routine and would sail through deflation, producing a confident PASS on a
    number that was never a Sharpe. Deflation is only meaningful against the
    statistic it was derived for.
    """
    try:
        from .trials import Trial, record, sharpe_of
        record(Trial(strategy=strategy, params=params,
                     sharpe=round(sharpe_of(returns), 4), cagr=round(cagr, 3),
                     max_drawdown=round(max_drawdown, 3), n_periods=len(returns),
                     window=window, note=note))
    except Exception:
        pass


def walk_forward(setup_keys: list[str], data: PriceData, train_bars: int = 378,
                 test_bars: int = 126, warmup: int = 35, cash: float = 100_000.0,
                 record_trials: bool = True, purge_bars: int = 5,
                 embargo_bars: int = 10) -> dict:
    setups = [build_setup(k) for k in setup_keys]
    hold = build_setup("C")                      # buy & hold benchmark
    folds = []
    t = warmup + train_bars

    while t + test_bars <= len(data):
        # PURGE + EMBARGO (Lopez de Prado). Plain adjacency is not enough:
        #
        #   train ...............|purge|embargo|--- test ---
        #
        # purge   a decision made h bars before the boundary has an OUTCOME that
        #         lands inside the test window. Training on it is training on
        #         the answer, so those bars are dropped.
        # embargo serial correlation does not stop at the purge line — the days
        #         either side of a boundary share regime, volatility and often
        #         the same news. The gap breaks that carry-over.
        #
        # Both SHRINK the training set and LOWER reported out-of-sample
        # numbers. That is the point: the previous figures were flattered.
        train_end = max(warmup + 1, t - purge_bars - embargo_bars)
        train = slice_data(data, 0, train_end)
        # test window carries `warmup` bars of history so indicators are warm,
        # but scoring starts after them — no overlap with the train decision.
        test = slice_data(data, t - warmup, t + test_bars)

        scored = []
        for s in setups:
            try:
                res = _run_of(s, train, cash, warmup)
                c, dd, rets = res.cagr * 100, res.max_drawdown * 100, _returns_from(res.equity_curve)
            except Exception:
                c, dd, rets = float("-inf"), 0.0, []
            scored.append((c, s))
            # Record EVERY setup examined on this fold, not just the winner.
            # The selection below is exactly the multiple-comparison problem
            # app/backtest/trials.py deflates for, and it can only deflate
            # against a trial count that includes the rejects.
            if record_trials and c > float("-inf"):
                _record(strategy=s.name.split(":")[0], params={"setup": s.name},
                        cagr=c, max_drawdown=dd, returns=rets,
                        window=f"{data.dates[0]}..{data.dates[t - 1]}",
                        note="walk-forward train-window selection candidate")
        scored.sort(key=lambda x: -x[0])
        best_train_cagr, chosen = scored[0]

        oos_cagr, oos_dd = _cagr_of(chosen, test, cash, warmup)
        hold_cagr, hold_dd = _cagr_of(hold, test, cash, warmup)

        folds.append({
            "train_end": data.dates[train_end - 1],
            "test_end": data.dates[min(t + test_bars - 1, len(data) - 1)],
            "gap_bars": t - train_end,
            "chosen": chosen.name.split(":")[0], "train_cagr": best_train_cagr,
            "oos_cagr": oos_cagr, "oos_dd": oos_dd,
            "hold_cagr": hold_cagr, "hold_dd": hold_dd,
            "gap": best_train_cagr - oos_cagr, "vs_hold": oos_cagr - hold_cagr,
        })
        t += test_bars

    if not folds:
        return {"folds": [], "note": "not enough history for even one fold"}

    return {
        "folds": folds,
        "median_train_cagr": st.median(f["train_cagr"] for f in folds),
        "median_oos_cagr": st.median(f["oos_cagr"] for f in folds),
        "median_hold_cagr": st.median(f["hold_cagr"] for f in folds),
        "median_gap": st.median(f["gap"] for f in folds),
        "beat_hold_folds": sum(1 for f in folds if f["vs_hold"] > 0),
        "n_folds": len(folds),
    }


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="MRVL,NVDA,TSLA")
    ap.add_argument("--setups", default=",".join(k for k in SETUPS if k != "C"))
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--train", type=int, default=378, help="train bars (~18 months)")
    ap.add_argument("--test", type=int, default=126, help="test bars (~6 months)")
    ap.add_argument("--cash", type=float, default=100_000.0)
    ap.add_argument("--purge", type=int, default=5,
                    help="bars dropped from the end of train whose outcome lands in test")
    ap.add_argument("--embargo", type=int, default=10,
                    help="extra gap after the purge, to break serial correlation")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    keys = [k.strip().upper() for k in args.setups.split(",") if k.strip()]
    data = load_prices(symbols, args.start, None)
    res = walk_forward(keys, data, args.train, args.test, cash=args.cash,
                       purge_bars=args.purge, embargo_bars=args.embargo)

    if not res["folds"]:
        print(res.get("note"))
        return

    print(f"Walk-forward: {symbols}  |  {len(data)} bars  |  "
          f"train={args.train} test={args.test} bars  |  candidates={keys}")
    print("=" * 96)
    print(f"{'train ends':<12}{'test ends':<12}{'picked':<8}{'train CAGR':>12}"
          f"{'OOS CAGR':>11}{'hold CAGR':>11}{'gap':>9}{'vs hold':>9}")
    print("-" * 96)
    for f in res["folds"]:
        print(f"{f['train_end']:<12}{f['test_end']:<12}{f['chosen']:<8}"
              f"{f['train_cagr']:>11.1f}%{f['oos_cagr']:>10.1f}%{f['hold_cagr']:>10.1f}%"
              f"{f['gap']:>8.1f}{f['vs_hold']:>+9.1f}")
    print("=" * 96)
    print(f"median train CAGR (in-sample, what tuning promises): {res['median_train_cagr']:>7.1f}%")
    print(f"median OOS   CAGR (out-of-sample, what you'd get)  : {res['median_oos_cagr']:>7.1f}%")
    print(f"median buy&hold CAGR on the same test windows      : {res['median_hold_cagr']:>7.1f}%")
    print(f"median overfit gap (train - OOS)                   : {res['median_gap']:>7.1f} pp")
    print(f"folds where the tuned pick beat buy&hold           : "
          f"{res['beat_hold_folds']}/{res['n_folds']}")


if __name__ == "__main__":
    _main()
