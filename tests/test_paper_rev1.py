"""rev1 paper engine: same signal definitions and exit mechanics as the backtest."""
import json

import pytest

from app.data.ohlc import Bar
from app.paper import rev1


def _bars(n, start="2026-01-01", px=100.0):
    from datetime import date, timedelta
    d0 = date.fromisoformat(start)
    return [Bar((d0 + timedelta(days=i)).isoformat(), px, px + 1, px - 1, px) for i in range(n)]


def test_fresh_break_needs_a_new_cross():
    bars = _bars(30)
    bars[29] = Bar(bars[29].date, 100, 100, 97, 97)          # close 97 < 20d low 99
    assert rev1._fresh_break(bars, 29, 20)
    bars[28] = Bar(bars[28].date, 100, 100, 97, 97)          # already broken yesterday
    assert not rev1._fresh_break(bars, 29, 20)


def test_live_price_break_uses_the_same_levels():
    bars = _bars(30)
    assert rev1._fresh_break(bars, 29, 20, price=98.5)
    assert not rev1._fresh_break(bars, 29, 20, price=99.5)


def test_shadow_exit_ignores_the_entry_bar_range():
    bars = _bars(40)
    bars[10] = Bar(bars[10].date, 100, 101, 50, 100)         # crash BEFORE the fill
    assert rev1._shadow_exit(bars, 10, 100.0, 1.0, "X3")[2] != 10


@pytest.fixture
def paper(tmp_path, monkeypatch):
    for name in ("DIR", "STATE", "TRADES", "SIGNALS", "PREVIEW", "SCORE"):
        monkeypatch.setattr(rev1, name, tmp_path / getattr(rev1, name).name)
    monkeypatch.setattr(rev1, "DIR", tmp_path)
    return tmp_path


def test_close_job_signals_then_fills_at_next_open_then_exits(paper, monkeypatch):
    aaa = _bars(300)
    spy = _bars(300)
    aaa[250] = Bar(aaa[250].date, 100, 100, 97, 97)          # 20d breakdown on day 250
    store = {"AAA": aaa[:251], "SPY": spy[:251]}
    monkeypatch.setattr(rev1, "universe", lambda: ["AAA"])
    monkeypatch.setattr(rev1, "load", lambda s: store.get(s, []))

    r = rev1.close_job(today=aaa[250].date, ensure_fresh=False)
    assert r["new_signals"]["rev1.S2"] == 1
    pend = [p for p in json.loads(rev1.STATE.read_text())["positions"] if p["track"] == "rev1.S2"]
    assert pend[0]["status"] == "pending"

    aaa[251] = Bar(aaa[251].date, 98, 99, 97, 98)            # next open fills at 98
    store.update(AAA=aaa[:252], SPY=spy[:252])
    rev1.close_job(today=aaa[251].date, ensure_fresh=False)
    pos = [p for p in json.loads(rev1.STATE.read_text())["positions"] if p["track"] == "rev1.S2"]
    assert pos[0]["status"] == "open" and pos[0]["entry_price"] == 98

    for i in range(252, 272):                                # flat: time stop after 20
        store.update(AAA=aaa[:i + 1], SPY=spy[:i + 1])
        rev1.close_job(today=aaa[i].date, ensure_fresh=False)
    trades = [json.loads(x) for x in rev1.TRADES.read_text().splitlines()]
    s2 = [t for t in trades if t["track"] == "rev1.S2"]
    assert len(s2) == 1 and s2[0]["hold"] == 20


def test_close_job_is_idempotent_per_day(paper, monkeypatch):
    aaa, spy = _bars(260), _bars(260)
    monkeypatch.setattr(rev1, "universe", lambda: ["AAA"])
    monkeypatch.setattr(rev1, "load", lambda s: {"AAA": aaa, "SPY": spy}.get(s, []))
    rev1.close_job(today=aaa[-1].date, ensure_fresh=False)
    assert "skipped" in rev1.close_job(today=aaa[-1].date, ensure_fresh=False)
