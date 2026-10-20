import unittest
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
from data_ngin.application.implied_vol_pass import (
    EOD_COLUMNS,
    METHOD_VERSION,
    QuoteStatus,
    atm_vol_by_underlying,
    compute_eod,
)
from data_ngin.domain.options_pricing import IvStatus, black76_price

TRADE_DATE = date(2026, 9, 15)
QUOTE_TS = pd.Timestamp(datetime(2026, 9, 15, 16, 0), tz="America/New_York").tz_convert("UTC")
FORWARD = 100.0
RATE = 0.04


def smile(strike: np.ndarray) -> np.ndarray:
    """Simple downward skew so each strike has a different vol."""
    return 0.2 + 0.002 * (FORWARD - strike)


def chain(days: int, root: str = "XYZ", first_id: int = 0) -> pd.DataFrame:
    """Calls and puts at strikes 80-120, priced off FORWARD and smile, 0.1% wide quotes."""
    strikes = np.arange(80.0, 125.0, 5.0)
    expiration = QUOTE_TS + timedelta(days=days)
    T = days / 365.0
    rows = []
    for call_put in ("C", "P"):
        prices = black76_price(FORWARD, strikes, T, RATE, smile(strikes), call_put == "C")
        for strike, price in zip(strikes, prices, strict=True):
            rows.append(
                {
                    "contract_id": first_id + len(rows),
                    "root": root,
                    "underlying": root,
                    "expiration": expiration,
                    "strike": strike,
                    "call_put": call_put,
                    "bid": price * 0.9995,
                    "ask": price * 1.0005,
                    "quote_ts": QUOTE_TS,
                }
            )
    return pd.DataFrame(rows)


class TestComputeEod(unittest.TestCase):
    def setUp(self) -> None:
        self.quotes = chain(14)
        self.eod = compute_eod(self.quotes, RATE, TRADE_DATE)

    def test_one_row_per_contract_with_the_eod_columns(self) -> None:
        self.assertEqual(list(self.eod.columns), list(EOD_COLUMNS))
        self.assertEqual(len(self.eod), len(self.quotes))
        self.assertTrue((self.eod["method_version"] == METHOD_VERSION).all())
        self.assertTrue((self.eod["date"] == TRADE_DATE).all())
        self.assertTrue((self.eod["days_to_expiry"] == 14).all())

    def test_forward_is_backed_out_of_the_prices(self) -> None:
        np.testing.assert_allclose(self.eod["forward"], FORWARD, atol=1e-9)

    def test_out_of_the_money_contracts_recover_the_smile(self) -> None:
        merged = self.quotes.merge(self.eod, on="contract_id")
        otm = merged[
            ((merged["call_put"] == "C") & (merged["strike"] >= FORWARD))
            | ((merged["call_put"] == "P") & (merged["strike"] < FORWARD))
        ]
        self.assertTrue((otm["iv_status"] == IvStatus.OK.value).all())
        np.testing.assert_allclose(otm["implied_vol"], smile(otm["strike"].to_numpy()), rtol=1e-8)
        self.assertTrue(otm[["delta", "gamma", "vega", "theta"]].notna().all().all())

    def test_in_the_money_contracts_are_priced_but_not_solved(self) -> None:
        merged = self.quotes.merge(self.eod, on="contract_id")
        itm = merged[
            ((merged["call_put"] == "C") & (merged["strike"] < FORWARD))
            | ((merged["call_put"] == "P") & (merged["strike"] >= FORWARD))
        ]
        self.assertFalse(itm.empty)
        self.assertTrue((itm["iv_status"] == QuoteStatus.IN_THE_MONEY.value).all())
        self.assertTrue(itm["implied_vol"].isna().all())
        self.assertTrue(itm["price_used"].notna().all())

    def test_rerun_gives_the_same_frame(self) -> None:
        shuffled = self.quotes.sample(frac=1.0, random_state=7)
        pd.testing.assert_frame_equal(compute_eod(shuffled, RATE, TRADE_DATE), self.eod)

    def test_expiries_outside_7_to_365_days_are_not_solved(self) -> None:
        quotes = pd.concat([chain(3, first_id=0), chain(400, first_id=100)])
        eod = compute_eod(quotes, RATE, TRADE_DATE)
        priced = eod["price_used"].notna()
        self.assertTrue((eod.loc[priced, "iv_status"] == QuoteStatus.DTE_OUT_OF_RANGE.value).all())

    def test_missing_zero_and_crossed_quotes_get_no_price(self) -> None:
        quotes = self.quotes.copy()
        quotes.loc[0, "bid"] = np.nan
        quotes.loc[1, "bid"] = 0.0
        quotes.loc[2, ["bid", "ask"]] = [2.0, 1.0]
        eod = compute_eod(quotes, RATE, TRADE_DATE).set_index("contract_id")
        self.assertEqual(eod.loc[0, "iv_status"], QuoteStatus.NO_QUOTE.value)
        self.assertEqual(eod.loc[1, "iv_status"], QuoteStatus.NO_QUOTE.value)
        self.assertEqual(eod.loc[2, "iv_status"], QuoteStatus.CROSSED_QUOTE.value)
        self.assertTrue(eod.loc[[0, 1, 2], "price_used"].isna().all())

    def test_an_expiry_with_only_calls_has_no_forward(self) -> None:
        calls_only = self.quotes[self.quotes["call_put"] == "C"]
        eod = compute_eod(calls_only, RATE, TRADE_DATE)
        self.assertTrue((eod["iv_status"] == QuoteStatus.NO_FORWARD.value).all())

    def test_rejects_duplicate_contracts(self) -> None:
        with self.assertRaises(ValueError):
            compute_eod(pd.concat([self.quotes, self.quotes.head(1)]), RATE, TRADE_DATE)

    def test_rejects_missing_columns(self) -> None:
        with self.assertRaises(ValueError):
            compute_eod(self.quotes.drop(columns=["quote_ts"]), RATE, TRADE_DATE)


class TestAtmVolByUnderlying(unittest.TestCase):
    def test_thirty_day_vol_from_the_bracketing_expiries(self) -> None:
        quotes = pd.concat([chain(14, first_id=0), chain(45, first_id=100)])
        eod = compute_eod(quotes, RATE, TRADE_DATE)
        result = atm_vol_by_underlying(quotes, eod)
        self.assertEqual(result["underlying"].tolist(), ["XYZ"])
        self.assertEqual(result["expiries"].tolist(), [2])
        # same smile at both expiries, so ATM vol should just be smile(F)
        self.assertAlmostEqual(result["atm_vol_30d"].iloc[0], 0.2, places=8)

    def test_one_expiry_cannot_be_interpolated(self) -> None:
        quotes = chain(45)
        result = atm_vol_by_underlying(quotes, compute_eod(quotes, RATE, TRADE_DATE))
        self.assertTrue(np.isnan(result["atm_vol_30d"].iloc[0]))
