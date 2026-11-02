"""Unit tests for series-based discovery, rollover (universe), shard assignment, the
dynamic Kalshi WS state machine and the RTDS parser. No network.

Market rows are trimmed copies of real ``GET /markets`` / Gamma payloads (2026-10-13).
"""

from __future__ import annotations

import json

import pytest

from src.recorder.config import ConfigError, DiscoveryGroup, MarketRef, load_config, parse_config
from src.recorder.series_discovery import (
    active_series_in_category,
    implied_center,
    kalshi_mid,
    kalshi_strike,
    market_row,
    nearest_strikes,
    select_kalshi,
    select_polymarket,
)
from src.recorder.universe import DynMarket, Universe, assign_shards
from src.recorder.venues import kalshi, polymarket_rtds

NS = 1_000_000_000
NOW = 1791886800 * NS  # 2026-10-13T10:20:00Z


def km(ticker, strike, bid, ask, close="2026-10-13T11:00:00Z", event="KXBTCD-26OCT1307", st="greater",
       cap=None, status="active"):
    return {"ticker": ticker, "event_ticker": event, "strike_type": st, "floor_strike": strike,
            "cap_strike": cap, "yes_bid_dollars": f"{bid:.4f}", "yes_ask_dollars": f"{ask:.4f}",
            "last_price_dollars": "0.0000", "close_time": close, "status": status,
            "title": "Bitcoin price on Oct 13, 2026?", "yes_sub_title": f"${strike} or above"}


def ladder(center_idx=10, n=21, event="KXBTCD-26OCT1307", close="2026-10-13T11:00:00Z"):
    """Directional ladder: P(above K) falls through 0.5 at index ``center_idx``."""
    out = []
    for i in range(n):
        p = min(0.99, max(0.01, 0.5 - 0.1 * (i - center_idx)))
        out.append(km(f"{event}-T{90000 + 250 * i}", 90000 + 250 * i, max(0.0, p - 0.01), min(1.0, p + 0.01),
                      close=close, event=event))
    return out


def group(**kw):
    base = dict(name="g", venue="kalshi", series=("KXBTCD",))
    base.update(kw)
    return DiscoveryGroup(**base)


# ---------------------------------------------------------------------------
# strike / center helpers
# ---------------------------------------------------------------------------


def test_strike_and_mid_parsing():
    assert kalshi_strike(km("a", 100.0, 0.4, 0.42)) == 100.0
    assert kalshi_strike({"strike_type": "between", "floor_strike": 10, "cap_strike": 20}) == 15
    assert kalshi_strike({"strike_type": "custom"}) is None
    assert kalshi_mid(km("a", 1, 0.40, 0.42)) == pytest.approx(0.41)
    # unquoted (bid 0, ask 1) and no last price -> None
    assert kalshi_mid({"yes_bid_dollars": "0", "yes_ask_dollars": "1", "last_price_dollars": "0"}) is None


def test_implied_center_directional_and_range():
    assert implied_center(ladder(center_idx=7)) == 90000 + 250 * 7
    rng = [{"strike_type": "between", "floor_strike": k, "cap_strike": k + 25, "yes_bid_dollars": str(b),
            "yes_ask_dollars": str(b + 0.02)} for k, b in ((6000, 0.05), (6025, 0.40), (6050, 0.20))]
    assert implied_center(rng) == 6037.5
    # nothing quoted -> median strike
    dead = [{"strike_type": "greater", "floor_strike": k, "yes_bid_dollars": "0", "yes_ask_dollars": "1"} for k in (1, 2, 3)]
    assert implied_center(dead) == 2


def test_nearest_strikes_per_event_and_hysteresis():
    a = ladder(center_idx=10, event="E1")
    b = ladder(center_idx=3, event="E2")
    sel = nearest_strikes(a + b, 4)
    by = {}
    for m in sel:
        by.setdefault(m["event_ticker"], []).append(m["floor_strike"])
    assert sorted(by["E1"]) == [90000 + 250 * i for i in (8, 9, 10, 11)] or len(by["E1"]) == 4
    assert 90000 + 250 * 10 in by["E1"] and 90000 + 250 * 3 in by["E2"]
    assert len(by["E2"]) == 4
    # an already-subscribed strike ranked 5th survives (within ceil(4*1.5)=6) ...
    ranked5 = sorted(a, key=lambda m: abs(m["floor_strike"] - (90000 + 2500)))[4]["ticker"]
    assert ranked5 in {m["ticker"] for m in nearest_strikes(a, 4, keep=[ranked5])}
    # ... but one ranked 10th does not
    far = sorted(a, key=lambda m: abs(m["floor_strike"] - (90000 + 2500)))[9]["ticker"]
    assert far not in {m["ticker"] for m in nearest_strikes(a, 4, keep=[far])}


def test_select_kalshi_close_filter_and_meta():
    soon = ladder(event="H11", close="2026-10-13T11:00:00Z")
    later = ladder(event="D21", close="2026-10-13T21:00:00Z")
    past = [km("OLD-1", 1, 0.4, 0.5, close="2026-10-13T10:00:00Z", event="OLD")]
    settled = [km("SET-1", 1, 0.4, 0.5, status="settled", event="SET")]
    g = group(name="k-crypto-hourly", max_hours_to_close=1.2, nearest_strikes=3, depth=7, heartbeat_s=30)
    out = select_kalshi(soon + later + past + settled, g, NOW)
    assert {d.info["event"] for d in out} == {"H11"} and len(out) == 3
    d = out[0]
    assert d.ref.venue == "kalshi" and d.ref.group == "k-crypto-hourly" and d.ref.depth == 7
    assert d.ref.heartbeat_s == 30 and d.close_ns == 1791889200 * NS
    assert d.info["series"] == "H11"  # series = ticker prefix before the first '-'
    # no filters -> everything still open
    assert len(select_kalshi(soon + later + past, group(), NOW)) == 42


def test_active_series_in_category():
    events = [{"series_ticker": "KXTRUMPMENTION"}, {"series_ticker": "KXBTCD"}, {"series_ticker": "KXTRUMPMENTION"},
              {"series_ticker": "KXFEDMENTION"}]
    assert active_series_in_category(events, ["KXTRUMPMENTION", "KXFEDMENTION", "KXOSCARMENTION"]) == [
        "KXFEDMENTION", "KXTRUMPMENTION"]


# ---------------------------------------------------------------------------
# Polymarket selection
# ---------------------------------------------------------------------------


def pm_event(slug, end, toks=("111", "222"), closed=False):
    return {"slug": slug, "endDate": end, "markets": [{
        "slug": slug, "question": f"{slug}?", "endDate": end, "closed": closed, "enableOrderBook": True,
        "clobTokenIds": json.dumps(list(toks)), "outcomes": json.dumps(["Up", "Down"])}]}


def test_select_polymarket_regex_close_and_tokens():
    evs = [pm_event("btc-updown-5m-1791886800", "2026-10-13T10:25:00Z", ("1", "2")),
           pm_event("doge-updown-5m-1791886800", "2026-10-13T10:25:00Z", ("3", "4")),
           pm_event("eth-updown-5m-1791888000", "2026-10-13T10:45:00Z", ("5", "6")),  # beyond 0.25 h
           pm_event("sol-updown-5m-1791886200", "2026-10-13T10:15:00Z", ("7", "8")),  # already ended
           pm_event("sol-updown-5m-1791887100", "2026-10-13T10:30:00Z", ("9", "10"), closed=True)]
    g = DiscoveryGroup(name="pm5", venue="polymarket", tags=("5M",), slug_regex="^(btc|eth|sol)-updown-5m-",
                       max_hours_to_close=0.25, depth=5)
    out = select_polymarket(evs, g, NOW)
    assert [d.ref.market_id for d in out] == ["1"]
    d = out[0]
    assert d.ref.outcome == "up" and d.ref.bbo is False and d.ref.depth == 5 and d.ref.label.startswith("btc-")
    assert d.info["series"] == "btc-updown-5m"
    both = DiscoveryGroup(name="b", venue="polymarket", tags=("5M",), slug_regex="^btc", tokens="both")
    assert [(d.ref.market_id, d.ref.outcome) for d in select_polymarket(evs, both, NOW)] == [("1", "up"), ("2", "down")]


# ---------------------------------------------------------------------------
# Universe rollover
# ---------------------------------------------------------------------------


def dm(mid, close_s=None, venue="kalshi", group="g"):
    return DynMarket(MarketRef(venue, mid, group=group), None if close_s is None else close_s * NS, {"series": mid[:3]})


def test_universe_set_group_replaces_and_reports_diff():
    calls = []
    u = Universe([MarketRef("kalshi", "STATIC-1")], on_change=calls.append)
    a, r = u.set_group("kalshi", "g", [dm("A"), dm("B")])
    assert sorted(a) == ["A", "B"] and r == [] and calls == ["kalshi"]
    v = u.version
    a, r = u.set_group("kalshi", "g", [dm("B"), dm("C")])  # A rolled out, C rolled in
    assert a == ["C"] and r == ["A"] and u.version == v + 1
    assert set(u.current("kalshi")) == {"STATIC-1", "B", "C"}
    assert set(u.current("kalshi", "static")) == {"STATIC-1"}
    assert set(u.current("kalshi", "dynamic")) == {"B", "C"}
    # unchanged refresh -> no version bump
    v = u.version
    assert u.set_group("kalshi", "g", [dm("B"), dm("C")]) == ([], [])
    assert u.version == v
    log = u.drain_log()
    assert [(x[0], x[2].ref.market_id) for x in log] == [("add", "A"), ("add", "B"), ("add", "C"), ("remove", "A")]
    assert u.drain_log() == []


def test_universe_overlapping_groups_and_static_are_not_removed():
    u = Universe([MarketRef("kalshi", "S")])
    u.set_group("kalshi", "hourly", [dm("X"), dm("S")])
    u.set_group("kalshi", "daily", [dm("X"), dm("Y")])
    assert u.dynamic_count("kalshi") == 3
    a, r = u.set_group("kalshi", "hourly", [])  # X still held by "daily", S is static
    assert r == []
    assert set(u.current("kalshi")) == {"S", "X", "Y"}
    assert "S" not in u.current("kalshi", "dynamic")  # static wins
    a, r = u.set_group("kalshi", "daily", [])
    assert r == ["X", "Y"] or sorted(r) == ["X", "Y"]
    assert set(u.current("kalshi")) == {"S"}


def test_universe_expire_unsubscribes_closed_markets_without_rest():
    u = Universe()
    u.set_group("kalshi", "15m", [dm("W1", close_s=1000), dm("W2", close_s=1900), dm("FOREVER")])
    assert u.expire(1000 * NS + 119 * NS, grace_ns=120 * NS) == {}
    gone = u.expire(1000 * NS + 121 * NS, grace_ns=120 * NS)
    assert gone == {"kalshi": ["W1"]}
    assert set(u.current("kalshi")) == {"W2", "FOREVER"}
    # a refresh that still lists an expired market does not resurrect it
    u.set_group("kalshi", "15m", [dm("W1", close_s=1000), dm("W2", close_s=1900)], now_ns=1200 * NS, grace_ns=120 * NS)
    assert set(u.current("kalshi")) == {"W2"}


def test_rollover_sequence_15m_windows():
    """Three refreshes across a 15-minute boundary: next window subscribed early, old one dropped."""
    u = Universe()
    w = lambda i: dm(f"KXBTC15M-W{i}", close_s=900 * (i + 1))  # noqa: E731
    u.set_group("kalshi", "15m", [w(0), w(1)], now_ns=800 * NS, grace_ns=120 * NS)
    assert set(u.current("kalshi")) == {"KXBTC15M-W0", "KXBTC15M-W1"}
    u.expire(1100 * NS, grace_ns=120 * NS)  # W0 closed at 900 + 120 grace
    assert set(u.current("kalshi")) == {"KXBTC15M-W1"}
    a, r = u.set_group("kalshi", "15m", [w(1), w(2)], now_ns=1100 * NS, grace_ns=120 * NS)
    assert a == ["KXBTC15M-W2"] and r == []


def test_market_row_for_markets_table():
    d = DynMarket(MarketRef("kalshi", "KXBTCD-X-T1", group="g"), 5 * NS,
                  {"series": "KXBTCD", "event": "KXBTCD-X", "title": "t", "strike_type": "greater", "floor_strike": 1.0})
    r = market_row("add", "kalshi", d, 7)
    assert r["series"] == "KXBTCD" and r["action"] == "add" and r["close_ns"] == 5 * NS and r["cap_strike"] is None


# ---------------------------------------------------------------------------
# Shard assignment
# ---------------------------------------------------------------------------


def test_assign_shards_sticky_with_cap():
    new, removed, add_by, rem_by = assign_shards(["a", "b", "c", "d", "e"], {}, {}, cap=2)
    assert new == {"a": 0, "b": 0, "c": 1, "d": 1, "e": 2}
    assert add_by == {0: ["a", "b"], 1: ["c", "d"], 2: ["e"]} and removed == []
    # b and c roll out, f and g roll in: existing markets never move, holes are refilled
    new2, removed2, add2, rem2 = assign_shards(["a", "d", "e", "f", "g"], new, {}, cap=2)
    assert removed2 == ["b", "c"] and rem2 == {0: ["b"], 1: ["c"]}
    assert {k: new2[k] for k in ("a", "d", "e")} == {"a": 0, "d": 1, "e": 2}
    assert add2 == {0: ["f"], 1: ["g"]}
    assert max(sum(1 for s in new2.values() if s == i) for i in range(3)) <= 2


# ---------------------------------------------------------------------------
# Kalshi WS state with dynamic subscriptions
# ---------------------------------------------------------------------------


def _snap(tk, sid, seq):
    return {"type": "orderbook_snapshot", "sid": sid, "seq": seq,
            "msg": {"market_ticker": tk, "yes_dollars_fp": [["0.40", "10"]], "no_dollars_fp": [["0.55", "5"]]}}


def _delta(tk, sid, seq, side="yes", px="0.41", d="3"):
    return {"type": "orderbook_delta", "sid": sid, "seq": seq,
            "msg": {"market_ticker": tk, "price_dollars": px, "delta_fp": d, "side": side}}


def test_kalshi_ws_ok_reply_seq_is_not_a_gap():
    """update_subscription replies carry the orderbook sid's seq (live 2026-10-13)."""
    active = {"A"}
    st = kalshi.KalshiWsState({}, active=active)
    st.process({"type": "subscribed", "id": 1, "msg": {"channel": "orderbook_delta", "sid": 1}}, 0)
    st.process({"type": "subscribed", "id": 1, "msg": {"channel": "trade", "sid": 2}}, 0)
    assert st.sids == {"orderbook_delta": 1, "trade": 2}
    assert not st.process(_snap("A", 1, 1), 1).resync
    ev = st.process({"type": "ok", "id": 3, "sid": 1, "seq": 2, "msg": {"market_tickers": ["B"]}}, 2)
    assert not ev.resync
    active.add("B")
    assert not st.process(_snap("B", 1, 3), 3).resync
    assert not st.process(_delta("A", 1, 4), 4).resync
    assert st.books["A"].best_bid == 0.41 and st.books["B"].valid
    # ok on the trade sid is ignored for sequencing
    assert not st.process({"type": "ok", "id": 4, "sid": 2, "seq": 99, "msg": {}}, 5).resync
    assert not st.process(_delta("A", 1, 5, d="1"), 6).resync


def test_kalshi_ws_rolled_out_market_is_ignored_not_resynced():
    active = {"A", "B"}
    st = kalshi.KalshiWsState({}, active=active)
    st.process(_snap("A", 1, 1), 1)
    st.process(_snap("B", 1, 2), 2)
    active.discard("B")
    st.drop("B")
    ev = st.process(_delta("B", 1, 3), 3)  # late delta after delete_markets
    assert not ev.resync and "B" not in st.books
    trade = {"type": "trade", "sid": 2, "msg": {"market_ticker": "B", "trade_id": "t", "yes_price_dollars": "0.5",
                                                 "count_fp": "1", "taker_side": "yes", "ts": 1}}
    assert st.process(trade, 4).trades == []
    # and the sequence stays continuous for the remaining market
    assert not st.process(_delta("A", 1, 4), 5).resync


def test_kalshi_ws_real_gap_still_resyncs_with_active_filter():
    st = kalshi.KalshiWsState({}, active={"A"})
    st.process(_snap("A", 1, 1), 1)
    ev = st.process(_delta("A", 1, 5), 2)
    assert ev.resync and not st.books["A"].valid


# ---------------------------------------------------------------------------
# Config + RTDS
# ---------------------------------------------------------------------------


def test_parse_discovery_config():
    cfg = parse_config("""
discovery:
  close_grace_s: 60
  kalshi:
    max_requests_per_s: 0.5
    groups:
      - {name: a, series: [KXBTC15M], refresh_s: 30, max_hours_to_close: 1, include_unopened: true}
      - {name: m, category: Mentions, depth: 5, heartbeat_s: 300}
  polymarket:
    groups:
      - {name: p, tags: [5M], slug_regex: '^btc-'}
rtds:
  enabled: true
  topics:
    - {topic: crypto_prices_chainlink, symbols: [btc/usd]}
""")
    d = cfg.discovery
    assert d.close_grace_s == 60 and d.kalshi_max_requests_per_s == 0.5
    assert [g.name for g in cfg.discovery_groups("kalshi")] == ["a", "m"]
    assert cfg.discovery_groups("polymarket")[0].tags == ("5M",)
    assert cfg.discovery.groups[0].series == ("KXBTC15M",) and cfg.discovery.groups[0].include_unopened
    assert cfg.rtds.enabled and cfg.rtds.topics == (("crypto_prices_chainlink", ("btc/usd",)),)


@pytest.mark.parametrize("bad", [
    "discovery: {kalshi: {groups: [{name: a}]}}",                       # no series/category
    "discovery: {kalshi: {groups: [{name: a, series: [X], bogus: 1}]}}",  # unknown key
    "discovery: {polymarket: {groups: [{name: a}]}}",                   # no tags
    "discovery: {kalshi: {groups: [{name: a, series: [X]}, {name: a, series: [Y]}]}}",  # dup name
    "discovery: {polymarket: {groups: [{name: a, tags: [x], slug_regex: '('}]}}",
    "discovery: {forecastex: {}}",
])
def test_parse_discovery_config_rejects_bad(bad):
    with pytest.raises(ConfigError):
        parse_config(bad)


def test_shipped_config_discovery_groups():
    cfg = load_config("config/recorder.yaml")
    names = {g.name for g in cfg.discovery.groups}
    assert {"k-crypto-15m", "k-econ", "k-mentions", "k-weather-high", "pm-crypto-15m"} <= names
    econ = next(g for g in cfg.discovery.groups if g.name == "k-econ")
    assert {"KXCPI", "KXCPICORE", "KXFEDDECISION", "KXPAYROLLS", "KXJOBLESSCLAIMS"} <= set(econ.series)
    weather = next(g for g in cfg.discovery.groups if g.name == "k-weather-high")
    assert len(weather.series) == 18
    assert len(cfg.pairs) == 10  # existing pairs kept


def test_rtds_parse_filters_topics_and_symbols():
    wanted = {"crypto_prices_chainlink": {"btc/usd", "eth/usd"}, "crypto_prices": None}
    raw = json.dumps({"connection_id": "x", "payload": {"full_accuracy_value": "2505698243850373000000",
                      "symbol": "eth/usd", "timestamp": 1791886836000, "value": 2505.698243850373},
                      "timestamp": 1791886837120, "topic": "crypto_prices_chainlink", "type": "update"})
    rows = polymarket_rtds.parse(raw, 5, wanted)
    assert rows == [{"venue": "polymarket_rtds", "topic": "crypto_prices_chainlink", "symbol": "eth/usd",
                     "ts_exchange_ns": 1791886836000 * 1_000_000, "ts_msg_ns": 1791886837120 * 1_000_000,
                     "ts_recv_ns": 5, "value": 2505.698243850373}]
    zec = raw.replace("eth/usd", "zec/usd")
    assert polymarket_rtds.parse(zec, 5, wanted) == []
    assert polymarket_rtds.parse("PONG", 5, wanted) == []
    other = json.dumps({"topic": "crypto_prices", "payload": {"symbol": "xrpusdt", "timestamp": 1, "value": 1.36}})
    assert polymarket_rtds.parse(other, 5, wanted)[0]["symbol"] == "xrpusdt"
    sub = polymarket_rtds.subscribe_msg([("crypto_prices_chainlink", ()), ("crypto_prices", ())])
    assert sub["action"] == "subscribe" and sub["subscriptions"][0] == {
        "topic": "crypto_prices_chainlink", "type": "*", "filters": ""}


def test_parquet_sink_writes_prices_and_markets(tmp_path):
    import pyarrow.parquet as pq

    from src.recorder.writer import ParquetSink

    sink = ParquetSink(tmp_path)
    t = 1791886837 * NS
    sink.add_price({"venue": "polymarket_rtds", "topic": "crypto_prices_chainlink", "symbol": "btc/usd",
                    "ts_exchange_ns": t, "ts_msg_ns": t, "ts_recv_ns": t, "value": 1.0})
    d = DynMarket(MarketRef("kalshi", "K-1", group="g"), t, {"series": "K"})
    sink.add_market(market_row("add", "kalshi", d, t))
    assert sink.flush() == 2
    assert sink.compact_completed(t + 3600 * NS) == 2
    p = pq.read_table(tmp_path / "polymarket_rtds" / "2026-10-13" / "prices-10.parquet").to_pylist()
    assert p[0]["symbol"] == "btc/usd"
    m = pq.read_table(tmp_path / "kalshi" / "2026-10-13" / "markets-10.parquet").to_pylist()
    assert m[0]["market_id"] == "K-1" and m[0]["group"] == "g"


def test_sampler_per_market_heartbeat_and_depth():
    import asyncio
    from collections import Counter

    from src.recorder.book import OrderBook
    from src.recorder.core import FeedContext
    from src.recorder.sampler import Sampler
    from src.recorder.writer import CountingSink

    sink = CountingSink()
    ctx = FeedContext(venue="kalshi", markets=[MarketRef("kalshi", "Q", depth=1, heartbeat_s=300)], venue_cfg=None,
                      depth=10, sampler=Sampler(1.0, 60.0), sink=sink, stop=asyncio.Event(), stats=Counter())
    b = OrderBook("kalshi", "Q")
    b.apply_snapshot([(0.4, 1), (0.3, 2)], [(0.6, 1)])
    assert ctx.emit_due([b], 0, "ws") == 1
    assert len(sink.latest[("kalshi", "Q")]["bid_px"]) == 1  # per-market depth
    assert ctx.emit_due([b], 61 * NS, "ws") == 0  # default 60 s heartbeat overridden by 300 s
    assert ctx.emit_due([b], 301 * NS, "ws") == 1
    ctx.forget("Q")
    assert ("kalshi", "Q") not in ctx.sampler._last


def test_polymarket_empty_ask_sentinel_is_not_a_mismatch():
    """Near-resolved up/down markets: no asks, venue echoes best_ask=1 (seen live 2026-10-13)."""
    from src.recorder.venues import polymarket

    st = polymarket.PolymarketWsState({})
    st.process({"event_type": "book", "asset_id": "T", "timestamp": "1000",
                "bids": [{"price": "0.98", "size": "10"}], "asks": []}, 1)
    st.process({"event_type": "price_change", "timestamp": "1001", "price_changes": [
        {"asset_id": "T", "price": "0.99", "size": "5", "side": "BUY", "best_bid": "0.99", "best_ask": "1"}]}, 2)
    assert st.verify_pending(10 * NS) == 0
    assert st.bbo_mismatches == 0 and st.bbo_checks == 1 and st.books["T"].valid


def test_universe_group_kind_isolates_one_group():
    u = Universe([MarketRef("polymarket", "S")])
    u.set_group("polymarket", "pm5", [dm("a", venue="polymarket"), dm("S", venue="polymarket")])
    u.set_group("polymarket", "pm15", [dm("b", venue="polymarket")])
    assert set(u.current("polymarket", "group:pm5")) == {"a"}  # static S served by the static feed
    assert set(u.current("polymarket", "group:pm15")) == {"b"}
    assert set(u.current("polymarket", "dynamic")) == {"a", "b"}


def test_polymarket_pending_group_sampleable_only_when_end_state_reached():
    from src.recorder.venues import polymarket

    st = polymarket.PolymarketWsState({})
    st.process({"event_type": "book", "asset_id": "T", "timestamp": "1000",
                "bids": [{"price": "0.40", "size": "10"}], "asks": [{"price": "0.45", "size": "10"}]}, 1)
    # first message of a split group: echo already shows the end state (bid 0.41) -> not reached yet
    st.process({"event_type": "price_change", "timestamp": "1001", "price_changes": [
        {"asset_id": "T", "price": "0.42", "size": "5", "side": "BUY", "best_bid": "0.41", "best_ask": "0.45"}]}, 2)
    assert st.books["T"] not in st.sampleable_books(3)
    # second message of the same group completes it -> consistent -> sampleable before the group closes
    st.process({"event_type": "price_change", "timestamp": "1001", "price_changes": [
        {"asset_id": "T", "price": "0.42", "size": "0", "side": "BUY", "best_bid": "0.41", "best_ask": "0.45"},
        ]}, 3)
    st.process({"event_type": "price_change", "timestamp": "1001", "price_changes": [
        {"asset_id": "T", "price": "0.41", "size": "2", "side": "BUY", "best_bid": "0.41", "best_ask": "0.45"}]}, 4)
    assert "T" in st._pending and st.books["T"] in st.sampleable_books(5)
    assert st.verify_pending(10 * NS) == 0


def test_universe_overlapping_groups_take_fastest_heartbeat_not_slower():
    from dataclasses import replace as _r
    u = Universe(default_heartbeat_s=60.0)
    slow = dm("G"); slow = _r(slow, ref=_r(slow.ref, heartbeat_s=None))
    u.set_group("kalshi", "k-econ", [slow])
    u.set_group("kalshi", "k-mentions", [_r(dm("G"), ref=_r(dm("G").ref, heartbeat_s=300.0))])
    assert u.current("kalshi")["G"].heartbeat_s is None  # a slower group never slows the default
    u.set_group("kalshi", "k-uflow-open", [_r(dm("G"), ref=_r(dm("G").ref, heartbeat_s=30.0, depth=15))])
    ref = u.current("kalshi")["G"]
    assert ref.heartbeat_s == 30.0 and ref.depth == 15
