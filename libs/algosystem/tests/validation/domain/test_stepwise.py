"""Tests for the stepwise (Romano-Wolf) permutation test."""

import numpy as np
import pytest
from algosystem.shared.errors import ValidationError
from algosystem.validation.domain.statistics.stepwise import (
    StepwiseResults,
    stepwise_permutation_test,
)


def corr_backtest(params, returns):
    """Deterministic 'Sharpe': correlation of returns with time, plus an offset.

    On the unshuffled ramp series the correlation is exactly 1.0; any
    complete shuffle drives it toward 0, which makes outcomes predictable.
    """
    t = np.arange(len(returns))
    return float(np.corrcoef(t, returns)[0, 1]) + params["offset"]


@pytest.fixture
def ramp_returns():
    return np.linspace(0.0, 1.0, 100)


class TestStepwisePermutationTest:
    def test_unknown_shuffle_method_raises(self, ramp_returns):
        with pytest.raises(ValidationError, match="Unknown shuffle method"):
            stepwise_permutation_test(
                corr_backtest,
                ramp_returns,
                [{"offset": 0.0}],
                np.array([1.0]),
                n_reps=5,
                shuffle_method="bogus",
            )

    def test_all_competitors_rejected(self, ramp_returns):
        param_list = [{"offset": 0.0}, {"offset": -0.05}, {"offset": -0.1}]
        original = np.array([1.0, 0.95, 0.9])
        res = stepwise_permutation_test(
            corr_backtest,
            ramp_returns,
            param_list,
            original,
            n_reps=30,
            alpha=0.10,
            seed=1,
        )
        assert res.n_rejected == 3
        assert res.passed.tolist() == [True, True, True]
        # Every original Sharpe is far above the null, so each step keeps
        # the minimum attainable p-value of 1 / (n_reps + 1).
        expected_min = 1.0 / 31.0
        assert np.allclose(res.stepwise_pvalues, expected_min)

    def test_no_competitor_rejected_stops_immediately(self, ramp_returns):
        param_list = [{"offset": 0.0}, {"offset": -0.05}]
        original = np.array([0.0, -0.05])
        res = stepwise_permutation_test(
            corr_backtest,
            ramp_returns,
            param_list,
            original,
            n_reps=30,
            alpha=0.10,
            seed=1,
        )
        assert res.n_rejected == 0
        assert not res.passed.any()
        assert res.stepwise_pvalues[0] > 0.10
        # Remaining steps are never tested.
        assert np.isnan(res.stepwise_pvalues[1])

    def test_partial_rejection(self, ramp_returns):
        param_list = [{"offset": 0.0}, {"offset": 0.0}]
        original = np.array([0.9, 0.0])
        res = stepwise_permutation_test(
            corr_backtest,
            ramp_returns,
            param_list,
            original,
            n_reps=30,
            alpha=0.10,
            seed=1,
        )
        assert res.n_rejected == 1
        assert res.passed.tolist() == [True, False]
        assert res.stepwise_pvalues[0] <= 0.10
        assert res.stepwise_pvalues[1] > 0.10

    def test_sort_indices_order_competitors_best_to_worst(self, ramp_returns):
        param_list = [{"offset": -0.1}, {"offset": 0.0}, {"offset": -0.05}]
        original = np.array([0.9, 1.0, 0.95])
        res = stepwise_permutation_test(
            corr_backtest,
            ramp_returns,
            param_list,
            original,
            n_reps=10,
            alpha=0.10,
            seed=1,
        )
        assert res.sort_indices.tolist() == [1, 2, 0]

    def test_pvalues_are_monotone_non_decreasing(self, ramp_returns):
        # Step 0 tests offset +0.5 against a null that includes itself, so
        # its p-value is sizable. Step 1's raw p-value (offset -0.5 only)
        # would be smaller; monotonicity must clamp it up to step 0's value.
        param_list = [{"offset": 0.5}, {"offset": -0.5}]
        original = np.array([0.6, 0.0])
        res = stepwise_permutation_test(
            corr_backtest,
            ramp_returns,
            param_list,
            original,
            n_reps=50,
            alpha=1.0,
            seed=2,
        )
        assert res.n_rejected == 2
        assert res.stepwise_pvalues[1] == res.stepwise_pvalues[0]
        raw_min = 1.0 / 51.0
        assert res.stepwise_pvalues[0] > raw_min

    @pytest.mark.parametrize("method", ["block", "cyclic"])
    def test_alternate_shuffle_methods_run(self, ramp_returns, method):
        res = stepwise_permutation_test(
            corr_backtest,
            ramp_returns,
            [{"offset": 0.0}],
            np.array([0.99]),
            n_reps=20,
            alpha=0.10,
            shuffle_method=method,
            block_size=10,
            seed=3,
        )
        assert res.n_params == 1
        pval = res.stepwise_pvalues[0]
        assert 0.0 < pval <= 1.0

    def test_result_metadata_round_trips(self, ramp_returns):
        param_list = [{"offset": 0.0}]
        original = np.array([1.0])
        res = stepwise_permutation_test(
            corr_backtest,
            ramp_returns,
            param_list,
            original,
            n_reps=15,
            alpha=0.25,
            seed=7,
        )
        assert res.n_params == 1
        assert res.n_reps == 15
        assert res.alpha == 0.25
        assert res.param_list is param_list
        assert res.original_sharpes is original


class TestStepwiseResultsSummary:
    def _make_results(self, pvalues, passed, n_rejected):
        n = len(pvalues)
        sharpes = np.linspace(2.0, 1.0, n)
        return StepwiseResults(
            n_params=n,
            n_reps=100,
            alpha=0.10,
            param_list=[{"p": i} for i in range(n)],
            original_sharpes=sharpes,
            sort_indices=np.arange(n),
            stepwise_pvalues=np.asarray(pvalues, dtype=float),
            passed=np.asarray(passed, dtype=bool),
            n_rejected=n_rejected,
        )

    def test_summary_contains_header_and_counts(self):
        res = self._make_results([0.01, 0.5], [True, False], 1)
        lines = res.summary()
        joined = "\n".join(lines)
        assert "STEPWISE PERMUTATION TEST (Romano-Wolf)" in joined
        assert "Competitors tested   : 2" in joined
        assert "Permutation reps     : 100" in joined
        assert "FWE alpha            : 0.1" in joined
        assert "Nulls rejected       : 1" in joined
        assert "Top 2 parameter sets" in joined

    def test_summary_marks_rejections(self):
        res = self._make_results([0.01, 0.5], [True, False], 1)
        lines = res.summary()
        row_best = next(line for line in lines if line.startswith("   1"))
        row_worst = next(line for line in lines if line.startswith("   2"))
        assert "YES" in row_best
        assert "no" in row_worst
        assert "{'p': 0}" in row_best

    def test_summary_handles_nan_pvalues(self):
        res = self._make_results([0.5, np.nan], [False, False], 0)
        lines = res.summary()
        assert any("nan" in line for line in lines)

    def test_summary_caps_rows_at_twenty(self):
        n = 25
        res = self._make_results([0.01] * n, [True] * n, n)
        lines = res.summary()
        assert "Top 20 parameter sets" in "\n".join(lines)
        rank_rows = [line for line in lines if line.lstrip()[:2].strip().isdigit()]
        assert len(rank_rows) == 20
