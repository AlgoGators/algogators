"""LIP pilot tests: mocked HTTP layer only (tests/live/lip_pilot_fakes.py). No network, no orders."""
from __future__ import annotations

import ast
import inspect
import itertools
import signal
from pathlib import Path

import pytest

from tests.live.lip_pilot_fakes import (ARMED, DISARMED, FakeClock, FakeExchange, FakeSigner, T0, make_cfg,
                                        make_pilot)
from src.live.lip_pilot import client as client_mod
from src.live.lip_pilot.book import Book
from src.live.lip_pilot.client import KalshiClient, MutationBlocked
from src.live.lip_pilot.config import (LIVE_ENV_VALUE, LIVE_ENV_VAR, LiveGate, load_config, resolve_gate)
from src.live.lip_pilot.monitors import KillSwitch
from src.live.lip_pilot.orders import ClientIdFactory, DryRunGateway
from src.live.lip_pilot.posture import Program, desired_quote
from src.live.lip_pilot.ratelimit import TokenBucket
from src.live.lip_pilot.risk import MarketExposure, max_side_size
from src.live.lip_pilot.runner import install_signal_handlers, restore_signal_handlers
from src.live.lip_pilot.scoring import payout, side_score, snapshot_score

PKG = Path(client_mod.__file__).resolve().parent


# ====================================================================== config and gates
def test_default_config_is_safe():
    cfg = load_config()
    assert cfg.dry_run is True and cfg.live is False
    assert cfg.caps.max_total_at_risk_usd <= 250 and cfg.caps.max_per_market_at_risk_usd <= 50
    assert cfg.caps.max_markets <= 5
    g = resolve_gate(cfg, cli_live=True, environ={LIVE_ENV_VAR: LIVE_ENV_VALUE})
    assert not g.armed and "config needs live: true and dry_run: false" in g.reasons()


@pytest.mark.parametrize("cli,env,live,dry", list(itertools.product([False, True], repeat=4)))
def test_gate_needs_all_three(cli, env, live, dry):
    cfg = load_config()
    cfg.live, cfg.dry_run = live, dry
    g = resolve_gate(cfg, cli_live=cli, environ={LIVE_ENV_VAR: LIVE_ENV_VALUE} if env else {})
    assert g.armed == (cli and env and live and not dry)


def test_env_value_must_match_exactly():
    cfg = load_config()
    cfg.live, cfg.dry_run = True, False
    for v in ("i_understand", "yes", "1", "I_UNDERSTAND "):
        assert not resolve_gate(cfg, True, {LIVE_ENV_VAR: v}).armed
    assert resolve_gate(cfg, True, {LIVE_ENV_VAR: "I_UNDERSTAND"}).armed


@pytest.mark.parametrize("over", [{"caps": {"max_total_at_risk_usd": 251}},
                                  {"caps": {"max_per_market_at_risk_usd": 60}},
                                  {"caps": {"max_markets": 6}},
                                  {"posture": {"ticks_behind_best_bid": 0}},
                                  {"posture": {"pull_minutes_before_close": 30}}])
def test_config_cannot_raise_hard_caps(over):
    with pytest.raises(ValueError):
        load_config(overrides=over)


# ====================================================================== scoring and risk
def test_scoring_matches_audit_hand_derivation():
    # docs/LIPGAS_AUDIT.md section 2, example 1 (KXAAAGASDWY NO side)
    ss = side_score([(0.12, 240), (0.08, 625), (0.01, 255)], 1000, 0.5, our_px=0.11, our_sz=250)
    assert ss.reference == pytest.approx(0.12)
    assert ss.share == pytest.approx(0.3093, abs=1e-4)
    # example 2: become the reference
    ss = side_score([(0.83, 1), (0.09, 1), (0.05, 1050)], 1000, 0.5, our_px=0.82, our_sz=250)
    assert ss.reference == pytest.approx(0.82) and ss.share == pytest.approx(0.996, abs=1e-3)


def test_snapshot_excluded_if_either_side_short_of_target():
    s, excluded, _, _ = snapshot_score([(0.5, 2000)], [(0.4, 500)], 1000, 0.5, (0.49, 100), (0.39, 100))
    assert excluded and s == 0


def test_payout_minimum_and_floor():
    assert payout(100, [0.001] * 10, 10) == 0.0               # below $1 -> nothing
    assert payout(100, [0.4] * 10, 20) == pytest.approx(10.0)  # 100 * 0.2 * 10/20


def test_max_loss_with_netting():
    e = MarketExposure(yes_resting=250, yes_px=0.14, no_resting=63, no_px=0.79)
    assert e.max_loss() == pytest.approx(max(250 * 0.14, 63 * 0.79))   # not the gross sum 84.77
    # both sides filled: netting returns $1 per pair -> loss only on the unmatched part
    e = MarketExposure(yes_filled=100, yes_cost=14, no_filled=63, no_cost=49.77)
    assert e.max_loss() == pytest.approx(max(14 + 49.77 - 100, 14 + 49.77 - 63))
    assert e.max_loss() == pytest.approx(0.77)


def test_max_side_size_respects_market_and_total_room():
    e = MarketExposure()
    assert max_side_size(e, "yes", 0.2, 50, 250) == 250
    assert max_side_size(e, "yes", 0.79, 50, 250) == 63
    assert max_side_size(e, "yes", 0.2, 50, 10) == 50          # total room binds
    e = MarketExposure(no_filled=10, no_cost=7.9)
    # NO settles: 7.9 + 0.2n - 10 (held NO pays $10) <= 50  ->  n = 260
    assert max_side_size(e, "yes", 0.2, 50, 250) == 260


# ====================================================================== posture / repricing
def _prog():
    return Program("p", "T", 1000.0, 0.5, 100.0, T0 - 3600, T0 + 12 * 3600)


def test_posture_bb1_size_and_caps():
    cfg = make_cfg()
    book = Book("T", [(0.15, 30), (0.14, 100), (0.10, 1200)], [(0.80, 20), (0.79, 50), (0.70, 1500)], T0)
    q = desired_quote(book, "yes", _prog(), cfg.posture, MarketExposure(), None, 0, None, 0, 50, 250, 0)
    assert (q.px, q.size, q.reason) == (0.14, 250, "ok")       # Target/4, one tick behind
    q = desired_quote(book, "no", _prog(), cfg.posture, MarketExposure(), None, 0, None, 0, 50, 250, 0)
    assert (q.px, q.size) == (0.79, 63)                        # $50 per-market cap binds


def test_posture_skips_below_reference_and_inventory_cap():
    cfg = make_cfg()
    rival = Book("T", [(0.15, 250), (0.10, 1200)], [(0.80, 20), (0.70, 1500)], T0)
    q = desired_quote(rival, "yes", _prog(), cfg.posture, MarketExposure(), None, 0, None, 0, 50, 250, 0)
    assert q.px is None and q.reason == "below_reference" and q.ahead == 250
    book = Book("T", [(0.15, 30), (0.10, 1200)], [(0.80, 20), (0.70, 1500)], T0)
    q = desired_quote(book, "yes", _prog(), cfg.posture, MarketExposure(), None, 0, None, 0, 50, 250, 250)
    assert q.reason == "inventory_cap"


def test_posture_ignores_our_own_order_when_finding_best_bid():
    cfg = make_cfg()
    # public book includes our 250 @0.14; others' best is still 0.15 -> stay at 0.14
    book = Book("T", [(0.15, 30), (0.14, 350), (0.10, 1200)], [(0.80, 20), (0.70, 1500)], T0)
    q = desired_quote(book, "yes", _prog(), cfg.posture, MarketExposure(yes_resting=250, yes_px=0.14),
                      0.14, 250, None, 0, 50, 250, 0)
    assert q.px == 0.14


def test_reprice_follows_book_and_throttles():
    pilot, ex, clock, j = make_pilot()
    pilot.setup()
    pilot.step()
    t = next(iter(pilot.markets))
    yes = pilot.om.side(t, "yes").order
    assert yes.px == 0.14
    ex.books[t]["yes"] = [[0.17, 40], [0.14, 100], [0.10, 1200]]   # best bid moves up
    clock.t += 1                                                   # inside the 5 s throttle
    pilot.step()
    assert pilot.om.side(t, "yes").order.px == 0.14
    clock.t += 10
    pilot.step()
    new = pilot.om.side(t, "yes").order
    assert new.px == 0.16 and new.order_id != yes.order_id
    assert yes.order_id not in ex.resting and new.order_id in ex.resting
    # exactly one resting order per side on the exchange
    sides = [(o["ticker"], o["outcome"]) for o in ex.resting.values()]
    assert len(sides) == len(set(sides))
    pilot.shutdown("test")


# ====================================================================== post-only
def test_create_order_is_post_only_and_maps_no_side():
    clock = FakeClock()
    ex = FakeExchange(clock)
    c = KalshiClient("https://x/trade-api/v2", ARMED, transport=ex, signer=FakeSigner(),
                     read_bucket=TokenBucket(1e9, 1e9), write_bucket=TokenBucket(1e9, 1e9),
                     sleep=lambda s: None, wall_clock=clock)
    t = ex.tickers[0]
    c.create_order(ticker=t, client_order_id="lip-a-1", outcome="yes", count=250, price=0.14, expiration_ts=123)
    c.create_order(ticker=t, client_order_id="lip-a-2", outcome="no", count=63, price=0.79)
    posts = [x for x in ex.calls if x[0] == "POST"]
    assert all(p[3]["post_only"] is True for p in posts)
    assert posts[0][3]["side"] == "bid" and posts[0][3]["price"] == "0.1400"
    assert posts[0][3]["expiration_time"] == 123 and posts[0][3]["time_in_force"] == "good_till_canceled"
    assert posts[1][3]["side"] == "ask" and posts[1][3]["price"] == "0.2100"   # NO bid 0.79 == YES ask 0.21
    assert "post_only" not in inspect.signature(KalshiClient.create_order).parameters


def test_post_only_violation_kills():
    pilot, ex, clock, j = make_pilot()
    ex.entry_fill = 5
    code = pilot.run(max_steps=3, sleep=lambda s: None)
    assert code == 2 and j.of_kind("kill")[0]["reason"] == "post_only_violation"
    assert ex.resting == {}


# ====================================================================== idempotency
def test_client_order_ids_unique_and_scoped():
    f = ClientIdFactory("run1")
    ids = [f.next() for _ in range(1000)]
    assert len(set(ids)) == 1000 and all(len(i) <= 64 for i in ids)
    assert f.owns(ids[0]) and not ClientIdFactory("run2").owns(ids[0])


def test_retry_after_network_error_reuses_client_order_id():
    pilot, ex, clock, j = make_pilot()
    ex.network_fail_once.add(("POST", "/portfolio/events/orders"))
    pilot.setup()
    pilot.step()
    posts = [x for x in ex.calls if x[0] == "POST"]
    assert posts[0][3]["client_order_id"] == posts[1][3]["client_order_id"]
    pilot.shutdown("test")


def test_duplicate_client_order_id_409_adopts_existing_order():
    pilot, ex, clock, j = make_pilot()
    pilot.setup()
    t = next(iter(pilot.markets))
    # first attempt lands, response lost -> client retries -> exchange says 409
    orig = ex._route

    def lossy(method, path, params, body, _state={"n": 0}):
        r = orig(method, path, params, body)
        if method == "POST" and _state["n"] == 0:
            _state["n"] += 1
            raise ConnectionError("response lost")
        return r
    ex._route = lossy
    pilot.step()
    ex._route = orig
    o = pilot.om.side(t, "yes").order
    assert o is not None and o.order_id in ex.resting
    assert j.of_kind("place_dedup")
    assert len([x for x in ex.resting.values() if x["ticker"] == t and x["outcome"] == "yes"]) == 1
    pilot.shutdown("test")


# ====================================================================== caps
def test_caps_enforced_with_eight_candidates():
    pilot, ex, clock, j = make_pilot()
    pilot.setup()
    for _ in range(5):
        pilot.step()
        clock.t += 3
    assert 0 < len(pilot.markets) <= 5
    for t in pilot.markets:
        assert pilot.exposure(t).max_loss() <= 50 + 1e-9
        for o in ex.resting.values():
            if o["ticker"] == t:
                assert o["remaining"] * o["px"] <= 50 + 1e-9
    assert pilot.total_max_loss() <= 250 + 1e-9
    # one market per state series
    assert len({t.split("-")[0] for t in pilot.markets}) == len(pilot.markets)
    pilot.shutdown("test")


def test_total_cap_binds_when_smaller():
    cfg = make_cfg(caps__max_total_at_risk_usd=80.0)
    pilot, ex, clock, j = make_pilot(cfg=cfg)
    pilot.setup()
    pilot.step()
    assert pilot.total_max_loss() <= 80 + 1e-9
    pilot.shutdown("test")


def test_fills_reduce_size_inventory_cap():
    pilot, ex, clock, j = make_pilot()
    pilot.setup()
    pilot.step()
    t = next(iter(pilot.markets))
    o = pilot.om.side(t, "yes").order
    ex.fill(o.order_id, 100)
    clock.t += 3
    pilot.step()
    st = pilot.om.side(t, "yes")
    assert st.filled == 100
    assert st.order.size - st.order.filled + st.filled <= 250
    pilot.shutdown("test")


# ====================================================================== cancel on exit / pull
def test_cancel_all_on_normal_exit_and_verify():
    pilot, ex, clock, j = make_pilot()
    code = pilot.run(max_steps=3, sleep=lambda s: None)
    assert code == 0 and ex.resting == {}
    assert any(c[0] == "DELETE" and c[1].endswith("/batched") for c in ex.calls)
    assert j.of_kind("stop")[-1]["clean"] is True


def test_cancel_all_on_signal():
    pilot, ex, clock, j = make_pilot()
    old = install_signal_handlers(pilot)
    try:
        def sleeper(s):
            signal.raise_signal(signal.SIGINT)
        code = pilot.run(max_steps=100, sleep=sleeper)
    finally:
        restore_signal_handlers(old)
    assert code == 130 and ex.resting == {}
    assert j.of_kind("stop")[-1]["reason"].startswith("signal:SIGINT")


def test_cancel_all_on_unexpected_exception():
    pilot, ex, clock, j = make_pilot()
    pilot.setup()
    pilot.step()
    assert ex.resting
    def boom(*a, **k):
        raise RuntimeError("bug")
    pilot.equity = boom
    pilot.setup = lambda: None
    code = pilot.run(max_steps=5, sleep=lambda s: None)
    assert code == 2 and ex.resting == {}


def test_shutdown_retries_individually_when_batch_cancel_fails():
    pilot, ex, clock, j = make_pilot()
    pilot.setup()
    pilot.step()
    ex.fail_paths["/batched"] = 500
    assert pilot.shutdown("test") is True
    assert ex.resting == {}


def test_pull_at_t_minus_60():
    clock = FakeClock()
    ex = FakeExchange(clock, close_in_s=3 * 3600)
    pilot, ex, clock, j = make_pilot(ex=ex)
    pilot.setup()
    pilot.step()
    assert ex.resting
    clock.t += 2 * 3600 + 1        # now 59 min before close
    more = pilot.step()
    assert ex.resting == {} and more is False
    assert all(m.pulled for m in pilot.markets.values())
    # every order also carried an exchange-side expiry at the pull time
    for c in ex.calls:
        if c[0] == "POST":
            assert c[3]["expiration_time"] <= T0 + 2 * 3600 + 1


# ====================================================================== kill switches
def _run_kill(pilot, ex, clock, mutate, steps=40, dt=3.0):
    pilot.setup()
    pilot.step()
    assert ex.resting
    mutate(pilot, ex, clock)

    def sleeper(s):
        clock.t += dt
    pilot.setup = lambda: None
    code = pilot.run(max_steps=steps, sleep=sleeper)
    return code


def _kill_reason(j):
    k = j.of_kind("kill")
    return k[0]["reason"] if k else None


def test_kill_api_errors():
    pilot, ex, clock, j = make_pilot()
    code = _run_kill(pilot, ex, clock, lambda p, e, c: e.fail_paths.__setitem__("/orderbook", 500))
    assert code == 2 and _kill_reason(j) == "api_errors_consecutive" and ex.resting == {}


def test_kill_stale_book():
    pilot, ex, clock, j = make_pilot()
    pilot.cfg.kill.max_consecutive_api_errors = 10_000
    pilot.cfg.kill.max_api_errors_per_10min = 10_000
    code = _run_kill(pilot, ex, clock, lambda p, e, c: e.fail_paths.__setitem__("/orderbook", 404), dt=30)
    assert code == 2 and _kill_reason(j) == "stale_book" and ex.resting == {}


def test_kill_clock_skew():
    pilot, ex, clock, j = make_pilot()
    code = _run_kill(pilot, ex, clock, lambda p, e, c: setattr(e, "skew_s", 30))
    assert code == 2 and _kill_reason(j) == "clock_skew" and ex.resting == {}


def test_kill_daily_loss_and_total_loss():
    pilot, ex, clock, j = make_pilot()

    def crash(p, e, c):
        t = next(iter(p.markets))
        e.fill(p.om.side(t, "yes").order.order_id, 250)    # $35 of YES
        e.books[t]["yes"] = [[0.01, 1200]]                  # mark collapses
    code = _run_kill(pilot, ex, clock, crash)
    assert code == 2 and _kill_reason(j) in ("total_loss", "daily_loss") and ex.resting == {}


def test_kill_switch_pnl_thresholds_unit():
    from src.live.lip_pilot.config import KillCfg
    ks = KillSwitch(KillCfg(max_total_loss_usd=1000))
    ks.check_pnl(0.0, "d1"); ks.check_pnl(-50.5, "d1")
    assert ks.tripped == "daily_loss"
    ks = KillSwitch(KillCfg(max_total_loss_usd=1000, max_daily_loss_usd=1000))
    ks.check_pnl(0.0, "d1"); ks.check_pnl(60.0, "d1"); ks.check_pnl(-41.0, "d2")
    assert ks.tripped == "drawdown"
    ks = KillSwitch(KillCfg())
    ks.check_pnl(0.0, "d1"); ks.check_pnl(-31.0, "d1")
    assert ks.tripped == "total_loss"


def test_kill_unexpected_position():
    pilot, ex, clock, j = make_pilot()
    code = _run_kill(pilot, ex, clock,
                     lambda p, e, c: (e.positions.__setitem__(next(iter(p.markets)), 7.0),
                                      setattr(p, "last_reconcile", -1e18)))
    assert code == 2 and _kill_reason(j) == "unexpected_position" and ex.resting == {}


def test_kill_foreign_order_on_managed_market():
    pilot, ex, clock, j = make_pilot()
    code = _run_kill(pilot, ex, clock, lambda p, e, c: (
        e.foreign.append({"order_id": "x1", "client_order_id": "manual", "ticker": next(iter(p.markets))}),
        setattr(p, "last_reconcile", -1e18)))
    assert code == 2 and _kill_reason(j) == "foreign_order" and ex.resting == {}


def test_kill_pennying():
    pilot, ex, clock, j = make_pilot()
    t_holder = {}

    def penny(p, e, c):
        t_holder["t"] = next(iter(p.markets))
    orig_route = ex._route
    n = itertools.count()

    def moving(method, path, params, body):
        if method == "GET" and path.endswith("/orderbook") and t_holder.get("t") and t_holder["t"] in path:
            k = next(n)
            ex.books[t_holder["t"]]["yes"] = [[0.15 + 0.01 * (k % 2), 40], [0.10, 1200]]
        return orig_route(method, path, params, body)
    ex._route = moving
    code = _run_kill(pilot, ex, clock, penny, steps=60, dt=6)
    assert code == 2 and _kill_reason(j) == "pennying" and ex.resting == {}


def test_kill_queue_ahead_rival_at_best_bid():
    pilot, ex, clock, j = make_pilot()

    def rival(p, e, c):
        t = next(iter(p.markets))
        e.books[t]["yes"] = [[0.15, 30 + 250], [0.14, 100], [0.10, 1200]]
    code = _run_kill(pilot, ex, clock, rival, steps=200, dt=10)
    assert code == 2 and _kill_reason(j) == "queue_ahead" and ex.resting == {}


def test_kill_cusum_markout():
    pilot, ex, clock, j = make_pilot()
    pilot.cfg.kill.max_total_loss_usd = 1e6
    pilot.cfg.kill.max_daily_loss_usd = 1e6
    pilot.cfg.kill.max_drawdown_usd = 1e6
    pilot.cfg.kill.cusum_h = 0.4        # one 63-lot fill at -56c adds 0.465 (k = 0.10)

    def adverse(p, e, c):
        t = next(iter(p.markets))
        e.fill(p.om.side(t, "no").order.order_id, 63)        # bought NO at 0.79
        e.books[t]["no"] = [[0.20, 20], [0.19, 1500]]         # NO collapses: markout ~ -0.5
        e.books[t]["yes"] = [[0.75, 30], [0.70, 1200]]
    code = _run_kill(pilot, ex, clock, adverse, steps=200, dt=10)
    assert code == 2 and _kill_reason(j) == "cusum_markout" and ex.resting == {}


def test_cusum_accumulates_over_fills_unit():
    from src.live.lip_pilot.config import KillCfg
    ks = KillSwitch(KillCfg())                        # k = 0.10, h = 0.60
    for i in range(3):
        ks.on_fill("T", "yes", 0.50, 10, now=float(i))
    rows = ks.update_markouts(1e6, lambda t: 0.25)    # each fill -25c -> +0.15 per fill
    assert [r["cusum"] for r in rows] == [0.15, 0.30, 0.45] and ks.tripped is None
    for i in range(2):
        ks.on_fill("T", "yes", 0.50, 10, now=1e6)
    ks.update_markouts(2e6, lambda t: 0.25)
    assert ks.tripped == "cusum_markout"
    ks2 = KillSwitch(KillCfg())
    ks2.on_fill("T", "no", 0.30, 10, now=0.0)
    ks2.update_markouts(1e6, lambda t: 0.60)          # NO value 0.40 > 0.30: favourable, CUSUM stays 0
    assert ks2.cusum == 0.0


def test_kill_exchange_inactive():
    pilot, ex, clock, j = make_pilot()
    orig = ex._route

    def closed(method, path, params, body):
        if path == "/exchange/status":
            return {"trading_active": False}
        return orig(method, path, params, body)
    code = _run_kill(pilot, ex, clock, lambda p, e, c: (setattr(e, "_route", closed),
                                                        setattr(p, "last_reconcile", -1e18)))
    assert code == 2 and _kill_reason(j) == "exchange_inactive" and ex.resting == {}


# ====================================================================== dry run never mutates
def test_dry_run_logs_would_place_and_never_mutates():
    pilot, ex, clock, j = make_pilot(gate=DISARMED, signer=None)
    assert isinstance(pilot.gw, DryRunGateway) and not hasattr(pilot.gw, "client")
    code = pilot.run(max_steps=5, sleep=lambda s: setattr(clock, "t", clock.t + 3))
    assert code == 0
    assert ex.mutating_calls() == []
    assert all(not c[4] for c in ex.calls)                     # no authenticated request at all
    assert all(not c[1].startswith("/portfolio") for c in ex.calls)
    assert j.of_kind("would_place") and j.of_kind("would_cancel") and j.of_kind("snapshot")


@pytest.mark.parametrize("cli,env,cfg_live", [c for c in itertools.product([False, True], repeat=3)
                                              if not all(c)])
def test_no_mutating_call_reachable_without_all_three_gates(cli, env, cfg_live, monkeypatch):
    """Fails if ANY state-changing HTTP call can happen unless all three gates hold."""
    if env:
        monkeypatch.setenv(LIVE_ENV_VAR, LIVE_ENV_VALUE)
    else:
        monkeypatch.delenv(LIVE_ENV_VAR, raising=False)
    cfg = make_cfg()
    cfg.live, cfg.dry_run = cfg_live, not cfg_live
    gate = resolve_gate(cfg, cli_live=cli)
    assert not gate.armed
    # even with a signer and account reads, a full run with every kill path must not mutate
    for scenario in ("normal", "kill", "signal"):
        clock = FakeClock()
        ex = FakeExchange(clock)
        pilot, ex, clock, j = make_pilot(gate=gate, cfg=cfg, ex=ex, signer="fake", account_reads=True)
        ex.foreign.append({"order_id": "z", "client_order_id": "lip-old-000001", "ticker": "OTHER"})
        if scenario == "kill":
            ex.skew_s = 60
        sleeper = (lambda s: pilot.request_stop("SIGINT")) if scenario == "signal" else \
            (lambda s: setattr(clock, "t", clock.t + 3))
        pilot.run(max_steps=5, sleep=sleeper)
        assert ex.mutating_calls() == [], (scenario, ex.mutating_calls())
    # and direct calls on the client are refused before reaching the transport
    c = KalshiClient(cfg.api.base_url, gate, transport=ex, signer=FakeSigner(),
                     read_bucket=TokenBucket(1e9, 1e9), write_bucket=TokenBucket(1e9, 1e9), sleep=lambda s: None)
    n = len(ex.calls)
    with pytest.raises(MutationBlocked):
        c.create_order(ticker="T", client_order_id="lip-x-1", outcome="yes", count=1, price=0.1)
    with pytest.raises(MutationBlocked):
        c.cancel_order("o1", "T")
    with pytest.raises(MutationBlocked):
        c.batch_cancel([("o1", "T")])
    for m in ("POST", "DELETE", "PUT", "PATCH"):
        with pytest.raises(MutationBlocked):
            c._send(m, "/portfolio/events/orders")
    assert len(ex.calls) == n


def test_armed_gate_does_mutate_sanity():
    pilot, ex, clock, j = make_pilot(gate=ARMED)
    pilot.run(max_steps=2, sleep=lambda s: None)
    assert any(c[0] == "POST" for c in ex.mutating_calls())


def test_static_only_client_reaches_transport_and_mutations_are_gated():
    """Source scan: the transport and non-GET methods appear only in client.py, inside gated paths."""
    for f in PKG.glob("*.py"):
        src = f.read_text(encoding="utf-8")
        if f.name != "client.py":
            assert "transport.request" not in src and "requests." not in src, f.name
            assert "_send(" not in src and "_mutate(" not in src, f.name
    tree = ast.parse((PKG / "client.py").read_text(encoding="utf-8"))
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
            name = getattr(call.func, "attr", None)
            if name == "_send" and fn.name not in ("_get", "_mutate"):
                pytest.fail(f"_send called outside _get/_mutate in {fn.name}")
            if name == "_mutate":
                assert fn.name in ("create_order", "cancel_order", "batch_cancel"), fn.name
            if name == "_send" and fn.name == "_get":
                assert isinstance(call.args[0], ast.Constant) and call.args[0].value == "GET"
    src = (PKG / "client.py").read_text(encoding="utf-8")
    assert "if not self.gate.armed" in src and "_permit" in src
    assert "amend" not in src.split('"""', 2)[2]   # no amend implementation outside the docstring


def test_frozen_gate_cannot_be_flipped():
    g = LiveGate(False, False, False)
    with pytest.raises(Exception):
        g.cli_live = True  # type: ignore[misc]


def test_journal_redacts_secrets(tmp_path):
    from src.live.lip_pilot.journal import Journal, read_jsonl
    j = Journal(tmp_path / "x.jsonl")
    j.write("t", headers={"KALSHI-ACCESS-SIGNATURE": "abc", "KALSHI-ACCESS-KEY": "id"}, private_key="pem")
    rec = read_jsonl(tmp_path / "x.jsonl")[0]
    assert "abc" not in str(rec) and "pem" not in str(rec) and "id" != rec["headers"]["KALSHI-ACCESS-KEY"]


# ====================================================================== reconciliation report
def test_report_compares_estimate_with_payouts(tmp_path):
    from src.live.lip_pilot.journal import Journal
    from src.live.lip_pilot.report import load_payouts, load_records, render, summarise
    pilot, ex, clock, j = make_pilot()
    pilot.journal = j = Journal(tmp_path / "run.jsonl", clock=clock)
    pilot.om.journal = pilot.gw.journal = j
    pilot.setup()
    for _ in range(20):
        pilot.step()
        clock.t += 60
    t = next(iter(pilot.markets))
    ex.fill(pilot.om.side(t, "yes").order.order_id, 10)
    pilot.step()
    pilot.shutdown("test")
    est = pilot.markets[t].est_reward_usd
    assert est > 0
    (tmp_path / "pay.csv").write_text(f"market_ticker,amount_usd\n{t},{est * 0.4:.6f}\n", encoding="utf-8")
    s = summarise(load_records([str(tmp_path / "run.jsonl")]), load_payouts(str(tmp_path / "pay.csv")))
    row = next(r for r in s["rows"] if r["market_ticker"] == t)
    assert row["est_credit_usd"] == pytest.approx(est, abs=1e-3)
    assert row["actual_over_est"] == pytest.approx(0.4, abs=0.01)
    assert row["yes_filled"] == 10 and row["fill_cost_usd"] == pytest.approx(1.4)
    assert s["implied_credit_from_balance_usd"] is not None          # balance snapshots were journaled
    out = render(s)
    assert t in out and "Prereg stop" in out


def test_report_prereg_stop_flag():
    from src.live.lip_pilot.report import summarise
    recs = [{"kind": "start", "run_id": "r", "mode": "LIVE"},
            {"kind": "snapshot", "ticker": "A", "program_id": "p", "excluded": False, "share_yes": 0.5,
             "share_no": 0.0, "est_reward_usd": 10.0}]
    assert summarise(recs, {"A": 4.0})["prereg_stop_credits_below_half_model"] is True
    assert summarise(recs, {"A": 6.0})["prereg_stop_credits_below_half_model"] is False
