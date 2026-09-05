from app.brokers.paper import PaperBroker
from app.portfolio.holdings import seed_paper_broker


def test_seed_merges_cross_broker_positions():
    b = PaperBroker()
    b.connect()
    holdings = [
        {"symbol": "BE", "shares": 50, "avg_price": 254.82, "last": 271.20, "broker": "robinhood"},
        {"symbol": "BE", "shares": 25, "avg_price": 252.62, "last": 271.05, "broker": "webull"},
        {"symbol": "ANET", "shares": 100, "avg_price": 156.32, "last": 193.75, "broker": "webull"},
    ]
    syms = seed_paper_broker(b, holdings)
    assert set(syms) == {"BE", "ANET"}
    positions = {p.symbol: p for p in b.get_positions()}
    # BE merged to 75 shares with weighted-average cost
    assert positions["BE"].quantity == 75
    expected_avg = (254.82 * 50 + 252.62 * 25) / 75
    assert abs(positions["BE"].avg_price - expected_avg) < 1e-6
    # last price recorded for quotes
    assert b.get_quote("ANET").price == 193.75


def test_seed_sets_prices():
    b = PaperBroker()
    b.connect()
    seed_paper_broker(b, [{"symbol": "NOW", "shares": 301, "avg_price": 105.24, "last": 141.10}])
    assert b.get_quote("NOW").price == 141.10
    assert b.get_positions()[0].unrealized_pnl(141.10) > 0
