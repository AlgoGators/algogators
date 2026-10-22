"""
Data quality monitoring for research pipelines.

Provides automated checks for data completeness, consistency, and freshness.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Set
import logging

import pandas as pd
import numpy as np


@dataclass
class QualityIssue:
    """
    Represents a data quality issue.

    Attributes:
        severity: error, warning, or info
        category: completeness, consistency, freshness, duplicates, outliers
        message: Human-readable description
        affected_records: Number of affected records
        details: Additional details about the issue
    """

    severity: str  # error, warning, info
    category: str  # completeness, consistency, freshness, duplicates, outliers
    message: str
    affected_records: int = 0
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class QualityReport:
    """
    Results of a data quality check run.

    Attributes:
        timestamp: When the check was run
        data_description: Description of checked data
        issues: List of found issues
        passed: Whether all checks passed
        total_records: Total records checked
        metrics: Summary metrics
    """

    timestamp: datetime
    data_description: str
    issues: List[QualityIssue]
    passed: bool
    total_records: int = 0
    metrics: Dict[str, Any] = field(default_factory=dict)

    def has_errors(self) -> bool:
        """Check if any error-level issues exist."""
        return any(i.severity == "error" for i in self.issues)

    def has_warnings(self) -> bool:
        """Check if any warning-level issues exist."""
        return any(i.severity == "warning" for i in self.issues)

    def summary(self) -> str:
        """Generate summary string."""
        errors = sum(1 for i in self.issues if i.severity == "error")
        warnings = sum(1 for i in self.issues if i.severity == "warning")
        infos = sum(1 for i in self.issues if i.severity == "info")

        status = "PASSED" if self.passed else "FAILED"
        return (
            f"Quality Report: {status}\n"
            f"  Records: {self.total_records:,}\n"
            f"  Errors: {errors}, Warnings: {warnings}, Info: {infos}"
        )


class QualityCheck(ABC):
    """
    Abstract base class for quality checks.

    Subclasses implement specific checks for different data quality aspects.
    """

    def __init__(self, name: str, severity: str = "error"):
        self.name = name
        self.severity = severity

    @abstractmethod
    def check(self, df: pd.DataFrame, **kwargs) -> List[QualityIssue]:
        """
        Run the quality check.

        Args:
            df: DataFrame to check

        Returns:
            List of QualityIssue objects
        """
        pass


class CompletenessCheck(QualityCheck):
    """Check for missing values in required columns."""

    def __init__(
        self,
        required_columns: Optional[List[str]] = None,
        max_null_pct: float = 0.05,
        severity: str = "error",
    ):
        super().__init__("completeness", severity)
        self.required_columns = required_columns
        self.max_null_pct = max_null_pct

    def check(self, df: pd.DataFrame, **kwargs) -> List[QualityIssue]:
        issues = []

        if df.empty:
            issues.append(QualityIssue(
                severity="error",
                category="completeness",
                message="DataFrame is empty",
                affected_records=0,
            ))
            return issues

        columns_to_check = self.required_columns or list(df.columns)

        # Check for missing columns
        missing_cols = [c for c in columns_to_check if c not in df.columns]
        if missing_cols:
            issues.append(QualityIssue(
                severity="error",
                category="completeness",
                message=f"Missing required columns: {missing_cols}",
                affected_records=len(df),
                details={"missing_columns": missing_cols},
            ))
            # Remove missing from check list
            columns_to_check = [c for c in columns_to_check if c in df.columns]

        # Check for null values
        for col in columns_to_check:
            null_count = df[col].isna().sum()
            null_pct = null_count / len(df)

            if null_pct > self.max_null_pct:
                issues.append(QualityIssue(
                    severity=self.severity,
                    category="completeness",
                    message=f"Column '{col}' has {null_pct:.1%} null values (threshold: {self.max_null_pct:.1%})",
                    affected_records=int(null_count),
                    details={"column": col, "null_pct": null_pct},
                ))

        return issues


class DateGapCheck(QualityCheck):
    """Check for gaps in date coverage."""

    def __init__(
        self,
        date_column: str = "date",
        max_gap_days: int = 7,
        severity: str = "warning",
    ):
        super().__init__("date_gaps", severity)
        self.date_column = date_column
        self.max_gap_days = max_gap_days

    def check(self, df: pd.DataFrame, **kwargs) -> List[QualityIssue]:
        issues = []

        if df.empty or self.date_column not in df.columns:
            return issues

        dates = pd.to_datetime(df[self.date_column]).dropna()
        if dates.empty:
            return issues

        # Get unique dates sorted
        unique_dates = dates.dt.date.unique()
        unique_dates = sorted(unique_dates)

        if len(unique_dates) < 2:
            return issues

        # Find gaps
        gaps = []
        for i in range(1, len(unique_dates)):
            gap = (unique_dates[i] - unique_dates[i-1]).days
            if gap > self.max_gap_days:
                gaps.append({
                    "start": str(unique_dates[i-1]),
                    "end": str(unique_dates[i]),
                    "days": gap,
                })

        if gaps:
            issues.append(QualityIssue(
                severity=self.severity,
                category="freshness",
                message=f"Found {len(gaps)} gaps > {self.max_gap_days} days",
                affected_records=len(gaps),
                details={"gaps": gaps[:10]},  # Limit to first 10
            ))

        return issues


class DuplicateCheck(QualityCheck):
    """Check for duplicate records."""

    def __init__(
        self,
        key_columns: Optional[List[str]] = None,
        max_dup_pct: float = 0.01,
        severity: str = "warning",
    ):
        super().__init__("duplicates", severity)
        self.key_columns = key_columns
        self.max_dup_pct = max_dup_pct

    def check(self, df: pd.DataFrame, **kwargs) -> List[QualityIssue]:
        issues = []

        if df.empty:
            return issues

        if self.key_columns:
            # Check only key columns exist
            missing = [c for c in self.key_columns if c not in df.columns]
            if missing:
                return issues
            dup_count = df.duplicated(subset=self.key_columns).sum()
        else:
            dup_count = df.duplicated().sum()

        dup_pct = dup_count / len(df)

        if dup_pct > self.max_dup_pct:
            issues.append(QualityIssue(
                severity=self.severity,
                category="duplicates",
                message=f"Found {dup_count:,} duplicates ({dup_pct:.2%})",
                affected_records=int(dup_count),
                details={
                    "duplicate_count": int(dup_count),
                    "duplicate_pct": dup_pct,
                    "key_columns": self.key_columns,
                },
            ))

        return issues


class OutlierCheck(QualityCheck):
    """Check for outliers in numeric columns."""

    def __init__(
        self,
        columns: Optional[List[str]] = None,
        z_threshold: float = 4.0,
        max_outlier_pct: float = 0.05,
        severity: str = "warning",
    ):
        super().__init__("outliers", severity)
        self.columns = columns
        self.z_threshold = z_threshold
        self.max_outlier_pct = max_outlier_pct

    def check(self, df: pd.DataFrame, **kwargs) -> List[QualityIssue]:
        issues = []

        if df.empty:
            return issues

        # Determine columns to check
        if self.columns:
            cols = [c for c in self.columns if c in df.columns]
        else:
            cols = df.select_dtypes(include=[np.number]).columns.tolist()

        for col in cols:
            values = df[col].dropna()
            if len(values) < 10:
                continue

            mean = values.mean()
            std = values.std()

            if std == 0:
                continue

            z_scores = np.abs((values - mean) / std)
            outlier_count = (z_scores > self.z_threshold).sum()
            outlier_pct = outlier_count / len(values)

            if outlier_pct > self.max_outlier_pct:
                issues.append(QualityIssue(
                    severity=self.severity,
                    category="outliers",
                    message=f"Column '{col}' has {outlier_count:,} outliers ({outlier_pct:.2%})",
                    affected_records=int(outlier_count),
                    details={
                        "column": col,
                        "mean": float(mean),
                        "std": float(std),
                        "z_threshold": self.z_threshold,
                    },
                ))

        return issues


class ConsistencyCheck(QualityCheck):
    """Check for consistency in categorical or price columns."""

    def __init__(
        self,
        price_column: Optional[str] = None,
        price_range: tuple = (0.0, 1.0),
        severity: str = "error",
    ):
        super().__init__("consistency", severity)
        self.price_column = price_column
        self.price_range = price_range

    def check(self, df: pd.DataFrame, **kwargs) -> List[QualityIssue]:
        issues = []

        if df.empty:
            return issues

        # Check price range
        if self.price_column and self.price_column in df.columns:
            prices = df[self.price_column].dropna()

            out_of_range = (
                (prices < self.price_range[0]) |
                (prices > self.price_range[1])
            ).sum()

            if out_of_range > 0:
                issues.append(QualityIssue(
                    severity=self.severity,
                    category="consistency",
                    message=f"Found {out_of_range:,} prices outside range {self.price_range}",
                    affected_records=int(out_of_range),
                    details={
                        "column": self.price_column,
                        "expected_range": self.price_range,
                    },
                ))

        return issues


class DataQualityMonitor:
    """
    Orchestrates data quality checks.

    Example:
        monitor = DataQualityMonitor()
        monitor.add_check(CompletenessCheck(["timestamp", "price"]))
        monitor.add_check(DateGapCheck(max_gap_days=3))

        report = monitor.run_all_checks(trades_df)
        if not report.passed:
            print("Quality issues found!")
            for issue in report.issues:
                print(f"  [{issue.severity}] {issue.message}")
    """

    def __init__(
        self,
        fail_on_error: bool = True,
        logger: Optional[logging.Logger] = None,
    ):
        self.fail_on_error = fail_on_error
        self.logger = logger or logging.getLogger("validation.quality")
        self._checks: List[QualityCheck] = []

    def add_check(self, check: QualityCheck) -> None:
        """Add a quality check."""
        self._checks.append(check)

    def add_default_checks(
        self,
        required_columns: Optional[List[str]] = None,
        date_column: str = "timestamp",
        price_column: str = "price",
    ) -> None:
        """Add standard default checks."""
        self.add_check(CompletenessCheck(required_columns))
        self.add_check(DateGapCheck(date_column, max_gap_days=7))
        self.add_check(DuplicateCheck())
        self.add_check(OutlierCheck())
        self.add_check(ConsistencyCheck(price_column=price_column))

    def run_all_checks(
        self,
        df: pd.DataFrame,
        data_description: str = "data",
        **kwargs,
    ) -> QualityReport:
        """
        Run all registered quality checks.

        Args:
            df: DataFrame to check
            data_description: Description for the report
            **kwargs: Additional arguments passed to checks

        Returns:
            QualityReport with all findings
        """
        all_issues: List[QualityIssue] = []

        for check in self._checks:
            try:
                issues = check.check(df, **kwargs)
                all_issues.extend(issues)
            except Exception as e:
                self.logger.error(f"Check '{check.name}' failed: {e}")
                all_issues.append(QualityIssue(
                    severity="error",
                    category="check_failure",
                    message=f"Check '{check.name}' failed: {str(e)}",
                ))

        has_errors = any(i.severity == "error" for i in all_issues)
        passed = not has_errors if self.fail_on_error else True

        report = QualityReport(
            timestamp=datetime.utcnow(),
            data_description=data_description,
            issues=all_issues,
            passed=passed,
            total_records=len(df) if not df.empty else 0,
            metrics={
                "total_issues": len(all_issues),
                "errors": sum(1 for i in all_issues if i.severity == "error"),
                "warnings": sum(1 for i in all_issues if i.severity == "warning"),
                "infos": sum(1 for i in all_issues if i.severity == "info"),
            },
        )

        self.logger.info(report.summary())
        return report

    def validate_before_backtest(
        self,
        trades_df: pd.DataFrame,
        strict: bool = True,
    ) -> QualityReport:
        """
        Validate data before running a backtest.

        Args:
            trades_df: Trading data to validate
            strict: If True, fail on any error

        Returns:
            QualityReport

        Raises:
            ValueError: If strict=True and validation fails
        """
        # Add backtest-specific checks if not already present
        check_names = {c.name for c in self._checks}

        if "completeness" not in check_names:
            self.add_check(CompletenessCheck([
                "timestamp", "market_id", "price", "usd_amount"
            ]))

        if "duplicates" not in check_names:
            self.add_check(DuplicateCheck(key_columns=["market_id", "timestamp"]))

        report = self.run_all_checks(trades_df, "backtest data")

        if strict and not report.passed:
            raise ValueError(
                f"Data validation failed with {report.metrics['errors']} errors. "
                "See report for details."
            )

        return report


__all__ = [
    "QualityIssue",
    "QualityReport",
    "QualityCheck",
    "DataQualityMonitor",
    "CompletenessCheck",
    "DateGapCheck",
    "DuplicateCheck",
    "OutlierCheck",
    "ConsistencyCheck",
]
