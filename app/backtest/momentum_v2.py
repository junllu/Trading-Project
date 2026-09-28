"""Mega-cap momentum v2 — only stocks that were in the S&P 500 at the time.

    python -m app.backtest.momentum_v2

Implements docs/prereg/2026-09-27-momentum-v2.md.
"""
from __future__ import annotations

import csv
import json
import statistics as st

from ..config import ROOT
from ..data.ohlc import OHLC_DIR, load
from .costs import CostModel
from .portfolio_sim import CAPITAL, ERA_SPLIT, START, metrics
from .trend_momentum import REBAL, TOP_N
from .trials import Trial, record

MEMBERSHIP = ROOT / "data" / "universe" / "sp500_ticker_start_end.csv"
OUT_PATH = ROOT / "data" / "research" / "momentum_v2.json"
RENAMES = {"META": ["FB"], "ELV": ["ANTM"], "COR": ["ABC"], "BALL": ["BLL"], "DAY": ["CDAY"],
           "FI": ["FISV"], "RTX": ["UTX"], "WBD": ["DISCA"], "PARA": ["VIAC", "CBS"],
           "BKNG": ["PCLN"]}


def intervals() -> dict[str, list[tuple[str, str]]]:
    raw: dict[str, list[tuple[str, str]]] = {}
    for r in csv.DictReader(MEMBERSHIP.open(encoding="utf-8")):
        raw.setdefault(r["ticker"].replace(".", "-"), []).append((r["start_date"], r["end_date"] or "9999"))
    out = {k: list(v) for k, v in raw.items()}
    for new, olds in RENAMES.items():
        for old in olds:
            out.setdefault(new, []).extend(raw.get(old, []))
    return out


def member(iv: list[tuple[str, str]], d: str) -> bool:
    return any(a <= d < b for a, b in iv)


def run_one(pool: int, dates: list[str], one_way: float) -> tuple[list, dict]:
    iv = intervals()
    have = {p.stem for p in OHLC_DIR.glob("*.csv")}
    syms = [s for s in iv if s in have and any(b >= START for _, b in iv[s])]
    data = {}
    for s in syms:
        b = load(s)
        if len(b) > 60:
            data[s] = {x.date: (x.close, x.volume) for x in b}
    all_dates = [b.date for b in load("SPY")]
    idx = {d: n for n, d in enumerate(all_dates)}
    eq, weights, curve, picks_log = CAPITAL, {}, [], {}
    for k, d in enumerate(dates):
        if k > 0 and weights:
            p = dates[k - 1]
            live = {s: w for s, w in weights.items() if d in data[s] and p in data[s]}
            # a name with no bar today (delisted mid-hold) is held at its last price: 0 return
            r = sum(w * (data[s][d][0] / data[s][p][0] - 1) for s, w in live.items())
            eq *= 1 + r
            grown = {s: w * (data[s][d][0] / data[s][p][0]) if s in live else w for s, w in weights.items()}
            tot = sum(grown.values())
            weights = {s: v / tot for s, v in grown.items()} if tot else {}
        if k % REBAL == 0:
            n = idx[d]
            d252, d21 = all_dates[n - 252], all_dates[n - 21]
            dv = {}
            for s, m in data.items():
                if not member(iv[s], d) or d not in m or d252 not in m or d21 not in m:
                    continue
                win = [m[x][0] * m[x][1] for x in all_dates[n - 62: n + 1] if x in m]
                if len(win) >= 50:
                    dv[s] = st.mean(win)
            big = sorted(dv, key=dv.get, reverse=True)[:pool]
            mom = {s: data[s][d21][0] / data[s][d252][0] - 1 for s in big}
            pick = sorted(mom, key=mom.get, reverse=True)[:TOP_N]
            new = {s: 1 / len(pick) for s in pick} if pick else {}
            turnover = sum(abs(new.get(s, 0) - weights.get(s, 0)) for s in set(new) | set(weights))
            eq *= 1 - turnover * one_way / 100
            weights = new
            picks_log[d] = pick
        curve.append((d, eq))
    return curve, picks_log


def run(record_trials: bool = True) -> dict:
    one_way = CostModel.retail_equity().one_way_bps / 100.0
    spy = [b for b in load("SPY") if b.date >= START]
    dates = [b.date for b in spy]
    W = (("full", ()), ("pre2023", ("", ERA_SPLIT)), ("from2023", (ERA_SPLIT,)))
    spy_curve = [(b.date, CAPITAL * b.close / spy[0].close) for b in spy]
    out = {"SPY": {w: metrics(spy_curve, *a) for w, a in W}, "configs": {}}
    for pool in (50, 100):
        name = f"M2-{pool}"
        curve, picks = run_one(pool, dates, one_way)
        m = {w: metrics(curve, *a) for w, a in W}
        passed = all(m[w]["cagr_pct"] > 0 and m[w]["sharpe"] > out["SPY"][w]["sharpe"]
                     for w in ("pre2023", "from2023"))
        freq: dict[str, int] = {}
        for ps in picks.values():
            for s in ps:
                freq[s] = freq.get(s, 0) + 1
        out["configs"][name] = m | {"passed": passed, "final": round(curve[-1][1]),
                                    "most_held": sorted(freq.items(), key=lambda kv: -kv[1])[:12],
                                    "latest_picks": picks[max(picks)]}
        if record_trials:
            record(Trial(strategy="momentum_v2", sharpe=m["full"]["sharpe"],
                         cagr=m["full"]["cagr_pct"] / 100, max_drawdown=m["full"]["max_dd_pct"] / 100,
                         params={"config": name}, n_periods=len(curve),
                         window=f"{START}..{dates[-1]}", note=f"point-in-time S&P; passed={passed}"))
    out["passed"] = [k for k, v in out["configs"].items() if v["passed"]]
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def _main() -> None:
    r = run()
    print(f"{'':<8}{'window':<10}{'CAGR':>8}{'vol':>8}{'Sharpe':>8}{'maxDD':>8}")
    for name, m in [("SPY", r["SPY"])] + list(r["configs"].items()):
        for w in ("full", "pre2023", "from2023"):
            x = m[w]
            print(f"{name:<8}{w:<10}{x['cagr_pct']:>7.1f}%{x['vol_pct']:>7.1f}%{x['sharpe']:>8.2f}{x['max_dd_pct']:>7.1f}%")
        if "passed" in m:
            print(f"         final ${m['final']:,}  PASS={m['passed']}  most held: "
                  + ", ".join(f"{s}({n})" for s, n in m["most_held"]))
            print(f"         latest picks: {', '.join(m['latest_picks'])}")
    print(f"\nPASSED: {', '.join(r['passed']) or 'none'}")


if __name__ == "__main__":
    _main()
