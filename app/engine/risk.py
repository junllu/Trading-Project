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
        # Entry budget SPENT PER SLEEVE.
        #
        # One shared counter meant the sleeve that happened to iterate first
        # consumed the day: in one session 20 of 21 rejections were "daily order
        # limit reached", daily_agent took 23 fills, and sma_crossover got 1
        # from 8 attempts. That is not an outcome, it is an ordering artifact —
        # and n=1 can neither convict nor acquit a strategy. Separate budgets
        # mean each sleeve accumulates a sample that can actually be measured,
        # and none can starve another.
        self._orders_by_sleeve: dict[str, int] = {}
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
            self._orders_by_sleeve.clear()
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

    def sleeve_budget(self, sleeve: str) -> int:
        """Entry orders this sleeve may place today.

        From `risk.sleeve_budgets` in config when present, otherwise an even
        split of the daily cap across the known sleeves. An unlisted sleeve
        still gets a share rather than zero — silently giving a new strategy no
        budget would look exactly like the strategy never signalling.
        """
        configured = getattr(self.limits, "sleeve_budgets", None) or {}
        if sleeve in configured:
            return int(configured[sleeve])
        known = max(1, len(configured) or len(self._orders_by_sleeve) or 1)
        return max(1, self.limits.max_orders_per_day // max(known, 2))

    def record_order(self, symbol: str | None = None, side: str | None = None,
                     sleeve: str | None = None) -> None:
        self._roll_day()
        self._orders_today += 1
        if (side or "").lower() == "buy":
            key = (sleeve or "unknown").lower()
            self._orders_by_sleeve[key] = self._orders_by_sleeve.get(key, 0) + 1
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

        # The allowlist governs what may be BOUGHT. Applied to sells it stops
        # you exiting something you already hold — the book contains 30 names
        # and the watchlist 12, so every position outside the watchlist became
        # unsellable. BE, flagged EXIT on a DEAD theme, was refused on exactly
        # this line for two days running.
        #
        # Third instance of one pattern today: the order-value cap, the daily
        # order budget and now the allowlist were all written to limit NEW risk
        # and all applied to risk-REDUCING orders, where they trap exposure
        # instead of limiting it. A control on buying is not a control on
        # selling, and treating them as one is how a guardrail becomes the
        # hazard.
        if (order.side is Side.BUY and self.allowed_symbols
                and L.allowed_symbols_only
                and order.symbol not in self.allowed_symbols):
            return RiskDecision(False, f"{order.symbol} not in allowed symbols")

        if order.quantity <= 0:
            return RiskDecision(False, "non-positive quantity")

        # Caps are resolved against THIS account's equity, so the same limits
        # mean the same thing on the $184 agentic account, a $27k sleeve, or the
        # full book. ref_price stands in for symbols we hold but have no live
        # quote for; that only ever makes the cap tighter, never looser.
        # Only the symbol being traded gets the live price. Everything else is
        # valued at its own cost basis, which is what Account.equity() falls
        # back to for anything absent from this dict.
        #
        # The previous form mapped EVERY held symbol to this order's ref_price,
        # so the whole book was repriced at whatever the current name happened
        # to trade at. One tick produced equity of $181k, $322k and $240k for
        # three different orders against a real book of $136k, and every
        # percentage cap was computed off that. It also did the opposite of
        # what its comment claimed: a high-priced symbol inflated equity and
        # made the caps LOOSER, and is_test_sleeve() keyed off the same number.
        equity = account.equity({order.symbol: ref_price})
        order_cap = L.order_cap(equity)
        position_cap = L.position_cap(equity)
        loss_cap = L.daily_loss_cap(equity)

        notional = order.notional(ref_price)
        # The per-order cap governs NEW risk, so it applies to buys only.
        #
        # Applying it to sells inverts the guardrail: a $19,000 position could
        # not be exited under a $2,000 cap, so the cap that exists to protect
        # capital was the thing preventing capital from being protected. That is
        # not hypothetical — the exit monitor's DEAD-theme EXIT on a $18,965
        # position was refused by this line, and in a fast drawdown a cap that
        # blocks the exit is the failure that ends a campaign.
        #
        # Everything that limits LOSS still applies to sells: the daily realized
        # loss halt, the day-trade guard, the order-count breaker and the
        # kill-switch are all below and none of them are skipped here.
        if order.side is Side.BUY and notional > order_cap:
            return RiskDecision(False, f"order ${notional:.0f} exceeds order cap ${order_cap:.0f} "
                                       f"({L.max_order_pct:.0%} of ${equity:,.0f} equity)")

        pdt = self.pdt_blocked(order, equity)
        if pdt:
            return RiskDecision(False, pdt)

        # The daily order budget governs NEW risk, so only buys spend it — the
        # same reasoning as the order-value cap above. A sell that cannot be
        # placed because entries used the last slot is a risk failure: the
        # budget existed to limit exposure and would instead be trapping it.
        if order.side is Side.BUY:
            sleeve = (getattr(order, "strategy", None) or "unknown").lower()
            spent = self._orders_by_sleeve.get(sleeve, 0)
            cap = self.sleeve_budget(sleeve)
            if spent >= cap:
                return RiskDecision(
                    False, f"{sleeve} used its daily entry budget ({spent}/{cap})")
            if self._orders_today >= L.max_orders_per_day:
                return RiskDecision(False,
                                    f"daily order limit reached ({L.max_orders_per_day})")

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
            "entries_by_sleeve": dict(self._orders_by_sleeve),
            "sleeve_budgets": {s: self.sleeve_budget(s)
                               for s in set(self._orders_by_sleeve)
                               | set(getattr(self.limits, "sleeve_budgets", None) or {})},
            "realized_loss": round(self._realized_loss, 2),
            "max_orders_per_day": self.limits.max_orders_per_day,
            "max_daily_loss": self.limits.max_daily_loss,
            "day_trades_used": self.day_trades_in_window(),
            "day_trade_max": PDT_MAX_DAY_TRADES,
        }
        if equity is not None:
            out["pdt"] = self.pdt_slots(equity)
        return out
