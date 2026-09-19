"""Candle deflation — the headline must answer the question actually asked.

The trap this guards: the grid's overall winner is a NEUTRAL pattern scored on
|x| - baseline. Absolute values are bounded below and unbounded above, so that
series is structurally right-skewed, and the DSR variance term SUBTRACTS skew —
so the transform itself inflates the ratio. Reporting that as "SURVIVES" implies
a tradeable candle signal when every directional pattern fails the same test.
"""
import statistics as st

import pytest

from app.analytics.candles import PatternScore, deflate
from app.backtest.trials import _moments, deflated_sharpe

# Structure, not which cell wins — so a 4-symbol grid is enough.
SUBSET = ["TSLA", "NVDA", "AMD", "KKR"]


def test_headline_verdict_reflects_directional_patterns_only():
    d = deflate(SUBSET)
    if "error" in d:
        pytest.skip("no OHLC cached")
    assert d["verdict"] == d["directional"]["verdict"]


def test_neutral_and_directional_are_judged_separately():
    d = deflate(SUBSET)
    if "error" in d:
        pytest.skip("no OHLC cached")
    assert "directional" in d and "neutral" in d
    assert d["directional"]["best"]["direction"] != 0
    assert d["neutral"]["best"]["direction"] == 0


def test_moments_are_measured_not_assumed():
    d = deflate(SUBSET)
    if "error" in d:
        pytest.skip("no OHLC cached")
    assert d["moments_measured"] is True
    # Gaussian defaults would be exactly these; measured data should differ.
    assert (d["deflation"]["skew"], d["deflation"]["kurtosis"]) != (0.0, 3.0)


def test_excesses_are_retained_but_not_reported():
    # They are an input to the statistics, not a field anyone reads.
    p = PatternScore(pattern="doji", symbol="X", horizon=3, excesses=[1.0, 2.0])
    assert "excesses" not in p.to_dict()
    assert p.excesses == [1.0, 2.0]


def test_fat_tails_penalise_and_transform_skew_rescues_the_neutral_winner():
    """Why neutral patterns are judged separately.

    Scale-independent form of the finding: for the neutral winner, measured
    KURTOSIS alone always lowers the ratio (fat tails widen the standard error)
    while adding its measured SKEW lifts it back. The skew is an artifact of the
    |x| transform, so it is rescuing the verdict for a reason unrelated to edge.

    The exact full-grid numbers are in candles.deflate()'s comment rather than
    here — the threshold crossing (0.9468 vs the 0.95 bar) depends on the trial
    count, and pinning it would mean a two-minute test.
    """
    from app.analytics.candles import grid
    rows = grid(SUBSET)
    if not rows:
        pytest.skip("no OHLC cached")
    var = st.pvariance([r.sharpe for r in rows])
    b = max((r for r in rows if r.direction == 0), key=lambda r: r.sharpe)
    sk, ku = _moments(b.excesses)
    common = dict(observed_sr=b.sharpe, n_periods=max(b.n, 1),
                  n_trials=len(rows), sharpe_variance=var)
    gaussian = deflated_sharpe(**common, skew=0.0, kurtosis=3.0)
    kurt_only = deflated_sharpe(**common, skew=0.0, kurtosis=ku)
    both = deflated_sharpe(**common, skew=sk, kurtosis=ku)

    assert sk > 0, "the |x| transform should leave the neutral series right-skewed"
    assert ku > 3.0, "returns should be fatter-tailed than normal"
    assert kurt_only["deflated_sharpe_ratio"] < gaussian["deflated_sharpe_ratio"]
    assert both["deflated_sharpe_ratio"] > kurt_only["deflated_sharpe_ratio"]


def test_absolute_value_of_a_normal_series_is_right_skewed():
    """The mechanism. |N(0,1)| has skew ~+0.99. Note this is a property of the
    NORMAL case — |uniform| is roughly symmetric — so the artifact depends on
    returns being approximately normal, which they are here."""
    import random
    random.seed(0)
    sample = [random.gauss(0, 1) for _ in range(4000)]
    base = st.mean(abs(x) for x in sample)
    skew, _ = _moments([abs(x) - base for x in sample])
    assert skew > 0.8
