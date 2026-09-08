"""Intraday exit measurement — the wick case is the whole point.

These build bars in memory and monkeypatch the loader, so nothing here depends
on a harvest having run or on Webull being reachable.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.analytics import intraday
from app.data.minute import MinuteBar, normalize

D0 = datetime(2026, 9, 3, 13, 30, tzinfo=timezone.utc)


def bar(i, o, h, lo, c, v=1000.0, session="RTH"):
    return MinuteBar(D0 + timedelta(minutes=i), o, h, lo, c, v, session)


@pytest.fixture
def patched(monkeypatch):
    def _install(bars):
        monkeypatch.setattr(intraday, "load",
                            lambda *a, **k: [b for b in bars
                                             if not k.get("sessions")
                                             or b.session in k["sessions"]])
    return _install


def test_normalize_rejects_impossible_ohlc():
    rows = [
        {"time": "2026-09-04T19:59:00.000+0000", "open": "10", "high": "11",
         "low": "9", "close": "10.5", "volume": "5", "trading_session": "RTH"},
        # open above the high — corrupt, must be dropped not repaired
        {"time": "2026-09-04T19:58:00.000+0000", "open": "99", "high": "11",
         "low": "9", "close": "10", "volume": "5", "trading_session": "RTH"},
    ]
    out = normalize(rows)
    assert len(out) == 1
    assert out[0].open == 10


def test_normalize_parses_webull_offset_format():
    out = normalize([{"time": "2026-09-04T19:59:00.000+0000", "open": "1",
                      "high": "2", "low": "1", "close": "2", "volume": "1",
                      "trading_session": "ATH"}])
    assert out[0].ts.tzinfo is not None
    assert out[0].ts.hour == 19 and out[0].session == "ATH"


def test_stop_not_triggered_when_level_never_touched(patched):
    patched([bar(i, 100, 101, 99.5, 100.5) for i in range(30)])
    r = intraday.stop_test("X", 15, trailing=True, entry_price=100)
    assert r.triggered is False
    assert r.regret_pp == 0.0


def test_fixed_stop_fires_on_intraday_low_a_daily_bar_would_hide(patched):
    # Opens 100, wicks to 84 mid-session, closes 99.5. A daily bar shows -0.5%
    # and no stop. A 15% stop was in fact hit.
    bars = [bar(0, 100, 100.5, 99.5, 100)]
    bars.append(bar(1, 100, 100, 84.0, 99))          # the wick
    bars += [bar(i, 99, 99.8, 98.8, 99.5) for i in range(2, 20)]
    patched(bars)

    r = intraday.stop_test("X", 15, trailing=False, entry_price=100)
    assert r.triggered is True
    assert r.trigger_price == pytest.approx(85.0)
    assert r.was_wick is True                        # recovered above 85 same day
    assert r.regret_pp > 0                           # holding beat stopping out


def test_trailing_stop_ratchets_with_the_peak(patched):
    # Runs to 120 then falls to 100. A 15% TRAILING stop is 102 off the peak,
    # so it fires; a 15% FIXED stop from 100 is 85 and does not.
    bars = [bar(0, 100, 100, 100, 100)]
    bars += [bar(i, 100 + i, 100 + i, 100 + i - 0.5, 100 + i) for i in range(1, 21)]
    bars += [bar(21 + i, 120 - i, 120 - i, 120 - i - 0.5, 120 - i) for i in range(21)]
    patched(bars)

    trail = intraday.stop_test("X", 15, trailing=True, entry_price=100)
    fixed = intraday.stop_test("X", 15, trailing=False, entry_price=100)
    assert trail.triggered is True
    assert fixed.triggered is False
    assert trail.peak_before_stop_pct == pytest.approx(0.20, abs=0.01)


def test_regret_is_negative_when_the_stop_actually_saved_you(patched):
    # Falls through the stop and keeps falling — stopping out was correct.
    bars = [bar(0, 100, 100, 100, 100)]
    bars += [bar(i, 100 - i * 2, 100 - i * 2, 100 - i * 2 - 1, 100 - i * 2)
             for i in range(1, 30)]
    patched(bars)
    r = intraday.stop_test("X", 15, trailing=False, entry_price=100)
    assert r.triggered is True
    assert r.was_wick is False
    assert r.regret_pp < 0


def test_daily_features_flags_where_the_close_sat_in_the_range(patched):
    bars = [bar(0, 100, 100, 100, 100)]
    bars += [bar(i, 100, 110, 90, 109) for i in range(1, 40)]
    patched(bars)
    feats = intraday.daily_features("X")
    assert len(feats) == 1
    f = feats[0]
    assert f.high == 110 and f.low == 90
    assert f.mfe_pct == pytest.approx(0.10)
    assert f.mae_pct == pytest.approx(-0.10)
    assert f.close_loc > 0.9                          # closed near the high


def test_vwap_is_volume_weighted_not_bar_weighted(patched):
    # One enormous print at 200 must drag VWAP far above the bar-average of ~102.
    bars = [bar(i, 100, 100, 100, 100, v=10) for i in range(10)]
    bars.append(bar(10, 200, 200, 200, 200, v=100_000))
    patched(bars)
    f = intraday.daily_features("X")[0]
    assert f.vwap > 190


def test_study_refuses_a_verdict_when_windows_overlap_into_few_samples(patched):
    """64 entries each held 10 sessions are ~6 independent observations, not 64.
    The verdict must key off the overlap-corrected count, or the study becomes a
    machine for turning one quarter into a hundred false confirmations."""
    bars = []
    for d in range(30):
        base = D0 + timedelta(days=d)
        for i in range(40):
            bars.append(MinuteBar(base + timedelta(minutes=i), 100, 101, 99, 100,
                                  1000, "RTH"))
    patched(bars)
    r = intraday.stop_study("X", 15, hold_sessions=10)
    assert r is not None
    assert r.entries > r.effective_n
    assert r.effective_n == pytest.approx(r.entries / 10)
    assert "INCONCLUSIVE" in r.verdict


def test_study_returns_none_when_the_window_exceeds_the_history(patched):
    patched([bar(i, 100, 101, 99, 100) for i in range(30)])
    assert intraday.stop_study("X", 15, hold_sessions=10) is None


def test_study_counts_a_stop_that_never_fires_as_zero_regret(patched):
    # Flat tape well inside the stop: no trigger, so holding and stopping agree.
    bars = []
    for d in range(60):
        base = D0 + timedelta(days=d)
        for i in range(40):
            bars.append(MinuteBar(base + timedelta(minutes=i), 100, 100.5, 99.5,
                                  100, 1000, "RTH"))
    patched(bars)
    r = intraday.stop_study("X", 25, hold_sessions=5)
    assert r.triggered == 0
    assert r.mean_regret_pp == 0.0


def test_sweep_reports_every_stop_width(patched):
    patched([bar(i, 100, 101, 99, 100) for i in range(30)])
    rows = intraday.stop_sweep("X", stops=(5, 10, 20), entry_price=100)
    assert [r["stop_pct"] for r in rows] == [5, 10, 20]
