import unittest
from typing import Any

from data_ngin.infrastructure.loader.loader import Loader


class MockLoader(Loader):
    """
    Mock implementation of the Loader abstract class for testing purposes.
    """

    def load_symbols(self) -> dict[str, str]:
        """
        Simulates loading symbols from a mock data source.

        Returns:
            Dict[str, str]: A dictionary of symbols and their corresponding asset types.
        """
        return {
            "AAPL": "EQUITY",
            "ES": "FUTURES",
            "BTC": "CRYPTO",
        }


class TestLoader(unittest.TestCase):
    """
    Unit tests for the Loader base class using MockLoader.
    """

    def setUp(self) -> None:
        """
        Set up a mock loader instance with test configuration.
        """
        config: dict[str, Any] = {"provider": {"supported_assets": ["EQUITY", "FUTURES"]}}
        self.loader: Loader = MockLoader(config=config)

    def test_loader_initialization(self) -> None:
        """
        Test that the loader initializes with the correct configuration.
        """
        expected_config: dict[str, Any] = {"provider": {"supported_assets": ["EQUITY", "FUTURES"]}}
        self.assertEqual(self.loader.config, expected_config, "Loader configuration mismatch.")

    def test_abstract_method_enforcement(self) -> None:
        """
        Test that calling the abstract method from Loader raises a TypeError.
        """
        with self.assertRaises(TypeError):
            _ = Loader(config={})  # Direct instantiation should raise an error.

    def test_load_symbols_mock(self) -> None:
        """
        Test that the mock loader's load_symbols method returns expected results.
        """
        symbols: dict[str, str] = self.loader.load_symbols()
        expected_symbols: dict[str, str] = {
            "AAPL": "EQUITY",
            "ES": "FUTURES",
            "BTC": "CRYPTO",
        }
        self.assertEqual(symbols, expected_symbols, "Loaded symbols do not match expected results.")


if __name__ == "__main__":
    unittest.main()
