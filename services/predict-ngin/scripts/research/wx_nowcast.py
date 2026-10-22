"""wx: empirical late-day nowcast of the CLI value from public METARs (fit on TRAIN only).

At each METAR report time (+5 min publication, + 60 s decision latency in the panel) for climate day D:
  R   = running max (high) / min (low) so far, from T-groups (0.1 C) AND 6-hour max/min remark groups, in F
  gap = R - current temperature (high) / current - R (low)
  features: LST hour bucket, gap bucket, fractional part of R (rounding position), whether the last
            synoptic 6h group has been seen since the running extreme was set (we use 'hours since R set')
  target: delta = CLI - round(R)   in {-2..+6} (clipped)
P(delta | cell) with additive smoothing toward the hour-bucket marginal. Bracket fair:
  p_now(lo,hi) = sum_delta P(delta) * 1[lo <= round(R)+delta <= hi]   (high);  same with min for lows.
Adds p_now to panel -> data/vendor/wx/panel_now.parquet (in-day prints only) and prints test Brier.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wx_common import OUT, STATIONS, TEST_START, TRAIN_END  # noqa: E402

DELTAS = np.arange(-3, 8)
HB = [0, 6, 10, 12, 14, 16, 18, 20, 22, 24.01]
GB = [-1e9, 0.5, 1.5, 3, 6, 1e9]
FB = [0, .25, .5, .75, 1.0001]
SB = [-1, 1, 3, 6, 1e9]  # hours since the extreme was set
ALPHA = 20.0


def feats(o, measure):
    off = o.code.map(lambda c: STATIONS[c][2])
    hour = ((o.ts + off * 3600) % 86400) / 3600
    if measure == "high":
        R = o.run_max; gap = o.run_max - o.tF; set_col = o.run_max
    else:
        R = o.run_min; gap = o.tF - o.run_min; set_col = o.run_min
    # time since the running extreme last changed
    chg = set_col.ne(o.groupby(["code", "date"])[set_col.name].shift())
    tset = o.ts.where(chg).groupby([o.code, o.date]).ffill()
    since = (o.ts - tset) / 3600
    f = pd.DataFrame(dict(hb=np.digitize(hour, HB), gb=np.digitize(gap.fillna(0), GB),
                          fb=np.digitize(R - np.floor(R), FB), sb=np.digitize(since.fillna(0), SB)), index=o.index)
    return R, f


def fit():
    o = pd.read_parquet(OUT / "obs.parquet").sort_values(["code", "date", "ts"]).reset_index(drop=True)
    cli = pd.read_parquet(OUT / "cli.parquet")
    o = o.merge(cli[["code", "date", "high", "low"]], on=["code", "date"], how="inner")
    tables = {}
    for meas in ("high", "low"):
        R, f = feats(o, meas)
        v = o[meas]
        d = (v - np.floor(R + 0.5)).clip(DELTAS[0], DELTAS[-1])
        x = f.assign(d=d, date=o.date).dropna()
        tr = x[x.date <= TRAIN_END]
        marg = tr.groupby("hb").d.value_counts().unstack(fill_value=0).reindex(columns=DELTAS, fill_value=0)
        margp = (marg + 0.5).div((marg + 0.5).sum(1), axis=0)
        cell = tr.groupby(["hb", "gb", "fb", "sb"]).d.value_counts().unstack(fill_value=0).reindex(columns=DELTAS, fill_value=0)
        prob = {}
        for k, row in cell.iterrows():
            prior = margp.loc[k[0]].to_numpy()
            prob[k] = ((row.to_numpy() + ALPHA * prior) / (row.sum() + ALPHA)).tolist()
        tables[meas] = dict(cells={"|".join(map(str, k)): v for k, v in prob.items()},
                            marg={str(k): v.tolist() for k, v in margp.iterrows()})
        # report: P(delta=0) by hour, train vs test
        rep = x.assign(split=np.where(x.date <= TRAIN_END, "train", "test")).groupby(["split", "hb"]).d.apply(
            lambda z: pd.Series({"P0": (z == 0).mean(), "P+1": (z == 1).mean(), "P<0": (z < 0).mean(), "n": len(z)})).unstack()
        print(meas, "\n", rep.round(3).to_string())
    with open(OUT / "nowcast.json", "w") as fh:
        json.dump(dict(tables=tables, deltas=DELTAS.tolist()), fh)
    return tables


def pmf(tables, meas, f):
    T = tables[meas]
    out = np.empty((len(f), len(DELTAS)))
    for i, (hb, gb, fb, sb) in enumerate(f[["hb", "gb", "fb", "sb"]].itertuples(index=False, name=None)):
        k = f"{hb}|{gb}|{fb}|{sb}"
        out[i] = T["cells"].get(k) or T["marg"].get(str(hb)) or np.full(len(DELTAS), 1 / len(DELTAS))
    return out


def apply_panel(tables):
    p = pd.read_parquet(OUT / "panel.parquet")
    p = p[p.in_day & p.run_max.notna()].copy()
    o = pd.read_parquet(OUT / "obs.parquet").sort_values(["code", "date", "ts"]).reset_index(drop=True)
    res = []
    for meas in ("high", "low"):
        R, f = feats(o, meas)
        oo = pd.concat([o[["code", "date", "ts_avail"]], f, R.rename("R")], axis=1)
        oo["key2"] = oo.code + "|" + oo.date
        q = p[p.measure == meas].copy()
        q["key2"] = q.code + "|" + q.date
        q["ts_obs"] = q.ts - 60
        q = pd.merge_asof(q.sort_values("ts_obs"), oo.sort_values("ts_avail")[["key2", "ts_avail", "hb", "gb", "fb", "sb", "R"]],
                          left_on="ts_obs", right_on="ts_avail", by="key2", direction="backward")
        q = q[q.R.notna()]
        # unique feature cells -> pmf once
        cells = q[["hb", "gb", "fb", "sb"]].drop_duplicates().reset_index(drop=True)
        P = pmf(tables, meas, cells)
        cells["pi"] = np.arange(len(cells))
        q = q.merge(cells, on=["hb", "gb", "fb", "sb"], how="left")
        base = np.floor(q.R.to_numpy() + 0.5)
        vals = base[:, None] + DELTAS[None, :]
        inb = (vals >= q.lo.to_numpy()[:, None]) & (vals <= q.hi.to_numpy()[:, None])
        q["p_now"] = (P[q.pi.to_numpy()] * inb).sum(1)
        res.append(q.drop(columns=["key2", "ts_obs", "ts_avail", "pi"]))
    q = pd.concat(res, ignore_index=True).sort_values(["ticker", "ts"], kind="mergesort").reset_index(drop=True)
    q.to_parquet(OUT / "panel_now.parquet", index=False)
    te = q[q.date >= TEST_START]
    off = te.code.map(lambda c: STATIONS[c][2])
    d0 = (pd.to_datetime(te.date) - pd.Timestamp("1970-01-01")) // pd.Timedelta("1s") - off * 3600
    rows = []
    for hrs in (12, 15, 18, 21):
        T = d0 + hrs * 3600
        w = te[(te.ts <= T) & (te.ts > T - 7200)].sort_values("ts").groupby("ticker").tail(1)
        for meas, g in w.groupby("measure"):
            rows.append(dict(T=f"D {hrs}h", measure=meas, n=len(g), brier_mkt=((g.y - g.win) ** 2).mean(),
                             brier_now=((g.p_now - g.win) ** 2).mean(), brier_obs=((g.p_obs - g.win) ** 2).mean(),
                             brier_blend=(((g.y + g.p_now) / 2 - g.win) ** 2).mean()))
    print(pd.DataFrame(rows).round(4).to_string(index=False))


if __name__ == "__main__":
    apply_panel(fit())
