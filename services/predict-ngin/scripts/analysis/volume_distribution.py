"""
Analyze the distribution of volume across all Polymarket markets.
"""

import sys
from pathlib import Path
from typing import Optional
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.trading.data_modules.database import PredictionMarketDB


def analyze_volume_distribution(
    db_path: str = "data/prediction_markets.db",
    use_trades: bool = False,
    parquet_trades_dir: str = "data/polymarket/trades",
    parquet_markets_dir: str = "data/polymarket",
    use_db: bool = False,
):
    """Analyze volume distribution across all markets. Uses parquet by default (no DB)."""
    
    df = None
    volume_col = "volume_24hr"

    # 1. Try parquet markets first (default: all data in parquets)
    if not use_db:
        pm_path = Path(parquet_markets_dir)
        if pm_path.exists():
            single = pm_path / "markets.parquet"
            markets_files = [single] if single.exists() else list(pm_path.glob("markets_*.parquet"))
            if markets_files:
                print("Loading market volume from parquet...")
                try:
                    markets_list = [pd.read_parquet(f) for f in markets_files]
                    df = pd.concat(markets_list, ignore_index=True)
                    if "id" not in df.columns and "ID" in df.columns:
                        df["id"] = df["ID"]
                    df["id"] = df["id"].astype(str)
                    if "market_id" not in df.columns:
                        df["market_id"] = df["id"]
                    if "volume24hr" in df.columns:
                        df["volume_24hr"] = df["volume24hr"]
                    if "volume_24hr" not in df.columns and "volume" in df.columns:
                        df["volume_24hr"] = df["volume"]
                    if "volume_24hr" not in df.columns:
                        df["volume_24hr"] = 0
                    if "volume" not in df.columns:
                        df["volume"] = df["volume_24hr"].fillna(0)
                    df = df[df["volume_24hr"].fillna(0) > 0].copy()
                    df = df.sort_values("volume_24hr", ascending=False).reset_index(drop=True)
                    if not df.empty:
                        print(f"Found {len(df):,} markets with volume from parquet")
                except Exception as e:
                    print(f"Parquet markets load failed: {e}")
                    df = None

    # 2. If no parquet markets or use_trades requested, try parquet trades (no DB)
    if (df is None or df.empty) and not use_db:
        print("Trying parquet trades...")
        try:
            from src.trading.data_modules.parquet_store import TradeStore
            trade_store = TradeStore(parquet_trades_dir)
            if trade_store.available():
                trades_df = trade_store.load_trades()
                if not trades_df.empty and "usd_amount" in trades_df.columns:
                    print(f"Loaded {len(trades_df):,} trades from parquet")
                    volume_df = trades_df.groupby("market_id").agg({
                        "usd_amount": "sum",
                        "market_id": "count"
                    }).rename(columns={"usd_amount": "volume_24hr", "market_id": "trade_count"})
                    volume_df = volume_df.reset_index()
                    df = volume_df.copy()
                    df["id"] = df["market_id"].astype(str)
                    df["slug"] = None
                    df["question"] = "Market " + df["market_id"].astype(str)
                    df["volume"] = df["volume_24hr"]
                    df["liquidity"] = None
                    df = df.sort_values("volume_24hr", ascending=False).reset_index(drop=True)
                    print(f"Calculated volume for {len(df):,} markets from parquet trades")
        except Exception as e:
            print(f"Parquet trades load failed: {e}")
            if df is None:
                df = pd.DataFrame()

    # 3. Fallback to DB only when requested or parquet not available
    if (df is None or df.empty) or use_db:
        if not use_db and (df is None or df.empty):
            print("Parquet had no data, trying database...")
        if not use_trades:
            print("Loading market volume data from database...")
            try:
                db = PredictionMarketDB(db_path)
                query = """
                    SELECT id, slug, question, volume_24hr, volume, liquidity
                    FROM polymarket_markets
                    WHERE volume_24hr IS NOT NULL AND volume_24hr > 0
                    ORDER BY volume_24hr DESC
                """
                df = db.query(query)
                if df.empty:
                    query2 = """
                        SELECT id, slug, question, volume as volume_24hr, volume, liquidity
                        FROM polymarket_markets
                        WHERE volume IS NOT NULL AND volume > 0
                        ORDER BY volume DESC
                    """
                    df = db.query(query2)
                db.close()
                if not df.empty:
                    print(f"Found {len(df):,} markets from database")
            except Exception as e:
                print(f"Database load failed: {e}")
                if df is None:
                    df = pd.DataFrame()
        if (df is None or df.empty) and use_db:
            try:
                db = PredictionMarketDB(db_path)
                trades_query = """
                    SELECT market_id, SUM(usd_amount) as volume_24hr, COUNT(*) as trade_count
                    FROM polymarket_trades
                    WHERE usd_amount IS NOT NULL AND usd_amount > 0
                    GROUP BY market_id ORDER BY volume_24hr DESC
                """
                volume_df = db.query(trades_query)
                if not volume_df.empty:
                    markets_df = db.query("SELECT id, slug, question, liquidity FROM polymarket_markets")
                    db.close()
                    df = volume_df.merge(markets_df, left_on="market_id", right_on="id", how="left") if not markets_df.empty else volume_df.copy()
                    if "id" not in df.columns:
                        df["id"] = df["market_id"]
                    if "volume" not in df.columns:
                        df["volume"] = df["volume_24hr"]
                    if "slug" not in df.columns:
                        df["slug"] = None
                    if "question" not in df.columns:
                        df["question"] = None
                    if "liquidity" not in df.columns:
                        df["liquidity"] = None
                    print(f"Found {len(df):,} markets from database trades")
                else:
                    db.close()
            except Exception as e:
                print(f"Database trades failed: {e}")
                if df is None:
                    df = pd.DataFrame()
    
    if df is None or df.empty:
        print("No volume data found from any source.")
        return
    
    print(f"\nLoaded {len(df):,} markets with volume data\n")
    
    # Ensure we have required columns
    if "question" not in df.columns:
        df["question"] = df.get("slug", df.get("market_id", "Unknown"))
    if "slug" not in df.columns:
        df["slug"] = df.get("market_id", "unknown")
    
    # Basic statistics
    total_volume = df["volume_24hr"].sum()
    total_volume_all_time = df["volume"].sum() if "volume" in df.columns and df["volume"].notna().any() else None
    
    print("=" * 80)
    print("VOLUME DISTRIBUTION SUMMARY")
    print("=" * 80)
    print(f"\nTotal Markets: {len(df):,}")
    print(f"Total Volume: ${total_volume:,.2f}")
    if total_volume_all_time and total_volume_all_time != total_volume:
        print(f"Total All-Time Volume: ${total_volume_all_time:,.2f}")
    
    # Volume statistics
    print("\n" + "-" * 80)
    print("VOLUME STATISTICS")
    print("-" * 80)
    print(f"Mean:   ${df['volume_24hr'].mean():,.2f}")
    print(f"Median: ${df['volume_24hr'].median():,.2f}")
    print(f"Std Dev: ${df['volume_24hr'].std():,.2f}")
    print(f"Min:    ${df['volume_24hr'].min():,.2f}")
    print(f"Max:    ${df['volume_24hr'].max():,.2f}")
    
    # Percentiles
    print("\n" + "-" * 80)
    print("VOLUME PERCENTILES")
    print("-" * 80)
    percentiles = [10, 25, 50, 75, 90, 95, 99]
    for p in percentiles:
        val = np.percentile(df["volume_24hr"], p)
        print(f"{p:2d}th percentile: ${val:,.2f}")
    
    # Top markets
    print("\n" + "-" * 80)
    print("TOP 20 MARKETS BY VOLUME")
    print("-" * 80)
    top_markets = df.head(20)
    for idx, row in top_markets.iterrows():
        market_id = str(row.get("market_id", row.get("id", "unknown")))
        question = row.get("question")
        slug = row.get("slug")
        
        if pd.notna(question) and str(question).strip():
            display_name = str(question)[:60] + "..." if len(str(question)) > 60 else str(question)
        elif pd.notna(slug) and str(slug).strip():
            display_name = str(slug)
        else:
            display_name = f"Market {market_id}"
        
        print(f"{row['volume_24hr']:>15,.2f}  {display_name} (ID: {market_id})")
    
    # Volume concentration
    print("\n" + "-" * 80)
    print("VOLUME CONCENTRATION")
    print("-" * 80)
    
    # Cumulative volume by rank
    df_sorted = df.sort_values("volume_24hr", ascending=False).reset_index(drop=True)
    df_sorted["cumulative_volume"] = df_sorted["volume_24hr"].cumsum()
    df_sorted["cumulative_pct"] = (df_sorted["cumulative_volume"] / total_volume) * 100
    df_sorted["rank_pct"] = ((df_sorted.index + 1) / len(df_sorted)) * 100
    
    # Find how many markets account for X% of volume
    for pct in [10, 25, 50, 75, 90]:
        markets_needed = len(df_sorted[df_sorted["cumulative_pct"] <= pct]) + 1
        if markets_needed <= len(df_sorted):
            markets_pct = (markets_needed / len(df_sorted)) * 100
            print(f"Top {markets_needed:,} markets ({markets_pct:.1f}% of markets) account for {pct}% of volume")
    
    # Volume buckets
    print("\n" + "-" * 80)
    print("VOLUME DISTRIBUTION BY BUCKETS")
    print("-" * 80)
    
    # Define volume buckets
    buckets = [
        (0, 100, "$0 - $100"),
        (100, 1_000, "$100 - $1K"),
        (1_000, 10_000, "$1K - $10K"),
        (10_000, 100_000, "$10K - $100K"),
        (100_000, 1_000_000, "$100K - $1M"),
        (1_000_000, float('inf'), "$1M+"),
    ]
    
    bucket_counts = []
    bucket_volumes = []
    
    for min_vol, max_vol, label in buckets:
        if max_vol == float('inf'):
            mask = df["volume_24hr"] >= min_vol
        else:
            mask = (df["volume_24hr"] >= min_vol) & (df["volume_24hr"] < max_vol)
        
        count = mask.sum()
        volume = df.loc[mask, "volume_24hr"].sum()
        pct_markets = (count / len(df)) * 100
        pct_volume = (volume / total_volume) * 100 if total_volume > 0 else 0
        
        bucket_counts.append(count)
        bucket_volumes.append(volume)
        
        print(f"{label:20s}  {count:6,} markets ({pct_markets:5.1f}%)  ${volume:>15,.2f} ({pct_volume:5.1f}% of total)")
    
    # Advanced concentration and distribution metrics
    print("\n" + "-" * 80)
    print("ADVANCED CONCENTRATION METRICS")
    print("-" * 80)
    
    # Calculate Gini coefficient (measure of inequality)
    volumes_sorted = np.sort(df["volume_24hr"].values)
    n = len(volumes_sorted)
    cumsum = np.cumsum(volumes_sorted)
    gini = (2 * np.sum((np.arange(1, n + 1)) * volumes_sorted)) / (n * cumsum[-1]) - (n + 1) / n
    
    print(f"Gini Coefficient: {gini:.4f}")
    print("  (0 = perfect equality, 1 = perfect inequality)")
    
    if gini > 0.7:
        print("  -> Highly concentrated (top markets dominate)")
    elif gini > 0.5:
        print("  -> Moderately concentrated")
    else:
        print("  -> Relatively evenly distributed")
    
    # Herfindahl-Hirschman Index (HHI)
    market_shares = df["volume_24hr"].values / total_volume
    hhi = np.sum(market_shares ** 2) * 10000  # Scale to 0-10,000
    print(f"\nHHI (Herfindahl-Hirschman Index): {hhi:.2f}")
    print("  (0 = perfect competition, 10,000 = monopoly)")
    if hhi < 1500:
        print("  -> Competitive market")
    elif hhi < 2500:
        print("  -> Moderately concentrated")
    else:
        print("  -> Highly concentrated")
    
    # Top N Concentration Ratios
    print("\nTop N Concentration Ratios:")
    for n in [5, 10, 20, 50, 100]:
        top_n_volume = df.nlargest(n, "volume_24hr")["volume_24hr"].sum()
        cr_n = (top_n_volume / total_volume) * 100
        print(f"  CR{n:3d}: {cr_n:5.2f}% of volume")
    
    # Distribution shape metrics
    print("\n" + "-" * 80)
    print("DISTRIBUTION SHAPE METRICS")
    print("-" * 80)
    
    volumes = df["volume_24hr"].values
    
    # Skewness and Kurtosis
    try:
        from scipy import stats as scipy_stats
        SCIPY_AVAILABLE = True
    except ImportError:
        SCIPY_AVAILABLE = False
    
    if SCIPY_AVAILABLE:
        skewness = scipy_stats.skew(volumes)
        kurtosis = scipy_stats.kurtosis(volumes)
        print(f"Skewness: {skewness:.4f}")
        print("  (Positive = right tail, Negative = left tail)")
        print(f"Kurtosis: {kurtosis:.4f}")
        print("  (High = fat tails, Low = thin tails)")
        print("  (Normal distribution: skewness=0, kurtosis=0)")
    else:
        # Manual calculation
        mean_vol = np.mean(volumes)
        std_vol = np.std(volumes)
        skewness = np.mean(((volumes - mean_vol) / std_vol) ** 3)
        kurtosis = np.mean(((volumes - mean_vol) / std_vol) ** 4) - 3
        print(f"Skewness: {skewness:.4f}")
        print(f"Kurtosis: {kurtosis:.4f}")
    
    # Power Law Exponent
    print("\n" + "-" * 80)
    print("POWER LAW ANALYSIS")
    print("-" * 80)
    
    df_sorted = df.sort_values("volume_24hr", ascending=False).reset_index(drop=True)
    df_sorted_log = df_sorted[df_sorted["volume_24hr"] > 0].copy()
    
    if len(df_sorted_log) > 100:
        # Fit power law to top markets (more stable)
        tail_size = min(1000, len(df_sorted_log) // 10)
        ranks = np.arange(1, tail_size + 1)
        volumes_tail = df_sorted_log["volume_24hr"].values[:tail_size]
        
        # Log-linear fit: log(V) = log(C) - alpha * log(R)
        log_ranks = np.log10(ranks)
        log_volumes = np.log10(volumes_tail)
        
        # Remove any infinite values
        valid = np.isfinite(log_ranks) & np.isfinite(log_volumes)
        if valid.sum() > 10:
            coeffs = np.polyfit(log_ranks[valid], log_volumes[valid], 1)
            alpha = -coeffs[0]  # Power law exponent
            intercept = coeffs[1]
            
            print(f"Power Law Exponent (alpha): {alpha:.4f}")
            print(f"  Fit: log(V) = {intercept:.4f} - {alpha:.4f} * log(Rank)")
            print("  (Higher alpha = more concentrated, typical range: 1.0-2.0)")
            
            # R-squared for fit quality
            log_volumes_pred = intercept - alpha * log_ranks[valid]
            ss_res = np.sum((log_volumes[valid] - log_volumes_pred) ** 2)
            ss_tot = np.sum((log_volumes[valid] - np.mean(log_volumes[valid])) ** 2)
            r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0
            print(f"  R² (fit quality): {r_squared:.4f}")
        else:
            alpha = None
            print("  Insufficient data for power law fit")
    else:
        alpha = None
        print("  Insufficient markets for power law analysis")
    
    # Volume Percentile Rankings
    print("\n" + "-" * 80)
    print("VOLUME PERCENTILE RANKINGS")
    print("-" * 80)
    
    df["volume_percentile"] = df["volume_24hr"].rank(pct=True) * 100
    df["volume_percentile"] = df["volume_percentile"].round(2)
    
    # Show distribution of percentiles
    percentile_buckets = [0, 10, 25, 50, 75, 90, 95, 99, 100]
    print("\nMarkets by percentile range:")
    for i in range(len(percentile_buckets) - 1):
        lower = percentile_buckets[i]
        upper = percentile_buckets[i + 1]
        count = len(df[(df["volume_percentile"] >= lower) & (df["volume_percentile"] < upper)])
        if i == len(percentile_buckets) - 2:  # Last bucket includes upper bound
            count = len(df[df["volume_percentile"] >= lower])
        pct = (count / len(df)) * 100
        print(f"  {lower:3.0f}-{upper:3.0f}th percentile: {count:6,} markets ({pct:5.1f}%)")
    
    # Effective number of markets (diversification index)
    effective_markets = 1 / (hhi / 10000) if hhi > 0 else len(df)
    print(f"\nEffective Number of Markets: {effective_markets:.1f}")
    print("  (Number of equally-sized markets with same HHI)")
    print(f"  (Actual markets: {len(df):,}, Concentration reduces effective diversity)")
    
    print("\n" + "=" * 80)
    
    return df, total_volume, gini, hhi, alpha, skewness, kurtosis, bucket_counts, bucket_volumes, buckets


def create_volume_visualizations_extra(
    df: pd.DataFrame,
    total_volume: float,
    gini: float,
    bucket_counts: list,
    bucket_volumes: list,
    buckets: list,
    output_dir: Path = None,
):
    """Create and save standalone charts: histogram, pareto, buckets, top markets."""
    if output_dir is None:
        output_dir = Path("data/research")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Set style
    plt.style.use('seaborn-v0_8-darkgrid')
    fig_size = (12, 8)
    
    # 1. Volume Distribution Histogram (log scale)
    fig, ax = plt.subplots(figsize=fig_size)
    volumes = df["volume_24hr"].values
    volumes_log = np.log10(volumes[volumes > 0])
    
    ax.hist(volumes_log, bins=50, edgecolor='black', alpha=0.7, color='steelblue')
    ax.set_xlabel('Volume (log10 USD)', fontsize=12, fontweight='bold')
    ax.set_ylabel('Number of Markets', fontsize=12, fontweight='bold')
    ax.set_title('Volume Distribution Across Markets (Log Scale)', fontsize=14, fontweight='bold')
    ax.grid(True, alpha=0.3)
    
    # Add statistics text
    stats_text = f'Mean: ${df["volume_24hr"].mean():,.0f}\n'
    stats_text += f'Median: ${df["volume_24hr"].median():,.0f}\n'
    stats_text += f'Gini: {gini:.3f}'
    ax.text(0.02, 0.98, stats_text, transform=ax.transAxes,
            fontsize=10, verticalalignment='top',
            bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
    
    plt.tight_layout()
    plt.savefig(output_dir / 'volume_distribution_histogram.png', dpi=300, bbox_inches='tight')
    plt.close()
    print(f"Saved: {output_dir / 'volume_distribution_histogram.png'}")
    
    # 2. Cumulative Volume Distribution (Pareto Chart)
    fig, ax = plt.subplots(figsize=fig_size)
    
    df_sorted = df.sort_values("volume_24hr", ascending=False).reset_index(drop=True)
    df_sorted["cumulative_volume"] = df_sorted["volume_24hr"].cumsum()
    df_sorted["cumulative_pct"] = (df_sorted["cumulative_volume"] / total_volume) * 100
    df_sorted["rank_pct"] = ((df_sorted.index + 1) / len(df_sorted)) * 100
    
    ax.plot(df_sorted["rank_pct"], df_sorted["cumulative_pct"], 
            linewidth=2, color='darkgreen', label='Cumulative Volume %')
    ax.axhline(y=50, color='r', linestyle='--', alpha=0.5, label='50% line')
    ax.axhline(y=90, color='orange', linestyle='--', alpha=0.5, label='90% line')
    
    # Find and mark key points
    p50_idx = df_sorted[df_sorted["cumulative_pct"] <= 50].index[-1] if len(df_sorted[df_sorted["cumulative_pct"] <= 50]) > 0 else 0
    p90_idx = df_sorted[df_sorted["cumulative_pct"] <= 90].index[-1] if len(df_sorted[df_sorted["cumulative_pct"] <= 90]) > 0 else 0
    
    ax.scatter([df_sorted.loc[p50_idx, "rank_pct"]], [50], 
               color='red', s=100, zorder=5, label=f'50% at {df_sorted.loc[p50_idx, "rank_pct"]:.1f}% markets')
    ax.scatter([df_sorted.loc[p90_idx, "rank_pct"]], [90], 
               color='orange', s=100, zorder=5, label=f'90% at {df_sorted.loc[p90_idx, "rank_pct"]:.1f}% markets')
    
    ax.set_xlabel('Markets Ranked by Volume (%)', fontsize=12, fontweight='bold')
    ax.set_ylabel('Cumulative Volume (%)', fontsize=12, fontweight='bold')
    ax.set_title('Pareto Chart: Volume Concentration', fontsize=14, fontweight='bold')
    ax.legend(loc='lower right', fontsize=10)
    ax.grid(True, alpha=0.3)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    
    plt.tight_layout()
    plt.savefig(output_dir / 'volume_pareto_chart.png', dpi=300, bbox_inches='tight')
    plt.close()
    print(f"Saved: {output_dir / 'volume_pareto_chart.png'}")
    
    # 3. Volume Buckets Bar Chart
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))
    
    bucket_labels = [b[2] for b in buckets]
    bucket_labels_short = [label.replace('$', '').replace(' ', '\n') for label in bucket_labels]
    
    # Left: Market count by bucket
    colors = plt.cm.viridis(np.linspace(0, 1, len(bucket_counts)))
    bars1 = ax1.bar(range(len(bucket_counts)), bucket_counts, color=colors, edgecolor='black', alpha=0.8)
    ax1.set_xticks(range(len(bucket_counts)))
    ax1.set_xticklabels(bucket_labels_short, rotation=45, ha='right', fontsize=9)
    ax1.set_ylabel('Number of Markets', fontsize=12, fontweight='bold')
    ax1.set_title('Markets by Volume Bucket', fontsize=13, fontweight='bold')
    ax1.grid(True, alpha=0.3, axis='y')
    
    # Add value labels on bars
    for i, (bar, count) in enumerate(zip(bars1, bucket_counts)):
        height = bar.get_height()
        pct = (count / len(df)) * 100
        ax1.text(bar.get_x() + bar.get_width()/2., height,
                f'{count:,}\n({pct:.1f}%)',
                ha='center', va='bottom', fontsize=8)
    
    # Right: Volume by bucket
    bars2 = ax2.bar(range(len(bucket_volumes)), bucket_volumes, color=colors, edgecolor='black', alpha=0.8)
    ax2.set_xticks(range(len(bucket_volumes)))
    ax2.set_xticklabels(bucket_labels_short, rotation=45, ha='right', fontsize=9)
    ax2.set_ylabel('Total Volume (USD)', fontsize=12, fontweight='bold')
    ax2.set_title('Volume by Bucket', fontsize=13, fontweight='bold')
    ax2.grid(True, alpha=0.3, axis='y')
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'${x/1e9:.2f}B' if x >= 1e9 else f'${x/1e6:.1f}M'))
    
    # Add value labels on bars
    for i, (bar, vol) in enumerate(zip(bars2, bucket_volumes)):
        height = bar.get_height()
        pct = (vol / total_volume) * 100
        ax2.text(bar.get_x() + bar.get_width()/2., height,
                f'${vol/1e9:.2f}B\n({pct:.1f}%)',
                ha='center', va='bottom', fontsize=8)
    
    plt.tight_layout()
    plt.savefig(output_dir / 'volume_buckets.png', dpi=300, bbox_inches='tight')
    plt.close()
    print(f"Saved: {output_dir / 'volume_buckets.png'}")
    
    # 4. Top Markets Bar Chart
    fig, ax = plt.subplots(figsize=(14, 8))
    
    top_n = 30
    top_markets = df.nlargest(top_n, "volume_24hr")
    
    y_pos = np.arange(len(top_markets))
    bars = ax.barh(y_pos, top_markets["volume_24hr"].values, 
                   color=plt.cm.plasma(np.linspace(0, 0.9, len(top_markets))), 
                   edgecolor='black', alpha=0.8)
    
    # Create labels
    labels = []
    for idx, row in top_markets.iterrows():
        market_id = str(row.get("market_id", row.get("id", "unknown")))
        labels.append(f"Market {market_id}")
    
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xlabel('Volume (USD)', fontsize=12, fontweight='bold')
    ax.set_title(f'Top {top_n} Markets by Volume', fontsize=14, fontweight='bold')
    ax.grid(True, alpha=0.3, axis='x')
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'${x/1e6:.1f}M' if x >= 1e6 else f'${x/1e3:.0f}K'))
    
    # Invert y-axis to show highest at top
    ax.invert_yaxis()
    
    # Add value labels
    for i, (bar, vol) in enumerate(zip(bars, top_markets["volume_24hr"].values)):
        width = bar.get_width()
        ax.text(width, bar.get_y() + bar.get_height()/2,
                f'${vol/1e6:.1f}M',
                ha='left', va='center', fontsize=8, fontweight='bold')
    
    plt.tight_layout()
    plt.savefig(output_dir / 'top_markets_volume.png', dpi=300, bbox_inches='tight')
    plt.close()
    print(f"Saved: {output_dir / 'top_markets_volume.png'}")
    
    print(f"\nAll visualizations saved to: {output_dir}")
    
    return df, total_volume, gini


def create_volume_visualizations(
    df: pd.DataFrame,
    total_volume: float,
    gini: float,
    hhi: float,
    alpha: Optional[float],
    skewness: float,
    kurtosis: float,
    bucket_counts: list,
    bucket_volumes: list,
    buckets: list,
    output_dir: Path = None,
):
    """Create and save 4-panel dashboard and power-law chart."""
    
    if output_dir is None:
        output_dir = Path("data/research")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # Set style
    plt.style.use('seaborn-v0_8-darkgrid' if 'seaborn-v0_8-darkgrid' in plt.style.available else 'default')
    fig_size = (14, 10)
    
    # 1. Volume Distribution Histogram (log scale)
    fig, axes = plt.subplots(2, 2, figsize=fig_size)
    fig.suptitle('Polymarket Volume Distribution Analysis', fontsize=16, fontweight='bold')
    
    # Histogram (log scale)
    ax1 = axes[0, 0]
    volumes = df["volume_24hr"].values
    volumes_log = np.log10(volumes[volumes > 0])
    ax1.hist(volumes_log, bins=50, edgecolor='black', alpha=0.7, color='steelblue')
    ax1.set_xlabel('Volume (log10 USD)', fontsize=11)
    ax1.set_ylabel('Number of Markets', fontsize=11)
    ax1.set_title('Volume Distribution (Log Scale)', fontsize=12, fontweight='bold')
    ax1.grid(True, alpha=0.3)
    
    # Add statistics text
    stats_text = f'Mean: ${df["volume_24hr"].mean():,.0f}\n'
    stats_text += f'Median: ${df["volume_24hr"].median():,.0f}\n'
    stats_text += f'Gini: {gini:.3f}\n'
    stats_text += f'HHI: {hhi:.0f}\n'
    if alpha is not None:
        stats_text += f'alpha: {alpha:.2f}'
    ax1.text(0.02, 0.98, stats_text, transform=ax1.transAxes,
            fontsize=9, verticalalignment='top',
            bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
    
    # 2. Cumulative Volume Distribution
    ax2 = axes[0, 1]
    df_sorted = df.sort_values("volume_24hr", ascending=False).reset_index(drop=True)
    df_sorted["cumulative_volume"] = df_sorted["volume_24hr"].cumsum()
    df_sorted["cumulative_pct"] = (df_sorted["cumulative_volume"] / total_volume) * 100
    df_sorted["rank_pct"] = ((df_sorted.index + 1) / len(df_sorted)) * 100
    
    ax2.plot(df_sorted["rank_pct"], df_sorted["cumulative_pct"], linewidth=2, color='darkgreen')
    ax2.fill_between(df_sorted["rank_pct"], 0, df_sorted["cumulative_pct"], alpha=0.3, color='lightgreen')
    ax2.set_xlabel('% of Markets (ranked by volume)', fontsize=11)
    ax2.set_ylabel('% of Total Volume', fontsize=11)
    ax2.set_title('Cumulative Volume Distribution', fontsize=12, fontweight='bold')
    ax2.grid(True, alpha=0.3)
    ax2.axhline(50, color='red', linestyle='--', alpha=0.5, label='50% volume')
    ax2.axvline(0.4, color='orange', linestyle='--', alpha=0.5, label='0.4% markets')
    ax2.legend()
    
    # 3. Top 30 Markets Bar Chart
    ax3 = axes[1, 0]
    top_30 = df_sorted.head(30)
    colors = plt.cm.viridis(np.linspace(0, 1, len(top_30)))
    bars = ax3.barh(range(len(top_30)), top_30["volume_24hr"].values / 1e6, color=colors)
    ax3.set_yticks(range(len(top_30)))
    mid_col = "market_id" if "market_id" in top_30.columns else "id"
    ax3.set_yticklabels([f"Market {mid}" for mid in top_30[mid_col].astype(str).values], fontsize=8)
    ax3.set_xlabel('Volume (Millions USD)', fontsize=11)
    ax3.set_title('Top 30 Markets by Volume', fontsize=12, fontweight='bold')
    ax3.grid(True, alpha=0.3, axis='x')
    ax3.invert_yaxis()
    
    # 4. Volume Buckets Pie Chart
    ax4 = axes[1, 1]
    bucket_labels = [b[2] for b in buckets]
    
    # Only show buckets with volume
    non_zero = [i for i, v in enumerate(bucket_volumes) if v > 0]
    bucket_volumes_filtered = [bucket_volumes[i] for i in non_zero]
    bucket_labels_filtered = [bucket_labels[i] for i in non_zero]
    
    colors_pie = plt.cm.Set3(np.linspace(0, 1, len(bucket_volumes_filtered)))
    wedges, texts, autotexts = ax4.pie(
        bucket_volumes_filtered,
        labels=bucket_labels_filtered,
        autopct='%1.1f%%',
        startangle=90,
        colors=colors_pie
    )
    ax4.set_title('Volume Distribution by Buckets', fontsize=12, fontweight='bold')
    
    # Make percentage text more readable
    for autotext in autotexts:
        autotext.set_color('black')
        autotext.set_fontweight('bold')
        autotext.set_fontsize(9)
    
    plt.tight_layout()
    
    # Save figure
    output_file = output_path / "volume_distribution.png"
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    print(f"\nSaved visualization to: {output_file}")
    
    plt.close()
    
    # Create a second figure: Log-log plot showing power law
    fig2, ax = plt.subplots(figsize=(10, 8))
    
    # Rank vs Volume (log-log)
    df_sorted_log = df_sorted[df_sorted["volume_24hr"] > 0].copy()
    ranks = np.arange(1, len(df_sorted_log) + 1)
    volumes = df_sorted_log["volume_24hr"].values
    
    ax.loglog(ranks, volumes, 'o', markersize=2, alpha=0.5, color='steelblue', label='Markets')
    ax.set_xlabel('Market Rank (by volume)', fontsize=12)
    ax.set_ylabel('Volume (USD)', fontsize=12)
    ax.set_title('Volume Distribution: Power Law Analysis (Log-Log)', fontsize=14, fontweight='bold')
    ax.grid(True, alpha=0.3, which='both')
    ax.legend()
    
    # Add reference lines for power law
    if len(df_sorted_log) > 100:
        # Fit a simple power law in the tail (top 1000 markets)
        tail_size = min(1000, len(df_sorted_log) // 10)
        tail_ranks = ranks[:tail_size]
        tail_volumes = volumes[:tail_size]
        
        # Log-linear fit
        log_ranks = np.log10(tail_ranks)
        log_volumes = np.log10(tail_volumes)
        coeffs = np.polyfit(log_ranks, log_volumes, 1)
        
        # Plot fitted line
        fit_ranks = np.logspace(np.log10(ranks[0]), np.log10(ranks[-1]), 100)
        fit_volumes = 10**(coeffs[1]) * fit_ranks**(coeffs[0])
        ax.plot(fit_ranks, fit_volumes, 'r--', linewidth=2, 
                label=f'Power law fit: V ~ R^{coeffs[0]:.2f}')
        ax.legend()
    
    plt.tight_layout()
    
    output_file2 = output_path / "volume_power_law.png"
    plt.savefig(output_file2, dpi=300, bbox_inches='tight')
    print(f"Saved power law visualization to: {output_file2}")
    
    plt.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Analyze volume distribution across markets")
    parser.add_argument("--db-path", default="data/prediction_markets.db", help="Path to database (only used with --use-db)")
    parser.add_argument("--use-trades", action="store_true", help="Prefer volume from parquet trades if no parquet markets")
    parser.add_argument("--parquet-trades-dir", default="data/polymarket/trades", help="Path to parquet trades directory")
    parser.add_argument("--parquet-markets-dir", default="data/polymarket", help="Path to parquet markets (default; used first)")
    parser.add_argument("--use-db", action="store_true", help="Use database (default is parquet only)")
    parser.add_argument("--output-dir", default="data/research", help="Directory to save PNG visualizations")
    parser.add_argument("--no-viz", action="store_true", help="Skip creating visualizations")
    
    args = parser.parse_args()
    result = analyze_volume_distribution(
        db_path=args.db_path,
        use_trades=args.use_trades,
        parquet_trades_dir=args.parquet_trades_dir,
        parquet_markets_dir=args.parquet_markets_dir,
        use_db=args.use_db,
    )
    
    if result and not args.no_viz:
        df, total_volume, gini, hhi, alpha, skewness, kurtosis, bucket_counts, bucket_volumes, buckets = result
        print("\nGenerating visualizations...")
        out = Path(args.output_dir)
        create_volume_visualizations_extra(
            df, total_volume, gini, bucket_counts, bucket_volumes, buckets, output_dir=out
        )
        create_volume_visualizations(
            df, total_volume, gini, hhi, alpha, skewness, kurtosis,
            bucket_counts, bucket_volumes, buckets,
            output_dir=out
        )