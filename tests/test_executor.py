from app.brokers.paper import PaperBroker
from app.config import RiskLimits, TradingMode
from app.engine.executor import Executor
from app.engine.risk import RiskManager
from app.models import OrderStatus, Side, Signal


def build(mode=TradingMode.PAPER):
    paper = PaperBroker(starting_cash=100_000, seed_prices={"AAPL": 100.0})
    paper.connect()
    rm = RiskManager(RiskLimits(max_order_value=5000, max_position_value=50000), allowed_symbols=["AAPL"])
    ex = Executor(risk=rm, mode=mode, paper_broker=paper)
    return ex, paper


def test_paper_signal_fills():
    ex, paper = build()
    sig = Signal("AAPL", Side.BUY, order_value=1000, strategy="t")
    res = ex.handle_signal(sig, ref_price=100.0)
    assert res.accepted
    assert res.order.status is OrderStatus.FILLED
    assert paper.get_positions()[0].symbol == "AAPL"


def test_kill_switch_blocks_execution():
    ex, paper = build()
    ex.kill()
    res = ex.handle_signal(Signal("AAPL", Side.BUY, order_value=1000), ref_price=100.0)
    assert not res.accepted
    assert res.order.status is OrderStatus.REJECTED
    assert "kill-switch" in res.order.reason
    assert paper.get_positions() == []


def test_confirm_mode_queues_then_approves():
    ex, paper = build(mode=TradingMode.CONFIRM)
    res = ex.handle_signal(Signal("AAPL", Side.BUY, order_value=1000), ref_price=100.0)
    assert res.order.status is OrderStatus.QUEUED
    assert len(ex.pending) == 1
    approved = ex.approve_pending(ex.pending[0].id, ref_price=100.0)
    assert approved.order.status is OrderStatus.FILLED
    assert ex.pending == []


def test_risk_rejection_stops_order():
    ex, paper = build()
    ex.risk.limits.max_order_value = 100  # any real order will exceed
    res = ex.handle_signal(Signal("AAPL", Side.BUY, order_value=1000), ref_price=100.0)
    assert not res.accepted
    assert "risk" in res.order.reason
