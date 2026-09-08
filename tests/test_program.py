"""Program manager — the doctrine guard is the part that matters.

Everything else here is schema hygiene. The tests that earn their place are the
ones proving the PM cannot propose closing a pace gap by taking more risk: that
is the failure mode which would look like diligence in a report and end the
campaign in the account.
"""
import pytest

from app.agent import program
from app.agent.chief import Decision, Evidence, GOVERNANCE, THIS_WEEK, validate


def proposal(claim, action, verb="research", _id="program:test"):
    return Decision(
        id=_id, subject="campaign", subject_type="portfolio", kind=GOVERNANCE,
        urgency=THIS_WEEK, claim=claim, falsifier="something measurable changes",
        owner="program_manager", action=action, action_verb=verb,
        evidence=[Evidence("program_manager", "m", "v")])


@pytest.mark.parametrize("action", [
    "raise the position cap to close the gap",
    "increase leverage to reach the target",
    "loosen the halt so the book can recover",
    "lower the conviction threshold to trade more",
    "concentrate further into the focus names",
])
def test_rejects_every_way_of_taking_more_risk_to_hit_the_target(action):
    errs = program.validate_proposals([proposal("behind plan", action)])
    assert errs, f"doctrine guard let through: {action}"
    assert "guardrail" in errs[0] or "pace gap" in errs[0]


def test_rejects_the_same_intent_written_in_the_claim_rather_than_the_action():
    # The guard reads claim AND action; hiding the intent in the narrative half
    # must not smuggle it past.
    errs = program.validate_proposals(
        [proposal("we should raise the position cap", "proceed")])
    assert errs


def test_allows_ordinary_process_work():
    errs = program.validate_proposals(
        [proposal("an agent is unwired", "wire an entrypoint for calibration_scorer")])
    assert errs == []


def test_rejects_a_proposal_that_would_need_approval():
    # PM work must be reversible and finishable. Anything that parks belongs to
    # the chief, which is the only gate in the graph.
    errs = program.validate_proposals(
        [proposal("a position is oversized", "sell 10 shares", verb="place_order")])
    assert any("parks for approval" in e for e in errs)


def test_emitted_decisions_satisfy_the_chiefs_schema():
    for d in program.decisions():
        assert validate(d) == [], f"{d.id} failed chief schema: {validate(d)}"


def test_live_decisions_never_violate_doctrine():
    assert program.validate_proposals(program.decisions()) == []


def test_pace_reports_the_gap_without_recommending_a_remedy():
    ds = [d for d in program.decisions() if d.id == "program:pace"]
    if not ds:
        pytest.skip("no holdings available in this environment")
    assert "no risk change" in ds[0].action


def test_report_exposes_the_doctrine_and_any_violations():
    r = program.report()
    assert r["proposal_violations"] == []
    assert "outranks the target" in r["doctrine"]


def test_pm_is_a_checker_not_a_second_coordinator():
    from app.agent.roster import BY_NAME, CHECKER, COORDINATOR, ROSTER, validate as rvalidate
    pm = BY_NAME["program_manager"]
    assert pm.kind == CHECKER
    assert pm.stages == []
    assert pm.rewrites_own_rules is False
    assert len([a for a in ROSTER if a.kind == COORDINATOR]) == 1
    assert rvalidate() == []


def test_chief_routes_program_items_into_the_one_queue():
    from app.agent.chief import build_queue
    q = build_queue()
    assert q["schema_errors"] == []
    assert q["collectors_failed"] == []
    assert any(d["id"].startswith("program:") for d in q["decisions"])
