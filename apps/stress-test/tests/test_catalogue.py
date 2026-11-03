from pathlib import Path

import pandas as pd
from conftest import WINDOW_AT, dates
from stress_test.catalogue import db_root, load_events, pick_variant


def test_loads_single_and_window_events(package_dir: Path) -> None:
    events = {e.id: e for e in load_events(package_dir)}
    single, window = events["EVT-S"], events["EVT-W"]

    assert single.single and not window.single
    assert single.name == "Franc floor break" and window.klass == "Major"
    # 6B is measured in the package but not named by the event.
    assert single.instruments == ("6S", "6E")
    # The catalogue's ES is the micro we trade.
    assert window.instruments == ("ZN", "MES")
    assert window.variant == "deep"
    assert (window.start, window.end) == (dates()[WINDOW_AT], dates()[WINDOW_AT + 4])


def test_pick_variant_takes_the_largest_absolute_excursion() -> None:
    measured = pd.DataFrame({"variant": ["up", "up", "down"], "move": [0.3, 0.1, -0.15]})
    assert set(pick_variant(measured, single=True)["variant"]) == {"up"}


def test_pick_variant_leaves_single_variant_events_alone() -> None:
    measured = pd.DataFrame({"variant": [None, None], "mdd": [-0.1, -0.2]})
    assert len(pick_variant(measured, single=False)) == 2
    assert len(pick_variant(measured.drop(columns="variant"), single=False)) == 2


def test_db_root_maps_only_the_index_contracts() -> None:
    assert [db_root(s) for s in ("ES", "NQ", "RTY", "YM", "ZN")] == [
        "MES",
        "MNQ",
        "M2K",
        "MYM",
        "ZN",
    ]
