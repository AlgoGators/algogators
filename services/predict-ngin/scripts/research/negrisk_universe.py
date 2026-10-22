"""Snapshot the universe of open Polymarket NegRisk events (gamma keyset API, public, no key).

Writes data/vendor/negrisk/universe_<UTC>.parquet (one row per market of every open negRisk event) and
data/vendor/negrisk/universe_latest.parquet. Also counts open binary (non-negRisk) markets.

    venv/Scripts/python.exe scripts/research/negrisk_universe.py
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

GAMMA = "https://gamma-api.polymarket.com"
OUT = Path("data/vendor/negrisk")


def _j(v, default=None):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return default
    return v if v is not None else default


def fetch_universe(max_pages: int = 5000, sleep: float = 0.05):
    s = requests.Session()
    cur = None
    rows = []
    binary = {"markets": 0, "accepting": 0}
    n_events = n_nr = 0
    for _ in range(max_pages):
        p = {"closed": "false", "limit": 100}
        if cur:
            p["after_cursor"] = cur
        for attempt in range(5):
            try:
                r = s.get(f"{GAMMA}/events/keyset", params=p, timeout=30)
                if r.status_code == 429:
                    time.sleep(2 + attempt * 2)
                    continue
                r.raise_for_status()
                d = r.json()
                break
            except Exception:  # noqa: BLE001
                time.sleep(2 + attempt * 2)
        else:
            raise RuntimeError("gamma keyset failed")
        evs = d.get("events") or []
        for e in evs:
            n_events += 1
            mk = [m for m in (e.get("markets") or []) if isinstance(m, dict)]
            if not e.get("negRisk"):
                for m in mk:
                    binary["markets"] += 1
                    binary["accepting"] += bool(m.get("acceptingOrders"))
                continue
            n_nr += 1
            tags = [t.get("slug") for t in (e.get("tags") or []) if isinstance(t, dict)]
            for m in mk:
                toks = _j(m.get("clobTokenIds"), []) or []
                fs = m.get("feeSchedule") or {}
                rows.append(dict(
                    event_id=str(e.get("id")), event_slug=e.get("slug"), event_title=e.get("title"),
                    neg_risk_augmented=bool(e.get("negRiskAugmented")),
                    event_end=e.get("endDate"), event_volume24h=float(e.get("volume24hr") or 0),
                    event_liquidity=float(e.get("liquidity") or 0), tags=",".join(t for t in tags if t),
                    market_id=str(m.get("id")), condition_id=m.get("conditionId"), question=m.get("question"),
                    group_item=m.get("groupItemTitle"), yes_token=toks[0] if len(toks) > 0 else None,
                    no_token=toks[1] if len(toks) > 1 else None, active=bool(m.get("active")),
                    closed=bool(m.get("closed")), accepting=bool(m.get("acceptingOrders")),
                    enable_ob=bool(m.get("enableOrderBook")), neg_risk_other=bool(m.get("negRiskOther")),
                    outcome_prices=m.get("outcomePrices"), fees_enabled=bool(m.get("feesEnabled")),
                    fee_type=m.get("feeType"), fee_rate=float(fs.get("rate") or 0) if m.get("feesEnabled") else 0.0,
                    fee_exp=float(fs.get("exponent") or 1), tick=float(m.get("orderPriceMinTickSize") or 0.01),
                    min_size=float(m.get("orderMinSize") or 5), end_date=m.get("endDate"),
                    version=m.get("version"), m_negrisk=bool(m.get("negRisk")),
                    volume24h=float(m.get("volume24hr") or 0),
                    nr_id=m.get("negRiskMarketID") or f"ev:{e.get('id')}",
                ))
        cur = d.get("next_cursor")
        if not cur or not evs:
            break
        time.sleep(sleep)
    df = pd.DataFrame(rows)
    return df, dict(n_events=n_events, n_negrisk_events=n_nr, **{f"binary_{k}": v for k, v in binary.items()})


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    df, stats = fetch_universe()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    df.to_parquet(OUT / f"universe_{ts}.parquet", index=False)
    df.to_parquet(OUT / "universe_latest.parquet", index=False)
    stats.update(rows=len(df), secs=round(time.time() - t0, 1), ts=ts)
    (OUT / f"universe_{ts}.json").write_text(json.dumps(stats))
    print(json.dumps(stats))


if __name__ == "__main__":
    main()
