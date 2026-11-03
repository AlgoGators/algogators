import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from conftest import EQUITY, dates
from stress_test import engine, replay
from stress_test.catalogue import load_events
from stress_test.paths import EventPath
from stress_test.snapshot import load_snapshot

LAST = dates()[-1]  # the last stored bar


@pytest.fixture
def session(snapshot_dir: Path, tmp_path: Path) -> replay.Session:
    return replay.Session(load_snapshot(snapshot_dir), tmp_path / "work", "/engine/dir", lead_in=3)


def test_the_books_are_merged_and_the_edit_added(snapshot_dir: Path) -> None:
    snap = load_snapshot(snapshot_dir)
    books = replay.replay_books(snap, {"ZT": 0.0}, "qt")
    assert set(books) == {"A system = B proposal", "C final", "Edit"}
    assert books["Edit"] == {"ZN": -2.0, "CL": 1.0}
    with pytest.raises(SystemExit, match="no nope book"):
        replay.replay_books(snap, {"ZT": 0.0}, "nope")


def test_a_run_starts_the_warm_up_and_the_lead_in_before_its_first_session(
    session: replay.Session,
) -> None:
    first = (LAST + pd.offsets.BDay(1)).date()
    assert session.warmup == 256
    # sized on half compounding's capital: the account less its drawdown from the peak (2,500)
    book = json.loads(
        (Path(session.config_dir) / "portfolios" / "stress" / "portfolio.json").read_text()
    )
    assert session.sizing_capital == book["initial_capital"] == EQUITY - 2500.0
    assert session.start_for(first) == dates()[-(256 + 3)].date()
    assert session.start_for(dates()[2].date()) == dates()[0].date()  # not enough history: all


def test_replays_are_planned_per_event_and_severity_with_no_event_twins(
    session: replay.Session, package_dir: Path
) -> None:
    specs, paths, skipped = replay.plan_replays(session, load_events(package_dir))
    assert skipped == []
    assert (
        set(specs)
        == set(paths)
        == {
            "EVT-S~central",
            "EVT-S~severe",
            "EVT-W~central",
            "EVT-W~severe",
            "none~1",
            "none~5",
        }
    )
    s = specs["EVT-W~central"]
    first = (LAST + pd.offsets.BDay(1)).date()
    assert s.start == session.start_for(first).isoformat()
    assert s.keep_from == LAST.date().isoformat()  # the close before the event comes back too
    assert s.end == (LAST + pd.offsets.BDay(5)).date().isoformat()
    assert s.bars[0].endswith("history.csv") and s.bars[1].endswith("EVT-W~central.csv")
    assert s.module_dir == "/engine/dir"


def test_the_difference_lasts_until_the_strategy_trades_the_symbol() -> None:
    idx = pd.bdate_range("2026-11-02", periods=4)
    books = pd.DataFrame({"ZN": [-1.0, -1.0, -3.0, -3.0], "CL": [0.0, 0.0, 0.0, 0.0]}, index=idx)
    diff = {"ZN": -1.0, "CL": 1.0, "ZT": -1.0}
    held = replay.difference_path(diff, books, "until-traded")
    assert held["ZN"].tolist() == [-1.0, -1.0, 0.0, 0.0]  # the strategy traded ZN on session 3
    assert held["CL"].tolist() == [1.0] * 4 and held["ZT"].tolist() == [-1.0] * 4
    assert replay.difference_path(diff, books, "held")["ZN"].tolist() == [-1.0] * 4
    with pytest.raises(ValueError):
        replay.difference_path(diff, books, "forever")


def test_the_difference_is_priced_session_by_session_on_the_path() -> None:
    idx = pd.bdate_range("2026-11-02", periods=3)
    levels = pd.DataFrame({"ZN": [0.01, -0.02, 0.0]}, index=idx)
    path = EventPath(pd.DataFrame(), idx, idx, {}, (), levels, {"ZN": 100.0})
    held = pd.DataFrame({"ZN": [2.0, 1.0, 1.0], "XX": [1.0, 1.0, 1.0]}, index=idx)
    pnl, unpriced = replay.difference_pnl(held, path, {"ZN": 1000.0})
    # 2 x 100 x 1000 x (+1%), then 1 x (-3%), then 1 x (+2%)
    assert pnl.tolist() == pytest.approx([2000.0, 2000.0 - 3000.0, 2000.0 - 3000.0 + 2000.0])
    assert unpriced == ["XX"]


def _canned(spec: engine.RunSpec) -> engine.RunResult:
    """The strategy holds ZN -1 and trades it to -3 on the last session; event runs lose 1% of the
    account from the event's second session, no-event runs 0.1%."""
    after = pd.bdate_range(pd.Timestamp(spec.keep_from) + pd.offsets.BDay(1), spec.end)
    days = [spec.keep_from] + [d.date().isoformat() for d in after]
    loss = 0.001 if "none" in spec.out_dir else 0.01
    eq = [(d, EQUITY * (1 - loss * (i >= 2))) for i, d in enumerate(days)]
    last = len(days) - 1
    pos = [(d, "ZN.v.0", -3.0 if i == last and i > 1 else -1.0) for i, d in enumerate(days)]
    return engine.RunResult(True, equity=eq, positions=pos)


def test_an_outcome_reads_the_strategys_path_from_the_close_before(
    session: replay.Session, package_dir: Path
) -> None:
    specs, paths, _ = replay.plan_replays(session, load_events(package_dir))
    o = replay.outcome(_canned(specs["EVT-W~central"]), paths["EVT-W~central"].path.dates)
    assert o.pnl.tolist() == pytest.approx([0.0] + [-0.01 * EQUITY] * 4)
    assert o.start_book == {"ZN": -1.0}
    assert o.traded == 2.0  # ZN -1 -> -3 on the last session


def test_the_summary_adds_the_tested_books_difference_to_the_strategy(
    session: replay.Session, package_dir: Path, tmp_path: Path
) -> None:
    specs, paths, _ = replay.plan_replays(session, load_events(package_dir))
    seen: list[str] = []
    results = replay.run_all(specs, 2, runner=_canned, progress=lambda n, r: seen.append(n))
    assert sorted(seen) == sorted(specs)
    books = {"C final": session.snap.books["qt"]}  # ZN -2, CL +1, ZT -1
    table = replay.summarise(session, paths, results, books)
    w = table[(table.event == "EVT-W") & (table.severity == "central")].iloc[0]
    assert w.strategy_start_book == "ZN -1" and w.held_difference == "CL +1, ZN -1, ZT -1"
    assert w.strategy_end_pct == pytest.approx(-1.0)
    # The ZN difference ends when the strategy trades ZN; the end book is the strategy's plus what
    # is still held (CL +1, ZT -1).
    assert w.end_book == "CL +1, ZN -3, ZT -1"
    path = paths["EVT-W~central"].path
    zn = path.base["ZN"] * 1000 * path.levels["ZN"].diff().fillna(path.levels["ZN"].iloc[0])
    cl = path.base["CL"] * 1000 * path.levels["CL"]
    zt = path.base["ZT"] * 2000 * path.levels["ZT"]
    expected = (-1 * zn.iloc[:4]).sum() + cl.iloc[-1] - zt.iloc[-1]
    assert w.difference_end_pct == pytest.approx(100 * expected / EQUITY)
    assert w.traded_end_pct == pytest.approx(w.strategy_end_pct + w.difference_end_pct)
    assert w.no_event_end_pct == pytest.approx(w.traded_end_pct - w.event_effect_end_pct)
    held = replay.summarise(session, paths, results, books, mode="held")
    wh = held[(held.event == "EVT-W") & (held.severity == "central")].iloc[0]
    assert wh.end_book == "CL +1, ZN -4, ZT -1"  # ZN -3 of the strategy's, -1 still held
    caps = engine.Capabilities("9.9", "/x", True, False, True)
    text = replay.write_replay_report(
        session.snap, caps, table, books, tmp_path / "r", 3
    ).read_text()
    assert "tradengin 9.9" in text and "unchanged" in text
    assert "Caveat: this engine's Bar has no instrument_id" in text
    assert "| C final | CL +1, ZN -2, ZT -1 | CL +1, ZN -1, ZT -1 |" in text
    assert "| EVT-W Treasury selloff | 5 |" in text and "until the strategy trades" in text
    assert (tmp_path / "r" / "replay.csv").exists()
    text_held = replay.write_replay_report(
        session.snap, caps, held, books, tmp_path / "h", 3, mode="held"
    ).read_text()
    assert "held through the" in text_held


def test_a_failed_run_is_listed_not_summarised(
    session: replay.Session, package_dir: Path, tmp_path: Path
) -> None:
    specs, paths, _ = replay.plan_replays(session, load_events(package_dir)[:1])
    results = {n: engine.RunResult(False, "boom") for n in specs}
    books = {"C final": session.snap.books["qt"]}
    table = replay.summarise(session, paths, results, books)
    assert not table.ok.any() and set(table.error) == {"boom"}
    caps = engine.Capabilities(None, "/x", True, True, True)
    text = replay.write_replay_report(session.snap, caps, table, books, tmp_path, 3).read_text()
    assert "## Runs that failed" in text and "boom" in text


def test_the_check_runs_each_stored_day_as_a_replay_starts(session: replay.Session) -> None:
    specs, stored = replay.plan_check(session)
    days = [d.date().isoformat() for d in dates()[-3:]]
    assert list(specs) == days
    for day, d in zip(days, dates()[-3:], strict=True):
        assert specs[day].keep_from == specs[day].end == day
        assert specs[day].start == session.start_for(d.date()).isoformat()
    assert sorted(stored.date.dt.date.astype(str).unique()) == days
    assert list(replay.plan_check(session, last=1)[0]) == days[-1:]
    own = {"6S.v.0": -1.0, "ZN.v.0": -2.0, "CL.v.0": 1.0, "ZT.v.0": -1.0}
    results = {
        d: engine.RunResult(True, positions=[(d, s, q) for s, q in own.items()]) for d in days
    }
    results[days[0]] = engine.RunResult(False, "boom")
    table = replay.check_table(results, stored)
    assert table.ok.tolist() == [False, True, True]
    done = table[table.ok]
    assert done.match.tolist() == [True, False]  # the system book went to ZT -2 on the last day
    assert done.contracts_apart.tolist() == [0.0, 1.0]
    assert done.differences.iloc[1] == "ZT:-2/-1"
    assert np.isclose(done.contracts_apart.mean(), 0.5)
