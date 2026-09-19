"""The band sleeve trades volatility around a thesis without ever leaving it.

Guards the failures that make a scale-out rule dangerous: harvesting the
position to zero, quietly building a bigger bet while claiming to manage one,
and trading a name whose normal range was never measured.
"""
import pytest

from app.models import Side
from app.strategy.band import VolatilityBand
from app.strategy.base import StrategyContext


def _ctx(symbol, last, cost, qty, original=None, **params):
    p = {"avg_price": cost, "original_qty": original or qty}
    p.update(params)
    return StrategyContext(symbol=symbol, history=[cost] * 30 + [last],
                           position_qty=qty, params=p)


@pytest.fixture
def band(monkeypatch):
    s = VolatilityBand({})
    # 10% median adverse move -> trim at +15%, add back at -10%.
    monkeypatch.setattr(s, "_band_pct", lambda sym: 10.0)
    return s


def test_trims_into_strength_past_the_band(band):
    sig = band.evaluate(_ctx("AAA", last=120.0, cost=100.0, qty=100))
    assert len(sig) == 1 and sig[0].side is Side.SELL
    assert "trim" in sig[0].note


def test_does_not_trim_inside_the_band(band):
    assert band.evaluate(_ctx("AAA", last=112.0, cost=100.0, qty=100)) == []


def test_never_sells_below_the_core(band):
    """Harvesting a thesis to zero is the failure every scale-out rule risks."""
    # core_fraction 0.6 of an original 100 -> 60 shares are untouchable.
    sig = band.evaluate(_ctx("AAA", last=200.0, cost=100.0, qty=60, original=100))
    assert sig == [], "sold into the core"


def test_adds_back_on_reversion_to_lower_basis(band):
    sig = band.evaluate(_ctx("AAA", last=88.0, cost=100.0, qty=70, original=100))
    assert len(sig) == 1 and sig[0].side is Side.BUY
    assert "lowering basis" in sig[0].note


def test_never_adds_above_the_original_size(band):
    """This lowers cost basis; it does not build a bigger bet."""
    assert band.evaluate(_ctx("AAA", last=80.0, cost=100.0, qty=100, original=100)) == []


def test_an_unmeasured_name_is_skipped_not_guessed(monkeypatch):
    s = VolatilityBand({})
    monkeypatch.setattr(s, "_band_pct", lambda sym: None)
    assert s.evaluate(_ctx("ZZZZ", last=200.0, cost=100.0, qty=100)) == []


def test_opens_nothing_when_flat(band):
    """This sleeve manages a holding; entries belong to the conviction engine."""
    assert band.evaluate(_ctx("AAA", last=200.0, cost=100.0, qty=0)) == []


def test_band_scales_with_the_name(monkeypatch):
    """A fixed band would trade a quiet name constantly and never a wild one."""
    quiet, wild = VolatilityBand({}), VolatilityBand({})
    monkeypatch.setattr(quiet, "_band_pct", lambda sym: 4.0)    # trim at +6%
    monkeypatch.setattr(wild, "_band_pct", lambda sym: 15.0)    # trim at +22.5%
    c = _ctx("AAA", last=110.0, cost=100.0, qty=100)            # +10%
    assert quiet.evaluate(c), "quiet name should have trimmed at +10%"
    assert wild.evaluate(c) == [], "wild name trimmed inside its own noise"
