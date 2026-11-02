# Data lake

Last revised: 2026-10-17

How market data is stored, queried and kept up to date. Code: `predict_ngin/tapes` (readers),
`predict_ngin/lake` (catalog, builders, contracts, command line). Commands assume the repo root and `uv`.

## Querying

```python
from predict_ngin import lake

con = lake.connect()          # DuckDB with standard limits + one view per dataset
con.sql("""
    SELECT category, round(sum(usd_amount)) AS usd
    FROM polymarket_maker_fills
    WHERE ts_chain >= 1767225600            -- 2026-01-01
    GROUP BY 1 ORDER BY 2 DESC
""").show()
lake.views(con)                # which views exist on this machine
```

| View | What it is |
|---|---|
| `kalshi_trades` | every Kalshi trade since 2021. `count` is the integer count, `count_fp` the exact (fractional) count |
| `kalshi_markets` | Kalshi markets seen since 2026-01-28, with status, result and settlement |
| `polymarket_fills` | every Polymarket on-chain fill since Nov 2022, YES-normalised, including taker-order rows |
| `polymarket_maker_fills` | the same without taker-order aggregate rows. **Use it for counts and volume** |
| `polymarket_markets` | one row per Polymarket market: event, tags, `category`, `is_updown` |
| `polymarket_bars_1h`, `polymarket_bars_1m` | per-market bars of the YES price from maker fills: open/high/low/close, VWAP, fills, USD, buy/sell USD. Start here for price paths; they are 50–100× smaller than the fills |
| `kalshi_books`, `polymarket_books` | order-book snapshots from the recorder (atlas) |
| `kalshi_live_trades`, `polymarket_live_trades` | trades seen live by the recorder |

You can also build SQL directly: `predict_ngin.tapes.kalshi.tape_sql(columns, start_ts, end_ts)` and
`predict_ngin.tapes.polymarket.clean_trades_sql(columns, start_ts, end_ts, maker_rows_only=True)` return one
SELECT over the whole tape. Pass `start_ts` / `end_ts` (UTC seconds) whenever you can: only the months in range are
opened.

Open DuckDB through `lake.connect()` or `predict_ngin.shared.duck.connect()`, not `duckdb.connect()`. They apply
the memory, thread and temp-disk limits below. One study with no temp cap once filled 48 GB of disk.

| Variable | Default | |
|---|---|---|
| `PREDICT_NGIN_DATA` | `<repo>/data` | data folder |
| `PREDICT_NGIN_DUCK_MEMORY` | `4GB` | DuckDB memory limit |
| `PREDICT_NGIN_DUCK_THREADS` | min(4, CPUs) | DuckDB threads |
| `PREDICT_NGIN_DUCK_TEMP_MAX` | `10GiB` | cap on DuckDB's spilled temp data |
| `PREDICT_NGIN_COLD` | unset | optional cold drive (below) |

## Exploring the data

```bash
uv sync --extra viz                 # once: streamlit + plotly
uv run predict-ngin viz             # opens http://localhost:8501 (--port N, --headless)
```

A local Streamlit app (`predict_ngin/viz`) over the same views, for looking rather than studying:

| Page | Shows |
|---|---|
| Overview | which views exist on this machine, the lake manifest, `data status`; monthly volume per venue and category |
| Market explorer | search markets by question / slug / ticker / title; price chart (lake bars, or bars built from the trades of just the months in range) with volume, and the latest trades |
| Calibration | price band vs share resolved YES for resolved markets, one price per market a set number of hours before its last trade, with 95% intervals; category / series filters, crypto up/down excluded by default |
| Order book replay | recorder snapshots of one market: depth chart at a chosen second, best bid / ask / mid over the window, recorder trades overlaid |

Every query is bounded by time and market and reads only the month folders (or recorder hour files) in range;
results are cached until *Reload data*. A page whose dataset is missing says which command creates it, so the app
works on a fresh clone with any subset of the data. SQL builders live in `predict_ngin/viz/queries.py` (no
Streamlit) and can be reused in notebooks.

## Keeping it up to date

```bash
uv run predict-ngin data status                  # one line per dataset: layout, coverage, what is running
uv run predict-ngin data update polymarket       # fetch new fills (polymarket_extend tail), rebuild changed months
uv run predict-ngin data update kalshi           # resume the Kalshi tape refresh (long-running, ~3 req/s)
uv run predict-ngin data build polymarket        # rebuild lake months from files on disk (incremental)
uv run predict-ngin data verify                  # run every data contract over the built datasets
uv run predict-ngin data catalog                 # write data/lake/catalog.duckdb for the DuckDB CLI / DBeaver
```

Each step takes a lock, so two runs cannot overlap. Builders skip months already built, except the newest one, which
is still growing. `--rebuild` forces a rebuild.

The Polymarket update is split between atlas and the PC:

- **atlas fetches daily.** The `pn-pmtail` timer (`config/collectors.yaml`) runs at 00:30 and 06:30 UTC. It runs
  `polymarket_extend.py tail --raw-only`, which writes each finished UTC day's OrderFilled logs to
  `trades_ext/tail/raw/orderfilled_<day>.parquet` (~200 MB, ~5 min).
- **atlas_sync copies the files.** It brings those files to the PC and deletes atlas copies older than 21 days once
  the PC has them.
- **The PC converts and rebuilds weekly.** The scheduled task `predict-ngin-polymarket-lake` runs Sunday 03:00, or
  at the next wake if the PC was asleep. It runs `scripts\data\polymarket_lake_update.ps1`, which runs
  `data update polymarket` and logs to `data\logs\polymarket_lake_update.log`. Each synced day converts in ~1 min
  without touching the RPC. Any day atlas missed is fetched on the PC.

Use `scripts\data\install_polymarket_lake_update.ps1` to install the task, adding `-Remove` to remove it. Create
`data\polymarket_clean\STOP` to pause both sides.

## Layout

```
data/lake/
  _manifest.json                         per dataset: rows, months, versions, last verify result
  catalog.duckdb                         views for external tools (rewritten by `data catalog`)
  polymarket/
    markets.parquet                      one row per market (4.1M), sorted by category, event, market
    fills/year=YYYY/month=MM/part-NN.parquet
    bars_1h/year=YYYY/month=MM/…         hourly per-market bars, rebuilt with each fill month
    bars_1m/year=YYYY/month=MM/…         1-minute per-market bars
    fills_state.json                     per-month build record; "_complete" once every month is built
  kalshi/                                after the 2026 tape refresh is verified
    trades/year=YYYY/month=MM/part-NN.parquet
```

Readers serve the lake only once a dataset's state file says it is complete, and otherwise read the originals. Code
works the same either way. The Kalshi originals (`data/kalshi/trades_fixed*`) stay in place. Most Polymarket
originals were retired after a row-for-row check (see Removed datasets), so for months before 2026-10 the Polymarket
lake is the only copy. The builder never rebuilds those months (`_sources_retired` in `fills_state.json`).

### Why this layout

The goal is fast backtests and queries. DuckDB reads only the folders, files and row groups (about 1M rows each) it
cannot rule out, using folder names and each row group's min/max statistics.

* **Folders by venue, then data type.** Different schemas and sizes; a study reads only what it needs.
* **Then by month** (`year=` / `month=`). Every backtest has a date window, so months outside it are never opened.
  New data only appends or rewrites the current month. A re-fetch rewrites one month, not the whole tape.
* **Category, event and market are sorted columns, not folders.** Rows inside a month are sorted by
  category → event → market → time. Each category, event and market then sits in a few row groups, and filters on
  them skip the rest almost as well as category folders would, without their costs:
  * category labels change (Polymarket and Kalshi re-file series), which would mean rewriting folders;
  * sports alone is about 60% of the volume, so folders would be badly unbalanced;
  * category × month would create hundreds of tiny folders in the early years.
* **No per-market or per-series folders.** Millions of tiny files is the classic slow case.
* **Files of 256 MB–2 GB** (at most 40M rows; Polymarket months built from 2026-01 on use 20M so the sort fits the temp cap), **zstd compression, 1M-row row groups.** These replace tens of
  thousands of small files.
* **Parquet for storage, DuckDB for querying.** A `.duckdb` database file allows only one writer at a time, and
  fetchers, the atlas sync and studies all touch the data. duckdb also has no wheel for the Python 3.15 used on atlas.
  DuckDB reads Parquet at close to its native speed, so copying the data in would gain nothing.
* **Not a row store** such as SQLite or an LSM tree. Studies scan whole columns, which column stores do far better.

### Categories (Polymarket)

`predict_ngin.markets.polymarket_category.category_of` maps each event's Gamma tags to one of Crypto, Sports,
Politics, Geopolitics, Economy, Finance, Tech, Science, Culture, Weather, Mentions or Other. It takes the first
match from the most specific section to the least. Tags come from `scripts/data/fetch_pm_event_tags.py` (all 1.15M
events, about 2–3 hours, public API). The rules are versioned (`VERSION`). The fill files are sorted by category, so
after a rule change, rebuild with `data build polymarket --markets`.

`is_updown` flags crypto "Up or Down" interval markets (5m/15m/1h/4h). They are most of the fill count since 2025,
and most studies want to exclude them or look at them separately.

### Bars

Most backtests need a market's price path, not every fill. The bars keep one row per market per hour (or
minute) that had trades: open/high/low/close of the YES price by fill time, token-weighted VWAP, fill count,
USD and token volume, and USD bought and sold by takers of the YES leg. They are built from maker fills only,
and each month's bars must add up to its fills (count exactly, USD to 1e-9) before they are published. Bars
are not forward-filled: a market with no trades in an hour has no row.

### What stays outside the lake

Per-study caches under `data/vendor/<prefix>/` stay as their studies wrote them. Many are read by exact file
name (`pm_quotes/<date>/<slug>.parquet`, per-ticker book files listed in a manifest, `trades/<event>.json.gz`),
so merging or converting them would break those studies for little gain: they total a few GB and each is
read by one study. New studies should read source data through `predict_ngin.lake` and write their own
outputs as Parquet.

## Data contracts

`predict_ngin/lake/contracts.py` declares each dataset's required columns and invariants: prices in [0, 1], no
missing ids, unique Kalshi trade ids, yes + no within a cent of 1, known categories, and so on. Builders run the
contract on every new month before swapping it in, together with a reconciliation against the source (row count,
sum of `usd_amount` and of `yes_price`) and a check that no row falls outside its month. A failure leaves the new
month in a `.building` folder and keeps the old one. `data verify` runs the contracts over whole datasets and
records the result in `_manifest.json`.

## Cold storage (optional)

Rarely used data can move to a slower drive: set `PREDICT_NGIN_COLD`, or `cold_root` in the gitignored
`config/storage.local.yaml` (template: `config/storage.example.yaml`). `uv run predict-ngin data tier demote
<folder>` moves the folder there and leaves a link at its old path, so code reads it unchanged, only more slowly.
`promote` brings it back before a long study. With no cold drive configured, nothing moves.

## Removed datasets (2026-10-17)

These were deleted as exact or derivable duplicates.

Still kept as Polymarket originals:
- `trades/part-0058`, which holds the partial 2025-12-30 day with `order_hash`.
- 27 `hf` parts that mix in the 2025-12-23..29 overlap, whose ext-only columns are not in the lake.
- The `hf` parts reaching October 2026; the newest month is rebuilt from source.
- `trades_ext/tail/`, which `data update` still appends to.

| Removed | Use instead |
|---|---|
| `data/polymarket/trades/` (monthly 12-column extract) | still readable by old code: `TradeStore` and the arbitrage backtest serve the same columns from `data/polymarket/trades.parquet` (`src/data_quality/legacy_pm_trades.py`). New code: `polymarket_fills` |
| `data/by_category/*/trades_*` (the same extract split by category) | `polymarket_fills` joined to `polymarket_markets` (category) |
| `data/polymarket/quant.parquet` (subset of trades.parquet) | `data/polymarket/trades.parquet` or `polymarket_fills` |
| `data/polymarket/users.parquet` (fills split per wallet) | `polymarket_fills` (maker / taker columns) |
| `data/kalshi/trades/` (raw HF tape with broken timestamps) | `kalshi_trades` |
| 1,116 Polymarket source fill files, 59.2 GB: `data/polymarket_clean/trades/part-0000..0057` and the `trades_ext/hf/` parts entirely in 2025-12-30..2026-09-30 | `polymarket_fills` (the lake). Checked before deletion: for every month 2022-11..2026-10, the row count and the sum of an all-column row hash of the source view equal the lake's (1.415B rows, 48/48 months). List: `data/lake/polymarket/retired_sources.json` |

The compatibility view matches the old extract's columns and conventions: raw per-token prices, which `TradeStore`
YES-normalises as before, and `timestamp` as a UTC string. It matches the extract's rows to within about 0.2%: the
extract also dropped exact duplicates and ended 2025-10-07. `pmfix_measure.py` skips the three deleted sources
with a note.

## Kalshi tape status (2026-10)

The 2026 part of the Kalshi tape is being re-fetched with exact fractional contract counts (`count_fp`). Until that
finishes and verifies, `kalshi_trades` has gaps after 2026-03-10. Check them with `predict-ngin data status`. The
Kalshi lake is built after the verified re-fetch.
