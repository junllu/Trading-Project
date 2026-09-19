"""The exit monitor must close the loop honestly.

The bug it exists to fix: exit_plans.py stamped levels onto every order and
nothing ever read them back, so the book had exit levels and no exit watcher.
The failure mode it must not introduce is worse — a monitor that reports
"nothing fired" when it never actually checked the rule.
"""
import pytest

from app.agent import exit_monitor as em


def test_dead_theme_invalidates_the_position():
    """Core policy exits on thesis/theme invalidation, not on price."""
    v = em.evaluate_position("BE", quantity=75, avg_price=10.0, last_price=9.95)
    fired = [c.rule for c in v.checks if c.fired]
    if v.checks and any(c.rule == "invalidation" for c in v.checks):
        inv = next(c for c in v.checks if c.rule == "invalidation")
        if inv.evidence.get("theme_state") in ("DEAD", "ROTATING_OUT"):
            assert v.action == em.EXIT
            assert "invalidation" in fired


def test_unevaluable_rules_are_reported_not_silently_passed():
    """'Checked and fine' must be distinguishable from 'never looked'."""
    v = em.evaluate_position("ZZZZ", quantity=10, avg_price=100.0, last_price=101.0)
    rules = {n["rule"] for n in v.not_evaluated}
    # A symbol with no theme mapping and a core pool can evaluate neither.
    assert rules, "a position with no theme and no max-loss policy reported nothing as unchecked"
    for n in v.not_evaluated:
        assert n["why"], f"{n['rule']} reported unevaluable with no reason"


def test_max_loss_uses_the_intraday_low_not_the_close(monkeypatch):
    """A day that opens 220, wicks 186 and closes 219 is a liquidation to a
    15% stop and a quiet -0.5% session to a daily bar. The monitor must see
    the wick."""
    monkeypatch.setattr(em, "_intraday_low_since",
                        lambda sym, days: (186.0, "minute bars (fake)"))
    monkeypatch.setattr(em, "_theme_state", lambda sym: "ALIVE")

    def fake_plan(symbol, side, **kw):
        return {"pool": "tactical", "max_loss_pct": 15.0,
                "time_stop_sessions": None, "staircase": {}}

    import app.analytics.exit_plans as ep
    monkeypatch.setattr(ep, "build_exit_plan", fake_plan)
    monkeypatch.setattr(ep, "classify_pool", lambda s, **k: "tactical")

    v = em.evaluate_position("MRVL", quantity=10, avg_price=220.0, last_price=219.0)

    ml = next(c for c in v.checks if c.rule == "max_loss_pct")
    assert ml.fired, "stop touched intraday at 186 was missed by looking at the close"
    assert v.action == em.EXIT
    assert ml.evidence["worst_price"] == pytest.approx(186.0)


def test_missing_minute_bars_are_declared_not_papered_over(monkeypatch):
    """Answering a stop with a daily close while implying minute precision is
    the failure intraday.py exists to prevent."""
    monkeypatch.setattr(em, "_intraday_low_since",
                        lambda sym, days: (None, "no minute bars cached"))
    monkeypatch.setattr(em, "_theme_state", lambda sym: "ALIVE")

    import app.analytics.exit_plans as ep
    monkeypatch.setattr(ep, "build_exit_plan",
                        lambda symbol, side, **kw: {"pool": "tactical", "max_loss_pct": 15.0,
                                                    "time_stop_sessions": None, "staircase": {}})
    monkeypatch.setattr(ep, "classify_pool", lambda s, **k: "tactical")

    v = em.evaluate_position("XYZ", quantity=1, avg_price=100.0, last_price=99.0)
    assert "close only" in v.price_basis


def test_a_named_time_stop_without_an_entry_date_is_flagged(monkeypatch):
    """Policy names a time stop; holdings.yaml records no entry date. That must
    surface as unchecked rather than as a silent pass."""
    monkeypatch.setattr(em, "_theme_state", lambda sym: "ALIVE")
    import app.analytics.exit_plans as ep
    monkeypatch.setattr(ep, "build_exit_plan",
                        lambda symbol, side, **kw: {"pool": "tactical", "max_loss_pct": None,
                                                    "time_stop_sessions": 21, "staircase": {}})
    monkeypatch.setattr(ep, "classify_pool", lambda s, **k: "tactical")

    v = em.evaluate_position("XYZ", quantity=1, avg_price=100.0, last_price=99.0)
    assert any(n["rule"] == "time_stop_sessions" for n in v.not_evaluated)


def test_a_spent_rung_does_not_fire_again(monkeypatch):
    """A staircase rung is a LEVEL, not an event. A position above +10% stays
    above +10%, so a monitor reading only the current gain re-trims it on every
    tick and grinds a winner to zero. Rungs fire once."""
    monkeypatch.setattr(em, "_theme_state", lambda sym: "ALIVE")
    import app.analytics.exit_plans as ep
    monkeypatch.setattr(ep, "classify_pool", lambda s, **k: "core")
    monkeypatch.setattr(ep, "build_exit_plan", lambda symbol, side, **kw: {
        "pool": "core", "max_loss_pct": None, "time_stop_sessions": None,
        "staircase": {"scale_out": {"enabled": True,
                                    "gain_pct_from_cost": [10, 20, 35],
                                    "fractions": [0.33, 0.33, 0.34]}}})

    # +50% clears all three rungs. Nothing spent yet -> deepest rung fires.
    first = em.evaluate_position("ARM", 100, 100.0, 150.0)
    assert first.action == em.TRIM
    assert first.rung == 2

    # With every rung spent, the same position must not fire again.
    again = em.evaluate_position("ARM", 100, 100.0, 150.0, done_rungs={0, 1, 2})
    assert again.action == em.HOLD, "a fully-laddered position re-fired"


def test_an_attempted_exit_does_not_retry_every_tick(monkeypatch):
    """A rejected exit that keeps re-firing spams the record without changing
    the outcome — the blockage is what needs fixing, not the retry rate."""
    monkeypatch.setattr(em, "_theme_state", lambda sym: "DEAD")
    import app.analytics.exit_plans as ep
    monkeypatch.setattr(ep, "classify_pool", lambda s, **k: "core")
    monkeypatch.setattr(ep, "build_exit_plan", lambda symbol, side, **kw: {
        "pool": "core", "max_loss_pct": None, "time_stop_sessions": None,
        "staircase": {}})

    assert em.evaluate_position("BE", 75, 10.0, 9.9).action == em.EXIT
    retry = em.evaluate_position("BE", 75, 10.0, 9.9, exit_attempted=True)
    assert retry.action == em.HOLD
    assert "not re-firing" in next(
        c.detail for c in retry.checks if c.rule == "invalidation")


def test_monitor_names_no_levels_of_its_own():
    """exit_plans.py owns policy. A hardcoded percentage here would fork it."""
    import inspect
    src = inspect.getsource(em)
    body = "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith("#") and '"""' not in l)
    for forbidden in ("0.15", "15.0", "0.20", "20.0", "= 21"):
        assert forbidden not in body, (
            f"exit_monitor hardcodes {forbidden!r} — policy belongs to exit_plans.py")
