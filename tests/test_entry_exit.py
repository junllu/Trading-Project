"""The trade simulator must be conservative where daily bars are ambiguous."""
from app.backtest.entry_exit_study import simulate
from app.data.ohlc import Bar


def _flat(n, px=100.0, rng=2.0):
    """n quiet bars: ATR = rng, so stops/targets sit at known distances."""
    return [Bar(f"d{i:04d}", px, px + rng / 2, px - rng / 2, px) for i in range(n)]


def test_time_exit_holds_exactly_five_sessions():
    bars = _flat(40)
    bars[25] = Bar("d0025", 100, 101, 99, 110)          # close 4 sessions after entry
    gross, hold, j = simulate(bars, 20, "X1")           # signal 20 -> entry 21 -> exit 25
    assert hold == 5 and j == 25 and round(gross, 6) == 10.0


def test_bracket_stop_gapping_through_fills_at_the_open():
    bars = _flat(40)
    bars[23] = Bar("d0023", 90, 91, 89, 90)             # opens below the 96 stop
    gross, hold, _ = simulate(bars, 20, "X3")
    assert round(gross, 6) == -10.0 and hold == 3       # filled 90, not 96


def test_bracket_bar_touching_both_assumes_the_stop():
    bars = _flat(40)
    bars[22] = Bar("d0022", 100, 110, 90, 100)          # touches +3ATR and -2ATR
    gross, _, _ = simulate(bars, 20, "X3")
    assert round(gross, 6) == -4.0                      # stop at 96


def test_trailing_stop_ratchets_up_and_never_down():
    bars = _flat(60)
    for i in range(22, 30):                             # rally to 120
        p = 100 + (i - 21) * 2.5
        bars[i] = Bar(f"d{i:04d}", p, p + 1, p - 1, p)
    bars[30] = Bar("d0030", 110, 110, 100, 105)         # drop through 120 - 6 = 114
    gross, _, j = simulate(bars, 20, "X4")
    assert j == 30 and round(gross, 6) == 10.0          # filled at the 110 open (gap below 114)


def test_trend_exit_fills_next_open_not_the_breaking_close():
    bars = _flat(60)
    bars[30] = Bar("d0030", 100, 100, 90, 95)           # close 95 < prior 20d low 99
    bars[31] = Bar("d0031", 93, 94, 92, 93)
    gross, _, j = simulate(bars, 25, "X5")
    assert j == 31 and round(gross, 6) == -7.0
