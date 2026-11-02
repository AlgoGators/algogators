"""Shared error taxonomy for research members.

Class names are kept verbatim from research-api's application layer so its
re-export preserves exception identity and ``except`` semantics.
"""


class ApplicationError(Exception):
    """Base application exception."""


class NotFoundError(ApplicationError):
    """Requested resource was not found."""


class ValidationError(ApplicationError):
    """Request data failed application validation."""
