"""
Market Regime Detection Research

Identifies market regimes to adapt trading strategy parameters.
Generates comprehensive research report with regime characteristics.

Regimes explored:
1. Volatility regimes (low/medium/high)
2. Trend regimes (trending up/down/sideways)
3. Liquidity regimes (liquid/illiquid)
4. Activity regimes (active/quiet)
5. Whale activity regimes (high/low whale participation)

Usage:
    python -m src.research.regime_detection
    python -m src.research.regime_detection --output data/research_reports/regimes.md
"""

import sqlite3
import json
import pandas as pd
try:
    from src.data_quality.polymarket_tokens import warn_legacy_reader as _pmfix_warn_legacy
except ImportError:  # src/ itself on sys.path
    from data_quality.polymarket_tokens import warn_legacy_reader as _pmfix_warn_legacy
import numpy as np
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from collections import defaultdict
import warnings
warnings.filterwarnings('ignore')

DEFAULT_DB_PATH = "data/prediction_markets.db"
OUTPUT_DIR = Path("data/research_reports")


@dataclass
class RegimeState:
    """Current regime classification."""
    timestamp: datetime
    volatility_regime: str  # low, medium, high
    trend_regime: str  # up, down, sideways
    liquidity_regime: str  # liquid, illiquid
    activity_regime: str  # active, quiet
    whale_regime: str  # high, low

    # Raw metrics
    volatility: float
    trend_strength: float
    avg_spread: float
    trade_count: float
    whale_participation: float


def load_price_data(
    db_path: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    """Load price history from database."""
    conn = sqlite3.connect(db_path)

    query = """
        SELECT market_id, outcome, timestamp, price, datetime
        FROM polymarket_prices
        WHERE outcome = 'YES'
    """

    if start_date:
        query += f" AND datetime >= '{start_date}'"
    if end_date:
        query += f" AND datetime <= '{end_date}'"

    query += " ORDER BY timestamp"

    try:
        df = pd.read_sql_query(query, conn)
    except:
        df = pd.DataFrame()

    conn.close()

    if not df.empty:
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
        df = df.dropna(subset=["datetime"])
        df["date"] = df["datetime"].dt.date

    return df


def load_trade_data(
    db_path: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    """Load trade history from database."""
    _pmfix_warn_legacy("load_trade_data (src/research/regime_detection.py)", "Selects trade prices without nonusdc_side/outcomeIndex, so YES and NO prints are mixed.")
    conn = sqlite3.connect(db_path)

    query = """
        SELECT timestamp, market_id, maker, taker, price, usd_amount
        FROM polymarket_trades
        WHERE usd_amount >= 10
    """

    if start_date:
        query += f" AND timestamp >= '{start_date}'"
    if end_date:
        query += f" AND timestamp <= '{end_date}'"

    query += " ORDER BY timestamp"

    try:
        df = pd.read_sql_query(query, conn)
    except:
        df = pd.DataFrame()

    conn.close()

    if not df.empty:
        df["datetime"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df = df.dropna(subset=["datetime"])
        df["date"] = df["datetime"].dt.date

    return df


def calculate_volatility(prices: pd.Series, window: int = 24) -> pd.Series:
    """Calculate rolling volatility."""
    returns = prices.pct_change()
    return returns.rolling(window=window, min_periods=1).std()


def calculate_trend(prices: pd.Series, window: int = 24) -> pd.Series:
    """Calculate trend strength using linear regression slope."""
    def rolling_slope(x):
        if len(x) < 2:
            return 0
        t = np.arange(len(x))
        try:
            slope = np.polyfit(t, x, 1)[0]
            return slope
        except:
            return 0

    return prices.rolling(window=window, min_periods=2).apply(rolling_slope, raw=False)


def classify_volatility(vol: float, thresholds: Tuple[float, float] = (0.02, 0.05)) -> str:
    """Classify volatility regime."""
    if vol < thresholds[0]:
        return "low"
    elif vol < thresholds[1]:
        return "medium"
    else:
        return "high"


def classify_trend(trend: float, threshold: float = 0.001) -> str:
    """Classify trend regime."""
    if trend > threshold:
        return "up"
    elif trend < -threshold:
        return "down"
    else:
        return "sideways"


def calculate_market_regimes(
    prices_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    whale_addresses: Optional[set] = None,
    freq: str = "1h",
) -> pd.DataFrame:
    """Calculate regime states over time."""

    if prices_df.empty:
        return pd.DataFrame()

    # Resample prices to frequency
    price_agg = prices_df.groupby([
        pd.Grouper(key="datetime", freq=freq),
        "market_id"
    ]).agg({
        "price": ["mean", "std", "min", "max", "count"]
    }).reset_index()

    price_agg.columns = ["datetime", "market_id", "price_mean", "price_std",
                          "price_min", "price_max", "price_count"]

    # Aggregate across markets
    market_agg = price_agg.groupby("datetime").agg({
        "price_mean": "mean",
        "price_std": "mean",
        "price_count": "sum",
    }).reset_index()

    market_agg.columns = ["datetime", "avg_price", "avg_volatility", "data_points"]

    # Add trade metrics
    if not trades_df.empty:
        trade_agg = trades_df.groupby(
            pd.Grouper(key="datetime", freq=freq)
        ).agg({
            "usd_amount": ["sum", "count"],
            "price": "std",
        }).reset_index()

        trade_agg.columns = ["datetime", "volume", "trade_count", "trade_volatility"]

        market_agg = market_agg.merge(trade_agg, on="datetime", how="left")
        market_agg["volume"] = market_agg["volume"].fillna(0)
        market_agg["trade_count"] = market_agg["trade_count"].fillna(0)

        # Whale participation
        if whale_addresses and len(whale_addresses) > 0:
            whale_trades = trades_df[
                (trades_df["maker"].isin(whale_addresses)) |
                (trades_df["taker"].isin(whale_addresses))
            ]

            whale_agg = whale_trades.groupby(
            pd.Grouper(key="datetime", freq=freq)
            ).agg({
                "usd_amount": "sum"
            }).reset_index()
            whale_agg.columns = ["datetime", "whale_volume"]

            market_agg = market_agg.merge(whale_agg, on="datetime", how="left")
            market_agg["whale_volume"] = market_agg["whale_volume"].fillna(0)
            market_agg["whale_participation"] = (
                market_agg["whale_volume"] / market_agg["volume"].replace(0, 1)
            )
        else:
            market_agg["whale_participation"] = 0
    else:
        market_agg["volume"] = 0
        market_agg["trade_count"] = 0
        market_agg["whale_participation"] = 0

    # Calculate rolling metrics
    market_agg["volatility"] = market_agg["avg_volatility"].rolling(24).mean()
    market_agg["trend"] = calculate_trend(market_agg["avg_price"])
    market_agg["activity"] = market_agg["trade_count"].rolling(24).mean()

    # Classify regimes
    vol_thresholds = (
        market_agg["volatility"].quantile(0.33),
        market_agg["volatility"].quantile(0.67),
    )

    market_agg["volatility_regime"] = market_agg["volatility"].apply(
        lambda x: classify_volatility(x, vol_thresholds)
    )

    market_agg["trend_regime"] = market_agg["trend"].apply(classify_trend)

    activity_median = market_agg["activity"].median()
    market_agg["activity_regime"] = market_agg["activity"].apply(
        lambda x: "active" if x > activity_median else "quiet"
    )

    whale_median = market_agg["whale_participation"].median()
    market_agg["whale_regime"] = market_agg["whale_participation"].apply(
        lambda x: "high" if x > whale_median else "low"
    )

    # Liquidity proxy (inverse of volatility + activity)
    market_agg["liquidity_score"] = (
        1 / (1 + market_agg["volatility"]) * market_agg["activity"]
    )
    liq_median = market_agg["liquidity_score"].median()
    market_agg["liquidity_regime"] = market_agg["liquidity_score"].apply(
        lambda x: "liquid" if x > liq_median else "illiquid"
    )

    return market_agg


def analyze_regime_transitions(regimes_df: pd.DataFrame) -> Dict[str, Any]:
    """Analyze regime transition patterns."""

    if regimes_df.empty:
        return {}

    results = {}

    for regime_col in ["volatility_regime", "trend_regime", "activity_regime", "whale_regime"]:
        if regime_col not in regimes_df.columns:
            continue

        # Transition matrix
        transitions = defaultdict(lambda: defaultdict(int))
        prev = None

        for val in regimes_df[regime_col]:
            if prev is not None:
                transitions[prev][val] += 1
            prev = val

        # Convert to probabilities
        transition_probs = {}
        for from_state, to_states in transitions.items():
            total = sum(to_states.values())
            transition_probs[from_state] = {
                to_state: count / total
                for to_state, count in to_states.items()
            }

        # Regime durations
        durations = []
        current_regime = None
        current_duration = 0

        for val in regimes_df[regime_col]:
            if val == current_regime:
                current_duration += 1
            else:
                if current_regime is not None:
                    durations.append((current_regime, current_duration))
                current_regime = val
                current_duration = 1

        if current_regime is not None:
            durations.append((current_regime, current_duration))

        avg_duration = defaultdict(list)
        for regime, duration in durations:
            avg_duration[regime].append(duration)

        avg_duration_stats = {
            regime: {"mean": np.mean(durs), "max": max(durs), "count": len(durs)}
            for regime, durs in avg_duration.items()
        }

        results[regime_col] = {
            "transition_probs": transition_probs,
            "duration_stats": avg_duration_stats,
            "distribution": regimes_df[regime_col].value_counts().to_dict(),
        }

    return results


def analyze_regime_performance(
    regimes_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    whale_addresses: set,
) -> Dict[str, Any]:
    """Analyze strategy performance in different regimes."""

    if regimes_df.empty or trades_df.empty:
        return {}

    # Merge trades with regimes
    trades_df = trades_df.copy()
    trades_df["period"] = trades_df["datetime"].dt.floor("1h")

    regimes_df = regimes_df.copy()
    regimes_df["period"] = regimes_df["datetime"].dt.floor("1h")

    merged = trades_df.merge(
        regimes_df[["period", "volatility_regime", "trend_regime",
                    "activity_regime", "whale_regime"]],
        on="period",
        how="left"
    )

    # Identify whale trades
    merged["is_whale"] = (
        merged["maker"].isin(whale_addresses) |
        merged["taker"].isin(whale_addresses)
    )

    results = {}

    for regime_col in ["volatility_regime", "trend_regime", "activity_regime", "whale_regime"]:
        if regime_col not in merged.columns:
            continue

        regime_stats = merged.groupby(regime_col).agg({
            "usd_amount": ["sum", "mean", "count"],
            "is_whale": "mean",
            "price": "std",
        })

        regime_stats.columns = ["total_volume", "avg_trade", "trade_count",
                                 "whale_ratio", "price_volatility"]

        results[regime_col] = regime_stats.to_dict()

    return results


def generate_regime_recommendations(
    transition_analysis: Dict,
    performance_analysis: Dict,
) -> List[str]:
    """Generate strategy recommendations based on regime analysis."""

    recommendations = []

    # Volatility recommendations
    if "volatility_regime" in transition_analysis:
        vol_dist = transition_analysis["volatility_regime"]["distribution"]
        high_pct = vol_dist.get("high", 0) / sum(vol_dist.values()) if vol_dist else 0

        if high_pct > 0.3:
            recommendations.append(
                "High volatility is common ({:.0%}). Consider: "
                "smaller position sizes, wider stops, shorter holding periods.".format(high_pct)
            )

    # Trend recommendations
    if "trend_regime" in transition_analysis:
        trend_dist = transition_analysis["trend_regime"]["distribution"]
        total = sum(trend_dist.values()) if trend_dist else 1

        sideways_pct = trend_dist.get("sideways", 0) / total

        if sideways_pct > 0.5:
            recommendations.append(
                "Market is often sideways ({:.0%}). Mean reversion may outperform momentum.".format(sideways_pct)
            )
        else:
            recommendations.append(
                "Trending conditions are common. Momentum strategies may be effective."
            )

    # Activity recommendations
    if "activity_regime" in transition_analysis:
        activity_dur = transition_analysis["activity_regime"].get("duration_stats", {})

        if "quiet" in activity_dur:
            avg_quiet = activity_dur["quiet"].get("mean", 0)
            if avg_quiet > 6:
                recommendations.append(
                    "Quiet periods last {:.0f} hours on average. Consider reducing position sizes during quiet regimes.".format(avg_quiet)
                )

    # Whale regime recommendations
    if "whale_regime" in performance_analysis:
        whale_perf = performance_analysis["whale_regime"]
        high_vol = whale_perf.get("total_volume", {}).get("high", 0)
        low_vol = whale_perf.get("total_volume", {}).get("low", 0)

        if high_vol > low_vol * 1.5:
            recommendations.append(
                "Whale activity periods have {:.1f}x more volume. Focus signals on high whale activity regimes.".format(high_vol / max(low_vol, 1))
            )

    if not recommendations:
        recommendations.append("No strong regime patterns detected. Use default strategy parameters.")

    return recommendations


def generate_research_report(
    db_path: str = DEFAULT_DB_PATH,
    output_path: Optional[str] = None,
    whale_addresses: Optional[set] = None,
) -> Dict[str, Any]:
    """Generate comprehensive regime detection research report."""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if output_path is None:
        output_path = OUTPUT_DIR / f"regime_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"

    print("=" * 70)
    print("MARKET REGIME DETECTION RESEARCH")
    print("=" * 70)

    # Load data
    print("\nLoading price data...")
    prices_df = load_price_data(db_path)
    print(f"  Loaded {len(prices_df):,} price points")

    print("Loading trade data...")
    trades_df = load_trade_data(db_path)
    print(f"  Loaded {len(trades_df):,} trades")

    # Get whale addresses if not provided
    if whale_addresses is None:
        try:
            from src.whale_strategy.polymarket_whales import (
                load_polymarket_trades, identify_polymarket_whales, build_price_snapshot
            )
            all_trades = load_polymarket_trades(db_path)
            price_snapshot = build_price_snapshot(all_trades)
            whale_addresses = identify_polymarket_whales(
                all_trades,
                method="win_rate_60pct",
                price_snapshot=price_snapshot,
            )
            print(f"  Identified {len(whale_addresses)} whales")
        except:
            whale_addresses = set()
            print("  Could not identify whales, skipping whale regime analysis")

    # Calculate regimes
    print("\nCalculating market regimes...")
    regimes_df = calculate_market_regimes(prices_df, trades_df, whale_addresses)

    if regimes_df.empty:
        print("  No regime data available")
        return {"error": "No data"}

    print(f"  Calculated {len(regimes_df):,} regime states")

    # Analyze transitions
    print("\nAnalyzing regime transitions...")
    transition_analysis = analyze_regime_transitions(regimes_df)

    # Analyze performance
    print("Analyzing regime performance...")
    performance_analysis = analyze_regime_performance(regimes_df, trades_df, whale_addresses)

    # Generate recommendations
    print("Generating recommendations...")
    recommendations = generate_regime_recommendations(transition_analysis, performance_analysis)

    # Generate report
    report = []
    report.append("# Market Regime Detection Research Report")
    report.append(f"\nGenerated: {datetime.now().isoformat()}")
    report.append(f"\nDatabase: {db_path}")

    report.append("\n## Executive Summary")
    report.append(f"\n- **Price Points Analyzed**: {len(prices_df):,}")
    report.append(f"\n- **Trades Analyzed**: {len(trades_df):,}")
    report.append(f"\n- **Regime States Calculated**: {len(regimes_df):,}")
    report.append(f"\n- **Whales Identified**: {len(whale_addresses):,}")

    report.append("\n## Regime Distributions")

    for regime_type in ["volatility_regime", "trend_regime", "activity_regime", "whale_regime"]:
        if regime_type in transition_analysis:
            report.append(f"\n### {regime_type.replace('_', ' ').title()}")
            dist = transition_analysis[regime_type]["distribution"]
            total = sum(dist.values())

            report.append("\n| Regime | Count | Percentage |")
            report.append("|--------|-------|------------|")
            for regime, count in sorted(dist.items()):
                pct = count / total * 100
                report.append(f"| {regime} | {count:,} | {pct:.1f}% |")

    report.append("\n## Regime Durations")

    for regime_type in ["volatility_regime", "trend_regime", "activity_regime"]:
        if regime_type in transition_analysis:
            dur_stats = transition_analysis[regime_type]["duration_stats"]
            report.append(f"\n### {regime_type.replace('_', ' ').title()}")
            report.append("\n| Regime | Avg Duration (hrs) | Max Duration | Occurrences |")
            report.append("|--------|-------------------|--------------|-------------|")
            for regime, stats in sorted(dur_stats.items()):
                report.append(f"| {regime} | {stats['mean']:.1f} | {stats['max']} | {stats['count']} |")

    report.append("\n## Transition Probabilities")

    for regime_type in ["volatility_regime", "trend_regime"]:
        if regime_type in transition_analysis:
            trans = transition_analysis[regime_type]["transition_probs"]
            report.append(f"\n### {regime_type.replace('_', ' ').title()}")

            all_states = sorted(set(trans.keys()) | set(s for t in trans.values() for s in t.keys()))

            header = "| From \\ To | " + " | ".join(all_states) + " |"
            sep = "|-----------|" + "|".join(["-----------"] * len(all_states)) + "|"
            report.append("\n" + header)
            report.append(sep)

            for from_state in all_states:
                row = f"| {from_state} |"
                for to_state in all_states:
                    prob = trans.get(from_state, {}).get(to_state, 0)
                    row += f" {prob:.1%} |"
                report.append(row)

    report.append("\n## Strategy Recommendations")
    report.append("\nBased on the regime analysis:")
    for i, rec in enumerate(recommendations, 1):
        report.append(f"\n{i}. {rec}")

    report.append("\n## Regime-Adaptive Parameters")
    report.append("\nSuggested parameter adjustments by regime:")

    report.append("\n### Position Sizing Multipliers")
    report.append("\n| Volatility Regime | Multiplier |")
    report.append("|-------------------|------------|")
    report.append("| low | 1.5x |")
    report.append("| medium | 1.0x |")
    report.append("| high | 0.5x |")

    report.append("\n### Holding Period Adjustments")
    report.append("\n| Trend Regime | Holding Period |")
    report.append("|--------------|----------------|")
    report.append("| up | Extend (momentum) |")
    report.append("| down | Shorten (cut losses) |")
    report.append("| sideways | Standard |")

    report.append("\n### Signal Confidence Adjustments")
    report.append("\n| Activity Regime | Whale Regime | Confidence Modifier |")
    report.append("|-----------------|--------------|---------------------|")
    report.append("| active | high | +20% |")
    report.append("| active | low | +0% |")
    report.append("| quiet | high | +10% |")
    report.append("| quiet | low | -20% |")

    report.append("\n## Raw Data Summary")
    report.append("\n### Recent Regime States (last 48 hours)")
    report.append("\n```")
    recent = regimes_df.tail(48)[
        ["datetime", "volatility_regime", "trend_regime", "activity_regime", "whale_regime"]
    ]
    report.append(recent.to_string(index=False))
    report.append("```")

    # Save report
    report_text = "\n".join(report)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    print(f"\nReport saved to: {output_path}")

    # Save regime data
    regimes_path = OUTPUT_DIR / "regime_states.csv"
    regimes_df.to_csv(regimes_path, index=False)
    print(f"Regime states saved to: {regimes_path}")

    return {
        "regimes_df": regimes_df,
        "transition_analysis": transition_analysis,
        "performance_analysis": performance_analysis,
        "recommendations": recommendations,
        "report_path": str(output_path),
    }


if __name__ == "__main__":
    import sys

    output = None
    if "--output" in sys.argv:
        idx = sys.argv.index("--output")
        if idx + 1 < len(sys.argv):
            output = sys.argv[idx + 1]

    results = generate_research_report(output_path=output)
