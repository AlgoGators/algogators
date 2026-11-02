"""Tests for the Polymarket tape extension: union reader, HF->ext conversion, RPC log decoding, Kalshi markets merge."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.data_quality import polymarket_onchain as oc  # noqa: E402
from src.data_quality import polymarket_tape as pt  # noqa: E402
from src.data_quality.polymarket_tokens import EXCHANGE_ADDRESSES, TokenMap  # noqa: E402

CUT = pt.CUTOVER_TS
TOK_Y, TOK_N = "111", "222"


def _tm() -> TokenMap:
    return TokenMap.from_markets(pd.DataFrame({"id": ["m1"], "condition_id": ["0xc1"], "token1": [TOK_Y],
                                               "token2": [TOK_N], "answer1": ["Yes"], "answer2": ["No"]}))


def _old_rows(ts: list[float]) -> pd.DataFrame:
    n = len(ts)
    return pd.DataFrame({
        "block_number": np.arange(n, dtype="int64"), "ts_local": np.array(ts, dtype="int64"),
        "ts_chain": np.array(ts, dtype=float), "timestamp_unreliable": False,
        "transaction_hash": [f"ABCDEF{i:02d}" for i in range(n)], "order_hash": "oh", "market_id": "m1",
        "condition_id": "0xc1", "token_id": TOK_Y, "outcome_index": np.float32(0), "outcome0_label": "Yes",
        "price_raw": 0.4, "yes_price": 0.4, "yes_side": "BUY", "is_taker_order_row": [i % 2 == 1 for i in range(n)],
        "usd_amount": 4.0, "token_amount": 10.0, "maker": "0xAbC", "taker": "0xDeF", "token_conflict": False,
    })


def _hf_rows(ts: list[int], side: list[str]) -> pd.DataFrame:
    n = len(ts)
    return pd.DataFrame({
        "timestamp": np.array(ts, dtype="uint64"), "block_number": np.arange(100, 100 + n, dtype="uint64"),
        "transaction_hash": [f"0xFEED{i:02d}" for i in range(n)], "log_index": np.arange(n, dtype="uint32"),
        "contract": "CTF_EXCHANGE_V2", "market_id": "m1", "condition_id": "0xc1", "event_id": "e1",
        "maker": "0xMAKER", "taker": "0xtaker", "price": 0.3, "usd_amount": 3.0, "token_amount": 10.0,
        "maker_direction": "BUY", "taker_direction": "SELL", "nonusdc_side": side,
        "asset_id": [TOK_Y if s == "token1" else TOK_N for s in side],
    })


@pytest.fixture()
def dirs(tmp_path):
    old, ext = tmp_path / "old", tmp_path / "ext"
    old.mkdir()
    (ext / "hf").mkdir(parents=True)
    _old_rows([CUT - 20, CUT - 10, CUT + 5, CUT + 6]).to_parquet(old / "part-0000.parquet")
    pt.to_ext_frame(_hf_rows([CUT - 15, CUT + 5, CUT + 7], ["token1", "token2", "token1"]), _tm(),
                    "hf").to_parquet(ext / "hf" / "part-rg00001.parquet")
    pd.DataFrame({"x": [1]}).to_parquet(ext / "hf" / "part-rg00002.parquet.tmp")  # in-flight file ignored
    return old, ext


def test_no_ext_returns_old_unchanged(tmp_path, dirs):
    old, _ = dirs
    df = duckdb.sql(pt.clean_trades_sql(old_dir=old, ext_dir=tmp_path / "missing")).df()
    assert len(df) == 4 and df["log_index"].isna().all()


def test_union_takes_each_side_of_cutover_without_overlap(dirs):
    old, ext = dirs
    df = duckdb.sql(pt.clean_trades_sql(old_dir=old, ext_dir=ext)).df().sort_values("ts_chain")
    assert df["ts_chain"].tolist() == [CUT - 20, CUT - 10, CUT + 5, CUT + 7]
    assert df.loc[df.ts_chain < CUT, "source"].isna().all()
    assert (df.loc[df.ts_chain >= CUT, "source"] == "hf").all()
    # identifier formats harmonised: 0x-lowercase hashes and lowercase addresses on both sides
    assert df["transaction_hash"].str.match(r"^0x[0-9a-f]+$").all()
    assert (df["maker"] == df["maker"].str.lower()).all()


def test_filters_and_columns(dirs):
    old, ext = dirs
    sql = pt.clean_trades_sql(["ts_chain", "yes_price", "order_hash"], start_ts=CUT - 10, end_ts=CUT + 6,
                              maker_rows_only=True, old_dir=old, ext_dir=ext)
    df = duckdb.sql(sql).df()
    assert list(df.columns) == ["ts_chain", "yes_price", "order_hash"]
    assert sorted(df.ts_chain) == [CUT + 5]  # old CUT-10 is a taker-order row; CUT+7 is past end
    assert df.order_hash.isna().all()
    with pytest.raises(ValueError):
        pt.clean_trades_sql(["nope"], old_dir=old, ext_dir=ext)


def test_to_ext_frame_yes_normalises_token2():
    out = pt.to_ext_frame(_hf_rows([CUT, CUT + 1], ["token1", "token2"]), _tm(), "hf")
    assert out["yes_price"].tolist() == pytest.approx([0.3, 0.7])
    assert out["yes_side"].tolist() == ["SELL", "BUY"]  # taker SELLs token; flipped for the NO token
    assert out["outcome0_label"].eq("Yes").all() and not out["is_taker_order_row"].any()
    assert out["ts_chain"].tolist() == [float(CUT), float(CUT + 1)]
    assert out["maker"].eq("0xmaker").all() and out["transaction_hash"].str.startswith("0xfeed").all()
    assert list(out.columns) == pt.EXT_COLUMNS


def test_to_ext_frame_takes_pre_cutover_times_from_block_anchors():
    anchors = pd.DataFrame({"block": [0, 101], "ts": [CUT - 1000.0, CUT - 798.0]})  # 2 s per block
    out = pt.to_ext_frame(_hf_rows([CUT - 5000, CUT - 4000, CUT + 1], ["token1"] * 3), _tm(), "hf", anchors=anchors)
    # blocks 100 / 101 are anchored; the release's own pre-cutover times are ignored; block 102 is past
    # the anchors but on the cutover side, so it keeps the release time
    assert out["ts_chain"].tolist() == [CUT - 800.0, CUT - 798.0, float(CUT + 1)]
    assert out["ts_local"].tolist() == [CUT - 5000, CUT - 4000, CUT + 1]
    assert not out["timestamp_unreliable"].any()
    late = pt.to_ext_frame(_hf_rows([CUT - 5000], ["token1"]), _tm(), "hf", anchors=anchors.iloc[:1])
    assert np.isnan(late.loc[0, "ts_chain"]) and late.loc[0, "timestamp_unreliable"]  # outside the anchors


def test_full_history_ext_serves_every_row(tmp_path):
    ext = tmp_path / "ext"
    (ext / "hf").mkdir(parents=True)
    anchors = pd.DataFrame({"block": [0, 1000], "ts": [CUT - 2000.0, CUT]})
    pt.to_ext_frame(_hf_rows([CUT - 1800, CUT + 5], ["token1", "token2"]), _tm(), "hf",
                    anchors=anchors).to_parquet(ext / "hf" / "part-rg00000.parquet")
    missing_old = tmp_path / "no_old"
    # without a coverage record the ext tape starts at the cutover (existing setups)
    df = duckdb.sql(pt.clean_trades_sql(old_dir=missing_old, ext_dir=ext)).df()
    assert df["ts_chain"].tolist() == [CUT + 5]
    # a --full build records start_ts 0: every ext row is served
    (ext / "coverage.json").write_text('{"start_ts": 0, "end_ts": null}')
    assert pt.ext_start_ts(ext) == 0
    df = duckdb.sql(pt.clean_trades_sql(old_dir=missing_old, ext_dir=ext)).df().sort_values("ts_chain")
    assert df["ts_chain"].tolist() == [CUT - 2000.0 + 200.0, CUT + 5]  # block 100 interpolated
    sql = pt.clean_trades_sql(old_dir=missing_old, ext_dir=ext, start_ts=CUT - 1900, end_ts=CUT - 1)
    assert duckdb.sql(sql).df()["ts_chain"].tolist() == [CUT - 1800.0]


def test_v2_exchanges_flag_taker_order_rows():
    assert set(oc.CONTRACTS) <= EXCHANGE_ADDRESSES


def _log(topic, data_words, maker="aa", taker="bb", li=1, contract="0xe111180000d2663c0091e4f400237545b87b996b"):
    word = lambda v: f"{v:064x}"  # noqa: E731
    return {"address": contract, "topics": [topic, "0x" + "11" * 32, "0x" + maker.rjust(64, "0"),
                                            "0x" + taker.rjust(64, "0")],
            "data": "0x" + "".join(word(v) for v in data_words), "blockNumber": hex(500),
            "blockTimestamp": hex(CUT), "transactionHash": "0xABC", "logIndex": hex(li), "removed": False}


def test_decode_v1_and_v2_and_build_trades():
    logs = [
        _log(oc.V2_TOPIC, [0, int(TOK_N), 3_000_000, 10_000_000, 0, 0, 0], li=1),  # maker BUYS 10 NO for $3
        _log(oc.V2_TOPIC, [1, int(TOK_Y), 5_000_000, 2_000_000, 0, 0, 0], li=2),  # maker SELLS 5 YES for $2
        _log(oc.V1_TOPIC, [int(TOK_Y), 0, 4_000_000, 1_000_000, 0], li=3,
             contract="0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e"),
        _log(oc.V2_TOPIC, [0, int(TOK_Y), 1, 1, 0, 0, 0], li=4, taker="e111180000d2663c0091e4f400237545b87b996b"),
        _log("0x" + "55" * 32, [1], li=5),  # other event ignored
        _log(oc.V2_TOPIC, [0, 999, 1_000_000, 2_000_000, 0, 0, 0], li=6),  # unknown token
    ]
    of = oc.decode_logs(logs)
    assert len(of) == 5 and of.loc[0, "maker_asset_id"] == "0" and of.loc[1, "taker_asset_id"] == "0"
    mk = oc.markets_token_frame(pd.DataFrame({"id": ["m1"], "condition_id": ["0xc1"], "token1": [TOK_Y],
                                              "token2": [TOK_N], "event_id": ["e1"]}))
    tr, unk = oc.orderfilled_to_trades(of, mk)
    assert list(tr.columns) == oc.HF_TRADE_COLUMNS
    assert tr["log_index"].tolist() == [1, 2, 3]  # exchange taker-order row dropped, unknown token split off
    assert len(unk) == 1 and unk.iloc[0]["asset_id"] == "999"
    r = tr.set_index("log_index")
    assert r.loc[1, "price"] == pytest.approx(0.3) and r.loc[1, "nonusdc_side"] == "token2"
    assert (r.loc[1, "maker_direction"], r.loc[1, "taker_direction"]) == ("BUY", "SELL")
    assert r.loc[2, "price"] == pytest.approx(0.4) and r.loc[2, "usd_amount"] == 2.0 and r.loc[2, "token_amount"] == 5.0
    assert r.loc[2, "maker_direction"] == "SELL" and r.loc[3, "contract"] == "CTF_EXCHANGE"
    assert r.loc[1, "maker"] == "0x" + "aa".rjust(40, "0")


def test_first_block_search():
    ts = {n: 1000 + 2 * n for n in range(200)}
    assert oc.first_block_at_or_after(1101, ts.__getitem__, 0, 199) == 51
    assert oc.first_block_at_or_after(1100, ts.__getitem__, 0, 199) == 50


def _load_kalshi_refresh():
    spec = importlib.util.spec_from_file_location("rkm", ROOT / "scripts" / "data" / "refresh_kalshi_markets.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_kalshi_markets_row_and_merge():
    rkm = _load_kalshi_refresh()
    row = rkm.to_row({"ticker": "KXA-26-X", "event_ticker": "KXA-26", "status": "finalized", "result": "yes",
                      "yes_bid_dollars": "0.9900", "last_price_dollars": "0.9900", "volume_fp": "600000.00",
                      "settlement_value_dollars": "1.0000", "updated_time": "2026-10-01"})
    assert (row["yes_bid"], row["last_price"], row["volume"], row["settlement_value"]) == (99, 99, 600000, 100.0)
    active = rkm.to_row({"ticker": "KXB-1", "event_ticker": "KXB", "status": "active", "volume_fp": "10",
                         "settlement_value_dollars": "0.0000"})
    assert active["settlement_value"] is None
    old = pd.DataFrame([{**{c: "" for c in rkm.COLUMNS}, "ticker": "KXB-1", "event_ticker": "KXB",
                         "series_ticker": "KXBSER", "category": "Old", "liquidity": 7, "volume": 900000,
                         "status": "active", "settlement_value": np.nan}])
    for c in ("yes_bid", "yes_ask", "no_bid", "no_ask", "last_price", "volume_24h", "open_interest"):
        old[c] = 0
    fresh = rkm.finalize_frame(pd.DataFrame([row, {**active, "status": "finalized"},
                                             {**row, "ticker": "KXC-1", "volume": 5}]), old, {"KXA": "Sports"})
    m = rkm.merge_markets(old, fresh, 500_000).set_index("ticker")
    assert sorted(m.index) == ["KXA-26-X", "KXB-1"]  # small new market dropped, old ticker always kept
    assert m.loc["KXB-1", "status"] == "finalized" and m.loc["KXB-1", "series_ticker"] == "KXBSER"
    assert m.loc["KXB-1", "liquidity"] == 7 and m.loc["KXA-26-X", "category"] == "Sports"
    assert list(m.reset_index().columns) == rkm.COLUMNS
