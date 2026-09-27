"""Operations health — did every scheduled job actually do its job today?

    python -m app.agent.ops_health            # check, print, append to the ledger
    python -m app.agent.ops_health --json

WHY THIS EXISTS

Every failure found on 2026-09-27 was silent. The daily-bar cache froze for
three weeks, the trade plan went unwritten for eleven days, the forward record
stopped, and each of them exited 0 or simply never ran — nothing reported a
problem, and the dashboard looked healthy throughout. A scheduled job that
fails quietly is worse than no job, because it is trusted.

So this checks OUTCOMES, not exit codes: not "did the task run" but "is the
artifact the task exists to produce present and current". A task that exits 0
and writes nothing fails here.

THE LEDGER

Each check appends {day, job, ok, detail} to data/ops_health.jsonl. That is the
receipt stream the autonomy ladder (app/agent/autonomy.py) grades routines on,
which until now had nothing to read and reported "no runs recorded" for all of
them. A routine is promoted only on this evidence and demoted when it decays.

Exit code is 1 when any due job failed, so the wrapper can raise an alert.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable

from ..config import ROOT
from ..data.market_hours import is_trading_day

DATA = ROOT / "data"
LEDGER = DATA / "ops_health.jsonl"
LATEST = DATA / "ops_health.json"


@dataclass
class Check:
    job: str
    task: str                 # Windows scheduled task name
    due: time                 # local time by which today's artifact must exist
    day: str = ""             # trading day being judged
    ok: bool = False
    detail: str = ""
    task_last_result: str = ""


def _due_day(due: time, now: datetime) -> date:
    """Most recent trading day whose due time has passed."""
    d = now.date()
    if not (is_trading_day(d) and now.time() >= due):
        d -= timedelta(days=1)
        while not is_trading_day(d):
            d -= timedelta(days=1)
    return d


# --- one check per job: is its artifact present and current for `day`? ---
def _daily_bars(day: date) -> tuple[bool, str]:
    try:
        s = json.loads((DATA / "refresh_status.json").read_text("utf-8"))
    except (OSError, ValueError):
        return False, "no refresh_status.json — refresh has never completed"
    if s.get("expected_last_bar", "") < day.isoformat():
        return False, f"last refresh targeted {s.get('expected_last_bar')}, need {day}"
    stale = s.get("stale") or {}
    if stale:
        return False, f"{len(stale)} file(s) stale, e.g. {next(iter(stale))}"
    return True, f"{s.get('files')} files current through {s.get('expected_last_bar')}"


def _daily_cycle(day: date) -> tuple[bool, str]:
    claim = DATA / "runs" / f"daily_cycle_{day}.claim"
    if not claim.exists():
        return False, f"no cycle ran on {day}"
    try:
        gen = json.loads((DATA / "trade_plan.json").read_text("utf-8")).get("generated", "")
    except (OSError, ValueError):
        return False, "cycle claimed the day but trade_plan.json is unreadable"
    if gen[:10] < day.isoformat():
        return False, f"cycle claimed the day but the plan is from {gen[:10]}"
    return True, f"plan generated {gen}"


def _forward_record(day: date) -> tuple[bool, str]:
    path = DATA / "forward_record.jsonl"
    if not path.exists():
        return False, "forward_record.jsonl missing"
    for line in reversed(path.read_text("utf-8").splitlines()):
        if line.strip():
            try:
                ts = json.loads(line).get("recorded_at_et", "")
            except ValueError:
                continue
            if ts[:10] == day.isoformat():
                return True, f"snapshot at {ts[11:16]} ET"
            if ts[:10] < day.isoformat():
                return False, f"latest snapshot is {ts[:10]}"
    return False, "no snapshots"


def _minute_harvest(day: date) -> tuple[bool, str]:
    p = DATA / "minute" / "NVDA.csv"            # a name every harvest batch covers
    try:
        rows = p.read_text("utf-8").strip().splitlines()
    except OSError:
        return False, "no minute cache"
    last = rows[-1].split(",")[0][:10] if rows else ""
    return (last >= day.isoformat(),
            f"minute bars through {last}" if last else "empty minute cache")


def _mcp_pulls(day: date) -> tuple[bool, str]:
    p = DATA / "research" / f"brief_{day:%Y%m%d}.json"
    return p.exists(), f"brief for {day}" + ("" if p.exists() else " missing")


# Due times are LOCAL (the scheduled tasks' clock), each with slack after the
# task's own start so a slow run is not a false alarm.
JOBS: list[tuple[str, str, time, Callable[[date], tuple[bool, str]]]] = [
    ("mcp_pulls",      "TradingPortal-DailyMCPPulls", time(7, 0),   _mcp_pulls),
    ("daily_cycle",    "TradingPortal-DailyCycle",    time(9, 45),  _daily_cycle),
    ("forward_record", "TradingPortal-ForwardRecord", time(13, 0),  _forward_record),
    ("minute_harvest", "TradingPortal-MinuteHarvest", time(13, 45), _minute_harvest),
    ("daily_bars",     "TradingPortal-DailyBars",     time(14, 30), _daily_bars),
]


def _task_result(name: str) -> str:
    """The scheduler's own view — context only; the artifact check decides."""
    try:
        out = subprocess.run(["schtasks", "/query", "/fo", "csv", "/v", "/tn", name],
                             capture_output=True, text=True, timeout=20)
        rows = list(csv.DictReader(out.stdout.splitlines()))
        if rows:
            return f"last run {rows[0].get('Last Run Time')}, result {rows[0].get('Last Result')}"
    except Exception:
        pass
    return "task not found"


def run(now: datetime | None = None, query_tasks: bool = True) -> list[Check]:
    now = now or datetime.now()
    out = []
    for job, task, due, fn in JOBS:
        c = Check(job=job, task=task, due=due)
        day = _due_day(due, now)
        c.day = day.isoformat()
        try:
            c.ok, c.detail = fn(day)
        except Exception as exc:                      # a broken check is a failed check
            c.ok, c.detail = False, f"check crashed: {type(exc).__name__}: {exc}"
        if query_tasks:
            c.task_last_result = _task_result(task)
        out.append(c)
    return out


def record(checks: list[Check]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds")
    with LEDGER.open("a", encoding="utf-8") as fh:
        for c in checks:
            fh.write(json.dumps({"checked_at": stamp, "day": c.day, "job": c.job,
                                 "ok": c.ok, "detail": c.detail}) + "\n")
    LATEST.write_text(json.dumps({"checked_at": stamp,
                                  "checks": [asdict(c) | {"due": c.due.strftime("%H:%M")}
                                             for c in checks]}, indent=2), encoding="utf-8")


def ledger_by_job(path: Path | None = None) -> dict[str, dict[str, bool]]:
    """{job: {day: ok}} — the LAST verdict per (job, day), so a late run that
    repaired a day overrides the earlier failure, but only for that day."""
    p = path or LEDGER
    out: dict[str, dict[str, bool]] = {}
    if not p.exists():
        return out
    for line in p.read_text("utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        out.setdefault(r["job"], {})[r["day"]] = bool(r["ok"])
    return out


def _main() -> int:
    ap = argparse.ArgumentParser(description="Did every scheduled job produce its artifact?")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-record", action="store_true")
    a = ap.parse_args()
    checks = run()
    if not a.no_record:
        record(checks)
    if a.json:
        print(json.dumps([asdict(c) | {"due": c.due.strftime("%H:%M")} for c in checks], indent=2))
    else:
        print(f"OPS HEALTH  {datetime.now():%Y-%m-%d %H:%M}")
        for c in checks:
            mark = "OK  " if c.ok else "FAIL"
            print(f"  {mark} {c.job:<15} {c.day}  {c.detail}")
            if not c.ok and c.task_last_result:
                print(f"       {'':<15} scheduler: {c.task_last_result}")
    failed = [c.job for c in checks if not c.ok]
    if failed:
        print(f"\n{len(failed)} job(s) failed: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
