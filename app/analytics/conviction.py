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
    "technical": 0.25,
    "forecast": 0.20,          # ML forward-return forecast (naive or Hugging Face model)
    "analyst": 0.25,
    "sentiment": 0.15,
    "macro": 0.12,             # policy-cycle / election-regime sector tilt (macro timeline)
    "geopolitical": 0.10,
    "serenity": 0.05,
    "professor_jiang": 0.05,
    "personal": 0.05,
}


# The score is unitless and therefore unfalsifiable: no outcome can prove a 0.62
# wrong, which also means no outcome can improve it. Restating it as a
# probability over a named horizon fixes both — such a claim is wrong a
# predictable fraction of the time BY CONSTRUCTION, and every one of those is a
# measurement app/analytics/calibration.py can score.
PROBABILITY_HORIZON_SESSIONS = 21
BENCHMARK = "SPY"

# Slope of the untuned score -> probability map. This is a PRIOR, not a fit:
# it says a score of +1.0 means roughly 82% and +0.2 means roughly 57%, which
# is a guess about the engine's own confidence and nothing more. It is replaced
# the moment there are enough graded outcomes to fit a Platt scaler.
_DEFAULT_SLOPE = 1.5


def _sigmoid(z: float) -> float:
    import math
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


@dataclass
class Conviction:
    symbol: str
    score: float = 0.0                     # -1..1 blended
    action: str = "hold"                   # strong_buy | buy | hold | sell | strong_sell
    contributions: dict[str, float] = field(default_factory=dict)  # source -> weighted contribution
    inputs: dict[str, float] = field(default_factory=dict)         # source -> raw score
    coverage: float = 0.0                  # fraction of weight actually present
    probability: float = 0.5               # P(beats BENCHMARK over the horizon)
    calibrated: bool = False               # True only once fitted on real outcomes

    def claim(self) -> str:
        """The falsifiable sentence. Says outright when it has not been earned."""
        pct = self.probability * 100
        tag = "" if self.calibrated else "  [UNCALIBRATED — a prior, not a measured rate]"
        return (f"{self.symbol}: {pct:.0f}% chance of beating {BENCHMARK} over "
                f"{PROBABILITY_HORIZON_SESSIONS} sessions{tag}")

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "score": round(self.score, 3),
            "action": self.action,
            "coverage": round(self.coverage, 2),
            "probability": round(self.probability, 4),
            "calibrated": self.calibrated,
            "horizon_sessions": PROBABILITY_HORIZON_SESSIONS,
            "benchmark": BENCHMARK,
            "claim": self.claim(),
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


def load_calibrator():
    """A fitted score->probability map, if one has been earned. Else None.

    Absent by design rather than by omission: a calibrator can only be fitted on
    resolved outcomes, and fitting one on the same data it will score is how a
    miscalibrated model is made to look calibrated.
    """
    import json
    from ..config import ROOT
    p = ROOT / "data" / "calibration.json"
    if not p.exists():
        return None
    try:
        from .calibration import PlattScaler
        d = json.loads(p.read_text(encoding="utf-8"))
        return PlattScaler(a=float(d["a"]), b=float(d["b"]), fitted_on=int(d.get("n", 0)))
    except Exception:
        return None


class ConvictionEngine:
    def __init__(self, weights: dict[str, float] | None = None, calibrator=None):
        self.weights = {**DEFAULT_WEIGHTS, **(weights or {})}
        self.calibrator = calibrator if calibrator is not None else load_calibrator()

    def _probability(self, score: float) -> tuple[float, bool]:
        if self.calibrator is not None and getattr(self.calibrator, "fitted_on", 0) >= 30:
            return self.calibrator.predict(score), True
        return _sigmoid(_DEFAULT_SLOPE * score), False

    def blend(self, symbol: str, inputs: dict[str, float]) -> Conviction:
        """inputs: {source_name: score in [-1,1]}. Missing sources are skipped."""
        conv = Conviction(symbol=symbol)
        present = {k: v for k, v in inputs.items() if v is not None}
        conv.inputs = present
        total_w = sum(self.weights.get(k, 0.0) for k in present)
        if total_w <= 0:
            # No sources present. 0.5 is the honest answer — a coin flip — not
            # a neutral-looking score that later reads as a weak opinion.
            conv.probability, conv.calibrated = 0.5, False
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
        conv.probability, conv.calibrated = self._probability(conv.score)
        return conv

    def rank(self, convictions: list[Conviction]) -> list[Conviction]:
        return sorted(convictions, key=lambda c: -c.score)
