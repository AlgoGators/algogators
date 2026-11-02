"""Per-round summary of the live LIP snapshots: pools, qualification, and resting liquidity inside the scoring band.

"In band" = the qualifying set of the official rule (levels walked from the best bid until cumulative size >= Target),
with its discounted score (size x DF^ticks below the reference price). Output:
data/vendor/klip/analysis/snapshot_summary.csv (one row per round x category) and snapshot_markets.parquet.
"""
from __future__ import annotations

import glob
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import klip_lip as L  # noqa: E402

SNAP = ROOT / "data/vendor/klip/snapshots"
AN = ROOT / "data/vendor/klip/analysis"


def band(px, sz, tgt, df):
    ok, _, tot = L.side_score(px, sz, tgt, df)
    if not ok:
        return False, 0.0, 0.0
    o = np.argsort(-px); cum = 0.0; usd = 0.0
    for i in o:
        if cum >= tgt:
            break
        cum += sz[i]; usd += sz[i] * px[i]
    return True, tot, usd


def main():
    ser = pd.read_parquet(ROOT / "data/vendor/kalshi_research/series.parquet").set_index("series_ticker")
    rows = []
    for bf in sorted(glob.glob(str(SNAP / "books_*.parquet"))):
        stamp = Path(bf).stem.split("_", 1)[1]
        pr = pd.read_parquet(SNAP / f"programs_{stamp}.parquet")
        bk = pd.read_parquet(bf)
        for c in ("start_date", "end_date"):
            pr[c] = pd.to_datetime(pr[c], format="ISO8601", utc=True)
        pr["pool_day"] = pr.period_reward / 1e4 / ((pr.end_date - pr.start_date).dt.total_seconds() / 86400).clip(lower=1 / 96)
        books = {t: g for t, g in bk[bk.side != "empty"].groupby("ticker")}
        for r in pr.itertuples():
            g = books.get(r.market_ticker)
            tgt = float(r.target_size_fp); df = r.discount_factor_bps / 1e4
            out = dict(snap=stamp, ticker=r.market_ticker, series=r.market_ticker.split("-")[0], pool_day=r.pool_day,
                       tgt=tgt, df=df, desc=r.incentive_description)
            for side in ("yes", "no"):
                if g is None:
                    ok, sc, usd = False, 0.0, 0.0
                else:
                    x = g[g.side == side]
                    ok, sc, usd = band(x.price.to_numpy(), x["size"].to_numpy(), tgt, df)
                out[f"{side}_ok"] = ok; out[f"{side}_score"] = sc; out[f"{side}_band_usd"] = usd
            out["qualifies"] = out["yes_ok"] and out["no_ok"]
            rows.append(out)
    m = pd.DataFrame(rows)
    m["category"] = m.series.map(ser.category).fillna("unknown")
    m.to_parquet(AN / "snapshot_markets.parquet", index=False)
    s = m.groupby(["snap", "category"]).agg(programs=("ticker", "size"), pool_day=("pool_day", "sum"),
                                            qual_frac=("qualifies", "mean"),
                                            pool_day_qualifying=("pool_day", lambda x: x[m.loc[x.index, "qualifies"]].sum()),
                                            med_yes_score=("yes_score", "median"), med_no_score=("no_score", "median"),
                                            band_usd=("yes_band_usd", "sum")).reset_index()
    tot = m.groupby("snap").agg(programs=("ticker", "size"), pool_day=("pool_day", "sum"), qual_frac=("qualifies", "mean"),
                                med_yes_score=("yes_score", "median"), med_no_score=("no_score", "median")).reset_index()
    tot["category"] = "ALL"
    s = pd.concat([s, tot])
    s.to_csv(AN / "snapshot_summary.csv", index=False)
    pd.set_option("display.width", 220)
    print(s[s.category == "ALL"].round(2).to_string())
    print(s[s.snap == s.snap.max()].sort_values("pool_day", ascending=False).round(2).to_string())


if __name__ == "__main__":
    main()
