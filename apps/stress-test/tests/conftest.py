"""Synthetic snapshot and events package: no database, no fund data.

Every price alternates +1% / -1% a day, so every instrument's volatility is the same before
each event and today, and the volatility rescale k is 1. Two events are planted:

- EVT-S, single session at bar 700: 6S jumps +20%, 6E falls 1%.
- EVT-W, a window at bars 900-904 (variant "deep"): ZN goes 0, -2%, -3%, +1%, +0.5%
  relative to bar 900. A second, milder variant ("mild") must not be picked. The package's
  ZN mdd is written as -0.05 against a true -0.03, so selfcheck has one mismatch to find.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

N_BARS = 1400
SINGLE_AT = 700
WINDOW_AT = 900
ZN_WINDOW = [0.0, -0.02, -0.03, 0.01, 0.005]  # levels relative to the bar-900 close
START_PRICE = {"6S": 1.0, "6E": 1.1, "ZN": 110.0, "ZT": 103.0, "CL": 80.0, "MES": 5000.0}
SPECS = {
    "6S": ("FX", 125000.0),
    "6E": ("FX", 125000.0),
    "ZN": ("Interest Rates", 1000.0),
    "ZT": ("Interest Rates", 2000.0),
    "CL": ("Energy", 1000.0),
    "MES": ("Equities", 5.0),
}
EQUITY = 1_000_000.0
SLEEVE = "TREND_FOLLOWING"
BOOKS = {
    "system": {"6S": -1, "ZN": -2, "CL": 1, "ZT": -1},
    "qt_proposal": {"6S": -1, "ZN": -2, "CL": 1, "ZT": -1},
    "qt": {"6S": 0, "ZN": -2, "CL": 1, "ZT": -1},
}
SETTINGS: dict[str, Any] = {
    "settings_used": {
        "config": {
            "portfolio_id": "TEST_PORTFOLIO",
            "initial_capital": EQUITY,
            "starting_capital": EQUITY,
            "strategies": {
                SLEEVE: {
                    "type": "TrendFollowingStrategy",
                    "enabled_backtest": True,
                    "config": {"ema_windows": [[2, 8], [64, 256]], "vol_lookback_long": 252},
                }
            },
            # read by the stand-in engine only (tests/fake_tradengin.py): the strategy's own book,
            # and what it trades on a run's last day
            "fake_book": {"ZN.v.0": -1.0, "CL.v.0": 1.0},
            "fake_last_day": {"ZN.v.0": -3.0},
            "live": {"historical_days": 30},
            "backtest": {"store_trade_details": True},
            "risk": {"schema": 2, "modules": []},
        }
    },
    "portfolio_config": {"statistics_K": 252.0},
}


def dates() -> pd.DatetimeIndex:
    return pd.bdate_range("2012-01-02", periods=N_BARS)


def closes_frame() -> pd.DataFrame:
    idx = dates()
    alt = np.where(np.arange(N_BARS) % 2 == 0, 1.01, 0.99)
    alt[0] = 1.0
    out = {}
    for r, p0 in START_PRICE.items():
        growth = alt.copy()
        if r == "6S":
            growth[SINGLE_AT] = 1.20
        if r == "6E":
            growth[SINGLE_AT] = 0.99
        if r == "ZN":
            for i in range(1, len(ZN_WINDOW)):
                growth[WINDOW_AT + i] = (1 + ZN_WINDOW[i]) / (1 + ZN_WINDOW[i - 1])
        out[r] = p0 * np.cumprod(growth)
    return pd.DataFrame(out, index=idx)


def write_snapshot(folder: Path, weekend_rows: bool = False) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    closes = closes_frame()
    long = closes.stack().rename("close").reset_index()
    long.columns = ["date", "root", "close"]
    long["symbol"] = long.root + ".v.0"
    if weekend_rows:
        sat = long[long.date == long.date.max()].assign(
            date=long.date.max() + pd.Timedelta(1, unit="D"), close=1e6
        )
        long = pd.concat([long, sat])
    long = long.assign(
        open=long.close, high=long.close * 1.004, low=long.close * 0.996, volume=1000.0
    )
    long["instrument_id"] = long.root.map({r: 100 + i for i, r in enumerate(START_PRICE)})
    long["date"] = long.date.dt.strftime("%Y-%m-%d")
    cols = ["date", "symbol", "open", "high", "low", "close", "volume", "instrument_id"]
    long[cols].to_csv(folder / "prices.csv.gz", index=False)
    pd.DataFrame(
        [
            {
                "Databento Symbol": r,
                "Sector": sec,
                "Contract Size": size,
                "Exchange": "CME",
                "Tick Size": 0.01,
                "Fee Per Contract": 0.85,
                "Overnight Initial Margin": 5000.0,
                "Overnight Maintenance Margin": 4000.0,
                "Trading Hours (EST)": "18:00-17:00",
                "Minimum Price Fluctuation": 10.0,
            }
            for r, (sec, size) in SPECS.items()
        ]
    ).to_csv(folder / "contracts.csv", index=False)
    rows = []
    for book, pos in BOOKS.items():
        rows += [
            {"book": book, "sleeve": SLEEVE, "symbol": f"{r}.v.0", "quantity": q}
            for r, q in pos.items()
        ]
    pd.DataFrame(rows).to_csv(folder / "positions.csv", index=False)
    # Stored history: the last three bar dates. The system book traded ZT -1 -> -2 on the last.
    d3 = [d.date().isoformat() for d in dates()[-3:]]
    hist = []
    for i, day in enumerate(d3):
        for book, pos in BOOKS.items():
            for r, q in pos.items():
                q = -2 if (r == "ZT" and book == "system" and i == 2) else q
                hist.append(
                    {
                        "date": day,
                        "book": book,
                        "sleeve": SLEEVE,
                        "symbol": f"{r}.v.0",
                        "quantity": q,
                    }
                )
    pd.DataFrame(hist).to_csv(folder / "positions_history.csv", index=False)
    pd.DataFrame(
        [
            {
                "date": day,
                "book": b,
                "book_source": None,
                "current_portfolio_value": EQUITY + 1000 * i,
                "daily_pnl": 1000.0,
            }
            for i, day in enumerate(d3)
            for b in ("system", "qt")
        ]
    ).to_csv(folder / "history.csv", index=False)
    (folder / "snapshot.json").write_text(
        json.dumps(
            {
                "portfolio_id": "TEST_PORTFOLIO",
                "date": "2017-06-01",
                "last_bar": str(dates()[-1].date()),
                "account": {
                    "date": "2017-05-31",
                    "book": "qt",
                    "value": EQUITY,
                    "starting_capital": EQUITY,
                    "peak": 2500.0,
                },
                "engine": {
                    "date": "2017-06-01",
                    "current_portfolio_value": EQUITY,
                    "gross_notional": 123.0,
                },
            }
        )
    )
    (folder / "settings.json").write_text(json.dumps(SETTINGS))
    return folder


def write_package(folder: Path) -> Path:
    idx = dates()
    (folder / "events").mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {"id": "EVT-S", "name": "Franc floor break", "klass": "Minor"},
            {"id": "EVT-W", "name": "Treasury selloff", "klass": "Major"},
        ]
    ).to_csv(folder / "MANIFEST.csv", index=False)

    single = folder / "events" / "EVT-S"
    single.mkdir()
    (single / "_index.json").write_text(json.dumps({"id": "EVT-S", "instruments": ["6S", "6E"]}))
    day, prev = idx[SINGLE_AT].date(), idx[SINGLE_AT - 1].date()
    pd.DataFrame(
        [
            {"wid": "EVT-S", "variant": None, "a": day, "b": day, "sym": "6S", "move": 0.20},
            {"wid": "EVT-S", "variant": None, "a": day, "b": day, "sym": "6E", "move": -0.01},
            {"wid": "EVT-S", "variant": None, "a": day, "b": day, "sym": "6B", "move": 0.0},
        ]
    ).assign(day=day, prev=prev).to_csv(single / "measured_singleday.csv", index=False)

    window = folder / "events" / "EVT-W"
    window.mkdir()
    (window / "_index.json").write_text(json.dumps({"id": "EVT-W", "instruments": ["ZN", "ES"]}))
    a, b = idx[WINDOW_AT].date(), idx[WINDOW_AT + 4].date()
    a2, b2 = idx[1000].date(), idx[1002].date()
    pd.DataFrame(
        [
            {"variant": "deep", "a": a, "b": b, "sym": "ZN", "net": 0.005, "mdd": -0.05},
            {"variant": "deep", "a": a, "b": b, "sym": "ES", "net": -0.0002, "mdd": -0.0101},
            {"variant": "mild", "a": a2, "b": b2, "sym": "ZN", "net": 0.0, "mdd": -0.01},
            {"variant": "mild", "a": a2, "b": b2, "sym": "ES", "net": 0.0, "mdd": -0.01},
        ]
    ).assign(wid="EVT-W").to_csv(window / "measured_window.csv", index=False)
    return folder


@pytest.fixture
def snapshot_dir(tmp_path: Path) -> Path:
    return write_snapshot(tmp_path / "snap")


@pytest.fixture
def package_dir(tmp_path: Path) -> Path:
    return write_package(tmp_path / "pkg")
