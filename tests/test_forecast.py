from app.ml import NaiveDriftForecaster, build_forecaster
from app.ml.forecast import Forecast


def rising(n=40, start=100.0, step=1.0):
    return [start + i * step for i in range(n)]


def falling(n=40, start=140.0, step=1.0):
    return [start - i * step for i in range(n)]


def test_forecast_score_mapping():
    up = Forecast("X", expected_return=0.05, confidence=1.0, horizon=5, source="t")
    assert up.score(scale=0.05) == 1.0
    dn = Forecast("X", expected_return=-0.05, confidence=1.0, horizon=5, source="t")
    assert dn.score(scale=0.05) == -1.0
    # confidence attenuates
    weak = Forecast("X", expected_return=0.05, confidence=0.2, horizon=5, source="t")
    assert abs(weak.score(scale=0.05) - 0.2) < 1e-9


def test_naive_forecaster_directions():
    f = NaiveDriftForecaster()
    up = f.predict("AAA", rising())
    dn = f.predict("BBB", falling())
    assert up.expected_return > 0 and up.score() > 0
    assert dn.expected_return < 0 and dn.score() < 0
    assert 0 <= up.confidence <= 0.9


def test_naive_low_confidence_on_short_history():
    f = NaiveDriftForecaster()
    fc = f.predict("AAA", [100.0, 101.0])
    assert fc.confidence == 0.0
    assert fc.score() == 0.0


def test_build_forecaster_default_is_naive():
    assert isinstance(build_forecaster("naive"), NaiveDriftForecaster)
    assert isinstance(build_forecaster(None), NaiveDriftForecaster)


def test_build_forecaster_hf_falls_back_without_deps():
    # No torch/chronos here -> HFForecaster.predict must gracefully use naive.
    f = build_forecaster("chronos")
    fc = f.predict("AAA", rising())
    assert isinstance(fc, Forecast)
    assert "naive" in fc.source          # degraded to the fallback, didn't crash
    assert fc.expected_return > 0
