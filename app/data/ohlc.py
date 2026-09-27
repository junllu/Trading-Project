"""OHLC bars — the cache holds closes only, and a candle needs four prices.

Every price file in data/prices/ is `date,close`. That is enough for returns,
volatility and trend, which is all the system has needed until now. It is not
enough for a candlestick: an engulfing bar is defined by where the open sits
relative to the previous close, a hammer by the lower wick, a doji by open
against close. None of that is recoverable from a close series.

So OHLC lives in its own store rather than widening the existing files. Two
reasons: the close cache is read by a dozen modules that would all need to
change, and OHLC is only fetched for the handful of names actually traded,
where the close cache spans 178.

    data/ohlc/TSLA.csv   date,open,high,low,close,volume

A NOTE ON ADJUSTMENT

Bars are split-adjusted. This matters more here than elsewhere: an unadjusted
10:1 split prints as a 90% gap down, which every gap detector in existence will
read as a crash. The exit-discipline module already hit the raw-vs-adjusted trap
once — NVDA's split showed as a -76% "saving" — so it is worth being explicit
that these are adjusted and consistent within a file.

    python -m app.data.ohlc TSLA AMD COIN
    python -m app.data.ohlc --held
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date

from ..config import ROOT

OHLC_DIR = ROOT / "data" / "ohlc"


@dataclass
class Bar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def bullish(self) -> bool:
        return self.close > self.open

    @property
    def body_pct(self) -> float:
        """Body as a share of the whole range. 0 = doji, 1 = marubozu."""
        return self.body / self.range if self.range > 0 else 0.0


# The universe exists to buy STATISTICAL POWER, not coverage for its own sake.
#
# Four symbols was the binding constraint on every pattern result: AMD and NVDA
# move together, so four names is closer to two independent observations, and
# "this pattern works" really meant "this pattern worked on TSLA". Widening the
# cross-section is the only axis that adds genuine power — more features only
# multiply the trial count and raise the bar they then have to clear.
#
# Benchmarks and sector ETFs are here as FEATURE INPUTS, not trade candidates:
# relative strength and regime state are defined against them.
BENCHMARKS = ["SPY", "QQQ", "IWM", "DIA"]
SECTOR_ETFS = ["XLK", "XLE", "XLF", "XLV", "XLI", "XLU",
               "XLP", "XLY", "XLB", "XLRE", "XLC", "SMH", "XBI"]
VOL_INDEX = ["^VIX"]

# Liquid, genuinely volatile large caps — the sleeve's hunting ground and, more
# importantly, enough independent names for a cross-sectional test to mean
# something. Spread across sectors ON PURPOSE: a universe of ten semis would
# re-create the correlation problem it is meant to solve.
LIQUID_VOLATILE = [
    "TSLA", "NVDA", "AMD", "AVGO", "MU", "MRVL", "INTC", "QCOM", "TXN", "AMAT",
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NFLX", "CRM", "ADBE", "ORCL",
    "COIN", "MSTR", "HOOD", "XYZ", "PYPL", "SOFI",   # SQ renamed XYZ (2025)
    "PLTR", "SNOW", "NOW", "PANW", "CRWD", "DDOG", "NET",
    "XOM", "CVX", "COP", "SLB", "OXY",
    "JPM", "GS", "BAC", "SCHW",
    "LLY", "UNH", "MRNA", "PFE",
    "CAT", "DE", "BA", "GE", "LMT", "RTX",
    "F", "GM", "RIVN", "LCID", "NIO",
    "UBER", "ABNB", "DASH", "SHOP", "RBLX", "U", "RDDT",
    "VRT", "ANET", "ALAB", "ARM", "SMCI", "DELL",
]

UNTRADEABLE = {"NEWYY", "EA", "SQ"}   # delisted / taken private / renamed; kept out so coverage math stays honest


def universe(include_held: bool = True) -> list[str]:
    """Every symbol worth caching bars for, de-duplicated and ordered."""
    syms = list(LIQUID_VOLATILE) + BENCHMARKS + SECTOR_ETFS + VOL_INDEX
    if include_held:
        try:
            import yaml
            from ..config import ROOT as _R
            h = yaml.safe_load((_R / "config" / "holdings.yaml").read_text("utf-8")) or {}
            syms += [x["symbol"] for x in h.get("holdings", []) if x.get("symbol")]
        except Exception:
            pass
    seen, out = set(), []
    for s in syms:
        u = s.upper()
        if u not in seen and u not in UNTRADEABLE:
            seen.add(u)
            out.append(u)
    return out


def path_for(symbol: str):
    return OHLC_DIR / f"{symbol.upper()}.csv"


def load(symbol: str, limit: int | None = None) -> list[Bar]:
    p = path_for(symbol)
    if not p.exists():
        return []
    out: list[Bar] = []
    with p.open("r", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                out.append(Bar(row["date"], float(row["open"]), float(row["high"]),
                               float(row["low"]), float(row["close"]),
                               float(row.get("volume") or 0)))
            except (KeyError, ValueError):
                continue
    return out[-limit:] if limit else out


def fetch(symbol: str, start: str = "2018-01-01") -> tuple[bool, str]:
    """Pull split-adjusted daily OHLCV and cache it."""
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import yfinance as yf
        df = yf.download(symbol, start=start, progress=False,
                         auto_adjust=True, actions=False)
        if df is None or df.empty:
            return False, "no data returned"
        if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
            df.columns = df.columns.get_level_values(0)

        OHLC_DIR.mkdir(parents=True, exist_ok=True)
        n = 0
        with path_for(symbol).open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "open", "high", "low", "close", "volume"])
            for idx, r in df.iterrows():
                try:
                    o, h, lo, c = (float(r["Open"]), float(r["High"]),
                                   float(r["Low"]), float(r["Close"]))
                except (KeyError, TypeError, ValueError):
                    continue
                # A bar whose high is below its low, or whose open sits outside
                # the range, is corrupt. Dropped rather than fed to a pattern
                # detector that would read it as an extraordinary signal.
                if not (lo <= o <= h and lo <= c <= h and h >= lo):
                    continue
                w.writerow([idx.strftime("%Y-%m-%d"), o, h, lo, c,
                            float(r.get("Volume", 0) or 0)])
                n += 1
        return (n > 0), f"{n} bars"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def coverage() -> dict:
    files = sorted(OHLC_DIR.glob("*.csv")) if OHLC_DIR.exists() else []
    out = {}
    for f in files:
        bars = load(f.stem)
        if bars:
            out[f.stem] = {"bars": len(bars), "first": bars[0].date, "last": bars[-1].date}
    return out


def report() -> dict:
    cov = coverage()
    stale = []
    for sym, v in cov.items():
        try:
            age = (date.today() - date.fromisoformat(v["last"])).days
            if age > 5:
                stale.append(f"{sym} ({age}d)")
        except ValueError:
            pass
    return {"symbols": len(cov), "coverage": cov, "stale": stale,
            "note": "OHLC is fetched only for names actually traded; the close "
                    "cache stays the broad universe."}


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="*")
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--universe", action="store_true",
                    help="fetch the full cross-section (see universe())")
    ap.add_argument("--missing-only", action="store_true",
                    help="skip symbols already cached")
    args = ap.parse_args()

    if args.universe:
        have = set(coverage())
        want = universe()
        todo = [s for s in want if s not in have] if args.missing_only else want
        print(f"universe: {len(want)} symbols, {len(have)} cached, fetching {len(todo)}")
        ok = 0
        for i, s in enumerate(todo, 1):
            good, msg = fetch(s, args.start)
            ok += good
            print(f"  [{i:>3}/{len(todo)}] {s:6} {'OK  ' if good else 'FAIL'} {msg}")
        print(f"\n{ok}/{len(todo)} fetched. Store now holds {len(coverage())} symbols.")
        return

    if args.status or not args.symbols:
        r = report()
        print(f"OHLC store: {r['symbols']} symbol(s)")
        for sym, v in r["coverage"].items():
            print(f"  {sym:6} {v['bars']:>5} bars   {v['first']} -> {v['last']}")
        if r["stale"]:
            print(f"  stale: {', '.join(r['stale'])}")
        if not args.symbols:
            return

    for s in args.symbols:
        ok, msg = fetch(s.upper(), args.start)
        print(f"  {s.upper():6} {'OK  ' if ok else 'FAIL'} {msg}")


if __name__ == "__main__":
    _main()
