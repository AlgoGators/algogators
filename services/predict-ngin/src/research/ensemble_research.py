"""
Multi-Strategy Ensemble Research

Analyzes combining multiple strategy signals for improved performance.
Generates comprehensive research report with optimal combination weights.

Strategies analyzed:
1. Whale following (primary)
2. Momentum (price trend following)
3. Mean reversion (price extremes revert)
4. Volume spikes (unusual activity)
5. Contrarian (opposite of crowd)

Combination methods:
- Equal weight
- Risk parity
- Correlation-based
- ML-optimized

Usage:
    python -m src.research.ensemble_research
    python -m src.research.ensemble_research --output data/research_reports/ensemble.md
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
from typing import Dict, Any, List, Optional, Tuple, Callable
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from collections import defaultdict
import warnings
warnings.filterwarnings('ignore')

DEFAULT_DB_PATH = "data/prediction_markets.db"
OUTPUT_DIR = Path("data/research_reports")


@dataclass
class StrategySignal:
    """A trading signal from a strategy."""
    timestamp: datetime
    market_id: str
    direction: int  # 1 = buy, -1 = sell, 0 = hold
    confidence: float  # 0 to 1
    strategy_name: str
    features: Dict[str, float] = field(default_factory=dict)


@dataclass
class StrategyPerformance:
    """Performance metrics for a strategy."""
    name: str
    signal_count: int
    win_rate: float
    avg_return: float
    sharpe_ratio: float
    max_drawdown: float
    correlation_with_others: Dict[str, float] = field(default_factory=dict)


def load_price_data(db_path: str) -> pd.DataFrame:
    """Load price history."""
    conn = sqlite3.connect(db_path)

    try:
        df = pd.read_sql_query("""
            SELECT market_id, timestamp, price, datetime
            FROM polymarket_prices
            WHERE outcome = 'YES'
            ORDER BY market_id, timestamp
        """, conn)
    except:
        df = pd.DataFrame()

    conn.close()

    if not df.empty:
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
        df = df.dropna(subset=["datetime"])

    return df


def load_trade_data(db_path: str) -> pd.DataFrame:
    """Load trade history."""
    _pmfix_warn_legacy("load_trade_data (src/research/ensemble_research.py)", "Selects trade prices without nonusdc_side/outcomeIndex, so YES and NO prints are mixed.")
    conn = sqlite3.connect(db_path)

    try:
        df = pd.read_sql_query("""
            SELECT timestamp, market_id, maker, taker,
                   maker_direction, taker_direction, price, usd_amount
            FROM polymarket_trades
            WHERE usd_amount >= 10
            ORDER BY timestamp
        """, conn)
    except:
        df = pd.DataFrame()

    conn.close()

    if not df.empty:
        df["datetime"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df = df.dropna(subset=["datetime"])

    return df


class WhaleStrategy:
    """Whale following strategy signal generator."""

    def __init__(self, whale_addresses: set, min_trade_usd: float = 100):
        self.whale_addresses = whale_addresses
        self.min_trade_usd = min_trade_usd
        self.name = "whale_following"

    def generate_signals(self, trades_df: pd.DataFrame) -> List[StrategySignal]:
        """Generate signals when whales trade."""
        signals = []

        for _, row in trades_df.iterrows():
            for role in ["maker", "taker"]:
                addr = row[role]
                if addr in self.whale_addresses and row["usd_amount"] >= self.min_trade_usd:
                    direction_str = row[f"{role}_direction"]
                    direction = 1 if str(direction_str).lower() == "buy" else -1

                    signals.append(StrategySignal(
                        timestamp=row["datetime"],
                        market_id=row["market_id"],
                        direction=direction,
                        confidence=min(row["usd_amount"] / 1000, 1.0),
                        strategy_name=self.name,
                        features={"trade_size": row["usd_amount"]},
                    ))

        return signals


class MomentumStrategy:
    """Price momentum strategy signal generator."""

    def __init__(self, lookback_hours: int = 24, threshold: float = 0.05):
        self.lookback_hours = lookback_hours
        self.threshold = threshold
        self.name = "momentum"

    def generate_signals(self, prices_df: pd.DataFrame) -> List[StrategySignal]:
        """Generate signals based on price momentum."""
        signals = []

        for market_id, group in prices_df.groupby("market_id"):
            group = group.sort_values("datetime")

            # Calculate returns over lookback period
            group["price_change"] = group["price"].pct_change(periods=self.lookback_hours)

            for _, row in group.iterrows():
                if pd.isna(row["price_change"]):
                    continue

                change = row["price_change"]

                if abs(change) >= self.threshold:
                    direction = 1 if change > 0 else -1
                    confidence = min(abs(change) / 0.2, 1.0)

                    signals.append(StrategySignal(
                        timestamp=row["datetime"],
                        market_id=market_id,
                        direction=direction,
                        confidence=confidence,
                        strategy_name=self.name,
                        features={"price_change": change},
                    ))

        return signals


class MeanReversionStrategy:
    """Mean reversion strategy signal generator."""

    def __init__(self, window: int = 48, z_threshold: float = 2.0):
        self.window = window
        self.z_threshold = z_threshold
        self.name = "mean_reversion"

    def generate_signals(self, prices_df: pd.DataFrame) -> List[StrategySignal]:
        """Generate signals when price deviates from moving average."""
        signals = []

        for market_id, group in prices_df.groupby("market_id"):
            group = group.sort_values("datetime")

            # Calculate z-score
            group["ma"] = group["price"].rolling(self.window).mean()
            group["std"] = group["price"].rolling(self.window).std()
            group["z_score"] = (group["price"] - group["ma"]) / group["std"].replace(0, 1)

            for _, row in group.iterrows():
                if pd.isna(row["z_score"]):
                    continue

                z = row["z_score"]

                if abs(z) >= self.z_threshold:
                    # Bet on reversion: if price high, sell; if low, buy
                    direction = -1 if z > 0 else 1
                    confidence = min(abs(z) / 4.0, 1.0)

                    signals.append(StrategySignal(
                        timestamp=row["datetime"],
                        market_id=market_id,
                        direction=direction,
                        confidence=confidence,
                        strategy_name=self.name,
                        features={"z_score": z},
                    ))

        return signals


class VolumeSpikestrategy:
    """Volume spike strategy signal generator."""

    def __init__(self, spike_threshold: float = 3.0, window: int = 24):
        self.spike_threshold = spike_threshold
        self.window = window
        self.name = "volume_spike"

    def generate_signals(self, trades_df: pd.DataFrame) -> List[StrategySignal]:
        """Generate signals on unusual volume."""
        signals = []

        # Aggregate volume by hour and market
        trades_df = trades_df.copy()
        trades_df["hour"] = trades_df["datetime"].dt.floor("h")

        hourly_vol = trades_df.groupby(["market_id", "hour"]).agg({
            "usd_amount": "sum",
            "maker_direction": lambda x: x.value_counts().idxmax() if len(x) > 0 else "buy",
        }).reset_index()

        for market_id, group in hourly_vol.groupby("market_id"):
            group = group.sort_values("hour")
            group["avg_vol"] = group["usd_amount"].rolling(self.window).mean()
            group["vol_ratio"] = group["usd_amount"] / group["avg_vol"].replace(0, 1)

            for _, row in group.iterrows():
                if pd.isna(row["vol_ratio"]):
                    continue

                if row["vol_ratio"] >= self.spike_threshold:
                    direction = 1 if str(row["maker_direction"]).lower() == "buy" else -1
                    confidence = min(row["vol_ratio"] / 10.0, 1.0)

                    signals.append(StrategySignal(
                        timestamp=row["hour"],
                        market_id=market_id,
                        direction=direction,
                        confidence=confidence,
                        strategy_name=self.name,
                        features={"volume_ratio": row["vol_ratio"]},
                    ))

        return signals


class ContrarianStrategy:
    """Contrarian strategy - bet against the crowd."""

    def __init__(self, crowd_threshold: float = 0.8):
        self.crowd_threshold = crowd_threshold
        self.name = "contrarian"

    def generate_signals(self, trades_df: pd.DataFrame) -> List[StrategySignal]:
        """Generate signals opposite to crowd sentiment."""
        signals = []

        # Aggregate by hour and market
        trades_df = trades_df.copy()
        trades_df["hour"] = trades_df["datetime"].dt.floor("h")
        trades_df["is_buy"] = trades_df["taker_direction"].str.lower() == "buy"

        hourly = trades_df.groupby(["market_id", "hour"]).agg({
            "is_buy": ["sum", "count"],
            "usd_amount": "sum",
        }).reset_index()

        hourly.columns = ["market_id", "hour", "buy_count", "total_count", "volume"]
        hourly["buy_ratio"] = hourly["buy_count"] / hourly["total_count"].replace(0, 1)

        for _, row in hourly.iterrows():
            buy_ratio = row["buy_ratio"]

            # Strong consensus = contrarian signal
            if buy_ratio >= self.crowd_threshold:
                # Everyone buying, contrarian sells
                direction = -1
                confidence = min((buy_ratio - 0.5) * 2, 1.0)
            elif buy_ratio <= (1 - self.crowd_threshold):
                # Everyone selling, contrarian buys
                direction = 1
                confidence = min((0.5 - buy_ratio) * 2, 1.0)
            else:
                continue

            signals.append(StrategySignal(
                timestamp=row["hour"],
                market_id=row["market_id"],
                direction=direction,
                confidence=confidence,
                strategy_name=self.name,
                features={"buy_ratio": buy_ratio},
            ))

        return signals


def evaluate_signals(
    signals: List[StrategySignal],
    prices_df: pd.DataFrame,
    forward_hours: int = 24,
) -> pd.DataFrame:
    """Evaluate signal performance by checking forward returns."""

    results = []

    # Create price lookup
    price_lookup = {}
    for market_id, group in prices_df.groupby("market_id"):
        group = group.sort_values("timestamp")
        price_lookup[market_id] = group.set_index("datetime")["price"]

    for signal in signals:
        market_id = signal.market_id
        signal_time = signal.timestamp

        if market_id not in price_lookup:
            continue

        prices = price_lookup[market_id]

        # Find price at signal time
        try:
            signal_price = prices.asof(signal_time)
        except:
            continue

        if pd.isna(signal_price):
            continue

        # Find price forward_hours later
        forward_time = signal_time + timedelta(hours=forward_hours)
        try:
            forward_price = prices.asof(forward_time)
        except:
            continue

        if pd.isna(forward_price):
            continue

        # Calculate return
        price_return = (forward_price - signal_price) / signal_price

        # Adjust for direction
        signal_return = signal.direction * price_return

        results.append({
            "timestamp": signal_time,
            "market_id": market_id,
            "strategy": signal.strategy_name,
            "direction": signal.direction,
            "confidence": signal.confidence,
            "signal_price": signal_price,
            "forward_price": forward_price,
            "price_return": price_return,
            "signal_return": signal_return,
            "correct": signal_return > 0,
        })

    return pd.DataFrame(results)


def calculate_strategy_performance(eval_df: pd.DataFrame) -> Dict[str, StrategyPerformance]:
    """Calculate performance metrics for each strategy."""

    performances = {}

    for strategy, group in eval_df.groupby("strategy"):
        signal_count = len(group)
        win_rate = group["correct"].mean() if signal_count > 0 else 0
        avg_return = group["signal_return"].mean() if signal_count > 0 else 0

        # Sharpe (simplified - annualized)
        if signal_count > 1 and group["signal_return"].std() > 0:
            sharpe = (avg_return / group["signal_return"].std()) * np.sqrt(252 * 24)
        else:
            sharpe = 0

        # Max drawdown
        cumulative = (1 + group.sort_values("timestamp")["signal_return"]).cumprod()
        rolling_max = cumulative.expanding().max()
        drawdown = (cumulative - rolling_max) / rolling_max
        max_dd = drawdown.min() if len(drawdown) > 0 else 0

        performances[strategy] = StrategyPerformance(
            name=strategy,
            signal_count=signal_count,
            win_rate=win_rate,
            avg_return=avg_return,
            sharpe_ratio=sharpe,
            max_drawdown=max_dd,
        )

    return performances


def calculate_strategy_correlations(eval_df: pd.DataFrame) -> pd.DataFrame:
    """Calculate correlation matrix between strategy returns."""

    # Pivot to get returns by strategy per time period
    eval_df = eval_df.copy()
    eval_df["period"] = eval_df["timestamp"].dt.floor("D")

    daily_returns = eval_df.groupby(["period", "strategy"])["signal_return"].mean().unstack()

    return daily_returns.corr()


def find_optimal_weights(
    eval_df: pd.DataFrame,
    method: str = "equal",
) -> Dict[str, float]:
    """Find optimal combination weights."""

    strategies = eval_df["strategy"].unique()
    n_strategies = len(strategies)

    if n_strategies == 0:
        return {}

    if method == "equal":
        return {s: 1.0 / n_strategies for s in strategies}

    elif method == "sharpe_weighted":
        # Weight by Sharpe ratio
        perfs = calculate_strategy_performance(eval_df)
        sharpes = {s: max(p.sharpe_ratio, 0) for s, p in perfs.items()}
        total = sum(sharpes.values())
        if total == 0:
            return {s: 1.0 / n_strategies for s in strategies}
        return {s: v / total for s, v in sharpes.items()}

    elif method == "inverse_correlation":
        # Weight by inverse of average correlation
        corr_matrix = calculate_strategy_correlations(eval_df)
        avg_corr = {s: abs(corr_matrix[s].drop(s).mean()) for s in strategies if s in corr_matrix}

        # Lower correlation = higher weight
        inv_corr = {s: 1 / (0.1 + c) for s, c in avg_corr.items()}
        total = sum(inv_corr.values())
        if total == 0:
            return {s: 1.0 / n_strategies for s in strategies}
        return {s: v / total for s, v in inv_corr.items()}

    elif method == "risk_parity":
        # Weight inversely by volatility
        perfs = calculate_strategy_performance(eval_df)
        vols = {}

        for s, group in eval_df.groupby("strategy"):
            vol = group["signal_return"].std()
            vols[s] = max(vol, 0.01)

        inv_vol = {s: 1 / v for s, v in vols.items()}
        total = sum(inv_vol.values())
        if total == 0:
            return {s: 1.0 / n_strategies for s in strategies}
        return {s: v / total for s, v in inv_vol.items()}

    else:
        return {s: 1.0 / n_strategies for s in strategies}


def evaluate_ensemble(
    eval_df: pd.DataFrame,
    weights: Dict[str, float],
) -> Dict[str, float]:
    """Evaluate ensemble performance with given weights."""

    # Calculate weighted returns
    eval_df = eval_df.copy()
    eval_df["period"] = eval_df["timestamp"].dt.floor("D")

    daily_returns = []

    for period, group in eval_df.groupby("period"):
        weighted_return = 0

        for strategy, strat_group in group.groupby("strategy"):
            if strategy in weights:
                avg_return = strat_group["signal_return"].mean()
                weighted_return += weights[strategy] * avg_return

        daily_returns.append({
            "period": period,
            "return": weighted_return,
        })

    returns_df = pd.DataFrame(daily_returns)

    if returns_df.empty:
        return {"sharpe": 0, "win_rate": 0, "avg_return": 0, "max_dd": 0}

    # Calculate metrics
    avg_return = returns_df["return"].mean()
    std_return = returns_df["return"].std()
    sharpe = (avg_return / std_return) * np.sqrt(252) if std_return > 0 else 0

    win_rate = (returns_df["return"] > 0).mean()

    cumulative = (1 + returns_df["return"]).cumprod()
    rolling_max = cumulative.expanding().max()
    drawdown = (cumulative - rolling_max) / rolling_max
    max_dd = drawdown.min()

    return {
        "sharpe": sharpe,
        "win_rate": win_rate,
        "avg_return": avg_return,
        "max_dd": max_dd,
    }


def generate_research_report(
    db_path: str = DEFAULT_DB_PATH,
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate comprehensive ensemble research report."""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if output_path is None:
        output_path = OUTPUT_DIR / f"ensemble_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"

    print("=" * 70)
    print("MULTI-STRATEGY ENSEMBLE RESEARCH")
    print("=" * 70)

    # Load data
    print("\nLoading data...")
    prices_df = load_price_data(db_path)
    trades_df = load_trade_data(db_path)

    print(f"  Prices: {len(prices_df):,}")
    print(f"  Trades: {len(trades_df):,}")

    if prices_df.empty or trades_df.empty:
        print("  Insufficient data for analysis")
        return {"error": "Insufficient data"}

    # Get whale addresses
    whale_addresses = set()
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
        print(f"  Whales: {len(whale_addresses)}")
    except Exception as e:
        print(f"  Could not load whales: {e}")

    # Generate signals from each strategy
    print("\nGenerating strategy signals...")

    strategies = [
        WhaleStrategy(whale_addresses) if whale_addresses else None,
        MomentumStrategy(),
        MeanReversionStrategy(),
        VolumeSpikestrategy(),
        ContrarianStrategy(),
    ]
    strategies = [s for s in strategies if s is not None]

    all_signals = []
    for strategy in strategies:
        print(f"  {strategy.name}...", end=" ")

        if strategy.name in ["whale_following", "volume_spike", "contrarian"]:
            signals = strategy.generate_signals(trades_df)
        else:
            signals = strategy.generate_signals(prices_df)

        print(f"{len(signals):,} signals")
        all_signals.extend(signals)

    print(f"\n  Total signals: {len(all_signals):,}")

    # Evaluate signals
    print("\nEvaluating signals...")
    eval_df = evaluate_signals(all_signals, prices_df)
    print(f"  Evaluated: {len(eval_df):,} signals")

    if eval_df.empty:
        print("  No signals could be evaluated")
        return {"error": "No evaluable signals"}

    # Calculate individual performance
    print("\nCalculating individual strategy performance...")
    performances = calculate_strategy_performance(eval_df)

    print("\nIndividual Strategy Performance:")
    for name, perf in sorted(performances.items(), key=lambda x: x[1].sharpe_ratio, reverse=True):
        print(f"  {name:20s} | Signals: {perf.signal_count:6,} | "
              f"Win: {perf.win_rate:.1%} | Sharpe: {perf.sharpe_ratio:6.2f}")

    # Calculate correlations
    print("\nCalculating strategy correlations...")
    corr_matrix = calculate_strategy_correlations(eval_df)
    print("\nCorrelation Matrix:")
    print(corr_matrix.round(2))

    # Find optimal weights
    print("\nFinding optimal ensemble weights...")

    weight_methods = ["equal", "sharpe_weighted", "inverse_correlation", "risk_parity"]
    ensemble_results = {}

    for method in weight_methods:
        weights = find_optimal_weights(eval_df, method)
        perf = evaluate_ensemble(eval_df, weights)
        ensemble_results[method] = {
            "weights": weights,
            "performance": perf,
        }
        print(f"\n  {method}:")
        print(f"    Sharpe: {perf['sharpe']:.2f} | Win: {perf['win_rate']:.1%}")

    # Find best method
    best_method = max(ensemble_results.keys(), key=lambda m: ensemble_results[m]["performance"]["sharpe"])

    # Generate report
    report = []
    report.append("# Multi-Strategy Ensemble Research Report")
    report.append(f"\nGenerated: {datetime.now().isoformat()}")
    report.append(f"\nDatabase: {db_path}")

    report.append("\n## Executive Summary")
    report.append(f"\n- **Strategies Analyzed**: {len(strategies)}")
    report.append(f"\n- **Total Signals Generated**: {len(all_signals):,}")
    report.append(f"\n- **Signals Evaluated**: {len(eval_df):,}")
    report.append(f"\n- **Best Combination Method**: {best_method}")
    report.append(f"\n- **Best Ensemble Sharpe**: {ensemble_results[best_method]['performance']['sharpe']:.2f}")

    report.append("\n## Individual Strategy Performance")
    report.append("\n| Strategy | Signals | Win Rate | Avg Return | Sharpe | Max DD |")
    report.append("|----------|---------|----------|------------|--------|--------|")

    for name, perf in sorted(performances.items(), key=lambda x: x[1].sharpe_ratio, reverse=True):
        report.append(f"| {name} | {perf.signal_count:,} | {perf.win_rate:.1%} | "
                      f"{perf.avg_return:.2%} | {perf.sharpe_ratio:.2f} | {perf.max_drawdown:.1%} |")

    report.append("\n## Strategy Correlation Matrix")
    report.append("\n```")
    report.append(corr_matrix.round(2).to_string())
    report.append("```")

    report.append("\n### Correlation Insights")
    low_corr_pairs = []
    for i, s1 in enumerate(corr_matrix.columns):
        for j, s2 in enumerate(corr_matrix.columns):
            if i < j:
                corr = corr_matrix.loc[s1, s2]
                if abs(corr) < 0.3:
                    low_corr_pairs.append((s1, s2, corr))

    if low_corr_pairs:
        report.append("\nLow correlation pairs (good for diversification):")
        for s1, s2, corr in low_corr_pairs:
            report.append(f"- {s1} + {s2}: {corr:.2f}")
    else:
        report.append("\nNo low correlation pairs found. Strategies may be highly correlated.")

    report.append("\n## Ensemble Combination Methods")

    for method in weight_methods:
        result = ensemble_results[method]
        perf = result["performance"]
        weights = result["weights"]

        report.append(f"\n### {method.replace('_', ' ').title()}")
        report.append(f"\n**Performance**: Sharpe={perf['sharpe']:.2f}, "
                      f"Win Rate={perf['win_rate']:.1%}, Max DD={perf['max_dd']:.1%}")

        report.append("\n**Weights**:")
        for strat, weight in sorted(weights.items(), key=lambda x: x[1], reverse=True):
            report.append(f"- {strat}: {weight:.1%}")

    report.append("\n## Recommended Configuration")

    best_weights = ensemble_results[best_method]["weights"]
    best_perf = ensemble_results[best_method]["performance"]

    report.append(f"\n**Method**: {best_method}")
    report.append(f"\n**Expected Performance**:")
    report.append(f"- Sharpe Ratio: {best_perf['sharpe']:.2f}")
    report.append(f"- Win Rate: {best_perf['win_rate']:.1%}")
    report.append(f"- Max Drawdown: {best_perf['max_dd']:.1%}")

    report.append("\n**Optimal Weights**:")
    report.append("\n```python")
    report.append("ENSEMBLE_WEIGHTS = {")
    for strat, weight in sorted(best_weights.items(), key=lambda x: x[1], reverse=True):
        report.append(f"    '{strat}': {weight:.4f},")
    report.append("}")
    report.append("```")

    report.append("\n## Implementation Notes")
    report.append("\n1. **Signal Aggregation**: When multiple strategies signal the same market:")
    report.append("   - Sum weighted confidences")
    report.append("   - Direction = sign of weighted sum")
    report.append("   - Final confidence = abs(weighted sum)")

    report.append("\n2. **Position Sizing**: Scale position by ensemble confidence:")
    report.append("   - High confidence (>0.7): 100% of target size")
    report.append("   - Medium confidence (0.4-0.7): 50% of target size")
    report.append("   - Low confidence (<0.4): Skip or 25% of target size")

    report.append("\n3. **Conflict Resolution**: When strategies conflict:")
    report.append("   - If whale signal present, bias toward whale direction")
    report.append("   - If mean reversion vs momentum conflict, check regime")

    # Save report
    report_text = "\n".join(report)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    print(f"\nReport saved to: {output_path}")

    # Save evaluation data
    eval_path = OUTPUT_DIR / "ensemble_evaluation.csv"
    eval_df.to_csv(eval_path, index=False)
    print(f"Evaluation data saved to: {eval_path}")

    return {
        "performances": performances,
        "correlations": corr_matrix,
        "ensemble_results": ensemble_results,
        "best_method": best_method,
        "best_weights": best_weights,
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
