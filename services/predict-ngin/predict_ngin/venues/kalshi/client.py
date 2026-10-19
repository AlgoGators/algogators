"""``KalshiReadClient``: Kalshi trade-api v2 reads. GET only, by construction.

Built on :class:`predict_ngin.venues.http.ReadOnlyClient` (GET only, limiter per attempt, back-off
on network errors / 429 / 5xx with ``Retry-After``). Adds optional per-request authentication
(``auth=True``); the signed path never includes the query string. The base URL is
:data:`predict_ngin.venues.kalshi.endpoints.BASE_URL` unless overridden.
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlsplit

from predict_ngin.venues.http import (
    HTTPReadError,
    ReadOnlyClient,
    ReadOnlyViolation,
    RetriesExhausted,
    default_backoff,
)
from predict_ngin.venues.kalshi.endpoints import BASE_URL

__all__ = [
    "ReadOnlyViolation",
    "KalshiHTTPError",
    "KalshiRetriesExhausted",
    "default_backoff",
    "KalshiReadClient",
]

KalshiHTTPError = HTTPReadError
KalshiRetriesExhausted = RetriesExhausted


class KalshiReadClient(ReadOnlyClient):
    """GET-only Kalshi REST client.

    ``signer``: object with ``headers(method, path)`` (e.g. ``KalshiSigner``), needed for
    ``auth=True``. ``auth``: default for ``get(..., auth=None)``. Other parameters: see
    :class:`~predict_ngin.venues.http.ReadOnlyClient`.
    """

    def __init__(self, base_url: str = BASE_URL, *, signer: Any = None, auth: bool = False, **kw):
        super().__init__(base_url, **kw)
        self.signer = signer
        self.auth = auth

    def __repr__(self) -> str:
        return f"KalshiReadClient({self.base_url!r}, auth={self.auth})"

    def get(
        self,
        path: str,
        params: Optional[dict] = None,
        *,
        auth: Optional[bool] = None,
        retries: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """GET ``path`` and return the parsed JSON (None for a status in ``none_on``)."""
        sign = self.auth if auth is None else auth
        if sign and self.signer is None:
            raise RuntimeError(f"GET {path}: authenticated read without a signer")
        signer = self.signer

        def headers_fn(url: str):
            return signer.headers("GET", urlsplit(url).path)

        return self._get(path, params, retries, timeout, headers_fn if sign else None)
