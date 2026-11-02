"""NegRisk sum-of-asks / sum-of-bids history Jan-Oct 2026 from Predexon book snapshots (RESEARCH ONLY).

Predexon GET /v2/polymarket/orderbooks (free, shared key -> we use <= 0.3 req/s, back off on 429) returns
full-depth snapshots, but they are SPARSE and irregular (median gap minutes-to-hours per token), so this
measures *standing* mispricings (the kind a slow small account could take), not sub-second races.

Sample: per month M (2026-01..2026-10), negRisk events that END in M, 2-12 tradable outcomes, top 6 by
volume + 6 random others with volume >= $20k (seed 7). Days sampled: end-7d and end-2d (UTC days).
For each (event, day): all YES tokens' snapshots; at every snapshot time, as-of combine the last snapshot
of every leg (max leg age recorded) and compute buy-all-YES and sell/convert edges (gross and net of the
market's fee schedule) with depth-walked size.

Outputs data/vendor/negrisk/predexon/{raw/<token>_<day>.json.gz, points.parquet, sample.parquet}.

    venv/Scripts/python.exe scripts/research/negrisk_predexon_history.py --months 2026-01..2026-10
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
import sys
import time
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from negrisk_common import buy_all_yes, fee_per_share, parse_levels, sell_all_yes  # noqa: E402

GAMMA = "https://gamma-api.polymarket.com"
API = "https://api.predexon.com/v2/polymarket/orderbooks"
OUT = Path("data/vendor/negrisk/predexon")
MIN_GAP = 1 / 0.3  # seconds between Predexon requests


def key():
    for l in open(".env"):
        if l.startswith("PREDEXON_API_KEY"):
            return l.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("PREDEXON_API_KEY missing")


class Pdx:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers["x-api-key"] = key()
        self.last = 0.0
        self.n = 0

    def get(self, params):
        for attempt in range(8):
            wait = MIN_GAP - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            self.n += 1
            try:
                r = self.s.get(API, params=params, timeout=60)
            except requests.RequestException:
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code == 429:
                time.sleep(10 * (attempt + 1))
                continue
            if r.status_code >= 500:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError("predexon failed")

    def day(self, token, t0_ms, t1_ms):
        snaps, pk = [], None
        while True:
            p = dict(token_id=token, start_time=t0_ms, end_time=t1_ms, limit=2000)
            if pk:
                p["pagination_key"] = pk
            d = self.get(p)
            snaps += d.get("snapshots") or []
            pg = d.get("pagination") or {}
            pk = pg.get("pagination_key")
            if not pg.get("has_more") or not pk:
                return snaps


def sample_events(month: str, rng: random.Random):
    y, m = map(int, month.split("-"))
    start = pd.Timestamp(year=y, month=m, day=1, tz="UTC")
    end = start + pd.offsets.MonthBegin(1)
    evs, cur = [], None
    for _ in range(3):  # top 300 by volume
        p = dict(closed="true", end_date_min=start.isoformat(), end_date_max=end.isoformat(), limit=100,
                 order="volume", ascending="false")
        if cur:
            p["after_cursor"] = cur
        d = requests.get(f"{GAMMA}/events/keyset", params=p, timeout=60).json()
        evs += d.get("events") or []
        cur = d.get("next_cursor")
        if not cur:
            break
    ok = []
    for e in evs:
        if not e.get("negRisk"):
            continue
        mk = [x for x in e.get("markets") or [] if isinstance(x, dict) and x.get("clobTokenIds")]
        if not (2 <= len(mk) <= 12) or float(e.get("volume") or 0) < 20000:
            continue
        ok.append(e)
    top, rest = ok[:6], ok[6:]
    pick = top + rng.sample(rest, min(6, len(rest)))
    return pick


def analyse(legs, rates):
    """legs: list of snapshot lists (sorted by ts). Returns list of point dicts at every snapshot time."""
    times = sorted({s["timestamp"] for l in legs for s in l})
    idx = [0] * len(legs)
    cur = [None] * len(legs)
    pts = []
    for t in times:
        for i, l in enumerate(legs):
            while idx[i] < len(l) and l[idx[i]]["timestamp"] <= t:
                cur[i] = l[idx[i]]
                idx[i] += 1
        if any(c is None for c in cur):
            continue
        age = max(t - c["timestamp"] for c in cur) / 1000.0
        asks = [parse_levels(c.get("asks")) for c in cur]
        bids = [parse_levels(c.get("bids")) for c in cur]
        row = dict(t=t, max_age_s=age)
        if all(asks):
            ba = [min(a)[0] for a in asks]
            row["sum_ask"] = sum(ba)
            row["buy_gross"] = 1 - sum(ba)
            f = buy_all_yes(asks, rates, min_shares=5)
            row.update(buy_net=f.top_edge, buy_profit=f.profit, buy_shares=f.shares,
                       **{f"buy_profit_{c}": f.at_capital(c)[1] for c in (1000, 10000)})
        legs_b = sorted(((max(b)[0] - fee_per_share(max(b)[0], rates[i]), max(b)[0], i)
                         for i, b in enumerate(bids) if b), reverse=True)
        if len(legs_b) >= 2:
            row["sum_bid"] = sum(x[1] for x in legs_b)
            row["sell_gross"] = row["sum_bid"] - 1
            S = [i for v, _, i in legs_b if v > 0] or [i for _, _, i in legs_b[:2]]
            if len(S) < 2:
                S = [i for _, _, i in legs_b[:2]]
            f = sell_all_yes([bids[i] for i in S], [rates[i] for i in S], min_shares=5)
            row.update(sell_net=f.top_edge, sell_profit=f.profit, sell_shares=f.shares, sell_k=len(S),
                       **{f"sell_profit_{c}": f.at_capital(c)[1] for c in (1000, 10000)})
        pts.append(row)
    return pts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", default="2026-01..2026-10")
    ap.add_argument("--max-requests", type=int, default=1500)
    ap.add_argument("--tag", default="", help="suffix for points/sample files (to add months to a run)")
    a = ap.parse_args()
    lo, hi = a.months.split("..")
    months = [str(p) for p in pd.period_range(lo, hi, freq="M")]
    (OUT / "raw").mkdir(parents=True, exist_ok=True)
    rng = random.Random(7)
    px = Pdx()
    all_pts, sample = [], []
    for month in months:
        for _ in range(3):
            evs = sample_events(month, rng)
            if evs:
                break
            time.sleep(10)
        print(month, "events", len(evs), flush=True)
        for e in evs:
            mk = [x for x in e["markets"] if isinstance(x, dict) and x.get("clobTokenIds")]
            toks = [json.loads(x["clobTokenIds"])[0] for x in mk]
            rates = [float((x.get("feeSchedule") or {}).get("rate") or 0) if x.get("feesEnabled") else 0.0
                     for x in mk]
            end = pd.Timestamp(e["endDate"])
            if end.tzinfo is None:
                end = end.tz_localize("UTC")
            for lag in (7, 2):
                d0 = (end - pd.Timedelta(days=lag)).normalize()
                t0, t1 = int(d0.timestamp() * 1000), int((d0 + pd.Timedelta(days=1)).timestamp() * 1000)
                if px.n >= a.max_requests:
                    break
                legs = []
                for tok in toks:
                    fp = OUT / "raw" / f"{tok[:20]}_{d0:%Y%m%d}.json.gz"
                    if fp.exists():
                        snaps = json.load(gzip.open(fp, "rt"))
                    else:
                        snaps = px.day(tok, t0, t1)
                        with gzip.open(fp, "wt") as f:
                            json.dump(snaps, f)
                    legs.append(sorted(snaps, key=lambda s: s["timestamp"]))
                n_snaps = [len(l) for l in legs]
                pts = analyse(legs, rates)
                for p in pts:
                    p.update(month=month, event_id=str(e["id"]), slug=e.get("slug"), day=f"{d0:%Y-%m-%d}",
                             n_legs=len(toks), fee_max=max(rates))
                all_pts += pts
                sample.append(dict(month=month, event_id=str(e["id"]), slug=e.get("slug"), day=f"{d0:%Y-%m-%d}",
                                   n_legs=len(toks), volume=float(e.get("volume") or 0), fee_max=max(rates),
                                   snaps_min=min(n_snaps), snaps_total=sum(n_snaps), n_points=len(pts)))
                print(f"  {e.get('slug')[:50]:50s} {d0:%m-%d} legs {len(toks)} snaps {sum(n_snaps)} "
                      f"pts {len(pts)} req {px.n}", flush=True)
        pd.DataFrame(all_pts).to_parquet(OUT / f"points{a.tag}.parquet", index=False)
        pd.DataFrame(sample).to_parquet(OUT / f"sample{a.tag}.parquet", index=False)
    print("done requests", px.n)


if __name__ == "__main__":
    main()
