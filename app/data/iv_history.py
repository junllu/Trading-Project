"""Implied-volatility history — one row per symbol per day, from quotes we already fetch.

    python -m app.data.iv_history            # summary: symbols, days collected

WHY

No historical single-stock option prices exist here, which is why every
option idea so far had to be tested on the index (VIX) or forward only. The
wheel and watch runs fetch real Robinhood chains every session and, until
now, threw the implied volatility away. Keeping it builds the dataset needed
to test the user's observation (option prices jump on risk-off days) on
INDIVIDUAL stocks: after ~3 months, "sell puts after a single-stock IV spike"
can be pre-registered and tested on data that did not exist when it was
proposed.

ROW: date, symbol, source, spot, rv20, and for the 30-45 day expiry the
contracts closest to 0.25 delta (put and call): strike, expiration, delta,
iv, bid, ask. iv_rv = put IV / rv20 — the quantity the wheel's rule reads.
"""
from __future__ import annotations

import json
from datetime import date

from ..config import ROOT

PATH = ROOT / "data" / "iv_history" / "iv_daily.jsonl"


def _nearest(contracts: list[dict], sym: str, kind: str, today: date) -> dict | None:
    pool = [c for c in contracts if c.get("symbol") == sym and c.get("type") == kind
            and c.get("iv") and 30 <= (date.fromisoformat(c["expiration"]) - today).days <= 45]
    if not pool:
        return None
    c = min(pool, key=lambda c: abs(abs(float(c.get("delta") or 0)) - 0.25))
    return {"strike": float(c["strike"]), "expiration": c["expiration"], "delta": float(c["delta"]),
            "iv": float(c["iv"]), "bid": float(c.get("bid") or 0), "ask": float(c.get("ask") or 0)}


def record(quotes: dict, source: str, rv: dict[str, float] | None = None,
           today: date | None = None) -> int:
    today = today or date.today()
    contracts = quotes.get("contracts", [])
    spots = quotes.get("spots") or {}
    n = 0
    PATH.parent.mkdir(parents=True, exist_ok=True)
    with PATH.open("a", encoding="utf-8") as fh:
        for sym in sorted({c["symbol"] for c in contracts}):
            put, call = _nearest(contracts, sym, "put", today), _nearest(contracts, sym, "call", today)
            if not put and not call:
                continue
            r = (rv or {}).get(sym)
            row = {"date": today.isoformat(), "symbol": sym, "source": source, "spot": spots.get(sym),
                   "rv20": r, "put": put, "call": call,
                   "iv_rv": round(put["iv"] / r, 3) if put and r else None}
            fh.write(json.dumps(row) + "\n")
            n += 1
    return n


def summary() -> dict:
    if not PATH.exists():
        return {"rows": 0}
    rows = [json.loads(x) for x in PATH.read_text("utf-8").splitlines() if x.strip()]
    return {"rows": len(rows), "symbols": len({r["symbol"] for r in rows}),
            "days": len({r["date"] for r in rows}),
            "first": min(r["date"] for r in rows), "last": max(r["date"] for r in rows)}


if __name__ == "__main__":
    print(json.dumps(summary(), indent=1))
