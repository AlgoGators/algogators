"""
Data collection module for prediction market data.

Provides unified interfaces for fetching data from various sources:
- Trades: Historical trade data from exchanges
- Prices: Historical price/probability data
- Liquidity: Orderbook snapshots and depth data
"""

from .base import BaseCollector
from .trades import TradeCollector
from .prices import PriceCollector
from .liquidity import LiquiditySnapshot, LiquidityCollector

__all__ = [
    "BaseCollector",
    "TradeCollector",
    "PriceCollector",
    "LiquiditySnapshot",
    "LiquidityCollector",
]
