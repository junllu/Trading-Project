from app.analysis import ema, macd, pct_change, rsi, sma


def test_sma_basic():
    values = [1, 2, 3, 4, 5]
    out = sma(values, 3)
    assert out[:2] == [None, None]
    assert out[2] == 2.0   # (1+2+3)/3
    assert out[3] == 3.0
    assert out[4] == 4.0


def test_sma_len_matches_input():
    values = list(range(20))
    assert len(sma(values, 5)) == len(values)


def test_ema_seeds_with_sma():
    values = [float(i) for i in range(1, 11)]
    out = ema(values, 3)
    assert out[1] is None
    assert out[2] == 2.0  # SMA of first 3 = (1+2+3)/3
    assert out[-1] is not None and out[-1] > out[2]


def test_rsi_all_gains_is_high():
    values = [float(i) for i in range(1, 30)]  # monotonically rising
    out = rsi(values, 14)
    assert out[14] is not None
    assert out[-1] > 90  # strong uptrend -> RSI near 100


def test_rsi_bounds():
    values = [100, 101, 99, 102, 98, 103, 97, 104, 96, 105, 95, 106, 94, 107, 93, 108]
    for v in rsi(values, 14):
        if v is not None:
            assert 0 <= v <= 100


def test_macd_shapes():
    values = [float(i % 7) + i * 0.1 for i in range(60)]
    line, signal, hist = macd(values, 12, 26, 9)
    assert len(line) == len(signal) == len(hist) == len(values)
    assert any(x is not None for x in line)
    assert any(x is not None for x in signal)


def test_pct_change():
    out = pct_change([100, 110, 99])
    assert out[0] is None
    assert round(out[1], 2) == 10.0
    assert round(out[2], 2) == -10.0
