from app.macro import MacroEngine, sector_of


def test_sector_mapping():
    assert sector_of("MRVL") == "semiconductors"
    assert sector_of("ET") == "oil_gas"
    assert sector_of("MP") == "rare_earth"
    assert sector_of("TLRY") == "cannabis"
    assert sector_of("ZZZZ") == "broad"


def test_trump2_favors_oil_and_rare_earth():
    v = MacroEngine().view("2026-06-01")
    assert any("Trump 2nd term" in r for r in v.regimes)
    assert v.sector_bias.get("rare_earth", 0) > 0.3
    assert v.sector_bias.get("oil_gas", 0) > 0
    assert v.sector_bias.get("clean_energy", 0) < 0     # IRA rollback headwind


def test_cannabis_exit_signal_2022():
    # During the 2021 peak->decline window, cannabis bias is negative (the exit).
    v = MacroEngine().view("2022-06-01")
    assert v.sector_bias.get("cannabis", 0) < 0


def test_ira_boosts_clean_energy_2023():
    v = MacroEngine().view("2023-06-01")
    assert v.sector_bias.get("clean_energy", 0) > 0
    assert any("IRA" in r or "CHIPS" in r for r in v.regimes)


def test_symbol_bias_uses_sector():
    eng = MacroEngine()
    # under Trump 2, ET (oil) should read positive, BE (clean energy) negative
    assert eng.symbol_bias("ET", "2026-06-01") > 0
    assert eng.symbol_bias("BE", "2026-06-01") < 0


def test_synthetic_date_returns_no_macro():
    v = MacroEngine().view("D0042")     # backtest synthetic date -> no crash, empty
    assert v.regimes == [] and v.sector_bias == {}
