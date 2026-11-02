"""Multi-strategy runner: one pass, shared memo, serial == process pool, RAM-sized pool bounds."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(Path(__file__).resolve().parent))

pytest.importorskip("numba")
from _engopt_strats import FillCount, Volume  # noqa: E402
from src.data.fills import causal_fills  # noqa: E402
from src.data.runner import ChunkContext, ram_sized_workers, run_strategies  # noqa: E402
from src.data.tape_store import TapeStore  # noqa: E402
from test_tape_store import make_tape  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    src = tmp_path / "tape.parquet"
    make_tape(src, n_tickers=80)
    return TapeStore.build_from_parquet(src, "t", print_cols=["ts", "yc", "tyes", "qty"], sort_cols=["created_time"],
                                        cache_root=tmp_path / "cache", verbose=False)


def strategies():
    return [FillCount("Fa"), FillCount("Fb"), FillCount("Fc"), Volume()]


def test_serial_matches_whole_tape(store):
    res = run_strategies(store, strategies(), store.chunk_specs(target_rows=700))
    f = causal_fills(store.col("_tk"), store.col("ts"), store.col("yc"), store.col("tyes"), store.col("qty"))
    for m in ("Fa", "Fb", "Fc"):
        g = f.by_model(m)
        assert res[f"fills_{m}"] == (len(g), int(g.fq.sum()))
    assert res["volume"] == float(store.col("qty").sum())


def test_pool_equals_serial(store):
    specs = store.chunk_specs(n_chunks=5)
    a = run_strategies(store, strategies(), specs, workers=1)
    b = run_strategies(store, strategies(), specs, workers=2)
    assert a == b


def test_memo_computes_once(store):
    ch = next(store.chunks(target_rows=10**9))
    ctx = ChunkContext(ch)
    calls = []
    for _ in range(3):
        ctx.memo("x", lambda: calls.append(1) or 42)
    assert calls == [1] and ctx.memo("x", lambda: 0) == 42


def test_duplicate_names_rejected(store):
    with pytest.raises(ValueError):
        run_strategies(store, [Volume(), Volume()], store.chunk_specs())


def test_ram_sized_workers_bounds(monkeypatch):
    assert ram_sized_workers(10**6) == 1                     # absurd per-worker need -> still 1
    assert ram_sized_workers(0.001, max_workers=3) <= 3
    assert ram_sized_workers(1.0, budget_gb=2.5, max_workers=8) <= 2
    monkeypatch.setenv("ENGOPT_MAX_WORKERS", "1")
    assert ram_sized_workers(0.001) == 1
