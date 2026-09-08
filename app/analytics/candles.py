"""Candlestick patterns — detected, then MEASURED. Never assumed.

Candlestick literature is the least evidence-backed corner of technical
analysis: patterns are named, drawn, and asserted to work, almost never scored.
"Bullish engulfing" appears in every textbook with a diagram and no hit rate.

So this module does two separable things, and the split is the point:

    detect()   finds the pattern. Mechanical, no claim attached.
    measure()  scores every occurrence against forward returns, against the
               benchmark of simply holding the same name over the same window.

A pattern earns its place only if `measure()` says so, and `deflate()` sends the
result through app/backtest/trials.py so the count of patterns examined discounts
whatever the best one shows. Eight patterns x four symbols x four horizons is 124
live cells, and the best of 124 draws from noise looks like an edge on its own.

Read `deflate()`, never the top row of `measure()`. As of the current cache the
best cell scores a Sharpe of 0.66 against a selection bar of 0.80 — that is, it
does not beat what looking 124 times produces by chance.

WHAT "WORKS" HAS TO MEAN

Not "price rose after". Price rises after most bars in a bull market. The test
is EXCESS return over the same name's own average move across the same horizon —
if a hammer is followed by +1.2% and the average day is followed by +1.1%, the
hammer told you nothing.

Definitions use ATR-relative thresholds rather than fixed percentages, so a
pattern means the same thing on a $410 stock and a $40 one.

    python -m app.analytics.candles TSLA
    python -m app.analytics.candles TSLA AMD NVDA --measure
"""
from __future__ import annotations

import argparse
import json
import math
import statistics as st
from dataclasses import dataclass, field

from ..data.ohlc import Bar, load

# Thresholds, all ATR- or ratio-relative so they travel across price levels.
DOJI_BODY_MAX = 0.10        # body <= 10% of the bar's range
LONG_WICK_MIN = 2.0         # wick at least 2x the body
MARUBOZU_BODY_MIN = 0.90
GAP_ATR_MIN = 0.5           # a gap worth naming is >= half an ATR
ATR_WINDOW = 14

# Below this, the standard deviation in a Sharpe's denominator is itself noise.
MIN_OCCURRENCES_FOR_SHARPE = 20

PATTERNS = ("bullish_engulfing", "bearish_engulfing", "hammer", "shooting_star",
            "doji", "gap_up", "gap_down", "inside_bar")

# Which way each pattern is conventionally said to point. Stated so `measure()`
# can score the CLAIM, not merely the subsequent move — a "bearish" pattern
# followed by a rise is a miss, and folding it in as a win would flatter it.
DIRECTION = {
    "bullish_engulfing": +1, "hammer": +1, "gap_up": +1,
    "bearish_engulfing": -1, "shooting_star": -1, "gap_down": -1,
    "doji": 0, "inside_bar": 0,          # indecision — no direction claimed
}


@dataclass
class Hit:
    symbol: str
    date: str
    pattern: str
    index: int


@dataclass
class PatternScore:
    pattern: str
    symbol: str
    horizon: int
    n: int = 0
    mean_move_pct: float = 0.0
    baseline_pct: float = 0.0          # the same name's average move, SAME measure
    hit_rate_pct: float = 0.0
    direction: int = 0
    occurrences_per_year: float = 0.0
    std_pct: float = 0.0               # dispersion of the per-occurrence excess

    @property
    def sharpe(self) -> float:
        """Annualised Sharpe of trading this pattern every time it fires.

        This exists because excess_pct is NOT a Sharpe and must never be fed to
        one. Deflation asks "how good is this ratio against the best of N random
        looks", and a raw percentage put in that slot printed a meaningless
        'PASS 0.9998' — a big number in the wrong units reads as overwhelming
        evidence. A Sharpe needs the dispersion too: +0.9% average with 8%
        noise is not the same finding as +0.9% with 1%.

        Scaled by how often the pattern actually fires. A signal appearing three
        times a year cannot compound like a daily one, and annualising by the
        horizon instead would silently pretend it could.
        """
        if self.n < MIN_OCCURRENCES_FOR_SHARPE or self.std_pct <= 0:
            return 0.0
        per_trade = self.excess_pct / self.std_pct
        return round(per_trade * math.sqrt(max(self.occurrences_per_year, 0.0)), 3)

    @property
    def excess_pct(self) -> float:
        """Move BEYOND the name's usual behaviour, measured the SAME way.

        Both terms must be the same quantity. Scoring a neutral pattern by
        absolute move while baselining against the signed mean produced a 100%
        hit rate and a fabricated +5.68 "excess" on doji — |x| is always
        positive, so every occurrence counted as a win by construction. The
        baseline for a neutral pattern is therefore the mean ABSOLUTE move.
        """
        return round(self.mean_move_pct - self.baseline_pct, 3)

    def to_dict(self) -> dict:
        return dict(self.__dict__) | {"excess_pct": self.excess_pct,
                                      "sharpe": self.sharpe}


def _atr(bars: list[Bar], i: int, window: int = ATR_WINDOW) -> float:
    lo = max(0, i - window)
    trs = []
    for j in range(lo + 1, i + 1):
        prev = bars[j - 1].close
        trs.append(max(bars[j].high - bars[j].low,
                       abs(bars[j].high - prev), abs(bars[j].low - prev)))
    return st.mean(trs) if trs else 0.0


def detect(bars: list[Bar], symbol: str = "") -> list[Hit]:
    """Find every pattern occurrence. Mechanical — no claim of usefulness."""
    hits: list[Hit] = []
    for i in range(1, len(bars)):
        b, p = bars[i], bars[i - 1]
        atr = _atr(bars, i)
        if b.range <= 0:
            continue

        # Engulfing: today's body fully covers yesterday's, opposite colour.
        if b.bullish and not p.bullish and b.close >= p.open and b.open <= p.close:
            hits.append(Hit(symbol, b.date, "bullish_engulfing", i))
        if not b.bullish and p.bullish and b.close <= p.open and b.open >= p.close:
            hits.append(Hit(symbol, b.date, "bearish_engulfing", i))

        # Hammer / shooting star: one long wick, small body at the other end.
        if b.body > 0:
            if b.lower_wick >= LONG_WICK_MIN * b.body and b.upper_wick <= b.body:
                hits.append(Hit(symbol, b.date, "hammer", i))
            if b.upper_wick >= LONG_WICK_MIN * b.body and b.lower_wick <= b.body:
                hits.append(Hit(symbol, b.date, "shooting_star", i))

        if b.body_pct <= DOJI_BODY_MAX:
            hits.append(Hit(symbol, b.date, "doji", i))

        # Gaps, sized in ATR so they mean the same thing at any price.
        if atr > 0:
            if b.low - p.high >= GAP_ATR_MIN * atr:
                hits.append(Hit(symbol, b.date, "gap_up", i))
            if p.low - b.high >= GAP_ATR_MIN * atr:
                hits.append(Hit(symbol, b.date, "gap_down", i))

        if b.high <= p.high and b.low >= p.low:
            hits.append(Hit(symbol, b.date, "inside_bar", i))
    return hits


def measure(symbol: str, horizon: int = 5) -> list[PatternScore]:
    """Score every pattern against the name's OWN average move.

    The baseline is the point. Price rises after most bars in a bull market, so
    "went up afterwards" is not evidence. A pattern has to beat the drift it is
    embedded in.
    """
    bars = load(symbol)
    if len(bars) < horizon + ATR_WINDOW + 2:
        return []

    fwd = [(bars[i + horizon].close / bars[i].close - 1) * 100
           for i in range(len(bars) - horizon)]
    if not fwd:
        return []
    # Two baselines, because directional and neutral patterns make different
    # claims. A bullish signal claims price RISES; a doji claims price MOVES.
    # Each is scored against the matching background.
    baseline_signed = st.mean(fwd)
    baseline_abs = st.mean(abs(x) for x in fwd)
    years = len(bars) / 252

    by_pattern: dict[str, list[int]] = {}
    for h in detect(bars, symbol):
        by_pattern.setdefault(h.pattern, []).append(h.index)

    out: list[PatternScore] = []
    for pat in PATTERNS:
        idxs = [i for i in by_pattern.get(pat, []) if i < len(fwd)]
        if len(idxs) < 10:
            continue
        moves = [fwd[i] for i in idxs]
        d = DIRECTION[pat]
        # For a directional pattern, score the CLAIM: a bearish signal is right
        # when price falls. For a neutral one, score absolute movement.
        if d:
            scored = [m * d for m in moves]
            base = baseline_signed * d
            # "was the direction right" — a real coin flip to beat
            hits = sum(1 for x in scored if x > 0)
        else:
            scored = [abs(m) for m in moves]
            base = baseline_abs
            # For a neutral pattern the claim is "moves MORE than usual", so a
            # hit is beating the typical move — not merely being positive,
            # which |x| guarantees.
            hits = sum(1 for x in scored if x > baseline_abs)
        out.append(PatternScore(
            pattern=pat, symbol=symbol, horizon=horizon, n=len(idxs),
            mean_move_pct=round(st.mean(scored), 3),
            baseline_pct=round(base, 3), direction=d,
            std_pct=round(st.pstdev(scored), 3) if len(scored) > 1 else 0.0,
            hit_rate_pct=round(100 * hits / len(idxs), 1),
            occurrences_per_year=round(len(idxs) / years, 1)))
    return sorted(out, key=lambda s: -s.excess_pct)


def grid(symbols: list[str] | None = None,
         horizons: tuple[int, ...] = (1, 3, 5, 10)) -> list[PatternScore]:
    """Every pattern x symbol x horizon. The SIZE of this grid is the finding.

    Each cell is a look, and the number of looks is exactly what deflation
    needs. Scoring eight patterns on four symbols across four horizons is 124
    live cells; the best of 124 draws from pure noise is impressive on its own.
    """
    from ..data.ohlc import coverage
    syms = symbols or sorted(coverage())
    return [s for sym in syms for h in horizons for s in measure(sym, h)]


def deflate(symbols: list[str] | None = None) -> dict:
    """Score the grid, then discount the winner for how hard we looked."""
    from ..backtest.trials import deflated_sharpe

    rows = grid(symbols)
    if not rows:
        return {"error": "no OHLC cached — run python -m app.data.ohlc TSLA"}

    ranked = sorted(rows, key=lambda r: -r.sharpe)
    best = ranked[0]
    sharpes = [r.sharpe for r in rows]
    var = st.pvariance(sharpes) if len(sharpes) > 1 else 0.0

    # n_periods is the number of OCCURRENCES the best cell actually saw, not
    # the number of bars in the file. The Sharpe was estimated from those
    # occurrences and its standard error follows from them alone.
    d = deflated_sharpe(observed_sr=best.sharpe, n_periods=max(best.n, 1),
                        n_trials=len(rows), sharpe_variance=var)
    return {
        "looks": len(rows),
        "positive_excess": sum(1 for r in rows if r.excess_pct > 0),
        "best": best.to_dict(),
        "top5": [r.to_dict() for r in ranked[:5]],
        "deflation": d,
        "verdict": ("SURVIVES" if d["deflated_sharpe_ratio"] >= 0.95
                    and best.sharpe > d["expected_max_sharpe_under_no_edge"]
                    else "NOT DISTINGUISHABLE FROM SELECTION"),
    }


def report() -> dict:
    """Agent entrypoint — the deflated view, never the raw best cell."""
    return deflate()


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="*", default=["TSLA"])
    ap.add_argument("--horizons", default="1,3,5,10")
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    horizons = [int(x) for x in args.horizons.split(",") if x.strip()]

    if args.json:
        print(json.dumps(report(), indent=2))
        return

    print("=" * 86)
    print("  CANDLESTICK PATTERNS — excess return over the name's OWN average move")
    print("=" * 86)
    for sym in args.symbols:
        bars = load(sym)
        if not bars:
            print(f"\n  {sym}: no OHLC — run  python -m app.data.ohlc {sym}")
            continue
        print(f"\n  {sym}   {len(bars)} bars   {bars[0].date} -> {bars[-1].date}")
        print(f"  {'pattern':20} {'dir':>4} {'n':>5} {'/yr':>6} "
              f"{'hit%':>7} {'move%':>8} {'base%':>8} {'EXCESS':>8}")
        print("  " + "-" * 78)
        for h in horizons:
            scores = measure(sym, h)
            if not scores:
                continue
            print(f"  horizon {h}d")
            for s in scores:
                flag = "  <--" if s.excess_pct > 0.5 and s.n >= 20 else ""
                d = {1: "up", -1: "dn", 0: "abs"}[s.direction]
                print(f"    {s.pattern:18} {d:>4} {s.n:>5} {s.occurrences_per_year:>6.1f} "
                      f"{s.hit_rate_pct:>6.1f}% {s.mean_move_pct:>+7.2f} "
                      f"{s.baseline_pct:>+7.2f} {s.excess_pct:>+7.2f}{flag}")

    print("\n  Excess is the move BEYOND the name's usual drift. Price rises after")
    print("  most bars in a bull market, so 'went up afterwards' is not evidence.")
    print("  Nothing here is a signal until it survives trials.py deflation.")


if __name__ == "__main__":
    _main()
