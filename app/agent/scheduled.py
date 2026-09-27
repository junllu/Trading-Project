"""The 09:00 daily cycle, runnable without the dashboard.

    python -m app.agent.scheduled

WHY THIS EXISTS

The daily cycle only ran inside the dashboard process (portal.py's
DailyScheduler). Close the window, let the PC sleep, or reboot, and the day was
skipped silently: trade_plan.json went unwritten from 2026-09-16 and the paper
record froze on 09-18 while nothing reported a failure. Task Scheduler runs this
instead, with StartWhenAvailable so a missed 09:00 fires on wake.

TWO GUARDS

1. ONCE PER DAY, WHOEVER GETS THERE FIRST. The dashboard's scheduler and this
   task can both be armed. Each claims the day by creating a marker with
   O_CREAT|O_EXCL — atomic at the filesystem, so two processes firing in the
   same second cannot both win — and the loser skips. Two cycles would place
   every paper order twice and corrupt the record the sleeve is graded on.

2. NEVER IN LIVE MODE. The cycle has a live-execution path. The dashboard
   refuses to autostart its schedule in LIVE because launching a window is not
   consent to trade; a scheduled task firing at 09:00 is even less so. Same rule.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date

from ..config import ROOT

CLAIM_DIR = ROOT / "data" / "runs"


def claim_today(who: str, today: date | None = None) -> bool:
    """Atomically claim today's cycle. False if someone already has it."""
    d = (today or date.today()).isoformat()
    CLAIM_DIR.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(CLAIM_DIR / f"daily_cycle_{d}.claim",
                     os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"by": who, "pid": os.getpid()}))
    return True


def main() -> int:
    from ..data.market_hours import is_trading_day
    from ..config import TradingMode
    from ..portal import Portal

    if not is_trading_day(date.today()):
        print("not a trading day - skipping")
        return 0

    portal = Portal().build()
    if portal.settings.mode is TradingMode.LIVE:
        print("REFUSED: TRADING_MODE is live. The scheduled cycle runs in paper or "
              "confirm mode only; live orders need approval in a session.")
        return 2
    if not claim_today("task"):
        print("today's cycle already ran (dashboard or an earlier task run) - skipping")
        return 0

    res = portal.run_autonomy_cycle()
    plan = res.get("plan") or {}
    print(f"cycle {res.get('ts')}  mode={portal.settings.mode.value}")
    print(f"  plan: {plan.get('orders', 0)} order intent(s) -> {plan.get('path')}")
    print(f"  paper executed: {res.get('executed')}  "
          f"actions: {len(res.get('actions') or [])}")
    if res.get("note"):
        print(f"  note: {res['note']}")
    if not plan:
        print("  NO PLAN BUILT - policy did not allow it:", res.get("policy"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
