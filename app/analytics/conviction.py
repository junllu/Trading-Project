"""Conviction engine: blend many signals into one score, transparently.

Every input is a number in [-1, 1] (bearish..bullish). The engine combines them
with configurable weights and — critically — always returns the per-source
breakdown, so you can see exactly why a name scored where it did.

Default sources:
  technical     — the composite technical score (app/analytics/technical.py)
  geopolitical  — ticker bias from our geopolitical engine
  sentiment     — event/news sentiment from the intel layer
  analyst       — Claude's per-symbol rating (or heuristic fallback)

Your own sources plug in the same way:
  serenity, professor_jiang, personal — supply a score and a weight, and they
  become first-class inputs with the same visible breakdown.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Default source weights. Tune to your trust in each. They need not sum to 1;
# the engine normalizes over whichever sources are present for a symbol.
DEFAULT_WEIGHTS: dict[str, float] = {
    "technical": 0.30,
    "analyst": 0.30,
    "sentiment": 0.15,
    "geopolitical": 0.10,
    "serenity": 0.05,
    "professor_jiang": 0.05,
    "personal": 0.05,
}


@dataclass
class Conviction:
    symbol: str
    score: float = 0.0                     # -1..1 blended
    action: str = "hold"                   # strong_buy | buy | hold | sell | strong_sell
    contributions: dict[str, float] = field(default_factory=dict)  # source -> weighted contribution
    inputs: dict[str, float] = field(default_factory=dict)         # source -> raw score
    coverage: float = 0.0                  # fraction of weight actually present

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "score": round(self.score, 3),
            "action": self.action,
            "coverage": round(self.coverage, 2),
            "inputs": {k: round(v, 3) for k, v in self.inputs.items()},
            "contributions": {k: round(v, 3) for k, v in
                              sorted(self.contributions.items(), key=lambda x: -abs(x[1]))},
        }


def _action(score: float) -> str:
    if score >= 0.5:
        return "strong_buy"
    if score >= 0.2:
        return "buy"
    if score <= -0.5:
        return "strong_sell"
    if score <= -0.2:
        return "sell"
    return "hold"


class ConvictionEngine:
    def __init__(self, weights: dict[str, float] | None = None):
        self.weights = {**DEFAULT_WEIGHTS, **(weights or {})}

    def blend(self, symbol: str, inputs: dict[str, float]) -> Conviction:
        """inputs: {source_name: score in [-1,1]}. Missing sources are skipped."""
        conv = Conviction(symbol=symbol)
        present = {k: v for k, v in inputs.items() if v is not None}
        conv.inputs = present
        total_w = sum(self.weights.get(k, 0.0) for k in present)
        if total_w <= 0:
            return conv
        blended = 0.0
        for src, val in present.items():
            w = self.weights.get(src, 0.0)
            contrib = (w / total_w) * max(-1.0, min(1.0, val))
            conv.contributions[src] = contrib
            blended += contrib
        conv.score = max(-1.0, min(1.0, blended))
        conv.action = _action(conv.score)
        conv.coverage = total_w / sum(self.weights.values())
        return conv

    def rank(self, convictions: list[Conviction]) -> list[Conviction]:
        return sorted(convictions, key=lambda c: -c.score)
