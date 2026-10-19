"""Execution safety: gates, kill switch, limits, dry run; nothing is sent outside a gated call."""

from __future__ import annotations

import json

import pytest

from predict_ngin.execution import gate as G
from predict_ngin.execution.brokers import make_broker
from predict_ngin.execution.limits import LimitBreached, LimitState, RiskLimits, check
from predict_ngin.execution.orders import OrderIntent
from predict_ngin.execution.venues import KalshiTrader


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PREDICT_NGIN_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("PREDICT_NGIN_LIVE", raising=False)
    cfg = tmp_path / "live.local.yaml"
    monkeypatch.setattr(G, "config_path", lambda: cfg)
    return tmp_path, cfg


def _arm(cfg, monkeypatch, venues=("kalshi",), limits=None):
    import yaml

    cfg.write_text(
        yaml.safe_dump({"live": {"enabled": True, "venues": list(venues)}, "limits": limits or {}})
    )
    monkeypatch.setenv("PREDICT_NGIN_LIVE", "1")


def intent(**kw):
    base = dict(venue="kalshi", market="KXA-1", outcome="yes", price=0.40, quantity=2)
    return OrderIntent(**{**base, **kw})


class FakeSigner:
    def headers(self, method, path):
        return {"sig": "x"}


class FakeHTTP:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw.get("json")))

        class R:
            status_code = 200

            def json(self_inner):
                if method == "POST":
                    return {"order": {"order_id": "o1", "fill_count": "0", "remaining_count": "2"}}
                if method == "DELETE":
                    return {"reduced_by": "2"}
                return {"orders": [], "balance": 1}

        return R()


def test_gates_block_by_default(env):
    g = G.LiveGate("kalshi")
    assert not g.armed(True)
    with pytest.raises(G.MutationBlocked):
        make_broker("kalshi", live=True)  # never a silent fall-back to dry run


def test_all_three_gates_needed(env, monkeypatch):
    tmp, cfg = env
    _arm(cfg, monkeypatch)
    g = G.LiveGate("kalshi")
    assert g.armed(True) and not g.armed(False)
    assert not G.LiveGate("polymarket_us").armed(True)  # venue not listed
    monkeypatch.setenv("PREDICT_NGIN_LIVE", "0")
    assert not g.armed(True)


def test_kill_switch_blocks_new_orders_not_cancels(env, monkeypatch):
    tmp, cfg = env
    _arm(cfg, monkeypatch)
    G.stop_path().parent.mkdir(parents=True, exist_ok=True)
    G.stop_path().write_text("on")
    g = G.LiveGate("kalshi")
    with pytest.raises(G.KillSwitchOn):
        g.require(True, new_order=True)
    g.require(True, new_order=False)  # cancels allowed


def test_limits():
    lim, st = RiskLimits(max_order_usd=1.0), LimitState()
    with pytest.raises(LimitBreached, match="max_order_usd"):
        check(intent(quantity=5), lim, st)  # $2.00
    with pytest.raises(LimitBreached, match="taker"):
        check(intent(quantity=1, post_only=False), lim, st)
    with pytest.raises(LimitBreached, match="allow_sell"):
        check(intent(quantity=1, action="sell"), lim, st)
    check(intent(quantity=1), lim, st)
    with pytest.raises(ValueError, match="unknown risk limit keys"):
        RiskLimits.from_config({"max_order": 3})


def test_dry_run_sends_nothing_and_journals(env):
    b = make_broker("kalshi")
    ack = b.place(intent())
    assert ack.status == "dry_run" and ack.order_id is None
    lines = [json.loads(x) for x in b.journal.path().read_text().splitlines()]
    assert [x["event"] for x in lines] == ["broker_open", "intent", "would_place"]


def test_no_mutation_outside_gate(env, monkeypatch):
    t = KalshiTrader(signer=FakeSigner())
    t._http = FakeHTTP()
    with pytest.raises(G.MutationBlocked):
        t._send(
            "POST", "/portfolio/events/orders", json={}
        )  # direct send of a mutation is impossible
    with pytest.raises(G.MutationBlocked):
        t.place(intent(), live=True)  # gates not armed
    assert t._http.calls == []


def test_live_place_and_cancel_with_fake_transport(env, monkeypatch):
    tmp, cfg = env
    _arm(cfg, monkeypatch, limits={"max_order_usd": 5})
    # the real trader would load Kalshi credentials from the environment; this test needs none
    monkeypatch.setattr("predict_ngin.execution.venues.make_trader", lambda venue, gate: None)
    b = make_broker("kalshi", live=True)
    b.trader = KalshiTrader(signer=FakeSigner(), gate=b.gate)
    fake = FakeHTTP()
    b.trader._http = fake
    ack = b.place(intent(outcome="no", price=0.30, quantity=2))
    method, url, body = fake.calls[0]
    assert method == "POST" and url.endswith("/portfolio/events/orders")
    assert (
        body["side"] == "ask" and body["price"] == "0.7000" and body["post_only"] is True
    )  # NO @.30 = ask YES @.70
    assert ack.order_id == "o1" and ack.status == "resting"
    assert b.cancel("o1") == 2.0
    assert fake.calls[1][0] == "DELETE"
