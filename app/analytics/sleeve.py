"""Capital sleeve ladder — confidence gate → allowed $ size.

Scoreboard is sleeve % return and edge vs buy-and-hold, not distance to $1M.
Size only moves when forward evidence clears a gate. Successful runs unlock
deeper analysis (deep_dive symbols), never automatic upsizing.

    python -m app.analytics.sleeve
    python -m app.analytics.sleeve --json
    python -m app.analytics.sleeve --reward
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..config import ROOT
from .confidence import ConfidenceReport, ScoredSignal, score_forward

STATUS_PATH = ROOT / "data" / "sleeve_status.json"
HISTORY_PATH = ROOT / "data" / "sleeve_history.jsonl"

# (min_n, gate, live_usd, paper_shadow_usd)
LADDER = [
    (0, "NO DATA", 0, 0),
    (30, "MEASURED", 0, 1000),
    (100, "EVIDENCED", 1000, 1000),
    (250, "ESTABLISHED", 5000, 5000),
]

MAX_DD_PCT_FOR_5K = 25.0
MIN_EDGE_PP = 0.0


@dataclass
class SleeveStatus:
    gate: str
    n: int
    pending: int
    horizon: int
    hit_rate_pct: float | None
    mean_signal_return_pct: float | None
    mean_buyhold_return_pct: float | None
    edge_vs_hold_pp: float | None
    recommended_sleeve_usd: int
    paper_shadow_usd: int
    release_stage_hint: str
    rewards_unlocked: list[str] = field(default_factory=list)
    deep_dive_symbols: list[str] = field(default_factory=list)
    advice: str = ""
    as_of: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _stage_hint(gate: str, live_usd: int) -> str:
    if live_usd <= 0:
        return "paper" if gate == "MEASURED" else "backtest_only"
    # Never suggest live_auto — human confirm stays required.
    return "live_confirm"


def _pick_ladder(n: int) -> tuple[str, int, int]:
    gate, live, paper = LADDER[0][1], LADDER[0][2], LADDER[0][3]
    for min_n, g, live_usd, paper_usd in LADDER:
        if n >= min_n:
            gate, live, paper = g, live_usd, paper_usd
    return gate, live, paper


def evaluate_from_summary(
    summary: dict[str, Any],
    scored: list[ScoredSignal] | None = None,
    sleeve_max_dd_pct: float | None = None,
) -> SleeveStatus:
    """Pure gate logic — unit-testable without network."""
    n = int(summary.get("n") or 0)
    edge = summary.get("edge_vs_hold_pp")
    gate, live, paper = _pick_ladder(n)

    advice_parts: list[str] = []
    if n == 0:
        advice_parts.append("Record signals via the live loop. Do not size off this.")
    elif edge is not None and edge <= MIN_EDGE_PP:
        live = 0
        advice_parts.append(
            f"Edge vs hold is {edge:+.3f} pp — keep paper/shadow only; do not fund live."
        )
        if gate in ("EVIDENCED", "ESTABLISHED"):
            gate = "MEASURED" if n >= 30 else "NO DATA"
            paper = 1000 if n >= 30 else 0
    elif gate == "ESTABLISHED" and sleeve_max_dd_pct is not None:
        if sleeve_max_dd_pct > MAX_DD_PCT_FOR_5K:
            live = 1000
            advice_parts.append(
                f"Sleeve maxDD {sleeve_max_dd_pct:.1f}% > {MAX_DD_PCT_FOR_5K:.0f}% "
                "— cap live at $1k until drawdown improves."
            )
        else:
            advice_parts.append("ESTABLISHED with DD in band — $5k live is defensible; promote explicitly.")
    elif gate == "EVIDENCED":
        advice_parts.append("Positive edge on ≥100 signals — $1k live sleeve is defensible; promote explicitly.")
    elif gate == "MEASURED":
        advice_parts.append("Direction only — run a $1k paper shadow book; no live size yet.")
    else:
        advice_parts.append(str(summary.get("advice") or "Keep recording."))

    rewards: list[str] = []
    dive: list[str] = []
    if edge is not None and edge > MIN_EDGE_PP and scored:
        rewards.append("deep_dive")
        # Top winners / worst losers by signed return
        ranked = sorted(scored, key=lambda s: s.signed_return, reverse=True)
        winners = [s.symbol for s in ranked[:3]]
        losers = [s.symbol for s in ranked[-3:]]
        # preserve order, unique
        seen: set[str] = set()
        for sym in winners + losers:
            if sym not in seen:
                seen.add(sym)
                dive.append(sym)
        rewards.append("calibration_fit")

    return SleeveStatus(
        gate=gate,
        n=n,
        pending=int(summary.get("pending") or 0),
        horizon=int(summary.get("horizon") or 5),
        hit_rate_pct=summary.get("hit_rate_pct"),
        mean_signal_return_pct=summary.get("mean_signal_return_pct"),
        mean_buyhold_return_pct=summary.get("mean_buyhold_return_pct"),
        edge_vs_hold_pp=edge,
        recommended_sleeve_usd=live,
        paper_shadow_usd=paper,
        release_stage_hint=_stage_hint(gate, live),
        rewards_unlocked=rewards,
        deep_dive_symbols=dive,
        advice=" ".join(advice_parts),
        as_of=time.strftime("%Y-%m-%d %H:%M:%S"),
    )


def evaluate_sleeve(horizon_days: int = 5, min_abs_score: float = 0.2) -> SleeveStatus:
    rep: ConfidenceReport = score_forward(horizon_days, min_abs_score)
    return evaluate_from_summary(rep.summary(), scored=rep.scored)


def save_status(status: SleeveStatus) -> Path:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    prev = None
    if STATUS_PATH.exists():
        try:
            prev = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            prev = None
    payload = status.to_dict()
    STATUS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    changed = (
        prev is None
        or prev.get("gate") != status.gate
        or prev.get("recommended_sleeve_usd") != status.recommended_sleeve_usd
    )
    if changed:
        with HISTORY_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")
    return STATUS_PATH


def print_reward_plan(status: SleeveStatus) -> None:
    print("=" * 64)
    print("REWARD — deeper analysis (not more size)")
    print("=" * 64)
    if not status.rewards_unlocked:
        print("No reward yet. Need edge vs hold > 0 on graded signals.")
        return
    print(f"unlocked : {', '.join(status.rewards_unlocked)}")
    print(f"sleeve   : still ${status.recommended_sleeve_usd} live "
          f"(paper shadow ${status.paper_shadow_usd}) — size is gated separately")
    if status.deep_dive_symbols:
        print("\nDeep-dive these symbols (winners + losers):")
        for sym in status.deep_dive_symbols:
            print(f"  python -m app.analytics.deep_dive {sym}")
    print("\nAlso useful:")
    print("  python -m app.analytics.calibration")
    print("  python -m app.analytics.confidence --horizon", status.horizon)


def _main() -> None:
    ap = argparse.ArgumentParser(description="Confidence-gated capital sleeve status")
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--min-score", type=float, default=0.2)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--reward", action="store_true",
                    help="print deeper-analysis unlocks for a successful edge")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()

    status = evaluate_sleeve(args.horizon, args.min_score)
    if not args.no_save:
        save_status(status)

    if args.json:
        print(json.dumps(status.to_dict(), indent=2))
        return

    if args.reward:
        print_reward_plan(status)
        return

    print("=" * 64)
    print("SLEEVE LADDER — % return & edge vs hold → size")
    print("=" * 64)
    print(f"gate                  : {status.gate}")
    print(f"signals scored        : {status.n}   (pending {status.pending}, horizon {status.horizon}d)")
    if status.edge_vs_hold_pp is not None:
        print(f"mean signal return    : {status.mean_signal_return_pct:+.3f}%")
        print(f"mean buy-hold return  : {status.mean_buyhold_return_pct:+.3f}%")
        print(f"EDGE vs holding       : {status.edge_vs_hold_pp:+.3f} pp")
        print(f"hit rate              : {status.hit_rate_pct}%")
    print(f"recommended LIVE      : ${status.recommended_sleeve_usd}")
    print(f"paper shadow book     : ${status.paper_shadow_usd}")
    print(f"release stage hint    : {status.release_stage_hint}  (promote manually)")
    print(f"advice                : {status.advice}")
    if status.rewards_unlocked:
        print(f"rewards unlocked      : {', '.join(status.rewards_unlocked)}")
        print("  → python -m app.analytics.sleeve --reward")
    print()
    print("Ladder: $0 → paper $1k (MEASURED) → live $1k (EVIDENCED) → live $5k (ESTABLISHED)")
    print(f"Saved: {STATUS_PATH}")


if __name__ == "__main__":
    _main()
