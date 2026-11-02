"""Tests for latency_arb_backtest look-ahead fixes (no outcome oracle, end-labelled VWAP)."""

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def lab():
    spec = importlib.util.spec_from_file_location("latency_arb_backtest", ROOT / "scripts/backtest/latency_arb_backtest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _series(vals, start="2025-01-01T00:00:00Z", freq="10min"):
    idx = pd.date_range(start, periods=len(vals), freq=freq, tz="UTC")
    return pd.Series(vals, index=idx)


class TestNoOracle:
    def test_crossing_signature_has_no_resolution(self, lab):
        import inspect

        assert "resolution" not in inspect.signature(lab.find_crossings).parameters

    def test_detects_both_directions_from_price_alone(self, lab):
        s = _series([0.70, 0.88, 0.80, 0.30, 0.12, 0.20])
        cr = lab.find_crossings(s)
        dirs = [c[2] for c in cr]
        assert dirs == ["YES", "NO"]

    def test_same_signals_regardless_of_outcome(self, lab):
        s = _series([0.70, 0.88, 0.95, 0.99])
        a = lab.build_signals_for_market("c", "Politics", s, resolution="YES", stop_hours=4)
        b = lab.build_signals_for_market("c", "Politics", s, resolution="NO", stop_hours=4)
        assert [x["entry_dt"] for x in a] == [x["entry_dt"] for x in b]
        assert [x["direction"] for x in a] == [x["direction"] for x in b] == ["YES"]

    def test_wrong_way_signal_loses_at_resolution(self, lab):
        # price spikes to 0.88 then market resolves NO; end of data -> settle at 0
        s = _series([0.70, 0.88, 0.86, 0.85])
        sig = lab.build_signals_for_market("c", "Politics", s, resolution="NO", stop_hours=100)
        assert sig[0]["exit_reason"] == "resolution"
        assert sig[0]["exit_price"] == pytest.approx(0.0)


class TestVwapLabel:
    def test_bucket_labelled_at_end(self, lab, tmp_path, monkeypatch):
        t0 = pd.Timestamp("2025-01-01T00:00:00Z")
        ts = [t0 + pd.Timedelta(minutes=m) for m in (1, 2, 9, 11, 12, 13)]
        df = pd.DataFrame(
            {
                "conditionId": ["c"] * 6,
                "price": [0.5, 0.5, 0.6, 0.7, 0.7, 0.7],
                "size": [1, 1, 2, 1, 1, 1],
                "timestamp": [int(x.timestamp()) for x in ts],
                "outcomeIndex": [0] * 6,
            }
        )
        d = tmp_path / "data" / "poly_cat" / "Politics"
        d.mkdir(parents=True)
        df.to_parquet(d / "trades.parquet")
        monkeypatch.setattr(lab, "ROOT", tmp_path)
        monkeypatch.setattr(lab, "MIN_VWAP_POINTS", 1, raising=False)
        out = lab.load_vwap_series("Politics", {"c"})
        v = out["c"]
        # first bucket [00:00, 00:10) is only complete at 00:10
        assert v.index[0] == t0 + pd.Timedelta(minutes=10)
        assert v.iloc[0] == pytest.approx((0.5 + 0.5 + 1.2) / 4)
        assert v.index[1] == t0 + pd.Timedelta(minutes=20)
