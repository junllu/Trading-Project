"""Performance tracker — overall book + sleeve scoreboard.

Scoreboard is % return and edge vs hold, not distance to $1M. Writes
data/performance.json for the dashboard and autonomy receipts.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..config import ROOT

OUT = ROOT / "data" / "performance.json"
HISTORY = ROOT / "data" / "performance_history.jsonl"


def snapshot(portal=None) -> dict[str, Any]:
    """Build a performance snapshot from sleeve + campaign + forward record."""
    from .sleeve import evaluate_sleeve, save_status, STATUS_PATH
    from .confidence import score_forward

    status = evaluate_sleeve()
    save_status(status)
    sleeve = status.to_dict()

    book = {}
    if portal is not None:
        try:
            camp = portal.campaign
            bv = portal._book_value()
            if camp:
                book = camp.status(bv).to_dict()
        except Exception as exc:
            book = {"error": str(exc)}

    conf = score_forward(5).summary()
    out = {
        "as_of": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scoreboard": {
            "sleeve_gate": sleeve.get("gate"),
            "sleeve_live_usd": sleeve.get("recommended_sleeve_usd"),
            "sleeve_paper_usd": sleeve.get("paper_shadow_usd"),
            "edge_vs_hold_pp": sleeve.get("edge_vs_hold_pp"),
            "mean_signal_return_pct": sleeve.get("mean_signal_return_pct"),
            "mean_buyhold_return_pct": sleeve.get("mean_buyhold_return_pct"),
            "hit_rate_pct": sleeve.get("hit_rate_pct"),
            "signals_scored": sleeve.get("n"),
            "signals_pending": sleeve.get("pending"),
        },
        "book": book,
        "confidence": conf,
        "policy": trade_policy_from_sleeve(sleeve),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps(out) + "\n")
    return out


def trade_policy_from_sleeve(sleeve: dict[str, Any]) -> dict[str, Any]:
    """Map sleeve gate → what the system may do without a new human prompt.

    Irreversible live orders still respect TRADING_MODE and risk/halt.
    Self-modifying strategy code is never granted here.
    """
    gate = sleeve.get("gate") or "NO DATA"
    live_usd = int(sleeve.get("recommended_sleeve_usd") or 0)
    edge = sleeve.get("edge_vs_hold_pp")
    edge_ok = edge is None or edge > 0

    if gate == "NO DATA" or not edge_ok and live_usd == 0:
        return {
            "level": "OBSERVE",
            "may_record": True,
            "may_build_plan": True,
            "may_paper_execute": False,
            "may_live_execute": False,
            "max_live_usd": 0,
            "may_propose_improvements": True,
            "may_apply_code_changes": False,
            "reason": "Accumulate forward grades; no autonomous trading yet.",
        }
    if gate == "MEASURED":
        return {
            "level": "PAPER_AUTO",
            "may_record": True,
            "may_build_plan": True,
            "may_paper_execute": True,
            "may_live_execute": False,
            "max_live_usd": 0,
            "may_propose_improvements": True,
            "may_apply_code_changes": False,
            "reason": "Paper shadow $1k — learn with fake fills only.",
        }
    if gate == "EVIDENCED" and live_usd >= 1000:
        return {
            "level": "CONFIRM_LIVE",
            "may_record": True,
            "may_build_plan": True,
            "may_paper_execute": True,
            "may_live_execute": False,  # still stage for human until ESTABLISHED
            "max_live_usd": 1000,
            "may_propose_improvements": True,
            "may_apply_code_changes": False,
            "reason": "$1k live is defensible — stage orders for explicit approval.",
        }
    if gate == "ESTABLISHED" and live_usd >= 5000:
        return {
            "level": "SLEEVE_AUTO",
            "may_record": True,
            "may_build_plan": True,
            "may_paper_execute": True,
            "may_live_execute": True,  # only if TRADING_MODE=live AND not halted
            "max_live_usd": 5000,
            "may_propose_improvements": True,
            "may_apply_code_changes": False,
            "reason": "Established sleeve — auto within $5k + risk/halt only.",
        }
    return {
        "level": "OBSERVE",
        "may_record": True,
        "may_build_plan": True,
        "may_paper_execute": False,
        "may_live_execute": False,
        "max_live_usd": 0,
        "may_propose_improvements": True,
        "may_apply_code_changes": False,
        "reason": f"Gate {gate} / live ${live_usd} — conservative default.",
    }


def _main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    snap = snapshot()
    if args.json:
        print(json.dumps(snap, indent=2))
        return
    s = snap["scoreboard"]
    p = snap["policy"]
    print("=" * 64)
    print("PERFORMANCE / AUTONOMY POLICY")
    print("=" * 64)
    print(f"gate {s['sleeve_gate']}  scored {s['signals_scored']}  pending {s['signals_pending']}")
    print(f"edge vs hold {s['edge_vs_hold_pp']}  live ${s['sleeve_live_usd']}  paper ${s['sleeve_paper_usd']}")
    print(f"policy {p['level']}: {p['reason']}")
    print(f"paper_exec={p['may_paper_execute']}  live_exec={p['may_live_execute']}  "
          f"max_live=${p['max_live_usd']}  code_changes={p['may_apply_code_changes']}")


if __name__ == "__main__":
    _main()
