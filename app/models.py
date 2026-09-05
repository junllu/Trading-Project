"""Core domain types shared across the portal.

Kept dependency-free (dataclasses + enums) so every layer — brokers, strategy,
risk, web — speaks the same language without importing heavy packages.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(str, Enum):
    PENDING = "pending"      # created, not yet sent
    QUEUED = "queued"        # awaiting manual confirmation (confirm mode)
    SUBMITTED = "submitted"  # sent to broker
    FILLED = "filled"
    REJECTED = "rejected"    # blocked by risk manager or broker
    CANCELLED = "cancelled"


@dataclass
class Quote:
    symbol: str
    price: float
    ts: float = field(default_factory=time.time)


@dataclass
class Position:
    symbol: str
    quantity: float
    avg_price: float
    broker: str = "paper"

    @property
    def cost_basis(self) -> float:
        return self.quantity * self.avg_price

    def market_value(self, price: float) -> float:
        return self.quantity * price

    def unrealized_pnl(self, price: float) -> float:
        return (price - self.avg_price) * self.quantity


@dataclass
class Account:
    broker: str
    cash: float
    positions: list[Position] = field(default_factory=list)

    def equity(self, prices: dict[str, float]) -> float:
        pos_value = sum(p.market_value(prices.get(p.symbol, p.avg_price)) for p in self.positions)
        return self.cash + pos_value


@dataclass
class Order:
    symbol: str
    side: Side
    quantity: float
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None
    broker: str = "paper"
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: OrderStatus = OrderStatus.PENDING
    filled_price: Optional[float] = None
    reason: Optional[str] = None  # rejection / status detail
    ts: float = field(default_factory=time.time)

    def notional(self, ref_price: float) -> float:
        px = self.limit_price if self.order_type is OrderType.LIMIT and self.limit_price else ref_price
        return self.quantity * px


@dataclass
class Signal:
    """A strategy's intent, before it becomes an order."""
    symbol: str
    side: Side
    strength: float = 1.0            # 0..1 confidence
    order_value: float = 0.0         # target $ notional; 0 => strategy decides qty
    strategy: str = "unknown"
    note: str = ""
    ts: float = field(default_factory=time.time)
