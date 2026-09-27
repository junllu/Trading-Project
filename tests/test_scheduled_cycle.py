"""The daily cycle runs at most once per day, however many schedulers are armed."""
from datetime import date

from app.agent import scheduled


def test_first_claim_wins_second_skips(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduled, "CLAIM_DIR", tmp_path)
    d = date(2026, 9, 28)
    assert scheduled.claim_today("dashboard", d) is True
    assert scheduled.claim_today("task", d) is False


def test_each_day_is_claimed_separately(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduled, "CLAIM_DIR", tmp_path)
    assert scheduled.claim_today("task", date(2026, 9, 28))
    assert scheduled.claim_today("task", date(2026, 9, 29))
