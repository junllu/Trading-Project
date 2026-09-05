"""Risk manager — the last line of defense before an order reaches a broker.

Every order, regardless of mode or broker, must pass approve(). It enforces
per-order and per-position dollar caps, a daily order-count circuit breaker, a
daily realized-loss halt, and an optional allowed-symbols whitelist.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from ..config import RiskLimits
from ..models import Account, Order, Side


@dataclass
class RiskDecision:
    approved: bool
    reason: str = ""


class RiskManager:
    def __init__(self, limits: RiskLimits, allowed_symbols: list[str] | None = None):
        self.limits = limits
        self.allowed_symbols = set(allowed_symbols or [])
        self._orders_today = 0
        self._realized_loss = 0.0
        self._day = self._today()

    # daily bookkeeping -----------------------------------------------------
    @staticmethod
    def _today() -> str:
        return time.strftime("%Y-%m-%d")

    def _roll_day(self) -> None:
        today = self._today()
        if today != self._day:
            self._day = today
            self._orders_today = 0
            self._realized_loss = 0.0

    def record_realized_loss(self, amount: float) -> None:
        """Feed realized P&L (negative = loss) so the daily halt can trip."""
        self._roll_day()
        if amount < 0:
            self._realized_loss += -amount

    def record_order(self) -> None:
        self._roll_day()
        self._orders_today += 1

    # the gate --------------------------------------------------------------
    def approve(self, order: Order, account: Account, ref_price: float) -> RiskDecision:
        self._roll_day()
        L = self.limits

        if self.allowed_symbols and L.allowed_symbols_only and order.symbol not in self.allowed_symbols:
            return RiskDecision(False, f"{order.symbol} not in allowed symbols")

        if order.quantity <= 0:
            return RiskDecision(False, "non-positive quantity")

        notional = order.notional(ref_price)
        if notional > L.max_order_value:
            return RiskDecision(False, f"order ${notional:.0f} exceeds max_order_value ${L.max_order_value:.0f}")

        if self._orders_today >= L.max_orders_per_day:
            return RiskDecision(False, f"daily order limit reached ({L.max_orders_per_day})")

        if self._realized_loss >= L.max_daily_loss:
            return RiskDecision(False, f"daily loss halt: ${self._realized_loss:.0f} >= ${L.max_daily_loss:.0f}")

        # Position cap applies to the resulting exposure after a buy.
        if order.side is Side.BUY:
            held = next((p for p in account.positions if p.symbol == order.symbol), None)
            held_value = held.market_value(ref_price) if held else 0.0
            projected = held_value + notional
            if projected > L.max_position_value:
                return RiskDecision(
                    False,
                    f"position ${projected:.0f} would exceed max_position_value ${L.max_position_value:.0f}",
                )
            if notional > account.cash:
                return RiskDecision(False, f"insufficient cash: need ${notional:.0f}, have ${account.cash:.0f}")

        return RiskDecision(True, "ok")

    def status(self) -> dict:
        self._roll_day()
        return {
            "orders_today": self._orders_today,
            "realized_loss": round(self._realized_loss, 2),
            "max_orders_per_day": self.limits.max_orders_per_day,
            "max_daily_loss": self.limits.max_daily_loss,
        }
