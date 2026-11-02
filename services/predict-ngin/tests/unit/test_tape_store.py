"""Tape store: build from a sorted Parquet tape, ticker-aligned chunks, zero-copy mmap columns, pushdown helpers."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.data.tape_store import TICKER_CODE, TapeNotSorted, TapeStore  # noqa: E402


def make_tape(path, n_tickers=50, seed=0):
    rng = np.random.default_rng(seed)
    parts = []
    for t in range(n_tickers):
        n = int(rng.integers(1, 200))
        ts = np.sort(rng.integers(1_700_000_000, 1_700_100_000, n))
        parts.append(pd.DataFrame(dict(
            ticker=f"T{t:04d}", ts=ts, created_time=[f"{x:012d}" for x in ts], yc=rng.integers(1, 100, n).astype(np.int16),
            tyes=rng.random(n) < .5, qty=rng.integers(1, 500, n).astype(np.int32),
            close_ts=np.int64(1_700_200_000 + t), series=f"S{t % 5}", fee_mult=(None if t % 3 == 0 else 1.0))))
    df = pd.concat(parts, ignore_index=True)
    df.to_parquet(path, row_group_size=137)  # many small row groups / batches -> exercises boundary logic
    return df


def build(tmp_path, src, **kw):
    return TapeStore.build_from_parquet(src, "t", print_cols=["ts", "yc", "tyes", "qty"],
                                        ticker_cols=["close_ts", "series", "fee_mult"], sort_cols=["created_time"],
                                        cache_root=tmp_path / "cache", batch_rows=100, verbose=False, **kw)


def test_build_roundtrip(tmp_path):
    src = tmp_path / "tape.parquet"; df = make_tape(src)
    st = build(tmp_path, src)
    assert st.n_rows == len(df)
    for c in ("ts", "yc", "tyes", "qty"):
        np.testing.assert_array_equal(st.col(c), df[c].to_numpy())
    codes = pd.factorize(df.ticker)[0]
    np.testing.assert_array_equal(st.col(TICKER_CODE), codes)
    assert list(st.tickers.ticker) == sorted(df.ticker.unique())
    np.testing.assert_array_equal(st.ticker_attr("close_ts")[codes], df.close_ts.to_numpy())
    assert st.tickers.fee_mult.isna().sum() == df.groupby("ticker").fee_mult.first().isna().sum()
    assert isinstance(st.col("ts"), np.memmap)


def test_chunks_cover_and_align(tmp_path):
    src = tmp_path / "tape.parquet"; df = make_tape(src)
    st = build(tmp_path, src)
    seen = []
    for ch in st.chunks(target_rows=500):
        tk = ch.ticker_code
        assert set(np.unique(tk)) == set(ch.codes)  # no ticker split across chunks
        seen.append(ch["ts"])
        assert isinstance(ch.rows, slice)
    np.testing.assert_array_equal(np.concatenate(seen), df.ts.to_numpy())
    assert len(st.chunk_specs(n_chunks=4)) <= 4


def test_subset_pushdown_and_filter(tmp_path):
    src = tmp_path / "tape.parquet"; df = make_tape(src)
    st = build(tmp_path, src)
    codes = st.codes_where(lambda t: t.series.isin(["S1", "S3"]))
    exp = df[df.series.isin(["S1", "S3"])]
    got = np.concatenate([ch["ts"] for ch in st.chunks(target_rows=300, tickers=codes)])
    np.testing.assert_array_equal(got, exp.ts.to_numpy())
    ch = st.chunk(st.codes_for(["T0001", "T0003"]))
    np.testing.assert_array_equal(ch["qty"], df[df.ticker.isin(["T0001", "T0003"])].qty.to_numpy())
    f = ch.filter(ch["yc"] > 50)
    np.testing.assert_array_equal(f["qty"], ch["qty"][ch["yc"] > 50])
    np.testing.assert_array_equal(f.tattr("close_ts"), st.ticker_attr("close_ts")[f.ticker_code])


def test_unsorted_source_raises(tmp_path):
    src = tmp_path / "tape.parquet"; df = make_tape(src)
    df.sample(frac=1, random_state=0).to_parquet(src)
    with pytest.raises(TapeNotSorted):
        build(tmp_path, src)


def test_nonconstant_ticker_col_raises(tmp_path):
    src = tmp_path / "tape.parquet"; df = make_tape(src)
    df.loc[5, "close_ts"] += 1
    df.to_parquet(src, row_group_size=137)
    with pytest.raises(ValueError, match="close_ts"):
        build(tmp_path, src)


def test_stale_source_rebuilds(tmp_path):
    src = tmp_path / "tape.parquet"; make_tape(src)
    st = build(tmp_path, src)
    assert TapeStore.is_fresh("t", src, tmp_path / "cache")
    df2 = make_tape(src, n_tickers=10, seed=1)
    os.utime(src, ns=(time.time_ns(), time.time_ns() + 10**9))
    assert not TapeStore.is_fresh("t", src, tmp_path / "cache")
    del st
    st2 = build(tmp_path, src)
    assert st2.n_rows == len(df2)


def test_duckdb_view(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    src = tmp_path / "tape.parquet"; df = make_tape(src)
    st = build(tmp_path, src)
    con = duckdb.connect()
    st.duckdb(con, "p")
    n = con.execute("select count(*) from p join p_tickers t on p._tk = t.code where t.series = 'S2'").fetchone()[0]
    assert n == (df.series == "S2").sum()


def test_presort_unsorted_source(tmp_path):
    pytest.importorskip("duckdb")
    src = tmp_path / "tape.parquet"; df = make_tape(src)
    shuffled = df.sample(frac=1, random_state=0).reset_index(drop=True)
    shuffled.to_parquet(src)
    st = build(tmp_path, src, presort=True)
    exp = shuffled.sort_values(["ticker", "created_time"], kind="mergesort")  # stable == file_row_number tie-break
    np.testing.assert_array_equal(st.col("qty"), exp.qty.to_numpy())
    np.testing.assert_array_equal(st.col("ts"), exp.ts.to_numpy())
    assert not (tmp_path / "cache" / "t.sorted.parquet").exists()
