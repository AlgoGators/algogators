"""Tests for transaction cost models with synthetic trade data."""

import dataclasses

import numpy as np
import pytest
from algosystem.shared.errors import ValidationError
from algosystem.validation.domain.costs import (
    CRYPTO_SPOT,
    INSTITUTIONAL_EQUITY,
    RETAIL_EQUITY,
    ZERO_COST,
    CostModel,
    TransactionCostBacktest,
    apply_transaction_costs,
    compute_trade_log,
    compute_turnover,
    trade_log_summary,
    wrap_backtest_with_costs,
)


def _sharpe_ref(returns: np.ndarray, annualize: float = 252.0) -> float:
    """Reference Sharpe: mean/std(ddof=1) * sqrt(annualize)."""
    if len(returns) < 2:
        return 0.0
    std = np.std(returns, ddof=1)
    if std < 1e-12:
        return 0.0
    return float(np.mean(returns) / std * np.sqrt(annualize))


class TestCostModel:
    def test_default_total_bps(self):
        assert CostModel().total_cost_per_trade_bps == pytest.approx(7.0)

    def test_default_total_decimal(self):
        assert CostModel().total_cost_per_trade_decimal == pytest.approx(0.0007)

    def test_slippage_adds_to_decimal_but_not_bps(self):
        cm = CostModel(
            commission_bps=10.0, spread_bps=5.0, market_impact_bps=5.0, slippage_pct=0.001
        )
        assert cm.total_cost_per_trade_bps == pytest.approx(20.0)
        assert cm.total_cost_per_trade_decimal == pytest.approx(0.003)

    def test_presets(self):
        assert ZERO_COST.total_cost_per_trade_decimal == 0.0
        assert RETAIL_EQUITY.total_cost_per_trade_bps == pytest.approx(8.0)
        assert INSTITUTIONAL_EQUITY.total_cost_per_trade_bps == pytest.approx(4.0)
        assert CRYPTO_SPOT.total_cost_per_trade_bps == pytest.approx(15.0)

    def test_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            CostModel().commission_bps = 1.0  # type: ignore[misc]


class TestComputeTurnover:
    def test_initial_entry_counts(self):
        assert np.allclose(compute_turnover(np.array([1.0, 1.0, 1.0])), [1.0, 0.0, 0.0])

    def test_flip_counts_double(self):
        turnover = compute_turnover(np.array([0.0, 1.0, -1.0, 0.0]))
        assert np.allclose(turnover, [0.0, 1.0, 2.0, 1.0])

    def test_fractional_positions(self):
        turnover = compute_turnover(np.array([0.5, 0.25, 0.25]))
        assert np.allclose(turnover, [0.5, 0.25, 0.0])

    def test_single_short_entry(self):
        assert np.allclose(compute_turnover(np.array([-1.0])), [1.0])


class TestApplyTransactionCosts:
    def test_length_mismatch_raises(self):
        with pytest.raises(ValidationError, match="same length"):
            apply_transaction_costs(np.zeros(3), np.zeros(4), ZERO_COST)

    def test_zero_cost_returns_gross(self):
        returns = np.array([0.01, -0.02, 0.03])
        positions = np.array([1.0, -1.0, 0.0])
        net = apply_transaction_costs(returns, positions, ZERO_COST)
        assert np.allclose(net, positions * returns)

    def test_costs_charged_on_turnover_only(self):
        returns = np.array([0.01, 0.02, -0.01])
        positions = np.array([1.0, 1.0, 0.0])
        net = apply_transaction_costs(returns, positions, CostModel())
        # turnover = [1, 0, 1]; cost per unit turnover = 0.0007
        assert np.allclose(net, [0.01 - 0.0007, 0.02, 0.0 - 0.0007])

    def test_short_position_gross_sign(self):
        returns = np.array([0.01, -0.02])
        positions = np.array([-1.0, -1.0])
        cm = CostModel(commission_bps=10.0, spread_bps=0.0)
        net = apply_transaction_costs(returns, positions, cm)
        assert np.allclose(net, [-0.01 - 0.001, 0.02])


class TestComputeTradeLog:
    def test_no_positions_no_trades(self):
        assert compute_trade_log(np.zeros(10), np.zeros(10)) == []

    def test_simple_long_round_trip(self):
        positions = np.array([0.0, 1.0, 1.0, 0.0, 0.0])
        returns = np.array([0.0, 0.01, 0.02, 0.03, 0.0])
        trades = compute_trade_log(positions, returns, CostModel())
        assert len(trades) == 1
        trade = trades[0]
        assert trade["entry_idx"] == 1
        assert trade["exit_idx"] == 3
        assert trade["direction"] == "long"
        assert trade["duration"] == 2
        # Entry-bar return is not accrued; PnL covers bars after entry.
        assert trade["gross_pnl"] == pytest.approx(0.05)
        assert trade["cost"] == pytest.approx(2 * 0.0007)
        assert trade["net_pnl"] == pytest.approx(0.05 - 0.0014)

    def test_none_cost_model_means_zero_cost(self):
        positions = np.array([1.0, 1.0, 0.0])
        returns = np.array([0.01, 0.02, 0.01])
        trades = compute_trade_log(positions, returns, cost_model=None)
        assert trades[0]["cost"] == 0.0
        assert trades[0]["net_pnl"] == trades[0]["gross_pnl"]

    def test_flip_creates_two_trades(self):
        positions = np.array([1.0, 1.0, -1.0, -1.0, 0.0])
        returns = np.full(5, 0.01)
        trades = compute_trade_log(positions, returns)
        assert len(trades) == 2
        first, second = trades
        assert (first["entry_idx"], first["exit_idx"], first["direction"]) == (0, 2, "long")
        assert first["gross_pnl"] == pytest.approx(0.02)
        assert (second["entry_idx"], second["exit_idx"], second["direction"]) == (2, 4, "short")
        assert second["gross_pnl"] == pytest.approx(-0.02)

    def test_short_trade_pnl_sign(self):
        positions = np.array([0.0, -1.0, -1.0, 0.0])
        returns = np.array([0.0, -0.01, -0.02, 0.005])
        trades = compute_trade_log(positions, returns)
        assert len(trades) == 1
        assert trades[0]["direction"] == "short"
        # cum = -(-0.02) + -(0.005) = 0.015
        assert trades[0]["gross_pnl"] == pytest.approx(0.015)

    def test_open_trade_closed_at_end(self):
        positions = np.array([0.0, 0.0, 1.0, 1.0, 1.0])
        returns = np.array([0.0, 0.0, 0.01, 0.02, 0.03])
        trades = compute_trade_log(positions, returns, CostModel())
        assert len(trades) == 1
        assert trades[0]["exit_idx"] == 4
        assert trades[0]["duration"] == 2
        assert trades[0]["gross_pnl"] == pytest.approx(0.05)
        assert trades[0]["cost"] == pytest.approx(0.0014)

    def test_entry_on_last_bar(self):
        positions = np.array([0.0, 0.0, 0.0, 0.0, 1.0])
        trades = compute_trade_log(positions, np.full(5, 0.01), CostModel())
        assert len(trades) == 1
        assert trades[0]["entry_idx"] == 4
        assert trades[0]["duration"] == 0
        assert trades[0]["gross_pnl"] == 0.0
        assert trades[0]["net_pnl"] == pytest.approx(-0.0014)

    def test_flip_on_last_bar_stays_one_trade(self):
        # Current behavior: a flip on the final bar does not split the trade;
        # the last return accrues to the original direction.
        positions = np.array([1.0, 1.0, -1.0])
        returns = np.array([0.0, 0.01, 0.02])
        trades = compute_trade_log(positions, returns)
        assert len(trades) == 1
        assert trades[0]["direction"] == "long"
        assert trades[0]["exit_idx"] == 2
        assert trades[0]["gross_pnl"] == pytest.approx(0.03)


class TestTradeLogSummary:
    def test_empty_log(self):
        summary = trade_log_summary([])
        assert summary["n_trades"] == 0
        assert summary["win_rate"] == 0.0
        assert summary["profit_factor"] == 0.0
        assert summary["net_pnl"] == 0.0

    def test_mixed_trades(self):
        trades = [
            {"net_pnl": 0.1, "gross_pnl": 0.12, "cost": 0.02, "duration": 5},
            {"net_pnl": -0.05, "gross_pnl": -0.04, "cost": 0.01, "duration": 3},
        ]
        summary = trade_log_summary(trades)
        assert summary["n_trades"] == 2
        assert summary["win_rate"] == pytest.approx(0.5)
        assert summary["avg_pnl"] == pytest.approx(0.025)
        assert summary["profit_factor"] == pytest.approx(2.0)
        assert summary["avg_duration"] == pytest.approx(4.0)
        assert summary["total_costs"] == pytest.approx(0.03)
        assert summary["gross_pnl"] == pytest.approx(0.08)
        assert summary["net_pnl"] == pytest.approx(0.05)

    def test_all_winners_infinite_profit_factor(self):
        trades = [{"net_pnl": 0.1, "gross_pnl": 0.1, "cost": 0.0, "duration": 1}]
        summary = trade_log_summary(trades)
        assert summary["profit_factor"] == float("inf")
        assert summary["win_rate"] == 1.0

    def test_breakeven_trade_is_not_a_win(self):
        trades = [{"net_pnl": 0.0, "gross_pnl": 0.0, "cost": 0.0, "duration": 1}]
        assert trade_log_summary(trades)["win_rate"] == 0.0

    def test_round_trip_from_trade_log(self):
        positions = np.array([1.0, 1.0, 0.0, -1.0, -1.0, 0.0])
        returns = np.array([0.0, 0.02, 0.0, 0.0, -0.03, 0.0])
        trades = compute_trade_log(positions, returns)
        summary = trade_log_summary(trades)
        assert summary["n_trades"] == 2
        assert summary["win_rate"] == 1.0
        assert summary["gross_pnl"] == pytest.approx(0.05)


class TestTransactionCostBacktest:
    RETURNS = np.array([0.01, -0.005, 0.02, 0.0, 0.015, -0.01, 0.005, 0.01])

    def test_wrap_returns_wrapper(self):
        def bt(params, returns):
            return 1.0

        wrapped = wrap_backtest_with_costs(bt, ZERO_COST)
        assert isinstance(wrapped, TransactionCostBacktest)
        assert wrapped.backtest_fn is bt
        assert wrapped.cost_model is ZERO_COST

    def test_position_aware_zero_cost_matches_raw_sharpe(self):
        def bt(params, returns):
            if params.get("_return_positions"):
                return (0.0, np.ones_like(returns))
            return 0.0

        wrapped = TransactionCostBacktest(backtest_fn=bt, cost_model=ZERO_COST)
        result = wrapped({}, self.RETURNS)
        assert result == pytest.approx(_sharpe_ref(self.RETURNS))

    def test_position_aware_costs_reduce_sharpe(self):
        def bt(params, returns):
            if params.get("_return_positions"):
                return (0.0, np.ones_like(returns))
            return 0.0

        wrapped = TransactionCostBacktest(backtest_fn=bt, cost_model=RETAIL_EQUITY)
        result = wrapped({}, self.RETURNS)
        net = self.RETURNS.copy()
        net[0] -= RETAIL_EQUITY.total_cost_per_trade_decimal  # turnover only at entry
        assert result == pytest.approx(_sharpe_ref(net))
        assert result < _sharpe_ref(self.RETURNS)

    def test_position_aware_does_not_mutate_params(self):
        seen = []

        def bt(params, returns):
            seen.append(dict(params))
            return (0.0, np.ones_like(returns))

        params = {"lookback": 5}
        TransactionCostBacktest(backtest_fn=bt, cost_model=ZERO_COST)(params, self.RETURNS)
        assert seen[0]["_return_positions"] is True
        assert params == {"lookback": 5}

    def test_constant_net_returns_give_zero_sharpe(self):
        def bt(params, returns):
            return (0.0, np.ones_like(returns))

        wrapped = TransactionCostBacktest(backtest_fn=bt, cost_model=ZERO_COST)
        assert wrapped({}, np.full(10, 0.01)) == 0.0

    def test_too_few_returns_give_zero_sharpe(self):
        def bt(params, returns):
            return (0.0, np.ones_like(returns))

        wrapped = TransactionCostBacktest(backtest_fn=bt, cost_model=ZERO_COST)
        assert wrapped({}, np.array([0.05])) == 0.0

    def test_fallback_penalizes_by_estimated_turnover(self):
        def bt(params, returns):
            return 1.0  # never position-aware

        wrapped = TransactionCostBacktest(backtest_fn=bt, cost_model=CostModel())
        result = wrapped({"lookback": 252}, self.RETURNS)
        # 252/252*2 = 2 trades/year at 7 bps each
        expected = 1.0 - (2 * 0.0007) / (0.01 * np.sqrt(252))
        assert result == pytest.approx(expected)

    def test_fallback_default_lookback(self):
        def bt(params, returns):
            return 1.0

        wrapped = TransactionCostBacktest(backtest_fn=bt, cost_model=CostModel())
        result = wrapped({}, self.RETURNS)
        expected = 1.0 - (252.0 / 20 * 2 * 0.0007) / (0.01 * np.sqrt(252))
        assert result == pytest.approx(expected)

    def test_fallback_uses_channel_param(self):
        def bt(params, returns):
            return 0.5

        wrapped = TransactionCostBacktest(backtest_fn=bt, cost_model=CostModel())
        result = wrapped({"channel": 126}, self.RETURNS)
        expected = 0.5 - (252.0 / 126 * 2 * 0.0007) / (0.01 * np.sqrt(252))
        assert result == pytest.approx(expected)

    def test_fallback_zero_cost_keeps_gross_sharpe(self):
        def bt(params, returns):
            return 1.5

        wrapped = TransactionCostBacktest(backtest_fn=bt, cost_model=ZERO_COST)
        assert wrapped({"lookback": 10}, self.RETURNS) == pytest.approx(1.5)
