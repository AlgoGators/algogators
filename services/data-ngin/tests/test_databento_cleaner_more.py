"""Additional tests for DatabentoCleaner: validation, transformation, and the
back-adjustment gate, all on synthetic pandas frames (no I/O)."""

import logging
from typing import Any

import pandas as pd
import pytest
from data_ngin.infrastructure.cleaner.databento_cleaner import (
    DatabentoCleaner,
    RequiredFields,
)

NO_FILL_CONFIG: dict[str, Any] = {"missing_data": {}}


def make_raw_frame(**overrides: Any) -> pd.DataFrame:
    """A minimal valid Databento-style frame (ts_event column, one symbol)."""
    base: dict[str, Any] = {
        "ts_event": ["2023-01-02", "2023-01-01", "2023-01-03"],
        "symbol": ["MES", "MES", "MES"],
        "open": [101.0, 100.0, 102.0],
        "high": [102.0, 101.0, 103.0],
        "low": [100.0, 99.0, 101.0],
        "close": [101.5, 100.5, 102.5],
        "volume": [1100.0, 1000.0, 1200.0],
    }
    base.update(overrides)
    return pd.DataFrame(base)


def make_cleaner(config: dict[str, Any] | None = None) -> DatabentoCleaner:
    return DatabentoCleaner(config=config if config is not None else dict(NO_FILL_CONFIG))


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


def test_init_defaults_back_adjustment_to_future() -> None:
    cleaner = make_cleaner({})
    assert cleaner.back_adjuster.applies_to == "FUTURE"


def test_init_reads_applies_to_from_config() -> None:
    cleaner = make_cleaner({"back_adjustment": {"applies_to": "EQUITY"}})
    assert cleaner.back_adjuster.applies_to == "EQUITY"


# ---------------------------------------------------------------------------
# clean
# ---------------------------------------------------------------------------


def test_clean_empty_dataframe_raises() -> None:
    cleaner = make_cleaner()
    with pytest.raises(ValueError, match="empty"):
        cleaner.clean(pd.DataFrame())


def test_clean_happy_path_returns_standardized_records() -> None:
    cleaner = make_cleaner()
    raw = make_raw_frame(extra_column=["a", "b", "c"])

    records = cleaner.clean(raw)

    assert isinstance(records, list)
    assert len(records) == 3
    required = {field.value for field in RequiredFields}
    assert set(records[0].keys()) == required

    # Sorted by time, timestamps localized to UTC
    times = [record["time"] for record in records]
    assert times == sorted(times)
    assert times[0] == pd.Timestamp("2023-01-01", tz="UTC")

    # Types standardized
    assert isinstance(records[0]["volume"], int)
    assert isinstance(records[0]["open"], float)
    # Original row for 2023-01-01 had open 100.0; no roll so no adjustment
    assert records[0]["open"] == 100.0


def test_clean_missing_fields_raises_with_field_names() -> None:
    cleaner = make_cleaner()
    raw = pd.DataFrame(
        {
            "ts_event": ["2023-01-01"],
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
        }
    )

    with pytest.raises(ValueError, match="symbol") as excinfo:
        cleaner.clean(raw)
    assert "close" in str(excinfo.value)
    assert "volume" in str(excinfo.value)


# ---------------------------------------------------------------------------
# validate_fields
# ---------------------------------------------------------------------------


def test_validate_fields_renames_ts_event_column() -> None:
    cleaner = make_cleaner()
    raw = make_raw_frame()

    validated = cleaner.validate_fields(raw)

    assert "time" in validated.columns
    assert "ts_event" not in validated.columns


def test_validate_fields_resets_ts_event_index() -> None:
    cleaner = make_cleaner()
    raw = make_raw_frame().set_index("ts_event")
    assert "ts_event" in raw.index.names

    validated = cleaner.validate_fields(raw)

    assert "time" in validated.columns
    assert "ts_event" not in validated.index.names


def test_validate_fields_passes_through_already_named_time() -> None:
    cleaner = make_cleaner()
    raw = make_raw_frame().rename(columns={"ts_event": "time"})

    validated = cleaner.validate_fields(raw)

    assert list(validated.columns) == list(raw.columns)


# ---------------------------------------------------------------------------
# handle_missing_data (delegation to MissingDataFiller)
# ---------------------------------------------------------------------------


def test_handle_missing_data_zero_fill() -> None:
    cleaner = make_cleaner({"missing_data": {"zero_fill": True}})
    data = pd.DataFrame({"close": [1.0, None, 3.0]})

    result = cleaner.handle_missing_data(data)

    assert result["close"].tolist() == [1.0, 0.0, 3.0]


def test_handle_missing_data_no_config_is_noop() -> None:
    cleaner = make_cleaner({})
    data = pd.DataFrame({"close": [1.0, None]})

    result = cleaner.handle_missing_data(data.copy())

    assert pd.isna(result["close"].iloc[1])


# ---------------------------------------------------------------------------
# transform_data
# ---------------------------------------------------------------------------


def test_transform_data_localizes_naive_timestamps_to_utc() -> None:
    cleaner = make_cleaner()
    data = make_raw_frame().rename(columns={"ts_event": "time"})

    result = cleaner.transform_data(data)

    assert isinstance(result["time"].dtype, pd.DatetimeTZDtype)
    assert str(result["time"].dt.tz) == "UTC"


def test_transform_data_converts_tz_aware_timestamps_to_utc() -> None:
    cleaner = make_cleaner()
    data = make_raw_frame().rename(columns={"ts_event": "time"})
    data["time"] = pd.to_datetime(data["time"]).dt.tz_localize("US/Eastern")

    result = cleaner.transform_data(data)

    assert str(result["time"].dt.tz) == "UTC"
    # 2023-01-01 00:00 Eastern == 05:00 UTC
    assert result["time"].iloc[0] == pd.Timestamp("2023-01-01 05:00:00", tz="UTC")


def test_transform_data_drops_extra_columns_and_sorts() -> None:
    cleaner = make_cleaner()
    data = make_raw_frame(rtype=[1, 1, 1], publisher_id=[2, 2, 2]).rename(
        columns={"ts_event": "time"}
    )

    result = cleaner.transform_data(data)

    assert "rtype" not in result.columns
    assert "publisher_id" not in result.columns
    assert result["time"].is_monotonic_increasing
    assert result["volume"].dtype == "int64"
    assert result["open"].dtype == "float64"


def test_transform_data_warns_on_duplicate_timestamps(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cleaner = make_cleaner()
    data = make_raw_frame(ts_event=["2023-01-01", "2023-01-01", "2023-01-02"]).rename(
        columns={"ts_event": "time"}
    )

    with caplog.at_level(logging.WARNING):
        cleaner.transform_data(data)

    assert any("Duplicate timestamps" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# apply_back_adjustment
# ---------------------------------------------------------------------------


def roll_frame() -> pd.DataFrame:
    """Two contracts with a volume-confirmed roll at index 2."""
    return pd.DataFrame(
        {
            "time": pd.to_datetime(["2023-01-01", "2023-01-02", "2023-01-03"]),
            "symbol": ["MESH23", "MESH23", "MESM23"],
            "open": [100.0, 101.0, 105.0],
            "high": [101.0, 102.0, 106.0],
            "low": [99.0, 100.0, 104.0],
            "close": [100.5, 101.5, 105.5],
            "volume": [1000, 1100, 1500],
        }
    )


def test_apply_back_adjustment_skipped_for_non_matching_asset() -> None:
    cleaner = make_cleaner(
        {"provider": {"asset": "EQUITY"}, "back_adjustment": {"applies_to": "FUTURE"}}
    )
    data = roll_frame()

    result = cleaner.apply_back_adjustment(data.copy())

    pd.testing.assert_frame_equal(result, data)


def test_apply_back_adjustment_applied_for_matching_asset() -> None:
    cleaner = make_cleaner(
        {"provider": {"asset": "FUTURE"}, "back_adjustment": {"applies_to": "FUTURE"}}
    )
    data = roll_frame()

    result = cleaner.apply_back_adjustment(data.copy())

    adjustment = 101.5 - 105.0  # prev close - new open
    assert result["open"].iloc[0] == pytest.approx(100.0 + adjustment)
    assert result["close"].iloc[1] == pytest.approx(101.5 + adjustment)
    # Rows at/after the roll are unchanged
    assert result["open"].iloc[2] == pytest.approx(105.0)


def test_clean_end_to_end_applies_back_adjustment() -> None:
    cleaner = make_cleaner(
        {
            "missing_data": {},
            "provider": {"asset": "FUTURE"},
            "back_adjustment": {"applies_to": "FUTURE"},
        }
    )
    raw = roll_frame().rename(columns={"time": "ts_event"})

    records = cleaner.clean(raw)

    adjustment = 101.5 - 105.0
    assert records[0]["open"] == pytest.approx(100.0 + adjustment)
    assert records[2]["open"] == pytest.approx(105.0)
