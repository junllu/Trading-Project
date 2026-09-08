"""Confidence tracker — does the live system earn trust, measured forward?

The live loop records what it *would* have done, before the outcome is known
(data/forward_record.jsonl). This grades those predictions against what actually
happened, and converts the result into an explicit capital-scaling gate.

Why this and not another backtest: every backtest in this project shares one
window and one AI-bull regime, and walk-forward showed the tuned strategy
beating buy-and-hold in only 7 of 34 out-of-sample folds with a ~21pp overfit
gap. Forward records cannot be tuned after the fact — that is their entire
value. A small honest sample beats a large fitted one.

Two rules that keep this honest:

  1. A signal is only scored once enough time has passed for its horizon. No
     peeking at partial outcomes to make the number look better.
  2. Every conviction signal is benchmarked against simply holding the same
     name over the same window. "Was it right" is uninteresting; "did it beat
     doing nothing" is the question, because doing nothing keeps winning.

    python -m app.analytics.confidence
    python -m app.analytics.confidence --horizon 5
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..config import ROOT

RECORD_PATH = ROOT / "data" / "forward_record.jsonl"

# Capital-scaling gates. Deliberately demanding: the cost of a false positive is
# real money on an unvalidated strategy, and the cost of waiting is only time.
GATES = [
    (0, "NO DATA", "Record signals. Do not size anything off this."),
    (30, "MEASURED", "Enough to see a direction. Still paper only."),
    (100, "EVIDENCED", "If edge > 0 vs hold, a small live sleeve is defensible."),
    (250, "ESTABLISHED", "Sustained edge across regimes — scale deliberately."),
]


@dataclass
class ScoredSignal:
    date: str
    symbol: str
    score: float
    action: str
    entry: float
    exit: float
    horizon_days: int

    @property
    def ret_pct(self) -> float:
        return (self.exit / self.entry - 1) * 100 if self.entry else 0.0

    @property
    def directional_hit(self) -> bool:
        """Did price move the way conviction implied?"""
        return (self.ret_pct > 0) == (self.score > 0)

    @property
    def signed_return(self) -> float:
        """Return you'd have captured acting on the sign (long if +, short if -)."""
        return self.ret_pct if self.score > 0 else -self.ret_pct


@dataclass
class ConfidenceReport:
    horizon: int
    scored: list[ScoredSignal] = field(default_factory=list)
    pending: int = 0
    rows: int = 0

    @property
    def n(self) -> int:
        return len(self.scored)

    def gate(self) -> tuple[str, str]:
        level, label, advice = GATES[0][0], GATES[0][1], GATES[0][2]
        for threshold, lab, adv in GATES:
            if self.n >= threshold:
                level, label, advice = threshold, lab, adv
        return label, advice

    def summary(self) -> dict:
        if not self.scored:
            label, advice = self.gate()
            return {"horizon": self.horizon, "n": 0, "rows": self.rows,
                    "pending": self.pending, "gate": label, "advice": advice}
        hits = [s for s in self.scored if s.directional_hit]
        signed = [s.signed_return for s in self.scored]
        # benchmark: hold the same names over the same windows
        hold = [s.ret_pct for s in self.scored]
        label, advice = self.gate()
        return {
            "horizon": self.horizon, "n": self.n, "rows": self.rows, "pending": self.pending,
            "hit_rate_pct": round(100 * len(hits) / self.n, 1),
            "mean_signal_return_pct": round(st.mean(signed), 3),
            "median_signal_return_pct": round(st.median(signed), 3),
            "mean_buyhold_return_pct": round(st.mean(hold), 3),
            "edge_vs_hold_pp": round(st.mean(signed) - st.mean(hold), 3),
            "gate": label, "advice": advice,
        }


def _load_rows() -> list[dict]:
    if not RECORD_PATH.exists():
        return []
    rows = []
    for line in RECORD_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def score_forward(horizon_days: int = 5, min_abs_score: float = 0.2) -> ConfidenceReport:
    """Grade recorded convictions whose horizon has fully elapsed."""
    import warnings
    warnings.filterwarnings("ignore")
    import yfinance as yf

    rows = _load_rows()
    rep = ConfidenceReport(horizon=horizon_days, rows=len(rows))
    if not rows:
        return rep

    symbols = sorted({c["symbol"] for r in rows for c in r.get("convictions", [])})
    if not symbols:
        return rep
    px = yf.download(symbols, period="1y", progress=False, auto_adjust=True)["Close"]
    if px is None or px.empty:
        return rep
    if hasattr(px, "to_frame") and len(symbols) == 1:
        px = px.to_frame(symbols[0])

    now = datetime.now()
    for r in rows:
        try:
            # timestamps carry an offset (e.g. -04:00); compare naively in ET
            rec_dt = datetime.fromisoformat(r["recorded_at_et"]).replace(tzinfo=None)
        except Exception:
            continue
        # rule 1: don't score a signal before its horizon has actually elapsed
        if (now - rec_dt).days < horizon_days:
            rep.pending += sum(1 for c in r.get("convictions", [])
                               if abs(c.get("score", 0)) >= min_abs_score)
            continue

        for c in r.get("convictions", []):
            sym, score = c["symbol"], float(c.get("score", 0))
            if abs(score) < min_abs_score or sym not in px.columns:
                continue
            s = px[sym].dropna()
            after = s[s.index >= rec_dt.strftime("%Y-%m-%d")]
            if len(after) < 2:
                continue
            window = after[after.index <= (rec_dt + timedelta(days=horizon_days)).strftime("%Y-%m-%d")]
            if len(window) < 2:
                continue
            rep.scored.append(ScoredSignal(
                date=rec_dt.strftime("%Y-%m-%d"), symbol=sym, score=score,
                action=c.get("action", ""), entry=float(window.iloc[0]),
                exit=float(window.iloc[-1]), horizon_days=horizon_days))
    return rep


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=5, help="trading-day horizon to grade")
    ap.add_argument("--min-score", type=float, default=0.2,
                     help="only grade signals that crossed the entry threshold")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rep = score_forward(args.horizon, args.min_score)
    s = rep.summary()
    if args.json:
        print(json.dumps(s, indent=2))
        return

    print("=" * 64)
    print("CONFIDENCE TRACKER — forward, out-of-sample")
    print("=" * 64)
    print(f"record rows        : {s['rows']}")
    print(f"signals scored     : {s['n']}   (horizon {args.horizon}d)")
    print(f"awaiting horizon   : {s['pending']}")
    print(f"gate               : {s['gate']}")
    print(f"  -> {s['advice']}")
    if not s["n"]:
        print("\nNothing gradable yet. Run the live loop through market hours and")
        print("re-check once signals are older than the horizon.")
        return
    print()
    print(f"directional hit rate      : {s['hit_rate_pct']}%")
    print(f"mean signal return        : {s['mean_signal_return_pct']:+.3f}%")
    print(f"mean buy-and-hold return  : {s['mean_buyhold_return_pct']:+.3f}%")
    print(f"EDGE vs holding           : {s['edge_vs_hold_pp']:+.3f} pp")
    print()
    if s["edge_vs_hold_pp"] <= 0:
        print("No edge over doing nothing. Do not scale capital on this.")
    elif s["n"] < 100:
        print("Positive so far, but under 100 signals this is not yet evidence.")
    else:
        print("Positive edge on a meaningful sample — scaling is defensible.")


if __name__ == "__main__":
    _main()
