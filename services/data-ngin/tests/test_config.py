import os
import unittest
from typing import Any

import pytest
from data_ngin.utils.dynamic_loader import load_config as _load_config
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


def load_config() -> dict[str, Any]:
    """
    Loads the (schema-validated) configuration settings from config.yaml.

    Delegates to utils.dynamic_loader.load_config -- this file used to
    duplicate that logic with its own copy pointed at the wrong path
    ("data/config/config.yaml", which never existed; the real file is at
    "src/config/config.yaml"), so it always raised FileNotFoundError.

    Returns:
        Dict[str, Any]: The configuration settings as a dictionary.

    Raises:
        FileNotFoundError: If the configuration file is not found.
        ValueError: If the configuration file is invalid or fails schema validation.
    """
    return _load_config()  # package-relative default


class TestConfig(unittest.TestCase):
    """
    Unit tests for verifying essential sections in the configuration file.
    """

    @classmethod
    def setUpClass(cls) -> None:
        """
        Load the configuration once for all tests.
        """
        cls.config: dict[str, Any] = load_config()

    def test_global_sections_exist(self) -> None:
        """
        Test that essential sections in config.yaml exist.
        """
        required_sections: list[str] = [
            "loader",
            "inserter",
            "fetcher",
            "cleaner",
            "provider",
            "database",
            "time_range",
            "missing_data",
        ]
        for section in required_sections:
            with self.subTest(section=section):
                self.assertIn(section, self.config, f"Section '{section}' missing from config.yaml")

    def test_loader_section(self) -> None:
        """
        Test that the loader section has necessary fields.
        """
        loader_config: dict[str, Any] = self.config["loader"]
        self.assertIn("class", loader_config, "Loader class missing from config.yaml")
        self.assertIn("module", loader_config, "Loader module missing from config.yaml")
        self.assertIn("file_path", loader_config, "Loader file path missing from config.yaml")

    def test_inserter_section(self) -> None:
        """
        Test that the inserter section has necessary fields.
        """
        inserter_config: dict[str, Any] = self.config["inserter"]
        self.assertIn("class", inserter_config, "Inserter class missing from config.yaml")
        self.assertIn("module", inserter_config, "Inserter module missing from config.yaml")

    def test_provider_section(self) -> None:
        """
        Test that the provider section has necessary fields.
        """
        provider_config: dict[str, Any] = self.config["provider"]
        required_fields: list[str] = ["asset", "dataset", "schema", "roll_type", "contract_type"]
        for field in required_fields:
            with self.subTest(field=field):
                self.assertIn(field, provider_config, f"{field} missing from provider config")

    def test_database_section(self) -> None:
        """
        Test that the database section exists with necessary fields.
        """
        database_config: dict[str, Any] = self.config["database"]
        self.assertIn("target_schema", database_config, "Database target schema missing")
        self.assertIn("table", database_config, "Database table missing")

    def test_time_range_section(self) -> None:
        """
        Test that the time range section exists.
        """
        time_range_config: dict[str, Any] = self.config["time_range"]
        self.assertIn("start_date", time_range_config, "Start date missing in time_range")
        self.assertIn("end_date", time_range_config, "End date missing in time_range")


@pytest.mark.integration
class TestEnvVariables(unittest.TestCase):
    """
    Unit tests to verify that all required environment variables are loaded.
    """

    def test_databento_api_key_exists(self) -> None:
        """
        Test that the Databento API key is set in the environment.
        """
        self.assertTrue(os.getenv("DATABENTO_API_KEY"), "Databento API key missing in .env")

    def test_database_credentials_exist(self) -> None:
        """
        Test that database credentials are set in the environment.
        """
        env_vars: list[str] = ["DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME"]
        for var in env_vars:
            with self.subTest(env_var=var):
                self.assertTrue(os.getenv(var), f"{var} missing in .env")


if __name__ == "__main__":
    unittest.main()
