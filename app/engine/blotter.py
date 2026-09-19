"""The blotter — a durable record of every order the executor resolved.

WHY THIS EXISTS

`Executor.history` is a list on a dataclass. It lives in one process's memory,
it is truncated to 25 rows on the dashboard, and it dies with the server. That
is not a track record; it is a status light. The consequence was visible on the
dashboard: "1 / 20 orders today" with no way to say what the order WAS, and no
way at all to ask how the simulated book has performed over a week — because by
then the evidence had been garbage-collected.

A simulation you cannot audit afterwards is not evidence, it is theatre. The
program manager's own audit names this as the binding constraint: 27 forward
record rows, calibration impossible, every backtested claim unsupported
out-of-sample. Paper runs only pay that debt down if the fills survive a
restart.

WHAT IT RECORDS, AND WHAT IT REFUSES TO

Recorded: one append-only JSONL row per order, at the point it reaches a
terminal state — filled or submitted at the broker, rejected by the risk
manager, or cancelled because a human declined it at the approval gate. A
queued order gets its row when it resolves, so nothing is counted twice.
Rejections and declines are kept deliberately: what the strategy wanted and the
guardrail (or the operator) refused is evidence. Keeping only the orders that
got through is how a simulation acquires an imaginary hit rate.

Refused: any notion of "performance" that requires marking open positions to
market. `realized()` matches sells against buys FIFO and reports CLOSED
round-trips only. Unrealized P&L belongs to the portfolio view, which has live
quotes; computing it here from stale fill prices would produce a number that
looks like a result and is not one.

The blotter never decides anything. It is written by one choke point
(`Executor._submit`) and read by analysis.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from typing import Any, Iterable

from ..config import ROOT

PATH = ROOT / "data" / "paper_blotter.jsonl"


def _thesis_for(order) -> dict[str, Any] | None:
    """The stated case for the trade, as it stood at the fill.

    For a BUY that opened a position: the exit plan it was opened on — the stop,
    where that stop came from, and the scale-out targets. For a SELL: the rule
    that fired, which the order's own reason already carries.
    """
    try:
        side = getattr(getattr(order, "side", None), "value", None)
        if side != "buy":
            return None
        from ..agent import position_plans
        p = position_plans.get(getattr(order, "symbol", ""))
        if not p:
            return None
        return {
            "stop_pct": p.get("stop_pct"),
            "stop_price": p.get("stop_price"),
            "stop_basis": p.get("stop_basis"),
            "targets_pct": [t.get("gain_pct") for t in (p.get("targets") or [])],
            "pool": p.get("pool"),
        }
    except Exception:
        return None


def record(order, mode: str, ref_price: float | None = None) -> dict[str, Any]:
    """Append one order to the blotter. Never raises into the execution path.

    Execution must not fail because bookkeeping failed. A blotter write that
    throws would turn a successful fill into an exception at the call site, so
    every error here is swallowed after being stamped into the returned row.
    """
    row = {
        "ts": time.time(),
        "when": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "id": getattr(order, "id", None),
        "symbol": getattr(order, "symbol", None),
        "side": getattr(getattr(order, "side", None), "value", None),
        "quantity": getattr(order, "quantity", None),
        "status": getattr(getattr(order, "status", None), "value", None),
        "filled_price": getattr(order, "filled_price", None),
        "reason": getattr(order, "reason", None),
        "strategy": getattr(order, "strategy", None) or "unknown",
        "broker": getattr(order, "broker", None),
        "mode": mode,
        # Stamped on every row so a simulated fill can never be read as a real
        # one. Mode alone is not enough: a row saying mode="live" in this file
        # would still be a simulated fill, because this file IS the training
        # record. The flag makes the row self-describing if it is ever copied,
        # merged or exported away from its filename.
        "simulated": mode != "live",
        "ref_price": ref_price,
    }
    # Dollar value of the order. Derivable from qty x price, stored anyway so
    # every consumer computes it the same way — a row where one reader used the
    # fill and another the reference price is a reconciliation bug waiting.
    try:
        q, px = row.get("quantity"), row.get("filled_price") or ref_price
        row["amount"] = round(float(q) * float(px), 2) if q and px else None
    except (TypeError, ValueError):
        row["amount"] = None
    # The plan the position was opened on, captured at the fill. Without it the
    # record says WHAT was traded and never WHY, and the thesis has to be
    # reconstructed from memory later — which is how a strategy quietly becomes
    # whatever its author now believes it was.
    row["thesis"] = _thesis_for(order)
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        with PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception as exc:                      # pragma: no cover - disk failure
        row["_write_error"] = f"{type(exc).__name__}: {exc}"
    return row


def rows(since_ts: float | None = None) -> list[dict[str, Any]]:
    """Every recorded order, oldest first. A corrupt line is skipped, not fatal."""
    if not PATH.exists():
        return []
    out: list[dict[str, Any]] = []
    with PATH.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since_ts is not None and float(r.get("ts") or 0) < since_ts:
                continue
            out.append(r)
    return out


def _fills(rs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rs
            if r.get("status") == "filled"
            and isinstance(r.get("filled_price"), (int, float))
            and isinstance(r.get("quantity"), (int, float))]


def realized(rs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """FIFO-matched realized P&L over CLOSED round-trips only.

    A sell with no prior buy in the record is reported as `unmatched_sell`
    rather than being priced against zero. Positions seeded into the paper
    broker from holdings.yaml were never bought *here*, so pretending their
    cost basis is 0 would manufacture enormous fake profits — precisely the
    kind of number that then gets quoted as evidence.
    """
    rs = rows() if rs is None else rs
    lots: dict[str, deque] = defaultdict(deque)
    per_symbol: dict[str, dict[str, float]] = defaultdict(
        lambda: {"realized_usd": 0.0, "round_trips": 0, "unmatched_sell_qty": 0.0})

    for r in _fills(rs):
        sym, side = r["symbol"], r["side"]
        qty, px = float(r["quantity"]), float(r["filled_price"])
        if side == "buy":
            lots[sym].append([qty, px])
            continue
        if side != "sell":
            continue
        remaining = qty
        while remaining > 1e-12 and lots[sym]:
            lot = lots[sym][0]
            take = min(remaining, lot[0])
            per_symbol[sym]["realized_usd"] += take * (px - lot[1])
            lot[0] -= take
            remaining -= take
            if lot[0] <= 1e-12:
                lots[sym].popleft()
                per_symbol[sym]["round_trips"] += 1
        if remaining > 1e-12:
            per_symbol[sym]["unmatched_sell_qty"] += remaining

    total = sum(v["realized_usd"] for v in per_symbol.values())
    unmatched = {s: v["unmatched_sell_qty"] for s, v in per_symbol.items()
                 if v["unmatched_sell_qty"] > 1e-12}
    return {
        "realized_usd": round(total, 2),
        "round_trips": sum(int(v["round_trips"]) for v in per_symbol.values()),
        "by_symbol": {s: {"realized_usd": round(v["realized_usd"], 2),
                          "round_trips": int(v["round_trips"])}
                      for s, v in sorted(per_symbol.items())},
        "unmatched_sells": unmatched,
        "open_lots": {s: round(sum(l[0] for l in q), 4)
                      for s, q in sorted(lots.items()) if q},
        "note": ("Closed round-trips only. Seeded holdings have no buy in this "
                 "record, so their sells appear as unmatched rather than as profit."),
    }


def by_strategy(rs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Per-sleeve attribution — the number that says which engine is working.

    Two engines route through one executor: the conviction/campaign agent and
    the config-driven technical strategies. Their fills share a blotter and,
    in the paper broker, share inventory. So there are two different questions
    and they need two different sums:

      book-level `realized()`   what the simulated account actually made,
                                FIFO across every fill regardless of origin.
      per-strategy here         what a sleeve made ON ITS OWN, matching each
                                sleeve's sells only against its own buys.

    The second is attribution, not account P&L, and the two will not add up to
    each other whenever one sleeve sells what another bought. Reported apart so
    that neither gets quoted as the other.
    """
    rs = rows() if rs is None else rs
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rs:
        groups[str(r.get("strategy") or "unknown")].append(r)
    out = {}
    for name, grp in sorted(groups.items()):
        fills = _fills(grp)
        out[name] = {
            "orders": len(grp),
            "fills": len(fills),
            "filled_notional_usd": round(
                sum(float(f["quantity"]) * float(f["filled_price"]) for f in fills), 2),
            "attributed": realized(grp),
        }
    return out


def summary() -> dict[str, Any]:
    """What the simulated book has actually done, durably."""
    rs = rows()
    fills = _fills(rs)
    by_status: dict[str, int] = defaultdict(int)
    for r in rs:
        by_status[str(r.get("status"))] += 1
    notional = sum(float(r["quantity"]) * float(r["filled_price"]) for r in fills)
    return {
        "path": str(PATH),
        "orders_recorded": len(rs),
        "fills": len(fills),
        "by_status": dict(sorted(by_status.items())),
        "filled_notional_usd": round(notional, 2),
        "first": rs[0]["when"] if rs else None,
        "last": rs[-1]["when"] if rs else None,
        "realized": realized(rs),
        "by_strategy": by_strategy(rs),
    }


def _main() -> None:                              # pragma: no cover - CLI
    import argparse
    ap = argparse.ArgumentParser(description="Paper blotter — the durable fill record.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--tail", type=int, default=15)
    args = ap.parse_args()
    s = summary()
    if args.json:
        print(json.dumps(s, indent=2))
        return
    print("=" * 78)
    print("  PAPER BLOTTER — every order the executor resolved")
    print("=" * 78)
    print(f"\n  recorded  {s['orders_recorded']} order(s) · {s['fills']} fill(s)"
          f" · ${s['filled_notional_usd']:,.0f} traded")
    print(f"  window    {s['first'] or '—'}  ->  {s['last'] or '—'}")
    print(f"  status    {s['by_status'] or '—'}")
    r = s["realized"]
    print(f"\n  REALIZED  ${r['realized_usd']:,.2f} over {r['round_trips']} closed round-trip(s)")
    for sym, v in r["by_symbol"].items():
        if v["round_trips"]:
            print(f"            {sym:6} ${v['realized_usd']:>10,.2f}  ({v['round_trips']} rt)")
    if r["unmatched_sells"]:
        print(f"  unmatched sells (seeded holdings, no cost basis here): "
              f"{', '.join(r['unmatched_sells'])}")

    bs = s["by_strategy"]
    if bs:
        print("\n  BY SLEEVE (attribution: each sleeve matched against its own buys,")
        print("             so these do NOT sum to the book figure above)")
        for name, v in bs.items():
            a = v["attributed"]
            print(f"    {name:16} {v['fills']:>3} fill(s)  "
                  f"${v['filled_notional_usd']:>10,.0f} traded  "
                  f"attributed ${a['realized_usd']:>9,.2f} over {a['round_trips']} rt")
    tail = rows()[-args.tail:]
    if tail:
        print(f"\n  LAST {len(tail)}")
        for x in tail:
            px = f"@{x['filled_price']:.2f}" if isinstance(x.get("filled_price"), (int, float)) else ""
            print(f"    {x['when']}  {x['symbol']:6} {str(x['side']):4} "
                  f"{x['quantity']:>10}  {x['status']:<10}{px}  {x.get('reason') or ''}")


if __name__ == "__main__":                        # pragma: no cover
    _main()
