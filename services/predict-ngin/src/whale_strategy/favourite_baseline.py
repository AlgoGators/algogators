"""
Naive "buy the favourite" baseline for the whale-following strategy.

Uses exactly the same entry price gates as the whale strategy
(``max_entry_yes_price`` and the per-category ``price_gates_for``), but no whale
information: any trade print in the test window whose YES price passes the gate is
an eligible decision time. Direction is the favourite side (BUY YES if YES >= min_buy,
SELL YES = buy NO if YES <= max_sell). The resulting ``WhaleSignal`` objects are fed
through the same simulator (same fills, latency, fees, sizing) as the whale signals.
"""

from __future__ import annotations

from typing import Iterable, List, Optional

import numpy as np
import pandas as pd

from .whale_config import WhaleConfig
from .whale_following_strategy import WhaleSignal

BASELINE_ADDRESS = "BASELINE"


def eligible_favourite_prints(
    prints_df: pd.DataFrame,
    cfg: WhaleConfig,
    min_print_usd: float = 0.0,
    allowed_markets: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """Prints passing the whale strategy's price gates, with a ``_side`` column."""
    df = prints_df
    if allowed_markets is not None:
        df = df[df["market_id"].astype(str).isin(set(map(str, allowed_markets)))]
    if min_print_usd > 0:
        df = df[df["usd_amount"] >= min_print_usd]
    if df.empty:
        return df.assign(_side=pd.Series(dtype=str))
    price = df["price"].astype(float)
    cats = df["category"].astype(str) if "category" in df.columns else pd.Series("Unknown", index=df.index)
    gates = {c: cfg.price_gates_for(c) for c in cats.unique()}
    min_buy = cats.map(lambda c: gates[c][0]).astype(float)
    max_sell = cats.map(lambda c: gates[c][1]).astype(float)
    ok_price = price <= cfg.max_entry_yes_price
    buy = ok_price & (price >= min_buy)
    sell = ok_price & (price <= max_sell) & ~buy
    out = df[buy | sell].copy()
    out["_side"] = np.where(buy[buy | sell], "BUY", "SELL")
    return out


def build_favourite_baseline_signals(
    prints_df: pd.DataFrame,
    cfg: WhaleConfig,
    rng: np.random.Generator,
    one_per_market: bool = True,
    min_print_usd: float = 0.0,
    allowed_markets: Optional[Iterable[str]] = None,
) -> List[WhaleSignal]:
    """
    Favourite-buying signals without whale information.

    one_per_market=True: one uniformly random eligible decision time per market.
    one_per_market=False: every eligible print is a signal.
    """
    elig = eligible_favourite_prints(prints_df, cfg, min_print_usd, allowed_markets)
    if elig.empty:
        return []
    if one_per_market:
        keys = elig["market_id"].astype(str).to_numpy()
        order = rng.permutation(len(elig))
        _, first = np.unique(keys[order], return_index=True)
        elig = elig.iloc[np.sort(order[first])]
    elig = elig.sort_values("datetime", kind="mergesort")
    sigs = []
    for mid, cat, side, px, usd, dt in zip(
        elig["market_id"].astype(str), elig.get("category", pd.Series("Unknown", index=elig.index)),
        elig["_side"], elig["price"].astype(float), elig["usd_amount"].astype(float), elig["datetime"],
    ):
        sigs.append(WhaleSignal(
            market_id=mid, category=cat, whale_address=BASELINE_ADDRESS, side=side,
            price=px, size_usd=usd, score=10.0, datetime=pd.Timestamp(dt),
            historical_winrate=px if side == "BUY" else 1.0 - px,
            whale_token_side="YES" if side == "BUY" else "NO", signal_trade_size_usd=usd,
        ))
    return sigs
