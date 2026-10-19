from datetime import datetime, timedelta

import pandas as pd

from trading.portfolio import PortfolioConstraints, PortfolioState, PositionSizer


def test_portfolio_constraints_market_allowed() -> None:
    constraints = PortfolioConstraints(
        min_liquidity=100,
        min_volume=50,
        allowed_categories={"sports"},
        blocked_categories={"crypto"},
    )

    assert constraints.market_allowed("sports", liquidity=200, volume=100) is True
    assert constraints.market_allowed("sports", liquidity=10, volume=100) is False
    assert constraints.market_allowed("sports", liquidity=200, volume=10) is False
    assert constraints.market_allowed("politics", liquidity=200, volume=100) is False
    assert constraints.market_allowed("crypto", liquidity=200, volume=100) is False


def test_position_sizer_respects_caps() -> None:
    sizer = PositionSizer(
        base_position_size=500,
        min_position_size=50,
        max_position_size=300,
        max_pct_liquidity=0.1,
    )

    size = sizer.size_for_market(liquidity=1000, capital_available=200, cap_per_market=250)
    assert size == 100  # 10% liquidity cap

    size = sizer.size_for_market(liquidity=10000, capital_available=200, cap_per_market=250)
    assert size == 200  # capital cap

    size = sizer.size_for_market(liquidity=1000, capital_available=20, cap_per_market=250)
    assert size == 0.0  # below min_position_size


def test_portfolio_state_open_close_and_exposure() -> None:
    constraints = PortfolioConstraints(
        max_concurrent_positions=2,
        max_total_capital=1000,
        max_capital_per_market=400,
        max_exposure_per_category=600,
    )
    portfolio = PortfolioState(constraints=constraints, total_capital=1000)

    now = datetime(2026, 1, 1, 12, 0, 0)
    assert portfolio.can_open("sports", 300) is True
    portfolio.open_position("m1", "sports", 300, pd.Timestamp(now + timedelta(days=1)))

    assert portfolio.can_open("sports", 400) is False  # category cap
    assert portfolio.can_open("politics", 500) is False  # per-market cap
    assert portfolio.can_open("politics", 350) is True
    portfolio.open_position("m2", "politics", 350, pd.Timestamp(now + timedelta(hours=6)))

    # At max concurrent positions
    assert portfolio.can_open("sports", 100) is False

    portfolio.close_positions_through(pd.Timestamp(now + timedelta(hours=8)))
    assert "m2" not in portfolio.open_positions
    assert portfolio.can_open("sports", 100) is True
