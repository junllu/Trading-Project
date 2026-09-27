"""rev2 — S2 dip buys on top of an invested base (SPY or sector rotation).

    python -m app.backtest.rev2_sim

Implements docs/prereg/2026-09-27-rev2-overlay-sector.md. Kept separate from
portfolio_sim.py and entry_exit_study.py so nothing rev1's paper engine
imports changes.

IDLE-CAPITAL ACCOUNTING (a simplification, stated)

Entries sell idle capital at the prior close's value; exit proceeds rejoin it
after that day's idle return. The overnight gap on the slice being moved is
ignored — small, and symmetric between entries and exits.
"""
from __future__ import annotations

import json
import math
import random
import statistics as st

from ..config import ROOT
from ..data.ohlc import Bar, load
from ..data.sectors import ETF, sector_etf
from .breakout_regime_study import PREREG, events_for
from .costs import CostModel
from .entry_exit_study import simulate
from .portfolio_sim import CAPITAL, ERA_SPLIT, MAX_POS, POS_FRAC, SEED, START, metrics
from .trials import Trial, record

OUT_PATH = ROOT / "data" / "research" / "rev2_sim.json"
MOM = 126
REBAL = 21
TOP_SECTORS_FILTER = 5
TOP_SECTORS_HOLD = 3

CONFIGS = {
    "A": {"idle": "SPY", "exit": "X3", "sector_filter": False},
    "B": {"idle": "SPY", "exit": "X6", "sector_filter": False},
    "C": {"idle": "SPY", "exit": "X3", "sector_filter": True},
    "D": {"idle": "ROT", "exit": "X3", "sector_filter": False},
}


def exit_x6(bars: list[Bar], i: int):
    """Enter next open; exit the open after the first close above its 5-session
    SMA (can't trade the close you observe); time stop at the 10th close."""
    k = i + 1
    if k >= len(bars) or bars[k].open <= 0:
        return None
    e = bars[k].open
    for j in range(k, min(k + 10, len(bars))):
        if j >= 4 and bars[j].close > sum(b.close for b in bars[j - 4: j + 1]) / 5:
            if j + 1 >= len(bars):
                return None
            return (bars[j + 1].open / e - 1) * 100, j + 1 - k + 1, j + 1
    j = k + 9
    return ((bars[j].close / e - 1) * 100, 10, j) if j < len(bars) else None


def sector_ranks(dates: list[str]) -> dict[str, dict[str, int]]:
    """{date: {etf: rank}} by trailing 126-session return, 0 = strongest."""
    series = {etf: {b.date: b.close for b in load(etf)} for etf in ETF.values()}
    out: dict[str, dict[str, int]] = {}
    for n, d in enumerate(dates):
        if n < MOM:
            continue
        d0 = dates[n - MOM]
        rets = {etf: s[d] / s[d0] - 1 for etf, s in series.items() if d in s and d0 in s}
        order = sorted(rets, key=rets.get, reverse=True)
        out[d] = {etf: r for r, etf in enumerate(order)}
    return out


def candidates(dates: list[str], ranks: dict) -> list[dict]:
    uni = json.loads(PREREG.read_text("utf-8"))
    out = []
    date_pos = {d: n for n, d in enumerate(dates)}
    for group in (uni["existing_study"] + uni["new_study"], uni["holdout_names"]):
        evs, *_ = events_for(group)
        by_sym: dict[str, list] = {}
        for e in evs:
            if e.trigger == "bd20":
                by_sym.setdefault(e.symbol, []).append(e.date)
        del evs
        for sym in group:
            bars = load(sym)
            if len(bars) < 300:
                continue
            idx = {b.date: n for n, b in enumerate(bars)}
            etf = sector_etf(sym)
            for d in by_sym.get(sym, []):
                i = idx.get(d)
                if i is None or i + 1 >= len(bars) or bars[i + 1].date < START:
                    continue
                rk = ranks.get(d, {}).get(etf) if etf else None
                c = {"symbol": sym, "entry_date": bars[i + 1].date, "entry_px": bars[i + 1].open,
                     "strong_sector": rk is not None and rk < TOP_SECTORS_FILTER}
                for x, fn in (("X3", lambda: simulate(bars, i, "X3")), ("X6", lambda: exit_x6(bars, i))):
                    r = fn()
                    if r is not None and bars[r[2]].date in date_pos:
                        c[x] = (r[0], bars[r[2]].date)
                out.append(c)
    return out


def run_config(cfg: dict, cands: list[dict], closes: dict, dates: list[str],
               idle_ret: dict[str, float], rot_cost: dict[str, float], cost: float) -> dict:
    rng = random.Random(SEED)
    one_way = cost / 2
    by_entry: dict[str, list[dict]] = {}
    for c in cands:
        if cfg["exit"] in c and (not cfg["sector_filter"] or c["strong_sector"]):
            by_entry.setdefault(c["entry_date"], []).append(c)
    idle, equity = CAPITAL, CAPITAL
    pos: list[tuple[dict, float]] = []
    curve, invested, n_tr = [], [], 0
    last: dict[str, float] = {}
    for d in dates:
        held = {c["symbol"] for c, _ in pos}
        todays = [c for c in by_entry.get(d, []) if c["symbol"] not in held]
        rng.shuffle(todays)
        for c in todays:
            if len(pos) >= MAX_POS or c["symbol"] in held:
                continue
            size = POS_FRAC * equity
            if idle < size * (1 + one_way / 100):
                break
            idle -= size * (1 + one_way / 100)
            pos.append((c, size))
            held.add(c["symbol"])
        idle *= 1 + idle_ret.get(d, 0.0) - rot_cost.get(d, 0.0)
        keep = []
        for c, size in pos:
            gross, xdate = c[cfg["exit"]]
            if xdate == d:
                idle += size * (1 + (gross - cost) / 100) * (1 - one_way / 100)
                n_tr += 1
            else:
                keep.append((c, size))
        pos = keep
        mv = 0.0
        for c, size in pos:
            px = closes.get(c["symbol"], {}).get(d) or last.get(c["symbol"]) or c["entry_px"]
            last[c["symbol"]] = px
            mv += size * px / c["entry_px"]
        equity = idle + mv
        curve.append((d, equity))
        invested.append(mv / equity if equity > 0 else 0.0)
    return {"curve": curve, "trades": n_tr, "stock_exposure": st.mean(invested)}


def idle_series(kind: str, dates: list[str], ranks: dict, cost: float):
    """Daily idle-asset returns (close to close) and rotation turnover cost."""
    spy = {b.date: b.close for b in load("SPY")}
    spy_r = {dates[n]: spy[dates[n]] / spy[dates[n - 1]] - 1 for n in range(1, len(dates))}
    if kind == "SPY":
        return spy_r, {}
    etfs = {etf: {b.date: b.close for b in load(etf)} for etf in ETF.values()}
    rets, tcost, held = {}, {}, []
    for n in range(1, len(dates)):
        d, p = dates[n], dates[n - 1]
        if held:
            day = [etfs[e][d] / etfs[e][p] - 1 for e in held if d in etfs[e] and p in etfs[e]]
            rets[d] = st.mean(day) if day else spy_r[d]
        else:
            rets[d] = spy_r[d]                        # before the first ranking: SPY
        if n % REBAL == 0 and d in ranks:             # choose at d's close, hold from d+1
            new = [e for e, _ in sorted(ranks[d].items(), key=lambda kv: kv[1])][:TOP_SECTORS_HOLD]
            replaced = len(set(new) - set(held)) / TOP_SECTORS_HOLD if held else 1.0
            if n + 1 < len(dates):
                tcost[dates[n + 1]] = replaced * cost / 100     # round trip on what changed
            held = new
    return rets, tcost


def run(record_trials: bool = True) -> dict:
    cost = CostModel.retail_equity().round_trip_bps() / 100.0
    spy_bars = [b for b in load("SPY") if b.date >= START]
    all_dates = [b.date for b in load("SPY")]
    ranks = sector_ranks(all_dates)
    dates = [b.date for b in spy_bars]
    cands = candidates(dates, ranks)
    syms = {c["symbol"] for c in cands}
    closes = {s: {b.date: b.close for b in load(s) if b.date >= START} for s in syms}
    spy_curve = [(b.date, CAPITAL * b.close / spy_bars[0].close) for b in spy_bars]
    out = {"spy": {w: metrics(spy_curve, *a) for w, a in
                   (("full", ()), ("pre2023", ("", ERA_SPLIT)), ("from2023", (ERA_SPLIT,)))},
           "candidates": len(cands),
           "strong_sector_share": round(sum(c["strong_sector"] for c in cands) / max(len(cands), 1), 3),
           "configs": {}}
    spy_full = out["spy"]["full"]
    for name, cfg in CONFIGS.items():
        ir, tc = idle_series(cfg["idle"], dates, ranks, cost)
        r = run_config(cfg, cands, closes, dates, ir, tc, cost)
        m = {w: metrics(r["curve"], *a) for w, a in
             (("full", ()), ("pre2023", ("", ERA_SPLIT)), ("from2023", (ERA_SPLIT,)))}
        beats = all(m[w]["cagr_pct"] > out["spy"][w]["cagr_pct"] and
                    m[w]["sharpe"] > out["spy"][w]["sharpe"] for w in m)
        passed = beats and m["full"]["max_dd_pct"] <= spy_full["max_dd_pct"]
        out["configs"][name] = m | {"cfg": cfg, "trades": r["trades"],
                                    "stock_exposure_pct": round(100 * r["stock_exposure"], 1),
                                    "final": round(r["curve"][-1][1]), "passed": passed}
        if record_trials:
            record(Trial(strategy="rev2_overlay", sharpe=m["full"]["sharpe"],
                         cagr=m["full"]["cagr_pct"] / 100, max_drawdown=m["full"]["max_dd_pct"] / 100,
                         params={"config": name} | cfg, n_periods=len(r["curve"]),
                         window=f"{START}..{dates[-1]}", note=f"prereg rev2; passed={passed}"))
    out["passed"] = [k for k, v in out["configs"].items() if v["passed"]]
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def _main() -> None:
    r = run()
    print("=" * 92)
    print(f"rev2 — S2 on an invested base; {r['candidates']:,} dip signals "
          f"({100 * r['strong_sector_share']:.0f}% in a top-5 sector)")
    print("=" * 92)
    print(f"{'':<6}{'window':<10}{'CAGR':>8}{'vol':>8}{'Sharpe':>8}{'maxDD':>8}")
    for w in ("full", "pre2023", "from2023"):
        m = r["spy"][w]
        print(f"{'SPY':<6}{w:<10}{m['cagr_pct']:>7.1f}%{m['vol_pct']:>7.1f}%{m['sharpe']:>8.2f}{m['max_dd_pct']:>7.1f}%")
    for name, c in r["configs"].items():
        cfg = c["cfg"]
        print(f"-- {name}: idle {cfg['idle']}, exit {cfg['exit']}, sector filter {cfg['sector_filter']} | "
              f"{c['trades']} trades, {c['stock_exposure_pct']}% in dip positions, final ${c['final']:,}"
              + ("  PASS" if c["passed"] else ""))
        for w in ("full", "pre2023", "from2023"):
            m = c[w]
            print(f"{name:<6}{w:<10}{m['cagr_pct']:>7.1f}%{m['vol_pct']:>7.1f}%{m['sharpe']:>8.2f}{m['max_dd_pct']:>7.1f}%")
    print(f"\nPASSED: {', '.join(r['passed']) or 'none'}")


if __name__ == "__main__":
    _main()
