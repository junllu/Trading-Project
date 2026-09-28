"""Single-stock IV-spike backtest on REAL historical option prices.

    python -m app.options.iv_backtest plan       # harvest tasks -> data/option_history/plan.json
    python -m app.options.iv_backtest status     # harvested vs planned
    python -m app.options.iv_backtest analyze    # the pre-registered test

Implements docs/prereg/2026-09-28-single-stock-iv-spike.md.

HARVEST FILE (one per symbol x expiry, written by a read-only Claude session):
  data/option_history/<SYM>_<YYYY-MM-DD>.json
  {"symbol": "NVDA", "expiry": "2026-06-18",
   "contracts": [{"type": "put", "strike": 200.0, "id": "...",
                  "bars": [{"date": "2026-04-01", "close": 27.5, "interpolated": false}, ...]}]}
"""
from __future__ import annotations

import json
import math
import statistics as st
import sys
from datetime import date, timedelta

from ..backtest.trials import Trial, record
from ..config import ROOT
from ..data.market_hours import is_trading_day
from ..data.ohlc import load
from .premium_study import strike_for_delta
from .pricing import black_scholes

DIR = ROOT / "data" / "option_history"
PANEL = ["GOOGL", "MRVL", "NVDA", "META", "TSLA", "NOW", "SPCX", "AAPL", "MSFT", "AMZN",
         "AMD", "AVGO", "MU", "NFLX", "PLTR", "COIN", "JPM"]
RATE, DELTA = 0.045, 0.25
SPIKE_REL, SPIKE_RV = 1.20, 1.20
HAIRCUT, HAIRCUT_STRESS = 0.075, 0.15
PASS_T = 2.24


def monthly_expiries(first="2025-10", last="2026-09") -> list[date]:
    out = []
    y, m = map(int, first.split("-"))
    ly, lm = map(int, last.split("-"))
    while (y, m) <= (ly, lm):
        d = date(y, m, 15)
        while d.weekday() != 4:                      # third Friday: 15th..21st
            d += timedelta(days=1)
        while not is_trading_day(d):                 # holiday -> Thursday
            d -= timedelta(days=1)
        out.append(d)
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _rv20(closes: list[float]) -> float | None:
    if len(closes) < 21:
        return None
    r = [math.log(closes[j] / closes[j - 1]) for j in range(len(closes) - 20, len(closes))]
    return st.pstdev(r) * math.sqrt(252)


def _on_or_before(bars, d: str):
    ok = [b for b in bars if b.date <= d]
    return ok[-1] if ok else None


def plan() -> dict:
    tasks = []
    for sym in PANEL:
        bars = load(sym)
        if not bars:
            continue
        for exp in monthly_expiries():
            if exp.isoformat() < bars[0].date:
                continue
            ref = (exp - timedelta(days=30)).isoformat()
            b = _on_or_before(bars, ref)
            if b is None or b.date < bars[0].date:
                continue
            closes = [x.close for x in bars if x.date <= b.date]
            rv = _rv20(closes)
            if not rv:
                continue
            tasks.append({"symbol": sym, "expiry": exp.isoformat(), "ref_date": b.date,
                          "spot_ref": round(b.close, 2), "rv20_ref": round(rv, 4),
                          "put_target": round(strike_for_delta(b.close, rv, 30, DELTA, True), 2),
                          "call_target": round(strike_for_delta(b.close, rv, 30, DELTA, False), 2),
                          "window_start": (exp - timedelta(days=45)).isoformat()})
    DIR.mkdir(parents=True, exist_ok=True)
    out = {"tasks": tasks, "instructions": (
        "Read-only. For each task without data/option_history/<SYM>_<expiry>.json: "
        "get_option_instruments(chain_symbol, expiration_dates=expiry, type=put, state=expired) and the same "
        "for calls; keep the 3 strikes nearest put_target and the 3 nearest call_target; "
        "get_option_historicals(those <=6 ids, start_time=window_start T00:00:00Z, "
        "end_time=expiry+1 day T00:00:00Z, interval=day); write the harvest file in the documented format "
        "(bars: date = begins_at[:10], close = close_price as a number, interpolated flag).")}
    (DIR / "plan.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def status() -> dict:
    p = json.loads((DIR / "plan.json").read_text("utf-8"))
    done = [t for t in p["tasks"] if (DIR / f"{t['symbol']}_{t['expiry']}.json").exists()]
    return {"planned": len(p["tasks"]), "harvested": len(done),
            "remaining": [f"{t['symbol']}_{t['expiry']}" for t in p["tasks"] if t not in done][:10]}


# --- analysis ----------------------------------------------------------------------
def _iv(price: float, spot: float, k: float, days: float, put: bool) -> float | None:
    intrinsic = max(k - spot, 0) if put else max(spot - k, 0)
    if price <= intrinsic + 0.01 or days <= 0:
        return None
    lo, hi = 0.01, 5.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if black_scholes(spot, k, days, mid, RATE, is_call=not put).price < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _series(c: dict, spot: dict, exp: date) -> list[dict]:
    """Per real bar: date, close, spot, days left, IV, |delta|."""
    put = c["type"] == "put"
    out = []
    for b in c["bars"]:
        if b.get("interpolated") or b["date"] not in spot:
            continue
        days = (exp - date.fromisoformat(b["date"])).days
        v = _iv(float(b["close"]), spot[b["date"]], c["strike"], days, put)
        if v is None:
            continue
        dlt = abs(black_scholes(spot[b["date"]], c["strike"], days, v, RATE, is_call=not put).delta)
        out.append({"date": b["date"], "close": float(b["close"]), "spot": spot[b["date"]],
                    "days": days, "iv": v, "delta": dlt})
    return out


def _pnl(kind: str, prem: float, k: float, s_entry: float, s_exp: float, haircut: float) -> float:
    p = prem * (1 - haircut)
    if kind == "put":
        return 100 * (p - max(k - s_exp, 0)) / k
    return 100 * (p - max(s_exp - k, 0)) / s_entry


def trades(haircut: float = HAIRCUT) -> list[dict]:
    p = json.loads((DIR / "plan.json").read_text("utf-8"))
    out = []
    for t in p["tasks"]:
        f = DIR / f"{t['symbol']}_{t['expiry']}.json"
        if not f.exists():
            continue
        h = json.loads(f.read_text("utf-8"))
        bars = load(t["symbol"])
        spot = {b.date: b.close for b in bars}
        closes = [b.close for b in bars]
        dates = [b.date for b in bars]
        exp = date.fromisoformat(t["expiry"])
        s_exp = spot.get(t["expiry"])
        if s_exp is None:
            continue
        for kind in ("put", "call"):
            cs = [c for c in h["contracts"] if c["type"] == kind]
            series = {c["strike"]: _series(c, spot, exp) for c in cs}
            # contract: entry-day |delta| closest to 0.25 on the reference date
            ref = t["ref_date"]
            def at(sr, d):
                return next((x for x in sr if x["date"] >= d), None)
            cand = [(k, at(sr, ref)) for k, sr in series.items() if at(sr, ref)]
            if not cand:
                continue
            k, _ = min(cand, key=lambda kv: abs(kv[1]["delta"] - DELTA))
            sr = series[k]
            base = at(sr, ref)
            lo, hi = (exp - timedelta(days=40)).isoformat(), (exp - timedelta(days=21)).isoformat()
            spike = None
            for i, x in enumerate(sr):
                if not (lo <= x["date"] <= hi) or i < 5:
                    continue
                prior = [y["iv"] for y in sr[max(0, i - 10): i]]
                n = dates.index(x["date"]) if x["date"] in dates else None
                rv = _rv20(closes[: n + 1]) if n else None
                if prior and rv and x["iv"] >= SPIKE_REL * st.mean(prior) and x["iv"] >= SPIKE_RV * rv:
                    spike = x
                    break
            row = {"symbol": t["symbol"], "expiry": t["expiry"], "type": kind, "strike": k}
            row["base"] = {"date": base["date"], "iv": round(base["iv"], 4),
                           "pnl": _pnl(kind, base["close"], k, base["spot"], s_exp, haircut)}
            if spike:
                row["spike"] = {"date": spike["date"], "iv": round(spike["iv"], 4),
                                "pnl": _pnl(kind, spike["close"], k, spike["spot"], s_exp, haircut)}
            out.append(row)
    return out


def _cvar(xs: list[float]) -> float:
    s = sorted(xs)
    tail = s[: max(1, len(s) // 20)]
    return st.mean(tail)


def analyze() -> dict:
    res = {"haircut": HAIRCUT, "stress_haircut": HAIRCUT_STRESS, "status": status(), "tests": {}}
    tr, tr_stress = trades(HAIRCUT), trades(HAIRCUT_STRESS)
    for kind in ("put", "call"):
        rows = [r for r in tr if r["type"] == kind]
        stress = [r for r in tr_stress if r["type"] == kind and "spike" in r]
        base = [r["base"]["pnl"] for r in rows]
        spk = [r["spike"]["pnl"] for r in rows if "spike" in r]
        by_exp: dict[str, list[float]] = {}
        for r in rows:
            if "spike" in r:
                by_exp.setdefault(r["expiry"], []).append(r["spike"]["pnl"] - r["base"]["pnl"])
        diffs = [st.mean(v) for v in by_exp.values()]
        t = (st.mean(diffs) / (st.stdev(diffs) / math.sqrt(len(diffs)))
             if len(diffs) >= 3 and st.stdev(diffs) > 0 else None)
        stress_mean = st.mean(r["spike"]["pnl"] for r in stress) if stress else None
        out = {"base_trades": len(base), "spike_trades": len(spk),
               "base_mean_pct": round(st.mean(base), 3) if base else None,
               "spike_mean_pct": round(st.mean(spk), 3) if spk else None,
               "paired_mean_diff_pct": round(st.mean(diffs), 3) if diffs else None,
               "expiries_paired": len(diffs), "t": None if t is None else round(t, 2),
               "spike_mean_at_stress_haircut": None if stress_mean is None else round(stress_mean, 3),
               "base_cvar5": round(_cvar(base), 2) if base else None,
               "spike_cvar5": round(_cvar(spk), 2) if spk else None}
        out["passed"] = bool(t is not None and diffs and st.mean(diffs) > 0 and t >= PASS_T
                             and stress_mean is not None and stress_mean > 0
                             and out["spike_cvar5"] >= 1.5 * out["base_cvar5"])
        res["tests"][kind] = out
        record(Trial(strategy="single_stock_iv_spike", sharpe=round(t or 0, 3), params={"type": kind},
                     n_periods=len(diffs), window="2025-10..2026-09 pilot",
                     note=f"prereg 2026-09-28; passed={out['passed']}"))
    (DIR / "analysis.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    return res


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    fn = {"plan": plan, "status": status, "analyze": analyze}[cmd]
    r = fn()
    print(json.dumps(r if cmd != "plan" else {"tasks": len(r["tasks"])}, indent=1)[:4000])
