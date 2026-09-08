"""The program manager — is the PROGRAM on track, and what is actually blocking it.

WHY A PM IS DANGEROUS HERE, AND WHAT THAT FORCES

The instinctive job of a program manager is to close the gap to plan. In a
trading system that instinct is the single most destructive thing you can
automate. The plan is $1,000,000 by end-2027; the campaign is behind; the
"obvious" PM moves are to raise position caps, loosen the drawdown halt, or
concentrate harder. Each one converts a pace problem into a ruin problem, and
the user's own standing rule — "make no mistake" — outranks the target.

So this agent is built with the opposite reflex. It reports the gap and is
FORBIDDEN from proposing anything that closes the gap by taking more risk.
`validate_proposals()` enforces that: a proposal touching risk limits, the
drawdown halt, position sizing or focus concentration is rejected outright,
not merely discouraged. A PM that could talk the book into more leverage when
behind schedule is worse than no PM at all.

WHAT IT MAY PROPOSE

Process and evidence work only — the things that are genuinely behind:
wire an agent the graph names but cannot invoke, harvest data a check is
starving for, resolve a blocker that needs a human, retire a claim that has no
falsifier. All reversible, all finishable without approval.

RELATION TO THE CHIEF

The chief is the sole COORDINATOR and that is an enforced invariant — the
roster fails validation with two. So this is a CHECKER, not a second gate. It
grades program execution and emits Decisions in the chief's schema; the chief
routes them into the one queue with everything else. It never gates, never
stages an irreversible action, and never executes.

NOT A SELF-IMPROVING LOOP (roster INVARIANT 6)

It does not read last week's P&L and rewrite strategy. It reads whether the
WORK happened. "The calibration scorer has never run" is a program fact;
"conviction underperformed last week so lower the threshold" is in-sample
fitting, and is exactly the mechanism behind this project's ~16pp overfit gap.
The distinction is enforced below by what the collectors are allowed to read:
execution state and data coverage, never realised returns.

    python -m app.agent.program
    python -m app.agent.program --json
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime

from .chief import (DATA, Decision, Evidence, GOVERNANCE, NOW, RESEARCH,
                    THIS_QUARTER, THIS_WEEK, WATCH)

TARGET_USD = 1_000_000
DEADLINE = date(2027, 12, 31)

# Proposals that close a pace gap by taking more risk. Rejected, not ranked.
# Phrased as substrings because the failure is semantic, not syntactic: any
# wording that amounts to "we are behind, so loosen the guardrail" belongs here.
FORBIDDEN_PROPOSAL_TERMS = (
    "raise the position cap", "raise position cap", "increase position cap",
    "raise the order cap", "increase order cap",
    "loosen the halt", "widen the halt", "raise the drawdown halt",
    "disable the halt", "relax the guardrail", "loosen the guardrail",
    "increase leverage", "add leverage", "use margin",
    "concentrate further", "increase position size to catch up",
    "lower the conviction threshold",
)


def _campaign_pace() -> dict:
    """Where the book stands against the target — reported, never acted on."""
    try:
        from ..portfolio.holdings import load_cash, load_holdings
        holdings = load_holdings()
        equity = sum(float(h["shares"]) * (float(h.get("last", 0)) or
                                           float(h.get("avg_price", 0)))
                     for h in holdings) + (load_cash() or 0.0)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}

    days_left = (DEADLINE - date.today()).days
    years = max(days_left / 365.25, 1e-9)
    required_cagr = ((TARGET_USD / equity) ** (1 / years) - 1) if equity > 0 else float("inf")
    return {
        "equity": round(equity, 2),
        "target": TARGET_USD,
        "progress_pct": round(equity / TARGET_USD * 100, 2),
        "days_remaining": days_left,
        "required_cagr_pct": round(required_cagr * 100, 1),
        "multiple_needed": round(TARGET_USD / equity, 2) if equity > 0 else None,
    }


def _delivery() -> dict:
    """Which agents the graph NAMES but cannot actually invoke.

    Reads the roster's own wiring rather than running anything. An agent listed
    with no entrypoint is outstanding work wearing the costume of a shipped
    feature, and it is invisible on every other report.
    """
    from .roster import COORDINATOR, ROSTER
    unwired, missing_module = [], []
    for a in ROSTER:
        if a.kind == COORDINATOR:
            continue
        if not a.module:
            missing_module.append(a.name)
        elif not a.entrypoint:
            unwired.append(a.name)
    return {
        "total": len(ROSTER),
        "unwired": unwired,
        "no_module": missing_module,
        "wired_pct": round((len(ROSTER) - len(unwired) - len(missing_module))
                           / len(ROSTER) * 100, 1),
    }


def _evidence_debt() -> dict:
    """Out-of-sample evidence the program claims to have but does not.

    forward_record.jsonl is the only record that cannot be tuned after the
    fact. If it is thin, every backtested claim in the system is unbacked by
    anything the future has not already seen.
    """
    from ..config import ROOT
    p = ROOT / "data" / "forward_record.jsonl"
    rows = 0
    if p.exists():
        try:
            rows = sum(1 for line in p.open("r", encoding="utf-8") if line.strip())
        except Exception:
            rows = 0
    return {"forward_record_rows": rows,
            "calibration_possible": rows >= 100,
            "note": ("Calibration needs resolved outcomes. Below ~100 rows any "
                     "Brier/ECE figure is describing noise.")}


def _blockers() -> list[dict]:
    """Work that is stopped pending something only a human can do.

    Named explicitly because a blocker that lives in someone's head is
    indistinguishable, on every dashboard, from work nobody started.
    """
    out = []
    try:
        from ..data.minute import coverage
        from ..analytics.statusboard import MIN_SESSIONS_FOR_STOP_VERDICT
        cov = coverage()
        best = max((v["days"] for v in cov.values()), default=0)
        # The portal cannot fetch minute bars itself: the .env key authenticates
        # but has no market-data entitlement, so bulk history depends either on a
        # purchase or on an in-session harvest.
        out.append({
            "id": "webull_market_data_subscription",
            "what": "Webull OpenAPI market-data subscription not active on WEBULL_APP_KEY",
            "blocks": "unattended minute-bar harvesting by the portal itself",
            "owner": "user",
            "workaround": "harvest in-session via the claude_ai_Webull connector",
            "severity": "medium" if best >= MIN_SESSIONS_FOR_STOP_VERDICT else "high",
        })
    except Exception:
        pass
    return out


def validate_proposals(decisions: list[Decision]) -> list[str]:
    """Reject any proposal that closes the pace gap by taking more risk.

    Enforced rather than documented. This is the one rule whose violation would
    look reasonable in a report — "behind plan, so size up" reads like diligence
    right up until it ends the campaign.
    """
    errs = []
    for d in decisions:
        text = f"{d.claim} {d.action}".lower()
        for term in FORBIDDEN_PROPOSAL_TERMS:
            if term in text:
                errs.append(f"{d.id}: proposes '{term}' — the PM may not close a "
                            f"pace gap by loosening a guardrail")
        if d.gate[0] != "FINISH":
            errs.append(f"{d.id}: PM proposals must be reversible work; "
                        f"'{d.action_verb}' parks for approval and belongs to the chief")
    return errs


def decisions() -> list[Decision]:
    """Program findings, in the chief's schema so they join the one queue."""
    out: list[Decision] = []
    today = datetime.now().strftime("%Y-%m-%d")

    pace = _campaign_pace()
    if "error" not in pace:
        # Reported as a FACT with no remedy attached. The remedy for "behind
        # plan" is not this agent's to propose — see the module docstring.
        out.append(Decision(
            id="program:pace", subject="campaign", subject_type="portfolio",
            kind=GOVERNANCE, urgency=THIS_QUARTER,
            claim=(f"${pace['equity']:,.0f} against $1M by {DEADLINE} — "
                   f"{pace['progress_pct']:.1f}% there, {pace['multiple_needed']}x "
                   f"needed in {pace['days_remaining']}d "
                   f"({pace['required_cagr_pct']:.0f}%/yr)"),
            falsifier=(f"required CAGR falls below 25%/yr, or the deadline moves"),
            owner="program_manager",
            action="note the pace; no risk change is proposed to close it",
            action_verb="report",
            evidence=[
                Evidence("holdings.yaml", "equity", f"${pace['equity']:,.0f}", today),
                Evidence("program_manager", "required_cagr",
                         f"{pace['required_cagr_pct']:.0f}%/yr", today),
            ]))

    d = _delivery()
    if d["unwired"] or d["no_module"]:
        stuck = d["unwired"] + d["no_module"]
        out.append(Decision(
            id="program:unwired", subject="agent graph", subject_type="subsystem",
            kind=GOVERNANCE, urgency=THIS_WEEK,
            claim=(f"{len(stuck)} of {d['total']} agents are named by the roster but "
                   f"cannot be invoked: {', '.join(stuck)}"),
            falsifier="each named agent exposes a no-arg entrypoint and run_pipeline runs it",
            owner="program_manager",
            action="wire an entrypoint for each, or remove it from the roster",
            action_verb="research",
            evidence=[Evidence("roster", "wired_pct", f"{d['wired_pct']}%", today)]))

    ev = _evidence_debt()
    if not ev["calibration_possible"]:
        out.append(Decision(
            id="program:evidence_debt", subject="forward record",
            subject_type="subsystem", kind=DATA, urgency=THIS_WEEK,
            claim=(f"only {ev['forward_record_rows']} forward-record rows exist — "
                   f"no claim in the system has out-of-sample support yet"),
            falsifier="forward_record.jsonl passes 100 rows with resolved outcomes",
            owner="program_manager",
            action="run the live recorder through regular sessions to accumulate rows",
            action_verb="research",
            evidence=[Evidence("live_recorder", "rows",
                               str(ev["forward_record_rows"]), today)]))

    for b in _blockers():
        out.append(Decision(
            id=f"program:blocked:{b['id']}", subject=b["what"][:40],
            subject_type="subsystem", kind=RESEARCH,
            urgency=NOW if b["severity"] == "high" else WATCH,
            claim=f"{b['what']} — blocks {b['blocks']}",
            falsifier=f"the blocker clears and {b['blocks']} runs unattended",
            owner="program_manager",
            action=f"owner={b['owner']}; workaround: {b['workaround']}",
            action_verb="report",
            evidence=[Evidence("program_manager", "severity", b["severity"], today)]))

    return out


def report() -> dict:
    ds = decisions()
    errs = validate_proposals(ds)
    return {
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "pace": _campaign_pace(),
        "delivery": _delivery(),
        "evidence_debt": _evidence_debt(),
        "blockers": _blockers(),
        "decisions": [d.to_dict() for d in ds],
        "proposal_violations": errs,
        "doctrine": ("The PM reports the gap to plan and may not propose closing it "
                     "by taking more risk. Capital preservation outranks the target."),
    }


def _main() -> None:
    ap = argparse.ArgumentParser(description="Program manager — is the program on track.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    r = report()
    if args.json:
        print(json.dumps(r, indent=2))
        return

    print("=" * 78)
    print("  PROGRAM MANAGER — execution, not strategy")
    print("=" * 78)

    p = r["pace"]
    if "error" not in p:
        print(f"\n  PACE      ${p['equity']:,.0f} -> $1,000,000 by {DEADLINE}")
        print(f"            {p['progress_pct']:.1f}% there · {p['multiple_needed']}x needed "
              f"· {p['days_remaining']}d left · {p['required_cagr_pct']:.0f}%/yr required")

    d = r["delivery"]
    print(f"\n  DELIVERY  {d['wired_pct']}% of the graph is invokable")
    if d["unwired"]:
        print(f"            not wired: {', '.join(d['unwired'])}")
    if d["no_module"]:
        print(f"            no module: {', '.join(d['no_module'])}")

    ev = r["evidence_debt"]
    print(f"\n  EVIDENCE  {ev['forward_record_rows']} forward-record rows "
          f"({'calibration possible' if ev['calibration_possible'] else 'too few to calibrate'})")

    if r["blockers"]:
        print("\n  BLOCKED")
        for b in r["blockers"]:
            print(f"            [{b['severity']:>6}] {b['what']}")
            print(f"                     owner={b['owner']} · {b['workaround']}")

    print(f"\n  {'-' * 74}")
    print(f"  {len(r['decisions'])} item(s) proposed to the chief's queue")
    for x in r["decisions"]:
        print(f"    [{x['urgency']:>12}] {x['claim'][:88]}")

    if r["proposal_violations"]:
        print("\n  DOCTRINE VIOLATIONS (proposals rejected):")
        for e in r["proposal_violations"]:
            print(f"    FAIL  {e}")
    else:
        print("\n  doctrine holds: no proposal closes the pace gap by taking more risk")


if __name__ == "__main__":
    _main()
