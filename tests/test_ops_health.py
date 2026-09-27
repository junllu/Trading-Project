"""Ops health judges OUTCOMES — a job that exits 0 and writes nothing fails."""
import json
from datetime import date, datetime

import pytest

from app.agent import autonomy, ops_health as oh


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(oh, "DATA", tmp_path)
    monkeypatch.setattr(oh, "LEDGER", tmp_path / "ops_health.jsonl")
    monkeypatch.setattr(oh, "LATEST", tmp_path / "ops_health.json")
    return tmp_path


def test_due_day_waits_for_due_time():
    from datetime import time
    mon_early = datetime(2026, 9, 28, 8, 0)
    assert oh._due_day(time(9, 45), mon_early) == date(2026, 9, 25)     # Friday
    assert oh._due_day(time(9, 45), datetime(2026, 9, 28, 10, 0)) == date(2026, 9, 28)


def test_cycle_that_claimed_the_day_but_wrote_no_plan_fails(data):
    (data / "runs").mkdir()
    (data / "runs" / "daily_cycle_2026-09-28.claim").write_text("{}")
    (data / "trade_plan.json").write_text(json.dumps({"generated": "2026-09-25 09:00:00"}))
    ok, detail = oh._daily_cycle(date(2026, 9, 28))
    assert not ok and "2026-09-25" in detail


def test_forward_record_needs_a_row_for_the_day(data):
    (data / "forward_record.jsonl").write_text(
        json.dumps({"recorded_at_et": "2026-09-16T10:00:00-04:00"}) + "\n")
    assert oh._forward_record(date(2026, 9, 28))[0] is False
    with (data / "forward_record.jsonl").open("a") as fh:
        fh.write(json.dumps({"recorded_at_et": "2026-09-28T15:40:00-04:00"}) + "\n")
    assert oh._forward_record(date(2026, 9, 28))[0] is True


def test_ledger_keeps_last_verdict_per_day_and_feeds_autonomy(data):
    rows = [{"day": "2026-09-28", "job": "daily_cycle", "ok": False, "detail": ""},
            {"day": "2026-09-28", "job": "daily_cycle", "ok": True, "detail": "repaired"},
            {"day": "2026-09-29", "job": "daily_cycle", "ok": True, "detail": ""}]
    oh.LEDGER.write_text("\n".join(json.dumps(r) for r in rows))
    led = oh.ledger_by_job()
    assert led["daily_cycle"] == {"2026-09-28": True, "2026-09-29": True}
    r = autonomy.ops_receipt("daily_plan", "daily_cycle", led)
    assert r.runs == 2 and r.passed == 2 and r.pass_rate == 1.0
