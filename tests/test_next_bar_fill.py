"""Next-bar fill discipline + sleeve gate checks."""
from __future__ import annotations

from app.analytics.sleeve import evaluate_from_summary
from app.backtest.data import synthetic
from app.backtest.engine import Backtest
from app.backtest.setups import build_setup


def test_backtest_runs_with_next_bar_fills():
    data = synthetic(["MRVL", "NVDA"], days=120, seed=11)
    res = Backtest(build_setup("A"), data, starting_cash=100_000, warmup=35).run()
    assert res.final_equity > 0
    assert len(res.equity_curve) == 120


def test_plan_does_not_mutate_shares_until_fill():
    data = synthetic(["MRVL", "NVDA"], days=80, seed=3)
    bt = Backtest(build_setup("A"), data, starting_cash=100_000, warmup=35)
    shares = {s: 0.0 for s in data.symbols}
    cost_basis = {s: 0.0 for s in data.symbols}
    t = 40
    prices = {s: data.closes[s][t] for s in data.symbols}
    plan = bt._plan_rebalance(t, prices, shares, cost_basis, 100_000.0, 100_000.0, False)
    assert all(v == 0.0 for v in shares.values())
    if not plan:
        return
    n, _ = bt._fill_orders(
        plan, t + 1, {s: data.closes[s][t + 1] for s in data.symbols},
        shares, cost_basis, 100_000.0)
    assert n >= 1
    assert sum(shares.values()) > 0


def test_negative_edge_forces_zero_live_sleeve():
    s = evaluate_from_summary({
        "n": 100, "pending": 0, "horizon": 5,
        "hit_rate_pct": 40.0,
        "mean_signal_return_pct": -0.5,
        "mean_buyhold_return_pct": 0.5,
        "edge_vs_hold_pp": -1.0,
    })
    assert s.recommended_sleeve_usd == 0
