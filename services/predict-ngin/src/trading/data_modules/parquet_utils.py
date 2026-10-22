"""
Utilities for optimized Parquet file writing.

Optimized for 100GB+ datasets with Zstd compression and row group optimization.
"""

from pathlib import Path
from typing import Optional, List

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


def write_optimized_parquet(
    df: pd.DataFrame,
    filepath: Path,
    sort_columns: Optional[List[str]] = None,
    compression: str = "zstd",
    compression_level: int = 3,
    row_group_size: int = 500_000,
) -> None:
    """
    Write DataFrame to Parquet with optimizations for large datasets.
    
    Optimizations:
    - Zstd compression (better ratio than snappy)
    - Row group size optimization (better predicate pushdown)
    - Optional sorting (improves predicate pushdown efficiency)
    
    Args:
        df: DataFrame to write
        filepath: Output file path
        sort_columns: Columns to sort by before writing (e.g., ["market_id", "outcome", "timestamp"])
        compression: Compression codec (default: zstd)
        compression_level: Compression level (1-9, default: 3)
        row_group_size: Target row group size in rows (default: 500_000)
    """
    if df.empty:
        # Write empty file with schema
        schema = pa.Schema.from_pandas(df)
        table = pa.Table.from_pandas(df, schema=schema, preserve_index=False)
    else:
        # Sort if requested (improves predicate pushdown)
        if sort_columns:
            # Only sort by columns that exist
            existing_cols = [col for col in sort_columns if col in df.columns]
            if existing_cols:
                df = df.sort_values(existing_cols).reset_index(drop=True)
        
        table = pa.Table.from_pandas(df, preserve_index=False)
    
    # Write with optimizations
    pq.write_table(
        table,
        filepath,
        compression=compression,
        compression_level=compression_level,
        row_group_size=row_group_size,
    )


def write_prices_optimized(
    df: pd.DataFrame,
    filepath: Path,
) -> None:
    """
    Write price data with optimal sorting for predicate pushdown.
    
    Sorts by (market_id, outcome, timestamp) for best predicate pushdown performance.
    """
    write_optimized_parquet(
        df,
        filepath,
        sort_columns=["market_id", "outcome", "timestamp"],
    )


def write_trades_optimized(
    df: pd.DataFrame,
    filepath: Path,
) -> None:
    """
    Write trade data with optimal sorting for predicate pushdown.
    
    Sorts by (timestamp, market_id) for best date-range query performance.
    """
    write_optimized_parquet(
        df,
        filepath,
        sort_columns=["timestamp", "market_id"],
    )


__all__ = [
    "write_optimized_parquet",
    "write_prices_optimized",
    "write_trades_optimized",
]
