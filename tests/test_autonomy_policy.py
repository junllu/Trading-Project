"""Autonomy trade policy from sleeve gates."""
from app.analytics.performance import trade_policy_from_sleeve


def test_no_data_observe():
    p = trade_policy_from_sleeve({"gate": "NO DATA", "recommended_sleeve_usd": 0})
    assert p["level"] == "OBSERVE"
    assert p["may_paper_execute"] is False
    assert p["may_live_execute"] is False
    assert p["may_apply_code_changes"] is False


def test_measured_paper_only():
    p = trade_policy_from_sleeve({
        "gate": "MEASURED", "recommended_sleeve_usd": 0, "edge_vs_hold_pp": 0.1})
    assert p["level"] == "PAPER_AUTO"
    assert p["may_paper_execute"] is True
    assert p["may_live_execute"] is False


def test_evidenced_confirm_not_live_auto():
    p = trade_policy_from_sleeve({
        "gate": "EVIDENCED", "recommended_sleeve_usd": 1000, "edge_vs_hold_pp": 0.2})
    assert p["level"] == "CONFIRM_LIVE"
    assert p["may_live_execute"] is False
    assert p["max_live_usd"] == 1000


def test_established_can_live_within_cap():
    p = trade_policy_from_sleeve({
        "gate": "ESTABLISHED", "recommended_sleeve_usd": 5000, "edge_vs_hold_pp": 0.3})
    assert p["level"] == "SLEEVE_AUTO"
    assert p["may_live_execute"] is True
    assert p["max_live_usd"] == 5000
    assert p["may_apply_code_changes"] is False


def test_negative_edge_stays_conservative():
    p = trade_policy_from_sleeve({
        "gate": "EVIDENCED", "recommended_sleeve_usd": 0, "edge_vs_hold_pp": -1.0})
    assert p["may_live_execute"] is False
