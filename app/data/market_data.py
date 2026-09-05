"""Market data provider.

Pulls live quotes from whichever broker is available and keeps a rolling
in-memory history of closes per symbol, which the strategy layer consumes to
compute indicators. In paper mode it synthesizes a gently random-walking price
series so the whole pipeline is exercisable offline.
"""
from __future__ import annotations

import random
from collections import defaultdict, deque
from typing import Optional

from ..brokers.base import BrokerBase, BrokerError
from ..models import Quote


class MarketData:
    def __init__(self, primary: Optional[BrokerBase] = None, history_len: int = 250):
        self.primary = primary
        self.history_len = history_len
        self._history: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=history_len))
        self._last: dict[str, Quote] = {}

    def set_primary(self, broker: BrokerBase) -> None:
        self.primary = broker

    def quote(self, symbol: str) -> Quote:
        q: Optional[Quote] = None
        if self.primary is not None:
            try:
                q = self.primary.get_quote(symbol)
            except (BrokerError, Exception):
                q = None
        if q is None:
            q = self._synthetic(symbol)
        self._last[symbol] = q
        self._history[symbol].append(q.price)
        return q

    def refresh(self, symbols: list[str]) -> dict[str, Quote]:
        return {s: self.quote(s) for s in symbols}

    def history(self, symbol: str) -> list[float]:
        return list(self._history[symbol])

    def last(self, symbol: str) -> Optional[Quote]:
        return self._last.get(symbol)

    def seed_history(self, symbol: str, prices: list[float]) -> None:
        dq = self._history[symbol]
        dq.clear()
        for p in prices[-self.history_len:]:
            dq.append(float(p))
        if prices:
            self._last[symbol] = Quote(symbol=symbol, price=float(prices[-1]))

    def backfill_trend(self, symbol: str, start: float, end: float, bars: int = 80,
                       noise: float = 0.012) -> None:
        """Synthesize a plausible price path from `start` to `end`.

        A stand-in for a real historical feed: interpolates cost→current with
        mild noise, so technical indicators have something to chew on and the
        trend reflects the actual move you've experienced in the name. Replace
        with a real data provider (yfinance/broker) when available.
        """
        if start <= 0 or end <= 0 or bars < 2:
            self.seed_history(symbol, [end])
            return
        rng = random.Random(hash(symbol) & 0xFFFFFFFF)  # deterministic per symbol
        prices: list[float] = []
        for i in range(bars):
            t = i / (bars - 1)
            base = start * (1 - t) + end * t                     # linear glide
            jitter = 1 + rng.uniform(-noise, noise) * (1 - abs(2 * t - 1))
            prices.append(round(max(0.01, base * jitter), 4))
        prices[-1] = float(end)                                   # pin the last bar
        self.seed_history(symbol, prices)

    def _synthetic(self, symbol: str) -> Quote:
        hist = self._history[symbol]
        if hist:
            last = hist[-1]
            drift = random.uniform(-0.01, 0.01)
            price = round(max(1.0, last * (1 + drift)), 2)
        else:
            price = round(random.uniform(50, 300), 2)
        return Quote(symbol=symbol, price=price)
