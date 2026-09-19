"""Market-wide candidate scan — the whole tape, not just the 100 we cache.

WHAT THIS ADDS

`discovery.py` screens the ~150 symbols with local price history, and
`breakout.py` screens the 100 with minute bars. Both are bounded by what has
been harvested. This queries the entire US market in one in-process request, so
a name that was never in the cache can still surface.

THE FILTER THAT MAKES IT USABLE

Unfiltered, this returns garbage. The first query run against it came back with
OTC shells showing 895x relative volume on 11,000 shares — technically a volume
surge, untradeable in practice. So liquidity and venue are filtered BEFORE
anything else, and the thresholds are deliberately blunt rather than tuned.

THE DISCIPLINE IT INHERITS

The underlying endpoint exposes 3,000+ fields. That is an overfitting engine:
searching a space that wide guarantees something looks significant, which is
precisely how 3,208 candlestick looks produced a best result indistinguishable
from selection noise. So this screens on a SMALL, FIXED set chosen for economic
meaning — venue, liquidity, size, relative volume, change — and it produces
CANDIDATES, never ratings. Anything that survives still has to clear the same
measurement everything else does.

    python -m app.analytics.market_scan
    python -m app.analytics.market_scan --min-rvol 3 --json
"""
from __future__ import annotations

import argparse
import json
from typing import Any

# Blunt on purpose. These exist to exclude the untradeable, not to select
# winners — a threshold tuned until the output looks good is a fitted parameter.
MIN_MARKET_CAP = 2_000_000_000
MIN_VOLUME = 1_000_000
MIN_RELATIVE_VOLUME = 2.0
EXCHANGES = ("NASDAQ", "NYSE")


def scan(min_rvol: float = MIN_RELATIVE_VOLUME, limit: int = 25,
         min_cap: float = MIN_MARKET_CAP,
         min_volume: int = MIN_VOLUME) -> dict[str, Any]:
    try:
        from tradingview_screener import Query, col
    except Exception as exc:
        return {"agent": "market_scan", "available": False,
                "why": f"tradingview_screener not installed: {exc}"}

    try:
        matched, df = (Query()
                       .set_markets("america")
                       .select("name", "close", "volume",
                               "relative_volume_10d_calc", "change",
                               "market_cap_basic", "exchange", "sector")
                       .where(col("exchange").isin(list(EXCHANGES)),
                              col("market_cap_basic") > min_cap,
                              col("volume") > min_volume,
                              col("relative_volume_10d_calc") > min_rvol)
                       .order_by("change", ascending=False)
                       .limit(limit)
                       .get_scanner_data())
    except Exception as exc:
        # An undocumented endpoint is allowed to fail; it must not take a
        # scheduled job down with it.
        return {"agent": "market_scan", "available": False,
                "why": f"query failed: {type(exc).__name__}: {exc}"}

    rows = []
    for _, r in df.iterrows():
        try:
            rows.append({
                "symbol": str(r.get("name") or "").upper(),
                "exchange": r.get("exchange"),
                "close": round(float(r.get("close") or 0), 2),
                "change_pct": round(float(r.get("change") or 0), 2),
                "rel_volume": round(float(r.get("relative_volume_10d_calc") or 0), 2),
                "volume": int(r.get("volume") or 0),
                "market_cap_b": round(float(r.get("market_cap_basic") or 0) / 1e9, 2),
                "sector": r.get("sector"),
            })
        except (TypeError, ValueError):
            continue

    held = set()
    try:
        from ..intel.onboard import held_symbols
        held = set(held_symbols())
    except Exception:
        pass

    return {
        "agent": "market_scan",
        "available": True,
        "matched": matched,
        "returned": len(rows),
        "filters": {"exchanges": list(EXCHANGES), "min_market_cap": min_cap,
                    "min_volume": min_volume, "min_relative_volume": min_rvol},
        "candidates": rows,
        "already_held": sorted({r["symbol"] for r in rows} & held),
        "does_not": [
            "rate anything — these are conditions that are true right now",
            "mine the 3,000+ available fields; a small fixed screen is the "
            "whole defence against finding significance in noise",
        ],
    }


def report() -> dict[str, Any]:
    return scan()


def _main() -> None:                              # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description="Market-wide candidate scan.")
    ap.add_argument("--min-rvol", type=float, default=MIN_RELATIVE_VOLUME)
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    r = scan(args.min_rvol, args.limit)
    if args.json:
        print(json.dumps(r, indent=2))
        return
    if not r.get("available"):
        print(f"unavailable: {r['why']}")
        return

    print("=" * 78)
    print(f"  MARKET SCAN — {r['matched']} name(s) match, showing {r['returned']}")
    print("=" * 78)
    f = r["filters"]
    print(f"  {'/'.join(f['exchanges'])} · cap > ${f['min_market_cap']/1e9:.0f}B · "
          f"vol > {f['min_volume']:,} · rel-vol > {f['min_relative_volume']}")
    print(f"\n  {'SYM':8}{'CLOSE':>9}{'CHG':>8}{'RVOL':>7}{'CAP $B':>9}   SECTOR")
    print("  " + "-" * 74)
    for c in r["candidates"]:
        mark = " *" if c["symbol"] in r["already_held"] else ""
        print(f"  {c['symbol']:8}{c['close']:>9.2f}{c['change_pct']:>7.1f}%"
              f"{c['rel_volume']:>7.2f}{c['market_cap_b']:>9.1f}   "
              f"{str(c['sector'] or '')[:26]}{mark}")
    if r["already_held"]:
        print(f"\n  * already held: {', '.join(r['already_held'])}")
    print("\n  Candidates, not ratings. Presence means a condition is true now.")


if __name__ == "__main__":                        # pragma: no cover
    _main()
