"""Tests for Probabilistic / Deflated Sharpe Ratio statistics (psr_dsr.py)."""

from __future__ import annotations

import importlib.util
import math
import sys

import algosystem.validation.domain.statistics.psr_dsr as psr_dsr
import numpy as np
import pytest
from algosystem.validation.domain.statistics.psr_dsr import (
    ReturnStats,
    TrialTracker,
    batch_deflated_sharpe,
    compute_return_stats,
    deflated_sharpe_ratio,
    probabilistic_sharpe_ratio,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def rng():
    return np.random.default_rng(7)


@pytest.fixture
def trend_returns(rng):
    """1000 days of positive-drift returns (annualized Sharpe ~1.4)."""
    return rng.normal(0.001, 0.01, 1000)


@pytest.fixture
def noise_returns(rng):
    """1000 days of zero-drift noise (this seed draws a negative sample mean)."""
    rng.normal(0.001, 0.01, 1000)  # burn the trend draw so seeds stay aligned
    return rng.normal(0.0, 0.01, 1000)


def _degenerate_stats(sharpe: float, skew: float, kurt: float, n_eff: int) -> ReturnStats:
    return ReturnStats(
        n_obs=n_eff,
        mean=0.0,
        std=1.0,
        sharpe=sharpe,
        skewness=skew,
        kurtosis=kurt,
        acf1=0.0,
        n_effective=n_eff,
    )


# ---------------------------------------------------------------------------
# compute_return_stats
# ---------------------------------------------------------------------------


class TestComputeReturnStats:
    def test_matches_numpy_moments(self, trend_returns):
        stats = compute_return_stats(trend_returns)

        assert stats.n_obs == 1000
        assert stats.mean == pytest.approx(float(np.mean(trend_returns)))
        assert stats.std == pytest.approx(float(np.std(trend_returns, ddof=1)))
        expected_sharpe = stats.mean / stats.std * math.sqrt(252.0)
        assert stats.sharpe == pytest.approx(expected_sharpe)
        # Gaussian draws: skew near 0, raw kurtosis near 3
        assert abs(stats.skewness) < 0.5
        assert abs(stats.kurtosis - 3.0) < 0.5
        assert -0.99 <= stats.acf1 <= 0.99
        assert 3 <= stats.n_effective <= stats.n_obs

    def test_custom_annualization_factor(self, trend_returns):
        daily = compute_return_stats(trend_returns, annualize=252.0)
        monthly = compute_return_stats(trend_returns, annualize=12.0)

        assert monthly.sharpe == pytest.approx(daily.sharpe * math.sqrt(12.0 / 252.0))

    def test_zero_variance_returns_degenerate_stats(self):
        stats = compute_return_stats(np.full(100, 0.01))

        assert stats.n_obs == 100
        assert stats.mean == pytest.approx(0.01)
        assert stats.std == pytest.approx(0.0, abs=1e-15)
        assert stats.sharpe == 0.0
        assert stats.skewness == 0.0
        assert stats.kurtosis == 3.0
        assert stats.acf1 == 0.0
        assert stats.n_effective == 100

    def test_two_element_series_floors_effective_sample_at_three(self):
        stats = compute_return_stats(np.array([0.01, -0.02]))

        assert stats.n_obs == 2
        assert stats.acf1 == 0.0  # acf requires n > 2
        assert stats.n_effective == 3  # floor of 3 exceeds n_obs here

    def test_positive_autocorrelation_shrinks_effective_sample(self, rng):
        ar = np.empty(500)
        ar[0] = 0.0
        eps = rng.normal(0, 0.01, 500)
        for i in range(1, 500):
            ar[i] = 0.9 * ar[i - 1] + eps[i]

        stats = compute_return_stats(ar)

        assert stats.acf1 > 0.5
        assert stats.n_effective < stats.n_obs // 2

    def test_negative_autocorrelation_is_clipped_and_capped_at_n(self, rng):
        alternating = np.array([0.01, -0.01] * 100) + rng.normal(0, 1e-4, 200)

        stats = compute_return_stats(alternating)

        assert stats.acf1 == pytest.approx(-0.99)  # clipped at -0.99
        assert stats.n_effective == 200  # capped at n despite (1-acf)/(1+acf) > 1


# ---------------------------------------------------------------------------
# probabilistic_sharpe_ratio
# ---------------------------------------------------------------------------


class TestProbabilisticSharpeRatio:
    def test_strong_positive_drift_is_significant(self, trend_returns):
        result = probabilistic_sharpe_ratio(trend_returns)

        assert result.psr == pytest.approx(1.0, abs=1e-9)
        assert result.is_significant
        assert result.sr0 == 0.0
        assert result.test_statistic > 10.0
        assert result.n_obs == 1000
        assert 3 <= result.n_effective <= 1000
        assert result.threshold == 0.95

    def test_negative_sample_sharpe_is_not_significant(self, noise_returns):
        result = probabilistic_sharpe_ratio(noise_returns)

        assert result.sr_hat < 0.0
        assert result.psr < 0.5
        assert not result.is_significant
        assert result.test_statistic < 0.0

    def test_raising_benchmark_lowers_psr(self, trend_returns):
        low = probabilistic_sharpe_ratio(trend_returns, sr0=0.0)
        high = probabilistic_sharpe_ratio(trend_returns, sr0=3.0)

        assert high.psr < low.psr
        assert high.test_statistic < low.test_statistic

    def test_zero_variance_returns_give_psr_of_half(self):
        result = probabilistic_sharpe_ratio(np.full(100, 0.01))

        assert result.sr_hat == 0.0
        assert result.test_statistic == 0.0
        assert result.psr == pytest.approx(0.5)
        assert not result.is_significant

    def test_zero_variance_with_positive_benchmark(self):
        result = probabilistic_sharpe_ratio(np.full(100, 0.01), sr0=1.0)

        # sharpe=0, skew=0, kurt=3 -> denom=1, z = -sr0*sqrt(n_eff-1)
        assert result.test_statistic == pytest.approx(-math.sqrt(99))
        assert result.psr < 1e-6

    def test_short_two_element_series_runs(self):
        result = probabilistic_sharpe_ratio(np.array([0.01, -0.02]))

        assert result.n_obs == 2
        assert result.n_effective == 3
        assert 0.0 <= result.psr <= 1.0

    def test_nonpositive_variance_denominator_is_clamped(self, monkeypatch):
        # skew=10, kurt=1, sharpe=1 -> denom_sq = 1 - 10 = -9 -> clamped to 1e-8
        fake = _degenerate_stats(sharpe=1.0, skew=10.0, kurt=1.0, n_eff=50)
        monkeypatch.setattr(psr_dsr, "compute_return_stats", lambda *a, **k: fake)

        result = probabilistic_sharpe_ratio(np.zeros(50))

        assert result.test_statistic == pytest.approx(1.0 * math.sqrt(49) / 1e-4)
        assert result.psr == pytest.approx(1.0)

    def test_custom_threshold_controls_significance(self, noise_returns):
        # Make a mildly positive series whose PSR sits strictly between 0 and 1
        result = probabilistic_sharpe_ratio(noise_returns, threshold=1e-200)

        assert result.threshold == 1e-200
        assert result.is_significant == (result.psr >= 1e-200)

    def test_summary_reports_status_lines(self, trend_returns, noise_returns):
        sig = probabilistic_sharpe_ratio(trend_returns).summary()
        not_sig = probabilistic_sharpe_ratio(noise_returns).summary()

        assert sig[0] == "Probabilistic Sharpe Ratio [SIGNIFICANT]"
        assert not_sig[0] == "Probabilistic Sharpe Ratio [NOT SIGNIFICANT]"
        assert any("Observations    : 1000" in line for line in sig)
        assert len(sig) == 8


# ---------------------------------------------------------------------------
# deflated_sharpe_ratio
# ---------------------------------------------------------------------------


class TestDeflatedSharpeRatio:
    def test_independent_trials_variance_from_sharpe_standard_error(self, trend_returns):
        result = deflated_sharpe_ratio(trend_returns, n_trials=2)

        expected_var = (1.0 + 0.5 * result.sr_hat**2) / result.n_effective
        assert result.var_sr == pytest.approx(expected_var)
        assert result.n_trials == 2
        assert result.n_trials_effective == 2
        assert result.sr0_star > 0.0
        assert 0.0 <= result.dsr <= 1.0

    def test_more_trials_raises_haircut_and_lowers_dsr(self, trend_returns):
        few = deflated_sharpe_ratio(trend_returns, n_trials=2)
        many = deflated_sharpe_ratio(trend_returns, n_trials=100)

        assert many.sr0_star > few.sr0_star
        assert many.dsr <= few.dsr
        assert many.n_trials_effective == 100

    def test_n_trials_floored_at_two(self, trend_returns):
        result = deflated_sharpe_ratio(trend_returns, n_trials=1)

        assert result.n_trials == 2
        assert result.n_trials_effective == 2

    def test_all_sharpes_sets_cross_sectional_variance(self, trend_returns):
        all_sharpes = np.array([0.2, 0.5, 1.5, 0.9, -0.3])

        result = deflated_sharpe_ratio(trend_returns, n_trials=5, all_sharpes=all_sharpes)

        assert result.var_sr == pytest.approx(float(np.var(all_sharpes, ddof=1)))
        assert 2 <= result.n_trials_effective <= 5

    def test_identical_sharpes_imply_full_correlation(self, trend_returns):
        same = np.array([1.0, 1.0, 1.0, 1.0])

        result = deflated_sharpe_ratio(trend_returns, n_trials=4, all_sharpes=same)

        assert result.var_sr == 0.0
        assert result.sr0_star == 0.0  # zero variance -> no haircut
        assert result.n_trials_effective == 2  # rho capped at 0.95 collapses N_eff

    def test_sr_hat_override_is_used(self, trend_returns):
        result = deflated_sharpe_ratio(trend_returns, n_trials=5, sr_hat=0.123)

        assert result.sr_hat == 0.123

    def test_min_track_record_finite_when_sharpe_beats_haircut(self, trend_returns):
        result = deflated_sharpe_ratio(trend_returns, n_trials=2)

        assert result.sr_hat > result.sr0_star
        assert 1.0 < result.min_track_record < float("inf")

    def test_min_track_record_infinite_when_sharpe_equals_haircut(self, trend_returns):
        all_sharpes = np.array([0.2, 0.5, 1.5, 0.9, -0.3])
        first = deflated_sharpe_ratio(trend_returns, n_trials=5, all_sharpes=all_sharpes)

        pinned = deflated_sharpe_ratio(
            trend_returns, n_trials=5, all_sharpes=all_sharpes, sr_hat=first.sr0_star
        )

        assert pinned.min_track_record == float("inf")
        assert pinned.dsr == pytest.approx(0.5)

    def test_zero_variance_returns_are_never_significant(self):
        result = deflated_sharpe_ratio(np.full(50, 0.02), n_trials=10)

        assert result.sr_hat == 0.0
        assert result.sr0_star > 0.0
        assert result.dsr < 0.5
        assert not result.is_significant

    def test_nonpositive_variance_denominator_is_clamped(self, monkeypatch):
        fake = _degenerate_stats(sharpe=1.0, skew=10.0, kurt=1.0, n_eff=50)
        monkeypatch.setattr(psr_dsr, "compute_return_stats", lambda *a, **k: fake)

        result = deflated_sharpe_ratio(np.zeros(50), n_trials=3)

        assert result.test_statistic > 1000.0
        assert result.dsr == pytest.approx(1.0)

    def test_summary_reports_status_lines(self, trend_returns, noise_returns):
        sig = deflated_sharpe_ratio(trend_returns, n_trials=2).summary()
        hacked = deflated_sharpe_ratio(
            noise_returns,
            n_trials=100,
            all_sharpes=np.array([0.5, 1.0, 1.5, 2.0]),
            sr_hat=0.05,
        ).summary()

        assert sig[0] == "Deflated Sharpe Ratio [SIGNIFICANT]"
        assert hacked[0] == "Deflated Sharpe Ratio [LIKELY P-HACKED]"
        assert any("Trials tested" in line for line in sig)
        assert len(sig) == 9


# ---------------------------------------------------------------------------
# batch_deflated_sharpe
# ---------------------------------------------------------------------------


class TestBatchDeflatedSharpe:
    @pytest.fixture
    def batch(self, rng):
        returns_list = [
            rng.normal(0.0015, 0.01, 500),  # strong strategy
            rng.normal(0.0, 0.01, 500),  # noise
            rng.normal(-0.0005, 0.01, 500),  # loser
        ]
        return returns_list, batch_deflated_sharpe(returns_list)

    def test_structure_and_best_selection(self, batch):
        _, result = batch

        assert result.n_strategies == 3
        assert len(result.results) == 3
        dsrs = [r.dsr for r in result.results]
        assert result.best_strategy_idx == int(np.argmax(dsrs))
        assert result.best_dsr == dsrs[result.best_strategy_idx]
        assert result.n_significant == sum(1 for r in result.results if r.is_significant)

    def test_each_result_uses_own_sharpe_and_shared_trial_count(self, batch):
        returns_list, result = batch

        for ret, res in zip(returns_list, result.results, strict=True):
            assert res.sr_hat == pytest.approx(compute_return_stats(ret).sharpe)
            assert res.n_trials == 3

    def test_summary_table_keys_and_values(self, batch):
        _, result = batch

        assert len(result.summary_table) == 3
        row = result.summary_table[0]
        assert set(row) == {"strategy", "sharpe", "dsr", "significant"}
        assert row["strategy"] == 0
        assert row["sharpe"] == pytest.approx(result.results[0].sr_hat)

    def test_summary_lines_mark_best_strategy(self, batch):
        _, result = batch

        lines = result.summary()
        assert "BATCH DEFLATED SHARPE RATIO (3 strategies)" in lines[1]
        best_lines = [ln for ln in lines if "<-- best" in ln]
        assert len(best_lines) == 1
        assert best_lines[0].strip().startswith(str(result.best_strategy_idx))

    def test_infinite_min_trl_rendered_as_inf(self):
        # Constant-return strategies: sr_hat = 0 for all, identical sharpes
        returns_list = [np.full(50, 0.01), np.full(50, 0.02), np.full(50, 0.03)]

        result = batch_deflated_sharpe(returns_list)

        # var_sr = 0 -> sr0_star = 0 = sr_hat -> MinTRL inf for every strategy
        assert all(r.min_track_record == float("inf") for r in result.results)
        assert any(ln.rstrip().endswith("inf") or "inf <-- best" in ln for ln in result.summary())


# ---------------------------------------------------------------------------
# TrialTracker
# ---------------------------------------------------------------------------


class TestTrialTracker:
    def test_empty_tracker_state(self):
        tracker = TrialTracker()

        assert tracker.n_trials == 0
        assert tracker.all_sharpes.tolist() == [0.0]
        assert tracker.current_sr0_star() == 0.0
        assert tracker.survivors() == []
        lines = tracker.summary()
        assert "TRIAL TRACKER (0 trials recorded)" in lines[1]
        assert lines[-1] == "  No trials recorded yet."

    def test_first_trial_recorded(self, noise_returns):
        tracker = TrialTracker()

        record = tracker.record_trial(noise_returns, 0.3, {"lookback": 5}, "noise")

        assert record.trial_id == 1
        assert record.sharpe == 0.3
        assert record.params == {"lookback": 5}
        assert record.strategy_name == "noise"
        assert 0.0 <= record.psr <= 1.0
        assert 0.0 <= record.dsr <= 1.0
        assert tracker.n_trials == 1
        assert tracker.all_sharpes.tolist() == [0.3]
        assert tracker.current_sr0_star() == 0.0  # needs >= 2 trials
        # With fewer than 2 trials, every trial "survives" by definition
        assert tracker.survivors() == [record]

    def test_accumulating_trials_raises_threshold(self, trend_returns, noise_returns):
        tracker = TrialTracker()
        tracker.record_trial(noise_returns, 0.3, {"a": 1}, "noise")
        tracker.record_trial(trend_returns, 1.6, {"a": 2}, "trend")
        tracker.record_trial(noise_returns * 0.5, -0.2, {"a": 3}, "noise2")

        assert tracker.n_trials == 3
        sr0_star = tracker.current_sr0_star()
        assert sr0_star > 0.0
        expected = np.std([0.3, 1.6, -0.2], ddof=1)  # sanity: scales with sharpe spread
        assert sr0_star < 3.0 * float(expected)

    def test_survivors_only_keep_sharpe_above_haircut(self, trend_returns, noise_returns):
        tracker = TrialTracker()
        tracker.record_trial(noise_returns, 0.3, {"a": 1}, "noise")
        tracker.record_trial(trend_returns, 1.6, {"a": 2}, "trend")
        tracker.record_trial(noise_returns * 0.5, -0.2, {"a": 3}, "noise2")

        survivor_ids = [t.trial_id for t in tracker.survivors()]
        assert survivor_ids == [2]  # only the genuine trend strategy survives
        # A looser threshold still cannot rescue sub-haircut trials with huge |z|
        assert [t.trial_id for t in tracker.survivors(threshold=0.01)] == [2]

    def test_survivors_skips_trials_with_missing_stats(self, trend_returns, noise_returns):
        tracker = TrialTracker()
        tracker.record_trial(noise_returns, 0.3, {"a": 1}, "noise")
        tracker.record_trial(trend_returns, 1.6, {"a": 2}, "trend")

        tracker._return_stats.pop(2)

        assert [t.trial_id for t in tracker.survivors()] == []

    def test_summary_with_trials(self, trend_returns, noise_returns):
        tracker = TrialTracker()
        tracker.record_trial(noise_returns, 0.3, {"a": 1}, "noise")
        tracker.record_trial(trend_returns, 1.6, {"a": 2}, "trend")

        lines = tracker.summary()
        text = "\n".join(lines)
        assert "TRIAL TRACKER (2 trials recorded)" in text
        assert "Best observed Sharpe   : 1.6000 (trial #2)" in text
        assert "trend" in text
        assert "noise" in text

    def test_custom_base_threshold_and_annualize(self, trend_returns):
        tracker = TrialTracker(annualize=12.0, base_threshold=0.5)

        record = tracker.record_trial(trend_returns, 1.0, {}, "s")

        assert tracker.annualize == 12.0
        assert tracker.base_threshold == 0.5
        assert record.significant_at_time == (record.dsr >= 0.5)


# ---------------------------------------------------------------------------
# Normal CDF / inverse CDF fallback (no scipy)
# ---------------------------------------------------------------------------


@pytest.fixture
def noscipy_module(monkeypatch):
    """Load psr_dsr in a fresh namespace with scipy imports blocked."""
    monkeypatch.setitem(sys.modules, "scipy", None)
    monkeypatch.setitem(sys.modules, "scipy.stats", None)
    spec = importlib.util.spec_from_file_location("psr_dsr_noscipy", psr_dsr.__file__)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "psr_dsr_noscipy", module)  # dataclasses needs this
    spec.loader.exec_module(module)
    return module


class TestNormalCdfFallback:
    def test_phi_known_values(self, noscipy_module):
        phi = noscipy_module._phi
        assert phi(0.0) == pytest.approx(0.5)
        assert phi(1.96) == pytest.approx(0.975, abs=1e-3)
        assert phi(-1.96) == pytest.approx(0.025, abs=1e-3)
        assert phi(8.0) == pytest.approx(1.0)

    def test_phi_inv_boundaries(self, noscipy_module):
        phi_inv = noscipy_module._phi_inv
        assert phi_inv(0.0) == -float("inf")
        assert phi_inv(-0.5) == -float("inf")
        assert phi_inv(1.0) == float("inf")
        assert phi_inv(1.5) == float("inf")

    def test_phi_inv_known_values_and_symmetry(self, noscipy_module):
        phi_inv = noscipy_module._phi_inv
        assert phi_inv(0.5) == pytest.approx(0.0, abs=1e-3)
        assert phi_inv(0.975) == pytest.approx(1.959964, abs=1e-3)
        assert phi_inv(0.9) == pytest.approx(-phi_inv(0.1), abs=1e-9)

    def test_phi_round_trip(self, noscipy_module):
        for p in (0.05, 0.25, 0.5, 0.75, 0.95):
            assert noscipy_module._phi(noscipy_module._phi_inv(p)) == pytest.approx(p, abs=2e-3)

    def test_fallback_psr_matches_scipy_version(self, noscipy_module, rng):
        returns = rng.normal(0.0005, 0.01, 400)

        fallback = noscipy_module.probabilistic_sharpe_ratio(returns)
        primary = probabilistic_sharpe_ratio(returns)

        assert fallback.psr == pytest.approx(primary.psr, abs=1e-3)
        assert fallback.test_statistic == pytest.approx(primary.test_statistic)


class TestModuleLevelNormalFunctions:
    def test_phi_and_phi_inv_are_consistent(self):
        assert float(psr_dsr._phi(0.0)) == pytest.approx(0.5)
        assert float(psr_dsr._phi(psr_dsr._phi_inv(0.9))) == pytest.approx(0.9, abs=1e-3)
