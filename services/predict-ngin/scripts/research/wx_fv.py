"""wx: point-in-time NBM forecast -> calibrated bracket fair values.

Forecast: NBM station text (NBS) via IEM. For climate date D (local standard day):
  high: TXN valid at ftime D+1 00Z (daytime max);  low: TXN valid at ftime D 12Z (overnight min).
A run is usable from runtime + AVAIL_LAG (90 min, conservative vs ~45-60 min NBM text dissemination).

Error model (fit on TRAIN climate dates only, <= wx_common.TRAIN_END):
  V = TXN + b[code,measure] + s * e,  e ~ Student-t(nu),  s = exp(a_h + c_h * log(max(xnd,1)))
  h = horizon bucket = hours from decision time to the END of the forecast period.
Bracket fair P(lo<=V<=hi) = F((hi+0.5-m)/s) - F((lo-0.5-m)/s) (CLI values are integers).

Outputs data/vendor/wx/fc_runs.parquet (code, measure, date, avail_ts, txn, xnd, period_end_ts)
        data/vendor/wx/fv_params.json
"""
from __future__ import annotations
import glob
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import optimize, stats

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wx_common import OUT, STATIONS, TRAIN_END  # noqa: E402

AVAIL_LAG = 90 * 60
HBINS = [0, 12, 24, 36, 48, 72, 1e9]


def runs():
    rows = []
    for c in STATIONS:
        fs = sorted(glob.glob(str(OUT / "iem" / "mos" / f"{c}_*.parquet")))
        if not fs:
            continue
        d = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True).dropna(subset=["txn"])
        d["runtime"] = pd.to_datetime(d.runtime); d["ftime"] = pd.to_datetime(d.ftime)
        d = d.drop_duplicates(["runtime", "ftime"])
        off = STATIONS[c][2]
        hi = d[d.ftime.dt.hour == 0].copy()
        hi["measure"] = "high"; hi["date"] = (hi.ftime - pd.Timedelta(days=1)).dt.date.astype(str)
        lo = d[d.ftime.dt.hour == 12].copy()
        lo["measure"] = "low"; lo["date"] = lo.ftime.dt.date.astype(str)
        x = pd.concat([hi, lo])
        x["code"] = c
        x["avail_ts"] = (x.runtime - pd.Timestamp("1970-01-01")) // pd.Timedelta("1s") + AVAIL_LAG
        x["period_end_ts"] = (x.ftime - pd.Timestamp("1970-01-01")) // pd.Timedelta("1s")
        # end of the CLI climate day (local standard midnight after D)
        x["day_end_ts"] = ((pd.to_datetime(x.date) + pd.Timedelta(days=1) - pd.Timedelta(hours=off))
                           - pd.Timestamp("1970-01-01")) // pd.Timedelta("1s")
        x["xnd"] = x.xnd.fillna(2.0)
        rows.append(x[["code", "measure", "date", "avail_ts", "txn", "xnd", "period_end_ts", "day_end_ts"]])
    r = pd.concat(rows, ignore_index=True).sort_values(["code", "measure", "date", "avail_ts"])
    r.to_parquet(OUT / "fc_runs.parquet", index=False)
    return r


def hbin(h):
    return np.digitize(h, HBINS) - 1


def nll(theta, txn, xnd, v, hb, ci, nu, nb, ncity):
    a = theta[:nb]; cc = theta[nb:2 * nb]; b = theta[2 * nb:2 * nb + ncity]
    m = txn + b[ci]
    s = np.exp(a[hb] + cc[hb] * np.log(np.maximum(xnd, 1.0)))
    p = stats.t.cdf((v + 0.5 - m) / s, nu) - stats.t.cdf((v - 0.5 - m) / s, nu)
    return -np.log(np.maximum(p, 1e-9)).sum()


def training_set(r, cli, dec_offsets_h=(6, 18, 30, 42, 60)):
    """For each (code, measure, date) and decision time = period_end - k hours, take the latest run available."""
    out = []
    for (c, ms, d), g in r.groupby(["code", "measure", "date"], sort=False):
        pe = g.period_end_ts.iloc[-1]
        for k in dec_offsets_h:
            t = pe - k * 3600
            gg = g[g.avail_ts <= t]
            if gg.empty:
                continue
            last = gg.iloc[-1]
            out.append((c, ms, d, k, last.txn, last.xnd))
    x = pd.DataFrame(out, columns=["code", "measure", "date", "h", "txn", "xnd"])
    cl = cli.melt(id_vars=["code", "date"], value_vars=["high", "low"], var_name="measure", value_name="v").dropna()
    return x.merge(cl, on=["code", "measure", "date"])


def fit(x, nu):
    keys = sorted(set(zip(x.code, x.measure)))
    kid = {k: i for i, k in enumerate(keys)}
    ci = np.array([kid[k] for k in zip(x.code, x.measure)])
    hb = hbin(x.h.to_numpy()); nb = len(HBINS) - 1
    th0 = np.r_[np.full(nb, np.log(2.5)), np.zeros(nb), np.zeros(len(keys))]
    res = optimize.minimize(nll, th0, args=(x.txn.to_numpy(float), x.xnd.to_numpy(float), x.v.to_numpy(float), hb, ci,
                                            nu, nb, len(keys)), method="L-BFGS-B")
    return res, keys


class FairModel:
    def __init__(self, params):
        self.p = params
        self.nb = len(HBINS) - 1
        self.kid = {tuple(k): i for i, k in enumerate(params["keys"])}

    def loc_scale(self, code, measure, txn, xnd, h):
        th = np.asarray(self.p["theta"]); nb = self.nb
        a = th[:nb]; cc = th[nb:2 * nb]; b = th[2 * nb:]
        i = self.kid.get((code, measure))
        bias = b[i] if i is not None else float(np.median(b))
        hb = np.clip(hbin(np.asarray(h, float)), 0, nb - 1)
        s = np.exp(a[hb] + cc[hb] * np.log(np.maximum(np.asarray(xnd, float), 1.0)))
        return np.asarray(txn, float) + bias, s

    def prob(self, m, s, lo, hi):
        nu = self.p["nu"]
        up = np.where(np.isinf(hi), 1.0, stats.t.cdf((hi + 0.5 - m) / s, nu))
        dn = np.where(np.isinf(lo), 0.0, stats.t.cdf((lo - 0.5 - m) / s, nu))
        return np.clip(up - dn, 0.0, 1.0)


def load_model():
    with open(OUT / "fv_params.json") as fh:
        return FairModel(json.load(fh))


def main():
    r = runs()
    cli = pd.read_parquet(OUT / "cli.parquet")
    x = training_set(r, cli)
    x.to_parquet(OUT / "fc_eval.parquet", index=False)
    tr = x[x.date <= TRAIN_END]
    print("train rows", len(tr), "codes", tr.code.nunique(), "dates", tr.date.min(), tr.date.max())
    best = None
    for nu in (3, 5, 10, 30):
        res, keys = fit(tr, nu)
        print("nu", nu, "nll", round(res.fun, 1))
        if best is None or res.fun < best[0].fun:
            best = (res, keys, nu)
    res, keys, nu = best
    params = dict(theta=res.x.tolist(), keys=[list(k) for k in keys], nu=nu, hbins=HBINS, avail_lag=AVAIL_LAG,
                  train_end=TRAIN_END)
    with open(OUT / "fv_params.json", "w") as fh:
        json.dump(params, fh, indent=1)
    fm = FairModel(params)
    # calibration on train vs test (PIT of integer outcome, CRPS-free check: P(V<=txn-bias) coverage & bracket Brier)
    for nm, g in (("train", tr), ("test", x[x.date > TRAIN_END])):
        if g.empty:
            continue
        m, s = fm.loc_scale_vec(g) if hasattr(fm, "loc_scale_vec") else (None, None)
        ms = [fm.loc_scale(c, me, t, xn, h) for c, me, t, xn, h in zip(g.code, g.measure, g.txn, g.xnd, g.h)]
        m = np.array([a for a, _ in ms]).ravel(); s = np.array([b for _, b in ms]).ravel()
        # coverage of central intervals
        cov = {}
        for q in (0.5, 0.8, 0.9):
            half = stats.t.ppf(0.5 + q / 2, nu) * s
            cov[q] = float(((g.v >= np.floor(m - half + 0.5)) & (g.v <= np.ceil(m + half - 0.5))).mean())
        print(nm, len(g), "MAE(txn+bias)", round(float(np.abs(g.v - m).mean()), 2), "mean err", round(float((g.v - m).mean()), 2),
              "coverage", cov, "median s", round(float(np.median(s)), 2))
        for h, gg in g.assign(m=m, s=s).groupby("h"):
            print("   h", h, "n", len(gg), "MAE", round(float(np.abs(gg.v - gg.m).mean()), 2), "s", round(float(gg.s.median()), 2))
    print("bias by key:", {f"{k[0]}-{k[1]}": round(b, 2) for k, b in zip(keys, res.x[2 * (len(HBINS) - 1):])})


if __name__ == "__main__":
    main()
