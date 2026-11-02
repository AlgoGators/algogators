"""Fee model tests for cross-platform arbitrage (Kalshi / Polymarket)."""

import math

import pytest

from src.trading.arbitrage.fees import (
    FeeModel,
    kalshi_fee,
    polymarket_fee,
    polymarket_fee_rate,
)


class TestKalshiFee:
    def test_taker_fee_100_contracts_at_50c(self):
        # 0.07 * 100 * 0.5 * 0.5 = 1.75
        assert kalshi_fee(100, 0.50) == pytest.approx(1.75)

    def test_taker_fee_rounds_up_to_cent_per_fill(self):
        # 0.07 * 1 * 0.25 = 0.0175 -> $0.02
        assert kalshi_fee(1, 0.50) == pytest.approx(0.02)
        # 0.07 * 10 * 0.9 * 0.1 = 0.063 -> $0.07
        assert kalshi_fee(10, 0.90) == pytest.approx(0.07)

    def test_fee_is_not_a_profit_fee(self):
        # Fee charged even if the trade later loses; depends only on C, P
        # 0.07 * 100 * 0.1 * 0.9 = 0.63
        assert kalshi_fee(100, 0.10) == pytest.approx(0.63)

    def test_maker_fee_coefficient(self):
        # 0.0175 * 100 * 0.25 = 0.4375 -> 0.44
        assert kalshi_fee(100, 0.50, maker=True) == pytest.approx(0.44)

    def test_maker_fee_can_be_disabled(self):
        assert kalshi_fee(100, 0.50, maker=True, maker_coef=0.0) == 0.0

    def test_zero_contracts(self):
        assert kalshi_fee(0, 0.5) == 0.0

    def test_symmetric_yes_no(self):
        assert kalshi_fee(37, 0.23) == kalshi_fee(37, 0.77)

    def test_float_noise_does_not_bump_a_cent(self):
        # 0.07 * 200 * 0.5 * 0.5 = 3.5 exactly; must not become 3.51
        assert kalshi_fee(200, 0.5) == pytest.approx(3.50)


class TestPolymarketFee:
    @pytest.mark.parametrize(
        "category,rate",
        [
            ("crypto", 0.07),
            ("sports", 0.05),
            ("politics", 0.04),
            ("finance", 0.04),
            ("tech", 0.04),
            ("economics", 0.05),
            ("culture", 0.05),
            ("weather", 0.05),
            ("other", 0.05),
            ("geopolitics", 0.0),
            ("something-unknown", 0.05),
        ],
    )
    def test_rates_by_category(self, category, rate):
        assert polymarket_fee_rate(category) == pytest.approx(rate)

    def test_taker_fee_formula(self):
        # 100 * 0.05 * 0.4 * 0.6 = 1.2
        assert polymarket_fee(100, 0.40, "sports") == pytest.approx(1.2)

    def test_maker_is_free(self):
        assert polymarket_fee(100, 0.40, "sports", maker=True) == 0.0

    def test_geopolitics_free(self):
        assert polymarket_fee(100, 0.40, "geopolitics") == 0.0


class TestFeeModel:
    def test_configurable(self):
        fm = FeeModel(kalshi_taker_coef=0.035, poly_rates={"sports": 0.0}, poly_default_rate=0.0)
        # 0.035 * 100 * 0.25 = 0.875 -> $0.88
        assert fm.kalshi(100, 0.5) == pytest.approx(0.88)
        assert fm.poly(100, 0.5, "sports") == 0.0

    def test_disable_poly_fees(self):
        fm = FeeModel(poly_fees_enabled=False)
        assert fm.poly(100, 0.5, "crypto") == 0.0

    def test_default_kalshi(self):
        fm = FeeModel()
        assert fm.kalshi(100, 0.5) == pytest.approx(1.75)
        assert fm.kalshi(100, 0.5, maker=True) == pytest.approx(0.44)
