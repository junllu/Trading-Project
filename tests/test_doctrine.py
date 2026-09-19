"""Doctrine tracking — the PENDING/CONTRADICTED split is the load-bearing part.

All hermetic: _fetch is monkeypatched, so nothing here touches FRED.
"""
import pytest

from app.macro import doctrine as D


@pytest.fixture
def series(monkeypatch):
    store = {}

    def fake_fetch(sid):
        return store.get(sid, [])

    monkeypatch.setattr(D, "_fetch", fake_fetch)
    return store


def ind(confirms=D.CONFIRMS_FALLING, **kw):
    base = dict(key="k", series="S", label="L", confirms=confirms, tests="t")
    base.update(kw)
    return D.Indicator(**base)


def test_confirmed_when_the_series_moves_the_predicted_way(series):
    series["S"] = [("2026-01-01", 100.0), ("2026-06-01", 90.0)]
    r = D._reading(ind(D.CONFIRMS_FALLING), "2026-02-22")
    assert r["status"] == D.CONFIRMED
    assert r["change_pct"] == pytest.approx(-10.0)


def test_contradicted_when_it_moves_the_other_way(series):
    series["S"] = [("2026-01-01", 100.0), ("2026-06-01", 115.0)]
    assert D._reading(ind(D.CONFIRMS_FALLING), "2026-02-22")["status"] == D.CONTRADICTED


def test_small_moves_are_neutral_not_scored(series):
    # Inside the revision band; scoring it either way would manufacture signal.
    series["S"] = [("2026-01-01", 100.0), ("2026-06-01", 100.5)]
    assert D._reading(ind(), "2026-02-22")["status"] == D.NEUTRAL


def test_a_series_with_no_print_since_baseline_is_pending_not_contradicted(series):
    """The distinction that matters: a slow series must never read as a
    refutation. GDP lags ~90 days, so early in a thesis's life the honest answer
    is 'no data yet', and collapsing that into CONTRADICTED would let publication
    lag masquerade as evidence against."""
    series["S"] = [("2025-06-01", 100.0), ("2026-01-01", 98.0)]
    r = D._reading(ind(), "2026-02-22")
    assert r["status"] == D.PENDING
    assert "no print since the baseline" in r["why"]


def test_missing_series_is_pending(series):
    assert D._reading(ind(), "2026-02-22")["status"] == D.PENDING


def test_no_observation_before_baseline_is_pending(series):
    series["S"] = [("2026-06-01", 100.0)]
    r = D._reading(ind(), "2026-02-22")
    assert r["status"] == D.PENDING


def test_derived_indicator_computes_a_spread_against_the_second_series(series):
    # GDP vs GDI: the 'ghost GDP' proxy is the gap as a % of GDI, not the level.
    series["A"] = [("2026-01-01", 110.0), ("2026-06-01", 130.0)]
    series["B"] = [("2026-01-01", 100.0), ("2026-06-01", 100.0)]
    r = D._reading(ind(D.CONFIRMS_RISING, series="A", series_b="B"), "2026-02-22")
    assert r["baseline_value"] == pytest.approx(10.0)
    assert r["latest_value"] == pytest.approx(30.0)
    assert r["status"] == D.CONFIRMED


def test_derived_indicator_is_pending_without_its_partner(series):
    series["A"] = [("2026-01-01", 110.0), ("2026-06-01", 130.0)]
    assert D._reading(ind(series="A", series_b="B"), "2026-02-22")["status"] == D.PENDING


def test_target_gap_is_reported_against_the_named_level(series):
    series["S"] = [("2026-01-01", 4.4), ("2026-06-01", 4.1)]
    r = D._reading(ind(D.CONFIRMS_RISING, target=10.2, target_by="2028-06-30"), "2026-02-22")
    assert r["target_gap"] == pytest.approx(6.1)


def test_verdict_refuses_to_call_it_on_too_few_scored_indicators():
    tally = {D.CONFIRMED: 1, D.CONTRADICTED: 1, D.NEUTRAL: 0, D.PENDING: 8}
    assert "TOO EARLY" in D._verdict(tally, 2)


def test_verdict_thresholds():
    assert "TRACKING" in D._verdict({D.CONFIRMED: 8, D.CONTRADICTED: 2}, 10)
    assert "NOT TRACKING" in D._verdict({D.CONFIRMED: 1, D.CONTRADICTED: 9}, 10)
    assert "MIXED" in D._verdict({D.CONFIRMED: 5, D.CONTRADICTED: 5}, 10)


def test_every_shipped_indicator_names_a_direction_and_a_mechanism_link():
    for d in D.DOCTRINES:
        assert d.falsifier.strip(), f"{d.key} has no falsifier"
        for i in d.indicators:
            assert i.confirms in (D.CONFIRMS_RISING, D.CONFIRMS_FALLING)
            assert i.tests.strip(), f"{d.key}/{i.key} does not say what it tests"
            assert i.series.strip()


def test_doctrine_keys_are_unique():
    keys = [d.key for d in D.DOCTRINES]
    assert len(keys) == len(set(keys))
