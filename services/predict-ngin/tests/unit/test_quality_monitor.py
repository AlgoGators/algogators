"""Unit tests for DataQualityMonitor."""

import pytest
from datetime import datetime, timedelta

import pandas as pd
import numpy as np

from src.validation.quality import (
    QualityIssue,
    QualityReport,
    DataQualityMonitor,
    CompletenessCheck,
    DateGapCheck,
    DuplicateCheck,
    OutlierCheck,
    ConsistencyCheck,
)


@pytest.fixture
def sample_trades_df():
    """Create sample trades DataFrame."""
    np.random.seed(42)
    n_trades = 100

    base_date = datetime(2025, 1, 1)
    dates = [base_date + timedelta(days=i) for i in range(n_trades)]

    return pd.DataFrame({
        "timestamp": dates,
        "date": [d.date() for d in dates],
        "market_id": [f"market_{i % 10}" for i in range(n_trades)],
        "price": np.random.uniform(0.1, 0.9, n_trades),
        "usd_amount": np.random.lognormal(5, 1, n_trades),
    })


class TestQualityIssue:
    def test_dataclass_fields(self):
        issue = QualityIssue(
            severity="error",
            category="completeness",
            message="Missing data",
            affected_records=10,
        )

        assert issue.severity == "error"
        assert issue.category == "completeness"
        assert issue.affected_records == 10


class TestQualityReport:
    def test_has_errors(self):
        report = QualityReport(
            timestamp=datetime.now(),
            data_description="test",
            issues=[
                QualityIssue("error", "test", "Error message"),
                QualityIssue("warning", "test", "Warning message"),
            ],
            passed=False,
        )

        assert report.has_errors()
        assert report.has_warnings()

    def test_summary(self):
        report = QualityReport(
            timestamp=datetime.now(),
            data_description="test",
            issues=[],
            passed=True,
            total_records=100,
        )

        summary = report.summary()

        assert "PASSED" in summary
        assert "100" in summary


class TestCompletenessCheck:
    def test_passes_complete_data(self, sample_trades_df):
        check = CompletenessCheck(["timestamp", "market_id", "price"])

        issues = check.check(sample_trades_df)

        assert len(issues) == 0

    def test_detects_missing_column(self, sample_trades_df):
        check = CompletenessCheck(["timestamp", "nonexistent_column"])

        issues = check.check(sample_trades_df)

        assert len(issues) == 1
        assert "Missing required columns" in issues[0].message

    def test_detects_null_values(self):
        df = pd.DataFrame({
            "col1": [1, 2, None, None, None],  # 60% null
            "col2": [1, 2, 3, 4, 5],
        })

        check = CompletenessCheck(["col1"], max_null_pct=0.5)

        issues = check.check(df)

        assert len(issues) == 1
        assert "60" in issues[0].message or "0.6" in issues[0].message

    def test_empty_dataframe(self):
        check = CompletenessCheck()

        issues = check.check(pd.DataFrame())

        assert len(issues) == 1
        assert issues[0].severity == "error"


class TestDateGapCheck:
    def test_passes_continuous_dates(self, sample_trades_df):
        check = DateGapCheck(date_column="timestamp", max_gap_days=2)

        issues = check.check(sample_trades_df)

        assert len(issues) == 0

    def test_detects_gap(self):
        dates = [
            datetime(2025, 1, 1),
            datetime(2025, 1, 2),
            datetime(2025, 1, 15),  # 13 day gap
        ]
        df = pd.DataFrame({"date": dates})

        check = DateGapCheck(date_column="date", max_gap_days=7)

        issues = check.check(df)

        assert len(issues) == 1
        assert "gap" in issues[0].message.lower()


class TestDuplicateCheck:
    def test_passes_no_duplicates(self, sample_trades_df):
        check = DuplicateCheck()

        issues = check.check(sample_trades_df)

        assert len(issues) == 0

    def test_detects_duplicates(self):
        df = pd.DataFrame({
            "id": [1, 1, 2, 3, 3, 3],  # 3 duplicates
            "value": [10, 10, 20, 30, 30, 30],
        })

        check = DuplicateCheck(max_dup_pct=0.1)

        issues = check.check(df)

        assert len(issues) == 1
        assert "duplicate" in issues[0].message.lower()

    def test_key_columns(self):
        df = pd.DataFrame({
            "market_id": ["A", "A", "B"],
            "timestamp": [1, 1, 2],
            "price": [0.5, 0.5, 0.6],
        })

        check = DuplicateCheck(key_columns=["market_id", "timestamp"])

        issues = check.check(df)

        assert len(issues) == 1


class TestOutlierCheck:
    def test_passes_normal_data(self, sample_trades_df):
        check = OutlierCheck(z_threshold=4.0)

        issues = check.check(sample_trades_df)

        # May or may not have outliers depending on random seed
        assert all(i.severity == "warning" for i in issues)

    def test_detects_outliers(self):
        values = list(np.random.normal(0, 1, 100)) + [100]  # Add extreme outlier
        df = pd.DataFrame({"value": values})

        check = OutlierCheck(columns=["value"], z_threshold=3.0, max_outlier_pct=0.005)

        issues = check.check(df)

        assert len(issues) == 1
        assert "outlier" in issues[0].message.lower()


class TestConsistencyCheck:
    def test_passes_valid_prices(self, sample_trades_df):
        check = ConsistencyCheck(price_column="price", price_range=(0.0, 1.0))

        issues = check.check(sample_trades_df)

        assert len(issues) == 0

    def test_detects_invalid_prices(self):
        df = pd.DataFrame({
            "price": [0.5, 0.6, 1.5, -0.1],  # 2 invalid
        })

        check = ConsistencyCheck(price_column="price", price_range=(0.0, 1.0))

        issues = check.check(df)

        assert len(issues) == 1
        assert issues[0].affected_records == 2


class TestDataQualityMonitor:
    def test_run_all_checks(self, sample_trades_df):
        monitor = DataQualityMonitor()
        monitor.add_check(CompletenessCheck(["timestamp", "price"]))
        monitor.add_check(DuplicateCheck())

        report = monitor.run_all_checks(sample_trades_df)

        assert report.passed
        assert report.total_records == len(sample_trades_df)

    def test_add_default_checks(self, sample_trades_df):
        monitor = DataQualityMonitor()
        monitor.add_default_checks(
            required_columns=["timestamp", "market_id"],
            price_column="price",
        )

        report = monitor.run_all_checks(sample_trades_df)

        assert report.passed

    def test_validate_before_backtest(self, sample_trades_df):
        monitor = DataQualityMonitor()

        report = monitor.validate_before_backtest(sample_trades_df, strict=False)

        assert isinstance(report, QualityReport)

    def test_validate_strict_raises(self):
        monitor = DataQualityMonitor()
        monitor.add_check(CompletenessCheck(["nonexistent"]))

        with pytest.raises(ValueError, match="validation failed"):
            monitor.validate_before_backtest(pd.DataFrame(), strict=True)

    def test_fail_on_error_false(self, sample_trades_df):
        monitor = DataQualityMonitor(fail_on_error=False)
        monitor.add_check(CompletenessCheck(["nonexistent"]))

        report = monitor.run_all_checks(sample_trades_df)

        # Report passes even with errors when fail_on_error=False
        assert report.passed
