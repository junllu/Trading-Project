"""Grok (xAI) client — live-event monitoring and geopolitical analysis.

xAI exposes an OpenAI-compatible API at https://api.x.ai/v1 with models such as
`grok-4`, and — critically — a *live search* feature that can pull real-time
posts from X. That makes Grok the cheapest single source for "monitor life
events": one call returns current, sourced developments.

Set XAI_API_KEY to go live. Without it, the client runs in mock mode and
returns a small canned event set so the whole pipeline is demoable offline.
"""
from __future__ import annotations

import json
import os
from typing import Any

from .events import Event, EventCategory

XAI_BASE = os.getenv("XAI_BASE_URL", "https://api.x.ai/v1")
XAI_MODEL = os.getenv("XAI_MODEL", "grok-4")

# The instruction that shapes Grok into a structured market-event extractor.
SYSTEM_PROMPT = """You are a markets intelligence analyst. Using live information,
return the most market-relevant real-world events from roughly the last 24 hours.
Respond ONLY with a JSON array. Each item:
{
  "headline": str,
  "category": one of ["geopolitical","macro","earnings","regulatory","company","energy","other"],
  "symbols": [tickers directly implicated],
  "entities": [countries/people/orgs],
  "sentiment_score": float in [-1,1] (market impact direction),
  "importance": float in [0,1] (how market-moving),
  "url": source url if available
}
No prose, no markdown fences — just the JSON array."""


_MOCK_EVENTS = [
    {
        "headline": "OPEC+ signals surprise production cut amid Middle East tensions",
        "category": "energy", "symbols": ["XLE", "USO"], "entities": ["OPEC", "Saudi Arabia"],
        "sentiment_score": 0.4, "importance": 0.8, "url": "",
    },
    {
        "headline": "Fed official strikes hawkish tone, hints rates stay higher for longer",
        "category": "macro", "symbols": ["QQQ", "TLT"], "entities": ["Federal Reserve"],
        "sentiment_score": -0.3, "importance": 0.7, "url": "",
    },
    {
        "headline": "New export tariffs proposed on semiconductor equipment",
        "category": "geopolitical", "symbols": ["SMH", "NVDA"], "entities": ["US", "China"],
        "sentiment_score": -0.5, "importance": 0.75, "url": "",
    },
    {
        "headline": "NVDA beats revenue estimates, raises data-center guidance",
        "category": "earnings", "symbols": ["NVDA"], "entities": ["Nvidia"],
        "sentiment_score": 0.7, "importance": 0.9, "url": "",
    },
]


class GrokClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("XAI_API_KEY", "")

    @property
    def live(self) -> bool:
        return bool(self.api_key)

    # --- public API --------------------------------------------------------
    def fetch_events(self, focus: list[str] | None = None, max_events: int = 8) -> list[Event]:
        """Return current market-relevant events. Live via xAI, else mock."""
        if not self.live:
            return self._mock_events(focus)
        try:
            raw = self._call_live(focus, max_events)
            return self._parse(raw, source="grok")
        except Exception:
            # Never let an API hiccup break the trading loop; fall back to mock.
            return self._mock_events(focus)

    def ask(self, question: str) -> str:
        """Free-form question to Grok with live search (e.g. a geopolitical brief)."""
        if not self.live:
            return "(Grok offline — set XAI_API_KEY for live analysis.)"
        payload = self._chat_payload(
            [{"role": "user", "content": question}],
            live_search=True,
        )
        return self._post_chat(payload)

    # --- live wiring -------------------------------------------------------
    def _call_live(self, focus: list[str] | None, max_events: int) -> str:
        user = "Focus tickers/themes: " + (", ".join(focus) if focus else "broad market")
        user += f"\nReturn up to {max_events} events."
        payload = self._chat_payload(
            [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "user", "content": user}],
            live_search=True,
        )
        return self._post_chat(payload)

    def _chat_payload(self, messages: list[dict], live_search: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": XAI_MODEL, "messages": messages, "temperature": 0.2}
        if live_search:
            # xAI live-search parameters (auto = let Grok decide when to search X/web).
            payload["search_parameters"] = {"mode": "auto", "sources": [{"type": "x"}, {"type": "web"}]}
        return payload

    def _post_chat(self, payload: dict) -> str:
        import requests  # local import so requests is optional in mock mode
        resp = requests.post(
            f"{XAI_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=payload, timeout=45,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    # --- parsing -----------------------------------------------------------
    def _parse(self, raw: str, source: str) -> list[Event]:
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("["):]
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end == -1:
            return []
        items = json.loads(text[start:end + 1])
        return [self._to_event(it, source) for it in items if isinstance(it, dict)]

    def _to_event(self, it: dict, source: str) -> Event:
        try:
            cat = EventCategory(it.get("category", "other"))
        except ValueError:
            cat = EventCategory.OTHER
        return Event(
            headline=str(it.get("headline", "")).strip(),
            source=source, category=cat,
            symbols=[s.upper() for s in it.get("symbols", []) if s],
            entities=list(it.get("entities", [])),
            sentiment_score=float(it.get("sentiment_score", 0.0) or 0.0),
            importance=float(it.get("importance", 0.5) or 0.5),
            url=str(it.get("url", "")),
        )

    def _mock_events(self, focus: list[str] | None) -> list[Event]:
        events = [self._to_event(it, "mock") for it in _MOCK_EVENTS]
        if focus:
            f = {s.upper() for s in focus}
            # keep events touching the focus set, plus all macro/geopolitical context
            events = [e for e in events if (set(e.symbols) & f) or e.category in
                      (EventCategory.MACRO, EventCategory.GEOPOLITICAL, EventCategory.ENERGY)]
        return events
