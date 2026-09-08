"""Minute store — paging cursors, merge semantics, and calendar awareness.

Hermetic: MINUTE_DIR is redirected to a tmp dir in every test, so these never
read or write the real store (which a harvest may be writing concurrently) and
never touch the network.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.data import minute


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(minute, "MINUTE_DIR", tmp_path / "minute")
    return tmp_path


def row(ts, o=100.0, h=101.0, lo=99.0, c=100.5, v=1000, session="RTH"):
    return {"time": ts, "open": str(o), "high": str(h), "low": str(lo),
            "close": str(c), "volume": str(v), "trading_session": session}


# --- ingest ---------------------------------------------------------------

def test_ingest_is_idempotent_on_overlapping_pages():
    # Paging backwards always overlaps at the seam; that must not duplicate.
    first = [row(f"2026-09-04T19:5{i}:00.000+0000") for i in range(5)]
    assert minute.ingest("X", first)["added"] == 5
    second = minute.ingest("X", first)
    assert second["added"] == 0
    assert second["total"] == 5


def test_ingest_rejects_corrupt_bars_rather_than_repairing_them():
    rows = [row("2026-09-04T19:50:00.000+0000"),
            row("2026-09-04T19:51:00.000+0000", o=999)]   # open above high
    res = minute.ingest("X", rows)
    assert res["valid"] == 1 and res["rejected"] == 1


def test_ingest_sorts_regardless_of_arrival_order():
    # Webull returns newest-first; the store must end up chronological.
    rows = [row("2026-09-04T19:59:00.000+0000"),
            row("2026-09-04T19:00:00.000+0000")]
    minute.ingest("X", rows)
    bars = minute.load("X")
    assert bars[0].ts < bars[1].ts


def test_ingest_accepts_a_json_string():
    import json
    res = minute.ingest("X", json.dumps([row("2026-09-04T19:59:00.000+0000")]))
    assert res["added"] == 1


def test_load_filters_by_session():
    minute.ingest("X", [row("2026-09-04T19:59:00.000+0000", session="RTH"),
                        row("2026-09-04T23:59:00.000+0000", session="ATH")])
    assert len(minute.load("X", sessions=("RTH",))) == 1
    assert len(minute.load("X")) == 2


# --- plan: the cursor bug this module shipped with -------------------------

def test_plan_cursors_never_land_on_a_non_trading_day():
    """Regression: cursors were derived from calendar arithmetic and landed on
    weekends. Webull silently clamps a weekend end_time to the last available
    session, so the plan re-requested the same day forever while reporting a
    gap it could never close."""
    from app.data.market_hours import is_trading_day
    p = minute.plan("X", days=60)
    assert p["requests"]
    for r in p["requests"]:
        cursor = datetime.fromtimestamp(r["args"]["end_time"] / 1000, tz=timezone.utc)
        assert is_trading_day(cursor.date()), f"cursor on non-trading day {cursor}"


def test_plan_excludes_market_holidays():
    # 2026-09-07 is Labor Day; no chunk may be anchored on it.
    p = minute.plan("X", days=10,
                    end=datetime(2026, 9, 8, tzinfo=timezone.utc))
    anchors = {datetime.fromtimestamp(r["args"]["end_time"] / 1000,
                                      tz=timezone.utc).date().isoformat()
               for r in p["requests"]}
    assert "2026-09-07" not in anchors


def test_plan_marks_covered_windows_as_skip():
    end = datetime(2026, 9, 4, 23, 59, tzinfo=timezone.utc)
    # days=1 still spans two trading days (09-03 and 09-04) because the window
    # opens mid-session on the 3rd. Both must be present for the chunk to skip.
    for day in (datetime(2026, 9, 3, 13, 30, tzinfo=timezone.utc),
                datetime(2026, 9, 4, 13, 30, tzinfo=timezone.utc)):
        minute.ingest("X", [row((day + timedelta(minutes=i)).isoformat())
                            for i in range(390)])
    p = minute.plan("X", days=1, end=end)
    assert p["requests"][0]["skip"] is True
    assert p["todo"] == 0


def test_plan_counts_a_days_bars_even_when_the_window_opens_midsession():
    """Regression: the coverage lookup was anchored at `end - days`, a mid-day
    timestamp, so bars printed earlier that morning were invisible and a
    complete session was reported as a gap to re-fetch forever."""
    end = datetime(2026, 9, 4, 23, 59, tzinfo=timezone.utc)
    day = datetime(2026, 9, 4, 13, 30, tzinfo=timezone.utc)
    minute.ingest("X", [row((day + timedelta(minutes=i)).isoformat())
                        for i in range(390)])
    # Window opens 09-04 at 23:59 minus 0 days -> same day, mid-session cutoff.
    p = minute.plan("X", days=0, end=end)
    assert p["requests"][0]["skip"] is True


def test_plan_requests_are_ordered_newest_first():
    p = minute.plan("X", days=40)
    cursors = [r["args"]["end_time"] for r in p["requests"]]
    assert cursors == sorted(cursors, reverse=True)


def test_plan_uses_the_m1_ceiling_not_the_general_one():
    # M1 allows 1650/request where every other timespan caps at 1200. Asking for
    # more silently truncates, which reads downstream as a data gap.
    p = minute.plan("X", days=30)
    assert p["bars_per_request"] == 1650
    assert all(r["args"]["count"] == "1650" for r in p["requests"])


# --- integrity reporting ---------------------------------------------------

def test_gaps_ignores_weekends_and_holidays():
    # One full session on Friday 09-04; the next weekday with bars is 09-08.
    # 09-05/06 are a weekend and 09-07 is Labor Day, so none may be reported.
    for day in (datetime(2026, 9, 4, 13, 30, tzinfo=timezone.utc),
                datetime(2026, 9, 8, 13, 30, tzinfo=timezone.utc)):
        minute.ingest("X", [row((day + timedelta(minutes=i)).isoformat())
                            for i in range(390)])
    assert minute.gaps("X") == []


def test_splits_suspected_flags_an_unadjusted_split_gap():
    # Minute bars are unadjusted: a 10:1 split prints as a -90% overnight gap.
    d1 = datetime(2026, 9, 3, 13, 30, tzinfo=timezone.utc)
    d2 = datetime(2026, 9, 4, 13, 30, tzinfo=timezone.utc)
    minute.ingest("X", [row((d1 + timedelta(minutes=i)).isoformat(),
                            o=1000, h=1001, lo=999, c=1000) for i in range(5)])
    minute.ingest("X", [row((d2 + timedelta(minutes=i)).isoformat(),
                            o=100, h=101, lo=99, c=100) for i in range(5)])
    flagged = minute.splits_suspected("X")
    assert len(flagged) == 1 and "-90" in flagged[0]


def test_coverage_counts_sessions_separately():
    minute.ingest("X", [row("2026-09-04T19:59:00.000+0000", session="RTH"),
                        row("2026-09-04T23:59:00.000+0000", session="ATH")])
    cov = minute.coverage()["X"]
    assert cov["sessions"] == {"RTH": 1, "ATH": 1}
    assert cov["days"] == 1


# --- execution timing refuses to answer on thin data -----------------------

def test_execution_refuses_a_recommendation_below_the_sample_floor(monkeypatch):
    from app.analytics import execution
    day = datetime(2026, 9, 4, 13, 30, tzinfo=timezone.utc)
    minute.ingest("X", [row((day + timedelta(minutes=i)).isoformat())
                        for i in range(390)])
    r = execution.recommend("X")
    assert r["status"] == "INSUFFICIENT"
    assert r["recommendation"] is None
    assert "noise" in r["why"]
