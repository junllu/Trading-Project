"""Live session runner — real quotes, real session boundaries, no auto-execution.

Wakes on actual market sessions rather than wall-clock guesses: refreshes quotes
at the open, re-evaluates on an interval through the day, and snapshots at the
close. Every cycle appends a timestamped row to data/forward_record.jsonl.

It does NOT place orders, and that is the point rather than a limitation:

  - CLAUDE.md requires explicit human approval for every order.
  - Walk-forward validation put the conviction engine at a 21% win rate against
    buy-and-hold across 34 out-of-sample folds, with a ~21pp overfit gap.
    Automating that would just lose money faster and more reliably.

What the forward record IS good for: it is the only genuinely out-of-sample
evidence we can generate. Every backtest in this project shares one window and
one AI-bull regime. A few weeks of timestamped forward signals — recorded before
the outcome is known — is the one dataset that can honestly clear the "algorithm
reading is confidently successful" gate.

    python -m app.agent.live_runner --once            # single cycle, then exit
    python -m app.agent.live_runner --interval 300    # loop, 5-min cadence
    python -m app.agent.live_runner --status          # session clock only
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from ..config import ROOT
from ..data import market_hours as mh

RECORD_PATH = ROOT / "data" / "forward_record.jsonl"


def _cycle(portal, agent, session: str) -> dict:
    """One evaluation: refresh real quotes, score, snapshot. Never trades."""
    ctx = agent._analyze()
    convictions = [c.to_dict() for c in ctx["convictions"]]
    equity = portal._book_value()
    camp = portal.campaign.status(equity).to_dict() if portal.campaign else {}

    plan = agent.build_plan(write=True)
    row = {
        "ts": time.time(),
        "recorded_at_et": mh.state().now_et.isoformat(timespec="seconds"),
        "session": session,
        "equity": round(equity, 2),
        "drawdown_pct": camp.get("drawdown_pct"),
        "halted": camp.get("breached", False),
        "prices": {s: round(p, 4) for s, p in ctx["prices"].items()},
        "convictions": [{"symbol": c["symbol"], "score": c["score"], "action": c["action"]}
                        for c in convictions],
        "intended_orders": [{"symbol": o["symbol"], "side": o["side"],
                             "order_value": o["order_value"], "conviction": o["conviction"]}
                            for o in plan.get("orders", [])],
        "executed": False,          # this runner never trades; kept explicit
    }
    RECORD_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RECORD_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def _print_cycle(row: dict) -> None:
    halt = "  [HALTED]" if row.get("halted") else ""
    print(f"[{row['recorded_at_et']}] {row['session']:<10} equity=${row['equity']:,.0f} "
          f"dd={row.get('drawdown_pct')}%{halt}")
    for o in row["intended_orders"]:
        print(f"    intent: {o['side']:<4} {o['symbol']:<6} ${o['order_value']:>8,.0f} "
              f"(conv {o['conviction']:+.2f})   [NOT placed — needs your approval]")
    if not row["intended_orders"]:
        print("    no signal crossed the entry threshold")


def run(once: bool = False, interval: int = 300, max_cycles: int | None = None) -> None:
    from ..portal import Portal
    from .daily import DailyAgent
    portal = Portal()
    portal.ensure_built()
    agent = DailyAgent(portal, execute=False)     # execute=False is load-bearing

    cycles = 0
    while True:
        st = mh.state()
        if st.is_open or once:
            _print_cycle(_cycle(portal, agent, st.session))
            cycles += 1
            if once or (max_cycles and cycles >= max_cycles):
                return
            time.sleep(interval)
            continue

        # Closed: report and sleep until the next boundary rather than spinning.
        wait = mh.seconds_until(st.next_open)
        print(f"[{st.now_et:%Y-%m-%d %H:%M %Z}] market {st.session} — "
              f"next open {st.next_open:%a %Y-%m-%d %H:%M %Z} (in {wait / 3600:.1f}h)")
        if max_cycles is not None:
            return
        time.sleep(min(wait + 5, 3600))            # re-check hourly at most


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run one cycle regardless of session")
    ap.add_argument("--interval", type=int, default=300, help="seconds between cycles")
    ap.add_argument("--max-cycles", type=int, default=None)
    ap.add_argument("--status", action="store_true", help="print the session clock and exit")
    args = ap.parse_args()

    if args.status:
        s = mh.state()
        print(json.dumps(s.to_dict(), indent=2))
        if RECORD_PATH.exists():
            n = sum(1 for _ in RECORD_PATH.open(encoding="utf-8"))
            print(f"forward record: {n} rows at {RECORD_PATH}")
        else:
            print("forward record: empty (no cycles recorded yet)")
        return

    run(once=args.once, interval=args.interval, max_cycles=args.max_cycles)


if __name__ == "__main__":
    _main()
