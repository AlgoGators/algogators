import logging
import os
import unittest
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest
from data_ngin.infrastructure import db_models
from data_ngin.infrastructure.db_models import OHLCV, Base, get_engine
from sqlalchemy import inspect
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


class TestGetEngineUnit(unittest.TestCase):
    """Unit tests for get_engine's URL construction -- no live database.

    get_engine now builds its DSN via platform_db.DatabaseConfig.url(), which
    quote_plus-escapes credentials; the old raw f-string broke as soon as the
    password contained '@' or '/'.
    """

    DB_ENV: ClassVar[dict[str, str]] = {
        "DB_HOST": "db.internal.example.com",
        "DB_PORT": "6543",
        "DB_NAME": "futures_data_db",
        "DB_USER": "app_user",
        "DB_PASSWORD": "p@ss/w:rd",
    }

    @patch.dict("os.environ", DB_ENV)
    def test_get_engine_survives_reserved_characters_in_password(self) -> None:
        engine = get_engine()

        self.assertEqual(engine.url.host, "db.internal.example.com")
        self.assertEqual(engine.url.port, 6543)
        self.assertEqual(engine.url.database, "futures_data_db")
        self.assertEqual(engine.url.username, "app_user")
        # The password round-trips intact despite '@', '/' and ':'.
        self.assertEqual(engine.url.password, "p@ss/w:rd")

    @patch.object(db_models, "load_dotenv", lambda: None)
    def test_get_engine_missing_env_raises_value_error(self) -> None:
        env = {key: value for key, value in os.environ.items() if not key.startswith("DB_")}
        with patch.dict("os.environ", env, clear=True), self.assertRaises(ValueError):
            get_engine()


@pytest.mark.integration
class TestDBModels(unittest.TestCase):
    """
    Unit tests for the `db_models.py` file, including database engine and ORM models.
    """

    engine: Engine | None = None
    session: Session | None = None

    @classmethod
    def setUpClass(cls) -> None:
        """
        Initialize the test database and create all tables.
        This method runs once before all tests in this class.
        """
        cls.engine = get_engine()
        cls.session = Session(cls.engine)
        try:
            Base.metadata.create_all(cls.engine)  # Ensure all tables are created
            logging.info("All tables created successfully.")
        except Exception as e:
            logging.error(f"Error creating tables: {e}")
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        """
        Clean up the database by dropping all tables and closing the session.
        This method runs once after all tests in this class.
        """
        if cls.session:
            cls.session.close()
        if cls.engine:
            logging.info("All tables dropped successfully.")

    def setUp(self) -> None:
        """
        Start a new transaction before each test.
        """
        assert self.engine is not None
        self.connection: Connection = self.engine.connect()
        self.trans = self.connection.begin()
        self.session = Session(bind=self.connection)

    def tearDown(self) -> None:
        """
        Rollback the transaction after each test.
        """
        self.session.rollback()
        self.connection.close()

    def test_get_engine(self) -> None:
        """
        Verify that `get_engine` successfully creates a database engine.
        """
        engine: Engine = get_engine()
        self.assertIsNotNone(engine)
        self.assertIn("postgresql", str(engine.url))

    def test_ohlcv_table_exists(self) -> None:
        """
        Check if the `ohlcv_1d` table exists in the database.
        """
        inspector = inspect(self.engine)
        tables: list[str] = inspector.get_table_names(schema="futures_data")
        self.assertIn("ohlcv_1d", tables, "The `ohlcv_1d` table was not found in the database.")

    def test_ohlcv_model_schema(self) -> None:
        """
        Validate the `OHLCV` model's table schema.
        """
        inspector = inspect(self.engine)
        columns: dict[str, str] = {
            col["name"]: col["type"]
            for col in inspector.get_columns("ohlcv_1d", schema="futures_data")
        }
        expected_columns: dict[str, str] = {
            "time": "TIMESTAMP",
            "symbol": "TEXT",
            "open": "DOUBLE_PRECISION",
            "high": "DOUBLE_PRECISION",
            "low": "DOUBLE_PRECISION",
            "close": "DOUBLE_PRECISION",
            "volume": "INTEGER",
        }

        for column, col_type in expected_columns.items():
            self.assertIn(column, columns, f"Column `{column}` is missing.")
            self.assertEqual(str(columns[column]), col_type, f"Type mismatch for `{column}`.")

    @patch("data.modules.db_models.Session.add")
    @patch("data.modules.db_models.Session.commit")
    def test_insert_ohlcv(self, mock_commit: MagicMock, mock_add: MagicMock) -> None:
        """
        Test inserting a record into the `ohlcv_1d` table.
        """
        record = OHLCV(
            time="2023-01-01T00:00:00Z",
            symbol="ES",
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1000,
        )
        self.session.add(record)
        self.session.commit()

        mock_add.assert_called_once_with(record)
        mock_commit.assert_called_once()

    @patch("data.modules.db_models.Session.commit")
    def test_insert_duplicate_ohlcv(self, mock_commit: MagicMock) -> None:
        """
        Test inserting duplicate records and expecting an IntegrityError.
        """
        record = OHLCV(
            time="2023-01-01T00:00:00Z",
            symbol="ES",
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1000,
        )

        # Mock commit behavior to raise IntegrityError
        mock_commit.side_effect = IntegrityError("Duplicate record", params=None, orig=None)

        self.session.add(record)
        with self.assertRaises(
            IntegrityError, msg="Duplicate record did not raise IntegrityError."
        ):
            self.session.commit()

    @patch("data.modules.db_models.Session.query")
    def test_query_ohlcv(self, mock_query: MagicMock) -> None:
        """
        Test querying the `ohlcv_1d` table.
        """
        record = OHLCV(
            time="2023-01-01T00:00:00Z",
            symbol="NQ",
            open=200.0,
            high=201.0,
            low=199.0,
            close=200.5,
            volume=2000,
        )

        mock_query.return_value.filter_by.return_value.one_or_none.return_value = record

        result = self.session.query(OHLCV).filter_by(symbol="NQ").one_or_none()
        self.assertIsNotNone(result, "The queried record was not found.")
        self.assertEqual(result.close, 200.5, "The `close` field did not match the inserted value.")

    @patch("data.modules.db_models.Session.delete")
    @patch("data.modules.db_models.Session.commit")
    def test_delete_ohlcv(self, mock_commit: MagicMock, mock_delete: MagicMock) -> None:
        """
        Test deleting a record from the `ohlcv_1d` table.
        """
        record = OHLCV(
            time="2023-01-01T00:00:00Z",
            symbol="YM",
            open=300.0,
            high=301.0,
            low=299.0,
            close=300.5,
            volume=3000,
        )

        self.session.delete(record)
        self.session.commit()

        mock_delete.assert_called_once_with(record)
        mock_commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
