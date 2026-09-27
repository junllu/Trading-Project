"""wheel1 — cash-secured puts → shares → covered calls, paper, on REAL quotes.

    python -m app.paper.wheel plan               # what to quote today -> plan.json
    python -m app.paper.wheel step QUOTES.json   # apply the rules to fetched quotes
    python -m app.paper.wheel score

Implements docs/prereg/2026-09-27-wheel1.md.

WHY TWO STEPS

Robinhood option quotes are reachable only from inside a Claude session (the
MCP), not from Python. So `plan` decides — deterministically, from cached
daily bars — which contracts need quotes; the session fetches exactly those
(read-only) into a quotes file; `step` applies the pre-registered rules to it.
The rules live here, in code, so the session only fetches — it never decides.

QUOTES FILE (written by the session)

  {"asof": "2026-09-28T12:10", "spots": {"CMCSA": 21.5, ...},
   "earnings": {"CMCSA": "2026-10-23", ...},          # next report date, if any
   "contracts": [{"instrument_id": "...", "symbol": "CMCSA", "type": "put",
                  "strike": 20.0, "expiration": "2026-11-06",
                  "bid": 0.55, "ask": 0.65, "mark": 0.60,
                  "delta": -0.24, "iv": 0.31}, ...]}
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics as st
from datetime import date, datetime

from ..backtest.breakout_regime_study import PREREG
from ..config import ROOT
from ..data.ohlc import Bar, load

VERSION = "wheel1"
DIR = ROOT / "data" / "paper" / VERSION
STATE = DIR / "state.json"
LEDGER = DIR / "ledger.jsonl"
PLAN = DIR / "plan.json"
CURVE = DIR / "equity.jsonl"
SCORE = DIR / "scorecard.json"

CAPITAL = 50_000.0
FEE = 0.05
BUFFER = 0.10
PRICE_MIN, PRICE_MAX = 20.0, 250.0
DAILY_QUOTED = 8
DTE_MIN, DTE_MAX = 30, 45
DELTA_LO, DELTA_HI, DELTA_TGT = 0.20, 0.30, 0.25
IVRV_PUT, IVRV_CALL = 1.2, 1.3
MIN_BID, MAX_SPREAD = 0.30, 0.25
TAKE_PROFIT, CLOSE_DTE = 0.50, 21
STOP = 0.25


# --- state ------------------------------------------------------------------------
def _load() -> dict:
    try:
        return json.loads(STATE.read_text("utf-8"))
    except (OSError, ValueError):
        return {"version": VERSION, "start": None, "cash": CAPITAL, "options": [],
                "stocks": {}, "spy_start": None, "last_step": None}


def _save(s: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_name("state.json.tmp")
    tmp.write_text(json.dumps(s, indent=1), encoding="utf-8")
    tmp.replace(STATE)


def _log(row: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


# --- market helpers -------------------------------------------------------------------
def rv20(bars: list[Bar]) -> float | None:
    if len(bars) < 22:
        return None
    r = [math.log(bars[j].close / bars[j - 1].close) for j in range(len(bars) - 20, len(bars))]
    v = st.pstdev(r) * math.sqrt(252)
    return v if v > 0 else None


def recent_breakdown(bars: list[Bar], lookback: int = 5) -> bool:
    for i in range(len(bars) - lookback, len(bars)):
        for n in (20, 55):
            if i < n + 1:
                continue
            lvl = min(x.low for x in bars[i - n: i])
            lvl_prev = min(x.low for x in bars[i - n - 1: i - 1])
            if bars[i].close < lvl and bars[i - 1].close >= lvl_prev:
                return True
    return False


def dte(expiration: str, today: date) -> int:
    return (date.fromisoformat(expiration) - today).days


# --- plan -------------------------------------------------------------------------------
def plan(today: date | None = None) -> dict:
    today = today or date.today()
    s = _load()
    u = json.loads(PREREG.read_text("utf-8"))
    names = sorted(set(u["existing_study"] + u["new_study"] + u["holdout_names"]))
    busy = {o["symbol"] for o in s["options"]} | set(s["stocks"])
    eligible, rvs = [], {}
    for sym in names:
        if sym in busy:
            continue
        bars = load(sym)
        if len(bars) < 80 or not (PRICE_MIN <= bars[-1].close <= PRICE_MAX):
            continue
        if recent_breakdown(bars):
            continue
        v = rv20(bars)
        if v:
            eligible.append(sym)
            rvs[sym] = round(v, 4)
    rng = random.Random(f"{VERSION}-{today.isoformat()}")
    picks = sorted(rng.sample(eligible, min(DAILY_QUOTED, len(eligible))))
    calls_for = [sym for sym, h in s["stocks"].items()
                 if not any(o["symbol"] == sym and o["type"] == "call" for o in s["options"])]
    for sym in calls_for:
        v = rv20(load(sym))
        if v:
            rvs[sym] = round(v, 4)
    out = {"date": today.isoformat(), "version": VERSION,
           "expiry_window_days": [DTE_MIN, DTE_MAX],
           "new_puts": [{"symbol": sym, "rv20": rvs[sym], "strike_band": "0.80-0.98 x spot"}
                        for sym in picks],
           "new_calls": [{"symbol": sym, "rv20": rvs.get(sym), "min_strike": s["stocks"][sym]["basis"],
                          "strike_band": "1.02-1.20 x spot"} for sym in calls_for],
           "marks_needed": [o["instrument_id"] for o in s["options"]],
           "spots_needed": sorted(set(picks) | set(calls_for) | set(s["stocks"])
                                  | {o["symbol"] for o in s["options"]} | {"SPY"}),
           "earnings_needed": picks,
           "instructions": ("Read-only. For each new_puts/new_calls symbol: get_option_chains, take the "
                            "first expiration >= 30 and <= 45 days out; get_option_instruments for that "
                            "expiration and type; quote every strike in the band (<= 20 per call) with "
                            "get_option_quotes. Quote marks_needed ids. get_equity_quotes for spots_needed. "
                            "get_earnings_calendar for earnings dates of earnings_needed. Write the quotes "
                            "file in the documented format, then run `step`.")}
    DIR.mkdir(parents=True, exist_ok=True)
    PLAN.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


# --- step ----------------------------------------------------------------------------------
def _close_on(sym: str, day: str) -> float | None:
    for b in load(sym):
        if b.date == day:
            return b.close
    return None


def _pick(contracts: list[dict], sym: str, kind: str, today: date, min_strike: float = 0.0) -> dict | None:
    ok = []
    for c in contracts:
        if c["symbol"] != sym or c["type"] != kind:
            continue
        d = dte(c["expiration"], today)
        bid, ask = float(c.get("bid") or 0), float(c.get("ask") or 0)
        delta = abs(float(c.get("delta") or 0))
        if not (DTE_MIN <= d <= DTE_MAX) or bid < MIN_BID or ask <= 0:
            continue
        mid = (bid + ask) / 2
        if (ask - bid) / mid > MAX_SPREAD or not (DELTA_LO <= delta <= DELTA_HI):
            continue
        if float(c["strike"]) < min_strike:
            continue
        ok.append(c)
    return min(ok, key=lambda c: abs(abs(float(c["delta"])) - DELTA_TGT)) if ok else None


def step(quotes: dict, today: date | None = None) -> dict:
    today = today or date.today()
    D = today.isoformat()
    s = _load()
    if s["last_step"] == D:
        return {"skipped": f"already stepped {D}"}
    spots = {k.upper(): float(v) for k, v in (quotes.get("spots") or {}).items()}
    by_id = {c["instrument_id"]: c for c in quotes.get("contracts", [])}
    contracts = quotes.get("contracts", [])
    if s["start"] is None:
        s["start"], s["spy_start"] = D, spots.get("SPY")
    events = []

    # 1. expiries that have passed: settle on the official close of the expiry date
    keep = []
    for o in s["options"]:
        if o["expiration"] >= D:
            keep.append(o)
            continue
        px = _close_on(o["symbol"], o["expiration"])
        if px is None:
            keep.append(o)                         # close not cached yet; settle next step
            continue
        if o["type"] == "put" and px < o["strike"]:
            s["cash"] -= 100 * o["strike"]
            s["stocks"][o["symbol"]] = {"shares": 100, "basis": round(o["strike"] - o["credit"], 4),
                                        "assigned": o["expiration"]}
            events.append({"event": "assigned", "symbol": o["symbol"], "strike": o["strike"], "close": px})
        elif o["type"] == "call" and px > o["strike"]:
            s["cash"] += 100 * o["strike"]
            st_ = s["stocks"].pop(o["symbol"], None)
            events.append({"event": "called_away", "symbol": o["symbol"], "strike": o["strike"],
                           "basis": st_ and st_["basis"], "close": px})
        else:
            events.append({"event": "expired_worthless", "symbol": o["symbol"], "type": o["type"],
                           "strike": o["strike"], "close": px})
    s["options"] = keep

    # 2. manage open shorts: take profit at 50%, or close at <= 21 days
    keep = []
    for o in s["options"]:
        q = by_id.get(o["instrument_id"])
        if q:
            o["mark"] = float(q.get("mark") or o.get("mark") or o["credit"])
            ask = float(q.get("ask") or 0)
            reason = ("take_profit" if 0 < ask <= TAKE_PROFIT * o["credit"]
                      else "time" if dte(o["expiration"], today) <= CLOSE_DTE and ask > 0 else None)
            if reason:
                s["cash"] -= 100 * ask + FEE
                events.append({"event": f"bought_back_{reason}", "symbol": o["symbol"], "type": o["type"],
                               "credit": o["credit"], "debit": ask,
                               "pnl": round(100 * (o["credit"] - ask) - 2 * FEE, 2)})
                continue
        keep.append(o)
    s["options"] = keep

    # 3. risk stop on assigned shares
    for sym in list(s["stocks"]):
        px = spots.get(sym)
        h = s["stocks"][sym]
        if px and px <= (1 - STOP) * h["basis"]:
            for o in [o for o in s["options"] if o["symbol"] == sym]:
                q = by_id.get(o["instrument_id"])
                ask = float(q["ask"]) if q and q.get("ask") else o.get("mark", o["credit"])
                s["cash"] -= 100 * ask + FEE
                s["options"].remove(o)
            s["cash"] += 100 * px
            s["stocks"].pop(sym)
            events.append({"event": "stopped_out", "symbol": sym, "basis": h["basis"], "price": px})

    # 4. covered calls on shares without one
    for sym, h in s["stocks"].items():
        if any(o["symbol"] == sym and o["type"] == "call" for o in s["options"]):
            continue
        bars = load(sym)
        v = rv20(bars)
        c = _pick(contracts, sym, "call", today, min_strike=h["basis"])
        if c and v and float(c["iv"]) >= IVRV_CALL * v:
            s["cash"] += 100 * float(c["bid"]) - FEE
            s["options"].append(_short(c, D))
            events.append({"event": "sold_call", "symbol": sym, "strike": float(c["strike"]),
                           "credit": float(c["bid"]), "iv_rv": round(float(c["iv"]) / v, 2)})

    # 5. new cash-secured puts
    earnings = quotes.get("earnings") or {}
    busy = {o["symbol"] for o in s["options"]} | set(s["stocks"])
    for p in json.loads(PLAN.read_text("utf-8")).get("new_puts", []) if PLAN.exists() else []:
        sym = p["symbol"]
        if sym in busy:
            continue
        c = _pick(contracts, sym, "put", today)
        if not c:
            events.append({"event": "no_qualifying_put", "symbol": sym})
            continue
        if earnings.get(sym) and earnings[sym] <= c["expiration"]:
            events.append({"event": "skip_earnings", "symbol": sym, "earnings": earnings[sym]})
            continue
        v = p["rv20"]
        if float(c["iv"]) < IVRV_PUT * v:
            events.append({"event": "skip_iv_too_low", "symbol": sym, "iv_rv": round(float(c["iv"]) / v, 2)})
            continue
        collateral = 100 * float(c["strike"])
        reserved = sum(100 * o["strike"] for o in s["options"] if o["type"] == "put")
        eq = equity(s, spots)
        if s["cash"] - reserved - collateral < BUFFER * eq:
            events.append({"event": "skip_no_capital", "symbol": sym})
            continue
        s["cash"] += 100 * float(c["bid"]) - FEE
        s["options"].append(_short(c, D))
        busy.add(sym)
        events.append({"event": "sold_put", "symbol": sym, "strike": float(c["strike"]),
                       "expiration": c["expiration"], "credit": float(c["bid"]),
                       "delta": float(c["delta"]), "iv_rv": round(float(c["iv"]) / v, 2)})

    eq = equity(s, spots)
    s["last_step"] = D
    _save(s)
    for e in events:
        _log({"date": D} | e)
    spy = spots.get("SPY")
    with CURVE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"date": D, "equity": round(eq, 2),
                             "spy_equiv": round(CAPITAL * spy / s["spy_start"], 2)
                             if spy and s["spy_start"] else None}) + "\n")
    return {"date": D, "equity": round(eq, 2), "cash": round(s["cash"], 2),
            "open_options": len(s["options"]), "stocks": list(s["stocks"]), "events": events}


def _short(c: dict, D: str) -> dict:
    return {"instrument_id": c["instrument_id"], "symbol": c["symbol"], "type": c["type"],
            "strike": float(c["strike"]), "expiration": c["expiration"], "credit": float(c["bid"]),
            "mark": float(c.get("mark") or c["bid"]), "opened": D}


def equity(s: dict, spots: dict) -> float:
    stock = sum(100 * (spots.get(sym) or _last_close(sym) or h["basis"]) for sym, h in s["stocks"].items())
    shorts = sum(100 * o.get("mark", o["credit"]) for o in s["options"])
    return s["cash"] + stock - shorts


def _last_close(sym: str) -> float | None:
    b = load(sym)
    return b[-1].close if b else None


def scorecard() -> dict:
    s = _load()
    rows = [json.loads(x) for x in LEDGER.read_text("utf-8").splitlines()] if LEDGER.exists() else []
    curve = [json.loads(x) for x in CURVE.read_text("utf-8").splitlines()] if CURVE.exists() else []
    sold = [r for r in rows if r["event"] in ("sold_put", "sold_call")]
    puts_settled = [r for r in rows if r["event"] in ("assigned", "expired_worthless") and r.get("type", "put") == "put"]
    out = {"version": VERSION, "start": s["start"], "sessions": len(curve),
           "equity": curve[-1]["equity"] if curve else CAPITAL,
           "spy_equiv": curve[-1]["spy_equiv"] if curve else None,
           "premium_collected": round(sum(100 * r["credit"] for r in sold), 2),
           "puts_sold": sum(1 for r in sold if r["event"] == "sold_put"),
           "calls_sold": sum(1 for r in sold if r["event"] == "sold_call"),
           "assignments": sum(1 for r in rows if r["event"] == "assigned"),
           "called_away": sum(1 for r in rows if r["event"] == "called_away"),
           "stopped_out": sum(1 for r in rows if r["event"] == "stopped_out"),
           "open_options": len(s["options"]), "holdings": s["stocks"]}
    if curve:
        peak, mdd = curve[0]["equity"], 0.0
        for c in curve:
            peak = max(peak, c["equity"])
            mdd = max(mdd, 1 - c["equity"] / peak)
        out["max_dd_pct"] = round(100 * mdd, 2)
    DIR.mkdir(parents=True, exist_ok=True)
    SCORE.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "step", "score"])
    ap.add_argument("quotes", nargs="?")
    a = ap.parse_args()
    if a.cmd == "plan":
        print(json.dumps(plan(), indent=1))
    elif a.cmd == "step":
        q = json.loads(open(a.quotes, encoding="utf-8").read())
        print(json.dumps(step(q), indent=1))
        print(json.dumps(scorecard(), indent=1))
    else:
        print(json.dumps(scorecard(), indent=1))


if __name__ == "__main__":
    _main()
