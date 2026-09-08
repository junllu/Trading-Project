"""Discovery — the screener that looks OUTWARD, at names you do not own.

Every other tool here analyses the book. deep_dive explains a holding, thesis
grades one, peer_value ranks one against its group, exit_discipline prices one
that was sold. All of them start from a symbol somebody already chose.

That is the wrong shape for the actual problem, which the operator stated
plainly: signals get missed not because the judgement is poor but because the
judgement is at work during market hours. Missing something is a COVERAGE
failure, and coverage is the one thing software is unambiguously better at than
a person. Judgement is the scarce input; scanning is not.

TWO TIERS, BECAUSE THE DATA HAS TWO COSTS

    TIER 1  free and automatic — cached daily closes. Theme membership and
            state, trend, relative strength, volatility. Runs over the whole
            universe on a clock, unattended.
    TIER 2  needs an MCP pull Claude must run — peer P/E and revenue growth,
            i.e. the operator's PRIMARY screen. Expensive per symbol.

So tier 1 narrows a wide universe cheaply, and tier 2 is requested only for the
survivors. Screening broadly on free data and paying for depth on a short list
is the only shape that keeps the human-gated step small enough to actually
happen — a screener that demands twenty MCP pulls a day will be switched off.

WHAT IT SCREENS FOR — the operator's method, not a generic factor model

    theme is ALIVE          the operator's edge is theme selection, measured in
                            app/intel/themes.py. A name in a rotating theme is
                            not a candidate however good it looks alone.
    above its own trend     alive, not merely cheap
    beating the benchmark   rising with the market is not a reason to act
    volatility routes it    core (>=1yr hold) or the 20% higher-vol sleeve

Deliberately NOT screened on: absolute P/E (meaningless without peers), price
momentum alone (that is how the weed position was averaged into for three
years), or anything requiring a view the system cannot check.

WHAT IT WILL NOT DO

Rank a name as a BUY. It produces a short list and the exact MCP pulls needed to
finish the work. The decision needs the operator's judgement — this exists so
that judgement gets pointed at the right ten names instead of missing them.

    python -m app.analytics.discovery
    python -m app.analytics.discovery --limit 15 --pool options
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics as st
from dataclasses import dataclass, field

from ..config import ROOT

PRICES_DIR = ROOT / "data" / "prices"
BENCHMARK = "SPY"

TREND_WINDOW = 200
RELATIVE_WINDOW = 126
MIN_BARS = 252

# Routing thresholds mirror config/config.yaml's pool definitions: the core pool
# has no volatility filter (you hold thesis names through drawdowns), the
# options sleeve needs enough movement to pay for theta.
OPTIONS_MIN_VOL = 0.45
CORE_MAX_VOL = 1.20        # above this, a >=1yr hold is a different decision


@dataclass
class Candidate:
    symbol: str
    theme: str
    theme_state: str
    last: float
    ret_6m_pct: float
    vs_benchmark_pct: float
    above_trend: bool
    realized_vol: float
    pool: str                        # core | options | neither
    peer_rank: str = ""              # filled only when peer data exists
    has_peer_data: bool = False
    has_fundamentals: bool = False
    reasons: list[str] = field(default_factory=list)
    mcp_pulls: list[str] = field(default_factory=list)
    score: float = 0.0

    def to_dict(self) -> dict:
        return dict(self.__dict__)


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


def _vol(closes: list[float], window: int = 60) -> float:
    c = closes[-(window + 1):]
    if len(c) < 30:
        return 0.0
    rets = [c[i] / c[i - 1] - 1 for i in range(1, len(c)) if c[i - 1]]
    if len(rets) < 2:
        return 0.0
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return round(math.sqrt(var) * math.sqrt(252), 3)


def _pct(closes: list[float], bars: int) -> float | None:
    if len(closes) <= bars:
        return None
    return (closes[-1] / closes[-bars - 1] - 1) * 100


def _universe(include_held: bool = False) -> list[str]:
    syms = {p.stem.upper() for p in PRICES_DIR.glob("*.csv")} if PRICES_DIR.exists() else set()
    syms.discard(BENCHMARK)
    if not include_held:
        try:
            from ..portfolio.holdings import UNTRADEABLE, load_holdings
            syms -= {str(h["symbol"]).upper() for h in load_holdings()}
            syms -= set(UNTRADEABLE)
        except Exception:
            pass
    return sorted(syms)


def _peer_standings() -> dict[str, dict]:
    try:
        from .peer_value import build
        return {s.symbol: {"band": s.band, "vs_median_x": s.vs_median_x,
                           "rank": f"{s.rank}/{s.of}"}
                for g in build() for s in g.standings}
    except Exception:
        return {}


def _fundamental_symbols() -> set[str]:
    from ..intel.onboard import _snapshot_has
    return {s for s in _universe(include_held=True) if _snapshot_has(s, "fundamentals")}


def scan(pool: str | None = None, include_held: bool = False) -> list[Candidate]:
    from ..intel.themes import ALIVE, THEMES, report as themes_report

    tr = themes_report()
    theme_state = {n: s["state"] for n, s in tr["themes"].items()}
    member_of: dict[str, str] = {}
    for name, members in THEMES.items():
        for m in members:
            # A name in several themes is attributed to its healthiest, so an
            # arguable label cannot disqualify a candidate outright.
            cur = member_of.get(m)
            if cur is None or (theme_state.get(name) == ALIVE
                               and theme_state.get(cur) != ALIVE):
                member_of[m] = name

    bench = _closes(BENCHMARK)
    bench_6m = _pct(bench, RELATIVE_WINDOW)
    if bench_6m is None:
        return []

    peers = _peer_standings()
    have_fund = _fundamental_symbols()
    out: list[Candidate] = []

    for sym in _universe(include_held):
        theme = member_of.get(sym)
        # An UNTAGGED name must not be invisible. Theme membership is
        # hand-curated and covers a fraction of the universe, so gating on it
        # would reproduce the exact failure this screener exists to fix —
        # missing something because nobody was looking at it. Untagged names
        # are screened on price evidence alone and labelled as such; only a
        # name in a theme that is actively ROTATING is excluded.
        if theme is not None and theme_state.get(theme) != ALIVE:
            continue
        c = _closes(sym)
        if len(c) < MIN_BARS:
            continue
        r6 = _pct(c, RELATIVE_WINDOW)
        if r6 is None:
            continue

        above = c[-1] > st.mean(c[-TREND_WINDOW:])
        vol = _vol(c)
        rel = round(r6 - bench_6m, 1)

        if vol >= OPTIONS_MIN_VOL and vol <= CORE_MAX_VOL:
            p = "either"
        elif vol >= OPTIONS_MIN_VOL:
            p = "options"
        else:
            p = "core"
        if pool and p not in (pool, "either"):
            continue

        cand = Candidate(
            symbol=sym, theme=theme or "untagged",
            theme_state=theme_state.get(theme, "NO_THEME") if theme else "NO_THEME",
            last=round(c[-1], 2), ret_6m_pct=round(r6, 1), vs_benchmark_pct=rel,
            above_trend=above, realized_vol=vol, pool=p,
            has_peer_data=sym in peers, has_fundamentals=sym in have_fund)

        if theme is None:
            cand.reasons.append("no theme mapping — screened on price evidence only, "
                                "so the operator's theme edge is NOT confirmed here")
        if not above:
            cand.reasons.append("below its own 200d average")
        if rel <= 0:
            cand.reasons.append(f"lagging {BENCHMARK} by {abs(rel):.0f}pp over 6m")
        if sym in peers:
            ps = peers[sym]
            cand.peer_rank = f"{ps['rank']} {ps['band']} ({ps['vs_median_x']:.2f}x median)"
        else:
            cand.mcp_pulls.append(f"get_equity_fundamentals(['{sym}']) — no peer multiple, "
                                  f"so the PRIMARY screen cannot run")
        if sym not in have_fund:
            cand.mcp_pulls.append(f"get_financials(['{sym}'], quarterly, 8) — no growth history")

        # Rank by relative strength while trending, which is the only tier-1
        # evidence available. This is a SORT ORDER, not a conviction score —
        # naming it `score` and treating it as one is how a screen becomes a
        # signal it was never validated as.
        cand.score = round(rel + (10 if above else -25), 1)
        out.append(cand)

    return sorted(out, key=lambda x: -x.score)


def report() -> dict:
    """Agent entrypoint — the short list and what it would cost to finish it."""
    rows = scan()
    top = rows[:10]
    pulls = sorted({p.split(" — ")[0] for c in top for p in c.mcp_pulls})
    return {
        "scanned": len(_universe()),
        "in_live_themes": len(rows),
        "shortlist": [c.symbol for c in top],
        "needs_mcp": pulls[:10],
        "note": ("Tier 1 is free and runs on a clock. Tier 2 — peer multiples and "
                 "growth, the operator's primary screen — needs an MCP pull per "
                 "symbol, so it is requested only for the short list."),
    }


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--pool", choices=["core", "options"], default=None)
    ap.add_argument("--include-held", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = scan(pool=args.pool, include_held=args.include_held)
    if args.json:
        print(json.dumps([c.to_dict() for c in rows[:args.limit]], indent=2))
        return

    universe = len(_universe(args.include_held))
    print("=" * 80)
    print("  DISCOVERY — names you do NOT own, inside themes that are alive")
    print("=" * 80)
    print(f"  universe {universe} cached symbols   ·   {len(rows)} sit in a live theme"
          f"{'   ·   pool=' + args.pool if args.pool else ''}\n")

    if not rows:
        print("  Nothing. Either no theme is alive or no cached name sits in one.")
        print("  That is a legitimate output — most weeks it should be a short list.")
        return

    print(f"  {'sym':6} {'theme':12} {'6m':>7} {'vs SPY':>8} {'vol':>6} {'pool':>8}  "
          f"{'trend':>6}  peer standing")
    print("  " + "-" * 76)
    for c in rows[:args.limit]:
        trend = "above" if c.above_trend else "BELOW"
        peer = c.peer_rank or "— no peer data —"
        print(f"  {c.symbol:6} {c.theme:12} {c.ret_6m_pct:>6.1f}% "
              f"{c.vs_benchmark_pct:>+7.1f}% {c.realized_vol:>6.2f} {c.pool:>8}  "
              f"{trend:>6}  {peer}")

    flagged = [c for c in rows[:args.limit] if c.reasons]
    if flagged:
        print("\n  CAVEATS")
        for c in flagged:
            for r in c.reasons:
                print(f"    {c.symbol:6} {r}")

    pulls = sorted({p for c in rows[:args.limit] for p in c.mcp_pulls})
    if pulls:
        print(f"\n  {'=' * 76}")
        print("  TIER 2 — needs an MCP pull Claude runs; the portal has no broker creds")
        for p in pulls[:12]:
            print(f"    {p}")
        if len(pulls) > 12:
            print(f"    ... and {len(pulls) - 12} more")

    print(f"\n  This is a SHORT LIST, not a buy list. Tier 1 evidence is price-only;")
    print(f"  the primary screen (peer multiple, growth) needs the pulls above.")


if __name__ == "__main__":
    _main()


# --- does the screen actually work? ---------------------------------------

def backtest(top_n: int = 10, hold_bars: int = 63, step_bars: int = 63,
             min_history: int = 300) -> dict:
    """Run the TIER-1 screen at past dates and score what it picked.

    A screener nobody has measured is a list of opinions with a scrollbar. This
    replays the same rules using only bars available at each cutoff, then scores
    the picks against SPY over the following window.

    Honest about what is being tested: with theme mapping covering a fraction of
    the universe, tier 1 reduces in practice to RELATIVE STRENGTH plus a trend
    filter. So this measures a momentum screen — which is what the code does,
    whatever the docstring intends. Peer multiples and growth are tier 2 and are
    NOT tested here, because they need an MCP pull per symbol per date and no
    point-in-time snapshot of them exists.

    Two biases that inflate the result, neither correctable from this data:
      - the universe is today's cached symbols, so delisted names are absent
        (survivorship);
      - the cache is skewed toward large, surviving, mostly-tech names.
    """
    bench = _closes(BENCHMARK)
    series = {s: _closes(s) for s in _universe(include_held=True)}
    series = {s: c for s, c in series.items() if len(c) >= min_history}
    if not bench or not series:
        return {"error": "insufficient price history"}

    n = min(len(bench), min(len(c) for c in series.values()))
    folds, picks_all = [], []
    t = min_history
    while t + hold_bars < n:
        scored = []
        for s, c in series.items():
            hist = c[:t]
            if len(hist) < RELATIVE_WINDOW + 1:
                continue
            r6 = _pct(hist, RELATIVE_WINDOW)
            b6 = _pct(bench[:t], RELATIVE_WINDOW)
            if r6 is None or b6 is None:
                continue
            above = hist[-1] > st.mean(hist[-TREND_WINDOW:]) if len(hist) >= TREND_WINDOW else False
            scored.append((round(r6 - b6, 1) + (10 if above else -25), s))
        if not scored:
            t += step_bars
            continue
        scored.sort(reverse=True)
        picks = [s for _, s in scored[:top_n]]

        fwd = []
        for s in picks:
            c = series[s]
            if t + hold_bars >= len(c) or c[t - 1] <= 0:
                continue
            fwd.append((c[t + hold_bars - 1] / c[t - 1] - 1) * 100)
        b_fwd = (bench[t + hold_bars - 1] / bench[t - 1] - 1) * 100 if bench[t - 1] else 0.0
        if fwd:
            folds.append({"cutoff_bar": t, "n_picks": len(fwd),
                          "picks_pct": round(st.mean(fwd), 2),
                          "bench_pct": round(b_fwd, 2),
                          "excess_pct": round(st.mean(fwd) - b_fwd, 2)})
            picks_all.extend(picks)
        t += step_bars

    if not folds:
        return {"error": "no fold had enough forward data"}
    exc = [f["excess_pct"] for f in folds]
    wins = sum(1 for x in exc if x > 0)
    return {
        "folds": folds,
        "n_folds": len(folds),
        "hold_bars": hold_bars,
        "universe": len(series),
        "mean_excess_pct": round(st.mean(exc), 2),
        "median_excess_pct": round(st.median(exc), 2),
        "folds_beating_benchmark": f"{wins}/{len(folds)}",
        "win_rate_pct": round(100 * wins / len(folds), 1),
        "verdict": (
            "NO EDGE — the screen did not beat holding the benchmark. Use it to "
            "narrow attention, never as a reason to buy."
            if st.mean(exc) <= 0 else
            f"POSITIVE on {len(folds)} folds, median {st.median(exc):+.2f}pp. "
            f"Survivorship-biased upward and n is small — directional, not proof."),
    }
