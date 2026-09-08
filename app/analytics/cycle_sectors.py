"""Which names lead in which presidential-cycle year — measured, not asserted.

app/macro/signals.py computes the cycle year. app/analytics/sectors.py measures
sector rotation. Nothing joined them, so the rotation map could report "XLE is
leading" without ever connecting that to where the political calendar sits.

THE TRAP THIS IS BUILT TO AVOID

The tempting version writes down what everyone knows: defence does well under
Republicans, energy under deregulation, crypto when the SEC softens. Every one
of those is a hindsight narrative, and this project already deleted a module for
exactly that sin — app/macro/timeline.py hard-coded "ai_boom 2023-2027:
semiconductors +0.6", a bet placed after the race. Its apparent edge evaporated
out of sample.

So nothing here is authored. Every figure is a measured annual return bucketed
by cycle year, with its sample size attached.

WHY PER-STOCK AND NOT SECTOR ETFs

The obvious source is the sector ETFs, but their history starts ~2015 — three
observations per cycle-year bucket, which is arithmetic rather than evidence.
data/cycle_raw.json carries per-NAME annual returns going back to 1997 for the
older names, giving 6-8 observations per bucket. Fewer symbols, far more signal.
Coverage is the book's own names, which is the right scope: the question is not
"how do sectors behave" but "how have THESE names behaved at this point in the
cycle".

WHAT IT IS FOR

Noticing DISAGREEMENT. When a name is doing the opposite of what this cycle year
usually brings, that is a question worth asking — either the pattern does not
apply this time, or the reversion has not arrived yet. Agreement, by contrast,
is not evidence of anything: a coin that lands as expected has told you nothing.

    python -m app.analytics.cycle_sectors
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from dataclasses import dataclass, field
from datetime import date

from ..config import ROOT

CYCLE_PATH = ROOT / "data" / "cycle_raw.json"
BENCHMARK_KEY = "^GSPC"

# Below this, a bucket median is description rather than evidence. Stated as a
# constant so the threshold is visible rather than buried in prose.
MIN_OBSERVATIONS = 5

CYCLE_LABELS = {1: "Yr1 post-election", 2: "Yr2 midterm",
                3: "Yr3 pre-election", 4: "Yr4 election"}

# Grouping for readability only. It carries no weight in any calculation — a
# label cannot change a measured return.
GROUPS = {
    "MU": "memory", "WDC": "memory", "STX": "memory", "SNDK": "memory",
    "NVDA": "semis/AI", "MRVL": "semis/AI", "ANET": "semis/AI",
    "AEIS": "semis/AI", "ALAB": "semis/AI",
    "NOW": "software", "RDDT": "software", "HOOD": "fintech",
    "COP": "energy", "CVX": "energy", "SHEL": "energy", "BE": "power",
}


@dataclass
class NameCycle:
    symbol: str
    group: str
    n_years: int
    first_year: int
    by_cycle: dict[int, list[float]] = field(default_factory=dict)
    this_year: float | None = None

    def median_for(self, cy: int) -> float | None:
        v = self.by_cycle.get(cy)
        return round(st.median(v), 1) if v else None

    def n_for(self, cy: int) -> int:
        return len(self.by_cycle.get(cy, []))

    def divergence(self, cy: int) -> float | None:
        """This year's actual, minus what this cycle year usually delivers."""
        m = self.median_for(cy)
        if m is None or self.this_year is None:
            return None
        return round(self.this_year - m, 1)

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "group": self.group, "n_years": self.n_years,
                "medians": {k: self.median_for(k) for k in (1, 2, 3, 4)},
                "n_per_bucket": {k: self.n_for(k) for k in (1, 2, 3, 4)},
                "this_year": self.this_year}


def cycle_year(year: int) -> int:
    """1-4 within the US presidential cycle. Matches app/macro/signals.py."""
    return ((year - 2025) % 4) + 1


def build() -> tuple[list[NameCycle], int]:
    if not CYCLE_PATH.exists():
        return [], 0
    raw = json.loads(CYCLE_PATH.read_text(encoding="utf-8"))
    this_year = date.today().year
    rows: list[NameCycle] = []
    for sym, e in raw.items():
        nc = NameCycle(symbol=("S&P 500" if sym == BENCHMARK_KEY else sym),
                       group=("benchmark" if sym == BENCHMARK_KEY
                              else GROUPS.get(sym, "other")),
                       n_years=e.get("n_years", 0), first_year=e.get("first_year", 0))
        for y, ret in e.get("by_year", {}).items():
            y = int(y)
            if y == this_year:
                # The current year is INCOMPLETE and must not enter a median it
                # is about to be compared against — that would let the outcome
                # define the expectation it is measured by.
                nc.this_year = ret
                continue
            nc.by_cycle.setdefault(cycle_year(y), []).append(ret)
        rows.append(nc)
    return rows, cycle_year(this_year)


def report() -> dict:
    rows, cy = build()
    if not rows:
        return {"error": "no data/cycle_raw.json"}

    scored = [r for r in rows if r.median_for(cy) is not None and r.group != "benchmark"]
    diverging = sorted(
        (r for r in scored if r.divergence(cy) is not None),
        key=lambda r: -abs(r.divergence(cy) or 0))
    n = max((r.n_for(cy) for r in scored), default=0)
    bench = next((r for r in rows if r.group == "benchmark"), None)

    return {
        "current_cycle_year": cy,
        "label": CYCLE_LABELS[cy],
        "observations_per_bucket": n,
        "sufficient": n >= MIN_OBSERVATIONS,
        "benchmark_median_this_cycle_year": bench.median_for(cy) if bench else None,
        "historically_strong": [r.symbol for r in
                                sorted(scored, key=lambda r: -(r.median_for(cy) or 0))[:3]],
        "historically_weak": [r.symbol for r in
                              sorted(scored, key=lambda r: (r.median_for(cy) or 0))[:3]],
        "biggest_divergence": [{"symbol": r.symbol, "median": r.median_for(cy),
                                "actual": r.this_year, "gap": r.divergence(cy)}
                               for r in diverging[:5]],
        "names": [r.to_dict() for r in rows],
        "verdict": (f"n={n} per bucket — enough to describe, not to forecast."
                    if n >= MIN_OBSERVATIONS else
                    f"n={n} per bucket, below {MIN_OBSERVATIONS}. Context only."),
    }


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.json:
        print(json.dumps(report(), indent=2))
        return

    rows, cy = build()
    if not rows:
        print("no data/cycle_raw.json — run the cycle analysis first")
        return
    r = report()

    print("=" * 88)
    print(f"  ANNUAL RETURN BY PRESIDENTIAL-CYCLE YEAR — median of past cycles")
    print("=" * 88)
    print(f"  now: cycle year {cy} — {CYCLE_LABELS[cy]}   ·   "
          f"{r['observations_per_bucket']} observations per bucket\n")
    print(f"  {'symbol':9} {'group':11} {'hist':>5}", end="")
    for k in (1, 2, 3, 4):
        print(f"{'Yr' + str(k):>9}", end="")
    print(f"{date.today().year:>10}{'gap':>9}")
    print("  " + "-" * 84)

    order = {"benchmark": 0, "memory": 1, "semis/AI": 2, "software": 3,
             "fintech": 4, "energy": 5, "power": 6, "other": 7}
    for nc in sorted(rows, key=lambda x: (order.get(x.group, 9), x.symbol)):
        print(f"  {nc.symbol:9} {nc.group:11} {nc.n_years:>4}y", end="")
        for k in (1, 2, 3, 4):
            m = nc.median_for(k)
            cell = f"{m:+.1f}" if m is not None else "—"
            print(f"{cell + ('*' if k == cy else ' '):>9}", end="")
        act = f"{nc.this_year:+.1f}" if nc.this_year is not None else "—"
        gap = nc.divergence(cy)
        gaps = f"{gap:+.0f}" if gap is not None else "—"
        print(f"{act:>10}{gaps:>9}")

    print(f"\n  * = the current cycle year.  gap = this year MINUS the Yr{cy} median.")
    print(f"\n  {'=' * 84}")
    b = r["benchmark_median_this_cycle_year"]
    if b is not None:
        print(f"  S&P 500 median in Yr{cy}: {b:+.1f}%")
    print(f"  historically strongest in Yr{cy}: {', '.join(r['historically_strong'])}")
    print(f"  historically weakest   in Yr{cy}: {', '.join(r['historically_weak'])}")

    print(f"\n  LARGEST DIVERGENCES FROM THE Yr{cy} PATTERN")
    for d in r["biggest_divergence"]:
        print(f"    {d['symbol']:6} usually {d['median']:+7.1f}%   "
              f"this year {d['actual']:+8.1f}%   gap {d['gap']:+8.0f}pp")
    print("\n    Divergence is a question, not a signal. Either the pattern does")
    print("    not apply this cycle, or the reversion has not arrived yet — and")
    print("    nothing in this table can tell you which.")
    print(f"\n  {r['verdict']}")


if __name__ == "__main__":
    _main()
