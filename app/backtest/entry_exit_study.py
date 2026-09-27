"""Entry × exit, trade by trade — win rate, payoff, expectancy, and edge.

    python -m app.backtest.entry_exit_study

Implements docs/prereg/2026-09-27-entry-exit-v4.md exactly.

WHY A WIN RATE IS NOT A RESULT

A tight target and a wide stop win often and lose big. An 80% win rate with a
0.2 payoff ratio loses money. So every combo is reported with the full
scorecard — win rate, average win and loss, payoff, expectancy, profit factor,
losing streak — and judged on expectancy and on EDGE over a control that uses
the SAME exit with no entry signal. Without the control, a trailing stop that
merely rides a rising market would look like a brilliant entry.

DAILY BARS AND INTRABAR ORDER

A daily bar does not say whether the high or the low came first. When one bar
touches both the stop and the target the stop is assumed first — the
conservative reading. Stops that gap through fill at the open, not the stop.
"""
from __future__ import annotations

import json
import math
import statistics as st
from dataclasses import asdict, dataclass, field

from ..analytics.features import _atr
from ..config import ROOT
from ..data.ohlc import Bar, load
from .breakout_regime_study import PREREG, events_for
from .costs import CostModel
from .trials import Trial, record

OUT_PATH = ROOT / "data" / "research" / "entry_exit_study.json"

ENTRIES = {
    "E1": ("bo55", {"volume", "close"}),
    "E2": ("bd55", {"relstr", "market"}),
    "E3": ("bd20", set()),
}
CONTROL_EVERY = 20
EXITS = ("X1", "X2", "X3", "X4", "X5")
EXIT_DESC = {
    "X1": "time 5", "X2": "time 20", "X3": "stop -2ATR / target +3ATR / 20",
    "X4": "trail 3ATR from high close / 120", "X5": "close < prior 20d low / 120",
}
PASS_T = 2.71
ERA_SPLIT = "2023-01-01"


@dataclass
class TradeResult:
    symbol: str
    entry_date: str
    ret: float              # % net of cost
    hold: int               # sessions


def simulate(bars: list[Bar], i: int, exit_id: str) -> tuple[float, int, int] | None:
    """Enter at bars[i+1].open. Returns (gross %, hold sessions, exit bar index)."""
    k = i + 1
    if k >= len(bars):
        return None
    e = bars[k].open
    atr = _atr(bars, i)
    if e <= 0 or atr <= 0:
        return None

    def done(px: float, j: int) -> tuple[float, int, int]:
        return (px / e - 1) * 100, j - k + 1, j

    if exit_id in ("X1", "X2"):
        j = k + (4 if exit_id == "X1" else 19)
        return done(bars[j].close, j) if j < len(bars) else None

    if exit_id == "X3":
        stop, tgt = e - 2 * atr, e + 3 * atr
        for j in range(k, min(k + 20, len(bars))):
            b = bars[j]
            if b.low <= stop:
                return done(min(b.open, stop), j)
            if b.high >= tgt:
                return done(max(b.open, tgt), j)
        j = k + 19
        return done(bars[j].close, j) if j < len(bars) else None

    if exit_id == "X4":
        stop, hc = e - 3 * atr, e
        for j in range(k, min(k + 120, len(bars))):
            b = bars[j]
            if b.low <= stop:
                return done(min(b.open, stop), j)
            hc = max(hc, b.close)
            stop = max(stop, hc - 3 * atr)        # ratchets up only, known at close
        j = k + 119
        return done(bars[j].close, j) if j < len(bars) else None

    if exit_id == "X5":
        for j in range(max(k, 20), min(k + 120, len(bars))):
            if bars[j].close < min(x.low for x in bars[j - 20: j]):
                if j + 1 >= len(bars):
                    return None
                return done(bars[j + 1].open, j + 1)   # can't trade the observed close
        j = k + 119
        return done(bars[j].close, j) if j < len(bars) else None
    raise ValueError(exit_id)


def run_universe(symbols: list[str], cost: float) -> dict[tuple[str, str], list[TradeResult]]:
    evs, *_ = events_for(symbols)
    sig: dict[str, dict[str, list[str]]] = {}
    for e in evs:
        for eid, (trig, need) in ENTRIES.items():
            if e.trigger == trig and need <= e.confirms:
                sig.setdefault(e.symbol, {}).setdefault(eid, []).append(e.date)
    del evs
    trades: dict[tuple[str, str], list[TradeResult]] = {}
    for sym in symbols:
        bars = load(sym)
        if len(bars) < 300:
            continue
        idx = {b.date: n for n, b in enumerate(bars)}
        entries = {eid: sorted(idx[d] for d in ds if d in idx)
                   for eid, ds in sig.get(sym, {}).items()}
        entries["E0"] = list(range(60, len(bars) - 1, CONTROL_EVERY))
        for eid, points in entries.items():
            for x in EXITS:
                busy = -1
                out = trades.setdefault((eid, x), [])
                for i in points:
                    if i <= busy:
                        continue                   # one open position per name
                    r = simulate(bars, i, x)
                    if r is None:
                        continue
                    gross, hold, j = r
                    busy = j
                    out.append(TradeResult(sym, bars[i + 1].date, gross - cost, hold))
    return trades


@dataclass
class Card:
    entry: str
    exit: str
    trades: int = 0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    payoff: float = 0.0
    expectancy: float = 0.0
    expectancy_3x: float = 0.0
    profit_factor: float = 0.0
    median_hold: float = 0.0
    exp_per_day: float = 0.0
    max_losing_streak: int = 0
    edge: float = 0.0              # vs control, same exit, mean monthly difference
    edge_t: float = 0.0
    edge_months: int = 0
    edge_pre2023: float = 0.0
    edge_2023: float = 0.0
    passed: bool = False
    notes: list[str] = field(default_factory=list)


def _monthly_edge(tr: list[TradeResult], ctl: list[TradeResult],
                  lo: str = "", hi: str = "9999") -> tuple[float, float, int]:
    def by_month(ts):
        m: dict[str, list[float]] = {}
        for t in ts:
            if lo <= t.entry_date < hi:
                m.setdefault(t.entry_date[:7], []).append(t.ret)
        return {k: st.mean(v) for k, v in m.items()}
    a, c = by_month(tr), by_month(ctl)
    diffs = [a[k] - c[k] for k in a if k in c]
    if len(diffs) < 3 or st.stdev(diffs) == 0:
        return (st.mean(diffs) if diffs else 0.0), 0.0, len(diffs)
    return st.mean(diffs), st.mean(diffs) / (st.stdev(diffs) / math.sqrt(len(diffs))), len(diffs)


def card(eid: str, x: str, tr: list[TradeResult], ctl: list[TradeResult], cost: float) -> Card:
    c = Card(eid, x, trades=len(tr))
    if not tr:
        return c
    rets = [t.ret for t in tr]
    wins, losses = [r for r in rets if r > 0], [r for r in rets if r <= 0]
    c.win_rate = len(wins) / len(rets)
    c.avg_win = st.mean(wins) if wins else 0.0
    c.avg_loss = st.mean(losses) if losses else 0.0
    c.payoff = abs(c.avg_win / c.avg_loss) if c.avg_loss else float("inf")
    c.expectancy = st.mean(rets)
    c.expectancy_3x = c.expectancy - 2 * cost
    gl = -sum(losses)
    c.profit_factor = sum(wins) / gl if gl > 0 else float("inf")
    c.median_hold = st.median(t.hold for t in tr)
    c.exp_per_day = c.expectancy / max(st.mean(t.hold for t in tr), 1)
    streak = worst = 0
    for t in sorted(tr, key=lambda t: t.entry_date):
        streak = streak + 1 if t.ret <= 0 else 0
        worst = max(worst, streak)
    c.max_losing_streak = worst
    if eid != "E0":
        c.edge, c.edge_t, c.edge_months = _monthly_edge(tr, ctl)
        c.edge_pre2023 = _monthly_edge(tr, ctl, hi=ERA_SPLIT)[0]
        c.edge_2023 = _monthly_edge(tr, ctl, lo=ERA_SPLIT)[0]
        c.passed = (c.expectancy > 0 and c.expectancy_3x > 0 and c.edge > 0
                    and c.edge_t >= PASS_T and c.edge_pre2023 > 0 and c.edge_2023 > 0)
    return c


def run(record_trials: bool = True) -> dict:
    uni = json.loads(PREREG.read_text("utf-8"))
    cost = CostModel.retail_equity().round_trip_bps() / 100.0
    trades: dict[tuple[str, str], list[TradeResult]] = {}
    for group in (uni["existing_study"] + uni["new_study"], uni["holdout_names"]):
        for k, v in run_universe(group, cost).items():
            trades.setdefault(k, []).extend(v)
    cards = []
    for eid in ("E0", "E1", "E2", "E3"):
        for x in EXITS:
            cards.append(card(eid, x, trades.get((eid, x), []), trades.get(("E0", x), []), cost))
    if record_trials:
        for c in cards:
            if c.entry == "E0":
                continue
            record(Trial(strategy="entry_exit_v4", sharpe=round(c.edge_t, 3),
                         params={"entry": c.entry, "exit": c.exit},
                         n_periods=c.edge_months, window="2015..2026 all 533 names",
                         note=f"prereg entry-exit-v4; exp {c.expectancy:.3f}%; passed={c.passed}"))
    out = {"prereg": "docs/prereg/2026-09-27-entry-exit-v4.md", "cost_pct": cost,
           "pass_t": PASS_T, "exits": EXIT_DESC,
           "cards": [asdict(c) for c in cards],
           "passed": [f"{c.entry}/{c.exit}" for c in cards if c.passed]}
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    return out


def _main() -> None:
    r = run()
    print("=" * 118)
    print(f"ENTRY x EXIT v4 — trade scorecard (net of {r['cost_pct']:.2f}% round trip)   "
          f"pass: edge t >= {r['pass_t']}, both eras, positive at 3x cost")
    print("=" * 118)
    print(f"{'combo':<8}{'exit':<34}{'trades':>7}{'win%':>6}{'avgW':>7}{'avgL':>7}{'payoff':>7}"
          f"{'exp%':>7}{'PF':>6}{'hold':>5}{'streak':>7}{'edge':>7}{'t':>6}{'<23':>7}{'23+':>7}")
    for c in r["cards"]:
        print(f"{c['entry'] + '/' + c['exit']:<8}{r['exits'][c['exit']]:<34}{c['trades']:>7}"
              f"{100 * c['win_rate']:>6.1f}{c['avg_win']:>7.2f}{c['avg_loss']:>7.2f}"
              f"{c['payoff']:>7.2f}{c['expectancy']:>7.2f}{c['profit_factor']:>6.2f}"
              f"{c['median_hold']:>5.0f}{c['max_losing_streak']:>7}"
              + ("" if c["entry"] == "E0" else
                 f"{c['edge']:>7.2f}{c['edge_t']:>6.2f}{c['edge_pre2023']:>7.2f}{c['edge_2023']:>7.2f}"
                 + ("  PASS" if c["passed"] else "")))
    print(f"\nE0 = control (no signal). PASSED: {', '.join(r['passed']) or 'none'}")


if __name__ == "__main__":
    _main()
