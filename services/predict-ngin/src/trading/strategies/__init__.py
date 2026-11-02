"""
Trading Strategies Library

A collection of pre-implemented strategies for prediction markets.

Strategies:
- whale: Follow high win-rate traders
- momentum: Follow price trends
- mean_reversion: Bet on price extremes reverting
- smart_money: Track institutional-sized trades
- breakout: Enter on range breakouts
- volatility: Trade volatility patterns
- event_catalyst: Trade around known events
- time_decay: Exploit expiry dynamics
- liquidity: Market making approach
- sentiment: Trade sentiment divergence
"""

from .base import BaseStrategy
from .whale import WhaleFollowingStrategy
from .composite import CompositeStrategy
from .smart_money import SmartMoneyStrategy
from .cross_market import CrossMarketStrategy

try:
    from .momentum import MomentumStrategy
except ImportError:  # pragma: no cover - optional strategy module
    MomentumStrategy = None

try:
    from .mean_reversion import MeanReversionStrategy
except ImportError:  # pragma: no cover - optional strategy module
    MeanReversionStrategy = None

try:
    from .breakout import BreakoutStrategy, VolatilityBreakoutStrategy
except ImportError:  # pragma: no cover - optional strategy module
    BreakoutStrategy = None
    VolatilityBreakoutStrategy = None

try:
    from .time_decay import TimeDecayStrategy
except ImportError:  # pragma: no cover - optional strategy module
    TimeDecayStrategy = None

try:
    from .sentiment import SentimentDivergenceStrategy
except ImportError:  # pragma: no cover - optional strategy module
    SentimentDivergenceStrategy = None

try:
    from .nlp_correlation import NLPCorrelationStrategy
except ImportError:  # pragma: no cover - optional strategy module
    NLPCorrelationStrategy = None

# New strategies will be imported here as they're created

__all__ = [
    "BaseStrategy",
    "WhaleFollowingStrategy",
    "CompositeStrategy",
    "SmartMoneyStrategy",
    "CrossMarketStrategy",
]

_optional = {
    "MomentumStrategy": MomentumStrategy,
    "MeanReversionStrategy": MeanReversionStrategy,
    "BreakoutStrategy": BreakoutStrategy,
    "VolatilityBreakoutStrategy": VolatilityBreakoutStrategy,
    "TimeDecayStrategy": TimeDecayStrategy,
    "SentimentDivergenceStrategy": SentimentDivergenceStrategy,
    "NLPCorrelationStrategy": NLPCorrelationStrategy,
}
for _name, _value in _optional.items():
    if _value is not None:
        __all__.append(_name)
