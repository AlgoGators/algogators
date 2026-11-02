"""Build YES-normalised, timestamp-repaired derived datasets under data/polymarket_clean/ (raw files untouched).

Targets (``venv/Scripts/python.exe scripts/research/pmfix_build_clean.py <target> ...``):

* ``trades``   data/polymarket/trades.parquet (293M on-chain OrderFilled rows) ->
               data/polymarket_clean/trades/part-NNNN.parquet, streamed row group by row group, plus
               BEFORE/AFTER stats in data/vendor/pmfix/rates_trades_parquet.csv (consecutive prints per market,
               ordered by block then file order, carried across row groups).
* ``research`` data/research/<cat>/trades.parquet (Data-API ticks; identical to data/pmxt/ticks) ->
               data/polymarket_clean/research_trades/<cat>.parquet (rows with a real timestamp only).
* ``prices``   data/polymarket/prices/prices_YYYY-MM.parquet (CLOB price history, one row per token per minute) ->
               data/polymarket_clean/prices/prices_YYYY-MM.parquet: one YES price per (market_id, timestamp).
               prices_2025-03.parquet is corrupt (no parquet footer) and is skipped/logged.
* ``small``    data/research/<cat>/prices.parquet, data/pmxt/ohlcv/ohlcv_all.parquet and
               data/vendor/tardis/trades/*.parquet -> data/polymarket_clean/{research_prices,pmxt_ohlcv,tardis_trades}/.

Output columns for trades (``trades`` target):
    block_number, ts_local (the file's 2.000 s/block timestamp), ts_chain (true block time, float seconds,
    NaN if outside anchors), timestamp_unreliable, transaction_hash, order_hash, market_id, condition_id,
    token_id, outcome_index, outcome0_label, price_raw, yes_price, yes_side (aggressor side in YES terms),
    is_taker_order_row (drop for volume), usd_amount, token_amount, maker, taker, token_conflict.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.data_quality.polygon_blocks import block_to_ts, load_anchors  # noqa: E402
from src.data_quality.polymarket_tokens import TokenMap, normalize_trades  # noqa: E402

D = ROOT / "data"
CLEAN = D / "polymarket_clean"
VEN = D / "vendor" / "pmfix"
CATS = ["Art_and_Culture", "Climate_and_Science", "Economy", "Finance", "Geopolitics", "Politics", "Sports", "Tech"]


# ------------------------------------------------------------------------------------------- trades.parquet


class JumpTracker:
    """Consecutive-print jump counter per market, carried across chunks."""

    def __init__(self, name: str):
        self.name = name
        self.last: dict = {}  # market -> (raw, yes, ts)
        self.pairs = self.raw_j = self.yes_j = self.yes_ext = self.yes_gap = 0
        self.rows = 0
        self.mkt_mask: dict = {}  # market -> bitmask of outcome indices seen
        self.mkt_raw_j: set = set()
        self.mkt_yes_j: set = set()

    def update(self, g: np.ndarray, raw: np.ndarray, yes: np.ndarray, ts: np.ndarray, oi: np.ndarray) -> None:
        ok = ~np.isnan(yes)
        g, raw, yes, ts, oi = g[ok], raw[ok], yes[ok], ts[ok], oi[ok]
        if not len(g):
            return
        self.rows += len(g)
        # rows arrive already sorted by (g, block, file order)
        first = np.r_[True, g[1:] != g[:-1]]
        prev_raw = np.r_[np.nan, raw[:-1]]
        prev_yes = np.r_[np.nan, yes[:-1]]
        prev_ts = np.r_[np.nan, ts[:-1]]
        for i in np.flatnonzero(first):
            c = self.last.get(g[i])
            if c is None:
                prev_raw[i] = prev_yes[i] = prev_ts[i] = np.nan
            else:
                prev_raw[i], prev_yes[i], prev_ts[i] = c
        last_idx = np.r_[np.flatnonzero(first)[1:] - 1, len(g) - 1]
        for i in last_idx:
            self.last[g[i]] = (raw[i], yes[i], ts[i])
        has = ~np.isnan(prev_raw)
        self.pairs += int(has.sum())
        rj = has & (np.abs(raw - prev_raw) > 0.5)
        yj = has & (np.abs(yes - prev_yes) > 0.5)
        ext = yj & ((np.minimum(yes, prev_yes) <= 0.02) | (np.maximum(yes, prev_yes) >= 0.98))
        gap = yj & ~ext & ((ts - prev_ts) > 21600)
        self.raw_j += int(rj.sum())
        self.yes_j += int(yj.sum())
        self.yes_ext += int(ext.sum())
        self.yes_gap += int(gap.sum())
        self.mkt_raw_j.update(g[rj].tolist())
        self.mkt_yes_j.update(g[yj].tolist())
        bits = pd.Series(np.left_shift(1, oi.astype(int))).groupby(g).agg(lambda s: int(np.bitwise_or.reduce(s.to_numpy())))
        for k, v in bits.items():
            self.mkt_mask[k] = self.mkt_mask.get(k, 0) | v

    def result(self) -> dict:
        n = max(self.pairs, 1)
        nm = len(self.mkt_mask)
        return {
            "source": "trades_parquet", "file": self.name, "n_rows": self.rows, "n_markets": nm, "n_pairs": self.pairs,
            "frac_markets_mixed": sum(1 for v in self.mkt_mask.values() if v == 3) / max(nm, 1),
            "raw_jumps": self.raw_j, "yes_jumps": self.yes_j, "yes_jumps_extreme": self.yes_ext,
            "yes_jumps_gap6h": self.yes_gap, "raw_frac_jumps": self.raw_j / n, "yes_frac_jumps": self.yes_j / n,
            "yes_frac_jumps_other": (self.yes_j - self.yes_ext - self.yes_gap) / n,
            "frac_markets_raw_jump": len(self.mkt_raw_j) / max(nm, 1),
            "frac_markets_yes_jump": len(self.mkt_yes_j) / max(nm, 1),
        }


TRADE_COLS = ["timestamp", "block_number", "transaction_hash", "market_id", "condition_id", "nonusdc_side",
              "maker", "taker", "maker_asset", "taker_asset", "maker_direction", "taker_direction", "price",
              "usd_amount", "token_amount", "asset_id", "order_hash"]


def build_trades(max_groups: int | None = None) -> None:
    src = pq.ParquetFile(D / "polymarket" / "trades.parquet")
    out_dir = CLEAN / "trades"
    out_dir.mkdir(parents=True, exist_ok=True)
    tm = TokenMap.load_default()
    o0 = tm.frame[tm.frame.outcome_index == 0].set_index("condition_id")["outcome_label"]
    anchors = load_anchors()
    trk_all, trk_dedup = JumpTracker("all_rows"), JumpTracker("excl_taker_order_rows")
    ts_err = []
    writer, part, rows_in_part = None, 0, 0
    n_groups = src.metadata.num_row_groups if max_groups is None else min(max_groups, src.metadata.num_row_groups)
    t0 = time.time()
    for gi in range(n_groups):
        df = src.read_row_group(gi, columns=TRADE_COLS).to_pandas()
        df["_rn"] = np.arange(len(df))
        n = normalize_trades(df, tm, schema="onchain")
        ts_chain = block_to_ts(n["block_number"].to_numpy(), anchors)
        out = pd.DataFrame({
            "block_number": n["block_number"].astype("int64"),
            "ts_local": n["timestamp"].astype("int64"),
            "ts_chain": ts_chain,
            "timestamp_unreliable": np.isnan(ts_chain),
            "transaction_hash": n["transaction_hash"], "order_hash": n["order_hash"],
            "market_id": n["market_id"], "condition_id": n["condition_id"],
            "token_id": n["token_id"].astype(str).where(n["token_id"].notna(), None),
            "outcome_index": n["outcome_index"].astype("float32"),
            "outcome0_label": n["condition_id"].map(o0),
            "price_raw": n["price"].astype("float64"), "yes_price": n["yes_price"].astype("float64"),
            "yes_side": n["yes_side"], "is_taker_order_row": n["is_taker_order_row"].astype(bool),
            "usd_amount": n["usd_amount"], "token_amount": n["token_amount"],
            "maker": n["maker"], "taker": n["taker"], "token_conflict": n["token_conflict"].astype(bool),
        })
        ok = ~np.isnan(ts_chain)
        if ok.any():
            ts_err.append(pd.Series(ts_chain[ok] - out["ts_local"].to_numpy()[ok]).describe(percentiles=[.5]).to_dict())
        # stats: sort by market, block, file order
        s = out.assign(_rn=df["_rn"].to_numpy()).sort_values(["market_id", "block_number", "_rn"], kind="mergesort")
        g = s["market_id"].astype(str).to_numpy()
        tsx = np.where(np.isnan(s["ts_chain"].to_numpy()), s["ts_local"].to_numpy(), s["ts_chain"].to_numpy())
        trk_all.update(g, s["price_raw"].to_numpy(), s["yes_price"].to_numpy(), tsx, s["outcome_index"].fillna(-1).to_numpy())
        d = ~s["is_taker_order_row"].to_numpy()
        trk_dedup.update(g[d], s["price_raw"].to_numpy()[d], s["yes_price"].to_numpy()[d], tsx[d],
                         s["outcome_index"].fillna(-1).to_numpy()[d])
        tbl = pa.Table.from_pandas(out, preserve_index=False)
        if writer is None or rows_in_part >= 5_000_000:
            if writer is not None:
                writer.close()
            writer = pq.ParquetWriter(out_dir / f"part-{part:04d}.parquet", tbl.schema, compression="zstd")
            part, rows_in_part = part + 1, 0
        writer.write_table(tbl, row_group_size=500_000)
        rows_in_part += len(out)
        if gi % 10 == 0:
            el = time.time() - t0
            print(f"rg {gi}/{n_groups} {el:.0f}s eta {el / (gi + 1) * (n_groups - gi - 1) / 60:.0f} min "
                  f"raw_j={trk_all.raw_j} yes_j={trk_all.yes_j}", flush=True)
    if writer is not None:
        writer.close()
    res = pd.DataFrame([trk_all.result(), trk_dedup.result()])
    res.to_csv(VEN / "rates_trades_parquet.csv", index=False)
    pd.DataFrame(ts_err).to_csv(VEN / "ts_chain_minus_local_by_rowgroup.csv", index=False)
    print(res.T.to_string())


# ------------------------------------------------------------------------------------------- research ticks


def build_research() -> None:
    tm = TokenMap.load_default()
    od = CLEAN / "research_trades"
    od.mkdir(parents=True, exist_ok=True)
    for cat in CATS:
        df = pd.read_parquet(D / "research" / cat / "trades.parquet")
        df = df[(df["timestamp"] > 0) & df["conditionId"].notna()]
        n = normalize_trades(df, tm, schema="data_api")
        keep = ["timestamp", "conditionId", "eventSlug", "slug", "title", "proxyWallet", "transactionHash",
                "asset", "outcome", "outcomeIndex", "side", "price", "size", "usd_amount",
                "token_id", "outcome_index", "outcome_label", "yes_price", "yes_side", "token_source", "token_conflict"]
        n = n[[c for c in keep if c in n.columns]].rename(columns={"price": "price_raw", "side": "side_raw"})
        n.to_parquet(od / f"{cat}.parquet", index=False)
        print(cat, len(n), "conflicts", int(n.token_conflict.sum()), "unknown", int(n.yes_price.isna().sum()), flush=True)


# ------------------------------------------------------------------------------------------- price history


def _yes_price_sql(src: str, dst: str) -> str:
    return f"""
    copy (
      select market_id, timestamp,
        coalesce(max(case when upper(outcome) = 'YES' then price end),
                 1 - max(case when upper(outcome) = 'NO' then price end)) as yes_price,
        max(case when upper(outcome) = 'YES' then price end) as yes_token_price,
        max(case when upper(outcome) = 'NO' then price end) as no_token_price,
        count(*) as n_src_rows
      from read_parquet('{src}')
      where upper(outcome) in ('YES', 'NO')
      group by market_id, timestamp
      order by market_id, timestamp
    ) to '{dst}' (format parquet, compression zstd)
    """


def build_prices() -> None:
    c = duckdb.connect()
    c.execute("SET enable_progress_bar=false")
    c.execute("SET memory_limit='6GB'")
    c.execute("SET threads=4")
    c.execute(f"SET temp_directory='{(VEN / 'duck_tmp').as_posix()}'")
    od = CLEAN / "prices"
    od.mkdir(parents=True, exist_ok=True)
    log = []
    for p in sorted((D / "polymarket" / "prices").glob("prices_*.parquet")):
        dst = od / p.name
        if dst.exists():
            continue
        try:
            pq.ParquetFile(p)
        except Exception as e:  # noqa: BLE001
            log.append({"file": p.name, "status": "corrupt_skipped", "error": repr(e)[:200]})
            print(p.name, "CORRUPT - skipped", flush=True)
            continue
        t = time.time()
        c.execute(_yes_price_sql(p.as_posix(), dst.as_posix()))
        log.append({"file": p.name, "status": "ok", "secs": round(time.time() - t, 1)})
        print(p.name, f"{time.time() - t:.0f}s", flush=True)
    pd.DataFrame(log).to_csv(VEN / "build_prices_log.csv", index=False)


def build_small() -> None:
    c = duckdb.connect()
    c.execute("SET enable_progress_bar=false")
    for sub in ("research_prices", "pmxt_ohlcv", "tardis_trades"):
        (CLEAN / sub).mkdir(parents=True, exist_ok=True)
    for cat in CATS:
        p = D / "research" / cat / "prices.parquet"
        if p.exists():
            c.execute(_yes_price_sql(p.as_posix(), (CLEAN / "research_prices" / f"{cat}.parquet").as_posix()))
    c.execute(_yes_price_sql((D / "pmxt" / "ohlcv" / "ohlcv_all.parquet").as_posix(),
                             (CLEAN / "pmxt_ohlcv" / "ohlcv_all.parquet").as_posix()))
    tmap = (D / "vendor" / "tardis" / "token_map.parquet").as_posix()
    for p in sorted((D / "vendor" / "tardis" / "trades").glob("*.parquet")):
        c.execute(f"""
        copy (
          select cast(t.symbol as varchar) token_id, m.market_id, m.outcome_idx outcome_index, t.timestamp ts_us,
                 upper(cast(t.side as varchar)) side_raw, t.price price_raw, t.amount, t.usd,
                 case when m.outcome_idx = 0 then t.price when m.outcome_idx = 1 then 1 - t.price end yes_price,
                 case when m.outcome_idx = 0 then upper(cast(t.side as varchar))
                      when m.outcome_idx = 1 then (case upper(cast(t.side as varchar)) when 'BUY' then 'SELL'
                                                   when 'SELL' then 'BUY' end) end yes_side
          from read_parquet('{p.as_posix()}') t left join '{tmap}' m on m.symbol = cast(t.symbol as varchar)
          order by m.market_id, t.timestamp
        ) to '{(CLEAN / 'tardis_trades' / p.name).as_posix()}' (format parquet, compression zstd)""")
    print("small done", flush=True)


if __name__ == "__main__":
    targets = sys.argv[1:] or ["research", "small", "prices", "trades"]
    for t in targets:
        if t == "trades":
            build_trades()
        elif t.startswith("trades:"):
            build_trades(int(t.split(":")[1]))
        elif t == "research":
            build_research()
        elif t == "prices":
            build_prices()
        elif t == "small":
            build_small()
        else:
            raise SystemExit(f"unknown target {t}")
