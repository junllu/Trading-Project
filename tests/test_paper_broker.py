from app.brokers.paper import PaperBroker
from app.models import Order, OrderStatus, Side


def make_broker():
    b = PaperBroker(starting_cash=10_000, seed_prices={"AAPL": 100.0})
    b.connect()
    return b


def test_buy_reduces_cash_and_creates_position():
    b = make_broker()
    order = Order(symbol="AAPL", side=Side.BUY, quantity=10)
    res = b.place_order(order)
    assert res.status is OrderStatus.FILLED
    assert res.filled_price == 100.0
    assert b.cash == 9_000.0
    pos = b.get_positions()[0]
    assert pos.symbol == "AAPL" and pos.quantity == 10 and pos.avg_price == 100.0


def test_buy_rejected_when_insufficient_cash():
    b = make_broker()
    order = Order(symbol="AAPL", side=Side.BUY, quantity=1000)  # $100k > $10k
    res = b.place_order(order)
    assert res.status is OrderStatus.REJECTED
    assert b.cash == 10_000.0
    assert not b.get_positions()


def test_sell_rejected_without_shares():
    b = make_broker()
    res = b.place_order(Order(symbol="AAPL", side=Side.SELL, quantity=5))
    assert res.status is OrderStatus.REJECTED


def test_average_price_on_scale_in():
    b = make_broker()
    b.place_order(Order(symbol="AAPL", side=Side.BUY, quantity=10))  # @100
    b.set_price("AAPL", 120.0)
    b.place_order(Order(symbol="AAPL", side=Side.BUY, quantity=10))  # @120
    pos = b.get_positions()[0]
    assert pos.quantity == 20
    assert pos.avg_price == 110.0  # (100*10 + 120*10)/20


def test_full_sell_closes_position():
    b = make_broker()
    b.place_order(Order(symbol="AAPL", side=Side.BUY, quantity=10))
    b.set_price("AAPL", 150.0)
    b.place_order(Order(symbol="AAPL", side=Side.SELL, quantity=10))
    assert b.get_positions() == []
    assert b.cash == 10_000 - 1000 + 1500  # bought $1000, sold $1500
