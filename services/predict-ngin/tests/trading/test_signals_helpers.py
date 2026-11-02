import json
from pathlib import Path

import pandas as pd
import pytest

from trading.signals import (
    Signal,
    SignalConfig,
    SignalContext,
    _history_to_frame,
    _price_at_or_before,
    build_features,
    iter_polymarket_clob_markets,
    mean_reversion_rule,
    passes_base_filters,
    signals_to_dataframe,
)


def test_history_to_frame_normalizes_and_sorts() -> None:
    history = [
        {"t": 2, "p": 0.52},
        {"t": 1, "p": 0.50},
        {"t": 3, "p": None},
    ]

    df = _history_to_frame(history)

    assert list(df["timestamp"]) == [1, 2]
    assert list(df["price"]) == [0.50, 0.52]


def test_price_at_or_before_handles_bounds() -> None:
    df = pd.DataFrame({"timestamp": [10, 20, 30], "price": [0.1, 0.2, 0.3]})

    assert _price_at_or_before(df, 20) == 0.2
    assert _price_at_or_before(df, 25) == 0.2
    assert _price_at_or_before(df, 5) is None
    assert _price_at_or_before(pd.DataFrame(columns=["timestamp", "price"]), 10) is None


def test_build_features_with_history_and_end_date() -> None:
    as_of = pd.Timestamp("2026-01-05T00:00:00Z")
    base_ts = int(as_of.timestamp())
    history = pd.DataFrame(
        {
            "timestamp": [base_ts - 6 * 3600, base_ts - 3600, base_ts],
            "price": [0.50, 0.40, 0.45],
        }
    )
    context = SignalContext(
        market_info={
            "id": "m1",
            "slug": "m1",
            "question": "Test?",
            "volume24hr": 500,
            "liquidity": 300,
            "volume": 1000,
            "endDate": "2026-01-10T00:00:00Z",
        },
        history=history,
        as_of=as_of,
    )

    features = build_features(context, SignalConfig(feature_window_hours=24))

    assert features["price"] == pytest.approx(0.45)
    assert features["history_points"] == 3
    assert features["return_1h"] == pytest.approx(0.125)
    assert features["return_6h"] == pytest.approx(-0.1)
    assert features["return_24h"] is None
    assert features["range_24h"] == pytest.approx(0.10)
    assert features["days_to_close"] == pytest.approx(5.0)


def test_build_features_without_history_returns_metadata_only() -> None:
    context = SignalContext(
        market_info={
            "id": "m1",
            "slug": "m1",
            "question": "Test?",
            "volume24hr": 500,
            "liquidity": 300,
            "volume": 1000,
        },
        history=None,
        as_of=pd.Timestamp("2026-01-05T00:00:00Z"),
    )

    features = build_features(context, SignalConfig())

    assert "price" not in features
    assert features["volume24hr"] == 500
    assert features["liquidity"] == 300


def test_passes_base_filters_respects_thresholds() -> None:
    config = SignalConfig(
        min_volume24hr=100,
        min_liquidity=200,
        min_history_points=3,
        min_price=0.1,
        max_price=0.9,
    )

    good_features = {
        "volume24hr": 500,
        "liquidity": 300,
        "history_points": 3,
        "price": 0.45,
    }
    assert passes_base_filters(good_features, config) is True

    assert passes_base_filters({**good_features, "price": None}, config) is False
    assert passes_base_filters({**good_features, "volume24hr": 50}, config) is False
    assert passes_base_filters({**good_features, "liquidity": 10}, config) is False
    assert passes_base_filters({**good_features, "history_points": 1}, config) is False
    assert passes_base_filters({**good_features, "price": 0.99}, config) is False


def test_mean_reversion_rule_selects_side() -> None:
    context = SignalContext(
        market_info={"id": "m1", "slug": "m1", "question": "Q?"},
        history=None,
        as_of=pd.Timestamp("2026-01-05T00:00:00Z"),
    )
    config = SignalConfig(shock_return_1h=0.05, stabilize_return_6h=0.2)

    features = {"return_1h": -0.12, "return_6h": 0.02}
    signal = mean_reversion_rule(context, features, config)

    assert signal is not None
    assert signal.side == "YES"
    assert signal.score == pytest.approx(0.12)

    features_no = {"return_1h": 0.11, "return_6h": 0.02}
    signal_no = mean_reversion_rule(context, features_no, config)

    assert signal_no is not None
    assert signal_no.side == "NO"


def test_mean_reversion_rule_filters_out_weak_signals() -> None:
    context = SignalContext(
        market_info={"id": "m1", "slug": "m1", "question": "Q?"},
        history=None,
        as_of=pd.Timestamp("2026-01-05T00:00:00Z"),
    )
    config = SignalConfig(shock_return_1h=0.05, stabilize_return_6h=0.2)

    assert mean_reversion_rule(context, {"return_1h": 0.01, "return_6h": 0.0}, config) is None
    assert mean_reversion_rule(context, {"return_1h": 0.2, "return_6h": 0.3}, config) is None


def test_signals_to_dataframe_expands_features() -> None:
    signals = [
        Signal(
            market_id="m1",
            slug="m1",
            question="Q1",
            side="YES",
            score=0.1,
            as_of=pd.Timestamp("2026-01-01T00:00:00Z"),
            features={"volume24hr": 500, "price": 0.45},
            rule="rule1",
        ),
        Signal(
            market_id="m2",
            slug="m2",
            question="Q2",
            side="NO",
            score=0.2,
            as_of=pd.Timestamp("2026-01-01T01:00:00Z"),
            features={"volume24hr": 1000, "price": 0.55},
            rule="rule2",
        ),
    ]

    df = signals_to_dataframe(signals)

    assert len(df) == 2
    assert "feat_volume24hr" in df.columns
    assert "feat_price" in df.columns
    assert set(df["market_id"]) == {"m1", "m2"}


def test_iter_polymarket_clob_markets_reads_json(tmp_path: Path) -> None:
    payload = {
        "market_info": {"id": "m1", "slug": "m1", "question": "Q?"},
        "price_history": {
            "YES": {
                "history": [
                    {"t": 10, "p": 0.60},
                    {"t": 20, "p": 0.65},
                ]
            }
        },
    }

    path = tmp_path / "market_001.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    contexts = list(iter_polymarket_clob_markets(data_dir=str(tmp_path), use_db=False))

    assert len(contexts) == 1
    context = contexts[0]
    assert context.market_info["id"] == "m1"
    assert list(context.history["price"]) == [0.60, 0.65]
    assert context.as_of == pd.Timestamp(20, unit="s", tz="UTC")
