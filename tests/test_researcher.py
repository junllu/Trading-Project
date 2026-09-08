"""Researcher maker — produces a brief, never grades."""
from __future__ import annotations

from app.analytics import researcher
from app.agent.roster import BY_NAME, validate


def test_researcher_on_roster_as_maker():
    a = BY_NAME["researcher"]
    assert a.kind == "maker"
    assert a.entrypoint == "report"
    assert a.module == "app.analytics.researcher"
    assert not a.stages


def test_roster_still_valid_with_researcher():
    errs = validate()
    assert errs == []


def test_report_writes_brief(tmp_path, monkeypatch):
    monkeypatch.setattr(researcher, "OUT_DIR", tmp_path)
    # discovery may be empty in CI — report must still return structure
    brief = researcher.report(limit=3)
    assert brief["agent"] == "researcher"
    assert "discovery" in brief
    assert "does_not" in brief
    assert (tmp_path / "brief_latest.json").exists()
    assert "assign buy/sell" in " ".join(brief["does_not"]).lower() or any(
        "buy/sell" in x for x in brief["does_not"])
