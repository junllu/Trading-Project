"""Portfolio simulation of rev1 — does it beat holding SPY, as a portfolio?

    python -m app.backtest.portfolio_sim

WHY THIS IS THE QUESTION

v4 proved a per-TRADE edge over random entries. That is not the same as a
portfolio that beats the index: positions overlap, capital sits in cash
between signals, and losses compound. Every earlier version of this system
lost to buy-and-hold; this is the test that would have caught that. It also
produces the sleeve drawdown the capital gate requires and never had.

FIXED BEFORE THE RUN (committed with this file, 2026-09-27)

  capital        $100,000 start; no leverage; idle cash earns 0% (conservative)
  sizing         2% of equity (prior close) per position; max 40 open
  selection      when signals exceed free slots, a seeded random draw (seed
                 20260927) — never "the best-looking", which would be fitting
  rules          rev1 exactly: S1 = bd55 + weak relstr + SPY<200dma, hold 5;
                 S2 = bd20, stop -2 ATR / target +3 ATR / 20 sessions; next-open
                 entry; exits from entry_exit_study.simulate(); one position
                 per name; retail round-trip cost at exit
  configs        S1 alone, S2 alone, S1+S2 (3 trials, recorded)
  window         2016-01-04 .. latest; eras split at 2023-01-01
  benchmark      SPY buy-and-hold, fully invested, same window
  universe       the 533 names (current S&P 500 list: survivorship flatters
                 dip-buying; the forward paper test has no such bias)
"""
from __future__ import annotations

import json
import math
import random
import statistics as st
from dataclasses import asdict, dataclass

from ..config import ROOT
from ..data.ohlc import load
from .breakout_regime_study import PREREG, events_for
from .costs import CostModel
from .entry_exit_study import simulate
from .trials import Trial, record

OUT_PATH = ROOT / "data" / "research" / "portfolio_sim.json"
START = "2016-01-04"
ERA_SPLIT = "2023-01-01"
CAPITAL = 100_000.0
POS_FRAC = 0.02
MAX_POS = 40
SEED = 20260927
STRATS = {"S1": ("bd55", {"relstr", "market"}, "X1"), "S2": ("bd20", set(), "X3")}
CONFIGS = {"S1": ("S1",), "S2": ("S2",), "S1+S2": ("S1", "S2")}


@dataclass
class Cand:
    strat: str
    symbol: str
    entry_date: str
    exit_date: str
    entry_px: float
    gross: float


def candidates(cost: float) -> tuple[list[Cand], dict[str, dict[str, float]]]:
    """Every rev1 signal as a would-be trade, plus closes for marking."""
    uni = json.loads(PREREG.read_text("utf-8"))
    out: list[Cand] = []
    closes: dict[str, dict[str, float]] = {}
    for group in (uni["existing_study"] + uni["new_study"], uni["holdout_names"]):
        evs, *_ = events_for(group)
        by_sym: dict[str, list] = {}
        for e in evs:
            by_sym.setdefault(e.symbol, []).append(e)
        del evs
        for sym in group:
            bars = load(sym)
            if len(bars) < 300:
                continue
            idx = {b.date: n for n, b in enumerate(bars)}
            used = False
            for e in by_sym.get(sym, []):
                for sname, (trig, need, x) in STRATS.items():
                    if e.trigger != trig or not need <= e.confirms:
                        continue
                    i = idx.get(e.date)
                    if i is None or i + 1 >= len(bars) or bars[i + 1].date < START:
                        continue
                    r = simulate(bars, i, x)
                    if r is None:
                        continue
                    gross, _hold, j = r
                    out.append(Cand(sname, sym, bars[i + 1].date, bars[j].date,
                                    bars[i + 1].open, gross))
                    used = True
            if used:
                closes[sym] = {b.date: b.close for b in bars if b.date >= START}
    return out, closes


def simulate_portfolio(cands: list[Cand], closes: dict[str, dict[str, float]],
                       dates: list[str], strats: tuple[str, ...], cost: float) -> dict:
    rng = random.Random(SEED)
    by_entry: dict[str, list[Cand]] = {}
    for c in cands:
        if c.strat in strats:
            by_entry.setdefault(c.entry_date, []).append(c)
    cash, equity = CAPITAL, CAPITAL
    open_pos: list[tuple[Cand, float]] = []          # (cand, $ size)
    curve, exposure, trades = [], [], []
    last_px: dict[str, float] = {}
    for d in dates:
        # 1. entries at the open, sized on the prior close's equity
        todays = [c for c in by_entry.get(d, [])
                  if c.symbol not in {p.symbol for p, _ in open_pos}]
        rng.shuffle(todays)
        seen: set[str] = set()
        for c in todays:
            if len(open_pos) >= MAX_POS or c.symbol in seen:
                continue
            size = POS_FRAC * equity
            if cash < size:
                break
            cash -= size
            open_pos.append((c, size))
            seen.add(c.symbol)
        # 2. exits (stop/target/time — the exit bar is fixed by simulate())
        keep = []
        for c, size in open_pos:
            if c.exit_date == d:
                pnl_pct = c.gross - cost
                cash += size * (1 + pnl_pct / 100)
                trades.append(pnl_pct)
            else:
                keep.append((c, size))
        open_pos = keep
        # 3. mark to the close
        mv = 0.0
        for c, size in open_pos:
            px = closes.get(c.symbol, {}).get(d) or last_px.get(c.symbol) or c.entry_px
            last_px[c.symbol] = px
            mv += size * px / c.entry_px
        equity = cash + mv
        curve.append((d, equity))
        exposure.append(mv / equity if equity > 0 else 0.0)
    return {"curve": curve, "exposure": st.mean(exposure) if exposure else 0.0,
            "trades": len(trades),
            "win_rate": (sum(1 for t in trades if t > 0) / len(trades)) if trades else 0.0}


def metrics(curve: list[tuple[str, float]], lo: str = "", hi: str = "9999") -> dict:
    pts = [(d, v) for d, v in curve if lo <= d < hi]
    if len(pts) < 20:
        return {}
    vals = [v for _, v in pts]
    rets = [vals[i] / vals[i - 1] - 1 for i in range(1, len(vals))]
    years = len(vals) / 252
    cagr = (vals[-1] / vals[0]) ** (1 / years) - 1
    sd = st.pstdev(rets)
    sharpe = (st.mean(rets) / sd * math.sqrt(252)) if sd > 0 else 0.0
    peak, mdd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        mdd = max(mdd, 1 - v / peak)
    return {"cagr_pct": round(100 * cagr, 2), "vol_pct": round(100 * sd * math.sqrt(252), 2),
            "sharpe": round(sharpe, 2), "max_dd_pct": round(100 * mdd, 2),
            "from": pts[0][0], "to": pts[-1][0]}


def run(record_trials: bool = True) -> dict:
    cost = CostModel.retail_equity().round_trip_bps() / 100.0
    cands, closes = candidates(cost)
    spy = [b for b in load("SPY") if b.date >= START]
    dates = [b.date for b in spy]
    spy_curve = [(b.date, CAPITAL * b.close / spy[0].close) for b in spy]
    out = {"settings": {"capital": CAPITAL, "pos_frac": POS_FRAC, "max_pos": MAX_POS,
                        "seed": SEED, "start": START, "cost_pct": cost},
           "candidates": {s: sum(1 for c in cands if c.strat == s) for s in STRATS},
           "spy": {"full": metrics(spy_curve), "pre2023": metrics(spy_curve, hi=ERA_SPLIT),
                   "from2023": metrics(spy_curve, lo=ERA_SPLIT)},
           "configs": {}}
    for name, strats in CONFIGS.items():
        r = simulate_portfolio(cands, closes, dates, strats, cost)
        m = {"full": metrics(r["curve"]), "pre2023": metrics(r["curve"], hi=ERA_SPLIT),
             "from2023": metrics(r["curve"], lo=ERA_SPLIT),
             "avg_exposure_pct": round(100 * r["exposure"], 1), "trades": r["trades"],
             "win_rate": round(r["win_rate"], 3),
             "final_equity": round(r["curve"][-1][1], 0)}
        out["configs"][name] = m
        if record_trials:
            record(Trial(strategy="rev1_portfolio", sharpe=m["full"].get("sharpe", 0.0),
                         cagr=m["full"].get("cagr_pct", 0.0) / 100,
                         max_drawdown=m["full"].get("max_dd_pct", 0.0) / 100,
                         params={"config": name, "pos_frac": POS_FRAC, "max_pos": MAX_POS},
                         n_periods=len(r["curve"]), window=f"{START}..{dates[-1]}",
                         note="portfolio sim of rev1 rules; seeded random selection"))
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def _main() -> None:
    r = run()
    s = r["settings"]
    print("=" * 96)
    print(f"rev1 PORTFOLIO — ${s['capital']:,.0f}, {100 * s['pos_frac']:.0f}% per position, max "
          f"{s['max_pos']} open, idle cash 0%, since {s['start']}")
    print(f"signals available: {r['candidates']}")
    print("=" * 96)
    print(f"{'':<8}{'window':<10}{'CAGR':>8}{'vol':>8}{'Sharpe':>8}{'maxDD':>8}")

    def line(name, window, m):
        if m:
            print(f"{name:<8}{window:<10}{m['cagr_pct']:>7.1f}%{m['vol_pct']:>7.1f}%{m['sharpe']:>8.2f}"
                  f"{m['max_dd_pct']:>7.1f}%")
    for w in ("full", "pre2023", "from2023"):
        line("SPY", w, r["spy"][w])
    for name, m in r["configs"].items():
        print(f"-- {name}: {m['trades']} trades, win {100 * m['win_rate']:.1f}%, "
              f"avg invested {m['avg_exposure_pct']}%, final ${m['final_equity']:,.0f}")
        for w in ("full", "pre2023", "from2023"):
            line(name, w, m[w])


if __name__ == "__main__":
    _main()
