"""Exit A/B — and the determinism rule that makes any of it replayable."""
import re
from pathlib import Path

import pytest

from app.backtest import exit_ab


def test_entries_are_non_overlapping():
    """Overlapping entries would report ~250 observations where ~12 exist.
    intraday.stop_study() already corrects for this; the A/B must not
    reintroduce it."""
    from app.data.ohlc import load
    bars = load("TSLA")
    if len(bars) < exit_ab.MIN_BARS:
        pytest.skip("no TSLA history")
    trades = exit_ab._run_arm(bars, "TSLA", scaled=False)
    dates = [t.entry_date for t in trades]
    assert len(dates) == len(set(dates))
    idx = {b.date: i for i, b in enumerate(bars)}
    gaps = [idx[dates[i + 1]] - idx[dates[i]] for i in range(len(dates) - 1)]
    assert all(g >= exit_ab.HOLD_BARS for g in gaps)


def test_volatility_is_measured_strictly_before_the_entry():
    """Look-ahead check: the window must end at i-1, never include bar i."""
    from app.data.ohlc import Bar
    bars = [Bar(f"2020-01-{i%28+1:02d}", 100, 101, 99, 100 + (i % 5), 0)
            for i in range(200)]
    # Poison bar 150 with an extreme close; vol measured AT 150 must not see it.
    poisoned = list(bars)
    poisoned[150] = Bar("2020-01-01", 100, 500, 1, 500, 0)
    assert exit_ab._trailing_vol(bars, 150) == exit_ab._trailing_vol(poisoned, 150)


def test_scaled_stop_respects_its_bounds():
    from app.data.ohlc import load
    from app.backtest.exit_ab import STOP_CEIL, STOP_FLOOR
    for sym in ("TSLA", "NVDA"):
        bars = load(sym)
        if len(bars) < exit_ab.MIN_BARS:
            continue
        for t in exit_ab._run_arm(bars, sym, scaled=True):
            assert STOP_FLOOR <= t.stop_pct <= STOP_CEIL


def test_flat_arm_caps_every_loss_at_the_stop():
    """The property the A/B ultimately turned on: a flat stop is a hard floor."""
    from app.data.ohlc import load
    bars = load("TSLA")
    if len(bars) < exit_ab.MIN_BARS:
        pytest.skip("no TSLA history")
    worst = min(t.ret for t in exit_ab._run_arm(bars, "TSLA", scaled=False))
    assert worst >= -exit_ab.FLAT_STOP_PCT - 1e-9


def test_scale_factor_is_fixed_not_swept():
    """The anchor is declared a priori. If it ever becomes a swept parameter,
    the result must go through trials deflation with the real trial count."""
    assert exit_ab.REF_DAILY_VOL == 0.02
    assert exit_ab.FLAT_STOP_PCT == 0.15


@pytest.mark.parametrize("path", ["app/intel/analyst.py",
                                  "app/intel/local_llm_analyst.py"])
def test_rating_models_run_at_temperature_zero(path):
    """These ratings carry weight in the conviction blend and conviction sets
    position size. A sampled rating makes the order value unreproducible, which
    breaks both forward-record replay and calibration scoring."""
    src = Path(path).read_text(encoding="utf-8")
    temps = re.findall(r'"temperature":\s*([0-9.]+)', src)
    assert temps, f"{path} sets no temperature"
    assert all(float(t) == 0.0 for t in temps), f"{path} samples: {temps}"
