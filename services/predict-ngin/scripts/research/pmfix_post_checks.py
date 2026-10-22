"""Post-build checks on data/polymarket_clean/trades (pmfix).

1. ts_chain - ts_local (the 2.000 s/block timestamp error) by month.
2. Do the monthly data/polymarket/trades/trades_YYYY-MM.parquet files carry true block times? (join on tx hash)
3. Residual YES jumps > 0.50 that are neither at an extreme nor after a > 6 h gap: examples.
4. Dune pm_pair_hourly (taker rows) vs local clean maker-row hourly YES prices: how often the Dune close / high /
   low disagree by > 0.20 (exposure to the Dune taker-row mislabel on mint/merge matches).

    venv/Scripts/python.exe scripts/research/pmfix_post_checks.py
Writes data/vendor/pmfix/post_*.csv and prints summaries.
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.data_quality import legacy_pm_trades  # noqa: E402  (monthly extract deleted 2026-10-17: served from trades.parquet)

CLEAN = (ROOT / "data" / "polymarket_clean" / "trades" / "*.parquet").as_posix()
VEN = ROOT / "data" / "vendor" / "pmfix"


def con():
    c = duckdb.connect()
    c.execute("SET enable_progress_bar=false")
    c.execute("SET memory_limit='6GB'")
    c.execute("SET threads=4")
    c.execute(f"SET temp_directory='{(VEN / 'duck_tmp').as_posix()}'")
    return c


def ts_error(c):
    q = f"""
    select strftime(to_timestamp(ts_chain), '%Y-%m') mon, count(*) n,
           avg(ts_chain - ts_local) mean_err_s, min(ts_chain - ts_local) min_err_s, max(ts_chain - ts_local) max_err_s,
           avg(case when timestamp_unreliable then 1.0 else 0.0 end) frac_unreliable
    from read_parquet('{CLEAN}') where ts_chain is not null and not isnan(ts_chain) group by 1 order by 1"""
    d = c.sql(q).df()
    d["unrel"] = c.sql(f"select avg(case when timestamp_unreliable then 1.0 else 0.0 end) from read_parquet('{CLEAN}')").fetchone()[0]
    d.to_csv(VEN / "post_ts_error_by_month.csv", index=False)
    print(d.to_string())


def monthly_ts(c):
    out = []
    for m in ("2023-06", "2024-06", "2024-11", "2025-03", "2025-09"):
        f = (ROOT / "data" / "polymarket" / "trades" / f"trades_{m}.parquet").as_posix()
        y, mm = map(int, m.split("-"))
        nxt = f"{y + (mm == 12):04d}-{mm % 12 + 1:02d}-01"
        src = (f"read_parquet('{f}')" if Path(f).exists() else
               f"({legacy_pm_trades.trades_sql(['transactionHash', 'timestamp'], f'{m}-01', nxt + ' 00:00:00')} "
               f"AND timestamp < epoch(TIMESTAMP '{nxt}'))")
        q = f"""
        with mo as (select distinct substr(transactionHash, 3) h, epoch(cast(timestamp as timestamp)) ts
                    from {src} using sample 20000 rows),
             cl as (select transaction_hash h, min(ts_chain) ts_chain, min(ts_local) ts_local
                    from read_parquet('{CLEAN}') where transaction_hash in (select h from mo) group by 1)
        select '{m}' mon, count(*) n, avg(abs(mo.ts - cl.ts_chain)) mae_vs_chain, max(abs(mo.ts - cl.ts_chain)) max_vs_chain,
               avg(abs(mo.ts - cl.ts_local)) mae_vs_local
        from mo join cl using (h)"""
        out.append(c.sql(q).df())
    d = pd.concat(out)
    d.to_csv(VEN / "post_monthly_files_ts.csv", index=False)
    print(d.to_string())


def residual_jumps(c):
    q = f"""
    with s as (select * from read_parquet('{CLEAN}') where not is_taker_order_row and yes_price is not null
               and market_id in (select market_id from read_parquet('{CLEAN}') using sample 3000 rows)),
    w as (select market_id, block_number, coalesce(ts_chain, ts_local) ts, yes_price,
            lag(yes_price) over (partition by market_id order by block_number) p0,
            lag(coalesce(ts_chain, ts_local)) over (partition by market_id order by block_number) t0,
            lag(block_number) over (partition by market_id order by block_number) b0
          from s)
    select market_id, block_number, b0, ts - t0 dt_s, p0, yes_price from w
    where abs(yes_price - p0) > 0.5 and least(yes_price, p0) > 0.02 and greatest(yes_price, p0) < 0.98 and ts - t0 <= 21600
    order by random() limit 25"""
    d = c.sql(q).df()
    m = c.sql(f"select id market_id, question from '{(ROOT / 'data/polymarket/markets.parquet').as_posix()}'").df()
    d = d.merge(m, on="market_id", how="left")
    d.to_csv(VEN / "post_residual_jumps_sample.csv", index=False)
    print(d.to_string())


def dune_vs_clean(c):
    dune = (ROOT / "data" / "vendor" / "dune" / "pm_pair_hourly.parquet").as_posix()
    q = f"""
    with loc as (
      select condition_id, cast(floor(ts_chain / 3600) * 3600 as bigint) hr,
             max_by(yes_price, block_number) c_loc, max(yes_price) h_loc, min(yes_price) l_loc, count(*) n_loc
      from read_parquet('{CLEAN}')
      where not is_taker_order_row and yes_price is not null and ts_chain is not null and not isnan(ts_chain)
        and condition_id in (select distinct '0x' || condition_id from '{dune}')
      group by 1, 2)
    select d.condition_id, d.hr, d.c, d.h, d.l, d.n, loc.c_loc, loc.h_loc, loc.l_loc, loc.n_loc
    from '{dune}' d join loc on loc.condition_id = '0x' || d.condition_id and loc.hr = cast(epoch(d.hr_ts) as bigint)"""
    d = c.sql(q).df()
    res = {
        "n_hours_matched": len(d),
        "frac_close_diff_gt_020": float(((d.c - d.c_loc).abs() > 0.20).mean()),
        "frac_close_diff_gt_005": float(((d.c - d.c_loc).abs() > 0.05).mean()),
        "frac_high_above_local_max_gt_020": float(((d.h - d.h_loc) > 0.20).mean()),
        "frac_low_below_local_min_gt_020": float(((d.l_loc - d.l) > 0.20).mean()),
        "median_abs_close_diff": float((d.c - d.c_loc).abs().median()),
    }
    pd.DataFrame([res]).to_csv(VEN / "post_dune_vs_clean.csv", index=False)
    print(res)


if __name__ == "__main__":
    c = con()
    import sys
    names = sys.argv[1:] or ["ts_error", "monthly_ts", "residual_jumps", "dune_vs_clean"]
    for f in [globals()[n] for n in names]:
        try:
            f(c)
        except Exception as e:  # noqa: BLE001
            print(f.__name__, "FAILED", repr(e)[:300])
