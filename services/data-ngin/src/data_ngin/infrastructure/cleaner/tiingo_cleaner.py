import logging
from enum import Enum
from typing import Any

import pandas as pd
from data_ngin.domain.services import MissingDataFiller
from data_ngin.infrastructure.cleaner.cleaner import Cleaner


class RequiredFields(Enum):
    """
    Required fields for cleaned Tiingo equity data. These map 1:1 to the
    columns of equities_data.ohlcv_1d: raw OHLCV + adjusted OHLCV + the
    dividend/split event fields.
    """

    TIME = "time"
    SYMBOL = "symbol"
    OPEN = "open"
    HIGH = "high"
    LOW = "low"
    CLOSE = "close"
    VOLUME = "volume"
    ADJ_OPEN = "adj_open"
    ADJ_HIGH = "adj_high"
    ADJ_LOW = "adj_low"
    ADJUSTED_CLOSE = "adjusted_close"
    ADJ_VOLUME = "adj_volume"
    DIV_CASH = "div_cash"
    SPLIT_FACTOR = "split_factor"


# Price columns that must be strictly positive to be considered valid.
PRICE_COLUMNS = [
    "open",
    "high",
    "low",
    "close",
    "adj_open",
    "adj_high",
    "adj_low",
    "adjusted_close",
]

# Float columns coerced to float64. adj_volume is split-adjusted (volume * cumulative
# split factor) so it can be fractional -- kept as float, unlike raw integer volume.
FLOAT_COLUMNS = [*PRICE_COLUMNS, "adj_volume", "div_cash", "split_factor"]


class TiingoCleaner(Cleaner):
    """
    Cleaner for Tiingo End-of-Day equity/ETF OHLCV data.

    Same contract as DatabentoCleaner: clean() returns a list of row dicts
    ready for OhlcvRepository.insert_data. Equity-specific behavior:
      - parses Tiingo's ISO-8601 'Z' timestamps to UTC
      - keeps the adjusted columns
      - drops rows with non-positive prices / negative volume
      - de-duplicates on (symbol, time)
      - no futures back-adjustment
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config: dict[str, Any] = config or {}
        self.logger: logging.Logger = logging.getLogger("TiingoCleaner")

    def clean(self, data: pd.DataFrame) -> list[dict[str, Any]]:
        """Validate, handle missing data, and transform raw Tiingo data into row
        dicts. Returns an empty list for empty input."""
        if data is None or data.empty:
            self.logger.warning("[TiingoCleaner] Received empty DataFrame -- nothing to clean.")
            return []

        data = self.validate_fields(data)
        data = self.handle_missing_data(data)
        data = self.transform_data(data)
        return data.to_dict(orient="records")

    def validate_fields(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Ensure all required columns are present, then slice to exactly the schema
        columns (dropping any extras the fetcher may have left in).

        Raises:
            ValueError: If any required column is absent.
        """
        required: list[str] = [f.value for f in RequiredFields]
        missing: list[str] = [c for c in required if c not in data.columns]
        if missing:
            self.logger.error(f"[TiingoCleaner] Missing required fields: {missing}")
            raise ValueError(f"Missing required fields: {missing}")

        return data[required].copy()

    def handle_missing_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Apply the config-driven missing-data strategies, then always drop rows
        with null time/symbol and rows with non-positive prices or negative volume.
        """
        data = MissingDataFiller(self.config.get("missing_data", {})).fill(data)

        initial_len = len(data)
        data = data.dropna(subset=["time", "symbol"])

        invalid = (data[PRICE_COLUMNS] <= 0).any(axis=1) | (data["volume"] < 0)
        dropped = int(invalid.sum())
        if dropped:
            self.logger.warning(
                f"[TiingoCleaner] Dropped {dropped} rows with non-positive price or negative volume."
            )
        # .copy() keeps the writes in transform_data off a view.
        data = data[~invalid].copy()

        self.logger.info(
            f"[TiingoCleaner] handle_missing_data complete. Retained {len(data)}/{initial_len} rows."
        )
        return data

    def transform_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Standardize types and ordering for DB insertion:
          - 'time' parsed to UTC datetime (Tiingo returns ISO-8601 with 'Z')
          - prices -> float64, volume -> int64
          - symbol stripped/uppercased
          - de-duplicated on (symbol, time), sorted ascending
        """
        data["time"] = pd.to_datetime(data["time"], utc=True, errors="coerce")
        bad_ts = int(data["time"].isna().sum())
        if bad_ts:
            self.logger.warning(
                f"[TiingoCleaner] Dropping {bad_ts} rows with unparseable timestamps."
            )
            data = data.dropna(subset=["time"])

        for col in FLOAT_COLUMNS:
            data[col] = pd.to_numeric(data[col], errors="coerce").astype("float64")

        data["volume"] = pd.to_numeric(data["volume"], errors="coerce")
        bad_vol = int(data["volume"].isna().sum())
        if bad_vol:
            self.logger.warning(f"[TiingoCleaner] Dropping {bad_vol} rows with non-numeric volume.")
            data = data.dropna(subset=["volume"])
        data["volume"] = data["volume"].astype("int64")

        data["symbol"] = data["symbol"].str.strip().str.upper()

        pre_dedup = len(data)
        data = data.drop_duplicates(subset=["symbol", "time"])
        if pre_dedup - len(data):
            self.logger.info(
                f"[TiingoCleaner] Removed {pre_dedup - len(data)} duplicate (symbol, time) rows."
            )

        return data.sort_values(by=["symbol", "time"]).reset_index(drop=True)
