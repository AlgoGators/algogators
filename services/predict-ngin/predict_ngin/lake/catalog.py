"""Named DuckDB views over every dataset, so studies query ``kalshi_trades`` instead of file paths.

Each view reads the partitioned lake when it is built and the original files otherwise (see
``predict_ngin.tapes``). Views are registered on a connection at run time, so they always see the
current files; ``write_catalog`` also saves them to ``<data>/lake/catalog.duckdb`` for external
tools (DuckDB CLI, DBeaver).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from ..shared.duck import sql_path
from ..shared.paths import data_root
from ..tapes import kalshi, polymarket
from ..tapes._lake import is_partition


@dataclass(frozen=True)
class View:
    name: str
    description: str
    sql: Callable[[], Optional[str]]  # None when the data is not on this machine


def _glob_sql(pattern: str, extra: str = "") -> Optional[str]:
    files = sorted(data_root().glob(pattern))
    if "month=*" in pattern:  # skip staging folders of a running build (month=03.building)
        files = [f for f in files if is_partition(f)]
    if not files:
        return None
    return (
        f"SELECT * FROM read_parquet([{', '.join(sql_path(f) for f in files)}], "
        f"union_by_name=true){extra}"
    )


def _try(fn: Callable[[], str]) -> Callable[[], Optional[str]]:
    def run() -> Optional[str]:
        try:
            return fn()
        except FileNotFoundError:
            return None

    return run


def _pm_markets() -> Optional[str]:
    p = data_root() / "lake" / "polymarket" / "markets.parquet"
    return f"SELECT * FROM read_parquet({sql_path(p)})" if p.exists() else None


def _pm_cols() -> List[str]:
    return polymarket.ALL_COLUMNS + (polymarket.LAKE_ONLY if polymarket.lake_ready() else [])


VIEWS: List[View] = [
    View(
        "kalshi_trades",
        "every Kalshi trade since 2021 (count_fp = exact contracts)",
        _try(lambda: kalshi.tape_sql(kalshi.ALL_COLUMNS)),
    ),
    View(
        "kalshi_markets",
        "Kalshi markets seen since 2026-01-28 with status, result and settlement (latest fetch)",
        lambda: _glob_sql(
            "kalshi/trades_fixed_ext/markets/part_*.parquet",
            " QUALIFY row_number() OVER (PARTITION BY ticker ORDER BY fetched_at DESC) = 1",
        ),
    ),
    View(
        "polymarket_fills",
        "every Polymarket on-chain fill since 2022, YES-normalised (incl. taker-order rows)",
        _try(lambda: polymarket.clean_trades_sql(_pm_cols())),
    ),
    View(
        "polymarket_maker_fills",
        "polymarket_fills without taker-order aggregate rows: use for counts and volume",
        _try(lambda: polymarket.clean_trades_sql(_pm_cols(), maker_rows_only=True)),
    ),
    View(
        "polymarket_markets",
        "one row per Polymarket market with event, tags, category, is_updown",
        _pm_markets,
    ),
    View(
        "polymarket_bars_1h",
        "hourly per-market YES-price OHLC / VWAP / volume from maker fills (lake only)",
        lambda: _glob_sql("lake/polymarket/bars_1h/year=*/month=*/*.parquet"),
    ),
    View(
        "polymarket_bars_1m",
        "1-minute per-market YES-price OHLC / VWAP / volume from maker fills (lake only)",
        lambda: _glob_sql("lake/polymarket/bars_1m/year=*/month=*/*.parquet"),
    ),
    View(
        "kalshi_bars_1h",
        "hourly per-market YES-price OHLC / VWAP / contracts / notional (lake only)",
        lambda: _glob_sql("lake/kalshi/bars_1h/year=*/month=*/*.parquet"),
    ),
    View(
        "kalshi_bars_1m",
        "1-minute per-market YES-price bars (lake only)",
        lambda: _glob_sql("lake/kalshi/bars_1m/year=*/month=*/*.parquet"),
    ),
    View(
        "kalshi_lake_markets",
        "one row per Kalshi market seen in the tape: series, category, result, close (lake only)",
        lambda: _glob_sql("lake/kalshi/markets.parquet"),
    ),
    View(
        "kalshi_books",
        "recorded Kalshi order-book snapshots (atlas recorder)",
        lambda: _glob_sql("live_books/kalshi/*/books-*.parquet"),
    ),
    View(
        "kalshi_live_trades",
        "Kalshi trades seen by the recorder (seconds latency)",
        lambda: _glob_sql("live_books/kalshi/*/trades-*.parquet"),
    ),
    View(
        "polymarket_books",
        "recorded Polymarket order-book snapshots (atlas recorder)",
        lambda: _glob_sql("live_books/polymarket/*/books-*.parquet"),
    ),
    View(
        "polymarket_live_trades",
        "Polymarket trades seen by the recorder",
        lambda: _glob_sql("live_books/polymarket/*/trades-*.parquet"),
    ),
]


def register(con) -> List[dict]:
    """Create every available view on ``con``.

    Returns one row per view: name, available, description.
    """
    out = []
    for v in VIEWS:
        sql = v.sql()
        if sql is not None:
            con.execute(f"CREATE OR REPLACE VIEW {v.name} AS {sql}")
        out.append({"view": v.name, "available": sql is not None, "description": v.description})
    return out


def write_catalog(path: Optional[Path] = None) -> Path:
    """Persist the views to a DuckDB file for external tools.

    Re-run after data updates (file lists are fixed).
    """
    import duckdb

    p = path or data_root() / "lake" / "catalog.duckdb"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp.duckdb")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    register(con)
    con.close()
    tmp.replace(p)
    return p
