"""S3 — what does the insurance ACTUALLY cost after a breakout or breakdown?

    python -m app.options.premium_study                     # the pre-registered study
    python -m app.options.premium_study calc ADBE 235.47 220 28 0.32 --put
                                        # premium / delta / theta / yield for one strike

Implements docs/prereg/2026-09-27-premium-s3.md.

THE MATH, IN ONE PLACE

Selling a put at strike K for premium p on a stock at S:
  collateral      = 100 · K                       (cash-secured)
  max profit      = p                              (stock ≥ K at expiry)
  breakeven       = K − p
  assignment      ≈ |delta| (risk-neutral); the study measures the REAL rate
  theta           = premium decay per day you are paid for waiting
  yield           = p / K over the days held; annualised ×365/days
  expected P&L    = p − E[max(K − S_T, 0)]   ← the only number that matters
A covered call is the mirror: payout max(S_T − K, 0), and the cost of being
assigned is upside you gave away, not a loss of cash.

"fair vol" is the vol at which the Black-Scholes premium equals the average
payout actually observed. If the market's implied vol is above it, the
premium over-pays for the risk; below it, you are selling insurance too cheap.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics as st
from dataclasses import asdict, dataclass

from ..backtest.breakout_regime_study import PREREG, events_for
from ..backtest.trials import Trial, record
from ..config import ROOT
from ..data.ohlc import Bar, load
from .pricing import black_scholes

OUT_PATH = ROOT / "data" / "research" / "premium_study.json"
SESSIONS = 20
DAYS = 28
DELTA = 0.25
RATE = 0.045
PASS_T = -2.24
ERA_SPLIT = "2023-01-01"
CONTROL_EVERY = 20

TRIALS = {
    "P-S2":  ("put",  "bd20", set()),
    "P-S1":  ("put",  "bd55", {"relstr", "market"}),
    "C-B20": ("call", "bo20", set()),
    "C-B55": ("call", "bo55", set()),
}


# --- the calculator -----------------------------------------------------------
def strike_for_delta(spot: float, vol: float, days: float, target: float, put: bool) -> float:
    """Strike whose |delta| equals `target` (bisection; delta is monotone in K)."""
    lo, hi = spot * 0.3, spot * 3.0
    for _ in range(60):
        mid = (lo + hi) / 2
        d = abs(black_scholes(spot, mid, days, vol, RATE, is_call=not put).delta)
        # put |delta| rises with K; call delta falls with K
        if (d < target) == put:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def calc(spot: float, strike: float, days: float, iv: float, put: bool) -> dict:
    g = black_scholes(spot, strike, days, iv, RATE, is_call=not put)
    p = g.price
    out = {"structure": "cash-secured put" if put else "covered call",
           "premium_per_share": round(p, 2), "premium_per_contract": round(100 * p, 2),
           "delta": g.delta, "theta_per_day_contract": round(100 * abs(g.theta), 2),
           "prob_assigned_model": g.prob_itm,
           "yield_pct": round(100 * p / (strike if put else spot), 2),
           "annualised_pct": round(100 * p / (strike if put else spot) * 365 / days, 1)}
    if put:
        out |= {"collateral": round(100 * strike, 2), "breakeven": round(strike - p, 2),
                "effective_buy_price_if_assigned": round(strike - p, 2)}
    else:
        out |= {"called_away_at": round(strike + p, 2),
                "upside_capped_above_pct": round(100 * (strike / spot - 1), 2)}
    return out


# --- history -------------------------------------------------------------------
@dataclass
class Obs:
    date: str
    rv: float          # trailing realised vol at the signal
    prem: float        # BS premium at rv, % of spot
    payout: float      # realised payout, % of spot
    assigned: bool


def _rv(bars: list[Bar], i: int) -> float | None:
    if i < 21:
        return None
    r = [math.log(bars[j].close / bars[j - 1].close) for j in range(i - 19, i + 1)]
    v = st.pstdev(r) * math.sqrt(252)
    return v if v > 0.05 else None


def observe(bars: list[Bar], i: int, put: bool) -> Obs | None:
    if i + SESSIONS >= len(bars):
        return None
    rv = _rv(bars, i)
    if rv is None:
        return None
    s0, sT = bars[i].close, bars[i + SESSIONS].close
    k = strike_for_delta(s0, rv, DAYS, DELTA, put)
    prem = black_scholes(s0, k, DAYS, rv, RATE, is_call=not put).price
    pay = max(k - sT, 0.0) if put else max(sT - k, 0.0)
    return Obs(bars[i].date, rv, 100 * prem / s0, 100 * pay / s0, pay > 0)


def fair_ratio(obs: list[Obs], put: bool, bars_cache: dict | None = None) -> float | None:
    """Vol multiple m such that mean BS premium at (rv·m) == mean payout.
    Approximated through the premium's near-linearity in vol for OTM strikes:
    solved on the aggregate premium curve by bisection."""
    if len(obs) < 10:
        return None
    target = st.mean(o.payout for o in obs)
    # premium % at multiple m for each obs, using a fixed-moneyness strike (the
    # strike was fixed at the time of sale; only the vol input scales)
    def mean_prem(m: float) -> float:
        tot = 0.0
        for o in obs:
            k = strike_for_delta(100.0, o.rv, DAYS, DELTA, put)
            tot += black_scholes(100.0, k, DAYS, o.rv * m, RATE, is_call=not put).price
        return tot / len(obs)
    lo, hi = 0.05, 5.0
    if mean_prem(hi) < target:
        return hi
    for _ in range(30):
        mid = (lo + hi) / 2
        if mean_prem(mid) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _cost_multiple(o: Obs) -> float:
    return o.payout / o.prem if o.prem > 0 else 0.0


def _monthly_diff(sig: list[Obs], ctl: list[Obs], lo="", hi="9999") -> tuple[float, float, int]:
    def by_m(xs):
        m: dict[str, list[float]] = {}
        for o in xs:
            if lo <= o.date < hi:
                m.setdefault(o.date[:7], []).append(_cost_multiple(o))
        return {k: st.mean(v) for k, v in m.items()}
    a, c = by_m(sig), by_m(ctl)
    d = [a[k] - c[k] for k in a if k in c]
    if len(d) < 3 or st.stdev(d) == 0:
        return (st.mean(d) if d else 0.0), 0.0, len(d)
    return st.mean(d), st.mean(d) / (st.stdev(d) / math.sqrt(len(d))), len(d)


def _summary(obs: list[Obs], put: bool) -> dict:
    if not obs:
        return {"events": 0}
    pnl = sorted(o.prem - o.payout for o in obs)
    tail = pnl[: max(1, len(pnl) // 20)]
    return {"events": len(obs),
            "assigned_rate": round(sum(o.assigned for o in obs) / len(obs), 3),
            "model_delta": DELTA,
            "mean_premium_at_rv_pct": round(st.mean(o.prem for o in obs), 3),
            "mean_payout_pct": round(st.mean(o.payout for o in obs), 3),
            "cost_multiple": round(st.mean(_cost_multiple(o) for o in obs), 3),
            "fair_ratio": round(fair_ratio(obs, put) or 0, 3),
            "cvar5_pct": round(st.mean(tail), 2)}


def run(record_trials: bool = True) -> dict:
    uni = json.loads(PREREG.read_text("utf-8"))
    obs: dict[str, list[Obs]] = {k: [] for k in TRIALS}
    ctl = {"put": [], "call": []}
    for group in (uni["existing_study"] + uni["new_study"], uni["holdout_names"]):
        evs, *_ = events_for(group)
        by_sym: dict[str, list] = {}
        for e in evs:
            by_sym.setdefault(e.symbol, []).append(e)
        for sym in group:
            bars = load(sym)
            if len(bars) < 300:
                continue
            idx = {b.date: n for n, b in enumerate(bars)}
            for tid, (kind, trig, need) in TRIALS.items():
                busy = -1
                for e in sorted(by_sym.get(sym, []), key=lambda e: e.date):
                    if e.trigger != trig or not need <= e.confirms:
                        continue
                    i = idx.get(e.date)
                    if i is None or i <= busy:
                        continue                  # one option per name at a time
                    o = observe(bars, i, kind == "put")
                    if o:
                        obs[tid].append(o)
                        busy = i + SESSIONS
            for i in range(60, len(bars) - SESSIONS, CONTROL_EVERY):
                for kind in ("put", "call"):
                    o = observe(bars, i, kind == "put")
                    if o:
                        ctl[kind].append(o)
    res = {"control_put": _summary(ctl["put"], True),
           "control_call": _summary(ctl["call"], False), "trials": {}}
    for tid, (kind, trig, need) in TRIALS.items():
        s = _summary(obs[tid], kind == "put")
        d, t, n = _monthly_diff(obs[tid], ctl[kind])
        d1 = _monthly_diff(obs[tid], ctl[kind], hi=ERA_SPLIT)[0]
        d2 = _monthly_diff(obs[tid], ctl[kind], lo=ERA_SPLIT)[0]
        s |= {"cost_vs_control": round(d, 3), "t": round(t, 2), "months": n,
              "pre2023": round(d1, 3), "from2023": round(d2, 3),
              "passed": d < 0 and t <= PASS_T and d1 < 0 and d2 < 0}
        res["trials"][tid] = s
        if record_trials:
            record(Trial(strategy="premium_s3", sharpe=round(-t, 3),
                         params={"trial": tid, "structure": kind, "trigger": trig,
                                 "conditions": sorted(need)},
                         n_periods=n, window="2015..2026 533 names",
                         note=f"prereg premium-s3; cost multiple vs control {d:+.3f}"))
    res["passed"] = [k for k, v in res["trials"].items() if v["passed"]]
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(res, indent=2), encoding="utf-8")
    return res


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="study", choices=["study", "calc"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--put", action="store_true")
    a = ap.parse_args()
    if a.cmd == "calc":
        spot, strike, days, iv = (float(x) for x in a.args[1:5])
        print(json.dumps({"symbol": a.args[0]} | calc(spot, strike, days, iv, a.put), indent=1))
        return
    r = run()
    print("=" * 104)
    print("S3 PREMIUM STUDY — short 0.25-delta options, 20 sessions, strike/premium at trailing realised vol")
    print("cost multiple = realised payout / premium at realised vol (1.0 = BS at realised vol breaks even)")
    print("fair ratio   = vol multiple the market must charge to break even (sell only if IV/RV above it)")
    print("=" * 104)
    hdr = f"{'':<14}{'events':>7}{'assigned':>9}{'prem%':>7}{'payout%':>8}{'cost x':>8}{'fair':>6}{'CVaR5%':>8}"
    print(hdr + f"{'vs ctl':>8}{'t':>7}{'<23':>7}{'23+':>7}")
    for name, s in (("control put", r["control_put"]), ("control call", r["control_call"])):
        print(f"{name:<14}{s['events']:>7}{100 * s['assigned_rate']:>8.1f}%{s['mean_premium_at_rv_pct']:>7.2f}"
              f"{s['mean_payout_pct']:>8.2f}{s['cost_multiple']:>8.2f}{s['fair_ratio']:>6.2f}{s['cvar5_pct']:>8.2f}")
    for tid, s in r["trials"].items():
        print(f"{tid:<14}{s['events']:>7}{100 * s['assigned_rate']:>8.1f}%{s['mean_premium_at_rv_pct']:>7.2f}"
              f"{s['mean_payout_pct']:>8.2f}{s['cost_multiple']:>8.2f}{s['fair_ratio']:>6.2f}{s['cvar5_pct']:>8.2f}"
              f"{s['cost_vs_control']:>+8.3f}{s['t']:>7.2f}{s['pre2023']:>+7.3f}{s['from2023']:>+7.3f}"
              + ("  PASS" if s["passed"] else ""))
    print(f"\nPASSED: {', '.join(r['passed']) or 'none'}")


if __name__ == "__main__":
    _main()
