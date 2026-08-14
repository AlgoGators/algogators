"""
Tests for the robustness statistics toolkit:

- bootstrap_sharpe_ci: bootstrap CI on the annualized Sharpe ratio
- monte_carlo_trades: trade-sequence reshuffling for drawdown luck
- alpha_beta_decomposition: OLS alpha/beta vs a benchmark
- kelly_criterion: bet sizing and risk of ruin from trade PnLs
- regime_conditional_performance: Sharpe split by volatility regime

All inputs are small synthetic numpy arrays with fixed seeds.
"""

import numpy as np
import pytest
from algosystem.validation.domain.statistics.robustness import (
    alpha_beta_decomposition,
    bootstrap_sharpe_ci,
    kelly_criterion,
    monte_carlo_trades,
    regime_conditional_performance,
)

# -----------------------------------------------------------------------
# bootstrap_sharpe_ci
# -----------------------------------------------------------------------


class TestBootstrapSharpeCI:
    def test_point_estimate_matches_manual_sharpe(self):
        rng = np.random.default_rng(0)
        returns = rng.normal(0.001, 0.01, 250)
        result = bootstrap_sharpe_ci(returns, n_bootstrap=200, seed=1)
        expected = np.mean(returns) / np.std(returns, ddof=1) * np.sqrt(252.0)
        assert result.point_estimate == pytest.approx(expected)

    def test_deterministic_given_seed(self):
        rng = np.random.default_rng(0)
        returns = rng.normal(0.001, 0.01, 100)
        a = bootstrap_sharpe_ci(returns, n_bootstrap=300, seed=7)
        b = bootstrap_sharpe_ci(returns, n_bootstrap=300, seed=7)
        assert a == b

    def test_strong_signal_excludes_zero(self):
        rng = np.random.default_rng(0)
        returns = rng.normal(0.005, 0.002, 300)
        result = bootstrap_sharpe_ci(returns, n_bootstrap=500, seed=1)
        assert result.ci_lower > 0
        assert not result.ci_includes_zero
        assert result.ci_lower <= result.point_estimate <= result.ci_upper
        assert result.n_bootstrap == 500
        assert result.confidence == 0.95

    def test_pure_noise_includes_zero(self):
        rng = np.random.default_rng(42)
        returns = rng.normal(0.0, 0.01, 300)
        result = bootstrap_sharpe_ci(returns, n_bootstrap=500, seed=1)
        assert result.ci_includes_zero
        assert result.ci_lower <= 0 <= result.ci_upper

    def test_constant_returns_give_zero_sharpe(self):
        returns = np.full(50, 0.001)
        result = bootstrap_sharpe_ci(returns, n_bootstrap=100, seed=1)
        assert result.point_estimate == 0.0
        assert result.ci_lower == 0.0
        assert result.ci_upper == 0.0
        assert result.ci_includes_zero

    def test_higher_confidence_widens_interval(self):
        rng = np.random.default_rng(42)
        returns = rng.normal(0.0, 0.01, 300)
        narrow = bootstrap_sharpe_ci(returns, n_bootstrap=500, confidence=0.5, seed=1)
        wide = bootstrap_sharpe_ci(returns, n_bootstrap=500, confidence=0.99, seed=1)
        assert wide.ci_upper - wide.ci_lower > narrow.ci_upper - narrow.ci_lower

    def test_summary_reports_pass_when_zero_excluded(self):
        rng = np.random.default_rng(0)
        returns = rng.normal(0.005, 0.002, 300)
        lines = bootstrap_sharpe_ci(returns, n_bootstrap=200, seed=1).summary()
        assert any("PASS" in line for line in lines)
        assert not any("FAIL" in line for line in lines)

    def test_summary_reports_fail_when_zero_included(self):
        rng = np.random.default_rng(42)
        returns = rng.normal(0.0, 0.01, 300)
        lines = bootstrap_sharpe_ci(returns, n_bootstrap=200, seed=1).summary()
        assert any("FAIL -- CI includes zero" in line for line in lines)


# -----------------------------------------------------------------------
# monte_carlo_trades
# -----------------------------------------------------------------------


class TestMonteCarloTrades:
    def test_fewer_than_three_trades_returns_degenerate_result(self):
        result = monte_carlo_trades([1.0, -0.5], n_simulations=100, seed=1)
        assert result.n_simulations == 0
        assert result.n_trades == 2
        assert result.actual_terminal_pnl == pytest.approx(0.5)
        assert result.actual_max_drawdown == 0.0
        assert result.terminal_pnl_percentile == 50
        assert result.max_drawdown_percentile == 50

    def test_terminal_pnl_is_invariant_under_shuffling(self):
        pnls = np.array([-1.0] * 6 + [1.0] * 8)
        result = monte_carlo_trades(pnls, n_simulations=200, seed=7)
        assert result.actual_terminal_pnl == pytest.approx(2.0)
        assert result.terminal_pnl_5th == pytest.approx(2.0)
        assert result.terminal_pnl_95th == pytest.approx(2.0)
        assert result.terminal_pnl_mean == pytest.approx(2.0)
        assert result.terminal_pnl_percentile == pytest.approx(100.0)

    def test_known_max_drawdown(self):
        # equity: [1, -1, 2]; running peak: [1, 1, 2]; max drawdown = 2
        result = monte_carlo_trades([1.0, -2.0, 3.0], n_simulations=50, seed=1)
        assert result.n_trades == 3
        assert result.actual_max_drawdown == pytest.approx(2.0)

    def test_losses_first_ordering_flagged_as_severe(self):
        pnls = np.array([-1.0] * 6 + [1.0] * 8)
        result = monte_carlo_trades(pnls, n_simulations=500, seed=7)
        assert result.actual_max_drawdown == pytest.approx(5.0)
        assert result.actual_max_drawdown > result.max_drawdown_95th
        assert result.max_drawdown_percentile > 95
        assert any("unusually severe" in line for line in result.summary())

    def test_alternating_ordering_flagged_as_mild(self):
        pnls = np.array([1.0, -0.5] * 8)
        result = monte_carlo_trades(pnls, n_simulations=500, seed=7)
        assert result.actual_max_drawdown == pytest.approx(0.5)
        assert result.actual_max_drawdown < result.max_drawdown_5th
        assert result.max_drawdown_percentile < 5
        assert any("unusually mild" in line for line in result.summary())

    def test_typical_ordering_has_no_warnings(self):
        rng = np.random.default_rng(5)
        pnls = rng.normal(0.1, 1.0, 40)
        result = monte_carlo_trades(pnls, n_simulations=500, seed=7)
        lines = result.summary()
        assert not any("WARNING" in line for line in lines)
        assert lines[0] == "Monte Carlo Trade Resampling"

    def test_drawdown_percentiles_are_ordered(self):
        rng = np.random.default_rng(5)
        pnls = rng.normal(0.1, 1.0, 40)
        result = monte_carlo_trades(pnls, n_simulations=300, seed=7)
        assert result.max_drawdown_5th <= result.max_drawdown_mean <= result.max_drawdown_95th

    def test_deterministic_given_seed(self):
        rng = np.random.default_rng(5)
        pnls = rng.normal(0.1, 1.0, 20)
        a = monte_carlo_trades(pnls, n_simulations=200, seed=3)
        b = monte_carlo_trades(pnls, n_simulations=200, seed=3)
        assert a == b

    def test_accepts_plain_list_input(self):
        result = monte_carlo_trades([1.0, -1.0, 2.0, -0.5, 1.5], n_simulations=100, seed=1)
        assert result.n_trades == 5
        assert result.n_simulations == 100


# -----------------------------------------------------------------------
# alpha_beta_decomposition
# -----------------------------------------------------------------------


class TestAlphaBetaDecomposition:
    def test_recovers_alpha_and_beta_from_noisy_linear_relation(self):
        rng = np.random.default_rng(3)
        x = rng.normal(0.0, 0.01, 400)
        y = 0.001 + 1.5 * x + rng.normal(0.0, 0.0005, 400)
        result = alpha_beta_decomposition(y, x)
        assert result.beta == pytest.approx(1.5, abs=0.05)
        assert result.alpha_daily == pytest.approx(0.001, abs=0.0002)
        assert result.alpha_annual == pytest.approx(result.alpha_daily * 252.0)
        assert result.r_squared > 0.95
        assert not result.is_just_beta
        assert result.information_ratio > 0
        assert result.tracking_error > 0

    def test_uncorrelated_noise_is_just_beta(self):
        rng = np.random.default_rng(3)
        x = rng.normal(0.0, 0.01, 400)
        y = rng.normal(0.0, 0.01, 400)
        result = alpha_beta_decomposition(y, x)
        assert abs(result.beta) < 0.2
        assert result.r_squared < 0.1
        assert result.is_just_beta

    def test_constant_benchmark_gives_zero_beta(self):
        rng = np.random.default_rng(3)
        y = rng.normal(0.001, 0.01, 50)
        x = np.full(50, 0.001)
        result = alpha_beta_decomposition(y, x)
        assert result.beta == 0.0
        assert result.alpha_daily == pytest.approx(float(np.mean(y)))
        assert result.is_just_beta  # se_alpha is inf when benchmark has no variance

    def test_constant_strategy_gives_zero_r_squared(self):
        rng = np.random.default_rng(3)
        x = rng.normal(0.0, 0.01, 50)
        y = np.full(50, 0.002)
        result = alpha_beta_decomposition(y, x)
        assert result.r_squared == 0.0
        assert result.tracking_error == pytest.approx(0.0, abs=1e-12)
        assert result.information_ratio == 0.0

    def test_mismatched_lengths_truncate_to_shortest(self):
        rng = np.random.default_rng(3)
        x = rng.normal(0.0, 0.01, 300)
        y = 0.001 + 1.5 * x
        y_long = np.concatenate([y, np.full(100, 99.0)])
        truncated = alpha_beta_decomposition(y_long, x)
        exact = alpha_beta_decomposition(y, x)
        assert truncated == exact

    def test_two_observations_never_claim_alpha(self):
        result = alpha_beta_decomposition(np.array([0.01, 0.02]), np.array([0.005, 0.01]))
        assert result.is_just_beta  # se_alpha is inf when n <= 2

    def test_perfect_fit_current_behavior(self):
        # Documents current behavior: a noiseless linear relation has se_alpha ~ 0,
        # which the guard maps to t_stat = 0, so a real alpha is labeled "just beta".
        rng = np.random.default_rng(3)
        x = rng.normal(0.0, 0.01, 400)
        y = 0.001 + 1.5 * x
        result = alpha_beta_decomposition(y, x)
        assert result.beta == pytest.approx(1.5)
        assert result.alpha_daily == pytest.approx(0.001)
        assert result.r_squared == pytest.approx(1.0)
        assert result.tracking_error == pytest.approx(0.0, abs=1e-12)
        assert result.information_ratio == 0.0
        assert result.is_just_beta

    def test_summary_has_alpha_verdict(self):
        rng = np.random.default_rng(3)
        x = rng.normal(0.0, 0.01, 400)
        y = 0.001 + 1.5 * x + rng.normal(0.0, 0.0005, 400)
        lines = alpha_beta_decomposition(y, x).summary()
        assert "HAS ALPHA" in lines[0]
        assert not any("WARNING" in line for line in lines)

    def test_summary_just_beta_verdict_includes_warning(self):
        rng = np.random.default_rng(3)
        x = rng.normal(0.0, 0.01, 400)
        y = rng.normal(0.0, 0.01, 400)
        lines = alpha_beta_decomposition(y, x).summary()
        assert "JUST BETA" in lines[0]
        assert any("mostly explained by market exposure" in line for line in lines)


# -----------------------------------------------------------------------
# kelly_criterion
# -----------------------------------------------------------------------


class TestKellyCriterion:
    def test_fewer_than_five_trades_returns_degenerate_result(self):
        result = kelly_criterion([1.0, -1.0, 2.0, 0.5])
        assert result.win_rate == 0
        assert result.kelly_fraction == 0
        assert not result.has_edge
        assert result.risk_of_ruin_full_kelly == 1
        assert result.risk_of_ruin_half_kelly == 1

    def test_known_edge_case_exact_values(self):
        # 6 wins of +2, 4 losses of -1: p=0.6, b=2 -> kelly = (0.6*2 - 0.4)/2 = 0.4
        pnls = np.array([2.0] * 6 + [-1.0] * 4)
        result = kelly_criterion(pnls)
        assert result.win_rate == pytest.approx(0.6)
        assert result.avg_win == pytest.approx(2.0)
        assert result.avg_loss == pytest.approx(1.0)
        assert result.payoff_ratio == pytest.approx(2.0)
        assert result.kelly_fraction == pytest.approx(0.4)
        assert result.half_kelly == pytest.approx(0.2)
        assert result.edge == pytest.approx(0.6 * 2.0 - 0.4 * 1.0)
        assert result.has_edge

    def test_known_edge_case_risk_of_ruin(self):
        pnls = np.array([2.0] * 6 + [-1.0] * 4)
        result = kelly_criterion(pnls)
        ratio = 0.4 / 0.6
        assert result.risk_of_ruin_full_kelly == pytest.approx(ratio ** (1 / 0.4))
        assert result.risk_of_ruin_half_kelly == pytest.approx(ratio ** (1 / 0.2))
        assert result.risk_of_ruin_half_kelly < result.risk_of_ruin_full_kelly

    def test_all_wins_current_behavior_reports_no_edge(self):
        # Documents current behavior: with no losses, avg_loss is set to the
        # sentinel 1e-12, which fails the strict `avg_loss > 1e-12` guard, so
        # payoff_ratio and kelly collapse to 0 and even a 100% win rate is
        # reported as "no edge" (though `edge` itself stays positive).
        result = kelly_criterion([1.0, 2.0, 1.5, 1.0, 3.0])
        assert result.win_rate == 1.0
        assert result.avg_win == pytest.approx(1.7)
        assert result.avg_loss == 1e-12
        assert result.payoff_ratio == 0.0
        assert result.kelly_fraction == 0.0
        assert not result.has_edge
        assert result.edge == pytest.approx(1.7)
        assert result.risk_of_ruin_full_kelly == 1.0
        assert result.risk_of_ruin_half_kelly == 1.0

    def test_all_losses_has_no_edge(self):
        result = kelly_criterion([-1.0, -2.0, -0.5, -1.5, -1.0])
        assert result.win_rate == 0.0
        assert result.avg_win == 0.0
        assert result.kelly_fraction == 0.0
        assert not result.has_edge
        assert result.risk_of_ruin_full_kelly == 1.0

    def test_negative_expectancy_is_clipped_to_zero_kelly(self):
        # p=0.4, b=1 -> raw kelly = (0.4 - 0.6)/1 = -0.2, clipped to 0
        pnls = np.array([1.0] * 4 + [-1.0] * 6)
        result = kelly_criterion(pnls)
        assert result.kelly_fraction == 0.0
        assert not result.has_edge
        assert result.edge == pytest.approx(0.4 - 0.6)

    def test_zero_pnl_trades_count_as_losses(self):
        pnls = np.array([0.0, 0.0, 0.0, 1.0, 1.0, 1.0])
        result = kelly_criterion(pnls)
        assert result.win_rate == pytest.approx(0.5)
        # zero-valued losses -> avg_loss 0 -> payoff 0 -> no exploitable edge
        assert result.avg_loss == 0.0
        assert result.payoff_ratio == 0.0
        assert not result.has_edge

    def test_tiny_kelly_uses_capped_capital_units(self):
        # 101 wins of +1, 99 losses of -1: kelly = 0.01, capital units capped near 100
        pnls = np.array([1.0] * 101 + [-1.0] * 99)
        result = kelly_criterion(pnls)
        assert result.kelly_fraction == pytest.approx(0.01)
        expected = (99.0 / 101.0) ** 100
        assert result.risk_of_ruin_full_kelly == pytest.approx(expected, rel=1e-6)
        assert result.risk_of_ruin_half_kelly == pytest.approx(expected, rel=1e-6)

    def test_summary_with_edge_lists_risk_of_ruin(self):
        lines = kelly_criterion(np.array([2.0] * 6 + [-1.0] * 4)).summary()
        assert "HAS EDGE" in lines[0]
        assert any("Risk of ruin (full Kelly)" in line for line in lines)
        assert any("Risk of ruin (half Kelly)" in line for line in lines)

    def test_summary_without_edge_warns(self):
        lines = kelly_criterion([-1.0, -2.0, -0.5, -1.5, -1.0]).summary()
        assert "NO EDGE" in lines[0]
        assert any("no edge to exploit" in line for line in lines)
        assert not any("Risk of ruin" in line for line in lines)


# -----------------------------------------------------------------------
# regime_conditional_performance
# -----------------------------------------------------------------------


class TestRegimeConditionalPerformance:
    def test_short_series_returns_single_all_regime(self):
        rng = np.random.default_rng(1)
        returns = rng.normal(0.001, 0.01, 15)
        result = regime_conditional_performance(returns, vol_window=21)
        assert result.regime_names == ["all"]
        assert result.regime_counts == [15]
        assert result.regime_frac == [1.0]
        assert result.overall_sharpe == 0.0
        assert not result.works_in_all_regimes

    def test_uniform_positive_returns_work_in_all_regimes(self):
        rng = np.random.default_rng(11)
        returns = rng.normal(0.005, 0.002, 300)
        result = regime_conditional_performance(returns, vol_window=21)
        assert result.regime_names == ["low_vol", "med_vol", "high_vol"]
        assert result.regime_counts == [93, 93, 93]  # 279 valid obs split into terciles
        assert sum(result.regime_frac) == pytest.approx(1.0)
        assert all(s > 0 for s in result.regime_sharpes)
        assert result.works_in_all_regimes
        assert result.overall_sharpe > 0

    def test_market_classified_regime_dependence(self):
        rng = np.random.default_rng(11)
        market = np.concatenate([rng.normal(0, 0.001, 150), rng.normal(0, 0.05, 150)])
        strategy = np.concatenate([rng.normal(0.01, 0.002, 150), rng.normal(-0.01, 0.002, 150)])
        result = regime_conditional_performance(strategy, market_returns=market, vol_window=21)
        assert not result.works_in_all_regimes
        assert result.worst_regime_name == "high_vol"
        assert result.worst_regime_sharpe < 0
        assert result.regime_sharpes[0] > 0  # profitable while market is calm

    def test_worst_regime_matches_minimum_sharpe(self):
        rng = np.random.default_rng(11)
        market = np.concatenate([rng.normal(0, 0.001, 150), rng.normal(0, 0.05, 150)])
        strategy = np.concatenate([rng.normal(0.01, 0.002, 150), rng.normal(-0.01, 0.002, 150)])
        result = regime_conditional_performance(strategy, market_returns=market, vol_window=21)
        worst_idx = result.regime_names.index(result.worst_regime_name)
        assert result.worst_regime_sharpe == min(result.regime_sharpes)
        assert result.regime_sharpes[worst_idx] == result.worst_regime_sharpe

    def test_non_default_regime_count_uses_generic_names(self):
        rng = np.random.default_rng(11)
        returns = rng.normal(0.005, 0.002, 300)
        result = regime_conditional_performance(returns, n_regimes=2, vol_window=21)
        assert result.regime_names == ["regime_0", "regime_1"]
        assert len(result.regime_sharpes) == 2
        assert sum(result.regime_counts) == 279

    def test_counts_cover_all_valid_observations(self):
        rng = np.random.default_rng(2)
        returns = rng.normal(0.0, 0.01, 200)
        result = regime_conditional_performance(returns, vol_window=21)
        assert sum(result.regime_counts) == 200 - 21
        assert all(f >= 0 for f in result.regime_frac)

    def test_summary_all_regimes_has_no_warning(self):
        rng = np.random.default_rng(11)
        returns = rng.normal(0.005, 0.002, 300)
        lines = regime_conditional_performance(returns, vol_window=21).summary()
        assert "ALL REGIMES" in lines[0]
        assert not any("WARNING" in line for line in lines)
        assert any("low_vol" in line for line in lines)

    def test_summary_regime_dependent_warns_with_worst_regime(self):
        rng = np.random.default_rng(11)
        market = np.concatenate([rng.normal(0, 0.001, 150), rng.normal(0, 0.05, 150)])
        strategy = np.concatenate([rng.normal(0.01, 0.002, 150), rng.normal(-0.01, 0.002, 150)])
        lines = regime_conditional_performance(
            strategy, market_returns=market, vol_window=21
        ).summary()
        assert "REGIME-DEPENDENT" in lines[0]
        assert any("Worst regime: high_vol" in line for line in lines)
