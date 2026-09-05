"""Broker registry: builds the enabled brokers from config.

Real-broker adapters are imported lazily so a missing optional dependency never
stops the portal from starting in paper mode.
"""
from __future__ import annotations

from typing import Any

from .base import BrokerBase
from .paper import PaperBroker

_INSTANCES: dict[str, BrokerBase] = {}


def build_brokers(broker_config: dict[str, Any]) -> dict[str, BrokerBase]:
    """Instantiate enabled brokers. Always includes paper as a fallback."""
    instances: dict[str, BrokerBase] = {}

    paper_cfg = broker_config.get("paper", {"enabled": True})
    if paper_cfg.get("enabled", True):
        instances["paper"] = PaperBroker(starting_cash=paper_cfg.get("starting_cash", 100_000))

    if broker_config.get("robinhood", {}).get("enabled"):
        from .robinhood import RobinhoodBroker
        instances["robinhood"] = RobinhoodBroker(broker_config["robinhood"])

    if broker_config.get("webull", {}).get("enabled"):
        from .webull import WebullBroker
        instances["webull"] = WebullBroker(broker_config["webull"])

    if not instances:  # never leave the portal with zero brokers
        instances["paper"] = PaperBroker()

    _INSTANCES.clear()
    _INSTANCES.update(instances)
    return instances


def get_broker(name: str) -> BrokerBase:
    if name not in _INSTANCES:
        raise KeyError(f"broker '{name}' not configured")
    return _INSTANCES[name]
