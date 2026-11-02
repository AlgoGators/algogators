"""
Bias-free backtesting engine for whale following strategies.

Addresses potential biases:
1. Look-ahead bias: Train/test split with temporal ordering
2. Survivorship bias: All markets included
3. Selection bias: Whales identified from training period only
4. Execution bias: Entry at probAfter (post-whale) price
5. Multiple entry bias: One position per market
"""

import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, Set, List, Any, Optional
from scipy import stats

from trading.data_modules.costs import CostModel, COST_ASSUMPTIONS, DEFAULT_COST_MODEL
from trading.portfolio import PortfolioConstraints, PositionSizer, PortfolioState
from trading.reporting import compute_daily_returns, compute_run_metrics


@dataclass
class BacktestResult:
    """Results from a backtest run."""

    strategy_name: str
    total_trades: int
    unique_markets: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_gross_pnl: float
    total_net_pnl: float
    total_costs: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    sharpe_ratio: float
    max_drawdown: float
    roi_pct: float
    position_size: float
    starting_capital: float
    avg_holding_days: float
    max_concurrent_positions: int
    avg_concurrent_positions: float
    trades_df: pd.DataFrame = field(repr=False)
    daily_returns: pd.Series = field(repr=False)
    avg_position_size: float = 0.0
    max_position_size: float = 0.0
    max_concurrent_capital: float = 0.0
    # Open position tracking (with defaults for backward compatibility)
    open_positions: int = 0
    open_capital: float = 0
    open_unrealized_pnl: float = 0
    closed_trades: int = 0

    def statistical_significance(self) -> Dict[str, float]:
        """Calculate statistical significance metrics."""
        if len(self.daily_returns) < 10:
            return {"t_stat": 0, "p_value": 1, "binom_p": 1}

        # T-test for returns
        t_stat, p_value = stats.ttest_1samp(self.daily_returns, 0)

        # Binomial test for win rate
        binom_result = stats.binomtest(
            self.winning_trades,
            self.total_trades,
            0.5,
            alternative="greater"
        )

        return {
            "t_stat": t_stat,
            "p_value": p_value,
            "binom_p": binom_result.pvalue,
        }


def run_backtest(
    test_df: pd.DataFrame,
    whale_set: Set[str],
    resolution_data: Dict[str, Dict[str, Any]],
    strategy_name: str = "Whale Strategy",
    cost_model: Optional[CostModel] = None,
    position_size: float = 100,
    starting_capital: float = 10000,
    min_liquidity: float = 0,
    category_filter: Optional[str] = None,
    include_open: bool = False,
    portfolio_constraints: Optional[PortfolioConstraints] = None,
    position_sizer: Optional[PositionSizer] = None,
) -> Optional[BacktestResult]:
    """
    Run bias-free backtest of whale following strategy.

    Args:
        test_df: Test period bets DataFrame
        whale_set: Set of whale user IDs
        resolution_data: Market resolution mapping
        strategy_name: Name for this strategy
        cost_model: Cost model (default: small retail)
        position_size: Fixed position size in dollars
        starting_capital: Starting capital for ROI calculation
        min_liquidity: Minimum market liquidity to trade
        category_filter: Only trade this category (if set)
        portfolio_constraints: Portfolio risk controls and market filters
        position_sizer: Optional position sizing rules

    Returns:
        BacktestResult or None if no valid trades
    """
    if cost_model is None:
        cost_model = DEFAULT_COST_MODEL

    if portfolio_constraints is None:
        portfolio_constraints = PortfolioConstraints(min_liquidity=min_liquidity)
    elif min_liquidity > portfolio_constraints.min_liquidity:
        portfolio_constraints.min_liquidity = min_liquidity

    if position_sizer is None:
        position_sizer = PositionSizer(base_position_size=position_size)

    portfolio = PortfolioState(constraints=portfolio_constraints)

    # Filter to whale trades
    whale_trades = test_df[test_df["userId"].isin(whale_set)].copy()
    whale_trades = whale_trades[whale_trades["outcome"].isin(["YES", "NO"])]
    whale_trades = whale_trades[whale_trades["probAfter"].notna()]
    whale_trades = whale_trades.sort_values("datetime")

    if len(whale_trades) == 0:
        return None

    # Track positions - one per market
    positions: Dict[str, bool] = {}
    trades: List[Dict[str, Any]] = []

    for _, trade in whale_trades.iterrows():
        contract_id = trade["contractId"]

        # One position per market
        if contract_id in positions:
            continue

        # Must have market data
        if contract_id not in resolution_data:
            continue

        res_data = resolution_data[contract_id]
        is_resolved = res_data.get("is_resolved", True)  # Default True for backward compat
        liquidity = res_data.get("liquidity", 1000)
        volume = res_data.get("volume", 0)

        # Skip open markets unless include_open is True
        if not is_resolved and not include_open:
            continue

        category = res_data.get("category", "other")
        if not portfolio_constraints.market_allowed(category, liquidity, volume):
            continue

        # Category filter
        if category_filter:
            if category != category_filter:
                continue

        trade_time = trade["createdTime"]
        outcome = trade["outcome"]
        prob_after = trade["probAfter"]
        entry_time = (
            pd.to_datetime(trade_time, unit="ms")
            if isinstance(trade_time, (int, float))
            else trade["datetime"]
        )

        portfolio.close_positions_through(entry_time)

        # Handle resolved vs open markets differently
        if is_resolved:
            resolution = res_data["resolution"]
            res_time = res_data["resolved_time"]

            # Must trade before resolution
            if res_time and trade_time >= res_time:
                continue

            # Calculate entry/exit prices for resolved markets
            if outcome == "YES":
                entry_price = prob_after
                exit_price = resolution
            else:
                entry_price = 1 - prob_after
                exit_price = 1 - resolution

            status = "closed"
        else:
            # Open market - use current probability as mark-to-market
            current_prob = res_data.get("current_prob", 0.5)
            res_time = None

            if outcome == "YES":
                entry_price = prob_after
                exit_price = current_prob  # Mark-to-market
            else:
                entry_price = 1 - prob_after
                exit_price = 1 - current_prob

            status = "open"

        # Skip extreme prices
        if entry_price < 0.02 or entry_price > 0.98:
            continue

        effective_position_size = position_sizer.size_for_market(
            liquidity=liquidity,
            capital_available=portfolio.available_capital(),
            cap_per_market=portfolio_constraints.max_capital_per_market,
        )
        if effective_position_size <= 0:
            continue

        if not portfolio.can_open(category, effective_position_size):
            continue

        # Apply costs
        entry_cost = cost_model.calculate_entry_price(
            entry_price, effective_position_size, liquidity
        )
        # For open positions, don't apply exit costs yet (unrealized)
        if status == "closed":
            exit_proceeds = cost_model.calculate_exit_price(
                exit_price, effective_position_size, liquidity
            )
        else:
            exit_proceeds = exit_price * effective_position_size  # No exit cost for open

        gross_pnl = (exit_price - entry_price) * effective_position_size
        if status == "closed":
            net_pnl = (exit_proceeds - entry_cost) * effective_position_size
        else:
            # Unrealized P&L (entry cost already paid, exit cost not yet)
            net_pnl = (exit_price * effective_position_size) - (
                entry_cost * effective_position_size
            )

        positions[contract_id] = True

        # Sanitize question for output
        question = res_data.get("question", "")[:60]
        question = question.encode("ascii", "replace").decode("ascii")

        # Calculate holding period
        if res_time:
            exit_time = pd.to_datetime(res_time, unit="ms")
            holding_days = (exit_time - entry_time).total_seconds() / 86400
        else:
            # For open positions, calculate days since entry
            exit_time = pd.Timestamp.now()
            holding_days = (exit_time - entry_time).total_seconds() / 86400

        portfolio.open_position(
            contract_id=contract_id,
            category=category,
            size=effective_position_size,
            exit_time=exit_time,
        )

        trades.append({
            "datetime": trade["datetime"],
            "date": trade["date"],
            "contract_id": contract_id,
            "whale_id": trade["userId"],
            "status": status,
            "outcome": outcome,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "position_size": effective_position_size,
            "question": question,
            "entry_time": entry_time,
            "exit_time": exit_time,
            "holding_days": holding_days,
        })

    if not trades:
        return None

    trades_df = pd.DataFrame(trades)
    trades_df = trades_df.sort_values("datetime")
    trades_df["cumulative_pnl"] = trades_df["net_pnl"].cumsum()

    daily_returns = compute_daily_returns(trades_df, starting_capital)
    metrics = compute_run_metrics(
        trades_df=trades_df,
        starting_capital=starting_capital,
        position_size=position_size,
        daily_returns=daily_returns,
    )

    return BacktestResult(
        strategy_name=strategy_name,
        total_trades=metrics.total_trades,
        unique_markets=metrics.unique_markets,
        winning_trades=metrics.winning_trades,
        losing_trades=metrics.losing_trades,
        win_rate=metrics.win_rate,
        total_gross_pnl=metrics.total_gross_pnl,
        total_net_pnl=metrics.total_net_pnl,
        total_costs=metrics.total_costs,
        avg_win=metrics.avg_win,
        avg_loss=metrics.avg_loss,
        profit_factor=metrics.profit_factor,
        sharpe_ratio=metrics.sharpe_ratio,
        max_drawdown=metrics.max_drawdown,
        roi_pct=metrics.roi_pct,
        position_size=position_size,
        starting_capital=starting_capital,
        avg_holding_days=metrics.avg_holding_days,
        max_concurrent_positions=metrics.max_concurrent_positions,
        avg_concurrent_positions=metrics.avg_concurrent_positions,
        trades_df=trades_df,
        daily_returns=daily_returns,
        avg_position_size=metrics.avg_position_size,
        max_position_size=metrics.max_position_size,
        max_concurrent_capital=metrics.max_concurrent_capital,
        open_positions=metrics.open_positions,
        open_capital=metrics.open_capital,
        open_unrealized_pnl=metrics.open_unrealized_pnl,
        closed_trades=metrics.closed_trades,
    )


def print_result(result: BacktestResult) -> None:
    """Print formatted backtest results."""
    print(f"\n{'='*60}")
    print(f"STRATEGY: {result.strategy_name}")
    print(f"{'='*60}")
    print(f"Unique Markets Traded:  {result.unique_markets:,}")
    print(f"Total Trades:           {result.total_trades:,}")
    print(f"Win Rate:               {result.win_rate*100:.1f}%")
    print()
    print(f"Gross P&L:              ${result.total_gross_pnl:>12,.2f}")
    print(f"Trading Costs:          ${result.total_costs:>12,.2f}")
    print(f"Net P&L:                ${result.total_net_pnl:>12,.2f}")
    print(f"ROI:                    {result.roi_pct:>12.2f}%")
    print()
    print(f"Avg Winning Trade:      ${result.avg_win:>12,.2f}")
    print(f"Avg Losing Trade:       ${result.avg_loss:>12,.2f}")
    print(f"Profit Factor:          {result.profit_factor:>12.2f}")
    print()
    print(f"Sharpe Ratio:           {result.sharpe_ratio:>12.2f}")
    print(f"Max Drawdown:           ${result.max_drawdown:>12,.2f}")
    print()
    print("--- Capital Allocation ---")
    print(f"Position Size:          ${result.position_size:>12,.0f}")
    if result.avg_position_size > 0 and abs(result.avg_position_size - result.position_size) > 0.01:
        print(f"Avg Position Size:      ${result.avg_position_size:>12,.0f}")
        print(f"Max Position Size:      ${result.max_position_size:>12,.0f}")
    print(f"Avg Holding Period:      {result.avg_holding_days:>12.1f} days")
    print(f"Max Concurrent Pos:      {result.max_concurrent_positions:>12,}")
    print(f"Avg Concurrent Pos:      {result.avg_concurrent_positions:>12.1f}")
    if result.max_concurrent_capital > 0:
        print(f"Peak Capital Required:  ${result.max_concurrent_capital:>12,.0f}")
    elif result.max_concurrent_positions > 0:
        peak_capital = result.max_concurrent_positions * result.position_size
        print(f"Peak Capital Required:  ${peak_capital:>12,.0f}")

    # Open positions (if any)
    if result.open_positions > 0:
        print()
        print("--- Open Positions ---")
        print(f"Closed Trades:          {result.closed_trades:>12,}")
        print(f"Open Positions:         {result.open_positions:>12,}")
        print(f"Capital in Open:        ${result.open_capital:>12,.0f}")
        print(f"Unrealized P&L:         ${result.open_unrealized_pnl:>12,.2f}")

    # Statistical significance
    sig = result.statistical_significance()
    print()
    print(f"T-statistic:            {sig['t_stat']:>12.3f}")
    print(f"P-value (returns):      {sig['p_value']:>12.4f}")
    print(f"P-value (win rate):     {sig['binom_p']:>12.4f}")


def run_position_size_analysis(
    test_df: pd.DataFrame,
    whale_set: Set[str],
    resolution_data: Dict[str, Dict[str, Any]],
    position_sizes: List[float] = None,
    total_capital: float = 100000,
    cost_categories: List[str] = None,
    include_open: bool = False,
) -> pd.DataFrame:
    """
    Analyze performance across different position sizes.

    Args:
        test_df: Test period bets DataFrame
        whale_set: Set of whale user IDs
        resolution_data: Market resolution mapping
        position_sizes: List of position sizes to test
        total_capital: Total capital available
        cost_categories: Cost model categories to test

    Returns:
        DataFrame with results for each position size
    """
    if position_sizes is None:
        position_sizes = [100, 250, 500, 1000, 2500, 5000, 10000]

    if cost_categories is None:
        cost_categories = ["small", "medium", "large"]

    results = []

    for pos_size in position_sizes:
        # Determine appropriate cost category
        if pos_size <= 500:
            cost_cat = "small"
        elif pos_size <= 5000:
            cost_cat = "medium"
        else:
            cost_cat = "large"

        cost_model = CostModel.from_assumptions(cost_cat)
        min_liq = COST_ASSUMPTIONS[cost_cat].liquidity_threshold

        result = run_backtest(
            test_df=test_df,
            whale_set=whale_set,
            resolution_data=resolution_data,
            strategy_name=f"${pos_size:,}",
            cost_model=cost_model,
            position_size=pos_size,
            min_liquidity=min_liq,
            include_open=include_open,
        )

        if result:
            max_positions = int(total_capital / pos_size)
            capital_utilization = min(
                result.avg_concurrent_positions * pos_size / total_capital * 100,
                100
            )

            results.append({
                "position_size": pos_size,
                "cost_category": cost_cat,
                "trades": result.total_trades,
                "win_rate": result.win_rate * 100,
                "gross_pnl": result.total_gross_pnl,
                "costs": result.total_costs,
                "net_pnl": result.total_net_pnl,
                "cost_pct": result.total_costs / result.total_gross_pnl * 100 if result.total_gross_pnl > 0 else 0,
                "pnl_per_trade": result.total_net_pnl / result.total_trades if result.total_trades > 0 else 0,
                "avg_holding_days": result.avg_holding_days,
                "max_concurrent": result.max_concurrent_positions,
                "avg_concurrent": result.avg_concurrent_positions,
                "max_positions_allowed": max_positions,
                "capital_utilization": capital_utilization,
                "peak_capital": result.max_concurrent_positions * pos_size,
            })

    return pd.DataFrame(results)


def run_rolling_backtest(
    df: pd.DataFrame,
    resolution_data: Dict[str, Dict[str, Any]],
    method: str = "volume_pct95",
    lookback_months: int = 3,
    position_size: float = 100,
    cost_model: Optional[CostModel] = None,
    include_open: bool = False,
) -> pd.DataFrame:
    """
    Run rolling monthly backtest with whale recalculation each month.

    Args:
        df: Full bets DataFrame
        resolution_data: Market resolution mapping
        method: Whale identification method
        lookback_months: Months of history for whale identification
        position_size: Position size in dollars
        cost_model: Cost model to use

    Returns:
        DataFrame with monthly results
    """
    from .strategy import identify_whales

    if cost_model is None:
        cost_model = DEFAULT_COST_MODEL

    # Get unique months
    months = sorted(df["month"].unique())
    results = []

    print(f"\nRolling Monthly Backtest ({lookback_months}-month lookback)")
    print("-" * 70)
    print(f"{'Month':<10} | {'Whales':>6} | {'Trades':>6} | {'Win%':>6} | {'Gross':>10} | {'Net':>10}")
    print("-" * 70)

    for i, month in enumerate(months):
        if i < lookback_months:
            continue

        # Training data: previous N months
        train_months = months[i - lookback_months:i]
        train_df = df[df["month"].isin(train_months)]

        # Test data: current month
        test_df = df[df["month"] == month]

        if len(train_df) < 1000 or len(test_df) < 100:
            continue

        # Identify whales from training period
        whales = identify_whales(train_df, resolution_data, method)

        if len(whales) == 0:
            continue

        # Run backtest on test month
        result = run_backtest(
            test_df=test_df,
            whale_set=whales,
            resolution_data=resolution_data,
            strategy_name=str(month),
            cost_model=cost_model,
            position_size=position_size,
            include_open=include_open,
        )

        if result and result.total_trades > 0:
            results.append({
                "month": str(month),
                "whales": len(whales),
                "trades": result.total_trades,
                "win_rate": result.win_rate * 100,
                "gross_pnl": result.total_gross_pnl,
                "net_pnl": result.total_net_pnl,
                "avg_holding_days": result.avg_holding_days,
            })

            print(
                f"{month!s:<10} | {len(whales):>6} | {result.total_trades:>6} | "
                f"{result.win_rate*100:>5.1f}% | ${result.total_gross_pnl:>9,.0f} | "
                f"${result.total_net_pnl:>9,.0f}"
            )

    results_df = pd.DataFrame(results)

    if len(results_df) > 0:
        print("-" * 70)
        print(
            f"{'TOTAL':<10} | {results_df['whales'].mean():>6.0f} | "
            f"{results_df['trades'].sum():>6} | {results_df['win_rate'].mean():>5.1f}% | "
            f"${results_df['gross_pnl'].sum():>9,.0f} | ${results_df['net_pnl'].sum():>9,.0f}"
        )
        print()
        print(f"Average whales per month: {results_df['whales'].mean():.0f}")
        print(f"Total trades: {results_df['trades'].sum():,}")
        print(f"Average monthly win rate: {results_df['win_rate'].mean():.1f}%")
        print(f"Total net P&L: ${results_df['net_pnl'].sum():,.0f}")

    return results_df


def print_position_size_analysis(df: pd.DataFrame, total_capital: float = 100000) -> None:
    """Print formatted position size analysis."""
    print(f"\n{'='*80}")
    print(f"POSITION SIZE ANALYSIS (Capital: ${total_capital:,.0f})")
    print(f"{'='*80}")
    print()
    print(f"{'Size':>10} | {'Trades':>7} | {'Win%':>6} | {'Net P&L':>12} | {'$/Trade':>8} | {'Hold':>6} | {'MaxPos':>6} | {'Peak$':>10}")
    print("-" * 80)

    for _, row in df.iterrows():
        print(
            f"${row['position_size']:>9,} | "
            f"{row['trades']:>7,} | "
            f"{row['win_rate']:>5.1f}% | "
            f"${row['net_pnl']:>11,.0f} | "
            f"${row['pnl_per_trade']:>7.2f} | "
            f"{row['avg_holding_days']:>5.0f}d | "
            f"{row['max_concurrent']:>6,} | "
            f"${row['peak_capital']:>9,.0f}"
        )

    print()
    print("Note: Larger positions require more liquidity, reducing tradeable markets.")
    print("Peak$ = Maximum capital deployed at any point (MaxPos * Size)")
