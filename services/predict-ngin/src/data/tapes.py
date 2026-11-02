"""Registry of the shared research tapes: how each is turned into a TapeStore. Research only.

    from src.data.tapes import open_tape
    st = open_tape("kalshi_base")          # builds on first use (or when the source Parquet changed), then mmaps

Data root: $PREDICT_NGIN_DATA, else <repo>/data. (Worktrees: point PREDICT_NGIN_DATA at the main checkout's data/;
the cache goes to $PREDICT_NGIN_TAPE_CACHE or <repo>/data/cache/tapes.)
"""
from __future__ import annotations

import os
from pathlib import Path

from .tape_store import ROOT, TapeStore


def data_root() -> Path:
    env = os.environ.get("PREDICT_NGIN_DATA")
    return Path(env) if env else ROOT / "data"


TAPES = {
    # data/vendor/micro/base.parquet (micro_build_base.py): settled Kalshi markets, sorted by ticker, created_time.
    # Consumers: mkgap_*, micro_*, audit_flb_* (via kalshi_flb), sharp/wx/rain makers, ...
    "kalshi_base": dict(
        src="vendor/micro/base.parquet",
        print_cols=["ts", "yc", "tyes", "qty"],
        ticker_cols=["event_ticker", "series", "cat", "close_ts", "settle_ts", "win", "fee_type", "fee_mult"],
        sort_cols=["created_time"],
    ),
    # data/vendor/ment5/tape.parquet (ment5_yesbias.py): mention markets; mrev_* order it by ticker, tus.
    # NOT stored sorted -> presort=True: one out-of-core DuckDB sort into the cache at build time.
    "ment5": dict(
        src="vendor/ment5/tape.parquet", presort=True,
        print_cols=["tus", "ts", "y", "tyes", "qty", "adv"],
        ticker_cols=["event_ticker", "series", "result", "settle_ts", "earn", "open_ts", "ev_end", "y01", "test"],
        sort_cols=["tus"],
    ),
}


def open_tape(name: str, rebuild: bool = False, cache_root: Path | None = None, verbose: bool = True) -> TapeStore:
    spec = TAPES[name]
    return TapeStore.build_from_parquet(data_root() / spec["src"], name, print_cols=spec["print_cols"],
                                        ticker_cols=spec["ticker_cols"], sort_cols=spec["sort_cols"],
                                        presort=spec.get("presort", False),
                                        cache_root=cache_root, rebuild=rebuild, verbose=verbose)
