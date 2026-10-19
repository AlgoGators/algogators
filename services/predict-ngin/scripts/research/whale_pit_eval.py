"""whale_pit stage 3: train-selected, frozen, out-of-sample evaluation of point-in-time whale following.

Pre-registered (written before any return was looked at, 2026-10-17):
  * Train = signals 2024-01-01..2024-12-31, test = 2025-01-01..2025-12-31. Skill history starts 2022-11.
  * Grid (48 trials): score in {mk (24h markout), res (resolution)}; whale trade >= {$1k, $5k, $25k};
    shrunk score S = sum(pnl) / (sum(usd) + $20k) >= theta in {0, 0.02, 0.05, 0.10}; >= 20 prior scored
    trades; exit in {hold to resolution, sell after 24h}.
  * Follower: sees the whale's fill on chain, enters 60 s later (stress 300 s) at the last print as of
    t + 60 s plus 1c (2c if that print hit the bid), +1c / +2c slippage stress; Polymarket taker fee
    (2026 schedule, predict_ngin.costs.polymarket) on entry and on a 24h exit; a 24h exit sells 2c
    under the last hourly close. Token cost at the reference print must be in [0.05, 0.95].
  * One position per (market, side, UTC day): the first qualifying signal.
  * Selection: highest train mean net return per $ among configs with >= 100 train positions; the
    config "passes train" only if its event-clustered 95% CI is above 0. Frozen and run once on test.

    uv run --no-sync python scripts/research/whale_pit_eval.py [--log-trials]
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import os
import subprocess
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from predict_ngin.costs.polymarket import polymarket_fee_rate
from predict_ngin.evaluation import deflated_sharpe, sharpe
from predict_ngin.shared.duck import connect, sql_path
from predict_ngin.shared.paths import data_root, repo_root

# Data end (exclusive). Default 2026-01-01 = the original study; WHALE_PIT_END=2026-10-17 runs the
# 2026 extension into its own folder (data/research/whale_pit/end_2026-10-17), leaving the original intact.
END = os.environ.get("WHALE_PIT_END", "2026-01-01")
DATA_END_TS = int(datetime.fromisoformat(END).replace(tzinfo=timezone.utc).timestamp())
_ORIG = END == "2026-01-01"
OUT = data_root() / "research" / "whale_pit" / ("" if _ORIG else f"end_{END}")
TRAIN = (pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2025-01-01", tz="UTC"))
# Original: test 2025. Extension (pre-registered 2026-10-17): the rule frozen on 2024, run once on
# 2026-01-01 .. END with the same stresses, baselines and checks; 2025 is not looked at again.
TEST = ((pd.Timestamp("2025-01-01", tz="UTC"), pd.Timestamp("2026-01-01", tz="UTC")) if _ORIG
        else (pd.Timestamp("2026-01-01", tz="UTC"), pd.Timestamp(END, tz="UTC")))
FROZEN = dict(score="mk", x=25_000, theta=0.02, exit="res")  # the 2026-10-17 train pick
U0, N_MIN = 20_000.0, 20
GRID = dict(score=["mk", "res"], x=[1_000, 5_000, 25_000], theta=[0.0, 0.02, 0.05, 0.10], exit=["res", "24h"])
RNG = np.random.default_rng(17)


def load() -> pd.DataFrame:
    con = connect(memory="2GB", threads=2)
    s = con.sql(f"SELECT * FROM read_parquet({sql_path(OUT / 'signals.parquet')}) "
                "ORDER BY ts, market_id, w, d, usd").df()
    s["t"] = pd.to_datetime(s["ts"], unit="s", utc=True)
    s["day"] = s["t"].dt.floor("D")
    for k in ("mk", "res"):
        s[f"{k}_n"] = s[f"{k}_n"].fillna(0)
        s[f"S_{k}"] = (s[f"{k}_p"].fillna(0) / (s[f"{k}_u"].fillna(0) + U0))
    s["vol24"] = (s["cum_now"].fillna(0) - s["cum_24h_ago"].fillna(0)).clip(lower=0)
    s["fee_rate"] = [polymarket_fee_rate(c) for c in s["category"]]
    return s


def follow(s: pd.DataFrame, side: np.ndarray | None = None, delay: int = 60, slip: float = 0.0,
           exit_: str = "res", fees: bool = True, at_whale: bool = False) -> pd.DataFrame:
    """Net return per $ of following each row (side +1 buys YES, -1 buys NO; default the whale's)."""
    d = s["d"].to_numpy() if side is None else side
    if at_whale:  # information content only: whale's own vwap, no delay, no costs
        p_ref, extra = s["p_vwap"].to_numpy(), np.zeros(len(s))
    else:
        p_ref = s[f"p_ref{delay}"].to_numpy()
        same = s[f"d_ref{delay}"].to_numpy() == d
        extra = np.where(same, 0.01, 0.02) + slip
    c0 = np.where(d == 1, p_ref, 1 - p_ref)
    c = c0 + extra
    rate = s["fee_rate"].to_numpy() if (fees and not at_whale) else np.zeros(len(s))
    fee_in = rate * c * (1 - c)
    if exit_ == "res":
        pay = s["yes_payoff"].to_numpy()
        mark = s["p_last"].to_numpy()
        v_yes = np.where(np.isnan(pay), mark, pay)
        v = np.where(d == 1, v_yes, 1 - v_yes) - np.where(np.isnan(pay), 0.02, 0.0)
        fee_out = np.zeros(len(s))
        exit_ts = np.minimum(s["last_fill_ts"].to_numpy(), TEST[1].timestamp())
    else:
        px = s[f"p_exit{delay}"].to_numpy()
        v = np.where(d == 1, px, 1 - px) - (0.0 if at_whale else 0.02)
        v = np.clip(v, 0, 1)
        fee_out = rate * v * (1 - v)
        exit_ts = s["ts"].to_numpy() + delay + 86400
    ok = (c0 >= 0.05) & (c0 <= 0.95) & (c < 0.99) & ~np.isnan(v)
    out = pd.DataFrame({"ret": (v - fee_out - c - fee_in) / (c + fee_in), "cost": c + fee_in,
                        "exit_ts": exit_ts, "ok": ok}, index=s.index)
    return out


def dedupe(s: pd.DataFrame, mask: np.ndarray) -> pd.DataFrame:
    x = s[mask]  # deterministic tie-break (DuckDB does not preserve row order)
    x = x.sort_values(["ts", "market_id", "w", "d", "usd"], kind="mergesort")
    return x.drop_duplicates(["market_id", "d", "day"])


def select(s, score, x, theta):
    return (s["usd"] >= x) & (s[f"{score}_n"] >= N_MIN) & (s[f"S_{score}"] >= theta)


def cluster_ci(ret: pd.Series, cluster: pd.Series, n_boot=2000, weights=None):
    w = pd.Series(1.0, index=ret.index) if weights is None else weights
    g = pd.DataFrame({"p": ret * w, "w": w, "c": cluster.values}).groupby("c")[["p", "w"]].sum()
    if len(g) < 2:
        return float("nan"), float("nan"), float("nan")
    P, W = g["p"].to_numpy(), g["w"].to_numpy()
    idx = RNG.integers(0, len(g), size=(n_boot, len(g)))
    bs = P[idx].sum(1) / W[idx].sum(1)
    return float(P.sum() / W.sum()), float(np.quantile(bs, 0.025)), float(np.quantile(bs, 0.975))


def evaluate(s, rows, **kw):
    f = follow(rows, **kw)
    f = f[f["ok"]]
    r = rows.loc[f.index]
    m, lo, hi = cluster_ci(f["ret"], r["event_id"])
    return dict(n=len(f), events=int(r["event_id"].nunique()), mean=m, lo=lo, hi=hi,
                hit=float((f["ret"] > 0).mean()) if len(f) else float("nan")), f


def portfolio(rows: pd.DataFrame, f: pd.DataFrame, capital: float, window, pos_frac=0.05):
    """Cash-limited book: stake = min(5% of capital, 10% of trailing-24h market $ volume, 50% of the
    whale's $); P&L booked on the exit day. Returns $/yr, daily Sharpe, avg deployed, and the
    uncapped capacity (sum of liquidity caps x mean return)."""
    r = rows.loc[f.index].assign(ret=f["ret"], exit_ts=f["exit_ts"])
    r = r.sort_values("ts")
    cap_liq = np.minimum(0.10 * r["vol24"], 0.5 * r["usd"])
    stake_cap = np.minimum(pos_frac * capital, cap_liq)
    cash, open_, pnl_by_day, dep = capital, [], {}, []
    days = pd.date_range(window[0], window[1] - pd.Timedelta(days=1), freq="D")
    for ts, st, ret, ex in zip(r["ts"], stake_cap, r["ret"], r["exit_ts"], strict=True):
        still = []
        for e_ts, e_st, e_pnl in open_:
            if e_ts <= ts:
                cash += e_st + e_pnl
                d = pd.Timestamp(e_ts, unit="s", tz="UTC").floor("D")
                pnl_by_day[d] = pnl_by_day.get(d, 0.0) + e_pnl
            else:
                still.append((e_ts, e_st, e_pnl))
        open_ = still
        stake = min(st, cash)
        if stake < 10:
            continue
        cash -= stake
        open_.append((ex, stake, stake * ret))
        dep.append((ts, ex, stake))
    for e_ts, _e_st, e_pnl in open_:
        d = min(pd.Timestamp(e_ts, unit="s", tz="UTC").floor("D"), days[-1])
        pnl_by_day[d] = pnl_by_day.get(d, 0.0) + e_pnl
    daily = pd.Series(pnl_by_day).reindex(days, fill_value=0.0)
    yrs = len(days) / 365.0
    # average deployed: stake-days / days
    sd = sum(st * (min(ex, window[1].timestamp()) - ts) / 86400 for ts, ex, st in dep)
    cap_total = float(cap_liq.sum())
    return dict(pnl_yr=float(daily.sum() / yrs), sharpe=sharpe(daily, 365), n_pos=len(dep),
                avg_deployed=sd / len(days), daily=daily,
                capacity_usd_yr=cap_total / yrs,
                capacity_pnl_yr=float((cap_liq * r["ret"]).sum() / yrs))


def in_window(s, w):
    return (s["t"] >= w[0]) & (s["t"] < w[1])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-trials", action="store_true")
    a = ap.parse_args()
    s = load()
    tr, te = s[in_window(s, TRAIN)], s[in_window(s, TEST)]
    res = {"grid": []}

    # ---------------------------------------------------------------- train grid
    for score, x, theta, ex in itertools.product(*GRID.values()):
        rows = dedupe(tr, select(tr, score, x, theta).to_numpy())
        st, f = evaluate(tr, rows, exit_=ex)
        pf = portfolio(rows, f, 5000, TRAIN) if st["n"] else {"sharpe": 0.0, "pnl_yr": 0.0}
        res["grid"].append(dict(score=score, x=x, theta=theta, exit=ex, **st,
                                train_sharpe=pf["sharpe"], train_pnl_yr_5k=pf["pnl_yr"]))
    g = pd.DataFrame(res["grid"])
    print(g.round(4).to_string())
    elig = g[g["n"] >= 100]
    best = elig.sort_values("mean", ascending=False).iloc[0].to_dict()
    best["passes_train"] = bool(best["lo"] > 0)
    res["selected"] = best
    pick = dict(score=best["score"], x=int(best["x"]), theta=float(best["theta"]), exit=best["exit"])
    if not _ORIG and pick != FROZEN:
        raise SystemExit(f"train re-selected {pick}, not the frozen rule {FROZEN}: stop, do not test")
    print("\nselected:", {k: best[k] for k in ("score", "x", "theta", "exit", "n", "mean", "lo", "hi", "passes_train")})

    # ---------------------------------------------------------------- frozen test
    sc, x, th, ex = best["score"], int(best["x"]), float(best["theta"]), best["exit"]
    sel_te = dedupe(te, select(te, sc, x, th).to_numpy())
    out = {}
    for name, kw in {
        "base_60s": {}, "delay_300s": dict(delay=300), "slip_+1c": dict(slip=0.01),
        "slip_+2c": dict(slip=0.02), "no_fees": dict(fees=False), "at_whale_price_gross": dict(at_whale=True),
    }.items():
        st, f = evaluate(te, sel_te, exit_=ex, **kw)
        out[name] = st
    res["test"] = out
    st, f = evaluate(te, sel_te, exit_=ex)
    for cap in (1000, 5000, 10000):
        pf = portfolio(sel_te, f, cap, TEST)
        out[f"book_{cap}"] = {k: v for k, v in pf.items() if k != "daily"}
        if cap == 5000:
            daily = pf["daily"]
    # DSR: trials = grid size; SR variance across trials from the train grid (annualised)
    n_trials = len(g) + 6
    sr_var = float(np.var(g["train_sharpe"].astype(float), ddof=1))
    dsr = deflated_sharpe(out["book_5000"]["sharpe"], n_trials, sr_var, len(daily),
                          float(daily.skew()), float(daily.kurt() + 3))
    res["dsr"] = dict(n_trials=n_trials, sr_var=sr_var, **dsr)

    # ---------------------------------------------------------------- baselines (test, same x / exit)
    base = {}
    pool = (te["usd"] >= x) & (te[f"{sc}_n"] >= N_MIN)
    for name, mask in {
        "all_large_trades": (te["usd"] >= x),
        "activity_matched_any_score": pool,
        "activity_matched_low_score": pool & (te[f"S_{sc}"] < th),
    }.items():
        base[name], _ = evaluate(te, dedupe(te, mask.to_numpy()), exit_=ex)
    # random wallets of the same activity: random subsets of the activity-matched pool, same size
    pool_rows = dedupe(te, pool.to_numpy())
    fp = follow(pool_rows, exit_=ex)
    fp = fp[fp["ok"]]
    k = min(out["base_60s"]["n"], len(fp))
    draws = [fp["ret"].sample(k, random_state=int(RNG.integers(1 << 30))).mean() for _ in range(500)] if k else [np.nan]
    base["random_activity_matched_draws"] = dict(n=k, mean=float(np.mean(draws)),
                                                 p2_5=float(np.quantile(draws, .025)),
                                                 p97_5=float(np.quantile(draws, .975)),
                                                 strategy_pctile=float(np.mean(np.array(draws) < st["mean"])))
    # same positions, other directions: price drift (last 24h) and favourite
    drift = np.sign(sel_te["p_ref60"] - sel_te["p_24h_ago"]).to_numpy()
    m = drift != 0
    base["price_drift_same_times"], _ = evaluate(te, sel_te[m], side=drift[m], exit_=ex) if m.any() else ({}, None)
    fav = np.where(sel_te["p_ref60"] >= 0.5, 1, -1)
    base["favourite_same_times"], _ = evaluate(te, sel_te, side=fav, exit_=ex)
    base["fade_whale_same_times"], _ = evaluate(te, sel_te, side=-sel_te["d"].to_numpy(), exit_=ex)
    res["baselines"] = base

    # ---------------------------------------------------------------- leakage checks
    leak = {}
    # L3 shuffle: permute (score, n) across pool signals within each month; 100 permutations
    perm = []
    pool_df = te[pool].copy()
    pool_df["mon"] = pool_df["t"].dt.strftime("%Y-%m")
    for _ in range(100):
        sh = pool_df.copy()
        sh[f"S_{sc}"] = sh.groupby("mon")[f"S_{sc}"].transform(lambda v: v.sample(frac=1, random_state=int(RNG.integers(1 << 30))).to_numpy())
        rows = dedupe(sh, (sh[f"S_{sc}"] >= th).to_numpy())
        f = follow(rows, exit_=ex)
        perm.append(float(f.loc[f["ok"], "ret"].mean()))
    leak["shuffle_scores_within_month"] = dict(mean=float(np.mean(perm)), p2_5=float(np.quantile(perm, .025)),
                                               p97_5=float(np.quantile(perm, .975)),
                                               strategy_pctile=float(np.mean(np.array(perm) < out["base_60s"]["mean"])))
    # L4 look-ahead canary: full-sample (future-inclusive) wallet score
    con = connect(memory="2GB", threads=2)
    full = con.sql(f"""SELECT w, sum(pnl) / (sum(usd) + {U0}) AS S_full, count(*) AS n_full
                       FROM read_parquet({sql_path(OUT / 'events.parquet')}) WHERE kind = '{sc}' GROUP BY 1""").df()
    te2 = te.merge(full, on="w", how="left")
    te2.index = te.index
    for nm, sel in {"canary_full_sample_score": (te2["usd"] >= x) & (te2["n_full"] >= N_MIN) & (te2["S_full"] >= th)}.items():
        leak[nm], _ = evaluate(te2, dedupe(te2, sel.to_numpy()), exit_=ex)
    res["leakage"] = leak

    print(json.dumps({k: v for k, v in res.items() if k != "grid"}, indent=1, default=float))
    (OUT / "results.json").write_text(json.dumps(res, indent=1, default=float))
    g.to_csv(OUT / "train_grid.csv", index=False)

    if a.log_trials:
        log_trials(g, best, out, res["dsr"], train_rows=_ORIG)
    return 0


def log_trials(g, best, out, dsr, train_rows=True):
    root = repo_root()
    try:
        commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=root, text=True).strip() + "-dirty"
    except Exception:
        commit = ""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for _, r in (g.iterrows() if train_rows else []):  # the extension re-uses the logged train grid
        rows.append([now, "whale_pit", f"{r['score']} x>={int(r['x'])} S>={r['theta']} exit={r['exit']}",
                     "PIT wallet skill score selects whale trades worth following",
                     json.dumps(dict(score=r["score"], x=int(r["x"]), theta=r["theta"], exit=r["exit"], n_min=N_MIN, u0=U0)),
                     "2024-01-01:2024-12-31", "train", round(float(r["train_sharpe"]), 3), "", "", "", int(r["n"]),
                     "discarded", commit, f"mean {r['mean']:.4f} CI[{r['lo']:.4f},{r['hi']:.4f}]"])
    t = out["base_60s"]
    rows.append([now, "whale_pit", f"SELECTED frozen {best['score']} x>={int(best['x'])} S>={best['theta']} exit={best['exit']}",
                 "PIT wallet skill score selects whale trades worth following", "{}",
                 f"{TEST[0]:%Y-%m-%d}:{TEST[1] - pd.Timedelta(days=1):%Y-%m-%d}", "oos",
                 round(out["book_5000"]["sharpe"], 3), "", "", "", t["n"], "discarded", commit,
                 f"test mean {t['mean']:.4f} CI[{t['lo']:.4f},{t['hi']:.4f}]; DSR {dsr['dsr']:.2f} N={dsr['n_trials']}"])
    with open(root / "research" / "trials.csv", "a", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
