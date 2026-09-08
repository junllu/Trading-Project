"""Score a curated source's track record against actual price action.

The point of the feed store is not to collect opinions — it's to find out
whether an opinion is worth weighting. This scores every dated view in the
store against what the named symbol actually did afterwards.

Method (deliberately strict, to avoid flattering the source):
  - entry price = first close ON OR AFTER the view's publish date, so a call
    can never be credited with a move that already happened
  - forward returns measured at fixed horizons from that entry
  - "hit" = the move went the direction the view implied (sign of bias)

Read the caveats it prints. A high hit rate on a handful of calls is not
evidence of edge, and views recorded from a source's own "Highlights" are
self-selected toward their memorable wins.

    python -m app.intel.score_source --source serenity
    python -m app.intel.score_source --source serenity --horizons 30,90,180
"""
from __future__ import annotations

import argparse
import statistics as st

from .feeds import KNOWN_SOURCES, FeedStore


def score(source: str, horizons: tuple[int, ...] = (30, 90), start: str = "2025-01-01") -> dict:
    import warnings

    import pandas as pd
    import yfinance as yf
    warnings.filterwarnings("ignore")

    views = [v for v in FeedStore().all_views() if v.source == source]
    if not views:
        return {"source": source, "views": 0}
    symbols = sorted({v.symbol for v in views})
    px = yf.download(symbols, start=start, progress=False, auto_adjust=True)["Close"]
    if isinstance(px, pd.Series):
        px = px.to_frame(symbols[0])

    rows = []
    for v in views:
        if v.symbol not in px.columns:
            continue
        s = px[v.symbol].dropna()
        d = pd.Timestamp(v.published)
        fwd = s[s.index >= d]
        if len(fwd) < 2:
            continue
        entry = fwd.iloc[0]
        rec = {"date": v.published, "symbol": v.symbol, "bias": v.bias,
               "confidence": v.confidence}
        for h in horizons:
            w = fwd[fwd.index <= d + pd.Timedelta(days=h)]
            rec[f"r{h}"] = (w.iloc[-1] / entry - 1) * 100 if len(w) > 1 else None
        rec["to_date"] = (s.iloc[-1] / entry - 1) * 100
        rows.append(rec)

    out = {"source": source, "views": len(views), "scored": len(rows), "rows": rows}
    for h in list(horizons) + ["to_date"]:
        key = f"r{h}" if h != "to_date" else "to_date"
        vals = [(r[key], r["bias"]) for r in rows if r.get(key) is not None]
        if not vals:
            continue
        # directional hit: move agreed with the sign of the stated bias
        hits = [1 for ret, b in vals if (ret > 0) == (b > 0)]
        rets = [ret for ret, _ in vals]
        out[key] = {"n": len(vals), "hit_rate_pct": round(100 * len(hits) / len(vals), 1),
                    "mean_pct": round(st.mean(rets), 2),
                    "median_pct": round(st.median(rets), 2)}
    return out


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, choices=KNOWN_SOURCES)
    ap.add_argument("--horizons", default="30,90")
    ap.add_argument("--start", default="2025-01-01")
    args = ap.parse_args()
    hz = tuple(int(x) for x in args.horizons.split(",") if x.strip())
    res = score(args.source, hz, args.start)

    if not res.get("scored"):
        print(f"{args.source}: nothing scoreable ({res.get('views', 0)} views stored)")
        return
    print(f"{res['source']}: {res['scored']} of {res['views']} views scored")
    for r in sorted(res["rows"], key=lambda x: x["date"]):
        cols = "  ".join(f"{k}={r[k]:+7.1f}%" for k in r if k.startswith("r") and r[k] is not None)
        print(f"  {r['date']}  {r['symbol']:6} bias={r['bias']:+.2f}  {cols}  "
              f"to_date={r['to_date']:+.1f}%")
    print()
    # Select the summary blocks by TYPE, not by name. The old prefix test also
    # matched "rows" — the per-view list — and crashed on the first field
    # access. A structural check cannot be broken by adding another r-key.
    for k, s in res.items():
        if not isinstance(s, dict) or "hit_rate_pct" not in s:
            continue
        print(f"  {k:8} n={s['n']:3}  hit={s['hit_rate_pct']:5.1f}%  "
              f"mean={s['mean_pct']:+7.2f}%  median={s['median_pct']:+7.2f}%")
    print("\nCAVEATS: small n is not evidence of edge; views taken from a source's own "
          "Highlights are self-selected toward wins; sector *maps* scored as directional "
          "calls understate a source that never told you to buy them.")


if __name__ == "__main__":
    _main()
