"""Tests for returns-series data validation."""

import numpy as np
from algosystem.validation.domain.statistics.validity import (
    ValidationResult,
    validate_returns,
)


def clean_returns(n=100, seed=42):
    return np.random.default_rng(seed).normal(0.0002, 0.01, size=n)


class TestValidateReturnsHappyPath:
    def test_clean_series_is_valid(self):
        result = validate_returns(clean_returns())
        assert result.is_valid
        assert result.n_observations == 100
        assert result.issues == []
        assert result.warnings == []

    def test_result_is_frozen_dataclass(self):
        result = validate_returns(clean_returns())
        assert isinstance(result, ValidationResult)


class TestValidateReturnsIssues:
    def test_too_few_observations(self):
        result = validate_returns(clean_returns(n=10))
        assert not result.is_valid
        assert any("Only 10 observations (minimum 50)" in issue for issue in result.issues)

    def test_custom_min_observations(self):
        result = validate_returns(clean_returns(n=10), min_observations=5)
        assert result.is_valid

    def test_empty_array(self):
        result = validate_returns(np.array([]))
        assert not result.is_valid
        assert result.n_observations == 0
        assert any("Only 0 observations" in issue for issue in result.issues)

    def test_nan_values_flagged(self):
        returns = clean_returns()
        returns[3] = np.nan
        returns[7] = np.nan
        result = validate_returns(returns)
        assert not result.is_valid
        assert any("2 NaN values found" in issue for issue in result.issues)

    def test_inf_values_flagged(self):
        returns = clean_returns()
        returns[5] = np.inf
        with np.errstate(invalid="ignore"):
            result = validate_returns(returns)
        assert not result.is_valid
        assert any("1 infinite values found" in issue for issue in result.issues)

    def test_zero_variance_flagged(self):
        result = validate_returns(np.full(60, 0.001))
        assert not result.is_valid
        assert any("zero variance" in issue for issue in result.issues)


class TestValidateReturnsWarnings:
    def test_extreme_values_warn_but_stay_valid(self):
        returns = clean_returns()
        returns[10] = 0.8
        returns[20] = -0.8
        result = validate_returns(returns)
        assert result.is_valid
        assert any("2 returns exceed 50%" in w for w in result.warnings)
        assert any("max |r| = 0.8000" in w for w in result.warnings)

    def test_custom_max_abs_return_threshold(self):
        returns = clean_returns()
        returns[0] = 0.2
        result = validate_returns(returns, max_abs_return=0.1)
        assert result.is_valid
        assert any("exceed 10%" in w for w in result.warnings)

    def test_mostly_zero_returns_warn(self):
        returns = np.zeros(100)
        returns[:40] = clean_returns(n=40)
        result = validate_returns(returns)
        assert result.is_valid
        assert any("60% of returns are exactly zero" in w for w in result.warnings)

    def test_high_drift_warns(self):
        rng = np.random.default_rng(0)
        returns = rng.normal(0.0, 0.001, size=300) + 0.01
        result = validate_returns(returns)
        assert result.is_valid
        assert any("Annualized mean return" in w for w in result.warnings)

    def test_drift_check_skipped_for_short_series(self):
        # n <= 20 skips the drift check even with a huge mean.
        returns = np.full(15, 0.02)
        returns[0] = 0.021  # avoid the zero-variance issue
        result = validate_returns(returns, min_observations=10)
        assert result.is_valid
        assert not any("Annualized" in w for w in result.warnings)


class TestValidationResultSummary:
    def test_pass_summary_with_no_findings(self):
        result = validate_returns(clean_returns())
        lines = result.summary()
        assert lines[0] == "Data Validation: PASS (100 observations)"
        assert "  No issues found." in lines

    def test_fail_summary_lists_errors(self):
        returns = clean_returns(n=10)
        returns[0] = np.nan
        lines = validate_returns(returns).summary()
        assert lines[0].startswith("Data Validation: FAIL")
        assert any(line.startswith("  [ERROR]") for line in lines)
        assert "  No issues found." not in lines

    def test_warning_only_summary_still_passes(self):
        returns = clean_returns()
        returns[0] = 0.9
        lines = validate_returns(returns).summary()
        assert lines[0].startswith("Data Validation: PASS")
        assert any(line.startswith("  [WARN]") for line in lines)
        assert "  No issues found." not in lines
