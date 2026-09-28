"""Watch list — the user's own Strategy-3 names, checked daily with wheel1's rules.

    python -m app.paper.watch plan              # what to quote -> data/paper/watch/plan.json
    python -m app.paper.watch eval QUOTES.json  # which names qualify today

ADVICE ONLY. Nothing here trades, on paper or otherwise: the core account is
operated manually by the user (2026-09-27) and is read-only to Claude. A name
"qualifies" under exactly wheel1's rules (docs/prereg/2026-09-27-wheel1.md):
30-45 days, |delta| 0.20-0.30, IV >= 1.2x (puts) / 1.3x (calls) trailing
20-session realised vol, bid >= $0.30, spread <= 25%, no earnings on or before
expiry, no 20/55-day breakdown in the last 5 sessions. Calls are only checked
on names the core book holds at least 100 shares of, struck at or above the
average cost.
"""
from __future__ import annotations

import json
import sys
from datetime import date

from ..config import ROOT
from ..data.ohlc import load
from ..portfolio.holdings import load_holdings
from .wheel import IVRV_CALL, IVRV_PUT, _pick, recent_breakdown, rv20

WATCH = ["GOOGL", "MRVL", "NVDA", "META", "NOW", "TSLA", "SPCX"]
DIR = ROOT / "data" / "paper" / "watch"


def _held() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for h in load_holdings():
        s = str(h["symbol"]).upper()
        cur = out.setdefault(s, {"shares": 0.0, "cost": 0.0})
        cur["shares"] += float(h.get("shares") or 0)
        cur["cost"] += float(h.get("shares") or 0) * float(h.get("avg_price") or 0)
    return {s: {"shares": v["shares"], "avg": v["cost"] / v["shares"]}
            for s, v in out.items() if v["shares"] >= 100}


def plan() -> dict:
    held = _held()
    rows = []
    for s in WATCH:
        b = load(s)
        rows.append({"symbol": s, "rv20": round(rv20(b) or 0, 4),
                     "recent_breakdown": recent_breakdown(b) if len(b) > 60 else False,
                     "check_calls": s in held,
                     "min_call_strike": round(held[s]["avg"], 2) if s in held else None,
                     "contracts_covered": int(held[s]["shares"] // 100) if s in held else 0})
    out = {"date": date.today().isoformat(), "symbols": rows,
           "instructions": "Same fetch as the wheel: first expiry 30-45 days out; puts 0.80-0.98 x spot "
                           "for every symbol, calls 1.02-1.20 x spot where check_calls is true; "
                           "get_earnings_results per symbol. Write quotes in wheel format to "
                           "data/paper/watch/quotes_<date>.json and run `eval`."}
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / "plan.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def evaluate(quotes: dict, today: date | None = None) -> list[dict]:
    today = today or date.today()
    p = json.loads((DIR / "plan.json").read_text("utf-8"))
    earnings = quotes.get("earnings") or {}
    spots = quotes.get("spots") or {}
    hits = []
    for row in p["symbols"]:
        s, rv = row["symbol"], row["rv20"]
        if row["recent_breakdown"] or not rv:
            continue
        kinds = [("put", IVRV_PUT, 0.0)]
        if row["check_calls"]:
            kinds.append(("call", IVRV_CALL, row["min_call_strike"] or 0.0))
        for kind, need, kmin in kinds:
            c = _pick(quotes.get("contracts", []), s, kind, today, min_strike=kmin)
            if not c or float(c["iv"]) < need * rv:
                continue
            if earnings.get(s) and earnings[s] <= c["expiration"]:
                continue
            k, bid = float(c["strike"]), float(c["bid"])
            hits.append({"symbol": s, "type": kind, "strike": k, "expiration": c["expiration"],
                         "bid": bid, "delta": float(c["delta"]), "iv_rv": round(float(c["iv"]) / rv, 2),
                         "spot": spots.get(s), "per_contract": round(100 * bid, 2),
                         "contracts": row["contracts_covered"] if kind == "call" else 1,
                         "collateral": round(100 * k, 2) if kind == "put" else None})
    DIR.mkdir(parents=True, exist_ok=True)
    with (DIR / "hits.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"date": today.isoformat(), "hits": hits}) + "\n")
    return hits


if __name__ == "__main__":
    if sys.argv[1] == "plan":
        print(json.dumps(plan(), indent=1))
    else:
        q = json.loads(open(sys.argv[2], encoding="utf-8").read())
        print(json.dumps(evaluate(q), indent=1))
