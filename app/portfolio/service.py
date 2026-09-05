"""Cross-broker portfolio aggregation.

Collapses accounts from every connected broker into one consolidated view:
total cash, per-symbol net positions, market value, and unrealized P&L.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..brokers.base import BrokerBase, BrokerError
from ..models import Position


@dataclass
class ConsolidatedPosition:
    symbol: str
    quantity: float
    avg_price: float
    price: float
    brokers: list[str] = field(default_factory=list)

    @property
    def market_value(self) -> float:
        return self.quantity * self.price

    @property
    def unrealized_pnl(self) -> float:
        return (self.price - self.avg_price) * self.quantity

    @property
    def unrealized_pct(self) -> float:
        return ((self.price / self.avg_price - 1) * 100) if self.avg_price else 0.0


class PortfolioService:
    def __init__(self, brokers: dict[str, BrokerBase]):
        self.brokers = brokers

    def snapshot(self, prices: dict[str, float] | None = None) -> dict:
        prices = prices or {}
        total_cash = 0.0
        merged: dict[str, ConsolidatedPosition] = {}
        broker_status: dict[str, str] = {}

        for name, broker in self.brokers.items():
            try:
                acct = broker.get_account()
                broker_status[name] = "connected" if broker.is_connected() else "idle"
            except (BrokerError, Exception) as exc:  # never let one broker sink the view
                broker_status[name] = f"error: {exc}"
                continue

            total_cash += acct.cash
            for p in acct.positions:
                price = prices.get(p.symbol, p.avg_price)
                self._merge(merged, p, price, name)

        positions = list(merged.values())
        equity = total_cash + sum(p.market_value for p in positions)
        unrealized = sum(p.unrealized_pnl for p in positions)

        return {
            "cash": round(total_cash, 2),
            "equity": round(equity, 2),
            "unrealized_pnl": round(unrealized, 2),
            "positions": [self._pos_dict(p) for p in sorted(positions, key=lambda x: -x.market_value)],
            "brokers": broker_status,
        }

    @staticmethod
    def _merge(merged: dict[str, ConsolidatedPosition], p: Position, price: float, broker: str) -> None:
        if p.symbol not in merged:
            merged[p.symbol] = ConsolidatedPosition(
                symbol=p.symbol, quantity=p.quantity, avg_price=p.avg_price,
                price=price, brokers=[broker],
            )
            return
        m = merged[p.symbol]
        total_qty = m.quantity + p.quantity
        if total_qty != 0:
            m.avg_price = (m.avg_price * m.quantity + p.avg_price * p.quantity) / total_qty
        m.quantity = total_qty
        m.price = price
        if broker not in m.brokers:
            m.brokers.append(broker)

    @staticmethod
    def _pos_dict(p: ConsolidatedPosition) -> dict:
        return {
            "symbol": p.symbol,
            "quantity": round(p.quantity, 4),
            "avg_price": round(p.avg_price, 2),
            "price": round(p.price, 2),
            "market_value": round(p.market_value, 2),
            "unrealized_pnl": round(p.unrealized_pnl, 2),
            "unrealized_pct": round(p.unrealized_pct, 2),
            "brokers": p.brokers,
        }
