"""H4a: do Quiver-flagged Polymarket large trades beat their own price?

Outcomes come from the free Polymarket gamma API (resolved markets only), cached at
data/vendor/quiver/pm_whale_outcomes.parquet.  Return of taking the whale's side
at the whale's price, per $ staked:
  BUY  outcome o at p : (v_o - p) / p
  SELL outcome o at p : (p - v_o) / (1 - p)      (long the complement at 1-p)
Event-cluster (event_slug) means and t-stats; train < 2026-04-01 <= test.
"""
from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd
import requests

from quiver_probe import OUT

GAMMA = "https://gamma-api.polymarket.com/markets"
SPLIT = pd.Timestamp("2026-04-01", tz="UTC")


def fetch_outcomes(cids: list[str]) -> pd.DataFrame:
    fn = OUT / "pm_whale_outcomes.parquet"
    have = pd.read_parquet(fn) if fn.exists() else pd.DataFrame(columns=["condition_id"])
    # gamma hides closed markets unless closed=true; unresolved markets are simply absent.
    # (re-runs re-query ids that were still open last time)
    todo = sorted(set(cids) - set(have.condition_id))
    rows = []
    s = requests.Session()
    for i in range(0, len(todo), 40):
        chunk = todo[i:i + 40]
        for attempt in range(4):
            r = s.get(GAMMA, params=[("condition_ids", c) for c in chunk] + [("limit", 100), ("closed", "true")], timeout=60)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1)); continue
            break
        r.raise_for_status()
        for m in r.json():
            rows.append({"condition_id": m.get("conditionId"), "closed": m.get("closed"),
                         "uma": m.get("umaResolutionStatus"), "outcomes": m.get("outcomes"),
                         "outcome_prices": m.get("outcomePrices"), "end_date": m.get("endDate"),
                         "closed_time": m.get("closedTime"), "category": m.get("category")})
        time.sleep(0.5)
        if i % 2000 == 0:
            print("gamma", i, len(todo), flush=True)
    new = pd.DataFrame(rows)
    out = pd.concat([have, new], ignore_index=True).drop_duplicates("condition_id")
    out.to_parquet(fn, index=False)
    return out


def cluster_stats(df: pd.DataFrame, col: str = "ret") -> dict:
    g = df.groupby("event_slug").agg(pnl=("pnl", "sum"), cost=("cost", "sum"), r=(col, "mean"))
    n = len(g)
    m = g.r.mean()
    t = m / (g.r.std(ddof=1) / np.sqrt(n)) if n > 2 else np.nan
    # $-weighted per-$ return with cluster bootstrap
    rng = np.random.default_rng(0)
    pr = g.pnl.to_numpy(); cr = g.cost.to_numpy()
    boots = []
    for _ in range(2000):
        idx = rng.integers(0, n, n)
        boots.append(pr[idx].sum() / cr[idx].sum())
    return {"trades": len(df), "events": n, "ev_mean_ret": round(m, 4), "t": round(t, 2),
            "usd_w_ret": round(pr.sum() / cr.sum(), 4),
            "usd_ci": f"[{np.percentile(boots, 2.5):.3f}, {np.percentile(boots, 97.5):.3f}]"}


def main():
    w = pd.read_parquet(OUT / "pm_whales.parquet")
    w["t"] = pd.to_datetime(w.trade_time, utc=True, format="mixed")
    w["up"] = pd.to_datetime(w.uploaded, utc=True, format="mixed")
    print("whale trades", len(w), w.t.min(), w.t.max(),
          "upload lag min median/p90:", (w.up - w.t).dt.total_seconds().div(60).quantile([.5, .9]).round(1).tolist())
    oc = fetch_outcomes(w.condition_id.dropna().unique().tolist())
    oc = oc[oc.closed.astype(bool)].copy()
    def final_price(row, idx):
        try:
            return float(json.loads(row.outcome_prices)[idx])
        except Exception:
            return np.nan
    d = w.merge(oc, on="condition_id", how="inner")
    d["v"] = [final_price(r, int(r.outcome_index)) for r in d.itertuples()]
    d = d[d.v.isin([0.0, 1.0]) & d.price.between(0.01, 0.99)].copy()
    buy = d.side.eq("BUY")
    d["p_side"] = np.where(buy, d.price, 1 - d.price)
    d["win"] = np.where(buy, d.v, 1 - d.v)
    d["ret"] = (d.win - d.p_side) / d.p_side
    d["cost"] = d["size"] * d.p_side
    d["pnl"] = d["size"] * (d.win - d.p_side)
    d["split"] = np.where(d.t < SPLIT, "train", "test")
    # 20 random fills for hand-check of side normalisation
    d.sample(20, random_state=1)[["title", "side", "outcome", "price", "v", "p_side", "win", "ret"]].to_csv(
        OUT / "h4_handcheck20.csv", index=False)
    res = []
    for sp in ["train", "test"]:
        x = d[d.split == sp]
        res.append({"split": sp, "bucket": "all", **cluster_stats(x)})
        for lo, hi in [(0, .2), (.2, .5), (.5, .8), (.8, .95), (.95, 1)]:
            y = x[(x.p_side >= lo) & (x.p_side < hi)]
            if len(y) > 20:
                res.append({"split": sp, "bucket": f"{lo}-{hi}", **cluster_stats(y)})
        for side in ["BUY", "SELL"]:
            res.append({"split": sp, "bucket": side, **cluster_stats(x[x.side == side])})
        big = x[x.estimated_amount >= 50_000]
        if len(big) > 20:
            res.append({"split": sp, "bucket": ">=50k", **cluster_stats(big)})
    r = pd.DataFrame(res)
    r.to_csv(OUT / "h4_results.csv", index=False)
    print("resolved whale trades", len(d), "of", len(w), "; markets", d.condition_id.nunique())
    print(r.to_string())


if __name__ == "__main__":
    main()
