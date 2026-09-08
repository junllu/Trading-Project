"""Theme rotation — mechanising the operator's actual exit rule.

Interviewing the operator produced a sharper description of the process than any
per-name analysis had: positions are not sold on a price target, they are sold
when the THEME rotates out. "Momentum has shifted away from crypto, solar, weed
legalization, EV." That is a sector-level judgement, and it is the only place in
the record where an edge is visible.

Tested against the trade history, the rule is real and INCONSISTENTLY APPLIED:

    EV      $6,506 deployed -> $26,961 realised (4.1x). Rotation caught.
    crypto  $1,726 -> $3,803 (2.2x). Caught.
    weed    $13,139 -> $10,564 (0.8x). MISSED — buying continued until
            2024-05, more than three years past the Feb-2021 peak.

Same operator, same stated rule, opposite outcomes. The gut caught one rotation
and slept through another that ran for three years. That asymmetry is the whole
argument for this module: a rule applied only when you happen to notice is not a
rule, and the cases you miss are exactly the ones where you are most invested in
not noticing.

WHAT THIS MEASURES

Theme strength, as an equal-weighted basket of its members, against the
benchmark. Two things matter and they are different:

    TREND       is the basket above its own long average? (is the theme alive)
    RELATIVE    is it beating SPY over the medium term? (is it still the place
                to be, or merely rising with everything else)

A theme rising slower than the index is rotating out even while it goes up,
which is precisely the case a price-based gut check misses — nothing looks
wrong, you are still making money, and the opportunity cost is invisible.

WHAT IT DELIBERATELY DOES NOT DO

It does not fire an order or a verdict on a holding. Theme state is EVIDENCE
handed to the thesis ledger, which grades. A rotation signal is a reason to
re-examine a thesis, never a reason on its own to sell — the thesis may still be
intact for name-specific reasons, which is what the checkpoints are for.

    python -m app.intel.themes
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics as st
from dataclasses import dataclass, field

from ..config import ROOT

PRICES_DIR = ROOT / "data" / "prices"
BENCHMARK = "SPY"

TREND_WINDOW = 200          # bars; "is the theme alive at all"
RELATIVE_WINDOW = 126       # ~6 months; "is it still the place to be"
ROTATING_OUT_PCT = -5.0     # underperforming the benchmark by this much

# Membership is coarse on purpose. A theme is a story people rotate into and out
# of, not a GICS classification, and the operator's own language ("crypto,
# solar, weed legalization, EV") is the right granularity.
#
# MEMBERSHIP IS A JUDGEMENT AND IT CHANGES THE ANSWER. BE is the live example:
# as a clean-energy name it sits with ENPH/RUN/PLUG, which currently reads
# FADING; as AI datacenter power it sits with VRT and AEIS, which reads ALIVE.
# The company is the same either way and the verdict flips. So names may belong
# to MORE THAN ONE theme, and a name is only treated as exposed to rotation when
# EVERY theme it belongs to is rotating — otherwise a debatable label would
# quietly become a sell signal.
THEMES: dict[str, list[str]] = {
    "ai_infra": ["NVDA", "MRVL", "ALAB", "ANET", "AEIS", "VRT", "AVGO", "SMCI", "ARM", "BE"],
    "memory": ["MU", "SNDK", "WDC", "STX"],
    "ev": ["TSLA", "NIO", "LCID", "XPEV", "LI", "RIVN"],
    "crypto": ["COIN", "MARA", "RIOT", "MSTR", "HOOD", "BULL"],
    "solar_clean": ["ENPH", "RUN", "FSLR", "PLUG", "BE"],
    "weed": ["TLRY", "ACB", "CGC", "CRON", "GRWG"],
    "meme": ["AMC", "GME", "PLBY", "CLOV"],
    "energy": ["CVX", "COP", "SHEL", "ET"],
    "defense": ["NOC", "LMT", "RTX", "AVAV"],
}

ALIVE, FADING, ROTATING_OUT, DEAD, NO_DATA = (
    "ALIVE", "FADING", "ROTATING_OUT", "DEAD", "NO_DATA")


@dataclass
class ThemeState:
    name: str
    members_with_data: list[str] = field(default_factory=list)
    members_missing: list[str] = field(default_factory=list)
    above_trend_pct: float = 0.0      # share of members above their 200d average
    relative_6m_pct: float = 0.0      # basket vs benchmark
    basket_6m_pct: float = 0.0
    bench_6m_pct: float = 0.0
    exposure_usd: float = 0.0
    state: str = NO_DATA

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def members_of(theme: str) -> list[str]:
    """Curated members PLUS anything the derived map assigns to this theme.

    The hand-written list covered 16% of the cached universe, so the operator's
    theme edge was unavailable for the rest. app/analytics/theme_map.py fills
    the gap by correlation to sector ETFs; curation still wins where it exists.
    Failure here falls back to the curated list rather than raising — a missing
    derived map should degrade coverage, never break the theme read.
    """
    base = list(THEMES.get(theme, []))
    try:
        from ..analytics.theme_map import build_map
        derived = [s for s, t in build_map().items() if t == theme]
    except Exception:
        derived = []
    return sorted(set(base) | set(derived))


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


def _pct_over(closes: list[float], bars: int) -> float | None:
    if len(closes) <= bars:
        return None
    return (closes[-1] / closes[-bars - 1] - 1) * 100


def _exposure() -> dict[str, float]:
    try:
        from ..portfolio.holdings import load_holdings
        out: dict[str, float] = {}
        for h in load_holdings():
            s = str(h["symbol"]).upper()
            last = float(h.get("last", 0)) or float(h.get("avg_price", 0))
            out[s] = out.get(s, 0.0) + float(h["shares"]) * last
        return out
    except Exception:
        return {}


def assess(name: str, members: list[str], bench_6m: float | None,
           exposure: dict[str, float]) -> ThemeState:
    ts = ThemeState(name=name)
    rels, above = [], 0
    for m in members:
        c = _closes(m)
        if len(c) < TREND_WINDOW + 1:
            ts.members_missing.append(m)
            continue
        ts.members_with_data.append(m)
        if c[-1] > st.mean(c[-TREND_WINDOW:]):
            above += 1
        r = _pct_over(c, RELATIVE_WINDOW)
        if r is not None:
            rels.append(r)
        ts.exposure_usd += exposure.get(m, 0.0)

    if not ts.members_with_data or bench_6m is None:
        return ts

    ts.above_trend_pct = round(100 * above / len(ts.members_with_data), 1)
    # Equal-weighted so one mega-cap cannot carry a theme that is otherwise
    # broken — the question is whether the STORY is working, not whether NVDA is.
    ts.basket_6m_pct = round(st.mean(rels), 1) if rels else 0.0
    ts.bench_6m_pct = round(bench_6m, 1)
    ts.relative_6m_pct = round(ts.basket_6m_pct - ts.bench_6m_pct, 1)

    if ts.above_trend_pct < 25:
        ts.state = DEAD
    elif ts.relative_6m_pct <= ROTATING_OUT_PCT:
        # The case a price check misses: still up, but no longer the place to be.
        ts.state = ROTATING_OUT
    elif ts.relative_6m_pct < 0 or ts.above_trend_pct < 60:
        ts.state = FADING
    else:
        ts.state = ALIVE
    return ts


def report() -> dict:
    """Agent entrypoint — theme state and where the book is exposed."""
    bench = _pct_over(_closes(BENCHMARK), RELATIVE_WINDOW)
    exp = _exposure()
    states = [assess(n, members_of(n), bench, exp) for n in THEMES]

    # A name is only "exposed to rotation" when EVERY theme it belongs to is
    # rotating. BE in a fading clean-energy basket while its AI-power basket is
    # alive is not a rotation signal — it is an unresolved label, and treating
    # it as a signal would turn my classification guess into your sell decision.
    healthy = {m for s in states if s.state in (ALIVE, FADING)
               for m in s.members_with_data}
    at_risk = []
    for s in states:
        if s.state not in (ROTATING_OUT, DEAD):
            continue
        stranded = sum(exp.get(m, 0.0) for m in s.members_with_data if m not in healthy)
        if stranded > 0:
            s.exposure_usd = stranded
            at_risk.append(s)
    return {
        "benchmark_6m_pct": round(bench, 1) if bench is not None else None,
        "themes": {s.name: s.to_dict() for s in states},
        "exposed_to_rotating_themes": {s.name: round(s.exposure_usd) for s in at_risk},
        "note": ("Theme state is EVIDENCE for the thesis ledger, never an order. A "
                 "rotation is a reason to re-examine a thesis, not to sell on its own."),
    }


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    r = report()
    if args.json:
        print(json.dumps(r, indent=2))
        return

    print("=" * 78)
    print("  THEME ROTATION — the operator's exit rule, applied consistently")
    print("=" * 78)
    print(f"  {BENCHMARK} over ~6m: {r['benchmark_6m_pct']}%\n")
    print(f"  {'theme':13} {'state':13} {'6m':>8} {'vs SPY':>8} {'above trend':>12} {'exposure':>11}")
    print("  " + "-" * 72)
    order = {ALIVE: 0, FADING: 1, ROTATING_OUT: 2, DEAD: 3, NO_DATA: 4}
    for s in sorted(r["themes"].values(), key=lambda x: (order[x["state"]], -x["exposure_usd"])):
        if s["state"] == NO_DATA:
            continue
        print(f"  {s['name']:13} {s['state']:13} {s['basket_6m_pct']:>7.1f}% "
              f"{s['relative_6m_pct']:>+7.1f}% {s['above_trend_pct']:>11.0f}% "
              f"${s['exposure_usd']:>10,.0f}")

    missing = {s["name"]: s["members_missing"] for s in r["themes"].values()
               if s["members_missing"]}
    if missing:
        print("\n  no price history (theme measured on the remainder):")
        for k, v in missing.items():
            print(f"    {k:13} {', '.join(v)}")

    if r["exposed_to_rotating_themes"]:
        print(f"\n  {'=' * 72}")
        print("  EXPOSED TO A ROTATING OR DEAD THEME")
        for k, v in r["exposed_to_rotating_themes"].items():
            print(f"    {k:13} ${v:,}")
        print("\n  This is the weed case, caught early. In 2021-2024 the same")
        print("  condition ran for three years while buying continued.")
    print(f"\n  {r['note']}")


if __name__ == "__main__":
    _main()
