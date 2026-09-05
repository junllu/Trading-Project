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
    max_position_value: float = 5000
    max_order_value: float = 2000
    max_orders_per_day: int = 20
    max_daily_loss: float = 1000
    allowed_symbols_only: bool = True


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
