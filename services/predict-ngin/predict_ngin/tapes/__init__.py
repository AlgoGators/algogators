"""Historical trade-tape readers for Kalshi and Polymarket behind one interface.

Each reader returns a DuckDB SELECT over the whole tape and serves the partitioned data lake when
it is built, else the original files. See docs/DATA_LAKE.md.
"""

from .kalshi import tape_sql as kalshi_tape_sql
from .polymarket import clean_trades_sql as polymarket_fills_sql

__all__ = ["kalshi_tape_sql", "polymarket_fills_sql"]
