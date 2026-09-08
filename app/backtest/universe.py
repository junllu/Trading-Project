"""Ticker universes for cross-sectional backtest sweeps.

These are curated, dated snapshots — not a live index-constituent feed (no tool
here has one). NASDAQ100 is built from long-standing, high-confidence large-cap
members; treat it as "a reasonable large tech/growth universe as of ~2024-2025",
not an authoritative, current official membership list. Expect some drift
(additions/removals, M&A) by the time you read this — refresh from a real data
vendor if exact current membership matters for your use case.

`chunk()` splits a universe into groups honoring the backtest engine's 5-symbol
cap (app/backtest/data.py: MAX_SYMBOLS) — each group becomes one independent
Backtest run.
"""
from __future__ import annotations

from ..backtest.data import MAX_SYMBOLS

# Large, long-standing NASDAQ-listed names — a snapshot, not a live feed (see
# module docstring). Ordered roughly by rough market-cap prominence.
NASDAQ100 = [
    "AAPL", "MSFT", "AMZN", "GOOGL", "GOOG", "META", "NVDA", "TSLA", "AVGO", "COST",
    "ADBE", "PEP", "CSCO", "AMD", "INTC", "QCOM", "TXN", "INTU", "AMAT", "ISRG",
    "BKNG", "HON", "VRTX", "SBUX", "GILD", "MU", "ADI", "LRCX", "PANW", "REGN",
    "KLAC", "SNPS", "CDNS", "MDLZ", "MELI", "ASML", "PDD", "CRWD", "MAR", "ORLY",
    "CTAS", "PYPL", "ABNB", "FTNT", "ADP", "MRVL", "NXPI", "WDAY", "MNST", "PCAR",
    "ROP", "DXCM", "CPRT", "PAYX", "ODFL", "AEP", "KDP", "EXC", "FAST", "EA",
    "VRSK", "CSGP", "GEHC", "ANSS", "XEL", "DDOG", "TTD", "ZS", "ON", "CTSH",
    "BIIB", "ILMN", "IDXX", "DLTR", "WBD", "SIRI", "LULU", "ENPH", "CHTR", "CMCSA",
    "TMUS", "AMGN", "MCHP", "SWKS", "TEAM", "ALGN", "OKTA",
    "MRNA", "LCID", "RIVN", "JD", "BIDU", "NTES", "TCOM", "DOCU", "ZM", "DASH",
]

# S&P 500 mega-caps not already in NASDAQ100 (NYSE-listed blue chips) — same
# snapshot caveat applies.
SP500_EX_NASDAQ = [
    "BRK.B", "JPM", "V", "MA", "UNH", "XOM", "JNJ", "PG", "HD", "CVX",
    "MRK", "ABBV", "KO", "BAC", "PFE", "TMO", "WMT", "DIS", "MCD", "CSX",
    "NKE", "ABT", "CRM", "ORCL", "ACN", "LIN", "DHR", "VZ", "NEE",
    "WFC", "UPS", "PM", "RTX", "SPGI", "LOW", "CAT", "GS",
    "IBM", "AMT", "BLK", "DE", "AXP", "GE", "BA", "NOW", "ELV", "SYK",
]


def chunk(symbols: list[str], size: int = MAX_SYMBOLS) -> list[list[str]]:
    """Split a universe into independent groups honoring the backtest's symbol cap."""
    return [symbols[i:i + size] for i in range(0, len(symbols), size)]
