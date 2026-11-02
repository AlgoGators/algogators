"""Decode Polymarket CLOB ``OrderFilled`` logs (eth_getLogs) into HF-2026 ``trades.parquet`` rows.

Used by ``scripts/data/polymarket_extend.py tail`` to extend the tape past the last HF release with free public
Polygon RPC data. The output has the 17 columns of the HF 2026-10-05 ``trades.parquet`` (one row per maker fill;
the taker order's aggregate row, whose ``taker`` is the exchange contract, is dropped exactly as HF does).

Event layouts (verified by matching decoded logs against HF rows on (transaction_hash, log_index)):

* V1 ``OrderFilled(bytes32 indexed orderHash, address indexed maker, address indexed taker, uint256 makerAssetId,
  uint256 takerAssetId, uint256 makerAmountFilled, uint256 takerAmountFilled, uint256 fee)``, topic0 ``V1_TOPIC``.
* V2 (exchanges live since 2026-04-28), topic0 ``V2_TOPIC``, same three indexed fields; data words
  ``[side, tokenId, makerAmountFilled, takerAmountFilled, fee, builder, metadata]`` where ``side`` 0 = the maker
  BUYS ``tokenId`` with USDC (V1 ``makerAssetId = 0``) and 1 = the maker SELLS it (V1 ``takerAssetId = 0``).

USDC and outcome tokens both have 6 decimals.
"""
from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

V1_TOPIC = "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6"
V2_TOPIC = "0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee"
CONTRACTS = {
    "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e": "CTF_EXCHANGE",
    "0xc5d563a36ae78145c45a50134d48a1215220f80a": "NEGRISK_CTF_EXCHANGE",
    "0xe111180000d2663c0091e4f400237545b87b996b": "CTF_EXCHANGE_V2",
    "0xe2222d279d744050d28e00520010520000310f59": "NEGRISK_CTF_EXCHANGE_V2",
}
HF_TRADE_COLUMNS = ["timestamp", "block_number", "transaction_hash", "log_index", "contract", "market_id",
                    "condition_id", "event_id", "maker", "taker", "price", "usd_amount", "token_amount",
                    "maker_direction", "taker_direction", "nonusdc_side", "asset_id"]


def _addr(topic: str) -> str:
    return "0x" + topic[-40:].lower()


def decode_logs(logs: Iterable[dict]) -> pd.DataFrame:
    """Raw eth_getLogs dicts -> one row per OrderFilled (V1 or V2) with amounts as Python ints.

    Columns: timestamp, block_number, transaction_hash, log_index, contract, order_hash, maker, taker,
    maker_asset_id, taker_asset_id, maker_amount_filled, taker_amount_filled, fee. Requires ``blockTimestamp``
    on each log (returned by current Bor nodes); other events are ignored.
    """
    rows = []
    for lg in logs:
        if lg.get("removed"):
            continue
        t = lg["topics"]
        t0 = t[0].lower()
        if t0 not in (V1_TOPIC, V2_TOPIC):
            continue
        d = lg["data"][2:]
        w = [int(d[i:i + 64], 16) for i in range(0, len(d), 64)]
        if t0 == V1_TOPIC:
            mk_id, tk_id, mk_amt, tk_amt, fee = w[0], w[1], w[2], w[3], w[4]
        else:
            side, tok, mk_amt, tk_amt, fee = w[0], w[1], w[2], w[3], w[4]
            if side == 0:
                mk_id, tk_id = 0, tok
            elif side == 1:
                mk_id, tk_id = tok, 0
            else:
                raise ValueError(f"unexpected V2 side {side} in {lg['transactionHash']}:{lg['logIndex']}")
        rows.append({
            "timestamp": int(lg["blockTimestamp"], 16),
            "block_number": int(lg["blockNumber"], 16),
            "transaction_hash": lg["transactionHash"].lower(),
            "log_index": int(lg["logIndex"], 16),
            "contract": CONTRACTS.get(lg["address"].lower(), lg["address"].lower()),
            "order_hash": t[1].lower(),
            "maker": _addr(t[2]),
            "taker": _addr(t[3]),
            "maker_asset_id": str(mk_id),
            "taker_asset_id": str(tk_id),
            "maker_amount_filled": mk_amt,
            "taker_amount_filled": tk_amt,
            "fee": fee,
        })
    cols = ["timestamp", "block_number", "transaction_hash", "log_index", "contract", "order_hash", "maker", "taker",
            "maker_asset_id", "taker_asset_id", "maker_amount_filled", "taker_amount_filled", "fee"]
    return pd.DataFrame(rows, columns=cols)


def orderfilled_to_trades(of: pd.DataFrame, markets: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Decoded OrderFilled rows -> (HF-2026 trades rows, rows whose token is unknown to ``markets``).

    ``markets`` needs columns token_id, market_id, condition_id, event_id, nonusdc_side ('token1'/'token2').
    Taker-order aggregate rows (taker = an exchange contract) are dropped, as in HF ``trades.parquet``.
    """
    exch = set(CONTRACTS)
    of = of[~of["taker"].str.lower().isin(exch)]
    mk0 = of["maker_asset_id"].eq("0")
    asset = np.where(mk0, of["taker_asset_id"], of["maker_asset_id"])
    usdc_raw = np.where(mk0, of["maker_amount_filled"], of["taker_amount_filled"]).astype("float64")
    tok_raw = np.where(mk0, of["taker_amount_filled"], of["maker_amount_filled"]).astype("float64")
    with np.errstate(divide="ignore", invalid="ignore"):
        price = np.where(tok_raw > 0, usdc_raw / tok_raw, 0.0)
    out = pd.DataFrame({
        "timestamp": of["timestamp"].astype("uint64").to_numpy(),
        "block_number": of["block_number"].astype("uint64").to_numpy(),
        "transaction_hash": of["transaction_hash"].to_numpy(),
        "log_index": of["log_index"].astype("uint32").to_numpy(),
        "contract": of["contract"].to_numpy(),
        "maker": of["maker"].str.lower().to_numpy(),
        "taker": of["taker"].str.lower().to_numpy(),
        "price": np.round(price, 6),
        "usd_amount": np.round(usdc_raw / 1e6, 2),
        "token_amount": np.round(tok_raw / 1e6, 2),
        "maker_direction": np.where(mk0, "BUY", "SELL"),
        "taker_direction": np.where(mk0, "SELL", "BUY"),
        "asset_id": asset.astype(str),
    })
    m = markets.drop_duplicates("token_id").set_index("token_id")
    meta = m.reindex(out["asset_id"])
    for c in ("market_id", "condition_id", "event_id", "nonusdc_side"):
        out[c] = meta[c].to_numpy()
    known = out["market_id"].notna().to_numpy()
    return out.loc[known, HF_TRADE_COLUMNS].reset_index(drop=True), out.loc[~known].reset_index(drop=True)


def markets_token_frame(markets: pd.DataFrame) -> pd.DataFrame:
    """HF ``markets.parquet`` (id, condition_id, token1, token2, event_id) -> token_id-keyed lookup frame."""
    base = markets[["id", "condition_id", "token1", "token2", "event_id"]].astype(str)
    a = pd.DataFrame({"token_id": base.token1, "market_id": base.id, "condition_id": base.condition_id,
                      "event_id": base.event_id, "nonusdc_side": "token1"})
    b = pd.DataFrame({"token_id": base.token2, "market_id": base.id, "condition_id": base.condition_id,
                      "event_id": base.event_id, "nonusdc_side": "token2"})
    fr = pd.concat([a, b], ignore_index=True)
    return fr[fr.token_id.str.len() > 0]


def gamma_markets_to_hf(rows: list[dict]) -> pd.DataFrame:
    """Gamma ``/markets`` JSON rows -> HF markets columns needed by the tape (id, condition_id, token1/2, ...)."""
    from .polymarket_tokens import parse_list_field

    out = []
    for r in rows:
        toks = parse_list_field(r.get("clobTokenIds"))
        outs = parse_list_field(r.get("outcomes"))
        if len(toks) != 2:
            continue
        ev = (r.get("events") or [{}])[0]
        out.append({
            "id": str(r.get("id")), "question": r.get("question"), "slug": r.get("slug"),
            "condition_id": r.get("conditionId"), "token1": toks[0], "token2": toks[1],
            "answer1": outs[0] if len(outs) == 2 else "outcome0", "answer2": outs[1] if len(outs) == 2 else "outcome1",
            "neg_risk": bool(r.get("negRisk")), "event_id": str(ev.get("id", "")), "event_slug": ev.get("slug"),
            "event_title": ev.get("title"), "closed": bool(r.get("closed")), "active": bool(r.get("active")),
            "outcome_prices": r.get("outcomePrices"), "end_date": r.get("endDate"), "created_at": r.get("createdAt"),
        })
    return pd.DataFrame(out)


def first_block_at_or_after(ts: int, get_block_ts, lo: int, hi: int) -> int:
    """Binary search for the first block with timestamp >= ``ts`` in ``[lo, hi]`` (``get_block_ts(n) -> int``)."""
    while lo < hi:
        mid = (lo + hi) // 2
        if get_block_ts(mid) >= ts:
            hi = mid
        else:
            lo = mid + 1
    return lo
