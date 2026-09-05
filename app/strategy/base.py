"""Strategy interface.

A strategy is a pure function of market history to signals. Keeping it side-effect
free means strategies are easy to unit-test and to back-test later.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..models import Signal


@dataclass
class StrategyContext:
    """Everything a strategy is allowed to see."""
    symbol: str
    history: list[float]                       # closes, oldest first
    position_qty: float = 0.0                  # current holding in this symbol
    params: dict[str, Any] = field(default_factory=dict)


class Strategy(ABC):
    name: str = "base"

    def __init__(self, params: dict[str, Any] | None = None):
        self.params = params or {}

    @abstractmethod
    def evaluate(self, ctx: StrategyContext) -> list[Signal]:
        """Return zero or more signals for the given symbol/context."""

    # helpers ---------------------------------------------------------------
    def order_value(self) -> float:
        return float(self.params.get("order_value", 0))
