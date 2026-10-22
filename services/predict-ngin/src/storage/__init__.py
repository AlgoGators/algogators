"""
Data storage module for prediction market data.

Provides unified interfaces for persisting data:
- Parquet: Columnar storage for trades, prices, and snapshots
- Database: SQLite for structured queries
- Liquidity: Specialized storage for orderbook snapshots
"""

from .base import BaseStore
from .parquet import TradeStore, PriceStore
from .database import PredictionMarketDB, build_database
from .liquidity import LiquidityStore

__all__ = [
    "BaseStore",
    "TradeStore",
    "PriceStore",
    "PredictionMarketDB",
    "build_database",
    "LiquidityStore",
]
