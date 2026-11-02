"""
SQLite database for prediction market data.

Re-exports from the existing database module for backward compatibility.
"""

# Re-export from existing location
from trading.data_modules.database import (
    PredictionMarketDB,
    build_database,
    run_data_quality_check,
    DEFAULT_DB_PATH,
)

__all__ = [
    "PredictionMarketDB",
    "build_database",
    "run_data_quality_check",
    "DEFAULT_DB_PATH",
]
