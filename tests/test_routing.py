"""Routing must refuse, not default.

The failure this guards is silent downgrade: a verdict question reaching a
language model and coming back as a confident narrative. This project has
measured that cost twice — 3,208 candlestick looks whose best directional
pattern lost to selection noise, and a conviction engine that beat buy-and-hold
in 7 of 34 folds while reading as insightful throughout.
"""
import pytest

from app.intel import routing as r


def test_invariants_hold():
    assert r.validate() == []


@pytest.mark.parametrize("task", [
    "strategy_verdict", "pattern_significance", "forecast_calibration",
    "risk_approval", "exit_rule_evaluation", "position_sizing",
    "autonomy_promotion",
])
def test_verdict_tasks_refuse_every_model(task):
    """Not 'prefers statistics'. Refuses, loudly, with what does decide it."""
    with pytest.raises(r.RoutingError) as exc:
        r.route(task)
    assert "DETERMINISTIC" in str(exc.value)
    assert r.BY_NAME[task].decided_by in str(exc.value)
    assert not r.may_use_model(task)


def test_an_undeclared_task_raises_rather_than_guessing():
    """A call site that never declared its tier must not be routed by inference."""
    with pytest.raises(r.RoutingError) as exc:
        r.route("some_new_thing_nobody_declared")
    assert "declare it" in str(exc.value)


def test_hard_judgement_goes_to_claude():
    for task in ("research_synthesis", "anomaly_triage", "method_variant_proposal"):
        assert r.route(task) == r.CLAUDE
        assert r.provider_for(task) == "anthropic"


def test_bounded_mechanical_work_goes_local():
    for task in ("report_summarization", "decision_explanation", "agent_chat"):
        assert r.route(task) == r.LOCAL
        assert r.provider_for(task) == "ollama"


def test_proposing_a_variant_and_judging_it_are_different_tiers():
    """The whole self-improvement loop hinges on this split: a model may suggest
    what to try, and may never rule on whether it worked."""
    assert r.route("method_variant_proposal") == r.CLAUDE
    with pytest.raises(r.RoutingError):
        r.route("strategy_verdict")


def test_every_task_states_why():
    for t in r.TASKS:
        assert t.why.strip(), f"{t.name} has no stated reason for its tier"
