"""Attack / Defend / Readiness — the chief's queue, projected for a human operator.

This is a VIEW, not an agent. It generates no evidence and reaches no verdict of
its own; it re-files decisions the chief already produced. That is why it is
absent from the roster: adding it there would claim a seat in the maker/checker
graph for something that only rearranges what a checker already said.

WHY THE PROJECTION, IN NORMAN'S TERMS

The existing dashboard is organised by PRODUCER — themes, sleeve, options,
research, agents. That is a map of the system's org chart, not of the
operator's question. Norman calls the resulting cost the GULF OF EVALUATION:
the state is all present, but turning it into meaning takes twenty panels and
memorised thresholds. Re-filing by what the operator is deciding closes it.

  CONCEPTUAL MODEL   Three questions, three regions, always in the same place:
                     where do I commit (ATTACK), where is capital already at
                     risk (DEFEND), and can I trust the instruments at all
                     (READINESS). A row never moves between regions for
                     cosmetic reasons — spatial stability is what makes a
                     glance sufficient.

  NATURAL MAPPING    Posture is derived from the ACTION, not the topic. "Buy"
                     is attack whatever the subject; "trim" is defence even on
                     a name you like. Deriving from topic is what produces a
                     board where a stop-loss sits under "opportunity".

  SIGNIFIERS         Every row states its posture in WORDS. Colour is
                     redundant, never load-bearing: this codebase already
                     learned that a board speaking only in colour is unreadable
                     to a colourblind operator and in a screenshot.

  KNOWLEDGE IN THE WORLD   Each row carries its own falsifier, evidence with
                     source and as_of, and the guardrail that binds it. Nothing
                     required to judge a row lives in the operator's head.

  CONSTRAINT / FORCING FUNCTION   The dangerous error here is a defensive item
                     rendered as an attack — reading "cut this" as "buy this".
                     So the mapping is a table, not a heuristic, and anything
                     it cannot classify goes to UNCLASSIFIED rather than being
                     guessed into a column. An unclassified row is a visible
                     gap; a misfiled row is an invisible trap.

  LOCKOUT            Irreversible items (gate PARK) expose no action affordance
                     at all. The board is an instrument panel, not a trigger:
                     you cannot place an order from it, and nothing on it is
                     clickable into an irreversible act.

    python -m app.agent.posture
"""
from __future__ import annotations

import argparse
import json

ATTACK, DEFEND, READINESS, UNCLASSIFIED = "attack", "defend", "readiness", "unclassified"

REGION_QUESTION = {
    ATTACK: "where to commit capital",
    DEFEND: "where capital is already at risk",
    READINESS: "whether the instruments can be trusted",
    UNCLASSIFIED: "could not be filed — read before acting",
}

# The mapping is a TABLE, deliberately. A heuristic that guesses would
# occasionally file a trim as an opportunity, and that error is silent.
# Verbs first: posture follows the action, not the subject.
ATTACK_VERBS = {"buy", "open", "add", "enter", "scale_in", "accumulate"}
DEFEND_VERBS = {"sell", "trim", "exit", "close", "hedge", "reduce",
                "liquidate", "halt", "stop"}

# Only used when the verb is neutral (report/research), which is most of the
# queue: the chief phrases nearly everything as a proposal to look at something.
KIND_POSTURE = {
    "opportunity": ATTACK,
    "risk": DEFEND,
    "data": READINESS,
    "governance": READINESS,
    "research": READINESS,
}

# Subject-level overrides for owners whose output is unambiguously one posture,
# regardless of how the action happens to be worded.
OWNER_POSTURE = {
    "thesis_ledger": DEFEND,        # a graded thesis only ever warns
    "exit_discipline": DEFEND,
    "statusboard": READINESS,
    "program_manager": READINESS,
}


def classify(decision: dict) -> tuple[str, str]:
    """Return (region, why). Never guesses — unmappable input is labelled."""
    verb = (decision.get("action_verb") or "").lower()
    kind = (decision.get("kind") or "").lower()
    owner = (decision.get("owner") or "").lower()

    if verb in ATTACK_VERBS:
        return ATTACK, f"action '{verb}' opens or adds exposure"
    if verb in DEFEND_VERBS:
        return DEFEND, f"action '{verb}' reduces or protects exposure"
    if owner in OWNER_POSTURE:
        return OWNER_POSTURE[owner], f"owner '{owner}' reports only {OWNER_POSTURE[owner]}"
    if kind in KIND_POSTURE:
        return KIND_POSTURE[kind], f"kind '{kind}'"
    return UNCLASSIFIED, (f"no rule for verb '{verb}' / kind '{kind}' / owner "
                          f"'{owner}' — classify it rather than assume")


def _control_for(decision: dict, limits: dict) -> dict:
    """What CONSTRAINS this row — the half of the picture a claim alone omits.

    Norman's point about knowledge in the world: an operator should not have to
    remember that buys are capped at $X while reading a row proposing one.
    """
    gate = decision.get("gate", "")
    region, _ = classify(decision)
    binding = []
    if region == ATTACK:
        if limits.get("max_order_value"):
            binding.append(f"order cap ${limits['max_order_value']:,.0f}")
        if limits.get("max_position_value"):
            binding.append(f"position cap ${limits['max_position_value']:,.0f}")
        if limits.get("derisk_factor") not in (None, 1.0):
            binding.append(f"derisk x{limits['derisk_factor']}")
    elif region == DEFEND:
        if limits.get("trailing_drawdown_halt_pct"):
            binding.append(f"book halt at {limits['trailing_drawdown_halt_pct']}")
    return {
        "gate": gate,
        "gate_reason": decision.get("gate_reason", ""),
        # The lockout, stated as data so the template cannot forget it.
        "actionable_here": gate == "FINISH",
        "binding_limits": binding,
    }


def board() -> dict:
    """The chief's queue, re-filed by operator question."""
    from .chief import build_queue

    q = build_queue()
    limits: dict = {}
    try:
        from ..config import settings
        from ..portfolio.holdings import load_cash, load_holdings
        equity = sum(float(h["shares"]) * (float(h.get("last", 0)) or
                                           float(h.get("avg_price", 0)))
                     for h in load_holdings()) + (load_cash() or 0.0)
        L = settings.risk
        limits = {"max_order_value": L.order_cap(equity),
                  "max_position_value": L.position_cap(equity)}
    except Exception:
        limits = {}

    regions: dict[str, list] = {ATTACK: [], DEFEND: [], READINESS: [], UNCLASSIFIED: []}
    for d in q.get("decisions", []):
        region, why = classify(d)
        regions[region].append({
            "id": d["id"],
            "subject": d["subject"],
            "urgency": d["urgency"],
            "claim": d["claim"],
            "action": d["action"],
            "owner": d["owner"],
            "posture_why": why,
            # SUPPORT — everything needed to judge the claim, on the card.
            "support": {
                "falsifier": d["falsifier"],
                "evidence": d["evidence"],
                "evidence_count": len(d["evidence"]),
            },
            # CONTROL — what limits it, and whether it may be acted on at all.
            "control": _control_for(d, limits),
        })

    return {
        "as_of": q.get("as_of"),
        "regions": regions,
        "questions": REGION_QUESTION,
        "counts": {k: len(v) for k, v in regions.items()},
        "staged_awaiting_approval": q.get("staged_awaiting_approval", []),
        "schema_errors": q.get("schema_errors", []),
        "collectors_failed": q.get("collectors_failed", []),
        "doctrine": ("A row is filed by its ACTION, not its topic. Anything the "
                     "mapping cannot place goes to UNCLASSIFIED rather than being "
                     "guessed into a column — a visible gap beats an invisible "
                     "misfile. Nothing here is actionable: PARK items carry no "
                     "affordance and no order can be placed from this board."),
    }


def _main() -> None:
    ap = argparse.ArgumentParser(description="Attack / Defend / Readiness board.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    b = board()
    if args.json:
        print(json.dumps(b, indent=2))
        return

    print("=" * 78)
    print(f"  OPERATOR BOARD   {b['as_of']}")
    print("=" * 78)
    for region in (DEFEND, ATTACK, READINESS, UNCLASSIFIED):
        rows = b["regions"][region]
        if not rows and region == UNCLASSIFIED:
            continue
        print(f"\n  {region.upper()} — {REGION_QUESTION[region]}   ({len(rows)})")
        print("  " + "-" * 74)
        if not rows:
            print("    nothing")
            continue
        for r in rows:
            lock = "" if r["control"]["actionable_here"] else "   [PARK — needs approval]"
            print(f"    [{r['urgency']:>12}] {r['claim'][:70]}{lock}")
            print(f"                   support: {r['support']['evidence_count']} evidence · "
                  f"falsifier: {r['support']['falsifier'][:52]}")
            if r["control"]["binding_limits"]:
                print(f"                   control: {', '.join(r['control']['binding_limits'])}")

    if b["counts"][UNCLASSIFIED]:
        print(f"\n  {b['counts'][UNCLASSIFIED]} row(s) UNCLASSIFIED — add a mapping rule "
              f"rather than letting them default into a column.")
    print(f"\n  {b['doctrine']}")


if __name__ == "__main__":
    _main()
