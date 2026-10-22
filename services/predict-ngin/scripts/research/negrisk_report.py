"""Tables for docs/POLYMARKET_INTRAVENUE_ARB.md from the negrisk_* outputs (RESEARCH ONLY).

Latency race model (same convention as docs/TICK_ARB_VALIDATION.md): an episode (net-of-fee
edge > 0 on every leg's top of book) detected at t0 is captured by a bot with round-trip latency L iff it is
still open at t0 + L; the captured profit is the depth-walked, capital-capped profit (state at t0 + L for
the WS feed, at t0 for vendor books). An attempt on an episode that closed before t0 + L is a leg-risk
failure costing FAIL_C per set on the size we would have sent (one leg left unfilled and unwound across
the spread). Sports in-play marketable orders carry a venue delay (gamma `secondsDelay` = 1), so sports
episodes are only reachable at L >= 1 s.

    venv/Scripts/python.exe scripts/research/negrisk_report.py > data/vendor/negrisk/report.md
"""

from __future__ import annotations

import glob
import json
from pathlib import Path

import numpy as np
import pandas as pd

D = Path("data/vendor/negrisk")
LAT = (0.0, 0.05, 0.25, 1.0, 5.0)
CAPS = (1000, 2500, 5000, 10000)
FAIL_C = 0.01
SPORT_PREFIX = ("nba", "nfl", "mlb", "nhl", "epl", "ucl", "uel", "elc", "bra", "fl1", "lal", "bun", "sea", "itc",
                "fif", "chi", "crint", "col", "acle", "cfb", "atp", "wta", "mls", "ufc")


def md(df: pd.DataFrame, fmt="{:,.2f}") -> str:
    d = df.copy()
    for c in d.columns:
        if pd.api.types.is_float_dtype(d[c]):
            d[c] = d[c].map(lambda v: "" if pd.isna(v) else fmt.format(v))
    return d.to_markdown(index=not isinstance(d.index, pd.RangeIndex))


def flicker_groups(E: pd.DataFrame, key: str, t0: str, t_unit: float, gap_s: float = 60.0) -> pd.Series:
    """Opportunity id: episodes of the same group/side separated by < gap_s are one opportunity (a sum that
    flickers around 1 is the same resting liquidity and can be taken once)."""
    E = E.sort_values([key, "side", t0])
    end = E[t0] + E.dur_ms.fillna(0) * 1e-3 / t_unit
    new = (E[key] != E[key].shift()) | (E.side != E.side.shift()) | ((E[t0] - end.shift()) * t_unit > gap_s)
    return new.cumsum()


def race(E: pd.DataFrame, L: float, prof: np.ndarray, fail: np.ndarray, sport_delay=True):
    """Per-opportunity PnL at latency L: max profit over parts that live longer than the effective latency
    (sports: +1 s venue delay); if no part survives, one failed attempt costing `fail` (first part)."""
    Le = (np.where(E.sport.values, np.maximum(L, 1.0) + L, L) if sport_delay else np.full(len(E), L)) * 1000
    ok = E.dur_ms.values > Le
    df = pd.DataFrame({"g": E.grp.values, "ok": ok, "p": np.where(ok, prof, -np.inf), "f": fail})
    a = df.groupby("g").agg(ok=("ok", "any"), p=("p", "max"), f=("f", "first"))
    return np.where(a.ok, a.p, a.f), a.ok.values


def is_sport(slug: str) -> bool:
    s = str(slug)
    return s.startswith(SPORT_PREFIX) or "-halftime-" in s or "exact-score" in s


# ----------------------------------------------------------------------------- live REST scan
def live_scan():
    fs = sorted(glob.glob(str(D / "live" / "sweep_*.parquet")))
    if not fs:
        return
    S = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)
    n_sw = S.sweep.nunique()
    hrs = (S.t.max() - S.t.min()) / 3600
    print(f"\n## Live REST scan\n\n{n_sw} sweeps over {hrs:.2f} h, {S.nr_id.nunique():,} NegRisk groups per sweep "
          f"(complete = every unresolved outcome tradable: {S.groupby('sweep').complete.mean().mean():.1%}).\n")
    g = S.groupby("sweep")
    t = pd.DataFrame({
        "buy_net>0 groups": g.apply(lambda x: (x.buy_top_edge > 0).sum()),
        "sell_net>0 groups": g.apply(lambda x: (x.sell_top_edge > 0).sum()),
        "buy gross (sum_ask<1) groups": g.apply(lambda x: (x.sum_ask < 1).sum()),
        "sell gross (sum_bid>1) groups": g.apply(lambda x: (x.sum_bid > 1).sum()),
        "buy $ (depth, no cap)": g.buy_profit.sum(),
        "sell $ (depth, no cap)": g.sell_profit.sum(),
        "buy $ @1k": g.buy_profit_1000.sum(),
        "buy $ @10k": g.buy_profit_10000.sum(),
    })
    print(md(t.describe().loc[["mean", "50%", "min", "max"]]))
    pos = S[(S.buy_top_edge > 0) | (S.sell_top_edge > 0)]
    agg = pos.groupby("event_slug").agg(sweeps=("sweep", "nunique"), n=("n_open", "first"),
                                        buy_edge=("buy_top_edge", "median"), buy_profit=("buy_profit", "median"),
                                        sell_edge=("sell_top_edge", "median"), sell_profit=("sell_profit", "median"),
                                        end=("event_end", "first"), fee=("fee_rate", "first"))
    print("\nGroups with a net-of-fee arb in any sweep (median over the sweeps where present):\n")
    print(md(agg.sort_values("sweeps", ascending=False).head(25), "{:,.4f}"))
    c = S[S.complete & S.sum_ask.notna()]
    dev = c.groupby("nr_id").sum_ask.median() - 1
    print(f"\nComplete groups: median (sum of best asks - 1) = {dev.median():.4f}; share with < 1: "
          f"{(dev < 0).mean():.2%}; median (sum of best bids - 1) = "
          f"{(S.groupby('nr_id').sum_bid.median() - 1).median():.4f}.")
    mf = D / "live" / "mirror_check.parquet"
    if mf.exists():
        M = pd.read_parquet(mf)
        print(f"\nBinary mirror check: {len(M):,} YES/NO book pairs; max |ask_NO - (1 - bid_YES)| = "
              f"{M.mirror_err.max():.6f}; min ask_YES + ask_NO = {M.sum_asks.min():.4f}; crossed books "
              f"{int(M.crossed.sum())}.")


# ----------------------------------------------------------------------------- WS episodes
def ws_episodes():
    """Episodes from the WS edge log. Standing = already open within 3 min of the capture start (a stock of
    resting mispricing, takeable once); flow = opened later (new opportunities per unit time)."""
    fs = sorted(glob.glob(str(D / "ws" / "edges_*.parquet")))
    if not fs:
        return None
    W = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True).sort_values(["event_id", "t_recv_ns"])
    t_start, t_stop = W.t_recv_ns.min(), W.t_recv_ns.max()
    hrs = (t_stop - t_start) / 3.6e12
    u = pd.read_parquet(D / "universe_latest.parquet").drop_duplicates("nr_id").set_index("nr_id")
    eps = []
    for eid, g in W.groupby("event_id"):
        t = g.t_recv_ns.values
        for side in ("buy", "sell"):
            e = g[f"{side}_edge"].values.astype(float)
            on = np.nan_to_num(e, nan=-1) > 0
            d = np.diff(np.concatenate([[0], on.astype(int), [0]]))
            for s0, e0 in zip(np.where(d == 1)[0], np.where(d == -1)[0]):
                end = t[e0] if e0 < len(t) else t_stop
                rec = dict(event_id=eid, slug=u.event_slug.get(eid), side=side, t0_ns=t[s0],
                           dur_ms=(end - t[s0]) / 1e6, censored=e0 >= len(t), edge0=e[s0], n=int(g.n.iloc[0]),
                           standing=(t[s0] - t_start) < 180e9, end_date=u.event_end.get(eid),
                           walk_shares=float(np.nan_to_num(g[f"{side}_shares"].values[s0])) if f"{side}_shares" in g else 0.0)
                for c in CAPS:
                    col = f"{side}_profit_{c}"
                    rec[f"profit_{c}"] = float(np.nan_to_num(g[col].values[s0])) if col in g else 0.0
                rec["walk_profit"] = float(np.nan_to_num(g[f"{side}_profit"].values[s0])) if f"{side}_profit" in g else 0.0
                eps.append(rec)
    E = pd.DataFrame(eps)
    E["sport"] = E.slug.map(is_sport)
    E["grp"] = flicker_groups(E, "event_id", "t0_ns", 1e-9)
    return E, hrs


def vendor_latency(E: pd.DataFrame, hrs: float, label: str, scale=1.0, sport_delay=True, fail_c=None):
    fail_c = FAIL_C if fail_c is None else fail_c
    rows = []
    for side in ("buy", "sell"):
        x = E[E.side == side]
        if x.empty:
            continue
        cps = 1.0 if side == "buy" else np.maximum(x.n.values - 1, 1)  # capital per set
        w = x.w.values if "w" in x else np.ones(len(x))
        for L in LAT:
            r = dict(side=side, L=f"{int(L * 1000)}ms", opportunities=x.grp.nunique())
            for c in CAPS:
                v, ok = race(x, L, x[f"profit_{c}"].values * w,
                             -fail_c * np.minimum(x.walk_shares.values, c / cps) * w, sport_delay)
                r["captured"] = int(ok.sum())
                r[f"$/wk @{c / 1000:g}k"] = v.sum() * 168 / hrs * scale
            rows.append(r)
    print("\n" + label + ":\n")
    print(md(pd.DataFrame(rows)))


def probalytics():
    f = D / "probalytics" / "episodes.parquet"
    if not f.exists():
        return
    E = pd.read_parquet(f)
    S = pd.read_parquet(D / "probalytics" / "event_stats.parquet")
    u = pd.read_parquet(D / "universe_latest.parquet")
    nclosed = u.groupby("nr_id").closed.sum()
    bad = set(S.nr_id[S.nr_id.map(nclosed) > 0])
    E, S = E[~E.nr_id.isin(bad)].copy(), S[~S.nr_id.isin(bad)].copy()
    ev = pd.read_parquet(D / "probalytics" / "sample_events.parquet").set_index("nr_id")
    E["sport"] = E.slug.map(is_sport)
    S["stratum"] = S.nr_id.map(ev.stratum)
    E["stratum"] = E.nr_id.map(ev.stratum)
    hrs = 7 * 24  # visible window
    n_raw = len(E)
    E["grp"] = flicker_groups(E, "nr_id", "t0_ns", 1e-9)
    print(f"\n## Probalytics 7-day tick books\n\n{len(S)} NegRisk groups ({(S.stratum == 'top').sum()} top-volume, "
          f"{(S.stratum == 'random').sum()} random), {S.hours.sum():,.0f} group-hours, "
          f"{S.n_changes.sum():,} book changes. Excluded {len(bad)} group(s) whose legs resolved inside the window; "
          f"{n_raw} episodes form {E.grp.nunique()} opportunities (gaps < 60 s = same resting liquidity).\n")
    print("Share of time an arb is open (mean over groups): gross buy "
          f"{S.buy_gross_on_frac.mean():.2%}, net buy {S.buy_on_frac.mean():.3%}; gross sell "
          f"{S.sell_gross_on_frac.mean():.2%}, net sell {S.sell_on_frac.mean():.3%}.\n")
    for side in ("buy", "sell"):
        x = E[E.side == side]
        if len(x):
            q = x.dur_ms.quantile([.1, .25, .5, .75, .9])
            print(f"- {side}: {len(x)} episodes in {x.nr_id.nunique()} groups; duration ms p10/p25/p50/p75/p90 = "
                  f"{'/'.join(f'{v:,.0f}' for v in q)}; median edge {x.edge0.median():.4f}; "
                  f"depth-walked profit sum ${x.walk_profit.sum():,.2f} (top group "
                  f"{x.groupby('slug').walk_profit.sum().idxmax()} ${x.groupby('slug').walk_profit.sum().max():,.2f})")
    vendor_latency(E, hrs, "Latency race on the 7-day sample (sample totals per week, no scaling)")
    vendor_latency(E, hrs, "Same, failed attempts free (upper bound)", fail_c=0.0)
    # scale random stratum to population
    pop = 0
    for nr, g in u.groupby("nr_id"):
        o = g[~g.closed]
        if 2 <= len(o) <= 12 and o.accepting.all() and g.closed.sum() == 0 and o.event_volume24h.iloc[0] >= 1000:
            pop += 1
    n_rand = (S.stratum == "random").sum()
    w = (pop - 40) / max(n_rand, 1)
    E["w"] = np.where(E.stratum == "random", w, 1.0)
    print(f"\nPopulation of comparable groups (2-12 legs, none resolved, 24h vol >= $1k): {pop}; random-stratum "
          f"weight {w:.1f}.")
    vendor_latency(E, hrs, "Same, extrapolated to the population of comparable groups (random stratum weighted)")
    return E


def tardis():
    f = D / "tardis" / "episodes.parquet"
    if not f.exists():
        return
    E = pd.read_parquet(f)
    P = pd.read_parquet(D / "tardis" / "groups_stats.parquet")
    E["sport"] = E.slug.map(is_sport)
    n_raw = len(E)
    E["grp"] = flicker_groups(E, "nr_id", "t0_us", 1e-6)
    cap_sh = np.minimum(E.size0, 1000.0)  # $1k account: ~1000 sets on the buy side
    E["profit_cap"] = E.edge0 * cap_sh
    E["fail_cap"] = -FAIL_C * cap_sh
    print(f"\n## Tardis free days (top-of-book, ms)\n\n{len(P)} groups over {P.date.nunique()} days "
          f"({P.groupby('date').size().to_dict()}); {n_raw} episodes form {E.grp.nunique()} opportunities.\n")
    t = E.groupby(["date", "side"]).agg(episodes=("dur_ms", "size"), med_ms=("dur_ms", "median"),
                                        p90_ms=("dur_ms", lambda v: v.quantile(.9)),
                                        med_edge=("edge0", "median"), profit_1k=("profit_cap", "sum"),
                                        sport_share=("sport", "mean"))
    print(md(t))
    rows = []
    for L in LAT:
        v1, ok = race(E, L, E.profit_cap.values, E.fail_cap.values)
        v0, _ = race(E, L, E.profit_cap.values, np.zeros(len(E)))
        rows.append(dict(L=f"{int(L * 1000)}ms", captured=int(ok.sum()), of=len(ok),
                         profit_fail1c=float(v1.sum()), per_day_fail1c=float(v1.sum() / P.date.nunique()),
                         per_day_fail0=float(v0.sum() / P.date.nunique())))
    print("\nLatency race on top-of-book size capped at 1,000 sets (5 days, sports +1 s venue delay):\n")
    print(md(pd.DataFrame(rows)))


def predexon():
    fs = sorted(glob.glob(str(D / "predexon" / "points*.parquet")))
    if not fs:
        return
    P = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True).sort_values("month")
    P = P[P.max_age_s <= 300]
    g = P.groupby("month")
    t = pd.DataFrame({
        "groups": g.event_id.nunique(), "points": g.size(),
        "median sum_ask-1": g.sum_ask.median() - 1,
        "p10 sum_ask-1": g.sum_ask.quantile(.1) - 1,
        "gross buy<0 share": g.apply(lambda x: (x.sum_ask < 1).mean()),
        "net buy>0 share": g.apply(lambda x: (x.buy_net > 0).mean()),
        "net sell>0 share": g.apply(lambda x: (x.sell_net > 0).mean()),
        "groups w/ net arb": g.apply(lambda x: x[(x.buy_net > 0) | (x.sell_net > 0)].event_id.nunique()),
        "median |dev|": g.apply(lambda x: np.nanmedian(np.abs(x.sum_ask - 1))),
    })
    print("\n## Predexon snapshots Jan-Oct 2026 (as-of merge, every leg <= 5 min old)\n")
    print(md(t, "{:,.4f}"))


def dune():
    p = D / "dune"
    if not (p / "conversions.csv").exists():
        return
    print("\n## On-chain (Dune)\n")
    c = pd.read_csv(p / "conversions.csv")
    c["m"] = c.m.str[:7]
    print("NegRisk conversions per month (wallet identity hidden behind the collateral adapter from 2026-05):\n")
    print(md(c.set_index("m")))
    for f, lab in (("baskets_b30", "buy-all-YES baskets, 60 s window"), ("baskets_b300", "buy-all-YES baskets, 10 min window"),
                   ("nobaskets_b30", "buy-NO baskets (sum NO < k-1), 60 s window")):
        if not (p / f"{f}.csv").exists():
            continue
        d = pd.read_csv(p / f"{f}.csv")
        d["q"] = d.m.str[:4] + "Q" + ((d.m.str[5:7].astype(int) - 1) // 3 + 1).astype(str)
        q = d.groupby(["q", "role"]).agg(pos_pnl=("pos_pnl", "sum"), top1=("top1_pos_pnl", "sum"),
                                          top10=("top10_pos_pnl", "sum"), wallets=("wallets_pos", "sum"),
                                          baskets=("pos_baskets", "sum")).unstack("role")
        print(f"\n{lab} (positive-PnL baskets, per quarter; 2026Q4 = 12 days):\n")
        print(md(q, "{:,.0f}"))


def main():
    live_scan()
    r = ws_episodes()
    if r is not None:
        E, hrs = r
        E.to_parquet(D / "ws_episodes.parquet", index=False)
        print(f"\n## WebSocket tick watch (all NegRisk groups, {hrs:.2f} h)\n")
        for lab, x in (("standing (open at start)", E[E.standing]), ("flow (opened during capture)", E[~E.standing])):
            for side in ("buy", "sell"):
                y = x[x.side == side]
                if len(y):
                    q = y.dur_ms.quantile([.1, .5, .9]) / 1000
                    first = y.sort_values("t0_ns").groupby("grp").first()
                    print(f"- {lab}, {side}: {len(y)} episodes / {y.grp.nunique()} opportunities in "
                          f"{y.event_id.nunique()} groups; duration s p10/p50/p90 = {q.iloc[0]:.2f}/{q.iloc[1]:.2f}/"
                          f"{q.iloc[2]:.2f}; profit at first sight (depth-walked) ${first.walk_profit.sum():,.2f}; "
                          f"groups: {', '.join(sorted(set(y.slug.astype(str)))[:8])}")
        fl = E[~E.standing]
        if len(fl):
            vendor_latency(fl, hrs, "Latency race on flow episodes, WS feed ($/wk scaled from the capture; fail = -1c/set)")
    probalytics()
    tardis()
    predexon()
    dune()


if __name__ == "__main__":
    main()
