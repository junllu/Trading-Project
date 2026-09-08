"""Feature panel — the inputs a prediction is allowed to use, and nothing else.

Candles alone failed: the best of 124 looks scored a Sharpe of 0.66 against a
selection bar of 0.80. That is the expected outcome for a single weak signal,
and the fix is not a better pattern — it is CONDITIONING. A signal that is noise
on average can still carry information in a particular regime, and the way to
find out is to measure the signal and the regime separately, then together.

So this module produces one row per (symbol, bar) with every candidate input
side by side, and app/backtest/combine.py does the combining. Keeping them apart
is what makes the trial count countable: you cannot deflate a result honestly if
you cannot say how many things you tried.

THREE RULES, EACH THE FIX TO A SPECIFIC WAY OF FOOLING YOURSELF

1. NOTHING FROM THE FUTURE. Every feature at bar i is computed from bars[:i+1]
   only. Moving averages, vol ranks and percentiles are all trailing. This is
   easy to state and easy to violate — a percentile over the whole series is the
   classic version, and it makes any backtest look brilliant.

2. YOU CANNOT TRADE THE CLOSE YOU JUST OBSERVED. A candle is only a candle once
   the bar has closed, so the earliest realistic entry is the NEXT bar's open.
   Entering at the signal bar's close is worth roughly a full overnight gap of
   free money, which is exactly where a gap pattern's apparent edge comes from.
   Forward returns here are open-to-close, entry deliberately delayed.

3. RANK ACROSS THE CROSS-SECTION, NOT THE TIME SERIES. "High momentum" in
   2021 meant something different than in 2022. Absolute thresholds bake the
   era into the rule; a rank against the other names ON THE SAME DAY does not.

    python -m app.analytics.features TSLA
    python -m app.analytics.features --panel
"""
from __future__ import annotations

import argparse
import statistics as st
from dataclasses import dataclass, field

from ..data.ohlc import Bar, load

# Lookbacks. Conventional values on purpose — tuning these on the same data the
# result is measured against is how a trial count silently becomes thousands.
TREND_SLOW = 200
TREND_FAST = 50
VOL_WINDOW = 20
VOL_RANK_WINDOW = 252
RVOL_WINDOW = 20
RSI_WINDOW = 2          # the short-horizon mean-reversion version
MOM_LONG = 252
MOM_SKIP = 21           # 12-1 momentum: skip the most recent month
MOM_MED = 63
ATR_WINDOW = 14

# Every continuous feature this module emits. Named explicitly because
# combine.py counts them to set the deflation bar — an unlisted feature that
# sneaks into a test is an untracked trial.
FEATURES = (
    "trend_200",        # close / 200dma - 1        : regime, slow
    "trend_50",         # close / 50dma - 1         : regime, fast
    "mom_12_1",         # 12-month return skipping the last month
    "mom_63",           # 3-month return
    "rsi_2",            # short-horizon mean reversion, 0-100
    "vol_20",           # realised vol, annualised %
    "vol_rank",         # that vol's percentile in its OWN trailing year, 0-1
    "rvol",             # today's volume / 20d average volume
    "atr_dist",         # (close - 20dma) in ATR units : stretch
    "rel_str_63",       # 63d return minus the benchmark's, in points
    "gap_atr",          # today's open vs yesterday's close, in ATR units
)


@dataclass
class Row:
    symbol: str
    date: str
    index: int
    close: float
    features: dict[str, float] = field(default_factory=dict)
    patterns: set[str] = field(default_factory=set)
    forward: dict[int, float] = field(default_factory=dict)

    def get(self, name: str) -> float | None:
        return self.features.get(name)


def _sma(vals: list[float], i: int, n: int) -> float | None:
    if i + 1 < n:
        return None
    return sum(vals[i - n + 1: i + 1]) / n


def _atr(bars: list[Bar], i: int, n: int = ATR_WINDOW) -> float:
    lo = max(1, i - n + 1)
    trs = [max(bars[j].high - bars[j].low,
               abs(bars[j].high - bars[j - 1].close),
               abs(bars[j].low - bars[j - 1].close))
           for j in range(lo, i + 1)]
    return st.mean(trs) if trs else 0.0


def _rsi(closes: list[float], i: int, n: int = RSI_WINDOW) -> float | None:
    if i < n:
        return None
    gains = losses = 0.0
    for j in range(i - n + 1, i + 1):
        d = closes[j] - closes[j - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    if gains + losses == 0:
        return 50.0
    return 100.0 * gains / (gains + losses)


def compute(symbol: str, horizons: tuple[int, ...] = (1, 3, 5, 10),
            benchmark: str = "SPY") -> list[Row]:
    """Every feature for every bar of one symbol. Trailing-only, by construction."""
    from .candles import detect

    bars = load(symbol)
    if len(bars) < MOM_LONG + 10:
        return []
    closes = [b.close for b in bars]
    vols = [b.volume for b in bars]

    # Benchmark returns, aligned BY DATE. Aligning by index would quietly
    # compare a symbol's bar 400 against SPY's bar 400, which are different days
    # whenever a listing is younger than the benchmark.
    bench = {b.date: b.close for b in load(benchmark)} if benchmark else {}

    pat_at: dict[int, set[str]] = {}
    for h in detect(bars, symbol):
        pat_at.setdefault(h.index, set()).add(h.pattern)

    # Trailing realised vol, needed before vol_rank can rank it.
    rets = [0.0] + [(closes[i] / closes[i - 1] - 1) for i in range(1, len(closes))]
    vol_series: list[float | None] = []
    for i in range(len(bars)):
        if i < VOL_WINDOW:
            vol_series.append(None)
        else:
            vol_series.append(st.pstdev(rets[i - VOL_WINDOW + 1: i + 1]) * (252 ** 0.5) * 100)

    out: list[Row] = []
    for i in range(len(bars)):
        b = bars[i]
        f: dict[str, float] = {}

        s200, s50, s20 = (_sma(closes, i, TREND_SLOW), _sma(closes, i, TREND_FAST),
                          _sma(closes, i, VOL_WINDOW))
        if s200:
            f["trend_200"] = closes[i] / s200 - 1
        if s50:
            f["trend_50"] = closes[i] / s50 - 1

        if i >= MOM_LONG:
            # 12-1: the most recent month is SKIPPED. Short-term reversal runs
            # opposite to momentum, and folding it in cancels the signal out.
            f["mom_12_1"] = closes[i - MOM_SKIP] / closes[i - MOM_LONG] - 1
        if i >= MOM_MED:
            f["mom_63"] = closes[i] / closes[i - MOM_MED] - 1

        r = _rsi(closes, i)
        if r is not None:
            f["rsi_2"] = r

        v = vol_series[i]
        if v is not None:
            f["vol_20"] = v
            hist = [x for x in vol_series[max(0, i - VOL_RANK_WINDOW + 1): i + 1]
                    if x is not None]
            if len(hist) >= 60:
                f["vol_rank"] = sum(1 for x in hist if x <= v) / len(hist)

        if i >= RVOL_WINDOW:
            avg = st.mean(vols[i - RVOL_WINDOW: i]) or 0.0
            if avg > 0:
                f["rvol"] = vols[i] / avg

        atr = _atr(bars, i)
        if s20 and atr > 0:
            f["atr_dist"] = (closes[i] - s20) / atr
        if i >= 1 and atr > 0:
            f["gap_atr"] = (b.open - closes[i - 1]) / atr

        if bench and i >= MOM_MED:
            d_now, d_then = bars[i].date, bars[i - MOM_MED].date
            if d_now in bench and d_then in bench:
                own = closes[i] / closes[i - MOM_MED] - 1
                bm = bench[d_now] / bench[d_then] - 1
                f["rel_str_63"] = (own - bm) * 100

        # Forward return, ENTERED AT THE NEXT OPEN. The signal is known at
        # bar i's close; the earliest fill is bar i+1's open. Measuring from
        # bar i's close instead hands the strategy the overnight gap for free.
        fwd: dict[int, float] = {}
        for h in horizons:
            if i + 1 < len(bars) and i + h < len(bars):
                entry = bars[i + 1].open
                if entry > 0:
                    fwd[h] = (bars[i + h].close / entry - 1) * 100

        out.append(Row(symbol=symbol, date=b.date, index=i, close=closes[i],
                       features=f, patterns=pat_at.get(i, set()), forward=fwd))
    return out


def panel(symbols: list[str] | None = None,
          horizons: tuple[int, ...] = (1, 3, 5, 10)) -> list[Row]:
    """Feature rows for the whole cross-section, cross-sectionally ranked.

    The ranking is the point. An absolute momentum threshold learned in 2021
    describes 2021; a rank against the other names on the same day survives the
    regime change because it is relative by construction.
    """
    from ..data.ohlc import coverage, BENCHMARKS, SECTOR_ETFS, VOL_INDEX

    skip = set(BENCHMARKS) | set(SECTOR_ETFS) | set(VOL_INDEX)
    syms = symbols or [s for s in sorted(coverage()) if s not in skip]

    rows: list[Row] = []
    for s in syms:
        rows.extend(compute(s, horizons))

    by_date: dict[str, list[Row]] = {}
    for r in rows:
        by_date.setdefault(r.date, []).append(r)

    for _d, group in by_date.items():
        if len(group) < 5:          # too thin a cross-section to rank into
            continue
        for name in FEATURES:
            vals = [(r, r.features[name]) for r in group if name in r.features]
            if len(vals) < 5:
                continue
            vals.sort(key=lambda t: t[1])
            n = len(vals) - 1
            for rank, (r, _v) in enumerate(vals):
                r.features[f"{name}_rank"] = rank / n if n else 0.5
    return rows


def report() -> dict:
    rows = panel()
    if not rows:
        return {"error": "no OHLC — run python -m app.data.ohlc --universe"}
    syms = {r.symbol for r in rows}
    cov = {f: round(100 * sum(1 for r in rows if f in r.features) / len(rows), 1)
           for f in FEATURES}
    return {"rows": len(rows), "symbols": len(syms),
            "dates": len({r.date for r in rows}),
            "feature_coverage_pct": cov,
            "note": "Entry is the NEXT bar's open; features are trailing-only."}


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="*")
    ap.add_argument("--panel", action="store_true")
    args = ap.parse_args()

    if args.panel or not args.symbols:
        r = report()
        if "error" in r:
            print(r["error"])
            return
        print(f"  panel: {r['rows']:,} rows   {r['symbols']} symbols   {r['dates']} dates")
        print("\n  feature coverage")
        for k, v in r["feature_coverage_pct"].items():
            print(f"    {k:14} {v:>6.1f}%")
        return

    for s in args.symbols:
        rows = compute(s)
        if not rows:
            print(f"  {s}: insufficient history")
            continue
        last = rows[-1]
        print(f"\n  {s}  {last.date}  close {last.close:.2f}")
        for k in FEATURES:
            v = last.features.get(k)
            print(f"    {k:14} {'—' if v is None else f'{v:>10.3f}'}")
        if last.patterns:
            print(f"    patterns       {', '.join(sorted(last.patterns))}")


if __name__ == "__main__":
    _main()
