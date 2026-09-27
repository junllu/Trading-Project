"""Live quotes in-process, for the whole universe, in one request.

WHY THIS CHANGES THE ECONOMICS

Every other real-time path here costs a Claude session. The Webull connector is
reachable only from inside one, which is why live coverage was capped at 100
symbols on a 30-minute cadence and why an OpenAPI subscription was on the table
just to get in-process polling.

This is an HTTP call from Python. Verified against the Webull bars on
2026-09-08 — NVDA 225.73, CVX 209.80, MRVL 225.41, HOOD 117.34, BE 277.22, all
matching exactly, unauthenticated, in 0.13 seconds. So the portal can price
itself continuously at no marginal cost.

WHAT IT DOES NOT REPLACE

Snapshots, not history. Backtests, the MAE and reward/risk studies, and breakout
measurement all read bar stores and still need them. This answers "what is it
worth right now", nothing else.

TWO FAILURE MODES IT IS BUILT AROUND

  * It is an undocumented endpoint. It can change or disappear without notice,
    so every failure is caught and the caller falls through to the cached
    source. A quote is never fabricated to keep the loop moving.
  * It is a network call in the tick path. A hung request would stall trading,
    so the timeout is short and deliberate — a late quote is worse than a
    cached one.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ..models import Quote

log = logging.getLogger("portal.tvquotes")

DEFAULT_TIMEOUT = 6.0
# Beyond this the cached source is the better answer; a live feed that has
# stopped updating looks identical to a quiet market until it is timed.
STALE_AFTER_SECONDS = 900


class TradingViewQuotes:
    """Batched live quote source. Read-only; cannot place anything."""

    name = "tradingview"

    def __init__(self, timeout: float = DEFAULT_TIMEOUT):
        self.timeout = timeout
        self._last: dict[str, tuple[float, float]] = {}   # sym -> (price, ts)
        self._available: bool | None = None
        self._consecutive_failures = 0
        self._prov: dict[str, dict[str, Any]] = {}

    # --- availability ------------------------------------------------------
    @property
    def available(self) -> bool:
        if self._available is None:
            try:
                import tradingview_screener  # noqa: F401
                self._available = True
            except Exception:
                self._available = False
                log.info("tradingview_screener not installed — live quotes disabled")
        return self._available and self._consecutive_failures < 5

    # --- the batch that makes this cheap -----------------------------------
    def fetch(self, symbols: list[str]) -> dict[str, float]:
        """One request for every symbol. Returns {} on any failure."""
        if not self.available or not symbols:
            return {}
        wanted = sorted({str(s).upper() for s in symbols})
        try:
            from tradingview_screener import Query, col
            _, df = (Query()
                     .set_markets("america")
                     .select("name", "close")
                     .where(col("name").isin(wanted))
                     .limit(len(wanted) + 50)
                     .get_scanner_data())
        except Exception as exc:
            self._consecutive_failures += 1
            log.warning("tradingview quote fetch failed (%d in a row): %s",
                        self._consecutive_failures, exc)
            return {}

        self._consecutive_failures = 0
        now = time.time()
        out: dict[str, float] = {}
        try:
            for _, row in df.iterrows():
                sym = str(row.get("name") or "").upper()
                px = row.get("close")
                if not sym or px is None:
                    continue
                px = float(px)
                if px <= 0:
                    continue
                out[sym] = px
                self._last[sym] = (px, now)
                self._prov[sym] = {"source": "tradingview", "as_of": now}
        except Exception as exc:                  # malformed frame, not fatal
            log.warning("tradingview frame parse failed: %s", exc)
        return out

    # --- the quote interface MarketData uses -------------------------------
    def get_quote(self, symbol: str) -> Quote:
        sym = symbol.upper()
        hit = self._last.get(sym)
        if hit and (time.time() - hit[1]) <= STALE_AFTER_SECONDS:
            return Quote(symbol=sym, price=hit[0])
        got = self.fetch([sym])
        if sym in got:
            return Quote(symbol=sym, price=got[sym])
        # Raised, never guessed — the caller falls through to the cached source.
        raise LookupError(f"no live quote for {sym}")

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        got = self.fetch(symbols)
        return {s: Quote(symbol=s, price=p) for s, p in got.items()}

    def is_connected(self) -> bool:
        return self.available

    def connect(self) -> None:
        return None

    def provenance(self, symbol: str | None = None) -> dict[str, Any]:
        if symbol:
            return self._prov.get(symbol.upper(), {})
        return dict(self._prov)


class LiveThenCached:
    """Live quotes when they answer, the local stores when they do not.

    The composition is the point: the network source is allowed to fail, and
    failing quietly falls back to real cached data rather than to a fabricated
    price. Provenance from whichever source answered is preserved, so a report
    can always say what a fill was priced from.
    """

    name = "live+cached"

    def __init__(self, live=None, cached=None):
        from .cached_quotes import CachedQuotes
        self.live = live if live is not None else TradingViewQuotes()
        self.cached = cached if cached is not None else CachedQuotes()
        self._served: dict[str, str] = {}

    def prime(self, symbols: list[str]) -> int:
        """Warm the live cache for a whole universe in one request."""
        got = self.live.fetch(symbols)
        for s in got:
            self._served[s] = "tradingview"
        return len(got)

    def get_quote(self, symbol: str) -> Quote:
        try:
            q = self.live.get_quote(symbol)
            self._served[symbol.upper()] = "tradingview"
            return q
        except Exception:
            q = self.cached.get_quote(symbol)     # may raise NoPriceData; correct
            self._served[symbol.upper()] = (
                self.cached.provenance(symbol).get("source") or "cached")
            return q

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        return {s: self.get_quote(s) for s in symbols}

    def is_connected(self) -> bool:
        return True

    def connect(self) -> None:
        return None

    def provenance(self, symbol: str | None = None) -> dict[str, Any]:
        if symbol:
            sym = symbol.upper()
            if self._served.get(sym) == "tradingview":
                return self.live.provenance(sym)
            return self.cached.provenance(sym)
        return {s: (self.live.provenance(s) if v == "tradingview"
                    else self.cached.provenance(s))
                for s, v in self._served.items()}

    def summary(self) -> dict[str, Any]:
        by: dict[str, int] = {}
        for v in self._served.values():
            by[v] = by.get(v, 0) + 1
        return {"quoted": len(self._served), "by_source": by,
                "live_available": self.live.available}
