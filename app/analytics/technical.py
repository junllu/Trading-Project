"""Composite technical scoring.

Turns a price history into a single trend/momentum score in [-1, 1] plus its
components, so both the conviction engine and the dashboard can reason about
"how technically strong is this name right now" in one number — while still
being able to show the parts that drove it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..analysis import ema, macd, rsi, sma


@dataclass
class TechnicalScore:
    symbol: str
    score: float = 0.0                    # -1..1 composite
    components: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    ready: bool = False                   # enough data?

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "score": round(self.score, 3),
            "components": {k: round(v, 3) for k, v in self.components.items()},
            "notes": self.notes,
            "ready": self.ready,
        }


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def technical_score(symbol: str, history: list[float], fast: int = 10,
                    slow: int = 30) -> TechnicalScore:
    ts = TechnicalScore(symbol=symbol)
    n = len(history)
    if n < slow + 2:
        ts.notes.append(f"insufficient history ({n} bars, need {slow + 2})")
        return ts
    ts.ready = True
    price = history[-1]

    # --- trend: fast vs slow SMA, and price vs slow SMA ---
    fast_s = sma(history, fast)[-1]
    slow_s = sma(history, slow)[-1]
    trend = 0.0
    if fast_s and slow_s:
        spread = (fast_s - slow_s) / slow_s            # relative gap
        trend = _clamp(spread * 20)                    # ~5% gap -> full score
        px_gap = (price - slow_s) / slow_s
        trend = _clamp(0.6 * trend + 0.4 * _clamp(px_gap * 15))
    ts.components["trend"] = trend

    # --- momentum: RSI centered at 50 ---
    r = rsi(history, 14)[-1]
    momentum = 0.0
    if r is not None:
        momentum = _clamp((r - 50) / 30)               # RSI 80 -> +1, 20 -> -1
        if r >= 70:
            ts.notes.append(f"RSI {r:.0f} (overbought)")
        elif r <= 30:
            ts.notes.append(f"RSI {r:.0f} (oversold)")
    ts.components["momentum"] = momentum

    # --- MACD histogram sign/strength ---
    _, _, hist = macd(history)
    macd_comp = 0.0
    h = hist[-1]
    if h is not None and price:
        macd_comp = _clamp((h / price) * 60)
    ts.components["macd"] = macd_comp

    # --- slope of short EMA (acceleration) ---
    e = ema(history, fast)
    slope = 0.0
    if e[-1] is not None and e[-5] is not None and e[-5] != 0:
        slope = _clamp(((e[-1] - e[-5]) / e[-5]) * 25)
    ts.components["slope"] = slope

    # weighted composite
    ts.score = _clamp(0.4 * trend + 0.25 * momentum + 0.2 * macd_comp + 0.15 * slope)
    return ts
