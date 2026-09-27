from datetime import date

from app.campaign import Campaign


def make(**kw):
    base = dict(start_capital=131000, target=1_000_000,
                started="2026-09-05", deadline="2027-12-31",
                focus_symbols=["MRVL", "NVDA", "TSLA"], trailing_drawdown_halt=0.20)
    base.update(kw)
    return Campaign(**base)


def test_progress_and_required_return():
    c = make()
    now = date(2026, 9, 5)
    st = c.status(131000, now)
    assert 0.12 < st.progress < 0.14           # 131k / 1M
    assert st.multiple_remaining > 7           # 7.6x to go
    assert st.required_cagr > 1.0              # >100%/yr — an honest moonshot
    assert st.days_remaining > 400


def test_high_water_mark_and_drawdown_halt():
    c = make(trailing_drawdown_halt=0.20)
    c.update_hwm(200000)                        # peak
    assert not c.breached(170000)              # 15% down — ok
    assert c.breached(150000)                  # 25% down — halt
    st = c.status(150000, date(2026, 9, 5))
    assert st.breached and st.drawdown * 100 >= 20
    assert st.to_dict()["drawdown_pct"] >= 20


def test_hwm_only_ratchets_up():
    c = make()
    c.update_hwm(150000)
    c.update_hwm(140000)                        # lower — ignored
    assert c.high_water_mark == 150000


def test_pace_ahead_and_behind():
    c = make()
    now = date(2027, 1, 1)                      # ~4 months in
    exp = c.expected_equity(now)
    ahead = c.status(exp * 1.3, now)
    behind = c.status(exp * 0.7, now)
    assert ahead.pace == "ahead"
    assert behind.pace == "behind"


def test_derisk_ramps_toward_deadline():
    c = make(derisk_window_days=210, derisk_floor=0.2)
    assert c.derisk_factor(date(2026, 9, 5)) == 1.0        # far out — full risk
    near = c.derisk_factor(date(2027, 12, 1))              # ~1 month out
    assert 0.2 <= near < 0.5
    assert c.phase(date(2027, 12, 20)) == "exit"
    assert c.phase(date(2026, 9, 5)) == "accumulate"


def test_focus_symbols_default():
    c = make(focus_symbols=["MRVL", "NVDA", "TSLA"])
    st = c.status(131000, date(2026, 9, 5))
    assert st.focus_symbols == ["MRVL", "NVDA", "TSLA"]


def test_hwm_survives_a_restart(tmp_path):
    """A drawdown that spans a restart must still trip the halt."""
    from app.campaign import campaign as cm
    path = tmp_path / "state.json"
    a = cm.Campaign(start_capital=100_000, state_path=path)
    a.update_hwm(150_000)
    state = cm.load_state(path)
    b = cm.Campaign(start_capital=state["start_capital"],
                    high_water_mark=state["high_water_mark"], state_path=path)
    b.update_hwm(110_000)                      # restart at a lower value
    assert b.high_water_mark == 150_000
    assert b.breached(110_000)                 # 26.7% below the persisted peak


def test_pace_is_not_on_track_by_construction():
    """Anchored at the true start, a flat book three weeks in is behind."""
    from datetime import date
    from app.campaign import campaign as cm
    c = cm.Campaign(start_capital=100_000, started="2026-09-05", deadline="2027-12-31")
    st = c.status(100_000, now=date(2026, 12, 1))
    assert st.pace == "behind"
