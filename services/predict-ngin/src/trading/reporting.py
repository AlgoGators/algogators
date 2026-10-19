"""
Reporting utilities including QuantStats HTML report generation.
Unified reporting for backtests and paper trading.
"""

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, List, Dict, Any, Iterable
import warnings

import numpy as np
import pandas as pd


@dataclass
class RunMetadata:
    """Metadata describing a backtest or paper trading run."""

    strategy_name: str
    run_type: str = "backtest"
    as_of: Optional[pd.Timestamp] = None
    start_date: Optional[pd.Timestamp] = None
    end_date: Optional[pd.Timestamp] = None
    position_size: Optional[float] = None
    starting_capital: Optional[float] = None
    cost_model: Optional[str] = None
    notes: Optional[str] = None


@dataclass
class RunMetrics:
    """Standardized performance metrics for a run."""

    total_trades: int = 0
    unique_markets: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_gross_pnl: float = 0.0
    total_net_pnl: float = 0.0
    total_costs: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    roi_pct: float = 0.0
    avg_holding_days: float = 0.0
    max_concurrent_positions: int = 0
    avg_concurrent_positions: float = 0.0
    open_positions: int = 0
    open_capital: float = 0.0
    open_unrealized_pnl: float = 0.0
    closed_trades: int = 0
    avg_position_size: float = 0.0
    max_position_size: float = 0.0
    max_concurrent_capital: float = 0.0


@dataclass
class RunDiagnostics:
    """Diagnostics and data-quality checks for a run."""

    issues: List[str] = field(default_factory=list)
    missing_columns: List[str] = field(default_factory=list)
    rows_missing_pnl: int = 0
    rows_invalid_prices: int = 0
    rows_negative_holding: int = 0
    rows_missing_times: int = 0
    duplicate_markets: int = 0


@dataclass
class RunSummary:
    """Bundle of metadata, metrics, and diagnostics."""

    metadata: RunMetadata
    metrics: RunMetrics
    diagnostics: RunDiagnostics


def _infer_datetime_series(trades_df: pd.DataFrame) -> Optional[pd.Series]:
    for col in ("datetime", "date", "timestamp"):
        if col in trades_df.columns:
            series = pd.to_datetime(trades_df[col], errors="coerce")
            if series.notna().any():
                return series
    return None


def _infer_date_series(trades_df: pd.DataFrame) -> Optional[pd.Series]:
    series = _infer_datetime_series(trades_df)
    if series is None:
        return None
    return series.dt.date


def _sort_trades(trades_df: pd.DataFrame) -> pd.DataFrame:
    for col in ("datetime", "date", "entry_time"):
        if col in trades_df.columns:
            return trades_df.sort_values(col)
    return trades_df


def _series_or_zero(trades_df: pd.DataFrame, column: str) -> pd.Series:
    if column in trades_df.columns:
        return trades_df[column].fillna(0.0)
    return pd.Series(0.0, index=trades_df.index)


def _position_size_column(trades_df: pd.DataFrame) -> Optional[str]:
    for col in ("position_size", "size"):
        if col in trades_df.columns:
            return col
    return None


def _format_timestamp(value: Optional[pd.Timestamp]) -> str:
    if value is None:
        return ""
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return str(value)


def infer_run_window(trades_df: pd.DataFrame) -> tuple[Optional[pd.Timestamp], Optional[pd.Timestamp]]:
    """Return start/end timestamps inferred from trades."""
    if trades_df is None or trades_df.empty:
        return None, None
    series = _infer_datetime_series(trades_df)
    if series is None:
        return None, None
    return series.min(), series.max()


def compute_daily_returns(
    trades_df: pd.DataFrame,
    starting_capital: Optional[float],
    pnl_column: str = "net_pnl",
) -> pd.Series:
    """Compute daily returns from a trades DataFrame."""
    if trades_df is None or trades_df.empty:
        return pd.Series(dtype=float)
    if starting_capital is None or starting_capital == 0:
        return pd.Series(dtype=float)
    if pnl_column not in trades_df.columns:
        return pd.Series(dtype=float)

    date_series = _infer_date_series(trades_df)
    if date_series is None:
        return pd.Series(dtype=float)

    daily_pnl = trades_df.groupby(date_series)[pnl_column].sum()
    daily_returns = daily_pnl / starting_capital
    daily_returns.index = pd.to_datetime(daily_returns.index)
    return daily_returns.sort_index()


def _calculate_concurrent_positions(
    trades_df: pd.DataFrame,
    entry_col: str = "entry_time",
    exit_col: str = "exit_time",
    as_of: Optional[pd.Timestamp] = None,
) -> pd.Series:
    """Calculate number of concurrent open positions over time."""
    if entry_col not in trades_df.columns or exit_col not in trades_df.columns:
        return pd.Series([0])

    events = []
    for _, row in trades_df.iterrows():
        entry_time = pd.to_datetime(row[entry_col], errors="coerce")
        exit_time = pd.to_datetime(row[exit_col], errors="coerce")

        if pd.isna(entry_time):
            continue
        if pd.isna(exit_time):
            if as_of is None:
                continue
            exit_time = as_of

        events.append((entry_time, 1))  # Open position
        events.append((exit_time, -1))  # Close position

    if not events:
        return pd.Series([0])

    events.sort(key=lambda x: x[0])

    concurrent = 0
    concurrent_series = []
    for _, delta in events:
        concurrent += delta
        concurrent_series.append(concurrent)

    return pd.Series(concurrent_series)


def _calculate_concurrent_capital(
    trades_df: pd.DataFrame,
    size_col: Optional[str],
    entry_col: str = "entry_time",
    exit_col: str = "exit_time",
    as_of: Optional[pd.Timestamp] = None,
) -> pd.Series:
    """Calculate concurrent capital deployed over time."""
    if size_col is None:
        return pd.Series([0])
    if entry_col not in trades_df.columns or exit_col not in trades_df.columns:
        return pd.Series([0])

    events = []
    for _, row in trades_df.iterrows():
        entry_time = pd.to_datetime(row[entry_col], errors="coerce")
        exit_time = pd.to_datetime(row[exit_col], errors="coerce")

        if pd.isna(entry_time):
            continue
        if pd.isna(exit_time):
            if as_of is None:
                continue
            exit_time = as_of

        size = row.get(size_col, 0.0)
        if pd.isna(size):
            size = 0.0

        events.append((entry_time, size))
        events.append((exit_time, -size))

    if not events:
        return pd.Series([0])

    events.sort(key=lambda x: x[0])

    deployed = 0.0
    deployed_series = []
    for _, delta in events:
        deployed += delta
        deployed_series.append(deployed)

    return pd.Series(deployed_series)


def compute_run_metrics(
    trades_df: pd.DataFrame,
    starting_capital: Optional[float] = None,
    position_size: Optional[float] = None,
    daily_returns: Optional[pd.Series] = None,
    as_of: Optional[pd.Timestamp] = None,
) -> RunMetrics:
    """Compute standardized run metrics from a trades DataFrame."""
    if trades_df is None or trades_df.empty:
        return RunMetrics()

    trades_df = trades_df.copy()

    total_trades = len(trades_df)
    unique_markets = (
        trades_df["contract_id"].nunique()
        if "contract_id" in trades_df.columns
        else total_trades
    )

    if "status" in trades_df.columns:
        closed_df = trades_df[trades_df["status"] == "closed"]
        open_df = trades_df[trades_df["status"] != "closed"]
    else:
        closed_df = trades_df
        open_df = trades_df.iloc[0:0]

    closed_trades = len(closed_df)
    open_positions = len(open_df)

    closed_net = _series_or_zero(closed_df, "net_pnl")
    winners = closed_df[closed_net > 0]
    losers = closed_df[closed_net <= 0]

    win_rate = len(winners) / len(closed_df) if len(closed_df) > 0 else 0.0

    total_gross = _series_or_zero(trades_df, "gross_pnl").sum()
    total_net = _series_or_zero(trades_df, "net_pnl").sum()

    if "gross_pnl" in closed_df.columns and "net_pnl" in closed_df.columns:
        total_costs = closed_df["gross_pnl"].sum() - closed_df["net_pnl"].sum()
    else:
        total_costs = 0.0

    avg_win = _series_or_zero(winners, "net_pnl").mean() if len(winners) > 0 else 0.0
    avg_loss = _series_or_zero(losers, "net_pnl").mean() if len(losers) > 0 else 0.0

    gross_wins = _series_or_zero(winners, "net_pnl").sum()
    gross_losses = abs(_series_or_zero(losers, "net_pnl").sum())
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf")

    if daily_returns is None:
        daily_returns = compute_daily_returns(trades_df, starting_capital)

    if len(daily_returns) > 1 and daily_returns.std() > 0:
        sharpe = daily_returns.mean() / daily_returns.std() * np.sqrt(252)
    else:
        sharpe = 0.0

    sorted_df = _sort_trades(trades_df)
    if "cumulative_pnl" in sorted_df.columns:
        cumulative = sorted_df["cumulative_pnl"]
    else:
        cumulative = _series_or_zero(sorted_df, "net_pnl").cumsum()
    drawdown = cumulative.cummax() - cumulative
    max_drawdown = drawdown.max() if len(drawdown) > 0 else 0.0

    roi_pct = (
        total_net / starting_capital * 100
        if starting_capital not in (None, 0)
        else 0.0
    )

    if "holding_days" in trades_df.columns:
        avg_holding_days = pd.to_numeric(
            trades_df["holding_days"], errors="coerce"
        ).mean()
    elif "entry_time" in trades_df.columns and "exit_time" in trades_df.columns:
        entry_time = pd.to_datetime(trades_df["entry_time"], errors="coerce")
        exit_time = pd.to_datetime(trades_df["exit_time"], errors="coerce")
        holding_days = (exit_time - entry_time).dt.total_seconds() / 86400
        avg_holding_days = holding_days.mean()
    else:
        avg_holding_days = 0.0

    concurrent_positions = _calculate_concurrent_positions(
        trades_df, as_of=as_of
    )
    max_concurrent = (
        int(concurrent_positions.max()) if len(concurrent_positions) > 0 else 0
    )
    avg_concurrent = (
        concurrent_positions.mean() if len(concurrent_positions) > 0 else 0.0
    )

    size_col = _position_size_column(trades_df)
    if size_col and size_col in open_df.columns:
        open_capital = open_df[size_col].sum()
    elif position_size is not None:
        open_capital = open_positions * position_size
    else:
        open_capital = 0.0

    open_unrealized_pnl = _series_or_zero(open_df, "net_pnl").sum()

    if size_col:
        size_series = pd.to_numeric(trades_df[size_col], errors="coerce")
        avg_position_size = size_series.mean()
        max_position_size = size_series.max()
        if pd.isna(avg_position_size):
            avg_position_size = 0.0
        if pd.isna(max_position_size):
            max_position_size = 0.0
        concurrent_capital = _calculate_concurrent_capital(
            trades_df, size_col, as_of=as_of
        )
        max_concurrent_capital = (
            concurrent_capital.max() if len(concurrent_capital) > 0 else 0.0
        )
        if pd.isna(max_concurrent_capital):
            max_concurrent_capital = 0.0
    elif position_size is not None:
        avg_position_size = position_size
        max_position_size = position_size
        max_concurrent_capital = max_concurrent * position_size
    else:
        avg_position_size = 0.0
        max_position_size = 0.0
        max_concurrent_capital = 0.0

    return RunMetrics(
        total_trades=total_trades,
        unique_markets=unique_markets,
        winning_trades=len(winners),
        losing_trades=len(losers),
        win_rate=win_rate,
        total_gross_pnl=total_gross,
        total_net_pnl=total_net,
        total_costs=total_costs,
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_factor=profit_factor,
        sharpe_ratio=sharpe,
        max_drawdown=max_drawdown,
        roi_pct=roi_pct,
        avg_holding_days=avg_holding_days,
        max_concurrent_positions=max_concurrent,
        avg_concurrent_positions=avg_concurrent,
        open_positions=open_positions,
        open_capital=open_capital,
        open_unrealized_pnl=open_unrealized_pnl,
        closed_trades=closed_trades,
        avg_position_size=avg_position_size,
        max_position_size=max_position_size,
        max_concurrent_capital=max_concurrent_capital,
    )


def run_metrics_from_backtest(result: Any) -> RunMetrics:
    """Create RunMetrics from a BacktestResult-like object."""
    return RunMetrics(
        total_trades=result.total_trades,
        unique_markets=result.unique_markets,
        winning_trades=result.winning_trades,
        losing_trades=result.losing_trades,
        win_rate=result.win_rate,
        total_gross_pnl=result.total_gross_pnl,
        total_net_pnl=result.total_net_pnl,
        total_costs=result.total_costs,
        avg_win=result.avg_win,
        avg_loss=result.avg_loss,
        profit_factor=result.profit_factor,
        sharpe_ratio=result.sharpe_ratio,
        max_drawdown=result.max_drawdown,
        roi_pct=result.roi_pct,
        avg_holding_days=result.avg_holding_days,
        max_concurrent_positions=result.max_concurrent_positions,
        avg_concurrent_positions=result.avg_concurrent_positions,
        open_positions=result.open_positions,
        open_capital=result.open_capital,
        open_unrealized_pnl=result.open_unrealized_pnl,
        closed_trades=result.closed_trades,
        avg_position_size=getattr(result, "avg_position_size", 0.0),
        max_position_size=getattr(result, "max_position_size", 0.0),
        max_concurrent_capital=getattr(result, "max_concurrent_capital", 0.0),
    )


def diagnose_trades(
    trades_df: pd.DataFrame,
    run_type: str = "backtest",
    required_columns: Optional[Iterable[str]] = None,
    enforce_unique_markets: Optional[bool] = None,
) -> RunDiagnostics:
    """Inspect trades for data quality and structural issues."""
    diagnostics = RunDiagnostics()

    if trades_df is None or trades_df.empty:
        diagnostics.issues.append("no_trades")
        return diagnostics

    if required_columns is None:
        required_columns = ("net_pnl", "gross_pnl", "contract_id", "entry_time", "exit_time")

    diagnostics.missing_columns = [
        col for col in required_columns if col not in trades_df.columns
    ]
    if diagnostics.missing_columns:
        diagnostics.issues.append(
            f"missing_columns: {', '.join(diagnostics.missing_columns)}"
        )

    if "net_pnl" in trades_df.columns:
        diagnostics.rows_missing_pnl = int(trades_df["net_pnl"].isna().sum())
        if diagnostics.rows_missing_pnl > 0:
            diagnostics.issues.append(
                f"missing_net_pnl: {diagnostics.rows_missing_pnl}"
            )

    price_cols = [col for col in ("entry_price", "exit_price") if col in trades_df.columns]
    if price_cols:
        invalid = pd.Series(False, index=trades_df.index)
        for col in price_cols:
            invalid = invalid | (trades_df[col] < 0) | (trades_df[col] > 1)
        diagnostics.rows_invalid_prices = int(invalid.sum())
        if diagnostics.rows_invalid_prices > 0:
            diagnostics.issues.append(
                f"invalid_prices: {diagnostics.rows_invalid_prices}"
            )

    if "entry_time" in trades_df.columns and "exit_time" in trades_df.columns:
        entry_time = pd.to_datetime(trades_df["entry_time"], errors="coerce")
        exit_time = pd.to_datetime(trades_df["exit_time"], errors="coerce")
        diagnostics.rows_missing_times = int(entry_time.isna().sum() + exit_time.isna().sum())
        if diagnostics.rows_missing_times > 0:
            diagnostics.issues.append(
                f"missing_times: {diagnostics.rows_missing_times}"
            )

        negative_holding = (exit_time < entry_time)
        diagnostics.rows_negative_holding = int(negative_holding.sum())
        if diagnostics.rows_negative_holding > 0:
            diagnostics.issues.append(
                f"negative_holding: {diagnostics.rows_negative_holding}"
            )

    if enforce_unique_markets is None:
        enforce_unique_markets = run_type == "backtest"

    if enforce_unique_markets and "contract_id" in trades_df.columns:
        diagnostics.duplicate_markets = int(trades_df["contract_id"].duplicated().sum())
        if diagnostics.duplicate_markets > 0:
            diagnostics.issues.append(
                f"duplicate_markets: {diagnostics.duplicate_markets}"
            )

    return diagnostics


def build_run_summary_from_trades(
    strategy_name: str,
    trades_df: pd.DataFrame,
    run_type: str = "paper",
    starting_capital: Optional[float] = None,
    position_size: Optional[float] = None,
    cost_model: Optional[str] = None,
    daily_returns: Optional[pd.Series] = None,
    as_of: Optional[pd.Timestamp] = None,
    notes: Optional[str] = None,
) -> RunSummary:
    """Build a full run summary from raw trades data."""
    if as_of is None:
        as_of = pd.Timestamp.utcnow()

    metrics = compute_run_metrics(
        trades_df=trades_df,
        starting_capital=starting_capital,
        position_size=position_size,
        daily_returns=daily_returns,
        as_of=as_of,
    )
    start_date, end_date = infer_run_window(trades_df)
    metadata = RunMetadata(
        strategy_name=strategy_name,
        run_type=run_type,
        as_of=as_of,
        start_date=start_date,
        end_date=end_date,
        position_size=position_size,
        starting_capital=starting_capital,
        cost_model=cost_model,
        notes=notes,
    )
    diagnostics = diagnose_trades(trades_df, run_type=run_type)
    return RunSummary(metadata=metadata, metrics=metrics, diagnostics=diagnostics)


def build_run_summary_from_backtest(
    result: Any,
    run_type: str = "backtest",
    as_of: Optional[pd.Timestamp] = None,
    cost_model: Optional[str] = None,
    notes: Optional[str] = None,
) -> RunSummary:
    """Build a run summary from a BacktestResult-like object."""
    if as_of is None:
        as_of = pd.Timestamp.utcnow()

    start_date, end_date = infer_run_window(result.trades_df)
    metadata = RunMetadata(
        strategy_name=result.strategy_name,
        run_type=run_type,
        as_of=as_of,
        start_date=start_date,
        end_date=end_date,
        position_size=result.position_size,
        starting_capital=getattr(result, "starting_capital", None),
        cost_model=cost_model,
        notes=notes,
    )
    metrics = run_metrics_from_backtest(result)
    diagnostics = diagnose_trades(result.trades_df, run_type=run_type)
    return RunSummary(metadata=metadata, metrics=metrics, diagnostics=diagnostics)


def _flatten_metadata(metadata: RunMetadata) -> Dict[str, Any]:
    data = asdict(metadata)
    for key in ("as_of", "start_date", "end_date"):
        data[key] = _format_timestamp(data[key])
    return data


def _flatten_diagnostics(diagnostics: RunDiagnostics) -> Dict[str, Any]:
    data = asdict(diagnostics)
    data["issues"] = "; ".join(diagnostics.issues)
    data["missing_columns"] = "; ".join(diagnostics.missing_columns)
    return data


def summaries_to_frame(summaries: List[RunSummary]) -> pd.DataFrame:
    rows = []
    for summary in summaries:
        row = {}
        row.update(_flatten_metadata(summary.metadata))
        row.update(asdict(summary.metrics))
        rows.append(row)
    return pd.DataFrame(rows)


def diagnostics_to_frame(summaries: List[RunSummary]) -> pd.DataFrame:
    rows = []
    for summary in summaries:
        row = {}
        row.update(_flatten_metadata(summary.metadata))
        row.update(_flatten_diagnostics(summary.diagnostics))
        rows.append(row)
    return pd.DataFrame(rows)


def generate_quantstats_report(
    daily_returns: pd.Series,
    output_path: str,
    title: str = "Whale Strategy Report",
    benchmark: Optional[pd.Series] = None,
    is_prices: bool = False,
) -> bool:
    """
    Generate an interactive QuantStats HTML report.

    Args:
        daily_returns: Series of daily returns (or prices if is_prices=True) indexed by date
        output_path: Path to save HTML report
        title: Report title
        benchmark: Optional benchmark returns for comparison
        is_prices: If True, input is equity curve (prices) and will be converted to returns

    Returns:
        True if successful, False otherwise
    """
    try:
        import matplotlib
        matplotlib.rcParams.update({"font.family": "DejaVu Sans", "font.sans-serif": ["DejaVu Sans"]})
        import quantstats as qs
    except ImportError:
        warnings.warn(
            "quantstats not installed. Run: pip install quantstats"
        )
        return False

    if len(daily_returns) < 5:
        warnings.warn("Not enough data points for QuantStats report")
        return False

    # Ensure proper datetime index
    data = pd.Series(
        daily_returns.values,
        index=pd.to_datetime(daily_returns.index)
    ).sort_index()

    # Auto-detect if input looks like prices (values > 1 suggest equity curve, not returns)
    if is_prices or data.mean() > 10:
        # Convert prices (equity curve) to returns
        returns = data.pct_change().dropna()
    else:
        returns = data

    try:
        qs.reports.html(
            returns,
            output=output_path,
            title=title,
            benchmark=benchmark,
        )
        return True
    except Exception as e:
        warnings.warn(f"Failed to generate QuantStats report: {e}")
        return False


def save_trades_csv(
    trades_df: pd.DataFrame,
    output_path: str
) -> None:
    """Save trades DataFrame to CSV."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    trades_df.to_csv(output_path, index=False)


def save_summary_csv(
    results: List[Any],
    output_path: str
) -> None:
    """Save summary results to CSV."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    if results and isinstance(results[0], RunSummary):
        summaries_df = summaries_to_frame(results)
        summaries_df.to_csv(output_path, index=False)
    else:
        pd.DataFrame(results).to_csv(output_path, index=False)


def save_diagnostics_csv(
    summaries: List[RunSummary],
    output_path: str,
) -> None:
    """Save run diagnostics to CSV."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    diagnostics_df = diagnostics_to_frame(summaries)
    diagnostics_df.to_csv(output_path, index=False)


def generate_all_reports(
    results: dict,
    output_dir: str = "data/research/html_reports",
    run_type: str = "backtest",
) -> None:
    """
    Generate all reports for backtest results.

    Args:
        results: Dictionary mapping strategy name to BacktestResult
        output_dir: Output directory for reports
        run_type: Run type label for summary/diagnostics
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    summaries: List[RunSummary] = []

    for name, result in results.items():
        if result is None:
            continue

        # Save trades
        safe_name = name.replace(" ", "_").lower()
        save_trades_csv(result.trades_df, output_path / f"trades_{safe_name}.csv")

        # Generate QuantStats report
        generate_quantstats_report(
            result.daily_returns,
            str(output_path / f"quantstats_{safe_name}.html"),
            title=f"Whale Strategy: {name}"
        )

        # Add to summary + diagnostics
        summary = build_run_summary_from_backtest(result, run_type=run_type)
        summary.metadata.strategy_name = name
        summaries.append(summary)

    # Save summary and diagnostics
    save_summary_csv(summaries, output_path / "summary.csv")
    save_diagnostics_csv(summaries, output_path / "diagnostics.csv")

    print(f"\nReports saved to {output_dir}/")
    print(f"  - summary.csv")
    print(f"  - diagnostics.csv")
    for name in results.keys():
        if results[name] is not None:
            safe_name = name.replace(" ", "_").lower()
            print(f"  - trades_{safe_name}.csv")
            print(f"  - quantstats_{safe_name}.html")
