"""DataProvider backed by the Wikimedia Pageviews REST API — free, keyless
daily view counts for a Wikipedia article. Used as a crude "public attention"
proxy: pageview spikes on a company, asset, or macro topic's article tend to
track retail search interest (e.g. meme-stock rallies, recession scares).

"Symbol" here is a short code mapped to a specific Wikipedia article title
(see `ARTICLES`), not a ticker. Counts are "user" agent only (bots/spiders
excluded) so the series reflects human traffic.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import requests

from algoterminal.data import cache
from algoterminal.data.provider import AssetClass, DataProvider

_BASE_URL = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
# Wikimedia asks API consumers to identify themselves with a descriptive
# User-Agent; requests without one are sometimes throttled or refused.
_HEADERS = {"User-Agent": "algoterminal-cli/1.0 (Data tab Wikipedia-attention provider)"}
# Pageview data only exists from mid-2015 onward.
_EARLIEST = date(2015, 7, 1)

# Symbol code -> (Wikipedia article title, human description).
ARTICLES: dict[str, tuple[str, str]] = {
    "GAMESTOP": ("GameStop", "GameStop Corp. — 2021 meme-stock short-squeeze bellwether"),
    "AMC": ("AMC_Entertainment_Holdings", "AMC Entertainment — meme-stock cohort alongside GameStop"),
    "TESLA": ("Tesla,_Inc.", "Tesla, Inc. — high-retail-attention large-cap equity"),
    "NVIDIA": ("Nvidia", "Nvidia — AI-buildout bellwether"),
    "BITCOIN": ("Bitcoin", "Bitcoin — retail crypto attention proxy"),
    "ARTIFICIAL_INTELLIGENCE": ("Artificial_intelligence", "General public interest in AI as a macro theme"),
    "RECESSION": ("Recession", "Public search interest in economic recession, a soft sentiment indicator"),
    "INFLATION": ("Inflation", "Public search interest in inflation"),
    "FEDERAL_RESERVE": ("Federal_Reserve", "Public attention on US Federal Reserve policy"),
}


class WikiPageviewsProvider(DataProvider):
    """Daily Wikipedia article pageviews (human traffic only), by topic."""

    name = "wiki-pageviews"

    def fetch(
        self,
        symbol: str,
        asset_class: AssetClass = AssetClass.ALT_DATA,
        start: date | None = None,
        end: date | None = None,
    ) -> pd.DataFrame:
        start = start or (date.today() - timedelta(days=365 * 3))
        end = end or (date.today() - timedelta(days=2))  # pageviews data lags a couple of days
        start = max(start, _EARLIEST)

        cached = cache.read(self.name, symbol)
        gaps = cache.missing_ranges(cached, start, end)
        if not gaps:
            return cached.loc[str(start) : str(end)]

        fresh_frames = [f for f in (self._download(symbol, s, e) for s, e in gaps) if not f.empty]
        fresh = pd.concat(fresh_frames) if fresh_frames else pd.DataFrame()
        merged = cache.merge_and_write(self.name, symbol, cached, fresh)

        if merged.empty:
            return merged
        return merged.loc[str(start) : str(end)]

    @staticmethod
    def _download(symbol: str, start: date, end: date) -> pd.DataFrame:
        article = ARTICLES.get(symbol.upper())
        if article is None:
            return pd.DataFrame()
        title, _desc = article

        url = "/".join(
            [
                _BASE_URL,
                "en.wikipedia",
                "all-access",
                "user",
                title,
                "daily",
                start.strftime("%Y%m%d"),
                end.strftime("%Y%m%d"),
            ]
        )
        try:
            response = requests.get(url, headers=_HEADERS, timeout=20)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return pd.DataFrame()

        items = payload.get("items", [])
        if not items:
            return pd.DataFrame()

        rows = [(pd.to_datetime(item["timestamp"][:8]), item["views"]) for item in items]
        df = pd.DataFrame(rows, columns=["date", "close"]).set_index("date").sort_index()
        df.index.name = "date"
        return df
