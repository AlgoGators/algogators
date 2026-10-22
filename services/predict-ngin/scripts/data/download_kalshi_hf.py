#!/usr/bin/env python3
"""
Download and munge Kalshi dataset from HuggingFace to project schema.

Source: https://huggingface.co/datasets/TrevorJS/kalshi-trades

- 154M trades (16 parquet shards)
- 17M markets (4 parquet shards)
- Output: data/kalshi/markets.parquet, data/kalshi/trades/trades_YYYY-MM.parquet
- Optional: data/kalshi/prices/prices_YYYY-MM.parquet (derived from trades)

Pinned to dataset revision REV, so every setup gets the same rows.

Usage:
    # Download markets + trades to project schema (~5.7 GB)
    uv run python scripts/data/download_kalshi_hf.py

    # Also derive OHLCV prices from trades (for arbitrage backtest)
    python scripts/data/download_kalshi_hf.py --derive-prices

    # Filter markets by minimum volume
    python scripts/data/download_kalshi_hf.py --min-volume 200000

    # Output to custom directory
    python scripts/data/download_kalshi_hf.py --output-dir data/kalshi
"""

import argparse
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from predict_ngin.shared.paths import data_root  # noqa: E402

REPO_ID = "TrevorJS/kalshi-trades"
REV = "5c622baffd0c8eeac8001ba05525803f544e4953"  # 2026-01-30 upload (trades to 2026-01-28)
HF_BASE = f"hf://datasets/{REPO_ID}@{REV}"
HF_TRADES_GLOB = f"{HF_BASE}/trades-*.parquet"


def _configure_duckdb_utc(con) -> None:
    """Force the DuckDB session to UTC so ::varchar / strftime on TIMESTAMPTZ are UTC."""
    con.execute("SET TimeZone='UTC'")


def trades_month_sql(source: str = HF_TRADES_GLOB) -> str:
    """SQL selecting one UTC month of trades (param: 'YYYY-MM').

    created_time is cast to TIMESTAMPTZ (not TIMESTAMP): casting a string such as
    '2025-01-01 12:34:16-05' to TIMESTAMP silently drops the -05 offset, which
    shifted ~49% of rows 4-5h early in the original download.
    """
    return f"""
        SELECT
            trade_id,
            ticker,
            count,
            yes_price::double / 100.0 as yes_price,
            no_price::double / 100.0 as no_price,
            taker_side,
            strftime(created_time::timestamptz, '%Y-%m-%dT%H:%M:%S.%fZ') as created_time,
            (epoch_ms(created_time::timestamptz) // 1000)::bigint as timestamp_unix
        FROM read_parquet('{source}')
        WHERE strftime(created_time::timestamptz, '%Y-%m') = ?
    """

# Project schema (from fetcher.py)
TRADES_COLUMNS = [
    "trade_id", "ticker", "event_ticker", "yes_price", "no_price",
    "count", "taker_side", "created_time", "timestamp_unix",
]
MARKETS_COLUMNS = [
    "ticker", "event_ticker", "series_ticker", "market_type", "title", "subtitle",
    "status", "result", "yes_bid", "yes_ask", "no_bid", "no_ask", "last_price",
    "volume", "volume_24h", "open_interest", "created_time", "open_time",
    "close_time", "expiration_time", "settlement_value", "category",
]


def _ensure_deps():
    """Ensure required dependencies. DuckDB preferred for hf:// streaming; fallback to huggingface_hub."""
    try:
        import pandas
        import pyarrow
        import pyarrow.parquet
    except ImportError as e:
        print(f"Missing dependency: {e}")
        print("Install: pip install pandas pyarrow")
        sys.exit(1)
    try:
        import duckdb
        return True  # DuckDB available
    except ImportError:
        try:
            import huggingface_hub
            print("DuckDB not found. Using huggingface_hub fallback (downloads ~5.7GB first).")
            print("  For faster streaming: pip install duckdb")
            return False
        except ImportError:
            print("Install duckdb or huggingface_hub: pip install duckdb  # or pip install huggingface_hub")
            sys.exit(1)


def _run_fallback_hub(output_dir: Path, min_volume: float) -> None:
    """Fallback when DuckDB unavailable: download via huggingface_hub and process with pandas."""
    from huggingface_hub import snapshot_download
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    print("\n[Fallback] Downloading dataset via huggingface_hub (~5.7 GB)...")
    cache = Path(snapshot_download(REPO_ID, repo_type="dataset", revision=REV))
    print(f"  Cached at: {cache}")

    # Markets
    print("\n[1/3] Loading markets...")
    market_files = sorted(cache.glob("markets-*.parquet"))
    if not market_files:
        print("  No markets-*.parquet found")
        return
    dfs = []
    for i, f in enumerate(market_files):
        print(f"  Reading {f.name} ({i+1}/{len(market_files)})...")
        dfs.append(pd.read_parquet(f))
    df = pd.concat(dfs, ignore_index=True)
    df = df[df["volume"] >= min_volume]
    if df.empty:
        print("  No markets above min_volume")
        return

    df["subtitle"] = (df.get("yes_sub_title", "") or "").fillna("") + " / " + (df.get("no_sub_title", "") or "").fillna("")
    for col in ["series_ticker", "expiration_time", "category"]:
        df[col] = ""
    df["settlement_value"] = None
    out = df[[c for c in MARKETS_COLUMNS if c in df.columns]].copy()
    for c in MARKETS_COLUMNS:
        if c not in out.columns:
            out[c] = "" if c != "settlement_value" else None
    out = out[MARKETS_COLUMNS]
    out["title"] = out["title"].astype(str).str[:200]
    out["subtitle"] = out["subtitle"].astype(str).str[:200]
    output_dir.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(out, preserve_index=False), output_dir / "markets.parquet", compression="zstd", compression_level=3)
    print(f"  Saved {len(out):,} markets")

    map_df = pd.concat([pd.read_parquet(f)[["ticker", "event_ticker"]] for f in market_files]).drop_duplicates("ticker")
    event_map = dict(zip(map_df["ticker"], map_df["event_ticker"]))
    del dfs, df, out, map_df

    # Trades
    print("\n[2/3] Loading trades...")
    trade_files = sorted(cache.glob("trades-*.parquet"))
    trades_dir = output_dir / "trades"
    trades_dir.mkdir(parents=True, exist_ok=True)
    monthly: dict[str, list] = {}
    for i, f in enumerate(trade_files):
        tdf = pd.read_parquet(f)
        tdf["yes_price"] = tdf["yes_price"] / 100.0
        tdf["no_price"] = tdf["no_price"] / 100.0
        _ct = pd.to_datetime(tdf["created_time"], utc=True, format="ISO8601")
        tdf["timestamp_unix"] = (_ct - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(seconds=1)
        tdf["event_ticker"] = tdf["ticker"].map(lambda x: event_map.get(x, "")).fillna("")
        tdf["created_time"] = _ct.dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        tdf = tdf[TRADES_COLUMNS]
        for month, grp in tdf.groupby(_ct.dt.strftime("%Y-%m")):
            if month not in monthly:
                monthly[month] = []
            monthly[month].append(grp)
        print(f"  [{i+1}/{len(trade_files)}] {f.name}: {len(tdf):,} trades")
        del tdf

    total = 0
    for month, grps in sorted(monthly.items()):
        combined = pd.concat(grps, ignore_index=True)
        total += len(combined)
        out_path = trades_dir / f"trades_{month}.parquet"
        pq.write_table(pa.Table.from_pandas(combined, preserve_index=False), out_path, compression="zstd", compression_level=3)
        print(f"  Wrote {out_path.name}: {len(combined):,} rows")
    print(f"  Total trades: {total:,}")


def _load_markets_hf(output_dir: Path, min_volume: float) -> "dict[str, str]":
    """Load markets from HF, transform to project schema, write. Returns ticker->event_ticker map."""
    import duckdb
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    print("\n[1/3] Loading markets from HuggingFace...")
    t0 = time.time()

    print("  Connecting DuckDB, loading httpfs...")
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    print(f"  Ready in {time.time() - t0:.1f}s")

    print("  Fetching markets (hf://datasets/.../markets-*.parquet, ~1.1 GB)...")
    print("  (First fetch streams ~1.1 GB over network - may take 5-15 min depending on connection)")
    sys.stdout.flush()
    fetch_start = time.time()
    df = _duckdb_query_with_retry(con, f"""
        SELECT
            ticker,
            event_ticker,
            market_type,
            title,
            COALESCE(yes_sub_title, '') || ' / ' || COALESCE(no_sub_title, '') as subtitle,
            status,
            result,
            yes_bid, yes_ask, no_bid, no_ask, last_price,
            volume, volume_24h, open_interest,
            created_time::varchar as created_time,
            open_time::varchar as open_time,
            close_time::varchar as close_time
        FROM read_parquet('{HF_BASE}/markets-*.parquet')
        WHERE volume >= ?
    """, [min_volume])

    fetch_elapsed = time.time() - fetch_start
    print(f"  Fetched {len(df):,} markets in {fetch_elapsed:.1f}s ({fetch_elapsed/60:.1f} min)")

    con.close()

    if df.empty:
        print("  No markets above min_volume. Try --min-volume 0")
        return {}

    print("  Transforming schema...")
    # Add missing columns (HF dataset doesn't have these)
    df["series_ticker"] = ""
    df["expiration_time"] = ""
    df["settlement_value"] = None
    df["category"] = ""
    df["liquidity"] = 0

    # Reorder to match project schema
    out = df[[c for c in MARKETS_COLUMNS if c in df.columns]].copy()
    for col in MARKETS_COLUMNS:
        if col not in out.columns:
            out[col] = "" if col != "settlement_value" else None

    out = out[MARKETS_COLUMNS]
    out["title"] = out["title"].astype(str).str[:200]
    out["subtitle"] = out["subtitle"].astype(str).str[:200]
    out["volume"] = out["volume"].fillna(0).astype("int64")
    out["volume_24h"] = out["volume_24h"].fillna(0).astype("int64")
    out["open_interest"] = out["open_interest"].fillna(0).astype("int64")

    out_path = output_dir / "markets.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"  Writing {out_path}...")
    pq.write_table(
        pa.Table.from_pandas(out, preserve_index=False),
        out_path, compression="zstd", compression_level=3,
    )
    print(f"  [1/3] Done: {len(out):,} markets in {time.time() - t0:.1f}s total")

    return dict(zip(out["ticker"], out["event_ticker"]))


def _duckdb_query_with_retry(con, sql: str, params=None, max_retries: int = 5):
    """Execute DuckDB query with retry on HTTP 429 rate limit."""
    import duckdb
    last_err = None
    for attempt in range(max_retries):
        try:
            if params is not None:
                return con.execute(sql, params).fetchdf()
            return con.execute(sql).fetchdf()
        except Exception as e:
            last_err = e
            err_str = str(e).lower()
            if "429" in err_str or "rate" in err_str or "too many requests" in err_str:
                wait = min(60 * (2 ** attempt), 300)  # 60s, 120s, 240s, 300s, 300s
                print(f"  HTTP 429 rate limit - waiting {wait}s before retry ({attempt + 1}/{max_retries})...")
                sys.stdout.flush()
                time.sleep(wait)
            else:
                raise
    raise last_err


def _load_trades_hf(output_dir: Path, event_ticker_map: dict) -> None:
    """Load trades from HF, join event_ticker, transform, write monthly parquet."""
    import duckdb
    import pyarrow as pa
    import pyarrow.parquet as pq

    trades_dir = output_dir / "trades"
    trades_dir.mkdir(parents=True, exist_ok=True)

    print("\n[2/3] Loading trades from HuggingFace (streaming by month)...")
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    _configure_duckdb_utc(con)

    # Get distinct months
    print("  Scanning trades for month list (reads ~4.5 GB)...")
    sys.stdout.flush()
    t0 = time.time()
    months_df = _duckdb_query_with_retry(con, f"""
        SELECT DISTINCT strftime(created_time::timestamptz, '%Y-%m') as month
        FROM read_parquet('{HF_BASE}/trades-*.parquet')
        ORDER BY month
    """)
    months = months_df["month"].tolist()
    print(f"  Months: {months[0]} .. {months[-1]} ({len(months)} total) in {time.time() - t0:.1f}s")

    total_rows = 0
    for i, month in enumerate(months):
        # Query this month's trades (with retry on 429)
        df = _duckdb_query_with_retry(con, trades_month_sql(), [month])

        if df.empty:
            continue

        # Add event_ticker from map (tickers not in map get "")
        df["event_ticker"] = df["ticker"].map(lambda t: event_ticker_map.get(t, "")).fillna("")

        # Reorder columns
        df = df[TRADES_COLUMNS]

        out_path = trades_dir / f"trades_{month}.parquet"
        pq.write_table(
            pa.Table.from_pandas(df, preserve_index=False),
            out_path, compression="zstd", compression_level=3,
        )
        total_rows += len(df)
        print(f"  [{i+1}/{len(months)}] {month}: {len(df):,} trades -> {out_path.name}")

        # Brief pause between months to reduce rate-limit risk
        if i < len(months) - 1:
            time.sleep(2)

    con.close()
    print(f"  Total trades: {total_rows:,}")


def _derive_prices_from_trades(
    output_dir: Path,
    trades_subdir: str = "trades",
    prices_subdir: str = "prices",
    overwrite: bool = False,
) -> None:
    """Aggregate trades into daily OHLCV candlesticks for KalshiPriceStore.

    Existing price files are never overwritten unless ``overwrite`` is set.
    """
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    from src.trading.arbitrage.kalshi_data import derive_daily_candles

    trades_dir = output_dir / "trades"
    prices_dir = output_dir / "prices"
    prices_dir.mkdir(parents=True, exist_ok=True)

    print("\n[3/3] Deriving OHLCV prices from trades...")
    trade_files = sorted(trades_dir.glob("trades_*.parquet"))
    if not trade_files:
        print("  No trade files found. Run without --derive-prices first.")
        return

    total_rows = 0
    for f in trade_files:
        month = f.stem.replace("trades_", "")
        df = pd.read_parquet(f)
        if df.empty:
            continue
        # NOTE: the old code did pd.to_datetime(date).astype("int64") // 10**9, which
        # under pandas>=2 (datetime64[s]) produced timestamp == 1 for every row.
        # Candles are stamped at the period END (close known only then).
        out = derive_daily_candles(df)
        out_path = prices_dir / f"prices_{month}.parquet"
        if out_path.exists() and not overwrite:
            print(f"  {out_path} exists - skipping (pass --overwrite-prices to replace)")
            continue
        pq.write_table(pa.Table.from_pandas(out, preserve_index=False), out_path, compression="zstd", compression_level=3)
        total_rows += len(out)
        print(f"  {month}: {len(out):,} daily candles -> {out_path.name}")

    print(f"  Total price rows: {total_rows:,}")


def main():
    parser = argparse.ArgumentParser(
        description="Download Kalshi dataset from HuggingFace and munge to project schema",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--output-dir",
        default=str(data_root() / "kalshi"),
        help="Output directory (default: <data root>/kalshi)",
    )
    parser.add_argument(
        "--min-volume",
        type=float,
        default=0,
        help="Minimum market volume to include (default: 0)",
    )
    parser.add_argument(
        "--derive-prices",
        action="store_true",
        help="Derive OHLCV prices from trades (for arbitrage backtest)",
    )
    parser.add_argument(
        "--derive-prices-only",
        action="store_true",
        help="Skip download; only derive prices from existing trades (requires data/kalshi/trades/)",
    )
    parser.add_argument(
        "--trades-subdir",
        default="trades",
        help="Trades subdirectory to derive prices from (e.g. trades_fixed after repair)",
    )
    parser.add_argument(
        "--prices-subdir",
        default="prices_derived",
        help="Where derived prices are written (default: prices_derived; never the original prices/)",
    )
    parser.add_argument(
        "--overwrite-prices",
        action="store_true",
        help="Allow overwriting existing derived price files",
    )
    parser.add_argument(
        "--use-hub-fallback",
        action="store_true",
        help="Use huggingface_hub to download first, then process locally (avoids HF rate limits)",
    )
    args = parser.parse_args()

    use_duckdb = _ensure_deps() and not args.use_hub_fallback
    output_dir = Path(args.output_dir)

    print("=" * 60)
    print("KALSHI HUGGINGFACE DATASET")
    print("=" * 60)
    print(f"Source: huggingface.co/datasets/{REPO_ID}")
    print(f"Output: {output_dir}")
    print(f"Min volume: {args.min_volume:,.0f}")
    print(f"Derive prices: {args.derive_prices}")
    if args.use_hub_fallback:
        print("Mode: huggingface_hub (download first, avoids rate limits)")
    print("=" * 60)

    t0 = time.time()

    if args.derive_prices_only:
        _derive_prices_from_trades(output_dir, args.trades_subdir, args.prices_subdir, args.overwrite_prices)
        elapsed = time.time() - t0
        print(f"\nDone in {elapsed:.1f}s")
        return

    if use_duckdb:
        _load_markets_hf(output_dir, args.min_volume)
        print("  Loading ticker->event_ticker map for trades (full scan)...")
        map_t0 = time.time()
        import duckdb
        con = duckdb.connect()
        con.execute("INSTALL httpfs; LOAD httpfs;")
        map_df = _duckdb_query_with_retry(con, f"""
            SELECT ticker, event_ticker FROM read_parquet('{HF_BASE}/markets-*.parquet')
        """)
        event_map = dict(zip(map_df["ticker"], map_df["event_ticker"]))
        con.close()
        print(f"  Loaded {len(event_map):,} mappings in {time.time() - map_t0:.1f}s")
        _load_trades_hf(output_dir, event_map)
    else:
        _run_fallback_hub(output_dir, args.min_volume)

    if args.derive_prices:
        _derive_prices_from_trades(output_dir, args.trades_subdir, args.prices_subdir, args.overwrite_prices)
    else:
        print("\n[3/3] Skipped (use --derive-prices to create OHLCV from trades)")

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed / 60:.1f} minutes")
    print(f"  Markets: {output_dir / 'markets.parquet'}")
    print(f"  Trades:  {output_dir / 'trades'}")
    if args.derive_prices:
        print(f"  Prices:  {output_dir / args.prices_subdir}")


if __name__ == "__main__":
    main()
