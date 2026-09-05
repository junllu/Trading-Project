from app.models import Position
from app.options.pricing import black_scholes
from app.options.strategies import (
    cash_secured_put_candidates,
    covered_call_candidates,
    sell_the_news_plan,
)


def test_black_scholes_call_put_sane():
    call = black_scholes(spot=100, strike=100, days=30, vol=0.3, is_call=True)
    put = black_scholes(spot=100, strike=100, days=30, vol=0.3, is_call=False)
    assert call.price > 0 and put.price > 0
    assert 0 < call.delta < 1
    assert -1 < put.delta < 0
    # ATM ~30d option prob ITM near 0.5
    assert 0.3 < call.prob_itm < 0.7


def test_black_scholes_deep_itm_call_high_delta():
    g = black_scholes(spot=150, strike=100, days=30, vol=0.3, is_call=True)
    assert g.delta > 0.9
    assert g.price >= 49  # at least intrinsic-ish


def test_covered_call_requires_100_lot():
    positions = [
        Position("ANET", 100, 156.32),  # 1 lot -> eligible
        Position("VRT", 2, 315.00),     # <100 -> skipped
        Position("NOW", 301, 105.24),   # 3 lots
    ]
    prices = {"ANET": 193.75, "VRT": 280.01, "NOW": 141.10}
    plans = covered_call_candidates(positions, prices, days=30)
    syms = {p.symbol: p for p in plans}
    assert "ANET" in syms and "NOW" in syms
    assert "VRT" not in syms
    assert syms["NOW"].contracts == 3
    assert syms["ANET"].est_premium > 0


def test_cash_secured_put_covered_by_cash():
    plans = cash_secured_put_candidates(["ABEV"], {"ABEV": 3.02}, cash=1000, days=30)
    assert plans and plans[0].symbol == "ABEV"
    assert plans[0].contracts >= 1
    # no plan when cash can't cover collateral
    none_plans = cash_secured_put_candidates(["NOW"], {"NOW": 141.10}, cash=100, days=30)
    assert none_plans == []


def test_sell_the_news_positive_writes_call_when_holding():
    plan = sell_the_news_plan("NOW", spot=141.10, sentiment_score=0.6, holds_shares=301)
    assert plan is not None
    assert plan.strategy == "sell_the_news"
    assert plan.contracts == 3
    assert plan.est_premium > 0


def test_sell_the_news_ignores_weak_signal():
    assert sell_the_news_plan("NOW", 141.10, sentiment_score=0.1, holds_shares=301) is None
