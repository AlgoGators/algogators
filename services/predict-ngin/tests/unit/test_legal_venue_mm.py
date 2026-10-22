"""Queue-model tests for the Kalshi maker simulator (scripts/research/legal_venue_kalshi_mm.py)."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("numba")
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("lvmm", ROOT / "scripts" / "research" / "legal_venue_kalshi_mm.py")
lvmm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lvmm)
S = 1_000_000_000


def book(rows):
    """rows: (t_s, b1, bs1, a1, as1)."""
    T = np.array([int(r[0] * S) for r in rows], np.int64)
    BP = np.zeros((len(rows), 3))
    BS = np.zeros((len(rows), 3))
    AP = np.zeros((len(rows), 3))
    AS = np.zeros((len(rows), 3))
    for k, r in enumerate(rows):
        BP[k, 0], BS[k, 0], AP[k, 0], AS[k, 0] = r[1], r[2], r[3], r[4]
    return T, BP, BS, AP, AS


def trades(rows):
    """rows: (t_s, price, size, dir) dir +1 taker buys YES, -1 taker sells YES."""
    if not rows:
        return (np.zeros(0, np.int64), np.zeros(0), np.zeros(0), np.zeros(0, np.int8))
    return (np.array([int(r[0] * S) for r in rows], np.int64), np.array([r[1] for r in rows], float),
            np.array([r[2] for r in rows], float), np.array([r[3] for r in rows], np.int8))


def sim(b, t, q=10.0, mode=0):
    return lvmm.simulate(*b, *t, q, mode, 100.0, 0.03, 0.97, 0.0)


def test_back_of_queue_needs_queue_consumed():
    b = book([(0, 0.50, 100, 0.51, 100), (10, 0.50, 100, 0.51, 100)])
    # 60 contracts sold at our bid: queue ahead 100 -> no fill
    ft, fs, fp, fq, *_ = sim(b, trades([(5, 0.50, 60, -1)]))
    assert len(fq) == 0
    # 130 sold: 100 ahead consumed, 30 for us (our size 10/0.5 = 20 contracts)
    ft, fs, fp, fq, *_ = sim(b, trades([(5, 0.50, 130, -1)]))
    assert fq.sum() == pytest.approx(20.0)
    assert fs[0] == 1 and fp[0] == pytest.approx(0.50)


def test_cancels_ahead_shrink_queue():
    # displayed size drops to 10 without trades -> at most 10 ahead of us
    b = book([(0, 0.50, 100, 0.51, 100), (2, 0.50, 10, 0.51, 100), (10, 0.50, 10, 0.51, 100)])
    ft, fs, fp, fq, *_ = sim(b, trades([(5, 0.50, 15, -1)]))
    assert fq.sum() == pytest.approx(5.0)


def test_adds_go_behind_us():
    b = book([(0, 0.50, 10, 0.51, 100), (2, 0.50, 500, 0.51, 100), (10, 0.50, 500, 0.51, 100)])
    ft, fs, fp, fq, *_ = sim(b, trades([(5, 0.50, 15, -1)]))
    assert fq.sum() == pytest.approx(5.0)


def test_trade_through_fills_up_to_print_size():
    b = book([(0, 0.50, 1000, 0.51, 100), (10, 0.49, 50, 0.51, 100)])
    ft, fs, fp, fq, *_ = sim(b, trades([(5, 0.49, 7, -1)]))
    assert fq.sum() == pytest.approx(7.0)
    assert fp[0] == pytest.approx(0.50)


def test_requote_on_touch_change_loses_priority():
    # touch moves up to 0.51/... we rejoin at back of 0.51 (size 40)
    b = book([(0, 0.50, 0, 0.53, 100), (1, 0.51, 40, 0.53, 100), (10, 0.51, 40, 0.53, 100)])
    ft, fs, fp, fq, *_ = sim(b, trades([(5, 0.51, 30, -1)]))
    assert len(fq) == 0


def test_flb_only_long_favourite():
    b = book([(0, 0.70, 0, 0.71, 0), (10, 0.70, 0, 0.71, 0)])
    t = trades([(5, 0.71, 5, 1), (6, 0.70, 5, -1)])
    ft, fs, fp, fq, *_ = sim(b, t, mode=1)
    assert set(fs.tolist()) == {1}


def test_inventory_cap_stops_bid():
    b = book([(0, 0.50, 0, 0.51, 1000)] + [(k, 0.50, 0, 0.51, 1000) for k in range(1, 20)])
    t = trades([(k + 0.5, 0.50, 1000, -1) for k in range(19)])
    ft, fs, fp, fq, pos, *_ = lvmm.simulate(*b, *t, 10.0, 0, 2.0, 0.03, 0.97, 0.0)
    assert pos * 0.5 <= 2.0 * 10.0 + 10.0 + 1e-9


def test_latency_stale_quote_gets_sniped():
    # bids at 0.50 are pulled at t=1 (new best 0.45); a seller prints at 0.45 at t=1.1
    b = book([(0, 0.50, 0, 0.51, 100), (1, 0.45, 50, 0.51, 100), (10, 0.45, 50, 0.51, 100)])
    t = trades([(1.1, 0.45, 8, -1)])
    r0 = lvmm.simulate(*b, *t, 10.0, 0, 100.0, 0.03, 0.97, 0.0, 0)
    r1 = lvmm.simulate(*b, *t, 10.0, 0, 100.0, 0.03, 0.97, 0.0, 250_000_000)
    # zero latency: we cancelled and re-joined at back of 0.45 (50 ahead) -> no fill
    assert len(r0[3]) == 0
    # 250 ms latency: our 0.50 bid is still live and gets hit
    assert r1[3].sum() == pytest.approx(8.0) and r1[2][0] == pytest.approx(0.50)


def test_snipe_takes_stale_quote_when_mid_crosses_it():
    # book jumps from 0.50/0.51 to 0.40/0.42 with no trades; with latency + snipe our 0.50 bid is taken
    b = book([(0, 0.50, 0, 0.51, 100), (1, 0.40, 50, 0.42, 100), (10, 0.40, 50, 0.42, 100)])
    t = trades([])
    r = lvmm.simulate(*b, *t, 10.0, 0, 100.0, 0.03, 0.97, 0.0, 250_000_000, True)
    assert r[3].sum() == pytest.approx(20.0) and r[2][0] == pytest.approx(0.50)
    r0 = lvmm.simulate(*b, *t, 10.0, 0, 100.0, 0.03, 0.97, 0.0, 0, True)
    assert len(r0[3]) == 0
