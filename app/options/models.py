"""Option domain types."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class OptionType(str, Enum):
    CALL = "call"
    PUT = "put"


@dataclass
class OptionContract:
    symbol: str
    option_type: OptionType
    strike: float
    expiry_days: int              # calendar days to expiration

    def label(self) -> str:
        return f"{self.symbol} {self.strike:g}{'C' if self.option_type is OptionType.CALL else 'P'} {self.expiry_days}d"


@dataclass
class OptionQuote:
    contract: OptionContract
    premium: float                # per share; x100 for one contract
    delta: float = 0.0
    prob_itm: float = 0.0
    theta: float = 0.0
    iv: float = 0.0


@dataclass
class OptionPlan:
    """A concrete, sized income-strategy suggestion for review."""
    strategy: str                 # "covered_call" | "cash_secured_put" | "sell_the_news"
    symbol: str
    action: str                   # human-readable instruction
    contracts: int
    quote: OptionQuote | None = None
    est_premium: float = 0.0      # total $ credit for the position
    annualized_return: float = 0.0
    rationale: str = ""
    warnings: list[str] = field(default_factory=list)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        q = self.quote
        return {
            "strategy": self.strategy,
            "symbol": self.symbol,
            "action": self.action,
            "contracts": self.contracts,
            "strike": q.contract.strike if q else None,
            "expiry_days": q.contract.expiry_days if q else None,
            "premium_per_share": round(q.premium, 2) if q else None,
            "delta": round(q.delta, 3) if q else None,
            "prob_itm": round(q.prob_itm, 3) if q else None,
            "est_premium": round(self.est_premium, 2),
            "annualized_return": round(self.annualized_return, 4),
            "rationale": self.rationale,
            "warnings": self.warnings,
        }
