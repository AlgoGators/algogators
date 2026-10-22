"""Paper trading page: runner discovery and health, file readers, order journal, paper fills."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import pytest

from predict_ngin.viz import paper_sources as ps

NOW = dt.datetime(2026, 10, 17, 12, 0, tzinfo=dt.timezone.utc)


def _runner(root: Path, name: str, **status) -> Path:
    d = root / "paper_trading" / name
    d.mkdir(parents=True)
    (d / "status.json").write_text(json.dumps({"name": name, "mode": "paper", **status}))
    return d


def test_discovery_and_health(tmp_path):
    good = _runner(
        tmp_path,
        "a",
        ok=True,
        last_run="2026-10-17T11:30:00Z",
        passes=3,
        cum_pnl={"low": -1.5, "high": 2.0},
        positions={"BTC": {"open": True, "qty": 2}},
    )
    _runner(tmp_path, "b", ok=False, last_run="2026-10-17T11:59:00Z", kill="drawdown")
    _runner(
        tmp_path,
        "c",
        ok=True,
        last_run="2026-10-16T00:00:00Z",
        next_boundary="2026-10-16T06:00:00Z",
    )
    (tmp_path / "paper_trading" / "no_status").mkdir()  # ignored: no status file
    extra = tmp_path / "fwd"
    extra.mkdir()
    (extra / "x.csv").write_text("a,b\n1,2\n")
    os.utime(extra / "x.csv", (NOW.timestamp() - 3 * 3600,) * 2)
    cfg = {
        "runs": [
            {"name": "fwd", "dir": str(extra), "stale_hours": 2},
            {"name": "gone", "dir": str(tmp_path / "missing")},
        ]
    }
    runs = ps.discover_runs(tmp_path, cfg)
    assert [r.name for r in runs] == ["a", "b", "c", "fwd", "gone"]
    h = {r.name: ps.health(r, NOW) for r in runs}
    assert h["a"]["ok"] and h["a"]["age_h"] == 0.5 and h["a"]["passes"] == 3
    assert "not ok" in h["b"]["problems"] and "kill: drawdown" in h["b"]["problems"]
    assert "missed next boundary" in h["c"]["problems"] and "nothing new" in h["c"]["problems"]
    assert not h["fwd"]["ok"] and h["fwd"]["age_h"] == 3.0  # newest file stands in for last run
    assert "folder missing" in h["gone"]["problems"]
    _, doc = ps.read_status(ps.Run("a", good))
    assert ps.number_dicts(doc) == {"cum_pnl": {"low": -1.5, "high": 2.0}}
    assert list(ps.table_dicts(doc)["positions"].columns) == ["open", "qty"]


def test_read_table_tails_and_time_column(tmp_path):
    p = tmp_path / "events.jsonl"
    rows = [
        {
            "utc": f"2026-10-17T00:{i:02d}:00Z",
            "usd": i,
            "kind": "funding" if i % 2 else "fee",
            "nested": {"x": i},
        }
        for i in range(50)
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n{broken\n")
    df = ps.read_table(p, 10)
    assert len(df) == 9 and df["usd"].iloc[-1] == 49 and "nested.x" in df.columns
    assert ps.time_column(df) == "utc" and str(df["utc"].dt.tz) == "UTC"
    assert "usd" in ps.numeric_columns(df)
    assert ps.tail_lines(p, 3)[-1] == "{broken"


def test_journal_and_fills(tmp_path, monkeypatch):
    monkeypatch.setenv("PREDICT_NGIN_DATA", str(tmp_path))
    from predict_ngin.execution.brokers import Broker
    from predict_ngin.execution.gate import LiveGate
    from predict_ngin.execution.limits import RiskLimits
    from predict_ngin.execution.orders import OrderAck, OrderIntent

    class FakePaper:
        def __init__(self):
            self.orders = {}

        def place(self, o):
            self.orders["p1"] = o
            return OrderAck("kalshi", o.market, o.client_order_id, "p1", "paper", 0.0, o.quantity)

        def poll(self):
            self.orders.pop("p1")
            return [
                {
                    "order_id": "p1",
                    "market": "KXA",
                    "outcome": "yes",
                    "action": "buy",
                    "price": 0.4,
                    "quantity": 2.0,
                    "ts": 1.0,
                }
            ]

    b = Broker("kalshi", "paper", LiveGate("kalshi"), RiskLimits(), paper=FakePaper())
    b.place(OrderIntent(venue="kalshi", market="KXA", outcome="yes", price=0.4, quantity=2))
    assert b.state.open_orders == 1
    assert len(b.poll()) == 1 and b.state.open_orders == 0 and not b.orders
    df = ps.journal_frame(tmp_path)
    assert list(df["event"]) == ["broker_open", "intent", "placed", "fill"]
    f = ps.fills(df)
    assert f["notional"].iloc[0] == pytest.approx(0.8) and f["fill_ts"].iloc[0] == 1.0
    assert set(ps.order_counts(df)["event"]) == {"broker_open", "intent", "placed", "fill"}


def test_heartbeat(tmp_path):
    meta, svc = ps.heartbeat(tmp_path)
    assert meta == {} and svc.empty
    (tmp_path / "heartbeat").mkdir()
    (tmp_path / "heartbeat" / "status.json").write_text(
        json.dumps(
            {
                "ts": "2026-10-17T05:58:16+00:00",
                "ok": True,
                "problems": [],
                "services": {"rec": {"active": "active", "mode": "data"}},
            }
        )
    )
    meta, svc = ps.heartbeat(tmp_path)
    assert meta["ok"] and list(svc["service"]) == ["rec"]


def test_page_renders(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    pytest.importorskip("plotly")
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    import predict_ngin.viz as pkg

    monkeypatch.setenv("PREDICT_NGIN_DATA", str(tmp_path))
    monkeypatch.setattr(ps, "load_config", lambda path=None: {})
    st.cache_data.clear()
    at = AppTest.from_file(str(Path(pkg.__file__).parent / "app.py"), default_timeout=60)
    at.run()
    at.sidebar.radio(key="page").set_value("Paper trading").run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("No paper runners" in i.value for i in at.info)
    d = _runner(tmp_path, "a", ok=True, last_run="2026-10-17T11:30:00Z", equity={"low": 1.0})
    (d / "events.jsonl").write_text(
        "\n".join(json.dumps({"utc": f"2026-10-17T0{i}:00:00Z", "usd": i}) for i in range(5))
    )
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.selectbox(key="perf_run").value == "a"
    assert at.selectbox(key="paper_run").value == "a"


def test_performance_metrics_and_costs(tmp_path):
    import numpy as np
    import pandas as pd

    from predict_ngin.paper.runner import append_performance
    from predict_ngin.viz import performance as perf

    # daily P&L rows with a fee column, net of fees already
    days = pd.date_range("2026-01-01", periods=60, freq="D", tz="UTC")
    rng = np.random.default_rng(0)
    pnl = rng.normal(5, 20, 60)
    pd.DataFrame(
        {"day": days.strftime("%Y-%m-%d"), "net": pnl, "fee": 1.0, "rule": ["A", "B"] * 30}
    ).to_csv(tmp_path / "daily.csv", index=False)
    spec = perf.Spec.from_config(
        {
            "file": "daily.csv",
            "time": "day",
            "pnl": "net",
            "costs": "fee",
            "group": "rule",
            "capital": 1000,
        }
    )
    df = perf.load(tmp_path, spec)
    per = perf.period_table(df, spec, "D")
    m = perf.metrics(per, 1000, "D", ["fee"])
    assert m["periods"] == 60 and m["net_pnl"] == pytest.approx(pnl.sum())
    assert m["costs"] == 60 and m["gross_pnl"] == pytest.approx(pnl.sum() + 60)
    r = pnl / (1000 + np.concatenate([[0], np.cumsum(pnl)[:-1]]))
    assert m["sharpe"] == pytest.approx(r.mean() / r.std(ddof=1) * np.sqrt(365))
    eq = 1000 + np.cumsum(pnl)
    assert m["max_drawdown"] == pytest.approx((eq - np.maximum.accumulate(eq)).min())
    assert m["total_return"] == pytest.approx(eq[-1] / 1000 - 1)
    assert set(perf.compare(df, spec, "D")["rule"]) == {"A", "B"}
    # costs not yet in P&L: net = pnl - fee
    spec2 = perf.Spec.from_config({**vars(spec), "costs_in_pnl": False, "where": {"rule": ["A"]}})
    m2 = perf.metrics(perf.period_table(perf.load(tmp_path, spec2), spec2, "W"), 1000, "W", ["fee"])
    assert m2["net_pnl"] == pytest.approx(pnl[::2].sum() - 30)

    # the standard performance.jsonl: equity levels and cumulative costs
    for i, (e, f) in enumerate([(1000, 0), (1003, 1), (1001, 2.5)]):
        append_performance(
            tmp_path, f"2026-01-0{i + 1}T00:00:00Z", e, benchmark=1000 + i, costs={"fees": f}
        )
    sp = perf.standard_spec(tmp_path)
    sp.capital = 1000
    per = perf.period_table(perf.load(tmp_path, sp), sp, "D")
    assert list(per["net"]) == [0, 3, -2] and list(per["cum_cost_fees"]) == [0, 1, 1.5]
    m3 = perf.metrics(per, 1000, "D", sp.costs)
    assert m3["benchmark_return"] == pytest.approx(0.002) and m3["costs"] == 2.5

    # time parsing of numbers: ns, s and days since 1970
    s = pd.Series([1792068660373078374, 1792068700629560994])
    assert perf.to_utc(s).dt.year.tolist() == [2026, 2026]
    assert perf.to_utc(pd.Series([20740])).dt.strftime("%Y-%m-%d").tolist() == ["2026-10-14"]


def test_nested_cells_are_hashable(tmp_path):
    p = tmp_path / "snap.jsonl"
    p.write_text(json.dumps({"utc": "2026-10-17T00:00:00Z", "a": {"b": {"c": 1}}, "l": [1, 2]}))
    df = ps.read_table(p, 10)
    assert df.nunique().notna().all()  # used to raise "unhashable type: 'dict'"
    assert ps.read_table(p, 10, max_level=None)["a.b.c"].iloc[0] == 1
