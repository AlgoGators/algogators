"""BEFORE/AFTER token-mixing and >0.50 consecutive-jump rates for every Polymarket price source on disk.

For each source a series is (market, time-ordered prints). "raw" = the stored ``price`` column read naively
(what an unpatched reader sees); "yp" = the YES-equivalent price under the rule in
src/data_quality/polymarket_tokens.py (outcome index 0 = clobTokenIds[0]; index 1 -> 1 - p). The SQL here
mirrors the helper's mapping so multi-GB files can be scanned; the helper itself is cross-checked on samples
(tests/unit/test_pmfix_tokens.py, pmfix_build_clean.py).

Residual YES jumps are split into: at an extreme (one side <= 0.02 or >= 0.98: resolution / settlement
prints), after a > 6 h gap in the tape, and other.

    venv/Scripts/python.exe scripts/research/pmfix_measure.py [source ...]
Writes data/vendor/pmfix/rates_<source>.csv (one row per file/category) and prints them.
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
# sources deleted on 2026-10-17 as duplicates are skipped with a note (their rows live on in trades.parquet,
# measured as its own source, and in the clean tape); see docs/DATA_LAKE.md "Removed datasets"
DELETED = "deleted 2026-10-17 as a duplicate; see data/polymarket/trades.parquet and the clean tape"

OUT = ROOT / "data" / "vendor" / "pmfix"
D = ROOT / "data"
CATS = ["Art_and_Culture", "Climate_and_Science", "Economy", "Finance", "Geopolitics", "Politics", "Sports", "Tech"]


def con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect()
    c.execute("SET enable_progress_bar=false")
    c.execute("SET memory_limit='6GB'")
    c.execute("SET threads=4")
    c.execute(f"SET temp_directory='{(OUT / 'duck_tmp').as_posix()}'")
    return c


def stats(c, base_sql: str) -> dict:
    """``base_sql`` must yield columns g (market), ts (seconds), rn (tiebreak), raw, yp, oi (0/1/NULL)."""
    q = f"""
    with b as ({base_sql}),
    b2 as (select * from b where raw is not null),
    w as (
      select g, ts, raw, yp, oi,
        lag(raw) over win as praw, lag(yp) over win as pyp, lag(ts) over win as pts
      from b2 window win as (partition by g order by ts, rn)
    ),
    per_g as (
      select g, count(distinct oi) k,
        bool_or(abs(raw - praw) > 0.5) raw_j, bool_or(abs(yp - pyp) > 0.5) yes_j
      from w group by g
    )
    select
      (select count(*) from w) n_rows,
      (select count(*) from per_g) n_markets,
      (select count(praw) from w) n_pairs,
      (select avg(case when k > 1 then 1.0 else 0.0 end) from per_g) frac_markets_mixed,
      (select avg(case when oi = 1 then 1.0 when oi = 0 then 0.0 end) from w) frac_rows_outcome1,
      (select avg(case when oi is null then 1.0 else 0.0 end) from w) frac_rows_unknown_token,
      (select sum(case when abs(raw - praw) > 0.5 then 1 else 0 end) from w) raw_jumps,
      (select sum(case when abs(yp - pyp) > 0.5 then 1 else 0 end) from w) yes_jumps,
      (select sum(case when abs(yp - pyp) > 0.5 and (least(yp, pyp) <= 0.02 or greatest(yp, pyp) >= 0.98)
                  then 1 else 0 end) from w) yes_jumps_extreme,
      (select sum(case when abs(yp - pyp) > 0.5 and not (least(yp, pyp) <= 0.02 or greatest(yp, pyp) >= 0.98)
                  and ts - pts > 21600 then 1 else 0 end) from w) yes_jumps_gap6h,
      (select avg(case when raw_j then 1.0 else 0.0 end) from per_g) frac_markets_raw_jump,
      (select avg(case when yes_j then 1.0 else 0.0 end) from per_g) frac_markets_yes_jump
    """
    r = c.sql(q).df().iloc[0].to_dict()
    n = r["n_pairs"] or 0
    r["raw_frac_jumps"] = r["raw_jumps"] / n if n else float("nan")
    r["yes_frac_jumps"] = r["yes_jumps"] / n if n else float("nan")
    r["yes_frac_jumps_other"] = (r["yes_jumps"] - r["yes_jumps_extreme"] - r["yes_jumps_gap6h"]) / n if n else float("nan")
    return r


# ----------------------------------------------------------------------------- source definitions


def src_research_trades(c):
    """data/research/<cat>/trades.parquet - Data-API generation (outcomeIndex). Gen-1 rows have timestamp 0."""
    out = []
    for cat in CATS:
        f = (D / "research" / cat / "trades.parquet").as_posix()
        base = (f"select conditionId g, timestamp ts, file_row_number rn, price raw, "
                f"case when outcomeIndex = 0 then price when outcomeIndex = 1 then 1 - price end yp, "
                f"cast(outcomeIndex as int) oi from read_parquet('{f}', file_row_number=true) "
                f"where timestamp > 0 and conditionId is not null")
        r = stats(c, base)
        r["gen1_rows_ts0"] = c.sql(f"select count(*) from '{f}' where timestamp <= 0").fetchone()[0]
        out.append({"source": "research_trades", "file": cat, **r})
    return out


def src_pmxt_ticks(c):
    out = []
    for cat in CATS:
        f = (D / "pmxt" / "ticks" / f"{cat}_trades.parquet").as_posix()
        g = (D / "research" / cat / "trades.parquet").as_posix()
        same = c.sql(f"select (select count(*) from '{f}') = (select count(*) from '{g}') and "
                     f"(select sum(hash(transactionHash || coalesce(cast(price as varchar),''))) from '{f}') = "
                     f"(select sum(hash(transactionHash || coalesce(cast(price as varchar),''))) from '{g}')").fetchone()[0]
        base = (f"select conditionId g, timestamp ts, file_row_number rn, price raw, "
                f"case when outcomeIndex = 0 then price when outcomeIndex = 1 then 1 - price end yp, "
                f"cast(outcomeIndex as int) oi from read_parquet('{f}', file_row_number=true) "
                f"where timestamp > 0 and conditionId is not null")
        out.append({"source": "pmxt_ticks", "file": cat, "identical_to_research_trades": bool(same), **stats(c, base)})
    return out


SAMPLE_MOD = 4


def _price_history(c, label, files):
    out = []
    for name, f in files:
        try:
            n = c.sql(f"select count(*) from read_parquet('{f}')").fetchone()[0] if "*" not in f else 0
        except Exception:  # noqa: BLE001  (corrupt file: reported by stats below)
            n = 0
        samp = f"where hash(market_id) % {SAMPLE_MOD} = 0" if n > 20_000_000 else ""
        base = (f"select market_id g, timestamp ts, file_row_number rn, price raw, "
                f"case when upper(outcome) = 'YES' then price when upper(outcome) = 'NO' then 1 - price end yp, "
                f"case when upper(outcome) = 'YES' then 0 when upper(outcome) = 'NO' then 1 end oi "
                f"from read_parquet('{f}', file_row_number=true) {samp}")
        try:
            r = stats(c, base)
        except Exception as e:  # noqa: BLE001
            r = {"error": repr(e)[:200]}
        out.append({"source": label, "file": name, "market_sample": f"1/{SAMPLE_MOD}" if samp else "all", **r})
    return out


def src_research_prices(c):
    files = [(cat, (D / "research" / cat / "prices.parquet").as_posix()) for cat in CATS
             if (D / "research" / cat / "prices.parquet").exists()]
    return _price_history(c, "research_prices", files)


def src_pmxt_ohlcv(c):
    return _price_history(c, "pmxt_ohlcv", [("ohlcv_all", (D / "pmxt" / "ohlcv" / "ohlcv_all.parquet").as_posix())])


def src_polymarket_prices(c):
    files = [(p.stem, p.as_posix()) for p in sorted((D / "polymarket" / "prices").glob("prices_*.parquet"))]
    return _price_history(c, "polymarket_prices_monthly", files)


def src_by_category_prices(c):
    files = [(p.name, (p / "prices_*.parquet").as_posix()) for p in sorted((D / "by_category").iterdir())
             if p.is_dir() and any(p.glob("prices_*.parquet"))]
    return _price_history(c, "by_category_prices", files)


TS = "coalesce(try_cast(timestamp as double), epoch(try_cast(timestamp as timestamp)))"


def _onchain_processed(c, label, files):
    out = []
    for name, f in files:
        base = (f"select market_id g, {TS} ts, file_row_number rn, cast(price as double) raw, "
                f"case when nonusdc_side = 'token1' then price when nonusdc_side = 'token2' then 1 - price end yp, "
                f"case when nonusdc_side = 'token1' then 0 when nonusdc_side = 'token2' then 1 end oi "
                f"from read_parquet('{f}', file_row_number=true, union_by_name=true) "
                f"where {TS} > 0")
        try:
            r = stats(c, base)
            r["rows_ts0"] = c.sql(f"select count(*) from read_parquet('{f}', union_by_name=true) "
                                  f"where coalesce({TS}, 0) <= 0").fetchone()[0]
        except Exception as e:  # noqa: BLE001
            r = {"error": repr(e)[:300]}
        out.append({"source": label, "file": name, **r})
    return out


def src_polymarket_trades_monthly(c):
    files = [(p.stem, p.as_posix()) for p in sorted((D / "polymarket" / "trades").glob("trades_*.parquet"))]
    if not files:
        return [{"source": "polymarket_trades_monthly", "skipped": DELETED}]
    return _onchain_processed(c, "polymarket_trades_monthly", files)


def src_by_category_trades(c):
    files = [(p.name, (p / "trades_*.parquet").as_posix()) for p in sorted((D / "by_category").iterdir())
             if p.is_dir() and any(p.glob("trades_*.parquet"))]
    if not files:
        return [{"source": "by_category_trades", "skipped": DELETED}]
    return _onchain_processed(c, "by_category_trades", files)


def src_tardis(c):
    tm = (D / "vendor" / "tardis" / "token_map.parquet").as_posix()
    out = []
    for p in sorted((D / "vendor" / "tardis" / "trades").glob("*.parquet")):
        f = p.as_posix()
        # per-market series (what a reader grouping by market_id sees) vs YES-normalised
        base = (f"select m.market_id g, t.timestamp / 1e6 ts, t.file_row_number rn, t.price raw, "
                f"case when m.outcome_idx = 0 then t.price when m.outcome_idx = 1 then 1 - t.price end yp, "
                f"m.outcome_idx oi from read_parquet('{f}', file_row_number=true) t "
                f"join '{tm}' m on m.symbol = cast(t.symbol as varchar)")
        r = stats(c, base)
        per_token = stats(c, f"select cast(symbol as varchar) g, timestamp / 1e6 ts, file_row_number rn, price raw, "
                             f"price yp, 0 oi from read_parquet('{f}', file_row_number=true)")
        r["per_token_raw_frac_jumps"] = per_token["raw_frac_jumps"]
        out.append({"source": "tardis_trades", "file": p.stem, **r})
    return out


def src_dune_pair_hourly(c):
    f = (D / "vendor" / "dune" / "pm_pair_hourly.parquet").as_posix()
    base = f"select condition_id g, hr ts, 0 rn, c raw, c yp, 0 oi from '{f}'"
    return [{"source": "dune_pm_pair_hourly", "file": "pm_pair_hourly", **stats(c, base)}]


def src_users_sample(c):
    """users.parquet has no token identity at all (market, user, role, price) - raw only, 4 row groups."""
    if not (D / "polymarket" / "users.parquet").exists():
        return [{"source": "users_parquet_sample", "skipped": DELETED}]
    f = (D / "polymarket" / "users.parquet").as_posix()
    base = (f"select market_id g, timestamp ts, file_row_number rn, price raw, cast(null as double) yp, "
            f"cast(null as int) oi from read_parquet('{f}', file_row_number=true) "
            f"where file_row_number < 4000000 and role = 'taker'")
    return [{"source": "users_parquet_sample", "file": "first_4M_rows_takers", **stats(c, base)}]


SOURCES = {
    "research_trades": src_research_trades,
    "pmxt_ticks": src_pmxt_ticks,
    "research_prices": src_research_prices,
    "pmxt_ohlcv": src_pmxt_ohlcv,
    "polymarket_prices": src_polymarket_prices,
    "by_category_prices": src_by_category_prices,
    "polymarket_trades_monthly": src_polymarket_trades_monthly,
    "by_category_trades": src_by_category_trades,
    "tardis": src_tardis,
    "dune_pair_hourly": src_dune_pair_hourly,
    "users_sample": src_users_sample,
}


def main(argv: list[str]) -> None:
    names = argv or list(SOURCES)
    (OUT / "duck_tmp").mkdir(parents=True, exist_ok=True)
    c = con()
    for n in names:
        rows = SOURCES[n](c)
        df = pd.DataFrame(rows)
        df.to_csv(OUT / f"rates_{n}.csv", index=False)
        cols = [x for x in ("source", "file", "n_rows", "n_markets", "frac_markets_mixed", "frac_rows_outcome1",
                            "raw_frac_jumps", "frac_markets_raw_jump", "yes_frac_jumps", "yes_frac_jumps_other",
                            "frac_markets_yes_jump", "error") if x in df.columns]
        with pd.option_context("display.width", 250, "display.max_columns", 30):
            print(df[cols].to_string(), flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
