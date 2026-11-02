#!/usr/bin/env python3
"""
Repair Kalshi trade timestamps produced by the old HF downloader.

The original ``download_kalshi_hf.py`` computed ``epoch(created_time::timestamp)``
which drops the UTC offset in strings like ``2025-01-01 12:34:16-05``; ~49% of
rows ended up 4-5 hours early. This script recomputes ``timestamp_unix`` from
``created_time`` (offset-aware) and writes the result to a NEW directory.
The original files are never modified.

Output columns: original schema, with ``timestamp_unix`` corrected and the old
value preserved as ``timestamp_unix_orig``. Rows are re-sorted by the corrected
timestamp. Files keep their original month name (month = created_time's month
in the source); use timestamp_unix for any time logic.

Usage:
    python scripts/data/repair_kalshi_trades.py                       # data/kalshi/trades -> data/kalshi/trades_fixed
    python scripts/data/repair_kalshi_trades.py --src X --dst Y
    python scripts/data/repair_kalshi_trades.py --then-derive-prices  # also write data/kalshi/prices_fixed
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from predict_ngin.shared.paths import data_root
from src.trading.arbitrage.kalshi_data import derive_daily_candles, recompute_timestamp_unix


def repair_file(src: Path, dst: Path) -> Dict[str, int]:
    """Repair one file. Uses DuckDB (streaming, low memory) when available."""
    try:
        import duckdb  # noqa: F401
    except ImportError:
        return _repair_file_pandas(src, dst)
    return _repair_file_duckdb(src, dst)


def _repair_file_duckdb(src: Path, dst: Path) -> Dict[str, int]:
    import duckdb

    s = str(src).replace("\\", "/").replace("'", "''")
    d = str(dst).replace("\\", "/").replace("'", "''")
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{s}')").fetchall()]
    if "created_time" not in cols:
        con.close()
        return _repair_file_pandas(src, dst)
    orig = "timestamp_unix" if "timestamp_unix" in cols else "NULL::BIGINT"
    others = ", ".join(f'"{c}"' for c in cols if c != "timestamp_unix")
    fixed = "(epoch_ms(try_cast(created_time AS TIMESTAMPTZ)) // 1000)::BIGINT"
    con.execute(
        f"""COPY (
              SELECT {others},
                     coalesce({fixed}, {orig}) AS timestamp_unix,
                     {orig} AS timestamp_unix_orig
              FROM read_parquet('{s}')
              ORDER BY timestamp_unix
            ) TO '{d}' (FORMAT PARQUET, COMPRESSION ZSTD)"""
    )
    rows, changed, unparse = con.execute(
        f"""SELECT count(*),
                   count(*) FILTER (WHERE timestamp_unix IS DISTINCT FROM timestamp_unix_orig),
                   count(*) FILTER (WHERE try_cast(created_time AS TIMESTAMPTZ) IS NULL)
            FROM read_parquet('{d}')"""
    ).fetchone()
    con.close()
    return {"rows": int(rows), "changed": int(changed), "unparseable": int(unparse)}


def _repair_file_pandas(src: Path, dst: Path) -> Dict[str, int]:
    df = pd.read_parquet(src)
    n = len(df)
    if n == 0 or "created_time" not in df.columns:
        pq.write_table(pa.Table.from_pandas(df, preserve_index=False), dst, compression="zstd")
        return {"rows": n, "changed": 0, "unparseable": 0}
    fixed = recompute_timestamp_unix(df)
    unparseable = int(fixed.isna().sum())
    orig = df["timestamp_unix"] if "timestamp_unix" in df.columns else pd.Series([pd.NA] * n)
    df["timestamp_unix_orig"] = orig
    # Keep the original value only where created_time could not be parsed
    df["timestamp_unix"] = fixed.fillna(orig).astype("int64")
    changed = int((df["timestamp_unix"] != df["timestamp_unix_orig"]).sum())
    df = df.sort_values("timestamp_unix", kind="mergesort").reset_index(drop=True)
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), dst, compression="zstd", compression_level=3)
    return {"rows": n, "changed": changed, "unparseable": unparseable}


def repair_dir(src_dir: Path, dst_dir: Path, pattern: str = "*.parquet", verbose: bool = False) -> Dict[str, int]:
    src_dir, dst_dir = Path(src_dir), Path(dst_dir)
    if src_dir.resolve() == dst_dir.resolve():
        raise ValueError("Refusing to repair in place: dst must differ from src")
    dst_dir.mkdir(parents=True, exist_ok=True)
    tot = {"files": 0, "rows": 0, "changed": 0, "unparseable": 0}
    for f in sorted(src_dir.glob(pattern)):
        st = repair_file(f, dst_dir / f.name)
        tot["files"] += 1
        for k in ("rows", "changed", "unparseable"):
            tot[k] += st[k]
        if verbose:
            pct = 100 * st["changed"] / max(st["rows"], 1)
            print(f"  {f.name}: {st['rows']:,} rows, {st['changed']:,} corrected ({pct:.1f}%), "
                  f"{st['unparseable']} unparseable", flush=True)
    return tot


def derive_prices(trades_dir: Path, prices_dir: Path) -> int:
    prices_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        import duckdb
    except ImportError:
        duckdb = None
    for f in sorted(Path(trades_dir).glob("trades_*.parquet")):
        if duckdb is not None:
            # Same semantics as derive_daily_candles (UTC day, stamped at period END), streaming.
            s = str(f).replace("\\", "/")
            out = duckdb.sql(
                f"""SELECT ticker,
                           (timestamp_unix // 86400) * 86400 + 86400 AS timestamp,
                           (timestamp_unix // 86400) * 86400 AS period_start_ts,
                           arg_min(yes_price, timestamp_unix) AS open, max(yes_price) AS high,
                           min(yes_price) AS low, arg_max(yes_price, timestamp_unix) AS close,
                           sum(count)::BIGINT AS volume
                    FROM read_parquet('{s}') WHERE timestamp_unix > 1
                    GROUP BY 1, 2, 3 ORDER BY 1, 2"""
            ).df()
            out["mean"] = (out["open"] + out["high"] + out["low"] + out["close"]) / 4
            out["open_interest"] = 0
        else:
            df = pd.read_parquet(f, columns=["ticker", "timestamp_unix", "yes_price", "count"])
            out = derive_daily_candles(df)
        pq.write_table(pa.Table.from_pandas(out, preserve_index=False),
                       prices_dir / f.name.replace("trades_", "prices_"), compression="zstd")
        total += len(out)
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default=str(data_root() / "kalshi/trades"))
    ap.add_argument("--dst", default=str(data_root() / "kalshi/trades_fixed"))
    ap.add_argument("--then-derive-prices", action="store_true")
    ap.add_argument("--prices-dst", default=str(data_root() / "kalshi/prices_fixed"))
    args = ap.parse_args()
    tot = repair_dir(Path(args.src), Path(args.dst), verbose=True)
    pct = 100 * tot["changed"] / max(tot["rows"], 1)
    print(f"Repaired {tot['files']} files, {tot['rows']:,} rows; {tot['changed']:,} timestamps corrected "
          f"({pct:.1f}%); {tot['unparseable']:,} unparseable kept as-is -> {args.dst}")
    if args.then_derive_prices:
        n = derive_prices(Path(args.dst), Path(args.prices_dst))
        print(f"Derived {n:,} daily candles -> {args.prices_dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
