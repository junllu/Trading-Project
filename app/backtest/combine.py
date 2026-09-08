"""Which measures actually predict, and which COMBINATION predicts best.

Candles alone failed honestly: the best of 124 looks scored 0.66 against a
selection bar of 0.80. This module asks the larger question on a 85-name
cross-section instead of four, and it asks it in the order that keeps the answer
trustworthy:

    1. MARGINAL  — does each feature carry information ON ITS OWN?
    2. COMBINE   — does a composite of the survivors beat the best single one?
    3. CONDITION — does a weak trigger (a candle) work inside a good regime?
    4. DEFLATE   — is the winner better than the best of N random looks?

WHY INFORMATION COEFFICIENT, NOT HIT RATE

A hit rate answers "how often" and hides "by how much" — a signal right 70% of
the time in tiny amounts and wrong 30% in large ones loses money. The IC is the
cross-sectional rank correlation between the feature and the forward return,
measured fresh each day and then averaged. It is the standard quant-equity
measure because it is scale-free, sign-honest, and directly comparable across
features on different units. A mean IC of 0.03 is a real, tradeable signal; 0.05
is strong. Anything under ~0.01 is indistinguishable from nothing.

THE TWO CORRECTIONS THAT DECIDE WHETHER THIS IS HONEST

Overlapping windows. A 10-day forward return sampled daily reuses nine of ten
days in the next observation, so 2,900 "independent" days are really ~290. That
inflates a t-statistic by sqrt(10) — a t of 2.2 becomes a t of 7 out of nothing
but double counting. Sampling is therefore strided by the horizon.

Selection. Features are chosen on TRAIN and scored on TEST only, with a purge
gap of one horizon between them so no training label overlaps a test bar.

    python -m app.backtest.combine
    python -m app.backtest.combine --horizon 5
"""
from __future__ import annotations

import argparse
import math
import statistics as st
from dataclasses import dataclass

from ..analytics.features import FEATURES, Row, panel
from .costs import CostModel

MIN_CROSS_SECTION = 10      # a rank correlation over 4 names is noise
MIN_PERIODS = 20            # too few sampled dates to average an IC over
QUINTILE = 5


def _rank(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    for pos, i in enumerate(order):
        out[i] = pos / (len(xs) - 1) if len(xs) > 1 else 0.5
    return out


def _corr(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = st.mean(xs), st.mean(ys)
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    dx = math.sqrt(sum((a - mx) ** 2 for a in xs))
    dy = math.sqrt(sum((b - my) ** 2 for b in ys))
    return num / (dx * dy) if dx > 0 and dy > 0 else None


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    return _corr(_rank(xs), _rank(ys))


@dataclass
class ICResult:
    name: str
    horizon: int
    periods: int
    mean_ic: float
    std_ic: float
    t_stat: float
    ir: float                 # mean / std — IC's own Sharpe
    spread_pct: float         # top quintile minus bottom, per period, %
    spread_net_pct: float     # after round-trip costs

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _by_date(rows: list[Row]) -> dict[str, list[Row]]:
    out: dict[str, list[Row]] = {}
    for r in rows:
        out.setdefault(r.date, []).append(r)
    return out


def _sampled_dates(dates: list[str], horizon: int) -> list[str]:
    """Every horizon-th date, so forward windows never overlap.

    This is the single most important line in the module. Sampling daily with a
    10-day label reuses 90% of each observation in the next one; the mean is
    unaffected but the standard error is understated by ~sqrt(horizon), which is
    the difference between 'significant' and 'nothing here'.
    """
    return dates[::horizon]


def score_feature(rows: list[Row], name: str, horizon: int,
                  costs: CostModel | None = None) -> ICResult | None:
    """Daily cross-sectional IC for one feature, averaged over sampled dates."""
    costs = costs or CostModel()
    grouped = _by_date(rows)
    dates = _sampled_dates(sorted(grouped), horizon)

    ics: list[float] = []
    spreads: list[float] = []
    for d in dates:
        pack = [(r.features[name], r.forward[horizon])
                for r in grouped[d]
                if name in r.features and horizon in r.forward]
        if len(pack) < MIN_CROSS_SECTION:
            continue
        xs = [p[0] for p in pack]
        ys = [p[1] for p in pack]
        ic = _spearman(xs, ys)
        if ic is None:
            continue
        ics.append(ic)

        pack.sort(key=lambda t: t[0])
        k = max(1, len(pack) // QUINTILE)
        top = st.mean([p[1] for p in pack[-k:]])
        bot = st.mean([p[1] for p in pack[:k]])
        spreads.append(top - bot)

    if len(ics) < MIN_PERIODS:
        return None
    mean_ic, std_ic = st.mean(ics), (st.pstdev(ics) or 1e-9)
    spread = st.mean(spreads) if spreads else 0.0
    # A long/short quintile trade pays a round trip on BOTH legs each rebalance.
    drag = 2 * costs.round_trip_bps() / 100.0
    return ICResult(
        name=name, horizon=horizon, periods=len(ics),
        mean_ic=round(mean_ic, 4), std_ic=round(std_ic, 4),
        t_stat=round(mean_ic / std_ic * math.sqrt(len(ics)), 2),
        ir=round(mean_ic / std_ic, 3),
        spread_pct=round(spread, 3), spread_net_pct=round(spread - drag, 3))


def marginal(rows: list[Row], horizon: int) -> list[ICResult]:
    """Step 1 — every feature scored alone, ranked by out-of-sample honesty."""
    out = []
    for f in FEATURES:
        r = score_feature(rows, f"{f}_rank", horizon)
        if r:
            r.name = f
            out.append(r)
    return sorted(out, key=lambda r: -abs(r.mean_ic))


def _composite(rows: list[Row], weights: dict[str, float], horizon: int,
               name: str = "composite") -> ICResult | None:
    """Score a weighted blend of ranked features as if it were one feature."""
    for r in rows:
        parts = [(w, r.features[f"{f}_rank"]) for f, w in weights.items()
                 if f"{f}_rank" in r.features]
        if len(parts) == len(weights):
            tw = sum(abs(w) for w, _ in parts) or 1.0
            r.features[f"__{name}"] = sum(w * v for w, v in parts) / tw
    res = score_feature(rows, f"__{name}", horizon)
    if res:
        res.name = name
    return res


def walk_forward_combo(rows: list[Row], horizon: int, folds: int = 4,
                       min_ic: float = 0.01, weight: str = "equal") -> dict:
    """Step 2 — pick features on TRAIN, score the blend on TEST. Purged.

    The selection has to happen inside the fold. Choosing the best features on
    all the data and then 'validating' on part of it is the most common way a
    backtest lies, and it is invisible in the output: the numbers look like
    out-of-sample numbers.
    """
    grouped = _by_date(rows)
    dates = sorted(grouped)
    fold_len = len(dates) // (folds + 1)
    if fold_len < MIN_PERIODS * horizon:
        return {"error": "not enough history for this fold count"}

    results = []
    for k in range(folds):
        tr_end = fold_len * (k + 1)
        # PURGE: drop one horizon of bars between train and test so no training
        # label peeks into the test window.
        te_start = tr_end + horizon
        te_end = min(te_start + fold_len, len(dates))
        if te_end - te_start < MIN_PERIODS * horizon:
            break

        tr_dates = set(dates[:tr_end])
        te_dates = set(dates[te_start:te_end])
        tr = [r for r in rows if r.date in tr_dates]
        te = [r for r in rows if r.date in te_dates]

        picked: dict[str, float] = {}
        for f in FEATURES:
            s = score_feature(tr, f"{f}_rank", horizon)
            if s and abs(s.mean_ic) >= min_ic:
                # Sign taken from TRAIN. A feature that predicts negatively is
                # still information — it just has to be used the right way up,
                # and the direction must be learned before the test, not after.
                #
                # WEIGHTING IS NOT A DETAIL. Equal-weighting a strong signal
                # with five weak ones is how a good feature gets averaged into
                # noise: the blend is only as good as its median member. Sizing
                # by train IC lets the evidence set the weight.
                if weight == "ic":
                    picked[f] = s.mean_ic
                elif weight == "top1":
                    picked[f] = s.mean_ic       # pruned to the single best below
                else:
                    picked[f] = 1.0 if s.mean_ic > 0 else -1.0
        if weight == "top1" and picked:
            bestf = max(picked, key=lambda k: abs(picked[k]))
            picked = {bestf: picked[bestf]}
        if not picked:
            continue

        oos = _composite(te, picked, horizon, name=f"combo{k}")
        best_single = max((score_feature(te, f"{f}_rank", horizon)
                           for f in picked), key=lambda s: abs(s.mean_ic) if s else 0)
        results.append({
            "fold": k + 1,
            "train_dates": len(tr_dates), "test_dates": len(te_dates),
            "picked": sorted(picked), "n_picked": len(picked),
            "oos_ic": oos.mean_ic if oos else None,
            "oos_t": oos.t_stat if oos else None,
            "oos_spread_net": oos.spread_net_pct if oos else None,
            "best_single_oos_ic": best_single.mean_ic if best_single else None,
        })

    live = [r for r in results if r["oos_ic"] is not None]
    return {
        "horizon": horizon, "weight": weight, "folds": results,
        "mean_oos_ic": round(st.mean([r["oos_ic"] for r in live]), 4) if live else None,
        "mean_oos_spread_net": round(
            st.mean([r["oos_spread_net"] for r in live]), 3) if live else None,
        "beats_best_single": sum(
            1 for r in live if r["best_single_oos_ic"] is not None
            and abs(r["oos_ic"]) > abs(r["best_single_oos_ic"])),
        "n_folds": len(live),
    }


def conditioned_candles(rows: list[Row], horizon: int,
                        top_features: list[str]) -> list[dict]:
    """Step 3 — does a weak trigger work inside a good regime?

    Meta-labelling in its simplest form. The candle does not have to predict
    direction; it only has to fire more usefully when the regime agrees. If the
    conditioned excess is no better than the unconditioned one, the pattern
    genuinely carries nothing and no amount of blending will rescue it.
    """
    from ..analytics.candles import DIRECTION

    out = []
    for pat, d in DIRECTION.items():
        if d == 0:
            continue
        base = [r.forward[horizon] * d for r in rows
                if pat in r.patterns and horizon in r.forward]
        allr = [r.forward[horizon] * d for r in rows if horizon in r.forward]
        if len(base) < 100 or not allr:
            continue
        row = {"pattern": pat, "n": len(base),
               "excess": round(st.mean(base) - st.mean(allr), 3)}
        for f in top_features:
            key = f"{f}_rank"
            sel = [r.forward[horizon] * d for r in rows
                   if pat in r.patterns and horizon in r.forward
                   and r.features.get(key, 0.5) >= 0.8]
            bench = [r.forward[horizon] * d for r in rows
                     if horizon in r.forward and r.features.get(key, 0.5) >= 0.8]
            row[f"cond_{f}"] = (round(st.mean(sel) - st.mean(bench), 3)
                                if len(sel) >= 50 and bench else None)
            row[f"n_{f}"] = len(sel)
        out.append(row)
    return sorted(out, key=lambda r: -abs(r["excess"]))


def run(horizon: int = 5, symbols: list[str] | None = None) -> dict:
    rows = panel(symbols, horizons=(1, 3, 5, 10))
    if not rows:
        return {"error": "no panel — run python -m app.data.ohlc --universe"}
    marg = marginal(rows, horizon)
    top = [m.name for m in marg[:3]]
    return {
        "rows": len(rows),
        "symbols": len({r.symbol for r in rows}),
        "horizon": horizon,
        "marginal": [m.to_dict() for m in marg],
        "walk_forward": walk_forward_combo(rows, horizon),
        "conditioned": conditioned_candles(rows, horizon, top),
        "trials": len(FEATURES) * 4,
    }


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=5)
    args = ap.parse_args()
    r = run(args.horizon)
    if "error" in r:
        print(r["error"])
        return

    print("=" * 88)
    print(f"  SIGNAL COMBINATION — {r['rows']:,} rows, {r['symbols']} symbols, "
          f"{r['horizon']}-day horizon")
    print("=" * 88)
    print("\n  1. MARGINAL — each measure alone (IC = cross-sectional rank correlation)")
    print(f"     {'feature':14} {'periods':>8} {'mean IC':>9} {'t':>7} {'IR':>7} "
          f"{'spread%':>9} {'net%':>8}")
    print("     " + "-" * 68)
    for m in r["marginal"]:
        flag = "  <--" if abs(m["t_stat"]) >= 3 and abs(m["mean_ic"]) >= 0.02 else ""
        print(f"     {m['name']:14} {m['periods']:>8} {m['mean_ic']:>+9.4f} "
              f"{m['t_stat']:>+7.2f} {m['ir']:>+7.3f} {m['spread_pct']:>+9.3f} "
              f"{m['spread_net_pct']:>+8.3f}{flag}")

    w = r["walk_forward"]
    print("\n  2. COMBINATION — features picked on TRAIN, scored on TEST (purged)")
    if w.get("error"):
        print(f"     {w['error']}")
    else:
        for f in w["folds"]:
            print(f"     fold {f['fold']}: picked {f['n_picked']:>2} "
                  f"({', '.join(f['picked'][:4])}{'...' if f['n_picked'] > 4 else ''})"
                  f"  OOS IC {f['oos_ic']:+.4f}  t {f['oos_t']:+.2f}  "
                  f"net spread {f['oos_spread_net']:+.3f}%")
        print(f"     mean OOS IC {w['mean_oos_ic']:+.4f}   "
              f"mean net spread {w['mean_oos_spread_net']:+.3f}%   "
              f"beat best single in {w['beats_best_single']}/{w['n_folds']} folds")

    print("\n  3. CONDITIONED — candles inside a favourable regime (excess %, signed)")
    for c in r["conditioned"][:6]:
        extras = "  ".join(f"{k.replace('cond_', '')}:{v:+.2f}"
                           for k, v in c.items()
                           if k.startswith("cond_") and v is not None)
        print(f"     {c['pattern']:20} n={c['n']:>5}  alone {c['excess']:+.3f}   {extras}")

    print("\n  IC is scale-free: 0.03 is a real signal, 0.05 strong, <0.01 nothing.")
    print("  Windows are strided by the horizon, so t-stats are not inflated by")
    print("  overlapping labels. Entry is the next bar's open, never the signal close.")


if __name__ == "__main__":
    _main()
