"""Theme membership, derived from price behaviour instead of hand-curated.

app/intel/themes.py carries a hand-written membership list. It covers 29 of 178
cached symbols — 16%. Everything else reaches the screener as "untagged", which
means the operator's actual edge (theme selection, and theme-rotation exits) is
unavailable for 84% of the universe. That is the same coverage failure the
discovery screener was built to fix, reappearing one layer down.

Hand-curation cannot close it. Adding names by hand does not scale, goes stale
the moment a business changes, and quietly encodes an opinion: putting BE in
"solar_clean" rather than "ai_infra" flips its theme verdict from FADING to
ALIVE without a single number changing.

So membership is MEASURED. For each symbol, correlate daily returns against
every sector and sub-industry ETF over ~6 months and assign it to the strongest
match. A stock's theme is not what its press release says; it is what it trades
with.

TWO PROPERTIES THAT MATTER

  A weak best match is left UNTAGGED. Below MIN_CORRELATION a name genuinely
  does not move with any group here, and forcing it into the nearest one would
  hand the screener a theme verdict that means nothing. "No theme" is a finding.

  The hand-written map still WINS where it exists. Curation encodes knowledge
  price cannot see — MU and SNDK belong to "memory" for reasons that survive a
  quarter where they happen to trade like the broad market. Derivation fills the
  gap; it does not overrule judgement.

    python -m app.analytics.theme_map
    python -m app.analytics.theme_map --symbol PANW
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from datetime import date

from ..config import ROOT

PRICES_DIR = ROOT / "data" / "prices"
CACHE_PATH = ROOT / "data" / "theme_map.json"

WINDOW = 126                 # ~6 months of sessions
MIN_BARS = 80
MIN_CORRELATION = 0.45       # below this, "closest" is not the same as "belongs"
CACHE_MAX_AGE_DAYS = 7

# Which ETF stands for which theme in app/intel/themes.py. Only themes with a
# clean traded proxy appear — there is no ETF for "meme", so that stays curated.
THEME_PROXY: dict[str, str] = {
    "ai_infra": "SMH", "memory": "SMH", "crypto": "XLF", "solar_clean": "XLU",
    "energy": "XLE", "ev": "XLY", "defense": "ITA",
    "software": "IGV", "biotech": "XBI", "health": "XLV",
    "financials": "XLF", "industrials": "XLI", "staples": "XLP",
    "materials": "XLB", "realestate": "XLRE", "comms": "XLC",
    "discretionary": "XLY", "tech": "XLK", "banks": "KRE",
}
PROXY_TO_THEME = {}
for _theme, _etf in THEME_PROXY.items():
    PROXY_TO_THEME.setdefault(_etf, _theme)

# Proxies that move together. A name scoring 0.60 on software and 0.55 on tech
# is not ambiguous — those two ETFs share most of their constituents, so the
# "tie" is granularity, not confusion. Requiring a gap against a sibling threw
# away correct assignments (CDNS -> software was rejected exactly this way).
# A gap is only demanded against a proxy from a DIFFERENT family.
PROXY_FAMILIES: list[set[str]] = [
    {"XLK", "IGV", "SMH"},        # tech / software / semis
    {"XLV", "XBI"},               # healthcare / biotech
    {"XLF", "KRE"},               # financials / regional banks
    {"XLE", "XLB"},               # energy / materials
    {"XLP", "XLU"},               # staples / utilities
]


def _same_family(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return a == b or any(a in f and b in f for f in PROXY_FAMILIES)


@dataclass
class Assignment:
    symbol: str
    theme: str | None
    proxy: str | None
    correlation: float
    runner_up: str | None = None
    runner_up_corr: float = 0.0
    runner_up_proxy: str | None = None
    source: str = "derived"          # derived | curated | none

    @property
    def confident(self) -> bool:
        """A clear winner — or a tie between siblings, which is not a conflict.

        The gap test only applies across FAMILIES. Losing a close race to a
        sibling ETF means the name sits inside that family, which is the answer,
        not a reason to discard it.
        """
        if self.theme is None or self.correlation < MIN_CORRELATION:
            return False
        if _same_family(self.proxy, self.runner_up_proxy):
            return True
        return self.correlation - self.runner_up_corr >= 0.05

    def to_dict(self) -> dict:
        return dict(self.__dict__) | {"confident": self.confident}


def _returns(symbol: str, window: int = WINDOW) -> list[float]:
    p = PRICES_DIR / f"{symbol.upper()}.csv"
    if not p.exists():
        return []
    closes: list[float] = []
    with p.open("r", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                closes.append(float(row["close"]))
            except (KeyError, ValueError):
                continue
    closes = closes[-(window + 1):]
    return [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1]]


def _corr(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n < MIN_BARS:
        return 0.0
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    return num / (da * db) if da and db else 0.0


def _curated() -> dict[str, str]:
    try:
        from ..intel.themes import THEMES
        out: dict[str, str] = {}
        for theme, members in THEMES.items():
            for m in members:
                out.setdefault(m.upper(), theme)
        return out
    except Exception:
        return {}


def assign(symbols: list[str] | None = None) -> list[Assignment]:
    curated = _curated()
    proxies = {etf: _returns(etf) for etf in sorted(set(THEME_PROXY.values()))}
    proxies = {k: v for k, v in proxies.items() if len(v) >= MIN_BARS}

    universe = symbols or sorted(
        p.stem.upper() for p in PRICES_DIR.glob("*.csv")) if PRICES_DIR.exists() else []
    out: list[Assignment] = []
    for sym in universe:
        if sym in proxies or sym == "SPY":
            continue                      # a proxy is not a member of itself
        if sym in curated:
            # Curation encodes knowledge price cannot see. It wins.
            out.append(Assignment(sym, curated[sym], None, 1.0, source="curated"))
            continue
        r = _returns(sym)
        if len(r) < MIN_BARS:
            out.append(Assignment(sym, None, None, 0.0, source="none"))
            continue
        scored = sorted(((_corr(r, pr), etf) for etf, pr in proxies.items()), reverse=True)
        best_c, best_etf = scored[0]
        run_c, run_etf = (scored[1] if len(scored) > 1 else (0.0, None))
        theme = PROXY_TO_THEME.get(best_etf) if best_c >= MIN_CORRELATION else None
        out.append(Assignment(
            sym, theme, best_etf if theme else None, round(best_c, 3),
            runner_up=PROXY_TO_THEME.get(run_etf) if run_etf else None,
            runner_up_corr=round(run_c, 3), runner_up_proxy=run_etf,
            source="derived" if theme else "none"))
    return out


def build_map(force: bool = False) -> dict[str, str]:
    """symbol -> theme, cached. Correlating 178 names is not a per-request cost."""
    if not force and CACHE_PATH.exists():
        try:
            c = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            age = (date.today() - date.fromisoformat(c.get("as_of", "1970-01-01"))).days
            if age <= CACHE_MAX_AGE_DAYS:
                return c.get("map", {})
        except Exception:
            pass
    rows = assign()
    m = {a.symbol: a.theme for a in rows if a.theme and a.confident}
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(
        {"as_of": date.today().isoformat(), "window": WINDOW,
         "min_correlation": MIN_CORRELATION, "map": m,
         "note": "derived from return correlation to sector ETFs; curated entries win"},
        indent=2), encoding="utf-8")
    return m


def report() -> dict:
    rows = assign()
    derived = [a for a in rows if a.source == "derived" and a.confident]
    curated = [a for a in rows if a.source == "curated"]
    none = [a for a in rows if a.theme is None or not a.confident]
    by_theme: dict[str, int] = {}
    for a in derived + curated:
        by_theme[a.theme] = by_theme.get(a.theme, 0) + 1
    return {
        "universe": len(rows),
        "curated": len(curated),
        "derived": len(derived),
        "still_untagged": len(none),
        "coverage_pct": round(100 * (len(curated) + len(derived)) / len(rows), 1) if rows else 0,
        "by_theme": dict(sorted(by_theme.items(), key=lambda x: -x[1])),
        "note": ("A stock's theme is not what its press release says; it is what "
                 "it trades with. Weak matches stay untagged — 'no theme' is a "
                 "finding, not a gap to fill with the nearest guess."),
    }


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.rebuild:
        m = build_map(force=True)
        print(f"rebuilt -> {CACHE_PATH}  ({len(m)} symbols mapped)")
        return
    if args.json:
        print(json.dumps(report(), indent=2))
        return

    rows = assign()
    if args.symbol:
        a = next((x for x in rows if x.symbol == args.symbol.upper()), None)
        if not a:
            print(f"{args.symbol.upper()}: not in the cached universe")
            return
        print(f"{a.symbol}: {a.theme or 'UNTAGGED'} ({a.source})")
        print(f"  correlation {a.correlation}   runner-up {a.runner_up} {a.runner_up_corr}")
        print(f"  confident: {a.confident}")
        return

    r = report()
    print("=" * 74)
    print("  THEME MAP — membership derived from what a name TRADES WITH")
    print("=" * 74)
    print(f"  universe {r['universe']}   curated {r['curated']}   derived {r['derived']}"
          f"   still untagged {r['still_untagged']}")
    print(f"  coverage {r['coverage_pct']}%   (was 16% hand-curated)\n")
    for theme, n in r["by_theme"].items():
        print(f"    {theme:15} {n:>3}")

    weak = [a for a in rows if a.theme and not a.confident]
    if weak:
        print(f"\n  AMBIGUOUS — a near-tie between two groups, so left untagged ({len(weak)}):")
        for a in weak[:6]:
            print(f"    {a.symbol:6} {a.theme} {a.correlation:.2f} vs "
                  f"{a.runner_up} {a.runner_up_corr:.2f}")
    print(f"\n  {r['note']}")


if __name__ == "__main__":
    _main()
