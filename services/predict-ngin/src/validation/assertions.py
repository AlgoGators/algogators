"""
Research assertions for validating data integrity.

These assertions help catch common research errors like:
- Look-ahead bias
- Insufficient data
- Duplicate records
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, List, Optional, Set
import pandas as pd


class ResearchAssertionError(Exception):
    """Raised when a research assertion fails."""

    pass


@dataclass
class ResearchAssertion:
    """
    A reusable research assertion.

    Attributes:
        name: Assertion name
        description: What this assertion checks
        check_fn: Function that returns True if assertion passes
        severity: "error" (raises) or "warning" (logs)
    """

    name: str
    description: str
    check_fn: Callable[..., bool]
    severity: str = "error"  # error or warning

    def check(self, *args, **kwargs) -> bool:
        """Run the assertion check."""
        return self.check_fn(*args, **kwargs)

    def assert_or_raise(self, *args, **kwargs) -> None:
        """Run check and raise if failed."""
        if not self.check(*args, **kwargs):
            raise ResearchAssertionError(
                f"Assertion '{self.name}' failed: {self.description}"
            )


def assert_no_lookahead(
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    time_col: str = "timestamp",
    strict: bool = True,
) -> bool:
    """
    Assert no look-ahead bias between train and test sets.

    Checks that all training data comes before all test data.

    Args:
        train_df: Training DataFrame
        test_df: Test DataFrame
        time_col: Column containing timestamps
        strict: If True, raises on failure

    Returns:
        True if assertion passes

    Raises:
        ResearchAssertionError: If look-ahead bias detected and strict=True
    """
    if train_df.empty or test_df.empty:
        return True

    if time_col not in train_df.columns or time_col not in test_df.columns:
        if strict:
            raise ResearchAssertionError(
                f"Column '{time_col}' not found in DataFrames"
            )
        return False

    train_max = pd.to_datetime(train_df[time_col]).max()
    test_min = pd.to_datetime(test_df[time_col]).min()

    if train_max >= test_min:
        if strict:
            raise ResearchAssertionError(
                f"Look-ahead bias detected: training data extends to {train_max}, "
                f"but test data starts at {test_min}"
            )
        return False

    return True


def assert_sufficient_data(
    df: pd.DataFrame,
    min_rows: int = 100,
    min_unique_dates: int = 10,
    date_col: str = "date",
    strict: bool = True,
) -> bool:
    """
    Assert sufficient data for analysis.

    Args:
        df: DataFrame to check
        min_rows: Minimum rows required
        min_unique_dates: Minimum unique dates required
        date_col: Column containing dates
        strict: If True, raises on failure

    Returns:
        True if assertion passes

    Raises:
        ResearchAssertionError: If insufficient data and strict=True
    """
    if len(df) < min_rows:
        if strict:
            raise ResearchAssertionError(
                f"Insufficient data: {len(df)} rows (need {min_rows})"
            )
        return False

    if date_col in df.columns:
        unique_dates = df[date_col].nunique()
        if unique_dates < min_unique_dates:
            if strict:
                raise ResearchAssertionError(
                    f"Insufficient date coverage: {unique_dates} unique dates "
                    f"(need {min_unique_dates})"
                )
            return False

    return True


def assert_no_duplicates(
    df: pd.DataFrame,
    key_cols: Optional[List[str]] = None,
    strict: bool = True,
) -> bool:
    """
    Assert no duplicate records in DataFrame.

    Args:
        df: DataFrame to check
        key_cols: Columns to check for duplicates (default: all)
        strict: If True, raises on failure

    Returns:
        True if assertion passes

    Raises:
        ResearchAssertionError: If duplicates found and strict=True
    """
    if df.empty:
        return True

    if key_cols:
        missing = [c for c in key_cols if c not in df.columns]
        if missing:
            if strict:
                raise ResearchAssertionError(
                    f"Key columns not found: {missing}"
                )
            return False
        dup_count = df.duplicated(subset=key_cols).sum()
    else:
        dup_count = df.duplicated().sum()

    if dup_count > 0:
        if strict:
            raise ResearchAssertionError(
                f"Found {dup_count} duplicate records"
            )
        return False

    return True


def assert_monotonic_timestamps(
    df: pd.DataFrame,
    time_col: str = "timestamp",
    strict: bool = True,
) -> bool:
    """
    Assert timestamps are monotonically increasing.

    Args:
        df: DataFrame to check
        time_col: Column containing timestamps
        strict: If True, raises on failure

    Returns:
        True if assertion passes
    """
    if df.empty or time_col not in df.columns:
        return True

    timestamps = pd.to_datetime(df[time_col])
    if not timestamps.is_monotonic_increasing:
        if strict:
            raise ResearchAssertionError(
                f"Timestamps in '{time_col}' are not monotonically increasing"
            )
        return False

    return True


def assert_no_future_data(
    df: pd.DataFrame,
    time_col: str = "timestamp",
    reference_time: Optional[datetime] = None,
    strict: bool = True,
) -> bool:
    """
    Assert no data from the future.

    Args:
        df: DataFrame to check
        time_col: Column containing timestamps
        reference_time: Reference time (default: now)
        strict: If True, raises on failure

    Returns:
        True if assertion passes
    """
    if df.empty or time_col not in df.columns:
        return True

    reference_time = reference_time or datetime.utcnow()
    max_time = pd.to_datetime(df[time_col]).max()

    if max_time > pd.Timestamp(reference_time):
        if strict:
            raise ResearchAssertionError(
                f"Data contains future timestamps: max is {max_time}, "
                f"reference is {reference_time}"
            )
        return False

    return True


def assert_valid_prices(
    df: pd.DataFrame,
    price_col: str = "price",
    min_price: float = 0.0,
    max_price: float = 1.0,
    strict: bool = True,
) -> bool:
    """
    Assert prices are within valid range.

    Args:
        df: DataFrame to check
        price_col: Column containing prices
        min_price: Minimum valid price
        max_price: Maximum valid price
        strict: If True, raises on failure

    Returns:
        True if assertion passes
    """
    if df.empty or price_col not in df.columns:
        return True

    prices = df[price_col].dropna()
    invalid = ((prices < min_price) | (prices > max_price)).sum()

    if invalid > 0:
        if strict:
            raise ResearchAssertionError(
                f"Found {invalid} prices outside valid range [{min_price}, {max_price}]"
            )
        return False

    return True


__all__ = [
    "ResearchAssertion",
    "ResearchAssertionError",
    "assert_no_lookahead",
    "assert_sufficient_data",
    "assert_no_duplicates",
    "assert_monotonic_timestamps",
    "assert_no_future_data",
    "assert_valid_prices",
]
