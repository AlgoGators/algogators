"""GET-only client for the SharpAPI REST API (https://docs.sharpapi.io, checked 2026-10-17).

* Base URL ``https://api.sharpapi.io/api/v1``; the key goes in the ``X-API-Key`` header. It is read
  from the environment variable ``SHARPAPI_KEY`` or the repo ``.env``.
* Lists come as ``{"data": [...], "pagination": {..., "has_more", "next_cursor", "next_offset"},
  "updated_at"}``. ``/odds`` and ``/odds/delta`` page with ``cursor`` (``limit`` at most 500).
* Errors come as ``{"error": {"code", "message", ...}}``; branch on ``code``. ``rate_limited``
  (429) carries ``retry_after`` in Unix **milliseconds**; the ``Retry-After`` header is seconds.
* Rate limits are per key per 60 s sliding window: Free 12/min (and a 60 s data delay), Hobby
  120, Pro 300, Sharp 1,000. ``X-RateLimit-Limit`` / ``-Remaining`` report them; the client
  sizes its token bucket from the limit header at 85% of the tier, so it never trips the limit.

Terms (sharpapi.io/terms, 2026-03-10): data may be stored and used internally (research and
modelling) indefinitely; redistributing raw data, or scraping around the API, is not allowed.
"""

from __future__ import annotations

import os
import time
from typing import Any, Iterator, Optional, Tuple

from ...shared.paths import repo_root
from ...shared.ratelimit import TokenBucket

BASE_URL = "https://api.sharpapi.io/api/v1"
KEY_VAR = "SHARPAPI_KEY"
PAGE = 500
SAFETY = 0.85  # share of the tier's per-minute limit we use


def api_key() -> Optional[str]:
    from ..kalshi.auth import read_dotenv

    return os.environ.get(KEY_VAR) or read_dotenv(repo_root() / ".env").get(KEY_VAR)


class SharpAPIError(RuntimeError):
    def __init__(self, status: int, code: str, message: str, body: Any = None):
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.body = status, code, body


class SharpAPIClient:
    """``get(path, params) -> (json, headers)``; ``pages(path, params)`` yields every row."""

    def __init__(
        self,
        key: Optional[str] = None,
        *,
        per_minute: float = 12,
        session=None,
        timeout: float = 30.0,
        retries: int = 6,
        sleep=time.sleep,
    ):
        self.key = key or api_key()
        if not self.key:
            raise SharpAPIError(0, "missing_api_key", f"set {KEY_VAR} in the environment or .env")
        if session is None:
            import requests

            session = requests.Session()
        self.session = session
        try:
            import requests

            self._net_errors: Tuple[type[BaseException], ...] = (requests.RequestException, OSError)
        except ImportError:  # pragma: no cover
            self._net_errors = (OSError,)
        self.session.headers.update({"X-API-Key": self.key, "Accept": "application/json"})
        self.timeout, self.retries, self._sleep = timeout, retries, sleep
        self.per_minute: Optional[float] = None
        self.bucket: Optional[TokenBucket] = None
        self._set_limit(per_minute)
        self.n_requests = self.n_429 = self.n_errors = 0
        self.last_headers: Any = {}

    def _set_limit(self, per_minute: float) -> None:
        if per_minute and per_minute != self.per_minute:
            self.per_minute = float(per_minute)
            rate = float(per_minute) * SAFETY / 60.0
            self.bucket = TokenBucket(rate, max(1.0, min(10.0, rate * 5)), sleep=self._sleep)

    @property
    def remaining(self) -> Optional[int]:
        v = self.last_headers.get("X-RateLimit-Remaining")
        return int(v) if v not in (None, "") else None

    @property
    def data_delay_s(self) -> Optional[float]:
        v = self.last_headers.get("X-Data-Delay")
        return float(v) if v not in (None, "") else None

    def get(self, path: str, params: Optional[dict] = None) -> Tuple[Any, Any]:
        url = path if path.startswith("http") else BASE_URL + path
        assert self.bucket is not None, "per_minute must be set"
        for attempt in range(self.retries):
            self.bucket.acquire()
            self.n_requests += 1
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
            except self._net_errors:  # network error: back off and retry
                self.n_errors += 1
                self._sleep(min(60.0, 2.0**attempt))
                continue
            h = r.headers  # case-insensitive (the API sends lowercase names)
            self.last_headers = h
            lim = h.get("X-RateLimit-Limit")
            if lim and str(lim).isdigit():
                self._set_limit(float(lim))
            if r.status_code == 429:
                self.n_429 += 1
                wait = h.get("Retry-After")
                self._sleep(
                    float(wait)
                    if wait and wait.replace(".", "").isdigit()
                    else min(60.0, 2.0 ** (attempt + 2))
                )
                continue
            if r.status_code >= 500:
                self.n_errors += 1
                self._sleep(min(60.0, 2.0**attempt))
                continue
            try:
                body = r.json()
            except ValueError:
                body = {"error": {"code": "bad_json", "message": r.text[:200]}}
            if isinstance(body, dict) and r.status_code < 400:
                rl = ((body.get("data") or {}) if isinstance(body.get("data"), dict) else {}).get(
                    "rate_limit"
                ) or {}
                n = rl.get("requests_per_minute") or rl.get("limit")
                if isinstance(n, (int, float)) and n > 0:  # /account and /account/usage
                    self._set_limit(float(n))
            if r.status_code >= 400:
                err = (body or {}).get("error") or {}
                raise SharpAPIError(
                    r.status_code, err.get("code", "http_error"), err.get("message", ""), body
                )
            return body, h
        raise SharpAPIError(0, "retries_exhausted", f"GET {path} failed {self.retries} times")

    def page(self, path: str, params: Optional[dict] = None) -> Tuple[list, Optional[str], bool]:
        """One page: (rows, next cursor, has_more)."""
        rows, nxt, more = self.page_any(path, params)
        return rows, nxt.get("cursor"), more

    def page_any(self, path: str, params: Optional[dict] = None) -> Tuple[list, dict, bool]:
        """One page: (rows, params for the next page, has_more). ``/odds`` and ``/odds/delta`` page
        by ``cursor``; other lists by ``offset``."""
        body, _ = self.get(path, params)
        pag = (body or {}).get("pagination") or {}
        data = (body or {}).get("data")
        rows = data if isinstance(data, list) else []
        nxt: dict = {}
        if pag.get("next_cursor"):
            nxt = {"cursor": pag["next_cursor"]}
        elif pag.get("next_offset") is not None:
            nxt = {"offset": pag["next_offset"]}
        return rows, nxt, bool(pag.get("has_more")) and bool(nxt)

    def pages(
        self, path: str, params: Optional[dict] = None, max_pages: int = 10_000
    ) -> Iterator[dict]:
        q = {"limit": PAGE, **(params or {})}
        for _ in range(max_pages):
            rows, nxt, more = self.page_any(path, q)
            yield from rows
            if not more:
                return
            q = {k: v for k, v in q.items() if k not in ("cursor", "offset")}
            q.update(nxt)
