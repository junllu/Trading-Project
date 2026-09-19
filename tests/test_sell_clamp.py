"""A sell must never be sized larger than the position it is exiting.

The bug: sizing is done in dollars (`order_value / price`) and dollars know
nothing about the holding. A $90 exit signal on a $2.30 stock asks for 39.1
shares of a 38-share position, the broker bounces the ENTIRE order, and the
position is never exited. In one paper run two of three orders died this way —
so the training record filled with rejections that say nothing about whether
the strategy was right.

Clamping is correct rather than lenient: nothing in this system shorts, so a
sell larger than the holding is always an arithmetic artifact. Holding nothing
at all is a different case and is still rejected.
"""
import pytest

from app.brokers.paper import PaperBroker
from app.config import RiskLimits, TradingMode
from app.engine.executor import Executor
from app.engine.risk import RiskManager
from app.models import OrderStatus, Side, Signal


def build(seed_price=2.30):
    paper = PaperBroker(starting_cash=100_000, seed_prices={"PEW": seed_price})
    paper.connect()
    rm = RiskManager(RiskLimits(max_order_value=5000, max_position_value=50_000))
    return Executor(risk=rm, mode=TradingMode.PAPER, paper_broker=paper), paper


def _give(paper, symbol, qty, price):
    """Seed a holding the way the portal seeds holdings.yaml into paper."""
    paper._apply_fill(symbol, qty, price)


def test_oversized_sell_is_clamped_and_fills_instead_of_bouncing():
    """The exact failure: $90 of a $2.30 stock is 39.1 shares against 38 held."""
    ex, paper = build()
    _give(paper, "PEW", 38.0, 2.30)

    res = ex.handle_signal(Signal("PEW", Side.SELL, order_value=90.0, strategy="daily_agent"),
                           ref_price=2.30)

    assert res.order.status is OrderStatus.FILLED, res.order.reason
    assert res.order.quantity <= 38.0
    assert "clamped to position" in res.order.reason
    assert paper.get_positions() == []          # fully exited


def test_clamp_never_rounds_up_past_the_holding():
    """Rounding a clamp UP would reintroduce the rejection it prevents."""
    ex, paper = build(seed_price=1.0)
    _give(paper, "PEW", 12.000059, 1.0)

    res = ex.handle_signal(Signal("PEW", Side.SELL, order_value=99.0), ref_price=1.0)

    assert res.order.status is OrderStatus.FILLED, res.order.reason
    assert res.order.quantity <= 12.000059


def test_a_sell_within_the_position_is_untouched():
    ex, paper = build()
    _give(paper, "PEW", 100.0, 2.30)

    res = ex.handle_signal(Signal("PEW", Side.SELL, order_value=23.0), ref_price=2.30)

    assert res.order.status is OrderStatus.FILLED
    assert res.order.quantity == pytest.approx(10.0)
    assert "clamped" not in (res.order.reason or "")


def test_selling_a_name_not_held_is_still_rejected():
    """Different in kind: a signal fired on something the book does not own.
    Worth recording as evidence, not silently clamped to zero."""
    ex, _ = build()

    res = ex.handle_signal(Signal("PEW", Side.SELL, order_value=90.0), ref_price=2.30)

    assert not res.accepted
    assert res.order.status is OrderStatus.REJECTED
    assert "no position in PEW" in res.order.reason


def test_buys_are_not_affected_by_the_sell_clamp():
    ex, paper = build()
    _give(paper, "PEW", 5.0, 2.30)

    res = ex.handle_signal(Signal("PEW", Side.BUY, order_value=230.0), ref_price=2.30)

    assert res.order.status is OrderStatus.FILLED
    assert res.order.quantity == pytest.approx(100.0)
