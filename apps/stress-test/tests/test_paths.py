from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from conftest import SINGLE_AT
from stress_test.catalogue import load_events
from stress_test.paths import event_path, history_for_replay, replay_dates, roll_adjusted
from stress_test.scenario import daily_vol
from stress_test.snapshot import load_bars, load_snapshot


@pytest.fixture
def world(snapshot_dir: Path, package_dir: Path):
    snap = load_snapshot(snapshot_dir)
    hist = history_for_replay(load_bars(snapshot_dir), snap)
    return snap, hist, {e.id: e for e in load_events(package_dir)}


def test_replay_sessions_are_the_weekdays_after_the_last_bar() -> None:
    friday = pd.Timestamp("2026-10-30")
    assert list(replay_dates(friday, 2)) == [pd.Timestamp("2026-11-02"), pd.Timestamp("2026-11-03")]
    sunday = pd.Timestamp("2026-11-01")
    assert replay_dates(sunday, 1)[0] == pd.Timestamp("2026-11-02")


def test_the_named_instrument_moves_from_its_last_close(world) -> None:
    snap, hist, events = world
    p = event_path(snap, hist, events["EVT-S"], scope="named")
    assert set(p.moves) == {"6S", "6E"}  # the event names both; neither must be held
    last = hist[hist.symbol == "6S.v.0"].close.iloc[-1]
    six = p.bars[p.bars.symbol == "6S.v.0"]
    assert len(six) == 1 and six.date.iloc[0] == p.dates[0]
    assert six.close.iloc[0] == pytest.approx(last * 1.20, rel=1e-3)
    assert six.open.iloc[0] == pytest.approx(last)
    assert list(p.calendar) == [pd.bdate_range("2012-01-02", periods=SINGLE_AT + 1)[-1]]


def test_an_unmoved_instrument_alternates_half_a_daily_sd_around_its_close(world) -> None:
    snap, hist, events = world
    p = event_path(snap, hist, events["EVT-W"], scope="named")
    assert "CL" not in p.moves
    cl = p.bars[p.bars.symbol == "CL.v.0"].close.to_numpy()
    last = hist[hist.symbol == "CL.v.0"].close.iloc[-1]
    half = 0.5 * daily_vol(snap.closes["CL"])
    assert cl / last - 1 == pytest.approx(half * np.array([1, -1, 1, -1, 1]), rel=1e-6)


def test_every_bar_has_a_range_and_the_usual_volume_and_contract(world) -> None:
    snap, hist, events = world
    p = event_path(snap, hist, events["EVT-W"], scope="sector")
    assert (p.bars.high > p.bars.low).all()  # the classifier holds a bar with no range
    assert (p.bars.high >= p.bars[["open", "close"]].max(axis=1)).all()
    assert (p.bars.low <= p.bars[["open", "close"]].min(axis=1)).all()
    assert (p.bars.volume == 1000.0).all()
    zn = p.bars[p.bars.symbol == "ZN.v.0"]
    assert set(zn.instrument_id) == {hist[hist.symbol == "ZN.v.0"].instrument_id.iloc[-1]}
    assert set(p.moves) == {"ZN", "ZT", "MES"}  # sector: ZT shares ZN's sector; ES is MES
    assert p.uncovered == ()


def test_the_no_event_path_moves_nothing(world) -> None:
    snap, hist, _ = world
    p = event_path(snap, hist, None, sessions=4)
    assert p.moves == {} and len(p.dates) == 4
    for sym, g in p.bars.groupby("symbol"):
        last = hist[hist.symbol == sym].close.iloc[-1]
        assert abs(g.close.mean() / last - 1) < 0.01  # it alternates around the last close
    with pytest.raises(ValueError):
        event_path(snap, hist, None, sessions=0)


def test_the_history_ends_before_the_day(world) -> None:
    snap, hist, _ = world
    assert hist.date.max() < pd.Timestamp(snap.day)


def test_a_roll_gap_is_taken_out_and_the_series_ends_at_todays_price() -> None:
    bars = pd.DataFrame(
        {
            "date": pd.bdate_range("2026-01-05", periods=5),
            "symbol": "CL.v.0",
            "open": [100.0, 101.0, 110.0, 111.0, 112.0],
            "high": [101.0, 102.0, 111.0, 112.0, 113.0],
            "low": [99.0, 100.0, 109.0, 110.0, 111.0],
            "close": [100.0, 101.0, 110.0, 111.0, 112.0],
            "volume": 1.0,
            "instrument_id": ["1", "1", "2", "2", ""],  # a roll on bar 3; bar 5 has no id
        }
    )
    adj = roll_adjusted(bars)
    returns = (adj.close / adj.close.shift(1) - 1).round(6).tolist()
    assert returns[2] == 0.0  # the roll bar books no move
    assert returns[3] == round(1 / 110, 6) and returns[4] == round(1 / 111, 6)
    assert adj.close.iloc[-1] == 112.0 and adj.close.iloc[2] == 110.0  # today's prices unchanged
    assert adj.close.iloc[0] == pytest.approx(100.0 * 110 / 101)  # the gap taken out before it
    assert adj.high.iloc[0] / adj.close.iloc[0] == pytest.approx(101 / 100)  # bar shapes kept
