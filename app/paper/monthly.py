"""Monthly paper tracks — trend1 (QQQ trend-timing) and mom1 (S&P momentum).

    python -m app.paper.monthly            # after the close; decides on month-end
    python -m app.paper.monthly score

WHY THESE TWO, AND THE HONEST STATUS

Both are positive on average in both eras (the user's bar, 2026-09-27), and
neither passed its stricter pre-registered rule:
  trend1 = T-QQQ    (e6a9350): 18.1% CAGR / Sharpe 1.02 / DD 28.6% — beat SPY on
                    all three full-period; lagged only the 2023+ bull.
  mom1   = M2-100   (6f918ed): 30.2% / 0.97 / DD 37.9% on POINT-IN-TIME S&P
                    membership — ~2x SPY's return mostly by carrying more risk.
The forward record decides; these tracks are frozen as of 2026-09-27.

RULES
  trend1  at each month-end close: QQQ above its 200-session average -> hold QQQ,
          else T-bills (DTB3). Otherwise hold. Daily marks at the close.
  mom1    at each month-end close: current S&P 500 members; top 100 by trailing
          63-session average dollar volume; hold the 10 with the best return from
          t-252 to t-21, equal weight, filled at that close. Daily marks.
Both: $100,000 paper, retail one-way cost on every dollar traded. The first run
allocates immediately (no waiting for a month-end).
"""
from __future__ import annotations

import csv
import json
import statistics as st
import sys

from ..backtest.costs import CostModel
from ..config import ROOT
from ..data.market_hours import next_trading_day
from ..data.ohlc import load

DIR = ROOT / "data" / "paper"
CAPITAL = 100_000.0
ONE_WAY = CostModel.retail_equity().one_way_bps / 100.0 / 100.0      # as a fraction
TRACKS = ("trend1", "mom1")


def _path(t: str, name: str):
    return DIR / t / name


def _load(t: str) -> dict:
    try:
        return json.loads(_path(t, "state.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {"track": t, "start": None, "cash": CAPITAL, "shares": {}, "last_run": None,
                "rebalances": 0}


def _save(t: str, s: dict) -> None:
    p = _path(t, "state.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name("state.json.tmp")
    tmp.write_text(json.dumps(s, indent=1), encoding="utf-8")
    tmp.replace(p)


def _append(t: str, name: str, row: dict) -> None:
    p = _path(t, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _tbill_daily(day: str) -> float:
    rows = (ROOT / "data" / "macro_series" / "DTB3.csv").read_text("utf-8").splitlines()[1:]
    last = 0.0
    for r in rows:
        d, _, v = r.partition(",")
        if d >= day:
            break
        try:
            last = float(v) / 100
        except ValueError:
            pass
    return last / 252


def _close(sym: str, day: str) -> float | None:
    for b in reversed(load(sym)):
        if b.date == day:
            return b.close
        if b.date < day:
            return None
    return None


def _equity(s: dict, day: str) -> float:
    return s["cash"] + sum(n * (_close(sym, day) or 0.0) for sym, n in s["shares"].items())


def _rebalance(s: dict, targets: dict[str, float], day: str) -> list[str]:
    """Move to target weights at `day`'s close, paying one-way cost on turnover."""
    eq = _equity(s, day)
    cur = {sym: n * (_close(sym, day) or 0.0) for sym, n in s["shares"].items()}
    want = {sym: w * eq for sym, w in targets.items()}
    traded = sum(abs(want.get(k, 0) - cur.get(k, 0)) for k in set(want) | set(cur))
    eq_after = eq - traded * ONE_WAY
    s["shares"] = {sym: w * eq_after / _close(sym, day) for sym, w in targets.items() if _close(sym, day)}
    s["cash"] = eq_after - sum(n * _close(sym, day) for sym, n in s["shares"].items())
    s["rebalances"] += 1
    return sorted(targets)


def _trend_target(day: str) -> dict[str, float]:
    bars = [b for b in load("QQQ") if b.date <= day]
    sma = sum(b.close for b in bars[-200:]) / 200
    return {"QQQ": 1.0} if bars[-1].close > sma else {}


def _mom_target(day: str) -> dict[str, float]:
    p = sorted((ROOT / "data" / "universe").glob("sp500_2*.csv"))[-1]
    members = [r["symbol"] for r in csv.DictReader(p.open(encoding="utf-8"))]
    spy_dates = [b.date for b in load("SPY") if b.date <= day]
    n = len(spy_dates) - 1
    d252, d21, win = spy_dates[n - 252], spy_dates[n - 21], set(spy_dates[n - 62: n + 1])
    dv, mom = {}, {}
    for sym in members:
        bars = {b.date: b for b in load(sym)}
        if day not in bars or d252 not in bars or d21 not in bars:
            continue
        w = [bars[d].close * bars[d].volume for d in win if d in bars]
        if len(w) >= 50:
            dv[sym] = st.mean(w)
            mom[sym] = bars[d21].close / bars[d252].close - 1
    big = sorted(dv, key=dv.get, reverse=True)[:100]
    pick = sorted(big, key=lambda x: mom[x], reverse=True)[:10]
    return {sym: 1 / len(pick) for sym in pick}


def step(day: str) -> dict:
    from datetime import date
    month_end = next_trading_day(date.fromisoformat(day)).month != date.fromisoformat(day).month
    out = {}
    for t in TRACKS:
        s = _load(t)
        if s["last_run"] == day:
            out[t] = {"skipped": "already ran"}
            continue
        if s["start"] and not s["shares"]:
            s["cash"] *= 1 + _tbill_daily(day)              # T-bills while out of the market
        decided = None
        if s["start"] is None or month_end:
            target = _trend_target(day) if t == "trend1" else _mom_target(day)
            decided = _rebalance(s, target, day)
            s["start"] = s["start"] or day
            _append(t, "decisions.jsonl", {"date": day, "holdings": decided or ["T-BILLS"]})
        eq = _equity(s, day)
        spy0 = s.setdefault("spy_start", _close("SPY", s["start"]))
        s["last_run"] = day
        _save(t, s)
        _append(t, "equity.jsonl", {"date": day, "equity": round(eq, 2),
                                    "spy_equiv": round(CAPITAL * (_close("SPY", day) or spy0) / spy0, 2)})
        out[t] = {"equity": round(eq, 2), "rebalanced": decided is not None,
                  "holdings": sorted(s["shares"]) or ["T-BILLS"]}
    return out


def score() -> dict:
    out = {}
    for t in TRACKS:
        p = _path(t, "equity.jsonl")
        rows = [json.loads(x) for x in p.read_text("utf-8").splitlines()] if p.exists() else []
        s = _load(t)
        out[t] = {"start": s["start"], "sessions": len(rows),
                  "equity": rows[-1]["equity"] if rows else CAPITAL,
                  "spy_equiv": rows[-1]["spy_equiv"] if rows else CAPITAL,
                  "holdings": sorted(s["shares"]) or ["T-BILLS"], "rebalances": s["rebalances"]}
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / "monthly_scorecard.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "score":
        print(json.dumps(score(), indent=1))
    else:
        from ..data.refresh import last_complete_session
        print(json.dumps(step(last_complete_session().isoformat()), indent=1))
        score()
