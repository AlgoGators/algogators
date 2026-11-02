"""Profile the CURRENT multi-strategy research path (mkgap_causal / mrev_fills style). Research infra only.

Representative run: the Fa / Fb / Fc causal fill models + the mkgap cell aggregate ("4 strategies") over the Kalshi
base tape (data/vendor/micro/base.parquet), chunked exactly as mkgap_causal.run does (DuckDB `hash(ticker) % nch`).

Splits wall time per chunk into: duckdb scan+filter+sort -> Arrow (io_scan), Arrow -> pandas (parse),
per-model numba engine (sim_<model>), fill frame build + aggregate (post). Peak RSS sampled every 50 ms (includes
DuckDB native memory). Writes JSON to --out.

usage: engopt_profile_baseline.py --data-root <main data dir> [--nch 48] [--chunks 0,1,2] [--out file.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts/research"))
from src.data.memutil import peak_private, peak_rss  # noqa: E402


class PeakRSS:  # OS-tracked process peak working set (includes DuckDB native memory)
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    @property
    def peak(self):
        return peak_rss()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--nch", type=int, default=48)
    ap.add_argument("--chunks", default="0,1,2")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    dr = Path(a.data_root)
    import mkgap_common as G
    G.BASE = (dr / "vendor/micro/base.parquet").as_posix()
    G.SERIES = (dr / "vendor/kalshi_research/series.parquet").as_posix()
    G.OUT = dr / "vendor/mkgap"  # read-only use (books_family.csv); nothing is written by this harness
    import mkgap_causal as MC
    MC._engine(np.zeros(2, np.int64), np.zeros(2, np.int64), np.full(2, 50, np.int64), np.zeros(2, np.bool_),
               np.ones(2), np.ones(2, np.bool_), np.ones(2), 0, 0.5)  # JIT warm-up (cache=True)
    rows = []
    with PeakRSS() as pk:
        t = time.perf_counter()
        import duckdb, tempfile  # same settings as MC.connect(), but spill to a temp dir (never into data/)
        con = duckdb.connect()
        con.execute(f"SET memory_limit='2500MB'; SET threads=2; SET enable_progress_bar=false; "
                    f"SET temp_directory='{Path(tempfile.gettempdir()).as_posix()}/engopt_duck'")
        fam = G.family_table(con)  # one full distinct-series scan, as in MC.connect()
        con.register("fam", fam[["series", "family"]])
        q0m = MC.q0_map()
        setup = time.perf_counter() - t
        for k in [int(x) for x in a.chunks.split(",")]:
            r = {"chunk": k}
            t = time.perf_counter()
            w = f"hash(ticker) % {a.nch} = {k}"
            tbl = con.execute(f"""
              select ticker, event_ticker ev, family, ts, yc, tyes, qty::DOUBLE qty, close_ts, settle_ts, win,
                     (fee_type like '%maker%') fmser, coalesce(fee_mult, 1.0) mult,
                     case when ts < {G.SPLIT} and close_ts < {G.SPLIT} then 'train' when ts >= {G.SPLIT} then 'test'
                          else 'none' end split
              from '{G.BASE}' b join fam using(series)
              where ts >= {G.T_START} - 7*86400 and ts < close_ts and yc between 1 and 99 and {w}
              order by ticker, created_time""").fetch_arrow_table()
            r["io_scan"] = time.perf_counter() - t
            t = time.perf_counter(); df = tbl.to_pandas(); del tbl
            r["parse"] = time.perf_counter() - t
            r["rows"] = len(df)
            # simulation split per model (mirrors run_chunk without the fill-frame build)
            tkc = pd.factorize(df.ticker)[0].astype(np.int64)
            sec = df.ts.to_numpy(np.int64); yc = df.yc.to_numpy(np.int64); ty = df.tyes.to_numpy(np.bool_)
            qty = df.qty.to_numpy(np.float64)
            elig = (df.split.isin(["train", "test"]) & (df.ts >= G.T_START)).to_numpy(np.bool_)
            q0 = df.family.map(q0m).fillna(MC.DEFAULT_Q0).to_numpy(np.float64)
            for mname, (mid, share) in MC.MODELS.items():
                t = time.perf_counter(); out = MC._engine(tkc, sec, yc, ty, qty, elig, q0, mid, share)
                r[f"sim_{mname}"] = time.perf_counter() - t; r[f"fills_{mname}"] = int(len(out[0]))
            t = time.perf_counter()
            res = MC.run_chunk(df, q0m)  # full original path (re-runs engines; we subtract them below)
            agg = {m: MC.aggregate(f) for m, f in res.items()}
            post_total = time.perf_counter() - t
            r["post"] = post_total - sum(r[f"sim_{m}"] for m in MC.MODELS)
            r["agg_rows"] = {m: int(len(v)) for m, v in agg.items()}
            rows.append(r); print(r, flush=True)
            del df, res, agg
    tot = {k: float(sum(r[k] for r in rows)) for k in ["io_scan", "parse", "sim_Fa", "sim_Fb", "sim_Fc", "post"]}
    out = dict(setup_s=setup, chunks=rows, totals=tot, nch=a.nch, peak_rss_gb=pk.peak / 1e9, peak_private_gb=peak_private() / 1e9,
               per_chunk_wall_s=(sum(tot.values())) / len(rows))
    print(json.dumps({k: v for k, v in out.items() if k != "chunks"}, indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
