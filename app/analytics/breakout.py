"""Intraday breakout screening — detected on live bars, MEASURED before believed.

WHAT THIS ADDS

Every existing screen reads daily closes. `discovery.py` ranks on 6-month
relative strength, `screener.py` filters on liquidity and trend. None of them
can see a name breaking out *today*, because a daily bar does not exist until
the session ends. With 100 symbols now carrying real-time minute bars, the
intraday move is finally observable while it is still actionable.

THE DISCIPLINE THIS INHERITS

`candles.py` is the cautionary example living in this same directory: eight
patterns across four symbols and four horizons produced 3,208 looks, and the
best directional result could not beat the Sharpe expected from selection alone.
Breakout trading has exactly the same literature problem — every setup is drawn
in a book and almost none are scored.

So the split is identical and deliberate:

    detect()    finds the condition. Mechanical, no claim attached.
    measure()   scores every past occurrence against forward returns AND
                against the index over the same window.

`scan()` returns candidates, never ratings. A name appearing here means "this
condition is true right now", not "this will go up". The difference is the whole
file.

WHY THE INDEX IS THE BENCHMARK, NOT THE NAME

The goal is a better STRATEGY, not better name selection. A breakout that gains
3% while SPY gains 3% is a market, not a signal. Every measurement here is
excess over the index, because capital that took the trade could have sat in the
index instead.

WHAT IT WILL NOT DO

Rank a breakout as a BUY, size it, or place anything. It narrows attention. The
conviction engine sizes and the chief gates.

    python -m app.analytics.breakout
    python -m app.analytics.breakout --measure --json
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from dataclasses import dataclass, asdict, field
from typing import Any

BENCHMARK = "SPY"

# An opening-range break needs the range to have formed first. Thirty minutes is
# the conventional window and, more importantly, it is fixed BEFORE looking at
# outcomes — choosing it afterwards from a sweep of candidate windows is how a
# parameter gets fitted and then presented as a discovery.
OPENING_RANGE_MINUTES = 30
VOLUME_SURGE_MULT = 1.5          # session volume pace vs its own recent average
MIN_SESSIONS_TO_MEASURE = 20     # below this, a hit rate is describing noise
RTH_MINUTES = 390                # a full regular session, for prorating volume


@dataclass
class Candidate:
    symbol: str
    last: float
    opening_range_high: float
    opening_range_low: float
    broke: str                      # "up" | "down" | ""
    pct_from_or: float              # distance beyond the opening range
    volume_mult: float              # today's pace vs its own average
    range_expansion: float          # today's range vs its average range
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _minute_sessions(symbol: str) -> dict[str, list]:
    """Minute bars grouped by session date, oldest first."""
    from ..data.minute import load
    out: dict[str, list] = {}
    for b in load(symbol):
        out.setdefault(b.ts.strftime("%Y-%m-%d"), []).append(b)
    return out


def _daily_baseline(symbol: str, lookback: int = 20) -> tuple[float, float]:
    """(avg daily volume, avg daily range %) from the DAILY store.

    Deliberately not from prior minute sessions. Real-time coverage was just
    extended to 100 symbols, but 96 of them carry a single session of minute
    history — enough to price a fill, nowhere near enough for a baseline. The
    daily store already holds years for these names, and a volume average does
    not need minute resolution. Requiring minute history here would have made
    the screen work on three symbols instead of a hundred.
    """
    try:
        from ..data.ohlc import load
        bars = load(symbol, limit=lookback + 1)[:-1]     # exclude today
    except Exception:
        return 0.0, 0.0
    if not bars:
        return 0.0, 0.0
    vols = [float(getattr(b, "volume", 0) or 0) for b in bars]
    ranges = []
    for b in bars:
        hi, lo = float(b.high), float(b.low)
        if lo > 0:
            ranges.append((hi - lo) / lo * 100.0)
    return (st.fmean(vols) if vols else 0.0,
            st.fmean(ranges) if ranges else 0.0)


def detect(symbol: str) -> Candidate | None:
    """Is this name breaking its opening range right now? No claim attached."""
    sessions = _minute_sessions(symbol)
    if not sessions:
        return None
    days = sorted(sessions)
    today = sessions[days[-1]]
    if len(today) <= OPENING_RANGE_MINUTES:
        return None

    opening = today[:OPENING_RANGE_MINUTES]
    rest = today[OPENING_RANGE_MINUTES:]
    or_high = max(b.high for b in opening)
    or_low = min(b.low for b in opening)
    last = rest[-1].close

    broke = ""
    if last > or_high:
        broke = "up"
    elif last < or_low:
        broke = "down"

    ref = or_high if broke == "up" else or_low
    pct = ((last - ref) / ref * 100.0) if ref else 0.0

    # Volume and range are compared to this name's OWN recent behaviour, never
    # to a cross-sectional constant: a 2x volume day means something different
    # on MU than on ABEV.
    avg_vol, avg_range = _daily_baseline(symbol)

    # Volume must be compared at the SAME point in the session. Today is
    # partial — the harvest stores a few hours, not a full day — so measuring
    # it against a full-day average makes every name look quiet and hides the
    # surge the screen exists to find. Prorate the baseline to the fraction of
    # the session actually elapsed.
    today_vol = sum(b.volume for b in today)
    session_fraction = min(1.0, len(today) / float(RTH_MINUTES))
    expected_vol = avg_vol * session_fraction
    vol_mult = (today_vol / expected_vol) if expected_vol > 0 else 0.0

    t_hi, t_lo = max(b.high for b in today), min(b.low for b in today)
    today_range = ((t_hi - t_lo) / t_lo * 100.0) if t_lo > 0 else 0.0
    expansion = (today_range / avg_range) if avg_range > 0 else 0.0

    c = Candidate(symbol=symbol.upper(), last=round(last, 4),
                  opening_range_high=round(or_high, 4),
                  opening_range_low=round(or_low, 4), broke=broke,
                  pct_from_or=round(pct, 2), volume_mult=round(vol_mult, 2),
                  range_expansion=round(expansion, 2))
    if vol_mult >= VOLUME_SURGE_MULT:
        c.reasons.append(f"volume {vol_mult:.1f}x its own average")
    if expansion >= 1.5:
        c.reasons.append(f"range {expansion:.1f}x its own average")
    if not broke:
        c.reasons.append("inside the opening range — no break")
    return c


def scan(symbols: list[str] | None = None) -> dict[str, Any]:
    """Candidates, not ratings. Presence means the condition is true now."""
    if not symbols:
        try:
            from ..data.minute import coverage
            symbols = sorted(coverage())
        except Exception:
            symbols = []

    out: list[Candidate] = []
    for s in symbols:
        try:
            c = detect(s)
        except Exception:
            c = None
        if c and c.broke:
            out.append(c)

    ups = sorted([c for c in out if c.broke == "up"],
                 key=lambda x: -x.pct_from_or)
    downs = sorted([c for c in out if c.broke == "down"],
                   key=lambda x: x.pct_from_or)
    return {
        "agent": "breakout",
        "scanned": len(symbols or []),
        "breaking_up": [c.to_dict() for c in ups[:15]],
        "breaking_down": [c.to_dict() for c in downs[:15]],
        "note": (f"opening range = first {OPENING_RANGE_MINUTES} minutes, fixed "
                 f"before any outcome was examined"),
        "does_not": [
            "rate any of these a BUY — presence is a condition, not a forecast",
            "claim an edge; run --measure, and read the index-relative column",
            "size or place anything",
        ],
    }


def measure(symbol: str, horizon_days: int = 3) -> dict[str, Any]:
    """Score past opening-range breaks against forward return AND the index."""
    from .decision_score import _closes

    sessions = _minute_sessions(symbol)
    days = sorted(sessions)
    if len(days) < MIN_SESSIONS_TO_MEASURE:
        return {"symbol": symbol.upper(), "available": False,
                "why": f"only {len(days)} session(s); {MIN_SESSIONS_TO_MEASURE}+ "
                       f"needed before a hit rate means anything"}

    bench = {d: c for d, c in _closes(BENCHMARK)}
    closes = {d: sessions[d][-1].close for d in days}

    hits, excess = [], []
    for i, d in enumerate(days[:-horizon_days]):
        bars = sessions[d]
        if len(bars) <= OPENING_RANGE_MINUTES:
            continue
        or_high = max(b.high for b in bars[:OPENING_RANGE_MINUTES])
        if bars[-1].close <= or_high:
            continue                                   # no upside break that day
        entry = bars[-1].close
        exit_day = days[i + horizon_days]
        fwd = (closes[exit_day] - entry) / entry * 100.0
        hits.append(fwd)
        b0, b1 = bench.get(d), bench.get(exit_day)
        if b0 and b1 and b0 > 0:
            excess.append(fwd - (b1 - b0) / b0 * 100.0)

    if not hits:
        return {"symbol": symbol.upper(), "available": False,
                "why": "no upside breaks in the stored window"}

    out = {
        "symbol": symbol.upper(), "available": True, "n": len(hits),
        "horizon_days": horizon_days,
        "mean_forward_pct": round(st.fmean(hits), 2),
        "win_rate_pct": round(sum(1 for h in hits if h > 0) / len(hits) * 100, 1),
    }
    if excess:
        out["vs_index"] = {
            "benchmark": BENCHMARK, "n": len(excess),
            "mean_excess_pct": round(st.fmean(excess), 2),
            "beat_index_pct": round(sum(1 for e in excess if e > 0) / len(excess) * 100, 1),
        }
    out["caution"] = (
        f"n={len(hits)} from one name and one window. This is a description of "
        f"what happened, not an edge — an edge needs deflation across every "
        f"variant examined (app/backtest/trials.py).")
    return out


def report() -> dict[str, Any]:
    return scan()


def _main() -> None:                              # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description="Intraday breakout screen on live minute bars.")
    ap.add_argument("--measure", action="store_true", help="score past breaks instead")
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--horizon", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.measure:
        syms = args.symbols
        if not syms:
            from ..data.minute import coverage
            syms = sorted(coverage())[:12]
        rows = [measure(s, args.horizon) for s in syms]
        if args.json:
            print(json.dumps(rows, indent=2))
            return
        print("=" * 78)
        print(f"  BREAKOUT MEASUREMENT — {args.horizon}-day forward, vs {BENCHMARK}")
        print("=" * 78)
        print(f"\n  {'SYM':7}{'N':>5}{'WIN%':>8}{'FWD%':>9}{'vs IDX':>9}{'BEAT%':>8}")
        print("  " + "-" * 74)
        for r in rows:
            if not r.get("available"):
                print(f"  {r['symbol']:7}  {r['why']}")
                continue
            vi = r.get("vs_index") or {}
            print(f"  {r['symbol']:7}{r['n']:>5}{r['win_rate_pct']:>7.1f}%"
                  f"{r['mean_forward_pct']:>8.2f}%"
                  f"{vi.get('mean_excess_pct', 0):>8.2f}%"
                  f"{vi.get('beat_index_pct', 0):>7.1f}%")
        print("\n  vs IDX is the column that matters: a break that gains 3% while")
        print(f"  {BENCHMARK} gains 3% is a market, not a signal.")
        return

    r = scan(args.symbols)
    if args.json:
        print(json.dumps(r, indent=2))
        return
    print("=" * 78)
    print(f"  BREAKOUT SCREEN — {r['scanned']} symbol(s) on live minute bars")
    print("=" * 78)
    for label, key in (("BREAKING UP", "breaking_up"), ("BREAKING DOWN", "breaking_down")):
        rows = r[key]
        print(f"\n  {label}  ({len(rows)})")
        if not rows:
            print("    none")
            continue
        print(f"    {'SYM':7}{'LAST':>10}{'FROM OR':>10}{'VOL x':>8}{'RANGE x':>9}   WHY")
        for c in rows:
            print(f"    {c['symbol']:7}{c['last']:>10.2f}{c['pct_from_or']:>9.2f}%"
                  f"{c['volume_mult']:>8.2f}{c['range_expansion']:>9.2f}"
                  f"   {'; '.join(c['reasons']) or '-'}")
    print(f"\n  {r['note']}")
    print("  Presence is a CONDITION, not a forecast. Run --measure for evidence.")


if __name__ == "__main__":                        # pragma: no cover
    _main()
