"""Breakouts and breakdowns, alone and confirmed — every combination counted.

    python -m app.backtest.breakout_study
    python -m app.backtest.breakout_study --no-record     # don't log trials

THE QUESTION

Does a price breaking out of (or down through) its recent range predict the
next 5-20 sessions — and does it predict BETTER when other signals agree? The
user's strategy direction (2026-09-27): breakouts/breakdowns in coordination
with other signals, combinations tested, then backtest -> forward test -> live.

THE GRID IS FIXED BEFORE ANY RESULT IS SEEN

    triggers       4   bo20 bo55 (close above prior N-day high, fresh cross)
                       bd20 bd55 (close below prior N-day low,  fresh cross)
    confirmations  6   volume   rvol >= 1.5
                       trend    close vs 200dma agrees with the trigger
                       relstr   63d relative strength rank >= .7 (bd: <= .3)
                       squeeze  vol rank <= .3 the day BEFORE (range was tight)
                       market   SPY vs its 200dma agrees with the trigger
                       close    close in the top (bd: bottom) quarter of the bar
    combos        22   per trigger: alone, each single, each pair
    horizons       3   5, 10, 20 sessions
    -> 264 trials. That N sets the bar a winner must clear (expected max of N
    null t-stats), and every one is written to the trial registry.

WHAT IS MEASURED, AND WHY EACH CHOICE

  Entry at the NEXT bar's open (features.py rule 2): the signal is only known
  at the close. Exit at the close `h` sessions later.
  EXCESS over the same-day cross-section average of the same horizon — so a
  breakout that merely rose with a rising tape scores zero, not a win.
  Breakdowns are scored with direction -1: success = UNDERperformance. This is
  a long-only book, so a breakdown's use is as an exit / do-not-buy signal.
  Net of round-trip cost (costs.py retail equity), reported again at 3x cost.
  t-stats on NON-OVERLAPPING blocks of h sessions (events in one block share a
  market move and overlapping windows; counting them separately is the
  748-signal mistake again).

TRAIN / TEST

  Discovery: bars through 2022-12-31 (minus a 20-session purge). Selection
  happens here only. Holdout: 2023-01-01 on, scored once, for survivors only.
  A combo that shines in discovery and dies in holdout is the expected outcome
  for most of them — that is the finding, not a failure of the run.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import statistics as st
from dataclasses import asdict, dataclass, field

from ..analytics.features import Row, panel
from ..config import ROOT
from ..data.ohlc import load
from .costs import CostModel
from .trials import Trial, expected_max_sharpe, record

OUT_PATH = ROOT / "data" / "research" / "breakout_study.json"

TRIGGERS = {"bo20": (20, +1), "bo55": (55, +1), "bd20": (20, -1), "bd55": (55, -1)}
CONFIRMS = ("volume", "trend", "relstr", "squeeze", "market", "close")
HORIZONS = (5, 10, 20)
DISCOVERY_END = "2022-12-31"
HOLDOUT_START = "2023-01-01"
PURGE_SESSIONS = 20
MIN_BLOCKS = 30             # fewer independent blocks than this is anecdote
HOLDOUT_T = 2.0


def combos() -> list[tuple[str, tuple[str, ...]]]:
    out = []
    for trig in TRIGGERS:
        for k in (0, 1, 2):
            for c in itertools.combinations(CONFIRMS, k):
                out.append((trig, c))
    return out


# --- events -----------------------------------------------------------------
@dataclass
class Event:
    symbol: str
    date: str
    trigger: str
    confirms: set[str] = field(default_factory=set)
    fwd: dict[int, float] = field(default_factory=dict)   # raw % from next open


def _market_regime() -> dict[str, int]:
    """{date: +1 if SPY above its 200dma else -1}, trailing only."""
    bars = load("SPY")
    out: dict[str, int] = {}
    closes = [b.close for b in bars]
    for i in range(199, len(bars)):
        sma = sum(closes[i - 199: i + 1]) / 200
        out[bars[i].date] = 1 if closes[i] > sma else -1
    return out


def detect(rows_by_symbol: dict[str, list[Row]], market: dict[str, int]) -> list[Event]:
    events: list[Event] = []
    for sym, rows in rows_by_symbol.items():
        bars = load(sym)
        if len(bars) != len(rows):
            continue                                   # index alignment is required
        for i in range(56, len(bars)):
            b, prev = bars[i], bars[i - 1]
            for trig, (n, d) in TRIGGERS.items():
                window, window_prev = bars[i - n: i], bars[i - n - 1: i - 1]
                if d > 0:
                    lvl, lvl_prev = max(x.high for x in window), max(x.high for x in window_prev)
                    fired = b.close > lvl and prev.close <= lvl_prev
                else:
                    lvl, lvl_prev = min(x.low for x in window), min(x.low for x in window_prev)
                    fired = b.close < lvl and prev.close >= lvl_prev
                if not fired:
                    continue
                f, fp = rows[i].features, rows[i - 1].features
                c: set[str] = set()
                if f.get("rvol", 0) >= 1.5:
                    c.add("volume")
                if "trend_200" in f and (f["trend_200"] > 0) == (d > 0):
                    c.add("trend")
                rs = f.get("rel_str_63_rank")
                if rs is not None and (rs >= 0.7 if d > 0 else rs <= 0.3):
                    c.add("relstr")
                if fp.get("vol_rank") is not None and fp["vol_rank"] <= 0.3:
                    c.add("squeeze")
                if market.get(b.date) == d:
                    c.add("market")
                rng = b.high - b.low
                if rng > 0:
                    pos = (b.close - b.low) / rng
                    if (pos >= 0.75 if d > 0 else pos <= 0.25):
                        c.add("close")
                events.append(Event(sym, b.date, trig, c, dict(rows[i].forward)))
    return events


# --- scoring ----------------------------------------------------------------
@dataclass
class Score:
    trigger: str
    confirms: tuple[str, ...]
    horizon: int
    n: int = 0
    blocks: int = 0
    mean_net: float = 0.0          # % per event, excess, direction-signed, net of cost
    mean_net_3x: float = 0.0
    hit: float = 0.0
    t: float = 0.0

    @property
    def name(self) -> str:
        return "+".join((self.trigger,) + self.confirms) + f"@{self.horizon}"


def _score(evs: list[Event], trig: str, conf: tuple[str, ...], h: int,
           xs_mean: dict[tuple[str, int], float], date_idx: dict[str, int],
           cost_pct: float) -> Score:
    d = TRIGGERS[trig][1]
    s = Score(trig, conf, h)
    vals: list[tuple[str, float]] = []
    for e in evs:
        if e.trigger != trig or h not in e.fwd or not set(conf) <= e.confirms:
            continue
        base = xs_mean.get((e.date, h))
        if base is None:
            continue
        vals.append((e.date, d * (e.fwd[h] - base)))
    s.n = len(vals)
    if not vals:
        return s
    # Cost applies to the trade actually taken: a long breakout pays it; a
    # breakdown used as an EXIT pays it too (it triggers a sale). Either way
    # it reduces the edge.
    net = [v - cost_pct for _, v in vals]
    s.mean_net = st.mean(net)
    s.mean_net_3x = st.mean(v - 3 * cost_pct for _, v in vals)
    s.hit = sum(1 for v in net if v > 0) / len(net)
    blocks: dict[int, list[float]] = {}
    for (dt, _), v in zip(vals, net):
        blocks.setdefault(date_idx[dt] // h, []).append(v)
    bm = [st.mean(v) for v in blocks.values()]
    s.blocks = len(bm)
    if len(bm) >= 3 and st.stdev(bm) > 0:
        s.t = st.mean(bm) / (st.stdev(bm) / math.sqrt(len(bm)))
    return s


def run(record_trials: bool = True) -> dict:
    rows = panel(horizons=HORIZONS)
    if not rows:
        return {"error": "no OHLC — run python -m app.data.ohlc --universe"}
    by_sym: dict[str, list[Row]] = {}
    for r in rows:
        by_sym.setdefault(r.symbol, []).append(r)

    # Same-day, same-horizon cross-section average: the "held the universe" bar.
    acc: dict[tuple[str, int], list[float]] = {}
    for r in rows:
        for h, v in r.forward.items():
            acc.setdefault((r.date, h), []).append(v)
    xs_mean = {k: st.mean(v) for k, v in acc.items() if len(v) >= 10}
    dates = sorted({r.date for r in rows})
    date_idx = {d: i for i, d in enumerate(dates)}

    events = detect(by_sym, _market_regime())
    purge_cut = dates[max(0, dates.index(max(d for d in dates if d <= DISCOVERY_END))
                          - PURGE_SESSIONS)]
    disc = [e for e in events if e.date <= purge_cut]
    hold = [e for e in events if e.date >= HOLDOUT_START]

    cost_pct = CostModel.retail_equity().round_trip_bps() / 100.0
    grid = [(t, c, h) for t, c in combos() for h in HORIZONS]
    n_trials = len(grid)
    bar = max(expected_max_sharpe(n_trials, 1.0), 2.0)   # E[max of N null t-stats]

    disc_scores = [_score(disc, t, c, h, xs_mean, date_idx, cost_pct) for t, c, h in grid]
    survivors = [s for s in disc_scores
                 if s.blocks >= MIN_BLOCKS and s.mean_net > 0 and s.t >= bar]
    hold_scores = {s.name: _score(hold, s.trigger, s.confirms, s.horizon,
                                  xs_mean, date_idx, cost_pct) for s in survivors}
    confirmed = [n for n, s in hold_scores.items()
                 if s.blocks >= MIN_BLOCKS // 2 and s.mean_net > 0 and s.t >= HOLDOUT_T]

    if record_trials:
        for s in disc_scores:
            record(Trial(strategy="breakout_study", sharpe=round(s.t, 3),
                         params={"trigger": s.trigger, "confirms": list(s.confirms),
                                 "horizon": s.horizon},
                         n_periods=s.blocks, window=f"..{purge_cut}",
                         note=f"t-stat on {s.horizon}-session blocks; mean_net {s.mean_net:.3f}%"))

    out = {
        "universe": len(by_sym), "rows": len(rows), "events": len(events),
        "discovery_events": len(disc), "holdout_events": len(hold),
        "discovery_window": f"{dates[0]}..{purge_cut}",
        "holdout_window": f"{HOLDOUT_START}..{dates[-1]}",
        "cost_round_trip_pct": cost_pct, "n_trials": n_trials,
        "selection_bar_t": round(bar, 2),
        "discovery": [asdict(s) | {"name": s.name} for s in
                      sorted(disc_scores, key=lambda s: -s.t)],
        "survivors": [s.name for s in survivors],
        "holdout": {n: asdict(s) for n, s in hold_scores.items()},
        "confirmed": confirmed,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def _row(s: dict) -> str:
    return (f"  {s['name']:<32} n={s['n']:>5} blk={s['blocks']:>4} "
            f"net={s['mean_net']:+7.3f}% 3x={s['mean_net_3x']:+7.3f}% "
            f"hit={100 * s['hit']:5.1f}% t={s['t']:+6.2f}")


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-record", action="store_true")
    ap.add_argument("--top", type=int, default=15)
    a = ap.parse_args()
    r = run(record_trials=not a.no_record)
    if "error" in r:
        print(r["error"])
        return
    print("=" * 96)
    print(f"BREAKOUT / BREAKDOWN STUDY — {r['universe']} names, {r['events']:,} events, "
          f"{r['n_trials']} trials")
    print(f"discovery {r['discovery_window']} ({r['discovery_events']:,} events)   "
          f"holdout {r['holdout_window']} ({r['holdout_events']:,} events)")
    print(f"cost {r['cost_round_trip_pct']:.2f}% round trip   selection bar t >= "
          f"{r['selection_bar_t']} (expected best of {r['n_trials']} null trials)")
    print("=" * 96)
    print(f"\nTop {a.top} in DISCOVERY (excess vs same-day universe, direction-signed, net):")
    for s in r["discovery"][:a.top]:
        print(_row(s))
    print(f"\nBottom 5 in DISCOVERY (for contrast):")
    for s in r["discovery"][-5:]:
        print(_row(s))
    print(f"\nSurvived discovery ({len(r['survivors'])}): {', '.join(r['survivors']) or 'none'}")
    if r["holdout"]:
        print("\nHOLDOUT (never seen by selection):")
        for n, s in r["holdout"].items():
            print(_row(s | {"name": n}))
    print(f"\nCONFIRMED out of sample: {', '.join(r['confirmed']) or 'none'}")
    print(f"\nSaved: {OUT_PATH}")


if __name__ == "__main__":
    _main()
