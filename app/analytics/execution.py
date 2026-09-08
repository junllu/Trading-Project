"""Execution timing — WHEN inside the session to send the order.

The portal emits market orders and CLAUDE.md executes them as market orders.
That is a defensible choice (a limit that never fills is its own risk), but it
makes the send time the only execution lever left, and right now that time is
"whenever the human happens to approve the plan". On a name that routinely
travels 5-6% intraday, an arbitrary send time is not a rounding error.

WHAT THIS MEASURES, AND WHAT IT REFUSES TO

Cost of trading at a given minute is proxied by bar range relative to the
volume printed in it: wide range on thin volume is expensive, tight range on
heavy volume is cheap. That is a proxy, not a spread — the historical bar API
carries no bid/ask, so calling it a spread would be a lie the downstream code
would then trust.

The refusal matters more than the metric. With two days of bars, the "best"
30-minute bucket is noise, and a module that answers anyway becomes a machine
for manufacturing false precision — the same trap the conviction work already
fell into. `profile()` therefore reports its own sample size and
`recommend()` returns INSUFFICIENT below `min_days`, with no bucket named.

Boundaries: intraday.py measures exits, this measures send timing, exit_plans.py
sets policy. None of the three may name a stop level or a conviction threshold
belonging to another.

    python -m app.analytics.execution --profile MRVL
    python -m app.analytics.execution --recommend MRVL
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import timedelta

from .intraday import by_day

# 30-minute buckets measured from the open, so the labels survive DST and any
# half-day session without a timezone lookup.
BUCKET_MINUTES = 30

# Below this, a recommendation is curve-fitting. 20 sessions is not a
# statistical guarantee either — it is the point at which each of the 13
# buckets has ~20 observations, which is the bare minimum for the spread
# between buckets to mean anything at all.
MIN_DAYS_FOR_RECOMMENDATION = 20


@dataclass
class Bucket:
    label: str
    minute_from_open: int
    days: int = 0
    mean_range_pct: float = 0.0
    mean_volume_share: float = 0.0
    mean_abs_move_pct: float = 0.0
    cost_index: float = 0.0          # range per unit of volume share; lower is cheaper
    _ranges: list = field(default_factory=list, repr=False)
    _vols: list = field(default_factory=list, repr=False)
    _moves: list = field(default_factory=list, repr=False)


def profile(symbol: str, sessions: tuple[str, ...] = ("RTH",)) -> dict:
    """Per-bucket liquidity and volatility across every stored session."""
    days = by_day(symbol, sessions)
    buckets: dict[int, Bucket] = {}

    for _d, bars in sorted(days.items()):
        if len(bars) < 30:
            continue                                   # half-session or bad day
        day_open = bars[0].ts
        day_vol = sum(b.volume for b in bars) or 1.0
        grouped: dict[int, list] = {}
        for b in bars:
            idx = int((b.ts - day_open).total_seconds() // 60) // BUCKET_MINUTES
            grouped.setdefault(idx, []).append(b)

        for idx, bs in grouped.items():
            hi = max(x.high for x in bs)
            lo = min(x.low for x in bs)
            o = bs[0].open
            if o <= 0:
                continue
            bk = buckets.setdefault(idx, Bucket(
                label=_label(idx), minute_from_open=idx * BUCKET_MINUTES))
            bk._ranges.append((hi - lo) / o)
            bk._vols.append(sum(x.volume for x in bs) / day_vol)
            bk._moves.append(abs(bs[-1].close / o - 1.0))

    out = []
    for idx in sorted(buckets):
        bk = buckets[idx]
        n = len(bk._ranges)
        bk.days = n
        bk.mean_range_pct = sum(bk._ranges) / n
        bk.mean_volume_share = sum(bk._vols) / n
        bk.mean_abs_move_pct = sum(bk._moves) / n
        # Cheap = little price travel per unit of liquidity available.
        bk.cost_index = (bk.mean_range_pct / bk.mean_volume_share
                         if bk.mean_volume_share > 0 else float("inf"))
        out.append(bk)

    return {"symbol": symbol.upper(), "days": len(days), "buckets": out,
            "sessions": list(sessions)}


def _label(idx: int) -> str:
    """Minutes from the open, as a clock offset rather than a wall time."""
    start = idx * BUCKET_MINUTES
    return f"+{start // 60}h{start % 60:02d}m"


def recommend(symbol: str, min_days: int = MIN_DAYS_FOR_RECOMMENDATION) -> dict:
    """The cheapest bucket to send into — or an explicit refusal.

    Returning INSUFFICIENT is the whole design. A ranked list built on three
    sessions looks identical to one built on three hundred once it reaches a
    report, and by then nobody remembers which it was.
    """
    p = profile(symbol)
    n_days = p["days"]
    if n_days < min_days:
        return {
            "symbol": p["symbol"], "status": "INSUFFICIENT",
            "days_available": n_days, "days_required": min_days,
            "recommendation": None,
            "why": (f"{n_days} session(s) stored; {min_days} required before a "
                    f"time-of-day preference is anything but noise. Harvest more "
                    f"minute history first."),
        }

    ranked = sorted((b for b in p["buckets"] if b.days >= min_days * 0.8),
                    key=lambda b: b.cost_index)
    if not ranked:
        return {"symbol": p["symbol"], "status": "INSUFFICIENT",
                "days_available": n_days, "recommendation": None,
                "why": "no bucket had consistent coverage across sessions"}

    best, worst = ranked[0], ranked[-1]
    spread = (worst.cost_index / best.cost_index - 1.0) if best.cost_index else 0.0
    return {
        "symbol": p["symbol"], "status": "OK", "days_available": n_days,
        "recommendation": best.label,
        "avoid": worst.label,
        "cost_spread_pct": round(spread * 100, 1),
        "detail": [{"bucket": b.label, "cost_index": round(b.cost_index, 4),
                    "mean_range_pct": round(b.mean_range_pct, 5),
                    "volume_share": round(b.mean_volume_share, 4),
                    "days": b.days} for b in ranked],
        "caveat": ("cost_index is range-per-volume, a PROXY for execution cost. "
                   "No bid/ask is available in historical bars, so this ranks "
                   "relative cheapness only — it is not a spread estimate."),
    }


def annotate_plan(symbols: list[str], min_days: int = MIN_DAYS_FOR_RECOMMENDATION) -> dict:
    """Timing notes for the symbols in a trade plan, keyed by symbol.

    Advisory only. This never changes an order's size, side or price — the plan
    is the brain's output and execution timing is not licence to reshape it.
    """
    out = {}
    for s in symbols:
        r = recommend(s, min_days=min_days)
        out[s.upper()] = ({"send_window": r["recommendation"], "avoid": r.get("avoid"),
                           "cost_spread_pct": r.get("cost_spread_pct")}
                          if r["status"] == "OK"
                          else {"send_window": None, "note": r["why"]})
    return out


def _main() -> None:
    ap = argparse.ArgumentParser(description="Execution timing from minute bars.")
    ap.add_argument("--profile", metavar="SYMBOL")
    ap.add_argument("--recommend", metavar="SYMBOL")
    ap.add_argument("--min-days", type=int, default=MIN_DAYS_FOR_RECOMMENDATION)
    args = ap.parse_args()

    if args.profile:
        p = profile(args.profile)
        print(f"{p['symbol']} — {p['days']} session(s) stored\n")
        print(f"  {'bucket':>8} {'days':>5} {'range%':>9} {'vol share':>10} "
              f"{'|move|%':>9} {'cost idx':>9}")
        for b in p["buckets"]:
            print(f"  {b.label:>8} {b.days:>5} {b.mean_range_pct:>8.3%} "
                  f"{b.mean_volume_share:>9.2%} {b.mean_abs_move_pct:>8.3%} "
                  f"{b.cost_index:>9.4f}")
        if p["days"] < args.min_days:
            print(f"\n  NOTE: {p['days']} session(s) — below the {args.min_days} "
                  f"needed for a recommendation. Numbers shown are descriptive only.")
        return

    if args.recommend:
        r = recommend(args.recommend, min_days=args.min_days)
        if r["status"] != "OK":
            print(f"{r['symbol']}: {r['status']} — {r['why']}")
            return
        print(f"{r['symbol']}: send in {r['recommendation']}, avoid {r['avoid']} "
              f"({r['cost_spread_pct']}% cost spread, {r['days_available']} sessions)")
        for d in r["detail"]:
            print(f"   {d['bucket']:>8}  cost {d['cost_index']:>8.4f}  "
                  f"range {d['mean_range_pct']:>7.3%}  vol {d['volume_share']:>6.2%}")
        print(f"\n  {r['caveat']}")
        return

    ap.print_help()


if __name__ == "__main__":
    _main()
