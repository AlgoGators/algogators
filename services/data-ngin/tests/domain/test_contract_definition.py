from datetime import date

import pandas as pd
from data_ngin.domain.contract_definition import (
    contract_expiration_spans,
    extract_definitions_from_df,
    lookup_expiration,
)


def _defs(rows):
    """rows: (instrument_id, raw_symbol, instrument_class, expiration, ts_recv)"""
    return pd.DataFrame(
        rows, columns=["instrument_id", "raw_symbol", "instrument_class", "expiration", "ts_recv"]
    ).assign(
        expiration=lambda f: pd.to_datetime(f.expiration, utc=True),
        ts_recv=lambda f: pd.to_datetime(f.ts_recv, utc=True),
    )


def test_extract_definitions_from_df():
    df = _defs(
        [
            (12345, "ESH6", "F", "2026-03-20 13:30", "2025-06-01"),
            (67890, "CLZ0", "F", "2020-11-20 19:30", "2019-11-01"),
        ]
    )
    index = extract_definitions_from_df(df)

    assert lookup_expiration(index, "12345", "ESH6", date(2026, 1, 5)) == date(2026, 3, 20)
    assert lookup_expiration(index, "67890", "CLZ0", date(2020, 6, 1)) == date(2020, 11, 20)


def test_reused_instrument_id_is_matched_with_its_symbol():
    # The vendor reused id 2017 for 6BX0 (2020) and GCQ4 (2024), and the GC archive is read
    # after the currency archive: keyed on the id alone, the 6B bars took GCQ4's date.
    index = extract_definitions_from_df(
        _defs([(2017, "6BX0", "F", "2020-11-16 14:16", "2020-05-01")])
    )
    extract_definitions_from_df(
        _defs([(2017, "GCQ4", "F", "2024-08-28 17:30", "2024-01-02")]), index
    )

    assert lookup_expiration(index, "2017", "6BX0", date(2020, 9, 1)) == date(2020, 11, 16)
    assert lookup_expiration(index, "2017", "GCQ4", date(2024, 5, 1)) == date(2024, 8, 28)


def test_symbol_repeating_a_decade_apart_resolves_by_bar_date():
    index = extract_definitions_from_df(
        _defs(
            [
                (100, "GCZ6", "F", "2016-12-28 17:30", "2016-01-04"),
                (100, "GCZ6", "F", "2026-12-29 17:30", "2026-01-05"),
            ]
        )
    )
    assert lookup_expiration(index, "100", "GCZ6", date(2016, 6, 1)) == date(2016, 12, 28)
    assert lookup_expiration(index, "100", "GCZ6", date(2026, 6, 1)) == date(2026, 12, 29)


def test_spreads_and_unknown_contracts_give_no_expiration():
    index = extract_definitions_from_df(
        _defs([(5, "GCZ6-GCG7", "S", "2026-12-29 17:30", "2026-01-05")])
    )
    assert index == {}
    # no definition: None, never a calendar-rule guess
    assert lookup_expiration(index, "5", "GCZ6", date(2026, 6, 1)) is None


def test_contract_gets_one_expiration_when_the_exchange_moved_it():
    # 6EM3: first published expiring 2023-06-19, re-published from 2021-12-19 expiring 2023-06-16.
    index = extract_definitions_from_df(
        _defs(
            [
                (11985, "6EM3", "F", "2023-06-19 14:16", "2018-06-04"),
                (11985, "6EM3", "F", "2023-06-19 14:16", "2021-12-17"),
                (11985, "6EM3", "F", "2023-06-16 14:16", "2021-12-19"),
                (11985, "6EM3", "F", "2023-06-16 14:16", "2023-06-16"),
            ]
        )
    )
    rows = [("11985", "6EM3", date(2021, 11, 17)), ("11985", "6EM3", date(2023, 6, 16))]
    assert contract_expiration_spans(rows, index) == [
        ("11985", "6EM3", date(2021, 11, 17), date(2023, 6, 16), date(2023, 6, 16))
    ]


def test_spans_split_two_contracts_sharing_id_and_symbol():
    index = extract_definitions_from_df(
        _defs(
            [
                (100, "GCZ6", "F", "2016-12-28 17:30", "2016-01-04"),
                (100, "GCZ6", "F", "2026-12-29 17:30", "2026-01-05"),
            ]
        )
    )
    rows = [("100", "GCZ6", date(2016, 11, 1)), ("100", "GCZ6", date(2026, 10, 1))]
    assert contract_expiration_spans(rows, index) == [
        ("100", "GCZ6", date(2016, 11, 1), date(2016, 11, 1), date(2016, 12, 28)),
        ("100", "GCZ6", date(2026, 10, 1), date(2026, 10, 1), date(2026, 12, 29)),
    ]


def test_one_id_carrying_two_live_contracts_is_split_by_symbol():
    # Her defect's shape: id 42003082 carried RTYU5 (expiring 2025-09-19) and MYMZ6
    # (2026-12-18) at overlapping times. Keyed on the id alone, a MYMZ6 bar of 2025-06-02
    # takes RTYU5's 2025-09-19 (the date fit and the nearest month cannot tell them apart).
    index = extract_definitions_from_df(
        _defs(
            [
                (42003082, "RTYU5", "F", "2025-09-19 13:30", "2025-03-03"),
                (42003082, "MYMZ6", "F", "2026-12-18 14:30", "2025-03-03"),
            ]
        )
    )
    assert lookup_expiration(index, "42003082", "MYMZ6", date(2025, 6, 2)) == date(2026, 12, 18)
    assert lookup_expiration(index, "42003082", "RTYU5", date(2025, 6, 2)) == date(2025, 9, 19)


def test_no_calendar_guess_is_left_to_call():
    from data_ngin.domain import contract_definition

    assert not hasattr(contract_definition, "get_cme_expiration_fallback")
    assert not hasattr(contract_definition, "resolve_contract_expiration")
