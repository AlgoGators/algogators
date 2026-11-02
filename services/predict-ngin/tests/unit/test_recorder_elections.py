"""Unit tests for the 2026 election additions to the recorder: time-windowed sampling,
series/event regex discovery filters, and the Kalshi settlement (status-change) log.
No network. Market rows are trimmed copies of real ``GET /markets`` payloads (2026-10-14).
"""

from __future__ import annotations

import asyncio
from collections import Counter

import pyarrow.parquet as pq
import pytest

from src.recorder.book import OrderBook
from src.recorder.config import ConfigError, MarketRef, SampleWindow, load_config, parse_config, parse_windows
from src.recorder.core import FeedContext
from src.recorder.sampler import Sampler, effective_sampling
from src.recorder.series_discovery import filter_series, select_kalshi
from src.recorder.settlement import load_state, save_state, status_changes, to_poll, tracked_markets
from src.recorder.universe import Universe
from src.recorder.writer import CountingSink, ParquetSink

NS = 1_000_000_000
NIGHT_START = 1793743200 * NS  # 2026-11-03T22:00:00Z = 17:00 EST
NIGHT_END = 1793811600 * NS  # 2026-11-04T17:00:00Z = 12:00 EST


# ---------------------------------------------------------------- windows / config


def test_parse_windows_offsets_and_naive_utc():
    w = parse_windows([{"start": "2026-11-03T17:00:00-05:00", "end": "2026-11-04T17:00:00",
                        "interval_s": 1, "heartbeat_s": 30}], "t")
    assert w == (SampleWindow(NIGHT_START, NIGHT_END, 1.0, 30.0),)
    assert w[0].contains(NIGHT_START) and not w[0].contains(NIGHT_END)


@pytest.mark.parametrize("raw", [
    [{"start": "2026-11-04T00:00:00Z", "end": "2026-11-03T00:00:00Z", "interval_s": 1}],  # end <= start
    [{"start": "2026-11-03T00:00:00Z", "end": "2026-11-04T00:00:00Z"}],  # no interval
    [{"start": "2026-11-03T00:00:00Z", "end": "2026-11-04T00:00:00Z", "interval_s": 0}],
    [{"start": "2026-11-03T00:00:00Z", "end": "2026-11-04T00:00:00Z", "interval_s": 1, "x": 1}],
    [{"start": "not a date", "end": "2026-11-04T00:00:00Z", "interval_s": 1}],
    {"start": "2026-11-03T00:00:00Z"},  # not a list
])
def test_parse_windows_rejects_bad(raw):
    with pytest.raises(ConfigError):
        parse_windows(raw, "t")


GROUP_YAML = """
discovery:
  kalshi:
    groups:
      - name: elex
        category: Elections
        series_regex: '^(CONTROL[HS]|SENATE[A-Z]{2}S?)$'
        event_regex: '-(26|2026)$'
        sample_interval_s: 5
        sample_windows:
          - {start: "2026-11-03T17:00:00-05:00", end: "2026-11-04T12:00:00-05:00", interval_s: 1}
        track_settlement: true
settlement:
  enabled: true
  interval_s: 3600
  fast_interval_s: 300
  fast_windows:
    - {start: "2026-11-03T18:00:00-05:00", end: "2026-11-11T00:00:00-05:00"}
"""


def test_group_and_settlement_parse():
    c = parse_config(GROUP_YAML)
    g = c.discovery.groups[0]
    assert g.sample_interval_s == 5.0 and g.track_settlement
    assert g.sample_windows[0].start_ns == NIGHT_START and g.sample_windows[0].end_ns == NIGHT_END
    s = c.settlement
    assert s.enabled and s.batch == 100
    assert s.interval_at(NIGHT_START) == 3600  # 17:00 EST: before the fast window
    assert s.interval_at(NIGHT_START + 3600 * NS) == 300  # 18:00 EST
    assert s.interval_at(1794373200 * NS) == 3600  # 2026-11-11T05:00Z = end (exclusive)


@pytest.mark.parametrize("bad", [
    GROUP_YAML.replace("sample_interval_s: 5", "sample_interval_s: 0"),
    GROUP_YAML.replace("series_regex: '^(CONTROL[HS]|SENATE[A-Z]{2}S?)$'", "series_regex: '('"),
    GROUP_YAML.replace("track_settlement: true", "track_settlement: false"),  # settlement on, nothing tracked
    GROUP_YAML.replace("  interval_s: 3600", "  bogus: 1"),
    GROUP_YAML + "  batch: 5000\n",
])
def test_group_and_settlement_reject_bad(bad):
    with pytest.raises(ConfigError):
        parse_config(bad)


def test_track_settlement_is_kalshi_only():
    y = """
discovery:
  polymarket:
    groups:
      - {name: p, tags: [x], track_settlement: true}
"""
    with pytest.raises(ConfigError):
        parse_config(y)


def test_shipped_config_election_groups():
    c = load_config("config/recorder.yaml")
    g = {x.name: x for x in c.discovery.groups}
    for name in ("k-elex-races", "k-elex-control"):
        assert g[name].track_settlement and g[name].depth == 5 and g[name].sample_interval_s == 5
        (w,) = g[name].sample_windows
        # covers 18:00 ET Nov 3 (user) and the pre-registered 22:00Z start, through 12:00 ET Nov 4
        assert w.start_ns <= NIGHT_START and w.end_ns >= NIGHT_END and w.interval_s == 1
    assert c.settlement.enabled
    assert c.settlement.interval_at(1793836800 * NS) == 300  # 2026-11-05T00:00Z
    assert c.settlement.interval_at(1792540800 * NS) == 3600  # 2026-10-21


# ---------------------------------------------------------------- sampling


def test_effective_sampling():
    ws = (SampleWindow(100, 200, 1.0, 30.0), SampleWindow(150, 300, 0.5))
    assert effective_sampling(50, 5.0, 300.0, ws) == (5.0, 300.0)
    assert effective_sampling(120, 5.0, 300.0, ws) == (1.0, 30.0)
    assert effective_sampling(160, 5.0, 300.0, ws) == (0.5, 30.0)  # fastest interval, a window hb
    assert effective_sampling(250, 5.0, 300.0, ws) == (0.5, 300.0)  # window without hb keeps market hb
    assert effective_sampling(120, None, None, ()) == (None, None)


def test_sampler_due_interval_override():
    s = Sampler(1.0, 60.0)
    b = OrderBook("kalshi", "X")
    b.apply_snapshot([(0.4, 1)], [(0.6, 1)])
    s.sample(b, 5, 0, "ws")
    b.apply_delta("bid", 0.41, 2)
    assert s.due(b, 2 * NS)  # default 1 s
    assert not s.due(b, 2 * NS, interval_s=5.0)
    assert s.due(b, 5 * NS, interval_s=5.0)


def _ctx(sink, ref):
    return FeedContext(venue="kalshi", markets=[ref], venue_cfg=None, depth=10,
                       sampler=Sampler(1.0, 60.0), sink=sink, stop=asyncio.Event(), stats=Counter())


def test_emit_due_windowed_market():
    """5 s on-change sampling normally, 1 s (and a 30 s heartbeat) inside the window."""
    w = (SampleWindow(1000 * NS, 2000 * NS, 1.0, 30.0),)
    sink = CountingSink()
    ctx = _ctx(sink, MarketRef("kalshi", "E", depth=5, heartbeat_s=300, sample_interval_s=5.0, sample_windows=w))
    b = OrderBook("kalshi", "E")
    b.apply_snapshot([(0.9, 10)], [(0.92, 5)])
    t = 100 * NS
    assert ctx.emit_due([b], t, "ws") == 1
    b.apply_delta("bid", 0.9, 11)
    assert ctx.emit_due([b], t + 2 * NS, "ws") == 0  # changed, but 5 s not elapsed
    assert ctx.emit_due([b], t + 5 * NS, "ws") == 1
    assert ctx.emit_due([b], t + 200 * NS, "ws") == 0  # unchanged: heartbeat 300 s
    # inside the window
    t = 1500 * NS
    assert ctx.emit_due([b], t, "ws") == 1  # heartbeat (>300 s since last row)
    b.apply_delta("bid", 0.9, 12)
    assert ctx.emit_due([b], t + 1 * NS, "ws") == 1  # 1 s sampling
    assert ctx.emit_due([b], t + 20 * NS, "ws") == 0
    assert ctx.emit_due([b], t + 31 * NS, "ws") == 1  # 30 s heartbeat
    row = sink.latest[("kalshi", "E")]
    assert all(s > 0 for s in row["bid_sz"] + row["ask_sz"]) and "age_s" in row and "last_update_ns" in row


# ---------------------------------------------------------------- discovery filters


def _m(tk, ev, status="active"):
    return {"ticker": tk, "event_ticker": ev, "status": status, "close_time": "2027-11-03T15:00:00Z",
            "strike_type": "structured", "title": tk}


def test_event_regex_and_series_regex():
    c = parse_config(GROUP_YAML)
    g = c.discovery.groups[0]
    rows = [_m("SENATEGA-26-D", "SENATEGA-26"), _m("SENATEGA-28-D", "SENATEGA-28"),
            _m("CONTROLS-2026-D", "CONTROLS-2026"), _m("SENATEGA-26-R", "SENATEGA-26", status="closed")]
    sel = select_kalshi(rows, g, 1792000000 * NS)
    assert sorted(d.ref.market_id for d in sel) == ["CONTROLS-2026-D", "SENATEGA-26-D"]
    ref = sel[0].ref
    assert ref.group == "elex" and ref.sample_interval_s == 5.0 and ref.sample_windows == g.sample_windows
    assert filter_series(["SENATEGA", "SENATEOHS", "SENATEPOWER", "CONTROLH", "KXSENATEGAPRIMARY"],
                         g.series_regex) == ["SENATEGA", "SENATEOHS", "CONTROLH"]
    assert filter_series(["A", "B"], None) == ["A", "B"]


def test_shipped_regexes_select_2026_races():
    import re

    g = {x.name: x for x in load_config("config/recorder.yaml").discovery.groups}
    races, ctl = g["k-elex-races"], g["k-elex-control"]
    ok = ["CONTROLH", "CONTROLS", "SENATEGA", "SENATEOHS", "GOVPARTYNH", "HOUSECA13", "KXHOUSERACE"]
    no = ["KXHOUSERACEPRIMARY", "SENATEPOWER", "GOVPARTY", "HOUSEWINSTATE", "KXSENATEGAPRIMARY"]
    assert filter_series(ok + no, races.series_regex) == ok
    er = re.compile(races.event_regex)
    assert all(er.search(e) for e in ["KXHOUSERACE-WYAL-26", "CONTROLS-2026", "GOVPARTYSC-26"])
    assert not any(er.search(e) for e in ["SENATEGA-28", "CONTROLS-2028"])
    ec = re.compile(ctl.event_regex)
    assert all(ec.search(e) for e in ["KXDSENATESEATS-27", "KXBALANCEPOWERCOMBO-27FEB",
                                      "KXCLOSESTSENATE-27JAN03", "KXHOUSEPOPVOTEMARGIN-27NOV03"])
    assert not any(ec.search(e) for e in ["KXDSENATESEATS-29", "POWER-28"])


# ---------------------------------------------------------------- settlement log

ACTIVE = {"ticker": "SENATEGA-26-D", "event_ticker": "SENATEGA-26", "status": "active", "result": "",
          "close_time": "2027-11-03T15:00:00Z", "expiration_value": "", "can_close_early": True}
SETTLED = dict(ACTIVE, status="finalized", result="yes", close_time="2026-11-04T09:12:00Z",
               settlement_ts="2026-11-04T10:00:01.5Z", settlement_value_dollars="1.0000")


def test_status_changes_first_seen_unchanged_changed():
    st = {}
    rows = status_changes(st, [ACTIVE], 1 * NS, {"SENATEGA-26-D": "k-elex-races"})
    assert len(rows) == 1 and rows[0]["first_seen"] and rows[0]["prev_status"] is None
    assert rows[0]["group"] == "k-elex-races" and rows[0]["result"] is None
    assert status_changes(st, [dict(ACTIVE, yes_bid_dollars="0.5")], 2 * NS, {}) == []  # prices ignored
    closed = dict(ACTIVE, status="closed", close_time="2026-11-04T09:12:00Z")
    (r,) = status_changes(st, [closed], 3 * NS, {})
    assert (r["prev_status"], r["status"], r["first_seen"]) == ("active", "closed", False)
    assert r["close_ns"] == 1793783520 * NS and r["group"] == "k-elex-races"  # group remembered
    (r,) = status_changes(st, [SETTLED], 4 * NS, {})
    assert (r["status"], r["result"], r["prev_result"]) == ("finalized", "yes", None)
    assert r["settlement_ns"] == 1793786401 * NS + 500_000_000 and r["settlement_value"] == 1.0


def test_to_poll_keeps_closed_until_finalized():
    st = {"A": {"status": "closed"}, "B": {"status": "finalized"}}
    assert to_poll(st, ["C", "A"]) == ["A", "C"]


def test_tracked_markets_from_universe():
    from src.recorder.universe import DynMarket

    u = Universe()
    u.set_group("kalshi", "k-elex-races", [DynMarket(MarketRef("kalshi", "R1", group="k-elex-races"))])
    u.set_group("kalshi", "k-mentions", [DynMarket(MarketRef("kalshi", "M1", group="k-mentions"))])
    assert tracked_markets(u, ["k-elex-races", "k-elex-control"]) == {"R1": "k-elex-races"}


def test_state_roundtrip(tmp_path):
    p = tmp_path / "kalshi" / "settlement_state.json"
    assert load_state(p) == {}
    st = {}
    status_changes(st, [ACTIVE], 1, {})
    save_state(p, st)
    assert load_state(p) == st
    p.write_text("{broken", encoding="utf-8")
    assert load_state(p) == {}


def test_status_rows_write_parquet(tmp_path):
    sink = ParquetSink(tmp_path)
    for r in status_changes({}, [ACTIVE, dict(SETTLED, ticker="X")], 1792000000 * NS, {}):
        sink.add_status(r)
    assert sink.flush() == 2
    (f,) = list((tmp_path / "kalshi").rglob("status-*.parquet"))
    t = pq.read_table(f).to_pandas()
    assert set(t.market_id) == {"SENATEGA-26-D", "X"} and t.first_seen.all()
    assert sink.compact_completed(1792000000 * NS + 7200 * NS) == 1
