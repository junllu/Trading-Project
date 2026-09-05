"""Direct X.com (Twitter) feed — optional, paid-tier adapter.

Reading recent posts by cashtag/keyword requires the X API v2 with a paid tier
(Basic and up) and a bearer token in X_BEARER_TOKEN. Because that costs money,
Grok's built-in live search is the default event source; this adapter is here
for when you want raw post-level sentiment straight from X.

Without a token it runs in mock mode. Local sentiment scoring is applied to
every post so you get a per-symbol "X buzz" score with no ML dependency.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from .events import Event, EventCategory
from .sentiment import score_text

X_API_BASE = "https://api.twitter.com/2"


@dataclass
class XPost:
    text: str
    author: str = ""
    likes: int = 0
    sentiment: float = 0.0


@dataclass
class XBuzz:
    symbol: str
    post_count: int
    avg_sentiment: float
    sample: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "post_count": self.post_count,
            "avg_sentiment": round(self.avg_sentiment, 3),
            "sample": self.sample[:3],
        }


class XFeed:
    def __init__(self, bearer_token: str | None = None):
        # None => read env; an explicit string (incl. "") is an override.
        self.bearer = os.getenv("X_BEARER_TOKEN", "") if bearer_token is None else bearer_token

    @property
    def live(self) -> bool:
        return bool(self.bearer)

    def buzz(self, symbol: str, max_posts: int = 25) -> XBuzz:
        posts = self._search(symbol, max_posts) if self.live else self._mock(symbol)
        if not posts:
            return XBuzz(symbol=symbol, post_count=0, avg_sentiment=0.0)
        avg = sum(p.sentiment for p in posts) / len(posts)
        return XBuzz(symbol=symbol, post_count=len(posts), avg_sentiment=avg,
                     sample=[p.text for p in sorted(posts, key=lambda x: -x.likes)])

    def buzz_to_event(self, buzz: XBuzz) -> Event | None:
        if buzz.post_count == 0:
            return None
        importance = min(1.0, 0.3 + buzz.post_count / 50.0)
        return Event(
            headline=f"X buzz on ${buzz.symbol}: {buzz.post_count} posts, "
                     f"sentiment {buzz.avg_sentiment:+.2f}",
            source="x.com", category=EventCategory.COMPANY,
            symbols=[buzz.symbol], sentiment_score=buzz.avg_sentiment, importance=importance,
        )

    # --- live wiring -------------------------------------------------------
    def _search(self, symbol: str, max_posts: int) -> list[XPost]:
        import requests
        query = f"(${symbol} OR #{symbol}) lang:en -is:retweet"
        resp = requests.get(
            f"{X_API_BASE}/tweets/search/recent",
            headers={"Authorization": f"Bearer {self.bearer}"},
            params={"query": query, "max_results": min(100, max_posts),
                    "tweet.fields": "public_metrics,lang"},
            timeout=30,
        )
        resp.raise_for_status()
        out: list[XPost] = []
        for t in resp.json().get("data", []):
            text = t.get("text", "")
            metrics = t.get("public_metrics", {})
            out.append(XPost(text=text, likes=metrics.get("like_count", 0), sentiment=score_text(text)))
        return out

    def _mock(self, symbol: str) -> list[XPost]:
        canned = [
            f"${symbol} looking strong here, demand is surging into earnings",
            f"Careful with ${symbol}, this rally looks weak and overextended",
            f"${symbol} breakout confirmed, bullish momentum building",
            f"Rotating out of ${symbol}, taking profit before the news",
        ]
        return [XPost(text=t, likes=len(t), sentiment=score_text(t)) for t in canned]
