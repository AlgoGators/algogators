import ast
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from data_ngin.infrastructure.repository.ohlcv_repository import OhlcvRepository
from data_ngin.infrastructure.repository.readiness_repository import ReadinessRepository
from data_ngin.ops.readiness_failure import (
    CONTINUOUS_ONLY_TASKS,
    CONTRACT_ONLY_TASKS,
    NO_READINESS_TASKS,
    make_readiness_failure_callback,
)

CONFIG = {
    "database": {
        "db_name": "new_algo_data",
        "target_schema": "futures_data",
        "raw_table": "ohlcv_1d_raw",
        "table": "ohlcv_1d",
    }
}


def _repo_with_cursor(cls: type) -> tuple[OhlcvRepository, MagicMock]:
    repo = cls(CONFIG)
    cursor = MagicMock()
    repo.connection = MagicMock()
    repo.connection.cursor.return_value.__enter__.return_value = cursor
    return repo, cursor


class TestGetBarDates(unittest.TestCase):
    def test_returns_symbol_date_pairs_for_the_utc_window(self) -> None:
        repo, cursor = _repo_with_cursor(OhlcvRepository)
        cursor.fetchall.return_value = [("6E.v.0", date(2026, 10, 12))]

        pairs = repo.get_bar_dates(["6E.v.0"], date(2026, 10, 7), date(2026, 10, 12))

        self.assertEqual(pairs, {("6E.v.0", date(2026, 10, 12))})
        sql, params = cursor.execute.call_args.args
        self.assertIn("futures_data.ohlcv_1d", sql)
        self.assertEqual(params[0], datetime(2026, 10, 7, tzinfo=UTC))
        self.assertEqual(params[1], datetime(2026, 10, 13, tzinfo=UTC))
        self.assertEqual(params[2], ["6E.v.0"])

    def test_no_symbols_skips_the_query(self) -> None:
        repo, cursor = _repo_with_cursor(OhlcvRepository)
        self.assertEqual(repo.get_bar_dates([], date(2026, 10, 7), date(2026, 10, 12)), set())
        cursor.execute.assert_not_called()


class TestReadinessRepository(unittest.TestCase):
    def test_upsert_on_dataset_and_bar_date(self) -> None:
        repo, cursor = _repo_with_cursor(ReadinessRepository)
        row = {
            "dataset": "futures_ohlcv_1d",
            "bar_date": date(2026, 10, 12),
            "expected": 2,
            "loaded": 1,
            "missing": ["ZC.v.0"],
            "status": "partial",
            "run_id": "r1",
            "finished_at": datetime(2026, 10, 13, 0, 5, tzinfo=UTC),
        }
        repo.upsert_readiness(row)
        sql, params = cursor.execute.call_args.args
        self.assertIn("INSERT INTO ops.data_ready", sql)
        self.assertIn("ON CONFLICT (dataset, bar_date) DO UPDATE", sql)
        self.assertIs(params, row)


class TestReadinessFailureCallback(unittest.TestCase):
    @patch("data_ngin.application.pipeline_tasks.write_futures_readiness")
    def test_writes_failed_row(self, mock_write: MagicMock) -> None:
        context = {
            "data_interval_end": datetime(2026, 10, 13, 0, 0, tzinfo=UTC),
            "run_id": "scheduled__2026-10-13",
        }
        make_readiness_failure_callback("new_config.yaml")(context)
        mock_write.assert_called_once_with(
            "new_config.yaml", date(2026, 10, 12), "scheduled__2026-10-13", failed=True
        )

    def _run_for_task(self, task_id: str | None) -> tuple[MagicMock, MagicMock]:
        context = {
            "data_interval_end": datetime(2026, 10, 13, 0, 0, tzinfo=UTC),
            "run_id": "scheduled__2026-10-13",
        }
        if task_id is not None:
            context["task_instance"] = MagicMock(task_id=task_id)
        with (
            patch("data_ngin.application.pipeline_tasks.write_futures_readiness") as continuous,
            patch(
                "data_ngin.application.pipeline_tasks.write_contract_futures_readiness"
            ) as contract,
        ):
            make_readiness_failure_callback("new_config.yaml")(context)
        return continuous, contract

    def test_a_failed_contract_task_leaves_the_continuous_row_alone(self) -> None:
        for task_id in ("run_contract_pipeline", "write_contract_readiness"):
            with self.subTest(task_id=task_id):
                continuous, contract = self._run_for_task(task_id)
                continuous.assert_not_called()
                contract.assert_called_once_with(
                    "new_config.yaml", date(2026, 10, 12), "scheduled__2026-10-13", failed=True
                )

    def test_a_failed_continuous_readiness_task_leaves_the_contract_row_alone(self) -> None:
        continuous, contract = self._run_for_task("write_readiness")
        continuous.assert_called_once_with(
            "new_config.yaml", date(2026, 10, 12), "scheduled__2026-10-13", failed=True
        )
        contract.assert_not_called()

    def test_a_failed_continuous_pull_writes_both_rows(self) -> None:
        # The per-contract pull runs after the continuous one, so it never runs.
        for task_id in ("run_pipeline", None):
            with self.subTest(task_id=task_id):
                continuous, contract = self._run_for_task(task_id)
                continuous.assert_called_once()
                contract.assert_called_once()

    def test_task_names_are_the_dags(self) -> None:
        # A renamed task would silently fall back to writing both rows.
        dag_file = Path(__file__).resolve().parents[2] / "dags" / "new_data_pipeline_dag.py"
        tasks = {
            node.name
            for node in ast.walk(ast.parse(dag_file.read_text()))
            if isinstance(node, ast.FunctionDef)
            and any(getattr(d, "id", None) == "task" for d in node.decorator_list)
        }
        self.assertEqual(
            tasks,
            {"run_pipeline"} | CONTINUOUS_ONLY_TASKS | CONTRACT_ONLY_TASKS | NO_READINESS_TASKS,
        )

    @patch("data_ngin.application.pipeline_tasks.write_contract_futures_readiness")
    @patch("data_ngin.application.pipeline_tasks.write_futures_readiness")
    def test_a_failed_continuous_fill_rewrites_no_row(
        self, mock_continuous: MagicMock, mock_contract: MagicMock
    ) -> None:
        # Both pulls wrote their rows before the fill ran; its failure must not mark them failed.
        context = {"task_instance": MagicMock(task_id="fill_continuous_holes")}
        with patch(
            "data_ngin.application.pipeline_tasks.bar_date_from_context",
            return_value=date(2026, 10, 21),
        ):
            make_readiness_failure_callback("new_config.yaml")(context)
        mock_continuous.assert_not_called()
        mock_contract.assert_not_called()

    @patch("data_ngin.application.pipeline_tasks.write_futures_readiness")
    def test_never_raises(self, mock_write: MagicMock) -> None:
        mock_write.side_effect = RuntimeError("relation ops.data_ready does not exist")
        with self.assertLogs("data_ngin.ops.readiness_failure", "ERROR"):
            make_readiness_failure_callback("new_config.yaml")({})


if __name__ == "__main__":
    unittest.main()
