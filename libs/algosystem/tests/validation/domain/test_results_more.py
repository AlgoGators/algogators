"""Report, analytical-DSR, MinTRL, and surface-analysis tests for OverfitResults."""

import sys

import numpy as np
import pytest
from algosystem.shared.errors import ValidationError
from algosystem.validation.domain.results import OverfitResults

LOOKBACKS = [5, 10, 20]
THRESHOLDS = [0.0, 0.1, 0.2]

SEPARATOR = "-" * 72


def make_results(sharpes, best_index, returns=None, param_list=None, n_reps=64):
    """Build an OverfitResults directly, without running a detector."""
    if param_list is None:
        param_list = [{"lookback": lb, "threshold": th} for lb in LOOKBACKS for th in THRESHOLDS]
    sharpe_array = np.asarray(sharpes, dtype=float)
    n = len(param_list)
    assert len(sharpe_array) == n
    return OverfitResults(
        param_list=param_list,
        n_params=n,
        n_reps=n_reps,
        shuffle_method="sign_flip",
        original_sharpes=sharpe_array,
        best_param_index=best_index,
        best_sharpe=float(sharpe_array[best_index]),
        solo_pvalues=np.linspace(0.01, 0.9, n),
        unbiased_pvalue=0.05,
        unbiased_pvalues=np.linspace(0.02, 0.6, n),
        null_best_sharpes=np.linspace(-1.0, 1.0, 16),
        prob_overfit=0.25,
        deflated_sharpe=0.8,
        sort_indices=np.argsort(-sharpe_array),
        returns=returns,
    )


VARIED_SHARPES = [0.2, 0.4, 0.1, 0.5, 1.8, 0.6, 0.3, 0.7, 0.2]


@pytest.fixture
def normal_returns():
    return np.random.default_rng(7).normal(0.0005, 0.01, size=400)


class TestSummary:
    def test_structure_without_returns(self):
        res = make_results(VARIED_SHARPES, best_index=4)
        lines = res.summary()
        assert all(isinstance(line, str) for line in lines)
        joined = "\n".join(lines)
        assert "PERMUTATION-BASED OVERFITTING DETECTION REPORT" in joined
        assert "Analytical DSR" not in joined
        assert f"{res.best_sharpe:.4f}" in joined
        assert str(res.param_list[4]) in joined
        assert "Parameter combinations tested : 9" in joined

    def test_row_count_matches_param_count(self):
        res = make_results(VARIED_SHARPES, best_index=4)
        lines = res.summary()
        separators = [i for i, line in enumerate(lines) if line == SEPARATOR]
        assert len(separators) == 3
        assert separators[2] - separators[1] - 1 == res.n_params

    def test_with_returns_includes_analytical_block(self, normal_returns):
        res = make_results(VARIED_SHARPES, best_index=4, returns=normal_returns)
        joined = "\n".join(res.summary())
        assert "Analytical DSR (Bailey/LdP)" in joined
        assert "SR0 (haircut threshold)" in joined
        assert "Min track record length" in joined
        assert "Returns skewness" in joined
        assert "Returns kurtosis" in joined


class TestAnalyticalDeflatedSharpe:
    def test_requires_returns(self):
        res = make_results(VARIED_SHARPES, best_index=4)
        with pytest.raises(ValidationError):
            res.analytical_deflated_sharpe()

    def test_keys_and_ranges(self, normal_returns):
        res = make_results(VARIED_SHARPES, best_index=4, returns=normal_returns)
        adsr = res.analytical_deflated_sharpe()
        assert set(adsr) == {"dsr", "sr0", "min_trl", "skewness", "kurtosis"}
        assert 0.0 <= adsr["dsr"] <= 1.0
        assert np.isfinite(adsr["sr0"])
        assert adsr["min_trl"] > 1.0
        assert abs(adsr["skewness"]) < 0.5
        assert 2.0 < adsr["kurtosis"] < 4.5

    def test_explicit_returns_argument_matches_stored(self, normal_returns):
        stored = make_results(VARIED_SHARPES, best_index=4, returns=normal_returns)
        bare = make_results(VARIED_SHARPES, best_index=4)
        expected = stored.analytical_deflated_sharpe()
        actual = bare.analytical_deflated_sharpe(returns=normal_returns)
        for key, value in expected.items():
            assert actual[key] == pytest.approx(value)

    def test_scipy_fallback_approximates_scipy(self, monkeypatch, normal_returns):
        res = make_results(VARIED_SHARPES, best_index=4, returns=normal_returns)
        with_scipy = res.analytical_deflated_sharpe()

        monkeypatch.setitem(sys.modules, "scipy.stats", None)
        without_scipy = res.analytical_deflated_sharpe()

        assert without_scipy["dsr"] == pytest.approx(with_scipy["dsr"], abs=0.02)
        assert without_scipy["sr0"] == pytest.approx(with_scipy["sr0"], abs=0.02)
        assert without_scipy["min_trl"] == pytest.approx(with_scipy["min_trl"], rel=0.05)

    def test_constant_returns_hit_zero_variance_branch(self):
        res = make_results(VARIED_SHARPES, best_index=4, returns=np.full(60, 0.001))
        adsr = res.analytical_deflated_sharpe()
        assert adsr["skewness"] == 0.0
        assert adsr["kurtosis"] == 3.0
        assert 0.0 <= adsr["dsr"] <= 1.0

    def test_two_param_grid_uses_raw_trial_count(self, normal_returns):
        param_list = [{"x": 1}, {"x": 2}]
        res = make_results([0.5, 1.0], best_index=1, returns=normal_returns, param_list=param_list)
        adsr = res.analytical_deflated_sharpe()
        assert 0.0 <= adsr["dsr"] <= 1.0
        assert np.isfinite(adsr["sr0"])

    def test_very_short_returns_series(self):
        res = make_results(VARIED_SHARPES, best_index=4, returns=np.array([0.01, -0.02]))
        adsr = res.analytical_deflated_sharpe()
        assert 0.0 <= adsr["dsr"] <= 1.0


class TestMinTrackRecordLength:
    def test_infinite_when_sharpe_equals_threshold(self):
        res = make_results(VARIED_SHARPES, best_index=4)
        min_trl = res.min_track_record_length(_sr0=res.best_sharpe, _gamma3=0.0, _gamma4=3.0)
        assert min_trl == float("inf")

    def test_requires_returns_when_gammas_missing(self):
        res = make_results(VARIED_SHARPES, best_index=4)
        with pytest.raises(ValidationError):
            res.min_track_record_length()

    def test_computes_from_stored_returns(self, normal_returns):
        res = make_results(VARIED_SHARPES, best_index=4, returns=normal_returns)
        min_trl = res.min_track_record_length()
        assert np.isfinite(min_trl)
        assert min_trl > 1.0

    def test_internal_sr0_with_explicit_gammas(self):
        res = make_results(VARIED_SHARPES, best_index=4)
        min_trl = res.min_track_record_length(_gamma3=0.0, _gamma4=3.0)
        assert min_trl > 1.0

    def test_constant_returns_gamma_defaults(self):
        res = make_results(VARIED_SHARPES, best_index=4, returns=np.full(50, 0.002))
        min_trl = res.min_track_record_length()
        assert min_trl > 1.0

    def test_fallback_phi_inv_approximates_scipy(self, monkeypatch):
        res = make_results(VARIED_SHARPES, best_index=4)
        with_scipy = res.min_track_record_length(_gamma3=0.0, _gamma4=3.0)
        monkeypatch.setitem(sys.modules, "scipy.stats", None)
        without_scipy = res.min_track_record_length(_gamma3=0.0, _gamma4=3.0)
        assert without_scipy == pytest.approx(with_scipy, rel=0.05)


class TestSurfaceAnalysis:
    def test_flat_plateau(self):
        res = make_results(np.ones(9), best_index=4)
        sa = res.surface_analysis()
        assert sa["robustness_ratio"] == pytest.approx(1.0)
        assert sa["frac_positive"] == pytest.approx(1.0)
        assert sa["frac_above_half"] == pytest.approx(1.0)
        assert sa["plateau_score"] == pytest.approx(1.0)
        assert sa["cv_neighbors"] == pytest.approx(0.0)
        assert sa["peak_to_neighbor"] == pytest.approx(1.0)
        for stats in sa["per_param_sensitivity"].values():
            assert stats["sobol_first"] == pytest.approx(0.0)
            assert stats["marginal_range"] == pytest.approx(0.0)

    def test_isolated_spike(self):
        sharpes = np.zeros(9)
        sharpes[0] = 2.0
        res = make_results(sharpes, best_index=0)
        sa = res.surface_analysis()
        assert sa["robustness_ratio"] == pytest.approx(0.0)
        assert sa["peak_to_neighbor"] == float("inf")
        assert sa["cv_neighbors"] == float("inf")
        assert sa["frac_positive"] == pytest.approx(1 / 9)
        assert sa["frac_above_half"] == pytest.approx(1 / 9)
        assert sa["plateau_score"] == pytest.approx(1 / 9)
        for stats in sa["per_param_sensitivity"].values():
            assert stats["sobol_first"] == pytest.approx(0.25)
            assert stats["marginal_range"] == pytest.approx(2 / 3)

    def test_negative_best_zeroes_fraction_metrics(self):
        res = make_results(np.full(9, -0.5), best_index=4)
        sa = res.surface_analysis()
        assert sa["frac_positive"] == pytest.approx(0.0)
        assert sa["frac_above_half"] == pytest.approx(0.0)
        assert sa["plateau_score"] == pytest.approx(0.0)
        assert sa["robustness_ratio"] == pytest.approx(1.0)

    def test_single_combo_has_no_neighbors(self):
        res = make_results([1.0], best_index=0, param_list=[{"x": 1.0}])
        sa = res.surface_analysis()
        assert sa["robustness_ratio"] == pytest.approx(1.0)
        assert sa["peak_to_neighbor"] == pytest.approx(1.0)
        assert sa["cv_neighbors"] == pytest.approx(0.0)
        assert sa["per_param_sensitivity"]["x"]["sobol_first"] == pytest.approx(0.0)

    def test_per_param_structure(self):
        res = make_results(VARIED_SHARPES, best_index=4)
        sa = res.surface_analysis()
        per_param = sa["per_param_sensitivity"]
        assert set(per_param) == {"lookback", "threshold"}
        for key, values in (("lookback", LOOKBACKS), ("threshold", THRESHOLDS)):
            assert per_param[key]["values"] == [float(v) for v in values]
            assert len(per_param[key]["conditional_means"]) == len(values)
            assert per_param[key]["sobol_first"] >= 0.0


class TestSurfaceSummary:
    def test_plateau_report(self):
        res = make_results(np.ones(9), best_index=4)
        lines = res.surface_summary()
        joined = "\n".join(lines)
        assert lines[0] == "=" * 72
        assert lines[-1] == "=" * 72
        assert "PARAMETER SURFACE ANALYSIS" in joined
        assert "Robustness ratio" in joined
        assert "GOOD" in joined
        assert "lookback" in joined
        assert "threshold" in joined

    def test_spike_report_flags_bad(self):
        sharpes = np.zeros(9)
        sharpes[0] = 2.0
        res = make_results(sharpes, best_index=0)
        joined = "\n".join(res.surface_summary())
        assert "BAD" in joined
