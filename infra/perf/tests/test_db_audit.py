"""Smoke test for database audit runner.

Note: This test validates the db_audit_runner logic with both:
1. Unit tests using mocked psycopg2 connections
2. Integration test guidance for when Docker is available
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from infra.perf.contract import validate_contract
from infra.perf.footprint.db_audit_runner import DbAuditRunner


class TestDbAuditRunner(unittest.TestCase):
    """Test db_audit_runner logic with mocked database."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.snapshots_dir = self.temp_dir.name

    def tearDown(self) -> None:
        """Clean up test fixtures."""
        self.temp_dir.cleanup()

    @patch("infra.perf.footprint.db_audit_runner.psycopg2.connect")
    def test_db_audit_produces_valid_contract(self, mock_connect: MagicMock) -> None:
        """The audit runner should produce a valid contract JSON per spec §3."""
        # Mock the database connection and cursor
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        # Mock query results
        mock_cursor.fetchall.return_value = []

        runner = DbAuditRunner(
            host="localhost",
            port=5432,
            database="test_db",
            user="postgres",
            password="postgres",
            snapshots_dir=self.snapshots_dir,
        )

        result = runner.run(host="test-host", commit="abc123")

        # Check contract structure per spec §3
        assert "suite" in result
        assert result["suite"] == "perf"
        assert "repo" in result
        assert result["repo"] == "algogators"
        assert "probe" in result
        assert result["probe"] == "db_footprint"
        assert "captured_at" in result
        assert "environment" in result
        assert result["environment"]["host"] == "test-host"
        assert result["environment"]["commit"] == "abc123"
        assert "metrics" in result
        assert isinstance(result["metrics"], list)

        # Validate contract
        errors = validate_contract(result)
        assert len(errors) == 0, f"Contract validation errors: {errors}"

    @patch("infra.perf.footprint.db_audit_runner.psycopg2.connect")
    def test_db_audit_raw_cleaned_ratio(self, mock_connect: MagicMock) -> None:
        """The audit should compute the correct raw/cleaned ratio."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        # Mock query to return row counts
        # 12 raw rows / 10 cleaned rows = 1.2
        # Order: table_sizes, idx_query, rel_query, row_count_query, stream_query, compression_query
        mock_cursor.fetchall.side_effect = [
            [],  # table_sizes
            [],  # index total
            [],  # relation total
            [
                {"schema_name": "futures_data", "table_name": "ohlcv_1d_raw", "row_count": 12},
                {"schema_name": "futures_data", "table_name": "ohlcv_1d", "row_count": 10},
            ],  # row counts
            [],  # stream row share
            [],  # compression status
        ]

        runner = DbAuditRunner(
            host="localhost",
            port=5432,
            database="test_db",
            user="postgres",
            password="postgres",
            snapshots_dir=self.snapshots_dir,
        )

        result = runner.run()

        # Find the raw_cleaned_ratio metric
        ratio_metrics = [m for m in result["metrics"] if m.get("id") == "raw_cleaned_ratio"]
        assert len(ratio_metrics) == 1

        metric = ratio_metrics[0]
        # 12 raw / 10 cleaned = 1.2
        assert metric["value"] == 1.2
        assert metric["unit"] == "ratio"

    @patch("infra.perf.footprint.db_audit_runner.psycopg2.connect")
    def test_db_audit_table_metrics(self, mock_connect: MagicMock) -> None:
        """The audit should include table size metrics."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        # Mock table sizes
        # Order: table_sizes, idx_query, rel_query, row_count_query, stream_query, compression_query
        mock_cursor.fetchall.side_effect = [
            [
                {
                    "metric_id": "table_total_bytes",
                    "schema_name": "futures_data",
                    "table_name": "ohlcv_1d",
                    "value": 1000000,
                    "unit": "bytes",
                }
            ],  # table sizes
            [{"total": 500000}],  # index total
            [{"total": 2000000}],  # relation total
            [],  # row counts
            [],  # stream row share
            [],  # compression status
        ]

        runner = DbAuditRunner(
            host="localhost",
            port=5432,
            database="test_db",
            user="postgres",
            password="postgres",
            snapshots_dir=self.snapshots_dir,
        )

        result = runner.run()

        # Should have table metrics
        table_metrics = [m for m in result["metrics"] if "table_bytes" in m.get("id", "")]
        assert len(table_metrics) > 0

        # Verify they have the right structure
        for metric in table_metrics:
            assert metric["unit"] == "bytes"
            assert isinstance(metric["value"], (int, float))

    @patch("infra.perf.footprint.db_audit_runner.psycopg2.connect")
    def test_db_audit_growth_metrics_first_run(self, mock_connect: MagicMock) -> None:
        """On first run, growth_bytes_per_day should be NO_DATA."""
        snapshots_dir = Path(self.snapshots_dir)

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []

        runner = DbAuditRunner(
            host="localhost",
            port=5432,
            database="test_db",
            user="postgres",
            password="postgres",
            snapshots_dir=str(snapshots_dir),
        )

        result = runner.run()

        # Find the growth metric
        growth_metrics = [m for m in result["metrics"] if m.get("id") == "growth_bytes_per_day"]
        assert len(growth_metrics) == 1

        metric = growth_metrics[0]
        assert metric.get("status") == "NO_DATA"
        assert "note" in metric

    @patch("infra.perf.footprint.db_audit_runner.psycopg2.connect")
    def test_snapshot_file_created(self, mock_connect: MagicMock) -> None:
        """The audit should write a snapshot file with correct contract schema."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []

        snapshots_dir = Path(self.snapshots_dir)

        runner = DbAuditRunner(
            host="localhost",
            port=5432,
            database="test_db",
            user="postgres",
            password="postgres",
            snapshots_dir=str(snapshots_dir),
        )

        runner.run(host="test-host", commit="abc123")

        # Check that a snapshot file was created
        snapshot_files = list(snapshots_dir.glob("db_footprint_*.json"))
        assert len(snapshot_files) > 0

        # Read and validate the snapshot
        with open(snapshot_files[0]) as f:
            snapshot = json.load(f)

        assert snapshot["probe"] == "db_footprint"
        assert snapshot["repo"] == "algogators"
        assert snapshot["suite"] == "perf"
        assert "metrics" in snapshot
        assert "environment" in snapshot
        assert "captured_at" in snapshot

        # Validate contract
        errors = validate_contract(snapshot)
        assert len(errors) == 0, f"Contract validation errors: {errors}"


@pytest.mark.integration
@pytest.mark.slow
class TestDbAuditDockerIntegration(unittest.TestCase):
    """
    Integration test for real Docker database (optional).

    This test requires Docker to be running. If Docker is not available,
    this test will be skipped.

    To run this test:
    1. Ensure Docker daemon is running
    2. Run: pytest -m integration infra/perf/tests/test_db_audit.py
    """

    container_id: str | None = None

    @classmethod
    def setUpClass(cls) -> None:
        """Start a TimescaleDB Docker container (skip if Docker unavailable)."""
        import subprocess

        # Try to start the container
        try:
            cmd = [
                "docker",
                "run",
                "-d",
                "--rm",
                "-e",
                "POSTGRES_PASSWORD=postgres",
                "-p",
                "5432:5432",
                "timescale/timescaledb:latest-pg16",
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                pytest.skip(f"Docker not available or failed to start: {result.stderr}")

            cls.container_id = result.stdout.strip()
            print(f"Started container: {cls.container_id}")

            # Wait for database to be ready
            import time

            import psycopg2

            for attempt in range(30):
                try:
                    conn = psycopg2.connect(
                        host="localhost",
                        port=5432,
                        database="postgres",
                        user="postgres",
                        password="postgres",
                    )
                    conn.close()
                    break
                except psycopg2.OperationalError:
                    if attempt == 29:
                        raise
                    time.sleep(1)

        except Exception as e:
            pytest.skip(f"Docker setup failed: {e}")

    @classmethod
    def tearDownClass(cls) -> None:
        """Stop and remove the Docker container."""
        if cls.container_id:
            import subprocess

            cmd = ["docker", "stop", cls.container_id]
            subprocess.run(cmd, timeout=10)

    def test_db_audit_with_real_database(self) -> None:
        """Test against a real TimescaleDB instance (if Docker available)."""
        import tempfile

        # Create test database and tables
        import psycopg2

        conn = psycopg2.connect(
            host="localhost",
            port=5432,
            database="postgres",
            user="postgres",
            password="postgres",
        )
        conn.autocommit = True

        try:
            with conn.cursor() as cur:
                cur.execute("CREATE DATABASE test_audit")
        except psycopg2.errors.DuplicateDatabase:
            pass

        conn.close()

        # Connect to test database
        conn = psycopg2.connect(
            host="localhost",
            port=5432,
            database="test_audit",
            user="postgres",
            password="postgres",
        )
        conn.autocommit = True

        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
            cur.execute("CREATE SCHEMA IF NOT EXISTS futures_data")
            cur.execute(
                """
                CREATE TABLE futures_data.ohlcv_1d (
                    time TIMESTAMPTZ NOT NULL,
                    symbol TEXT NOT NULL,
                    open NUMERIC,
                    high NUMERIC,
                    low NUMERIC,
                    close NUMERIC,
                    volume NUMERIC
                )
            """
            )
            cur.execute(
                "SELECT create_hypertable('futures_data.ohlcv_1d', 'time', if_not_exists => TRUE)"
            )

            # Insert test data
            cur.execute(
                """
                INSERT INTO futures_data.ohlcv_1d
                (time, symbol, open, high, low, close, volume)
                VALUES ('2024-01-01', 'ES', 100, 101, 99, 100.5, 1000000)
            """
            )

        conn.close()

        # Run the audit
        with tempfile.TemporaryDirectory() as tmp_dir:
            runner = DbAuditRunner(
                host="localhost",
                port=5432,
                database="test_audit",
                user="postgres",
                password="postgres",
                snapshots_dir=tmp_dir,
            )

            result = runner.run(host="docker-localhost", commit="test-commit")

            # Validate contract per spec §3
            assert result["probe"] == "db_footprint"
            assert result["repo"] == "algogators"
            assert result["suite"] == "perf"
            assert result["environment"]["host"] == "docker-localhost"
            assert result["environment"]["commit"] == "test-commit"
            errors = validate_contract(result)
            assert len(errors) == 0, f"Contract validation errors: {errors}"


if __name__ == "__main__":
    unittest.main()
