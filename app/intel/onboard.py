"""Onboarding pipeline — route a new ticker into every layer that needs it.

Adding a symbol used to be tribal knowledge: fetch prices somewhere, pull
fundamentals through the MCP, hand-write two snapshots, remember to add a
thesis. Miss one step and the failure is silent — the name simply scores off
synthetic prices, or gets graded against a snapshot that has no entry for it,
and nothing anywhere says so. That is exactly how 24 of 30 held names ended up
running on fabricated volatility.

So this does the automatable half and states the rest precisely.

    AUTOMATED        real daily closes, derived metrics, screen verdicts,
                     volatility rank, macro-fact coverage, curated-source views
    NEEDS AN MCP     fundamentals and SEC filing facts. The portal has no broker
    PULL             credentials by design; Claude fetches these and commits them
                     as DATED snapshots, which is also what keeps them
                     point-in-time honest.

The output is a READINESS verdict per leg, because the two legs need different
data and a symbol is routinely ready for one and not the other:

    OPTIONS  needs price history deep enough to RANK volatility, not merely to
             measure it. A level without its own distribution is not a signal,
             so ~1 year is the real bar.
    CORE     needs fundamentals, filing facts, and a thesis with checkpoints
             written before the outcome. Prices are almost irrelevant here.

A symbol that passes neither is not rejected — it is reported as data-only, with
the exact missing pieces named. The point is that nothing is ever silently
half-onboarded.

    python -m app.intel.onboard MU
    python -m app.intel.onboard MU SNDK AVGO --fetch
    python -m app.intel.onboard --held
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field

from ..config import ROOT

PRICES_DIR = ROOT / "data" / "prices"
FUNDAMENTALS_DIR = ROOT / "data" / "fundamentals"

# vol_rank needs a distribution, not a reading. app/options/desk.py requires
# window + lookback//4 bars before it will rank at all; a full year makes the
# percentile meaningful rather than merely computable.
MIN_BARS_TO_MEASURE_VOL = 60
MIN_BARS_TO_RANK_VOL = 252

READY_BOTH, READY_OPTIONS, READY_CORE, DATA_ONLY, NO_DATA = (
    "READY_BOTH", "READY_OPTIONS_ONLY", "READY_CORE_ONLY", "DATA_ONLY", "NO_DATA")


@dataclass
class Readiness:
    symbol: str
    bars: int = 0
    newest_bar: str | None = None
    has_fundamentals: bool = False
    has_filings: bool = False
    has_thesis: bool = False
    macro_facts: list[str] = field(default_factory=list)
    source_views: int = 0
    held: bool = False
    metrics: dict = field(default_factory=dict)
    screens: dict = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    mcp_pulls: list[str] = field(default_factory=list)

    @property
    def options_ready(self) -> bool:
        return self.bars >= MIN_BARS_TO_RANK_VOL

    @property
    def core_ready(self) -> bool:
        return self.has_fundamentals and self.has_filings and self.has_thesis

    def verdict(self) -> str:
        if self.bars == 0 and not self.has_fundamentals:
            return NO_DATA
        if self.options_ready and self.core_ready:
            return READY_BOTH
        if self.options_ready:
            return READY_OPTIONS
        if self.core_ready:
            return READY_CORE
        return DATA_ONLY

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()} | {
            "verdict": self.verdict(),
            "options_ready": self.options_ready,
            "core_ready": self.core_ready,
        }


def _price_state(symbol: str) -> tuple[int, str | None, list[float]]:
    p = PRICES_DIR / f"{symbol.upper()}.csv"
    if not p.exists():
        return 0, None, []
    import csv
    closes, last = [], None
    with p.open("r", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                closes.append(float(row["close"]))
                last = row.get("date") or last
            except (KeyError, ValueError):
                continue
    return len(closes), last, closes


def _snapshot_has(symbol: str, prefix: str) -> bool:
    """Is this symbol present in the newest dated snapshot of that kind?"""
    if not FUNDAMENTALS_DIR.exists():
        return False
    pattern = "filings_*.json" if prefix == "filings" else "????-??-??.json"
    files = sorted(FUNDAMENTALS_DIR.glob(pattern))
    if not files:
        return False
    try:
        d = json.loads(files[-1].read_text(encoding="utf-8"))
        return symbol.upper() in (d.get("symbols") or {})
    except Exception:
        return False


def fetch_prices(symbol: str, start: str = "2022-01-01") -> tuple[bool, str]:
    """Pull and cache real daily closes. The one step that IS automatable."""
    try:
        import warnings
        warnings.filterwarnings("ignore")
        from ..backtest.data import load_prices
        d = load_prices([symbol], start=start)
        return True, f"cached {len(d)} bars"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def assess(symbol: str, fetch: bool = False) -> Readiness:
    sym = symbol.upper()
    r = Readiness(symbol=sym)

    if fetch and not (PRICES_DIR / f"{sym}.csv").exists():
        ok, msg = fetch_prices(sym)
        if not ok:
            r.missing.append(f"price fetch failed — {msg}")

    r.bars, r.newest_bar, closes = _price_state(sym)
    r.has_fundamentals = _snapshot_has(sym, "fundamentals")
    r.has_filings = _snapshot_has(sym, "filings")

    try:
        from .thesis import LEDGER
        r.has_thesis = any(t.symbol == sym for t in LEDGER)
    except Exception:
        pass

    try:
        from ..macro.facts import facts_as_of
        r.macro_facts = [f.key for f in facts_as_of()
                         if sym in {s.upper() for s in f.affected_symbols}]
    except Exception:
        pass

    try:
        from .feeds import KNOWN_SOURCES, FeedStore
        store = FeedStore()
        r.source_views = sum(len(store.views(source=s, symbol=sym)) for s in KNOWN_SOURCES)
    except Exception:
        pass

    try:
        from ..portfolio.holdings import load_holdings
        r.held = any(str(h["symbol"]).upper() == sym for h in load_holdings())
    except Exception:
        pass

    # Derived metrics, only where the data supports them.
    if r.bars >= MIN_BARS_TO_MEASURE_VOL:
        try:
            from ..options.desk import realized_vol, vol_rank
            r.metrics = {"last": round(closes[-1], 2),
                         "realized_vol": realized_vol(sym),
                         "vol_rank": vol_rank(sym)}
        except Exception:
            pass
        try:
            from ..analytics.deep_dive import _screens
            r.screens = _screens(sym)
        except Exception:
            pass

    # Name every gap, and say exactly what would close it.
    if r.bars == 0:
        r.missing.append("no price history at all — every metric is unavailable")
    elif r.bars < MIN_BARS_TO_MEASURE_VOL:
        r.missing.append(f"only {r.bars} bars; {MIN_BARS_TO_MEASURE_VOL}+ needed to measure vol")
    elif r.bars < MIN_BARS_TO_RANK_VOL:
        r.missing.append(f"{r.bars} bars measures vol but cannot RANK it "
                         f"({MIN_BARS_TO_RANK_VOL}+ needed) — the options desk will decline it")
    if not r.has_fundamentals:
        r.mcp_pulls.append(f"get_equity_fundamentals + get_financials(['{sym}'], quarterly, 8) "
                           f"-> data/fundamentals/<today>.json")
    if not r.has_filings:
        r.mcp_pulls.append(f"get_sec_filing_index('{sym}') -> get_sec_filing_facts("
                           f"[InventoryNet, CostOfGoodsAndServicesSold, CostOfRevenue, "
                           f"RevenueFromContractWithCustomerExcludingAssessedTax]) "
                           f"-> data/fundamentals/filings_<today>.json")
    if not r.has_thesis and (r.has_fundamentals or r.held):
        r.missing.append("no thesis — held or covered, but nothing states what would "
                         "prove the position wrong")
    if r.held and r.bars < MIN_BARS_TO_MEASURE_VOL:
        r.missing.append("HELD with no usable price history — this position is being "
                         "sized off synthetic volatility")
    return r


def assess_many(symbols: list[str], fetch: bool = False) -> list[Readiness]:
    return [assess(s, fetch=fetch) for s in symbols]


def held_symbols(include_untradeable: bool = False) -> list[str]:
    """Held names worth assessing. Delisted positions are excluded by default —
    they have no feed to route in and no exit to plan, so listing them as gaps
    every run only teaches the reader to skim past this section."""
    try:
        from ..portfolio.holdings import UNTRADEABLE, load_holdings
        syms = {str(h["symbol"]).upper() for h in load_holdings()}
        if not include_untradeable:
            syms -= set(UNTRADEABLE)
        return sorted(syms)
    except Exception:
        return []


def report() -> dict:
    """Agent entrypoint — onboarding coverage across the whole book."""
    rows = assess_many(held_symbols())
    by = {}
    for r in rows:
        by.setdefault(r.verdict(), []).append(r.symbol)
    return {"assessed": len(rows), "by_verdict": by,
            "needs_mcp_pull": sorted(r.symbol for r in rows if r.mcp_pulls),
            "held_on_synthetic_prices": sorted(
                r.symbol for r in rows if r.bars < MIN_BARS_TO_MEASURE_VOL)}


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="*")
    ap.add_argument("--held", action="store_true", help="assess every held name")
    ap.add_argument("--fetch", action="store_true", help="pull missing price history")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    syms = held_symbols() if args.held else [s.upper() for s in args.symbols]
    if not syms:
        ap.error("give one or more symbols, or --held")

    rows = assess_many(syms, fetch=args.fetch)
    if args.json:
        print(json.dumps([r.to_dict() for r in rows], indent=2))
        return

    print("=" * 78)
    print("  ONBOARDING — what each symbol can actually be used for")
    print("=" * 78)
    for r in rows:
        held = "  [HELD]" if r.held else ""
        print(f"\n  {'-' * 74}")
        print(f"  {r.symbol:6} {r.verdict()}{held}")
        print(f"      prices     {r.bars} bars"
              + (f", newest {r.newest_bar}" if r.newest_bar else "")
              + f"   ·   options {'YES' if r.options_ready else 'no'}"
              f"   core {'YES' if r.core_ready else 'no'}")
        if r.metrics:
            vr = r.metrics.get("vol_rank")
            print(f"      vol        {r.metrics.get('realized_vol')}"
                  f"   rank {f'{vr:.0f}/100' if vr is not None else 'unrankable'}")
        if r.screens.get("available"):
            passed = [k for k in ("core", "options", "tactical") if r.screens[k]["passed"]]
            print(f"      screens    {', '.join(passed) if passed else 'none passed'}")
        print(f"      layers     fundamentals {'Y' if r.has_fundamentals else 'N'}"
              f" · filings {'Y' if r.has_filings else 'N'}"
              f" · thesis {'Y' if r.has_thesis else 'N'}"
              f" · macro {len(r.macro_facts)} · sources {r.source_views}")
        for m in r.missing:
            print(f"      GAP        {m}")
        for p in r.mcp_pulls:
            print(f"      MCP PULL   {p}")

    print(f"\n  {'=' * 74}")
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.verdict()] = counts.get(r.verdict(), 0) + 1
    print("  " + "   ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    need = [r.symbol for r in rows if r.mcp_pulls]
    if need:
        print(f"\n  {len(need)} symbol(s) need an MCP pull Claude must run — the portal")
        print(f"  has no broker credentials by design: {', '.join(need[:12])}"
              + (" ..." if len(need) > 12 else ""))


if __name__ == "__main__":
    _main()
