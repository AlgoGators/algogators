"""Implied vol pass that fills options_data.eod (#32 O5).

Runs one trading day at a time. For each contract:

1. Price = mid of the closing bid/ask (16:00:00 ET, or 15:59:00 if 16:00 is missing;
   13:00 / 12:59 on early close days). The caller picks the snapshot and passes its
   time in as quote_ts. No price if the quote is missing, zero or crossed.
2. Forward per (root, expiration), from the call/put pair whose prices are closest:
   F = K + e^(rT) (C - P).
3. IV and greeks with Black-76 on that forward, but only for OTM contracts (calls
   K >= F, puts K < F) with 7 to 365 days to expiry. ETF and stock options are
   American, but we still use the European formula. That's fine OTM, which is why
   we skip ITM.

Every contract still gets a row, and iv_status says why when there's no vol. Same
input always gives the same output, so re-running a day that's already loaded
changes nothing.

No database code in here. The O2 loader passes in the day's quotes (joined to
options_data.contracts) and upserts whatever comes back.
"""

from datetime import date
from enum import StrEnum

import numpy as np
import pandas as pd

from data_ngin.domain.options_pricing import (
    IvStatus,
    atm_vol,
    black76_greeks,
    forward_from_parity,
    implied_vol,
    vol_at_tenor,
)

METHOD_VERSION = "o5-v1"
EXCHANGE_TZ = "America/New_York"
MIN_DAYS_TO_EXPIRY = 7
MAX_DAYS_TO_EXPIRY = 365
SECONDS_PER_YEAR = 365.0 * 24 * 60 * 60

REQUIRED_COLUMNS = (
    "contract_id",
    "root",
    "expiration",  # full expiry timestamp (tz-aware), not just the date
    "strike",
    "call_put",  # "C" or "P"
    "bid",
    "ask",
    "quote_ts",  # when the bid/ask snapshot was taken (tz-aware)
)

EOD_COLUMNS = (
    "contract_id",
    "date",
    "price_used",
    "forward",
    "rate",
    "days_to_expiry",
    "years_to_expiry",
    "implied_vol",
    "delta",
    "gamma",
    "vega",
    "theta",
    "iv_status",
    "method_version",
)


class QuoteStatus(StrEnum):
    """Reasons we skip a contract before solving. Solver failures are in IvStatus."""

    NO_QUOTE = "no_quote"  # bid or ask missing, zero or negative
    CROSSED_QUOTE = "crossed_quote"  # ask < bid
    NO_FORWARD = "no_forward"  # this expiry has no strike with both a call and put price
    DTE_OUT_OF_RANGE = "dte_out_of_range"
    IN_THE_MONEY = "itm"


def compute_eod(quotes: pd.DataFrame, rate: float, trade_date: date) -> pd.DataFrame:
    """Build one day's options_data.eod rows.

    quotes: one row per contract, with REQUIRED_COLUMNS.
    rate: that day's continuous rate (convert DTB3 with discount_yield_to_continuous).
    Returns EOD_COLUMNS, one row per contract, sorted by contract_id.
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in quotes.columns]
    if missing:
        raise ValueError(f"quotes is missing columns: {missing}")
    df = (
        quotes.loc[:, list(REQUIRED_COLUMNS)]
        .sort_values("contract_id", kind="mergesort")
        .reset_index(drop=True)
    )
    if df["contract_id"].duplicated().any():
        raise ValueError("quotes must hold one row per contract_id")
    if not df["call_put"].isin(["C", "P"]).all():
        raise ValueError("call_put must be 'C' or 'P'")

    is_call = df["call_put"].eq("C").to_numpy()
    strike = df["strike"].to_numpy(dtype=np.float64)
    bid = df["bid"].to_numpy(dtype=np.float64)
    ask = df["ask"].to_numpy(dtype=np.float64)
    with np.errstate(invalid="ignore"):
        has_quote = (bid > 0) & (ask > 0)
        crossed = has_quote & (ask < bid)
    priced = has_quote & ~crossed
    df["mid"] = np.where(priced, 0.5 * (bid + ask), np.nan)

    years = (df["expiration"] - df["quote_ts"]).dt.total_seconds().to_numpy() / SECONDS_PER_YEAR
    df["years"] = years
    expiry_day = df["expiration"].dt.tz_convert(EXCHANGE_TZ).dt.normalize().dt.tz_localize(None)
    days_to_expiry = (expiry_day - pd.Timestamp(trade_date)).dt.days.to_numpy()

    forward = _forwards(df, rate)

    status = np.full(len(df), IvStatus.OK.value, dtype=object)
    status[~has_quote] = QuoteStatus.NO_QUOTE.value
    status[crossed] = QuoteStatus.CROSSED_QUOTE.value
    pending = priced.copy()
    with np.errstate(invalid="ignore"):
        otm = np.where(is_call, strike >= forward, strike < forward)
    for mask, reason in (
        (~np.isfinite(forward), QuoteStatus.NO_FORWARD),
        (
            (days_to_expiry < MIN_DAYS_TO_EXPIRY) | (days_to_expiry > MAX_DAYS_TO_EXPIRY),
            QuoteStatus.DTE_OUT_OF_RANGE,
        ),
        (~otm, QuoteStatus.IN_THE_MONEY),
    ):
        hit = pending & mask
        status[hit] = reason.value
        pending &= ~hit

    vols = np.full(len(df), np.nan)
    greeks = {name: np.full(len(df), np.nan) for name in ("delta", "gamma", "vega", "theta")}
    idx = np.flatnonzero(pending)
    if idx.size:
        solved, solver_status = implied_vol(
            df["mid"].to_numpy()[idx], forward[idx], strike[idx], years[idx], rate, is_call[idx]
        )
        vols[idx] = solved
        status[idx] = solver_status
        ok = idx[solver_status == IvStatus.OK.value]
        g = black76_greeks(forward[ok], strike[ok], years[ok], rate, vols[ok], is_call[ok])
        for name in greeks:
            greeks[name][ok] = getattr(g, name)

    return pd.DataFrame(
        {
            "contract_id": df["contract_id"],
            "date": trade_date,
            "price_used": df["mid"],
            "forward": forward,
            "rate": rate,
            "days_to_expiry": days_to_expiry,
            "years_to_expiry": years,
            "implied_vol": vols,
            **greeks,
            "iv_status": status,
            "method_version": METHOD_VERSION,
        },
        columns=list(EOD_COLUMNS),
    )


def _forwards(df: pd.DataFrame, rate: float) -> np.ndarray:
    """Parity forward for each row's (root, expiration). NaN if we can't get one."""
    forward = np.full(len(df), np.nan)
    for idx in df.groupby(["root", "expiration"], sort=True).indices.values():
        group = df.iloc[idx]
        legs = group.dropna(subset=["mid"]).drop_duplicates(["call_put", "strike"])
        calls = legs.loc[legs["call_put"] == "C", ["strike", "mid"]]
        puts = legs.loc[legs["call_put"] == "P", ["strike", "mid"]]
        pairs = calls.merge(puts, on="strike", suffixes=("_call", "_put"))
        if pairs.empty:
            continue
        # Some rows might be from the 15:59 snapshot instead of 16:00. One minute
        # doesn't matter for e^(rT), so just use the first row's T.
        forward[idx] = forward_from_parity(
            pairs["strike"], pairs["mid_call"], pairs["mid_put"], group["years"].iloc[0], rate
        )
    return forward


def atm_vol_by_underlying(
    quotes: pd.DataFrame, eod: pd.DataFrame, tenor_days: int = 30
) -> pd.DataFrame:
    """ATM vol at a fixed tenor (30 days by default) for each underlying.

    For each (root, expiration) we take the ATM vol off the smile, then interpolate
    across expiries to tenor_days in total variance. All roots of an underlying go in
    together, e.g. SPX and SPXW. quotes needs contract_id, underlying, root,
    expiration and strike.
    """
    merged = quotes.loc[:, ["contract_id", "underlying", "root", "expiration", "strike"]].merge(
        eod.loc[:, ["contract_id", "forward", "years_to_expiry", "implied_vol", "iv_status"]],
        on="contract_id",
    )
    solved = merged[merged["iv_status"] == IvStatus.OK.value]
    rows = []
    for underlying, by_underlying in solved.groupby("underlying", sort=True):
        terms = []
        for _, expiry in by_underlying.groupby(["root", "expiration"], sort=True):
            vol = atm_vol(expiry["forward"].iloc[0], expiry["strike"], expiry["implied_vol"])
            if np.isfinite(vol):
                terms.append((expiry["years_to_expiry"].iloc[0], vol))
        years = [t for t, _ in terms]
        vols = [v for _, v in terms]
        rows.append(
            {
                "underlying": underlying,
                "expiries": len(terms),
                f"atm_vol_{tenor_days}d": vol_at_tenor(years, vols, tenor_days / 365.0),
            }
        )
    return pd.DataFrame(rows, columns=["underlying", "expiries", f"atm_vol_{tenor_days}d"])
