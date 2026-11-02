"""Unit tests for recorder Parquet output, sampling, and pair-config parsing. No network."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from src.recorder.book import OrderBook
from src.recorder.config import ConfigError, load_config, parse_config
from src.recorder.discovery import score_match
from src.recorder.sampler import Sampler, book_row
from src.recorder.writer import BOOK_SCHEMA, TRADE_SCHEMA, CountingSink, ParquetSink


def _ns(y, mo, d, h, mi=0, s=0):
    return int(datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp()) * 1_000_000_000


def _book(market="M1"):
    b = OrderBook("kalshi", market, outcome="yes", pair="fed")
    b.apply_snapshot(bids=[(0.40, 10), (0.39, 5)], asks=[(0.42, 7)], seq=3, ts_exchange_ns=111)
    return b


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------


def test_book_row_contents():
    row = book_row(_book(), depth=10, now_ns=500, source="ws")
    assert row["venue"] == "kalshi" and row["market_id"] == "M1" and row["pair"] == "fed"
    assert row["bid_px"] == [0.40, 0.39] and row["ask_sz"] == [7.0]
    assert row["best_bid"] == 0.40 and row["best_ask"] == 0.42
    assert row["seq"] == 3 and row["ts_exchange_ns"] == 111 and row["ts_recv_ns"] == 500
    assert set(row) == set(BOOK_SCHEMA.names)


def test_sampler_emits_on_change_and_heartbeat_only():
    s = Sampler(interval_s=1.0, heartbeat_s=10.0)
    b = _book()
    sec = 1_000_000_000
    assert s.due(b, 0) is True
    s.mark(b, 0)
    assert s.due(b, sec // 2) is False  # unchanged
    b.set_level("bid", 0.41, 1)
    assert s.due(b, sec // 2) is False  # changed but < interval
    assert s.due(b, sec) is True
    s.mark(b, sec)
    assert s.due(b, 5 * sec) is False  # unchanged, before heartbeat
    assert s.due(b, 11 * sec) is True  # heartbeat


def test_sampler_counts_updates_and_gap_flag():
    s = Sampler(interval_s=0, heartbeat_s=60)
    b = _book()
    s.mark(b, 0)
    b.set_level("bid", 0.41, 1)
    b.set_level("bid", 0.41, 2)
    b.gap_since_sample = True
    row = s.sample(b, depth=5, now_ns=10, source="ws")
    assert row["n_updates"] == 2 and row["gap"] is True
    assert b.gap_since_sample is False


# ---------------------------------------------------------------------------
# Parquet writing
# ---------------------------------------------------------------------------


def test_parquet_sink_hour_partitioning_and_roundtrip(tmp_path: Path):
    sink = ParquetSink(tmp_path)
    t0 = _ns(2026, 10, 12, 21, 5)
    t1 = _ns(2026, 10, 12, 22, 1)
    b = _book()
    sink.add_book(book_row(b, 10, t0, "ws"))
    sink.add_book(book_row(b, 10, t0 + 1, "ws"))
    sink.add_book(book_row(b, 10, t1, "rest"))
    sink.add_trade({
        "venue": "kalshi", "market_id": "M1", "outcome": "yes", "pair": "fed", "trade_id": "t1",
        "ts_exchange_ns": t0, "ts_recv_ns": t0, "price": 0.4, "size": 3.0, "taker_side": "yes",
        "source": "ws",
    })
    written = sink.flush()
    assert written == 4
    d = tmp_path / "kalshi" / "2026-10-12"
    parts = sorted(p.name for p in d.iterdir())
    assert parts == ["books-21.part0000.parquet", "books-22.part0000.parquet", "trades-21.part0000.parquet"]
    t = pq.read_table(d / "books-21.part0000.parquet")
    assert t.schema.equals(BOOK_SCHEMA, check_metadata=False)
    assert t.num_rows == 2
    assert t.column("bid_px").to_pylist()[0] == [0.40, 0.39]
    tt = pq.read_table(d / "trades-21.part0000.parquet")
    assert tt.schema.equals(TRADE_SCHEMA, check_metadata=False)


def test_parquet_sink_part_numbering_and_compaction(tmp_path: Path):
    sink = ParquetSink(tmp_path)
    t0 = _ns(2026, 10, 12, 21, 5)
    b = _book()
    sink.add_book(book_row(b, 10, t0, "ws"))
    sink.flush()
    sink.add_book(book_row(b, 10, t0 + 5, "ws"))
    sink.flush()
    d = tmp_path / "kalshi" / "2026-10-12"
    assert sorted(p.name for p in d.iterdir()) == ["books-21.part0000.parquet", "books-21.part0001.parquet"]
    # hour 21 is complete once we are in hour 22
    n = sink.compact_completed(now_ns=_ns(2026, 10, 12, 22, 0, 30))
    assert n == 1
    assert sorted(p.name for p in d.iterdir()) == ["books-21.parquet"]
    t = pq.read_table(d / "books-21.parquet")
    assert t.num_rows == 2
    assert t.column("ts_recv_ns").to_pylist() == [t0, t0 + 5]
    # a new sink (restart) appends parts again and re-compaction merges into existing file
    sink2 = ParquetSink(tmp_path)
    sink2.add_book(book_row(b, 10, t0 + 9, "ws"))
    sink2.flush()
    sink2.compact_completed(now_ns=_ns(2026, 10, 12, 23))
    assert pq.read_table(d / "books-21.parquet").num_rows == 3


def test_current_hour_not_compacted(tmp_path: Path):
    sink = ParquetSink(tmp_path)
    t0 = _ns(2026, 10, 12, 21, 5)
    sink.add_book(book_row(_book(), 10, t0, "ws"))
    sink.flush()
    assert sink.compact_completed(now_ns=t0 + 60 * 1_000_000_000) == 0


def test_counting_sink_dry_run(tmp_path: Path):
    s = CountingSink()
    s.add_book(book_row(_book(), 10, 1, "ws"))
    assert s.flush() == 1
    assert s.counts["books"]["kalshi"] == 1
    assert not any(tmp_path.iterdir())


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------

YAML_OK = """
defaults:
  depth: 5
venues:
  kalshi:
    mode: rest
    rest_poll_interval_s: 2
pairs:
  - name: fed-dec
    kalshi: {ticker: KXFEDDECISION-26DEC-H0}
    polymarket:
      yes_token: 23250308899791469248496736713890367877528653645908077470390716328108040647589
      no_token: "87732679008647163528726872230813311501648219217123953628483118558477192701376"
    polymarket_us: {slug: fed-dec-hold}
markets:
  - {venue: kalshi, ticker: KXCPI-26OCT-T0.3}
  - {venue: polymarket, token_id: "111", outcome: yes, label: something}
  - {venue: polymarket_us, slug: tec-mlb-lad}
"""


def test_parse_config_pairs_and_singles():
    cfg = parse_config(YAML_OK)
    assert cfg.defaults.depth == 5
    assert cfg.venues["kalshi"].mode == "rest"
    assert cfg.venues["kalshi"].rest_poll_interval_s == 2
    assert cfg.venues["polymarket"].mode == "ws"  # default
    subs = cfg.subscriptions()
    k = {m.market_id: m for m in subs["kalshi"]}
    assert set(k) == {"KXFEDDECISION-26DEC-H0", "KXCPI-26OCT-T0.3"}
    assert k["KXFEDDECISION-26DEC-H0"].pair == "fed-dec"
    p = {m.market_id: m for m in subs["polymarket"]}
    # large unquoted YAML ints must survive as exact strings
    assert "23250308899791469248496736713890367877528653645908077470390716328108040647589" in p
    assert p["87732679008647163528726872230813311501648219217123953628483118558477192701376"].outcome == "no"
    assert p["111"].pair is None
    assert {m.market_id for m in subs["polymarket_us"]} == {"fed-dec-hold", "tec-mlb-lad"}


@pytest.mark.parametrize(
    "bad",
    [
        "pairs:\n  - name: x\n    kalshi: {ticker: A}\n",  # only one venue
        "pairs:\n  - kalshi: {ticker: A}\n    polymarket_us: {slug: b}\n",  # no name
        "pairs:\n  - name: x\n    kalshi: {ticker: A}\n    polymarket: {no_token: '1'}\n",  # no yes_token
        "markets:\n  - {venue: bogus, ticker: A}\n",
        "venues:\n  kalshi: {mode: carrier_pigeon}\n",
        "pairs:\n  - name: x\n    kalshi: {ticker: A}\n    polymarket_us: {slug: b}\n"
        "  - name: x\n    kalshi: {ticker: C}\n    polymarket_us: {slug: d}\n",  # dup name
    ],
)
def test_parse_config_rejects_bad(bad):
    with pytest.raises(ConfigError):
        parse_config(bad)


def test_shipped_config_parses():
    root = Path(__file__).resolve().parents[2]
    cfg = load_config(root / "config" / "recorder.yaml")
    assert sum(len(v) for v in cfg.subscriptions().values()) >= 1


# ---------------------------------------------------------------------------
# Discovery matching (pure function)
# ---------------------------------------------------------------------------


def test_score_match_prefers_same_event_and_date():
    a = "Will the Fed cut rates by 25bps at the December 2026 meeting?"
    good = "Fed decreases interest rates by 25 bps after December 2026 meeting?"
    bad = "Will the ECB cut rates in March 2027?"
    s_good = score_match(a, good, "2026-12-10T00:00:00Z", "2026-12-10T19:00:00Z")
    s_bad = score_match(a, bad, "2026-12-10T00:00:00Z", "2027-03-15T00:00:00Z")
    assert 0 <= s_bad < s_good <= 1


def test_parquet_sink_writes_bbo_table(tmp_path: Path):
    from src.recorder.core import bbo_row
    from src.recorder.writer import BBO_SCHEMA

    sink = ParquetSink(tmp_path)
    t0 = _ns(2026, 10, 12, 21, 5)
    sink.add_bbo(bbo_row("polymarket", "tok", None, ts_exchange_ns=t0 - 5, ts_recv_ns=t0,
                         best_bid=0.34, best_ask=0.35, source="ws"))
    assert sink.flush() == 1
    files = list((tmp_path / "polymarket" / "2026-10-12").glob("bbo-21.part*.parquet"))
    assert len(files) == 1
    t = pq.read_table(files[0])
    assert t.schema.equals(BBO_SCHEMA)
    assert t.column("best_ask").to_pylist() == [0.35]
    assert sink.compact_completed(now_ns=t0 + 3600 * 1_000_000_000) == 1
    assert (tmp_path / "polymarket" / "2026-10-12" / "bbo-21.parquet").exists()


def test_pair_status_parsed_and_validated():
    ok = parse_config("""
pairs:
  - name: a
    status: verified
    kalshi: {ticker: K1}
    polymarket_us: {slug: s1}
  - name: b
    kalshi: {ticker: K2}
    polymarket: {yes_token: "1"}
""")
    assert [p.status for p in ok.pairs] == ["verified", "needs-review"]
    with pytest.raises(ConfigError):
        parse_config("""
pairs:
  - name: a
    status: maybe
    kalshi: {ticker: K1}
    polymarket_us: {slug: s1}
""")


def test_shipped_config_pairs_have_status_and_stay_small():
    cfg = load_config(Path(__file__).resolve().parents[2] / "config" / "recorder.yaml")
    assert all(p.status in ("verified", "needs-review") for p in cfg.pairs)
    assert sum(len(v) for v in cfg.subscriptions().values()) <= 30  # storage budget, see docs/RECORDER.md
