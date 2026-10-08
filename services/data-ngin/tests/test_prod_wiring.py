"""Pins the production wiring: packaged configs/contracts, db_name routing,
date-range seeding, and the DAG task bodies."""

import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from data_ngin.application import pipeline_tasks
from data_ngin.infrastructure.loader.csv_loader import (
    PACKAGE_CONTRACTS_DIR,
    CSVLoader,
    resolve_contract_path,
)
from data_ngin.infrastructure.repository.ohlcv_repository import OhlcvRepository
from data_ngin.utils.dynamic_loader import determine_date_range, load_config

DB_ENV = {
    "DB_HOST": "db.internal",
    "DB_PORT": "5432",
    "DB_NAME": "algo_data",
    "DB_USER": "u",
    "DB_PASSWORD": "p",
}

PIPELINES = {
    "config.yaml": ("algo_data", "futures_data", "BatchDownloadDatabentoFetcher"),
    "new_config.yaml": ("new_algo_data", "futures_data", "BatchDownloadDatabentoFetcher"),
    "config_tiingo.yaml": ("new_algo_data", "equities_data", "TiingoFetcher"),
}


class TestPackagedConfigs(unittest.TestCase):
    def test_configs_load_and_target_the_right_database(self) -> None:
        for name, (db_name, schema, fetcher) in PIPELINES.items():
            with self.subTest(config=name):
                config = load_config(pipeline_tasks.config_path(name))
                self.assertEqual(config["database"]["db_name"], db_name)
                self.assertEqual(config["database"]["target_schema"], schema)
                self.assertEqual(config["fetcher"]["class"], fetcher)
                # Incremental mode in production: never a hardcoded window.
                self.assertFalse(config["time_range"]["start_date"])
                self.assertFalse(config["time_range"]["end_date"])

    def test_contract_files_ship_and_load(self) -> None:
        for name in PIPELINES:
            with self.subTest(config=name):
                symbols = CSVLoader(load_config(pipeline_tasks.config_path(name))).load_symbols()
                self.assertGreater(len(symbols), 0)

    def test_config_dir_override(self) -> None:
        with patch.dict(os.environ, {"DATA_NGIN_CONFIG_DIR": "/etc/dn"}):
            self.assertEqual(
                pipeline_tasks.config_path("config.yaml"), os.path.join("/etc/dn", "config.yaml")
            )


class TestContractPathResolution(unittest.TestCase):
    def test_relative_resolves_to_package(self) -> None:
        with patch.dict(os.environ, {"DATA_NGIN_CONTRACTS_DIR": ""}):
            self.assertEqual(
                resolve_contract_path("x.csv"), os.path.join(PACKAGE_CONTRACTS_DIR, "x.csv")
            )

    def test_env_override(self) -> None:
        with patch.dict(os.environ, {"DATA_NGIN_CONTRACTS_DIR": "/data/contracts"}):
            self.assertEqual(
                resolve_contract_path("x.csv"), os.path.join("/data/contracts", "x.csv")
            )

    def test_absolute_is_untouched(self) -> None:
        absolute = os.path.abspath("x.csv")
        self.assertEqual(resolve_contract_path(absolute), absolute)


class TestDbNameRouting(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(OhlcvRepository.reset_pool_for_testing)
        OhlcvRepository.reset_pool_for_testing()

    @patch.dict(os.environ, DB_ENV)
    @patch(
        "data_ngin.infrastructure.repository.ohlcv_repository.psycopg2_pool.ThreadedConnectionPool"
    )
    def test_one_pool_per_database(self, mock_pool_cls: MagicMock) -> None:
        mock_pool_cls.side_effect = lambda *a, **kw: MagicMock(dbname=kw["dbname"])

        default = OhlcvRepository._get_pool()
        new_db = OhlcvRepository._get_pool("new_algo_data")

        self.assertEqual(default.dbname, "algo_data")
        self.assertEqual(new_db.dbname, "new_algo_data")
        self.assertIs(OhlcvRepository._get_pool("new_algo_data"), new_db)
        self.assertEqual(mock_pool_cls.call_count, 2)

    def test_repository_uses_config_db_name(self) -> None:
        repo = OhlcvRepository({"database": {"db_name": "new_algo_data"}})
        with patch.object(OhlcvRepository, "_get_pool") as mock_get_pool:
            mock_get_pool.return_value.getconn.side_effect = RuntimeError("stop")
            with self.assertRaises(RuntimeError):
                repo.connect()
        mock_get_pool.assert_called_with("new_algo_data")


class TestDateRangeSeeding(unittest.TestCase):
    def _config(self, **time_range: str) -> dict:
        return {"database": {"target_schema": "s", "table": "t"}, "time_range": time_range}

    @patch("data_ngin.utils.dynamic_loader.OhlcvRepository")
    def test_seed_used_when_table_empty(self, mock_repo_cls: MagicMock) -> None:
        mock_repo_cls.return_value.get_latest_date.return_value = None
        start, _end = determine_date_range(
            self._config(start_date="", end_date="2026-02-01", seed_start_date="2026-01-01")
        )
        self.assertEqual(start, "2026-01-01")

    @patch("data_ngin.utils.dynamic_loader.OhlcvRepository")
    def test_empty_table_without_seed_raises(self, mock_repo_cls: MagicMock) -> None:
        mock_repo_cls.return_value.get_latest_date.return_value = None
        with self.assertRaises(ValueError):
            determine_date_range(self._config(start_date="", end_date=""))

    @patch("data_ngin.utils.dynamic_loader.OhlcvRepository")
    def test_caught_up_range_is_clamped(self, mock_repo_cls: MagicMock) -> None:
        mock_repo_cls.return_value.get_latest_date.return_value = "2026-02-01"
        start, end = determine_date_range(self._config(start_date="", end_date="2026-02-01"))
        self.assertEqual((start, end), ("2026-02-01", "2026-02-01"))


class TestPipelineTasks(unittest.TestCase):
    @patch("data_ngin.application.orchestrator.Orchestrator")
    def test_run_pipeline(self, mock_orchestrator_cls: MagicMock) -> None:
        async def _run() -> None:
            return None

        mock_orchestrator_cls.return_value.run.side_effect = _run
        pipeline_tasks.run_pipeline("config_tiingo.yaml", run_type="manual")

        config = mock_orchestrator_cls.call_args.kwargs["config"]
        self.assertEqual(config["fetcher"]["class"], "TiingoFetcher")

    def test_dags_have_no_all_done_tasks(self) -> None:
        # Airflow takes a run's state from its leaf tasks. A leaf with
        # trigger_rule="all_done" succeeds after the pipeline task fails, which
        # marks the whole run green and hides the failure.
        dags = Path(__file__).resolve().parents[1] / "dags"
        for dag_file in sorted(dags.glob("*_dag.py")):
            self.assertNotIn("all_done", dag_file.read_text(encoding="utf-8"), dag_file.name)


if __name__ == "__main__":
    unittest.main()
