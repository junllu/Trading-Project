"""Intraday truth about exits — what the daily bar cannot tell you.

THE BIAS THIS EXISTS TO KILL

exit_plans.py sets a 20% book trailing halt and a 15% tactical max-loss. Every
backtest of those numbers so far has run on daily bars, and a daily bar cannot
answer the only question that matters for a stop: *was the level actually
touched?* A day that opens 220, wicks to 186 and closes 219 is, to a daily bar,
a quiet -0.5% session. To a 15% stop it is a liquidation at the low followed by
a recovery you no longer participated in.

Daily-bar stop backtests therefore under-count triggers and over-state the
strategy. Minute bars remove the guess. This module supplies the measurement;
it deliberately sets no policy.

    intraday.py   measures  (MAE/MFE, true touch, wick vs break, stop regret)
    exit_plans.py decides   (which stop, which pool, when to trim)

Keeping that line clean is why nothing here imports exit_plans, and why every
function takes its thresholds as arguments instead of reading config. A
measurement module that quietly re-implemented the policy would be a second
source of truth for the same rule.

COMPRESSION IS THE POINT

390 bars/day/symbol does not scale to a research corpus, and does not need to.
`daily_features()` reduces a session to ~15 numbers and caches them in
data/intraday/{SYM}.csv. A year is then 252 rows instead of ~98,000 bars, which
survives even if the raw minute store is later pruned.

    python -m app.analytics.intraday --features MRVL
    python -m app.analytics.intraday --stop-test MRVL --stop 15 --trailing
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone

from ..config import ROOT
from ..data.minute import MinuteBar, load

INTRADAY_DIR = ROOT / "data" / "intraday"

RTH = ("RTH",)


def by_day(symbol: str, sessions: tuple[str, ...] = RTH,
           bars: list[MinuteBar] | None = None) -> dict[date, list[MinuteBar]]:
    out: dict[date, list[MinuteBar]] = {}
    for b in (bars if bars is not None else load(symbol, sessions=sessions)):
        out.setdefault(b.ts.date(), []).append(b)
    for d in out:
        out[d].sort(key=lambda x: x.ts)
    return out


@dataclass
class DayFeatures:
    """One session, compressed. Percentages are fractions, not points."""
    symbol: str
    date: str
    bars: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float
    # Excursion measured FROM THE OPEN — the only entry price knowable without
    # a position, so the store stays position-independent and reusable.
    mae_pct: float           # worst drawdown from open, intraday
    mfe_pct: float           # best gain from open, intraday
    mae_minute: int          # minutes after the open that the MAE printed
    mfe_minute: int
    range_pct: float
    close_loc: float         # where close sits in the range: 0=low, 1=high
    open_30m_range_pct: float
    last_30m_volume_share: float
    max_1m_move_pct: float


def daily_features(symbol: str, sessions: tuple[str, ...] = RTH) -> list[DayFeatures]:
    out: list[DayFeatures] = []
    for d, bars in sorted(by_day(symbol, sessions).items()):
        if len(bars) < 2:
            continue
        o, c = bars[0].open, bars[-1].close
        hi = max(b.high for b in bars)
        lo = min(b.low for b in bars)
        vol = sum(b.volume for b in bars)
        # Volume-weighted, not bar-weighted: a 200-share minute must not carry
        # the same weight as the closing auction.
        vwap = (sum(b.typical * b.volume for b in bars) / vol) if vol > 0 else c

        mae_i = min(range(len(bars)), key=lambda i: bars[i].low)
        mfe_i = max(range(len(bars)), key=lambda i: bars[i].high)
        first30 = bars[:30]
        last30 = bars[-30:]
        max_move = 0.0
        for b in bars:
            if b.open > 0:
                max_move = max(max_move, abs(b.close / b.open - 1.0))

        out.append(DayFeatures(
            symbol=symbol.upper(), date=d.isoformat(), bars=len(bars),
            open=round(o, 4), high=round(hi, 4), low=round(lo, 4), close=round(c, 4),
            volume=vol, vwap=round(vwap, 4),
            mae_pct=round(lo / o - 1.0, 6) if o else 0.0,
            mfe_pct=round(hi / o - 1.0, 6) if o else 0.0,
            mae_minute=int((bars[mae_i].ts - bars[0].ts).total_seconds() // 60),
            mfe_minute=int((bars[mfe_i].ts - bars[0].ts).total_seconds() // 60),
            range_pct=round((hi - lo) / o, 6) if o else 0.0,
            close_loc=round((c - lo) / (hi - lo), 4) if hi > lo else 0.5,
            open_30m_range_pct=round(
                (max(b.high for b in first30) - min(b.low for b in first30)) / o, 6) if o else 0.0,
            last_30m_volume_share=round(
                sum(b.volume for b in last30) / vol, 4) if vol > 0 else 0.0,
            max_1m_move_pct=round(max_move, 6),
        ))
    return out


def write_features(symbol: str, sessions: tuple[str, ...] = RTH) -> dict:
    feats = daily_features(symbol, sessions)
    if not feats:
        return {"symbol": symbol.upper(), "rows": 0, "note": "no minute bars stored"}
    INTRADAY_DIR.mkdir(parents=True, exist_ok=True)
    p = INTRADAY_DIR / f"{symbol.upper()}.csv"
    tmp = p.with_suffix(".csv.tmp")
    cols = list(asdict(feats[0]).keys())
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for f in feats:
            w.writerow(asdict(f))
    tmp.replace(p)
    return {"symbol": symbol.upper(), "rows": len(feats),
            "first": feats[0].date, "last": feats[-1].date, "written_to": str(p)}


@dataclass
class StopResult:
    """What a stop would ACTUALLY have done, minute by minute."""
    symbol: str
    stop_pct: float
    trailing: bool
    entry_date: str
    entry_price: float
    triggered: bool
    trigger_ts: str | None
    trigger_price: float | None
    minutes_held: int
    exit_value_pct: float        # return if the stop took you out (or held to end)
    hold_value_pct: float        # return if you had ignored the stop entirely
    regret_pp: float             # hold minus stop, in percentage points
    was_wick: bool               # price recovered above the stop level same day
    peak_before_stop_pct: float
    final_pct: float


def stop_test(symbol: str, stop_pct: float, *, trailing: bool = True,
              entry_price: float | None = None, start: datetime | None = None,
              end: datetime | None = None, sessions: tuple[str, ...] = RTH
              ) -> StopResult | None:
    """Walk minute bars and find the first true touch of the stop.

    `regret_pp` is the number the exit debate actually turns on: positive means
    holding beat stopping out, negative means the stop saved you. The existing
    doctrine — "forgone gains came from selling winners too early, not from
    missing a tight stop" — predicts a positive mean regret. This is how that
    claim gets tested instead of asserted.

    Fills are modelled AT the stop level, not at the bar low. That flatters the
    stop slightly, which is the right direction for an honest test: if the stop
    still loses with generous fills, it loses.
    """
    bars = load(symbol, start=start, end=end, sessions=sessions)
    if len(bars) < 2:
        return None
    entry = entry_price if entry_price is not None else bars[0].open
    if entry <= 0:
        return None
    frac = stop_pct / 100.0 if stop_pct > 1 else stop_pct

    peak = entry
    trigger = None
    for b in bars:
        level = peak * (1 - frac) if trailing else entry * (1 - frac)
        if b.low <= level:
            trigger = (b, level)
            break
        peak = max(peak, b.high)

    final = bars[-1].close
    hold_pct = final / entry - 1.0

    if trigger is None:
        return StopResult(
            symbol=symbol.upper(), stop_pct=frac * 100, trailing=trailing,
            entry_date=bars[0].ts.date().isoformat(), entry_price=round(entry, 4),
            triggered=False, trigger_ts=None, trigger_price=None,
            minutes_held=len(bars), exit_value_pct=round(hold_pct, 6),
            hold_value_pct=round(hold_pct, 6), regret_pp=0.0, was_wick=False,
            peak_before_stop_pct=round(peak / entry - 1.0, 6),
            final_pct=round(hold_pct, 6))

    hit_bar, level = trigger
    exit_pct = level / entry - 1.0
    # A "wick" is a stop that fired and was then proven wrong within the SAME
    # session — the distinction between a stop that protected capital and one
    # that just harvested volatility.
    same_day = [b for b in bars if b.ts.date() == hit_bar.ts.date() and b.ts > hit_bar.ts]
    was_wick = bool(same_day) and max(b.high for b in same_day) > level

    return StopResult(
        symbol=symbol.upper(), stop_pct=frac * 100, trailing=trailing,
        entry_date=bars[0].ts.date().isoformat(), entry_price=round(entry, 4),
        triggered=True, trigger_ts=hit_bar.ts.isoformat(),
        trigger_price=round(level, 4),
        minutes_held=sum(1 for b in bars if b.ts <= hit_bar.ts),
        exit_value_pct=round(exit_pct, 6), hold_value_pct=round(hold_pct, 6),
        regret_pp=round((hold_pct - exit_pct) * 100, 3), was_wick=was_wick,
        peak_before_stop_pct=round(peak / entry - 1.0, 6),
        final_pct=round(hold_pct, 6))


def stop_sweep(symbol: str, stops=(5, 8, 10, 12, 15, 20, 25), *,
               trailing: bool = True, **kw) -> list[dict]:
    """The same window against a ladder of stop widths.

    Reported together because a single stop width in isolation invites picking
    the one that happened to work. The shape of the curve across widths is the
    honest signal; a lone favourable point is a trial, not a finding.
    """
    out = []
    for s in stops:
        r = stop_test(symbol, s, trailing=trailing, **kw)
        if r:
            out.append({"stop_pct": r.stop_pct, "triggered": r.triggered,
                        "exit_pct": r.exit_value_pct, "hold_pct": r.hold_value_pct,
                        "regret_pp": r.regret_pp, "was_wick": r.was_wick,
                        "minutes_held": r.minutes_held})
    return out


@dataclass
class StudyResult:
    """Many entries, one stop rule — the only way the question is answerable.

    A single stop_test is an anecdote: it depends entirely on which day you
    happened to enter. Rolling the entry across every stored session is what
    turns it into evidence.
    """
    symbol: str
    stop_pct: float
    trailing: bool
    hold_sessions: int
    entries: int
    effective_n: float
    triggered: int
    trigger_rate: float
    wick_rate: float
    mean_regret_pp: float
    median_regret_pp: float
    mean_exit_pct: float
    mean_hold_pct: float
    verdict: str


def stop_study(symbol: str, stop_pct: float = 15.0, *, trailing: bool = True,
               hold_sessions: int = 10, sessions: tuple[str, ...] = RTH
               ) -> StudyResult | None:
    """Roll the entry across every stored session and aggregate the outcome.

    OVERLAP IS THE TRAP HERE. Sixty-four entries each held ten sessions are not
    sixty-four independent observations — consecutive windows share nine tenths
    of their data. `effective_n` divides through by the hold length to give the
    non-overlapping equivalent, and the verdict refuses to commit below ten.
    Reporting `entries` alone would overstate the evidence by an order of
    magnitude, which is precisely how a 139-config sweep produces a DSR of
    0.9998 and still fails forward.
    """
    days = sorted(by_day(symbol, sessions))
    if len(days) < hold_sessions + 2:
        return None

    results: list[StopResult] = []
    for i in range(len(days) - hold_sessions):
        window = load(symbol, sessions=sessions,
                      start=datetime.combine(days[i], datetime.min.time(),
                                             tzinfo=timezone.utc),
                      end=datetime.combine(days[i + hold_sessions - 1],
                                           datetime.max.time(), tzinfo=timezone.utc))
        if len(window) < 2:
            continue
        r = _stop_on_bars(symbol, window, stop_pct, trailing)
        if r:
            results.append(r)

    if not results:
        return None

    fired = [r for r in results if r.triggered]
    regrets = sorted(r.regret_pp for r in results)
    n = len(results)
    eff = n / max(hold_sessions, 1)
    mean_regret = sum(regrets) / n
    median_regret = regrets[n // 2]

    if eff < 10:
        verdict = f"INCONCLUSIVE — only ~{eff:.1f} independent windows"
    elif mean_regret > 1.0:
        verdict = f"STOP HURTS — holding beat it by {mean_regret:.1f}pp on average"
    elif mean_regret < -1.0:
        verdict = f"STOP HELPS — it saved {abs(mean_regret):.1f}pp on average"
    else:
        verdict = "NEUTRAL — no material difference either way"

    return StudyResult(
        symbol=symbol.upper(), stop_pct=stop_pct, trailing=trailing,
        hold_sessions=hold_sessions, entries=n, effective_n=round(eff, 1),
        triggered=len(fired),
        trigger_rate=round(len(fired) / n, 4),
        wick_rate=round(sum(1 for r in fired if r.was_wick) / len(fired), 4) if fired else 0.0,
        mean_regret_pp=round(mean_regret, 3),
        median_regret_pp=round(median_regret, 3),
        mean_exit_pct=round(sum(r.exit_value_pct for r in results) / n, 5),
        mean_hold_pct=round(sum(r.hold_value_pct for r in results) / n, 5),
        verdict=verdict)


def _stop_on_bars(symbol: str, bars: list[MinuteBar], stop_pct: float,
                  trailing: bool) -> StopResult | None:
    """stop_test's core, against an in-memory window.

    Split out so the study does not re-read the CSV once per entry — 64 entries
    over a 25k-bar file is 1.6M rows of avoidable parsing.
    """
    entry = bars[0].open
    if entry <= 0:
        return None
    frac = stop_pct / 100.0 if stop_pct > 1 else stop_pct
    peak, trigger = entry, None
    for b in bars:
        level = peak * (1 - frac) if trailing else entry * (1 - frac)
        if b.low <= level:
            trigger = (b, level)
            break
        peak = max(peak, b.high)

    final = bars[-1].close
    hold_pct = final / entry - 1.0
    if trigger is None:
        return StopResult(symbol.upper(), frac * 100, trailing,
                          bars[0].ts.date().isoformat(), round(entry, 4),
                          False, None, None, len(bars), round(hold_pct, 6),
                          round(hold_pct, 6), 0.0, False,
                          round(peak / entry - 1.0, 6), round(hold_pct, 6))

    hit, level = trigger
    exit_pct = level / entry - 1.0
    same_day = [b for b in bars if b.ts.date() == hit.ts.date() and b.ts > hit.ts]
    return StopResult(
        symbol.upper(), frac * 100, trailing, bars[0].ts.date().isoformat(),
        round(entry, 4), True, hit.ts.isoformat(), round(level, 4),
        sum(1 for b in bars if b.ts <= hit.ts), round(exit_pct, 6),
        round(hold_pct, 6), round((hold_pct - exit_pct) * 100, 3),
        bool(same_day) and max(b.high for b in same_day) > level,
        round(peak / entry - 1.0, 6), round(hold_pct, 6))


def _main() -> None:
    ap = argparse.ArgumentParser(description="Intraday exit measurement (minute bars).")
    ap.add_argument("--features", metavar="SYMBOL")
    ap.add_argument("--stop-test", metavar="SYMBOL")
    ap.add_argument("--sweep", metavar="SYMBOL")
    ap.add_argument("--stop", type=float, default=15.0)
    ap.add_argument("--trailing", action="store_true", default=True)
    ap.add_argument("--fixed", dest="trailing", action="store_false")
    ap.add_argument("--entry", type=float, default=None)
    ap.add_argument("--study", nargs="+", metavar="SYMBOL",
                    help="roll the entry across every stored session")
    ap.add_argument("--hold", type=int, default=10, help="sessions held per entry")
    args = ap.parse_args()

    if args.study:
        print(f"Stop study — entry rolled across every session, {args.hold}-session hold, "
              f"trailing={args.trailing}\n")
        print(f"  {'sym':>5} {'stop':>5} {'entries':>8} {'eff n':>6} {'fired':>6} "
              f"{'wick':>6} {'mean regret':>12} {'verdict'}")
        for sym in args.study:
            for stop in (8, 10, 15, 20, 25):
                r = stop_study(sym, stop, trailing=args.trailing,
                               hold_sessions=args.hold)
                if not r:
                    print(f"  {sym.upper():>5} — not enough sessions stored")
                    break
                print(f"  {r.symbol:>5} {r.stop_pct:>4.0f}% {r.entries:>8} "
                      f"{r.effective_n:>6.1f} {r.trigger_rate:>5.0%} "
                      f"{r.wick_rate:>5.0%} {r.mean_regret_pp:>11.2f}pp  {r.verdict}")
            print()
        print("  regret > 0 = holding beat the stop.  'wick' = of the stops that fired,")
        print("  the share where price recovered above the stop level the SAME day.")
        return

    if args.features:
        res = write_features(args.features)
        print(res)
        for f in daily_features(args.features)[-10:]:
            print(f"  {f.date}  {f.bars:>3}b  O{f.open:>9.2f} C{f.close:>9.2f}  "
                  f"MAE{f.mae_pct:>7.2%}@{f.mae_minute:>3}m  "
                  f"MFE{f.mfe_pct:>7.2%}@{f.mfe_minute:>3}m  "
                  f"rng{f.range_pct:>6.2%}  loc{f.close_loc:>5.2f}")
        return

    if args.stop_test:
        r = stop_test(args.stop_test, args.stop, trailing=args.trailing,
                      entry_price=args.entry)
        if not r:
            print("not enough minute bars stored for that symbol")
            return
        for k, v in asdict(r).items():
            print(f"  {k:>22}: {v}")
        return

    if args.sweep:
        rows = stop_sweep(args.sweep, trailing=args.trailing, entry_price=args.entry)
        if not rows:
            print("not enough minute bars stored for that symbol")
            return
        print(f"{args.sweep.upper()} — trailing={args.trailing}")
        print(f"  {'stop':>6} {'fired':>6} {'exit':>8} {'hold':>8} {'regret_pp':>10} {'wick':>6}")
        for r in rows:
            print(f"  {r['stop_pct']:>5.0f}% {str(r['triggered']):>6} "
                  f"{r['exit_pct']:>7.2%} {r['hold_pct']:>7.2%} "
                  f"{r['regret_pp']:>10.2f} {str(r['was_wick']):>6}")
        print("\n  regret_pp > 0 means holding beat the stop.")
        return

    from ..data.minute import coverage
    cov = coverage()
    print(f"minute store: {len(cov)} symbol(s). "
          f"Run --features SYMBOL to build the compressed daily layer.")


if __name__ == "__main__":
    _main()
