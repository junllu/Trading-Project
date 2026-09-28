"""Trend-timed index and point-in-time mega-cap momentum.

    python -m app.backtest.trend_momentum

Implements docs/prereg/2026-09-27-trend-momentum.md.

THE LOOK-AHEAD THIS AVOIDS

"The 50 largest stocks" chosen TODAY and backtested from 2016 is a list of
winners — they are largest because they went up. Size here is ranked at each
rebalance by trailing 63-session dollar volume, which was knowable then.
"""
from __future__ import annotations

import csv
import json
import statistics as st

from ..config import ROOT
from ..data.ohlc import load
from .breakout_regime_study import PREREG
from .costs import CostModel
from .portfolio_sim import CAPITAL, ERA_SPLIT, START, metrics
from .trials import Trial, record

OUT_PATH = ROOT / "data" / "research" / "trend_momentum.json"
REBAL = 21
TOP_N = 10


def _tbill() -> list[tuple[str, float]]:
    rows = []
    p = ROOT / "data" / "macro_series" / "DTB3.csv"
    for r in csv.reader(p.read_text("utf-8").splitlines()[1:]):
        try:
            rows.append((r[0], float(r[1]) / 100))
        except (ValueError, IndexError):
            continue
    return rows


def _daily_rf(dates: list[str]) -> dict[str, float]:
    """Daily T-bill return using the last print strictly BEFORE each date."""
    tb, j, out, last = _tbill(), 0, {}, 0.0
    for d in dates:
        while j < len(tb) and tb[j][0] < d:
            last = tb[j][1]
            j += 1
        out[d] = last / 252
    return out


def trend(sym: str, dates: list[str], rf: dict, one_way: float) -> list[tuple[str, float]]:
    bars = [b for b in load(sym)]
    close = {b.date: b.close for b in bars}
    alld = [b.date for b in bars]
    pos = {d: n for n, d in enumerate(alld)}
    eq, invested, curve = CAPITAL, False, []
    for k, d in enumerate(dates):
        if k > 0:
            p = dates[k - 1]
            r = close[d] / close[p] - 1 if invested else rf.get(d, 0.0)
            eq *= 1 + r
        month_end = k + 1 < len(dates) and dates[k + 1][:7] != d[:7]
        n = pos.get(d)
        if month_end and n is not None and n >= 199:
            sma = sum(close[alld[i]] for i in range(n - 199, n + 1)) / 200
            want = close[d] > sma
            if want != invested:
                eq *= 1 - one_way / 100
                invested = want
        curve.append((d, eq))
    return curve


def momentum(universe: list[str], pool: int, dates: list[str], one_way: float) -> list[tuple[str, float]]:
    data = {}
    for s in universe:
        b = load(s)
        if len(b) > 300:
            data[s] = {x.date: (x.close, x.volume) for x in b}
    all_dates = [b.date for b in load("SPY")]
    idx = {d: n for n, d in enumerate(all_dates)}
    eq, weights, curve = CAPITAL, {}, []
    for k, d in enumerate(dates):
        if k > 0 and weights:
            p = dates[k - 1]
            r = sum(w * (data[s][d][0] / data[s][p][0] - 1) for s, w in weights.items()
                    if d in data[s] and p in data[s])
            eq *= 1 + r
            # drift the weights with prices
            grown = {s: w * (data[s][d][0] / data[s][p][0]) for s, w in weights.items()
                     if d in data[s] and p in data[s]}
            tot = sum(grown.values())
            weights = {s: v / tot for s, v in grown.items()} if tot else {}
        if k % REBAL == 0:
            n = idx[d]
            if n < 253:
                curve.append((d, eq))
                continue
            d63, d252, d21 = all_dates[n - 63], all_dates[n - 252], all_dates[n - 21]
            dv = {}
            for s, m in data.items():
                win = [m[x][0] * m[x][1] for x in all_dates[n - 62: n + 1] if x in m]
                if len(win) >= 50 and d252 in m and d21 in m and d in m:
                    dv[s] = st.mean(win)
            big = sorted(dv, key=dv.get, reverse=True)[:pool]
            mom = {s: data[s][d21][0] / data[s][d252][0] - 1 for s in big}
            pick = sorted(mom, key=mom.get, reverse=True)[:TOP_N]
            new = {s: 1 / len(pick) for s in pick} if pick else {}
            turnover = sum(abs(new.get(s, 0) - weights.get(s, 0)) for s in set(new) | set(weights))
            eq *= 1 - turnover * one_way / 100
            weights = new
        curve.append((d, eq))
    return curve


def run(record_trials: bool = True) -> dict:
    one_way = CostModel.retail_equity().one_way_bps / 100.0
    spy = [b for b in load("SPY") if b.date >= START]
    dates = [b.date for b in spy]
    rf = _daily_rf(dates)
    uni = json.loads(PREREG.read_text("utf-8"))
    names = sorted(set(uni["existing_study"] + uni["new_study"] + uni["holdout_names"]))
    spy_curve = [(b.date, CAPITAL * b.close / spy[0].close) for b in spy]
    qqq = {b.date: b.close for b in load("QQQ")}
    qqq_curve = [(d, CAPITAL * qqq[d] / qqq[dates[0]]) for d in dates if d in qqq]
    W = (("full", ()), ("pre2023", ("", ERA_SPLIT)), ("from2023", (ERA_SPLIT,)))
    out = {"SPY": {w: metrics(spy_curve, *a) for w, a in W},
           "QQQ": {w: metrics(qqq_curve, *a) for w, a in W}, "configs": {}}
    curves = {"T-SPY": trend("SPY", dates, rf, one_way), "T-QQQ": trend("QQQ", dates, rf, one_way),
              "M-50": momentum(names, 50, dates, one_way), "M-100": momentum(names, 100, dates, one_way)}
    for name, curve in curves.items():
        m = {w: metrics(curve, *a) for w, a in W}
        passed = all(m[w]["sharpe"] > out["SPY"][w]["sharpe"] for w in ("pre2023", "from2023"))
        positive = all(m[w]["cagr_pct"] > 0 for w in ("pre2023", "from2023"))
        out["configs"][name] = m | {"passed": passed, "positive_both_eras": positive,
                                    "final": round(curve[-1][1])}
        if record_trials:
            record(Trial(strategy="trend_momentum", sharpe=m["full"]["sharpe"],
                         cagr=m["full"]["cagr_pct"] / 100, max_drawdown=m["full"]["max_dd_pct"] / 100,
                         params={"config": name}, n_periods=len(curve),
                         window=f"{START}..{dates[-1]}", note=f"prereg trend-momentum; passed={passed}"))
    out["passed"] = [k for k, v in out["configs"].items() if v["passed"]]
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def _main() -> None:
    r = run()
    print(f"{'':<7}{'window':<10}{'CAGR':>8}{'vol':>8}{'Sharpe':>8}{'maxDD':>8}")
    rows = [("SPY", r["SPY"]), ("QQQ", r["QQQ"])] + [(k, v) for k, v in r["configs"].items()]
    for name, m in rows:
        for w in ("full", "pre2023", "from2023"):
            x = m[w]
            print(f"{name:<7}{w:<10}{x['cagr_pct']:>7.1f}%{x['vol_pct']:>7.1f}%{x['sharpe']:>8.2f}{x['max_dd_pct']:>7.1f}%")
        if "passed" in m:
            print(f"        final ${m['final']:,}  beats SPY Sharpe both eras: {m['passed']}  "
                  f"positive both eras: {m['positive_both_eras']}")
    print(f"\nPASSED: {', '.join(r['passed']) or 'none'}")


if __name__ == "__main__":
    _main()
