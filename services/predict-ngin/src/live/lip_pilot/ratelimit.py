"""Token-bucket rate limiting (Kalshi charges write tokens: order 10, each batched cancel 2).

Shim: ``TokenBucket`` lives in ``predict_ngin.shared.ratelimit``.
"""
from __future__ import annotations

from predict_ngin.shared.ratelimit import TokenBucket

__all__ = ["TokenBucket"]
