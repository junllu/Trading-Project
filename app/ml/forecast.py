"""Forecasting layer — turn a price history into a *predicted* forward return.

This is where a Hugging Face time-series foundation model plugs in. The rest of
the system only sees a `Forecast` (expected return + confidence), which the
conviction engine consumes as one more weighted, transparent source. Swapping
the model never changes the architecture.

Two implementations:

- `NaiveDriftForecaster` — pure Python, no dependencies. Estimates the forward
  return from recent drift and scales confidence by the drift's statistical
  significance vs. noise (a t-stat). Always available; the default; tested.

- `HFForecaster` — wraps a Hugging Face foundation model (Chronos-2, TimesFM,
  Moirai, or the finance-specific **Kronos**, pretrained on OHLCV candles).
  Lazy-loaded; needs `torch` + the model package + a one-time weight download,
  so it runs on your own machine (not restricted/CI environments). Falls back to
  the naive forecaster whenever the model isn't available — nothing breaks.

Honest note: foundation TS models are competitive but not magic on noisy equity
returns; validate any of them on real history before trusting the signal.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger("portal.forecast")


@dataclass
class Forecast:
    symbol: str
    expected_return: float        # over `horizon` bars (e.g. +0.02 = +2%)
    confidence: float             # 0..1
    horizon: int
    source: str

    def score(self, scale: float = 0.05) -> float:
        """Map the forecast to a conviction score in [-1, 1].

        `scale` is the forward move that counts as full conviction (5% default);
        the raw score is then attenuated by confidence, so an uncertain forecast
        contributes little.
        """
        raw = max(-1.0, min(1.0, self.expected_return / scale)) if scale else 0.0
        return max(-1.0, min(1.0, raw * self.confidence))

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "expected_return": round(self.expected_return, 4),
            "confidence": round(self.confidence, 3),
            "horizon": self.horizon,
            "score": round(self.score(), 3),
            "source": self.source,
        }


class ForecastModel(Protocol):
    name: str
    def predict(self, symbol: str, history: list[float], horizon: int = 5) -> Forecast: ...


class NaiveDriftForecaster:
    """Forward return from recent drift; confidence from its significance."""
    name = "naive"

    def __init__(self, lookback: int = 30):
        self.lookback = lookback

    def predict(self, symbol: str, history: list[float], horizon: int = 5) -> Forecast:
        h = history[-(self.lookback + 1):]
        rets = [math.log(h[i] / h[i - 1]) for i in range(1, len(h)) if h[i - 1] > 0]
        if len(rets) < 3:
            return Forecast(symbol, 0.0, 0.0, horizon, self.name)
        mean = sum(rets) / len(rets)
        var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
        std = math.sqrt(var) if var > 0 else 1e-6
        expected = math.exp(mean * horizon) - 1.0                    # horizon return
        tstat = abs(mean) / (std / math.sqrt(len(rets)) + 1e-9)      # drift vs noise
        confidence = max(0.05, min(0.9, tstat / 3.0))
        return Forecast(symbol, expected, confidence, horizon, self.name)


class HFForecaster:
    """Hugging Face time-series foundation model adapter (local-only).

    Default targets Amazon Chronos. For the finance-specific Kronos or for
    TimesFM/Moirai, pass the model id and adjust per that model's API. Any
    failure (missing torch/package, no weights, restricted network) degrades to
    the naive forecaster.
    """
    def __init__(self, model_id: str = "amazon/chronos-t5-small"):
        self.model_id = model_id
        self.name = f"hf:{model_id}"
        self._pipe = None
        self._fallback = NaiveDriftForecaster()

    def _pipeline(self):
        if self._pipe is None:
            import torch  # noqa: F401  (ensures torch present)
            from chronos import BaseChronosPipeline
            self._pipe = BaseChronosPipeline.from_pretrained(self.model_id, device_map="auto")
        return self._pipe

    def predict(self, symbol: str, history: list[float], horizon: int = 5) -> Forecast:
        try:
            import torch
            pipe = self._pipeline()
            ctx = torch.tensor(history[-256:], dtype=torch.float32)
            quantiles, _ = pipe.predict_quantiles(
                context=ctx, prediction_length=horizon, quantile_levels=[0.1, 0.5, 0.9]
            )
            q = quantiles[0]                          # shape [horizon, 3]
            last = history[-1]
            median = float(q[-1, 1])
            lo, hi = float(q[-1, 0]), float(q[-1, 2])
            expected = (median / last - 1.0) if last else 0.0
            # tighter forecast interval -> higher confidence
            spread = (hi - lo) / median if median else 1.0
            confidence = max(0.05, min(0.95, 1.0 / (1.0 + 8.0 * abs(spread))))
            return Forecast(symbol, expected, confidence, horizon, self.name)
        except Exception as exc:  # pragma: no cover - depends on optional heavy deps
            log.warning("HF forecaster '%s' unavailable (%s); using naive fallback", self.model_id, exc)
            f = self._fallback.predict(symbol, history, horizon)
            return Forecast(f.symbol, f.expected_return, f.confidence, f.horizon,
                            f"{self.name}->naive")


def build_forecaster(name: str = "naive") -> ForecastModel:
    """Factory. 'naive' (default) or an HF model id / alias.

    Aliases: 'chronos' -> amazon/chronos-t5-small, 'chronos-2' -> amazon/chronos-2,
    'kronos' -> a finance OHLCV model, 'timesfm' -> google/timesfm-2.5.
    Anything else is treated as a literal HF model id.
    """
    key = (name or "naive").lower()
    if key in ("", "naive", "none"):
        return NaiveDriftForecaster()
    aliases = {
        "chronos": "amazon/chronos-t5-small",
        "chronos-2": "amazon/chronos-2",
        "timesfm": "google/timesfm-2.5-200m-pytorch",
        "kronos": "NeoQuasar/Kronos-small",
    }
    return HFForecaster(aliases.get(key, name))
