"""
Hold-to-resolution cross-platform arbitrage simulator (Kalshi <-> Polymarket).

At each observation for a matched pair we can buy YES on the cheap venue and
NO on the expensive venue. If both venues resolve the same way, exactly one
leg pays $1 per contract, so the locked capital (both legs + fees) is returned
as $1 per contract pair at resolution.

Accounting choices (all configurable on ``ResolutionArbConfig``):

* Executable prices: only last-trade / mid prices exist historically, so each
  leg pays ``last + half_spread`` (default 1c Kalshi, 0.75c Polymarket).
* Fees: per fill (Kalshi ceil-to-cent 0.07*C*P*(1-P); Polymarket category rate
  * C*p*(1-p)), charged at entry. No exit fills: positions settle.
* Size: capped per observation by ``volume_cap_frac`` of the contemporaneous
  (same-hour) USD volume on EACH venue, measured against that venue's leg
  notional; also by free cash and a per-pair capital cap.
* Capital lockup: cost is deducted at entry and returned (as payout) at
  ``resolve_ts + settle_lag_seconds``. Each leg settles on its own venue's
  outcome, so resolution mismatches show up as losses (or windfalls).
* Unresolved pairs are NOT dropped: they are marked at the last price of each
  leg and flagged ``status == 'open_marked'``.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .fees import FeeModel

DAY = 86400.0


@dataclass
class ResolutionArbConfig:
    total_capital: float = 5000.0
    max_capital_per_pair: float = 1000.0
    min_net_edge_per_contract: float = 0.005  # $ per contract pair after fees/spreads
    kalshi_half_spread: float = 0.01
    poly_half_spread: float = 0.0075
    volume_cap_frac: float = 0.05
    settle_lag_seconds: float = 0.0
    min_price: float = 0.02  # skip near-certain prints (stale/settling)
    max_price: float = 0.98
    min_lock_days: float = 0.0
    max_lock_days: Optional[float] = None
    min_trade_usd: float = 1.0
    kalshi_maker: bool = False
    fees: FeeModel = field(default_factory=FeeModel)


@dataclass
class ResolutionArbResult:
    trades: pd.DataFrame
    monthly: pd.DataFrame
    summary: Dict[str, Any]
    equity: pd.DataFrame


def executable_legs(k_price: float, pm_price: float, cfg: ResolutionArbConfig) -> Dict[str, Any]:
    """Pick the cheap side and return the executable per-contract fills (no fees)."""
    if pm_price >= k_price:
        # Kalshi YES is cheap: buy K YES at its ask, buy PM NO at its ask
        direction = "k_yes_pm_no"
        k_fill = k_price + cfg.kalshi_half_spread
        pm_fill = (1.0 - pm_price) + cfg.poly_half_spread
    else:
        direction = "pm_yes_k_no"
        k_fill = (1.0 - k_price) + cfg.kalshi_half_spread
        pm_fill = pm_price + cfg.poly_half_spread
    return {
        "direction": direction,
        "k_fill": float(k_fill),
        "pm_fill": float(pm_fill),
        "gross_cost": float(k_fill + pm_fill),
    }


def _cost_for(C: int, legs: Dict[str, Any], cfg: ResolutionArbConfig, category: str):
    k_fee = cfg.fees.kalshi(C, legs["k_fill"], maker=cfg.kalshi_maker)
    pm_fee = cfg.fees.poly(C, legs["pm_fill"], category)
    return C * legs["gross_cost"] + k_fee + pm_fee, k_fee, pm_fee


def _size(budget: float, legs: Dict[str, Any], cfg: ResolutionArbConfig, category: str) -> int:
    if budget <= 0:
        return 0
    per = legs["gross_cost"] + cfg.fees.kalshi_per_contract(legs["k_fill"], cfg.kalshi_maker) \
        + cfg.fees.poly_per_contract(legs["pm_fill"], category)
    C = int(math.floor(budget / per + 1e-9))
    while C > 0 and _cost_for(C, legs, cfg, category)[0] > budget + 1e-9:
        C -= 1
    return C


def _leg_values(direction: str, k_val: float, pm_val: float) -> float:
    """Value per contract pair given YES values (outcome 0/1 or mark price) on each venue."""
    if direction == "k_yes_pm_no":
        return k_val + (1.0 - pm_val)
    return (1.0 - k_val) + pm_val


def run_resolution_arb(
    obs: pd.DataFrame,
    pairs: pd.DataFrame,
    cfg: Optional[ResolutionArbConfig] = None,
) -> ResolutionArbResult:
    """Simulate hold-to-resolution arbitrage.

    obs columns: pair_id, ts, k_price, pm_price, k_usd_vol, pm_usd_vol
        (k_price/pm_price = YES prices on the same proposition, already
        polarity-aligned; *_usd_vol = USD traded on that venue in the hour)
    pairs columns: pair_id, category, resolve_ts, k_outcome, pm_outcome,
        k_last_price, pm_last_price  (outcomes 1/0, NaN if unresolved)
    """
    cfg = cfg or ResolutionArbConfig()
    P = pairs.set_index("pair_id")
    o = obs.sort_values(["ts", "pair_id"], kind="mergesort").reset_index(drop=True)

    cash = float(cfg.total_capital)
    locked = 0.0
    releases: List[tuple] = []  # (release_ts, seq, trade_idx)
    per_pair_locked: Dict[str, float] = {}
    trades: List[Dict[str, Any]] = []
    equity_rows: List[Dict[str, Any]] = []
    seq = 0

    def _release_until(t: float) -> None:
        nonlocal cash, locked
        while releases and releases[0][0] <= t:
            rts, _, idx = heapq.heappop(releases)
            tr = trades[idx]
            cash += tr["payout"]
            locked -= tr["cost"]
            per_pair_locked[tr["pair_id"]] -= tr["cost"]
            equity_rows.append({"ts": rts, "cash": cash, "locked": locked, "event": "release"})

    for row in o.itertuples(index=False):
        ts = float(row.ts)
        _release_until(ts)
        if row.pair_id not in P.index:
            continue
        pr = P.loc[row.pair_id]
        resolve_ts = float(pr["resolve_ts"])
        if not (ts < resolve_ts):
            continue
        lock_days = (resolve_ts + cfg.settle_lag_seconds - ts) / DAY
        if lock_days < cfg.min_lock_days:
            continue
        if cfg.max_lock_days is not None and lock_days > cfg.max_lock_days:
            continue
        kp, pp = float(row.k_price), float(row.pm_price)
        if not (cfg.min_price <= kp <= cfg.max_price and cfg.min_price <= pp <= cfg.max_price):
            continue
        category = str(pr.get("category", "other"))
        legs = executable_legs(kp, pp, cfg)
        # quick screen with un-rounded per-contract fees
        per_fee = cfg.fees.kalshi_per_contract(legs["k_fill"], cfg.kalshi_maker) + \
            cfg.fees.poly_per_contract(legs["pm_fill"], category)
        if 1.0 - legs["gross_cost"] - per_fee < cfg.min_net_edge_per_contract:
            continue

        # capacity: leg notional on each venue <= frac * that venue's hourly USD volume
        k_vol = float(row.k_usd_vol) if pd.notna(row.k_usd_vol) else 0.0
        pm_vol = float(row.pm_usd_vol) if pd.notna(row.pm_usd_vol) else 0.0
        cap_C = math.floor(min(cfg.volume_cap_frac * k_vol / legs["k_fill"],
                               cfg.volume_cap_frac * pm_vol / legs["pm_fill"]) + 1e-9)
        pair_room = cfg.max_capital_per_pair - per_pair_locked.get(row.pair_id, 0.0)
        budget = min(cash, pair_room)
        C = min(_size(budget, legs, cfg, category), cap_C)
        if C <= 0:
            continue
        cost, k_fee, pm_fee = _cost_for(C, legs, cfg, category)
        net_edge = (C * 1.0 - cost) / C
        if net_edge < cfg.min_net_edge_per_contract or cost < cfg.min_trade_usd:
            continue

        k_out, pm_out = pr["k_outcome"], pr["pm_outcome"]
        if pd.notna(k_out) and pd.notna(pm_out):
            payout = C * _leg_values(legs["direction"], float(k_out), float(pm_out))
            status = "settled"
            mismatch = float(k_out) != float(pm_out)
        else:
            k_mark = float(k_out) if pd.notna(k_out) else float(pr["k_last_price"])
            pm_mark = float(pm_out) if pd.notna(pm_out) else float(pr["pm_last_price"])
            payout = C * _leg_values(legs["direction"], k_mark, pm_mark)
            status = "open_marked"
            mismatch = False
        release_ts = resolve_ts + cfg.settle_lag_seconds
        trades.append({
            "pair_id": row.pair_id,
            "category": category,
            "entry_ts": int(ts),
            "release_ts": int(release_ts),
            "direction": legs["direction"],
            "k_price": kp,
            "pm_price": pp,
            "k_fill": legs["k_fill"],
            "pm_fill": legs["pm_fill"],
            "contracts": int(C),
            "k_fee": k_fee,
            "pm_fee": pm_fee,
            "cost": cost,
            "payout": payout,
            "pnl": payout - cost,
            "net_edge_per_contract": net_edge,
            "lock_days": (release_ts - ts) / DAY,
            "status": status,
            "outcome_mismatch": bool(mismatch),
            "k_usd_vol": k_vol,
            "pm_usd_vol": pm_vol,
        })
        cash -= cost
        locked += cost
        per_pair_locked[row.pair_id] = per_pair_locked.get(row.pair_id, 0.0) + cost
        heapq.heappush(releases, (release_ts, seq, len(trades) - 1))
        seq += 1
        equity_rows.append({"ts": ts, "cash": cash, "locked": locked, "event": "entry"})

    _release_until(float("inf"))

    T = pd.DataFrame(trades)
    equity = pd.DataFrame(equity_rows)
    monthly = _monthly(T)
    summary = _summary(T, cfg, equity)
    return ResolutionArbResult(trades=T, monthly=monthly, summary=summary, equity=equity)


def _month(ts: pd.Series) -> pd.Series:
    return pd.to_datetime(ts, unit="s", utc=True).dt.strftime("%Y-%m")


def _monthly(T: pd.DataFrame) -> pd.DataFrame:
    cols = ["month", "n_trades", "deployed", "net_pnl", "fees", "capital_days", "settled_pnl", "open_marked_pnl"]
    if T.empty:
        return pd.DataFrame(columns=cols)
    T = T.assign(month=_month(T["entry_ts"]), fees=T["k_fee"] + T["pm_fee"],
                 capital_days=T["cost"] * T["lock_days"])
    g = T.groupby("month")
    out = pd.DataFrame({
        "n_trades": g.size(),
        "pairs": g["pair_id"].nunique(),
        "deployed": g["cost"].sum(),
        "net_pnl": g["pnl"].sum(),
        "fees": g["fees"].sum(),
        "capital_days": g["capital_days"].sum(),
        "settled_pnl": g.apply(lambda d: d.loc[d.status == "settled", "pnl"].sum(), include_groups=False),
        "open_marked_pnl": g.apply(lambda d: d.loc[d.status != "settled", "pnl"].sum(), include_groups=False),
        "mismatch_pnl": g.apply(lambda d: d.loc[d.outcome_mismatch, "pnl"].sum(), include_groups=False),
    }).reset_index()
    out["ret_on_deployed"] = out["net_pnl"] / out["deployed"].where(out["deployed"] > 0)
    out["ann_ret_on_locked"] = out["net_pnl"] / (out["capital_days"] / 365.0).where(out["capital_days"] > 0)
    return out


def _summary(T: pd.DataFrame, cfg: ResolutionArbConfig, equity: pd.DataFrame) -> Dict[str, Any]:
    if T.empty:
        return {"n_trades": 0, "net_pnl": 0.0, "deployed": 0.0, "ann_return_on_locked": float("nan"),
                "avg_locked": 0.0, "max_locked": 0.0}
    cap_days = float((T["cost"] * T["lock_days"]).sum())
    span_days = max((T["release_ts"].max() - T["entry_ts"].min()) / DAY, 1e-9)
    max_locked = float(equity["locked"].max()) if not equity.empty else 0.0
    return {
        "n_trades": int(len(T)),
        "pairs_traded": int(T["pair_id"].nunique()),
        "net_pnl": float(T["pnl"].sum()),
        "deployed": float(T["cost"].sum()),
        "fees": float((T["k_fee"] + T["pm_fee"]).sum()),
        "settled_pnl": float(T.loc[T.status == "settled", "pnl"].sum()),
        "open_marked_pnl": float(T.loc[T.status != "settled", "pnl"].sum()),
        "n_open_marked": int((T.status != "settled").sum()),
        "mismatch_trades": int(T["outcome_mismatch"].sum()),
        "mismatch_pnl": float(T.loc[T.outcome_mismatch, "pnl"].sum()),
        "capital_days": cap_days,
        "ann_return_on_locked": float(T["pnl"].sum() / (cap_days / 365.0)) if cap_days > 0 else float("nan"),
        "avg_locked": cap_days / span_days,
        "max_locked": max_locked,
        "ann_return_on_total_capital": float(T["pnl"].sum() / cfg.total_capital * 365.0 / span_days),
        "span_days": span_days,
        "win_rate": float((T["pnl"] > 0).mean()),
    }
