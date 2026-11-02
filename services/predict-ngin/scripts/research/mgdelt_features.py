"""mgdelt step 3: point-in-time features from cached GDELT responses (no network unless missing:
missing responses are left NaN; run mgdelt_fetch.py first).

usage: python mgdelt_features.py <split>
Output: data/vendor/mgdelt/features_<split>.parquet
"""
import os
import sys
from urllib.parse import urlparse

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import gammaln

sys.path.insert(0, os.path.dirname(__file__))
import mgdelt_common as C  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from src.timeconv import to_epoch_seconds  # noqa: E402
from mgdelt_fetch import al_params, tl_params  # noqa: E402

DAY = 86400


def cached(params):
    import hashlib
    import json
    import urllib.parse
    url = C.API + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    fn = C.CACHE + hashlib.sha1(url.encode()).hexdigest() + ".json"
    if not os.path.exists(fn):
        return None
    with open(fn, encoding="utf-8") as f:
        return json.load(f)


def timeline(obj):
    if not obj or "_error" in obj or "timeline" not in obj or not obj["timeline"]:
        return None
    d = pd.DataFrame(obj["timeline"][0]["data"])
    d["day"] = to_epoch_seconds(pd.to_datetime(d.date, format="%Y%m%dT%H%M%SZ", utc=True)).astype("int64")
    return d.set_index("day")[["value", "norm"]].astype(float)


def nb_fit(y, logn, dow, lam=1.0):
    """NB2 with offset logn, intercept + 6 ridge-penalised DOW terms, log alpha. Returns (mu_fn, alpha)."""
    X = np.zeros((len(y), 7))
    X[:, 0] = 1
    for k in range(1, 7):
        X[:, k] = (dow == k)
    # pseudo-count 0.5 at the mean exposure (keeps all-zero baselines defined)
    y2 = np.append(y, 0.5)
    X2 = np.vstack([X, [1, 0, 0, 0, 0, 0, 0]])
    o2 = np.append(logn, np.log(np.mean(np.exp(logn))))

    def nll(th):
        b, la = th[:7], th[7]
        a = np.exp(la)
        mu = np.exp(np.clip(X2 @ b + o2, -30, 30))
        r = 1 / a
        ll = (gammaln(y2 + r) - gammaln(r) - gammaln(y2 + 1) + r * np.log(r / (r + mu))
              + y2 * np.log(mu / (r + mu) + 1e-300))
        return -ll.sum() + lam * np.sum(b[1:] ** 2)

    b0 = np.log((y.sum() + 0.5) / np.exp(logn).sum())
    th0 = np.r_[b0, np.zeros(6), np.log(0.5)]
    res = minimize(nll, th0, method="L-BFGS-B",
                   bounds=[(None, None)] * 7 + [(np.log(1e-4), np.log(50))])
    return res.x[:7], float(np.exp(res.x[7]))


def zfeat(tl, D):
    base_days = np.arange(D - 59 * DAY, D - 3 * DAY, DAY)
    att_days = np.arange(D - 3 * DAY, D, DAY)
    if not set(base_days).issubset(tl.index) or not set(att_days).issubset(tl.index):
        return None
    b = tl.loc[base_days]
    a = tl.loc[att_days]
    if (b.norm <= 0).any() or (a.norm <= 0).any():
        return None
    dow = lambda days: pd.to_datetime(days, unit="s", utc=True).dayofweek.values  # noqa: E731
    beta, alpha = nb_fit(b.value.values, np.log(b.norm.values), dow(base_days))
    Xa = np.zeros((3, 7))
    Xa[:, 0] = 1
    da = dow(att_days)
    for k in range(1, 7):
        Xa[:, k] = da == k
    mu = np.exp(Xa @ beta + np.log(a.norm.values))
    Y, M, V = a.value.sum(), mu.sum(), (mu + alpha * mu ** 2).sum()
    return dict(Y3=Y, M3=M, z=(Y - M) / np.sqrt(V), alpha=alpha,
                L=np.log((Y + 0.5) / a.norm.sum()), base_cnt=b.value.sum())


def breadth(obj):
    if obj is None:
        return dict(al_fetched=0)
    if "_error" in obj:
        return dict(al_fetched=1, al_err=1)
    arts = obj.get("articles", [])
    doms = [a.get("domain") or urlparse(a.get("url", "")).netloc for a in arts]
    n = len(doms)
    out = dict(al_fetched=1, al_err=0, n_art=n, n_dom=len(set(doms)), B=np.log1p(len(set(doms))))
    if n >= 5:
        s = pd.Series(doms).value_counts(normalize=True)
        out["HHI"] = float((s ** 2).sum())
    return out


def main():
    split = sys.argv[1]
    p = pd.read_parquet(C.OUT + "panel.parquet")
    p = p[(p.split == split) & p.term_q.notna()].copy()
    tls = {}
    rows = []
    for r in p.itertuples():
        k = (r.spk_q, r.term_q)
        if k not in tls:
            obj = cached(tl_params(r.spk_q, r.term_q, split))
            tls[k] = (obj is not None, "_error" in obj if obj else False, timeline(obj))
        fetched, err, tl = tls[k]
        f = dict(ticker=r.ticker, tl_fetched=int(fetched), tl_err=int(err))
        if tl is not None:
            zf = zfeat(tl, int(r.D))
            if zf:
                f.update(zf)
        elif fetched and not err:
            f.update(dict(Y3=0.0, z=np.nan))  # empty result: no co-mentions at all in span
        f.update(breadth(cached(al_params(r.spk_q, r.term_q, r.Dstr))))
        rows.append(f)
    F = p.merge(pd.DataFrame(rows), on="ticker")
    F.to_parquet(C.OUT + f"features_{split}.parquet", index=False)
    print(F[["tl_fetched", "tl_err", "al_fetched"]].mean())
    print(F[["z", "B", "HHI", "L", "alpha"]].describe().T)


if __name__ == "__main__":
    main()
