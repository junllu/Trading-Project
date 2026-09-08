"""Strategy release â€” the packaged, versioned form a refined backtest setup
takes once it's proven itself on real history, ready for a local LLM (or
Claude) to execute repeatedly.

A release freezes three things:
  - the Setup config (weights, thresholds, rebalance cadence) that was tested
  - which analyst backs the "analyst" input, if any (local LLM model, or none)
  - the backtest metrics that justified releasing it, and on what data

Crucially, a release grants NO live trading authority by itself. `stage`
always starts at "backtest_only" and can only move forward via `promote()`,
called deliberately â€” never as a side effect of running a backtest. Even at
"live_confirm" or "live_auto", the risk manager's caps, the campaign drawdown
halt/kill-switch, and the explicit per-session human-approval rule in
CLAUDE.md still gate every real order. This file describes strategy intent;
it does not bypass any guardrail.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..config import ROOT
from .setups import Setup

RELEASES_DIR = ROOT / "data" / "releases"

# Ordered so "promote" can sanity-check forward motion, but nothing here
# enforces that automatically â€” promote() accepts any explicit target.
STAGES = ("backtest_only", "paper", "live_confirm", "live_auto")


@dataclass
class AnalystConfig:
    kind: str = "none"              # "none" | "local_llm" | "claude" | "heuristic"
    model: str = ""
    base_url: str = ""

    def to_dict(self) -> dict:
        return {"kind": self.kind, "model": self.model, "base_url": self.base_url}


@dataclass
class StrategyRelease:
    name: str
    version: str
    setup: Setup
    analyst: AnalystConfig
    validation_metrics: dict            # a BacktestResult.to_dict() snapshot
    validation_symbols: list[str]
    validation_window: str              # e.g. "2022-01-01..2026-09-06"
    stage: str = "backtest_only"        # never starts higher; promote() explicitly
    trigger: str = "manual"             # descriptive only until wired to a scheduler
    created: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name, "version": self.version, "created": self.created,
            "stage": self.stage, "trigger": self.trigger,
            "setup": self.setup.to_dict(),
            "analyst": self.analyst.to_dict(),
            "validation": {
                "symbols": self.validation_symbols,
                "window": self.validation_window,
                "metrics": self.validation_metrics,
            },
            "notes": self.notes,
            "guardrail_note": (
                "This release describes strategy intent only. It grants no live "
                "trading authority: the risk manager's position/order caps, the "
                "campaign drawdown halt/kill-switch, and the explicit per-session "
                "human-approval rule in CLAUDE.md apply regardless of `stage`."
            ),
        }

    def path(self) -> Path:
        return RELEASES_DIR / f"{self.name}_{self.version}.json"

    def save(self) -> Path:
        RELEASES_DIR.mkdir(parents=True, exist_ok=True)
        p = self.path()
        p.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return p


def load_release(name: str, version: str) -> dict:
    p = RELEASES_DIR / f"{name}_{version}.json"
    return json.loads(p.read_text(encoding="utf-8"))


def list_releases() -> list[dict]:
    if not RELEASES_DIR.exists():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(RELEASES_DIR.glob("*.json"))]



def suggest_stage_from_sleeve(sleeve: dict) -> str:
    """Map sleeve status → release stage hint. Never returns live_auto."""
    hint = (sleeve or {}).get("release_stage_hint") or "backtest_only"
    if hint not in STAGES:
        return "backtest_only"
    if hint == "live_auto":
        return "live_confirm"
    return hint
def promote(name: str, version: str, stage: str) -> dict:
    """Advance (or roll back) a release's stage. Always explicit â€” never automatic."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage '{stage}'. Known: {STAGES}")
    p = RELEASES_DIR / f"{name}_{version}.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    old = data["stage"]
    data["stage"] = stage
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    data["notes"] = (data.get("notes", "") + f"\n[{stamp}] promoted {old} -> {stage}").strip()
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def _main() -> None:
    ap = argparse.ArgumentParser(description="List or promote strategy releases.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("suggest")
    p_promote = sub.add_parser("promote")
    p_promote.add_argument("name")
    p_promote.add_argument("version")
    p_promote.add_argument("stage", choices=STAGES)
    args = ap.parse_args()

    if args.cmd == "list":
        releases = list_releases()
        if not releases:
            print(f"No releases in {RELEASES_DIR}")
            return
        for r in releases:
            m = r.get("validation", {}).get("metrics", {})
            print(f"{r['name']}_{r['version']}  stage={r['stage']:<13}  "
                  f"CAGR={m.get('cagr_pct', '?')}%  Sharpe={m.get('sharpe', '?')}  "
                  f"maxDD={m.get('max_drawdown_pct', '?')}%  created={r['created']}")
    elif args.cmd == "suggest":
        from ..analytics.sleeve import evaluate_sleeve
        status = evaluate_sleeve()
        hint = suggest_stage_from_sleeve(status.to_dict())
        print(f"gate={status.gate}  live=${status.recommended_sleeve_usd}  "
              f"paper_shadow=${status.paper_shadow_usd}")
        print(f"suggested release stage: {hint}")
        print("(promote is still manual — run: python -m app.backtest.release promote "
              "<name> <version> <stage>)")
    elif args.cmd == "promote":
        data = promote(args.name, args.version, args.stage)
        print(f"{args.name}_{args.version} -> stage={data['stage']}")


if __name__ == "__main__":
    _main()

