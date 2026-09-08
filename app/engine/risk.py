"""Risk manager — the last line of defense before an order reaches a broker.

Every order, regardless of mode or broker, must pass approve(). It enforces
per-order and per-position dollar caps, a daily order-count circuit breaker, a
daily realized-loss halt, and an optional allowed-symbols whitelist.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from datetime import date, timedelta

from ..config import RiskLimits
from ..models import Account, Order, Side

# FINRA pattern-day-trader rule. Four or more DAY TRADES (a buy and a sell of
# the same security on the same session) inside five rolling business days
# flags the account; under $25,000 equity the broker then restricts it —
# typically closing-only for 90 days.
#
# This is not a preference the config can relax. It is enforced by the broker,
# and hitting it on the $184 sleeve would end the very experiment the sleeve
# exists to run: three round-trips on a Monday could leave the account
# restricted by Wednesday with nothing learned.
#
# Note the distinction the old counter missed entirely: 20 ORDERS a day is
# fine. Twenty orders that happen to be four same-day round trips is not.
PDT_EQUITY_FLOOR = 25_000.0
PDT_MAX_DAY_TRADES = 3          # the 4th inside the window is what flags you
PDT_WINDOW_DAYS = 5


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
        # (date, symbol) -> ORDERED sides filled that session. A set will not do
        # here: {"buy","sell"} says a round trip happened but not how many, and
        # buy/sell/buy/sell in one session is two day trades, not one.
        self._fills: dict[tuple[str, str], list[str]] = {}
        # Symbols carried in from a previous session. The first sell of one of
        # these closes an overnight position, which is not a day trade.
        self._overnight: set[str] = set()

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
            # Fill history OUTLIVES the day — the PDT window is five business
            # days, so clearing it here would reset the count every morning and
            # make the guard useless. Only drop what has aged out.
            keep = self._window_start(date.fromisoformat(today))
            self._fills = {k: v for k, v in self._fills.items()
                           if k[0] >= keep.isoformat()}

    def record_realized_loss(self, amount: float) -> None:
        """Feed realized P&L (negative = loss) so the daily halt can trip."""
        self._roll_day()
        if amount < 0:
            self._realized_loss += -amount

    def record_order(self, symbol: str | None = None, side: str | None = None) -> None:
        self._roll_day()
        self._orders_today += 1
        if symbol and side:
            self._fills.setdefault((self._day, symbol.upper()), []).append(side.lower())

    def mark_overnight(self, symbols) -> None:
        """Declare positions carried in from a previous session.

        Selling one of these closes an overnight position, not a same-day one,
        so it does not spend a PDT slot. Without this the counter would charge
        a slot for the ordinary swing exit this sleeve is meant to default to.
        """
        self._overnight = {s.upper() for s in symbols}

    # --- pattern day trading ------------------------------------------------
    @staticmethod
    def _window_start(ref: date, business_days: int = PDT_WINDOW_DAYS) -> date:
        """First day of a rolling window of N BUSINESS days, ref included.

        Walked rather than approximated. Five business days is 7 calendar days
        across one weekend and 9 across two; a fixed calendar offset is either
        too short (letting a 4th trade through) or too long (blocking a legal
        one). Holidays are not modelled, which only ever makes the window
        longer in calendar terms — the conservative direction.
        """
        seen, d = 1, ref
        while seen < business_days:
            d -= timedelta(days=1)
            if d.weekday() < 5:
                seen += 1
        return d

    @staticmethod
    def _count_round_trips(sides: list[str], overnight: bool = False) -> int:
        """Day trades in one symbol's session: open-then-close pairs.

        Consecutive same-side fills are one leg — three buys then a sell is a
        single day trade, not three. So compress to runs and pair them off.
        If the symbol was held overnight, a leading run of sells is closing
        yesterday's position and opens nothing, so it is dropped first.
        """
        runs: list[str] = []
        for s in sides:
            if not runs or runs[-1] != s:
                runs.append(s)
        if overnight and runs and runs[0] == "sell":
            runs.pop(0)
        return len(runs) // 2

    def day_trades_in_window(self, today: str | None = None) -> int:
        """Day trades inside the rolling five-business-day window."""
        ref = date.fromisoformat(today or self._today())
        start = self._window_start(ref)
        n = 0
        for (d, sym), sides in self._fills.items():
            try:
                when = date.fromisoformat(d)
            except ValueError:
                continue
            if start <= when <= ref:
                n += self._count_round_trips(sides, sym in self._overnight)
        return n

    def would_be_day_trade(self, order: Order) -> bool:
        """Would this order close a position opened in the same session?"""
        sym = order.symbol.upper()
        sides = self._fills.get((self._day, sym), [])
        if not sides:
            return False
        before = self._count_round_trips(sides, sym in self._overnight)
        side = "buy" if order.side is Side.BUY else "sell"
        after = self._count_round_trips(sides + [side], sym in self._overnight)
        return after > before

    def pdt_blocked(self, order: Order, equity: float) -> str | None:
        """Reason this order would trip the PDT rule, or None."""
        if equity >= PDT_EQUITY_FLOOR:
            return None
        if not self.would_be_day_trade(order):
            return None
        used = self.day_trades_in_window()
        if used < PDT_MAX_DAY_TRADES:
            return None
        return (f"pattern day trader: {used} day trade(s) already in the last "
                f"{PDT_WINDOW_DAYS} business days and equity ${equity:,.0f} is "
                f"under ${PDT_EQUITY_FLOOR:,.0f}. A 4th flags the account and "
                f"restricts it to closing-only. Hold this one overnight.")

    def pdt_slots(self, equity: float) -> dict:
        """What the agent needs to decide whether a day trade is worth it."""
        used = self.day_trades_in_window()
        exempt = equity >= PDT_EQUITY_FLOOR
        return {
            "applies": not exempt,
            "used": used,
            "remaining": None if exempt else max(0, PDT_MAX_DAY_TRADES - used),
            "max": PDT_MAX_DAY_TRADES,
            "window_business_days": PDT_WINDOW_DAYS,
            "equity": round(equity, 2),
        }

    # the gate --------------------------------------------------------------
    def approve(self, order: Order, account: Account, ref_price: float) -> RiskDecision:
        self._roll_day()
        L = self.limits

        if self.allowed_symbols and L.allowed_symbols_only and order.symbol not in self.allowed_symbols:
            return RiskDecision(False, f"{order.symbol} not in allowed symbols")

        if order.quantity <= 0:
            return RiskDecision(False, "non-positive quantity")

        # Caps are resolved against THIS account's equity, so the same limits
        # mean the same thing on the $184 agentic account, a $27k sleeve, or the
        # full book. ref_price stands in for symbols we hold but have no live
        # quote for; that only ever makes the cap tighter, never looser.
        equity = account.equity({p.symbol: ref_price for p in account.positions})
        order_cap = L.order_cap(equity)
        position_cap = L.position_cap(equity)
        loss_cap = L.daily_loss_cap(equity)

        notional = order.notional(ref_price)
        if notional > order_cap:
            return RiskDecision(False, f"order ${notional:.0f} exceeds order cap ${order_cap:.0f} "
                                       f"({L.max_order_pct:.0%} of ${equity:,.0f} equity)")

        pdt = self.pdt_blocked(order, equity)
        if pdt:
            return RiskDecision(False, pdt)

        if self._orders_today >= L.max_orders_per_day:
            return RiskDecision(False, f"daily order limit reached ({L.max_orders_per_day})")

        if self._realized_loss >= loss_cap:
            return RiskDecision(False, f"daily loss halt: ${self._realized_loss:.0f} >= ${loss_cap:.0f}")

        # Position cap applies to the resulting exposure after a buy.
        if order.side is Side.BUY:
            held = next((p for p in account.positions if p.symbol == order.symbol), None)
            held_value = held.market_value(ref_price) if held else 0.0
            projected = held_value + notional
            if projected > position_cap:
                return RiskDecision(
                    False,
                    f"position ${projected:.0f} would exceed position cap ${position_cap:.0f} "
                    f"({L.max_position_pct:.0%} of ${equity:,.0f} equity)",
                )
            if notional > account.cash:
                return RiskDecision(False, f"insufficient cash: need ${notional:.0f}, have ${account.cash:.0f}")

        return RiskDecision(True, "ok")

    def status(self, equity: float | None = None) -> dict:
        self._roll_day()
        out = {
            "orders_today": self._orders_today,
            "realized_loss": round(self._realized_loss, 2),
            "max_orders_per_day": self.limits.max_orders_per_day,
            "max_daily_loss": self.limits.max_daily_loss,
            "day_trades_used": self.day_trades_in_window(),
            "day_trade_max": PDT_MAX_DAY_TRADES,
        }
        if equity is not None:
            out["pdt"] = self.pdt_slots(equity)
        return out
