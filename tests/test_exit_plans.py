"""Exit plan schema for core vs tactical plays."""
from __future__ import annotations

from app.analytics.exit_plans import (
    CORE, TACTICAL, build_exit_plan, classify_pool, reentry_to_action,
)


def test_focus_is_core():
    assert classify_pool("MRVL", focus={"MRVL", "NVDA"}, sleeve_usd=1000) == CORE


def test_non_focus_with_sleeve_is_tactical():
    assert classify_pool("HOOD", focus={"MRVL"}, sleeve_usd=1000) == TACTICAL


def test_core_buy_has_invalidation_no_time_stop():
    p = build_exit_plan("MRVL", "buy", pool=CORE, theme_state="ALIVE")
    assert p["kind"] == "open_or_add"
    assert "theme" in p["invalidation"].lower() or "thesis" in p["invalidation"].lower()
    assert p["time_stop_sessions"] is None
    assert p["max_loss_pct"] is None
    assert p["hard_halt"]


def test_tactical_buy_has_time_stop_and_max_loss():
    p = build_exit_plan("HOOD", "buy", pool=TACTICAL)
    assert p["time_stop_sessions"] == 21
    assert p["max_loss_pct"] == 15.0


def test_sell_plan_is_exit_now():
    p = build_exit_plan("MRVL", "sell", pool=CORE)
    assert p["kind"] == "exit_now"
    assert "reentry" in p["reentry_if"].lower()


def test_reentry_action_is_review_only():
    row = {"symbol": "PLTR", "theme": "ai_software", "sold_on": "2024-01-01",
           "sold_at": 20.0, "now": 40.0, "move_pct": 100.0, "forgone_now": 1000}
    a = reentry_to_action(row, focus={"MRVL"})
    assert a["auto_order"] is False
    assert a["type"] == "reentry_review"
    assert a["exit_plan"]["pool"] == TACTICAL


def test_staircase_scale_out_always_on_core_buy():
    p = build_exit_plan("MRVL", "buy", pool=CORE, theme_state="ALIVE",
                        book_drawdown_pct=0.02)
    assert p["staircase"]["scale_out"]["enabled"] is True
    assert p["staircase"]["scale_in"]["enabled"] is True


def test_staircase_blocks_scale_in_on_dead_theme():
    p = build_exit_plan("WEED", "buy", pool=CORE, theme_state="DEAD",
                        book_drawdown_pct=0.0)
    assert p["staircase"]["scale_in"]["enabled"] is False


def test_staircase_blocks_scale_in_when_book_deep_red_and_no_edge():
    p = build_exit_plan("MRVL", "buy", pool=CORE, theme_state="ALIVE",
                        book_drawdown_pct=0.25, sleeve_edge_pp=-0.5)
    assert p["staircase"]["scale_in"]["enabled"] is False


def test_portfolio_ok_to_add_helpers():
    from app.analytics.exit_plans import portfolio_ok_to_add
    ok, _ = portfolio_ok_to_add(book_drawdown_pct=0.05)
    assert ok
    bad, _ = portfolio_ok_to_add(book_drawdown_pct=0.30, sleeve_edge_pp=-1.0)
    assert not bad
