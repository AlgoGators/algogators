# Quick start

Last revised: 2026-10-18

From a fresh clone to Kalshi and Polymarket trade data you can query. Every step uses public data and
needs no API keys or paid accounts. [README.md](README.md) has the reference for each command.

## What you need

- [uv](https://docs.astral.sh/uv/) (it installs Python 3.11+ for you if needed) and git.
- Disk: about 65 GB for Polymarket and 60 GB for Kalshi. Put the data on a fast drive if you can.
- Time: the Hugging Face downloads take hours. The Kalshi API catch-up (every trade since
  2026-01-28) takes days at the public rate limit. Every step resumes where it stopped, so you can
  interrupt it and run the same command again.

## 1. Install

```bash
git clone https://github.com/AlgoGators/algogators && cd algogators/services/predict-ngin
uv sync --extra research --extra dev   # core + research libraries + pytest (name every extra in one sync)
cp .env.example .env              # optional: only needed for keys you add later
uv run pytest -m "not data" -q    # sanity check: should pass with no data present
```

To keep data outside the checkout, set `PREDICT_NGIN_DATA` (for example `D:\predict-data`). Many
research scripts still read `data/` inside the checkout, so also make `data` a link to that folder
(on Windows: `mklink /J data D:\predict-data` in cmd).

## 2. Kalshi

```bash
uv run python scripts/data/download_kalshi_hf.py                  # Hugging Face tape to 2026-01-28 (~6 GB)
uv run python scripts/data/repair_kalshi_trades.py                # fixes its timestamps -> kalshi/trades_fixed/
uv run python scripts/data/refresh_kalshi_markets.py              # markets and settlements
uv run python scripts/research/tape_refresh.py run --skip-verify  # every trade since, from the public API (days)
uv run python scripts/research/tape_refresh.py status             # progress
```

## 3. Polymarket

One command downloads the ready-built data from Hugging Face
([domdd/predict_ngin](https://huggingface.co/datasets/domdd/predict_ngin), about 64 GB), checks every
month against its recorded row count, then fetches the days since the snapshot from public Polygon RPC
and updates the lake:

```bash
uv run predict-ngin data pull polymarket
```

Interrupted? Run it again: finished files are skipped. Later, `uv run predict-ngin data update polymarket`
adds the newest days; `pull` again only downloads when a newer snapshot has been published.

**Building it yourself instead** (no snapshot; same result, but you process the 46 GB release):

```bash
uv run python scripts/data/download_hf_polymarket.py              # markets.parquet (0.5 GB)
uv run python scripts/data/polymarket_extend.py hf --full         # every fill to 2026-10-04
uv run python scripts/data/polymarket_extend.py tail              # the days since, from public Polygon RPC
uv run predict-ngin data build polymarket                         # the data lake
```

## 4. Use it

```python
import duckdb
from predict_ngin.tapes.polymarket import clean_trades_sql
from predict_ngin.tapes.kalshi import tape_sql

con = duckdb.connect()
con.sql(f"SELECT count(*) FROM ({clean_trades_sql(maker_rows_only=True)})").show()
con.sql(f"SELECT count(*) FROM ({tape_sql()})").show()
```

Both queries read the data lake when it is built (sorted month files with `category` and `is_updown`
columns). See what is on disk and how fresh it is with:

```bash
uv run predict-ngin data status
```

## 5. Live order books (optional)

Trade tapes can't show spreads or resting depth. To record live books from Kalshi and Polymarket
(no keys needed), see [docs/RECORDER.md](docs/RECORDER.md):

```bash
uv run python scripts/live/record_books.py --dry-run --duration 60
```

## Before you test a strategy

Read [docs/strategies/README.md](docs/strategies/README.md) and
[docs/RESEARCH_LEDGER.md](docs/RESEARCH_LEDGER.md). Many ideas have been tried, and most failed. The
repository does not trade.

## History

- 2026-10-18: Polymarket setup now pulls the published Hugging Face snapshot by default.
- 2026-10-18: rewritten as the setup guide for new users. The earlier version (April 2026) described
  the whale-era paper-trading runner (`scripts/live/run_paper_trading.py`), now quarantined legacy
  code (docs/ARCHITECTURE.md, D14).
