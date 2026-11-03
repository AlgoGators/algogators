"""A stand-in for the trade-ngin Python module: the surface stress_test.engine uses, no engine.

`write_fake_engine(folder, ids_and_fees=True, config_runs=True)` writes it as `tradengin.py` into
`folder`, so a test can point --engine (or TRADENGIN_DIR) at it, including from a subprocess.

Its runner starts flat, like the real engine. After FAKE_WARMUP bar dates it holds a "strategy"
book: portfolio.json's `fake_book` ({symbol: contracts}), and on the window's last bar date
`fake_last_day` replaces entries of it (an action of the strategy). It marks that book on the bars
it is given from portfolio.json's `initial_capital`, writes the engine's CSV export, and records
what it was handed in `handed.json` beside it. `ids_and_fees=False` is a module whose Bar and
FuturesSpec lack instrument_id and fee_per_contract (the published one); `config_runs=False` one
too old to run a book from a config directory.
"""

from __future__ import annotations

from pathlib import Path

SOURCE = r"""
import csv
import datetime as dt
import enum
import json
import os

IDS_AND_FEES = __IDS__
CONFIG_RUNS = __CONFIG__
FAKE_WARMUP = 5


class AssetClass(enum.Enum):
    FUTURES = 1


class DataFrequency(enum.Enum):
    DAILY = 1


class Bar:
    def __init__(self, timestamp=None, open=0.0, high=0.0, low=0.0, close=0.0, volume=0.0, symbol=""):
        self.timestamp, self.open, self.high, self.low = timestamp, open, high, low
        self.close, self.volume, self.symbol = close, volume, symbol
        if IDS_AND_FEES:
            self.instrument_id = ""


class FuturesSpec:
    def __init__(self):
        self.root_symbol = ""
        self.multiplier = 1.0
        if IDS_AND_FEES:
            self.fee_per_contract = None
            self.tick_value = 0.0


class FuturesInstrument:
    def __init__(self, root, spec):
        self.root, self.spec = root, spec


class InstrumentRegistry:
    _one = None

    def __init__(self):
        self.items = {}

    @classmethod
    def instance(cls):
        if cls._one is None:
            cls._one = cls()
        return cls._one

    def clear(self):
        self.items = {}

    def initialize_without_database(self):
        pass

    def register_instrument(self, root, instrument):
        self.items[root] = instrument


class MarketDataSource:
    def __init__(self):
        pass


class BacktestRunConfig:
    def __init__(self):
        self.symbols = []
        if CONFIG_RUNS:
            self.config_base_path = ""
            self.export_csv = False
            self.csv_output_path = ""


class BacktestRunner:
    def initialize_with_config(self, name, cfg):
        self.name, self.cfg = name, cfg

    def run_backtest(self):
        cfg = self.cfg
        book_file = os.path.join(cfg.config_base_path, "portfolios", self.name, "portfolio.json")
        for f in ("defaults.json", f"portfolios/{self.name}/risk.json", f"portfolios/{self.name}/email.json"):
            with open(os.path.join(cfg.config_base_path, f)) as fh:
                json.load(fh)
        with open(book_file) as fh:
            conf = json.load(fh)
        capital = float(conf["initial_capital"])
        strategy = {k: float(v) for k, v in conf.get("fake_book", {"ZN.v.0": -1.0}).items()}
        last_day = {k: float(v) for k, v in conf.get("fake_last_day", {}).items()}
        for s in list(strategy) + list(last_day):
            if s not in cfg.symbols:
                raise RuntimeError("no bars for " + s)
        bars = cfg.data_source.load_bars(cfg.symbols, cfg.start_date, cfg.end_date, cfg.asset_class, cfg.data_freq)
        reg = InstrumentRegistry.instance().items
        by_day = {}
        for b in bars:
            by_day.setdefault(b.timestamp, {})[b.symbol] = b
        days = sorted(by_day)
        out = os.path.join(cfg.csv_output_path, "STRESS")
        os.makedirs(out, exist_ok=True)
        value, prev = capital, {}
        eq_rows, pos_rows = [], []
        for i, t in enumerate(days):
            day = t.astimezone(dt.timezone.utc).date().isoformat()
            closes = {s: b.close for s, b in by_day[t].items()}
            if i >= FAKE_WARMUP:
                held = dict(strategy)
                if i == len(days) - 1:
                    held.update(last_day)
                for s, q in held.items():
                    if s in closes and s in prev:
                        value += q * (closes[s] - prev[s]) * reg[s.split(".")[0]].spec.multiplier
                eq_rows.append((day, value))
                pos_rows += [(day, "TREND_FOLLOWING", s, q) for s, q in held.items() if q]
            prev.update(closes)
        with open(os.path.join(out, "equity_curve.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "portfolio_value"])
            w.writerows(eq_rows)
        with open(os.path.join(out, "positions.csv"), "w", newline="") as f:
            f.write("# Portfolio Value: test\n")
            w = csv.writer(f)
            w.writerow(["date", "strategy", "symbol", "quantity"])
            w.writerows(pos_rows)
        handed = {
            "capital": capital, "days": len(days),
            "first": days[0].astimezone(dt.timezone.utc).date().isoformat(),
            "ids": sorted({getattr(b, "instrument_id", "") for b in bars}),
            "fees": {r: getattr(i.spec, "fee_per_contract", None) for r, i in reg.items()},
        }
        with open(os.path.join(out, "handed.json"), "w") as fh:
            json.dump(handed, fh)
        return object()
"""


def write_fake_engine(folder: Path, ids_and_fees: bool = True, config_runs: bool = True) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    source = SOURCE.replace("__IDS__", repr(ids_and_fees)).replace("__CONFIG__", repr(config_runs))
    (folder / "tradengin.py").write_text(source)
    return folder
