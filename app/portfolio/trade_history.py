"""Analyze exported Robinhood trade history.

The local Claude (with the robinhood-trading MCP) exports every filled order to
data/trade_history.json:

    {"orders": [
       {"symbol":"TLRY","side":"buy","quantity":100,"price":50.0,"date":"2021-02-10"},
       {"symbol":"TLRY","side":"sell","quantity":100,"price":20.0,"date":"2022-08-01"} ]}

This module matches buys to sells FIFO, computes realized P&L, win rate, holding
periods, per-symbol stats, and — using the macro timeline — whether entries and
exits were aligned with the policy regime (e.g. selling cannabis long AFTER the
sector regime turned negative = "exited too late").
"""
from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..config import ROOT
from ..macro import MacroEngine, sector_of

HISTORY_PATH = ROOT / "data" / "trade_history.json"


def _days(d1: str, d2: str) -> int:
    try:
        return (datetime.strptime(d2, "%Y-%m-%d") - datetime.strptime(d1, "%Y-%m-%d")).days
    except ValueError:
        return 0


@dataclass
class RoundTrip:
    symbol: str
    qty: float
    buy_date: str
    sell_date: str
    buy_price: float
    sell_price: float

    @property
    def pnl(self) -> float:
        return (self.sell_price - self.buy_price) * self.qty

    @property
    def return_pct(self) -> float:
        return (self.sell_price / self.buy_price - 1) * 100 if self.buy_price else 0.0

    @property
    def holding_days(self) -> int:
        return _days(self.buy_date, self.sell_date)


def validate(orders: list[dict]) -> list[dict]:
    """Flag data-quality issues that would corrupt the analysis even if the
    analyzer runs correctly. Each warning: {level, symbol, issue}."""
    warnings: list[dict] = []
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    seen: set[tuple] = set()

    for o in orders:
        sym = str(o.get("symbol", "")).upper()
        # structural checks
        try:
            qty = float(o["quantity"]); price = float(o["price"])
        except (KeyError, TypeError, ValueError):
            warnings.append({"level": "error", "symbol": sym, "issue": "missing/invalid quantity or price"})
            continue
        if qty <= 0 or price <= 0:
            warnings.append({"level": "error", "symbol": sym, "issue": f"non-positive qty/price ({qty}@{price})"})
        if not o.get("date"):
            warnings.append({"level": "warn", "symbol": sym, "issue": "missing date (breaks holding-period + macro flags)"})
        # option/crypto-looking symbol
        if not sym.isalnum() or len(sym) > 6 or any(ch.isdigit() for ch in sym):
            warnings.append({"level": "warn", "symbol": sym, "issue": "symbol looks like an option/crypto — may be mis-parsed as equity"})
        key = (sym, o.get("side"), o.get("quantity"), o.get("price"), o.get("date"))
        if key in seen:
            warnings.append({"level": "warn", "symbol": sym, "issue": "exact duplicate execution"})
        seen.add(key)
        by_symbol[sym].append(o)

    for sym, os_ in by_symbol.items():
        prices = [float(o["price"]) for o in os_ if _num(o.get("price"))]
        if prices and min(prices) > 0 and max(prices) / min(prices) > 8:
            warnings.append({"level": "warn", "symbol": sym,
                             "issue": f"price range {max(prices)/min(prices):.0f}x — likely a STOCK SPLIT; "
                                      f"verify prices are split-adjusted before trusting P&L"})
        # running inventory: a sell exceeding prior buys = missing history or split mismatch
        inv = 0.0
        for o in sorted(os_, key=lambda x: x.get("date", "")):
            if str(o["side"]).lower() == "buy":
                inv += float(o["quantity"])
            elif str(o["side"]).lower() == "sell":
                if float(o["quantity"]) > inv + 1e-6:
                    warnings.append({"level": "warn", "symbol": sym,
                                     "issue": f"sell exceeds prior buys on {o.get('date')} — missing early history or split"})
                inv -= float(o["quantity"])
    return warnings


def _num(x) -> bool:
    try:
        float(x); return True
    except (TypeError, ValueError):
        return False


def load_trade_history(path: Path | None = None) -> list[dict]:
    p = path or HISTORY_PATH
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    return list(data.get("orders", data if isinstance(data, list) else []))


def _fifo_round_trips(orders: list[dict]) -> tuple[list[RoundTrip], dict[str, float]]:
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for o in orders:
        by_symbol[str(o["symbol"]).upper()].append(o)
    trips: list[RoundTrip] = []
    open_qty: dict[str, float] = {}
    for sym, os_ in by_symbol.items():
        os_ = sorted(os_, key=lambda x: x.get("date", ""))
        lots: deque[tuple[float, float, str]] = deque()   # (qty, price, date)
        for o in os_:
            side = str(o["side"]).lower()
            qty = float(o["quantity"]); price = float(o["price"]); date = o.get("date", "")
            if side == "buy":
                lots.append((qty, price, date))
            elif side == "sell":
                remaining = qty
                while remaining > 1e-9 and lots:
                    bq, bp, bd = lots[0]
                    matched = min(bq, remaining)
                    trips.append(RoundTrip(sym, matched, bd, date, bp, price))
                    remaining -= matched
                    if matched >= bq - 1e-9:
                        lots.popleft()
                    else:
                        lots[0] = (bq - matched, bp, bd)
        open_qty[sym] = sum(q for q, _, _ in lots)
    return trips, open_qty


def analyze(orders: list[dict]) -> dict:
    macro = MacroEngine()
    trips, open_qty = _fifo_round_trips(orders)

    wins = [t for t in trips if t.pnl > 0]
    losses = [t for t in trips if t.pnl <= 0]
    total_pnl = sum(t.pnl for t in trips)

    per_symbol: dict[str, dict] = defaultdict(lambda: {"pnl": 0.0, "trips": 0, "wins": 0})
    late_exits: list[dict] = []
    for t in trips:
        ps = per_symbol[t.symbol]
        ps["pnl"] += t.pnl; ps["trips"] += 1; ps["wins"] += 1 if t.pnl > 0 else 0
        # macro alignment: sold into a negative sector regime = potential "late exit"
        exit_tilt = macro.symbol_bias(t.symbol, t.sell_date)
        entry_tilt = macro.symbol_bias(t.symbol, t.buy_date)
        if exit_tilt < -0.1 and t.holding_days > 60:
            late_exits.append({
                "symbol": t.symbol, "sector": sector_of(t.symbol),
                "buy_date": t.buy_date, "sell_date": t.sell_date,
                "return_pct": round(t.return_pct, 1), "pnl": round(t.pnl, 2),
                "exit_regime_tilt": round(exit_tilt, 2),
                "note": "sold while the policy regime for this sector was already negative",
            })

    return {
        "trades_matched": len(trips),
        "realized_pnl": round(total_pnl, 2),
        "win_rate_pct": round(100 * len(wins) / len(trips), 1) if trips else 0.0,
        "avg_win": round(sum(t.pnl for t in wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(t.pnl for t in losses) / len(losses), 2) if losses else 0.0,
        "avg_holding_days": round(sum(t.holding_days for t in trips) / len(trips), 1) if trips else 0,
        "best": max((t.__dict__ | {"pnl": round(t.pnl, 2)} for t in trips),
                    key=lambda x: x["pnl"], default={}),
        "worst": min((t.__dict__ | {"pnl": round(t.pnl, 2)} for t in trips),
                     key=lambda x: x["pnl"], default={}),
        "per_symbol": {k: {"pnl": round(v["pnl"], 2), "trips": v["trips"],
                           "win_rate_pct": round(100 * v["wins"] / v["trips"], 1)}
                       for k, v in sorted(per_symbol.items(), key=lambda x: x[1]["pnl"])},
        "still_open": {k: v for k, v in open_qty.items() if v > 1e-9},
        "late_exits": late_exits,     # the "exited too late" flags (macro-aligned)
    }
