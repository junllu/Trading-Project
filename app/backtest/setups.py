"""Backtest setups — the A/B configurations to compare.

A "setup" is a full strategy definition: which signals to use and how to weight
them, how often to rebalance, whether the drawdown halt is on, and how large to
size. The engine runs any number of these over the SAME price data so results
are directly comparable.

Backtest signals are deliberately the cheap, deterministic ones (technical +
forecast) — no LLM calls per bar, so a multi-year sweep costs zero tokens.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Setup:
    name: str
    strategy: str = "conviction"              # "conviction" | "buy_hold"
    weights: dict[str, float] = field(default_factory=lambda: {"technical": 0.6, "forecast": 0.4})
    forecast_model: str = "naive"             # naive | chronos | kronos | ...
    rebalance_days: int = 5                    # weekly rebalance (turnover + realism)
    drawdown_halt: float = 0.20                # 0 disables the halt
    entry_threshold: float = 0.2
    base_frac: float = 0.15                    # fraction of equity per strong signal
    max_position_frac: float = 0.40            # cap on any single name
    target_vol: float = 0.35                   # for the volatility scalar
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name, "strategy": self.strategy, "weights": self.weights,
            "forecast_model": self.forecast_model, "rebalance_days": self.rebalance_days,
            "drawdown_halt": self.drawdown_halt, "base_frac": self.base_frac,
            "max_position_frac": self.max_position_frac, "note": self.note,
        }


# The four setups I recommend comparing first.
SETUPS: dict[str, Setup] = {
    "A": Setup(
        name="A: Trend+Forecast, halt ON",
        weights={"technical": 0.6, "forecast": 0.4}, drawdown_halt=0.20,
        note="My pick: momentum/trend + forward-return forecast, vol-scaled, with the 20% drawdown halt.",
    ),
    "B": Setup(
        name="B: Trend+Forecast, halt OFF",
        weights={"technical": 0.6, "forecast": 0.4}, drawdown_halt=0.0,
        note="Same signals as A but no drawdown halt — isolates whether the halt helps or hurts.",
    ),
    "C": Setup(
        name="C: Buy & Hold (equal weight)",
        strategy="buy_hold",
        note="Benchmark. Deploy equally on day one and hold. If we can't beat this, don't trade.",
    ),
    "D": Setup(
        name="D: Trend only (no forecast)",
        weights={"technical": 1.0}, drawdown_halt=0.20,
        note="Isolates the forecast source's contribution vs A.",
    ),
    "E": Setup(
        name="E: Trend+Forecast+Macro, halt ON",
        weights={"technical": 0.5, "forecast": 0.3, "macro": 0.2}, drawdown_halt=0.20,
        note="Adds the policy-cycle macro tilt on top of A — tests whether regime awareness helps.",
    ),
}


def build_setup(key: str) -> Setup:
    if key in SETUPS:
        return SETUPS[key]
    raise KeyError(f"unknown setup '{key}'. Known: {list(SETUPS)}")
