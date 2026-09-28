"""Monitor the user's REAL option positions - alerts only, one line each.

    python -m app.paper.positions_monitor gate            # RUN or SKIP for this hour
    python -m app.paper.positions_monitor check POS.json   # alert lines to push

Read-only by construction: the session fetches positions and quotes from the
brokers with get_* tools, this decides what is worth saying. The user trades
the core accounts manually (2026-09-27); nothing here can place anything.

CADENCE
  Once a day (the 12:32 PT run, ~30 min before the close - time to act), and
  hourly on any day one of the positions expires, when assignment and pin risk
  actually move. `gate` decides from the expiries seen on the last run.

ALERTS (each at most once per day per position)
  short: 50% of the credit can be kept by buying back (wheel1's take-profit)
         in the money (assignment risk), with days left
         expiring within 7 days and out of the money: let expire, or close cheap
  long:  expiring within 5 days: sell or let expire
         value halved or doubled vs cost

POS.json (written by the session):
  {"asof": "...", "positions": [{"account": "RH"|"WB", "symbol": "AAOI",
    "type": "put", "side": "short", "strike": 88, "expiration": "2026-10-30",
    "qty": 1, "cost": 3.40, "bid": 1.6, "ask": 1.75, "delta": -0.2, "spot": 101.2}]}
  cost = premium per share (credit for shorts, debit for longs).
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime

from ..config import ROOT

DIR = ROOT / "data" / "paper" / "watch"
STATE = DIR / "positions_monitor_state.json"
DAILY_HOUR = 12


def _state() -> dict:
    try:
        return json.loads(STATE.read_text("utf-8"))
    except (OSError, ValueError):
        return {"expiries": [], "fired": {}}


def gate(now: datetime | None = None) -> str:
    """Every hourly run checks prices (user, 2026-09-28: don't skip the price
    checks). Alerts still fire at most once per day per position, so running
    hourly adds vigilance, not noise."""
    return "RUN"


def _label(p: dict) -> str:
    return f"{p['symbol']} ${p['strike']:g}{p['type'][0].upper()} {p['expiration'][5:]}"


def check(data: dict, today: date | None = None) -> list[str]:
    today = today or date.today()
    s = _state()
    out: list[tuple[str, str]] = []
    for p in data.get("positions", []):
        dte = (date.fromisoformat(p["expiration"]) - today).days
        spot, bid, ask = float(p.get("spot") or 0), float(p.get("bid") or 0), float(p.get("ask") or 0)
        k, cost, qty = float(p["strike"]), float(p["cost"]), int(p.get("qty") or 1)
        itm = (spot < k) if p["type"] == "put" else (spot > k)
        key = f"{p['account']}:{_label(p)}"
        if p["side"] == "short":
            if ask > 0 and ask <= 0.5 * cost:
                out.append((key + ":tp", f"{_label(p)}: {1 - ask / cost:.0%} of credit kept - buy back ~${ask:.2f}"
                                         f" x{qty} to lock ${100 * qty * (cost - ask):.0f}."))
            if itm and spot:
                out.append((key + ":itm", f"{_label(p)} is ITM: {p['symbol']} ${spot:.2f}, {dte}d left - "
                                          f"assignment risk. Roll, close (~${ask:.2f}) or accept."))
            elif 0 <= dte <= 7 and spot:
                gap = abs(spot / k - 1)
                out.append((key + ":exp", f"{_label(p)} expires in {dte}d, OTM by {gap:.0%} - let it expire"
                                          f" or close for ${ask:.2f}."))
        else:
            if 0 <= dte <= 5:
                out.append((key + ":exp", f"{_label(p)} (long) expires in {dte}d: sell at ~${bid:.2f} or let it"
                                          f" {'expire ITM' if itm else 'expire worthless'}."))
            if cost and bid >= 2 * cost:
                out.append((key + ":x2", f"{_label(p)} (long) doubled: bid ${bid:.2f} vs ${cost:.2f} cost."))
            elif cost and 0 < bid <= 0.5 * cost:
                out.append((key + ":half", f"{_label(p)} (long) halved: bid ${bid:.2f} vs ${cost:.2f} cost."))
    s["expiries"] = sorted({p["expiration"] for p in data.get("positions", [])})
    fired = s.setdefault("fired", {})
    msgs = []
    for k2, text in out:
        if fired.get(k2) != today.isoformat():
            fired[k2] = today.isoformat()
            msgs.append(text)
    DIR.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=1), encoding="utf-8")
    return msgs


if __name__ == "__main__":
    if sys.argv[1] == "gate":
        print(gate())
    else:
        for m in check(json.loads(open(sys.argv[2], encoding="utf-8").read())):
            print(m)
