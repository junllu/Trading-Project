"""Capital sleeve ladder — confidence gate → allowed $ size.

Scoreboard is sleeve % return and edge vs buy-and-hold, not distance to $1M.
Size only moves when forward evidence clears a gate. Successful runs unlock
deeper analysis (deep_dive symbols), never automatic upsizing.

WHY THE GATE COUNTS DAYS, NOT SIGNALS

On 2026-09-27 this gate read ESTABLISHED and recommended $5,000 live. The
evidence was 4 trading days of 5-minute snapshots whose signals LOST 1.98% on
average — the "edge" was only that they lost less than holding in a falling
tape — and every one was computed on a price cache frozen at 09-04. A count
threshold alone could not see any of that. So each tier now requires ALL of:

  signals   graded (one per name per day — see confidence.py rule 3)
  days      distinct trading days: same-day signals share one market move
  span      calendar reach, so one calm month cannot stand in for a cycle
  mean > 0  the signals must MAKE money, not merely lose less than holding
  edge > 0  and beat holding the same names over the same windows
  t >= 2    overlap-adjusted, by day: distinguishable from luck
  max DD    measured, and within band; UNMEASURED blocks the top tier

A requirement that cannot be evaluated counts as failed. The gate reports
exactly which requirement is missing, so "why not yet" is never a guess.

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

# Evidence recorded before this date does not count. Everything earlier was
# computed on the 09-04 frozen price cache (fixed 09-27). Move it forward
# whenever the strategy that produces the signals changes — a new strategy
# inherits no evidence from the old one.
EVIDENCE_START = "2026-09-28"

# (gate, live_usd, paper_shadow_usd, requirements). Checked top-down; the
# highest tier whose requirements ALL pass wins.
TIERS = [
    ("ESTABLISHED", 5000, 5000,
     {"n": 250, "days": 80, "span_days": 120, "mean_pos": True, "edge_pos": True,
      "t_stat": 2.0, "max_dd": True}),
    ("EVIDENCED", 1000, 1000,
     {"n": 100, "days": 40, "mean_pos": True, "edge_pos": True, "t_stat": 2.0}),
    ("MEASURED", 0, 1000,
     {"n": 30, "days": 10}),
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
    days: int = 0
    t_stat: float | None = None
    next_gate: str | None = None
    missing: list[str] = field(default_factory=list)   # what the NEXT tier still needs

    def to_dict(self) -> dict:
        return asdict(self)


def _stage_hint(gate: str, live_usd: int) -> str:
    if live_usd <= 0:
        return "paper" if gate == "MEASURED" else "backtest_only"
    # Never suggest live_auto — human confirm stays required.
    return "live_confirm"


def unmet(req: dict, summary: dict, max_dd_pct: float | None) -> list[str]:
    """Every requirement in `req` that `summary` does not satisfy, in words."""
    out: list[str] = []
    n = int(summary.get("n") or 0)
    days = int(summary.get("days") or 0)
    if n < req.get("n", 0):
        out.append(f"signals {n}/{req['n']}")
    if days < req.get("days", 0):
        out.append(f"trading days {days}/{req['days']}")
    if "span_days" in req and int(summary.get("span_days") or 0) < req["span_days"]:
        out.append(f"span {int(summary.get('span_days') or 0)}/{req['span_days']} calendar days")
    mean = summary.get("mean_signal_return_pct")
    if req.get("mean_pos") and not (mean is not None and mean > 0):
        out.append(f"mean signal return must be > 0 (is {mean})")
    edge = summary.get("edge_vs_hold_pp")
    if req.get("edge_pos") and not (edge is not None and edge > MIN_EDGE_PP):
        out.append(f"edge vs hold must be > 0 (is {edge})")
    t = summary.get("t_stat")
    if "t_stat" in req and not (t is not None and t >= req["t_stat"]):
        out.append(f"t-stat >= {req['t_stat']} (is {t})")
    if req.get("max_dd"):
        if max_dd_pct is None:
            out.append("sleeve max drawdown unmeasured")
        elif max_dd_pct > MAX_DD_PCT_FOR_5K:
            out.append(f"sleeve max drawdown {max_dd_pct:.1f}% > {MAX_DD_PCT_FOR_5K:.0f}%")
    return out


def evaluate_from_summary(
    summary: dict[str, Any],
    scored: list[ScoredSignal] | None = None,
    sleeve_max_dd_pct: float | None = None,
) -> SleeveStatus:
    """Pure gate logic — unit-testable without network."""
    n = int(summary.get("n") or 0)
    edge = summary.get("edge_vs_hold_pp")

    gate, live, paper = "NO DATA", 0, 0
    for name, live_usd, paper_usd, req in TIERS:
        if not unmet(req, summary, sleeve_max_dd_pct):
            gate, live, paper = name, live_usd, paper_usd
            break

    order = ["NO DATA"] + [t[0] for t in reversed(TIERS)]
    nxt = order[order.index(gate) + 1] if gate != order[-1] else None
    missing = unmet(next(t[3] for t in TIERS if t[0] == nxt), summary,
                    sleeve_max_dd_pct) if nxt else []

    advice_parts: list[str] = []
    if n == 0:
        advice_parts.append("Record signals via the live loop. Do not size off this.")
    elif gate == "ESTABLISHED":
        advice_parts.append("All requirements met — $5k live is defensible; promote explicitly.")
    elif gate == "EVIDENCED":
        advice_parts.append("Significant positive edge over 40+ days — $1k live sleeve is "
                            "defensible; promote explicitly.")
    elif gate == "MEASURED":
        advice_parts.append("Direction only — run a $1k paper shadow book; no live size yet.")
    else:
        advice_parts.append("Not enough independent evidence yet. Keep recording.")
    if edge is not None and edge <= MIN_EDGE_PP:
        advice_parts.append(f"Edge vs hold is {edge:+.3f} pp — do not fund live.")
    if missing:
        advice_parts.append(f"{nxt} still needs: " + "; ".join(missing) + ".")

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
        days=int(summary.get("days") or 0),
        t_stat=summary.get("t_stat"),
        next_gate=nxt,
        missing=missing,
    )


def evaluate_sleeve(horizon_days: int = 5, min_abs_score: float = 0.2,
                    since: str | None = EVIDENCE_START) -> SleeveStatus:
    rep: ConfidenceReport = score_forward(horizon_days, min_abs_score, since=since)
    # No sleeve equity curve exists yet, so drawdown is honestly unmeasured —
    # which blocks ESTABLISHED rather than silently skipping the check, as the
    # previous version did by never passing a value at all.
    return evaluate_from_summary(rep.summary(), scored=rep.scored, sleeve_max_dd_pct=None)


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
    print(f"gate                  : {status.gate}   (evidence since {EVIDENCE_START})")
    print(f"signals scored        : {status.n} over {status.days} trading day(s)   "
          f"(pending {status.pending}, horizon {status.horizon} sessions)")
    if status.edge_vs_hold_pp is not None:
        print(f"mean signal return    : {status.mean_signal_return_pct:+.3f}%")
        print(f"mean buy-hold return  : {status.mean_buyhold_return_pct:+.3f}%")
        print(f"EDGE vs holding       : {status.edge_vs_hold_pp:+.3f} pp")
        print(f"hit rate              : {status.hit_rate_pct}%")
        print(f"t-stat (by day)       : {status.t_stat}")
    print(f"recommended LIVE      : ${status.recommended_sleeve_usd}")
    print(f"paper shadow book     : ${status.paper_shadow_usd}")
    print(f"release stage hint    : {status.release_stage_hint}  (promote manually)")
    print(f"advice                : {status.advice}")
    if status.missing:
        print(f"to reach {status.next_gate:<12}: ")
        for m in status.missing:
            print(f"    - {m}")
    if status.rewards_unlocked:
        print(f"rewards unlocked      : {', '.join(status.rewards_unlocked)}")
        print("  → python -m app.analytics.sleeve --reward")
    print()
    print("Ladder: $0 → paper $1k (MEASURED: 30 sig/10 days) → live $1k (EVIDENCED: "
          "100/40 days, t>=2) → live $5k (ESTABLISHED: 250/80 days, 120d span, DD<=25%)")
    print(f"Saved: {STATUS_PATH}")


if __name__ == "__main__":
    _main()
