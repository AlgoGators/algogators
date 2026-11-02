"""
Distribution plotting for research analysis.

Provides visualization tools for trade analysis and strategy evaluation.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import logging

import numpy as np
import pandas as pd

try:
    import matplotlib
    # Use non-GUI backend to avoid Tkinter dependency
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.figure
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    plt = None
    MATPLOTLIB_AVAILABLE = False

try:
    import seaborn as sns
    SEABORN_AVAILABLE = True
except ImportError:
    sns = None
    SEABORN_AVAILABLE = False


Figure = Any  # Type alias for matplotlib Figure


class DistributionPlotter:
    """
    Visualization tools for trade analysis.

    Provides standardized plots for:
    - P&L distributions
    - Trade size distributions
    - Win rate by dimension
    - Temporal patterns

    Example:
        plotter = DistributionPlotter()

        # Plot P&L distribution
        fig = plotter.plot_pnl_distribution(trades_df)
        fig.savefig("pnl_distribution.png")

        # Generate full analysis report
        report_path = plotter.generate_analysis_report(trades_df, ["category", "day_of_week"])
    """

    def __init__(
        self,
        output_dir: str = "data/plots",
        style: str = "seaborn",
        figsize: Tuple[int, int] = (10, 6),
        dpi: int = 100,
        logger: Optional[logging.Logger] = None,
    ):
        if not MATPLOTLIB_AVAILABLE:
            raise ImportError("matplotlib required for plotting. Install with: pip install matplotlib")

        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.figsize = figsize
        self.dpi = dpi
        self.logger = logger or logging.getLogger("analysis.distributions")

        # Set style
        if style == "seaborn" and SEABORN_AVAILABLE:
            sns.set_style("whitegrid")
            sns.set_palette("husl")
        else:
            plt.style.use("ggplot")

    def _create_figure(
        self,
        nrows: int = 1,
        ncols: int = 1,
        figsize: Optional[Tuple[int, int]] = None,
    ) -> Tuple[Figure, Any]:
        """Create a new figure with axes."""
        figsize = figsize or self.figsize
        fig, axes = plt.subplots(nrows, ncols, figsize=figsize, dpi=self.dpi)
        return fig, axes

    def _save_figure(self, fig: Figure, name: str) -> str:
        """Save figure and return path."""
        filepath = self.output_dir / f"{name}.png"
        fig.savefig(filepath, bbox_inches="tight", dpi=self.dpi)
        plt.close(fig)
        return str(filepath)

    def plot_pnl_distribution(
        self,
        trades_df: pd.DataFrame,
        pnl_col: str = "net_pnl",
        by: Optional[str] = None,
        bins: int = 50,
        title: Optional[str] = None,
    ) -> Figure:
        """
        Plot P&L distribution.

        Args:
            trades_df: DataFrame with trade data
            pnl_col: Column containing P&L values
            by: Optional column to group by
            bins: Number of histogram bins
            title: Plot title

        Returns:
            matplotlib Figure
        """
        if pnl_col not in trades_df.columns:
            raise ValueError(f"Column '{pnl_col}' not found in DataFrame")

        pnl = trades_df[pnl_col].dropna()

        if by and by in trades_df.columns:
            fig, ax = self._create_figure()

            groups = trades_df.groupby(by)[pnl_col]
            for name, group in groups:
                ax.hist(group.dropna(), bins=bins, alpha=0.6, label=str(name))

            ax.legend()
        else:
            fig, ax = self._create_figure()

            # Color by win/loss
            wins = pnl[pnl > 0]
            losses = pnl[pnl <= 0]

            ax.hist(wins, bins=bins//2, alpha=0.7, label=f"Wins ({len(wins)})", color="green")
            ax.hist(losses, bins=bins//2, alpha=0.7, label=f"Losses ({len(losses)})", color="red")
            ax.legend()

        ax.axvline(x=0, color="black", linestyle="--", alpha=0.5)
        ax.axvline(x=pnl.mean(), color="blue", linestyle="-", alpha=0.7, label=f"Mean: ${pnl.mean():.2f}")

        ax.set_xlabel("P&L ($)")
        ax.set_ylabel("Frequency")
        ax.set_title(title or "P&L Distribution")

        # Add stats annotation
        stats_text = (
            f"Mean: ${pnl.mean():.2f}\n"
            f"Median: ${pnl.median():.2f}\n"
            f"Std: ${pnl.std():.2f}\n"
            f"Total: ${pnl.sum():.2f}"
        )
        ax.annotate(stats_text, xy=(0.95, 0.95), xycoords="axes fraction",
                    fontsize=9, ha="right", va="top",
                    bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5))

        return fig

    def plot_trade_size_distribution(
        self,
        trades_df: pd.DataFrame,
        size_col: str = "usd_amount",
        log_scale: bool = True,
        bins: int = 50,
        title: Optional[str] = None,
    ) -> Figure:
        """
        Plot trade size distribution.

        Args:
            trades_df: DataFrame with trade data
            size_col: Column containing trade sizes
            log_scale: Use log scale for x-axis
            bins: Number of histogram bins
            title: Plot title

        Returns:
            matplotlib Figure
        """
        if size_col not in trades_df.columns:
            raise ValueError(f"Column '{size_col}' not found in DataFrame")

        sizes = trades_df[size_col].dropna()
        sizes = sizes[sizes > 0]  # Filter non-positive for log scale

        fig, ax = self._create_figure()

        if log_scale:
            log_sizes = np.log10(sizes)
            ax.hist(log_sizes, bins=bins, alpha=0.7, edgecolor="black")
            ax.set_xlabel("Trade Size (log10 $)")

            # Add tick labels in original scale
            ticks = ax.get_xticks()
            ax.set_xticklabels([f"${10**t:,.0f}" for t in ticks])
        else:
            ax.hist(sizes, bins=bins, alpha=0.7, edgecolor="black")
            ax.set_xlabel("Trade Size ($)")

        ax.set_ylabel("Frequency")
        ax.set_title(title or "Trade Size Distribution")

        # Add stats
        stats_text = (
            f"Mean: ${sizes.mean():,.0f}\n"
            f"Median: ${sizes.median():,.0f}\n"
            f"Max: ${sizes.max():,.0f}\n"
            f"Count: {len(sizes):,}"
        )
        ax.annotate(stats_text, xy=(0.95, 0.95), xycoords="axes fraction",
                    fontsize=9, ha="right", va="top",
                    bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5))

        return fig

    def plot_win_rate_by_dimension(
        self,
        trades_df: pd.DataFrame,
        dimension: str,
        pnl_col: str = "net_pnl",
        min_trades: int = 10,
        title: Optional[str] = None,
    ) -> Figure:
        """
        Plot win rate by a categorical dimension.

        Args:
            trades_df: DataFrame with trade data
            dimension: Column to group by
            pnl_col: Column containing P&L values
            min_trades: Minimum trades per group to display
            title: Plot title

        Returns:
            matplotlib Figure
        """
        if dimension not in trades_df.columns:
            raise ValueError(f"Column '{dimension}' not found in DataFrame")

        if pnl_col not in trades_df.columns:
            raise ValueError(f"Column '{pnl_col}' not found in DataFrame")

        # Calculate win rate by dimension
        grouped = trades_df.groupby(dimension).agg({
            pnl_col: ["count", lambda x: (x > 0).mean(), "sum"]
        })
        grouped.columns = ["trades", "win_rate", "total_pnl"]
        grouped = grouped[grouped["trades"] >= min_trades]
        grouped = grouped.sort_values("win_rate", ascending=True)

        fig, axes = self._create_figure(1, 2, figsize=(14, 6))

        # Win rate bar chart
        ax1 = axes[0]
        colors = ["green" if wr > 0.5 else "red" for wr in grouped["win_rate"]]
        bars = ax1.barh(range(len(grouped)), grouped["win_rate"], color=colors, alpha=0.7)
        ax1.set_yticks(range(len(grouped)))
        ax1.set_yticklabels(grouped.index)
        ax1.axvline(x=0.5, color="black", linestyle="--", alpha=0.5)
        ax1.set_xlabel("Win Rate")
        ax1.set_title("Win Rate by " + dimension.replace("_", " ").title())

        # Add value labels
        for i, (idx, row) in enumerate(grouped.iterrows()):
            ax1.text(row["win_rate"] + 0.01, i, f"{row['win_rate']:.1%} ({int(row['trades'])})",
                     va="center", fontsize=8)

        # Total P&L bar chart
        ax2 = axes[1]
        colors = ["green" if pnl > 0 else "red" for pnl in grouped["total_pnl"]]
        ax2.barh(range(len(grouped)), grouped["total_pnl"], color=colors, alpha=0.7)
        ax2.set_yticks(range(len(grouped)))
        ax2.set_yticklabels(grouped.index)
        ax2.axvline(x=0, color="black", linestyle="--", alpha=0.5)
        ax2.set_xlabel("Total P&L ($)")
        ax2.set_title("Total P&L by " + dimension.replace("_", " ").title())

        plt.tight_layout()
        return fig

    def plot_temporal_patterns(
        self,
        trades_df: pd.DataFrame,
        datetime_col: str = "datetime",
        pnl_col: str = "net_pnl",
        title: Optional[str] = None,
    ) -> Figure:
        """
        Plot temporal patterns in trading.

        Args:
            trades_df: DataFrame with trade data
            datetime_col: Column containing datetimes
            pnl_col: Column containing P&L values
            title: Plot title

        Returns:
            matplotlib Figure
        """
        if datetime_col not in trades_df.columns:
            raise ValueError(f"Column '{datetime_col}' not found in DataFrame")

        df = trades_df.copy()
        df[datetime_col] = pd.to_datetime(df[datetime_col])

        # Extract temporal features
        df["hour"] = df[datetime_col].dt.hour
        df["day_of_week"] = df[datetime_col].dt.day_name()
        df["month"] = df[datetime_col].dt.to_period("M").astype(str)

        fig, axes = self._create_figure(2, 2, figsize=(14, 10))

        # Hour of day
        ax1 = axes[0, 0]
        hourly = df.groupby("hour")[pnl_col].agg(["mean", "count"])
        ax1.bar(hourly.index, hourly["mean"], alpha=0.7)
        ax1.set_xlabel("Hour of Day (UTC)")
        ax1.set_ylabel("Mean P&L ($)")
        ax1.set_title("P&L by Hour of Day")
        ax1.axhline(y=0, color="black", linestyle="--", alpha=0.5)

        # Day of week
        ax2 = axes[0, 1]
        day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        daily = df.groupby("day_of_week")[pnl_col].agg(["mean", "count"])
        daily = daily.reindex([d for d in day_order if d in daily.index])
        colors = ["green" if m > 0 else "red" for m in daily["mean"]]
        ax2.bar(range(len(daily)), daily["mean"], color=colors, alpha=0.7)
        ax2.set_xticks(range(len(daily)))
        ax2.set_xticklabels([d[:3] for d in daily.index], rotation=45)
        ax2.set_xlabel("Day of Week")
        ax2.set_ylabel("Mean P&L ($)")
        ax2.set_title("P&L by Day of Week")
        ax2.axhline(y=0, color="black", linestyle="--", alpha=0.5)

        # Monthly cumulative P&L
        ax3 = axes[1, 0]
        monthly = df.groupby("month")[pnl_col].sum()
        ax3.plot(range(len(monthly)), monthly.cumsum(), marker="o")
        ax3.set_xticks(range(len(monthly)))
        ax3.set_xticklabels(monthly.index, rotation=45, ha="right")
        ax3.set_xlabel("Month")
        ax3.set_ylabel("Cumulative P&L ($)")
        ax3.set_title("Cumulative P&L Over Time")
        ax3.axhline(y=0, color="black", linestyle="--", alpha=0.5)

        # Trade count by month
        ax4 = axes[1, 1]
        monthly_counts = df.groupby("month").size()
        ax4.bar(range(len(monthly_counts)), monthly_counts, alpha=0.7)
        ax4.set_xticks(range(len(monthly_counts)))
        ax4.set_xticklabels(monthly_counts.index, rotation=45, ha="right")
        ax4.set_xlabel("Month")
        ax4.set_ylabel("Number of Trades")
        ax4.set_title("Trade Volume Over Time")

        plt.tight_layout()
        return fig

    def plot_cumulative_pnl(
        self,
        trades_df: pd.DataFrame,
        pnl_col: str = "net_pnl",
        datetime_col: str = "datetime",
        title: Optional[str] = None,
    ) -> Figure:
        """
        Plot cumulative P&L over time.

        Args:
            trades_df: DataFrame with trade data
            pnl_col: Column containing P&L values
            datetime_col: Column containing datetimes
            title: Plot title

        Returns:
            matplotlib Figure
        """
        df = trades_df.sort_values(datetime_col).copy()
        df["cumulative_pnl"] = df[pnl_col].cumsum()

        fig, ax = self._create_figure()

        # Plot cumulative P&L
        ax.plot(df[datetime_col], df["cumulative_pnl"], linewidth=1.5)
        ax.fill_between(df[datetime_col], 0, df["cumulative_pnl"],
                        where=df["cumulative_pnl"] >= 0, alpha=0.3, color="green")
        ax.fill_between(df[datetime_col], 0, df["cumulative_pnl"],
                        where=df["cumulative_pnl"] < 0, alpha=0.3, color="red")

        ax.axhline(y=0, color="black", linestyle="--", alpha=0.5)
        ax.set_xlabel("Date")
        ax.set_ylabel("Cumulative P&L ($)")
        ax.set_title(title or "Cumulative P&L")

        # Rotate x-axis labels
        plt.xticks(rotation=45, ha="right")

        # Add final value annotation
        final_pnl = df["cumulative_pnl"].iloc[-1]
        ax.annotate(f"Final: ${final_pnl:,.2f}",
                    xy=(df[datetime_col].iloc[-1], final_pnl),
                    xytext=(10, 10), textcoords="offset points",
                    fontsize=10, fontweight="bold")

        return fig

    def generate_analysis_report(
        self,
        trades_df: pd.DataFrame,
        dimensions: List[str],
        output_prefix: str = "analysis",
    ) -> str:
        """
        Generate a comprehensive analysis report.

        Args:
            trades_df: DataFrame with trade data
            dimensions: List of categorical columns to analyze
            output_prefix: Prefix for output files

        Returns:
            Path to main output directory
        """
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        report_dir = self.output_dir / f"{output_prefix}_{timestamp}"
        report_dir.mkdir(parents=True, exist_ok=True)

        self.logger.info(f"Generating analysis report in {report_dir}")

        # P&L distribution
        try:
            fig = self.plot_pnl_distribution(trades_df)
            fig.savefig(report_dir / "pnl_distribution.png", bbox_inches="tight")
            plt.close(fig)
        except Exception as e:
            self.logger.warning(f"Failed to plot P&L distribution: {e}")

        # Trade size distribution
        try:
            fig = self.plot_trade_size_distribution(trades_df)
            fig.savefig(report_dir / "trade_size_distribution.png", bbox_inches="tight")
            plt.close(fig)
        except Exception as e:
            self.logger.warning(f"Failed to plot trade size distribution: {e}")

        # Temporal patterns
        try:
            fig = self.plot_temporal_patterns(trades_df)
            fig.savefig(report_dir / "temporal_patterns.png", bbox_inches="tight")
            plt.close(fig)
        except Exception as e:
            self.logger.warning(f"Failed to plot temporal patterns: {e}")

        # Cumulative P&L
        try:
            fig = self.plot_cumulative_pnl(trades_df)
            fig.savefig(report_dir / "cumulative_pnl.png", bbox_inches="tight")
            plt.close(fig)
        except Exception as e:
            self.logger.warning(f"Failed to plot cumulative P&L: {e}")

        # Win rate by each dimension
        for dim in dimensions:
            if dim in trades_df.columns:
                try:
                    fig = self.plot_win_rate_by_dimension(trades_df, dim)
                    fig.savefig(report_dir / f"win_rate_by_{dim}.png", bbox_inches="tight")
                    plt.close(fig)
                except Exception as e:
                    self.logger.warning(f"Failed to plot win rate by {dim}: {e}")

        self.logger.info(f"Analysis report generated: {report_dir}")
        return str(report_dir)


__all__ = ["DistributionPlotter"]
