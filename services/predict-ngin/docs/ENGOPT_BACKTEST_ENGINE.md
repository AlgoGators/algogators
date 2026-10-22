# ENGOPT: research backtest engine for many strategies at once (2026-10-16)

Last revised: 2026-10-16

This is research infrastructure only. It places no orders and uses no keys. Branch: `worktree-agent-ad66bd96c39bcc31a`, based on `fix/backtest-integrity`.

## 1. What the research path actually does

The current studies (`mkgap_*`, `mrev_*`, `audit_flb_*`, `micro_*`, `sharp_maker`, `wx_maker` and others) do not use `src/trading/engine.py` or `src/backtest*`. Those modules are the older Polymarket engines. The studies all follow the same three steps:

1. **Load a Parquet tape with DuckDB in hash chunks.** Examples are `data/vendor/micro/base.parquet` (165 M prints, 570 k tickers) and `data/vendor/ment5/tape.parquet` (14 M prints). The query is `where hash(ticker) % n = k ... order by ticker, created_time`. About 30 scripts use this pattern.
2. **Run a causal fill simulation** with the Fa, Fb and Fc models, using `audit_flb_common.causal_opps` (pure Python) or its numba port `mkgap_causal._engine`.
3. **Post-process with pandas:** fill frames, cell aggregates, and `audit_flb_common.simulate` portfolio P&L.

The path that matters for "many strategies at once" is therefore the shared tape plus the fill layer.

## 2. Profile first (before)

The representative run is mkgap Part B in full: the Fa, Fb and Fc cell aggregates plus the family-volume table (four "strategies") over all of `base.parquet` in 48 chunks. The harness is `scripts/bench/engopt_profile_baseline.py`, and the raw numbers are in `docs/engopt/baseline_full.json` and `baseline_profile.json`.

| Phase (48 chunks)                                      | Seconds | Share |
|--------------------------------------------------------|--------:|------:|
| DuckDB scan, hash filter, join and sort, to Arrow (I/O) | 297.8   | 70%   |
| Arrow to pandas (parse)                                | 0.9     | <1%   |
| Numba fill engine, 3 models                            | 7.8     | 2%    |
| Fill-frame build and string-keyed aggregate (post)     | 92.2    | 22%   |
| **Wall time**                                          | **424** |       |

- Peak working set was 3.2 GB and peak private memory was 3.9 GB. Both were measured on 3 chunks with results discarded, so a real run that keeps its results would need more.
- **The fill engine is not the bottleneck.** The hash predicate cannot be pushed down, so each of the 48 chunks decodes the whole 1.5 GB file and re-sorts it.

**Finding: the old path is not deterministic.** `created_time` has exact ties: 28 of 40 prints in one sampled ticker share a timestamp. `order by ticker, created_time` therefore returns tied prints in a different order on each run. On one 1/48 chunk, two identical runs of `mkgap_causal.run_chunk` differed by up to 51 fills and $266 of gross P&L per model (`docs/engopt/equivalence.json`, `old_nondeterminism`). This is small relative to the totals, but published mkgap numbers are not exactly reproducible. The store fixes it by keeping the tape's stored order, which is equivalent to adding `file_row_number` as a final tie-break.

## 3. What was built (`src/data/`)

| Module | What it does |
|---|---|
| `tape_store.py` | **`TapeStore`.** A one-time build turns a ticker-sorted Parquet tape into one memory-mapped `.npy` file per print column, plus a per-ticker table (`row_start`, `row_end`, constants such as event, series, close, settle, win and fees) and `meta.json`. The store is rebuilt automatically when the source's size or mtime changes. The build verifies the sort order and that the ticker constants really are constant. `presort=True` first sorts an unsorted source out of core with DuckDB (2 threads, 2.5 GB). Ticker filters run on the small ticker table (`codes_where`, `codes_for`), which works as a pre-built index: no scan and no sort. `chunks()` returns chunks aligned to ticker boundaries, as zero-copy views when the tickers are contiguous. `duckdb()` exposes the mmapped columns as a DuckDB view for ad-hoc SQL. |
| `tapes.py` | Registry with `open_tape("kalshi_base")` and `open_tape("ment5")`. The data root is `$PREDICT_NGIN_DATA`, otherwise `<repo>/data`. The cache is `$PREDICT_NGIN_TAPE_CACHE`, otherwise `<repo>/data/cache/tapes`. |
| `fills.py` | **`causal_fills`.** Fa, Fb and Fc are computed in one fused numba pass, promoted from `mkgap_causal._engine` with unchanged semantics. Output buffers are sized tightly from the number of (ticker, second) groups rather than 2·n·models, which matters because those buffers are committed memory. |
| `runner.py` | **`run_strategies(store, [strategies], chunk_specs, workers=..)`.** Runs N strategies in one pass, with `ctx.memo(key, fn)` for work they share (row filters, fills). Pool workers open the store by path, so the tape is never pickled or copied: one page-cache copy serves every process. `ram_sized_workers(per_worker_gb, budget_gb, max_workers)` sizes the pool from available RAM, not CPU count, and respects `$ENGOPT_MAX_WORKERS`. |
| `memutil.py` | Peak working set, peak private commit and system RAM, without psutil (psutil is not in the venv). |

**Built stores:**

| Store | Source | Build time | Size on disk |
|---|---|---:|---:|
| `kalshi_base` | 165 M rows, sorted in place | 34 s | 3.2 GB |
| `ment5` | 14 M rows, presorted | 10 s | 0.64 GB |

`kalshi_base` stores 19 bytes per print. Per-ticker constants are stored once per ticker instead of once per print.

## 4. After

`scripts/research/mkgap_store.py` is the migrated mkgap Part B, run as four strategies in one pass. Benchmarks come from `scripts/bench/engopt_profile_new.py`, and the JSON files are in `docs/engopt/new_full*.json`. Each configuration ran in its own process.

| Configuration (48 chunks, all 165 M prints)                    | Wall time | Speed-up | Peak private (GB)                     |
|----------------------------------------------------------------|----------:|---------:|---------------------------------------|
| Old `mkgap_causal` path                                        | 424 s     | 1x       | 3.9 (3 chunks, results dropped)       |
| Store, serial, results in memory                               | 36 s      | 11.8x    | 5.9 (23 M result rows held)           |
| Store, serial, `spill_dir` (each chunk's cells written to Parquet) | 43 s  | 9.9x     | **1.9**                               |
| Store, 2 workers, results in memory                            | 23 s      | 18x      | not measured (RSS 5.8 across the workers) |
| Store, 3 workers, spill                                        | **17.2 s** | **25x** | 5.4 across the workers + 0.6 main     |

- **Per chunk (same tickers, `equivalence.json`):** the old path took 6.9 s to load plus 2.0 s of post-processing, against 1.35 s in total for the new one.
- **Where the new path's time goes:** shared prep 6.2 s and fused fills 3.9 s across all 48 chunks; the rest is pandas aggregation per strategy.
- **Working set versus private memory:** the working set (RSS) also counts the mmapped tape pages, which are shared and reclaimable. For example, 3 workers show 6.5 GB of RSS in total, but they share one copy of the 3.2 GB tape.
- **Pool sizing:** budget about 2 GB of private memory per worker for 1/48 chunks of `kalshi_base`, i.e. `per_worker_gb=2.0`.

**Fill-engine micro-benchmark** (1.0 M real prints, 3000 tickers, `engine_bench.json`):

| Engine | Time |
|---|---:|
| `causal_opps` (pure Python) | 3.9 s (extrapolated) |
| `mkgap_causal._engine` (3 calls) | 0.049 s |
| Fused engine | 0.033 s |

Older `audit_flb_*` scripts that still loop over `causal_opps` gain about 100x by switching engines. The mkgap and mrev scripts gain little, because the engine is already about 2% of their time.

## 5. Equivalence

- **Unit tests** (`tests/unit/test_fills_engine.py`, `test_tape_store.py`, `test_runner.py`; 26 tests):
  - The fused engine matches `audit_flb_common.causal_opps` fill for fill on random tapes, and matches `mkgap_causal._engine` array for array, in the same order.
  - Store build round-trips the data and chunks never split a ticker.
  - Building from an unsorted source fails unless `presort=True`, and a ticker column that varies within a ticker is rejected.
  - A changed source triggers a rebuild.
  - A process pool gives the same results as a serial run.
- **Real data** (`scripts/bench/engopt_equivalence.py`, three 1/48 chunks, about 10 M prints):
  - **Identical:** raw fills (ticker, side, ts, q, fq, through, atl), every cell aggregate including exact float sums, family volume, and `audit_flb_common.simulate` portfolio P&L on test-split fills.
  - **Condition:** the old SQL needs the deterministic tie-break described in section 2. Without it, the old path cannot even match itself.
- **Full suite** (`pytest tests --continue-on-collection-errors`): 718 passed, 3 skipped. The 25 collection errors are pre-existing; none are new.

## 6. Migration guide for research scripts

1. **Loading.** Replace the hash-chunk load:

   ```python
   # before
   df = con.execute(f"select ... from '{BASE}' where hash(ticker) % {n} = {k} order by ticker, created_time").df()
   # after
   from src.data.tapes import open_tape
   st = open_tape("kalshi_base")                    # builds once (~30 s), then mmaps (<0.1 s)
   for ch in st.chunks(n_chunks=48):                # or st.chunks(tickers=st.codes_where(lambda t: t.series.isin(S)))
       ts, yc, tyes, qty = ch["ts"], ch["yc"], ch["tyes"], ch["qty"]   # numpy, zero-copy
       close = ch.tattr("close_ts"); tk = ch.ticker_code               # per-ticker attrs broadcast by code
       ch = ch.filter((ts < close) & (yc >= 1) & (yc <= 99))          # the old WHERE clause
   ```

2. **Strings.** Per-print strings (event, series, family) become integer codes into `st.tickers`. Group on codes and map names back only on the aggregate. If the codes are made sorted with `pd.factorize(sort=True)`, a sorted integer groupby reproduces the old string groupby exactly; see `mkgap_store.CellsStrategy`.

3. **Fills.** Use `from src.data.fills import causal_fills` (inputs are integer cents) in place of `causal_opps` or `_engine`. One call computes all three models; select one with `.by_model("Fa")`.

4. **Several strategies.** Write each as a class with `name`, `on_chunk(ch, ctx)` and `finalize(parts)`. Put shared work in `ctx.memo(...)`. Then call `run_strategies(st, strategies, st.chunk_specs(n_chunks=48), workers=ram_sized_workers(2.0, budget_gb=6, max_workers=3))`. Classes must be importable (module level) for the pool.

5. **Large outputs.** Spill each chunk to Parquet (`ctx.index` gives a stable part number) rather than accumulating every chunk in memory.

6. **New tapes.** Add an entry to `src/data/tapes.TAPES`. Only fixed-width, null-free columns can be print columns; constants go in `ticker_cols`. Set `presort=True` if the file is not sorted by ticker.

7. **Worktrees.** Set `PREDICT_NGIN_DATA` to the main checkout's `data/`. The cache is written under the worktree's own `data/cache`.

Example: `mrev_fills.run` could keep `event_closes` and `phases` as they are and replace `load_chunk` and `_engine` with `open_tape("ment5")` and `causal_fills`. That port is not done.

## 7. What I did not do

- **No research scripts were migrated** apart from the new `mkgap_store.py`. The originals are untouched, including `mkgap_causal`, `mrev_*` and `audit_flb_*`. The doc says nothing about whether old results change: only the order of tied prints differs, and it is reproducible now.
- **No stores for `data/kalshi/trades_fixed`** (57 monthly files, 5.4 GB) **or `live_books`**, and no partitioning by series or date. The `TapeStore` build works for any ticker-sorted Parquet file. `trades_fixed` would need a `presort` multi-file build: DuckDB accepts a glob, but `build_from_parquet` currently takes one file.
- **No parallelism inside the numba engine** (`prange`). It is 2% of the time and was not worth it.
- **Windows file locking:** a store cannot be rebuilt while another process has it mmapped.
- **Baseline memory caveat:** the baseline peak memory figure discards results, which is optimistic for the old path. A full old-path run that keeps its 48 chunk aggregates was not re-measured.
- **psutil** was not added to the venv. `memutil` uses ctypes on Windows and `/proc` or `resource` on POSIX.
