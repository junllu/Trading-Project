"""Live quotes must be optional, batched, and never fabricated.

The endpoint behind this is undocumented and sits in the tick path, so two
things must hold: it can vanish without stopping trading, and a failure must
fall through to REAL cached data rather than to an invented price.
"""
import pytest

from app.data.tradingview_quotes import LiveThenCached, TradingViewQuotes
from app.models import Quote


class _FakeLive:
    name = "fake-live"

    def __init__(self, prices=None, boom=False):
        self.prices = prices or {}
        self.boom = boom
        self.calls = 0
        self.available = True

    def fetch(self, symbols):
        self.calls += 1
        if self.boom:
            raise RuntimeError("endpoint gone")
        return {s: self.prices[s] for s in symbols if s in self.prices}

    def get_quote(self, symbol):
        if self.boom or symbol not in self.prices:
            raise LookupError(symbol)
        return Quote(symbol=symbol, price=self.prices[symbol])

    def provenance(self, symbol=None):
        return {"source": "tradingview"}


class _FakeCached:
    name = "fake-cached"

    def __init__(self, prices=None):
        self.prices = prices or {}

    def get_quote(self, symbol):
        if symbol not in self.prices:
            raise LookupError(symbol)
        return Quote(symbol=symbol, price=self.prices[symbol])

    def provenance(self, symbol=None):
        return {"source": "daily_close"}


def test_live_is_preferred_when_it_answers():
    q = LiveThenCached(live=_FakeLive({"AAA": 10.0}), cached=_FakeCached({"AAA": 9.0}))
    assert q.get_quote("AAA").price == pytest.approx(10.0)
    assert q.provenance("AAA")["source"] == "tradingview"


def test_a_dead_endpoint_falls_through_to_cached_not_to_a_guess():
    q = LiveThenCached(live=_FakeLive(boom=True), cached=_FakeCached({"AAA": 9.0}))
    assert q.get_quote("AAA").price == pytest.approx(9.0)
    assert q.provenance("AAA")["source"] == "daily_close"


def test_no_source_raises_rather_than_inventing_a_price():
    q = LiveThenCached(live=_FakeLive(boom=True), cached=_FakeCached({}))
    with pytest.raises(LookupError):
        q.get_quote("GHOST")


def test_prime_batches_the_whole_universe_in_one_call():
    """Per-symbol calls would be thirty round trips a tick and a good way to
    get rate limited off an endpoint nobody promised us."""
    live = _FakeLive({"A": 1.0, "B": 2.0, "C": 3.0})
    q = LiveThenCached(live=live, cached=_FakeCached())
    assert q.prime(["A", "B", "C"]) == 3
    assert live.calls == 1


def test_missing_library_disables_live_without_raising(monkeypatch):
    tv = TradingViewQuotes()
    monkeypatch.setattr(tv, "_available", False)
    assert not tv.available
    assert tv.fetch(["AAA"]) == {}


def test_repeated_failures_trip_a_breaker():
    tv = TradingViewQuotes()
    tv._available = True
    tv._consecutive_failures = 5
    assert not tv.available, "a persistently failing endpoint must stop being called"
