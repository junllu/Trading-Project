"""Execution engine.

Turns signals into orders, runs them through the risk manager, and routes the
survivors to a broker according to the trading mode:

  paper   -> filled on the simulated broker
  confirm -> queued, awaiting manual approval via approve_pending()
  live    -> sent to the real broker

A global kill-switch halts everything instantly. Nothing here bypasses the
risk manager — not even live mode.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from ..config import TradingMode
from ..brokers.base import BrokerBase, BrokerError
from ..models import Order, OrderStatus, OrderType, Side, Signal
from .risk import RiskManager

log = logging.getLogger("portal.executor")


@dataclass
class ExecutionResult:
    order: Order
    accepted: bool
    detail: str = ""


@dataclass
class Executor:
    risk: RiskManager
    mode: TradingMode = TradingMode.PAPER
    paper_broker: Optional[BrokerBase] = None      # always used in paper mode
    live_brokers: dict[str, BrokerBase] = field(default_factory=dict)
    killed: bool = False
    default_live_broker: str = "robinhood"

    pending: list[Order] = field(default_factory=list)   # confirm-mode queue
    history: list[Order] = field(default_factory=list)    # everything we acted on

    # kill-switch -----------------------------------------------------------
    def kill(self) -> None:
        self.killed = True
        log.warning("KILL-SWITCH ENGAGED — all execution halted")

    def resume(self) -> None:
        self.killed = False
        log.warning("kill-switch released — execution resumed")

    # signal -> order -------------------------------------------------------
    @staticmethod
    def signal_to_order(sig: Signal, ref_price: float) -> Optional[Order]:
        if sig.order_value and ref_price > 0:
            qty = sig.order_value / ref_price
            qty = float(f"{qty:.4f}")  # fractional shares allowed
        else:
            qty = 0.0
        if qty <= 0:
            return None
        return Order(symbol=sig.symbol, side=sig.side, quantity=qty,
                     order_type=OrderType.MARKET, reason=f"{sig.strategy}: {sig.note}")

    def _broker_for(self, order: Order) -> BrokerBase:
        if self.mode is TradingMode.PAPER:
            assert self.paper_broker is not None, "paper broker not configured"
            return self.paper_broker
        # live/confirm: prefer the named default, else the first available
        if self.default_live_broker in self.live_brokers:
            return self.live_brokers[self.default_live_broker]
        if self.live_brokers:
            return next(iter(self.live_brokers.values()))
        # Fail safe: never silently trade if no live broker — fall back to paper.
        assert self.paper_broker is not None
        return self.paper_broker

    # main entry ------------------------------------------------------------
    def handle_signal(self, sig: Signal, ref_price: float) -> ExecutionResult:
        if self.killed:
            o = Order(symbol=sig.symbol, side=sig.side, quantity=0)
            o.status = OrderStatus.REJECTED
            o.reason = "kill-switch engaged"
            return ExecutionResult(o, False, "kill-switch engaged")

        order = self.signal_to_order(sig, ref_price)
        if order is None:
            o = Order(symbol=sig.symbol, side=sig.side, quantity=0)
            o.status = OrderStatus.REJECTED
            o.reason = "could not size order (no order_value / price)"
            return ExecutionResult(o, False, o.reason)

        broker = self._broker_for(order)
        try:
            account = broker.get_account()
        except BrokerError as exc:
            order.status = OrderStatus.REJECTED
            order.reason = f"account fetch failed: {exc}"
            return ExecutionResult(order, False, order.reason)

        decision = self.risk.approve(order, account, ref_price)
        if not decision.approved:
            order.status = OrderStatus.REJECTED
            order.reason = f"risk: {decision.reason}"
            self.history.append(order)
            return ExecutionResult(order, False, order.reason)

        if self.mode is TradingMode.CONFIRM:
            order.status = OrderStatus.QUEUED
            order.broker = broker.name
            self.pending.append(order)
            self.history.append(order)
            return ExecutionResult(order, True, "queued for manual confirmation")

        return self._submit(order, broker)

    def _submit(self, order: Order, broker: BrokerBase) -> ExecutionResult:
        if not broker.is_connected():
            try:
                broker.connect()
            except BrokerError as exc:
                order.status = OrderStatus.REJECTED
                order.reason = f"connect failed: {exc}"
                self.history.append(order)
                return ExecutionResult(order, False, order.reason)
        result = broker.place_order(order)
        self.risk.record_order()
        self.history.append(result)
        accepted = result.status in (OrderStatus.FILLED, OrderStatus.SUBMITTED)
        return ExecutionResult(result, accepted, result.reason or result.status.value)

    # confirm-mode approval -------------------------------------------------
    def approve_pending(self, order_id: str, ref_price: float) -> ExecutionResult:
        order = next((o for o in self.pending if o.id == order_id), None)
        if order is None:
            dummy = Order(symbol="?", side=Side.BUY, quantity=0)
            dummy.status = OrderStatus.REJECTED
            return ExecutionResult(dummy, False, "order not found in pending queue")
        self.pending.remove(order)
        broker = self._broker_for(order)
        return self._submit(order, broker)

    def reject_pending(self, order_id: str) -> bool:
        order = next((o for o in self.pending if o.id == order_id), None)
        if order is None:
            return False
        self.pending.remove(order)
        order.status = OrderStatus.CANCELLED
        return True
