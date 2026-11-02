"""Unit tests for order-book state reconstruction and sequence-gap detection.

All fixtures below are trimmed copies of real payloads captured from the public
Kalshi / Polymarket endpoints on 2026-10-12. No network access.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from src.recorder.book import OrderBook
from src.recorder.sequence import SequenceTracker
from src.recorder.venues import kalshi, polymarket, polymarket_us


# ---------------------------------------------------------------------------
# Generic OrderBook
# ---------------------------------------------------------------------------


def test_snapshot_sorts_best_first_and_truncates():
    b = OrderBook("x", "m")
    b.apply_snapshot(
        bids=[(0.40, 10), (0.45, 5), (0.30, 1)],
        asks=[(0.60, 7), (0.55, 3), (0.99, 100)],
    )
    bp, bs, ap, asz = b.top(2)
    assert bp == [0.45, 0.40] and bs == [5, 10]
    assert ap == [0.55, 0.60] and asz == [3, 7]
    assert b.best_bid == 0.45 and b.best_ask == 0.55


def test_snapshot_replaces_previous_state():
    b = OrderBook("x", "m")
    b.apply_snapshot(bids=[(0.4, 1)], asks=[(0.6, 1)])
    b.apply_snapshot(bids=[(0.3, 2)], asks=[])
    assert b.top(10) == ([0.3], [2], [], [])
    assert b.best_ask is None


def test_zero_size_levels_are_dropped_in_snapshot():
    b = OrderBook("x", "m")
    b.apply_snapshot(bids=[(0.4, 0), (0.39, 3)], asks=[])
    assert b.top(10)[0] == [0.39]


def test_set_level_absolute_and_removal():
    b = OrderBook("x", "m")
    b.apply_snapshot(bids=[(0.4, 10)], asks=[(0.6, 10)])
    b.set_level("bid", 0.41, 4)
    b.set_level("ask", 0.6, 0)  # remove
    assert b.top(10) == ([0.41, 0.4], [4, 10], [], [])


def test_apply_delta_increments_and_removes():
    b = OrderBook("x", "m")
    b.apply_snapshot(bids=[(0.4, 10)], asks=[])
    assert b.apply_delta("bid", 0.4, -4) is True
    assert b.top(1)[1] == [6]
    assert b.apply_delta("bid", 0.4, -6) is True
    assert b.top(1)[0] == []
    assert b.apply_delta("bid", 0.42, 5) is True
    assert b.best_bid == 0.42


def test_apply_delta_negative_result_flags_inconsistency():
    b = OrderBook("x", "m")
    b.apply_snapshot(bids=[(0.4, 1)], asks=[])
    assert b.apply_delta("bid", 0.4, -5) is False
    assert b.valid is False
    assert b.top(1)[0] == []  # level removed, not negative


def test_float_price_keys_are_normalised():
    b = OrderBook("x", "m")
    b.apply_snapshot(bids=[(0.1 + 0.2, 1)], asks=[])
    b.set_level("bid", 0.3, 2)
    assert b.top(10) == ([0.3], [2], [], [])


def test_version_only_bumps_on_real_change():
    b = OrderBook("x", "m")
    b.apply_snapshot(bids=[(0.4, 1)], asks=[(0.6, 1)])
    v = b.version
    b.apply_snapshot(bids=[(0.4, 1)], asks=[(0.6, 1)])
    assert b.version == v
    b.set_level("bid", 0.4, 1)
    assert b.version == v
    b.set_level("bid", 0.4, 2)
    assert b.version == v + 1


# ---------------------------------------------------------------------------
# Sequence tracking
# ---------------------------------------------------------------------------


def test_sequence_first_ok_gap_stale():
    t = SequenceTracker()
    assert t.check("s1", 5).status == "first"
    assert t.check("s1", 6).status == "ok"
    r = t.check("s1", 9)
    assert r.status == "gap" and r.expected == 7 and r.got == 9 and r.missing == 2
    assert t.check("s1", 10).status == "ok"  # tracker resumes from latest
    assert t.check("s1", 10).status == "stale"
    assert t.check("s1", 3).status == "stale"
    assert t.gaps == 1


def test_sequence_streams_are_independent_and_reset():
    t = SequenceTracker()
    t.check("a", 1)
    t.check("b", 100)
    assert t.check("a", 2).status == "ok"
    assert t.check("b", 101).status == "ok"
    t.reset("a")
    assert t.check("a", 50).status == "first"
    t.reset()
    assert t.check("b", 7).status == "first"


# ---------------------------------------------------------------------------
# Kalshi
# ---------------------------------------------------------------------------

KALSHI_REST = {
    "orderbook_fp": {
        "no_dollars": [["0.3100", "4910.00"], ["0.3200", "151.00"], ["0.4100", "1194.00"]],
        "yes_dollars": [["0.0100", "100.00"]],
    }
}


def test_kalshi_rest_orderbook_converted_to_yes_terms():
    bids, asks = kalshi.parse_rest_orderbook(KALSHI_REST)
    b = OrderBook("kalshi", "KXFED-27APR-T6.00")
    b.apply_snapshot(bids, asks)
    bp, bs, ap, asz = b.top(10)
    assert bp == [0.01] and bs == [100.0]
    # NO bid at 0.41 => YES ask at 0.59 (best), then 0.68, 0.69
    assert ap == [0.59, 0.68, 0.69]
    assert asz == [1194.0, 151.0, 4910.0]


def test_kalshi_rest_legacy_cents_format():
    bids, asks = kalshi.parse_rest_orderbook({"orderbook": {"yes": [[40, 10]], "no": [[55, 3]]}})
    assert bids == [(0.40, 10.0)]
    assert asks == [(0.45, 3.0)]


def _ksnap(seq, sid=2):
    return {
        "type": "orderbook_snapshot",
        "sid": sid,
        "seq": seq,
        "msg": {
            "market_ticker": "FED-23DEC-T3.00",
            "market_id": "9b0f6b43",
            "yes_dollars_fp": [["0.0800", "300.00"], ["0.2200", "333.00"]],
            "no_dollars_fp": [["0.5400", "20.00"], ["0.5600", "146.00"]],
        },
        "sending_ts_ms": 1669149841234,
    }


def _kdelta(seq, price, delta, side, sid=2):
    return {
        "type": "orderbook_delta",
        "sid": sid,
        "seq": seq,
        "msg": {
            "market_ticker": "FED-23DEC-T3.00",
            "market_id": "9b0f6b43",
            "price_dollars": price,
            "delta_fp": delta,
            "side": side,
            "ts_ms": 1669149841000,
        },
        "sending_ts_ms": 1669149841123,
    }


def test_kalshi_ws_snapshot_plus_deltas_reconstructs_book():
    s = kalshi.KalshiWsState()
    ev = s.process(_ksnap(1), recv_ns=1)
    assert ev.book_updates == ["FED-23DEC-T3.00"] and not ev.resync
    s.process(_kdelta(2, "0.2200", "-33.00", "yes"), recv_ns=2)
    s.process(_kdelta(3, "0.5600", "-146.00", "no"), recv_ns=3)  # removes YES ask 0.44
    s.process(_kdelta(4, "0.2500", "10.00", "yes"), recv_ns=4)
    b = s.books["FED-23DEC-T3.00"]
    assert b.top(10) == ([0.25, 0.22, 0.08], [10.0, 300.0, 300.0], [0.46], [20.0])
    assert b.seq == 4
    assert b.ts_exchange_ns == 1669149841000 * 1_000_000


def test_kalshi_ws_gap_requests_resync_and_invalidates():
    s = kalshi.KalshiWsState()
    s.process(_ksnap(1), recv_ns=1)
    ev = s.process(_kdelta(3, "0.2200", "-33.00", "yes"), recv_ns=2)
    assert ev.resync is True
    assert s.books["FED-23DEC-T3.00"].valid is False
    assert s.seq.gaps == 1
    # a fresh snapshot (after resubscribe) makes the book valid again
    s.process(_ksnap(10, sid=5), recv_ns=3)
    assert s.books["FED-23DEC-T3.00"].valid is True


def test_kalshi_ws_delta_before_snapshot_is_ignored_and_resyncs():
    s = kalshi.KalshiWsState()
    ev = s.process(_kdelta(1, "0.2200", "5.00", "yes"), recv_ns=1)
    assert ev.resync is True
    assert "FED-23DEC-T3.00" not in s.books or not s.books["FED-23DEC-T3.00"].valid


def test_kalshi_ws_trade_message():
    s = kalshi.KalshiWsState()
    ev = s.process(
        {
            "type": "trade",
            "sid": 11,
            "msg": {
                "trade_id": "abc",
                "market_ticker": "FED-23DEC-T3.00",
                "yes_price_dollars": "0.3600",
                "no_price_dollars": "0.6400",
                "count_fp": "136.00",
                "taker_side": "no",
                "ts": 1669149841,
            },
        },
        recv_ns=99,
    )
    assert len(ev.trades) == 1
    t = ev.trades[0]
    assert t["market_id"] == "FED-23DEC-T3.00" and t["price"] == 0.36 and t["size"] == 136.0
    assert t["taker_side"] == "no" and t["trade_id"] == "abc"
    assert t["ts_exchange_ns"] == 1669149841 * 1_000_000_000 and t["ts_recv_ns"] == 99


def test_kalshi_rest_trades_parse():
    rows = kalshi.parse_rest_trades(
        {
            "trades": [
                {
                    "count_fp": "61.00",
                    "created_time": "2026-10-07T14:31:34.372341Z",
                    "no_price_dollars": "0.9800",
                    "taker_side": "no",
                    "ticker": "KXFED-27APR-T5.00",
                    "trade_id": "07254afd",
                    "yes_price_dollars": "0.0200",
                }
            ]
        },
        recv_ns=5,
    )
    assert rows[0]["price"] == 0.02 and rows[0]["size"] == 61.0
    assert rows[0]["ts_exchange_ns"] == 1791383494372341000


# ---------------------------------------------------------------------------
# Polymarket (global CLOB)
# ---------------------------------------------------------------------------

TOK = "23250308899791469248496736713890367877528653645908077470390716328108040647589"

PM_BOOK = {
    "event_type": "book",
    "market": "0xb850",
    "asset_id": TOK,
    "timestamp": "1791838886143",
    "hash": "9f21",
    "bids": [{"price": "0.45", "size": "100"}, {"price": "0.47", "size": "394923.83"}, {"price": "0.46", "size": "5"}],
    "asks": [{"price": "0.99", "size": "26909"}, {"price": "0.49", "size": "50"}, {"price": "0.48", "size": "12"}],
}


def test_polymarket_rest_book_parse():
    p = dict(PM_BOOK)
    p.pop("event_type")
    bids, asks, ts_ns = polymarket.parse_book(p)
    b = OrderBook("polymarket", TOK)
    b.apply_snapshot(bids, asks)
    assert b.top(2) == ([0.47, 0.46], [394923.83, 5.0], [0.48, 0.49], [12.0, 50.0])
    assert ts_ns == 1791838886143 * 1_000_000


def test_polymarket_ws_book_then_price_change_v2():
    s = polymarket.PolymarketWsState()
    ev = s.process([PM_BOOK], recv_ns=1)
    assert ev.book_updates == [TOK]
    ev = s.process(
        {
            "event_type": "price_change",
            "market": "0xb850",
            "timestamp": "1791838890000",
            "price_changes": [
                {"asset_id": TOK, "price": "0.47", "size": "0", "side": "BUY", "hash": "h1",
                 "best_bid": "0.46", "best_ask": "0.48"},
                {"asset_id": TOK, "price": "0.475", "size": "20", "side": "SELL", "hash": "h1",
                 "best_bid": "0.46", "best_ask": "0.475"},
                {"asset_id": "999", "price": "0.5", "size": "1", "side": "BUY", "hash": "h2"},
            ],
        },
        recv_ns=2,
    )
    assert TOK in ev.book_updates
    b = s.books[TOK]
    assert b.top(2) == ([0.46, 0.45], [5.0, 100.0], [0.475, 0.48], [20.0, 12.0])
    assert b.ts_exchange_ns == 1791838890000 * 1_000_000
    # best_bid/best_ask cross-check recorded as consistent
    assert s.bbo_mismatches == 0
    # unknown asset without snapshot is ignored (no crash)
    assert "999" not in s.books or not s.books["999"].valid


def test_polymarket_ws_bbo_mismatch_counted_when_group_settles():
    s = polymarket.PolymarketWsState()
    s.process(PM_BOOK, recv_ns=1)
    s.process(
        {"event_type": "price_change", "timestamp": "1", "price_changes": [
            {"asset_id": TOK, "price": "0.30", "size": "1", "side": "BUY", "best_bid": "0.50", "best_ask": "0.48"}
        ]},
        recv_ns=2,
    )
    assert s.bbo_mismatches == 0  # group still open (venue may send the rest of it)
    s.verify_pending(now_ns=2 + polymarket.GROUP_SETTLE_NS)
    assert s.bbo_mismatches == 1
    assert s.books[TOK].valid is False


def test_polymarket_ws_trade_and_pong_ignored():
    s = polymarket.PolymarketWsState()
    ev = s.process(
        {"event_type": "last_trade_price", "market": "0xb850", "asset_id": TOK, "price": "0.47",
         "size": "219.21", "fee_rate_bps": "0", "side": "BUY", "timestamp": "1750428146322",
         "transaction_hash": "0xabc"},
        recv_ns=7,
    )
    assert ev.trades[0]["price"] == 0.47 and ev.trades[0]["size"] == 219.21
    assert ev.trades[0]["taker_side"] == "buy"
    assert ev.trades[0]["ts_exchange_ns"] == 1750428146322 * 1_000_000
    assert s.process_raw("PONG", recv_ns=8).trades == []


# ---------------------------------------------------------------------------
# Polymarket US (public gateway REST)
# ---------------------------------------------------------------------------


def test_polymarket_us_book_parse():
    payload = {
        "marketData": {
            "marketSlug": "tec-mlb-nlchamp-2026-09-27-lad",
            "bids": [{"px": {"value": "0.4790", "currency": "USD"}, "qty": "1123.0000"},
                     {"px": {"value": "0.4800", "currency": "USD"}, "qty": "482.0000"}],
            "offers": [{"px": {"value": "0.5100", "currency": "USD"}, "qty": "141.0000"},
                       {"px": {"value": "0.4820", "currency": "USD"}, "qty": "957.0000"}],
            "state": "MARKET_STATE_OPEN",
            "transactTime": "2026-10-12T21:01:36.863795210Z",
        }
    }
    bids, asks, ts_ns = polymarket_us.parse_book(payload)
    b = OrderBook("polymarket_us", "tec")
    b.apply_snapshot(bids, asks)
    assert b.top(1) == ([0.48], [482.0], [0.482], [957.0])
    assert ts_ns == 1791838896863795210


# ---------------------------------------------------------------------------
# Polymarket: one book update split across several price_change messages
# ---------------------------------------------------------------------------

_FIXTURE = Path(__file__).parent / "fixtures" / "recorder_polymarket_split_update.jsonl"


def _replay(state, path=_FIXTURE):
    import json as _json

    last = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        d = _json.loads(line)
        state.process_raw(d["raw"], d["recv_ns"])
        last = d["recv_ns"]
    return last


def test_polymarket_split_update_replay_has_no_false_mismatch():
    """Captured live 2026-10-12 (NLCS LAD-MIL). The venue sends ONE matching-engine update
    as two price_change messages with the same timestamp; best_bid/best_ask on the FIRST
    already reflect the state after the SECOND. Checking per message raised a false
    mismatch + REST resync."""
    s = polymarket.PolymarketWsState()
    last = _replay(s)
    s.verify_pending(last + 10**10)
    assert s.bbo_mismatches == 0
    lad, mil = ("90674515154806304598327967883140350059806255690346797701826119081248016015646",
                "1145578312860253508259027519584721473567818987425729724913077142408562193504")
    assert s.books[lad].valid and s.books[mil].valid
    assert (s.books[lad].best_bid, s.books[lad].best_ask) == (0.34, 0.35)
    assert (s.books[mil].best_bid, s.books[mil].best_ask) == (0.65, 0.66)


def _pc(ts, *changes):
    return {"event_type": "price_change", "market": "0xb850", "timestamp": ts,
            "price_changes": [dict(asset_id=TOK, hash="h", **c) for c in changes]}


def test_polymarket_bbo_checked_at_end_of_same_timestamp_group():
    s = polymarket.PolymarketWsState()
    s.process(PM_BOOK, recv_ns=1)  # bids .47/.46/.45, asks .48/.49/.99
    # buy order lifts the .48 ask: first msg adds the bid, second removes the ask.
    s.process(_pc("1791838890000", {"price": "0.48", "size": "3", "side": "BUY",
                                    "best_bid": "0.48", "best_ask": "0.49"}), recv_ns=2)
    assert s.bbo_mismatches == 0  # intermediate state not judged
    s.process(_pc("1791838890000", {"price": "0.48", "size": "0", "side": "SELL",
                                    "best_bid": "0.48", "best_ask": "0.49"}), recv_ns=3)
    s.process(_pc("1791838890500", {"price": "0.44", "size": "1", "side": "BUY",
                                    "best_bid": "0.48", "best_ask": "0.49"}), recv_ns=4)
    s.verify_pending(10**12)
    assert s.bbo_mismatches == 0
    assert s.books[TOK].valid


def test_polymarket_pending_group_excluded_from_sampling_until_verified():
    s = polymarket.PolymarketWsState()
    s.process(PM_BOOK, recv_ns=1)
    s.process(_pc("5", {"price": "0.48", "size": "3", "side": "BUY",
                        "best_bid": "0.48", "best_ask": "0.49"}), recv_ns=2)
    assert TOK not in s.sampleable_books(now_ns=3)
    s.verify_pending(now_ns=2 + 10**9)  # group closed by age -> mismatch is real
    assert s.bbo_mismatches == 1 and not s.books[TOK].valid


def test_polymarket_stale_rest_snapshot_does_not_overwrite_newer_ws_book():
    s = polymarket.PolymarketWsState()
    s.process(PM_BOOK, recv_ns=1)
    s.process(_pc("1791838890000", {"price": "0.47", "size": "0", "side": "BUY",
                                    "best_bid": "0.46", "best_ask": "0.48"}), recv_ns=2)
    old = dict(PM_BOOK, timestamp="1791838889000")
    old.pop("event_type")
    s.apply_rest(old, recv_ns=3)
    assert s.books[TOK].best_bid == 0.46
    s.verify_pending(10**12)
    assert s.bbo_mismatches == 0


def test_polymarket_best_bid_ask_event_recorded():
    s = polymarket.PolymarketWsState()
    ev = s.process({"market": "0xb850", "asset_id": TOK, "best_bid": "0.34", "best_ask": "0.35",
                    "spread": "0.01", "timestamp": "1791843687408", "event_type": "best_bid_ask"},
                   recv_ns=9)
    assert len(ev.bbo) == 1
    r = ev.bbo[0]
    assert (r["venue"], r["market_id"], r["best_bid"], r["best_ask"]) == ("polymarket", TOK, 0.34, 0.35)
    assert r["ts_exchange_ns"] == 1791843687408 * 1_000_000 and r["ts_recv_ns"] == 9
