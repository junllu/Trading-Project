"""No sleeve may starve another, and no entry may block an exit.

The failure: one shared order counter meant whichever sleeve iterated first
spent the day. In a single session 20 of 21 rejections were "daily order limit
reached", daily_agent took 23 fills, and sma_crossover got 1 from 8 attempts —
an ordering artifact that reads exactly like a strategy with no signals, and
n=1 can neither convict nor acquit it.
"""
import pytest

from app.config import RiskLimits
from app.engine.risk import RiskManager
from app.models import Account, Order, Position, Side


def _mgr(**kw):
    lim = RiskLimits(max_order_value=10_000, max_position_value=100_000,
                     max_order_pct=1.0, max_position_pct=1.0,
                     max_orders_per_day=20,
                     sleeve_budgets={"alpha": 2, "beta": 2}, **kw)
    return RiskManager(lim)


def _acct():
    return Account(broker="paper", cash=100_000.0,
                   positions=[Position(symbol="AAA", quantity=100, avg_price=10.0)])


def _buy(sleeve):
    return Order(symbol="AAA", side=Side.BUY, quantity=1, strategy=sleeve)


def test_a_sleeve_spending_its_budget_does_not_starve_another():
    rm = _mgr()
    acct = _acct()
    for _ in range(2):
        assert rm.approve(_buy("alpha"), acct, 10.0).approved
        rm.record_order("AAA", "buy", sleeve="alpha")

    # alpha is spent...
    d = rm.approve(_buy("alpha"), acct, 10.0)
    assert not d.approved and "entry budget" in d.reason
    # ...but beta is untouched.
    assert rm.approve(_buy("beta"), acct, 10.0).approved


def test_sells_are_never_blocked_by_the_entry_budget():
    """A blocked exit traps risk rather than limiting it."""
    rm = _mgr()
    acct = _acct()
    for _ in range(2):
        rm.record_order("AAA", "buy", sleeve="alpha")
    assert not rm.approve(_buy("alpha"), acct, 10.0).approved

    sell = Order(symbol="AAA", side=Side.SELL, quantity=50, strategy="alpha")
    assert rm.approve(sell, acct, 10.0).approved, "an exit was refused by an entry budget"


def test_sells_do_not_consume_a_sleeves_entry_budget():
    rm = _mgr()
    acct = _acct()
    for _ in range(5):
        rm.record_order("AAA", "sell", sleeve="alpha")
    assert rm.approve(_buy("alpha"), acct, 10.0).approved


def test_an_unlisted_sleeve_gets_a_share_not_zero():
    """Silently giving a new strategy no budget looks identical to that
    strategy never signalling."""
    rm = _mgr()
    assert rm.sleeve_budget("brand_new") >= 1
    assert rm.approve(_buy("brand_new"), _acct(), 10.0).approved


def test_budgets_reset_with_the_day():
    rm = _mgr()
    rm.record_order("AAA", "buy", sleeve="alpha")
    rm._day = "1999-01-01"
    rm._roll_day()
    assert rm._orders_by_sleeve == {}


def test_a_held_name_outside_the_watchlist_can_still_be_SOLD():
    """The allowlist governs what may be BOUGHT.

    The book holds 30 names and the watchlist lists 12, so applying it to sells
    made every position outside the watchlist unsellable — BE, flagged EXIT on a
    DEAD theme, was refused on this rule for two days.
    """
    lim = RiskLimits(max_order_value=100_000, max_position_value=1_000_000,
                     max_order_pct=1.0, max_position_pct=1.0,
                     allowed_symbols_only=True)
    rm = RiskManager(lim, allowed_symbols=["AAA"])          # BE not listed
    acct = Account(broker="paper", cash=0.0,
                   positions=[Position(symbol="BE", quantity=75, avg_price=250.0)])

    sell = Order(symbol="BE", side=Side.SELL, quantity=75, strategy="exit_monitor")
    assert rm.approve(sell, acct, 250.0).approved, "a held name could not be exited"

    buy = Order(symbol="BE", side=Side.BUY, quantity=1, strategy="daily_agent")
    d = rm.approve(buy, acct, 250.0)
    assert not d.approved and "not in allowed symbols" in d.reason


def test_status_exposes_spend_per_sleeve():
    rm = _mgr()
    rm.record_order("AAA", "buy", sleeve="alpha")
    s = rm.status()
    assert s["entries_by_sleeve"]["alpha"] == 1
    assert s["sleeve_budgets"]["alpha"] == 2
