"""wx: point-in-time fair values from Open-Meteo previous-runs forecasts (GFS, ECMWF IFS 0.25, NBM).

Daily feature for climate day D (local standard day) and lead L in {1, 2}:
  f_model = max (high) / min (low) of temperature_2m_previous_dayL over D's 24 local-standard hours.
Availability (conservative, see wx_fetch_om.py): lead 1 usable from D 08:00 LST, lead 2 from D-1 08:00 LST.

Model, fit on TRAIN climate dates only (<= TRAIN_END), separately per (measure, lead):
  mu = b[code] + w_g*f_gfs + w_e*f_ecmwf + w_n*f_nbm        (b absorbs station bias)
  s  = exp(a + c*log(1 + |f_gfs - f_ecmwf|) + d[code])     (scale grows with model disagreement)
  V ~ mu + s * t(nu) discretised to integers (CLI values).
Outputs data/vendor/wx/fc_daily.parquet, data/vendor/wx/fv_om_params.json, prints train/test skill.
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

MODELS = {"gfs_seamless": "g", "ecmwf_ifs025": "e", "ncep_nbm_conus": "n"}
NU = (3, 4, 6, 10, 30)


def daily_features():
    rows = []
    for c, (_, _, off, _) in STATIONS.items():
        parts = {}
        for mdl, k in MODELS.items():
            p = OUT / "om" / f"{mdl}_{c}.parquet"
            if not p.exists():
                continue
            d = pd.read_parquet(p)
            d["date"] = (d.time + pd.Timedelta(hours=off)).dt.date.astype(str)
            for L in (1, 2):
                col = f"temperature_2m_previous_day{L}"
                g = d.groupby("date")[col].agg(["max", "min", "count"])
                g = g[g["count"] == 24]
                parts[(k, L)] = g
        if not parts:
            continue
        for L in (1, 2):
            for meas, fn in (("high", "max"), ("low", "min")):
                cols = {}
                for k in MODELS.values():
                    if (k, L) in parts:
                        cols["f_" + k] = parts[(k, L)][fn]
                x = pd.DataFrame(cols)
                x["code"] = c; x["measure"] = meas; x["lead"] = L
                x = x.reset_index().rename(columns={"index": "date"})
                day0 = pd.to_datetime(x.date) - pd.Timedelta(hours=off)  # D 00:00 LST in UTC
                avail = day0 + pd.Timedelta(hours=8) - pd.Timedelta(days=L - 1)
                x["avail_ts"] = (avail - pd.Timestamp("1970-01-01")) // pd.Timedelta("1s")
                x["day_start_ts"] = (day0 - pd.Timestamp("1970-01-01")) // pd.Timedelta("1s")
                x["day_end_ts"] = x.day_start_ts + 86400
                rows.append(x)
    f = pd.concat(rows, ignore_index=True)
    f.to_parquet(OUT / "fc_daily.parquet", index=False)
    return f


def _unpack(th, ncode):
    w = th[:4]; a, c = th[4], th[5]; b = th[6:6 + ncode]; d = th[6 + ncode:6 + 2 * ncode]
    return w, a, c, b, d


def _musig(th, X, sp, ci, ncode):
    w, a, c, b, d = _unpack(th, ncode)
    mu = w[0] + b[ci] + X @ w[1:]
    s = np.exp(a + c * np.log1p(sp) + d[ci])
    return mu, s


def _nll(th, X, sp, ci, v, ncode, nu, lam=2.0):
    mu, s = _musig(th, X, sp, ci, ncode)
    p = stats.t.cdf((v + 0.5 - mu) / s, nu) - stats.t.cdf((v - 0.5 - mu) / s, nu)
    _, _, _, b, d = _unpack(th, ncode)
    return -np.log(np.maximum(p, 1e-9)).sum() + lam * (b ** 2).sum() + 20 * lam * (d ** 2).sum()


def design(x):
    X = x[["f_g", "f_e", "f_n"]].to_numpy(float)
    sp = np.abs(x.f_g - x.f_e).to_numpy(float)
    return X, sp


class OMFair:
    def __init__(self, params):
        self.P = params

    def musig(self, x):
        """x: DataFrame with code, measure, lead, f_g, f_e, f_n. Returns mu, s, nu arrays."""
        mu = np.full(len(x), np.nan); s = np.full(len(x), np.nan); nu = np.full(len(x), np.nan)
        for key, pr in self.P.items():
            meas, L = key.split("|"); L = int(L)
            m = ((x.measure == meas) & (x.lead == L)).to_numpy()
            if not m.any():
                continue
            codes = pr["codes"]; cid = {c: i for i, c in enumerate(codes)}
            ci = x.loc[m, "code"].map(cid).fillna(-1).astype(int).to_numpy()
            th = np.asarray(pr["theta"]); ncode = len(codes)
            th2 = np.r_[th, 0.0, 0.0]  # unknown code -> b=d=0 (appended slots)
            X, sp = design(x.loc[m])
            w, a, c, b, d = _unpack(th, ncode)
            bb = np.r_[b, 0.0]; dd = np.r_[d, 0.0]
            mu[m] = w[0] + bb[ci] + X @ w[1:]
            s[m] = np.exp(a + c * np.log1p(sp) + dd[ci])
            nu[m] = pr["nu"]
        return mu, s, nu

    @staticmethod
    def prob(mu, s, nu, lo, hi, floor_=None, cap_=None):
        """P(lo <= V <= hi) for integer V; optional hard support restriction V >= floor_ (or V <= cap_), renormalised."""
        lo = np.asarray(lo, float); hi = np.asarray(hi, float)
        L = np.full_like(mu, -np.inf) if floor_ is None else np.asarray(floor_, float)
        U = np.full_like(mu, np.inf) if cap_ is None else np.asarray(cap_, float)
        lo2 = np.maximum(lo, L); hi2 = np.minimum(hi, U)

        def cdf_le(v):  # P(V <= v), v integer or +-inf
            return np.where(np.isposinf(v), 1.0, np.where(np.isneginf(v), 0.0,
                            stats.t.cdf((np.nan_to_num(v, posinf=0, neginf=0) + 0.5 - mu) / s, nu)))
        num = np.where(hi2 >= lo2, cdf_le(hi2) - cdf_le(lo2 - 1), 0.0)
        den = cdf_le(U) - cdf_le(L - 1)
        return np.clip(num / np.maximum(den, 1e-12), 0, 1)


def load():
    with open(OUT / "fv_om_params.json") as fh:
        return OMFair(json.load(fh))


def main():
    f = daily_features()
    cli = pd.read_parquet(OUT / "cli.parquet")
    cl = cli.melt(id_vars=["code", "date"], value_vars=["high", "low"], var_name="measure", value_name="v").dropna()
    x = f.merge(cl, on=["code", "measure", "date"]).dropna(subset=["f_g", "f_e", "f_n"])
    tr = x[x.date <= TRAIN_END]; te = x[x.date > TRAIN_END]
    print("rows train", len(tr), "test", len(te), "train dates", tr.date.min(), tr.date.max())
    params = {}
    for (meas, L), g in tr.groupby(["measure", "lead"]):
        codes = sorted(g.code.unique()); cid = {c: i for i, c in enumerate(codes)}
        ci = g.code.map(cid).to_numpy(); X, sp = design(g); v = g.v.to_numpy(float)
        best = None
        for nu in NU:
            th0 = np.r_[0.0, 0.34, 0.33, 0.33, np.log(2.5), 0.0, np.zeros(len(codes)), np.zeros(len(codes))]
            r = optimize.minimize(_nll, th0, args=(X, sp, ci, v, len(codes), nu), method="L-BFGS-B")
            if best is None or r.fun < best[0].fun:
                best = (r, nu)
        r, nu = best
        params[f"{meas}|{L}"] = dict(theta=r.x.tolist(), codes=codes, nu=nu)
        w = r.x[:4]
        print(meas, L, "nu", nu, "w", np.round(w, 3), "a,c", np.round(r.x[4:6], 3))
    with open(OUT / "fv_om_params.json", "w") as fh:
        json.dump(dict(params), fh, indent=1)
    fm = OMFair(params)
    for nm, g in (("train", tr), ("test", te)):
        mu, s, nu = fm.musig(g)
        e = g.v - mu
        out = g.assign(mu=mu, s=s, nu=nu, e=e)
        # PIT coverage of the 80% central interval and log-score of the realised integer
        p = OMFair.prob(mu, s, nu, g.v, g.v)
        cov80 = ((g.v >= np.floor(mu - stats.t.ppf(0.9, nu) * s + 0.5)) & (g.v <= np.floor(mu + stats.t.ppf(0.9, nu) * s + 0.5))).mean()
        print(f"{nm}: n={len(g)} MAE={np.abs(e).mean():.2f} bias={e.mean():+.2f} cov80={cov80:.3f} "
              f"P(realised integer) mean={p.mean():.3f}")
        print(out.groupby(["measure", "lead"]).apply(lambda z: pd.Series(dict(n=len(z), mae=np.abs(z.e).mean(),
              bias=z.e.mean(), s=z.s.median()))).round(2).to_string())
        raw = {k: np.abs(g.v - g["f_" + k]).mean().round(2) for k in ("g", "e", "n")}
        print("   raw-model MAE (no calibration):", raw)


if __name__ == "__main__":
    main()
