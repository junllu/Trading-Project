from app.portfolio.trade_history import analyze, _fifo_round_trips


SAMPLE = [
    # Cannabis: bought into the 2021 mania, sold LATE in 2022 (regime already negative)
    {"symbol": "TLRY", "side": "buy", "quantity": 100, "price": 50.0, "date": "2021-02-10"},
    {"symbol": "TLRY", "side": "sell", "quantity": 100, "price": 20.0, "date": "2022-08-01"},
    # A winning semi round-trip
    {"symbol": "NVDA", "side": "buy", "quantity": 10, "price": 100.0, "date": "2023-01-05"},
    {"symbol": "NVDA", "side": "sell", "quantity": 10, "price": 300.0, "date": "2024-01-05"},
    # Still-open position
    {"symbol": "MRVL", "side": "buy", "quantity": 50, "price": 60.0, "date": "2023-06-01"},
]


def test_fifo_matches_and_open():
    trips, open_qty = _fifo_round_trips(SAMPLE)
    assert len(trips) == 2                       # TLRY + NVDA closed
    assert open_qty["MRVL"] == 50                # still open


def test_realized_pnl_and_win_rate():
    a = analyze(SAMPLE)
    assert a["trades_matched"] == 2
    # TLRY: (20-50)*100 = -3000 ; NVDA: (300-100)*10 = +2000 ; net -1000
    assert a["realized_pnl"] == -1000.0
    assert a["win_rate_pct"] == 50.0
    assert "MRVL" in a["still_open"]


def test_late_exit_flag_on_cannabis():
    a = analyze(SAMPLE)
    syms = [le["symbol"] for le in a["late_exits"]]
    assert "TLRY" in syms                         # sold into a negative cannabis regime
    le = next(le for le in a["late_exits"] if le["symbol"] == "TLRY")
    assert le["exit_regime_tilt"] < 0
    assert le["return_pct"] < 0


def test_empty_history():
    a = analyze([])
    assert a["trades_matched"] == 0
    assert a["realized_pnl"] == 0.0


def test_validate_flags_split_and_bad_data():
    from app.portfolio.trade_history import validate
    orders = [
        # AMC-like reverse-split price range (2 -> 90): >8x -> split flag
        {"symbol": "AMC", "side": "buy", "quantity": 100, "price": 2.0, "date": "2021-06-01"},
        {"symbol": "AMC", "side": "sell", "quantity": 100, "price": 90.0, "date": "2023-09-01"},
        # non-positive price
        {"symbol": "XYZ", "side": "buy", "quantity": 10, "price": 0.0, "date": "2022-01-01"},
        # sell with no prior buys
        {"symbol": "ZZZ", "side": "sell", "quantity": 5, "price": 10.0, "date": "2022-01-01"},
    ]
    w = validate(orders)
    issues = " ".join(x["issue"] for x in w)
    assert "STOCK SPLIT" in issues
    assert "non-positive" in issues
    assert "exceeds prior buys" in issues


def test_validate_clean_data_no_flags():
    from app.portfolio.trade_history import validate
    clean = [
        {"symbol": "NVDA", "side": "buy", "quantity": 10, "price": 100.0, "date": "2023-01-05"},
        {"symbol": "NVDA", "side": "sell", "quantity": 10, "price": 130.0, "date": "2023-06-05"},
    ]
    assert validate(clean) == []
