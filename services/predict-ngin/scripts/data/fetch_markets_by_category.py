#!/usr/bin/env python3
"""
Fetch Polymarket markets from the Gamma API and save markets_filtered.csv
per category into data/poly_cat/{Category}/.

Runs both API endpoints in parallel with up to N_WORKERS threads each.
The CSV columns match the existing data/research markets_filtered.csv format
exactly, so it is a drop-in replacement for the research data.

Categories:
  Art_and_Culture, Climate_and_Science, Economy, Finance,
  Geopolitics, Politics, Sports, Tech, Other

Usage:
  python scripts/data/fetch_markets_by_category.py
  python scripts/data/fetch_markets_by_category.py --workers 35
  python scripts/data/fetch_markets_by_category.py --include-closed
  python scripts/data/fetch_markets_by_category.py --output-dir data/poly_cat
  python scripts/data/fetch_markets_by_category.py --dry-run
"""

import argparse
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Set

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

GAMMA_API  = "https://gamma-api.polymarket.com"
PAGE_SIZE  = 100

# ── Exact column order from data/research markets_filtered.csv ───────────────
CANONICAL_COLUMNS = [
    "id", "question", "conditionId", "slug", "resolutionSource",
    "endDate", "startDate", "fee", "image", "icon", "description",
    "outcomes", "outcomePrices", "volume", "active", "marketType",
    "closed", "marketMakerAddress", "updatedBy", "createdAt",
    "updatedAt", "closedTime", "wideFormat", "new", "featured",
    "submitted_by", "archived", "resolvedBy", "restricted",
    "groupItemTitle", "groupItemThreshold", "questionID", "umaEndDate",
    "enableOrderBook", "orderPriceMinTickSize", "orderMinSize",
    "umaResolutionStatus", "volumeNum", "endDateIso", "startDateIso",
    "hasReviewedDates", "commentsEnabled", "volume1wk", "volume1mo",
    "volume1yr", "secondsDelay", "clobTokenIds", "umaBond", "umaReward",
    "fpmmLive", "volume1wkAmm", "volume1moAmm", "volume1yrAmm",
    "volume1wkClob", "volume1moClob", "volume1yrClob", "volumeClob",
    "makerBaseFee", "takerBaseFee", "customLiveness", "acceptingOrders",
    "negRisk", "negRiskMarketID", "negRiskRequestID", "notificationsEnabled",
    "events", "creator", "ready", "funded", "cyom",
    "pagerDutyNotificationEnabled", "approved", "clobRewards",
    "rewardsMinSize", "rewardsMaxSpread", "spread", "automaticallyResolved",
    "oneDayPriceChange", "oneHourPriceChange", "oneWeekPriceChange",
    "oneMonthPriceChange", "oneYearPriceChange", "lastTradePrice",
    "bestBid", "bestAsk", "automaticallyActive", "clearBookOnStart",
    "seriesColor", "showGmpSeries", "showGmpOutcome", "manualActivation",
    "negRiskOther", "umaResolutionStatuses", "pendingDeployment",
    "deploying", "rfqEnabled", "holdingRewardsEnabled", "feesEnabled",
    "requiresTranslation", "acceptingOrdersTimestamp", "gameStartTime",
    "deployingTimestamp", "liquidity", "liquidityNum", "volume24hr",
    "volume24hrClob", "liquidityClob", "competitive", "liquidityAmm",
    "readyForCron", "category", "mailchimpTag", "gameId",
    "sportsMarketType", "eventStartTime", "sentDiscord", "volume24hrAmm",
    "volumeAmm", "groupItemRange", "twitterCardImage",
    "twitterCardLocation", "twitterCardLastRefreshed",
    "twitterCardLastValidated", "umaEndDateIso", "line", "disqusThread",
    "marketGroup", "categoryMailchimpTag", "lowerBound", "upperBound",
    "formatType", "makerRebatesFeeShareBps", "denominationToken",
    "teamAID", "teamBID", "sponsorImage", "subcategory", "createdBy",
    "topic1", "topic2",
]

# ── Category priority & tag mapping ──────────────────────────────────────────
CATEGORY_PRIORITY = [
    "Politics", "Sports", "Geopolitics", "Finance",
    "Economy", "Tech", "Climate_and_Science", "Art_and_Culture",
]

TAG_TO_CATEGORY: Dict[str, str] = {
    # Politics
    "politics": "Politics", "election": "Politics", "elections": "Politics",
    "us-elections": "Politics", "us elections": "Politics",
    "us-current-affairs": "Politics", "government": "Politics",
    "congress": "Politics", "senate": "Politics", "president": "Politics",
    "presidential": "Politics", "democratic": "Politics",
    "republican": "Politics", "political": "Politics", "voting": "Politics",
    "ballot": "Politics", "legislation": "Politics",
    "impeachment": "Politics", "2024 us elections": "Politics",
    "trump": "Politics", "biden": "Politics", "harris": "Politics",
    "white house": "Politics", "supreme court": "Politics",
    "midterms": "Politics", "cabinet": "Politics",
    "doge": "Politics",  # Dept of Govt Efficiency – disambiguated from crypto below
    # Sports
    "sports": "Sports", "nba": "Sports", "nfl": "Sports",
    "nhl": "Sports", "mlb": "Sports", "soccer": "Sports",
    "tennis": "Sports", "golf": "Sports", "mma": "Sports",
    "boxing": "Sports", "olympics": "Sports", "chess": "Sports",
    "poker": "Sports", "f1": "Sports", "formula 1": "Sports",
    "formula1": "Sports", "racing": "Sports", "basketball": "Sports",
    "football": "Sports", "baseball": "Sports", "hockey": "Sports",
    "esports": "Sports", "cricket": "Sports", "rugby": "Sports",
    "volleyball": "Sports", "swimming": "Sports", "ncaa": "Sports",
    "premier league": "Sports", "bundesliga": "Sports",
    "la liga": "Sports", "serie a": "Sports",
    "champions league": "Sports", "ufc": "Sports", "wwe": "Sports",
    "nascar": "Sports", "pga": "Sports", "wimbledon": "Sports",
    "nba playoffs": "Sports", "world cup": "Sports",
    "super bowl": "Sports", "march madness": "Sports",
    "mlb playoffs": "Sports", "conmebol": "Sports",
    "college sports": "Sports", "super rugby pacific": "Sports",
    # Geopolitics
    "geopolitics": "Geopolitics", "global politics": "Geopolitics",
    "international": "Geopolitics", "war": "Geopolitics",
    "russia": "Geopolitics", "ukraine": "Geopolitics",
    "middle east": "Geopolitics", "china": "Geopolitics",
    "nato": "Geopolitics", "united nations": "Geopolitics",
    "foreign policy": "Geopolitics", "diplomacy": "Geopolitics",
    "conflict": "Geopolitics", "sanctions": "Geopolitics",
    "terrorism": "Geopolitics", "north korea": "Geopolitics",
    "iran": "Geopolitics", "israel": "Geopolitics",
    "palestine": "Geopolitics", "taiwan": "Geopolitics",
    "europe": "Geopolitics", "eu": "Geopolitics",
    "european union": "Geopolitics", "india": "Geopolitics",
    "peace deals": "Geopolitics", "greenland": "Geopolitics",
    # Finance
    "finance": "Finance", "stocks": "Finance", "stock market": "Finance",
    "ipo": "Finance", "ipos": "Finance", "forex": "Finance",
    "bonds": "Finance", "derivatives": "Finance",
    "commodities": "Finance", "gold": "Finance", "oil": "Finance",
    "wall street": "Finance", "etf": "Finance",
    "interest rates": "Finance", "fed": "Finance",
    "federal reserve": "Finance", "inflation": "Finance",
    "s&p": "Finance", "nasdaq": "Finance", "dow": "Finance",
    "sp500": "Finance", "hedge fund": "Finance",
    "private equity": "Finance", "m&a": "Finance",
    # Economy
    "economy": "Economy", "business": "Economy", "trade": "Economy",
    "gdp": "Economy", "unemployment": "Economy", "recession": "Economy",
    "economic": "Economy", "commerce": "Economy", "tariffs": "Economy",
    "labor": "Economy", "supply chain": "Economy", "retail": "Economy",
    "housing market": "Economy", "real estate": "Economy",
    "jobs": "Economy",
    # Tech
    "tech": "Tech", "crypto": "Tech", "ai": "Tech", "nfts": "Tech",
    "web3": "Tech", "defi": "Tech", "blockchain": "Tech",
    "cryptocurrency": "Tech", "software": "Tech", "hardware": "Tech",
    "semiconductor": "Tech", "artificial intelligence": "Tech",
    "machine learning": "Tech", "bitcoin": "Tech", "ethereum": "Tech",
    "solana": "Tech", "chatgpt": "Tech", "openai": "Tech",
    "elon musk": "Tech",
    # Climate_and_Science
    "science": "Climate_and_Science", "climate": "Climate_and_Science",
    "environment": "Climate_and_Science",
    "coronavirus": "Climate_and_Science", "covid": "Climate_and_Science",
    "pandemic": "Climate_and_Science", "space": "Climate_and_Science",
    "health": "Climate_and_Science", "biology": "Climate_and_Science",
    "physics": "Climate_and_Science", "chemistry": "Climate_and_Science",
    "energy": "Climate_and_Science", "renewable": "Climate_and_Science",
    "nuclear": "Climate_and_Science", "weather": "Climate_and_Science",
    "astronomy": "Climate_and_Science", "nasa": "Climate_and_Science",
    "space exploration": "Climate_and_Science",
    # Art_and_Culture
    "art": "Art_and_Culture", "culture": "Art_and_Culture",
    "entertainment": "Art_and_Culture", "pop-culture": "Art_and_Culture",
    "pop culture": "Art_and_Culture", "movies": "Art_and_Culture",
    "music": "Art_and_Culture", "tv": "Art_and_Culture",
    "television": "Art_and_Culture", "film": "Art_and_Culture",
    "celebrity": "Art_and_Culture", "awards": "Art_and_Culture",
    "oscars": "Art_and_Culture", "grammys": "Art_and_Culture",
    "emmys": "Art_and_Culture", "fashion": "Art_and_Culture",
    "food": "Art_and_Culture", "anime": "Art_and_Culture",
    "books": "Art_and_Culture", "literature": "Art_and_Culture",
    "gaming": "Art_and_Culture",
}

# Fallback: market.category string (API raw value) → folder name
API_CATEGORY_MAP: Dict[str, str] = {
    "us-current-affairs": "Politics",
    "global politics":    "Geopolitics",
    "sports":             "Sports",
    "nba playoffs":       "Sports",
    "olympics":           "Sports",
    "chess":              "Sports",
    "poker":              "Sports",
    "crypto":             "Tech",
    "nfts":               "Tech",
    "tech":               "Tech",
    "space":              "Climate_and_Science",
    "science":            "Climate_and_Science",
    "coronavirus":        "Climate_and_Science",
    "business":           "Economy",
    "finance":            "Finance",
    "art":                "Art_and_Culture",
    "pop-culture":        "Art_and_Culture",
    "pop culture":        "Art_and_Culture",
}


# ── Thread-safe page counter ──────────────────────────────────────────────────

class _PageCounter:
    """Atomically dispenses page numbers until stop() is called."""
    def __init__(self):
        self._n   = 0
        self._lk  = threading.Lock()
        self._stop = False

    def next_offset(self) -> Optional[int]:
        with self._lk:
            if self._stop:
                return None
            off = self._n * PAGE_SIZE
            self._n += 1
            return off

    def stop(self):
        with self._lk:
            self._stop = True


# ── Session factory ───────────────────────────────────────────────────────────

# Global semaphore caps concurrent requests across all threads to avoid 429s.
# 12 is empirically safe for the Polymarket Gamma API without rate-limit drops.
_REQUEST_SEM = threading.Semaphore(12)

def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "predict-ngin/1.0 (research)"})
    retry = Retry(
        total=10,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        respect_retry_after_header=True,
    )
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=20))
    return s


# ── Parallel fetch helper ─────────────────────────────────────────────────────

def _fetch_all_parallel(
    endpoint: str,
    base_params: dict,
    n_workers: int,
    label: str,
) -> List[dict]:
    """
    Parallel pagination: workers race to fetch pages until one gets empty.
    A global semaphore caps simultaneous in-flight requests to avoid 429s.
    Returns flat list of all items.
    """
    counter  = _PageCounter()
    results: List[dict] = []
    lock     = threading.Lock()
    dot_lock = threading.Lock()
    pages_done = [0]

    session = _make_session()

    def worker():
        local: List[dict] = []
        while True:
            offset = counter.next_offset()
            if offset is None:
                break
            with _REQUEST_SEM:
                try:
                    r = session.get(
                        endpoint,
                        params={**base_params, "offset": offset, "limit": PAGE_SIZE},
                        timeout=30,
                    )
                    r.raise_for_status()
                    batch = r.json()
                except Exception as exc:
                    # Transient error – skip page, do not stop
                    print(f"\n  WARN [{label}] offset={offset}: {exc}")
                    continue

            if not batch:
                counter.stop()
                break

            local.extend(batch)
            with dot_lock:
                pages_done[0] += 1
                if pages_done[0] % 10 == 0:
                    print(".", end="", flush=True)

        with lock:
            results.extend(local)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return results


# ── Classification helpers ────────────────────────────────────────────────────

def _tags_to_category(tags: List[dict]) -> Optional[str]:
    if not tags:
        return None
    matched: Set[str] = set()
    for tag in tags:
        for key in (
            tag.get("label", "").lower().strip(),
            tag.get("slug",  "").lower().strip(),
        ):
            if key in TAG_TO_CATEGORY:
                matched.add(TAG_TO_CATEGORY[key])
    if not matched:
        return None
    for cat in CATEGORY_PRIORITY:
        if cat in matched:
            return cat
    return next(iter(matched))


def _api_category_to_folder(raw: Optional[str]) -> str:
    if not raw:
        return "Other"
    return API_CATEGORY_MAP.get(raw.lower().strip(), "Other")


# ── Main logic ────────────────────────────────────────────────────────────────

def run(output_dir: Path, n_workers: int, include_closed: bool, dry_run: bool,
        min_volume: float = 0.0):
    t0 = time.time()

    # ── Phase 1: fetch events + markets simultaneously ────────────────────
    print("Phase 1: fetching from Gamma API ...")

    event_params  = {"active": "true"}
    market_params = {}
    if not include_closed:
        event_params["closed"]  = "false"
        market_params["closed"] = "false"

    # Run both fetches in parallel using a thread pool
    with ThreadPoolExecutor(max_workers=2) as outer:
        f_events  = outer.submit(
            _fetch_all_parallel,
            f"{GAMMA_API}/events",  event_params,  n_workers, "events"
        )
        f_markets = outer.submit(
            _fetch_all_parallel,
            f"{GAMMA_API}/markets", market_params, n_workers, "markets"
        )
        print(f"  events  ", end="", flush=True)
        raw_events  = f_events.result()
        print(f"\n  markets ", end="", flush=True)
        raw_markets = f_markets.result()

    print(f"\n  Got {len(raw_events):,} events, {len(raw_markets):,} markets "
          f"({time.time()-t0:.1f}s)")

    # ── Phase 2: build event_id → category map ────────────────────────────
    print("Phase 2: building event->category map ...")
    event_cat: Dict[str, str] = {}   # str(event_id) → category
    for evt in raw_events:
        tags = evt.get("tags") or []
        cat  = _tags_to_category(tags)
        if cat:
            event_cat[str(evt.get("id", ""))] = cat

    print(f"  {len(event_cat):,} events with known category")

    # ── Phase 3: classify & normalise markets ─────────────────────────────
    print("Phase 3: classifying markets ...")
    buckets: Dict[str, List[dict]] = defaultdict(list)
    seen_ids: Set[str] = set()

    for m in raw_markets:
        cid = str(m.get("conditionId") or m.get("id") or "").strip()
        if not cid or cid in seen_ids:
            continue
        seen_ids.add(cid)

        # Volume filter
        if min_volume > 0:
            vol = float(m.get("volumeNum") or m.get("volume") or 0)
            if vol < min_volume:
                continue

        # Try event tags first (most accurate)
        cat = None
        evts_field = m.get("events") or []
        if isinstance(evts_field, list):
            for evt_ref in evts_field:
                if isinstance(evt_ref, dict):
                    eid = str(evt_ref.get("id") or "")
                    if eid in event_cat:
                        cat = event_cat[eid]
                        break

        # Fallback to market's own category string
        if cat is None:
            cat = _api_category_to_folder(m.get("category"))

        # Flatten nested dicts/lists → str for CSV compatibility
        flat: dict = {}
        for k, v in m.items():
            if isinstance(v, (list, dict)):
                flat[k] = str(v)
            else:
                flat[k] = v

        flat["category"] = cat

        # Ensure liquidityNum / volumeNum exist
        if "liquidityNum" not in flat:
            flat["liquidityNum"] = flat.get("liquidity", 0) or 0
        if "volumeNum" not in flat:
            flat["volumeNum"]    = flat.get("volume", 0) or 0

        buckets[cat].append(flat)

    print(f"  {sum(len(v) for v in buckets.values()):,} unique markets classified")

    # ── Phase 4: write CSVs ───────────────────────────────────────────────
    print("Phase 4: writing CSVs ...")
    all_cats = CATEGORY_PRIORITY + ["Other"]

    print(f"\n{'Category':<22}  {'Markets':>8}  Output")
    print("-" * 72)

    for cat in all_cats:
        rows = buckets.get(cat, [])
        if not rows:
            print(f"  {cat:<20}  {'0':>8}  (no markets)")
            continue

        df = pd.DataFrame(rows)

        # Re-order columns to match canonical format; add missing cols as NaN
        for col in CANONICAL_COLUMNS:
            if col not in df.columns:
                df[col] = pd.NA
        extra_cols = [c for c in df.columns if c not in CANONICAL_COLUMNS]
        df = df[CANONICAL_COLUMNS + extra_cols]

        out_path = output_dir / cat / "markets_filtered.csv"

        if dry_run:
            print(f"  {cat:<20}  {len(df):>8}  [dry-run] {out_path}")
        else:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(out_path, index=False)
            print(f"  {cat:<20}  {len(df):>8}  {out_path}")

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s.")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(
        description="Fetch Polymarket markets from the Gamma API, split by category"
    )
    p.add_argument("--output-dir", default="data/poly_cat",
                   help="Base output directory (default: data/poly_cat)")
    p.add_argument("--workers", type=int, default=35,
                   help="Number of parallel fetch threads (default: 35)")
    p.add_argument("--include-closed", action="store_true",
                   help="Include closed/resolved markets (default: active only)")
    p.add_argument("--min-volume", type=float, default=0.0,
                   help="Minimum market volume in USD to include (default: 0)")
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would be written without creating files")
    args = p.parse_args()

    output_dir = Path(args.output_dir)
    print("=" * 72)
    print("FETCH POLYMARKET MARKETS BY CATEGORY")
    print("=" * 72)
    print(f"Output dir    : {output_dir}")
    print(f"Workers       : {args.workers}")
    print(f"Include closed: {args.include_closed}")
    print(f"Min volume    : ${args.min_volume:,.0f}")
    print(f"Dry run       : {args.dry_run}")
    print()

    run(output_dir, args.workers, args.include_closed, args.dry_run,
        min_volume=args.min_volume)
    return 0


if __name__ == "__main__":
    sys.exit(main())
