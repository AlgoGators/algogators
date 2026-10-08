import os
import unittest
from datetime import UTC, date, datetime
from unittest.mock import MagicMock, patch

from data_ngin.ops import check_data_freshness as freshness
from data_ngin.ops.dag_failure_notifier import notify_dag_failure
from data_ngin.ops.github_issues import DEFAULT_REPO, file_or_update_issue, github_settings

DB_ENV = {
    "DB_HOST": "db.internal",
    "DB_PORT": "5432",
    "DB_NAME": "algo_data",
    "DB_USER": "u",
    "DB_PASSWORD": "p",
}


def _response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    return resp


class TestGithubIssues(unittest.TestCase):
    def test_settings_default_repo_and_blank_token(self) -> None:
        with patch.dict(os.environ, {"GITHUB_TOKEN": " ", "GITHUB_REPO": ""}):
            self.assertEqual(github_settings(), (None, DEFAULT_REPO))
        self.assertEqual(DEFAULT_REPO, "AlgoGators/algogators")

    @patch("data_ngin.ops.github_issues.requests")
    def test_comments_on_existing_issue(self, mock_requests: MagicMock) -> None:
        mock_requests.get.return_value = _response({"items": [{"number": 7}]})
        number = file_or_update_issue("o/r", "t", "lbl", "Title", "Title", "body")

        self.assertEqual(number, 7)
        url = mock_requests.post.call_args.args[0]
        self.assertTrue(url.endswith("/repos/o/r/issues/7/comments"))

    @patch("data_ngin.ops.github_issues.requests")
    def test_opens_new_issue(self, mock_requests: MagicMock) -> None:
        mock_requests.get.return_value = _response({"items": []})
        mock_requests.post.return_value = _response({"number": 9})
        number = file_or_update_issue("o/r", "t", "lbl", "Title", "Title", "body")

        self.assertEqual(number, 9)
        self.assertEqual(mock_requests.post.call_args.kwargs["json"]["labels"], ["lbl"])


class TestDagFailureNotifier(unittest.TestCase):
    def _context(self) -> dict:
        dag = MagicMock(dag_id="tiingo_data_dag")
        ti = MagicMock(task_id="run_tiingo_pipeline", log_url="http://logs")
        return {"dag": dag, "task_instance": ti, "exception": RuntimeError("boom")}

    @patch("data_ngin.ops.dag_failure_notifier.file_or_update_issue")
    def test_no_token_is_a_no_op(self, mock_file: MagicMock) -> None:
        with patch.dict(os.environ, {"GITHUB_TOKEN": ""}):
            notify_dag_failure(self._context())
        mock_file.assert_not_called()

    @patch("data_ngin.ops.dag_failure_notifier.file_or_update_issue", return_value=3)
    def test_files_issue(self, mock_file: MagicMock) -> None:
        with patch.dict(os.environ, {"GITHUB_TOKEN": "t", "GITHUB_REPO": "o/r"}):
            notify_dag_failure(self._context())
        kwargs = mock_file.call_args.kwargs
        self.assertEqual(kwargs["repo"], "o/r")
        self.assertEqual(kwargs["label"], "dag-failure")
        self.assertIn("boom", kwargs["body"])

    @patch("data_ngin.ops.dag_failure_notifier.file_or_update_issue", side_effect=OSError)
    def test_api_error_never_raises(self, _mock_file: MagicMock) -> None:
        with patch.dict(os.environ, {"GITHUB_TOKEN": "t"}):
            notify_dag_failure(self._context())


class TestCheckDataFreshness(unittest.TestCase):
    NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)

    def test_monitored_tables_follow_packaged_configs(self) -> None:
        self.assertEqual(
            freshness.monitored_tables(),
            [
                ("algo_data", "futures_data.ohlcv_1d"),
                ("new_algo_data", "futures_data.ohlcv_1d"),
                ("new_algo_data", "equities_data.ohlcv_1d"),
            ],
        )

    def test_find_stale_tables(self) -> None:
        latest = {
            "fresh": datetime(2026, 10, 7, 12, tzinfo=UTC),
            "naive_old": datetime(2026, 10, 1),
            "date_fresh": date(2026, 10, 7),
            "empty": None,
            "broken": ConnectionError("refused"),
        }
        stale = dict(freshness.find_stale_tables(latest, self.NOW, 72))

        self.assertEqual(set(stale), {"naive_old", "empty", "broken"})
        self.assertIn("refused", stale["broken"])

    @patch.dict(os.environ, DB_ENV)
    @patch("psycopg2.connect")
    def test_latest_timestamps_per_database(self, mock_connect: MagicMock) -> None:
        conn = mock_connect.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = (datetime(2026, 10, 7),)

        latest = freshness.get_latest_timestamps(
            [("algo_data", "futures_data.ohlcv_1d"), ("new_algo_data", "equities_data.ohlcv_1d")]
        )

        self.assertEqual(
            set(latest), {"algo_data/futures_data.ohlcv_1d", "new_algo_data/equities_data.ohlcv_1d"}
        )
        dbnames = [c.kwargs["dbname"] for c in mock_connect.call_args_list]
        self.assertEqual(dbnames, ["algo_data", "new_algo_data"])

    @patch.dict(os.environ, DB_ENV)
    @patch("psycopg2.connect", side_effect=OSError("unreachable"))
    def test_unreachable_db_is_reported(self, _mock_connect: MagicMock) -> None:
        latest = freshness.get_latest_timestamps([("algo_data", "futures_data.ohlcv_1d")])
        self.assertIsInstance(latest["algo_data/futures_data.ohlcv_1d"], OSError)

    @patch.object(freshness, "file_or_update_issue")
    @patch.object(freshness, "get_latest_timestamps")
    def test_main_exit_codes(self, mock_latest: MagicMock, mock_file: MagicMock) -> None:
        mock_latest.return_value = {"t": datetime.now(UTC)}
        self.assertEqual(freshness.main(), 0)

        mock_latest.return_value = {"t": None}
        with patch.dict(os.environ, {"GITHUB_TOKEN": "tok"}):
            self.assertEqual(freshness.main(), 1)
        mock_file.assert_called_once()


if __name__ == "__main__":
    unittest.main()
