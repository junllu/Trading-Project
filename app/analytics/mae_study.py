"""Where should a stop sit, per name, given how far that name actually travels?

THE FINDING THAT MOTIVATES THIS

`intraday.py` measured a 15% max-loss over 64 sessions: breached on 79% of MRVL
entries (median 21-session MAE -25.7%) and 0% of NVDA's (-6.4%). One number,
simultaneously hair-trigger and decorative depending on the name. A stop set
below a name's ordinary noise does not manage risk — it converts ordinary noise
into realised losses and removes you from the trades that would have worked.

So a stop is not a preference. It is a claim about a distribution, and the
distribution is per name.

WHAT THIS DOES, AND THE TRAP IT REFUSES

It reads each name's adverse-excursion distribution and reports where a stop
would have to sit to clear the ordinary case. It does NOT sweep stop values and
report the most profitable one.

That refusal is the whole point. Sweeping seven stops across four names is 28
looks, and the best of 28 draws from noise looks like an edge — the identical
mechanism that made `candles.py` produce a 2.3 Sharpe that deflation then
rejected as "the best of N coin flips". A stop chosen because it scored best in
sample is a fitted parameter wearing a risk-management costume.

So the recommendation here is derived from the SPREAD, not from returns:
place the stop outside the name's ordinary adverse move, and let the widest
tolerable loss — a policy question owned by exit_plans.py — decide whether that
name can be carried at all. Where the two conflict the honest answer is that the
name is too volatile for the sleeve, not that the stop should be tightened into
the noise.

    python -m app.analytics.mae_study
    python -m app.analytics.mae_study --symbols MRVL NVDA TSLA --json
"""
from __future__ import annotations

import argparse
import json
from typing import Any

# Percentile of the adverse-excursion distribution a stop must clear. At 80,
# four of every five entries would never have touched it, so the stop is
# reserved for genuinely abnormal moves rather than firing on routine ones.
CLEAR_PERCENTILE = 80

# A stop is placed a little beyond the level it must clear, so an entry that
# merely matches the historical worst-ordinary case is not stopped by a tick.
BUFFER_MULT = 1.15


def _profile(symbol: str, hold_sessions: int) -> dict | None:
    from .intraday import excursion_profile
    return excursion_profile(symbol, hold_sessions=hold_sessions)


def _worst_moves(symbol: str, hold_sessions: int) -> list[float]:
    """The adverse excursion of every entry, as negative percentages.

    Reads DAILY bars, not minute-derived sessions. The question here is "how far
    below the entry did price go over the next N sessions", and a daily bar's LOW
    already is that session's true low — minute resolution answers *when* it
    happened, which this does not ask.

    That distinction is worth 106 symbols. Minute history exists for three names
    and one session for the other ninety-six, so a minute-based study could only
    ever speak about MRVL, NVDA and TSLA. The daily store holds years for 109.
    A stop placed on three names and applied to thirty is not a measurement.
    """
    from ..data.ohlc import load
    try:
        bars = load(symbol)
    except Exception:
        return []
    if len(bars) < hold_sessions + 2:
        return []
    out = []
    for i in range(len(bars) - hold_sessions):
        entry = float(bars[i].open)
        if entry <= 0:
            continue
        low = min(float(b.low) for b in bars[i:i + hold_sessions])
        out.append((low / entry - 1.0) * 100.0)
    return sorted(out)


def study_symbol(symbol: str, hold_sessions: int = 21) -> dict[str, Any]:
    moves = _worst_moves(symbol, hold_sessions)
    if not moves:
        return {"symbol": symbol.upper(), "available": False,
                "why": "not enough minute-bar history to build a distribution"}

    n = len(moves)
    # moves are negative; sorted ascending puts the worst first.
    idx = min(n - 1, max(0, int(round((100 - CLEAR_PERCENTILE) / 100.0 * n))))
    clears = moves[idx]                    # the level that CLEAR_PERCENTILE% never breached
    suggested = round(abs(clears) * BUFFER_MULT, 1)

    # Breach rates from the SAME distribution the stop is derived from. These
    # previously came from the minute-based profile while everything else came
    # from daily bars, so for the 96 names without minute history they silently
    # read 0% — a table saying "a 15% stop never fires" on names whose median
    # adverse move is -15%. One source per table.
    def _breached(pct: float) -> int:
        return round(sum(1 for m in moves if m <= -pct) / n * 100)

    return {
        "symbol": symbol.upper(),
        "available": True,
        "entries": n,
        "hold_sessions": hold_sessions,
        "median_mae_pct": round(moves[n // 2], 1),
        "worst_mae_pct": round(moves[0], 1),
        f"p{CLEAR_PERCENTILE}_mae_pct": round(clears, 1),
        "suggested_stop_pct": suggested,
        "breach_at_15_pct": _breached(15.0),
        "breach_at_20_pct": _breached(20.0),
        "basis": (f"a stop at {suggested:.0f}% sits outside the adverse move "
                  f"{CLEAR_PERCENTILE}% of entries made over {hold_sessions} sessions"),
    }


def study(symbols: list[str] | None = None, hold_sessions: int = 21) -> dict[str, Any]:
    if not symbols:
        # Held names first — a stop matters most where capital already sits —
        # then anything else with daily history.
        try:
            from ..intel.onboard import held_symbols
            symbols = held_symbols()
        except Exception:
            symbols = []
        try:
            from ..data.ohlc import coverage
            for s in sorted(coverage()):
                if s not in symbols:
                    symbols.append(s)
        except Exception:
            pass
    rows = [study_symbol(s, hold_sessions) for s in symbols]
    ok = [r for r in rows if r.get("available")]

    spread = None
    if len(ok) > 1:
        lo = min(r["suggested_stop_pct"] for r in ok)
        hi = max(r["suggested_stop_pct"] for r in ok)
        spread = {"tightest": lo, "widest": hi, "ratio": round(hi / lo, 1) if lo else None}

    return {
        "agent": "mae_study",
        "hold_sessions": hold_sessions,
        "clear_percentile": CLEAR_PERCENTILE,
        "symbols": rows,
        "spread": spread,
        "verdict": (
            f"per-name stops span {spread['tightest']:.0f}%-{spread['widest']:.0f}% "
            f"({spread['ratio']}x) — a single global stop cannot serve both ends"
            if spread and spread.get("ratio") and spread["ratio"] >= 1.5 else
            "stops cluster closely enough that one number may serve"
            if spread else "not enough names with history to compare"),
        "does_not": [
            "sweep stop values and pick the most profitable — that is fitting, "
            "and the best of N looks is not an edge",
            "set policy; exit_plans.py owns the number this informs",
        ],
    }


def report() -> dict[str, Any]:
    return study()


def _main() -> None:                              # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description="Per-name adverse excursion → stop placement.")
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--hold", type=int, default=21)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    r = study(args.symbols, args.hold)
    if args.json:
        print(json.dumps(r, indent=2))
        return

    print("=" * 78)
    print(f"  MAE STUDY — how far each name travels against you "
          f"({args.hold}-session hold)")
    print("=" * 78)
    print(f"\n  {'SYM':7}{'N':>5}{'MEDIAN':>9}{'WORST':>9}{'P80':>8}"
          f"{'STOP':>8}{'@15%':>7}{'@20%':>7}")
    print("  " + "-" * 74)
    for s in r["symbols"]:
        if not s.get("available"):
            print(f"  {s['symbol']:7}  {s['why']}")
            continue
        print(f"  {s['symbol']:7}{s['entries']:>5}"
              f"{s['median_mae_pct']:>8.1f}%{s['worst_mae_pct']:>8.1f}%"
              f"{s[f'p{CLEAR_PERCENTILE}_mae_pct']:>7.1f}%"
              f"{s['suggested_stop_pct']:>7.1f}%"
              f"{(s.get('breach_at_15_pct') if s.get('breach_at_15_pct') is not None else 0):>6}%"
              f"{(s.get('breach_at_20_pct') if s.get('breach_at_20_pct') is not None else 0):>6}%")

    print(f"\n  {'-' * 74}")
    print(f"  {r['verdict']}")
    print("\n  @15% / @20% are the share of entries that WOULD have been stopped out.")
    print("  A stop inside the ordinary move converts noise into realised losses.")
    print("  This informs exit_plans.py; it does not set the number.")


if __name__ == "__main__":                        # pragma: no cover
    _main()
