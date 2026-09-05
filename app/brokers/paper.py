"""A fully simulated broker.

Fills market orders instantly at the last known/simulated price and keeps a
cash + positions ledger in memory. This is the default execution target and
the backbone of every test — no network, no credentials, no risk.
"""
from __future__ import annotations

import random
from typing import Optional

from ..models import Account, Order, OrderStatus, Position, Quote, Side
from .base import BrokerBase, BrokerError


class PaperBroker(BrokerBase):
    name = "paper"

    def __init__(self, starting_cash: float = 100_000.0, seed_prices: Optional[dict[str, float]] = None):
        self.cash = float(starting_cash)
        self._positions: dict[str, Position] = {}
        self._prices: dict[str, float] = dict(seed_prices or {})
        self._connected = False
        self.orders: list[Order] = []

    # lifecycle -------------------------------------------------------------
    def connect(self) -> None:
        self._connected = True

    def is_connected(self) -> bool:
        return self._connected

    # price simulation ------------------------------------------------------
    def set_price(self, symbol: str, price: float) -> None:
        self._prices[symbol] = float(price)

    def _price(self, symbol: str) -> float:
        if symbol not in self._prices:
            # Deterministic-ish starting price for unseen symbols.
            self._prices[symbol] = round(random.uniform(50, 250), 2)
        return self._prices[symbol]

    # reads -----------------------------------------------------------------
    def get_quote(self, symbol: str) -> Quote:
        return Quote(symbol=symbol, price=self._price(symbol))

    def get_positions(self) -> list[Position]:
        return [p for p in self._positions.values() if p.quantity != 0]

    def get_account(self) -> Account:
        return Account(broker=self.name, cash=self.cash, positions=self.get_positions())

    # writes ----------------------------------------------------------------
    def place_order(self, order: Order) -> Order:
        if not self._connected:
            raise BrokerError("paper broker not connected")
        price = order.limit_price or self._price(order.symbol)
        cost = price * order.quantity

        if order.side is Side.BUY:
            if cost > self.cash:
                order.status = OrderStatus.REJECTED
                order.reason = "insufficient paper cash"
                self.orders.append(order)
                return order
            self.cash -= cost
            self._apply_fill(order.symbol, order.quantity, price)
        else:  # SELL
            held = self._positions.get(order.symbol)
            if not held or held.quantity < order.quantity:
                order.status = OrderStatus.REJECTED
                order.reason = "insufficient shares to sell"
                self.orders.append(order)
                return order
            self.cash += cost
            self._apply_fill(order.symbol, -order.quantity, price)

        order.status = OrderStatus.FILLED
        order.filled_price = price
        order.broker = self.name
        self.orders.append(order)
        return order

    def _apply_fill(self, symbol: str, signed_qty: float, price: float) -> None:
        pos = self._positions.get(symbol)
        if pos is None:
            self._positions[symbol] = Position(symbol=symbol, quantity=signed_qty, avg_price=price, broker=self.name)
            return
        new_qty = pos.quantity + signed_qty
        if signed_qty > 0:  # buying more: weighted average cost
            total_cost = pos.cost_basis + signed_qty * price
            pos.avg_price = total_cost / new_qty if new_qty else price
        pos.quantity = new_qty
        if abs(pos.quantity) < 1e-9:
            self._positions.pop(symbol, None)
