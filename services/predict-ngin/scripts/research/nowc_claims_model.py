"""nowc: pre-release nowcast of the SA initial-claims headline from public DOL data.

Models (all use only data public before the Thursday release of week W):
  M0  random walk: SA_W = SA_{W-1}
  M1  seasonal-ratio: NSA_W = NSA_{W-1} * median_k(NSA_{W,y-k}/NSA_{W-1,y-k}), k=1..5 (skip 2020-21), SA = NSA_W / SF_W
  M2  4-week mean of SA
  MB  blend weights fitted on 2012-2019 + 2022-2024 (current-vintage history), frozen.
Error model: log(SA_adv/SA_hat) ~ t/normal with sigma from the same history.
Writes data/vendor/nowc/claims_model_hist.parquet and claims_model_params.json.
"""
from __future__ import annotations
import json, datetime as dt
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "data/vendor/nowc"


def build(nat: pd.DataFrame, rt: pd.DataFrame | None = None):
    """nat: week, nsa, sf, sa (current vintage). rt: real-time advance values to override the lag (week, sa_adv, nsa_adv)."""
    n = nat.dropna(subset=["sf"]).copy()
    n["week"] = pd.to_datetime(n.week)
    n = n.set_index("week").sort_index()
    lag_sa = n.sa.shift(1); lag_nsa = n.nsa.shift(1)
    if rt is not None:
        r = rt.copy(); r["week"] = pd.to_datetime(r.week); r = r.set_index("week")
        nxt = r.index + pd.Timedelta(days=7)
        lag_sa = lag_sa.copy(); lag_nsa = lag_nsa.copy()
        for w, wn in zip(r.index, nxt):
            if wn in n.index:
                lag_sa[wn] = r.loc[w, "sa_adv"]; lag_nsa[wn] = r.loc[w, "nsa_adv"]
    out = pd.DataFrame(index=n.index)
    out["sf"] = n.sf; out["sf_prev"] = n.sf.shift(1)
    out["lag_sa"] = lag_sa; out["lag_nsa"] = lag_nsa
    out["m0"] = lag_sa
    # seasonal ratio from prior years, same week-of-year aligned by nearest date 52 weeks back
    ratio = n.nsa / n.nsa.shift(1)
    rs = []
    for w in n.index:
        vals = []
        for k in range(1, 9):
            ww = w - pd.Timedelta(weeks=52 * k)
            # align to the week whose end date is closest (52w drift of 1 day/yr): pick among 52k, 52k+1 weeks
            cands = [ww, ww - pd.Timedelta(weeks=1)] if (w - ww).days % 7 == 0 else [ww]
            c = min(cands, key=lambda x: abs((w.replace(year=x.year) - x).days) if not (w.month == 2 and w.day == 29) else 0)
            if c.year in (2020, 2021) or c not in ratio.index or pd.isna(ratio.get(c)):
                continue
            vals.append(ratio[c])
            if len(vals) == 5:
                break
        rs.append(np.median(vals) if vals else np.nan)
    out["seas_ratio"] = rs
    out["m1"] = out.lag_nsa * out.seas_ratio / (out.sf / 100)
    out["m2"] = n.sa.shift(1).rolling(4).mean()
    if rt is not None:
        out["m2"] = lag_sa.rolling(4).mean()
    out["sa"] = n.sa
    return out


if __name__ == "__main__":
    nat = pd.read_parquet(D / "dol/national.parquet")
    h = build(nat)
    yrs = h.index.year
    tr = h[((yrs >= 2012) & (yrs <= 2019)) | ((yrs >= 2022) & (yrs <= 2024))].dropna()
    y = np.log(tr.sa)
    res = {}
    for m in ["m0", "m1", "m2"]:
        e = y - np.log(tr[m]); res[m] = dict(rmse=float(e.std()), bias=float(e.mean()), mad=float(np.median(np.abs(e))))
    # blend by least squares on logs (weights sum to 1)
    X = np.column_stack([np.log(tr.m0), np.log(tr.m1), np.log(tr.m2)])
    from itertools import product
    best = None
    for a, b in product(np.arange(0, 1.01, 0.05), repeat=2):
        if a + b > 1: continue
        p = a * X[:, 0] + b * X[:, 1] + (1 - a - b) * X[:, 2]
        e = y - p; s = e.std()
        if best is None or s < best[0]: best = (s, a, b, e.mean())
    s, a, b, bias = best
    eb = y - (a * X[:, 0] + b * X[:, 1] + (1 - a - b) * X[:, 2])
    res["blend"] = dict(w_m0=a, w_m1=b, w_m2=1 - a - b, rmse=float(s), bias=float(bias),
                        q=[float(np.quantile(eb - bias, q)) for q in (0.05, 0.25, 0.5, 0.75, 0.95)],
                        kurt=float(pd.Series(eb).kurt()))
    res["n_train_weeks"] = int(len(tr))
    print(json.dumps(res, indent=1))
    json.dump(res, open(D / "claims_model_params.json", "w"), indent=1)
    h.to_parquet(D / "claims_model_hist.parquet")
