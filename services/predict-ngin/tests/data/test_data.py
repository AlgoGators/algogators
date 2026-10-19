import json
from pathlib import Path

import pandas as pd
import pytest

from trading.data_modules.data import (
    build_resolution_map,
    load_manifold_data,
    load_markets,
    train_test_split,
)


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def test_load_manifold_data_reads_and_sorts(tmp_path: Path) -> None:
    data_dir = tmp_path / "manifold"
    t1 = pd.Timestamp("2026-01-01 00:00:00")
    t2 = pd.Timestamp("2026-01-02 00:00:00")
    bets = [
        {
            "createdTime": int(t2.timestamp() * 1000),
            "amount": -50,
            "outcome": "YES",
            "probAfter": 0.6,
            "userId": "u2",
            "contractId": "m2",
        },
        {
            "createdTime": int(t1.timestamp() * 1000),
            "amount": 20,
            "outcome": "NO",
            "probAfter": 0.4,
            "userId": "u1",
            "contractId": "m1",
        },
    ]
    _write_json(data_dir / "bets_1.json", bets)

    df = load_manifold_data(data_dir=str(data_dir), auto_fetch=False, use_db=False)

    assert len(df) == 2
    assert list(df["createdTime"]) == [int(t1.timestamp() * 1000), int(t2.timestamp() * 1000)]
    assert "datetime" in df.columns
    assert "date" in df.columns
    assert "month" in df.columns
    assert "amount_abs" in df.columns
    assert df.loc[0, "amount_abs"] == 20
    assert df.loc[1, "amount_abs"] == 50


def test_load_manifold_data_raises_when_missing(tmp_path: Path) -> None:
    data_dir = tmp_path / "manifold"
    with pytest.raises(ValueError):
        load_manifold_data(data_dir=str(data_dir), auto_fetch=False, use_db=False)


def test_load_markets_reads_data(tmp_path: Path) -> None:
    data_dir = tmp_path / "manifold"
    markets = [
        {"id": "m1", "question": "Market 1"},
        {"id": "m2", "question": "Market 2"},
    ]
    _write_json(data_dir / "markets_1.json", markets)

    df = load_markets(data_dir=str(data_dir), use_db=False)

    assert len(df) == 2
    assert set(df["id"]) == {"m1", "m2"}


def test_build_resolution_map_handles_open_and_resolved() -> None:
    markets_df = pd.DataFrame(
        [
            {
                "id": "m1",
                "isResolved": True,
                "resolution": "YES",
                "resolutionTime": 111,
                "question": "Resolved?",
                "totalLiquidity": 1200,
                "volume": 50,
            },
            {
                "id": "m2",
                "isResolved": False,
                "probability": 0.42,
                "closeTime": 222,
                "question": "Open?",
                "totalLiquidity": 800,
                "volume": 10,
            },
        ]
    )

    closed_only = build_resolution_map(markets_df, include_open=False)
    assert "m1" in closed_only
    assert "m2" not in closed_only
    assert closed_only["m1"]["resolution"] == 1.0
    assert closed_only["m1"]["status"] == "resolved"

    with_open = build_resolution_map(markets_df, include_open=True)
    assert "m1" in with_open
    assert "m2" in with_open
    assert with_open["m2"]["current_prob"] == 0.42
    assert with_open["m2"]["status"] == "open"
    assert with_open["m2"]["is_resolved"] is False


def test_train_test_split_time_order() -> None:
    df = pd.DataFrame(
        {"datetime": pd.date_range("2026-01-01", periods=10, freq="D")}
    )
    train_df, test_df = train_test_split(df, train_ratio=0.5)

    assert len(train_df) > 0
    assert len(test_df) > 0
    assert train_df["datetime"].max() < test_df["datetime"].min()


def test_train_test_split_recent_days_window() -> None:
    df = pd.DataFrame(
        {"datetime": pd.date_range("2026-01-01", periods=10, freq="D")}
    )
    train_df, test_df = train_test_split(df, test_days=3)

    assert test_df["datetime"].min() == pd.Timestamp("2026-01-08")
    assert test_df["datetime"].max() == pd.Timestamp("2026-01-10")
    assert train_df["datetime"].max() == pd.Timestamp("2026-01-07")
