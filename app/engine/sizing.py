"""Position sizing for the execution algorithm.

Refines "how much to trade" from a flat dollar amount into something risk-aware:

  size = base_budget × conviction_strength × volatility_scalar

- conviction_strength scales linearly above an entry threshold, so weak signals
  trade small (or not at all) and strong ones get more capital.
- volatility_scalar shrinks size for jumpy names (a light risk-parity nudge),
  using recent realized volatility from the price history.
- hard caps keep any single order within the risk manager's limits anyway.

Everything is deterministic and unit-tested; the risk manager still has final
say before any order is sent.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..models import Side, Signal


@dataclass
class SizingParams:
    base_budget: float = 1000.0        # $ at full conviction, average volatility
    entry_threshold: float = 0.2       # |conviction| below this -> no trade
    max_budget: float = 2000.0         # $ ceiling per order
    target_vol: float = 0.30           # annualized vol the scalar is calibrated to
    min_vol_scalar: float = 0.4
    max_vol_scalar: float = 1.5


def realized_vol(history: list[float], lookback: int = 20) -> float:
    """Annualized realized volatility from daily closes (0 if not enough data)."""
    if len(history) < 3:
        return 0.0
    window = history[-(lookback + 1):]
    rets = [(window[i] / window[i - 1] - 1.0) for i in range(1, len(window)) if window[i - 1]]
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252)


def vol_scalar(history: list[float], params: SizingParams) -> float:
    vol = realized_vol(history)
    if vol <= 0:
        return 1.0
    return max(params.min_vol_scalar, min(params.max_vol_scalar, params.target_vol / vol))


def size_order_value(conviction: float, history: list[float],
                     params: SizingParams | None = None) -> float:
    """Return the $ notional to trade for a given conviction (-1..1). 0 => skip."""
    p = params or SizingParams()
    strength = abs(conviction)
    if strength < p.entry_threshold:
        return 0.0
    # linear ramp from threshold..1 -> 0..1
    ramp = (strength - p.entry_threshold) / (1.0 - p.entry_threshold)
    value = p.base_budget * ramp * vol_scalar(history, p)
    return round(min(value, p.max_budget), 2)


def conviction_to_signal(symbol: str, conviction: float, history: list[float],
                         strategy: str = "conviction", note: str = "",
                         params: SizingParams | None = None) -> Signal | None:
    order_value = size_order_value(conviction, history, params)
    if order_value <= 0:
        return None
    side = Side.BUY if conviction > 0 else Side.SELL
    return Signal(symbol=symbol, side=side, strength=abs(conviction),
                  order_value=order_value, strategy=strategy,
                  note=note or f"conviction {conviction:+.2f}, sized ${order_value:.0f}")
