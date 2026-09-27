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
  3. ONE SIGNAL PER NAME PER DAY. The live loop snapshots every 5 minutes, and
     an earlier version graded every snapshot: 124 rows from 4 trading days
     became "748 signals" and cleared a gate meant to need months. Ten
     snapshots of MRVL on one morning are one opinion, not ten.
  4. THE HORIZON IS TRADING SESSIONS. It was calendar days, so a Friday
     signal's "5-day" window held three sessions.

The capital gate itself lives in sleeve.py; this module only measures.

    python -m app.analytics.confidence
    python -m app.analytics.confidence --horizon 5
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from dataclasses import dataclass, field
from datetime import datetime

from ..config import ROOT
from ..data.market_hours import is_trading_day

RECORD_PATH = ROOT / "data" / "forward_record.jsonl"



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
    excluded: int = 0          # rows recorded before `since` (not evidence)
    since: str | None = None

    @property
    def n(self) -> int:
        return len(self.scored)

    def t_stat(self) -> float | None:
        """Significance of the mean signed return, counted in DAYS, not signals.

        Signals on the same day share one market move, so they are averaged into
        one observation per day first. Consecutive days' 5-session windows
        overlap, which inflates a naive t by roughly sqrt(horizon); dividing by
        that is a conservative stand-in for a Newey-West correction.
        """
        by_day: dict[str, list[float]] = {}
        for s in self.scored:
            by_day.setdefault(s.date, []).append(s.signed_return)
        daily = [st.mean(v) for v in by_day.values()]
        if len(daily) < 3:
            return None
        sd = st.stdev(daily)
        if sd == 0:
            return None
        return st.mean(daily) / (sd / len(daily) ** 0.5) / max(self.horizon, 1) ** 0.5

    def summary(self) -> dict:
        base = {"horizon": self.horizon, "n": self.n, "rows": self.rows,
                "pending": self.pending, "excluded": self.excluded, "since": self.since}
        if not self.scored:
            return {**base, "days": 0}
        hits = [s for s in self.scored if s.directional_hit]
        signed = [s.signed_return for s in self.scored]
        # benchmark: hold the same names over the same windows
        hold = [s.ret_pct for s in self.scored]
        days = sorted({s.date for s in self.scored})
        t = self.t_stat()
        return {
            **base,
            "days": len(days), "first_date": days[0], "last_date": days[-1],
            "span_days": (datetime.fromisoformat(days[-1])
                          - datetime.fromisoformat(days[0])).days,
            "hit_rate_pct": round(100 * len(hits) / self.n, 1),
            "mean_signal_return_pct": round(st.mean(signed), 3),
            "median_signal_return_pct": round(st.median(signed), 3),
            "mean_buyhold_return_pct": round(st.mean(hold), 3),
            "edge_vs_hold_pp": round(st.mean(signed) - st.mean(hold), 3),
            "t_stat": None if t is None else round(t, 2),
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


def score_forward(horizon_days: int = 5, min_abs_score: float = 0.2,
                  since: str | None = None) -> ConfidenceReport:
    """Grade recorded convictions whose horizon has fully elapsed.

    `since` (YYYY-MM-DD) drops rows recorded before it: evidence produced by a
    different strategy, or on broken data, is not evidence for this one.
    """
    import warnings
    warnings.filterwarnings("ignore")
    import yfinance as yf

    rows = _load_rows()
    rep = ConfidenceReport(horizon=horizon_days, rows=len(rows), since=since)
    if since:
        kept = [r for r in rows if str(r.get("recorded_at_et", ""))[:10] >= since]
        rep.excluded = len(rows) - len(kept)
        rows = kept
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

    seen: set[tuple[str, str]] = set()
    rows = sorted(rows, key=lambda r: str(r.get("recorded_at_et", "")))
    for r in rows:
        try:
            # timestamps carry an offset (e.g. -04:00); compare naively in ET
            rec_dt = datetime.fromisoformat(r["recorded_at_et"]).replace(tzinfo=None)
        except Exception:
            continue
        day = rec_dt.strftime("%Y-%m-%d")
        # A row written on a weekend or holiday (a manual --once run) is not a
        # trading day; counting it would inflate the gate's day count.
        if not is_trading_day(rec_dt.date()):
            continue

        for c in r.get("convictions", []):
            sym, score = c["symbol"], float(c.get("score", 0))
            if abs(score) < min_abs_score or (sym, day) in seen:
                continue
            seen.add((sym, day))            # rule 3: first qualifying call of the day
            if sym not in px.columns:
                continue
            s = px[sym].dropna()
            after = s[s.index >= day]
            # rules 1 + 4: entry is that day's close, exit `horizon` SESSIONS
            # later. Until that session has closed the signal is pending.
            if len(after) < horizon_days + 1:
                rep.pending += 1
                continue
            rep.scored.append(ScoredSignal(
                date=day, symbol=sym, score=score,
                action=c.get("action", ""), entry=float(after.iloc[0]),
                exit=float(after.iloc[horizon_days]), horizon_days=horizon_days))
    return rep


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=5, help="trading-day horizon to grade")
    ap.add_argument("--min-score", type=float, default=0.2,
                     help="only grade signals that crossed the entry threshold")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--since", default=None, help="ignore rows recorded before YYYY-MM-DD")
    args = ap.parse_args()

    rep = score_forward(args.horizon, args.min_score, since=args.since)
    s = rep.summary()
    if args.json:
        print(json.dumps(s, indent=2))
        return

    print("=" * 64)
    print("CONFIDENCE TRACKER — forward, out-of-sample")
    print("=" * 64)
    print(f"record rows        : {s['rows']}  (excluded before {s['since']}: {s['excluded']})")
    print(f"signals scored     : {s['n']} over {s['days']} trading day(s)  "
          f"(one per name per day, horizon {args.horizon} sessions)")
    print(f"awaiting horizon   : {s['pending']}")
    print("capital gate       : python -m app.analytics.sleeve")
    if not s["n"]:
        print("\nNothing gradable yet. Run the live loop through market hours and")
        print("re-check once signals are older than the horizon.")
        return
    print()
    print(f"directional hit rate      : {s['hit_rate_pct']}%")
    print(f"mean signal return        : {s['mean_signal_return_pct']:+.3f}%")
    print(f"mean buy-and-hold return  : {s['mean_buyhold_return_pct']:+.3f}%")
    print(f"EDGE vs holding           : {s['edge_vs_hold_pp']:+.3f} pp")
    print(f"t-stat (by day, overlap-adjusted): {s['t_stat']}")


if __name__ == "__main__":
    _main()
