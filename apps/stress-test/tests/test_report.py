from pathlib import Path

import pytest
from stress_test.catalogue import load_events
from stress_test.report import EDIT, merge_identical, run_all, selfcheck, write_report
from stress_test.shock import SCOPES, apply_edits
from stress_test.snapshot import BOOKS, load_snapshot


def test_identical_books_share_a_column() -> None:
    merged = merge_identical({"system": {"6S": -1.0}, "qt_proposal": {"6S": -1.0}, "qt": {}})
    assert merged == {"A system = B proposal": {"6S": -1.0}, "C final": {}}


def test_run_all_and_report(snapshot_dir: Path, package_dir: Path, tmp_path: Path) -> None:
    snap = load_snapshot(snapshot_dir)
    events = load_events(package_dir)
    books = merge_identical({b: snap.books[b] for b in BOOKS})
    base = "C final"
    books[EDIT] = apply_edits(books[base], {"ZN": 0.0})

    results = run_all(snap, events, books)
    assert len(results) == len(events) * len(books) * len(SCOPES) * 2

    franc = results[
        (results.event == "EVT-S") & (results.scope == "named") & (results.severity == "central")
    ]
    by_book = franc.set_index("book").worst_pct
    assert by_book["A system = B proposal"] < -2  # short the franc
    assert by_book[base] == 0.0  # the final book closed the franc short
    severe = results[
        (results.event == "EVT-S")
        & (results.book == "A system = B proposal")
        & (results.scope == "named")
    ]
    assert severe.set_index("severity").worst_pct["severe"] < by_book["A system = B proposal"]

    path = write_report(snap, books, results, tmp_path / "out", base, "ZN=0")
    text = path.read_text()
    assert (tmp_path / "out" / "results.csv").exists()
    assert "## Worst point during each event" in text and "## Exposure of C final" in text
    assert "Edit change" in text and "(C final with ZN=0)" in text
    assert "EVT-W Treasury selloff [deep]" in text
    assert "engine: $123.00" in text


def test_selfcheck_finds_exactly_the_planted_mismatch(
    snapshot_dir: Path, package_dir: Path
) -> None:
    snap = load_snapshot(snapshot_dir, weekdays_only=False)
    df = selfcheck(load_events(package_dir), snap.closes)
    bad = df[~df.ok]
    assert list(zip(bad.event, bad.root, bad.metric, strict=True)) == [("EVT-W", "ZN", "mdd")]
    assert bad.ours.iloc[0] == pytest.approx(-0.03)
    assert len(df) == 2 + 4  # two single-day moves, net and mdd for two window instruments
