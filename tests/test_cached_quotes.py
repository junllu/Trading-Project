"""Prices must come from real data, and a missing feed must not become a number.

Two failures guarded. First, the simulator quoting itself: with no live broker
the market primary was the PaperBroker, seeded from a 49-hour-old holdings file,
so prices never moved and every round trip netted exactly zero by construction.
Second, and worse, a symbol with no price at all falling through to a synthetic
RANDOM WALK — NEWYY was sold at 5.99 and marked at a fabricated 295.97, a
-4841% figure that swamped every real result in the record.
"""
import pytest

from app.data import cached_quotes as cq
from app.data.market_data import MarketData


@pytest.fixture
def prices(tmp_path, monkeypatch):
    monkeypatch.setattr(cq, "PRICES_DIR", tmp_path)
    (tmp_path / "AAA.csv").write_text(
        "date,close\n2026-09-03,10.0\n2026-09-04,12.5\n", encoding="utf-8")
    return tmp_path


def test_a_daily_close_is_used_when_no_minute_bar_exists(prices, monkeypatch):
    monkeypatch.setattr(cq.CachedQuotes, "_minute", lambda self, s: None)
    q = cq.CachedQuotes()
    assert q.get_quote("AAA").price == pytest.approx(12.5)
    assert q.provenance("AAA")["source"] == "daily_close"


def test_a_fresh_minute_bar_beats_the_daily_close(prices, monkeypatch):
    import time
    monkeypatch.setattr(cq.CachedQuotes, "_minute",
                        lambda self, s: (99.0, time.time() - 60))
    q = cq.CachedQuotes()
    assert q.get_quote("AAA").price == pytest.approx(99.0)
    assert q.provenance("AAA")["source"] == "minute"


def test_a_stale_minute_bar_still_wins_when_the_daily_store_is_older(prices, monkeypatch):
    """Freshness is a comparison, not a fixed threshold.

    The daily feed publishes with a lag, so overnight the old fixed-window
    fallback served Friday's close over a bar from that same afternoon — HOOD
    priced at 122.11 when it had closed at 117.34 hours earlier.
    """
    import time
    old = time.time() - (cq.MINUTE_FRESH_SECONDS + 3600)   # today, but not "live"
    monkeypatch.setattr(cq.CachedQuotes, "_minute", lambda self, s: (99.0, old))
    q = cq.CachedQuotes()
    assert q.get_quote("AAA").price == pytest.approx(99.0)
    assert q.provenance("AAA")["source"] == "minute_session_close"


def test_a_stale_minute_bar_yields_to_a_NEWER_daily_close(prices, monkeypatch):
    """The other direction: an genuinely old bar must not beat fresher daily data."""
    import time
    ancient = time.mktime(time.strptime("2026-08-01", "%Y-%m-%d"))
    monkeypatch.setattr(cq.CachedQuotes, "_minute", lambda self, s: (99.0, ancient))
    q = cq.CachedQuotes()
    assert q.get_quote("AAA").price == pytest.approx(12.5)   # 2026-09-04 close
    assert q.provenance("AAA")["source"] == "daily_close"


def test_no_data_raises_rather_than_inventing_a_price(prices, monkeypatch):
    monkeypatch.setattr(cq.CachedQuotes, "_minute", lambda self, s: None)
    q = cq.CachedQuotes()
    with pytest.raises(cq.NoPriceData):
        q.get_quote("NOSUCH")


def test_market_data_flags_a_synthetic_quote_as_not_real():
    """The guard that stops an order being placed against a random walk."""
    class Dead:
        name = "dead"
        def get_quote(self, symbol):
            raise RuntimeError("no feed")

    m = MarketData(primary=Dead())
    m.quote("GHOST")
    assert not m.is_real("GHOST")
    assert "GHOST" in m.synthetic_symbols


def test_a_real_quote_clears_the_synthetic_flag():
    from app.models import Quote

    class Live:
        name = "live"
        def __init__(self):
            self.ok = False
        def get_quote(self, symbol):
            if not self.ok:
                raise RuntimeError("no feed yet")
            return Quote(symbol=symbol, price=42.0)

    src = Live()
    m = MarketData(primary=src)
    m.quote("AAA")
    assert not m.is_real("AAA")
    src.ok = True
    m.quote("AAA")
    assert m.is_real("AAA"), "a recovered feed must clear the synthetic flag"
