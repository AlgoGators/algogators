"""Discover *candidate* cross-venue pairs (Fed decision, CPI, major sports).

Matches are fuzzy (title tokens + close-date proximity) and are written for HUMAN
REVIEW -- resolution criteria differ between venues (e.g. Fed "cut 25bps" vs
"rate above X%", CPI MoM vs YoY, sports "regulation vs incl. OT"). Never copy a
candidate into config/recorder.yaml without reading both rule texts.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

log = logging.getLogger("recorder.discovery")

KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"
GAMMA = "https://gamma-api.polymarket.com"
PM_US = "https://gateway.polymarket.us"

TOPICS = {
    "fed": {"kalshi_series": ["KXFEDDECISION", "KXFED"], "gamma_tags": ["fed-rates", "fed"], "pm_us_category": ("finance", "macro", "politics")},
    "cpi": {"kalshi_series": ["KXCPI", "KXCPIYOY"], "gamma_tags": ["cpi", "inflation"], "pm_us_category": ("finance", "macro", "politics")},
    "sports": {
        "kalshi_series": ["KXNFLGAME", "KXNBAGAME", "KXMLBGAME", "KXNHLGAME"],
        "gamma_tags": ["nfl", "nba", "mlb", "nhl"],
        "pm_us_category": ("sports",),
    },
}

_STOP = {
    "will", "the", "a", "an", "of", "by", "at", "in", "on", "to", "be", "for", "after", "before",
    "is", "than", "or", "and", "vs", "v", "win", "wins", "market", "meeting", "game", "what", "how",
}
_SYN = {
    "decrease": "cut", "decreases": "cut", "lower": "cut", "lowers": "cut", "cuts": "cut",
    "hike": "raise", "hikes": "raise", "increase": "raise", "increases": "raise", "raises": "raise",
    "rates": "rate", "bp": "bps", "basis": "bps", "points": "", "federal": "fed", "reserve": "",
    "fomc": "fed", "interest": "", "inflation": "cpi", "dec": "december", "nov": "november",
    "oct": "october", "sep": "september", "jan": "january",
}


def tokens(text: str) -> set:
    text = text.lower().replace(">", " gt ").replace("+", " plus ")
    text = re.sub(r"(\d)([a-zA-Z])", r"\1 \2", text)
    out = set()
    for t in re.findall(r"[a-z]+|\d+(?:\.\d+)?", text):
        t = _SYN.get(t, t)
        if t and t not in _STOP:
            out.add(t)
    return out


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def score_match(title_a: str, title_b: str, close_a: Optional[str], close_b: Optional[str]) -> float:
    """0..1 similarity: 0.7 * token Jaccard + 0.3 * close-date proximity (half-life ~5 days)."""
    ta, tb = tokens(title_a), tokens(title_b)
    jac = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    da, db = _parse_dt(close_a), _parse_dt(close_b)
    if da and db:
        days = abs((da - db).total_seconds()) / 86400
        date_s = math.exp(-days / 7.0)
    else:
        date_s = 0.3
    return round(0.7 * jac + 0.3 * date_s, 4)


# ---------------------------------------------------------------------------
# Fetchers (sync httpx; this is a one-off helper, not the hot path)
# ---------------------------------------------------------------------------


def kalshi_markets(client, series: Iterable[str]) -> List[dict]:
    out = []
    for s in series:
        cursor = None
        for _ in range(5):
            params = {"series_ticker": s, "status": "open", "with_nested_markets": "true", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            r = client.get(f"{KALSHI_API}/events", params=params)
            if r.status_code != 200:
                log.warning("kalshi events %s -> %s", s, r.status_code)
                break
            d = r.json()
            for e in d.get("events") or []:
                for m in e.get("markets") or []:
                    sub = m.get("yes_sub_title") or ""
                    out.append({
                        "venue": "kalshi", "id": m["ticker"],
                        "title": f"{e.get('title', '')} {m.get('title', '')} {sub}".strip(),
                        "close": m.get("close_time"),
                        "bid": m.get("yes_bid_dollars"), "ask": m.get("yes_ask_dollars"),
                        "volume": m.get("volume_fp") or m.get("volume"),
                    })
            cursor = d.get("cursor")
            if not cursor:
                break
    return out


def gamma_markets(client, tags: Iterable[str], horizon_days: Optional[int] = None) -> List[dict]:
    out, seen = [], set()
    now = datetime.now(timezone.utc)
    for tag in tags:
        r = client.get(f"{GAMMA}/events", params={"closed": "false", "active": "true", "limit": 500, "tag_slug": tag})
        if r.status_code != 200:
            continue
        for e in r.json() or []:
            for m in e.get("markets") or []:
                if m.get("closed") or not m.get("enableOrderBook", True):
                    continue
                end = _parse_dt(m.get("endDate") or e.get("endDate"))
                if horizon_days is not None and (end is None or end < now or end > now + timedelta(days=horizon_days)):
                    continue
                toks = m.get("clobTokenIds")
                if isinstance(toks, str):
                    import json
                    try:
                        toks = json.loads(toks)
                    except ValueError:
                        toks = None
                if not toks or m.get("id") in seen:
                    continue
                seen.add(m.get("id"))
                outcomes = m.get("outcomes")
                out.append({
                    "venue": "polymarket", "id": m.get("conditionId"),
                    "title": f"{e.get('title', '')} {m.get('question', '')} {m.get('groupItemTitle') or ''}".strip(),
                    "close": m.get("endDate"), "tokens": [str(t) for t in toks], "outcomes": outcomes,
                    "bid": m.get("bestBid"), "ask": m.get("bestAsk"), "volume": m.get("volume24hr"),
                })
    return out


def pm_us_markets(client, categories: Iterable[str], pages: int = 40) -> List[dict]:
    out = []
    for i in range(pages):
        r = client.get(f"{PM_US}/v1/markets", params={"limit": 100, "offset": i * 100, "active": "true", "closed": "false"})
        if r.status_code != 200:
            break
        ms = r.json().get("markets") or []
        for m in ms:
            if m.get("category") not in categories or m.get("closed"):
                continue
            out.append({
                "venue": "polymarket_us", "id": m.get("slug"),
                "title": f"{m.get('question', '')} {m.get('description', '')[:120]}",
                "close": m.get("endDate"), "bid": m.get("bestBid"), "ask": m.get("bestAsk"),
            })
        if len(ms) < 100:
            break
    return out


def match(kalshi: List[dict], others: List[dict], min_score: float = 0.35, top_k: int = 2) -> List[dict]:
    cands = []
    for k in kalshi:
        scored = sorted(((score_match(k["title"], o["title"], k["close"], o["close"]), o) for o in others),
                        key=lambda x: -x[0])[:top_k]
        for s, o in scored:
            if s >= min_score:
                cands.append({"score": s, "kalshi": k, "other": o})
    cands.sort(key=lambda c: -c["score"])
    return cands


def to_yaml_entries(cands: List[dict]) -> List[dict]:
    out = []
    for i, c in enumerate(cands):
        k, o = c["kalshi"], c["other"]
        e = {
            "name": f"cand-{i:03d}-{k['id'].lower()}",
            "needs_review": True,
            "score": c["score"],
            "kalshi": {"ticker": k["id"]},
            "kalshi_title": k["title"][:160],
            "other_title": o["title"][:160],
            "kalshi_close": k["close"],
            "other_close": o["close"],
        }
        if o["venue"] == "polymarket":
            toks = o["tokens"]
            e["polymarket"] = {"yes_token": toks[0], **({"no_token": toks[1]} if len(toks) > 1 else {})}
            e["polymarket_outcomes"] = o.get("outcomes")
        else:
            e["polymarket_us"] = {"slug": o["id"]}
        out.append(e)
    return out


def discover(topics: Iterable[str], sports_horizon_days: int = 10, min_score: float = 0.35) -> Dict[str, List[dict]]:
    import httpx

    res: Dict[str, List[dict]] = {}
    with httpx.Client(timeout=30, headers={"User-Agent": "predict-ngin-recorder/0.1 (read-only)"}) as client:
        for topic in topics:
            spec = TOPICS[topic]
            ks = kalshi_markets(client, spec["kalshi_series"])
            horizon = sports_horizon_days if topic == "sports" else None
            pm = gamma_markets(client, spec["gamma_tags"], horizon)
            us = pm_us_markets(client, spec["pm_us_category"])
            log.info("%s: kalshi=%d polymarket=%d polymarket_us=%d", topic, len(ks), len(pm), len(us))
            res[topic] = to_yaml_entries(match(ks, pm, min_score) + match(ks, us, min_score))
    return res
