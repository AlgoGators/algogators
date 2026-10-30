"""mgdelt step 3b: point-in-time features from the GKG day files (Amendment 2).

usage: python mgdelt_gkg_features.py <split>
Output: data/vendor/mgdelt/features_<split>.parquet (same columns the gate expects: z, B, HHI, L)
"""
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from mgdelt_features import nb_fit  # noqa: E402

G = "data/vendor/mgdelt/gkg/"
DAY = 86400


def main():
    split = sys.argv[1]
    p = pd.read_parquet("data/vendor/mgdelt/panel.parquet")
    p = p[(p.split == split) & p.term_q.notna()].copy()
    tot = {}
    for f in glob.glob(G + "*.json"):
        with open(f) as fh:
            m = json.load(fh)
        if m["nfiles"] >= 20:  # a day with < 20 of 24 sampled files is treated as missing
            tot[int(pd.Timestamp(m["day"], tz="UTC").timestamp())] = m["total"]
    need = set(zip(p.speaker, p.term_q))
    att_days = {int(D) - k * DAY for D in p.D.unique() for k in (1, 2, 3)}
    cnt, doms = {}, {}
    for f in sorted(glob.glob(G + "*.parquet")):
        h = pd.read_parquet(f, columns=["day", "speaker", "term_q", "domain"])
        if h.empty:
            continue
        t = int(pd.Timestamp(h.day.iloc[0], tz="UTC").timestamp())
        h = h[[k in need for k in zip(h.speaker, h.term_q)]]
        for k, v in h.groupby(["speaker", "term_q"]).size().items():
            cnt[(k[0], k[1], t)] = v
        if t in att_days:
            for k, v in h.groupby(["speaker", "term_q"]).domain.apply(list).items():
                doms[(k[0], k[1], t)] = v
    rows = []
    for r in p.itertuples():
        D = int(r.D)
        base = np.arange(D - 59 * DAY, D - 3 * DAY, DAY)
        att = np.arange(D - 3 * DAY, D, DAY)
        f = dict(ticker=r.ticker, complete=all(d in tot for d in np.r_[base, att]))
        if f["complete"]:
            y = np.array([cnt.get((r.speaker, r.term_q, d), 0) for d in base], float)
            n = np.array([tot[d] for d in base], float)
            ya = np.array([cnt.get((r.speaker, r.term_q, d), 0) for d in att], float)
            na = np.array([tot[d] for d in att], float)
            dow = lambda days: pd.to_datetime(days, unit="s", utc=True).dayofweek.values  # noqa: E731
            beta, alpha = nb_fit(y, np.log(n), dow(base))
            Xa = np.zeros((3, 7))
            Xa[:, 0] = 1
            da = dow(att)
            for k in range(1, 7):
                Xa[:, k] = da == k
            mu = np.exp(Xa @ beta + np.log(na))
            Y, M, V = ya.sum(), mu.sum(), (mu + alpha * mu ** 2).sum()
            dl = sum((doms.get((r.speaker, r.term_q, d), []) for d in att), [])
            f.update(Y3=Y, M3=M, z=(Y - M) / np.sqrt(V), alpha=alpha, base_cnt=y.sum(),
                     L=np.log((Y + 0.5) / na.sum()), n_dom=len(set(dl)), B=np.log1p(len(set(dl))))
            if len(dl) >= 5:
                s = pd.Series(dl).value_counts(normalize=True)
                f["HHI"] = float((s ** 2).sum())
        rows.append(f)
    F = p.merge(pd.DataFrame(rows), on="ticker")
    F = F[F.complete]
    F.to_parquet(f"data/vendor/mgdelt/features_{split}.parquet", index=False)
    print(f"days with data: {len(tot)}; markets with complete windows: {len(F)} / {len(p)}, "
          f"events {F.event_ticker.nunique()}")
    print(F[["z", "B", "HHI", "L", "alpha", "Y3", "base_cnt"]].describe().T.round(3).to_string())
    print("share with zero baseline co-mentions:", (F.base_cnt == 0).mean().round(3))


if __name__ == "__main__":
    main()
