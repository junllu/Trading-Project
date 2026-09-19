"""The four agents added to own modules that had no owner."""
import pytest

from app.agent.roster import BY_NAME, CHECKER, MAKER, ROSTER, validate


NEW = ("intraday_bars", "macro_series", "exit_auditor", "doctrine_tracker")


def test_graph_still_holds_with_the_new_agents():
    assert validate() == []


@pytest.mark.parametrize("name", NEW)
def test_each_new_agent_is_registered_and_invokable(name):
    a = BY_NAME[name]
    assert a.module and a.entrypoint, f"{name} is named but cannot be invoked"
    import importlib
    fn = getattr(importlib.import_module(a.module), a.entrypoint, None)
    assert callable(fn), f"{a.module}.{a.entrypoint} is not callable"


@pytest.mark.parametrize("name", NEW)
def test_each_new_agent_states_why_its_cadence(name):
    assert BY_NAME[name].why_that_cadence.strip()


def test_doctrine_tracker_consumes_a_maker_rather_than_supplying_itself():
    """The reason macro_series exists: doctrine.py used to fetch the series it
    then graded, which is a checker supplying its own evidence — the same shape
    as thesis_ledger passing ANET on its own preferred margin."""
    dt = BY_NAME["doctrine_tracker"]
    assert dt.kind == CHECKER
    upstream = [BY_NAME[c] for c in dt.consumes if c in BY_NAME]
    assert any(u.kind == MAKER for u in upstream)
    assert "macro_series" in dt.consumes
    assert BY_NAME["macro_series"].kind == MAKER


def test_exit_auditor_grades_policy_but_never_sets_it():
    a = BY_NAME["exit_auditor"]
    assert a.kind == CHECKER
    assert a.stages == []          # checkers may not stage irreversible acts
    from app.analytics.intraday import report
    r = report()
    # It must not emit a threshold of its own — exit_plans.py owns that.
    assert "recommended_stop_pct" not in r
    assert r["policy"]["max_loss_pct"] == 15.0


def test_intraday_bars_declares_that_it_cannot_refresh_itself():
    """An agent that silently stops fetching looks identical to one with
    nothing to do. This dependency on a human is stated in the payload."""
    from app.data.minute import report
    r = report()
    assert r["self_refreshing"] is False
    assert "entitlement" in r["blocked_by"]


def test_no_new_maker_stages_anything():
    for n in NEW:
        a = BY_NAME[n]
        if a.kind == MAKER:
            assert a.stages == []


def test_roster_still_has_exactly_one_coordinator():
    assert len([a for a in ROSTER if a.kind == "coordinator"]) == 1
