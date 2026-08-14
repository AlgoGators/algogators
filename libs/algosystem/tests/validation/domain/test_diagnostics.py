"""Tests for autocorrelation pre-test diagnostics."""

import sys

import numpy as np
from algosystem.validation.domain.statistics.diagnostics import (
    AutocorrelationDiagnostic,
    check_autocorrelation,
)


def white_noise(n=500, seed=0):
    return np.random.default_rng(seed).normal(0.0, 0.01, size=n)


def ar1_series(phi, n, seed):
    rng = np.random.default_rng(seed)
    eps = rng.normal(0.0, 0.01, size=n)
    out = np.empty(n)
    out[0] = eps[0]
    for t in range(1, n):
        out[t] = phi * out[t - 1] + eps[t]
    return out


class TestCheckAutocorrelation:
    def test_white_noise_recommends_complete_shuffle(self):
        diag = check_autocorrelation(white_noise())
        assert isinstance(diag, AutocorrelationDiagnostic)
        assert not diag.has_autocorrelation
        assert diag.recommended_shuffle == "complete"
        assert diag.warning_message == ""
        assert diag.ljung_box_pvalue > 0.05

    def test_strong_ar1_recommends_block_shuffle(self):
        diag = check_autocorrelation(ar1_series(phi=0.6, n=1000, seed=3))
        assert diag.has_autocorrelation
        assert diag.acf_1 > 0.1
        assert diag.recommended_shuffle == "block"
        assert "block" in diag.warning_message
        assert "Significant autocorrelation" in diag.warning_message
        assert diag.ljung_box_pvalue < 0.05

    def test_mild_ar1_recommends_cyclic_shuffle(self):
        diag = check_autocorrelation(ar1_series(phi=0.06, n=5000, seed=0))
        assert diag.has_autocorrelation
        assert abs(diag.acf_1) <= 0.1
        assert diag.recommended_shuffle == "cyclic"
        assert "Mild autocorrelation" in diag.warning_message
        assert "cyclic" in diag.warning_message

    def test_acf_values_shape_and_first_lag(self):
        diag = check_autocorrelation(white_noise(), max_lag=7)
        assert diag.acf_values.shape == (7,)
        assert diag.acf_1 == diag.acf_values[0]
        # ACF magnitudes are bounded by 1 for a stationary series.
        assert np.all(np.abs(diag.acf_values) <= 1.0)

    def test_strong_ar1_acf_estimates_decay(self):
        diag = check_autocorrelation(ar1_series(phi=0.6, n=5000, seed=3), max_lag=3)
        # Theoretical ACF of AR(1) is phi**lag; estimates should be close.
        assert diag.acf_values[0] > diag.acf_values[1] > diag.acf_values[2]
        assert abs(diag.acf_values[0] - 0.6) < 0.1

    def test_series_shorter_than_max_lag_pads_zero(self):
        diag = check_autocorrelation(np.array([0.01, -0.02, 0.005, 0.007, -0.01]), max_lag=10)
        assert diag.acf_values.shape == (10,)
        # Lags >= n are set to exactly 0 and excluded from the Q statistic.
        assert np.all(diag.acf_values[4:] == 0.0)
        assert np.isfinite(diag.ljung_box_stat)

    def test_fields_are_python_floats(self):
        diag = check_autocorrelation(white_noise())
        assert isinstance(diag.acf_1, float)
        assert isinstance(diag.ljung_box_stat, float)
        assert isinstance(diag.ljung_box_pvalue, float)

    def test_significance_level_controls_detection(self):
        # With significance ~1.0, any p-value counts as significant.
        diag = check_autocorrelation(white_noise(), significance=1.0)
        assert diag.has_autocorrelation
        assert diag.recommended_shuffle in ("cyclic", "block")

    def test_fallback_pvalue_without_scipy(self, monkeypatch):
        # Poison the scipy.stats entry so `from scipy.stats import chi2`
        # raises ImportError and the crude normal approximation is used.
        monkeypatch.setitem(sys.modules, "scipy.stats", None)
        diag = check_autocorrelation(white_noise())
        assert 0.0 <= diag.ljung_box_pvalue <= 1.0
        assert not diag.has_autocorrelation

    def test_fallback_pvalue_flags_strong_autocorrelation(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "scipy.stats", None)
        diag = check_autocorrelation(ar1_series(phi=0.6, n=1000, seed=3))
        assert diag.ljung_box_pvalue < 0.05
        assert diag.recommended_shuffle == "block"
