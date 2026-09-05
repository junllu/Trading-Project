"""Robinhood broker adapter (unofficial, via robin_stocks).

robin_stocks is imported lazily so the portal runs without it in paper mode.
Credentials come from the encrypted vault or environment. Robinhood has no
official API; this talks to its private endpoints and can break at any time.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from ..models import Account, Order, OrderStatus, OrderType, Position, Quote, Side
from ..security import load_credentials
from .base import BrokerBase, BrokerError


class RobinhoodBroker(BrokerBase):
    name = "robinhood"

    def __init__(self, config: Optional[dict[str, Any]] = None):
        self.config = config or {}
        self._connected = False
        self._rh = None  # the robin_stocks module

    def _lib(self):
        if self._rh is None:
            try:
                import robin_stocks.robinhood as rh  # type: ignore
            except Exception as exc:  # pragma: no cover - depends on optional dep
                raise BrokerError(
                    "robin_stocks is not installed. Run: pip install robin-stocks"
                ) from exc
            self._rh = rh
        return self._rh

    def _creds(self) -> dict[str, str]:
        vault = load_credentials().get("robinhood", {})
        return {
            "username": vault.get("username") or os.getenv("ROBINHOOD_USERNAME", ""),
            "password": vault.get("password") or os.getenv("ROBINHOOD_PASSWORD", ""),
            "mfa": vault.get("mfa_secret") or os.getenv("ROBINHOOD_MFA_SECRET", ""),
        }

    def connect(self) -> None:
        if self._connected:
            return
        rh = self._lib()
        creds = self._creds()
        if not creds["username"] or not creds["password"]:
            raise BrokerError("Robinhood credentials missing (set them in .env or the vault)")
        kwargs: dict[str, Any] = {}
        if creds["mfa"]:
            try:
                import pyotp  # type: ignore
                kwargs["mfa_code"] = pyotp.TOTP(creds["mfa"]).now()
            except Exception:
                pass
        rh.login(creds["username"], creds["password"], **kwargs)
        self._connected = True

    def is_connected(self) -> bool:
        return self._connected

    def get_quote(self, symbol: str) -> Quote:
        rh = self._lib()
        price = rh.stocks.get_latest_price(symbol)
        if not price or price[0] is None:
            raise BrokerError(f"no quote for {symbol}")
        return Quote(symbol=symbol, price=float(price[0]))

    def get_positions(self) -> list[Position]:
        rh = self._lib()
        out: list[Position] = []
        for h in rh.account.build_holdings().items() if hasattr(rh.account, "build_holdings") else []:
            symbol, info = h
            out.append(
                Position(
                    symbol=symbol,
                    quantity=float(info.get("quantity", 0)),
                    avg_price=float(info.get("average_buy_price", 0)),
                    broker=self.name,
                )
            )
        return out

    def get_account(self) -> Account:
        rh = self._lib()
        profile = rh.profiles.load_account_profile()
        cash = float(profile.get("cash", 0) or profile.get("buying_power", 0) or 0)
        return Account(broker=self.name, cash=cash, positions=self.get_positions())

    def place_order(self, order: Order) -> Order:
        rh = self._lib()
        try:
            if order.side is Side.BUY:
                if order.order_type is OrderType.LIMIT and order.limit_price:
                    res = rh.orders.order_buy_limit(order.symbol, order.quantity, order.limit_price)
                else:
                    res = rh.orders.order_buy_market(order.symbol, order.quantity)
            else:
                if order.order_type is OrderType.LIMIT and order.limit_price:
                    res = rh.orders.order_sell_limit(order.symbol, order.quantity, order.limit_price)
                else:
                    res = rh.orders.order_sell_market(order.symbol, order.quantity)
        except Exception as exc:  # pragma: no cover - network
            order.status = OrderStatus.REJECTED
            order.reason = f"robinhood error: {exc}"
            return order

        if isinstance(res, dict) and res.get("id"):
            order.status = OrderStatus.SUBMITTED
            order.broker = self.name
            order.reason = res.get("state")
        else:
            order.status = OrderStatus.REJECTED
            order.reason = str(res)
        return order
