import pandas as pd
import pytest

from whale_strategy.backtest import (
    run_backtest,
    run_position_size_analysis,
    run_rolling_backtest,
)
from trading.data_modules.costs import CostModel
from trading.portfolio import PortfolioConstraints, PositionSizer


def _make_trade(ts: pd.Timestamp, contract_id: str, user_id: str, outcome: str, prob_after: float):
    created_ms = int(ts.timestamp() * 1000)
    return {
        "createdTime": created_ms,
        "datetime": ts,
        "date": ts.date(),
        "userId": user_id,
        "outcome": outcome,
        "probAfter": prob_after,
        "contractId": contract_id,
    }


def _sample_inputs():
    base_time = pd.Timestamp("2026-01-01 12:00:00")
    trades = [
        _make_trade(base_time, "m1", "whale1", "YES", 0.4),
        _make_trade(base_time + pd.Timedelta(days=1), "m2", "whale1", "NO", 0.3),
    ]
    test_df = pd.DataFrame(trades)
    resolution_data = {
        "m1": {
            "resolution": 1.0,
            "resolved_time": int((base_time + pd.Timedelta(days=2)).timestamp() * 1000),
            "question": "Resolved market",
            "liquidity": 1000,
            "volume": 200,
            "is_resolved": True,
            "status": "resolved",
            "category": "sports",
        },
        "m2": {
            "resolution": None,
            "current_prob": 0.6,
            "resolved_time": None,
            "question": "Open market",
            "liquidity": 1000,
            "volume": 200,
            "is_resolved": False,
            "status": "open",
            "close_time": int((base_time + pd.Timedelta(days=10)).timestamp() * 1000),
            "category": "sports",
        },
    }
    return test_df, resolution_data


def test_run_backtest_basic_closed_trade() -> None:
    test_df, resolution_data = _sample_inputs()
    zero_cost = CostModel(base_spread=0.0, base_slippage=0.0, impact_coefficient=0.0)

    result = run_backtest(
        test_df=test_df,
        whale_set={"whale1"},
        resolution_data=resolution_data,
        cost_model=zero_cost,
        position_size=100,
        include_open=False,
    )

    assert result is not None
    assert result.total_trades == 1
    assert result.unique_markets == 1
    assert result.open_positions == 0
    assert result.closed_trades == 1
    assert result.total_net_pnl == pytest.approx(60.0)
    assert result.total_gross_pnl == pytest.approx(60.0)
    assert result.trades_df.iloc[0]["status"] == "closed"


def test_run_backtest_include_open_positions() -> None:
    test_df, resolution_data = _sample_inputs()
    zero_cost = CostModel(base_spread=0.0, base_slippage=0.0, impact_coefficient=0.0)

    result = run_backtest(
        test_df=test_df,
        whale_set={"whale1"},
        resolution_data=resolution_data,
        cost_model=zero_cost,
        position_size=100,
        include_open=True,
    )

    assert result is not None
    assert result.total_trades == 2
    assert result.open_positions == 1
    assert result.closed_trades == 1
    open_trade = result.trades_df[result.trades_df["status"] == "open"].iloc[0]
    assert open_trade["contract_id"] == "m2"
    assert result.open_unrealized_pnl == pytest.approx(open_trade["net_pnl"])


def test_run_backtest_returns_none_without_whales() -> None:
    test_df, resolution_data = _sample_inputs()
    zero_cost = CostModel(base_spread=0.0, base_slippage=0.0, impact_coefficient=0.0)

    result = run_backtest(
        test_df=test_df,
        whale_set=set(),
        resolution_data=resolution_data,
        cost_model=zero_cost,
    )

    assert result is None


def test_run_backtest_skips_after_resolution() -> None:
    base_time = pd.Timestamp("2026-01-01 12:00:00")
    trade = _make_trade(base_time + pd.Timedelta(days=1), "m1", "whale1", "YES", 0.4)
    test_df = pd.DataFrame([trade])
    resolution_data = {
        "m1": {
            "resolution": 1.0,
            "resolved_time": int(base_time.timestamp() * 1000),
            "question": "Resolved early",
            "liquidity": 1000,
            "volume": 100,
            "is_resolved": True,
            "status": "resolved",
            "category": "sports",
        }
    }

    result = run_backtest(
        test_df=test_df,
        whale_set={"whale1"},
        resolution_data=resolution_data,
        cost_model=CostModel(base_spread=0.0, base_slippage=0.0, impact_coefficient=0.0),
    )

    assert result is None


def test_run_backtest_one_position_per_market() -> None:
    base_time = pd.Timestamp("2026-01-01 12:00:00")
    trades = [
        _make_trade(base_time, "m1", "whale1", "YES", 0.4),
        _make_trade(base_time + pd.Timedelta(minutes=10), "m1", "whale1", "YES", 0.5),
    ]
    test_df = pd.DataFrame(trades)
    resolution_data = {
        "m1": {
            "resolution": 1.0,
            "resolved_time": int((base_time + pd.Timedelta(days=1)).timestamp() * 1000),
            "question": "Single market",
            "liquidity": 1000,
            "volume": 100,
            "is_resolved": True,
            "status": "resolved",
            "category": "sports",
        }
    }

    result = run_backtest(
        test_df=test_df,
        whale_set={"whale1"},
        resolution_data=resolution_data,
        cost_model=CostModel(base_spread=0.0, base_slippage=0.0, impact_coefficient=0.0),
    )

    assert result is not None
    assert result.total_trades == 1
    assert result.unique_markets == 1


def test_run_backtest_skips_extreme_prices() -> None:
    base_time = pd.Timestamp("2026-01-01 12:00:00")
    trade = _make_trade(base_time, "m1", "whale1", "YES", 0.99)
    test_df = pd.DataFrame([trade])
    resolution_data = {
        "m1": {
            "resolution": 1.0,
            "resolved_time": int((base_time + pd.Timedelta(days=1)).timestamp() * 1000),
            "question": "Extreme",
            "liquidity": 1000,
            "volume": 100,
            "is_resolved": True,
            "status": "resolved",
            "category": "sports",
        }
    }

    result = run_backtest(
        test_df=test_df,
        whale_set={"whale1"},
        resolution_data=resolution_data,
        cost_model=CostModel(base_spread=0.0, base_slippage=0.0, impact_coefficient=0.0),
    )

    assert result is None


def test_run_backtest_category_filter_blocks_trade() -> None:
    test_df, resolution_data = _sample_inputs()
    zero_cost = CostModel(base_spread=0.0, base_slippage=0.0, impact_coefficient=0.0)

    result = run_backtest(
        test_df=test_df,
        whale_set={"whale1"},
        resolution_data=resolution_data,
        cost_model=zero_cost,
        category_filter="politics",
        include_open=False,
    )

    assert result is None


def test_run_backtest_min_liquidity_blocks_trade() -> None:
    test_df, resolution_data = _sample_inputs()
    zero_cost = CostModel(base_spread=0.0, base_slippage=0.0, impact_coefficient=0.0)
    constraints = PortfolioConstraints(min_liquidity=5000)

    result = run_backtest(
        test_df=test_df,
        whale_set={"whale1"},
        resolution_data=resolution_data,
        cost_model=zero_cost,
        portfolio_constraints=constraints,
        include_open=False,
    )

    assert result is None


def test_run_backtest_position_sizer_respects_capital_floor() -> None:
    base_time = pd.Timestamp("2026-01-01 12:00:00")
    trade = _make_trade(base_time, "m1", "whale1", "YES", 0.4)
    test_df = pd.DataFrame([trade])
    resolution_data = {
        "m1": {
            "resolution": 1.0,
            "resolved_time": int((base_time + pd.Timedelta(days=1)).timestamp() * 1000),
            "question": "Capital cap",
            "liquidity": 1000,
            "volume": 100,
            "is_resolved": True,
            "status": "resolved",
            "category": "sports",
        }
    }
    constraints = PortfolioConstraints(max_total_capital=20)
    position_sizer = PositionSizer(base_position_size=100, min_position_size=50)

    result = run_backtest(
        test_df=test_df,
        whale_set={"whale1"},
        resolution_data=resolution_data,
        cost_model=CostModel(base_spread=0.0, base_slippage=0.0, impact_coefficient=0.0),
        portfolio_constraints=constraints,
        position_sizer=position_sizer,
    )

    assert result is None


def test_run_backtest_applies_costs() -> None:
    test_df, resolution_data = _sample_inputs()
    cost_model = CostModel(base_spread=0.02, base_slippage=0.01, impact_coefficient=0.5)

    result = run_backtest(
        test_df=test_df,
        whale_set={"whale1"},
        resolution_data=resolution_data,
        cost_model=cost_model,
        position_size=100,
        include_open=False,
    )

    assert result is not None
    assert result.total_net_pnl < result.total_gross_pnl


def test_run_position_size_analysis_outputs() -> None:
    test_df, resolution_data = _sample_inputs()
    for market in resolution_data.values():
        market["liquidity"] = 10000

    results = run_position_size_analysis(
        test_df=test_df,
        whale_set={"whale1"},
        resolution_data=resolution_data,
        position_sizes=[100, 1000],
        total_capital=10000,
        include_open=False,
    )

    assert len(results) == 2
    assert set(results["position_size"]) == {100, 1000}
    assert set(results["cost_category"]) == {"small", "medium"}


def test_run_rolling_backtest_basic() -> None:
    base_time = pd.Timestamp("2026-01-01 12:00:00")
    jan_rows = []
    for i in range(900):
        ts = base_time + pd.Timedelta(minutes=i)
        jan_rows.append(_make_trade(ts, "m1", "minnow", "YES", 0.4))
        jan_rows[-1]["amount_abs"] = 10
    for i in range(100):
        ts = base_time + pd.Timedelta(minutes=900 + i)
        jan_rows.append(_make_trade(ts, "m1", "whale", "YES", 0.4))
        jan_rows[-1]["amount_abs"] = 1000

    feb_base = pd.Timestamp("2026-02-01 12:00:00")
    feb_rows = []
    for i in range(120):
        ts = feb_base + pd.Timedelta(minutes=i)
        feb_rows.append(_make_trade(ts, "m1", "whale", "YES", 0.4))
        feb_rows[-1]["amount_abs"] = 1000

    df = pd.DataFrame(jan_rows + feb_rows)
    df["month"] = pd.to_datetime(df["datetime"]).dt.to_period("M")

    resolution_data = {
        "m1": {
            "resolution": 1.0,
            "resolved_time": int(pd.Timestamp("2026-03-01").timestamp() * 1000),
            "question": "Rolling test",
            "liquidity": 10000,
            "volume": 1000,
            "is_resolved": True,
            "status": "resolved",
            "category": "sports",
        }
    }

    results = run_rolling_backtest(
        df=df,
        resolution_data=resolution_data,
        method="volume_pct95",
        lookback_months=1,
        position_size=100,
        cost_model=CostModel(base_spread=0.0, base_slippage=0.0, impact_coefficient=0.0),
        include_open=False,
    )

    assert len(results) == 1
    assert results.iloc[0]["trades"] > 0
