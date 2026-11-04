import unittest

import numpy as np
from data_ngin.domain.options_pricing import (
    MAX_VOL,
    IvStatus,
    atm_vol,
    black76_greeks,
    black76_price,
    discount_yield_to_continuous,
    forward_from_parity,
    implied_vol,
    vol_at_tenor,
)


class TestBlack76Price(unittest.TestCase):
    def test_at_the_money_call_matches_closed_form(self) -> None:
        # F = K, r = 0: C = F (2 N(sigma sqrt(T) / 2) - 1); N(0.1) = 0.5398278372770290
        price = black76_price(100.0, 100.0, 1.0, 0.0, 0.2, True)
        self.assertAlmostEqual(float(price), 100.0 * (2 * 0.5398278372770290 - 1), places=10)

    def test_put_call_parity(self) -> None:
        F, T, r, sigma = 105.0, 0.5, 0.04, 0.3
        K = np.array([80.0, 100.0, 105.0, 130.0])
        calls = black76_price(F, K, T, r, sigma, True)
        puts = black76_price(F, K, T, r, sigma, False)
        np.testing.assert_allclose(calls - puts, np.exp(-r * T) * (F - K), atol=1e-10)


class TestImpliedVol(unittest.TestCase):
    def test_recovers_the_volatility_that_priced_the_option(self) -> None:
        K = np.array([60.0, 80.0, 95.0, 100.0, 105.0, 120.0, 160.0] * 2)
        is_call = np.array([True] * 7 + [False] * 7)
        for T in (7 / 365, 30 / 365, 1.0):
            for sigma in (0.05, 0.2, 0.8):
                price = black76_price(100.0, K, T, 0.05, sigma, is_call)
                intrinsic = np.exp(-0.05 * T) * np.maximum(
                    np.where(is_call, 100.0 - K, K - 100.0), 0
                )
                # deep ITM/OTM at short T and low vol has basically no time value, skip those
                usable = price - intrinsic > 1e-8
                vols, status = implied_vol(
                    price[usable], 100.0, K[usable], T, 0.05, is_call[usable]
                )
                self.assertTrue((status == IvStatus.OK.value).all(), (T, sigma, status))
                np.testing.assert_allclose(vols, sigma, rtol=1e-7)

    def test_rerun_is_bit_identical(self) -> None:
        K = np.linspace(70.0, 130.0, 25)
        price = black76_price(100.0, K, 0.25, 0.03, 0.25, K >= 100.0)
        first = implied_vol(price, 100.0, K, 0.25, 0.03, K >= 100.0)
        second = implied_vol(price, 100.0, K, 0.25, 0.03, K >= 100.0)
        np.testing.assert_array_equal(first[0], second[0])
        np.testing.assert_array_equal(first[1], second[1])

    def test_prices_outside_the_bounds_get_a_status(self) -> None:
        F, T, r = 100.0, 0.5, 0.05
        discount = np.exp(-r * T)
        cases = [
            # (price, strike, is_call, expected status)
            (discount * 10.0, 90.0, True, IvStatus.BELOW_INTRINSIC),  # exactly intrinsic
            (discount * 9.0, 90.0, True, IvStatus.BELOW_INTRINSIC),
            (discount * F, 90.0, True, IvStatus.ABOVE_MAX_PRICE),
            (discount * 110.0, 110.0, False, IvStatus.ABOVE_MAX_PRICE),
            (0.0, 100.0, True, IvStatus.INVALID_INPUT),
            (float("nan"), 100.0, True, IvStatus.INVALID_INPUT),
            (1e-6, 100.0, True, IvStatus.VOL_BELOW_MIN),  # price(MIN_VOL) is about 2.8e-3 here
            (
                float(black76_price(F, 150.0, T, r, MAX_VOL * 1.5, True)),
                150.0,
                True,
                IvStatus.VOL_ABOVE_MAX,
            ),
        ]
        for price, K, is_call, expected in cases:
            vol, status = implied_vol(price, F, K, T, r, is_call)
            self.assertEqual(status.item(), expected.value, (price, K, is_call))
            self.assertTrue(np.isnan(vol).all())

    def test_non_positive_time_is_invalid(self) -> None:
        _, status = implied_vol([1.0, 1.0], 100.0, 100.0, [0.0, -1.0], 0.0, True)
        self.assertEqual(list(status), [IvStatus.INVALID_INPUT.value] * 2)

    def test_scalar_inputs_return_zero_dimensional_results(self) -> None:
        price = float(black76_price(100.0, 110.0, 0.5, 0.02, 0.3, True))
        vol, status = implied_vol(price, 100.0, 110.0, 0.5, 0.02, True)
        self.assertAlmostEqual(vol.item(), 0.3, places=8)
        self.assertEqual(status.item(), IvStatus.OK.value)


class TestGreeks(unittest.TestCase):
    def test_greeks_match_finite_differences(self) -> None:
        F, T, r, sigma, h = 100.0, 0.4, 0.03, 0.25, 1e-4
        for K, is_call in ((90.0, False), (100.0, True), (115.0, True)):
            g = black76_greeks(F, K, T, r, sigma, is_call)

            def price(f=F, t=T, s=sigma, k=K, c=is_call):
                return float(black76_price(f, k, t, r, s, c))

            delta = (price(f=F + h) - price(f=F - h)) / (2 * h)
            gamma = (price(f=F + h) - 2 * price() + price(f=F - h)) / h**2
            vega = (price(s=sigma + h) - price(s=sigma - h)) / (2 * h)
            theta = -(price(t=T + h) - price(t=T - h)) / (2 * h)
            self.assertAlmostEqual(float(g.delta), delta, places=6)
            self.assertAlmostEqual(float(g.gamma), gamma, places=4)
            self.assertAlmostEqual(float(g.vega), vega, places=5)
            self.assertAlmostEqual(float(g.theta), theta, places=5)

    def test_put_delta_is_call_delta_less_the_discount(self) -> None:
        call = black76_greeks(100.0, 95.0, 0.5, 0.04, 0.2, True)
        put = black76_greeks(100.0, 95.0, 0.5, 0.04, 0.2, False)
        self.assertAlmostEqual(float(call.delta - put.delta), np.exp(-0.04 * 0.5), places=12)


class TestRateConversion(unittest.TestCase):
    def test_discount_yield_converts_to_the_continuous_rate(self) -> None:
        # 5% discount, 91 days -> bill price 0.98736111, then back to 1 over 91/365 yrs
        expected = -np.log(1 - 0.05 * 91 / 360) * 365 / 91
        self.assertAlmostEqual(float(discount_yield_to_continuous(5.0)), expected, places=12)
        self.assertGreater(float(discount_yield_to_continuous(5.0)), 0.05)

    def test_zero_rate_stays_zero(self) -> None:
        self.assertEqual(float(discount_yield_to_continuous(0.0)), 0.0)


class TestForwardFromParity(unittest.TestCase):
    def test_recovers_the_forward(self) -> None:
        F, T, r = 704.52, 21 / 365, 0.042
        K = np.arange(650.0, 760.0, 5.0)
        calls = black76_price(F, K, T, r, 0.18, True)
        puts = black76_price(F, K, T, r, 0.18, False)
        self.assertAlmostEqual(forward_from_parity(K, calls, puts, T, r), F, places=9)

    def test_uses_the_strike_where_call_and_put_are_closest(self) -> None:
        # only the 100 strike is consistent with F = 101, the others are junk
        K = [90.0, 100.0, 110.0]
        calls = [12.0, 1.5, 0.2]
        puts = [0.3, 0.5, 9.0]
        self.assertAlmostEqual(forward_from_parity(K, calls, puts, 0.0, 0.0), 101.0)

    def test_ties_go_to_the_lowest_strike(self) -> None:
        self.assertEqual(forward_from_parity([105.0, 95.0], [1.0, 1.0], [1.0, 1.0], 0.0, 0.0), 95.0)

    def test_no_usable_pair_is_nan(self) -> None:
        self.assertTrue(np.isnan(forward_from_parity([100.0], [np.nan], [1.0], 0.1, 0.0)))


class TestAtmVol(unittest.TestCase):
    def test_interpolates_linearly_in_strike(self) -> None:
        self.assertAlmostEqual(atm_vol(102.5, [95.0, 100.0, 105.0], [0.3, 0.2, 0.1]), 0.15)

    def test_strike_on_the_forward_is_used_directly(self) -> None:
        self.assertAlmostEqual(atm_vol(100.0, [95.0, 100.0, 105.0], [0.3, 0.2, 0.1]), 0.2)

    def test_forward_outside_the_strikes_is_nan(self) -> None:
        self.assertTrue(np.isnan(atm_vol(110.0, [95.0, 100.0], [0.3, 0.2])))


class TestVolAtTenor(unittest.TestCase):
    def test_interpolates_in_total_variance(self) -> None:
        T1, T2, v1, v2, t = 20 / 365, 40 / 365, 0.10, 0.20, 30 / 365
        w = v1**2 * T1 + (v2**2 * T2 - v1**2 * T1) * (t - T1) / (T2 - T1)
        self.assertAlmostEqual(vol_at_tenor([T2, T1], [v2, v1], t), np.sqrt(w / t), places=12)

    def test_flat_term_structure_stays_flat(self) -> None:
        self.assertAlmostEqual(vol_at_tenor([0.05, 0.1, 0.5], [0.2, 0.2, 0.2], 30 / 365), 0.2)

    def test_no_extrapolation(self) -> None:
        self.assertTrue(np.isnan(vol_at_tenor([0.2, 0.5], [0.2, 0.3], 30 / 365)))
