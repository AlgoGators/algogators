"""Unioned Kalshi tape (trades_fixed + trades_fixed_ext) and tape_refresh conversions."""
import importlib.util
import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from src.trading.arbitrage import kalshi_tape as kt

ROOT = Path(__file__).resolve().parents[2]
CUT = kt.CUTOVER_TS


def _rows(ids, ts):
    n = len(ids)
    return pd.DataFrame({
        "trade_id": ids, "ticker": ["T-1"] * n, "event_ticker": ["T"] * n, "yes_price": [0.5] * n,
        "no_price": [0.5] * n, "count": [1] * n, "taker_side": ["yes"] * n,
        "created_time": ["x"] * n, "timestamp_unix": ts, "timestamp_unix_orig": ts,
    })


@pytest.fixture
def dirs(tmp_path):
    old, ext = tmp_path / "old", tmp_path / "ext"
    old.mkdir(); ext.mkdir()
    # old tape: complete before the cutover, a partial (duplicated) subset after it
    _rows(["a", "b", "dup"], [CUT - 100, CUT - 1, CUT + 10]).to_parquet(old / "trades_2026-01.parquet")
    return old, ext


def test_no_ext_is_exactly_old(dirs):
    old, ext = dirs
    df = duckdb.sql(kt.tape_sql(old_dir=old, ext_dir=ext)).df()
    assert sorted(df.trade_id) == ["a", "b", "dup"]
    assert list(df.columns) == kt.COLUMNS


def test_union_uses_cutover_no_duplicates(dirs):
    old, ext = dirs
    _rows(["dup", "c"], [CUT + 10, CUT + 20]).to_parquet(ext / "trades_2026-01-28.parquet")
    _rows(["junk"], [CUT + 30]).to_parquet(ext / "trades_2026-01-29.parquet.tmp")  # ignored
    df = duckdb.sql(kt.tape_sql(old_dir=old, ext_dir=ext)).df()
    assert sorted(df.trade_id) == ["a", "b", "c", "dup"]
    assert df.trade_id.is_unique


def test_range_and_columns(dirs):
    old, ext = dirs
    _rows(["dup", "c"], [CUT + 10, CUT + 20]).to_parquet(ext / "trades_2026-01-28.parquet")
    df = duckdb.sql(kt.tape_sql(["trade_id"], start_ts=CUT - 1, end_ts=CUT + 15,
                                old_dir=old, ext_dir=ext)).df()
    assert sorted(df.trade_id) == ["b", "dup"] and list(df.columns) == ["trade_id"]
    only_new = duckdb.sql(kt.tape_sql(["trade_id"], start_ts=CUT, old_dir=old, ext_dir=ext)).df()
    assert sorted(only_new.trade_id) == ["c", "dup"]


def test_tape_end_ts(tmp_path):
    assert kt.tape_end_ts(tmp_path) is None
    _rows(["c"], [CUT]).to_parquet(tmp_path / "trades_2026-01-28.parquet")
    assert kt.tape_end_ts(tmp_path) == CUT + 86400


def _load_refresh():
    spec = importlib.util.spec_from_file_location("tape_refresh", ROOT / "scripts/research/tape_refresh.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tape_refresh"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_refresh_to_frame_matches_old_conventions():
    tr = _load_refresh()
    rows = [
        {"trade_id": "x", "ticker": "KXMVEFOO-S1-AB", "count_fp": "22.00", "created_time": "2026-01-27T22:59:59.7565Z",
         "yes_price_dollars": "0.6360", "no_price_dollars": "0.3640", "taker_side": "yes",
         "taker_outcome_side": "yes", "is_block_trade": False},
        {"trade_id": "y", "ticker": "KXNBA-26-X", "count_fp": "3.00", "created_time": "2026-01-27T22:00:00Z",
         "yes_price_dollars": "0.2900", "no_price_dollars": "0.7100", "taker_side": "no",
         "taker_outcome_side": "no", "is_block_trade": False},
    ]
    df, st = tr.to_frame(rows)
    assert df.yes_price.tolist() == [0.63, 0.29] and df.no_price.tolist() == [0.36, 0.71]
    assert df["count"].tolist() == [22, 3] and df.taker_side.tolist() == ["yes", "no"]
    assert df.created_time.tolist() == ["2026-01-27T22:59:59.756500Z", "2026-01-27T22:00:00.000000Z"]
    assert df.timestamp_unix.tolist() == [1769554799, 1769551200]
    assert (df.timestamp_unix == df.timestamp_unix_orig).all()
    assert st["subcent_price"] == 1 and st["frac_count"] == 0
