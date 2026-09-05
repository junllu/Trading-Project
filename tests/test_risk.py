from app.config import RiskLimits
from app.engine.risk import RiskManager
from app.models import Account, Order, Position, Side


def acct(cash=100_000, positions=None):
    return Account(broker="paper", cash=cash, positions=positions or [])


def limits(**kw):
    base = dict(max_position_value=5000, max_order_value=2000,
                max_orders_per_day=20, max_daily_loss=1000, allowed_symbols_only=True)
    base.update(kw)
    return RiskLimits(**base)


def test_approves_ordinary_order():
    rm = RiskManager(limits(), allowed_symbols=["AAPL"])
    o = Order(symbol="AAPL", side=Side.BUY, quantity=10)  # $1000 @ 100
    assert rm.approve(o, acct(), ref_price=100).approved


def test_blocks_symbol_not_allowed():
    rm = RiskManager(limits(), allowed_symbols=["AAPL"])
    o = Order(symbol="TSLA", side=Side.BUY, quantity=1)
    d = rm.approve(o, acct(), ref_price=100)
    assert not d.approved and "not in allowed" in d.reason


def test_blocks_oversized_order():
    rm = RiskManager(limits(max_order_value=500), allowed_symbols=["AAPL"])
    o = Order(symbol="AAPL", side=Side.BUY, quantity=10)  # $1000 > $500
    assert not rm.approve(o, acct(), ref_price=100).approved


def test_blocks_position_cap():
    rm = RiskManager(limits(max_position_value=1500, max_order_value=5000), allowed_symbols=["AAPL"])
    held = [Position(symbol="AAPL", quantity=10, avg_price=100)]  # $1000 already
    o = Order(symbol="AAPL", side=Side.BUY, quantity=10)          # +$1000 -> $2000 > $1500
    d = rm.approve(o, acct(positions=held), ref_price=100)
    assert not d.approved and "position" in d.reason


def test_daily_order_limit_trips():
    rm = RiskManager(limits(max_orders_per_day=2), allowed_symbols=["AAPL"])
    o = Order(symbol="AAPL", side=Side.BUY, quantity=1)
    rm.record_order(); rm.record_order()
    assert not rm.approve(o, acct(), ref_price=100).approved


def test_daily_loss_halt():
    rm = RiskManager(limits(max_daily_loss=500), allowed_symbols=["AAPL"])
    rm.record_realized_loss(-600)  # exceeded the halt
    o = Order(symbol="AAPL", side=Side.BUY, quantity=1)
    d = rm.approve(o, acct(), ref_price=100)
    assert not d.approved and "loss halt" in d.reason


def test_insufficient_cash_blocked():
    rm = RiskManager(limits(max_order_value=5000, max_position_value=10000), allowed_symbols=["AAPL"])
    o = Order(symbol="AAPL", side=Side.BUY, quantity=10)  # $1000
    assert not rm.approve(o, acct(cash=500), ref_price=100).approved
