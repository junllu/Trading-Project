"""One screen, once a day — what changed, what needs you, what is broken.

WHY A ROLLUP AND NOT ANOTHER REPORT

There are now a dozen agents, each with a good `report()`. That is exactly the
problem: checking a self-running system should not require remembering which
twelve commands to run, and a loop nobody checks is a loop that fails silently
for a week. Twelve honest reports nobody reads are worth less than one that gets
read every morning.

WHAT IT REFUSES TO DO

It does not re-derive anything. Every line traces to an agent that already
computed it — the heartbeat for liveness, the chief for decisions, the blotter
for what was traded, decision_score for whether it beat doing nothing. This is
composition, not a thirteenth opinion.

It also does not congratulate. The temptation in a daily brief is to lead with a
number that went up; here the lead is whatever is BROKEN or WAITING, because
those are the only two things a morning read can act on. A green day with a dead
harvest is not a green day.

ORDER IS THE MESSAGE

    1. broken      liveness failures — nothing else matters if a feed is dead
    2. waiting     staged decisions needing a human yes
    3. did         what actually traded since the last brief
    4. learned     what resolved, and whether it beat holding
    5. pace        the campaign, reported last on purpose

Pace is last because it is the number most likely to provoke a bad decision, and
the program manager is already forbidden from proposing risk to close it.

    python -m app.agent.daily_brief
    python -m app.agent.daily_brief --json
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from typing import Any

from ..config import ROOT

OUT_DIR = ROOT / "data" / "briefs"


def _safe(fn, default=None, label=""):
    """A failing section is reported, never silently omitted.

    A brief that quietly drops the section it could not build is worse than one
    that says so: the reader sees a clean page and concludes all is well.
    """
    try:
        return fn()
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "section": label} \
            if default is None else default


def _since_ts(hours: float = 24.0) -> float:
    return time.time() - hours * 3600


def _traded(since: float) -> dict[str, Any]:
    from ..engine import blotter
    rows = [r for r in blotter.rows() if float(r.get("ts") or 0) >= since]
    fills = [r for r in rows if r.get("status") == "filled"]
    by_strat: dict[str, dict[str, int]] = {}
    for r in rows:
        s = by_strat.setdefault(r.get("strategy") or "unknown",
                                {"orders": 0, "fills": 0, "rejected": 0})
        s["orders"] += 1
        if r.get("status") == "filled":
            s["fills"] += 1
        elif r.get("status") == "rejected":
            s["rejected"] += 1
    notional = sum(float(r["quantity"]) * float(r["filled_price"])
                   for r in fills
                   if isinstance(r.get("filled_price"), (int, float)))
    backlog = sum(1 for r in rows if "BACKLOG" in (r.get("reason") or ""))
    return {
        "orders": len(rows), "fills": len(fills),
        "notional_usd": round(notional, 2),
        "by_strategy": by_strat,
        "backlog_fills": backlog,
        "rejections": [
            {"symbol": r["symbol"], "reason": (r.get("reason") or "")[:90]}
            for r in rows if r.get("status") == "rejected"][:6],
    }


def build(hours: float = 24.0) -> dict[str, Any]:
    from .heartbeat import scan as heartbeat_scan

    hb = _safe(heartbeat_scan, label="heartbeat") or {}
    broken = [c for c in (hb.get("checks") or [])
              if c.get("status") in ("STALE", "NEVER")]

    def _decisions():
        from .chief import build_queue
        q = build_queue()
        return {
            "counts": q.get("counts", {}),
            "staged": q.get("staged_awaiting_approval", []),
            "now": [{"subject": d["subject"], "claim": d["claim"][:140],
                     "action": d["action"][:120]}
                    for d in q.get("decisions", []) if d.get("urgency") == "now"],
            "collectors_failed": q.get("collectors_failed", []),
        }

    def _learned():
        from ..analytics.decision_score import score
        return score().to_dict()

    def _pace():
        from .program import _campaign_pace
        return _campaign_pace()

    since = _since_ts(hours)
    brief = {
        "agent": "daily_brief",
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "window_hours": hours,
        "broken": [{"name": c["name"], "status": c["status"],
                    "detail": c.get("detail", ""), "cadence": c.get("cadence", "")}
                   for c in broken],
        "market_session": hb.get("market_session"),
        "waiting": _safe(_decisions, label="chief"),
        "did": _safe(lambda: _traded(since), label="blotter"),
        "learned": _safe(_learned, label="decision_score"),
        "pace": _safe(_pace, label="program"),
        "reads_from": ["heartbeat", "chief", "blotter", "decision_score", "program"],
        "does_not": ["re-derive anything — every line traces to an agent that "
                     "already computed it",
                     "lead with a number that went up; broken and waiting come first"],
    }
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / f"{datetime.now():%Y-%m-%d}.json").write_text(
            json.dumps(brief, indent=2, default=str), encoding="utf-8")
    except OSError:
        pass
    return brief


def report() -> dict[str, Any]:
    return build()


def _main() -> None:                              # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description="The one-screen daily read.")
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    b = build(args.hours)
    if args.json:
        print(json.dumps(b, indent=2, default=str))
        return

    print("=" * 78)
    print(f"  DAILY BRIEF — {b['as_of']}   market: {b.get('market_session') or '—'}")
    print("=" * 78)

    # 1. BROKEN
    print(f"\n  {'-' * 74}\n  BROKEN")
    if b["broken"]:
        for c in b["broken"]:
            print(f"    {c['status']:6} {c['name']:22} expected {c['cadence']}")
            if c["detail"]:
                print(f"           {c['detail']}")
    else:
        print("    nothing — every component is inside its window")

    # 2. WAITING
    w = b.get("waiting") or {}
    print(f"\n  {'-' * 74}\n  WAITING ON YOU")
    if w.get("error"):
        print(f"    section failed: {w['error']}")
    else:
        staged = w.get("staged") or []
        print(f"    {len(staged)} staged for approval"
              + (f": {', '.join(staged[:6])}" if staged else ""))
        for d in (w.get("now") or [])[:4]:
            print(f"    NOW  {d['subject']}: {d['claim']}")
        if w.get("collectors_failed"):
            print(f"    checker(s) FAILED: {len(w['collectors_failed'])}")

    # 3. DID
    d = b.get("did") or {}
    print(f"\n  {'-' * 74}\n  TRADED (last {b['window_hours']:.0f}h, simulated)")
    if d.get("error"):
        print(f"    section failed: {d['error']}")
    else:
        print(f"    {d['orders']} order(s) · {d['fills']} fill(s) · "
              f"${d['notional_usd']:,.0f}"
              + (f" · {d['backlog_fills']} backlog" if d.get("backlog_fills") else ""))
        for s, v in (d.get("by_strategy") or {}).items():
            print(f"      {s:16} {v['fills']} filled, {v['rejected']} rejected")
        for r in (d.get("rejections") or [])[:4]:
            print(f"      rejected {r['symbol']}: {r['reason']}")

    # 4. WIN / LOSS
    l = b.get("learned") or {}
    print(f"\n  {'-' * 74}\n  WIN / LOSS (decisions whose horizon has elapsed)")
    if l.get("error"):
        print(f"    section failed: {l['error']}")
    else:
        o = l.get("overall") or {}
        if not o.get("n"):
            print(f"    nothing resolved yet — {l.get('unresolved', 0)} decision(s) "
                  f"waiting on {l.get('horizon_sessions')} sessions")
            print(f"    (a decision is scored only once its horizon fully elapses;")
            print(f"     partial outcomes are excluded, never extrapolated)")
        else:
            wins = round(o["n"] * o["win_rate_pct"] / 100)
            pr = o.get("payoff_ratio") or "—"
            pf = o.get("profit_factor") or "—"
            print(f"    {wins}W / {o['n'] - wins}L   win {o['win_rate_pct']}%   "
                  f"EXPECTANCY {o['expectancy_pct']:+.3f}% per decision")
            print(f"    avg win {o['avg_win_pct']:+.2f}%  ·  avg loss "
                  f"-{o['avg_loss_pct']:.2f}%  ·  payoff {pr}  ·  PF {pf}")
            if o.get("worst_losing_streak"):
                print(f"    worst losing streak: {o['worst_losing_streak']}")
            for k, v in (l.get("by_strategy") or {}).items():
                if v.get("n"):
                    w = round(v["n"] * v["win_rate_pct"] / 100)
                    print(f"      {k:16} {w}W/{v['n'] - w}L  "
                          f"exp {v['expectancy_pct']:+.3f}%")
            dc = o.get("drift_control")
            if dc:
                print(f"    drift check: beat the market's move "
                      f"{dc['beat_market_move_pct']}% of the time — a sanity check "
                      f"that this is the rule, not the tape")

    # 5. PACE
    p = b.get("pace") or {}
    print(f"\n  {'-' * 74}\n  PACE")
    if p.get("error"):
        print(f"    section failed: {p['error']}")
    else:
        print(f"    ${p.get('equity', 0):,.0f} -> $1,000,000 · "
              f"{p.get('progress_pct', 0):.1f}% · {p.get('days_remaining')}d left · "
              f"{p.get('required_cagr_pct')}%/yr required")
        print("    reported, not actioned — closing a pace gap with more risk is "
              "not a move this system will propose.")


if __name__ == "__main__":                        # pragma: no cover
    _main()
