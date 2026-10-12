"""The per-contract trading-day rule: does contract X have a bar for UTC day D?

Expected sets below match what Databento actually returned into
new_algo_data.futures_data.ohlcv_1d on those days.
"""

import unittest
from datetime import date

from data_ngin.application.trading_calendar import (
    calendar_name_for,
    contract_traded_on,
    expected_contracts,
)

FX = {"dataSymbol": "6E", "exchange": "CME", "underlying": "FX"}
EQUITY = {"dataSymbol": "ES", "exchange": "CME", "underlying": "Equity"}
RATES = {"dataSymbol": "ZN", "exchange": "CBOT", "underlying": "Bond"}
ENERGY = {"dataSymbol": "CL", "exchange": "NYMEX", "underlying": "OilGas"}
METALS = {"dataSymbol": "GC", "exchange": "COMEX", "underlying": "Metals"}
CRYPTO = {"dataSymbol": "MBT", "exchange": "CME", "underlying": "Crypto"}
GRAIN = {"dataSymbol": "ZC", "exchange": "CBOT", "underlying": "Ags"}
LIVESTOCK = {"dataSymbol": "LE", "exchange": "CME", "underlying": "Ags"}
ALL = [FX, EQUITY, RATES, ENERGY, METALS, CRYPTO, GRAIN, LIVESTOCK]
GLOBEX_OVERNIGHT = {"6E", "ES", "ZN", "CL", "GC", "MBT"}


def symbols(day: date) -> set[str]:
    return {c["dataSymbol"] for c in expected_contracts(ALL, day)}


class TestTradingDayRule(unittest.TestCase):
    def test_weekday_everything_trades(self) -> None:
        self.assertEqual(symbols(date(2026, 10, 9)), {c["dataSymbol"] for c in ALL})

    def test_sunday_evening_session_counts(self) -> None:
        # Globex opens Sunday 17:00 CT (22:00 UTC in CDT), so UTC Sunday has a
        # bar. Grains open 19:00 CT (00:00 UTC Monday); livestock has no
        # overnight session.
        self.assertEqual(symbols(date(2026, 10, 11)), GLOBEX_OVERNIGHT)
        # Same in CST (23:00 UTC open).
        self.assertEqual(symbols(date(2026, 1, 11)), GLOBEX_OVERNIGHT)

    def test_saturday_is_skipped(self) -> None:
        self.assertEqual(symbols(date(2026, 10, 10)), set())

    def test_full_holiday_closes_every_product(self) -> None:
        # Christmas 2026 is a Friday: closed all day, reopening Sunday evening.
        self.assertEqual(symbols(date(2026, 12, 25)), set())

    def test_holiday_with_abbreviated_session(self) -> None:
        # Labor Day: Globex products trade an early-close session (a bar);
        # grains and livestock are closed (no bar).
        self.assertEqual(symbols(date(2026, 9, 7)), GLOBEX_OVERNIGHT)
        # Good Friday 2026: equity index, rates, FX and crypto trade
        # abbreviated; energy, metals, grains and livestock are closed.
        self.assertEqual(symbols(date(2026, 4, 3)), {"6E", "ES", "ZN", "MBT"})

    def test_early_close_day_still_trades(self) -> None:
        self.assertEqual(symbols(date(2026, 12, 24)), {c["dataSymbol"] for c in ALL})

    def test_unmapped_contract_is_expected(self) -> None:
        unknown = {"dataSymbol": "FDAX", "exchange": "EUREX", "underlying": "Equity"}
        self.assertIsNone(calendar_name_for(unknown))
        with self.assertLogs("data_ngin.application.trading_calendar", "WARNING"):
            self.assertTrue(contract_traded_on(unknown, date(2026, 10, 10)))

    def test_unknown_underlying_on_cme_is_unmapped(self) -> None:
        self.assertIsNone(
            calendar_name_for({"dataSymbol": "X", "exchange": "CME", "underlying": "?"})
        )

    def test_ags_split_by_symbol(self) -> None:
        self.assertEqual(calendar_name_for(GRAIN), "CMEGlobex_Grains")
        self.assertEqual(calendar_name_for(LIVESTOCK), "CMEGlobex_Livestock")


if __name__ == "__main__":
    unittest.main()
