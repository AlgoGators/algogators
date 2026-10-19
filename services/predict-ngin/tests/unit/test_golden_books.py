"""Golden tests for step 5.7 (order book -> predict_ngin.books).

The recorder runs 24/7 on atlas and its Parquet output must stay byte-identical. These tests replay
Kalshi WS delta sequences through the recorder's own state machine and sampler twice, once with
the frozen pre-change book (tests/unit/_legacy_recorder_book.py) and once with the shim (the
canonical ``predict_ngin.books.OrderBook``), and compare the sampled rows and the Parquet bytes:

* a synthetic sequence (snapshots, sub-cent prices, fractional sizes, negative levels, seq gaps);
* when the local recorder data is present (``data`` marker), sequences derived from recorded book
  rows; the replayed rows also reproduce the recorded levels.

The adapters from the other book types are checked on small examples.
"""
from __future__ import annotations

import glob
import io
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _legacy_recorder_book as LG  # noqa: E402

from predict_ngin.books import OrderBook, complement, from_kalshi_bids, walk_levels  # noqa: E402
from src.recorder import book as recorder_book  # noqa: E402
from src.recorder.sampler import Sampler  # noqa: E402
from src.recorder.venues import kalshi as KV  # noqa: E402
from src.recorder.writer import BOOK_SCHEMA  # noqa: E402

_NS = 1_000_000_000


def _legacy_to_yes_terms(yes_bids, no_bids):  # frozen src/recorder/venues/kalshi.py:61
    return list(yes_bids), [(LG.norm_px(1.0 - p), s) for p, s in no_bids]


def replay(msgs, legacy: bool, monkeypatch, depth: int = 10, every_msg: bool = False):
    """Feed ``(recv_ns, msg)`` pairs through KalshiWsState + Sampler; return the sampled rows."""
    with monkeypatch.context() as m:
        if legacy:
            m.setattr(KV, "OrderBook", LG.OrderBook)
            m.setattr(KV, "norm_px", LG.norm_px)
            m.setattr(KV, "to_yes_terms", _legacy_to_yes_terms)
        st = KV.KalshiWsState()
        smp = Sampler(interval_s=0.0, heartbeat_s=60.0)
        rows = []
        for recv_ns, msg in msgs:
            ev = st.process(msg, recv_ns)
            for tk in dict.fromkeys(ev.book_updates):
                b = st.books[tk]
                if every_msg or smp.due(b, recv_ns):
                    rows.append(smp.sample(b, depth, recv_ns, "ws"))
            for tk in ev.resync_ids:  # what the feed does after a REST resync: a fresh snapshot
                b = st.books.get(tk)
                if b is not None:
                    b.apply_snapshot(*KV.to_yes_terms([(0.5, 1.0)], [(0.4, 2.0)]), ts_recv_ns=recv_ns)
                    rows.append(smp.sample(b, depth, recv_ns, "rest"))
        return rows


def parquet_bytes(rows) -> bytes:
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows, schema=BOOK_SCHEMA), buf, compression="zstd")
    return buf.getvalue()


def synthetic_stream(seed=7, n=6000):
    rng = random.Random(seed)
    tickers = [f"KXT-26OCT15-T{i}" for i in range(6)]
    seq, t, out = 0, 1_760_000_000 * _NS, []

    def lv():
        return [[f"{rng.choice([rng.randint(1, 99) / 100, rng.randint(10, 999) / 1000]):.4f}",
                 f"{rng.choice([rng.randint(1, 500), rng.random() * 300]):.2f}"] for _ in range(rng.randint(0, 8))]

    for tk in tickers:
        seq += 1
        out.append((t, {"type": "orderbook_snapshot", "sid": 1, "seq": seq,
                        "msg": {"market_ticker": tk, "yes_dollars_fp": lv(), "no_dollars_fp": lv()}}))
    for i in range(n):
        t += rng.randint(1, 900) * 1_000_000
        tk = rng.choice(tickers)
        r = rng.random()
        seq += 1
        if r < 0.003:
            seq += 2  # sequence gap -> resync
        if r < 0.03:
            out.append((t, {"type": "orderbook_snapshot", "sid": 1, "seq": seq,
                            "msg": {"market_ticker": tk, "yes_dollars_fp": lv(), "no_dollars_fp": lv()}}))
            continue
        px = rng.choice([rng.randint(1, 99) / 100, rng.randint(10, 999) / 1000, 0.1 + 0.2])
        delta = rng.choice([rng.randint(-50, 80), round(rng.uniform(-40, 60), 2), 0.0])
        body = {"market_ticker": tk, "side": rng.choice(["yes", "no", "yes", "no", "maybe"]),
                "delta_fp": f"{delta}", "ts_ms": t // 1_000_000}
        if rng.random() < 0.2:
            body["price"] = int(round(px * 100))
        else:
            body["price_dollars"] = f"{px}"
        out.append((t, {"type": "orderbook_delta", "sid": 1, "seq": seq, "msg": body}))
    return out


def test_recorder_book_is_the_core_type():
    assert recorder_book.OrderBook is OrderBook
    assert KV.OrderBook is OrderBook
    assert recorder_book.norm_px(0.1 + 0.2) == LG.norm_px(0.1 + 0.2) == 0.3


def test_synthetic_replay_rows_and_parquet_identical(monkeypatch):
    msgs = synthetic_stream()
    new = replay(msgs, False, monkeypatch)
    old = replay(msgs, True, monkeypatch)
    assert len(new) > 1000
    assert any(r["gap"] for r in new) and any(r["source"] == "rest" for r in new)
    assert new == old
    assert parquet_bytes(new) == parquet_bytes(old)
    every_new = replay(msgs, False, monkeypatch, depth=3, every_msg=True)
    every_old = replay(msgs, True, monkeypatch, depth=3, every_msg=True)
    assert every_new == every_old


def test_legacy_methods_identical_on_random_ops():
    rng = random.Random(3)
    a, b = LG.OrderBook("v", "m"), OrderBook("v", "m")
    for _ in range(20000):
        op = rng.random()
        side = rng.choice(["bid", "ask", "buy", "SELL", "bids", "asks"])
        px = rng.choice([rng.randint(1, 99) / 100, rng.random(), 0.1 + 0.2])
        stamp = dict(seq=rng.choice([None, rng.randint(0, 9)]), ts_exchange_ns=rng.choice([None, 5]),
                     ts_recv_ns=rng.choice([None, 7]))
        if op < 0.05:
            lvls = [(rng.random(), rng.choice([0, -1, rng.random() * 9])) for _ in range(rng.randint(0, 5))]
            for x in (a, b):
                x.apply_snapshot(lvls, lvls[::-1], **stamp)
        elif op < 0.4:
            sz = rng.choice([0, -2, rng.randint(1, 30), rng.random()])
            for x in (a, b):
                x.set_level(side, px, sz, **stamp)
        elif op < 0.98:
            d = rng.choice([0, rng.randint(-20, 20), rng.uniform(-5, 5)])
            assert a.apply_delta(side, px, d, **stamp) == b.apply_delta(side, px, d, **stamp)
        else:
            a.invalidate()
            b.invalidate()
        for s in OrderBook.__slots__:
            assert getattr(a, s) == getattr(b, s), s
        assert a.top(4) == b.top(4) and a.best_bid == b.best_bid and a.best_ask == b.best_ask


def _recorded_stream(df):
    """Kalshi WS messages that rebuild each market's recorded top-of-book rows in order."""
    msgs, seq = [], 0
    for tk, g in df.groupby("market_id", observed=True, sort=True):
        prev = None
        for r in g.itertuples(index=False):
            bids = dict(zip(map(float, r.bid_px), map(float, r.bid_sz)))
            asks = dict(zip(map(float, r.ask_px), map(float, r.ask_sz)))
            t = int(r.ts_recv_ns)
            if prev is None:
                seq += 1
                msgs.append((t, {"type": "orderbook_snapshot", "sid": 1, "seq": seq, "msg": {
                    "market_ticker": tk, "yes_dollars_fp": [[str(p), str(s)] for p, s in bids.items()],
                    "no_dollars_fp": [[str(1.0 - p), str(s)] for p, s in asks.items()]}}))
            else:
                for side, cur, old in (("yes", bids, prev[0]), ("no", asks, prev[1])):
                    for p in sorted(set(cur) | set(old)):
                        d = cur.get(p, 0.0) - old.get(p, 0.0)
                        if d:
                            seq += 1
                            msgs.append((t, {"type": "orderbook_delta", "sid": 1, "seq": seq, "msg": {
                                "market_ticker": tk, "side": side, "delta_fp": repr(d),
                                "price_dollars": repr(p if side == "yes" else 1.0 - p)}}))
            prev = (bids, asks)
    return msgs


@pytest.mark.data
def test_recorded_kalshi_sequences_replay_identically(monkeypatch):
    files = sorted(glob.glob(str(ROOT / "data" / "live_books" / "kalshi" / "*" / "books-*.parquet")))
    if not files:
        pytest.skip("no recorded Kalshi book files")
    f = files[len(files) // 2]
    t = pq.read_table(f, columns=["market_id", "ts_recv_ns", "bid_px", "bid_sz", "ask_px", "ask_sz"])
    df = t.to_pandas()
    keep = sorted(df.market_id.astype(str).unique())[:40]
    df = df[df.market_id.astype(str).isin(keep)].sort_values(["market_id", "ts_recv_ns"], kind="mergesort")
    df = df.groupby("market_id", observed=True).head(250)
    msgs = _recorded_stream(df)
    new = replay(msgs, False, monkeypatch, depth=1000)
    old = replay(msgs, True, monkeypatch, depth=1000)
    assert len(new) > 100
    assert new == old
    assert parquet_bytes(new) == parquet_bytes(old)
    # and the replay rebuilds the recorded levels (within float noise from the size deltas)
    last = {}
    for r in new:
        last[r["market_id"]] = r
    for tk, g in df.groupby("market_id", observed=True):
        rec = g.iloc[-1]
        got = last[str(tk)]
        assert got["bid_px"] == list(rec.bid_px) and got["ask_px"] == [round(p, 6) for p in rec.ask_px]
        assert got["bid_sz"] == pytest.approx(list(rec.bid_sz), abs=1e-6)
        assert got["ask_sz"] == pytest.approx(list(rec.ask_sz), abs=1e-6)


# ------------------------------------------------------------------ additions and adapters
def test_views_walk_and_complement():
    b = OrderBook.from_levels("kalshi", "M", [(0.40, 10), (0.42, 5)], [(0.45, 3), (0.47, 10)])
    assert b.valid and b.levels("bid") == [(0.42, 5.0), (0.40, 10.0)]
    assert b.mid == pytest.approx(0.435) and b.spread == pytest.approx(0.03) and not b.is_crossed()
    assert b.walk("buy", 5) == (5.0, pytest.approx(3 * 0.45 + 2 * 0.47))
    assert b.walk("sell", 100) == (15.0, pytest.approx(5 * 0.42 + 10 * 0.40))
    assert b.depth("ask", 0.02) == 13.0 and b.depth("bid", 0.0) == 5.0
    n = b.no_side()
    assert n.outcome == "no" and n.best_bid == 0.55 and n.best_ask == 0.58
    assert complement([(0.3, 1.0)]) == [(0.7, 1.0)]
    assert walk_levels([], 5) == (0.0, 0.0)
    with pytest.raises(ValueError):
        b.walk("sideways", 1)


def test_lip_pilot_book_adapter():
    from src.live.lip_pilot.book import Book
    lb = Book.from_api("KXA-1", {"orderbook_fp": {"yes_dollars": [["0.40", "10"], ["0.42", "5"]],
                                                  "no_dollars": [["0.55", "7"]]}}, ts=1_760_000_000.25)
    ob = lb.to_order_book()
    assert ob.best_bid == lb.best_bid("yes") == 0.42
    assert ob.best_ask == pytest.approx(1 - lb.best_bid("no"))
    assert ob.mid == pytest.approx(lb.mid_yes())
    assert ob.ts_recv_ns == 1_760_000_000_250_000_000
    assert from_kalshi_bids("KXA-1", lb.yes, lb.no).top(5) == ob.top(5)


def test_clob_snapshot_adapters():
    clob = pytest.importorskip("src.backtest_v3.data.clob_book")  # private module: skipped in a public clone
    BookLevel, BookSnapshot = clob.BookLevel, clob.BookSnapshot
    snap = BookSnapshot("0xabc", 1_700_000_000, 0.5, [BookLevel(0.49, 100.0), BookLevel(0.48, 50.0)],
                        [BookLevel(0.51, 80.0)], 1e6, 0.001)
    ob = snap.to_order_book()
    assert (ob.best_bid, ob.best_ask, ob.market_id) == (snap.best_bid, snap.best_ask, "0xabc")
    assert ob.spread == pytest.approx(snap.spread) and ob.ts_exchange_ns == 1_700_000_000 * _NS
    from src.trading.live.order_router import OrderBook as RouterBook
    rb = RouterBook("tok", datetime(2026, 10, 15, tzinfo=timezone.utc), [(0.30, 10.0)], [(0.35, 4.0)],
                    0.30, 0.35, 0.05, 0.325)
    ob2 = rb.to_order_book()
    assert (ob2.best_bid, ob2.best_ask, ob2.mid) == (rb.best_bid, rb.best_ask, pytest.approx(rb.midpoint))
