"""Day-D snapshot: one read-only export from the team database, then local files only.

`export_snapshot` is the only code in this package that talks to a database. It opens the
session read-only, so the server refuses any write, and every statement is a SELECT.
Everything else reads the folder it writes:

    snapshot.json          portfolio, day, last bar used, the account at the close before the day
                           (and the half compounding's running peak), the engine's risk readings
    positions.csv          book, sleeve, symbol, quantity for the three desk books on the day
    positions_history.csv  the same for every stored day up to the day (replay-check reads it)
    history.csv            the stored daily results up to the day (date, book, account value)
    settings.json          the settings, portfolio config and allocations the engine ran with
    contracts.csv          contract specs (the "Contract Size" column is the dollar multiplier)
    prices.csv.gz          daily continuous bars for every symbol, strictly before the day, read as
                           the engine reads them: one copy per symbol and instant, with the
                           vendor's contract id of that print
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

DB_CONFIG_ENV = "STRESS_TEST_DB_CONFIG"
# The engine's own config file (gitignored) holds the team database's connection.
ENGINE_DB_CONFIG = Path("services") / "trade-ngin" / "config" / "defaults.json"


def default_db_config() -> Path | None:
    """$STRESS_TEST_DB_CONFIG, else the engine's config file in the checkout the working directory
    or this tool sits in. None: there is none, so the caller must name one."""
    named = os.environ.get(DB_CONFIG_ENV)
    if named:
        return Path(named)
    for start in (Path.cwd(), Path(__file__).resolve()):
        for folder in (start, *start.parents):
            if (folder / ENGINE_DB_CONFIG).is_file():
                return folder / ENGINE_DB_CONFIG
    return None


BOOKS = ("system", "qt_proposal", "qt")

# The contract table's Sector is coarse where it matters for event contagion: livestock and
# grains react to different shocks, and copper is not a precious metal.
SECTOR_OVERRIDES = {"HE": "Livestock", "LE": "Livestock", "GF": "Livestock", "HG": "Base metals"}

# live_results columns kept as the engine's own reading of the book on the day.
ENGINE_FIELDS = (
    "date",
    "current_portfolio_value",
    "gross_notional",
    "net_notional",
    "portfolio_leverage",
    "net_leverage",
    "portfolio_var",
    "jump_risk",
    "var_95_1d",
    "overlay_risk",
    "overlay_risk_jump",
    "overlay_risk_shock",
    "overlay_gross_leverage",
    "overlay_net_leverage",
)


# The engine's futures bar query (services/trade-ngin market_data_utils.cpp
# build_futures_bar_query): ohlcv_1d has no key and can hold several copies of a print, so one copy
# per symbol and instant is kept in the engine's order, and the vendor's contract id comes from the
# raw table's identical print.
PRICES_SQL = """
SELECT (k.time AT TIME ZONE 'UTC')::date AS date, k.symbol, k.open, k.high, k.low, k.close,
       k.volume, r.instrument_id
FROM (SELECT DISTINCT ON (symbol, time) time, symbol, open, high, low, close, volume
      FROM futures_data.ohlcv_1d
      WHERE time < (%s::date)::timestamp AT TIME ZONE 'UTC'
      ORDER BY symbol, time, volume DESC, close ASC, open ASC, high ASC, low ASC) AS k
LEFT JOIN futures_data.ohlcv_1d_raw AS r
  ON r.symbol = k.symbol AND r.ts_event = k.time AND r.volume = k.volume AND r.open = k.open
 AND r.high = k.high AND r.low = k.low AND r.close = k.close
ORDER BY k.symbol, k.time
"""


def root(symbol: str) -> str:
    """`6E.v.0` -> `6E`. Books, prices and contract specs are joined on the root."""
    return symbol.split(".", 1)[0]


@dataclass(frozen=True)
class Snapshot:
    portfolio_id: str
    day: date
    last_bar: date
    equity: float  # the account at the close before the day (the engine's reading if unknown)
    books: dict[str, dict[str, float]]  # book -> root -> contracts
    closes: pd.DataFrame  # index: bar date; columns: root
    multipliers: dict[str, float]  # root -> dollars per 1.0 of quoted price
    sectors: dict[str, str]  # root -> sector, for the "sector" scope
    engine: dict[str, Any]
    settings: dict[str, Any]
    folder: Path | None = None
    peak: float = 0.0  # running peak of cumulative P&L above the starting capital, floored at 0
    symbols: dict[str, str] | None = None  # root -> the engine's symbol ("6E" -> "6E.v.0")
    account_date: date | None = None  # the stored day `equity` was read from

    def notional_per_contract(self) -> dict[str, float]:
        """Dollar notional of one contract at the last close before the day."""
        last = self.closes.ffill().iloc[-1]
        return {
            r: float(last[r]) * self.multipliers[r]
            for r in self.multipliers
            if r in last.index and pd.notna(last[r])
        }


# ----------------------------------------------------------------------------- export
def _connect(db_config: Path) -> Any:
    import psycopg2

    cfg = json.loads(db_config.read_text())["database"]
    con = psycopg2.connect(
        host=cfg["host"],
        port=cfg["port"],
        dbname=cfg["name"],
        user=cfg["username"],
        password=cfg["password"],
        connect_timeout=15,
    )
    con.set_session(readonly=True)
    return con


def _rows(cur: Any, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cur.execute(sql, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


def _plain(v: Any) -> Any:
    """Decimals and dates from psycopg2 -> JSON-friendly values."""
    if isinstance(v, date | datetime):
        return v.isoformat()
    if v is None or isinstance(v, bool | int | float | str | dict | list):
        return v
    return float(v)


def export_snapshot(portfolio_id: str, day: date | None, out_root: Path, db_config: Path) -> Path:
    con = _connect(db_config)
    try:
        cur = con.cursor()
        if day is None:
            day = _rows(
                cur,
                "SELECT max(date) AS d FROM trading.positions WHERE portfolio_id = %s",
                (portfolio_id,),
            )[0]["d"]
            if day is None:
                raise SystemExit(f"no positions for {portfolio_id}")

        history_positions = _rows(
            cur,
            """SELECT date, portfolio_type AS book, strategy_name AS sleeve, symbol, quantity
               FROM trading.positions WHERE portfolio_id = %s AND date <= %s
               ORDER BY date, portfolio_type, symbol""",
            (portfolio_id, day),
        )
        positions = [
            {k: v for k, v in r.items() if k != "date"}
            for r in history_positions
            if r["date"] == day
        ]
        if not positions:
            raise SystemExit(f"no positions for {portfolio_id} on {day}")
        results = _rows(
            cur,
            """SELECT date, portfolio_type AS book, book_source, current_portfolio_value, daily_pnl
               FROM trading.live_results WHERE portfolio_id = %s AND date <= %s
               ORDER BY date, portfolio_type, book_source""",
            (portfolio_id, day),
        )

        # Two rows per day can exist (book_source 'model' and NULL) with the same equity;
        # prefer the system book's 'model' row. Fall back to the latest day at or before D.
        engine = _rows(
            cur,
            f"""SELECT {", ".join(ENGINE_FIELDS)} FROM trading.live_results
                WHERE portfolio_id = %s AND date <= %s
                ORDER BY date DESC, (portfolio_type = 'system') DESC,
                         (book_source = 'model') DESC NULLS LAST
                LIMIT 1""",
            (portfolio_id, day),
        )
        if not engine:
            raise SystemExit(f"no live_results for {portfolio_id} at or before {day}")

        settings = _rows(
            cur,
            """SELECT date, settings_used, portfolio_config, strategy_allocations
               FROM trading.live_run_metadata WHERE portfolio_id = %s AND date <= %s
               ORDER BY date DESC, created_at DESC LIMIT 1""",
            (portfolio_id, day),
        )
        contracts = _rows(cur, "SELECT * FROM metadata.contract_metadata")
        # Bars are stamped 00:00 UTC; read them as UTC dates whatever the session timezone.
        prices = _rows(cur, PRICES_SQL, (day,))
    finally:
        con.close()

    out = out_root / f"{portfolio_id}_{day.isoformat()}"
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(positions).assign(quantity=lambda d: d.quantity.astype(float)).to_csv(
        out / "positions.csv", index=False
    )
    pd.DataFrame(history_positions).assign(quantity=lambda d: d.quantity.astype(float)).to_csv(
        out / "positions_history.csv", index=False
    )
    hist = pd.DataFrame(results, columns=HISTORY_COLUMNS)
    for c in ("current_portfolio_value", "daily_pnl"):
        hist[c] = hist[c].astype(float)
    hist.to_csv(out / "history.csv", index=False)
    px = pd.DataFrame(prices)
    for c in ("open", "high", "low", "close", "volume"):
        px[c] = px[c].astype(float)
    # The raw table can hold two identical prints with different ids: keep one bar per instant.
    px = px.drop_duplicates(["symbol", "date"], keep="first")
    px.to_csv(out / "prices.csv.gz", index=False)
    pd.DataFrame(contracts).to_csv(out / "contracts.csv", index=False)
    s = settings[0] if settings else {}
    meta = {
        "portfolio_id": portfolio_id,
        "date": day.isoformat(),
        "last_bar": str(px["date"].max()),
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "account": account_before(hist, day, _starting_capital(s)),
        "engine": {k: _plain(v) for k, v in engine[0].items()},
    }
    (out / "snapshot.json").write_text(json.dumps(meta, indent=2))
    (out / "settings.json").write_text(json.dumps({k: _plain(v) for k, v in s.items()}, indent=2))
    return out


HISTORY_COLUMNS = ["date", "book", "book_source", "current_portfolio_value", "daily_pnl"]


def _starting_capital(settings: dict[str, Any]) -> float | None:
    used = (settings.get("settings_used") or {}).get("config") or {}
    for key in ("starting_capital", "initial_capital"):
        if used.get(key) is not None:
            return float(used[key])
    total = (settings.get("portfolio_config") or {}).get("total_capital")
    return float(total) if total is not None else None


def account_before(
    history: pd.DataFrame, day: date, starting_capital: float | None
) -> dict[str, Any] | None:
    """The account at the close before `day`: the stored value of the latest day before it, read
    from the final desk book where the portfolio has one (the account actually held), else the
    system book. With the starting capital, also the half compounding's running peak: the highest
    cumulative P&L above the starting capital so far, floored at 0 (sizing_capital.hpp)."""
    if history.empty:
        return None
    h = history.assign(date=pd.to_datetime(history["date"]).dt.date)
    h = h[h["date"] < day]
    book = "qt" if (h["book"] == "qt").any() else "system"
    h = h[h["book"] == book].sort_values("date").drop_duplicates("date", keep="last")
    if h.empty:
        return None
    out: dict[str, Any] = {
        "date": h["date"].iloc[-1].isoformat(),
        "book": book,
        "value": float(h["current_portfolio_value"].iloc[-1]),
    }
    if starting_capital is not None:
        cumulative = h["current_portfolio_value"].astype(float) - starting_capital
        out["starting_capital"] = starting_capital
        out["peak"] = max(0.0, float(cumulative.max()))
    return out


# ----------------------------------------------------------------------------- load
def load_bars(folder: Path) -> pd.DataFrame:
    """Every stored bar of the snapshot as the engine gets them (weekend prints included)."""
    px = pd.read_csv(folder / "prices.csv.gz", parse_dates=["date"], dtype={"symbol": str})
    if "instrument_id" not in px.columns:  # a snapshot exported before ids were read
        px["instrument_id"] = ""
    px["instrument_id"] = px["instrument_id"].map(_id_text)
    return px.sort_values(["symbol", "date"]).reset_index(drop=True)


def _id_text(v: Any) -> str:
    """Vendor ids are integers stored as text; CSV round trips can turn them into floats."""
    if v is None or (isinstance(v, float) and pd.isna(v)) or v == "":
        return ""
    try:
        return str(int(float(v)))
    except (TypeError, ValueError):
        return str(v)


def load_positions_history(folder: Path) -> pd.DataFrame:
    path = folder / "positions_history.csv"
    if not path.exists():
        raise SystemExit(f"{folder} has no positions_history.csv: export the snapshot again")
    h = pd.read_csv(path, parse_dates=["date"])
    return h[h["quantity"] != 0]


def load_snapshot(folder: Path, weekdays_only: bool = True) -> Snapshot:
    """Read an exported folder. `weekdays_only` drops the thin weekend bars the feed stores
    (Sunday-evening session stubs), as the engine does; they bias volatility estimates."""
    meta = json.loads((folder / "snapshot.json").read_text())
    pos = pd.read_csv(folder / "positions.csv")
    books: dict[str, dict[str, float]] = {}
    for book, grp in pos.groupby("book"):
        books[str(book)] = {
            root(s): float(q) for s, q in zip(grp.symbol, grp.quantity, strict=True) if q != 0
        }

    px = pd.read_csv(folder / "prices.csv.gz", parse_dates=["date"])
    px["root"] = px.symbol.map(root)
    symbols = dict(zip(px["root"], px["symbol"], strict=True))
    closes = px.pivot_table(index="date", columns="root", values="close", aggfunc="last")
    closes = closes.sort_index()
    if weekdays_only:
        closes = closes[closes.index.dayofweek < 5]

    specs = pd.read_csv(folder / "contracts.csv")
    multipliers = {
        str(s): float(m)
        for s, m in zip(specs["Databento Symbol"], specs["Contract Size"], strict=True)
        if pd.notna(m)
    }
    sectors = {
        str(s): SECTOR_OVERRIDES.get(str(s), str(sec))
        for s, sec in zip(specs["Databento Symbol"], specs["Sector"], strict=True)
        if pd.notna(sec)
    }
    settings_path = folder / "settings.json"
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    account = meta.get("account") or {}
    return Snapshot(
        portfolio_id=meta["portfolio_id"],
        day=date.fromisoformat(meta["date"]),
        last_bar=date.fromisoformat(meta["last_bar"]),
        equity=float(account.get("value", meta["engine"]["current_portfolio_value"])),
        books=books,
        closes=closes,
        multipliers=multipliers,
        sectors=sectors,
        engine=meta["engine"],
        settings=settings,
        folder=folder,
        peak=float(account.get("peak", 0.0)),
        symbols=symbols,
        account_date=date.fromisoformat(account["date"]) if account.get("date") else None,
    )
