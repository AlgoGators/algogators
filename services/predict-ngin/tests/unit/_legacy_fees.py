"""Frozen copies of the fee code that predict_ngin.costs replaced (step 5.1, 2026-10-15).

Verbatim from the files named in each section, as of commit a0bfade. The golden tests compare
these against the canonical module and the shims; do not edit them. The only change is in
``feeaudit_build_schedule``, which takes the two JSON payloads as arguments instead of reading them
from data/vendor/feeaudit/raw.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- src/strategies_legal/kalshi_flb.py
FLB_TAKER_RATE = 0.07
FLB_MAKER_RATE = 0.0175
FLB_MAKER_FEE_START = pd.Timestamp("2025-04-01", tz="UTC").value // 10**9


def flb_ceil_cent(x):
    return np.ceil(np.round(np.asarray(x, dtype=float) * 100.0, 6)) / 100.0


def flb_taker_fee(contracts, price, rate: float = FLB_TAKER_RATE, multiplier=1.0):
    c = np.asarray(contracts, dtype=float)
    p = np.asarray(price, dtype=float)
    return flb_ceil_cent(rate * c * p * (1.0 - p)) * np.asarray(multiplier, dtype=float)


def flb_maker_fee(contracts, price, applies, rate: float = FLB_MAKER_RATE, multiplier=1.0):
    c = np.asarray(contracts, dtype=float)
    p = np.asarray(price, dtype=float)
    f = flb_ceil_cent(rate * c * p * (1.0 - p)) * np.asarray(multiplier, dtype=float)
    return np.where(np.asarray(applies, dtype=bool), f, 0.0)


def flb_maker_fee_applies(fee_type, ts_unix, mode: str = "series", start: int = FLB_MAKER_FEE_START):
    ts = np.asarray(ts_unix)
    if mode == "none":
        return np.zeros(ts.shape, dtype=bool)
    if mode == "all":
        return np.ones(ts.shape, dtype=bool)
    ft = pd.Series(np.asarray(fee_type, dtype=object)).fillna("").astype(str).str.contains("maker").to_numpy()
    return ft & (ts >= start)


# ---------------------------------------------------------------- scripts/research/feeaudit_common.py
FA_T0 = pd.Timestamp("2021-01-01", tz="UTC")
FA_MAKER_START = pd.Timestamp("2025-04-01", tz="UTC")
FA_DIRECT_PREC_TS = int(pd.Timestamp("2026-05-28", tz="UTC").value // 10**9)
FA_TAKER, FA_MAKER = 0.07, 0.0175


def _half_family(s: str) -> bool:
    return s.startswith("KXINX") or s.startswith("INX") or s.startswith("KXNASDAQ100") or s.startswith("NASDAQ100")


def feeaudit_build_schedule(sa, ch, extra_series=()) -> pd.DataFrame:
    cur = {s["ticker"]: (s.get("fee_type") or "quadratic", float(s.get("fee_multiplier") if s.get("fee_multiplier") is not None else 1.0)) for s in sa}
    chg = pd.DataFrame(ch)
    chg["effective_from"] = pd.to_datetime(chg.scheduled_ts, utc=True, format="ISO8601")
    rows = []
    logged = set(chg.series_ticker)
    for s in sorted(set(cur) | set(extra_series)):
        ft, fm = cur.get(s, ("quadratic", 1.0))
        if s in logged:
            g = chg[chg.series_ticker == s].sort_values("effective_from")
            rows.append((s, FA_T0, "quadratic", 0.5 if _half_family(s) else 1.0,
                         "fee_schedule_pdf_2026-02-05" if _half_family(s) else "assumed_pre_log"))
            for r in g.itertuples():
                rows.append((s, r.effective_from, r.fee_type, float(r.fee_multiplier), "api_fee_change"))
        else:
            src = "api_series_current" if s in cur else "missing_default"
            if "maker" in ft:
                rows.append((s, FA_T0, "quadratic", fm, "assumed_pre_maker"))
                rows.append((s, FA_MAKER_START, ft, fm, "assumed_maker_start_2025-04-01"))
            else:
                rows.append((s, FA_T0, ft, fm, src))
    d = pd.DataFrame(rows, columns=["series", "effective_from", "fee_type", "multiplier", "source"])
    last = d.sort_values("effective_from").groupby("series").tail(1).set_index("series")
    bad = [(s, tuple(last.loc[s, ["fee_type", "multiplier"]]), cur[s]) for s in logged if s in cur
           and (last.loc[s, "fee_type"], float(last.loc[s, "multiplier"])) != cur[s]]
    d.attrs["inconsistent"] = bad
    return d


def feeaudit_pit(o: pd.DataFrame, sched: pd.DataFrame, maker_start=None, maker_series=None) -> pd.DataFrame:
    s = sched.copy()
    s["eff"] = (s.effective_from.astype("int64") // 10**9) if str(s.effective_from.dtype).startswith("datetime64[ns") \
        else s.effective_from.map(lambda t: int(pd.Timestamp(t).timestamp()))
    if maker_start is not None:
        m = s.source.str.startswith("assumed_maker_start")
        if maker_series is not None:
            m &= s.series.isin(maker_series)
        s.loc[m, "eff"] = int(maker_start)
    s = s.sort_values("eff")
    x = o[["series", "ts"]].copy()
    x["_i"] = np.arange(len(x)); x["ts"] = x.ts.astype("int64")
    x = x.sort_values("ts")
    y = pd.merge_asof(x, s[["series", "eff", "fee_type", "multiplier"]].rename(columns={"eff": "ts_eff"}),
                      left_on="ts", right_on="ts_eff", by="series", direction="backward").sort_values("_i")
    o = o.copy()
    o["pit_fee_type"] = y.fee_type.fillna("quadratic").to_numpy()
    o["pit_mult"] = y.multiplier.fillna(1.0).to_numpy()
    return o


def feeaudit_fee(qn, px, maker: bool, rate_applies: bool, mult: float, rounding: str, ts: int) -> tuple[float, float]:
    if maker and not rate_applies:
        return 0.0, 0.0
    ex = (FA_MAKER if maker else FA_TAKER) * mult * qn * px * (1.0 - px)
    if rounding == "exact" or (rounding == "regime" and ts >= FA_DIRECT_PREC_TS):
        return ex, ex
    return math.ceil(round(ex * 100.0, 6)) / 100.0, ex


# ---------------------------------------------------------------- src/trading/arbitrage/fees.py
ARB_KALSHI_TAKER_COEF = 0.07
ARB_KALSHI_MAKER_COEF = 0.0175
ARB_POLY_CATEGORY_RATES: Dict[str, float] = {
    "crypto": 0.07, "sports": 0.05, "politics": 0.04, "finance": 0.04, "tech": 0.04, "technology": 0.04,
    "economics": 0.05, "economy": 0.05, "econ": 0.05, "culture": 0.05, "weather": 0.05, "other": 0.05,
    "geopolitics": 0.0,
}
ARB_POLY_DEFAULT_RATE = 0.05


def arb_ceil_cents(x: float) -> float:
    if x <= 0:
        return 0.0
    cents = x * 100.0
    return math.ceil(cents - 1e-9) / 100.0


def arb_kalshi_fee(contracts, price, maker=False, taker_coef=ARB_KALSHI_TAKER_COEF, maker_coef=ARB_KALSHI_MAKER_COEF):
    if contracts <= 0:
        return 0.0
    p = min(max(float(price), 0.0), 1.0)
    coef = maker_coef if maker else taker_coef
    return arb_ceil_cents(coef * contracts * p * (1.0 - p))


def arb_polymarket_fee_rate(category, rates=None, default_rate=ARB_POLY_DEFAULT_RATE) -> float:
    table = ARB_POLY_CATEGORY_RATES if rates is None else rates
    key = (category or "other").strip().lower()
    return float(table.get(key, default_rate))


def arb_polymarket_fee(contracts, price, category=None, maker=False, rates=None, default_rate=ARB_POLY_DEFAULT_RATE):
    if maker or contracts <= 0:
        return 0.0
    p = min(max(float(price), 0.0), 1.0)
    return contracts * arb_polymarket_fee_rate(category, rates, default_rate) * p * (1.0 - p)


@dataclass
class ArbFeeModel:
    kalshi_taker_coef: float = ARB_KALSHI_TAKER_COEF
    kalshi_maker_coef: float = ARB_KALSHI_MAKER_COEF
    poly_fees_enabled: bool = True
    poly_rates: Dict[str, float] = field(default_factory=lambda: dict(ARB_POLY_CATEGORY_RATES))
    poly_default_rate: float = ARB_POLY_DEFAULT_RATE

    def kalshi(self, contracts, price, maker=False):
        return arb_kalshi_fee(contracts, price, maker, self.kalshi_taker_coef, self.kalshi_maker_coef)

    def poly(self, contracts, price, category: Optional[str] = None, maker=False):
        if not self.poly_fees_enabled:
            return 0.0
        return arb_polymarket_fee(contracts, price, category, maker, self.poly_rates, self.poly_default_rate)

    def kalshi_per_contract(self, price, maker=False):
        coef = self.kalshi_maker_coef if maker else self.kalshi_taker_coef
        return coef * price * (1.0 - price)

    def poly_per_contract(self, price, category=None, maker=False):
        return self.poly(1.0, price, category, maker)


# ---------------------------------------------------------------- scripts/research/sharp_common.py
def sharp_taker_fee(p, c=1.0, mult=1.0):
    return np.ceil(np.round(0.07 * mult * c * p * (1 - p) * 100, 6)) / 100


# ---------------------------------------------------------------- scripts/research/capbook_forward.py
CAPFWD_MAKER_FEE_START = 1743465600


def capfwd_fee_c(price, fee_type, fee_mode, ts):
    p = float(price)
    if fee_mode == "taker":
        return math.ceil(round(0.07 * 100 * p * (1 - p) * 100, 6)) / 100
    if "maker" in str(fee_type) and ts >= CAPFWD_MAKER_FEE_START:
        return math.ceil(round(0.0175 * 100 * p * (1 - p) * 100, 6)) / 100
    return 0.0


def capfwd_kalshi_fee(qn, px, taker: bool, maker_applies: bool, mult: float) -> tuple[float, float]:
    if not taker and not maker_applies:
        return 0.0, 0.0
    ex = (0.07 if taker else 0.0175) * float(mult) * qn * px * (1.0 - px)
    return math.ceil(round(ex * 100.0, 6)) / 100.0, ex


# ---------------------------------------------------------------- scripts/research/wxlive_paper_eval.py
def wxlive_fee(n: int, p: float) -> float:
    return math.ceil(round(0.07 * n * p * (1 - p) * 100, 6)) / 100


# ---------------------------------------------------------------- Polymarket per-share helpers
def polyx_pm_taker_fee(p, rate: float = 0.05):          # scripts/research/polyx_common.py
    return rate * p * (1.0 - p)


def alpha_pm_fee_pc(p, rate: float):                    # scripts/research/alpha_decay_common.py
    p = np.clip(np.asarray(p, dtype=float), 0.0, 1.0)
    return rate * p * (1.0 - p)


def whale_taker_fee_per_share(price: float, rate: float) -> float:   # src/whale_strategy/execution_model.py
    p = float(price)
    return float(rate) * p * (1.0 - p)


# ---------------------------------------------------------------- scripts/live/perps_carry_paper.py
PERPS_TAKER = 0.0012
PERPS_MAKER = 0.0002


