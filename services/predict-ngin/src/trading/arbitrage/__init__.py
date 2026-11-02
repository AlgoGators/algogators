"""
Cross-platform arbitrage infrastructure for prediction markets.

Matches equivalent markets across Polymarket and Kalshi, detects
price discrepancies, and backtests arbitrage strategies.
"""

from .fees import FeeModel, kalshi_fee, polymarket_fee
from .market_matcher import MarketMatcher, MatchedPair
from .rule_matcher import RuleMatcher, validate_pair, extract_contract_terms
from .resolution_arb import ResolutionArbConfig, run_resolution_arb
from .cross_platform_price_store import CrossPlatformPriceStore
from .arbitrage_strategy import ArbitrageStrategy, ArbitrageSignal
from .cross_platform_backtest import (
    ArbitrageBacktestConfig,
    ArbitrageBacktestResult,
    run_arbitrage_backtest,
    print_arbitrage_result,
)

__all__ = [
    "FeeModel",
    "kalshi_fee",
    "polymarket_fee",
    "RuleMatcher",
    "validate_pair",
    "extract_contract_terms",
    "ResolutionArbConfig",
    "run_resolution_arb",
    "MarketMatcher",
    "MatchedPair",
    "CrossPlatformPriceStore",
    "ArbitrageStrategy",
    "ArbitrageSignal",
    "ArbitrageBacktestConfig",
    "ArbitrageBacktestResult",
    "run_arbitrage_backtest",
    "print_arbitrage_result",
]
