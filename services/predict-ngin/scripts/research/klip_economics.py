"""Economics of Kalshi LIP farming from the causal fill sim (klip_fill_sim.py).

Per sim row (ticker, side, placement, q):
  capital-days = resting collateral-days + inventory collateral-days (each fill held from fill time to window end)
  net = LIP reward (scenario) + fill PnL (settlement value or last mid, minus maker fee)
Outputs:
  * by group x placement x q: reward, fill PnL, net per $ capital per day, event-cluster bootstrap CI
  * point-in-time capacity: rank candidates on the FIRST 25% of each window (reward rate - pooled cost rate of
    that placement/q in the same first 25%), evaluate on the LAST 75% only; greedy allocation of $1k-$10k.
Run: venv/Scripts/python.exe -I scripts/research/klip_economics.py --tag base
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
AN = ROOT / "data" / "vendor" / "klip" / "analysis"
CAPS = [1000, 2500, 5000, 10000]


def group_of(series: str) -> str:
    if series.startswith("KXCPI"):
        return "econ_cpi"
    if series.startswith("KXAAAGAS"):
        return "econ_gas"
    if "MENTION" in series or series.startswith("KXTRUMPSAY"):
        return "mentions"
    return "other"


def maker_fee(p, mult):
    return 0.0175 * mult * p * (1 - p)


def load(tag):
    s = pd.read_parquet(AN / f"sim_{tag}.parquet")
    f = pd.read_parquet(AN / f"fills_{tag}.parquet")
    s["group"] = s.series.map(group_of)
    s["event"] = s.ticker.str.rsplit("-", n=1).str[0]
    s["days"] = s.hours / 24
    key = ["ticker", "side", "placement", "q"]
    res = pd.read_parquet(sorted(AN.parent.glob("results_*.parquet"))[-1])
    fin = res[res.result.isin(["yes", "no"])].set_index("ticker").result  # determined or finalized
    if len(f):
        yv = f.ticker.map(fin).map({"yes": 1.0, "no": 0.0})
        newval = np.where(f.side == "yes", yv, 1 - yv)
        f["val"] = np.where(yv.notna(), newval, f.val)
        f["settled"] = f.settled | yv.notna().to_numpy()
        f = f.merge(s[key + ["t_split", "t_end", "fees", "fill_contracts"]], on=key, how="left")
        f["fee_c"] = np.where(f.fill_contracts > 0, f.fees / f.fill_contracts.replace(0, np.nan), 0.0)
        f["pnl"] = f.qty * (f.val - f.price - f.fee_c.fillna(0))
        f["inv_cd"] = f.qty * f.price * (f.t_end - f.ts).clip(lower=0) / 86400e9
        f["post"] = f.ts >= f.t_split
        f["inv_cd_post"] = np.where(f.post, f.inv_cd, f.qty * f.price * (f.t_end - f.t_split).clip(lower=0) / 86400e9)
        g = f.groupby(key).agg(inv_cd=("inv_cd", "sum"), inv_cd_post=("inv_cd_post", "sum"),
                               pnl_pre=("pnl", lambda x: x[~f.loc[x.index, "post"]].sum()),
                               pnl_post=("pnl", lambda x: x[f.loc[x.index, "post"]].sum()))
        g2 = f.groupby(key).agg(pnl_all=("pnl", "sum"), settled_fill=("settled", "mean"))
        s = s.merge(g.reset_index(), on=key, how="left").merge(g2.reset_index(), on=key, how="left")
        s["fill_pnl"] = s.pnl_all.fillna(0.0)  # re-marked with the latest settlement results
    for c in ("inv_cd", "inv_cd_post", "pnl_pre", "pnl_post"):
        s[c] = s.get(c, 0.0)
        s[c] = s[c].fillna(0.0)
    s["cap_days"] = s.rest_capital_days + s.inv_cd
    s["fill_pnl"] = s.fill_pnl.fillna(0.0)
    s["reward_post"] = s.reward - s.reward_pre
    s["cap_post"] = (s.rest_capital_days - s.cap_pre) + s.inv_cd_post
    return s, f


def boot(df, col_num, col_den, n=2000, seed=0):
    ev = df.groupby("event")[[col_num, col_den]].sum()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(ev), (n, len(ev)))
    num = ev[col_num].to_numpy()[idx].sum(1); den = ev[col_den].to_numpy()[idx].sum(1)
    r = num / np.where(den > 0, den, np.nan)
    return np.nanpercentile(r, 2.5), np.nanpercentile(r, 97.5)


def summary(s):
    rows = []
    for (grp, pl, q), d in list(s.groupby(["group", "placement", "q"])) + [(("ALL", pl, q), d) for (pl, q), d in s.groupby(["placement", "q"])]:
        d = d.copy()
        for scen, col in (("base", "reward"), ("comp2", "reward_comp2"), ("rival3", "reward_rival3")):
            d["net_" + scen] = d[col] + d.fill_pnl
        lo, hi = boot(d, "net_base", "cap_days")
        rows.append(dict(group=grp, placement=pl, q=q, n=len(d), events=d.event.nunique(), days=d.days.sum(),
                         cap_days=d.cap_days.sum(), reward=d.reward.sum(), fill_pnl=d.fill_pnl.sum(),
                         fills=d.fill_contracts.sum(), fees=d.fees.sum(),
                         net=d.net_base.sum(), net_comp2=d.net_comp2.sum(), net_rival3=d.net_rival3.sum(),
                         rew_per_capday=d.reward.sum() / d.cap_days.sum(),
                         cost_per_capday=-d.fill_pnl.sum() / d.cap_days.sum(),
                         net_per_capday=d.net_base.sum() / d.cap_days.sum(), ci_lo=lo, ci_hi=hi,
                         top5_share=(d.groupby("event").net_base.sum().nlargest(5).sum() / d.net_base.sum()) if d.net_base.sum() > 0 else np.nan))
    return pd.DataFrame(rows)


def capacity(s, scen_col="reward"):
    """Point-in-time. For each group, choose the (placement, q) with the best pooled net rate on the FIRST 25% of
    every window (and, inside it, only markets whose first-25% reward rate beats that config's first-25% cost
    rate); evaluate pooled net per capital-day on the LAST 75% only. $/day at C = rate * min(C, available
    capital), where available capital = post capital-days / post calendar days."""
    s = s[(s.cap_pre > 0) & (s.cap_post > 0)].copy()
    post_frac = (s.reward_post / s.reward.replace(0, np.nan)).fillna(0.75)
    s["rew_post_scen"] = s[scen_col] * post_frac
    s["net_post"] = s.rew_post_scen + s.pnl_post
    s["net_pre"] = s.reward_pre + s.pnl_pre
    span = (s.t_end.max() - s.t_split.min()) / 86400e9
    out = []
    for grp, d in list(s.groupby("group")) + [("ALL", s)]:
        pre = d.groupby(["placement", "q"]).apply(lambda x: x.net_pre.sum() / x.cap_pre.sum(), include_groups=False)
        pl, q = pre.idxmax()
        x = d[(d.placement == pl) & (d.q == q)]
        cost_pre = -x.pnl_pre.sum() / x.cap_pre.sum()
        x = x[x.reward_pre / x.cap_pre > cost_pre]
        rate = x.net_post.sum() / x.cap_post.sum() if len(x) else 0.0
        avail = x.cap_post.sum() / span if len(x) else 0.0
        lo, hi = boot(x.assign(n=x.net_post, c=x.cap_post), "n", "c") if len(x) > 3 else (np.nan, np.nan)
        row = dict(group=grp, config=f"{pl}/{q}", pre_rate=pre.max(), n=len(x), events=x.event.nunique(),
                   post_rate=rate, ci_lo=lo, ci_hi=hi, avail_capital=avail,
                   reward_rate=x.rew_post_scen.sum() / max(x.cap_post.sum(), 1e-9),
                   cost_rate=-x.pnl_post.sum() / max(x.cap_post.sum(), 1e-9))
        for C in CAPS:
            row[f"usd_day_{C}"] = rate * min(C, avail)
            row[f"deployed_{C}"] = min(C, avail)
        out.append(row)
    return pd.DataFrame(out)


BUCKET_NAMES = ["<1h", "1-6h", "6-24h", "24-72h", ">72h"]
EDGES_H = [0, 1, 6, 24, 72, 1e9]


def by_time_to_close(s, f):
    """Reward, fill PnL and capital-days by hours-to-close bucket (close_time from the results file).
    Inventory capital is attributed to the bucket of the fill (approximation)."""
    res = pd.read_parquet(sorted(AN.parent.glob("results_*.parquet"))[-1]).set_index("ticker")
    close = pd.to_datetime(res.close_time, format="ISO8601", utc=True)
    close_ns = close.astype("datetime64[ns, UTC]").astype("int64")  # ns exact, not seconds
    f = f.copy()
    f["h"] = (f.ticker.map(close_ns) - f.ts) / 3600e9
    f["b"] = pd.cut(f.h, EDGES_H, right=False, labels=False)
    rows = []
    for (grp, pl, q), d in list(s.groupby(["group", "placement", "q"])) + [(("ALL", pl, q), d) for (pl, q), d in s.groupby(["placement", "q"])]:
        ff = f.merge(d[["ticker", "side", "placement", "q"]], on=["ticker", "side", "placement", "q"])
        for i, nm in enumerate(BUCKET_NAMES):
            rew = d[f"rew_b{i}"].sum(); cap = d[f"cap_b{i}"].sum()
            fb = ff[ff.b == i]
            pnl = fb.pnl.sum(); inv = fb.inv_cd.sum()
            rows.append(dict(group=grp, placement=pl, q=q, ttc=nm, reward=rew, fill_pnl=pnl, cap_days=cap + inv,
                             net_rate=(rew + pnl) / (cap + inv) if cap + inv > 0 else np.nan,
                             rew_rate=rew / (cap + inv) if cap + inv > 0 else np.nan))
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="base")
    a = ap.parse_args()
    s, f = load(a.tag)
    sm = summary(s)
    sm.to_csv(AN / f"econ_summary_{a.tag}.csv", index=False)
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
    print(sm.round(4).to_string())
    if "rew_b0" in s.columns:
        tt = by_time_to_close(s, f)
        tt.to_csv(AN / f"econ_ttc_{a.tag}.csv", index=False)
        print(tt[tt.group.isin(["ALL", "mentions", "econ_cpi", "econ_gas"]) & (tt.placement == "ref")].round(4).to_string())
    caps = []
    for scen, col in (("base", "reward"), ("comp2", "reward_comp2"), ("rival3", "reward_rival3")):
        c = capacity(s, col); c["scen"] = scen; caps.append(c)
    caps = pd.concat(caps)
    caps.to_csv(AN / f"econ_capacity_{a.tag}.csv", index=False)
    print(caps.round(2).to_string())


if __name__ == "__main__":
    main()
