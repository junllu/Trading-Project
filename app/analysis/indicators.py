"""Technical indicators in pure Python.

No numpy/pandas dependency, so these run anywhere and are trivially testable.
Each function takes a list of closing prices (oldest first) and returns a list
aligned to the input length, using None for positions without enough data.
"""
from __future__ import annotations

from typing import Optional

Series = list[float]
OptSeries = list[Optional[float]]


def sma(values: Series, period: int) -> OptSeries:
    """Simple moving average."""
    if period <= 0:
        raise ValueError("period must be positive")
    out: OptSeries = []
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        out.append(running / period if i >= period - 1 else None)
    return out


def ema(values: Series, period: int) -> OptSeries:
    """Exponential moving average, seeded with the first SMA."""
    if period <= 0:
        raise ValueError("period must be positive")
    out: OptSeries = [None] * len(values)
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(values: Series, period: int = 14) -> OptSeries:
    """Relative Strength Index (Wilder's smoothing)."""
    out: OptSeries = [None] * len(values)
    if len(values) <= period:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        delta = values[i] - values[i - 1]
        gains += max(delta, 0.0)
        losses += max(-delta, 0.0)
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = _rsi_from(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        delta = values[i] - values[i - 1]
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = _rsi_from(avg_gain, avg_loss)
    return out


def _rsi_from(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(
    values: Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[OptSeries, OptSeries, OptSeries]:
    """Return (macd_line, signal_line, histogram)."""
    fast_ema = ema(values, fast)
    slow_ema = ema(values, slow)
    macd_line: OptSeries = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(fast_ema, slow_ema)
    ]
    # signal line = EMA of the defined portion of macd_line
    defined = [(i, v) for i, v in enumerate(macd_line) if v is not None]
    signal_line: OptSeries = [None] * len(values)
    if len(defined) >= signal:
        vals = [v for _, v in defined]
        sig = ema(vals, signal)
        for (idx, _), s in zip(defined, sig):
            signal_line[idx] = s
    hist: OptSeries = [
        (m - s) if (m is not None and s is not None) else None
        for m, s in zip(macd_line, signal_line)
    ]
    return macd_line, signal_line, hist


def pct_change(values: Series) -> OptSeries:
    """Period-over-period percent change."""
    out: OptSeries = [None]
    for i in range(1, len(values)):
        prev = values[i - 1]
        out.append(((values[i] - prev) / prev * 100.0) if prev else None)
    return out
