"""
Confirmed-whale signal pipeline.

Applies a sequential filter chain to raw trades:

  1. Size         : usd_amount >= min_size_usd            (default $10 K)
  2. Liquidity    : market volume >= min_market_volume     (default $500 K)
  3. Category     : optional allowlist (e.g. politics/geo)
  4. Wallet WR    : whale_winrates[trader] >= min_wallet_wr (default 60 %)
  5. Price impact : usd_amount / market_volume <= max_price_impact (default 5 %)
  6. Confirmation : >= min_confirmations distinct whales trade
                    same market + same direction within confirmation_window_hours

A signal fires on the trade that brings the distinct-whale count to
min_confirmations.  A per-(market, direction) cooldown prevents duplicate
signals for the same setup.
"""

from typing import Dict, List, Optional, Set
import pandas as pd
import numpy as np


def extract_confirmed_signals(
    trades_df: pd.DataFrame,
    whale_set: Set[str],
    whale_winrates: Dict[str, float],
    market_liquidity: Dict[str, float],
    allowed_categories: Optional[List[str]] = None,
    min_size_usd: float = 10_000,
    min_market_volume: float = 500_000,
    min_wallet_wr: float = 0.60,
    max_price_impact: float = 0.05,
    min_confirmations: int = 2,
    confirmation_window_hours: int = 24,
    cooldown_hours: int = 168,
    trader_col: str = "maker",
) -> pd.DataFrame:
    """
    Run the full filter pipeline and return confirmed signals.

    Parameters
    ----------
    trades_df            : all raw trades (already loaded & normalised)
    whale_set            : addresses that cleared the whale quality bar
    whale_winrates       : address -> historical win rate
    market_liquidity     : market_id -> volume/liquidity in USD
    allowed_categories   : if given, only these categories pass (case-insensitive)
    min_size_usd         : step 1 — minimum single-trade size
    min_market_volume    : step 2 — minimum market volume
    min_wallet_wr        : step 4 — minimum wallet historical win-rate
    max_price_impact     : step 5 — max fraction of market volume per trade
    min_confirmations    : step 6 — distinct whales needed to confirm
    confirmation_window_hours : look-back window for co-directional whales
    cooldown_hours       : silence repeated signals for same (market, direction)
    trader_col           : column name for the active trader ("maker")

    Returns
    -------
    DataFrame with one row per confirmed signal and columns:
      datetime, market_id, category, direction, price,
      confirming_whales, whale_addresses,
      market_liquidity_usd, price_impact_pct, trigger_usd
    """
    if trades_df.empty:
        return pd.DataFrame()

    direction_col = f"{trader_col}_direction"

    # ── Steps 1-5: vectorised pre-filter ────────────────────────────────────
    df = trades_df.copy()
    # Use non-underscore names so itertuples namedtuple fields stay valid
    df["xmid"] = (
        df["market_id"].astype(str).str.strip().str.replace(".0", "", regex=False)
    )

    # 1. Size
    mask = df["usd_amount"] >= min_size_usd

    # 2. Whale set
    if whale_set:
        mask &= df[trader_col].isin(whale_set)

    # 3. Category
    if allowed_categories:
        allowed_lower = {c.lower() for c in allowed_categories}
        mask &= df["category"].str.lower().isin(allowed_lower)

    # 4. Market liquidity
    df["xliq"] = df["xmid"].map(market_liquidity).fillna(0.0)
    mask &= df["xliq"] >= min_market_volume

    # 5a. Wallet win rate
    df["xwr"] = df[trader_col].map(whale_winrates).fillna(0.0)
    mask &= df["xwr"] >= min_wallet_wr

    # 5b. Price impact
    safe_liq = df["xliq"].replace(0, np.nan)
    df["ximpact"] = df["usd_amount"] / safe_liq
    mask &= df["ximpact"] <= max_price_impact

    filtered = df[mask].sort_values("datetime").reset_index(drop=True)
    if filtered.empty:
        return pd.DataFrame()

    # ── Step 6: sequential confirmation scan ────────────────────────────────
    window_td   = pd.Timedelta(hours=confirmation_window_hours)
    cooldown_td = pd.Timedelta(hours=cooldown_hours)

    # (market_id, direction) -> list[(datetime, trader)]
    active: dict = {}
    last_signal: dict = {}   # (market_id, direction) -> datetime of last emitted signal

    confirmed: list = []

    for row in filtered.itertuples(index=False):
        mid       = row.xmid
        direction = (
            str(getattr(row, direction_col, "buy")).lower()
            if pd.notna(getattr(row, direction_col, None)) else "buy"
        )
        key    = (mid, direction)
        t      = row.datetime
        trader = getattr(row, trader_col)

        # Enforce cooldown
        if key in last_signal and (t - last_signal[key]) < cooldown_td:
            active.setdefault(key, []).append((t, trader))
            continue

        # Prune stale entries from active window
        cutoff = t - window_td
        existing = [(ts, tr) for ts, tr in active.get(key, []) if ts >= cutoff]
        active[key] = existing

        # Distinct whales in window, excluding current trader
        prior_whales = {tr for _, tr in existing if tr != trader}

        if len(prior_whales) >= (min_confirmations - 1):
            all_whales = prior_whales | {trader}
            confirmed.append({
                "datetime":             t,
                "market_id":            mid,
                "category":             getattr(row, "category", "Unknown"),
                "direction":            direction,
                "price":                float(row.price),
                "trigger_usd":          float(row.usd_amount),
                "confirming_whales":    len(all_whales),
                "whale_addresses":      ",".join(sorted(all_whales)),
                "market_liquidity_usd": float(row.xliq),
                "price_impact_pct":     round(float(row.ximpact) * 100, 3),
            })
            last_signal[key] = t
            # Reset the active window so next confirmation starts fresh
            active[key] = [(t, trader)]
        else:
            active[key].append((t, trader))

    if not confirmed:
        return pd.DataFrame()

    return pd.DataFrame(confirmed).sort_values("datetime").reset_index(drop=True)
