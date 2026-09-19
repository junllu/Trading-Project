"""Test isolation for state that lives on disk.

The blotter is an append-only evidence file. Any test that drives the executor
places orders, and the executor records every one — so without this, running
the suite writes synthetic fills (AAA, BBB, and friends) into
`data/paper_blotter.jsonl` and quietly corrupts the very record the paper runs
exist to produce. Redirect it per-test rather than trusting each test to
remember.
"""
import pytest

from app.engine import blotter


@pytest.fixture(autouse=True)
def _isolate_blotter(tmp_path, monkeypatch):
    monkeypatch.setattr(blotter, "PATH", tmp_path / "paper_blotter.jsonl")


@pytest.fixture(autouse=True)
def _isolate_position_plans(tmp_path, monkeypatch):
    """Same reason as the blotter: entry plans are durable per-symbol state.

    Without this a test that evaluates MRVL picks up whatever stop the real
    book recorded for MRVL, so the test passes or fails on live data rather
    than on its own fixture — and a test run writes plans into the real file.
    """
    from app.agent import position_plans
    monkeypatch.setattr(position_plans, "PATH", tmp_path / "position_plans.json")
