"""Tests for walk-forward analysis with synthetic backtest functions."""

import numpy as np
import pytest
from algosystem.shared.errors import ValidationError
from algosystem.validation.domain.statistics.walkforward import (
    WalkForwardResults,
    walk_forward_analysis,
)

# With n=200, n_folds=2, is_ratio=0.8: each IS window has 80 points and
# each OOS window has 20, so len(data) distinguishes IS from OOS calls.
RETURNS_200 = np.zeros(200)


def make_results(
    wfe: float = 1.0,
    vetoed: bool = False,
    catastrophic: list | None = None,
    degradation: np.ndarray | None = None,
) -> WalkForwardResults:
    n_folds = 2
    if degradation is None:
        degradation = np.array([1.0, 1.0])
    return WalkForwardResults(
        n_folds=n_folds,
        purge_gap=3,
        is_sharpes=np.array([1.0, 2.0]),
        oos_sharpes=np.array([0.5, 1.0]),
        best_params_per_fold=[{"x": 1}, {"x": 2}],
        degradation_ratios=degradation,
        wfe=wfe,
        mean_is_sharpe=1.5,
        mean_oos_sharpe=0.75,
        frac_oos_positive=1.0,
        frac_oos_profitable=1.0,
        catastrophic_folds=catastrophic or [],
        vetoed=vetoed,
    )


class TestWalkForwardAnalysis:
    def test_fold_size_too_small_raises(self):
        with pytest.raises(ValidationError, match="too small"):
            walk_forward_analysis(lambda p, d: 0.0, np.zeros(50), {"x": [1]}, n_folds=5)

    def test_constant_backtest_perfect_wfe(self):
        def bt(params, data):
            return params["x"]

        res = walk_forward_analysis(bt, RETURNS_200, {"x": [1.0, 2.0, 3.0]}, n_folds=2)
        assert res.n_folds == 2
        assert np.allclose(res.is_sharpes, [3.0, 3.0])
        assert np.allclose(res.oos_sharpes, [3.0, 3.0])
        assert res.best_params_per_fold == [{"x": 3.0}, {"x": 3.0}]
        assert np.allclose(res.degradation_ratios, [1.0, 1.0])
        assert res.wfe == pytest.approx(1.0)
        assert res.mean_is_sharpe == pytest.approx(3.0)
        assert res.mean_oos_sharpe == pytest.approx(3.0)
        assert res.frac_oos_positive == 1.0
        assert res.frac_oos_profitable == 1.0
        assert res.vetoed is False
        assert res.catastrophic_folds == []

    def test_oos_evaluated_with_is_best_params(self):
        def bt(params, data):
            # IS windows (len 80) reward high lb; OOS windows reward low lb.
            return params["lb"] if len(data) >= 80 else 4.0 - params["lb"]

        res = walk_forward_analysis(bt, RETURNS_200, {"lb": [1.0, 2.0, 3.0]}, n_folds=2)
        # IS picks lb=3 (score 3); its OOS score is 4-3=1, not the OOS-best 3.
        assert res.best_params_per_fold == [{"lb": 3.0}, {"lb": 3.0}]
        assert np.allclose(res.is_sharpes, [3.0, 3.0])
        assert np.allclose(res.oos_sharpes, [1.0, 1.0])
        assert np.allclose(res.degradation_ratios, [1.0 / 3.0, 1.0 / 3.0])
        assert res.wfe == pytest.approx(1.0 / 3.0)

    def test_catastrophic_veto_on_very_negative_oos(self):
        def bt(params, data):
            return 2.0 if len(data) >= 80 else -2.0

        res = walk_forward_analysis(bt, RETURNS_200, {"x": [1]}, n_folds=2)
        assert res.vetoed is True
        assert res.catastrophic_folds == [1, 2]
        assert res.wfe == pytest.approx(-1.0)
        assert res.frac_oos_positive == 0.0
        assert res.frac_oos_profitable == 0.0

    def test_catastrophic_veto_on_sign_flip(self):
        def bt(params, data):
            return 1.0 if len(data) >= 80 else -0.6

        res = walk_forward_analysis(bt, RETURNS_200, {"x": [1]}, n_folds=2)
        # OOS > -1.0, but IS > 0.5 with OOS < -0.5 still triggers the veto.
        assert res.vetoed is True
        assert res.catastrophic_folds == [1, 2]
        assert np.allclose(res.degradation_ratios, [-0.6, -0.6])

    def test_negative_is_sharpe_zeroes_ratios(self):
        def bt(params, data):
            return -1.0

        res = walk_forward_analysis(bt, RETURNS_200, {"x": [1]}, n_folds=2)
        assert np.allclose(res.is_sharpes, [-1.0, -1.0])
        assert np.allclose(res.degradation_ratios, [0.0, 0.0])
        assert res.wfe == 0.0  # undefined when mean IS is not positive
        assert res.vetoed is False  # -1.0 is not strictly below -1.0
        assert res.frac_oos_positive == 0.0

    def test_folds_skipped_when_oos_too_short(self):
        calls = []

        def bt(params, data):
            calls.append(len(data))
            return 1.0

        # fold_size=25, is_ratio=0.9 -> OOS has 3 points (<5): every fold skipped.
        res = walk_forward_analysis(bt, np.zeros(125), {"x": [1]}, n_folds=5, is_ratio=0.9)
        assert calls == []
        assert np.allclose(res.is_sharpes, 0.0)
        assert np.allclose(res.oos_sharpes, 0.0)
        assert res.best_params_per_fold == [{}] * 5
        assert res.wfe == 0.0
        assert res.frac_oos_positive == 0.0

    def test_folds_skipped_when_is_too_short(self):
        # fold_size=20, is_ratio=0.3 -> IS has 6 points (<10): every fold skipped.
        res = walk_forward_analysis(
            lambda p, d: 1.0, np.zeros(100), {"x": [1]}, n_folds=5, is_ratio=0.3
        )
        assert np.allclose(res.is_sharpes, 0.0)
        assert res.best_params_per_fold == [{}] * 5

    def test_purge_gap_shrinks_oos_window(self):
        lengths = []

        def bt(params, data):
            lengths.append(len(data))
            return 1.0

        res = walk_forward_analysis(bt, RETURNS_200, {"x": [1]}, n_folds=2, purge_gap=5)
        # Per fold: one IS call (len 80), one OOS call (len 20-5=15).
        assert lengths == [80, 15, 80, 15]
        assert res.purge_gap == 5

    def test_oversized_purge_gap_is_skipped(self):
        lengths = []

        def bt(params, data):
            lengths.append(len(data))
            return 1.0

        walk_forward_analysis(bt, RETURNS_200, {"x": [1]}, n_folds=2, purge_gap=50)
        # oos_start would pass fold_end, so purging is dropped: full 20-point OOS.
        assert lengths == [80, 20, 80, 20]

    def test_worse_candidate_does_not_replace_best(self):
        def bt(params, data):
            return params["x"]

        res = walk_forward_analysis(bt, RETURNS_200, {"x": [3.0, 1.0]}, n_folds=2)
        assert res.best_params_per_fold == [{"x": 3.0}, {"x": 3.0}]
        assert np.allclose(res.is_sharpes, [3.0, 3.0])

    def test_param_grid_cartesian_product(self):
        calls = []

        def bt(params, data):
            if len(data) >= 80:
                calls.append(params)
            return params["a"] + params["b"] / 100.0

        res = walk_forward_analysis(bt, RETURNS_200, {"a": [1, 2], "b": [10, 20]}, n_folds=2)
        assert len(calls) == 8  # 4 combos x 2 folds searched in-sample
        assert res.best_params_per_fold == [{"a": 2, "b": 20}, {"a": 2, "b": 20}]
        assert np.allclose(res.is_sharpes, [2.2, 2.2])


class TestWalkForwardResultsSummary:
    @pytest.mark.parametrize(
        ("wfe", "verdict"),
        [
            (0.9, "EXCELLENT"),
            (0.7, "EXCELLENT"),
            (0.6, "PASSING"),
            (0.5, "PASSING"),
            (0.4, "WEAK"),
            (0.3, "WEAK"),
            (0.1, "FAILING - likely overfit"),
            (-0.5, "FAILING - likely overfit"),
        ],
    )
    def test_verdict_thresholds(self, wfe, verdict):
        text = "\n".join(make_results(wfe=wfe).summary())
        assert f"[{verdict}]" in text

    def test_summary_core_fields(self):
        lines = make_results(wfe=0.5).summary()
        text = "\n".join(lines)
        assert "WALK-FORWARD ANALYSIS" in text
        assert "Folds                : 2" in text
        assert "Purge gap            : 3 periods" in text
        assert "Walk-Forward Eff.    : 0.5000" in text
        assert "Mean IS Sharpe       : 1.5000" in text
        assert "Mean OOS Sharpe      : 0.7500" in text
        assert "Folds OOS > 0        : 100.0%" in text

    def test_summary_has_one_row_per_fold(self):
        text = "\n".join(make_results().summary())
        assert "{'x': 1}" in text
        assert "{'x': 2}" in text

    def test_veto_line_only_when_vetoed(self):
        clean = "\n".join(make_results().summary())
        assert "CATASTROPHIC VETO" not in clean
        vetoed = "\n".join(make_results(vetoed=True, catastrophic=[2]).summary())
        assert "CATASTROPHIC VETO    : YES - folds [2]" in vetoed

    def test_non_finite_degradation_shown_as_na(self):
        results = make_results(degradation=np.array([np.inf, np.nan]))
        text = "\n".join(results.summary())
        assert "N/A" in text
        assert "inf" not in text

    def test_summary_from_real_analysis(self):
        res = walk_forward_analysis(lambda p, d: 1.0, RETURNS_200, {"x": [1]}, n_folds=2)
        text = "\n".join(res.summary())
        assert "[EXCELLENT]" in text
        assert "Folds                : 2" in text
