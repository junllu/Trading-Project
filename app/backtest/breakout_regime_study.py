"""Breakout study v2 — confirmations AND regime, current era first.

    python -m app.backtest.breakout_regime_study

Implements docs/prereg/2026-09-27-breakout-regime.md exactly; read that first.
Anything here that disagrees with the pre-registration is a bug in this file,
not a change of plan.

WHY REGIMES ARE CONDITIONS, NOT ERAS

"2023 is different" is true and is also a hindsight label — the algorithm could
not have known it in 2023. The macro timeline in this repo failed that way
(`ai_boom 2023-2027` was written knowing the boom happened). So the environment
enters as conditions read from data available at that day's close — VIX,
credit (HYG/IEF), rates (10y, with its publication lag), the presidential
cycle — and the era split is used only to demand that an edge exist in BOTH
the current regime and the one before it. A strategy that works only
2023-2026 is precisely the one that fails when the regime turns, and the
thesis this campaign is built on expects it to turn in 2028.
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import statistics as st
from dataclasses import asdict
from datetime import date

from ..analytics.features import Row, panel
from ..config import ROOT
from ..data.ohlc import load
from ..macro.signals import cycle_year
from .breakout_study import (HORIZONS, TRIGGERS, Event, _market_regime, _row,
                             _score, detect)
from .costs import CostModel
from .trials import Trial, expected_max_sharpe, record

PREREG = ROOT / "docs" / "prereg" / "2026-09-27-breakout-regime-universe.json"
OUT_PATH = ROOT / "data" / "research" / "breakout_regime_study.json"

BASE_CONFIRMS = ("volume", "trend", "relstr", "squeeze", "market", "close")
REGIMES = ("vix", "credit", "rates", "cycle")
CONDITIONS = BASE_CONFIRMS + REGIMES
ERA_A_START = "2023-01-01"
ERA_B_END = "2022-12-31"
PURGE_SESSIONS = 20
MIN_BLOCKS = 30
CONFIRM_T = 2.0
LOOKBACK = 63


def combos() -> list[tuple[str, tuple[str, ...]]]:
    return [(t, c) for t in TRIGGERS for k in (0, 1, 2)
            for c in itertools.combinations(CONDITIONS, k)]


# --- regime flags: +1 risk-on, -1 risk-off, per date, knowable at the close --
def _closes(sym: str) -> dict[str, float]:
    return {b.date: b.close for b in load(sym)}


def _fred(sid: str) -> list[tuple[str, float]]:
    out = []
    p = ROOT / "data" / "macro_series" / f"{sid}.csv"
    for row in csv.reader(p.read_text("utf-8").splitlines()[1:]):
        try:
            out.append((row[0], float(row[1])))
        except (ValueError, IndexError):
            continue                          # FRED writes '.' for holidays
    return out


def regime_flags() -> dict[str, dict[str, int]]:
    spy_dates = [b.date for b in load("SPY")]
    vix, hyg, ief = _closes("^VIX"), _closes("HYG"), _closes("IEF")
    dgs = _fred("DGS10")
    flags: dict[str, dict[str, int]] = {}
    j = 0                                      # pointer into DGS10 (strictly before date)
    prior_yield: list[float | None] = []
    for d in spy_dates:
        while j < len(dgs) and dgs[j][0] < d:  # publication lag: only earlier prints
            j += 1
        prior_yield.append(dgs[j - 1][1] if j else None)
    vix_hist: list[float] = []
    for i, d in enumerate(spy_dates):
        f: dict[str, int] = {}
        if d in vix:
            vix_hist.append(vix[d])
            window = vix_hist[-252:]
            if len(window) >= 60:
                f["vix"] = 1 if vix[d] < st.median(window) else -1
        if i >= LOOKBACK:
            d0 = spy_dates[i - LOOKBACK]
            if all(k in hyg and k in ief for k in (d, d0)):
                f["credit"] = 1 if hyg[d] / ief[d] > hyg[d0] / ief[d0] else -1
            y, y0 = prior_yield[i], prior_yield[i - LOOKBACK]
            if y is not None and y0 is not None:
                f["rates"] = 1 if y < y0 else -1
        f["cycle"] = 1 if cycle_year(date.fromisoformat(d)) == 3 else -1
        flags[d] = f
    return flags


def events_for(symbols: list[str]) -> tuple[list[Event], dict, dict, list[str]]:
    rows = panel(symbols, horizons=HORIZONS)
    by_sym: dict[str, list[Row]] = {}
    for r in rows:
        by_sym.setdefault(r.symbol, []).append(r)
    acc: dict[tuple[str, int], list[float]] = {}
    for r in rows:
        for h, v in r.forward.items():
            acc.setdefault((r.date, h), []).append(v)
    xs_mean = {k: st.mean(v) for k, v in acc.items() if len(v) >= 10}
    dates = sorted({r.date for r in rows})
    date_idx = {d: i for i, d in enumerate(dates)}
    evs = detect(by_sym, _market_regime())
    del rows, by_sym, acc
    flags = regime_flags()
    for e in evs:
        d = TRIGGERS[e.trigger][1]
        for k, v in flags.get(e.date, {}).items():
            if v == d:
                e.confirms.add(k)
    return evs, xs_mean, date_idx, dates


def run(record_trials: bool = True) -> dict:
    uni = json.loads(PREREG.read_text("utf-8"))
    study = uni["existing_study"] + uni["new_study"]
    cost = CostModel.retail_equity().round_trip_bps() / 100.0
    grid = [(t, c, h) for t, c in combos() for h in HORIZONS]
    n_trials = len(grid)
    bar = max(expected_max_sharpe(n_trials, 1.0), 2.0)

    evs, xs, didx, dates = events_for(study)
    era_a = [e for e in evs if e.date >= ERA_A_START]
    last_b = max(d for d in dates if d <= ERA_B_END)
    cut_b = dates[dates.index(last_b) - PURGE_SESSIONS]
    era_b = [e for e in evs if e.date <= cut_b]

    # Step 1 — the current regime, where selection happens.
    a_scores = [_score(era_a, t, c, h, xs, didx, cost) for t, c, h in grid]
    step1 = [s for s in a_scores if s.blocks >= MIN_BLOCKS and s.mean_net > 0 and s.t >= bar]
    # Step 2 — the prior regime, survivors only.
    b_scores = {s.name: _score(era_b, s.trigger, s.confirms, s.horizon, xs, didx, cost)
                for s in step1}
    step2 = [s for s in step1 if b_scores[s.name].blocks >= MIN_BLOCKS
             and b_scores[s.name].mean_net > 0 and b_scores[s.name].t >= CONFIRM_T]
    # Step 3 — names never examined, full period, survivors only.
    h_scores: dict[str, dict] = {}
    step3: list[str] = []
    if step2:
        hevs, hxs, hdidx, _ = events_for(uni["holdout_names"])
        for s in step2:
            hs = _score(hevs, s.trigger, s.confirms, s.horizon, hxs, hdidx, cost)
            h_scores[s.name] = asdict(hs)
            if hs.mean_net > 0 and hs.t >= CONFIRM_T:
                step3.append(s.name)

    if record_trials:
        for s in a_scores:
            record(Trial(strategy="breakout_regime_v2", sharpe=round(s.t, 3),
                         params={"trigger": s.trigger, "conditions": list(s.confirms),
                                 "horizon": s.horizon},
                         n_periods=s.blocks, window=f"{ERA_A_START}..{dates[-1]}",
                         note=f"era A t on {s.horizon}-session blocks; mean_net {s.mean_net:.3f}%"))

    out = {
        "prereg": "docs/prereg/2026-09-27-breakout-regime.md",
        "study_names": len(study), "events": len(evs),
        "era_a_events": len(era_a), "era_b_events": len(era_b),
        "era_a": f"{ERA_A_START}..{dates[-1]}", "era_b": f"{dates[0]}..{cut_b}",
        "n_trials": n_trials, "selection_bar_t": round(bar, 2), "cost_pct": cost,
        "era_a_ranked": [asdict(s) | {"name": s.name} for s in sorted(a_scores, key=lambda s: -s.t)],
        "step1": [s.name for s in step1],
        "step2_era_b": {n: asdict(s) for n, s in b_scores.items()},
        "step2": [s.name for s in step2],
        "step3_holdout_names": h_scores,
        "passed_all": step3,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-record", action="store_true")
    ap.add_argument("--top", type=int, default=15)
    a = ap.parse_args()
    r = run(record_trials=not a.no_record)
    print("=" * 100)
    print(f"BREAKOUT x REGIME v2 — {r['study_names']} names, {r['events']:,} events, "
          f"{r['n_trials']} trials (pre-registered)")
    print(f"era A {r['era_a']} ({r['era_a_events']:,})   era B {r['era_b']} ({r['era_b_events']:,})")
    print(f"selection bar t >= {r['selection_bar_t']}   cost {r['cost_pct']:.2f}% round trip")
    print("=" * 100)
    print(f"\nTop {a.top} in ERA A (current regime):")
    for s in r["era_a_ranked"][:a.top]:
        print(_row(s))
    print(f"\nStep 1 passed ({len(r['step1'])}): {', '.join(r['step1']) or 'none'}")
    for n, s in r["step2_era_b"].items():
        print("  era B " + _row(s | {"name": n}))
    print(f"Step 2 passed ({len(r['step2'])}): {', '.join(r['step2']) or 'none'}")
    for n, s in r["step3_holdout_names"].items():
        print("  holdout names " + _row(s | {"name": n}))
    print(f"\nPASSED ALL THREE: {', '.join(r['passed_all']) or 'none'}")
    print(f"Saved: {OUT_PATH}")


if __name__ == "__main__":
    _main()
