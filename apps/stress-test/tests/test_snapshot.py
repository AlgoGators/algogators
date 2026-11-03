import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from conftest import EQUITY, write_snapshot
from stress_test import snapshot as snapshot_mod
from stress_test.snapshot import (
    ENGINE_FIELDS,
    account_before,
    export_snapshot,
    load_bars,
    load_positions_history,
    load_snapshot,
    root,
)


def test_root_strips_the_continuous_suffix() -> None:
    assert (root("6E.v.0"), root("MES")) == ("6E", "MES")


def test_load_reads_books_specs_and_equity(snapshot_dir: Path) -> None:
    snap = load_snapshot(snapshot_dir)
    assert snap.portfolio_id == "TEST_PORTFOLIO" and snap.day == date(2017, 6, 1)
    assert snap.equity == EQUITY
    assert snap.books["system"] == {"6S": -1.0, "ZN": -2.0, "CL": 1.0, "ZT": -1.0}
    assert "6S" not in snap.books["qt"]  # a zero position is no position
    assert snap.multipliers["ZT"] == 2000.0 and snap.sectors["ZT"] == "Interest Rates"
    last = snap.closes.iloc[-1]
    assert snap.notional_per_contract()["6S"] == pytest.approx(last["6S"] * 125000)
    assert snap.settings["portfolio_config"] == {"statistics_K": 252.0}
    assert snap.peak == 2500.0 and str(snap.account_date) == "2017-05-31"
    assert snap.symbols is not None and snap.symbols["ZT"] == "ZT.v.0"


def test_weekend_bars_are_dropped_unless_asked_for(tmp_path: Path) -> None:
    folder = write_snapshot(tmp_path / "s", weekend_rows=True)
    assert (load_snapshot(folder).closes.index.dayofweek < 5).all()
    raw = load_snapshot(folder, weekdays_only=False).closes
    assert raw.index[-1].dayofweek == 5 and raw.iloc[-1].max() == 1e6


def test_sector_overrides_split_livestock_and_copper(snapshot_dir: Path) -> None:
    specs = pd.read_csv(snapshot_dir / "contracts.csv")
    extra = pd.DataFrame(
        {
            "Databento Symbol": ["HE", "HG"],
            "Sector": ["Agriculture", "Metals"],
            "Contract Size": [400, 25000],
        }
    )
    pd.concat([specs, extra]).to_csv(snapshot_dir / "contracts.csv", index=False)
    sectors = load_snapshot(snapshot_dir).sectors
    assert (sectors["HE"], sectors["HG"]) == ("Livestock", "Base metals")


class FakeCursor:
    """Answers each query by table, records every statement."""

    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.description: list[tuple[str]] = []
        self._rows: list[tuple[Any, ...]] = []

    def execute(self, sql: str, args: tuple[Any, ...] = ()) -> None:
        self.log.append(sql)
        cols: list[str]
        rows: list[tuple[Any, ...]]
        s = " ".join(sql.split())
        if "max(date)" in s:
            cols, rows = ["d"], [(date(2026, 11, 2),)]
        elif "trading.positions" in s:
            cols = ["date", "book", "sleeve", "symbol", "quantity"]
            d0, d1 = date(2026, 11, 1), date(2026, 11, 2)
            rows = [
                (d0, "system", "TREND_FOLLOWING", "6S.v.0", Decimal("-2")),
                (d1, "system", "TREND_FOLLOWING", "6S.v.0", Decimal("-1")),
                (d1, "qt", "TREND_FOLLOWING", "6S.v.0", Decimal("0")),
            ]
        elif "trading.live_results" in s and "daily_pnl" in s:
            cols = ["date", "book", "book_source", "current_portfolio_value", "daily_pnl"]
            rows = [
                (date(2026, 10, 31), "qt", "model", Decimal("510000"), Decimal("1")),
                (date(2026, 11, 1), "qt", "model", Decimal("489089.5"), Decimal("-2")),
                (date(2026, 11, 1), "system", None, Decimal("489000"), Decimal("-2")),
                (date(2026, 11, 2), "qt", "model", Decimal("489077.3"), Decimal("-3")),
            ]
        elif "trading.live_results" in s:
            cols = list(ENGINE_FIELDS)
            rows = [(date(2026, 11, 2), Decimal("490111.5"), *([None] * (len(cols) - 2)))]
        elif "live_run_metadata" in s:
            cols = ["date", "settings_used", "portfolio_config", "strategy_allocations"]
            used = {"risk": {"schema": 2}, "config": {"starting_capital": 500000.0}}
            rows = [(date(2026, 11, 2), used, {}, {"TREND_FOLLOWING": 1.0})]
        elif "contract_metadata" in s:
            cols, rows = ["Databento Symbol", "Sector", "Contract Size"], [("6S", "FX", "125000")]
        elif "ohlcv_1d" in s:
            cols = ["date", "symbol", "open", "high", "low", "close", "volume", "instrument_id"]
            bar = (*[Decimal("1.2")] * 4, Decimal("10"))
            rows = [
                (date(2026, 10, 30), "6S.v.0", *bar, "42140878"),
                (date(2026, 10, 30), "6S.v.0", *bar, "42140879"),  # two raw prints: one bar kept
            ]
        else:
            raise AssertionError(f"unexpected query: {s}")
        self.description = [(c,) for c in cols]
        self._rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class FakeConnection:
    def __init__(self) -> None:
        self.log: list[str] = []
        self.closed = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.log)

    def close(self) -> None:
        self.closed = True


def test_export_runs_only_selects_and_writes_a_loadable_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = FakeConnection()
    monkeypatch.setattr(snapshot_mod, "_connect", lambda _cfg: con)

    out = export_snapshot("QT_TEST", None, tmp_path, tmp_path / "db.json")

    assert out == tmp_path / "QT_TEST_2026-11-02"
    assert con.closed
    assert con.log and all(q.lstrip().upper().startswith("SELECT") for q in con.log)
    meta = json.loads((out / "snapshot.json").read_text())
    assert (
        meta["last_bar"] == "2026-10-30" and meta["engine"]["current_portfolio_value"] == 490111.5
    )
    assert json.loads((out / "settings.json").read_text())["settings_used"]["risk"] == {"schema": 2}
    # The account is the final book's value at the latest close before the day; the peak is the
    # highest cumulative P&L above the starting capital (510,000 - 500,000).
    assert meta["account"] == {
        "date": "2026-11-01",
        "book": "qt",
        "value": 489089.5,
        "starting_capital": 500000.0,
        "peak": 10000.0,
    }
    snap = load_snapshot(out)
    assert snap.books == {"system": {"6S": -1.0}, "qt": {}}
    assert snap.equity == 489089.5 and snap.peak == 10000.0
    assert snap.notional_per_contract() == {"6S": pytest.approx(1.2 * 125000)}
    bars = load_bars(out)
    assert len(bars) == 1 and bars["instrument_id"].tolist() == ["42140878"]
    history = load_positions_history(out)
    assert set(history["date"].dt.date) == {date(2026, 11, 1), date(2026, 11, 2)}
    assert "sleeve" in pd.read_csv(out / "positions.csv").columns


def test_export_refuses_a_day_without_positions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = FakeConnection()
    monkeypatch.setattr(snapshot_mod, "_connect", lambda _cfg: con)
    original = FakeCursor.execute

    def no_positions(self: FakeCursor, sql: str, args: tuple[Any, ...] = ()) -> None:
        original(self, sql, args)
        if "trading.positions" in sql and "max(date)" not in sql:
            self._rows = []

    monkeypatch.setattr(FakeCursor, "execute", no_positions)
    with pytest.raises(SystemExit, match="no positions"):
        export_snapshot("QT_TEST", date(2026, 1, 5), tmp_path, tmp_path / "db.json")
    assert con.closed


def test_account_before_reads_the_system_book_without_a_desk_book() -> None:
    h = pd.DataFrame(
        [
            {"date": "2026-01-02", "book": "system", "current_portfolio_value": 480000.0},
            {"date": "2026-01-05", "book": "system", "current_portfolio_value": 470000.0},
            {"date": "2026-01-06", "book": "system", "current_portfolio_value": 999999.0},
        ]
    )
    got = account_before(h, date(2026, 1, 6), 500000.0)
    assert got == {
        "date": "2026-01-05",
        "book": "system",
        "value": 470000.0,
        "starting_capital": 500000.0,
        "peak": 0.0,
    }
    assert account_before(h.iloc[0:0], date(2026, 1, 6), 500000.0) is None
    assert "peak" not in (account_before(h, date(2026, 1, 6), None) or {})


def test_an_old_snapshot_without_ids_or_account_still_loads(tmp_path: Path) -> None:
    folder = write_snapshot(tmp_path / "old")
    meta = json.loads((folder / "snapshot.json").read_text())
    del meta["account"]
    (folder / "snapshot.json").write_text(json.dumps(meta))
    px = pd.read_csv(folder / "prices.csv.gz").drop(columns=["instrument_id"])
    px.to_csv(folder / "prices.csv.gz", index=False)
    snap = load_snapshot(folder)
    assert snap.equity == EQUITY and snap.peak == 0.0 and snap.account_date is None
    assert (load_bars(folder)["instrument_id"] == "").all()
