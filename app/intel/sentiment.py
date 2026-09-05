"""Lightweight, transparent sentiment scoring — pure Python, no ML deps.

A lexicon + negation + intensifier model. It's deliberately simple and
auditable: you can see exactly which words drove a score. Good enough to rank
headlines and X posts; swap in Grok's own analysis when you want nuance.
"""
from __future__ import annotations

import re

# Finance-tilted polarity lexicon (-1..1).
_LEXICON: dict[str, float] = {
    # bullish
    "beat": 0.6, "beats": 0.6, "surge": 0.8, "surged": 0.8, "soar": 0.9, "rally": 0.7,
    "record": 0.5, "growth": 0.5, "profit": 0.5, "upgrade": 0.7, "upgraded": 0.7,
    "outperform": 0.7, "bullish": 0.8, "strong": 0.5, "gains": 0.5, "jump": 0.6,
    "approval": 0.6, "approved": 0.6, "wins": 0.6, "breakthrough": 0.7, "expands": 0.4,
    "raises": 0.4, "boost": 0.6, "optimistic": 0.6, "demand": 0.4, "buyback": 0.6,
    # bearish
    "miss": -0.6, "misses": -0.6, "plunge": -0.8, "plunged": -0.8, "crash": -0.9,
    "slump": -0.7, "downgrade": -0.7, "downgraded": -0.7, "bearish": -0.8, "weak": -0.5,
    "loss": -0.5, "losses": -0.5, "fraud": -0.9, "probe": -0.5, "lawsuit": -0.5,
    "recall": -0.6, "bankruptcy": -0.9, "layoffs": -0.5, "warning": -0.5, "cuts": -0.5,
    "sanction": -0.6, "sanctions": -0.6, "war": -0.7, "conflict": -0.6, "invasion": -0.8,
    "tariff": -0.5, "tariffs": -0.5, "ban": -0.6, "banned": -0.6, "default": -0.7,
    "recession": -0.7, "inflation": -0.4, "selloff": -0.7, "halts": -0.4, "slowdown": -0.5,
}

_NEGATIONS = {"not", "no", "never", "without", "avoids", "avoided", "denies", "denied"}
_INTENSIFIERS = {"very": 1.3, "sharply": 1.4, "massively": 1.5, "slightly": 0.6, "modestly": 0.7}

_TOKEN_RE = re.compile(r"[a-z']+")


def score_text(text: str) -> float:
    """Return a sentiment score in [-1, 1]."""
    tokens = _TOKEN_RE.findall(text.lower())
    if not tokens:
        return 0.0
    total = 0.0
    hits = 0
    for i, tok in enumerate(tokens):
        if tok not in _LEXICON:
            continue
        val = _LEXICON[tok]
        # look back up to 2 tokens for negation / intensifier
        window = tokens[max(0, i - 2):i]
        if any(w in _NEGATIONS for w in window):
            val = -val
        for w in window:
            if w in _INTENSIFIERS:
                val *= _INTENSIFIERS[w]
        total += val
        hits += 1
    if hits == 0:
        return 0.0
    # average of hits, squashed to keep it in range
    avg = total / hits
    return max(-1.0, min(1.0, avg))


def explain(text: str) -> list[tuple[str, float]]:
    """Return the (word, polarity) pairs that contributed — for auditability."""
    tokens = _TOKEN_RE.findall(text.lower())
    return [(t, _LEXICON[t]) for t in tokens if t in _LEXICON]
