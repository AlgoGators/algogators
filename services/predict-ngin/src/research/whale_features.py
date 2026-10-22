"""
ML Whale Detection Research

Feature engineering and ML-based whale identification beyond simple win rate.
Generates comprehensive research report with findings.

Features explored:
1. Trading behavior patterns (timing, frequency, sizing)
2. Market selection patterns (categories, liquidity preference)
3. Position management (holding periods, exit timing)
4. Network effects (following other whales, contrarian signals)
5. Performance consistency (Sharpe, drawdowns, streaks)

Usage:
    python -m src.research.whale_features
    python -m src.research.whale_features --output data/research_reports/whale_ml.html
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
class WhaleFeatures:
    """Feature set for a single trader."""
    address: str

    # Volume metrics
    total_volume: float
    avg_trade_size: float
    median_trade_size: float
    max_trade_size: float
    trade_count: int

    # Win rate metrics (from extreme prices)
    win_rate: float
    extreme_trade_count: int
    profit_estimate: float

    # Timing patterns
    avg_hour_of_day: float
    hour_std: float
    weekend_ratio: float

    # Frequency patterns
    avg_trades_per_day: float
    trading_days: int
    active_day_ratio: float

    # Sizing patterns
    size_consistency: float  # std/mean of trade sizes
    large_trade_ratio: float  # % trades > 2x avg

    # Market selection
    unique_markets: int
    market_concentration: float  # HHI
    avg_market_volume: float
    avg_market_liquidity: float

    # Position management
    avg_holding_period_est: float
    quick_flip_ratio: float  # trades < 1hr apart same market

    # Performance consistency
    daily_return_std: float
    max_winning_streak: int
    max_losing_streak: int
    sharpe_estimate: float

    # Network effects
    follows_whales: float  # trades after other whales
    contrarian_score: float  # trades opposite to crowd


def load_all_trades(db_path: str, min_usd: float = 10) -> pd.DataFrame:
    """Load all trades from database."""
    _pmfix_warn_legacy("load_all_trades (src/research/whale_features.py)", "Selects trade prices without nonusdc_side/outcomeIndex, so YES and NO prints are mixed.")
    conn = sqlite3.connect(db_path)

    df = pd.read_sql_query(f"""
        SELECT
            trade_id,
            timestamp,
            market_id,
            maker,
            taker,
            maker_direction,
            taker_direction,
            price,
            usd_amount
        FROM polymarket_trades
        WHERE usd_amount >= {min_usd}
        ORDER BY timestamp
    """, conn)

    conn.close()

    df["datetime"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["datetime"])
    df["date"] = df["datetime"].dt.date
    df["hour"] = df["datetime"].dt.hour
    df["dayofweek"] = df["datetime"].dt.dayofweek
    df["is_weekend"] = df["dayofweek"] >= 5

    return df


def load_market_metadata(db_path: str) -> pd.DataFrame:
    """Load market metadata."""
    conn = sqlite3.connect(db_path)

    try:
        df = pd.read_sql_query("""
            SELECT id, volume, liquidity, volume_24hr
            FROM polymarket_markets
        """, conn)
    except Exception:
        df = pd.DataFrame(columns=["id", "volume", "liquidity", "volume_24hr"])

    conn.close()
    return df


def extract_trader_trades(
    all_trades: pd.DataFrame,
    address: str,
    role: str = "both",
) -> pd.DataFrame:
    """Extract trades for a specific trader."""
    if role == "maker":
        mask = all_trades["maker"] == address
        direction_col = "maker_direction"
    elif role == "taker":
        mask = all_trades["taker"] == address
        direction_col = "taker_direction"
    else:
        mask = (all_trades["maker"] == address) | (all_trades["taker"] == address)
        # Use appropriate direction
        trades = all_trades[mask].copy()
        trades["direction"] = np.where(
            trades["maker"] == address,
            trades["maker_direction"],
            trades["taker_direction"]
        )
        return trades

    trades = all_trades[mask].copy()
    trades["direction"] = trades[direction_col]
    return trades


def estimate_correct_trade(price: float, direction: str) -> Optional[bool]:
    """Estimate if trade was correct based on extreme prices."""
    direction = str(direction).lower()

    if price > 0.85 and direction == "buy":
        return True
    if price > 0.85 and direction == "sell":
        return False
    if price < 0.15 and direction == "buy":
        return False
    if price < 0.15 and direction == "sell":
        return True

    return None


def calculate_features(
    trades: pd.DataFrame,
    address: str,
    market_meta: pd.DataFrame,
    all_whale_trades: Optional[pd.DataFrame] = None,
) -> WhaleFeatures:
    """Calculate comprehensive features for a trader."""

    if trades.empty:
        return None

    # Basic volume metrics
    total_volume = trades["usd_amount"].sum()
    avg_trade_size = trades["usd_amount"].mean()
    median_trade_size = trades["usd_amount"].median()
    max_trade_size = trades["usd_amount"].max()
    trade_count = len(trades)

    # Win rate from extreme prices
    trades["correct"] = trades.apply(
        lambda r: estimate_correct_trade(r["price"], r["direction"]), axis=1
    )
    extreme_trades = trades[trades["correct"].notna()]
    extreme_trade_count = len(extreme_trades)

    if extreme_trade_count > 0:
        win_rate = extreme_trades["correct"].mean()
        profit_estimate = (win_rate - 0.5) * total_volume * 0.1  # rough
    else:
        win_rate = 0.5
        profit_estimate = 0

    # Timing patterns
    avg_hour = trades["hour"].mean()
    hour_std = trades["hour"].std() if len(trades) > 1 else 12
    weekend_ratio = trades["is_weekend"].mean()

    # Frequency patterns
    unique_dates = trades["date"].nunique()
    date_range = (trades["datetime"].max() - trades["datetime"].min()).days + 1
    avg_trades_per_day = trade_count / max(unique_dates, 1)
    active_day_ratio = unique_dates / max(date_range, 1)

    # Sizing patterns
    size_consistency = trades["usd_amount"].std() / max(avg_trade_size, 1)
    large_trade_ratio = (trades["usd_amount"] > 2 * avg_trade_size).mean()

    # Market selection
    unique_markets = trades["market_id"].nunique()
    market_counts = trades.groupby("market_id").size()
    market_shares = market_counts / market_counts.sum()
    market_concentration = (market_shares ** 2).sum()  # HHI

    # Merge with market metadata
    if not market_meta.empty:
        market_stats = trades.merge(
            market_meta, left_on="market_id", right_on="id", how="left"
        )
        avg_market_volume = market_stats["volume"].mean() or 0
        avg_market_liquidity = market_stats["liquidity"].mean() or 0
    else:
        avg_market_volume = 0
        avg_market_liquidity = 0

    # Position management (estimate holding period)
    trades_sorted = trades.sort_values("datetime")
    same_market_gaps = []

    for market_id, group in trades_sorted.groupby("market_id"):
        if len(group) > 1:
            times = group["datetime"].values
            gaps = np.diff(times).astype("timedelta64[h]").astype(float)
            same_market_gaps.extend(gaps)

    if same_market_gaps:
        avg_holding_period = np.median(same_market_gaps)
        quick_flip_ratio = sum(1 for g in same_market_gaps if g < 1) / len(same_market_gaps)
    else:
        avg_holding_period = 24
        quick_flip_ratio = 0

    # Performance consistency
    daily_pnl = trades.groupby("date").apply(
        lambda g: (g["correct"].fillna(0.5).mean() - 0.5) * g["usd_amount"].sum()
    )
    daily_return_std = daily_pnl.std() if len(daily_pnl) > 1 else 0

    # Calculate streaks
    if extreme_trade_count > 0:
        correct_seq = extreme_trades["correct"].values
        max_winning = max_losing = current_win = current_lose = 0

        for c in correct_seq:
            if c:
                current_win += 1
                current_lose = 0
                max_winning = max(max_winning, current_win)
            else:
                current_lose += 1
                current_win = 0
                max_losing = max(max_losing, current_lose)
    else:
        max_winning = max_losing = 0

    # Sharpe estimate
    if len(daily_pnl) > 1 and daily_pnl.std() > 0:
        sharpe_estimate = (daily_pnl.mean() / daily_pnl.std()) * np.sqrt(252)
    else:
        sharpe_estimate = 0

    # Network effects (if we have other whale trades)
    follows_whales = 0
    contrarian_score = 0

    if all_whale_trades is not None and not all_whale_trades.empty:
        # Check if trader follows other whales
        follows_count = 0
        contrarian_count = 0
        total_compare = 0

        for _, trade in trades.iterrows():
            # Find other whale trades in same market within 1 hour before
            window_start = trade["datetime"] - timedelta(hours=1)
            prior_whale_trades = all_whale_trades[
                (all_whale_trades["market_id"] == trade["market_id"]) &
                (all_whale_trades["datetime"] >= window_start) &
                (all_whale_trades["datetime"] < trade["datetime"])
            ]

            if len(prior_whale_trades) > 0:
                total_compare += 1
                prior_direction = prior_whale_trades["direction"].mode()
                if len(prior_direction) > 0:
                    if trade["direction"] == prior_direction.iloc[0]:
                        follows_count += 1
                    else:
                        contrarian_count += 1

        if total_compare > 0:
            follows_whales = follows_count / total_compare
            contrarian_score = contrarian_count / total_compare

    return WhaleFeatures(
        address=address,
        total_volume=total_volume,
        avg_trade_size=avg_trade_size,
        median_trade_size=median_trade_size,
        max_trade_size=max_trade_size,
        trade_count=trade_count,
        win_rate=win_rate,
        extreme_trade_count=extreme_trade_count,
        profit_estimate=profit_estimate,
        avg_hour_of_day=avg_hour,
        hour_std=hour_std,
        weekend_ratio=weekend_ratio,
        avg_trades_per_day=avg_trades_per_day,
        trading_days=unique_dates,
        active_day_ratio=active_day_ratio,
        size_consistency=size_consistency,
        large_trade_ratio=large_trade_ratio,
        unique_markets=unique_markets,
        market_concentration=market_concentration,
        avg_market_volume=avg_market_volume,
        avg_market_liquidity=avg_market_liquidity,
        avg_holding_period_est=avg_holding_period,
        quick_flip_ratio=quick_flip_ratio,
        daily_return_std=daily_return_std,
        max_winning_streak=max_winning,
        max_losing_streak=max_losing,
        sharpe_estimate=sharpe_estimate,
        follows_whales=follows_whales,
        contrarian_score=contrarian_score,
    )


def build_feature_matrix(
    db_path: str = DEFAULT_DB_PATH,
    min_trades: int = 20,
    min_volume: float = 1000,
    top_n: int = 1000,
) -> pd.DataFrame:
    """Build feature matrix for all qualifying traders."""

    print("Loading trades...")
    all_trades = load_all_trades(db_path)
    print(f"  Loaded {len(all_trades):,} trades")

    print("Loading market metadata...")
    market_meta = load_market_metadata(db_path)

    # Get all unique traders with sufficient activity
    print("Identifying qualifying traders...")

    trader_stats = defaultdict(lambda: {"count": 0, "volume": 0})

    for _, row in all_trades.iterrows():
        for role in ["maker", "taker"]:
            addr = row[role]
            trader_stats[addr]["count"] += 1
            trader_stats[addr]["volume"] += row["usd_amount"]

    qualifying = [
        addr for addr, stats in trader_stats.items()
        if stats["count"] >= min_trades and stats["volume"] >= min_volume
    ]

    # Sort by volume and take top N
    qualifying.sort(key=lambda a: trader_stats[a]["volume"], reverse=True)
    qualifying = qualifying[:top_n]

    print(f"  Found {len(qualifying)} qualifying traders")

    # Calculate features for each trader
    print("Calculating features...")
    features_list = []

    for i, address in enumerate(qualifying):
        if (i + 1) % 100 == 0:
            print(f"  Processing trader {i+1}/{len(qualifying)}...")

        trades = extract_trader_trades(all_trades, address)
        features = calculate_features(trades, address, market_meta)

        if features:
            features_list.append(asdict(features))

    df = pd.DataFrame(features_list)
    print(f"  Created feature matrix: {df.shape}")

    return df


def identify_feature_clusters(features_df: pd.DataFrame) -> pd.DataFrame:
    """Cluster traders by feature patterns."""
    from sklearn.preprocessing import StandardScaler
    from sklearn.cluster import KMeans
    from sklearn.decomposition import PCA

    # Select numeric features for clustering
    numeric_cols = [
        "total_volume", "avg_trade_size", "trade_count", "win_rate",
        "extreme_trade_count", "avg_trades_per_day", "active_day_ratio",
        "size_consistency", "unique_markets", "market_concentration",
        "avg_holding_period_est", "sharpe_estimate"
    ]

    X = features_df[numeric_cols].fillna(0)

    # Standardize
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # Cluster
    kmeans = KMeans(n_clusters=5, random_state=42, n_init=10)
    features_df["cluster"] = kmeans.fit_predict(X_scaled)

    # PCA for visualization
    pca = PCA(n_components=2)
    coords = pca.fit_transform(X_scaled)
    features_df["pca_1"] = coords[:, 0]
    features_df["pca_2"] = coords[:, 1]

    return features_df


def find_best_features_for_prediction(
    features_df: pd.DataFrame,
    target: str = "win_rate",
    threshold: float = 0.6,
) -> Dict[str, Any]:
    """Find features most predictive of high win rate."""
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import cross_val_score

    # Create binary target
    y = (features_df[target] >= threshold).astype(int)

    # Feature columns (exclude target and identifiers)
    exclude = ["address", "cluster", "pca_1", "pca_2", target]
    feature_cols = [c for c in features_df.columns if c not in exclude]

    X = features_df[feature_cols].fillna(0)

    # Train Random Forest
    rf = RandomForestClassifier(n_estimators=100, random_state=42, max_depth=5)

    # Cross-validation
    cv_scores = cross_val_score(rf, X, y, cv=5, scoring="roc_auc")

    # Fit for feature importance
    rf.fit(X, y)

    importance = pd.DataFrame({
        "feature": feature_cols,
        "importance": rf.feature_importances_
    }).sort_values("importance", ascending=False)

    return {
        "cv_auc_mean": cv_scores.mean(),
        "cv_auc_std": cv_scores.std(),
        "feature_importance": importance,
        "top_features": importance.head(10)["feature"].tolist(),
    }


def generate_research_report(
    db_path: str = DEFAULT_DB_PATH,
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate comprehensive research report."""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if output_path is None:
        output_path = OUTPUT_DIR / f"whale_ml_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"

    print("=" * 70)
    print("ML WHALE DETECTION RESEARCH")
    print("=" * 70)

    # Build feature matrix
    features_df = build_feature_matrix(db_path)

    if features_df.empty:
        print("No data available for analysis")
        return {"error": "No data"}

    # Cluster analysis
    print("\nPerforming cluster analysis...")
    features_df = identify_feature_clusters(features_df)

    cluster_profiles = features_df.groupby("cluster").agg({
        "total_volume": "mean",
        "win_rate": "mean",
        "trade_count": "mean",
        "sharpe_estimate": "mean",
        "address": "count",
    }).rename(columns={"address": "count"})

    print("\nCluster Profiles:")
    print(cluster_profiles.round(2))

    # Feature importance
    print("\nFinding predictive features...")
    prediction_results = find_best_features_for_prediction(features_df)

    print(f"\nPrediction AUC: {prediction_results['cv_auc_mean']:.3f} (+/- {prediction_results['cv_auc_std']:.3f})")
    print("\nTop Predictive Features:")
    for _, row in prediction_results["feature_importance"].head(10).iterrows():
        print(f"  {row['feature']}: {row['importance']:.3f}")

    # Identify best whales by different criteria
    print("\nIdentifying whale archetypes...")

    archetypes = {
        "high_volume_high_wr": features_df[
            (features_df["total_volume"] > features_df["total_volume"].quantile(0.9)) &
            (features_df["win_rate"] >= 0.6)
        ],
        "consistent_performers": features_df[
            (features_df["sharpe_estimate"] > 1.0) &
            (features_df["extreme_trade_count"] >= 20)
        ],
        "market_specialists": features_df[
            (features_df["market_concentration"] > 0.3) &
            (features_df["win_rate"] >= 0.6)
        ],
        "high_frequency": features_df[
            (features_df["avg_trades_per_day"] > 5) &
            (features_df["win_rate"] >= 0.55)
        ],
    }

    # Generate report
    report = []
    report.append("# ML Whale Detection Research Report")
    report.append(f"\nGenerated: {datetime.now().isoformat()}")
    report.append(f"\nDatabase: {db_path}")

    report.append("\n## Executive Summary")
    report.append(f"\n- **Traders Analyzed**: {len(features_df):,}")
    report.append(f"- **Prediction AUC**: {prediction_results['cv_auc_mean']:.3f}")
    report.append(f"- **Clusters Identified**: {features_df['cluster'].nunique()}")

    report.append("\n## Feature Statistics")
    report.append("\n### Distribution of Key Metrics")
    report.append("\n```")
    report.append(features_df[["total_volume", "win_rate", "trade_count", "sharpe_estimate"]].describe().to_string())
    report.append("```")

    report.append("\n## Cluster Analysis")
    report.append("\n### Cluster Profiles")
    report.append("\n```")
    report.append(cluster_profiles.to_string())
    report.append("```")

    # Cluster descriptions
    report.append("\n### Cluster Interpretations")
    for cluster_id in sorted(features_df["cluster"].unique()):
        cluster_data = features_df[features_df["cluster"] == cluster_id]
        avg_wr = cluster_data["win_rate"].mean()
        avg_vol = cluster_data["total_volume"].mean()
        avg_sharpe = cluster_data["sharpe_estimate"].mean()

        if avg_wr > 0.6 and avg_sharpe > 1:
            label = "Elite Performers"
        elif avg_vol > features_df["total_volume"].quantile(0.8):
            label = "High Volume Traders"
        elif avg_wr < 0.45:
            label = "Underperformers"
        elif cluster_data["market_concentration"].mean() > 0.3:
            label = "Market Specialists"
        else:
            label = "Diversified Traders"

        report.append(f"\n**Cluster {cluster_id}: {label}**")
        report.append(f"- Count: {len(cluster_data)}")
        report.append(f"- Avg Win Rate: {avg_wr:.1%}")
        report.append(f"- Avg Volume: ${avg_vol:,.0f}")
        report.append(f"- Avg Sharpe: {avg_sharpe:.2f}")

    report.append("\n## Feature Importance for Win Rate Prediction")
    report.append("\n### Top 10 Predictive Features")
    report.append("\n| Rank | Feature | Importance |")
    report.append("|------|---------|------------|")
    for i, (_, row) in enumerate(prediction_results["feature_importance"].head(10).iterrows()):
        report.append(f"| {i+1} | {row['feature']} | {row['importance']:.3f} |")

    report.append("\n## Whale Archetypes")
    for archetype_name, archetype_df in archetypes.items():
        report.append(f"\n### {archetype_name.replace('_', ' ').title()}")
        report.append(f"- **Count**: {len(archetype_df)}")
        if not archetype_df.empty:
            report.append(f"- **Avg Win Rate**: {archetype_df['win_rate'].mean():.1%}")
            report.append(f"- **Avg Sharpe**: {archetype_df['sharpe_estimate'].mean():.2f}")
            report.append(f"- **Total Volume**: ${archetype_df['total_volume'].sum():,.0f}")

    report.append("\n## Recommendations")
    report.append("\n### For Strategy Development")
    report.append("\n1. **Primary Whale Signals**: Focus on traders from elite performer cluster")
    report.append("2. **Feature-Based Filtering**: Use top predictive features to filter whales:")
    for feat in prediction_results["top_features"][:5]:
        report.append(f"   - {feat}")
    report.append("3. **Archetype Diversification**: Combine signals from different archetypes")
    report.append("4. **Avoid**: Underperformer cluster and low-Sharpe traders")

    report.append("\n## Raw Data")
    report.append("\n### Top 20 Whales by Estimated Sharpe")
    report.append("\n```")
    top_whales = features_df.nlargest(20, "sharpe_estimate")[
        ["address", "total_volume", "win_rate", "trade_count", "sharpe_estimate"]
    ]
    top_whales["address"] = top_whales["address"].apply(lambda x: x[:10] + "..." + x[-6:])
    report.append(top_whales.to_string(index=False))
    report.append("```")

    # Save report
    report_text = "\n".join(report)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    print(f"\nReport saved to: {output_path}")

    # Also save feature matrix
    features_path = OUTPUT_DIR / "whale_features.csv"
    features_df.to_csv(features_path, index=False)
    print(f"Feature matrix saved to: {features_path}")

    return {
        "features_df": features_df,
        "cluster_profiles": cluster_profiles,
        "prediction_results": prediction_results,
        "archetypes": {k: len(v) for k, v in archetypes.items()},
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
