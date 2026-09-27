"""Which symbols the intraday harvest should cover, in connector-sized batches.

WHY THIS IS CODE AND NOT A LIST IN A PROMPT

The harvest runs from a scheduled Claude session reading a markdown prompt. A
hardcoded ticker list in that prompt goes stale the first time the book changes
and nobody notices, because a harvest that quietly covers the wrong names still
exits 0 and still writes bars. Deriving the list from the live book means the
coverage follows the portfolio automatically.

WHY COVERAGE MATTERS MORE THAN IT SOUNDS

Names without a minute bar fall back to the previous daily close, and on
2026-09-08 that fallback was wrong by 9.6% on BE, 4.1% on HOOD and 3.7% on ARM.
A simulator pricing fills at a four-day-old close is not measuring the strategy;
it is measuring the staleness. Every name the sim can trade should be quoted
from the same session it trades in.

PRIORITY, BECAUSE THE BUDGET IS FINITE

Each batch of 20 is one connector call inside one Claude session, so coverage
costs tool calls rather than money. Ordered by how much a wrong price hurts:

    1. focus names      the campaign concentrates here
    2. held positions   exits are graded against these prices
    3. watchlist        entry decisions are priced off these
    4. the rest         only if the budget allows

    python -m app.data.harvest_list
    python -m app.data.harvest_list --max 100 --json
"""
from __future__ import annotations

import argparse
import json

BATCH = 20                     # the connector's per-query symbol ceiling
DEFAULT_MAX = 100              # 5 calls; keeps one harvest inside a short session


def symbols(max_symbols: int = DEFAULT_MAX) -> list[str]:
    """Priority-ordered, de-duplicated, capped."""
    out: list[str] = []
    seen: set[str] = set()

    def add(items) -> None:
        for s in items or []:
            u = str(s).upper().strip()
            if u and u not in seen:
                seen.add(u)
                out.append(u)

    # 1. focus
    try:
        from ..config import settings
        camp = (settings.raw.get("campaign") or {})
        add(camp.get("focus_symbols") or ["MRVL", "NVDA", "TSLA"])
    except Exception:
        add(["MRVL", "NVDA", "TSLA"])

    # 2. held
    try:
        from ..portfolio.holdings import UNTRADEABLE, load_holdings
        held = [h["symbol"] for h in load_holdings()]
        add([s for s in held if str(s).upper() not in set(UNTRADEABLE)])
    except Exception:
        pass

    # 3. watchlist
    try:
        from ..config import settings
        add(settings.watchlist)
    except Exception:
        pass

    # 4. anything else already carrying daily history
    try:
        from .ohlc import coverage
        add(sorted(coverage()))
    except Exception:
        pass

    return out[:max_symbols]


def batches(max_symbols: int = DEFAULT_MAX) -> list[list[str]]:
    syms = symbols(max_symbols)
    return [syms[i:i + BATCH] for i in range(0, len(syms), BATCH)]


def report() -> dict:
    b = batches()
    return {"symbols": sum(len(x) for x in b), "batches": len(b), "batch_size": BATCH,
            "list": b}


def _main() -> None:                              # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description="Symbols for the intraday harvest.")
    ap.add_argument("--max", type=int, default=DEFAULT_MAX)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    b = batches(args.max)
    if args.json:
        print(json.dumps({"batches": b}, indent=2))
        return
    for i, group in enumerate(b, 1):
        print(f"BATCH {i}: {json.dumps(group)}")


if __name__ == "__main__":                        # pragma: no cover
    _main()
