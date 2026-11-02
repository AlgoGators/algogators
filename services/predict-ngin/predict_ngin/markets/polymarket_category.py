"""Polymarket market category from Gamma event tags.

Polymarket tags every event with its own top-level section labels ("Sports", "Crypto",
"Politics", ...) plus finer ones. The category is the first section label found in
:data:`SECTIONS` order (more specific sections first, so a "Trump" crypto-token market is
Crypto, a Fed market is Economy), then the legacy ``category`` field, then a slug rule for
crypto up/down markets, else ``"Other"``.

Bump :data:`VERSION` when the rules change: the data lake sorts fill files by category, so
changed rules mean the affected months should be rebuilt (build_pm_lake.py --rebuild).
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

VERSION = 1

# Polymarket section tags, most specific first. Values are the category names used in the lake.
SECTIONS = [
    ("mentions", "Mentions"),
    ("weather", "Weather"),
    ("crypto", "Crypto"),
    ("sports", "Sports"),
    ("esports", "Sports"),
    ("economy", "Economy"),
    ("finance", "Finance"),
    ("business", "Finance"),
    ("tech", "Tech"),
    ("science", "Science"),
    ("geopolitics", "Geopolitics"),
    ("world", "Geopolitics"),
    ("politics", "Politics"),
    ("elections", "Politics"),
    ("culture", "Culture"),
    ("pop-culture", "Culture"),
]
CATEGORIES = sorted({c for _, c in SECTIONS} | {"Other"})

# legacy event.category values (pre-2024 events)
_LEGACY = {
    "sports": "Sports",
    "nba playoffs": "Sports",
    "olympics": "Sports",
    "chess": "Sports",
    "poker": "Sports",
    "crypto": "Crypto",
    "nfts": "Crypto",
    "us-current-affairs": "Politics",
    "global politics": "Geopolitics",
    "ukraine & russia": "Geopolitics",
    "pop-culture": "Culture",
    "art": "Culture",
    "business": "Finance",
    "science": "Science",
    "coronavirus": "Science",
}
_UPDOWN = re.compile(r"(updown|up-or-down)")


def _norm(s: Optional[str]) -> str:
    """Lower-case text; anything that is not a string (None, NaN, pandas NA) becomes ''."""
    return s.strip().lower() if isinstance(s, str) else ""


def category_of(
    tag_labels: Iterable[str] = (),
    tag_slugs: Iterable[str] = (),
    category_raw: Optional[str] = None,
    slug: Optional[str] = None,
) -> str:
    tags = {_norm(t) for t in tag_labels} | {_norm(t) for t in tag_slugs}
    for key, cat in SECTIONS:
        if key in tags:
            return cat
    legacy = _LEGACY.get(_norm(category_raw))
    if legacy:
        return legacy
    if isinstance(slug, str) and _UPDOWN.search(slug):
        return "Crypto"
    return "Other"


def is_updown(tag_labels: Iterable[str] = (), slug: Optional[str] = None) -> bool:
    """Crypto 'Up or Down' interval markets (5m / 15m / 1h / 4h ...).

    Most of Polymarket's fill count since 2025.
    """
    labels = {_norm(t) for t in tag_labels}
    return "up or down" in labels or bool(isinstance(slug, str) and _UPDOWN.search(slug))
