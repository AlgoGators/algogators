from datetime import datetime

import pytest

from trading.risk import (
    RiskLimits,
    PortfolioRiskState,
    PositionState,
    PositionLimitModule,
    ExposureLimitModule,
    DrawdownModule,
    MarketQualityModule,
    VolatilitySizingModule,
    RiskManager,
    RiskAction,
)


def _base_state() -> PortfolioRiskState:
    return PortfolioRiskState(capital=1000, cash=1000)


def test_position_limit_module_rejects_over_max_positions() -> None:
    limits = RiskLimits(max_total_positions=1)
    state = _base_state()
    state.positions["p1"] = PositionState(
        market_id="m1",
        category="sports",
        outcome="YES",
        side="BUY",
        entry_time=datetime.utcnow(),
        entry_price=0.5,
        size=100,
    )
    module = PositionLimitModule()
    signal = {"market_id": "m2", "category": "sports", "size": 50}

    result = module.check(signal, state, limits)
    assert result.action == RiskAction.REJECT


def test_position_limit_module_reduces_size() -> None:
    limits = RiskLimits(max_position_size=100)
    state = _base_state()
    module = PositionLimitModule()
    signal = {"market_id": "m1", "category": "sports", "size": 250}

    result = module.check(signal, state, limits)
    assert result.action == RiskAction.REDUCE_SIZE
    assert result.adjusted_size == 100


def test_exposure_limit_module_enforces_total() -> None:
    limits = RiskLimits(max_total_exposure=200, min_position_size=10)
    state = _base_state()
    state.total_exposure = 180
    module = ExposureLimitModule()
    signal = {"market_id": "m1", "category": "sports", "size": 100}

    result = module.check(signal, state, limits)
    assert result.action == RiskAction.REDUCE_SIZE
    assert result.adjusted_size == pytest.approx(20)


def test_exposure_limit_module_rejects_when_no_room() -> None:
    limits = RiskLimits(max_total_exposure=200, min_position_size=50)
    state = _base_state()
    state.total_exposure = 180
    module = ExposureLimitModule()
    signal = {"market_id": "m1", "category": "sports", "size": 100}

    result = module.check(signal, state, limits)
    assert result.action == RiskAction.REJECT


def test_drawdown_module_halts_trading() -> None:
    limits = RiskLimits(drawdown_halt_pct=0.1)
    state = _base_state()
    state.high_water_mark = 1000
    state.max_drawdown = 150
    module = DrawdownModule()
    result = module.check({}, state, limits)

    assert result.action == RiskAction.HALT_TRADING


def test_market_quality_module_rejects() -> None:
    limits = RiskLimits(min_liquidity=500, min_volume_24h=100, max_spread_pct=0.1)
    state = _base_state()
    module = MarketQualityModule()

    signal = {"liquidity": 100, "volume_24h": 200, "spread": 0.05}
    result = module.check(signal, state, limits)
    assert result.action == RiskAction.REJECT

    signal = {"liquidity": 600, "volume_24h": 50, "spread": 0.05}
    result = module.check(signal, state, limits)
    assert result.action == RiskAction.REJECT

    signal = {"liquidity": 600, "volume_24h": 200, "spread": 0.2}
    result = module.check(signal, state, limits)
    assert result.action == RiskAction.REJECT


def test_volatility_sizing_module_reduces_size() -> None:
    limits = RiskLimits(
        max_position_size=500,
        max_position_volatility=0.5,
        target_portfolio_volatility=0.1,
    )
    state = _base_state()
    module = VolatilitySizingModule()
    signal = {"volatility": 0.4, "size": 300}

    result = module.check(signal, state, limits)
    assert result.action in (RiskAction.ALLOW, RiskAction.REDUCE_SIZE)


def test_risk_manager_adjusts_size() -> None:
    limits = RiskLimits(max_position_size=100)
    manager = RiskManager(limits=limits)
    manager.initialize(capital=1000)

    result = manager.check_signal({"market_id": "m1", "size": 200, "liquidity": 1000, "volume_24h": 1000})
    assert result.action == RiskAction.REDUCE_SIZE
    assert result.adjusted_size == 100
