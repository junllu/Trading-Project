"""Session-aware live loop, run in-process by the portal.

Why in-process: a standalone runner would build its own Portal, its own
MarketData and its own rolling history. Two processes means two copies of state
that quietly diverge — the dashboard showing one set of prices and conviction
scores while the loop acts on another. Sharing the portal's single runtime
removes the sync problem rather than managing it.

Behaviour:
  - market open      -> evaluate every `interval` seconds
  - premarket/after  -> refresh quotes only (no scoring churn)
  - closed / holiday -> sleep until the next open, re-checking hourly

Every evaluation appends a timestamped row to data/forward_record.jsonl. It
NEVER places orders: CLAUDE.md requires per-order human approval, and the
walk-forward result (21% win rate vs buy-and-hold over 34 out-of-sample folds)
means automating this would only lose money more reliably. The record is the
point — it is the only out-of-sample evidence this project can generate.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Optional

from ..config import ROOT
from ..data import market_hours as mh

log = logging.getLogger("portal.live")
RECORD_PATH = ROOT / "data" / "forward_record.jsonl"

# Bounded retry, matching the circuit breaker app/intel/local_llm_analyst.py
# already uses. A loop with no hard stop is an unlimited budget attached to an
# undefined result: it retries the same failing path indefinitely while looking
# healthy from outside. Tripping requires an explicit restart, which is the
# point — a human should see that it broke.
MAX_CONSECUTIVE_ERRORS = 3
BACKOFF_BASE_SECONDS = 15


class LiveLoop:
    def __init__(self, portal, interval: int = 300):
        self.portal = portal
        self.interval = interval
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.cycles = 0
        self.errors = 0
        self.last_cycle: Optional[dict[str, Any]] = None
        self.last_error: Optional[str] = None
        self.started_at: Optional[float] = None
        self.consecutive_errors = 0
        self.tripped: bool = False
        self.tripped_at: Optional[float] = None

    # --- lifecycle ---------------------------------------------------------
    def start(self) -> dict:
        if self._thread and self._thread.is_alive():
            return self.status()
        self._stop.clear()
        # An explicit start is the human acknowledging the fault, so it is the
        # only thing that rearms the breaker.
        self.tripped = False
        self.tripped_at = None
        self.consecutive_errors = 0
        self.started_at = time.time()
        self._thread = threading.Thread(target=self._run, name="live-loop", daemon=True)
        self._thread.start()
        log.info("live loop started (interval=%ss)", self.interval)
        return self.status()

    def stop(self) -> dict:
        self._stop.set()
        log.info("live loop stopping")
        return self.status()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def status(self) -> dict:
        st = mh.state()
        return {
            "running": self.running,
            "interval_seconds": self.interval,
            "cycles": self.cycles,
            "errors": self.errors,
            "consecutive_errors": self.consecutive_errors,
            "tripped": self.tripped,
            "tripped_at": self.tripped_at,
            "max_consecutive_errors": MAX_CONSECUTIVE_ERRORS,
            "last_error": self.last_error,
            "started_at": self.started_at,
            "session": st.session,
            "market_open": st.is_open,
            "next_open": st.next_open.isoformat(timespec="seconds") if st.next_open else None,
            "next_close": st.next_close.isoformat(timespec="seconds") if st.next_close else None,
            "last_cycle": self.last_cycle,
            "record_rows": self._record_rows(),
        }

    @staticmethod
    def _record_rows() -> int:
        if not RECORD_PATH.exists():
            return 0
        try:
            with RECORD_PATH.open(encoding="utf-8") as fh:
                return sum(1 for _ in fh)
        except Exception:
            return 0

    # --- the loop ----------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            if self.tripped:
                # Bounded, not infinite. The old behaviour retried a failing
                # cycle every 60s forever: cost and log noise grew while the
                # information did not, and a silently-broken loop still looked
                # "running" on the dashboard. Escalation is a visible stop.
                log.error("live loop circuit breaker OPEN after %d consecutive "
                          "failures — not retrying. Last error: %s",
                          MAX_CONSECUTIVE_ERRORS, self.last_error)
                return
            try:
                st = mh.state()
                if st.is_open:
                    self.last_cycle = self.cycle(st.session)
                    self.cycles += 1
                    self.consecutive_errors = 0     # only a real cycle clears it
                    self._stop.wait(self.interval)
                elif st.session in ("premarket", "afterhours"):
                    # keep prices warm without re-scoring every few minutes
                    self.portal.market.refresh(self.portal.held_symbols or [])
                    self._stop.wait(self.interval)
                else:
                    wait = min(mh.seconds_until(st.next_open) + 5, 3600)
                    log.info("market %s — next open %s (sleeping %.0f min)",
                             st.session, st.next_open, wait / 60)
                    self._stop.wait(wait)
            except Exception as exc:                      # never let the thread die silently
                self.errors += 1
                self.consecutive_errors += 1
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("live loop cycle failed (%d/%d consecutive)",
                              self.consecutive_errors, MAX_CONSECUTIVE_ERRORS)
                if self.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    self.tripped = True
                    self.tripped_at = time.time()
                    continue
                # Back off geometrically so a persistent fault is not hammered.
                backoff = min(BACKOFF_BASE_SECONDS * (2 ** (self.consecutive_errors - 1)),
                              self.interval)
                self._stop.wait(backoff)

    def cycle(self, session: str = "manual") -> dict:
        """One evaluation against live data. Records; never trades."""
        p = self.portal
        p.ensure_built()
        agent = p.daily_agent
        ctx = agent._analyze()
        equity = p._book_value()
        camp = p.campaign.status(equity).to_dict() if p.campaign else {}
        plan = agent.build_plan(write=True)

        row = {
            "ts": time.time(),
            "recorded_at_et": mh.state().now_et.isoformat(timespec="seconds"),
            "session": session,
            "equity": round(equity, 2),
            "drawdown_pct": camp.get("drawdown_pct"),
            "halted": camp.get("breached", False),
            "prices": {s: round(v, 4) for s, v in ctx["prices"].items()},
            "convictions": [{"symbol": c.symbol, "score": round(c.score, 3), "action": c.action}
                            for c in ctx["convictions"]],
            "intended_orders": [{"symbol": o["symbol"], "side": o["side"],
                                 "order_value": o["order_value"],
                                 "conviction": o["conviction"]} for o in plan.get("orders", [])],
            "executed": False,
        }
        RECORD_PATH.parent.mkdir(parents=True, exist_ok=True)
        with RECORD_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        log.info("live cycle: equity=$%.0f session=%s intents=%d",
                 equity, session, len(row["intended_orders"]))
        return row


def report() -> dict:
    """Agent entrypoint — the forward record's shape, without a live portal.

    The loop itself is owned by the portal process, so this reads its ARTEFACT
    rather than its state. That separation is deliberate: an agent that had to
    reach into a running thread to report would be coupling the graph to a
    process lifetime, and would go silent exactly when the portal is down.
    """
    import json
    from collections import Counter

    if not RECORD_PATH.exists():
        return {"rows": 0, "status": "no forward record yet — the loop has never run"}

    rows, bad = [], 0
    for line in RECORD_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            bad += 1

    sessions = Counter(r.get("session", "?") for r in rows)
    live = sessions.get("regular", 0)
    with_prob = sum(1 for r in rows for c in r.get("convictions", []) if "probability" in c)

    return {
        "rows": len(rows),
        "corrupt_lines": bad,
        "sessions": dict(sessions),
        "live_session_cycles": live,
        "predictions_with_probability": with_prob,
        "first": rows[0].get("recorded_at_et") if rows else None,
        "last": rows[-1].get("recorded_at_et") if rows else None,
        "status": ("no cycle has run during a REGULAR session — live behaviour is "
                   "unproven and the loop cannot leave OBSERVE"
                   if live == 0 else f"{live} live-session cycles recorded"),
    }
