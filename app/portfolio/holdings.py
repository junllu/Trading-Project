"""Load a real holdings snapshot and seed it into the paper broker.

This lets the portal reflect your actual Robinhood/Webull book — so the
portfolio view, intel signals, geopolitical exposure, and options plans all run
against real positions — until live broker sync replaces the snapshot.
"""
from __future__ import annotations

from pathlib import Path

from ..config import ROOT
from ..brokers.paper import PaperBroker
from ..models import Position

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None  # type: ignore

HOLDINGS_PATH = ROOT / "config" / "holdings.yaml"

# Positions that exist on the book but cannot be acted on. These are NOT gaps to
# be closed: no price feed will ever cover them, no thesis can be graded, and no
# exit is available. Reporting them as missing data every run trains the eye to
# skip warnings, which is how a real gap gets missed — so they are excluded by
# name, with the reason recorded here rather than in someone's memory.
UNTRADEABLE: dict[str, str] = {
    "NEWYY": "delisted; broker reports an inactive instrument and the position "
             "cannot be sold. Excluded from price coverage, onboarding and "
             "screening. Carried at cost basis in holdings.yaml.",
}


def is_untradeable(symbol: str) -> bool:
    return symbol.upper() in UNTRADEABLE


def load_holdings(path: Path | None = None) -> list[dict]:
    p = path or HOLDINGS_PATH
    if yaml is None or not p.exists():
        return []
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return list(data.get("holdings", []))


def load_cash(path: Path | None = None) -> float | None:
    """Real uninvested cash from holdings.yaml (top-level `cash:`), or None.

    None means "unknown" — the portal then assumes 0 real cash rather than
    inventing paper buying power, so Equity reflects the real account.
    """
    p = path or HOLDINGS_PATH
    if yaml is None or not p.exists():
        return None
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    cash = data.get("cash")
    return float(cash) if cash is not None else None


def seed_paper_broker(broker: PaperBroker, holdings: list[dict]) -> list[str]:
    """Load holdings into a paper broker as consolidated positions + prices.

    Same symbol across brokers is merged with a share-weighted average cost.
    Returns the list of symbols loaded.
    """
    merged: dict[str, Position] = {}
    for h in holdings:
        sym = str(h["symbol"]).upper()
        qty = float(h["shares"])
        avg = float(h.get("avg_price", h.get("last", 0.0)))
        last = float(h.get("last", avg))
        broker.set_price(sym, last)
        if sym in merged:
            pos = merged[sym]
            total = pos.quantity + qty
            pos.avg_price = (pos.avg_price * pos.quantity + avg * qty) / total if total else avg
            pos.quantity = total
        else:
            merged[sym] = Position(symbol=sym, quantity=qty, avg_price=avg, broker="portfolio")

    # Inject directly into the broker's ledger (bypasses cash accounting — these
    # are pre-existing shares, not new purchases).
    for sym, pos in merged.items():
        broker._positions[sym] = pos  # noqa: SLF001 - intentional seed
    return sorted(merged.keys())
