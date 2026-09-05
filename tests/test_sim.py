from app.sim.simulator import SimConfig, Simulator
from app.sim.store import SimStore


def _store(tmp_path):
    return SimStore(path=tmp_path / "history.db")


def test_store_records_and_reads(tmp_path):
    s = _store(tmp_path)
    s.start_run("r1", "simulation", {"a": 1})
    s.record_trade("r1", 3, "MRVL", "buy", 2.0, 100.0, 0.5, "test")
    s.record_event("r1", 3, "policy", "MRVL", {"headline": "x"})
    s.record_equity("r1", 3, 1000.0, 500.0, 500.0, 0.0, "accumulate")
    s.commit()
    assert len(s.trades("r1")) == 1
    assert s.trades("r1")[0]["symbol"] == "MRVL"
    assert len(s.events("r1")) == 1
    assert s.equity_curve("r1")[0]["equity"] == 1000.0


def test_simulation_runs_and_persists(tmp_path):
    cfg = SimConfig(symbols=["MRVL", "NVDA", "TSLA"], focus_symbols=["MRVL", "NVDA", "TSLA"],
                    days=120, starting_cash=50000, seed=3)
    sim = Simulator(cfg, store=_store(tmp_path))
    res = sim.run()
    d = res.to_dict()
    assert d["trades"] > 0
    assert d["start_equity"] == 50000
    # equity curve was stored for every day
    assert len(sim.store.equity_curve(res.run_id)) == 120


def test_deterministic_given_seed(tmp_path):
    def run():
        cfg = SimConfig(symbols=["MRVL", "NVDA"], focus_symbols=["MRVL", "NVDA"],
                        days=80, starting_cash=20000, seed=42)
        return Simulator(cfg, store=_store(tmp_path / str(id(cfg)))).run().to_dict()["final_equity"]
    assert run() == run()


def test_self_correction_records_weight_history(tmp_path):
    cfg = SimConfig(symbols=["MRVL", "NVDA", "TSLA"], focus_symbols=["MRVL", "NVDA", "TSLA"],
                    days=150, starting_cash=50000, seed=5, self_correct=True,
                    correct_every=20, lookback=40)
    sim = Simulator(cfg, store=_store(tmp_path))
    res = sim.run()
    hist = sim.store.weight_history(res.run_id)
    assert len(hist) >= 3                       # corrected multiple times
    # weights stay finite and positive
    for row in hist:
        for k in ("technical", "analyst", "sentiment", "geopolitical"):
            assert row[k] > 0


def test_drawdown_halt_engages(tmp_path):
    # Fully invested in a volatile name with a tight halt: a peak-to-trough dip
    # past the threshold must engage the guardrail (halted days > 0).
    cfg = SimConfig(symbols=["MRVL", "NVDA", "TSLA"], focus_symbols=["MRVL", "NVDA", "TSLA"],
                    days=200, starting_cash=1000, seed=9,
                    drawdown_halt=0.04, starting_positions={"MRVL": (500, 100.0)})
    sim = Simulator(cfg, store=_store(tmp_path))
    res = sim.run()
    assert res.halted_days > 0                   # the guardrail engaged at some point
