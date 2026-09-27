"""Real prices for the simulator, from the freshest cache that has them.

THE BUG THIS FIXES

With no live broker configured, `portal.build()` set the market-data primary to
the PaperBroker itself. So the simulator asked itself what things were worth,
and got back the values seeded from `holdings.yaml` at startup — a file the
statusboard was separately reporting as 49 hours old. Every fill was priced at
a two-day-old number that never moved, which is why marking 27 fills to market
returned exactly 0.00% on every one. That is not a flat market; it is a closed
loop measuring itself.

Meanwhile live minute bars for the focus names sat in `data/minute/`, harvested
to the current session at `delay_minutes: 0`, and nothing priced a fill from
them.

WHAT IT DOES

Answers a quote from the freshest real source available, in order:

    1. the newest MINUTE bar close      (focus names, current to the session)
    2. the newest DAILY close            (everything else)

and refuses to answer at all when neither exists, so `MarketData` falls back to
its synthetic path and the gap is visible rather than papered over with a
plausible number.

WHY PROVENANCE IS RECORDED

Every quote carries where it came from and how old it is, readable through
`provenance()`. A simulator that cannot say whether it traded on live bars or a
four-day-old close cannot tell you whether its results mean anything — and the
whole reason this file exists is that the previous answer was silently the
latter.

This is a READ-ONLY quote source, not a broker. It cannot place anything.
"""
from __future__ import annotations

import csv
import time
from datetime import datetime, timezone
from typing import Any

from ..config import ROOT
from ..models import Quote

PRICES_DIR = ROOT / "data" / "prices"

# Beyond this a minute bar is not "live" any more and the daily close is the
# more honest answer. Generous on purpose: it must span a lunch lull and a
# missed harvest without flapping between sources mid-session.
MINUTE_FRESH_SECONDS = 6 * 3600


class NoPriceData(Exception):
    """No real price exists for this symbol. Deliberately not a zero."""


class CachedQuotes:
    """Quote source backed by the local real-price caches."""

    name = "cached"

    def __init__(self) -> None:
        self._daily: dict[str, tuple[str, float]] = {}
        self._prov: dict[str, dict[str, Any]] = {}

    # --- sources -----------------------------------------------------------
    def _minute(self, symbol: str) -> tuple[float, float] | None:
        """(price, epoch_seconds) from the newest cached minute bar."""
        try:
            from .minute import load
        except Exception:
            return None
        try:
            bars = load(symbol)
        except Exception:
            return None
        if not bars:
            return None
        last = bars[-1]
        ts = last.ts
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return float(last.close), ts.timestamp()

    def _daily_close(self, symbol: str) -> tuple[str, float] | None:
        if symbol in self._daily:
            return self._daily[symbol]
        p = PRICES_DIR / f"{symbol.upper()}.csv"
        if not p.exists():
            return None
        last_row = None
        try:
            with p.open("r", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    last_row = row
        except OSError:
            return None
        if not last_row:
            return None
        try:
            out = (last_row["date"], float(last_row["close"]))
        except (KeyError, ValueError):
            return None
        self._daily[symbol] = out
        return out

    # --- the quote interface MarketData uses -------------------------------
    def get_quote(self, symbol: str) -> Quote:
        sym = symbol.upper()
        now = time.time()

        m = self._minute(sym)
        d = self._daily_close(sym)

        if m is not None:
            price, ts = m
            age = now - ts
            if age <= MINUTE_FRESH_SECONDS:
                self._prov[sym] = {
                    "source": "minute", "age_seconds": round(age),
                    "as_of": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M"),
                }
                return Quote(symbol=sym, price=price)

            # Aged out of the live window — but still better than the daily
            # store when that store is OLDER. The daily feed publishes with a
            # lag, so overnight the fallback was serving Friday's close over
            # a bar from this afternoon: HOOD priced at 122.11 when it had
            # closed at 117.34 hours earlier. Freshness is a comparison, not a
            # fixed threshold.
            minute_day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            if d is None or minute_day > d[0]:
                self._prov[sym] = {
                    "source": "minute_session_close", "age_seconds": round(age),
                    "as_of": minute_day,
                    "why": "newer than the daily store, which lags after the close",
                }
                return Quote(symbol=sym, price=price)

        if d is not None:
            day, price = d
            self._prov[sym] = {"source": "daily_close", "as_of": day}
            return Quote(symbol=sym, price=price)

        self._prov[sym] = {"source": None, "as_of": None}
        # Raised, not zeroed. MarketData falls back to synthetic and the missing
        # feed stays visible instead of becoming a confident wrong price.
        raise NoPriceData(f"no cached price for {sym}")

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        out = {}
        for s in symbols:
            try:
                out[s] = self.get_quote(s)
            except NoPriceData:
                continue
        return out

    def is_connected(self) -> bool:
        return True

    def connect(self) -> None:
        return None

    # --- auditability ------------------------------------------------------
    def provenance(self, symbol: str | None = None) -> dict[str, Any]:
        if symbol:
            return self._prov.get(symbol.upper(), {})
        return dict(self._prov)

    def summary(self) -> dict[str, Any]:
        by_source: dict[str, int] = {}
        for v in self._prov.values():
            by_source[str(v.get("source"))] = by_source.get(str(v.get("source")), 0) + 1
        return {"quoted": len(self._prov), "by_source": by_source}
