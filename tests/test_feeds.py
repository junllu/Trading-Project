"""Curated-source feed: point-in-time correctness is the property that matters.

The macro timeline leaked future knowledge into backtests by being authored with
hindsight. This store must not repeat that, so the as_of filtering and age decay
are tested directly.
"""
from __future__ import annotations

import pytest

from app.intel.feeds import HALFLIFE_DAYS, FeedStore, SourceView


@pytest.fixture()
def store(tmp_path):
    return FeedStore(path=tmp_path / "views.jsonl")


def _view(**kw):
    base = dict(source="professor_jiang", symbol="MU", bias=0.6,
                published="2026-06-01", confidence=1.0)
    base.update(kw)
    return SourceView(**base)


def test_roundtrip_and_symbol_uppercased(store):
    store.add(_view(symbol="mu"))
    rows = store.all_views()
    assert len(rows) == 1
    assert rows[0].symbol == "MU"
    assert rows[0].bias == pytest.approx(0.6)


def test_unknown_source_rejected(store):
    with pytest.raises(ValueError):
        store.add(_view(source="not_a_real_source"))


def test_bad_date_rejected(store):
    with pytest.raises(ValueError):
        store.add(_view(published="June 1st 2026"))


def test_as_of_hides_future_views(store):
    """The core guarantee: a replay cannot see a view published after as_of."""
    store.add(_view(published="2026-06-01", bias=0.6))
    store.add(_view(published="2026-08-01", bias=-0.8))

    assert len(store.views(as_of="2026-05-01")) == 0      # before anything was said
    assert len(store.views(as_of="2026-06-15")) == 1      # only the first
    assert len(store.views(as_of="2026-09-01")) == 2

    # bias at a past date must not be contaminated by the later bearish call
    assert store.bias("professor_jiang", "MU", as_of="2026-06-15") > 0


def test_bias_none_when_source_silent(store):
    """Silence is not neutrality — the conviction slot should be skipped."""
    store.add(_view(symbol="MU"))
    assert store.bias("professor_jiang", "NVDA", as_of="2026-09-01") is None
    assert store.bias("serenity", "MU", as_of="2026-09-01") is None


def test_recent_view_outweighs_stale_one(store):
    """Age decay: a fresh opposite call should dominate an old one."""
    store.add(_view(published="2026-01-01", bias=1.0))
    store.add(_view(published="2026-09-01", bias=-1.0))
    assert store.bias("professor_jiang", "MU", as_of="2026-09-01") < 0


def test_decay_halves_at_halflife(store):
    """Two equal-and-opposite calls, one a halflife older, net bullish-ish."""
    store.add(_view(published="2026-08-02", bias=-1.0))    # 30 days before as_of
    store.add(_view(published="2026-09-01", bias=1.0))     # same day as as_of
    b = store.bias("professor_jiang", "MU", as_of="2026-09-01")
    # fresh weight 1.0 vs decayed 0.5 -> (1.0 - 0.5)/1.5
    assert b == pytest.approx((1.0 - 0.5) / 1.5, abs=0.02)


def test_bias_clamped_to_unit_range(store):
    store.add(_view(bias=5.0))
    assert store.bias("professor_jiang", "MU", as_of="2026-09-01") <= 1.0


def test_status_flags_stale_feed(store):
    store.add(_view(published="2026-01-01"))
    st = store.status(as_of="2026-09-01")
    assert st["professor_jiang"]["stale"] is True
    assert st["serenity"]["views"] == 0
    assert st["serenity"]["stale"] is True


def test_status_fresh_within_biweekly_window(store):
    store.add(_view(published="2026-08-25"))
    st = store.status(as_of="2026-09-01")
    assert st["professor_jiang"]["stale"] is False
    assert st["professor_jiang"]["age_days"] == 7


def test_biases_skips_uncovered_symbols(store):
    store.add(_view(symbol="MU", bias=0.5))
    out = store.biases("professor_jiang", ["MU", "NVDA", "AA"], as_of="2026-09-01")
    assert set(out) == {"MU"}


def test_halflife_constant_is_sane():
    assert 1.0 < HALFLIFE_DAYS <= 365.0
