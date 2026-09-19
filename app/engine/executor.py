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
import math
from dataclasses import dataclass, field
from typing import Optional

from ..config import TradingMode
from ..brokers.base import BrokerBase, BrokerError
from ..models import Order, OrderStatus, OrderType, Side, Signal
from . import blotter
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
                     order_type=OrderType.MARKET, strategy=sig.strategy,
                     reason=f"{sig.strategy}: {sig.note}")

    @staticmethod
    def _clamp_sell_to_position(order: Order, account) -> Optional[str]:
        """Size a SELL to what is actually held. Returns a rejection reason, or
        None if the order may proceed (possibly with a reduced quantity).

        Sizing is done in DOLLARS — `order_value / price` — and dollars know
        nothing about the position. So a $90 exit signal on a $2.30 stock asks
        for 39.1 shares of a 38-share holding, and the broker bounces the whole
        order. The position does not get exited, and the run records a rejection
        instead of a fill. Two of every three orders in the last paper run died
        this way, which starves the training record of the very evidence it
        exists to collect while telling you nothing about strategy quality.

        Clamping rather than rejecting is right because the intent of a sell
        here is "reduce or exit this position", and selling what you own honors
        that intent exactly. Nothing in this system shorts, so a sell larger
        than the holding is always an arithmetic artifact, never a view.

        Holding NOTHING is different in kind and is rejected: it means a signal
        fired on a name the book does not own. That is worth recording as
        evidence rather than silently discarding.
        """
        if order.side is not Side.SELL:
            return None
        held = 0.0
        for p in getattr(account, "positions", None) or []:
            if p.symbol == order.symbol:
                held = float(p.quantity)
                break
        if held <= 0:
            return f"no position in {order.symbol} to sell"
        if order.quantity <= held:
            return None
        # Floor rather than round: rounding a clamp UP past the holding would
        # reintroduce the same rejection it exists to prevent.
        clamped = math.floor(held * 10_000) / 10_000
        note = (f"sell sized {order.quantity:g} > {held:g} held — "
                f"clamped to position")
        order.quantity = clamped
        order.reason = f"{order.reason}; {note}" if order.reason else note
        log.info("%s: %s", order.symbol, note)
        return None

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

        clamp = self._clamp_sell_to_position(order, account)
        if clamp is not None:
            order.status = OrderStatus.REJECTED
            order.reason = clamp
            self.history.append(order)
            blotter.record(order, self.mode.value, ref_price)
            return ExecutionResult(order, False, order.reason)

        decision = self.risk.approve(order, account, ref_price)
        if not decision.approved:
            order.status = OrderStatus.REJECTED
            order.reason = f"risk: {decision.reason}"
            self.history.append(order)
            # Recorded too: what the strategy WANTED and the guardrail stopped
            # is evidence. Keeping only the orders that got through is how a
            # simulation acquires an imaginary hit rate.
            blotter.record(order, self.mode.value, ref_price)
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
        self.history.append(result)
        accepted = result.status in (OrderStatus.FILLED, OrderStatus.SUBMITTED)

        # The entry plan is written BEFORE the blotter row, because the row
        # copies the plan into its thesis. Recording the fill first left every
        # buy with an empty thesis — the plan existed a millisecond too late.
        if accepted and result.side is Side.BUY and result.filled_price:
            try:
                from ..agent import position_plans
                position_plans.record(
                    result.symbol, float(result.filled_price),
                    float(result.quantity),
                    strategy=getattr(result, "strategy", "unknown"))
            except Exception:
                log.exception("could not record the entry plan for %s", result.symbol)

        # Durable record. self.history dies with the process, so without this
        # a week of paper runs leaves no evidence anything ever traded.
        blotter.record(result, self.mode.value)
        # Symbol and side are what make this countable as a day trade; without
        # them the risk manager can only count orders, and orders are not day
        # trades. Recorded only when the broker took it — a rejected order
        # spends no PDT slot.
        if accepted:
            self.risk.record_order(result.symbol, result.side.value,
                                   sleeve=getattr(result, "strategy", None))
        else:
            self.risk.record_order()
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
        order.reason = "declined at approval"
        blotter.record(order, self.mode.value)
        return True
