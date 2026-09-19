"""The blotter must outlive the process and must not invent profit.

Two failures are guarded here. First, a paper run whose fills exist only in
`Executor.history` leaves no evidence after a restart — that is why the record
is on disk at all. Second, positions seeded into the paper broker from
holdings.yaml were never bought inside this record; matching their sells
against a zero cost basis would report a fortune that never happened.
"""
import json

import pytest

from app.engine import blotter
from app.models import Order, OrderStatus, Side


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(blotter, "PATH", tmp_path / "blotter.jsonl")


def _order(symbol, side, qty, px, status=OrderStatus.FILLED, strategy="unknown"):
    o = Order(symbol=symbol, side=side, quantity=qty, strategy=strategy)
    o.status = status
    o.filled_price = px
    return o


def test_fills_are_attributed_to_the_sleeve_that_asked_for_them():
    """Mixing the conviction engine's fills with sma_crossover's makes both
    unmeasurable — the whole point of tracking paper performance."""
    blotter.record(_order("MRVL", Side.BUY, 10, 100.0, strategy="daily_agent"), "confirm")
    blotter.record(_order("MRVL", Side.SELL, 10, 110.0, strategy="daily_agent"), "confirm")
    blotter.record(_order("VRT", Side.BUY, 5, 200.0, strategy="sma_crossover"), "confirm")
    blotter.record(_order("VRT", Side.SELL, 5, 190.0, strategy="sma_crossover"), "confirm")

    bs = blotter.by_strategy()
    assert bs["daily_agent"]["attributed"]["realized_usd"] == pytest.approx(100.0)
    assert bs["sma_crossover"]["attributed"]["realized_usd"] == pytest.approx(-50.0)
    assert bs["daily_agent"]["fills"] == 2


def test_a_sleeve_does_not_close_another_sleeves_lot_in_attribution():
    """Attribution matches each sleeve against its OWN buys. A sell with no
    buy in that sleeve is unmatched, not a windfall."""
    blotter.record(_order("MU", Side.BUY, 10, 50.0, strategy="daily_agent"), "confirm")
    blotter.record(_order("MU", Side.SELL, 10, 80.0, strategy="sma_crossover"), "confirm")

    bs = blotter.by_strategy()
    assert bs["sma_crossover"]["attributed"]["realized_usd"] == 0.0
    assert bs["sma_crossover"]["attributed"]["unmatched_sells"]["MU"] == pytest.approx(10.0)
    # Book level still sees a real closed round-trip across the two sleeves.
    assert blotter.realized()["realized_usd"] == pytest.approx(300.0)


def test_record_survives_as_a_file_and_reads_back():
    blotter.record(_order("MRVL", Side.BUY, 2, 100.0), "confirm")
    assert blotter.PATH.exists()
    rows = blotter.rows()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "MRVL"
    assert rows[0]["side"] == "buy"
    assert rows[0]["mode"] == "confirm"


def test_rejections_are_kept_not_silently_dropped():
    blotter.record(_order("NVDA", Side.BUY, 1, None, OrderStatus.REJECTED), "confirm")
    blotter.record(_order("NVDA", Side.BUY, 1, 50.0), "confirm")
    s = blotter.summary()
    assert s["orders_recorded"] == 2
    assert s["fills"] == 1
    assert s["by_status"]["rejected"] == 1


def test_realized_pnl_matches_fifo_over_closed_round_trips():
    blotter.record(_order("TSLA", Side.BUY, 10, 100.0), "confirm")
    blotter.record(_order("TSLA", Side.BUY, 10, 120.0), "confirm")
    blotter.record(_order("TSLA", Side.SELL, 10, 130.0), "confirm")   # vs the 100 lot
    r = blotter.realized()
    assert r["realized_usd"] == pytest.approx(300.0)
    assert r["round_trips"] == 1
    assert r["open_lots"]["TSLA"] == pytest.approx(10.0)


def test_sell_without_a_recorded_buy_is_unmatched_not_profit():
    """A seeded holding sold here has no cost basis in this record."""
    blotter.record(_order("CCXI", Side.SELL, 70, 12.0), "confirm")
    r = blotter.realized()
    assert r["realized_usd"] == 0.0
    assert r["round_trips"] == 0
    assert r["unmatched_sells"]["CCXI"] == pytest.approx(70.0)


def test_a_corrupt_line_does_not_kill_the_reader():
    blotter.record(_order("MU", Side.BUY, 1, 10.0), "confirm")
    with blotter.PATH.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    assert len(blotter.rows()) == 1


def test_write_failure_never_breaks_execution(monkeypatch):
    """Bookkeeping must not turn a successful fill into an exception."""
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(blotter.json, "dumps", boom)
    row = blotter.record(_order("AMD", Side.BUY, 1, 10.0), "confirm")
    assert "_write_error" in row
