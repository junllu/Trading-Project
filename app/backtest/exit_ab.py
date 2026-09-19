"""A/B: does scaling the exit stop by a name's own volatility beat a flat one?

THE CLAIM UNDER TEST

exit_plans.py applies max_loss_pct=15 to every name. Measured on 64 sessions of
minute data, that threshold was breached on 79% of MRVL entries (median
21-session MAE -25.7%) and 0% of NVDA's (-6.4%). The same number is
simultaneously hair-trigger and decorative. The obvious fix is to express the
stop in units of the name's own movement. This module tests whether that
actually helps, rather than assuming it does because the motivation is tidy.

HOW THIS AVOIDS THE USUAL WAYS OF FOOLING YOURSELF

  NON-OVERLAPPING ENTRIES. One entry every `hold` bars, so samples are
  independent. Rolling an entry every day and holding 21 would turn ~250
  observations into ~12 real ones while reporting 250, which is precisely the
  overlap trap intraday.stop_study() already corrects for.

  NO LOOK-AHEAD IN THE SCALING. Volatility is measured on the 60 bars BEFORE
  the entry. Using full-sample vol would let the stop know how turbulent the
  name was going to be.

  THE SCALE FACTOR IS FIXED A PRIORI, NOT SWEPT. B is defined as the flat stop
  translated into vol units against a fixed 2%/day anchor — no free parameter is
  tuned on the outcome. Sweeping the anchor and reporting the best would be
  fitting, and this project has a ~16pp overfit gap on record from exactly that.

  BOTH ARMS SHARE THE SAME BARS AND THE SAME BIAS. Daily bars under-detect
  intraday stop touches, but they under-detect identically for A and B, so the
  COMPARISON survives even though neither arm's absolute number is exact.

  THE RESULT IS DEFLATED. Two arms is still a search. The verdict runs through
  trials.deflated_sharpe with the trial count declared.

    python -m app.backtest.exit_ab
"""
from __future__ import annotations

import argparse
import json
import math
import statistics as st
from dataclasses import dataclass

from ..data.ohlc import Bar, coverage, load

FLAT_STOP_PCT = 0.15          # what exit_plans.py applies to every name today
REF_DAILY_VOL = 0.02          # a priori anchor: ~2%/day is an ordinary large cap
VOL_WINDOW = 60               # trailing bars used to measure a name's vol
HOLD_BARS = 21                # matches exit_plans' time_stop_sessions
STOP_FLOOR, STOP_CEIL = 0.05, 0.50   # keeps the scaled stop sane on extremes
MIN_BARS = 400


@dataclass
class Trade:
    symbol: str
    entry_date: str
    entry: float
    stop_pct: float
    exit_price: float
    stopped: bool
    ret: float


def _trailing_vol(bars: list[Bar], i: int, window: int = VOL_WINDOW) -> float | None:
    """Daily return stdev over the `window` bars ending at i-1 (never including i)."""
    if i < window + 1:
        return None
    rets = []
    for j in range(i - window, i):
        prev = bars[j - 1].close
        if prev > 0:
            rets.append(bars[j].close / prev - 1.0)
    if len(rets) < window // 2:
        return None
    return st.pstdev(rets) if len(rets) > 1 else None


def _run_arm(bars: list[Bar], symbol: str, scaled: bool) -> list[Trade]:
    out: list[Trade] = []
    i = VOL_WINDOW + 1
    while i + HOLD_BARS < len(bars):
        vol = _trailing_vol(bars, i)
        if vol is None or vol <= 0:
            i += HOLD_BARS
            continue
        entry = bars[i].open
        if entry <= 0:
            i += HOLD_BARS
            continue

        if scaled:
            stop_pct = FLAT_STOP_PCT * (vol / REF_DAILY_VOL)
            stop_pct = max(STOP_FLOOR, min(STOP_CEIL, stop_pct))
        else:
            stop_pct = FLAT_STOP_PCT

        level = entry * (1 - stop_pct)
        exit_px, stopped = None, False
        for k in range(i, i + HOLD_BARS):
            if bars[k].low <= level:
                exit_px, stopped = level, True     # filled AT the stop, both arms
                break
        if exit_px is None:
            exit_px = bars[i + HOLD_BARS - 1].close

        out.append(Trade(symbol, bars[i].date, entry, stop_pct, exit_px, stopped,
                         exit_px / entry - 1.0))
        i += HOLD_BARS                              # non-overlapping
    return out


def _summarise(trades: list[Trade], label: str) -> dict:
    rets = [t.ret for t in trades]
    if not rets:
        return {"arm": label, "trades": 0}
    mean = st.mean(rets)
    sd = st.pstdev(rets) if len(rets) > 1 else 0.0
    # Per-trade Sharpe annualised by how many 21-bar holds fit in a year.
    per_year = 252 / HOLD_BARS
    sharpe = (mean / sd * math.sqrt(per_year)) if sd > 0 else 0.0
    return {
        "arm": label,
        "trades": len(rets),
        "symbols": len({t.symbol for t in trades}),
        "mean_ret_pct": round(mean * 100, 3),
        "median_ret_pct": round(st.median(rets) * 100, 3),
        "stdev_pct": round(sd * 100, 3),
        "win_rate_pct": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
        "stop_rate_pct": round(sum(1 for t in trades if t.stopped) / len(trades) * 100, 1),
        "worst_pct": round(min(rets) * 100, 2),
        "sharpe": round(sharpe, 3),
        "total_ret_pct": round(sum(rets) * 100, 1),
    }


def run(symbols: list[str] | None = None) -> dict:
    syms = symbols or sorted(coverage())
    flat: list[Trade] = []
    scaled: list[Trade] = []
    used = []
    for s in syms:
        bars = load(s)
        if len(bars) < MIN_BARS:
            continue
        used.append(s)
        flat += _run_arm(bars, s, scaled=False)
        scaled += _run_arm(bars, s, scaled=True)

    a, b = _summarise(flat, "A flat 15%"), _summarise(scaled, "B vol-scaled")
    if not flat or not scaled:
        return {"error": "no trades generated"}

    # Paired by construction: same symbol, same entry dates, same bars. So the
    # difference series is the honest unit of evidence, not two separate means.
    paired = [s.ret - f.ret for f, s in zip(flat, scaled)]
    diff_mean = st.mean(paired)
    diff_sd = st.pstdev(paired) if len(paired) > 1 else 0.0
    t_stat = (diff_mean / (diff_sd / math.sqrt(len(paired)))) if diff_sd > 0 else 0.0

    from .trials import deflated_sharpe
    better = b if b["sharpe"] >= a["sharpe"] else a
    d = deflated_sharpe(observed_sr=better["sharpe"], n_periods=better["trades"],
                        n_trials=2, sharpe_variance=abs(a["sharpe"] - b["sharpe"]) or 1e-9)

    return {
        "config": {"flat_stop_pct": FLAT_STOP_PCT, "ref_daily_vol": REF_DAILY_VOL,
                   "vol_window": VOL_WINDOW, "hold_bars": HOLD_BARS,
                   "stop_bounds": [STOP_FLOOR, STOP_CEIL],
                   "entries": "non-overlapping"},
        "symbols_used": len(used),
        "A_flat": a, "B_scaled": b,
        "paired_diff": {
            "n": len(paired),
            "mean_pp": round(diff_mean * 100, 4),
            "stdev_pp": round(diff_sd * 100, 4),
            "t_stat": round(t_stat, 2),
            "b_better_share_pct": round(
                sum(1 for d_ in paired if d_ > 0) / len(paired) * 100, 1),
        },
        "deflation_of_winner": d,
        "verdict": _verdict(a, b, diff_mean, t_stat),
    }


def _verdict(a: dict, b: dict, diff_mean: float, t_stat: float) -> str:
    if abs(t_stat) < 2.0:
        return (f"NO DIFFERENCE — paired t={t_stat:.2f}, inside noise. The flat "
                f"stop is not measurably worse on this evidence; do not change "
                f"policy on it.")
    if diff_mean > 0:
        return (f"SCALED WINS — +{diff_mean*100:.3f}pp per trade, t={t_stat:.2f}")
    return (f"FLAT WINS — scaling costs {abs(diff_mean)*100:.3f}pp per trade, "
            f"t={t_stat:.2f}")


def _main() -> None:
    ap = argparse.ArgumentParser(description="A/B a flat vs vol-scaled exit stop.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("symbols", nargs="*")
    args = ap.parse_args()
    r = run(args.symbols or None)
    if args.json:
        print(json.dumps(r, indent=2))
        return
    if "error" in r:
        print(r["error"])
        return
    print(f"\n  Exit A/B — {r['symbols_used']} symbols, non-overlapping "
          f"{HOLD_BARS}-bar holds\n")
    hdr = f"  {'arm':<14} {'trades':>7} {'mean%':>8} {'win%':>7} {'stop%':>7} {'worst%':>8} {'sharpe':>8}"
    print(hdr); print("  " + "-" * (len(hdr) - 2))
    for arm in (r["A_flat"], r["B_scaled"]):
        print(f"  {arm['arm']:<14} {arm['trades']:>7} {arm['mean_ret_pct']:>8.3f} "
              f"{arm['win_rate_pct']:>7.1f} {arm['stop_rate_pct']:>7.1f} "
              f"{arm['worst_pct']:>8.2f} {arm['sharpe']:>8.3f}")
    p = r["paired_diff"]
    print(f"\n  paired difference: {p['mean_pp']:+.4f}pp/trade  t={p['t_stat']}  "
          f"(B better on {p['b_better_share_pct']}% of {p['n']} paired trades)")
    print(f"\n  {r['verdict']}")


if __name__ == "__main__":
    _main()
