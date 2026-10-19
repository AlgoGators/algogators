"""The deleted monthly Polymarket extract is served from trades.parquet to old readers (TradeStore, arb backtest)."""
from __future__ import annotations

import calendar
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]


def _trades(path: Path) -> None:
    ts = [calendar.timegm((2024, 11, 5, h, 0, 0)) for h in range(4)] + [calendar.timegm((2024, 12, 1, 0, 0, 0))]
    pd.DataFrame({
        "timestamp": ts, "market_id": ["7", "7", "", "8", "7"], "maker": ["0xM"] * 5, "taker": ["0xT"] * 5,
        "nonusdc_side": ["token1", "token2", "token1", "token1", "token1"],
        "maker_direction": ["sell"] * 5, "taker_direction": ["buy"] * 5,
        "price": [0.40, 0.70, 0.5, 0.2, 0.45], "usd_amount": [150.0, 50.0, 500.0, 0.0, 300.0],
        "token_amount": [375.0, 71.4, 1000.0, 0.0, 666.0], "transaction_hash": ["AA", "BB", "CC", "DD", "EE"],
    }).to_parquet(path / "trades.parquet", index=False)


def test_tradestore_serves_deleted_extract(tmp_path):
    _trades(tmp_path)
    sys.path.insert(0, str(ROOT / "src"))
    from trading.data_modules.parquet_store import TradeStore

    ts = TradeStore(str(tmp_path / "trades"))  # monthly folder absent
    assert ts.available() and ts.list_months() == ["2024-11", "2024-12"]
    raw = ts.load_trades(min_usd=10, start_date="2024-11-05", end_date="2024-11-05", yes_normalize=False)
    # zero-dollar and no-market rows dropped; end date includes its whole day; Dec row excluded
    assert raw["transactionHash"].tolist() == ["0xaa", "0xbb"]
    assert raw["timestamp"].iloc[0] == "2024-11-05 00:00:00"
    yes = ts.load_trades(min_usd=100, start_date="2024-11-01", end_date="2024-12-31")
    assert len(yes) == 2 and set(yes.market_id) == {"7"}


def test_hourly_volume_falls_back(tmp_path):
    _trades(tmp_path)
    from src.trading.arbitrage.pair_observations import extract_pm_hourly_volume

    out = tmp_path / "pv.parquet"
    extract_pm_hourly_volume(str(tmp_path / "trades" / "*.parquet"), ["7"], out)
    pv = pd.read_parquet(out)
    assert pv.usd.sum() == 500.0 and len(pv) == 3
