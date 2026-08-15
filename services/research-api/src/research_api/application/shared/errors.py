"""Application-layer exceptions.

The taxonomy lives in research-core; this module re-exports it so existing
``research_api.application.shared.errors`` imports keep working unchanged.
"""

from research_core.errors import ApplicationError, NotFoundError, ValidationError

__all__ = ["ApplicationError", "NotFoundError", "ValidationError"]
