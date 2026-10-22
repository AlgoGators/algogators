# Kalshi tape refresh (2026-10-16)

Last revised: 2026-10-16

Status when written (2026-10-15 ~04:50 UTC): the fetcher is running. It has passed schema parity and is
extending the tape day by day. This is research only: public GET requests, no keys, no orders.

## TL;DR

- **Canonical tape:** `data/kalshi/trades_fixed/trades_YYYY-MM.parquet` (57 monthly files, 5.4 GB).
  It is the HF dataset `TrevorJS/kalshi-trades` (snapshot 2026-01-30), with timestamps fixed by
  `scripts/data/repair_kalshi_trades.py`.
- **The old tape is complete through 2026-01-28, not 2026-02-11.** From 2026-01-29 it holds only the
  ~1.7k tickers in the HF snapshot, against 66k–166k tickers/day before. Its last 14 days are a
  survivorship-biased subset, so the extension starts at **CUTOVER_TS = 1769558400 (2026-01-28 00:00 UTC)**.
- **New tape:** `data/kalshi/trades_fixed_ext/trades_YYYY-MM-DD.parquet`, one file per UTC day. It has the
  same 10 columns, dtypes, price convention and side convention as the old tape, compressed with zstd.
- **Parity:** I re-fetched 2026-01-21..28 (8 days) and diffed it against the old tape trade by trade.
  All 15,427,631 old trades matched on trade_id. There were **0** differences in ticker, event_ticker,
  yes_price, no_price, count, taker_side, timestamp_unix and created_time instant. The API also returned
  171 trades (0.001%) that the old tape lacks.
- **Loaders:** use `src/trading/arbitrage/kalshi_tape.py::tape_sql()` instead of
  `read_parquet('data/kalshi/trades_fixed/*.parquet')`. It takes old rows with ts < cutover and new rows
  with ts >= cutover, so there are no duplicates. With no ext files it returns exactly the old tape.
- **Runtime:** about 11 minutes per day of data at 3 req/s (~2,000 requests and ~2M trades per day in
  Jan 2026). About 258 days remain to 2026-10-15, so a full catch-up takes **at least ~2 days of
  wall-clock**, more if 2026 volume grew. The run resumes from checkpoints.

## 1. Canonical tape and loaders

| Item | Path | Notes |
|---|---|---|
| Raw HF download | `data/kalshi/trades/trades_YYYY-MM.parquet` | `scripts/data/download_kalshi_hf.py`; ~49% of rows have a wrong `timestamp_unix` (UTC offset dropped) |
| **Canonical** | `data/kalshi/trades_fixed/trades_YYYY-MM.parquet` | repaired copy; 2021-06 .. 2026-02-11 |
| Extension (new) | `data/kalshi/trades_fixed_ext/trades_YYYY-MM-DD.parquet` | this refresh; 2026-01-28 .. |
| Market metadata/settlements (new) | `data/kalshi/trades_fixed_ext/markets/part_*.parquet` | every non-MVE ticker seen in the extension |

Schema (identical in both, same order):
`trade_id str, ticker str, event_ticker str, yes_price f64, no_price f64, count i64, taker_side str,
created_time str, timestamp_unix i64, timestamp_unix_orig i64`.

Conventions, all checked trade for trade:
- `yes_price` is the YES price in dollars. `taker_side='yes'` means the taker bought YES at that price.
  The API's `taker_side` equals `taker_outcome_side` on every row fetched so far (0 mismatches).
- **Sub-cent prices are truncated down to the cent on both legs.** This is how the HF source stored
  integer cents. On the 5,745 sub-cent trades in one test hour, old = floor(new, 1c) every time, so
  yes+no = 0.99 on those rows. They are ~4–10% of trades, 99.9% of them KXMVE parlay combos. The fetcher
  applies the same truncation, so exact sub-penny prices are lost on both tapes. The daily
  `subcent_price` counts are in `progress.json`.
- `count`: the API returns `count_fp` and the fetcher rounds it to int64. Fractional counts seen so far: 0.
- `created_time`: the old tape mixes string formats (`2026-01-27 17:24:15.261981-05` and ISO `...Z` with
  3–6 decimals). The new tape always writes `%Y-%m-%dT%H:%M:%S.%fZ`. The instants agree on 100% of matched
  trades. Use `timestamp_unix`, which matches exactly.
- `timestamp_unix_orig` equals `timestamp_unix` in the new tape. Nothing needed repair.
- `event_ticker` for non-MVE tickers comes from market metadata (`/historical/markets`, then `/markets`).
  For `KXMVE*` combos it is the ticker minus its last `-` segment, which holds for 100% of MVE rows in the
  old tape and saves ~40k lookups/day. 0 non-MVE tickers have needed the fallback so far.

Loaders that read `trades_fixed` directly (none are in `src/`; all are research scripts with their own
glob) include `kalshi_flb_build.py`, `micro_build_base.py` (→ `data/vendor/micro/base.parquet`, used by
mkgap), `settle_*`, `sharp_extract_tape*.py`, `fin_*_extract_tape.py`, `release_tape_extract.py`,
`parlay5_data.py`, `alpha_decay_*` and `measles_*`. None of them pick up the new data until they switch to
`tape_sql()`. That is a one-line change:

```python
from src.trading.arbitrage.kalshi_tape import tape_sql, tape_end_ts
con.execute(f"CREATE VIEW t AS {tape_sql()}")          # was: read_parquet('data/kalshi/trades_fixed/*.parquet')
end = tape_end_ts()                                     # exclusive end of contiguous coverage
```

`tape_sql(columns=None, start_ts=None, end_ts=None)` pushes the time filter into each branch, so a slice
reads only the files it needs. `tape_end_ts()` gives the end of contiguous coverage from the last finished
day file. Studies should cap their test window there, because the extension grows while the fetcher runs.

One behaviour change: while the extension is shorter than 2026-02-11, `tape_sql()` drops the old tape's
biased 2026-01-29..02-11 subset beyond `tape_end_ts()`. This is deliberate, since that subset is a
survivor sample. Old-only behaviour is still available by reading `trades_fixed` directly.

Derived tapes such as `data/vendor/fin_D/*`, `audit_flb`, `micro/base.parquet` and `ment5/tape.parquet` are
not rebuilt here. Each needs its build script re-run against `tape_sql()`.

Tests: `tests/unit/test_kalshi_tape.py` (5 tests: exact old behaviour with no ext, no duplicates across
the cutover, tmp files ignored, range/column filters, `tape_end_ts`, API→schema conversion incl. cent
truncation). `venv/Scripts/python.exe -m pytest -q tests/unit/test_kalshi_tape.py tests/unit/test_kalshi_data.py`
→ 23 passed.

## 2. Fetcher: `scripts/research/tape_refresh.py`

- Endpoints: `GET /historical/cutoff`. Trades created before `trades_created_ts` (currently
  2026-08-15) come from `GET /historical/trades`, later ones from `GET /markets/trades`. Both are queried
  market-wide with `min_ts`/`max_ts`/`cursor` and `limit=1000` (the maximum). The historical endpoint is
  required: `/markets/trades` returns nothing for Jan 2026. A window that straddles the cutoff is split.
- Window semantics were checked: `min_ts` is inclusive and `max_ts` exclusive. Hourly windows
  `[h, h+3600)` therefore neither overlap nor leave gaps, and every row is asserted to fall inside its window.
- Checkpointing: each hour is written atomically to `_staging/YYYY-MM-DD/HH.parquet` (tmp+rename). When all
  24 hours exist, the fetcher looks up metadata for new tickers, merges the hours with DuckDB (1.5 GB memory
  limit), sorts by `timestamp_unix`, asserts trade_ids are unique, writes the day file and removes the
  staging directory. A killed run loses at most the hours in flight.
- Rate: one process, enforced with a lock file on `.lock`. A shared limiter caps it at `--rps 3` (hard cap
  4) across 2 worker threads. On 429 it honours Retry-After or backs off exponentially for all threads.
  Network errors and 5xx get exponential retry, up to 5 minutes. About 40 429s in the first 1.5 h, all
  absorbed by backoff (other agents share the IP). RAM is ~250–600 MB.
- Metadata: `/historical/markets?tickers=` (200/request), then `/markets?tickers=` for the rest.
  It stores ticker, event_ticker, market_type, title, yes_sub_title, status, result, open/close/expiration
  time, settlement_ts, settlement_value_dollars, expiration_value, volume_fp, price_level_structure, source
  and fetched_at. `settle` re-queries markets that were still active when first seen; run it after
  catch-up. About 8% are active at first fetch (mostly long-dated).
  Tickers that neither endpoint returns go to `markets/not_found.txt` (0 so far).

Commands (from the repo root):

```
venv/Scripts/python.exe scripts/research/tape_refresh.py run --skip-verify   # resume (verify week is done)
venv/Scripts/python.exe scripts/research/tape_refresh.py status
venv/Scripts/python.exe scripts/research/tape_refresh.py diff --diff-dir data/kalshi/trades_fixed_ext  # old vs new overlap
venv/Scripts/python.exe scripts/research/tape_refresh.py settle            # refresh unsettled markets
```

The overnight run was started detached as `venv/Scripts/python.exe -u scripts/research/tape_refresh.py run`.
Its logs are `data/kalshi/trades_fixed_ext/fetch.log` and `data/kalshi/tape_refresh.std{out,err}.log`.
Per-day stats (rows, tickers, requests, seconds, sub-cent and block counts) are in
`data/kalshi/trades_fixed_ext/progress.json`. If the process has died, re-run `run --skip-verify`.
`run` without the flag also works, because finished verify days are skipped. The fetcher stops at the
last complete UTC day; re-running it later catches up to the new "now".

## 3. Parity checks (verify week + overlap)

`verify/diff.json` and the `--diff-dir` run. Every old trade in the window matched; `d_*` = matched trades
whose field differs.

| UTC day | old trades | new trades | matched | only old | only new | d_ticker/event/yes/no/count/side/ts/ctime-instant |
|---|---:|---:|---:|---:|---:|---|
| 01-21 | 1,579,562 | 1,579,613 | 1,579,562 | 0 | 51 | all 0 |
| 01-22 | 1,519,544 | 1,519,551 | 1,519,544 | 0 | 7 | all 0 |
| 01-23 | 1,582,336 | 1,582,354 | 1,582,336 | 0 | 18 | all 0 |
| 01-24 | 2,254,550 | 2,254,566 | 2,254,550 | 0 | 16 | all 0 |
| 01-25 | 2,952,682 | 2,952,723 | 2,952,682 | 0 | 41 | all 0 |
| 01-26 | 1,788,933 | 1,788,945 | 1,788,933 | 0 | 12 | all 0 |
| 01-27 | 1,641,401 | 1,641,417 | 1,641,401 | 0 | 16 | all 0 |
| 01-28 | 2,108,623 | 2,108,633 | 2,108,623 | 0 | 10 | all 0 |
| 01-29 | 1,019,208 | 1,874,775 | 1,019,208 | 0 | **855,567** | all 0 |
| 01-30 | 905,950 | 1,866,683 | 905,950 | 0 | **960,733** | all 0 |

The few "only new" trades before 01-29 are scattered across tickers (NFL SGP packs, MVE combos, a few
weather/crypto). They look like trades missing from the HF snapshot, not convention differences. From
01-29 the old tape is a strict subset: every old trade is present and identical, and the extension adds
the missing ~half of the day. 01-21..27 sit in `verify/` and are not read by loaders.

## 4. Coverage (trades/day)

Before (old tape, monthly average per UTC day):

| month | trades | avg/day | distinct tickers |
|---|---:|---:|---:|
| 2025-09 | 9.8M | 325k | 34k |
| 2025-10 | 16.2M | 523k | 153k |
| 2025-11 | 21.6M | 721k | 368k |
| 2025-12 | 29.8M | 963k | 1.05M |
| 2026-01 | 51.2M | 1.65M | 1.72M |
| 2026-02 (1–11, partial subset) | 12.9M | 1.17M | 5.1k |

After (extension, finished days when written; see `progress.json` / `status` for the latest):

| UTC day | trades | tickers | requests | fetch time |
|---|---:|---:|---:|---:|
| 2026-01-28 | 2,108,633 | 78,288 | 2,155 | 12.1 min |
| 2026-01-29 | 1,874,775 | 73,366 | 1,921 | 10.8 min |
| 2026-01-30 | 1,866,683 | 71,202 | 1,913 | 10.7 min |

Projection at ~11 min per data-day (~130 data-days per wall-clock day at January volumes): about
2026-02-20..25 by 09:00 UTC (05:00 ET) on 10-15, and today's date in ~2 days if volume stays flat. Check
progress with `status`.

## 5. Gaps and caveats

- **Old tape 2026-01-29..02-11 is a survivor subset.** Use the extension from the cutover (handled by `tape_sql`).
- **Sub-penny precision is truncated in both tapes**, mainly in MVE combos. If a study needs exact
  sub-cent prices, the fetcher would need extra columns; I did not add them, to keep strict schema parity.
- **The API omits nothing the HF tape had** (only_old = 0 on 10 days), but the HF tape omits ~1e-5 of
  trades. Pre-cutover studies are unaffected in practice.
- `is_block_trade` is not stored (0 block trades so far; the daily count is in `progress.json`).
  `taker_book_side` is not stored either: it is redundant with taker_side, and the 0 mismatches are logged.
- The historical/live boundary (2026-08-15) moves forward over time. The fetcher re-reads it each day and
  splits windows at it, so no window is double-fetched or missed.
- The current UTC day is never written. Its finished hours stay in `_staging` until the day is over.
- Market `status`/`result` is as of fetch time. Run `settle` after catch-up for markets that settled later.

## 6. Disk

- Old tape: `trades_fixed` 5.4 GB (raw `trades` 5.5 GB). Neither was touched.
- Extension: ~57–67 MB per day (~30 bytes/trade, zstd). `verify/` (7 days, deletable) is 409 MB.
  Total `trades_fixed_ext` was 591 MB when written.
- Projection: ~260 days × 2M+ trades → **~16–25 GB** for a full catch-up. The C: drive had 123 GB free.
