"""Liveness must be cadence-relative, market-aware, and honest about NEVER.

The failure guarded: an unattended job stops and every page keeps rendering on
stale data, so the outage is invisible. The failure this must not introduce is
crying wolf every weekend, which trains the operator to ignore it.
"""
import time

from app.agent import heartbeat as hb


def test_fresh_output_inside_its_window_is_ok():
    c = hb._grade("thing", time.time() - 60, tolerance_min=120, cadence="hourly")
    assert c.status == hb.OK
    assert c.age_minutes < 2


def test_output_past_its_window_is_stale():
    c = hb._grade("thing", time.time() - 60 * 300, tolerance_min=120, cadence="hourly")
    assert c.status == hb.STALE


def test_never_having_run_is_not_the_same_as_stale():
    """'The harvest broke this morning' and 'the harvest was never set up' need
    different responses and must not look identical."""
    c = hb._grade("thing", None, tolerance_min=120, cadence="hourly")
    assert c.status == hb.NEVER
    assert c.last is None
    assert c.age_minutes is None


def test_a_market_job_is_idle_not_stale_when_the_market_is_shut():
    """Faulting the harvest every weekend would train the reader to ignore it."""
    old = time.time() - 60 * 600
    shut = hb._grade("bars", old, tolerance_min=45, cadence="30m",
                     only_when=lambda: False)
    assert shut.status == hb.IDLE

    openn = hb._grade("bars", old, tolerance_min=45, cadence="30m",
                      only_when=lambda: True)
    assert openn.status == hb.STALE


def test_scan_reports_every_component_and_never_raises():
    r = hb.scan()
    assert r["agent"] == "heartbeat"
    assert len(r["checks"]) == len(hb.PROBES)
    for c in r["checks"]:
        assert c["status"] in (hb.OK, hb.STALE, hb.NEVER, hb.IDLE)
        assert c["cadence"], f"{c['name']} declares no cadence to be judged against"


def test_a_broken_probe_is_reported_not_swallowed(monkeypatch):
    def boom():
        raise RuntimeError("probe exploded")
    monkeypatch.setattr(hb, "PROBES", (boom,))
    r = hb.scan()
    assert r["checks"][0]["status"] == hb.NEVER
    assert "probe raised" in r["checks"][0]["detail"]
    assert not r["healthy"]
