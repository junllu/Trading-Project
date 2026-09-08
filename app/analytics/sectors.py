"""Market-wide sector rotation — where money is going, not just where we are.

app/intel/themes.py tracks the themes the book is IN. That answers "is what I
own still working" and is blind by construction to everything else: a theme has
to be hand-added before it can be measured, so the map only ever covers ground
already walked. Money rotating INTO a sector nobody tagged is invisible, which
is the same coverage failure the discovery screener exists to fix.

This is the complement — the standard sector map, measured whether or not the
book has a position:

    XLK XLF XLE XLV XLI XLY XLP XLU XLB XLRE XLC   the eleven GICS sectors
    SMH IGV XBI ITA KRE                            sub-industries that matter
                                                   here (semis, software, biotech,
                                                   defence, regional banks)

WHAT IS MEASURED

Relative strength against SPY over three windows, because rotation is a change
in ranking, not a level. A sector up 8% in a market up 15% is rotating OUT while
still rising — the case a price chart hides and a P&L never shows.

    1m   where money moved recently
    3m   the trend that is actually established
    6m   the regime

The signal worth acting on is ACCELERATION: 1m relative strength better than 3m
means money is arriving now. That is what "rotation" means operationally, and it
is measurable without any judgement about narrative.

DEFENSIVE LEADERSHIP IS THE ONE READING THAT MEANS SOMETHING ON ITS OWN.
When XLP, XLU and XLV lead, the market is paying for safety. It does not predict
a drawdown — nothing here does — but it says the bid has changed character, and
for a book that is 63% in one theme that is worth knowing before it shows up in
the equity line.

    python -m app.analytics.sectors
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass, field

from ..config import ROOT

PRICES_DIR = ROOT / "data" / "prices"
BENCHMARK = "SPY"

WINDOWS = {"1m": 21, "3m": 63, "6m": 126}

SECTORS: dict[str, str] = {
    "XLK": "Technology", "XLF": "Financials", "XLE": "Energy",
    "XLV": "Health Care", "XLI": "Industrials", "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples", "XLU": "Utilities", "XLB": "Materials",
    "XLRE": "Real Estate", "XLC": "Communication Svcs",
}
SUB_INDUSTRIES: dict[str, str] = {
    "SMH": "Semiconductors", "IGV": "Software", "XBI": "Biotech",
    "ITA": "Aerospace & Defence", "KRE": "Regional Banks",
}

# Sectors people buy when they want to stop losing money rather than to make it.
DEFENSIVE = {"XLP", "XLU", "XLV"}

# Where the book's themes map onto the sector grid, so exposure can be shown
# against the market rather than only against itself.
THEME_TO_SECTOR = {
    "ai_infra": "SMH", "memory": "SMH", "crypto": "XLF", "solar_clean": "XLU",
    "energy": "XLE", "ev": "XLY", "defense": "ITA", "weed": "XLV", "meme": "XLY",
}

LEADING, IMPROVING, WEAKENING, LAGGING = (
    "LEADING", "IMPROVING", "WEAKENING", "LAGGING")


@dataclass
class SectorState:
    symbol: str
    label: str
    kind: str                       # sector | sub_industry
    rel: dict[str, float] = field(default_factory=dict)   # window -> vs SPY pp
    accelerating: bool = False
    quadrant: str = ""
    defensive: bool = False
    exposure_usd: float = 0.0

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _closes(symbol: str) -> list[float]:
    p = PRICES_DIR / f"{symbol.upper()}.csv"
    if not p.exists():
        return []
    out = []
    with p.open("r", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                out.append(float(row["close"]))
            except (KeyError, ValueError):
                continue
    return out


def _pct(c: list[float], bars: int) -> float | None:
    return (c[-1] / c[-bars - 1] - 1) * 100 if len(c) > bars else None


def _quadrant(rel_1m: float, rel_3m: float) -> str:
    """The rotation grid: current strength against its own direction.

    IMPROVING is the interesting box — still behind the index, but the gap is
    closing. That is money arriving, and it is invisible to any screen that
    ranks on absolute performance.
    """
    if rel_3m > 0 and rel_1m >= rel_3m:
        return LEADING
    if rel_3m <= 0 and rel_1m > rel_3m:
        return IMPROVING
    if rel_3m > 0 and rel_1m < rel_3m:
        return WEAKENING
    return LAGGING


def _exposure_by_sector() -> dict[str, float]:
    """Book exposure mapped onto the sector grid, via theme membership."""
    try:
        from ..intel.themes import THEMES
        from ..portfolio.holdings import load_holdings
        pos: dict[str, float] = {}
        for h in load_holdings():
            s = str(h["symbol"]).upper()
            last = float(h.get("last", 0)) or float(h.get("avg_price", 0))
            pos[s] = pos.get(s, 0.0) + float(h["shares"]) * last
        out: dict[str, float] = {}
        for theme, members in THEMES.items():
            etf = THEME_TO_SECTOR.get(theme)
            if not etf:
                continue
            out[etf] = out.get(etf, 0.0) + sum(pos.get(m, 0.0) for m in members)
        return out
    except Exception:
        return {}


def build() -> list[SectorState]:
    bench = _closes(BENCHMARK)
    if not bench:
        return []
    exposure = _exposure_by_sector()
    out: list[SectorState] = []
    for mapping, kind in ((SECTORS, "sector"), (SUB_INDUSTRIES, "sub_industry")):
        for sym, label in mapping.items():
            c = _closes(sym)
            if len(c) < max(WINDOWS.values()) + 1:
                continue
            st_ = SectorState(symbol=sym, label=label, kind=kind,
                              defensive=sym in DEFENSIVE,
                              exposure_usd=round(exposure.get(sym, 0.0)))
            for w, bars in WINDOWS.items():
                a, b = _pct(c, bars), _pct(bench, bars)
                if a is not None and b is not None:
                    st_.rel[w] = round(a - b, 1)
            if "1m" in st_.rel and "3m" in st_.rel:
                st_.accelerating = st_.rel["1m"] > st_.rel["3m"]
                st_.quadrant = _quadrant(st_.rel["1m"], st_.rel["3m"])
            out.append(st_)
    return sorted(out, key=lambda s: -s.rel.get("1m", -999))


def report() -> dict:
    rows = build()
    if not rows:
        return {"error": "no sector price history"}
    sectors = [s for s in rows if s.kind == "sector"]
    top3 = [s.symbol for s in sectors[:3]]
    defensive_leading = [s.symbol for s in sectors[:4] if s.defensive]
    return {
        "rows": [s.to_dict() for s in rows],
        "leaders_1m": top3,
        "laggards_1m": [s.symbol for s in sectors[-3:]],
        "improving": [s.symbol for s in rows if s.quadrant == IMPROVING],
        "weakening_with_exposure": [s.symbol for s in rows
                                    if s.quadrant == WEAKENING and s.exposure_usd > 0],
        "defensive_leadership": bool(len(defensive_leading) >= 2),
        "defensive_in_top4": defensive_leading,
        "note": ("Relative strength, not absolute. A sector up 8% in a market up 15% "
                 "is rotating OUT while still rising."),
    }


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    r = report()
    if args.json:
        print(json.dumps(r, indent=2))
        return
    if "error" in r:
        print(r["error"])
        return

    rows = build()
    print("=" * 78)
    print("  SECTOR ROTATION — the whole market, not just what we own")
    print("=" * 78)
    print(f"  relative to {BENCHMARK}, in percentage points\n")
    print(f"  {'':6} {'sector':22} {'1m':>7} {'3m':>7} {'6m':>7}  {'quadrant':<10} {'exposure':>10}")
    print("  " + "-" * 74)
    for kind, title in (("sector", "GICS SECTORS"), ("sub_industry", "SUB-INDUSTRIES")):
        print(f"  {title}")
        for s in [x for x in rows if x.kind == kind]:
            arrow = "^" if s.accelerating else "v"
            d = " *" if s.defensive else "  "
            exp = f"${s.exposure_usd:,}" if s.exposure_usd else "—"
            print(f"  {s.symbol:6} {s.label:22} {s.rel.get('1m', 0):>+6.1f}{arrow} "
                  f"{s.rel.get('3m', 0):>+6.1f}  {s.rel.get('6m', 0):>+6.1f}  "
                  f"{s.quadrant:<10}{d} {exp:>10}")
        print()

    print("=" * 78)
    print(f"  leading 1m   {', '.join(r['leaders_1m'])}")
    print(f"  lagging 1m   {', '.join(r['laggards_1m'])}")
    if r["improving"]:
        print(f"  IMPROVING    {', '.join(r['improving'])}")
        print("               behind the index but closing — money arriving, and")
        print("               invisible to any screen ranked on absolute performance")
    if r["weakening_with_exposure"]:
        print(f"  WEAKENING while we hold it: {', '.join(r['weakening_with_exposure'])}")
    if r["defensive_leadership"]:
        print(f"\n  ** DEFENSIVE LEADERSHIP — {', '.join(r['defensive_in_top4'])} in the top 4.")
        print("     The bid has changed character. Not a forecast; a fact worth")
        print("     knowing before it reaches the equity line.")
    print(f"\n  {r['note']}")


if __name__ == "__main__":
    _main()
