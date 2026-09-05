"""Webull broker adapter (unofficial, via the `webull` package).

Like Robinhood, Webull has no official API. First login typically needs a
device id and an emailed/SMS security code, plus a 6-digit trade PIN to arm
order placement. The library is imported lazily so paper mode needs none of it.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from ..models import Account, Order, OrderStatus, OrderType, Position, Quote, Side
from ..security import load_credentials
from .base import BrokerBase, BrokerError


class WebullBroker(BrokerBase):
    name = "webull"

    def __init__(self, config: Optional[dict[str, Any]] = None):
        self.config = config or {}
        self._connected = False
        self._wb = None

    def _client(self):
        if self._wb is None:
            try:
                from webull import webull  # type: ignore
            except Exception as exc:  # pragma: no cover - optional dep
                raise BrokerError("webull is not installed. Run: pip install webull") from exc
            self._wb = webull()
        return self._wb

    def _creds(self) -> dict[str, str]:
        vault = load_credentials().get("webull", {})
        return {
            "username": vault.get("username") or os.getenv("WEBULL_USERNAME", ""),
            "password": vault.get("password") or os.getenv("WEBULL_PASSWORD", ""),
            "trade_pin": vault.get("trade_pin") or os.getenv("WEBULL_TRADE_PIN", ""),
            "device_id": vault.get("device_id") or os.getenv("WEBULL_DEVICE_ID", ""),
        }

    def connect(self) -> None:
        if self._connected:
            return
        wb = self._client()
        creds = self._creds()
        if not creds["username"] or not creds["password"]:
            raise BrokerError("Webull credentials missing (set them in .env or the vault)")
        if creds["device_id"]:
            wb._did = creds["device_id"]
        result = wb.login(creds["username"], creds["password"])
        if isinstance(result, dict) and result.get("accessToken"):
            self._connected = True
        else:
            raise BrokerError(f"Webull login failed / needs security code: {result}")

    def is_connected(self) -> bool:
        return self._connected

    def get_quote(self, symbol: str) -> Quote:
        wb = self._client()
        data = wb.get_quote(stock=symbol)
        price = data.get("close") or data.get("pPrice") if isinstance(data, dict) else None
        if price is None:
            raise BrokerError(f"no quote for {symbol}")
        return Quote(symbol=symbol, price=float(price))

    def get_positions(self) -> list[Position]:
        wb = self._client()
        out: list[Position] = []
        for p in wb.get_positions() or []:
            try:
                out.append(
                    Position(
                        symbol=p["ticker"]["symbol"],
                        quantity=float(p.get("position", 0)),
                        avg_price=float(p.get("costPrice", 0)),
                        broker=self.name,
                    )
                )
            except (KeyError, TypeError):
                continue
        return out

    def get_account(self) -> Account:
        wb = self._client()
        acct = wb.get_account()
        cash = 0.0
        if isinstance(acct, dict):
            for item in acct.get("accountMembers", []):
                if item.get("key") in ("cashBalance", "usableCash"):
                    cash = float(item.get("value", 0))
                    break
        return Account(broker=self.name, cash=cash, positions=self.get_positions())

    def place_order(self, order: Order) -> Order:
        wb = self._client()
        creds = self._creds()
        try:
            if creds["trade_pin"]:
                wb.get_trade_token(creds["trade_pin"])
            res = wb.place_order(
                stock=order.symbol,
                action=order.side.value.upper(),
                orderType="MKT" if order.order_type is OrderType.MARKET else "LMT",
                quant=int(order.quantity),
                price=order.limit_price or 0,
                enforce="DAY",
            )
        except Exception as exc:  # pragma: no cover - network
            order.status = OrderStatus.REJECTED
            order.reason = f"webull error: {exc}"
            return order

        if isinstance(res, dict) and res.get("success", False):
            order.status = OrderStatus.SUBMITTED
            order.broker = self.name
        else:
            order.status = OrderStatus.REJECTED
            order.reason = str(res)
        return order
