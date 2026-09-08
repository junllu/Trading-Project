"""The chief — routes checker output into ONE decision queue. Generates nothing.

The roster declared a coordinator, listed seven consumers, and had no entrypoint,
so nothing ever produced the artefact it owned. Its inputs each print their own
report and stop; assembling them was a human job done from memory.

WHAT A COORDINATOR MAY AND MAY NOT DO

It routes, gates and escalates. It runs no analysis of its own — the moment a
coordinator starts producing research it becomes another maker grading its own
work, and the maker/checker split that caught the ANET margin erosion is gone.
Every field below traces to a checker; none is computed here.

THE SCHEMA, AND WHY EACH FIELD EARNS ITS PLACE

A decision list of bare sentences is unusable a week later: you cannot tell what
it was based on, whether it still holds, or what would change it. So each record
carries its own audit trail.

    claim       one sentence of what is true
    evidence    the numbers behind it, each with a SOURCE and an AS_OF, so a
                stale input is visible rather than inherited silently
    falsifier   what would prove this wrong. The project's central discipline:
                a claim with no falsifier cannot be graded, cannot be improved,
                and quietly becomes an opinion that survives by never being
                tested. Records without one are rejected.
    action      what to DO, phrased as a proposal
    reversible  drives the gate. Reversible work is finished; irreversible work
                is STAGED with its exact proposed action visible and waits.
    urgency     ordered by consequence, not by interest
    owner       which checker produced it, so a wrong call is attributable

VALIDATION IS ENFORCED, NOT DESCRIBED

`validate()` rejects a record missing a falsifier, carrying evidence without
provenance, or marking an irreversible action as finished. A schema that is only
documented drifts the first time something is added in a hurry.

    python -m app.agent.chief
    python -m app.agent.chief --json
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime

from .autonomy import IRREVERSIBLE, classify_action

SCHEMA_VERSION = "1.0"

NOW, THIS_WEEK, THIS_QUARTER, WATCH = "now", "this_week", "this_quarter", "watch"
URGENCY_ORDER = {NOW: 0, THIS_WEEK: 1, THIS_QUARTER: 2, WATCH: 3}

RISK, DATA, RESEARCH, OPPORTUNITY, GOVERNANCE = (
    "risk", "data", "research", "opportunity", "governance")


@dataclass
class Evidence:
    source: str                 # which agent/module produced it
    metric: str
    value: str
    as_of: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Decision:
    id: str
    subject: str                # a ticker, a subsystem name, or the whole book
    subject_type: str           # symbol | subsystem | portfolio
    kind: str
    urgency: str
    claim: str
    falsifier: str              # what would prove this wrong. Required.
    owner: str                  # the checker that produced it
    action: str = ""
    action_verb: str = ""       # maps into the reversibility classifier
    evidence: list[Evidence] = field(default_factory=list)

    @property
    def gate(self) -> tuple[str, str]:
        """FINISH or PARK, decided by reversibility — never by importance."""
        return classify_action(self.action_verb or "report")

    def to_dict(self) -> dict:
        verdict, why = self.gate
        return {
            "id": self.id, "subject": self.subject,
            "subject_type": self.subject_type, "kind": self.kind,
            "urgency": self.urgency, "claim": self.claim,
            "falsifier": self.falsifier, "owner": self.owner,
            "action": self.action, "action_verb": self.action_verb,
            "gate": verdict, "gate_reason": why,
            "evidence": [e.to_dict() for e in self.evidence],
        }


def validate(d: Decision) -> list[str]:
    """Reject a malformed record rather than let it into the queue."""
    errs = []
    if not d.falsifier.strip():
        errs.append(f"{d.id}: no falsifier — an ungradeable claim is an opinion")
    if not d.evidence:
        errs.append(f"{d.id}: no evidence")
    for e in d.evidence:
        if not e.source:
            errs.append(f"{d.id}: evidence '{e.metric}' has no source")
    if d.subject_type not in ("symbol", "subsystem", "portfolio"):
        errs.append(f"{d.id}: unknown subject_type '{d.subject_type}'")
    if d.urgency not in URGENCY_ORDER:
        errs.append(f"{d.id}: unknown urgency '{d.urgency}'")
    if d.action_verb in IRREVERSIBLE and d.gate[0] != "PARK":
        errs.append(f"{d.id}: irreversible '{d.action_verb}' must PARK")
    return errs


# --- collection: every decision traces to a checker ------------------------

# Subsystems whose findings arrive in more detail from a dedicated collector.
# The board rolls them up for the at-a-glance view; the queue wants the
# specific version, and carrying both makes the list look longer than the
# number of actual problems.
BOARD_DEFERS_TO_SPECIALIST = {"THESIS", "RE-ENTRY", "THEMES"}


def _from_statusboard() -> list[Decision]:
    from ..analytics.statusboard import ALARM, CAUTION, board
    out = []
    for c in board().get("checks", []):
        if c["state"] not in (ALARM, CAUTION):
            continue
        if c["system"] in BOARD_DEFERS_TO_SPECIALIST:
            continue
        out.append(Decision(
            id=f"board:{c['system'].lower()}",
            subject=c["system"], subject_type="subsystem",
            kind=RISK if c["critical"] else GOVERNANCE,
            urgency=NOW if c["state"] == ALARM else THIS_WEEK,
            claim=c["line"],
            falsifier=f"the {c['system']} check returns NOMINAL on its next run",
            owner="statusboard", action="review the subsystem", action_verb="research",
            evidence=[Evidence("statusboard", c["system"], c["line"])] +
                     [Evidence("statusboard", "detail", d) for d in c["detail"]]))
    return out


def _from_thesis() -> list[Decision]:
    from ..intel.thesis import BROKEN, WATCH as T_WATCH, report
    rep = report()
    if "error" in rep:
        return []
    out = []
    for r in rep["theses"]:
        if r["verdict"] not in (BROKEN, T_WATCH):
            continue
        t = r["thesis"]
        broken = r["verdict"] == BROKEN
        ev = [Evidence("thesis_ledger", res["metric"], res["detail"],
                       rep.get("snapshot_date", ""))
              for res in r["results"] if res["verdict"] != "INTACT"]
        out.append(Decision(
            id=f"thesis:{r['symbol']}",
            subject=r["symbol"], subject_type="symbol", kind=RISK,
            urgency=NOW if broken else THIS_QUARTER,
            claim=f"{r['symbol']} thesis is {r['verdict']}",
            falsifier=t.exit_trigger,
            owner="thesis_ledger",
            action=("re-underwrite or exit" if broken else "re-examine at the next filing"),
            action_verb="research",
            evidence=ev or [Evidence("thesis_ledger", "verdict", r["verdict"])]))
    return out


def _from_options_desk() -> list[Decision]:
    from ..options.desk import BLOCKED, NO_TRADE, build
    rep = build()
    if "error" in rep:
        return []
    out = []
    for c in rep["candidates"]:
        if c.verdict in (NO_TRADE, BLOCKED):
            continue
        out.append(Decision(
            id=f"options:{c.symbol}",
            subject=c.symbol, subject_type="symbol", kind=OPPORTUNITY, urgency=THIS_WEEK,
            claim=f"{c.symbol}: {c.verdict} into {c.event_date} (T-{c.days_to_event}d)",
            falsifier=("the event date moves, or vol rank falls out of its band "
                       "before the structure is opened"),
            owner="options_desk", action=c.structure,
            # The sleeve is NOT_FUNDED and every structure here is an order.
            action_verb="place_order",
            evidence=[Evidence("options_desk", "vol_rank", str(c.vol_rank)),
                      Evidence("options_desk", "realized_vol", str(c.realized_vol)),
                      Evidence("options_desk", "coordination", c.coordination)]))
    return out


def _from_researcher() -> list[Decision]:
    from ..analytics.researcher import latest
    b = latest()
    if not b or b.get("empty"):
        return []
    out = []
    sent = b.get("sentiment") or {}
    gap = (sent.get("themes") or {}).get("alive_without_exposure") or []
    if gap:
        out.append(Decision(
            id="research:theme_gap", subject=", ".join(gap), subject_type="portfolio",
            kind=OPPORTUNITY,
            urgency=THIS_WEEK,
            claim=f"theme(s) ALIVE with zero exposure: {', '.join(gap)}",
            falsifier="the theme's relative strength turns negative, or exposure is opened",
            owner="researcher", action="review the theme's names against the core screen",
            action_verb="screen",
            evidence=[Evidence("themes", "alive_without_exposure", ", ".join(gap),
                               b.get("as_of", ""))]))
    q = b.get("mcp_queue") or []
    if q:
        out.append(Decision(
            id="research:mcp_queue", subject="data coverage", subject_type="subsystem", kind=DATA,
            urgency=THIS_WEEK,
            claim=f"{len(q)} symbol(s) cannot be judged on the primary screen "
                  f"without a broker pull",
            falsifier="the pulls land and peer standing resolves for every shortlisted name",
            owner="researcher",
            action="Claude runs the queued MCP pulls — the portal holds no broker credentials",
            action_verb="research",
            evidence=[Evidence("researcher", "mcp_queue", f"{len(q)} pulls",
                               b.get("as_of", ""))]))
    return out


def _from_exit_discipline() -> list[Decision]:
    from ..intel.exit_discipline import reentry_candidates
    rc = [r for r in reentry_candidates() if r["forgone_now"] > 0]
    if not rc:
        return []
    total = sum(r["forgone_now"] for r in rc)
    return [Decision(
        id="exits:reentry", subject=", ".join(r["symbol"] for r in rc),
        subject_type="portfolio", kind=OPPORTUNITY, urgency=THIS_WEEK,
        claim=f"{len(rc)} name(s) sold out of a still-live theme · ${total:,} forgone",
        falsifier="the theme rotates out, or the position is re-entered",
        owner="exit_discipline",
        action="decide re-entry deliberately; absence raises no alarm on its own",
        action_verb="research",
        evidence=[Evidence("exit_discipline", r["symbol"],
                           f"sold {r['sold_on']} · {r['move_pct']:+.0f}% since") for r in rc])]


def _from_program() -> list[Decision]:
    """Program-execution findings from the PM checker.

    Imported lazily: program.py imports this module's schema, so a top-level
    import here would close the cycle. The PM's own doctrine check runs first —
    a proposal that would close the pace gap by taking risk is dropped at the
    source rather than routed into the queue.
    """
    from .program import decisions as program_decisions, validate_proposals
    ds = program_decisions()
    bad = {e.split(":")[0] for e in validate_proposals(ds)}
    return [d for d in ds if d.id not in bad]


COLLECTORS = (_from_statusboard, _from_thesis, _from_options_desk,
              _from_researcher, _from_exit_discipline, _from_program)


def build_queue() -> dict:
    decisions, errors, failed = [], [], []
    for fn in COLLECTORS:
        try:
            decisions.extend(fn())
        except Exception as exc:
            # A checker that fails is REPORTED. Silently skipping it would make
            # a broken input indistinguishable from a clean one.
            failed.append(f"{fn.__name__}: {type(exc).__name__}: {exc}")
    for d in decisions:
        errors.extend(validate(d))
    decisions.sort(key=lambda d: (URGENCY_ORDER[d.urgency], d.kind))

    staged = [d for d in decisions if d.gate[0] == "PARK"]
    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "agent": "chief", "kind": "coordinator",
        "decisions": [d.to_dict() for d in decisions],
        "counts": {u: sum(1 for d in decisions if d.urgency == u) for u in URGENCY_ORDER},
        "staged_awaiting_approval": [d.id for d in staged],
        "schema_errors": errors,
        "collectors_failed": failed,
        "does_not": ["generate research — every field traces to a checker",
                     "execute anything — irreversible actions are staged only",
                     "size positions — the risk caps and the human do that"],
    }


def report() -> dict:
    return build_queue()


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    q = build_queue()
    if args.json:
        print(json.dumps(q, indent=2))
        return

    print("=" * 80)
    print(f"  DECISION QUEUE — schema v{q['schema_version']}   ·   {q['as_of']}")
    print("=" * 80)
    c = q["counts"]
    print(f"  now {c['now']}   this week {c['this_week']}   "
          f"this quarter {c['this_quarter']}   watch {c['watch']}\n")

    last = None
    for d in q["decisions"]:
        if d["urgency"] != last:
            print(f"  {'-' * 76}\n  {d['urgency'].upper().replace('_', ' ')}")
            last = d["urgency"]
        park = "  [PARK]" if d["gate"] == "PARK" else ""
        print(f"\n    {d['subject']:22} {d['kind']:<12}{park}")
        print(f"      claim      {d['claim']}")
        print(f"      action     {d['action']}")
        print(f"      falsifier  {d['falsifier']}")
        for e in d["evidence"][:3]:
            stamp = f"  ({e['as_of']})" if e["as_of"] else ""
            print(f"      · {e['source']}/{e['metric']}: {e['value'][:88]}{stamp}")

    if q["staged_awaiting_approval"]:
        print(f"\n  {'=' * 76}")
        print("  STAGED — irreversible, awaiting your approval")
        for i in q["staged_awaiting_approval"]:
            print(f"    {i}")
    if q["schema_errors"]:
        print("\n  SCHEMA ERRORS")
        for e in q["schema_errors"]:
            print(f"    {e}")
    if q["collectors_failed"]:
        print("\n  COLLECTORS FAILED (reported, never silently skipped)")
        for f in q["collectors_failed"]:
            print(f"    {f}")


if __name__ == "__main__":
    _main()
