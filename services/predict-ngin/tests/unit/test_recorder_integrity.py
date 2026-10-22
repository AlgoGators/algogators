"""Regression tests for two data-quality artifacts reported on 2026-10-14
(docs/MICRO_ANOMALY_SCAN.md, section 1.5):

1. "best_bid stays set after its level empties (size 0)". Every venue path must remove an
   emptied level and move the touch; a stored row's best_bid/best_ask must always name a
   level with size > 0, and an emptied side must be stored as null (not the old price).
2. Feeds that go silent: rows must carry explicit staleness (``last_update_ns``/``age_s``),
   and the Kalshi silence audit must tell a quiet book from a dead subscription.
"""

from __future__ import annotations

import asyncio
from collections import Counter

import pyarrow as pa
import pyarrow.parquet as pq

from src.recorder.book import OrderBook
from src.recorder.sampler import Sampler, book_row
from src.recorder.venues import kalshi, polymarket
from src.recorder.writer import BOOK_SCHEMA, ParquetSink

NS = 1_000_000_000
TK = "KXHIGHCHI-26OCT13-B82.5"


def _snap(seq=1, yes=(("0.3500", "0.91"), ("0.3200", "20.20")), no=(("0.6400", "51.00"),), sid=2):
    return {"type": "orderbook_snapshot", "sid": sid, "seq": seq,
            "msg": {"market_ticker": TK, "yes_dollars_fp": [list(l) for l in yes],
                    "no_dollars_fp": [list(l) for l in no]},
            "sending_ts_ms": 1_791_900_000_000}


def _delta(seq, price, delta, side, sid=2):
    return {"type": "orderbook_delta", "sid": sid, "seq": seq,
            "msg": {"market_ticker": TK, "price_dollars": price, "delta_fp": delta, "side": side,
                    "ts_ms": 1_791_900_000_000 + seq}}


def _assert_row_touch_ok(row):
    for side in ("bid", "ask"):
        px, sz = row[f"{side}_px"], row[f"{side}_sz"]
        assert all(s > 0 for s in sz), row
        assert row[f"best_{side}"] == (px[0] if px else None), row


# --- artifact 1: zero-size touch -------------------------------------------------


def test_kalshi_delta_to_zero_removes_touch_and_moves_best_bid():
    s = kalshi.KalshiWsState()
    s.process(_snap(), recv_ns=1)
    s.process(_delta(2, "0.3500", "-0.91", "yes"), recv_ns=2)  # touch level empties exactly
    b = s.books[TK]
    assert b.best_bid == 0.32 and 0.35 not in b.bids
    row = book_row(b, 10, 3, "ws")
    _assert_row_touch_ok(row)
    assert row["best_bid"] == 0.32 and row["bid_sz"] == [20.2]


def test_kalshi_delta_float_residue_does_not_leave_dust_level():
    s = kalshi.KalshiWsState()
    s.process(_snap(yes=(("0.3500", "0.10"), ("0.3200", "5.00"))), recv_ns=1)
    s.process(_delta(2, "0.3500", "0.20", "yes"), recv_ns=2)
    s.process(_delta(3, "0.3500", "-0.30", "yes"), recv_ns=3)  # 0.1 + 0.2 - 0.3 != 0 in floats
    b = s.books[TK]
    assert 0.35 not in b.bids and b.best_bid == 0.32
    _assert_row_touch_ok(book_row(b, 10, 4, "ws"))


def test_kalshi_no_side_emptied_gives_null_best_ask_not_old_price():
    s = kalshi.KalshiWsState()
    s.process(_snap(), recv_ns=1)
    s.process(_delta(2, "0.6400", "-51.00", "no"), recv_ns=2)  # the only YES ask (0.36) empties
    row = book_row(s.books[TK], 10, 3, "ws")
    assert row["best_ask"] is None and row["ask_px"] == [] and row["ask_sz"] == []
    _assert_row_touch_ok(row)


def test_polymarket_price_change_size_zero_at_touch_moves_best():
    tok = "123"
    s = polymarket.PolymarketWsState()
    s.process({"event_type": "book", "asset_id": tok, "timestamp": "1791838886143",
               "bids": [{"price": "0.46", "size": "100"}, {"price": "0.47", "size": "3"}],
               "asks": [{"price": "0.49", "size": "50"}, {"price": "0.48", "size": "12"}]}, recv_ns=1)
    s.process({"event_type": "price_change", "timestamp": "1791838890000", "price_changes": [
        {"asset_id": tok, "price": "0.47", "size": "0", "side": "BUY", "best_bid": "0.46", "best_ask": "0.48"},
        {"asset_id": tok, "price": "0.48", "size": "0", "side": "SELL", "best_bid": "0.46", "best_ask": "0.49"},
    ]}, recv_ns=2)
    b = s.books[tok]
    assert (b.best_bid, b.best_ask) == (0.46, 0.49)
    s.verify_pending(10 * NS)
    assert s.bbo_mismatches == 0
    _assert_row_touch_ok(book_row(b, 10, 3, "ws"))


def test_row_builder_never_emits_non_positive_level_even_if_book_dict_is_dirty():
    b = OrderBook("kalshi", TK)
    b.apply_snapshot([(0.35, 1.0), (0.32, 5.0)], [(0.36, 2.0)])
    b.bids[0.35] = 0.0  # simulate an adapter bypassing set_level/apply_delta
    b.asks[0.36] = -1.0
    row = book_row(b, 10, 1, "ws")
    _assert_row_touch_ok(row)
    assert row["best_bid"] == 0.32 and row["best_ask"] is None


# --- artifact 2: staleness ----------------------------------------------------------


def test_rows_carry_last_update_and_age_heartbeat_shows_growing_age():
    s = kalshi.KalshiWsState()
    s.process(_snap(), recv_ns=100 * NS)
    b = s.books[TK]
    smp = Sampler(interval_s=1.0, heartbeat_s=60.0)
    r1 = smp.sample(b, 10, 100 * NS + NS // 2, "ws")
    assert r1["last_update_ns"] == 100 * NS and abs(r1["age_s"] - 0.5) < 1e-9
    # feed silent: the next row is a heartbeat with the same book and a large age
    assert not smp.due(b, 130 * NS) and smp.due(b, 161 * NS)
    r2 = smp.sample(b, 10, 161 * NS, "ws")
    assert r2["last_update_ns"] == 100 * NS and r2["age_s"] == 61.0
    assert r2["bid_px"] == r1["bid_px"]
    # a delta refreshes it
    s.process(_delta(2, "0.3200", "1.00", "yes"), recv_ns=170 * NS)
    r3 = smp.sample(b, 10, 171 * NS, "ws")
    assert r3["last_update_ns"] == 170 * NS and r3["age_s"] == 1.0


def test_parquet_sink_writes_staleness_and_compacts_parts_without_it(tmp_path):
    hour0 = 1_791_900_000 * NS - (1_791_900_000 % 3600) * NS  # an hour boundary
    # part file written by a recorder before the staleness columns existed
    old_schema = pa.schema([f for f in BOOK_SCHEMA if f.name not in ("last_update_ns", "age_s")])
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(hour0 / NS, tz=timezone.utc)
    d = tmp_path / "kalshi" / dt.strftime("%Y-%m-%d")
    d.mkdir(parents=True)
    old = {"venue": "kalshi", "market_id": TK, "outcome": "yes", "pair": None, "source": "ws",
           "ts_exchange_ns": None, "ts_recv_ns": hour0 + NS, "seq": 1, "n_updates": 0, "gap": False,
           "best_bid": 0.3, "best_ask": 0.4, "bid_px": [0.3], "bid_sz": [1.0], "ask_px": [0.4], "ask_sz": [1.0]}
    pq.write_table(pa.Table.from_pylist([old], schema=old_schema), d / f"books-{dt:%H}.part0000.parquet")
    sink = ParquetSink(tmp_path)
    b = OrderBook("kalshi", TK)
    b.apply_snapshot([(0.3, 1.0)], [(0.4, 1.0)], ts_recv_ns=hour0 + 2 * NS)
    sink.add_book(book_row(b, 10, hour0 + 5 * NS, "ws"))
    sink.flush()
    assert sink.compact_completed(hour0 + 3600 * NS) == 1
    t = pq.read_table(d / f"books-{dt:%H}.parquet").to_pylist()
    assert [r["age_s"] for r in t] == [None, 3.0]
    assert t[1]["last_update_ns"] == hour0 + 2 * NS


# --- Kalshi silence audit -------------------------------------------------------------


def test_audit_candidates_pick_only_silent_valid_books_and_rotate():
    books = {}
    for i, last in enumerate([0, 50, 190, 10]):
        b = OrderBook("kalshi", f"T{i}")
        b.apply_snapshot([(0.3, 1.0)], [(0.4, 1.0)], ts_recv_ns=last * NS)
        books[f"T{i}"] = b
    books["T3"].invalidate()
    now = 200 * NS
    got = kalshi.audit_candidates(books, now, 120 * NS, {}, limit=10, recheck_ns=600 * NS)
    assert got == ["T0", "T1"]  # T2 heard from 10 s ago, T3 invalid
    got = kalshi.audit_candidates(books, now, 120 * NS, {"T0": now - NS}, limit=10, recheck_ns=600 * NS)
    assert got == ["T1"]


def test_book_matches_rest_detects_missed_deltas():
    s = kalshi.KalshiWsState()
    s.process(_snap(), recv_ns=1)
    b = s.books[TK]
    rest_same = {"orderbook_fp": {"yes_dollars": [["0.3200", "20.20"], ["0.3500", "0.91"]],
                                  "no_dollars": [["0.6400", "51.00"]]}}
    assert kalshi.book_matches_rest(b, *kalshi.parse_rest_orderbook(rest_same))
    rest_moved = {"orderbook_fp": {"yes_dollars": [["0.3200", "20.20"], ["0.3700", "4.00"]],
                                   "no_dollars": [["0.6400", "51.00"]]}}
    assert not kalshi.book_matches_rest(b, *kalshi.parse_rest_orderbook(rest_moved))


class _Ctx:
    def __init__(self):
        self.stats = Counter()
        self.stop = asyncio.Event()

    async def sleep(self, s):
        return False


class _Shard:
    def __init__(self, state):
        self.state = state


def _run_audit(rest_payload, monkeypatch):
    s = kalshi.KalshiWsState()
    s.process(_snap(), recv_ns=1)

    async def fake_get(client, bucket, url, params=None):
        return rest_payload

    monkeypatch.setattr(kalshi, "_get", fake_get)
    r = asyncio.run(kalshi._audit_one(_Ctx(), object(), None, _Shard(s), TK))
    return r, s.books[TK]


def test_audit_one_confirms_quiet_book_and_refreshes_staleness(monkeypatch):
    r, b = _run_audit({"orderbook_fp": {"yes_dollars": [["0.3200", "20.20"], ["0.3500", "0.91"]],
                                        "no_dollars": [["0.6400", "51.00"]]}}, monkeypatch)
    assert r is True and b.ts_recv_ns > 1


def test_audit_one_flags_dead_subscription(monkeypatch):
    r, b = _run_audit({"orderbook_fp": {"yes_dollars": [["0.3900", "7.00"]],
                                        "no_dollars": [["0.6000", "9.00"]]}}, monkeypatch)
    assert r is False and b.ts_recv_ns == 1
