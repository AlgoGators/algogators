# SharpAPI odds recorder

Last revised: 2026-10-17

SharpAPI (sharpapi.io) sells real-time odds from US sportsbooks, sharp books (e.g. Pinnacle) and
exchanges (Betfair, Novig, ProphetX, Polymarket). History is Enterprise-only, so the recorder
stores the live feed as it arrives. We use it as a fair-value reference for Kalshi and Polymarket
sports markets.

## Use

```
# put SHARPAPI_KEY=... in .env
uv run --no-sync python -m predict_ngin.capture.sharpapi probe            # tier, limit, books, fields
uv run --no-sync python -m predict_ngin.capture.sharpapi run --hours 70   # record (Ctrl+C or STOP to end)
uv run --no-sync python -m predict_ngin.capture.sharpapi status
```

Optional filters: `--sport football,basketball`, `--league nfl`, `--sportsbook pinnacle,draftkings`.
Stop early with `data/sharpapi/STOP` (an empty file).

## What it records

The scheduler, request pacing, dedupe and file layout are described in the module docstring of
`predict_ngin/capture/sharpapi.py`. In short:
- odds deltas every 5 s (20 s on the free tier) and a full snapshot every 30 min;
- events every 5 min, plus finished events every 30 min;
- closing lines and betting splits when the tier allows them;
- reference lists and account usage.

Files go under `data/sharpapi/YYYY-MM-DD/<kind>/part-*.parquet`. Query them with DuckDB:
`read_parquet('data/sharpapi/*/odds/*.parquet', union_by_name=true)`.

## Limits and terms

- Rate limits per key per minute: Free 12 (with a 60 s data delay, 2 books), Hobby 120, Pro 300,
  Sharp 1,000. The recorder uses 85% of the limit reported in the response headers.
- Paid trials last 3 days, need a card and are one per person; cancel before the end to avoid a
  charge.
- The terms (sharpapi.io/terms) allow storing data for internal research indefinitely. Raw data
  must not be redistributed. Keep `data/sharpapi/` out of git (all of `data/` is ignored).
