"""``ReadOnlyClient``: the one GET-only HTTP read loop shared by every venue client.

* GET only, by construction: :meth:`ReadOnlyClient.request` raises :class:`ReadOnlyViolation` for
  any other method, and the HTTP session is only ever called through its ``get``.
* A rate limiter (token bucket by default) is acquired before every attempt. A limiter with a
  ``feedback(status, latency, retry_after)`` method (``AimdLimiter``) is told every answer.
* Network errors, 429 and 5xx are retried with a back-off; ``Retry-After`` is honoured.
* Statuses in ``none_on`` return None; other 4xx call the response's ``raise_for_status()``
  (so ``requests.HTTPError`` / ``httpx.HTTPStatusError`` propagate as before).

The session can be a ``requests.Session`` (default) or an ``httpx.Client``: both expose
``get(url, params=, headers=, timeout=)`` and a response with ``status_code``, ``headers``,
``json()`` and ``raise_for_status()``.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Iterator, Optional

from predict_ngin.shared.ratelimit import TokenBucket

__all__ = [
    "ReadOnlyViolation",
    "HTTPReadError",
    "RetriesExhausted",
    "default_backoff",
    "ReadOnlyClient",
]

READ_METHODS = frozenset({"GET"})
RETRY_STATUS_MIN = 500

HeadersFn = Callable[[str], Optional[Dict[str, str]]]


class ReadOnlyViolation(RuntimeError):
    """A non-GET request was attempted on a read-only client."""


class HTTPReadError(RuntimeError):
    def __init__(self, status: int, path: str, body: Any = None):
        super().__init__(f"GET {path} -> HTTP {status}")
        self.status = status
        self.body = body


class RetriesExhausted(RuntimeError):
    """Every attempt failed with a network error, 429 or 5xx."""


def default_backoff(attempt: int, status: Optional[int], retry_after: Optional[float]) -> float:
    """Seconds to wait before the next attempt: ``max(Retry-After, min(60, 2 ** attempt))``."""
    return max(retry_after or 0.0, min(60.0, 2.0**attempt))


def _retry_after(headers) -> Optional[float]:
    try:
        v = headers.get("Retry-After") if headers is not None else None
        return float(v) if v else None
    except (TypeError, ValueError):
        return None


def _network_errors() -> tuple:
    errs: list = [ConnectionError, TimeoutError]
    try:
        import requests

        errs.append(requests.RequestException)
    except ImportError:  # pragma: no cover
        pass
    try:
        import httpx

        errs.append(httpx.TransportError)
    except ImportError:  # pragma: no cover
        pass
    return tuple(errs)


class ReadOnlyClient:
    """GET-only JSON client for one base URL.

    Parameters
    ----------
    base_url: prefix for relative paths (absolute ``http...`` paths are used as given).
    session: ``requests.Session`` / ``httpx.Client``; a new ``requests.Session`` by default.
    limiter: object with ``acquire()``; default ``TokenBucket(10/s, burst 10)``.
    retries: attempts per request (default 8).
    backoff: ``(attempt, status or None, retry_after or None) -> seconds``.
    none_on: statuses for which ``get`` returns None instead of raising (e.g. ``{404}``).
    network_errors: exception types treated as retryable network errors.
    headers: default headers set on the session (e.g. a User-Agent).
    """

    def __init__(
        self,
        base_url: str = "",
        *,
        session: Any = None,
        limiter: Any = None,
        retries: int = 8,
        backoff: Callable[[int, Optional[int], Optional[float]], float] = default_backoff,
        timeout: float = 60.0,
        none_on: frozenset = frozenset(),
        network_errors: Optional[tuple] = None,
        headers: Optional[Dict[str, str]] = None,
        sleep: Optional[Callable[[float], None]] = None,
    ):
        self.base_url = base_url.rstrip("/")
        if session is None:
            import requests

            session = requests.Session()
        self.session = session
        if headers:
            self.session.headers.update(headers)
        self.limiter = limiter if limiter is not None else TokenBucket(10.0, 10.0)
        self._feedback = getattr(self.limiter, "feedback", None)
        self.retries = retries
        self.backoff = backoff
        self.timeout = timeout
        self.none_on = frozenset(none_on)
        self.network_errors = network_errors if network_errors is not None else _network_errors()
        self._sleep_fn = sleep
        self.n_requests = 0
        self.n_retries = 0
        self.n_429 = 0

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.base_url!r})"

    def _sleep(self, seconds: float) -> None:
        (self._sleep_fn or time.sleep)(seconds)

    def url(self, path: str) -> str:
        return path if path.startswith("http") else self.base_url + path

    def request(self, method: str, path: str, params=None, **kw) -> Any:
        """Only GET is allowed; anything else raises :class:`ReadOnlyViolation`."""
        if method.upper() not in READ_METHODS:
            raise ReadOnlyViolation(f"{method.upper()} {path} refused: read-only client")
        return self.get(path, params, **kw)

    def get(
        self,
        path: str,
        params: Optional[dict] = None,
        *,
        retries: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """GET ``path`` and return the parsed JSON (None for a status in ``none_on``)."""
        return self._get(path, params, retries, timeout, None)

    def _get(
        self,
        path: str,
        params: Optional[dict],
        retries: Optional[int],
        timeout: Optional[float],
        headers_fn: Optional[HeadersFn],
    ) -> Any:
        url = self.url(path)
        n = self.retries if retries is None else retries
        for attempt in range(n):
            self.limiter.acquire()
            headers = headers_fn(url) if headers_fn is not None else None
            self.n_requests += 1
            t0 = time.monotonic()
            try:
                r = self.session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self.timeout if timeout is None else timeout,
                )
            except self.network_errors:
                self.n_retries += 1
                self._sleep(self.backoff(attempt, None, None))
                continue
            status = r.status_code
            if self._feedback is not None:
                self._feedback(status, time.monotonic() - t0, _retry_after(r.headers))
            if status == 429 or status >= RETRY_STATUS_MIN:
                self.n_retries += 1
                self.n_429 += status == 429
                self._sleep(self.backoff(attempt, status, _retry_after(r.headers)))
                continue
            if status in self.none_on:
                return None
            if status >= 400:
                r.raise_for_status()
                raise HTTPReadError(status, path, getattr(r, "text", None))
            return r.json()
        raise RetriesExhausted(f"GET {path} failed after {n} retries")

    def paginate(self, path: str, key: str, params: Optional[dict] = None, **kw) -> Iterator[dict]:
        """Yield the items under ``key`` across ``cursor`` pages (Kalshi style)."""
        q = dict(params or {})
        while True:
            page = self.get(path, q, **kw) or {}
            yield from page.get(key) or []
            cur = page.get("cursor")
            if not cur:
                return
            q["cursor"] = cur
