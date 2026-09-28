"""Sell index puts after a fear spike — tested on the VIX, a real option price.

    python -m app.options.vix_putwrite

Implements docs/prereg/2026-09-28-vix-spike-putwrite.md.
"""
from __future__ import annotations

import json
import math
import statistics as st

from ..backtest.trials import Trial, record
from ..config import ROOT
from ..data.ohlc import load
from .premium_study import strike_for_delta
from .pricing import black_scholes

OUT = ROOT / "data" / "research" / "vix_putwrite.json"
H, DAYS, DELTA, RATE = 21, 30, 0.25, 0.045
ERAS = (("<2008", "", "2008-01-01"), ("2008-2019", "2008-01-01", "2020-01-01"), ("2020+", "2020-01-01", "9999"))
PASS_T = 2.13


def _rv20(closes: list[float], i: int) -> float | None:
    if i < 21:
        return None
    r = [math.log(closes[j] / closes[j - 1]) for j in range(i - 19, i + 1)]
    return st.pstdev(r) * math.sqrt(252)


def trades(signal) -> list[tuple[str, float]]:
    spy = load("SPY")
    vix = {b.date: b.close for b in load("^VIX")}
    dates = [b.date for b in spy]
    closes = [b.close for b in spy]
    vseries = [vix.get(d) for d in dates]
    out, busy = [], -1
    for i in range(25, len(spy) - H):
        v = vseries[i]
        if v is None or i <= busy:
            continue
        window = [x for x in vseries[i - 19: i + 1] if x is not None]
        rv = _rv20(closes, i)
        if not signal(i, v, st.mean(window) if window else None, rv):
            continue
        s0, iv = closes[i], v / 100
        k = strike_for_delta(s0, iv, DAYS, DELTA, put=True)
        prem = black_scholes(s0, k, DAYS, iv, RATE, is_call=False).price
        pay = max(k - closes[i + H], 0.0)
        out.append((dates[i], 100 * (prem - pay) / k))
        busy = i + H
    return out


CONFIGS = {
    "BASE": lambda i, v, avg, rv: i % H == 0,
    "V1": lambda i, v, avg, rv: avg is not None and v >= 1.20 * avg,
    "V2": lambda i, v, avg, rv: rv is not None and rv > 0 and (v / 100) / rv >= 1.5,
    "V3": lambda i, v, avg, rv: (avg is not None and v >= 1.20 * avg
                                 and rv is not None and rv > 0 and (v / 100) / rv >= 1.5),
}


def stats(tr: list[tuple[str, float]]) -> dict:
    r = [x for _, x in tr]
    if not r:
        return {"n": 0}
    tail = sorted(r)[: max(1, len(r) // 20)]
    return {"n": len(r), "mean_pct": round(st.mean(r), 3), "win": round(sum(x > 0 for x in r) / len(r), 3),
            "cvar5_pct": round(st.mean(tail), 2), "worst_pct": round(min(r), 2),
            "sd": st.stdev(r) if len(r) > 1 else 0.0}


def run() -> dict:
    res = {name: trades(sig) for name, sig in CONFIGS.items()}
    base = stats(res["BASE"])
    out = {"BASE": base | {"eras": {e: stats([t for t in res["BASE"] if lo <= t[0] < hi])
                                     for e, lo, hi in ERAS}}}
    for name in ("V1", "V2", "V3"):
        s = stats(res[name])
        if s["n"] < 5:
            out[name] = s | {"passed": False}
            continue
        se = math.sqrt(s["sd"] ** 2 / s["n"] + base["sd"] ** 2 / base["n"])
        t = (s["mean_pct"] - base["mean_pct"]) / se if se else 0.0
        eras = {e: stats([x for x in res[name] if lo <= x[0] < hi]) for e, lo, hi in ERAS}
        passed = (t >= PASS_T and all(v.get("mean_pct", -1) > 0 for v in eras.values())
                  and s["cvar5_pct"] >= 1.5 * base["cvar5_pct"])
        out[name] = s | {"t_vs_base": round(t, 2), "eras": eras, "passed": passed}
        record(Trial(strategy="vix_putwrite", sharpe=round(t, 3), params={"config": name},
                     n_periods=s["n"], window="1993..2026", note=f"prereg 2026-09-28; passed={passed}"))
    for v in out.values():
        v.pop("sd", None)
        for e in v.get("eras", {}).values():
            e.pop("sd", None)
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    r = run()
    print(f"{'':<6}{'trades':>7}{'mean%':>8}{'win':>7}{'CVaR5%':>8}{'worst%':>8}{'t vs BASE':>10}   eras (mean%)")
    for k, v in r.items():
        eras = "  ".join(f"{e}:{x.get('mean_pct', '-')}" for e, x in v.get("eras", {}).items())
        print(f"{k:<6}{v['n']:>7}{v.get('mean_pct', 0):>8.3f}{100 * v.get('win', 0):>6.1f}%{v.get('cvar5_pct', 0):>8.2f}"
              f"{v.get('worst_pct', 0):>8.2f}{v.get('t_vs_base', ''):>10}   {eras}"
              + ("   PASS" if v.get("passed") else ""))
