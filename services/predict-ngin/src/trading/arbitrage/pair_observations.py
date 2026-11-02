"""
Build hourly, polarity-aligned Kalshi/Polymarket observations for matched pairs,
plus per-pair resolution info, for ``resolution_arb.run_resolution_arb``.

Timing conventions (no look-ahead):
* Kalshi: per (ticker, UTC hour) the LAST trade price and its timestamp ``tk``
  (from trades with offset-corrected ``timestamp_unix``; see
  scripts/data/repair_kalshi_trades.py).
* Polymarket: the latest price at or before ``tk`` (as-of join) within
  ``pm_max_staleness_s``.
* Volumes: USD paid by takers in that same hour on each venue.

Resolution:
* Kalshi outcome from markets.parquet ``result``; if missing, inferred from the
  final trade price (>= 0.97 -> 1, <= 0.03 -> 0) and flagged.
* Polymarket outcome from ``outcome_prices`` (YES = first outcome).
* ``t_known``: first Kalshi trade after the last trade printed inside
  [0.03, 0.97] - when the outcome became effectively known. Used only for
  settlement timing / entry cut-off, never for trade direction or sizing.
"""

from __future__ import annotations

import ast
import glob
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

FAMILY_POLY_CATEGORY = {
    "fed": "economics",
    "election": "politics",
}


def poly_category_for_family(family: str) -> str:
    if family in FAMILY_POLY_CATEGORY:
        return FAMILY_POLY_CATEGORY[family]
    if family.startswith(("sports", "futures")):
        return "sports"
    return "other"


def extract_kalshi_trades(trades_glob, tickers, out_path: Path, source_sql: str | None = None) -> Path:
    """Copy trades for ``tickers`` (from corrected trades) into one parquet.

    ``source_sql`` (a DuckDB SELECT, e.g. ``predict_ngin.tapes.kalshi.tape_sql()``) replaces ``trades_glob``.
    """
    import duckdb

    con = duckdb.connect()
    con.execute("SET memory_limit='10GB'")
    con.register("kt", pd.DataFrame({"ticker": sorted(set(tickers))}))
    if source_sql:
        src = f"({source_sql})"
    else:
        g = trades_glob.replace("\\", "/")
        src = f"read_parquet('{g}', union_by_name=true)"
    o = str(out_path).replace("\\", "/")
    con.execute(
        f"""COPY (SELECT t.ticker, t.timestamp_unix AS ts, t.yes_price AS p, t.count AS n, t.taker_side AS side
                  FROM {src} t JOIN kt USING (ticker))
            TO '{o}' (FORMAT PARQUET)"""
    )
    con.close()
    return out_path


def extract_pm_prices(prices_glob: str, market_ids, out_path: Path) -> Path:
    import duckdb

    pm = pd.DataFrame({"market_id": sorted({str(x) for x in market_ids})})
    outs = []
    for f in sorted(glob.glob(prices_glob)):
        con = duckdb.connect()
        con.register("pm", pm)
        try:
            outs.append(con.sql(
                f"SELECT p.market_id, p.timestamp AS ts, p.price FROM '{f}' p JOIN pm USING (market_id) "
                f"WHERE outcome='YES'").df())
        except Exception:
            pass
        con.close()
    pd.concat(outs, ignore_index=True).to_parquet(out_path)
    return out_path


def extract_pm_hourly_volume(trades_glob: str, market_ids, out_path: Path) -> Path:
    import duckdb

    pmv = pd.DataFrame({"mid": sorted({float(x) for x in market_ids})})
    outs = []
    files = sorted(glob.glob(trades_glob))
    if not files:  # monthly extract deleted 2026-10-17 as a duplicate: same rows from trades.parquet
        try:
            from src.data_quality import legacy_pm_trades as legacy
        except ImportError:
            from data_quality import legacy_pm_trades as legacy
        base = Path(trades_glob).parent
        if legacy.available(base):
            files = [f"({legacy.trades_sql(['timestamp', 'market_id', 'usd_amount'], base=base)})"]
    for f in files:
        con = duckdb.connect()
        con.execute("SET TimeZone='UTC'")
        con.register("pmv", pmv)
        outs.append(con.sql(
            f"""SELECT CAST(CAST(t.market_id AS BIGINT) AS VARCHAR) market_id,
                       epoch(date_trunc('hour', CAST(t.timestamp AS TIMESTAMP)))::BIGINT // 3600 AS hr,
                       sum(usd_amount) usd
                FROM {f if f.startswith('(') else "'" + f + "'"} t JOIN pmv ON t.market_id = pmv.mid GROUP BY 1, 2""").df())
        con.close()
    pd.concat(outs, ignore_index=True).to_parquet(out_path)
    return out_path


def _parse_pm_outcome(s) -> float:
    try:
        v = [float(x) for x in ast.literal_eval(str(s))]
    except Exception:
        return np.nan
    if len(v) < 1:
        return np.nan
    if v[0] >= 0.99:
        return 1.0
    if v[0] <= 0.01:
        return 0.0
    return np.nan


def build_observations(
    pairs: pd.DataFrame,
    k_trades_path: Path,
    pm_prices: pd.DataFrame,
    pm_hourly_vol: Optional[pd.DataFrame],
    kalshi_markets: Optional[pd.DataFrame],
    pm_markets: Optional[pd.DataFrame],
    pm_max_staleness_s: int = 120,
    pm_vol_coverage_end_ts: Optional[int] = None,
    inplay_hours_before_known: float = 4.0,
) -> Dict[str, pd.DataFrame]:
    """Return {'obs': hourly observations, 'pairs': per-pair info}."""
    import duckdb

    kp = str(k_trades_path).replace("\\", "/")
    con = duckdb.connect()
    kh = con.sql(
        f"""SELECT ticker, ts // 3600 AS hr, arg_max(p, ts) AS k_price, max(ts) AS tk,
                   sum(n) AS k_contracts,
                   sum(n * CASE WHEN side = 'no' THEN 1 - p ELSE p END) AS k_usd_vol
            FROM '{kp}' GROUP BY 1, 2"""
    ).df()
    kstat = con.sql(
        f"""WITH t AS (SELECT ticker, ts, p FROM '{kp}'),
                 l AS (SELECT ticker, max(ts) AS lin FROM t WHERE p BETWEEN 0.03 AND 0.97 GROUP BY 1),
                 last AS (SELECT ticker, max(ts) AS k_last, arg_max(p, ts) AS k_final FROM t GROUP BY 1)
            SELECT last.ticker, last.k_last, last.k_final,
                   coalesce(min(t.ts) FILTER (WHERE t.ts > l.lin), max(l.lin)) AS t_known
            FROM last LEFT JOIN l USING (ticker) LEFT JOIN t ON t.ticker = last.ticker
            GROUP BY 1, 2, 3"""
    ).df()
    con.close()

    P = pairs.copy()
    P["pm_id"] = P["pm_id"].astype(str)
    P["pair_id"] = P["pm_id"] + "|" + P["k_ticker"]
    P = P.merge(kstat.rename(columns={"ticker": "k_ticker"}), on="k_ticker", how="left")

    # Kalshi outcome
    P["k_outcome"] = np.nan
    P["k_outcome_src"] = ""
    if kalshi_markets is not None and not kalshi_markets.empty:
        km = kalshi_markets[["ticker", "result"]].rename(columns={"ticker": "k_ticker"})
        P = P.merge(km, on="k_ticker", how="left")
        P.loc[P["result"] == "yes", "k_outcome"] = 1.0
        P.loc[P["result"] == "no", "k_outcome"] = 0.0
        P.loc[P["k_outcome"].notna(), "k_outcome_src"] = "markets"
        P = P.drop(columns=["result"])
    inf = P["k_outcome"].isna() & P["k_final"].notna()
    P.loc[inf & (P["k_final"] >= 0.97), ["k_outcome", "k_outcome_src"]] = [1.0, "inferred_last_trade"]
    P.loc[inf & (P["k_final"] <= 0.03), ["k_outcome", "k_outcome_src"]] = [0.0, "inferred_last_trade"]

    # Polymarket outcome
    P["pm_outcome"] = np.nan
    if pm_markets is not None and not pm_markets.empty:
        pmm = pm_markets[["id", "outcome_prices"]].rename(columns={"id": "pm_id"})
        pmm["pm_id"] = pmm["pm_id"].astype(str)
        P = P.merge(pmm, on="pm_id", how="left")
        P["pm_outcome"] = P["outcome_prices"].map(_parse_pm_outcome)
        P = P.drop(columns=["outcome_prices"])
    flip = ~P["pm_yes_is_k_yes"].astype(bool)
    P.loc[flip, "pm_outcome"] = 1.0 - P.loc[flip, "pm_outcome"]

    # Observations
    pp = pm_prices.rename(columns={"market_id": "pm_id", "ts": "tpm", "price": "pm_price"}).copy()
    pp["pm_id"] = pp["pm_id"].astype(str)
    pp = pp.sort_values("tpm")
    pp["tpm"] = pp["tpm"].astype("int64")
    pm_last = pp.groupby("pm_id").agg(pm_last_ts=("tpm", "max"),
                                      pm_last_price=("pm_price", "last")).reset_index()
    kh = kh.merge(P[["k_ticker", "pm_id", "pair_id", "family", "pm_yes_is_k_yes"]].rename(columns={"k_ticker": "ticker"}),
                  on="ticker")
    kh["tk"] = kh["tk"].astype("int64")
    kh = kh.sort_values("tk")
    obs = pd.merge_asof(kh, pp[["pm_id", "tpm", "pm_price"]], left_on="tk", right_on="tpm", by="pm_id",
                        direction="backward", tolerance=pm_max_staleness_s)
    obs = obs.dropna(subset=["pm_price"]).copy()
    flip = ~obs["pm_yes_is_k_yes"].astype(bool)
    obs.loc[flip, "pm_price"] = 1.0 - obs.loc[flip, "pm_price"]

    # PM hourly volume (coverage may end before the price data)
    obs["pm_usd_vol"] = np.nan
    obs["pm_vol_imputed"] = False
    if pm_hourly_vol is not None and not pm_hourly_vol.empty:
        pv = pm_hourly_vol.rename(columns={"market_id": "pm_id", "usd": "pm_usd_vol"})[["pm_id", "hr", "pm_usd_vol"]]
        pv["pm_id"] = pv["pm_id"].astype(str)
        pv["hr"] = pv["hr"].astype("int64")
        obs = obs.drop(columns=["pm_usd_vol"]).merge(pv, on=["pm_id", "hr"], how="left")
        cov_end = pm_vol_coverage_end_ts if pm_vol_coverage_end_ts is not None else int(pv["hr"].max() * 3600 + 3600)
        covered = obs["tk"] < cov_end
        obs.loc[covered & obs["pm_usd_vol"].isna(), "pm_usd_vol"] = 0.0  # no PM trade that hour
        obs["pm_vol_imputed"] = ~covered
        # Impute outside coverage using the family median PM/Kalshi volume ratio
        ratio = (obs.loc[covered, "pm_usd_vol"] / obs.loc[covered, "k_usd_vol"]).replace([np.inf, -np.inf], np.nan)
        rmed = obs.loc[covered].assign(r=ratio).groupby("family")["r"].median()
        imp = ~covered
        obs.loc[imp, "pm_usd_vol"] = obs.loc[imp, "k_usd_vol"] * obs.loc[imp, "family"].map(rmed).fillna(ratio.median())

    # Pair-level resolution timing / marks
    P = P.merge(pm_last, on="pm_id", how="left")
    P["k_last_price"] = P["k_final"]
    P["resolve_ts"] = P["t_known"].fillna(P["k_last"])
    if "pm_yes_is_k_yes" in P.columns:
        P.loc[~P["pm_yes_is_k_yes"].astype(bool), "pm_last_price"] = 1 - P["pm_last_price"]
    P["category"] = P["family"].map(poly_category_for_family)

    obs = obs.merge(P[["pair_id", "resolve_ts", "pm_end"]] if "pm_end" in P.columns else P[["pair_id", "resolve_ts"]],
                    on="pair_id", how="left")
    is_game = obs["family"].str.startswith("sports")
    pm_end_ts = pd.to_datetime(obs.get("pm_end"), utc=True, errors="coerce")
    pm_end_s = ((pm_end_ts - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(seconds=1)).astype("float")
    game_start = np.minimum(pm_end_s.fillna(np.inf), obs["resolve_ts"] - inplay_hours_before_known * 3600)
    obs["in_play"] = (is_game & (obs["tk"] >= game_start)) | (
        (obs["family"] == "election") & (obs["tk"] >= 1730851200))  # 2024-11-06 00:00Z
    obs = obs.rename(columns={"tk": "ts"})
    obs["month"] = pd.to_datetime(obs["ts"], unit="s", utc=True).dt.strftime("%Y-%m")
    cols = ["pair_id", "family", "ts", "hr", "month", "k_price", "pm_price", "k_contracts", "k_usd_vol",
            "pm_usd_vol", "pm_vol_imputed", "in_play", "tpm"]
    return {"obs": obs[cols].sort_values(["ts", "pair_id"]).reset_index(drop=True), "pairs": P}


def polarity_check(obs: pd.DataFrame, min_obs: int = 10) -> pd.DataFrame:
    """Per-pair correlation between Kalshi and Polymarket YES prices (negative => polarity bug)."""
    def _c(d):
        if len(d) < min_obs or d["k_price"].std() == 0 or d["pm_price"].std() == 0:
            return np.nan
        return d["k_price"].corr(d["pm_price"])
    return obs.groupby("pair_id")[["k_price", "pm_price"]].apply(_c).rename("corr").reset_index()
