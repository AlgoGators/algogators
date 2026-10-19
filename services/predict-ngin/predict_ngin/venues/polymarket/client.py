"""``PolymarketReadClient``: read-only access to Gamma, the CLOB and the Data API.

One :class:`~predict_ngin.venues.http.ReadOnlyClient` per host (GET only, back-off on network
errors / 429 / 5xx), each paced by its own token bucket, sharing one HTTP session. No endpoint here
needs authentication, and nothing can place or cancel an order.

Endpoints (docs.polymarket.com, checked 2026-10-15):

* Gamma ``GET /markets``, ``/markets/{id}``, ``/events``: offset pagination (``limit``, ``offset``).
* CLOB ``GET /book?token_id=``, ``/prices-history?market=<token id>``, ``/markets``
  (cursor pagination: ``next_cursor``, ``"LTE="`` at the end).
* Data API ``GET /trades`` (``market`` = condition id, ``limit`` <= 10000, ``offset`` <= 10000).
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, Mapping, Optional

from predict_ngin.markets.polymarket_tokens import clob_token_ids, yes_no_token_ids
from predict_ngin.shared.ratelimit import TokenBucket
from predict_ngin.venues.http import ReadOnlyClient, default_backoff
from predict_ngin.venues.polymarket.endpoints import CLOB_URL, DATA_API_URL, GAMMA_URL

__all__ = ["CLOB_END_CURSOR", "DEFAULT_RATES", "PolymarketReadClient"]

CLOB_END_CURSOR = "LTE="
DATA_API_MAX_OFFSET = 10_000
# Requests per second per host: 90% of the strictest published limit among the endpoints used on
# each host (docs.polymarket.com rate limits, checked 2026-10-17, per IP over a sliding 10 s):
# Gamma /markets 300, CLOB /prices-history 1000, Data API /trades 200. The limits are known, so the
# pace is fixed rather than probed; the buckets hold one token, so they never burst.
# (Was 5 / 10 / 3 until 2026-10-17.)
DEFAULT_RATES: Dict[str, float] = {"gamma": 27.0, "clob": 90.0, "data": 18.0}
USER_AGENT = "predict-ngin (read-only research)"


class PolymarketReadClient:
    """Read-only Polymarket client. ``rates`` overrides the per-host request rates (req/s)."""

    def __init__(
        self,
        *,
        session: Any = None,
        rates: Optional[Mapping[str, float]] = None,
        retries: int = 6,
        backoff=default_backoff,
        timeout: float = 30.0,
        user_agent: Optional[str] = USER_AGENT,
        sleep=None,
    ):
        if session is None:
            import requests

            session = requests.Session()
        if user_agent:
            session.headers.update({"User-Agent": user_agent})
        self.session = session
        r = dict(DEFAULT_RATES, **(rates or {}))

        def host(url: str, name: str) -> ReadOnlyClient:
            return ReadOnlyClient(
                url,
                session=session,
                limiter=TokenBucket(r[name], 1.0),
                retries=retries,
                backoff=backoff,
                timeout=timeout,
                none_on=frozenset({404}),
                sleep=sleep,
            )

        self.gamma = host(GAMMA_URL, "gamma")
        self.clob = host(CLOB_URL, "clob")
        self.data = host(DATA_API_URL, "data")

    # ------------------------------------------------------------------ Gamma
    def gamma_markets(self, **params) -> list:
        return self.gamma.get("/markets", params) or []

    def gamma_market(self, market_id) -> Optional[dict]:
        return self.gamma.get(f"/markets/{market_id}")

    def gamma_events(self, **params) -> list:
        return self.gamma.get("/events", params) or []

    def iter_gamma(
        self, path: str, params: Optional[dict] = None, page: int = 100, max_pages: int = 10_000
    ) -> Iterator[dict]:
        """Yield records from an offset-paginated Gamma list (``/markets``, ``/events``)."""
        for k in range(max_pages):
            q = dict(params or {})
            q.update(limit=page, offset=k * page)
            rows = self.gamma.get(path, q) or []
            yield from rows
            if len(rows) < page:
                return

    # ------------------------------------------------------------------ CLOB
    def clob_book(self, token_id: str) -> Optional[dict]:
        return self.clob.get("/book", {"token_id": token_id})

    def clob_prices_history(self, token_id: str, **params) -> list:
        """``[{"t": epoch s, "p": price}, ...]`` for one token (``interval``/``startTs``/...)."""
        out = self.clob.get("/prices-history", {"market": token_id, **params}) or {}
        return out.get("history") or []

    def iter_clob_markets(self) -> Iterator[dict]:
        cursor = ""
        while True:
            page = self.clob.get("/markets", {"next_cursor": cursor} if cursor else None) or {}
            yield from page.get("data") or []
            cursor = page.get("next_cursor") or CLOB_END_CURSOR
            if cursor == CLOB_END_CURSOR:
                return

    # ------------------------------------------------------------------ Data API
    def data_trades(self, **params) -> list:
        return self.data.get("/trades", params) or []

    def iter_data_trades(self, params: Optional[dict] = None, page: int = 500) -> Iterator[dict]:
        """Yield trades page by page until a short page or the API's offset limit."""
        offset = 0
        while offset <= DATA_API_MAX_OFFSET:
            q = dict(params or {})
            q.update(limit=page, offset=offset)
            rows = self.data.get("/trades", q) or []
            yield from rows
            if len(rows) < page:
                return
            offset += page

    # ------------------------------------------------------------------ tokens
    @staticmethod
    def token_ids(market: Mapping) -> list[str]:
        return clob_token_ids(market)

    @staticmethod
    def yes_no_tokens(market: Mapping):
        return yes_no_token_ids(market)
