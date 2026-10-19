"""
Live trading modules for prediction markets.

Modules:
- paper_trading: Simulated live execution
- order_router: Polymarket CLOB order routing
- position_monitor: Real-time P/L and alerts
- execution_logger: Trade logging and slippage tracking
- realtime_signals: Real-time whale signal pipeline
"""

from .realtime_signals import (
    Trade,
    WhaleSignal,
    SignalType,
    WhaleSignalGenerator,
    RealTimeTradeStream,
    RealTimeSignalPipeline,
)

__all__ = [
    "Trade",
    "WhaleSignal",
    "SignalType",
    "WhaleSignalGenerator",
    "RealTimeTradeStream",
    "RealTimeSignalPipeline",
]
