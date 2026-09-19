"""Executed decisions are scored against doing nothing, and only once resolved.

Two failures guarded: scoring a half-elapsed horizon to make the number look
better, and crediting the strategy with BACKLOG fills — rungs crossed before the
monitor existed, which are catch-up rather than decisions it made.
"""
import time

import pytest

from app.analytics import decision_score as ds
from app.engine import blotter
from app.models import Order, OrderStatus, Side


@pytest.fixture(autouse=True)
def _prices(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "PRICES_DIR", tmp_path)
    # A falling series: 100 -> 90 over five sessions.
    rows = ["date,close"] + [
        f"2026-09-{d:02d},{100 - (d - 1) * 2}" for d in range(1, 9)]
    (tmp_path / "FALL.csv").write_text("\n".join(rows), encoding="utf-8")
    return tmp_path


def _fill(symbol, side, price, strategy="daily_agent", reason=None, ts=None):
    o = Order(symbol=symbol, side=side, quantity=1, strategy=strategy)
    o.status = OrderStatus.FILLED
    o.filled_price = price
    o.reason = reason
    row = blotter.record(o, "paper")
    if ts is not None:
        row["ts"] = ts
    return row


def test_a_sell_is_scored_on_the_fall_it_avoided(monkeypatch):
    """Selling before a decline beats holding. That is the whole benchmark."""
    fills = [_fill("FALL", Side.SELL, 100.0,
                   ts=time.mktime(time.strptime("2026-09-01", "%Y-%m-%d")))]
    monkeypatch.setattr(blotter, "rows", lambda *a, **k: fills)

    rep = ds.score(horizon_sessions=5)
    assert len(rep.scored) == 1
    d = rep.scored[0]
    assert d.forward_pct < 0, "price should have fallen"
    assert d.edge_pct > 0, "a sell before a fall must score positively"
    assert d.right


def test_a_buy_into_a_decline_scores_negatively(monkeypatch):
    fills = [_fill("FALL", Side.BUY, 100.0,
                   ts=time.mktime(time.strptime("2026-09-01", "%Y-%m-%d")))]
    monkeypatch.setattr(blotter, "rows", lambda *a, **k: fills)

    d = ds.score(horizon_sessions=5).scored[0]
    assert d.edge_pct < 0
    assert not d.right


def test_an_unresolved_horizon_is_excluded_not_extrapolated(monkeypatch):
    """No peeking at partial outcomes."""
    fills = [_fill("FALL", Side.SELL, 100.0,
                   ts=time.mktime(time.strptime("2026-09-07", "%Y-%m-%d")))]
    monkeypatch.setattr(blotter, "rows", lambda *a, **k: fills)

    rep = ds.score(horizon_sessions=5)
    assert rep.scored == []
    assert rep.unresolved == 1


def test_backlog_fills_are_not_credited_to_the_strategy(monkeypatch):
    fills = [_fill("FALL", Side.SELL, 100.0, strategy="exit_monitor",
                   reason="exit_monitor: BACKLOG TRIM — staircase_scale_out",
                   ts=time.mktime(time.strptime("2026-09-01", "%Y-%m-%d")))]
    monkeypatch.setattr(blotter, "rows", lambda *a, **k: fills)

    rep = ds.score(horizon_sessions=5)
    assert rep.scored == []
    assert rep.skipped_backlog == 1
    # ...unless explicitly asked for.
    assert len(ds.score(horizon_sessions=5, include_backlog=True).scored) == 1


def test_sleeves_are_scored_separately(monkeypatch):
    t = time.mktime(time.strptime("2026-09-01", "%Y-%m-%d"))
    fills = [_fill("FALL", Side.SELL, 100.0, strategy="exit_monitor", ts=t),
             _fill("FALL", Side.BUY, 100.0, strategy="daily_agent", ts=t)]
    monkeypatch.setattr(blotter, "rows", lambda *a, **k: fills)

    d = ds.score(horizon_sessions=5).to_dict()
    assert d["by_strategy"]["exit_monitor"]["expectancy_pct"] > 0
    assert d["by_strategy"]["daily_agent"]["expectancy_pct"] < 0


def test_expectancy_not_win_rate_decides():
    """The reason expectancy is the headline: an 80% win rate loses money when
    the losses are large, and a 30% win rate makes money when the wins are."""
    from app.analytics.decision_score import ScoreReport, ScoredDecision

    def mk(edges):
        rep = ScoreReport(horizon_sessions=5)
        rep.scored = [
            ScoredDecision(symbol="X", side="buy", strategy="s", when="",
                           fill_price=100.0, later_price=100.0,
                           horizon_sessions=5, forward_pct=e, edge_pct=e)
            for e in edges]
        return rep.to_dict()["overall"]

    high_win = mk([1.0] * 8 + [-12.0] * 2)     # wins 80% of the time
    low_win = mk([-1.0] * 7 + [9.0] * 3)       # wins 30% of the time

    assert high_win["win_rate_pct"] > low_win["win_rate_pct"]
    assert high_win["expectancy_pct"] < 0, "high win rate, negative expectancy"
    assert low_win["expectancy_pct"] > 0, "low win rate, positive expectancy"
    assert low_win["payoff_ratio"] > high_win["payoff_ratio"]


def test_worst_losing_streak_is_reported():
    """Expectancy is an average; it says nothing about surviving the path."""
    from app.analytics.decision_score import ScoreReport, ScoredDecision
    rep = ScoreReport(horizon_sessions=5)
    rep.scored = [
        ScoredDecision(symbol="X", side="buy", strategy="s", when="",
                       fill_price=100.0, later_price=100.0, horizon_sessions=5,
                       forward_pct=e, edge_pct=e)
        for e in [1.0, -1.0, -1.0, -1.0, -1.0, 5.0, -1.0]]
    assert rep.to_dict()["overall"]["worst_losing_streak"] == 4
