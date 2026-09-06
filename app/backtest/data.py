"""Historical price data for the backtest — capped at 5 symbols.

Sources, in order of preference for `source="auto"`:
  1. local CSV cache   data/prices/<SYMBOL>.csv   (columns: date,close)
  2. yfinance          (pip install yfinance)     — runs locally
  3. stooq             (CSV over HTTPS)            — runs locally

Fetched data is cached to CSV so later runs are offline and free. A synthetic
generator is included so the engine is testable without any network.

The 5-symbol cap is deliberate: it keeps runs fast, focuses the strategy, and
(when Claude is later in the loop) bounds token usage.
"""
from __future__ import annotations

import csv
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

from ..config import ROOT

PRICES_DIR = ROOT / "data" / "prices"
MAX_SYMBOLS = 5


@dataclass
class PriceData:
    dates: list[str]
    closes: dict[str, list[float]]        # symbol -> closes aligned to `dates`
    source: str = "unknown"

    @property
    def symbols(self) -> list[str]:
        return list(self.closes.keys())

    def __len__(self) -> int:
        return len(self.dates)


def _cap(symbols: list[str]) -> list[str]:
    syms = [s.upper() for s in dict.fromkeys(symbols)]     # de-dup, keep order
    if len(syms) > MAX_SYMBOLS:
        raise ValueError(f"backtest is capped at {MAX_SYMBOLS} symbols (got {len(syms)}: {syms})")
    return syms


# --- CSV cache ------------------------------------------------------------
def _csv_path(symbol: str) -> Path:
    return PRICES_DIR / f"{symbol.upper()}.csv"


def save_csv(symbol: str, dates: list[str], closes: list[float]) -> None:
    PRICES_DIR.mkdir(parents=True, exist_ok=True)
    with _csv_path(symbol).open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "close"])
        for d, c in zip(dates, closes):
            w.writerow([d, c])


def _load_csv(symbol: str) -> tuple[list[str], list[float]] | None:
    p = _csv_path(symbol)
    if not p.exists():
        return None
    dates, closes = [], []
    with p.open("r", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            dates.append(row["date"])
            closes.append(float(row["close"]))
    return (dates, closes) if dates else None


# --- live sources (local only) -------------------------------------------
def _fetch_yfinance(symbol: str, start: str, end: str | None) -> tuple[list[str], list[float]] | None:
    try:
        import yfinance as yf
        df = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        closes = [float(x) for x in df["Close"].values.ravel()]
        dates = [d.strftime("%Y-%m-%d") for d in df.index]
        return dates, closes
    except Exception:
        return None


def _fetch_stooq(symbol: str, start: str, end: str | None) -> tuple[list[str], list[float]] | None:
    try:
        import requests
        url = f"https://stooq.com/q/d/l/?s={symbol.lower()}.us&i=d"
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        lines = r.text.strip().splitlines()
        dates, closes = [], []
        for row in csv.DictReader(lines):
            if "Close" not in row or not row.get("Date"):
                continue
            if row["Date"] < start or (end and row["Date"] > end):
                continue
            try:
                closes.append(float(row["Close"]))
                dates.append(row["Date"])
            except ValueError:
                continue
        return (dates, closes) if dates else None
    except Exception:
        return None


# --- synthetic (offline testing) -----------------------------------------
def synthetic(symbols: list[str], days: int = 500, seed: int = 1,
              drift: float = 0.25, vol: float = 0.45) -> PriceData:
    rng = random.Random(seed)
    dates = [f"D{i:04d}" for i in range(days)]
    closes: dict[str, list[float]] = {}
    for s in _cap(symbols):
        price = 100.0
        series = []
        for _ in range(days):
            z = rng.gauss(0, 1)
            price = max(1.0, price * math.exp((drift - 0.5 * vol * vol) / 252 + vol / math.sqrt(252) * z))
            series.append(round(price, 4))
        closes[s] = series
    return PriceData(dates=dates, closes=closes, source="synthetic")


# --- public loader --------------------------------------------------------
def load_prices(symbols: list[str], start: str = "2022-01-01", end: str | None = None,
                source: str = "auto") -> PriceData:
    syms = _cap(symbols)
    raw: dict[str, tuple[list[str], list[float]]] = {}
    used = source

    for s in syms:
        got = None
        if source in ("auto", "csv"):
            got = _load_csv(s)
            if got:
                used = "csv"
        if got is None and source in ("auto", "yfinance"):
            got = _fetch_yfinance(s, start, end)
            if got:
                used = "yfinance"; save_csv(s, *got)
        if got is None and source in ("auto", "stooq"):
            got = _fetch_stooq(s, start, end)
            if got:
                used = "stooq"; save_csv(s, *got)
        if got is None:
            raise RuntimeError(
                f"No price data for {s}. Run locally with yfinance installed, or drop a CSV "
                f"at data/prices/{s}.csv (columns: date,close)."
            )
        raw[s] = got

    # align to the common date range (intersection)
    common = set(raw[syms[0]][0])
    for s in syms[1:]:
        common &= set(raw[s][0])
    dates = sorted(common)
    closes = {s: [c for d, c in zip(raw[s][0], raw[s][1]) if d in common] for s in syms}
    return PriceData(dates=dates, closes=closes, source=used)
