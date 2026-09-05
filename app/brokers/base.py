"""Broker abstraction.

Every broker — paper, Robinhood, Webull — implements this interface, so the
rest of the portal (portfolio, executor, dashboard) never needs to know which
brokerage it's talking to.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Account, Order, Position, Quote


class BrokerError(Exception):
    """Raised when a broker call fails (auth, network, rejected order)."""


class BrokerBase(ABC):
    name: str = "base"

    @abstractmethod
    def connect(self) -> None:
        """Authenticate. Should be idempotent."""

    @abstractmethod
    def is_connected(self) -> bool:
        ...

    @abstractmethod
    def get_account(self) -> Account:
        ...

    @abstractmethod
    def get_positions(self) -> list[Position]:
        ...

    @abstractmethod
    def get_quote(self, symbol: str) -> Quote:
        ...

    @abstractmethod
    def place_order(self, order: Order) -> Order:
        """Submit an order and return it with an updated status."""

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """Default fan-out; brokers may override with a batch endpoint."""
        return {s: self.get_quote(s) for s in symbols}
