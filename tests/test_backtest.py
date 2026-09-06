import pytest

from app.backtest import compare
from app.backtest.data import MAX_SYMBOLS, load_prices, synthetic
from app.backtest.engine import Backtest
from app.backtest.setups import SETUPS, build_setup


def test_symbol_cap_enforced():
    with pytest.raises(ValueError):
        synthetic(["A", "B", "C", "D", "E", "F"])          # 6 > MAX_SYMBOLS
    assert MAX_SYMBOLS == 5


def test_synthetic_and_backtest_runs():
    data = synthetic(["MRVL", "NVDA", "TSLA"], days=300, seed=1)
    assert len(data) == 300
    res = Backtest(build_setup("A"), data, starting_cash=100000).run()
    d = res.to_dict()
    assert d["final_equity"] > 0
    assert d["trades"] > 0
    assert len(res.equity_curve) == 300
    assert -1.0 <= res.total_return


def test_buy_and_hold_deploys_and_holds():
    data = synthetic(["MRVL", "NVDA"], days=200, seed=2)
    res = Backtest(build_setup("C"), data, starting_cash=100000).run()
    # buy & hold makes exactly one trade per symbol (initial deployment)
    assert res.trades == 2


def test_compare_sorts_by_cagr():
    data = synthetic(["MRVL", "NVDA", "TSLA"], days=300, seed=3)
    rows = compare([SETUPS["A"], SETUPS["C"], SETUPS["D"]], data)
    assert len(rows) == 3
    cagrs = [r["cagr_pct"] for r in rows]
    assert cagrs == sorted(cagrs, reverse=True)


def test_deterministic():
    d1 = synthetic(["MRVL", "NVDA"], days=250, seed=9)
    d2 = synthetic(["MRVL", "NVDA"], days=250, seed=9)
    r1 = Backtest(build_setup("A"), d1).run().to_dict()["final_equity"]
    r2 = Backtest(build_setup("A"), d2).run().to_dict()["final_equity"]
    assert r1 == r2


def test_load_prices_missing_data_raises():
    # No CSV, no network in tests -> clear error telling the user how to supply data.
    with pytest.raises(RuntimeError):
        load_prices(["___NOPE___"], source="csv")
