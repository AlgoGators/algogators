"""mgdelt step 4: pre-registered gate (Test 1) on TRAIN only.
y ~ 1 + X, offset logit(p), event-clustered SE; event-grouped 5-fold CV log-loss gain with event bootstrap.
usage: python mgdelt_gate.py train
"""
import json
import sys

import numpy as np
import pandas as pd
import statsmodels.api as sm

OUT = "data/vendor/mgdelt/"
SPLIT = sys.argv[1] if len(sys.argv) > 1 else "train"
assert SPLIT == "train", "gate is train-only; the test period is not touched unless the gate passes"
PRIMARY = ["z_w", "B"]  # Amendment 3: z winsorised at its train 1st/99th percentiles (outcome-blind)
SECOND = ["z", "HHI", "L"]
rng = np.random.default_rng(0)

p = pd.read_parquet(OUT + f"features_{SPLIT}.parquet")
p["lp"] = np.log(p.p / (1 - p.p))
lo, hi = p.z.quantile([0.01, 0.99])
p["z_w"] = p.z.clip(lo, hi)


def ll(y, q):
    q = np.clip(q, 1e-6, 1 - 1e-6)
    return -(y * np.log(q) + (1 - y) * np.log(1 - q))


def fit(df, cols, offset=True):
    X = sm.add_constant(df[cols]) if cols else np.ones((len(df), 1))
    kw = dict(offset=df.lp.values) if offset else {}
    return sm.GLM(df.y.values, X, family=sm.families.Binomial(), **kw).fit(
        cov_type="cluster", cov_kwds={"groups": pd.factorize(df.event_ticker)[0]})


def pred(m, df, cols):
    X = sm.add_constant(df[cols], has_constant="add") if cols else np.ones((len(df), 1))
    return m.predict(X, offset=df.lp.values)


def cv_delta(df, var, k=5):
    ev = df.event_ticker.unique()
    fold = dict(zip(ev, rng.permutation(len(ev)) % k))
    f = df.event_ticker.map(fold).values
    d = np.zeros(len(df))
    for i in range(k):
        tr, te = df[f != i], df[f == i]
        q0 = pred(fit(tr, []), te, [])
        q1 = pred(fit(tr, [var]), te, [var])
        d[f == i] = ll(te.y.values, np.asarray(q0)) - ll(te.y.values, np.asarray(q1))
    g = df.assign(d=d).groupby("event_ticker").d.agg(["sum", "size"])
    s, n = g["sum"].values, g["size"].values
    bs = []
    for _ in range(2000):
        ix = rng.integers(0, len(g), len(g))
        bs.append(s[ix].sum() / n[ix].sum())
    return d.mean(), list(np.percentile(bs, [2.5, 97.5]))


def run(df0, var, tag):
    df = df0.dropna(subset=[var]).copy()
    if len(df) < 50:
        return None
    df[var] = (df[var] - df[var].mean()) / df[var].std()
    m = fit(df, [var])
    dm, ci = cv_delta(df, var)
    mr = fit(df, ["lp", var], offset=False)
    df["ter"] = pd.qcut(df[var].rank(method="first"), 3, labels=["low", "mid", "high"])
    cal = df.groupby("ter", observed=True).agg(n=("y", "size"), p=("p", "mean"), y=("y", "mean"))
    cal["gap"] = cal.y - cal.p
    passed = bool(abs(np.asarray(m.tvalues)[1]) >= 2.24 and ci[0] > 0)
    r = dict(tag=tag, var=var, N=len(df), events=df.event_ticker.nunique(), coef=float(np.asarray(m.params)[1]),
             t=float(np.asarray(m.tvalues)[1]), cv_dll=float(dm), cv_ci=[float(c) for c in ci],
             coef_regressor=float(np.asarray(mr.params)[2]), t_regressor=float(np.asarray(mr.tvalues)[2]), coef_lp=float(np.asarray(mr.params)[1]),
             passed=passed if var in PRIMARY and tag == "full" else "secondary",
             calib={k: {c: float(v) for c, v in row.items()} for k, row in cal.iterrows()})
    print(f"[{tag}] {var}: N={len(df)} ev={r['events']} coef/SD={r['coef']:+.4f} t={r['t']:+.2f} "
          f"CV dLL/mkt={dm:+.5f} [{ci[0]:+.5f},{ci[1]:+.5f}] | regressor t={r['t_regressor']:+.2f} "
          f"lp coef={r['coef_lp']:.2f} | pass={r['passed']}")
    print(cal.round(3).to_string())
    return r


print(f"== {SPLIT}: N={len(p)} mkts {p.event_ticker.nunique()} events YES {p.y.mean():.3f}")
print("coverage:", {v: round(p[v].notna().mean(), 3) for v in PRIMARY + SECOND})
res = []
for tag, df in (("full", p), ("compliant", p[p.compliant])):
    for v in PRIMARY + SECOND:
        r = run(df, v, tag)
        if r:
            res.append(r)
gate = any(r["passed"] is True for r in res)
print("\nGATE PASSED" if gate else "\nGATE FAILED -> stop (no test fetch, no strategy)")
with open(OUT + f"gate_{SPLIT}.json", "w") as fh:
    json.dump(dict(results=res, gate=gate), fh, indent=1)
