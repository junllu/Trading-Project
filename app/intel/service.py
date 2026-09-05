"""IntelService — orchestrates the whole intelligence pipeline.

    sources (Grok live-search + X.com)  ->  events
        ->  sentiment aggregation per symbol
        ->  geopolitical assessment (our own engine)
        ->  a briefing the dashboard and strategies consume.

This is the single object the rest of the app talks to. It caches the last
briefing so the trading loop isn't hammering APIs every tick.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..models import Side, Signal
from .events import Event, EventCategory, Sentiment
from .geopolitics import GeopoliticalEngine
from .grok import GrokClient
from .xfeed import XFeed


@dataclass
class Briefing:
    ts: float
    events: list[Event]
    geo: dict                                   # GeoAssessment.to_dict()
    symbol_sentiment: dict[str, float]          # ticker -> aggregate score
    x_buzz: dict[str, dict] = field(default_factory=dict)
    grok_note: str = ""

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "age_seconds": round(time.time() - self.ts, 1),
            "events": [e.to_dict() for e in self.events],
            "geo": self.geo,
            "symbol_sentiment": {k: round(v, 3) for k, v in self.symbol_sentiment.items()},
            "x_buzz": self.x_buzz,
            "grok_note": self.grok_note,
        }


class IntelService:
    def __init__(self, grok: GrokClient | None = None, xfeed: XFeed | None = None,
                 geo: GeopoliticalEngine | None = None, cache_seconds: int = 300):
        self.grok = grok or GrokClient()
        self.xfeed = xfeed or XFeed()
        self.geo_engine = geo or GeopoliticalEngine()
        self.cache_seconds = cache_seconds
        self._last: Briefing | None = None

    @property
    def sources_live(self) -> dict[str, bool]:
        return {"grok": self.grok.live, "x.com": self.xfeed.live}

    def briefing(self, symbols: list[str], force: bool = False, use_x: bool = False) -> Briefing:
        if not force and self._last and (time.time() - self._last.ts) < self.cache_seconds:
            return self._last

        events = self.grok.fetch_events(focus=symbols)

        x_buzz: dict[str, dict] = {}
        if use_x:
            for sym in symbols:
                buzz = self.xfeed.buzz(sym)
                x_buzz[sym] = buzz.to_dict()
                ev = self.xfeed.buzz_to_event(buzz)
                if ev:
                    events.append(ev)

        geo = self.geo_engine.assess(events)
        symbol_sentiment = self._aggregate_sentiment(events, symbols, geo.ticker_bias)

        self._last = Briefing(
            ts=time.time(), events=events, geo=geo.to_dict(),
            symbol_sentiment=symbol_sentiment, x_buzz=x_buzz,
        )
        return self._last

    def _aggregate_sentiment(self, events: list[Event], symbols: list[str],
                             ticker_bias: dict[str, float]) -> dict[str, float]:
        """Blend per-event sentiment (weighted by importance) with geo ticker bias."""
        acc: dict[str, float] = {}
        wsum: dict[str, float] = {}
        for e in events:
            for sym in e.symbols:
                acc[sym] = acc.get(sym, 0.0) + e.sentiment_score * e.importance
                wsum[sym] = wsum.get(sym, 0.0) + e.importance
        out: dict[str, float] = {}
        for sym in set(list(acc) + symbols + list(ticker_bias)):
            base = acc.get(sym, 0.0) / wsum[sym] if wsum.get(sym) else 0.0
            geo_component = ticker_bias.get(sym, 0.0)
            out[sym] = max(-1.0, min(1.0, 0.7 * base + 0.3 * geo_component))
        return out

    def signals_for(self, symbols: list[str], order_value: float = 1000.0,
                    threshold: float = 0.4) -> list[Signal]:
        """Turn strong aggregate sentiment into (reviewable) directional signals."""
        brief = self.briefing(symbols)
        sigs: list[Signal] = []
        for sym in symbols:
            score = brief.symbol_sentiment.get(sym, 0.0)
            if score >= threshold:
                sigs.append(Signal(sym, Side.BUY, strength=score, order_value=order_value,
                                   strategy="intel_sentiment", note=f"aggregate sentiment {score:+.2f}"))
            elif score <= -threshold:
                sigs.append(Signal(sym, Side.SELL, strength=-score, order_value=order_value,
                                   strategy="intel_sentiment", note=f"aggregate sentiment {score:+.2f}"))
        return sigs
