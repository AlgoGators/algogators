"""Shared research calculations and interfaces."""

from research_core.errors import ApplicationError, NotFoundError, ValidationError
from research_core.returns import compute_return_stats, compute_sharpe

__all__ = [
    "ApplicationError",
    "NotFoundError",
    "ValidationError",
    "compute_return_stats",
    "compute_sharpe",
]
