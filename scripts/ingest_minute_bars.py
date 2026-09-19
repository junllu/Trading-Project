"""Ingest a Webull MCP bar payload into the local minute store.

Exists because the harvesting session cannot ingest bars itself: the MCP result
for a few hundred minute bars is hundreds of kilobytes, and pulling that through
a model's context to re-emit it as CSV is both expensive and a chance to corrupt
it. The session fetches, the payload lands on disk, and this reads it directly.

Accepts the raw tool-result file (BOM tolerated) or stdin, in either the
grouped shape {"result":[{"symbol":..., "result":[bars]}]} or a bare bar list.

    python scripts/ingest_minute_bars.py <payload.json>
    python scripts/ingest_minute_bars.py <payload.json> --symbol MRVL
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data.minute import coverage, ingest  # noqa: E402


def _groups(payload, forced_symbol: str | None):
    if isinstance(payload, dict) and isinstance(payload.get("result"), list):
        for g in payload["result"]:
            if isinstance(g, dict) and "result" in g:
                yield g.get("symbol") or forced_symbol, g.get("result") or [], g.get("delay_minutes")
            elif forced_symbol:
                yield forced_symbol, payload["result"], None
                return
    elif isinstance(payload, list) and forced_symbol:
        yield forced_symbol, payload, None


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest Webull minute bars into data/minute.")
    ap.add_argument("payload", nargs="?", help="path to the JSON payload; omit to read stdin")
    ap.add_argument("--symbol", help="symbol, when the payload does not name one")
    args = ap.parse_args()

    raw = (Path(args.payload).read_text(encoding="utf-8-sig") if args.payload
           else sys.stdin.read())
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"ERROR: payload is not JSON ({exc})")
        return 1

    seen = 0
    for sym, rows, delay in _groups(payload, args.symbol):
        if not sym or not rows:
            continue
        seen += 1
        r = ingest(sym, rows)
        stale = "" if delay in (0, None) else f"  delay={delay}min"
        print(f"  {r['symbol']:6} received={r['received']:<5} valid={r['valid']:<5} "
              f"added={r['added']:<5} total={r['total']:<7} last={r['last']}{stale}")
        if r["rejected"]:
            # Dropped bars are reported, never repaired — a bar whose open sits
            # outside its own high/low reads as an extraordinary signal to a
            # pattern detector rather than as the bad data it is.
            print(f"         {r['rejected']} bar(s) rejected as malformed")

    if not seen:
        print("ERROR: no symbol groups found in payload")
        return 1

    print("\n  coverage:")
    for k, v in coverage().items():
        print(f"    {k:6} bars={v['bars']:<7} days={v['days']:<4} last={v['last']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
