"""Symbol -> GICS sector -> SPDR sector ETF, for sector-rotation research.

S&P 500 members take their GICS sector from data/universe/sp500_*.csv (the
2026-09-27 snapshot). The 30 studied names outside the index are assigned by
hand below to their standard GICS sector; names that could not be identified
with confidence, and funds, map to None and are treated as "no sector" (a
sector filter excludes them rather than guessing).

Sector ETF history: XLRE starts 2015-10, XLC 2018-06; before those dates the
sector has no ETF and is unranked.
"""
from __future__ import annotations

import csv
from functools import lru_cache

from ..config import ROOT

ETF = {
    "Information Technology": "XLK", "Energy": "XLE", "Financials": "XLF",
    "Health Care": "XLV", "Industrials": "XLI", "Utilities": "XLU",
    "Consumer Staples": "XLP", "Consumer Discretionary": "XLY", "Materials": "XLB",
    "Real Estate": "XLRE", "Communication Services": "XLC",
}
MANUAL = {
    "ABEV": "Consumer Staples", "AEIS": "Information Technology", "ALAB": "Information Technology",
    "ARM": "Information Technology", "BULL": "Financials", "CRML": "Materials", "ET": "Energy",
    "FIG": "Information Technology", "GSM": "Materials", "INFQ": "Information Technology",
    "LCID": "Consumer Discretionary", "MP": "Materials", "MSTR": "Information Technology",
    "NET": "Information Technology", "NIO": "Consumer Discretionary", "NOK": "Information Technology",
    "RBLX": "Communication Services", "RIVN": "Consumer Discretionary", "SHEL": "Energy",
    "SHOP": "Information Technology", "SNOW": "Information Technology", "SOC": "Energy",
    "SOFI": "Financials", "TCEHY": "Communication Services", "U": "Information Technology",
    "USAR": "Materials", "WEN": "Consumer Discretionary",
    "CCXI": None, "PEW": None, "JEPQ": None,          # unidentified / a fund
}


@lru_cache(maxsize=1)
def _sp500() -> dict[str, str]:
    p = sorted((ROOT / "data" / "universe").glob("sp500_*.csv"))[-1]
    return {r["symbol"]: r["sector"] for r in csv.DictReader(p.open(encoding="utf-8"))}


def sector(symbol: str) -> str | None:
    s = symbol.upper()
    if s in MANUAL:
        return MANUAL[s]
    return _sp500().get(s)


def sector_etf(symbol: str) -> str | None:
    sec = sector(symbol)
    return ETF.get(sec) if sec else None
