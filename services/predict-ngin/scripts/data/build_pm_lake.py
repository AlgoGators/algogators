"""Build the Polymarket data lake (thin wrapper; the builder is ``predict_ngin.lake.build_polymarket``).

Prefer the pipeline command, which also fetches new fills first: ``uv run predict-ngin data update polymarket``.

    uv run python scripts/data/fetch_pm_event_tags.py        # event tags for the category column (once, ~2-3 h)
    uv run python scripts/data/build_pm_lake.py markets
    uv run python scripts/data/build_pm_lake.py fills [--months 2026-03 2026-04] [--rebuild]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from predict_ngin.lake import build_polymarket as b  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["markets", "fills"])
    ap.add_argument("--months", nargs="*")
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args()
    b.build_markets() if a.what == "markets" else b.build_fills(a.months, a.rebuild)
