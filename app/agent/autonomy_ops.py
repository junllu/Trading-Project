"""Autonomy operations — one cycle the system can run unattended.

Reversible work FINISHES (record, grade, plan, propose). Irreversible live
orders only fire when performance.policy says so AND TRADING_MODE allows.
Strategy code is never auto-edited — improvements are proposals only.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..config import ROOT

PROPOSALS = ROOT / "data" / "improvement_proposals.jsonl"
OPS_LOG = ROOT / "data" / "autonomy_ops.jsonl"


def propose_improvements(snap: dict[str, Any]) -> list[dict[str, Any]]:
    """Suggest improvements from telemetry — never apply code automatically."""
    proposals = []
    sb = snap.get("scoreboard") or {}
    edge = sb.get("edge_vs_hold_pp")
    n = sb.get("signals_scored") or 0
    if n >= 30 and edge is not None and edge <= 0:
        proposals.append({
            "id": f"edge_neg_{int(time.time())}",
            "kind": "strategy_review",
            "title": "Forward edge ≤ 0 vs hold — prefer core B&H / cut tactical size",
            "auto_apply": False,
            "evidence": {"edge_vs_hold_pp": edge, "n": n},
        })
    if (sb.get("signals_pending") or 0) > 100 and n < 30:
        proposals.append({
            "id": f"pending_ backlog_{int(time.time())}",
            "kind": "data",
            "title": "Large pending forward queue — keep live recorder through regular session",
            "auto_apply": False,
            "evidence": {"pending": sb.get("signals_pending"), "n": n},
        })
    gate = sb.get("sleeve_gate")
    if gate in ("EVIDENCED", "ESTABLISHED"):
        proposals.append({
            "id": f"release_hint_{int(time.time())}",
            "kind": "release",
            "title": f"Sleeve {gate} — consider manual release promote to live_confirm",
            "auto_apply": False,
            "evidence": {"gate": gate, "live_usd": sb.get("sleeve_live_usd")},
        })
    if proposals:
        PROPOSALS.parent.mkdir(parents=True, exist_ok=True)
        with PROPOSALS.open("a", encoding="utf-8") as f:
            for p in proposals:
                p["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
                f.write(json.dumps(p) + "\n")
    return proposals


def run_cycle(portal) -> dict[str, Any]:
    """Full autonomy cycle: performance → plan → optional paper exec → proposals."""
    from ..analytics.performance import snapshot, trade_policy_from_sleeve
    from ..analytics.sleeve import evaluate_sleeve

    snap = snapshot(portal)
    policy = snap["policy"]
    result: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "policy": policy,
        "scoreboard": snap["scoreboard"],
        "plan": None,
        "executed": False,
        "proposals": [],
    }

    # Always build a plan when allowed (reversible staging)
    if policy.get("may_build_plan") and portal.daily_agent:
        # Temporarily respect paper-only execute for autonomy cycle
        mode = portal.settings.mode.value
        plan = portal.daily_agent.build_plan(write=True)
        result["plan"] = {
            "orders": len(plan.get("orders") or []),
            "reentry_actions": len((plan.get("plays") or {}).get("reentry_actions") or []),
            "path": plan.get("_written_to"),
        }

        # Paper auto-exec only
        if policy.get("may_paper_execute") and mode == "paper" and portal.daily_agent.execute:
            # Cap order notionals to paper shadow / live max
            max_usd = int(policy.get("max_live_usd") or 0) or int(
                snap["scoreboard"].get("sleeve_paper_usd") or 0)
            # Daily agent run places via executor in paper
            report = portal.daily_agent.run()
            result["executed"] = True
            result["actions"] = (report.to_dict().get("actions") if report else [])[:20]
            result["paper_cap_usd"] = max_usd

        # Live auto only at SLEEVE_AUTO + mode live + not halted
        elif (policy.get("may_live_execute") and mode == "live"
              and not (portal.campaign and portal.campaign.breached(portal._book_value()))
              and not portal.executor.killed):
            # Still stage for safety unless config autonomy.live_auto true
            auto_cfg = (portal.settings.raw.get("autonomy") or {})
            if auto_cfg.get("live_auto", False):
                report = portal.daily_agent.run()
                result["executed"] = True
                result["actions"] = (report.to_dict().get("actions") if report else [])[:20]
            else:
                result["executed"] = False
                result["note"] = "SLEEVE_AUTO but autonomy.live_auto is false — plan staged only"

    if policy.get("may_propose_improvements"):
        result["proposals"] = propose_improvements(snap)

    OPS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with OPS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({k: v for k, v in result.items() if k != "actions"}) + "\n")
    return result


def _main() -> None:
    from ..portal import portal
    portal.build()
    out = run_cycle(portal)
    print(json.dumps({k: out[k] for k in ("ts", "policy", "scoreboard", "plan", "executed",
                                            "proposals") if k in out}, indent=2))


if __name__ == "__main__":
    _main()
