"""wheel1 cash accounting and rules — the parts where a paper bug flatters results."""
import json
from datetime import date

import pytest

from app.data.ohlc import Bar
from app.paper import wheel


@pytest.fixture
def w(tmp_path, monkeypatch):
    for name in ("DIR", "STATE", "LEDGER", "PLAN", "CURVE", "SCORE"):
        monkeypatch.setattr(wheel, name, tmp_path / getattr(wheel, name).name)
    bars = {"AAA": [Bar(f"2026-10-{d:02d}", 50, 51, 49, 50) for d in range(1, 31)]}
    monkeypatch.setattr(wheel, "load", lambda s: bars.get(s, []))
    wheel.PLAN.write_text(json.dumps({"new_puts": [{"symbol": "AAA", "rv20": 0.30}]}))
    return bars


def _put(strike=45.0, bid=1.0, ask=1.1, delta=-0.25, iv=0.40, exp="2026-11-13"):
    return {"instrument_id": f"p{strike}", "symbol": "AAA", "type": "put", "strike": strike,
            "expiration": exp, "bid": bid, "ask": ask, "mark": (bid + ask) / 2, "delta": delta, "iv": iv}


def test_sells_put_at_bid_and_reserves_collateral(w):
    r = wheel.step({"spots": {"AAA": 50, "SPY": 500}, "contracts": [_put()]}, date(2026, 10, 1))
    assert [e["event"] for e in r["events"]] == ["sold_put"]
    assert r["cash"] == 50_000 + 100 - wheel.FEE


def test_iv_below_threshold_is_skipped(w):
    r = wheel.step({"spots": {"AAA": 50}, "contracts": [_put(iv=0.33)]}, date(2026, 10, 1))
    assert r["events"][0]["event"] == "skip_iv_too_low"          # 0.33 < 1.2 * 0.30


def test_earnings_before_expiry_is_skipped(w):
    r = wheel.step({"spots": {"AAA": 50}, "earnings": {"AAA": "2026-10-20"}, "contracts": [_put()]},
                   date(2026, 10, 1))
    assert r["events"][0]["event"] == "skip_earnings"


def test_assignment_settles_on_expiry_close_and_sets_basis(w):
    wheel.step({"spots": {"AAA": 50, "SPY": 500}, "contracts": [_put(exp="2026-10-10")]}, date(2026, 9, 1))
    w["AAA"][9] = Bar("2026-10-10", 44, 44, 40, 42)              # closes below the 45 strike
    wheel.PLAN.write_text(json.dumps({"new_puts": []}))
    r = wheel.step({"spots": {"AAA": 42, "SPY": 500}, "contracts": []}, date(2026, 10, 12))
    assert r["events"][0]["event"] == "assigned"
    st = json.loads(wheel.STATE.read_text())
    assert st["stocks"]["AAA"]["basis"] == 44.0                  # 45 strike - 1.00 credit
    assert round(st["cash"], 2) == round(50_000 + 100 - wheel.FEE - 4500, 2)


def test_take_profit_buys_back_at_ask(w):
    wheel.step({"spots": {"AAA": 50}, "contracts": [_put()]}, date(2026, 10, 1))
    wheel.PLAN.write_text(json.dumps({"new_puts": []}))
    r = wheel.step({"spots": {"AAA": 53}, "contracts": [_put(bid=0.40, ask=0.45)]}, date(2026, 10, 2))
    assert r["events"][0]["event"] == "bought_back_take_profit"
    assert r["events"][0]["pnl"] == round(100 * (1.0 - 0.45) - 2 * wheel.FEE, 2)
