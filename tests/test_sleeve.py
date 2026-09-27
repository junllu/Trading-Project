"""Unit tests for the confidence → capital sleeve ladder."""
from __future__ import annotations

import json
import sys
import types

import pandas as pd

from app.analytics import confidence
from app.analytics.confidence import ScoredSignal
from app.analytics.sleeve import evaluate_from_summary


def _sig(sym: str, score: float, entry: float, exit: float) -> ScoredSignal:
    return ScoredSignal(
        date="2026-01-01", symbol=sym, score=score, action="buy",
        entry=entry, exit=exit, horizon_days=5,
    )


# A summary that clears every requirement of every tier.
STRONG = {
    "n": 300, "days": 90, "span_days": 130, "pending": 0, "horizon": 5,
    "hit_rate_pct": 55.0, "mean_signal_return_pct": 1.2,
    "mean_buyhold_return_pct": 0.8, "edge_vs_hold_pp": 0.4, "t_stat": 2.5,
}


def test_no_data_zero_sleeve():
    s = evaluate_from_summary({"n": 0, "pending": 3, "horizon": 5})
    assert s.gate == "NO DATA"
    assert s.recommended_sleeve_usd == 0
    assert s.paper_shadow_usd == 0
    assert s.release_stage_hint == "backtest_only"


def test_the_2026_09_27_evidence_cannot_fund_anything():
    """The exact case that read ESTABLISHED / $5k: many snapshots, 4 days,
    signals that LOST money and only 'beat' a falling tape."""
    s = evaluate_from_summary({
        "n": 748, "days": 4, "span_days": 8, "pending": 0, "horizon": 5,
        "hit_rate_pct": 33.3, "mean_signal_return_pct": -1.979,
        "mean_buyhold_return_pct": -3.463, "edge_vs_hold_pp": 1.484, "t_stat": None,
    }, sleeve_max_dd_pct=None)
    assert s.gate == "NO DATA"
    assert s.recommended_sleeve_usd == 0
    assert any("trading days 4/10" in m for m in s.missing)


def test_measured_paper_only():
    s = evaluate_from_summary({**STRONG, "n": 30, "days": 10, "t_stat": None})
    assert s.gate == "MEASURED"
    assert s.recommended_sleeve_usd == 0
    assert s.paper_shadow_usd == 1000


def test_many_signals_on_few_days_is_not_evidence():
    s = evaluate_from_summary({**STRONG, "days": 12})
    assert s.gate == "MEASURED"
    assert s.recommended_sleeve_usd == 0
    assert "trading days 12/40" in s.missing


def test_evidenced_requires_positive_edge_and_positive_mean():
    ok = evaluate_from_summary({**STRONG, "n": 100, "days": 40, "span_days": 60})
    assert ok.gate == "EVIDENCED"
    assert ok.recommended_sleeve_usd == 1000
    assert ok.release_stage_hint == "live_confirm"

    losing_less = evaluate_from_summary({**STRONG, "n": 100, "days": 40,
                                         "mean_signal_return_pct": -0.5,
                                         "edge_vs_hold_pp": 0.7})
    assert losing_less.recommended_sleeve_usd == 0

    bad_edge = evaluate_from_summary({**STRONG, "n": 100, "days": 40,
                                      "edge_vs_hold_pp": -0.7})
    assert bad_edge.recommended_sleeve_usd == 0
    assert bad_edge.gate == "MEASURED"


def test_evidenced_requires_significance():
    s = evaluate_from_summary({**STRONG, "n": 100, "days": 40, "t_stat": 1.2})
    assert s.gate == "MEASURED" and s.recommended_sleeve_usd == 0


def test_established_needs_measured_drawdown():
    unmeasured = evaluate_from_summary(STRONG, sleeve_max_dd_pct=None)
    assert unmeasured.recommended_sleeve_usd == 1000
    assert "sleeve max drawdown unmeasured" in unmeasured.missing

    capped = evaluate_from_summary(STRONG, sleeve_max_dd_pct=40.0)
    assert capped.recommended_sleeve_usd == 1000

    full = evaluate_from_summary(STRONG, sleeve_max_dd_pct=20.0)
    assert full.gate == "ESTABLISHED"
    assert full.recommended_sleeve_usd == 5000
    assert full.missing == []


def test_reward_deep_dive_on_positive_edge():
    scored = [
        _sig("AAA", 0.5, 100, 110),   # +10%
        _sig("BBB", 0.5, 100, 105),   # +5%
        _sig("CCC", 0.5, 100, 90),    # -10%
    ]
    s = evaluate_from_summary({**STRONG, "n": 40, "days": 10}, scored=scored)
    assert "deep_dive" in s.rewards_unlocked
    assert "AAA" in s.deep_dive_symbols
    assert "CCC" in s.deep_dive_symbols


def test_no_reward_without_edge():
    s = evaluate_from_summary({**STRONG, "n": 40, "days": 10,
                               "mean_signal_return_pct": -0.5, "edge_vs_hold_pp": -1.0},
                              scored=[_sig("AAA", 0.5, 100, 110)])
    assert s.rewards_unlocked == []


# --- the scorer that feeds the gate --------------------------------------

def _record(tmp_path, monkeypatch, rows, closes):
    path = tmp_path / "forward_record.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    monkeypatch.setattr(confidence, "RECORD_PATH", path)
    frame = pd.DataFrame(closes, index=pd.to_datetime(sorted(
        {d for series in closes.values() for d in series.index})))
    fake = types.SimpleNamespace(download=lambda *a, **k: {"Close": frame})
    monkeypatch.setitem(sys.modules, "yfinance", fake)


def _row(ts, **scores):
    return {"recorded_at_et": ts,
            "convictions": [{"symbol": k, "score": v} for k, v in scores.items()]}


def _series(dates, prices):
    return pd.Series(prices, index=pd.to_datetime(dates))


SESSIONS = ["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06",
            "2026-10-07", "2026-10-08", "2026-10-09"]


def test_snapshots_on_one_day_count_once(tmp_path, monkeypatch):
    rows = [_row(f"2026-10-01T10:{m:02d}:00-04:00", AAA=0.5) for m in range(0, 50, 5)]
    _record(tmp_path, monkeypatch, rows,
            {"AAA": _series(SESSIONS, [100, 101, 102, 103, 104, 110, 111])})
    rep = confidence.score_forward(5)
    assert rep.n == 1                       # ten snapshots, one opinion


def test_horizon_is_sessions_not_calendar_days(tmp_path, monkeypatch):
    # Thursday signal: 5 sessions later is the following Thursday (10-08), not
    # Tuesday 10-06 as a calendar-day window would give.
    _record(tmp_path, monkeypatch, [_row("2026-10-01T10:00:00-04:00", AAA=0.5)],
            {"AAA": _series(SESSIONS, [100, 101, 102, 103, 104, 110, 111])})
    rep = confidence.score_forward(5)
    assert rep.scored[0].exit == 110


def test_incomplete_horizon_is_pending(tmp_path, monkeypatch):
    _record(tmp_path, monkeypatch, [_row("2026-10-06T10:00:00-04:00", AAA=0.5)],
            {"AAA": _series(SESSIONS, [100, 101, 102, 103, 104, 110, 111])})
    rep = confidence.score_forward(5)
    assert rep.n == 0 and rep.pending == 1


def test_rows_before_since_are_not_evidence(tmp_path, monkeypatch):
    _record(tmp_path, monkeypatch,
            [_row("2026-09-08T10:00:00-04:00", AAA=0.5),
             _row("2026-10-01T10:00:00-04:00", AAA=0.5)],
            {"AAA": _series(SESSIONS, [100, 101, 102, 103, 104, 110, 111])})
    rep = confidence.score_forward(5, since="2026-09-28")
    assert rep.excluded == 1 and rep.n == 1


def test_weekend_rows_are_not_trading_days(tmp_path, monkeypatch):
    _record(tmp_path, monkeypatch, [_row("2026-10-03T12:00:00-04:00", AAA=0.5)],   # Saturday
            {"AAA": _series(SESSIONS, [100, 101, 102, 103, 104, 110, 111])})
    rep = confidence.score_forward(5)
    assert rep.n == 0 and rep.pending == 0
