"""Intelligence layer: turn real-world events into trade signals.

Pipeline:  event sources (Grok live-search, X.com)  ->  Event objects
        -> sentiment + geopolitical analysis  ->  Signal / OptionPlan hints.

Everything degrades gracefully to an offline "mock" mode when no API keys are
present, so the whole chain is testable and demoable without credentials.
"""
from .events import Event, EventCategory, Sentiment
from .sentiment import score_text
from .geopolitics import GeopoliticalEngine, GeoAssessment
from .grok import GrokClient
from .xfeed import XFeed
from .service import IntelService

__all__ = [
    "Event", "EventCategory", "Sentiment", "score_text",
    "GeopoliticalEngine", "GeoAssessment", "GrokClient", "XFeed", "IntelService",
]
