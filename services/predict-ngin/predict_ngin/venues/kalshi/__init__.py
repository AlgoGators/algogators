"""Kalshi anti-corruption layer: URLs, request signing and a GET-only read client.

Order placement is deliberately absent; the LIP pilot keeps its own gated trading client
(``src/live/lip_pilot/client.py``) and uses the signer from here.
"""

from predict_ngin.venues.kalshi.auth import (
    KalshiSigner,
    credentials,
    load_private_key,
    sign_message,
    signed_headers,
)
from predict_ngin.venues.kalshi.client import (
    KalshiHTTPError,
    KalshiReadClient,
    KalshiRetriesExhausted,
    ReadOnlyViolation,
    default_backoff,
)
from predict_ngin.venues.kalshi.endpoints import API_PREFIX, BASE_URL, HOST, WS_SIGN_PATH, WS_URL

__all__ = [
    "HOST",
    "API_PREFIX",
    "BASE_URL",
    "WS_URL",
    "WS_SIGN_PATH",
    "KalshiSigner",
    "credentials",
    "load_private_key",
    "sign_message",
    "signed_headers",
    "KalshiReadClient",
    "KalshiHTTPError",
    "KalshiRetriesExhausted",
    "ReadOnlyViolation",
    "default_backoff",
]
