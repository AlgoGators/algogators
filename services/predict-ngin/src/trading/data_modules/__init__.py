"""
Data modules for the trading system.

Note: This module is maintained for backward compatibility.
New code should import from the reorganized modules:
  - src.storage for parquet/database storage
  - src.collection for data fetching
  - src.taxonomy for market categorization
"""

from .costs import (
    CostAssumptions,
    CostModel,
    COST_ASSUMPTIONS,
    DEFAULT_COST_MODEL,
    POLYMARKET_ZERO_COST_MODEL,
)
from .categories import CATEGORIES, categorize_market, categorize_markets
from .data import (
    DEFAULT_DB_PATH,
    load_manifold_data,
    load_markets,
    build_resolution_map,
    train_test_split,
)
from .database import PredictionMarketDB, build_database
from .fetcher import DataFetcher, ensure_data_exists
from .parquet_store import TradeStore, PriceStore
from .trade_price_store import TradeBasedPriceStore

__all__ = [
    # Cost models
    "CostAssumptions",
    "CostModel",
    "COST_ASSUMPTIONS",
    "DEFAULT_COST_MODEL",
    "POLYMARKET_ZERO_COST_MODEL",
    # Categories (legacy - prefer src.taxonomy)
    "CATEGORIES",
    "categorize_market",
    "categorize_markets",
    # Data loading
    "DEFAULT_DB_PATH",
    "load_manifold_data",
    "load_markets",
    "build_resolution_map",
    "train_test_split",
    # Database
    "PredictionMarketDB",
    "build_database",
    # Fetcher
    "DataFetcher",
    "ensure_data_exists",
    # Parquet stores
    "TradeStore",
    "PriceStore",
    "TradeBasedPriceStore",
]
