"""vix1 — sell an S&P put only after a fear spike (paper, forward evidence).

    python -m app.paper.vix1          # after the close: record signal, settle, maybe open
    python -m app.paper.vix1 status   # current spike state (used by alerts)

WHY IT EXISTS

The user's observation (2026-09-28): on risk-off days option prices jump. At
the index level, docs/prereg/2026-09-28-vix-spike-putwrite.md tested selling a
21-session 0.25-delta S&P put only when the VIX spiked (>= 1.2x its 20-day
average) AND fear was priced well above movement (VIX / SPY 20-day realised vol
>= 1.5). Over 1993-2026: +0.74%/trade vs +0.36% for a monthly put-write, 92%
wins, worst trade -6.6% vs -16.7% — but t 2.07 missed the 2.13 bar on only
100 trades. A near miss where both return AND tail improve is exactly what a
forward record should settle, so it runs here, frozen, as the backtest did.

MECHANICS (identical to the backtest)
  signal on the close; strike at BS |delta| 0.25 with IV = VIX/100, 30 days;
  premium BS at VIX (conservative: real OTM puts trade above ATM IV); settle
  on SPY's close 21 sessions later; one position at a time. Results are % of
  cash collateral, so they are scale-free.

LIVE SIZING NOTE: one SPY/XSP put secures ~$75k. Live use needs that much
collateral or a defined-risk put spread — decided at funding, not here.
"""
from __future__ import annotations

import json
import math
import statistics as st
import sys

from ..config import ROOT
from ..data.ohlc import load
from ..options.premium_study import strike_for_delta
from ..options.pricing import black_scholes

DIR = ROOT / "data" / "paper" / "vix1"
H, DAYS, DELTA, RATE = 21, 30, 0.25, 0.045
SPIKE, FEAR = 1.20, 1.5
CAPITAL = 50_000.0


def _p(name):
    return DIR / name


def _load() -> dict:
    try:
        return json.loads(_p("state.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {"position": None, "equity": CAPITAL, "last_run": None, "trades": 0}


def _append(name: str, row: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    with _p(name).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def signal_state() -> dict:
    """Today's VIX spike reading from cached daily closes (knowable at the close)."""
    spy, vix = load("SPY"), {b.date: b.close for b in load("^VIX")}
    d = spy[-1].date
    closes = [b.close for b in spy]
    vs = [vix[b.date] for b in spy[-20:] if b.date in vix]
    r = [math.log(closes[j] / closes[j - 1]) for j in range(len(closes) - 20, len(closes))]
    rv = st.pstdev(r) * math.sqrt(252)
    v = vix.get(d)
    ratio = v / st.mean(vs) if v and vs else None
    fear = (v / 100) / rv if v and rv else None
    return {"date": d, "vix": v, "vix_20d_avg": round(st.mean(vs), 2) if vs else None,
            "spike_ratio": None if ratio is None else round(ratio, 3),
            "fear_ratio": None if fear is None else round(fear, 3), "spy": closes[-1],
            "on": bool(ratio and fear and ratio >= SPIKE and fear >= FEAR)}


def step() -> dict:
    s = _load()
    sig = signal_state()
    d = sig["date"]
    if s["last_run"] == d:
        return {"skipped": f"already ran for {d}"}
    _append("signals.jsonl", sig)
    spy = load("SPY")
    idx = {b.date: n for n, b in enumerate(spy)}
    events = []
    pos = s["position"]
    if pos:
        n0, n = idx.get(pos["entry_date"]), idx[d]
        if n0 is not None and n - n0 >= H:
            pay = max(pos["strike"] - spy[n0 + H].close, 0.0)
            pnl_pct = 100 * (pos["premium"] - pay) / pos["strike"]
            s["equity"] *= 1 + pnl_pct / 100
            s["trades"] += 1
            _append("trades.jsonl", pos | {"exit_date": spy[n0 + H].date, "payout": round(pay, 4),
                                          "pnl_pct": round(pnl_pct, 3)})
            events.append(f"settled: {pnl_pct:+.2f}% on collateral")
            s["position"] = pos = None
    if pos is None and sig["on"]:
        s0, iv = sig["spy"], sig["vix"] / 100
        k = strike_for_delta(s0, iv, DAYS, DELTA, put=True)
        prem = black_scholes(s0, k, DAYS, iv, RATE, is_call=False).price
        s["position"] = {"entry_date": d, "spy": s0, "vix": sig["vix"], "strike": round(k, 2),
                         "premium": round(prem, 4)}
        events.append(f"SPIKE: sold SPY put {k:.0f} for {prem:.2f} ({100 * prem / k:.2f}% of collateral)")
    # mark to market for the equity curve (BS at today's VIX on the open position)
    mtm = s["equity"]
    if s["position"]:
        p = s["position"]
        left = max(DAYS - (idx[d] - idx[p["entry_date"]]) * 30 / H, 0.5)
        now = black_scholes(sig["spy"], p["strike"], left, sig["vix"] / 100, RATE, is_call=False).price
        mtm = s["equity"] * (1 + (p["premium"] - now) / p["strike"])
    s["last_run"] = d
    DIR.mkdir(parents=True, exist_ok=True)
    _p("state.json").write_text(json.dumps(s, indent=1), encoding="utf-8")
    _append("equity.jsonl", {"date": d, "equity": round(mtm, 2)})
    return {"date": d, "signal": sig, "events": events, "open": bool(s["position"]), "trades": s["trades"]}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        print(json.dumps(signal_state(), indent=1))
    else:
        print(json.dumps(step(), indent=1))
