"""Market data provider.

Pulls live quotes from whichever broker is available and keeps a rolling
in-memory history of closes per symbol, which the strategy layer consumes to
compute indicators. In paper mode it synthesizes a gently random-walking price
series so the whole pipeline is exercisable offline.
"""
from __future__ import annotations

import random
from collections import defaultdict, deque
from typing import Optional

from ..brokers.base import BrokerBase, BrokerError
from ..models import Quote


class MarketData:
    def __init__(self, primary: Optional[BrokerBase] = None, history_len: int = 250):
        self.primary = primary
        self.history_len = history_len
        self._history: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=history_len))
        self._last: dict[str, Quote] = {}

    def set_primary(self, broker: BrokerBase) -> None:
        self.primary = broker

    def quote(self, symbol: str) -> Quote:
        q: Optional[Quote] = None
        if self.primary is not None:
            try:
                q = self.primary.get_quote(symbol)
            except (BrokerError, Exception):
                q = None
        if q is None:
            q = self._synthetic(symbol)
        self._last[symbol] = q
        self._history[symbol].append(q.price)
        return q

    def refresh(self, symbols: list[str]) -> dict[str, Quote]:
        return {s: self.quote(s) for s in symbols}

    def history(self, symbol: str) -> list[float]:
        return list(self._history[symbol])

    def last(self, symbol: str) -> Optional[Quote]:
        return self._last.get(symbol)

    def seed_history(self, symbol: str, prices: list[float]) -> None:
        dq = self._history[symbol]
        dq.clear()
        for p in prices[-self.history_len:]:
            dq.append(float(p))
        if prices:
            self._last[symbol] = Quote(symbol=symbol, price=float(prices[-1]))

    def load_real_history(self, symbol: str, bars: int | None = None) -> bool:
        """Seed history from REAL daily closes. Returns True on success.

        Sources, in order: the CSV cache under data/prices/ (written by the
        backtest data layer), then yfinance. Falls back to nothing — the caller
        decides whether to synthesize.

        This matters more than it looks: `backfill_trend` produces a nearly
        straight line, which makes realized_vol read ~0.03-0.05 for names whose
        true annualized vol is 0.5-1.2. That pins the sizing volatility scalar
        at its 1.5 ceiling for every symbol, so the risk-parity term in
        app/engine/sizing.py silently does nothing at all.
        """
        n = bars or self.history_len
        try:
            from ..config import ROOT
            p = ROOT / "data" / "prices" / f"{symbol.upper()}.csv"
            if p.exists():
                import csv
                closes = []
                with p.open("r", encoding="utf-8") as fh:
                    for row in csv.DictReader(fh):
                        try:
                            closes.append(float(row["close"]))
                        except (KeyError, ValueError):
                            continue
                if len(closes) >= 20:
                    self.seed_history(symbol, closes[-n:])
                    return True
        except Exception:
            pass
        try:
            import yfinance as yf
            df = yf.download(symbol, period="1y", progress=False, auto_adjust=True)
            if df is not None and not df.empty:
                closes = [float(x) for x in df["Close"].values.ravel()]
                if len(closes) >= 20:
                    self.seed_history(symbol, closes[-n:])
                    return True
        except Exception:
            pass
        return False

    def backfill_trend(self, symbol: str, start: float, end: float, bars: int = 80,
                       noise: float = 0.012) -> None:
        """Synthesize a plausible price path from `start` to `end`.

        A stand-in for a real historical feed: interpolates cost→current with
        mild noise, so technical indicators have something to chew on and the
        trend reflects the actual move you've experienced in the name. Replace
        with a real data provider (yfinance/broker) when available.
        """
        if start <= 0 or end <= 0 or bars < 2:
            self.seed_history(symbol, [end])
            return
        rng = random.Random(hash(symbol) & 0xFFFFFFFF)  # deterministic per symbol
        prices: list[float] = []
        for i in range(bars):
            t = i / (bars - 1)
            base = start * (1 - t) + end * t                     # linear glide
            jitter = 1 + rng.uniform(-noise, noise) * (1 - abs(2 * t - 1))
            prices.append(round(max(0.01, base * jitter), 4))
        prices[-1] = float(end)                                   # pin the last bar
        self.seed_history(symbol, prices)

    def _synthetic(self, symbol: str) -> Quote:
        hist = self._history[symbol]
        if hist:
            last = hist[-1]
            drift = random.uniform(-0.01, 0.01)
            price = round(max(1.0, last * (1 + drift)), 2)
        else:
            price = round(random.uniform(50, 300), 2)
        return Quote(symbol=symbol, price=price)


def report() -> dict:
    """Agent entrypoint — coverage and freshness of the real-price cache.

    This maker's failure mode is not an exception, it is fabrication: an earlier
    version silently synthesised near-straight lines when a symbol was missing,
    which read as ~0.05 annualised volatility for names whose true vol was above
    1.0 and pinned every volatility scalar at its ceiling. Sizing looked like it
    was working and was inert. So the number that matters here is how many held
    names have REAL history, and any gap is named rather than filled.
    """
    import csv
    from datetime import date, datetime

    from ..config import ROOT

    prices_dir = ROOT / "data" / "prices"
    files = sorted(prices_dir.glob("*.csv")) if prices_dir.exists() else []

    newest: str | None = None
    stale: list[str] = []
    for f in files:
        try:
            with f.open("r", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            if not rows:
                stale.append(f.stem)
                continue
            last = rows[-1].get("date") or rows[-1].get("Date")
            if last:
                newest = max(newest, last) if newest else last
        except Exception:
            stale.append(f.stem)

    cached = {f.stem.upper() for f in files}
    try:
        from ..portfolio.holdings import UNTRADEABLE, load_holdings
        held = {str(h["symbol"]).upper() for h in load_holdings()}
        # Excluded, not missing. A delisted position has no feed to fetch, and
        # reporting it as a gap forever teaches the reader to ignore this list.
        excluded = held & set(UNTRADEABLE)
        held -= excluded
    except Exception:
        held, excluded = set(), set()
    missing = sorted(held - cached)

    age = None
    if newest:
        try:
            age = (date.today() - datetime.strptime(newest, "%Y-%m-%d").date()).days
        except ValueError:
            age = None

    return {
        "symbols_cached": len(files),
        "newest_bar": newest,
        "age_days": age,
        "unreadable": sorted(stale),
        "held_symbols": len(held),
        "excluded_untradeable": sorted(excluded),
        "held_without_real_history": missing,
        "coverage_pct": round(100 * (1 - len(missing) / len(held)), 1) if held else None,
        "note": ("Held names absent here fall back to SYNTHETIC prices, whose "
                 "volatility is meaningless — every risk scalar computed from them "
                 "is wrong in the direction of taking more risk, not less."),
    }
