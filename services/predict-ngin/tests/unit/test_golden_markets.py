"""Golden tests for step 5.5 (series/ticker parsing and settlement access -> predict_ngin.markets).

Each replaced parser is compared with its frozen copy (tests/unit/_legacy_markets.py) on a grid of
tickers, by exact equality. The settlement reader is compared frame-for-frame on synthetic frames
and, when the local data is present, on every saved series file.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts" / "research"))
sys.path.insert(0, str(ROOT / "scripts" / "live"))

import _legacy_markets as LG  # noqa: E402

from predict_ngin.markets import (  # noqa: E402
    Event,
    Market,
    event_of_market,
    iter_settlements,
    read_settled_markets,
    series_column,
    series_of,
    settled_frame,
    win_of,
)

TICKERS = ["KXHIGHNY-26OCT15-T70", "KXHIGHNY-26OCT15", "KXHIGHNY", "INXD-23DEC29-B4700", "", "-", "A-",
           "-B", "KX--X", "kxbtc15m-26oct151415-15", "PRES-2024-DJT", "KXMVE-X-Y-Z-W", " SP-ACE ", "über-1"]
ODD = [None, float("nan"), 12, 3.5]


def test_series_parsers_equal_their_frozen_copies():
    CB = pytest.importorskip("capbook_common")  # private module: skipped in a public clone
    import market_feature_watch as MFW

    from src.live.lip_pilot import selection as SEL
    K = pytest.importorskip("src.strategies_legal.kalshi_flb")  # private module: skipped in a public clone
    for t in TICKERS:
        assert SEL.series_of(t) == LG.lip_pilot_series_of(t) == series_of(t)
        assert K.series_from_event(t) == LG.kalshi_flb_series_from_event(t)
        assert CB.series_of(t) == LG.capbook_series_of(t)
        assert MFW.series_of(t) == LG.market_feature_watch_series_of(t)
    for t in ODD:  # non-strings: str() first (kalshi_flb, capbook); market_feature_watch maps falsy to ""
        assert K.series_from_event(t) == LG.kalshi_flb_series_from_event(t)
        assert CB.series_of(t) == LG.capbook_series_of(t)
    for t in (None, ""):
        assert MFW.series_of(t) == LG.market_feature_watch_series_of(t) == ""


def test_series_column_equals_lpland():
    s = pd.Series(TICKERS + ODD, index=np.arange(100, 100 + len(TICKERS) + len(ODD)))
    pd.testing.assert_series_equal(series_column(s), LG.lpland_series_of(s))
    assert list(series_column(TICKERS)) == [series_of(t) for t in TICKERS]


def test_event_and_domain():
    assert event_of_market("KXHIGHNY-26OCT15-T70") == "KXHIGHNY-26OCT15"
    assert event_of_market("KXHIGHNY") == "KXHIGHNY"
    m = Market.from_ticker("KXHIGHNY-26OCT15-T70")
    assert (m.event_ticker, m.series) == ("KXHIGHNY-26OCT15", "KXHIGHNY")
    assert Market.from_ticker("KXMVE-A-B", event_ticker="KXMVE-A").series == "KXMVE"
    assert Event.from_ticker("KXFED-26DEC").series == "KXFED"
    with pytest.raises(AttributeError):
        m.ticker = "x"  # frozen
    assert (win_of("yes"), win_of("no"), win_of("void"), win_of(None)) == (1.0, 0.0, None, None)


def _frame():
    return pd.DataFrame({
        "ticker": [f"KXRAIN-26OCT{i:02d}-T1" for i in range(8)],
        "event_ticker": [f"KXRAIN-26OCT{i:02d}" for i in range(8)],
        "close_time": ["2026-10-01T12:00:00Z", "2026-10-02T12:00:00.123Z", "2026-10-03T00:00:00+00:00",
                       "2026-10-04T23:59:59.999999Z", "2026-10-05T12:00:00Z", "2026-10-06T12:00:00Z",
                       "1969-12-31T23:59:59.5Z", "2026-10-08T12:00:00-04:00"],
        "settlement_ts": ["2026-10-01T13:00:00Z", None, "", "2026-10-04T23:59:59.999Z", "garbage",
                          "2026-10-06T13:00:00.5Z", "1970-01-01T00:00:00Z", np.nan],
        "result": ["yes", "no", "yes", "", None, "void", "no", "yes"],
    })


def test_settled_frame_equals_settle_common():
    got = settled_frame(_frame())
    exp = LG.settle_load_markets_frame(_frame())
    pd.testing.assert_frame_equal(got, exp, check_exact=True)
    assert list(got.result) == ["yes", "no", "yes", "no", "yes"]
    src = _frame()
    settled_frame(src)
    pd.testing.assert_frame_equal(src, _frame())  # input untouched


def test_settle_common_load_markets_is_the_shim(tmp_path, monkeypatch):
    SC = pytest.importorskip("settle_common")  # private module: skipped in a public clone
    (tmp_path / "markets").mkdir()
    _frame().to_parquet(tmp_path / "markets" / "KXRAIN.parquet")
    monkeypatch.setattr(SC, "OUT", tmp_path)
    pd.testing.assert_frame_equal(SC.load_markets("KXRAIN"),
                                  LG.settle_load_markets_frame(pd.read_parquet(tmp_path / "markets" / "KXRAIN.parquet")),
                                  check_exact=True)


def test_iter_settlements_typed():
    s = list(iter_settlements(settled_frame(_frame())))
    assert [x.win for x in s] == [1.0, 0.0, 1.0, 0.0, 1.0]
    assert s[0].settle_ts == int(pd.Timestamp("2026-10-01T13:00:00Z").value // 10**9)
    assert s[1].settle_ts is None and s[1].event_ticker == "KXRAIN-26OCT01"
    assert all(isinstance(x.close_ts, int) for x in s)


@pytest.mark.data
def test_settled_markets_on_saved_series():
    files = sorted((ROOT / "data" / "vendor" / "settle" / "markets").glob("*.parquet"))
    if not files:
        pytest.skip("data/vendor/settle/markets not present")
    for f in files:
        pd.testing.assert_frame_equal(read_settled_markets(f), LG.settle_load_markets_frame(pd.read_parquet(f)),
                                      check_exact=True)
