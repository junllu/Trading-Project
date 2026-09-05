"""Domain types for the intelligence layer."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class EventCategory(str, Enum):
    GEOPOLITICAL = "geopolitical"   # conflict, sanctions, elections, treaties
    MACRO = "macro"                 # rates, inflation, jobs, GDP
    EARNINGS = "earnings"           # company results / guidance
    REGULATORY = "regulatory"       # antitrust, approvals, bans
    COMPANY = "company"             # product, M&A, leadership
    ENERGY = "energy"               # oil/gas supply shocks
    OTHER = "other"


class Sentiment(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"

    @staticmethod
    def from_score(score: float, threshold: float = 0.15) -> "Sentiment":
        if score >= threshold:
            return Sentiment.BULLISH
        if score <= -threshold:
            return Sentiment.BEARISH
        return Sentiment.NEUTRAL


@dataclass
class Event:
    """A single real-world event as ingested by a source."""
    headline: str
    source: str                          # "grok", "x.com", "mock"
    category: EventCategory = EventCategory.OTHER
    symbols: list[str] = field(default_factory=list)   # tickers implicated
    entities: list[str] = field(default_factory=list)  # countries, people, orgs
    sentiment_score: float = 0.0         # -1..1
    importance: float = 0.5              # 0..1, how market-moving
    url: str = ""
    body: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    ts: float = field(default_factory=time.time)

    @property
    def sentiment(self) -> Sentiment:
        return Sentiment.from_score(self.sentiment_score)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "headline": self.headline,
            "source": self.source,
            "category": self.category.value,
            "symbols": self.symbols,
            "entities": self.entities,
            "sentiment": self.sentiment.value,
            "sentiment_score": round(self.sentiment_score, 3),
            "importance": round(self.importance, 3),
            "url": self.url,
        }
