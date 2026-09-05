from app.analytics import ConvictionEngine
from app.analytics.technical import technical_score
from app.engine.sizing import SizingParams, realized_vol, size_order_value


def rising(n=60, start=100.0, step=1.0):
    return [start + i * step for i in range(n)]


def falling(n=60, start=160.0, step=1.0):
    return [start - i * step for i in range(n)]


def test_technical_score_uptrend_positive():
    ts = technical_score("AAA", rising())
    assert ts.ready
    assert ts.score > 0.3
    assert ts.components["trend"] > 0


def test_technical_score_downtrend_negative():
    ts = technical_score("BBB", falling())
    assert ts.ready
    assert ts.score < -0.3


def test_technical_score_insufficient_history():
    ts = technical_score("CCC", [1, 2, 3])
    assert not ts.ready
    assert ts.score == 0.0


def test_conviction_blend_weights_and_breakdown():
    eng = ConvictionEngine({"technical": 0.5, "analyst": 0.5})
    conv = eng.blend("AAA", {"technical": 0.8, "analyst": 0.4})
    assert abs(conv.score - 0.6) < 1e-9      # (0.8+0.4)/2
    assert conv.action in ("strong_buy", "buy")
    assert set(conv.contributions) == {"technical", "analyst"}


def test_conviction_handles_missing_sources():
    eng = ConvictionEngine()
    conv = eng.blend("AAA", {"technical": 0.5})   # only one source present
    assert conv.score == 0.5                       # normalized over present weight
    assert conv.coverage < 1.0


def test_realized_vol_and_sizing():
    flat = [100.0] * 30
    assert realized_vol(flat) == 0.0
    # weak conviction below threshold -> no trade
    assert size_order_value(0.1, rising(), SizingParams(entry_threshold=0.2)) == 0.0
    # strong conviction -> positive size, capped
    val = size_order_value(1.0, rising(), SizingParams(base_budget=1000, max_budget=2000))
    assert 0 < val <= 2000


def test_sizing_scales_with_conviction():
    p = SizingParams(base_budget=1000, entry_threshold=0.2, max_budget=999999)
    hist = rising()
    low = size_order_value(0.4, hist, p)
    high = size_order_value(0.9, hist, p)
    assert high > low > 0
