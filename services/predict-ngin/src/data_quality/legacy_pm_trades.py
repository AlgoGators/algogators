"""Compatibility view: the old monthly Polymarket trade extract, served from data/polymarket/trades.parquet.

``data/polymarket/trades/trades_YYYY-MM.parquet`` (12 columns, built from the HF ``trades.parquet`` by
scripts/data/merge_trades.py and kept until 2026-10-17) was deleted as a duplicate: every row is a row of
``data/polymarket/trades.parquet``, which is kept. Legacy readers (``TradeStore``, the arbitrage backtest) use this
module when the monthly files are gone, so they run unchanged:

* same columns and conventions: ``timestamp`` is a ``'YYYY-MM-DD HH:MM:SS'`` UTC string, prices are raw per-token
  (``TradeStore.load_trades`` YES-normalises them via ``nonusdc_side`` as before), ``transactionHash`` is
  ``0x``-prefixed; ``market_cum_liquidity_usd`` was never read by any script and is not served;
* same rows up to about 0.2%: rows with no market_id are skipped (no consumer can use them); the extract
  dropped zero-dollar rows (also dropped here) and exact duplicates
  (not repeated here: about 0.2% of rows through 2025-10-07); it ended 2025-10-07, this ends with trades.parquet
  (2025-12-30).

New code should read ``predict_ngin.tapes.polymarket.clean_trades_sql`` (YES-normalised, through today) instead.
"""
from __future__ import annotations

import calendar
import datetime as dt
from pathlib import Path
from typing import List, Optional, Sequence

from predict_ngin.shared.duck import sql_path
from predict_ngin.shared.paths import data_root

COLUMNS = ["timestamp", "market_id", "maker", "taker", "nonusdc_side", "maker_direction", "taker_direction",
           "price", "usd_amount", "token_amount", "transactionHash"]
_EXPR = {"timestamp": "strftime(to_timestamp(timestamp), '%Y-%m-%d %H:%M:%S')",
         "transactionHash": "'0x' || lower(transaction_hash)"}


def source(base: Optional[Path] = None) -> Path:
    """``trades.parquet`` next to the old monthly folder (``base`` = that folder, default <data>/polymarket/trades)."""
    return (Path(base).parent if base else data_root() / "polymarket") / "trades.parquet"


def available(base: Optional[Path] = None) -> bool:
    return source(base).exists()


def _epoch(day: str, end: bool = False) -> int:
    d = dt.datetime.fromisoformat(day[:19]) if len(day) > 10 else dt.datetime.fromisoformat(day)
    if end and len(day) <= 10:
        d += dt.timedelta(days=1)  # an end date includes its whole day, as the string compare did
    return calendar.timegm(d.timetuple())


def trades_sql(columns: Optional[Sequence[str]] = None, start_date: Optional[str] = None,
               end_date: Optional[str] = None, min_usd: float = 0.0, base: Optional[Path] = None) -> str:
    """DuckDB SELECT with the old extract's columns; dates are 'YYYY-MM-DD' (end inclusive) or full timestamps."""
    cols = [c for c in (columns or COLUMNS) if c in COLUMNS]
    sel = ", ".join(f"{_EXPR.get(c, c)} AS \"{c}\"" for c in cols)
    where = ["usd_amount > 0", f"usd_amount >= {float(min_usd)!r}", "coalesce(market_id, '') <> ''"]
    if start_date:
        where.append(f"timestamp >= {_epoch(start_date)}")
    if end_date:
        where.append(f"timestamp {'<' if len(end_date) <= 10 else '<='} {_epoch(end_date, end=True)}")
    return f"SELECT {sel} FROM read_parquet({sql_path(source(base))}) WHERE {' AND '.join(where)}"


def months(base: Optional[Path] = None) -> List[str]:
    """Months covered, from the file's row-group statistics (no scan)."""
    import pyarrow.parquet as pq

    md = pq.ParquetFile(source(base)).metadata
    i = md.schema.to_arrow_schema().get_field_index("timestamp")
    lo = min(md.row_group(r).column(i).statistics.min for r in range(md.num_row_groups))
    hi = max(md.row_group(r).column(i).statistics.max for r in range(md.num_row_groups))
    out = []
    y, m = map(int, dt.datetime.fromtimestamp(lo, dt.timezone.utc).strftime("%Y %m").split())
    end = dt.datetime.fromtimestamp(hi, dt.timezone.utc).strftime("%Y-%m")
    while f"{y:04d}-{m:02d}" <= end:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out
