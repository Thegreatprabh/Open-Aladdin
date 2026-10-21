import pytest

from market_data.instruments import InstrumentRegistry, UnknownInstrument


def test_known_index_tokens():
    reg = InstrumentRegistry()
    assert reg.token_for("NIFTY") == 256265
    assert reg.token_for("BANKNIFTY") == 260105
    assert reg.symbol_for(256265) == "NIFTY"


def test_round_trip_all_symbols():
    reg = InstrumentRegistry()
    for sym in reg.symbols():
        assert reg.symbol_for(reg.token_for(sym)) == sym


def test_unknown_symbol_and_token():
    reg = InstrumentRegistry()
    with pytest.raises(UnknownInstrument):
        reg.token_for("DOESNOTEXIST")
    with pytest.raises(UnknownInstrument):
        reg.symbol_for(999999999)


def test_from_dump_rows_skips_partial():
    reg = InstrumentRegistry.from_dump_rows([
        {"tradingsymbol": "AAA", "instrument_token": 111, "last_price": 10.5},
        {"tradingsymbol": "BROKEN"},                      # missing keys
        {"instrument_token": "x", "last_price": "y"},      # bad types, no symbol
        {"tradingsymbol": "BBB", "instrument_token": 222, "last_price": 20},
    ])
    assert reg.token_for("AAA") == 111
    assert reg.token_for("BBB") == 222
    assert reg.symbols() == ["AAA", "BBB"]
