from datetime import date

import pandas as pd
from data_ngin.domain.contract_definition import (
    extract_definitions_from_df,
    get_cme_expiration_fallback,
)


def test_extract_definitions_from_df():
    data = {
        "instrument_id": [12345, 67890],
        "raw_symbol": ["ESH6", "CLZ0"],
        "expiration": [
            pd.Timestamp("2026-03-20 13:30:00+0000", tz="UTC"),
            pd.Timestamp("2020-11-20 19:30:00+0000", tz="UTC"),
        ],
    }
    df = pd.DataFrame(data)
    mapping = extract_definitions_from_df(df)

    assert "12345" in mapping
    assert mapping["12345"] == date(2026, 3, 20)
    assert "67890" in mapping
    assert mapping["67890"] == date(2020, 11, 20)


def test_get_cme_expiration_fallback_equities():
    # ES 3rd Friday: ESH6 in 2026 -> March 20, 2026
    exp = get_cme_expiration_fallback("ES", "H", 2026)
    assert exp == date(2026, 3, 20)

    # NQ 3rd Friday: NQU0 in 2020 -> September 18, 2020
    exp = get_cme_expiration_fallback("NQ", "U", 2020)
    assert exp == date(2020, 9, 18)


def test_get_cme_expiration_fallback_general():
    # General monthly contract defaults to 3rd Friday or approx delivery
    exp = get_cme_expiration_fallback("CL", "Z", 2020)
    assert exp.year == 2020
    assert exp.month in (11, 12)
