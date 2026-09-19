"""Is every part of this system actually still running?

THE FAILURE THIS EXISTS TO CATCH

The training loop is now unattended: a paper loop ticking on a timer, a
fundamentals pull at 06:00, a minute-bar harvest every 30 minutes, a researcher
on a daily cadence. Unattended work has one characteristic failure — it stops
without telling anyone — and every page in this portal keeps rendering happily
on stale data. A dashboard showing yesterday's numbers looks exactly like a
dashboard showing today's.

So this checks liveness by asking each component the only question that matters:
when did you last produce output, and is that age acceptable FOR YOUR CADENCE?

WHY CADENCE-RELATIVE AND MARKET-AWARE

Freshness is meaningless in the absolute. Minute bars eighteen hours old at 2am
Sunday are perfectly healthy; the same bars at 11am Tuesday mean the harvest is
dead. A check that compares an age against a fixed number would page you every
weekend and stay silent during the outage that matters. So each check declares
its cadence, and the market-sensitive ones are only judged while the market is
open.

FOUR STATES, AND THE DISTINCTION THAT MATTERS

    OK       produced output inside its expected window
    STALE    has run before, but not recently enough
    NEVER    has never produced output at all
    IDLE     not expected to run right now (market closed, weekend)

NEVER and STALE are kept apart deliberately. "The harvest broke this morning"
and "the harvest was never set up" look identical on an age check and need
completely different responses.

    python -m app.agent.heartbeat
    python -m app.agent.heartbeat --json
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Callable

import pathlib

from ..config import ROOT

OK, STALE, NEVER, IDLE = "OK", "STALE", "NEVER", "IDLE"

# When this interpreter started. Captured at import so it reflects the process,
# not the moment the check runs.
_PROCESS_START = time.time()


@dataclass
class Check:
    name: str
    status: str
    age_minutes: float | None
    tolerance_minutes: float | None
    last: str | None
    cadence: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _market_open() -> bool:
    try:
        from ..data import market_hours as mh
        return mh.state().session in ("premarket", "regular", "afterhours")
    except Exception:
        return False


def _regular_session() -> bool:
    try:
        from ..data import market_hours as mh
        return mh.state().session == "regular"
    except Exception:
        return False


def _age_minutes(ts: float | None) -> float | None:
    if ts is None:
        return None
    return max(0.0, (time.time() - ts) / 60.0)


def _grade(name: str, ts: float | None, tolerance_min: float, cadence: str,
           *, only_when: Callable[[], bool] | None = None,
           detail: str = "") -> Check:
    """Grade one component. `only_when` gates whether staleness even applies."""
    last = (datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
            if ts else None)
    age = _age_minutes(ts)
    if ts is None:
        return Check(name, NEVER, None, tolerance_min, None, cadence,
                     detail or "no output has ever been produced")
    if only_when is not None and not only_when():
        return Check(name, IDLE, age, tolerance_min, last, cadence,
                     detail or "not expected to run right now")
    status = OK if age is not None and age <= tolerance_min else STALE
    return Check(name, status, round(age, 1) if age is not None else None,
                 tolerance_min, last, cadence, detail)


# --- per-component freshness probes ---------------------------------------

def _code_freshness() -> Check:
    """Is the RUNNING process older than the code on disk?

    This is not a data check, and it is here because it caused two separate
    false alarms: a server started at 06:51 kept serving routes from before
    later edits, so a new endpoint returned 404 and its dashboard panel showed
    dashes. Nothing was broken except that the process predated the fix, and
    from the outside that is indistinguishable from a bug.

    Compares the newest mtime under app/ against this process's start time.
    """
    import os
    newest = 0.0
    app_dir = ROOT / "app"
    for base, _dirs, files in os.walk(app_dir):
        if "__pycache__" in base:
            continue
        for f in files:
            if f.endswith(".py"):
                try:
                    newest = max(newest, (pathlib.Path(base) / f).stat().st_mtime)
                except OSError:
                    continue
    started = _PROCESS_START
    if newest <= started:
        return Check("code_freshness", OK, round((time.time() - started) / 60, 1),
                     None, datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M:%S"),
                     "restart after editing app/", "process is running current code")
    behind = round((newest - started) / 60, 1)
    return Check("code_freshness", STALE, behind, 0,
                 datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M:%S"),
                 "restart after editing app/",
                 f"app/ was edited {behind:.0f} min AFTER this process started — "
                 f"restart, or new routes 404 and panels show dashes")


def _minute_bars() -> Check:
    try:
        from ..data.minute import coverage
        cov = coverage()
        if not cov:
            return Check("minute_bars", NEVER, None, 45, None,
                         "every 30 min during market hours", "no symbols cached")
        newest = None
        for v in cov.values():
            raw = v.get("last")
            if not raw:
                continue
            try:
                dt = datetime.fromisoformat(str(raw))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                ts = dt.timestamp()
            except ValueError:
                continue
            newest = ts if newest is None else max(newest, ts)
        return _grade("minute_bars", newest, 45,
                      "every 30 min during market hours",
                      only_when=_regular_session,
                      detail=f"{len(cov)} symbol(s) cached")
    except Exception as exc:
        return Check("minute_bars", NEVER, None, 45, None,
                     "every 30 min during market hours", f"probe failed: {exc}")


def _newest_file(pattern: str, directory) -> float | None:
    try:
        files = sorted(directory.glob(pattern))
        return files[-1].stat().st_mtime if files else None
    except Exception:
        return None


def _fundamentals() -> Check:
    ts = _newest_file("????-??-??.json", ROOT / "data" / "fundamentals")
    # A weekday job: a Monday-morning check should not fault Friday's snapshot
    # over a weekend, so the tolerance spans three days.
    return _grade("fundamentals_pull", ts, 60 * 24 * 3, "weekdays 06:00",
                  detail="scheduled task TradingPortal-DailyMCPPulls")


def _research_brief() -> Check:
    p = ROOT / "data" / "research" / "brief_latest.json"
    ts = p.stat().st_mtime if p.exists() else None
    return _grade("researcher_brief", ts, 60 * 24 * 2, "daily")


def _trade_plan() -> Check:
    p = ROOT / "data" / "trade_plan.json"
    ts = p.stat().st_mtime if p.exists() else None
    return _grade("trade_plan", ts, 60 * 24 * 2, "daily / on demand")


def _paper_loop() -> Check:
    """The loop is alive if it has recently RESOLVED an order.

    Deliberately measured by output rather than by a `loop_running` flag. A
    thread that is alive but throwing on every tick reports running and produces
    nothing, which is the failure mode most worth catching.
    """
    from ..engine import blotter
    rows = blotter.rows()
    ts = float(rows[-1]["ts"]) if rows and rows[-1].get("ts") else None
    return _grade("paper_loop", ts, 120, "every tick while the market is open",
                  only_when=_regular_session,
                  detail=f"{len(rows)} order(s) recorded")


def _forward_record() -> Check:
    p = ROOT / "data" / "forward_record.jsonl"
    ts = p.stat().st_mtime if p.exists() else None
    rows = 0
    if p.exists():
        try:
            rows = sum(1 for line in p.open("r", encoding="utf-8") if line.strip())
        except OSError:
            rows = 0
    c = _grade("forward_record", ts, 60 * 24, "per live-loop cycle",
               only_when=_market_open,
               detail=f"{rows} row(s) — calibration needs ~100")
    return c


def _exit_monitor() -> Check:
    p = ROOT / "data" / "exit_state.json"
    ts = p.stat().st_mtime if p.exists() else None
    return _grade("exit_monitor", ts, 120, "every tick while the market is open",
                  only_when=_regular_session)


PROBES: tuple[Callable[[], Check], ...] = (
    _code_freshness,
    _minute_bars, _fundamentals, _research_brief, _trade_plan,
    _paper_loop, _forward_record, _exit_monitor,
)


def scan() -> dict[str, Any]:
    checks: list[Check] = []
    for probe in PROBES:
        try:
            checks.append(probe())
        except Exception as exc:                  # a broken probe is reported
            checks.append(Check(probe.__name__.strip("_"), NEVER, None, None,
                                None, "unknown", f"probe raised: {exc}"))

    counts = {s: sum(1 for c in checks if c.status == s) for s in (OK, STALE, NEVER, IDLE)}
    unhealthy = [c.name for c in checks if c.status in (STALE, NEVER)]
    session = "unknown"
    try:
        from ..data import market_hours as mh
        session = mh.state().session
    except Exception:
        pass

    return {
        "agent": "heartbeat",
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "market_session": session,
        "healthy": not unhealthy,
        "counts": counts,
        "unhealthy": unhealthy,
        "checks": [c.to_dict() for c in checks],
        "does_not": [
            "judge whether output is CORRECT — only whether it is recent",
            "fault a market-sensitive job while the market is closed",
            "conflate 'never ran' with 'ran but is stale'",
        ],
    }


def report() -> dict[str, Any]:
    return scan()


def _main() -> None:                              # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description="Heartbeat — is anything silently dead?")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    r = scan()
    if args.json:
        print(json.dumps(r, indent=2))
        return

    mark = {OK: "  ok ", STALE: "STALE", NEVER: "NEVER", IDLE: " idle"}
    print("=" * 78)
    print(f"  HEARTBEAT — {r['as_of']}   market: {r['market_session']}")
    print("=" * 78)
    print(f"\n  {'':6}{'COMPONENT':22}{'AGE':>10}{'TOLERANCE':>12}   CADENCE")
    print("  " + "-" * 74)
    for c in r["checks"]:
        age = f"{c['age_minutes']:.0f}m" if c["age_minutes"] is not None else "—"
        tol = f"{c['tolerance_minutes']:.0f}m" if c["tolerance_minutes"] else "—"
        print(f"  {mark[c['status']]} {c['name']:22}{age:>10}{tol:>12}   {c['cadence']}")
        if c["detail"]:
            print(f"         {c['detail']}")

    print(f"\n  {'-' * 74}")
    if r["healthy"]:
        print("  everything is inside its expected window")
    else:
        print(f"  ATTENTION: {', '.join(r['unhealthy'])}")
        print("  NEVER means it has never produced output — a different problem")
        print("  from STALE, which means it ran and then stopped.")


if __name__ == "__main__":                        # pragma: no cover
    _main()
