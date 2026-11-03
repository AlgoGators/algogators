"""The trade-ngin engine, driven through its public Python module (`tradengin`) as published.

This is the one module that imports `tradengin`, and it needs no change to the engine. It uses the
module's documented surface (BacktestRunner.initialize_with_config with a config directory,
BacktestRunConfig, MarketDataSource, InstrumentRegistry, FuturesSpec, Bar) and the engine's own CSV
export, never its internals, so an engine update that keeps that surface keeps this tool working.
What the installed module offers is read at run time (`capabilities`): a bar's vendor contract id
and a contract's own fee are handed over where the module has them, and named as caveats where it
does not.

Every backtest runs in a fresh process (`run_in_subprocess`): the engine keeps process-wide
singletons and holds the GIL, so runs neither share a process nor gain from threads.
"""

from __future__ import annotations

import bisect
import datetime as dt
import importlib
import json
import os
import subprocess
import sys
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

UTC = dt.UTC
ENGINE_ENV = "TRADENGIN_DIR"  # a directory holding a locally built tradengin module
PORTFOLIO_DIR = "stress"  # the book's folder in the config tree a run writes


# ----------------------------------------------------------------------------- the module
@dataclass(frozen=True)
class Capabilities:
    version: str | None
    location: str
    config_runs: bool  # BacktestRunConfig.config_base_path and the CSV export (needed)
    bar_instrument_id: bool  # Bar.instrument_id: the engine sees rolls as production does
    fee_per_contract: bool  # FuturesSpec.fee_per_contract: the contract's own fee is charged

    def missing_for_replay(self) -> list[str]:
        return [] if self.config_runs else ["BacktestRunConfig.config_base_path / export_csv"]

    def caveats(self) -> list[str]:
        out = []
        if not self.bar_instrument_id:
            out.append(
                "this engine's Bar has no instrument_id: it is given the history with each "
                "roll's price gap taken out instead of the contract ids production reads"
            )
        if not self.fee_per_contract:
            out.append(
                "this engine's FuturesSpec has no fee_per_contract: fills pay the cost model's "
                "flat default fee, not each contract's own"
            )
        return out


def import_engine(module_dir: str | os.PathLike[str] | None = None) -> Any:
    """`module_dir` (or $TRADENGIN_DIR) first, then an installed `tradengin` package."""
    where = module_dir or os.environ.get(ENGINE_ENV)
    if where:
        sys.path.insert(0, str(Path(where).resolve()))
    try:
        return importlib.import_module("tradengin")
    except ImportError as e:
        raise SystemExit(
            "the trade-ngin Python module (tradengin) is not importable: install it "
            "(pip install tradengin, Linux and Windows wheels) or build it from "
            "services/trade-ngin and pass --engine DIR or set "
            f"{ENGINE_ENV}. ({e})"
        ) from e


def capabilities(te: Any) -> Capabilities:
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            ver: str | None = version("tradengin")
        except PackageNotFoundError:
            ver = None
    except ImportError:  # pragma: no cover
        ver = None
    cfg = te.BacktestRunConfig()
    return Capabilities(
        version=getattr(te, "__version__", None) or ver,
        location=str(getattr(te, "__file__", "?")),
        config_runs=all(
            hasattr(cfg, a) for a in ("config_base_path", "export_csv", "csv_output_path")
        ),
        bar_instrument_id=hasattr(te.Bar(), "instrument_id"),
        fee_per_contract=hasattr(te.FuturesSpec(), "fee_per_contract"),
    )


# ----------------------------------------------------------------------------- the book's config
def write_config_tree(settings: Mapping[str, Any], out: Path, capital: float | None = None) -> Path:
    """The config tree the engine's ConfigLoader reads, from the settings production recorded
    for the day (live_run_metadata.settings_used), so the replayed strategy is the one that
    produced the stored book. The record leaves out two things a run needs: the statistics'
    sessions a year (recorded as portfolio_config.statistics_K) and the database (not used: the
    run reads bars from this tool and stores nothing). `capital` replaces the starting capital
    (a run starts flat on it: the account of the day, so the book is sized as production's)."""
    used = (settings.get("settings_used") or {}).get("config")
    if not used:
        raise SystemExit("the snapshot has no recorded settings (settings.json settings_used)")
    book = {k: v for k, v in used.items() if k != "risk"}
    book.setdefault("backtest", {})["store_trade_details"] = False
    if capital is not None:
        book["initial_capital"] = book["starting_capital"] = float(capital)
    k = (settings.get("portfolio_config") or {}).get("statistics_K")
    # The loader refuses a config without database fields although this run never connects
    # (the bars come from this tool and storage is off). ".invalid" names resolve nowhere
    # (RFC 2606), so even a connection attempt could not reach a server.
    defaults: dict[str, Any] = {
        "database": {
            "host": "unused.invalid",
            "port": "1",
            "username": "unused",
            "password": "unused",
            "name": "unused",
            "num_connections": 1,
        },
        "backtest": {"store_trade_details": False},
    }
    if k:
        defaults["statistics"] = {"sessions_per_year": {"futures": float(k), "equities": 252.0}}
    folder = out / "portfolios" / PORTFOLIO_DIR
    folder.mkdir(parents=True, exist_ok=True)
    (out / "defaults.json").write_text(json.dumps(defaults, indent=2))
    (folder / "portfolio.json").write_text(json.dumps(book, indent=2))
    (folder / "risk.json").write_text(json.dumps(used.get("risk", {}), indent=2))
    (folder / "email.json").write_text("{}")
    return out


def warmup_sessions(settings: Mapping[str, Any]) -> int:
    """The bar dates a run spends warming up before it trades: the longest lookback of the recorded
    sleeves (their slowest EMA and their long volatility window), as the engine counts it. Only
    where trading starts depends on it, and a run reads back where it did start."""
    used = (settings.get("settings_used") or {}).get("config") or {}
    longest = 0
    for sdef in (used.get("strategies") or {}).values():
        cfg = (sdef or {}).get("config") or {}
        spans = [int(w[1]) for w in cfg.get("ema_windows", []) if len(w) == 2]
        longest = max(longest, *spans, int(cfg.get("vol_lookback_long", 252)))
    return longest or 256


# ----------------------------------------------------------------------------- one run
@dataclass
class RunSpec:
    """Everything one engine run needs, as plain data (it crosses a process boundary)."""

    bars: list[str]  # CSVs of date, symbol, open, high, low, close, volume, instrument_id
    contracts: str  # contracts.csv of the snapshot
    config_dir: str  # write_config_tree's output
    out_dir: str  # the run's working directory (the engine logs into ./logs) and CSV export
    start: str  # first bar date of the window (warm-up starts here)
    end: str
    keep_from: str  # rows of the export from this date on come back
    module_dir: str | None = None


@dataclass
class RunResult:
    ok: bool
    error: str = ""
    equity: list[tuple[str, float]] = field(default_factory=list)  # date, account value
    positions: list[tuple[str, str, float]] = field(default_factory=list)  # date, symbol, qty
    seconds: float = 0.0


def _local_naive(d: dt.date) -> dt.datetime:
    """pybind11's chrono caster reads a naive datetime as LOCAL wall time and ignores tzinfo, so
    midnight UTC of `d` (the stored bar instant) is handed over as that instant's local time."""
    return dt.datetime(d.year, d.month, d.day, tzinfo=UTC).astimezone().replace(tzinfo=None)


def _to_utc(t: dt.datetime) -> dt.datetime:
    return t.astimezone(UTC)  # a naive datetime is taken as local time


def _register_instruments(te: Any, contracts: pd.DataFrame, caps: Capabilities) -> None:
    """As the engine's database loader does from metadata.contract_metadata."""
    reg = te.InstrumentRegistry.instance()
    reg.clear()
    reg.initialize_without_database()
    for _, r in contracts.iterrows():
        root = str(r["Databento Symbol"])
        spec = te.FuturesSpec()
        spec.root_symbol = root
        spec.exchange = str(r["Exchange"])
        spec.currency = "USD"
        size = float(r["Contract Size"]) if pd.notna(r["Contract Size"]) else 0.0
        spec.multiplier = size if size > 0 else 1.0
        spec.tick_size = float(r["Tick Size"])
        fee = float(r["Fee Per Contract"])
        spec.commission_per_contract = fee
        spec.initial_margin = float(r["Overnight Initial Margin"])
        spec.maintenance_margin = float(r["Overnight Maintenance Margin"])
        spec.trading_hours = str(r["Trading Hours (EST)"])
        if caps.fee_per_contract:
            spec.fee_per_contract = fee
            spec.tick_value = float(r["Minimum Price Fluctuation"])
        reg.register_instrument(root, te.FuturesInstrument(root, spec))


def _source(te: Any, bars: pd.DataFrame, contracts: pd.DataFrame, caps: Capabilities) -> Any:
    class FrameSource(te.MarketDataSource):
        """Serves the run's bars. A bar's instant is midnight UTC of its date (the stored instant),
        and a range selects on that instant, as the engine's database query does."""

        def __init__(self) -> None:
            super().__init__()
            self._by_symbol: dict[str, tuple[list[dt.datetime], list[Any]]] = {}
            for sym, g in bars.groupby("symbol", sort=False):
                days = [d.date() for d in g["date"]]
                instants = [dt.datetime(d.year, d.month, d.day, tzinfo=UTC) for d in days]
                out = []
                for d, o, h, lo, c, v, iid in zip(
                    days,
                    g["open"],
                    g["high"],
                    g["low"],
                    g["close"],
                    g["volume"].fillna(0.0),
                    g["instrument_id"],
                    strict=True,
                ):
                    bar = te.Bar(
                        _local_naive(d), float(o), float(h), float(lo), float(c), float(v), str(sym)
                    )
                    if caps.bar_instrument_id and isinstance(iid, str) and iid:
                        bar.instrument_id = iid
                    out.append(bar)
                self._by_symbol[str(sym)] = (instants, out)
            self._sectors = [
                (str(r["Databento Symbol"]), str(r["Sector"]))
                for _, r in contracts.iterrows()
                if isinstance(r["Sector"], str)
            ]

        def load_bars(self, symbols, start_date, end_date, asset_class, freq):
            lo, hi = _to_utc(start_date), _to_utc(end_date)
            out = []
            for s in symbols:
                instants, items = self._by_symbol.get(s, ([], []))
                i, j = bisect.bisect_left(instants, lo), bisect.bisect_right(instants, hi)
                out.extend(items[i:j])
            return out

        def get_symbols(self, asset_class):
            return sorted(self._by_symbol)

        def get_contract_sectors(self):
            return list(self._sectors)

    return FrameSource()


def run_spec(spec: RunSpec) -> RunResult:
    """Run one backtest in THIS process. Call it in a fresh process (run_in_subprocess)."""
    import time

    t0 = time.perf_counter()
    out = Path(spec.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    os.chdir(out)  # the engine writes its logs under ./logs
    te = import_engine(spec.module_dir)
    caps = capabilities(te)
    missing = caps.missing_for_replay()
    if missing:
        return RunResult(False, "this engine lacks " + ", ".join(missing))
    bars = pd.concat(
        [pd.read_csv(f, parse_dates=["date"], dtype={"instrument_id": str}) for f in spec.bars],
        ignore_index=True,
    )
    bars = bars[bars["date"] <= pd.Timestamp(spec.end)]
    bars = bars.sort_values(["symbol", "date"]).reset_index(drop=True)
    contracts = pd.read_csv(spec.contracts)
    _register_instruments(te, contracts, caps)

    cfg = te.BacktestRunConfig()
    cfg.symbols = sorted(bars["symbol"].unique().tolist())
    cfg.start_date = _local_naive(dt.date.fromisoformat(spec.start))
    cfg.end_date = _local_naive(dt.date.fromisoformat(spec.end))
    cfg.asset_class = te.AssetClass.FUTURES
    cfg.data_freq = te.DataFrequency.DAILY
    cfg.data_source = _source(te, bars, contracts, caps)
    cfg.config_base_path = spec.config_dir
    cfg.store_results = False
    cfg.export_csv = True
    cfg.csv_output_path = str(out / "csv")

    runner = te.BacktestRunner()
    try:
        runner.initialize_with_config(PORTFOLIO_DIR, cfg)
        runner.run_backtest()
    except Exception as e:  # the module raises on an engine error result
        return RunResult(False, str(e)[:2000], seconds=time.perf_counter() - t0)

    folder = next((p for p in (out / "csv").iterdir() if p.is_dir()), None)
    if folder is None:
        return RunResult(False, "the engine wrote no CSV export", seconds=time.perf_counter() - t0)
    eq = pd.read_csv(folder / "equity_curve.csv", comment="#")
    pos = pd.read_csv(folder / "positions.csv", comment="#")
    eq = eq[eq["date"].astype(str) >= spec.keep_from]
    pos = pos[pos["date"].astype(str) >= spec.keep_from]
    agg = pos.groupby(["date", "symbol"], as_index=False)["quantity"].sum()
    return RunResult(
        True,
        equity=[(str(d), float(v)) for d, v in zip(eq["date"], eq["portfolio_value"], strict=True)],
        positions=[
            (str(d), str(s), float(q))
            for d, s, q in zip(agg["date"], agg["symbol"], agg["quantity"], strict=True)
        ],
        seconds=time.perf_counter() - t0,
    )


def run_in_subprocess(spec: RunSpec, timeout: float = 1800.0) -> RunResult:
    """One run in a fresh interpreter (`python -m stress_test.engine SPEC.json`)."""
    out = Path(spec.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    spec_path = out / "spec.json"
    spec_path.write_text(json.dumps(asdict(spec)))
    package_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(package_root), env.get("PYTHONPATH")]))
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "stress_test.engine", str(spec_path)],
            cwd=out,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return RunResult(False, f"the run took longer than {timeout:.0f} s")
    result_path = out / "result.json"
    if not result_path.exists():
        tail = (proc.stderr or proc.stdout or "")[-2000:]
        return RunResult(False, f"the run exited {proc.returncode} without a result: {tail}")
    data = json.loads(result_path.read_text())
    return RunResult(
        ok=data["ok"],
        error=data["error"],
        equity=[tuple(x) for x in data["equity"]],
        positions=[tuple(x) for x in data["positions"]],
        seconds=data["seconds"],
    )


def write_bars(frame: pd.DataFrame, path: Path) -> str:
    cols = ["date", "symbol", "open", "high", "low", "close", "volume", "instrument_id"]
    frame.loc[:, cols].to_csv(path, index=False)
    return str(path)


def _main(argv: Iterable[str]) -> int:
    spec_path = Path(next(iter(argv)))
    spec = RunSpec(**json.loads(spec_path.read_text()))
    result = run_spec(spec)
    (Path(spec.out_dir) / "result.json").write_text(json.dumps(asdict(result)))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
