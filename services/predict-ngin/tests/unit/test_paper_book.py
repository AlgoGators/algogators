"""Paper fills: queue position, trade-through, trade dedupe, batched books (no network)."""

from __future__ import annotations

import time

from predict_ngin.execution.orders import OrderIntent
from predict_ngin.execution.paper import PaperBook, _kalshi_top

M = "KXINXU-TEST-T1"


class FakeKalshi(PaperBook):
    """PaperBook whose GETs answer from in-memory books and trades."""

    def __init__(self):
        super().__init__("kalshi", max_rps=1e9)
        self.book = {
            "yes_bid_dollars": "0.40",
            "yes_bid_size_fp": "5",
            "yes_ask_dollars": "0.50",
            "yes_ask_size_fp": "7",
        }
        self.tape: list = []
        self.calls: list = []

    def _get(self, url, **params):
        self.calls.append((url.rsplit("/", 1)[-1], params))
        if url.endswith("/markets"):
            return {"markets": [{"ticker": t, **self.book} for t in params["tickers"].split(",")]}
        if url.endswith("/markets/trades"):
            return {"trades": list(self.tape), "cursor": ""}
        raise AssertionError(url)


def trade(tid, price, size, taker_side, ts=None, ticker=M):
    t = ts if ts is not None else time.time() + 1
    iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int(t % 1 * 1e6):06d}Z"
    return {
        "trade_id": tid,
        "ticker": ticker,
        "yes_price_dollars": f"{price:.4f}",
        "count_fp": str(size),
        "taker_side": taker_side,
        "created_time": iso,
    }


def bid(px, qty=10):
    return OrderIntent("kalshi", M, "yes", px, qty)


def test_join_waits_behind_displayed_queue():
    pb = FakeKalshi()
    ack = pb.place(bid(0.40))  # joins 5 ahead at the best bid
    assert pb.orders[ack.order_id].queue_ahead == 5
    pb.tape = [trade("a", 0.40, 3, "no")]  # taker sold YES 3 at our price: queue 5 -> 2
    assert pb.poll() == []
    pb.tape.append(trade("b", 0.40, 4, "no"))  # 2 more ahead, then 2 for us
    fills = pb.poll()
    assert [f["quantity"] for f in fills] == [2.0]
    assert pb.orders[ack.order_id].remaining == 8


def test_trade_through_fills_all_and_trades_count_once():
    pb = FakeKalshi()
    pb.place(bid(0.41))  # stepped inside: first in line
    pb.tape = [trade("a", 0.40, 1, "no")]  # sold below our price: we would have been hit first
    fills = pb.poll()
    assert sum(f["quantity"] for f in fills) == 10
    assert pb.orders == {}
    ack = pb.place(bid(0.41))
    assert pb.poll() == []  # the same trade id is not applied again
    assert pb.orders[ack.order_id].remaining == 10


def test_trades_before_the_order_and_wrong_side_are_ignored():
    pb = FakeKalshi()
    old = time.time() - 30
    pb.place(bid(0.41))
    pb.tape = [trade("old", 0.39, 5, "no", ts=old), trade("buy", 0.45, 5, "yes")]
    assert pb.poll() == []


def test_ask_side_is_a_no_buy():
    pb = FakeKalshi()
    pb.place(OrderIntent("kalshi", M, "no", 0.51, 10))  # rests as a YES ask at 0.49
    pb.tape = [trade("a", 0.49, 4, "yes")]
    fills = pb.poll()
    assert [f["quantity"] for f in fills] == [4.0]


def test_post_only_rejects_crossing_and_books_are_batched():
    pb = FakeKalshi()
    ack = pb.place(bid(0.50))
    assert ack.order_id is None and ack.status == "rejected"
    pb.prefetch([M, "OTHER", M])
    names = [c[0] for c in pb.calls]
    assert names.count("markets") == 2  # place() fetched once, prefetch once for both tickers
    assert pb.calls[-1][1]["tickers"] == f"{M},OTHER"


def test_kalshi_top_parsing():
    assert _kalshi_top({"yes_bid_dollars": "0.0000", "yes_ask_dollars": "1.0000"}) == (
        None,
        0.0,
        None,
        0.0,
    )
    assert _kalshi_top(
        {
            "yes_bid_dollars": "0.3",
            "yes_bid_size_fp": "2",
            "yes_ask_dollars": "0.6",
            "yes_ask_size_fp": "3",
        }
    ) == (0.3, 2.0, 0.6, 3.0)
