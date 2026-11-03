from pathlib import Path

import pandas as pd
import pytest
from conftest import SINGLE_AT, WINDOW_AT, ZN_WINDOW, dates
from stress_test.catalogue import load_events
from stress_test.shock import apply_edits, in_scope, parse_edits, shock_book
from stress_test.snapshot import load_snapshot


@pytest.fixture
def world(snapshot_dir: Path, package_dir: Path):
    snap = load_snapshot(snapshot_dir)
    events = {e.id: e for e in load_events(package_dir)}
    return snap, events


def test_single_session_shock_is_contracts_times_notional_times_move(world) -> None:
    snap, events = world
    notional = snap.notional_per_contract()
    s = shock_book(snap.books["system"], notional, events["EVT-S"], snap.closes)

    expected = -1 * notional["6S"] * 0.20  # short one franc contract, franc +20%
    assert s.worst == pytest.approx(expected, rel=1e-3)
    assert s.end == s.worst
    assert s.worst_date == dates()[SINGLE_AT]
    assert s.contributors()[0][0] == "6S"
    assert s.uncovered == ()
    assert s.moves["6S"].k == pytest.approx(1.0, abs=1e-2)


def test_scopes_widen_from_named_to_sector_to_all(world) -> None:
    snap, events = world
    notional = snap.notional_per_contract()
    ev = events["EVT-W"]
    book = snap.books["system"]  # 6S -1, ZN -2, CL +1, ZT -1

    named = shock_book(book, notional, ev, snap.closes, "named", sectors=snap.sectors)
    sector = shock_book(book, notional, ev, snap.closes, "sector", sectors=snap.sectors)
    every = shock_book(book, notional, ev, snap.closes, "all", sectors=snap.sectors)

    assert set(named.pnl.columns) == {"ZN"}
    assert set(sector.pnl.columns) == {"ZN", "ZT"}  # ZT shares ZN's sector
    assert set(every.pnl.columns) == {"ZN", "ZT", "6S", "CL"}
    # Short ZN gains as it falls; the path's end is +0.5%, so the short ends down.
    zn = named.pnl["ZN"].tolist()
    assert zn == pytest.approx([-2 * notional["ZN"] * lv for lv in ZN_WINDOW], rel=1e-3)
    assert named.end == pytest.approx(zn[-1])
    assert named.worst == pytest.approx(min(0.0, *zn))
    assert named.worst_date == dates()[WINDOW_AT + 3]


def test_a_book_that_only_gains_has_no_worst_point(world) -> None:
    snap, events = world
    notional = snap.notional_per_contract()
    s = shock_book({"6S": 1.0}, notional, events["EVT-S"], snap.closes)
    assert s.worst == 0.0 and s.worst_date is None and s.end > 0
    assert s.contributors() == [("6S", pytest.approx(s.end))]


def test_a_book_holding_nothing_named_is_untouched(world) -> None:
    snap, events = world
    s = shock_book({"CL": 1.0}, snap.notional_per_contract(), events["EVT-S"], snap.closes)
    assert (s.worst, s.end, s.contributors()) == (0.0, 0.0, [])


def test_missing_contract_size_is_an_error(world) -> None:
    snap, events = world
    with pytest.raises(ValueError, match="contract size"):
        shock_book({"6S": -1.0}, {}, events["EVT-S"], snap.closes)


def test_unknown_scope_is_an_error(world) -> None:
    _, events = world
    with pytest.raises(ValueError, match="scope"):
        in_scope(["6S"], events["EVT-S"], "everything")


def test_parse_and_apply_edits() -> None:
    assert parse_edits("6s=0, ZT=-1,") == {"6S": 0.0, "ZT": -1.0}
    assert apply_edits({"6S": -1.0, "ZN": -2.0}, {"6S": 0.0, "CL": 1.0}) == {"ZN": -2.0, "CL": 1.0}
    for bad in ("6S", "=1", "6S=x"):
        with pytest.raises(ValueError):
            parse_edits(bad)


def test_shock_has_an_empty_path_when_nothing_moves(world) -> None:
    snap, events = world
    s = shock_book({}, {}, events["EVT-W"], snap.closes)
    assert s.pnl.empty and isinstance(s.total, pd.Series) and s.end == 0.0
