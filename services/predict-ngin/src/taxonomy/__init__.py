"""
Market taxonomy module for classification.

Provides hierarchical market categorization using keyword and ML-based methods.
"""

from .markets import (
    MarketClassification,
    MarketTaxonomy,
    CATEGORIES,
    categorize_market,
    categorize_markets,
)

__all__ = [
    "MarketClassification",
    "MarketTaxonomy",
    "CATEGORIES",
    "categorize_market",
    "categorize_markets",
]
