#!/usr/bin/env python3
"""
Merge trades.parquet (HuggingFace) with monthly trades_*.parquet into deduplicated monthly files.

Memory-safe: streams in chunks (~500k rows). Outputs monthly parquet for efficient backtest loading.
Requires: data/polymarket/trades.parquet and/or data/parquet/trades/trades_*.parquet

Usage:
    python scripts/data/merge_trades.py
    python scripts/data/merge_trades.py --chunk-rows 50000
"""
import argparse
import gc
from pathlib import Path
import sys

import pandas as pd
import pyarrow.parquet as pq

CHUNK_ROWS = 100_000  # 16GB RAM limit: avoid dictionary_encode OOM
POLY_DIR = Path("data/polymarket")
SINGLE_FILE = POLY_DIR / "trades.parquet"
OUT_DIR = POLY_DIR / "trades"
LEGACY_DIR = Path("data/parquet/trades")


def _ensure_columns(df, schema_map):
    """Map source columns to TradeStore schema."""
    out = {}
    for target, sources in schema_map.items():
        for s in sources:
            if s in df.columns:
                out[target] = df[s].copy()
                break
    return pd.DataFrame(out) if out else pd.DataFrame()


def main():
    parser = argparse.ArgumentParser(description="Merge trades into monthly parquet (memory-safe)")
    parser.add_argument("--chunk-rows", type=int, default=CHUNK_ROWS, help="Rows per batch")
    parser.add_argument("--min-usd", type=float, default=0, help="Minimum USD per trade (liquidity filter, default: 0)")
    parser.add_argument("--output-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--single-file", type=Path, default=SINGLE_FILE)
    parser.add_argument("--legacy-dir", type=Path, default=LEGACY_DIR)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    SCHEMA = {
        "timestamp": ["timestamp", "datetime", "created_at"],
        "market_id": ["market_id", "marketId"],
        "maker": ["maker", "maker_address"],
        "taker": ["taker", "taker_address"],
        "price": ["price", "outcomePrice"],
        "usd_amount": ["usd_amount", "usdAmount", "amount"],
        "token_amount": ["token_amount", "tokenAmount"],
        "transaction_hash": ["transaction_hash", "id", "trade_id"],
    }

    # 1. Copy existing monthly files to output as base
    for d in [args.legacy_dir, args.output_dir]:
        if d == args.output_dir:
            continue
        if d.exists():
            for f in d.glob("trades_*.parquet"):
                dest = args.output_dir / f.name
                if not dest.exists() or dest.stat().st_mtime < f.stat().st_mtime:
                    import shutil
                    shutil.copy2(f, dest)
                    print(f"  Copied base: {f.name}")

    # 2. Stream trades.parquet in chunks
    if not args.single_file.exists():
        print("No trades.parquet found; only consolidated existing monthly files.")
        return 0

    print(f"Streaming {args.single_file} (batch_size={args.chunk_rows}, min_usd={args.min_usd:,.0f})...")
    month_buffers = {}
    batches = 0

    pf = pq.ParquetFile(args.single_file)
    for batch in pf.iter_batches(batch_size=args.chunk_rows):
        df = batch.to_pandas()
        if df.empty:
            continue

        out = _ensure_columns(df, SCHEMA)
        if out.empty or "market_id" not in out.columns:
            continue

        if args.min_usd > 0 and "usd_amount" in out.columns:
            out = out[pd.to_numeric(out["usd_amount"], errors="coerce").fillna(0) >= args.min_usd]
            if out.empty:
                continue

        ts = pd.to_datetime(out["timestamp"], errors="coerce")
        out["_month"] = ts.dt.strftime("%Y-%m")
        out = out[out["_month"].notna()]

        for month, g in out.groupby("_month"):
            g = g.drop(columns=["_month"], errors="ignore")
            if month not in month_buffers:
                month_buffers[month] = []
            month_buffers[month].append(g)

        batches += 1
        if batches % 20 == 0:
            print(f"  Batches: {batches}, months: {len(month_buffers)}")
            gc.collect()

    # 3. Flush: merge with existing, dedupe, write monthly (trades_YYYY-MM.parquet)
    # Monthly schema: one file per month, TradeStore-compatible
    TRADESTORE_COLS = ["timestamp", "market_id", "maker", "taker", "price", "usd_amount", "token_amount", "transaction_hash"]

    for month, parts in month_buffers.items():
        new_rows = pd.concat(parts, ignore_index=True)
        key_cols = [c for c in ["transaction_hash", "market_id", "timestamp", "maker", "taker"] if c in new_rows.columns]

        # Convert string cols to object to avoid Arrow dictionary_encode OOM during drop_duplicates
        for c in key_cols:
            if c in new_rows.columns:
                col = new_rows[c]
                if hasattr(col.dtype, "pyarrow_dtype") and col.dtype.pyarrow_dtype is not None:
                    new_rows[c] = col.astype(object)
                elif pd.api.types.is_string_dtype(col) and str(col.dtype) != "object":
                    new_rows[c] = col.astype(object)

        new_rows = new_rows.drop_duplicates(subset=key_cols) if key_cols else new_rows

        # Keep only TradeStore columns, consistent order
        new_rows = new_rows[[c for c in TRADESTORE_COLS if c in new_rows.columns]]

        out_path = args.output_dir / f"trades_{month}.parquet"
        if out_path.exists():
            existing = pd.read_parquet(out_path)
            combined = pd.concat([existing, new_rows], ignore_index=True)
            # Convert to object to avoid Arrow OOM on drop_duplicates
            for c in key_cols:
                if c in combined.columns and hasattr(combined[c].dtype, "pyarrow_dtype") and combined[c].dtype.pyarrow_dtype is not None:
                    combined[c] = combined[c].astype(object)
            combined = combined.drop_duplicates(subset=key_cols) if key_cols else combined
            del existing
        else:
            combined = new_rows

        combined = combined[[c for c in TRADESTORE_COLS if c in combined.columns]]
        # use_dictionary=False avoids 4GB+ ArrowMemoryError on string columns
        import pyarrow as pa
        tab = pa.Table.from_pandas(combined, preserve_index=False)
        pq.write_table(
            tab,
            out_path,
            use_dictionary=False,
            row_group_size=50_000,
            compression="zstd",
        )
        print(f"  {out_path.name}: {len(combined):,} rows")
        del tab, combined, new_rows
        gc.collect()

    print("Done. Output: monthly schema (trades_YYYY-MM.parquet) in", args.output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
