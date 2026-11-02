"""
Performance Attribution Analysis

Breaks down performance by:
- Strategy
- Market category
- Time period
- Market characteristics (volume, liquidity, expiry)
- Trade size
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import pandas as pd
import numpy as np


@dataclass
class AttributionBreakdown:
    """Performance breakdown for a single dimension."""
    
    dimension: str
    category: str
    trade_count: int
    win_rate: float
    total_pnl: float
    avg_pnl: float
    sharpe_ratio: float
    max_drawdown: float
    profit_factor: float
    avg_holding_days: float


@dataclass
class AttributionReport:
    """Complete attribution analysis report."""
    
    by_strategy: Dict[str, AttributionBreakdown]
    by_category: Dict[str, AttributionBreakdown]
    by_time_period: Dict[str, AttributionBreakdown]
    by_volume_tier: Dict[str, AttributionBreakdown]
    by_expiry_window: Dict[str, AttributionBreakdown]
    by_trade_size: Dict[str, AttributionBreakdown]
    overall: AttributionBreakdown


def calculate_sharpe(returns: pd.Series) -> float:
    """Calculate Sharpe ratio from returns."""
    if len(returns) < 2 or returns.std() == 0:
        return 0.0
    return (returns.mean() / returns.std()) * np.sqrt(252)


def calculate_max_drawdown(cumulative_pnl: pd.Series) -> float:
    """Calculate maximum drawdown from cumulative PnL."""
    if len(cumulative_pnl) == 0:
        return 0.0
    running_max = cumulative_pnl.expanding().max()
    drawdown = (cumulative_pnl - running_max) / running_max.clip(lower=0.01)
    return abs(drawdown.min())


def calculate_profit_factor(winners: pd.Series, losers: pd.Series) -> float:
    """Calculate profit factor."""
    gross_profit = winners.sum() if len(winners) > 0 else 0.0
    gross_loss = abs(losers.sum()) if len(losers) > 0 else 0.0
    if gross_loss == 0:
        return float('inf') if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def analyze_strategy_attribution(
    trades_df: pd.DataFrame,
    pnl_column: str = "net_pnl",
    strategy_column: str = "strategy",
) -> Dict[str, AttributionBreakdown]:
    """Break down performance by strategy."""
    
    if strategy_column not in trades_df.columns:
        return {}
    
    breakdowns = {}
    
    for strategy in trades_df[strategy_column].unique():
        if pd.isna(strategy):
            continue
            
        strategy_trades = trades_df[trades_df[strategy_column] == strategy].copy()
        
        if len(strategy_trades) == 0:
            continue
        
        pnl = strategy_trades[pnl_column]
        winners = pnl[pnl > 0]
        losers = pnl[pnl <= 0]
        
        win_rate = len(winners) / len(strategy_trades) if len(strategy_trades) > 0 else 0.0
        total_pnl = pnl.sum()
        avg_pnl = pnl.mean()
        
        # Calculate daily returns for Sharpe
        if "entry_time" in strategy_trades.columns:
            daily_returns = _calculate_daily_returns(strategy_trades, pnl_column)
            sharpe = calculate_sharpe(daily_returns) if len(daily_returns) > 1 else 0.0
        else:
            sharpe = 0.0
        
        # Max drawdown
        cumulative = pnl.cumsum()
        max_dd = calculate_max_drawdown(cumulative)
        
        # Profit factor
        profit_factor = calculate_profit_factor(winners, losers)
        
        # Avg holding days
        avg_holding = _calculate_avg_holding_days(strategy_trades)
        
        breakdowns[strategy] = AttributionBreakdown(
            dimension="strategy",
            category=str(strategy),
            trade_count=len(strategy_trades),
            win_rate=win_rate,
            total_pnl=total_pnl,
            avg_pnl=avg_pnl,
            sharpe_ratio=sharpe,
            max_drawdown=max_dd,
            profit_factor=profit_factor,
            avg_holding_days=avg_holding,
        )
    
    return breakdowns


def analyze_category_attribution(
    trades_df: pd.DataFrame,
    market_categories: Optional[Dict[str, str]] = None,
    pnl_column: str = "net_pnl",
    market_id_column: str = "market_id",
) -> Dict[str, AttributionBreakdown]:
    """Break down performance by market category."""
    
    if market_categories is None:
        # Try to infer from trades_df
        if "category" in trades_df.columns:
            market_categories = trades_df.set_index(market_id_column)["category"].to_dict()
        else:
            return {}
    
    if market_id_column not in trades_df.columns:
        return {}
    
    breakdowns = {}
    
    # Map trades to categories
    trades_df = trades_df.copy()
    trades_df["_category"] = trades_df[market_id_column].map(market_categories).fillna("Unknown")
    
    for category in trades_df["_category"].unique():
        if pd.isna(category):
            continue
        
        category_trades = trades_df[trades_df["_category"] == category].copy()
        
        if len(category_trades) == 0:
            continue
        
        pnl = category_trades[pnl_column]
        winners = pnl[pnl > 0]
        losers = pnl[pnl <= 0]
        
        win_rate = len(winners) / len(category_trades) if len(category_trades) > 0 else 0.0
        total_pnl = pnl.sum()
        avg_pnl = pnl.mean()
        
        # Calculate daily returns for Sharpe
        if "entry_time" in category_trades.columns:
            daily_returns = _calculate_daily_returns(category_trades, pnl_column)
            sharpe = calculate_sharpe(daily_returns) if len(daily_returns) > 1 else 0.0
        else:
            sharpe = 0.0
        
        # Max drawdown
        cumulative = pnl.cumsum()
        max_dd = calculate_max_drawdown(cumulative)
        
        # Profit factor
        profit_factor = calculate_profit_factor(winners, losers)
        
        # Avg holding days
        avg_holding = _calculate_avg_holding_days(category_trades)
        
        breakdowns[str(category)] = AttributionBreakdown(
            dimension="category",
            category=str(category),
            trade_count=len(category_trades),
            win_rate=win_rate,
            total_pnl=total_pnl,
            avg_pnl=avg_pnl,
            sharpe_ratio=sharpe,
            max_drawdown=max_dd,
            profit_factor=profit_factor,
            avg_holding_days=avg_holding,
        )
    
    return breakdowns


def analyze_time_period_attribution(
    trades_df: pd.DataFrame,
    pnl_column: str = "net_pnl",
    time_column: str = "entry_time",
    period: str = "month",  # "day", "week", "month", "quarter", "year"
) -> Dict[str, AttributionBreakdown]:
    """Break down performance by time period."""
    
    if time_column not in trades_df.columns:
        return {}
    
    trades_df = trades_df.copy()
    trades_df[time_column] = pd.to_datetime(trades_df[time_column], errors="coerce")
    
    # Group by time period
    if period == "day":
        trades_df["_period"] = trades_df[time_column].dt.date
    elif period == "week":
        trades_df["_period"] = trades_df[time_column].dt.to_period("W").astype(str)
    elif period == "month":
        trades_df["_period"] = trades_df[time_column].dt.to_period("M").astype(str)
    elif period == "quarter":
        trades_df["_period"] = trades_df[time_column].dt.to_period("Q").astype(str)
    elif period == "year":
        trades_df["_period"] = trades_df[time_column].dt.year.astype(str)
    else:
        return {}
    
    breakdowns = {}
    
    for period_label in trades_df["_period"].unique():
        if pd.isna(period_label):
            continue
        
        period_trades = trades_df[trades_df["_period"] == period_label].copy()
        
        if len(period_trades) == 0:
            continue
        
        pnl = period_trades[pnl_column]
        winners = pnl[pnl > 0]
        losers = pnl[pnl <= 0]
        
        win_rate = len(winners) / len(period_trades) if len(period_trades) > 0 else 0.0
        total_pnl = pnl.sum()
        avg_pnl = pnl.mean()
        
        # Calculate daily returns for Sharpe
        daily_returns = _calculate_daily_returns(period_trades, pnl_column)
        sharpe = calculate_sharpe(daily_returns) if len(daily_returns) > 1 else 0.0
        
        # Max drawdown
        cumulative = pnl.cumsum()
        max_dd = calculate_max_drawdown(cumulative)
        
        # Profit factor
        profit_factor = calculate_profit_factor(winners, losers)
        
        # Avg holding days
        avg_holding = _calculate_avg_holding_days(period_trades)
        
        breakdowns[str(period_label)] = AttributionBreakdown(
            dimension=f"time_{period}",
            category=str(period_label),
            trade_count=len(period_trades),
            win_rate=win_rate,
            total_pnl=total_pnl,
            avg_pnl=avg_pnl,
            sharpe_ratio=sharpe,
            max_drawdown=max_dd,
            profit_factor=profit_factor,
            avg_holding_days=avg_holding,
        )
    
    return breakdowns


def analyze_volume_tier_attribution(
    trades_df: pd.DataFrame,
    pnl_column: str = "net_pnl",
    volume_column: str = "usd_amount",
    tiers: Optional[List[float]] = None,
) -> Dict[str, AttributionBreakdown]:
    """Break down performance by market volume tier."""
    
    if volume_column not in trades_df.columns:
        return {}
    
    if tiers is None:
        # Default tiers: Low (<10k), Medium (10k-100k), High (>100k)
        tiers = [0, 10000, 100000, float('inf')]
    
    trades_df = trades_df.copy()
    
    # Get market volumes (need to aggregate if volume is per-trade)
    if "market_volume" in trades_df.columns:
        market_volumes = trades_df.groupby("market_id")["market_volume"].first()
    else:
        # Estimate from trade sizes
        market_volumes = trades_df.groupby("market_id")[volume_column].sum()
    
    trades_df["_volume_tier"] = pd.cut(
        trades_df["market_id"].map(market_volumes),
        bins=tiers,
        labels=["Low", "Medium", "High"],
        include_lowest=True,
    ).cat.add_categories("Unknown").fillna("Unknown")
    
    breakdowns = {}
    
    for tier in trades_df["_volume_tier"].unique():
        if pd.isna(tier):
            continue
        
        tier_trades = trades_df[trades_df["_volume_tier"] == tier].copy()
        
        if len(tier_trades) == 0:
            continue
        
        pnl = tier_trades[pnl_column]
        winners = pnl[pnl > 0]
        losers = pnl[pnl <= 0]
        
        win_rate = len(winners) / len(tier_trades) if len(tier_trades) > 0 else 0.0
        total_pnl = pnl.sum()
        avg_pnl = pnl.mean()
        
        # Calculate daily returns for Sharpe
        if "entry_time" in tier_trades.columns:
            daily_returns = _calculate_daily_returns(tier_trades, pnl_column)
            sharpe = calculate_sharpe(daily_returns) if len(daily_returns) > 1 else 0.0
        else:
            sharpe = 0.0
        
        # Max drawdown
        cumulative = pnl.cumsum()
        max_dd = calculate_max_drawdown(cumulative)
        
        # Profit factor
        profit_factor = calculate_profit_factor(winners, losers)
        
        # Avg holding days
        avg_holding = _calculate_avg_holding_days(tier_trades)
        
        breakdowns[str(tier)] = AttributionBreakdown(
            dimension="volume_tier",
            category=str(tier),
            trade_count=len(tier_trades),
            win_rate=win_rate,
            total_pnl=total_pnl,
            avg_pnl=avg_pnl,
            sharpe_ratio=sharpe,
            max_drawdown=max_dd,
            profit_factor=profit_factor,
            avg_holding_days=avg_holding,
        )
    
    return breakdowns


def analyze_expiry_window_attribution(
    trades_df: pd.DataFrame,
    pnl_column: str = "net_pnl",
    entry_time_column: str = "entry_time",
    expiry_column: Optional[str] = None,
) -> Dict[str, AttributionBreakdown]:
    """Break down performance by days until expiry."""
    
    if entry_time_column not in trades_df.columns:
        return {}
    
    # Try to get expiry info
    if expiry_column and expiry_column in trades_df.columns:
        trades_df = trades_df.copy()
        trades_df[entry_time_column] = pd.to_datetime(trades_df[entry_time_column], errors="coerce")
        trades_df[expiry_column] = pd.to_datetime(trades_df[expiry_column], errors="coerce")
        trades_df["_days_to_expiry"] = (
            trades_df[expiry_column] - trades_df[entry_time_column]
        ).dt.days
    elif "days_to_expiry" in trades_df.columns:
        trades_df = trades_df.copy()
        trades_df["_days_to_expiry"] = trades_df["days_to_expiry"]
    else:
        return {}
    
    # Bin into windows
    bins = [0, 1, 3, 7, 14, 30, float('inf')]
    labels = ["<1 day", "1-3 days", "3-7 days", "7-14 days", "14-30 days", "30+ days"]
    
    trades_df["_expiry_window"] = pd.cut(
        trades_df["_days_to_expiry"],
        bins=bins,
        labels=labels,
        include_lowest=True,
    ).cat.add_categories("Unknown").fillna("Unknown")
    
    breakdowns = {}
    
    for window in trades_df["_expiry_window"].unique():
        if pd.isna(window):
            continue
        
        window_trades = trades_df[trades_df["_expiry_window"] == window].copy()
        
        if len(window_trades) == 0:
            continue
        
        pnl = window_trades[pnl_column]
        winners = pnl[pnl > 0]
        losers = pnl[pnl <= 0]
        
        win_rate = len(winners) / len(window_trades) if len(window_trades) > 0 else 0.0
        total_pnl = pnl.sum()
        avg_pnl = pnl.mean()
        
        # Calculate daily returns for Sharpe
        daily_returns = _calculate_daily_returns(window_trades, pnl_column)
        sharpe = calculate_sharpe(daily_returns) if len(daily_returns) > 1 else 0.0
        
        # Max drawdown
        cumulative = pnl.cumsum()
        max_dd = calculate_max_drawdown(cumulative)
        
        # Profit factor
        profit_factor = calculate_profit_factor(winners, losers)
        
        # Avg holding days
        avg_holding = _calculate_avg_holding_days(window_trades)
        
        breakdowns[str(window)] = AttributionBreakdown(
            dimension="expiry_window",
            category=str(window),
            trade_count=len(window_trades),
            win_rate=win_rate,
            total_pnl=total_pnl,
            avg_pnl=avg_pnl,
            sharpe_ratio=sharpe,
            max_drawdown=max_dd,
            profit_factor=profit_factor,
            avg_holding_days=avg_holding,
        )
    
    return breakdowns


def analyze_trade_size_attribution(
    trades_df: pd.DataFrame,
    pnl_column: str = "net_pnl",
    size_column: str = "size",
    tiers: Optional[List[float]] = None,
) -> Dict[str, AttributionBreakdown]:
    """Break down performance by trade size tier."""
    
    if size_column not in trades_df.columns:
        return {}
    
    if tiers is None:
        # Default tiers based on quartiles
        sizes = trades_df[size_column].dropna()
        if len(sizes) > 0:
            q25, q50, q75 = sizes.quantile([0.25, 0.5, 0.75])
            tiers = [0, q25, q50, q75, float('inf')]
        else:
            tiers = [0, 100, 250, 500, float('inf')]
    
    trades_df = trades_df.copy()
    trades_df["_size_tier"] = pd.cut(
        trades_df[size_column],
        bins=tiers,
        labels=["Small", "Medium", "Large", "Very Large"],
        include_lowest=True,
    ).cat.add_categories("Unknown").fillna("Unknown")
    
    breakdowns = {}
    
    for tier in trades_df["_size_tier"].unique():
        if pd.isna(tier):
            continue
        
        tier_trades = trades_df[trades_df["_size_tier"] == tier].copy()
        
        if len(tier_trades) == 0:
            continue
        
        pnl = tier_trades[pnl_column]
        winners = pnl[pnl > 0]
        losers = pnl[pnl <= 0]
        
        win_rate = len(winners) / len(tier_trades) if len(tier_trades) > 0 else 0.0
        total_pnl = pnl.sum()
        avg_pnl = pnl.mean()
        
        # Calculate daily returns for Sharpe
        if "entry_time" in tier_trades.columns:
            daily_returns = _calculate_daily_returns(tier_trades, pnl_column)
            sharpe = calculate_sharpe(daily_returns) if len(daily_returns) > 1 else 0.0
        else:
            sharpe = 0.0
        
        # Max drawdown
        cumulative = pnl.cumsum()
        max_dd = calculate_max_drawdown(cumulative)
        
        # Profit factor
        profit_factor = calculate_profit_factor(winners, losers)
        
        # Avg holding days
        avg_holding = _calculate_avg_holding_days(tier_trades)
        
        breakdowns[str(tier)] = AttributionBreakdown(
            dimension="trade_size",
            category=str(tier),
            trade_count=len(tier_trades),
            win_rate=win_rate,
            total_pnl=total_pnl,
            avg_pnl=avg_pnl,
            sharpe_ratio=sharpe,
            max_drawdown=max_dd,
            profit_factor=profit_factor,
            avg_holding_days=avg_holding,
        )
    
    return breakdowns


def _calculate_daily_returns(
    trades_df: pd.DataFrame,
    pnl_column: str,
    time_column: str = "entry_time",
) -> pd.Series:
    """Calculate daily returns from trades."""
    if time_column not in trades_df.columns:
        return pd.Series(dtype=float)
    
    trades_df = trades_df.copy()
    trades_df[time_column] = pd.to_datetime(trades_df[time_column], errors="coerce")
    trades_df["_date"] = trades_df[time_column].dt.date
    
    daily_pnl = trades_df.groupby("_date")[pnl_column].sum()
    daily_returns = daily_pnl / abs(daily_pnl).sum() if daily_pnl.sum() != 0 else daily_pnl * 0
    
    return daily_returns


def _calculate_avg_holding_days(trades_df: pd.DataFrame) -> float:
    """Calculate average holding days."""
    if "entry_time" in trades_df.columns and "exit_time" in trades_df.columns:
        entry = pd.to_datetime(trades_df["entry_time"], errors="coerce")
        exit = pd.to_datetime(trades_df["exit_time"], errors="coerce")
        holding_days = (exit - entry).dt.total_seconds() / 86400
        return holding_days.mean() if len(holding_days) > 0 else 0.0
    elif "holding_days" in trades_df.columns:
        return trades_df["holding_days"].mean() if len(trades_df) > 0 else 0.0
    else:
        return 0.0


def generate_attribution_report(
    trades_df: pd.DataFrame,
    market_categories: Optional[Dict[str, str]] = None,
    pnl_column: str = "net_pnl",
    strategy_column: str = "strategy",
    time_period: str = "month",
) -> AttributionReport:
    """
    Generate a complete performance attribution report.
    
    Args:
        trades_df: DataFrame with trade data
        market_categories: Optional dict mapping market_id to category
        pnl_column: Column name for PnL
        strategy_column: Column name for strategy
        time_period: Time period for breakdown ("day", "week", "month", "quarter", "year")
    
    Returns:
        AttributionReport with all breakdowns
    """
    
    # Handle empty DataFrame
    if trades_df is None or trades_df.empty:
        empty_breakdown = AttributionBreakdown(
            dimension="overall",
            category="All",
            trade_count=0,
            win_rate=0.0,
            total_pnl=0.0,
            avg_pnl=0.0,
            sharpe_ratio=0.0,
            max_drawdown=0.0,
            profit_factor=0.0,
            avg_holding_days=0.0,
        )
        return AttributionReport(
            by_strategy={},
            by_category={},
            by_time_period={},
            by_volume_tier={},
            by_expiry_window={},
            by_trade_size={},
            overall=empty_breakdown,
        )
    
    # Overall performance
    if pnl_column not in trades_df.columns:
        raise ValueError(f"Column '{pnl_column}' not found in trades_df")
    
    pnl = trades_df[pnl_column]
    winners = pnl[pnl > 0]
    losers = pnl[pnl <= 0]
    
    win_rate = len(winners) / len(trades_df) if len(trades_df) > 0 else 0.0
    total_pnl = pnl.sum()
    avg_pnl = pnl.mean()
    
    daily_returns = _calculate_daily_returns(trades_df, pnl_column)
    sharpe = calculate_sharpe(daily_returns) if len(daily_returns) > 1 else 0.0
    
    cumulative = pnl.cumsum()
    max_dd = calculate_max_drawdown(cumulative)
    
    profit_factor = calculate_profit_factor(winners, losers)
    avg_holding = _calculate_avg_holding_days(trades_df)
    
    overall = AttributionBreakdown(
        dimension="overall",
        category="All",
        trade_count=len(trades_df),
        win_rate=win_rate,
        total_pnl=total_pnl,
        avg_pnl=avg_pnl,
        sharpe_ratio=sharpe,
        max_drawdown=max_dd,
        profit_factor=profit_factor,
        avg_holding_days=avg_holding,
    )
    
    # Breakdowns
    by_strategy = analyze_strategy_attribution(trades_df, pnl_column, strategy_column)
    by_category = analyze_category_attribution(trades_df, market_categories, pnl_column)
    by_time_period = analyze_time_period_attribution(trades_df, pnl_column, period=time_period)
    by_volume_tier = analyze_volume_tier_attribution(trades_df, pnl_column)
    by_expiry_window = analyze_expiry_window_attribution(trades_df, pnl_column)
    by_trade_size = analyze_trade_size_attribution(trades_df, pnl_column)
    
    return AttributionReport(
        by_strategy=by_strategy,
        by_category=by_category,
        by_time_period=by_time_period,
        by_volume_tier=by_volume_tier,
        by_expiry_window=by_expiry_window,
        by_trade_size=by_trade_size,
        overall=overall,
    )


def attribution_report_to_dataframe(report: AttributionReport) -> pd.DataFrame:
    """Convert attribution report to DataFrame for easy viewing."""
    
    rows = []
    
    # Overall
    rows.append({
        "dimension": report.overall.dimension,
        "category": report.overall.category,
        "trade_count": report.overall.trade_count,
        "win_rate": report.overall.win_rate,
        "total_pnl": report.overall.total_pnl,
        "avg_pnl": report.overall.avg_pnl,
        "sharpe_ratio": report.overall.sharpe_ratio,
        "max_drawdown": report.overall.max_drawdown,
        "profit_factor": report.overall.profit_factor,
        "avg_holding_days": report.overall.avg_holding_days,
    })
    
    # By strategy
    for breakdown in report.by_strategy.values():
        rows.append({
            "dimension": breakdown.dimension,
            "category": breakdown.category,
            "trade_count": breakdown.trade_count,
            "win_rate": breakdown.win_rate,
            "total_pnl": breakdown.total_pnl,
            "avg_pnl": breakdown.avg_pnl,
            "sharpe_ratio": breakdown.sharpe_ratio,
            "max_drawdown": breakdown.max_drawdown,
            "profit_factor": breakdown.profit_factor,
            "avg_holding_days": breakdown.avg_holding_days,
        })
    
    # By category
    for breakdown in report.by_category.values():
        rows.append({
            "dimension": breakdown.dimension,
            "category": breakdown.category,
            "trade_count": breakdown.trade_count,
            "win_rate": breakdown.win_rate,
            "total_pnl": breakdown.total_pnl,
            "avg_pnl": breakdown.avg_pnl,
            "sharpe_ratio": breakdown.sharpe_ratio,
            "max_drawdown": breakdown.max_drawdown,
            "profit_factor": breakdown.profit_factor,
            "avg_holding_days": breakdown.avg_holding_days,
        })
    
    # By time period
    for breakdown in report.by_time_period.values():
        rows.append({
            "dimension": breakdown.dimension,
            "category": breakdown.category,
            "trade_count": breakdown.trade_count,
            "win_rate": breakdown.win_rate,
            "total_pnl": breakdown.total_pnl,
            "avg_pnl": breakdown.avg_pnl,
            "sharpe_ratio": breakdown.sharpe_ratio,
            "max_drawdown": breakdown.max_drawdown,
            "profit_factor": breakdown.profit_factor,
            "avg_holding_days": breakdown.avg_holding_days,
        })
    
    # By volume tier
    for breakdown in report.by_volume_tier.values():
        rows.append({
            "dimension": breakdown.dimension,
            "category": breakdown.category,
            "trade_count": breakdown.trade_count,
            "win_rate": breakdown.win_rate,
            "total_pnl": breakdown.total_pnl,
            "avg_pnl": breakdown.avg_pnl,
            "sharpe_ratio": breakdown.sharpe_ratio,
            "max_drawdown": breakdown.max_drawdown,
            "profit_factor": breakdown.profit_factor,
            "avg_holding_days": breakdown.avg_holding_days,
        })
    
    # By expiry window
    for breakdown in report.by_expiry_window.values():
        rows.append({
            "dimension": breakdown.dimension,
            "category": breakdown.category,
            "trade_count": breakdown.trade_count,
            "win_rate": breakdown.win_rate,
            "total_pnl": breakdown.total_pnl,
            "avg_pnl": breakdown.avg_pnl,
            "sharpe_ratio": breakdown.sharpe_ratio,
            "max_drawdown": breakdown.max_drawdown,
            "profit_factor": breakdown.profit_factor,
            "avg_holding_days": breakdown.avg_holding_days,
        })
    
    # By trade size
    for breakdown in report.by_trade_size.values():
        rows.append({
            "dimension": breakdown.dimension,
            "category": breakdown.category,
            "trade_count": breakdown.trade_count,
            "win_rate": breakdown.win_rate,
            "total_pnl": breakdown.total_pnl,
            "avg_pnl": breakdown.avg_pnl,
            "sharpe_ratio": breakdown.sharpe_ratio,
            "max_drawdown": breakdown.max_drawdown,
            "profit_factor": breakdown.profit_factor,
            "avg_holding_days": breakdown.avg_holding_days,
        })
    
    return pd.DataFrame(rows)
