"""Evaluate N strategies in ONE pass over a TapeStore, serially or on a RAM-sized process pool. Research only.

A strategy is any object with
    name: str
    on_chunk(chunk: Chunk, ctx: ChunkContext) -> partial      # called once per chunk
    finalize(partials: list) -> result                         # called once, partials in chunk order
Work shared by several strategies (row filters, derived columns, the Fa/Fb/Fc fills) goes through
`ctx.memo(key, fn)`: computed by the first strategy that asks, reused by the rest for that chunk.

    res = run_strategies("kalshi_base", [A(), B(), C()], chunk_specs=store.chunk_specs(n_chunks=48), workers="auto",
                         per_worker_gb=1.5)

Workers open the store by path (mmap) -- the tape is never pickled or copied per worker; the OS page cache holds one
copy shared by all processes. Only strategies (pickled once per worker) and partial results cross processes.
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

from .memutil import peak_private, peak_rss, ram_available
from .tape_store import Chunk, TapeStore


class Strategy(Protocol):
    name: str

    def on_chunk(self, chunk: Chunk, ctx: "ChunkContext") -> Any: ...

    def finalize(self, partials: list) -> Any: ...


class ChunkContext:
    """Per-chunk memo shared by all strategies; also records time spent per key (shared work) for profiling."""

    def __init__(self, chunk: Chunk, index: int = 0):
        self.chunk = chunk
        self.index = index  # chunk number in the run (stable across serial / pool) -- e.g. for spill file names
        self._memo: dict = {}
        self.timings: dict[str, float] = {}

    def memo(self, key, fn: Callable[[], Any]):
        if key not in self._memo:
            t = time.perf_counter()
            self._memo[key] = fn()
            self.timings[f"shared:{key}"] = time.perf_counter() - t
        return self._memo[key]


def ram_sized_workers(per_worker_gb: float, *, reserve_gb: float = 4.0, budget_gb: float | None = None,
                      max_workers: int | None = None) -> int:
    """Pool size from memory, not CPU count: floor(min(available - reserve, budget) / per_worker), clamped to
    [1, min(max_workers, cpu_count)]. per_worker_gb = a worker's PRIVATE peak (chunk copies + results), not the
    mmap'd tape, which is shared page cache. $ENGOPT_MAX_WORKERS caps it further (shared machines)."""
    avail = ram_available() / 1e9 - reserve_gb
    if budget_gb is not None:
        avail = min(avail, budget_gb)
    n = int(avail // max(per_worker_gb, 1e-3))
    cap = os.cpu_count() or 1
    if max_workers:
        cap = min(cap, max_workers)
    env = os.environ.get("ENGOPT_MAX_WORKERS")
    if env:
        cap = min(cap, int(env))
    return max(1, min(n, cap))


# ---------------------------------------------------------------------- worker plumbing (module level: picklable)
_W: dict = {}


def _init_worker(store_path: str, strategies):
    _W["store"] = TapeStore.open(Path(store_path))
    _W["strategies"] = strategies


def _run_chunk(store: TapeStore, strategies, codes, i: int):
    t = time.perf_counter()
    ch = store.chunk(codes)
    ctx = ChunkContext(ch, i)
    out = {}
    tim = {}
    for s in strategies:
        t1 = time.perf_counter()
        out[s.name] = s.on_chunk(ch, ctx)
        tim[s.name] = time.perf_counter() - t1
    shared = sum(ctx.timings.values())
    tim.update(ctx.timings)
    tim["_chunk_total"] = time.perf_counter() - t
    tim["_shared_total"] = shared
    tim["_rows"] = len(ch)
    tim["_pid"] = os.getpid()
    tim["_peak_rss"] = peak_rss()  # this process's peak working set so far (bytes; includes mmap'd tape pages)
    tim["_peak_private"] = peak_private()  # peak private commit (bytes; excludes the shared mmap)
    return i, out, tim


def _worker_task(codes, i):
    return _run_chunk(_W["store"], _W["strategies"], codes, i)


def run_strategies(store: TapeStore | str, strategies: Sequence[Strategy], chunk_specs=None, *,
                   workers: int | str = 1, per_worker_gb: float = 1.5, reserve_gb: float = 4.0,
                   budget_gb: float | None = None, max_workers: int | None = None, progress: bool = False,
                   return_timings: bool = False):
    """One pass over the chunks; every strategy sees every chunk. Returns {strategy.name: finalize(partials)}
    (and per-chunk timings if return_timings). workers='auto' -> ram_sized_workers(...)."""
    if isinstance(store, str):
        from .tapes import open_tape
        store = open_tape(store)
    if chunk_specs is None:
        chunk_specs = store.chunk_specs()
    if workers == "auto":
        workers = ram_sized_workers(per_worker_gb, reserve_gb=reserve_gb, budget_gb=budget_gb,
                                    max_workers=max_workers)
    names = [s.name for s in strategies]
    if len(set(names)) != len(names):
        raise ValueError(f"strategy names must be unique: {names}")
    parts: dict[int, dict] = {}
    timings: list = [None] * len(chunk_specs)
    if workers <= 1:
        for i, codes in enumerate(chunk_specs):
            _, out, tim = _run_chunk(store, strategies, codes, i)
            parts[i] = out; timings[i] = tim
            if progress:
                print(f"  chunk {i + 1}/{len(chunk_specs)} rows={tim['_rows']:,} {tim['_chunk_total']:.2f}s", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker,
                                 initargs=(str(store.path), list(strategies))) as ex:
            futs = [ex.submit(_worker_task, codes, i) for i, codes in enumerate(chunk_specs)]
            for f in futs:
                i, out, tim = f.result()
                parts[i] = out; timings[i] = tim
                if progress:
                    print(f"  chunk {i + 1}/{len(chunk_specs)} rows={tim['_rows']:,} {tim['_chunk_total']:.2f}s",
                          flush=True)
    res = {s.name: s.finalize([parts[i][s.name] for i in range(len(chunk_specs))]) for s in strategies}
    return (res, timings) if return_timings else res
