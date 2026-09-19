"""What a name typically pays you, against what it typically costs you.

THE GAP THIS FILLS

Nothing in the approval path performs a risk/benefit analysis. `sizing.py` gates
on a conviction score crossing 0.2 and ramps size from there; the risk manager
checks caps, budgets and halts; the chief gates on reversibility. Every one of
those asks "is this allowed" or "how big". None asks "is this worth it".

The practical consequence: a 0.3 conviction on CVX and a 0.3 conviction on BE
are treated as the same trade apart from a volatility scalar, while CVX's median
adverse move over 21 sessions is -3.8% and BE's is -15.1%. Four times the
downside, same gate.

WHAT THIS MEASURES

Both excursions from the same entry, over the same hold:

    MAE   how far it went AGAINST you   (already used to place stops)
    MFE   how far it went FOR you       (computed here, never used before)
    R:R   the ratio of the two

Read at the MEDIAN, not the mean. Excursion distributions are heavily skewed by
a few enormous moves, and a mean R:R flatters exactly the names whose tails are
doing the work. p80 is reported alongside so the spread is visible.

WHAT IT IS NOT

Not a screen for the best names. Ranking 109 symbols by R:R and trading the top
ten is the multiplicity trap this project has already measured twice — the best
of 3,208 candlestick looks could not beat selection noise. R:R here is a
per-name CALIBRATION input: it says what a given name's downside costs relative
to its upside, so a gate or a size can respect that difference.

And it is a description of the past, not a forecast. A name that paid 2:1 for
three years can stop. The number earns its place only if a gate built on it
beats the current no-gate behaviour on the same decisions, measured forward.

    python -m app.analytics.reward_risk
    python -m app.analytics.reward_risk --hold 5 --json
"""
from __future__ import annotations

import argparse
import json
from typing import Any

DEFAULT_HOLD = 21


def _excursions(symbol: str, hold_sessions: int) -> tuple[list[float], list[float]]:
    """(adverse, favourable) percentage excursions for every entry.

    Entry is each session's OPEN — the only price knowable without already
    holding, which keeps the measurement independent of any position.
    """
    from ..data.ohlc import load
    try:
        bars = load(symbol)
    except Exception:
        return [], []
    if len(bars) < hold_sessions + 2:
        return [], []

    mae, mfe = [], []
    for i in range(len(bars) - hold_sessions):
        entry = float(bars[i].open)
        if entry <= 0:
            continue
        window = bars[i:i + hold_sessions]
        low = min(float(b.low) for b in window)
        high = max(float(b.high) for b in window)
        mae.append((low / entry - 1.0) * 100.0)
        mfe.append((high / entry - 1.0) * 100.0)
    return sorted(mae), sorted(mfe, reverse=True)


def _pct(values: list[float], p: int) -> float:
    """Value at percentile p of an already-sorted list."""
    if not values:
        return 0.0
    idx = min(len(values) - 1, max(0, int(round(p / 100.0 * len(values)))))
    return values[idx]


def study_symbol(symbol: str, hold_sessions: int = DEFAULT_HOLD) -> dict[str, Any]:
    mae, mfe = _excursions(symbol, hold_sessions)
    if not mae:
        return {"symbol": symbol.upper(), "available": False,
                "why": "not enough daily history"}

    n = len(mae)
    med_mae = abs(mae[n // 2])
    med_mfe = mfe[n // 2]
    p80_mae = abs(_pct(mae, 20))          # sorted ascending: worst first
    p80_mfe = _pct(mfe, 20)               # sorted descending: best first

    rr = (med_mfe / med_mae) if med_mae > 0 else None
    return {
        "symbol": symbol.upper(), "available": True, "entries": n,
        "hold_sessions": hold_sessions,
        "median_mae_pct": round(-med_mae, 1),
        "median_mfe_pct": round(med_mfe, 1),
        "p80_mae_pct": round(-p80_mae, 1),
        "p80_mfe_pct": round(p80_mfe, 1),
        "reward_risk": round(rr, 2) if rr else None,
        # Whether the typical favourable move even covers a round trip of the
        # typical adverse one. Below 1.0 the name pays less than it costs at the
        # median, and only the tail makes it look worthwhile.
        "pays_at_median": bool(rr and rr >= 1.0),
    }


def study(symbols: list[str] | None = None,
          hold_sessions: int = DEFAULT_HOLD) -> dict[str, Any]:
    if not symbols:
        try:
            from ..intel.onboard import held_symbols
            symbols = held_symbols()
        except Exception:
            symbols = []
    rows = [study_symbol(s, hold_sessions) for s in symbols]
    ok = [r for r in rows if r.get("available") and r.get("reward_risk")]

    below = [r["symbol"] for r in ok if not r["pays_at_median"]]
    return {
        "agent": "reward_risk",
        "hold_sessions": hold_sessions,
        "symbols": rows,
        "below_one_to_one": below,
        "note": ("median, not mean — excursion distributions are skewed by a few "
                 "enormous moves and a mean R:R flatters exactly the names whose "
                 "tails do the work"),
        "does_not": [
            "rank names to trade — that is the multiplicity trap, not a screen",
            "forecast; this describes the past and a name that paid 2:1 can stop",
            "gate anything yet — a gate must beat the current behaviour on the "
            "same decisions before it is wired in",
        ],
    }


def report() -> dict[str, Any]:
    return study()


def _main() -> None:                              # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description="Reward-to-risk per name, both excursions.")
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--hold", type=int, default=DEFAULT_HOLD)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    r = study(args.symbols, args.hold)
    if args.json:
        print(json.dumps(r, indent=2))
        return

    print("=" * 78)
    print(f"  REWARD / RISK — both excursions over {args.hold} sessions from entry")
    print("=" * 78)
    print(f"\n  {'SYM':7}{'N':>6}{'MED MAE':>10}{'MED MFE':>10}{'R:R':>8}"
          f"{'p80 MAE':>10}{'p80 MFE':>10}")
    print("  " + "-" * 74)
    rows = [x for x in r["symbols"] if x.get("available")]
    for x in sorted(rows, key=lambda v: -(v.get("reward_risk") or 0)):
        rr = f"{x['reward_risk']:.2f}" if x["reward_risk"] else "—"
        flag = "" if x["pays_at_median"] else "  <1"
        print(f"  {x['symbol']:7}{x['entries']:>6}{x['median_mae_pct']:>9.1f}%"
              f"{x['median_mfe_pct']:>9.1f}%{rr:>8}"
              f"{x['p80_mae_pct']:>9.1f}%{x['p80_mfe_pct']:>9.1f}%{flag}")

    if r["below_one_to_one"]:
        print(f"\n  PAYS LESS THAN IT COSTS at the median: "
              f"{', '.join(r['below_one_to_one'])}")
        print("  These names only look worthwhile through their tail.")
    print(f"\n  {r['note']}")
    print("  This calibrates size and gating per name. It does NOT rank names to buy.")


if __name__ == "__main__":                        # pragma: no cover
    _main()
