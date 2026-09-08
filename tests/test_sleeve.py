"""Unit tests for the confidence → capital sleeve ladder."""
from __future__ import annotations

from app.analytics.confidence import ScoredSignal
from app.analytics.sleeve import evaluate_from_summary


def _sig(sym: str, score: float, entry: float, exit: float) -> ScoredSignal:
    return ScoredSignal(
        date="2026-01-01", symbol=sym, score=score, action="buy",
        entry=entry, exit=exit, horizon_days=5,
    )


def test_no_data_zero_sleeve():
    s = evaluate_from_summary({"n": 0, "pending": 3, "horizon": 5})
    assert s.gate == "NO DATA"
    assert s.recommended_sleeve_usd == 0
    assert s.paper_shadow_usd == 0
    assert s.release_stage_hint == "backtest_only"


def test_measured_paper_only():
    s = evaluate_from_summary({
        "n": 30, "pending": 0, "horizon": 5,
        "hit_rate_pct": 55.0,
        "mean_signal_return_pct": 0.5,
        "mean_buyhold_return_pct": 0.4,
        "edge_vs_hold_pp": 0.1,
    })
    assert s.gate == "MEASURED"
    assert s.recommended_sleeve_usd == 0
    assert s.paper_shadow_usd == 1000


def test_evidenced_requires_positive_edge():
    ok = evaluate_from_summary({
        "n": 100, "pending": 0, "horizon": 5,
        "hit_rate_pct": 52.0,
        "mean_signal_return_pct": 1.0,
        "mean_buyhold_return_pct": 0.5,
        "edge_vs_hold_pp": 0.5,
    })
    assert ok.gate == "EVIDENCED"
    assert ok.recommended_sleeve_usd == 1000
    assert ok.release_stage_hint == "live_confirm"

    bad = evaluate_from_summary({
        "n": 100, "pending": 0, "horizon": 5,
        "hit_rate_pct": 40.0,
        "mean_signal_return_pct": -0.2,
        "mean_buyhold_return_pct": 0.5,
        "edge_vs_hold_pp": -0.7,
    })
    assert bad.recommended_sleeve_usd == 0
    assert bad.gate == "MEASURED"


def test_established_dd_cap():
    capped = evaluate_from_summary({
        "n": 250, "pending": 0, "horizon": 5,
        "hit_rate_pct": 55.0,
        "mean_signal_return_pct": 1.2,
        "mean_buyhold_return_pct": 0.8,
        "edge_vs_hold_pp": 0.4,
    }, sleeve_max_dd_pct=40.0)
    assert capped.recommended_sleeve_usd == 1000

    full = evaluate_from_summary({
        "n": 250, "pending": 0, "horizon": 5,
        "hit_rate_pct": 55.0,
        "mean_signal_return_pct": 1.2,
        "mean_buyhold_return_pct": 0.8,
        "edge_vs_hold_pp": 0.4,
    }, sleeve_max_dd_pct=20.0)
    assert full.recommended_sleeve_usd == 5000


def test_reward_deep_dive_on_positive_edge():
    scored = [
        _sig("AAA", 0.5, 100, 110),   # +10%
        _sig("BBB", 0.5, 100, 105),   # +5%
        _sig("CCC", 0.5, 100, 90),    # -10%
    ]
    s = evaluate_from_summary({
        "n": 40, "pending": 0, "horizon": 5,
        "hit_rate_pct": 66.0,
        "mean_signal_return_pct": 1.0,
        "mean_buyhold_return_pct": 0.5,
        "edge_vs_hold_pp": 0.5,
    }, scored=scored)
    assert "deep_dive" in s.rewards_unlocked
    assert "AAA" in s.deep_dive_symbols
    assert "CCC" in s.deep_dive_symbols


def test_no_reward_without_edge():
    s = evaluate_from_summary({
        "n": 40, "pending": 0, "horizon": 5,
        "hit_rate_pct": 40.0,
        "mean_signal_return_pct": -0.5,
        "mean_buyhold_return_pct": 0.5,
        "edge_vs_hold_pp": -1.0,
    }, scored=[_sig("AAA", 0.5, 100, 110)])
    assert s.rewards_unlocked == []
