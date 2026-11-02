"""Unit tests for src/data_quality/polymarket_tokens.py (pmfix: YES/NO token normalisation)."""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import pytest

from src.data_quality.polymarket_tokens import (
    EXCHANGE_ADDRESSES,
    LegacyPolymarketReaderWarning,
    MixedTokenSeriesError,
    TokenMap,
    assert_single_token_series,
    detect_schema,
    flip_side,
    mixing_stats,
    normalize_trades,
    parse_list_field,
    to_yes,
    warn_legacy_reader,
    yes_price_frame,
)

CTF = "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e"


@pytest.fixture()
def tm() -> TokenMap:
    meta = pd.DataFrame(
        {
            "id": ["100", "200", "201", "300"],
            "condition_id": ["0xa", "0xb", "0xc", "0xd"],
            "token1": ["Y1", "Y2", "Y3", "UP4"],
            "token2": ["N1", "N2", "N3", "DN4"],
            "answer1": ["Yes", "Yes", "Yes", "Up"],
            "answer2": ["No", "No", "No", "Down"],
            "neg_risk": [False, True, True, False],
        }
    )
    return TokenMap.from_markets(meta)


# ------------------------------------------------------------------ parsing / primitives


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('["1", "2"]', ["1", "2"]),
        ("['1', '2']", ["1", "2"]),
        ('[""""1"""", """"2""""]', ["1", "2"]),
        (["1", "2"], ["1", "2"]),
        (None, []),
        (float("nan"), []),
        ("", []),
    ],
)
def test_parse_list_field(raw, expected):
    assert parse_list_field(raw) == expected


def test_flip_side_and_to_yes():
    assert flip_side("BUY") == "SELL" and flip_side("sell") == "buy" and flip_side("X") == "X"
    yes, side = to_yes([0.3, 0.3, 0.3], [0, 1, np.nan], ["BUY", "BUY", "BUY"])
    assert yes[0] == pytest.approx(0.3) and yes[1] == pytest.approx(0.7) and np.isnan(yes[2])
    assert list(side) == ["BUY", "SELL", None]


# ------------------------------------------------------------------ token map


def test_token_map_schemas_agree(tm):
    gamma = pd.DataFrame({"id": ["100"], "conditionId": ["0xa"], "clobTokenIds": ['["Y1", "N1"]'],
                          "outcomes": ['["Yes", "No"]'], "negRisk": ["False"]})
    uni = pd.DataFrame({"market_id": ["100"], "condition_id": ["0xa"], "yes_token": ["Y1"], "no_token": ["N1"]})
    for other in (TokenMap.from_markets(gamma), TokenMap.from_markets(uni)):
        merged = tm.merge(other)
        assert merged.lookup(["Y1"]).outcome_index.iloc[0] == 0
        assert merged.lookup(["N1"]).outcome_index.iloc[0] == 1
        assert len(merged.conflicts) == 0
    assert tm.yes_token("0xa") == "Y1"
    mt = tm.market_tokens().set_index("condition_id")
    assert mt.loc["0xd", "outcome0_label"] == "Up" and mt.loc["0xd", "token1"] == "DN4"


def test_token_map_conflict_dropped(tm):
    swapped = pd.DataFrame({"id": ["100"], "conditionId": ["0xa"], "clobTokenIds": ['["N1", "Y1"]'],
                            "outcomes": ['["No", "Yes"]']})
    merged = tm.merge(TokenMap.from_markets(swapped))
    assert merged.lookup(["Y1"]).outcome_index.isna().all()
    assert set(merged.conflicts.token_id) == {"Y1", "N1"}


def test_token_map_skips_non_binary():
    g = pd.DataFrame({"id": ["1", "2"], "conditionId": ["0x1", "0x2"],
                      "clobTokenIds": ['["a", "b", "c"]', '["d", "e"]'], "outcomes": ['["x","y","z"]', '["Yes","No"]']})
    m = TokenMap.from_markets(g)
    assert len(m) == 2 and m.skipped["not_two_tokens"] == 1


# ------------------------------------------------------------------ schema handling


def test_data_api_ticks(tm):
    df = pd.DataFrame({
        "conditionId": ["0xa"] * 4, "asset": ["Y1", "N1", "N1", "Y1"], "outcomeIndex": [0.0, 1.0, 1.0, 0.0],
        "outcome": ["Yes", "No", "No", "Yes"], "side": ["BUY", "BUY", "SELL", "SELL"],
        "price": [0.20, 0.79, 0.81, 0.21], "timestamp": [1, 2, 3, 4],
    })
    assert detect_schema(df) == "data_api"
    n = normalize_trades(df, tm)
    np.testing.assert_allclose(n.yes_price, [0.20, 0.21, 0.19, 0.21])
    assert list(n.yes_side) == ["BUY", "SELL", "BUY", "SELL"]
    assert (n.token_source == "token_id").all() and not n.token_conflict.any()
    # raw series jumps > 0.5 twice; the YES series never does
    assert mixing_stats(n, "conditionId", "timestamp", "price", "outcome_index")["n_jumps"] == 2
    assert mixing_stats(n, "conditionId", "timestamp", "yes_price")["n_jumps"] == 0


def test_data_api_conflict_flagged(tm):
    df = pd.DataFrame({"conditionId": ["0xa"], "asset": ["N1"], "outcomeIndex": [0.0], "side": ["BUY"],
                       "price": [0.7]})
    n = normalize_trades(df, tm)
    assert n.token_conflict.iloc[0] and n.outcome_index.iloc[0] == 1 and n.yes_price.iloc[0] == pytest.approx(0.3)


def test_data_api_without_token_map_uses_outcome_index():
    df = pd.DataFrame({"conditionId": ["0xa", "0xa"], "asset": ["Y1", "N1"], "outcomeIndex": [0, 1],
                       "side": ["BUY", "BUY"], "price": [0.4, 0.6]})
    n = normalize_trades(df)
    np.testing.assert_allclose(n.yes_price, [0.4, 0.4])
    assert list(n.yes_side) == ["BUY", "SELL"]


def _onchain_rows():
    # 1) plain maker row: maker BUYs YES (gives USDC), taker SELLs YES -> aggressor SELL YES @0.40
    # 2) MINT match maker row: maker BUYs NO @0.60 (gives USDC for token2), taker order is BUY YES @0.40
    # 3) the taker order's aggregate row (taker = exchange): maker field = taker trader BUYing YES @0.40
    return pd.DataFrame({
        "market_id": ["100"] * 3, "block_number": [10, 11, 11],
        "maker": ["0xm1", "0xm2", "0xt"], "taker": ["0xt0", "0xt", CTF],
        "nonusdc_side": ["token1", "token2", "token1"],
        "maker_asset": ["USDC", "USDC", "USDC"], "taker_asset": ["token1", "token2", "token1"],
        "maker_direction": ["BUY", "BUY", "BUY"], "taker_direction": ["SELL", "SELL", "SELL"],
        "price": [0.40, 0.60, 0.40], "asset_id": ["Y1", "N1", "Y1"],
    })


def test_onchain_processed(tm):
    df = _onchain_rows()
    assert detect_schema(df) == "onchain"
    n = normalize_trades(df, tm)
    np.testing.assert_allclose(n.yes_price, [0.40, 0.40, 0.40])
    assert list(n.yes_side) == ["SELL", "BUY", "BUY"]
    assert list(n.is_taker_order_row) == [False, False, True]
    assert (n.token_source == "token_id").all()


def test_onchain_nonusdc_side_disagrees_with_metadata(tm):
    df = _onchain_rows().iloc[[0]].assign(asset_id="N1")  # file says token1 but the id is the NO token
    n = normalize_trades(df, tm)
    assert n.token_conflict.iloc[0] and n.yes_price.iloc[0] == pytest.approx(0.60)


def test_quant_is_not_flipped_twice(tm):
    # quant.parquet stores row 2 of _onchain_rows as: nonusdc_side token1, price 0.40, maker SELL (YES terms),
    # but asset_id still the NO token and maker/taker_asset still say token2.
    q = pd.DataFrame({
        "market_id": ["100", "100"], "maker": ["0xm1", "0xm2"], "taker": ["0xt0", "0xt"],
        "nonusdc_side": ["token1", "token1"], "maker_asset": ["USDC", "USDC"], "taker_asset": ["token1", "token2"],
        "maker_direction": ["BUY", "SELL"], "taker_direction": ["SELL", "BUY"], "price": [0.40, 0.40],
        "asset_id": ["Y1", "N1"],
    })
    assert detect_schema(q) == "quant"
    n = normalize_trades(q, tm)
    np.testing.assert_allclose(n.yes_price, [0.40, 0.40])
    assert list(n.yes_side) == ["SELL", "BUY"]
    assert list(n.outcome_index) == [0.0, 1.0]


def test_onchain_raw_orderfilled(tm):
    o = pd.DataFrame({
        "maker": ["0xm2", "0xt"], "taker": ["0xt", CTF],
        "maker_asset_id": ["0", "0"], "taker_asset_id": ["N1", "Y1"],
        "maker_amount_filled": [60_000_000, 40_000_000], "taker_amount_filled": [100_000_000, 100_000_000],
    })
    assert detect_schema(o) == "onchain_raw"
    n = normalize_trades(o, tm)
    np.testing.assert_allclose(n.price_raw_onchain, [0.60, 0.40])
    np.testing.assert_allclose(n.yes_price, [0.40, 0.40])
    assert list(n.yes_side) == ["BUY", "BUY"]


def test_mixed_generations_research_file(tm):
    df = pd.DataFrame({
        "timestamp": [0, 5], "market_id": ["100", "100"], "conditionId": [None, "0xa"],
        "nonusdc_side": ["token2", None], "maker_asset": ["USDC", None], "taker_asset": ["token2", None],
        "maker_direction": ["BUY", None], "taker_direction": ["SELL", None], "taker": ["0xt", None],
        "price": [0.7, 0.7], "outcomeIndex": [np.nan, 1.0], "asset": [None, "N1"], "side": [None, "BUY"],
    })
    assert detect_schema(df) == "mixed_generations"
    n = normalize_trades(df, tm)
    np.testing.assert_allclose(n.yes_price, [0.3, 0.3])
    assert list(n.yes_side) == ["BUY", "SELL"]


def test_price_history_labels(tm):
    p = pd.DataFrame({"market_id": ["100", "100", "300", "300", "999"], "outcome": ["YES", "NO", "Up", "Down", "Maybe"],
                      "timestamp": [1, 1, 1, 1, 1], "price": [0.2, 0.8, 0.55, 0.45, 0.5]})
    assert detect_schema(p) == "price_history"
    n = normalize_trades(p, tm)
    np.testing.assert_allclose(n.yes_price.iloc[:4], [0.2, 0.2, 0.55, 0.55])
    assert np.isnan(n.yes_price.iloc[4])  # unknown label is never guessed
    f = yes_price_frame(p, tm)
    assert len(f) == 4 and "price_raw" in f


def test_token_series_vendor(tm):
    t = pd.DataFrame({"symbol": ["Y2", "N2", "N3"], "side": ["buy", "sell", "buy"], "price": [0.1, 0.9, 0.95]})
    n = normalize_trades(t, tm, schema="token_series")
    np.testing.assert_allclose(n.yes_price, [0.1, 0.1, 0.05])
    assert list(n.yes_side) == ["BUY", "BUY", "SELL"]


def test_neg_risk_event_never_complemented_across_markets(tm):
    # Markets 200 and 201 are two candidates of one neg-risk event. Their YES prices must stay separate
    # (0.10 and 0.85) - not 1 - p of each other - and their series are grouped per condition.
    df = pd.DataFrame({
        "conditionId": ["0xb", "0xc", "0xb", "0xc"], "asset": ["Y2", "Y3", "N2", "N3"],
        "outcomeIndex": [0, 0, 1, 1], "side": ["BUY"] * 4, "price": [0.10, 0.85, 0.90, 0.15],
        "timestamp": [1, 2, 3, 4], "eventSlug": ["e"] * 4,
    })
    n = normalize_trades(df, tm)
    np.testing.assert_allclose(n.yes_price, [0.10, 0.85, 0.10, 0.85])
    assert n.yes_price.round(9).groupby(n.conditionId).nunique().eq(1).all()
    # grouping the event as one series would fabricate jumps; per-condition grouping has none
    assert mixing_stats(n, "eventSlug", "timestamp", "yes_price")["n_jumps"] == 3
    assert mixing_stats(n, "conditionId", "timestamp", "yes_price")["n_jumps"] == 0


def test_unknown_token_gives_nan_not_guess(tm):
    df = pd.DataFrame({"conditionId": ["0xz"], "asset": ["ZZ"], "side": ["BUY"], "price": [0.3]})
    n = normalize_trades(df, tm)
    assert np.isnan(n.yes_price.iloc[0]) and n.yes_side.iloc[0] is None


# ------------------------------------------------------------------ guards


def test_assert_single_token_series():
    df = pd.DataFrame({"m": ["a", "a", "b"], "oi": [0, 1, 0]})
    with pytest.raises(MixedTokenSeriesError):
        assert_single_token_series(df, "m", "oi", where="t")
    with pytest.warns(LegacyPolymarketReaderWarning):
        assert_single_token_series(df, "m", "oi", where="t", action="warn")
    assert_single_token_series(df[df.m == "b"], "m", "oi")


def test_warn_legacy_reader(monkeypatch):
    monkeypatch.delenv("PMFIX_STRICT", raising=False)
    with pytest.warns(LegacyPolymarketReaderWarning):
        warn_legacy_reader("x")
    monkeypatch.setenv("PMFIX_STRICT", "1")
    with pytest.raises(MixedTokenSeriesError):
        warn_legacy_reader("x")


def test_exchange_addresses_lowercase():
    assert all(a == a.lower() for a in EXCHANGE_ADDRESSES)


def test_dune_market_trades_taker_rows_dropped():
    # One mint match: taker BUYs YES @0.08, maker BUYs NO @0.92. Dune's maker row is right (NO, maker BUY @0.92);
    # its taker row is mislabelled (claims a YES print @0.92). Only maker rows may be used.
    d = pd.DataFrame({"condition_id": ["0xa", "0xa"], "outcome_index": [1, 0], "price": [0.92, 0.92],
                      "is_taker_side": [False, True], "maker_side": ["BUY", "SELL"]})
    assert detect_schema(d) == "dune_market_trades"
    n = normalize_trades(d)
    assert n.yes_price.iloc[0] == pytest.approx(0.08) and n.yes_side.iloc[0] == "BUY"
    assert np.isnan(n.yes_price.iloc[1]) and n.token_source.iloc[1] == "dune_taker_row_dropped"
    k = normalize_trades(d, dune_taker_rows="keep")
    assert k.yes_price.iloc[1] == pytest.approx(0.92)  # the fake print the default protects against
