"""pmfix: 30 random real prints (20 on-chain OrderFilled rows of data/polymarket/trades.parquet, 10 Data-API ticks)
checked by hand against the raw Polygon OrderFilled logs (tests/unit/fixtures/pmfix_hand_checked_fills.json).

For each print the fixture holds the processed row, the decoded raw log, the outcome index of the token from market
metadata (clobTokenIds order) and the YES-equivalent price / aggressor side worked out by hand from the raw amounts.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.data_quality.polymarket_tokens import TokenMap, normalize_trades

FX = json.loads((Path(__file__).parent / "fixtures" / "pmfix_hand_checked_fills.json").read_text())


def _tm(rows):
    fr = pd.DataFrame(rows, columns=["token_id", "outcome_index"]).drop_duplicates("token_id")
    fr = fr.assign(condition_id="c", market_id="m", outcome_label="", outcome0_label="", neg_risk=False,
                   complement_token_id="")
    return TokenMap._finalise(fr, {})


def test_fixture_has_30_prints():
    assert len(FX["onchain"]) == 20 and len(FX["data_api"]) == 10


@pytest.mark.parametrize("e", FX["onchain"], ids=lambda e: str(e["file_row_number"]))
def test_onchain_processed_and_raw_match_hand(e):
    p, raw = e["processed"], e["raw_orderfilled"]
    tm = _tm([(p["asset_id"], e["meta_outcome_index"])])
    # nonusdc_side label agrees with metadata
    assert {"token1": 0, "token2": 1}[p["nonusdc_side"]] == e["meta_outcome_index"]
    n = normalize_trades(pd.DataFrame([p]), tm, schema="onchain").iloc[0]
    assert n.yes_price == pytest.approx(e["hand_yes_price"], abs=1e-6)
    assert n.yes_side == e["hand_yes_side"]
    r = normalize_trades(pd.DataFrame([{
        "maker": raw["maker"], "taker": raw["taker"], "maker_asset_id": raw["makerAssetId"],
        "taker_asset_id": raw["takerAssetId"], "maker_amount_filled": raw["makerAmountFilled"],
        "taker_amount_filled": raw["takerAmountFilled"]}]), tm, schema="onchain_raw").iloc[0]
    assert r.price_raw_onchain == pytest.approx(p["price"], abs=1e-6)
    assert r.yes_price == pytest.approx(e["hand_yes_price"], abs=1e-6)
    assert r.yes_side == e["hand_yes_side"]


@pytest.mark.parametrize("e", FX["data_api"], ids=lambda e: e["category"] + e["tick"]["asset"][-6:])
def test_data_api_tick_matches_hand(e):
    t = e["tick"]
    assert int(t["outcomeIndex"]) == e["meta_outcome_index"]
    tm = _tm([(t["asset"], e["meta_outcome_index"])])
    n = normalize_trades(pd.DataFrame([t]), tm, schema="data_api").iloc[0]
    assert n.yes_price == pytest.approx(e["hand_yes_price"], abs=1e-6)
    assert n.yes_side == e["hand_yes_side"]
    # the tick's wallet is the taker order in the raw log (Data-API side = aggressor side)
    w = t["proxyWallet"].lower()
    agg = [x for x in e["raw_orderfilled"] if x["maker"].lower() == w and x["taker"].lower() in (
        "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e", "0xc5d563a36ae78145c45a50134d48a1215220f80a")]
    if agg:
        x = agg[0]
        pays_usdc = x["makerAssetId"] == "0"
        usdc = x["makerAmountFilled"] if pays_usdc else x["takerAmountFilled"]
        shares = x["takerAmountFilled"] if pays_usdc else x["makerAmountFilled"]
        assert usdc / shares == pytest.approx(t["price"], abs=1e-3)
        assert ("BUY" if pays_usdc else "SELL") == t["side"]
