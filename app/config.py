"""Configuration loading: environment (.env) + YAML config file.

Environment holds secrets and the trading mode; YAML holds the declarative
setup (brokers, watchlist, risk limits, strategies). Both are optional in
paper mode so a fresh clone runs with sensible defaults.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # dotenv optional
    pass

try:
    import yaml
except Exception:  # pragma: no cover - yaml optional for tests
    yaml = None  # type: ignore

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "config.yaml"
EXAMPLE_CONFIG_PATH = ROOT / "config" / "config.example.yaml"


class TradingMode(str, Enum):
    PAPER = "paper"
    CONFIRM = "confirm"
    LIVE = "live"


@dataclass
class RiskLimits:
    """Caps in BOTH absolute dollars and percent of equity; the tighter binds.

    Absolute-only caps were the original bug: $5,000 describes no real book.
    Against the $184 agentic account it never binds (cash binds first), and
    against the $136k consolidated book it is breached by six positions. A cap
    that is simultaneously unreachable and violated is measuring nothing.

    Percent-of-equity scales with whatever the algorithm is tested against —
    the $184 account, a $27k options sleeve, or the full book — so a simulation
    result means the same thing at every size. The absolute values stay as a
    hard ceiling for the case where equity is large but conviction is not.
    """
    max_position_value: float = 5000
    max_order_value: float = 2000
    max_orders_per_day: int = 20
    max_daily_loss: float = 1000
    allowed_symbols_only: bool = True

    # Fractions of account equity. 0 disables the percentage test.
    max_position_pct: float = 0.10     # no single name above 10% of the book
    # Equal to the position cap ON PURPOSE. A smaller order cap assumes you are
    # scaling into a position over days, which is right for a large book and
    # wrong for a small test sleeve: at 4% of $184 the order cap was $7.36
    # against an $18.40 pocket, so opening ONE pocket took three orders — 15
    # orders to open five, against a 20/day limit, each paying the spread
    # separately. The absolute $2,000 ceiling still binds on the real book, so
    # this changes nothing above ~$20k.
    max_order_pct: float = 0.10
    max_daily_loss_pct: float = 0.02   # halt the day at a 2% realised loss

    # A TEST SLEEVE is not a small book — it is a different activity, and it
    # wants a flat dollar cap rather than a percentage. The operator sets the
    # order size they are willing to lose per trade and the agent sizes freely
    # underneath it; a percentage would drift that number every time the
    # balance moved. Applies ONLY below `test_sleeve_below`, so the real book
    # keeps its own ceilings untouched.
    test_sleeve_below: float = 1000.0    # equity under this is a test sleeve
    test_sleeve_max_order: float = 15.0  # hard $ cap per order in that sleeve

    def is_test_sleeve(self, equity: float) -> bool:
        return 0 < equity < self.test_sleeve_below

    def position_cap(self, equity: float) -> float:
        pct = equity * self.max_position_pct if self.max_position_pct > 0 else float("inf")
        return min(self.max_position_value, pct)

    def order_cap(self, equity: float) -> float:
        pct = equity * self.max_order_pct if self.max_order_pct > 0 else float("inf")
        cap = min(self.max_order_value, pct)
        if self.is_test_sleeve(equity) and self.test_sleeve_max_order > 0:
            cap = min(cap, self.test_sleeve_max_order)
        return cap

    def daily_loss_cap(self, equity: float) -> float:
        pct = equity * self.max_daily_loss_pct if self.max_daily_loss_pct > 0 else float("inf")
        return min(self.max_daily_loss, pct)


DEFAULT_CONFIG: dict[str, Any] = {
    "brokers": {"paper": {"enabled": True, "starting_cash": 100000}},
    "watchlist": ["AAPL", "MSFT", "NVDA", "SPY"],
    "risk": {},
    "strategies": [
        {
            "name": "sma_crossover",
            "symbols": ["AAPL", "MSFT", "NVDA"],
            "params": {"fast": 10, "slow": 30, "order_value": 1000},
        }
    ],
    "loop_interval_seconds": 60,
}


@dataclass
class Settings:
    mode: TradingMode = TradingMode.PAPER
    host: str = "127.0.0.1"
    port: int = 8000
    secret_key: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    # convenience accessors -------------------------------------------------
    @property
    def watchlist(self) -> list[str]:
        return list(self.raw.get("watchlist", []))

    @property
    def brokers(self) -> dict[str, Any]:
        return dict(self.raw.get("brokers", {}))

    @property
    def strategies(self) -> list[dict[str, Any]]:
        return list(self.raw.get("strategies", []))

    @property
    def loop_interval(self) -> int:
        return int(self.raw.get("loop_interval_seconds", 60))

    @property
    def risk(self) -> RiskLimits:
        r = self.raw.get("risk", {}) or {}
        base = RiskLimits()
        return RiskLimits(
            max_position_value=r.get("max_position_value", base.max_position_value),
            max_order_value=r.get("max_order_value", base.max_order_value),
            max_orders_per_day=r.get("max_orders_per_day", base.max_orders_per_day),
            max_daily_loss=r.get("max_daily_loss", base.max_daily_loss),
            allowed_symbols_only=r.get("allowed_symbols_only", base.allowed_symbols_only),
            max_position_pct=r.get("max_position_pct", base.max_position_pct),
            max_order_pct=r.get("max_order_pct", base.max_order_pct),
            max_daily_loss_pct=r.get("max_daily_loss_pct", base.max_daily_loss_pct),
            test_sleeve_below=r.get("test_sleeve_below", base.test_sleeve_below),
            test_sleeve_max_order=r.get("test_sleeve_max_order", base.test_sleeve_max_order),
        )

    def env(self, key: str, default: str = "") -> str:
        return os.getenv(key, default)


def _load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None or not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_settings() -> Settings:
    raw = _load_yaml(CONFIG_PATH) or _load_yaml(EXAMPLE_CONFIG_PATH) or dict(DEFAULT_CONFIG)

    mode_str = os.getenv("TRADING_MODE", "paper").lower()
    try:
        mode = TradingMode(mode_str)
    except ValueError:
        mode = TradingMode.PAPER

    return Settings(
        mode=mode,
        host=os.getenv("PORTAL_HOST", "127.0.0.1"),
        port=int(os.getenv("PORTAL_PORT", "8000")),
        secret_key=os.getenv("PORTAL_SECRET_KEY", ""),
        raw=raw,
    )


# module-level singleton
settings = load_settings()
