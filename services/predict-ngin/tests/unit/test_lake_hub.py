"""Hugging Face snapshot helpers: what a snapshot holds, the month check, stale-file pruning."""
from __future__ import annotations

import json

import pandas as pd

from predict_ngin.lake import hub


def _lake(root, months: dict) -> None:
    """months: {"2024-07": [rows_in_part0, rows_in_part1, ...]}"""
    state = {"_complete": True, "_source_end_ts": 1722470400}
    for m, parts in months.items():
        y, mo = m.split("-")
        d = root / f"lake/polymarket/fills/year={y}/month={mo}"
        d.mkdir(parents=True)
        for i, n in enumerate(parts):
            pd.DataFrame({"x": range(n)}).to_parquet(d / f"part-{i:02d}.parquet")
        state[m] = {"files": len(parts), "rows": sum(parts), "usd": 1.0}
    (root / "lake/polymarket/fills_state.json").write_text(json.dumps(state))


def test_check_lake_matches_state_and_reports_drift(tmp_path):
    _lake(tmp_path, {"2024-06": [3], "2024-07": [2, 4]})
    assert hub.check_lake(tmp_path) == []
    (tmp_path / "lake/polymarket/fills/year=2024/month=07/part-01.parquet").unlink()
    bad = hub.check_lake(tmp_path)
    assert len(bad) == 1 and bad[0].startswith("2024-07: 1 files / 2 rows")
    assert hub.check_lake(tmp_path / "empty") != []


def test_snapshot_files_keep_only_published_paths(tmp_path):
    _lake(tmp_path, {"2024-07": [1]})
    ext = tmp_path / "polymarket_clean/trades_ext"
    for rel in ["hf/part-rg00001.parquet", "hf/part-rg00002.parquet.tmp", "hf/_manifest/part-rg00001.json",
                "tail/fills_2026-10-05.parquet", "tail/raw/orderfilled_2026-10-05.parquet",
                "tail/progress.json", "coverage.json", "build.log", ".lock"]:
        (ext / rel).parent.mkdir(parents=True, exist_ok=True)
        (ext / rel).write_text("x")
    (tmp_path / "lake/polymarket/retired_sources.json").write_text("{}")  # holds local paths
    files = hub.snapshot_files(tmp_path)
    assert files == [
        "lake/polymarket/fills/year=2024/month=07/part-00.parquet",
        "lake/polymarket/fills_state.json",
        "polymarket_clean/trades_ext/coverage.json",
        "polymarket_clean/trades_ext/hf/_manifest/part-rg00001.json",
        "polymarket_clean/trades_ext/hf/part-rg00001.parquet",
        "polymarket_clean/trades_ext/tail/fills_2026-10-05.parquet",
    ]


def test_prune_removes_parts_a_newer_snapshot_replaced(tmp_path):
    _lake(tmp_path, {"2024-06": [1, 1], "2024-07": [1, 1, 1]})
    keep = {"lake/polymarket/fills/year=2024/month=07/part-00.parquet",
            "lake/polymarket/fills/year=2024/month=07/part-01.parquet"}
    assert hub._prune(tmp_path, keep) == 1  # 2024-07 part-02 goes; 2024-06 is not in the snapshot
    assert not (tmp_path / "lake/polymarket/fills/year=2024/month=07/part-02.parquet").exists()
    assert len(list((tmp_path / "lake/polymarket/fills/year=2024/month=06").glob("*.parquet"))) == 2


def test_dataset_card_states_coverage(tmp_path):
    _lake(tmp_path, {"2024-06": [3], "2024-07": [2]})
    card = hub.dataset_card(tmp_path, "someone/pm")
    assert card.startswith("---\nlicense: mit")
    assert "from 2024-06 to 2024-08-01 (exclusive): 5 rows" in card
    assert "hf download someone/pm --type dataset" in card


def test_repo_id_precedence(monkeypatch):
    monkeypatch.delenv(hub.REPO_ENV_VAR, raising=False)
    assert hub.repo_id() == hub.DEFAULT_REPO
    monkeypatch.setenv(hub.REPO_ENV_VAR, "a/b")
    assert hub.repo_id() == "a/b" and hub.repo_id("c/d") == "c/d"


def test_snapshot_manifest_keeps_polymarket_only_and_put_restores_it(tmp_path, monkeypatch):
    from predict_ngin.lake import manifest

    monkeypatch.setenv("PREDICT_NGIN_DATA", str(tmp_path))
    manifest.update("polymarket/markets", rows=4, tagged_events=True)
    manifest.update("kalshi/trades", rows=9)
    snap = hub.snapshot_manifest()
    assert set(snap) == {"polymarket/markets"}  # never another venue's entries

    other = tmp_path / "other"
    monkeypatch.setenv("PREDICT_NGIN_DATA", str(other))
    manifest.update("kalshi/trades", rows=1)
    manifest.put(snap)
    m = manifest.load()
    assert m["polymarket/markets"] == snap["polymarket/markets"]  # `updated` kept as published
    assert m["kalshi/trades"]["rows"] == 1
