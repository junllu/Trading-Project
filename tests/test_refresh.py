"""The daily-bar refresh keeps the cache current without ever losing history.

Guards the failures that matter for an unattended job: storing a still-forming
intraday bar as a close, overwriting a good file with a truncated download, and
losing a delisted name's history because the feed now returns less of it.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.data import refresh as R

ET = ZoneInfo("America/New_York")


def _et(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


def test_last_session_excludes_a_bar_still_forming():
    # Friday 2026-09-25 mid-session: Friday's bar is not final yet.
    assert R.last_complete_session(_et(2026, 9, 25, 11, 0)).isoformat() == "2026-09-24"


def test_last_session_includes_today_after_the_close_settles():
    assert R.last_complete_session(_et(2026, 9, 25, 16, 30)).isoformat() == "2026-09-25"


def test_last_session_skips_the_weekend():
    assert R.last_complete_session(_et(2026, 9, 27, 12, 0)).isoformat() == "2026-09-25"


def _rows(dates):
    return [[d, 1.0] for d in dates]


def test_accepts_a_superset():
    ok, why = R.accept(_rows(["2026-09-01", "2026-09-02"]),
                       _rows(["2026-09-01", "2026-09-02", "2026-09-03"]))
    assert ok and "+1" in why


def test_rejects_moving_the_last_bar_back():
    """EA went private: the feed now ends earlier than the file. Keep the file."""
    ok, why = R.accept(_rows(["2026-08-03", "2026-08-10"]), _rows(["2026-08-03", "2026-08-04"]))
    assert not ok and "move back" in why


def test_rejects_a_truncated_download():
    old = _rows([f"2026-01-{d:02d}" for d in range(1, 31)])
    new = old[:5] + old[-1:]
    ok, why = R.accept(old, new)
    assert not ok and "truncated" in why


def test_rejects_shrinking_history():
    ok, _ = R.accept(_rows(["2022-01-03", "2026-09-04"]), _rows(["2025-01-02", "2026-09-25"]))
    assert not ok


def test_rejects_empty():
    assert R.accept(_rows(["2026-09-01"]), [])[0] is False


@pytest.fixture
def stores(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "PRICES_DIR", tmp_path / "prices")
    monkeypatch.setattr(R, "OHLC_DIR", tmp_path / "ohlc")
    monkeypatch.setattr(R, "STATUS_PATH", tmp_path / "status.json")
    monkeypatch.setattr(R, "_held", lambda: [])
    monkeypatch.setattr(R, "universe", lambda include_held=False: [])
    (tmp_path / "prices").mkdir()
    (tmp_path / "prices" / "AAA.csv").write_text(
        "date,close\n2026-09-03,10.0\n2026-09-04,11.0\n", encoding="utf-8")
    return tmp_path


def _bar(d, c):
    return [d, c, c, c, c, 100.0]


def test_refresh_writes_both_stores_and_drops_the_live_bar(stores):
    def fake(symbols, start, through):
        assert start == "2026-09-03"          # each file's own start is honored
        return {"AAA": [_bar("2026-09-03", 9.5), _bar("2026-09-04", 10.5),
                        _bar("2026-09-25", 12.0)]}
    s = R.refresh(now=_et(2026, 9, 26, 12, 0), downloader=fake)
    text = (stores / "prices" / "AAA.csv").read_text("utf-8")
    assert text.splitlines()[-1] == "2026-09-25,12.0"
    assert "9.5" in text                      # re-adjusted history replaces the old
    assert s["written"] == 1 and s["stale"] == {} and (stores / "status.json").exists()


def test_refresh_keeps_old_file_when_download_fails(stores):
    def boom(symbols, start, through):
        raise ConnectionError("rate limited")
    s = R.refresh(now=_et(2026, 9, 26, 12, 0), downloader=boom)
    assert "AAA" in s["failed"]
    assert (stores / "prices" / "AAA.csv").read_text("utf-8").endswith("2026-09-04,11.0\n")
