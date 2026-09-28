"""vix1 signal/settlement and the IV history recorder."""
import json
from datetime import date

from app.data import iv_history
from app.data.ohlc import Bar
from app.paper import vix1


def _bars(closes, start=1):
    return [Bar(f"2026-01-{i + start:02d}" if i + start <= 31 else f"2026-02-{i + start - 31:02d}",
                c, c, c, c) for i, c in enumerate(closes)]


def test_spike_needs_both_the_jump_and_fear_above_movement(monkeypatch):
    spy = _bars([100.0] * 40)                               # flat: realised vol ~0
    vix = _bars([15.0] * 39 + [20.0])                       # +33% vs 20d avg
    monkeypatch.setattr(vix1, "load", lambda s: spy if s == "SPY" else vix)
    s = vix1.signal_state()
    assert s["spike_ratio"] is None or s["spike_ratio"] > 1.2
    # zero realised vol makes the fear ratio undefined -> signal stays off
    assert s["on"] is False


def test_iv_history_keeps_the_quarter_delta_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(iv_history, "PATH", tmp_path / "iv.jsonl")
    q = {"spots": {"AAA": 100}, "contracts": [
        {"symbol": "AAA", "type": "put", "strike": 90, "expiration": "2026-11-06", "delta": -0.15, "iv": 0.30, "bid": 1, "ask": 1.1},
        {"symbol": "AAA", "type": "put", "strike": 95, "expiration": "2026-11-06", "delta": -0.26, "iv": 0.33, "bid": 2, "ask": 2.2}]}
    assert iv_history.record(q, "test", {"AAA": 0.30}, date(2026, 9, 28)) == 1
    row = json.loads((tmp_path / "iv.jsonl").read_text().splitlines()[0])
    assert row["put"]["strike"] == 95 and row["iv_rv"] == 1.1
