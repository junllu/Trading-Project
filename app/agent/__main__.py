"""Run the daily agent once from the command line.

    python -m app.agent

Ideal for a real cron job or systemd timer:
    0 9 * * 1-5  cd /path/to/Trading-Project && python -m app.agent >> agent.log 2>&1

Prints the daily report as JSON and exits. Honors TRADING_MODE, so on a cron in
paper/confirm mode it will analyze and queue without sending live orders.
"""
from __future__ import annotations

import json

from ..portal import Portal


def main() -> None:
    portal = Portal().build()
    report = portal.run_daily()
    print(json.dumps(report, indent=2, default=str))
    print("\n=== SUMMARY ===")
    print(report.get("summary", ""))


if __name__ == "__main__":
    main()
