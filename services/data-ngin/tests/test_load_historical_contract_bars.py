import importlib.util
from datetime import date
from pathlib import Path

import pandas as pd
from data_ngin.domain.contract_definition import extract_definitions_from_df

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "load_historical_contract_bars.py"


def _loader():
    spec = importlib.util.spec_from_file_location("load_historical_contract_bars", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _index():
    df = pd.DataFrame(
        {
            "instrument_id": [2017, 2017],
            "raw_symbol": ["6BX0", "GCQ4"],
            "instrument_class": ["F", "F"],
            "expiration": pd.to_datetime(["2020-11-16 14:16", "2024-08-28 17:30"], utc=True),
            "ts_recv": pd.to_datetime(["2020-05-01", "2024-01-02"], utc=True),
        }
    )
    return extract_definitions_from_df(df)


def test_loader_matches_definitions_on_id_and_symbol():
    loader = _loader()
    index = _index()
    assert loader.resolve_expiration("2017", "6BX0", date(2020, 9, 1), index) == date(2020, 11, 16)
    assert loader.resolve_expiration("2017", "GCQ4", date(2024, 5, 1), index) == date(2024, 8, 28)


def test_loader_writes_no_calendar_guess_without_a_definition():
    loader = _loader()
    assert loader.resolve_expiration("999", "ZNZ6", date(2026, 6, 1), _index()) is None
    assert loader.resolve_expiration("999", "ZNZ6", date(2026, 6, 1), None) is None


def test_loader_splits_one_id_carrying_two_live_contracts():
    df = pd.DataFrame(
        {
            "instrument_id": [42003082, 42003082],
            "raw_symbol": ["RTYU5", "MYMZ6"],
            "instrument_class": ["F", "F"],
            "expiration": pd.to_datetime(["2025-09-19 13:30", "2026-12-18 14:30"], utc=True),
            "ts_recv": pd.to_datetime(["2025-03-03", "2025-03-03"], utc=True),
        }
    )
    index = extract_definitions_from_df(df)
    loader = _loader()
    assert loader.resolve_expiration("42003082", "MYMZ6", date(2025, 6, 2), index) == date(
        2026, 12, 18
    )
    assert loader.resolve_expiration("42003082", "RTYU5", date(2025, 6, 2), index) == date(
        2025, 9, 19
    )


def _populate():
    path = SCRIPT.parent / "populate_contract_expirations.py"
    spec = importlib.util.spec_from_file_location("populate_contract_expirations", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_populate_refuses_to_write_null_over_a_stored_expiration():
    populate = _populate()
    rows = [
        ("2017", "GCQ4", date(2024, 5, 1), date(2024, 8, 28)),
        # a contract listed after the archives: the daily pull stored its date
        ("777", "GCZ7", date(2026, 11, 2), date(2027, 12, 29)),
        # no definition and no stored date: nothing to protect
        ("888", "GCG8", date(2026, 11, 2), None),
    ]
    spans = [
        ("2017", "GCQ4", date(2024, 5, 1), date(2024, 5, 1), date(2024, 8, 28)),
        ("777", "GCZ7", date(2026, 11, 2), date(2026, 11, 2), None),
        ("888", "GCG8", date(2026, 11, 2), date(2026, 11, 2), None),
    ]
    assert populate.unresolved_spans_holding_values(rows, spans) == [
        ("777", "GCZ7", date(2026, 11, 2), date(2026, 11, 2))
    ]
