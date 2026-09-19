"""Equity must not be fabricated from the traded symbol's price.

The bug: `approve()` valued EVERY held position at the current order's
ref_price, so the whole book was repriced at whatever name happened to be
trading. In one tick that produced equity of $181k, $322k and $240k for three
orders against a real book of $136k. Every percentage cap — order, position,
daily loss — was computed off that, and `is_test_sleeve()` keyed off it too.

The comment above the line claimed it "only ever makes the cap tighter, never
looser". A high-priced symbol inflates equity and loosens every cap, so the
code did the opposite of what it documented.
"""
import pytest

from app.config import RiskLimits
from app.engine.risk import RiskManager
from app.models import Account, Order, Position, Side


def _account():
    return Account(
        broker="paper",
        cash=1_000.0,
        positions=[
            Position(symbol="CHEAP", quantity=100, avg_price=1.0),    # $100
            Position(symbol="RICH", quantity=10, avg_price=100.0),    # $1,000
        ],
    )


def _equity_seen(order_symbol, ref_price):
    """Recover the equity the risk manager used, from its own rejection text."""
    rm = RiskManager(RiskLimits(max_order_value=1, max_order_pct=0.0001))
    o = Order(symbol=order_symbol, side=Side.BUY, quantity=1)
    d = rm.approve(o, _account(), ref_price)
    assert not d.approved
    # "... (0% of $X equity)"
    return float(d.reason.split("of $")[1].split(" ")[0].replace(",", ""))


def test_equity_is_stable_regardless_of_which_symbol_is_traded():
    """The book is worth what it is worth. Trading a $500 name does not make it
    richer than trading a $1 name."""
    cheap = _equity_seen("CHEAP", 1.0)
    rich = _equity_seen("RICH", 500.0)
    # Only RICH's own 10 shares reprice; the rest holds at cost basis.
    assert cheap == pytest.approx(1_000 + 100 + 1_000)          # 2,100
    assert rich == pytest.approx(1_000 + 100 + 10 * 500)        # 6,100
    # The old bug repriced EVERYTHING at ref_price: 100*500 + 10*500 = 55,000.
    assert rich < 10_000, "whole book was repriced at the traded symbol's price"


def test_untraded_positions_hold_their_cost_basis():
    eq = _equity_seen("RICH", 100.0)
    assert eq == pytest.approx(1_000 + 100 + 1_000)


def test_a_large_sell_is_not_blocked_by_the_order_cap():
    """The cap governs NEW risk. Applying it to sells inverted the guardrail:
    a $19k position could not be exited under a $2k cap, so the rule meant to
    protect capital was the thing preventing capital from being protected."""
    rm = RiskManager(RiskLimits(max_order_value=2_000, max_order_pct=1.0))
    acct = Account(broker="paper", cash=0.0,
                   positions=[Position(symbol="BE", quantity=75, avg_price=253.0)])
    sell = Order(symbol="BE", side=Side.SELL, quantity=75)
    assert rm.approve(sell, acct, 253.0).approved, "a large exit was refused"


def test_a_large_buy_is_still_blocked_by_the_order_cap():
    """The exemption must not leak to the side that adds risk."""
    rm = RiskManager(RiskLimits(max_order_value=2_000, max_order_pct=1.0))
    acct = Account(broker="paper", cash=100_000.0, positions=[])
    buy = Order(symbol="BE", side=Side.BUY, quantity=75)
    d = rm.approve(buy, acct, 253.0)
    assert not d.approved
    assert "order cap" in d.reason


def test_a_high_priced_order_cannot_inflate_the_caps():
    """The failure that matters: inflated equity loosens every percentage cap."""
    low = _equity_seen("CHEAP", 1.0)
    high = _equity_seen("CHEAP", 1.0)
    assert low == high
    # Trading an expensive name must not multiply the book.
    assert _equity_seen("RICH", 1_000.0) < 15_000
