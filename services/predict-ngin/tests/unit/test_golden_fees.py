"""Golden equivalence tests for step 5.1 (fees -> predict_ngin.costs).

Every fee implementation that was replaced by a shim or repointed at predict_ngin.costs must give
bit-identical numbers to its frozen pre-change copy in ``_legacy_fees.py``, over a grid of prices
(0.01..0.99, plus sub-cent prices), counts (1..1000), maker/taker, multipliers, dates around the
fee-schedule changes and series overrides. Equality is exact (``==``), not approximate.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "research"))
sys.path.insert(0, str(ROOT / "scripts" / "live"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _legacy_fees as LG  # noqa: E402

from predict_ngin.costs import kalshi as KC  # noqa: E402
from predict_ngin.costs import kalshi_schedule as KS  # noqa: E402
from predict_ngin.costs import perps as KP  # noqa: E402
from predict_ngin.costs import polymarket as PM  # noqa: E402
from predict_ngin.shared.units import ceil_cents  # noqa: E402

PRICES = np.round(np.arange(1, 100) / 100.0, 2)            # 0.01 .. 0.99
SUBCENT = np.arange(1, 1000) / 1000.0                       # 0.001 .. 0.999
COUNTS = np.arange(1, 1001, dtype=float)                    # 1 .. 1000
MULTS = (0.25, 0.5, 1.0, 1.5, 2.0)
PG, CG = (a.ravel() for a in np.meshgrid(PRICES, COUNTS))   # 99,000 (price, count) pairs
SPG, SCG = (a.ravel() for a in np.meshgrid(SUBCENT, np.array([1.0, 3.0, 7.0, 10.0, 33.0, 100.0, 250.0, 1000.0])))


def same(a, b):
    a, b = np.asarray(a), np.asarray(b)
    assert a.shape == b.shape
    assert np.array_equal(a, b), f"{int((a != b).sum())} differences, first at {np.flatnonzero(a != b)[:5]}"


# ------------------------------------------------------------------------------- shared constants
def test_constants():
    assert KC.TAKER_RATE == LG.FLB_TAKER_RATE == LG.FA_TAKER == 0.07
    assert KC.MAKER_RATE == LG.FLB_MAKER_RATE == LG.FA_MAKER == 0.0175
    assert KC.MAKER_FEE_START_TS == LG.FLB_MAKER_FEE_START == LG.CAPFWD_MAKER_FEE_START
    assert KC.MAKER_FEE_START == LG.FA_MAKER_START
    assert int(KC.MAKER_FEE_START.timestamp()) == KC.MAKER_FEE_START_TS
    assert KC.DIRECT_PRECISION_TS == LG.FA_DIRECT_PREC_TS
    assert int(KC.DIRECT_PRECISION_START.timestamp()) == KC.DIRECT_PRECISION_TS
    assert KS.SCHEDULE_START == LG.FA_T0


def test_ceil_cents_hand_checked():
    assert ceil_cents(1.75) == 1.75
    assert ceil_cents(0.0101) == 0.02
    assert ceil_cents(0.01000000001) == 0.01          # float noise snaps to the cent
    assert ceil_cents(0.010000001, eps=1e-9) == 0.02  # the arbitrage guard does not
    assert KC.kalshi_order_fee(100, 0.5) == 1.75
    assert KC.kalshi_order_fee(1, 0.5) == 0.02
    assert KC.kalshi_order_fee(100, 0.5, maker=True) == 0.44
    assert KC.kalshi_order_fee(100, 0.5, rounding="exact") == pytest.approx(1.75)
    with pytest.raises(ValueError):
        KC.kalshi_order_fee(1, 0.5, rounding="bankers")


# ------------------------------------------------------------------------------- kalshi_flb (shim)
def test_kalshi_flb_shim_arrays():
    K = pytest.importorskip("src.strategies_legal.kalshi_flb")  # private module: skipped in a public clone
    for m in MULTS:
        same(K.taker_fee(CG, PG, multiplier=m), LG.flb_taker_fee(CG, PG, multiplier=m))
        same(K.taker_fee(SCG, SPG, multiplier=m), LG.flb_taker_fee(SCG, SPG, multiplier=m))
        applies = (CG.astype(int) % 3) != 0
        same(K.maker_fee(CG, PG, applies, multiplier=m), LG.flb_maker_fee(CG, PG, applies, multiplier=m))
    mult_arr = np.resize(np.array(MULTS), PG.size)
    same(K.taker_fee(CG, PG, multiplier=mult_arr), LG.flb_taker_fee(CG, PG, multiplier=mult_arr))
    same(K.taker_fee(CG, PG, rate=0.035), LG.flb_taker_fee(CG, PG, rate=0.035))
    same(K.ceil_cent(0.07 * CG * PG * (1 - PG)), LG.flb_ceil_cent(0.07 * CG * PG * (1 - PG)))
    assert K.MAKER_FEE_START == LG.FLB_MAKER_FEE_START
    assert (K.TAKER_RATE, K.MAKER_RATE) == (LG.FLB_TAKER_RATE, LG.FLB_MAKER_RATE)


def test_kalshi_flb_shim_scalars_keep_type():
    K = pytest.importorskip("src.strategies_legal.kalshi_flb")  # private module: skipped in a public clone
    for p in PRICES[::7]:
        for c in (1, 7, 100, 999):
            a, b = K.taker_fee(c, float(p), multiplier=0.5), LG.flb_taker_fee(c, float(p), multiplier=0.5)
            assert a == b and type(a) is type(b)
            a, b = K.maker_fee(c, float(p), True), LG.flb_maker_fee(c, float(p), True)
            assert a == b and type(a) is type(b)


def test_maker_fee_applies_dates_and_modes():
    K = pytest.importorskip("src.strategies_legal.kalshi_flb")  # private module: skipped in a public clone
    s = KC.MAKER_FEE_START_TS
    ts = np.array([s - 86400, s - 1, s, s + 1, s + 86400 * 400] * 4)
    ft = np.array(["quadratic"] * 5 + ["quadratic_with_maker_fees"] * 5 + [None] * 5 + [np.nan] * 5, dtype=object)
    for mode in ("series", "all", "none"):
        same(K.maker_fee_applies(ft, ts, mode), LG.flb_maker_fee_applies(ft, ts, mode))
        same(KC.maker_fee_applies(ft, ts, mode, start=s + 1), LG.flb_maker_fee_applies(ft, ts, mode, start=s + 1))


# ------------------------------------------------------------------------------- feeaudit fill fee
@pytest.mark.parametrize("rounding", ["fill_cent", "exact", "regime"])
def test_fill_fee_matches_feeaudit(rounding):
    d = KC.DIRECT_PRECISION_TS
    for maker, applies in ((False, True), (True, True), (True, False)):
        for mult in (0.5, 1.0, 2.0):
            for ts in (d - 1, d):
                for c, p in zip(CG[::3], PG[::3]):
                    c, p = int(c), float(p)
                    new = KC.kalshi_fill_fee(c, p, maker, applies, mult, rounding, ts)
                    assert new == LG.feeaudit_fee(c, p, maker, applies, mult, rounding, ts)


def test_fill_fee_subcent_and_numpy_scalars():
    for c, p in zip(SCG, SPG):
        for args in ((False, True, 1.0), (True, True, 0.5)):
            assert KC.kalshi_fill_fee(c, p, *args, "fill_cent", 0) == LG.feeaudit_fee(c, p, *args, "fill_cent", 0)
            pn, cn = np.float64(p), np.int64(c)
            assert KC.kalshi_fill_fee(cn, pn, *args, "fill_cent", 0) == LG.feeaudit_fee(cn, pn, *args, "fill_cent", 0)


# ------------------------------------------------------------------------------- PIT schedule
def _payloads():
    series_all = [
        {"ticker": "KXINXU", "fee_type": "quadratic", "fee_multiplier": 1.0},           # half family, logged
        {"ticker": "KXNASDAQ100", "fee_type": "quadratic", "fee_multiplier": 0.5},      # half family, unlogged
        {"ticker": "KXHIGHNY", "fee_type": "quadratic_with_maker_fees", "fee_multiplier": 1.0},  # assumed maker
        {"ticker": "KXFED", "fee_type": "quadratic", "fee_multiplier": None},           # None multiplier
        {"ticker": "KXNBAGAME", "fee_type": "quadratic_with_maker_fees", "fee_multiplier": 1.0},  # logged twice
        {"ticker": "KXMISMATCH", "fee_type": "quadratic", "fee_multiplier": 2.0},       # inconsistent with log
        {"ticker": "KXNOTYPE", "fee_type": None},
    ]
    changes = [
        {"series_ticker": "KXINXU", "scheduled_ts": "2026-02-05T00:00:00Z", "fee_type": "quadratic",
         "fee_multiplier": 1.0},
        {"series_ticker": "KXNBAGAME", "scheduled_ts": "2025-10-04T13:00:00Z", "fee_type": "quadratic",
         "fee_multiplier": 0.5},
        {"series_ticker": "KXNBAGAME", "scheduled_ts": "2026-09-03T04:00:00.000Z",
         "fee_type": "quadratic_with_maker_fees", "fee_multiplier": 1.0},
        {"series_ticker": "KXMISMATCH", "scheduled_ts": "2026-01-01T00:00:00Z", "fee_type": "quadratic",
         "fee_multiplier": 1.0},
    ]
    return series_all, changes


def test_build_schedule_matches_feeaudit():
    sa, ch = _payloads()
    for extra in ((), ("KXMISSING", "KXINXU")):
        new = KS.build_schedule(sa, ch, extra)
        old = LG.feeaudit_build_schedule(sa, ch, extra)
        pd.testing.assert_frame_equal(new, old)
        assert new.attrs["inconsistent"] == old.attrs["inconsistent"]
    assert new.attrs["inconsistent"]  # KXMISMATCH is flagged


def _opps(sched):
    rows = []
    for eff in pd.to_datetime(sched.effective_from, utc=True).unique():
        t = int(pd.Timestamp(eff).timestamp())
        for s in list(sched.series.unique()) + ["KXUNKNOWN"]:
            for dt in (-1, 0, 1, 86400):
                rows.append((s, t + dt))
    o = pd.DataFrame(rows, columns=["series", "ts"]).sample(frac=1.0, random_state=0).reset_index(drop=True)
    o["x"] = np.arange(len(o))
    return o


@pytest.mark.parametrize("unit", ["ns", "us"])
def test_pit_matches_feeaudit(unit):
    sa, ch = _payloads()
    sched = KS.build_schedule(sa, ch, ("KXMISSING",))
    sched["effective_from"] = sched.effective_from.astype(f"datetime64[{unit}, UTC]")
    o = _opps(sched)
    shift = int(pd.Timestamp("2025-06-01", tz="UTC").timestamp())
    for kw in ({}, {"maker_start": shift}, {"maker_start": shift, "maker_series": ["KXHIGHNY"]},
               {"maker_start": shift, "maker_series": ["KXNOPE"]}):
        pd.testing.assert_frame_equal(KS.pit(o, sched, **kw), LG.feeaudit_pit(o, sched, **kw))


@pytest.mark.data
def test_schedule_on_saved_api_payloads():
    raw = ROOT / "data" / "vendor" / "feeaudit" / "raw"
    if not (raw / "series_all.json").exists() or not (raw / "series_fee_changes.json").exists():
        pytest.skip("feeaudit raw payloads not present")
    import json
    sa = json.loads((raw / "series_all.json").read_text())["series"]
    ch = json.loads((raw / "series_fee_changes.json").read_text())["series_fee_change_arr"]
    pd.testing.assert_frame_equal(KS.build_schedule_from_files(raw), LG.feeaudit_build_schedule(sa, ch))


# ------------------------------------------------------------------------------- arbitrage fees (shim)
def test_arbitrage_shim_kalshi_fee():
    from src.trading.arbitrage import fees as F
    for c, p in zip(CG[::5], PG[::5]):
        for maker in (False, True):
            assert F.kalshi_fee(c, p, maker) == LG.arb_kalshi_fee(c, p, maker)
    for c, p in zip(SCG, SPG):
        assert F.kalshi_fee(c, p) == LG.arb_kalshi_fee(c, p)
        assert F.kalshi_fee(c, p, True, 0.07, 0.035) == LG.arb_kalshi_fee(c, p, True, 0.07, 0.035)
        assert F.kalshi_fee(np.float64(c), np.float64(p)) == LG.arb_kalshi_fee(np.float64(c), np.float64(p))
    for c, p in ((0, 0.5), (-3, 0.5), (10, 0.0), (10, 1.0), (10, -0.2), (10, 1.3), (10, 0.5)):
        a, b = F.kalshi_fee(c, p), LG.arb_kalshi_fee(c, p)
        assert a == b and type(a) is type(b)
    for x in (0.0, -1.0, 1e-12, 0.01, 0.0100000001, 0.0123, 1.75, 17.5000000004):
        assert F._ceil_cents(x) == LG.arb_ceil_cents(x)


def test_arbitrage_shim_polymarket_and_model():
    from src.trading.arbitrage import fees as F
    cats = [None, "crypto", "Sports", " politics ", "geopolitics", "weird", "ECON"]
    for cat in cats:
        assert F.polymarket_fee_rate(cat) == LG.arb_polymarket_fee_rate(cat)
        for c, p in zip(SCG[::11], SPG[::11]):
            for maker in (False, True):
                assert F.polymarket_fee(c, p, cat, maker) == LG.arb_polymarket_fee(c, p, cat, maker)
    assert F.POLY_CATEGORY_RATES == LG.ARB_POLY_CATEGORY_RATES
    assert F.POLY_DEFAULT_RATE == LG.ARB_POLY_DEFAULT_RATE
    assert (F.KALSHI_TAKER_COEF, F.KALSHI_MAKER_COEF) == (LG.ARB_KALSHI_TAKER_COEF, LG.ARB_KALSHI_MAKER_COEF)
    for kw in ({}, {"poly_fees_enabled": False}, {"kalshi_taker_coef": 0.035, "poly_default_rate": 0.02}):
        new, old = F.FeeModel(**kw), LG.ArbFeeModel(**kw)
        for c, p in zip(CG[::97], PG[::97]):
            for maker in (False, True):
                assert new.kalshi(c, p, maker) == old.kalshi(c, p, maker)
                assert new.poly(c, p, "crypto", maker) == old.poly(c, p, "crypto", maker)
                assert new.kalshi_per_contract(p, maker) == old.kalshi_per_contract(p, maker)
                assert new.poly_per_contract(p, "tech", maker) == old.poly_per_contract(p, "tech", maker)
        assert new.describe()["poly_fees_enabled"] == old.poly_fees_enabled


# ------------------------------------------------------------------------------- repointed helpers
def test_sharp_common_taker_fee():
    SC = pytest.importorskip("sharp_common")  # private module: skipped in a public clone
    for m in MULTS:
        same(SC.taker_fee(PG, CG, m), LG.sharp_taker_fee(PG, CG, m))
        same(SC.taker_fee(SPG, SCG, m), LG.sharp_taker_fee(SPG, SCG, m))
    ps = pd.Series(PRICES, index=np.arange(99) + 7)
    pd.testing.assert_series_equal(SC.taker_fee(ps, 10), LG.sharp_taker_fee(ps, 10))
    for c, p in zip(CG[::13], PG[::13]):
        assert SC.taker_fee(float(p), float(c)) == LG.sharp_taker_fee(float(p), float(c))


def test_capbook_forward_fees():
    CF = pytest.importorskip("capbook_forward")  # private module: skipped in a public clone
    s = LG.CAPFWD_MAKER_FEE_START
    for p in PRICES:
        for ft in ("quadratic", "quadratic_with_maker_fees"):
            for mode in ("taker", "maker", "series"):
                for ts in (s - 1, s):
                    assert CF.fee_c(p, ft, mode, ts) == LG.capfwd_fee_c(p, ft, mode, ts)
    for c, p in zip(CG[::3], PG[::3]):
        for taker, applies, mult in ((True, False, 1.0), (False, True, 0.5), (False, False, 1.0), (True, True, 2)):
            assert CF.kalshi_fee(int(c), float(p), taker, applies, mult) == \
                LG.capfwd_kalshi_fee(int(c), float(p), taker, applies, mult)


def test_wxlive_fee():
    W = pytest.importorskip("wxlive_paper_eval")  # private module: skipped in a public clone
    for c, p in zip(CG, PG):
        assert W.fee(int(c), float(p)) == LG.wxlive_fee(int(c), float(p))
    for c, p in zip(SCG, SPG):
        assert W.fee(int(c), float(p)) == LG.wxlive_fee(int(c), float(p))


def test_polymarket_per_share_helpers():
    AD = pytest.importorskip("alpha_decay_common")  # private module: skipped in a public clone
    PX = pytest.importorskip("polyx_common")  # private module: skipped in a public clone
    from src.whale_strategy import execution_model as WM
    for rate in (0.0, 0.04, 0.05, 0.07):
        same(PX.pm_taker_fee(SPG, rate), LG.polyx_pm_taker_fee(SPG, rate))
        same(AD.pm_fee_pc(np.r_[-0.1, SPG, 1.2], rate), LG.alpha_pm_fee_pc(np.r_[-0.1, SPG, 1.2], rate))
        for p in SPG[::17]:
            assert PX.pm_taker_fee(float(p), rate) == LG.polyx_pm_taker_fee(float(p), rate)
            assert WM.taker_fee_per_share(p, rate) == LG.whale_taker_fee_per_share(p, rate)
    assert PX.pm_taker_fee(0.3) == LG.polyx_pm_taker_fee(0.3)
    assert PM.polymarket_fee_per_share(0.5, 0.04) == 0.01


def test_perps_tiers():
    P = pytest.importorskip("perps_carry_paper")  # private module: skipped in a public clone
    assert (P.TAKER, P.MAKER) == (LG.PERPS_TAKER, LG.PERPS_MAKER)
    assert (KP.CRYPTO_PERPS.taker, KP.CRYPTO_PERPS.maker) == (0.0012, 0.0002)
    assert (KP.METALS_INDEX_PERPS.taker, KP.METALS_INDEX_PERPS.maker) == (0.000072, 0.000012)
    assert KP.perps_fee(10_000.0) == 10_000.0 * 0.0012
    assert KP.perps_fee(10_000.0, KP.METALS_INDEX_PERPS, maker=True) == 10_000.0 * 0.000012


def test_canonical_variants_reproduce_kept_helpers():
    """The helpers left as deliberate variants (docs/ARCHITECTURE.md) are still expressible exactly."""
    FD = pytest.importorskip("fin_D_common")  # private module: skipped in a public clone
    MK = pytest.importorskip("mkgap_common")  # private module: skipped in a public clone
    for m in (0.5, 1.0):
        same(FD.taker_fee(CG, PG, m), KC.kalshi_order_fee(CG, PG, multiplier=m, multiplier_in_ceil=False))
    q = PG * 100
    same(MK.taker_fee_per_contract(q, CG, 0.5),
         KC.kalshi_order_fee(CG, q / 100.0, multiplier=np.asarray(0.5), multiplier_in_ceil=False) / CG)
    # the arbitrage guard (eps) and the schedule guard agree on this whole grid (cent and 0.1c prices) ...
    for x in (0.07 * CG * PG * (1 - PG), 0.07 * SCG * SPG * (1 - SPG)):
        same(ceil_cents(x), ceil_cents(x, eps=1e-9))
    # ... and differ only within 5e-7 cent of a whole cent, which is why both are kept
    assert ceil_cents(0.010000004) == 0.01
    assert ceil_cents(0.010000004, eps=1e-9) == 0.02
    assert math.ceil(0.010000004 * 100 - 1e-9) / 100 == 0.02

