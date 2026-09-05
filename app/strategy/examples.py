"""Example strategies.

These are illustrative, not investment advice. They exist to prove out the
signal → risk → order pipeline end to end. Add your own by subclassing
Strategy and registering it in STRATEGY_REGISTRY.
"""
from __future__ import annotations

from typing import Any

from ..analysis import rsi, sma
from ..models import Side, Signal
from .base import Strategy, StrategyContext


class SMACrossover(Strategy):
    """Buy when the fast SMA crosses above the slow SMA; sell on the reverse."""
    name = "sma_crossover"

    def evaluate(self, ctx: StrategyContext) -> list[Signal]:
        fast = int(self.params.get("fast", 10))
        slow = int(self.params.get("slow", 30))
        h = ctx.history
        if len(h) < slow + 1:
            return []
        fast_s = sma(h, fast)
        slow_s = sma(h, slow)
        f_now, f_prev = fast_s[-1], fast_s[-2]
        s_now, s_prev = slow_s[-1], slow_s[-2]
        if None in (f_now, f_prev, s_now, s_prev):
            return []

        crossed_up = f_prev <= s_prev and f_now > s_now
        crossed_down = f_prev >= s_prev and f_now < s_now

        if crossed_up:
            return [Signal(ctx.symbol, Side.BUY, order_value=self.order_value(),
                           strategy=self.name, note=f"fast{fast} crossed above slow{slow}")]
        if crossed_down and ctx.position_qty > 0:
            return [Signal(ctx.symbol, Side.SELL, order_value=self.order_value(),
                           strategy=self.name, note=f"fast{fast} crossed below slow{slow}")]
        return []


class RSIReversion(Strategy):
    """Buy when RSI is oversold; sell when overbought (and holding)."""
    name = "rsi_reversion"

    def evaluate(self, ctx: StrategyContext) -> list[Signal]:
        period = int(self.params.get("period", 14))
        oversold = float(self.params.get("oversold", 30))
        overbought = float(self.params.get("overbought", 70))
        h = ctx.history
        if len(h) < period + 2:
            return []
        r = rsi(h, period)
        now, prev = r[-1], r[-2]
        if now is None or prev is None:
            return []
        if prev <= oversold < now:  # crossing up out of oversold
            return [Signal(ctx.symbol, Side.BUY, order_value=self.order_value(),
                           strategy=self.name, note=f"RSI {now:.0f} leaving oversold")]
        if prev >= overbought > now and ctx.position_qty > 0:  # crossing down out of overbought
            return [Signal(ctx.symbol, Side.SELL, order_value=self.order_value(),
                           strategy=self.name, note=f"RSI {now:.0f} leaving overbought")]
        return []


STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    SMACrossover.name: SMACrossover,
    RSIReversion.name: RSIReversion,
}


def build_strategy(name: str, params: dict[str, Any] | None = None) -> Strategy:
    if name not in STRATEGY_REGISTRY:
        raise KeyError(f"unknown strategy '{name}'. Known: {list(STRATEGY_REGISTRY)}")
    return STRATEGY_REGISTRY[name](params or {})
