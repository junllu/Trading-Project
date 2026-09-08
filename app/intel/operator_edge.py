"""Scoring the OPERATOR as a signal source — does the intuition have edge?

Every other signal in this project has been measured. The conviction blend was
measured and found wanting. The macro layer was measured and rebuilt. The one
signal never scored is the one that actually built the book: the operator's own
discretionary selection, 558 buys across 152 names since 2016.

That omission is strange, because it is the highest-prior signal available. It
is also the easiest to fool yourself about — a decade of decisions remembered
selectively will always feel skilful, which is precisely why it needs the same
treatment as everything else.

The test is the one used everywhere else here: not "did it go up" but "did it
beat doing nothing". A pick that returned +40% in a year SPY returned +50% was
a bad pick, however good it felt.

THREE BIASES, NAMED RATHER THAN BURIED

  SURVIVORSHIP   the worst one. Delisted tickers have no price history to fetch,
                 so they drop out — and delisted names are overwhelmingly
                 losers. AMRSQ (Amyris, bankrupt) is in this history and cannot
                 be scored. Every number here is therefore BIASED UPWARD, and
                 the count of unscoreable buys is reported alongside so the size
                 of that bias is visible rather than implied.

  COVERAGE       only names with cached prices are scored. That subset skews
                 large-cap and surviving.

  OVERLAP        buys cluster in time (163 in 2021 alone), so outcomes share
                 market regime. The effective sample is far smaller than the
                 count suggests, and a hit rate computed as if each buy were
                 independent overstates confidence.

None of these are fixable from this data. They are stated so the result is read
as "directional, upward-biased" rather than as a measurement.

WHY THIS MATTERS MORE THAN ANOTHER STRATEGY TWEAK

If the operator's selection beats the benchmark out of sample, it is the best
signal in the system and should be weighted as a first-class source. If it does
not, that is worth knowing before more capital follows it. And if it works only
in certain conditions, that is meta-labeling applied to a human — the most
useful outcome of the three.

    python -m app.intel.operator_edge
    python -m app.intel.operator_edge --horizon 63
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics as st
from dataclasses import dataclass, field

from ..config import ROOT

HISTORY_PATH = ROOT / "data" / "trade_history.json"
PRICES_DIR = ROOT / "data" / "prices"
BENCHMARK = "SPY"

# Trading-day horizons. 21 ~ 1 month, 63 ~ 1 quarter, 252 ~ 1 year.
HORIZONS = (21, 63, 252)


@dataclass
class Scored:
    symbol: str
    date: str
    horizon: int
    entry: float
    exit: float
    bench_entry: float
    bench_exit: float

    @property
    def ret_pct(self) -> float:
        return (self.exit / self.entry - 1) * 100 if self.entry else 0.0

    @property
    def bench_pct(self) -> float:
        return (self.bench_exit / self.bench_entry - 1) * 100 if self.bench_entry else 0.0

    @property
    def excess_pct(self) -> float:
        return self.ret_pct - self.bench_pct

    @property
    def beat_benchmark(self) -> bool:
        return self.excess_pct > 0


@dataclass
class EdgeReport:
    horizon: int
    scored: list[Scored] = field(default_factory=list)
    unscoreable: dict[str, int] = field(default_factory=dict)
    total_buys: int = 0

    @property
    def n(self) -> int:
        return len(self.scored)

    def summary(self) -> dict:
        if not self.scored:
            return {"horizon": self.horizon, "n": 0,
                    "note": "no buys could be scored at this horizon"}
        exc = [s.excess_pct for s in self.scored]
        ret = [s.ret_pct for s in self.scored]
        ben = [s.bench_pct for s in self.scored]
        wins = sum(1 for s in self.scored if s.beat_benchmark)
        return {
            "horizon": self.horizon,
            "n": self.n,
            "total_buys": self.total_buys,
            "coverage_pct": round(100 * self.n / self.total_buys, 1) if self.total_buys else 0,
            "unscoreable": self.unscoreable,
            "hit_rate_vs_benchmark_pct": round(100 * wins / self.n, 1),
            "mean_return_pct": round(st.mean(ret), 2),
            "mean_benchmark_pct": round(st.mean(ben), 2),
            "mean_excess_pct": round(st.mean(exc), 2),
            "median_excess_pct": round(st.median(exc), 2),
            # The mean is dragged by a few huge winners; the median says whether
            # the TYPICAL pick worked. A large gap between them means the record
            # rests on a handful of names, not on repeatable selection.
            "verdict": self._verdict(st.mean(exc), st.median(exc), wins / self.n),
        }

    @staticmethod
    def _verdict(mean_exc: float, median_exc: float, hit: float) -> str:
        if mean_exc > 0 and median_exc > 0 and hit > 0.5:
            return ("EDGE — the typical pick beat the benchmark, not just the average one. "
                    "Upward-biased by survivorship, so treat as directional.")
        if mean_exc > 0 > median_exc:
            return ("CARRIED BY OUTLIERS — positive on average but the MEDIAN pick lost to "
                    "the benchmark. The record rests on a few names, which is a different "
                    "skill from selection and much harder to repeat deliberately.")
        if mean_exc <= 0:
            return ("NO EDGE vs BENCHMARK — and this is the upward-biased estimate. "
                    "Buying the index over the same windows did better.")
        return "MIXED — positive median but a losing average; a few large losers dominate."

    def by_year(self) -> dict:
        buckets: dict[str, list[float]] = {}
        for s in self.scored:
            buckets.setdefault(s.date[:4], []).append(s.excess_pct)
        return {y: {"n": len(v), "mean_excess_pct": round(st.mean(v), 2)}
                for y, v in sorted(buckets.items())}

    def by_era(self, split_year: str = "2022") -> dict:
        """Did the process change help? Tested, not assumed.

        The tempting story is that the early record is meme-era noise and the
        later thesis-driven selection is better. It is a good hypothesis and it
        happens to be false on this data — the later era is slightly WORSE on
        mean excess. Kept here so the claim stays checkable rather than becoming
        a narrative that survives because nobody re-ran it.
        """
        out: dict[str, dict] = {}
        for label, pred in (("pre_" + split_year, lambda d: d[:4] < split_year),
                            (split_year + "_onward", lambda d: d[:4] >= split_year)):
            v = [s.excess_pct for s in self.scored if pred(s.date)]
            if not v:
                continue
            out[label] = {"n": len(v), "mean_excess_pct": round(st.mean(v), 2),
                          "median_excess_pct": round(st.median(v), 2),
                          "beat_benchmark_pct": round(100 * sum(1 for x in v if x > 0) / len(v), 1)}
        return out

    def best_and_worst(self, k: int = 5) -> dict:
        rows = sorted(self.scored, key=lambda s: -s.excess_pct)
        fmt = lambda s: {"symbol": s.symbol, "date": s.date,
                         "excess_pct": round(s.excess_pct, 1)}
        return {"best": [fmt(s) for s in rows[:k]],
                "worst": [fmt(s) for s in rows[-k:]]}


def _series(symbol: str) -> list[tuple[str, float]]:
    p = PRICES_DIR / f"{symbol.upper()}.csv"
    if not p.exists():
        return []
    out = []
    with p.open("r", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                out.append((row["date"], float(row["close"])))
            except (KeyError, ValueError):
                continue
    return out


def _at_or_after(series: list[tuple[str, float]], date: str) -> int | None:
    for i, (d, _) in enumerate(series):
        if d >= date:
            return i
    return None


def score(horizon: int = 63) -> EdgeReport:
    rep = EdgeReport(horizon=horizon)
    if not HISTORY_PATH.exists():
        return rep

    orders = json.loads(HISTORY_PATH.read_text(encoding="utf-8")).get("orders", [])
    buys = [o for o in orders if o.get("side") == "buy"]
    rep.total_buys = len(buys)

    bench = _series(BENCHMARK)
    if not bench:
        rep.unscoreable["no benchmark prices"] = len(buys)
        return rep

    cache: dict[str, list[tuple[str, float]]] = {}
    for o in buys:
        sym, date = str(o["symbol"]).upper(), str(o["date"])
        if sym not in cache:
            cache[sym] = _series(sym)
        s = cache[sym]
        if not s:
            # Overwhelmingly delisted names. Counted, never silently dropped.
            rep.unscoreable["no price history (often delisted)"] = \
                rep.unscoreable.get("no price history (often delisted)", 0) + 1
            continue

        i = _at_or_after(s, date)
        j = _at_or_after(bench, date)
        if i is None or j is None or i + horizon >= len(s) or j + horizon >= len(bench):
            rep.unscoreable["horizon extends past available data"] = \
                rep.unscoreable.get("horizon extends past available data", 0) + 1
            continue

        rep.scored.append(Scored(sym, date, horizon, s[i][1], s[i + horizon][1],
                                 bench[j][1], bench[j + horizon][1]))
    return rep


def report() -> dict:
    """Agent entrypoint — the operator's edge at the medium horizon."""
    return score(63).summary()


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    horizons = [args.horizon] if args.horizon else list(HORIZONS)
    if args.json:
        print(json.dumps({h: score(h).summary() for h in horizons}, indent=2))
        return

    print("=" * 78)
    print("  OPERATOR EDGE — does the discretionary selection beat the benchmark?")
    print("=" * 78)
    print(f"  every buy since 2016, scored against {BENCHMARK} over the same window\n")

    for h in horizons:
        rep = score(h)
        s = rep.summary()
        if not s.get("n"):
            print(f"  {h}d — nothing scoreable")
            continue
        print(f"  {'-' * 74}")
        print(f"  HORIZON {h} trading days   ·   {s['n']} of {s['total_buys']} buys scored "
              f"({s['coverage_pct']}%)")
        print(f"      picks      {s['mean_return_pct']:+.2f}%   "
              f"benchmark {s['mean_benchmark_pct']:+.2f}%")
        print(f"      EXCESS     mean {s['mean_excess_pct']:+.2f}%   "
              f"median {s['median_excess_pct']:+.2f}%")
        print(f"      beat {BENCHMARK}   {s['hit_rate_vs_benchmark_pct']}% of the time")
        print(f"      -> {s['verdict']}")

    rep = score(63)
    print(f"\n  {'=' * 74}")
    print("  EXCESS RETURN BY YEAR (63-day horizon)")
    for y, v in rep.by_year().items():
        bar = "+" if v["mean_excess_pct"] > 0 else "-"
        print(f"    {y}   n={v['n']:>3}   {v['mean_excess_pct']:>+8.2f}%  {bar * min(abs(int(v['mean_excess_pct'])), 40)}")

    print(f"\n  {'=' * 74}")
    print("  DID THE PROCESS CHANGE HELP?  (63-day horizon)")
    for label, v in rep.by_era().items():
        print(f"    {label:16} n={v['n']:>3}   mean {v['mean_excess_pct']:>+7.2f}%   "
              f"median {v['median_excess_pct']:>+7.2f}%   beat {BENCHMARK} {v['beat_benchmark_pct']:.0f}%")

    bw = rep.best_and_worst()
    print("\n  BEST                                WORST")
    for b, w in zip(bw["best"], bw["worst"][::-1]):
        print(f"    {b['symbol']:6} {b['date']} {b['excess_pct']:>+8.1f}%"
              f"        {w['symbol']:6} {w['date']} {w['excess_pct']:>+8.1f}%")

    print(f"\n  {'=' * 74}")
    print("  UNSCOREABLE — and why this makes every number above too GENEROUS")
    for k, v in rep.unscoreable.items():
        print(f"    {v:>4}  {k}")
    print("\n    Delisted tickers cannot be priced, and delisted tickers are")
    print("    overwhelmingly losers. Nothing here corrects for that.")


if __name__ == "__main__":
    _main()
