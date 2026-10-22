#!/usr/bin/env python3
"""
Whale-following re-test: original fills vs realistic fills vs naive favourite baseline.

All runs share one prepared context (same point-in-time whale sets, same signals, same
price gates, same resolution handling) and the same small-account sizing:
capital $5,000, max position $250, position <= 5% of trailing-24h traded volume.

Runs
  original   : fill at the whale's own print, zero latency, net = gross * 0.97 (legacy)
  realistic  : next print >= 5s after the whale print, +1c half-spread, taker fee
  baseline   : same as realistic but signals = random eligible favourite print per market
               (no whale information), R random draws
  baseline_lg: as baseline but only prints >= $1,000 (controls for "a large trade printed")
  legacy_ref : original fills + legacy lifetime-volume Kelly sizing at $1M (reference only)

Whale alpha = per-position return on cost (net_pnl / position_size), whale realistic minus
baseline, with a market-clustered bootstrap CI, raw and entry-price-stratified.

Usage:
    python scripts/backtest/whale_retest.py --draws 200 --workers 8
"""

from __future__ import annotations

import argparse
import json
import sys
for _stream in (sys.stdout, sys.stderr):  # cp1252 consoles cannot print box/arrow glyphs
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
import time
from pathlib import Path

_root = Path(__file__).resolve().parent.parent.parent
for _p in (str(_root), str(_root / "src"), str(_root / "scripts" / "backtest")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np
import pandas as pd

from run_whale_category_backtest import prepare_whale_backtest, simulate_whale_backtest
from src.whale_strategy.execution_model import FillConfig, SizingConfig
from src.whale_strategy.favourite_baseline import build_favourite_baseline_signals
from src.whale_strategy.retest_stats import bootstrap_mean_diff_ci, deflated_sharpe_ratio
from src.whale_strategy.whale_config import load_whale_config
from src.whale_strategy.whale_scoring import WHALE_CRITERIA

# Trials behind the current configuration (conservative count of configs evaluated):
#   run_whale_sweep.py FULL_GRID           3*3*3*2         =  54
#   run_whale_full_sweep.py FULL_GRID      3*3*3*2*2*1*2   = 216
#   run_whale_category_backtest SENSITIVITY_GRIDS 8+4+5    =  17
#   per-category price gates hand-picked from backtests (7 categories x >=2 values) = 14
#   tune_parameters.py has no whale strategy space (momentum/smart_money only)  =   0
N_TRIALS = 54 + 216 + 17 + 14   # = 301

PRICE_BUCKETS = [0.0, 0.80, 0.85, 0.90, 0.95, 1.01]


def _bucket(x):
    return pd.cut(pd.Series(x, dtype=float), PRICE_BUCKETS, right=False, labels=False).to_numpy()


def summarize(name: str, res: dict, capital: float, n_trials: int) -> dict:
    if res.get("total_trades", 0) == 0 or "trades_df" not in res:
        return {"run": name, "trades": 0}
    df = res["trades_df"]
    dsr = deflated_sharpe_ratio(res["daily_returns"], n_trials=n_trials)
    dep = res["deployed_by_day"]
    ret = df["net_pnl"] / df["position_size"]
    return {
        "run": name,
        "trades": int(len(df)),
        "pnl": float(df["net_pnl"].sum()),
        "roc_pct": float(df["net_pnl"].sum() / capital * 100),
        "hit_rate": float((df["net_pnl"] > 0).mean()),
        "median_trade_pnl": float(df["net_pnl"].median()),
        "mean_ret_on_cost_pct": float(ret.mean() * 100),
        "fees": float(df.get("fees_usd", pd.Series(0.0)).sum()),
        "avg_pos": float(df["position_size"].mean()),
        "daily_sr": dsr["sr"],
        "ann_sr": dsr["sr_annual"],
        "skew": dsr["skew"],
        "kurt": dsr["kurt"],
        "n_days": dsr["n"],
        "psr": dsr["psr"],
        "sr0": dsr["sr0"],
        "dsr": dsr["dsr"],
        "avg_deployed": float(dep.mean()) if len(dep) else 0.0,
        "peak_deployed": float(dep.max()) if len(dep) else 0.0,
        "skips": res.get("skips", {}),
    }


def baseline_prints(ctx: dict) -> pd.DataFrame:
    """Test-period prints in the same market universe the whale signals are drawn from."""
    tp = ctx["test_prints"]
    liq = ctx["market_liquidity"]
    min_liq = ctx.get("min_market_liquidity") or WHALE_CRITERIA["min_market_liquidity"]
    mids = tp["market_id"].astype(str)
    ok = mids.map(lambda m: liq.get(m, 100_000)).astype(float) >= min_liq
    tp = tp[ok]
    # Same max time-to-resolution filter as filter_and_score_signals (scheduled end only)
    end = tp["market_id"].astype(str).map(ctx["scheduled_end_dates"])
    end = pd.to_datetime(end, errors="coerce")
    ttr = (end - tp["datetime"]).dt.days
    tp = tp[~(ttr > WHALE_CRITERIA["max_time_to_resolution"])]
    return tp.reset_index(drop=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--research-dir", type=Path, default=_root / "data" / "research")
    ap.add_argument("--resolutions-dir", type=Path, default=_root / "data" / "poly_cat")
    ap.add_argument("--capital", type=float, default=5_000.0)
    ap.add_argument("--max-position", type=float, default=250.0)
    ap.add_argument("--volume-cap-pct", type=float, default=0.05)
    ap.add_argument("--latency", type=float, default=5.0)
    ap.add_argument("--half-spread", type=float, default=0.01)
    ap.add_argument("--draws", type=int, default=200)
    ap.add_argument("--n-boot", type=int, default=5000)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--n-trials", type=int, default=N_TRIALS)
    ap.add_argument("--out-dir", type=Path, default=_root / "data" / "output" / "whale_retest")
    ap.add_argument("--skip-legacy", action="store_true")
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    cfg = load_whale_config()
    print(f"Preparing context ({cfg}) ...", flush=True)
    ctx = prepare_whale_backtest(
        research_dir=args.research_dir, whale_config=cfg, rebalance_freq="1W",
        n_workers=args.workers, extra_resolutions_dir=args.resolutions_dir,
        whale_cache_dir=_root / "cache" / "whale_sets",
    )
    if "error" in ctx:
        print("prepare error:", ctx)
        return 1
    print(f"  prepared in {time.time() - t0:.0f}s: {len(ctx['signals'])} candidate whale signals, "
          f"test {ctx['test_start']} -> {ctx['test_end']}", flush=True)

    sizing = SizingConfig.small_account(max_position_usd=args.max_position, volume_cap_pct=args.volume_cap_pct)
    realistic = FillConfig(latency_s=args.latency, half_spread=args.half_spread)
    original = FillConfig.original()
    cap = args.capital

    rows = []
    runs = {}
    for name, fc in (("original", original), ("realistic", realistic)):
        res = simulate_whale_backtest(ctx, capital=cap, fill_config=fc, sizing=sizing)
        runs[name] = res
        rows.append(summarize(name, res, cap, args.n_trials))
        print(f"  {name}: {rows[-1].get('trades')} trades, pnl {rows[-1].get('pnl', 0):.2f}", flush=True)
        if "trades_df" in res:
            res["trades_df"].to_csv(args.out_dir / f"{name}_positions.csv", index=False)

    # Capacity probes (realistic fills): larger max position / capital
    for mp, c in ((1_000.0, 5_000.0), (2_500.0, 25_000.0)):
        name = f"realistic_maxpos{int(mp)}_cap{int(c)}"
        res = simulate_whale_backtest(ctx, capital=c, fill_config=realistic,
                                      sizing=SizingConfig.small_account(max_position_usd=mp, capital=c,
                                                                        volume_cap_pct=args.volume_cap_pct))
        rows.append(summarize(name, res, c, args.n_trials))

    # Baselines
    bp = baseline_prints(ctx)
    whale_mkts = set(runs["realistic"]["trades_df"]["market_id"]) if "trades_df" in runs["realistic"] else set()
    print(f"  baseline universe: {bp['market_id'].nunique()} markets, {len(bp):,} prints; "
          f"whale traded {len(whale_mkts)} markets", flush=True)
    base_pos = {"baseline": [], "baseline_lg": [], "baseline_same_mkts": []}
    base_rows = {k: [] for k in base_pos}
    for d in range(args.draws):
        rng = np.random.default_rng(1000 + d)
        variants = {
            "baseline": dict(min_print_usd=0.0, allowed_markets=None),
            "baseline_lg": dict(min_print_usd=1_000.0, allowed_markets=None),
            "baseline_same_mkts": dict(min_print_usd=0.0, allowed_markets=whale_mkts),
        }
        for vname, kw in variants.items():
            sigs = build_favourite_baseline_signals(bp, ctx["cfg"], rng, one_per_market=True, **kw)
            res = simulate_whale_backtest(ctx, signals=sigs, capital=cap, fill_config=realistic,
                                          sizing=sizing, apply_whale_filter=False)
            s = summarize(vname, res, cap, 1)
            base_rows[vname].append(s)
            if "trades_df" in res:
                t = res["trades_df"].copy()
                t["draw"] = d
                base_pos[vname].append(t)
        if (d + 1) % 20 == 0:
            print(f"    baseline draws {d + 1}/{args.draws}", flush=True)

    # Deterministic "each eligible decision time" baseline: every eligible print is a signal
    # (first one opens, later ones layer up to the per-market max position).
    each_sigs = build_favourite_baseline_signals(bp, ctx["cfg"], np.random.default_rng(0), one_per_market=False)
    res_each = simulate_whale_backtest(ctx, signals=each_sigs, capital=cap, fill_config=realistic,
                                       sizing=sizing, apply_whale_filter=False)
    rows.append(summarize("baseline_each_print", res_each, cap, 1))
    if "trades_df" in res_each:
        base_pos["baseline_each_print"] = [res_each["trades_df"]]

    base_summary = {}
    for vname, lst in base_rows.items():
        b = pd.DataFrame([r for r in lst if r.get("trades", 0) > 0])
        if b.empty:
            continue
        base_summary[vname] = b
        agg = {k: float(b[k].mean()) for k in ("trades", "pnl", "roc_pct", "hit_rate", "median_trade_pnl",
                                                 "mean_ret_on_cost_pct", "fees", "avg_pos", "daily_sr", "ann_sr",
                                                 "skew", "kurt", "n_days", "psr", "avg_deployed", "peak_deployed")}
        agg["run"] = f"{vname} (mean of {len(b)} draws)"
        agg["pnl_p05"] = float(b["pnl"].quantile(0.05))
        agg["pnl_p95"] = float(b["pnl"].quantile(0.95))
        rows.append(agg)

    # Legacy reference: original fills + legacy sizing at $1M
    if not args.skip_legacy:
        res = simulate_whale_backtest(ctx, capital=1_000_000, fill_config=original, sizing=SizingConfig.legacy())
        rows.append(summarize("legacy_ref_$1M (orig fill+lifetime-vol Kelly)", res, 1_000_000, args.n_trials))

    # Whale alpha: per-position return on cost
    alpha = {}
    if "trades_df" in runs["realistic"]:
        w = runs["realistic"]["trades_df"]
        wr = (w["net_pnl"] / w["position_size"]).to_numpy()
        for vname, parts in base_pos.items():
            if not parts:
                continue
            b = pd.concat(parts, ignore_index=True)
            br = (b["net_pnl"] / b["position_size"]).to_numpy()
            raw = bootstrap_mean_diff_ci(wr, br, n_boot=args.n_boot, seed=7,
                                         a_groups=w["market_id"].to_numpy(), b_groups=b["market_id"].to_numpy())
            strat = bootstrap_mean_diff_ci(wr, br, n_boot=args.n_boot, seed=7,
                                           a_groups=w["market_id"].to_numpy(), b_groups=b["market_id"].to_numpy(),
                                           a_strata=_bucket(w["entry_token_price"]), b_strata=_bucket(b["entry_token_price"]))
            pnl_dist = base_summary[vname]["pnl"] if vname in base_summary else pd.Series(dtype=float)
            whale_pnl = float(w["net_pnl"].sum())
            alpha[vname] = {
                "raw": raw, "price_stratified": strat,
                "whale_pnl_percentile_in_baseline": float((pnl_dist < whale_pnl).mean()) if len(pnl_dist) else None,
            }

    # Entry-price diagnostics
    diag = {}
    for name in ("original", "realistic"):
        if "trades_df" in runs[name]:
            t = runs[name]["trades_df"]
            diag[name] = {
                "median_entry_token_price": float(t["entry_token_price"].median()),
                "by_reason": t["reason"].value_counts().to_dict(),
                "by_category": t.groupby("category")["net_pnl"].agg(["count", "sum"]).round(2).to_dict("index"),
            }
    if "trades_df" in runs["realistic"]:
        t = runs["realistic"]["trades_df"]
        tv = [ctx["tape"].trailing_volume(m, ts) for m, ts in zip(t["market_id"], pd.to_datetime(t["entry_ts"]))]
        tv = pd.Series(tv, dtype=float)
        diag["capacity"] = {
            "median_trailing24h_volume_at_entry": float(tv.median()),
            "median_volume_cap_usd": float(tv.median() * args.volume_cap_pct),
            "share_positions_volume_capped_below_max": float((tv * args.volume_cap_pct < args.max_position).mean()),
            "test_days": int(runs["realistic"]["deployed_by_day"].shape[0]),
            "positions_per_month": float(len(t) / max(runs["realistic"]["deployed_by_day"].shape[0] / 30.4, 1e-9)),
        }
    if "fills_df" in runs["original"] and "fills_df" in runs["realistic"]:
        o = runs["original"]["trades_df"].set_index("market_id")
        r = runs["realistic"]["trades_df"].set_index("market_id")
        common = o.index.intersection(r.index)
        if len(common):
            slip = (r.loc[common, "entry_token_price"].groupby(level=0).first()
                    - o.loc[common, "entry_token_price"].groupby(level=0).first())
            diag["entry_slippage_vs_whale_price"] = {
                "n": int(len(slip)), "mean_c": float(slip.mean() * 100), "median_c": float(slip.median() * 100)}

    out = {"rows": rows, "alpha": alpha, "diag": diag, "n_trials": args.n_trials,
           "test_start": str(ctx["test_start"]), "test_end": str(ctx["test_end"]),
           "n_whales": len(ctx["whales"]), "n_candidate_signals": len(ctx["signals"]),
           "baseline_markets": int(bp["market_id"].nunique()), "draws": args.draws,
           "elapsed_s": time.time() - t0}
    with open(args.out_dir / "whale_retest_results.json", "w") as f:
        json.dump(out, f, indent=2, default=str)
    pd.DataFrame([{k: v for k, v in r.items() if k != "skips"} for r in rows]).to_csv(
        args.out_dir / "whale_retest_summary.csv", index=False)
    print(json.dumps(out, indent=2, default=str)[:6000])
    ctx["price_store"].close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
