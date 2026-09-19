"""Did the decisions the simulator actually took beat doing nothing?

THE JOIN THIS CLOSES

Two records existed and never met. `data/paper_blotter.jsonl` holds what the
simulator DID — fills, prices, the sleeve that asked. `confidence.py` grades what
the live loop SAID it would do, from `forward_record.jsonl`. So executed
decisions were recorded in detail and never scored, while scored predictions
were never executed. This grades the executed ones, on the same terms.

THE QUESTION THIS ANSWERS

This stack swing-trades and day-trades: capital is deployed in bursts and
returns to cash between decisions. So "what if I had held the index the whole
time" is not the alternative actually faced, and it is not the bar. The bar is
EXPECTANCY — the average outcome of taking the next signal:

    expectancy = win_rate x avg_win  -  (1 - win_rate) x avg_loss

Win rate alone is not enough and is actively misleading in both directions. A
40% win rate is excellent if winners are twice the size of losers; an 80% win
rate is ruinous if one loss erases twenty wins. So the payoff ratio, the profit
factor and the worst losing streak are reported alongside it — expectancy is an
average, and an average says nothing about whether the drawdown on the way to it
is survivable.

Each decision is scored against not having taken it:

    SELL   the counterfactual is still holding. Selling was right exactly when
           the price fell afterwards, so the edge is the fall it avoided.
    BUY    the counterfactual is staying in cash. Buying was right when the
           price rose afterwards.

THE DRIFT CONTROL, AND WHY IT IS SECONDARY

A long-only rule in a rising tape wins most of the time on beta alone. So the
index move over the SAME holding window is reported as a control, not as the
target: negative drift with positive expectancy means the edge belongs to the
market rather than to the rule. It answers "is this skill or tape", never
"should I have bought SPY instead".

TWO RULES THAT KEEP IT HONEST

  1. A decision is scored only once its horizon has fully elapsed. Partial
     outcomes are excluded, not annualised — peeking at a half-resolved trade to
     make the number look better is the failure this whole record exists to
     prevent.
  2. BACKLOG fills are excluded by default. Those were rungs crossed before the
     monitor existed; they are catch-up, not decisions the strategy made, and
     counting them would credit the strategy with a position's entire prior
     run-up.

Attribution is per sleeve, because that is the comparison that matters: entries
(`daily_agent`) and exits (`exit_monitor`) have opposite evidence behind them and
averaging them together hides both.

    python -m app.analytics.decision_score
    python -m app.analytics.decision_score --horizon 10 --json
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics as st
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..config import ROOT

PRICES_DIR = ROOT / "data" / "prices"
DEFAULT_HORIZON = 5

# The alternative every decision competes with. The goal is a better STRATEGY,
# not better name selection, so the bar is the index — not the same ticker held.
# A rule that beats the name it traded and loses to SPY has not earned capital.
DEFAULT_BENCHMARK = "SPY"

# Fills recorded before this moment were priced under a broken market feed and
# are NOT scoreable. Three regimes ran on 2026-09-08: the paper broker quoting
# itself from a 49-hour-old seed (prices never moved, so every round trip netted
# exactly zero), then a fallback to the previous daily close (wrong by 9.6% on
# BE and 4.1% on HOOD), and only then real-time bars. Scoring across that mix
# would measure the repair work rather than the strategy.
#
# Quarantined rather than deleted: the rows are the evidence that the bugs were
# real, and the count of excluded fills is reported so the gap is visible.
VALID_PRICING_FROM = "2026-09-08 23:00:00"


@dataclass
class ScoredDecision:
    symbol: str
    side: str
    strategy: str
    when: str
    fill_price: float
    later_price: float
    horizon_sessions: int
    forward_pct: float          # market move from fill to horizon
    edge_pct: float             # signed for the decision: + means it beat doing nothing
    backlog: bool = False
    # The index over the same window, and the decision's edge against it. The
    # question "did this beat holding the name" and "did this beat holding the
    # index" have different answers, and only the second one tells you whether a
    # strategy is worth running instead of buying QQQ.
    benchmark_pct: float | None = None
    vs_benchmark_pct: float | None = None

    @property
    def right(self) -> bool:
        return self.edge_pct > 0

    @property
    def beat_index(self) -> bool | None:
        return None if self.vs_benchmark_pct is None else self.vs_benchmark_pct > 0


@dataclass
class ScoreReport:
    horizon_sessions: int
    benchmark: str = ""
    benchmark_available: bool = True
    scored: list[ScoredDecision] = field(default_factory=list)
    unresolved: int = 0
    skipped_backlog: int = 0
    skipped_bad_pricing: int = 0
    no_price_data: list[str] = field(default_factory=list)

    def _summary(self, rows: list[ScoredDecision]) -> dict[str, Any]:
        if not rows:
            return {"n": 0, "note": "nothing has resolved yet"}
        edges = [r.edge_pct for r in rows]
        wins = [e for e in edges if e > 0]
        losses = [e for e in edges if e < 0]
        avg_win = st.fmean(wins) if wins else 0.0
        avg_loss = abs(st.fmean(losses)) if losses else 0.0
        win_rate = len(wins) / len(edges)

        # EXPECTANCY is the headline for a swing/day-trading stack: the average
        # outcome of taking the next signal. A 40% win rate is fine if winners
        # are twice the size of losers, and an 80% win rate is ruinous if the
        # rare loss erases twenty wins. Win rate alone hides both cases.
        expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss
        gross_win, gross_loss = sum(wins), abs(sum(losses))

        # Longest losing streak: expectancy is an average, and an average says
        # nothing about whether the drawdown on the way to it is survivable.
        streak = worst_streak = 0
        for e in edges:
            streak = streak + 1 if e <= 0 else 0
            worst_streak = max(worst_streak, streak)

        out = {
            "n": len(rows),
            "win_rate_pct": round(win_rate * 100, 1),
            "avg_win_pct": round(avg_win, 3),
            "avg_loss_pct": round(avg_loss, 3),
            "payoff_ratio": round(avg_win / avg_loss, 2) if avg_loss > 0 else None,
            "expectancy_pct": round(expectancy, 3),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
            "worst_losing_streak": worst_streak,
            "total_edge_pct": round(sum(edges), 2),
            # kept, but secondary — see the drift note below
            "median_edge_pct": round(st.median(edges), 3),
        }
        vs = [r.vs_benchmark_pct for r in rows if r.vs_benchmark_pct is not None]
        if vs:
            # NOT the bar for a trading stack. This is a DRIFT CONTROL: a
            # long-only rule in a rising tape wins most of the time on beta
            # alone, so this asks whether the decisions did better than the
            # market's move over the same holding window. Negative here with
            # positive expectancy means the edge is the market, not the rule.
            out["drift_control"] = {
                "n": len(vs),
                "beat_market_move_pct": round(sum(1 for v in vs if v > 0) / len(vs) * 100, 1),
                "mean_pct": round(st.fmean(vs), 3),
            }
        return out

    def to_dict(self) -> dict[str, Any]:
        by_strategy: dict[str, list[ScoredDecision]] = defaultdict(list)
        by_side: dict[str, list[ScoredDecision]] = defaultdict(list)
        for r in self.scored:
            by_strategy[r.strategy].append(r)
            by_side[r.side].append(r)
        return {
            "agent": "decision_score",
            "horizon_sessions": self.horizon_sessions,
            "benchmark": self.benchmark,
            "benchmark_available": self.benchmark_available,
            "overall": self._summary(self.scored),
            "by_strategy": {k: self._summary(v) for k, v in sorted(by_strategy.items())},
            "by_side": {k: self._summary(v) for k, v in sorted(by_side.items())},
            "unresolved": self.unresolved,
            "skipped_backlog": self.skipped_backlog,
            "skipped_bad_pricing": self.skipped_bad_pricing,
            "valid_pricing_from": VALID_PRICING_FROM,
            "no_price_data": sorted(set(self.no_price_data)),
            "benchmark": ("doing nothing — a SELL is scored on the fall it avoided, "
                          "a BUY on the rise it captured"),
            "does_not": [
                "score a decision whose horizon has not fully elapsed",
                "count BACKLOG fills as decisions the strategy made",
                "rule on whether a sleeve is worth running — that needs "
                "deflation across every variant tried, not one sample",
            ],
        }


def _closes(symbol: str) -> list[tuple[str, float]]:
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


def score(horizon_sessions: int = DEFAULT_HORIZON,
          include_backlog: bool = False,
          benchmark: str = DEFAULT_BENCHMARK) -> ScoreReport:
    from ..engine import blotter

    rep = ScoreReport(horizon_sessions=horizon_sessions, benchmark=benchmark)
    series: dict[str, list[tuple[str, float]]] = {}
    bench = _closes(benchmark)
    if not bench:
        rep.benchmark_available = False

    for row in blotter.rows():
        if row.get("status") != "filled":
            continue
        px = row.get("filled_price")
        sym, side = row.get("symbol"), row.get("side")
        if not sym or not side or not isinstance(px, (int, float)) or px <= 0:
            continue

        if str(row.get("when") or "") < VALID_PRICING_FROM:
            rep.skipped_bad_pricing += 1
            continue

        is_backlog = "BACKLOG" in (row.get("reason") or "")
        if is_backlog and not include_backlog:
            rep.skipped_backlog += 1
            continue

        if sym not in series:
            series[sym] = _closes(sym)
        closes = series[sym]
        if not closes:
            rep.no_price_data.append(sym)
            continue

        # Locate the fill in the daily series, then look `horizon` sessions on.
        try:
            fill_day = datetime.fromtimestamp(
                float(row.get("ts") or 0), tz=timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, OSError):
            continue
        idx = next((i for i, (d, _) in enumerate(closes) if d >= fill_day), None)
        if idx is None:
            rep.unresolved += 1
            continue
        target = idx + horizon_sessions
        if target >= len(closes):
            # Not enough sessions have passed. Excluded, never extrapolated.
            rep.unresolved += 1
            continue

        later = closes[target][1]
        fwd = (later - px) / px * 100.0
        # A sell is right when the price falls; a buy when it rises.
        edge = -fwd if side == "sell" else fwd

        # The index over the SAME calendar window. Capital deployed into this
        # decision could have sat in the index instead, and that is the
        # alternative a strategy has to beat to be worth running at all.
        bench_pct = vs_bench = None
        if bench:
            bi = next((i for i, (d, _) in enumerate(bench) if d >= fill_day), None)
            if bi is not None and bi + horizon_sessions < len(bench):
                b0 = bench[bi][1]
                b1 = bench[bi + horizon_sessions][1]
                if b0 > 0:
                    bench_pct = (b1 - b0) / b0 * 100.0
                    # A BUY competes with owning the index. A SELL frees capital,
                    # so its alternative is the index rising without you.
                    vs_bench = (fwd - bench_pct) if side == "buy" else (bench_pct - fwd)

        rep.scored.append(ScoredDecision(
            symbol=sym, side=side, strategy=row.get("strategy") or "unknown",
            when=row.get("when") or "", fill_price=round(float(px), 4),
            later_price=round(later, 4), horizon_sessions=horizon_sessions,
            forward_pct=round(fwd, 3), edge_pct=round(edge, 3), backlog=is_backlog,
            benchmark_pct=round(bench_pct, 3) if bench_pct is not None else None,
            vs_benchmark_pct=round(vs_bench, 3) if vs_bench is not None else None))
    return rep


def report() -> dict[str, Any]:
    """Roster entrypoint."""
    return score().to_dict()


def _main() -> None:                              # pragma: no cover - CLI
    ap = argparse.ArgumentParser(
        description="Score executed paper decisions against doing nothing.")
    ap.add_argument("--horizon", type=int, default=DEFAULT_HORIZON)
    ap.add_argument("--include-backlog", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rep = score(args.horizon, include_backlog=args.include_backlog)
    d = rep.to_dict()
    if args.json:
        print(json.dumps(d, indent=2))
        return

    print("=" * 78)
    print(f"  DECISION SCORE — executed paper fills vs doing nothing "
          f"({args.horizon}-session horizon)")
    print("=" * 78)

    o = d["overall"]
    if not o.get("n"):
        print(f"\n  nothing has resolved yet — {d['unresolved']} fill(s) waiting for "
              f"{args.horizon} sessions to elapse")
        if d["skipped_backlog"]:
            print(f"  {d['skipped_backlog']} BACKLOG fill(s) excluded (catch-up, "
                  f"not decisions)")
        if d["no_price_data"]:
            print(f"  no cached prices for: {', '.join(d['no_price_data'][:10])}")
        return

    pf = f"{o['profit_factor']:.2f}" if o.get("profit_factor") else "—"
    pr = f"{o['payoff_ratio']:.2f}" if o.get("payoff_ratio") else "—"
    print(f"\n  EXPECTANCY  {o['expectancy_pct']:+.3f}% per decision"
          f"   (n={o['n']})")
    print(f"              win {o['win_rate_pct']}%  ·  avg win {o['avg_win_pct']:+.2f}%  "
          f"·  avg loss -{o['avg_loss_pct']:.2f}%")
    print(f"              payoff {pr}  ·  profit factor {pf}  "
          f"·  worst losing streak {o['worst_losing_streak']}")
    print(f"\n              Expectancy is the bar: the average outcome of taking")
    print(f"              the next signal. A high win rate with a bad payoff")
    print(f"              ratio still loses money.")

    dc = o.get("drift_control")
    if dc:
        print(f"\n  drift check  beat the market's move {dc['beat_market_move_pct']}% "
              f"of the time (mean {dc['mean_pct']:+.2f}%)")
        print(f"              not the bar — a sanity check that positive expectancy")
        print(f"              is the rule and not simply a rising tape.")

    print(f"\n  {'-' * 74}\n  BY SLEEVE")
    for k, v in d["by_strategy"].items():
        if v.get("n"):
            vpr = f"{v['payoff_ratio']:.2f}" if v.get("payoff_ratio") else "—"
            print(f"    {k:16} n={v['n']:<4} exp {v['expectancy_pct']:+.3f}%  "
                  f"win {v['win_rate_pct']:>5}%  payoff {vpr}")

    print(f"\n  BY SIDE")
    for k, v in d["by_side"].items():
        if v.get("n"):
            print(f"    {k:16} n={v['n']:<4} hit {v['hit_rate_pct']:>5}%  "
                  f"mean edge {v['mean_edge_pct']:+.2f}%")

    print(f"\n  {d['unresolved']} unresolved · {d['skipped_backlog']} backlog excluded")
    print(f"\n  benchmark: {d['benchmark']}")
    print("  a positive edge on ONE sample is not an edge — it is one sample.")


if __name__ == "__main__":                        # pragma: no cover
    _main()
