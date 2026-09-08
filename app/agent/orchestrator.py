"""Runs the roster on its declared cadences, so you stop being the scheduler.

Until this existed, `cadence_class` in roster.py was documentation: it said
thesis_ledger runs on each filing and options_desk runs daily, and then nothing
ran either one. Every agent fired only when a human typed its module name. The
graph was real and the clock was imaginary.

Three kinds of work, and only the first is genuinely automatable:

  CLOCK-DRIVEN   an interval has elapsed. Every agent's report() reads snapshots
                 already on disk, so all twelve can run unattended.
  EVENT-DRIVEN   an upstream artefact changed. `on_filing` agents wake when a
                 new filings snapshot lands, not on a timer — a quarterly clock
                 would re-grade on an arbitrary day and miss the filing by weeks.
  HUMAN-GATED    fetching NEW fundamentals, filings or earnings dates. These come
                 through the broker MCP, which the portal has no credentials for
                 by design. Claude fetches and commits them as dated snapshots.

That last category is why some instruction will always be manual, and it is a
deliberate boundary rather than an omission: the portal never holds broker
credentials, and a dated snapshot committed by hand is also what keeps the data
point-in-time honest. What was NOT deliberate is everything else being manual,
which is what this fixes.

The queue it emits is the useful half: instead of remembering which of twenty-
five symbols still needs a fundamentals pull, the orchestrator names them.

    python -m app.agent.orchestrator            # what is due right now
    python -m app.agent.orchestrator --run      # run everything due
    python -m app.agent.orchestrator --force    # run all, ignoring cadence
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from ..config import ROOT
from .roster import ROSTER, Agent

STATE_PATH = ROOT / "data" / "agent_runs.json"
FUNDAMENTALS_DIR = ROOT / "data" / "fundamentals"

DAY = 86400.0
CADENCE_SECONDS: dict[str, float] = {
    "daily": DAY,
    "weekly": 7 * DAY,
    "bi_weekly": 14 * DAY,
    "monthly": 30 * DAY,
    "quarterly": 91 * DAY,
    "dual": DAY,            # the coordinator runs on the faster of its two clocks
}

# Cadences that wait for an artefact rather than a timer.
EVENT_DRIVEN = {"on_filing", "on_sweep"}

# Owned by the portal's own thread, not by this orchestrator.
PORTAL_OWNED = {"intraday"}


@dataclass
class Due:
    agent: Agent
    due: bool
    reason: str
    last_run: float | None = None

    def to_dict(self) -> dict:
        return {"agent": self.agent.name, "cadence": self.agent.cadence_class,
                "due": self.due, "reason": self.reason,
                "last_run": (datetime.fromtimestamp(self.last_run, timezone.utc).isoformat()
                             if self.last_run else None)}


def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _newest_snapshot_mtime() -> float:
    """When did new filing evidence last arrive? Drives the on_filing agents."""
    if not FUNDAMENTALS_DIR.exists():
        return 0.0
    files = list(FUNDAMENTALS_DIR.glob("*.json"))
    return max((f.stat().st_mtime for f in files), default=0.0)


def what_is_due(now: float | None = None) -> list[Due]:
    now = now or time.time()
    state = _load_state()
    snapshot_at = _newest_snapshot_mtime()
    out: list[Due] = []

    for a in ROSTER:
        last = state.get(a.name, {}).get("last_run")

        if a.cadence_class in PORTAL_OWNED:
            out.append(Due(a, False, "owned by the portal's live thread", last))
            continue
        if not a.entrypoint:
            out.append(Due(a, False, "no callable entrypoint", last))
            continue

        if a.cadence_class in EVENT_DRIVEN:
            # Wake on new evidence, not on a calendar. A quarterly TIMER would
            # fire on an arbitrary day and could miss a filing by weeks.
            if last is None:
                out.append(Due(a, True, "never run", last))
            elif snapshot_at > last:
                out.append(Due(a, True, "new snapshot since last run", last))
            else:
                out.append(Due(a, False, "no new evidence since last run", last))
            continue

        interval = CADENCE_SECONDS.get(a.cadence_class)
        if interval is None:
            out.append(Due(a, False, f"unknown cadence '{a.cadence_class}'", last))
        elif last is None:
            out.append(Due(a, True, "never run", last))
        elif now - last >= interval:
            out.append(Due(a, True, f"{(now - last) / DAY:.1f}d since last run", last))
        else:
            nxt = (interval - (now - last)) / DAY
            out.append(Due(a, False, f"next in {nxt:.1f}d", last))
    return out


def run_due(force: bool = False) -> dict:
    """Run every agent whose clock has come up. Read-only by construction.

    Nothing here stages or executes an irreversible action: the coordinator is
    excluded, and every agent invoked is a maker or checker whose report() only
    reads what is already on disk.
    """
    import importlib

    state = _load_state()
    ran, skipped, failed = {}, {}, {}

    for d in what_is_due():
        a = d.agent
        if a.kind == "coordinator":
            skipped[a.name] = "coordinator — its output is a human queue, not a job"
            continue
        if not (d.due or (force and a.entrypoint)):
            skipped[a.name] = d.reason
            continue
        try:
            fn = getattr(importlib.import_module(a.module), a.entrypoint, None)
            if fn is None:
                failed[a.name] = f"{a.module}.{a.entrypoint} missing"
                continue
            result = fn()
            ran[a.name] = _summarise(a.name, result)
            state.setdefault(a.name, {})["last_run"] = time.time()
        except Exception as exc:
            failed[a.name] = f"{type(exc).__name__}: {exc}"

    _save_state(state)
    return {"ran": ran, "skipped": skipped, "failed": failed,
            "human_queue": human_queue()}


def _summarise(name: str, result) -> str:
    if not isinstance(result, dict):
        return "ok"
    for key in ("status", "verdict", "note"):
        if key in result and isinstance(result[key], str):
            return result[key][:120]
    return f"{len(result)} fields"


def human_queue() -> list[str]:
    """What genuinely cannot be automated — the MCP fetches only Claude can run.

    This is the honest residue. Everything else on this page runs itself now.
    """
    items: list[str] = []
    try:
        from ..intel.onboard import report as onboard_report
        r = onboard_report()
        need = r.get("needs_mcp_pull", [])
        if need:
            items.append(f"MCP pull — fundamentals/filings missing for {len(need)} symbol(s): "
                         f"{', '.join(need[:10])}{' ...' if len(need) > 10 else ''}")
        synth = r.get("held_on_synthetic_prices", [])
        if synth:
            items.append(f"price backfill — held names on synthetic data: {', '.join(synth)}")
    except Exception:
        pass
    try:
        from .live_loop import report as live_report
        lr = live_report()
        if lr.get("live_session_cycles", 0) == 0:
            items.append("live_recorder has never run during a REGULAR session — "
                         "start the portal during market hours")
    except Exception:
        pass
    return items


class OrchestratorLoop:
    """A daemon that ticks and runs whatever the clock says is due.

    Ticks hourly rather than at a fixed time of day because the cadences here
    are heterogeneous — daily, weekly, bi-weekly, and two event-driven ones that
    must fire when a snapshot lands rather than on a calendar. A once-a-day
    scheduler could not express that, which is why the existing DailyScheduler
    was never enough for the roster.

    Read-only: it invokes makers and checkers, never the coordinator, so it
    cannot stage or place anything.
    """

    def __init__(self, tick_seconds: int = 3600):
        import threading
        self.tick_seconds = tick_seconds
        self._stop = threading.Event()
        self._thread = None
        self.ticks = 0
        self.last_result: dict | None = None
        self.last_error: str | None = None

    def start(self) -> dict:
        import threading
        if self._thread and self._thread.is_alive():
            return self.status()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="orchestrator", daemon=True)
        self._thread.start()
        return self.status()

    def stop(self) -> dict:
        self._stop.set()
        return self.status()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def _run(self) -> None:
        import logging
        log = logging.getLogger("portal.orchestrator")
        while not self._stop.is_set():
            try:
                self.last_result = run_due()
                self.ticks += 1
                n = len(self.last_result.get("ran", {}))
                if n:
                    log.info("orchestrator ran %d due agent(s)", n)
            except Exception as exc:
                # Research failing must never take the portal down with it.
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("orchestrator tick failed")
            self._stop.wait(self.tick_seconds)

    def status(self) -> dict:
        due = [d.agent.name for d in what_is_due() if d.due]
        return {"running": self.running, "tick_seconds": self.tick_seconds,
                "ticks": self.ticks, "last_error": self.last_error,
                "due_now": due, "human_queue": human_queue(),
                "last_ran": list((self.last_result or {}).get("ran", {}))}


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--force", action="store_true", help="ignore cadence, run everything")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.run or args.force:
        res = run_due(force=args.force)
        if args.json:
            print(json.dumps(res, indent=2))
            return
        print("=" * 76)
        print("  ORCHESTRATOR — ran what was due")
        print("=" * 76)
        for n, s in res["ran"].items():
            print(f"  [ran ] {n:20} {s}")
        for n, s in res["failed"].items():
            print(f"  [FAIL] {n:20} {s}")
        for n, s in res["skipped"].items():
            print(f"  [skip] {n:20} {s}")
        if res["human_queue"]:
            print(f"\n  {'-' * 72}")
            print("  NEEDS A HUMAN — the broker MCP has no portal credentials by design")
            for item in res["human_queue"]:
                print(f"    · {item}")
        return

    rows = what_is_due()
    if args.json:
        print(json.dumps([r.to_dict() for r in rows], indent=2))
        return

    print("=" * 76)
    print("  ORCHESTRATOR — what the clock says, not what you remember")
    print("=" * 76)
    for r in sorted(rows, key=lambda x: (not x.due, x.agent.name)):
        mark = "DUE " if r.due else "    "
        print(f"  [{mark}] {r.agent.name:20} {r.agent.cadence_class:11} {r.reason}")
    n_due = sum(1 for r in rows if r.due)
    print(f"\n  {n_due} of {len(rows)} agents due.  Run them:  "
          f"python -m app.agent.orchestrator --run")
    q = human_queue()
    if q:
        print(f"\n  NEEDS A HUMAN ({len(q)}):")
        for item in q:
            print(f"    · {item}")


if __name__ == "__main__":
    _main()
